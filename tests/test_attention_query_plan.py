from pathlib import Path
import json
from app.store import REPLY_TASK_SQL, WORK_SUMMARY_INPUT_SQL
import sqlite3
from datetime import datetime, timezone

import pytest

from app.audit_web import _queue_attention_rows, _reply_task_queue_snapshot, _work_summary_input_queue_snapshot
from app.action_storage import update_action
import app.store as store_module
from app.store import AgentRole, AutoReplyStore
from app.task_entity_storage import append_task_input, bind_current_input, create_task
from app.work_summary_payload import decode_work_summary_payload, encode_work_summary_payload
from tests.support.native_task_fields import _TASK_KINDS, set_native_task_fields



def insert_task(db, kind, values):
    if kind == "reply_tasks":
        now = values.get("created_at", "2026-10-06T00:00:00Z")
        task = create_task(db, values={
            "kind": "reply", "status": values.get("status", "pending"),
            "error": values.get("error", ""), "input_version": 1,
            "created_at": now, "updated_at": now,
        })
        input_values = {
            "channel": values.get("channel", "dingtalk"),
            "conversation_id": values["conversation_id"],
            "conversation_title": values["conversation_title"],
            "single_chat": values["single_chat"],
            "trigger_message_id": values["trigger_message_id"],
            "trigger_create_time": values["trigger_create_time"],
            "trigger_sender": values["trigger_sender"],
            "trigger_text": values.get("trigger_text", ""),
            "trigger_message_json": values.get("trigger_message_json", "{}"),
            "business_object_key": values.get("business_object_key", ""),
            "input_revision_key": "",
            "created_at": now,
        }
        task_input, _ = append_task_input(db, task_id=task["id"], values=input_values)
        assert bind_current_input(
            db, task_id=task["id"], input_id=task_input["id"],
            expected_input_version=1, input_version=2,
        )
        return int(task["id"])
    if kind == "work_summary_inputs":
        now = values.get("created_at", "2026-10-06T00:00:00Z")
        task = create_task(db, values={
            "kind": "work_summary", "status": values.get("status", "pending"),
            "error": values.get("error", ""), "created_at": now, "updated_at": now,
        })
        db.execute(
            "insert into work_summary_inputs(task_id,source_type,source_ref,payload_json) values(?,?,?,?)",
            (task["id"], values["source_type"], values["source_ref"],
             encode_work_summary_payload(values["payload_json"])),
        )
        return int(task["id"])
    raise AssertionError(f"unsupported fixture task kind: {kind}")


def _executed_table_column_reads(db, query):
    objects = {
        int(row[3]): (str(row[0]), str(row[1]))
        for row in db.execute(
            "select type,name,tbl_name,rootpage from sqlite_schema where rootpage>0"
        )
    }
    columns = {}
    cursors = {}
    for instruction in db.execute("explain " + query):
        opcode, cursor, operand = instruction[1], int(instruction[2]), int(instruction[3])
        if opcode == "OpenRead":
            schema_object = objects.get(operand)
            cursors[cursor] = (
                schema_object[1]
                if schema_object is not None and schema_object[0] == "table"
                else None
            )
        elif opcode == "OpenDup":
            cursors[cursor] = cursors.get(operand)
        elif opcode == "Column" and cursors.get(cursor) is not None:
            table = cursors[cursor]
            column = db.execute(f'pragma table_info("{table}")').fetchall()[operand]
            columns.setdefault(table, set()).add(str(column[1]))
    return columns


def _update_task(db, kind, values, where="1", parameters=()):
    ids = [row[0] for row in db.execute(
        f"select id from tasks where kind=? and {where}", (_TASK_KINDS[kind], *parameters))]
    for task_id in ids:
        set_native_task_fields(db, kind, task_id, values)
    return len(ids)


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
        _update_task(db, "reply_tasks", {"status": status, "error": "service failure"}, "id=?", (task.id,))
    return task


@pytest.mark.parametrize("unrelated_count", [0, 2000])
def test_attention_selects_failed_ids_without_scanning_task_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unrelated_count: int
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_task(store, "failed")
    with store._connect() as db:
        for index in range(unrelated_count):
            insert_task(db, "reply_tasks", {
                "conversation_id": f"irrelevant-{index}", "trigger_message_id": str(index),
                "status": "done", "conversation_title": "Unrelated", "single_chat": 1,
                "trigger_create_time": "2026-10-12T00:00:00Z", "trigger_sender": "sender",
                "trigger_text": "irrelevant body " * 200,
                "trigger_message_json": '{"body":"unrelated"}',
            })
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
    assert any("SEARCH tasks USING INDEX idx_tasks_claim_due (kind=?)" in step for step in plan), plan
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
        db.execute("select id from tasks indexed by idx_tasks_claim_due "
                   "where kind='reply' and lower(status)='failed'").fetchall()
    assert {column for table, column in selector_reads if table == "tasks"} <= {
        "id", "kind", "status",
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
        _update_task(db, "reply_tasks", {"status": "done"}, "id=?", (task.id,))
    with store.read_snapshot():
        assert _queue_attention_rows(store, limit=1) == []


def test_indexed_attention_applies_order_and_limit_after_failed_selection(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    tasks = [_failed_task(store, status) for status in ("failed", "FAILED", "FaIlEd")]
    with store._connect() as db:
        _update_task(db, "reply_tasks", {"updated_at": "2026-10-06T00:00:00Z"})
        _update_task(db, "reply_tasks", {"status": "done"}, "id=?", (tasks[1].id,))
    for limit, expected in ((None, [tasks[2], tasks[0]]), (1, [tasks[2]])):
        with store.read_snapshot():
            rows = _queue_attention_rows(store, limit=limit)
        assert [row["id"] for row in rows] == [str(task.id) for task in expected]


def test_attention_excludes_typed_unsubscribe_failures_but_keeps_failed_history(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    unsubscribe = _failed_task(store, "failed")
    ordinary_email = _failed_task(store, "FAILED")
    with store._connect() as db:
        for task, action_type in ((unsubscribe, "unsubscribe"), (ordinary_email, "reply")):
            _update_task(db, "reply_tasks", {
                "channel": "email", "conversation_title": "Email action",
                "trigger_message_id": f"email-action:{task.id}",
                "trigger_message_json": json.dumps({"schema": "email_agent_action.v1", "action_type": action_type}),
            }, "id=?", (task.id,))
        _update_task(db, "reply_tasks", {"error": "unsubscribe_operation_rejected:ValueError"}, "id=?", (unsubscribe.id,))

    rows = _queue_attention_rows(store)

    assert {row["id"] for row in rows if row["category"] == "Reply task"} == {
        str(ordinary_email.id)
    }
    with store._connect() as db:
        row = db.execute(
            f"select status,error from ({REPLY_TASK_SQL}) where id=?",
            (unsubscribe.id,),
        ).fetchone()
        assert tuple(row) == ("failed", "unsubscribe_operation_rejected:ValueError")


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
                    needle = f"select error from ({WORK_SUMMARY_INPUT_SQL}) where lower(status)='failed'"
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
        # Main materializes the current-business projection once and reads only
        # its metadata from the native TaskInput owner.
        assert ('tasks', 'error') in read_columns
        assert not {'payload_json', 'trigger_text', 'trigger_message_json'} & {
            column for name, column in read_columns if name == 'tasks'
        }
        with store._connect() as db:
            actual_reads = _executed_table_column_reads(db, query)
        assert actual_reads.get('task_inputs', set()) <= {'task_id', 'business_object_key'}, actual_reads
        assert not {'trigger_text', 'trigger_message_json'} & actual_reads.get('task_inputs', set()), actual_reads
        assert any("SEARCH inputs USING INTEGER PRIMARY KEY (rowid=?)" in step for step in plan), plan
        assert not any(step.startswith("SCAN inputs") for step in plan), plan
        assert any('MATERIALIZE current' in step for step in plan), plan
        return
    if surface == 'work_attention':
        assert any("SEARCH t USING INDEX idx_tasks_claim_due (kind=?)" in step for step in plan), plan
        with store._connect() as db:
            selector_reads = set()

            def capture_selector(action, name, column, *_):
                if action == sqlite3.SQLITE_READ:
                    selector_reads.add((name, column))
                return sqlite3.SQLITE_OK

            db.set_authorizer(capture_selector)
            db.execute("select id from tasks indexed by idx_tasks_claim_due "
                       "where kind='work_summary' and lower(status)='failed'").fetchall()
        assert {column for name, column in selector_reads if name == 'tasks'} <= {
            "id", "kind", "status",
        }
    else:
        assert ('tasks', 'error') in read_columns
        with store._connect() as db:
            actual_reads = _executed_table_column_reads(db, query)
        assert 'payload_json' not in actual_reads.get('work_summary_inputs', set()), actual_reads
        assert any("SEARCH w USING INTEGER PRIMARY KEY (rowid=?)" in step for step in plan), plan
        assert not any(step.startswith("SCAN w") for step in plan), plan
        assert any("SEARCH t USING INDEX idx_tasks_claim_due (kind=?)" in step for step in plan), plan
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
        _update_task(db, "work_summary_inputs", {"status": status, "updated_at": "2026-10-06T00:00:00Z"})
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
    current_projection = query.split("from (", maxsplit=1)[0]
    assert "tasks.id" in current_projection
    assert "tasks.updated_at" in current_projection
    assert "tasks.error" in current_projection
    assert "trigger_message_json" not in current_projection
    assert "trigger_text" not in current_projection
    assert "payload_json" not in current_projection
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert sum("SEARCH tasks USING INDEX idx_tasks_claim_due (kind=?)" in step for step in plan) == 1, plan
    assert any("SEARCH inputs USING INTEGER PRIMARY KEY (rowid=?)" in step for step in plan), plan
    assert not any(step.startswith("SCAN inputs") for step in plan), plan

    assert ("tasks", "error") in read_columns
    assert {column for name, column in read_columns if name == "tasks"} <= {
        "id", "kind", "status", "updated_at", "error", "attempts", "available_at",
        "claimed_input_id", "claimed_input_version", "created_at", "current_input_id",
        "execution_generation", "input_version", "locked_at", "parameters_json",
    }
    assert any(name == "task_inputs" for name, column in read_columns)
    with store._connect() as db:
        actual_reads = _executed_table_column_reads(db, query)
    assert actual_reads.get("task_inputs", set()) <= {"id", "task_id", "business_object_key"}, actual_reads
    assert not {"trigger_text", "trigger_message_json"} & actual_reads.get("task_inputs", set()), actual_reads
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

        store_module._close_idle_connections()  # measure on a fresh connection both times
        with store._connect() as db:
            db.create_function("decode_work_summary_payload", 1, decode, deterministic=True)
            db.set_progress_handler(progress, 10)
            try:
                selected = db.execute(query).fetchall()
            finally:
                db.set_progress_handler(None, 0)
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


@pytest.mark.parametrize("latest_status", ["done", "failed"])
def test_attention_counts_only_the_latest_dispatched_run_of_a_scheduled_task(
    tmp_path: Path, latest_status: str,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    schedule = store.create_scheduled_task(
        name="Weekly report", prompt="Send weekly report",
        cron_expression="0 12 * * 5", timezone_name="UTC",
        runtime_id="codex_oauth", working_directory=str(tmp_path), now=now,
    )

    def dispatched_task(run_number: int, status: str):
        scheduled_run = store.create_scheduled_task_run(
            schedule.id, trigger_kind="manual", scheduled_for=now, now=now,
        )
        task = store.ensure_reply_task(
            conversation_id=f"scheduled-task-run:{scheduled_run.id}",
            conversation_title="Weekly report", single_chat=False,
            trigger_message_id=f"event-{run_number}", trigger_create_time=now.isoformat(),
            trigger_sender="Agent Cron", trigger_text="Send weekly report",
            channel="scheduled", execution_generation=f"scheduled-run-{scheduled_run.id}",
            business_object_key=f"scheduled-task-run:{scheduled_run.id}",
        )
        owner = f"dispatcher-{run_number}"
        assert store.claim_scheduled_task_run(
            scheduled_run.id, owner=owner, lease_seconds=600, now=now,
        ) is not None
        store.link_scheduled_task_run_execution(
            scheduled_run.id, owner=owner, execution_kind="reply_task",
            execution_id=str(task.id), now=now,
        )
        store.finish_scheduled_task_dispatch(
            scheduled_run.id, owner=owner, status="dispatched", now=now,
        )
        attempt_id = None
        if status == "failed":
            agent_run = store.claim_agent_run(
                task.id, task.execution_generation, role=AgentRole.CONSUMER,
                proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
                operation_id="", owner=f"consumer-{run_number}",
            ).run
            claimed_task = store.claim_reply_task(task.id)
            assert claimed_task is not None
            store.fail_agent_run(agent_run.id, {"code": "test_failure"},
                                 owner=f"consumer-{run_number}")
            store.fail_reply_task(
                task.id, "test failure",
                expected_execution_generation=task.execution_generation,
            )
            attempt_id = store.record_reply_attempt(
                conversation_id=task.conversation_id,
                conversation_title=task.conversation_title,
                trigger_message_id=task.trigger_message_id,
                trigger_sender="Agent Cron", trigger_text=task.trigger_text,
                action="agent_run", sensitivity_kind="general",
                send_status="failed", channel="scheduled",
            )
            with store._connect() as db:
                update_action(db, "reply_attempts", attempt_id,
                              {"agent_run_id": agent_run.id})
        else:
            claimed_task = store.claim_reply_task(task.id)
            assert claimed_task is not None
            store.complete_reply_task(
                task.id, expected_execution_generation=task.execution_generation,
            )
        return task, attempt_id

    old_task, old_attempt_id = dispatched_task(1, "failed")
    newest_task, newest_attempt_id = dispatched_task(2, latest_status)
    rows = _queue_attention_rows(store)
    task_ids = {row["id"] for row in rows if row["category"] == "Reply task"}
    attempt_ids = {row["id"] for row in rows if row["category"] == "Reply"}

    assert str(old_task.id) not in task_ids
    assert str(old_attempt_id) not in attempt_ids
    if latest_status == "done":
        assert str(newest_task.id) not in task_ids
        assert str(newest_attempt_id) not in attempt_ids
    else:
        assert str(newest_task.id) in task_ids or str(newest_attempt_id) in attempt_ids


def test_attention_ignores_orphan_scheduled_run_read_errors_but_keeps_history(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error(
        "scheduled-task-run:99776",
        None,
        "read_recent_messages_rerun",
        "invalid_request: openCid or cid is required",
    )

    rows = _queue_attention_rows(store)

    assert not any(
        row.get("category") == "Service error"
        and row.get("context") == "read_recent_messages_rerun"
        for row in rows
    )
    error = store.list_errors()[0]
    assert error.conversation_id == "scheduled-task-run:99776"
    assert error.resolved_at == ""
