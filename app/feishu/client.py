"""Delayed adapter for the official ``lark-channel-sdk``.

Importing this module never imports ``lark_channel``.  The optional dependency
is loaded only when :func:`build_channel` is called after explicit enablement.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class FeishuSdkUnavailable(RuntimeError):
    pass


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


def _code(value: Any) -> str:
    code = getattr(value, "code", "")
    return str(getattr(code, "value", code) or "").strip().lower()


def _request_log_id(raw: Any, *, depth: int = 0) -> str:
    """Extract only a request identifier; never persist or expose raw results."""
    if depth > 3 or not isinstance(raw, dict):
        return ""
    for key in ("request_log_id", "log_id", "request_id", "x-tt-logid"):
        value = raw.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()[:256]
    for key in ("headers", "data", "response", "raw"):
        found = _request_log_id(raw.get(key), depth=depth + 1)
        if found:
            return found
    return ""


def normalize_send_result(result: Any) -> FeishuSendResult:
    success = bool(getattr(result, "success", False))
    error = getattr(result, "error", None)
    return FeishuSendResult(
        success=success,
        message_id=str(getattr(result, "message_id", "") or ""),
        request_log_id=_request_log_id(getattr(result, "raw", None)),
        error_code="" if success else (_code(error) or "unknown"),
    )


class FeishuChannelClient:
    """Small facade that exposes only lifecycle and fail-closed text replies."""

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

    async def send_reply(self, delivery) -> FeishuSendResult:
        if str(getattr(delivery, "app_id", "") or "").strip() != self.app_id:
            raise PermissionError("Feishu delivery App ID does not match client")
        if not str(getattr(delivery, "reply_to_message_id", "") or "").strip():
            raise ValueError("Feishu delivery requires reply_to_message_id")
        if not str(getattr(delivery, "idempotency_key", "") or "").strip():
            raise ValueError("Feishu delivery requires a stable idempotency key")
        text = str(getattr(delivery, "reply_text", "") or "").strip()
        if not text:
            raise ValueError("Feishu delivery reply text is empty")

        # reply_target_gone='fail' is essential: never fall back to an unrelated
        # fresh group message if the original message was recalled or revoked.
        result = await self.channel.send(
            delivery.chat_id,
            {"text": text},
            {
                "reply_to": delivery.reply_to_message_id,
                "reply_in_thread": bool(delivery.reply_in_thread),
                "receive_id_type": "chat_id",
                "reply_target_gone": "fail",
                "uuid": delivery.idempotency_key,
                "resolve_mentions_in_text": False,
            },
        )
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
    kwargs: dict[str, Any] = {
        "app_id": config.app_id,
        "app_secret": config.app_secret,
        "transport": "ws",
        "security": security,
        "policy": policy,
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
