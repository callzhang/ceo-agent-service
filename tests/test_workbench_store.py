from tests.test_email_legacy_schema import published_source as published_source
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import app.store as store_module
from app.workbench.models import TurnStatus
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


def test_store_schema_check_rejects_missing_required_reply_attempt_column(
    tmp_path: Path,
):
    store = store_module.AutoReplyStore(tmp_path / "worker.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "alter table actions rename column attempt_human_decision_options_json to damaged_human_decision_options_json"
        )

    assert store._schema_is_current() is False


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

    _, turns, events, _, _, page = snapshot
    assert stopped.status is TurnStatus.STOPPED
    assert [(item.status, item.task_sequence) for item in turns] == [
        (TurnStatus.QUEUED, 1)
    ]
    assert [event.event_type for event in events] == ["status_changed"]
    assert page["task_state"] == TurnStatus.QUEUED.value


def test_store_has_no_public_confirmation_decision_bypass(tmp_path: Path):
    store = _store(tmp_path)
    assert not hasattr(store, "decide_confirmation")
    assert not hasattr(store, "get_confirmation_for_executor")
    for name in (
        "create_confirmation", "get_confirmation", "list_confirmations",
        "claim_confirmation_execution", "finish_confirmation_execution",
        "cancel_confirmation_execution", "mark_confirmation_proposer_quiesced",
        "confirmation_is_quiesced", "confirmation_quiescence",
        "reconcile_unquiesced_proposers",
    ):
        assert not hasattr(store, name)
    with store._connect() as db:
        assert db.execute(
            "select name from sqlite_schema where name like '%workbench_confirmations%'"
        ).fetchall() == []


def test_public_turn_excludes_private_resume_context(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    with store._connect() as db:
        db.execute(
            "update workbench_turns set resume_context=? where task_id=?",
            ("private receipt", turn_id),
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
        store.complete_turn(
            turn_id,
            status=TurnStatus.COMPLETED,
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )


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
    with store._connect() as db:
        db.execute(
            "insert into workbench_attachments(workbench_task_id,client_request_id,filename,"
            "media_type,size_bytes,content_sha256,storage_path,created_at) "
            "values(?,?,?,?,?,?,?,current_timestamp)",
            (task.id, missing_id, "missing.txt", "text/plain", 0,
             "0" * 64, str(directory / missing_id)),
        )

    WorkbenchStore(store.path)

    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from workbench_attachments").fetchone()[0] == 0
    assert list(directory.iterdir()) == []


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
