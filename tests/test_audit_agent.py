"""Audit reviews the whole typed candidate without provider writes.

The former Audit-executes tests were removed with that obsolete responsibility;
system execution and receipts are covered by test_system_executor.py.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import AuditOutcome, ConsumerAgentResult
from app.agent_result import ResultParseError
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import AgentRuntimeRouter
from app.audit_agent import AuditAgentRunner
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


def _wire(digest: str, *, outcome: str = "approve", revision: int = 0) -> str:
    result = {
        "outcome": outcome, "summary": "Reviewed whole candidate",
        "proposal_revision": revision, "candidate_digest": digest,
        "evidence_refs": ["source:1"],
        "feedback": ({"rule": "Missing source", "observation": "Unverified fact",
                     "requested_revision": "Read the source"} if outcome in ("return", "reject") else None),
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
    return AuditAgentRunner(
        store=store, workspace=Path("/workspace"), executor=executor,
        runtime_config=config, runtime_router=router,
        codex_adapter=CodexRuntimeAdapter(Path("/workspace"), config),
    )


def test_audit_context_contains_exact_candidate_and_all_review_checks(setup):
    _store, _task, _parent, context, _config, _router = setup
    rendered = context.render()
    assert context.candidate_digest in rendered
    assert "Exact prepared notice" in rendered
    assert "Check exact audience and content" in rendered
    for phrase in ("business Skill", "applicant or source owner", "technical/provider",
                   "meaningfully different", "directly executable option"):
        assert phrase in rendered


def test_human_question_renders_every_bound_branch_and_open_fact_request(setup):
    _store, _task, _parent, context, _config, _router = setup
    fact = {"assertion": "Applicant confirmed the current status", "references": ["source:1"]}
    basis = {"verified_facts": [fact], "rule_evidence": [fact],
             "quality_explanation": "The current instance needs a choice",
             "no_external_action_evidence": [fact], "conclusion": "Ask Derek"}
    action_plan = context.candidate.proposal.model_dump(mode="json")
    question = ConsumerAgentResult.model_validate({
        **context.candidate.model_dump(mode="json"),
        "outcome": "needs_human", "proposal": None,
        "needs_human_reason": "Choose the current applicant response",
        "decision_basis": basis,
        "decision_options": [
            {"key": "send", "label": "Send", "instruction": "Send notice",
             "consequence": "Applicant gets the notice", "plan": action_plan},
            {"key": "stop", "label": "Stop", "instruction": "Do nothing",
             "consequence": "No notice is sent", "terminal_outcome": "skipped",
             "reason": "Applicant already has the information"},
        ],
    })
    rendered = replace(context, candidate=question, candidate_digest=candidate_digest(question)).render()
    assert '"key": "send"' in rendered
    assert '"key": "stop"' in rendered
    assert "Exact prepared notice" in rendered
    assert "Applicant already has the information" in rendered
    open_input = question.model_copy(update={"decision_options": (), "requested_input": "Provide the missing date"})
    assert "Provide the missing date" in replace(context, candidate=open_input).render()


def test_audit_route_requires_role_boundary_and_image_capability(setup):
    _store, _task, _parent, context, _config, _router = setup
    assert AuditAgentRunner._required_capabilities(context) == frozenset({"role_bound_agent_tools"})
    with_image = replace(context, task=replace(context.task, image_paths=("/tmp/audit-image.png",)))
    assert AuditAgentRunner._required_capabilities(with_image) == frozenset({
        "role_bound_agent_tools", "image_input",
    })


def test_review_only_run_preserves_session_and_uses_bound_read_cli(setup):
    store, task, parent, context, _config, _router = setup
    executor = CapturingExecutor(_wire(context.candidate_digest))
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


@pytest.mark.parametrize("outcome", ["return", "reject"])
def test_audit_feedback_is_business_review_without_external_result(setup, outcome):
    _store, task, parent, context, _config, _router = setup
    executor = CapturingExecutor(_wire(context.candidate_digest, outcome=outcome))
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


def test_audit_wire_digest_and_revision_must_match_claimed_candidate(setup):
    _store, task, parent, context, _config, _router = setup
    for turn_attempt, raw in enumerate((_wire("0" * 64), _wire(context.candidate_digest, revision=1))):
        with pytest.raises(ResultParseError):
            _runner(setup, CapturingExecutor(raw)).run(task, context, turn_attempt=turn_attempt, parent_agent_run_id=parent.id)


def test_audit_parent_must_be_completed_exact_consumer_candidate(setup):
    store, task, _parent, context, _config, _router = setup
    other = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
                                  proposal_revision=0, turn_attempt=1, parent_agent_run_id=None,
                                  operation_id="", owner="other").run
    store.complete_agent_run(other.id, {"outcome": "no_action"}, owner="other")
    with pytest.raises(ValueError, match="parent candidate mismatch"):
        _runner(setup, CapturingExecutor("")).run(task, context, turn_attempt=0, parent_agent_run_id=other.id)


def test_actual_audit_runner_uses_production_review_instructions(setup):
    from app.audit_rules import render_audit_rules
    from app.consumer_agent import audit_developer_instructions

    _store, task, parent, _context, _config, _router = setup
    executor = CapturingExecutor(_wire(_context.candidate_digest))
    _runner(setup, executor).run(task, _context, turn_attempt=0, parent_agent_run_id=parent.id)
    command = executor.commands[0]
    setting = next(value for value in command if value.startswith("developer_instructions="))
    actual = json.loads(setting.split("=", 1)[1])
    [snapshot] = [event for event in _store.list_agent_runs_for_task_generation(task.id, task.execution_generation)[-1].tool_events if event.get("type") == "runtime.prompt"]
    assert actual == audit_developer_instructions(render_audit_rules(AgentRole.AUDIT), runtime_context="") + "\n\n" + snapshot["runtime_context"]
    assert actual == snapshot["developer_instructions"]
    assert "只读审核" in snapshot["runtime_context"]
