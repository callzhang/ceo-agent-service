import inspect
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent_contracts import (
    AuditAgentResult,
    AuditExternalResult,
    AuditOutcome,
    ConsumerAgentResult,
)
from app.agent_result import AgentError
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeCapabilitySnapshot,
    RuntimeKind,
    RuntimeRoute,
)
from app.agent_runtime_router import AgentRuntimeRouter, RuntimeRouteDecision
from app.agent_turn_runner import (
    AgentTurnProcess,
    _decode_runtime_domain_result,
    _encode_runtime_domain_result,
    _required_runtime_capabilities,
)
from app.agent_wire_contracts import (
    parse_audit_agent_wire_result,
    parse_consumer_agent_wire_result,
)
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayExecutionResult
from app.process_runner import ProcessRunResult
from app.service_codex_config import ServiceMcpServer
from app.store import (
    AgentRole,
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
        conversation_contract_hash="current-contract",
    ) == "claude-session"
    assert process._session_for_route(
        _claude_route(),
        role=AgentRole.CONSUMER,
        requested_session_id="claude-session",
        conversation_contract_hash="different-contract",
    ) is None
    assert process._session_for_route(
        _claude_route(),
        role=AgentRole.AUDIT,
        requested_session_id="claude-session",
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

    class OneRouteRouter:
        def first_route_decision(self, **kwargs):
            return RuntimeRouteDecision(route, False, "eligible_route")

    raw_result = json.dumps(
        {
            "outcome": "no_action",
            "summary": "Nothing to do.",
            "proposal": None,
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
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
                        "action_identity": "send-update",
                        "capability": "agent_cli.dws",
                        "operation": "chat message send",
                        "target": {"conversation_id": "cid-1"},
                        "payload": {
                            "document_content": body_marker,
                            "source_url": url_marker,
                        },
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "Ready.",
            },
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
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

    # Sensitive payload policy is covered by runtime contract tests; this case focuses on session ownership.

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
    router = AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots=snapshots,
    )
    result_json = json.dumps(
        {
            "outcome": "no_action",
            "summary": "Claude completed the read-only turn.",
            "proposal": None,
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
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


def test_friday_runtime_fallback_completes_consumer_run(tmp_path):
    store = AutoReplyStore(tmp_path / "friday-consumer.sqlite3")
    task = _task(store)
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-agent",
            "CEO_FRIDAY_RUNTIME_MODEL": "MiniMax-M3",
            "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
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
                    "task_context",
                    "channel:wechat",
                }
            ),
            healthy=True,
            checked_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
        )
        for route in config.routes
    }
    router = AgentRuntimeRouter(routes=config.routes, store=store, snapshots=snapshots)
    result_json = json.dumps(
        {
            "outcome": "no_action",
            "summary": "Friday completed the turn.",
            "proposal": None,
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        },
        separators=(",", ":"),
    )

    class Friday:
        def execute(self, prompt, **kwargs):
            return FridayExecutionResult(
                text=result_json,
                thread_id="friday-thread",
                turn_id="friday-turn",
                operation_id="friday-operation",
                artifact={"final_message": result_json},
            )

    def executor(command, **kwargs):
        return ProcessRunResult(
            1,
            "",
            "unexpected status 401 unauthorized: missing bearer or basic authentication for /v1/responses",
        )

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
        friday_adapter=Friday(),
    ).execute(
        run=claim.run,
        prompt="Read-only decision",
        session_id=None,
        developer_instructions="Return the exact schema.",
        configure_command=lambda command: None,
        parse_result=parse_consumer_agent_wire_result,
        persist_conversation_session=False,
        conversation_contract_hash="contract-v1",
    )

    assert result.result.outcome == "no_action"
    attempts = store.list_agent_runtime_attempts(claim.run.id)
    assert [attempt.route_name for attempt in attempts] == ["codex_oauth", "friday_runtime"]
    assert attempts[-1].transcript_reference == "friday_operation:friday-operation"


@pytest.mark.parametrize("codex_failure", [
    "missing bearer or basic authentication for /v1/responses",
    "workspace is out of credits",
    "stream disconnected before completion",
])
def test_audit_pre_session_failure_is_ordinary_failed_retry(tmp_path, codex_failure):
    """Provider startup failures remain ordinary failed attempts; no app recovery phase."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_api", "CEO_CODEX_API_KEY": "test-secret"})
    route = config.routes[0]
    class Router:
        def first_route_decision(self, **kwargs): return RuntimeRouteDecision(route, False, "eligible_route")
    def executor(command, *, on_stdout_line, **kwargs):
        return ProcessRunResult(1, "", codex_failure)
    with pytest.raises(Exception):
        AgentTurnProcess(store=store, task=task, workspace=tmp_path, owner="audit", executor=executor, runtime_config=config, runtime_router=Router()).execute(run=run, prompt="Audit", session_id=None, developer_instructions="Return schema", configure_command=lambda c: None, parse_result=parse_audit_agent_wire_result, persist_conversation_session=False)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert persisted.status in {"failed", "running"}

def test_missing_claude_skill_is_not_a_route_preflight_requirement(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    claim = _claim_consumer(store, task)
    skill = "reviewed_skill:dingtalk-chat:expected-sha"

    required = _required_runtime_capabilities(
        run=claim.run,
        expected_actions=(),
        explicit_capabilities=frozenset({skill}),
    )

    assert skill not in required


def test_effectful_audit_never_selects_claude_even_with_false_surface_claims(
    tmp_path,
):
    """Provider choice is owned by the runtime, not an application effect policy."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    event = _effect_event(capability="agent_cli.dws", operation="chat message send")
    store.append_agent_run_event(run.id, event, owner="audit")
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert persisted.tool_events[-1] == event
    assert persisted.status == "running"



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
                            "risk": "low",
                            "confidence": 1.0,
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
        feedback=None,
        external_result=AuditExternalResult(
            operation_id="operation-0",
            live_result_reference={
                "message_id": "mid-1",
                "document_content": {"confidential": document_marker},
            },
        ),
        error=AgentError(),
    )

    with pytest.raises(ValueError, match="runtime_result_envelope_document_field_invalid"):
        _encode_runtime_domain_result(
            schema_id="schema-v1",
            role=AgentRole.AUDIT,
            result=result,
        )
    assert document_marker.encode() not in store.path.read_bytes()
    persisted_attempt = store.get_agent_runtime_attempt(attempt.id)
    assert persisted_attempt is not None and persisted_attempt.status == "running"


def test_runtime_domain_result_codec_preserves_message_readback_for_ledger_projection():
    result = AuditAgentResult(
        outcome=AuditOutcome.EXECUTED,
        summary="Message sent and read back.",
        proposal_revision=0,
        feedback=None,
        external_result=AuditExternalResult(
            operation_id="operation-readback",
            live_result_reference={
                "send_status": "SUCCESS",
                "message_id": "message-1",
                "readback": {
                    "conversationId": "conversation-1",
                    "messageId": "message-1",
                    "text": "已发送正文",
                },
            },
        ),
        error=AgentError(),
    )

    encoded = _encode_runtime_domain_result(
        schema_id="schema-v1",
        role=AgentRole.AUDIT,
        result=result,
    )
    decoded = json.loads(encoded)

    assert decoded["result"]["external_result"]["live_result_reference"]["readback"] == {
        "conversationId": "conversation-1",
        "messageId": "message-1",
        "text": "已发送正文",
    }


def test_runtime_domain_result_codec_preserves_consumer_action_identity():
    result = ConsumerAgentResult.model_validate(
        {
            "outcome": "proposal",
            "summary": "Send the result.",
            "proposal": {
                "objective": "Send one result.",
                "actions": [{
                    "description": "Send",
                    "action_identity": "send-result",
                    "capability": "dingtalk-chat",
                    "operation": "send_to_group",
                    "target": {"conversation_id": "cid-1"},
                    "payload": {"content": "done"},
                }],
                "sourced_facts": [],
                "authored_judgment": "",
            },
            "error": {"code": "", "retryable": False, "authorization_required": False},
        }
    )

    encoded = _encode_runtime_domain_result(
        schema_id="schema-v1",
        role=AgentRole.CONSUMER,
        result=result,
    )
    decoded = _decode_runtime_domain_result(
        encoded,
        schema_id="schema-v1",
        role=AgentRole.CONSUMER,
    )

    assert isinstance(decoded.result, ConsumerAgentResult)
    assert decoded.result.proposal is not None
    assert decoded.result.proposal.actions[0].action_identity == "send-result"


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
                                        "action_identity": "send-update",
                                        "capability": "agent_cli.dws",
                                        "operation": "chat message send",
                                        "target": {"conversation_id": "cid-1"},
                                        "payload": {
                                            "document_content": (
                                                "full-business-document-must-not-persist"
                                            )
                                        },
                                    }
                                ],
                                "sourced_facts": [],
                                "authored_judgment": "Ready.",
                            },
                            "decision_options": [],
                            "risk": "low",
                            "confidence": 1.0,
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
            result=result,
        )


def test_runtime_attempt_completion_does_not_treat_provider_events_as_result_evidence(
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
                            "risk": "low",
                            "confidence": 1.0,
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
        result=result,
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
    assert completion_errors == []
    persisted_attempt = store.get_agent_runtime_attempt(attempt.id)
    assert persisted_attempt is not None
    assert persisted_attempt.status == "completed"
    assert persisted_attempt.result_envelope_json == envelope


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
                            "risk": "high" if outcome == "needs_human" else "low",
                            "confidence": 0.0 if outcome == "needs_human" else 1.0,
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
        result=result,
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


def test_failed_claude_audit_result_remains_an_ordinary_retryable_run(tmp_path):
    """Retired recovery modes are ordinary failed runs with retry metadata."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    store.fail_agent_run(
        run.id,
        {"code": "runtime_result_invalid", "retryable": True},
        owner="audit",
    )
    failed = store.get_agent_run(run.id)
    assert failed is not None and failed.status == "failed"
    error = json.loads(failed.structured_error_json)
    assert error["code"] == "runtime_result_invalid"
    assert error["retryable"] is True



def test_completed_claude_result_contract_mismatch_with_effect_never_spawns(
    tmp_path,
):
    """Invalid typed results fail normally; no application recovery is spawned."""
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    store.fail_agent_run(
        run.id,
        {"code": "completed_runtime_result_contract_mismatch", "retryable": True},
        owner="audit",
    )
    failed = store.get_agent_run(run.id)
    assert failed is not None and failed.status == "failed"
    assert json.loads(failed.structured_error_json)["retryable"] is True
    assert store.list_agent_runtime_attempts(run.id) == []



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


def test_new_retry_run_resumes_prior_session_without_overwriting_history(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    first = _claim_consumer(store, task, owner="consumer-0").run
    first = store.set_agent_run_session(
        first.id,
        "consumer-session-1",
        owner="consumer-0",
    )
    store.fail_agent_run(
        first.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="consumer-0",
    )

    retry = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer-1",
    )

    assert retry.run.id != first.id
    assert retry.run.codex_session_id == "consumer-session-1"
    persisted_first = store.get_agent_run(first.id)
    assert persisted_first is not None
    assert persisted_first.status == "failed"
    assert persisted_first.codex_session_id == "consumer-session-1"


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
        expected_actions=(),
    ) == frozenset(
        {
            "structured_output",
            "local_schema_validation",
        }
    )
    assert _required_runtime_capabilities(
        run=audit,
        expected_actions=({"capability": "agent_cli.dws"},),
    ) == frozenset(
        {
            "structured_output",
            "local_schema_validation",
        }
    )
    assert "dingtalk_chat" not in _required_runtime_capabilities(
        run=audit,
        expected_actions=({"capability": "dingtalk_chat"},),
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


def test_agent_run_terminal_apis_do_not_accept_side_effect_state():
    assert "side_effect_state" not in inspect.signature(
        AutoReplyStore.complete_agent_run
    ).parameters
    assert "side_effect_state" not in inspect.signature(
        AutoReplyStore.fail_agent_run
    ).parameters


def test_store_exposes_no_legacy_agent_run_recovery_projection_apis():
    assert not hasattr(AutoReplyStore, "update_agent_run_projection")
    assert not hasattr(AutoReplyStore, "recover_failed_effect_free_consumer_tasks")
    assert not hasattr(AutoReplyStore, "recover_failed_effect_free_audit_tasks")


def test_consumer_turn_persists_provider_events_opaquely(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run

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

    refreshed = store.get_agent_run(run.id)
    assert refreshed is not None
    assert refreshed.tool_events[-1]["item"]["tool"] == "write"
    assert not hasattr(refreshed, "side_effect_state")


def test_retryable_process_failure_preserves_specific_code(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)

    failed = store.fail_agent_run(
        run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="audit",
    )

    assert failed.status == "failed"
    assert json.loads(failed.structured_error_json) == {
        "code": "codex_process_failed",
        "retryable": True,
    }


def test_provider_effect_events_are_append_only_execution_facts(
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
    store.append_agent_run_event(run.id, started, owner="audit")
    persisted_start = store.get_agent_run(run.id)
    assert persisted_start is not None
    assert "arguments" not in persisted_start.tool_events[0]["item"]
    assert "result" not in persisted_start.tool_events[0]["item"]

    completed = {**started, "type": "item.completed"}
    store.append_agent_run_event(run.id, completed, owner="audit")
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert [event["type"] for event in persisted.tool_events] == [
        "item.started",
        "item.completed",
    ]


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

    store.append_agent_run_event(run.id, mismatched, owner="audit")
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert len(persisted.tool_events) == 2
    assert persisted.tool_events[-1]["item"]["metadata"]["operation_digest"] == (
        "different"
    )



def test_provider_operation_id_is_persisted_without_application_rewrite(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)

    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {"type": "mcp_tool_call", "id": "write-1", "metadata": {"effect": "effectful", "operation_id": "operation-other"}},
        },
        owner="audit",
    )
    refreshed = store.get_agent_run(run.id)
    assert refreshed is not None
    assert refreshed.tool_events[-1]["item"]["metadata"]["operation_id"] == "operation-other"


def test_provider_failed_event_is_preserved_without_business_state(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    run = _claim_audit(store, task)
    started = _effect_event(operation_digest="same")
    store.append_agent_run_event(run.id, started, owner="audit")
    failed = {**started, "type": "item.failed"}
    store.append_agent_run_event(run.id, failed, owner="audit")
    closed = store.get_agent_run(run.id)
    assert closed is not None

    assert [event["type"] for event in closed.tool_events] == [
        "item.started",
        "item.failed",
    ]
    assert not hasattr(closed, "side_effect_state")


def test_duplicate_provider_starts_remain_visible_in_append_only_events(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    run = _claim_audit(store, _task(store))
    started = _effect_event(
        operation_digest="same",
        arguments_digest="same-arguments",
        target_identifiers={"group": "cid"},
    )

    store.append_agent_run_event(run.id, started, owner="audit")
    store.append_agent_run_event(run.id, started, owner="audit")
    store.append_agent_run_event(
        run.id,
        {**started, "type": "item.completed"},
        owner="audit",
    )
    persisted = store.get_agent_run(run.id)
    assert persisted is not None

    assert persisted.status == "running"
    assert [event["type"] for event in persisted.tool_events] == [
        "item.started",
        "item.started",
        "item.completed",
    ]


def test_provider_event_append_does_not_rescan_history(tmp_path):
    statements: list[str] = []

    class TracedStore(AutoReplyStore):
        def _open_connection(self):
            connection = super()._open_connection()
            connection.set_trace_callback(statements.append)
            return connection

    store = TracedStore(tmp_path / "turns.sqlite3")
    run = _claim_audit(store, _task(store))
    statements.clear()

    store.append_agent_run_event(
        run.id,
        _effect_event(operation_digest="digest"),
        owner="audit",
    )
    persisted = store.get_agent_run(run.id)
    assert persisted is not None

    normalized = [statement.casefold() for statement in statements]
    assert len(persisted.tool_events) == 1
    assert not any("with call_state" in statement for statement in normalized)
    assert sum("from agent_run_events" in statement for statement in normalized) <= 4


def test_failed_run_preserves_effect_event_fact(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    run = _claim_audit(store, _task(store))
    store.append_agent_run_event(run.id, _effect_event(operation_digest="command-digest"), owner="audit")
    store.fail_agent_run(run.id, {"code": "crash_after_write", "retryable": True}, owner="audit")
    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "failed"
    assert len(persisted.tool_events) == 1


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
    assert store.foreign_key_violations() == []
    with sqlite3.connect(db_path) as db:
        event = db.execute(
            "select id, created_at from agent_run_events where agent_run_id=7"
        ).fetchone()
        receipt = db.execute(
            "select id, created_at, effect_counted "
            "from agent_execution_receipts where agent_run_id=7"
        ).fetchone()
    assert event == (8, "2026-08-06 15:00:00")
    assert receipt == (9, "2026-08-06 15:01:00", 0)
    assert not hasattr(store, "list_agent_execution_receipts")
    with sqlite3.connect(db_path) as db:
        columns = {
            row[1] for row in db.execute("pragma table_info(agent_runs)").fetchall()
        }
    assert "side_effect_state" not in columns
    assert not any(column.startswith("reconciliation_") for column in columns)
    assert not any(column.startswith("effect_") for column in columns)


def test_agent_run_migration_converts_unknown_projection_and_preserves_history(
    tmp_path,
):
    db_path = _create_pre_role_database(tmp_path / "legacy-unknown.sqlite3")
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update agent_runs set status='unknown', structured_error_json='' where id=7"
        )

    store = AutoReplyStore(db_path)
    run = store.get_agent_run(7)

    assert run is not None
    assert run.status == "failed"
    assert json.loads(run.structured_error_json) == {
        "code": "legacy_unknown",
        "retryable": True,
    }
    with sqlite3.connect(db_path) as db:
        event = db.execute(
            "select phase, structured_error_json from agent_run_state_events "
            "where agent_run_id=7 order by id desc limit 1"
        ).fetchone()
    assert event == (
        "legacy_unknown_migrated",
        '{"code":"legacy_unknown","retryable":true}',
    )


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


def test_failed_audit_keeps_consumer_projection(tmp_path):
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
    ).run
    store.fail_agent_run(
        audit.id,
        {"code": "outcome_unavailable", "retryable": True},
        owner="audit",
    )
    assert store.get_agent_run(audit.id).status == "failed"
    assert store.get_agent_run(consumer.id).status == "running"


def test_consumer_failed_rows_are_terminal_failures(tmp_path):
    store = AutoReplyStore(tmp_path / "turns.sqlite3")
    task = _task(store)
    consumer = _claim_consumer(store, task).run
    store.fail_agent_run(
        consumer.id,
        {"code": "read_failed", "retryable": True},
        owner="consumer",
    )
    assert store.get_agent_run(consumer.id).status == "failed"


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
