"""Audit may quote exact service-prepared bodies without changing their bytes."""

import json
from dataclasses import replace

import pytest

from app.agent_contracts import AuditAgentResult
from app.agent_result import ResultParseError
from app.codex_decision import append_signature
from app.feedback_spike import build_callback_url, build_feedback_link_text
from app.reviewed_candidates import candidate_digest
from tests.test_audit_agent import (  # noqa: F401
    CapturingExecutor, _candidate, _runner, _wire,
)


BASE = "https://feedback.example.test"


@pytest.fixture
def callback_fixture(tmp_path):
    from tests.test_audit_agent import setup
    return setup.__wrapped__(tmp_path)


def prepared_body(base=BASE):
    return build_feedback_link_text(
        append_signature("Exact prepared notice"),
        up_url=build_callback_url(base, feedback_token="spike_1700000000_0123abcd", rating="up"),
        down_url=build_callback_url(base, feedback_token="spike_1700000000_0123abcd", rating="down"),
    )


def review_for(candidate, *, value=None):
    data = json.loads(json.loads(_wire(outcome="return").splitlines()[-1])["item"]["text"])
    data.update(proposal_revision=0, candidate_digest=candidate_digest(candidate))
    data["error"] = {"code": data.pop("error_code"), "retryable": data.pop("error_retryable"),
                     "authorization_required": data.pop("error_authorization_required")}
    data["feedback"].update(observation_basis="candidate_fields", candidate_field_assertions=[{
        "path": ["proposal", "actions", 0, "payload", "content"],
        "value": candidate.proposal.actions[0].payload["content"] if value is None else value,
        "json_type": "string",
    }])
    return AuditAgentResult.model_validate(data)


def candidate_with_body(body):
    candidate = _candidate()
    candidate.proposal.actions[0].payload["content"] = body
    return candidate


def test_bound_callback_assertion_has_same_existing_validation_as_prepared_body(monkeypatch):
    from app.audit_agent import audit_result_contains_sensitive_value
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    candidate = candidate_with_body(prepared_body())
    result = review_for(candidate)
    original = result.model_dump(mode="json")
    assert audit_result_contains_sensitive_value(result, candidate) is False
    assert result.model_dump(mode="json") == original
    assert result.feedback.candidate_field_assertions[0].value == prepared_body()


@pytest.mark.parametrize("body", [
    "access_token=synthetic-test-secret",
    "Read https://unrelated.example.test/?access_token=synthetic-test-secret",
    prepared_body("https://unrelated.example.test"),
    prepared_body().replace("rating=down", "rating=up"),
])
def test_assertions_do_not_exempt_credentials_or_other_callbacks(monkeypatch, body):
    from app.audit_agent import audit_result_contains_sensitive_value
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    candidate = candidate_with_body(body)
    try:
        sensitive = audit_result_contains_sensitive_value(review_for(candidate), candidate)
    except ResultParseError as exc:
        assert str(exc) == "agent_result_contains_sensitive_value"
        return
    assert sensitive is True


def test_callback_assertion_must_still_match_exact_candidate(monkeypatch):
    from app.audit_agent import audit_result_contains_sensitive_value
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    with pytest.raises(ResultParseError, match="contradicts"):
        audit_result_contains_sensitive_value(review_for(_candidate(), value=prepared_body()), _candidate())


def test_callback_exception_does_not_cover_authored_audit_prose(monkeypatch):
    from app.audit_agent import audit_result_contains_sensitive_value
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    candidate = candidate_with_body(prepared_body())
    result = review_for(candidate)
    result = result.model_copy(update={"summary": prepared_body()})
    assert audit_result_contains_sensitive_value(result, candidate) is True


def test_audit_context_explains_service_owned_postfix():
    from app.agent_context import AuditTurnContext, AgentTaskContext
    candidate = candidate_with_body(prepared_body())
    context = AuditTurnContext(task=AgentTaskContext(
        task_id=1, channel="dingtalk", conversation_id="cid", conversation_title="Notice",
        single_chat=True, trigger_message_id="msg", trigger_sender="Derek", trigger_text="Notify",
        trigger_create_time="2026-10-15", messages=(), materials=(), prior_receipts=(),
    ), proposal_revision=0, operation_id="audit", candidate=candidate,
       candidate_digest=candidate_digest(candidate), audit_rules="Review candidate")
    rendered = context.render()
    assert "Service-prepared outbound message postfix" in rendered
    assert "Consumer cannot remove" in rendered
    assert prepared_body() in rendered or json.dumps(prepared_body(), ensure_ascii=False) in rendered


def callback_setup(original_setup):
    store, task, _parent, context, config, router = original_setup
    candidate = candidate_with_body(prepared_body())
    parent = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer", proposal_revision=0,
        turn_attempt=1, parent_agent_run_id=None, operation_id="", owner="callback-consumer",
    ).run
    store.complete_agent_run(parent.id, candidate.model_dump(mode="json"), owner="callback-consumer")
    context = replace(context, candidate=candidate, candidate_digest=candidate_digest(candidate))
    return store, task, parent, context, config, router


def test_fresh_audit_persists_original_callback_assertion(callback_fixture, monkeypatch):
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    current = callback_setup(callback_fixture)
    store, task, parent, context, _config, _router = current
    review = review_for(context.candidate)
    output = review.model_dump(mode="json")
    output.pop("proposal_revision")
    output.pop("candidate_digest")
    output.pop("error")
    output.update(error_code="", error_retryable=False, error_authorization_required=False)
    stream = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(output)}})
    result = _runner(current, CapturingExecutor(stream)).run(
        task, context, turn_attempt=0, parent_agent_run_id=parent.id,
    )
    assert result.result.feedback.candidate_field_assertions[0].value == prepared_body()
    saved = store.get_agent_run(result.run_id)
    assert saved.status == "completed"
    assert json.loads(saved.adopted_result_json)["feedback"]["candidate_field_assertions"][0]["value"] == prepared_body()


def test_recovered_audit_checks_same_bound_callback_assertion(callback_fixture, monkeypatch):
    from types import SimpleNamespace
    import app.agent_turn_runner as turns

    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    current = callback_setup(callback_fixture)
    store, task, parent, context, config, _router = current
    runner = _runner(current, CapturingExecutor(""))
    run = store.claim_agent_run(
        task.id, task.execution_generation, role="audit", proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=parent.id, operation_id=context.operation_id, owner=runner.owner,
    ).run
    route = config.routes[0]
    runtime = store.claim_agent_runtime_attempt(
        run.id, route.name, route.runtime_kind.value, "local_oauth", "test-model",
    )
    store.complete_agent_runtime_attempt(runtime.id, "", "", 0, 0)
    monkeypatch.setattr(turns, "hashlib", SimpleNamespace(sha256=lambda _: SimpleNamespace(hexdigest=lambda: "")))
    monkeypatch.setattr(turns, "recover_completed_runtime_domain_result", lambda *a, **kw: review_for(context.candidate))
    result = runner._execute_claimed(task, context, run=run, rendered_rules="Review", force_new_session=False)
    assert result.result.feedback.candidate_field_assertions[0].value == prepared_body()
    assert store.get_agent_run(result.run_id).status == "completed"


@pytest.mark.parametrize("recover", [False, True])
def test_runtime_codec_uses_exact_candidate_bound_audit_inspection(callback_fixture, monkeypatch, recover):
    from app.audit_agent import audit_result_contains_sensitive_value
    from app.agent_turn_runner import (_encode_runtime_domain_result, _decode_runtime_domain_result,
                                      recover_completed_runtime_domain_result)
    from app.store import AgentRole
    from tests.runtime_route_env import claude_api_env
    from app.agent_runtime_config import load_runtime_config

    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    store, task, parent, context, _, _ = callback_setup(callback_fixture)
    result = review_for(context.candidate)
    def inspect(value):
        return audit_result_contains_sensitive_value(value, context.candidate)
    original = result.model_dump(mode="json")
    if recover:
        config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "claude_api",
                                      **claude_api_env("synthetic-key")})
        route = config.routes[0]
        run = store.claim_agent_run(task.id, task.execution_generation, role="audit",
            proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
            operation_id=context.operation_id, owner="codec-audit").run
        attempt = store.claim_agent_runtime_attempt(run.id, route.name, route.runtime_kind.value,
                                                   route.credential_mode.value, route.model)
        monkeypatch.setattr("app.agent_turn_runner.read_native_result_stream", lambda *a: "native result")
        decoded = recover_completed_runtime_domain_result(
            attempt.model_copy(update={"result_schema_id": "schema-v1"}), run, schema_id="schema-v1",
            parse_result=lambda raw: result, result_contains_sensitive_value=inspect)
    else:
        encoded = _encode_runtime_domain_result(schema_id="schema-v1", role=AgentRole.AUDIT,
            result=result, result_contains_sensitive_value=inspect)
        decoded = _decode_runtime_domain_result(encoded, schema_id="schema-v1", role=AgentRole.AUDIT,
            result_contains_sensitive_value=inspect).result
    assert decoded.model_dump(mode="json") == original


@pytest.mark.parametrize("field", ["summary", "assertion"])
def test_runtime_bound_inspection_keeps_rejecting_real_sensitive_values(monkeypatch, field):
    from app.audit_agent import audit_result_contains_sensitive_value
    from app.agent_turn_runner import _encode_runtime_domain_result
    from app.store import AgentRole

    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    candidate = candidate_with_body(prepared_body() if field == "summary" else "access_token=synthetic-secret")
    result = review_for(candidate)
    if field == "summary":
        result = result.model_copy(update={"summary": "access_token=synthetic-secret"})
    with pytest.raises(ValueError, match="contains_sensitive_value"):
        _encode_runtime_domain_result(schema_id="schema-v1", role=AgentRole.AUDIT, result=result,
            result_contains_sensitive_value=lambda value: audit_result_contains_sensitive_value(value, candidate))


def test_fresh_native_audit_persists_bound_callback(callback_fixture, monkeypatch, tmp_path):
    from app import native_trajectory
    from app.audit_agent import audit_result_contains_sensitive_value
    from app.agent_turn_runner import AgentTurnProcess
    from app.agent_runtime_config import load_runtime_config
    from app.claude_runtime_adapter import ClaudeRuntimeAdapter
    from app.process_runner import ProcessRunResult
    from tests.runtime_route_env import claude_api_env

    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", BASE)
    store, task, parent, context, _, _ = callback_setup(callback_fixture)
    result = review_for(context.candidate)
    run = store.claim_agent_run(task.id, task.execution_generation, role="audit",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=context.operation_id, owner="codec-audit").run
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "claude_api",
                                  **claude_api_env("synthetic-key")})
    route = config.routes[0]
    sid = "native-bound-callback-audit"
    text = json.dumps(result.model_dump(mode="json"))
    events = [
        {"type": "system", "subtype": "init", "session_id": sid},
        {"type": "assistant", "session_id": sid, "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}]}},
        {"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "result": text},
    ]
    native = tmp_path / "native.jsonl"
    native.touch()
    monkeypatch.setattr(native_trajectory, "claude_session_path", lambda *a, **k: native)
    def executor(command, *, on_stdout_line, **kwargs):
        native.write_text(json.dumps(events[1]) + "\n")
        for event in events:
            on_stdout_line(json.dumps(event))
        return ProcessRunResult(0, "\n".join(map(json.dumps, events)), "")
    process = AgentTurnProcess(store=store, task=task, workspace=tmp_path, owner="codec-audit",
        executor=executor, runtime_config=config, forced_runtime_route=route,
        claude_adapter=ClaudeRuntimeAdapter(workspace=tmp_path, config=config,
                                           claude_bin="claude-test", service_mcp_servers=()))
    completed = process.execute(run=run, prompt="Review exact candidate", session_id=None,
        developer_instructions="Review current candidate", configure_command=lambda cmd: None,
        parse_result=lambda raw: result,
        result_contains_sensitive_value=lambda value: audit_result_contains_sensitive_value(value, context.candidate))
    assert completed.result == result
    assert store.get_agent_run(run.id).status == "completed"
    [attempt] = store.list_agent_runtime_attempts(run.id)
    assert attempt.status == "completed"
    assert json.loads(store.get_agent_run(run.id).adopted_result_json) == result.model_dump(mode="json")
