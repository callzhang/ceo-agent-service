"""Service-owned pre-execution permission broker for Claude CLI tools."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from app.agent_effects import McpToolEffectRegistry
from app.native_cli_metadata import NativeCliMetadataClassifier

DispatchT = TypeVar("DispatchT")


class ClaudePermissionBroker:
    """Authorize exact reviewed tool name+input before Claude dispatches it."""

    def __init__(
        self,
        *,
        allowed_mcp_tools: frozenset[str],
        allow_native_cli: bool,
        effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
    ) -> None:
        self._allowed_mcp_tools = allowed_mcp_tools
        self._allow_native_cli = allow_native_cli
        self._effects = effect_registry or McpToolEffectRegistry.default()
        self._native_cli = native_cli_classifier or NativeCliMetadataClassifier()
        self._reviewed_mcp_names = {
            f"mcp__{server}__{tool}": (server, tool)
            for server, tools in self._effects.reviewed_tools().items()
            for tool in tools
        }

    def authorize(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        identity = self._reviewed_mcp_names.get(tool_name)
        if tool_name in self._allowed_mcp_tools and identity is not None:
            server, tool = identity
            call = self._effects.classify(
                {
                    "type": "mcp_tool_call",
                    "server": server,
                    "tool": tool,
                    "arguments": arguments,
                }
            )
            if call is not None:
                return {"behavior": "allow", "updatedInput": arguments}
        if self._allow_native_cli and tool_name == "Bash":
            command = arguments.get("command")
            if isinstance(command, str):
                reviewed = self._native_cli.classify(
                    {"type": "command_execution", "command": command}
                )
                if reviewed is not None and reviewed.effect is not None:
                    return {"behavior": "allow", "updatedInput": arguments}
        return {"behavior": "deny", "message": "claude_tool_unreviewed"}

    def dispatch_if_authorized(
        self,
        tool_name: str,
        arguments: dict[str, object],
        dispatch: Callable[[str, dict[str, object]], DispatchT],
    ) -> dict[str, object]:
        decision = self.authorize(tool_name, arguments)
        if decision.get("behavior") == "allow":
            dispatch(tool_name, arguments)
        return decision


def _load_broker(path: Path) -> ClaudePermissionBroker:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Claude broker policy must be an object")
    raw_tools = payload.get("allowed_mcp_tools")
    allow_native_cli = payload.get("allow_native_cli")
    if (
        not isinstance(raw_tools, list)
        or not all(isinstance(item, str) and item for item in raw_tools)
        or not isinstance(allow_native_cli, bool)
    ):
        raise ValueError("Claude broker policy is invalid")
    return ClaudePermissionBroker(
        allowed_mcp_tools=frozenset(raw_tools),
        allow_native_cli=allow_native_cli,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args(argv)
    broker = _load_broker(args.policy)

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("ceo-runtime-permission")

    @server.tool(name="permission_prompt")
    def permission_prompt(
        tool_name: str, input: dict[str, object]
    ) -> str:
        return json.dumps(
            broker.authorize(tool_name, input),
            sort_keys=True,
            separators=(",", ":"),
        )

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
