from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_contracts import AuditAgentResult, AuditOutcome, ConsumerAgentResult
from app.agent_cron.commands import (
    SERVICE_COMMAND_EXECUTION_KIND,
    ServiceCommandRegistry,
)
from app.agent_cron.consumer import (
    SERVICE_COMMAND_FAILED,
    ScheduledAgentConsumer,
    ScheduledTaskTriggerConsumer,
    build_scheduled_orchestrator,
)
from app.agent_cron.context import ScheduledAgentContextBuilder
from app.agent_cron.models import ScheduledTaskSkillRef
from app.agent_orchestrator import AgentOrchestrator
from app.agent_result import AgentError
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.agent_runtime_contracts import LOCAL_SERVICE_RUNTIME_CAPABILITIES
from app.agent_turn_runner import AgentTurnRunResult
from app.dispatcher.adapters import (
    ReplyQueueAdapter, ScheduledExecutionQueueAdapter, ScheduledTaskQueueAdapter,
)
from app.dispatcher.models import ClaimGuard
from app.skill_files import SkillDocument
from app.store import AgentRole, AutoReplyStore

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


class Options:
    def __init__(self, managed, operation, kind=RuntimeKind.CODEX_CLI):
        self.managed, self.operation, self.kind = managed, operation, kind
        self.available = True
        self.managed_available = True
        self.operation_available = True

    def resolve_runtime_route(self, name, **_kwargs):
        if not self.available:
            raise ValueError("runtime unavailable")
        if (
            self.kind is RuntimeKind.FRIDAY_RUNTIME
            and _kwargs.get("required_capabilities")
        ):
            raise ValueError("runtime missing local service capabilities")
        return RuntimeRoute(
            name=name, runtime_kind=self.kind,
            credential_mode=CredentialMode.LOCAL_OAUTH, model="configured",
        )

    def resolve_managed_skill_revision(self, **_kwargs):
        if not self.managed_available:
            raise ValueError("managed revision unavailable")
        return self.managed

    def resolve_operation_skill(self, _name):
        if not self.operation_available:
            raise ValueError("operation Skill unavailable")
        return self.operation


def fixture(
    tmp_path,
    *,
    kind=RuntimeKind.CODEX_CLI,
    runtime_options=None,
    required_runtime_capabilities=(),
):
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    skill = store.create_managed_skill("managed-check", "Managed Check")
    revision = store.create_managed_skill_revision(
        skill.id,
        "---\nname: managed-check\ndescription: exact\nmetadata:\n  managed_by: ceo-agent-service\n---\n\nEXACT MANAGED BODY\n",
        source="settings",
    )
    path = tmp_path / "operation" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("EXACT OPERATION BODY")
    raw = path.read_bytes()
    operation = SkillDocument(
        name="operation-check", description="operation", managed_by=None,
        path=path, content=raw.decode(), sha256=hashlib.sha256(raw).hexdigest(),
        raw_bytes=raw,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = store.create_scheduled_task(
        name="Check", prompt="Only snapshot", cron_expression="0 * * * * *",
        timezone_name="UTC", runtime_id="runtime",
        runtime_options=runtime_options or {"model": "saved", "reasoning_effort": "high"},
        required_runtime_capabilities=required_runtime_capabilities,
        working_directory=str(workspace), enabled=True,
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="managed", skill_name="managed-check",
                managed_skill_id=skill.id, managed_revision_id=revision.id, position=0,
            ),
            ScheduledTaskSkillRef(
                skill_source="operation", skill_name="operation-check", position=1,
            ),
        ), now=NOW,
    )
    run = store.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW, now=NOW
    )
    return store, run, Options(revision, operation, kind)


def claim(adapter, source_id, owner):
    envelope = adapter.claim(
        NOW, owner=owner, owner_pid=42, lease=timedelta(minutes=5)
    )
    assert envelope and envelope.source_id == str(source_id)
    return envelope, ClaimGuard(adapter=adapter, envelope=envelope, owner=owner)


def commands(produce_once=lambda: "produce-once queued=0"):
    return ServiceCommandRegistry({
        "produce-once": produce_once,
        "recover-recent-messages": lambda: "recover-recent-messages queued=0",
        "wechat-produce-once": lambda: "wechat produce-once queued=0",
        "scan-meetings-once": lambda: "scan-meetings-once queued=0",
        "scan-oa-approvals": lambda: "scan-oa-approvals queued=0",
        "scan-work-sources-once": lambda: "scan-work-sources-once queued=0",
        "sync-minutes-once": lambda: "sync-minutes-once queued=0",
    })


def dispatch(store, run, options, *, registry=None):
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, run.id, "trigger")
    ScheduledTaskTriggerConsumer(
        store=store, option_service=options, commands=registry or commands(),
        now=lambda: NOW,
    )(envelope, guard)
    return store.get_scheduled_task_run(run.id)


def command_task_run(store):
    task = store.create_scheduled_task(
        name="Producer", command="produce-once", cron_expression="0 * * * * *",
        timezone_name="UTC", enabled=True, now=NOW,
    )
    return store.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW, now=NOW
    )


def error_rows(store):
    with store._connect() as db:
        return [dict(row) for row in db.execute(
            "select conversation_id, message_id, kind, detail from errors order by id"
        ).fetchall()]


def audit(outcome, revision):
    return AuditAgentResult.model_validate({
        "outcome": outcome, "summary": outcome, "proposal_revision": revision,
        "feedback": ({"rule": "evidence", "observation": "revise",
                      "requested_revision": "replace"}
                     if outcome == "feedback_provided" else None),
        "external_result": ({"operation_id": "placeholder",
                             "live_result_reference": {"status": "done"}}
                            if outcome == "executed" else None),
        "error": {"code": "", "retryable": False},
        # needs_human must classify as NEEDS_HUMAN, which the decision quality
        # gate reaches through high risk plus low confidence.  Leaving
        # information_completeness low instead would classify it as ASK_BACK.
        **({"risk": "high", "confidence": 0.1,
            "rule_coverage": 1.0, "information_completeness": 1.0}
           if outcome == "needs_human" else
           {"risk": "low", "confidence": 1.0,
            "rule_coverage": 1.0, "information_completeness": 1.0}),
    })


def proposal(label):
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": label,
        "proposal": {"objective": label, "sourced_facts": [], "authored_judgment": "",
                     "actions": [{"description": label,
                                  "action_identity": "stable-action",
                                  "capability": "agent_cli.dws",
                                  "operation": "chat message send",
                                  "target": {"group": "cron"},
                                  "payload": {"argv": ["dws", "chat", "message", "send"]}}]},
        "error": {"code": "", "retryable": False},
        "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
    })


class Consumer:
    def __init__(self, store, *results): self.store, self.results = store, deque(results)
    def run(self, task, _context, *, proposal_revision, parent_agent_run_id, feedback=None):
        claimed = self.store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision, turn_attempt=0,
            parent_agent_run_id=parent_agent_run_id, operation_id="", owner="consumer",
        )
        result = self.results.popleft()
        self.store.complete_agent_run(claimed.run.id, result.model_dump(mode="json"), owner="consumer")
        return AgentTurnRunResult(claimed.run.id, result, 0, 1)


class Audit:
    def __init__(self, store, *results): self.store, self.results = store, deque(results)
    def run(self, task, context, *, turn_attempt, parent_agent_run_id):
        claimed = self.store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision, turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id, operation_id=context.operation_id,
            owner="audit",
        )
        result = self.results.popleft()
        if result.outcome is AuditOutcome.EXECUTED:
            result = result.model_copy(update={"external_result": result.external_result.model_copy(
                update={"operation_id": context.operation_id})})
        self.store.complete_agent_run(claimed.run.id, result.model_dump(mode="json"), owner="audit")
        return AgentTurnRunResult(claimed.run.id, result, 0, 1)


def test_context_uses_exact_snapshot_and_rejects_unsupported_thinking(tmp_path):
    _store, run, options = fixture(tmp_path)
    built = ScheduledAgentContextBuilder(options).build(run, reply_task_id=7)
    assert built.context.trigger_text == "Only snapshot"
    assert built.context.messages == built.context.materials == ()
    assert built.route.model == "saved" and built.reasoning_effort == "high"
    assert "EXACT MANAGED BODY" in built.skill_protocol
    assert options.operation.sha256 in built.skill_protocol
    for kind in (RuntimeKind.CLAUDE_CLI, RuntimeKind.FRIDAY_RUNTIME):
        _store, run, options = fixture(
            tmp_path / kind.value, kind=kind,
            runtime_options={"model": "saved", "thinking": "high"},
        )
        with pytest.raises(ValueError, match="does not support reasoning effort"):
            ScheduledAgentContextBuilder(options).build(run, reply_task_id=7)


def test_context_rejects_remote_runtime_for_local_service_task(tmp_path):
    _store, run, options = fixture(
        tmp_path,
        kind=RuntimeKind.FRIDAY_RUNTIME,
        runtime_options={"model": "saved"},
        required_runtime_capabilities=tuple(
            sorted(LOCAL_SERVICE_RUNTIME_CAPABILITIES)
        ),
    )

    with pytest.raises(ValueError, match="missing local service capabilities"):
        ScheduledAgentContextBuilder(options).build(run, reply_task_id=7)


@pytest.mark.parametrize(
    ("dry_run", "ambient", "expected"),
    (
        (True, "0", "1"),
        (False, "1", "0"),
    ),
)
def test_scheduled_orchestrator_pins_parent_execution_mode_for_both_roles(
    tmp_path, monkeypatch, dry_run, ambient, expected
):
    monkeypatch.setenv("CEO_DRY_RUN", ambient)
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", ambient)
    store, run, options = fixture(tmp_path)
    built = ScheduledAgentContextBuilder(options).build(run, reply_task_id=7)
    runtime_config = load_runtime_config(
        {"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"}
    )

    orchestrator = build_scheduled_orchestrator(
        store=store,
        built=built,
        runtime_config=runtime_config,
        dry_run=dry_run,
    )

    expected_environment = {
        "CEO_DRY_RUN": expected,
        "CEO_NOT_SEND_MESSAGE": expected,
    }
    assert orchestrator.consumer.execution_environment == expected_environment
    assert orchestrator.audit.execution_environment == expected_environment


def test_trigger_dispatches_once_and_generic_reply_adapter_excludes_it(tmp_path):
    store, run, options = fixture(tmp_path)
    persisted = dispatch(store, run, options)
    assert persisted.dispatch_status == "dispatched"
    task = store.get_reply_task(int(persisted.execution_id))
    assert task.channel == "scheduled" and task.status == "pending"
    assert ReplyQueueAdapter(store).claim(
        NOW, owner="generic", owner_pid=42, lease=timedelta(minutes=5)
    ) is None
    claimed = ScheduledExecutionQueueAdapter(store).claim(
        NOW, owner="execution", owner_pid=42, lease=timedelta(minutes=5)
    )
    assert claimed and claimed.source_id == persisted.execution_id

    with store._connect() as db:
        visible = db.execute(
            "select runs.dispatch_status from reply_tasks tasks "
            "join scheduled_task_runs runs on runs.execution_kind='reply_task' "
            "and runs.execution_id=cast(tasks.id as text) "
            "where tasks.channel='scheduled'"
        ).fetchall()
    assert visible and {row["dispatch_status"] for row in visible} == {"dispatched"}


def test_atomic_dispatch_rolls_back_source_link_run_and_ledger_on_interrupt(tmp_path):
    store, run, options = fixture(tmp_path)
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, _guard = claim(adapter, run.id, "trigger")
    built = ScheduledAgentContextBuilder(options).build(run, reply_task_id=0)
    with store._connect() as db:
        db.execute(
            "create trigger abort_scheduled_dispatch before update of dispatch_status "
            "on scheduled_task_runs when new.dispatch_status='dispatched' "
            "begin select raise(abort, 'interrupted'); end"
        )
    with pytest.raises(Exception, match="interrupted"):
        store.dispatch_scheduled_task_reply_execution(
            run.id, owner="trigger", claim_generation=envelope.generation,
            execution_context_json=built.to_execution_json(), now=NOW,
        )
    persisted = store.get_scheduled_task_run(run.id)
    assert persisted.dispatch_status == "pending" and persisted.execution_id == ""
    with store._connect() as db:
        assert db.execute(
            "select count(*) from reply_tasks where channel='scheduled'"
        ).fetchone()[0] == 0
        ledger = db.execute(
            "select terminal_at from dispatcher_claim_leases "
            "where adapter_name='scheduled' and source_id=?", (str(run.id),)
        ).fetchone()
    assert ledger["terminal_at"] == ""
    with store._connect() as db:
        db.execute("drop trigger abort_scheduled_dispatch")
    finished, source = store.dispatch_scheduled_task_reply_execution(
        run.id, owner="trigger", claim_generation=envelope.generation,
        execution_context_json=built.to_execution_json(), now=NOW,
    )
    assert finished.dispatch_status == "dispatched"
    assert finished.execution_id == str(source.id)
    assert store.list_reply_tasks(channel="scheduled") == [source]


def test_command_trigger_runs_in_process_and_never_creates_an_agent_input(tmp_path):
    store = AutoReplyStore(tmp_path / "command.sqlite3")
    run = command_task_run(store)
    calls = []

    def produce_once():
        calls.append("produce-once")
        return "produce-once queued=2"

    persisted = dispatch(store, run, None, registry=commands(produce_once))

    assert calls == ["produce-once"]
    assert persisted.dispatch_status == "dispatched"
    assert persisted.execution_kind == SERVICE_COMMAND_EXECUTION_KIND
    assert persisted.execution_id == "produce-once"
    assert persisted.dispatched_at == NOW
    assert store.list_reply_tasks(channel="scheduled") == []
    assert ScheduledExecutionQueueAdapter(store).claim(
        NOW, owner="execution", owner_pid=42, lease=timedelta(minutes=5)
    ) is None
    assert error_rows(store) == []
    assert ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False).claim(
        NOW, owner="again", owner_pid=42, lease=timedelta(minutes=5)
    ) is None


def test_command_trigger_failure_ends_the_trigger_and_raises_attention(tmp_path):
    store = AutoReplyStore(tmp_path / "command-failed.sqlite3")
    run = command_task_run(store)

    def produce_once():
        raise RuntimeError("dws unreachable")

    persisted = dispatch(store, run, None, registry=commands(produce_once))

    assert persisted.dispatch_status == "failed"
    assert persisted.skip_or_error_reason == (
        f"{SERVICE_COMMAND_FAILED}: dws unreachable"
    )
    assert persisted.execution_kind == "" and persisted.execution_id == ""
    assert store.list_reply_tasks(channel="scheduled") == []
    rows = error_rows(store)
    assert [row["kind"] for row in rows] == [SERVICE_COMMAND_FAILED]
    assert rows[0]["conversation_id"] == f"scheduled-task:{run.scheduled_task_id}"
    assert rows[0]["message_id"] == run.event_id
    assert "produce-once" in rows[0]["detail"] and "dws unreachable" in rows[0]["detail"]


def test_command_registry_rejects_bindings_that_do_not_match_the_catalog():
    with pytest.raises(ValueError, match="match the catalog exactly"):
        ServiceCommandRegistry({})
    with pytest.raises(ValueError, match="match the catalog exactly"):
        ServiceCommandRegistry({"produce-once": lambda: ""})
    with pytest.raises(ValueError, match="match the catalog exactly"):
        ServiceCommandRegistry({
            "produce-once": lambda: "", "wechat-produce-once": lambda: "",
            "extra": lambda: "",
        })
    with pytest.raises(ValueError, match="service_command_not_registered"):
        commands().run("unknown-service-command")


def test_preflight_loss_skips_before_source_or_agent_fact(tmp_path):
    store, run, options = fixture(tmp_path)
    options.available = False
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, run.id, "trigger")
    ScheduledTaskTriggerConsumer(
        store=store, option_service=options, commands=commands(), now=lambda: NOW
    )(envelope, guard)
    persisted = store.get_scheduled_task_run(run.id)
    assert persisted.dispatch_status == "skipped"
    assert persisted.execution_id == ""
    assert store.list_reply_tasks(channel="scheduled") == []
    assert store.list_agent_runs_for_task_generation(1, "scheduled-run-1") == []


def test_execution_uses_persisted_preflight_context_after_current_options_change(tmp_path):
    store, run, options = fixture(tmp_path)
    persisted = dispatch(store, run, options)
    task_id = int(persisted.execution_id)
    options.operation.path.write_text("CHANGED AFTER DISPATCH")
    observed = []

    class Orchestrator:
        def process(self, task, context, *, refresh_context):
            observed.append((context.trigger_text, refresh_context().trigger_text))
            claimed = store.claim_agent_run(
                task.id, task.execution_generation, role=AgentRole.AUDIT,
                proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
                operation_id="persisted-context", owner="fake",
            ).run
            result = audit("executed", 0)
            result = result.model_copy(update={"external_result": result.external_result.model_copy(
                update={"operation_id": "persisted-context"})})
            store.complete_agent_run(claimed.id, result.model_dump(mode="json"), owner="fake")
            return SimpleNamespace(status="executed", final_run_id=claimed.id,
                summary="done", error=AgentError(code="", retryable=False), audit_result=result)

    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, task_id, "execution")
    consumer = ScheduledAgentConsumer(
        store=store, option_service=options,
        orchestrator_factory=lambda built: (
            observed.append(built.skill_protocol) or Orchestrator()
        ), now=lambda: NOW,
    )
    consumer(envelope, guard)
    assert "EXACT OPERATION BODY" in observed[0]
    assert "CHANGED AFTER DISPATCH" not in observed[0]
    assert observed[1] == ("Only snapshot", "Only snapshot")


@pytest.mark.parametrize(
    "loss_kind",
    [
        "runtime_unhealthy",
        "managed_missing",
        "managed_disabled",
        "workspace_deleted",
        "operation_uninstalled",
    ],
)
def test_execution_availability_loss_is_skipped_without_agent_run(tmp_path, loss_kind):
    store, run, options = fixture(tmp_path)
    task_id = int(dispatch(store, run, options).execution_id)
    if loss_kind == "runtime_unhealthy":
        options.available = False
    elif loss_kind in {"managed_missing", "managed_disabled"}:
        options.managed_available = False
    elif loss_kind == "workspace_deleted":
        Path(store.get_scheduled_task_run(run.id).snapshot.working_directory).rmdir()
    else:
        options.operation_available = False
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, task_id, "execution")

    ScheduledAgentConsumer(
        store=store, option_service=options,
        orchestrator_factory=lambda _built: (_ for _ in ()).throw(
            AssertionError("Agent must not start")
        ), now=lambda: NOW,
    )(envelope, guard)

    task = store.get_reply_task(task_id)
    attempt = store.get_latest_reply_attempt_for_trigger(
        task.conversation_id, task.trigger_message_id
    )
    assert task.status == "done"
    assert attempt is not None and attempt.send_status == "skipped"
    assert store.list_agent_runs_for_task_generation(
        task_id, task.execution_generation
    ) == []
    assert store.get_scheduled_task_run(run.id).dispatch_status == "dispatched"
    assert store.list_errors()
    with store._connect() as db:
        ledger = db.execute(
            "select terminal_at from dispatcher_claim_leases "
            "where adapter_name='scheduled_execution' and source_id=?",
            (str(task_id),),
        ).fetchone()
    assert ledger is not None and ledger["terminal_at"]


def test_provider_failure_only_fails_execution_fact(tmp_path):
    store, run, options = fixture(tmp_path)
    task_id = int(dispatch(store, run, options).execution_id)
    task = store.get_reply_task(task_id)
    claimed = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="provider-failure", owner="fake",
    ).run
    error = AgentError(code="provider_failed", retryable=False)
    store.fail_agent_run(claimed.id, error.model_dump(mode="json"), owner="fake")
    result = SimpleNamespace(
        status="failed_terminal", final_run_id=claimed.id, summary="provider failed",
        error=error, audit_result=None,
    )
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, task_id, "execution")
    ScheduledAgentConsumer(
        store=store, option_service=options,
        orchestrator_factory=lambda _built: SimpleNamespace(
            process=lambda *_args, **_kwargs: result
        ), now=lambda: NOW,
    )(envelope, guard)
    assert store.get_reply_task(task_id).status == "failed"
    assert store.get_scheduled_task_run(run.id).dispatch_status == "dispatched"


def test_scheduled_skip_rejects_stale_execution_claim(tmp_path):
    store, run, options = fixture(tmp_path)
    task_id = int(dispatch(store, run, options).execution_id)
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, _guard = claim(adapter, task_id, "execution")
    task = store.get_reply_task(task_id)
    with pytest.raises(ValueError, match="claim is no longer current"):
        store.skip_scheduled_reply_task(
            task_id, "unavailable",
            expected_execution_generation=task.execution_generation,
            dispatcher_owner="execution",
            dispatcher_generation=envelope.generation + 1,
            now=NOW,
        )
    assert store.get_reply_task(task_id).status == "processing"


def test_retry_reclaims_same_execution_source(tmp_path):
    store, run, options = fixture(tmp_path)
    execution_id = dispatch(store, run, options).execution_id
    calls = 0
    class Orchestrator:
        def process(self, task, _context, *, refresh_context):
            nonlocal calls
            calls += 1
            refresh_context()
            if calls == 1:
                return SimpleNamespace(status="failed_retryable", final_run_id=0,
                    summary="retry", error=AgentError(code="temporary", retryable=True),
                    audit_result=None)
            run_claim = store.claim_agent_run(
                task.id, task.execution_generation, role=AgentRole.AUDIT,
                proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
                operation_id="stable-operation", owner="fake",
            ).run
            result = audit("executed", 0).model_copy(update={"external_result":
                audit("executed", 0).external_result.model_copy(update={"operation_id": "stable-operation"})})
            store.complete_agent_run(run_claim.id, result.model_dump(mode="json"), owner="fake")
            return SimpleNamespace(status="executed", final_run_id=run_claim.id,
                summary="done", error=AgentError(code="", retryable=False), audit_result=result)
    consumer = ScheduledAgentConsumer(
        store=store, option_service=options,
        orchestrator_factory=lambda _built: Orchestrator(), now=lambda: NOW,
    )
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, int(execution_id), "first")
    consumer(envelope, guard)
    guard.complete(NOW)
    envelope, guard = claim(adapter, int(execution_id), "second")
    consumer(envelope, guard)
    assert store.get_reply_task(int(execution_id)).status == "done"
    assert store.get_scheduled_task_run(run.id).execution_id == execution_id


def test_real_orchestrator_preserves_feedback_revision_chain(tmp_path):
    store, run, options = fixture(tmp_path)
    execution_id = int(dispatch(store, run, options).execution_id)
    orchestrator = AgentOrchestrator(
        store=store, consumer=Consumer(store, proposal("R0"), proposal("R1")),
        audit=Audit(store, audit("feedback_provided", 0), audit("executed", 1)),
    )
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, execution_id, "execution")
    ScheduledAgentConsumer(
        store=store, option_service=options,
        orchestrator_factory=lambda _built: orchestrator, now=lambda: NOW,
    )(envelope, guard)
    task = store.get_reply_task(execution_id)
    runs = store.list_agent_runs_for_task_generation(execution_id, task.execution_generation)
    assert [(r.role.value, r.proposal_revision) for r in runs] == [
        ("consumer", 0), ("audit", 0), ("consumer", 1), ("audit", 1)
    ]
    assert [r.parent_agent_run_id for r in runs] == [None, runs[0].id, runs[1].id, runs[2].id]
    identities = [json.loads(r.final_result_json)["proposal"]["actions"][0]["action_identity"]
                  for r in (runs[0], runs[2])]
    assert identities == ["stable-action", "stable-action"]
    assert task.status == "done"
    assert store.get_scheduled_task_run(run.id).dispatch_status == "dispatched"
