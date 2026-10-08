from __future__ import annotations

from collections.abc import Iterable
import csv
from dataclasses import dataclass
import io
import json
import os
import re
from pathlib import Path
import shutil
import tempfile



BUNDLED_BUSINESS_SKILL_NAMES = (
    "ceo-message-triage",
    "ceo-calendar-invite",
    "ceo-document-review",
    "ceo-meeting-work",
    "ceo-mail-review",
    "ceo-personnel-communication",
    "ceo-work-tracking",
    "ceo-sales-weekly-report",
)

MANAGED_BY = "ceo-agent-service"


class BusinessSkillError(RuntimeError):
    """Base error for bundled business Skill operations."""


class BusinessSkillValidationError(BusinessSkillError):
    """Raised when a bundled Skill is missing or has invalid metadata."""


class BusinessSkillInstallConflict(BusinessSkillError):
    """Raised when installation would replace a user-owned Skill."""


class BusinessSkillInstallTargetError(BusinessSkillError):
    """Raised when the requested installation root is prohibited."""


class BusinessSkillInstallRollbackError(BusinessSkillError):
    """Raised when installation and at least one rollback operation fail."""

    def __init__(
        self,
        install_error: BaseException,
        rollback_errors: tuple[tuple[Path, BaseException], ...],
        recovery_path: Path,
    ) -> None:
        self.install_error = install_error
        self.rollback_errors = rollback_errors
        self.recovery_path = recovery_path
        rollback_detail = "; ".join(
            f"{target}: {error}" for target, error in rollback_errors
        )
        super().__init__(
            f"business Skill install failed: {install_error}; rollback failed: "
            f"{rollback_detail}; recovery data preserved at {recovery_path}"
        )


def sync_bundled_skill(
    name: str,
    *,
    source_path: Path | None = None,
    target_root: Path | None = None,
) -> Path:
    """Atomically synchronize one service-managed project Skill to its runtime copy."""
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise BusinessSkillValidationError(f"invalid Skill name: {name!r}")
    source = Path(source_path) if source_path is not None else bundled_business_skills_root() / name / "SKILL.md"
    try:
        raw_content = source.read_bytes()
        content = raw_content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise BusinessSkillValidationError(f"unable to read Skill: {source}: {exc}") from exc
    frontmatter = _parse_frontmatter(content, source)
    if _required_scalar(frontmatter, "name", source) != name:
        raise BusinessSkillValidationError(f"Skill name does not match directory: {source}")
    _required_scalar(frontmatter, "description", source)
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("managed_by") != MANAGED_BY:
        raise BusinessSkillValidationError(f"Skill missing managed marker: {source}")
    root = Path.home() / ".agents" / "skills" if target_root is None else Path(target_root).expanduser()
    _validate_install_target(root)
    target_dir = root / name
    try:
        resolved_root = root.resolve(strict=False)
        resolved_target = target_dir.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise BusinessSkillInstallTargetError(
            f"unable to validate business Skill destination: {target_dir}"
        ) from exc
    if resolved_target.parent != resolved_root:
        raise BusinessSkillInstallTargetError(
            f"business Skill destination escaped target root: {target_dir}"
        )
    had_existing = _check_swap_conflict(target_dir)
    root.mkdir(parents=True, exist_ok=True)
    transaction_root = Path(tempfile.mkdtemp(prefix=".ceo-business-skill-", dir=root.parent))
    staged_dir = transaction_root / "staged"
    backup_dir = transaction_root / "backup"
    swap = _SwapState(
        staged_dir=staged_dir,
        target_dir=target_dir,
        backup_dir=backup_dir / name,
        had_existing=had_existing,
    )
    cleanup_transaction = True
    try:
        backup_dir.mkdir()
        _stage_skill_package(source.parent, staged_dir, raw_content)
        expected_resolved_root = root.resolve(strict=True)
        if expected_resolved_root != resolved_root:
            raise BusinessSkillInstallTargetError(
                f"business Skill target changed during installation: {root}"
            )
        _swap_in(swap, root, expected_resolved_root)
    except BaseException as install_error:
        rollback_error = _rollback_swap(swap, root, resolved_root)
        if rollback_error is not None:
            cleanup_transaction = False
            raise BusinessSkillInstallRollbackError(
                install_error, ((target_dir, rollback_error),), transaction_root
            ) from install_error
        raise
    finally:
        if cleanup_transaction:
            shutil.rmtree(transaction_root)
    return target_dir / "SKILL.md"


@dataclass(frozen=True)
class BundledBusinessSkill:
    name: str
    description: str
    managed_by: str
    source_path: Path
    content: str


@dataclass(frozen=True)
class InstalledBusinessSkill:
    name: str
    install_path: Path


@dataclass(frozen=True)
class BusinessSkillCatalogEntry:
    name: str
    skill_path: Path
    description: str = ""


@dataclass(frozen=True)
class FrozenTaskSkillMaterial:
    """One exact Skill body saved in a reply task's structured input."""

    name: str
    content: str


@dataclass(frozen=True)
class FrozenTaskSkillBinding:
    """Structured frozen-Skill declaration and readable saved materials."""

    declared: bool
    declared_names: tuple[str, ...]
    materials: tuple[FrozenTaskSkillMaterial, ...]


@dataclass
class _SwapState:
    staged_dir: Path
    target_dir: Path
    backup_dir: Path
    had_existing: bool
    backup_moved: bool = False
    installed: bool = False


def _package_file_ignore(directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == "__pycache__"
        or name.endswith(".pyc")
        or Path(directory, name).is_symlink()
    }


def _stage_skill_package(source_dir: Path, staged_dir: Path, skill_md: bytes) -> None:
    """Stage a whole Skill package: the validated SKILL.md plus its sibling files.

    A Skill can ship more than instructions - ceo-wechat carries
    capability.json and the scripts its SKILL.md tells the Agent to run - so
    installing SKILL.md alone leaves a package whose own commands do not exist.
    SKILL.md is written from the exact bytes that were validated. Bytecode
    caches and symlinks are left behind: a cache is rebuilt on use, and a link
    could point outside the package into files the installer never vetted.
    """
    staged_dir.mkdir()
    (staged_dir / "SKILL.md").write_bytes(skill_md)
    ignored = _package_file_ignore(str(source_dir), [e.name for e in source_dir.iterdir()])
    for entry in sorted(source_dir.iterdir(), key=lambda item: item.name):
        if entry.name == "SKILL.md" or entry.name in ignored:
            continue
        destination = staged_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, ignore=_package_file_ignore)
        elif entry.is_file():
            shutil.copy2(entry, destination)


def _check_swap_conflict(target_dir: Path) -> bool:
    """Validate a would-be swap target is safe to replace; return whether it exists.

    Shared preflight used by both the single-Skill and bulk install paths so a
    user-owned or symlinked directory is always rejected before anything is staged.
    """
    if target_dir.is_symlink():
        raise BusinessSkillInstallTargetError(
            f"refusing symlinked business Skill directory: {target_dir}"
        )
    if target_dir.exists():
        target_file = target_dir / "SKILL.md"
        if not target_file.is_file() or not _is_service_managed(target_file):
            raise BusinessSkillInstallConflict(
                f"refusing to overwrite user-owned Skill: {target_dir}"
            )
        return True
    return False


def _swap_in(swap: _SwapState, target_root: Path, expected_resolved_root: Path) -> None:
    """Move a staged Skill directory live, backing up any existing directory first."""
    if swap.had_existing:
        os.replace(swap.target_dir, swap.backup_dir)
        swap.backup_moved = True
    _validate_swap_destination(target_root, expected_resolved_root, swap.target_dir)
    os.replace(swap.staged_dir, swap.target_dir)
    swap.installed = True


def _rollback_swap(
    swap: _SwapState, target_root: Path, expected_resolved_root: Path
) -> BaseException | None:
    """Undo one swap, returning the failure if rollback itself could not complete."""
    try:
        if swap.installed:
            os.replace(swap.target_dir, swap.staged_dir)
            swap.installed = False
        if swap.backup_moved:
            _validate_swap_destination(target_root, expected_resolved_root, swap.target_dir)
            os.replace(swap.backup_dir, swap.target_dir)
            swap.backup_moved = False
    except BaseException as rollback_error:  # noqa: BLE001 - surfaced to the caller
        return rollback_error
    return None


def bundled_business_skills_root() -> Path:
    """Where the business Skills are authored.

    `~/.agents/skills` is the source of truth for installed Skills on this
    machine, so the service reads its baseline from there rather than keeping a
    second copy in this repository. Two copies meant an edit could land in the
    one nothing loads, and a publish to the shared Skills library could
    overwrite it.
    """
    return Path(
        os.environ.get("CEO_SKILLS_ROOT", "").strip()
        or Path.home() / ".agents" / "skills"
    ).expanduser()


def installed_business_skill_catalog(
    target_root: Path | None = None,
) -> tuple[BusinessSkillCatalogEntry, ...]:
    root = (
        Path.home() / ".agents" / "skills"
        if target_root is None
        else Path(target_root).expanduser()
    ).resolve()
    return tuple(
        BusinessSkillCatalogEntry(
            name=name,
            skill_path=(root / name / "SKILL.md").resolve(),
        )
        for name in BUNDLED_BUSINESS_SKILL_NAMES
    )


def runtime_skill_root(target_root: Path | None = None) -> Path:
    return (
        Path.home() / ".agents" / "skills"
        if target_root is None
        else Path(target_root).expanduser()
    )


def installed_runtime_skill_paths(target_root: Path | None = None) -> tuple[Path, ...]:
    """Every SKILL.md under the runtime tree, including nested ones.

    Codex disables Skills by path and discovers them at any depth, so the
    exclusion list has to be built from a full walk rather than a top-level
    listing; a Skill missed here silently stays enabled.
    """
    root = runtime_skill_root(target_root)
    if not root.is_dir():
        return ()
    return tuple(sorted(root.rglob("SKILL.md")))


def _describe_skill_file(path: Path) -> tuple[str, str] | None:
    """Return (name, description) from the service-supported frontmatter, or None.

    Runtime managed Skills use the same compact frontmatter parser as the
    installer and validator.  In particular, descriptions may contain a colon
    without YAML quoting; using a stricter YAML loader here made an installed
    Skill disappear from the catalog even though the service could load it.
    """
    try:
        frontmatter = _parse_frontmatter(path.read_text(encoding="utf-8"), path)
    except (OSError, UnicodeError, BusinessSkillValidationError):
        return None
    if not isinstance(frontmatter, dict):
        return None
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    name, description = name.strip(), description.strip()
    return (name, description) if name and description else None


def installed_runtime_skills(
    target_root: Path | None = None, *, names: Iterable[str] | None = None
) -> tuple[BusinessSkillCatalogEntry, ...]:
    """Describe runtime Skills by reading their frontmatter, optionally filtered.

    Scanning beats a hardcoded list: a Skill added or removed on disk is
    reflected without a code change, and an entry can never point at a file that
    is not there. Frontmatter is parsed as real YAML because Skills in the wild
    use structures the bundled strict parser rejects. A file that will not parse
    is skipped: a catalog entry without a description cannot help the Agent
    choose, which is the only reason to list it.
    """
    wanted = None if names is None else {name for name in names if name}
    entries: list[BusinessSkillCatalogEntry] = []
    for path in installed_runtime_skill_paths(target_root):
        described = _describe_skill_file(path)
        if described is None:
            continue
        name, description = described
        if wanted is not None and name not in wanted:
            continue
        entries.append(
            BusinessSkillCatalogEntry(
                name=name, skill_path=path.resolve(), description=description
            )
        )
    return tuple(entries)


def _compact_skill_catalog(catalog: tuple[BusinessSkillCatalogEntry, ...]) -> str:
    candidates = dict.fromkeys(item.skill_path.parent.parent for item in catalog)
    roots = tuple(root for root in candidates if not any(parent in candidates for parent in root.parents))
    aliases = {root: f"r{index}" for index, root in enumerate(roots, start=1)}
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(("Root", "Path"))
    writer.writerows((alias, str(root)) for root, alias in aliases.items())
    output.write("\n")
    writer.writerow(("Name", "Read path", "Description"))
    for item in catalog:
        root = next(root for root in roots if item.skill_path.is_relative_to(root))
        writer.writerow((
            item.name,
            f"{aliases[root]}:{item.skill_path.relative_to(root)}",
            item.description,
        ))
    return (
        "Read paths use root-alias:relative-path; join with the exact root below.\n"
        "Tables are tab-separated; quoted cells preserve full text.\n"
        + output.getvalue().rstrip("\n")
    )


def render_business_skill_protocol(
    catalog: tuple[BusinessSkillCatalogEntry, ...], *, compact: bool = False,
) -> str:
    inventory = [
        {
            "name": item.name,
            "path": str(item.skill_path),
            **({"description": item.description} if item.description else {}),
        }
        for item in catalog
    ]
    return (
        "## Installed CEO business Skill catalog\n"
        + (_compact_skill_catalog(catalog) if compact else json.dumps(inventory, ensure_ascii=False, sort_keys=True))
        + "\n\n## Required Skill protocol\n"
        "PROTOCOL PRECONDITION: before returning any Consumer outcome, call "
        "`agent_cli.read_skill` for at least one CEO business Skill from the exact "
        "catalog above. Choose the applicable Skill yourself from the full context; "
        "the service does not route the domain. Read every additional business or "
        "operation Skill needed for the judgment. Do not return an outcome before "
        "completing this read."
    )


def render_task_skill_discovery(
    selected_names: Iterable[str],
    *,
    catalog: tuple[BusinessSkillCatalogEntry, ...],
    frozen_names: Iterable[str] = (),
) -> str:
    """Render name/use/read-only discovery without preloading Skill bodies."""
    selected = tuple(dict.fromkeys(name for name in selected_names if name))
    frozen = frozenset(name for name in frozen_names if name in selected)
    by_name = {entry.name: entry for entry in catalog}
    if selected:
        missing_installed = tuple(
            name for name in selected if name not in frozen and name not in by_name
        )
        if missing_installed:
            raise ValueError(
                "selected installed Skill metadata is unavailable: "
                + ", ".join(missing_installed)
            )
        entries = tuple((name, by_name.get(name)) for name in selected)
        heading = "## Selected Task Skills"
        instructions: list[str] = []
        if frozen:
            names = ", ".join(f"`{name}`" for name in selected if name in frozen)
            instructions.append(
                f"For the task-frozen selection {names}, call "
                "`agent_cli.read_task_skill(name)` before deciding or reviewing. "
                "That lookup returns this task's exact frozen content; do not "
                "substitute a similarly named installed Skill."
            )
        installed = tuple(name for name in selected if name not in frozen)
        if installed:
            instructions.append(
                "For each installed selection, call its exact "
                "`agent_cli.read_skill(path=...)` entry below before deciding "
                "or reviewing."
            )
        read_instruction = " ".join(instructions)
    else:
        entries = tuple((entry.name, entry) for entry in dict.fromkeys(catalog))
        heading = "## Skill Discovery"
        read_instruction = (
            "No task Skill was selected. When the task needs business or operation "
            "guidance, choose the applicable entry below and call its exact "
            "`agent_cli.read_skill(path=...)` command before applying it. Read additional applicable "
            "Skills on demand; do not infer a domain from keywords in the task text."
        )
    lines = [heading]
    for name, entry in entries:
        use = (
            " ".join(entry.description.split())
            if entry is not None
            else "Selected for this task."
        ) or "Selected for this task."
        line = f"- `{name}`: {use}"
        if name not in frozen:
            assert entry is not None
            path = json.dumps(str(entry.skill_path.resolve()), ensure_ascii=False)
            line += f" Read with `agent_cli.read_skill(path={path})`."
        lines.append(line)
    lines.append(read_instruction)
    return "\n".join(lines)


def frozen_task_skill_names(
    trigger_message_json: str,
    selected_names: Iterable[str],
) -> tuple[str, ...]:
    """Return selected names declared as task-frozen in structured input."""
    selected = tuple(dict.fromkeys(name for name in selected_names if name))
    binding = frozen_task_skill_binding(trigger_message_json)
    if not binding.declared:
        return ()
    declared = frozenset(binding.declared_names)
    return tuple(name for name in selected if name in declared)


def frozen_task_skill_binding(trigger_message_json: str) -> FrozenTaskSkillBinding:
    """Read one task's structured frozen declaration without repairing it."""
    empty = FrozenTaskSkillBinding(False, (), ())
    try:
        trigger = json.loads(trigger_message_json)
    except (TypeError, json.JSONDecodeError):
        return empty
    if not isinstance(trigger, dict):
        return empty
    if trigger.get("schema") == "scheduled_agent_execution.v1":
        container = trigger
    else:
        raw_payload = trigger.get("raw_payload")
        container = (
            raw_payload.get("scheduled_consumer")
            if isinstance(raw_payload, dict)
            else None
        )
    if not isinstance(container, dict) or "skill_materials" not in container:
        return empty
    raw_names = container.get("skill_names")
    declared_names = (
        tuple(name for name in raw_names if isinstance(name, str) and name)
        if isinstance(raw_names, list)
        else ()
    )
    materials = container.get("skill_materials")
    if not isinstance(materials, list):
        return FrozenTaskSkillBinding(True, declared_names, ())
    raw_material_names = tuple(
        material.get("name")
        for material in materials
        if isinstance(material, dict)
        and isinstance(material.get("name"), str)
        and material["name"]
    )
    if not declared_names:
        declared_names = tuple(dict.fromkeys(raw_material_names))
    valid_materials = all(
        isinstance(material, dict)
        and set(material) == {"name", "content"}
        and isinstance(material.get("name"), str)
        and material["name"].strip() == material["name"]
        and bool(material["name"])
        and isinstance(material.get("content"), str)
        and bool(material["content"].strip())
        for material in materials
    )
    readable = (
        tuple(
            FrozenTaskSkillMaterial(
                name=material["name"],
                content=material["content"],
            )
            for material in materials
        )
        if valid_materials
        else ()
    )
    return FrozenTaskSkillBinding(True, declared_names, readable)


def frozen_task_skill_materials(
    trigger_message_json: str,
) -> tuple[FrozenTaskSkillMaterial, ...]:
    """Read frozen materials from service-command or generic scheduled input."""
    return frozen_task_skill_binding(trigger_message_json).materials


def expand_skill_dependencies(
    names: Iterable[str], *, target_root: Path | None = None
) -> tuple[str, ...]:
    """Add every installed Skill the given Skills reference, transitively.

    A task declares the Skills it works through; those Skills delegate to others
    by name (the OA Skill shells out to `ocr`, which leads to `pdf` and `xlsx`
    several hops away). Leaving a dependency out would hide it from the Agent
    silently, so the closure is followed to the end rather than cut at a depth.

    Two kinds of reference count, and only when they resolve to a Skill that is
    actually installed: an exact Skill name, and a `dws <product>` command, which
    is documented by the `dingtalk-<product>` Skill. Matching a Skill mentioned
    only to say "do not use it" over-includes, which errs toward visibility; an
    unresolvable token is never invented into a name.
    """
    catalog = {entry.name: entry.skill_path for entry in installed_runtime_skills(target_root)}
    if not catalog:
        return tuple(dict.fromkeys(name for name in names if name))
    name_pattern = re.compile(
        r"(?<![\w-])("
        + "|".join(re.escape(name) for name in sorted(catalog, key=len, reverse=True))
        + r")(?![\w-])"
    )
    dws_pattern = re.compile(r"(?<![\w-])dws\s+([a-z][a-z0-9-]*)")
    resolved: dict[str, None] = {}
    pending = [name for name in names if name]
    while pending:
        name = pending.pop()
        if name in resolved:
            continue
        resolved[name] = None
        path = catalog.get(name)
        if path is None:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        referenced = set(name_pattern.findall(body))
        referenced |= {
            f"dingtalk-{product}"
            for product in dws_pattern.findall(body)
            if f"dingtalk-{product}" in catalog
        }
        pending.extend(sorted(referenced - resolved.keys()))
    return tuple(resolved)


# Plugins whose Skills every Agent turn gets regardless of the task's own list.
# The Consumer and Audit contracts tell the Agent to recall durable context
# through memory; trimming these away would leave that instruction unusable.
DEFAULT_SKILL_PLUGINS = ("memory-connector",)


def _version_key(name: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in name.split("."))


def default_plugin_skill_files(plugin_cache_root: Path) -> tuple[Path, ...]:
    """SKILL.md files of the always-on plugins, newest installed version only.

    A plugin cache keeps every version it ever installed; only the newest is the
    one the runtime loads, so older copies are not offered to the Agent.
    """
    files: list[Path] = []
    if not plugin_cache_root.is_dir():
        return ()
    for plugin in DEFAULT_SKILL_PLUGINS:
        for plugin_dir in sorted(plugin_cache_root.glob(f"*/{plugin}")):
            versions = [entry for entry in plugin_dir.iterdir() if entry.is_dir()]
            if not versions:
                continue
            newest = max(versions, key=lambda entry: _version_key(entry.name))
            files.extend(sorted((newest / "skills").glob("*/SKILL.md")))
    return tuple(files)


def default_skill_catalog(plugin_cache_root: Path | None = None) -> tuple[BusinessSkillCatalogEntry, ...]:
    """Catalog entries for the always-on plugin Skills, named as the runtime names them.

    Plugin Skills declare short names such as `recall`; the entry carries the
    runtime's `plugin:skill` name so it cannot be mistaken for another Skill.
    """
    root = (
        Path(plugin_cache_root).expanduser()
        if plugin_cache_root is not None
        else Path.home() / ".claude" / "plugins" / "cache"
    )
    entries: list[BusinessSkillCatalogEntry] = []
    for path in default_plugin_skill_files(root):
        described = _describe_skill_file(path)
        if described is None:
            continue
        plugin = next(name for name in DEFAULT_SKILL_PLUGINS if name in path.parts)
        entries.append(
            BusinessSkillCatalogEntry(
                name=f"{plugin}:{described[0]}",
                skill_path=path.resolve(),
                description=described[1],
            )
        )
    return tuple(entries)


def codex_skill_roots(
    *, target_root: Path | None = None, codex_home: Path | None = None
) -> tuple[Path, ...]:
    """Every directory Codex discovers Skills in, not only the shared runtime tree."""
    home = (
        Path(codex_home).expanduser()
        if codex_home is not None
        else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    )
    return (runtime_skill_root(target_root), home / "skills", home / "plugins" / "cache")


def codex_skill_config_override(
    allowed_names: Iterable[str],
    *,
    target_root: Path | None = None,
    codex_home: Path | None = None,
) -> str:
    """Build the Codex `-c skills.config=[...]` that shows exactly one task's Skills.

    Codex injects every Skill it finds in all of its roots and offers no
    allow-list; per-path `enabled` entries are the only lever, verified against
    `codex debug prompt-input`. Two things follow from how that lever behaves:

    - The override is merged with the user's own ~/.codex/config.toml rather
      than replacing it, so a Skill the user disabled there stays invisible
      unless it is enabled here explicitly. Every Skill the task needs is
      therefore forced on - the OA task's own Skill was disabled that way and
      had never been visible to a Codex run.
    - Codex also reads ~/.codex/skills and its plugin caches, so exclusion has
      to walk those roots too, or their Skills keep crowding the budget.

    Trimming matters for quality, not only size: over its Skill budget Codex
    truncates descriptions mid-sentence, and the description is what the Agent
    picks a Skill by. Returns an empty string when there is nothing to set.
    """
    keep_names = set(expand_skill_dependencies(allowed_names, target_root=target_root))
    roots = codex_skill_roots(target_root=target_root, codex_home=codex_home)
    files = sorted(
        {path for root in roots if root.is_dir() for path in root.rglob("SKILL.md")}
    )
    always_on = set(default_plugin_skill_files(roots[2]))
    enable: list[Path] = []
    disable: list[Path] = []
    for path in files:
        described = _describe_skill_file(path)
        if path in always_on or (described is not None and described[0] in keep_names):
            enable.append(path)
        else:
            disable.append(path)
    items = [f'{{path="{path}",enabled=true}}' for path in enable] + [
        f'{{path="{path}",enabled=false}}' for path in disable
    ]
    return f"skills.config=[{','.join(items)}]" if items else ""


def load_bundled_business_skills() -> tuple[BundledBusinessSkill, ...]:
    skills: list[BundledBusinessSkill] = []
    for expected_name in BUNDLED_BUSINESS_SKILL_NAMES:
        source_path = bundled_business_skills_root() / expected_name / "SKILL.md"
        try:
            content = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BusinessSkillValidationError(
                f"unable to read bundled Skill: {source_path}: {exc}"
            ) from exc
        frontmatter = _parse_frontmatter(content, source_path)
        name = _required_scalar(frontmatter, "name", source_path)
        if name != expected_name:
            raise BusinessSkillValidationError(
                f"bundled Skill must have matching name {expected_name!r}: {source_path}"
            )
        description = _required_scalar(frontmatter, "description", source_path)
        metadata = frontmatter.get("metadata")
        managed_by = metadata.get("managed_by") if isinstance(metadata, dict) else None
        if managed_by != MANAGED_BY:
            raise BusinessSkillValidationError(
                f"bundled Skill must contain managed marker {MANAGED_BY!r}: {source_path}"
            )
        skills.append(
            BundledBusinessSkill(
                name=name,
                description=description,
                managed_by=managed_by,
                source_path=source_path,
                content=content,
            )
        )
    return tuple(skills)


def install_bundled_business_skills(
    target_root: Path,
) -> tuple[InstalledBusinessSkill, ...]:
    target_root = Path(target_root).expanduser()
    _validate_install_target(target_root)
    skills = load_bundled_business_skills()
    source_root = bundled_business_skills_root()
    if target_root.resolve(strict=False) == source_root.resolve(strict=False):
        # The Skills are authored where they are installed, so installing them
        # into their own directory would copy each file onto itself. Report
        # what is there instead of staging a swap that can only lose data.
        return tuple(
            InstalledBusinessSkill(
                name=skill.name, install_path=target_root / skill.name / "SKILL.md"
            )
            for skill in skills
        )
    target_root_existed = target_root.exists()

    # Ownership and symlink checks happen before staging creates anything.
    for skill in skills:
        _check_swap_conflict(target_root / skill.name)

    resolved_target_root = target_root.resolve(strict=False)
    resolved_target_root.parent.mkdir(parents=True, exist_ok=True)
    transaction_root = Path(
        tempfile.mkdtemp(
            prefix=".ceo-business-skills-",
            dir=resolved_target_root.parent,
        )
    )
    staged_root = transaction_root / "staged"
    backup_root = transaction_root / "backups"
    swaps: list[_SwapState] = []
    cleanup_transaction = True
    try:
        staged_root.mkdir()
        backup_root.mkdir()
        for skill in skills:
            _stage_skill_package(
                skill.source_path.parent,
                staged_root / skill.name,
                skill.content.encode("utf-8"),
            )

        target_root.mkdir(parents=True, exist_ok=True)
        expected_resolved_root = target_root.resolve(strict=True)
        if expected_resolved_root != resolved_target_root:
            raise BusinessSkillInstallTargetError(
                f"business Skill target changed during installation: {target_root}"
            )

        # Each old directory remains in backups until every staged directory is live.
        for skill in skills:
            swap = _SwapState(
                staged_dir=staged_root / skill.name,
                target_dir=target_root / skill.name,
                backup_dir=backup_root / skill.name,
                had_existing=(target_root / skill.name).exists(),
            )
            swaps.append(swap)
            _swap_in(swap, target_root, expected_resolved_root)
    except BaseException as install_error:
        rollback_errors: list[tuple[Path, BaseException]] = []
        # Continue restoring other directories if one restore fails. Their backups
        # remain together until every directory reports a successful rollback.
        for swap in reversed(swaps):
            rollback_error = _rollback_swap(swap, target_root, resolved_target_root)
            if rollback_error is not None:
                rollback_errors.append((swap.target_dir, rollback_error))
        if rollback_errors:
            cleanup_transaction = False
            raise BusinessSkillInstallRollbackError(
                install_error,
                tuple(rollback_errors),
                transaction_root,
            ) from install_error
        if not target_root_existed and target_root.exists():
            target_root.rmdir()
        raise
    finally:
        if cleanup_transaction:
            shutil.rmtree(transaction_root)

    return tuple(
        InstalledBusinessSkill(name=skill.name, install_path=target_root / skill.name)
        for skill in skills
    )


def _validate_install_target(target_root: Path) -> None:
    try:
        resolved_target = target_root.resolve(strict=False)
        forbidden_target = (Path.home() / ".codex" / "skills").resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise BusinessSkillInstallTargetError(
            f"unable to validate business Skill install target: {target_root}"
        ) from exc
    if resolved_target == forbidden_target or forbidden_target in resolved_target.parents:
        raise BusinessSkillInstallTargetError(
            f"refusing to install business Skills into prohibited target: {forbidden_target}"
        )


def _validate_swap_destination(
    target_root: Path,
    expected_resolved_root: Path,
    target_dir: Path,
) -> None:
    if target_dir.is_symlink():
        raise BusinessSkillInstallTargetError(
            f"refusing symlinked business Skill directory: {target_dir}"
        )
    try:
        current_resolved_root = target_root.resolve(strict=True)
        resolved_destination = target_dir.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise BusinessSkillInstallTargetError(
            f"unable to validate business Skill destination: {target_dir}"
        ) from exc
    if (
        current_resolved_root != expected_resolved_root
        or resolved_destination.parent != expected_resolved_root
    ):
        raise BusinessSkillInstallTargetError(
            f"business Skill destination escaped target root: {target_dir}"
        )


def _is_service_managed(path: Path) -> bool:
    try:
        frontmatter = _parse_frontmatter(path.read_text(encoding="utf-8"), path)
    except (OSError, UnicodeError, BusinessSkillValidationError):
        return False
    metadata = frontmatter.get("metadata")
    return isinstance(metadata, dict) and metadata.get("managed_by") == MANAGED_BY


def _required_scalar(
    frontmatter: dict[str, object],
    key: str,
    source_path: Path,
) -> str:
    value = frontmatter.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BusinessSkillValidationError(
            f"bundled Skill must have nonempty {key}: {source_path}"
        )
    return value.strip()


def _parse_frontmatter(content: str, source_path: Path) -> dict[str, object]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise BusinessSkillValidationError(
            f"Skill must start with YAML frontmatter: {source_path}"
        )
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise BusinessSkillValidationError(
            f"Skill frontmatter is not closed: {source_path}"
        ) from exc

    result: dict[str, object] = {}
    section: dict[str, str] | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, separator, raw_value = line.strip().partition(":")
        if not separator or not key:
            raise BusinessSkillValidationError(
                f"invalid Skill frontmatter line in {source_path}: {line!r}"
            )
        value = _frontmatter_scalar(raw_value.strip())
        if indentation == 0:
            if value:
                result[key] = value
                section = None
            else:
                section = {}
                result[key] = section
        elif indentation == 2 and section is not None and value:
            section[key] = value
        else:
            raise BusinessSkillValidationError(
                f"unsupported Skill frontmatter structure in {source_path}: {line!r}"
            )
    return result


def _frontmatter_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
