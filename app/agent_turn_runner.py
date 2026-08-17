from __future__ import annotations

import json
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generic, TypeVar, cast

from pydantic import ValidationError

from app.agent_contracts import (
    AuditAgentResult,
    AuditExternalResult,
    AuditReconciliation,
    AuditOutcome,
    ConsumerOutcome,
    ReconciliationDisposition,
)
from app.agent_result import (
    AgentError,
    EffectKind,
    ResultParseError,
    SideEffectState,
)
from app.agent_skill_usage import (
    LoadedSkillReceipt,
    loaded_skill_receipts,
    normalized_read_skill_metadata,
)
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
from app.codex_failure import (
    CODEX_PROVIDER_AUTH_FAILED,
    CODEX_PROVIDER_UNAVAILABLE,
    classify_codex_process_failure,
)
from app.codex_history import (
    count_codex_session_lines,
    extract_codex_mcp_tool_results_from_session,
)
from app.codex_runner import CodexRunner, _codex_home
from app.codex_capacity import (
    CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    codex_provider_failure_code,
    is_codex_capacity_exhausted,
)
from app.leak_check import contains_credential
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
    describe_native_command,
    native_command_argv,
)
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import (
    RECONCILIATION_EVENT_LIMIT_ERROR,
    AgentRole,
    AgentRun,
    AutoReplyStore,
    ReplyTask,
)


ResultT = TypeVar("ResultT")
ProcessExecutor = Callable[..., ProcessRunResult]
UNKNOWN_RECONCILIATION_RETRY_BASE_SECONDS = 60
UNKNOWN_RECONCILIATION_RETRY_MAX_SECONDS = 15 * 60


def unknown_reconciliation_retry_at(
    attempts: int, *, now: datetime | None = None
) -> str:
    delay_seconds = min(
        UNKNOWN_RECONCILIATION_RETRY_BASE_SECONDS
        * (2 ** max(attempts - 1, 0)),
        UNKNOWN_RECONCILIATION_RETRY_MAX_SECONDS,
    )
    current = now or datetime.now(timezone.utc)
    return (current.astimezone(timezone.utc) + timedelta(seconds=delay_seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


@dataclass(frozen=True)
class AgentTurnRunResult(Generic[ResultT]):
    run_id: int
    result: ResultT
    transcript_start_line: int
    transcript_end_line: int


def _process_failure_code(process: ProcessRunResult) -> str:
    code = classify_codex_process_failure(process.stdout, process.stderr)
    if code == CODEX_PROVIDER_AUTH_FAILED:
        return f"{code}: native Codex CLI authentication is unavailable"
    if code == CODEX_PROVIDER_UNAVAILABLE:
        provider_code = codex_provider_failure_code(
            f"{process.stdout}\n{process.stderr}"
        )
        if provider_code == CODEX_PROVIDER_CAPACITY_EXHAUSTED:
            return provider_code
    if is_codex_capacity_exhausted(f"{process.stdout}\n{process.stderr}"):
        return CODEX_PROVIDER_CAPACITY_EXHAUSTED
    return code


def _agent_process_error_code(exc: Exception) -> str:
    code = str(exc).strip()
    if code.startswith(CODEX_PROVIDER_AUTH_FAILED):
        return code
    if code in {CODEX_PROVIDER_UNAVAILABLE, CODEX_PROVIDER_CAPACITY_EXHAUSTED}:
        return code
    if isinstance(exc, ResultParseError):
        if code == "no valid typed result JSON found in Codex JSONL":
            return "codex_result_missing"
        return "codex_result_invalid"
    return "codex_process_failed"


def _result_parse_error_detail(exc: ResultParseError) -> str:
    """Keep validation locations, never the model output that failed validation."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ValidationError):
            fields = []
            for error in current.errors():
                location = ".".join(str(part) for part in error.get("loc", ())) or "result"
                kind = str(error.get("type") or "validation_error")
                fields.append(f"{location}: {kind}")
            if fields:
                return "; ".join(fields[:8])
        current = current.__cause__ or current.__context__
    return str(exc)[:240]


def _is_terminal_codex_auth_failure(code: str) -> bool:
    return code.startswith(CODEX_PROVIDER_AUTH_FAILED)


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
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
    ) -> None:
        self.store = store
        self.task = task
        self.owner = owner
        self.codex = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor or run_process_with_idle_timeout
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.native_cli = native_cli_classifier or NativeCliMetadataClassifier()

    def execute(
        self,
        *,
        run: AgentRun,
        prompt: str,
        session_id: str | None,
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
        image_paths: list[Path] | None = None,
        required_skill_receipts: tuple[LoadedSkillReceipt, ...] = (),
    ) -> AgentTurnRunResult[ResultT]:
        if recovery_phase not in {"", "reconcile", "execute"}:
            raise ValueError("invalid recovery phase")
        recover_unknown = bool(recovery_phase)
        recovery_authorizations = recovery_authorizations or {}
        line_count = 0
        saw_json = False
        primary_turn_started = False
        primary_turn_closed = False
        recovery_started_actions: set[int] = set()
        effect_action_counts = [0] * len(expected_effect_actions)
        effect_action_by_call_id: dict[str, int] = {}
        completed_effect_call_ids: set[str] = set()
        suppressed_session_replay_call_ids: set[str] = set()
        observed_session_id = ""
        session_transcript_end = 0
        turn_event_start = len(run.tool_events)
        recovery_event_start = turn_event_start
        completed_before_recovery = (
            _action_completion_accounting(
                [],
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

        def persist_effect_event(
            payload: dict[str, object], *, from_session_replay: bool = False
        ) -> None:
            nonlocal line_count, saw_json
            nonlocal primary_turn_started, primary_turn_closed
            event = self._normalized_effect_event(
                payload,
                read_only=(
                    run.role is AgentRole.CONSUMER
                    or recovery_phase == "reconcile"
                ),
                operation_id=run.operation_id,
                require_recovery_authorization=recovery_phase == "execute",
            )
            if event is None:
                return
            item = event.get("item")
            metadata = item.get("metadata") if isinstance(item, dict) else None
            effect = metadata.get("effect") if isinstance(metadata, dict) else None
            call_id = str(item.get("id") or item.get("call_id") or "") if isinstance(item, dict) else ""
            if (
                event.get("type") == "item.started"
                and effect == EffectKind.EFFECTFUL.value
                and required_skill_receipts
            ):
                current_run = self.store.get_agent_run(run.id)
                persisted_events = (
                    current_run.tool_events[turn_event_start:]
                    if current_run
                    else ()
                )
                observed = {
                    (receipt.name, receipt.path, receipt.sha256)
                    for receipt in loaded_skill_receipts(persisted_events)
                }
                required = {
                    (receipt.name, receipt.path, receipt.sha256)
                    for receipt in required_skill_receipts
                }
                if not required.issubset(observed):
                    raise AgentReadOnlyViolationError(
                        "audit_skill_reread_missing"
                    )
            if event.get("type") == "item.completed" and call_id:
                completed_effect_call_ids.add(call_id)
            if effect == EffectKind.EFFECTFUL.value and isinstance(metadata, dict):
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
                        # The local Codex session can contain the same completed
                        # controlled call already observed in the live JSON stream,
                        # but with a different call ID. Replaying it would create a
                        # second lifecycle in our ledger and falsely make one action
                        # look like two executions. Keep session-only recovery, but
                        # never replay an action that live streaming already covered.
                        if from_session_replay and effect_action_counts[action_index]:
                            suppressed_session_replay_call_ids.add(call_id)
                            return
                        metadata["action_index"] = action_index
                        effect_action_counts[action_index] += 1
                        effect_action_by_call_id[call_id] = action_index
                else:
                    if from_session_replay and call_id in suppressed_session_replay_call_ids:
                        return
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
            self._record_direct_send_receipt(event, payload, run=run)

        def persist_line(line: str) -> None:
            nonlocal line_count, saw_json, observed_session_id
            nonlocal primary_turn_started, primary_turn_closed
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
            payload_type = payload.get("type")
            if primary_turn_closed:
                return
            if payload_type == "turn.started":
                primary_turn_started = True
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
                observed_session_id = new_session
                if recover_unknown:
                    # Reconciliation and the narrowly authorized follow-up write
                    # run as fresh sessions. Preserve the original session on the
                    # AgentRun as immutable execution history rather than replacing
                    # it with a recovery session identifier.
                    pass
                else:
                    self.store.set_agent_run_session(
                        run.id,
                        new_session,
                        owner=self.owner,
                        transcript_start_line=run.transcript_start_line,
                        allow_consumer_session_handoff=(
                            run.role is AgentRole.CONSUMER
                        ),
                    )
                if persist_conversation_session and not recover_unknown:
                    self.store.upsert_conversation(
                        self.task.conversation_id,
                        self.task.conversation_title,
                        self.task.single_chat,
                        new_session,
                    )
            persist_effect_event(payload)
            if primary_turn_started and payload_type in {
                "turn.completed",
                "turn.failed",
            }:
                primary_turn_closed = True

        try:
            command = self.codex.build_command(
                prompt=prompt,
                session_id=session_id,
                use_output_schema=False,
                approval_policy="untrusted" if allow_effectful_tools else "never",
                developer_instructions=developer_instructions,
                use_approval_bypass=allow_effectful_tools,
                image_paths=image_paths,
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
            session_for_receipts = observed_session_id or session_id or run.codex_session_id
            if session_for_receipts:
                session_start = 0 if recover_unknown else transcript_start
                # The CLI can flush local session events after the JSON stream has
                # ended. Wait briefly for a stable line count, then replay only
                # previously unseen completed calls through the normal validator.
                previous_count = -1
                for _ in range(4):
                    session_end = count_codex_session_lines(
                        session_for_receipts, codex_home=_codex_home()
                    )
                    if session_end == previous_count:
                        break
                    previous_count = session_end
                    time.sleep(0.05)
                session_transcript_end = max(previous_count, 0)
                for completed_payload in extract_codex_mcp_tool_results_from_session(
                    session_for_receipts,
                    codex_home=_codex_home(),
                    start_line=session_start,
                    end_line=max(previous_count, 0),
                ):
                    completed_item = completed_payload.get("item")
                    call_id = (
                        str(completed_item.get("id") or "")
                        if isinstance(completed_item, dict)
                        else ""
                    )
                    if not call_id or call_id in completed_effect_call_ids:
                        continue
                    # Session history contains every MCP call, including
                    # read-only integrations owned outside this effect registry.
                    # They are useful audit context but cannot create execution
                    # evidence here; only a reviewed call may be replayed into
                    # the durable effect ledger.
                    call = self.effects.classify(completed_item)
                    if call is None:
                        continue
                    if run.role is AgentRole.CONSUMER:
                        continue
                    if recovery_phase != "reconcile" and call.effect is EffectKind.READ_ONLY:
                        continue
                    # A session-only read without a controlled receipt cannot
                    # become audit evidence. Ignore it here; reconciliation
                    # validation will still reject a result that relies on it.
                    try:
                        self._normalized_effect_event(
                            completed_payload,
                            read_only=(recovery_phase == "reconcile"),
                            operation_id=run.operation_id,
                            require_recovery_authorization=(
                                recovery_phase == "execute"
                            ),
                        )
                    except AgentReadOnlyViolationError as exc:
                        if str(exc) != "agent_cli_receipt_invalid":
                            raise
                        continue
                    start_payload = {
                        "type": "item.started",
                        "item": {
                            key: value
                            for key, value in completed_item.items()
                            if key not in {"status", "result"}
                        }
                        | {"status": "in_progress"},
                    }
                    persist_effect_event(start_payload, from_session_replay=True)
                    persist_effect_event(completed_payload, from_session_replay=True)
            if _contains_sensitive_value(result.model_dump(mode="json")):
                raise ValueError("agent_result_contains_sensitive_value")
        except ResultParseError as exc:
            fallback = _recovery_execution_result_from_receipts(
                run=run,
                recovery_phase=recovery_phase,
                persisted=self.store.get_agent_run(run.id),
                expected_effect_actions=expected_effect_actions,
                recovery_started_actions=recovery_started_actions,
                authorized_recovery_actions=authorized_recovery_actions,
                registry=self.effects,
                store=self.store,
            )
            if fallback is None:
                parse_error_code = _agent_process_error_code(exc)
                if recover_unknown:
                    self._defer_unknown(run, parse_error_code)
                else:
                    self._fail_running(
                        run,
                        parse_error_code,
                        detail=_result_parse_error_detail(exc),
                    )
                raise
            result = cast(ResultT, fallback)
        except AgentReadOnlyViolationError as exc:
            code = str(exc).strip() or "agent_read_only_violation"
            if recover_unknown:
                self._defer_unknown(run, code)
            else:
                self._fail_running(run, code)
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
            if provider_recovery in {
                CODEX_PROVIDER_UNAVAILABLE,
                CODEX_PROVIDER_CAPACITY_EXHAUSTED,
            }:
                raise RuntimeError(code) from exc
            raise
        transcript_end = (
            run.transcript_end_line
            if recover_unknown
            else max(transcript_start + line_count, session_transcript_end)
        )
        outcome = getattr(result, "outcome")
        side_effect_state = getattr(result, "side_effect_state", SideEffectState.NONE)
        persisted = self.store.get_agent_run(run.id)
        assert persisted is not None
        if run.role is AgentRole.AUDIT and recovery_phase == "reconcile":
            reconciliation = self._validate_audit_reconciliation_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
                recovery_event_start=recovery_event_start,
                completed_before_recovery=completed_before_recovery,
            )
            result = result.model_copy(
                update={
                    "reconciliation": tuple(
                        reconciliation[index] for index in sorted(reconciliation)
                    )
                }
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
                required_skill_receipts=required_skill_receipts,
                turn_event_start=turn_event_start,
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
        require_recovery_authorization: bool = False,
    ) -> dict[str, object] | None:
        if payload.get("type") not in {"item.started", "item.completed", "item.failed"}:
            return None
        item = payload.get("item")
        if not isinstance(item, dict):
            return None
        if item.get("type") == "command_execution":
            if read_only:
                raise AgentReadOnlyViolationError("agent_shell_execution_forbidden")
            command = self.native_cli.classify(item)
            if command is None:
                raise AgentReadOnlyViolationError("agent_shell_execution_forbidden")
            if command.effect is not EffectKind.READ_ONLY:
                raise AgentReadOnlyViolationError("agent_write_forbidden")
            status = {
                "item.started": "in_progress",
                "item.completed": "completed",
                "item.failed": "failed",
            }[str(payload["type"])]
            return {
                "type": str(payload["type"]),
                "item": {
                    "type": "command_execution",
                    "id": str(item.get("id") or item.get("call_id") or ""),
                    "status": status,
                    "metadata": {
                        "effect": EffectKind.READ_ONLY.value,
                        "capability": f"native_cli.{command.cli}",
                        "operation": command.command_path,
                        "operation_digest": command.command_digest,
                        "target_identifiers": command.target_identifiers,
                        "arguments_digest": _json_digest(
                            {"command": item.get("command")}
                        ),
                        "native_cli": command.cli,
                    },
                },
            }
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
        skill_metadata: dict[str, str] | None = None
        controlled_receipt_failed = False
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
                if read_only and call.tool == "execute_reviewed_read":
                    if payload.get("type") != "item.completed":
                        return None
                    failure_code = _agent_cli_tool_error(item.get("result"))
                    if failure_code:
                        return _failed_agent_cli_read_event(item, failure_code)
                raise AgentReadOnlyViolationError("agent_cli_command_invalid")
            capability = f"agent_cli.{descriptor.cli}"
            operation = descriptor.command_path
            operation_digest = descriptor.command_digest
            target_identifiers = descriptor.target_identifiers
            native_cli = descriptor.cli
            if payload.get("type") == "item.completed":
                receipt = _agent_cli_receipt(
                    item.get("result"),
                    allow_error=True,
                )
                if receipt is None:
                    failure_code = _agent_cli_tool_error(item.get("result"))
                    if (
                        read_only
                        and call.tool == "execute_reviewed_read"
                        and failure_code
                    ):
                        return _failed_agent_cli_read_event(item, failure_code)
                    raise AgentReadOnlyViolationError(
                        failure_code or "agent_cli_receipt_missing"
                    )
                if receipt.get("operation") != descriptor.command_path:
                    raise AgentReadOnlyViolationError("agent_cli_receipt_operation_mismatch")
                if receipt.get("operation_digest") != operation_digest:
                    raise AgentReadOnlyViolationError("agent_cli_receipt_digest_mismatch")
                if receipt.get("target_identifiers") != target_identifiers:
                    raise AgentReadOnlyViolationError("agent_cli_receipt_target_mismatch")
                if (
                    require_recovery_authorization
                    and call.effect is EffectKind.EFFECTFUL
                    and (not authorization_id or receipt.get("authorization_id") != authorization_id)
                ):
                    raise AgentReadOnlyViolationError(
                        "agent_cli_receipt_authorization_mismatch"
                    )
                validated_receipt = receipt
                controlled_receipt_failed = (
                    "error" in receipt or item.get("status") != "completed"
                )
        elif call.server == "agent_cli" and call.tool == "read_skill":
            if payload.get("type") == "item.completed":
                skill_metadata = normalized_read_skill_metadata(
                    item.get("arguments"), item.get("result")
                )
                controlled_receipt_failed = (
                    skill_metadata is None
                    or item.get("status") != "completed"
                )
        event_type = str(payload["type"])
        if controlled_receipt_failed:
            event_type = "item.failed"
        elif (
            event_type == "item.completed"
            and validated_receipt is None
            and skill_metadata is None
            and not _mcp_call_completed(payload)
        ):
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
        if call.server == "agent_cli" and call.tool == "read_skill":
            requested_skill_path = _requested_skill_path(item.get("arguments"))
            if requested_skill_path:
                metadata["requested_skill_path"] = requested_skill_path
        if controlled_receipt_failed and validated_receipt is not None:
            receipt_error = validated_receipt.get("error")
            if isinstance(receipt_error, dict):
                failure_code = receipt_error.get("code")
                if isinstance(failure_code, str) and failure_code:
                    metadata["failure_code"] = failure_code
                failure_retryable = receipt_error.get("retryable")
                if isinstance(failure_retryable, bool):
                    metadata["failure_retryable"] = failure_retryable
                failure_gate_state = receipt_error.get("gate_state")
                if isinstance(failure_gate_state, str) and failure_gate_state:
                    metadata["failure_gate_state"] = failure_gate_state
                failure_detail = receipt_error.get("detail")
                if isinstance(failure_detail, str) and failure_detail:
                    metadata["failure_detail"] = failure_detail
        if controlled_receipt_failed and call.tool == "read_skill":
            metadata["failure_code"] = "agent_cli_skill_receipt_invalid"
        if event_type == "item.completed" and skill_metadata is not None:
            metadata.update(skill_metadata)
        if event_type == "item.completed":
            result_digest = (
                validated_receipt.get("result_digest")
                if validated_receipt is not None
                else _json_digest(item.get("result"))
            )
            if isinstance(result_digest, str) and result_digest:
                metadata["result_digest"] = result_digest
            result_identifiers = self.effects.result_identifiers(
                server=call.server,
                tool=call.tool,
                operation=operation,
                result=(
                    validated_receipt
                    if validated_receipt is not None
                    else item.get("result")
                ),
            )
            if result_identifiers:
                metadata["result_identifiers"] = result_identifiers
        elif controlled_receipt_failed and validated_receipt is not None:
            result_digest = validated_receipt.get("result_digest")
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

    def _record_direct_send_receipt(
        self,
        event: dict[str, object],
        payload: dict[str, object],
        *,
        run: AgentRun,
    ) -> None:
        """Persist the service delivery fact for a completed reviewed chat send."""
        if event.get("type") != "item.completed":
            return
        event_item = event.get("item")
        metadata = (
            event_item.get("metadata") if isinstance(event_item, dict) else None
        )
        raw_item = payload.get("item")
        arguments = raw_item.get("arguments") if isinstance(raw_item, dict) else None
        argv = native_command_argv(
            {"type": "command_execution", "argv": arguments.get("argv")}
            if isinstance(arguments, dict)
            else {}
        )
        if (
            not isinstance(metadata, dict)
            or not self._is_recordable_dingtalk_chat_delivery(metadata, argv)
        ):
            return
        reply_text = _command_option_value(argv, "--text")
        if not reply_text or self.store.has_sent_reply_for_trigger(
            self.task.conversation_id, self.task.trigger_message_id
        ):
            return
        self.store.record_sent_reply(
            self.task.conversation_id,
            self.task.trigger_message_id,
            reply_text,
            send_result_json=json.dumps(
                {
                    "agent_run_id": run.id,
                    "operation_id": run.operation_id,
                    "operation_digest": metadata.get("operation_digest", ""),
                    "result_digest": metadata.get("result_digest", ""),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def _is_recordable_dingtalk_chat_delivery(
        self,
        metadata: dict[str, object],
        argv: tuple[str, ...] | None,
    ) -> bool:
        if _is_dingtalk_chat_send_argv(metadata, argv):
            return True
        recipient = _command_option_value(argv, "--to")
        return (
            self.task.single_chat
            and metadata.get("operation") == "chat +dm"
            and bool(recipient)
            and recipient.casefold() == self.task.trigger_sender.casefold()
            and bool(_command_option_value(argv, "--text"))
        )

    def _require_direct_send_receipt(
        self,
        run: AgentRun,
        expected_effect_actions: tuple[dict[str, object], ...],
    ) -> None:
        if not any(
            _is_expected_dingtalk_chat_send(action)
            for action in expected_effect_actions
        ):
            return
        if self.store.has_sent_reply_for_trigger(
            self.task.conversation_id, self.task.trigger_message_id
        ):
            return
        self.store.mark_agent_run_unknown(
            run.id,
            {"code": "audit_delivery_ledger_missing", "retryable": True},
            owner=self.owner,
        )
        raise RuntimeError("audit_delivery_ledger_missing")

    def _validate_audit_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
        required_skill_receipts: tuple[LoadedSkillReceipt, ...],
        turn_event_start: int,
    ) -> None:
        outcome = getattr(result, "outcome")
        turn_events = persisted.tool_events[turn_event_start:]
        observed_receipts = loaded_skill_receipts(turn_events)
        observed_by_identity = {
            (receipt.name, receipt.path): receipt.sha256
            for receipt in observed_receipts
        }
        missing_receipts = tuple(
            receipt
            for receipt in required_skill_receipts
            if (receipt.name, receipt.path) not in observed_by_identity
        )
        mismatched_receipts = tuple(
            receipt
            for receipt in required_skill_receipts
            if observed_by_identity.get((receipt.name, receipt.path))
            not in {None, receipt.sha256}
        )
        attempted_paths = _attempted_skill_paths(turn_events)
        unreadable_receipts = tuple(
            receipt for receipt in missing_receipts if receipt.path in attempted_paths
        )
        absent_receipts = tuple(
            receipt for receipt in missing_receipts if receipt.path not in attempted_paths
        )
        if absent_receipts or (
            unreadable_receipts and outcome is not AuditOutcome.REVISION_REQUIRED
        ):
            self._fail_running(run, "audit_skill_reread_missing")
            raise AgentReadOnlyViolationError("audit_skill_reread_missing")
        if mismatched_receipts and outcome is not AuditOutcome.REVISION_REQUIRED:
            self._fail_running(run, "audit_skill_reread_mismatch")
            raise AgentReadOnlyViolationError("audit_skill_reread_mismatch")
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
                if not _actions_have_required_readbacks(
                    persisted.tool_events,
                    expected_effect_actions,
                    self.effects,
                ):
                    self.store.mark_agent_run_unknown(
                        run.id,
                        {"code": "audit_external_readback_missing", "retryable": True},
                        owner=self.owner,
                    )
                    raise RuntimeError("audit_external_readback_missing")
                self._require_direct_send_receipt(run, expected_effect_actions)
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
        completed_before_recovery: set[int],
    ) -> dict[int, AuditReconciliation]:
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
        # A completed tool lifecycle event is not enough to suppress recovery.
        # This run is explicitly ``unknown`` because the original write has no
        # durable receipt. Only a receipt that existed before the recovery turn
        # can prove the action was already reconciled; a receipt produced during
        # this turn must not suppress the readback required for that same turn.
        completed_by_events = completed_before_recovery
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
        return reconciliation

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
        if not _actions_have_required_readbacks(
            persisted.tool_events,
            expected_effect_actions,
            self.effects,
        ):
            raise RuntimeError("audit_external_readback_missing")
        self._require_direct_send_receipt(run, expected_effect_actions)

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

    def _fail_running(self, run: AgentRun, code: str, *, detail: str = "") -> None:
        persisted = self.store.get_agent_run(run.id)
        if persisted is not None and persisted.status == "running":
            if persisted.side_effect_state == SideEffectState.NONE.value:
                terminal_auth_failure = _is_terminal_codex_auth_failure(code)
                self.store.fail_agent_run(
                    run.id,
                    {
                        "code": code,
                        "retryable": not terminal_auth_failure,
                        "authorization_required": False,
                        **({"detail": detail} if detail else {}),
                    },
                    owner=self.owner,
                )
            elif failure := _closed_effect_failure(persisted, fallback_code=code):
                self.store.fail_agent_run(
                    run.id,
                    {
                        **failure,
                        **({"detail": detail} if detail else {}),
                    },
                    owner=self.owner,
                    side_effect_state=SideEffectState.CONFIRMED.value,
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
        reconciliation_limit_reached = code == RECONCILIATION_EVENT_LIMIT_ERROR
        error = {
            "code": code,
            "retryable": not reconciliation_limit_reached,
        }
        if reconciliation_limit_reached:
            error["reason"] = (
                "Controlled reconciliation evidence reached its bounded event limit; "
                "a manual readback is required before another retry."
            )
        self.store.defer_unknown_agent_run_reconciliation(
            run.id,
            error,
            owner=self.owner,
            expected_execution_generation=run.execution_generation,
            next_attempt_at=unknown_reconciliation_retry_at(
                persisted.reconciliation_attempts
            ),
            suspended=reconciliation_limit_reached,
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


def _is_dingtalk_chat_send(metadata: dict[str, object]) -> bool:
    target = metadata.get("target_identifiers")
    return (
        metadata.get("effect") == EffectKind.EFFECTFUL.value
        and metadata.get("capability") == "agent_cli.dws"
        and isinstance(target, dict)
        and any(
            isinstance(target.get(key), str) and target[key]
            for key in ("group", "user", "open-dingtalk-id", "conversation-id", "conversation")
        )
    )


def _is_dingtalk_chat_send_argv(
    metadata: dict[str, object],
    argv: tuple[str, ...] | None,
) -> bool:
    operation = metadata.get("operation")
    return (
        _is_dingtalk_chat_send(metadata)
        and argv is not None
        and len(argv) >= 3
        and argv[0] == "dws"
        and isinstance(operation, str)
        and operation.startswith("chat ")
        and bool(_command_option_value(argv, "--text"))
    )


def _is_expected_dingtalk_chat_send(action: dict[str, object]) -> bool:
    target = action.get("target_identifiers")
    return (
        action.get("capability") == "agent_cli.dws"
        and isinstance(target, dict)
        and any(
            isinstance(target.get(key), str) and target[key]
            for key in ("group", "user", "open-dingtalk-id", "conversation-id", "conversation")
        )
    )


def _command_option_value(
    argv: tuple[str, ...] | None,
    option: str,
) -> str:
    if argv is None:
        return ""
    try:
        index = argv.index(option)
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    value = argv[index + 1]
    return value if value and not value.startswith("--") else ""


def _closed_effect_failure(
    run: AgentRun,
    *,
    fallback_code: str,
) -> dict[str, object] | None:
    if (
        run.effect_started_count <= 0
        or run.effect_failed_count <= 0
        or run.effect_unreviewed_count
        or run.effect_started_count
        > run.effect_completed_count
        + run.effect_failed_count
        + run.effect_receipt_count
    ):
        return None
    for event in reversed(run.tool_events):
        if event.get("type") != "item.failed":
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict) or metadata.get("effect") != "effectful":
            continue
        failure_code = metadata.get("failure_code")
        return {
            "code": (
                failure_code
                if isinstance(failure_code, str) and failure_code
                else fallback_code
            ),
            "retryable": bool(metadata.get("failure_retryable", False)),
            "authorization_required": False,
        }
    return None


def _session_id(payload: dict[str, object]) -> str:
    if payload.get("type") not in {"thread.started", "thread_started"}:
        return ""
    for key in ("thread_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _agent_cli_receipt(
    value: object, *, allow_error: bool = False
) -> dict[str, object] | None:
    if isinstance(value, str):
        try:
            encoded_size = len(value.encode("utf-8"))
        except (UnicodeError, MemoryError):
            return None
        if encoded_size > 64 * 1024:
            return None
        encoded = value.strip()
        if not encoded.startswith(("{", "[")):
            marker = "\nOutput:\n"
            if marker not in encoded:
                return None
            _timing, encoded = encoded.rsplit(marker, 1)
            encoded = encoded.strip()
        try:
            decoded = json.loads(encoded)
        except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
            return None
        return _agent_cli_receipt(decoded, allow_error=allow_error)
    if isinstance(value, list):
        for block in value:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            receipt = _agent_cli_receipt(block.get("text"), allow_error=allow_error)
            if receipt is not None:
                return receipt
        return None
    receipt = _controlled_cli_receipt(value)
    if receipt is not None or not isinstance(value, dict):
        return receipt
    candidates: list[object] = [value.get("structuredContent"), value.get("structured_content")]
    content = value.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str) or len(text.encode("utf-8")) > 64 * 1024:
                continue
            try:
                candidates.append(json.loads(text))
            except json.JSONDecodeError:
                continue
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        receipt = _controlled_cli_receipt(candidate)
        if receipt is not None and (
            allow_error or not isinstance(candidate.get("error"), dict)
        ):
            return receipt
        if allow_error and all(
            isinstance(candidate.get(key), expected)
            for key, expected in (
                ("result_digest", str),
                ("operation_digest", str),
                ("operation", str),
                ("target_identifiers", dict),
                ("error", dict),
            )
        ):
            return candidate
    return None


def _agent_cli_tool_error(value: object) -> str:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 64 * 1024:
            return ""
        text = value.strip()
        marker = "\nOutput:\n"
        if not text.startswith(("{", "[")) and marker in text:
            _timing, text = text.rsplit(marker, 1)
            text = text.strip()
        if text.startswith(("{", "[")):
            try:
                return _agent_cli_tool_error(json.loads(text))
            except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
                return ""
        prefix = "Error executing tool "
        if not text.startswith(prefix) or ": " not in text:
            return ""
        code = text.rsplit(": ", 1)[-1].strip()
        return code if code.replace("_", "").isalnum() else ""
    if isinstance(value, list):
        for block in value:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            code = _agent_cli_tool_error(block.get("text"))
            if code:
                return code
    if isinstance(value, dict):
        for key in ("structuredContent", "structured_content"):
            code = _agent_cli_tool_error(value.get(key))
            if code:
                return code
        content = value.get("content")
        if isinstance(content, list):
            return _agent_cli_tool_error(content)
    return ""


def _failed_agent_cli_read_event(
    item: dict[str, object], failure_code: str
) -> dict[str, object]:
    arguments = item.get("arguments")
    argv = arguments.get("argv") if isinstance(arguments, dict) else None
    return {
        "type": "item.failed",
        "item": {
            "type": "mcp_tool_call",
            "id": str(item.get("id") or item.get("call_id") or ""),
            "server": "agent_cli",
            "tool": "execute_reviewed_read",
            "status": "failed",
            "metadata": {
                "effect": EffectKind.READ_ONLY.value,
                "capability": "agent_cli",
                "operation": "",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation_digest": "",
                "target_identifiers": {},
                "arguments_digest": _json_digest({"argv": argv}),
                "failure_code": failure_code,
            },
        },
    }


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
    if action.get("operation_contract_valid") is False:
        return False
    identity_matches = all(
        metadata.get(key) == action.get(key)
        for key in (
            "capability",
            "arguments_digest",
            "target_identifiers",
        )
    )
    expected_command_digest = action.get("operation_digest")
    if expected_command_digest is not None:
        return (
            identity_matches
            and metadata.get("operation_digest") == expected_command_digest
        )
    return identity_matches and metadata.get("operation") == action.get("operation")


def _recovery_execution_result_from_receipts(
    *,
    run: AgentRun,
    recovery_phase: str,
    persisted: AgentRun | None,
    expected_effect_actions: tuple[dict[str, object], ...],
    recovery_started_actions: set[int],
    authorized_recovery_actions: frozenset[int],
    registry: McpToolEffectRegistry,
    store: AutoReplyStore,
) -> AuditAgentResult | None:
    """Finish a recovery only when the controlled write receipts are complete."""
    if (
        recovery_phase != "execute"
        or run.role is not AgentRole.AUDIT
        or persisted is None
        or not authorized_recovery_actions
        or recovery_started_actions != set(authorized_recovery_actions)
    ):
        return None
    completed, all_effects_closed = _action_completion_accounting(
        persisted.tool_events,
        store.list_agent_execution_receipts(run.id),
        expected_effect_actions,
        operation_id=run.operation_id,
        registry=registry,
    )
    if completed != set(authorized_recovery_actions) or not all_effects_closed:
        return None
    return AuditAgentResult(
        outcome=AuditOutcome.EXECUTED,
        summary="Authorized recovery actions completed with controlled receipts.",
        proposal_revision=run.proposal_revision,
        side_effect_state=SideEffectState.CONFIRMED,
        feedback=None,
        external_result=AuditExternalResult(
            operation_id=run.operation_id,
            verification_summary="Controlled recovery receipts completed.",
            live_result_reference={
                "recovery_action_indexes": sorted(authorized_recovery_actions),
                "evidence": "controlled_receipts",
            },
        ),
        reconciliation=(),
        error=AgentError(),
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
    if not registry.readback_operations_match(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
        read_operation=str(read.get("operation") or ""),
        write_operation=str(action.get("operation") or ""),
    ):
        return False
    read_target = read.get("target_identifiers")
    action_target = action.get("readback_target_identifiers")
    if not isinstance(action_target, dict):
        action_target = action.get("target_identifiers")
    if not isinstance(read_target, dict) or not isinstance(action_target, dict):
        return False
    if not registry.readback_targets_match(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
        read_targets=read_target,
        write_targets=action_target,
    ):
        return False
    read_result_identifiers = read.get("result_identifiers")
    write_result_identifiers = action.get("result_identifiers")
    return registry.readback_identities_match(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
        read_operation=str(read.get("operation") or ""),
        write_operation=str(action.get("operation") or ""),
        read_targets=read_target,
        read_result_identifiers=(
            read_result_identifiers
            if isinstance(read_result_identifiers, dict)
            else {}
        ),
        write_result_identifiers=(
            write_result_identifiers
            if isinstance(write_result_identifiers, dict)
            else {}
        ),
    )


def _action_has_readback(
    action: dict[str, object],
    registry: McpToolEffectRegistry,
) -> bool:
    return registry.has_readback_for(
        write_server=str(action.get("reviewed_server") or ""),
        write_tool=str(action.get("reviewed_tool") or ""),
        write_operation=str(action.get("operation") or ""),
    )


def _matching_read_digest(
    events: list[dict[str, object]],
    action: dict[str, object],
    *,
    event_start: int = 0,
    after_index: int = -1,
    registry: McpToolEffectRegistry,
) -> str:
    readback_action = _readback_action_metadata(action, action)
    for index, event in enumerate(events):
        if index < event_start or index <= after_index or event.get("type") != "item.completed":
            continue
        metadata = _event_metadata(event)
        if metadata is None or metadata.get("effect") != EffectKind.READ_ONLY.value:
            continue
        digest = metadata.get("result_digest")
        if _read_matches_action(metadata, readback_action, registry) and isinstance(digest, str):
            return digest
    return ""


def _actions_have_required_readbacks(
    events: list[dict[str, object]],
    actions: tuple[dict[str, object], ...],
    registry: McpToolEffectRegistry,
) -> bool:
    for action_index, action in enumerate(actions):
        if not _action_has_readback(action, registry):
            continue
        writes = [
            (index, metadata)
            for index, event in enumerate(events)
            if event.get("type") == "item.completed"
            and (metadata := _event_metadata(event)) is not None
            and metadata.get("effect") == EffectKind.EFFECTFUL.value
            and metadata.get("action_index") in {None, action_index}
            and _metadata_matches_action(metadata, action)
        ]
        if not writes:
            writes = [(-1, action)]
        if any(
            not _matching_read_digest(
                events,
                _readback_action_metadata(write_metadata, action),
                after_index=write_index,
                registry=registry,
            )
            for write_index, write_metadata in writes
        ):
            return False
    return True


def _readback_action_metadata(
    write_metadata: dict[str, object],
    expected_action: dict[str, object],
) -> dict[str, object]:
    """Keep the actual write receipt, with a reviewed recipient for name-resolved DMs."""
    targets = expected_action.get("readback_target_identifiers")
    if not isinstance(targets, dict):
        return write_metadata
    return {**write_metadata, "target_identifiers": targets}


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
        matching_digests: list[str] = []
        for index, event in enumerate(events):
            if index < event_start or (
                event.get("type") != "item.completed"
                and not (
                    event.get("type") == "item.failed"
                    and entry.disposition is ReconciliationDisposition.AMBIGUOUS
                )
            ):
                continue
            metadata = _event_metadata(event)
            if (
                metadata is None
                or metadata.get("effect") != EffectKind.READ_ONLY.value
                or not isinstance(metadata.get("result_digest"), str)
            ):
                continue
            write_metadata = _latest_matching_write_metadata(
                events,
                actions[action_index],
                before_index=index,
            )
            if _read_matches_action(
                metadata,
                write_metadata or actions[action_index],
                registry,
            ):
                matching_digests.append(str(metadata["result_digest"]))
        if not matching_digests:
            raise RuntimeError("audit_reconciliation_evidence_mismatch")
        if entry.read_result_digest not in matching_digests:
            raise RuntimeError("audit_reconciliation_evidence_mismatch")
        validated[action_index] = entry
    return validated


def _latest_matching_write_metadata(
    events: list[dict[str, object]],
    action: dict[str, object],
    *,
    before_index: int,
) -> dict[str, object] | None:
    for index in range(before_index - 1, -1, -1):
        event = events[index]
        if event.get("type") != "item.completed":
            continue
        metadata = _event_metadata(event)
        if (
            metadata is not None
            and metadata.get("effect") == EffectKind.EFFECTFUL.value
            and _metadata_matches_action(metadata, action)
        ):
            return metadata
    return None


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

    # An interrupted write may be followed by a later, independently recorded
    # successful write and its target-matched readback. The readback closes the
    # interrupted lifecycle as well; otherwise recovery keeps retrying an
    # already verified effect forever.
    for start in starts:
        action_index = start["action_index"]
        if (
            not start["closed"]
            and not any(
                other is not start
                and other["call_id"] == start["call_id"]
                for other in starts
            )
            and action_index is not None
            and _matching_read_digest(
                events,
                actions[int(action_index)],
                after_index=int(start["event_index"]),
                registry=registry,
            )
        ):
            start["closed"] = True

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

    # Session replay is suppressed before persistence. Multiple completed calls
    # for one proposal action in this accounting pass are therefore distinct
    # external writes and must keep Audit from reporting a single safe effect.
    completed = {
        index for index, success_count in enumerate(successes) if success_count == 1
    }
    return completed, all(bool(start["closed"]) for start in starts) and all(
        success_count <= 1 for success_count in successes
    )


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _requested_skill_path(arguments: object) -> str:
    if not isinstance(arguments, dict) or set(arguments) != {"path"}:
        return ""
    path = arguments.get("path")
    if not isinstance(path, str) or not path or not Path(path).is_absolute():
        return ""
    try:
        return str(Path(path).resolve(strict=False))
    except (OSError, RuntimeError):
        return ""


def _attempted_skill_paths(
    events: tuple[dict[str, object], ...] | list[dict[str, object]],
) -> frozenset[str]:
    paths: set[str] = set()
    for event in events:
        if event.get("type") != "item.failed":
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            continue
        if (
            metadata.get("reviewed_server") != "agent_cli"
            or metadata.get("reviewed_tool") != "read_skill"
        ):
            continue
        path = metadata.get("requested_skill_path")
        if isinstance(path, str) and path:
            paths.add(path)
    return frozenset(paths)


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
