from contextlib import contextmanager
import sqlite3

import pytest

from app import history_types
from app.action_storage import insert_action, update_action
from app.run_storage import insert_run
from app.store import AutoReplyStore, OperationLog
from app.task_storage import insert_task
from app.task_storage import REPLY_TASK_SQL


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
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("old-trigger-key",),
        ).fetchone()[0]
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
        db.execute(
            "update tasks set status='done', trigger_message_id=?, "
            "updated_at='2026-10-16 00:00:00' where id=?",
            ("refreshed-trigger-key", task_id),
        )

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
        uncertain_task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("uncertain-trigger",),
        ).fetchone()[0]
        db.execute(
            "update tasks set status='failed', error='external_action_uncertain' where id=?",
            (uncertain_task_id,),
        )
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
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("trigger",),
        ).fetchone()[0]
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
        db.execute(
            "update tasks set status='done' where id=?", (task_id,)
        )

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
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("trigger",),
        ).fetchone()[0]
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
        db.execute("update tasks set status='done' where id=?", (task_id,))

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
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("trigger",),
        ).fetchone()[0]
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
        db.execute("update tasks set status='done' where id=?", (task_id,))

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
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("trigger",),
        ).fetchone()[0]
        db.execute(
            "update tasks set status='failed', error='external_action_uncertain' where id=?",
            (task_id,),
        )
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
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            ("old-trigger",),
        ).fetchone()[0]
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
        candidate = insert_action(db, "review_candidates", {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "consumer_run_id": consumer.lastrowid,
            "stage_index": 0,
            "proposal_revision": 0,
            "candidate_digest": "candidate-digest",
            "candidate_json": '{"outcome":"proposal","proposal":{"actions":[]}}',
        })
        review = insert_action(db, "candidate_reviews", {
            "candidate_id": candidate.lastrowid,
            "audit_run_id": audit.lastrowid,
            "decision": "approve",
            "candidate_digest": "candidate-digest",
            "result_json": '{"outcome":"approve"}',
        })
        execution = insert_action(db, "candidate_executions", {
            "candidate_id": candidate.lastrowid,
            "review_id": review.lastrowid,
            "status": "uncertain",
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
        db.execute(
            "update tasks set status='done', trigger_message_id=?, "
            "updated_at='2026-10-16 00:00:00' where id=?",
            ("new-trigger", task_id),
        )

    uncertain_total, uncertain_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("uncertain",), source_tables=("reply_attempts",)
    )
    done_total, done_rows = store.list_operation_logs_with_count(
        limit=20, statuses=("done",), source_tables=("reply_attempts",)
    )

    assert execution.lastrowid
    assert uncertain_total == 1
    assert [(row.source_id, row.status) for row in uncertain_rows] == [(attempt_id, "uncertain")]
    assert done_total == 0
    assert done_rows == []
    # History is a projection; the underlying Attempt audit row remains failed.
    assert store.get_reply_attempt(attempt_id).send_status == "failed"
