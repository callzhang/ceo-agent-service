"""The native chat reader must use the task's stable direct-chat identity."""

import sqlite3

import pytest

from app.agent_cli import build_role_server
from app.dingtalk_models import DingTalkMessage
from app.dws_client import DwsClient
from app.store import AutoReplyStore, REPLY_TASK_SQL


def _bound_direct_task(tmp_path, *, source_conversation="cid-direct", source_message="msg-trigger"):
    db_path = tmp_path / "service.sqlite3"
    store = AutoReplyStore(db_path)
    source = DingTalkMessage(
        open_conversation_id=source_conversation,
        open_message_id=source_message,
        conversation_title="",
        single_chat=True,
        sender_name="Colleague",
        sender_open_dingtalk_id="open-colleague",
        create_time="2026-10-21 10:00:00",
        content="Please check this request",
    )
    assert store.enqueue_reply_task(
        conversation_id="cid-direct", conversation_title="", single_chat=True,
        trigger_message_id="msg-trigger", trigger_create_time=source.create_time,
        trigger_sender=source.sender_name, trigger_text=source.content,
        trigger_message_json=source.model_dump_json(),
        execution_generation="generation-one",
    )
    with sqlite3.connect(db_path) as db:
        task_id = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where conversation_id='cid-direct'"
        ).fetchone()[0]
    assert store.claim_reply_task(task_id) is not None
    return db_path, task_id, source


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("single_chat", [False, True])
def test_bound_direct_reader_uses_trigger_identity_when_model_supplies_only_conversation(
    tmp_path, monkeypatch, role, single_chat,
):
    db_path, task_id, source = _bound_direct_task(tmp_path)
    calls = []

    def read_recent(self, conversation, *, limit):
        calls.append((conversation, limit))
        return [source]

    monkeypatch.setattr(DwsClient, "read_recent_messages", read_recent)
    tool = build_role_server(
        role, task_id=task_id, db_path=db_path,
        execution_generation="generation-one",
    )._tool_manager.get_tool("read_dingtalk_messages").fn
    result = tool("cid-direct", single_chat=single_chat, limit=30)

    assert len(result["messages"]) == 1
    assert calls[0][0].direct_open_dingtalk_id == "open-colleague"
    assert calls[0][0].single_chat is True
    assert calls[0][0].open_conversation_id == "cid-direct"
    assert calls[0][1] == 30


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_bound_direct_window_reads_complete_source_conversation(tmp_path, monkeypatch, role):
    db_path, task_id, source = _bound_direct_task(tmp_path)
    calls = []

    def read_complete(self, conversation, start, end):
        calls.append((conversation, start, end))
        return [source]

    monkeypatch.setattr(DwsClient, "read_complete_message_window", read_complete)
    tool = build_role_server(
        role, task_id=task_id, db_path=db_path,
        execution_generation="generation-one",
    )._tool_manager.get_tool("read_dingtalk_messages_in_window").fn
    result = tool("cid-direct", "2026-10-21T02:00:00Z", "2026-10-21T03:00:00Z")

    assert result["complete"] is True
    assert len(result["messages"]) == 1
    assert calls[0][0].direct_open_dingtalk_id == "open-colleague"
    assert calls[0][0].single_chat is True
    assert calls[0][1:] == ("2026-10-21T02:00:00Z", "2026-10-21T03:00:00Z")


def test_bound_task_keeps_unrelated_group_window_command(tmp_path, monkeypatch):
    db_path, task_id, _ = _bound_direct_task(tmp_path)
    commands = []
    monkeypatch.setattr(
        DwsClient, "run_json",
        lambda self, command: commands.append(command) or {"complete": True, "messages": []},
    )
    tool = build_role_server(
        "audit", task_id=task_id, db_path=db_path,
        execution_generation="generation-one",
    )._tool_manager.get_tool("read_dingtalk_messages_in_window").fn

    result = tool("cid-unrelated-group", "2026-10-21T02:00:00Z", "2026-10-21T03:00:00Z")

    assert result == {"complete": True, "messages": []}
    assert commands[0][1:4] == ["chat", "+chat-messages", "--group"]
    assert commands[0][4] == "cid-unrelated-group"


@pytest.mark.parametrize("source_conversation,source_message", [
    ("cid-other", "msg-trigger"), ("cid-direct", "msg-other"),
])
def test_bound_direct_reader_rejects_mismatched_trigger_before_provider(
    tmp_path, monkeypatch, source_conversation, source_message,
):
    db_path, task_id, _ = _bound_direct_task(
        tmp_path, source_conversation=source_conversation,
        source_message=source_message,
    )
    calls = []
    monkeypatch.setattr(DwsClient, "read_recent_messages", lambda *args, **kwargs: calls.append(1))
    tool = build_role_server(
        "audit", task_id=task_id, db_path=db_path,
        execution_generation="generation-one",
    )._tool_manager.get_tool("read_dingtalk_messages").fn

    with pytest.raises(ValueError, match="trigger identity"):
        tool("cid-direct", single_chat=True)
    assert calls == []


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
