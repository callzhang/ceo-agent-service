"""Dedicated synthetic MCP tool used only by the Claude runtime probe."""

from __future__ import annotations

_MARKER = "ceo-agent-runtime-probe-v1"


def main() -> int:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("runtime-probe")

    @server.tool(name="record_effect_start")
    def record_effect_start(marker: str) -> dict[str, bool]:
        if marker != _MARKER:
            raise ValueError("runtime probe marker is invalid")
        return {"recorded": True}

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
