"""Content feedback is bounded independently of technical execution attempts."""

from app.agent_orchestrator import MAX_CONTENT_FEEDBACK_CYCLES
from test_agent_orchestrator import (
    ScriptedAudit,
    ScriptedConsumer,
    candidate,
    orchestrator,
    task_and_context,
)


def test_audit_returns_are_a_normal_loop_of_up_to_ten_and_then_terminal(tmp_path):
    # Derek 2026-10-26: being sent back is ordinary business flow, not a retry.
    assert MAX_CONTENT_FEEDBACK_CYCLES == 10
    turns = MAX_CONTENT_FEEDBACK_CYCLES + 1
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, *[candidate(f"revision-{n}") for n in range(turns)])
    audit = ScriptedAudit(store, *["return"] * turns)
    driver, handler = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_terminal"
    assert result.error.code == "audit_revision_exhausted"
    assert len(consumer.calls) == len(audit.calls) == turns
    assert handler.calls == []


def test_a_candidate_approved_after_several_returns_still_completes(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, *[candidate(f"revision-{n}") for n in range(6)])
    audit = ScriptedAudit(store, *["return"] * 5, "approve")
    driver, _handler = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status != "failed_terminal"
    assert len(consumer.calls) == len(audit.calls) == 6
