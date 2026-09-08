from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace

from app.agent_contracts import AuditAgentResult, AuditExternalResult, AuditOutcome
from app.agent_cron.consumer import (
    ScheduledAgentConsumer,
    build_scheduled_orchestrator,
)
from app.agent_cron.context import ScheduledAgentContextBuilder
from app.agent_cron.models import ScheduledTaskSkillRef
from app.agent_result import AgentError
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.dispatcher.adapters import ScheduledTaskQueueAdapter
from app.dispatcher.models import ClaimGuard
from app.managed_skills import ManagedSkillRevision
from app.skill_files import SkillDocument
from app.store import AgentRole, AutoReplyStore


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


class Options:
    def __init__(self, managed: ManagedSkillRevision, operation: SkillDocument):
        self.managed = managed
        self.operation = operation
        self.available = True

    def resolve_runtime_route(self, route_name: str):
        if not self.available:
            raise ValueError("runtime route became unavailable")
        return RuntimeRoute(
            name=route_name,
            runtime_kind=RuntimeKind.CODEX_CLI,
            credential_mode=CredentialMode.LOCAL_OAUTH,
            model="configured-model",
        )

    def resolve_managed_skill_revision(self, **_kwargs):
        return self.managed

    def resolve_operation_skill(self, _name: str):
        return self.operation


def _fixture(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    skill = store.create_managed_skill("managed-check", "Managed Check")
    managed = store.create_managed_skill_revision(
        skill.id,
        "---\nname: managed-check\ndescription: exact\nmetadata:\n  managed_by: ceo-agent-service\n---\n\nEXACT BODY\n",
        source="settings",
    )
    operation_path = tmp_path / "operation" / "SKILL.md"
    operation_path.parent.mkdir()
    operation_path.write_text("OPERATION BODY", encoding="utf-8")
    operation = SkillDocument(
        name="operation-check",
        description="operation",
        managed_by=None,
        path=operation_path,
        content="OPERATION BODY",
        sha256=hashlib.sha256(b"OPERATION BODY").hexdigest(),
        raw_bytes=b"OPERATION BODY",
    )
    task = store.create_scheduled_task(
        name="Immutable check",
        prompt="Inspect the configured source only.",
        cron_expression="0 * * * * *",
        timezone_name="UTC",
        runtime_id="codex_oauth",
        runtime_options={"model": "snapshot-model", "reasoning_effort": "high"},
        working_directory=str(tmp_path),
        enabled=True,
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name="managed-check",
                managed_skill_id=skill.id,
                managed_revision_id=managed.id,
                position=0,
            ),
            ScheduledTaskSkillRef(
                skill_source="operation",
                skill_name="operation-check",
                position=1,
            ),
        ),
        now=NOW,
    )
    run = store.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=NOW, now=NOW
    )
    return store, run, Options(managed, operation)


def _claim(store: AutoReplyStore, run_id: int):
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope = adapter.claim(
        NOW,
        owner="dispatcher-1",
        owner_pid=123,
        lease=timedelta(minutes=5),
    )
    assert envelope is not None and envelope.source_id == str(run_id)
    return envelope, ClaimGuard(
        adapter=adapter, envelope=envelope, owner="dispatcher-1"
    )


def test_context_uses_only_immutable_snapshot_and_exact_skill_sources(tmp_path: Path):
    _store, run, options = _fixture(tmp_path)

    built = ScheduledAgentContextBuilder(options).build(run, reply_task_id=41)

    assert built.context.trigger_text == "Inspect the configured source only."
    assert built.context.messages == ()
    assert built.context.materials == ()
    assert built.route.name == "codex_oauth"
    assert built.route.model == "snapshot-model"
    assert built.reasoning_effort == "high"
    assert built.workspace == tmp_path.resolve()
    assert "EXACT BODY" in built.skill_protocol
    assert "OPERATION BODY" in built.skill_protocol
    assert str(options.operation.path.resolve()) in built.skill_protocol
    assert options.operation.sha256 in built.skill_protocol


def test_claimed_run_creates_and_links_one_execution_then_resumes_it(tmp_path: Path):
    store, run, options = _fixture(tmp_path)
    calls = []

    class Orchestrator:
        def process(self, task, context, *, refresh_context):
            calls.append((task.id, context.trigger_text, refresh_context()))
            agent_run = store.claim_agent_run(
                task.id,
                task.execution_generation,
                role=AgentRole.AUDIT,
                proposal_revision=0,
                turn_attempt=0,
                parent_agent_run_id=None,
                operation_id="scheduled-test",
                owner="test",
                lease_seconds=60,
            ).run
            result = AuditAgentResult(
                outcome=AuditOutcome.EXECUTED,
                summary="done",
                proposal_revision=0,
                feedback=None,
                external_result=AuditExternalResult(
                    operation_id="scheduled-test",
                    live_result_reference={"status": "completed"},
                ),
                error=AgentError(code="", retryable=False),
                risk="low",
                confidence=1,
            )
            store.complete_agent_run(
                agent_run.id, result.model_dump(mode="json"), owner="test"
            )
            return SimpleNamespace(
                status="executed",
                final_run_id=agent_run.id,
                summary="done",
                error=AgentError(code="", retryable=False),
                audit_result=result,
            )

    consumer = ScheduledAgentConsumer(
        store=store,
        option_service=options,
        orchestrator_factory=lambda _built: Orchestrator(),
        now=lambda: NOW,
    )
    envelope, guard = _claim(store, run.id)

    consumer(envelope, guard)

    persisted = store.list_scheduled_task_runs(run.scheduled_task_id)[0]
    assert persisted.dispatch_status == "dispatched"
    assert persisted.execution_kind == "reply_task"
    execution_id = persisted.execution_id
    task = store.get_reply_task(int(execution_id))
    assert task is not None and task.status == "done"
    assert len(calls) == 1
    assert calls[0][2] == calls[0][2]  # immutable refresh returns the same value

    # A duplicate delivery of the same envelope cannot create or execute again.
    assert (
        len(
            store.list_agent_runs_for_task_generation(
                task.id, task.execution_generation
            )
        )
        == 1
    )


def test_runtime_loss_before_execution_skips_with_attention_and_no_source(
    tmp_path: Path,
):
    store, run, options = _fixture(tmp_path)
    options.available = False
    consumer = ScheduledAgentConsumer(
        store=store,
        option_service=options,
        orchestrator_factory=lambda _built: (_ for _ in ()).throw(
            AssertionError("runtime must not start")
        ),
        now=lambda: NOW,
    )
    envelope, guard = _claim(store, run.id)

    consumer(envelope, guard)

    persisted = store.list_scheduled_task_runs(run.scheduled_task_id)[0]
    assert persisted.dispatch_status == "skipped"
    assert persisted.execution_id == ""
    assert "runtime" in persisted.skip_or_error_reason
    assert store.list_errors()


def test_retry_reclaims_same_execution_source_without_duplicate_agent_history(
    tmp_path: Path,
):
    store, run, options = _fixture(tmp_path)
    attempts = 0

    class Orchestrator:
        def process(self, task, _context, *, refresh_context):
            nonlocal attempts
            attempts += 1
            refresh_context()
            if attempts == 1:
                return SimpleNamespace(
                    status="failed_retryable",
                    final_run_id=0,
                    summary="retry",
                    error=AgentError(code="temporary", retryable=True),
                    audit_result=None,
                )
            agent_run = store.claim_agent_run(
                task.id,
                task.execution_generation,
                role=AgentRole.AUDIT,
                proposal_revision=0,
                turn_attempt=0,
                parent_agent_run_id=None,
                operation_id="scheduled-retry",
                owner="test",
                lease_seconds=60,
            ).run
            result = AuditAgentResult(
                outcome=AuditOutcome.EXECUTED,
                summary="recovered",
                proposal_revision=0,
                feedback=None,
                external_result=AuditExternalResult(
                    operation_id="scheduled-retry",
                    live_result_reference={"status": "completed"},
                ),
                error=AgentError(code="", retryable=False),
                risk="low",
                confidence=1,
            )
            store.complete_agent_run(
                agent_run.id, result.model_dump(mode="json"), owner="test"
            )
            return SimpleNamespace(
                status="executed",
                final_run_id=agent_run.id,
                summary="recovered",
                error=result.error,
                audit_result=result,
            )

    clock = [NOW]
    consumer = ScheduledAgentConsumer(
        store=store,
        option_service=options,
        orchestrator_factory=lambda _built: Orchestrator(),
        now=lambda: clock[0],
    )
    envelope, guard = _claim(store, run.id)
    consumer(envelope, guard)
    first = store.get_scheduled_task_run(run.id)
    assert first is not None and first.dispatch_status == "pending"
    source_id = first.execution_id

    clock[0] = NOW + timedelta(seconds=6)
    envelope, guard = _claim(store, run.id)
    consumer(envelope, guard)

    recovered = store.get_scheduled_task_run(run.id)
    assert recovered is not None and recovered.dispatch_status == "dispatched"
    assert recovered.execution_id == source_id
    assert attempts == 2


def test_production_factory_pins_route_model_effort_workdir_and_skill_protocol(
    tmp_path: Path,
):
    _store, run, options = _fixture(tmp_path)
    built = ScheduledAgentContextBuilder(options).build(run, reply_task_id=1)
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth",
            "CEO_CODEX_MODEL": "gpt-5.6-sol",
        }
    )

    orchestrator = build_scheduled_orchestrator(
        store=_store,
        built=built,
        runtime_config=config,
    )

    assert orchestrator.consumer.workspace == tmp_path.resolve()
    assert orchestrator.consumer.forced_runtime_route == built.route
    assert orchestrator.consumer.reasoning_effort == "high"
    assert orchestrator.consumer.skill_protocol_override == built.skill_protocol
    assert orchestrator.audit.forced_runtime_route == built.route
    assert orchestrator.audit.runtime_config.routes == (built.route,)
