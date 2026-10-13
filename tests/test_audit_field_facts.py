import json

import pytest

from app import audit_agent
from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_result import ResultParseError
from app.agent_wire_contracts import parse_audit_agent_wire_result
from app.reviewed_candidates import candidate_digest


def candidate():
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": "Review exact return", "proposal": {
            "objective": "Return missing material", "actions": [{
                "description": "Return", "action_identity": "return-original-node",
                "capability": "dingtalk-oa", "operation": "revert_task",
                "target": {"process_instance_id": "process", "task_id": "123",
                           "target_activity_id": "handover"},
                "payload": {"remark": "Need handover facts", "revert_action": "REVERT_FOR_APPROVAL"},
            }], "sourced_facts": [], "authored_judgment": "Material gap",
        }, "decision_options": [], "error": {"code": "", "retryable": False,
            "authorization_required": False}, "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
    })


def context(value):
    return AuditTurnContext(task=AgentTaskContext(task_id=1, channel="dingtalk",
        conversation_id="oa", conversation_title="OA", single_chat=True,
        trigger_message_id="original", trigger_sender="Derek", trigger_text="Review",
        trigger_create_time="2026-10-13 10:00:00", messages=(), materials=(), prior_receipts=()),
        proposal_revision=0, operation_id="operation", candidate=value,
        candidate_digest=candidate_digest(value), audit_rules="Review current business facts")


def review(value, assertion):
    return AuditAgentResult.model_validate({
        "outcome": "return", "summary": "Field needs correction", "proposal_revision": 0,
        "candidate_digest": candidate_digest(value), "evidence_refs": [],
        "feedback": {"rule": "Field contract", "observation": "Current field type",
            "requested_revision": "Correct the field", "observation_basis": "candidate_fields",
            "candidate_field_assertions": [assertion]},
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0, "information_completeness": 1.0,
    })


def test_rendered_field_facts_preserve_quoted_identifier_type():
    rendered = context(candidate()).render()
    assert "## Candidate Field Facts" in rendered
    section = rendered.split("## Candidate Field Facts\n", 1)[1].split("\n\n", 1)[0]
    facts = json.loads(section)
    assert {"path": ["proposal", "actions", 0, "target", "task_id"], "json_type": "string"} in facts


@pytest.mark.parametrize("path,value,json_type", [
    (["proposal", "actions", 0, "target", "task_id"], 123, "number"),
    (["proposal", "actions", 0, "target", "task_id"], "different", "string"),
    (["proposal", "actions", 0, "target", "missing"], "123", "string"),
    (["proposal", "actions", -1, "target", "task_id"], "123", "string"),
])
def test_false_candidate_field_assertion_is_audit_correction_not_business_return(path, value, json_type):
    original = candidate()
    validate = getattr(audit_agent, "validate_audit_candidate_fields", None)
    assert callable(validate), "Audit must validate field assertions against the bound candidate"
    result = review(original, {"path": path, "value": value, "json_type": json_type})
    with pytest.raises(ResultParseError, match="Audit candidate field assertion"):
        validate(result, original)
    assert original.proposal.actions[0].target["task_id"] == "123"


def test_true_assertion_preserves_review_without_approving_action():
    original = candidate()
    validate = getattr(audit_agent, "validate_audit_candidate_fields", None)
    assert callable(validate)
    result = review(original, {"path": ["proposal", "actions", 0, "target", "task_id"],
        "value": "123", "json_type": "string"})
    validate(result, original)
    assert result.outcome.value == "return"


def test_new_native_feedback_cannot_omit_its_observation_classification():
    payload = review(candidate(), {"path": ["proposal", "actions", 0, "target", "task_id"],
        "value": "123", "json_type": "string"}).model_dump(mode="json")
    payload.pop("error")
    payload.update(error_code="", error_retryable=False, error_authorization_required=False)
    payload["feedback"].pop("observation_basis")
    payload["feedback"].pop("candidate_field_assertions")
    payload["feedback"]["observation"] = "The quoted identifier is number 123"
    native_output = json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(payload)}})
    with pytest.raises(ResultParseError):
        parse_audit_agent_wire_result(native_output)
