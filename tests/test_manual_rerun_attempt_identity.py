import json

from app.store import AutoReplyStore


def test_oa_manual_rerun_uses_latest_input_and_validates_original_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "oa-rerun-latest-input.sqlite3")
    process_id = "proc-latest-input"
    oa_task_id = "task-latest-input"
    conversation_id = f"oa_pending_scan:{process_id}"
    business_object_key = f"oa:{process_id}:{oa_task_id}"
    oa_url = (
        "https://aflow.dingtalk.com/detail?"
        f"procInstId={process_id}&taskId={oa_task_id}"
    )
    source_trigger = "scan-v1"
    current_trigger = "scan-v2"

    original_task = store.ensure_reply_task(
        conversation_id=conversation_id,
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=source_trigger,
        trigger_create_time="2026-11-05T10:00:00Z",
        trigger_sender="Derek OA",
        trigger_text="Review this OA process.",
        trigger_message_json=json.dumps({"message_id": source_trigger}),
        channel="dingtalk",
        oa_url=oa_url,
        business_object_key=business_object_key,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=conversation_id,
        conversation_title="审批待办",
        trigger_message_id=source_trigger,
        trigger_sender="Derek OA",
        trigger_text="Review this OA process.",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id=process_id,
        oa_task_id=oa_task_id,
        oa_url=oa_url,
        oa_action="review",
        oa_action_result_json="{}",
        send_status="needs_human",
        channel="dingtalk",
    )
    current_task = store.ensure_reply_task(
        conversation_id=conversation_id,
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=current_trigger,
        trigger_create_time="2026-11-05T10:05:00Z",
        trigger_sender="Derek OA",
        trigger_text="Review the current OA scan.",
        trigger_message_json=json.dumps({"message_id": current_trigger}),
        channel="dingtalk",
        oa_url=oa_url,
        business_object_key=business_object_key,
    )

    assert current_task.id == original_task.id
    assert current_task.trigger_message_id == current_trigger
    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id=conversation_id,
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=current_trigger,
        source_attempt_trigger_message_id=source_trigger,
        trigger_create_time="2026-11-05T10:05:00Z",
        trigger_sender="Derek OA",
        trigger_text="Review the current OA scan.",
        trigger_message_json=json.dumps({"message_id": current_trigger}),
        oa_url=oa_url,
        attempt_id=attempt_id,
        channel="dingtalk",
        expected_execution_generation=original_task.execution_generation,
    )

    assert rerun.id == original_task.id
    assert rerun.trigger_message_id == current_trigger
    assert rerun.manual_rerun_attempt_id == attempt_id
