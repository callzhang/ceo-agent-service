"""Source-field ownership, without provider or runtime effects."""
from contextlib import closing
import sqlite3

from app.task_lifecycle_current_schema import create_current_task20_schema
from app.task8_storage import register_task8_functions, task8_insert_parts
import pytest


def test_executable_lifecycle_has_one_physical_owner():
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        create_current_task20_schema(db)
        physical = {row[1] for row in db.execute("pragma table_xinfo(tasks)") if not row[6]}
        expected = {
            "status", "available_at", "attempts", "error", "created_at", "updated_at",
            "locked_at", "lease_owner", "lease_expires_at", "owner_pid", "generation",
            "adapter_name",
        }
        assert expected <= physical
        parts = task8_insert_parts("work_summary_inputs", {
            "source_type": "report", "source_ref": "source:1", "payload_json": "{}",
        })
        db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
        row = db.execute(
            "select lifecycle_status,lifecycle_attempts,lifecycle_available_at,"
            "lifecycle_locked_at,lifecycle_lease_owner,lifecycle_generation "
            "from tasks"
        ).fetchone()
        assert row == ("pending", 0, "", None, None, None)


@pytest.mark.parametrize("kind,source_status,generic", [
    ("meeting_alignment_jobs", "ready_to_send", "processing"),
    ("meeting_alignment_jobs", "quarantined", "quarantined"),
    ("weekly_okr_analysis_jobs", "running", "processing"),
    ("wechat_memory_import_jobs", "completed", "done"),
    ("feedback_processing_items", "resolved", "done"),
    ("email_agent_classification_tasks", "running", "processing"),
])
def test_generic_status_has_only_one_source_status_owner(kind, source_status, generic):
    from tests.test_task20_definitions import payload
    from app.task12_storage import insert_task12
    from app.task20_definitions import task20_column, TASK20_NEW_KINDS
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        create_current_task20_schema(db)
        values = payload(db, kind, status=source_status)
        if kind in TASK20_NEW_KINDS:
            insert_task12(db, kind, values)
        else:
            parts = task8_insert_parts(kind, values)
            db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
        row = db.execute("select lifecycle_status," + task20_column(kind, "status") + " from tasks").fetchone()
        assert row == (generic, source_status)


def test_dispatch_claim_fences_and_renewal_use_task_owner(tmp_path):
    from datetime import datetime, timedelta, timezone
    from app.store import AutoReplyStore
    from app.dispatcher.adapters import ReplyQueueAdapter
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.enqueue_reply_task(conversation_id="chat", conversation_title="Chat", single_chat=True,
        trigger_message_id="trigger", trigger_create_time="time", trigger_sender="sender",
        trigger_text="source")
    adapter = ReplyQueueAdapter(store, owner_alive=lambda _: False)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    first = adapter.claim(now, owner="first", owner_pid=17, lease=timedelta(minutes=5))
    assert first is not None
    with store._immediate_write_transaction() as db:
        assert db.execute("select lease_owner,generation from tasks "
                          "where kind='reply'").fetchone()[:] == ("first", first.generation)
        db.execute("update tasks set lease_owner='new-owner' where kind='reply'")
    # A stale passive ledger cannot authorize a claim whose Task owner changed.
    with pytest.raises(ValueError, match="no longer owned"):
        adapter.assert_current(first, owner="first", now=now + timedelta(seconds=1))
    with store._immediate_write_transaction() as db:
        db.execute("update tasks set lease_owner='first' where kind='reply'")
    adapter.renew(first, owner="first", now=now + timedelta(seconds=1), lease=timedelta(minutes=5))
    adapter.assert_current(first, owner="first", now=now + timedelta(seconds=2))
    adapter.release(first, owner="first", now=now + timedelta(seconds=3))
    assert store.get_reply_task(int(first.source_id)).attempts == 0
    with pytest.raises(ValueError, match="no longer owned"):
        adapter.renew(first, owner="first", now=now + timedelta(seconds=4), lease=timedelta(minutes=5))


@pytest.mark.parametrize("adapter_name", ["ReplyQueueAdapter", "ScheduledExecutionQueueAdapter",
                                         "MeetingQueueAdapter", "WorkSummaryQueueAdapter", "OkrReviewQueueAdapter"])
def test_due_claim_and_metrics_seek_kind_status_without_materialized_history(tmp_path, monkeypatch, adapter_name):
    from datetime import datetime, timezone, timedelta
    from app.store import AutoReplyStore
    from app.dispatcher import adapters
    store = AutoReplyStore(tmp_path / "plans.sqlite3")
    statements = []
    original_open = store._open_connection

    def traced_open(*args, **kwargs):
        connection = original_open(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_open_connection", traced_open)
    adapter = getattr(adapters, adapter_name)(store, owner_alive=lambda _: False)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert adapter.claim(now, owner="fixture", owner_pid=7, lease=timedelta(minutes=5)) is None
    assert adapter.metrics(now).due == 0
    queries = [sql for sql in statements if sql.lstrip().startswith("select") and "task_status" in sql]
    assert len(queries) == 6
    with store._connect() as db:
        plans = [[row[3] for row in db.execute("explain query plan " + sql)] for sql in queries]
    for plan in plans:
        assert any(
            step.startswith("SEARCH task USING") and "INDEX idx_tasks_claim_due (kind=? AND status=?)" in step
            for step in plan
        ), plan
        assert not any("MATERIALIZE" in step or "SCAN task" in step for step in plan), plan


def test_history_pruning_cannot_clear_live_newer_or_other_adapter_claim(tmp_path):
    from app.store import AutoReplyStore
    from app.dispatcher.adapters import ReplyQueueAdapter
    from app.task_lifecycle_storage import forget_pruned_claims
    from datetime import datetime, timezone, timedelta
    store = AutoReplyStore(tmp_path / "retention.sqlite3")
    store.enqueue_reply_task(conversation_id="chat", conversation_title="Chat", single_chat=True,
        trigger_message_id="message", trigger_create_time="clock", trigger_sender="sender", trigger_text="text")
    adapter = ReplyQueueAdapter(store)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    first = adapter.claim(now, owner="live", owner_pid=7, lease=timedelta(minutes=5))
    with store._immediate_write_transaction() as db:
        before = db.execute("select adapter_name,lease_owner,owner_pid,generation,lease_expires_at from tasks "
                            "where kind='reply'").fetchone()[:]
        forget_pruned_claims(db, [("reply", first.source_id, first.generation)])
        assert db.execute("select adapter_name,lease_owner,owner_pid,generation,lease_expires_at from tasks "
                          "where kind='reply'").fetchone()[:] == before
    adapter.release(first, owner="live", now=now)
    with store._immediate_write_transaction() as db:
        forget_pruned_claims(db, [("reply", first.source_id, first.generation - 1),
                                 ("scheduled_execution", first.source_id, first.generation)])
        assert db.execute("select generation from tasks where kind='reply'").fetchone()[0] == first.generation


def test_task_domain_and_claim_commit_together_with_passive_history(tmp_path, monkeypatch):
    from app.store import AutoReplyStore
    from app.dispatcher.adapters import ReplyQueueAdapter
    from app import task_lifecycle_storage as writer
    from datetime import datetime, timezone, timedelta
    store = AutoReplyStore(tmp_path / "atomic.sqlite3")
    store.enqueue_reply_task(conversation_id="atomic", conversation_title="Atomic", single_chat=True,
        trigger_message_id="atomic", trigger_create_time="clock", trigger_sender="sender", trigger_text="text")
    query = ("select status,attempts,lease_owner,generation "
             "from tasks where kind='reply'")
    original = writer._write_claim_history

    def fail_after_task_cas(db, *args, **kwargs):
        assert db.execute(query).fetchone()[:] == ("processing", 1, "worker", 1)
        with store._connect() as reader:
            assert reader.execute(query).fetchone()[:] == ("pending", 0, None, None)
        raise RuntimeError("injected passive history write failure")

    monkeypatch.setattr(writer, "_write_claim_history", fail_after_task_cas)
    adapter = ReplyQueueAdapter(store)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="passive history"):
        adapter.claim(now, owner="worker", owner_pid=7, lease=timedelta(minutes=5))
    with store._connect() as db:
        assert db.execute(query).fetchone()[:] == ("pending", 0, None, None)
        assert db.execute("select count(*) from state where record_type='dispatcher_claim_leases'").fetchone()[0] == 0
    monkeypatch.setattr(writer, "_write_claim_history", original)
    assert adapter.claim(now, owner="worker", owner_pid=7, lease=timedelta(minutes=5)).generation == 1
