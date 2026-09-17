"""The reviewed read/write nature of tools on third-party MCP servers.

`app.native_cli_metadata` classifies a native CLI command from the provider's
own schema. An MCP server outside that schema -- the interview system, for
instance -- has no such schema to read here, so its catalogue is recorded as
service-owned data in `data/config/mcp-tool-effects.json` and looked up by the
tool name the runtime recorded, which is the name that was actually invoked.

A tool the file does not list is deliberately left unjudged rather than guessed
in either direction: calling it evidence would let a read pass as a write, and
calling it a write would refuse work whose effect the service cannot recognise.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.agent_result import EffectKind

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config" / "mcp-tool-effects.json"


@lru_cache(maxsize=1)
def _reviewed_effects() -> dict[tuple[str, str], EffectKind]:
    try:
        payload = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = payload.get("servers")
    if not isinstance(servers, dict):
        return {}
    reviewed: dict[tuple[str, str], EffectKind] = {}
    for server, entry in servers.items():
        tools = entry.get("tools") if isinstance(entry, dict) else None
        if not isinstance(tools, dict):
            continue
        for tool, effect in tools.items():
            if effect == "effectful":
                reviewed[(server, tool)] = EffectKind.EFFECTFUL
            elif effect == "read_only":
                reviewed[(server, tool)] = EffectKind.READ_ONLY
    return reviewed


def reviewed_mcp_tool_effect(server: str, tool: str) -> EffectKind | None:
    """Return the reviewed effect of one MCP tool, or None when it is unlisted."""
    if not server.strip() or not tool.strip():
        return None
    return _reviewed_effects().get((server.strip(), tool.strip()))
