from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from app.agent_context import AuditTurnContext
from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
)
from app.agent_result import AgentError, SideEffectState
from app.agent_wire_contracts import AuditAgentWireResult, parse_audit_agent_wire_result
from app.agent_cli import RECOVERY_WRITE_ALLOWLIST_ENV
from app.audit_rules import render_audit_rules
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_turn_runner import (
    AgentTurnProcess,
    AgentTurnRunResult,
    ProcessExecutor,
    unknown_reconciliation_retry_at,
)
from app.consumer_agent import audit_developer_instructions
from app.native_cli_metadata import describe_native_command
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_audit_agent_command


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "audit_agent_wire.schema.json"
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
        database_absence = _database_delivery_absence_reconciliation(
            self.store,
            task,
            context,
            claim.run,
        )
        if database_absence:
            return self._requeue_absent_direct_delivery(task, claim.run)
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
                    next_attempt_at=unknown_reconciliation_retry_at(
                        persisted.reconciliation_attempts
                    ),
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
        claim = self.store.claim_unknown_agent_run(
            run.id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        if _database_delivery_absence_reconciliation(
            self.store,
            task,
            context,
            claim.run,
        ):
            return self._requeue_absent_direct_delivery(task, claim.run)
        authorizations = _recovery_authorizations(
            run, context, absent, self.effects
        )
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

    def _requeue_absent_direct_delivery(
        self,
        task: ReplyTask,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        self.store.resolve_unknown_agent_run_absent(
            run.id,
            task.id,
            code="persisted_delivery_absent",
            owner=self.owner,
            transcript_end_line=run.transcript_end_line,
        )
        result = AuditAgentResult(
            outcome=AuditOutcome.FAILED,
            summary=(
                "No persisted delivery record exists for the exact trigger; "
                "the direct chat action was requeued in a new generation."
            ),
            proposal_revision=run.proposal_revision,
            side_effect_state=SideEffectState.NONE,
            feedback=None,
            external_result=None,
            reconciliation=(),
            error=AgentError(code="persisted_delivery_absent", retryable=True),
        )
        return AgentTurnRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=run.transcript_end_line,
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
            expected_schema=AuditAgentWireResult.model_json_schema(),
            developer_instructions=audit_developer_instructions(
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
            parse_result=parse_audit_agent_wire_result,
            persist_conversation_session=False,
            expected_effect_actions=expected_effect_actions,
            recovery_phase=recovery_phase,
            authorized_recovery_actions=authorized_recovery_actions,
            recovery_authorizations={
                str(entry["authorization_id"]): int(entry["action_index"])
                for entry in recovery_authorizations
            },
            allow_effectful_tools=(
                not self.dry_run and recovery_phase != "reconcile"
            ),
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
    legacy_argv = _legacy_dingtalk_chat_send_argv(action)
    if descriptor is None and legacy_argv is not None:
        descriptor = describe_native_command(
            {"type": "command_execution", "argv": legacy_argv}
        )
    if descriptor is not None:
        expected["operation_contract_valid"] = (
            legacy_argv is not None or action.operation == descriptor.command_path
        )
        if legacy_argv is not None:
            expected["capability"] = f"agent_cli.{descriptor.cli}"
            expected["operation"] = descriptor.command_path
            expected["arguments_digest"] = _json_digest({"argv": legacy_argv})
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


def _database_delivery_absence_reconciliation(
    store: AutoReplyStore,
    task: ReplyTask,
    context: AuditTurnContext,
    run: AgentRun,
) -> bool:
    """Use the service delivery ledger for an all-direct-chat unknown outcome."""
    if store.has_sent_reply_for_trigger(task.conversation_id, task.trigger_message_id):
        return False
    actions = context.proposal.actions
    if not actions or not all(_is_direct_chat_send(action) for action in actions):
        return False
    return True


def _is_direct_chat_send(action: object) -> bool:
    capability = getattr(action, "capability", "")
    operation = getattr(action, "operation", "")
    payload = getattr(action, "payload", None)
    if capability != "agent_cli.dws" or operation != "chat message send":
        return False
    if not isinstance(payload, dict):
        return False
    # DWS has used more than one reviewed CLI spelling for the same message
    # send operation. Resolve the stored command and trust its typed target
    # metadata instead of assuming a particular argv spelling.
    descriptor = describe_native_command({"type": "command_execution", **payload})
    if descriptor is None or descriptor.cli != "dws":
        return False
    # The controlled +send-to-group command is ledger-backed like a direct
    # chat send. Other group write spellings retain their normal readback path.
    target = getattr(action, "target", None)
    target_keys = set(descriptor.target_identifiers)
    if isinstance(target, dict):
        target_keys.update(str(key).replace("_", "-") for key in target)
    return "open-dingtalk-id" in target_keys or (
        descriptor.command_path == "chat +send-to-group" and "group" in target_keys
    )


def _legacy_dingtalk_chat_send_argv(action) -> list[str] | None:
    """Canonicalize only a persisted pre-contract DingTalk chat action."""
    if action.capability != "dingtalk-chat" or action.operation != "dws chat message send":
        return None
    group = action.payload.get("group")
    text = action.payload.get("text")
    if not isinstance(group, str) or not group or not isinstance(text, str) or not text:
        return None
    return [
        "dws", "chat", "message", "send", "--group", group,
        "--text", text, "--yes",
    ]


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
        "reconciled with side_effect_state unknown and no external_result. "
        "reconciliation_json must be a JSON-encoded array whose entries contain "
        "exactly action_index, disposition, and read_result_digest. Do not wrap the "
        "array in an operation_id/entries object. Before any DWS read, load the "
        "operation-specific installed skill with agent_cli.read_skill; an unknown "
        "readback command is an evidence task, not a reason to fail or escalate. "
        "Do not write."
    )
