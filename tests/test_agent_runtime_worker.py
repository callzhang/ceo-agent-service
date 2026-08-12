import json
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from app.agent_context import AgentTaskContext
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_orchestrator import AgentOrchestrator, OrchestrationResult
from app.agent_result import AgentError, SideEffectState
from app.audit_agent import AuditAgentRunner
from app.channel_gate import ChannelGateResult, ChannelGateState
from app.consumer_agent import ConsumerAgentRunner
from app.dingtalk_models import DingTalkMessage
from app.native_cli_metadata import describe_native_command
from app.store import AgentRole, AutoReplyStore
from app.process_runner import ProcessRunResult
from app.worker import ORCHESTRATION_ATTEMPT_STATUS, DingTalkAutoReplyWorker


NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)


class ScriptOutcome(StrEnum):
    COMPLETED = "completed"
    NO_ACTION = "no_action"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class ScriptResult(BaseModel):
    outcome: ScriptOutcome
    summary: str
    error: AgentError = Field(default_factory=AgentError)
    oa_action_receipt: object | None = None


def test_worker_orchestration_status_mapping_is_exact():
    assert ORCHESTRATION_ATTEMPT_STATUS == {
        "executed": ("completed", "done"),
        "no_action": ("skipped", "done"),
        "needs_human": ("needs_human", "done"),
        "failed_retryable": ("failed", "pending"),
        "failed_terminal": ("failed", "failed"),
        "unknown": ("pending_reconciliation", "pending"),
    }


class NoActionOrchestrator:
    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store
        self.calls: list[tuple[object, AgentTaskContext]] = []

    def process(self, task, context, *, refresh_context) -> OrchestrationResult:
        self.calls.append((task, context))
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner="worker-orchestrator-test",
        )
        result = ConsumerAgentResult.model_validate(
            {
                "outcome": "no_action",
                "summary": "No external action is required.",
                "proposal": None,
                "error": {
                    "code": "",
                    "retryable": False,
                    "authorization_required": False,
                },
            }
        )
        self.store.complete_agent_run(
            claim.run.id,
            result.model_dump(mode="json"),
            owner="worker-orchestrator-test",
        )
        return OrchestrationResult(
            status="no_action",
            final_run_id=claim.run.id,
            final_role=AgentRole.CONSUMER,
            summary=result.summary,
            error=result.error,
            feedback_cycles=0,
            consumer_result=result,
        )


class UnknownEffectOrchestrator:
    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def process(self, task, context, *, refresh_context) -> OrchestrationResult:
        del context, refresh_context
        claim = _claim_audit_run(
            self.store,
            task.id,
            task.execution_generation,
            owner="unknown-audit",
        )
        assert claim.claimed
        self.store.set_agent_run_session(
            claim.run.id,
            "unknown-audit-session",
            owner="unknown-audit",
        )
        self.store.append_agent_run_event(
            claim.run.id,
            _persisted_effect_evidence("unknown-write", "started"),
            owner="unknown-audit",
        )
        run = self.store.mark_agent_run_unknown(
            claim.run.id,
            {"code": "agent_side_effect_unknown", "retryable": False},
            owner="unknown-audit",
        )
        result = AuditAgentResult.model_validate(
            {
                "outcome": "unknown",
                "summary": "The external effect requires reconciliation.",
                "proposal_revision": 0,
                "side_effect_state": "unknown",
                "feedback": None,
                "external_result": None,
                "error": {
                    "code": "agent_side_effect_unknown",
                    "retryable": False,
                    "authorization_required": False,
                },
            }
        )
        return OrchestrationResult(
            status="unknown",
            final_run_id=run.id,
            final_role=AgentRole.AUDIT,
            summary=result.summary,
            error=result.error,
            feedback_cycles=0,
            audit_result=result,
        )


class PoisonedSessionFailureOrchestrator:
    def __init__(self, store: AutoReplyStore, role: AgentRole) -> None:
        self.store = store
        self.role = role
        self.run_id = 0
        self.observed_resume_sessions: list[str] = []

    def process(self, task, context, *, refresh_context) -> OrchestrationResult:
        del context, refresh_context
        if self.run_id == 0:
            parent_id = None
            proposal_revision = 0
            turn_attempt = 0
            operation_id = ""
            conversation_session = "poisoned-consumer-session"
            if self.role is AgentRole.AUDIT:
                consumer_zero = self.store.claim_agent_run(
                    task.id,
                    task.execution_generation,
                    role=AgentRole.CONSUMER,
                    proposal_revision=0,
                    turn_attempt=0,
                    parent_agent_run_id=None,
                    operation_id="",
                    owner="poison-consumer-zero",
                ).run
                audit_zero = self.store.claim_agent_run(
                    task.id,
                    task.execution_generation,
                    role=AgentRole.AUDIT,
                    proposal_revision=0,
                    turn_attempt=0,
                    parent_agent_run_id=consumer_zero.id,
                    operation_id=(
                        f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
                    ),
                    owner="poison-audit-zero",
                ).run
                consumer_one = self.store.claim_agent_run(
                    task.id,
                    task.execution_generation,
                    role=AgentRole.CONSUMER,
                    proposal_revision=1,
                    turn_attempt=0,
                    parent_agent_run_id=audit_zero.id,
                    operation_id="",
                    owner="poison-consumer-one",
                ).run
                parent_id = consumer_one.id
                proposal_revision = 1
                turn_attempt = 2
                operation_id = (
                    f"agent-task:{task.id}:{task.execution_generation}:proposal:1"
                )
                conversation_session = "healthy-consumer-session"
            self.store.upsert_conversation(
                task.conversation_id,
                task.conversation_title,
                task.single_chat,
                conversation_session,
            )
            claim = self.store.claim_agent_run(
                task.id,
                task.execution_generation,
                role=self.role,
                proposal_revision=proposal_revision,
                turn_attempt=turn_attempt,
                parent_agent_run_id=parent_id,
                operation_id=operation_id,
                owner="poisoned-role",
            )
            assert claim.claimed
            self.store.set_agent_run_session(
                claim.run.id,
                "poisoned-role-session",
                owner="poisoned-role",
            )
            failed = self.store.fail_agent_run(
                claim.run.id,
                {"code": "codex_process_failed", "retryable": True},
                owner="poisoned-role",
            )
            self.run_id = failed.id
            raise RuntimeError("codex_process_failed")

        run = self.store.get_agent_run(self.run_id)
        assert run is not None
        self.observed_resume_sessions.append(run.codex_session_id)
        error = AgentError(code="stop_after_recovery", retryable=False)
        return OrchestrationResult(
            status="failed_terminal",
            final_run_id=run.id,
            final_role=self.role,
            summary=error.code,
            error=error,
            feedback_cycles=run.proposal_revision,
        )


def _get_audit_run(store, task_id: int, execution_generation: str):
    audit = store.get_agent_run_for_turn(
        task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    if audit is not None:
        return audit
    return store.get_agent_run_for_turn(
        task_id,
        execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )


def _claim_audit_run(
    store,
    task_id: int,
    execution_generation: str,
    *,
    owner: str,
    **kwargs,
):
    return store.claim_agent_run(
        task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{task_id}:{execution_generation}",
        owner=owner,
        **kwargs,
    )


def _claim_consumer_run(
    store,
    task_id: int,
    execution_generation: str,
    *,
    owner: str,
    **kwargs,
):
    return store.claim_agent_run(
        task_id,
        execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner=owner,
        **kwargs,
    )


class ReadyGate:
    def __init__(self, channel: str) -> None:
        self.channel_name = channel

    def check(self) -> ChannelGateResult:
        return ChannelGateResult(
            channel=self.channel_name,
            state=ChannelGateState.READY,
            reason_code="ready",
        )


class StaticGate:
    def __init__(self, result: ChannelGateResult) -> None:
        self.channel_name = result.channel
        self.result = result
        self.checks = 0

    def check(self) -> ChannelGateResult:
        self.checks += 1
        return self.result


class RecordingLoginCoordinator:
    def __init__(self) -> None:
        self.results: list[ChannelGateResult] = []

    def handle(self, result: ChannelGateResult):
        from app.channel_gate import LoginHandlingResult

        self.results.append(result)
        return LoginHandlingResult(
            launched=result.state is ChannelGateState.NEEDS_LOGIN
        )


class ContextOnlyDws:
    dws_bin = "dws"

    def __init__(self, messages: list[DingTalkMessage]) -> None:
        self.messages = messages
        self.recent_reads = 0
        self.unread_reads = 0
        self.forbidden_material_reads: list[str] = []

    def read_recent_messages(self, _conversation) -> list[DingTalkMessage]:
        self.recent_reads += 1
        return list(self.messages)

    def read_unread_messages(self, _conversation) -> list[DingTalkMessage]:
        self.unread_reads += 1
        return list(self.messages)

    def __getattr__(self, name: str):
        if name.startswith(
            (
                "read_doc",
                "read_minutes",
                "download",
                "get_aitable",
                "query_aitable",
                "read_oa",
                "search_document",
            )
        ):

            def forbidden(*_args, **_kwargs):
                self.forbidden_material_reads.append(name)
                raise AssertionError(f"service material read is forbidden: {name}")

            return forbidden
        raise AttributeError(name)


class FailingRefreshDws(ContextOnlyDws):
    def read_recent_messages(self, conversation) -> list[DingTalkMessage]:
        if self.recent_reads >= 1:
            raise RuntimeError("context refresh unavailable")
        return super().read_recent_messages(conversation)


class NativeCodexFacade:
    def __init__(self, workspace: Path) -> None:
        self.runner = type(
            "NativeRunnerConfig",
            (),
            {"workspace": workspace, "codex_bin": "codex"},
        )()


class UnexpectedRoleRunner:
    def run(self, *_args, **_kwargs):
        raise AssertionError("persisted terminal role turn must not be rerun")


def _persisted_effect_evidence(call_id: str, status: str) -> dict[str, object]:
    item: dict[str, object] = {
        "id": call_id,
        "type": "command_execution",
        "metadata": {
            "effect": "effectful",
            "native_cli": "dws",
            "operation": "chat message send",
            "command_digest": "a" * 64,
        },
    }
    if status == "completed":
        item.update({"exit_code": 0, "status": "completed"})
    return {"type": f"item.{status}", "item": item}


def _read_event(call_id: str = "read-1") -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "id": call_id,
            "type": "mcp_tool_call",
            "server": "memory_connector",
            "tool": "memory_recall",
            "arguments": {"query": "current task context"},
            "result": {"content": []},
            "status": "completed",
        },
    }


def _result(
    outcome: ScriptOutcome = ScriptOutcome.COMPLETED,
    *,
    summary: str = "任务已完成。",
    retryable: bool = False,
    side_effect_state: SideEffectState = SideEffectState.NONE,
    code: str = "",
) -> ScriptResult:
    return ScriptResult(
        outcome=outcome,
        summary=summary,
        error=AgentError(
            code=code,
            retryable=retryable,
            side_effect_state=side_effect_state,
        ),
    )


@dataclass
class ScriptedRun:
    result: ScriptResult
    events: tuple[dict[str, object], ...] = ()
    session_id: str = "session-1"
    receipts: tuple["PersistedCommandReceipt", ...] = ()


@dataclass(frozen=True)
class PersistedCommandReceipt:
    operation_id: str
    command_digest: str
    cli: str = "dws"
    command_path: str = "chat message send"


def _receipt(
    operation_id: str,
    *,
    command_digest: str = "a" * 64,
    command_path: str = "chat message send",
) -> PersistedCommandReceipt:
    return PersistedCommandReceipt(
        operation_id=operation_id,
        command_digest=command_digest,
        command_path=command_path,
    )


class ScriptedTaskOrchestrator:
    def __init__(self, store: AutoReplyStore, scripts: list[ScriptedRun]) -> None:
        self.store = store
        self.scripts = scripts
        self.calls: list[tuple[int, str, AgentTaskContext]] = []
        self.resume_session_ids: list[str] = []
        self.read_only_values: list[bool] = []
        self.owner = "scripted-agent"

    def process(self, task, context, *, refresh_context) -> OrchestrationResult:
        persisted = self.store.list_agent_runs_for_task_generation(
            task.id,
            task.execution_generation,
        )
        if not self.scripts and persisted:
            final_run = persisted[-1]
            if final_run.status == "completed" and final_run.role is AgentRole.AUDIT:
                audit_result = AuditAgentResult.model_validate_json(
                    final_run.final_result_json
                )
                return OrchestrationResult(
                    status="executed",
                    final_run_id=final_run.id,
                    final_role=AgentRole.AUDIT,
                    summary=audit_result.summary,
                    error=audit_result.error,
                    feedback_cycles=final_run.proposal_revision,
                    audit_result=audit_result,
                )
            if final_run.status == "completed" and final_run.role is AgentRole.CONSUMER:
                consumer_result = ConsumerAgentResult.model_validate_json(
                    final_run.final_result_json
                )
                return OrchestrationResult(
                    status=(
                        "no_action"
                        if consumer_result.outcome.value == "no_action"
                        else "needs_human"
                    ),
                    final_run_id=final_run.id,
                    final_role=AgentRole.CONSUMER,
                    summary=consumer_result.summary,
                    error=consumer_result.error,
                    feedback_cycles=final_run.proposal_revision,
                    consumer_result=consumer_result,
                )
        self.calls.append((task.id, task.execution_generation, context))
        self.read_only_values.append(True)
        script = self.scripts.pop(0)
        session_id = self.store.get_codex_session_id(task.conversation_id)
        if session_id:
            self.resume_session_ids.append(session_id)
        else:
            session_id = script.session_id
            self.store.upsert_conversation(
                task.conversation_id,
                task.conversation_title,
                task.single_chat,
                session_id,
            )
        consumer_claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=0,
            ),
            parent_agent_run_id=None,
            operation_id="",
            owner=self.owner,
            lease_seconds=1800,
            now=NOW,
        )
        assert consumer_claim.claimed
        self.store.set_agent_run_session(
            consumer_claim.run.id,
            session_id,
            owner=self.owner,
            now=NOW,
        )
        direct_result = script.result
        consumer_outcome = {
            ScriptOutcome.NO_ACTION: "no_action",
            ScriptOutcome.NEEDS_HUMAN: "needs_human",
            ScriptOutcome.FAILED: "failed",
        }.get(direct_result.outcome, "proposal")
        proposal = None
        if consumer_outcome == "proposal":
            proposal = {
                "objective": direct_result.summary,
                "actions": [
                    {
                        "description": direct_result.summary,
                        "capability": "agent_cli.dws",
                        "operation": "scripted test action",
                        "target": {"task_id": str(task.id)},
                        "payload": {"task_id": task.id},
                        "expected_verification": "Scripted test verification.",
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "",
            }
        consumer_result = ConsumerAgentResult.model_validate(
            {
                "outcome": consumer_outcome,
                "summary": direct_result.summary,
                "proposal": proposal,
                "decision_options": (
                    [
                        {
                            "key": "A",
                            "label": "采用保守处理",
                            "instruction": "采用已核验的保守处理并发布。",
                            "consequence": "不会扩大当前外部影响。",
                        },
                        {
                            "key": "B",
                            "label": "采用推进处理",
                            "instruction": "按已核验事实推进处理并发布。",
                            "consequence": "会执行对应的已审计动作。",
                        },
                    ]
                    if consumer_outcome == "needs_human"
                    else []
                ),
                "error": {
                    "code": direct_result.error.code,
                    "retryable": direct_result.error.retryable,
                    "authorization_required": direct_result.error.authorization_required,
                },
            }
        )
        if consumer_outcome == "failed":
            consumer_run = self.store.fail_agent_run(
                consumer_claim.run.id,
                consumer_result.error.model_dump(mode="json"),
                owner=self.owner,
                now=NOW,
            )
            return OrchestrationResult(
                status=(
                    "failed_retryable"
                    if consumer_result.error.retryable
                    else "failed_terminal"
                ),
                final_run_id=consumer_run.id,
                final_role=AgentRole.CONSUMER,
                summary=consumer_result.summary,
                error=consumer_result.error,
                feedback_cycles=0,
                consumer_result=consumer_result,
            )
        consumer_run = self.store.complete_agent_run(
            consumer_claim.run.id,
            consumer_result.model_dump(mode="json"),
            owner=self.owner,
            now=NOW,
        )
        if consumer_outcome != "proposal":
            return OrchestrationResult(
                status=(
                    "no_action" if consumer_outcome == "no_action" else "needs_human"
                ),
                final_run_id=consumer_run.id,
                final_role=AgentRole.CONSUMER,
                summary=consumer_result.summary,
                error=consumer_result.error,
                feedback_cycles=0,
                consumer_result=consumer_result,
            )

        operation_id = f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
        audit_claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=consumer_run.id,
            operation_id=operation_id,
            owner=self.owner,
            lease_seconds=1800,
            now=NOW,
        )
        assert audit_claim.claimed
        self.store.set_agent_run_session(
            audit_claim.run.id,
            f"{script.session_id}-audit",
            owner=self.owner,
            now=NOW,
        )
        for event in script.events:
            self.store.append_agent_run_event(
                audit_claim.run.id,
                event,
                owner=self.owner,
                now=NOW,
            )
        for receipt in script.receipts:
            self.store.record_agent_execution_receipt(
                audit_claim.run.id,
                receipt_id=f"native:{receipt.operation_id}:{receipt.command_digest}",
                operation_id=receipt.operation_id,
                cli=receipt.cli,
                command_path=receipt.command_path,
                command_digest=receipt.command_digest,
                exit_code=0,
                owner=self.owner,
                now=NOW,
            )
        live_reference = {"id": "scripted-result"}
        if direct_result.oa_action_receipt is not None:
            receipt = direct_result.oa_action_receipt
            live_reference = {
                "process_instance_id": receipt.process_instance_id,
                "task_id": receipt.task_id,
                "action": receipt.action,
                "remark": receipt.remark,
                **receipt.result,
            }
        audit_result = AuditAgentResult.model_validate(
            {
                "outcome": "executed",
                "summary": direct_result.summary,
                "proposal_revision": 0,
                "side_effect_state": "confirmed",
                "feedback": None,
                "external_result": {
                    "operation_id": operation_id,
                    "verification_summary": direct_result.summary,
                    "live_result_reference": live_reference,
                },
                "error": {
                    "code": direct_result.error.code,
                    "retryable": direct_result.error.retryable,
                    "authorization_required": direct_result.error.authorization_required,
                },
            }
        )
        audit_run = self.store.complete_agent_run(
            audit_claim.run.id,
            audit_result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state="confirmed",
            transcript_end_line=len(script.events),
            now=NOW,
        )
        return OrchestrationResult(
            status="executed",
            final_run_id=audit_run.id,
            final_role=AgentRole.AUDIT,
            summary=audit_result.summary,
            error=audit_result.error,
            feedback_cycles=0,
            audit_result=audit_result,
        )


def _prompt_json_section(prompt: str, heading: str):
    start = prompt.index(heading) + len(heading)
    value, _end = json.JSONDecoder().raw_decode(prompt[start:].lstrip())
    return value


def _agent_result_event(result) -> dict[str, object]:
    if isinstance(result, ConsumerAgentResult):
        error = result.error
        payload = {
            "outcome": result.outcome.value,
            "summary": result.summary,
            "proposal_json": (
                result.proposal.model_dump_json() if result.proposal is not None else None
            ),
            "decision_options_json": json.dumps(
                [option.model_dump() for option in result.decision_options]
            ),
            "error_code": error.code,
            "error_retryable": error.retryable,
            "error_authorization_required": error.authorization_required,
        }
    elif isinstance(result, AuditAgentResult):
        error = result.error
        payload = {
            "outcome": result.outcome.value,
            "summary": result.summary,
            "proposal_revision": result.proposal_revision,
            "side_effect_state": result.side_effect_state.value,
            "feedback_json": (
                result.feedback.model_dump_json() if result.feedback is not None else None
            ),
            "external_result_json": (
                result.external_result.model_dump_json()
                if result.external_result is not None
                else None
            ),
            "reconciliation_json": json.dumps(
                [item.model_dump(mode="json") for item in result.reconciliation]
            ),
            "error_code": error.code,
            "error_retryable": error.retryable,
            "error_authorization_required": error.authorization_required,
        }
    else:
        raise TypeError(f"unsupported result type: {type(result)!r}")
    return {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": json.dumps(payload)},
    }


def _consumer_protocol_result(
    outcome: str,
    summary: str,
    *,
    proposal: dict[str, object] | None = None,
    code: str = "",
    retryable: bool = False,
) -> ConsumerAgentResult:
    return ConsumerAgentResult.model_validate(
        {
            "outcome": outcome,
            "summary": summary,
            "proposal": proposal,
            "decision_options": (
                [
                    {
                        "key": "A",
                        "label": "采用保守处理",
                        "instruction": "采用已核验的保守处理并发布。",
                        "consequence": "不会扩大当前外部影响。",
                    },
                    {
                        "key": "B",
                        "label": "采用推进处理",
                        "instruction": "按已核验事实推进处理并发布。",
                        "consequence": "会执行对应的已审计动作。",
                    },
                ]
                if outcome == "needs_human"
                else []
            ),
            "error": {
                "code": code,
                "retryable": retryable,
                "authorization_required": False,
            },
        }
    )


def _audit_protocol_result(
    outcome: str,
    revision: int,
    summary: str,
    *,
    operation_id: str,
    live_reference: dict[str, object] | None = None,
    code: str = "",
    retryable: bool = False,
    authorization_required: bool = False,
) -> AuditAgentResult:
    executed = outcome == "executed"
    return AuditAgentResult.model_validate(
        {
            "outcome": outcome,
            "summary": summary,
            "proposal_revision": revision,
            "side_effect_state": "confirmed" if executed else "none",
            "feedback": None,
            "external_result": (
                {
                    "operation_id": operation_id,
                    "verification_summary": summary,
                    "live_result_reference": live_reference or {},
                }
                if executed
                else None
            ),
            "error": {
                "code": code,
                "retryable": retryable,
                "authorization_required": authorization_required,
            },
        }
    )


def _reviewed_cli_event(
    event_type: str,
    call_id: str,
    command: str,
    *,
    output: str = "",
    effectful: bool = False,
    succeeded: bool = True,
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": call_id,
        "type": "mcp_tool_call",
        "server": "agent_cli",
        "tool": "execute_reviewed_write" if effectful else "execute_reviewed_read",
        "arguments": {"argv": shlex.split(command)},
        "status": "in_progress",
    }
    if event_type == "item.completed":
        descriptor = describe_native_command(
            {"type": "command_execution", "argv": shlex.split(command)}
        )
        assert descriptor is not None
        receipt = {
            "cli": descriptor.cli,
            "operation": descriptor.command_path,
            "operation_digest": descriptor.command_digest,
            "target_identifiers": descriptor.target_identifiers,
            "result_digest": "protocol-result-digest",
            "stdout": output,
        }
        item.update(
            {
                "status": "completed" if succeeded else "failed",
                "result": {
                    "content": [{"type": "text", "text": json.dumps(receipt)}],
                    "structuredContent": receipt,
                    "isError": not succeeded,
                },
            }
        )
    return {"type": event_type, "item": item}


class ProtocolCodexExecutor:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.session_count = 0

    def __call__(self, _command, *, prompt: str, **kwargs) -> ProcessRunResult:
        self.prompts.append(prompt)
        self.session_count += 1
        records = [
            {
                "type": "thread.started",
                "thread_id": f"protocol-session-{self.session_count}",
            },
            *self.records(prompt),
        ]
        output = "\n".join(json.dumps(record) for record in records)
        callback = kwargs["on_stdout_line"]
        for line in output.splitlines():
            callback(line)
        return ProcessRunResult(returncode=0, stdout=output, stderr="")

    def records(self, prompt: str) -> list[dict[str, object]]:
        raise NotImplementedError


class ConfirmedFactProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self, fact_value: str) -> None:
        super().__init__()
        self.fact_value = fact_value
        self.fact_was_present = False

    def records(self, prompt: str) -> list[dict[str, object]]:
        messages = _prompt_json_section(prompt, "Recent conversation context\n")
        self.fact_was_present = any(
            self.fact_value in str(message.get("text") or "")
            for message in messages
            if isinstance(message, dict)
        )
        result = (
            _consumer_protocol_result(
                "no_action",
                f"Reused confirmed context value {self.fact_value}.",
            )
            if self.fact_was_present
            else _consumer_protocol_result(
                "needs_human",
                "A required confirmed context value is missing.",
                code="confirmed_fact_missing",
            )
        )
        return [_agent_result_event(result)]


class NativeCommandStub:
    def __init__(self, read_output: dict[str, object]) -> None:
        self.read_output = read_output
        self.calls: list[str] = []
        self.write_calls: list[str] = []

    def __call__(self, command: str) -> str:
        self.calls.append(command)
        if command.startswith((
            ".venv/bin/python -m app.cli read-oa-approval-detail ",
            "dws oa approval detail ",
            "dws oa approval tasks ",
        )):
            return json.dumps(self.read_output)
        if command.startswith("dws oa approval approve "):
            self.write_calls.append(command)
            return json.dumps({"success": True})
        if command.startswith(("dws doc info ", "dws doc read ")):
            return json.dumps({"content": "diagnostic evidence"})
        raise AssertionError(f"unexpected native command: {command}")


class OaProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self, native_executor: NativeCommandStub) -> None:
        super().__init__()
        self.native_executor = native_executor
        self.read_commands: list[str] = []

    def records(self, prompt: str) -> list[dict[str, object]]:
        audit_turn = "Candidate revision\n" in prompt
        materials = _prompt_json_section(
            prompt,
            "Raw material references and exact read commands\n",
        )
        oa_material = next(
            material
            for material in materials
            if isinstance(material, dict) and material.get("kind") == "dingtalk_oa"
        )
        reference = json.loads(str(oa_material["reference"]))
        records: list[dict[str, object]] = []
        live_results: list[dict[str, object]] = []
        for index, command in enumerate(oa_material["read_commands"]):
            self.read_commands.append(command)
            output = self.native_executor(command)
            records.extend(
                (
                    _reviewed_cli_event("item.started", f"oa-read-{index}", command),
                    _reviewed_cli_event(
                        "item.completed",
                        f"oa-read-{index}",
                        command,
                        output=output,
                    ),
                )
            )
            live_results.append(json.loads(output))

        tasks = live_results[-1].get("tasks") if live_results else []
        live_tasks = tasks if isinstance(tasks, list) else []
        if len(live_tasks) != 1:
            result = _consumer_protocol_result(
                "needs_human",
                "Live OA detail has more than one candidate task.",
                code="oa_target_ambiguous",
            )
        else:
            live_task = live_tasks[0] if isinstance(live_tasks[0], dict) else {}
            status = str(live_task.get("status") or "").lower()
            if status == "completed":
                result = _consumer_protocol_result(
                    "no_action",
                    "Live OA task is already completed.",
                )
            else:
                task_id = str(live_task.get("task_id") or "")
                process_id = str(reference.get("process_instance_id") or "")
                argv = [
                    "dws",
                    "oa",
                    "approval",
                    "approve",
                    "--instance-id",
                    process_id,
                    "--task-id",
                    task_id,
                    "--remark",
                    "Reviewed by protocol agent",
                    "--format",
                    "json",
                    "--yes",
                ]
                if not audit_turn:
                    result = _consumer_protocol_result(
                        "proposal",
                        "Prepared the live OA action for independent audit.",
                        proposal={
                            "objective": "Review the live OA task.",
                            "actions": [
                                {
                                    "description": "Approve the live OA task.",
                                    "capability": "agent_cli.dws",
                                    "operation": "oa approval approve",
                                    "target": {
                                        "process_instance_id": process_id,
                                        "task_id": task_id,
                                    },
                                    "payload": {"argv": argv},
                                    "expected_verification": "Read live OA detail again.",
                                }
                            ],
                            "sourced_facts": [],
                            "authored_judgment": "Use only the live task identity.",
                        },
                    )
                else:
                    candidate = _prompt_json_section(prompt, "Candidate revision\n")
                    candidate_action = candidate["proposal"]["actions"][0]
                    candidate_argv = candidate_action["payload"]["argv"]
                    write_command = shlex.join(candidate_argv)
                    output = self.native_executor(write_command)
                    records.extend(
                        (
                            _reviewed_cli_event(
                                "item.started",
                                "oa-write",
                                write_command,
                                effectful=True,
                            ),
                            _reviewed_cli_event(
                                "item.completed",
                                "oa-write",
                                write_command,
                                output=output,
                                effectful=True,
                            ),
                        )
                    )
                    verify_command = oa_material["read_commands"][0]
                    verify_output = self.native_executor(verify_command)
                    records.extend(
                        (
                            _reviewed_cli_event(
                                "item.started", "oa-verify", verify_command
                            ),
                            _reviewed_cli_event(
                                "item.completed",
                                "oa-verify",
                                verify_command,
                                output=verify_output,
                            ),
                        )
                    )
                    result = _audit_protocol_result(
                        "executed",
                        int(candidate["proposal_revision"]),
                        "Live OA task was reviewed and verified.",
                        operation_id=str(candidate["operation_id"]),
                        live_reference={
                            "process_instance_id": process_id,
                            "task_id": task_id,
                            "action": "approve",
                            "result": json.loads(output),
                        },
                    )
        records.append(_agent_result_event(result))
        return records


class DiagnosisOnlyProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self, native_executor: NativeCommandStub) -> None:
        super().__init__()
        self.native_executor = native_executor

    def records(self, prompt: str) -> list[dict[str, object]]:
        materials = _prompt_json_section(
            prompt,
            "Raw material references and exact read commands\n",
        )
        command = next(
            command
            for material in materials
            if isinstance(material, dict)
            for command in material.get("read_commands", [])
        )
        output = self.native_executor(command)
        return [
            _reviewed_cli_event("item.started", "diagnostic-read", command),
            _reviewed_cli_event(
                "item.completed",
                "diagnostic-read",
                command,
                output=output,
            ),
            _agent_result_event(
                _consumer_protocol_result(
                    "needs_human",
                    "Diagnosed the requested repair but no executable action is available.",
                    code="executable_proposal_missing",
                )
            ),
        ]


class FailedWriteProtocolExecutor(ProtocolCodexExecutor):
    def records(self, prompt: str) -> list[dict[str, object]]:
        command = "dws chat message send --group cid-1 --text 'hello' --yes"
        if "Candidate revision\n" not in prompt:
            return [
                _agent_result_event(
                    _consumer_protocol_result(
                        "proposal",
                        "Prepared a message send for independent audit.",
                        proposal={
                            "objective": "Send the requested message.",
                            "actions": [
                                {
                                    "description": "Send the message.",
                                    "capability": "agent_cli.dws",
                                    "operation": "chat message send",
                                    "target": {"group": "cid-1"},
                                    "payload": {"argv": shlex.split(command)},
                                    "expected_verification": "Read the live message.",
                                }
                            ],
                            "sourced_facts": [],
                            "authored_judgment": "",
                        },
                    )
                )
            ]
        candidate = _prompt_json_section(prompt, "Candidate revision\n")
        failed = _reviewed_cli_event(
            "item.completed",
            "send-failed",
            command,
            output='{"error":"send_failed"}',
            effectful=True,
            succeeded=False,
        )
        return [
            _reviewed_cli_event("item.started", "send-failed", command, effectful=True),
            failed,
            _agent_result_event(
                _audit_protocol_result(
                    "failed",
                    int(candidate["proposal_revision"]),
                    "The native write returned a nonzero exit code.",
                    operation_id=str(candidate["operation_id"]),
                    retryable=True,
                    code="native_write_failed",
                )
            ),
        ]


class ContextRefreshingProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self, dws: ContextOnlyDws, new_message: DingTalkMessage) -> None:
        super().__init__()
        self.dws = dws
        self.new_message = new_message
        self.audit_prompt = ""

    def records(self, prompt: str) -> list[dict[str, object]]:
        if "Candidate revision\n" not in prompt:
            self.dws.messages.append(self.new_message)
            return [
                _agent_result_event(
                    _consumer_protocol_result(
                        "proposal",
                        "Prepared candidate before the conversation update.",
                        proposal={
                            "objective": "Send the requested update.",
                            "actions": [
                                {
                                    "description": "Send the update.",
                                    "capability": "agent_cli.dws",
                                    "operation": "chat message send",
                                    "target": {"group": "cid-1"},
                                    "payload": {
                                        "argv": [
                                            "dws",
                                            "chat",
                                            "message",
                                            "send",
                                            "--group",
                                            "cid-1",
                                            "--text",
                                            "old target",
                                            "--yes",
                                        ]
                                    },
                                    "expected_verification": "Read the live message.",
                                }
                            ],
                            "sourced_facts": [],
                            "authored_judgment": "",
                        },
                    )
                )
            ]
        self.audit_prompt = prompt
        candidate = _prompt_json_section(prompt, "Candidate revision\n")
        return [
            _agent_result_event(
                _audit_protocol_result(
                    "needs_human",
                    int(candidate["proposal_revision"]),
                    "The refreshed context requires human review.",
                    operation_id=str(candidate["operation_id"]),
                    code="newer_context_requires_review",
                )
            )
        ]


class AuthorizationRecoveryProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.audit_attempts = 0

    def __call__(self, _command, *, prompt: str, **kwargs) -> ProcessRunResult:
        self.prompts.append(prompt)
        audit_turn = "Candidate revision\n" in prompt
        if audit_turn:
            self.audit_attempts += 1
            thread_id = "authorization-audit-session"
        else:
            thread_id = "authorization-consumer-session"
        records = [
            {"type": "thread.started", "thread_id": thread_id},
            *self.records(prompt),
        ]
        output = "\n".join(json.dumps(record) for record in records)
        callback = kwargs["on_stdout_line"]
        for line in output.splitlines():
            callback(line)
        return ProcessRunResult(returncode=0, stdout=output, stderr="")

    def records(self, prompt: str) -> list[dict[str, object]]:
        if "Candidate revision\n" not in prompt:
            return [
                _agent_result_event(
                    _consumer_protocol_result(
                        "proposal",
                        "Prepared the requested message.",
                        proposal={
                            "objective": "Send the requested message.",
                            "actions": [
                                {
                                    "description": "Send the message.",
                                    "capability": "agent_cli.dws",
                                    "operation": "chat message send",
                                    "target": {"group": "cid-1"},
                                    "payload": {
                                        "argv": [
                                            "dws",
                                            "chat",
                                            "message",
                                            "send",
                                            "--group",
                                            "cid-1",
                                            "--text",
                                            "approved",
                                            "--yes",
                                        ]
                                    },
                                    "expected_verification": "Read the live message.",
                                }
                            ],
                            "sourced_facts": [],
                            "authored_judgment": "",
                        },
                    )
                )
            ]
        candidate = _prompt_json_section(prompt, "Candidate revision\n")
        if self.audit_attempts == 1:
            return [
                _agent_result_event(
                    _audit_protocol_result(
                        "failed",
                        int(candidate["proposal_revision"]),
                        "Authorization must be restored.",
                        operation_id=str(candidate["operation_id"]),
                        code="authorization_wait",
                        retryable=True,
                        authorization_required=True,
                    )
                )
            ]
        write_command = shlex.join(
            candidate["proposal"]["actions"][0]["payload"]["argv"]
        )
        verify_command = "dws chat message list --group cid-1 --time 2026-08-07"
        return [
            _reviewed_cli_event(
                "item.started", "authorization-write", write_command, effectful=True
            ),
            _reviewed_cli_event(
                "item.completed",
                "authorization-write",
                write_command,
                effectful=True,
                output='{"ok":true}',
            ),
            _reviewed_cli_event("item.started", "authorization-verify", verify_command),
            _reviewed_cli_event(
                "item.completed",
                "authorization-verify",
                verify_command,
                output='{"messages":["approved"]}',
            ),
            _agent_result_event(
                _audit_protocol_result(
                    "executed",
                    int(candidate["proposal_revision"]),
                    "Execution succeeded after authorization recovery.",
                    operation_id=str(candidate["operation_id"]),
                )
            ),
        ]


class RetryExhaustionProtocolExecutor(AuthorizationRecoveryProtocolExecutor):
    def records(self, prompt: str) -> list[dict[str, object]]:
        if "Candidate revision\n" not in prompt:
            return super().records(prompt)
        candidate = _prompt_json_section(prompt, "Candidate revision\n")
        return [
            _agent_result_event(
                _audit_protocol_result(
                    "failed",
                    int(candidate["proposal_revision"]),
                    "The audit dependency remains unavailable.",
                    operation_id=str(candidate["operation_id"]),
                    code="audit_dependency_unavailable",
                    retryable=True,
                )
            )
        ]


def _message(
    text: str = "@CEO Agent 请处理",
    *,
    message_id: str = "msg-1",
    raw_payload: dict[str, object] | None = None,
) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id=message_id,
        conversation_title="测试群",
        single_chat=False,
        sender_name="ET",
        sender_user_id="user-1",
        create_time="2026-07-29 16:55:00",
        content=text,
        raw_payload=raw_payload or {},
    )


def test_agent_context_preserves_raw_sender_identity(tmp_path: Path):
    trigger = _message().model_copy(
        update={
            "sender_open_dingtalk_id": "open-user-1",
            "mentioned_user_ids": ["mentioned-1"],
        }
    )
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION))],
    )
    _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    context = runner.calls[0][2]
    assert context.trigger_sender == "ET"
    assert context.trigger_sender_user_id == "user-1"
    assert context.trigger_sender_open_dingtalk_id == "open-user-1"
    assert context.trigger_mentioned_user_ids == ("mentioned-1",)


def _enqueue(
    store: AutoReplyStore,
    trigger: DingTalkMessage,
    *,
    generation: str = "g1",
    oa_url: str = "",
) -> int:
    assert store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        execution_generation=generation,
        oa_url=oa_url,
    )
    task = store.get_reply_task_for_message(
        trigger.open_conversation_id,
        trigger.open_message_id,
    )
    assert task is not None
    return task.id


def test_queued_task_uses_orchestrator_without_alternate_runtime(tmp_path: Path):
    trigger = _message("No external action is needed.")
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    orchestrator = NoActionOrchestrator(store)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=ContextOnlyDws([trigger]),
        codex=object(),
        agent_orchestrator=orchestrator,
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )
    task_id = _enqueue(store, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    assert len(orchestrator.calls) == 1
    assert store.get_reply_task(task_id).status == "done"
    run = store.list_agent_runs_for_task_generation(task_id, "g1")[-1]
    assert run.role is AgentRole.CONSUMER
    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None and attempt.send_status == "skipped"


def test_worker_passes_dry_run_to_real_audit_runner(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=ContextOnlyDws([]),
        codex=NativeCodexFacade(tmp_path),
        dry_run=True,
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )

    orchestrator = worker._agent_orchestrator()

    assert isinstance(orchestrator.audit, AuditAgentRunner)
    assert orchestrator.audit.dry_run is True


def test_stale_worker_recovers_completed_consumer_turn_without_legacy_parsing(
    tmp_path: Path,
):
    trigger = _message("No action remains.")
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    task_id = _enqueue(store, trigger)
    task = store.claim_reply_task(task_id)
    assert task is not None
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="completed-consumer",
    )
    store.complete_agent_run(
        claim.run.id,
        _consumer_protocol_result("no_action", "Nothing remains.").model_dump(
            mode="json"
        ),
        owner="completed-consumer",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (task.id,),
        )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=ContextOnlyDws([trigger]),
        codex=object(),
        agent_orchestrator=AgentOrchestrator(
            store=store,
            consumer=UnexpectedRoleRunner(),
            audit=UnexpectedRoleRunner(),
        ),
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )

    assert worker.consume_once(max_tasks=1) == 1

    recovered = store.get_reply_task(task.id)
    assert recovered is not None and recovered.status == "done"
    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None and attempt.send_status == "skipped"


def test_worker_finalizes_unknown_audit_without_session_as_needs_human(
    tmp_path: Path,
):
    trigger = _message("Send the reviewed message.")
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    task_id = _enqueue(store, trigger)
    task = store.claim_reply_task(task_id)
    assert task is not None
    proposal = {
        "objective": "Send the reviewed message.",
        "actions": [
            {
                "description": "Send the message.",
                "capability": "agent_cli.dws",
                "operation": "chat message send",
                "target": {"group": "cid-1"},
                "payload": {
                    "argv": [
                        "dws",
                        "chat",
                        "message",
                        "send",
                        "--group",
                        "cid-1",
                        "--text",
                        "done",
                        "--yes",
                    ]
                },
                "expected_verification": "The message exists in the group.",
            }
        ],
        "sourced_facts": [],
        "authored_judgment": "The user requested delivery.",
    }
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="seed-consumer",
    ).run
    store.complete_agent_run(
        consumer.id,
        _consumer_protocol_result(
            "proposal",
            "Prepared a reviewed message.",
            proposal=proposal,
        ).model_dump(mode="json"),
        owner="seed-consumer",
    )
    operation_id = "reply-task:g1:revision:0"
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id=operation_id,
        owner="seed-audit",
    ).run
    store.append_agent_run_event(
        audit.id,
        {
            "type": "item.started",
            "item": {
                "id": "write-1",
                "metadata": {
                    "effect": "effectful",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "operation_id": operation_id,
                    "operation_digest": "operation-digest",
                    "arguments_digest": "arguments-digest",
                    "target_identifiers": {"group": "cid-1"},
                },
            },
        },
        owner="seed-audit",
    )
    store.mark_agent_run_unknown(
        audit.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="seed-audit",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-29 08:00:00' where id=?",
            (task.id,),
        )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=ContextOnlyDws([trigger]),
        codex=object(),
        agent_orchestrator=AgentOrchestrator(
            store=store,
            consumer=UnexpectedRoleRunner(),
            audit=UnexpectedRoleRunner(),
        ),
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )

    assert worker.consume_once(max_tasks=1) == 1

    persisted_task = store.get_reply_task(task.id)
    persisted_run = store.get_agent_run(audit.id)
    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert persisted_task is not None and persisted_task.status == "done"
    assert persisted_run is not None and persisted_run.status == "completed"
    assert persisted_run.side_effect_state == "unknown"
    assert attempt is not None and attempt.send_status == "needs_human"
    assert attempt.send_error == "audit_recovery_session_missing"


def test_worker_requeues_absent_direct_mcp_recovery_without_write(tmp_path: Path):
    trigger = _message("Submit the reviewed interview result.")
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    task_id = _enqueue(store, trigger)
    task = store.claim_reply_task(task_id)
    assert task is not None
    proposal = {
        "objective": "Submit the reviewed interview result.",
        "actions": [
            {
                "description": "Upload interview result.",
                "capability": "xiaoqing_interview",
                "operation": "upload_interview_result",
                "target": {
                    "candidate_id": "candidate-1",
                    "interview_id": "interview-1",
                },
                "payload": {
                    "candidate_id": "candidate-1",
                    "interview_id": "interview-1",
                    "evaluation": "approved",
                },
                "expected_verification": "Read the same interview context.",
            }
        ],
        "sourced_facts": [],
        "authored_judgment": "The user requested delivery.",
    }
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="seed-consumer",
    ).run
    store.complete_agent_run(
        consumer.id,
        _consumer_protocol_result(
            "proposal", "Prepared an interview result.", proposal=proposal
        ).model_dump(mode="json"),
        owner="seed-consumer",
    )
    operation_id = "reply-task:g1:revision:0"
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id=operation_id,
        owner="seed-audit",
    ).run
    store.set_agent_run_session(audit.id, "audit-session", owner="seed-audit")
    store.append_agent_run_event(
        audit.id,
        {
            "type": "item.started",
            "item": {
                "id": "direct-write",
                "metadata": {
                    "effect": "effectful",
                    "capability": "xiaoqing_interview",
                    "operation": "upload_interview_result",
                    "operation_id": operation_id,
                    "operation_digest": "operation-digest",
                    "arguments_digest": "arguments-digest",
                    "target_identifiers": {
                        "candidate_id": "candidate-1",
                        "interview_id": "interview-1",
                    },
                },
            },
        },
        owner="seed-audit",
    )
    store.mark_agent_run_unknown(
        audit.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="seed-audit",
    )
    reconciled = AuditAgentResult.model_validate(
        {
            "outcome": "reconciled",
            "summary": "Live readback proved the direct MCP action absent.",
            "proposal_revision": 0,
            "side_effect_state": "unknown",
            "feedback": None,
            "external_result": None,
            "reconciliation": [
                {
                    "action_index": 0,
                    "disposition": "absent",
                    "read_result_digest": "read-digest",
                }
            ],
            "error": {"code": "", "retryable": False, "authorization_required": False},
        }
    )
    with store._connect() as db:
        db.execute(
            "update agent_runs set final_result_json=? where id=?",
            (reconciled.model_dump_json(), audit.id),
        )
        db.execute(
            "update reply_tasks set status='pending', locked_at=null, available_at='' "
            "where id=?",
            (task.id,),
        )

    class NoWriteExecutor:
        def __call__(self, *args, **kwargs):
            raise AssertionError("worker must not replay direct MCP writes")

    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=ContextOnlyDws([trigger]),
        codex=object(),
        agent_orchestrator=AgentOrchestrator(
            store=store,
            consumer=UnexpectedRoleRunner(),
            audit=AuditAgentRunner(
                store=store,
                workspace=tmp_path,
                executor=NoWriteExecutor(),
            ),
        ),
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )

    assert worker.consume_once(max_tasks=1) == 0
    persisted = store.get_agent_run(audit.id)
    requeued = store.get_reply_task(task.id)
    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert persisted is not None and persisted.status == "failed"
    assert requeued is not None and requeued.status == "pending"
    assert requeued.execution_generation != task.execution_generation
    assert attempt is not None and attempt.send_status == "failed"
    assert attempt.send_error == "audit_recovery_candidate_invalid"


def _worker(
    tmp_path: Path,
    messages: list[DingTalkMessage],
    scripts: list[ScriptedRun],
    *,
    max_task_attempts: int = 3,
) -> tuple[DingTalkAutoReplyWorker, ScriptedTaskOrchestrator, ContextOnlyDws]:
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    dws = ContextOnlyDws(messages)
    runner = ScriptedTaskOrchestrator(store, scripts)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=object(),
        agent_orchestrator=runner,
        channel_gates={
            "dingtalk": ReadyGate("dingtalk"),
            "lark": ReadyGate("lark"),
        },
        now_provider=lambda: NOW,
        max_task_attempts=max_task_attempts,
    )
    return worker, runner, dws


def _worker_with_protocol_executor(
    tmp_path: Path,
    messages: list[DingTalkMessage],
    executor: ProtocolCodexExecutor,
    *,
    max_task_attempts: int = 3,
) -> tuple[DingTalkAutoReplyWorker, ContextOnlyDws]:
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    dws = ContextOnlyDws(messages)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
            owner="protocol-consumer",
            codex_session_exists=lambda _session_id: True,
        ),
        audit=AuditAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
            owner="protocol-audit",
        ),
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=object(),
        agent_orchestrator=orchestrator,
        channel_gates={
            "dingtalk": ReadyGate("dingtalk"),
            "lark": ReadyGate("lark"),
        },
        now_provider=lambda: NOW,
        max_task_attempts=max_task_attempts,
    )
    return worker, dws


def test_worker_refreshes_conversation_after_consumer_before_audit(tmp_path: Path):
    trigger = _message("Send the current target.")
    new_message = _message(
        "The target changed after the request.",
        message_id="msg-new",
    )
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    dws = ContextOnlyDws([trigger])
    executor = ContextRefreshingProtocolExecutor(dws, new_message)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
            owner="context-refresh-consumer",
            codex_session_exists=lambda _session_id: True,
        ),
        audit=AuditAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
            owner="context-refresh-audit",
        ),
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=object(),
        agent_orchestrator=orchestrator,
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )
    _enqueue(store, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    assert "The target changed after the request." in executor.audit_prompt
    assert dws.recent_reads == 2
    assert dws.unread_reads == 2


def test_oa_pending_scan_refresh_reuses_synthetic_trigger_without_chat_lookup(
    tmp_path: Path,
):
    trigger = _message(
        "Review the queued approval.",
        raw_payload={"source": "oa_pending_scan"},
    )
    new_message = _message(
        "The approval context changed after the request.",
        message_id="msg-new",
    )
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    dws = ContextOnlyDws([trigger])
    executor = ContextRefreshingProtocolExecutor(dws, new_message)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=object(),
        agent_orchestrator=AgentOrchestrator(
            store=store,
            consumer=ConsumerAgentRunner(
                store=store,
                workspace=tmp_path,
                executor=executor,
                owner="oa-refresh-consumer",
                codex_session_exists=lambda _session_id: True,
            ),
            audit=AuditAgentRunner(
                store=store,
                workspace=tmp_path,
                executor=executor,
                owner="oa-refresh-audit",
            ),
        ),
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )
    _enqueue(store, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    assert "Review the queued approval." in executor.audit_prompt
    assert "The approval context changed after the request." not in executor.audit_prompt
    assert dws.recent_reads == 0
    assert dws.unread_reads == 0


def test_worker_defers_without_audit_when_context_refresh_fails(tmp_path: Path):
    trigger = _message("Send the current target.")
    new_message = _message("unused", message_id="msg-new")
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    dws = FailingRefreshDws([trigger])
    executor = ContextRefreshingProtocolExecutor(dws, new_message)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=object(),
        agent_orchestrator=AgentOrchestrator(
            store=store,
            consumer=ConsumerAgentRunner(
                store=store,
                workspace=tmp_path,
                executor=executor,
                owner="failing-refresh-consumer",
                codex_session_exists=lambda _session_id: True,
            ),
            audit=AuditAgentRunner(
                store=store,
                workspace=tmp_path,
                executor=executor,
                owner="failing-refresh-audit",
            ),
        ),
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )
    task_id = _enqueue(store, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    task = store.get_reply_task(task_id)
    assert task is not None and task.status == "pending"
    assert task.error == "agent_context_refresh_failed"
    assert executor.audit_prompt == ""


def test_worker_retries_authorization_failed_turn_after_gate_recovery(tmp_path: Path):
    trigger = _message("Send the approved message.")
    executor = AuthorizationRecoveryProtocolExecutor()
    worker, _dws = _worker_with_protocol_executor(tmp_path, [trigger], executor)
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    waiting = worker.store.get_reply_task(task_id)
    assert waiting is not None
    assert waiting.status == "pending"
    assert waiting.error == "authorization_wait"
    assert executor.audit_attempts == 1
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='' where id=?",
            (task_id,),
        )

    assert worker.consume_once(max_tasks=1) == 1

    completed = worker.store.get_reply_task(task_id)
    assert completed is not None and completed.status == "done"
    assert executor.audit_attempts == 2
    runs = worker.store.list_agent_runs_for_task_generation(task_id, "g1")
    audit_runs = [run for run in runs if run.role is AgentRole.AUDIT]
    assert len(audit_runs) == 1
    assert audit_runs[0].status == "completed"


def test_worker_defers_authorization_failure_at_attempt_limit(tmp_path: Path):
    trigger = _message("Send the approved message.")
    executor = AuthorizationRecoveryProtocolExecutor()
    worker, _dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        executor,
        max_task_attempts=1,
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    waiting = worker.store.get_reply_task(task_id)
    assert waiting is not None
    assert waiting.status == "pending"
    assert waiting.error == "authorization_wait"
    assert waiting.attempts == 0


def test_worker_stops_retryable_orchestration_at_attempt_limit(tmp_path: Path):
    trigger = _message("Send the approved message.")
    executor = RetryExhaustionProtocolExecutor()
    worker, _dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        executor,
        max_task_attempts=2,
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    first = worker.store.get_reply_task(task_id)
    assert first is not None and first.status == "pending"
    assert first.attempts == 1
    assert first.error == "audit_retry_exhausted"
    first_attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert first_attempt is not None
    assert first_attempt.send_status == "failed"
    assert first_attempt.send_error == "audit_retry_exhausted"
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='' where id=?",
            (task_id,),
        )

    assert worker.consume_once(max_tasks=1) == 0

    retried = worker.store.get_reply_task(task_id)
    assert retried is not None and retried.status == "failed"
    assert retried.attempts == 2
    attempts = [
        attempt
        for attempt in worker.store.list_reply_attempts(limit=10)
        if attempt.trigger_message_id == trigger.open_message_id
    ]
    assert len(attempts) == 2
    assert {attempt.send_error for attempt in attempts} == {"audit_retry_exhausted"}
    assert executor.audit_attempts == 4


def test_oa_material_does_not_recover_target_from_historical_context(tmp_path: Path):
    historical = _message(
        "https://aflow.dingtalk.com/detail?procInstId=old-proc&taskId=old-task",
        message_id="msg-old",
    )
    trigger = _message("Please review the current request.")
    worker, runner, _dws = _worker(
        tmp_path,
        [historical, trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION, summary="No action."))],
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert all(
        material.kind != "dingtalk_oa" for material in runner.calls[0][2].materials
    )


def test_oa_trigger_without_exact_process_id_preserves_reference_without_fallback(
    tmp_path: Path,
):
    oa_url = "https://aflow.dingtalk.com/dingtalk/pc/query/pchomepage.htm?swfrom=oa"
    trigger = _message(f"Please review {oa_url}")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(ScriptOutcome.NEEDS_HUMAN, summary="Target is ambiguous.")
            )
        ],
    )
    _enqueue(worker.store, trigger, oa_url=oa_url)

    worker.consume_once(max_tasks=1)

    material = next(
        item for item in runner.calls[0][2].materials if item.kind == "dingtalk_oa"
    )
    assert oa_url in material.reference
    assert material.read_commands == ()


def test_queued_task_runs_agent_once_and_records_completed_attempt(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    summary="已回复并确认发送成功。",
                    side_effect_state=SideEffectState.CONFIRMED,
                ),
                receipts=(_receipt("send-1"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 1
    assert [(task, generation) for task, generation, _ in runner.calls] == [
        (task_id, "g1")
    ]
    assert worker.store.get_reply_task(task_id).status == "done"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert attempt.codex_reason == "已回复并确认发送成功。"
    assert attempt.audit_summary == "已回复并确认发送成功。"


def test_dry_run_invokes_audit_orchestrator_in_read_only_mode(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION, summary="只读检查完成。"))],
    )
    worker.dry_run = True
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert runner.read_only_values == [True]


@pytest.mark.parametrize(
    ("script", "task_status", "attempt_status"),
    [
        (
            ScriptedRun(_result(ScriptOutcome.NO_ACTION, summary="无需动作。")),
            "done",
            "skipped",
        ),
        (
            ScriptedRun(
                _result(
                    ScriptOutcome.NEEDS_HUMAN,
                    summary="需要人工补充权限。",
                    code="permission_missing",
                )
            ),
            "done",
            "needs_human",
        ),
        (
            ScriptedRun(
                _result(
                    ScriptOutcome.FAILED,
                    summary="暂时无法读取。",
                    retryable=True,
                    code="temporary_read_failure",
                )
            ),
            "pending",
            "failed",
        ),
        (
            ScriptedRun(
                _result(
                    ScriptOutcome.FAILED,
                    summary="材料永久缺失。",
                    code="material_missing",
                )
            ),
            "failed",
            "failed",
        ),
    ],
)
def test_orchestration_outcome_maps_to_task_and_attempt(
    tmp_path: Path,
    script: ScriptedRun,
    task_status: str,
    attempt_status: str,
):
    trigger = _message()
    worker, _runner, _dws = _worker(tmp_path, [trigger], [script])
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == task_status
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == attempt_status
    if attempt_status == "needs_human":
        assert "permission_missing" in attempt.send_error


def test_retryable_failure_reuses_generation_and_session_with_new_turn_then_succeeds(
    tmp_path: Path,
):
    trigger = _message("请读取材料后完成回复")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    ScriptOutcome.FAILED,
                    summary="临时读取失败。",
                    retryable=True,
                    code="temporary_read_failure",
                ),
                session_id="session-retry",
            ),
            ScriptedRun(
                _result(ScriptOutcome.NO_ACTION, summary="已完成复核，无需回复。"),
                session_id="unused",
            ),
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)
    first = worker.store.get_reply_task(task_id)
    assert first is not None and first.status == "pending"
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='' where id=?",
            (task_id,),
        )

    worker.consume_once(max_tasks=1)

    assert len(runner.calls) == 2
    assert [generation for _task, generation, _context in runner.calls] == [
        "g1",
        "g1",
    ]
    assert runner.resume_session_ids == ["session-retry"]
    failed_run = worker.store.get_agent_run_for_turn(
        task_id,
        "g1",
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    completed_run = worker.store.get_agent_run_for_turn(
        task_id,
        "g1",
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=1,
    )
    assert failed_run is not None and failed_run.status == "failed"
    assert completed_run is not None and completed_run.status == "completed"
    assert worker.store.get_reply_task(task_id).status == "done"


def test_retryable_failure_becomes_terminal_at_worker_attempt_limit(tmp_path: Path):
    trigger = _message("请读取材料后完成回复")
    scripts = [
        ScriptedRun(
            _result(
                ScriptOutcome.FAILED,
                summary="临时读取失败。",
                retryable=True,
                code="temporary_read_failure",
            ),
            session_id="session-retry",
        )
        for _ in range(3)
    ]
    worker, runner, _dws = _worker(tmp_path, [trigger], scripts)
    worker.max_task_attempts = 2
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)
    with worker.store._connect() as db:
        db.execute("update reply_tasks set available_at='' where id=?", (task_id,))
    worker.consume_once(max_tasks=1)

    assert len(runner.calls) == 2
    assert worker.store.get_reply_task(task_id).status == "failed"


def test_retryable_failure_is_requeued_without_custom_receipt_logic(
    tmp_path: Path,
):
    trigger = _message("请发送回复")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    ScriptOutcome.FAILED,
                    summary="发送已确认，但最终结果写入失败。",
                    retryable=True,
                    code="result_persistence_failed",
                    side_effect_state=SideEffectState.CONFIRMED,
                ),
                receipts=(_receipt("send-confirmed"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    task = worker.store.get_reply_task(task_id)
    assert task is not None and task.status == "pending"
    assert len(runner.calls) == 1
    run = _get_audit_run(worker.store, task_id, "g1")
    assert run is not None and run.side_effect_state == "none"
    assert worker.store.list_agent_execution_receipts(run.id) == []


def test_completed_result_does_not_require_custom_effect_evidence(tmp_path: Path):
    trigger = _message("请修复服务")
    worker, _runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(side_effect_state=SideEffectState.CONFIRMED),
                (_read_event(),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == "done"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "completed"


def test_diagnosis_only_for_requested_execution_waits_for_human_by_agent_result(
    tmp_path: Path,
):
    trigger = _message("请执行修复并验证")
    worker, _runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    ScriptOutcome.NEEDS_HUMAN,
                    summary="已定位问题，但当前没有执行权限。",
                    code="execution_not_performed",
                ),
                (_read_event("diagnosis-read"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == "done"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "execution_not_performed"


def test_no_action_result_does_not_consult_custom_receipts(tmp_path: Path):
    trigger = _message("请检查是否需要处理")
    worker, _runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(ScriptOutcome.NO_ACTION, summary="无需动作。"),
                receipts=(_receipt("unexpected-write"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == "done"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    run = _get_audit_run(worker.store, task_id, "g1")
    assert run is not None
    assert worker.store.list_agent_execution_receipts(run.id) == []


def test_failed_result_is_a_regular_failure_without_unknown_effect_state(
    tmp_path: Path,
):
    trigger = _message("请发送回复")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    ScriptOutcome.FAILED,
                    summary="发送结果未知。",
                    code="send_interrupted",
                    side_effect_state=SideEffectState.UNKNOWN,
                ),
                (_persisted_effect_evidence("send-1", "started"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert len(runner.calls) == 1
    assert worker.store.get_reply_task(task_id).status == "failed"
    run = _get_audit_run(worker.store, task_id, "g1")
    assert run is not None and run.status == "failed"
    assert run.side_effect_state == "none"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None and attempt.send_status == "failed"


def test_manual_rerun_rotates_generation_and_allows_changed_work(tmp_path: Path):
    trigger = _message("请给目标 A 发送第一版")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(side_effect_state=SideEffectState.CONFIRMED),
                receipts=(_receipt("send-a", command_digest="a" * 64),),
            ),
            ScriptedRun(
                _result(side_effect_state=SideEffectState.CONFIRMED),
                receipts=(_receipt("send-b", command_digest="b" * 64),),
            ),
        ],
    )
    first_id = _enqueue(worker.store, trigger)
    assert worker.consume_once(max_tasks=1) == 1
    first_generation = worker.store.get_reply_task(first_id).execution_generation

    rerun = worker.store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-1",
        conversation_title="测试群",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text="请改为给目标 B 发送修订版",
        trigger_message_json=trigger.model_copy(
            update={"content": "请改为给目标 B 发送修订版"}
        ).model_dump_json(),
        attempt_id=1,
    )
    assert worker.consume_once(max_tasks=1) == 1

    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set status='pending', available_at='', locked_at='' "
            "where id=?",
            (first_id,),
        )
    assert worker.consume_once(max_tasks=1) == 1

    assert rerun.execution_generation != first_generation
    assert [generation for _task, generation, _context in runner.calls] == [
        first_generation,
        rerun.execution_generation,
    ]
    assert runner.calls[0][2].trigger_text == "请给目标 A 发送第一版"
    assert runner.calls[1][2].trigger_text == "请改为给目标 B 发送修订版"
    first_run = _get_audit_run(
        worker.store,
        first_id,
        first_generation,
    )
    second_run = _get_audit_run(
        worker.store,
        first_id,
        rerun.execution_generation,
    )
    assert first_run is not None and second_run is not None
    assert [
        receipt.operation_id
        for receipt in worker.store.list_agent_execution_receipts(first_run.id)
    ] == ["send-a"]
    assert [
        receipt.operation_id
        for receipt in worker.store.list_agent_execution_receipts(second_run.id)
    ] == ["send-b"]


def test_manual_review_reaches_agent_without_unrelated_attempt_fields(tmp_path: Path):
    trigger = _message("请根据审核意见重新处理")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION))],
    )
    _enqueue(worker.store, trigger)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="按审核后的版本处理。",
        direct_user_id="unrelated-direct-user",
        direct_open_dingtalk_id="unrelated-open-id",
        codex_session_id="unrelated-session",
        audit_documents_json='[{"path":"/private/unrelated"}]',
        audit_tool_events_json='[{"token":"unrelated-secret"}]',
    )
    worker.store.record_reply_feedback(
        attempt_id,
        feedback="先核对材料，再执行。",
        corrected_reply_text="按最新材料回复。",
    )
    worker.store.enqueue_manual_rerun_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        attempt_id=attempt_id,
    )

    assert worker.consume_once(max_tasks=1) == 1

    context = runner.calls[0][2]
    assert context.manual_rerun is not None
    assert context.manual_rerun.source_attempt_id == attempt_id
    assert context.manual_rerun.reviewer_feedback == "先核对材料，再执行。"
    assert context.manual_rerun.suggested_reply_text == "按最新材料回复。"
    rendered = context.render()
    assert "unrelated-direct-user" not in rendered
    assert "unrelated-open-id" not in rendered
    assert "unrelated-session" not in rendered
    assert "/private/unrelated" not in rendered
    assert "unrelated-secret" not in rendered


def test_completed_generation_is_not_executed_again(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION))],
    )
    task_id = _enqueue(worker.store, trigger)
    assert worker.consume_once(max_tasks=1) == 1
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set status='pending', available_at='', locked_at='' "
            "where id=?",
            (task_id,),
        )

    assert worker.consume_once(max_tasks=1) == 1

    assert len(runner.calls) == 1
    run = _get_audit_run(worker.store, task_id, "g1")
    assert run is not None and run.status == "completed"


def test_stale_processing_resumes_same_generation_and_session(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION), session_id="unused")],
    )
    task_id = _enqueue(worker.store, trigger)
    task = worker.store.claim_reply_task(task_id, now="2026-07-28 07:00:00")
    assert task is not None
    claim = _claim_consumer_run(
        worker.store,
        task.id,
        task.execution_generation,
        owner="dead-worker",
        lease_seconds=60,
        now="2026-07-28 07:00:00",
    )
    worker.store.set_agent_run_session(
        claim.run.id,
        "session-stale",
        owner="dead-worker",
        now="2026-07-28 07:00:00",
    )
    worker.store.upsert_conversation(
        task.conversation_id,
        task.conversation_title,
        task.single_chat,
        "session-stale",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task.id,),
        )

    worker.consume_once(max_tasks=1)

    assert runner.resume_session_ids == ["session-stale"]
    assert [generation for _task, generation, _context in runner.calls] == ["g1"]
    assert worker.store.get_reply_task(task_id).status == "done"


def test_stale_retryable_failed_run_resumes_same_generation_and_session(
    tmp_path: Path,
):
    trigger = _message("请读取材料后完成回复")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION), session_id="unused")],
    )
    task_id = _enqueue(worker.store, trigger)
    task = worker.store.claim_reply_task(task_id, now="2026-07-28 07:00:00")
    assert task is not None
    claim = _claim_consumer_run(
        worker.store,
        task.id,
        task.execution_generation,
        owner="dead-worker",
        lease_seconds=60,
        now="2026-07-28 07:00:00",
    )
    worker.store.set_agent_run_session(
        claim.run.id,
        "session-retry-after-restart",
        owner="dead-worker",
        now="2026-07-28 07:00:00",
    )
    worker.store.upsert_conversation(
        task.conversation_id,
        task.conversation_title,
        task.single_chat,
        "session-retry-after-restart",
    )
    worker.store.fail_agent_run(
        claim.run.id,
        {"code": "temporary_read_failure", "retryable": True},
        owner="dead-worker",
        now="2026-07-28 07:00:01",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task.id,),
        )

    assert worker.consume_once(max_tasks=1) == 1

    assert runner.resume_session_ids == ["session-retry-after-restart"]
    assert [generation for _task, generation, _context in runner.calls] == ["g1"]
    failed_run = worker.store.get_agent_run_for_turn(
        task_id,
        "g1",
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    completed_run = worker.store.get_agent_run_for_turn(
        task_id,
        "g1",
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=1,
    )
    assert failed_run is not None and failed_run.status == "failed"
    assert completed_run is not None and completed_run.status == "completed"
    assert worker.store.get_reply_task(task_id).status == "done"


def test_stale_recovery_keeps_turn_history_for_orchestrator_at_task_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    trigger = _message("请读取材料后完成回复")
    worker, _runner, _dws = _worker(
        tmp_path,
        [trigger],
        [],
        max_task_attempts=1,
    )
    notifications: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker,
        "_notify",
        lambda **kwargs: notifications.append(kwargs),
    )
    task_id = _enqueue(worker.store, trigger)

    first_task = worker.store.claim_reply_task(
        task_id,
        now="2026-07-28 07:00:00",
    )
    assert first_task is not None and first_task.attempts == 1
    first_claim = _claim_audit_run(
        worker.store,
        first_task.id,
        first_task.execution_generation,
        owner="dead-worker-1",
        lease_seconds=60,
        now="2026-07-28 07:00:00",
    )
    worker.store.fail_agent_run(
        first_claim.run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="dead-worker-1",
        now="2026-07-28 07:00:01",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task_id,),
        )

    worker._recover_stale_agent_reply_tasks()

    recovered_task = worker.store.get_reply_task(task_id)
    assert recovered_task is not None and recovered_task.status == "pending"
    assert recovered_task.attempts == 1
    assert notifications == []
    notifications.clear()
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='' where id=?",
            (task_id,),
        )

    second_task = worker.store.claim_reply_task(
        task_id,
        now="2026-07-28 07:01:00",
    )
    assert second_task is not None and second_task.attempts == 2
    second_claim = _claim_audit_run(
        worker.store,
        second_task.id,
        second_task.execution_generation,
        owner="dead-worker-2",
        lease_seconds=60,
        now="2026-07-28 07:01:00",
    )
    assert second_claim.claimed
    worker.store.fail_agent_run(
        second_claim.run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="dead-worker-2",
        now="2026-07-28 07:01:01",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task_id,),
        )

    worker._recover_stale_agent_reply_tasks()

    failed_task = worker.store.get_reply_task(task_id)
    assert failed_task is not None and failed_task.status == "pending"
    assert failed_task.error == "stale_agent_turn_recovery"
    assert failed_task.attempts == 2
    assert notifications == []


def test_stale_unknown_audit_requeues_and_recovers_same_session(tmp_path: Path):
    trigger = _message("请发送一次通知")
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    task_id = _enqueue(store, trigger)
    task = store.claim_reply_task(task_id, now="2026-07-28 07:00:00")
    assert task is not None
    proposal = _consumer_protocol_result(
        "proposal",
        "Prepared one reviewed notification.",
        proposal={
            "objective": "Send one notification.",
            "actions": [
                {
                    "description": "Send the notification.",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"group": "cid-1"},
                    "payload": {
                        "argv": [
                            "dws", "chat", "message", "send", "--group", "cid-1",
                            "--text", "approved", "--yes",
                        ]
                    },
                    "expected_verification": "Read the target conversation.",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "",
        },
    )
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="seed-consumer",
    ).run
    store.complete_agent_run(
        consumer.id,
        proposal.model_dump(mode="json"),
        owner="seed-consumer",
    )
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="dead-worker",
        lease_seconds=60,
        now="2026-07-28 07:00:00",
    )
    store.set_agent_run_session(
        claim.run.id,
        "session-stale",
        owner="dead-worker",
        now="2026-07-28 07:00:00",
    )
    store.append_agent_run_event(
        claim.run.id,
        _persisted_effect_evidence("send-1", "started"),
        owner="dead-worker",
        now="2026-07-28 07:00:01",
    )
    store.mark_expired_agent_run_unknown(
        claim.run.id,
        {"code": "expired_effect_requires_reconciliation", "retryable": False},
        expected_execution_generation=task.execution_generation,
        now="2026-07-28 07:02:00",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task.id,),
        )

    class SameSessionRecoveryAudit:
        def __init__(self) -> None:
            self.sessions: list[str] = []

        def recover(self, _task, _context, *, run):
            self.sessions.append(run.codex_session_id)
            owner = "same-session-recovery"
            recovered = store.claim_unknown_agent_run(run.id, owner=owner)
            assert recovered.claimed
            result = AuditAgentResult.model_validate(
                {
                    "outcome": "reconciled",
                    "summary": "Live readback was ambiguous.",
                    "proposal_revision": 0,
                    "side_effect_state": "unknown",
                    "feedback": None,
                    "external_result": None,
                    "reconciliation": [
                        {
                            "action_index": 0,
                            "disposition": "ambiguous",
                            "read_result_digest": "live-read-digest",
                        }
                    ],
                    "error": {
                        "code": "",
                        "retryable": False,
                        "authorization_required": False,
                    },
                }
            )
            store.persist_unknown_agent_run_result(
                run.id,
                result.model_dump(mode="json"),
                owner=owner,
                transcript_end_line=run.transcript_end_line,
            )

    audit_runner = SameSessionRecoveryAudit()
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=ContextOnlyDws([trigger]),
        codex=object(),
        agent_orchestrator=AgentOrchestrator(
            store=store,
            consumer=UnexpectedRoleRunner(),
            audit=audit_runner,
        ),
        channel_gates={"dingtalk": ReadyGate("dingtalk")},
        now_provider=lambda: NOW,
    )

    assert worker.consume_once(max_tasks=1) == 1

    assert audit_runner.sessions == ["session-stale"]
    run = _get_audit_run(store, task_id, "g1")
    assert run is not None
    assert run.status == "completed"
    assert run.side_effect_state == "unknown"
    assert store.get_reply_task(task_id).status == "done"
    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None and attempt.send_status == "needs_human"
    assert attempt.agent_run_id == run.id


def test_context_reuses_confirmed_fact_and_does_not_pre_read_material(tmp_path: Path):
    fact_value = "value-4827-zeta"
    context_fact = _message(
        json.dumps({"confirmed_field": fact_value}),
        message_id="msg-fact",
    )
    trigger = _message(
        "请复用上下文中的已确认字段，不要再次询问。",
        message_id="msg-2",
    )
    executor = ConfirmedFactProtocolExecutor(fact_value)
    worker, dws = _worker_with_protocol_executor(
        tmp_path,
        [context_fact, trigger],
        executor,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert executor.fact_was_present is True
    assert fact_value in executor.prompts[0]
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-2")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == ""
    assert dws.forbidden_material_reads == []


def test_confirmed_fact_protocol_agent_asks_only_when_fact_is_absent(tmp_path: Path):
    fact_value = "value-4827-zeta"
    trigger = _message("请复用上下文中的已确认字段。")
    executor = ConfirmedFactProtocolExecutor(fact_value)
    worker, _dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        executor,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert executor.fact_was_present is False
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "confirmed_fact_missing"


@pytest.mark.parametrize(
    ("action", "send_status"),
    [("agent_run", "skipped"), ("no_action", "completed")],
)
def test_skipped_attempt_is_not_exposed_as_completed_prior_receipt(
    tmp_path: Path,
    action: str,
    send_status: str,
):
    trigger = _message("无需执行外部动作")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION, summary="无需动作。"))],
    )
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="测试群",
        trigger_message_id="msg-1",
        trigger_sender="ET",
        trigger_text=trigger.content,
        action=action,
        sensitivity_kind="general",
        codex_reason="No external action was required.",
        audit_summary="No external action was required.",
        send_status=send_status,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    context = runner.calls[0][2]
    assert context.prior_receipts == ()
    assert "No external action was required" not in context.render()


def test_calendar_context_passes_raw_event_id_and_exact_live_read_command(
    tmp_path: Path,
):
    trigger = _message(
        "dingtalk://dingtalkclient/action/open_mini_app?page=detail%3FuniqueId%3Devent-1",
        raw_payload={"eventId": "event-1"},
    )
    worker, runner, dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION, summary="已检查日程。"))],
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    material = next(
        item
        for item in runner.calls[0][2].materials
        if item.kind == "dingtalk_calendar"
    )
    assert '"event_id": "event-1"' in material.reference
    assert material.read_commands == (
        "dws calendar event get --id event-1 --format json",
    )
    assert dws.forbidden_material_reads == []


@pytest.mark.parametrize(
    ("oa_state", "raw_payload", "live_output", "attempt_status", "effectful"),
    [
        (
            "complete_form",
            {"processInstanceId": "proc-1", "taskId": "task-1"},
            {
                "tasks": [
                    {"task_id": "task-1", "status": "running", "current_user": True}
                ]
            },
            "completed",
            True,
        ),
        (
            "instance_id_only",
            {"processInstanceId": "proc-1"},
            {
                "tasks": [
                    {"task_id": "task-live", "status": "running", "current_user": True}
                ]
            },
            "completed",
            True,
        ),
        (
            "ambiguous_candidates",
            {"processInstanceId": "proc-1"},
            {
                "tasks": [
                    {"task_id": "task-a", "status": "running", "current_user": True},
                    {"task_id": "task-b", "status": "running", "current_user": True},
                ]
            },
            "needs_human",
            False,
        ),
        (
            "task_completed",
            {"processInstanceId": "proc-1"},
            {
                "tasks": [
                    {"task_id": "task-1", "status": "completed", "current_user": True}
                ]
            },
            "skipped",
            False,
        ),
        (
            "task_not_current_user",
            {"processInstanceId": "proc-1"},
            {
                "tasks": [
                    {"task_id": "task-1", "status": "running", "current_user": False}
                ]
            },
            "completed",
            True,
        ),
    ],
)
def test_oa_runtime_agent_executes_live_read_commands_and_decides_from_output(
    tmp_path: Path,
    oa_state: str,
    raw_payload: dict[str, object],
    live_output: dict[str, object],
    attempt_status: str,
    effectful: bool,
):
    trigger = _message(
        "请审核这个审批",
        raw_payload=raw_payload,
    )
    native_executor = NativeCommandStub(live_output)
    codex_executor = OaProtocolExecutor(native_executor)
    worker, dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        codex_executor,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert "proc-1" in " ".join(codex_executor.read_commands)
    assert any(
        "read-oa-approval-detail --instance-id proc-1" in command
        for command in codex_executor.read_commands
    )
    assert any(
        "dws oa approval tasks --instance-id proc-1" in command
        for command in codex_executor.read_commands
    )
    assert native_executor.calls[: len(codex_executor.read_commands)] == (
        codex_executor.read_commands
    )
    assert dws.forbidden_material_reads == []
    task = worker.store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    task_id = task.id
    run = _get_audit_run(worker.store, task_id, "g1")
    assert run is not None
    assert bool(native_executor.write_calls) is effectful
    assert worker.store.list_agent_execution_receipts(run.id) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == attempt_status
    if oa_state == "instance_id_only":
        assert "task-live" in native_executor.write_calls[0]


def test_service_waits_when_agent_cannot_form_requested_execution_proposal(
    tmp_path: Path,
):
    trigger = _message(
        "请修复并验证这个文档关联的问题 "
        "https://alidocs.dingtalk.com/i/nodes/diagnostic-doc"
    )
    native_executor = NativeCommandStub({})
    codex_executor = DiagnosisOnlyProtocolExecutor(native_executor)
    worker, dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        codex_executor,
        max_task_attempts=1,
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    task = worker.store.get_reply_task(task_id)
    assert task is not None and task.status == "done"
    run = _get_audit_run(worker.store, task_id, "g1")
    assert run is not None
    assert run.status == "completed"
    assert run.side_effect_state == "none"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "executable_proposal_missing"
    assert len(native_executor.calls) == 1
    assert native_executor.write_calls == []
    assert dws.forbidden_material_reads == []


def test_nonzero_native_write_uses_failed_retry_path_in_real_runner_protocol(
    tmp_path: Path,
):
    trigger = _message("请发送一次通知")
    executor = FailedWriteProtocolExecutor()
    worker, _dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        executor,
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    task = worker.store.get_reply_task(task_id)
    assert task is not None and task.status == "pending"
    run = _get_audit_run(worker.store, task_id, "g1")
    assert run is not None
    assert run.status == "failed"
    assert run.side_effect_state == "none"
    assert worker.store.list_agent_execution_receipts(run.id) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "audit_retry_exhausted"
    audit_runs = [
        item
        for item in worker.store.list_agent_runs_for_task_generation(task_id, "g1")
        if item.role is AgentRole.AUDIT
    ]
    assert [item.turn_attempt for item in audit_runs] == [0, 1]
    assert len({item.operation_id for item in audit_runs}) == 1
