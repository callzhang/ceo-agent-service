"""Strict command, credential, permission, and event boundary for Claude CLI."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TypeVar

from app.agent_effects import McpToolEffectRegistry
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeEventType,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.codex_runtime_adapter import _safe_child_environment
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.service_codex_config import ServiceMcpServer, load_service_mcp_servers

ResultT = TypeVar("ResultT")
_POLICY_SEAL = object()
_PERMISSION_TOOL = "mcp__ceo_runtime_permission__permission_prompt"
_BUILTIN_TOOLS = frozenset(
    {
        "Agent", "AskUserQuestion", "Bash", "Edit", "EnterPlanMode",
        "ExitPlanMode", "Glob", "Grep", "KillShell", "LS", "NotebookEdit",
        "Read", "Skill", "Task", "TaskCreate", "TaskGet", "TaskList",
        "TaskOutput", "TaskStop", "TaskUpdate", "TodoWrite", "WebFetch",
        "WebSearch", "Write",
    }
)


class ClaudeEventPolicyError(RuntimeError):
    """A Claude event cannot be proven safe under the reviewed event grammar."""


class ClaudeRuntimeResultError(RuntimeError):
    def __init__(self, failure: RuntimeFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)


@dataclass(frozen=True, slots=True)
class ClaudeTerminalProof:
    """Opaque proof that one invocation reached a valid terminal result."""

    result: str
    session_id: str
    nonce: str


@dataclass(frozen=True, slots=True, init=False)
class ClaudeCommandPolicy:
    """Sealed pre-execution tool surface for one Claude invocation."""

    mcp_tools: tuple[str, ...]
    allow_native_cli: bool
    _seal: object

    def __init__(
        self, *, mcp_tools: tuple[str, ...], allow_native_cli: bool, seal: object
    ) -> None:
        if seal is not _POLICY_SEAL:
            raise ValueError("Claude command policies use named constructors")
        if len(mcp_tools) != len(set(mcp_tools)) or any(
            not item.startswith("mcp__")
            or item.count("__") != 2
            or "*" in item
            or not item.strip()
            for item in mcp_tools
        ):
            raise ValueError("Claude MCP tools must be unique exact names")
        object.__setattr__(self, "mcp_tools", tuple(sorted(mcp_tools)))
        object.__setattr__(self, "allow_native_cli", allow_native_cli)
        object.__setattr__(self, "_seal", seal)

    @classmethod
    def no_tools(cls) -> ClaudeCommandPolicy:
        return cls(mcp_tools=(), allow_native_cli=False, seal=_POLICY_SEAL)

    @classmethod
    def reviewed(
        cls, *, mcp_tools: tuple[str, ...] = (), allow_native_cli: bool = False
    ) -> ClaudeCommandPolicy:
        return cls(
            mcp_tools=mcp_tools,
            allow_native_cli=allow_native_cli,
            seal=_POLICY_SEAL,
        )


class ClaudeRuntimeAdapter:
    """Build isolated non-interactive Claude invocations for one route."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: AgentRuntimeConfig,
        claude_bin: str = "claude",
        effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.claude_bin = claude_bin
        self.effects = effect_registry or McpToolEffectRegistry.default()
        self.native_cli = native_cli_classifier or NativeCliMetadataClassifier()
        self._runtime_root = tempfile.TemporaryDirectory(
            prefix="ceo-agent-claude-", dir=workspace
        )
        self._lock = RLock()
        self._pending_proofs: dict[
            object, tuple[ClaudeTerminalProof, str]
        ] = {}
        self._invocation_env_names: dict[str, frozenset[str]] = {}

    def build_command(
        self,
        *,
        route: RuntimeRoute,
        session_id: str | None,
        max_turns: int,
        policy: ClaudeCommandPolicy | None = None,
    ) -> list[str]:
        configured = self._configured_route(route)
        selected_policy = policy or ClaudeCommandPolicy.no_tools()
        if not isinstance(selected_policy, ClaudeCommandPolicy):
            raise ValueError("Claude command policy is invalid")
        if (
            isinstance(max_turns, bool)
            or not isinstance(max_turns, int)
            or max_turns <= 0
        ):
            raise ValueError("max_turns must be a positive integer")
        settings_path, mcp_path = self._write_invocation_boundary(selected_policy)
        exposed_builtins = "Bash" if selected_policy.allow_native_cli else ""
        denied_builtins = sorted(
            _BUILTIN_TOOLS
            - ({"Bash"} if selected_policy.allow_native_cli else set())
        )
        command = [
            self.claude_bin, "-p", "--bare", "--setting-sources", "",
            "--settings", str(settings_path), "--strict-mcp-config",
            "--mcp-config", str(mcp_path), "--input-format", "text",
            "--output-format", "stream-json", "--model", configured.model,
            "--max-turns", str(max_turns), "--verbose", "--tools",
            exposed_builtins, "--disallowedTools", *denied_builtins,
        ]
        if selected_policy.mcp_tools or selected_policy.allow_native_cli:
            command.extend(
                [
                    "--allowedTools", _PERMISSION_TOOL,
                    "--permission-mode", "default",
                    "--permission-prompt-tool", _PERMISSION_TOOL,
                ]
            )
        if session_id is not None:
            if not session_id.strip() or session_id != session_id.strip():
                raise ValueError("session_id must be non-empty and normalized")
            command.extend(["--resume", session_id])
        return command

    def build_env(
        self, route: RuntimeRoute, *, command: list[str] | None = None
    ) -> dict[str, str]:
        configured = self._configured_route(route)
        secret = self.config.secret_for(configured.name)
        if secret is None or not secret.get_secret_value():
            raise ValueError("claude_api credential is missing")
        env = _safe_child_environment(dict(os.environ))
        env["ANTHROPIC_API_KEY"] = secret.get_secret_value()
        # Prevent Claude from consulting the caller's ~/.claude state.  Each
        # invocation receives only the service-owned settings and MCP config.
        env["CLAUDE_CONFIG_DIR"] = self._runtime_root.name
        if command is not None:
            try:
                mcp_path = str(
                    Path(command[command.index("--mcp-config") + 1]).resolve()
                )
            except (ValueError, IndexError):
                raise ValueError("Claude invocation MCP config is missing") from None
            with self._lock:
                required_env_names = self._invocation_env_names.get(mcp_path)
            if required_env_names is None:
                raise ValueError("Claude invocation MCP config is not adapter-owned")
            for name in required_env_names:
                value = os.environ.get(name)
                if not isinstance(value, str) or not value:
                    raise ValueError("Claude reviewed MCP environment is missing")
                env[name] = value
        return env

    def new_event_normalizer(
        self, *, expected_session_id: str | None = None
    ) -> ClaudeEventNormalizer:
        owner = object()
        return ClaudeEventNormalizer(
            effect_registry=self.effects,
            native_cli_classifier=self.native_cli,
            expected_session_id=expected_session_id,
            owner=owner,
            proof_issuer=self._issue_terminal_proof,
        )

    def parse_final_result(
        self,
        *,
        normalizer: ClaudeEventNormalizer,
        proof: ClaudeTerminalProof,
        parser: Callable[[str], ResultT],
    ) -> ResultT:
        if not isinstance(normalizer, ClaudeEventNormalizer) or not isinstance(
            proof, ClaudeTerminalProof
        ):
            raise ClaudeRuntimeResultError(
                _result_failure("claude_result_incomplete")
            )
        with self._lock:
            pending = self._pending_proofs.get(normalizer._owner)
            if (
                pending is None
                or pending[0] is not proof
                or pending[1] != proof.nonce
            ):
                raise ClaudeRuntimeResultError(
                    _result_failure("claude_result_incomplete")
                )
            del self._pending_proofs[normalizer._owner]
        try:
            return parser(proof.result)
        except Exception as exc:
            raise ClaudeRuntimeResultError(
                _result_failure("claude_result_validation_failed")
            ) from exc

    def _issue_terminal_proof(
        self, owner: object, result: str, session_id: str
    ) -> ClaudeTerminalProof:
        proof = ClaudeTerminalProof(
            result=result,
            session_id=session_id,
            nonce=uuid.uuid4().hex,
        )
        with self._lock:
            if owner in self._pending_proofs:
                raise ClaudeEventPolicyError("claude_result_duplicate")
            self._pending_proofs[owner] = (proof, proof.nonce)
        return proof

    def classify_failure(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
        *,
        timed_out: bool = False,
        timeout_kind: str = "",
    ) -> RuntimeFailure:
        if timed_out:
            code = {"idle": "claude_idle_timeout", "total": "claude_total_timeout"}.get(
                timeout_kind, "claude_transport_timeout"
            )
            return _transport_failure(code)
        structured_subtypes = _trusted_error_subtypes(stdout)
        failure_text = stderr[:16384].casefold()
        if any(
            marker in failure_text
            for marker in (
                "authentication_error", "invalid x-api-key", "invalid api key",
                "unauthorized",
            )
        ):
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                code="claude_authentication_failed",
                detail="Claude provider authentication failed.",
                failover_permitted=True,
                route_pause_required=True,
            )
        if any(
            marker in failure_text
            for marker in ("rate_limit_error", "overloaded", "status 429")
        ):
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.CAPACITY,
                code="claude_capacity_unavailable",
                detail="Claude provider capacity is unavailable.",
                failover_permitted=True,
                route_pause_required=True,
            )
        if any(
            marker in failure_text
            for marker in (
                "connection reset", "connection refused", "network error",
                "stream disconnected",
            )
        ):
            return _transport_failure("claude_transport_failed")
        if any(
            marker in failure_text
            for marker in ("session not found", "invalid session", "resume session")
        ):
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.SESSION,
                code="claude_session_invalid",
                detail="Claude session evidence is invalid or unavailable.",
            )
        if "error_max_turns" in structured_subtypes or "error_max_turns" in failure_text:
            return _result_failure("claude_result_incomplete")
        return RuntimeFailure(
            failure_class=RuntimeFailureClass.UNCLASSIFIED,
            code="claude_runtime_unclassified",
            detail=(
                "Claude exited without a classified runtime failure."
                if returncode != 0
                else "Claude completed without a classified runtime result."
            ),
        )

    def _write_invocation_boundary(
        self, policy: ClaudeCommandPolicy
    ) -> tuple[Path, Path]:
        root = Path(self._runtime_root.name)
        invocation_id = uuid.uuid4().hex
        policy_path = root / f"broker-{invocation_id}.json"
        settings_path = root / f"settings-{invocation_id}.json"
        mcp_path = root / f"mcp-{invocation_id}.json"
        policy_path.write_text(
            json.dumps(
                {
                    "allowed_mcp_tools": list(policy.mcp_tools),
                    "allow_native_cli": policy.allow_native_cli,
                }, sort_keys=True, separators=(",", ":"),
            ), encoding="utf-8",
        )
        denied_builtins = sorted(
            _BUILTIN_TOOLS
            - ({"Bash"} if policy.allow_native_cli else set())
        )
        broker_enabled = bool(policy.mcp_tools or policy.allow_native_cli)
        reviewed_transports, required_env_names = self._reviewed_mcp_transports(
            policy
        )
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {"allow": [], "deny": denied_builtins},
                    "enableAllProjectMcpServers": False,
                    "enabledMcpjsonServers": (
                        [
                            *(["ceo_runtime_permission"] if broker_enabled else []),
                            *sorted(reviewed_transports),
                        ]
                    ),
                }, sort_keys=True, separators=(",", ":"),
            ), encoding="utf-8",
        )
        mcp_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        **(
                            {
                            "ceo_runtime_permission": {
                                "type": "stdio",
                                "command": sys.executable,
                                "args": [
                                    "-m",
                                    "app.claude_permission_broker",
                                    "--policy",
                                    str(policy_path),
                                ],
                                "env": {},
                            }
                            }
                            if broker_enabled
                            else {}
                        ),
                        **reviewed_transports,
                    }
                },
                sort_keys=True,
                separators=(",", ":"),
            ), encoding="utf-8",
        )
        with self._lock:
            self._invocation_env_names[str(mcp_path.resolve())] = required_env_names
        return settings_path, mcp_path

    def _reviewed_mcp_transports(
        self, policy: ClaudeCommandPolicy
    ) -> tuple[dict[str, dict[str, object]], frozenset[str]]:
        if not policy.mcp_tools:
            return {}, frozenset()
        reviewed = self.effects.reviewed_tools()
        required_servers: set[str] = set()
        for exact_name in policy.mcp_tools:
            _, server, tool = exact_name.split("__", 2)
            if tool not in reviewed.get(server, ()):
                raise ValueError("Claude policy requires a reviewed MCP tool")
            required_servers.add(server)
        configured = {
            server.name: server for server in load_service_mcp_servers(env=os.environ)
        }
        missing = required_servers - configured.keys()
        if missing:
            raise ValueError("Claude reviewed MCP transport is missing")
        selected = [configured[name] for name in sorted(required_servers)]
        if any(server.args_env is not None for server in selected):
            raise ValueError("Claude reviewed MCP args_env is not safely supported")
        required_env_names = frozenset(
            name
            for server in selected
            for name in (
                *((server.bearer_token_env_var,) if server.bearer_token_env_var else ()),
                *(env_name for _, env_name in server.env_http_headers),
            )
        )
        return (
            {server.name: _claude_mcp_transport(server) for server in selected},
            required_env_names,
        )

    def _configured_route(self, route: RuntimeRoute) -> RuntimeRoute:
        if (
            route.name != "claude_api"
            or route.runtime_kind is not RuntimeKind.CLAUDE_CLI
            or route.credential_mode is not CredentialMode.SERVICE_API
        ):
            raise ValueError("unsupported runtime route")
        configured = next(
            (candidate for candidate in self.config.routes if candidate.name == route.name),
            None,
        )
        if configured != route:
            raise ValueError("runtime route is not configured")
        return configured


class ClaudeEventNormalizer:
    """Invocation-scoped strict Claude stream state machine."""

    def __init__(
        self,
        *,
        effect_registry: McpToolEffectRegistry,
        native_cli_classifier: NativeCliMetadataClassifier,
        expected_session_id: str | None,
        owner: object,
        proof_issuer: Callable[[object, str, str], ClaudeTerminalProof],
    ) -> None:
        if expected_session_id is not None and _required_string(expected_session_id) is None:
            raise ValueError("expected_session_id must be normalized")
        self._effects = effect_registry
        self._native_cli = native_cli_classifier
        self._expected_session_id = expected_session_id
        self._owner = owner
        self._proof_issuer = proof_issuer
        self._session_id: str | None = None
        self._init_seen = False
        self._final_seen = False
        self._failed = False
        self._terminal_proof: ClaudeTerminalProof | None = None
        self._started_items: dict[str, dict[str, object]] = {}
        self._seen_call_ids: set[str] = set()
        self._mcp_tool_names = {
            f"mcp__{server}__{tool}": (server, tool)
            for server, tools in self._effects.reviewed_tools().items()
            for tool in tools
        }

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def normalize_event(self, event: dict[str, object]) -> dict[str, object]:
        normalized = self.normalize_events(event)
        if len(normalized) != 1:
            raise ClaudeEventPolicyError("claude_event_requires_single_item")
        return normalized[0]

    def normalize_events(
        self, event: dict[str, object]
    ) -> tuple[dict[str, object], ...]:
        if self._failed:
            raise ClaudeEventPolicyError("claude_invocation_failed")
        if self._final_seen:
            raise ClaudeEventPolicyError("claude_event_after_result")
        snapshot = (
            self._session_id,
            self._init_seen,
            self._final_seen,
            dict(self._started_items),
            set(self._seen_call_ids),
            self._terminal_proof,
        )
        try:
            return self._normalize_events_unchecked(event)
        except (ClaudeEventPolicyError, ClaudeRuntimeResultError) as exc:
            (
                self._session_id,
                self._init_seen,
                self._final_seen,
                self._started_items,
                self._seen_call_ids,
                self._terminal_proof,
            ) = snapshot
            self._failed = True
            if isinstance(exc, ClaudeRuntimeResultError):
                raise ClaudeEventPolicyError(exc.failure.code) from exc
            raise

    def _normalize_events_unchecked(
        self, event: dict[str, object]
    ) -> tuple[dict[str, object], ...]:
        event_type = event.get("type")
        session_id = _required_string(event.get("session_id"))
        if event_type == "system" and event.get("subtype") == "init":
            if self._init_seen:
                raise ClaudeEventPolicyError("claude_init_duplicate")
            if session_id is None:
                raise ClaudeEventPolicyError("claude_session_evidence_missing")
            if self._expected_session_id is not None and session_id != self._expected_session_id:
                raise ClaudeEventPolicyError("claude_session_mismatch")
            self._session_id = session_id
            self._init_seen = True
            return ({"type": RuntimeEventType.TURN_STARTED.value, "session_id": session_id},)
        self._require_active_session(session_id)
        if event_type in {"assistant", "user"}:
            message = event.get("message")
            if (
                not isinstance(message, dict)
                or message.get("role") != event_type
                or not isinstance(message.get("content"), list)
                or not message["content"]
            ):
                raise ClaudeEventPolicyError("claude_event_unrecognized")
            events: list[dict[str, object]] = []
            for block in message["content"]:
                if (
                    event_type == "assistant"
                    and isinstance(block, dict)
                    and block.get("type") in {"thinking", "redacted_thinking"}
                ):
                    continue
                events.append(
                    self._normalize_assistant_block(block)
                    if event_type == "assistant"
                    else self._normalize_user_block(block)
                )
            return tuple(events)
        if event_type == "result":
            raw = _validated_success_result(event)
            if self._started_items:
                raise ClaudeEventPolicyError("claude_open_tool_items")
            self._final_seen = True
            assert self._session_id is not None
            self._terminal_proof = self._proof_issuer(
                self._owner,
                raw,
                self._session_id,
            )
            return ({"type": RuntimeEventType.TURN_COMPLETED.value, "session_id": self._session_id, "result": raw},)
        raise ClaudeEventPolicyError("claude_event_unrecognized")

    def finalize(self) -> None:
        if self._failed:
            raise ClaudeEventPolicyError("claude_invocation_failed")
        if not self._init_seen:
            raise ClaudeEventPolicyError("claude_session_evidence_missing")
        if self._started_items:
            raise ClaudeEventPolicyError("claude_open_tool_items")
        if not self._final_seen:
            raise ClaudeEventPolicyError("claude_result_incomplete")

    def terminal_proof(self) -> ClaudeTerminalProof:
        if self._failed or not self._final_seen or self._terminal_proof is None:
            raise ClaudeRuntimeResultError(
                _result_failure("claude_result_incomplete")
            )
        return self._terminal_proof

    def _require_active_session(self, session_id: str | None) -> None:
        if not self._init_seen:
            raise ClaudeEventPolicyError("claude_init_missing")
        if session_id is None:
            raise ClaudeEventPolicyError("claude_session_evidence_missing")
        if session_id != self._session_id:
            raise ClaudeEventPolicyError("claude_session_mismatch")

    def _normalize_assistant_block(self, block: object) -> dict[str, object]:
        if not isinstance(block, dict):
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return {"type": RuntimeEventType.ITEM_COMPLETED.value, "item": {"type": "agent_message", "text": block["text"]}}
        if block.get("type") != "tool_use":
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        call_id = _required_string(block.get("id"))
        tool_name = _required_string(block.get("name"))
        arguments = block.get("input")
        if call_id is None or tool_name is None or not isinstance(arguments, dict):
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        if call_id in self._seen_call_ids:
            raise ClaudeEventPolicyError("claude_tool_id_duplicate")
        item = self._reviewed_tool_item(call_id, tool_name, arguments)
        self._seen_call_ids.add(call_id)
        self._started_items[call_id] = item
        return {"type": RuntimeEventType.ITEM_STARTED.value, "item": item}

    def _reviewed_tool_item(self, call_id: str, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        mcp_identity = self._mcp_tool_names.get(tool_name)
        if mcp_identity is not None:
            server, tool = mcp_identity
            call = self._effects.classify({"type": "mcp_tool_call", "server": server, "tool": tool, "arguments": arguments})
            if call is None:
                raise ClaudeEventPolicyError("claude_tool_unreviewed")
            return {
                "type": "mcp_tool_call", "id": call_id, "status": "in_progress",
                "server": server, "tool": tool, "arguments": arguments,
                "metadata": {"effect": call.effect.value, "capability": call.server, "operation": call.operation, "operation_digest": call.operation_digest, "target_identifiers": call.target_identifiers},
            }
        if tool_name == "Bash":
            command = arguments.get("command")
            if not isinstance(command, str):
                raise ClaudeEventPolicyError("claude_tool_unreviewed")
            reviewed = self._native_cli.classify({"type": "command_execution", "command": command})
            if reviewed is None or reviewed.effect is None:
                raise ClaudeEventPolicyError("claude_tool_unreviewed")
            return {
                "type": "command_execution", "id": call_id, "status": "in_progress", "command": command,
                "metadata": {"effect": reviewed.effect.value, "capability": f"agent_cli.{reviewed.cli}", "operation": reviewed.command_path, "operation_digest": reviewed.command_digest, "target_identifiers": reviewed.target_identifiers, "native_cli": reviewed.cli},
            }
        raise ClaudeEventPolicyError("claude_tool_unreviewed")

    def _normalize_user_block(self, block: object) -> dict[str, object]:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        call_id = _required_string(block.get("tool_use_id"))
        is_error = block.get("is_error", False)
        if call_id is None or not isinstance(is_error, bool):
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        started = self._started_items.pop(call_id, None)
        if started is None:
            raise ClaudeEventPolicyError("claude_tool_result_without_start")
        item = dict(started)
        item["status"] = "failed" if is_error else "completed"
        item["result"] = block.get("content")
        return {"type": RuntimeEventType.ITEM_FAILED.value if is_error else RuntimeEventType.ITEM_COMPLETED.value, "item": item}


def _claude_mcp_transport(server: ServiceMcpServer) -> dict[str, object]:
    if server.command is not None:
        return {
            "type": "stdio",
            "command": server.command,
            "args": list(server.args),
        }
    if server.url is None:
        raise ValueError("Claude reviewed MCP transport is incomplete")
    headers = dict(server.http_headers)
    headers.update(
        {name: f"${{{env_name}}}" for name, env_name in server.env_http_headers}
    )
    if server.bearer_token_env_var is not None:
        headers["Authorization"] = f"Bearer ${{{server.bearer_token_env_var}}}"
    transport: dict[str, object] = {"type": "http", "url": server.url}
    if headers:
        transport["headers"] = headers
    return transport


def _required_string(value: object) -> str | None:
    return value if isinstance(value, str) and value and value == value.strip() else None


def _validated_success_result(event: dict[str, object]) -> str:
    if (
        event.get("type") != "result"
        or event.get("subtype") != "success"
        or event.get("is_error") is not False
        or _required_string(event.get("session_id")) is None
        or not isinstance(event.get("result"), str)
    ):
        raise ClaudeRuntimeResultError(_result_failure("claude_result_incomplete"))
    return event["result"]


def _trusted_error_subtypes(stdout: str) -> frozenset[str]:
    subtypes = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        if (
            isinstance(event, dict)
            and event.get("type") == "result"
            and event.get("is_error") is True
            and event.get("subtype") in {"error_max_turns", "error_during_execution"}
        ):
            subtypes.add(str(event["subtype"]))
    return frozenset(subtypes)


def _transport_failure(code: str) -> RuntimeFailure:
    return RuntimeFailure(
        failure_class=RuntimeFailureClass.TRANSPORT, code=code,
        detail="Claude transport failed before a complete bounded result.",
        retryable_on_same_route=True, failover_permitted=True, route_pause_required=True,
    )


def _result_failure(code: str) -> RuntimeFailure:
    return RuntimeFailure(
        failure_class=RuntimeFailureClass.RESULT, code=code,
        detail="Claude did not return a valid caller result.",
    )
