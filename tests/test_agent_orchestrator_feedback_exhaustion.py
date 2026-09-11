from pathlib import Path

import pytest

from app.agent_orchestrator import AgentOrchestrator, OrchestrationResult
from app.store import AutoReplyStore

from tests.test_agent_orchestrator import (
    ScriptedAudit,
    ScriptedConsumer,
    _audit_result,
    _consumer_result,
    _process,
    _task,
)


@pytest.fixture
def store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "orchestrator.sqlite3")


def test_feedback_exhaustion_surfaces_last_audit_revision_as_human_choice(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "candidate-0"),
        _consumer_result("proposal", "candidate-1"),
        _consumer_result("proposal", "candidate-2"),
        _consumer_result("proposal", "candidate-3"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("feedback_provided", 0),
        _audit_result("feedback_provided", 1),
        _audit_result("feedback_provided", 2),
        _audit_result("feedback_provided", 3),
    )

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert isinstance(result, OrchestrationResult)
    assert result.status == "needs_human"
    assert result.error.code == "audit_revision_exhausted"
    assert result.feedback is not None
    assert (
        result.feedback.requested_revision
        == "Return a complete replacement proposal."
    )
    assert result.audit_result is not None
    assert result.audit_result.outcome.value == "needs_human"
    assert [option.key for option in result.audit_result.decision_options] == [
        "apply_audit_revision",
        "stop_without_action",
    ]
