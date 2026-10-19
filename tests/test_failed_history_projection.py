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
