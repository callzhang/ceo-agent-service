import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.agent_runtime_router import RuntimeRouteDecision
from app.agent_turn_runner import AgentTurnProcess, _required_runtime_capabilities
from app.agent_wire_contracts import parse_consumer_agent_wire_result
from app.native_cli_metadata import describe_native_command
from app.process_runner import ProcessRunResult
from app.store import (
    MAX_RECONCILIATION_EVENTS,
    RECONCILIATION_EVENT_LIMIT_ERROR,
    AgentRole,
    AgentRunLeaseLostError,
    AgentRuntimeAttemptStartConflictError,
    AutoReplyStore,
)


def _task(store: AutoReplyStore):
    store.enqueue_reply_task(
        conversation_id="cid-turns",
        conversation_title="Turn persistence",
        single_chat=False,
        trigger_message_id="msg-turns",
        trigger_create_time="2026-08-06 10:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        execution_generation="generation-1",
    )
    return store.claim_reply_tasks(limit=1)[0]


def _claim_consumer(store, task, *, revision=0, owner="consumer"):
    return store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=revision,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner=owner,
    )


def _claim_audit(store, task):
    return store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="operation-0",
        owner="audit",
    ).run


def _claude_route() -> RuntimeRoute:
    return RuntimeRoute(
        name="claude_api",
        runtime_kind=RuntimeKind.CLAUDE_CLI,
        credential_mode=CredentialMode.SERVICE_API,
        model="claude-sonnet-4-5",
    )


def test_claude_consumer_session_requires_exact_route_and_contract_hash(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    store.upsert_conversation_runtime_session(
        task.conversation_id, "codex_oauth", "oauth-session", "current-contract"
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id, "codex_api", "api-session", "current-contract"
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id, "claude_api", "claude-session", "current-contract"
    )
    process = AgentTurnProcess(
        store=store, task=task, workspace=tmp_path, owner="consumer"
    )

    assert process._session_for_route(
        _claude_route(),
        role=AgentRole.CONSUMER,
        requested_session_id="oauth-session",
        recovery_phase="",
        conversation_contract_hash="current-contract",
    ) == "claude-session"
    assert process._session_for_route(
        _claude_route(),
        role=AgentRole.CONSUMER,
        requested_session_id="claude-session",
        recovery_phase="",
        conversation_contract_hash="different-contract",
    ) is None
    assert process._session_for_route(
        _claude_route(),
        role=AgentRole.AUDIT,
        requested_session_id="claude-session",
        recovery_phase="",
        conversation_contract_hash="current-contract",
    ) is None
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_oauth"
    ) == "oauth-session"
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_api"
    ) == "api-session"
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api"
    ) == "claude-session"


def test_claude_incompatible_resume_clears_only_matching_route_slot(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run
    for route_name, session_id in (
        ("codex_oauth", "oauth-session"),
        ("codex_api", "api-session"),
        ("claude_api", "claude-session"),
    ):
        store.upsert_conversation_runtime_session(
            task.conversation_id, route_name, session_id, "contract"
        )
    route = _claude_route()
    attempt = store.claim_agent_runtime_attempt(
        run.id,
        route.name,
        route.runtime_kind.value,
        route.credential_mode.value,
        route.model,
        session_mode="resume",
        source_session_id="claude-session",
    )
    attempt = store.mark_agent_runtime_attempt_running_once(attempt.id)
    attempt = store.fail_agent_runtime_attempt(
        attempt.id,
        "session",
        "session_route_incompatible",
        True,
        session_id="claude-session",
    )
    process = AgentTurnProcess(
        store=store, task=task, workspace=tmp_path, owner="consumer"
    )

    process._clear_incompatible_route_session_for_fresh_retry(
        run=run, route=route, failed_attempt=attempt
    )

    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api"
    ) is None
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_oauth"
    ) == "oauth-session"
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_api"
    ) == "api-session"
    persisted = store.get_agent_runtime_attempt(attempt.id)
    assert persisted is not None
    assert persisted.session_mode == "resume"
    assert persisted.source_session_id == "claude-session"


def test_malformed_or_legacy_claude_session_never_resumes(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    process = AgentTurnProcess(
        store=store, task=task, workspace=tmp_path, owner="consumer"
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id, "claude_api", "legacy-claude-session"
    )
    assert process._session_for_route(
        _claude_route(),
        role=AgentRole.CONSUMER,
        requested_session_id=None,
        recovery_phase="",
        conversation_contract_hash="current-contract",
    ) is None
    with store._connect() as db:
        db.execute(
            "update conversation_runtime_sessions set session_id='--malformed', "
            "contract_hash='current-contract' where conversation_id=? "
            "and route_name='claude_api'",
            (task.conversation_id,),
        )

    with pytest.raises(ValueError, match="Claude session_id"):
        process._session_for_route(
            _claude_route(),
            role=AgentRole.CONSUMER,
            requested_session_id=None,
            recovery_phase="",
            conversation_contract_hash="current-contract",
        )


def test_claude_success_uses_trusted_session_without_codex_history_and_resumes(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    route = _claude_route()
    store.upsert_conversation_runtime_session(
        task.conversation_id, "codex_oauth", "oauth-session", "contract-v1"
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id, "codex_api", "api-session", "contract-v1"
    )
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api",
            "CEO_CLAUDE_API_KEY": "test-claude-secret",
            "CEO_CLAUDE_MODEL": route.model,
        }
    )

    def reject_codex_history(*args, **kwargs):
        raise AssertionError("Claude session must not touch Codex history")

    monkeypatch.setattr(
        "app.agent_turn_runner.count_codex_session_lines", reject_codex_history
    )
    monkeypatch.setattr(
        "app.agent_turn_runner.extract_codex_mcp_tool_results_from_session",
        reject_codex_history,
    )

    class OneRouteRouter:
        def first_route_decision(self, **kwargs):
            return RuntimeRouteDecision(route, False, "eligible_route")

    raw_result = json.dumps(
        {
            "outcome": "no_action",
            "summary": "Nothing to do.",
            "proposal": None,
            "decision_options": [],
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )
    session_id = "collision-codex-session"
    stream = "\n".join(
        (
            json.dumps(
                {"type": "system", "subtype": "init", "session_id": session_id}
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": session_id,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": raw_result}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "session_id": session_id,
                    "result": raw_result,
                }
            ),
        )
    )

    class Executor:
        def __init__(self, current_stream):
            self.commands = []
            self.stream = current_stream

        def __call__(self, command, *, on_stdout_line, **kwargs):
            self.commands.append(command)
            for line in self.stream.splitlines():
                on_stdout_line(line)
            return ProcessRunResult(0, self.stream, "")

    executor = Executor(stream)

    def execute(current_task):
        run = _claim_consumer(store, current_task).run
        return AgentTurnProcess(
            store=store,
            task=current_task,
            workspace=tmp_path,
            owner="consumer",
            executor=executor,
            runtime_config=config,
            runtime_router=OneRouteRouter(),
        ).execute(
            run=run,
            prompt="Read-only decision",
            session_id=None,
            developer_instructions="Return the exact schema.",
            configure_command=lambda command: None,
            parse_result=parse_consumer_agent_wire_result,
            persist_conversation_session=True,
            conversation_contract_hash="contract-v1",
        )

    execute(task)
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api", required_contract_hash="contract-v1"
    ) == session_id

    def stream_for_result(result_text: str) -> str:
        return "\n".join(
            (
                json.dumps(
                    {"type": "system", "subtype": "init", "session_id": session_id}
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "session_id": session_id,
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": result_text}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "session_id": session_id,
                        "result": result_text,
                    }
                ),
            )
        )

    def next_task(message_id: str, generation: str):
        assert store.enqueue_reply_task(
            conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            single_chat=task.single_chat,
            trigger_message_id=message_id,
            trigger_create_time="2026-08-06 10:02:00",
            trigger_sender="Derek",
            trigger_text="Handle another task",
            execution_generation=generation,
        )
        pending = store.get_reply_task_for_message(task.conversation_id, message_id)
        assert pending is not None
        claimed = store.claim_reply_task(pending.id)
        assert claimed is not None
        return claimed

    sensitive_result = json.dumps(
        {
            "outcome": "no_action",
            "summary": "Bearer sk-secret-must-not-persist",
            "proposal": None,
            "decision_options": [],
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )
    executor.stream = stream_for_result(sensitive_result)
    sensitive_task = next_task("msg-turns-secret", "generation-secret")
    with pytest.raises(ValueError, match="agent_result_contains_sensitive_value"):
        execute(sensitive_task)
    [sensitive_attempt] = store.list_agent_runtime_attempts(
        store.list_agent_runs_for_task_generation(
            sensitive_task.id, sensitive_task.execution_generation
        )[0].id
    )
    assert sensitive_attempt.session_id == ""
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api", required_contract_hash="contract-v1"
    ) == session_id

    executor.stream = stream_for_result('{"outcome":"no_action"}')
    invalid_task = next_task("msg-turns-invalid", "generation-invalid")
    with pytest.raises(RuntimeError, match="claude_result_validation_failed"):
        execute(invalid_task)
    [invalid_attempt] = store.list_agent_runtime_attempts(
        store.list_agent_runs_for_task_generation(
            invalid_task.id, invalid_task.execution_generation
        )[0].id
    )
    assert invalid_attempt.session_id == ""
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api", required_contract_hash="contract-v1"
    ) == session_id

    failed_result = json.dumps(
        {
            "outcome": "failed",
            "summary": "The business decision failed.",
            "proposal": None,
            "decision_options": [],
            "error_code": "business_decision_failed",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )
    executor.stream = stream_for_result(failed_result)
    failed_task = next_task("msg-turns-failed", "generation-failed")
    execute(failed_task)
    [failed_attempt] = store.list_agent_runtime_attempts(
        store.list_agent_runs_for_task_generation(
            failed_task.id, failed_task.execution_generation
        )[0].id
    )
    assert failed_attempt.status == "failed"
    assert failed_attempt.failure_code == "runtime_business_result_failed"
    assert failed_attempt.session_id == ""
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api", required_contract_hash="contract-v1"
    ) == session_id

    executor.stream = stream
    crash_task = next_task("msg-turns-crash", "generation-crash")
    original_complete_agent_run = store.complete_agent_run
    parent_writes = 0

    def crash_before_parent_terminal(*args, **kwargs):
        nonlocal parent_writes
        parent_writes += 1
        raise RuntimeError("injected_parent_terminal_failure")

    monkeypatch.setattr(store, "complete_agent_run", crash_before_parent_terminal)
    with pytest.raises(RuntimeError, match="injected_parent_terminal_failure"):
        execute(crash_task)
    executor_calls_after_crash = len(executor.commands)
    [crash_run] = store.list_agent_runs_for_task_generation(
        crash_task.id, crash_task.execution_generation
    )
    [crash_attempt] = store.list_agent_runtime_attempts(crash_run.id)
    assert crash_run.status == "running"
    assert crash_attempt.status == "completed"
    assert crash_attempt.session_id == session_id
    assert crash_attempt.result_envelope_json
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api", required_contract_hash="contract-v1"
    ) == session_id

    monkeypatch.setattr(store, "complete_agent_run", original_complete_agent_run)
    execute(crash_task)
    assert len(executor.commands) == executor_calls_after_crash
    recovered_run = store.get_agent_run(crash_run.id)
    assert recovered_run is not None and recovered_run.status == "completed"
    assert parent_writes == 1
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_oauth", required_contract_hash="contract-v1"
    ) == "oauth-session"
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_api", required_contract_hash="contract-v1"
    ) == "api-session"
    [first_attempt] = store.list_agent_runtime_attempts(
        store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation
        )[0].id
    )
    assert first_attempt.session_id == session_id

    assert store.enqueue_reply_task(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_message_id="msg-turns-2",
        trigger_create_time="2026-08-06 10:01:00",
        trigger_sender="Derek",
        trigger_text="Handle the next task",
        execution_generation="generation-2",
    )
    second = store.get_reply_task_for_message(task.conversation_id, "msg-turns-2")
    assert second is not None
    second = store.claim_reply_task(second.id)
    assert second is not None
    executor.stream = stream
    execute(second)

    assert "--resume" not in executor.commands[0]
    resume_index = executor.commands[1].index("--resume")
    assert executor.commands[1][resume_index + 1] == session_id
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api", required_contract_hash="contract-v1"
    ) == session_id


def _effect_event(event_type="item.started", **metadata):
    return {
        "type": event_type,
        "item": {
            "type": "mcp_tool_call",
            "id": "write-1",
            "status": "completed" if event_type == "item.completed" else "in_progress",
            "metadata": {
                "effect": "effectful",
                "operation_id": "operation-0",
                **metadata,
            },
        },
    }


def test_task_generation_can_store_consumer_and_multiple_audit_attempts(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    a0 = _claim_consumer(store, task)
    b0 = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=a0.run.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="audit-0",
    )
    b1 = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=a0.run.id,
        operation_id=b0.run.operation_id,
        owner="audit-1",
    )

    assert len({a0.run.id, b0.run.id, b1.run.id}) == 3
    assert store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation
    ) == [a0.run, b0.run, b1.run]


def test_same_turn_identity_is_idempotent(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)

    first = _claim_consumer(store, task, owner="one")
    second = _claim_consumer(store, task, owner="two")

    assert first.run.id == second.run.id
    assert second.claimed is False


def test_runtime_attempt_process_start_is_claimed_exactly_once(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run
    attempt = store.claim_agent_runtime_attempt(
        run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
    )

    running = store.mark_agent_runtime_attempt_running_once(attempt.id)

    assert running.status == "running"
    with pytest.raises(
        AgentRuntimeAttemptStartConflictError,
        match="process start already claimed",
    ):
        store.mark_agent_runtime_attempt_running_once(attempt.id)


def test_role_runtime_capabilities_include_policy_and_exact_action_tools(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    consumer = _claim_consumer(store, task).run
    audit = _claim_audit(store, task)

    assert _required_runtime_capabilities(
        run=consumer,
        recovery_phase="",
        expected_effect_actions=(),
    ) == frozenset(
        {
            "structured_output",
            "local_schema_validation",
            "consumer_read_only_enforcement",
            "reviewed_read_tools",
        }
    )
    assert _required_runtime_capabilities(
        run=audit,
        recovery_phase="reconcile",
        expected_effect_actions=({"capability": "agent_cli.dws"},),
    ) == frozenset(
        {
            "structured_output",
            "local_schema_validation",
            "consumer_read_only_enforcement",
            "reconciliation_read_only",
            "reviewed_read_tools",
            "agent_cli.dws",
        }
    )


def test_turn_operation_identity_is_role_specific(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)

    with pytest.raises(ValueError, match="Consumer operation_id must be empty"):
        store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="unexpected",
            owner="consumer",
        )
    with pytest.raises(ValueError, match="Audit operation_id must be non-empty"):
        store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner="audit",
        )


def test_consumer_turn_cannot_persist_a_side_effect(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run

    with pytest.raises(ValueError, match="Consumer Agent cannot persist side effects"):
        store.complete_agent_run(
            run.id,
            {"outcome": "proposal"},
            owner="consumer",
            side_effect_state="confirmed",
        )


def test_consumer_turn_rejects_effectful_tool_events(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run

    with pytest.raises(ValueError, match="Consumer Agent cannot persist side effects"):
        store.append_agent_run_event(
            run.id,
            {
                "type": "item.started",
                "item": {
                    "type": "mcp_tool_call",
                    "id": "call-1",
                    "server": "business",
                    "tool": "write",
                    "metadata": {"effect": "effectful"},
                },
            },
            owner="consumer",
        )

    assert store.get_agent_run(run.id).side_effect_state == "none"


def test_unknown_reconciliation_event_limit_suspends_inner_recovery(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    store.mark_agent_run_unknown(
        run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="audit",
    )
    claim = store.claim_unknown_agent_run(run.id, owner="audit-recovery")
    assert claim.claimed

    AgentTurnProcess(
        store=store,
        task=task,
        workspace=Path("/workspace"),
        owner="audit-recovery",
    )._defer_unknown(claim.run, RECONCILIATION_EVENT_LIMIT_ERROR)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert persisted.reconciliation_suspended is True
    error = json.loads(persisted.structured_error_json)
    assert error["code"] == RECONCILIATION_EVENT_LIMIT_ERROR
    assert error["retryable"] is False


def test_event_limited_unknown_run_is_suspended_before_pending_claim(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    store.mark_agent_run_unknown(
        run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="audit",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update agent_runs set reconciliation_event_count=? where id=?",
            (MAX_RECONCILIATION_EVENTS, run.id),
        )

    assert store.suspend_reconciliation_event_limited_agent_runs() == 1
    assert [item.id for item in store.list_suspended_unknown_agent_runs()] == [run.id]
    assert store.peek_reply_tasks(limit=10) == []


def test_suspended_unknown_run_closes_pending_task_once_for_human_resolution(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    oa_url = (
        "https://aflow.dingtalk.com/process?procInstId=process-123&taskId=task-456"
    )
    with sqlite3.connect(store.path) as db:
        db.execute("update reply_tasks set oa_url=? where id=?", (oa_url, task.id))
    run = _claim_audit(store, task)
    store.mark_agent_run_unknown(
        run.id,
        {"code": "audit_reconciliation_evidence_mismatch", "retryable": True},
        owner="audit",
    )
    claim = store.claim_unknown_agent_run(run.id, owner="audit-recovery")
    assert claim.claimed
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "audit_reconciliation_evidence_mismatch", "retryable": False},
        owner="audit-recovery",
        expected_execution_generation=task.execution_generation,
        next_attempt_at="",
        suspended=True,
    )
    store.requeue_reply_task(
        task.id,
        "agent_run_unavailable",
        expected_execution_generation=task.execution_generation,
    )

    assert store.suspend_reconciliation_event_limited_agent_runs() == 1

    closed_task = store.get_reply_task(task.id)
    attempt = store.get_latest_reply_attempt_for_trigger(
        task.conversation_id,
        task.trigger_message_id,
    )
    assert closed_task is not None and closed_task.status == "failed"
    assert closed_task.available_at == ""
    assert attempt is not None and attempt.send_status == "needs_human"
    assert attempt.agent_run_id == run.id
    assert attempt.send_error == RECONCILIATION_EVENT_LIMIT_ERROR
    assert attempt.oa_process_instance_id == "process-123"
    assert attempt.oa_task_id == "task-456"
    assert attempt.oa_url == oa_url
    attempt_count = store.count_reply_attempts()

    assert store.suspend_reconciliation_event_limited_agent_runs() == 0
    assert store.count_reply_attempts() == attempt_count
    assert store.claim_reply_task(task.id) is None


def _normalize_read_skill_event(store, task, payload):
    return AgentTurnProcess(
        store=store,
        task=task,
        workspace=Path("/workspace"),
        owner="consumer",
    )._normalized_effect_event(payload, read_only=True, operation_id="")


def test_normal_audit_write_receipt_does_not_require_recovery_authorization(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    argv = [
        "dws", "chat", "+messages-send", "--open-dingtalk-id", "recipient-1",
        "--text", "done", "--yes", "--format", "json",
    ]
    descriptor = describe_native_command(
        {"type": "command_execution", "argv": argv}
    )
    assert descriptor is not None
    payload = {
        "type": "item.completed",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "server": "agent_cli",
            "tool": "execute_reviewed_write",
            "arguments": {"argv": argv, "authorization_id": "not-a-recovery-id"},
            "status": "completed",
            "result": {
                "structuredContent": {
                    "cli": "dws",
                    "operation": descriptor.command_path,
                    "operation_digest": descriptor.command_digest,
                    "target_identifiers": descriptor.target_identifiers,
                    "result_digest": "result-digest",
                    "stdout": "{}",
                },
                "isError": False,
            },
        },
    }

    event = AgentTurnProcess(
        store=store,
        task=task,
        workspace=Path("/workspace"),
        owner="audit",
    )._normalized_effect_event(
        payload,
        read_only=False,
        operation_id="operation-1",
    )

    assert event is not None
    assert event["type"] == "item.completed"


def _read_skill_payload(
    path: Path,
    content: str,
    sha256: str,
    *,
    wrapper: str = "both",
    result_path: str | None = None,
    result_name: str = "business-review",
):
    receipt = {
        "content": content,
        "sha256": sha256,
        "path": result_path or str(path.resolve()),
        "name": result_name,
    }
    result = {"isError": False}
    if wrapper in {"both", "content"}:
        result["content"] = [{"type": "text", "text": json.dumps(receipt)}]
    if wrapper in {"both", "structured"}:
        result["structuredContent"] = receipt
    return {
        "type": "item.completed",
        "item": {
            "id": "skill-1",
            "type": "mcp_tool_call",
            "server": "agent_cli",
            "tool": "read_skill",
            "arguments": {"path": str(path)},
            "status": "completed",
            "result": result,
        },
    }


def test_completed_read_skill_persists_verified_metadata_without_content(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    content = "# Business review\n"
    skill_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS", (tmp_path / "skills",)
    )
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)

    event = _normalize_read_skill_event(
        store,
        task,
        _read_skill_payload(
            skill_path,
            content,
            hashlib.sha256(content.encode()).hexdigest(),
        ),
    )

    assert event is not None
    assert event["type"] == "item.completed"
    assert event["item"]["metadata"] | {
        "skill_path": str(skill_path),
        "skill_name": "business-review",
        "skill_sha256": hashlib.sha256(content.encode()).hexdigest(),
    } == event["item"]["metadata"]
    assert "content" not in json.dumps(event)
    assert "result" not in event["item"]
    assert "arguments" not in event["item"]


def test_completed_read_skill_normalizes_alias_to_trusted_result_path(
    tmp_path, monkeypatch
):
    root = tmp_path / "skills"
    skill_path = root / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    content = "# Business review\n"
    skill_path.write_text(content, encoding="utf-8")
    alias = tmp_path / "skill-alias.md"
    alias.symlink_to(skill_path)
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))
    store = AutoReplyStore(tmp_path / "turns.sqlite3")

    event = _normalize_read_skill_event(
        store,
        _task(store),
        _read_skill_payload(
            alias,
            content,
            hashlib.sha256(content.encode()).hexdigest(),
            result_path=str(skill_path.resolve()),
        ),
    )

    assert event is not None
    assert event["type"] == "item.completed"
    assert event["item"]["metadata"]["skill_path"] == str(skill_path.resolve())


def test_malformed_unicode_skill_content_becomes_failed_controlled_event(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("valid", encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS", (tmp_path / "skills",)
    )
    store = AutoReplyStore(tmp_path / "turns.sqlite3")

    event = _normalize_read_skill_event(
        store,
        _task(store),
        _read_skill_payload(skill_path, "\ud800", "0" * 64),
    )

    assert event is not None
    assert event["type"] == "item.failed"
    assert event["item"]["metadata"]["failure_code"] == (
        "agent_cli_skill_receipt_invalid"
    )


def test_skill_receipt_hash_resource_error_becomes_failed_controlled_event(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    content = "# Business review\n"
    skill_path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS", (tmp_path / "skills",)
    )

    class FailingHashlib:
        @staticmethod
        def sha256(_content):
            raise MemoryError("hash allocation failed")

    monkeypatch.setattr("app.agent_skill_usage.hashlib", FailingHashlib())
    store = AutoReplyStore(tmp_path / "turns.sqlite3")

    event = _normalize_read_skill_event(
        store,
        _task(store),
        _read_skill_payload(skill_path, content, digest),
    )

    assert event is not None
    assert event["type"] == "item.failed"
    assert event["item"]["metadata"]["failure_code"] == (
        "agent_cli_skill_receipt_invalid"
    )


@pytest.mark.parametrize("wrapper", ("structured", "content"))
def test_completed_read_skill_accepts_current_mcp_result_wrappers(
    tmp_path, monkeypatch, wrapper
):
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    content = "# Business review\n"
    skill_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS", (tmp_path / "skills",)
    )
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)

    event = _normalize_read_skill_event(
        store,
        task,
        _read_skill_payload(
            skill_path,
            content,
            hashlib.sha256(content.encode()).hexdigest(),
            wrapper=wrapper,
        ),
    )

    assert event is not None
    assert event["type"] == "item.completed"
    assert event["item"]["metadata"]["skill_path"] == str(skill_path)


@pytest.mark.parametrize("case", ("digest_mismatch", "path_mismatch"))
def test_malformed_read_skill_receipt_is_normalized_as_failed(
    tmp_path, monkeypatch, case
):
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    content = "# Business review\n"
    skill_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS", (tmp_path / "skills",)
    )
    requested_path = skill_path
    result_path = str(skill_path.resolve())
    if case == "path_mismatch":
        result_path = str(skill_path.parent / "different.md")
    digest = hashlib.sha256(content.encode()).hexdigest()
    if case == "digest_mismatch":
        digest = "0" * 64
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)

    event = _normalize_read_skill_event(
        store,
        task,
        _read_skill_payload(
            requested_path,
            content,
            digest,
            result_path=result_path,
        ),
    )

    assert event is not None
    assert event["type"] == "item.failed"
    assert event["item"]["status"] == "failed"
    assert "skill_path" not in event["item"]["metadata"]
    assert event["item"]["metadata"]["failure_code"] == "agent_cli_skill_receipt_invalid"


def test_effect_started_persists_minimal_identity_and_matching_completion_confirms(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    started = _effect_event(
        capability="agent_cli.dws",
        operation="chat message send",
        operation_digest="command-digest",
        arguments_digest="arguments-digest",
        target_identifiers={"group": "cid"},
    )
    after_start = store.append_agent_run_event(run.id, started, owner="audit")
    assert after_start.side_effect_state == "unknown"
    persisted_start = store.get_agent_run(run.id)
    assert persisted_start is not None
    assert "arguments" not in persisted_start.tool_events[0]["item"]
    assert "result" not in persisted_start.tool_events[0]["item"]

    completed = {**started, "type": "item.completed"}
    after_completed = store.append_agent_run_event(run.id, completed, owner="audit")
    assert after_completed.side_effect_state == "confirmed"


def test_mismatched_effect_completion_cannot_confirm_started_identity(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    started = _effect_event(
        operation_digest="original",
        arguments_digest="arguments",
        target_identifiers={"group": "cid"},
    )
    store.append_agent_run_event(run.id, started, owner="audit")
    mismatched = {
        **started,
        "type": "item.completed",
        "item": {
            **started["item"],
            "metadata": {
                **started["item"]["metadata"],
                "operation_digest": "different",
            },
        },
    }

    with pytest.raises(ValueError, match="effect completion identity mismatch"):
        store.append_agent_run_event(run.id, mismatched, owner="audit")

    assert store.get_agent_run(run.id).side_effect_state == "unknown"


def test_effect_event_operation_id_must_match_audit_run(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)

    with pytest.raises(ValueError, match="effect operation identity mismatch"):
        store.append_agent_run_event(
            run.id,
            {
                "type": "item.started",
                "item": {
                    "type": "mcp_tool_call",
                    "id": "write-1",
                    "metadata": {
                        "effect": "effectful",
                        "operation_id": "operation-other",
                    },
                },
            },
            owner="audit",
        )


def test_failed_effect_closes_started_identity_without_confirmation(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    started = _effect_event(operation_digest="same")
    store.append_agent_run_event(run.id, started, owner="audit")
    failed = {**started, "type": "item.failed"}
    closed = store.append_agent_run_event(run.id, failed, owner="audit")

    assert closed.side_effect_state == "none"


def test_two_same_call_starts_with_one_completion_remains_unknown(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    run = _claim_audit(store, _task(store))
    started = _effect_event(
        operation_digest="same",
        arguments_digest="same-arguments",
        target_identifiers={"group": "cid"},
    )

    store.append_agent_run_event(run.id, started, owner="audit")
    store.append_agent_run_event(run.id, started, owner="audit")
    persisted = store.append_agent_run_event(
        run.id,
        {**started, "type": "item.completed"},
        owner="audit",
    )

    assert persisted.side_effect_state == "unknown"
    assert persisted.effect_started_count == 2
    assert persisted.effect_completed_count == 1


def test_agent_effect_state_uses_incremental_counters_not_history_scan(tmp_path):
    statements: list[str] = []

    class TracedStore(AutoReplyStore):
        def _open_connection(self):
            connection = super()._open_connection()
            connection.set_trace_callback(statements.append)
            return connection

    store = TracedStore(tmp_path / "turns.sqlite3")
    run = _claim_audit(store, _task(store))
    statements.clear()

    persisted = store.append_agent_run_event(
        run.id,
        _effect_event(operation_digest="digest"),
        owner="audit",
    )

    normalized = [statement.casefold() for statement in statements]
    assert persisted.effect_started_count == 1
    assert persisted.side_effect_state == "unknown"
    assert not any("with call_state" in statement for statement in normalized)
    assert sum("from agent_run_events" in statement for statement in normalized) <= 4


def test_legacy_unknown_start_binds_exact_action_index_once(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    run = _claim_audit(store, _task(store))
    identity = {
        "capability": "agent_cli.dws",
        "operation": "chat message send",
        "operation_digest": "command-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"group": "cid-one"},
    }
    store.append_agent_run_event(
        run.id,
        _effect_event(**identity),
        owner="audit",
    )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "crash_after_write"},
        owner="audit",
    )
    assert store.claim_unknown_agent_run(run.id, owner="recovery").claimed

    assert store.bind_legacy_unknown_effect_action(
        run.id,
        action_index=1,
        operation_id="operation-0",
        expected_identity=identity,
        owner="recovery",
    )
    assert not store.bind_legacy_unknown_effect_action(
        run.id,
        action_index=1,
        operation_id="operation-0",
        expected_identity=identity,
        owner="recovery",
    )
    receipt_operation_id = (
        '{"action_index":1,"arguments_digest":"arguments-digest",'
        '"capability":"agent_cli.dws","operation":"chat message send",'
        '"operation_digest":"command-digest",'
        '"proposal_operation_id":"operation-0"}'
    )
    store.record_agent_execution_receipt(
        run.id,
        receipt_id="legacy-present",
        operation_id=receipt_operation_id,
        cli="dws",
        command_path="chat message send",
        command_digest="command-digest",
        exit_code=0,
        owner="recovery",
        expected_status="unknown",
    )
    store.confirm_agent_execution_receipt(
        run.id, receipt_operation_id, owner="recovery"
    )
    store.confirm_agent_execution_receipt(
        run.id, receipt_operation_id, owner="recovery"
    )

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert persisted.tool_events[0]["item"]["metadata"]["action_index"] == 1
    assert persisted.effect_receipt_count == 1
    assert persisted.side_effect_state == "confirmed"


def test_effect_counter_backfill_is_migration_safe(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    run = _claim_audit(store, _task(store))
    started = _effect_event(operation_digest="digest")
    store.append_agent_run_event(run.id, started, owner="audit")
    store.append_agent_run_event(
        run.id,
        {**started, "type": "item.completed"},
        owner="audit",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update agent_runs set effect_started_count=0, "
            "effect_completed_count=0, effect_failed_count=0, "
            "effect_receipt_count=0, effect_unreviewed_count=0 where id=?",
            (run.id,),
        )
        db.row_factory = sqlite3.Row
        AutoReplyStore._backfill_agent_run_effect_counters(db)

    migrated = store.get_agent_run(run.id)
    assert migrated is not None
    assert migrated.effect_started_count == 1
    assert migrated.effect_completed_count == 1
    assert migrated.side_effect_state == "confirmed"


def _create_pre_role_database(path: Path) -> Path:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            pragma foreign_keys=on;
            create table reply_tasks (
                id integer primary key autoincrement,
                channel text not null default 'dingtalk',
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                trigger_message_json text not null default '{}',
                available_at text not null default '',
                force_new_decision integer not null default 0,
                oa_url text not null default '',
                manual_rerun_attempt_id integer not null default 0,
                manual_rerun_revision_key text not null default '',
                execution_generation text not null default 'initial',
                status text not null default 'done',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(channel, conversation_id, trigger_message_id)
            );
            create table agent_runs (
                id integer primary key autoincrement,
                reply_task_id integer not null,
                execution_generation text not null,
                status text not null default 'pending',
                codex_session_id text not null default '',
                transcript_start_line integer not null default 0,
                transcript_end_line integer not null default 0,
                final_result_json text not null default '',
                structured_error_json text not null default '',
                tool_events_json text not null default '[]',
                side_effect_state text not null default 'none',
                lease_owner text not null default '',
                lease_expires_at text not null default '',
                reconciliation_attempts integer not null default 0,
                reconciliation_next_attempt_at text not null default '',
                reconciliation_suspended integer not null default 0,
                started_at text not null default '',
                completed_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(reply_task_id, execution_generation),
                foreign key(reply_task_id) references reply_tasks(id)
            );
            create table agent_run_events (
                id integer primary key autoincrement,
                agent_run_id integer not null,
                sequence integer not null,
                event_json text not null,
                event_type text not null default '',
                call_id text not null default '',
                effect_kind text not null default '',
                receipt_operation_id text not null default '',
                event_scope text not null default 'direct',
                created_at text not null default current_timestamp,
                unique(agent_run_id, sequence),
                foreign key(agent_run_id) references agent_runs(id)
            );
            create table agent_execution_receipts (
                id integer primary key autoincrement,
                agent_run_id integer not null,
                receipt_id text not null,
                operation_id text not null,
                cli text not null,
                command_path text not null,
                command_digest text not null,
                exit_code integer not null,
                completed integer not null,
                persisted integer not null,
                safe_to_confirm integer not null,
                created_at text not null default current_timestamp,
                unique(agent_run_id, operation_id),
                foreign key(agent_run_id) references agent_runs(id)
            );
            insert into reply_tasks (
                id, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, execution_generation, status
            ) values (1, 'cid-old', 'Old', 0, 'msg-old',
                      '2026-08-05 10:00:00', 'Derek', 'old task', 'old-gen', 'done');
            insert into agent_runs (
                id, reply_task_id, execution_generation, status,
                codex_session_id, final_result_json, completed_at,
                created_at, updated_at
            ) values (7, 1, 'old-gen', 'completed', 'session-old',
                      '{"outcome":"completed"}', '2026-08-05 10:02:00',
                      '2026-08-05 10:00:00', '2026-08-05 10:02:00');
            insert into agent_run_events (
                id, agent_run_id, sequence, event_json, event_type,
                event_scope, created_at
            ) values (8, 7, 1, '{"type":"item.completed"}', 'item.completed',
                      'reconciliation', '2026-08-06 15:00:00');
            insert into agent_execution_receipts (
                id, agent_run_id, receipt_id, operation_id, cli,
                command_path, command_digest, exit_code, completed,
                persisted, safe_to_confirm, created_at
            ) values (9, 7, 'receipt-1', 'operation-1', 'dws',
                      'chat.message.send', 'digest-1', 0, 1, 1, 1,
                      '2026-08-06 15:01:00');
            """
        )
    return path


def test_agent_run_migration_preserves_events_and_receipts(tmp_path):
    db_path = _create_pre_role_database(tmp_path / "old.sqlite3")

    store = AutoReplyStore(db_path)
    run = store.get_agent_run(7)

    assert run is not None
    assert run.role is AgentRole.AUDIT
    assert run.proposal_revision == 0
    assert run.turn_attempt == 0
    assert run.parent_agent_run_id is None
    assert run.operation_id == ""
    assert run.tool_events == [{"type": "item.completed"}]
    assert run.reconciliation_event_count == 1
    assert store.list_agent_execution_receipts(7)[0].receipt_id == "receipt-1"
    assert store.foreign_key_violations() == []
    with sqlite3.connect(db_path) as db:
        event = db.execute(
            "select id, created_at from agent_run_events where agent_run_id=7"
        ).fetchone()
        receipt = db.execute(
            "select id, created_at from agent_execution_receipts where agent_run_id=7"
        ).fetchone()
    assert event == (8, "2026-08-06 15:00:00")
    assert receipt == (9, "2026-08-06 15:01:00")
    assert run.effect_started_count == 0
    assert store.list_agent_execution_receipts(7)[0].effect_counted is False


def test_agent_run_migration_preserves_existing_turn_identity(tmp_path):
    db_path = _create_pre_role_database(tmp_path / "partial.sqlite3")
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            alter table agent_runs add column role text not null default 'audit';
            alter table agent_runs add column proposal_revision integer not null default 0;
            alter table agent_runs add column turn_attempt integer not null default 0;
            alter table agent_runs add column parent_agent_run_id integer;
            alter table agent_runs add column operation_id text not null default '';
            update agent_runs
            set role='consumer', proposal_revision=2, turn_attempt=3;
            """
        )

    run = AutoReplyStore(db_path).get_agent_run(7)

    assert run is not None
    assert run.role is AgentRole.CONSUMER
    assert run.proposal_revision == 2
    assert run.turn_attempt == 3


def test_agent_run_migration_rolls_back_before_commit_on_foreign_key_failure(
    tmp_path,
):
    db_path = _create_pre_role_database(tmp_path / "broken.sqlite3")
    with sqlite3.connect(db_path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute(
            """
            insert into agent_run_events (
                id, agent_run_id, sequence, event_json, event_type
            ) values (10, 999, 1, '{}', 'item.completed')
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="broke foreign keys"):
        AutoReplyStore(db_path)

    with sqlite3.connect(db_path) as db:
        columns = {
            row[1] for row in db.execute("pragma table_info(agent_runs)").fetchall()
        }
        orphan = db.execute(
            "select agent_run_id from agent_run_events where id=10"
        ).fetchone()
    assert "role" not in columns
    assert orphan == (999,)


def test_absent_reconciliation_supersedes_other_running_turns(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    consumer = _claim_consumer(store, task).run
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="operation-0",
        owner="audit",
        now="2026-08-06 10:00:00",
    ).run
    store.mark_agent_run_unknown(
        audit.id,
        {"code": "outcome_unknown"},
        owner="audit",
        now="2026-08-06 10:00:01",
    )
    assert store.claim_unknown_agent_run(
        audit.id,
        owner="reconciler",
        now="2026-08-06 10:00:02",
    ).claimed

    store.resolve_unknown_agent_run_absent(
        audit.id,
        task.id,
        code="effect_absent",
        owner="reconciler",
        now="2026-08-06 10:00:03",
    )

    assert store.get_agent_run(consumer.id).status == "failed"


def test_consumer_unknown_rows_are_not_reconciliation_candidates(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    consumer = _claim_consumer(store, task).run
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            update agent_runs
            set status='unknown', side_effect_state='unknown',
                reconciliation_suspended=1
            where id=?
            """,
            (consumer.id,),
        )

    assert store.list_suspended_unknown_agent_runs() == []
    assert store.list_unknown_agent_runs() == []
    with pytest.raises(AgentRunLeaseLostError):
        store.resume_suspended_unknown_agent_run(
            consumer.id,
            expected_execution_generation=task.execution_generation,
        )


def test_single_chat_trigger_replacement_supersedes_running_turn(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-single",
        conversation_title="Single chat",
        single_chat=True,
        trigger_message_id="msg-old",
        trigger_create_time="2026-08-06 10:00:00",
        trigger_sender="Derek",
        trigger_text="old",
        execution_generation="generation-old",
    )
    task = store.get_reply_task_for_message("cid-single", "msg-old")
    assert task is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="operation-old",
        owner="audit",
    ).run

    assert store.replace_pending_single_chat_reply_task_trigger(
        conversation_id="cid-single",
        trigger_message_id="msg-new",
        trigger_create_time="2026-08-06 10:01:00",
        trigger_sender="Derek",
        trigger_text="new",
        trigger_message_json="{}",
    ) == 1

    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.execution_generation != task.execution_generation
    assert store.get_agent_run(run.id).status == "failed"


def test_duplicate_single_chat_trigger_does_not_supersede_running_turn(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-single",
        conversation_title="Single chat",
        single_chat=True,
        trigger_message_id="msg-current",
        trigger_create_time="2026-08-06 10:00:00",
        trigger_sender="Derek",
        trigger_text="same",
        trigger_message_json="{}",
        execution_generation="generation-current",
    )
    task = store.get_reply_task_for_message("cid-single", "msg-current")
    assert task is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="operation-current",
        owner="audit",
    ).run

    assert store.replace_pending_single_chat_reply_task_trigger(
        conversation_id="cid-single",
        trigger_message_id="msg-current",
        trigger_create_time="2026-08-06 10:00:00",
        trigger_sender="Derek",
        trigger_text="same",
        trigger_message_json="{}",
    ) == 0

    assert store.get_agent_run(run.id).status == "running"


def test_audit_parent_must_be_consumer_turn_from_same_task_generation(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    first = _task(store)
    parent = _claim_consumer(store, first).run
    store.enqueue_reply_task(
        conversation_id="cid-other",
        conversation_title="Other",
        single_chat=False,
        trigger_message_id="msg-other",
        trigger_create_time="2026-08-06 10:01:00",
        trigger_sender="Derek",
        trigger_text="other",
        execution_generation="generation-other",
    )
    second = store.claim_reply_tasks(limit=1)[0]

    with pytest.raises(ValueError, match="Audit parent must be the matching Consumer turn"):
        store.claim_agent_run(
            second.id,
            second.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=parent.id,
            operation_id="operation-other",
            owner="audit",
        )


def test_consumer_parent_follows_previous_audit_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    consumer_0 = _claim_consumer(store, task).run
    audit_0 = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer_0.id,
        operation_id="operation-0",
        owner="audit",
    ).run

    consumer_1 = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=1,
        turn_attempt=0,
        parent_agent_run_id=audit_0.id,
        operation_id="",
        owner="consumer-1",
    ).run

    assert consumer_1.parent_agent_run_id == audit_0.id
    consumer_2_retry = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=1,
        turn_attempt=1,
        parent_agent_run_id=audit_0.id,
        operation_id="",
        owner="consumer-2",
    ).run
    assert consumer_2_retry.turn_attempt == 1
    with pytest.raises(ValueError, match="Initial Consumer parent must be empty"):
        store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=audit_0.id,
            operation_id="",
            owner="consumer-invalid",
        )


def test_clear_agent_run_session_targets_one_turn(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    consumer = _claim_consumer(store, task).run
    consumer = store.set_agent_run_session(
        consumer.id,
        "consumer-session",
        owner="consumer",
    )
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="operation-0",
        owner="audit",
    ).run
    store.set_agent_run_session(audit.id, "audit-session", owner="audit")

    assert store.clear_agent_run_session(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    ) == 1

    assert store.get_agent_run(consumer.id).codex_session_id == "consumer-session"
    assert store.get_agent_run(audit.id).codex_session_id == ""
