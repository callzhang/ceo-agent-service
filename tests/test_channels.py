from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.channels import DingTalkCliAdapter, FeishuCliAdapter, enqueue_channel_messages
from app.channels.feishu import official_bot_doctor, parse_feishu_messages
from app.channels.models import ChannelMessage
from app.feishu.models import FeishuInboundMessage
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
    child_environments: list[dict[str, str]] = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        child_environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, stdout="usage", stderr="")

    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setenv("CEO_FEISHU_APP_SECRET", "must-not-reach-cli")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-cli")

    status = FeishuCliAdapter(binary="lark", runner=fake_runner).doctor()

    assert status.status == "ready"
    assert commands == [["lark", "--help"]]
    assert "CEO_FEISHU_APP_SECRET" not in child_environments[0]
    assert "OPENAI_API_KEY" not in child_environments[0]


def test_official_bot_doctor_is_distinct_and_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr("app.config.feishu_app_secret", lambda: "secret")

    status = official_bot_doctor()

    assert status.channel == "feishu_bot"
    assert status.command == ["ceo-agent", "feishu", "doctor"]
    assert status.reason.startswith("offline official Bot check:")


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
            channel="feishu_cli",
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


@pytest.mark.parametrize("live_send_enabled", [False, True])
def test_feishu_cli_send_reply_is_permanently_blocked_and_redacted(
    live_send_enabled: bool,
) -> None:
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    result = FeishuCliAdapter(
        binary="lark", runner=fake_runner, live_send_enabled=live_send_enabled
    ).send_reply(conversation_id="chat-1", text="reply")

    assert result.status == "blocked"
    assert "official Bot delivery pipeline" in result.reason
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


def test_enqueue_channel_messages_is_idempotent_and_channel_scoped(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    adapter = FeishuCliAdapter(binary="lark", live_send_enabled=False)
    messages = [
        ChannelMessage(
            channel="feishu_cli",
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

    assert store.count_reply_tasks(channel="feishu_cli") == 1
    assert store.count_reply_tasks(channel="dingtalk") == 1


def test_feishu_cli_task_isolated_from_dingtalk_and_official_feishu_claims(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    adapter = FeishuCliAdapter(binary="lark", live_send_enabled=False)
    messages = [
        ChannelMessage(
            channel="feishu_cli",
            conversation_id="chat-1",
            conversation_title="Feishu CLI",
            conversation_type="direct",
            message_id="msg-1",
            sent_at="2026-07-23 10:00:00",
            sender_display="Mina",
            text="hello",
        )
    ]

    assert enqueue_channel_messages(store, adapter, messages) == 1

    # The default claim is the DingTalk worker namespace. The official Bot
    # consumer independently claims only channel="feishu".
    assert store.claim_reply_tasks(limit=1) == []
    assert store.claim_reply_tasks(limit=1, channel="feishu") == []

    [pending] = store.list_reply_tasks(channel="feishu_cli")
    assert pending.status == "pending"
    assert pending.attempts == 0

    [claimed] = store.claim_reply_tasks(limit=1, channel="feishu_cli")
    assert claimed.id == pending.id
    assert claimed.channel == "feishu_cli"
    assert claimed.status == "processing"
    assert claimed.attempts == 1


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_legacy_feishu_cli_unfinished_task_migrates_to_cli_namespace(
    tmp_path: Path,
    status: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    message = ChannelMessage(
        channel="feishu",
        conversation_id="legacy-chat",
        conversation_title="Legacy CLI",
        conversation_type="group",
        message_id=f"legacy-{status}",
        sent_at="2026-07-22 10:00:00",
        sender_display="Mina",
        text="legacy message",
        raw_json={"id": f"legacy-{status}", "source": "lark-cli"},
    )
    assert store.enqueue_reply_task(
        channel="feishu",
        conversation_id=message.conversation_id,
        conversation_title=message.conversation_title,
        single_chat=message.single_chat,
        trigger_message_id=message.message_id,
        trigger_create_time=message.sent_at,
        trigger_sender=message.sender_display,
        trigger_text=message.text,
        trigger_message_json=message.model_dump_json(),
    )
    if status == "processing":
        with store._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='processing', attempts=1, lease_token='old-lease',
                    locked_at='2026-07-22 10:01:00'
                where channel='feishu' and trigger_message_id=?
                """,
                (message.message_id,),
            )

    store._initialize()

    assert store.get_reply_task_for_message(
        message.conversation_id,
        message.message_id,
        channel="feishu",
    ) is None
    migrated = store.get_reply_task_for_message(
        message.conversation_id,
        message.message_id,
        channel="feishu_cli",
    )
    assert migrated is not None
    assert migrated.status == status
    assert store.claim_reply_tasks(limit=10, channel="feishu") == []


def test_recoverable_failed_legacy_cli_task_never_enters_official_queue(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    message = ChannelMessage(
        channel="feishu",
        conversation_id="legacy-failed-chat",
        conversation_title="Legacy failed CLI",
        conversation_type="group",
        message_id="legacy-failed-message",
        sent_at="2026-07-22 10:00:00",
        sender_display="Mina",
        text="legacy failed message",
        raw_json={"id": "legacy-failed-message", "source": "lark-cli"},
    )
    assert store.enqueue_reply_task(
        channel="feishu",
        conversation_id=message.conversation_id,
        conversation_title=message.conversation_title,
        single_chat=message.single_chat,
        trigger_message_id=message.message_id,
        trigger_create_time=message.sent_at,
        trigger_sender=message.sender_display,
        trigger_text=message.text,
        trigger_message_json=message.model_dump_json(),
    )
    [claimed_legacy] = store.claim_reply_tasks(limit=1, channel="feishu")
    store.fail_reply_task(
        claimed_legacy.id,
        f"codex session locked: {message.conversation_id}",
    )

    store._initialize()

    migrated = store.get_reply_task_for_message(
        message.conversation_id,
        message.message_id,
        channel="feishu_cli",
    )
    assert migrated is not None
    assert migrated.status == "failed"
    recovered = store.reset_recoverable_reply_tasks()
    assert [task.id for task in recovered] == [migrated.id]
    assert store.claim_reply_tasks(limit=10, channel="feishu") == []
    [claimed_cli] = store.claim_reply_tasks(limit=1, channel="feishu_cli")
    assert claimed_cli.id == migrated.id
    assert claimed_cli.status == "processing"


def test_legacy_feishu_cli_migration_preserves_official_durable_task(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event = store.record_feishu_event(
        FeishuInboundMessage(
            event_id="evt-official",
            app_id="cli-official",
            message_id="om-official",
            chat_id="oc-official",
            chat_type="group",
            chat_title="Official Bot",
            sender_open_id="ou-official",
            sender_name="Alex",
            message_type="text",
            mentioned_bot=True,
            body_text="official message",
            event_create_time="2026-07-22T10:00:00+08:00",
            received_at="2026-07-22T10:00:01+08:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    [official_before] = store.list_reply_tasks(channel="feishu")
    legacy = ChannelMessage(
        channel="feishu",
        conversation_id="legacy-chat",
        conversation_title="Legacy CLI",
        conversation_type="direct",
        message_id="legacy-message",
        sent_at="2026-07-22 10:01:00",
        sender_display="Mina",
        text="legacy message",
        raw_json={"id": "legacy-message"},
    )
    assert store.enqueue_reply_task(
        channel="feishu",
        conversation_id=legacy.conversation_id,
        conversation_title=legacy.conversation_title,
        single_chat=legacy.single_chat,
        trigger_message_id=legacy.message_id,
        trigger_create_time=legacy.sent_at,
        trigger_sender=legacy.sender_display,
        trigger_text=legacy.text,
        trigger_message_json=legacy.model_dump_json(),
    )

    store._initialize()

    [official_after] = store.list_reply_tasks(channel="feishu")
    assert official_after == official_before
    assert official_after.id == event.reply_task_id
    [claimed] = store.claim_reply_tasks(limit=10, channel="feishu")
    assert claimed.id == event.reply_task_id
    assert claimed.trigger_message_id == "om-official"
    assert store.get_reply_task_for_message(
        legacy.conversation_id,
        legacy.message_id,
        channel="feishu_cli",
    ) is not None


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_unbound_normalized_feishu_trigger_is_quarantined(
    tmp_path: Path,
    status: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger = FeishuInboundMessage(
        event_id=f"evt-unbound-{status}",
        app_id="cli-unbound",
        message_id=f"om-unbound-{status}",
        chat_id="oc-unbound",
        chat_type="group",
        chat_title="Unbound Official Shape",
        sender_open_id="ou-unbound",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=True,
        body_text="normalized but not durably bound",
        event_create_time="2026-07-22T10:00:00+08:00",
        received_at="2026-07-22T10:00:01+08:00",
    )
    conversation_id = AutoReplyStore._feishu_task_conversation_id(
        trigger.app_id,
        trigger.chat_id,
    )
    assert store.enqueue_reply_task(
        channel="feishu",
        conversation_id=conversation_id,
        conversation_title=trigger.chat_title,
        single_chat=False,
        trigger_message_id=trigger.message_id,
        trigger_create_time=trigger.event_create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.body_text,
        trigger_message_json=trigger.model_dump_json(),
    )
    if status == "processing":
        with store._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='processing', lease_token='unbound-lease',
                    locked_at='2026-07-22 10:01:00'
                where trigger_message_id=?
                """,
                (trigger.message_id,),
            )

    store._initialize()

    assert store.claim_reply_tasks(limit=10, channel="feishu") == []
    with store._connect() as db:
        quarantined = db.execute(
            "select channel, status from reply_tasks where trigger_message_id=?",
            (trigger.message_id,),
        ).fetchone()
    assert quarantined["channel"].startswith("feishu_cli_quarantine:")
    assert quarantined["status"] == status


@pytest.mark.parametrize(
    "trigger_message_json",
    ["[]", "{", '{"message_id":"untrusted-no-channel"}'],
    ids=["non-object", "damaged-json", "missing-top-level-channel"],
)
def test_untrusted_unbound_feishu_payload_is_quarantined(
    tmp_path: Path,
    trigger_message_json: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        channel="feishu",
        conversation_id="untrusted-chat",
        conversation_title="Untrusted",
        single_chat=False,
        trigger_message_id="untrusted-message",
        trigger_create_time="2026-07-22 10:00:00",
        trigger_sender="Unknown",
        trigger_text="untrusted",
        trigger_message_json=trigger_message_json,
    )

    store._initialize()

    assert store.claim_reply_tasks(limit=10, channel="feishu") == []
    with store._connect() as db:
        quarantined = db.execute(
            "select channel from reply_tasks where trigger_message_id=?",
            ("untrusted-message",),
        ).fetchone()
    assert quarantined["channel"].startswith("feishu_cli_quarantine:")


def test_legacy_feishu_cli_terminal_task_is_not_reclassified(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    message = ChannelMessage(
        channel="feishu",
        conversation_id="completed-legacy-chat",
        conversation_title="Completed Legacy CLI",
        conversation_type="group",
        message_id="completed-legacy-message",
        sent_at="2026-07-22 10:00:00",
        sender_display="Mina",
        text="already completed",
        raw_json={"id": "completed-legacy-message"},
    )
    assert store.enqueue_reply_task(
        channel="feishu",
        conversation_id=message.conversation_id,
        conversation_title=message.conversation_title,
        single_chat=message.single_chat,
        trigger_message_id=message.message_id,
        trigger_create_time=message.sent_at,
        trigger_sender=message.sender_display,
        trigger_text=message.text,
        trigger_message_json=message.model_dump_json(),
    )
    task = store.get_reply_task_for_message(
        message.conversation_id,
        message.message_id,
        channel="feishu",
    )
    assert task is not None
    store.complete_reply_task(task.id)

    store._initialize()

    terminal = store.get_reply_task_for_message(
        message.conversation_id,
        message.message_id,
        channel="feishu",
    )
    assert terminal is not None
    assert terminal.status == "done"
    assert store.get_reply_task_for_message(
        message.conversation_id,
        message.message_id,
        channel="feishu_cli",
    ) is None


def test_ambiguous_legacy_feishu_cli_envelope_is_quarantined(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    message = ChannelMessage(
        channel="feishu",
        conversation_id="ambiguous-chat",
        conversation_title="Ambiguous CLI",
        conversation_type="group",
        message_id="ambiguous-message",
        sent_at="2026-07-22 10:00:00",
        sender_display="Mina",
        text="payload and durable columns disagree",
        raw_json={"id": "ambiguous-message"},
    )
    ambiguous_payload = message.model_dump()
    ambiguous_payload["conversation_id"] = "different-chat"
    assert store.enqueue_reply_task(
        channel="feishu",
        conversation_id=message.conversation_id,
        conversation_title=message.conversation_title,
        single_chat=message.single_chat,
        trigger_message_id=message.message_id,
        trigger_create_time=message.sent_at,
        trigger_sender=message.sender_display,
        trigger_text=message.text,
        trigger_message_json=json.dumps(ambiguous_payload),
    )

    store._initialize()

    assert store.claim_reply_tasks(limit=10, channel="feishu") == []
    assert store.list_reply_tasks(channel="feishu_cli") == []
    with store._connect() as db:
        quarantined = db.execute(
            "select channel, status from reply_tasks where trigger_message_id=?",
            (message.message_id,),
        ).fetchone()
    assert quarantined["channel"].startswith("feishu_cli_quarantine:")
    assert quarantined["status"] == "pending"
