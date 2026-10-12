from pathlib import Path
import sqlite3

import pytest

from app.audit_web import _queue_attention_rows, _reply_task_queue_snapshot, _work_summary_input_queue_snapshot
from app.store import AutoReplyStore
from app.task_storage import insert_task
from app.work_summary_payload import decode_work_summary_payload


def _failed_task(store: AutoReplyStore, status: str):
    task = store.ensure_reply_task(
        conversation_id=f"cid-{status}",
        conversation_title="Query plan regression",
        single_chat=True,
        trigger_message_id=f"msg-{status}",
        trigger_sender="sender",
        trigger_text="Please handle this request.",
        trigger_create_time="2026-10-06T00:00:00Z",
    )
    with store._connect() as db:
        db.execute(
            "update tasks set status=?, error='service failure' where record_type='reply_tasks' and id=?",
            (status, task.id),
        )
    return task


@pytest.mark.parametrize("unrelated_count", [0, 2000])
def test_attention_selects_failed_ids_without_scanning_task_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unrelated_count: int
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_task(store, "failed")
    with store._connect() as db:
        for index in range(unrelated_count):
            parent = insert_task(db, "reply_tasks", {
                "conversation_id": f"irrelevant-{index}", "trigger_message_id": str(index),
                "status": "done", "conversation_title": "Unrelated", "single_chat": 1,
                "trigger_create_time": "2026-10-12T00:00:00Z", "trigger_sender": "sender",
            }).lastrowid
            canonical = insert_task(db, "reply_task_inputs", {
                "legacy_key": 100000 + index, "parent_id": parent, "channel": "dingtalk",
                "conversation_id": f"unrelated-input-{index}", "conversation_title": "Unrelated",
                "single_chat": 1, "trigger_message_id": str(index),
                "trigger_create_time": "2026-10-12T00:00:00Z", "trigger_sender": "sender",
                "business_object_key": "",
                "trigger_text": "irrelevant body " * 200,
                "trigger_message_json": '{"body":"unrelated"}',
            }).lastrowid
            db.execute("update tasks set current_input_id=? where id=?", (canonical, parent))
    statements: list[str] = []
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        rows = _queue_attention_rows(store)
    assert any(row["id"] == str(task.id) for row in rows)
    query = next(
        sql for sql in statements
        if "select reply_tasks.id, reply_tasks.channel" in sql
    )
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert any("SEARCH t USING INDEX idx_tasks_public_id" in step for step in plan), plan
    assert any("SEARCH i USING INTEGER PRIMARY KEY" in step for step in plan), plan
    assert any("SCAN tasks USING INDEX idx_reply_tasks_status" in step for step in plan), plan
    assert not any(
        step.startswith(("SCAN tasks", "SCAN t"))
        and "USING INDEX" not in step
        for step in plan
    ), plan
    steps = 0

    def progress():
        nonlocal steps
        steps += 100
        return int(steps >= 100000)

    with store._connect() as db:
        db.set_progress_handler(progress, 100)
        selected = db.execute(query).fetchall()
    assert [row["id"] for row in selected] == [task.id]
    assert steps < 100000
    with store._connect() as db:
        selector_reads: set[tuple[str, str]] = set()

        def capture_selector_reads(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                selector_reads.add((table, column))
            return sqlite3.SQLITE_OK

        db.set_authorizer(capture_selector_reads)
        db.execute("select cast(legacy_key as integer) from tasks indexed by "
                   "idx_reply_tasks_status where record_type='reply_tasks' "
                   "and lower(status)='failed'").fetchall()
    assert {column for table, column in selector_reads if table == "tasks"} <= {
        "legacy_key", "record_type", "status",
    }


@pytest.mark.parametrize("status", ["failed", "FAILED", "FaIlEd"])
def test_indexed_attention_keeps_case_insensitive_matching_and_fresh_reads(
    tmp_path: Path, status: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_task(store, status)
    with store.read_snapshot():
        rows = _queue_attention_rows(store, limit=1)
    assert [(row["id"], row["status"]) for row in rows] == [(str(task.id), status)]

    with store._connect() as db:
        db.execute("update tasks set status='done' where record_type='reply_tasks' and id=?", (task.id,))
    with store.read_snapshot():
        assert _queue_attention_rows(store, limit=1) == []


def test_indexed_attention_applies_order_and_limit_after_failed_selection(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    tasks = [_failed_task(store, status) for status in ("failed", "FAILED", "FaIlEd")]
    with store._connect() as db:
        db.execute("update tasks set updated_at='2026-10-06T00:00:00Z' where record_type='reply_tasks'")
        db.execute("update tasks set status='done' where record_type='reply_tasks' and id=?", (tasks[1].id,))
    for limit, expected in ((None, [tasks[2], tasks[0]]), (1, [tasks[2]])):
        with store.read_snapshot():
            rows = _queue_attention_rows(store, limit=limit)
        assert [row["id"] for row in rows] == [str(task.id) for task in expected]


@pytest.mark.parametrize("surface", ["work_attention", "work_error", "reply_error"])
def test_failure_diagnostics_do_not_scan_queue_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _failed_task(store, "FaIlEd")
    statements: list[str] = []
    read_columns: set[tuple[str, str]] = set()
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)

        def capture_reads(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                read_columns.add((table, column))
            return sqlite3.SQLITE_OK

        db.set_authorizer(capture_reads)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        if surface == "work_attention":
            _queue_attention_rows(store)
            needle = "select id, status as status, case when lower(source_type)"
            table = "tasks"
        else:
            with store._connect() as db:
                if surface == "work_error":
                    assert _work_summary_input_queue_snapshot(db)["latest_error"] == ""
                    needle = "select error from tasks where record_type='work_summary_inputs'"
                    table = "tasks"
                else:
                    assert _reply_task_queue_snapshot(db)["latest_error"] == "service failure"
                    needle = "with current as materialized"
                    table = "tasks"
    query = next(sql for sql in statements if needle in sql)
    with store._connect() as db:
        if surface == "work_attention":
            db.create_function(
                "decode_work_summary_payload", 1, decode_work_summary_payload,
                deterministic=True,
            )
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    if surface == 'reply_error':
        # Main evaluates the current-business projection once. Its table scan
        # reads scalar metadata, and never joins canonical input bodies.
        assert ('tasks', 'error') in read_columns
        assert not {'payload_json', 'trigger_text', 'trigger_message_json'} & {
            column for name, column in read_columns if name == 'tasks'
        }
        assert any('MATERIALIZE current' in step for step in plan), plan
        return
    if surface == 'work_attention':
        assert any("SEARCH t USING INDEX idx_tasks_public_id" in step for step in plan), plan
        assert any("SCAN tasks USING INDEX idx_work_summary_inputs_updated" in step for step in plan), plan
        with store._connect() as db:
            selector_reads = set()

            def capture_selector(action, name, column, *_):
                if action == sqlite3.SQLITE_READ:
                    selector_reads.add((name, column))
                return sqlite3.SQLITE_OK

            db.set_authorizer(capture_selector)
            db.execute("select cast(legacy_key as integer) from tasks "
                       "where record_type='work_summary_inputs' "
                       "and lower(status)='failed'").fetchall()
        assert {column for name, column in selector_reads if name == 'tasks'} <= {
            "legacy_key", "record_type", "status",
        }
    else:
        assert ('tasks', 'error') in read_columns
        assert ('tasks', 'payload_json') not in read_columns
        assert any("SCAN tasks USING INDEX idx_work_summary_inputs_updated" in step for step in plan), plan
    assert not any(step.startswith(f"SCAN {table}") and "USING INDEX" not in step for step in plan), plan


@pytest.mark.parametrize("status", ["failed", "FaIlEd"])
def test_work_failure_reads_keep_nonblank_error_order_and_fresh_recovery(
    tmp_path: Path, status: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    failed = store.enqueue_work_summary_input("test", "first", '{"summary":"First item"}')
    blank = store.enqueue_work_summary_input("test", "second", '{"summary":"Blank error item"}')
    store.mark_work_summary_input_failed(failed, "work failure")
    store.mark_work_summary_input_failed(blank, "  ")
    with store._connect() as db:
        db.execute("update tasks set status=?, updated_at='2026-10-06T00:00:00Z' where record_type='work_summary_inputs'", (status,))
    with store.read_snapshot():
        rows = _queue_attention_rows(store, limit=1)
        assert [row["id"] for row in rows] == [str(blank)]
        with store._connect() as db:
            assert _work_summary_input_queue_snapshot(db)["latest_error"] == "work failure"
    store.mark_work_summary_input_done(failed)
    store.mark_work_summary_input_done(blank)
    with store.read_snapshot():
        assert _queue_attention_rows(store) == []
        with store._connect() as db:
            assert _work_summary_input_queue_snapshot(db)["latest_error"] == ""


def test_reply_queue_snapshot_reads_metadata_in_one_source_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _failed_task(store, "FaIlEd")
    statements: list[str] = []
    read_columns: set[tuple[str, str]] = set()
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)

        def capture_reads(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                read_columns.add((table, column))
            return sqlite3.SQLITE_OK

        db.set_authorizer(capture_reads)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        with store._connect() as db:
            snapshot = _reply_task_queue_snapshot(db)
            assert snapshot["latest_error"] == "service failure"
            assert snapshot["failed"] == 1
    queries = [sql for sql in statements if "with current as materialized" in sql]
    assert len(queries) == 1
    query = queries[0]
    assert "trigger_message_json" not in query
    assert "trigger_text" not in query
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert sum(step.startswith("SCAN tasks") for step in plan) == 1, plan

    assert ("tasks", "error") in read_columns
    assert {column for name, column in read_columns if name == "tasks"} <= {
        "id", "status", "updated_at", "error", "business_object_key", "record_type",
    }
    assert not any(name == "reply_task_inputs" for name, column in read_columns)
    assert any("MATERIALIZE current" in step for step in plan), plan


def test_work_attention_scalar_scan_ignores_unrelated_task_growth(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "work-cost.sqlite3")
    failed = store.enqueue_work_summary_input("test", "failed", '{"summary":"exact failed body"}')
    store.mark_work_summary_input_failed(failed, "work failure")
    with store._connect() as db:
        for index in range(2000):
            insert_task(db, "work_summary_inputs", {
                "source_type": "test", "source_ref": str(index), "status": "done",
                "payload_json": '{"summary":"' + "unrelated body " * 200 + '"}',
            })
    statements = []
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        rows = _queue_attention_rows(store)
    assert [(row["id"], row["summary"]) for row in rows] == [(str(failed), "exact failed body")]
    query = next(sql for sql in statements if "select id, status as status, case when lower(source_type)" in sql)

    def measured_query():
        steps = 0
        decoded = []

        def progress():
            nonlocal steps
            steps += 10
            return int(steps >= 400000)

        def decode(value):
            decoded.append(value)
            return decode_work_summary_payload(value)

        with store._connect() as db:
            db.create_function("decode_work_summary_payload", 1, decode, deterministic=True)
            db.set_progress_handler(progress, 10)
            selected = db.execute(query).fetchall()
        assert [row["id"] for row in selected] == [failed]
        assert decoded == ['{"summary":"exact failed body"}']
        assert steps < 400000
        return steps

    before = measured_query()
    with store._connect() as db:
        for index in range(2000):
            insert_task(db, "reply_tasks", {
                "conversation_id": f"unrelated-{index}", "conversation_title": "Unrelated",
                "single_chat": 1, "trigger_message_id": str(index),
                "trigger_create_time": "2026-10-12T00:00:00Z", "trigger_sender": "sender",
                "status": "done",
            })
    assert measured_query() == before
