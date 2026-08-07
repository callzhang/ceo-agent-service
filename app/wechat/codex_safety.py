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


def has_any_tool_event(raw: str) -> bool:
    """Detect any Codex tool lifecycle event, including attempted/started calls."""
    for payload in _jsonl_payloads(raw):
        item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in _TOOL_ITEM_TYPES or item_type.endswith("_tool_call"):
            return True
    return False


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


def disable_configured_mcp_servers(
    command: list[str], *, except_names: frozenset[str] = frozenset(),
) -> None:
    for name in configured_transport_server_names(command):
        if name not in except_names:
            _insert_command_options(
                command, ["-c", f"mcp_servers.{name}.enabled=false"]
            )


def make_read_only_without_tools(command: list[str]) -> None:
    """Constrain extraction to read-only Codex with no MCP, web, or other tools."""
    while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
        command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    _remove_config_options(
        command,
        prefixes=("approval_policy=", "approvals_reviewer=", "tools.enabled_tools="),
    )
    disable_configured_mcp_servers(command)
    _insert_command_options(
        command,
        [
            "-c", "features.plugins=false",
            "-c", "features.apps=false",
            "--sandbox", "read-only",
            "-c", 'approval_policy="never"',
            "-c", "tools.enabled_tools=[]",
            "-c", 'web_search="disabled"',
        ],
    )


def make_role_agent_command(
    command: list[str],
    *,
    reviewed_mcp_tools: dict[str, tuple[str, ...]],
    controlled_cli: ControlledCliConfig,
    allow_write: bool,
) -> None:
    if not allow_write:
        while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
            command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    _remove_config_options(
        command,
        prefixes=("approval_policy=", "approvals_reviewer=", "tools.enabled_tools="),
    )
    for name in configured_transport_server_names(command):
        tools = reviewed_mcp_tools.get(name, ())
        if tools:
            encoded = json.dumps(list(tools), separators=(",", ":"))
            _insert_command_options(
                command, ["-c", f"mcp_servers.{name}.enabled_tools={encoded}"]
            )
        else:
            _insert_command_options(command, ["-c", f"mcp_servers.{name}.enabled=false"])
    agent_cli_tools = ["execute_reviewed_read", "read_skill"]
    approval_options = ["-c", 'approval_policy="never"']
    if allow_write:
        agent_cli_tools.insert(1, "execute_reviewed_write")
        approval_options = [
            "-c",
            'approval_policy="untrusted"',
            "-c",
            'approvals_reviewer="auto_review"',
        ]
    _insert_command_options(
        command,
        [
            "-c", "features.plugins=false",
            "-c", "features.apps=false",
            "--sandbox", "read-only",
            *approval_options,
            "-c", f"mcp_servers.agent_cli.command={json.dumps(controlled_cli.command)}",
            "-c", "mcp_servers.agent_cli.args=" + json.dumps(list(controlled_cli.args)),
            "-c", f"mcp_servers.agent_cli.cwd={json.dumps(controlled_cli.cwd)}",
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
            "-c", "mcp_servers.agent_cli.enabled_tools=" + json.dumps(agent_cli_tools),
        ],
    )


def make_consumer_agent_command(
    command: list[str],
    *,
    reviewed_mcp_tools: dict[str, tuple[str, ...]],
    controlled_cli: ControlledCliConfig,
) -> None:
    make_role_agent_command(
        command,
        reviewed_mcp_tools=reviewed_mcp_tools,
        controlled_cli=controlled_cli,
        allow_write=False,
    )


def make_audit_agent_command(
    command: list[str],
    *,
    reviewed_mcp_tools: dict[str, tuple[str, ...]],
    controlled_cli: ControlledCliConfig,
    allow_write: bool = True,
) -> None:
    make_role_agent_command(
        command,
        reviewed_mcp_tools=reviewed_mcp_tools,
        controlled_cli=controlled_cli,
        allow_write=allow_write,
    )


def make_read_only_with_memory_tools(command: list[str]) -> None:
    """Allow only durable-memory reads while a WeChat reply is being decided."""
    while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
        command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    _remove_config_options(
        command,
        prefixes=("approval_policy=", "approvals_reviewer=", "tools.enabled_tools="),
    )
    disable_configured_mcp_servers(
        command,
        except_names=frozenset({"memory_connector"}),
    )
    enabled_tools = json.dumps(
        list(WECHAT_MEMORY_READ_TOOLS), ensure_ascii=True, separators=(",", ":")
    )
    _insert_command_options(
        command,
        [
            "-c",
            "features.plugins=false",
            "-c",
            "features.apps=false",
            "--sandbox",
            "read-only",
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            f"mcp_servers.memory_connector.enabled_tools={enabled_tools}",
        ],
    )


def make_read_only_with_reviewed_tools(
    command: list[str],
    *,
    reviewed_mcp_tools: dict[str, tuple[str, ...]],
    controlled_cli_command: str,
    controlled_cli_args: tuple[str, ...],
    controlled_cli_cwd: str,
) -> None:
    """Use a read-only sandbox and expose only explicitly reviewed MCP reads."""
    while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
        command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    _remove_config_options(
        command,
        prefixes=("approval_policy=", "approvals_reviewer=", "tools.enabled_tools="),
    )
    configured = configured_transport_server_names(command)
    for name in configured:
        tools = reviewed_mcp_tools.get(name, ())
        if not tools:
            _insert_command_options(
                command,
                ["-c", f"mcp_servers.{name}.enabled=false"],
            )
            continue
        encoded = json.dumps(list(tools), ensure_ascii=True, separators=(",", ":"))
        _insert_command_options(
            command,
            ["-c", f"mcp_servers.{name}.enabled_tools={encoded}"],
        )
    _insert_command_options(
        command,
        [
            "-c",
            "features.plugins=false",
            "-c",
            "features.apps=false",
            "--sandbox",
            "read-only",
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            f"mcp_servers.agent_cli.command={json.dumps(controlled_cli_command)}",
            "-c",
            "mcp_servers.agent_cli.args="
            + json.dumps(list(controlled_cli_args), ensure_ascii=True),
            "-c",
            f"mcp_servers.agent_cli.cwd={json.dumps(controlled_cli_cwd)}",
            "-c",
            'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read","read_skill"]',
        ],
    )


def make_direct_agent_sandbox(
    command: list[str],
    *,
    reviewed_mcp_tools: dict[str, tuple[str, ...]],
    controlled_cli_command: str,
    controlled_cli_args: tuple[str, ...],
    controlled_cli_cwd: str,
) -> None:
    """Expose sandboxed local reads and only reviewed external capabilities."""
    while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
        command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    _remove_command_options(command, names=("--sandbox",))
    _remove_config_options(
        command,
        prefixes=("approval_policy=", "tools.enabled_tools=", "web_search="),
    )
    configured = configured_transport_server_names(command)
    for name in configured:
        tools = reviewed_mcp_tools.get(name, ())
        if not tools:
            _insert_command_options(
                command,
                ["-c", f"mcp_servers.{name}.enabled=false"],
            )
            continue
        encoded = json.dumps(list(tools), ensure_ascii=True, separators=(",", ":"))
        _insert_command_options(
            command,
            ["-c", f"mcp_servers.{name}.enabled_tools={encoded}"],
        )
    _insert_command_options(
        command,
        [
            "-c",
            "features.plugins=false",
            "-c",
            "features.apps=false",
            "--sandbox",
            "read-only",
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            f"mcp_servers.agent_cli.command={json.dumps(controlled_cli_command)}",
            "-c",
            "mcp_servers.agent_cli.args="
            + json.dumps(list(controlled_cli_args), ensure_ascii=True),
            "-c",
            f"mcp_servers.agent_cli.cwd={json.dumps(controlled_cli_cwd)}",
            "-c",
            "mcp_servers.agent_cli.enabled_tools="
            '["execute_reviewed_read","execute_reviewed_write","read_skill"]',
        ],
    )


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


def _remove_command_options(command: list[str], *, names: tuple[str, ...]) -> None:
    index = 0
    while index + 1 < len(command):
        if command[index] in names:
            del command[index : index + 2]
            continue
        index += 1
