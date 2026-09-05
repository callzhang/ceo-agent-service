from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING
from pathlib import Path

from app.business_skills import (
    MANAGED_BY,
    BusinessSkillValidationError,
    _parse_frontmatter,
    _required_scalar,
)

if TYPE_CHECKING:
    from app.store import AutoReplyStore


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


@dataclass(frozen=True)
class RuntimeSkillConfig:
    id: int
    parent_id: int | None
    status: str
    created_at: str


@dataclass(frozen=True)
class RuntimeSkillBinding:
    config_id: int
    skill_id: int
    revision_id: int
    enabled: bool
    load_order: int
    purpose: str


@dataclass(frozen=True)
class RuntimeSkillLoadReceipt:
    id: int
    config_id: int
    pid: int
    loaded_json: str
    error: str
    created_at: str


@dataclass(frozen=True)
class RuntimeSkillSnapshot:
    """The exact managed Skill revisions loaded for one service process."""

    config_id: int
    revisions: tuple[ManagedSkillRevision, ...]

    def protocol(self) -> str:
        entries = "\n\n".join(
            f"## Managed runtime Skill: {revision.skill_id}@{revision.revision_number}\n"
            f"sha256: {revision.sha256}\n\n{revision.content}"
            for revision in self.revisions
        )
        return "## Managed runtime Skills\n" + entries if entries else ""


def resolve_pending_runtime_skills(
    store: "AutoReplyStore", *, pid: int
) -> RuntimeSkillSnapshot:
    """Resolve one immutable startup snapshot and persist its load receipt."""
    config = store.get_pending_or_active_runtime_skill_config()
    if config is None:
        return RuntimeSkillSnapshot(config_id=0, revisions=())
    def load_snapshot(candidate: RuntimeSkillConfig) -> RuntimeSkillSnapshot:
        revisions: list[ManagedSkillRevision] = []
        for binding in store.list_runtime_skill_bindings(candidate.id):
            if not binding.enabled:
                continue
            revision = store.get_managed_skill_revision(binding.revision_id)
            if revision is None or revision.skill_id != binding.skill_id:
                raise ValueError(f"revision missing for managed Skill {binding.skill_id}")
            revisions.append(revision)
        return RuntimeSkillSnapshot(config_id=candidate.id, revisions=tuple(revisions))

    try:
        snapshot = load_snapshot(config)
        store.record_runtime_skill_load(
            config.id,
            pid=pid,
            loaded={revision.skill_id: revision.sha256 for revision in snapshot.revisions},
        )
        return snapshot
    except Exception as exc:
        store.record_runtime_skill_load_failure(config.id, pid=pid, error=str(exc))
        active = store.get_active_runtime_skill_config()
        if active is None or active.id == config.id:
            raise
        snapshot = load_snapshot(active)
        store.record_runtime_skill_load(
            active.id,
            pid=pid,
            loaded={revision.skill_id: revision.sha256 for revision in snapshot.revisions},
        )
        return snapshot


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
