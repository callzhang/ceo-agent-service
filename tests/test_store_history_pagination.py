from contextlib import contextmanager
import sqlite3

import pytest

from app import history_types
from app.action_storage import insert_action, update_action
from app.run_storage import insert_run
from app.store import AutoReplyStore, OperationLog
from app.task_storage import insert_task
from app.task_storage import REPLY_TASK_SQL


def test_plain_history_key_stage_uses_compact_branch_projections(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "compact-history.sqlite3")
    store.record_reply_attempt(
        conversation_id="compact", conversation_title="Compact",
        trigger_message_id="one", trigger_sender="sender", trigger_text="body",
        action="agent_run", sensitivity_kind="general", send_status="failed",
    )
    calls = []
    original = store._operation_logs_key_query

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(store, "_operation_logs_key_query", capture)
    total, rows = store.list_operation_logs_with_count(
        limit=1, source_tables=("reply_attempts", "errors"),
    )
    assert total == 1
    assert len(rows) == 1
    compact_sql, branches = calls[0]
    assert "select source_table, source_id, occurred_at" in compact_sql
    assert branches == 2


def test_plain_history_key_stage_does_not_compute_unfiltered_type(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "compact-type.sqlite3")
    for number in range(30):
        store.record_reply_attempt(
            conversation_id="compact", conversation_title="Compact",
            trigger_message_id=str(number), trigger_sender="sender",
            trigger_text="body", action="agent_run",
            sensitivity_kind="general", send_status="sent",
        )
    computed = []
    original_connect = store._connect
    original_type_sql = history_types.reply_attempt_history_type_sql

    @contextmanager
    def instrumented_connection():
        with original_connect() as db:
            db.create_function(
                "count_history_type", 1,
                lambda value: computed.append(value) or value,
            )
            yield db

    monkeypatch.setattr(store, "_connect", instrumented_connection)
    monkeypatch.setattr(
        history_types, "reply_attempt_history_type_sql",
        lambda: "count_history_type(" + original_type_sql() + ")",
    )
    total, rows = store.list_operation_logs_with_count(
        limit=1, source_tables=("reply_attempts", "errors"),
    )
    assert total == 30
    assert len(rows) == 1
    assert len(computed) == 1


def test_history_generic_filters_match_full_projection(tmp_path):
    store = AutoReplyStore(tmp_path / "history-filters.sqlite3")
    for index, status in enumerate(("failed", "sent", "needs_human")):
        store.record_reply_attempt(
            conversation_id="filters", conversation_title="Matching context",
            trigger_message_id=str(index), trigger_sender="sender",
            trigger_text=f"searchable body {index}", action="agent_run",
            sensitivity_kind="general", send_status=status,
        )
    selections = (
        {}, {"query": "searchable"}, {"statuses": ("sent",)},
        {"log_type": "Reply"}, {"history_types": (history_types.DINGTALK,)},
        {"query": "matching", "statuses": ("failed", "sent")},
    )
    for filters in selections:
        where_sql, where_args = store._operation_log_filters(
            source_tables=("reply_attempts", "errors"), **filters
        )
        with store._connect() as db:
            expected = db.execute(
                "select * from (" + store._operation_logs_base_query(
                    ("reply_attempts", "errors")
                ) + ") " + where_sql +
                " order by occurred_at desc, source_table desc, source_id desc",
                tuple(where_args),
            ).fetchall()
        for offset, limit in ((0, 2), (1, 1), (99, 2)):
            total, rows = store.list_operation_logs_with_count(
                offset=offset, limit=limit,
                source_tables=("reply_attempts", "errors"),
                **filters,
            )
            assert total == len(expected)
            assert [row.model_dump() for row in rows] == [
                OperationLog.model_validate(dict(row)).model_dump()
                for row in expected[offset:offset + limit]
            ]


def test_later_terminal_attempt_is_shared_across_long_trigger_history(tmp_path):
    store = AutoReplyStore(tmp_path / "terminal-history.sqlite3")
    original_ids = []
    for number in range(240):
        original_ids.append(store.record_reply_attempt(
            channel="dingtalk", conversation_id="long-history",
            conversation_title="History", trigger_message_id="same-trigger",
            trigger_sender="sender", trigger_text=f"failed {number}",
            action="agent_run", sensitivity_kind="general", send_status="failed",
        ))
    superseded_ids = []
    for status in ("blocked", "pending", "needs_human"):
        superseded_ids.append(store.record_reply_attempt(
            channel="dingtalk", conversation_id="long-history",
            conversation_title="History", trigger_message_id="same-trigger",
            trigger_sender="sender", trigger_text=status,
            action="agent_run", sensitivity_kind="general", send_status=status,
        ))
    terminal_id = store.record_reply_attempt(
        channel="dingtalk", conversation_id="long-history",
        conversation_title="History", trigger_message_id="same-trigger",
        trigger_sender="sender", trigger_text="sent",
        action="agent_run", sensitivity_kind="general", send_status="sent",
    )
    other_id = store.record_reply_attempt(
        channel="wechat", conversation_id="long-history",
        conversation_title="History", trigger_message_id="same-trigger",
        trigger_sender="sender", trigger_text="other channel",
        action="agent_run", sensitivity_kind="general", send_status="failed",
    )
    with store._connect() as db:
        steps = 0

        def count_steps():
            nonlocal steps
            steps += 1000
            return steps > 2_000_000

        db.set_progress_handler(count_steps, 1000)
        try:
            rows = db.execute(
                "select source_id, status from (" +
                store._operation_logs_base_query(("reply_attempts",)) +
                ") order by source_id"
            ).fetchall()
        finally:
            db.set_progress_handler(None, 0)
    statuses = {row["source_id"]: row["status"] for row in rows}
    assert len(statuses) == len(original_ids) + len(superseded_ids) + 2
    assert all(statuses[attempt_id] == "recovered" for attempt_id in original_ids)
    assert all(statuses[attempt_id] == "recovered" for attempt_id in superseded_ids)
    assert statuses[terminal_id] == "sent"
    assert statuses[other_id] == "failed"
    assert steps < 500_000


@pytest.mark.parametrize(
    "sources", (None, (), ("reply_attempts", "reply_attempts"), ("absent",))
)
def test_history_compact_keys_preserve_source_selection(tmp_path, sources):
    store = AutoReplyStore(tmp_path / "source-selection.sqlite3")
    store.record_reply_attempt(
        conversation_id="source", conversation_title="Source",
        trigger_message_id="one", trigger_sender="sender", trigger_text="body",
        action="agent_run", sensitivity_kind="general", send_status="sent",
    )
    where_sql, where_args = store._operation_log_filters(source_tables=sources)
    with store._connect() as db:
        expected = db.execute(
            "select source_table, source_id from (" +
            store._operation_logs_base_query(sources) + ") " + where_sql +
            " order by occurred_at desc, source_table desc, source_id desc",
            tuple(where_args),
        ).fetchall()
    total, rows = store.list_operation_logs_with_count(
        limit=20, source_tables=sources
    )
    assert total == len(expected)
    assert [(row.source_table, row.source_id) for row in rows] == [
        tuple(row) for row in expected
    ]


@pytest.mark.parametrize("offset,limit", [(0, 1), (3, 2), (8, 2)])
def test_history_hydrates_bodies_only_for_requested_page(
    tmp_path, monkeypatch, offset, limit
):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    for number in range(8):
        store.record_reply_attempt(
            conversation_id="pagination",
            conversation_title="Pagination",
            trigger_message_id=str(number),
            trigger_sender="sender",
            trigger_text=f"body {number}",
            action="agent_run",
            sensitivity_kind="general",
            send_status="skipped",
        )
    expected = store.list_operation_logs(
        limit=limit, offset=offset, source_tables=("reply_attempts",)
    )
    body_reads = []
    original_connect = store._connect
    original_base = store._operation_logs_base_query

    @contextmanager
    def instrumented_connection():
        with original_connect() as db:
            db.create_function(
                "read_history_body", 1, lambda value: body_reads.append(value) or value
            )
            yield db

    def instrumented_base(*args, **kwargs):
        return original_base(*args, **kwargs).replace(
            "trigger_text as summary", "read_history_body(trigger_text) as summary"
        )

    monkeypatch.setattr(store, "_connect", instrumented_connection)
    monkeypatch.setattr(store, "_operation_logs_base_query", instrumented_base)
    total, rows = store.list_operation_logs_with_count(
        limit=limit,
        offset=offset,
        source_tables=("reply_attempts",),
    )
    assert total == 8
    assert rows == expected
    assert len(body_reads) == len(rows)


def test_failed_history_uses_source_candidates_without_changing_results(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "failed-history.sqlite3")
    for number in range(40):
        store.record_reply_attempt(
            conversation_id="history-candidates",
            conversation_title="History candidates",
            trigger_message_id=f"recovered-{number}",
            trigger_sender="sender",
            trigger_text=f"failed body {number}",
            action="agent_run",
            sensitivity_kind="general",
            send_status="failed",
        )
        store.record_reply_attempt(
            conversation_id="history-candidates",
            conversation_title="History candidates",
            trigger_message_id=f"recovered-{number}",
            trigger_sender="sender",
            trigger_text=f"successful body {number}",
            action="agent_run",
            sensitivity_kind="general",
            send_status="sent",
        )
    for number in range(100):
        store.record_reply_attempt(
            conversation_id="history-candidates",
            conversation_title="History candidates",
            trigger_message_id=f"ok-{number}",
            trigger_sender="sender",
            trigger_text=f"successful body {number}",
            action="agent_run",
            sensitivity_kind="general",
            send_status="sent",
        )
    failed_id = store.record_reply_attempt(
        conversation_id="history-candidates",
        conversation_title="History candidates",
        trigger_message_id="failed",
        trigger_sender="sender",
        trigger_text="failed body",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    store.enqueue_reply_task(
        conversation_id="history-candidates",
        conversation_title="History candidates",
        single_chat=False,
        trigger_message_id="same-task-failures",
        trigger_create_time="2026-10-14 00:00:00",
        trigger_sender="sender",
        trigger_text="failed task body",
        execution_generation="history-generation",
    )
    with store._connect() as db:
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("same-task-failures",),
        ).fetchone()[0]
    same_task_attempts = []
    for turn in range(2):
        attempt_id = store.record_reply_attempt(
            conversation_id="history-candidates",
            conversation_title="History candidates",
            trigger_message_id="same-task-failures",
            trigger_sender="sender",
            trigger_text=f"same task failure {turn}",
            action="agent_run",
            sensitivity_kind="general",
            send_status="failed",
        )
        with store._connect() as db:
            run = insert_run(
                db,
                "agent_runs",
                {
                    "task_id": task_id,
                    "execution_generation": "history-generation",
                    "role": "consumer",
                    "turn_attempt": turn,
                    "status": "failed",
                },
            )
            update_action(
                db,
                "reply_attempts",
                attempt_id,
                {"agent_run_id": run.lastrowid},
            )
        same_task_attempts.append(attempt_id)
    source_tables = history_types.HISTORY_SOURCE_TABLES
    where_sql, where_args = store._operation_log_filters(
        statuses=("failed",), source_tables=source_tables
    )
    with store._connect() as db:
        baseline_rows = db.execute(
            f"""
            with operation_logs as ({store._operation_logs_base_query(source_tables)})
            select *, count(*) over() as __total
            from operation_logs
            {where_sql}
            order by occurred_at desc, source_table desc, source_id desc
            """,
            tuple(where_args),
        ).fetchall()
    expected_total = int(baseline_rows[0]["__total"]) if baseline_rows else 0
    expected_rows = [
        OperationLog.model_validate(
            {key: value for key, value in dict(row).items() if key != "__total"}
        )
        for row in baseline_rows
    ]

    original_base = store._operation_logs_base_query
    source_calls = []
    attempt_id_batch_sizes = []
    original_attempt_projection = store._failed_reply_attempt_operation_logs

    def instrumented_base(sources=None, *args, **kwargs):
        source_calls.append(tuple(sources or ()))
        reply_attempt_ids = kwargs.get("reply_attempt_ids")
        if reply_attempt_ids is None and args:
            reply_attempt_ids = args[0]
        if reply_attempt_ids is not None:
            attempt_id_batch_sizes.append(len(reply_attempt_ids))
        return original_base(sources, *args, **kwargs)

    def instrumented_attempt_projection(db, attempt_ids):
        attempt_id_batch_sizes.append(len(attempt_ids))
        return original_attempt_projection(db, attempt_ids)

    monkeypatch.setattr(store, "_operation_logs_base_query", instrumented_base)
    monkeypatch.setattr(
        store, "_failed_reply_attempt_operation_logs", instrumented_attempt_projection
    )
    actual_total, actual_rows = store.list_operation_logs_with_count(
        limit=100,
        source_tables=source_tables,
        statuses=("failed",),
    )

    assert actual_total == expected_total == 2
    assert [(row.source_table, row.source_id, row.status) for row in actual_rows] == [
        (row.source_table, row.source_id, row.status) for row in expected_rows
    ]
    assert ("reply_attempts",) not in source_calls
    assert source_tables not in source_calls
    assert all(len(sources) <= 1 for sources in source_calls)
    assert attempt_id_batch_sizes == [2]
    actual_attempt_ids = {row.source_id for row in actual_rows if row.source_table == "reply_attempts"}
    assert actual_attempt_ids == {failed_id, same_task_attempts[-1]}

    page_total, page_rows = store.list_operation_logs_with_count(
        limit=1,
        source_tables=source_tables,
        statuses=("failed",),
    )
    assert page_total == expected_total
    assert page_rows == expected_rows[:1]
    assert attempt_id_batch_sizes[-1] == 1


def test_history_latest_attempt_query_work_is_bounded_by_task_count(tmp_path):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    with store._connect() as db:
        for number in range(240):
            task_id = insert_task(db, "reply_tasks", {
                "channel": "dingtalk", "conversation_id": f"history-{number}",
                "conversation_title": "History", "single_chat": 0,
                "trigger_message_id": str(number), "trigger_create_time": "",
                "trigger_sender": "sender", "status": "done",
                "execution_generation": "",
            }).lastrowid
            run_id = insert_run(db, "agent_runs", {
                "task_id": task_id, "execution_generation": "",
                "role": "consumer", "operation_id": f"history-{number}",
                "status": "completed",
            }).lastrowid
            insert_action(db, "reply_attempts", {
                "conversation_id": f"history-{number}",
                "conversation_title": "History", "trigger_message_id": str(number),
                "trigger_sender": "sender", "trigger_text": "body", "action": "agent_run",
                "sensitivity_kind": "general", "send_status": "skipped",
                "agent_run_id": run_id,
            })

    with store._connect() as db:
        steps = 0

        def count_steps():
            nonlocal steps
            steps += 1000
            return steps > 3_000_000

        db.set_progress_handler(count_steps, 1000)
        try:
            rows = db.execute(
                "select source_id from (" +
                store._operation_logs_base_query(("reply_attempts",)) +
                ") order by source_id"
            ).fetchall()
        finally:
            db.set_progress_handler(None, 0)
    assert len(rows) == 240
    assert steps < 100_000


def test_history_page_key_query_prunes_unused_display_projection(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "history-key-page.sqlite3")
    with store._connect() as db:
        for number in range(480):
            insert_action(db, "reply_attempts", {
                "conversation_id": f"history-{number}",
                "conversation_title": "History",
                "trigger_message_id": str(number),
                "trigger_sender": "sender",
                "trigger_text": "body",
                "action": "agent_run",
                "sensitivity_kind": "general",
                "send_status": "skipped",
            })

    steps = 0
    original_open_connection = store._open_connection

    def instrumented_connection():
        nonlocal steps
        db = original_open_connection()

        def count_steps():
            nonlocal steps
            steps += 1000
            return 0

        db.set_progress_handler(count_steps, 1000)
        return db

    monkeypatch.setattr(store, "_open_connection", instrumented_connection)
    total, rows = store.list_operation_logs_with_count(
        limit=20,
        offset=1,
        source_tables=("reply_attempts", "work_updates"),
    )

    assert total == 480
    assert len(rows) == 20
    assert {row.source_table for row in rows} == {"reply_attempts"}
    assert steps < 100_000


def test_history_latest_attempt_keeps_only_current_generation_and_last_task_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    ids = []
    orphan_run_ids = []
    with store._connect() as db:
        for task_number, generations in enumerate(
            (("current", "current"), ("old", "current"), (None,),
             ("current", "current"), ("current", "old"))
        ):
            task_id = insert_task(db, "reply_tasks", {
                "channel": "dingtalk", "conversation_id": f"task-{task_number}",
                "conversation_title": "History", "single_chat": 0,
                "trigger_message_id": "same-message", "trigger_create_time": "",
                "trigger_sender": "sender", "status": "done",
                "execution_generation": "current",
            }).lastrowid
            for position, generation in enumerate(generations):
                run_id = None
                if generation is not None:
                    run_id = insert_run(db, "agent_runs", {
                        "task_id": task_id, "execution_generation": generation,
                        "role": "consumer", "operation_id": f"history-{task_number}-{position}",
                        "turn_attempt": position,
                        "status": "completed",
                    }).lastrowid
                    if task_number == 3:
                        orphan_run_ids.append(run_id)
                attempt_id = insert_action(db, "reply_attempts", {
                    "conversation_id": "same-trigger", "conversation_title": "History",
                    "trigger_message_id": "same-message", "trigger_sender": "sender",
                    "trigger_text": f"task {task_number} attempt {position}",
                    "action": "agent_run", "sensitivity_kind": "general",
                    "send_status": "skipped", "agent_run_id": run_id,
                }).lastrowid
                ids.append(attempt_id)
                if task_number == 0 and position == 0:
                    insert_action(db, "memory_write_events", {
                        "attempt_id": attempt_id, "event_type": "history",
                        "status": "pending", "attempts": 0,
                    })

    # A historical copy can contain real foreign-key violations. The original
    # predicate groups valid runs even when their task parent is missing.
    with sqlite3.connect(store.path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("update runs set task_id=99999999 where id in (?, ?)", orphan_run_ids)
        db.execute("update actions set payload_json=json_set(payload_json, '$.agent_run_id', 99999998) where legacy_key=? "
                   "and record_type='reply_attempts'", (str(ids[4]),))

    total, rows = store.list_operation_logs_with_count(
        limit=20, source_tables=("reply_attempts",)
    )
    assert total == 4
    assert {item.source_id for item in rows} == {ids[1], ids[3], ids[4], ids[6]}
    with store._connect() as db:
        row = db.execute(
            "select id, cast(legacy_key as integer) from actions "
            "where record_type='reply_attempts' and legacy_key=?", (str(ids[1]),)
        ).fetchone()
    assert row[0] != row[1]
    assert store.count_operation_logs(source_tables=("reply_attempts",)) == 4
    assert store.list_reply_attempt_operation_statuses(ids) == {
        ids[1]: "skipped", ids[3]: "skipped", ids[4]: "skipped",
        ids[6]: "skipped",
    }
