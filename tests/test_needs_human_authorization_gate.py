"""Technical authorization failures never become current-instance business choices."""
import pytest
from pydantic import ValidationError

from app.agent_contracts import AuditAgentResult, ConsumerAgentResult


def _business_choice():
    fact = {"assertion": "The current OA task is open.", "references": ["task:1"]}
    return {
        "outcome": "needs_human",
        "summary": "Derek must choose whether to return this OA task.",
        "proposal": None,
        "decision_options": [
            {
                "key": "return", "label": "Return this task",
                "instruction": "Return this one OA task.",
                "consequence": "This task returns to its supervisor.",
                "plan": {
                    "objective": "Return the current task to its supervisor.",
                    "actions": [{
                        "description": "Return the current OA task.",
                        "action_identity": "return-current-oa-task",
                        "capability": "dingtalk-oa", "operation": "revert_task",
                        "target": {"process_instance_id": "process-1", "task_id": "task-1",
                                   "target_activity_id": "supervisor"},
                        "payload": {"revert_action": "REDIRECT_PROCESS", "remark": "Review again."},
                    }],
                    "sourced_facts": [fact], "authored_judgment": "The current task can be returned.",
                },
            },
            {
                "key": "stop", "label": "Leave this task unchanged",
                "instruction": "Stop this instance.", "consequence": "No external action occurs.",
                "terminal_outcome": "skipped", "reason": "Derek chose to leave the task unchanged.",
            },
        ],
        "risk": "high", "confidence": 0.8, "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "needs_human_reason": "Only Derek can choose the treatment of this instance.",
        "decision_basis": {
            "verified_facts": [fact], "rule_evidence": [fact],
            "quality_explanation": "The facts are complete; the business preference is missing.",
            "no_external_action_evidence": [fact], "conclusion": "Ask Derek about this task.",
        },
    }


@pytest.mark.parametrize("error", [
    {"code": "PAT_HIGH_RISK_NO_PERMISSION", "authorization_required": True},
    {"code": "authorization_required", "authorization_required": True},
    {"code": "confirmation_required", "authorization_required": False},
    {"code": "", "authorization_required": True},
    {"code": "", "retryable": True},
])
def test_technical_authorization_failure_cannot_forge_a_business_question(error):
    payload = _business_choice()
    payload["error"].update(error)
    with pytest.raises(ValidationError, match="cannot carry a technical error"):
        ConsumerAgentResult.model_validate(payload)


def test_audit_cannot_issue_a_human_question_even_with_complete_choices():
    payload = _business_choice()
    payload.pop("proposal")
    payload.update(proposal_revision=0, candidate_digest="a" * 64, feedback=None)
    with pytest.raises(ValidationError) as raised:
        AuditAgentResult.model_validate(payload)
    assert any(error["loc"] == ("outcome",) for error in raised.value.errors())


def test_business_choice_carries_complete_plans_without_authorization_plan():
    result = ConsumerAgentResult.model_validate(_business_choice())
    assert result.decision_options[0].plan.actions[0].target["task_id"] == "task-1"
    assert result.decision_options[1].terminal_outcome == "skipped"
    assert "authorization_plan" not in result.model_dump(mode="json")


def test_current_instance_choice_rejects_the_obsolete_authorization_plan():
    payload = _business_choice()
    payload["authorization_plan"] = {"summary": "Allow this action"}
    with pytest.raises(ValidationError, match="authorization_plan"):
        ConsumerAgentResult.model_validate(payload)


def test_business_question_requires_complete_branches_even_with_high_scores():
    payload = _business_choice()
    payload["confidence"] = 1.0
    payload["decision_options"][0].pop("plan")
    with pytest.raises(ValidationError, match="exactly one plan or terminal outcome"):
        ConsumerAgentResult.model_validate(payload)
