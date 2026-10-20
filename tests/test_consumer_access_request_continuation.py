from app.agent_orchestrator import AgentOrchestrator
from app.agent_wire_contracts import consumer_wire_model
from app.consumer_purpose_retry import RETRY_REQUESTED
from app.system_executor import ActionOutcome, SystemExecutor
from test_agent_orchestrator import ScriptedAudit, ScriptedConsumer, task_and_context


def test_request_access_keeps_audit_required_noncontinuation_flag():
    result = consumer_wire_model("oa").model_validate({
        "action": "request_access",
        "reason": "Access is blocked and a request is needed.",
        "target": {"process_instance_id": "process-1"},
        "payload": {"content": "Please grant read access."},
        "evidence_refs": [],
        "summary": "Request access before reviewing the material.",
        "continue_after_execution": False,
        "error_code": "",
        "error_retryable": False,
        "error_authorization_required": False,
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "durable_memories": [],
    }).to_result()

    assert result.continue_after_execution is False


def test_verified_access_request_starts_wait_even_when_review_stage_must_stop(tmp_path):
    store, task, context = task_and_context(tmp_path)
    request = consumer_wire_model("oa").model_validate({
        "action": "request_access",
        "reason": "Access is blocked and a request is needed.",
        "target": {"process_instance_id": "process-1"},
        "payload": {"content": "Please grant read access."},
        "evidence_refs": [],
        "summary": "Request access before reviewing the material.",
        "continue_after_execution": False,
        "error_code": "",
        "error_retryable": False,
        "error_authorization_required": False,
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "durable_memories": [],
    }).to_result()
    consumer = ScriptedConsumer(store, request)
    audit = ScriptedAudit(store, "approve")

    class VerifiedRequestHandler:
        def dispatch(self, action, *, action_key, candidate):
            return ActionOutcome("verified", {"provider_receipt_id": action_key})

        def reconcile(self, action, *, action_key, candidate):
            return None

    handler = VerifiedRequestHandler()
    runtime = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        system_executor=SystemExecutor(
            store, {("dingtalk-oa", "comment"): handler}
        ),
    )

    result = runtime.process(task, context, refresh_context=lambda: context)

    assert result.error.code == RETRY_REQUESTED
    assert len(consumer.calls) == 1
    assert len(audit.calls) == 1
    assert store.list_verified_candidate_actions(task.id)
