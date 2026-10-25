from pathlib import Path

import pytest

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore, ERROR_SQL
from app.scheduled_occurrence_storage import insert_occurrence
from app.schedules_storage import insert_schedule
from app.scheduled_config_storage import intern_scheduled_config
from app.action_storage import insert_error_action
from app.task_entity_storage import get_task, update_task_cas


def _schedule(db, identity):
    assert insert_schedule(db, dict(id=identity, name="Report", prompt="p",
        cron_expression="0 * * * *", timezone="UTC", runtime_id="", enabled=1)) == identity


def _set_reply_task(db, public_id, values):
    assert update_task_cas(
        db, task_id=public_id, expected={"status": "pending"}, changes=values,
    ) is not None


def _insert_occurrence(db, *, occurrence_id, event_id, scheduled_task_id, snapshot_id,
                       execution_kind, execution_id, dispatch_status="dispatched"):
    insert_occurrence(db, {
        "id": occurrence_id,
        "event_id": event_id,
        "scheduled_task_id": scheduled_task_id,
        "trigger_kind": "manual",
        "scheduled_for": "2026-10-07T10:00:00Z",
        "first_scheduled_for": "2026-10-07T10:00:00Z",
        "occurrence_count": 1,
        "dispatch_status": dispatch_status,
        "skip_or_error_reason": "",
        "snapshot_id": snapshot_id,
        "execution_kind": execution_kind,
        "execution_id": execution_id,
        "lease_owner": "",
        "result_summary": "",
        "created_at": "2026-10-07 10:00:00",
    })


@pytest.mark.parametrize("source", ["reply", "error"])
def test_attention_recovery_does_not_search_unrelated_successes_first(
    tmp_path: Path, source
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    failed = store.ensure_reply_task(
        conversation_id="unrelated-chat",
        conversation_title="Chat",
        single_chat=True,
        trigger_message_id="message",
        trigger_create_time="2026-10-07 10:00:00",
        trigger_sender="sender",
        trigger_text="request",
    )
    completed = store.ensure_reply_task(
        conversation_id="scheduled-task-run:1",
        conversation_title="Report",
        single_chat=False,
        trigger_message_id="report",
        trigger_create_time="2026-10-07 10:00:00",
        trigger_sender="scheduler",
        trigger_text="report",
        channel="scheduled",
    )
    with store._connect() as db:
        _set_reply_task(db, completed.id, {"status": "done"})
        if source == "reply":
            _set_reply_task(db, failed.id, {"status": "failed", "error": "failed"})
        else:
            _set_reply_task(db, failed.id, {"status": "done"})
            insert_error_action(db, {"conversation_id":"unrelated-chat", "message_id":"message",
                                     "kind":"read", "detail":"failed"})
        _schedule(db, 1)
        snapshot_id = intern_scheduled_config(db, "{}")
        for index in range(20000):
            _insert_occurrence(
                db, occurrence_id=index + 1, event_id=f"event-{index}", scheduled_task_id=1,
                snapshot_id=snapshot_id, execution_kind="reply_task",
                execution_id=str(completed.id),
            )
    steps = 0

    def progress():
        nonlocal steps
        steps += 1000
        return int(steps >= 400000)

    with store.read_snapshot(), store._connect() as db:
        db.set_progress_handler(progress, 1000)
        try:
            rows = _queue_attention_rows(store)
        finally:
            db.set_progress_handler(None, 0)
    assert len(rows) == 1
    assert rows[0]["category"] == (
        "Reply task" if source == "reply" else "Service error"
    )
    # Bound actual VM work, not wall time or a planner's display text.
    assert steps < 400000, steps


@pytest.mark.parametrize("source", ["reply", "error"])
@pytest.mark.parametrize(
    ("schedule_id", "later_id", "status", "dispatch", "kind", "reply_superseded", "error_superseded"),
    [
        (1, 11, "done", "dispatched", "reply_task", True, True),
        (1, 11, "skipped", "dispatched", "reply_task", True, True),
        (2, 11, "done", "dispatched", "reply_task", False, False),
        (1, 9, "done", "dispatched", "reply_task", False, False),
        (1, 11, "failed", "dispatched", "reply_task", True, False),
        (1, 11, "done", "failed", "reply_task", False, False),
        (1, 11, "done", "dispatched", "service_command", True, False),
    ],
)
def test_attention_recovery_keeps_same_schedule_terminal_evidence(
    tmp_path, source, schedule_id, later_id, status, dispatch, kind,
    reply_superseded, error_superseded,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    tasks = [
        store.ensure_reply_task(
            conversation_id=f"scheduled-task-run:{run_id}",
            conversation_title="Report",
            single_chat=False,
            trigger_message_id=f"event-{run_id}",
            trigger_create_time="2026-10-07 10:00:00",
            trigger_sender="scheduler",
            trigger_text="report",
            channel="scheduled",
        )
        for run_id in (10, later_id)
    ]
    with store._connect() as db:
        _set_reply_task(db, tasks[0].id, {"status": "failed" if source == "reply" else "done"})
        _set_reply_task(db, tasks[1].id, {"status": status})
        if source == "error":
            insert_error_action(db, {"conversation_id":"scheduled-task-run:10", "message_id":"event-10",
                                     "kind":"read", "detail":"failed"})
        _schedule(db, 1)
        _schedule(db, 2)
        snapshot_id = intern_scheduled_config(db, "{}")
        for run_id, event_id, scheduled_id, dispatch_status, execution_kind, execution_id in (
            (10, 'event-10', 1, 'dispatched', 'reply_task', str(tasks[0].id)),
            (later_id, f'event-{later_id}', schedule_id, dispatch, kind, str(tasks[1].id)),
        ):
            _insert_occurrence(
                db, occurrence_id=run_id, event_id=event_id, scheduled_task_id=scheduled_id,
                snapshot_id=snapshot_id, execution_kind=execution_kind,
                execution_id=execution_id, dispatch_status=dispatch_status,
            )
    rows = _queue_attention_rows(store)
    category = "Reply task" if source == "reply" else "Service error"
    matches = [
        row
        for row in rows
        if row["category"] == category
        and (row["id"] == str(tasks[0].id) if source == "reply" else row["id"] == "1")
    ]
    superseded = reply_superseded if source == "reply" else error_superseded
    assert bool(matches) is not superseded
    if source == "reply" and status == "failed" and dispatch == "dispatched":
        assert any(
            row["category"] == "Reply task" and row["id"] == str(tasks[1].id)
            for row in rows
        )
    with store._connect() as db:
        assert get_task(db, tasks[0].id)["status"] == (
            "failed" if source == "reply" else "done"
        )
        if source == "error":
            assert db.execute(f"select resolved_at from ({ERROR_SQL})").fetchone()[0] == ""
