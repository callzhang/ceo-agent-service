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
    ) -> AgentTurnRunResult[ResultT]:
        line_count = 0
        saw_json = False

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
            self.store.renew_agent_run_lease(
                run.id, owner=self.owner, lease_seconds=LEASE_SECONDS
            )
            if on_progress is not None:
                on_progress()
            new_session = _session_id(payload)
            if new_session:
                self.store.set_agent_run_session(
                    run.id,
                    new_session,
                    owner=self.owner,
                    transcript_start_line=run.transcript_start_line,
                )
                if persist_conversation_session:
                    self.store.upsert_conversation(
                        self.task.conversation_id,
                        self.task.conversation_title,
                        self.task.single_chat,
                        new_session,
                    )
            event = self._normalized_effect_event(
                payload, read_only=run.role is AgentRole.CONSUMER
            )
            if event is not None:
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
            self._fail_running(run, "agent_read_only_violation")
            raise
        except Exception:
            self._fail_running(run, "codex_process_failed")
            raise
        transcript_end = run.transcript_start_line + line_count
        outcome = getattr(result, "outcome")
        side_effect_state = getattr(result, "side_effect_state", SideEffectState.NONE)
        persisted = self.store.get_agent_run(run.id)
        assert persisted is not None
        if run.role is AgentRole.AUDIT:
            self._validate_audit_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
            )
        if outcome in {ConsumerOutcome.FAILED, AuditOutcome.FAILED}:
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
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=completed.transcript_end_line,
        )

    def _normalized_effect_event(
        self, payload: dict[str, object], *, read_only: bool
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
            "operation_digest": operation_digest,
            "target_identifiers": target_identifiers,
            "arguments_digest": _json_digest(item.get("arguments")),
        }
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
            if (
                persisted.side_effect_state == SideEffectState.CONFIRMED.value
                and _effect_actions_match(
                    persisted.tool_events,
                    expected_effect_actions,
                )
                and _effect_actions_were_verified(persisted.tool_events)
            ):
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


def _effect_actions_match(
    events: list[dict[str, object]],
    expected_actions: tuple[dict[str, object], ...],
) -> bool:
    actual_actions: list[dict[str, object]] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict) or metadata.get("effect") != "effectful":
            continue
        operation = metadata.get("operation")
        capability = metadata.get("capability")
        arguments_digest = metadata.get("arguments_digest")
        if (
            not isinstance(capability, str)
            or not isinstance(operation, str)
            or not isinstance(arguments_digest, str)
        ):
            return False
        actual_actions.append(
            {
                "capability": capability,
                "operation": operation,
                "arguments_digest": arguments_digest,
            }
        )
    if len(actual_actions) != len(expected_actions):
        return False
    unmatched = list(actual_actions)
    for expected in expected_actions:
        expected_capability = expected.get("capability")
        expected_operation = expected.get("operation")
        expected_arguments_digest = expected.get("arguments_digest")
        if (
            not isinstance(expected_capability, str)
            or not isinstance(expected_operation, str)
            or not isinstance(expected_arguments_digest, str)
        ):
            return False
        match_index = next(
            (
                index
                for index, actual in enumerate(unmatched)
                if actual == expected
            ),
            None,
        )
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return True


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _effect_actions_were_verified(events: list[dict[str, object]]) -> bool:
    effects: list[tuple[int, dict[str, object]]] = []
    reads: list[tuple[int, dict[str, object]]] = []
    for index, event in enumerate(events):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            continue
        target = metadata.get("target_identifiers")
        normalized_target = target if isinstance(target, dict) else {}
        effect = metadata.get("effect")
        if effect == "effectful":
            effects.append((index, normalized_target))
        elif effect == "read_only":
            reads.append((index, normalized_target))
    if not effects:
        return False
    for effect_index, effect_target in effects:
        if not any(
            read_index > effect_index
            and (
                not effect_target
                or bool(set(effect_target.values()) & set(read_target.values()))
            )
            for read_index, read_target in reads
        ):
            return False
    return True


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
