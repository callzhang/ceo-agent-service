import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_result import AgentError
from app.agent_contracts import ConsumerAgentResult
from app.email_unsubscribe_consumer import (
    EmailUnsubscribeConsumerResult,
    EmailUnsubscribeConsumerRunner,
    parse_email_unsubscribe_agent_turn_result,
)


def _task() -> SimpleNamespace:
    payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
        "action_type": "unsubscribe",
        "action_identity": "email-action:unsubscribe-1",
        "action_plan_id": "email-plan:1",
        "action_plan_version": 1,
        "classification_id": 1,
        "account_id": "account-1",
        "stable_message_identity": "account-1:message-id:<mail@example.com>",
        "thread_identity": "thread-1",
        "category": "subscription",
        "classification_source": "user",
        "confidence": 1.0,
        "model_id": "email-model:test",
        "config_version": "email-config:test",
        "action_parameters": {},
        "unsubscribe_entries": [
            {"reference": "unsubscribe-entry:opaque", "priority": 0, "source": "header_https"}
        ],
        "unsubscribe_authentication": None,
        "unsubscribe_network_policy_reference": "network-policy:test",
        "unsubscribe_network_policy_origin_references": ["network-origin:test"],
    }
    return SimpleNamespace(
        id=7,
        execution_generation="generation-1",
        channel="email",
        conversation_id="email-thread:1",
        trigger_message_id=payload["action_identity"],
        trigger_message_json=json.dumps(payload),
    )


def _context(task: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task.id,
        channel="email",
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        trigger_raw_payload=json.loads(task.trigger_message_json),
    )


def _result(outcome: str) -> EmailUnsubscribeConsumerResult:
    return EmailUnsubscribeConsumerResult(
        outcome=outcome,
        summary=f"Consumer decided {outcome}.",
        error=AgentError(),
    )


def test_no_action_finishes_without_operation():
    calls = []
    runner = EmailUnsubscribeConsumerRunner(
        decide=lambda _task, _context: _result("no_action"),
        execute=lambda **kwargs: calls.append(kwargs),
    )

    result = runner.run(_task(), _context(_task()))

    assert result.outcome == "no_action"
    assert result.receipt_id == ""
    assert calls == []


def test_execute_calls_exactly_one_task_bound_operation_and_returns_receipt():
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        return {
            "status": "done",
            "receipt_id": "unsubscribe-receipt:7",
            "summary": "Terminal page confirmed unsubscribe.",
        }

    runner = EmailUnsubscribeConsumerRunner(
        decide=lambda _task, _context: _result("execute"),
        execute=execute,
    )
    task = _task()

    result = runner.run(task, _context(task))

    assert result.outcome == "executed"
    assert result.receipt_id == "unsubscribe-receipt:7"
    assert result.summary == "Terminal page confirmed unsubscribe."
    assert calls == [{"task_id": 7, "execution_generation": "generation-1"}]


def test_task_or_context_identity_mutation_is_rejected_before_operation():
    calls = []
    task = _task()
    context = _context(task)
    context.render = lambda: "complete email context"

    def decide(_task, current_context):
        current_context.trigger_raw_payload["action_plan_id"] = "email-plan:forged"
        return _result("execute")

    runner = EmailUnsubscribeConsumerRunner(
        decide=decide,
        execute=lambda **kwargs: calls.append(kwargs),
    )

    result = runner.run(task, context)

    assert result.outcome == "failed"
    assert result.error.code == "unsubscribe_identity_changed"
    assert calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda task: setattr(task, "trigger_message_id", "forged-action"),
        lambda task: setattr(task, "execution_generation", "forged-generation"),
    ],
)
def test_task_mutation_is_rejected_before_operation(mutation):
    task = _task()
    calls = []

    def decide(current_task, _context):
        mutation(current_task)
        return _result("execute")

    result = EmailUnsubscribeConsumerRunner(
        decide=decide,
        execute=lambda **kwargs: calls.append(kwargs),
    ).run(task, _context(task))

    assert result.outcome == "failed"
    assert result.error.code == "unsubscribe_identity_changed"
    assert calls == []


def test_operation_failure_is_strictly_projected_without_audit_result():
    runner = EmailUnsubscribeConsumerRunner(
        decide=lambda _task, _context: _result("execute"),
        execute=lambda **_kwargs: {
            "status": "failed",
            "receipt_id": "",
            "summary": "Provider rejected the operation.",
            "error": {"code": "provider_failed", "retryable": True},
        },
    )

    result = runner.run(_task(), _context(_task()))

    assert result.outcome == "failed"
    assert result.error.code == "provider_failed"
    assert result.error.retryable is True


def test_result_and_payload_are_strict():
    with pytest.raises(ValueError):
        EmailUnsubscribeConsumerResult(
            outcome="execute",
            summary="execute",
            error=AgentError(),
            unexpected="not-allowed",
        )


def test_agent_turn_adapter_keeps_shared_consumer_result_contract():
    result = parse_email_unsubscribe_agent_turn_result(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "outcome": "executed",
                                    "summary": "The provider page confirmed completion.",
                                    "receipt_id": "unsubscribe-receipt:7",
                                    "error": {},
                                }
                            ),
                        }
                    ],
                },
            }
        )
    )

    assert isinstance(result, ConsumerAgentResult)
    assert result.outcome.value == "no_action"
    assert result.summary.startswith("email-unsubscribe-direct-executed:")
    assert "unsubscribe-receipt:7" in result.summary


def test_real_consumer_runner_uses_agent_turn_and_verifies_durable_receipt(
    monkeypatch, tmp_path
):
    import app.email_unsubscribe_consumer as module

    task = _task()
    context = _context(task)
    calls = []
    context.render = lambda: "complete email context"

    class Store:
        path = Path(tmp_path / "worker.sqlite3")

        def codex_session_lock(self, *_args):
            return nullcontext()

        def next_agent_run_turn_attempt(self, *args, **kwargs):
            return 0

        def claim_agent_run(self, *args, **kwargs):
            return SimpleNamespace(claimed=True, run=SimpleNamespace(id=12))

    class FakeProcess:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, **_kwargs):
            pass

        def execute(self, **kwargs):
            command = []
            kwargs["configure_command"](command)
            calls.append((command, kwargs["parse_result"]))
            return module.AgentTurnRunResult(
                run_id=12,
                result=module.parse_email_unsubscribe_agent_turn_result(
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": json.dumps(
                                            {
                                                "outcome": "executed",
                                                "summary": "model text is not authoritative",
                                                "receipt_id": "unsubscribe-receipt:12",
                                                "error": {},
                                            }
                                        ),
                                    }
                                ],
                            },
                        }
                    )
                ),
                transcript_start_line=0,
                transcript_end_line=1,
            )

    monkeypatch.setattr(module, "AgentTurnProcess", FakeProcess)
    runner = module.EmailUnsubscribeConsumerAgentRunner(
        store=Store(),
        workspace=tmp_path,
        receipt_loader=lambda identity: {
            "action_identity": identity,
            "receipt_id": "unsubscribe-receipt:12",
            "result_text": "Terminal page text.",
        },
    )

    result = runner.run(task, context)

    assert result.outcome == "executed"
    assert result.summary == "Terminal page text."
    assert result.receipt_id == "unsubscribe-receipt:12"
    assert "execute_email_unsubscribe" in json.dumps(calls[0][0])
