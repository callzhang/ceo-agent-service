"""Existing result validation must stay inside the bounded turn retry lifecycle."""

import json
from pathlib import Path

import pytest

from app.agent_orchestrator import AgentOrchestrator
from app.agent_result import ResultParseError
from app.consumer_agent import ConsumerAgentRunner
from tests import test_consumer_agent as consumer_tests
from tests.test_consumer_agent import CapturingExecutor, _wire_result

store = consumer_tests.store
task = consumer_tests.task
context = consumer_tests.context


def invalid_callback_stream():
    result = _wire_result({
        "outcome": "no_action",
        "summary": "Synthetic incomplete callback https://feedback.example.com/api/dingtalk-feedback-spike?token=synthetic",
        "proposal": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    })
    return "\n".join((
        json.dumps({"type": "thread.started", "thread_id": "invalid-callback-synthetic"}),
        json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps(result),
        }}),
    ))


def runner_for(store, tmp_path):
    return ConsumerAgentRunner(store=store, workspace=Path(tmp_path),
        executor=CapturingExecutor(invalid_callback_stream()))


def test_invalid_callback_result_uses_existing_parse_failure(store, task, context, tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.com")
    with pytest.raises(ResultParseError, match="feedback_callback_pair_invalid"):
        runner_for(store, tmp_path).run(task, context, proposal_revision=0, parent_agent_run_id=None)
    [run] = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    error = json.loads(run.structured_error_json)
    assert run.status == "failed"
    assert error["code"] == "codex_result_invalid"
    assert error["stage"] == "result"
    assert error["detail"] == "feedback_callback_pair_invalid"
    assert run.final_result_json == ""


def test_invalid_callback_results_stop_at_existing_consecutive_failure_limit(store, task, context, tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.com")

    class NeverAudit:
        def run(self, *args, **kwargs):
            raise AssertionError("invalid Consumer result must not reach Audit")

    driver = AgentOrchestrator(store=store, consumer=runner_for(store, tmp_path), audit=NeverAudit())
    outcomes = [driver.process(task, context, refresh_context=lambda: context) for _ in range(3)]
    assert [result.status for result in outcomes] == ["failed_retryable", "failed_retryable", "failed_terminal"]
    assert outcomes[-1].error.code == "codex_result_invalid"
    assert outcomes[-1].feedback_cycles == 0
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    assert len(runs) == 6
    assert all(run.status == "failed" and run.role.value == "consumer" for run in runs)
    with store._connect() as db:
        for table in ("review_candidates", "candidate_executions", "external_action_results", "sent_replies"):
            assert db.execute(f"select count(*) from {table}").fetchone()[0] == 0
