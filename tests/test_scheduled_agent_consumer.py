from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.agent_contracts import AuditAgentResult, AuditOutcome, ConsumerAgentResult
from app.agent_cron.consumer import ScheduledAgentConsumer, ScheduledTaskTriggerConsumer
from app.agent_cron.context import ScheduledAgentContextBuilder
from app.agent_cron.models import ScheduledTaskSkillRef
from app.agent_orchestrator import AgentOrchestrator
from app.agent_result import AgentError
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
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

    def resolve_runtime_route(self, name):
        if not self.available:
            raise ValueError("runtime unavailable")
        return RuntimeRoute(
            name=name, runtime_kind=self.kind,
            credential_mode=CredentialMode.LOCAL_OAUTH, model="configured",
        )

    def resolve_managed_skill_revision(self, **_kwargs): return self.managed
    def resolve_operation_skill(self, _name): return self.operation


def fixture(tmp_path, *, kind=RuntimeKind.CODEX_CLI, runtime_options=None):
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
    task = store.create_scheduled_task(
        name="Check", prompt="Only snapshot", cron_expression="0 * * * * *",
        timezone_name="UTC", runtime_id="runtime",
        runtime_options=runtime_options or {"model": "saved", "reasoning_effort": "high"},
        working_directory=str(tmp_path), enabled=True,
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


def dispatch(store, run, options):
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, run.id, "trigger")
    ScheduledTaskTriggerConsumer(store=store, option_service=options, now=lambda: NOW)(
        envelope, guard
    )
    return store.get_scheduled_task_run(run.id)


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
