from datetime import datetime, timedelta
import json

import pytest

from app.store import AutoReplyStore
from app.agent_cron.commands import (
    SERVICE_COMMAND_OPTIONS,
    ServiceCommandConsumerContext,
    ServiceCommandRegistry,
)
from tests.scheduled_command_results import unknown_service_result
from app.skill_features import FeatureRegistry
from app.wechat.models import WechatAccount, WechatMessage, WechatReplyScope
from app.wechat.producer import WechatReplyProducer, is_reply_candidate
from app.wechat import producer as producer_module


class FakeReader:
    def __init__(self):
        self.messages: list[WechatMessage] = []

    def read_messages(
        self, account, *, conversation_id, conversation_type, since, limit,
        until="", order="newest",
    ):
        del until
        del account, conversation_type
        messages = [
            m for m in self.messages
            if m.conversation_id == conversation_id and (not since or m.sent_at >= since)
        ]
        if order == "newest":
            return sorted(messages, key=lambda m: m.sent_at, reverse=True)[:limit]
        overlap = [m for m in messages if m.sent_at == since]
        newer = sorted(
            (m for m in messages if not since or m.sent_at > since),
            key=lambda m: m.sent_at,
        )[:limit]
        return overlap + newer


def _after(timestamp: str, seconds: int = 1) -> str:
    return (datetime.fromisoformat(timestamp) + timedelta(seconds=seconds)).isoformat()


def group_message(mid, *, text, mentioned_user_ids):
    return WechatMessage(
        account_id="acct-1", conversation_id="g1", message_id=mid,
        sender_id="u1", sender_display_name="Avery", conversation_type="group",
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


def test_scheduled_trigger_persists_consumer_prompt_and_skills(
    producer, reader, store
):
    reader.messages = [direct_message("d1", text="hello")]
    context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=8,
        prompt="使用 $ceo-wechat 处理真实微信消息。",
        skill_names=("ceo-wechat",),
        skill_protocol="TARGETED WECHAT SKILL",
    )
    implementations = {
        option.name: (lambda: unknown_service_result("unused"))
        for option in SERVICE_COMMAND_OPTIONS
    }
    implementations["wechat-produce-once"] = lambda: unknown_service_result(
        str(producer.run_once())
    )
    registry = ServiceCommandRegistry(implementations)

    registry.run("wechat-produce-once", consumer_context=context)

    [task] = store.list_reply_tasks(channel="wechat")
    payload = json.loads(task.trigger_message_json)
    assert payload["scheduled_consumer"] == context.to_payload()


def test_direct_messages_coalesce_during_the_five_minute_settle_window(producer, reader, store):
    reader.messages = [direct_message("d1", text="hi"), direct_message("d2", text="hello")]
    assert producer.run_once() == 1
    tasks = store.list_reply_tasks(channel="wechat")
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "d2"
    assert tasks[0].trigger_text == "hello"
    assert tasks[0].available_at == "2026-07-17T11:05:02"


def test_direct_chat_waits_for_observed_quiet_window_across_scans(
    producer, reader, store, monkeypatch,
):
    class Clock(datetime):
        current = datetime.fromisoformat("2026-07-17T11:04:11+00:00")

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(producer_module, "datetime", Clock)
    first = direct_message("d1", text="LinkedIn link").model_copy(
        update={"sent_at": "2026-07-17T11:00:00+00:00"}
    )
    second = direct_message("d2", text="more context").model_copy(
        update={"sent_at": "2026-07-17T11:04:32+00:00"}
    )
    reader.messages = [first]
    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 0

    reader.messages.append(second)
    Clock.current = datetime.fromisoformat("2026-07-17T11:09:10+00:00")
    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 0

    Clock.current = datetime.fromisoformat("2026-07-17T11:10:30+00:00")
    assert producer.run_once() == 1
    [task] = store.list_reply_tasks(channel="wechat")
    assert task.trigger_message_id == second.message_id
    assert producer.run_once() == 0


def test_empty_direct_text_does_not_trigger_a_reply(producer, reader, store):
    reader.messages = [direct_message("d3", text="  ")]
    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 0


def test_new_direct_burst_does_not_hold_an_earlier_settled_burst(
    producer, reader, store, monkeypatch,
):
    class Clock(datetime):
        current = datetime.fromisoformat("2026-07-17T11:09:00+00:00")

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(producer_module, "datetime", Clock)
    first = direct_message("d1", text="first topic").model_copy(
        update={"sent_at": "2026-07-17T11:00:00+00:00"}
    )
    second = direct_message("d2", text="second topic").model_copy(
        update={"sent_at": "2026-07-17T11:08:00+00:00"}
    )
    reader.messages = [first, second]

    assert producer.run_once() == 1
    assert [t.trigger_message_id for t in store.list_reply_tasks(channel="wechat")] == ["d1"]
    assert store.get_wechat_reply_scope("acct-1", "direct", "u9").last_active_at == first.sent_at

    Clock.current = datetime.fromisoformat("2026-07-17T11:14:00+00:00")
    assert producer.run_once() == 1
    assert [t.trigger_message_id for t in store.list_reply_tasks(channel="wechat")] == ["d2"]


def test_disabled_wechat_auto_reply_does_not_create_wechat_tasks(
    store, reader, account
):
    store.replace_wechat_reply_scopes(
        "acct-1",
        [
            WechatReplyScope(
                account_id="acct-1",
                target_type="direct",
                target_id="u9",
                conversation_id="u9",
                display_name="Alex",
                trigger_mode="every_inbound_text",
                last_active_at="2026-07-17T09:00:00+00:00",
            )
        ],
    )
    reader.messages = [direct_message("d1", text="hello")]
    registry = FeatureRegistry(state_path=store.path.parent / "skill-state.json")
    registry.set_enabled("wechat_auto_reply", False)

    producer = WechatReplyProducer(
        store,
        reader,
        account,
        self_user_id="self-1",
        feature_registry=registry,
    )
    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 0


def test_outbound_and_nontext_ignored(producer, reader):
    reader.messages = [
        direct_message("d3", text="mine", direction="outbound"),
        direct_message("d4", text="", kind="image"),
    ]
    assert producer.run_once() == 0


def test_direct_voice_message_is_enqueued_with_its_placeholder(producer, reader, store):
    reader.messages = [direct_message("v1", text="[语音3秒·未转写，用 save_message_audio 取音频再转写]", kind="voice")]
    assert producer.run_once() == 1
    task = store.list_reply_tasks(channel="wechat")[0]
    assert task.trigger_message_id == "v1"
    assert "save_message_audio" in task.trigger_text


def test_outbound_voice_is_ignored(producer, reader):
    reader.messages = [direct_message("v2", text="[语音3秒·未转写]", kind="voice", direction="outbound")]
    assert producer.run_once() == 0


def _self_chat_message(mid, *, kind="voice", direction="outbound", text="[语音3秒·未转写]"):
    return direct_message(mid, text=text, kind=kind, direction=direction).model_copy(
        update={"conversation_id": "filehelper", "sender_id": "self-1"}
    )


def _self_chat_scope():
    return WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="filehelper",
        conversation_id="filehelper", display_name="文件传输助手",
        trigger_mode="every_inbound_text", last_active_at="2026-07-17T09:00:00+00:00",
    )


def test_principals_own_voice_in_the_self_chat_is_enqueued(store, reader, account):
    """A voice cannot carry an @-mention; in the self chat it is the instruction."""
    store.replace_wechat_reply_scopes("acct-1", [_self_chat_scope()])
    reader.messages = [_self_chat_message("v5")]
    producer = WechatReplyProducer(store, reader, account, self_user_id="self-1")

    assert producer.run_once() == 1
    assert [t.trigger_message_id for t in store.list_reply_tasks(channel="wechat")] == ["v5"]


def test_other_outbound_messages_in_the_self_chat_stay_ignored():
    scope = _self_chat_scope()
    # the service's own replies there are text; a voice elsewhere is the principal's reply
    assert not is_reply_candidate(_self_chat_message("v6", kind="text", text="reply"),
                                  scope, self_user_id="self-1")
    elsewhere = direct_message("v7", text="[语音3秒·未转写]", kind="voice", direction="outbound")
    assert not is_reply_candidate(elsewhere, scope, self_user_id="self-1")


def test_self_chat_voice_still_needs_an_enabled_scope():
    assert not is_reply_candidate(_self_chat_message("v8"), None, self_user_id="self-1")


def test_shared_link_decoded_as_text_is_enqueued(producer, reader, store):
    reader.messages = [direct_message("d6", text="[链接]《Article》 https://example.com/a")]
    assert producer.run_once() == 1
    task = store.list_reply_tasks(channel="wechat")[0]
    assert "https://example.com/a" in task.trigger_text


def test_own_direct_message_is_never_enqueued(producer, reader, store):
    reader.messages = [
        direct_message("d5", text="sent by me", direction="outbound").model_copy(
            update={"sender_id": "self-1", "sender_display_name": "Derek"}
        )
    ]

    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 0


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
    target_type, store, reader, account, monkeypatch,
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
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            settled = datetime.fromisoformat(sent_at) + timedelta(minutes=6)
            return settled if tz is None else settled.astimezone(tz)

    monkeypatch.setattr(producer_module, "datetime", Clock)
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


def test_same_second_message_visible_on_later_scan_is_not_lost(store, reader, account):
    boundary = "2026-07-20T10:00:00+08:00"
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(
            account_id="acct-1", target_type="direct", target_id="u9",
            conversation_id="u9", display_name="Alex",
            trigger_mode="every_inbound_text", last_active_at=boundary,
        ),
    ])
    first = direct_message("d1", text="first").model_copy(update={"sent_at": boundary})
    late = direct_message("d2", text="late").model_copy(update={"sent_at": boundary})
    producer = WechatReplyProducer(store, reader, account, self_user_id="self-1")

    reader.messages = [first]
    assert producer.run_once() == 1
    reader.messages = [first, late]
    assert producer.run_once() == 1
    tasks = store.list_reply_tasks(channel="wechat")
    assert [task.trigger_message_id for task in tasks] == ["d2"]


def test_backlog_larger_than_read_limit_is_processed_oldest_page_first(
    store, reader, account,
):
    baseline = "2026-07-20T09:00:00+08:00"
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(
            account_id="acct-1", target_type="direct", target_id="u9",
            conversation_id="u9", display_name="Alex",
            trigger_mode="every_inbound_text", last_active_at=baseline,
        ),
    ])
    reader.messages = [
        direct_message(f"d{i}", text=str(i)).model_copy(
            update={"sent_at": _after(baseline, i)}
        )
        for i in range(1, 4)
    ]
    producer = WechatReplyProducer(
        store, reader, account, self_user_id="self-1", read_limit=2,
    )

    assert producer.run_once() == 1
    assert producer.run_once() == 1
    tasks = store.list_reply_tasks(channel="wechat")
    assert [task.trigger_message_id for task in tasks] == ["d3"]
