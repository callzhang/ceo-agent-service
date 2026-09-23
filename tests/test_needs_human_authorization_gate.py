import pytest
from pydantic import ValidationError

from app.agent_contracts import AuditAgentResult, ConsumerAgentResult


def _payload(model, *, confidence: float, error_code: str):
    action = {
        "description": "Return the current OA task to its supervisor.",
        "action_identity": "return-current-oa-task",
        "capability": "dingtalk-oa",
        "operation": "return_task",
        "target": {"oa_process_instance_id": "process-1", "oa_task_id": "task-1"},
        "payload": {},
        "effect": "external",
    }
    result = {
        "outcome": "needs_human",
        "summary": "A concrete authorization decision is required.",
        "decision_options": [
            {
                "key": "one_time",
                "label": "Allow this instance",
                "instruction": "Return this one OA task.",
                "consequence": "This OA task is returned to its supervisor.",
            },
            {
                "key": "skill_rule",
                "label": "Define a reusable rule",
                "instruction": "Add an explicit matching rule to the approval Skill.",
                "consequence": "Future matching approvals follow that rule.",
            },
        ],
        "risk": "high",
        "confidence": confidence,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "error": {
            "code": error_code,
            "retryable": False,
            "authorization_required": True,
        },
        "needs_human_reason": "The exact external action requires an explicit boundary.",
        "decision_basis": {
            "verified_facts": [
                {"assertion": "The current task is open.", "references": ["task:1"]}
            ],
            "rule_evidence": [
                {"assertion": "A matching authorization rule applies.", "references": ["skill:approval"]}
            ],
            "quality_explanation": "The facts and current rule were checked.",
            "no_external_action_evidence": [
                {"assertion": "No provider action has occurred.", "references": ["attempt:1"]}
            ],
            "conclusion": "Only the bounded action is awaiting a decision.",
        },
        "authorization_plan": {
            "summary": action["description"],
            "primary_action": action,
            "follow_up_actions": [],
            "side_effects": ["The task is returned to its supervisor."],
            "will_not_do": ["The OA will not be approved or rejected."],
            "readback": ["Read the OA history after the action."],
        },
    }
    if model is ConsumerAgentResult:
        result["proposal"] = None
    else:
        result.update(proposal_revision=0, feedback=None, external_result=None)
    return result


@pytest.mark.parametrize("model", (ConsumerAgentResult, AuditAgentResult))
def test_authorization_boundary_does_not_bypass_needs_human_quality_thresholds(model):
    payload = _payload(model, confidence=0.8, error_code="authorization_required")

    with pytest.raises(ValidationError, match="decision quality"):
        model.model_validate(payload)


@pytest.mark.parametrize("model", (ConsumerAgentResult, AuditAgentResult))
def test_only_generic_authorization_error_can_use_authorization_needs_human(model):
    payload = _payload(model, confidence=0.49, error_code="confirmation_required")

    with pytest.raises(ValidationError, match="authorization_required"):
        model.model_validate(payload)


@pytest.mark.parametrize("model", (ConsumerAgentResult, AuditAgentResult))
def test_authorization_plan_requires_applicable_rule_coverage(model):
    payload = _payload(model, confidence=0.3, error_code="authorization_required")
    payload["rule_coverage"] = 0.3

    with pytest.raises(ValidationError, match="complete information and rule coverage"):
        model.model_validate(payload)
