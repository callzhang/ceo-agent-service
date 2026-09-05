from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.business_skills import (
    MANAGED_BY,
    BusinessSkillValidationError,
    _parse_frontmatter,
    _required_scalar,
)


class ManagedSkillValidationError(ValueError):
    """Raised when managed Skill content does not meet the persisted contract."""


@dataclass(frozen=True)
class ManagedSkill:
    id: int
    name: str
    display_name: str
    created_at: str


@dataclass(frozen=True)
class ManagedSkillRevision:
    id: int
    skill_id: int
    revision_number: int
    content: str
    sha256: str
    parent_revision_id: int | None
    source: str
    created_at: str


def validate_managed_skill_content(name: str, content: str) -> str:
    """Validate exact UTF-8 Skill text and return its exact-content SHA-256."""
    if not isinstance(name, str) or not name.strip():
        raise ManagedSkillValidationError("managed Skill name must be nonempty")
    if not isinstance(content, str):
        raise ManagedSkillValidationError("managed Skill content must be text")
    try:
        encoded_content = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ManagedSkillValidationError("managed Skill content must be UTF-8") from exc

    source_path = Path(f"managed:{name}/SKILL.md")
    try:
        frontmatter = _parse_frontmatter(content, source_path)
        declared_name = _required_scalar(frontmatter, "name", source_path)
        _required_scalar(frontmatter, "description", source_path)
    except BusinessSkillValidationError as exc:
        raise ManagedSkillValidationError(str(exc)) from exc
    if declared_name != name:
        raise ManagedSkillValidationError("Skill name does not match managed Skill")
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("managed_by") != MANAGED_BY:
        raise ManagedSkillValidationError("Skill missing metadata.managed_by marker")
    return hashlib.sha256(encoded_content).hexdigest()
