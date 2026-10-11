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
from typing import Any

from app.agent_cron.models import ScheduledTaskSkillRef
from app.managed_skills import REPOSITORY_IMPORT_SOURCE, RUNTIME_EDIT_SOURCE, RuntimeSkillBinding
from app.store import AutoReplyStore


MANAGED_NAMES = (
    "ceo-calendar-invite", "ceo-daily-report", "ceo-document-review",
    "ceo-mail-review", "ceo-message-triage", "ceo-weekly-report",
)
SKILL_PATHS = tuple(f"ci/shared-skills/{name}/SKILL.md" for name in MANAGED_NAMES)
EXPECTED_SOURCES = frozenset((
    *SKILL_PATHS,
    "ci/shared-skills/ceo-weekly-report/references/dingtalk-runbook.md",
    "ci/shared-skills/dingtalk-oa-approval/SKILL.md",
    "app/defaults/audit_rules.md",
    "ci/shared-skills/ceo-wechat/SKILL.md",
))
DEFAULT_MANIFEST = Path("ci/consumer-system-contract-release.json")
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_replace(path: Path, data: bytes, mode: int) -> None:
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
    old_sha256: str
    old_managed_sha256: str | None
    new_sha256: str
    old_bytes: bytes
    new_bytes: bytes
    mode: int


def _load_file_plan(
    root: Path, skills_root: Path, audit_rules_path: Path, manifest_path: Path,
) -> tuple[_FilePlan, ...]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("Consumer system release manifest is invalid")
    entries = manifest["files"]
    if {item.get("source") for item in entries} != EXPECTED_SOURCES or len(entries) != len(EXPECTED_SOURCES):
        raise ValueError("Consumer system release manifest must name the exact approved files")
    plans = []
    for item in entries:
        source_name = item["source"]
        target_name = item.get("target")
        expected_target = (
            "audit_rules" if source_name == "app/defaults/audit_rules.md"
            else "skills/" + source_name.removeprefix("ci/shared-skills/")
        )
        if target_name != expected_target:
            raise ValueError(f"Consumer system release target mismatch: {source_name}")
        source = root / source_name
        target = audit_rules_path if target_name == "audit_rules" else skills_root / target_name.removeprefix("skills/")
        if source.is_symlink() or target.is_symlink() or not source.is_file() or not target.is_file():
            raise ValueError(f"Consumer system release file is missing or linked: {source_name}")
        old_bytes = target.read_bytes()
        new_bytes = source.read_bytes()
        old_sha = item.get("old_sha256")
        old_managed_sha = item.get("old_managed_sha256")
        new_sha = item.get("new_sha256")
        if not isinstance(old_sha, str) or not SHA256_HEX.fullmatch(old_sha):
            raise ValueError(f"Consumer system release old SHA is invalid: {source_name}")
        if not isinstance(new_sha, str) or not SHA256_HEX.fullmatch(new_sha):
            raise ValueError(f"Consumer system release new SHA is invalid: {source_name}")
        if source_name in SKILL_PATHS:
            if not isinstance(old_managed_sha, str) or not SHA256_HEX.fullmatch(old_managed_sha):
                raise ValueError(f"Consumer system release old managed SHA is invalid: {source_name}")
        elif old_managed_sha is not None:
            raise ValueError(f"Consumer system release unexpected managed SHA: {source_name}")
        if old_sha != _sha256(old_bytes) or new_sha != _sha256(new_bytes):
            raise ValueError(f"Consumer system release SHA mismatch: {source_name}")
        plans.append(_FilePlan(
            source_name, target, old_sha, old_managed_sha, new_sha, old_bytes, new_bytes,
            stat.S_IMODE(target.stat().st_mode),
        ))
    return tuple(plans)


class ConsumerSystemPublication:
    def __init__(
        self, *, store: AutoReplyStore, plans: tuple[_FilePlan, ...],
        receipt_dir: Path, old_config_id: int,
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
                    "backup": f"old-{index}",
                }
                for index, plan in enumerate(self.plans)
            ],
            "scheduled_task_refs": {
                str(task_id): [asdict(ref) for ref in refs]
                for task_id, refs in self.original_task_refs.items()
            },
            "original_task_versions": self.original_task_versions,
            "updated_task_versions": self.updated_task_versions,
        }
        (self.receipt_dir / "receipt.json").write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
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
            task = self.store.update_scheduled_task(
                task_id, expected_version=self.original_task_versions[task_id],
                skill_refs=updated,
            )
            self.updated_task_versions[task_id] = task.version
            self._save_receipt()
        next_bindings = [
            {
                "skill_id": binding.skill_id,
                "revision_id": revisions.get(self.store.get_managed_skill(binding.skill_id).name, binding.revision_id),
                "enabled": binding.enabled,
                "load_order": binding.load_order,
                "purpose": binding.purpose,
            }
            for binding in self.bindings
        ]
        config = self.store.create_runtime_skill_config(
            next_bindings, expected_parent_id=self.old_config_id,
        )
        self.new_config_id = config.id
        self._save_receipt()

    def verify_loaded(self) -> None:
        if self.new_config_id is None:
            raise ValueError("Consumer system release has no new runtime configuration")
        active = self.store.get_active_runtime_skill_config()
        if active is None or active.id != self.new_config_id:
            raise ValueError("Consumer system runtime configuration did not activate")
        bindings = self.store.list_runtime_skill_bindings(active.id)
        expected_loaded = {
            str(binding.skill_id): self.store.get_managed_skill_revision(binding.revision_id).sha256
            for binding in bindings if binding.enabled
        }
        for name in MANAGED_NAMES:
            skill = self.store.get_managed_skill_by_name(name)
            plan = next(item for item in self.plans if item.source == f"ci/shared-skills/{name}/SKILL.md")
            if skill is None or expected_loaded.get(str(skill.id)) != plan.new_sha256:
                raise ValueError(f"Consumer system active Skill revision mismatch: {name}")
        receipts = self.store.list_runtime_skill_load_receipts(active.id)
        if not any(
            not receipt.error and _pid_alive(receipt.pid)
            and json.loads(receipt.loaded_json) == expected_loaded
            for receipt in receipts
        ):
            raise ValueError("Consumer system runtime Skill load receipt is missing")
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
        for task_id, updated_version in reversed(tuple(self.updated_task_versions.items())):
            self.store.update_scheduled_task(
                task_id, expected_version=updated_version,
                skill_refs=self.original_task_refs[task_id],
            )
        if self.new_config_id is not None:
            current = self.store.get_pending_or_active_runtime_skill_config()
            if current is not None and current.id != self.old_config_id:
                self.store.create_runtime_skill_rollback_config(
                    self.old_config_id, expected_parent_id=current.id,
                )
        for index in reversed(self._written):
            plan = self.plans[index]
            _atomic_replace(plan.target, (self.receipt_dir / f"old-{index}").read_bytes(), plan.mode)
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


def _target_revisions(store: AutoReplyStore, plans: tuple[_FilePlan, ...]) -> dict[str, int]:
    revisions = {}
    for plan in plans:
        name = plan.source.removeprefix("ci/shared-skills/").removesuffix("/SKILL.md")
        if name not in (*MANAGED_NAMES, "dingtalk-oa-approval"):
            continue
        skill = store.get_managed_skill_by_name(name)
        if skill is None:
            if name != "dingtalk-oa-approval":
                raise ValueError(f"Consumer system managed Skill is missing: {name}")
            skill = store.create_managed_skill(name, name)
        existing = next((
            revision for revision in store.list_managed_skill_revisions(skill.id)
            if revision.sha256 == plan.new_sha256
        ), None)
        revision = existing or store.create_managed_skill_revision(
            skill.id, plan.new_bytes.decode("utf-8"),
            source=RUNTIME_EDIT_SOURCE if name == "dingtalk-oa-approval" else REPOSITORY_IMPORT_SOURCE,
            require_managed_marker=name != "dingtalk-oa-approval",
        )
        store.record_managed_skill_export(
            revision.id, sha256=revision.sha256, path=str(plan.target),
        )
        revisions[name] = revision.id
    return revisions


def prepare_consumer_system_contracts(
    *, root: Path, database_path: Path, operation_id: str,
    skills_root: Path | None = None, audit_rules_path: Path | None = None,
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
        plan.source.removeprefix("ci/shared-skills/").removesuffix("/SKILL.md"): plan.old_managed_sha256
        for plan in plans if plan.source in SKILL_PATHS
    }
    bindings = store.list_runtime_skill_bindings(original.id)
    bound = {}
    for binding in bindings:
        skill = store.get_managed_skill(binding.skill_id)
        if skill is None or skill.name not in MANAGED_NAMES:
            continue
        revision = store.get_managed_skill_revision(binding.revision_id)
        if revision is None or revision.sha256 != old_by_name[skill.name]:
            raise ValueError(f"Consumer system configured Skill has a different revision: {skill.name}")
        bound[skill.name] = binding
    if set(bound) != set(MANAGED_NAMES):
        raise ValueError("Consumer system runtime configuration lacks a managed Skill")
    original_task_refs = {}
    original_task_versions = {}
    for task in store.list_scheduled_tasks():
        affected = [
            ref for ref in task.skill_refs
            if ref.skill_source == "managed" and ref.skill_name in MANAGED_NAMES
        ]
        if not affected:
            continue
        for ref in affected:
            revision = store.get_managed_skill_revision(ref.managed_revision_id)
            if revision is None or revision.sha256 != old_by_name[ref.skill_name]:
                raise ValueError(f"Consumer system scheduled Skill has a different revision: {ref.skill_name}")
        original_task_refs[task.id] = task.skill_refs
        original_task_versions[task.id] = task.version
    receipt_dir = Path(database_path).parent / "release-receipts" / operation_id
    return ConsumerSystemPublication(
        store=store, plans=plans, receipt_dir=receipt_dir,
        old_config_id=original.id, original_task_refs=original_task_refs,
        original_task_versions=original_task_versions, bindings=tuple(bindings),
    )
