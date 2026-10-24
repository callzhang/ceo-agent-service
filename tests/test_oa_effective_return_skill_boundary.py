from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_wire_contracts import AuditAgentWireResult
from app.audit_agent import validate_audit_candidate_fields


def _candidate() -> ConsumerAgentResult:
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal",
        "summary": "Reject the org-structure request after the same gap remains unresolved.",
        "proposal": {
            "objective": "Review this org-structure request",
            "actions": [{
                "description": "Reject after three effective returns for the same gap",
                "action_identity": "oa-reject",
                "capability": "dingtalk-oa",
                "operation": "reject",
                "target": {"process_instance_id": "process-1", "task_id": "task-1"},
                "payload": {"remark": "同一实质缺口经过三次有效退回仍未解决，按规则拒绝。"},
            }],
            "sourced_facts": [],
            "authored_judgment": "The source evidence and Skill support the decision.",
        },
        "decision_options": [],
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "medium",
        "confidence": 0.9,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "stage_index": 0,
        "predecessor_review_id": None,
    })


def test_same_gap_threshold_is_reviewed_under_skill_without_extra_audit_wire_gate() -> None:
    candidate = _candidate()
    result = AuditAgentResult.model_validate({
        "outcome": "approve",
        "summary": "The current readable evidence supports the proposed decision under the selected Skill.",
        "proposal_revision": 0,
        "candidate_digest": "a" * 64,
        "evidence_refs": ["dws:current-process", "dws:operation-history"],
        "feedback": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "medium",
        "confidence": 0.9,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })

    validate_audit_candidate_fields(result, candidate)

    assert "oa_effective_return_assessment" not in AuditAgentResult.model_fields
    assert "oa_effective_return_assessment" not in AuditAgentWireResult.model_json_schema()["$defs"]["_AuditWire"]["properties"]
