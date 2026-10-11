import pytest
from pydantic import ValidationError

from app.dws_client import DwsClient
from app.group_discovery import DingTalkGroupDiscoveryProvider, GroupRef


def message_row(message_id, text):
    return {
        "conversationId": "group-1",
        "messageId": message_id,
        "sender": "Participant",
        "senderId": "sender-1",
        "createTime": "2026-10-06 12:00:00",
        "content": "" if text is None else text,
        "text": text,
        "sendType": "user",
        "messageAiSendFlag": "DWS",
    }


def ledger(rows):
    return {
        "contractVersion": "im.message-list.v1",
        "messages": rows,
        "complete": True,
        "hasMore": False,
        "failures": [],
    }


@pytest.mark.parametrize("text", [None, "", "null", "Discussion evidence"])
def test_typed_ledger_preserves_absent_text_and_message_identity(text):
    row = message_row("message-1", text)
    messages = DwsClient.parse_messages(ledger([row]), "Group", False)

    assert len(messages) == 1
    assert messages[0].content == ("" if text is None else text)
    assert messages[0].open_message_id == row["messageId"]
    assert messages[0].open_conversation_id == row["conversationId"]
    assert messages[0].raw_payload == row
    assert row["text"] == text


def test_group_discovery_reads_full_batch_with_textless_messages(monkeypatch):
    rows = [message_row("empty-1", None), message_row("text-1", "Evidence"),
            message_row("empty-2", None)]
    client = DwsClient()
    monkeypatch.setattr(client, "run_json", lambda command: ledger(rows))
    provider = DingTalkGroupDiscoveryProvider(client)
    group = GroupRef(provider.scope, "group-1", "Group")

    messages = provider.read_recent_group_messages(group, limit=30)

    assert [message.text_excerpt for message in messages] == ["", "Evidence", ""]


@pytest.mark.parametrize("text", [123, {"unverified": "content"}, ["content"]])
def test_typed_ledger_rejects_nontext_values(text):
    with pytest.raises(ValidationError):
        DwsClient.parse_messages(ledger([message_row("message-1", text)]), "Group", False)
