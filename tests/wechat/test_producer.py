from datetime import datetime, timedelta

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


def _after(timestamp: str, seconds: int = 1) -> str:
    return (datetime.fromisoformat(timestamp) + timedelta(seconds=seconds)).isoformat()


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
                         trigger_mode="mention_current_account",
                         last_active_at="2026-07-17T09:00:00+00:00"),
        WechatReplyScope(account_id="acct-1", target_type="direct", target_id="u9",
                         conversation_id="u9", display_name="Alex",
                         trigger_mode="every_inbound_text",
                         last_active_at="2026-07-17T09:00:00+00:00"),
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


@pytest.mark.parametrize("target_type", ["direct", "group"])
def test_first_activation_establishes_watermark_and_does_not_replay_history(
    target_type, store, reader, account,
):
    target_id = "u9" if target_type == "direct" else "g1"
    trigger_mode = (
        "every_inbound_text" if target_type == "direct"
        else "mention_current_account"
    )
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(
            account_id="acct-1", target_type=target_type, target_id=target_id,
            conversation_id=target_id, display_name="Target",
            trigger_mode=trigger_mode,
        ),
    ])
    reader.messages = (
        [direct_message("d1", text="historical")]
        if target_type == "direct"
        else [group_message("m1", text="@Derek historical", mentioned_user_ids=["self-1"])]
    )

    scope = store.get_wechat_reply_scope("acct-1", target_type, target_id)
    assert scope.last_active_at
    assert WechatReplyProducer(
        store, reader, account, self_user_id="self-1"
    ).run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 0


@pytest.mark.parametrize("target_type", ["direct", "group"])
def test_message_after_activation_is_processed_once_and_advances_watermark(
    target_type, store, reader, account,
):
    target_id = "u9" if target_type == "direct" else "g1"
    trigger_mode = (
        "every_inbound_text" if target_type == "direct"
        else "mention_current_account"
    )
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(
            account_id="acct-1", target_type=target_type, target_id=target_id,
            conversation_id=target_id, display_name="Target",
            trigger_mode=trigger_mode,
        ),
    ])
    baseline = store.get_wechat_reply_scope(
        "acct-1", target_type, target_id
    ).last_active_at
    sent_at = _after(baseline)
    if target_type == "direct":
        message = direct_message("d1", text="new").model_copy(update={"sent_at": sent_at})
    else:
        message = group_message(
            "m1", text="@Derek new", mentioned_user_ids=["self-1"]
        ).model_copy(update={"sent_at": sent_at})
    reader.messages = [message]
    producer = WechatReplyProducer(store, reader, account, self_user_id="self-1")

    assert producer.run_once() == 1
    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 1
    assert store.get_wechat_reply_scope(
        "acct-1", target_type, target_id
    ).last_active_at == sent_at


@pytest.mark.parametrize("target_type", ["direct", "group"])
def test_failed_batch_does_not_advance_scope_watermark(
    target_type, store, reader, account, monkeypatch,
):
    target_id = "u9" if target_type == "direct" else "g1"
    trigger_mode = (
        "every_inbound_text" if target_type == "direct"
        else "mention_current_account"
    )
    baseline = "2026-07-20T09:00:00+08:00"
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(
            account_id="acct-1", target_type=target_type, target_id=target_id,
            conversation_id=target_id, display_name="Target",
            trigger_mode=trigger_mode, last_active_at=baseline,
        ),
    ])
    sent_at = _after(baseline)
    if target_type == "direct":
        message = direct_message("d1", text="new").model_copy(update={"sent_at": sent_at})
    else:
        message = group_message(
            "m1", text="@Derek new", mentioned_user_ids=["self-1"]
        ).model_copy(update={"sent_at": sent_at})
    reader.messages = [message]
    monkeypatch.setattr(
        store, "enqueue_reply_task",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    with pytest.raises(RuntimeError, match="db unavailable"):
        WechatReplyProducer(
            store, reader, account, self_user_id="self-1"
        ).run_once()
    assert store.get_wechat_reply_scope(
        "acct-1", target_type, target_id
    ).last_active_at == baseline
