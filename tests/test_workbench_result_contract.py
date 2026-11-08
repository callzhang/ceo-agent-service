import json
from types import SimpleNamespace

import pytest

from app.agent_runtime_router import RoutedResultValidationError
from app.workbench.runtime import RuntimeRequest
from app.workbench.service_runtime import ServiceWorkbenchRuntime
from app.workbench.store import WorkbenchConflictError, WorkbenchStore
from tests.test_automatic_repair_gate import store as store

CONTRACT = "automatic_repair.pr.v3"
RESULT = {
    "outcome": "pr_updated",
    "summary": "Pushed verified fixes",
    "remaining_work": [],
    "addressed_review_ids": [],
}


def test_turn_result_contract_survives_restart_and_is_idempotent(store):
    task = store.create_task(title="repair", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="fix CI",
        client_request_id="repair",
        result_contract=CONTRACT,
    )
    restarted = WorkbenchStore(store.path)
    assert restarted.get_turn_result_contract(turn.id) == CONTRACT
    assert (
        restarted.create_turn(
            task.id,
            user_text="fix CI",
            client_request_id="repair",
            result_contract=CONTRACT,
        ).id
        == turn.id
    )
    with pytest.raises(WorkbenchConflictError):
        restarted.create_turn(task.id, user_text="fix CI", client_request_id="repair")


def test_unknown_contract_cannot_create_a_native_turn(store):
    task = store.create_task(title="repair", runtime_kind="codex")
    with pytest.raises(ValueError):
        store.create_turn(
            task.id,
            user_text="fix",
            client_request_id="invalid",
            result_contract="unknown",
        )
    assert not store.list_turns(task.id)


def test_native_contract_uses_fixed_schema_and_same_session_correction(tmp_path):
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        factory = kwargs["command_factory"]
        assert factory.use_output_schema
        assert factory.output_schema_path.is_file()
        schema = json.loads(factory.output_schema_path.read_text())
        assert set(schema["required"]) == set(schema["properties"])
        with pytest.raises(RoutedResultValidationError):
            kwargs["parser"](
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": "Let me find the current columns.",
                        },
                    }
                )
            )
        missing_fields = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": '{"outcome":"blocked","summary":"pending"}',
                },
            }
        )
        with pytest.raises(
            RoutedResultValidationError, match="remaining_work"
        ) as invalid:
            kwargs["parser"](missing_fields)
        correction = kwargs["result_validation_retry"].corrected_prompt(
            "original task", invalid.value
        )
        assert "remaining_work" in correction
        assert '"properties"' not in correction
        raw = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(RESULT)},
            }
        )
        result = kwargs["parser"](raw)
        assert json.loads(result) == RESULT
        assert kwargs["result_validation_retry"].resume_same_session
        assert {
            "structured_output",
            "local_schema_validation",
            "local_workspace_access",
        } <= kwargs["required_capabilities"]
        return SimpleNamespace(value=result, session_id="native-session")

    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path,
        routed_execution_factory=lambda **kwargs: SimpleNamespace(execute=execute),
    )
    request = RuntimeRequest(
        turn_id="2",
        conversation_id="1",
        workspace=tmp_path,
        prompt="fix",
        result_contract=CONTRACT,
    )
    result = runtime.wait(runtime.start(request, on_event=lambda event: None))
    assert result.status == "completed"
    assert json.loads(result.final_text) == RESULT
    assert len(calls) == 1


def test_executor_carries_durable_contract_to_runtime(store, tmp_path):
    from app.workbench.executor import WorkbenchExecutor
    from app.workbench.runtime import RuntimeRegistry
    from tests.test_workbench_executor import FakeRuntime

    runtime = FakeRuntime()
    task = store.create_task(title="repair", runtime_kind="fake")
    turn = store.create_turn(
        task.id, user_text="fix", client_request_id="typed", result_contract=CONTRACT
    )
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    executor.run_once()
    assert runtime.requests[0].result_contract == CONTRACT
    assert store.get_turn_result_contract(turn.id) == CONTRACT
    executor.close()


@pytest.mark.parametrize(
    "text,status",
    [("Let me find columns", "failed"), (json.dumps(RESULT), "completed")],
)
def test_direct_codex_also_binds_and_validates_contract(tmp_path, text, status):
    from app.workbench.codex_runtime import CodexRuntime
    from tests.test_workbench_codex_runtime import FakeProcessExecutor, SESSION_ID

    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": text},
        },
        {"type": "turn.completed"},
    ]
    process = FakeProcessExecutor(records)
    runtime = CodexRuntime(workspace=tmp_path, executor=process)
    request = RuntimeRequest(
        turn_id="2", workspace=tmp_path, prompt="fix", result_contract=CONTRACT
    )
    result = runtime.wait(runtime.start(request, on_event=lambda event: None))
    assert "--output-schema" in process.commands[0]
    assert result.status == status
    if status == "failed":
        assert result.error_code == "runtime_result_validation_failed"
        assert result.provider_session_ref == SESSION_ID
        assert len(process.commands) == 2
    else:
        assert json.loads(result.final_text) == RESULT


def test_contract_metadata_and_turn_roll_back_together(store, monkeypatch):
    import app.workbench.store as module

    task = store.create_task(title="repair", runtime_kind="codex")

    def fail(*args, **kwargs):
        raise RuntimeError("contract insert failed")

    monkeypatch.setattr(module, "insert_state", fail)
    with pytest.raises(RuntimeError, match="contract insert failed"):
        store.create_turn(
            task.id,
            user_text="fix",
            client_request_id="atomic",
            result_contract=CONTRACT,
        )
    assert not store.list_turns(task.id)
    with store._connect() as db:
        assert (
            db.execute(
                "select count(*) from tasks where kind='workbench_turn'"
            ).fetchone()[0]
            == 0
        )


def test_fixed_schemas_match_the_models_and_native_required_fields():
    from app.workbench.result_contracts import _CONTRACTS

    for contract in _CONTRACTS.values():
        schema = json.loads(contract.schema_path.read_text())
        assert schema == contract.model.model_json_schema()
        assert set(schema["required"]) == set(schema["properties"])


def test_repair_requests_native_contract_without_manual_input(store, tmp_path):
    from app.automatic_repair import AutomaticRepairCoordinator
    from app.automatic_repair_store import get_repair
    from app.automatic_repair_contracts import REPAIR_DIAGNOSIS_CONTRACT
    from tests.test_automatic_repair_gate import finish

    store.record_error(None, None, "actual_failure", "diagnostic")
    identity = store.list_errors(limit=1)[0].id
    c = AutomaticRepairCoordinator(
        store, repository=tmp_path, pr_reader=lambda branch: [], interval_seconds=0
    )
    c.run_once()
    entry = get_repair(store, "errors", identity)
    first = entry["turn_ids"][-1]
    assert store.get_turn_result_contract(first) == REPAIR_DIAGNOSIS_CONTRACT
    finish(store, first)
    entry.update(
        pr_url="https://github.com/example/service/pull/9", status="pr_created"
    )
    from app.automatic_repair_store import save_repair

    save_repair(store, entry)
    from tests.test_repair_pr_feedback import snapshot, failed_check, poll

    poll(store, tmp_path, snapshot(checks=[failed_check()]))
    next_turn = get_repair(store, "errors", identity)["turn_ids"][-1]
    assert store.get_turn_result_contract(next_turn) == CONTRACT


def test_initial_intent_recovers_the_declared_contract_after_producer_change(
    store, tmp_path, monkeypatch
):
    import app.automatic_repair as module
    from app.automatic_repair_contracts import REPAIR_DIAGNOSIS_CONTRACT
    from app.automatic_repair_store import get_repair
    from tests.test_automatic_repair_gate import coordinator

    store.record_error(None, None, "failure", "diagnostic")
    identity = store.list_errors(limit=1)[0].id
    original_save = module.save_repair
    calls = 0

    def crash_after_native_turn(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("checkpoint")
        return original_save(*args)

    monkeypatch.setattr(module, "save_repair", crash_after_native_turn)
    with pytest.raises(RuntimeError, match="checkpoint"):
        coordinator(store, tmp_path).run_once()
    monkeypatch.setattr(module, "save_repair", original_save)
    monkeypatch.setattr(module, "REPAIR_DIAGNOSIS_CONTRACT", CONTRACT)
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, "errors", identity)
    assert len(entry["turn_ids"]) == 1
    assert (
        store.get_turn_result_contract(entry["turn_ids"][0])
        == REPAIR_DIAGNOSIS_CONTRACT
    )


def test_native_schema_is_not_duplicated_in_repair_task_body(store, tmp_path):
    from tests.test_repair_pr_feedback import attached, poll, snapshot, failed_check
    from app.automatic_repair_store import get_repair

    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, snapshot(checks=[failed_check()]))
    turn = store.get_turn(get_repair(store, "errors", identity)["turn_ids"][-1])
    assert "result_schema" not in json.loads(turn.user_text.split("\n", 1)[1])
    assert '"properties"' not in turn.user_text



def test_direct_codex_corrects_invalid_contract_in_same_native_session(tmp_path):
    from app.workbench.codex_runtime import CodexRuntime
    from app.process_runner import ProcessRunResult
    from tests.test_workbench_codex_runtime import SESSION_ID

    calls = []
    def execute(command, **kwargs):
        calls.append((command, kwargs['prompt']))
        if len(calls) == 2:
            assert 'resume' in command
            assert command[-2] == SESSION_ID
            assert 'remaining_work' in kwargs['prompt']
        text = json.dumps(RESULT if len(calls) == 2 else {
            'outcome': 'blocked', 'summary': 'not finished',
        })
        for record in [
            {'type': 'thread.started', 'thread_id': SESSION_ID},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}},
            {'type': 'turn.completed'},
        ]:
            kwargs['on_stdout_line'](json.dumps(record))
        return ProcessRunResult(0, '', '')

    runtime = CodexRuntime(workspace=tmp_path, executor=execute)
    result = runtime.wait(runtime.start(RuntimeRequest(
        turn_id='2', workspace=tmp_path, prompt='fix CI', result_contract=CONTRACT,
    ), on_event=lambda event: None))
    assert len(calls) == 2
    assert result.status == 'completed', result.error_detail
    assert json.loads(result.final_text) == RESULT
    assert result.provider_session_ref == SESSION_ID


@pytest.mark.parametrize("second_failure", ["timeout", "exit", "mismatch"])
def test_direct_contract_correction_preserves_first_session_on_failure(tmp_path, second_failure):
    from app.workbench.codex_runtime import CodexRuntime
    from app.process_runner import ProcessRunResult
    from tests.test_workbench_codex_runtime import SESSION_ID, OTHER_SESSION_ID

    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        if len(calls) == 2:
            if second_failure == "mismatch":
                kwargs["on_stdout_line"](json.dumps({"type": "thread.started", "thread_id": OTHER_SESSION_ID}))
            return ProcessRunResult(1, "", "provider unavailable", timed_out=second_failure == "timeout")
        for record in [
            {"type": "thread.started", "thread_id": SESSION_ID},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "not final JSON"}},
            {"type": "turn.completed"},
        ]:
            kwargs["on_stdout_line"](json.dumps(record))
        return ProcessRunResult(0, "", "")

    runtime = CodexRuntime(workspace=tmp_path, executor=execute)
    result = runtime.wait(runtime.start(RuntimeRequest(
        turn_id="2", workspace=tmp_path, prompt="fix CI", result_contract=CONTRACT,
    ), on_event=lambda event: None))
    assert len(calls) == 2
    assert result.status == "failed"
    assert result.provider_session_ref == SESSION_ID
    assert result.error_code == {
        "timeout": "provider_timeout", "exit": "provider_process_failed",
        "mismatch": "conflicting_provider_session",
    }[second_failure]
