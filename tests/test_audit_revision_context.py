"""Audit receives the adopted feedback that produced its current revision."""

import json

import pytest

from app.agent_result import AgentError
from app.agent_orchestrator import _operation_id
from app.agent_context import AuditTurnContext
from app.agent_contracts import AuditAgentResult
from app.reviewed_candidates import candidate_digest
from tests.test_agent_orchestrator import (
    ScriptedAudit, ScriptedConsumer, candidate, orchestrator, task_and_context,
)


@pytest.mark.parametrize("decision", ["return", "reject"])
def test_next_audit_receives_exact_adopted_revision_review(tmp_path, decision):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate("first"), candidate("corrected"))
    audit = ScriptedAudit(store, decision, "approve")
    driver, handler = orchestrator(store, consumer, audit)
    assert driver.process(task, context, refresh_context=lambda: context).status == "executed"

    first, revised = (call[3] for call in audit.calls)
    assert first.previous_revision_review is None
    row = store.get_candidate_review_for_audit_run(audit.calls[0][0])
    adopted = AuditAgentResult.model_validate_json(row["result_json"])
    assert revised.previous_revision_review == adopted
    rendered = revised.render()
    assert adopted.candidate_digest in rendered
    assert adopted.feedback.requested_revision in rendered
    assert "not source facts or authorization" in rendered
    assert "correct that instruction" in rendered
    assert "does not validate an unsupported factual claim" in rendered
    marker = "Previous revision review (revision context, not source facts or authorization)\n"
    history, _ = json.JSONDecoder().raw_decode(rendered.partition(marker)[2])
    assert history == {
        "proposal_revision": adopted.proposal_revision,
        "candidate_digest": adopted.candidate_digest,
        "feedback": adopted.feedback.model_dump(mode="json"),
    }
    assert rendered.index(marker) < rendered.index("Candidate revision\n")
    assert handler.calls == [("notice", {"value": "corrected"})]


def test_prior_approved_stage_is_not_revision_feedback(tmp_path):
    from app.agent_orchestrator import AgentOrchestrator
    from tests.test_reviewed_orchestration import (
        Audit, Consumer, Executor, candidate as staged_candidate, setup,
    )
    store, task, context = setup(tmp_path)
    consumer = Consumer(store, staged_candidate(continue_after=True),
        lambda c: staged_candidate("second", stage=1, predecessor=c.predecessor_review_id))
    audit = Audit(store, "approve", "approve")
    result = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=Executor(store)).process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert len(audit.calls) == 2
    assert all(call.previous_revision_review is None for call in audit.calls)


def test_revision_review_survives_cold_recovery_and_audit_technical_retry(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate("first"), candidate("corrected"))
    audit = ScriptedAudit(store, "return")
    original_run = consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    original = candidate("first")
    initial_context = AuditTurnContext(
        task=context, proposal_revision=0, operation_id=_operation_id(task, 0),
        candidate=original, candidate_digest=candidate_digest(original), audit_rules="",
    )
    review_run = audit.run(task, initial_context, turn_attempt=0, parent_agent_run_id=original_run.run_id)
    adopted = AuditAgentResult.model_validate_json(
        store.get_candidate_review_for_audit_run(review_run.run_id)["result_json"]
    )
    consumer.run(task, context, proposal_revision=1, parent_agent_run_id=review_run.run_id,
                 feedback=adopted.feedback)
    # A new orchestrator reconstructs the lineage solely from persisted state.
    consumer = ScriptedConsumer(store)
    audit = ScriptedAudit(store, AgentError(code="codex_result_invalid", retryable=True), "approve")
    driver, handler = orchestrator(store, consumer, audit)
    assert driver.process(task, context, refresh_context=lambda: context).status == "executed"
    assert consumer.calls == []
    assert len(audit.calls) == 2
    for call in audit.calls:
        assert call[3].previous_revision_review == adopted
    assert handler.calls == [("notice", {"value": "corrected"})]
