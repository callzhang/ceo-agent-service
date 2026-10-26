from contextlib import contextmanager
from types import SimpleNamespace
import pytest

from app import history_types
from app.action_storage import insert_action, update_action
from app.store import AutoReplyStore, OperationLog, NATIVE_REPLY_TASK_CURRENT_SQL
from app import task_entity_storage


def insert_run(db, record_type, values):
    assert record_type == "agent_runs"
    mapping = {
        "parent_agent_run_id": "parent_id",
        "codex_session_id": "session_id",
        "codex_transcript_start_line": "transcript_start",
        "codex_transcript_end_line": "transcript_end",
    }
    row = {mapping.get(key, key): value for key, value in values.items()}
    row.setdefault("proposal_revision", 0)
    row.setdefault("turn_attempt", 0)
    row.setdefault("operation_id", "")
    row.setdefault("session_id", "")
    row.setdefault("transcript_start", 0)
    row.setdefault("transcript_end", 0)
    row.setdefault("structured_error_json", "{}")
    row.setdefault("error", "")
    columns = list(row)
    return db.execute(
        f"insert into runs ({','.join(columns)}) values ({','.join('?' for _ in columns)})",
        tuple(row.values()),
    )


def _insert_reply_task(db, values):
    task = task_entity_storage.create_task(
        db, values={"kind": "reply", "status": values.get("status", "pending"),
                    "execution_generation": values.get("execution_generation", ""),
                    "created_at": "2026-10-01 00:00:00",
                    "updated_at": "2026-10-01 00:00:00"},
    )
    task_entity_storage.append_task_input(db, task_id=task["id"], values={
        "channel": values.get("channel", "dingtalk"),
        "conversation_id": values["conversation_id"],
        "conversation_title": values.get("conversation_title", ""),
        "single_chat": values.get("single_chat", 0),
        "trigger_message_id": values["trigger_message_id"],
        "input_revision_key": str(task["id"]),
        "trigger_create_time": values.get("trigger_create_time", ""),
        "trigger_sender": values.get("trigger_sender", ""),
        "trigger_text": values.get("trigger_text", ""),
        "business_object_key": "message:" + values.get("channel", "dingtalk") + ":" +
            values["conversation_id"] + ":" + values["trigger_message_id"],
    })
    row = db.execute("select id from tasks where id=?", (task["id"],)).fetchone()
    return SimpleNamespace(lastrowid=row["id"])


def _update_reply_task(db, physical_id, values):
    task_changes = {key: value for key, value in values.items()
                    if key in {"status", "execution_generation", "updated_at", "error"}}
    cursor = None
    if task_changes:
        fields = tuple(task_changes)
        cursor = db.execute(
            "update tasks set " + ",".join(f'"{name}"=?' for name in fields) + " where id=? and kind='reply'",
            (*task_changes.values(), physical_id),
        )
    if "trigger_message_id" in values:
        current = db.execute(
            "select * from task_inputs where id=(select current_input_id from tasks where id=?)",
            (physical_id,),
        ).fetchone()
        item = {key: current[key] for key in (
            "channel", "conversation_id", "conversation_title", "single_chat",
            "trigger_message_id", "trigger_create_time", "trigger_sender", "trigger_text",
            "trigger_message_json", "oa_url", "business_object_key", "input_provenance_json",
            "input_compacted",
        )}
        item["trigger_message_id"] = values["trigger_message_id"]
        item["input_revision_key"] = "history-refresh"
        new_input, _ = task_entity_storage.append_task_input(db, task_id=physical_id, values=item)
        db.execute("update tasks set current_input_id=?,input_version=input_version+1 where id=?",
                   (new_input["id"], physical_id))
    return cursor or SimpleNamespace(rowcount=1)


def _reply_task_ids(store, trigger_message_id):
    with store._connect() as db:
        row = db.execute(
            f"""select id as physical_id,id as public_id
            from ({NATIVE_REPLY_TASK_CURRENT_SQL}) where trigger_message_id=?""",
            (trigger_message_id,),
        ).fetchone()
    assert row is not None
    return int(row["physical_id"]), int(row["public_id"])


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
    task_id = _reply_task_ids(store, "same-task-failures")[0]
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
    base_attempt_id_batch_sizes = []
    original_attempt_projection = store._failed_reply_attempt_operation_logs

    def instrumented_base(sources=None, *args, **kwargs):
        source_calls.append(tuple(sources or ()))
        reply_attempt_ids = kwargs.get("reply_attempt_ids")
        if reply_attempt_ids is None and args:
            reply_attempt_ids = args[0]
        if reply_attempt_ids is not None:
            base_attempt_id_batch_sizes.append(len(reply_attempt_ids))
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
    assert source_tables not in source_calls
    assert all(len(sources) <= 1 for sources in source_calls)
    assert attempt_id_batch_sizes == [2]
    assert base_attempt_id_batch_sizes == [2]
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
    assert base_attempt_id_batch_sizes[-1] == 1


def test_history_latest_attempt_query_work_is_bounded_by_task_count(tmp_path):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    with store._connect() as db:
        for number in range(240):
            task_id = _insert_reply_task(db, {
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

    def unexpected_email_probe():
        pytest.fail("non-Email History must not probe an excluded Email branch")

    monkeypatch.setattr(store, "_email_actions_history_available", unexpected_email_probe)
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
            task_id = _insert_reply_task(db, {
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
    with store._connect() as db:
        db.execute("pragma foreign_keys=off")
        db.execute("update runs set task_id=99999999 where id in (?, ?)", orphan_run_ids)
        update_action(db, "reply_attempts", ids[4], {"agent_run_id": 99999998})
    assert store.get_reply_attempt(ids[4]).agent_run_id == 99999998

    total, rows = store.list_operation_logs_with_count(
        limit=20, source_tables=("reply_attempts",)
    )
    assert total == 4
    assert {item.source_id for item in rows} == {ids[1], ids[3], ids[4], ids[6]}
    with store._connect() as db:
        row = db.execute(
            "select actions.id,cast(identity.source_identity as integer) "
            "from actions join action_entity_identity_map identity on identity.action_id=actions.id "
            "where identity.source_type='reply_attempts' and identity.source_identity=?", (str(ids[1]),)
        ).fetchone()
    assert row[0] != row[1]
    assert store.count_operation_logs(source_tables=("reply_attempts",)) == 4
    assert store.list_reply_attempt_operation_statuses(ids) == {
        ids[1]: "skipped", ids[3]: "skipped", ids[4]: "skipped",
        ids[6]: "skipped",
    }


def test_failed_history_projects_terminal_status_through_stable_run_task_id(tmp_path):
    store = AutoReplyStore(tmp_path / "history-bound-task.sqlite3")
    store.enqueue_reply_task(
        conversation_id="history-bound-task",
        conversation_title="Bound task",
        single_chat=False,
        trigger_message_id="old-trigger-key",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="sender",
        trigger_text="approval reminder",
        execution_generation="generation-1",
    )
    with store._connect() as db:
        task_id = _reply_task_ids(store, "old-trigger-key")[0]
        attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": "history-bound-task",
            "conversation_title": "Bound task",
            "trigger_message_id": "old-trigger-key",
            "trigger_sender": "sender",
            "trigger_text": "approval reminder",
            "action": "agent_run",
            "sensitivity_kind": "general",
            "send_status": "failed",
        }).lastrowid
        run_id = insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "consumer",
            "status": "completed",
        }).lastrowid
        update_action(db, "reply_attempts", attempt_id, {"agent_run_id": run_id})
        _update_reply_task(db, task_id, {
            "status": "done", "trigger_message_id": "refreshed-trigger-key",
            "updated_at": "2026-10-16 00:00:00",
        })

    done_total, done_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("done",), source_tables=("reply_attempts",)
    )
    failed_total, failed_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("failed",), source_tables=("reply_attempts",)
    )
    store.enqueue_reply_task(
        conversation_id="history-bound-uncertain",
        conversation_title="Uncertain task",
        single_chat=False,
        trigger_message_id="uncertain-trigger",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="sender",
        trigger_text="action awaiting receipt",
        execution_generation="generation-uncertain",
    )
    with store._connect() as db:
        uncertain_task_id = _reply_task_ids(store, "uncertain-trigger")[0]
        _update_reply_task(db, uncertain_task_id, {
            "status": "failed", "error": "external_action_uncertain",
        })
        uncertain_attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": "history-bound-uncertain",
            "conversation_title": "Uncertain task",
            "trigger_message_id": "uncertain-trigger",
            "trigger_sender": "sender",
            "trigger_text": "action awaiting receipt",
            "action": "agent_run",
            "sensitivity_kind": "general",
            "send_status": "failed",
        }).lastrowid
        uncertain_run_id = insert_run(db, "agent_runs", {
            "task_id": uncertain_task_id,
            "execution_generation": "generation-uncertain",
            "role": "consumer",
            "status": "completed",
        }).lastrowid
        update_action(db, "reply_attempts", uncertain_attempt_id, {"agent_run_id": uncertain_run_id})
    uncertain_total, uncertain_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("uncertain",), source_tables=("reply_attempts",)
    )
    failed_total, failed_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("failed",), source_tables=("reply_attempts",)
    )

    assert done_total == 1
    assert [(row.source_id, row.status) for row in done_rows] == [(attempt_id, "done")]
    assert failed_total == 0
    assert failed_rows == []
    assert uncertain_total == 1
    assert [(row.source_id, row.status) for row in uncertain_rows] == [(uncertain_attempt_id, "uncertain")]


@pytest.mark.parametrize("legacy_status", ["completed", "commented", "decision_selected", "recovered"])
def test_history_projects_successful_legacy_attempt_status_from_current_task(
    tmp_path, legacy_status
):
    store = AutoReplyStore(tmp_path / f"history-{legacy_status}.sqlite3")
    store.enqueue_reply_task(
        conversation_id=f"history-{legacy_status}",
        conversation_title="Bound task",
        single_chat=False,
        trigger_message_id="trigger",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="sender",
        trigger_text="approval action",
        execution_generation="generation-1",
    )
    with store._connect() as db:
        task_id = _reply_task_ids(store, "trigger")[0]
        run = insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "audit",
            "status": "completed",
        })
        attempt = insert_action(db, "reply_attempts", {
            "conversation_id": f"history-{legacy_status}",
            "conversation_title": "Bound task",
            "trigger_message_id": "trigger",
            "trigger_sender": "sender",
            "trigger_text": "approval action",
            "action": "agent_run",
            "oa_process_instance_id": "process-instance",
            "sensitivity_kind": "general",
            "send_status": legacy_status,
            "agent_run_id": run.lastrowid,
        })
        _update_reply_task(db, task_id, {"status": "done"})

    done_total, done_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("done",), source_tables=("reply_attempts",)
    )
    legacy_total, legacy_rows = store.list_operation_logs_with_count(
        limit=20, statuses=(legacy_status,), source_tables=("reply_attempts",)
    )

    assert done_total == 1
    assert [(row.source_id, row.status, row.action) for row in done_rows] == [
        (attempt.lastrowid, "done", "agent_run")
    ]
    assert legacy_total == 0
    assert legacy_rows == []


def test_history_skipped_attempt_matches_detail_only_when_bound_run_is_latest(tmp_path):
    store = AutoReplyStore(tmp_path / "history-skipped-current-run.sqlite3")
    store.enqueue_reply_task(
        conversation_id="history-skipped-current-run",
        conversation_title="Bound task",
        single_chat=False,
        trigger_message_id="trigger",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="sender",
        trigger_text="no action",
        execution_generation="generation-1",
    )
    with store._connect() as db:
        task_id = _reply_task_ids(store, "trigger")[0]
        bound_run = insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "consumer",
            "status": "completed",
        })
        insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "audit",
            "status": "completed",
        })
        attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "history-skipped-current-run",
            "conversation_title": "Bound task",
            "trigger_message_id": "trigger",
            "trigger_sender": "sender",
            "trigger_text": "no action",
            "action": "agent_run",
            "sensitivity_kind": "general",
            "oa_process_instance_id": "process-instance",
            "send_status": "skipped",
            "agent_run_id": bound_run.lastrowid,
        })
        _update_reply_task(db, task_id, {"status": "done"})

    done_total, done_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("done",), source_tables=("reply_attempts",)
    )
    skipped_total, skipped_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("skipped",), source_tables=("reply_attempts",)
    )

    assert done_total == 1
    assert [(row.source_id, row.status) for row in done_rows] == [
        (attempt.lastrowid, "done")
    ]
    assert skipped_total == 0
    assert skipped_rows == []


@pytest.mark.parametrize(
    "legacy_status", ["blocked", "completed", "commented", "failed", "recovered"]
)
def test_history_projects_attempt_without_run_from_current_message_task(
    tmp_path, legacy_status
):
    store = AutoReplyStore(tmp_path / "history-unbound-message-task.sqlite3")
    store.enqueue_reply_task(
        conversation_id="history-unbound-message-task",
        conversation_title="Bound by message",
        single_chat=False,
        trigger_message_id="trigger",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="sender",
        trigger_text="approval result",
        execution_generation="generation-1",
    )
    with store._connect() as db:
        task_id = _reply_task_ids(store, "trigger")[0]
        attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "history-unbound-message-task",
            "conversation_title": "Bound by message",
            "trigger_message_id": "trigger",
            "trigger_sender": "sender",
            "trigger_text": "approval result",
            "action": "agent_run",
            "sensitivity_kind": "general",
            "oa_process_instance_id": "process-instance",
            "send_status": legacy_status,
            "agent_run_id": 999999,
        })
        _update_reply_task(db, task_id, {"status": "done"})

    done_total, done_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("done",), source_tables=("reply_attempts",)
    )
    legacy_total, legacy_rows = store.list_operation_logs_with_count(
        limit=20, statuses=(legacy_status,), source_tables=("reply_attempts",)
    )

    assert done_total == 1
    assert [(row.source_id, row.status) for row in done_rows] == [
        (attempt.lastrowid, "done")
    ]
    assert legacy_total == 0
    assert legacy_rows == []


def test_history_projects_terminal_oa_closure_as_skipped_while_preserving_uncertainty(tmp_path):
    import json

    store = AutoReplyStore(tmp_path / "history-terminal-oa-skipped.sqlite3")
    store.enqueue_reply_task(
        conversation_id="terminal-oa-source",
        conversation_title="张玲玲",
        single_chat=False,
        trigger_message_id="terminal-trigger",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="张玲玲",
        trigger_text="催办已过期",
        execution_generation="generation-1",
    )
    with store._connect() as db:
        task_id = _reply_task_ids(store, "terminal-trigger")[0]
        consumer = insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "consumer",
            "status": "completed",
        })
        audit = insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "audit",
            "parent_id": consumer.lastrowid,
            "status": "completed",
        })
        store._adopt_review_candidate_in_connection(
            db, db.execute("select * from runs where id=?", (consumer.lastrowid,)).fetchone(),
            {"outcome": "proposal", "proposal": {"actions": []}},
        )
        store._adopt_candidate_review_in_connection(
            db, consumer.lastrowid, audit.lastrowid,
            {"outcome": "approve", "candidate_digest": store._candidate_context(db, consumer.lastrowid)["candidate_digest"]},
        )
        attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": "terminal-oa-source",
            "conversation_title": "张玲玲",
            "trigger_message_id": "terminal-trigger",
            "trigger_sender": "张玲玲",
            "trigger_text": "催办已过期",
            "action": "agent_run",
            "sensitivity_kind": "general",
            "send_status": "failed",
            "send_error": "external_action_uncertain",
            "agent_run_id": audit.lastrowid,
        }).lastrowid
        _update_reply_task(db, task_id, {"status": "done"})
        resolution = json.dumps({
            "business_id": "business-1",
            "checked_at": "2026-10-15T10:37:10+00:00",
            "closure_reason": "source process terminated; follow-up is no longer actionable",
            "current_principal_task_ids": [],
            "external_write_count": 0,
            "historical_chat_delivery": "unverified; original uncertain delivery remains unchanged",
            "native_and_openapi_business_id_match": True,
            "original_message_id": "terminal-trigger",
            "original_oa_task_id": "task-1",
            "original_oa_task_status": "CANCELED",
            "process_finished_at": "2026-10-14 17:44:07",
            "process_instance_id": "process-1",
            "process_status": "TERMINATED",
            "source": "exact original DingTalk message and native OA detail/OpenAPI readback",
        })
        update_action(db, "reply_attempts", attempt_id, {
            "resolved_at": "2026-10-15T10:37:10+00:00",
            "resolution": resolution,
        })

    skipped_total, skipped_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("skipped",), source_tables=("reply_attempts",)
    )
    uncertain_total, uncertain_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("uncertain",), source_tables=("reply_attempts",)
    )

    assert store.get_review_candidate(consumer.lastrowid) is not None
    assert skipped_total == 1
    assert [(row.source_id, row.status) for row in skipped_rows] == [(attempt_id, "skipped")]
    assert uncertain_total == 0
    assert uncertain_rows == []
    history = store.list_history_items(
        send_statuses=("skipped",), kinds=("reply",), limit=20
    )
    assert [(item.source_id, item.status) for item in history] == [(attempt_id, "skipped")]
    stored = store.get_reply_attempt(attempt_id)
    assert stored is not None
    assert stored.send_status == "failed"
    assert stored.send_error == "external_action_uncertain"


def test_history_keeps_unbound_oa_external_uncertainty_uncertain(tmp_path):
    store = AutoReplyStore(tmp_path / "history-unbound-oa-uncertain.sqlite3")
    store.enqueue_reply_task(
        conversation_id="history-unbound-oa-uncertain",
        conversation_title="Uncertain OA task",
        single_chat=False,
        trigger_message_id="trigger",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="sender",
        trigger_text="approval action",
        execution_generation="generation-1",
    )
    with store._connect() as db:
        task_id = _reply_task_ids(store, "trigger")[0]
        _update_reply_task(db, task_id, {
            "status": "failed", "error": "external_action_uncertain",
        })
        attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "history-unbound-oa-uncertain",
            "conversation_title": "Uncertain OA task",
            "trigger_message_id": "trigger",
            "trigger_sender": "sender",
            "trigger_text": "approval action",
            "action": "agent_run",
            "oa_process_instance_id": "process-instance",
            "sensitivity_kind": "general",
            "send_status": "failed",
        })

    uncertain_total, uncertain_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("uncertain",), source_tables=("reply_attempts",)
    )
    recovered_total, recovered_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("recovered",), source_tables=("reply_attempts",)
    )

    assert uncertain_total == 1
    assert [(row.source_id, row.status) for row in uncertain_rows] == [
        (attempt.lastrowid, "uncertain")
    ]
    assert recovered_total == 0
    assert recovered_rows == []


def test_history_does_not_call_unbound_oa_failure_recovered_without_a_task(tmp_path):
    store = AutoReplyStore(tmp_path / "history-unbound-oa-failure.sqlite3")
    with store._connect() as db:
        failed_attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "history-unbound-oa-failure",
            "conversation_title": "Failed OA task",
            "trigger_message_id": "trigger",
            "trigger_sender": "sender",
            "trigger_text": "approval action",
            "action": "agent_run",
            "oa_process_instance_id": "process-instance",
            "sensitivity_kind": "general",
            "send_status": "failed",
        })
        insert_action(db, "reply_attempts", {
            "conversation_id": "history-unbound-oa-failure",
            "conversation_title": "Failed OA task",
            "trigger_message_id": "trigger",
            "trigger_sender": "sender",
            "trigger_text": "approval action",
            "action": "agent_run",
            "oa_process_instance_id": "process-instance",
            "sensitivity_kind": "general",
            "send_status": "completed",
        })

    failed_total, failed_rows = store.list_operation_logs_with_count(
        limit=20,
        statuses=("failed",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )
    recovered_total, recovered_rows = store.list_operation_logs_with_count(
        limit=20,
        statuses=("recovered",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )

    assert failed_total == 1
    assert [(row.source_id, row.status) for row in failed_rows] == [
        (failed_attempt.lastrowid, "failed")
    ]
    assert recovered_total == 0
    assert recovered_rows == []


def test_unbound_failed_oa_attempts_close_only_for_exact_orphan_source(tmp_path):
    store = AutoReplyStore(tmp_path / "history-unbound-oa-terminal.sqlite3")
    with store._connect() as db:
        failed_attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "orphan-oa",
            "conversation_title": "Completed OA task",
            "trigger_message_id": "trigger-oa",
            "trigger_sender": "sender",
            "trigger_text": "approval action",
            "action": "agent_run",
            "oa_process_instance_id": "process-oa",
            "oa_task_id": "task-oa",
            "sensitivity_kind": "general",
            "send_status": "failed",
            "send_error": "codex_process_failed",
            "codex_reason": "runtime failed before a Consumer result",
        })
        older_attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "orphan-oa",
            "conversation_title": "Completed OA task",
            "trigger_message_id": "message-older",
            "trigger_sender": "sender",
            "trigger_text": "approval action retry",
            "action": "agent_run",
            "oa_process_instance_id": "process-oa",
            "oa_task_id": "task-oa",
            "sensitivity_kind": "general",
            "send_status": "failed",
            "send_error": "codex_process_failed",
        })
        later_completed_attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "oa_pending_scan:process-oa",
            "conversation_title": "Completed OA task",
            "trigger_message_id": "later-business-result",
            "trigger_sender": "sender",
            "trigger_text": "terminal source result",
            "action": "agent_run",
            "oa_process_instance_id": "process-oa",
            "oa_task_id": "task-oa",
            "sensitivity_kind": "general",
            "send_status": "completed",
        })

    candidates = store.list_unbound_failed_oa_attempts()
    assert [attempt.id for attempt in candidates] == [older_attempt.lastrowid]
    evidence = '{"process_status":"COMPLETED","oa_task_status":"COMPLETED"}'
    assert store.skip_unbound_failed_oa_attempt(
        candidates[0].id,
        process_instance_id="process-oa",
        oa_task_id="task-oa",
        terminal_evidence=evidence,
    )
    rows = store.list_operation_logs_with_count(
        limit=10,
        statuses=("skipped",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )[1]
    assert {(row.source_id, row.status) for row in rows} == {
        (failed_attempt.lastrowid, "skipped"),
        (older_attempt.lastrowid, "skipped"),
    }
    attempt = store.get_reply_attempt(failed_attempt.lastrowid)
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "codex_process_failed"
    assert attempt.codex_reason == "runtime failed before a Consumer result"
    assert attempt.resolution == evidence
    older = store.get_reply_attempt(older_attempt.lastrowid)
    assert older.send_status == "skipped"
    assert older.send_error == "codex_process_failed"
    assert older.resolution == evidence
    later = store.get_reply_attempt(later_completed_attempt.lastrowid)
    assert later.send_status == "completed"
    assert store.list_unbound_failed_oa_attempts() == []


def test_unbound_failed_oa_attempt_closure_cannot_skip_when_local_task_exists(tmp_path):
    store = AutoReplyStore(tmp_path / "history-bound-oa-terminal.sqlite3")
    store.enqueue_reply_task(
        conversation_id="bound-oa",
        conversation_title="Local task exists",
        single_chat=False,
        trigger_message_id="trigger-oa",
        trigger_create_time="2026-10-17T00:00:00Z",
        trigger_sender="sender",
        trigger_text="approval action",
        business_object_key="oa:process-oa:task-oa",
    )
    with store._connect() as db:
        failed_attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "bound-oa",
            "conversation_title": "Local task exists",
            "trigger_message_id": "trigger-oa",
            "trigger_sender": "sender",
            "trigger_text": "approval action",
            "action": "agent_run",
            "oa_process_instance_id": "process-oa",
            "oa_task_id": "task-oa",
            "sensitivity_kind": "general",
            "send_status": "failed",
        })

    assert store.list_unbound_failed_oa_attempts() == []
    assert not store.skip_unbound_failed_oa_attempt(
        failed_attempt.lastrowid,
        process_instance_id="process-oa",
        oa_task_id="task-oa",
        terminal_evidence='{"process_status":"COMPLETED"}',
    )


def test_terminal_local_task_does_not_block_orphan_attempt_closure(tmp_path):
    store = AutoReplyStore(tmp_path / "history-terminal-owner-oa.sqlite3")
    store.enqueue_reply_task(
        conversation_id="terminal-owner-oa",
        conversation_title="Terminal local task",
        single_chat=False,
        trigger_message_id="task-trigger",
        trigger_create_time="2026-10-17T00:00:00Z",
        trigger_sender="sender",
        trigger_text="approval action",
        business_object_key="oa:process-oa:task-oa",
    )
    task_id, public_task_id = _reply_task_ids(store, "task-trigger")
    with store._connect() as db:
        _update_reply_task(db, task_id, {"status": "failed"})
    assert store.close_unresolved_reply_task_already_settled(
        public_task_id,
        expected_execution_generation="initial",
        settled_evidence="previously settled",
    )
    with store._connect() as db:
        attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "terminal-owner-oa",
            "conversation_title": "Terminal local task",
            "trigger_message_id": "old-attempt-trigger",
            "trigger_sender": "sender",
            "trigger_text": "old approval run",
            "action": "agent_run",
            "oa_process_instance_id": "process-oa",
            "oa_task_id": "task-oa",
            "sensitivity_kind": "general",
            "send_status": "failed",
        })

    assert [row.id for row in store.list_unbound_failed_oa_attempts()] == [
        attempt.lastrowid
    ]
    assert store.skip_unbound_failed_oa_attempt(
        attempt.lastrowid,
        process_instance_id="process-oa",
        oa_task_id="task-oa",
        terminal_evidence='{"process_status":"COMPLETED","oa_task_status":"COMPLETED"}',
    )


def test_history_keeps_current_uncertain_execution_uncertain_after_task_closes(tmp_path):
    store = AutoReplyStore(tmp_path / "history-uncertain-execution.sqlite3")
    store.enqueue_reply_task(
        conversation_id="history-uncertain-execution",
        conversation_title="Uncertain execution",
        single_chat=False,
        trigger_message_id="old-trigger",
        trigger_create_time="2026-10-15 00:00:00",
        trigger_sender="sender",
        trigger_text="approved OA action",
        execution_generation="generation-1",
    )
    with store._connect() as db:
        task_id = _reply_task_ids(store, "old-trigger")[0]
        consumer = insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "consumer",
            "status": "completed",
        })
        audit = insert_run(db, "agent_runs", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "audit",
            "parent_id": consumer.lastrowid,
            "status": "completed",
        })
        store._adopt_review_candidate_in_connection(
            db, db.execute("select * from runs where id=?", (consumer.lastrowid,)).fetchone(),
            {"outcome": "proposal", "proposal": {"actions": []}},
        )
        store._adopt_candidate_review_in_connection(
            db, consumer.lastrowid, audit.lastrowid,
            {"outcome": "approve", "candidate_digest": store._candidate_context(db, consumer.lastrowid)["candidate_digest"]},
        )
        from app.execution_owner_storage import create_native_execution, update_native_execution
        create_native_execution(
            db, consumer_run_id=consumer.lastrowid,
            approved_audit_run_id=audit.lastrowid, selection_id=None,
            status="uncertain", lease_owner="", lease_expires_at="",
        )
        update_native_execution(db, consumer.lastrowid, {
            "result_json": '{"reason":"receipt pending"}',
        })
        attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": "history-uncertain-execution",
            "conversation_title": "Uncertain execution",
            "trigger_message_id": "old-trigger",
            "trigger_sender": "sender",
            "trigger_text": "approved OA action",
            "action": "agent_run",
            "sensitivity_kind": "general",
            "send_status": "failed",
            "send_error": "external_action_uncertain",
            "resolved_at": "2026-10-16 00:00:00",
            "resolution": "Business follow-up closed; original external receipt remains unverified.",
            "agent_run_id": audit.lastrowid,
        }).lastrowid
        _update_reply_task(db, task_id, {
            "status": "done", "trigger_message_id": "new-trigger",
            "updated_at": "2026-10-16 00:00:00",
        })

    uncertain_total, uncertain_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("uncertain",), source_tables=("reply_attempts",)
    )
    done_total, done_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("done",), source_tables=("reply_attempts",)
    )

    assert store.get_review_candidate(consumer.lastrowid) is not None
    assert uncertain_total == 1
    assert [(row.source_id, row.status) for row in uncertain_rows] == [(attempt_id, "uncertain")]
    assert done_total == 0
    assert done_rows == []
    # History is a projection; the underlying Attempt audit row remains failed.
    assert store.get_reply_attempt(attempt_id).send_status == "failed"
