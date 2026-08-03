import json
import hashlib
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
)
from app.codex_runner import CodexRunner
from app.codex_history import count_codex_session_lines, find_codex_session_path
from app.channel_gate import ChannelGateState
from app.dws_client import DwsClient
from app.history import safe_observability_error
from app.leak_check import contains_credential
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliCommand,
    NativeCliMetadataClassifier,
    NativeCliMetadataUnavailableError,
    describe_native_command,
    structured_target_identifiers,
)
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import AgentRun, AgentRunLeaseLostError, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import (
    make_direct_agent_sandbox,
    make_read_only_with_reviewed_tools,
)


AGENT_RESULT_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "agent_result.schema.json"
)
AGENT_RECONCILIATION_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "agent_reconciliation_result.schema.json"
)
DEFAULT_MCP_EFFECTS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "mcp-tool-effects.json"
)
SERVICE_ROOT = Path(__file__).resolve().parent.parent
SHARED_AGENT_RULES_PATH = Path.home() / ".agents" / "AGENT.md"
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
- Use the configured MCP tools and installed DWS/Lark CLIs directly. Read an applicable installed SKILL.md before using a business capability.
- After any confirmed OA action (approve, reject, return, or comment), identify the OA originator from the approval detail and notify that applicant through DingTalk before returning AgentResult. State the actual action and, when relevant, the next node or material needed. Use the real originator identifier; do not notify someone merely because they forwarded the request. Verify the send was accepted. If the originator cannot be resolved or notification fails, report that concrete exception in the final summary; do not invent delivery.
- Use the original conversation context and live tool results to decide and execute the task. Report the actual outcome without inventing success."""
READ_ONLY_DEVELOPER_INSTRUCTION = (
    "This invocation is read-only. Use configured MCP tools and installed CLIs "
    "only for reads. Do not perform any external write, send, approval, comment, "
    "reaction, edit, login, reset, logout, or other state-changing action."
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


def direct_agent_developer_instructions() -> str:
    if not SHARED_AGENT_RULES_PATH.is_file():
        return DIRECT_AGENT_DEVELOPER_INSTRUCTIONS
    shared_rules = SHARED_AGENT_RULES_PATH.read_text(encoding="utf-8").strip()
    if not shared_rules:
        return DIRECT_AGENT_DEVELOPER_INSTRUCTIONS
    return (
        DIRECT_AGENT_DEVELOPER_INSTRUCTIONS
        + "\n\nCanonical shared agent rules already loaded into this invocation. "
        "Do not re-read agent rule files through shell or exec.\n\n"
        + shared_rules
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
_COMMAND_CONTENT_FLAGS = frozenset(
    {
        "--body",
        "--comment",
        "--content",
        "--html",
        "--markdown",
        "--message",
        "--remark",
        "--text",
        "--title",
    }
)
_RECEIPT_KEYS = frozenset(ExecutionReceipt.model_fields)
_REDACTED = "[REDACTED]"
_MAX_MCP_RESULT_DEPTH = 32
_MAX_MCP_RESULT_NODES = 2048
_MAX_MCP_RESULT_JSON_STRINGS = 64
_MAX_MCP_RESULT_JSON_BYTES = 256 * 1024
_MAX_RECONCILIATION_EVENT_BYTES = 256 * 1024
_MAX_RECONCILIATION_EVENTS = 256


class AgentRunUnavailableError(RuntimeError):
    pass


class AgentStreamError(RuntimeError):
    pass


class AgentRunUnknownError(RuntimeError):
    def __init__(self, code: str, run_id: int) -> None:
        self.code = code
        self.run_id = run_id
        super().__init__(code)


class AgentRunNoEffectEvidenceError(RuntimeError):
    pass


class ReconciliationDependencyError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        channel: str,
        gate_state: ChannelGateState,
        retryable: bool,
    ) -> None:
        self.code = code
        self.channel = channel
        self.gate_state = gate_state
        self.retryable = retryable
        super().__init__(code)


class ReconciliationProof(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    observed_state: Literal["effect_present", "effect_absent"]


class ReconciliationError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = ""
    retryable: bool = False
    authorization_required: bool = False


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: AgentOutcome
    summary: str = Field(min_length=1)
    proof: ReconciliationProof | None = None
    error: ReconciliationError = Field(default_factory=ReconciliationError)


@dataclass(frozen=True)
class AgentReconciliationRunResult:
    run_id: int
    result: ReconciliationResult
    transcript_start_line: int
    transcript_end_line: int
    events: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class UnknownEffectReference:
    call_id: str
    transport: str
    operation: str
    operation_digest: str
    target_identifiers: dict[str, str]


@dataclass(frozen=True)
class DirectAgentRunResult:
    run_id: int
    result: AgentResult
    transcript_start_line: int
    transcript_end_line: int
    events: tuple[dict[str, object], ...]
    receipts: tuple[ExecutionReceipt, ...] = ()


@dataclass(frozen=True)
class McpToolCall:
    server: str
    tool: str
    effect: EffectKind
    operation: str
    operation_digest: str
    target_identifiers: dict[str, str]
    native_cli: str = ""


class McpToolEffectRegistry:
    """Exact reviewed MCP capabilities; unknown server/tool pairs fail closed."""

    def __init__(
        self,
        effects: dict[tuple[str, str], EffectKind],
        *,
        dry_run_arguments: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self._effects = dict(effects)
        self._dry_run_arguments = dict(dry_run_arguments or {})

    @classmethod
    def from_path(cls, path: Path) -> "McpToolEffectRegistry":
        if not path.exists():
            return cls({})
        payload = json.loads(path.read_text(encoding="utf-8"))
        tools = payload.get("tools") if isinstance(payload, dict) else None
        if not isinstance(tools, list):
            raise ValueError("MCP effect registry must contain a tools list")
        effects: dict[tuple[str, str], EffectKind] = {}
        dry_run_arguments: dict[tuple[str, str], str] = {}
        for item in tools:
            if not isinstance(item, dict):
                raise ValueError("MCP effect registry tools must be objects")
            server = item.get("server")
            tool = item.get("tool")
            effect = item.get("effect")
            if not isinstance(server, str) or not server.strip():
                raise ValueError("MCP effect registry server must be non-empty")
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError("MCP effect registry tool must be non-empty")
            if effect not in {EffectKind.READ_ONLY.value, EffectKind.EFFECTFUL.value}:
                raise ValueError("MCP effect registry effect is invalid")
            key = (server.strip(), tool.strip())
            parsed_effect = EffectKind(effect)
            if key in effects and effects[key] is not parsed_effect:
                raise ValueError("MCP effect registry contains a conflicting tool")
            effects[key] = parsed_effect
            dry_run_argument = item.get("dry_run_argument")
            if dry_run_argument is not None:
                if (
                    parsed_effect is not EffectKind.EFFECTFUL
                    or not isinstance(dry_run_argument, str)
                    or not dry_run_argument.strip()
                ):
                    raise ValueError("MCP effect registry dry-run argument is invalid")
                dry_run_arguments[key] = dry_run_argument.strip()
        return cls(effects, dry_run_arguments=dry_run_arguments)

    @classmethod
    def default(cls) -> "McpToolEffectRegistry":
        configured = os.environ.get("CEO_AGENT_MCP_EFFECTS_PATH", "").strip()
        return cls.from_path(Path(configured) if configured else DEFAULT_MCP_EFFECTS_PATH)

    def classify(self, item: dict[str, object]) -> McpToolCall | None:
        if item.get("type") != "mcp_tool_call":
            return None
        server = item.get("server")
        tool = item.get("tool")
        if not isinstance(server, str) or not isinstance(tool, str):
            return None
        effect = self._effects.get((server, tool))
        if effect is None:
            return None
        arguments = item.get("arguments")
        dry_run_argument = self._dry_run_arguments.get((server, tool))
        if (
            effect is EffectKind.EFFECTFUL
            and dry_run_argument
            and isinstance(arguments, dict)
            and arguments.get(dry_run_argument) is True
        ):
            effect = EffectKind.READ_ONLY
        canonical = json.dumps(
            {"server": server, "tool": tool, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return McpToolCall(
            server=server,
            tool=tool,
            effect=effect,
            operation=tool,
            operation_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            target_identifiers=structured_target_identifiers(arguments),
        )

    def reviewed_read_tools(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for (server, tool), effect in self._effects.items():
            if effect is EffectKind.READ_ONLY:
                grouped.setdefault(server, []).append(tool)
        return {server: tuple(sorted(tools)) for server, tools in grouped.items()}

    def reviewed_tools(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for server, tool in self._effects:
            grouped.setdefault(server, []).append(tool)
        return {server: tuple(sorted(tools)) for server, tools in grouped.items()}


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
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        codex_session_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.codex = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor or run_process_with_idle_timeout
        self.owner = owner or f"direct-agent-{uuid4().hex}"
        self.native_cli_classifier = (
            native_cli_classifier or NativeCliMetadataClassifier()
        )
        self.mcp_effect_registry = mcp_effect_registry or McpToolEffectRegistry.default()
        self.codex_session_exists = codex_session_exists or (
            lambda session_id: find_codex_session_path(session_id) is not None
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
        session_id = (
            run.codex_session_id
            or self.store.get_codex_session_id(task.conversation_id)
            or None
        )
        if session_id and not self.codex_session_exists(session_id):
            self.store.clear_codex_session(task.conversation_id)
            session_id = None
        transcript_start_line = count_codex_session_lines(session_id) if session_id else 0
        prompt = context.render()
        developer_instructions = direct_agent_developer_instructions()
        approval_policy = "untrusted"
        if read_only:
            approval_policy = "never"
            prompt = (
                "Read-only invocation. Do not perform any external write, send, "
                "approval, comment, reaction, document edit, or state-changing "
                "command. Query live state only.\n\n" + prompt
            )
            developer_instructions += "\n\n" + READ_ONLY_DEVELOPER_INSTRUCTION
        command = self.codex.build_command(
            prompt=prompt,
            session_id=session_id,
            output_schema_path=AGENT_RESULT_SCHEMA_PATH,
            approval_policy=approval_policy,
            developer_instructions=developer_instructions,
            use_approval_bypass=not read_only,
            preserve_native_model_config=True,
        )
        saw_json = False
        stream_line_count = 0

        def persist_line(line: str) -> None:
            nonlocal saw_json, stream_line_count
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
            stream_line_count += 1
            session_id = _session_id(payload)
            if session_id:
                self.store.set_agent_run_session(
                    run.id,
                    session_id,
                    owner=self.owner,
                    transcript_start_line=transcript_start_line,
                    now=now,
                )
                self.store.upsert_conversation(
                    task.conversation_id,
                    task.conversation_title,
                    task.single_chat,
                    session_id,
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
        except AgentReadOnlyViolationError as exc:
            self._record_failure(run.id, str(exc), now=now)
            raise
        except AgentRunLeaseLostError:
            raise
        except AgentStreamError as exc:
            self._record_failure(
                run.id,
                "codex_stream_invalid",
                now=now,
            )
            if self.store.get_agent_run(run.id).status == "unknown":
                raise AgentRunUnknownError("codex_stream_invalid", run.id) from exc
            raise RuntimeError("codex_stream_invalid") from exc
        except Exception as exc:
            self._record_failure(
                run.id,
                "codex_process_failed",
                now=now,
            )
            if self.store.get_agent_run(run.id).status == "unknown":
                raise AgentRunUnknownError("codex_process_failed", run.id) from exc
            raise RuntimeError("codex_process_failed") from exc

        if process.timed_out:
            self._record_failure(
                run.id,
                "codex_process_timeout",
                now=now,
            )
            if self.store.get_agent_run(run.id).status == "unknown":
                raise AgentRunUnknownError("codex_process_timeout", run.id)
            raise RuntimeError("codex_process_timeout")
        if process.returncode != 0:
            self._record_failure(
                run.id,
                "codex_process_failed",
                now=now,
            )
            if self.store.get_agent_run(run.id).status == "unknown":
                raise AgentRunUnknownError("codex_process_failed", run.id)
            raise RuntimeError("codex_process_failed")
        persisted = self.store.get_agent_run(run.id)
        if persisted is None:
            raise RuntimeError("agent run was not persisted")
        try:
            result = parse_agent_result(process.stdout)
        except (ResultParseError, ValueError) as exc:
            self._record_failure(
                run.id,
                "codex_result_invalid",
                now=now,
            )
            if self.store.get_agent_run(run.id).status == "unknown":
                raise AgentRunUnknownError(
                    "codex_result_invalid", run.id
                ) from exc
            raise RuntimeError("codex_result_invalid") from exc

        persisted_session_id = self.store.get_agent_run(run.id).codex_session_id
        transcript_end_line = max(
            transcript_start_line + stream_line_count,
            count_codex_session_lines(persisted_session_id)
            if persisted_session_id
            else 0,
        )
        if result.outcome is AgentOutcome.FAILED:
            self.store.fail_agent_run(
                run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=SideEffectState.NONE.value,
                transcript_end_line=transcript_end_line,
                now=now,
            )
        else:
            self.store.complete_agent_run(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=SideEffectState.NONE.value,
                transcript_end_line=transcript_end_line,
                now=now,
            )
        completed_run = self.store.get_agent_run(run.id)
        if completed_run is None:
            raise RuntimeError("agent run was not persisted")
        return DirectAgentRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=transcript_start_line,
            transcript_end_line=completed_run.transcript_end_line,
            events=(),
            receipts=(),
        )

    def reconcile(
        self,
        existing_run: AgentRun,
        context: AgentTaskContext,
        *,
        now: str | None = None,
    ) -> AgentReconciliationRunResult:
        if existing_run.status != "unknown":
            raise ValueError("reconciliation requires an unknown agent run")
        if context.task_id != existing_run.reply_task_id:
            raise ValueError("agent context does not match unknown run")
        claim = self.store.claim_unknown_agent_run(
            existing_run.id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
            now=now,
        )
        if not claim.claimed:
            raise AgentRunUnavailableError(
                f"agent reconciliation is not available: {existing_run.id}"
            )
        run = claim.run
        original = unknown_effect_reference(run.tool_events)
        prompt = _reconciliation_prompt(context, original)
        command = self.codex.build_command(
            prompt=prompt,
            session_id=None,
            output_schema_path=AGENT_RECONCILIATION_SCHEMA_PATH,
            approval_policy="never",
            developer_instructions=(
                direct_agent_developer_instructions()
                + "\n\n"
                + READ_ONLY_DEVELOPER_INSTRUCTION
            ),
            use_approval_bypass=False,
            preserve_native_model_config=True,
        )
        make_read_only_with_reviewed_tools(
            command,
            reviewed_mcp_tools=self.mcp_effect_registry.reviewed_read_tools(),
            controlled_cli_command=sys.executable,
            controlled_cli_args=("-m", "app.reconciliation_cli"),
            controlled_cli_cwd=str(SERVICE_ROOT),
        )
        self.native_cli_classifier.prewarm()
        appended_events: list[dict[str, object]] = []
        saw_json = False

        def persist_line(line: str) -> None:
            nonlocal saw_json
            if not line.strip():
                return
            if len(line.encode("utf-8")) > _MAX_RECONCILIATION_EVENT_BYTES:
                raise AgentStreamError("reconciliation_event_too_large")
            if len(appended_events) >= _MAX_RECONCILIATION_EVENTS:
                raise AgentStreamError("reconciliation_event_limit_exceeded")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                if saw_json:
                    raise AgentStreamError("codex_stream_invalid") from exc
                return
            saw_json = True
            if not isinstance(payload, dict):
                raise AgentStreamError("codex_stream_invalid")
            safe_event = self._read_only_safe_event(payload)
            appended_events.append(safe_event)
            self.store.append_unknown_agent_run_event(
                run.id,
                safe_event,
                owner=self.owner,
                now=now,
            )
            dependency_error = _reconciliation_dependency_error(safe_event)
            if dependency_error is not None:
                raise dependency_error

        try:
            process = self.executor(
                command,
                prompt=prompt,
                env=self.codex.build_env(preserve_local_cli_auth=True),
                total_timeout_seconds=TOTAL_TIMEOUT_SECONDS,
                idle_timeout_seconds=IDLE_TIMEOUT_SECONDS,
                on_stdout_line=persist_line,
            )
        except AgentReadOnlyViolationError:
            raise
        except ReconciliationDependencyError:
            raise
        except AgentRunLeaseLostError:
            raise
        except AgentStreamError as exc:
            raise RuntimeError("codex_stream_invalid") from exc
        except Exception as exc:
            raise RuntimeError("codex_process_failed") from exc
        if process.timed_out:
            raise RuntimeError("codex_process_timeout")
        if process.returncode != 0:
            raise RuntimeError("codex_process_failed")
        result = _parse_reconciliation_result(process.stdout)
        _validate_reconciliation_proof(result, original, appended_events)
        persisted = self.store.get_agent_run(run.id)
        if persisted is None:
            raise RuntimeError("agent run was not persisted")
        return AgentReconciliationRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=run.transcript_end_line,
            transcript_end_line=persisted.transcript_end_line,
            events=tuple(appended_events),
        )

    def _read_only_safe_event(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        event_type = payload.get("type")
        item = payload.get("item")
        if event_type not in {"item.started", "item.completed", "item.failed"}:
            return _safe_event(payload)
        if not isinstance(item, dict):
            return _safe_event(payload)
        item_type = str(item.get("type") or "")
        if item_type == "command_execution":
            raise AgentReadOnlyViolationError("reconciliation_shell_forbidden")
        if item_type == "mcp_tool_call":
            if (
                item.get("server") == "reconciliation_cli"
                and item.get("tool") == "execute_reviewed_read"
            ):
                arguments = item.get("arguments")
                argv = arguments.get("argv") if isinstance(arguments, dict) else None
                command_item = {"type": "command_execution", "argv": argv}
                try:
                    command = self.native_cli_classifier.classify_cached(command_item)
                except NativeCliMetadataUnavailableError as exc:
                    descriptor = describe_native_command(command_item)
                    if descriptor is None:
                        raise AgentReadOnlyViolationError(
                            "reconciliation_command_unreviewed"
                        ) from exc
                    return _metadata_discovery_failure_event(
                        payload,
                        descriptor=descriptor,
                        discovery_error=exc,
                    )
                if command is None or command.effect is not EffectKind.READ_ONLY:
                    raise AgentReadOnlyViolationError("reconciliation_write_forbidden")
                safe_event = _safe_event(payload, native_command=command)
                if event_type == "item.completed":
                    receipt = _controlled_cli_receipt(item.get("result"))
                    if (
                        receipt is None
                        or receipt.get("operation") != command.command_path
                        or receipt.get("operation_digest") != command.command_digest
                        or receipt.get("target_identifiers")
                        != command.target_identifiers
                    ):
                        raise AgentReadOnlyViolationError(
                            "reconciliation_query_receipt_invalid"
                        )
                    safe_item = safe_event.get("item")
                    metadata = (
                        safe_item.get("metadata")
                        if isinstance(safe_item, dict)
                        else None
                    )
                    if isinstance(metadata, dict):
                        receipt_error = receipt.get("error")
                        if isinstance(receipt_error, dict):
                            metadata["reconciliation_error"] = (
                                _validated_reconciliation_error(receipt_error)
                            )
                        else:
                            metadata["result_digest"] = receipt["result_digest"]
                return safe_event
            call = self.mcp_effect_registry.classify(item)
            if call is None or call.effect is not EffectKind.READ_ONLY:
                raise AgentReadOnlyViolationError("reconciliation_write_forbidden")
            return _safe_event(payload, mcp_call=call)
        if item_type in _NATIVE_READ_ONLY_ITEM_TYPES or item_type == "agent_message":
            return _safe_event(payload)
        if (
            item_type.endswith("_tool_call")
            or item_type in _NATIVE_CLASSIFIABLE_ITEM_TYPES
        ):
            raise AgentReadOnlyViolationError("reconciliation_unknown_tool_forbidden")
        return _safe_event(payload)

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
        if persisted.status != "running":
            return
        self.store.fail_agent_run(
            run_id,
            {"code": code, "retryable": True},
            owner=self.owner,
            transcript_end_line=persisted.transcript_end_line,
            side_effect_state=SideEffectState.NONE.value,
            now=now,
        )

    def _persist_deferred_execution_evidence(
        self,
        run_id: int,
        stdout: str,
        *,
        now: str | None,
    ) -> None:
        for line in stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            native_command = _native_cli_command(
                payload,
                self.native_cli_classifier,
                cached_only=False,
            )
            mcp_call = _mcp_tool_call(payload, self.mcp_effect_registry)
            if native_command is None and mcp_call is None:
                continue
            safe_event = _effect_evidence_event(
                payload,
                native_command=native_command,
                mcp_call=mcp_call,
            )
            self.store.append_agent_run_event(
                run_id,
                safe_event,
                owner=self.owner,
                now=now,
            )
            if (
                native_command is not None
                and native_command.effect is EffectKind.EFFECTFUL
                and _native_command_completed(payload)
            ):
                call_id = _native_call_id(payload)
                if call_id:
                    self.store.record_agent_execution_receipt(
                        run_id,
                        receipt_id=f"native-cli:{run_id}:{call_id}",
                        operation_id=call_id,
                        cli=native_command.cli,
                        command_path=native_command.command_path,
                        command_digest=native_command.command_digest,
                        exit_code=0,
                        owner=self.owner,
                        now=now,
                    )
            if (
                mcp_call is not None
                and mcp_call.effect is EffectKind.EFFECTFUL
                and _mcp_call_completed(payload)
            ):
                call_id = _native_call_id(payload)
                if call_id:
                    self.store.record_agent_execution_receipt(
                        run_id,
                        receipt_id=f"mcp:{run_id}:{call_id}",
                        operation_id=call_id,
                        cli=f"mcp:{mcp_call.server}",
                        command_path=mcp_call.tool,
                        command_digest=mcp_call.operation_digest,
                        exit_code=0,
                        owner=self.owner,
                        now=now,
                    )

    def _classify_persisted_execution_events(
        self,
        run_id: int,
        *,
        now: str | None,
    ) -> None:
        run = self.store.get_agent_run(run_id)
        if run is None:
            raise RuntimeError("agent run was not persisted")
        serialized = "\n".join(
            json.dumps(event, ensure_ascii=False) for event in run.tool_events
        )
        self._persist_deferred_execution_evidence(run_id, serialized, now=now)


def unknown_effect_reference(
    events: list[dict[str, object]] | tuple[dict[str, object], ...],
) -> UnknownEffectReference:
    started: dict[str, dict[str, object]] = {}
    closed: set[str] = set()
    saw_effectful = False
    saw_unreviewed = False
    for event in events:
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        effect = metadata.get("effect") if isinstance(metadata, dict) else None
        if effect == EffectKind.EFFECTFUL.value:
            saw_effectful = True
        elif effect == EffectKind.UNREVIEWED.value:
            saw_unreviewed = True
        call_id = item.get("call_id") or item.get("id")
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get("type") == "item.started" and isinstance(metadata, dict):
            if effect == EffectKind.EFFECTFUL.value:
                started[call_id] = metadata
        elif event.get("type") in {"item.completed", "item.failed"}:
            closed.add(call_id)
    incomplete = [
        (call_id, data) for call_id, data in started.items() if call_id not in closed
    ]
    if not incomplete and not saw_effectful and not saw_unreviewed:
        raise AgentRunNoEffectEvidenceError(
            "unknown_run_has_no_incomplete_effect"
        )
    if saw_unreviewed:
        raise ValueError("unknown_run_contains_unreviewed_effect")
    if not incomplete:
        raise ValueError("unknown_run_effect_identity_missing")
    if len(incomplete) != 1:
        raise ValueError("unknown_run_effect_count_invalid")
    call_id, metadata = incomplete[0]
    digest = metadata.get("command_digest") or metadata.get("operation_digest")
    operation = metadata.get("operation")
    transport = metadata.get("native_cli") or metadata.get("mcp_server")
    targets = metadata.get("target_identifiers")
    if not all(
        isinstance(value, str) and value for value in (digest, operation, transport)
    ):
        raise ValueError("unknown_effect_identity_incomplete")
    target_identifiers = (
        {
            str(key): str(value)
            for key, value in targets.items()
            if isinstance(key, str) and isinstance(value, str) and value
        }
        if isinstance(targets, dict)
        else {}
    )
    return UnknownEffectReference(
        call_id=call_id,
        transport=str(transport),
        operation=str(operation),
        operation_digest=str(digest),
        target_identifiers=target_identifiers,
    )


def _reconciliation_prompt(
    context: AgentTaskContext,
    original: UnknownEffectReference,
) -> str:
    identity = {
        "call_id": original.call_id,
        "transport": original.transport,
        "operation": original.operation,
        "operation_digest": original.operation_digest,
        "target_identifiers": original.target_identifiers,
    }
    return (
        "Read-only unknown side-effect reconciliation. Never replay the original "
        "operation. Run exact DWS/Lark read commands only through the "
        "reconciliation_cli execute_reviewed_read tool; direct shell execution is "
        "disabled. Query live state with reviewed read-only tools. Return completed "
        "only when the effect is present, no_action only when its absence is confirmed, "
        "and set proof.observed_state to effect_present or effect_absent. The service "
        "binds the unique matching completed live read receipt; do not reproduce "
        "internal call IDs or digests.\n\n"
        "Original uncertain operation\n"
        + json.dumps(identity, ensure_ascii=False, indent=2)
        + "\n\n"
        + context.render()
    )


def _parse_reconciliation_result(raw: str) -> ReconciliationResult:
    payloads: list[dict[str, object]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    for payload in reversed(payloads):
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        candidate = item.get("text") or item.get("message")
        if not isinstance(candidate, str):
            continue
        try:
            return ReconciliationResult.model_validate_json(candidate)
        except ValidationError as exc:
            raise RuntimeError("reconciliation_result_invalid") from exc
    raise RuntimeError("reconciliation_result_invalid")


def _validate_reconciliation_proof(
    result: ReconciliationResult,
    original: UnknownEffectReference,
    events: list[dict[str, object]],
) -> None:
    if result.outcome not in {AgentOutcome.COMPLETED, AgentOutcome.NO_ACTION}:
        return
    proof = result.proof
    expected_state = (
        "effect_present"
        if result.outcome is AgentOutcome.COMPLETED
        else "effect_absent"
    )
    if proof is None or proof.observed_state != expected_state:
        raise RuntimeError("reconciliation_proof_invalid")
    matches = [
        event
        for event in events
        if _is_matching_reconciliation_read_event(event, original)
    ]
    if not matches:
        raise RuntimeError("reconciliation_proof_invalid")
    if len(matches) != 1:
        raise RuntimeError("reconciliation_proof_ambiguous")


def _is_matching_reconciliation_read_event(
    event: dict[str, object], original: UnknownEffectReference
) -> bool:
    if event.get("type") != "item.completed":
        return False
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
        return False
    metadata = item.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("effect") != "read_only":
        return False
    server = item.get("server")
    tool = item.get("tool")
    controlled_cli = (
        server == "reconciliation_cli"
        and tool == "execute_reviewed_read"
        and metadata.get("native_cli") in {"dws", "lark-cli"}
    )
    reviewed_mcp = (
        isinstance(server, str)
        and server != "reconciliation_cli"
        and isinstance(tool, str)
        and metadata.get("mcp_server") == server
        and metadata.get("operation") == tool
    )
    query_targets = metadata.get("target_identifiers")
    operation_digest = metadata.get("command_digest") or metadata.get(
        "operation_digest"
    )
    call_id = item.get("call_id") or item.get("id")
    if (
        not (controlled_cli or reviewed_mcp)
        or not isinstance(call_id, str)
        or not call_id
        or not isinstance(operation_digest, str)
        or not operation_digest
        or not isinstance(metadata.get("result_digest"), str)
        or not metadata.get("result_digest")
        or not isinstance(query_targets, dict)
        or not original.target_identifiers
    ):
        return False
    return all(
        any(
            _target_key_matches(original_key, query_key)
            and query_value == original_value
            for query_key, query_value in query_targets.items()
        )
        for original_key, original_value in original.target_identifiers.items()
    )


def _target_key_matches(left: str, right: str) -> bool:
    left_parts = _target_key_parts(left)
    right_parts = _target_key_parts(right)
    if left_parts == right_parts:
        return True
    return {left_parts, right_parts} == {
        ("instance", "id"),
        ("process", "instance", "id"),
    }


def _target_key_parts(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    current = ""
    for character in value:
        if not character.isalnum():
            if current:
                parts.append(current.casefold())
                current = ""
            continue
        if current and character.isupper() and not current[-1].isupper():
            parts.append(current.casefold())
            current = character
        else:
            current += character
    if current:
        parts.append(current.casefold())
    return tuple(parts)


def _controlled_cli_receipt(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or value.get("isError") is True:
        return None
    candidates = [value]
    structured = value.get("structuredContent") or value.get("structured_content")
    if isinstance(structured, dict):
        candidates.append(structured)
    for candidate in candidates:
        if not isinstance(candidate.get("result_digest"), str):
            continue
        if not isinstance(candidate.get("operation_digest"), str):
            continue
        if not isinstance(candidate.get("operation"), str):
            continue
        if not isinstance(candidate.get("target_identifiers"), dict):
            continue
        return candidate
    return None


def _metadata_discovery_failure_event(
    payload: dict[str, object],
    *,
    descriptor: NativeCliCommand,
    discovery_error: NativeCliMetadataUnavailableError,
) -> dict[str, object]:
    safe_event = _safe_event(payload)
    if payload.get("type") != "item.completed":
        return safe_event
    item = payload.get("item")
    receipt = (
        _controlled_cli_receipt(item.get("result"))
        if isinstance(item, dict)
        else None
    )
    receipt_error = receipt.get("error") if isinstance(receipt, dict) else None
    validated_error = (
        _validated_reconciliation_error(receipt_error)
        if isinstance(receipt_error, dict)
        else None
    )
    expected_error = {
        "channel": discovery_error.cli,
        "code": discovery_error.code,
        "gate_state": ChannelGateState.UNAVAILABLE.value,
        "retryable": discovery_error.retryable,
    }
    if (
        receipt is None
        or receipt.get("operation") != descriptor.command_path
        or receipt.get("operation_digest") != descriptor.command_digest
        or receipt.get("target_identifiers") != descriptor.target_identifiers
        or validated_error != expected_error
    ):
        raise AgentReadOnlyViolationError("reconciliation_query_receipt_invalid")
    safe_item = safe_event.get("item")
    if not isinstance(safe_item, dict):
        raise AgentReadOnlyViolationError("reconciliation_query_receipt_invalid")
    safe_item["metadata"] = {
        "native_cli": descriptor.cli,
        "operation": descriptor.command_path,
        "command_digest": descriptor.command_digest,
        "target_identifiers": descriptor.target_identifiers,
        "reconciliation_error": validated_error,
    }
    return safe_event


def _reconciliation_dependency_error(
    event: dict[str, object],
) -> ReconciliationDependencyError | None:
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
        return None
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return None
    error = metadata.get("reconciliation_error")
    if not isinstance(error, dict):
        return None
    validated = _validated_reconciliation_error(error)
    return ReconciliationDependencyError(
        validated["code"],
        channel=validated["channel"],
        gate_state=ChannelGateState(validated["gate_state"]),
        retryable=validated["retryable"],
    )


def _validated_reconciliation_error(error: dict[str, object]) -> dict[str, object]:
    code = error.get("code")
    channel = error.get("channel")
    gate_state = error.get("gate_state")
    retryable = error.get("retryable")
    if (
        not isinstance(code, str)
        or not code
        or channel not in {"dws", "lark-cli"}
        or gate_state not in {state.value for state in ChannelGateState}
        or not isinstance(retryable, bool)
    ):
        raise AgentReadOnlyViolationError("reconciliation_query_receipt_invalid")
    return {
        "channel": channel,
        "code": code,
        "gate_state": gate_state,
        "retryable": retryable,
    }


def _session_id(payload: dict[str, object]) -> str:
    if payload.get("type") not in {"thread.started", "thread_started"}:
        return ""
    for key in ("thread_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _native_cli_command(
    payload: dict[str, object],
    classifier: NativeCliMetadataClassifier,
    *,
    cached_only: bool,
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
    try:
        return (
            classifier.classify_cached(item)
            if cached_only
            else classifier.classify(item)
        )
    except NativeCliMetadataUnavailableError:
        return None


def _mcp_tool_call(
    payload: dict[str, object],
    registry: McpToolEffectRegistry,
) -> McpToolCall | None:
    if payload.get("type") not in {
        "item.started",
        "item.completed",
        "item.failed",
    }:
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    call = registry.classify(item)
    if call is None or call.server != "reconciliation_cli":
        return call
    if call.tool not in {"execute_reviewed_read", "execute_reviewed_write"}:
        return call
    arguments = item.get("arguments")
    argv = arguments.get("argv") if isinstance(arguments, dict) else None
    descriptor = describe_native_command(
        {"type": "command_execution", "argv": argv}
    )
    if descriptor is None:
        return call
    return McpToolCall(
        server=call.server,
        tool=call.tool,
        effect=call.effect,
        operation=descriptor.command_path,
        operation_digest=descriptor.command_digest,
        target_identifiers=descriptor.target_identifiers,
        native_cli=descriptor.cli,
    )


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


def _mcp_call_completed(payload: dict[str, object]) -> bool:
    if payload.get("type") != "item.completed":
        return False
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("status") != "completed":
        return False
    result = item.get("result")
    return _mcp_result_explicitly_succeeded(result)


def _mcp_result_explicitly_succeeded(value: object) -> bool:
    """Accept only a valid top-level MCP CallToolResult without error evidence."""
    decoded_strings = 0
    decoded_bytes = 0
    if isinstance(value, str):
        if len(value) > _MAX_MCP_RESULT_JSON_BYTES:
            return False
        try:
            encoded_size = len(value.encode("utf-8"))
        except (UnicodeError, MemoryError):
            return False
        if encoded_size > _MAX_MCP_RESULT_JSON_BYTES:
            return False
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
            return False
        decoded_strings = 1
        decoded_bytes = encoded_size
    if not isinstance(value, dict) or not value:
        return False

    if "content" not in value:
        return False
    content = value["content"]
    if not isinstance(content, list) or not all(
        _valid_mcp_content_block(block) for block in content
    ):
        return False

    if "isError" in value:
        flag = value["isError"]
        if not isinstance(flag, bool) or flag:
            return False

    structured_keys = ("structured_content", "structuredContent")
    for key in structured_keys:
        if key in value and value[key] is not None and not isinstance(value[key], dict):
            return False

    stack: list[tuple[object, int, bool, bool]] = [(value, 0, True, False)]
    node_count = 0
    while stack:
        current, depth, inspect_errors, decode_json_strings = stack.pop()
        node_count += 1
        if node_count > _MAX_MCP_RESULT_NODES or depth > _MAX_MCP_RESULT_DEPTH:
            return False

        if isinstance(current, dict):
            if len(current) > _MAX_MCP_RESULT_NODES - node_count - len(stack):
                return False
            if inspect_errors and _mcp_mapping_has_error(current):
                return False
            for key, nested in current.items():
                if depth == 0:
                    child_errors = key in {"result", *structured_keys}
                    child_decode = child_errors
                else:
                    child_errors = inspect_errors
                    child_decode = decode_json_strings
                stack.append((nested, depth + 1, child_errors, child_decode))
            continue
        if isinstance(current, list):
            if len(current) > _MAX_MCP_RESULT_NODES - node_count - len(stack):
                return False
            for nested in current:
                stack.append(
                    (nested, depth + 1, inspect_errors, decode_json_strings)
                )
            continue
        if not decode_json_strings or not isinstance(current, str):
            continue

        stripped = current.lstrip()
        if not stripped.startswith(("{", "[")):
            continue
        remaining_bytes = _MAX_MCP_RESULT_JSON_BYTES - decoded_bytes
        if len(current) > remaining_bytes:
            return False
        try:
            encoded_size = len(current.encode("utf-8"))
        except (UnicodeError, MemoryError):
            return False
        if (
            decoded_strings >= _MAX_MCP_RESULT_JSON_STRINGS
            or encoded_size > remaining_bytes
        ):
            return False
        try:
            decoded = json.loads(current)
        except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
            return False
        if not isinstance(decoded, (dict, list)):
            continue
        decoded_strings += 1
        decoded_bytes += encoded_size
        stack.append((decoded, depth + 1, inspect_errors, True))

    return True


def _mcp_mapping_has_error(value: dict[str, object]) -> bool:
    if "isError" in value:
        flag = value["isError"]
        if not isinstance(flag, bool) or flag:
            return True
    for key, nested in value.items():
        normalized_key = key.replace("_", "").lower()
        if normalized_key == "error" and nested not in (None, False, ""):
            return True
        if normalized_key in {"errorcode", "errcode"} and nested not in (
            None,
            False,
            0,
            "",
            "0",
        ):
            return True
    return False


def _valid_mcp_content_block(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    block_type = value.get("type")
    if block_type == "text":
        return isinstance(value.get("text"), str)
    if block_type in {"image", "audio"}:
        mime_type = value.get("mimeType", value.get("mime_type"))
        return isinstance(value.get("data"), str) and isinstance(mime_type, str)
    if block_type == "resource_link":
        return isinstance(value.get("name"), str) and isinstance(
            value.get("uri"), str
        )
    if block_type != "resource":
        return False
    resource = value.get("resource")
    if not isinstance(resource, dict) or not isinstance(resource.get("uri"), str):
        return False
    return isinstance(resource.get("text"), str) or isinstance(
        resource.get("blob"), str
    )


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


def _effect_evidence_event(
    payload: dict[str, object],
    *,
    native_command: NativeCliCommand | None,
    mcp_call: McpToolCall | None,
) -> dict[str, object]:
    item = payload.get("item")
    if not isinstance(item, dict):
        raise ValueError("effect evidence requires an item")
    evidence_item: dict[str, object] = {
        "type": str(item.get("type") or ""),
    }
    call_id = _native_call_id(payload)
    if call_id:
        evidence_item["id"] = call_id
    if isinstance(item.get("status"), str):
        evidence_item["status"] = item["status"]
    evidence = {
        "type": str(payload.get("type") or ""),
        "item": evidence_item,
    }
    return _safe_event(
        evidence,
        native_command=native_command,
        mcp_call=mcp_call,
        completion_payload=payload,
    )


def _safe_event(
    payload: dict[str, object],
    *,
    native_command: NativeCliCommand | None = None,
    mcp_call: McpToolCall | None = None,
    completion_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    completion_payload = completion_payload or payload
    safe_event = _minimal_safe_execution_event(payload)
    item = safe_event.get("item")
    if (
        native_command is None
        and mcp_call is None
        and isinstance(item, dict)
        and (
            item.get("type") in _NATIVE_CLASSIFIABLE_ITEM_TYPES
            or str(item.get("type") or "").endswith("_tool_call")
        )
    ):
        item.pop("metadata", None)
        item.pop("annotations", None)
        metadata: dict[str, object] = {"effect": EffectKind.UNREVIEWED.value}
        item["metadata"] = metadata
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
                "target_identifiers": native_command.target_identifiers,
            }
        )
        result_digest = _native_read_result_digest(completion_payload)
        if result_digest:
            metadata["result_digest"] = result_digest
        if (
            safe_event.get("type") == "item.completed"
            and native_command.effect is EffectKind.EFFECTFUL
            and not _native_command_completed(completion_payload)
        ):
            safe_event["type"] = "item.failed"
    if mcp_call is not None and isinstance(item, dict):
        item["server"] = mcp_call.server
        item["tool"] = mcp_call.tool
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            item["metadata"] = metadata
        metadata.update(
            {
                "effect": mcp_call.effect.value,
                "mcp_server": mcp_call.server,
                "operation": mcp_call.operation,
                "operation_digest": mcp_call.operation_digest,
                "target_identifiers": mcp_call.target_identifiers,
            }
        )
        if mcp_call.native_cli:
            metadata["native_cli"] = mcp_call.native_cli
        result_digest = _mcp_read_result_digest(completion_payload)
        if result_digest:
            metadata["result_digest"] = result_digest
        if (
            safe_event.get("type") == "item.completed"
            and mcp_call.effect is EffectKind.EFFECTFUL
            and not _mcp_call_completed(completion_payload)
        ):
            safe_event["type"] = "item.failed"
    return safe_event


def _native_read_result_digest(payload: dict[str, object]) -> str:
    if not _native_command_completed(payload):
        return ""
    item = payload.get("item")
    if not isinstance(item, dict):
        return ""
    output = item.get("aggregated_output")
    if not isinstance(output, str):
        return ""
    return hashlib.sha256(output.encode("utf-8")).hexdigest()


def _mcp_read_result_digest(payload: dict[str, object]) -> str:
    if not _mcp_call_completed(payload):
        return ""
    item = payload.get("item")
    if not isinstance(item, dict):
        return ""
    result = item.get("result")
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _minimal_safe_execution_event(
    payload: dict[str, object],
) -> dict[str, object]:
    item = payload.get("item")
    if not isinstance(item, dict):
        return {
            str(key): _sanitize_event_value(value, key=str(key))
            for key, value in payload.items()
        }
    item_type = str(item.get("type") or "")
    if item_type == "mcp_tool_call":
        safe_item: dict[str, object] = {"type": item_type}
        for key in ("id", "call_id", "server", "tool", "status"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                safe_item[key] = safe_observability_error(value, limit=200)
        return {"type": str(payload.get("type") or ""), "item": safe_item}
    if item_type == "command_execution":
        safe_item = {"type": item_type}
        for key in ("id", "call_id", "status"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                safe_item[key] = safe_observability_error(value, limit=200)
        if isinstance(item.get("exit_code"), int):
            safe_item["exit_code"] = item["exit_code"]
        for key in _COMMAND_KEY_NAMES:
            if key in item:
                safe_item[key] = _sanitize_event_value(item[key], key=key)
                break
        return {"type": str(payload.get("type") or ""), "item": safe_item}
    return {
        str(key): _sanitize_event_value(value, key=str(key))
        for key, value in payload.items()
    }


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
        if (
            flag in DwsClient.SENSITIVE_COMMAND_FLAGS
            or flag in _COMMAND_CONTENT_FLAGS
            or _is_sensitive_key(normalized_flag)
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
        if effect in {
            EffectKind.READ_ONLY.value,
            EffectKind.EFFECTFUL.value,
            EffectKind.UNREVIEWED.value,
        }:
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
