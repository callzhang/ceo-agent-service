from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.feishu.ingress import normalize_sdk_message
from app.feishu.producer import FeishuReplyProducer
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeSdkMessage


NOW = datetime(2026, 7, 22, 3, 20, tzinfo=timezone.utc)


def _producer(tmp_path, *, media_enabled=False, media_max_assets=8):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    producer = FeishuReplyProducer(
        store,
        app_id="cli_test",
        stale_event_seconds=300,
        media_enabled=media_enabled,
        media_max_assets=media_max_assets,
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


def _approve_group(store, producer):
    producer.ingest_sdk_message(_sdk())
    store.review_feishu_reply_scope(
        "cli_test", "group", "oc_1", approved=True, approved_by="local-user"
    )


def _media_sdk(*, message_id="om_media", event_id="evt_media", resources=None):
    return _sdk(
        message_id=message_id,
        raw={"header": {"event_id": event_id}},
        raw_content_type="image",
        body_text="opaque-key-must-not-be-used",
        resources=resources
        or [SimpleNamespace(type="image", file_key="img_secret_key")],
    )


def test_approved_media_is_receive_only_until_assets_are_terminal(tmp_path):
    store, producer = _producer(tmp_path, media_enabled=True)
    _approve_group(store, producer)

    result = producer.ingest_sdk_message(_media_sdk())

    assert result.decision.eligible
    assert not result.enqueued
    assert result.message.body_text == "[图片]"
    assert store.count_reply_tasks(channel="feishu") == 0
    [asset] = store.list_feishu_media_assets(
        event_record_id=result.record.id
    )
    assert asset.status == "pending"
    assert asset.file_key == "img_secret_key"


def test_media_message_id_replay_is_idempotent(tmp_path):
    store, producer = _producer(tmp_path, media_enabled=True)
    _approve_group(store, producer)
    message = _media_sdk()

    first = producer.ingest_sdk_message(message)
    second = producer.ingest_sdk_message(message)

    assert first.record.id == second.record.id
    assert len(
        store.list_feishu_media_assets(event_record_id=first.record.id)
    ) == 1
    assert store.count_reply_tasks(channel="feishu") == 0


def test_unapproved_media_never_persists_resource_key_or_enqueues(tmp_path):
    store, producer = _producer(tmp_path, media_enabled=True)

    result = producer.ingest_sdk_message(_media_sdk())

    assert result.decision.reason == "scope_pending"
    assert store.list_feishu_media_assets(event_record_id=result.record.id) == []
    assert store.get_feishu_event(result.record.id).body_text == ""
    assert store.count_reply_tasks(channel="feishu") == 0


def test_media_gate_defaults_closed_without_persisting_key(tmp_path):
    store, producer = _producer(tmp_path)
    _approve_group(store, producer)

    result = producer.ingest_sdk_message(_media_sdk())

    assert result.decision.reason == "media_disabled"
    assert store.list_feishu_media_assets(event_record_id=result.record.id) == []
    assert store.get_feishu_event(result.record.id).body_text == ""
    assert store.count_reply_tasks(channel="feishu") == 0


def test_bare_rich_post_cannot_bypass_normalized_envelope_media_gate(tmp_path):
    store, producer = _producer(tmp_path)
    _approve_group(store, producer)
    sdk_message = _sdk(
        message_id="om_post_media",
        raw={"header": {"event_id": "evt_post_media"}},
        raw_content_type="post",
        body_text="[图片] 请查看",
        resources=[SimpleNamespace(type="image", file_key="post_secret_key")],
    )

    result = producer.ingest(
        normalize_sdk_message(sdk_message, app_id="cli_test", now=lambda: NOW)
    )

    assert result.decision.reason == "media_requires_normalized_envelope"
    assert not result.enqueued
    assert store.list_feishu_media_assets(event_record_id=result.record.id) == []
    assert store.count_reply_tasks(channel="feishu") == 0


def test_truncated_resource_set_never_persists_any_key_or_enqueues(tmp_path):
    store, producer = _producer(tmp_path, media_enabled=True)
    _approve_group(store, producer)
    resources = [
        SimpleNamespace(type="image", file_key=f"secret_{index}")
        for index in range(9)
    ]

    result = producer.ingest_sdk_message(
        _media_sdk(resources=resources)
    )

    assert result.decision.reason == "normalization_truncated"
    assert store.list_feishu_media_assets(event_record_id=result.record.id) == []
    assert store.count_reply_tasks(channel="feishu") == 0
    stored = store.get_feishu_event(result.record.id)
    assert stored.normalization_version == 1
    assert stored.content_truncated is False
    assert stored.resource_truncated is True


def test_producer_media_limit_cannot_exceed_normalized_contract(tmp_path):
    with pytest.raises(ValueError, match="between 1 and 8"):
        _producer(tmp_path, media_enabled=True, media_max_assets=9)


def test_rejected_first_observation_cannot_gain_media_keys_after_approval(tmp_path):
    store, producer = _producer(tmp_path, media_enabled=True)
    message = _media_sdk()
    first = producer.ingest_sdk_message(message)
    assert first.decision.reason == "scope_pending"
    store.review_feishu_reply_scope(
        "cli_test", "group", "oc_1", approved=True, approved_by="local-user"
    )

    replay = producer.ingest_sdk_message(message)

    assert replay.decision.reason == "scope_pending"
    assert store.list_feishu_media_assets(event_record_id=first.record.id) == []
    assert store.count_reply_tasks(channel="feishu") == 0


def test_message_id_replay_with_changed_type_cannot_add_media_keys(tmp_path):
    store, producer = _producer(tmp_path, media_enabled=True)
    _approve_group(store, producer)
    text = _sdk(
        message_id="om_changed",
        raw={"header": {"event_id": "evt_changed"}},
    )
    producer.ingest_sdk_message(text)

    replay = producer.ingest_sdk_message(
        _media_sdk(message_id="om_changed", event_id="evt_changed")
    )

    assert replay.decision.reason == "event_replay_mismatch"
    assert store.list_feishu_media_assets(event_record_id=replay.record.id) == []
