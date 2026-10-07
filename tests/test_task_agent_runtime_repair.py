import json
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import RoutedCodexExecution
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore
from app.task_agent import (
    TASK_DECISION_REPAIR_ROUNDS,
    TASK_RUNTIME_CAPABILITIES,
    RepairableTaskDecisionValidationError,
    TaskAgentCodexRunner,
    TaskAgentRunner,
    TaskDecisionRepairExhausted,
    _validate_task_agent_decision,
    process_work_item,
)
from tests.test_routed_codex_execution import NOW, FakeAdapter, make_router
from tests.test_task_agent import _candidate_decision, _work_item


@pytest.fixture
def config():
    return load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"})


def _routed_runner(store, config, payloads):
    provider_calls = []
    executions = 0

    class RecordingAdapter(FakeAdapter):
        def build_command(self, **kwargs):
            provider_calls.append(kwargs["prompt"])
            return super().build_command(**kwargs)

    def executor(*args, **kwargs):
        nonlocal executions
        assert not store.list_business_tasks()
        payload = payloads[min(executions, len(payloads) - 1)]
        executions += 1
        session_event = json.dumps(
            {"type": "system", "session_id": "task-agent-session"}
        )
        return ProcessRunResult(
            0, session_event + "\n" + json.dumps(payload), ""
        )

    snapshots = {route.name: RuntimeCapabilitySnapshot(
        route_name=route.name, capabilities=TASK_RUNTIME_CAPABILITIES, healthy=True,
        checked_at="2026-08-20T09:59:00+00:00",
        expires_at="2026-08-20T10:05:00+00:00",
    ) for route in config.routes}
    codex = TaskAgentCodexRunner(routed_execution=RoutedCodexExecution(
        store=store, config=config, router=make_router(store, config, snapshots=snapshots),
        adapter=RecordingAdapter(), executor=executor,
        session_line_counter=lambda _session: 0, now=lambda: NOW,
    ))
    return TaskAgentRunner(codex), provider_calls


@pytest.mark.parametrize("invalid_kind", ["title", "date_actor"])
def test_semantic_repair_executes_new_round_and_replays_only_matching_receipt(
    tmp_path, monkeypatch, config, invalid_kind,
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "routed-repair.sqlite3")
    item = _work_item().model_copy(update={"summary": "Prepare report by 2026-09-25"})
    corrected = _candidate_decision(item, excerpt=item.summary).model_dump(mode="json")
    invalid = json.loads(json.dumps(corrected))
    if invalid_kind == "title":
        invalid["task_decisions"][0]["title"] = ""
    else:
        invalid["task_decisions"][0]["date_evidence"] = [{
            "kind": "estimated_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-25",
            "actor_name": "Report document",
        }]
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    runner, provider_calls = _routed_runner(store, config, [invalid, corrected])

    process_work_item(store, runner, store.claim_work_summary_inputs(limit=1)[0])

    assert len(provider_calls) == 2
    if invalid_kind == "title":
        assert "previous output was not accepted" in provider_calls[1]
    else:
        assert "Previous candidate" in provider_calls[1]
    assert store.get_work_summary_input(input_id).status.value == "done"
    assert len(store.list_business_tasks()) == 1
    with store._connect() as db:
        run_id = db.execute("select id from task_agent_runs").fetchone()[0]
        keys = [row[0] for row in db.execute(
            "select workload_key from agent_runtime_attempts order by id"
        )]
    repair_round = 0 if invalid_kind == "title" else 1
    assert keys == (
        [str(run_id), str(run_id)]
        if invalid_kind == "title"
        else [str(run_id), f"{run_id}:decision_repair.1"]
    )
    replay = runner.decide(
        item, "Replay the same correction", run_id=run_id, repair_round=repair_round,
        session_scope_id="task-agent",
    )
    assert replay.model_dump(mode="json") == corrected
    assert len(provider_calls) == 2
    reopened = AutoReplyStore(tmp_path / "routed-repair.sqlite3")
    new_runner, new_calls = _routed_runner(reopened, config, [corrected])
    restored = new_runner.decide(
        item, "Restore persisted correction", run_id=run_id, repair_round=repair_round,
        session_scope_id="task-agent",
    )
    assert restored == replay
    assert new_calls == []


def test_routed_semantic_repair_exhaustion_has_real_bounded_provider_turns(
    tmp_path, monkeypatch, config,
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "routed-exhaustion.sqlite3")
    item = _work_item().model_copy(update={"summary": "Prepare report by 2026-09-25"})
    invalid = _candidate_decision(item).model_dump(mode="json")
    invalid["task_decisions"][0]["date_evidence"] = [{
        "kind": "estimated_deadline_at", "value": "2026-09-25",
        "source_ref": item.source.ref, "source_excerpt": "2026-09-25",
        "actor_name": "Report document",
    }]
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    runner, calls = _routed_runner(store, config, [invalid])

    with pytest.raises(TaskDecisionRepairExhausted, match="date actor_name"):
        process_work_item(store, runner, store.claim_work_summary_inputs(limit=1)[0])

    assert len(calls) == 1 + TASK_DECISION_REPAIR_ROUNDS
    assert not store.list_business_tasks()
    assert store.get_work_summary_input(input_id).status.value == "failed"


@pytest.mark.parametrize("date_patch, context, message", [
    ({"actor_name": "Report document"}, {}, "date actor_name"),
    ({"actor_user_id": "other"}, {}, "date actor_user_id"),
    ({}, {"sender": "", "sender_user_id": ""}, "not attributable"),
    ({"source_ref": "other-source"}, {}, "source_ref must match"),
    ({"source_excerpt": "2026-09-26"}, {}, "exact source substring"),
    ({"value": "2026-09-25T00:00:00"}, {}, "exact, parseable date phrase"),
])
def test_date_evidence_is_repairable_before_domain_transaction(date_patch, context, message):
    item = _work_item(**context).model_copy(update={"summary": "Prepare by 2026-09-25"})
    decision = _candidate_decision(item, excerpt=item.summary)
    fact = {
        "kind": "estimated_deadline_at", "value": "2026-09-25",
        "source_ref": item.source.ref, "source_excerpt": "2026-09-25", **date_patch,
    }
    decision = type(decision).model_validate({"project_decisions": [], "task_decisions": [{
        **decision.task_decisions[0].model_dump(mode="json"), "date_evidence": [fact],
    }], "project_assessments": [], "update_summary": "No relevant Project was found."})

    with pytest.raises(RepairableTaskDecisionValidationError, match=message):
        _validate_task_agent_decision(decision, work_item=item)


def _active_task_run(store):
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    store.claim_work_summary_inputs(limit=1)
    return input_id, store.begin_task_agent_run(input_id)


def _claim_repair(store, run_id, **kwargs):
    return store.claim_runtime_operation_attempt(
        "task", f"{run_id}:decision_repair.1", "codex_oauth", "codex_cli",
        "local_oauth", "gpt-5.5", **kwargs,
    )


def test_repair_runtime_requires_its_active_task_run_not_a_project(tmp_path):
    store = AutoReplyStore(tmp_path / "repair-parent.sqlite3")
    _, run_id = _active_task_run(store)
    attempt = _claim_repair(store, run_id)
    assert attempt.workload_key == f"{run_id}:decision_repair.1"
    with pytest.raises(ValueError, match="parent does not exist"):
        _claim_repair(store, run_id + 1000)
    store.finish_task_agent_run(run_id, status="failed", error="No valid candidate")
    with pytest.raises(ValueError, match="parent does not exist"):
        _claim_repair(store, run_id)


@pytest.mark.parametrize("recovery", ["reset", "orphan", "expired_terminal"])
@pytest.mark.parametrize("running", [False, True])
def test_repair_runtime_is_recovered_with_original_parent_after_interruption(tmp_path, recovery, running):
    store = AutoReplyStore(tmp_path / "repair-recovery.sqlite3")
    input_id, run_id = _active_task_run(store)
    now = datetime.now(UTC)
    attempt = _claim_repair(store, run_id, now=now, lease_seconds=60)
    if running:
        store.mark_agent_runtime_attempt_running_once(
            attempt.id, now=now, lease_seconds=60,
        )
    if recovery == "reset":
        store.reset_processing_work_summary_inputs()
    elif recovery == "orphan":
        store.mark_work_summary_input_failed(input_id, "Interrupted")
        assert store.recover_orphaned_task_agent_runs() == 1
    else:
        store.finish_task_agent_run(run_id, status="failed", error="Interrupted")
        assert store.recover_expired_terminal_task_runtime_attempts(
            now=now + timedelta(seconds=61)
        ) == 1
    assert store.get_agent_runtime_attempt(attempt.id).status == "failed"


@pytest.mark.parametrize("boundary", ["effect", "unexpired", "backfill"])
def test_terminal_repair_recovery_preserves_nonrecoverable_attempts(tmp_path, boundary):
    store = AutoReplyStore(tmp_path / "repair-boundaries.sqlite3")
    _, run_id = _active_task_run(store)
    now = datetime.now(UTC)
    attempt = _claim_repair(store, run_id, now=now, lease_seconds=60)
    if boundary == "effect":
        store.mark_agent_runtime_attempt_running_once(
            attempt.id, now=now, lease_seconds=60, effectful=True,
        )
    elif boundary == "backfill":
        with store._connect() as db:
            db.execute("update agent_runtime_attempts set workload_key=? where id=?",
                       (f"{run_id}:memory_backfill", attempt.id))
    store.finish_task_agent_run(run_id, status="failed", error="Interrupted")

    assert store.recover_expired_terminal_task_runtime_attempts(
        now=now + timedelta(seconds=30 if boundary == "unexpired" else 61)
    ) == 0
    assert store.get_agent_runtime_attempt(attempt.id).status == (
        "running" if boundary == "effect" else "starting"
    )


def test_reset_reclaim_cannot_replay_previous_run_correction(tmp_path, config):
    store = AutoReplyStore(tmp_path / "new-run.sqlite3")
    input_id, run_id = _active_task_run(store)
    old = _claim_repair(store, run_id)
    store.reset_processing_work_summary_inputs()
    store.claim_work_summary_inputs(limit=1)
    new_run_id = store.begin_task_agent_run(input_id)
    item = _work_item()
    corrected = _candidate_decision(item).model_dump(mode="json")
    runner, calls = _routed_runner(store, config, [corrected])

    runner.decide(item, "New claim correction", run_id=new_run_id, repair_round=1,
                  session_scope_id="task-agent")

    assert new_run_id != run_id
    assert len(calls) == 1
    assert store.get_agent_runtime_attempt(old.id).status == "failed"
    assert len(store.list_runtime_operation_attempts(
        "task", f"{new_run_id}:decision_repair.1"
    )) == 1


@pytest.mark.parametrize("suffix", ["decision_repair.0", "decision_repair.-1", "decision_repair.01", "decision_repair.1.extra"])
def test_repair_round_key_must_be_canonical_positive_integer(suffix):
    with pytest.raises(ValueError, match="unsupported suffix"):
        AutoReplyStore._validate_runtime_operation_workload("task", f"1:{suffix}")
