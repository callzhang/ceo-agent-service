"""Normalize SDK messages and apply the fail-closed Feishu ingress policy.

Only structured fields emitted by the official Channel SDK are trusted.  In
particular, group wake-up is based on ``mentioned_bot`` and is never inferred
from visible ``@name`` text.  The module contains no SDK import, which keeps the
rest of CEO Agent usable when the optional dependency is not installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
import re
from typing import Any, Callable, Iterable, Mapping

from app.feishu.models import (
    FeishuInboundMessage,
    FeishuInboundResourceCandidate,
    FeishuNormalizedEnvelope,
    FeishuReplyScope,
)


SUPPORTED_MESSAGE_TYPES = frozenset(
    {"text", "post", "image", "file", "audio", "media", "sticker"}
)
MEDIA_MESSAGE_TYPES = frozenset({"image", "file", "audio", "media", "sticker"})
REJECTED_SENDER_TYPES = frozenset({"app", "bot", "system"})

MAX_BODY_BYTES = 32 * 1024
MAX_RESOURCES = 8
MAX_RESOURCE_KEY_LENGTH = 512
MAX_RESOURCE_FILE_NAME_LENGTH = 255
MAX_RESOURCE_DURATION_MS = 86_400_000
MAX_ID_LENGTH = 512
MAX_APP_ID_LENGTH = 128
MAX_DISPLAY_LENGTH = 256
MAX_SUMMARY_BYTES = 512

_POST_IMAGE_REFERENCE_RE = re.compile(
    r"!\[(?:[^\r\n]*?)\]\((?P<key>[^)\r\n]+)\)"
)
_POST_MEDIA_REFERENCE_RE = re.compile(
    r"\[media:(?P<key>[^\]\r\n]+)\]",
    re.IGNORECASE,
)
_RESOURCE_PLACEHOLDERS = {
    "image": "[图片]",
    "file": "[文件]",
    "audio": "[音频]",
    "video": "[视频]",
    "sticker": "[表情贴纸]",
}


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


def _first_identifier(name: str, *values: Any) -> str:
    """Select one bounded identifier without coercing arbitrary raw values."""
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if len(value) > MAX_ID_LENGTH:
            raise ValueError(
                f"Feishu {name} exceeds {MAX_ID_LENGTH} characters"
            )
        cleaned = value.strip()
        if not cleaned:
            continue
        if len(cleaned) > MAX_ID_LENGTH:
            raise ValueError(
                f"Feishu {name} exceeds {MAX_ID_LENGTH} characters"
            )
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError(f"Feishu {name} contains control characters")
        return cleaned
    return ""


def _raw_path(raw: Mapping[str, Any], *names: str) -> Any:
    """Traverse raw only through mapping nodes from an explicit path."""
    current: Any = raw
    for name in names:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(name)
    return current


def _raw_message_identifier(message: Any, name: str) -> str:
    """Read one official message identifier from the SDK's bounded raw view.

    SDK 1.2 keeps ``root_id``/``parent_id`` only in ``InboundMessage.raw``.
    Accept both its real top-level message mapping and the official event
    envelope shape used by contract fixtures.  No other raw key is observed.
    """
    if name not in {"root_id", "parent_id"}:  # internal misuse guard
        raise ValueError("Unsupported Feishu raw identifier")
    raw = _value(message, "raw", None)
    if not isinstance(raw, Mapping):
        return ""
    return _first_identifier(
        name,
        raw.get(name, ""),
        _raw_path(raw, "message", name),
        _raw_path(raw, "event", "message", name),
    )


def _required_bounded(value: str, *, name: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"Feishu {name} is required")
    if len(cleaned) > limit:
        raise ValueError(f"Feishu {name} exceeds {limit} characters")
    return cleaned


def _optional_bounded(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _optional_identifier(value: Any, *, name: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) > MAX_ID_LENGTH:
        raise ValueError(
            f"Feishu {name} exceeds {MAX_ID_LENGTH} characters"
        )
    return cleaned


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _event_id(message: Any, message_id: str, app_id: str) -> str:
    raw = _value(message, "raw", {}) or {}
    if not isinstance(raw, Mapping):
        raw = {}
    # Event metadata is the only non-message identifier read from raw.  Keep
    # the path allowlist explicit; never traverse or retain the rest of raw.
    event_id = _first_identifier(
        "event_id",
        _value(message, "event_id", ""),
        _raw_path(raw, "header", "event_id"),
        _raw_path(raw, "event", "header", "event_id"),
        raw.get("event_id", ""),
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


def _normalized_message_type(message: Any) -> str:
    content = _value(message, "content", None)
    return _first_nonempty(
        _value(message, "raw_content_type", ""),
        _value(message, "message_type", ""),
        _value(content, "kind", ""),
        "unknown",
    ).lower()[:32]


def _safe_post_body(
    body: str,
    resources: tuple[FeishuInboundResourceCandidate, ...],
) -> str:
    """Remove opaque SDK resource keys from a flattened Post body.

    Channel SDK 1.2 renders Post resources as ``![...](image_key)`` and
    ``[media:file_key]`` while also returning typed resource descriptors.  The
    descriptors are the sole controlled boundary for those keys; model/event/
    task text receives only deterministic display placeholders.  A reference
    with no matching descriptor is rejected without echoing the suspect value.
    """
    placeholders: dict[str, str] = {}
    for resource in resources:
        placeholder = _RESOURCE_PLACEHOLDERS.get(
            resource.resource_type, "[资源]"
        )
        previous = placeholders.get(resource.file_key)
        placeholders[resource.file_key] = (
            placeholder
            if previous in (None, placeholder)
            else "[资源]"
        )

    def replace_reference(match: re.Match[str]) -> str:
        key = match.group("key").strip()
        placeholder = placeholders.get(key)
        if placeholder is None:
            raise ValueError(
                "Feishu post body contains unbound resource reference"
            )
        return placeholder

    sanitized = _POST_IMAGE_REFERENCE_RE.sub(replace_reference, body)
    sanitized = _POST_MEDIA_REFERENCE_RE.sub(replace_reference, sanitized)

    # Be fail-safe if the SDK gains another flattened representation: an
    # already-extracted opaque key may still never cross the text boundary.
    # Longest-first replacement keeps overlapping adversarial keys stable.
    for key in sorted(placeholders, key=lambda value: (-len(value), value)):
        sanitized = sanitized.replace(key, placeholders[key])
    # Pathological descriptors can deliberately collide with a display
    # placeholder (for example, a file key literally equal to ``[图片]``).
    # Never weaken the no-key invariant to accommodate such an invalid edge.
    if any(key in sanitized for key in placeholders):
        raise ValueError("Feishu post body contains unsafe resource key")
    return sanitized


def _normalized_body(
    message: Any,
    message_type: str,
    resources: tuple[FeishuInboundResourceCandidate, ...],
) -> tuple[str, bool]:
    # Media flatteners include opaque file/image keys in their text rendering.
    # Do not let those keys escape the resource candidate boundary.
    if message_type not in {"text", "post"}:
        return "", False
    body = _first_nonempty(
        _value(message, "body_text", ""),
        _value(message, "safe_content_text", ""),
    )
    if message_type == "post":
        body = _safe_post_body(body, resources)
    return _truncate_utf8(body, MAX_BODY_BYTES)


def _resource_items(message: Any) -> Iterable[Any]:
    resources = _value(message, "resources", ())
    if isinstance(resources, (str, bytes, Mapping)) or resources is None:
        return ()
    try:
        # One look-ahead item is enough to set the explicit truncation flag;
        # never materialize an attacker-controlled iterable without a bound.
        return tuple(islice(resources, MAX_RESOURCES + 1))
    except TypeError:
        return ()


def _typed_content_resources(
    message: Any, message_type: str
) -> tuple[dict[str, Any], ...]:
    """Read only bounded scalar fields from the SDK content union.

    Deliberately excluded fields include ``raw``, Post ``post`` ASTs, and
    Interactive ``card`` JSON.
    """
    content = _value(message, "content", None)
    if content is None:
        return ()
    if message_type == "image":
        return ({"type": "image", "file_key": _value(content, "image_key", "")},)
    if message_type == "file":
        return (
            {
                "type": "file",
                "file_key": _value(content, "file_key", ""),
                "file_name": _value(content, "file_name", ""),
            },
        )
    if message_type == "audio":
        return (
            {
                "type": "audio",
                "file_key": _value(content, "file_key", ""),
                "duration_ms": _value(content, "duration_ms", None),
            },
        )
    if message_type == "media":
        return (
            {
                "type": "video",
                "file_key": _value(content, "file_key", ""),
                "file_name": _value(content, "file_name", ""),
                "duration_ms": _value(content, "duration_ms", None),
                "cover_image_key": _value(content, "image_key", ""),
            },
        )
    if message_type == "sticker":
        return ({"type": "sticker", "file_key": _value(content, "file_key", "")},)
    return ()


def _normalize_resources(
    message: Any, message_type: str
) -> tuple[tuple[FeishuInboundResourceCandidate, ...], bool]:
    candidates: list[FeishuInboundResourceCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    resource_truncated = False

    def add(
        resource_type: Any,
        file_key: Any,
        *,
        file_name: Any = "",
        duration_ms: Any = None,
        role: str = "content",
    ) -> None:
        nonlocal resource_truncated
        normalized_type = str(resource_type or "").strip().lower()
        if normalized_type == "media":
            normalized_type = "video"
        if normalized_type not in {"image", "file", "audio", "video", "sticker"}:
            if normalized_type:
                resource_truncated = True
            return

        key = str(file_key or "").strip()
        if not key:
            # A typed media descriptor without its required opaque key is an
            # incomplete user turn, not a resource-less text message.
            resource_truncated = True
            return
        if len(key) > MAX_RESOURCE_KEY_LENGTH:
            resource_truncated = True
            return

        normalized_duration: int | None = None
        if duration_ms not in (None, ""):
            try:
                parsed_duration = int(duration_ms)
            except (TypeError, ValueError):
                resource_truncated = True
            else:
                if 0 <= parsed_duration <= MAX_RESOURCE_DURATION_MS:
                    normalized_duration = parsed_duration
                else:
                    resource_truncated = True

        dedupe_key = (normalized_type, key, role)
        if dedupe_key in seen:
            return
        if len(candidates) >= MAX_RESOURCES:
            resource_truncated = True
            return

        raw_name = str(file_name or "")
        candidate = FeishuInboundResourceCandidate(
            ordinal=len(candidates),
            resource_type=normalized_type,
            file_key=key,
            file_name=raw_name,
            duration_ms=normalized_duration,
            role=role,
        )
        if candidate.file_name != raw_name.strip():
            resource_truncated = True
        candidates.append(candidate)
        seen.add(dedupe_key)

    sdk_descriptors = tuple(_resource_items(message))
    if len(sdk_descriptors) > MAX_RESOURCES:
        resource_truncated = True
    descriptors = sdk_descriptors + _typed_content_resources(
        message, message_type
    )
    for descriptor in descriptors:
        resource_type = _value(descriptor, "type", "")
        add(
            resource_type,
            _value(descriptor, "file_key", ""),
            file_name=_value(descriptor, "file_name", ""),
            duration_ms=_value(descriptor, "duration_ms", None),
        )
        cover_key = _value(descriptor, "cover_image_key", "")
        if cover_key:
            add("image", cover_key, role="cover")
    return tuple(candidates), resource_truncated


def _media_summary(
    message_type: str,
    resources: tuple[FeishuInboundResourceCandidate, ...],
) -> str:
    if message_type == "post" and resources:
        return f"[富文本消息: {len(resources)} 个资源]"
    if message_type == "image":
        return "[图片]"
    if message_type == "file":
        file_name = next(
            (
                item.file_name
                for item in resources
                if item.role == "content" and item.file_name
            ),
            "",
        )
        return f"[文件: {file_name}]" if file_name else "[文件]"
    if message_type == "audio":
        return "[音频]"
    if message_type == "media":
        return "[视频]"
    if message_type == "sticker":
        return "[表情贴纸]"
    return ""


def normalize_sdk_envelope(
    sdk_message: Any,
    *,
    app_id: str,
    received_at: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> FeishuNormalizedEnvelope:
    """Normalize the official Channel SDK 1.2 typed inbound contract.

    The returned object contains only bounded scalar business fields and
    resource references. Raw payloads, Post ASTs, and Card JSON are never
    copied across this boundary.
    """
    normalized_app_id = _required_bounded(
        app_id, name="app_id", limit=MAX_APP_ID_LENGTH
    )

    conversation = _value(sdk_message, "conversation", None)
    sender = _value(sdk_message, "sender", None)
    message_id = _required_bounded(
        _first_nonempty(
            _value(sdk_message, "message_id", ""),
            _value(sdk_message, "id", ""),
        ),
        name="message_id",
        limit=MAX_ID_LENGTH,
    )
    chat_id = _required_bounded(
        _first_nonempty(
            _value(sdk_message, "chat_id", ""),
            _value(conversation, "chat_id", ""),
        ),
        name="chat_id",
        limit=MAX_ID_LENGTH,
    )
    chat_type = _first_nonempty(
        _value(sdk_message, "chat_type", ""),
        _value(conversation, "chat_type", ""),
        "unknown",
    ).lower()
    if chat_type not in {"p2p", "group", "topic", "unknown"}:
        chat_type = "unknown"

    sender_open_id = _required_bounded(
        _first_nonempty(
            _value(sdk_message, "sender_id", ""),
            _value(sender, "open_id", ""),
        ),
        name="sender_open_id",
        limit=MAX_ID_LENGTH,
    )
    sender_is_bot = bool(
        _value(sdk_message, "sender_is_bot", False)
        or _value(sender, "is_bot", False)
    )
    sender_type = _first_nonempty(
        _value(sdk_message, "sender_type", ""),
        _value(sender, "sender_type", ""),
        _value(sender, "type", ""),
        "user",
    ).lower()[:32]
    if sender_is_bot:
        sender_type = "bot"

    message_type = _normalized_message_type(sdk_message)
    resources, resource_truncated = _normalize_resources(
        sdk_message, message_type
    )
    body_text, content_truncated = _normalized_body(
        sdk_message, message_type, resources
    )
    normalized_summary, summary_truncated = _truncate_utf8(
        _media_summary(message_type, resources), MAX_SUMMARY_BYTES
    )
    content_truncated = content_truncated or summary_truncated

    event_create_time = _required_bounded(
        _first_nonempty(
            _value(sdk_message, "create_time", ""),
            _value(sdk_message, "event_create_time", ""),
        ),
        name="event create_time",
        limit=64,
    )
    event_id = _required_bounded(
        _event_id(sdk_message, message_id, normalized_app_id),
        name="event_id",
        limit=MAX_ID_LENGTH,
    )

    reply = _value(sdk_message, "reply", None)
    # lark-channel-sdk 1.2 exposes conversation/thread and reply as typed
    # objects, but keeps the platform root_id (and parent_id==root_id case)
    # only in raw.  Read exactly those two raw scalars, then discard raw.
    root_message_id = _first_identifier(
        "root_message_id",
        _value(sdk_message, "root_message_id", ""),
        _value(sdk_message, "root_id", ""),
        _value(conversation, "root_message_id", ""),
        _value(conversation, "root_id", ""),
        _raw_message_identifier(sdk_message, "root_id"),
    )
    parent_message_id = _first_identifier(
        "parent_message_id",
        _value(sdk_message, "parent_message_id", ""),
        _value(sdk_message, "parent_id", ""),
        _raw_message_identifier(sdk_message, "parent_id"),
        _value(reply, "message_id", ""),
        _value(sdk_message, "reply_to_message_id", ""),
    )
    root_message_id = _optional_identifier(
        root_message_id, name="root_message_id"
    )
    parent_message_id = _optional_identifier(
        parent_message_id, name="parent_message_id"
    )

    clock = now or (lambda: datetime.now(timezone.utc))
    received = received_at or clock().astimezone(timezone.utc).isoformat()
    message = FeishuInboundMessage(
        event_id=event_id,
        app_id=normalized_app_id,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=_optional_bounded(_chat_title(sdk_message), MAX_DISPLAY_LENGTH),
        thread_id=_optional_identifier(
            _first_nonempty(
                _value(sdk_message, "thread_id", ""),
                _value(conversation, "thread_id", ""),
            ),
            name="thread_id",
        ),
        root_message_id=root_message_id,
        parent_message_id=parent_message_id,
        reply_to_message_id=_optional_identifier(
            _first_nonempty(
                _value(sdk_message, "reply_to_message_id", ""),
                parent_message_id,
            ),
            name="reply_to_message_id",
        ),
        sender_open_id=sender_open_id,
        sender_type=sender_type,
        sender_name=_optional_bounded(
            _first_nonempty(
                _value(sdk_message, "sender_name", ""),
                _value(sender, "display_name", ""),
                _value(sender, "name", ""),
            ),
            MAX_DISPLAY_LENGTH,
        ),
        sender_is_bot=sender_is_bot,
        message_type=message_type,
        mentioned_bot=bool(_value(sdk_message, "mentioned_bot", False)),
        body_text=body_text,
        normalized_summary=normalized_summary,
        event_create_time=event_create_time,
        received_at=_optional_bounded(received, 64),
    )
    return FeishuNormalizedEnvelope(
        message=message,
        resources=resources,
        content_truncated=content_truncated,
        resource_truncated=resource_truncated,
    )


def normalize_sdk_message(
    sdk_message: Any,
    *,
    app_id: str,
    received_at: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> FeishuInboundMessage:
    """Backward-compatible view over :func:`normalize_sdk_envelope`."""
    return normalize_sdk_envelope(
        sdk_message,
        app_id=app_id,
        received_at=received_at,
        now=now,
    ).message


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
    has_safe_content = bool(message.body_text.strip())
    if (
        message.message_type in MEDIA_MESSAGE_TYPES
        or message.message_type == "post"
    ):
        has_safe_content = has_safe_content or bool(
            message.normalized_summary.strip()
        )
    if not has_safe_content:
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
