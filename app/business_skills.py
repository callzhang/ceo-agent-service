from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
import shutil
import tempfile

import yaml


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
        staged_dir.mkdir()
        backup_dir.mkdir()
        (staged_dir / "SKILL.md").write_bytes(raw_content)
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


@dataclass
class _SwapState:
    staged_dir: Path
    target_dir: Path
    backup_dir: Path
    had_existing: bool
    backup_moved: bool = False
    installed: bool = False


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
    return Path(__file__).resolve().parents[1] / "skills"


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
        try:
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if not lines or lines[0].strip() != "---":
                continue
            frontmatter = yaml.safe_load("\n".join(lines[1 : lines.index("---", 1)]))
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            continue
        if not isinstance(frontmatter, dict):
            continue
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            continue
        name, description = name.strip(), description.strip()
        if not name or not description:
            continue
        if wanted is not None and name not in wanted:
            continue
        entries.append(
            BusinessSkillCatalogEntry(
                name=name, skill_path=path.resolve(), description=description
            )
        )
    return tuple(entries)


def render_business_skill_protocol(
    catalog: tuple[BusinessSkillCatalogEntry, ...],
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
        + json.dumps(inventory, ensure_ascii=False, sort_keys=True)
        + "\n\n## Required Skill protocol\n"
        "PROTOCOL PRECONDITION: before returning any Consumer outcome, call "
        "`agent_cli.read_skill` for at least one CEO business Skill from the exact "
        "catalog above. Choose the applicable Skill yourself from the full context; "
        "the service does not route the domain. Read every additional business or "
        "operation Skill needed for the judgment. Do not return an outcome before "
        "completing this read."
    )


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


def codex_skill_exclusion_override(
    allowed_names: Iterable[str], *, target_root: Path | None = None
) -> str:
    """Build the Codex `-c` override that disables every Skill outside the allow set.

    Codex always injects its own Skill catalog and offers no allow-list: the only
    working lever is marking individual Skills `enabled = false`, verified against
    `codex debug prompt-input`. Passing this on the command line keeps the user's
    own ~/.codex/config.toml untouched. Trimming matters for quality, not just
    size: over its Skill budget Codex truncates descriptions mid-sentence, which
    is what the Agent relies on to pick the right Skill.

    Returns an empty string when nothing needs disabling, so the caller can omit
    the flag entirely.
    """
    allowed = set(expand_skill_dependencies(allowed_names, target_root=target_root))
    keep = {
        entry.skill_path
        for entry in installed_runtime_skills(target_root, names=allowed)
    }
    excluded = [
        path
        for path in installed_runtime_skill_paths(target_root)
        if path.resolve() not in keep
    ]
    if not excluded:
        return ""
    items = ",".join(f'{{path="{path}",enabled=false}}' for path in excluded)
    return f"skills.config=[{items}]"


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
            staged_dir = staged_root / skill.name
            staged_dir.mkdir()
            (staged_dir / "SKILL.md").write_text(skill.content, encoding="utf-8")

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
