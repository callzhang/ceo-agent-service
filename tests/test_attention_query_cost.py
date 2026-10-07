from pathlib import Path

import pytest

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore


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
        db.execute("update reply_tasks set status='done' where id=?", (completed.id,))
        if source == "reply":
            db.execute(
                "update reply_tasks set status='failed', error='failed' where id=?",
                (failed.id,),
            )
        else:
            db.execute("update reply_tasks set status='done' where id=?", (failed.id,))
            db.execute(
                "insert into errors(conversation_id,message_id,kind,detail) values ('unrelated-chat','message','read','failed')"
            )
        db.execute(
            "insert into scheduled_tasks(id,name,prompt,cron_expression,timezone,runtime_id,enabled) values (1,'Report','p','0 * * * *','UTC','',1)"
        )
        db.executemany(
            "insert into scheduled_task_runs(event_id,scheduled_task_id,trigger_kind,scheduled_for,first_scheduled_for,dispatch_status,snapshot_json,execution_kind,execution_id) values (?,1,'manual','2026-10-07T10:00:00Z','2026-10-07T10:00:00Z','dispatched','{}','reply_task',?)",
            [(f"event-{index}", str(completed.id)) for index in range(20000)],
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
        db.execute(
            "update reply_tasks set status=? where id=?",
            ("failed" if source == "reply" else "done", tasks[0].id),
        )
        db.execute("update reply_tasks set status=? where id=?", (status, tasks[1].id))
        if source == "error":
            db.execute(
                "insert into errors(conversation_id,message_id,kind,detail) values ('scheduled-task-run:10','event-10','read','failed')"
            )
        db.executemany(
            "insert into scheduled_tasks(id,name,prompt,cron_expression,timezone,runtime_id,enabled) values (?,'Report','p','0 * * * *','UTC','',1)",
            [(1,), (2,)],
        )
        db.executemany(
            "insert into scheduled_task_runs(id,event_id,scheduled_task_id,trigger_kind,scheduled_for,first_scheduled_for,dispatch_status,snapshot_json,execution_kind,execution_id) values (?, ?, ?, 'manual','2026-10-07T10:00:00Z','2026-10-07T10:00:00Z',?,'{}',?,?)",
            [
                (10, "event-10", 1, "dispatched", "reply_task", str(tasks[0].id)),
                (
                    later_id,
                    f"event-{later_id}",
                    schedule_id,
                    dispatch,
                    kind,
                    str(tasks[1].id),
                ),
            ],
        )
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
            "select status from reply_tasks where id=?", (tasks[0].id,)
        ).fetchone()[0] == ("failed" if source == "reply" else "done")
        if source == "error":
            assert db.execute("select resolved_at from errors").fetchone()[0] == ""
