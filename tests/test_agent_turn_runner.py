import inspect
import json
from types import SimpleNamespace

import pytest
import app.agent_turn_runner as agent_turn_runner
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.agent_turn_runner import (
    AgentTurnProcess,
    RuntimeRouteUnavailableError,
    _fallback_requested_session_id,
    _decode_runtime_domain_result,
    _encode_runtime_domain_result,
    _native_output_schema_for_route,
    _process_failure_detail,
    _persist_provider_event,
    _prompt_for_runtime_route,
    _runtime_result_schema_id,
)
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore, RuntimeRoutePausedError


def test_compact_prompt_is_used_only_for_its_exact_codex_session():
    route = RuntimeRoute(
        name="codex_oauth",
        runtime_kind=RuntimeKind.CODEX_CLI,
        credential_mode=CredentialMode.LOCAL_OAUTH,
        model="gpt-5.6-luna",
    )
    scoped = {
        "route_name": "codex_oauth",
        "session_id": "session-a",
        "prompt": "feedback-only",
        "invocation_facts": {"prompt_sections": [{"section": "feedback"}]},
    }

    prompt, facts = _prompt_for_runtime_route(
        route, "session-a", "full-task-context", {"full": True}, scoped
    )

    assert prompt == "feedback-only"
    assert facts == scoped["invocation_facts"]


@pytest.mark.parametrize(
    ("runtime_kind", "route_name", "session_id"),
    [
        (RuntimeKind.CODEX_CLI, "codex_oauth", "different-session"),
        (RuntimeKind.CODEX_CLI, "other-codex", "session-a"),
        (RuntimeKind.CLAUDE_CLI, "codex_oauth", "session-a"),
        (RuntimeKind.FRIDAY_RUNTIME, "codex_oauth", "session-a"),
    ],
)
def test_other_route_or_session_keeps_full_prompt(
    runtime_kind, route_name, session_id
):
    route = RuntimeRoute(
        name=route_name,
        runtime_kind=runtime_kind,
        credential_mode=CredentialMode.LOCAL_OAUTH,
        model="route-model",
    )
    full_facts = {"prompt_sections": [{"section": "full"}]}
    scoped = {
        "route_name": "codex_oauth",
        "session_id": "session-a",
        "prompt": "feedback-only",
        "invocation_facts": {"prompt_sections": [{"section": "feedback"}]},
    }

    prompt, facts = _prompt_for_runtime_route(
        route, session_id, "full-task-context", full_facts, scoped
    )

    assert prompt == "full-task-context"
    assert facts is full_facts


def test_native_output_schema_is_selected_for_codex_consumer_and_audit():
    general = _native_output_schema_for_route(
        RuntimeKind.CODEX_CLI, AgentRole.CONSUMER,
    )
    oa = _native_output_schema_for_route(
        RuntimeKind.CODEX_CLI, AgentRole.CONSUMER, purpose="oa",
    )

    assert general is not None and general.name == "consumer_general_agent_native_output.schema.json"
    assert oa is not None and oa.name == "consumer_oa_agent_native_output.schema.json"
    audit = _native_output_schema_for_route(
        RuntimeKind.CODEX_CLI, AgentRole.AUDIT,
    )
    assert audit is not None and audit.name == "audit_agent_native_output.schema.json"
    assert _native_output_schema_for_route(RuntimeKind.CLAUDE_CLI, AgentRole.AUDIT) is None
    assert _native_output_schema_for_route(RuntimeKind.CLAUDE_CLI, AgentRole.CONSUMER) is None


def test_runtime_result_recovery_binds_native_consumer_schema(tmp_path, monkeypatch):
    execution_contract = "execution-contract-digest"
    native_id = _runtime_result_schema_id(
        execution_contract, RuntimeKind.CODEX_CLI, AgentRole.CONSUMER,
    )
    claude_id = _runtime_result_schema_id(
        execution_contract, RuntimeKind.CLAUDE_CLI, AgentRole.CONSUMER,
    )
    audit_id = _runtime_result_schema_id(
        execution_contract, RuntimeKind.CODEX_CLI, AgentRole.AUDIT,
    )
    oa_id = _runtime_result_schema_id(
        execution_contract, RuntimeKind.CODEX_CLI, AgentRole.CONSUMER, purpose="oa",
    )

    assert len({native_id, claude_id, audit_id, oa_id}) == 4
    assert audit_id != claude_id
    assert native_id == _runtime_result_schema_id(
        execution_contract, RuntimeKind.CODEX_CLI, AgentRole.CONSUMER,
    )
    schema_path = tmp_path / "consumer-schema.json"
    schema_path.write_bytes(b'{"schema":"first"}')
    monkeypatch.setattr(
        agent_turn_runner, "native_output_schema_path",
        lambda role="consumer", *, purpose="general": schema_path,
    )
    first_schema_id = _runtime_result_schema_id(
        execution_contract, RuntimeKind.CODEX_CLI, AgentRole.CONSUMER,
    )
    schema_path.write_bytes(b'{"schema":"changed"}')
    changed_schema_id = _runtime_result_schema_id(
        execution_contract, RuntimeKind.CODEX_CLI, AgentRole.CONSUMER,
    )
    assert changed_schema_id != first_schema_id


@pytest.mark.parametrize("role", [AgentRole.CONSUMER, AgentRole.AUDIT])
@pytest.mark.parametrize("session_id", ["failed-session", ""])
def test_explicit_noncontinuable_failure_requires_fresh_session(role, session_id):
    run = SimpleNamespace(
        role=role, proposal_revision=2, status="failed", codex_session_id=session_id,
        turn_attempt=1, id=17,
        structured_error_json=json.dumps({
            "code": "dependency_read_unavailable", "retryable": True,
            "session_continuable": False,
        }),
    )
    def status_runs(*_args, **kwargs):
        assert kwargs["load_events"] is False
        return [run]
    store = SimpleNamespace(list_agent_runs_for_task_generation=status_runs)
    task = SimpleNamespace(id=9, execution_generation="current")
    assert agent_turn_runner.repeated_result_failure_requires_fresh_session(
        store, task, role=role, proposal_revision=2,
    )


def test_retryable_consumer_failure_does_not_treat_default_flag_as_session_loss():
    run = SimpleNamespace(
        role=AgentRole.CONSUMER, proposal_revision=0, status="failed",
        codex_session_id="existing-session", turn_attempt=1, id=1,
        structured_error_json=json.dumps({
            "code": "agent_reported_failure", "retryable": True,
            "source": "agent", "session_continuable": False,
        }),
    )
    store = SimpleNamespace(
        list_agent_runs_for_task_generation=lambda *_args, **_kwargs: [run]
    )
    task = SimpleNamespace(id=9, execution_generation="current")

    assert not agent_turn_runner.repeated_result_failure_requires_fresh_session(
        store, task, role=AgentRole.CONSUMER, proposal_revision=0,
    )


def test_retryable_audit_failure_keeps_existing_fresh_session_behavior():
    run = SimpleNamespace(
        role=AgentRole.AUDIT, proposal_revision=0, status="failed",
        codex_session_id="existing-session", turn_attempt=1, id=1,
        structured_error_json=json.dumps({
            "code": "agent_reported_failure", "retryable": True,
            "source": "agent", "session_continuable": False,
        }),
    )
    store = SimpleNamespace(
        list_agent_runs_for_task_generation=lambda *_args, **_kwargs: [run]
    )
    task = SimpleNamespace(id=9, execution_generation="current")

    assert agent_turn_runner.repeated_result_failure_requires_fresh_session(
        store, task, role=AgentRole.AUDIT, proposal_revision=0,
    )


def test_retryable_service_failure_still_honors_explicit_noncontinuable_session():
    run = SimpleNamespace(
        role=AgentRole.CONSUMER, proposal_revision=0, status="failed",
        codex_session_id="existing-session", turn_attempt=1, id=1,
        structured_error_json=json.dumps({
            "code": "dependency_read_unavailable", "retryable": True,
            "source": "service", "session_continuable": False,
        }),
    )
    store = SimpleNamespace(
        list_agent_runs_for_task_generation=lambda *_args, **_kwargs: [run]
    )
    task = SimpleNamespace(id=9, execution_generation="current")

    assert agent_turn_runner.repeated_result_failure_requires_fresh_session(
        store, task, role=AgentRole.CONSUMER, proposal_revision=0,
    )


@pytest.mark.parametrize("continuable", [True, None, "false"])
def test_fresh_session_requires_explicit_false_not_missing_or_coerced_flag(continuable):
    run = SimpleNamespace(
        role=AgentRole.CONSUMER, proposal_revision=0, status="failed",
        codex_session_id="existing-session", turn_attempt=1, id=1,
        structured_error_json=json.dumps({"session_continuable": continuable}),
    )
    def status_runs(*_args, **kwargs):
        assert kwargs["load_events"] is False
        return [run]
    store = SimpleNamespace(list_agent_runs_for_task_generation=status_runs)
    task = SimpleNamespace(id=9, execution_generation="current")
    assert not agent_turn_runner.repeated_result_failure_requires_fresh_session(
        store, task, role=AgentRole.CONSUMER, proposal_revision=0,
    )


@pytest.mark.parametrize("session_id", ["valid", ""])
def test_later_completed_session_covers_older_noncontinuable_failure(session_id):
    earlier = SimpleNamespace(
        role=AgentRole.CONSUMER, proposal_revision=0, status="failed",
        codex_session_id="old", turn_attempt=1, id=1,
        structured_error_json='{"session_continuable":false}',
    )
    later = SimpleNamespace(
        role=AgentRole.CONSUMER, proposal_revision=0, status="completed",
        codex_session_id=session_id, turn_attempt=2, id=2, structured_error_json="",
    )
    def status_runs(*_args, **kwargs):
        assert kwargs["load_events"] is False
        return [earlier, later]
    store = SimpleNamespace(list_agent_runs_for_task_generation=status_runs)
    task = SimpleNamespace(id=9, execution_generation="current")
    assert not agent_turn_runner.repeated_result_failure_requires_fresh_session(
        store, task, role=AgentRole.CONSUMER, proposal_revision=0,
    )


def test_result_parse_failure_preserves_runtime_attempt_code():
    from app.agent_result import ResultParseError

    calls = []

    class Store:
        def get_agent_runtime_attempt(self, attempt_id):
            return SimpleNamespace(
                id=attempt_id, status="running", session_id="",
                transcript_start=0, transcript_end=0,
            )

        def fail_agent_runtime_attempt(self, *args, **kwargs):
            calls.append((args, kwargs))

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()
    runner._fail_runtime_attempt_unclassified(
        SimpleNamespace(id=17),
        ResultParseError("no valid typed result JSON found in Codex JSONL"),
    )

    assert calls == [((17, "result", "codex_result_missing", False),
                      {"transcript_start": 0, "transcript_end": 0})]


def test_routed_runtime_failure_preserves_router_failure_code():
    from app.agent_runtime_router import RoutedCodexExecutionError

    calls = []

    class Store:
        def get_agent_runtime_attempt(self, attempt_id):
            return SimpleNamespace(
                id=attempt_id, status="running", session_id="",
                transcript_start=0, transcript_end=0,
            )

        def fail_agent_runtime_attempt(self, *args, **kwargs):
            calls.append((args, kwargs))

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()
    runner._fail_runtime_attempt_unclassified(
        SimpleNamespace(id=18),
        RoutedCodexExecutionError(
            "runtime_executor_failed",
            "executor raised",
            failure_code="runtime_executor_failed",
        ),
    )

    assert calls == [((18, "process", "runtime_executor_failed", False),
                      {"transcript_start": 0, "transcript_end": 0})]


@pytest.mark.parametrize(
    ("failure_class", "retryable", "expected_retryable"),
    [
        ("capability", False, False),
        ("capacity", True, True),
        (None, False, True),
    ],
)
def test_routed_failure_persists_router_retryability(
    failure_class, retryable, expected_retryable,
):
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError

    calls = []

    class Store:
        def get_agent_run(self, run_id):
            return SimpleNamespace(id=run_id, status="running")

        def fail_agent_run(self, run_id, error, *, owner):
            calls.append((run_id, error, owner))

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()
    runner.owner = "test-owner"
    error = RoutedCodexExecutionError(
        "runtime_execution_failed",
        failure_class=(RuntimeFailureClass(failure_class) if failure_class else None),
        failure_code="provider_failure",
        retryable_external_dependency=retryable,
    )

    runner._fail_routed_execution(SimpleNamespace(id=19), error)

    assert calls[0][0] == 19
    assert calls[0][1]["retryable"] is expected_retryable
    assert calls[0][1]["source_code"] == "provider_failure"
    assert calls[0][1]["stage"] == "execution"
    assert calls[0][1]["source"] == "runtime"
    assert calls[0][1]["session_continuable"] is True
    assert calls[0][2] == "test-owner"


def test_same_route_capacity_retry_resumes_observed_audit_session():
    assert _fallback_requested_session_id(
        previous_route_name="codex_oauth",
        next_route_name="codex_oauth",
        fresh_session=False,
        initial_session_id=None,
        failed_session_id="audit-session-1",
    ) == "audit-session-1"
    assert _fallback_requested_session_id(
        previous_route_name="codex_api",
        next_route_name="codex_oauth",
        fresh_session=False,
        initial_session_id=None,
        failed_session_id="api-session-1",
    ) is None
    assert _fallback_requested_session_id(
        previous_route_name="codex_oauth",
        next_route_name="codex_oauth",
        fresh_session=True,
        initial_session_id=None,
        failed_session_id="audit-session-1",
    ) is None


def test_context_overflow_fresh_retry_clears_only_the_failed_route_session():
    cleared: list[tuple[str, str, str]] = []
    failed_attempt = SimpleNamespace(
        id=73,
        parent_id=19,
        route_name="codex_oauth",
        status="failed",
        session_mode="resume",
        failure_class="session",
        failure_code="codex_context_window_exceeded",
        source_session_id="shared-session",
    )

    class Store:
        def get_agent_runtime_attempt(self, attempt_id):
            assert attempt_id == failed_attempt.id
            return failed_attempt

        def clear_conversation_runtime_session_if_matches(self, *args):
            cleared.append(args)

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()
    runner.task = SimpleNamespace(conversation_id="task-agent:work-tracking:v1")
    runner._clear_incompatible_route_session_for_fresh_retry(
        run=SimpleNamespace(id=19, role=AgentRole.CONSUMER),
        route=SimpleNamespace(name="codex_oauth"),
        failed_attempt=failed_attempt,
    )

    assert cleared == [(
        "task-agent:work-tracking:v1", "codex_oauth", "shared-session",
    )]


def test_runner_has_no_application_effect_recovery_policy_helpers():
    """Provider traces stay opaque; the runner has no effect recovery state machine."""

    obsolete_module_helpers = {
        "_has_unclosed_effects",
        "_closed_effect_failure",
        "_failed_agent_cli_read_event",
        "_matching_effect_metadata",
        "_trusted_claude_effect_event",
        "_attempted_skill_paths",
    }
    assert not obsolete_module_helpers.intersection(vars(agent_turn_runner))
    assert not hasattr(AgentTurnProcess, "_normalized_effect_event")
    assert not hasattr(AgentTurnProcess, "_require_direct_send_receipt")
    assert not hasattr(AgentTurnProcess, "_record_direct_send_receipt")
    assert not hasattr(AgentTurnProcess, "_validate_audit_result")


def test_successor_claim_route_pause_stays_retryable():
    class Store:
        def claim_agent_runtime_attempt(self, *args, **kwargs):
            raise RuntimeRoutePausedError("runtime route is paused")

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()
    route = SimpleNamespace(
        name="codex_api",
        runtime_kind=SimpleNamespace(value="codex_cli"),
        credential_mode=SimpleNamespace(value="service_api"),
        model="MiniMax-M2.5",
    )

    with pytest.raises(RuntimeRouteUnavailableError) as raised:
        runner._claim_and_start_attempt(SimpleNamespace(id=19205), route, None)

    assert raised.value.code == "runtime_execution_failed"
    assert raised.value.reason == "codex_api_paused"


def _audit_result(proposal_revision: int) -> AuditAgentResult:
    return AuditAgentResult.model_validate(
        {
            "outcome": "return",
            "summary": "缺少冲突处理动作",
            "proposal_revision": proposal_revision,
            "candidate_digest": "a" * 64,
            "feedback": {
                "rule": "Rule 15 (calendar_conflicts)",
                "observation": "两个会议在 14:00-14:30 重叠",
                "requested_revision": "拒绝 HR 例会并通知发起人",
            },
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
            "risk": "medium",
            "confidence": 0.9,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
        }
    )


def test_runner_wire_contract_has_no_application_recovery_evidence_fields():
    execute_parameters = inspect.signature(AgentTurnProcess.execute).parameters
    assert not {
        "recovery_phase",
        "authorized_recovery_actions",
        "recovery_authorizations",
        "allow_effectful_tools",
        "required_skill_receipts",
    }.intersection(execute_parameters)

    result = ConsumerAgentResult.model_validate(
        {
            "outcome": "no_action",
            "summary": "无需处理",
            "proposal": None,
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 0.75,
            "information_completeness": 0.8,
        }
    )
    encoded = _encode_runtime_domain_result(
        schema_id="schema-v1",
        role=AgentRole.CONSUMER,
        result=result,
    )
    assert set(json.loads(encoded)) == {"schema_id", "version", "role", "result"}
    assert {
        "risk",
        "confidence",
        "rule_coverage",
        "information_completeness",
    } <= set(json.loads(encoded)["result"])
    decoded = _decode_runtime_domain_result(
        encoded,
        schema_id="schema-v1",
        role=AgentRole.CONSUMER,
    )
    assert decoded.result == result


def test_runtime_projection_round_trip_preserves_all_decision_quality_fields():
    result = ConsumerAgentResult.model_validate(
        {
            "outcome": "no_action",
            "summary": "无需处理",
            "proposal": None,
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
            "risk": "medium",
            "confidence": 0.61,
            "rule_coverage": 0.42,
            "information_completeness": 0.37,
        }
    )
    encoded = _encode_runtime_domain_result(
        schema_id="schema-v1", role=AgentRole.CONSUMER, result=result
    )
    projected = json.loads(encoded)["result"]
    assert {
        key: projected[key]
        for key in ("risk", "confidence", "rule_coverage", "information_completeness")
    } == {
        "risk": "medium",
        "confidence": 0.61,
        "rule_coverage": 0.42,
        "information_completeness": 0.37,
    }
    assert (
        _decode_runtime_domain_result(
            encoded, schema_id="schema-v1", role=AgentRole.CONSUMER
        ).result
        == result
    )


def test_provider_tool_event_does_not_create_application_delivery_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "agent-turn.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-agent",
        conversation_title="Agent receipt",
        single_chat=False,
        trigger_message_id="msg-agent",
        trigger_create_time="2026-09-05 10:00:00",
        trigger_sender="Derek",
        trigger_text="请处理",
        execution_generation="generation-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="audit-1",
        owner="audit",
    ).run
    expected_argv = [
        "dws",
        "chat",
        "message",
        "send",
        "--group",
        "cid-agent",
        "--content",
        "候选正文",
        "--yes",
    ]
    payload = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "id": "write-1",
            "server": "agent_cli",
            "tool": "execute_reviewed_write",
            "arguments": {"authorization_id": "authorization-1", "argv": expected_argv},
            "result": {"content": [{"type": "text", "text": "sent"}]},
        },
    }
    event = _persist_provider_event(payload)
    assert event is not None

    # Persisting a provider trace must not make the application infer that a
    # business delivery happened. The provider result is projected only via
    # its stable external action identity.
    store.append_agent_run_event(run.id, event, owner="audit")

    assert event["item"] == payload["item"]
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) is None


def test_runtime_failure_persists_bounded_redacted_detail():
    store_calls: list[tuple[int, dict[str, object]]] = []

    class Store:
        def get_agent_run(self, run_id: int):
            assert run_id == 7
            return SimpleNamespace(status="running")

        def fail_agent_run(self, run_id: int, structured_error, *, owner: str):
            store_calls.append((run_id, structured_error))
            assert owner == "test-owner"

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()
    runner.owner = "test-owner"

    runner._fail_running(
        SimpleNamespace(id=7),
        "codex_process_failed",
        detail=agent_turn_runner._runtime_failure_detail(
            RuntimeError("database is locked while reading /tmp/private-output")
        ),
        stage="execution",
        source="codex",
        source_code="codex_process_failed",
        session_continuable=True,
    )

    assert len(store_calls) == 1
    payload = store_calls[0][1]
    assert payload["code"] == "codex_process_failed"
    assert payload["stage"] == "execution"
    assert payload["source_code"] == "codex_process_failed"
    assert "database is locked" in payload["detail"]
    assert "/tmp/" not in payload["detail"]


def test_runtime_failure_detail_keeps_wrapped_validation_reason():
    try:
        try:
            raise ValueError("invalid AgentEnvelope: missing user_response")
        except ValueError as cause:
            raise RuntimeError("runtime_result_validation_failed") from cause
    except RuntimeError as exc:
        detail = agent_turn_runner._runtime_failure_detail(exc)

    assert "invalid AgentEnvelope: missing user_response" in detail
    assert "runtime_result_validation_failed" in detail


def test_process_failure_detail_keeps_diagnostics_without_agent_output():
    process = ProcessRunResult(
        returncode=1,
        stdout=(
            '{"type":"error","error":{"message":"MCP startup failed: timed out"}}\n'
            '{"type":"agent_message","text":"private business output"}'
        ),
        stderr="fatal: authorization token=secret-value",
    )

    detail = _process_failure_detail(process)

    assert "MCP startup failed: timed out" in detail
    assert "fatal: authorization [REDACTED]" in detail
    assert "secret-value" not in detail
    assert "private business output" not in detail


def test_process_failure_detail_redacts_original_lines_before_joining_fields():
    diagnostic = "access token: load access token: acquiring file lock"
    process = ProcessRunResult(
        returncode=1,
        stdout=json.dumps({
            "type": "error",
            "error": {"message": "secret=other-value\nmore diagnostics"},
        }),
        stderr="token=secret-value \t\n" + diagnostic,
    )

    detail = _process_failure_detail(process)

    assert "secret-value" not in detail
    assert "other-value" not in detail
    assert diagnostic in detail
    assert "more diagnostics" in detail
    assert "[REDACTED]" in detail


@pytest.mark.parametrize(("raw", "secret"), [
    ("Bearer\nabcdefghi", "abcdefghi"),
    ("Basic\ndXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ("token=\nsecret-value", "secret-value"),
])
def test_process_failure_detail_keeps_cross_line_credential_redaction(raw, secret):
    process = ProcessRunResult(
        returncode=1,
        stdout=json.dumps({"type": "error", "error": {"message": raw}}),
        stderr="",
    )

    detail = _process_failure_detail(process)

    assert secret not in detail
    assert "[REDACTED]" in detail


def test_raise_for_process_failure_attaches_diagnostics_to_runtime_error():
    class Store:
        def get_agent_run(self, run_id: int):
            assert run_id == 9
            return SimpleNamespace(tool_events=())

    runner = object.__new__(AgentTurnProcess)
    runner.store = Store()

    with pytest.raises(RuntimeError) as raised:
        runner._raise_for_process_failure(
            ProcessRunResult(
                returncode=1,
                stdout='{"type":"error","message":"worker crashed"}',
                stderr="fatal: MCP connection refused",
            ),
            run=SimpleNamespace(id=9, role=AgentRole.AUDIT),
        )

    assert str(raised.value) == "codex_process_failed"
    assert raised.value.detail == (
        "fatal: MCP connection refused | worker crashed"
    )


def test_a_result_correction_names_what_the_contract_says_is_wrong() -> None:
    """Task 384711: two retries were told only `result: value_error`.

    They resent the same result twice. The contract's own sentence says what to
    change; the rejected model output must still stay out of the detail.
    """
    from pydantic import BaseModel, ValidationError, model_validator

    from app.agent_result import ResultParseError
    from app.agent_turn_runner import _result_parse_error_detail

    class Result(BaseModel):
        outcome: str

        @model_validator(mode="after")
        def check(self) -> "Result":
            raise ValueError(
                "authorization needs_human requires complete information and rule coverage"
            )

    try:
        Result.model_validate({"outcome": "secret model text"})
    except ValidationError as exc:
        error = ResultParseError("invalid")
        error.__cause__ = exc

    detail = _result_parse_error_detail(error)

    assert "requires complete information and rule coverage" in detail
    assert "secret model text" not in detail


@pytest.mark.parametrize(
    ("runtime_kind", "credential_mode"),
    [
        ("codex_cli", "service_api"),
        ("claude_cli", "service_api"),
        ("codex_cli", "local_oauth"),
    ],
)
def test_forced_fresh_session_applies_to_every_route(
    tmp_path, runtime_kind, credential_mode
):
    """No route is exempt: a forced fresh turn never resumes a persisted session."""

    from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute

    store = AutoReplyStore(tmp_path / "runner.sqlite3")
    store.upsert_conversation_runtime_session(
        "cid-kind", "kksj", "persisted-session", "contract-v1"
    )
    route = RuntimeRoute(
        name="kksj",
        runtime_kind=RuntimeKind(runtime_kind),
        credential_mode=CredentialMode(credential_mode),
        model="qwen3.8-27b",
    )
    process = SimpleNamespace(
        store=store, task=SimpleNamespace(conversation_id="cid-kind")
    )

    session = agent_turn_runner.AgentTurnProcess._session_for_route(
        process,
        route,
        role=AgentRole.CONSUMER,
        requested_session_id=None,
        force_new_session=True,
    )

    assert session is None


def test_native_audit_recovery_fingerprint_tracks_current_schema(tmp_path, monkeypatch):
    schema = tmp_path / "audit-native.json"
    schema.write_bytes(b'{"schema":"first"}')
    monkeypatch.setattr(agent_turn_runner, "native_output_schema_path", lambda *args, **kwargs: schema)
    first = _runtime_result_schema_id("audit-contract", RuntimeKind.CODEX_CLI, AgentRole.AUDIT)
    schema.write_bytes(b'{"schema":"changed"}')
    changed = _runtime_result_schema_id("audit-contract", RuntimeKind.CODEX_CLI, AgentRole.AUDIT)
    assert first != changed
    assert changed != _runtime_result_schema_id("audit-contract", RuntimeKind.CLAUDE_CLI, AgentRole.AUDIT)
