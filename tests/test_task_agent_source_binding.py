"""Actual runtime coordinates reach current Task/Project source bindings."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from app import native_trajectory
from app import task_agent
from app.business_source_reader import SourceVersionBinding
from app.task_models import TaskDecision
from tests.test_task_agent import _work_item, FakeRoutedTaskExecution
from tests.test_provenance_vector_storage import db as db


def test_routed_runner_retains_actual_attempt_id():
    routed = FakeRoutedTaskExecution(json.dumps(dict(project_decisions=[], project_assessments=[],
        update_summary="none", task_decisions=[dict(action="skip", transition="none", skip_reason="none")])))
    runner = task_agent.TaskAgentCodexRunner(routed_execution=routed)
    runner.decide(prompt="{}", workload_key="one")
    assert runner.last_runtime_attempt_id == 1


def test_decision_preparation_uses_injected_request_reader():
    from app.business_source_reader import RequestSourceReader
    reader = RequestSourceReader(provider_reader=object())
    decision = task_agent._parse_task_agent_decision(json.dumps(dict(project_decisions=[], project_assessments=[],
        update_summary="none", task_decisions=[dict(action="skip", transition="none", skip_reason="none")])))
    prepared = task_agent._prepare_decision_sources(SimpleNamespace(), decision, source_reader=reader)
    assert prepared.reader is reader


@pytest.mark.parametrize("runtime", ("codex_cli", "claude_cli"))
def test_actual_completed_turn_locator_flows_to_both_source_factories(db, tmp_path, monkeypatch, runtime):
    work = _work_item()
    leaf = "existing source " + json.dumps(work.summary, ensure_ascii=False)
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"content": leaf}, ensure_ascii=False) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session: path)
    monkeypatch.setattr(native_trajectory, "claude_session_path", lambda session: path)
    attempt = SimpleNamespace(status="completed", runtime_kind=runtime, session_id="actual-session",
                              transcript_start=0, transcript_end=1)
    seen = []
    def get_attempt(attempt_id):
        seen.append(attempt_id)
        return attempt
    store = SimpleNamespace(get_agent_runtime_attempt=get_attempt)
    runner = SimpleNamespace(codex=SimpleNamespace(last_runtime_attempt_id=73, last_session_id="actual-session"))
    native, provider = task_agent._work_item_source_coordinates(store, runner, work)
    assert seen == [73] and native["runtime_kind"] == runtime and provider is None
    assert native_trajectory.read_native_source_body(native) == work.summary
    item = TaskDecision(action="record_candidate", transition="none", source_ref=work.source.ref,
                        source_excerpt=work.summary, title="Candidate")
    from app.store import AutoReplyStore
    from app.task_semantic_service import TaskSemanticService
    from datetime import datetime, timezone
    actual_store = AutoReplyStore.__new__(AutoReplyStore)
    db.row_factory = sqlite3.Row
    service = TaskSemanticService(actual_store)
    for signal in (task_agent._task_source_signal(work, item, native_provenance=native),
                   task_agent._project_source_signal(work, native_provenance=native)):
        assert isinstance(signal.source_binding, SourceVersionBinding)
        assert signal.source_binding.matches_body(work.summary)
        assert json.loads(signal.source_binding.native_provenance_json) == native
        assert signal.source_binding.provider_locator_json is None
        public_id = service._signal_id_or_create(signal=signal, db=db, now=datetime.now(timezone.utc))
        fact = actual_store.get_business_task_signal_in_transaction(signal_id=public_id, _db=db)
        assert fact.source == signal.source_binding
        assert actual_store.business_source_reader().resolve(fact.source).body == work.summary
    assert db.execute("select count(*) from provenance").fetchone()[0] == 1


def test_source_outside_completed_turn_cannot_supply_native_locator(tmp_path, monkeypatch):
    work = _work_item()
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"content": json.dumps(work.summary, ensure_ascii=False)}, ensure_ascii=False)
                    + '\n{"content":"different turn"}\n')
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session: path)
    attempt = SimpleNamespace(status="completed", runtime_kind="codex_cli", session_id="actual-session",
                              transcript_start=1, transcript_end=2)
    store = SimpleNamespace(get_agent_runtime_attempt=lambda attempt_id: attempt)
    runner = SimpleNamespace(codex=SimpleNamespace(last_runtime_attempt_id=73, last_session_id="actual-session"))
    assert task_agent._work_item_source_coordinates(store, runner, work) == (None, None)
