"""Fail-closed Codex command and JSONL helpers for the WeChat Memory workflow."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

from app.codex_runner import CODEX_BYPASS_APPROVALS_AND_SANDBOX, _config_string

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
WECHAT_MEMORY_READ_TOOLS = (
    "memory_get",
    "memory_recall",
    "timeline_get",
    "user_get",
)

@dataclass(frozen=True)
class ControlledCliConfig:
    command: str
    args: tuple[str, ...]
    cwd: str
    env: tuple[tuple[str, str], ...] = ()


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
    completed_items: list[dict] = []
    for payload in _jsonl_payloads(raw):
        if payload.get("type") != "item.completed":
            continue
        item = payload.get("item")
        if isinstance(item, dict):
            completed_items.append(item)

    calls = [
        event
        for event in completed_items
        if event.get("type") == "mcp_tool_call"
    ]
    outputs = {
        str(event.get("call_id") or ""): event.get("output")
        for event in completed_items
        if event.get("type") == "function_call_output"
        and str(event.get("call_id") or "")
    }
    for event in completed_items:
        namespace = str(event.get("namespace") or "")
        if event.get("type") != "function_call" or not namespace.startswith("mcp__"):
            continue
        normalized = dict(event)
        normalized["type"] = "mcp_tool_call"
        normalized["tool"] = str(event.get("name") or "")
        normalized["result"] = outputs.get(str(event.get("call_id") or ""))
        calls.append(normalized)
    return calls


def configured_transport_server_names(command: list[str]) -> tuple[str, ...]:
    """Find service-owned MCP transports present in the generated command."""
    names: set[str] = set()
    for index, value in enumerate(command[:-1]):
        if value != "-c":
            continue
        match = _TRANSPORT_OPTION.match(command[index + 1])
        if match:
            names.add(match.group(1))
    return tuple(sorted(names))


def make_role_agent_command(
    command: list[str],
    *,
    controlled_cli: ControlledCliConfig | None = None,
) -> None:
    """Use the native Codex capability surface and its automatic reviewer.

    Consumer and Audit differ in their typed business contracts, not in their
    shell or MCP permissions.  The service therefore does not construct a
    command allowlist or a role-specific read-only sandbox here; the service
    MCP manifest is already part of every routed Codex command.
    """
    while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
        command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    _remove_config_options(
        command,
        prefixes=("approval_policy=", "approvals_reviewer=", "tools.enabled_tools="),
    )
    options = [
        "-c",
        'approval_policy="on-failure"',
        "-c",
        'approvals_reviewer="auto_review"',
    ]
    if controlled_cli is not None:
        options.extend(
            [
                "-c",
                f"mcp_servers.agent_cli.command={json.dumps(controlled_cli.command)}",
                "-c",
                "mcp_servers.agent_cli.args=" + json.dumps(list(controlled_cli.args)),
                "-c",
                f"mcp_servers.agent_cli.cwd={json.dumps(controlled_cli.cwd)}",
                *(
                    [
                        "-c",
                        _config_string(
                            "mcp_servers.agent_cli.env", dict(controlled_cli.env)
                        ),
                    ]
                    if controlled_cli.env
                    else []
                ),
            ]
        )
    _insert_command_options(
        command,
        options,
    )


def make_consumer_agent_command(
    command: list[str],
) -> None:
    make_role_agent_command(command)


def make_audit_agent_command(
    command: list[str],
    *,
    controlled_cli: ControlledCliConfig | None = None,
) -> None:
    make_role_agent_command(
        command,
        controlled_cli=controlled_cli,
    )


def disable_automatic_review(command: list[str]) -> None:
    """Run the turn without Codex's automatic reviewer.

    The reviewer is a separate call to the `codex-auto-review` model on the
    route's provider. Third-party OpenAI-compatible providers do not serve that
    model, so every reviewed action on such a route would be rejected. The
    sandbox stays in place; the turn simply never asks for approval.
    """
    _remove_config_options(
        command,
        prefixes=("approval_policy=", "approvals_reviewer="),
    )
    _insert_command_options(command, ["-c", 'approval_policy="never"'])


def _insert_command_options(command: list[str], options: list[str]) -> None:
    prompt_index = len(command) - 1
    if command[1:3] == ["exec", "resume"]:
        prompt_index -= 1
    command[prompt_index:prompt_index] = options


def _remove_config_options(command: list[str], *, prefixes: tuple[str, ...]) -> None:
    index = 0
    while index + 1 < len(command):
        if command[index] == "-c" and command[index + 1].startswith(prefixes):
            del command[index : index + 2]
            continue
        index += 1
