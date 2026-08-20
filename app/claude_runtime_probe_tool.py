"""Dedicated synthetic MCP tool used only by the Claude runtime probe."""

from __future__ import annotations

from typing import Literal

from app.claude_tool_input import reviewed_claude_tool_schema

_MARKER = "ceo-agent-runtime-probe-v1"


def build_server():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("runtime-probe")

    @server.tool(name="record_effect_start")
    def record_effect_start(
        marker: Literal["ceo-agent-runtime-probe-v1"],
    ) -> dict[str, bool]:
        return {"recorded": True}

    tool = server._tool_manager.list_tools()[0]
    reviewed = reviewed_claude_tool_schema("mcp__runtime_probe__record_effect_start")
    if reviewed is None:
        raise RuntimeError("runtime probe schema is unavailable")
    tool.parameters = reviewed
    tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
    tool.fn_metadata.arg_model.model_rebuild(force=True)
    return server


def main() -> int:
    server = build_server()

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
