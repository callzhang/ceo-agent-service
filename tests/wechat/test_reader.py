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


def test_reader_detect_self_username_delegates_to_backend():
    from app.wechat.reader import WechatReader
    from app.wechat.models import WechatAccount
    from tests.wechat.fakes import StaticTestKeyProvider, FakeCipherBackend

    backend = FakeCipherBackend(tables=["Message"])
    backend.detect_self_username = lambda db_dir, passphrase: "derek840121"
    reader = WechatReader(backend, StaticTestKeyProvider(b"x" * 32))
    account = WechatAccount(
        account_id="a", display_name="a", self_user_id="",
        account_dir="/d", db_dir="/d/db_storage", app_version="4.1.10",
    )
    assert reader.detect_self_username(account) == "derek840121"


def test_reader_uses_account_self_id_for_direction_even_if_helper_started_without_it(
    fake_account,
):
    account = fake_account.model_copy(update={"self_user_id": "self-1"})
    backend = FakeCipherBackend(
        tables=["Msg_filehelper"],
        rows=[{
            "message_id": "m1", "conversation_id": "filehelper",
            "sender_id": "self-1", "sender_name": "Derek",
            "direction": "inbound", "sent_at": "2026-07-21T08:00:00+08:00",
            "kind": "text", "text": "note", "conversation_type": "direct",
        }],
    )
    reader = WechatReader(backend, StaticTestKeyProvider(b"secret"))

    assert reader.read_messages(account, conversation_id="filehelper")[0].direction == "outbound"
