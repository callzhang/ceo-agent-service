import json

import pytest

from app.agent_contracts import AuditOutcome
from app.process_runner import ProcessRunResult
from app.store import AgentRole
from tests.test_audit_agent import (
    CapturingExecutor,
    _runner,
    _wire,
    setup as _audit_setup,
)


@pytest.fixture
def setup(tmp_path):
    return _audit_setup.__wrapped__(tmp_path)


def test_audit_idle_timeout_binds_fresh_runtime_session_and_preserves_attempts(setup):
    store, task, parent, context, _config, _router = setup
    calls = []
    successful_executor = CapturingExecutor(
        _wire().replace("audit-session", "audit-recovered")
    )

    def executor(command, *, on_stdout_line, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            output = json.dumps({"type": "thread.started", "thread_id": "audit-idle"})
            on_stdout_line(output)
            return ProcessRunResult(1, output, "idle timeout", timed_out=True, timeout_kind="idle")
        return successful_executor(command, on_stdout_line=on_stdout_line, **kwargs)

    result = _runner(setup, executor).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert result.result.outcome is AuditOutcome.APPROVE
    assert len(calls) == 2
    assert "resume" not in calls[1]
    run = store.get_agent_run(result.run_id)
    assert run.codex_session_id == "audit-recovered"
    attempts = store.list_agent_runtime_attempts(run.id)
    assert [attempt.session_id for attempt in attempts] == ["audit-idle", "audit-recovered"]
    assert attempts[0].failure_code == "codex_idle_timeout"
    assert attempts[1].status == "completed"
    assert store.get_codex_session_id(task.conversation_id) in (None, "")


def test_audit_session_replacement_still_requires_explicit_handoff(setup):
    store, task, parent, context, _config, _router = setup
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=context.operation_id, owner="audit",
    ).run
    store.set_agent_run_session(run.id, "original-audit", owner="audit")

    with pytest.raises(ValueError, match="agent run session cannot be replaced"):
        store.set_agent_run_session(run.id, "unrequested-session", owner="audit")

    assert store.get_agent_run(run.id).codex_session_id == "original-audit"
