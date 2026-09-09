"""Read the MCP servers Codex itself would load for a run."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class CodexMcpInventoryError(RuntimeError):
    """`codex mcp list` did not produce a usable inventory."""


@dataclass(frozen=True)
class CodexMcpServer:
    name: str
    transport_type: str
    # URL without query/fragment, or the stdio command; never carries tokens.
    location: str
    enabled: bool
    auth_status: str


def list_codex_mcp_servers(
    *,
    codex_bin: str = "codex",
    env: Mapping[str, str] = os.environ,
    timeout_seconds: float = 30.0,
) -> tuple[CodexMcpServer, ...]:
    """Return every server from the user's Codex configuration."""

    try:
        completed = subprocess.run(
            [codex_bin, "mcp", "list", "--json"],
            capture_output=True,
            text=True,
            env=dict(env),
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexMcpInventoryError("codex mcp list could not be run") from exc
    if completed.returncode != 0:
        raise CodexMcpInventoryError(
            f"codex mcp list exited with status {completed.returncode}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CodexMcpInventoryError("codex mcp list returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise CodexMcpInventoryError("codex mcp list returned an unexpected shape")
    servers: list[CodexMcpServer] = []
    for entry in payload:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise CodexMcpInventoryError("codex mcp list returned an unexpected shape")
        transport = entry.get("transport")
        transport = transport if isinstance(transport, dict) else {}
        transport_type = str(transport.get("type") or "unknown")
        if isinstance(transport.get("url"), str):
            location = _redacted_url(transport["url"])
        else:
            location = str(transport.get("command") or "")
        servers.append(
            CodexMcpServer(
                name=entry["name"],
                transport_type=transport_type,
                location=location,
                enabled=entry.get("enabled") is True,
                auth_status=str(entry.get("auth_status") or "unknown"),
            )
        )
    return tuple(servers)


def _redacted_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
