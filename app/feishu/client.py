"""Delayed adapter for the official ``lark-channel-sdk``.

Importing this module never imports ``lark_channel``.  The optional dependency
is loaded only when a real channel is built or a typed outbound mention is sent.
"""
from __future__ import annotations

import asyncio
import contextvars
import importlib
import json
import logging
import re
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from app.feishu.actions import action_idempotency_key, canonical_payload
from app.feishu.payloads import (
    MAX_DELIVERY_CHUNKS,
    MAX_WIRE_CHUNK_CHARS,
    contains_untrusted_at_markup,
)


MAX_SEND_MESSAGE_IDS = 100
MAX_MENTION_OPEN_IDS = 20
MAX_INBOUND_RESOURCE_DOWNLOAD_BYTES = 20 * 1024 * 1024 + 1
ALLOWED_REACTION_EMOJI_TYPES = frozenset(
    {
        "APPLAUSE",
        "DONE",
        "HEART",
        "OK",
        "SMILE",
        "THANKS",
        "THUMBSUP",
    }
)

_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9_-]{1,253}$")
_SAFE_ERROR_CODES = frozenset(
    {
        "download_failed",
        "format_error",
        "not_connected",
        "permission_denied",
        "rate_limited",
        "send_timeout",
        "ssrf_blocked",
        "target_revoked",
        "unknown",
        "upload_failed",
    }
)
_MESSAGE_NOT_FOUND_CODES = frozenset({230002, 230005, 230017, 230020})
_RAW_RATE_LIMIT_CODES = frozenset({99991402, 11020, 11021})
_RAW_TARGET_REVOKED_CODES = _MESSAGE_NOT_FOUND_CODES
_RAW_FORMAT_ERROR_CODES = frozenset({230001, 230021, 230022, 230099})
_RAW_RETRYABLE_TOKEN_CODES = frozenset(
    {99991663, 99991664, 99991665, 99991666, 99991668}
)
_RAW_PERMISSION_DENIED_CODES = frozenset(
    {
        99991400,
        99991401,
        99991672,
        99991679,
        99991680,
        99991681,
        230003,
        230010,
    }
)

_SDK_LOG_OPERATION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "feishu_sdk_log_operation", default=""
)


class _SdkBoundaryLogFilter(logging.Filter):
    """Remove opaque IDs and vendor payloads from SDK diagnostics at the sink."""

    _ceo_agent_feishu_filter = True

    def filter(self, record: logging.LogRecord) -> bool:
        operation = _SDK_LOG_OPERATION.get()
        is_lark_sdk = record.name == "Lark" or record.name.startswith("Lark.")
        if not operation and not is_lark_sdk:
            return True
        if not operation:
            operation = "inbound"
        record.msg = "Feishu SDK %s diagnostic redacted"
        record.args = (operation,)
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def _ensure_sdk_log_filter() -> None:
    # Logger filters are not inherited by descendants.  Install the
    # context-bound scrubber on every concrete transport logger known to the
    # pinned SDK/httpx stack, including any httpcore modules already loaded.
    names = {
        "Lark",
        "httpx",
        "httpcore.connection",
        "httpcore.connection_pool",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    }
    names.update(
        str(name)
        for name in logging.root.manager.loggerDict
        if str(name) == "httpx"
        or str(name).startswith("httpcore.")
        or str(name).startswith("Lark.")
    )
    for name in names:
        logger = logging.getLogger(name)
        if not any(
            getattr(filter_, "_ceo_agent_feishu_filter", False)
            for filter_ in logger.filters
        ):
            logger.addFilter(_SdkBoundaryLogFilter())


@contextmanager
def _sdk_log_boundary(operation: str):
    """Sanitize SDK log records emitted synchronously by this async context."""

    _ensure_sdk_log_filter()
    token = _SDK_LOG_OPERATION.set(operation)
    try:
        yield
    finally:
        _SDK_LOG_OPERATION.reset(token)


def _disabled_name_lookup(_open_ids: list[str]) -> dict[str, str]:
    """Deliberately avoid the SDK's implicit Contact API name lookup."""
    return {}


class FeishuSdkUnavailable(RuntimeError):
    pass


class FeishuSdkOperationError(RuntimeError):
    """An SDK failure without vendor payloads, response text, or secrets."""

    def __init__(self, operation: str, *, code: str = "unknown"):
        safe_code = code if code in _SAFE_ERROR_CODES else "unknown"
        super().__init__(f"Feishu {operation} failed:{safe_code}")
        self.code = safe_code


@dataclass(frozen=True)
class FeishuClientConfig:
    app_id: str
    app_secret: str = field(repr=False)
    security_mode: str = "strict"
    domain: str = ""
    max_ws_fragment_parts: int = 128
    max_ws_fragment_bytes: int = 8 * 1024 * 1024
    max_concurrent_ws_handlers: int = 64

    def __post_init__(self):
        if not self.app_id.strip() or not self.app_secret.strip():
            raise ValueError("Feishu App ID and App Secret are required")
        if self.security_mode not in {"audit", "strict"}:
            raise ValueError("Feishu security_mode must be audit or strict")


@dataclass(frozen=True)
class FeishuSendResult:
    success: bool
    message_id: str = ""
    request_log_id: str = ""
    error_code: str = ""
    message_ids: tuple[str, ...] = ()
    reaction_id: str = ""

    def __post_init__(self):
        message_ids = _ordered_message_ids(self.message_id, self.message_ids)
        object.__setattr__(self, "message_ids", message_ids)
        object.__setattr__(self, "message_id", message_ids[0] if message_ids else "")
        object.__setattr__(
            self, "reaction_id", _safe_result_identifier(self.reaction_id)
        )


FeishuMessageExistence = Literal["exists", "absent", "unknown"]


@dataclass(frozen=True)
class FeishuMessageState:
    """Payload-free final-state probe for an individual Feishu message."""

    state: FeishuMessageExistence


def _ordered_message_ids(primary: Any, chunks: Any) -> tuple[str, ...]:
    """Keep only bounded SDK message identifiers, preserving wire order."""
    candidates: list[Any] = [primary]
    if isinstance(chunks, (list, tuple)):
        candidates.extend(chunks)
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        value = candidate.strip()
        if not value or len(value) > 512 or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
        if len(ordered) >= MAX_SEND_MESSAGE_IDS:
            break
    return tuple(ordered)


def _safe_result_identifier(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or len(normalized) > 512:
        return ""
    if any(ord(character) < 32 for character in normalized):
        return ""
    return normalized


def _reaction_id(raw: Any) -> str:
    """Extract only the opaque reaction identifier from an SDK response."""
    if not isinstance(raw, dict):
        return ""
    direct = _safe_result_identifier(raw.get("reaction_id"))
    if direct:
        return direct
    data = raw.get("data")
    if not isinstance(data, dict):
        return ""
    return _safe_result_identifier(data.get("reaction_id"))


def _code(value: Any) -> str:
    try:
        code = getattr(value, "code", "")
        return str(getattr(code, "value", code) or "").strip().lower()
    except Exception:
        return ""


def _raw_error_code(value: Any) -> int | None:
    try:
        raw = getattr(value, "raw_code", None)
        if isinstance(raw, bool) or raw is None:
            return None
        return int(raw)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_vendor_error_code(raw_code: int | None) -> str:
    if raw_code in _RAW_RETRYABLE_TOKEN_CODES:
        return "not_connected"
    if raw_code in _RAW_RATE_LIMIT_CODES:
        return "rate_limited"
    if raw_code in _RAW_TARGET_REVOKED_CODES:
        return "target_revoked"
    if raw_code in _RAW_FORMAT_ERROR_CODES:
        return "format_error"
    if raw_code in _RAW_PERMISSION_DENIED_CODES:
        return "permission_denied"
    return "unknown"


def _safe_error_code(value: Any) -> str:
    code = _code(value)
    mapped = _safe_vendor_error_code(_raw_error_code(value))
    if mapped != "unknown":
        return mapped
    if code in _SAFE_ERROR_CODES and code != "unknown":
        return code
    return code if code in _SAFE_ERROR_CODES else "unknown"


def _official_openapi_config(channel: Any) -> Any | None:
    """Return only the pinned SDK client's validated OpenAPI configuration."""

    if not type(channel).__module__.startswith("lark_channel."):
        return None
    client = getattr(channel, "client", None)
    config = getattr(client, "config", None)
    if config is None:
        raise FeishuSdkOperationError(
            "resource download", code="download_failed"
        )
    return config


def _validated_api_origin(config: Any) -> str:
    domain = str(getattr(config, "domain", "") or "").strip()
    parsed = urllib.parse.urlsplit(domain)
    loopback = (parsed.hostname or "").lower() in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if (
        (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or not parsed.netloc
        or "@" in parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise FeishuSdkOperationError(
            "resource download", code="ssrf_blocked"
        )
    return domain


async def _bounded_message_resource_download(
    channel: Any,
    *,
    message_id: str,
    file_key: str,
    resource_type: str,
    max_bytes: int,
) -> bytes:
    """Stream one message-bound resource without buffering an unbounded body."""

    config = _official_openapi_config(channel)
    if config is None:
        raise FeishuSdkOperationError(
            "resource download", code="download_failed"
        )
    origin = _validated_api_origin(config)
    try:
        token_module = importlib.import_module("lark_channel.core.token")
        token_manager = getattr(token_module, "TokenManager")
        tenant_token = await asyncio.to_thread(
            token_manager.get_self_tenant_token, config
        )
        if not isinstance(tenant_token, str) or not tenant_token.strip():
            raise FeishuSdkOperationError(
                "resource download", code="permission_denied"
            )
        httpx = importlib.import_module("httpx")
        # Importing httpx materializes its concrete httpcore loggers.  Attach
        # the context-bound scrubber before constructing or sending a request,
        # so opaque resource keys and bearer tokens cannot enter INFO/DEBUG
        # records while unrelated concurrent traffic remains untouched.
        _ensure_sdk_log_filter()
        client_kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "trust_env": bool(
                getattr(config, "trust_env_proxy", False)
            ),
        }
        proxy_url = str(getattr(config, "proxy_url", "") or "").strip()
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        timeout = float(getattr(config, "timeout", 30.0) or 30.0)
        url = (
            f"{origin}/open-apis/im/v1/messages/"
            f"{urllib.parse.quote(message_id, safe='')}/resources/"
            f"{urllib.parse.quote(file_key, safe='')}"
        )
        headers = {
            "Authorization": f"Bearer {tenant_token}",
            "Accept": "application/octet-stream",
            "User-Agent": "ceo-agent-service feishu-media/1",
        }
        async with httpx.AsyncClient(**client_kwargs) as transport:
            async with transport.stream(
                "GET",
                url,
                headers=headers,
                params={"type": resource_type},
                timeout=timeout,
            ) as response:
                if response.status_code == 429:
                    raise FeishuSdkOperationError(
                        "resource download", code="rate_limited"
                    )
                if response.status_code in {401, 403}:
                    raise FeishuSdkOperationError(
                        "resource download", code="permission_denied"
                    )
                if response.status_code == 404:
                    raise FeishuSdkOperationError(
                        "resource download", code="target_revoked"
                    )
                if response.status_code < 200 or response.status_code >= 300:
                    raise FeishuSdkOperationError(
                        "resource download", code="download_failed"
                    )
                media_type = str(
                    response.headers.get("content-type", "") or ""
                ).split(";", 1)[0].strip().lower()
                if media_type in {
                    "application/xhtml+xml",
                    "application/xml",
                    "text/html",
                    "text/xml",
                } or media_type.endswith("+xml"):
                    # A successful proxy/login/error document is not a Feishu
                    # attachment.  Reject it without reading or retaining the
                    # diagnostic body.
                    raise FeishuSdkOperationError(
                        "resource download", code="download_failed"
                    )
                if media_type == "application/json" or media_type.endswith(
                    "+json"
                ):
                    envelope = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                        if len(envelope) + len(chunk) > 64 * 1024:
                            raise FeishuSdkOperationError(
                                "resource download", code="download_failed"
                            )
                        envelope.extend(chunk)
                    try:
                        decoded = json.loads(envelope.decode("utf-8"))
                        raw_code_value = (
                            decoded.get("code") if isinstance(decoded, dict) else None
                        )
                        raw_code = (
                            None
                            if isinstance(raw_code_value, bool)
                            else int(raw_code_value)
                        )
                    except (UnicodeDecodeError, ValueError, TypeError):
                        raw_code = None
                    raise FeishuSdkOperationError(
                        "resource download",
                        code=(
                            _safe_vendor_error_code(raw_code)
                            if raw_code not in {None, 0}
                            else "download_failed"
                        ),
                    )
                content_length = response.headers.get("content-length", "")
                try:
                    declared_size = int(content_length) if content_length else 0
                except (TypeError, ValueError):
                    declared_size = 0
                if declared_size > max_bytes:
                    raise ValueError(
                        "Feishu inbound resource exceeds max_bytes"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > max_bytes:
                        raise ValueError(
                            "Feishu inbound resource exceeds max_bytes"
                        )
                    body.extend(chunk)
                return bytes(body)
    except (FeishuSdkOperationError, ValueError):
        raise
    except Exception as exc:
        raise FeishuSdkOperationError(
            "resource download", code=_safe_error_code(exc)
        ) from None
    finally:
        # Do not retain a second credential copy beyond the request scope.
        if "tenant_token" in locals():
            tenant_token = ""


def _request_log_id(raw: Any, *, depth: int = 0) -> str:
    """Extract only a request identifier; never persist or expose raw results."""
    if depth > 3 or not isinstance(raw, dict):
        return ""
    for key in ("request_log_id", "log_id", "request_id", "x-tt-logid"):
        value = raw.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return _safe_result_identifier(str(value).strip())[:256]
    for key in ("headers", "data", "response", "raw"):
        found = _request_log_id(raw.get(key), depth=depth + 1)
        if found:
            return found
    return ""


def normalize_send_result(result: Any) -> FeishuSendResult:
    success = bool(getattr(result, "success", False))
    error = getattr(result, "error", None)
    message_ids = _ordered_message_ids(
        getattr(result, "message_id", ""),
        getattr(result, "chunk_ids", ()),
    )
    raw = getattr(result, "raw", None)
    return FeishuSendResult(
        success=success,
        message_id=message_ids[0] if message_ids else "",
        request_log_id=_request_log_id(raw),
        error_code="" if success else _safe_error_code(error),
        message_ids=message_ids,
        reaction_id=_reaction_id(raw),
    )


def _normalize_action_result(result: Any, *, target_message_id: str) -> FeishuSendResult:
    """Remove only the official SDK's exact successful target-ID echo.

    ``lark-channel-sdk`` uses the requested message ID as ``SendResult``'s
    message ID for successful reaction and recall calls.  It is not a newly
    created remote object.  Mismatched/multiple IDs and every failed result
    remain intact so the durable action sender can quarantine ambiguity.
    """
    normalized = normalize_send_result(result)
    if normalized.success and normalized.message_ids == (target_message_id,):
        return FeishuSendResult(
            True,
            request_log_id=normalized.request_log_id,
            error_code=normalized.error_code,
            reaction_id=normalized.reaction_id,
        )
    return normalized


class FeishuChannelClient:
    """Small facade exposing only app-bound, payload-free channel operations."""

    def __init__(self, channel: Any, *, app_id: str):
        if not app_id.strip():
            raise ValueError("Feishu client App ID is required")
        self.channel = channel
        # This is the identity used to authenticate the underlying channel.
        # Delivery code binds every durable row to this value before claiming
        # or sending it, so one application's runtime cannot drain another
        # application's rows from a shared database.
        self.app_id = app_id.strip()

    async def connect_until_ready(self, timeout: float = 30) -> None:
        await self.channel.connect_until_ready(timeout=timeout)

    async def disconnect(self) -> None:
        await self.channel.disconnect()

    def _require_app_binding(self, app_id: Any) -> None:
        if not isinstance(app_id, str) or app_id.strip() != self.app_id:
            raise PermissionError("Feishu operation App ID does not match client")

    @staticmethod
    def _require_identifier(value: Any, name: str, *, maximum: int = 512) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Feishu {name} is required")
        normalized = value.strip()
        if (
            not normalized
            or normalized != value
            or len(normalized) > maximum
            or any(ord(character) < 32 for character in normalized)
        ):
            raise ValueError(f"Feishu {name} is invalid")
        return normalized

    @staticmethod
    def _mention_open_ids(delivery: Any) -> tuple[str, ...]:
        raw = getattr(delivery, "mention_open_ids", ()) or ()
        if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
            raise ValueError("Feishu mention_open_ids must be a bounded sequence")
        if len(raw) > MAX_MENTION_OPEN_IDS:
            raise ValueError("Feishu mention_open_ids exceeds the safe limit")
        ordered: list[str] = []
        seen: set[str] = set()
        for value in raw:
            if not isinstance(value, str) or not _OPEN_ID_RE.fullmatch(value):
                raise ValueError("Feishu mention_open_ids contains an invalid open_id")
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return tuple(ordered)

    @staticmethod
    def _typed_outbound(
        *, reply_format: str, text: str, mention_open_ids: tuple[str, ...]
    ) -> Any:
        try:
            sdk = importlib.import_module("lark_channel")
        except ImportError as exc:
            raise FeishuSdkUnavailable(
                "Install the 'feishu' optional dependency before using mentions."
            ) from exc
        mentions = [sdk.Identity(open_id=open_id) for open_id in mention_open_ids]
        if reply_format == "text":
            return sdk.OutboundText(text=text, mentions=mentions)
        return sdk.OutboundPost(markdown=text, mentions=mentions)

    async def _send_reply_once(
        self,
        delivery: Any,
        *,
        text: str,
        reply_format: str,
        mention_open_ids: tuple[str, ...],
        idempotency_key: str,
    ) -> FeishuSendResult:
        self._require_app_binding(getattr(delivery, "app_id", None))
        reply_to = self._require_identifier(
            getattr(delivery, "reply_to_message_id", None),
            "reply_to_message_id",
        )
        chat_id = self._require_identifier(
            getattr(delivery, "chat_id", None), "chat_id"
        )
        idempotency_key = self._require_identifier(
            idempotency_key,
            "idempotency_key",
            maximum=50,
        )
        text = str(text or "")
        if not text:
            raise ValueError("Feishu delivery reply text is empty")
        # Defence in depth at the real SDK sink.  Normal delivery payloads are
        # already validated before approval, but this also protects direct
        # callers and corrupted/stale persisted rows from creating mentions.
        if contains_untrusted_at_markup(text):
            raise ValueError("Feishu reply contains untrusted at markup")
        if reply_format not in {"text", "post"}:
            raise ValueError("Feishu delivery reply_format must be text or post")
        if mention_open_ids:
            outbound = self._typed_outbound(
                reply_format=reply_format,
                text=text,
                mention_open_ids=mention_open_ids,
            )
        else:
            outbound = (
                {"text": text}
                if reply_format == "text"
                else {"markdown": text}
            )

        # reply_target_gone='fail' is essential: never fall back to an unrelated
        # fresh group message if the original message was recalled or revoked.
        try:
            with _sdk_log_boundary("reply"):
                result = await self.channel.send(
                    chat_id,
                    outbound,
                    {
                        "reply_to": reply_to,
                        "reply_in_thread": bool(
                            getattr(delivery, "reply_in_thread", False)
                        ),
                        "receive_id_type": "chat_id",
                        "reply_target_gone": "fail",
                        "uuid": idempotency_key,
                        "resolve_mentions_in_text": False,
                    },
                )
        except Exception as exc:
            return FeishuSendResult(False, error_code=_safe_error_code(exc))
        return normalize_send_result(result)

    async def send_reply(self, delivery: Any) -> FeishuSendResult:
        """Compatibility entry point for a delivery that is exactly one chunk."""
        text = str(getattr(delivery, "reply_text", "") or "").strip()
        if len(text) > MAX_WIRE_CHUNK_CHARS:
            raise ValueError(
                "Feishu long delivery must use deterministic local chunks"
            )
        return await self._send_reply_once(
            delivery,
            text=text,
            reply_format=str(
                getattr(delivery, "reply_format", "text") or "text"
            ),
            mention_open_ids=self._mention_open_ids(delivery),
            idempotency_key=str(
                getattr(delivery, "idempotency_key", "") or ""
            ),
        )

    async def send_reply_chunk(
        self,
        delivery: Any,
        *,
        text: str,
        ordinal: int,
        expected_chunks: int,
        idempotency_key: str,
    ) -> FeishuSendResult:
        """Send one preplanned chunk without allowing SDK-side splitting."""
        if (
            isinstance(ordinal, bool)
            or isinstance(expected_chunks, bool)
            or not isinstance(ordinal, int)
            or not isinstance(expected_chunks, int)
            or expected_chunks <= 0
            or expected_chunks > MAX_DELIVERY_CHUNKS
            or ordinal < 0
            or ordinal >= expected_chunks
        ):
            raise ValueError("Feishu delivery chunk position is invalid")
        if not isinstance(text, str) or not text or len(text) > MAX_WIRE_CHUNK_CHARS:
            raise ValueError("Feishu delivery chunk text is invalid")
        mentions = self._mention_open_ids(delivery) if ordinal == 0 else ()
        return await self._send_reply_once(
            delivery,
            text=text,
            reply_format=str(
                getattr(delivery, "reply_format", "text") or "text"
            ),
            mention_open_ids=mentions,
            idempotency_key=idempotency_key,
        )

    async def fetch_message_state(
        self, app_id: str, message_id: str
    ) -> FeishuMessageState:
        """Probe existence without returning message content or vendor payloads."""
        self._require_app_binding(app_id)
        message_id = self._require_identifier(message_id, "message_id")
        try:
            with _sdk_log_boundary("message-state"):
                raw = await self.channel.fetch_message(message_id)
        except Exception:
            return FeishuMessageState("unknown")
        if not isinstance(raw, dict):
            return FeishuMessageState("unknown")
        try:
            code = int(raw.get("code", 0))
        except (TypeError, ValueError):
            return FeishuMessageState("unknown")
        if code in _MESSAGE_NOT_FOUND_CODES:
            return FeishuMessageState("absent")
        if code != 0:
            return FeishuMessageState("unknown")
        data = raw.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return FeishuMessageState("unknown")
        return FeishuMessageState("exists" if items else "absent")

    async def download_inbound_resource(
        self,
        app_id: str,
        message_id: str,
        file_key: str,
        resource_type: str,
        max_bytes: int,
    ) -> bytes:
        """Download only a resource cryptographically bound to a message."""
        self._require_app_binding(app_id)
        message_id = self._require_identifier(message_id, "message_id")
        file_key = self._require_identifier(file_key, "file_key")
        if resource_type not in {"image", "file", "audio", "video"}:
            raise ValueError("Feishu resource_type is not downloadable")
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or max_bytes > MAX_INBOUND_RESOURCE_DOWNLOAD_BYTES
        ):
            raise ValueError("Feishu max_bytes is outside the safe limit")
        sdk_resource_type = "image" if resource_type == "image" else "file"
        if _official_openapi_config(self.channel) is not None:
            with _sdk_log_boundary("resource-download"):
                return await _bounded_message_resource_download(
                    self.channel,
                    message_id=message_id,
                    file_key=file_key,
                    resource_type=sdk_resource_type,
                    max_bytes=max_bytes,
                )
        # Test doubles and narrow local adapters retain the public SDK-shaped
        # seam. Real ``lark_channel`` objects must take the bounded stream path
        # above; the public helper buffers the complete response in memory.
        try:
            with _sdk_log_boundary("resource-download"):
                payload = await self.channel.download_resource(
                    file_key,
                    resource_type=sdk_resource_type,
                    message_id=message_id,
                )
        except Exception as exc:
            raise FeishuSdkOperationError(
                "resource download", code=_safe_error_code(exc)
            ) from None
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise FeishuSdkOperationError(
                "resource download", code="download_failed"
            )
        value = bytes(payload)
        if len(value) > max_bytes:
            raise ValueError("Feishu inbound resource exceeds max_bytes")
        return value

    async def add_reaction(
        self, app_id: str, message_id: str, emoji_type: str
    ) -> FeishuSendResult:
        self._require_app_binding(app_id)
        message_id = self._require_identifier(message_id, "message_id")
        if emoji_type not in ALLOWED_REACTION_EMOJI_TYPES:
            raise ValueError("Feishu emoji_type is not allowlisted")
        try:
            with _sdk_log_boundary("reaction"):
                result = await self.channel.add_reaction(message_id, emoji_type)
        except Exception as exc:
            return FeishuSendResult(False, error_code=_safe_error_code(exc))
        return _normalize_action_result(
            result, target_message_id=message_id
        )

    async def recall_message(
        self, app_id: str, message_id: str
    ) -> FeishuSendResult:
        self._require_app_binding(app_id)
        message_id = self._require_identifier(message_id, "message_id")
        try:
            with _sdk_log_boundary("recall"):
                result = await self.channel.recall_message(message_id)
        except Exception as exc:
            return FeishuSendResult(False, error_code=_safe_error_code(exc))
        return _normalize_action_result(
            result, target_message_id=message_id
        )

    async def send_handoff(self, action: Any) -> FeishuSendResult:
        """Send one closed-contract direct notification to an allowlisted user.

        Target selection happens in the local store.  This adapter accepts no
        receive-id type, SDK JSON, card, mention, reply, or fallback options.
        """
        self._require_app_binding(getattr(action, "app_id", None))
        if getattr(action, "kind", None) != "handoff_notify":
            raise ValueError("Feishu handoff requires handoff_notify action")
        target_open_id = self._require_identifier(
            getattr(action, "target_open_id", None),
            "target_open_id",
            maximum=256,
        )
        if not _OPEN_ID_RE.fullmatch(target_open_id):
            raise ValueError("Feishu handoff target_open_id is invalid")
        if str(getattr(action, "target_message_id", "") or ""):
            raise ValueError("Feishu handoff cannot carry a message target")
        idempotency_key = self._require_identifier(
            getattr(action, "idempotency_key", None),
            "idempotency_key",
            maximum=50,
        )
        expected_key = action_idempotency_key(
            app_id=self.app_id,
            reply_task_id=int(getattr(action, "reply_task_id", 0) or 0),
            action_key=str(getattr(action, "action_key", "") or ""),
            target_id=target_open_id,
        )
        if idempotency_key != expected_key:
            raise ValueError("Feishu handoff idempotency identity changed")
        payload_json = str(getattr(action, "payload_json", "") or "")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("Feishu handoff payload is invalid") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"text"}
            or canonical_payload(payload) != payload_json
        ):
            raise ValueError("Feishu handoff payload is invalid")
        text = payload["text"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 2000
            or any(ord(character) < 32 and character not in "\n\t" for character in text)
        ):
            raise ValueError("Feishu handoff text is invalid")
        if contains_untrusted_at_markup(text):
            raise ValueError("Feishu reply contains untrusted at markup")
        try:
            with _sdk_log_boundary("handoff"):
                result = await self.channel.send(
                    target_open_id,
                    {"text": text},
                    {
                        "receive_id_type": "open_id",
                        "uuid": idempotency_key,
                        "resolve_mentions_in_text": False,
                    },
                )
        except Exception as exc:
            return FeishuSendResult(False, error_code=_safe_error_code(exc))
        return normalize_send_result(result)


def build_channel(
    config: FeishuClientConfig,
    *,
    on_message: Callable[[Any], Awaitable[None]] | None = None,
    on_error: Callable[[Any], Awaitable[None]] | None = None,
    on_reconnecting: Callable[..., Awaitable[None]] | None = None,
    on_reconnected: Callable[..., Awaitable[None]] | None = None,
) -> FeishuChannelClient:
    """Build the real channel.  This is the sole optional-SDK import point."""
    # The pinned SDK's inbound dispatcher logs raw event bodies at DEBUG.
    # Install a permanent Lark payload scrubber before importing/building the
    # channel; operation-scoped calls receive a more specific safe label.
    _ensure_sdk_log_filter()
    try:
        sdk = importlib.import_module("lark_channel")
    except ImportError as exc:  # pragma: no cover - exercised without dependency
        raise FeishuSdkUnavailable(
            "Install the 'feishu' optional dependency before enabling Feishu."
        ) from exc

    security = sdk.SecurityConfig(
        mode=config.security_mode,
        strict_content_text=True,
        legacy_token_cache_fallback=False,
        allow_insecure_ws=False,
        max_ws_fragment_parts=config.max_ws_fragment_parts,
        max_ws_fragment_bytes=config.max_ws_fragment_bytes,
        max_concurrent_ws_handlers=config.max_concurrent_ws_handlers,
    )
    policy = sdk.PolicyConfig(
        dm_policy="open",
        group_policy="open",
        require_mention=True,
        respond_to_mention_all=False,
    )
    # lark-channel-sdk 1.2.0 batches text by default.  A zero delay plus a
    # one-message cap makes every accepted platform message its own dispatch;
    # the enabled chat queue still serializes handlers for the same chat.
    safety = sdk.SafetyConfig(
        text_batch=sdk.TextBatchConfig(
            delay_ms=0,
            long_threshold_chars=1,
            long_delay_ms=0,
            max_messages=1,
            max_chars=1,
        ),
        media_batch=sdk.MediaBatchConfig(enabled=False),
        chat_queue=sdk.ChatQueueConfig(
            enabled=True,
            merge_while_busy=False,
        ),
    )
    # The SDK's defaults may issue extra Contact/IM reads before our local
    # scope policy sees the message.  Disable every unneeded enrichment.  Raw
    # message inclusion is the single exception: SDK 1.2's typed model omits
    # the official root_id and collapses parent_id==root_id into reply=None.
    # app.feishu.ingress reads only a bounded identifier allowlist from raw and
    # never copies raw into normalized models, SQLite, prompts, or logs.
    inbound = sdk.InboundConfig(
        expand_merge_forward=False,
        fetch_interactive_card=False,
        reaction_notifications="off",
        name_cache=sdk.NameCacheConfig(enabled=False),
        drop_self_sent=True,
        inject_chat_mode=False,
        include_raw=True,
        emit_raw_events=False,
    )
    kwargs: dict[str, Any] = {
        "app_id": config.app_id,
        "app_secret": config.app_secret,
        "transport": "ws",
        "security": security,
        "policy": policy,
        "safety": safety,
        "inbound": inbound,
        # Passing a callable is required: None selects the SDK's default
        # contact.v3.user.batch implementation even with its name cache off.
        "name_lookup": _disabled_name_lookup,
    }
    if config.domain:
        kwargs["domain"] = config.domain
    channel = sdk.FeishuChannel(**kwargs)
    if on_message is not None:
        channel.on("message", on_message)
    if on_error is not None:
        channel.on("error", on_error)
    if on_reconnecting is not None:
        channel.on("reconnecting", on_reconnecting)
    if on_reconnected is not None:
        channel.on("reconnected", on_reconnected)
    return FeishuChannelClient(channel, app_id=config.app_id)
