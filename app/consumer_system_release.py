"""Publish the reviewed Consumer/Audit contracts in the stopped deploy window."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any

from app.agent_cron.models import ScheduledTaskSkillRef
from app.managed_skills import (
    REPOSITORY_IMPORT_SOURCE,
    RUNTIME_EDIT_SOURCE,
    RuntimeSkillBinding,
)
from app.store import AutoReplyStore, NATIVE_REPLY_TASK_CURRENT_SQL


MANAGED_NAMES = (
    "ceo-calendar-invite",
    "ceo-daily-report",
    "ceo-document-review",
    "ceo-mail-review",
    "ceo-message-triage",
    "ceo-weekly-report",
    "ceo-weekly-okr-report",
)
SKILL_PATHS = tuple(f"ci/shared-skills/{name}/SKILL.md" for name in MANAGED_NAMES)
EXPECTED_SOURCES = frozenset(
    (
        *SKILL_PATHS,
        "ci/shared-skills/ceo-weekly-report/README.md",
        "ci/shared-skills/ceo-weekly-report/scripts/report_contract.py",
        "ci/shared-skills/ceo-weekly-report/scripts/validate_run.py",
        "ci/shared-skills/ceo-weekly-report/scripts/render_dingtalk_jsonml.py",
        "ci/shared-skills/ceo-weekly-report/scripts/jsonml_check.py",
        "ci/shared-skills/ceo-weekly-report/scripts/materials.py",
        "ci/shared-skills/ceo-daily-report/scripts/window.py",
        "ci/shared-skills/ceo-daily-report/scripts/destination.py",
        "ci/shared-skills/ceo-weekly-okr-report/SKILL.md",
        "ci/shared-skills/ceo-weekly-okr-report/scripts/report.py",
        "ci/shared-skills/ceo-weekly-okr-report/scripts/destination.py",
        "ci/shared-skills/ceo-weekly-okr-report/scripts/analysis.schema.json",
        "ci/shared-skills/ceo-weekly-report/references/dingtalk-runbook.md",
        "ci/shared-skills/dingtalk-oa-approval/SKILL.md",
        "ci/shared-skills/stardust-oa-finance-review/SKILL.md",
        "ci/shared-skills/stardust-oa-finance-review/references/finance-rule-cards.md",
        "ci/shared-skills/stardust-oa-people-review/SKILL.md",
        "ci/shared-skills/stardust-oa-attendance-travel-review/SKILL.md",
        "app/defaults/audit_rules.md",
        "ci/shared-skills/ceo-wechat/SKILL.md",
    )
)
DEFAULT_MANIFEST = Path("ci/consumer-system-contract-release.json")
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
LOAD_READBACK_TIMEOUT_SECONDS = 15 * 60
LOAD_READBACK_POLL_SECONDS = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_replace(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True)
class _FilePlan:
    source: str
    target: Path
    old_sha256: str | None
    old_managed_sha256: str | None
    new_sha256: str
    old_bytes: bytes
    new_bytes: bytes
    mode: int
    old_existed: bool


def _load_file_plan(
    root: Path,
    skills_root: Path,
    audit_rules_path: Path,
    manifest_path: Path,
) -> tuple[_FilePlan, ...]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("Consumer system release manifest is invalid")
    entries = manifest["files"]
    if {item.get("source") for item in entries} != EXPECTED_SOURCES or len(
        entries
    ) != len(EXPECTED_SOURCES):
        raise ValueError(
            "Consumer system release manifest must name the exact approved files"
        )
    plans = []
    for item in entries:
        source_name = item["source"]
        target_name = item.get("target")
        expected_target = (
            "audit_rules"
            if source_name == "app/defaults/audit_rules.md"
            else "skills/" + source_name.removeprefix("ci/shared-skills/")
        )
        if target_name != expected_target:
            raise ValueError(f"Consumer system release target mismatch: {source_name}")
        source = root / source_name
        target = (
            audit_rules_path
            if target_name == "audit_rules"
            else skills_root / target_name.removeprefix("skills/")
        )
        if (
            source.is_symlink()
            or target.is_symlink()
            or not source.is_file()
            or (target.exists() and not target.is_file())
        ):
            raise ValueError(
                f"Consumer system release file is missing or linked: {source_name}"
            )
        old_existed = target.exists()
        old_bytes = target.read_bytes() if old_existed else b""
        new_bytes = source.read_bytes()
        old_sha = item.get("old_sha256")
        old_managed_sha = item.get("old_managed_sha256")
        new_sha = item.get("new_sha256")
        if (
            old_existed
            and (not isinstance(old_sha, str) or not SHA256_HEX.fullmatch(old_sha))
        ) or (not old_existed and old_sha is not None):
            raise ValueError(
                f"Consumer system release old SHA is invalid: {source_name}"
            )
        if not isinstance(new_sha, str) or not SHA256_HEX.fullmatch(new_sha):
            raise ValueError(
                f"Consumer system release new SHA is invalid: {source_name}"
            )
        if source_name in SKILL_PATHS:
            if old_existed and (
                not isinstance(old_managed_sha, str)
                or not SHA256_HEX.fullmatch(old_managed_sha)
            ):
                raise ValueError(
                    f"Consumer system release old managed SHA is invalid: {source_name}"
                )
        elif old_managed_sha is not None:
            raise ValueError(
                f"Consumer system release unexpected managed SHA: {source_name}"
            )
        if (old_existed and old_sha != _sha256(old_bytes)) or new_sha != _sha256(
            new_bytes
        ):
            raise ValueError(f"Consumer system release SHA mismatch: {source_name}")
        plans.append(
            _FilePlan(
                source_name,
                target,
                old_sha,
                old_managed_sha,
                new_sha,
                old_bytes,
                new_bytes,
                stat.S_IMODE(target.stat().st_mode) if old_existed else 0o644,
                old_existed,
            )
        )
    return tuple(plans)


class ConsumerSystemPublication:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        plans: tuple[_FilePlan, ...],
        receipt_dir: Path,
        old_config_id: int,
        original_task_refs: dict[int, tuple[ScheduledTaskSkillRef, ...]],
        original_task_versions: dict[int, int],
        bindings: tuple[RuntimeSkillBinding, ...],
    ) -> None:
        self.store = store
        self.plans = plans
        self.receipt_dir = receipt_dir
        self.old_config_id = old_config_id
        self.original_task_refs = original_task_refs
        self.original_task_versions = original_task_versions
        self.bindings = bindings
        self.updated_task_versions: dict[int, int] = {}
        self.new_config_id: int | None = None
        self.new_revisions: dict[str, int] = {}
        self._written: list[int] = []
        self.status = "prepared"
        self.task_definitions: dict[int, tuple[object, object]] = {}

    def _save_receipt(self) -> None:
        payload: dict[str, Any] = {
            "release_kind": "consumer_system_contracts_v1",
            "status": self.status,
            "old_config_id": self.old_config_id,
            "new_config_id": self.new_config_id,
            "files": [
                {
                    "source": plan.source,
                    "target": str(plan.target),
                    "old_sha256": plan.old_sha256,
                    "old_managed_sha256": plan.old_managed_sha256,
                    "new_sha256": plan.new_sha256,
                    "mode": plan.mode,
                    "old_existed": plan.old_existed,
                    "backup": f"old-{index}",
                }
                for index, plan in enumerate(self.plans)
            ],
            "scheduled_task_refs": {
                str(task_id): [asdict(ref) for ref in refs]
                for task_id, refs in self.original_task_refs.items()
            },
            "original_task_versions": self.original_task_versions,
            "task_definitions": {
                str(key): {"before": asdict(pair[0]), "after": asdict(pair[1])}
                for key, pair in self.task_definitions.items()
            },
            "updated_task_versions": self.updated_task_versions,
        }
        (self.receipt_dir / "receipt.json").write_text(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, indent=2, default=str
            )
            + "\n",
            encoding="utf-8",
        )

    def publish_files(self) -> None:
        self.receipt_dir.mkdir(parents=True, exist_ok=False)
        for index, plan in enumerate(self.plans):
            (self.receipt_dir / f"old-{index}").write_bytes(plan.old_bytes)
        self._save_receipt()
        for index, plan in enumerate(self.plans):
            _atomic_replace(plan.target, plan.new_bytes, plan.mode)
            self._written.append(index)

    def publish(self) -> None:
        self.publish_files()
        revisions = _target_revisions(self.store, self.plans)
        self.new_revisions = revisions
        for task_id, refs in self.original_task_refs.items():
            updated = tuple(
                replace(ref, managed_revision_id=revisions[ref.skill_name])
                if ref.skill_source == "managed" and ref.skill_name in MANAGED_NAMES
                else ref
                for ref in refs
            )
            before = self.store.get_scheduled_task(task_id)
            task = self.store.update_scheduled_task(
                task_id,
                expected_version=self.original_task_versions[task_id],
                skill_refs=updated,
            )
            self.updated_task_versions[task_id] = task.version
            self.task_definitions[task_id] = (before, task)
            self._save_receipt()
        next_bindings = [
            {
                "skill_id": binding.skill_id,
                "revision_id": revisions.get(
                    self.store.get_managed_skill(binding.skill_id).name,
                    binding.revision_id,
                ),
                "enabled": binding.enabled,
                "load_order": binding.load_order,
                "purpose": binding.purpose,
            }
            for binding in self.bindings
        ]
        present = {item["skill_id"] for item in next_bindings}
        for name in MANAGED_NAMES:
            skill = self.store.get_managed_skill_by_name(name)
            if skill.id not in present:
                next_bindings.append(
                    {
                        "skill_id": skill.id,
                        "revision_id": revisions[name],
                        "enabled": True,
                        "load_order": max((item["load_order"] for item in next_bindings), default=-1) + 1,
                        "purpose": "scheduled_task",
                    }
                )
        config = self.store.create_runtime_skill_config(
            next_bindings,
            expected_parent_id=self.old_config_id,
        )
        self.new_config_id = config.id
        self._save_receipt()
        self._migrate_task_defaults(config.id)

    def _migrate_task_defaults(self, config_id: int) -> None:
        """Apply the approved report-definition migration while service is stopped."""
        from app.agent_cron.options import ScheduledTaskOptionService
        from app.agent_cron.seeds import (
            SCHEDULED_TASK_DEFAULTS,
            _seed_task,
            _upgrade_default_copy,
        )
        from app.agent_runtime_production import PRODUCTION_RUNTIME_CAPABILITIES
        from app.managed_skills import RuntimeSkillSnapshot
        from app.skill_files import SkillFileService

        skills_root = next(
            plan.target.parent for plan in self.plans if plan.source == SKILL_PATHS[0]
        ).parent
        bindings = self.store.list_runtime_skill_bindings(config_id)
        snapshot = RuntimeSkillSnapshot(
            config_id,
            tuple(
                self.store.get_managed_skill_revision(b.revision_id)
                for b in bindings
                if b.enabled
            ),
        )
        options = ScheduledTaskOptionService(
            store=self.store,
            environment=os.environ,
            runtime_snapshots=PRODUCTION_RUNTIME_CAPABILITIES,
            operation_skill_files=SkillFileService(skills_root),
            runtime_skill_snapshot=snapshot,
        )
        existing = {
            task.migration_key: task for task in self.store.list_scheduled_tasks()
        }
        for default in SCHEDULED_TASK_DEFAULTS:
            if default.command or default.migration_key not in existing:
                continue
            before = self.store.get_scheduled_task(existing[default.migration_key].id)
            try:
                after = _seed_task(default, store=self.store, options=options, now=None)
                after = _upgrade_default_copy(self.store, after, now=None)
            finally:
                current = self.store.get_scheduled_task(before.id)
                if current.version != before.version:
                    original = self.task_definitions.get(before.id, (before, before))[0]
                    self.task_definitions[before.id] = (original, current)
                    self.updated_task_versions[before.id] = current.version
                    self._save_receipt()

    def verify_loaded(
        self, *, wait_timeout_seconds: float = LOAD_READBACK_TIMEOUT_SECONDS,
    ) -> None:
        if self.new_config_id is None:
            raise ValueError("Consumer system release has no new runtime configuration")
        deadline = time.monotonic() + wait_timeout_seconds
        active = None
        expected_loaded: dict[str, str] | None = None
        while True:
            active = self.store.get_active_runtime_skill_config()
            current = active
            while current is not None and current.id != self.new_config_id:
                if current.parent_id is None:
                    current = None
                    break
                current = self.store.get_runtime_skill_config(current.parent_id)
            if active is not None and current is not None:
                bindings = self.store.list_runtime_skill_bindings(active.id)
                expected_loaded = {
                    str(binding.skill_id): self.store.get_managed_skill_revision(binding.revision_id).sha256
                    for binding in bindings if binding.enabled
                }
                for name in MANAGED_NAMES:
                    skill = self.store.get_managed_skill_by_name(name)
                    plan = next(
                        item for item in self.plans
                        if item.source == f"ci/shared-skills/{name}/SKILL.md"
                    )
                    if skill is None or expected_loaded.get(str(skill.id)) != plan.new_sha256:
                        raise ValueError(f"Consumer system active Skill revision mismatch: {name}")
                receipts = self.store.list_runtime_skill_load_receipts(active.id)
                live_receipts = [receipt for receipt in receipts if _pid_alive(receipt.pid)]
                if live_receipts:
                    if any(
                        not receipt.error
                        and json.loads(receipt.loaded_json) == expected_loaded
                        for receipt in live_receipts
                    ):
                        break
                    if any(not receipt.error for receipt in live_receipts):
                        raise ValueError(
                            "Consumer system runtime Skill load receipt digest mismatch"
                        )
                    raise ValueError("Consumer system runtime Skill load failed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if active is None or current is None:
                    raise ValueError(
                        "Consumer system runtime configuration did not activate within "
                        f"{wait_timeout_seconds / 60:g} minutes"
                    )
                raise ValueError(
                    "Consumer system runtime Skill load receipt did not appear within "
                    f"{wait_timeout_seconds / 60:g} minutes"
                )
            time.sleep(min(LOAD_READBACK_POLL_SECONDS, remaining))

        assert active is not None
        for plan in self.plans:
            if _sha256(plan.target.read_bytes()) != plan.new_sha256:
                raise ValueError(f"Consumer system released file changed: {plan.source}")
        for task_id, original_refs in self.original_task_refs.items():
            task = self.store.get_scheduled_task(task_id)
            if task is None:
                raise ValueError("Consumer system scheduled task disappeared")
            expected_refs = tuple(
                replace(ref, managed_revision_id=self.new_revisions[ref.skill_name])
                if ref.skill_source == "managed" and ref.skill_name in self.new_revisions
                else ref
                for ref in original_refs
            )
            if task.skill_refs != expected_refs:
                raise ValueError("Consumer system scheduled Skill reference changed")

    def rollback(self) -> None:
        for task_id, (before, after) in reversed(tuple(self.task_definitions.items())):
            self.store.update_scheduled_task(
                task_id,
                expected_version=after.version,
                name=before.name,
                description=before.description,
                prompt=before.prompt,
                command=before.command,
                runtime_id=before.runtime_id,
                runtime_options=before.runtime_options,
                required_runtime_capabilities=before.required_runtime_capabilities,
                working_directory=before.working_directory,
                skill_refs=before.skill_refs,
            )
        if self.new_config_id is not None:
            current = self.store.get_pending_or_active_runtime_skill_config()
            if current is not None and current.id != self.old_config_id:
                self.store.create_runtime_skill_rollback_config(
                    self.old_config_id,
                    expected_parent_id=current.id,
                )
        for index in reversed(self._written):
            plan = self.plans[index]
            if plan.old_existed:
                _atomic_replace(
                    plan.target,
                    (self.receipt_dir / f"old-{index}").read_bytes(),
                    plan.mode,
                )
            else:
                plan.target.unlink()
        self._written.clear()
        self.status = "rolled_back"
        if self.receipt_dir.exists():
            self._save_receipt()

    def finalize(self) -> None:
        # Keep the verified copy of the exact previous texts for this release.
        self.status = "verified"
        self._save_receipt()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _target_revisions(
    store: AutoReplyStore, plans: tuple[_FilePlan, ...]
) -> dict[str, int]:
    revisions = {}
    for plan in plans:
        name = plan.source.removeprefix("ci/shared-skills/").removesuffix("/SKILL.md")
        if name not in (*MANAGED_NAMES, "dingtalk-oa-approval"):
            continue
        skill = store.get_managed_skill_by_name(name)
        if skill is None:
            if name != "dingtalk-oa-approval" and plan.old_existed:
                raise ValueError(f"Consumer system managed Skill is missing: {name}")
            skill = store.create_managed_skill(name, name)
        existing = next(
            (
                revision
                for revision in store.list_managed_skill_revisions(skill.id)
                if revision.sha256 == plan.new_sha256
            ),
            None,
        )
        revision = existing or store.create_managed_skill_revision(
            skill.id,
            plan.new_bytes.decode("utf-8"),
            source=RUNTIME_EDIT_SOURCE
            if name == "dingtalk-oa-approval"
            else REPOSITORY_IMPORT_SOURCE,
            require_managed_marker=name != "dingtalk-oa-approval",
        )
        store.record_managed_skill_export(
            revision.id,
            sha256=revision.sha256,
            path=str(plan.target),
        )
        revisions[name] = revision.id
    return revisions


def prepare_consumer_system_contracts(
    *,
    root: Path,
    database_path: Path,
    operation_id: str,
    skills_root: Path | None = None,
    audit_rules_path: Path | None = None,
    manifest_path: Path | None = None,
) -> ConsumerSystemPublication:
    """Return the preflight snapshot before changing any release asset."""
    root = Path(root)
    skills_root = Path(skills_root or Path.home() / ".agents/skills")
    audit_rules_path = Path(audit_rules_path or root / "data/prompts/audit_rules.md")
    manifest_path = Path(manifest_path or root / DEFAULT_MANIFEST)
    plans = _load_file_plan(root, skills_root, audit_rules_path, manifest_path)
    store = AutoReplyStore(Path(database_path))
    original = store.get_pending_or_active_runtime_skill_config()
    if original is None:
        raise ValueError("Consumer system runtime Skill configuration is missing")
    old_by_name = {
        plan.source.removeprefix("ci/shared-skills/").removesuffix(
            "/SKILL.md"
        ): plan.old_managed_sha256
        for plan in plans
        if plan.source in SKILL_PATHS
    }
    bindings = store.list_runtime_skill_bindings(original.id)
    bound = {}
    for binding in bindings:
        skill = store.get_managed_skill(binding.skill_id)
        if skill is None or skill.name not in MANAGED_NAMES:
            continue
        revision = store.get_managed_skill_revision(binding.revision_id)
        if revision is None or revision.sha256 != old_by_name[skill.name]:
            raise ValueError(
                f"Consumer system configured Skill has a different revision: {skill.name}"
            )
        bound[skill.name] = binding
    if set(bound) != {name for name in MANAGED_NAMES if old_by_name[name] is not None}:
        raise ValueError("Consumer system runtime configuration lacks a managed Skill")
    # The current task may already have been edited; only the saved run snapshot
    # describes the entrypoints an interrupted execution will resume with.
    with store._connect() as db:
        active_runs = db.execute(
            "select scheduled.id from schedule_occurrences scheduled "
            "where scheduled.dispatch_status='pending' or "
            "(scheduled.execution_kind='reply_task' and scheduled.execution_id in "
            f"(select cast(id as text) from ({NATIVE_REPLY_TASK_CURRENT_SQL}) "
            "where channel='scheduled' and status in ('pending','processing')))"
        ).fetchall()
    for row in active_runs:
        run = store.get_scheduled_task_run(row["id"])
        if run.snapshot.command == "weekly-okr-report" or any(
            ref.skill_name in {"ceo-weekly-report", "ceo-daily-report"}
            for ref in run.snapshot.skill_refs
        ):
            raise ValueError(
                f"report execution must drain before migration: scheduled run {run.id}"
            )
    original_task_refs = {}
    original_task_versions = {}
    for task in store.list_scheduled_tasks():
        affected = [
            ref
            for ref in task.skill_refs
            if ref.skill_source == "managed" and ref.skill_name in MANAGED_NAMES
        ]
        if not affected:
            continue
        for ref in affected:
            revision = store.get_managed_skill_revision(ref.managed_revision_id)
            if revision is None or revision.sha256 != old_by_name[ref.skill_name]:
                raise ValueError(
                    f"Consumer system scheduled Skill has a different revision: {ref.skill_name}"
                )
        original_task_refs[task.id] = task.skill_refs
        original_task_versions[task.id] = task.version
    receipt_dir = Path(database_path).parent / "release-receipts" / operation_id
    return ConsumerSystemPublication(
        store=store,
        plans=plans,
        receipt_dir=receipt_dir,
        old_config_id=original.id,
        original_task_refs=original_task_refs,
        original_task_versions=original_task_versions,
        bindings=tuple(bindings),
    )
