from app.agent_turn_runner import AgentTurnProcess, _persist_provider_event
from app.service_message_sender import ServiceMessageSender, agent_message_delivery_key
from app.store import AgentRole, AutoReplyStore


def test_direct_send_receipt_records_prepared_final_body_not_provider_argv(tmp_path):
    store = AutoReplyStore(tmp_path / "agent-turn.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-agent",
        conversation_title="Agent receipt",
        single_chat=False,
        trigger_message_id="msg-agent",
        trigger_create_time="2026-09-05 10:00:00",
        trigger_sender="Derek",
        trigger_text="请处理",
        execution_generation="generation-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="audit-1",
        owner="audit",
    ).run
    delivery_key = agent_message_delivery_key(
        business_object_key=task.business_object_key,
        action_identity="send-result",
    )
    prepared = ServiceMessageSender(store=store).prepare(
        channel="dingtalk",
        delivery_key=delivery_key,
        body="候选正文",
        original_text=task.trigger_text,
    )
    expected_argv = [
        "dws", "chat", "message", "send", "--group", "cid-agent",
        "--content", prepared.final_body, "--yes",
    ]
    process = AgentTurnProcess(store=store, task=task, workspace=tmp_path, owner="audit")

    payload = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "id": "write-1",
            "server": "agent_cli",
            "tool": "execute_reviewed_write",
            "arguments": {"authorization_id": "authorization-1", "argv": expected_argv},
            "result": {"content": [{"type": "text", "text": "sent"}]},
        },
    }
    event = _persist_provider_event(payload)
    assert event is not None

    process._record_direct_send_receipt(
        event,
        payload,
        run=run,
        expected_effect_actions=(
            {
                "argv": expected_argv,
                "delivery_key": delivery_key,
                "external_action_key": "external-send-result",
                "target_identifiers": {"group": "cid-agent"},
            },
        ),
    )

    receipt = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    assert receipt is not None
    assert receipt.reply_text == prepared.final_body
