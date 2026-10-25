"""Runtime reads and transitions use typed storage with original public IDs."""

import sqlite3

import pytest

from app.store import AgentRunLeaseLostError, AutoReplyStore
from app.task_storage import RETIRED_TASK_TABLES
@pytest.fixture
def typed_runtime(tmp_path, monkeypatch):
    path = tmp_path / "runtime.db"

    # Initialize the current declaration before installing the runtime reader
    # fence. work_summary_inputs remains a narrow native domain owner; its
    # identity metadata is readable, while the captured body stays excluded.
    store = AutoReplyStore(path)
    retired_accesses = []
    body_accesses = []
    check_body_reads = [False]
    retired_runtime_tables = set(RETIRED_TASK_TABLES) - {"work_summary_inputs"}
    original_open = AutoReplyStore._open_connection

    def observe(action, table, column, *_):
        if action in {sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE}:
            if table in retired_runtime_tables:
                retired_accesses.append((action, table, column))
                return sqlite3.SQLITE_DENY
            if (check_body_reads[0] and action == sqlite3.SQLITE_READ
                    and table == "work_summary_inputs" and column == "payload_json"):
                body_accesses.append((action, table, column))
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def monitored_open(self, *args, **kwargs):
        db = original_open(self, *args, **kwargs)
        db.set_authorizer(observe)
        return db

    monkeypatch.setattr(AutoReplyStore, "_open_connection", monitored_open)
    with store._connect() as db:
        names = {row[0] for row in db.execute("select name from sqlite_master")}
        assert not names.intersection(retired_runtime_tables)
        assert "work_summary_inputs" in names
    yield store, "fresh", check_body_reads
    assert retired_accesses == []
    assert body_accesses == []


def _reply(store, suffix="one"):
    return store.ensure_reply_task(
        conversation_id="typed-readers",
        conversation_title="Typed readers",
        single_chat=False,
        trigger_message_id=suffix,
        trigger_create_time="2026-10-11 12:00:00",
        trigger_sender="source",
        trigger_text="Exact queued input " + suffix,
    )


def _pre_run_failure(store, task, *, generation=None):
    return store.finalize_reply_task_without_run(
        task_id=task.id,
        expected_execution_generation=generation or task.execution_generation,
        task_status="failed",
        task_error="source_unavailable",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="Source read did not finish",
        audit_summary="",
        send_status="failed",
        send_error="source_unavailable",
        channel=task.channel,
    )


def test_history_and_work_input_recovery_use_canonical_task_ids(typed_runtime):
    store, _, check_body_reads = typed_runtime
    input_id = store.enqueue_work_summary_input("local_file", "reader", '{ "body": 1 }')
    store.record_error("work_summary_input", str(input_id), "task_agent", "retry")
    store.mark_work_summary_input_done(input_id)
    assert store.resolve_errors_recovered_by_terminal_work_summary_inputs() == 1
    assert store.list_errors()[0].resolved_at
    assert store.get_work_summary_input(input_id).body_compacted == 1
    check_body_reads[0] = True
    try:
        history = store.list_operation_logs(source_tables=("work_summary_inputs",))
    finally:
        check_body_reads[0] = False
    assert input_id in [item.source_id for item in history]


def test_pre_run_failure_retry_and_settlement_keep_generation_and_history(typed_runtime):
    store, _, _ = typed_runtime
    task = store.claim_reply_task(_reply(store).id)
    attempt_id = _pre_run_failure(store, task)
    assert store.count_current_unresolved_problem_attempts() == 1
    assert [item.id for item in store.list_current_unresolved_problem_attempts()] == [attempt_id]
    assert store.count_reply_tasks(status="failed") == 1
    retried = store.retry_failed_pre_agent_reply_task(task.id, reason="source available")
    assert retried.status == "pending"
    assert retried.attempts == 0
    assert retried.execution_generation == task.execution_generation
    assert store.count_current_unresolved_problem_attempts() == 0
    claimed = store.claim_reply_task(task.id)
    assert _pre_run_failure(store, claimed) == attempt_id
    assert store.close_unresolved_reply_task_already_settled(
        task.id,
        expected_execution_generation=task.execution_generation,
        settled_evidence="source receipt confirms completion",
    )
    assert store.reply_task_is_done(task.id)
    settled = store.get_reply_task(task.id)
    assert settled.input_compacted == 1
    assert settled.trigger_text == ""
    assert not store.close_unresolved_reply_task_already_settled(
        task.id,
        expected_execution_generation=task.execution_generation,
        settled_evidence="same source receipt",
    )
    history = store.list_operation_logs(source_tables=("reply_attempts",))
    assert [item.source_id for item in history] == [attempt_id]
    assert history[0].status == "recovered"


def test_stale_pre_run_finalization_rolls_back_attempt_write(typed_runtime):
    store, _, _ = typed_runtime
    task = store.claim_reply_task(_reply(store, "stale").id)
    next_generation = store.rotate_reply_task_execution_generation(task.id)
    current = store.claim_reply_task(task.id)
    assert current.execution_generation == next_generation
    with pytest.raises(AgentRunLeaseLostError):
        _pre_run_failure(store, task)
    assert store.list_current_unresolved_problem_attempts() == []
    assert store.get_reply_task(task.id).status == "processing"
    _pre_run_failure(store, current)
    assert store.count_current_unresolved_problem_attempts() == 1
