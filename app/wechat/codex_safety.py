"""Fail-closed Codex command and JSONL helpers for the WeChat Memory workflow."""
from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass

from app.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    _config_string,
    resolved_codex_home,
)

_TRANSPORT_OPTION = re.compile(
    r"^mcp_servers\.([A-Za-z0-9_-]+)\.(?:url|command)="
)
_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
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

AGENT_CLI_READ_TOOLS = (
    "read_skill",
    "read_text_file",
    "read_spreadsheet",
    "read_dingtalk_document",
    "read_dingtalk_document_full",
    "read_dingtalk_document_info",
    "read_dingtalk_document_permissions",
    "read_dingtalk_drive_info",
    "list_dingtalk_sheets",
    "read_dingtalk_sheet_info",
    "read_dingtalk_sheet_range",
    "read_dingtalk_oa",
    "list_dingtalk_pending_oa",
    "read_dingtalk_oa_records",
    "read_dingtalk_oa_tasks",
    "read_dingtalk_oa_revert_activities",
    "read_dingtalk_calendar_event",
    "list_dingtalk_calendar_events",
    "search_dingtalk_contacts",
    "search_dingtalk_documents",
    "read_dingtalk_messages",
    "search_dingtalk_messages",
    "read_dingtalk_thread_replies",
    "search_dingtalk_conversations",
    "list_dingtalk_minutes",
    "read_dingtalk_minutes",
    "recent_dingtalk_conversations",
    "read_dingtalk_messages_in_window",
    "daily_report_facts",
    "weekly_report_materials",
    "read_weekly_report_archive",
)
AGENT_CLI_CONSUMER_TOOLS = AGENT_CLI_READ_TOOLS + (
    "consumer_document_write",
    "validate_weekly_report",
    "render_weekly_report",
)

# Exact tools from the service's reviewed MCP effect registry. New provider
# operations stay unavailable until their effect is reviewed and named here.
ROLE_MCP_READ_TOOLS = {
    "memory_connector": (
        "user_get", "memory_recall", "memory_get", "timeline_get",
    ),
    "exa": ("web_search_exa", "web_fetch_exa"),
    "xiaoqing_interview": (
        "search_candidates", "get_dashboard_stats", "get_interview_context",
        "download_attachment", "list_candidate_interviews",
    ),
}

# These native surfaces can mutate local/app state or delegate work outside
# the task-bound MCP catalog. The service roles use the named MCP operations
# below instead; Consumer's report-document tool remains available there.
ROLE_DISABLED_NATIVE_FEATURES = (
    "shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot",
    "apps", "plugins", "remote_plugin", "hooks", "computer_use",
    "browser_use", "browser_use_external", "browser_use_full_cdp_access",
    "in_app_browser", "in_app_local_automation", "in_app_chat",
    "image_generation", "multi_agent", "code_mode_host", "goals",
    "worktrees", "in_app_updates",
    "skill_mcp_dependency_install", "memories",
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


def inherited_native_server_names() -> tuple[str, ...]:
    """Inventory only server names from this CLI home's native config."""
    config_path = resolved_codex_home(os.environ) / "config.toml"
    if not config_path.is_file():
        return ()
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("codex_native_mcp_inventory_unavailable") from exc
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict) or any(
        not isinstance(name, str) or _SERVER_NAME.fullmatch(name) is None
        for name in servers
    ):
        raise RuntimeError("codex_native_mcp_inventory_invalid")
    return tuple(sorted(servers))


def make_role_agent_command(
    command: list[str],
    *,
    role: str,
    controlled_cli: ControlledCliConfig | None = None,
) -> None:
    """Use native tool selection for a role-bound service turn."""
    if role not in {"consumer", "audit"}:
        raise ValueError("unsupported agent role")
    while CODEX_BYPASS_APPROVALS_AND_SANDBOX in command:
        command.remove(CODEX_BYPASS_APPROVALS_AND_SANDBOX)
    _remove_config_options(
        command,
        prefixes=(
            "approval_policy=", "approvals_reviewer=", "tools.enabled_tools=",
            "sandbox_mode=",
            *(f"features.{feature}=" for feature in ROLE_DISABLED_NATIVE_FEATURES),
        ),
    )
    options = [
        *(option for feature in ROLE_DISABLED_NATIVE_FEATURES
          for option in ("-c", f"features.{feature}=false")),
        "-c",
        'sandbox_mode="read-only"',
        "-c",
        "mcp_servers.agent_cli.enabled_tools="
        + json.dumps(
            list(AGENT_CLI_CONSUMER_TOOLS if role == "consumer" else AGENT_CLI_READ_TOOLS)
        ),
        "-c",
        'mcp_servers.agent_cli.default_tools_approval_mode="approve"',
        "-c",
        'approval_policy="never"',
    ]
    configured_names = set(configured_transport_server_names(command))
    for name in sorted(configured_names):
        if name != "agent_cli":
            options.extend([
                "-c", f"mcp_servers.{name}.enabled_tools="
                + json.dumps(list(ROLE_MCP_READ_TOOLS.get(name, ()))),
                "-c", f'mcp_servers.{name}.default_tools_approval_mode="approve"',
            ])
    for name in sorted((set(inherited_native_server_names()) | configured_names)
                       - ({"agent_cli"} | (configured_names & ROLE_MCP_READ_TOOLS.keys()))):
        options.extend(("-c", f"mcp_servers.{name}.enabled=false"))
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
    *,
    controlled_cli: ControlledCliConfig | None = None,
) -> None:
    make_role_agent_command(command, role="consumer", controlled_cli=controlled_cli)


def make_audit_agent_command(
    command: list[str],
    *,
    controlled_cli: ControlledCliConfig | None = None,
) -> None:
    make_role_agent_command(
        command,
        role="audit",
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
