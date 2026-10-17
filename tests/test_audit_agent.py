"""Audit reviews the whole typed candidate without provider writes.

The former Audit-executes tests were removed with that obsolete responsibility;
system execution and receipts are covered by test_system_executor.py.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.run_storage import AGENT_RUN_SQL
from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_cron.commands import (
    ServiceCommandConsumerContext,
    ServiceCommandSkillMaterial,
)
from app.agent_contracts import AuditAgentResult, AuditOutcome, ConsumerAgentResult
from app.agent_result import ResultParseError
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import AgentRuntimeRouter
from app.audit_agent import (
    AuditAgentRunner,
    validate_audit_candidate_decision_quality,
    validate_audit_candidate_fields,
)
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.process_runner import ProcessRunResult
from app.reviewed_candidates import candidate_digest
from app.store import AgentRole, AutoReplyStore
from tests.runtime_route_env import codex_api_env


def _candidate() -> ConsumerAgentResult:
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": "Notify applicant", "proposal": {
            "objective": "Send exact notice", "actions": [{
                "description": "Notify applicant", "action_identity": "notice",
                "capability": "dingtalk-chat", "operation": "send_direct_message",
                "target": {"open_dingtalk_id": "applicant"},
                "payload": {"content": "Exact prepared notice"},
            }], "sourced_facts": [], "authored_judgment": "Applicant needs status",
        }, "decision_options": [], "error": {"code": "", "retryable": False,
            "authorization_required": False}, "risk": "low", "confidence": 0.9,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        "needs_human_reason": None, "decision_basis": None,
        "requested_input": None, "stage_index": 0,
        "predecessor_review_id": None, "continue_after_execution": False,
        "durable_memories": [],
    })


def test_audit_rechecks_needs_human_candidate_against_quality_scores():
    candidate = ConsumerAgentResult.model_validate({
        "outcome": "needs_human", "summary": "Choose a policy",
        "proposal": None, "decision_options": [
            {"key": "A", "label": "Adopt rule A", "instruction": "Use rule A for this task class.",
             "consequence": "Future matching tasks use rule A.", "applies_to": "task_class",
             "plan": _candidate().proposal.model_dump(mode="json")},
            {"key": "B", "label": "Adopt rule B", "instruction": "Use rule B for this task class.",
             "consequence": "Future matching tasks use rule B.", "applies_to": "task_class",
             "terminal_outcome": "skipped", "reason": "Do not proceed without a rule choice."},
        ],
        "requested_input": None,
        "needs_human_reason": "The rule coverage is incomplete",
        "decision_basis": {
            "verified_facts": [{
                "assertion": "The current decision depends on a specific unresolved policy.",
                "references": ["source:1"],
            }],
            "rule_evidence": [{
                "assertion": "No applicable rule covers this decision.",
                "references": ["skill:current"],
            }],
            "quality_explanation": "Information is complete but the applicable rule is missing.",
            "no_external_action_evidence": [{
                "assertion": "No external action has been taken.",
                "references": ["audit:no-action"],
            }],
            "conclusion": "A rule decision is required.",
        },
        "risk": "low", "confidence": 1.0, "rule_coverage": 0.49,
        "information_completeness": 1.0,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    }).model_copy(update={
        "rule_coverage": 1.0,
        "risk": "low",
        "confidence": 0.99,
    })
    audit_result = AuditAgentResult.model_validate({
        "outcome": "approve", "summary": "Reviewed candidate",
        "proposal_revision": 0, "candidate_digest": "a" * 64,
        "evidence_refs": ["source:1"], "feedback": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 0.9, "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })

    with pytest.raises(ResultParseError, match="does not satisfy decision quality gate"):
        validate_audit_candidate_fields(audit_result, candidate)


def test_audit_rejects_legacy_current_instance_question_as_needs_human():
    from app.agent_contracts import parse_persisted_consumer_result

    candidate = parse_persisted_consumer_result({
        "outcome": "needs_human", "summary": "Need a current salary boundary",
        "proposal": None, "decision_options": [],
        "requested_input": "What salary ceiling should I use for this candidate?",
        "needs_human_reason": "This current business choice is consequential",
        "decision_basis": {
            "verified_facts": [{"assertion": "Known current facts were reviewed.", "references": ["source:1"]}],
            "rule_evidence": [{"assertion": "No missing reusable rule was identified.", "references": ["skill:1"]}],
            "quality_explanation": "A current-instance answer is being requested.",
            "no_external_action_evidence": [{"assertion": "No action has been taken.", "references": ["audit:1"]}],
            "conclusion": "This is a fact or business question, not a reusable rule decision.",
        },
        "risk": "high", "confidence": 0.42, "rule_coverage": 1.0,
        "information_completeness": 0.62,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    })
    with pytest.raises(ResultParseError, match="current-instance questions must be ordinary proposals"):
        validate_audit_candidate_decision_quality(candidate)


def _wire(*, outcome: str = "approve") -> str:
    result = {
        "outcome": outcome, "summary": "Reviewed whole candidate",
        "evidence_refs": ["source:1"],
        "feedback": ({"rule": "Missing source", "observation": "Unverified fact",
                     "requested_revision": "Read the source", "observation_basis": "business",
                     "candidate_field_assertions": []} if outcome in ("return", "reject") else None),
        "error_code": "", "error_retryable": False,
        "error_authorization_required": False, "risk": "low", "confidence": 0.9,
        "rule_coverage": 1.0, "information_completeness": 1.0,
    }
    return "\n".join((
        json.dumps({"type": "thread.started", "thread_id": "audit-session"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(result)}}),
    ))


class CapturingExecutor:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []
        self.environments: list[dict[str, str]] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        if getattr(self, 'capture_active_events', None):
            self.active_events = self.capture_active_events()
        self.commands.append(command)
        self.prompts.append(kwargs["prompt"])
        self.environments.append(dict(kwargs["env"]))
        for line in self.stdout.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(0, self.stdout, "")


@pytest.fixture
def setup(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "audit.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-audit", conversation_title="Review", single_chat=True,
        trigger_message_id="msg-audit", trigger_create_time="2026-10-04 10:00:00",
        trigger_sender="Derek", trigger_text="Notify applicant",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    candidate = _candidate()
    parent = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(parent.id, candidate.model_dump(mode="json"), owner="consumer")
    context = AuditTurnContext(
        task=AgentTaskContext(
            task_id=task.id, channel=task.channel, conversation_id=task.conversation_id,
            conversation_title=task.conversation_title, single_chat=task.single_chat,
            trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text, trigger_create_time=task.trigger_create_time,
            messages=(), materials=(), prior_receipts=(),
        ),
        proposal_revision=0, operation_id="audit-operation",
        candidate=candidate, candidate_digest=candidate_digest(candidate),
        audit_rules="Check exact audience and content.",
    )
    config = load_runtime_config({
        "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
        **codex_api_env("fallback-test-key"),
    })
    capabilities = frozenset({
        "structured_output", "local_schema_validation", "audit_effect_visibility",
        "reviewed_read_tools", "agent_cli.dws", "task_context",
        "channel:dingtalk", "mcp:agent_cli:reviewed_read",
        "native_cli:reviewed", "native_cli:dws", "mcp:memory_connector:read",
        "role_bound_agent_tools",
    })
    snapshots = {
        route.name: RuntimeCapabilitySnapshot(
            route_name=route.name, capabilities=capabilities, healthy=True,
            checked_at="2026-08-20 00:00:00", expires_at="2099-08-20 00:00:00",
        ) for route in config.routes
    }
    router = AgentRuntimeRouter(routes=config.routes, store=store, snapshots=snapshots)
    return store, task, parent, context, config, router


def _runner(setup, executor):
    store, _task, _parent, _context, config, router = setup
    if isinstance(executor, CapturingExecutor):
        from app.native_trajectory import live_events
        def capture_active_events():
            with store._connect() as db:
                run_id = db.execute(f"select id from ({AGENT_RUN_SQL}) where status='running' order by id desc limit 1").fetchone()[0]
            return live_events(str(store.path.resolve()), run_id) or []
        executor.capture_active_events = capture_active_events
    return AuditAgentRunner(
        store=store, workspace=Path("/workspace"), executor=executor,
        runtime_config=config, runtime_router=router,
        codex_adapter=CodexRuntimeAdapter(Path("/workspace"), config),
    )


@pytest.mark.parametrize("continuable", [False, True, None, "false"])
def test_audit_retry_does_not_resume_explicit_noncontinuable_session(setup, monkeypatch, continuable):
    from app.agent_turn_runner import AgentTurnProcess

    store, task, parent, context, _config, _router = setup
    prior = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=context.operation_id, owner="prior-audit",
    ).run
    store.set_agent_run_session(prior.id, "unusable-session", owner="prior-audit")
    store.fail_agent_run(prior.id, {
        "code": "dependency_read_unavailable", "retryable": True,
        "session_continuable": continuable,
    }, owner="prior-audit")
    captured = {}

    def execute(_self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(AgentTurnProcess, "execute", execute)
    _runner(setup, None).run(task, context, turn_attempt=1, parent_agent_run_id=parent.id)
    assert captured.get("force_new_session") is (continuable is False)
    assert captured["run"].codex_session_id == ("" if continuable is False else "unusable-session")
    assert store.get_agent_run(prior.id).codex_session_id == "unusable-session"


def test_audit_repeated_result_failure_starts_and_binds_fresh_session(setup):
    store, task, parent, context, _config, _router = setup
    old_ids = []
    for attempt in range(2):
        prior = store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.AUDIT,
            proposal_revision=0, turn_attempt=attempt, parent_agent_run_id=parent.id,
            operation_id=context.operation_id, owner="prior-audit",
        ).run
        old_ids.append(prior.id)
        store.set_agent_run_session(prior.id, "old-audit-session", owner="prior-audit")
        store.fail_agent_run(prior.id, {
            "code": "codex_result_invalid", "detail": "same invalid result",
            "retryable": True, "session_continuable": True,
        }, owner="prior-audit")
    executor = CapturingExecutor(_wire())
    result = _runner(setup, executor).run(
        task, context, turn_attempt=2, parent_agent_run_id=parent.id,
    )
    assert result.result.outcome is AuditOutcome.APPROVE
    assert "resume" not in executor.commands[0]
    assert all(store.get_agent_run(i).codex_session_id == "old-audit-session" for i in old_ids)


def test_audit_context_contains_exact_candidate_and_all_review_checks(setup):
    _store, _task, _parent, context, _config, _router = setup
    from app.consumer_agent import audit_developer_instructions
    rendered = audit_developer_instructions(context.audit_rules) + context.render()
    assert context.candidate_digest in rendered
    assert "Exact prepared notice" in rendered
    assert "Check exact audience and content" in rendered
    for phrase in ("business Skill", "applicant or source owner", "technical/provider",
                   "mutually exclusive reusable rule choices", "directly executable option"):
        assert phrase in rendered


def test_human_question_renders_only_reusable_task_class_rule_choices(setup):
    _store, _task, _parent, context, _config, _router = setup
    fact = {"assertion": "Applicant confirmed the current status", "references": ["source:1"]}
    basis = {"verified_facts": [fact], "rule_evidence": [fact],
             "quality_explanation": "No supported reusable rule covers this task class",
             "no_external_action_evidence": [fact], "conclusion": "Ask for a reusable rule"}
    action_plan = context.candidate.proposal.model_dump(mode="json")
    question = ConsumerAgentResult.model_validate({
        **context.candidate.model_dump(mode="json"),
        "outcome": "needs_human", "proposal": None,
        "needs_human_reason": "No current rule covers this class of applicant response",
        "decision_basis": basis,
        "risk": "high", "confidence": 0.49, "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "decision_options": [
            {"key": "send", "label": "Send", "instruction": "Send notice",
             "consequence": "Matching future cases use the reviewed notice", "applies_to": "task_class",
             "plan": action_plan},
            {"key": "stop", "label": "Stop", "instruction": "Do nothing",
             "consequence": "Matching future cases are held for facts", "applies_to": "task_class",
             "terminal_outcome": "skipped",
             "reason": "Applicant already has the information"},
        ],
    })
    rendered = replace(context, candidate=question, candidate_digest=candidate_digest(question)).render()
    document, _ = json.JSONDecoder().raw_decode(rendered.split("Candidate revision\n", 1)[1])
    assert [option["key"] for option in document["candidate"]["decision_options"]] == ["send", "stop"]
    assert all(option["applies_to"] == "task_class" for option in document["candidate"]["decision_options"])
    assert "Exact prepared notice" in rendered
    assert "Applicant already has the information" in rendered
    assert question.requested_input is None


def test_audit_route_requires_role_boundary_and_image_capability(setup):
    _store, _task, _parent, context, _config, _router = setup
    assert AuditAgentRunner._required_capabilities(context) == frozenset({"role_bound_agent_tools"})
    with_image = replace(context, task=replace(context.task, image_paths=("/tmp/audit-image.png",)))
    assert AuditAgentRunner._required_capabilities(with_image) == frozenset({
        "role_bound_agent_tools", "image_input",
    })


def test_review_only_run_preserves_session_and_uses_bound_read_cli(setup):
    store, task, parent, context, _config, _router = setup
    executor = CapturingExecutor(_wire())
    result = _runner(setup, executor).run(
        task, context, turn_attempt=0, parent_agent_run_id=parent.id,
    )
    assert result.result.outcome is AuditOutcome.APPROVE
    saved = store.get_agent_run(result.run_id)
    assert saved.status == "completed" and saved.codex_session_id == "audit-session"
    command = executor.commands[0]
    args_setting = next(value for value in command if value.startswith("mcp_servers.agent_cli.args="))
    assert json.loads(args_setting.split("=", 1)[1]) == [
        "-m", "app.agent_cli", "--role", "audit", "--task-id", str(task.id),
        "--db", str(store.path), "--execution-generation", task.execution_generation,
    ]
    assert "send_approved_dingtalk_message" not in json.dumps(command)
    assert "unsubscribe_email" not in json.dumps(command)
    assert "Execute only the accepted candidate" not in executor.prompts[0]
    assert "agent_cli.read_skill()" in executor.prompts[0]
    snapshot = next(
        event for event in executor.active_events
        if event.get("type") == "runtime.prompt"
    )
    assert snapshot["invocation_facts"]["skill_names"] == []


@pytest.mark.parametrize("outcome", ["return", "reject"])
def test_audit_feedback_is_business_review_without_external_result(setup, outcome):
    _store, task, parent, context, _config, _router = setup
    executor = CapturingExecutor(_wire(outcome=outcome))
    result = _runner(setup, executor).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    assert result.result.outcome.value == outcome
    assert result.result.feedback.requested_revision == "Read the source"
    assert "external_result" not in type(result.result).model_fields


def test_candidate_digest_is_checked_before_claim(setup):
    store, task, parent, context, _config, _router = setup
    changed = replace(context, candidate_digest="0" * 64)
    with pytest.raises(ValueError, match="digest mismatch"):
        _runner(setup, CapturingExecutor("")).run(task, changed, turn_attempt=0, parent_agent_run_id=parent.id)
    assert store.get_agent_run_for_turn(task.id, task.execution_generation, role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=0) is None


def test_audit_wire_binding_is_service_owned_and_unexpected_echo_is_rejected(setup):
    _store, task, parent, context, _config, _router = setup
    result = _runner(setup, CapturingExecutor(_wire())).run(
        task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    assert result.result.candidate_digest == context.candidate_digest
    assert result.result.proposal_revision == context.proposal_revision

    raw_lines = _wire().splitlines()
    event = json.loads(raw_lines[-1])
    output = json.loads(event["item"]["text"])
    output["candidate_digest"] = "0" * 64
    output["proposal_revision"] = 17
    event["item"]["text"] = json.dumps(output)
    raw_lines[-1] = json.dumps(event)
    with pytest.raises(ResultParseError):
        _runner(setup, CapturingExecutor("\n".join(raw_lines))).run(
            task, context, turn_attempt=1, parent_agent_run_id=parent.id)


def test_completed_audit_recovery_uses_the_candidate_bound_parser(setup, monkeypatch):
    from types import SimpleNamespace

    import app.agent_turn_runner as turn_runner
    from app.agent_wire_contracts import parse_audit_agent_wire_result

    _store, _task, _parent, context, _config, _router = setup
    attempt = SimpleNamespace(
        result_schema_id="audit-schema", runtime_kind="codex_cli",
        session_id="audit-session", transcript_start=0, transcript_end=1,
    )
    run = SimpleNamespace(role=AgentRole.AUDIT, proposal_revision=context.proposal_revision)
    monkeypatch.setattr(turn_runner, "read_native_result_stream", lambda *_args: _wire())

    recovered = turn_runner.recover_completed_runtime_domain_result(
        attempt, run, schema_id="audit-schema",
        parse_result=lambda raw: parse_audit_agent_wire_result(
            raw, proposal_revision=context.proposal_revision,
            candidate_digest=context.candidate_digest,
        ),
    )

    assert recovered.proposal_revision == context.proposal_revision
    assert recovered.candidate_digest == context.candidate_digest


def test_false_field_feedback_corrects_audit_without_revising_or_approving_candidate(setup):
    store, task, parent, context, _config, _router = setup
    lines = _wire(outcome="return").splitlines()
    event = json.loads(lines[-1])
    output = json.loads(event["item"]["text"])
    output["feedback"].update(observation_basis="candidate_fields", candidate_field_assertions=[{
        "path": ["proposal", "actions", 0, "target", "open_dingtalk_id"],
        "value": 123, "json_type": "number",
    }])
    event["item"]["text"] = json.dumps(output)
    lines[-1] = json.dumps(event)
    with pytest.raises(ResultParseError, match="Audit candidate field assertion"):
        _runner(setup, CapturingExecutor("\n".join(lines))).run(
            task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    failed = store.get_agent_run_for_turn(task.id, task.execution_generation,
        role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=0)
    assert failed.status == "failed"
    assert json.loads(failed.structured_error_json)["code"] == "codex_result_invalid"
    assert json.loads(store.adopted_candidate_for_consumer_run(parent.id)["candidate_json"]) == context.candidate.model_dump(mode="json")
    executor = CapturingExecutor(_wire(outcome="return"))
    result = _runner(setup, executor).run(task, context, turn_attempt=1,
        parent_agent_run_id=parent.id)
    assert result.result.outcome is AuditOutcome.RETURN
    assert "Audit candidate field assertion" in executor.prompts[0]
    assert context.candidate_digest == result.result.candidate_digest


def test_sensitive_audit_output_is_a_result_failure_not_a_process_outage(setup):
    store, task, parent, context, _config, _router = setup
    lines = _wire(outcome="return").splitlines()
    event = json.loads(lines[-1])
    output = json.loads(event["item"]["text"])
    output["feedback"]["observation"] = "access_token=synthetic-test-secret"
    event["item"]["text"] = json.dumps(output)
    lines[-1] = json.dumps(event)
    executor = CapturingExecutor("\n".join(lines))

    with pytest.raises(ResultParseError, match="agent_result_contains_sensitive_value"):
        _runner(setup, executor).run(
            task, context, turn_attempt=0, parent_agent_run_id=parent.id,
        )

    run = store.get_agent_run_for_turn(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0,
    )
    assert run.status == "failed"
    assert run.final_result_json == ""
    error = json.loads(run.structured_error_json)
    assert error["code"] == "codex_result_invalid"
    assert error["stage"] == "result"
    assert error["detail"] == "agent_result_contains_sensitive_value"


def test_sensitive_recovered_audit_result_is_a_result_failure_not_a_process_outage(
    setup, monkeypatch,
):
    from types import SimpleNamespace

    import app.agent_turn_runner as agent_turn_runner
    from app.audit_agent import parse_audit_agent_wire_result

    store, task, parent, context, config, _router = setup
    route = config.routes[0]
    completed_attempt = SimpleNamespace(
        id=901, status="completed", result_schema_id="stable-schema",
        route_name=route.name, runtime_kind=route.runtime_kind.value,
        session_id="", transcript_start=0, transcript_end=0,
    )
    store.list_agent_runtime_attempts = lambda _run_id: [completed_attempt]
    monkeypatch.setattr(
        agent_turn_runner,
        "hashlib",
        SimpleNamespace(sha256=lambda _value: SimpleNamespace(
            hexdigest=lambda: "stable-schema",
        )),
    )
    recovered = parse_audit_agent_wire_result(
        _wire(outcome="return"), proposal_revision=context.proposal_revision,
        candidate_digest=context.candidate_digest,
    )
    unsafe = recovered.model_copy(update={
        "feedback": recovered.feedback.model_copy(update={
            "observation": "access_token=synthetic-test-secret",
        }),
    })
    monkeypatch.setattr(
        agent_turn_runner,
        "recover_completed_runtime_domain_result",
        lambda *_args, **_kwargs: unsafe,
    )

    with pytest.raises(ResultParseError, match="agent_result_contains_sensitive_value"):
        _runner(setup, CapturingExecutor("")).run(
            task, context, turn_attempt=0, parent_agent_run_id=parent.id,
        )

    failed = store.get_agent_run_for_turn(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0,
    )
    assert failed.status == "failed"
    error = json.loads(failed.structured_error_json)
    assert error["code"] == "codex_result_invalid"
    assert error["stage"] == "result"
    assert error["detail"] == "agent_result_contains_sensitive_value"


def test_audit_parent_must_be_completed_exact_consumer_candidate(setup):
    store, task, _parent, context, _config, _router = setup
    other = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
                                  proposal_revision=0, turn_attempt=1, parent_agent_run_id=None,
                                  operation_id="", owner="other").run
    store.complete_agent_run(other.id, {"outcome": "no_action"}, owner="other")
    with pytest.raises(ValueError, match="parent candidate mismatch"):
        _runner(setup, CapturingExecutor("")).run(task, context, turn_attempt=0, parent_agent_run_id=other.id)


def test_actual_audit_runner_uses_production_review_instructions(setup, monkeypatch):
    from app.agent_context import _AUDIT_AGENT_RULES
    from app.audit_rules import SEED_AUDIT_RULES_TEMPLATE, render_audit_rules
    from app.consumer_agent import audit_developer_instructions

    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(SEED_AUDIT_RULES_TEMPLATE))
    _store, task, parent, _context, _config, _router = setup
    context = replace(_context, audit_rules="")
    executor = CapturingExecutor(_wire())
    _runner(setup, executor).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    command = executor.commands[0]
    setting = next(value for value in command if value.startswith("developer_instructions="))
    actual = json.loads(setting.split("=", 1)[1])
    [snapshot] = [event for event in executor.active_events
                  if event.get("type") == "runtime.prompt"]
    rendered_rules = render_audit_rules(AgentRole.AUDIT)
    assert actual == audit_developer_instructions(rendered_rules, runtime_context="") + "\n\n" + snapshot["runtime_context"]
    assert actual == snapshot["developer_instructions"]
    assert (actual + executor.prompts[0]).count(_AUDIT_AGENT_RULES) == 1
    assert f"## Audit Rules\n{rendered_rules}" in actual
    assert "reusable rule should govern this task class" in rendered_rules
    assert "not ask " in rendered_rules
    assert "to choose the outcome or action for this individual OA" in rendered_rules
    assert "current-instance `needs_human` question" not in rendered_rules
    task_rules = executor.prompts[0].partition("## Audit Rules\n")[2].partition(
        "\n\n## Context Facts"
    )[0]
    assert task_rules == ""
    assert rendered_rules not in executor.prompts[0]
    assert "只读审核" in snapshot["runtime_context"]


@pytest.mark.parametrize(
    ("protocol_source", "protocol_present", "fact_source"),
    (
        ("generated_discovery", False, "frozen_task_skills"),
        ("explicit_custom", True, "explicit_custom"),
    ),
)
def test_audit_task_carries_selected_frozen_skill_discovery_on_every_turn(
    setup, protocol_source, protocol_present, fact_source
):
    import hashlib
    store, task, parent, context, _config, _router = setup
    saved_task_instruction = "Apply this task's exact private review requirement."
    context = replace(
        context,
        task=replace(
            context.task,
            consumer_prompt=saved_task_instruction + "\n\nOLD SELECTED PROTOCOL MUST NOT BE PRELOADED",
            skill_names=("ceo-document-review",),
            skill_protocol_override="OLD SELECTED PROTOCOL MUST NOT BE PRELOADED",
            skill_protocol_source=protocol_source,
        ),
    )
    executor = CapturingExecutor(_wire())
    runner = _runner(setup, executor)
    runner.skill_protocol_override = "OLD SELECTED PROTOCOL MUST NOT BE PRELOADED"
    selected_task = task.model_copy(update={
        "trigger_message_json": json.dumps({
            "raw_payload": {
                "scheduled_consumer": ServiceCommandConsumerContext(
                    scheduled_task_id=11,
                    scheduled_task_run_id=29,
                    prompt="Review the selected document.",
                    skill_names=("ceo-document-review",),
                    skill_protocol="Read the selected frozen Skill.",
                    skill_protocol_source=protocol_source,
                    skill_materials=(ServiceCommandSkillMaterial(
                        name="ceo-document-review", content="FROZEN BODY"
                    ),),
                ).to_payload()
            }
        })
    })

    runner.run(selected_task, context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert executor.prompts[0].count(saved_task_instruction) == 1
    assert "ceo-document-review" in executor.prompts[0]
    assert "agent_cli.read_task_skill(name)" in executor.prompts[0]
    assert executor.prompts[0].count(hashlib.sha256(b"FROZEN BODY").hexdigest()) == 1
    assert "FROZEN BODY" not in executor.prompts[0]
    assert (
        "OLD SELECTED PROTOCOL MUST NOT BE PRELOADED" in executor.prompts[0]
    ) is protocol_present
    developer_setting = next(
        value for value in executor.commands[0] if value.startswith("developer_instructions=")
    )
    assert "ceo-document-review" not in json.loads(developer_setting.split("=", 1)[1])
    [snapshot] = [
        event for event in executor.active_events
        if event.get("type") == "runtime.prompt"
    ]
    task_sections = [
        section for section in snapshot["sections"] if section["placement"] == "task"
    ]
    task_instruction_sections = [section for section in task_sections if section["name"] == "任务专项指令"]
    assert len(task_instruction_sections) == 1
    assert task_instruction_sections[0]["source"] == "已保存任务配置"
    saved_section = "## Scheduled Consumer Prompt\n" + saved_task_instruction
    assert saved_section in executor.prompts[0]
    assert task_instruction_sections[0]["characters"] == len(saved_section)
    assert any(section["name"] == "任务 Skill 入口" for section in task_sections)
    assert snapshot["invocation_facts"]["skill_protocol_source"] == fact_source
    assert snapshot["invocation_facts"]["skill_names"] == ["ceo-document-review"]
    assert snapshot["invocation_facts"]["skill_protocol"] == (
        "OLD SELECTED PROTOCOL MUST NOT BE PRELOADED" if protocol_present else ""
    )


def test_audit_reads_frozen_prepared_parent_when_native_proposal_differs(setup, tmp_path, monkeypatch):
    from app import native_trajectory
    from app.agent_turn_runner import AgentTurnProcess

    store, task, parent, context, _config, _router = setup
    raw = context.candidate.model_copy(deep=True)
    raw.proposal.actions[0].payload['content'] = 'Raw message before service preparation'
    path = tmp_path / 'raw-consumer.jsonl'
    path.write_text(json.dumps({'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': raw.model_dump_json()}
        ]}}) + '\n')
    monkeypatch.setattr(native_trajectory, 'find_codex_session_path', lambda *a, **k: path)
    with store._connect() as db:
        db.execute("update runs set session_id='native-consumer',transcript_end=1 where record_type='agent_runs' and cast(legacy_key as integer)=?", (parent.id,))
    assert json.loads(store.get_agent_run(parent.id).final_result_json) != context.candidate.model_dump(mode='json')
    captured = {}
    monkeypatch.setattr(AgentTurnProcess, 'execute', lambda _self, **kwargs: captured.update(kwargs))
    _runner(setup, None).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    assert 'Exact prepared notice' in captured['prompt']
    assert 'Raw message before service preparation' not in captured['prompt']


def test_audit_builds_native_role_skill_config_once(setup, tmp_path, monkeypatch):
    from tests.support.role_skill_scan import track_role_skill_build, assert_single_role_skill_build

    calls, expected = track_role_skill_build(tmp_path, monkeypatch)
    _store, task, parent, context, _config, _router = setup
    context = replace(context, task=replace(context.task, skill_names=("ceo-document-review",)))
    executor = CapturingExecutor(_wire())
    _runner(setup, executor).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    assert_single_role_skill_build(calls, expected, executor)
