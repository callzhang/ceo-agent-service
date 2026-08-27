from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.agent_context import AuditTurnContext
from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    AuditOutcome,
)
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_result import AgentError, EffectKind, ResultParseError, SideEffectState
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import RuntimeKind
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_turn_runner import (
    AgentTurnProcess,
    AgentTurnRunResult,
    ProcessExecutor,
    _action_completion_accounting,
    _action_receipt_operation_id,
    _actions_have_required_readbacks,
    _agent_process_error_code,
    _message_rendered_text_digest,
    _metadata_matches_action,
    _result_parse_error_detail,
    unknown_reconciliation_retry_at,
)
from app.agent_wire_contracts import parse_audit_agent_wire_result
from app.audit_rules import render_audit_rules
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_history import extract_codex_mcp_tool_results_from_session
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.consumer_agent import audit_developer_instructions
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    dingtalk_message_text,
    describe_native_command,
    has_noninteractive_confirmation,
    native_command_argv,
)
from app.store import (
    AgentRole,
    AgentRun,
    AutoReplyStore,
    ReplyTask,
)
from app.wechat.codex_safety import ControlledCliConfig, make_audit_agent_command

RECOVERY_WRITE_ALLOWLIST_ENV = "CEO_AGENT_RECOVERY_WRITE_ALLOWLIST"
EFFECT_INTENT_CONTEXT_ENV = "CEO_AGENT_EFFECT_INTENT_CONTEXT"
SERVICE_ROOT = Path(__file__).resolve().parent.parent


class AuditAgentRunner:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        codex_bin: str = "codex",
        runtime_config: AgentRuntimeConfig | None = None,
        runtime_router: AgentRuntimeRouter | None = None,
        codex_adapter: CodexRuntimeAdapter | None = None,
        claude_adapter: ClaudeRuntimeAdapter | None = None,
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        dry_run: bool = False,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.runtime_config = runtime_config
        self.runtime_router = runtime_router
        self.codex_adapter = codex_adapter
        self.claude_adapter = claude_adapter
        self.executor = executor
        self.owner = owner or f"audit-agent-{uuid4().hex}"
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.dry_run = dry_run
        self.refresh_runtime_capabilities = refresh_runtime_capabilities

    @staticmethod
    def _required_capabilities(
        context: AuditTurnContext,
        *,
        recovery_phase: str,
    ) -> frozenset[str]:
        required = {
            "task_context",
            f"channel:{context.task.channel}",
            "mcp:agent_cli:reviewed_read",
            "native_cli:reviewed",
            "mcp:memory_connector:read",
        }
        if context.task.channel == "dingtalk":
            required.add("native_cli:dws")
        elif context.task.channel in {"lark", "feishu"}:
            required.add("native_cli:lark")
        if context.task.image_paths:
            required.add("image_input")
        if recovery_phase != "reconcile":
            required.add("mcp:agent_cli:reviewed_write")
        # Reconciliation answers only whether an already-unknown effect
        # happened.  It must be able to run with persisted operation context
        # even when an old Consumer Skill receipt has since changed.  The
        # recovery command remains strictly read-only.
        if recovery_phase != "reconcile":
            for receipt in context.consumer_skills:
                required.add(f"reviewed_skill:{receipt.name}:{receipt.sha256}")
        return frozenset(required)

    def run(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        turn_attempt: int,
        parent_agent_run_id: int,
        frozen_delivery_retry: bool = False,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        if context.task.task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        rendered_rules = render_audit_rules(AgentRole.AUDIT)
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        if skill_failure := self._skill_receipt_gate(
            task,
            context,
            run=claim.run,
            recovery_phase="",
            allow_missing_receipts=frozen_delivery_retry,
        ):
            return skill_failure
        recipient_type_mismatches = _typed_direct_recipient_mismatches(context)
        if recipient_type_mismatches and not frozen_delivery_retry:
            return self._return_invalid_candidate(
                claim.run,
                recipient_type_mismatches=recipient_type_mismatches,
            )
        executed = self._execute_claimed(
            task,
            context,
            run=claim.run,
            rendered_rules=rendered_rules,
            frozen_delivery_retry=frozen_delivery_retry,
        )
        # A normal candidate that reaches the reviewed-write tool without a
        # valid persisted authorization contract cannot be repaired by retrying
        # Audit. Return it to Consumer for a fresh typed proposal instead of
        # leaving the task in an unknown/retry loop.
        if executed.result.error.code == "reviewed_write_not_authorized":
            return self._requeue_for_consumer(
                task,
                claim.run,
                code="audit_candidate_invalid",
                summary=(
                    "候选写入缺少可验证的 reviewed-write 授权合同，已退回 Consumer 重新生成；"
                    "本轮未确认外部写入回执。"
                ),
            )
        return executed

    def recover(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        if context.task.task_id != task.id or run.reply_task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        if run.role is not AgentRole.AUDIT or run.status != "unknown":
            raise ValueError("audit recovery requires an unknown Audit run")
        if run.execution_generation != task.execution_generation:
            raise ValueError("audit recovery generation mismatch")
        if (
            run.proposal_revision != context.proposal_revision
            or run.operation_id != context.operation_id
        ):
            raise ValueError("audit recovery identity mismatch")
        rendered_rules = render_audit_rules(AgentRole.AUDIT)
        claim = self.store.claim_unknown_agent_run(
            run.id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        try:
            self._backfill_persisted_direct_delivery_receipt(
                task,
                context,
                claim.run,
            )
            if _persisted_single_direct_delivery(task, context, self.store):
                return self._complete_persisted_direct_delivery_recovery(
                    claim.run,
                )
            expected_effect_actions = tuple(
                _expected_effect_action(action, self.effects, action_index=index)
                for index, action in enumerate(context.proposal.actions)
            )
            completed, all_effects_closed = _action_completion_accounting(
                claim.run.tool_events,
                self.store.list_agent_execution_receipts(claim.run.id),
                expected_effect_actions,
                operation_id=claim.run.operation_id,
                registry=self.effects,
            )
            if (
                completed == set(range(len(expected_effect_actions)))
                and all_effects_closed
                and _actions_have_required_readbacks(
                    claim.run.tool_events,
                    expected_effect_actions,
                    self.effects,
                )
            ):
                return self._complete_verified_effect_recovery(
                    task,
                    context,
                    claim.run,
                    completed=completed,
                )
            if skill_failure := self._skill_receipt_gate(
                task,
                context,
                run=claim.run,
                recovery_phase="reconcile",
            ):
                return skill_failure
            database_absence = _database_delivery_absence_reconciliation(
                self.store,
                task,
                context,
                claim.run,
            )
            if database_absence:
                return self._requeue_absent_direct_delivery(task, claim.run)
            executed = self._execute_claimed(
                task,
                context,
                run=claim.run,
                rendered_rules=rendered_rules,
                recovery_phase="reconcile",
            )
            if executed.result.error.code == "audit_recovery_action_not_authorized":
                return self._requeue_for_consumer(
                    task,
                    claim.run,
                    code="audit_recovery_candidate_invalid",
                    summary=(
                        "历史写入候选缺少可验证的命令授权合同，已退回 Consumer 重新生成；"
                        "本轮未确认外部写入回执。"
                    ),
                )
            return executed
        except Exception as exc:
            # A legacy unknown run may have a persisted started marker but no
            # valid one-shot authorization contract (for example, an older
            # proposal stored only free-form write text).  Repeating that
            # recovery can never produce a valid receipt.  Return it to
            # Consumer A for a fresh, typed proposal instead of leaving the
            # unknown run cycling forever.
            recovery_code = _audit_recovery_error_code(exc)
            if (
                recovery_code == "audit_recovery_action_not_authorized"
                and claim.run.effect_receipt_count == 0
                and claim.run.effect_unreviewed_count == 0
            ) or (
                recovery_code in {
                    "audit_reconciliation_evidence_mismatch",
                    "audit_recovery_result_invalid",
                }
                and claim.run.side_effect_state == "none"
                and claim.run.effect_receipt_count == 0
                and claim.run.effect_unreviewed_count == 0
            ):
                return self._requeue_for_consumer(
                    task,
                    claim.run,
                    code="audit_recovery_candidate_invalid",
                    summary=(
                        "历史候选缺少可验证的恢复证据或授权合同，已退回 Consumer 重新生成；"
                        "本轮未确认外部写入回执。"
                    ),
                )
            self._defer_claimed_unknown_recovery(claim.run, exc)
            raise

    def _backfill_persisted_direct_delivery_receipt(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        run: AgentRun,
    ) -> None:
        """Restore an omitted direct-delivery ledger only from its own receipt."""
        runtime_attempt = next(
            (
                attempt
                for attempt in reversed(self.store.list_agent_runtime_attempts(run.id))
                if attempt.session_id
                and attempt.agent_run_id == run.id
                and attempt.workload_kind == "agent_run"
                and attempt.workload_key == str(run.id)
            ),
            None,
        )
        if (
            runtime_attempt is None
            or runtime_attempt.runtime_kind != RuntimeKind.CODEX_CLI.value
            or self.store.has_sent_reply_for_trigger(
                task.conversation_id,
                task.trigger_message_id,
            )
        ):
            return
        expected_actions = tuple(
            _expected_effect_action(action, self.effects, action_index=index)
            for index, action in enumerate(context.proposal.actions)
        )
        process = AgentTurnProcess(
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            runtime_config=self.runtime_config,
            runtime_router=self.runtime_router,
            codex_adapter=self.codex_adapter,
            claude_adapter=self.claude_adapter,
            mcp_effect_registry=self.effects,
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
        )
        for payload in extract_codex_mcp_tool_results_from_session(
            runtime_attempt.session_id,
        ):
            try:
                event = process._normalized_effect_event(
                    payload,
                    read_only=False,
                    operation_id=run.operation_id,
                )
            except AgentReadOnlyViolationError:
                # This is historical evidence only.  A command that was
                # reviewed by an older CLI policy may no longer be registered
                # today; it cannot prove a receipt, but it must not block the
                # independent read-only reconciliation turn.
                continue
            item = event.get("item") if event is not None else None
            metadata = item.get("metadata") if isinstance(item, dict) else None
            if not isinstance(metadata, dict) or not any(
                _metadata_matches_action(metadata, action)
                for action in expected_actions
            ):
                continue
            process._record_direct_send_receipt(event, payload, run=run)
            if self.store.has_sent_reply_for_trigger(
                task.conversation_id,
                task.trigger_message_id,
            ):
                return

    def _complete_persisted_direct_delivery_recovery(
        self,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        result = AuditAgentResult(
            outcome=AuditOutcome.EXECUTED,
            summary="A persisted direct-delivery receipt matches the approved action.",
            proposal_revision=run.proposal_revision,
            side_effect_state=SideEffectState.CONFIRMED,
            feedback=None,
            external_result={
                "operation_id": run.operation_id,
                "verification_summary": (
                    "The local delivery ledger contains the matching successful "
                    "direct-message receipt."
                ),
                "live_result_reference": {
                    "evidence": "persisted_direct_delivery_receipt",
                },
            },
            reconciliation=(),
            error=AgentError(),
        )
        completed = self.store.complete_agent_run(
            run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state=SideEffectState.CONFIRMED.value,
            transcript_end_line=run.transcript_end_line,
            expected_status="unknown",
        )
        return AgentTurnRunResult(
            run_id=completed.id,
            result=result,
            transcript_start_line=run.transcript_end_line,
            transcript_end_line=completed.transcript_end_line,
        )

    def _complete_verified_effect_recovery(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        run: AgentRun,
        *,
        completed: set[int],
    ) -> AgentTurnRunResult[AuditAgentResult]:
        """Complete a crashed write only after its controlled evidence closes."""
        result = AuditAgentResult(
            outcome=AuditOutcome.EXECUTED,
            summary=(
                "Completed tool events and target-matched live readbacks cover "
                "every approved action."
            ),
            proposal_revision=run.proposal_revision,
            side_effect_state=SideEffectState.CONFIRMED,
            feedback=None,
            external_result={
                "operation_id": run.operation_id,
                "verification_summary": (
                    "Persisted controlled write events and live readbacks confirm "
                    "every approved action."
                ),
                "live_result_reference": {
                    "recovery_action_indexes": sorted(completed),
                    "evidence": "completed_tool_events_and_readbacks",
                },
            },
            reconciliation=(),
            error=AgentError(),
        )
        completed_run = self.store.complete_agent_run(
            run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state=SideEffectState.CONFIRMED.value,
            transcript_end_line=run.transcript_end_line,
            expected_status="unknown",
        )
        _record_verified_chat_delivery_receipt(
            self.store,
            task,
            context,
            audit_run=completed_run,
        )
        return AgentTurnRunResult(
            run_id=completed_run.id,
            result=result,
            transcript_start_line=run.transcript_end_line,
            transcript_end_line=completed_run.transcript_end_line,
        )

    def execute_recovery(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        if run.status != "unknown" or not run.final_result_json:
            raise ValueError(
                "audit recovery execution requires persisted reconciliation"
            )
        reconciliation = AuditAgentResult.model_validate_json(run.final_result_json)
        if reconciliation.outcome.value != "reconciled":
            raise ValueError("audit recovery execution requires reconciled outcome")
        absent = frozenset(
            entry.action_index
            for entry in reconciliation.reconciliation
            if entry.disposition.value == "absent"
        )
        if not absent:
            raise ValueError("audit recovery execution requires absent actions")
        claim = self.store.claim_unknown_agent_run(
            run.id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        try:
            if skill_failure := self._skill_receipt_gate(
                task,
                context,
                run=claim.run,
                recovery_phase="execute",
            ):
                return skill_failure
            if _database_delivery_absence_reconciliation(
                self.store,
                task,
                context,
                claim.run,
            ):
                return self._requeue_absent_direct_delivery(task, claim.run)
            expected_effect_actions = tuple(
                _expected_effect_action(action, self.effects, action_index=index)
                for index, action in enumerate(context.proposal.actions)
            )
            completed, all_effects_closed = _action_completion_accounting(
                claim.run.tool_events,
                self.store.list_agent_execution_receipts(claim.run.id),
                expected_effect_actions,
                operation_id=claim.run.operation_id,
                registry=self.effects,
            )
            unresolved_absent = absent - completed
            if not unresolved_absent:
                if (
                    completed != set(range(len(expected_effect_actions)))
                    or not all_effects_closed
                ):
                    raise RuntimeError("audit_recovery_effect_unresolved")
                if not _actions_have_required_readbacks(
                    claim.run.tool_events,
                    expected_effect_actions,
                    self.effects,
                ):
                    raise RuntimeError("audit_external_readback_missing")
                result = AuditAgentResult(
                    outcome=AuditOutcome.EXECUTED,
                    summary=(
                        "All recovery actions already have completed tool evidence "
                        "and required readbacks."
                    ),
                    proposal_revision=claim.run.proposal_revision,
                    side_effect_state=SideEffectState.CONFIRMED,
                    feedback=None,
                    external_result={
                        "operation_id": claim.run.operation_id,
                        "verification_summary": (
                            "Completed tool events, persisted receipts, and live "
                            "readbacks cover every action."
                        ),
                        "live_result_reference": {
                            "recovery_action_indexes": sorted(completed),
                            "evidence": "completed_tool_events_receipts_and_readbacks",
                        },
                    },
                    reconciliation=(),
                    error=AgentError(),
                )
                completed_run = self.store.complete_agent_run(
                    claim.run.id,
                    result.model_dump(mode="json"),
                    owner=self.owner,
                    side_effect_state=SideEffectState.CONFIRMED.value,
                    transcript_end_line=claim.run.transcript_end_line,
                    expected_status="unknown",
                )
                return AgentTurnRunResult(
                    run_id=completed_run.id,
                    result=result,
                    transcript_start_line=claim.run.transcript_end_line,
                    transcript_end_line=completed_run.transcript_end_line,
                )
            authorizations = _recovery_authorizations(
                run, context, unresolved_absent, self.effects
            )
            if len(authorizations) != len(unresolved_absent):
                return self._requeue_for_consumer(
                    task,
                    claim.run,
                    code="audit_recovery_candidate_invalid",
                    summary=(
                        "The absent action cannot execute under the current command "
                        "contract; Consumer Agent A must produce a valid replacement."
                    ),
                )
            executed = self._execute_claimed(
                task,
                context,
                run=claim.run,
                rendered_rules=render_audit_rules(AgentRole.AUDIT),
                recovery_phase="execute",
                authorized_recovery_actions=unresolved_absent,
                recovery_authorizations=authorizations,
            )
            if executed.result.error.code == "audit_recovery_action_not_authorized":
                return self._requeue_for_consumer(
                    task,
                    claim.run,
                    code="audit_recovery_candidate_invalid",
                    summary=(
                        "历史写入候选缺少可验证的命令授权合同，已退回 Consumer 重新生成；"
                        "本轮未确认外部写入回执。"
                    ),
                )
            return executed
        except Exception as exc:
            if _audit_recovery_error_code(exc) == "audit_recovery_action_not_authorized":
                return self._requeue_for_consumer(
                    task,
                    claim.run,
                    code="audit_recovery_candidate_invalid",
                    summary=(
                        "历史写入候选缺少可验证的命令授权合同，已退回 Consumer 重新生成；"
                        "本轮未确认外部写入回执。"
                    ),
                )
            self._defer_claimed_unknown_recovery(claim.run, exc)
            raise

    def _requeue_absent_direct_delivery(
        self,
        task: ReplyTask,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        return self._requeue_for_consumer(
            task,
            run,
            code="persisted_delivery_absent",
            summary=(
                "No persisted delivery record exists for the exact trigger; "
                "the direct chat action was requeued in a new generation."
            ),
        )

    def _requeue_for_consumer(
        self,
        task: ReplyTask,
        run: AgentRun,
        *,
        code: str,
        summary: str,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        self.store.resolve_unknown_agent_run_absent(
            run.id,
            task.id,
            code=code,
            owner=self.owner,
            transcript_end_line=run.transcript_end_line,
        )
        result = AuditAgentResult(
            outcome=AuditOutcome.FAILED,
            summary=summary,
            proposal_revision=run.proposal_revision,
            side_effect_state=SideEffectState.NONE,
            feedback=None,
            external_result=None,
            reconciliation=(),
            error=AgentError(code=code, retryable=True),
        )
        return AgentTurnRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=run.transcript_end_line,
        )

    def _return_invalid_candidate(
        self,
        run: AgentRun,
        *,
        recipient_type_mismatches: tuple[int, ...],
    ) -> AgentTurnRunResult[AuditAgentResult]:
        invalid_details: list[str] = []
        if recipient_type_mismatches:
            listed = ", ".join(str(index) for index in recipient_type_mismatches)
            invalid_details.append(
                f"Candidate action indexes {listed} use the trigger's open-DingTalk ID as a user ID."
            )
        result = AuditAgentResult(
            outcome=AuditOutcome.REVISION_REQUIRED,
            summary="The candidate uses an invalid typed recipient identifier.",
            proposal_revision=run.proposal_revision,
            side_effect_state=SideEffectState.NONE,
            feedback=AuditFeedback(
                rule="The candidate must use the correct typed recipient identifier.",
                observation=" ".join(invalid_details),
                requested_revision=(
                    "Return the same intended operation with the correct recipient identifier. For a "
                    "single-chat recipient, use --user only with sender_user_id and "
                    "use --open-dingtalk-id with sender_open_dingtalk_id. Preserve the "
                    "business recipient and payload."
                ),
            ),
            external_result=None,
            reconciliation=(),
            error=AgentError(),
        )
        completed = self.store.complete_agent_run(
            run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state=SideEffectState.NONE.value,
        )
        return AgentTurnRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=completed.transcript_end_line,
            transcript_end_line=completed.transcript_end_line,
        )

    def _return_missing_skill_receipts(
        self,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        result = AuditAgentResult(
            outcome=AuditOutcome.REVISION_REQUIRED,
            summary="The candidate has no verified Consumer Skill receipt.",
            proposal_revision=run.proposal_revision,
            side_effect_state=SideEffectState.NONE,
            feedback=AuditFeedback(
                rule=(
                    "Every applicable business and operation Skill requires a "
                    "verified Consumer A receipt before Audit can execute."
                ),
                observation=(
                    "The applicable candidate contains actions but no verified "
                    "Consumer Skill receipt was supplied."
                ),
                requested_revision=(
                    "Consumer Agent A must independently read all applicable Skills "
                    "and return a replacement candidate with verified receipts."
                ),
            ),
            external_result=None,
            reconciliation=(),
            error=AgentError(),
        )
        completed = self.store.complete_agent_run(
            run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state=SideEffectState.NONE.value,
        )
        return AgentTurnRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=completed.transcript_end_line,
            transcript_end_line=completed.transcript_end_line,
        )

    def _skill_receipt_gate(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
        recovery_phase: str,
        allow_missing_receipts: bool = False,
    ) -> AgentTurnRunResult[AuditAgentResult] | None:
        if context.consumer_skills or allow_missing_receipts:
            return None
        if recovery_phase == "reconcile":
            return None
        if recovery_phase == "execute":
            return self._requeue_for_consumer(
                task,
                run,
                code="audit_skill_receipts_missing",
                summary=(
                    "Recovery cannot execute an absent action without verified "
                    "Consumer Skill receipts; the task was returned to Consumer A."
                ),
            )
        if recovery_phase:
            raise ValueError("invalid audit recovery phase")
        return self._return_missing_skill_receipts(run)

    def _execute_claimed(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
        rendered_rules: str,
        recovery_phase: str = "",
        authorized_recovery_actions: frozenset[int] = frozenset(),
        recovery_authorizations: tuple[dict[str, object], ...] = (),
        frozen_delivery_retry: bool = False,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        expected_effect_actions = tuple(
            _expected_effect_action(action, self.effects, action_index=index)
            for index, action in enumerate(context.proposal.actions)
        )
        if recovery_phase == "reconcile":
            expected_effect_actions = _bind_started_action_contracts(
                expected_effect_actions,
                run.tool_events,
            )
        write_authorizations = (
            recovery_authorizations
            if recovery_phase == "execute"
            else _initial_write_authorizations(run, expected_effect_actions)
            if not recovery_phase and not self.dry_run
            else ()
        )
        if write_authorizations:
            self.store.prepare_agent_effect_intents(
                run.id,
                write_authorizations,
                owner=self.owner,
            )
        process = AgentTurnProcess[AuditAgentResult](
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            runtime_config=self.runtime_config,
            runtime_router=self.runtime_router,
            codex_adapter=self.codex_adapter,
            claude_adapter=self.claude_adapter,
            mcp_effect_registry=self.effects,
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
        )
        turn_prompt = (
            _recovery_prompt(run, context, expected_effect_actions, self.effects)
            if recovery_phase == "reconcile"
            else _recovery_execute_prompt(
                run,
                context,
                recovery_authorizations,
            )
            if recovery_phase == "execute"
            else _frozen_delivery_retry_prompt(
                run,
                context,
                write_authorizations,
            )
            if frozen_delivery_retry
            else context.render()
        )
        if write_authorizations and recovery_phase != "execute":
            turn_prompt += _write_authorization_prompt(write_authorizations)
        turn_prompt += (
            "\n\n### Needs Human Display Contract\n"
            "Return needs_human only for a reusable policy gap: existing rules "
            "cannot determine how this class of cases should be handled. Its "
            "summary and every decision option must describe the rule key, the "
            "recurring pattern, and mutually exclusive policy choices in concise "
            "Simplified Chinese. Do not ask Derek how to finish this one task, "
            "and do not convert a technical failure or missing runtime evidence "
            "into needs_human."
        )
        if self.dry_run:
            turn_prompt += (
                "\n\n### Dry Run Context\n"
                "Use read-only tools to complete the independent review. Return "
                "feedback_provided normally when the candidate must change. When "
                "the candidate is executable but execution is suppressed only by "
                "dry-run, return dry_run with error code "
                "dry_run_execution_suppressed and side_effect_state none. Do not "
                "return needs_human and do not provide decision_options: the "
                "simulation setting is not a management decision."
            )
        return process.execute(
            run=run,
            prompt=turn_prompt,
            # A recovery has complete persisted task, proposal, and operation
            # context. Do not resume an interrupted execution thread: a terminal
            # Codex session cannot produce the independent read-only evidence that
            # reconciliation requires. Keep the original session on the run for
            # audit history and start a fresh, isolated recovery turn instead.
            session_id=None if recovery_phase else run.codex_session_id or None,
            developer_instructions=audit_developer_instructions(
                rendered_rules,
                allow_write=(not self.dry_run and recovery_phase != "reconcile"),
                recovery_reconciliation=recovery_phase == "reconcile",
                frozen_delivery_retry=frozen_delivery_retry,
            ),
            configure_command=lambda command: make_audit_agent_command(
                command,
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli"),
                    cwd=str(SERVICE_ROOT),
                    env=(
                        (
                            RECOVERY_WRITE_ALLOWLIST_ENV,
                            json.dumps(
                                write_authorizations,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                        (
                            EFFECT_INTENT_CONTEXT_ENV,
                            json.dumps(
                                {"db_path": str(self.store.path), "run_id": run.id},
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    if write_authorizations
                    else (),
                ),
                allow_write=not self.dry_run and recovery_phase != "reconcile",
            ),
            parse_result=parse_audit_agent_wire_result,
            persist_conversation_session=False,
            expected_effect_actions=expected_effect_actions,
            recovery_phase=recovery_phase,
            authorized_recovery_actions=authorized_recovery_actions,
            recovery_authorizations={
                str(entry["authorization_id"]): int(entry["action_index"])
                for entry in recovery_authorizations
            },
            allow_effectful_tools=(not self.dry_run and recovery_phase != "reconcile"),
            image_paths=[Path(path) for path in context.task.image_paths],
            required_skill_receipts=(
                ()
                if recovery_phase == "reconcile" or frozen_delivery_retry
                else context.consumer_skills
            ),
            required_capabilities=self._required_capabilities(
                context,
                recovery_phase=recovery_phase,
            ),
        )

    def _defer_claimed_unknown_recovery(
        self,
        run: AgentRun,
        exc: Exception,
    ) -> None:
        persisted = self.store.get_agent_run(run.id)
        if (
            persisted is None
            or persisted.status != "unknown"
            or persisted.lease_owner != self.owner
        ):
            return
        structured_error = {
            "code": _audit_recovery_error_code(exc),
            "retryable": True,
        }
        if detail := _audit_recovery_error_detail(exc):
            structured_error["detail"] = detail
        self.store.defer_unknown_agent_run_reconciliation(
            run.id,
            structured_error,
            owner=self.owner,
            expected_execution_generation=run.execution_generation,
            next_attempt_at=unknown_reconciliation_retry_at(
                persisted.reconciliation_attempts
            ),
            suspended=False,
        )


def _audit_recovery_error_code(exc: Exception) -> str:
    runtime_code = getattr(exc, "code", "")
    if isinstance(runtime_code, str) and runtime_code.startswith("runtime_"):
        return runtime_code
    code = _agent_process_error_code(exc)
    if code != "codex_process_failed":
        return code
    detail = str(exc).strip()
    if detail in {
        "codex_process_failed",
        "runtime_unclassified",
        "runtime_session_evidence_missing",
    }:
        return detail
    if detail.startswith("audit_"):
        return detail
    return "audit_recovery_result_invalid"


def _audit_recovery_error_detail(exc: Exception) -> str:
    """Persist only stable recovery diagnostics, never provider output."""
    if isinstance(exc, ResultParseError):
        return _result_parse_error_detail(exc)
    reason = getattr(exc, "reason", "")
    if isinstance(reason, str) and reason.startswith("no_eligible_route:"):
        return reason[:512]
    return ""


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_effect_action(
    action,
    registry: McpToolEffectRegistry,
    *,
    action_index: int,
) -> dict[str, object]:
    expected = {
        "action_index": action_index,
        "capability": action.capability,
        "operation": action.operation,
        "arguments_digest": _json_digest(action.payload),
        "target_identifiers": action.target,
    }
    descriptor = describe_native_command(
        {"type": "command_execution", **action.payload}
    )
    if descriptor is not None:
        argv = native_command_argv(
            {"type": "command_execution", **action.payload}
        )
        # ``operation`` is prose generated by Consumer A. The argv is the
        # executable contract, and native metadata derives its canonical path
        # and target identifiers. Requiring both spellings to match made a
        # harmless label reject a valid command before Audit could review it.
        # The command parser is the authority for executable contracts.  A
        # Consumer-generated capability is descriptive metadata and must not
        # invalidate an otherwise allow-listed command with a verified argv.
        expected_capability = f"agent_cli.{descriptor.cli}"
        expected["operation_contract_valid"] = (
            expected_capability == f"agent_cli.{descriptor.cli}"
            and (
                descriptor.cli != "dws"
                or (argv is not None and has_noninteractive_confirmation(argv))
            )
        )
        expected["capability"] = expected_capability
        expected["operation"] = descriptor.command_path
        expected["arguments_digest"] = _json_digest({"argv": list(argv or ())})
        expected["operation_digest"] = descriptor.command_digest
        expected["target_identifiers"] = descriptor.target_identifiers
        message_text = dingtalk_message_text(tuple(argv or ()))
        if descriptor.cli == "dws" and message_text:
            expected["message_text_digest"] = hashlib.sha256(
                message_text.encode("utf-8")
            ).hexdigest()
            expected["message_rendered_text_digest"] = _message_rendered_text_digest(
                message_text
            )
        if descriptor.command_path == "chat +dm":
            recipient = action.target.get("recipient_open_dingtalk_id")
            if isinstance(recipient, str) and recipient:
                expected["readback_target_identifiers"] = {
                    "open-dingtalk-id": recipient
                }
            else:
                recipient_user_id = action.target.get("recipient_user_id")
                if isinstance(recipient_user_id, str) and recipient_user_id:
                    expected["readback_target_identifiers"] = {
                        "user": recipient_user_id
                    }
        expected["reviewed_server"] = "agent_cli"
        expected["reviewed_tool"] = "execute_reviewed_write"
    else:
        call = registry.classify(
            {
                "type": "mcp_tool_call",
                "server": action.capability,
                "tool": action.operation,
                "arguments": action.payload,
            }
        )
        if call is not None:
            expected["operation_digest"] = call.operation_digest
            expected["target_identifiers"] = call.target_identifiers
            expected["reviewed_server"] = call.server
            expected["reviewed_tool"] = call.tool
    return expected


def _bind_started_action_contracts(
    actions: tuple[dict[str, object], ...],
    events: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Recover canonical contracts for legacy proposals during reconciliation.

    Older Consumer proposals stored only a free-form operation string.  The
    durable ``item.started`` event is the authoritative, already-reviewed
    contract for that action; binding its typed operation/target metadata lets
    read-only reconciliation request the registered readback without changing
    the original proposal or authorizing a write.
    """
    started: list[dict[str, object]] = []
    for event in events:
        if event.get("type") != "item.started":
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            continue
        if metadata.get("effect") != EffectKind.EFFECTFUL.value:
            continue
        if metadata.get("operation") == "read_skill":
            continue
        started.append(metadata)
    if not started:
        return actions
    by_index: dict[int, dict[str, object]] = {}
    unindexed: list[dict[str, object]] = []
    for metadata in started:
        index = metadata.get("action_index")
        if isinstance(index, int) and 0 <= index < len(actions):
            by_index.setdefault(index, metadata)
        else:
            unindexed.append(metadata)
    for index, metadata in enumerate(unindexed):
        if index >= len(actions):
            break
        by_index.setdefault(index, metadata)
    bound: list[dict[str, object]] = []
    canonical_keys = {
        "capability",
        "operation",
        "operation_digest",
        "arguments_digest",
        "target_identifiers",
        "readback_target_identifiers",
        "message_text_digest",
        "message_rendered_text_digest",
        "reviewed_server",
        "reviewed_tool",
    }
    for index, action in enumerate(actions):
        metadata = by_index.get(index)
        if metadata is None:
            bound.append(action)
            continue
        merged = dict(action)
        merged.update({key: metadata[key] for key in canonical_keys if key in metadata})
        bound.append(merged)
    return tuple(bound)


def _typed_direct_recipient_mismatches(
    context: AuditTurnContext,
) -> tuple[int, ...]:
    """Reject only a known open-DingTalk ID passed through the user-ID flag."""
    if not context.task.single_chat:
        return ()
    open_dingtalk_id = context.task.trigger_sender_open_dingtalk_id
    if not open_dingtalk_id:
        return ()
    mismatches: list[int] = []
    for index, action in enumerate(context.proposal.actions):
        descriptor = describe_native_command(
            {"type": "command_execution", **action.payload}
        )
        if descriptor is None or descriptor.cli != "dws":
            continue
        if descriptor.target_identifiers.get("user") == open_dingtalk_id:
            mismatches.append(index)
    return tuple(mismatches)


def _database_delivery_absence_reconciliation(
    store: AutoReplyStore,
    task: ReplyTask,
    context: AuditTurnContext,
    run: AgentRun,
) -> bool:
    """Use the delivery ledger only for a single direct-message recovery."""
    del run
    if store.has_sent_reply_for_trigger(task.conversation_id, task.trigger_message_id):
        return False
    actions = context.proposal.actions
    return bool(actions) and all(_is_direct_chat_send(action, task) for action in actions)


def _persisted_single_direct_delivery(
    task: ReplyTask,
    context: AuditTurnContext,
    store: AutoReplyStore,
) -> bool:
    """A ledger row is terminal only for one matching direct-message action."""
    actions = context.proposal.actions
    return (
        len(actions) == 1
        and _is_direct_chat_send(actions[0], task)
        and store.has_sent_reply_for_trigger(
            task.conversation_id,
            task.trigger_message_id,
        )
    )


def _is_direct_chat_send(action: object, task: ReplyTask | None = None) -> bool:
    payload = getattr(action, "payload", None)
    if not isinstance(payload, dict):
        return False
    descriptor = describe_native_command({"type": "command_execution", **payload})
    if descriptor is None or descriptor.cli != "dws":
        return False
    argv = native_command_argv({"type": "command_execution", **payload})
    if descriptor.command_path == "chat +dm":
        return bool(
            task is not None
            and task.single_chat
            and argv is not None
            and _argv_option_value(argv, "--to").casefold()
            == task.trigger_sender.casefold()
        )
    target = getattr(action, "target", None)
    target_keys = set(descriptor.target_identifiers)
    if isinstance(target, dict):
        target_keys.update(str(key).replace("_", "-") for key in target)
    return bool({"open-dingtalk-id", "user"} & target_keys)


def _record_verified_chat_delivery_receipt(
    store: AutoReplyStore,
    task: ReplyTask,
    context: AuditTurnContext,
    *,
    audit_run: AgentRun,
) -> None:
    """Backfill one delivery ledger row only after every audited effect is confirmed."""
    if store.has_sent_reply_for_trigger(task.conversation_id, task.trigger_message_id):
        return
    actions = context.proposal.actions
    if len(actions) != 1:
        return
    action = actions[0]
    argv = native_command_argv({"type": "command_execution", **action.payload})
    descriptor = describe_native_command(
        {"type": "command_execution", **action.payload}
    )
    if descriptor is None or descriptor.cli != "dws" or argv is None:
        return
    reply_text = dingtalk_message_text(argv)
    if not reply_text:
        return
    target = descriptor.target_identifiers
    is_direct_message = (
        descriptor.command_path == "chat +dm"
        and task.single_chat
        and _argv_option_value(argv, "--to").casefold()
        == task.trigger_sender.casefold()
    )
    is_conversation_reply = (
        descriptor.command_path in {"chat message reply", "chat +messages-reply"}
        and target.get("conversation-id", target.get("conversation", ""))
        == task.conversation_id
    )
    is_addressed_send = descriptor.command_path in {
        "chat message send",
        "chat +messages-send",
        "chat +send-to-group",
    } and bool({"group", "user", "open-dingtalk-id"} & set(target))
    if not (is_direct_message or is_conversation_reply or is_addressed_send):
        return
    store.record_confirmed_sent_reply_if_absent(
        audit_run_id=audit_run.id,
        reply_text=reply_text,
        send_result_json=json.dumps(
            {
                "source": "agent_audit_verified_recovery",
                "operation_id": audit_run.operation_id,
                "verification_summary": "Controlled write and target-matched readback confirmed.",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


def _argv_option_value(argv: tuple[str, ...], option: str) -> str:
    for index, value in enumerate(argv):
        if value == option and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith(f"{option}="):
            return value.partition("=")[2]
    return ""


def _recovery_execute_prompt(
    run: AgentRun,
    context: AuditTurnContext,
    authorizations: tuple[dict[str, object], ...],
) -> str:
    allowed = [
        {
            "action_index": entry["action_index"],
            "authorization_id": entry["authorization_id"],
        }
        for entry in authorizations
    ]
    return (
        f"{context.render()}\n\nRecovery execution for operation {run.operation_id}, "
        f"proposal revision {run.proposal_revision}. Persisted live reconciliation "
        f"proved only these actions absent: {json.dumps(allowed, separators=(',', ':'))}. "
        "Execute each through agent_cli.execute_reviewed_write with its exact "
        "authorization_id, and verify only those actions. Do not repeat any other action."
    )


def _frozen_delivery_retry_prompt(
    run: AgentRun,
    context: AuditTurnContext,
    authorizations: tuple[dict[str, object], ...],
) -> str:
    allowed = [
        {
            "action_index": entry["action_index"],
            "authorization_id": entry["authorization_id"],
        }
        for entry in authorizations
    ]
    return (
        "FROZEN DELIVERY RETRY: the persisted Consumer proposal is the immutable "
        "business decision for this turn. Do not reconsider its content, return "
        "feedback_provided, or ask Consumer A to generate a replacement. Execute "
        "only the exact authorized external action below once through "
        "agent_cli.execute_reviewed_write, then perform its target-matched readback. "
        "If either step cannot complete, return failed with a stable error code; do "
        "not substitute another action or target.\n\n"
        f"{context.render()}\n\n"
        f"Frozen operation: {run.operation_id}; proposal revision: {run.proposal_revision}.\n"
        f"Authorized action: {json.dumps(allowed, ensure_ascii=False, separators=(',', ':'))}"
    )


def _recovery_authorizations(
    run: AgentRun,
    context: AuditTurnContext,
    absent: frozenset[int],
    registry: McpToolEffectRegistry,
) -> tuple[dict[str, object], ...]:
    actions = tuple(
        _expected_effect_action(
            context.proposal.actions[action_index],
            registry,
            action_index=action_index,
        )
        for action_index in sorted(absent)
    )
    # Legacy unknown runs may have only free-form proposal operations. Reuse
    # the durable reviewed contract when deriving one-shot authorization IDs so
    # the execution turn receives the same canonical identity as reconciliation.
    actions = _bind_started_action_contracts(actions, run.tool_events)
    return _write_authorizations(run, actions)


def _initial_write_authorizations(
    run: AgentRun,
    actions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    return _write_authorizations(run, actions)


def _write_authorizations(
    run: AgentRun,
    actions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for action in actions:
        action_index = action.get("action_index")
        if (
            not isinstance(action_index, int)
            or action.get("reviewed_server") != "agent_cli"
            or action.get("reviewed_tool") != "execute_reviewed_write"
            or action.get("operation_contract_valid") is False
        ):
            continue
        identity = {
            "proposal_operation_id": run.operation_id,
            "proposal_revision": run.proposal_revision,
            **action,
        }
        authorization_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        entries.append(
            {
                "authorization_id": authorization_id,
                "receipt_operation_id": _action_receipt_operation_id(
                    run.operation_id, action, action_index
                ),
                **action,
            }
        )
    return tuple(entries)


def _write_authorization_prompt(
    authorizations: tuple[dict[str, object], ...],
) -> str:
    allowed = [
        {
            "action_index": entry["action_index"],
            "authorization_id": entry["authorization_id"],
        }
        for entry in authorizations
    ]
    return (
        "\n\n### One-shot write authorizations\n"
        "For each approved action, call agent_cli.execute_reviewed_write exactly "
        "once with the proposal argv and the matching authorization_id below. "
        "An authorization is durably consumed before the external command starts "
        "and cannot be reused.\n"
        f"{json.dumps(allowed, ensure_ascii=False, separators=(',', ':'))}"
    )


def _recovery_prompt(
    run: AgentRun,
    context: AuditTurnContext,
    actions: tuple[dict[str, object], ...],
    registry: McpToolEffectRegistry,
) -> str:
    identity = json.dumps(
        {
            "operation_id": run.operation_id,
            "proposal_revision": run.proposal_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    unavailable = [
        index
        for index, action in enumerate(actions)
        if not registry.has_registered_readback_for(
            write_server=str(action.get("reviewed_server") or ""),
            write_tool=str(action.get("reviewed_tool") or ""),
            write_operation=str(action.get("operation") or ""),
        )
    ]
    readback_contracts = json.dumps(
        [
            {
                "action_index": index,
                "operation": action.get("operation", ""),
                "target_identifiers": action.get("target_identifiers", {}),
            }
            for index, action in enumerate(actions)
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    guidance = ""
    if unavailable:
        guidance = (
            "\nAutomatic readback is unavailable for action indexes "
            f"{unavailable}. Do not execute or replay these actions. return failed with the concrete capability reason; do not turn a technical limitation into a policy decision."
        )
    chat_guidance = ""
    if context.task.channel == "dingtalk":
        chat_guidance = (
            " For DingTalk chat writes, use dws chat +chat-messages against the "
            "exact group or conversation with --start and --end, a window no "
            f"wider than two hours that covers the original operation start "
            f"{run.started_at}Z (this value is UTC; do not reinterpret it as local time), "
            "plus --page-all and --format json. Treat an exact "
            "full message text match in that scoped window as present. Treat no "
            "match as absent only when complete=true, hasMore=false, "
            "paginationKnown=true, and failures is empty; otherwise use ambiguous."
        )
    comment_indexes = [
        index
        for index, action in enumerate(actions)
        if str(action.get("operation") or "") in {
            "doc +comment-create", "doc +comment-reply",
            "doc +comment-update", "doc +comment-delete",
        }
    ]
    comment_guidance = ""
    if comment_indexes:
        comment_guidance = (
            " For document-comment actions at indexes "
            f"{comment_indexes}, the only valid readback is exactly "
            "dws doc +comment-list with the same node; doc +inspect, doc +fetch, "
            "doc info, and doc read do not inspect comments and cannot be cited."
        )
    return (
        "The previous attempt did not produce a valid terminal structured result. "
        "RECOVERY MODE OVERRIDES NORMAL AUDIT EXECUTION. Perform strictly read-only recovery and return one terminal structured "
        "result (executed, feedback_provided, needs_human, failed, unknown, or "
        "reconciled). The only valid outcome for this turn is reconciled when "
        "performing reconciliation; Do not return executed from read-only recovery. "
        "reconciled). reconciliation must be an array of per-action readback "
        "records; never execute or blindly replay an action whose effect is unknown. "
        "Do not wrap the array in an operation_id/entries object. Every entry must "
        "include action_index, disposition, and read_result_digest.\n\n"
        "An unknown readback command is an evidence task; use read-only capability discovery.\n\n"
        f"Exact readback contracts: {readback_contracts}\n\n"
        "A present disposition shares a stable identifier from its exact readback contract.\n\n"
        "Do not substitute a different target type or broaden the readback scope.\n\n"
        "Do not start with an unbounded read; scope every read to the exact target contract.\n\n"
        "An incomplete window cannot prove absence.\n\n"
        f"{context.render()}\n\nPrior attempt: {identity}{guidance}\n"
    )
