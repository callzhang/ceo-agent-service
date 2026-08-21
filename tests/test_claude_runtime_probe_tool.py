import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.claude_tool_input import (
    normalize_claude_tool_schema,
    reviewed_claude_tool_schema,
)


def test_real_probe_server_handshake_exposes_one_exact_tool_and_calls_marker():
    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.claude_runtime_probe_tool"],
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["record_effect_start"]
            actual = listed.tools[0].inputSchema
            reviewed = reviewed_claude_tool_schema(
                "mcp__runtime_probe__record_effect_start"
            )
            assert normalize_claude_tool_schema(actual) == reviewed
            result = await session.call_tool(
                "record_effect_start",
                {"marker": "ceo-agent-runtime-probe-v1"},
            )
            assert result.isError is False
            rejected = await session.call_tool(
                "record_effect_start",
                {
                    "marker": "ceo-agent-runtime-probe-v1",
                    "unexpected": "blocked",
                },
            )
            assert rejected.isError is True

    asyncio.run(exercise())


def test_schema_normalization_ignores_only_non_constraint_metadata():
    reviewed = reviewed_claude_tool_schema("mcp__runtime_probe__record_effect_start")
    decorated = {
        **reviewed,
        "title": "Arguments",
        "description": "Synthetic only",
        "properties": {
            "marker": {
                **reviewed["properties"]["marker"],
                "title": "Marker",
            }
        },
    }
    assert normalize_claude_tool_schema(decorated) == reviewed
    weakened = dict(decorated)
    weakened.pop("additionalProperties")
    assert normalize_claude_tool_schema(weakened) != reviewed
