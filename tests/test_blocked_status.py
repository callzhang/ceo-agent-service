from app.dingtalk_models import DingTalkMessage
from app.store import AgentRole, AutoReplyStore


def _message() -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=False,
        sender_name="Mina",
        create_time="2026-07-25 10:00:00",
        content="@Alex Chen 看一下",
    )


def test_recoverable_blocked_attempt_does_not_suppress_direct_agent_task(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger = _message()
    store.record_reply_attempt(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="blocked",
        sensitivity_kind="general",
        send_status="blocked",
    )

    store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    task = store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    assert task.status == "pending"
    assert store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{task.id}:{task.execution_generation}",
        owner="worker",
    ).claimed


def test_explained_blocked_attempt_remains_recoverable_and_rerun_is_idempotent(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger = _message()
    attempt_id = store.record_reply_attempt(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="blocked",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(
        attempt_id,
        send_error="blocked_unrecoverable_external_auth: not current user",
    )

    assert store.count_recoverable_blocked_reply_attempts() == 1

    original = store.enqueue_manual_rerun_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        attempt_id=attempt_id,
    )
    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        attempt_id=attempt_id,
    )

    assert rerun.id == original.id
    assert rerun.status == "pending"
    assert rerun.execution_generation == original.execution_generation
