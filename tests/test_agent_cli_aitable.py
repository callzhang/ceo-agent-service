import asyncio
import json

import pytest

import app.agent_cli as agent_cli
from app.wechat.codex_safety import AGENT_CLI_READ_TOOLS


class FakeDwsClient:
    calls = []

    def get_aitable_base(self, base_id):
        self.calls.append(("base", base_id))
        return {"base_id": base_id}

    def get_aitable_tables(self, base_id, table_ids=None):
        self.calls.append(("tables", base_id, table_ids))
        return {"base_id": base_id, "table_ids": table_ids}

    def query_aitable_records(self, base_id, table_id, limit=10):
        self.calls.append(("records", base_id, table_id, limit))
        return {"base_id": base_id, "table_id": table_id, "limit": limit}


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_aitable_read_tools_are_registered_for_both_roles(role):
    server = agent_cli.build_role_server(role)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    expected = {
        "read_dingtalk_aitable_base",
        "read_dingtalk_aitable_tables",
        "read_dingtalk_aitable_records",
    }
    assert expected <= names
    assert expected <= set(AGENT_CLI_READ_TOOLS)


def test_aitable_read_tools_forward_only_requested_read_arguments(monkeypatch):
    FakeDwsClient.calls = []
    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    server = agent_cli.build_role_server("consumer")

    results = (
        ("read_dingtalk_aitable_base", ("base-1",), {"base_id": "base-1"}),
        ("read_dingtalk_aitable_tables", ("base-1", ["table-2"]), {
            "base_id": "base-1", "table_ids": ["table-2"],
        }),
        ("read_dingtalk_aitable_records", ("base-1", "table-2", 7), {
            "base_id": "base-1", "table_id": "table-2", "limit": 7,
        }),
    )
    for tool_name, args, expected in results:
        tool = server._tool_manager.get_tool(tool_name)
        assert tool.output_schema is None
        result = tool.fn(*args)
        assert json.loads(result) == expected
        assert result == json.dumps(expected, ensure_ascii=False, separators=(",", ":"))

    content = asyncio.run(server.call_tool(
        "read_dingtalk_aitable_records",
        {"base_id": "base-1", "table_id": "table-2", "limit": 7},
    ))
    assert len(content) == 1
    assert json.loads(content[0].text) == {
        "base_id": "base-1", "table_id": "table-2", "limit": 7,
    }
    assert FakeDwsClient.calls == [
        ("base", "base-1"),
        ("tables", "base-1", ["table-2"]),
        ("records", "base-1", "table-2", 7),
        ("records", "base-1", "table-2", 7),
    ]


@pytest.mark.parametrize(
    ("tool_name", "args", "message"),
    [
        ("read_dingtalk_aitable_base", ("",), "base ID"),
        ("read_dingtalk_aitable_tables", ("base-1", [""]), "table IDs"),
        ("read_dingtalk_aitable_records", ("base-1", "", 1), "base and table IDs"),
        ("read_dingtalk_aitable_records", ("base-1", "table-1", 0), "positive integer"),
        ("read_dingtalk_aitable_records", ("base-1", "table-1", True), "positive integer"),
    ],
)
def test_aitable_read_tools_reject_invalid_ids_and_limits_before_dws(
    monkeypatch, tool_name, args, message,
):
    class UnexpectedDwsClient:
        def __init__(self):
            pytest.fail("invalid AITable arguments must not invoke DWS")

    monkeypatch.setattr("app.dws_client.DwsClient", UnexpectedDwsClient)
    server = agent_cli.build_role_server("consumer")
    tool = server._tool_manager.get_tool(tool_name)

    with pytest.raises(ValueError, match=message):
        tool.fn(*args)
