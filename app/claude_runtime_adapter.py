"""Strict command and credential boundary for the Claude CLI runtime."""

from __future__ import annotations

import os
from pathlib import Path

from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.codex_runtime_adapter import _safe_child_environment


class ClaudeRuntimeAdapter:
    """Build bounded non-interactive Claude invocations for one configured route."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: AgentRuntimeConfig,
        claude_bin: str = "claude",
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.claude_bin = claude_bin

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
