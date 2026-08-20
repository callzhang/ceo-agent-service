"""Strict command and credential boundary for the Claude CLI runtime."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
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

ResultT = TypeVar("ResultT")


class ClaudeEventPolicyError(RuntimeError):
    """A Claude event cannot be proven safe under the reviewed event grammar."""


class ClaudeRuntimeResultError(RuntimeError):
    def __init__(self, failure: RuntimeFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)


class ClaudeRuntimeAdapter:
    """Build bounded non-interactive Claude invocations for one configured route."""

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
        self._started_items: dict[str, dict[str, object]] = {}
        self._mcp_tool_names = {
            f"mcp__{server}__{tool}": (server, tool)
            for server, tools in self.effects.reviewed_tools().items()
            for tool in tools
        }

    def build_command(
        self,
        *,
        route: RuntimeRoute,
        session_id: str | None,
        max_turns: int,
    ) -> list[str]:
        configured = self._configured_route(route)
        if isinstance(max_turns, bool) or max_turns <= 0:
            raise ValueError("max_turns must be a positive integer")
        command = [
            self.claude_bin,
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--model",
            configured.model,
            "--max-turns",
            str(max_turns),
            "--verbose",
        ]
        if session_id is not None:
            if not session_id.strip() or session_id != session_id.strip():
                raise ValueError("session_id must be non-empty and normalized")
            command.extend(["--resume", session_id])
        return command

    def build_env(self, route: RuntimeRoute) -> dict[str, str]:
        configured = self._configured_route(route)
        secret = self.config.secret_for(configured.name)
        if secret is None or not secret.get_secret_value():
            raise ValueError("claude_api credential is missing")
        env = _safe_child_environment(dict(os.environ))
        env["ANTHROPIC_API_KEY"] = secret.get_secret_value()
        return env

    def normalize_event(self, event: dict[str, object]) -> dict[str, object]:
        normalized = self.normalize_events(event)
        if len(normalized) != 1:
            raise ClaudeEventPolicyError("claude_event_requires_single_item")
        return normalized[0]

    def normalize_events(
        self, event: dict[str, object]
    ) -> tuple[dict[str, object], ...]:
        event_type = event.get("type")
        session_id = _required_string(event.get("session_id"))
        if event_type == "system" and event.get("subtype") == "init":
            if session_id is None:
                raise ClaudeEventPolicyError("claude_session_evidence_missing")
            return (
                {
                    "type": RuntimeEventType.TURN_STARTED.value,
                    "session_id": session_id,
                },
            )
        if event_type in {"assistant", "user"}:
            if session_id is None:
                raise ClaudeEventPolicyError("claude_session_evidence_missing")
            message = event.get("message")
            expected_role = event_type
            if (
                not isinstance(message, dict)
                or message.get("role") != expected_role
                or not isinstance(message.get("content"), list)
                or not message["content"]
            ):
                raise ClaudeEventPolicyError("claude_event_unrecognized")
            normalizer = (
                self._normalize_assistant_block
                if event_type == "assistant"
                else self._normalize_user_block
            )
            return tuple(normalizer(block) for block in message["content"])
        if event_type == "result":
            if (
                event.get("subtype") != "success"
                or event.get("is_error") is not False
                or session_id is None
                or not isinstance(event.get("result"), str)
            ):
                raise ClaudeEventPolicyError("claude_result_incomplete")
            return (
                {
                    "type": RuntimeEventType.TURN_COMPLETED.value,
                    "session_id": session_id,
                    "result": event["result"],
                },
            )
        raise ClaudeEventPolicyError("claude_event_unrecognized")

    def parse_final_result(
        self,
        event: dict[str, object],
        parser: Callable[[str], ResultT],
    ) -> ResultT:
        normalized = self.normalize_event(event)
        if normalized.get("type") != RuntimeEventType.TURN_COMPLETED.value:
            raise ClaudeRuntimeResultError(_result_failure("claude_result_incomplete"))
        raw = normalized.get("result")
        if not isinstance(raw, str):
            raise ClaudeRuntimeResultError(_result_failure("claude_result_incomplete"))
        try:
            return parser(raw)
        except Exception as exc:
            raise ClaudeRuntimeResultError(
                _result_failure("claude_result_validation_failed")
            ) from exc

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
            code = {
                "idle": "claude_idle_timeout",
                "total": "claude_total_timeout",
            }.get(timeout_kind, "claude_transport_timeout")
            return _transport_failure(code)
        failure_text = _safe_failure_signal(stdout, stderr).casefold()
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
        if "error_max_turns" in failure_text:
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

    def _normalize_assistant_block(self, block: object) -> dict[str, object]:
        if not isinstance(block, dict):
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return {
                "type": RuntimeEventType.ITEM_COMPLETED.value,
                "item": {"type": "agent_message", "text": block["text"]},
            }
        if block.get("type") != "tool_use":
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        call_id = _required_string(block.get("id"))
        tool_name = _required_string(block.get("name"))
        arguments = block.get("input")
        if call_id is None or tool_name is None or not isinstance(arguments, dict):
            raise ClaudeEventPolicyError("claude_event_unrecognized")
        item = self._reviewed_tool_item(call_id, tool_name, arguments)
        self._started_items[call_id] = item
        return {
            "type": RuntimeEventType.ITEM_STARTED.value,
            "item": item,
        }

    def _reviewed_tool_item(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        mcp_identity = self._mcp_tool_names.get(tool_name)
        if mcp_identity is not None:
            server, tool = mcp_identity
            call = self.effects.classify(
                {
                    "type": "mcp_tool_call",
                    "server": server,
                    "tool": tool,
                    "arguments": arguments,
                }
            )
            if call is None:
                raise ClaudeEventPolicyError("claude_tool_unreviewed")
            return {
                "type": "mcp_tool_call",
                "id": call_id,
                "status": "in_progress",
                "server": server,
                "tool": tool,
                "arguments": arguments,
                "metadata": {
                    "effect": call.effect.value,
                    "capability": call.server,
                    "operation": call.operation,
                    "operation_digest": call.operation_digest,
                    "target_identifiers": call.target_identifiers,
                },
            }
        if tool_name == "Bash":
            command = arguments.get("command")
            if not isinstance(command, str):
                raise ClaudeEventPolicyError("claude_tool_unreviewed")
            reviewed = self.native_cli.classify(
                {"type": "command_execution", "command": command}
            )
            if reviewed is None or reviewed.effect is None:
                raise ClaudeEventPolicyError("claude_tool_unreviewed")
            return {
                "type": "command_execution",
                "id": call_id,
                "status": "in_progress",
                "command": command,
                "metadata": {
                    "effect": reviewed.effect.value,
                    "capability": f"agent_cli.{reviewed.cli}",
                    "operation": reviewed.command_path,
                    "operation_digest": reviewed.command_digest,
                    "target_identifiers": reviewed.target_identifiers,
                    "native_cli": reviewed.cli,
                },
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
        return {
            "type": (
                RuntimeEventType.ITEM_FAILED.value
                if is_error
                else RuntimeEventType.ITEM_COMPLETED.value
            ),
            "item": item,
        }

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


def _required_string(value: object) -> str | None:
    return value if isinstance(value, str) and value and value == value.strip() else None


def _safe_failure_signal(stdout: str, stderr: str) -> str:
    signals = [stderr]
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        if not isinstance(event, dict) or event.get("type") != "result":
            continue
        for key in ("subtype", "result"):
            value = event.get(key)
            if isinstance(value, str):
                signals.append(value)
    return "\n".join(signals)


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
