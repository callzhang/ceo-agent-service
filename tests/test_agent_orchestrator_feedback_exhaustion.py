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


def test_fourth_feedback_is_a_terminal_technical_failure(store):
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
    assert result.status == "failed_terminal"
    assert result.feedback_cycles == 4
    assert result.error.code == "audit_revision_exhausted"
    assert result.audit_result is not None
    assert result.audit_result.outcome.value == "failed"
