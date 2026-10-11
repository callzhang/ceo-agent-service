"""Synthetic, read-only contract probes for the frozen v1 manifest.

These probes compare Python behavior under two source roots. They do not call a
model or a live provider, and they do not grade business judgment.
"""

from __future__ import annotations

import argparse
import inspect
import json


def _proposal(text="body"):
    from app.agent_contracts import ConsumerAgentResult
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": "synthetic", "proposal": {
            "objective": "synthetic", "actions": [{
                "description": "synthetic", "action_identity": "notice",
                "capability": "test", "operation": "act", "target": {"id": "object-1"},
                "payload": {"body": text}, "effect": "external",
            }], "sourced_facts": [], "authored_judgment": "synthetic",
        },
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })


def _human():
    from app.agent_contracts import ConsumerAgentResult
    plan = _proposal().proposal.model_dump(mode="json")
    return ConsumerAgentResult.model_validate({
        "outcome": "needs_human", "summary": "synthetic", "proposal": None,
        "decision_options": [
            {"key": "go", "label": "Go", "instruction": "Do it", "consequence": "Sent", "plan": plan},
            {"key": "stop", "label": "Stop", "instruction": "Stop", "consequence": "No send",
             "terminal_outcome": "skipped", "reason": "Requested stop"},
        ],
        "needs_human_reason": "A current business choice is missing",
        "decision_basis": {
            "verified_facts": [{"assertion": "Fact", "references": ["source:1"]}],
            "rule_evidence": [{"assertion": "Rule", "references": ["rule:1"]}],
            "quality_explanation": "Two viable options", "no_external_action_evidence": [
                {"assertion": "No write", "references": ["attempt:1"]}],
            "conclusion": "Choose",
        },
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })


def _audit(outcome, *, feedback=None):
    from app.agent_contracts import AuditAgentResult
    from app.reviewed_candidates import candidate_digest
    return AuditAgentResult.model_validate({
        "outcome": outcome, "summary": "synthetic", "proposal_revision": 0,
        "candidate_digest": candidate_digest(_proposal()), "evidence_refs": [],
        "feedback": feedback,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })


def _feedback():
    return {"rule": "evidence", "observation": "Missing fact", "requested_revision": "Revise"}


def readonly_audit():
    from app.agent_contracts import AuditAgentResult
    assert _audit("approve").outcome.value == "approve"
    assert "external_result" not in AuditAgentResult.model_fields
    assert "decision_options" not in AuditAgentResult.model_fields


def whole_return():
    assert _audit("return", feedback=_feedback()).feedback.requested_revision == "Revise"


def substantive_reject():
    from app.reviewed_candidates import rejected_content_changed
    original = _proposal("first")
    cosmetic = original.model_copy(update={"summary": "cosmetic"})
    changed = _proposal("second")
    assert not rejected_content_changed(original, cosmetic)
    assert rejected_content_changed(original, changed)


def review_budget():
    from app.agent_orchestrator import MAX_CONTENT_FEEDBACK_CYCLES
    assert MAX_CONTENT_FEEDBACK_CYCLES == 3


def technical_budget():
    from app.agent_orchestrator import MAX_ROLE_ATTEMPTS_PER_PROCESS, _is_waiting_failure
    from app.agent_result import AgentError
    assert MAX_ROLE_ATTEMPTS_PER_PROCESS >= 2
    assert _is_waiting_failure(AgentError(code="runtime_provider_unreachable", retryable=True))


def human_review():
    from app.reviewed_candidates import candidate_digest
    assert _human().outcome.value == "needs_human"
    assert len(candidate_digest(_human())) == 64
    assert _audit("approve").outcome.value == "approve"


def unnecessary_human():
    assert _audit("reject", feedback=_feedback()).outcome.value == "reject"


def question_quality():
    from app.agent_contracts import ConsumerAgentResult
    values = _human().model_dump(mode="json")
    values["decision_options"] = []
    values["requested_input"] = "Which fact is missing?"
    assert ConsumerAgentResult.model_validate(values).requested_input is not None


def exact_choice():
    human = _human()
    assert human.decision_options[0].plan.actions[0].payload == {"body": "body"}
    assert human.decision_options[1].plan is None


def selection_idempotency():
    from app.store import AutoReplyStore
    assert callable(AutoReplyStore.select_candidate_option)
    assert callable(AutoReplyStore.wake_selected_candidate_execution)


def supplement():
    from app.store import AutoReplyStore
    assert callable(AutoReplyStore.record_candidate_supplement)
    assert callable(AutoReplyStore.list_human_decision_evidence)


def stop_and_progress():
    from app.agent_contracts import SystemExecutionResult
    assert _human().decision_options[1].terminal_outcome == "skipped"
    assert SystemExecutionResult(outcome="skipped", summary="Requested stop").outcome == "skipped"


def oa_notification():
    from app.system_action_handlers import native_dws_handlers
    class Dws:
        pass

    class Store:
        pass
    handlers = native_dws_handlers(Store(), Dws())
    assert ("dingtalk-oa", "approve") in handlers
    assert ("dingtalk-chat", "send_direct_message") in handlers


def staged_materials():
    from app.agent_contracts import ConsumerAgentResult
    staged = _proposal().model_copy(update={"stage_index": 1, "predecessor_review_id": 7,
                                            "continue_after_execution": True})
    assert ConsumerAgentResult.model_validate(staged.model_dump(mode="json")).stage_index == 1


def uncertain_dispatch():
    from app.system_executor import ActionOutcome
    assert ActionOutcome("uncertain", {"reason": "unknown"}).status == "uncertain"


def restart_bindings():
    from app.system_executor import SystemExecutor
    from app.store import AutoReplyStore
    assert list(inspect.signature(SystemExecutor.execute).parameters)[:4] == [
        "self", "task", "candidate_id", "review_id"]
    assert callable(AutoReplyStore.current_reviewed_candidate)


def consumer_documents():
    from app.agent_contracts import ProposedAction
    action = ProposedAction.model_validate({"description": "Create report", "action_identity": "report",
        "capability": "dingtalk-doc", "operation": "create_document", "target": {"name": "Report"},
        "payload": {"content": "# Report"}})
    assert action.payload["content"] == "# Report"


def current_projections():
    from app.store import AutoReplyStore
    assert callable(AutoReplyStore.current_reviewed_candidate)
    assert callable(AutoReplyStore.list_verified_candidate_actions)


def historical_refusal():
    from app.store import AutoReplyStore
    assert callable(AutoReplyStore._check_historical_runtime_refusal)
    assert callable(AutoReplyStore.get_verified_action_source)


def old_unbound_options():
    from app.agent_contracts import DecisionOption
    try:
        DecisionOption.model_validate({"key": "old", "label": "Old", "instruction": "Go",
            "consequence": "Effect"})
    except ValueError:
        return
    raise AssertionError("unbound text-only option was accepted")


SCENARIOS = {
    "readonly_audit": readonly_audit,
    "whole_return": whole_return,
    "substantive_reject": substantive_reject,
    "review_budget": review_budget,
    "technical_budget": technical_budget,
    "human_review": human_review,
    "unnecessary_human": unnecessary_human,
    "question_quality": question_quality,
    "exact_choice": exact_choice,
    "selection_idempotency": selection_idempotency,
    "supplement": supplement,
    "stop_and_progress": stop_and_progress,
    "oa_notification": oa_notification,
    "staged_materials": staged_materials,
    "uncertain_dispatch": uncertain_dispatch,
    "restart_bindings": restart_bindings,
    "consumer_documents": consumer_documents,
    "current_projections": current_projections,
    "historical_refusal": historical_refusal,
    "old_unbound_options": old_unbound_options,
}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True, choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)
    try:
        SCENARIOS[args.case]()
    except Exception as exc:
        print(json.dumps({"case_id": args.case, "ok": False,
                          "error": f"{type(exc).__name__}: {str(exc)[:240]}"}))
        return 1
    print(json.dumps({"case_id": args.case, "ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
