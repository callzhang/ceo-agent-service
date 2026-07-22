from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.channels import DingTalkCliAdapter, FeishuCliAdapter, enqueue_channel_messages
from app.channels.feishu import parse_feishu_messages
from app.channels.models import ChannelMessage
from app.store import AutoReplyStore


def test_channel_message_single_chat() -> None:
    message = ChannelMessage(
        channel="feishu",
        conversation_id="chat-1",
        conversation_type="direct",
        message_id="msg-1",
        sent_at="2026-07-23 10:00:00",
        sender_display="Alex",
        text="hello",
    )

    assert message.single_chat is True


def test_feishu_doctor_blocks_when_command_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda binary: None)

    status = FeishuCliAdapter(binary="missing-lark").doctor()

    assert status.status == "blocked"
    assert status.reason == "missing-lark command not found"


def test_feishu_doctor_ready_via_help_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="usage", stderr="")

    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/bin/{binary}")

    status = FeishuCliAdapter(binary="lark", runner=fake_runner).doctor()

    assert status.status == "ready"
    assert commands == [["lark", "--help"]]


def test_dingtalk_doctor_ready_via_dws_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 5
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/bin/{binary}")

    status = DingTalkCliAdapter(binary="dws", runner=fake_runner).doctor()

    assert status.status == "ready"
    assert status.reason == "DingTalk DWS auth status completed"
    assert commands == [["dws", "auth", "status", "--format", "json", "--timeout", "5"]]


def test_dingtalk_doctor_failed_via_dws_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="not logged in")

    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/bin/{binary}")

    status = DingTalkCliAdapter(binary="dws", runner=fake_runner).doctor()

    assert status.status == "failed"
    assert status.reason == "not logged in"


def test_parse_feishu_recent_messages_simple_shape() -> None:
    messages = parse_feishu_messages(
        """
        {
          "messages": [
            {
              "conversation": {"id": "chat-1", "title": "Ops", "type": "group"},
              "message_id": "msg-1",
              "sent_at": "2026-07-23T10:00:00+08:00",
              "sender": {"display_name": "Mina"},
              "text": "@Derek check"
            }
          ]
        }
        """
    )

    assert messages == [
        ChannelMessage(
            channel="feishu",
            conversation_id="chat-1",
            conversation_title="Ops",
            conversation_type="group",
            message_id="msg-1",
            sent_at="2026-07-23T10:00:00+08:00",
            sender_display="Mina",
            text="@Derek check",
            raw_json={
                "conversation": {"id": "chat-1", "title": "Ops", "type": "group"},
                "message_id": "msg-1",
                "sent_at": "2026-07-23T10:00:00+08:00",
                "sender": {"display_name": "Mina"},
                "text": "@Derek check",
            },
        )
    ]


def test_parse_feishu_recent_messages_maps_lark_p2p_to_direct() -> None:
    messages = parse_feishu_messages(
        '[{"conversation_id":"chat-1","conversation_type":"p2p",'
        '"message_id":"msg-1","sent_at":"2026-07-23 10:00:00",'
        '"sender_display":"Mina","text":"hello"}]'
    )

    assert messages[0].conversation_type == "direct"
    assert messages[0].single_chat is True


def test_parse_feishu_recent_messages_fails_clearly() -> None:
    with pytest.raises(ValueError, match="missing required fields: conversation_id"):
        parse_feishu_messages(
            '[{"message_id": "msg-1", "sent_at": "now", "sender_display": "Mina"}]'
        )


def test_parse_feishu_recent_messages_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="Feishu CLI output is not JSON"):
        parse_feishu_messages("not json")


def test_feishu_list_recent_messages_uses_explicit_json_command() -> None:
    commands: list[list[str]] = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '[{"conversation_id":"chat-1","conversation_title":"Ops",'
                '"conversation_type":"group","message_id":"msg-1",'
                '"sent_at":"2026-07-23 10:00:00","sender_display":"Mina",'
                '"text":"hello"}]'
            ),
            stderr="",
        )

    messages = FeishuCliAdapter(binary="lark", runner=fake_runner).list_recent_messages(
        limit=3
    )

    assert commands == [
        ["lark", "message", "list", "--recent", "--limit", "3", "--json"]
    ]
    assert messages[0].conversation_id == "chat-1"


def test_feishu_list_recent_messages_times_out_clearly() -> None:
    def fake_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=30)

    with pytest.raises(RuntimeError, match="Feishu CLI list command timed out"):
        FeishuCliAdapter(binary="lark", runner=fake_runner).list_recent_messages()


def test_feishu_send_reply_blocks_without_live_send_and_redacts_text() -> None:
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    result = FeishuCliAdapter(
        binary="lark", runner=fake_runner, live_send_enabled=False
    ).send_reply(conversation_id="chat-1", text="reply")

    assert result.status == "blocked"
    assert "CEO_FEISHU_LIVE_SEND_ENABLED=1" in result.reason
    assert result.command == [
        "lark",
        "message",
        "send",
        "--chat-id",
        "chat-1",
        "--text",
        "[redacted]",
        "--json",
    ]
    assert "reply" not in result.model_dump_json()
    assert calls == []


def test_feishu_send_reply_executes_real_text_but_returns_redacted_command() -> None:
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")

    result = FeishuCliAdapter(
        binary="lark", runner=fake_runner, live_send_enabled=True
    ).send_reply(conversation_id="chat-1", text="sensitive reply")

    assert calls == [
        [
            "lark",
            "message",
            "send",
            "--chat-id",
            "chat-1",
            "--text",
            "sensitive reply",
            "--json",
        ]
    ]
    assert result.status == "ready"
    assert result.command == [
        "lark",
        "message",
        "send",
        "--chat-id",
        "chat-1",
        "--text",
        "[redacted]",
        "--json",
    ]
    assert "sensitive reply" not in result.model_dump_json()


def test_feishu_send_reply_returns_redacted_command_on_failure() -> None:
    def fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    result = FeishuCliAdapter(
        binary="lark", runner=fake_runner, live_send_enabled=True
    ).send_reply(conversation_id="chat-1", text="sensitive reply")

    assert result.status == "failed"
    assert result.reason == "boom"
    assert "sensitive reply" not in result.model_dump_json()


def test_feishu_send_reply_returns_redacted_command_on_timeout() -> None:
    def fake_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=30)

    result = FeishuCliAdapter(
        binary="lark", runner=fake_runner, live_send_enabled=True
    ).send_reply(conversation_id="chat-1", text="sensitive reply")

    assert result.status == "failed"
    assert result.reason == "Feishu CLI send command timed out"
    assert "sensitive reply" not in result.model_dump_json()


def test_enqueue_channel_messages_is_idempotent_and_channel_scoped(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    adapter = FeishuCliAdapter(binary="lark", live_send_enabled=False)
    messages = [
        ChannelMessage(
            channel="feishu",
            conversation_id="shared",
            conversation_title="Feishu Shared",
            conversation_type="group",
            message_id="same-message",
            sent_at="2026-07-23 10:00:00",
            sender_display="Mina",
            text="hello",
        )
    ]

    assert enqueue_channel_messages(store, adapter, messages) == 1
    assert enqueue_channel_messages(store, adapter, messages) == 0
    assert store.enqueue_reply_task(
        channel="dingtalk",
        conversation_id="shared",
        conversation_title="DingTalk Shared",
        single_chat=False,
        trigger_message_id="same-message",
        trigger_create_time="2026-07-23 10:00:00",
        trigger_sender="Mina",
        trigger_text="hello",
    )

    assert store.count_reply_tasks(channel="feishu") == 1
    assert store.count_reply_tasks(channel="dingtalk") == 1
