from __future__ import annotations

import json
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from app.agent_contracts import (
    AuditReconciliation,
    AuditOutcome,
    ConsumerOutcome,
    ReconciliationDisposition,
)
from app.agent_result import EffectKind, ResultParseError, SideEffectState
from app.agent_effects import (
    IDLE_TIMEOUT_SECONDS,
    LEASE_SECONDS,
    TOTAL_TIMEOUT_SECONDS,
    McpToolEffectRegistry,
    _controlled_cli_receipt,
    _is_sensitive_key,
    _is_signed_url,
    _mcp_call_completed,
    _normalized_key,
)
from app.codex_runner import CodexRunner
from app.leak_check import contains_credential
from app.native_cli_metadata import AgentReadOnlyViolationError, describe_native_command
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask


ResultT = TypeVar("ResultT")
ProcessExecutor = Callable[..., ProcessRunResult]
CODEX_PROVIDER_UNAVAILABLE = "codex_provider_unavailable"


@dataclass(frozen=True)
class AgentTurnRunResult(Generic[ResultT]):
    run_id: int
    result: ResultT
    transcript_start_line: int
    transcript_end_line: int


def _process_failure_code(process: ProcessRunResult) -> str:
    detail = f"{process.stdout}\n{process.stderr}".casefold()
    if any(
        marker in detail
        for marker in (
            "workspace is out of credits",
            "hit your usage limit",
            "quota exceeded",
        )
    ):
        return CODEX_PROVIDER_UNAVAILABLE
    return "codex_process_failed"


def _agent_process_error_code(exc: Exception) -> str:
    code = str(exc).strip()
    if code == CODEX_PROVIDER_UNAVAILABLE:
        return code
    if isinstance(exc, ResultParseError):
        if code == "no valid typed result JSON found in Codex JSONL":
            return "codex_result_missing"
        return "codex_result_invalid"
    return "codex_process_failed"


class AgentTurnProcess(Generic[ResultT]):
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        task: ReplyTask,
        workspace: Path,
        owner: str,
        executor: ProcessExecutor | None = None,
        codex_bin: str = "codex",
        mcp_effect_registry: McpToolEffectRegistry | None = None,
    ) -> None:
        self.store = store
        self.task = task
        self.owner = owner
        self.codex = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor or run_process_with_idle_timeout
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()

    def execute(
        self,
        *,
        run: AgentRun,
        prompt: str,
        session_id: str | None,
        schema_path: Path,
        expected_schema: dict[str, object],
        developer_instructions: str,
        configure_command: Callable[[list[str]], None],
        parse_result: Callable[[str], ResultT],
        persist_conversation_session: bool,
        expected_effect_actions: tuple[dict[str, object], ...] = (),
        on_progress: Callable[[], None] | None = None,
        recovery_phase: str = "",
        authorized_recovery_actions: frozenset[int] = frozenset(),
        recovery_authorizations: dict[str, int] | None = None,
        allow_effectful_tools: bool = False,
    ) -> AgentTurnRunResult[ResultT]:
        if recovery_phase not in {"", "reconcile", "execute"}:
            raise ValueError("invalid recovery phase")
        recover_unknown = bool(recovery_phase)
        recovery_authorizations = recovery_authorizations or {}
        line_count = 0
        saw_json = False
        recovery_started_actions: set[int] = set()
        effect_action_counts = [0] * len(expected_effect_actions)
        effect_action_by_call_id: dict[str, int] = {}
        recovery_event_start = len(run.tool_events)
        completed_before_recovery = (
            _action_completion_accounting(
                run.tool_events,
                self.store.list_agent_execution_receipts(run.id),
                expected_effect_actions,
                operation_id=run.operation_id,
                registry=self.effects,
            )[0]
            if recover_unknown
            else set()
        )
        transcript_start = (
            run.transcript_end_line if recover_unknown else run.transcript_start_line
        )

        def persist_line(line: str) -> None:
            nonlocal line_count, saw_json
            if not line.strip():
                return
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                if saw_json:
                    raise RuntimeError("codex_stream_invalid") from exc
                return
            saw_json = True
            if not isinstance(payload, dict):
                raise RuntimeError("codex_stream_invalid")
            line_count += 1
            if recover_unknown:
                self.store.renew_agent_run_lease(
                    run.id,
                    owner=self.owner,
                    lease_seconds=LEASE_SECONDS,
                    expected_status="unknown",
                )
            else:
                self.store.renew_agent_run_lease(
                    run.id, owner=self.owner, lease_seconds=LEASE_SECONDS
                )
            if on_progress is not None:
                on_progress()
            new_session = _session_id(payload)
            if new_session:
                if recover_unknown:
                    if new_session != run.codex_session_id:
                        raise RuntimeError("audit_recovery_session_mismatch")
                else:
                    self.store.set_agent_run_session(
                        run.id,
                        new_session,
                        owner=self.owner,
                        transcript_start_line=run.transcript_start_line,
                    )
                if persist_conversation_session and not recover_unknown:
                    self.store.upsert_conversation(
                        self.task.conversation_id,
                        self.task.conversation_title,
                        self.task.single_chat,
                        new_session,
                    )
            event = self._normalized_effect_event(
                payload,
                read_only=(
                    run.role is AgentRole.CONSUMER
                    or recovery_phase == "reconcile"
                ),
                operation_id=run.operation_id,
            )
            if event is not None:
                item = event.get("item")
                metadata = item.get("metadata") if isinstance(item, dict) else None
                effect = metadata.get("effect") if isinstance(metadata, dict) else None
                if effect == EffectKind.EFFECTFUL.value and isinstance(metadata, dict):
                    call_id = str(item.get("id") or item.get("call_id") or "")
                    if event.get("type") == "item.started":
                        authorization_id = metadata.get("authorization_id")
                        if recovery_phase == "execute":
                            action_index = recovery_authorizations.get(
                                str(authorization_id or "")
                            )
                            if (
                                action_index is None
                                or action_index >= len(expected_effect_actions)
                                or not _metadata_matches_action(
                                    metadata, expected_effect_actions[action_index]
                                )
                            ):
                                action_index = None
                        else:
                            candidates = [
                                index
                                for index, action in enumerate(expected_effect_actions)
                                if _metadata_matches_action(metadata, action)
                            ]
                            action_index = (
                                min(candidates, key=effect_action_counts.__getitem__)
                                if candidates
                                else None
                            )
                        if action_index is not None:
                            metadata["action_index"] = action_index
                            effect_action_counts[action_index] += 1
                            effect_action_by_call_id[call_id] = action_index
                    else:
                        action_index = effect_action_by_call_id.get(call_id)
                        if action_index is not None:
                            metadata["action_index"] = action_index
                if (
                    recovery_phase == "execute"
                    and event.get("type") == "item.started"
                    and effect == EffectKind.EFFECTFUL.value
                ):
                    action_index = metadata.get("action_index")
                    if (
                        action_index is None
                        or action_index not in authorized_recovery_actions
                        or action_index in completed_before_recovery
                        or action_index in recovery_started_actions
                    ):
                        raise RuntimeError("audit_recovery_action_not_authorized")
                    recovery_started_actions.add(action_index)
                if recover_unknown:
                    self.store.append_unknown_agent_run_event(
                        run.id, event, owner=self.owner
                    )
                else:
                    self.store.append_agent_run_event(run.id, event, owner=self.owner)

        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise ValueError("agent result schema must be a JSON object")
            if schema != expected_schema:
                raise ValueError("agent result schema does not match the result model")
            contract_instructions = (
                developer_instructions
                + "\n\nOutput JSON Schema (validated locally):\n"
                + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            )
            command = self.codex.build_command(
                prompt=prompt,
                session_id=session_id,
                output_schema_path=schema_path,
                use_output_schema=False,
                approval_policy="untrusted" if allow_effectful_tools else "never",
                developer_instructions=contract_instructions,
                use_approval_bypass=allow_effectful_tools,
            )
            configure_command(command)
            process = self.executor(
                command,
                prompt=prompt,
                env=self.codex.build_env(preserve_local_cli_auth=True),
                total_timeout_seconds=TOTAL_TIMEOUT_SECONDS,
                idle_timeout_seconds=IDLE_TIMEOUT_SECONDS,
                on_stdout_line=persist_line,
            )
            self._raise_for_process_failure(process, run=run)
            result = parse_result(process.stdout)
            if _contains_sensitive_value(result.model_dump(mode="json")):
                raise ValueError("agent_result_contains_sensitive_value")
        except AgentReadOnlyViolationError:
            if recover_unknown:
                self._defer_unknown(run, "agent_read_only_violation")
            else:
                self._fail_running(run, "agent_read_only_violation")
            raise
        except Exception as exc:
            provider_recovery = _agent_process_error_code(exc)
            code = (
                provider_recovery
                if provider_recovery != "codex_process_failed"
                else (
                    str(exc)
                    if str(exc).startswith("audit_recovery_")
                    else "codex_process_failed"
                )
            )
            if recover_unknown:
                self._defer_unknown(run, code)
            else:
                self._fail_running(run, code)
            if provider_recovery == CODEX_PROVIDER_UNAVAILABLE:
                raise RuntimeError(code) from exc
            raise
        transcript_end = transcript_start + line_count
        outcome = getattr(result, "outcome")
        side_effect_state = getattr(result, "side_effect_state", SideEffectState.NONE)
        persisted = self.store.get_agent_run(run.id)
        assert persisted is not None
        if run.role is AgentRole.AUDIT and recovery_phase == "reconcile":
            self._validate_audit_reconciliation_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
                recovery_event_start=recovery_event_start,
            )
        elif run.role is AgentRole.AUDIT and recovery_phase == "execute":
            self._validate_audit_recovery_execution_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
                recovery_started_actions=recovery_started_actions,
                authorized_recovery_actions=authorized_recovery_actions,
            )
        elif run.role is AgentRole.AUDIT:
            self._validate_audit_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
            )
        if recovery_phase == "reconcile":
            self.store.persist_unknown_agent_run_result(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=transcript_end,
            )
        elif recovery_phase == "execute":
            if outcome is AuditOutcome.EXECUTED:
                self.store.complete_agent_run(
                    run.id,
                    result.model_dump(mode="json"),
                    owner=self.owner,
                    side_effect_state=SideEffectState.CONFIRMED.value,
                    transcript_end_line=transcript_end,
                    expected_status="unknown",
                )
            else:
                self._defer_unknown(
                    run,
                    getattr(result, "error").code or "audit_recovery_incomplete",
                )
        elif outcome in {ConsumerOutcome.FAILED, AuditOutcome.FAILED}:
            self.store.fail_agent_run(
                run.id,
                getattr(result, "error").model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=side_effect_state.value,
                transcript_end_line=transcript_end,
            )
        elif outcome is AuditOutcome.UNKNOWN:
            self.store.mark_agent_run_unknown(
                run.id,
                getattr(result, "error").model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=transcript_end,
            )
        else:
            self.store.complete_agent_run(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=side_effect_state.value,
                transcript_end_line=transcript_end,
            )
        completed = self.store.get_agent_run(run.id)
        assert completed is not None
        return AgentTurnRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=transcript_start,
            transcript_end_line=completed.transcript_end_line,
        )

    def _normalized_effect_event(
        self,
        payload: dict[str, object],
        *,
        read_only: bool,
        operation_id: str,
    ) -> dict[str, object] | None:
        if payload.get("type") not in {"item.started", "item.completed", "item.failed"}:
            return None
        item = payload.get("item")
        if not isinstance(item, dict):
            return None
        if item.get("type") == "command_execution":
            raise AgentReadOnlyViolationError("agent_shell_execution_forbidden")
        if item.get("type") != "mcp_tool_call":
            return None
        call = self.effects.classify(item)
        if call is None:
            raise AgentReadOnlyViolationError("agent_tool_unreviewed")
        if read_only and call.effect is not EffectKind.READ_ONLY:
            raise AgentReadOnlyViolationError("agent_write_forbidden")
        operation = call.operation
        capability = call.server
        operation_digest = call.operation_digest
        target_identifiers = call.target_identifiers
        native_cli = ""
        authorization_id = ""
        validated_receipt: dict[str, object] | None = None
        if call.server == "agent_cli" and call.tool in {
            "execute_reviewed_read",
            "execute_reviewed_write",
        }:
            arguments = item.get("arguments")
            argv = arguments.get("argv") if isinstance(arguments, dict) else None
            if isinstance(arguments, dict):
                candidate_authorization = arguments.get("authorization_id")
                if isinstance(candidate_authorization, str):
                    authorization_id = candidate_authorization
            descriptor = describe_native_command(
                {"type": "command_execution", "argv": argv}
            )
            if descriptor is None:
                raise AgentReadOnlyViolationError("agent_cli_command_invalid")
            capability = f"agent_cli.{descriptor.cli}"
            operation = descriptor.command_path
            operation_digest = descriptor.command_digest
            target_identifiers = descriptor.target_identifiers
            native_cli = descriptor.cli
            if payload.get("type") == "item.completed":
                receipt = _agent_cli_receipt(item.get("result"))
                if (
                    receipt is None
                    or "error" in receipt
                    or receipt.get("operation") != descriptor.command_path
                    or receipt.get("operation_digest") != operation_digest
                    or receipt.get("target_identifiers") != target_identifiers
                    or (
                        authorization_id
                        and receipt.get("authorization_id") != authorization_id
                    )
                ):
                    raise AgentReadOnlyViolationError("agent_cli_receipt_invalid")
                validated_receipt = receipt
        event_type = str(payload["type"])
        if event_type == "item.completed" and not _mcp_call_completed(payload):
            event_type = "item.failed"
        status = {
            "item.started": "in_progress",
            "item.completed": "completed",
            "item.failed": "failed",
        }[event_type]
        metadata: dict[str, object] = {
            "effect": call.effect.value,
            "capability": capability,
            "operation": operation,
            "reviewed_server": call.server,
            "reviewed_tool": call.tool,
            "operation_digest": operation_digest,
            "target_identifiers": target_identifiers,
            "arguments_digest": _json_digest(
                {"argv": argv}
                if native_cli
                else item.get("arguments")
            ),
        }
        if call.effect is EffectKind.EFFECTFUL:
            metadata["operation_id"] = operation_id
        if event_type == "item.completed":
            result_digest = (
                validated_receipt.get("result_digest")
                if validated_receipt is not None
                else _json_digest(item.get("result"))
            )
            if isinstance(result_digest, str) and result_digest:
                metadata["result_digest"] = result_digest
        if native_cli:
            metadata["native_cli"] = native_cli
        if authorization_id:
            metadata["authorization_id"] = authorization_id
        return {
            "type": event_type,
            "item": {
                "type": "mcp_tool_call",
                "id": str(item.get("id") or item.get("call_id") or ""),
                "server": call.server,
                "tool": call.tool,
                "status": status,
                "metadata": metadata,
            },
        }

    def _validate_audit_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
    ) -> None:
        outcome = getattr(result, "outcome")
        if getattr(result, "proposal_revision") != run.proposal_revision:
            self._fail_running(run, "audit_proposal_revision_mismatch")
            raise RuntimeError("audit_proposal_revision_mismatch")
        if getattr(result, "reconciliation", ()):
            self._fail_running(run, "audit_reconciliation_unexpected")
            raise RuntimeError("audit_reconciliation_unexpected")
        if outcome is AuditOutcome.EXECUTED:
            external_result = getattr(result, "external_result")
            if external_result.operation_id != run.operation_id:
                self._fail_running(run, "audit_operation_mismatch")
                raise RuntimeError("audit_operation_mismatch")
            completed, all_effects_closed = _action_completion_accounting(
                persisted.tool_events,
                self.store.list_agent_execution_receipts(run.id),
                expected_effect_actions,
                operation_id=run.operation_id,
                registry=self.effects,
            )
            if completed == set(range(len(expected_effect_actions))) and all_effects_closed:
                return
            code = (
                "audit_execution_evidence_missing"
                if persisted.side_effect_state == SideEffectState.NONE.value
                else "audit_execution_evidence_mismatch"
            )
            if persisted.side_effect_state != SideEffectState.NONE.value:
                self.store.mark_agent_run_unknown(
                    run.id,
                    {"code": code, "retryable": True},
                    owner=self.owner,
                )
            else:
                self.store.fail_agent_run(
                    run.id,
                    {"code": code, "retryable": True},
                    owner=self.owner,
                )
            raise RuntimeError(code)
        if (
            outcome is not AuditOutcome.UNKNOWN
            and persisted.side_effect_state != SideEffectState.NONE.value
        ):
            self.store.mark_agent_run_unknown(
                run.id,
                {"code": "audit_effect_without_executed_result", "retryable": True},
                owner=self.owner,
            )
            raise RuntimeError("audit_effect_without_executed_result")

    def _validate_audit_reconciliation_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
        recovery_event_start: int,
    ) -> None:
        outcome = getattr(result, "outcome")
        if getattr(result, "proposal_revision") != run.proposal_revision:
            raise RuntimeError("audit_proposal_revision_mismatch")
        if outcome is not AuditOutcome.RECONCILED:
            raise RuntimeError("audit_reconciliation_result_invalid")
        reconciliation = _validated_reconciliation(
            getattr(result, "reconciliation"),
            persisted.tool_events,
            expected_effect_actions,
            event_start=recovery_event_start,
            registry=self.effects,
        )
        completed_by_events = _action_completion_accounting(
            persisted.tool_events[:recovery_event_start],
            [],
            expected_effect_actions,
            operation_id=run.operation_id,
            registry=self.effects,
        )[0]
        required = {
            index
            for index, action in enumerate(expected_effect_actions)
            if index not in completed_by_events
            and _action_has_readback(action, self.effects)
        }
        if set(reconciliation) != required:
            raise RuntimeError("audit_recovery_evidence_missing")
        present = {
            index
            for index, entry in reconciliation.items()
            if entry.disposition is ReconciliationDisposition.PRESENT
        }
        receipts = self.store.list_agent_execution_receipts(run.id)
        for action_index in sorted(present - completed_by_events):
            action = expected_effect_actions[action_index]
            metadata = _matching_effect_metadata(
                persisted.tool_events,
                action,
                operation_id=run.operation_id,
                event_type="item.started",
                action_index=action_index,
            )
            read_result_digest = reconciliation[action_index].read_result_digest
            if metadata is None or not read_result_digest:
                continue
            persisted_index = metadata.get("action_index")
            if persisted_index is None:
                if not self.store.bind_legacy_unknown_effect_action(
                    run.id,
                    action_index=action_index,
                    operation_id=run.operation_id,
                    expected_identity=action,
                    owner=self.owner,
                ):
                    continue
                metadata["action_index"] = action_index
            command_digest = metadata.get("operation_digest")
            if command_digest != action.get("operation_digest"):
                continue
            receipt_operation_id = _action_receipt_operation_id(
                run.operation_id, action, action_index
            )
            if not any(
                getattr(receipt, "operation_id", "") == receipt_operation_id
                for receipt in receipts
            ):
                self.store.record_agent_execution_receipt(
                    run.id,
                    receipt_id=(
                        "reconciliation:"
                        f"{receipt_operation_id}:{read_result_digest}"
                    ),
                    operation_id=receipt_operation_id,
                    cli=str(metadata.get("native_cli") or metadata.get("capability")),
                    command_path=str(metadata.get("operation")),
                    command_digest=str(command_digest),
                    exit_code=0,
                    owner=self.owner,
                    expected_status="unknown",
                )
            self.store.confirm_agent_execution_receipt(
                run.id,
                receipt_operation_id,
                owner=self.owner,
            )
        receipt_completed = _action_completion_accounting(
            persisted.tool_events,
            self.store.list_agent_execution_receipts(run.id),
            expected_effect_actions,
            operation_id=run.operation_id,
            registry=self.effects,
        )[0]
        for action_index, action in enumerate(expected_effect_actions):
            if (
                action_index in receipt_completed
                and action_index not in completed_by_events
                and not _action_has_readback(action, self.effects)
            ):
                self.store.bind_legacy_unknown_effect_action(
                    run.id,
                    action_index=action_index,
                    operation_id=run.operation_id,
                    expected_identity=action,
                    owner=self.owner,
                )
                self.store.confirm_agent_execution_receipt(
                    run.id,
                    _action_receipt_operation_id(
                        run.operation_id, action, action_index
                    ),
                    owner=self.owner,
                )

    def _validate_audit_recovery_execution_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
        recovery_started_actions: set[int],
        authorized_recovery_actions: frozenset[int],
    ) -> None:
        if getattr(result, "proposal_revision") != run.proposal_revision:
            raise RuntimeError("audit_proposal_revision_mismatch")
        if getattr(result, "outcome") is not AuditOutcome.EXECUTED:
            raise RuntimeError("audit_recovery_result_invalid")
        if getattr(result, "reconciliation"):
            raise RuntimeError("audit_reconciliation_unexpected")
        external_result = getattr(result, "external_result")
        if external_result.operation_id != run.operation_id:
            raise RuntimeError("audit_operation_mismatch")
        if recovery_started_actions != set(authorized_recovery_actions):
            raise RuntimeError("audit_recovery_effect_unresolved")
        completed, all_effects_closed = _action_completion_accounting(
            persisted.tool_events,
            self.store.list_agent_execution_receipts(run.id),
            expected_effect_actions,
            operation_id=run.operation_id,
            registry=self.effects,
        )
        if (
            completed != set(range(len(expected_effect_actions)))
            or not all_effects_closed
        ):
            raise RuntimeError("audit_execution_evidence_missing")

    def _raise_for_process_failure(
        self, process: ProcessRunResult, *, run: AgentRun
    ) -> None:
        if process.timed_out:
            raise RuntimeError("codex_process_timeout")
        if process.returncode != 0:
            failure_code = _process_failure_code(process)
            persisted = self.store.get_agent_run(run.id)
            if (
                failure_code == "codex_process_failed"
                and run.role is AgentRole.CONSUMER
                and persisted is not None
                and not persisted.tool_events
                and _stream_has_no_agent_result(process.stdout)
            ):
                raise ResultParseError("no valid typed result JSON found in Codex JSONL")
            raise RuntimeError(failure_code)

    def _fail_running(self, run: AgentRun, code: str) -> None:
        persisted = self.store.get_agent_run(run.id)
        if persisted is not None and persisted.status == "running":
            if persisted.side_effect_state == SideEffectState.NONE.value:
                self.store.fail_agent_run(
                    run.id,
                    {"code": code, "retryable": True},
                    owner=self.owner,
                )
            else:
                self.store.mark_agent_run_unknown(
                    run.id,
                    {"code": code, "retryable": True},
                    owner=self.owner,
                )

    def _defer_unknown(self, run: AgentRun, code: str) -> None:
        persisted = self.store.get_agent_run(run.id)
        if (
            persisted is None
            or persisted.status != "unknown"
            or persisted.lease_owner != self.owner
        ):
            return
        self.store.defer_unknown_agent_run_reconciliation(
            run.id,
            {"code": code, "retryable": True},
            owner=self.owner,
            expected_execution_generation=run.execution_generation,
            next_attempt_at="",
        )


def _stream_has_no_agent_result(raw: str) -> bool:
    saw_json = False
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        saw_json = True
        response_item = payload.get("payload")
        if (
            payload.get("type") == "response_item"
            and isinstance(response_item, dict)
            and response_item.get("type") == "message"
            and response_item.get("role") == "assistant"
        ):
            return False
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            return False
        if isinstance(payload.get("last_agent_message"), str):
            return False
        if (
            isinstance(payload.get("message"), str)
            and payload.get("type") in (None, "agent_message", "task_complete")
        ):
            return False
    return saw_json


def _session_id(payload: dict[str, object]) -> str:
    if payload.get("type") not in {"thread.started", "thread_started"}:
        return ""
    for key in ("thread_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _agent_cli_receipt(value: object) -> dict[str, object] | None:
    receipt = _controlled_cli_receipt(value)
    if receipt is not None or not isinstance(value, dict):
        return receipt
    content = value.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 64 * 1024:
            continue
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError:
            continue
        receipt = _controlled_cli_receipt(candidate)
        if receipt is not None:
            return receipt
    return None


def _matching_effect_metadata(
    events: list[dict[str, object]],
    expected_action: dict[str, object],
    *,
    operation_id: str,
    event_type: str,
    action_index: int,
) -> dict[str, object] | None:
    for event in events:
        if event.get("type") != event_type:
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            continue
        if (
            metadata.get("effect") == EffectKind.EFFECTFUL.value
            and metadata.get("operation_id") == operation_id
            and metadata.get("action_index") in {None, action_index}
            and _metadata_matches_action(metadata, expected_action)
        ):
            return metadata
    return None


def _event_metadata(event: dict[str, object]) -> dict[str, object] | None:
    item = event.get("item")
    metadata = item.get("metadata") if isinstance(item, dict) else None
    return metadata if isinstance(metadata, dict) else None


def _metadata_matches_action(
    metadata: dict[str, object],
    action: dict[str, object],
) -> bool:
    identity_matches = all(
        metadata.get(key) == action.get(key)
        for key in (
            "capability",
            "operation",
            "arguments_digest",
            "target_identifiers",
        )
    )
    expected_command_digest = action.get("operation_digest")
    return identity_matches and (
        expected_command_digest is None
        or metadata.get("operation_digest") == expected_command_digest
    )


def _read_matches_action(
    read: dict[str, object],
    action: dict[str, object],
    registry: McpToolEffectRegistry,
) -> bool:
    read_server = str(read.get("reviewed_server") or "")
    read_tool = str(read.get("reviewed_tool") or "")
    write_server = str(action.get("reviewed_server") or "")
    write_tool = str(action.get("reviewed_tool") or "")
    if not registry.can_readback(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
    ):
        return False
    read_target = read.get("target_identifiers")
    action_target = action.get("target_identifiers")
    if not isinstance(read_target, dict) or not isinstance(action_target, dict):
        return False
    return registry.readback_targets_match(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
        read_targets=read_target,
        write_targets=action_target,
    )


def _action_has_readback(
    action: dict[str, object],
    registry: McpToolEffectRegistry,
) -> bool:
    return registry.has_readback_for(
        write_server=str(action.get("reviewed_server") or ""),
        write_tool=str(action.get("reviewed_tool") or ""),
    )


def _matching_read_digest(
    events: list[dict[str, object]],
    action: dict[str, object],
    *,
    event_start: int = 0,
    after_index: int = -1,
    registry: McpToolEffectRegistry,
) -> str:
    for index, event in enumerate(events):
        if index < event_start or index <= after_index or event.get("type") != "item.completed":
            continue
        metadata = _event_metadata(event)
        if metadata is None or metadata.get("effect") != EffectKind.READ_ONLY.value:
            continue
        digest = metadata.get("result_digest")
        if _read_matches_action(metadata, action, registry) and isinstance(digest, str):
            return digest
    return ""


def _validated_reconciliation(
    entries: tuple[AuditReconciliation, ...],
    events: list[dict[str, object]],
    actions: tuple[dict[str, object], ...],
    *,
    event_start: int,
    registry: McpToolEffectRegistry,
) -> dict[int, AuditReconciliation]:
    validated: dict[int, AuditReconciliation] = {}
    for entry in entries:
        action_index = entry.action_index
        if action_index >= len(actions):
            raise RuntimeError("audit_reconciliation_action_mismatch")
        matching_digest = any(
            index >= event_start
            and event.get("type") == "item.completed"
            and (metadata := _event_metadata(event)) is not None
            and metadata.get("effect") == EffectKind.READ_ONLY.value
            and metadata.get("result_digest") == entry.read_result_digest
            and _read_matches_action(metadata, actions[action_index], registry)
            for index, event in enumerate(events)
        )
        if not matching_digest:
            raise RuntimeError("audit_reconciliation_evidence_mismatch")
        validated[action_index] = entry
    return validated


def _action_receipt_operation_id(
    operation_id: str,
    action: dict[str, object],
    action_index: int,
) -> str:
    return json.dumps(
        {
            "proposal_operation_id": operation_id,
            "action_index": action_index,
            "capability": action.get("capability", ""),
            "operation": action.get("operation", ""),
            "operation_digest": action.get("operation_digest", ""),
            "arguments_digest": action.get("arguments_digest", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _action_completion_accounting(
    events: list[dict[str, object]],
    receipts: list[object],
    actions: tuple[dict[str, object], ...],
    *,
    operation_id: str,
    registry: McpToolEffectRegistry,
) -> tuple[set[int], bool]:
    starts: list[dict[str, object]] = []
    starts_per_action = [0] * len(actions)
    successes = [0] * len(actions)
    for event_index, event in enumerate(events):
        metadata = _event_metadata(event)
        if (
            metadata is None
            or metadata.get("effect") != EffectKind.EFFECTFUL.value
            or metadata.get("operation_id") != operation_id
        ):
            continue
        item = event.get("item")
        call_id = str(item.get("id") or item.get("call_id") or "") if isinstance(item, dict) else ""
        if event.get("type") == "item.started":
            candidates = [
                index
                for index, action in enumerate(actions)
                if _metadata_matches_action(metadata, action)
            ]
            persisted_index = metadata.get("action_index")
            action_index = (
                int(persisted_index)
                if isinstance(persisted_index, int)
                and persisted_index in candidates
                else min(candidates, key=starts_per_action.__getitem__)
                if candidates
                else None
            )
            if action_index is not None:
                action = actions[action_index]
                for prior in starts:
                    if (
                        not prior["closed"]
                        and prior["action_index"] == action_index
                        and _matching_read_digest(
                            events[:event_index],
                            action,
                            after_index=int(prior["event_index"]),
                            registry=registry,
                        )
                    ):
                        prior["closed"] = True
                starts_per_action[action_index] += 1
            starts.append(
                {
                    "call_id": call_id,
                    "action_index": action_index,
                    "event_index": event_index,
                    "closed": False,
                }
            )
            continue
        if event.get("type") not in {"item.completed", "item.failed"}:
            continue
        closure_action_index = metadata.get("action_index")
        start = next(
            (
                candidate
                for candidate in starts
                if not candidate["closed"]
                and candidate["call_id"] == call_id
                and (
                    not isinstance(closure_action_index, int)
                    or candidate["action_index"] == closure_action_index
                )
                and (
                    candidate["action_index"] is None
                    or _metadata_matches_action(
                        metadata, actions[int(candidate["action_index"])]
                    )
                )
            ),
            None,
        )
        if start is None:
            continue
        start["closed"] = True
        action_index = start["action_index"]
        if (
            event.get("type") == "item.completed"
            and action_index is not None
        ):
            successes[int(action_index)] += 1

    used_receipts: set[int] = set()
    for action_index, action in enumerate(actions):
        expected_receipt_id = _action_receipt_operation_id(
            operation_id, action, action_index
        )
        expected_cli = str(action.get("capability") or "").rsplit(".", 1)[-1]
        receipt_index = next(
            (
                index
                for index, receipt in enumerate(receipts)
                if index not in used_receipts
                and getattr(receipt, "operation_id", "") == expected_receipt_id
                and getattr(receipt, "completed", False)
                and getattr(receipt, "persisted", False)
                and getattr(receipt, "safe_to_confirm", False)
                and getattr(receipt, "cli", "") == expected_cli
                and getattr(receipt, "command_path", "") == action.get("operation")
                and getattr(receipt, "command_digest", "")
                == action.get("operation_digest")
            ),
            None,
        )
        if receipt_index is None:
            continue
        used_receipts.add(receipt_index)
        open_start = next(
            (
                start
                for start in starts
                if not start["closed"] and start["action_index"] == action_index
            ),
            None,
        )
        if open_start is not None:
            open_start["closed"] = True
        successes[action_index] += 1

    completed = {
        index for index, success_count in enumerate(successes) if success_count == 1
    }
    return completed, all(bool(start["closed"]) for start in starts)


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains_sensitive_value(value: object, *, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, dict):
        if _contains_sensitive_argv(value):
            return True
        return any(
            _is_sensitive_key(_normalized_key(str(key)))
            or _contains_sensitive_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_sensitive_value(item, depth=depth + 1) for item in value)
    if not isinstance(value, str):
        return False
    if _is_signed_url(value) or contains_credential(value):
        return True
    stripped = value.lstrip()
    if not stripped.startswith(("{", "[")) or len(value) > 64 * 1024:
        return False
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return False
    return _contains_sensitive_value(decoded, depth=depth + 1)


def _contains_sensitive_argv(value: dict[object, object]) -> bool:
    argv = value.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return False
    for token in argv:
        if not token.startswith("--"):
            continue
        flag = token[2:].partition("=")[0]
        if _is_sensitive_key(_normalized_key(flag)):
            return True
    return False
