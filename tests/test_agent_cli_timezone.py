import asyncio

from app.agent_cli import build_role_server
from app.wechat.codex_safety import AGENT_CLI_READ_TOOLS


def test_timezone_converter_is_a_read_only_consumer_and_audit_tool():
    assert "convert_dingtalk_calendar_time" in AGENT_CLI_READ_TOOLS

    for role in ("consumer", "audit"):
        server = build_role_server(role)
        tool = server._tool_manager.get_tool("convert_dingtalk_calendar_time")
        result = tool.fn(
            "2026-10-27T08:00", "America/Los_Angeles", "Europe/London"
        )
        descriptor = next(
            item
            for item in asyncio.run(server.list_tools())
            if item.name == "convert_dingtalk_calendar_time"
        )

        assert result["status"] == "converted"
        assert result["conversion"]["utc"] == "2026-10-27T15:00:00Z"
        assert descriptor.annotations.readOnlyHint is True
        assert "does not check calendar or participant availability" in descriptor.description
