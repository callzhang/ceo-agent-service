from __future__ import annotations

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
from app.agent_result import AgentError, EffectKind, ResultParseError
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
    """Execute one ordinary Audit turn.

    Audit validates the typed proposal/result contract and delegates provider
    capabilities to the selected runtime.  It does not maintain an
    application-level command review, read-only, unknown-outcome, or
    reconciliation state machine.
    """

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
        context: AuditTurnContext, *, recovery_phase: str = ""
    ) -> frozenset[str]:
        del recovery_phase
        required = {"task_context", f"channel:{context.task.channel}"}
        if context.task.image_paths:
            required.add("image_input")
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
        mismatches = _typed_direct_recipient_mismatches(context)
        if mismatches and not frozen_delivery_retry:
            return self._return_invalid_candidate(claim.run, mismatches)
        return self._execute_claimed(
            task,
            context,
            run=claim.run,
            rendered_rules=render_audit_rules(AgentRole.AUDIT),
            frozen_delivery_retry=frozen_delivery_retry,
        )

    def _return_invalid_candidate(
        self,
        run: AgentRun,
        mismatches: tuple[int, ...],
    ) -> AgentTurnRunResult[AuditAgentResult]:
        listed = ", ".join(str(index) for index in mismatches)
        result = AuditAgentResult(
            outcome=AuditOutcome.FEEDBACK_PROVIDED,
            summary="The candidate uses an invalid typed recipient identifier.",
            proposal_revision=run.proposal_revision,
            feedback=AuditFeedback(
                rule="The candidate must use the correct typed recipient identifier.",
                observation=f"Candidate action indexes {listed} use an open-DingTalk ID as a user ID.",
                requested_revision=(
                    "Return the same intended operation with the correct recipient identifier. "
                    "For a single chat use --open-dingtalk-id with the typed recipient. "
                    "Preserve the business recipient and payload."
                ),
            ),
            external_result=None,
            error=AgentError(),
        )
        completed = self.store.complete_agent_run(
            run.id, result.model_dump(mode="json"), owner=self.owner
        )
        return AgentTurnRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=completed.transcript_end_line,
            transcript_end_line=completed.transcript_end_line,
        )

    def _execute_claimed(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
        rendered_rules: str,
        frozen_delivery_retry: bool = False,
    ) -> AgentTurnRunResult[AuditAgentResult]:
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
        prompt = context.render()
        if frozen_delivery_retry:
            prompt += (
                "\n\nThis is a delivery retry. Re-evaluate the same typed proposal "
                "against the current task context and return one terminal result."
            )
        prompt += (
            "\n\n### Needs Human Display Contract\n"
            "Return needs_human only for a reusable policy gap: existing rules "
            "cannot determine how this class of cases should be handled. Describe "
            "the rule key, recurring pattern, and mutually exclusive policy choices. "
            "Do not turn a technical failure into needs_human."
        )
        if self.dry_run:
            prompt += (
                "\n\n### Dry Run Context\n"
                "Do not publish an external action in this simulation; return failed "
                "with error code dry_run_execution_suppressed when execution is suppressed."
            )
        return process.execute(
            run=run,
            prompt=prompt,
            session_id=run.codex_session_id or None,
            developer_instructions=audit_developer_instructions(rendered_rules),
            configure_command=lambda command: None,
            parse_result=parse_audit_agent_wire_result,
            persist_conversation_session=False,
            allow_effectful_tools=not self.dry_run,
            image_paths=[Path(path) for path in context.task.image_paths],
            required_capabilities=self._required_capabilities(context),
        )


def _audit_recovery_error_code(exc: Exception) -> str:
    """Map a process exception to the ordinary failed-result code."""
    code = getattr(exc, "code", "")
    return str(code) if code else "codex_process_failed"


def _json_digest(value: object) -> str:
    import hashlib, json
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _expected_effect_action(action, registry=None, *, action_index: int = 0) -> dict[str, object]:
    """Return only stable business identity; provider command details are runtime-owned."""
    del registry
    return {
        "action_index": action_index,
        "operation": getattr(action, "operation", ""),
        "target_identifiers": getattr(action, "target", {}),
    }


def _initial_write_authorizations(*args, **kwargs) -> tuple[dict[str, object], ...]:
    return ()


def _recovery_authorizations(*args, **kwargs) -> tuple[dict[str, object], ...]:
    return ()


def _recovery_prompt(run: AgentRun, context: AuditTurnContext, actions=(), registry=None) -> str:
    del actions, registry
    return (
        "Run the normal typed Audit turn against the current task context and "
        "the applicable operation Skill.\n\n" + context.render()
    )


def _typed_direct_recipient_mismatches(context: AuditTurnContext) -> tuple[int, ...]:
    """Validate the typed recipient fields without reviewing execution commands."""
    if not context.task.single_chat or not context.task.trigger_sender_open_dingtalk_id:
        return ()
    expected = context.task.trigger_sender_open_dingtalk_id
    mismatches: list[int] = []
    for index, action in enumerate(context.proposal.actions):
        descriptor = describe_native_command({"type": "command_execution", **action.payload})
        if descriptor is not None and descriptor.target_identifiers.get("user") == expected:
            mismatches.append(index)
    return tuple(mismatches)
