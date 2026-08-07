from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from app.agent_context import AuditTurnContext
from app.agent_contracts import AuditAgentResult
from app.agent_result import parse_typed_agent_result
from app.audit_rules import render_audit_rules
from app.agent_runner import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.consumer_agent import _developer_instructions
from app.native_cli_metadata import describe_native_command
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_audit_agent_command


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "audit_agent_result.schema.json"
SERVICE_ROOT = Path(__file__).resolve().parent.parent


class AuditAgentRunner:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        codex_bin: str = "codex",
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        dry_run: bool = False,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.executor = executor
        self.owner = owner or f"audit-agent-{uuid4().hex}"
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.dry_run = dry_run

    def run(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        turn_attempt: int,
        parent_agent_run_id: int,
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
        return self._execute_claimed(
            task,
            context,
            run=claim.run,
            rendered_rules=rendered_rules,
            recovery=False,
        )

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
        if not run.codex_session_id:
            raise ValueError("audit recovery requires the original Codex session")
        rendered_rules = render_audit_rules(AgentRole.AUDIT)
        claim = self.store.claim_unknown_agent_run(
            run.id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        try:
            return self._execute_claimed(
                task,
                context,
                run=claim.run,
                rendered_rules=rendered_rules,
                recovery=True,
            )
        except Exception:
            persisted = self.store.get_agent_run(run.id)
            if (
                persisted is not None
                and persisted.status == "unknown"
                and persisted.lease_owner == self.owner
            ):
                self.store.defer_unknown_agent_run_reconciliation(
                    run.id,
                    {"code": "audit_recovery_failed", "retryable": True},
                    owner=self.owner,
                    expected_execution_generation=run.execution_generation,
                    next_attempt_at="",
                )
            raise

    def _execute_claimed(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
        rendered_rules: str,
        recovery: bool,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        process = AgentTurnProcess[AuditAgentResult](
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            mcp_effect_registry=self.effects,
        )
        return process.execute(
            run=run,
            prompt=(
                _recovery_prompt(run, context)
                if recovery
                else context.render()
            ),
            session_id=run.codex_session_id or None,
            schema_path=SCHEMA_PATH,
            expected_schema=AuditAgentResult.model_json_schema(),
            developer_instructions=_developer_instructions(
                "Audit Agent B independently reviews and executes accepted candidates.\n\n"
                + (
                    "This is recovery of an unknown external outcome in the same Audit "
                    "session. Reconcile live state for the exact operation before any "
                    "repeat. If live state confirms the operation, verify it and return "
                    "executed without another write. If live state definitely confirms "
                    "absence, execute this same revision once and verify it. If live state "
                    "is ambiguous, return needs_human without a write.\n\n"
                    if recovery
                    else ""
                )
                + (
                    "Dry-run is active. Use read-only tools to complete the independent "
                    "review. Return revision_required normally when the candidate must "
                    "change. When the candidate is executable but execution is suppressed "
                    "only by dry-run, return needs_human with error code "
                    "dry_run_execution_suppressed and side_effect_state none.\n\n"
                    if self.dry_run
                    else ""
                )
                + rendered_rules
            ),
            configure_command=lambda command: make_audit_agent_command(
                command,
                reviewed_mcp_tools=(
                    self.effects.reviewed_read_tools()
                    if self.dry_run
                    else self.effects.reviewed_tools()
                ),
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli"),
                    cwd=str(SERVICE_ROOT),
                ),
                allow_write=not self.dry_run,
            ),
            parse_result=lambda raw: parse_typed_agent_result(raw, AuditAgentResult),
            persist_conversation_session=False,
            expected_effect_actions=tuple(
                _expected_effect_action(action)
                for action in context.proposal.actions
            ),
            recover_unknown=recovery,
        )


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_effect_action(action) -> dict[str, object]:
    expected = {
        "capability": action.capability,
        "operation": action.operation,
        "arguments_digest": _json_digest(action.payload),
        "target_identifiers": action.target,
    }
    descriptor = describe_native_command(
        {"type": "command_execution", **action.payload}
    )
    if descriptor is not None:
        expected["operation_digest"] = descriptor.command_digest
        expected["target_identifiers"] = descriptor.target_identifiers
    return expected


def _recovery_prompt(run: AgentRun, context: AuditTurnContext) -> str:
    identity = json.dumps(
        {
            "operation_id": run.operation_id,
            "proposal_revision": run.proposal_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{context.render()}\n\nUnknown outcome recovery: {identity}"
