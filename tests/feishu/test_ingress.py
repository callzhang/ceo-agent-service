from datetime import datetime, timezone

from app.feishu.ingress import evaluate_ingress, normalize_sdk_message
from app.feishu.models import FeishuReplyScope
from tests.feishu.fakes import FakeSdkMessage


NOW = datetime(2026, 7, 22, 3, 20, tzinfo=timezone.utc)


def _scope(*, target_type="group", target_id="oc_1", enabled=True):
    return FeishuReplyScope(
        app_id="cli_test",
        target_type=target_type,
        target_id=target_id,
        display_name="Target",
        trigger_mode=(
            "mention_bot" if target_type == "group" else "every_inbound_text"
        ),
        enabled=enabled,
        binding_status="verified" if enabled else "pending",
    )


def _message(*, app_id="cli_test", **updates):
    sdk = FakeSdkMessage(create_time=str(int(NOW.timestamp() * 1000)))
    for key, value in updates.items():
        setattr(sdk, key, value)
    return normalize_sdk_message(
        sdk, app_id=app_id, now=lambda: NOW
    )


def test_normalizes_only_safe_business_fields():
    message = _message()
    assert message.event_id == "evt_1"
    assert message.body_text == "请看一下"
    assert message.mentioned_bot is True
    assert not hasattr(message, "raw")


def test_missing_event_header_uses_deterministic_message_key():
    message = _message(raw={})
    assert message.event_id == "message:cli_test:om_1"


def test_missing_event_header_scopes_message_key_by_normalized_app_id():
    first = _message(app_id="  cli_a  ", raw={})
    duplicate = _message(app_id="cli_a", raw={})
    other_app = _message(app_id="cli_b", raw={})

    assert first.app_id == "cli_a"
    assert first.event_id == duplicate.event_id == "message:cli_a:om_1"
    assert other_app.event_id == "message:cli_b:om_1"
    assert first.event_id != other_app.event_id


def test_official_event_id_remains_unchanged_across_apps():
    assert _message(app_id="  cli_a  ").event_id == "evt_1"
    assert _message(app_id="cli_b").event_id == "evt_1"


def test_group_requires_structured_bot_mention_not_visible_text():
    message = _message(mentioned_bot=False, body_text="@CEO Agent 请看一下")
    result = evaluate_ingress(message, _scope(), stale_event_seconds=300, now=NOW)
    assert not result.eligible
    assert result.reason == "bot_not_mentioned"


def test_group_structured_mention_is_eligible():
    result = evaluate_ingress(_message(), _scope(), stale_event_seconds=300, now=NOW)
    assert result.eligible
    assert result.store_body


def test_direct_verified_sender_is_eligible_without_mention():
    message = _message(chat_type="p2p", chat_id="oc_dm", mentioned_bot=False)
    result = evaluate_ingress(
        message,
        _scope(target_type="direct_sender", target_id="ou_1"),
        stale_event_seconds=300,
        now=NOW,
    )
    assert result.eligible


def test_bot_system_and_app_senders_are_rejected():
    for sender_type in ("bot", "system", "app"):
        result = evaluate_ingress(
            _message(sender_type=sender_type),
            _scope(),
            stale_event_seconds=300,
            now=NOW,
        )
        assert result.reason == "sender_not_user"


def test_sender_is_bot_flag_is_rejected_even_with_user_type():
    result = evaluate_ingress(
        _message(sender_is_bot=True), _scope(), stale_event_seconds=300, now=NOW
    )
    assert result.reason == "sender_not_user"


def test_media_uses_normalized_summary_when_body_is_empty():
    result = evaluate_ingress(
        _message(raw_content_type="image"),
        _scope(),
        stale_event_seconds=300,
        now=NOW,
    )
    assert result.eligible
    assert result.store_body


def test_unknown_message_type_is_rejected_without_body_retention():
    result = evaluate_ingress(
        _message(raw_content_type="interactive"),
        _scope(),
        stale_event_seconds=300,
        now=NOW,
    )
    assert result.reason == "unsupported_media"
    assert not result.store_body


def test_stale_and_future_events_are_rejected():
    stale = _message(create_time=str(int((NOW.timestamp() - 301) * 1000)))
    future = _message(create_time=str(int((NOW.timestamp() + 61) * 1000)))
    assert evaluate_ingress(stale, _scope(), stale_event_seconds=300, now=NOW).reason == "stale_event"
    assert evaluate_ingress(future, _scope(), stale_event_seconds=300, now=NOW).reason == "event_time_in_future"


def test_unknown_scope_is_pending_and_does_not_store_body():
    result = evaluate_ingress(
        _message(), None, stale_event_seconds=300, now=NOW
    )
    assert result.reason == "scope_pending"
    assert not result.store_body
