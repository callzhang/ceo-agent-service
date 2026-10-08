import pytest

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore


def test_reply_recovery_does_not_scan_other_execution_links(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    failed = store.ensure_reply_task(
        conversation_id="chat",
        conversation_title="Chat",
        single_chat=True,
        trigger_message_id="request",
        trigger_create_time="2026-10-08T00:00:00Z",
        trigger_sender="Sender",
        trigger_text="Request",
    )
    other = store.ensure_reply_task(
        conversation_id="other",
        conversation_title="Other",
        single_chat=True,
        trigger_message_id="other-request",
        trigger_create_time="2026-10-08T00:00:00Z",
        trigger_sender="Sender",
        trigger_text="Other request",
    )
    schedule = store.create_scheduled_task(
        name="Report",
        command="scan-meetings-once",
        cron_expression="* * * * *",
        timezone_name="UTC",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set status='failed',error='failed' where id=?",
            (failed.id,),
        )
        db.execute("update reply_tasks set status='done' where id=?", (other.id,))
        db.executemany(
            "insert into scheduled_task_runs "
            "(event_id,scheduled_task_id,trigger_kind,scheduled_for,first_scheduled_for,dispatch_status,snapshot_json,execution_kind,execution_id) "
            "values (?,?,'manual','2026-10-08T00:00:00Z','2026-10-08T00:00:00Z','dispatched','{}','reply_task',?)",
            [(f"event-{i}", schedule.id, str(other.id)) for i in range(5000)],
        )
    statements = []
    original_open = store._open_connection

    def traced():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced)
    _queue_attention_rows(store)
    query = next(
        sql for sql in statements if "select reply_tasks.id, reply_tasks.channel" in sql
    )
    steps = [0]

    def budget():
        steps[0] += 100
        return steps[0] > 2000

    with store._connect() as db:
        db.set_progress_handler(budget, 100)
        try:
            assert len(db.execute(query).fetchall()) == 1
        finally:
            db.set_progress_handler(None, 0)
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert any(
        "stale_run USING INDEX idx_scheduled_task_runs_reply_execution" in step
        for step in plan
    ), plan


@pytest.mark.parametrize("execution_id", ["1", "001", " 1 ", "1suffix"])
def test_execution_expression_index_keeps_existing_integer_cast_semantics(
    tmp_path, execution_id
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    schedule = store.create_scheduled_task(
        name="Report",
        command="scan-meetings-once",
        cron_expression="* * * * *",
        timezone_name="UTC",
    )
    with store._connect() as db:
        db.execute(
            "insert into scheduled_task_runs "
            "(event_id,scheduled_task_id,trigger_kind,scheduled_for,first_scheduled_for,dispatch_status,snapshot_json,execution_kind,execution_id) "
            "values ('event',?,'manual','2026-10-08T00:00:00Z','2026-10-08T00:00:00Z','dispatched','{}','reply_task',?)",
            (schedule.id, execution_id),
        )
        assert (
            db.execute(
                "select count(*) from scheduled_task_runs "
                "where execution_kind='reply_task' and cast(execution_id as integer)=1"
            ).fetchone()[0]
            == 1
        )
