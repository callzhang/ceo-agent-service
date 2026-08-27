import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent_contracts import AuditAgentResult, AuditExternalResult, AuditOutcome
from app.agent_effects import McpToolEffectRegistry
from app.agent_result import AgentError, EffectKind
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeCapabilitySnapshot,
    RuntimeKind,
    RuntimeRoute,
    RuntimeRouteSurfaceManifest,
)
from app.agent_runtime_router import AgentRuntimeRouter, RuntimeRouteDecision
from app.agent_turn_runner import (
    AgentTurnProcess,
    _decode_runtime_domain_result,
    _encode_runtime_domain_result,
    _required_runtime_capabilities,
    _runtime_result_evidence,
)
from app.agent_wire_contracts import (
    parse_audit_agent_wire_result,
    parse_consumer_agent_wire_result,
)
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.native_cli_metadata import AgentReadOnlyViolationError, describe_native_command
from app.process_runner import ProcessRunResult
from app.service_codex_config import ServiceMcpServer
from app.store import (
    MAX_RECONCILIATION_EVENTS,
    MAX_UNKNOWN_AUDIT_RECONCILIATION_ATTEMPTS,
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

    def execute(
        current_task,
        *,
        current_prompt="Read-only decision",
        current_developer_instructions="Return the exact schema.",
        prepare_result=None,
    ):
        run = _claim_consumer(store, current_task).run
        return AgentTurnProcess(
            store=store,
            task=current_task,
            workspace=tmp_path,
            owner="consumer",
            executor=executor,
            runtime_config=config,
            runtime_router=OneRouteRouter(),
            claude_adapter=ClaudeRuntimeAdapter(
                workspace=tmp_path,
                config=config,
                claude_bin="claude-test",
                service_mcp_servers=(
                    ServiceMcpServer(name="agent_cli", command="/usr/bin/true"),
                    ServiceMcpServer(
                        name="memory_connector", url="http://127.0.0.1:9/mcp"
                    ),
                ),
            ),
        ).execute(
            run=run,
            prompt=current_prompt,
            session_id=None,
            developer_instructions=current_developer_instructions,
            configure_command=lambda command: None,
            parse_result=parse_consumer_agent_wire_result,
            persist_conversation_session=True,
            prepare_result=prepare_result,
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

    body_marker = "consumer-business-body-marker"
    url_marker = "https://business.example.test/private/source"
    proposal_result = json.dumps(
        {
            "outcome": "proposal",
            "summary": "Prepared the reviewed proposal.",
            "proposal": {
                "objective": "Send the reviewed update.",
                "actions": [
                    {
                        "description": "Send update",
                        "capability": "agent_cli.dws",
                        "operation": "chat message send",
                        "target": {"conversation_id": "cid-1"},
                        "payload": {
                            "document_content": body_marker,
                            "source_url": url_marker,
                        },
                        "expected_verification": "Read it back",
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "Ready.",
            },
            "decision_options": [],
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )
    executor.stream = stream_for_result(proposal_result)
    proposal_task = next_task("msg-turns-proposal", "generation-proposal")
    execute(proposal_task)
    [proposal_run] = store.list_agent_runs_for_task_generation(
        proposal_task.id, proposal_task.execution_generation
    )
    [proposal_attempt] = store.list_agent_runtime_attempts(proposal_run.id)
    proposal_envelope = json.loads(proposal_attempt.result_envelope_json)
    assert proposal_run.status == "completed"
    assert proposal_envelope["result_ref"]["agent_run_id"] == proposal_run.id
    assert "result" not in proposal_envelope
    assert body_marker not in proposal_attempt.result_envelope_json
    assert url_marker not in proposal_attempt.result_envelope_json
    assert body_marker in proposal_run.final_result_json
    assert url_marker in proposal_run.final_result_json

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

    def private_consumer_result(outcome: str, unsafe_value: str) -> str:
        proposal = None
        options = []
        summary = "Reviewed terminal result."
        if outcome == "no_action":
            summary = unsafe_value
        elif outcome == "proposal":
            proposal = {
                "objective": "Prepare the reviewed update.",
                "actions": [
                    {
                        "description": "Prepare update",
                        "capability": "agent_cli.dws",
                        "operation": "chat message send",
                        "target": {"conversation_id": "cid-privacy"},
                        "payload": {"document_content": unsafe_value},
                        "expected_verification": "Read it back",
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "Ready.",
            }
        else:
            options = [
                {
                    "key": "A",
                    "label": "Approve",
                    "instruction": unsafe_value,
                    "consequence": "The reviewed plan may continue.",
                },
                {
                    "key": "B",
                    "label": "Hold",
                    "instruction": "Hold the reviewed option.",
                    "consequence": "No further action is taken.",
                },
            ]
        return json.dumps(
            {
                "outcome": outcome,
                "summary": summary,
                "proposal": proposal,
                "decision_options": options,
                "error_code": (
                    "decision_required" if outcome == "needs_human" else ""
                ),
                "error_retryable": False,
                "error_authorization_required": False,
            },
            separators=(",", ":"),
        )

    for unsafe_kind, unsafe_value, expected_error in (
        (
            "path",
            "file:///private/var/tmp/claude-runtime/transcript-settings.jsonl",
            "runtime_result_contains_local_runtime_leak",
        ),
        (
            "credential",
            "Bearer sk-private-secret-must-not-persist",
            "agent_result_contains_sensitive_value",
        ),
        (
            "signed-url",
            "https://business.example.test/file?X-Amz-Signature=secret",
            "agent_result_contains_sensitive_value",
        ),
        ("oversize", "x" * (33 * 1024), "too_large|summary_invalid"),
    ):
        for outcome in ("proposal", "no_action", "needs_human"):
            executor.stream = stream_for_result(
                private_consumer_result(outcome, unsafe_value)
            )
            private_task = next_task(
                f"msg-turns-{unsafe_kind}-{outcome}",
                f"generation-{unsafe_kind}-{outcome}",
            )
            with pytest.raises(
                (ValueError, AgentReadOnlyViolationError), match=expected_error
            ):
                execute(private_task)
            [private_run] = store.list_agent_runs_for_task_generation(
                private_task.id, private_task.execution_generation
            )
            [private_attempt] = store.list_agent_runtime_attempts(private_run.id)
            assert private_run.status == "failed"
            assert private_run.final_result_json == ""
            assert private_attempt.status == "failed"
            assert private_attempt.session_id == ""
            assert private_attempt.result_envelope_json == ""
            assert store.get_conversation_runtime_session(
                task.conversation_id,
                "claude_api",
                required_contract_hash="contract-v1",
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
    preparation_calls = 0

    def prepare_completed_result(result):
        nonlocal preparation_calls
        preparation_calls += 1
        return result.model_copy(update={"summary": "Prepared exactly once."})

    def crash_before_parent_terminal(*args, **kwargs):
        nonlocal parent_writes
        parent_writes += 1
        raise RuntimeError("injected_parent_terminal_failure")

    monkeypatch.setattr(store, "complete_agent_run", crash_before_parent_terminal)
    with pytest.raises(RuntimeError, match="injected_parent_terminal_failure"):
        execute(crash_task, prepare_result=prepare_completed_result)
    executor_calls_after_crash = len(executor.commands)
    [crash_run] = store.list_agent_runs_for_task_generation(
        crash_task.id, crash_task.execution_generation
    )
    [crash_attempt] = store.list_agent_runtime_attempts(crash_run.id)
    assert crash_run.status == "completed"
    assert crash_attempt.status == "completed"
    assert crash_attempt.session_id == session_id
    assert crash_attempt.result_envelope_json
    crash_envelope = json.loads(crash_attempt.result_envelope_json)
    assert "result" not in crash_envelope
    assert crash_envelope["result_ref"]["agent_run_id"] == crash_run.id
    assert "Nothing to do." not in crash_attempt.result_envelope_json
    assert store.get_conversation_runtime_session(
        task.conversation_id, "claude_api", required_contract_hash="contract-v1"
    ) == session_id

    monkeypatch.setattr(store, "complete_agent_run", original_complete_agent_run)
    execute(crash_task, prepare_result=prepare_completed_result)
    assert len(executor.commands) == executor_calls_after_crash
    assert preparation_calls == 2
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
    first_envelope = json.loads(first_attempt.result_envelope_json)
    assert "result" not in first_envelope
    assert first_envelope["result_ref"]["agent_run_id"] > 0
    assert "Nothing to do." not in first_attempt.result_envelope_json

    needs_human_result = json.dumps(
        {
            "outcome": "needs_human",
            "summary": "A management decision is required.",
            "proposal": None,
            "decision_options": [
                {
                    "key": "A",
                    "label": "Approve",
                    "instruction": "Approve the reviewed option.",
                    "consequence": "The reviewed plan may continue.",
                },
                {
                    "key": "B",
                    "label": "Hold",
                    "instruction": "Hold the reviewed option.",
                    "consequence": "No further action is taken.",
                },
            ],
            "error_code": "decision_required",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )
    executor.stream = stream_for_result(needs_human_result)
    needs_human_task = next_task("msg-turns-human", "generation-human")
    monkeypatch.setattr(store, "complete_agent_run", crash_before_parent_terminal)
    with pytest.raises(RuntimeError, match="injected_parent_terminal_failure"):
        execute(needs_human_task)
    needs_human_executor_calls = len(executor.commands)
    [needs_human_run] = store.list_agent_runs_for_task_generation(
        needs_human_task.id, needs_human_task.execution_generation
    )
    [needs_human_attempt] = store.list_agent_runtime_attempts(needs_human_run.id)
    assert needs_human_run.status == "completed"
    needs_human_envelope = json.loads(needs_human_attempt.result_envelope_json)
    assert "result" not in needs_human_envelope
    assert needs_human_envelope["result_ref"]["agent_run_id"] == needs_human_run.id
    assert "A management decision is required." not in (
        needs_human_attempt.result_envelope_json
    )
    monkeypatch.setattr(store, "complete_agent_run", original_complete_agent_run)
    execute(needs_human_task)
    assert len(executor.commands) == needs_human_executor_calls
    recovered_needs_human = store.get_agent_run(needs_human_run.id)
    assert recovered_needs_human is not None
    assert recovered_needs_human.status == "completed"

    # A completed provider result belongs to the full execution contract. Once
    # the parent and result are atomically terminal, a context mismatch must
    # block rather than spawn a second model call.
    stale_task = next_task("msg-turns-stale", "generation-stale")
    monkeypatch.setattr(store, "complete_agent_run", crash_before_parent_terminal)
    with pytest.raises(RuntimeError, match="injected_parent_terminal_failure"):
        execute(stale_task, current_prompt="OLD business context")
    stale_executor_calls = len(executor.commands)
    monkeypatch.setattr(store, "complete_agent_run", original_complete_agent_run)
    with pytest.raises(
        ValueError, match="completed_runtime_result_contract_mismatch"
    ):
        execute(stale_task, current_prompt="NEW business context")
    assert len(executor.commands) == stale_executor_calls
    [stale_run] = store.list_agent_runs_for_task_generation(
        stale_task.id, stale_task.execution_generation
    )
    stale_attempts = store.list_agent_runtime_attempts(stale_run.id)
    assert len(stale_attempts) == 1
    assert stale_attempts[0].status == "completed"

    corrupt_task = next_task("msg-turns-corrupt", "generation-corrupt")
    monkeypatch.setattr(store, "complete_agent_run", crash_before_parent_terminal)
    with pytest.raises(RuntimeError, match="injected_parent_terminal_failure"):
        execute(corrupt_task)
    corrupt_executor_calls = len(executor.commands)
    [corrupt_run] = store.list_agent_runs_for_task_generation(
        corrupt_task.id, corrupt_task.execution_generation
    )
    [corrupt_attempt] = store.list_agent_runtime_attempts(corrupt_run.id)
    corrupt_envelope = json.loads(corrupt_attempt.result_envelope_json)
    corrupt_envelope["evidence"]["events_sha256"] = "0" * 64
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update agent_runtime_attempts set result_envelope_json=? where id=?",
            (json.dumps(corrupt_envelope), corrupt_attempt.id),
        )
        db.execute(
            """
            update agent_runs
            set status='running', final_result_json='', completed_at='',
                lease_owner='consumer', lease_expires_at='2099-01-01 00:00:00'
            where id=?
            """,
            (corrupt_run.id,),
        )
    monkeypatch.setattr(store, "complete_agent_run", original_complete_agent_run)
    with pytest.raises(ValueError, match="completed_runtime_result_invalid"):
        execute(corrupt_task)
    assert len(executor.commands) == corrupt_executor_calls
    blocked_corrupt_run = store.get_agent_run(corrupt_run.id)
    assert blocked_corrupt_run is not None
    assert blocked_corrupt_run.status == "unknown"
    assert blocked_corrupt_run.lease_owner == ""
    assert blocked_corrupt_run.reconciliation_suspended is True
    assert json.loads(blocked_corrupt_run.structured_error_json)["code"] == (
        "completed_runtime_result_invalid"
    )
    blocked_corrupt_task = store.get_reply_task(corrupt_task.id)
    assert blocked_corrupt_task is not None
    assert blocked_corrupt_task.status == "failed"
    assert blocked_corrupt_task.locked_at is None
    assert blocked_corrupt_task.recovery_code == "completed_runtime_result_invalid"
    assert store.claim_reply_task(blocked_corrupt_task.id) is None
    assert store.list_unknown_agent_runs(limit=10) == []
    assert store.list_suspended_unknown_agent_runs(limit=10) == []

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


def test_openai_failure_falls_back_to_claude_for_consumer(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api,claude_api",
            "CEO_CODEX_API_KEY": "test-openai-secret",
            "CEO_CLAUDE_API_KEY": "test-anthropic-secret",
            "CEO_CLAUDE_MODEL": "claude-sonnet-4-5",
        }
    )
    now = datetime.now(UTC)
    snapshots = {
        route.name: RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=frozenset(
                {
                    "structured_output",
                    "local_schema_validation",
                    "consumer_read_only_enforcement",
                }
            ),
            healthy=True,
            checked_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
        )
        for route in config.routes
    }
    manifests = {
        route.name: RuntimeRouteSurfaceManifest(
            route_name=route.name,
            capabilities=frozenset({"reviewed_read_tools"}),
        )
        for route in config.routes
    }
    router = AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots=snapshots,
        surface_manifests=manifests,
    )
    result_json = json.dumps(
        {
            "outcome": "no_action",
            "summary": "Claude completed the read-only turn.",
            "proposal": None,
            "decision_options": [],
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )
    claude_session = "claude-consumer-session"
    claude_stream = "\n".join(
        (
            json.dumps(
                {"type": "system", "subtype": "init", "session_id": claude_session}
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": claude_session,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": result_json}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "session_id": claude_session,
                    "result": result_json,
                }
            ),
        )
    )
    commands: list[list[str]] = []
    submitted_prompts: list[str] = []

    def executor(command, *, prompt, on_stdout_line, **kwargs):
        commands.append(command)
        submitted_prompts.append(prompt)
        if command[0] != "claude-test":
            return ProcessRunResult(
                1,
                "",
                "unexpected status 401 Unauthorized: missing bearer or basic "
                "authentication /v1/responses",
            )
        for line in claude_stream.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(0, claude_stream, "")

    claim = _claim_consumer(store, task)
    result = AgentTurnProcess(
        store=store,
        task=task,
        workspace=tmp_path,
        owner="consumer",
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test"),
        claude_adapter=ClaudeRuntimeAdapter(
            workspace=tmp_path,
            config=config,
            claude_bin="claude-test",
            service_mcp_servers=(
                ServiceMcpServer(name="agent_cli", command="/usr/bin/true"),
                ServiceMcpServer(
                    name="memory_connector", url="http://127.0.0.1:9/mcp"
                ),
            ),
        ),
    ).execute(
        run=claim.run,
        prompt="Read-only decision",
        session_id=None,
        developer_instructions="Return the exact schema.",
        configure_command=lambda command: None,
        parse_result=parse_consumer_agent_wire_result,
        persist_conversation_session=True,
        conversation_contract_hash="contract-v1",
    )

    assert result.run_id == claim.run.id
    attempts = store.list_agent_runtime_attempts(claim.run.id)
    assert [attempt.route_name for attempt in attempts] == [
        "codex_oauth",
        "codex_api",
        "claude_api",
    ]
    assert [command[0] for command in commands] == [
        "codex-test",
        "codex-test",
        "claude-test",
    ]
    assert submitted_prompts[:2] == ["Read-only decision", "Read-only decision"]
    assert submitted_prompts[2] == (
        "<developer-instructions>\n"
        "Return the exact schema.\n"
        "</developer-instructions>\n"
        "<task>\n"
        "Read-only decision\n"
        "</task>"
    )
    assert "Return the exact schema." not in commands[2]
    assert store.get_conversation_runtime_session(
        task.conversation_id,
        "claude_api",
        required_contract_hash="contract-v1",
    ) == claude_session


@pytest.mark.parametrize(
    "codex_failure",
    [
        "missing bearer or basic authentication for /v1/responses",
        "workspace is out of credits",
        "stream disconnected before completion",
    ],
)
def test_read_only_audit_reconcile_pre_session_failure_reaches_claude(
    tmp_path,
    codex_failure,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    owner = "audit-reconcile"
    run = _claim_audit(store, task)
    action = {
        "capability": "mcp:write.send",
        "reviewed_server": "write",
        "reviewed_tool": "send",
        "operation": "send",
        "operation_digest": "operation-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"uuid": "target-1"},
    }
    store.append_agent_run_event(run.id, _effect_event(**action), owner="audit")
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="audit",
    )
    claim = store.claim_unknown_agent_run(run.id, owner=owner)
    assert claim.claimed
    run = claim.run
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api,claude_api",
            "CEO_CODEX_API_KEY": "test-openai-secret",
            "CEO_CLAUDE_API_KEY": "test-anthropic-secret",
            "CEO_CLAUDE_MODEL": "claude-sonnet-4-5",
        }
    )
    now = datetime.now(UTC)
    snapshots = {
        route.name: RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=frozenset(
                {
                    "structured_output",
                    "local_schema_validation",
                    "consumer_read_only_enforcement",
                }
            ),
            healthy=True,
            checked_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
        )
        for route in config.routes
    }
    surfaces = {
        route.name: RuntimeRouteSurfaceManifest(
            route_name=route.name,
            capabilities=frozenset(
                {"reviewed_read_tools", "reconciliation_read_only"}
            ),
        )
        for route in config.routes
    }
    router = AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots=snapshots,
        surface_manifests=surfaces,
    )
    registry = McpToolEffectRegistry(
        {
            ("write", "send"): EffectKind.EFFECTFUL,
            ("memory_connector", "memory_get"): EffectKind.READ_ONLY,
        },
        readbacks={
            ("memory_connector", "memory_get"): {("write", "send")}
        },
        readback_target_modes={
            ("memory_connector", "memory_get", "write", "send"): "shared"
        },
        readback_operation_modes={
            ("memory_connector", "memory_get", "write", "send"): "registered"
        },
        readback_operation_relations={
            ("memory_connector", "memory_get", "write", "send"): {
                ("memory_get", "send")
            },
        },
    )
    read_result = json.dumps(
        {
            "content": [{"type": "text", "text": "synthetic readback"}],
            "isError": False,
        },
        separators=(",", ":"),
    )
    read_digest = hashlib.sha256(
        json.dumps(
            read_result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result_json = json.dumps(
        {
            "outcome": "reconciled",
            "summary": "Readback proves the original action is absent.",
            "proposal_revision": 0,
            "side_effect_state": "unknown",
            "feedback": None,
            "external_result": None,
            "reconciliation": [
                {
                    "action_index": 0,
                    "disposition": "absent",
                    "read_result_digest": read_digest,
                }
            ],
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )
    claude_session = "claude-reconcile-session"
    claude_stream = "\n".join(
        json.dumps(event, separators=(",", ":"))
        for event in (
            {"type": "system", "subtype": "init", "session_id": claude_session},
            {
                "type": "assistant",
                "session_id": claude_session,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "readback-1",
                            "name": "mcp__memory_connector__memory_get",
                            "input": {"uuid": "target-1"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "session_id": claude_session,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "readback-1",
                            "content": read_result,
                            "is_error": False,
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "session_id": claude_session,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": result_json}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": claude_session,
                "result": result_json,
            },
        )
    )
    commands = []

    def executor(command, *, on_stdout_line, **kwargs):
        commands.append(command)
        if command[0] != "claude-test":
            return ProcessRunResult(1, "", codex_failure)
        for line in claude_stream.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(0, claude_stream, "")

    result = AgentTurnProcess(
        store=store,
        task=task,
        workspace=tmp_path,
        owner=owner,
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test"),
        claude_adapter=ClaudeRuntimeAdapter(
            workspace=tmp_path,
            config=config,
            claude_bin="claude-test",
            effect_registry=registry,
            service_mcp_servers=(
                ServiceMcpServer(
                    name="memory_connector", url="http://127.0.0.1:9/mcp"
                ),
            ),
        ),
        mcp_effect_registry=registry,
    ).execute(
        run=run,
        prompt="Reconcile by readback only.",
        session_id=None,
        developer_instructions="Never execute the original action.",
        configure_command=lambda command: None,
        parse_result=parse_audit_agent_wire_result,
        persist_conversation_session=False,
        expected_effect_actions=(action,),
        recovery_phase="reconcile",
    )

    assert result.result.outcome is AuditOutcome.RECONCILED
    assert [command[0] for command in commands] == [
        "codex-test",
        "codex-test",
        "claude-test",
    ]
    attempts = store.list_agent_runtime_attempts(run.id)
    assert [attempt.route_name for attempt in attempts] == [
        "codex_oauth",
        "codex_api",
        "claude_api",
    ]
    assert all(not attempt.first_effect_started_at for attempt in attempts)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.effect_started_count == 1
    recovery_events = persisted.tool_events[len(run.tool_events) :]
    assert recovery_events
    assert all(
        event["item"]["metadata"]["effect"] == "read_only"
        for event in recovery_events
    )


def test_missing_claude_skill_is_not_a_route_preflight_requirement(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    claim = _claim_consumer(store, task)
    skill = "reviewed_skill:dingtalk-chat:expected-sha"

    required = _required_runtime_capabilities(
        run=claim.run,
        recovery_phase="",
        expected_effect_actions=(),
        explicit_capabilities=frozenset({skill}),
    )

    assert skill not in required


def test_effectful_audit_never_selects_claude_even_with_false_surface_claims(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api",
            "CEO_CLAUDE_API_KEY": "test-anthropic-secret",
            "CEO_CLAUDE_MODEL": "claude-sonnet-4-5",
        }
    )
    now = datetime.now(UTC)
    required = _required_runtime_capabilities(
        run=run,
        recovery_phase="",
        expected_effect_actions=(),
    )
    router = AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots={
            "claude_api": RuntimeCapabilitySnapshot(
                route_name="claude_api",
                capabilities=required,
                healthy=True,
                checked_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=5)).isoformat(),
            )
        },
        surface_manifests={
            "claude_api": RuntimeRouteSurfaceManifest(
                route_name="claude_api",
                capabilities=required,
            )
        },
    )
    executor_calls = 0

    def must_not_execute(*args, **kwargs):
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("effectful Audit must stop before Claude spawn")

    with pytest.raises(RuntimeError, match="runtime_route_unavailable"):
        AgentTurnProcess(
            store=store,
            task=task,
            workspace=tmp_path,
            owner="audit",
            executor=must_not_execute,
            runtime_config=config,
            runtime_router=router,
        ).execute(
            run=run,
            prompt="Execute an external action",
            session_id=None,
            developer_instructions="Return the exact schema.",
            configure_command=lambda command: None,
            parse_result=lambda raw: raw,
            persist_conversation_session=False,
            allow_effectful_tools=True,
        )

    assert executor_calls == 0
    assert store.list_agent_runtime_attempts(run.id) == []
    failed = store.get_agent_run(run.id)
    assert failed is not None and failed.status == "failed"
    assert json.loads(failed.structured_error_json)["code"] == (
        "runtime_route_unavailable"
    )


def test_claude_effect_fence_atomically_persists_one_dispatch_start(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    attempt = store.claim_agent_runtime_attempt(
        run.id,
        "claude_api",
        "claude_cli",
        "service_api",
        "claude-sonnet-test",
    )
    attempt = store.mark_agent_runtime_attempt_running_once(
        attempt.id,
        owner="audit",
        effectful=False,
    )
    action = {
        "capability": "agent_cli.dws",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat message send",
        "operation_digest": "operation-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"group": "cid-test"},
    }
    event = _effect_event(event_type="item.started", action_index=0, **action)
    event["item"]["id"] = "claude-call-1"

    first = store.authorize_claude_effect_dispatch(
        run_id=run.id,
        attempt_id=attempt.id,
        owner="audit",
        event=event,
        expected_action=action,
    )
    duplicate = store.authorize_claude_effect_dispatch(
        run_id=run.id,
        attempt_id=attempt.id,
        owner="audit",
        event=event,
        expected_action=action,
    )

    assert first.dispatch_acquired is True
    assert duplicate.dispatch_acquired is False
    persisted_attempt = store.get_agent_runtime_attempt(attempt.id)
    persisted_run = store.get_agent_run(run.id)
    assert persisted_attempt is not None and persisted_attempt.first_effect_started_at
    assert persisted_run is not None and persisted_run.effect_started_count == 1
    assert persisted_run.tool_events[-1] == event


@pytest.mark.parametrize(
    "unsafe_summary, expected",
    [
        ("Bearer sk-private-secret", "sensitive"),
        ("https://example.com/file?X-Amz-Signature=secret", "sensitive"),
        ("/private/var/tmp/claude-runtime.json", "local_path"),
        ("x" * (33 * 1024), "summary_invalid"),
    ],
)
def test_runtime_domain_result_codec_rejects_private_values(
    unsafe_summary, expected
):
    result = parse_consumer_agent_wire_result(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "outcome": "no_action",
                            "summary": unsafe_summary,
                            "proposal": None,
                            "decision_options": [],
                            "error_code": "",
                            "error_retryable": False,
                            "error_authorization_required": False,
                        }
                    ),
                },
            }
        )
    )

    with pytest.raises(ValueError, match=expected):
        _encode_runtime_domain_result(
            schema_id="schema-v1",
            role=AgentRole.CONSUMER,
            recovery_phase="",
            result=result,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": 2},
        {"version": True},
        {"unexpected": "field"},
        {"result": []},
    ],
)
def test_runtime_domain_result_codec_rejects_corrupt_shape(mutation):
    valid = {
        "schema_id": "schema-v1",
        "version": 1,
        "role": "consumer",
        "recovery_phase": "",
        "result": {
            "outcome": "no_action",
            "summary": "Nothing to do.",
            "proposal": None,
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        },
    }
    valid.update(mutation)

    with pytest.raises(ValueError, match="runtime_result_envelope_invalid"):
        _decode_runtime_domain_result(
            json.dumps(valid),
            schema_id="schema-v1",
            role=AgentRole.CONSUMER,
            recovery_phase="",
        )


def test_runtime_domain_result_codec_rejects_business_document_reference(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    attempt = store.claim_agent_runtime_attempt(
        run.id,
        "claude_api",
        "claude_cli",
        "service_api",
        "claude-sonnet-4-5",
    )
    store.mark_agent_runtime_attempt_running_once(attempt.id)
    document_marker = "full-business-document-must-not-persist"
    result = AuditAgentResult(
        outcome=AuditOutcome.EXECUTED,
        summary="Confirmed.",
        proposal_revision=0,
        side_effect_state="confirmed",
        feedback=None,
        external_result=AuditExternalResult(
            operation_id="operation-0",
            verification_summary="Confirmed from live state.",
            live_result_reference={
                "message_id": "mid-1",
                "document_content": {"confidential": document_marker},
            },
        ),
        reconciliation=(),
        error=AgentError(),
    )

    with pytest.raises(
        ValueError, match="runtime_result_envelope_external_reference_invalid"
    ):
        _encode_runtime_domain_result(
            schema_id="schema-v1",
            role=AgentRole.AUDIT,
            recovery_phase="execute",
            result=result,
        )
    assert document_marker.encode() not in store.path.read_bytes()
    persisted_attempt = store.get_agent_runtime_attempt(attempt.id)
    assert persisted_attempt is not None and persisted_attempt.status == "running"


def test_runtime_domain_result_codec_rejects_consumer_document_payload():
    result = parse_consumer_agent_wire_result(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "outcome": "proposal",
                            "summary": "Prepared a proposal.",
                            "proposal": {
                                "objective": "Send the reviewed update.",
                                "actions": [
                                    {
                                        "description": "Send update",
                                        "capability": "agent_cli.dws",
                                        "operation": "chat message send",
                                        "target": {"conversation_id": "cid-1"},
                                        "payload": {
                                            "document_content": (
                                                "full-business-document-must-not-persist"
                                            )
                                        },
                                        "expected_verification": "Read it back",
                                    }
                                ],
                                "sourced_facts": [],
                                "authored_judgment": "Ready.",
                            },
                            "decision_options": [],
                            "error_code": "",
                            "error_retryable": False,
                            "error_authorization_required": False,
                        }
                    ),
                },
            }
        )
    )

    with pytest.raises(ValueError, match="runtime_result_envelope_document_field"):
        _encode_runtime_domain_result(
            schema_id="schema-v1",
            role=AgentRole.CONSUMER,
            recovery_phase="",
            result=result,
        )


def test_runtime_attempt_completion_rejects_event_appended_after_evidence_snapshot(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run
    attempt = store.claim_agent_runtime_attempt(
        run.id,
        "claude_api",
        "claude_cli",
        "service_api",
        "claude-sonnet-4-5",
    )
    attempt = store.mark_agent_runtime_attempt_running_once(attempt.id)
    result = parse_consumer_agent_wire_result(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "outcome": "no_action",
                            "summary": "Nothing to do.",
                            "proposal": None,
                            "decision_options": [],
                            "error_code": "",
                            "error_retryable": False,
                            "error_authorization_required": False,
                        }
                    ),
                },
            }
        )
    )
    snapshot = store.get_agent_run(run.id)
    assert snapshot is not None
    schema_id = "schema-evidence-cas"
    envelope = _encode_runtime_domain_result(
        schema_id=schema_id,
        role=AgentRole.CONSUMER,
        recovery_phase="",
        result=result,
        evidence=_runtime_result_evidence(
            run=snapshot,
            event_start=0,
            receipts=[],
            recovery_started_actions=set(),
            completed_before_recovery=set(),
        ),
    )
    snapshot_ready = threading.Barrier(2)
    append_done = threading.Barrier(2)
    completion_errors: list[BaseException] = []

    def complete_from_stale_snapshot() -> None:
        snapshot_ready.wait()
        append_done.wait()
        try:
            store.complete_agent_runtime_attempt(
                attempt.id,
                "claude-session",
                "",
                0,
                3,
                result_schema_id=schema_id,
                result_envelope_json=envelope,
            )
        except Exception as exc:
            completion_errors.append(exc)

    thread = threading.Thread(target=complete_from_stale_snapshot)
    thread.start()
    snapshot_ready.wait()
    store.append_agent_run_event(
        run.id,
        {"type": "turn.started", "thread_id": "claude-session"},
        owner="consumer",
    )
    append_done.wait()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(completion_errors) == 1
    assert "evidence changed" in str(completion_errors[0])
    persisted_attempt = store.get_agent_runtime_attempt(attempt.id)
    assert persisted_attempt is not None
    assert persisted_attempt.status == "running"
    assert persisted_attempt.result_envelope_json == ""


@pytest.mark.parametrize("outcome", ("no_action", "needs_human"))
def test_consumer_terminal_result_slot_failure_rolls_back_and_store_retry_is_atomic(
    tmp_path,
    monkeypatch,
    outcome,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run
    attempt = store.claim_agent_runtime_attempt(
        run.id,
        "claude_api",
        "claude_cli",
        "service_api",
        "claude-sonnet-4-5",
    )
    attempt = store.mark_agent_runtime_attempt_running_once(attempt.id)
    summary = f"terminal summary must exist once for {outcome}"
    options = (
        [
            {
                "key": "A",
                "label": "Approve",
                "instruction": "Approve the reviewed option.",
                "consequence": "The reviewed plan may continue.",
            },
            {
                "key": "B",
                "label": "Hold",
                "instruction": "Hold the reviewed option.",
                "consequence": "No further action is taken.",
            },
        ]
        if outcome == "needs_human"
        else []
    )
    result = parse_consumer_agent_wire_result(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "outcome": outcome,
                            "summary": summary,
                            "proposal": None,
                            "decision_options": options,
                            "error_code": (
                                "decision_required" if outcome == "needs_human" else ""
                            ),
                            "error_retryable": False,
                            "error_authorization_required": False,
                        }
                    ),
                },
            }
        )
    )
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    schema_id = f"schema-{outcome}"
    envelope = _encode_runtime_domain_result(
        schema_id=schema_id,
        role=AgentRole.CONSUMER,
        recovery_phase="",
        result=result,
        evidence=_runtime_result_evidence(
            run=persisted,
            event_start=0,
            receipts=[],
            recovery_started_actions=set(),
            completed_before_recovery=set(),
        ),
        result_reference_run_id=run.id,
    )
    original_upsert = store._upsert_conversation_runtime_session_in_connection

    def fail_slot_write(*args, **kwargs):
        raise RuntimeError("injected_slot_write_failure")

    monkeypatch.setattr(
        store, "_upsert_conversation_runtime_session_in_connection", fail_slot_write
    )
    complete_kwargs = {
        "owner": "consumer",
        "result_schema_id": schema_id,
        "result_envelope_json": envelope,
        "conversation_id": task.conversation_id,
        "route_name": "claude_api",
        "conversation_contract_hash": "contract-v1",
        "agent_run_final_result": result.model_dump(mode="json"),
        "agent_run_final_side_effect_state": "none",
        "agent_run_transcript_end": 3,
    }
    with pytest.raises(RuntimeError, match="injected_slot_write_failure"):
        store.complete_agent_runtime_attempt(
            attempt.id,
            "claude-session",
            "",
            0,
            3,
            **complete_kwargs,
        )
    rolled_back_attempt = store.get_agent_runtime_attempt(attempt.id)
    rolled_back_run = store.get_agent_run(run.id)
    assert rolled_back_attempt is not None and rolled_back_attempt.status == "running"
    assert rolled_back_attempt.result_envelope_json == ""
    assert rolled_back_run is not None and rolled_back_run.status == "running"
    assert rolled_back_run.final_result_json == ""
    assert store.get_conversation_runtime_session(
        task.conversation_id,
        "claude_api",
        required_contract_hash="contract-v1",
    ) is None

    monkeypatch.setattr(
        store,
        "_upsert_conversation_runtime_session_in_connection",
        original_upsert,
    )
    store.complete_agent_runtime_attempt(
        attempt.id,
        "claude-session",
        "",
        0,
        3,
        **complete_kwargs,
    )
    completed_attempt = store.get_agent_runtime_attempt(attempt.id)
    completed_run = store.get_agent_run(run.id)
    assert completed_attempt is not None and completed_attempt.status == "completed"
    assert summary not in completed_attempt.result_envelope_json
    assert completed_run is not None and completed_run.status == "completed"
    assert summary in completed_run.final_result_json
    assert store.get_conversation_runtime_session(
        task.conversation_id,
        "claude_api",
        required_contract_hash="contract-v1",
    ) == "claude-session"


@pytest.mark.parametrize(
    "evidence_mutation",
    (
        {"event_end": -1},
        {"events_sha256": "short"},
        {"recovery_started_actions": [True]},
        {"unexpected": "field"},
    ),
)
def test_runtime_domain_result_codec_rejects_corrupt_evidence(evidence_mutation):
    result = parse_consumer_agent_wire_result(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "outcome": "no_action",
                            "summary": "Nothing to do.",
                            "proposal": None,
                            "decision_options": [],
                            "error_code": "",
                            "error_retryable": False,
                            "error_authorization_required": False,
                        }
                    ),
                },
            }
        )
    )
    envelope = json.loads(
        _encode_runtime_domain_result(
            schema_id="schema-v1",
            role=AgentRole.CONSUMER,
            recovery_phase="",
            result=result,
        )
    )
    envelope["evidence"].update(evidence_mutation)

    with pytest.raises(ValueError, match="runtime_result_envelope_invalid"):
        _decode_runtime_domain_result(
            json.dumps(envelope),
            schema_id="schema-v1",
            role=AgentRole.CONSUMER,
            recovery_phase="",
        )


def _runtime_result_schema_for_test(
    run,
    *,
    prompt,
    developer_instructions,
    recovery_phase,
    expected_effect_actions,
    recovery_authorizations=None,
):
    recovery_authorizations = recovery_authorizations or {}
    required_capabilities = _required_runtime_capabilities(
        run=run,
        recovery_phase=recovery_phase,
        expected_effect_actions=expected_effect_actions,
    )
    contract = {
        "version": 1,
        "role": run.role.value,
        "recovery_phase": recovery_phase,
        "operation_id": run.operation_id,
        "conversation_contract_hash": "",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "developer_instructions_sha256": hashlib.sha256(
            developer_instructions.encode()
        ).hexdigest(),
        "required_capabilities": sorted(required_capabilities),
        "expected_actions_sha256": hashlib.sha256(
            json.dumps(
                expected_effect_actions,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "reviewed_skills": [],
        "recovery_authorizations_sha256": hashlib.sha256(
            json.dumps(
                sorted(recovery_authorizations.items()),
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }
    contract_digest = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return hashlib.sha256(
        f"agent_turn_claude_result_v1\0{contract_digest}".encode()
    ).hexdigest()


def _unknown_audit_recovery_fixture(store, task, *, owner):
    run = _claim_audit(store, task)
    action = {
        "capability": "mcp:write.send",
        "reviewed_server": "write",
        "reviewed_tool": "send",
        "operation": "send",
        "operation_digest": "operation-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"id": "target-1"},
    }
    store.append_agent_run_event(
        run.id,
        _effect_event(**action),
        owner="audit",
    )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="audit",
    )
    claim = store.claim_unknown_agent_run(run.id, owner=owner)
    assert claim.claimed
    return claim.run, action


@pytest.mark.parametrize(
    ("recovery_phase", "rotate_authorization"),
    (("reconcile", False), ("execute", False), ("execute", True)),
)
def test_completed_claude_audit_recovery_rebuilds_persisted_evidence_without_spawn(
    tmp_path,
    recovery_phase,
    rotate_authorization,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    owner = f"audit-{recovery_phase}"
    run, action = _unknown_audit_recovery_fixture(store, task, owner=owner)
    actions = (action,)
    registry = McpToolEffectRegistry(
        {
            ("write", "send"): EffectKind.EFFECTFUL,
            ("read", "get"): EffectKind.READ_ONLY,
        },
        readbacks={("read", "get"): {("write", "send")}},
    )
    event_start = len(run.tool_events)
    recovery_started_actions: set[int] = set()
    completed_before_recovery: set[int] = set()
    persisted_authorizations = (
        {"authorization-old": 0} if recovery_phase == "execute" else {}
    )
    if recovery_phase == "reconcile":
        store.append_unknown_agent_run_event(
            run.id,
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "id": "read-1",
                    "status": "completed",
                    "metadata": {
                        "effect": "read_only",
                        "operation_id": run.operation_id,
                        "reviewed_server": "read",
                        "reviewed_tool": "get",
                        "operation": "get",
                        "target_identifiers": {"id": "target-1"},
                        "result_digest": "read-result-digest",
                    },
                },
            },
            owner=owner,
        )
        result = AuditAgentResult(
            outcome=AuditOutcome.RECONCILED,
            summary="Live readback reconciled the action.",
            proposal_revision=0,
            side_effect_state="unknown",
            feedback=None,
            external_result=None,
            reconciliation=(
                {
                    "action_index": 0,
                    "disposition": "present",
                    "read_result_digest": "read-result-digest",
                },
            ),
            error=AgentError(),
        )
    else:
        recovery_started_actions = {0}
        for event_type in ("item.started", "item.completed"):
            event = _effect_event(
                event_type=event_type, action_index=0, **action
            )
            event["item"]["id"] = "write-recovery"
            store.append_unknown_agent_run_event(
                run.id,
                event,
                owner=owner,
            )
        store.append_unknown_agent_run_event(
            run.id,
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "id": "read-after-write",
                    "status": "completed",
                    "metadata": {
                        "effect": "read_only",
                        "operation_id": run.operation_id,
                        "reviewed_server": "read",
                        "reviewed_tool": "get",
                        "operation": "get",
                        "target_identifiers": {"id": "target-1"},
                        "result_digest": "execute-read-result-digest",
                    },
                },
            },
            owner=owner,
        )
        result = AuditAgentResult(
            outcome=AuditOutcome.EXECUTED,
            summary="The authorized recovery action completed.",
            proposal_revision=0,
            side_effect_state="confirmed",
            feedback=None,
            external_result=AuditExternalResult(
                operation_id=run.operation_id,
                verification_summary="Persisted tool evidence confirms completion.",
                live_result_reference={"id": "receipt-1"},
            ),
            reconciliation=(),
            error=AgentError(),
        )

    prompt = f"Recover {recovery_phase}"
    developer = "Use only persisted reviewed evidence."
    schema_id = _runtime_result_schema_for_test(
        run,
        prompt=prompt,
        developer_instructions=developer,
        recovery_phase=recovery_phase,
        expected_effect_actions=actions,
        recovery_authorizations=persisted_authorizations,
    )
    validator = AgentTurnProcess(
        store=store,
        task=task,
        workspace=tmp_path,
        owner=owner,
        mcp_effect_registry=registry,
    )
    persisted_before_completion = store.get_agent_run(run.id)
    assert persisted_before_completion is not None
    if recovery_phase == "reconcile":
        validated = validator._validate_audit_reconciliation_result(
            run,
            result,
            persisted_before_completion,
            expected_effect_actions=actions,
            recovery_event_start=event_start,
            completed_before_recovery=completed_before_recovery,
        )
        result = result.model_copy(
            update={
                "reconciliation": tuple(
                    validated[index] for index in sorted(validated)
                )
            }
        )
    else:
        validator._validate_audit_recovery_execution_result(
            run,
            result,
            persisted_before_completion,
            expected_effect_actions=actions,
            recovery_started_actions=recovery_started_actions,
            authorized_recovery_actions=frozenset({0}),
        )
    attempt = store.claim_unknown_recovery_agent_runtime_attempt(
        run.id,
        "claude_api",
        "claude_cli",
        "service_api",
        "claude-sonnet-4-5",
        owner=owner,
    ).attempt
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    envelope = _encode_runtime_domain_result(
        schema_id=schema_id,
        role=AgentRole.AUDIT,
        recovery_phase=recovery_phase,
        result=result,
        evidence=_runtime_result_evidence(
            run=persisted,
            event_start=event_start,
            receipts=store.list_agent_execution_receipts(run.id),
            recovery_started_actions=recovery_started_actions,
            completed_before_recovery=completed_before_recovery,
            recovery_authorizations=persisted_authorizations,
        ),
    )
    store.complete_agent_runtime_attempt(
        attempt.id,
        "claude-recovery-session",
        "",
        0,
        3,
        result_schema_id=schema_id,
        result_envelope_json=envelope,
    )

    executor_calls = 0

    def must_not_execute(*args, **kwargs):
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("completed recovery must not spawn")

    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api",
            "CEO_CLAUDE_API_KEY": "test-claude-secret",
            "CEO_CLAUDE_MODEL": "claude-sonnet-4-5",
        }
    )
    process = AgentTurnProcess(
        store=store,
        task=task,
        workspace=tmp_path,
        owner=owner,
        executor=must_not_execute,
        runtime_config=config,
        runtime_router=object(),
        mcp_effect_registry=registry,
    )
    execute_kwargs = {
        "run": store.get_agent_run(run.id),
        "prompt": prompt,
        "session_id": None,
        "developer_instructions": developer,
        "configure_command": lambda command: None,
        "parse_result": lambda raw: (_ for _ in ()).throw(
            AssertionError("completed recovery must not parse provider output")
        ),
        "persist_conversation_session": False,
        "expected_effect_actions": actions,
        "recovery_phase": recovery_phase,
        "authorized_recovery_actions": (
            frozenset({0}) if recovery_phase == "execute" else frozenset()
        ),
        "recovery_authorizations": (
            {"authorization-new": 0}
            if rotate_authorization
            else persisted_authorizations
        ),
    }
    if rotate_authorization:
        with pytest.raises(
            ValueError, match="completed_runtime_result_contract_mismatch"
        ):
            process.execute(**execute_kwargs)
        assert executor_calls == 0
        blocked = store.get_agent_run(run.id)
        assert blocked is not None and blocked.status == "unknown"
        assert blocked.lease_owner == ""
        return
    recovered = process.execute(**execute_kwargs)

    assert executor_calls == 0
    assert recovered.result.outcome is result.outcome
    final_run = store.get_agent_run(run.id)
    assert final_run is not None
    assert final_run.status == (
        "completed" if recovery_phase == "execute" else "unknown"
    )
    assert final_run.lease_owner == ""
    final_task = store.get_reply_task(task.id)
    assert final_task is not None and final_task.status == "processing"


def test_completed_claude_result_contract_mismatch_with_effect_never_spawns(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    store.append_agent_run_event(
        run.id,
        _effect_event(
            capability="agent_cli.dws",
            operation="chat message send",
            operation_digest="command-digest",
            arguments_digest="arguments-digest",
            target_identifiers={"group": "cid"},
        ),
        owner="audit",
    )
    attempt = store.claim_agent_runtime_attempt(
        run.id,
        "claude_api",
        "claude_cli",
        "service_api",
        "claude-sonnet-4-5",
    )
    attempt = store.mark_agent_runtime_attempt_running_once(attempt.id)
    old_schema = "old-execution-contract"
    store.complete_agent_runtime_attempt(
        attempt.id,
        "claude-session",
        "",
        0,
        3,
        result_schema_id=old_schema,
        result_envelope_json=json.dumps(
            {"schema_id": old_schema, "version": 1, "result": {}}
        ),
    )

    class MustNotRoute:
        def first_route_decision(self, **kwargs):
            raise AssertionError("stale effectful result must fail before routing")

    executor_calls = 0

    def must_not_execute(*args, **kwargs):
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("stale effectful result must not spawn")

    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api",
            "CEO_CLAUDE_API_KEY": "test-claude-secret",
            "CEO_CLAUDE_MODEL": "claude-sonnet-4-5",
        }
    )
    with pytest.raises(ValueError, match="completed_runtime_result_contract_mismatch"):
        AgentTurnProcess(
            store=store,
            task=task,
            workspace=tmp_path,
            owner="audit",
            executor=must_not_execute,
            runtime_config=config,
            runtime_router=MustNotRoute(),
        ).execute(
            run=store.get_agent_run(run.id),
            prompt="NEW business context",
            session_id=None,
            developer_instructions="NEW reviewed rules",
            configure_command=lambda command: None,
            parse_result=lambda raw: (_ for _ in ()).throw(
                AssertionError("stale result must not be parsed")
            ),
            persist_conversation_session=False,
            expected_effect_actions=(
                {
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "operation_digest": "command-digest",
                    "arguments_digest": "arguments-digest",
                    "target_identifiers": {"group": "cid"},
                },
            ),
        )

    assert executor_calls == 0
    blocked_run = store.get_agent_run(run.id)
    assert blocked_run is not None
    assert blocked_run.status == "unknown"
    assert blocked_run.lease_owner == ""
    assert json.loads(blocked_run.structured_error_json)["code"] == (
        "completed_runtime_result_contract_mismatch"
    )
    blocked_task = store.get_reply_task(task.id)
    assert blocked_task is not None
    assert blocked_task.status == "processing"


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


def test_role_runtime_capabilities_use_execution_surfaces_only(tmp_path):
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
            "reviewed_read_tools",
        }
    )
    assert "dingtalk_chat" not in _required_runtime_capabilities(
        run=audit,
        recovery_phase="execute",
        expected_effect_actions=({"capability": "dingtalk_chat"},),
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


def test_consumer_turn_can_complete_typed_result_without_side_effect_state(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run

    completed = store.complete_agent_run(
        run.id,
        {
            "outcome": "no_action",
            "summary": "No external action is required.",
            "proposal": None,
            "decision_options": [],
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        owner="consumer",
    )

    assert completed.status == "completed"
    assert not hasattr(completed, "side_effect_state")


def test_consumer_turn_persists_provider_events_opaquely(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run

    persisted = store.append_agent_run_event(
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

    refreshed = store.get_agent_run(run.id)
    assert refreshed is not None
    assert refreshed.tool_events[-1]["item"]["tool"] == "write"
    assert not hasattr(refreshed, "side_effect_state")


def test_unknown_reconciliation_event_limit_defers_the_next_read_only_window(tmp_path):
    """Legacy unknown outcome is now represented as an ordinary failed run."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    failed = store.fail_agent_run(
        run.id, {"code": "codex_process_failed", "retryable": True}, owner="audit"
    )
    assert failed.status == "failed"
    assert json.loads(failed.structured_error_json)["code"] == "codex_process_failed"


def test_unknown_recovery_can_start_after_runtime_effect_boundary_without_tool_event(tmp_path):
    """A runtime interruption follows the normal failed/retry path."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    failed = store.fail_agent_run(
        run.id, {"code": "runtime_failed", "retryable": True}, owner="audit"
    )
    assert failed.status == "failed"
    assert failed.structured_error_json


def test_event_limited_unknown_run_remains_due_for_read_only_recovery(tmp_path):
    """Retry scheduling is driven by failed status, not reconciliation counters."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    failed = store.fail_agent_run(
        run.id, {"code": "codex_process_failed", "retryable": True}, owner="audit"
    )
    assert failed.status == "failed"
    assert store.get_agent_run(run.id).status == "failed"


def test_attempt_limited_unknown_run_can_start_the_next_read_only_window(tmp_path):
    """Retry attempts remain ordinary failed run attempts."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    failed = store.fail_agent_run(
        run.id, {"code": "audit_read_failed", "retryable": True}, owner="audit"
    )
    assert failed.status == "failed"
    assert json.loads(failed.structured_error_json)["retryable"] is True


def test_suspended_unknown_run_is_reopened_for_read_only_reconciliation(tmp_path):
    """Historical suspended rows are not reopened by the current application path."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    failed = store.fail_agent_run(
        run.id, {"code": "recovery_retired", "retryable": False}, owner="audit"
    )
    assert failed.status == "failed"
    assert json.loads(failed.structured_error_json)["code"] == "recovery_retired"


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


def test_completed_dingtalk_message_read_persists_content_proof_without_plaintext(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    argv = [
        "dws",
        "chat",
        "+chat-messages",
        "--group",
        "cid-turns",
        "--start",
        "2026-08-06T09:55:00+08:00",
        "--end",
        "2026-08-06T10:05:00+08:00",
        "--page-all",
        "--format",
        "json",
    ]
    descriptor = describe_native_command(
        {"type": "command_execution", "argv": argv}
    )
    assert descriptor is not None
    stdout = json.dumps(
        {
            "complete": True,
            "hasMore": False,
            "paginationKnown": True,
            "failures": [],
            "queryRange": {
                "startTime": "2026-08-06T01:55:00Z",
                "endTime": "2026-08-06T02:05:00Z",
            },
            "messages": [
                {
                    "conversationId": "cid-turns",
                    "messageId": "message-1",
                    "text": "exact reviewed reply",
                }
            ],
        }
    )
    receipt = {
        "cli": "dws",
        "operation": descriptor.command_path,
        "operation_digest": descriptor.command_digest,
        "target_identifiers": descriptor.target_identifiers,
        "result_digest": hashlib.sha256(stdout.encode()).hexdigest(),
        "stdout": stdout,
    }
    payload = {
        "type": "item.completed",
        "item": {
            "id": "read-1",
            "type": "mcp_tool_call",
            "server": "agent_cli",
            "tool": "execute_reviewed_read",
            "arguments": {"argv": argv},
            "status": "completed",
            "result": {"structuredContent": receipt, "isError": False},
        },
    }

    event = AgentTurnProcess(
        store=store,
        task=task,
        workspace=tmp_path,
        owner="audit",
    )._normalized_effect_event(
        payload,
        read_only=True,
        operation_id="",
        expected_message_text_digests=frozenset(
            {hashlib.sha256(b"exact reviewed reply").hexdigest()}
        ),
        message_operation_started_at="2026-08-06 02:00:00",
    )

    assert event is not None
    metadata = event["item"]["metadata"]
    assert metadata["message_readback_complete"] is True
    assert metadata["message_readback_window_matches"] is True
    assert metadata["message_text_digests"] == [
        hashlib.sha256(b"exact reviewed reply").hexdigest()
    ]
    assert "exact reviewed reply" not in json.dumps(event)
    assert "stdout" not in json.dumps(event)


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
    persisted_start = store.get_agent_run(run.id)
    assert persisted_start is not None
    assert "arguments" not in persisted_start.tool_events[0]["item"]
    assert "result" not in persisted_start.tool_events[0]["item"]

    completed = {**started, "type": "item.completed"}
    after_completed = store.append_agent_run_event(run.id, completed, owner="audit")


def test_provider_event_identity_is_opaque_to_application(tmp_path):
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

    persisted = store.append_agent_run_event(run.id, mismatched, owner="audit")
    assert persisted.effect_started_count == 1
    assert persisted.effect_completed_count == 1



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

    assert closed.effect_started_count == 1
    assert closed.effect_failed_count == 1
    assert not hasattr(closed, "side_effect_state")


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

    assert persisted.status == "running"
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
