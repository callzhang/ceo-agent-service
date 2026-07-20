"""Fail-closed Codex command and JSONL helpers for the WeChat Memory workflow."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from app.codex_runner import CODEX_BYPASS_APPROVALS_AND_SANDBOX

_TRANSPORT_OPTION = re.compile(
    r"^mcp_servers\.([A-Za-z0-9_-]+)\.(?:url|command)="
)
_TOOL_ITEM_TYPES = frozenset({
    "command_execution",
    "dynamic_tool_call",
    "function_call",
    "mcp_tool_call",
    "tool_call",
    "tool_search_call",
    "web_search",
    "web_search_call",
})


def _jsonl_payloads(raw: str) -> Iterator[dict]:
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def completed_tool_events(raw: str) -> list[dict]:
    """Return completed tool events; lifecycle starts are never audit evidence."""
    events: list[dict] = []
    for payload in _jsonl_payloads(raw):
        if payload.get("type") != "item.completed":
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in _TOOL_ITEM_TYPES or item_type.endswith("_tool_call"):
            events.append(item)
    return events


def completed_mcp_tool_calls(raw: str) -> list[dict]:
    return [
        event for event in completed_tool_events(raw)
        if event.get("type") == "mcp_tool_call"
    ]


def has_any_tool_event(raw: str) -> bool:
    """Detect any Codex tool lifecycle event, including attempted/started calls."""
    for payload in _jsonl_payloads(raw):
        item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in _TOOL_ITEM_TYPES or item_type.endswith("_tool_call"):
            return True
    return False


def configured_transport_server_names(command: list[str]) -> tuple[str, ...]:
    """Find servers with an actual URL/command, never names from default allowlists."""
    from app.codex_runner import _codex_config, _passthrough_mcp_server_names

    names: set[str] = set()
    passthrough_names = frozenset(_passthrough_mcp_server_names())
    servers = _codex_config().get("mcp_servers") or {}
    if isinstance(servers, dict):
        for name, server in servers.items():
            if not isinstance(name, str) or not isinstance(server, dict):
                continue
            if name in passthrough_names and any(
                isinstance(server.get(key), str) and server[key].strip()
                for key in ("url", "command")
            ):
                names.add(name)
    for index, value in enumerate(command[:-1]):
        if value != "-c" or index + 1 >= len(command):
            continue
        match = _TRANSPORT_OPTION.match(command[index + 1])
        if match:
            names.add(match.group(1))
    return tuple(sorted(names))


def disable_configured_mcp_servers(
    command: list[str], *, except_names: frozenset[str] = frozenset(),
) -> None:
    for name in configured_transport_server_names(command):
        if name not in except_names:
            command[-1:-1] = ["-c", f"mcp_servers.{name}.enabled=false"]


def make_read_only_without_tools(command: list[str]) -> None:
    """Constrain extraction to read-only Codex with no MCP, web, or other tools."""
    while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
        command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    disable_configured_mcp_servers(command)
    command[-1:-1] = [
        "--sandbox", "read-only",
        "-c", "tools.enabled_tools=[]",
        "-c", 'web_search="disabled"',
    ]
