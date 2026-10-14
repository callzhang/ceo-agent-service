"""Historical receipt correction preserves evidence and never permits resend."""

import json
from types import SimpleNamespace

import pytest

from app.agent_contracts import ConsumerAgentResult
from app.agent_orchestrator import AgentOrchestrator
from app.attempt_projection import project_attempt_status
from app.dingtalk_delivery_repair import apply_delivery_reverification
from app.system_executor import ActionOutcome, SystemExecutor
from tests.test_agent_orchestrator import ScriptedAudit, ScriptedConsumer, candidate, task_and_context
from tests.test_dingtalk_delivery_verification import reply_receipt


def historical_receipt(tmp_path):
    store, task, context = task_and_context(tmp_path)
    data = candidate().model_dump(mode="json")
    action = data["proposal"]["actions"][0]
    action.update(capability="dingtalk-chat", operation="reply_to_message",
                  target={"conversation_id": "conversation", "message_id": "trigger"},
                  payload={"content": "Exact prepared advice"})
    consumer = ScriptedConsumer(store, ConsumerAgentResult.model_validate(data))
    audit = ScriptedAudit(store, "approve")
    old_result = {"provider_result": reply_receipt(), "verification": {
        "state": "sent", "open_task_id": "", "status_result": {},
    }}

    class Handler:
        sends = 0
        reads = 0
        confirmed = False

        def dispatch(self, action, **kwargs):
            self.sends += 1
            return ActionOutcome("verified", old_result)

        def reconcile(self, action, **kwargs):
            self.reads += 1
            if self.confirmed:
                return ActionOutcome("verified", {"provider_result": reply_receipt(), "verification": {
                    "state": "sent", "open_task_id": "send-task-1",
                    "status_result": {"success": True, "result": {"sendStatus": "SUCCESS"}},
                }})
            return ActionOutcome("uncertain", {"reason": "task_not_exists"})

    handler = Handler()
    driver = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {("dingtalk-chat", "reply_to_message"): handler}, owner="worker"))
    assert driver.process(task, context, refresh_context=lambda: context).status == "executed"
    store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    execution = store.get_candidate_execution(reviewed["id"])
    attempt = store.list_candidate_action_attempts(execution["id"])[0]
    receipt = store.get_candidate_external_action(attempt["external_action_key"])
    return store, task, context, driver, handler, receipt, execution


def proof(receipt, state):
    return {"key": receipt["external_action_key"], "original_provider_json": receipt["provider_result_json"],
        "queried_at": "2026-10-14T02:00:00Z", "verification": {
            "state": state, "open_task_id": "send-task-1",
            "status_result": {"success": True, "result": {"sendStatus": "SUCCESS"}} if state == "sent" else {},
        }}


def test_failed_current_task_does_not_inherit_historical_completion():
    assert project_attempt_status(SimpleNamespace(send_status="completed"),
        SimpleNamespace(status="failed", execution_generation="initial"), []) == "failed"


@pytest.mark.parametrize("state", ["sent", "ambiguous"])
def test_reverification_keeps_original_provider_and_previous_verifier(tmp_path, state):
    store, task, _context, _driver, _handler, receipt, execution = historical_receipt(tmp_path)
    evidence = proof(receipt, state)
    assert apply_delivery_reverification(store, evidence)
    assert not apply_delivery_reverification(store, evidence)
    updated = json.loads(store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"])
    original = json.loads(receipt["provider_result_json"])
    assert updated["provider_result"] == original["provider_result"]
    assert updated["verification_repair"]["previous_verification"] == original["verification"]
    assert updated["verification"] == evidence["verification"]
    assert store.get_reply_task(task.id).status == ("done" if state == "sent" else "failed")
    assert store.get_candidate_execution(execution["id"])["status"] == ("done" if state == "sent" else "uncertain")
    assert all(run.status == "completed" for run in store.list_agent_runs_for_task_generation(task.id, task.execution_generation, load_events=False))


def test_uncertain_historical_receipt_is_reconciled_without_dispatch(tmp_path):
    store, task, context, driver, handler, receipt, _execution = historical_receipt(tmp_path)
    apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.error.code == "external_action_uncertain"
    assert (handler.sends, handler.reads) == (1, 1)


def test_reverification_rejects_other_task_without_mutation(tmp_path):
    store, _task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    evidence = proof(receipt, "sent")
    evidence["verification"]["open_task_id"] = "different-task"
    with pytest.raises(ValueError, match="identity mismatch"):
        apply_delivery_reverification(store, evidence)
    assert store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"] == receipt["provider_result_json"]


def test_corrected_uncertain_receipt_can_later_complete_from_readback(tmp_path):
    store, task, context, driver, handler, receipt, _execution = historical_receipt(tmp_path)
    apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    handler.confirmed = True
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert (handler.sends, handler.reads) == (1, 1)
    saved = json.loads(store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"])
    assert saved["verification"]["state"] == "sent"
    assert saved["verification_repair"]["previous_verification"]["state"] == "sent"


def test_actual_delivery_failure_is_not_reclassified_as_unknown(tmp_path):
    store, task, _context, _driver, _handler, receipt, execution = historical_receipt(tmp_path)
    apply_delivery_reverification(store, proof(receipt, "failed"))
    assert store.get_candidate_execution(execution["id"])["status"] == "failed"
    assert store.get_reply_task(task.id).error == "external_action_failed"
