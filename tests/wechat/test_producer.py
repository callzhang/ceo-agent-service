import pytest

from app.store import AutoReplyStore
from app.wechat.models import WechatAccount, WechatMessage, WechatReplyScope
from app.wechat.producer import WechatReplyProducer, is_reply_candidate


class FakeReader:
    def __init__(self):
        self.messages: list[WechatMessage] = []

    def read_messages(self, account, *, conversation_id, conversation_type, since, limit):
        del account, conversation_type, since
        return [m for m in self.messages if m.conversation_id == conversation_id][:limit]


def group_message(mid, *, text, mentioned_user_ids):
    return WechatMessage(
        account_id="acct-1", conversation_id="g1", message_id=mid,
        sender_id="u1", sender_display_name="Mina", conversation_type="group",
        direction="inbound", sent_at=f"2026-07-17T10:00:0{mid[-1]}", kind="text",
        text=text, mentioned_user_ids=mentioned_user_ids, source_version="4.1.10",
    )


def direct_message(mid, *, text, direction="inbound", kind="text"):
    return WechatMessage(
        account_id="acct-1", conversation_id="u9", message_id=mid,
        sender_id="u9", sender_display_name="Alex", conversation_type="direct",
        direction=direction, sent_at=f"2026-07-17T11:00:0{mid[-1]}", kind=kind,
        text=text, source_version="4.1.10",
    )


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "w.sqlite3")


@pytest.fixture
def account():
    return WechatAccount(account_id="acct-1", display_name="derek", self_user_id="self-1",
                         account_dir="/a", db_dir="/a/db_storage", app_version="4.1.10")


@pytest.fixture
def reader():
    return FakeReader()


@pytest.fixture
def producer(store, reader, account):
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(account_id="acct-1", target_type="group", target_id="g1",
                         conversation_id="g1", display_name="CEO group",
                         trigger_mode="mention_current_account"),
        WechatReplyScope(account_id="acct-1", target_type="direct", target_id="u9",
                         conversation_id="u9", display_name="Alex",
                         trigger_mode="every_inbound_text"),
    ])
    return WechatReplyProducer(store, reader, account, self_user_id="self-1")


def test_selected_group_requires_structured_self_mention(producer, reader, store):
    reader.messages = [
        group_message("m1", text="Derek 看下", mentioned_user_ids=[]),
        group_message("m2", text="@Derek 看下", mentioned_user_ids=["self-1"]),
    ]
    assert producer.run_once() == 1
    tasks = store.list_reply_tasks(channel="wechat")
    assert [t.trigger_message_id for t in tasks] == ["m2"]


def test_direct_replies_to_every_inbound_text(producer, reader, store):
    reader.messages = [direct_message("d1", text="hi"), direct_message("d2", text="hello")]
    assert producer.run_once() == 2
    assert store.count_reply_tasks(channel="wechat") == 2


def test_outbound_and_nontext_ignored(producer, reader):
    reader.messages = [
        direct_message("d3", text="mine", direction="outbound"),
        direct_message("d4", text="", kind="image"),
    ]
    assert producer.run_once() == 0


def test_repeated_scan_does_not_duplicate_wechat_task(producer, reader, store):
    reader.messages = [group_message("m2", text="@Derek", mentioned_user_ids=["self-1"])]
    assert producer.run_once() == 1
    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 1


def test_is_reply_candidate_pure_rules():
    scope_direct = WechatReplyScope(account_id="a", target_type="direct", target_id="u",
                                    display_name="U", trigger_mode="every_inbound_text")
    assert is_reply_candidate(direct_message("d1", text="x"), scope_direct, self_user_id="self-1")
    assert not is_reply_candidate(direct_message("d1", text="x", direction="outbound"),
                                  scope_direct, self_user_id="self-1")
