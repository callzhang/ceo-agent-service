import json
import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from app.agent_context import AgentTaskContext
from app.agent_result import (
    AgentOutcome,
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
from app.codex_runner import CodexRunner
from app.dws_client import DwsClient
from app.history import safe_observability_error
from app.leak_check import contains_credential
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import AgentRunLeaseLostError, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import make_read_only_without_tools


AGENT_RESULT_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "agent_result.schema.json"
)
TOTAL_TIMEOUT_SECONDS = 1200
IDLE_TIMEOUT_SECONDS = 900
LEASE_SECONDS = TOTAL_TIMEOUT_SECONDS + 300
DIRECT_AGENT_DEVELOPER_INSTRUCTIONS = """You are the Direct Agent for one queued task.

- The Agent owns evidence reads, business judgment, direct execution and verification.
- Use raw identifiers, references, exact read commands, and live tool results. Do not rely on service-side target assumptions.
- Complete authorized work directly with available CLI and MCP tools. Do not produce plans, action arrays, or requests for service execution.
- Return only one JSON object matching the AgentResult schema supplied to Codex.
- Never run authentication login, reset, or logout commands. Authentication readiness belongs to the service gate.
- Never expose credentials, tokens, cookies, authorization codes, signed URLs, or local credential paths.
- Do not infer successful execution from prose. Report completion only when direct execution and verification produced structured evidence."""
READ_ONLY_DEVELOPER_INSTRUCTION = (
    "This invocation is read-only. Do not perform any external write, send, "
    "approval, comment, reaction, edit, or other state-changing action."
)
_NATIVE_READ_ONLY_ITEM_TYPES = frozenset(
    {"tool_search", "tool_search_call", "web_search", "web_search_call"}
)
_NATIVE_CLASSIFIABLE_ITEM_TYPES = frozenset(
    {
        "command_execution",
        "dynamic_tool_call",
        "function_call",
        "mcp_tool_call",
        "tool_call",
    }
)
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "authorization",
        "bearer",
        "cookie",
        "password",
        "secret",
        "signature",
        "signedurl",
        "token",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "apikey",
        "clientsecret",
        "privatekey",
        "webhook",
    }
)
_SESSION_KEY_NAMES = frozenset({"sessionid", "threadid"})
_COMMAND_KEY_NAMES = frozenset({"argv", "cmd", "command"})
_STRUCTURED_TEXT_KEY_NAMES = frozenset({"arguments", "output", "result"})
_RECEIPT_KEYS = frozenset(ExecutionReceipt.model_fields)
_REDACTED = "[REDACTED]"


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
    receipts: tuple[ExecutionReceipt, ...] = ()


@dataclass(frozen=True)
class NativeCliCommand:
    cli: str
    command_path: str
    argv: tuple[str, ...]
    effect: EffectKind
    command_digest: str


class NativeCliMetadataClassifier:
    """Classify native CLI commands from their installed reviewed metadata."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, ...], NativeCliCommand | None] = {}

    def classify(self, item: dict[str, object]) -> NativeCliCommand | None:
        argv = _native_command_argv(item)
        if argv is None or "--dry-run" in argv:
            return None
        cached = self._cache.get(argv)
        if cached is not None or argv in self._cache:
            return cached
        cli = Path(argv[0]).name
        if cli == "dws":
            classified = self._classify_dws(argv)
        elif cli == "lark-cli":
            classified = self._classify_lark(argv)
        else:
            classified = None
        self._cache[argv] = classified
        return classified

    def _classify_dws(self, argv: tuple[str, ...]) -> NativeCliCommand | None:
        for command_path in _command_path_candidates(argv[1:]):
            try:
                process = subprocess.run(
                    [
                        argv[0],
                        "schema",
                        "--cli-path",
                        command_path,
                        "--compact",
                        "--format",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if process.returncode != 0:
                continue
            try:
                metadata = json.loads(process.stdout)
            except json.JSONDecodeError:
                continue
            effect = metadata.get("effect") if isinstance(metadata, dict) else None
            if effect not in {"read", "write"}:
                continue
            return _classified_native_command(
                "dws",
                command_path,
                argv,
                EffectKind.READ_ONLY if effect == "read" else EffectKind.EFFECTFUL,
            )
        return None

    def _classify_lark(self, argv: tuple[str, ...]) -> NativeCliCommand | None:
        for command_path in _command_path_candidates(argv[1:]):
            try:
                process = subprocess.run(
                    [argv[0], *command_path.split(), "--help"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if process.returncode != 0:
                continue
            risk = ""
            for line in (process.stdout + "\n" + process.stderr).splitlines():
                if line.strip().casefold().startswith("risk:"):
                    risk = line.split(":", 1)[1].strip().casefold()
                    break
            if risk not in {"read", "write", "high-risk-write"}:
                continue
            return _classified_native_command(
                "lark-cli",
                command_path,
                argv,
                (
                    EffectKind.READ_ONLY
                    if risk == "read"
                    else EffectKind.EFFECTFUL
                ),
            )
        return None


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
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
    ) -> None:
        self.store = store
        self.codex = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor or run_process_with_idle_timeout
        self.owner = owner or f"direct-agent-{uuid4().hex}"
        self.native_cli_classifier = (
            native_cli_classifier or NativeCliMetadataClassifier()
        )

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
        developer_instructions = DIRECT_AGENT_DEVELOPER_INSTRUCTIONS
        approval_policy = "untrusted"
        if read_only:
            approval_policy = "never"
            prompt = (
                "Read-only invocation. Do not perform any external write, send, "
                "approval, comment, reaction, document edit, or state-changing "
                "command. Query live state only.\n\n"
                + prompt
            )
            developer_instructions += "\n\n" + READ_ONLY_DEVELOPER_INSTRUCTION
        command = self.codex.build_command(
            prompt=prompt,
            session_id=run.codex_session_id or None,
            output_schema_path=AGENT_RESULT_SCHEMA_PATH,
            approval_policy=approval_policy,
            developer_instructions=developer_instructions,
            use_approval_bypass=False,
            preserve_native_model_config=True,
        )
        if read_only:
            make_read_only_without_tools(command)
        events: list[dict[str, object]] = []
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
            native_command = _native_cli_command(
                payload,
                self.native_cli_classifier,
            )
            safe_event = _safe_event(payload, native_command=native_command)
            self.store.append_agent_run_event(
                run.id,
                safe_event,
                owner=self.owner,
                now=now,
            )
            events.append(safe_event)
            if (
                native_command is not None
                and native_command.effect is EffectKind.EFFECTFUL
                and _native_command_completed(payload)
            ):
                call_id = _native_call_id(payload)
                if call_id:
                    self.store.record_agent_execution_receipt(
                        run.id,
                        receipt_id=f"native-cli:{run.id}:{call_id}",
                        operation_id=call_id,
                        cli=native_command.cli,
                        command_path=native_command.command_path,
                        command_digest=native_command.command_digest,
                        exit_code=0,
                        now=now,
                    )

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
                now=now,
            )
            raise RuntimeError("codex_stream_invalid") from exc
        except Exception as exc:
            self._record_failure(
                run.id,
                "codex_process_failed",
                now=now,
            )
            raise RuntimeError("codex_process_failed") from exc

        if process.timed_out:
            self._record_failure(
                run.id,
                "codex_process_timeout",
                now=now,
            )
            raise RuntimeError("codex_process_timeout")
        if process.returncode != 0:
            self._record_failure(
                run.id,
                "codex_process_failed",
                now=now,
            )
            raise RuntimeError("codex_process_failed")
        persisted = self.store.get_agent_run(run.id)
        if persisted is None:
            raise RuntimeError("agent run was not persisted")
        all_effect_events, embedded_receipts = structured_execution_evidence(
            persisted.tool_events
        )
        persisted_receipts = _execution_receipts_for_run(self.store, run.id)
        all_receipts = (*embedded_receipts, *persisted_receipts)
        try:
            result = parse_agent_result(process.stdout)
            evidence_state = validate_completion_evidence(
                result,
                events=all_effect_events,
                receipts=all_receipts,
            )
        except (ResultParseError, ValueError) as exc:
            self._record_failure(
                run.id,
                "codex_result_invalid",
                now=now,
            )
            raise RuntimeError("codex_result_invalid") from exc

        if evidence_state is SideEffectState.UNKNOWN:
            self.store.mark_agent_run_unknown(
                run.id,
                {"code": "agent_side_effect_unknown"},
                owner=self.owner,
                transcript_end_line=persisted.transcript_end_line,
                now=now,
            )
        elif result.outcome is AgentOutcome.FAILED:
            self.store.fail_agent_run(
                run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=evidence_state.value,
                transcript_end_line=persisted.transcript_end_line,
                now=now,
            )
        else:
            self.store.complete_agent_run(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=evidence_state.value,
                transcript_end_line=persisted.transcript_end_line,
                now=now,
            )
        completed_run = self.store.get_agent_run(run.id)
        if completed_run is None:
            raise RuntimeError("agent run was not persisted")
        return DirectAgentRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=completed_run.transcript_end_line,
            events=tuple(completed_run.tool_events),
            receipts=tuple(all_receipts),
        )

    def _record_failure(
        self,
        run_id: int,
        code: str,
        *,
        now: str | None,
    ) -> None:
        persisted = self.store.get_agent_run(run_id)
        if persisted is None:
            raise RuntimeError("agent run was not persisted")
        events, embedded_receipts = structured_execution_evidence(
            persisted.tool_events
        )
        receipts = (
            *embedded_receipts,
            *_execution_receipts_for_run(self.store, run_id),
        )
        evidence_state = _evidence_state(list(events), list(receipts))
        if evidence_state is SideEffectState.UNKNOWN:
            self.store.mark_agent_run_unknown(
                run_id,
                {"code": code},
                owner=self.owner,
                transcript_end_line=persisted.transcript_end_line,
                now=now,
            )
            return
        self.store.fail_agent_run(
            run_id,
            {"code": code},
            owner=self.owner,
            transcript_end_line=persisted.transcript_end_line,
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


def _native_command_argv(item: dict[str, object]) -> tuple[str, ...] | None:
    raw_command = item.get("argv") or item.get("command")
    if isinstance(raw_command, list) and all(
        isinstance(part, str) for part in raw_command
    ):
        argv = tuple(raw_command)
    elif isinstance(raw_command, str):
        try:
            lexer = shlex.shlex(
                raw_command,
                posix=True,
                punctuation_chars="|&;<>\n",
            )
            lexer.whitespace = " \t\r"
            lexer.whitespace_split = True
            lexer.commenters = ""
            argv = tuple(lexer)
        except ValueError:
            return None
        shell_punctuation = frozenset("|&;<>\n")
        if any(
            token and all(character in shell_punctuation for character in token)
            for token in argv
        ):
            return None
    else:
        return None
    if not argv:
        return None
    executable = Path(argv[0]).name
    if executable in {"bash", "sh", "zsh"}:
        for flag in ("-lc", "-c"):
            if flag in argv:
                index = argv.index(flag)
                if index + 1 < len(argv):
                    return _native_command_argv({"command": argv[index + 1]})
        return None
    if executable not in {"dws", "lark-cli"}:
        return None
    return argv


def _command_path_candidates(argv: tuple[str, ...]) -> tuple[str, ...]:
    command_tokens: list[str] = []
    for token in argv:
        if token.startswith("-"):
            break
        command_tokens.append(token)
    return tuple(
        " ".join(command_tokens[:length])
        for length in range(len(command_tokens), 0, -1)
    )


def _classified_native_command(
    cli: str,
    command_path: str,
    argv: tuple[str, ...],
    effect: EffectKind,
) -> NativeCliCommand:
    normalized = shlex.join(argv)
    return NativeCliCommand(
        cli=cli,
        command_path=command_path,
        argv=argv,
        effect=effect,
        command_digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def _native_cli_command(
    payload: dict[str, object],
    classifier: NativeCliMetadataClassifier,
) -> NativeCliCommand | None:
    if payload.get("type") not in {
        "item.started",
        "item.completed",
        "item.failed",
    }:
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "command_execution":
        return None
    return classifier.classify(item)


def _native_call_id(payload: dict[str, object]) -> str:
    item = payload.get("item")
    if not isinstance(item, dict):
        return ""
    call_id = item.get("call_id") or item.get("id")
    return call_id.strip() if isinstance(call_id, str) else ""


def _native_command_completed(payload: dict[str, object]) -> bool:
    if payload.get("type") != "item.completed":
        return False
    item = payload.get("item")
    if not isinstance(item, dict):
        return False
    return item.get("exit_code") == 0 and item.get("status") == "completed"


def _execution_receipts_for_run(
    store: AutoReplyStore,
    run_id: int,
) -> tuple[ExecutionReceipt, ...]:
    return tuple(
        ExecutionReceipt(
            receipt_id=receipt.receipt_id,
            operation_id=receipt.operation_id,
            completed=receipt.completed,
            persisted=receipt.persisted,
            safe_to_confirm=receipt.safe_to_confirm,
        )
        for receipt in store.list_agent_execution_receipts(run_id)
    )


def _safe_event(
    payload: dict[str, object],
    *,
    native_command: NativeCliCommand | None = None,
) -> dict[str, object]:
    safe_event = {
        str(key): _sanitize_event_value(value, key=str(key))
        for key, value in payload.items()
    }
    item = safe_event.get("item")
    if native_command is not None and isinstance(item, dict):
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            item["metadata"] = metadata
        metadata.update(
            {
                "effect": native_command.effect.value,
                "native_cli": native_command.cli,
                "operation": native_command.command_path,
                "command_digest": native_command.command_digest,
            }
        )
        if (
            safe_event.get("type") == "item.completed"
            and native_command.effect is EffectKind.EFFECTFUL
            and not _native_command_completed(payload)
        ):
            safe_event["type"] = "item.failed"
    return safe_event


def _sanitize_event_value(value: object, *, key: str = "") -> object:
    normalized_key = _normalized_key(key)
    if normalized_key in _SESSION_KEY_NAMES:
        return "[stored separately]"
    if _is_sensitive_key(normalized_key):
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_event_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        if normalized_key in _COMMAND_KEY_NAMES and all(
            isinstance(item, str) for item in value
        ):
            return _redact_argv(value)
        return [_sanitize_event_value(item) for item in value]
    if isinstance(value, str):
        if normalized_key in _COMMAND_KEY_NAMES:
            return _redact_command(value)
        if normalized_key in _STRUCTURED_TEXT_KEY_NAMES:
            structured = _sanitize_json_text(value)
            if structured is not None:
                return structured
        if _is_signed_url(value):
            return _REDACTED
        return safe_observability_error(value, limit=2000)
    if value is None or isinstance(value, bool | int | float):
        return value
    return safe_observability_error(str(value), limit=2000)


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _is_sensitive_key(normalized_key: str) -> bool:
    if normalized_key in _SENSITIVE_KEY_NAMES:
        return True
    if normalized_key.startswith("x") and normalized_key[1:] in _SENSITIVE_KEY_NAMES:
        return True
    return (
        normalized_key.endswith("token")
        or normalized_key.endswith("password")
        or normalized_key.endswith("secret")
        or normalized_key.endswith("cookie")
        or normalized_key.endswith("authorization")
        or normalized_key.endswith("apikey")
        or normalized_key.endswith("signedurl")
        or normalized_key.endswith("signature")
    )


def _sanitize_json_text(value: str) -> str | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict | list):
        return None
    sanitized = _sanitize_event_value(parsed)
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))


def _redact_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return _REDACTED
    return " ".join(shlex.join(_redact_argv(parts)).split())[:2000]


def _redact_argv(parts: list[str]) -> list[str]:
    sanitized: list[str] = []
    redact_next = False
    for part in parts:
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next = False
            continue
        flag, separator, value = part.partition("=")
        normalized_flag = _normalized_key(flag.lstrip("-"))
        if flag in DwsClient.SENSITIVE_COMMAND_FLAGS or _is_sensitive_key(
            normalized_flag
        ):
            sanitized.append(
                f"{flag}={_REDACTED}" if separator else flag
            )
            redact_next = not separator
        elif (separator and _is_signed_url(value)) or _is_signed_url(part):
            sanitized.append(_REDACTED)
        elif contains_credential(part):
            sanitized.append(_REDACTED)
        else:
            sanitized.append(part)
    return sanitized


def _is_signed_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.query:
        return False
    return any(
        _is_sensitive_key(_normalized_key(name))
        for name, _value in parse_qsl(parsed.query, keep_blank_values=True)
    )


def _effect_event(payload: dict[str, object]) -> ToolEffectEvent | None:
    event_type = str(payload.get("type") or "")
    if event_type not in {"item.started", "item.completed", "item.failed"}:
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "").strip().casefold()
    effect = _native_effect_kind(item_type, item)
    if effect is None:
        return None
    call_id = item.get("call_id") or item.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    return ToolEffectEvent(
        call_id=call_id,
        effect=effect,
        status={
            "item.started": EffectEventStatus.STARTED,
            "item.completed": EffectEventStatus.COMPLETED,
            "item.failed": EffectEventStatus.FAILED,
        }[event_type],
    )


def _native_effect_kind(
    item_type: str, item: dict[str, object]
) -> EffectKind | None:
    if item_type in _NATIVE_READ_ONLY_ITEM_TYPES:
        return EffectKind.READ_ONLY
    if item_type not in _NATIVE_CLASSIFIABLE_ITEM_TYPES and not item_type.endswith(
        "_tool_call"
    ):
        return None
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        effect = metadata.get("effect")
        if effect in {EffectKind.READ_ONLY.value, EffectKind.EFFECTFUL.value}:
            return EffectKind(effect)
    for annotations in _annotation_candidates(item, metadata):
        effect = _mcp_annotation_effect(annotations)
        if effect is not None:
            return effect
    return None


def _annotation_candidates(
    item: dict[str, object], metadata: object
) -> tuple[dict[str, object], ...]:
    candidates: list[dict[str, object]] = []
    for candidate in (
        item.get("annotations"),
        metadata,
        metadata.get("annotations") if isinstance(metadata, dict) else None,
    ):
        if isinstance(candidate, dict):
            candidates.append(candidate)
    return tuple(candidates)


def _mcp_annotation_effect(annotations: dict[str, object]) -> EffectKind | None:
    read_only = annotations.get("readOnlyHint")
    destructive = annotations.get("destructiveHint")
    if read_only is True and destructive is not True:
        return EffectKind.READ_ONLY
    if destructive is True and read_only is not True:
        return EffectKind.EFFECTFUL
    return None


def _receipt(payload: dict[str, object]) -> ExecutionReceipt | None:
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") == "command_execution":
        return None
    operation_id = item.get("call_id") or item.get("id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        return None
    sources = [item[key] for key in ("result", "output") if key in item]
    receipt = _first_valid_receipt(sources)
    if receipt is None or receipt.operation_id != operation_id:
        return None
    return receipt


def structured_execution_evidence(
    events: tuple[dict[str, object], ...] | list[dict[str, object]],
) -> tuple[tuple[ToolEffectEvent, ...], tuple[ExecutionReceipt, ...]]:
    """Extract only trusted effect metadata and receipts from persisted events."""
    effect_events: list[ToolEffectEvent] = []
    receipts: list[ExecutionReceipt] = []
    for event in events:
        effect_event = _effect_event(event)
        if effect_event is not None:
            effect_events.append(effect_event)
        receipt = _receipt(event)
        if receipt is not None:
            receipts.append(receipt)
    return tuple(effect_events), tuple(receipts)


def _first_valid_receipt(sources: list[object]) -> ExecutionReceipt | None:
    for source in sources:
        for candidate in _structured_receipt_candidates(source):
            try:
                return ExecutionReceipt.model_validate(candidate)
            except ValueError:
                continue
    return None


def _structured_receipt_candidates(value: object):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return
        if isinstance(parsed, dict | list):
            yield from _structured_receipt_candidates(parsed)
        return
    if isinstance(value, list):
        for item in value:
            yield from _structured_receipt_candidates(item)
        return
    if not isinstance(value, dict):
        return
    if frozenset(value) == _RECEIPT_KEYS:
        yield value
        return
    for nested in value.values():
        yield from _structured_receipt_candidates(nested)


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
