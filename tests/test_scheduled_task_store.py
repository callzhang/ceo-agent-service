from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.store as store_module
from app.agent_cron.models import (
    ScheduledTaskSkillRef,
    ScheduledTaskSnapshot,
    ScheduledTaskVersionConflictError,
)
from app.store import AutoReplyStore


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 16, 0, tzinfo=UTC)


def _managed_ref(
    store: AutoReplyStore,
    *,
    skill_name: str = "ceo-test",
) -> ScheduledTaskSkillRef:
    skill = store.create_managed_skill(skill_name, "Test Skill")
    revision = store.create_managed_skill_revision(
        skill.id,
        f"---\nname: {skill_name}\ndescription: Test\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n# Test\n",
        source="settings",
    )
    return ScheduledTaskSkillRef(
        skill_source="managed",
        skill_name=skill.name,
        managed_skill_id=skill.id,
        managed_revision_id=revision.id,
        position=0,
    )


def _create_task(
    store: AutoReplyStore,
    *,
    migration_key: str | None = None,
    skill_refs: tuple[ScheduledTaskSkillRef, ...] | None = None,
):
    refs = skill_refs if skill_refs is not None else (_managed_ref(store),)
    return store.create_scheduled_task(
        migration_key=migration_key,
        name="Daily minutes",
        prompt="Sync the latest meeting minutes with $ceo-test.",
        cron_expression="0 0 20 * * *",
        timezone_name="Asia/Shanghai",
        runtime_id="codex_oauth",
        runtime_options={"model": "gpt-5.5", "reasoning_effort": "high"},
        working_directory="/tmp/ceo-agent-service",
        skill_refs=refs,
        enabled=True,
        now=NOW,
    )


def test_schema_manifest_creates_scheduled_task_tables_and_indexes(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")

    with sqlite3.connect(store.path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        indexes = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            )
        }

    assert {
        "scheduled_tasks",
        "scheduled_task_skill_refs",
        "scheduled_task_runs",
    } <= tables
    assert {
        "idx_scheduled_tasks_migration_key",
        "idx_scheduled_tasks_enabled",
        "idx_scheduled_task_skill_refs_position",
        "idx_scheduled_task_runs_scheduled_instant",
        "idx_scheduled_task_runs_dispatch",
    } <= indexes
    assert store._schema_is_current() is True


def test_create_and_read_task_preserves_structured_refs_and_utc_contract(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)

    assert task.id > 0
    assert task.version == 1
    assert task.created_at == NOW
    assert task.updated_at == NOW
    assert task.runtime_options == {
        "model": "gpt-5.5",
        "reasoning_effort": "high",
    }
    assert task.skill_refs[0].scheduled_task_id == task.id
    assert task.skill_refs[0].skill_source == "managed"
    assert store.get_scheduled_task(task.id) == task
    assert store.list_scheduled_tasks() == (task,)


def test_managed_skill_ref_requires_exact_revision_belonging_to_skill(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    first = _managed_ref(store, skill_name="ceo-first")
    second = _managed_ref(store, skill_name="ceo-second")

    wrong_revision = ScheduledTaskSkillRef(
        skill_source="managed",
        skill_name=first.skill_name,
        managed_skill_id=first.managed_skill_id,
        managed_revision_id=second.managed_revision_id,
        position=0,
    )
    with pytest.raises(ValueError, match="revision does not belong"):
        _create_task(store, skill_refs=(wrong_revision,))

    with pytest.raises(ValueError, match="exact revision"):
        ScheduledTaskSkillRef(
            skill_source="managed",
            skill_name=first.skill_name,
            managed_skill_id=first.managed_skill_id,
            managed_revision_id=None,
            position=0,
        )


def test_operation_skill_ref_rejects_managed_identifiers() -> None:
    with pytest.raises(ValueError, match="must not include managed"):
        ScheduledTaskSkillRef(
            skill_source="operation",
            skill_name="dingtalk-minutes",
            managed_skill_id=1,
            position=0,
        )


@pytest.mark.parametrize(
    ("values", "message"),
    (
        (
            {"skill_source": "unknown", "skill_name": "x", "position": 0},
            "source",
        ),
        (
            {"skill_source": "managed", "skill_name": "x", "position": 0},
            "exact revision",
        ),
        (
            {
                "skill_source": "managed",
                "skill_name": "x",
                "managed_skill_id": 0,
                "managed_revision_id": 1,
                "position": 0,
            },
            "positive",
        ),
        (
            {"skill_source": "operation", "skill_name": " ", "position": 0},
            "name",
        ),
        (
            {"skill_source": "operation", "skill_name": "x", "position": -1},
            "position",
        ),
    ),
)
def test_skill_ref_model_rejects_invalid_structure(
    values: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ScheduledTaskSkillRef(**values)  # type: ignore[arg-type]


def test_migration_key_is_idempotent(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    ref = _managed_ref(store)

    first = _create_task(
        store,
        migration_key="ceo-minutes-sync-daily-v1",
        skill_refs=(ref,),
    )
    repeated = _create_task(
        store,
        migration_key="ceo-minutes-sync-daily-v1",
        skill_refs=(ref,),
    )

    assert repeated == first
    assert store.list_scheduled_tasks() == (first,)


def test_update_is_atomic_and_rejects_stale_version(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)
    operation_ref = ScheduledTaskSkillRef(
        skill_source="operation",
        skill_name="dingtalk-minutes",
        position=0,
    )

    updated = store.update_scheduled_task(
        task.id,
        expected_version=task.version,
        name="Updated minutes",
        prompt="Updated prompt",
        cron_expression="0 30 20 * * *",
        timezone_name="America/Los_Angeles",
        runtime_id="claude_oauth",
        runtime_options={"model": "claude-opus"},
        working_directory="/tmp/updated",
        skill_refs=(operation_ref,),
        now=NOW + timedelta(minutes=1),
    )

    assert updated.version == 2
    assert updated.skill_refs == (
        ScheduledTaskSkillRef(
            scheduled_task_id=task.id,
            skill_source="operation",
            skill_name="dingtalk-minutes",
            position=0,
        ),
    )
    with pytest.raises(ScheduledTaskVersionConflictError):
        store.update_scheduled_task(
            task.id,
            expected_version=task.version,
            name="Stale update",
            now=NOW + timedelta(minutes=2),
        )

    assert store.get_scheduled_task(task.id) == updated


def test_enable_disable_and_soft_delete_use_version_checks(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)

    disabled = store.set_scheduled_task_enabled(
        task.id,
        enabled=False,
        expected_version=task.version,
        now=NOW + timedelta(minutes=1),
    )
    assert disabled.enabled is False
    assert disabled.version == 2

    deleted = store.delete_scheduled_task(
        task.id,
        expected_version=disabled.version,
        now=NOW + timedelta(minutes=2),
    )
    assert deleted.deleted_at == NOW + timedelta(minutes=2)
    assert deleted.version == 3
    assert store.get_scheduled_task(task.id) is None
    assert store.list_scheduled_tasks() == ()
    assert store.get_scheduled_task(task.id, include_deleted=True) == deleted
    assert store.list_scheduled_tasks(include_deleted=True) == (deleted,)


def test_scheduled_run_is_unique_per_planned_instant_and_snapshot_round_trips(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)
    scheduled_for = NOW + timedelta(hours=1)

    first = store.create_scheduled_task_run(
        task.id,
        trigger_kind="scheduled",
        scheduled_for=scheduled_for,
        now=NOW,
    )
    repeated = store.create_scheduled_task_run(
        task.id,
        trigger_kind="scheduled",
        scheduled_for=scheduled_for,
        now=NOW + timedelta(seconds=1),
    )

    assert repeated == first
    assert first.snapshot == ScheduledTaskSnapshot.from_json(first.snapshot.to_json())
    assert first.snapshot.task_version == task.version
    assert first.snapshot.skill_refs == task.skill_refs
    assert first.scheduled_for == scheduled_for


def test_manual_runs_at_same_instant_are_distinct_events(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)

    first = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    second = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )

    assert second.id != first.id
    assert second.event_id != first.event_id


def test_soft_delete_keeps_run_history(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )

    store.delete_scheduled_task(
        task.id,
        expected_version=task.version,
        now=NOW + timedelta(minutes=1),
    )

    assert store.list_scheduled_task_runs(task.id) == (run,)


def test_claim_link_and_finish_are_owner_guarded_atomic_transitions(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )

    claimed = store.claim_scheduled_task_run(
        run.id,
        owner="dispatcher-1",
        lease_seconds=60,
        now=NOW,
    )
    assert claimed is not None
    assert claimed.lease_owner == "dispatcher-1"
    assert store.claim_scheduled_task_run(
        run.id,
        owner="dispatcher-2",
        lease_seconds=60,
        now=NOW,
    ) is None

    with pytest.raises(ValueError, match="lease owner"):
        store.link_scheduled_task_run_execution(
            run.id,
            owner="dispatcher-2",
            execution_kind="scheduled_agent",
            execution_id="exec-1",
        )

    linked = store.link_scheduled_task_run_execution(
        run.id,
        owner="dispatcher-1",
        execution_kind="scheduled_agent",
        execution_id="exec-1",
    )
    assert linked.execution_kind == "scheduled_agent"
    assert linked.execution_id == "exec-1"

    finished = store.finish_scheduled_task_dispatch(
        run.id,
        owner="dispatcher-1",
        status="dispatched",
        now=NOW + timedelta(seconds=5),
    )
    assert finished.dispatch_status == "dispatched"
    assert finished.dispatched_at == NOW + timedelta(seconds=5)
    assert finished.lease_owner == ""
    assert store.claim_scheduled_task_run(
        run.id,
        owner="dispatcher-2",
        lease_seconds=60,
        now=NOW + timedelta(minutes=2),
    ) is None


def test_run_snapshot_rejects_corrupt_persisted_json(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update scheduled_task_runs set snapshot_json='{}' where id=?",
            (run.id,),
        )

    with pytest.raises(ValueError, match="snapshot"):
        store.list_scheduled_task_runs(task.id)


@pytest.mark.parametrize(
    "tampering",
    (
        "managed_missing_revision",
        "operation_with_managed_ids",
        "task_mismatch",
        "position_gap",
        "unknown_source",
    ),
)
def test_run_snapshot_rejects_semantically_tampered_skill_refs(
    tmp_path: Path,
    tampering: str,
) -> None:
    store = AutoReplyStore(tmp_path / f"cron-{tampering}.sqlite3")
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    payload = json.loads(run.snapshot.to_json())
    ref = payload["skill_refs"][0]
    if tampering == "managed_missing_revision":
        ref["managed_revision_id"] = None
    elif tampering == "operation_with_managed_ids":
        ref["skill_source"] = "operation"
    elif tampering == "task_mismatch":
        ref["scheduled_task_id"] = task.id + 1
    elif tampering == "position_gap":
        ref["position"] = 2
    else:
        ref["skill_source"] = "unknown"
    tampered_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update scheduled_task_runs set snapshot_json=? where id=?",
            (tampered_json, run.id),
        )

    with pytest.raises(ValueError, match="snapshot"):
        store.list_scheduled_task_runs(task.id)


def test_previous_schema_additively_creates_cron_tables_and_preserves_data(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "previous-schema.sqlite3"
    previous = AutoReplyStore(db_path)
    previous.set_service_state("cron-migration-marker", "keep-me")
    skill = previous.create_managed_skill("legacy-skill", "Legacy Skill")
    with previous._connect() as db:
        db.execute("drop table scheduled_task_runs")
        db.execute("drop table scheduled_task_skill_refs")
        db.execute("drop table scheduled_tasks")
        db.execute(
            "update service_state set value='2026-09-07.4' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = AutoReplyStore(db_path)

    assert migrated.get_service_state("cron-migration-marker") == "keep-me"
    assert migrated.get_managed_skill(skill.id) == skill
    with migrated._connect() as db:
        tables = {
            str(row["name"])
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        indexes = {
            str(row["name"])
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            )
        }
    assert {
        "scheduled_tasks",
        "scheduled_task_skill_refs",
        "scheduled_task_runs",
    } <= tables
    assert {
        "idx_scheduled_tasks_migration_key",
        "idx_scheduled_tasks_enabled",
        "idx_scheduled_task_skill_refs_position",
        "idx_scheduled_task_runs_scheduled_instant",
        "idx_scheduled_task_runs_dispatch",
    } <= indexes
    assert migrated._schema_is_current() is True
