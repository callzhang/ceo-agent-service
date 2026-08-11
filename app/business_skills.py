from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4

from app.config import repo_root


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


def bundled_business_skills_root() -> Path:
    return repo_root() / "skills"


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
    skills = load_bundled_business_skills()
    target_root = Path(target_root)

    # Preflight every destination so one ownership conflict cannot cause a partial upgrade.
    for skill in skills:
        target_dir = target_root / skill.name
        target = target_dir / "SKILL.md"
        if target_dir.exists() or target_dir.is_symlink():
            if not target.is_file() or not _is_service_managed(target):
                raise BusinessSkillInstallConflict(
                    f"refusing to overwrite user-owned Skill: {target_dir}"
                )

    installed: list[InstalledBusinessSkill] = []
    for skill in skills:
        target_dir = target_root / skill.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(skill.content, encoding="utf-8")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        installed.append(
            InstalledBusinessSkill(name=skill.name, install_path=target_dir)
        )
    return tuple(installed)


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
