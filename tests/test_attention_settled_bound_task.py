import pytest

from app.action_storage import update_action
from app.store import AutoReplyStore
from tests.support.native_run_rows import insert_run_row
from tests.test_action_receipt_callers import _update_reply_task_fixture


@pytest.mark.parametrize("task_status", ["done", "skipped", "failed"])
def test_attention_follows_original_run_task_after_scanner_trigger_changes(tmp_path, task_status):
    store = AutoReplyStore(tmp_path / "attention.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="oa-chat", conversation_title="Approval",
        single_chat=False, trigger_message_id="original-card",
        trigger_create_time="2026-10-14 10:00:00", trigger_sender="Applicant",
        trigger_text="Review this approval", business_object_key="oa:process:node",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action="agent_run", sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        run_id = insert_run_row(db, {
            "task_id": task.id, "execution_generation": task.execution_generation,
            "role": "audit", "status": "completed",
        }).lastrowid
        update_action(db, "reply_attempts", attempt_id, {"agent_run_id": run_id})
        _update_reply_task_fixture(db, task.id, {"status": task_status})
        owner = db.execute(
            "select current_input_id, claimed_input_id from tasks where id=?",
            (task.id,),
        ).fetchone()
        input_id = owner["claimed_input_id"] or owner["current_input_id"]
        assert input_id is not None
        assert db.execute(
            "update task_inputs set trigger_message_id=? where id=? and task_id=?",
            ("updated-card", input_id, task.id),
        ).rowcount == 1
    original = store.get_reply_attempt(attempt_id)

    expected = [attempt_id] if task_status == "failed" else []
    assert [a.id for a in store.list_current_unresolved_problem_attempts()] == expected
    assert [int(a["id"]) for a in store.list_current_unresolved_problem_attempt_summaries()] == expected
    assert store.count_current_unresolved_problem_attempts() == len(expected)
    assert store.get_reply_attempt(attempt_id) == original
