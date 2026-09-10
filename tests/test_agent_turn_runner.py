import inspect
import json
from types import SimpleNamespace

import app.agent_turn_runner as agent_turn_runner
from app.agent_contracts import ConsumerAgentResult
from app.agent_turn_runner import (
    AgentTurnProcess,
    _decode_runtime_domain_result,
    _encode_runtime_domain_result,
    _persist_provider_event,
)
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


def test_runner_wire_contract_has_no_application_recovery_evidence_fields():
    execute_parameters = inspect.signature(AgentTurnProcess.execute).parameters
    assert not {
        "recovery_phase",
        "authorized_recovery_actions",
        "recovery_authorizations",
        "allow_effectful_tools",
        "required_skill_receipts",
    }.intersection(execute_parameters)

    result = ConsumerAgentResult.model_validate(
        {
            "outcome": "no_action",
            "summary": "无需处理",
            "proposal": None,
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
            "risk": "low",
            "confidence": 1.0,
        }
    )
    encoded = _encode_runtime_domain_result(
        schema_id="schema-v1",
        role=AgentRole.CONSUMER,
        result=result,
    )
    assert set(json.loads(encoded)) == {"schema_id", "version", "role", "result"}
    decoded = _decode_runtime_domain_result(
        encoded,
        schema_id="schema-v1",
        role=AgentRole.CONSUMER,
    )
    assert decoded.result == result


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


def test_runtime_failure_persists_bounded_redacted_detail():
    store_calls: list[tuple[int, dict[str, object]]] = []

    class Store:
        def get_agent_run(self, run_id: int):
            assert run_id == 7
            return SimpleNamespace(status="running")

        def fail_agent_run(self, run_id: int, structured_error, *, owner: str):
            store_calls.append((run_id, structured_error))
            assert owner == "test-owner"

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()
    runner.owner = "test-owner"

    runner._fail_running(
        SimpleNamespace(id=7),
        "codex_process_failed",
        detail=agent_turn_runner._runtime_failure_detail(
            RuntimeError("database is locked while reading /tmp/private-output")
        ),
        stage="execution",
        source="codex",
        source_code="codex_process_failed",
        session_continuable=True,
    )

    assert len(store_calls) == 1
    payload = store_calls[0][1]
    assert payload["code"] == "codex_process_failed"
    assert payload["stage"] == "execution"
    assert payload["source_code"] == "codex_process_failed"
    assert "database is locked" in payload["detail"]
    assert "/tmp/" not in payload["detail"]


def test_runtime_failure_detail_keeps_wrapped_validation_reason():
    try:
        try:
            raise ValueError("invalid AgentEnvelope: missing user_response")
        except ValueError as cause:
            raise RuntimeError("runtime_result_validation_failed") from cause
    except RuntimeError as exc:
        detail = agent_turn_runner._runtime_failure_detail(exc)

    assert "invalid AgentEnvelope: missing user_response" in detail
    assert "runtime_result_validation_failed" in detail
