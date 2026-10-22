import pytest

from app import history_types
from app.action_storage import insert_action, update_action
from app.run_storage import insert_run
from app.store import AutoReplyStore, REPLY_TASK_SQL
from app.task8_storage import task8_set_parts


@pytest.mark.parametrize("legacy_status", [
    "blocked", "completed", "commented", "decision_selected",
    "failed", "recovered", "skipped",
])
def test_failed_history_includes_projected_oa_failures(tmp_path, legacy_status):
    store = AutoReplyStore(tmp_path / "oa-projected-failure.sqlite3")
    store.enqueue_reply_task(
        conversation_id="oa-current", conversation_title="Approval",
        single_chat=False, trigger_message_id="trigger",
        trigger_create_time="2026-10-15 00:00:00", trigger_sender="sender",
        trigger_text="approval", execution_generation="generation",
    )
    with store._connect() as db:
        task_id = db.execute(f"select id from ({REPLY_TASK_SQL})").fetchone()[0]
        parts = task8_set_parts("reply_tasks", {"status": "failed"})
        assert db.execute(
            f"update tasks set {parts.sql} where record_type='reply_tasks' and cast(legacy_key as integer)=?",
            (*parts.parameters, task_id),
        ).rowcount == 1
        attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": "oa-current", "conversation_title": "Approval",
            "trigger_message_id": "trigger", "trigger_sender": "sender",
            "trigger_text": "approval",
            "action": "agent_run", "sensitivity_kind": "general",
            "oa_process_instance_id": "approval-instance",
            "send_status": legacy_status,
        }).lastrowid
    for sources in (("reply_attempts",), ("reply_attempts", "errors")):
        total, rows = store.list_operation_logs_with_count(
            limit=1, statuses=("failed",), source_tables=sources,
        )
        assert total == 1
        assert [(row.source_id, row.status) for row in rows] == [(attempt_id, "failed")]
        for offset in (1, 99):
            page_total, page = store.list_operation_logs_with_count(
                limit=1, offset=offset, statuses=("failed",), source_tables=sources,
            )
            assert page_total == 1
            assert page == []


@pytest.mark.parametrize("scenario", ["orphan_after_terminal", "uncertain", "pending"])
def test_failed_history_uses_current_oa_status(tmp_path, scenario):
    store = AutoReplyStore(tmp_path / "oa-current-status.sqlite3")
    task_id = None
    if scenario != "orphan_after_terminal":
        store.enqueue_reply_task(
            conversation_id="oa-current", conversation_title="Approval",
            single_chat=False, trigger_message_id="trigger",
            trigger_create_time="2026-10-15 00:00:00", trigger_sender="sender",
            trigger_text="approval", execution_generation="generation",
        )
    with store._connect() as db:
        if scenario != "orphan_after_terminal":
            task_id = db.execute(f"select id from ({REPLY_TASK_SQL})").fetchone()[0]
            parts = task8_set_parts("reply_tasks", {
                "status": "failed" if scenario == "uncertain" else "pending",
                "error": "external_action_uncertain" if scenario == "uncertain" else "",
            })
            assert db.execute(
                f"update tasks set {parts.sql} where record_type='reply_tasks' and cast(legacy_key as integer)=?",
                (*parts.parameters, task_id),
            ).rowcount == 1
        values = {
            "conversation_id": "oa-current", "conversation_title": "Approval",
            "trigger_message_id": "trigger", "trigger_sender": "sender",
            "trigger_text": "approval",
            "action": "agent_run", "sensitivity_kind": "general",
            "oa_process_instance_id": "approval-instance", "send_status": "failed",
        }
        attempt_id = insert_action(db, "reply_attempts", values).lastrowid
        if scenario == "orphan_after_terminal":
            insert_action(db, "reply_attempts", {**values, "send_status": "completed"})
        elif scenario == "pending":
            run_id = insert_run(db, "agent_runs", {
                "task_id": task_id, "execution_generation": "generation",
                "role": "consumer", "status": "completed",
            }).lastrowid
            update_action(db, "reply_attempts", attempt_id, {"agent_run_id": run_id})
    full = store.list_operation_logs(
        statuses=("failed",), source_tables=("reply_attempts",),
    )
    expected = [(attempt_id, "failed")] if scenario == "orphan_after_terminal" else []
    assert [(row.source_id, row.status) for row in full] == expected
    for history_filter in (None, (history_types.APPROVAL,)):
        total, rows = store.list_operation_logs_with_count(
            limit=20, statuses=("failed",), source_tables=("reply_attempts",),
            history_types=history_filter,
        )
        assert total == len(expected)
        assert [(row.source_id, row.status) for row in rows] == expected
    assert store.get_reply_attempt(attempt_id).send_status == "failed"


def test_failed_oa_history_uses_verified_execution_while_followup_is_pending(tmp_path):
    store = AutoReplyStore(tmp_path / "oa-verified-followup-history.sqlite3")
    from tests.test_reviewed_candidate_store import _reviewed

    task, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    store.begin_candidate_action(execution["id"], "worker", 0, "oa-return-readback")
    store.record_candidate_external_action(
        execution["id"], "worker", 0, "oa-return-readback", "send",
        {"user_id": "x"}, {"readback": "returned"},
    )
    store.finish_candidate_execution(execution["id"], "worker", "done", {
        "summary": "OA return verified by readback",
    })
    with store._connect() as db:
        attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": task.conversation_id,
            "conversation_title": task.conversation_title,
            "trigger_message_id": task.trigger_message_id,
            "trigger_sender": task.trigger_sender,
            "trigger_text": task.trigger_text,
            "action": "agent_run",
            "sensitivity_kind": "general",
            "oa_process_instance_id": "approval-instance",
            "send_status": "failed",
            "send_error": "external_action_uncertain",
            "agent_run_id": review["audit_run_id"],
        }).lastrowid
        task_parts = task8_set_parts("reply_tasks", {
            "status": "pending",
            "error": "external_action_uncertain",
        })
        assert db.execute(
            f"update tasks set {task_parts.sql} where record_type='reply_tasks' and cast(legacy_key as integer)=?",
            (*task_parts.parameters, task.id),
        ).rowcount == 1

    pending_total, pending_rows = store.list_operation_logs_with_count(
        limit=20,
        statuses=("pending",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )
    failed_total, failed_rows = store.list_operation_logs_with_count(
        limit=20,
        statuses=("failed",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )
    uncertain_total, uncertain_rows = store.list_operation_logs_with_count(
        limit=20,
        statuses=("uncertain",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )

    assert [(row.source_id, row.status) for row in pending_rows] == [(attempt_id, "pending")]
    assert pending_total == 1
    assert failed_total == 0
    assert failed_rows == []
    assert uncertain_total == 0
    assert uncertain_rows == []
    assert store.get_reply_attempt(attempt_id).send_status == "failed"


def test_failed_oa_history_keeps_current_uncertain_execution_uncertain(tmp_path):
    store = AutoReplyStore(tmp_path / "oa-uncertain-execution-history.sqlite3")
    from tests.test_reviewed_candidate_store import _reviewed

    task, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    store.finish_candidate_execution(execution["id"], "worker", "uncertain", {
        "error": {"code": "external_action_uncertain"},
    })
    with store._connect() as db:
        attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": task.conversation_id,
            "conversation_title": task.conversation_title,
            "trigger_message_id": task.trigger_message_id,
            "trigger_sender": task.trigger_sender,
            "trigger_text": task.trigger_text,
            "action": "agent_run",
            "sensitivity_kind": "general",
            "oa_process_instance_id": "approval-instance",
            "send_status": "failed",
            "send_error": "external_action_uncertain",
            "agent_run_id": review["audit_run_id"],
        }).lastrowid
        task_parts = task8_set_parts("reply_tasks", {
            "status": "failed",
            "error": "external_action_uncertain",
        })
        assert db.execute(
            f"update tasks set {task_parts.sql} where record_type='reply_tasks' and cast(legacy_key as integer)=?",
            (*task_parts.parameters, task.id),
        ).rowcount == 1

    uncertain_total, uncertain_rows = store.list_operation_logs_with_count(
        limit=20,
        statuses=("uncertain",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )
    failed_total, failed_rows = store.list_operation_logs_with_count(
        limit=20,
        statuses=("failed",),
        history_types=(history_types.APPROVAL,),
        source_tables=("reply_attempts",),
    )

    assert [(row.source_id, row.status) for row in uncertain_rows] == [(attempt_id, "uncertain")]
    assert uncertain_total == 1
    assert failed_total == 0
    assert failed_rows == []
    assert store.get_reply_attempt(attempt_id).send_status == "failed"
