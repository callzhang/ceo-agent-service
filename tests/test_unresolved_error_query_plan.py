import pytest

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore
from app.action_storage import insert_error_action
from app.action_error_definitions import error_projection
from tests.support.native_run_rows import insert_scheduled_occurrence


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
    return next(sql for sql in statements if "record_type='errors'" in sql
                and "newer_error" in sql)


def test_attention_reads_only_the_unresolved_error_index(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    query = _attention_error_query(store, monkeypatch)
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert any(
        "USING INDEX idx_actions_errors_unresolved" in step for step in plan
    ), plan


def test_resolved_error_history_does_not_consume_attention_query_budget(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._immediate_write_transaction() as db:
        for i in range(5000):
            insert_error_action(db, {
                "conversation_id": f"cid-{i}", "message_id": f"msg-{i}",
                "kind": "test", "detail": "historical resolved incident",
                "created_at": "2026-10-08 00:00:00",
                "resolved_at": "2026-10-08 00:00:00", "resolution": "",
            })
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
        assert db.execute(f"select count(*) from ({error_projection()})").fetchone()[0] == 5000


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
    with store._immediate_write_transaction() as db:
        from app.scheduled_config_storage import intern_scheduled_config

        snapshot_id = intern_scheduled_config(db, "{}")
        for i in range(5000):
            insert_scheduled_occurrence(db, {
                'event_id': f"unrelated-event-{i}",
                'scheduled_task_id': task.id,
                'trigger_kind': 'manual',
                'scheduled_for': '2026-10-08T00:00:00Z',
                'first_scheduled_for': '2026-10-08T00:00:00Z',
                'dispatch_status': 'failed',
                'snapshot_id': snapshot_id,
            })
    # An error bound to an existing run that has no later dispatched run stays visible.
    store.record_error(
        "scheduled-task-run:4999", "missing-event", "test", "not recovered"
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
        # Run 10 failed, but run 11 of the same schedule was dispatched and is done.
        ("scheduled-task-run:10", None, True),
        # Run 11 is the latest dispatched run: its read error is still current.
        ("scheduled-task-run:11", "missing-event", False),
        # A run identity that is not exactly an existing run id is an orphan (History only).
        ("scheduled-task-run:011", "missing-event", True),
        ("scheduled-task-run:010", "missing-event", True),
        ("scheduled-task-run:+10", "missing-event", True),
        ("scheduled-task-run:10suffix", "missing-event", True),
        ("scheduled-task-run:100", "missing-event", True),
        ("scheduled-task-run:99", "missing-event", True),
        ("scheduled-task-run:999", "missing-event", True),
        # The event route: an event id of the superseded run 10 is covered.
        ("unrelated", "event-old", True),
        ("scheduled-task-run:010", "event-old", True),
        ("scheduled-task-run:10", "event-old", True),
        ("unrelated", "missing-event", False),
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
    with store._immediate_write_transaction() as db:
        from app.scheduled_config_storage import intern_scheduled_config

        snapshot_id = intern_scheduled_config(db, "{}")
        db.execute("update tasks set status='done' where id=?", (reply.id,))
        for run_id, event_id, dispatch, kind, execution in (
            (10, 'event-old', 'failed', '', ''),
            (11, 'event-new', 'dispatched', 'reply_task', str(reply.id)),
        ):
            insert_scheduled_occurrence(db, {
                'id': run_id,
                'event_id': event_id,
                'scheduled_task_id': schedule.id,
                'trigger_kind': 'manual',
                'scheduled_for': '2026-10-08T00:00:00Z',
                'first_scheduled_for': '2026-10-08T00:00:00Z',
                'dispatch_status': dispatch,
                'snapshot_id': snapshot_id,
                'execution_kind': kind,
                'execution_id': execution,
            })
    store.record_error(conversation_id, message_id, "read", "failed")
    errors = f"select * from ({error_projection()}) order by id"
    with store._connect() as db:
        before = {
            "errors": [tuple(row) for row in db.execute(errors)],
            **{
                table: [tuple(row) for row in db.execute(f'select * from {table} order by id')]
                for table in ('actions', 'tasks', 'runs', 'schedule_occurrences')
            },
        }
    rows = _queue_attention_rows(store)
    assert (
        bool([row for row in rows if row["category"] == "Service error"]) is not covered
    )
    with store._connect() as db:
        assert db.execute(
            f"select resolved_at from ({error_projection()})"
        ).fetchone()[0] == ""
        assert {
            "errors": [tuple(row) for row in db.execute(errors)],
            **{
                table: [tuple(row) for row in db.execute(f'select * from {table} order by id')]
                for table in ('actions', 'tasks', 'runs', 'schedule_occurrences')
            },
        } == before
