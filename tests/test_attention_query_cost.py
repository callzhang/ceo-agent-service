from pathlib import Path

import pytest
from app.store import REPLY_TASK_SQL

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore, ERROR_SQL
from tests.support.native_run_rows import insert_scheduled_occurrence
from tests.support.native_task_fields import set_native_task_fields
from app.schedules_storage import insert_schedule
from app.scheduled_config_storage import intern_scheduled_config
from app.action_storage import insert_error_action


def _schedule(db, identity):
    assert insert_schedule(db, dict(id=identity, name="Report", prompt="p",
        cron_expression="0 * * * *", timezone="UTC", runtime_id="", enabled=1)) == identity


def _set_reply_task(db, public_id, values):
    set_native_task_fields(db, "reply_tasks", public_id, values)


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
            insert_scheduled_occurrence(db, {
                'event_id': f"event-{index}",
                'scheduled_task_id': 1,
                'trigger_kind': 'manual',
                'scheduled_for': '2026-10-07T10:00:00Z',
                'first_scheduled_for': '2026-10-07T10:00:00Z',
                'dispatch_status': 'dispatched',
                'snapshot_id': snapshot_id,
                'execution_kind': 'reply_task',
                'execution_id': str(completed.id),
            })
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
    ("schedule_id", "later_id", "status", "dispatch", "kind", "covered"),
    [
        (1, 11, "done", "dispatched", "reply_task", True),
        (1, 11, "skipped", "dispatched", "reply_task", True),
        (2, 11, "done", "dispatched", "reply_task", False),
        (1, 9, "done", "dispatched", "reply_task", False),
        (1, 11, "failed", "dispatched", "reply_task", False),
        (1, 11, "done", "failed", "reply_task", False),
        (1, 11, "done", "dispatched", "service_command", False),
    ],
)
def test_attention_recovery_keeps_same_schedule_terminal_evidence(
    tmp_path, source, schedule_id, later_id, status, dispatch, kind, covered
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
            insert_scheduled_occurrence(db, {
                'id': run_id,
                'event_id': event_id,
                'scheduled_task_id': scheduled_id,
                'trigger_kind': 'manual',
                'scheduled_for': '2026-10-07T10:00:00Z',
                'first_scheduled_for': '2026-10-07T10:00:00Z',
                'dispatch_status': dispatch_status,
                'snapshot_id': snapshot_id,
                'execution_kind': execution_kind,
                'execution_id': execution_id,
            })
    rows = _queue_attention_rows(store)
    category = "Reply task" if source == "reply" else "Service error"
    matches = [
        row
        for row in rows
        if row["category"] == category
        and (row["id"] == str(tasks[0].id) if source == "reply" else row["id"] == "1")
    ]
    assert bool(matches) is not covered
    with store._connect() as db:
        assert db.execute(
            f"select status from ({REPLY_TASK_SQL}) where id=?", (tasks[0].id,)
        ).fetchone()[0] == ("failed" if source == "reply" else "done")
        if source == "error":
            assert db.execute(f"select resolved_at from ({ERROR_SQL})").fetchone()[0] == ""
