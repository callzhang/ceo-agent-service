from __future__ import annotations

from dataclasses import dataclass
import json
import os
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


@dataclass
class _SwapState:
    staged_dir: Path
    target_dir: Path
    backup_dir: Path
    had_existing: bool
    backup_moved: bool = False
    installed: bool = False


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


def render_business_skill_protocol(
    catalog: tuple[BusinessSkillCatalogEntry, ...],
) -> str:
    inventory = [
        {"name": item.name, "path": str(item.skill_path)} for item in catalog
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
        target_dir = target_root / skill.name
        target = target_dir / "SKILL.md"
        if target_dir.is_symlink():
            raise BusinessSkillInstallTargetError(
                f"refusing symlinked business Skill directory: {target_dir}"
            )
        if target_dir.exists():
            if not target.is_file() or not _is_service_managed(target):
                raise BusinessSkillInstallConflict(
                    f"refusing to overwrite user-owned Skill: {target_dir}"
                )

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
            if swap.had_existing:
                os.replace(swap.target_dir, swap.backup_dir)
                swap.backup_moved = True
            _validate_swap_destination(
                target_root,
                expected_resolved_root,
                swap.target_dir,
            )
            os.replace(swap.staged_dir, swap.target_dir)
            swap.installed = True
    except BaseException as install_error:
        rollback_errors: list[tuple[Path, BaseException]] = []
        # Continue restoring other directories if one restore fails. Their backups
        # remain together until every directory reports a successful rollback.
        for swap in reversed(swaps):
            try:
                if swap.installed:
                    os.replace(swap.target_dir, swap.staged_dir)
                    swap.installed = False
                if swap.backup_moved:
                    _validate_swap_destination(
                        target_root,
                        resolved_target_root,
                        swap.target_dir,
                    )
                    os.replace(swap.backup_dir, swap.target_dir)
                    swap.backup_moved = False
            except BaseException as rollback_error:
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
    except (OSError, BusinessSkillValidationError):
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
