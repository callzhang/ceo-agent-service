import app.agent_turn_runner as agent_turn_runner
from app.agent_turn_runner import AgentTurnProcess, _persist_provider_event
from app.store import AgentRole, AutoReplyStore


def test_runner_has_no_application_effect_recovery_policy_helpers():
    """Provider traces stay opaque; the runner has no effect recovery state machine."""

    obsolete_module_helpers = {
        "_has_unclosed_effects",
        "_closed_effect_failure",
        "_failed_agent_cli_read_event",
        "_matching_effect_metadata",
        "_trusted_claude_effect_event",
        "_attempted_skill_paths",
    }
    assert not obsolete_module_helpers.intersection(vars(agent_turn_runner))
    assert not hasattr(AgentTurnProcess, "_normalized_effect_event")
    assert not hasattr(AgentTurnProcess, "_require_direct_send_receipt")
    assert not hasattr(AgentTurnProcess, "_record_direct_send_receipt")


def test_provider_tool_event_does_not_create_application_delivery_receipt(tmp_path):
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
    expected_argv = [
        "dws",
        "chat",
        "message",
        "send",
        "--group",
        "cid-agent",
        "--content",
        "候选正文",
        "--yes",
    ]
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

    # Persisting a provider trace must not make the application infer that a
    # business delivery happened. The provider result is projected only via
    # its stable external action identity.
    store.append_agent_run_event(run.id, event, owner="audit")

    assert event["item"] == payload["item"]
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) is None
