"""Normalize SDK messages and apply the fail-closed Feishu ingress policy.

Only structured fields emitted by the official Channel SDK are trusted.  In
particular, group wake-up is based on ``mentioned_bot`` and is never inferred
from visible ``@name`` text.  The module contains no SDK import, which keeps the
rest of CEO Agent usable when the optional dependency is not installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.feishu.models import FeishuInboundMessage, FeishuReplyScope


SUPPORTED_MESSAGE_TYPES = frozenset({"text", "post"})
REJECTED_SENDER_TYPES = frozenset({"app", "bot", "system"})


@dataclass(frozen=True)
class IngressDecision:
    """Auditable policy result used by the producer and persistence layer."""

    eligible: bool
    status: str
    reason: str = ""

    @property
    def store_body(self) -> bool:
        # Unknown/unapproved targets must not retain message content.
        return self.eligible


def _value(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _nested(value: Any, *names: str, default: Any = "") -> Any:
    current = value
    for name in names:
        current = _value(current, name, None)
        if current is None:
            return default
    return current


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _event_id(message: Any, message_id: str, app_id: str) -> str:
    raw = _value(message, "raw", {}) or {}
    event_id = _first_nonempty(
        _value(message, "event_id", ""),
        _nested(raw, "header", "event_id"),
        _nested(raw, "event", "header", "event_id"),
        _value(raw, "event_id", ""),
    )
    # Some normalized SDK objects omit the event header.  Include the
    # normalized application identity so two Apps receiving the same message
    # id cannot collide at the DB's global event-id idempotency boundary.
    return event_id or f"message:{app_id}:{message_id}"


def _chat_title(message: Any) -> str:
    conversation = _value(message, "conversation", None)
    return _first_nonempty(
        _value(message, "chat_title", ""),
        _value(message, "chat_name", ""),
        _value(conversation, "name", ""),
    )


def normalize_sdk_message(
    sdk_message: Any,
    *,
    app_id: str,
    received_at: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> FeishuInboundMessage:
    """Convert an official SDK ``InboundMessage`` (or a test fake) to storage.

    Raw payloads are inspected only to recover the event id; they are never
    copied into the returned model or SQLite.
    """
    normalized_app_id = app_id.strip()
    if not normalized_app_id:
        raise ValueError("Feishu app_id is required")

    conversation = _value(sdk_message, "conversation", None)
    sender = _value(sdk_message, "sender", None)
    message_id = _first_nonempty(
        _value(sdk_message, "message_id", ""),
        _value(sdk_message, "id", ""),
    )
    if not message_id:
        raise ValueError("Feishu message_id is required")

    chat_id = _first_nonempty(
        _value(sdk_message, "chat_id", ""),
        _value(conversation, "chat_id", ""),
    )
    chat_type = _first_nonempty(
        _value(sdk_message, "chat_type", ""),
        _value(conversation, "chat_type", ""),
        "unknown",
    ).lower()
    if chat_type not in {"p2p", "group", "topic", "unknown"}:
        chat_type = "unknown"

    sender_open_id = _first_nonempty(
        _value(sdk_message, "sender_id", ""),
        _value(sender, "open_id", ""),
    )
    sender_is_bot = bool(
        _value(sdk_message, "sender_is_bot", False)
        or _value(sender, "is_bot", False)
    )
    sender_type = _first_nonempty(
        _value(sdk_message, "sender_type", ""),
        _value(sender, "type", ""),
        "user",
    ).lower()
    if sender_is_bot:
        sender_type = "bot"

    message_type = _first_nonempty(
        _value(sdk_message, "raw_content_type", ""),
        _value(sdk_message, "message_type", ""),
        _value(_value(sdk_message, "content", None), "kind", ""),
        "unknown",
    ).lower()
    body_text = _first_nonempty(
        _value(sdk_message, "body_text", ""),
        _value(sdk_message, "safe_content_text", ""),
    )
    event_create_time = _first_nonempty(
        _value(sdk_message, "create_time", ""),
        _value(sdk_message, "event_create_time", ""),
    )
    if not event_create_time:
        raise ValueError("Feishu event create_time is required")

    clock = now or (lambda: datetime.now(timezone.utc))
    received = received_at or clock().astimezone(timezone.utc).isoformat()
    return FeishuInboundMessage(
        event_id=_event_id(sdk_message, message_id, normalized_app_id),
        app_id=normalized_app_id,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=_chat_title(sdk_message),
        thread_id=_first_nonempty(
            _value(sdk_message, "thread_id", ""),
            _value(conversation, "thread_id", ""),
        ),
        reply_to_message_id=_first_nonempty(
            _value(sdk_message, "reply_to_message_id", ""),
        ),
        sender_open_id=sender_open_id,
        sender_type=sender_type,
        sender_name=_first_nonempty(
            _value(sdk_message, "sender_name", ""),
            _value(sender, "display_name", ""),
            _value(sender, "name", ""),
        ),
        sender_is_bot=sender_is_bot,
        message_type=message_type,
        mentioned_bot=bool(_value(sdk_message, "mentioned_bot", False)),
        body_text=body_text,
        event_create_time=event_create_time,
        received_at=received,
    )


def event_datetime(value: str) -> datetime | None:
    """Parse official millisecond/second epoch values and ISO timestamps."""
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        numeric = float(cleaned)
    except ValueError:
        numeric = None
    if numeric is not None:
        if abs(numeric) >= 100_000_000_000:  # milliseconds
            numeric /= 1000.0
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def scope_target(message: FeishuInboundMessage) -> tuple[str, str]:
    if message.chat_type == "p2p":
        return "direct_sender", message.sender_open_id
    return "group", message.chat_id


def evaluate_ingress(
    message: FeishuInboundMessage,
    scope: FeishuReplyScope | None,
    *,
    stale_event_seconds: int,
    now: datetime | None = None,
) -> IngressDecision:
    """Apply the first-version admission policy in a deterministic order."""
    if stale_event_seconds <= 0:
        raise ValueError("stale_event_seconds must be positive")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    created = event_datetime(message.event_create_time)
    if created is None:
        return IngressDecision(False, "rejected", "invalid_event_time")
    age = (current - created).total_seconds()
    if age > stale_event_seconds:
        return IngressDecision(False, "rejected", "stale_event")
    if age < -60:
        return IngressDecision(False, "rejected", "event_time_in_future")

    sender_type = message.sender_type.strip().lower()
    if message.sender_is_bot or sender_type in REJECTED_SENDER_TYPES:
        return IngressDecision(False, "rejected", "sender_not_user")
    if not message.sender_open_id:
        return IngressDecision(False, "rejected", "sender_identity_missing")
    if message.chat_type not in {"p2p", "group", "topic"}:
        return IngressDecision(False, "rejected", "unsupported_chat_type")
    if message.message_type not in SUPPORTED_MESSAGE_TYPES:
        return IngressDecision(False, "rejected", "unsupported_media")
    if not message.body_text.strip():
        return IngressDecision(False, "rejected", "empty_message")

    target_type, target_id = scope_target(message)
    if scope is None:
        return IngressDecision(False, "rejected", "scope_pending")
    if scope.app_id != message.app_id or scope.target_type != target_type or scope.target_id != target_id:
        return IngressDecision(False, "rejected", "scope_mismatch")
    if scope.binding_status != "verified":
        reason = "scope_disabled" if scope.binding_status == "disabled" else "scope_pending"
        return IngressDecision(False, "rejected", reason)
    if not scope.enabled:
        return IngressDecision(False, "rejected", "scope_disabled")

    if message.chat_type == "p2p":
        if scope.trigger_mode != "every_inbound_text":
            return IngressDecision(False, "rejected", "invalid_trigger_mode")
    else:
        if scope.trigger_mode != "mention_bot":
            return IngressDecision(False, "rejected", "invalid_trigger_mode")
        if not message.mentioned_bot:
            return IngressDecision(False, "rejected", "bot_not_mentioned")
    return IngressDecision(True, "eligible")
