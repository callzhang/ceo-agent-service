from __future__ import annotations

import hashlib
import json
import os
import subprocess
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

# Skills the service depends on that live only in the runtime tree: they have no
# repository baseline to import from, so the file on disk is their first
# revision. Without this they were edited in place with no history at all.
RUNTIME_ONLY_VERSIONED_SKILL_NAMES = ("dingtalk-oa-approval",)

VERSIONED_RUNTIME_SKILL_NAMES = (
    *REPOSITORY_MANAGED_SKILL_NAMES,
    *RUNTIME_ONLY_VERSIONED_SKILL_NAMES,
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


def repository_managed_skill_content(name: str) -> str:
    """Return the current exact repository baseline for one managed Skill."""
    try:
        return next(
            content
            for repository_name, content in _repository_managed_skills()
            if repository_name == name
        )
    except StopIteration as exc:
        raise ManagedSkillValidationError(
            f"repository managed Skill does not exist: {name}"
        ) from exc


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
    store: "AutoReplyStore", *, upgrade_existing_runtime: bool = True,
) -> tuple[RepositoryManagedSkillImport, ...]:
    """Import only this service's bundled repository Skills once.

    A pre-existing non-reserved managed name is deliberately left alone. The
    reserved email-classifier name is adopted by appending the exact repository
    revision while preserving every user revision. Repository import never
    scans global agent, plugin, or runtime directories.
    """
    with store.managed_skill_baseline_initialization_lock():
        return _import_repository_managed_skills_locked(store, upgrade_existing_runtime=upgrade_existing_runtime)


def _import_repository_managed_skills_locked(
    store: "AutoReplyStore", *, upgrade_existing_runtime: bool = True,
) -> tuple[RepositoryManagedSkillImport, ...]:
    """Reconcile one complete baseline while the store lock is held."""
    imported: list[tuple[str, ManagedSkillRevision]] = []
    baseline: list[tuple[str, ManagedSkillRevision]] = []
    for name, content in _repository_managed_skills():
        existing = store.get_managed_skill_by_name(name)
        if existing is not None:
            revisions = store.list_managed_skill_revisions(existing.id)
            repository_revisions = tuple(
                revision for revision in revisions
                if revision.source == REPOSITORY_IMPORT_SOURCE
            )
            if not repository_revisions:
                if name != EMAIL_CLASSIFIER_SKILL_NAME:
                    continue
                revision = store.create_managed_skill_revision(
                    existing.id, content, source=REPOSITORY_IMPORT_SOURCE
                )
                imported.append((name, revision))
                baseline.append((name, revision))
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
                baseline.append((name, revisions[-1] if len(repository_revisions) != len(revisions) else exact_repository_revision))
                continue
            exact_repository_revision = store.create_managed_skill_revision(
                existing.id,
                content,
                source=REPOSITORY_IMPORT_SOURCE,
            )
            imported.append((name, exact_repository_revision))
            baseline.append((name, revisions[-1] if len(repository_revisions) != len(revisions) else exact_repository_revision))
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
        if classifier_upgrade_required and upgrade_existing_runtime:
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
        # A Skill that has no binding at all is bound here whoever owns the
        # rest of the configuration. Only additions happen below -- every
        # existing binding is carried over verbatim -- so a configuration that
        # someone has edited is not overwritten by adding a Skill beside it.
        #
        # This used to require that *every* existing binding still pointed at a
        # repository revision. One Skill edited in settings therefore disabled
        # binding for every repository Skill imported afterwards: on
        # 2026-09-18 `ceo-message-triage` sat at a `settings` revision, so the
        # freshly imported `ceo-weekly-report` was never bound, the scheduled
        # task naming it could not resolve its Skill, and seeding raised.
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


RUNTIME_EDIT_SOURCE = "runtime:agents-skills"


class RuntimeSkillCaptureIncomplete(ManagedSkillValidationError):
    """Some runtime Skills could not be captured; every other one still was."""

    def __init__(self, problems: tuple[str, ...]):
        self.problems = problems
        super().__init__(
            "could not capture runtime Skill(s): " + "; ".join(problems)
        )


@dataclass(frozen=True)
class RuntimeSkillEditCapture:
    """One hand edit found in the runtime Skill tree and recorded as a revision."""

    name: str
    revision_id: int
    revision_number: int
    sha256: str


def capture_runtime_skill_edits(
    store: "AutoReplyStore", *, skills_root: Path | None = None
) -> tuple[RuntimeSkillEditCapture, ...]:
    """Record edits made directly in the runtime Skill tree as new revisions.

    The file is the runtime source every CLI on this machine reads, so an edit
    there is already in effect before this service ever sees it. Capturing it
    keeps the version history honest; without this, the next write from any other
    path would discard a change that was already live.

    A file whose content still matches the skill's latest revision is untouched.
    A repository-managed Skill with no revisions yet, or no file on disk, is
    skipped rather than invented: its baseline belongs to the import.

    A runtime-only Skill has no baseline anywhere else, so its file IS the first
    revision and is recorded as one.
    """
    root = Path(
        skills_root or (Path.home() / ".agents" / "skills")
    ).expanduser()
    captured: list[RuntimeSkillEditCapture] = []
    problems: list[str] = []
    for name in VERSIONED_RUNTIME_SKILL_NAMES:
        runtime_only = name in RUNTIME_ONLY_VERSIONED_SKILL_NAMES
        path = root / name / "SKILL.md"
        if not path.is_file():
            continue
        skill = store.get_managed_skill_by_name(name)
        if skill is None:
            if not runtime_only:
                continue
            try:
                skill = store.create_managed_skill(name, name)
            except Exception as exc:  # noqa: BLE001 - reported per Skill below
                problems.append(f"{name} ({path}): {exc}")
                continue
        revisions = store.list_managed_skill_revisions(skill.id)
        if not revisions and not runtime_only:
            continue
        # One unreadable or foreign file must not hide edits to the others, so
        # each Skill is attempted and every failure is named at the end.
        try:
            content = path.read_text(encoding="utf-8")
            digest = validate_managed_skill_content(
                name, content, require_managed_marker=not runtime_only
            )
            if revisions:
                latest = max(
                    revisions, key=lambda revision: revision.revision_number
                )
                if digest == latest.sha256:
                    continue
                # An edit that restores earlier content is a revert, and the
                # history is content-addressed, so there is no new revision to
                # write. Recording the export keeps the file tied to the
                # revision it now matches. Without this, `ceo-minutes-sync`
                # reverted to its first revision filed the same capture error
                # on every service start and scan.
                restored = next(
                    (
                        revision
                        for revision in revisions
                        if revision.sha256 == digest
                    ),
                    None,
                )
                if restored is not None:
                    store.record_managed_skill_export(
                        restored.id, sha256=restored.sha256, path=str(path)
                    )
                    continue
            revision = store.create_managed_skill_revision(
                skill.id,
                content,
                source=RUNTIME_EDIT_SOURCE,
                require_managed_marker=not runtime_only,
            )
            store.record_managed_skill_export(
                revision.id, sha256=revision.sha256, path=str(path)
            )
        except (
            OSError,
            UnicodeError,
            ManagedSkillValidationError,
            ValueError,
        ) as exc:
            # Naming the Skill matters: the capture used to raise out of the
            # loop, and the recorded error said only what went wrong.
            problems.append(f"{name} ({path}): {exc}")
            continue
        captured.append(
            RuntimeSkillEditCapture(
                name=name,
                revision_id=revision.id,
                revision_number=revision.revision_number,
                sha256=revision.sha256,
            )
        )
    if problems:
        raise RuntimeSkillCaptureIncomplete(tuple(problems))
    return tuple(captured)


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
    _reconcile_scheduled_task_skill_revisions(store)
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


def _reconcile_scheduled_task_skill_revisions(store: "AutoReplyStore") -> None:
    """Stage task-referenced managed revisions before the process snapshot loads.

    Settings can update a task's immutable Skill reference independently from
    the next-start runtime configuration.  Leaving those two facts divergent
    makes every scheduled occurrence fail with ``managed_revision_not_loaded``.
    The newest enabled task reference for each managed Skill is the only
    revision that can represent the current scheduled workload; stage it in a
    new immutable config and let the normal startup loader activate it.
    """
    current = store.get_pending_or_active_runtime_skill_config()
    if current is None:
        return

    selected: dict[int, tuple[object, object]] = {}
    for task in store.list_scheduled_tasks():
        if not task.enabled or task.deleted_at is not None:
            continue
        for ref in task.skill_refs:
            if ref.skill_source != "managed":
                continue
            if ref.managed_skill_id is None or ref.managed_revision_id is None:
                continue
            previous = selected.get(ref.managed_skill_id)
            candidate_key = (task.updated_at, task.id)
            if previous is None or candidate_key > previous[0]:
                selected[ref.managed_skill_id] = (
                    candidate_key,
                    ref.managed_revision_id,
                )

    if not selected:
        return
    bindings = list(store.list_runtime_skill_bindings(current.id))
    desired = {binding.skill_id: binding.revision_id for binding in bindings}
    changed = False
    for skill_id, (_key, revision_id) in selected.items():
        if desired.get(skill_id) != revision_id:
            desired[skill_id] = revision_id
            changed = True
    if not changed:
        return

    next_load_order = max((binding.load_order for binding in bindings), default=-1) + 1
    config_bindings = [
            {
                "skill_id": binding.skill_id,
                "revision_id": desired[binding.skill_id],
                "enabled": binding.enabled,
                "load_order": binding.load_order,
                "purpose": binding.purpose,
            }
            for binding in bindings
        ]
    bound = {binding.skill_id for binding in bindings}
    config_bindings.extend(
        {
            "skill_id": skill_id,
            "revision_id": revision_id,
            "enabled": True,
            "load_order": next_load_order + index,
            "purpose": "scheduled_task",
        }
        for index, (skill_id, (_key, revision_id)) in enumerate(selected.items())
        if skill_id not in bound
    )
    store.create_runtime_skill_config(
        config_bindings,
        expected_parent_id=current.id,
    )


def runtime_skill_snapshot_for_process(
    store: "AutoReplyStore",
    *,
    pid: int,
) -> RuntimeSkillSnapshot | None:
    """Rebuild only the exact managed revisions proven loaded by one process."""

    if type(pid) is not int or pid <= 0:
        raise ValueError("runtime Skill snapshot pid must be positive")
    active = store.get_active_runtime_skill_config()
    if active is None:
        return None
    receipts = tuple(
        receipt
        for receipt in reversed(store.list_runtime_skill_load_receipts(active.id))
        if not receipt.error
        and _process_is_alive(receipt.pid)
        and _process_descends_from(receipt.pid, pid)
    )
    receipt = receipts[0] if receipts else None
    if receipt is None:
        return None
    try:
        loaded = json.loads(receipt.loaded_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    revisions: list[ManagedSkillRevision] = []
    expected: dict[str, str] = {}
    for binding in store.list_runtime_skill_bindings(receipt.config_id):
        if not binding.enabled:
            continue
        revision = store.get_managed_skill_revision(binding.revision_id)
        if revision is None or revision.skill_id != binding.skill_id:
            return None
        revisions.append(revision)
        expected[str(binding.skill_id)] = revision.sha256
    if loaded != expected:
        return None
    return RuntimeSkillSnapshot(
        config_id=receipt.config_id,
        revisions=tuple(revisions),
    )


def _process_is_alive(pid: int) -> bool:
    """Return true only for a currently live, non-zombie process."""
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return True
    return not result.stdout.strip().startswith("Z")


def _process_descends_from(pid: int, ancestor_pid: int) -> bool:
    """Check whether pid is the supervisor itself or one of its children."""
    if pid == ancestor_pid:
        return True
    if type(ancestor_pid) is not int or ancestor_pid <= 0:
        return False
    current = pid
    seen: set[int] = set()
    for _ in range(32):
        if current in seen or current <= 1:
            return False
        seen.add(current)
        try:
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(current)],
                capture_output=True,
                text=True,
                check=False,
            )
            parent = int(result.stdout.strip())
        except (OSError, ValueError):
            return False
        if parent == ancestor_pid:
            return True
        current = parent
    return False


def validate_managed_skill_content(
    name: str, content: str, *, require_managed_marker: bool = True
) -> str:
    """Validate exact UTF-8 Skill text and return its exact-content SHA-256.

    A runtime-only Skill is versioned by this service but not owned by it: the
    operation-Skill catalog rejects any file carrying the ownership marker, so
    demanding one here would take the Skill away from the scheduled tasks that
    reference it. Those Skills are validated on name and description alone.
    """
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
    if require_managed_marker:
        metadata = frontmatter.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("managed_by") != MANAGED_BY:
            raise ManagedSkillValidationError(
                "Skill missing metadata.managed_by marker"
            )
    return hashlib.sha256(encoded_content).hexdigest()
