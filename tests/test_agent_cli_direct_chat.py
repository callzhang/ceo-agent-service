"""The native chat reader must accept a stable direct-chat identity."""

import pytest

from app.agent_cli import build_role_server
from app.dws_client import DwsClient


def test_direct_chat_reader_routes_trigger_sender_identity_without_name_search(monkeypatch):
    captured = []

    class FakeDwsClient:
        def read_recent_messages(self, conversation, *, limit):
            captured.append((conversation, limit))
            return []

    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    tool = build_role_server("audit")._tool_manager.get_tool("read_dingtalk_messages")

    assert "direct_open_dingtalk_id" in tool.parameters["properties"]
    assert tool.fn(
        "cid-synthetic-direct",
        single_chat=True,
        direct_open_dingtalk_id="open-synthetic-sender",
    ) == {"messages": []}
    conversation, limit = captured.pop()
    assert conversation.open_conversation_id == "cid-synthetic-direct"
    assert conversation.single_chat is True
    assert conversation.direct_open_dingtalk_id == "open-synthetic-sender"
    assert limit == 50

    assert tool.fn(
        "cid-synthetic-direct",
        direct_open_dingtalk_id="open-synthetic-sender",
    ) == {"messages": []}
    conversation, _ = captured.pop()
    assert conversation.single_chat is True

    assert tool.fn("cid-synthetic-group", limit=12) == {"messages": []}
    conversation, limit = captured.pop()
    assert conversation.open_conversation_id == "cid-synthetic-group"
    assert conversation.single_chat is False
    assert conversation.direct_open_dingtalk_id == ""
    assert limit == 12


def test_direct_chat_read_uses_stable_identity_in_dws_command(monkeypatch):
    commands = []

    def fake_run_json(self, command):
        commands.append(command)
        return {"messages": []}

    monkeypatch.setattr(DwsClient, "run_json", fake_run_json)
    tool = build_role_server("audit")._tool_manager.get_tool("read_dingtalk_messages").fn

    assert tool(
        "cid-synthetic-direct",
        direct_open_dingtalk_id="open-synthetic-sender",
    ) == {"messages": []}
    assert len(commands) == 1
    assert commands[0][1:4] == ["chat", "message", "list-direct"]
    assert commands[0][4:6] == ["--open-dingtalk-id", "open-synthetic-sender"]

    with pytest.raises(ValueError, match="direct chat sender"):
        tool("cid-synthetic-direct", single_chat=True)
    assert len(commands) == 1
