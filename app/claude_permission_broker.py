"""Service-owned pre-execution permission broker for Claude CLI tools."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from app.agent_effects import McpToolEffectRegistry
from app.claude_tool_input import validate_claude_tool_input
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
        grant_endpoints: dict[str, dict[str, str]] | None = None,
        grant_issuer: Callable[[str, dict[str, object]], str | None] | None = None,
    ) -> None:
        self._allowed_mcp_tools = allowed_mcp_tools
        self._allow_native_cli = allow_native_cli
        self._effects = effect_registry or McpToolEffectRegistry.default()
        self._native_cli = native_cli_classifier or NativeCliMetadataClassifier()
        self._grant_endpoints = dict(grant_endpoints or {})
        self._grant_issuer = grant_issuer or self._issue_remote_grant
        self._reviewed_mcp_names = {
            f"mcp__{server}__{tool}": (server, tool)
            for server, tools in self._effects.reviewed_tools().items()
            for tool in tools
        }

    def authorize(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        identity = self._reviewed_mcp_names.get(tool_name)
        if (
            tool_name in self._allowed_mcp_tools
            and identity is not None
            and "__ceo_runtime_grant" not in arguments
            and validate_claude_tool_input(tool_name, arguments)
        ):
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
                grant = self._grant_issuer(tool_name, arguments)
                if grant is not None:
                    return {
                        "behavior": "allow",
                        "updatedInput": {
                            **arguments,
                            "__ceo_runtime_grant": grant,
                        },
                    }
        if self._allow_native_cli and tool_name == "Bash":
            command = arguments.get("command")
            if isinstance(command, str) and "$" not in command and "`" not in command:
                reviewed = self._native_cli.classify(
                    {"type": "command_execution", "command": command}
                )
                if reviewed is not None and reviewed.effect is not None:
                    return {"behavior": "allow", "updatedInput": arguments}
        return {"behavior": "deny", "message": "claude_tool_unreviewed"}

    def _issue_remote_grant(
        self, tool_name: str, arguments: dict[str, object]
    ) -> str | None:
        from urllib.request import Request, urlopen

        identity = self._reviewed_mcp_names.get(tool_name)
        endpoint = self._grant_endpoints.get(identity[0]) if identity else None
        if endpoint is None:
            return None
        payload = json.dumps(
            {"tool": tool_name, "arguments": arguments},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        request = Request(
            endpoint["url"],
            data=payload,
            headers={
                "X-CEO-Runtime-Invocation": endpoint["token"],
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                result = json.loads(response.read())
        except Exception:  # noqa: BLE001 - deny on grant transport failure
            return None
        grant = result.get("grant") if isinstance(result, dict) else None
        return grant if isinstance(grant, str) and grant else None

    def dispatch_if_authorized(
        self,
        tool_name: str,
        arguments: dict[str, object],
        dispatch: Callable[[str, dict[str, object]], DispatchT],
    ) -> dict[str, object]:
        decision = self.authorize(tool_name, arguments)
        if decision.get("behavior") == "allow":
            updated = decision.get("updatedInput")
            if isinstance(updated, dict):
                dispatch(tool_name, updated)
        return decision


def _load_broker(path: Path) -> ClaudePermissionBroker:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Claude broker policy must be an object")  # noqa: TRY004
    raw_tools = payload.get("allowed_mcp_tools")
    allow_native_cli = payload.get("allow_native_cli")
    grant_endpoints = payload.get("grant_endpoints", {})
    if (
        not isinstance(raw_tools, list)
        or not all(isinstance(item, str) and item for item in raw_tools)
        or not isinstance(allow_native_cli, bool)
        or not isinstance(grant_endpoints, dict)
        or not all(
            isinstance(server, str)
            and isinstance(endpoint, dict)
            and set(endpoint) == {"url", "token"}
            and all(isinstance(value, str) and value for value in endpoint.values())
            for server, endpoint in grant_endpoints.items()
        )
    ):
        raise ValueError("Claude broker policy is invalid")
    return ClaudePermissionBroker(
        allowed_mcp_tools=frozenset(raw_tools),
        allow_native_cli=allow_native_cli,
        grant_endpoints=grant_endpoints,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args(argv)
    broker = _load_broker(args.policy)

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("ceo-runtime-permission")

    @server.tool(name="permission_prompt")
    def permission_prompt(tool_name: str, input: dict[str, object]) -> str:
        return json.dumps(
            broker.authorize(tool_name, input),
            sort_keys=True,
            separators=(",", ":"),
        )

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
