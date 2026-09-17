"""Claude CLI transport, credential, session, event, and result adapter."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TypeVar

from app.agent_runtime_config import (
    AgentRuntimeConfig,
    SUPPORTED_RUNTIME_REASONING_EFFORTS,
)
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeEventType,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.codex_runtime_adapter import _safe_child_environment
from app.service_codex_config import ServiceMcpServer, load_service_mcp_servers

ResultT = TypeVar("ResultT")
_POLICY_SEAL = object()
# Provider telemetry the Claude transport emits around a turn: the
# subscription quota window (which can precede session init) and the
# extended-thinking budget notice raised by --effort.  Neither carries a turn
# item, and the terminal result still decides the outcome, so both map to no
# runtime event.  Every other event shape stays a grammar violation.
# The events a turn is read from. Everything else the CLI streams (telemetry,
# status, hooks, new event kinds) is skipped, as the native CLI's own consumers
# do: an event we do not use is not a reason to fail the turn.
_TURN_EVENT_TYPES = frozenset({"assistant", "user", "result"})


class ClaudeEventPolicyError(RuntimeError):
    """A Claude event violates the transport/session event grammar."""


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
    """Choose a normal runtime turn or an isolated no-tool health probe."""

    tools_enabled: bool
    _seal: object

    def __init__(self, *, tools_enabled: bool, seal: object) -> None:
        if seal is not _POLICY_SEAL:
            raise ValueError("Claude command policies use named constructors")
        object.__setattr__(self, "tools_enabled", tools_enabled)
        object.__setattr__(self, "_seal", seal)

    @classmethod
    def no_tools(cls) -> ClaudeCommandPolicy:
        return cls(tools_enabled=False, seal=_POLICY_SEAL)

    @classmethod
    def normal(cls) -> ClaudeCommandPolicy:
        return cls(tools_enabled=True, seal=_POLICY_SEAL)


CLAUDE_INPUT_MAX_BYTES = 1024 * 1024
# Claude requires an explicit turn budget; Codex has none, and the shared
# total/idle timeouts are what actually bound a turn. One turn cannot finish
# any work that calls a tool: the run ends on `stop_reason: tool_use` and the
# result never arrives, which is how every Claude turn failed before this.
CLAUDE_MAX_TURNS_PER_INVOCATION = 64


class ClaudeInputTooLargeError(ValueError):
    """The turn's text exceeds what one Claude invocation accepts."""

    code = "claude_input_contract_too_large"


def claude_input_contract(*, prompt: str, developer_instructions: str) -> str:
    """The single message a Claude turn receives, for every caller.

    Claude has no separate developer-instruction channel, so the instructions
    and the task travel in one message under fixed tags.
    """
    payload = (
        "<developer-instructions>\n"
        f"{developer_instructions}\n"
        "</developer-instructions>\n"
        "<task>\n"
        f"{prompt}\n"
        "</task>"
    )
    if len(payload.encode("utf-8")) > CLAUDE_INPUT_MAX_BYTES:
        raise ClaudeInputTooLargeError(ClaudeInputTooLargeError.code)
    return payload


def require_claude_session_id(session_id: str) -> str:
    """Return one CLI-safe, normalized Claude conversation session ID."""
    if not isinstance(session_id, str):
        raise TypeError("Claude session_id must be a string")
    if (
        not session_id
        or session_id != session_id.strip()
        or len(session_id) > 256
        or session_id.startswith("-")
        or not session_id.isprintable()
        or any(character.isspace() for character in session_id)
    ):
        raise ValueError("Claude session_id must be normalized and CLI-safe")
    return session_id


def _mcp_transport(
    server: ServiceMcpServer, source_env: Mapping[str, str]
) -> dict[str, object]:
    """Describe one service MCP server to Claude the way it is configured.

    Servers are connected directly. Claude-side OAuth (memory_connector) checks
    that the protected resource named in the server's metadata is the URL it
    connected to, so anything in between breaks it.
    """
    if server.args_env is not None:
        raise ValueError("Claude MCP args_env is not supported")
    if server.command is not None:
        return {"type": "stdio", "command": server.command, "args": list(server.args)}
    if server.url is None:
        raise ValueError("Claude MCP transport is incomplete")
    headers = dict(server.http_headers)
    for header, env_name in server.env_http_headers:
        headers[header] = _required_mcp_secret(source_env, env_name)
    if server.bearer_token_env_var is not None:
        headers["Authorization"] = "Bearer " + _required_mcp_secret(
            source_env, server.bearer_token_env_var
        )
    transport: dict[str, object] = {"type": "http", "url": server.url}
    if headers:
        transport["headers"] = headers
    return transport


def _required_mcp_secret(source_env: Mapping[str, str], name: str) -> str:
    value = source_env.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"Claude MCP credential {name} is not configured")
    return value


class ClaudeRuntimeAdapter:
    """Build isolated non-interactive Claude invocations for one route."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: AgentRuntimeConfig,
        claude_bin: str = "claude",
        service_mcp_servers: tuple[ServiceMcpServer, ...] | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.claude_bin = claude_bin
        self._service_mcp_servers = service_mcp_servers
        self._lock = RLock()
        self._pending_proofs: dict[object, tuple[ClaudeTerminalProof, str]] = {}

    def build_command(
        self,
        *,
        route: RuntimeRoute,
        session_id: str | None,
        max_turns: int,
        policy: ClaudeCommandPolicy | None = None,
        reasoning_effort: str | None = None,
    ) -> list[str]:
        configured = self._configured_route(route)
        effort = reasoning_effort or self.config.claude_reasoning_effort
        if effort not in SUPPORTED_RUNTIME_REASONING_EFFORTS:
            raise ValueError("Claude reasoning effort is unsupported")
        selected_policy = policy or ClaudeCommandPolicy.normal()
        if not isinstance(selected_policy, ClaudeCommandPolicy):
            raise ValueError("Claude command policy is invalid")  # noqa: TRY004
        if (
            isinstance(max_turns, bool)
            or not isinstance(max_turns, int)
            or max_turns <= 0
        ):
            raise ValueError("max_turns must be a positive integer")
        if session_id is not None:
            require_claude_session_id(session_id)
        settings_json, mcp_json = self._invocation_boundary(selected_policy)
        command = [self.claude_bin, "-p"]
        if configured.credential_mode is CredentialMode.SERVICE_API:
            # --bare reads Anthropic auth strictly from ANTHROPIC_API_KEY.  The
            # local-OAuth route must reach the keychain credential instead, so
            # it relies on --setting-sources/--strict-mcp-config alone to keep
            # the caller's CLAUDE.md, skills, plugins, and hooks out of the run.
            command.append("--bare")
        command.extend(
            [
                "--setting-sources",
                "",
                "--settings",
                settings_json,
                "--strict-mcp-config",
                "--mcp-config",
                mcp_json,
                "--input-format",
                "text",
                "--output-format",
                "stream-json",
                "--model",
                configured.model,
                "--effort",
                effort,
                "--max-turns",
                str(max_turns),
                "--verbose",
                "--permission-mode",
                "default",
            ]
        )
        if not selected_policy.tools_enabled:
            command.extend(["--tools", ""])
        if session_id is not None:
            command.extend(["--resume", session_id])
        return command

    def build_env(self, route: RuntimeRoute) -> dict[str, str]:
        configured = self._configured_route(route)
        env = _safe_child_environment(dict(os.environ))
        if configured.credential_mode is CredentialMode.SERVICE_API:
            secret = self.config.secret_for(configured.name)
            if secret is None or not secret.get_secret_value():
                raise ValueError("claude_api credential is missing")
            env["ANTHROPIC_API_KEY"] = secret.get_secret_value()
            # --bare (see build_command) already forces auth to come from
            # ANTHROPIC_API_KEY alone, so CLAUDE_CONFIG_DIR does not need to
            # move: the caller's ~/.claude credentials are never consulted
            # regardless of where session/config state lives.
        return env

    def new_event_normalizer(
        self,
        *,
        expected_session_id: str | None = None,
    ) -> ClaudeEventNormalizer:
        owner = object()
        return ClaudeEventNormalizer(
            expected_session_id=expected_session_id,
            owner=owner,
            proof_issuer=self._issue_terminal_proof,
            cleanup_owner=self._discard_owner,
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
            raise ClaudeRuntimeResultError(_result_failure("claude_result_incomplete"))
        with self._lock:
            pending = self._pending_proofs.get(normalizer._owner)
            if pending is None or pending[0] is not proof or pending[1] != proof.nonce:
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
        finally:
            self._discard_owner(normalizer._owner)

    def _discard_owner(self, owner: object) -> None:
        """Forget a finished stream's unclaimed terminal proof."""
        with self._lock:
            self._pending_proofs.pop(owner, None)

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
        # The provider reports an expired login in its terminal result rather
        # than on stderr, so both surfaces are scanned.
        failure_text = (
            stderr[:16384] + "\n" + _trusted_error_result_text(stdout)[:4096]
        ).casefold()
        if any(
            marker in failure_text
            for marker in (
                "oauth session expired",
                "not logged in",
                "please run /login",
                # A credential that exists but may not run inference: seen live
                # when another local process replaced the stored token with one
                # whose scopes exclude user:inference.
                "does not meet scope requirement",
            )
        ):
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                code="claude_credentials_unavailable",
                detail=(
                    "The local Claude credential is unusable. The quota guard "
                    "refills an access token from the auth pool every 15 "
                    "minutes; more than four consecutive failures (about an "
                    "hour) needs the account owner to sign in again."
                ),
                failover_permitted=True,
                route_pause_required=True,
            )
        if any(
            marker in failure_text
            for marker in (
                "authentication_error",
                "invalid x-api-key",
                "invalid api key",
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
                "connection reset",
                "connection refused",
                "network error",
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
        if (
            "error_max_turns" in structured_subtypes
            or "error_max_turns" in failure_text
        ):
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

    def _invocation_boundary(self, policy: ClaudeCommandPolicy) -> tuple[str, str]:
        """The settings and MCP servers for one turn, passed to the CLI inline.

        The CLI takes both as JSON strings, so nothing is written to disk and
        nothing has to be cleaned up after the turn.
        """
        transports = self._mcp_transports() if policy.tools_enabled else {}
        settings_json = json.dumps(
            {
                "enableAllProjectMcpServers": policy.tools_enabled,
                "enabledMcpjsonServers": sorted(transports),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        mcp_json = json.dumps(
            {"mcpServers": transports}, sort_keys=True, separators=(",", ":")
        )
        return settings_json, mcp_json

    def _mcp_transports(self) -> dict[str, dict[str, object]]:
        configured = (
            self._service_mcp_servers
            if self._service_mcp_servers is not None
            else load_service_mcp_servers(env=os.environ)
        )
        return {server.name: _mcp_transport(server, os.environ) for server in configured}

    _CREDENTIAL_MODES = {
        "claude_oauth": CredentialMode.LOCAL_OAUTH,
        "claude_api": CredentialMode.SERVICE_API,
    }

    def _configured_route(self, route: RuntimeRoute) -> RuntimeRoute:
        if (
            route.runtime_kind is not RuntimeKind.CLAUDE_CLI
            or self._CREDENTIAL_MODES.get(route.name) is not route.credential_mode
        ):
            raise ValueError("unsupported runtime route")
        configured = next(
            (
                candidate
                for candidate in self.config.routes
                if candidate.name == route.name
            ),
            None,
        )
        if configured != route:
            raise ValueError("runtime route is not configured")
        return configured


class ClaudeEventNormalizer:
    """Invocation-scoped Claude stream state machine."""

    def __init__(
        self,
        *,
        expected_session_id: str | None,
        owner: object,
        proof_issuer: Callable[[object, str, str], ClaudeTerminalProof],
        cleanup_owner: Callable[[object], None],
    ) -> None:
        if (
            expected_session_id is not None
            and _required_string(expected_session_id) is None
        ):
            raise ValueError("expected_session_id must be normalized")
        self._expected_session_id = expected_session_id
        self._owner = owner
        self._proof_issuer = proof_issuer
        self._cleanup_owner = cleanup_owner
        self._cleanup_done = False
        self._session_id: str | None = None
        self._init_seen = False
        self._final_seen = False
        self._failed = False
        self._terminal_proof: ClaudeTerminalProof | None = None
        self._started_items: dict[str, dict[str, object]] = {}
        self._seen_call_ids: set[str] = set()

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
            self._cleanup()
            raise ClaudeEventPolicyError("claude_invocation_failed")
        if self._final_seen:
            self._cleanup()
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
            self._cleanup()
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
            if (
                self._expected_session_id is not None
                and session_id != self._expected_session_id
            ):
                raise ClaudeEventPolicyError("claude_session_mismatch")
            self._session_id = session_id
            self._init_seen = True
            return (
                {"type": RuntimeEventType.TURN_STARTED.value, "session_id": session_id},
            )
        if event_type not in _TURN_EVENT_TYPES:
            return ()
        self._require_active_session(session_id)
        if event_type in {"assistant", "user"}:
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                return ()
            events: list[dict[str, object]] = []
            for block in content:
                normalized = (
                    self._normalize_assistant_block(block)
                    if event_type == "assistant"
                    else self._normalize_user_block(block)
                )
                if normalized is not None:
                    events.append(normalized)
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
            return (
                {
                    "type": RuntimeEventType.TURN_COMPLETED.value,
                    "session_id": self._session_id,
                    "result": raw,
                },
            )
        return ()

    def finalize(self) -> None:
        if self._failed:
            self._cleanup()
            raise ClaudeEventPolicyError("claude_invocation_failed")
        if not self._init_seen:
            self._cleanup()
            raise ClaudeEventPolicyError("claude_session_evidence_missing")
        if self._started_items:
            self._cleanup()
            raise ClaudeEventPolicyError("claude_open_tool_items")
        if not self._final_seen:
            self._cleanup()
            raise ClaudeEventPolicyError("claude_result_incomplete")

    def _cleanup(self) -> None:
        if not self._cleanup_done:
            self._cleanup_done = True
            self._cleanup_owner(self._owner)

    def terminal_proof(self) -> ClaudeTerminalProof:
        if self._failed or not self._final_seen or self._terminal_proof is None:
            raise ClaudeRuntimeResultError(_result_failure("claude_result_incomplete"))
        return self._terminal_proof

    def _require_active_session(self, session_id: str | None) -> None:
        if not self._init_seen:
            raise ClaudeEventPolicyError("claude_init_missing")
        if session_id is None:
            raise ClaudeEventPolicyError("claude_session_evidence_missing")
        if session_id != self._session_id:
            raise ClaudeEventPolicyError("claude_session_mismatch")

    def _normalize_assistant_block(self, block: object) -> dict[str, object] | None:
        if not isinstance(block, dict):
            return None
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return {
                "type": RuntimeEventType.ITEM_COMPLETED.value,
                "item": {"type": "agent_message", "text": block["text"]},
            }
        if block.get("type") != "tool_use":
            # Thinking, server-side tools and new block kinds carry nothing the
            # turn is read from.
            return None
        call_id = _required_string(block.get("id"))
        tool_name = _required_string(block.get("name"))
        arguments = block.get("input")
        if call_id is None or tool_name is None or not isinstance(arguments, dict):
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        if call_id in self._seen_call_ids:
            raise ClaudeEventPolicyError("claude_tool_id_duplicate")
        item = self._tool_item(call_id, tool_name)
        self._seen_call_ids.add(call_id)
        self._started_items[call_id] = item
        return {"type": RuntimeEventType.ITEM_STARTED.value, "item": item}

    def _tool_item(self, call_id: str, tool_name: str) -> dict[str, object]:
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__", 2)
            if len(parts) == 3 and parts[1] and parts[2]:
                _, server, tool = parts
                return {
                    "type": "mcp_tool_call",
                    "id": call_id,
                    "status": "in_progress",
                    "server": server,
                    "tool": tool,
                }
        if tool_name == "Bash":
            return {
                "type": "command_execution",
                "id": call_id,
                "status": "in_progress",
                "tool": tool_name,
            }
        return {
            "type": "provider_tool_call",
            "id": call_id,
            "status": "in_progress",
            "tool": tool_name,
        }

    def _normalize_user_block(self, block: object) -> dict[str, object] | None:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            return None
        call_id = _required_string(block.get("tool_use_id"))
        is_error = block.get("is_error", False)
        if call_id is None or not isinstance(is_error, bool):
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        started = self._started_items.pop(call_id, None)
        if started is None:
            raise ClaudeEventPolicyError("claude_tool_result_without_start")
        item = dict(started)
        item["status"] = "failed" if is_error else "completed"
        return {
            "type": RuntimeEventType.ITEM_FAILED.value
            if is_error
            else RuntimeEventType.ITEM_COMPLETED.value,
            "item": item,
        }


def _required_string(value: object) -> str | None:
    return (
        value if isinstance(value, str) and value and value == value.strip() else None
    )


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


def _trusted_error_result_text(stdout: str) -> str:
    """The provider's own message when it marked its terminal result an error.

    An unusable credential arrives this way rather than on stderr: the result
    event carries `is_error: true` while its subtype is still `success`, so
    neither the stderr scan nor `_trusted_error_subtypes` sees it, and an
    actionable configuration failure was filed as unclassified.
    """
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        if (
            isinstance(event, dict)
            and event.get("type") == "result"
            and event.get("is_error") is True
            and isinstance(event.get("result"), str)
        ):
            return str(event["result"])
    return ""


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
        failure_class=RuntimeFailureClass.TRANSPORT,
        code=code,
        detail="Claude transport failed before a complete bounded result.",
        retryable_on_same_route=True,
        failover_permitted=True,
        route_pause_required=True,
    )


def _result_failure(code: str) -> RuntimeFailure:
    return RuntimeFailure(
        failure_class=RuntimeFailureClass.RESULT,
        code=code,
        detail="Claude did not return a valid caller result.",
    )
