from __future__ import annotations

import json
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from app.agent_contracts import AuditOutcome, ConsumerOutcome
from app.agent_result import EffectKind, SideEffectState
from app.agent_runner import (
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


@dataclass(frozen=True)
class AgentTurnRunResult(Generic[ResultT]):
    run_id: int
    result: ResultT
    transcript_start_line: int
    transcript_end_line: int


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
        recover_unknown: bool = False,
    ) -> AgentTurnRunResult[ResultT]:
        line_count = 0
        saw_json = False
        recovery_effect_started = False
        recovery_reads: list[dict[str, object]] = []
        recovery_started_actions: set[int] = set()
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
            nonlocal recovery_effect_started
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
                read_only=run.role is AgentRole.CONSUMER,
                operation_id=run.operation_id,
            )
            if event is not None:
                item = event.get("item")
                metadata = item.get("metadata") if isinstance(item, dict) else None
                effect = metadata.get("effect") if isinstance(metadata, dict) else None
                if (
                    recover_unknown
                    and event.get("type") == "item.started"
                    and effect == EffectKind.EFFECTFUL.value
                ):
                    action_index = _matching_action_index(
                        metadata, expected_effect_actions
                    )
                    if (
                        action_index is None
                        or action_index in completed_before_recovery
                        or action_index in recovery_started_actions
                        or not any(
                            _read_matches_action(
                                read,
                                expected_effect_actions[action_index],
                                self.effects,
                            )
                            for read in recovery_reads
                        )
                    ):
                        raise RuntimeError("audit_recovery_read_required")
                    recovery_started_actions.add(action_index)
                    recovery_effect_started = True
                if (
                    recover_unknown
                    and event.get("type") == "item.completed"
                    and effect == EffectKind.READ_ONLY.value
                ):
                    assert isinstance(metadata, dict)
                    recovery_reads.append(metadata)
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
                approval_policy="never",
                developer_instructions=contract_instructions,
                use_approval_bypass=False,
                ignore_user_config=True,
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
            self._raise_for_process_failure(process)
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
            code = (
                str(exc)
                if str(exc).startswith("audit_recovery_")
                else "codex_process_failed"
            )
            if recover_unknown:
                self._defer_unknown(run, code)
            else:
                self._fail_running(run, "codex_process_failed")
            raise
        transcript_end = transcript_start + line_count
        outcome = getattr(result, "outcome")
        side_effect_state = getattr(result, "side_effect_state", SideEffectState.NONE)
        persisted = self.store.get_agent_run(run.id)
        assert persisted is not None
        if run.role is AgentRole.AUDIT and recover_unknown:
            self._validate_audit_recovery_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
                recovery_effect_started=recovery_effect_started,
                recovery_event_start=recovery_event_start,
            )
        elif run.role is AgentRole.AUDIT:
            self._validate_audit_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
            )
        if recover_unknown:
            if outcome is AuditOutcome.EXECUTED:
                self.store.complete_agent_run(
                    run.id,
                    result.model_dump(mode="json"),
                    owner=self.owner,
                    side_effect_state=SideEffectState.CONFIRMED.value,
                    transcript_end_line=transcript_end,
                    expected_status="unknown",
                )
            elif outcome is AuditOutcome.NEEDS_HUMAN:
                self.store.complete_agent_run(
                    run.id,
                    result.model_dump(mode="json"),
                    owner=self.owner,
                    side_effect_state=SideEffectState.UNKNOWN.value,
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
        validated_receipt: dict[str, object] | None = None
        if call.server == "agent_cli" and call.tool in {
            "execute_reviewed_read",
            "execute_reviewed_write",
        }:
            arguments = item.get("arguments")
            argv = arguments.get("argv") if isinstance(arguments, dict) else None
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
            "arguments_digest": _json_digest(item.get("arguments")),
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

    def _validate_audit_recovery_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
        recovery_effect_started: bool,
        recovery_event_start: int,
    ) -> None:
        outcome = getattr(result, "outcome")
        if getattr(result, "proposal_revision") != run.proposal_revision:
            raise RuntimeError("audit_proposal_revision_mismatch")
        if outcome is AuditOutcome.NEEDS_HUMAN:
            if recovery_effect_started:
                raise RuntimeError("audit_recovery_effect_unresolved")
            reconciled = _reconciled_action_indexes(
                persisted.tool_events,
                expected_effect_actions,
                event_start=recovery_event_start,
                registry=self.effects,
            )
            completed_before = _action_completion_accounting(
                persisted.tool_events[:recovery_event_start],
                self.store.list_agent_execution_receipts(run.id),
                expected_effect_actions,
                operation_id=run.operation_id,
                registry=self.effects,
            )[0]
            unresolved = set(range(len(expected_effect_actions))) - completed_before
            if not unresolved or any(
                index not in reconciled
                and _action_has_readback(expected_effect_actions[index], self.effects)
                for index in unresolved
            ):
                raise RuntimeError("audit_recovery_evidence_missing")
            return
        if outcome in {AuditOutcome.UNKNOWN, AuditOutcome.FAILED}:
            return
        if outcome is not AuditOutcome.EXECUTED:
            raise RuntimeError("audit_recovery_result_invalid")
        external_result = getattr(result, "external_result")
        if external_result.operation_id != run.operation_id:
            raise RuntimeError("audit_operation_mismatch")
        reconciled = _reconciled_action_indexes(
            persisted.tool_events,
            expected_effect_actions,
            event_start=recovery_event_start,
            registry=self.effects,
        )
        receipts = self.store.list_agent_execution_receipts(run.id)
        completed = _action_completion_accounting(
            persisted.tool_events,
            receipts,
            expected_effect_actions,
            operation_id=run.operation_id,
            registry=self.effects,
        )[0]
        for action_index in sorted(reconciled - completed):
            action = expected_effect_actions[action_index]
            metadata = _matching_effect_metadata(
                persisted.tool_events,
                action,
                operation_id=run.operation_id,
                event_type="item.started",
            )
            read_result_digest = _matching_read_digest(
                persisted.tool_events,
                action,
                event_start=recovery_event_start,
                registry=self.effects,
            )
            if metadata is None or not read_result_digest:
                continue
            command_digest = metadata.get("operation_digest")
            if command_digest != action.get("operation_digest"):
                continue
            self.store.record_agent_execution_receipt(
                run.id,
                receipt_id=f"reconciliation:{run.operation_id}:{read_result_digest}",
                operation_id=_action_receipt_operation_id(run.operation_id, action),
                cli=str(metadata.get("native_cli") or metadata.get("capability")),
                command_path=str(metadata.get("operation")),
                command_digest=str(command_digest),
                exit_code=0,
                owner=self.owner,
                expected_status="unknown",
            )
        completed, all_effects_closed = _action_completion_accounting(
            persisted.tool_events,
            self.store.list_agent_execution_receipts(run.id),
            expected_effect_actions,
            operation_id=run.operation_id,
            registry=self.effects,
        )
        missing_live_readback = {
            index
            for index in completed
            if _action_has_readback(expected_effect_actions[index], self.effects)
            and index not in reconciled
        }
        if (
            completed != set(range(len(expected_effect_actions)))
            or not all_effects_closed
            or missing_live_readback
        ):
            raise RuntimeError("audit_execution_evidence_missing")

    @staticmethod
    def _raise_for_process_failure(process: ProcessRunResult) -> None:
        if process.timed_out:
            raise RuntimeError("codex_process_timeout")
        if process.returncode != 0:
            raise RuntimeError("codex_process_failed")

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


def _matching_action_index(
    metadata: dict[str, object] | None,
    actions: tuple[dict[str, object], ...],
) -> int | None:
    if metadata is None:
        return None
    return next(
        (
            index
            for index, action in enumerate(actions)
            if _metadata_matches_action(metadata, action)
        ),
        None,
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


def _reconciled_action_indexes(
    events: list[dict[str, object]],
    actions: tuple[dict[str, object], ...],
    *,
    event_start: int,
    registry: McpToolEffectRegistry,
) -> set[int]:
    return {
        index
        for index, action in enumerate(actions)
        if _matching_read_digest(
            events,
            action,
            event_start=event_start,
            registry=registry,
        )
    }


def _action_receipt_operation_id(
    operation_id: str,
    action: dict[str, object],
) -> str:
    return f"{operation_id}:{action.get('arguments_digest', '')}"


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
            action_index = (
                min(candidates, key=starts_per_action.__getitem__)
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
        start = next(
            (
                candidate
                for candidate in starts
                if not candidate["closed"]
                and candidate["call_id"] == call_id
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
            and _matching_read_digest(
                events,
                actions[int(action_index)],
                after_index=event_index,
                registry=registry,
            )
        ):
            successes[int(action_index)] += 1

    used_receipts: set[int] = set()
    for action_index, action in enumerate(actions):
        expected_receipt_ids = {
            operation_id,
            _action_receipt_operation_id(operation_id, action),
        }
        expected_cli = str(action.get("capability") or "").rsplit(".", 1)[-1]
        receipt_index = next(
            (
                index
                for index, receipt in enumerate(receipts)
                if index not in used_receipts
                and getattr(receipt, "operation_id", "") in expected_receipt_ids
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
