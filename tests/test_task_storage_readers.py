"""Runtime reads and transitions use typed storage with original public IDs."""

from contextlib import closing
import sqlite3

import pytest

from app.store import AgentRunLeaseLostError, AutoReplyStore
from app.task_storage import RETIRED_TASK_TABLES, migrate_task_schema
from tests.test_task_storage import source_database
from tests.workbench_caller_fixtures import empty_source13, advance_source16_to_store17


def original_rows_source13(path, source_factory=source_database):
    """Keep every original business row on independently captured source13 DDL."""
    with closing(source_factory(path.with_suffix(".original.sqlite3"))) as original:
        original.execute("begin immediate")
        migrate_task_schema(original)
        original.commit()
        with empty_source13(path) as db:
            for table, sql in original.execute(
                "select name,sql from sqlite_master where type='table' "
                "and name not like 'sqlite_%' and name<>'service_state'"
            ).fetchall():
                if not db.execute("select 1 from sqlite_master where name=?", (table,)).fetchone():
                    db.execute(sql)
                    for (object_sql,) in original.execute(
                        "select sql from sqlite_master where tbl_name=? "
                        "and type<>'table' and sql is not null", (table,),
                    ):
                        db.execute(object_sql)
                db.execute(f'delete from "{table}"')
                columns = [row[1] for row in original.execute(f'pragma table_info("{table}")')]
                rows = original.execute(f'select * from "{table}"').fetchall()
                column_sql = ','.join(f'"{column}"' for column in columns)
                if rows:
                    placeholders = ','.join('?' for _ in columns)
                    db.executemany(f'insert into "{table}" ({column_sql}) values ({placeholders})', rows)
                assert db.execute(f'select {column_sql} from "{table}" order by rowid').fetchall() == original.execute(
                    f'select {column_sql} from "{table}" order by rowid'
                ).fetchall()
            for name, seq in original.execute("select name,seq from sqlite_sequence"):
                db.execute("update sqlite_sequence set seq=? where name=?", (seq, name))


@pytest.fixture(params=("fresh", "migrated"))
def typed_runtime(request, tmp_path, monkeypatch):
    path = tmp_path / "runtime.db"
    if request.param == "migrated":
        original_rows_source13(path)
        # Preserve this reader's original local-file recovery input on source13.
        with sqlite3.connect(path) as db:
            db.execute("update tasks set source_type='local_file' "
                       "where record_type='work_summary_inputs' and legacy_key='2'")
        advance_source16_to_store17(path)
        AutoReplyStore(path)  # Publish the complete source before observing typed readers.

    accesses = set()
    retired_accesses = []
    original_open = AutoReplyStore._open_connection

    def observe(action, table, column, *_):
        if action in {sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE}:
            accesses.add(table)
            if table in RETIRED_TASK_TABLES:
                retired_accesses.append((action, table, column))
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def monitored_open(self, *args, **kwargs):
        db = original_open(self, *args, **kwargs)
        db.set_authorizer(observe)
        return db

    monkeypatch.setattr(AutoReplyStore, "_open_connection", monitored_open)
    store = AutoReplyStore(path)
    with store._connect() as db:
        names = {row[0] for row in db.execute("select name from sqlite_master")}
        assert not names.intersection(RETIRED_TASK_TABLES)
    yield store, request.param
    assert "tasks" in accesses
    assert retired_accesses == []


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


def test_history_and_work_input_recovery_use_original_public_ids(typed_runtime):
    store, layout = typed_runtime
    if layout == "migrated":
        input_id = 2
        with store._connect() as db:
            physical_id = db.execute(
                "select id from tasks where record_type='work_summary_inputs' and legacy_key='2'"
            ).fetchone()[0]
        assert physical_id != input_id
    else:
        input_id = store.enqueue_work_summary_input("local_file", "reader", '{ "body": 1 }')
    store.record_error("work_summary_input", str(input_id), "task_agent", "retry")
    store.mark_work_summary_input_done(input_id)
    assert store.resolve_errors_recovered_by_terminal_work_summary_inputs() == 1
    assert store.list_errors()[0].resolved_at
    assert store.get_work_summary_input(input_id).body_compacted == 1
    history = store.list_operation_logs(source_tables=("work_summary_inputs",))
    assert input_id in [item.source_id for item in history]
    if layout == "migrated":
        assert {item.source_id for item in history} == {1, 2}
        with store._connect() as db:
            assert db.execute("select hex(body) from receipt where id=1").fetchone()[0] == "FF00"
            assert db.execute("pragma foreign_key_check").fetchall() == []


def test_pre_run_failure_retry_and_settlement_keep_generation_and_history(typed_runtime):
    store, _ = typed_runtime
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
    store, _ = typed_runtime
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
