import pytest

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore


def _attention_error_query(store, monkeypatch):
    statements = []
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        _queue_attention_rows(store)
    return next(sql for sql in statements if "from errors error_event" in sql)


def test_attention_reads_only_the_unresolved_error_index(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    query = _attention_error_query(store, monkeypatch)
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert any(
        "error_event USING INDEX idx_errors_unresolved" in step for step in plan
    ), plan


def test_resolved_error_history_does_not_consume_attention_query_budget(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        db.executemany(
            "insert into errors (conversation_id,message_id,kind,detail,resolved_at) "
            "values (?,?,'test','historical resolved incident','2026-10-08 00:00:00')",
            [(f"cid-{i}", f"msg-{i}") for i in range(5000)],
        )
    query = _attention_error_query(store, monkeypatch)
    steps = [0]

    def budget():
        steps[0] += 100
        return steps[0] > 2000

    with store._connect() as db:
        db.set_progress_handler(budget, 100)
        try:
            assert db.execute(query).fetchall() == []
        finally:
            db.set_progress_handler(None, 0)
        assert db.execute("select count(*) from errors").fetchone()[0] == 5000


def test_unresolved_error_index_tracks_new_and_resolved_incidents(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid", "msg", "test", "current incident")
    with store.read_snapshot():
        rows = _queue_attention_rows(store)
    assert len(rows) == 1
    error_id = int(rows[0]["id"])
    assert store.resolve_errors([error_id], resolution="verified recovery") == 1
    with store.read_snapshot():
        assert _queue_attention_rows(store) == []
    assert len(store.list_errors()) == 1


def test_attention_locates_scheduled_runs_without_scanning_unrelated_history(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.create_scheduled_task(
        name="History",
        command="scan-meetings-once",
        cron_expression="* * * * *",
        timezone_name="UTC",
    )
    with store._connect() as db:
        from app.scheduled_config_storage import intern_scheduled_config

        snapshot_id = intern_scheduled_config(db, "{}")
        db.executemany(
            "insert into scheduled_task_runs "
            "(event_id,scheduled_task_id,trigger_kind,scheduled_for,first_scheduled_for,dispatch_status,snapshot_id) "
            "values (?,?,'manual','2026-10-08T00:00:00Z','2026-10-08T00:00:00Z','failed',?)",
            [(f"unrelated-event-{i}", task.id, snapshot_id) for i in range(5000)],
        )
    store.record_error(
        "scheduled-task-run:999999", "missing-event", "test", "not recovered"
    )
    query = _attention_error_query(store, monkeypatch)
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
    assert not any(step.startswith("SCAN stale_run") for step in plan), plan


@pytest.mark.parametrize(
    ("conversation_id", "message_id", "covered"),
    [
        ("scheduled-task-run:10", None, True),
        ("scheduled-task-run:010", "missing-event", False),
        ("scheduled-task-run:+10", "missing-event", False),
        ("scheduled-task-run:10suffix", "missing-event", False),
        ("unrelated", "event-old", True),
        ("scheduled-task-run:010", "event-old", True),
    ],
)
def test_scheduled_error_locator_preserves_exact_identity_and_event_route(
    tmp_path,
    conversation_id,
    message_id,
    covered,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    schedule = store.create_scheduled_task(
        name="Report",
        command="scan-meetings-once",
        cron_expression="* * * * *",
        timezone_name="UTC",
    )
    reply = store.ensure_reply_task(
        conversation_id="scheduled-task-run:11",
        conversation_title="Report",
        single_chat=False,
        trigger_message_id="event-new",
        trigger_create_time="2026-10-08T00:00:00Z",
        trigger_sender="scheduler",
        trigger_text="Report",
        channel="scheduled",
    )
    with store._connect() as db:
        from app.scheduled_config_storage import intern_scheduled_config

        snapshot_id = intern_scheduled_config(db, "{}")
        db.execute("update reply_tasks set status='done' where id=?", (reply.id,))
        db.executemany(
            "insert into scheduled_task_runs "
            "(id,event_id,scheduled_task_id,trigger_kind,scheduled_for,first_scheduled_for,dispatch_status,snapshot_id,execution_kind,execution_id) "
            "values (?,?,?,'manual','2026-10-08T00:00:00Z','2026-10-08T00:00:00Z',?,?,?,?)",
            [
                (10, "event-old", schedule.id, "failed", snapshot_id, "", ""),
                (
                    11,
                    "event-new",
                    schedule.id,
                    "dispatched",
                    snapshot_id,
                    "reply_task",
                    str(reply.id),
                ),
            ],
        )
        db.execute(
            "insert into errors(conversation_id,message_id,kind,detail) values (?,?,'read','failed')",
            (conversation_id, message_id),
        )
    rows = _queue_attention_rows(store)
    assert (
        bool([row for row in rows if row["category"] == "Service error"]) is not covered
    )
    with store._connect() as db:
        assert db.execute("select resolved_at from errors").fetchone()[0] == ""
