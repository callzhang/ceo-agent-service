"""Adopt a synthetic unit review without inventing an execution receipt."""

from hashlib import sha256

from app.agent_contracts import AuditAgentResult, ConsumerAgentResult


def complete_synthetic_approval(store, audit_run, *, owner):
    candidate = store.adopted_candidate_for_consumer_run(audit_run.parent_agent_run_id)
    assert candidate is not None
    assert candidate["task_id"] == audit_run.reply_task_id
    assert candidate["execution_generation"] == audit_run.execution_generation
    assert candidate["proposal_revision"] == audit_run.proposal_revision
    ConsumerAgentResult.model_validate_json(candidate["candidate_json"])
    assert sha256(candidate["candidate_json"].encode()).hexdigest() == candidate["candidate_digest"]
    review = AuditAgentResult.model_validate({
        "outcome": "approve", "summary": "Synthetic unit review approves the bound candidate.",
        "proposal_revision": audit_run.proposal_revision, "candidate_digest": candidate["candidate_digest"],
        "evidence_refs": [], "feedback": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0, "information_completeness": 1.0,
    })
    completed = store.complete_agent_run(audit_run.id, review.model_dump(mode="json"), owner=owner)
    adopted = store.reviewed_candidate_for_audit_run(completed.id)
    assert adopted is not None
    assert adopted["id"] == candidate["id"]
    assert adopted["decision"] == "approve"
    assert adopted["candidate_digest"] == candidate["candidate_digest"]
    return completed
