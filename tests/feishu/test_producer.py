from datetime import datetime, timezone

from app.feishu.producer import FeishuReplyProducer
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeSdkMessage


NOW = datetime(2026, 7, 22, 3, 20, tzinfo=timezone.utc)


def _producer(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    producer = FeishuReplyProducer(
        store,
        app_id="cli_test",
        stale_event_seconds=300,
        now=lambda: NOW,
    )
    return store, producer


def _sdk(**updates):
    message = FakeSdkMessage(create_time=str(int(NOW.timestamp() * 1000)))
    for key, value in updates.items():
        setattr(message, key, value)
    return message


def test_unknown_target_is_discovered_pending_without_body(tmp_path):
    store, producer = _producer(tmp_path)
    result = producer.ingest_sdk_message(_sdk())
    assert result.decision.reason == "scope_pending"
    scope = store.get_feishu_reply_scope("cli_test", "group", "oc_1")
    assert scope is not None and not scope.enabled
    event = store.get_feishu_event("evt_1")
    assert event.body_text == ""
    assert store.count_reply_tasks(channel="feishu") == 0


def test_approved_group_mention_enqueues_atomically(tmp_path):
    store, producer = _producer(tmp_path)
    producer.ingest_sdk_message(_sdk())
    store.review_feishu_reply_scope(
        "cli_test", "group", "oc_1", approved=True, approved_by="local-user"
    )
    result = producer.ingest_sdk_message(
        _sdk(message_id="om_2", raw={"header": {"event_id": "evt_2"}})
    )
    assert result.enqueued
    assert store.get_feishu_event("evt_2").body_text == "请看一下"
    tasks = store.list_reply_tasks(channel="feishu")
    assert [task.trigger_message_id for task in tasks] == ["om_2"]


def test_visible_name_without_structured_mention_never_enqueues(tmp_path):
    store, producer = _producer(tmp_path)
    producer.ingest_sdk_message(_sdk())
    store.review_feishu_reply_scope(
        "cli_test", "group", "oc_1", approved=True, approved_by="local-user"
    )
    result = producer.ingest_sdk_message(
        _sdk(
            message_id="om_2",
            raw={"header": {"event_id": "evt_2"}},
            mentioned_bot=False,
            body_text="@CEO Agent hi",
        )
    )
    assert result.decision.reason == "bot_not_mentioned"
    assert store.get_feishu_event("evt_2").body_text == ""
    assert store.count_reply_tasks(channel="feishu") == 0


def test_duplicate_event_and_message_do_not_duplicate_task(tmp_path):
    store, producer = _producer(tmp_path)
    producer.ingest_sdk_message(_sdk())
    store.review_feishu_reply_scope(
        "cli_test", "group", "oc_1", approved=True, approved_by="local-user"
    )
    message = _sdk(message_id="om_2", raw={"header": {"event_id": "evt_2"}})
    producer.ingest_sdk_message(message)
    producer.ingest_sdk_message(message)
    assert store.count_reply_tasks(channel="feishu") == 1
