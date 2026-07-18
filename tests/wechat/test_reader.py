from app.wechat.reader import WechatReader
from tests.wechat.fakes import (
    FakeCipherBackend, StaticTestKeyProvider, UnavailableTestKeyProvider,
)


def test_reader_is_blocked_without_key_provider(fake_account):
    reader = WechatReader(
        FakeCipherBackend(), UnavailableTestKeyProvider("no validated provider"),
    )
    capability = reader.probe(fake_account)
    assert capability.status == "blocked"
    assert capability.reason == "no validated provider"


def test_reader_is_blocked_on_empty_schema(fake_account):
    reader = WechatReader(FakeCipherBackend(tables=[]), StaticTestKeyProvider(b"k"))
    assert reader.probe(fake_account).status == "blocked"


def test_reader_ready_with_schema(fake_account):
    reader = WechatReader(FakeCipherBackend(tables=["Msg_x"]), StaticTestKeyProvider(b"k"))
    assert reader.probe(fake_account).status == "ready"


def test_reader_normalizes_exact_group_mentions(fake_account):
    backend = FakeCipherBackend(
        tables=["Msg_g1"],
        rows=[{
            "message_id": "m1", "conversation_id": "g1", "sender_id": "u1",
            "sender_name": "Mina", "direction": "inbound",
            "sent_at": "2026-07-17T10:00:00+08:00", "kind": "text",
            "text": "@Derek hi", "mentioned_user_ids": ["self-1"],
            "conversation_type": "group",
        }],
    )
    reader = WechatReader(backend, StaticTestKeyProvider(b"secret"))
    messages = reader.read_messages(fake_account, conversation_id="g1", limit=100)
    assert len(messages) == 1
    assert messages[0].mentioned_user_ids == frozenset({"self-1"})
    assert messages[0].mentions_user("self-1") is True
    assert messages[0].source_version == "4.1.10"
