import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from app.agent_context import AgentTaskContext
from app.agent_result import (
    AgentResult,
    EffectEventStatus,
    EffectKind,
    ExecutionReceipt,
    ResultParseError,
    SideEffectState,
    ToolEffectEvent,
    parse_agent_result,
    validate_completion_evidence,
)
from app.codex_runner import CodexRunner, codex_developer_instructions
from app.dws_client import DwsClient
from app.history import safe_observability_error
from app.leak_check import contains_credential
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import AgentRunLeaseLostError, AutoReplyStore, ReplyTask


AGENT_RESULT_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "agent_result.schema.json"
)
TOTAL_TIMEOUT_SECONDS = 1200
IDLE_TIMEOUT_SECONDS = 900
LEASE_SECONDS = TOTAL_TIMEOUT_SECONDS + 300


class AgentRunUnavailableError(RuntimeError):
    pass


class AgentStreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class DirectAgentRunResult:
    run_id: int
    result: AgentResult
    transcript_start_line: int
    transcript_end_line: int
    events: tuple[dict[str, object], ...]


ProcessExecutor = Callable[..., ProcessRunResult]


class DirectAgentRunner:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        codex_bin: str = "codex",
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
    ) -> None:
        self.store = store
        self.codex = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor or run_process_with_idle_timeout
        self.owner = owner or f"direct-agent-{uuid4().hex}"

    def run(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        read_only: bool = False,
        now: str | None = None,
    ) -> DirectAgentRunResult:
        if context.task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
            now=now,
        )
        if not claim.claimed:
            raise AgentRunUnavailableError(
                f"agent run is not available for task generation: {task.id}"
            )
        run = claim.run
        prompt = context.render()
        developer_instructions = None
        approval_policy = "untrusted"
        if read_only:
            approval_policy = "never"
            prompt = (
                "Read-only invocation. Do not perform any external write, send, "
                "approval, comment, reaction, document edit, or state-changing "
                "command. Query live state only.\n\n"
                + prompt
            )
            developer_instructions = (
                codex_developer_instructions()
                + "\n\nThis invocation is read-only. Do not perform any external write."
            )
        command = self.codex.build_command(
            prompt=prompt,
            session_id=run.codex_session_id or None,
            output_schema_path=AGENT_RESULT_SCHEMA_PATH,
            approval_policy=approval_policy,
            developer_instructions=developer_instructions,
            use_approval_bypass=False,
            preserve_native_model_config=True,
        )
        events: list[dict[str, object]] = []
        effect_events: list[ToolEffectEvent] = []
        receipts: list[ExecutionReceipt] = []
        saw_json = False

        def persist_line(line: str) -> None:
            nonlocal saw_json
            if not line.strip():
                return
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                if saw_json:
                    raise AgentStreamError("codex_stream_invalid") from exc
                return
            saw_json = True
            if not isinstance(payload, dict):
                raise AgentStreamError("codex_stream_invalid")
            session_id = _session_id(payload)
            if session_id:
                self.store.set_agent_run_session(
                    run.id,
                    session_id,
                    owner=self.owner,
                    transcript_start_line=run.transcript_start_line,
                    now=now,
                )
            safe_event = _safe_event(payload)
            self.store.append_agent_run_event(
                run.id,
                safe_event,
                owner=self.owner,
                now=now,
            )
            events.append(safe_event)
            effect_event = _effect_event(payload)
            if effect_event is not None:
                effect_events.append(effect_event)
            receipt = _receipt(payload)
            if receipt is not None:
                receipts.append(receipt)

        try:
            process = self.executor(
                command,
                prompt=prompt,
                env=self.codex.build_env(preserve_local_cli_auth=True),
                total_timeout_seconds=TOTAL_TIMEOUT_SECONDS,
                idle_timeout_seconds=IDLE_TIMEOUT_SECONDS,
                on_stdout_line=persist_line,
            )
        except AgentRunLeaseLostError:
            raise
        except AgentStreamError as exc:
            self._record_failure(
                run.id,
                "codex_stream_invalid",
                events=effect_events,
                receipts=receipts,
                transcript_end_line=len(events),
                now=now,
            )
            raise RuntimeError("codex_stream_invalid") from exc
        except Exception as exc:
            self._record_failure(
                run.id,
                "codex_process_failed",
                events=effect_events,
                receipts=receipts,
                transcript_end_line=len(events),
                now=now,
            )
            raise RuntimeError("codex_process_failed") from exc

        if process.timed_out:
            self._record_failure(
                run.id,
                "codex_process_timeout",
                events=effect_events,
                receipts=receipts,
                transcript_end_line=len(events),
                now=now,
            )
            raise RuntimeError("codex_process_timeout")
        if process.returncode != 0:
            self._record_failure(
                run.id,
                "codex_process_failed",
                events=effect_events,
                receipts=receipts,
                transcript_end_line=len(events),
                now=now,
            )
            raise RuntimeError("codex_process_failed")
        try:
            result = parse_agent_result(process.stdout)
            evidence_state = validate_completion_evidence(
                result,
                events=effect_events,
                receipts=receipts,
            )
        except (ResultParseError, ValueError) as exc:
            self._record_failure(
                run.id,
                "codex_result_invalid",
                events=effect_events,
                receipts=receipts,
                transcript_end_line=len(events),
                now=now,
            )
            raise RuntimeError("codex_result_invalid") from exc

        if evidence_state is SideEffectState.UNKNOWN:
            self.store.mark_agent_run_unknown(
                run.id,
                {"code": "agent_side_effect_unknown"},
                owner=self.owner,
                transcript_end_line=len(events),
                now=now,
            )
        else:
            self.store.complete_agent_run(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=evidence_state.value,
                transcript_end_line=len(events),
                now=now,
            )
        return DirectAgentRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=len(events),
            events=tuple(events),
        )

    def _record_failure(
        self,
        run_id: int,
        code: str,
        *,
        events: list[ToolEffectEvent],
        receipts: list[ExecutionReceipt],
        transcript_end_line: int,
        now: str | None,
    ) -> None:
        evidence_state = _evidence_state(events, receipts)
        if evidence_state is SideEffectState.UNKNOWN:
            self.store.mark_agent_run_unknown(
                run_id,
                {"code": code},
                owner=self.owner,
                transcript_end_line=transcript_end_line,
                now=now,
            )
            return
        self.store.fail_agent_run(
            run_id,
            {"code": code},
            owner=self.owner,
            transcript_end_line=transcript_end_line,
            side_effect_state=evidence_state.value,
            now=now,
        )


def _session_id(payload: dict[str, object]) -> str:
    if payload.get("type") not in {"thread.started", "thread_started"}:
        return ""
    for key in ("thread_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _safe_event(payload: dict[str, object]) -> dict[str, object]:
    def sanitize(value: object, *, key: str = "") -> object:
        if key in {"thread_id", "session_id"}:
            return "[stored separately]"
        if isinstance(value, str):
            return (
                _redact_command(value)
                if key in {"command", "cmd"}
                else safe_observability_error(value, limit=2000)
            )
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {str(item_key): sanitize(item, key=str(item_key)) for item_key, item in value.items()}
        if value is None or isinstance(value, bool | int | float):
            return value
        return safe_observability_error(str(value), limit=2000)

    return {str(key): sanitize(value, key=str(key)) for key, value in payload.items()}


def _redact_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return safe_observability_error(command, limit=2000)
    sanitized: list[str] = []
    redact_next = False
    for part in parts:
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next = False
            continue
        flag, separator, _value = part.partition("=")
        if (
            flag in DwsClient.SENSITIVE_COMMAND_FLAGS
            or contains_credential(f"{flag}=placeholder")
        ):
            sanitized.append(
                f"{flag}=[REDACTED]" if separator else flag
            )
            redact_next = not separator
        else:
            sanitized.append(part)
    return safe_observability_error(shlex.join(sanitized), limit=2000)


def _effect_event(payload: dict[str, object]) -> ToolEffectEvent | None:
    event_type = str(payload.get("type") or "")
    if event_type not in {"item.started", "item.completed"}:
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    effect = item.get("effect")
    metadata = item.get("metadata")
    if effect is None and isinstance(metadata, dict):
        effect = metadata.get("effect")
    if effect not in {"read_only", "effectful"}:
        return None
    call_id = item.get("call_id") or item.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    return ToolEffectEvent(
        call_id=call_id,
        effect=EffectKind(effect),
        status=(
            EffectEventStatus.STARTED
            if event_type == "item.started"
            else EffectEventStatus.COMPLETED
        ),
    )


def _receipt(payload: dict[str, object]) -> ExecutionReceipt | None:
    candidate = payload.get("receipt")
    if not isinstance(candidate, dict):
        return None
    try:
        return ExecutionReceipt.model_validate(candidate)
    except ValueError:
        return None


def _evidence_state(
    events: list[ToolEffectEvent],
    receipts: list[ExecutionReceipt],
) -> SideEffectState:
    started = {
        event.call_id
        for event in events
        if event.effect is EffectKind.EFFECTFUL
        and event.status is EffectEventStatus.STARTED
    }
    completed = {
        event.call_id
        for event in events
        if event.effect is EffectKind.EFFECTFUL
        and event.status is EffectEventStatus.COMPLETED
    }
    completed.update(
        receipt.operation_id
        for receipt in receipts
        if receipt.completed and receipt.persisted and receipt.safe_to_confirm
    )
    if started - completed:
        return SideEffectState.UNKNOWN
    if completed:
        return SideEffectState.CONFIRMED
    return SideEffectState.NONE
