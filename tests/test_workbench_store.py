import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import app.store as store_module
import app.workbench.store as workbench_store_module
from app.workbench.models import ConfirmationStatus, TurnStatus
from app.workbench.store import WorkbenchConflictError, WorkbenchStore


def _store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / "workbench.sqlite3")


def _running_turn(tmp_path: Path) -> tuple[WorkbenchStore, str, str]:
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    claimed = store.claim_next_turn(
        owner="worker-1", lease_seconds=10, now="2026-08-13T00:00:00Z"
    )
    assert claimed is not None
    return store, task.id, turn.id


def _waiting_confirmation(store: WorkbenchStore):
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id=f"request-{task.id}",
    )
    assert store.claim_next_turn(owner="seed") is not None
    confirmation = store.create_confirmation(
        turn.id,
        action_kind="reviewed_cli",
        target="Executive group",
        summary="Send update",
        risk="External message",
        arguments_json={"argv": ["dws", "chat", "message", "send", "--yes"]},
        owner="seed",
    )
    store.mark_confirmation_proposer_quiesced(
        turn.id,
        owner="seed",
        proposer_run_id=store.execution_run_id_for_executor(turn.id, owner="seed"),
    )
    return task, turn.id, confirmation


def test_store_migrates_resume_context_without_losing_existing_turns(tmp_path: Path):
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Compare regions", client_request_id="request-1"
    )
    with store._connect() as db:
        db.execute("alter table workbench_turns drop column resume_context")
        db.execute(
            "update service_state set value='2026-08-13.1' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = WorkbenchStore(db_path)

    assert migrated.get_turn(turn.id).user_text == "Compare regions"
    assert "resume_context" not in migrated.get_turn(turn.id).model_dump()
    with migrated._connect() as db:
        columns = {
            row["name"]
            for row in db.execute("pragma table_info(workbench_turns)").fetchall()
        }
    assert "resume_context" in columns


def test_store_schema_check_rejects_missing_required_reply_attempt_column(
    tmp_path: Path,
):
    store = store_module.AutoReplyStore(tmp_path / "worker.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "alter table reply_attempts drop column human_decision_options_json"
        )

    assert store._schema_is_current() is False


def test_store_upgrades_point_seven_schema_with_current_indexes(tmp_path: Path):
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    task = store.create_task(title="Upgrade", runtime_kind="codex")
    with store._connect() as db:
        for index in (
            "idx_workbench_events_turn_id_id",
            "idx_workbench_artifacts_turn_created_id",
            "idx_workbench_turns_task_created_id",
            "idx_workbench_tasks_updated_id",
            "idx_workbench_events_id_turn_id",
            "idx_workbench_artifacts_created_id_turn",
            "idx_workbench_confirmations_created_id_turn",
            "idx_workbench_confirmations_turn_created_id",
            "idx_workbench_attachments_task_created_id",
            "idx_workbench_events_event_type",
        ):
            db.execute(f"drop index if exists {index}")
        db.execute(
            "update service_state set value='2026-08-13.7' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = WorkbenchStore(db_path)
    with upgraded._connect() as db:
        indexes = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            )
        }

    assert store_module.STORE_SCHEMA_VERSION == "2026-08-18.1"
    assert "idx_workbench_events_event_type" in indexes
    assert "idx_workbench_events_turn_id_id" in indexes
    assert "idx_workbench_turns_task_sequence" in indexes
    assert "idx_workbench_confirmations_turn_created_id" in indexes
    snapshot = upgraded.timeline_snapshot(task.id)
    assert snapshot[0].id == task.id


def test_store_repairs_missing_required_point_nine_index(tmp_path: Path):
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    with store._connect() as db:
        db.execute("drop index idx_workbench_events_event_type")
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    repaired = WorkbenchStore(db_path)

    with repaired._connect() as db:
        index = db.execute(
            """select name from sqlite_master
               where type='index' and name='idx_workbench_events_event_type'"""
        ).fetchone()
    assert index is not None


def test_store_upgrades_point_nine_attachments_with_idempotency_columns(
    tmp_path: Path,
):
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    task = store.create_task(title="Attachment upgrade", runtime_kind="codex")
    attachment = store.save_attachment(
        task.id,
        filename="legacy.png",
        media_type="image/png",
        content=b"legacy",
    )
    with store._connect() as db:
        db.execute("drop index idx_workbench_attachments_task_request")
        db.execute("alter table workbench_attachments drop column content_sha256")
        db.execute("alter table workbench_attachments drop column client_request_id")
        db.execute(
            "update service_state set value='2026-08-13.9' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = WorkbenchStore(db_path)
    with upgraded._connect() as db:
        row = db.execute(
            "select client_request_id, content_sha256 from workbench_attachments where id=?",
            (attachment.id,),
        ).fetchone()
        index = db.execute(
            """select name from sqlite_master
               where type='index' and name='idx_workbench_attachments_task_request'"""
        ).fetchone()

    assert row["client_request_id"] == attachment.id
    assert row["content_sha256"] == ""
    assert index is not None


def test_store_upgrades_point_eight_turns_with_stable_per_task_sequence(
    tmp_path: Path,
):
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    task = store.create_task(title="Legacy order", runtime_kind="codex")
    first_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    second_id = "00000000-0000-4000-8000-000000000001"
    with store._connect() as db:
        db.execute(
            """insert into workbench_turns
               (id,task_id,client_request_id,user_text,status,task_sequence,created_at)
               values(?,?,?,?,?,?,?)""",
            (first_id, task.id, "legacy-first", "first", "completed", 1, "2026-08-13 00:00:00"),
        )
        db.execute(
            """insert into workbench_turns
               (id,task_id,client_request_id,user_text,status,task_sequence,created_at)
               values(?,?,?,?,?,?,?)""",
            (second_id, task.id, "legacy-second", "second", "failed", 2, "2026-08-13 00:00:00"),
        )
        db.execute("drop index idx_workbench_turns_task_sequence")
        db.execute("alter table workbench_turns drop column task_sequence")
        db.execute(
            "update service_state set value='2026-08-13.8' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = WorkbenchStore(db_path)
    turns = upgraded.list_turns(task.id)

    assert store_module.STORE_SCHEMA_VERSION == "2026-08-18.1"
    assert [(turn.id, turn.task_sequence) for turn in turns] == [
        (second_id, 2),
        (first_id, 1),
    ]
    assert upgraded.get_task_summary(task.id)[1] == "failed"


def test_concurrent_turn_creation_allocates_unique_monotonic_task_sequences(
    tmp_path: Path,
):
    first = _store(tmp_path)
    second = WorkbenchStore(first.path)
    task = first.create_task(title="Concurrent", runtime_kind="codex")

    def create_and_stop(index: int):
        selected = first if index % 2 else second
        while True:
            try:
                turn = selected.create_turn(
                    task.id,
                    user_text=f"turn {index}",
                    client_request_id=f"concurrent-{index}",
                )
            except ValueError as exc:
                assert str(exc) == "task already has an active turn"
                continue
            selected.request_stop(turn.id)
            return turn.id

    with ThreadPoolExecutor(max_workers=4) as executor:
        created_ids = set(executor.map(create_and_stop, range(12)))

    turns = first.list_turns(task.id)
    assert {turn.id for turn in turns} == created_ids
    assert [turn.task_sequence for turn in turns] == list(range(12, 0, -1))


def test_timeline_snapshot_does_not_mix_a_concurrent_terminal_transition(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    writer = WorkbenchStore(store.path)
    with store._connect() as db:
        db.execute("pragma journal_mode=wal")
    task = store.create_task(title="Snapshot", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="snapshot", client_request_id="snapshot-race"
    )
    snapshot_started = threading.Event()
    allow_snapshot = threading.Event()
    original_require_task = store._require_task

    def pause_after_first_read(db, task_id):
        row = original_require_task(db, task_id)
        snapshot_started.set()
        assert allow_snapshot.wait(2)
        return row

    monkeypatch.setattr(store, "_require_task", pause_after_first_read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        snapshot_future = executor.submit(store.timeline_snapshot, task.id)
        assert snapshot_started.wait(2)
        stopped = writer.request_stop(turn.id)
        allow_snapshot.set()
        snapshot = snapshot_future.result(timeout=2)

    _, turns, events, _, _, _, page = snapshot
    assert stopped.status is TurnStatus.STOPPED
    assert [(item.status, item.task_sequence) for item in turns] == [
        (TurnStatus.QUEUED, 1)
    ]
    assert [event.event_type for event in events] == ["status_changed"]
    assert page["task_state"] == TurnStatus.QUEUED.value


def test_store_migrates_confirmation_execution_claim_without_losing_data(
    tmp_path: Path,
):
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    _, _, confirmation = _waiting_confirmation(store)
    with store._connect() as db:
        db.execute("drop index idx_workbench_confirmations_recovery")
        db.execute("drop index idx_workbench_confirmations_ready_intents")
        db.execute("drop index idx_workbench_confirmations_proposer_recovery")
        db.execute("drop index idx_workbench_confirmations_legacy_proposer_recovery")
        db.execute(
            "drop index idx_workbench_confirmations_legacy_execution_owner_recovery"
        )
        db.execute(
            "drop index idx_workbench_confirmations_legacy_execution_lease_recovery"
        )
        for column in (
            "execution_owner",
            "execution_lease_expires_at",
            "execution_started_at",
            "canonical_capability",
            "canonical_operation",
            "canonical_targets_json",
            "canonical_operation_digest",
            "canonical_arguments_digest",
            "authorization_consumed_at",
            "proposer_run_id",
            "proposer_owner",
            "proposer_lease_expires_at",
            "proposer_quiesced_at",
            "decision_requested",
            "decision_requested_at",
        ):
            db.execute(f"alter table workbench_confirmations drop column {column}")
        for column in ("execution_run_id", "runtime_quiesced_run_id"):
            db.execute(f"alter table workbench_turns drop column {column}")
        db.execute(
            "update service_state set value='2026-08-13.2' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = WorkbenchStore(db_path)

    assert migrated.get_confirmation(confirmation.id).status is ConfirmationStatus.FAILED
    with migrated._connect() as db:
        row = db.execute(
            "select * from workbench_confirmations where id=?", (confirmation.id,)
        ).fetchone()
    assert row["execution_owner"] == ""
    assert row["execution_lease_expires_at"] == ""
    assert row["execution_started_at"] == ""
    assert row["canonical_targets_json"] == "[]"
    assert row["authorization_consumed_at"] == ""
    assert row["proposer_run_id"] == ""
    assert row["decision_requested"] == ""
    with migrated._connect() as db:
        turn_columns = {
            item["name"]
            for item in db.execute("pragma table_info(workbench_turns)").fetchall()
        }
    assert {"execution_run_id", "runtime_quiesced_run_id"} <= turn_columns


def test_migration_fails_legacy_pending_confirmations_once_across_turn_states(
    tmp_path: Path,
):
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    turns = {}
    confirmations = []
    for label in ("waiting", "queued", "running", "completed"):
        task = store.create_task(title=label, runtime_kind="codex")
        turn = store.create_turn(
            task.id,
            user_text=label,
            client_request_id=f"request-{label}",
        )
        store.claim_next_turn(owner=f"owner-{label}")
        confirmation = store.create_confirmation(
            turn.id,
            action_kind="reviewed_cli",
            target="group=executive",
            summary="[Untrusted agent description] send",
            risk="[Untrusted agent risk] external",
            arguments_json={"argv": ["dws", "chat", "message", "send", "--yes"]},
            owner=f"owner-{label}",
        )
        turns[label] = turn.id
        confirmations.append(confirmation.id)
    with store._connect() as db:
        source = db.execute(
            "select * from workbench_confirmations where id=?",
            (confirmations[0],),
        ).fetchone()
        duplicate_id = "00000000-0000-4000-8000-000000000099"
        db.execute(
            """
            insert into workbench_confirmations (
                id, turn_id, action_kind, target, summary, risk, arguments_json,
                canonical_capability, canonical_operation, canonical_targets_json,
                canonical_operation_digest, canonical_arguments_digest, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                duplicate_id,
                source["turn_id"],
                source["action_kind"],
                source["target"],
                source["summary"],
                source["risk"],
                source["arguments_json"],
                source["canonical_capability"],
                source["canonical_operation"],
                source["canonical_targets_json"],
                source["canonical_operation_digest"],
                source["canonical_arguments_digest"],
            ),
        )
        confirmations.append(duplicate_id)
        db.execute(
            "update workbench_turns set status='queued' where id=?",
            (turns["queued"],),
        )
        db.execute(
            "update workbench_turns set status='running' where id=?",
            (turns["running"],),
        )
        db.execute(
            """
            update workbench_turns set status='completed', completed_at=current_timestamp
            where id=?
            """,
            (turns["completed"],),
        )
        db.execute("drop index idx_workbench_confirmations_ready_intents")
        db.execute("drop index idx_workbench_confirmations_proposer_recovery")
        db.execute("drop index idx_workbench_confirmations_legacy_proposer_recovery")
        for column in (
            "proposer_run_id",
            "proposer_owner",
            "proposer_lease_expires_at",
            "proposer_quiesced_at",
            "decision_requested",
            "decision_requested_at",
        ):
            db.execute(f"alter table workbench_confirmations drop column {column}")
        for column in ("execution_run_id", "runtime_quiesced_run_id"):
            db.execute(f"alter table workbench_turns drop column {column}")
        db.execute(
            "update service_state set value='2026-08-13.4' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = WorkbenchStore(db_path)

    expected_result = {
        "code": "legacy_proposer_state_unknown",
        "retryable": False,
        "status": "failed",
    }
    for confirmation_id in confirmations:
        result = migrated.get_confirmation(confirmation_id)
        assert result.status is ConfirmationStatus.FAILED
        assert json.loads(result.result_json) == expected_result
        assert result.decision_requested == ""
        assert result.decided_at
        assert migrated.claim_confirmation_execution(
            confirmation_id, owner="executor"
        ) is None
        with pytest.raises(ValueError, match="already been decided"):
            migrated.cancel_confirmation_execution(
                confirmation_id, resume_context="cancelled"
            )
    with migrated._connect() as db:
        internal_rows = db.execute(
            "select * from workbench_confirmations order by id"
        ).fetchall()
    for row in internal_rows:
        assert row["execution_owner"] == ""
        assert row["execution_lease_expires_at"] == ""
        assert row["execution_started_at"] == ""
        assert row["authorization_consumed_at"] == ""
        assert row["proposer_owner"] == ""
        assert row["proposer_lease_expires_at"] == ""
        assert row["proposer_quiesced_at"] == ""
    assert migrated.get_turn(turns["waiting"]).status is TurnStatus.FAILED
    assert migrated.get_turn(turns["queued"]).status is TurnStatus.FAILED
    assert migrated.get_turn(turns["running"]).status is TurnStatus.FAILED
    assert migrated.get_turn(turns["completed"]).status is TurnStatus.COMPLETED
    before = {
        turn_id: [
            (event.sequence, event.event_type, event.payload)
            for event in migrated.events_after(turn_id)
        ]
        for turn_id in turns.values()
    }
    assert sum(event[1] == "turn_failed" for event in before[turns["waiting"]]) == 1
    assert sum(event[1] == "turn_failed" for event in before[turns["queued"]]) == 1
    assert sum(event[1] == "turn_failed" for event in before[turns["running"]]) == 1
    assert sum(event[1] == "turn_failed" for event in before[turns["completed"]]) == 0

    with migrated._connect() as db:
        db.execute(
            "update service_state set value='2026-08-13.4' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    repeated = WorkbenchStore(db_path)
    assert {
        turn_id: [
            (event.sequence, event.event_type, event.payload)
            for event in repeated.events_after(turn_id)
        ]
        for turn_id in turns.values()
    } == before


def test_store_has_no_public_confirmation_decision_bypass(tmp_path: Path):
    store = _store(tmp_path)
    assert not hasattr(store, "decide_confirmation")
    assert not hasattr(store, "get_confirmation_for_executor")


def test_public_turn_excludes_private_resume_context(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    with store._connect() as db:
        db.execute(
            "update workbench_turns set resume_context='private receipt' where id=?",
            (turn_id,),
        )
    public = store.get_turn(turn_id)
    assert "resume_context" not in public.model_dump()
    assert (
        store.resume_context_for_executor(
            turn_id, owner="worker-1", now="2026-08-13T00:00:01Z"
        )
        == "private receipt"
    )


def test_create_turn_persists_initial_status_event_and_lists_nested_resources(
    tmp_path: Path,
):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    assert store.list_turns(task.id) == [turn]
    assert [event.payload for event in store.events_after(turn.id, limit=1)] == [
        {"status": "queued"}
    ]
    assert store.list_artifacts(task.id) == []


def test_archived_task_rejects_new_turn_and_active_task_rejects_archive(
    tmp_path: Path,
):
    store = _store(tmp_path)
    archived = store.create_task(title="Archived", runtime_kind="codex")
    store.archive_task(archived.id)

    with pytest.raises(ValueError, match="workbench task is archived"):
        store.create_turn(
            archived.id,
            user_text="Must not run",
            client_request_id="archived-request",
        )

    active = store.create_task(title="Active", runtime_kind="codex")
    store.create_turn(
        active.id,
        user_text="Still running",
        client_request_id="active-request",
    )
    with pytest.raises(ValueError, match="workbench task has an active turn"):
        store.archive_task(active.id)
    assert store.get_task(active.id).archived_at == ""


def test_archived_task_returns_exact_idempotent_turn_before_rejecting_new_requests(
    tmp_path: Path,
):
    store = _store(tmp_path)
    task = store.create_task(title="Archived retry", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Generate report",
        client_request_id="stable-request",
    )
    assert store.claim_next_turn(owner="worker") is not None
    store.complete_turn(
        turn.id,
        status=TurnStatus.COMPLETED,
        final_text="done",
        owner="worker",
    )
    store.archive_task(task.id)

    retry = store.create_turn(
        task.id,
        user_text="Generate report",
        client_request_id="stable-request",
    )

    assert retry.id == turn.id
    with pytest.raises(WorkbenchConflictError) as archived_error:
        store.create_turn(
            task.id,
            user_text="Generate another report",
            client_request_id="new-request",
        )
    assert archived_error.value.code == "task_archived"
    with pytest.raises(WorkbenchConflictError) as collision_error:
        store.create_turn(
            task.id,
            user_text="Different request body",
            client_request_id="stable-request",
        )
    assert collision_error.value.code == "client_request_conflict"


def test_archive_and_create_turn_race_has_one_valid_outcome(tmp_path: Path):
    first = _store(tmp_path)
    second = WorkbenchStore(first.path)
    task = first.create_task(title="Race", runtime_kind="codex")
    barrier = threading.Barrier(2)

    def archive():
        barrier.wait()
        try:
            return "archived", second.archive_task(task.id)
        except ValueError as exc:
            return "rejected", str(exc)

    def create():
        barrier.wait()
        try:
            return "created", first.create_turn(
                task.id,
                user_text="Race turn",
                client_request_id="race-request",
            )
        except ValueError as exc:
            return "rejected", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        archive_result, create_result = executor.map(
            lambda operation: operation(), (archive, create)
        )

    outcomes = {archive_result[0], create_result[0]}
    assert outcomes in ({"archived", "rejected"}, {"created", "rejected"})
    persisted_task = first.get_task(task.id)
    turns = first.list_turns(task.id)
    assert (bool(persisted_task.archived_at), len(turns)) in {(True, 0), (False, 1)}


def test_confirmation_quiescence_exposes_only_truthful_public_boolean(tmp_path: Path):
    store = _store(tmp_path)
    _, _, confirmation = _waiting_confirmation(store)

    assert store.confirmation_is_quiesced(confirmation.id) is True
    assert store.confirmation_is_quiesced("00000000-0000-4000-8000-000000000099") is None


def test_workbench_query_indexes_exist(tmp_path: Path):
    store = _store(tmp_path)
    with store._connect() as db:
        indexes = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            ).fetchall()
        }
    assert {
        "idx_workbench_turns_queue",
        "idx_workbench_turns_recovery",
        "idx_workbench_confirmations_recovery",
        "idx_workbench_confirmations_turn_status",
        "idx_workbench_confirmations_ready_intents",
        "idx_workbench_confirmations_proposer_recovery",
        "idx_workbench_confirmations_legacy_proposer_recovery",
        "idx_workbench_confirmations_legacy_execution_owner_recovery",
        "idx_workbench_confirmations_legacy_execution_lease_recovery",
        "idx_workbench_events_turn_id_id",
        "idx_workbench_artifacts_turn_created_id",
        "idx_workbench_turns_task_created_id",
        "idx_workbench_turns_task_sequence",
        "idx_workbench_tasks_updated_id",
        "idx_workbench_events_id_turn_id",
        "idx_workbench_artifacts_created_id_turn",
        "idx_workbench_confirmations_created_id_turn",
        "idx_workbench_confirmations_turn_created_id",
        "idx_workbench_attachments_task_created_id",
        "idx_workbench_events_event_type",
    } <= indexes
    with store._connect() as db:
        plan = " ".join(
            row["detail"]
            for row in db.execute(
                """
                explain query plan select id from workbench_confirmations
                where turn_id=? and status=? and result_json=''
                order by created_at, id
                """,
                ("turn-id", "confirmed"),
            ).fetchall()
        )
    assert "idx_workbench_confirmations_turn_status" in plan
    with store._connect() as db:
        timeline_plans = {
            "events": db.execute(
                """explain query plan select * from workbench_events
                   where turn_id in (?,?) order by id desc limit ?""",
                ("turn-1", "turn-2", 1001),
            ).fetchall(),
            "artifacts": db.execute(
                """explain query plan select * from workbench_artifacts
                   where turn_id in (?,?) order by created_at,id limit 101""",
                ("turn-1", "turn-2"),
            ).fetchall(),
            "confirmations": db.execute(
                """explain query plan select * from workbench_confirmations
                   where turn_id in (?,?) order by created_at,id limit 101""",
                ("turn-1", "turn-2"),
            ).fetchall(),
        }
        stats_plan = db.execute(
            """explain query plan select event_type,count(*)
               from workbench_events group by event_type"""
        ).fetchall()
    for table, rows in timeline_plans.items():
        rendered = " ".join(row["detail"] for row in rows)
        assert f"SEARCH workbench_{table}" in rendered
        assert "turn_id=?" in rendered
    rendered_stats_plan = " ".join(row["detail"] for row in stats_plan)
    assert "COVERING INDEX idx_workbench_events_event_type" in rendered_stats_plan
    assert "TEMP B-TREE" not in rendered_stats_plan
    with store._connect() as db:
        ready_plan = " ".join(
            row["detail"]
            for row in db.execute(
                """
                explain query plan select id, decision_requested
                from workbench_confirmations
                where status='pending' and decision_requested<>''
                  and proposer_run_id<>'' and proposer_quiesced_at<>''
                order by decision_requested_at, id limit 2
                """
            ).fetchall()
        )
        recovery_queries = (
            (
                workbench_store_module.LEGACY_PENDING_PROPOSER_RECOVERY_SQL,
                ("", 10),
                "idx_workbench_confirmations_legacy_proposer_recovery",
            ),
            (
                workbench_store_module.EXPIRED_PENDING_PROPOSER_RECOVERY_SQL,
                ("2099-01-01 00:00:00", 10),
                "idx_workbench_confirmations_proposer_recovery",
            ),
            (
                workbench_store_module.LEGACY_CONFIRMED_OWNER_RECOVERY_SQL,
                ("", 10),
                "idx_workbench_confirmations_legacy_execution_owner_recovery",
            ),
            (
                workbench_store_module.LEGACY_CONFIRMED_LEASE_RECOVERY_SQL,
                ("", 10),
                "idx_workbench_confirmations_legacy_execution_lease_recovery",
            ),
            (
                workbench_store_module.EXPIRED_CONFIRMED_EXECUTION_RECOVERY_SQL,
                ("2099-01-01 00:00:00", 10),
                "idx_workbench_confirmations_recovery",
            ),
        )
        recovery_plans = [
            (
                " ".join(
                    row["detail"]
                    for row in db.execute(
                        f"explain query plan {sql}", parameters
                    ).fetchall()
                ),
                index_name,
            )
            for sql, parameters, index_name in recovery_queries
        ]
        assert all(" or " not in sql.lower() for sql, _, _ in recovery_queries)
    assert "idx_workbench_confirmations_ready_intents" in ready_plan
    assert "USE TEMP B-TREE" not in ready_plan
    for recovery_plan, index_name in recovery_plans:
        assert index_name in recovery_plan
        assert "SCAN confirmations" not in recovery_plan
        assert "SCAN workbench_confirmations" not in recovery_plan
        assert "USE TEMP B-TREE" not in recovery_plan


def test_timeline_snapshot_work_is_bounded_by_selected_turns_not_global_events(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    selected_task = store.create_task(title="Selected", runtime_kind="codex")
    selected_turn = store.create_turn(
        selected_task.id,
        user_text="selected",
        client_request_id="selected-request",
    )
    store.request_stop(selected_turn.id)
    unrelated_task = store.create_task(title="Unrelated", runtime_kind="codex")
    unrelated_turn_id = "00000000-0000-4000-8000-000000000050"
    with store._connect() as db:
        db.execute(
            """insert into workbench_turns
               (id,task_id,client_request_id,task_sequence,user_text,status)
               values(?,?,?,?,?,?)""",
            (
                unrelated_turn_id,
                unrelated_task.id,
                "unrelated-request",
                1,
                "unrelated",
                "completed",
            ),
        )
        db.executemany(
            """insert into workbench_events
               (turn_id,sequence,event_type,payload_json)
               values(?,?,'text_delta','{"text":"noise"}')""",
            ((unrelated_turn_id, sequence) for sequence in range(1, 50_001)),
        )

    progress_calls = 0
    original_open = store._open_connection

    def counted_connection():
        nonlocal progress_calls
        connection = original_open()

        def progress():
            nonlocal progress_calls
            progress_calls += 1
            return 0

        connection.set_progress_handler(progress, 100)
        return connection

    monkeypatch.setattr(store, "_open_connection", counted_connection)

    snapshot = store.timeline_snapshot(selected_task.id)

    assert [turn.id for turn in snapshot[1]] == [selected_turn.id]
    assert {event.turn_id for event in snapshot[2]} == {selected_turn.id}
    assert progress_calls * 100 < 20_000


def test_get_task_summary_uses_latest_turn_index_and_limit_one(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Summary", runtime_kind="codex")
    with store._connect() as db:
        for index in range(1_000):
            created_at = f"2026-08-{1 + index // 24:02d} {index % 24:02d}:00:00"
            db.execute(
                """insert into workbench_turns
                   (id,task_id,client_request_id,task_sequence,user_text,status,
                    created_at,updated_at)
                   values(?,?,?,?,?,?,?,?)""",
                (
                    f"00000000-0000-4000-8000-{index:012d}",
                    task.id,
                    f"summary-{index}",
                    index + 1,
                    "summary",
                    "completed" if index < 999 else "failed",
                    created_at,
                    created_at,
                ),
            )
        plan = " ".join(
            row["detail"]
            for row in db.execute(
                """explain query plan
                   select status from workbench_turns where task_id=?
                   order by task_sequence desc limit 1""",
                (task.id,),
            ).fetchall()
        )

    summary = store.get_task_summary(task.id)

    assert summary is not None
    assert summary[1] == "failed"
    assert "idx_workbench_turns_task_sequence" in plan
    assert "TEMP B-TREE" not in plan


def test_stale_prior_run_quiescence_cannot_unlock_confirmation(tmp_path: Path):
    store = _store(tmp_path)
    _, turn_id, confirmation = _waiting_confirmation(store)
    old_run = store.execution_run_id_for_executor(turn_id, owner="seed")
    with store._connect() as db:
        db.execute(
            """
            update workbench_confirmations
            set proposer_quiesced_at='', decision_requested='confirm'
            where id=?
            """,
            (confirmation.id,),
        )
        db.execute(
            """
            update workbench_turns
            set execution_run_id='new-run', runtime_quiesced_run_id=''
            where id=?
            """,
            (turn_id,),
        )

    with pytest.raises(ValueError, match="proposer run is stale"):
        store.mark_confirmation_proposer_quiesced(
            turn_id,
            owner="seed",
            proposer_run_id=old_run,
        )
    assert store.claim_confirmation_execution(
        confirmation.id, owner="executor", lease_seconds=10
    ) is None


def test_create_task_and_idempotent_turn_request(tmp_path: Path):
    store = _store(tmp_path)

    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    first = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    second = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    assert first == second
    assert first.task_sequence == second.task_sequence == 1
    assert first.status is TurnStatus.QUEUED
    assert store.get_task(task.id) == task


def test_first_turn_derives_default_title_and_idempotently_replays(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="新任务", runtime_kind="codex")
    user_text = "  今天有哪些 \n\t 值得我关注的事项？  "

    first = store.create_turn(
        task.id,
        user_text=user_text,
        client_request_id="derive-default-title",
    )
    derived_task = store.get_task(task.id)
    replayed = store.create_turn(
        task.id,
        user_text="\u3000今天有哪些 \n\t 值得我关注的事项？\u00a0",
        client_request_id="derive-default-title",
    )

    assert replayed == first
    assert replayed.user_text == "今天有哪些 \n\t 值得我关注的事项？"
    assert derived_task.title == "今天有哪些 值得我关注的事项？"
    assert store.get_task(task.id) == derived_task


def test_first_turn_does_not_overwrite_manually_renamed_default_title(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="新任务", runtime_kind="codex")
    store.rename_task(task.id, title="每日关注")

    store.create_turn(
        task.id,
        user_text="检查今天的重要事项",
        client_request_id="manual-title",
    )

    assert store.get_task(task.id).title == "每日关注"


def test_second_turn_never_derives_title_after_title_is_restored_to_default(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="项目复盘", runtime_kind="codex")
    first = store.create_turn(
        task.id,
        user_text="总结昨天的进展",
        client_request_id="completed-first-turn",
    )
    claimed = store.claim_next_turn(
        owner="worker-1", lease_seconds=30, now="2026-08-13T00:00:00Z"
    )
    assert claimed is not None and claimed.id == first.id
    completed = store.complete_turn(
        first.id,
        status=TurnStatus.COMPLETED,
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    assert completed.status is TurnStatus.COMPLETED
    store.rename_task(task.id, title="新任务")

    store.create_turn(
        task.id,
        user_text="检查今天的重要事项",
        client_request_id="second-turn-after-restore",
    )

    assert store.get_task(task.id).title == "新任务"


def test_derived_default_title_truncates_to_unicode_boundary(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="新任务", runtime_kind="codex")
    user_text = "请帮我整理今天与人工智能🚀、产品、客户和团队相关的重要事项，并指出需要我立即处理的风险和机会"

    store.create_turn(
        task.id,
        user_text=user_text,
        client_request_id="truncated-derived-title",
    )

    title = store.get_task(task.id).title
    assert len(title) == 32
    assert title[-1] == "…"
    assert title == user_text[:31].rstrip() + "…"
    assert all(not 0xD800 <= ord(character) <= 0xDFFF for character in title)
    assert "人工智能" in title
    assert "🚀" in title


def test_create_turn_rejects_second_active_request_for_task(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    with pytest.raises(ValueError, match="task already has an active turn"):
        store.create_turn(
            task.id,
            user_text="Compare products",
            client_request_id="request-2",
        )


def test_events_replay_in_id_order_and_reject_duplicate_sequence(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    with pytest.raises(ValueError, match="event sequence must be next"):
        store.append_event(
            turn_id,
            sequence=3,
            event_type="text_delta",
            payload={"text": "South"},
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    first = store.append_event(
        turn_id,
        sequence=2,
        event_type="text_delta",
        payload={"text": "North"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    with pytest.raises(ValueError, match="event sequence must be next"):
        store.append_event(
            turn_id,
            sequence=4,
            event_type="text_delta",
            payload={"text": "gap"},
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    with pytest.raises(ValueError, match="event sequence must be next"):
        store.append_event(
            turn_id,
            sequence=2,
            event_type="text_delta",
            payload={"text": "duplicate"},
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    second = store.append_event(
        turn_id,
        sequence=3,
        event_type="text_delta",
        payload={"text": "South"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )

    assert store.events_after(turn_id, after_id=first.id) == [second]


def test_event_stream_snapshot_returns_bounded_events_and_authoritative_turn(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    store.append_event(
        turn_id,
        sequence=2,
        event_type="text_delta",
        payload={"text": "one"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )

    events, turn = store.event_stream_snapshot(turn_id, after_id=0, limit=1)

    assert len(events) == 1
    assert turn is not None and turn.status is TurnStatus.RUNNING


def test_append_artifact_event_is_atomic_and_public_payload_has_no_path(tmp_path: Path):
    store, task_id, turn_id = _running_turn(tmp_path)
    artifact_path = tmp_path / "report.txt"
    artifact_path.write_text("report", encoding="utf-8")
    artifact, event = store.append_artifact_event(
        turn_id,
        sequence=2,
        label="Report",
        path=str(artifact_path),
        media_type="text/plain",
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    assert event.payload == {
        "artifact_id": artifact.id,
        "label": "Report",
        "filename": "report.txt",
        "media_type": "text/plain",
    }
    with pytest.raises(ValueError):
        store.append_artifact_event(
            turn_id,
            sequence=4,
            label="Invalid",
            path=str(artifact_path),
            media_type="text/plain",
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    assert len(store.list_artifacts(task_id)) == 1
    assert len(store.events_after(turn_id)) == 2


def test_append_artifact_event_rolls_back_artifact_when_event_insert_fails(tmp_path: Path):
    store, task_id, turn_id = _running_turn(tmp_path)
    artifact_path = tmp_path / "report.txt"
    artifact_path.write_text("report", encoding="utf-8")
    with store._connect() as db:
        db.execute(
            """create trigger reject_artifact_event before insert on workbench_events
               when new.event_type='artifact_created'
               begin select raise(abort, 'rejected'); end"""
        )

    with pytest.raises(sqlite3.IntegrityError):
        store.append_artifact_event(
            turn_id,
            sequence=2,
            label="Report",
            path=str(artifact_path),
            media_type="text/plain",
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )

    assert store.list_artifacts(task_id) == []
    assert [event.event_type for event in store.events_after(turn_id)] == [
        "status_changed"
    ]


def test_recover_expired_running_turn_as_queued(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    claimed = store.claim_next_turn(
        owner="worker-1", lease_seconds=1, now="2026-08-13T00:00:00Z"
    )
    assert claimed is not None

    assert store.recover_expired_turns(now="2026-08-13T00:00:02Z") == 1
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED


def test_recovery_leaves_waiting_confirmation_turn_unchanged(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    store.claim_next_turn(
        owner="worker-1", lease_seconds=1, now="2026-08-13T00:00:00Z"
    )
    store.create_confirmation(
        turn.id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:00Z",
    )

    assert store.recover_expired_turns(now="2026-08-13T00:00:02Z") == 0
    assert store.get_turn(turn.id).status is TurnStatus.WAITING_CONFIRMATION


def test_transition_to_waiting_confirmation_releases_worker_lease(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:00Z")

    store.create_confirmation(
        turn.id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )

    assert store.get_turn(turn.id).status is TurnStatus.WAITING_CONFIRMATION
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select lease_owner from workbench_turns where id=?", (turn.id,)
        ).fetchone()[0] == ""


def test_running_transition_requires_claiming_a_lease(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    with pytest.raises(ValueError, match="running turns must be claimed"):
        store.complete_turn(turn.id, status=TurnStatus.RUNNING)


def test_running_turn_executor_mutations_require_an_owner(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload={"text": "North"},
        )

    store, _, turn_id = _running_turn(tmp_path / "confirmation")
    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.create_confirmation(
            turn_id,
            action_kind="send_message",
            target="sales@example.com",
            summary="Send the regional comparison",
            risk="external communication",
            arguments_json={"channel": "email"},
        )

    store, _, turn_id = _running_turn(tmp_path / "complete")
    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.complete_turn(turn_id, status=TurnStatus.COMPLETED)


def test_running_turn_executor_mutations_reject_mismatched_and_expired_owners(
    tmp_path: Path,
):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="turn lease is stale"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload={"text": "North"},
            owner="worker-2",
            now="2026-08-13T00:00:01Z",
        )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.create_confirmation(
            turn_id,
            action_kind="send_message",
            target="sales@example.com",
            summary="Send the regional comparison",
            risk="external communication",
            arguments_json={"channel": "email"},
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.complete_turn(
            turn_id,
            status=TurnStatus.COMPLETED,
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )


def test_confirmation_list_redacts_arguments_and_execution_claim_exposes_them_once(
    tmp_path: Path,
):
    store, task_id, turn_id = _running_turn(tmp_path)
    confirmation = store.create_confirmation(
        turn_id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    store.mark_confirmation_proposer_quiesced(
        turn_id,
        owner="worker-1",
        proposer_run_id=store.execution_run_id_for_executor(turn_id, owner="worker-1"),
        now="2026-08-13T00:00:01Z",
    )

    assert store.list_confirmations(task_id)[0].arguments_json == ""
    with pytest.raises(TypeError):
        store.list_confirmations(task_id, include_arguments=True)
    assert (
        store.claim_confirmation_execution(
            confirmation.id,
            owner="executor-1",
            lease_seconds=10,
            now="2026-08-13T00:00:02Z",
        ).arguments_json
        == '{"channel":"email"}'
    )
    assert (
        store.claim_confirmation_execution(
            confirmation.id,
            owner="executor-2",
            lease_seconds=10,
            now="2026-08-13T00:00:03Z",
        )
        is None
    )
    public = store.get_confirmation(confirmation.id)
    assert public.arguments_json == ""
    assert not hasattr(public, "execution_owner")
    with store._connect() as db:
        row = db.execute(
            "select * from workbench_confirmations where id=?", (confirmation.id,)
        ).fetchone()
    assert row["execution_owner"] == "executor-1"
    assert row["execution_started_at"] == "2026-08-13 00:00:02"
    assert row["execution_lease_expires_at"] == "2026-08-13 00:00:12"


def test_claim_owner_can_finish_receipt_after_turn_is_stopped(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    confirmation = store.create_confirmation(
        turn_id,
        action_kind="reviewed_cli",
        target="Executive group",
        summary="Send update",
        risk="External message",
        arguments_json={"argv": ["dws", "chat", "message", "send", "--yes"]},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    store.mark_confirmation_proposer_quiesced(
        turn_id,
        owner="worker-1",
        proposer_run_id=store.execution_run_id_for_executor(turn_id, owner="worker-1"),
        now="2026-08-13T00:00:01Z",
    )
    assert store.claim_confirmation_execution(
        confirmation.id,
        owner="executor-1",
        lease_seconds=10,
        now="2026-08-13T00:00:02Z",
    )
    with store._connect() as db:
        db.execute(
            "update workbench_confirmations set authorization_consumed_at=? where id=?",
            ("2026-08-13 00:00:02", confirmation.id),
        )

    assert store.request_stop(
        turn_id, now="2026-08-13T00:00:03Z"
    ).status is TurnStatus.STOPPED
    result = store.finish_confirmation_execution(
        confirmation.id,
        owner="executor-1",
        status=ConfirmationStatus.EXECUTED,
        result_json={"status": "executed", "receipt_digest": "a" * 64},
        resume_context='{"status":"executed"}',
        now="2026-08-13T00:00:04Z",
    )

    assert result.status is ConfirmationStatus.EXECUTED
    assert store.get_turn(turn_id).status is TurnStatus.STOPPED
    with store._connect() as db:
        row = db.execute(
            "select * from workbench_confirmations where id=?", (confirmation.id,)
        ).fetchone()
    assert row["execution_owner"] == ""
    assert row["execution_lease_expires_at"] == ""


def test_reconcile_confirmation_claims_only_when_abandoned(tmp_path: Path):
    store = _store(tmp_path)
    _, live_turn, live = _waiting_confirmation(store)
    assert store.claim_confirmation_execution(
        live.id,
        owner="live-executor",
        lease_seconds=10,
        now="2026-08-13T00:00:00Z",
    )

    assert store.reconcile_confirmed_without_result(now="2026-08-13T00:00:05Z") == 0
    assert store.get_confirmation(live.id).status is ConfirmationStatus.CONFIRMED
    assert store.get_turn(live_turn).status is TurnStatus.WAITING_CONFIRMATION
    assert store.reconcile_confirmed_without_result(now="2026-08-13T00:00:11Z") == 1
    assert store.get_confirmation(live.id).status is ConfirmationStatus.FAILED
    assert store.get_turn(live_turn).status is TurnStatus.FAILED


def test_confirmation_receipt_rejects_non_owner_and_expired_claim(tmp_path: Path):
    store = _store(tmp_path)
    _, _, confirmation = _waiting_confirmation(store)
    assert store.claim_confirmation_execution(
        confirmation.id,
        owner="executor-1",
        lease_seconds=10,
        now="2026-08-13T00:00:00Z",
    )

    for owner, now in (
        ("executor-2", "2026-08-13T00:00:01Z"),
        ("executor-1", "2026-08-13T00:00:11Z"),
    ):
        with pytest.raises(ValueError, match="execution lease is stale"):
            store.finish_confirmation_execution(
                confirmation.id,
                owner=owner,
                status=ConfirmationStatus.EXECUTED,
                result_json={"status": "executed"},
                resume_context='{"status":"executed"}',
                now=now,
            )
    assert store.get_confirmation(confirmation.id).status is ConfirmationStatus.CONFIRMED


def test_recovered_stale_worker_cannot_append_events(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    assert store.recover_expired_turns(now="2026-08-13T00:00:11Z") == 1

    with pytest.raises(ValueError, match="turn lease requires running status"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload={"text": "stale"},
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )


def test_provider_session_requires_running_turn_lease(tmp_path: Path):
    store, task_id, turn_id = _running_turn(tmp_path)

    with pytest.raises(TypeError):
        store.set_provider_session(turn_id, "session-1", now="2026-08-13T00:00:01Z")
    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.set_provider_session(
            turn_id,
            "session-1",
            owner="",
            now="2026-08-13T00:00:01Z",
        )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.set_provider_session(
            turn_id,
            "session-1",
            owner="worker-2",
            now="2026-08-13T00:00:01Z",
        )

    assert store.set_provider_session(
        turn_id,
        "session-1",
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    ).id == task_id
    assert store.get_task(task_id).provider_session_ref == "session-1"
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.set_provider_session(
            turn_id,
            "session-2",
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )


@pytest.mark.parametrize("payload", [{"value": float("nan")}, '{"value":NaN}'])
def test_event_payload_rejects_nonfinite_json(tmp_path: Path, payload):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="payload must be a JSON object"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload=payload,
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )


def test_confirmation_arguments_reject_nonfinite_json(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="arguments_json must be a JSON object"):
        store.create_confirmation(
            turn_id,
            action_kind="send_message",
            target="sales@example.com",
            summary="Send the regional comparison",
            risk="external communication",
            arguments_json='{"value":Infinity}',
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )


def test_stop_is_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    stopped_turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    first_stop = store.request_stop(stopped_turn.id, now="2026-08-13T00:00:01Z")
    assert store.request_stop(
        stopped_turn.id, now="2026-08-13T00:00:02Z"
    ) == first_stop

    active_turn = store.create_turn(
        task.id,
        user_text="Compare products",
        client_request_id="request-2",
    )
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:03Z")
    completed = store.complete_turn(
        active_turn.id,
        status=TurnStatus.COMPLETED,
        owner="worker-1",
        now="2026-08-13T00:00:06Z",
    )
    with pytest.raises(ValueError, match="invalid turn transition"):
        store.request_stop(completed.id, now="2026-08-13T00:00:07Z")


def test_attachment_db_failure_leaves_no_orphan_file(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            create trigger reject_workbench_attachment
            before insert on workbench_attachments
            begin
                select raise(abort, 'injected attachment failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected attachment failure"):
        store.save_attachment(
            task.id,
            filename="report.txt",
            media_type="text/plain",
            content=b"private",
        )

    directory = tmp_path / "workbench" / "attachments" / task.id
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("task_id", ["../escaped", "not-a-task-id"])
def test_attachment_rejects_untrusted_task_ids_before_filesystem_access(
    tmp_path: Path, task_id: str
):
    store = _store(tmp_path)

    with pytest.raises(ValueError):
        store.save_attachment(
            task_id,
            filename="report.txt",
            media_type="text/plain",
            content=b"private",
        )

    assert not (tmp_path / "workbench").exists()


def test_attachment_replace_or_commit_failure_leaves_no_metadata_or_file(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    directory = tmp_path / "workbench" / "attachments" / task.id

    def fail_commit(_db):
        raise sqlite3.OperationalError("injected commit failure")

    monkeypatch.setattr(
        store, "_commit_attachment_metadata", fail_commit, raising=False
    )
    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        store.save_attachment(
            task.id,
            filename="report.txt",
            media_type="text/plain",
            content=b"private",
        )
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from workbench_attachments").fetchone()[0] == 0
    assert list(directory.iterdir()) == []

    monkeypatch.delattr(store, "_commit_attachment_metadata", raising=False)

    def fail_replace(*_args):
        raise OSError("injected replace failure")

    monkeypatch.setattr("app.workbench.store.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        store.save_attachment(
            task.id,
            filename="report.txt",
            media_type="text/plain",
            content=b"private",
        )
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from workbench_attachments").fetchone()[0] == 0
    assert list(directory.iterdir()) == []


def test_attachment_reconciliation_removes_generated_orphans(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    directory = tmp_path / "workbench" / "attachments" / task.id
    directory.mkdir(parents=True)
    stale_temp = directory / ".stale.tmp"
    stale_temp.write_bytes(b"temp")
    orphan_id = "fdd6195c-07f1-4aa5-902f-39e0468be9da"
    orphan_file = directory / orphan_id
    orphan_file.write_bytes(b"orphan")
    missing_id = "5ff5ff8c-9ef3-4190-8d32-f6a4a6cf1a11"
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            insert into workbench_attachments (
                id, task_id, client_request_id, filename, media_type,
                size_bytes, storage_path, content_sha256
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                missing_id,
                task.id,
                missing_id,
                "missing.txt",
                "text/plain",
                0,
                str(directory / missing_id),
                "0" * 64,
            ),
        )

    WorkbenchStore(store.path)

    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from workbench_attachments").fetchone()[0] == 0
    assert list(directory.iterdir()) == []


def test_confirmation_creation_and_control_events_are_atomic(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    confirmation = store.create_confirmation(
        turn_id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )

    assert store.get_turn(turn_id).status is TurnStatus.WAITING_CONFIRMATION
    events = [
        (event.sequence, event.event_type, event.payload)
        for event in store.events_after(turn_id)
    ]
    assert events == [
        (1, "status_changed", {"status": "queued"}),
        (
            2,
            "confirmation_required",
            {
                "action_kind": "send_message",
                "confirmation_id": confirmation.id,
                "target": "sales@example.com",
            },
        )
    ]


def test_stop_and_terminal_completion_append_atomic_control_events(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    queued = store.create_turn(
        task.id, user_text="Compare regions", client_request_id="request-1"
    )
    stopped = store.request_stop(queued.id, now="2026-08-13T00:00:01Z")
    assert stopped.status is TurnStatus.STOPPED
    assert [event.event_type for event in store.events_after(queued.id)] == [
        "status_changed",
        "turn_completed"
    ]

    running = store.create_turn(
        task.id, user_text="Compare products", client_request_id="request-2"
    )
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:02Z")
    stop_requested = store.request_stop(running.id, now="2026-08-13T00:00:03Z")
    assert stop_requested.status is TurnStatus.STOPPED
    assert stop_requested.stop_requested is True
    assert [event.event_type for event in store.events_after(running.id)] == [
        "status_changed",
        "turn_completed"
    ]
    assert store.request_stop(running.id) == stop_requested
    assert len(store.events_after(running.id)) == 2

    failing = store.create_turn(
        task.id, user_text="Compare channels", client_request_id="request-3"
    )
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:05Z")
    failed = store.complete_turn(
        failing.id,
        status=TurnStatus.FAILED,
        error_code="provider_error",
        owner="worker-1",
        now="2026-08-13T00:00:06Z",
    )
    assert failed.status is TurnStatus.FAILED
    assert [event.event_type for event in store.events_after(failing.id)] == [
        "status_changed",
        "turn_failed"
    ]


def test_independent_store_instances_idempotently_create_one_turn(tmp_path: Path):
    first = _store(tmp_path)
    task = first.create_task(title="Analyse sales", runtime_kind="codex")
    second = _store(tmp_path)
    barrier = threading.Barrier(2)

    def create(store: WorkbenchStore):
        barrier.wait()
        return store.create_turn(
            task.id,
            user_text="Compare regions",
            client_request_id="request-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(create, (first, second)))

    assert created[0] == created[1]


def test_independent_stores_allow_one_active_turn_and_one_claim(tmp_path: Path):
    first = _store(tmp_path)
    task = first.create_task(title="Analyse sales", runtime_kind="codex")
    second = _store(tmp_path)
    barrier = threading.Barrier(2)

    def create(store: WorkbenchStore, client_request_id: str):
        try:
            barrier.wait()
            return "ok", store.create_turn(
                task.id,
                user_text="Compare regions",
                client_request_id=client_request_id,
            )
        except ValueError as exc:
            return "error", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(create, (first, second), ("request-1", "request-2"))
        )
    assert [result[0] for result in results].count("ok") == 1
    assert [result[0] for result in results].count("error") == 1

    turn = next(result[1] for result in results if result[0] == "ok")
    barrier = threading.Barrier(2)

    def claim(store: WorkbenchStore, owner: str):
        barrier.wait()
        return store.claim_next_turn(owner=owner, now="2026-08-13T00:00:01Z")

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, (first, second), ("worker-1", "worker-2")))
    assert sum(item is not None for item in claimed) == 1
    assert store_turn_id(claimed) == turn.id


def store_turn_id(turns) -> str:
    return next(turn.id for turn in turns if turn is not None)


def test_independent_stores_allow_one_next_sequence_insert(tmp_path: Path):
    first, _, turn_id = _running_turn(tmp_path)
    second = WorkbenchStore(first.path)
    barrier = threading.Barrier(2)

    def append(store: WorkbenchStore):
        try:
            barrier.wait()
            return "ok", store.append_event(
                turn_id,
                sequence=2,
                event_type="text_delta",
                payload={"text": "North"},
                owner="worker-1",
                now="2026-08-13T00:00:01Z",
            )
        except ValueError as exc:
            return "error", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, (first, second)))

    assert [result[0] for result in results].count("ok") == 1
    assert [result[0] for result in results].count("error") == 1
    assert [event.sequence for event in first.events_after(turn_id)] == [1, 2]


def test_independent_stores_linearize_stop_against_terminal_completion(tmp_path: Path):
    first, _, turn_id = _running_turn(tmp_path)
    second = WorkbenchStore(first.path)
    barrier = threading.Barrier(2)

    def stop():
        barrier.wait()
        try:
            return "stopped", second.request_stop(turn_id).status
        except ValueError as exc:
            return "error", str(exc)

    def complete():
        barrier.wait()
        try:
            return "completed", first.complete_turn(
                turn_id, status=TurnStatus.COMPLETED, owner="worker-1"
            ).status
        except ValueError as exc:
            return "error", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda operation: operation(), (stop, complete)))

    assert [result[0] for result in results].count("error") == 1
    persisted = first.get_turn(turn_id)
    assert persisted.status in {TurnStatus.STOPPED, TurnStatus.COMPLETED}
    events = first.events_after(turn_id)
    assert [event.sequence for event in events] == [1, 2]
    assert events[1].event_type == "turn_completed"


def test_running_stop_is_immediately_terminal_on_both_recovery_paths(
    tmp_path: Path,
):
    store, _, first_turn_id = _running_turn(tmp_path)
    store.request_stop(first_turn_id, now="2026-08-13T00:00:01Z")

    assert store.recover_expired_turns(now="2026-08-13T00:00:11Z") == 0
    assert store.get_turn(first_turn_id).status is TurnStatus.STOPPED
    assert [event.event_type for event in store.events_after(first_turn_id)] == [
        "status_changed",
        "turn_completed"
    ]
    assert store.claim_next_turn(owner="worker-2", now="2026-08-13T00:00:12Z") is None

    task = store.create_task(title="Analyse marketing", runtime_kind="codex")
    second_turn = store.create_turn(
        task.id,
        user_text="Compare campaigns",
        client_request_id="request-2",
    )
    store.claim_next_turn(
        owner="worker-1", lease_seconds=1, now="2026-08-13T00:01:00Z"
    )
    store.request_stop(second_turn.id, now="2026-08-13T00:01:01Z")

    assert store.claim_next_turn(owner="worker-2", now="2026-08-13T00:01:02Z") is None
    assert store.get_turn(second_turn.id).status is TurnStatus.STOPPED
    assert [event.event_type for event in store.events_after(second_turn.id)] == [
        "status_changed",
        "turn_completed"
    ]


def test_waiting_confirmation_terminalization_resolves_pending_confirmations(
    tmp_path: Path,
):
    store, task_id, turn_id = _running_turn(tmp_path)
    confirmation = store.create_confirmation(
        turn_id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    stopped = store.request_stop(turn_id, now="2026-08-13T00:00:02Z")

    assert stopped.status is TurnStatus.STOPPED
    listed = store.list_confirmations(task_id)
    assert listed[0].id == confirmation.id
    assert listed[0].status is ConfirmationStatus.CANCELLED
    assert listed[0].arguments_json == ""

    next_turn = store.create_turn(
        task_id,
        user_text="Compare products",
        client_request_id="request-2",
    )
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:03Z")
    failed_confirmation = store.create_confirmation(
        next_turn.id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the product comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:03Z",
    )
    failed = store.complete_turn(
        next_turn.id,
        status=TurnStatus.FAILED,
        error_code="provider_error",
        now="2026-08-13T00:00:04Z",
    )

    assert failed.status is TurnStatus.FAILED
    statuses = {item.id: item.status for item in store.list_confirmations(task_id)}
    assert statuses[failed_confirmation.id] is ConfirmationStatus.FAILED
    assert ConfirmationStatus.PENDING not in statuses.values()


def test_generic_completion_rejects_reserved_confirmation_transitions(tmp_path: Path):
    store, task_id, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="reserved"):
        store.complete_turn(
            turn_id,
            status=TurnStatus.WAITING_CONFIRMATION,
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )

    store.create_confirmation(
        turn_id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    with pytest.raises(ValueError, match="reserved"):
        store.complete_turn(
            turn_id,
            status=TurnStatus.QUEUED,
            now="2026-08-13T00:00:02Z",
        )
    assert store.get_turn(turn_id).status is TurnStatus.WAITING_CONFIRMATION
    assert store.list_confirmations(task_id)[0].status is ConfirmationStatus.PENDING


def test_attachment_symlink_components_are_rejected(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    outside = tmp_path / "outside"
    outside.mkdir()
    workbench = tmp_path / "workbench"
    workbench.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        store.save_attachment(
            task.id,
            filename="report.txt",
            media_type="text/plain",
            content=b"private",
        )
    assert list(outside.iterdir()) == []

    workbench.unlink()
    attachments = workbench / "attachments"
    attachments.mkdir(parents=True)
    task_directory = attachments / task.id
    task_directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        store.save_attachment(
            task.id,
            filename="report.txt",
            media_type="text/plain",
            content=b"private",
        )
    assert list(outside.iterdir()) == []


def test_reconciliation_waits_for_active_attachment_upload(tmp_path: Path, monkeypatch):
    first = _store(tmp_path)
    task = first.create_task(title="Analyse sales", runtime_kind="codex")
    temp_ready = threading.Event()
    release_upload = threading.Event()
    reconciliation_attempted = threading.Event()
    upload_done = threading.Event()
    reconciliation_done = threading.Event()
    upload_errors: list[Exception] = []

    def hold_upload(_temp_path):
        temp_ready.set()
        assert release_upload.wait(timeout=5)

    first._after_attachment_temp_created = hold_upload
    original_lock = WorkbenchStore._attachment_lock

    def observe_lock(self, *, create_workbench: bool):
        if not create_workbench:
            reconciliation_attempted.set()
        return original_lock(self, create_workbench=create_workbench)

    monkeypatch.setattr(WorkbenchStore, "_attachment_lock", observe_lock)

    def upload():
        try:
            first.save_attachment(
                task.id,
                filename="report.txt",
                media_type="text/plain",
                content=b"private",
            )
        except Exception as exc:  # pragma: no cover - assertion below reports it
            upload_errors.append(exc)
        finally:
            upload_done.set()

    def reconcile():
        WorkbenchStore(first.path)
        reconciliation_done.set()

    upload_thread = threading.Thread(target=upload)
    upload_thread.start()
    assert temp_ready.wait(timeout=5)
    assert list((tmp_path / "workbench" / "attachments" / task.id).glob("*.tmp"))
    reconcile_thread = threading.Thread(target=reconcile)
    reconcile_thread.start()
    assert reconciliation_attempted.wait(timeout=5)
    assert not reconciliation_done.is_set()

    release_upload.set()
    upload_thread.join(timeout=5)
    reconcile_thread.join(timeout=5)
    assert upload_done.is_set()
    assert reconciliation_done.is_set()
    assert upload_errors == []
    assert len(first.list_attachments(task.id)) == 1


def test_attachment_filename_cannot_escape_generated_task_directory(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    attachment = store.save_attachment(
        task.id,
        filename="../../secret.txt",
        media_type="text/plain",
        content=b"private",
    )

    with sqlite3.connect(store.path) as db:
        storage_path = Path(
            db.execute(
                "select storage_path from workbench_attachments where id=?",
                (attachment.id,),
            ).fetchone()[0]
        )
    assert storage_path.parent == (
        tmp_path / "workbench" / "attachments" / task.id
    )
    assert storage_path.read_bytes() == b"private"
