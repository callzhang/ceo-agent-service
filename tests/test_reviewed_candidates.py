from app.agent_contracts import ConsumerAgentResult
from app.reviewed_candidates import action_content_digest, candidate_digest, rejected_content_changed


def _candidate():
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": "Notify", "proposal": {
            "objective": "Notify", "actions": [{
                "description": "Send notice", "action_identity": "send", "capability": "chat",
                "operation": "send", "target": {"id": "1"}, "payload": {"text": "hello"},
            }], "sourced_facts": [], "authored_judgment": "good",
        }, "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })


def test_canonical_candidate_digest_ignores_mapping_order():
    candidate = _candidate()
    reversed_data = dict(reversed(list(candidate.model_dump(mode="json").items())))
    assert candidate_digest(candidate) == candidate_digest(ConsumerAgentResult.model_validate(reversed_data))


def test_rejected_action_requires_content_change_but_return_can_add_evidence():
    before = _candidate()
    after = before.model_copy(deep=True)
    after.proposal.actions[0].description = "Cosmetic rewrite"
    after.proposal.authored_judgment = "New rationale"
    assert candidate_digest(before) != candidate_digest(after)
    assert action_content_digest(before) == action_content_digest(after)
    assert rejected_content_changed(before, after) is False
    after.proposal.actions[0].payload["text"] = "changed"
    assert rejected_content_changed(before, after) is True
