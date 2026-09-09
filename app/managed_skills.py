from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING
from pathlib import Path

from app.business_skills import (
    BUNDLED_BUSINESS_SKILL_NAMES,
    MANAGED_BY,
    BusinessSkillValidationError,
    _parse_frontmatter,
    _required_scalar,
    load_bundled_business_skills,
)

if TYPE_CHECKING:
    from app.store import AutoReplyStore


class ManagedSkillValidationError(ValueError):
    """Raised when managed Skill content does not meet the persisted contract."""


def validate_managed_skill_name(name: str) -> str:
    """Require a portable single-directory identifier for a managed Skill."""
    if (
        not isinstance(name, str)
        or not name
        or name != name.strip()
        or not name[0].isascii()
        or not name[0].isalnum()
        or any(
            not character.isascii()
            or not (character.isalnum() or character in {"-", "_"})
            for character in name
        )
    ):
        raise ManagedSkillValidationError(f"invalid managed Skill name: {name!r}")
    return name


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
class ManagedSkillExportReceipt:
    """Immutable evidence that one revision was explicitly exported."""

    id: int
    revision_id: int
    sha256: str
    path: str
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


REPOSITORY_IMPORT_SOURCE = "repository:skills"
FEEDBACK_ITERATION_SKILL_NAME = "ceo-feedback-iteration"
WECHAT_SKILL_NAME = "ceo-wechat"
EMAIL_CLASSIFIER_SKILL_NAME = "ceo-email-classifier"
MINUTES_SYNC_SKILL_NAME = "ceo-minutes-sync"
REPOSITORY_MANAGED_SKILL_NAMES = (
    *BUNDLED_BUSINESS_SKILL_NAMES,
    FEEDBACK_ITERATION_SKILL_NAME,
    WECHAT_SKILL_NAME,
    EMAIL_CLASSIFIER_SKILL_NAME,
    MINUTES_SYNC_SKILL_NAME,
)


def _repository_managed_skills() -> tuple[tuple[str, str], ...]:
    """Return only the repository baselines owned by this service.

    The feedback-iteration protocol is a system capability rather than a
    business producer, but it is still a managed runtime binding and therefore
    must receive the same immutable import treatment as the business Skills.
    """
    business = tuple(
        (skill.name, skill.content) for skill in load_bundled_business_skills()
    )
    repository_skills_root = Path(__file__).resolve().parents[1] / "skills"

    def load_runtime_skill(name: str) -> str:
        source_path = repository_skills_root / name / "SKILL.md"
        try:
            content = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManagedSkillValidationError(
                f"unable to read managed Skill: {source_path}: {exc}"
            ) from exc
        validate_managed_skill_content(name, content)
        return content

    return (
        *business,
        (
            FEEDBACK_ITERATION_SKILL_NAME,
            load_runtime_skill(FEEDBACK_ITERATION_SKILL_NAME),
        ),
        (WECHAT_SKILL_NAME, load_runtime_skill(WECHAT_SKILL_NAME)),
        (EMAIL_CLASSIFIER_SKILL_NAME, load_runtime_skill(EMAIL_CLASSIFIER_SKILL_NAME)),
        (MINUTES_SYNC_SKILL_NAME, load_runtime_skill(MINUTES_SYNC_SKILL_NAME)),
    )


@dataclass(frozen=True)
class RepositoryManagedSkillImport:
    name: str
    revision_id: int
    revision_number: int
    sha256: str
    source: str


@dataclass(frozen=True)
class RepositoryManagedSkillExport:
    name: str
    revision_id: int
    sha256: str
    path: Path
    receipt: ManagedSkillExportReceipt


def import_repository_managed_skills(
    store: "AutoReplyStore",
) -> tuple[RepositoryManagedSkillImport, ...]:
    """Import only this service's bundled repository Skills once.

    A pre-existing non-reserved managed name is deliberately left alone. The
    reserved email-classifier name is adopted by appending the exact repository
    revision while preserving every user revision. Repository import never
    scans global agent, plugin, or runtime directories.
    """
    with store.managed_skill_baseline_initialization_lock():
        return _import_repository_managed_skills_locked(store)


def _import_repository_managed_skills_locked(
    store: "AutoReplyStore",
) -> tuple[RepositoryManagedSkillImport, ...]:
    """Reconcile one complete baseline while the store lock is held."""
    imported: list[tuple[str, ManagedSkillRevision]] = []
    baseline: list[tuple[str, ManagedSkillRevision]] = []
    for name, content in _repository_managed_skills():
        existing = store.get_managed_skill_by_name(name)
        if existing is not None:
            revisions = store.list_managed_skill_revisions(existing.id)
            if name != EMAIL_CLASSIFIER_SKILL_NAME:
                if any(
                    revision.source == REPOSITORY_IMPORT_SOURCE
                    for revision in revisions
                ):
                    baseline.append((name, revisions[-1]))
                continue
            expected_digest = validate_managed_skill_content(name, content)
            exact_repository_revision = next(
                (
                    revision
                    for revision in revisions
                    if revision.source == REPOSITORY_IMPORT_SOURCE
                    and revision.sha256 == expected_digest
                    and revision.content == content
                ),
                None,
            )
            if exact_repository_revision is not None:
                baseline.append((name, exact_repository_revision))
                continue
            exact_repository_revision = store.create_managed_skill_revision(
                existing.id,
                content,
                source=REPOSITORY_IMPORT_SOURCE,
            )
            imported.append((name, exact_repository_revision))
            baseline.append((name, exact_repository_revision))
            continue
        skill = store.create_managed_skill(name, name)
        revision = store.create_managed_skill_revision(
            skill.id,
            content,
            source=REPOSITORY_IMPORT_SOURCE,
        )
        imported.append(
            (
                name,
                revision,
            )
        )
        baseline.append((name, revision))
    current = store.get_pending_or_active_runtime_skill_config()
    if baseline and current is None:
        store.create_runtime_skill_config(
            [
                {
                    "skill_id": revision.skill_id,
                    "revision_id": revision.id,
                    "enabled": True,
                    "load_order": index,
                    "purpose": (
                        "feedback_iteration"
                        if name == FEEDBACK_ITERATION_SKILL_NAME
                        else "email_classification"
                        if name == EMAIL_CLASSIFIER_SKILL_NAME
                        else "repository_import"
                    ),
                }
                for index, (name, revision) in enumerate(baseline)
            ],
            expected_parent_id=None,
        )
    elif baseline and current is not None:
        bindings = list(store.list_runtime_skill_bindings(current.id))
        valid_existing_bindings = all(
            (revision := store.get_managed_skill_revision(binding.revision_id))
            is not None
            and revision.skill_id == binding.skill_id
            for binding in bindings
        )
        if not valid_existing_bindings:
            return tuple(
                RepositoryManagedSkillImport(
                    name=name,
                    revision_id=revision.id,
                    revision_number=revision.revision_number,
                    sha256=revision.sha256,
                    source=revision.source,
                )
                for name, revision in imported
            )
        classifier = next(
            (
                revision
                for name, revision in baseline
                if name == EMAIL_CLASSIFIER_SKILL_NAME
            ),
            None,
        )
        classifier_binding = next(
            (
                binding
                for binding in bindings
                if classifier is not None and binding.skill_id == classifier.skill_id
            ),
            None,
        )
        classifier_upgrade_required = classifier is not None and (
            classifier_binding is None
            or classifier_binding.revision_id != classifier.id
            or not classifier_binding.enabled
            or classifier_binding.purpose != "email_classification"
        )
        if classifier_upgrade_required:
            assert classifier is not None
            next_load_order = (
                max((binding.load_order for binding in bindings), default=-1) + 1
            )
            upgraded_bindings = [
                {
                    "skill_id": binding.skill_id,
                    "revision_id": (
                        classifier.id
                        if binding.skill_id == classifier.skill_id
                        else binding.revision_id
                    ),
                    "enabled": (
                        True
                        if binding.skill_id == classifier.skill_id
                        else binding.enabled
                    ),
                    "load_order": binding.load_order,
                    "purpose": (
                        "email_classification"
                        if binding.skill_id == classifier.skill_id
                        else binding.purpose
                    ),
                }
                for binding in bindings
            ]
            if classifier_binding is None:
                upgraded_bindings.append(
                    {
                        "skill_id": classifier.skill_id,
                        "revision_id": classifier.id,
                        "enabled": True,
                        "load_order": next_load_order,
                        "purpose": "email_classification",
                    }
                )
            store.create_runtime_skill_config(
                upgraded_bindings,
                expected_parent_id=current.id,
            )
            return tuple(
                RepositoryManagedSkillImport(
                    name=name,
                    revision_id=revision.id,
                    revision_number=revision.revision_number,
                    sha256=revision.sha256,
                    source=revision.source,
                )
                for name, revision in imported
            )
        repository_owned_config = all(
            (revision := store.get_managed_skill_revision(binding.revision_id))
            is not None
            and revision.source == REPOSITORY_IMPORT_SOURCE
            for binding in bindings
        )
        if not repository_owned_config:
            return tuple(
                RepositoryManagedSkillImport(
                    name=name,
                    revision_id=revision.id,
                    revision_number=revision.revision_number,
                    sha256=revision.sha256,
                    source=revision.source,
                )
                for name, revision in imported
            )
        bound = {binding.skill_id for binding in bindings}
        missing = [
            (name, revision)
            for name, revision in baseline
            if revision.skill_id not in bound
        ]
        if missing:
            store.create_runtime_skill_config(
                [
                    {
                        "skill_id": binding.skill_id,
                        "revision_id": binding.revision_id,
                        "enabled": binding.enabled,
                        "load_order": index,
                        "purpose": binding.purpose,
                    }
                    for index, binding in enumerate(bindings)
                ]
                + [
                    {
                        "skill_id": revision.skill_id,
                        "revision_id": revision.id,
                        "enabled": True,
                        "load_order": len(bindings) + index,
                        "purpose": (
                            "feedback_iteration"
                            if name == FEEDBACK_ITERATION_SKILL_NAME
                            else "email_classification"
                            if name == EMAIL_CLASSIFIER_SKILL_NAME
                            else "repository_import"
                        ),
                    }
                    for index, (name, revision) in enumerate(missing)
                ],
                expected_parent_id=current.id,
            )
    return tuple(
        RepositoryManagedSkillImport(
            name=name,
            revision_id=revision.id,
            revision_number=revision.revision_number,
            sha256=revision.sha256,
            source=revision.source,
        )
        for name, revision in imported
    )


def export_managed_skill_revision(
    store: "AutoReplyStore", revision_id: int, *, skills_root: Path | None = None
) -> RepositoryManagedSkillExport:
    """Explicitly export one immutable local revision into this repository.

    Export is deliberately separate from save and activation: runtime state is
    already complete without Git, while this adapter is the opt-in bridge for
    engineers who want a managed revision committed to the repository.
    """
    revision = store.get_managed_skill_revision(revision_id)
    if revision is None:
        raise ValueError("managed Skill revision does not exist")
    skill = store.get_managed_skill(revision.skill_id)
    if skill is None:
        raise ValueError("managed Skill does not exist")
    name = validate_managed_skill_name(skill.name)
    root = Path(
        skills_root or (Path(__file__).resolve().parents[1] / "skills")
    ).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManagedSkillValidationError(
            f"unable to prepare managed Skill export root: {root}"
        ) from exc
    if root.is_symlink() or resolved_root != root.absolute():
        raise ManagedSkillValidationError(
            f"refusing symlinked managed Skill export root: {root}"
        )
    destination = root / name / "SKILL.md"
    if destination.parent.is_symlink():
        raise ManagedSkillValidationError(
            f"refusing symlinked managed Skill export directory: {destination.parent}"
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_destination = destination.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ManagedSkillValidationError(
            f"unable to validate managed Skill export destination: {destination}"
        ) from exc
    if (
        destination.parent.is_symlink()
        or resolved_destination.parent.parent != resolved_root
    ):
        raise ManagedSkillValidationError(
            f"managed Skill export destination escaped root: {destination}"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".managed-skill-export-", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(revision.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    receipt = store.record_managed_skill_export(
        revision.id, sha256=revision.sha256, path=str(destination)
    )
    return RepositoryManagedSkillExport(
        name=skill.name,
        revision_id=revision.id,
        sha256=revision.sha256,
        path=destination,
        receipt=receipt,
    )


def resolve_pending_runtime_skills(
    store: "AutoReplyStore", *, pid: int
) -> RuntimeSkillSnapshot:
    """Resolve one immutable startup snapshot and persist its load receipt."""
    import_repository_managed_skills(store)
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
                raise ValueError(
                    f"revision missing for managed Skill {binding.skill_id}"
                )
            revisions.append(revision)
        return RuntimeSkillSnapshot(config_id=candidate.id, revisions=tuple(revisions))

    try:
        snapshot = load_snapshot(config)
        store.record_runtime_skill_load(
            config.id,
            pid=pid,
            loaded={
                revision.skill_id: revision.sha256 for revision in snapshot.revisions
            },
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
            loaded={
                revision.skill_id: revision.sha256 for revision in snapshot.revisions
            },
        )
        return snapshot


def validate_managed_skill_content(name: str, content: str) -> str:
    """Validate exact UTF-8 Skill text and return its exact-content SHA-256."""
    name = validate_managed_skill_name(name)
    if not isinstance(content, str):
        raise ManagedSkillValidationError("managed Skill content must be text")
    try:
        encoded_content = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ManagedSkillValidationError(
            "managed Skill content must be UTF-8"
        ) from exc

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
