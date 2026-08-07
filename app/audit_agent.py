from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from app.agent_context import AuditTurnContext
from app.agent_contracts import AuditAgentResult, AuditOutcome
from app.agent_result import AgentError, SideEffectState, parse_typed_agent_result
from app.agent_cli import RECOVERY_WRITE_ALLOWLIST_ENV
from app.audit_rules import render_audit_rules
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
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
                recovery_phase="reconcile",
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

    def execute_recovery(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        if run.status != "unknown" or not run.final_result_json:
            raise ValueError("audit recovery execution requires persisted reconciliation")
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
        authorizations = _recovery_authorizations(
            run, context, absent, self.effects
        )
        claim = self.store.claim_unknown_agent_run(
            run.id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        if len(authorizations) != len(absent):
            result = AuditAgentResult(
                outcome=AuditOutcome.NEEDS_HUMAN,
                summary="An absent direct MCP effect cannot be replayed safely.",
                proposal_revision=run.proposal_revision,
                side_effect_state=SideEffectState.NONE,
                feedback=None,
                external_result=None,
                reconciliation=(),
                error=AgentError(
                    code="audit_recovery_direct_mcp_replay_forbidden",
                    retryable=False,
                ),
            )
            completed = self.store.complete_agent_run(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=SideEffectState.UNKNOWN.value,
                expected_status="unknown",
            )
            return AgentTurnRunResult(
                run_id=run.id,
                result=result,
                transcript_start_line=completed.transcript_end_line,
                transcript_end_line=completed.transcript_end_line,
            )
        return self._execute_claimed(
            task,
            context,
            run=claim.run,
            rendered_rules=render_audit_rules(AgentRole.AUDIT),
            recovery_phase="execute",
            authorized_recovery_actions=absent,
            recovery_authorizations=authorizations,
        )

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
    ) -> AgentTurnRunResult[AuditAgentResult]:
        expected_effect_actions = tuple(
            _expected_effect_action(action, self.effects, action_index=index)
            for index, action in enumerate(context.proposal.actions)
        )
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
                _recovery_prompt(run, context, expected_effect_actions, self.effects)
                if recovery_phase == "reconcile"
                else _recovery_execute_prompt(
                    run,
                    context,
                    recovery_authorizations,
                )
                if recovery_phase == "execute"
                else context.render()
            ),
            session_id=run.codex_session_id or None,
            schema_path=SCHEMA_PATH,
            expected_schema=AuditAgentResult.model_json_schema(),
            developer_instructions=_developer_instructions(
                "Audit Agent B independently reviews and executes accepted candidates.\n\n"
                + (
                    "This is recovery of an unknown external outcome in the same Audit "
                    "session. This phase is strictly read-only: reconcile live state for "
                    "each exact operation and return outcome reconciled with structured "
                    "per-action dispositions. Never execute or replay a write in this "
                    "phase; a later turn will consume persisted absent dispositions.\n\n"
                    if recovery_phase == "reconcile"
                    else "This is recovery execution in the same Audit session. Execute "
                    "only the action indexes that the persisted reconciliation proved "
                    "absent. Do not repeat present or ambiguous actions.\n\n"
                    if recovery_phase == "execute"
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
                    if self.dry_run or recovery_phase in {"reconcile", "execute"}
                    else self.effects.reviewed_tools()
                ),
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli"),
                    cwd=str(SERVICE_ROOT),
                    env=(
                        (
                            RECOVERY_WRITE_ALLOWLIST_ENV,
                            json.dumps(
                                recovery_authorizations,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    if recovery_phase == "execute"
                    else (),
                ),
                allow_write=not self.dry_run and recovery_phase != "reconcile",
            ),
            parse_result=lambda raw: parse_typed_agent_result(raw, AuditAgentResult),
            persist_conversation_session=False,
            expected_effect_actions=expected_effect_actions,
            recovery_phase=recovery_phase,
            authorized_recovery_actions=authorized_recovery_actions,
            recovery_authorizations={
                str(entry["authorization_id"]): int(entry["action_index"])
                for entry in recovery_authorizations
            },
        )


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
        expected["operation_digest"] = descriptor.command_digest
        expected["target_identifiers"] = descriptor.target_identifiers
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


def _recovery_authorizations(
    run: AgentRun,
    context: AuditTurnContext,
    absent: frozenset[int],
    registry: McpToolEffectRegistry,
) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for action_index in sorted(absent):
        action = _expected_effect_action(
            context.proposal.actions[action_index],
            registry,
            action_index=action_index,
        )
        if (
            action.get("reviewed_server") != "agent_cli"
            or action.get("reviewed_tool") != "execute_reviewed_write"
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
                **action,
            }
        )
    return tuple(entries)


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
        if not registry.has_readback_for(
            write_server=str(action.get("reviewed_server") or ""),
            write_tool=str(action.get("reviewed_tool") or ""),
        )
    ]
    guidance = ""
    if unavailable:
        guidance = (
            "\nAutomatic readback is unavailable for action indexes "
            f"{unavailable}. Do not execute or replay these actions. Unless an exact "
            "persisted receipt already confirms them, return needs_human."
        )
    return (
        f"{context.render()}\n\nUnknown outcome recovery: {identity}{guidance}\n"
        "For every unresolved action that has a configured readback, return one "
        "reconciliation entry with its action_index, a disposition of present, "
        "absent, or ambiguous, and the exact result_digest from the matching live "
        "read completed in this recovery turn. Use present only when the read proves "
        "the old action happened, absent only when it proves the action did not "
        "happen, and ambiguous when human judgment is required. Return outcome "
        "reconciled with side_effect_state unknown and no external_result. Do not write."
    )
