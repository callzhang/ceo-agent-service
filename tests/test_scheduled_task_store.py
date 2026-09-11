from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.store as store_module
from app.agent_cron.models import (
    ScheduledTaskSkillRef,
    ScheduledTaskSnapshot,
    ScheduledTaskVersionConflictError,
)
from app.agent_runtime_contracts import (
    LOCAL_SERVICE_RUNTIME_CAPABILITIES,
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
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
    required_runtime_capabilities: tuple[str, ...] = (),
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
        required_runtime_capabilities=required_runtime_capabilities,
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
        "idx_scheduled_task_runs_claim",
        "idx_scheduled_task_runs_latest",
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


def test_task_and_run_snapshot_preserve_required_runtime_capabilities(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "cron-capabilities.sqlite3")
    task = _create_task(
        store,
        required_runtime_capabilities=tuple(
            sorted(LOCAL_SERVICE_RUNTIME_CAPABILITIES)
        ),
    )
    run = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )

    assert task.required_runtime_capabilities == tuple(
        sorted(LOCAL_SERVICE_RUNTIME_CAPABILITIES)
    )
    assert run.snapshot.required_runtime_capabilities == (
        task.required_runtime_capabilities
    )


def test_capability_backfill_is_atomic_across_concurrent_store_instances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent-capability-backfill.sqlite3"
    first_store = AutoReplyStore(path)
    task = _create_task(
        first_store,
        migration_key="legacy-local-producer-v1",
    )
    second_store = AutoReplyStore(path)
    barrier = threading.Barrier(2)
    results = []
    errors: list[BaseException] = []

    def migrate(store: AutoReplyStore) -> None:
        try:
            barrier.wait()
            results.append(
                store.backfill_scheduled_task_runtime_capabilities(
                    migration_key="legacy-local-producer-v1",
                    required_capabilities=LOCAL_SERVICE_RUNTIME_CAPABILITIES,
                    eligible_runtime_ids=frozenset({"codex_oauth"}),
                    now=NOW + timedelta(minutes=1),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = (
        threading.Thread(target=migrate, args=(first_store,)),
        threading.Thread(target=migrate, args=(second_store,)),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    updated = first_store.get_scheduled_task(task.id)
    assert updated is not None
    assert updated.version == task.version + 1
    assert all(result == updated for result in results)
    assert frozenset(updated.required_runtime_capabilities) == (
        LOCAL_SERVICE_RUNTIME_CAPABILITIES
    )


def test_capability_backfill_reads_latest_user_fields_inside_write_transaction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "competing-capability-backfill.sqlite3"
    user_store = AutoReplyStore(path)
    task = _create_task(
        user_store,
        migration_key="edited-local-producer-v1",
    )
    latest_ref = _managed_ref(user_store, skill_name="ceo-latest-user-skill")
    migration_store = AutoReplyStore(path)
    started = threading.Event()
    results = []

    def migrate() -> None:
        started.set()
        results.append(
            migration_store.backfill_scheduled_task_runtime_capabilities(
                migration_key="edited-local-producer-v1",
                required_capabilities=LOCAL_SERVICE_RUNTIME_CAPABILITIES,
                eligible_runtime_ids=frozenset({"latest-user-runtime"}),
                now=NOW + timedelta(minutes=2),
            )
        )

    with user_store._immediate_write_transaction() as db:
        db.execute(
            """
            update scheduled_tasks
               set name=?, prompt=?, cron_expression=?, runtime_id=?,
                   runtime_options_json=?, working_directory=?,
                   version=version + 1, updated_at=?
             where id=?
            """,
            (
                "最新用户名称",
                "最新用户 Prompt",
                "0 15 * * * *",
                "latest-user-runtime",
                '{"model":"latest-user-model"}',
                "/latest/user/workspace",
                (NOW + timedelta(minutes=1)).isoformat(),
                task.id,
            ),
        )
        db.execute(
            "delete from scheduled_task_skill_refs where scheduled_task_id=?",
            (task.id,),
        )
        db.execute(
            """
            insert into scheduled_task_skill_refs (
                scheduled_task_id, skill_source, skill_name,
                managed_skill_id, managed_revision_id, position
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                latest_ref.skill_source,
                latest_ref.skill_name,
                latest_ref.managed_skill_id,
                latest_ref.managed_revision_id,
                latest_ref.position,
            ),
        )
        thread = threading.Thread(target=migrate)
        thread.start()
        assert started.wait(timeout=1)
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(results) == 1
    updated = results[0]
    assert updated is not None
    assert updated.version == task.version + 2
    assert updated.name == "最新用户名称"
    assert updated.prompt == "最新用户 Prompt"
    assert updated.cron_expression == "0 15 * * * *"
    assert updated.runtime_id == "latest-user-runtime"
    assert updated.runtime_options == {"model": "latest-user-model"}
    assert updated.working_directory == "/latest/user/workspace"
    assert tuple(ref.skill_name for ref in updated.skill_refs) == (
        "ceo-latest-user-skill",
    )
    assert frozenset(updated.required_runtime_capabilities) == (
        LOCAL_SERVICE_RUNTIME_CAPABILITIES
    )


def test_previous_scheduled_tasks_gain_empty_runtime_requirements(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "previous-capabilities.sqlite3"
    previous = AutoReplyStore(db_path)
    task = _create_task(previous)
    run = previous.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    with previous._connect() as db:
        snapshot = json.loads(run.snapshot.to_json())
        snapshot.pop("required_runtime_capabilities")
        db.execute(
            "update scheduled_task_runs set snapshot_json=? where id=?",
            (json.dumps(snapshot), run.id),
        )
        db.execute(
            "alter table scheduled_tasks drop column required_runtime_capabilities_json"
        )
        db.execute(
            "update service_state set value='2026-09-08.5' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = AutoReplyStore(db_path)

    assert migrated.get_scheduled_task(task.id).required_runtime_capabilities == ()
    assert migrated.get_scheduled_task_run(run.id).snapshot.required_runtime_capabilities == ()


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


@pytest.mark.parametrize("invalid_version", (True, False, 1.5, 0, -1))
@pytest.mark.parametrize("operation", ("update", "set_enabled", "delete"))
def test_task_mutations_reject_non_positive_exact_integer_versions(
    tmp_path: Path,
    operation: str,
    invalid_version: object,
) -> None:
    store = AutoReplyStore(tmp_path / f"version-{operation}-{invalid_version}.sqlite3")
    task = _create_task(store)

    with pytest.raises(ValueError, match="version must be a positive integer"):
        if operation == "update":
            store.update_scheduled_task(
                task.id,
                expected_version=invalid_version,  # type: ignore[arg-type]
                name="invalid",
            )
        elif operation == "set_enabled":
            store.set_scheduled_task_enabled(
                task.id,
                enabled=False,
                expected_version=invalid_version,  # type: ignore[arg-type]
            )
        else:
            store.delete_scheduled_task(
                task.id,
                expected_version=invalid_version,  # type: ignore[arg-type]
            )

    assert store.get_scheduled_task(task.id) == task


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
            now=NOW + timedelta(seconds=1),
        )

    linked = store.link_scheduled_task_run_execution(
        run.id,
        owner="dispatcher-1",
        execution_kind="scheduled_agent",
        execution_id="exec-1",
        now=NOW + timedelta(seconds=1),
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


def test_expired_owner_cannot_link_or_finish_and_reclaimer_owns_writes(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "expired-lease.sqlite3")
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    assert store.claim_scheduled_task_run(
        run.id,
        owner="old-owner",
        lease_seconds=30,
        now=NOW,
    ) is not None

    with pytest.raises(ValueError, match="lease"):
        store.link_scheduled_task_run_execution(
            run.id,
            owner="old-owner",
            execution_kind="scheduled_agent",
            execution_id="exec-1",
            now=NOW + timedelta(seconds=31),
        )
    reclaimed = store.claim_scheduled_task_run(
        run.id,
        owner="new-owner",
        lease_seconds=30,
        now=NOW + timedelta(seconds=31),
    )
    assert reclaimed is not None
    assert reclaimed.lease_owner == "new-owner"
    with pytest.raises(ValueError, match="lease"):
        store.link_scheduled_task_run_execution(
            run.id,
            owner="old-owner",
            execution_kind="scheduled_agent",
            execution_id="exec-old",
            now=NOW + timedelta(seconds=32),
        )
    store.link_scheduled_task_run_execution(
        run.id,
        owner="new-owner",
        execution_kind="scheduled_agent",
        execution_id="exec-new",
        now=NOW + timedelta(seconds=32),
    )
    with pytest.raises(ValueError, match="lease"):
        store.finish_scheduled_task_dispatch(
            run.id,
            owner="old-owner",
            status="dispatched",
            now=NOW + timedelta(seconds=33),
        )
    finished = store.finish_scheduled_task_dispatch(
        run.id,
        owner="new-owner",
        status="dispatched",
        now=NOW + timedelta(seconds=33),
    )
    assert finished.execution_id == "exec-new"


def test_expired_owner_cannot_finish_even_before_another_owner_reclaims(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "expired-finish.sqlite3")
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    store.claim_scheduled_task_run(
        run.id,
        owner="old-owner",
        lease_seconds=30,
        now=NOW,
    )
    store.link_scheduled_task_run_execution(
        run.id,
        owner="old-owner",
        execution_kind="scheduled_agent",
        execution_id="exec-1",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="lease"):
        store.finish_scheduled_task_dispatch(
            run.id,
            owner="old-owner",
            status="dispatched",
            now=NOW + timedelta(seconds=31),
        )


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


def test_run_reader_rejects_snapshot_bound_to_another_persisted_task(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "cross-task-snapshot.sqlite3")
    first = _create_task(store)
    second = _create_task(
        store,
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="operation",
                skill_name="dingtalk-minutes",
                position=0,
            ),
        ),
    )
    run = store.create_scheduled_task_run(
        first.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update scheduled_task_runs set scheduled_task_id=? where id=?",
            (second.id, run.id),
        )

    with pytest.raises(ValueError, match="snapshot"):
        store.list_scheduled_task_runs(second.id)


@pytest.mark.parametrize(
    "tampering",
    (
        "managed_missing_revision",
        "operation_with_managed_ids",
        "task_mismatch",
        "position_gap",
        "unknown_source",
        "task_id_bool",
        "task_version_bool",
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
    elif tampering == "unknown_source":
        ref["skill_source"] = "unknown"
    elif tampering == "task_id_bool":
        payload["task_id"] = True
    else:
        payload["task_version"] = True
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
        "idx_scheduled_task_runs_claim",
        "idx_scheduled_task_runs_latest",
    } <= indexes
    assert migrated._schema_is_current() is True


def test_claim_query_index_starts_with_status_schedule_and_id(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "claim-index.sqlite3")

    with store._connect() as db:
        columns = tuple(
            str(row["name"])
            for row in db.execute("pragma index_info(idx_scheduled_task_runs_claim)")
        )
        plan = tuple(
            str(row["detail"])
            for row in db.execute(
                """
                explain query plan
                select id from scheduled_task_runs
                 where dispatch_status='pending'
                   and scheduled_for <= ?
                   and (lease_owner='' or lease_expires_at <= ?)
                 order by scheduled_for, id
                 limit 1
                """,
                (NOW.isoformat(), NOW.isoformat()),
            )
        )

    assert columns == ("dispatch_status", "scheduled_for", "id")
    assert any("idx_scheduled_task_runs_claim" in detail for detail in plan)


def test_latest_runs_are_batched_and_page_query_uses_latest_index(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "latest-index.sqlite3")
    first = _create_task(store)
    existing_ref = first.skill_refs[0]
    second = _create_task(
        store,
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source=existing_ref.skill_source,
                skill_name=existing_ref.skill_name,
                managed_skill_id=existing_ref.managed_skill_id,
                managed_revision_id=existing_ref.managed_revision_id,
                position=0,
            ),
        ),
    )
    for task in (first, second):
        for offset in range(3):
            store.create_scheduled_task_run(
                task.id,
                trigger_kind="manual",
                scheduled_for=NOW + timedelta(seconds=offset),
                now=NOW + timedelta(seconds=offset),
            )

    latest = store.latest_scheduled_task_runs((first.id, second.id))
    page = store.list_scheduled_task_runs_page(first.id, before_id=None, limit=2)
    next_page = store.list_scheduled_task_runs_page(
        first.id,
        before_id=page[-1].id,
        limit=2,
    )

    assert {task_id: run.id for task_id, run in latest.items()} == {
        first.id: 3,
        second.id: 6,
    }
    assert [run.id for run in page] == [3, 2]
    assert [run.id for run in next_page] == [1]
    with store._connect() as db:
        columns = tuple(
            str(row["name"])
            for row in db.execute(
                "pragma index_info(idx_scheduled_task_runs_latest)"
            )
        )
        plan = tuple(
            str(row["detail"])
            for row in db.execute(
                """
                explain query plan
                select id from scheduled_task_runs
                 where scheduled_task_id=? and id < ?
                 order by id desc limit ?
                """,
                (first.id, 999, 20),
            )
        )
    assert columns == ("scheduled_task_id", "id")
    assert any("idx_scheduled_task_runs_latest" in detail for detail in plan)


def test_runtime_capability_bridge_is_exactly_scoped_to_process_pid(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "runtime-capability.sqlite3")
    snapshot = RuntimeCapabilitySnapshot(
        route_name="codex_oauth",
        capabilities=PROBE_VERIFIED_RUNTIME_CAPABILITIES,
        healthy=True,
        checked_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(minutes=5)).isoformat(),
    )

    store.record_runtime_capability_snapshot(snapshot, pid=701)

    assert store.runtime_capability_snapshots_for_pid(
        ("codex_oauth",), pid=701
    ) == {"codex_oauth": snapshot}
    assert store.runtime_capability_snapshots_for_pid(
        ("codex_oauth",), pid=702
    ) == {}


def test_command_task_round_trips_and_rejects_mixed_execution_forms(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "command-task.sqlite3")

    task = store.create_scheduled_task(
        name="Producer",
        command=" produce-once ",
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=NOW,
    )

    assert task.command == "produce-once"
    assert task.prompt == "" and task.runtime_id == ""
    assert task.runtime_options == {} and task.required_runtime_capabilities == ()
    assert task.working_directory == "" and task.skill_refs == ()
    run = store.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW, now=NOW
    )
    assert run.snapshot.command == "produce-once"
    assert json.loads(run.snapshot.to_json())["command"] == "produce-once"
    assert ScheduledTaskSnapshot.from_json(run.snapshot.to_json()) == run.snapshot

    with pytest.raises(ValueError, match="must not carry Agent"):
        store.create_scheduled_task(
            name="Mixed", command="produce-once", prompt="also an Agent prompt",
            cron_expression="0 * * * * *", timezone_name="UTC", now=NOW,
        )
    with pytest.raises(ValueError, match="must not carry Agent"):
        store.create_scheduled_task(
            name="Mixed", command="produce-once", runtime_id="codex_oauth",
            cron_expression="0 * * * * *", timezone_name="UTC", now=NOW,
        )
    with pytest.raises(ValueError, match="prompt must be nonempty"):
        store.create_scheduled_task(
            name="Agent", runtime_id="codex_oauth",
            cron_expression="0 * * * * *", timezone_name="UTC", now=NOW,
        )
    with pytest.raises(ValueError, match="runtime must be nonempty"):
        store.create_scheduled_task(
            name="Agent", prompt="Do the thing",
            cron_expression="0 * * * * *", timezone_name="UTC", now=NOW,
        )
    with pytest.raises(ValueError, match="must not carry Agent"):
        store.update_scheduled_task(
            task.id, expected_version=task.version, prompt="now an Agent", now=NOW
        )
    assert store.get_scheduled_task(task.id).version == task.version


def test_previous_scheduled_tasks_gain_empty_command(tmp_path: Path) -> None:
    db_path = tmp_path / "previous-command.sqlite3"
    previous = AutoReplyStore(db_path)
    task = _create_task(previous)
    run = previous.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW, now=NOW
    )
    with previous._connect() as db:
        snapshot = json.loads(run.snapshot.to_json())
        snapshot.pop("command")
        db.execute(
            "update scheduled_task_runs set snapshot_json=? where id=?",
            (json.dumps(snapshot), run.id),
        )
        db.execute("alter table scheduled_tasks drop column command")
        # The version a database carried before the command column shipped:
        # the schema gate must treat it as stale, or the column is never added.
        db.execute(
            "update service_state set value='2026-09-08.6' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    assert store_module.STORE_SCHEMA_VERSION > "2026-09-08.6"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = AutoReplyStore(db_path)

    assert migrated.get_scheduled_task(task.id).command == ""
    assert migrated.get_scheduled_task_run(run.id).snapshot.command == ""
    assert migrated.get_scheduled_task_run(run.id).snapshot.prompt == task.prompt


def test_current_schema_version_still_migrates_missing_command_column(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "current-version-missing-command.sqlite3"
    previous = AutoReplyStore(db_path)
    task = _create_task(previous)
    with previous._connect() as db:
        db.execute("alter table scheduled_tasks drop column command")
        db.execute(
            "update service_state set value=? where key=?",
            (store_module.STORE_SCHEMA_VERSION, store_module.STORE_SCHEMA_VERSION_KEY),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = AutoReplyStore(db_path)

    assert migrated.get_scheduled_task(task.id).command == ""


def test_pre_bump_schema_version_backfills_legacy_run_snapshot_fields(
    tmp_path: Path,
) -> None:
    """A database written before the snapshot fields carries an older version.

    The row check no longer runs on every store construction, so the version is
    what drives the repair: any build that changes the persisted snapshot shape
    must bump STORE_SCHEMA_VERSION, which is what makes this database migrate.
    """
    db_path = tmp_path / "pre-bump-legacy-snapshot.sqlite3"
    previous = AutoReplyStore(db_path)
    task = _create_task(previous)
    run = previous.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW, now=NOW
    )
    with previous._connect() as db:
        snapshot = json.loads(run.snapshot.to_json())
        snapshot.pop("command")
        db.execute(
            "update scheduled_task_runs set snapshot_json=? where id=?",
            (json.dumps(snapshot), run.id),
        )
        db.execute(
            "update service_state set value=? where key=?",
            ("2026-09-09.1", store_module.STORE_SCHEMA_VERSION_KEY),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = AutoReplyStore(db_path)

    assert migrated.get_scheduled_task_run(run.id).snapshot.command == ""


def test_adopting_a_service_command_moves_the_seed_in_place_once(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "adopt.sqlite3")
    legacy = _create_task(store, migration_key="producer-v1")
    disabled = store.set_scheduled_task_enabled(
        legacy.id, enabled=False, expected_version=legacy.version, now=NOW
    )

    adopted = store.adopt_scheduled_task_service_command(
        migration_key="producer-v1", command="produce-once", seed_enabled=True,
        now=NOW + timedelta(minutes=1),
    )

    assert adopted is not None and adopted.id == legacy.id
    assert adopted.version == disabled.version + 1
    assert adopted.command == "produce-once"
    assert adopted.prompt == "" and adopted.runtime_id == ""
    assert adopted.runtime_options == {} and adopted.required_runtime_capabilities == ()
    assert adopted.working_directory == "" and adopted.skill_refs == ()
    assert adopted.name == legacy.name
    assert adopted.cron_expression == legacy.cron_expression
    assert adopted.timezone_name == legacy.timezone_name
    assert adopted.enabled is False
    assert adopted.updated_at == NOW + timedelta(minutes=1)
    with store._connect() as db:
        refs = db.execute(
            "select count(*) from scheduled_task_skill_refs where scheduled_task_id=?",
            (legacy.id,),
        ).fetchone()[0]
    assert refs == 0

    again = store.adopt_scheduled_task_service_command(
        migration_key="producer-v1", command="produce-once", seed_enabled=True,
        now=NOW + timedelta(minutes=2),
    )
    assert again == adopted
    assert store.adopt_scheduled_task_service_command(
        migration_key="unknown-v1", command="produce-once", seed_enabled=True, now=NOW
    ) is None

    untouched = store.create_scheduled_task(
        migration_key="untouched-v1", name="Untouched", prompt="Agent form",
        cron_expression="0 * * * * *", timezone_name="UTC", runtime_id="codex_oauth",
        skill_refs=(_managed_ref(store, skill_name="ceo-untouched"),),
        enabled=False, now=NOW,
    )
    converted = store.adopt_scheduled_task_service_command(
        migration_key="untouched-v1", command="produce-once", seed_enabled=True, now=NOW
    )
    assert untouched.version == 1 and converted.enabled is True
    assert converted.version == 2 and converted.command == "produce-once"

    deleted_legacy = _create_task(
        store,
        migration_key="deleted-v1",
        skill_refs=(_managed_ref(store, skill_name="ceo-deleted"),),
    )
    deleted = store.delete_scheduled_task(
        deleted_legacy.id, expected_version=deleted_legacy.version, now=NOW
    )
    assert store.adopt_scheduled_task_service_command(
        migration_key="deleted-v1", command="produce-once", seed_enabled=True, now=NOW
    ) == deleted


def test_corrupt_run_snapshot_neither_crashes_startup_nor_loops_migration(
    tmp_path: Path,
) -> None:
    """A snapshot migration cannot repair must not be reported as stale.

    The currency check once treated an unparseable snapshot_json as an
    out-of-date schema while the migration parsed it without guarding, so a
    single corrupt row asked for a migration that then raised on it and the
    supervisor restarted the worker in a loop.
    """
    db_path = tmp_path / "corrupt-snapshot.sqlite3"
    store = AutoReplyStore(db_path)
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW, now=NOW
    )
    with store._connect() as db:
        db.execute(
            "update scheduled_task_runs set snapshot_json=? where id=?",
            ("{not valid json", run.id),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    # Reopening must not raise, and the corrupt row must be left exactly as it
    # is rather than backfilled with invented defaults.
    reopened = AutoReplyStore(db_path)

    with reopened._connect() as db:
        stored = db.execute(
            "select snapshot_json from scheduled_task_runs where id=?", (run.id,)
        ).fetchone()
    assert stored["snapshot_json"] == "{not valid json"
    # Healthy rows stay readable alongside the corrupt one.
    healthy = reopened.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW + timedelta(minutes=1), now=NOW
    )
    assert reopened.get_scheduled_task_run(healthy.id).snapshot.command == ""


def _traced_construction(tmp_path, monkeypatch, path):
    """Construct a store again, returning every SQL statement it issued."""
    statements: list[str] = []
    original_open = store_module.AutoReplyStore._open_connection

    def traced(self):
        connection = original_open(self)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store_module.AutoReplyStore, "_open_connection", traced)
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    AutoReplyStore(path)
    return statements


def test_store_construction_does_not_read_run_snapshots_on_the_fast_path(
    tmp_path, monkeypatch
):
    """A damaged scheduled_task_runs page must not kill every store construction.

    The row check reads a table that grows with every scheduled run. Running it
    on every construction made one damaged page fatal at startup for every CLI
    subprocess, and `database disk image is malformed` arrives as
    sqlite3.DatabaseError, which the callers' `except sqlite3.OperationalError`
    does not catch.
    """
    path = tmp_path / "fast-path.sqlite3"
    AutoReplyStore(path)  # first construction migrates and writes the version

    statements = _traced_construction(tmp_path, monkeypatch, path)

    # `pragma table_info` only reads the schema, which a damaged data page
    # cannot affect; what must not happen is a row read.
    offending = [
        text for text in statements if "from scheduled_task_runs" in text.lower()
    ]
    assert not offending, f"fast path still reads rows: {offending[:2]}"


def test_migration_still_verifies_the_run_snapshots_it_wrote(tmp_path, monkeypatch):
    """The row check stays where a migration claims to have written the fields."""
    path = tmp_path / "verified.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "update service_state set value='stale' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )

    statements = _traced_construction(tmp_path, monkeypatch, path)

    assert any(
        "scheduled_task_runs" in text and "json_type" in text for text in statements
    ), "post-migration verification must still read the snapshots back"
