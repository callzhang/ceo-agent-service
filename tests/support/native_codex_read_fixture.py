"""Shared isolation for opt-in native Codex read-only fixtures."""

from __future__ import annotations

import json

from app.wechat.codex_safety import configured_transport_server_names


READ_ONLY_AGENT_CLI_TOOLS = ("read_skill", "execute_reviewed_read")


def isolate_read_only_fixture_command(
    command: list[str],
    *,
    server_command: str,
    server_args: tuple[str, ...],
    server_cwd: str,
) -> list[str]:
    isolated = _remove_conflicting_options(command)
    insert_at = isolated.index("--cd")
    isolated[insert_at:insert_at] = [
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-c",
        "features.plugins=false",
        "-c",
        "features.apps=false",
        "-c",
        "tools.enabled_tools=[]",
        "-c",
        'web_search="disabled"',
        "-c",
        'approval_policy="untrusted"',
        "-c",
        'approvals_reviewer="auto_review"',
        "-c",
        f"mcp_servers.agent_cli.command={json.dumps(server_command)}",
        "-c",
        "mcp_servers.agent_cli.args=" + json.dumps(list(server_args)),
        "-c",
        f"mcp_servers.agent_cli.cwd={json.dumps(server_cwd)}",
        "-c",
        "mcp_servers.agent_cli.enabled_tools="
        + json.dumps(list(READ_ONLY_AGENT_CLI_TOOLS), separators=(",", ":")),
    ]
    assert_isolated_read_only_fixture_command(isolated)
    return isolated


def assert_isolated_read_only_fixture_command(command: list[str]) -> None:
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "features.plugins=false" in command
    assert "features.apps=false" in command
    assert "tools.enabled_tools=[]" in command
    assert 'web_search="disabled"' in command
    assert 'approval_policy="untrusted"' in command
    assert 'approvals_reviewer="auto_review"' in command
    assert configured_transport_server_names(command) == ("agent_cli",)
    mcp_options = [
        command[index + 1]
        for index, item in enumerate(command[:-1])
        if item == "-c" and command[index + 1].startswith("mcp_servers.")
    ]
    assert mcp_options
    assert all(option.startswith("mcp_servers.agent_cli.") for option in mcp_options)
    assert (
        'mcp_servers.agent_cli.enabled_tools=["read_skill","execute_reviewed_read"]'
        in command
    )
    assert all("execute_reviewed_write" not in item for item in command)


def _remove_conflicting_options(command: list[str]) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(command):
        item = command[index]
        if item in {
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--dangerously-bypass-approvals-and-sandbox",
        }:
            index += 1
            continue
        if item == "--sandbox":
            index += 2
            continue
        if item == "-c" and index + 1 < len(command):
            value = command[index + 1]
            if value.startswith(
                (
                    "approval_policy=",
                    "approvals_reviewer=",
                    "features.plugins=",
                    "features.apps=",
                    "tools.enabled_tools=",
                    "web_search=",
                    "mcp_servers.",
                )
            ):
                index += 2
                continue
        cleaned.append(item)
        index += 1
    return cleaned
