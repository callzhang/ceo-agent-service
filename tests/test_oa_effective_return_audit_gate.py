import pytest
from pydantic import ValidationError

from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_result import ResultParseError
from app.agent_context import _AUDIT_AGENT_RULES
from app.audit_agent import validate_audit_candidate_fields


def _candidate(remark: str) -> ConsumerAgentResult:
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal",
        "summary": "Reject the org-structure request",
        "proposal": {
            "objective": "Review this org-structure request",
            "actions": [{
                "description": "Reject the OA request",
                "action_identity": "oa-reject",
                "capability": "dingtalk-oa",
                "operation": "reject",
                "target": {"process_instance_id": "process-1", "task_id": "task-1"},
                "payload": {"remark": remark},
            }],
            "sourced_facts": [],
            "authored_judgment": "The request is unresolved",
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


def _assessment(*, cured_round: int | None = None) -> dict[str, object]:
    rounds = []
    for index, day in enumerate(("01", "02", "03"), 1):
        response_ref = f"dws:reply-{index}"
        rounds.append({
            "return_ref": f"dws:return-{index}",
            "return_time": f"2026-01-{day}T10:00:00Z",
            "same_gap": "missing receiving-team and reporting-line facts",
            "actionable_request": "State the receiving team and reporting line.",
            "responses": [{
                "source_ref": response_ref,
                "source_time": f"2026-01-{day}T11:00:00Z",
                "response_type": "applicant_reply",
                "substantive_summary": "The reply does not identify the receiving team.",
                "cures_same_gap": index == cured_round,
            }],
            "same_gap_unresolved_after_response": index != cured_round,
        })
    return {
        "rounds": rounds,
        "current_gap_unresolved": True,
        "current_evidence_ref": "dws:current-form",
        "current_evidence_time": "2026-01-04T09:00:00Z",
        "current_unresolved_basis": "The current form still has no receiving-team field value.",
    }


def _approve(evidence_refs: list[str] | None = None, *, assessment=None) -> AuditAgentResult:
    return AuditAgentResult.model_validate({
        "outcome": "approve",
        "summary": "The rejection candidate is supported",
        "proposal_revision": 0,
        "candidate_digest": "a" * 64,
        "evidence_refs": evidence_refs or [],
        "oa_effective_return_assessment": assessment,
        "feedback": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "medium",
        "confidence": 0.9,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })


def test_audit_cannot_approve_threshold_rejection_without_round_ledger() -> None:
    candidate = _candidate(
        "同一实质缺口经过三次有效退回仍未解决，按规则拒绝。"
    )

    with pytest.raises(ResultParseError, match="three effective return rounds"):
        validate_audit_candidate_fields(_approve(), candidate)


def test_ordinary_oa_rejection_does_not_require_repeat_return_ledger() -> None:
    candidate = _candidate("申请与可核验的业务事实直接冲突，按通常审批标准拒绝。")

    validate_audit_candidate_fields(_approve(["dws:current-fact"]), candidate)


def test_valid_three_round_ledger_allows_threshold_review_to_be_approved() -> None:
    candidate = _candidate("Same gap remains unresolved after three effective returns.")
    refs = ["dws:current-form"]
    for index in range(1, 4):
        refs.extend((f"dws:return-{index}", f"dws:reply-{index}"))

    validate_audit_candidate_fields(_approve(refs, assessment=_assessment()), candidate)


def test_audit_cannot_approve_threshold_rejection_when_any_round_cured_gap() -> None:
    candidate = _candidate("Same gap remains unresolved after three effective returns.")
    refs = ["dws:current-form"]
    for index in range(1, 4):
        refs.extend((f"dws:return-{index}", f"dws:reply-{index}"))

    with pytest.raises(ResultParseError, match="same gap was cured"):
        validate_audit_candidate_fields(
            _approve(refs, assessment=_assessment(cured_round=2)), candidate,
        )


def test_audit_cannot_approve_threshold_rejection_with_unbound_round_citations() -> None:
    candidate = _candidate("Same gap remains unresolved after three effective returns.")

    with pytest.raises(ResultParseError, match="evidence_refs"):
        validate_audit_candidate_fields(
            _approve(["dws:current-form"], assessment=_assessment()), candidate,
        )


def test_audit_prompt_requires_round_ledger_for_threshold_based_oa_rejection() -> None:
    assert "oa_effective_return_assessment" in _AUDIT_AGENT_RULES
    assert "every source-timestamped post-return applicant reply" in _AUDIT_AGENT_RULES
    assert "If any response cured the gap" in _AUDIT_AGENT_RULES


def test_round_ledger_rejects_response_timestamp_before_return() -> None:
    assessment = _assessment()
    assessment["rounds"][1]["responses"][0]["source_time"] = "2026-01-02T09:00:00Z"

    with pytest.raises(ValidationError, match="post-return response source_time precedes its return"):
        AuditAgentResult.model_validate({
            **_approve().model_dump(mode="json"),
            "oa_effective_return_assessment": assessment,
        })


def test_round_ledger_requires_complete_readback_when_no_response_was_found() -> None:
    assessment = _assessment()
    assessment["rounds"][0]["responses"] = []
    assessment["rounds"][0]["no_response_readback_ref"] = None

    with pytest.raises(ValidationError, match="requires a complete no-response readback"):
        AuditAgentResult.model_validate({
            **_approve().model_dump(mode="json"),
            "oa_effective_return_assessment": assessment,
        })


def test_round_ledger_rejects_different_gaps_across_returns() -> None:
    assessment = _assessment()
    assessment["rounds"][2]["same_gap"] = "a different missing fact"

    with pytest.raises(ValidationError, match="same substantive gap"):
        AuditAgentResult.model_validate({
            **_approve().model_dump(mode="json"),
            "oa_effective_return_assessment": assessment,
        })
