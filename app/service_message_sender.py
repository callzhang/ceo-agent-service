"""Prepare immutable service messages before handing them to channel adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.outbound_postfix import PreparedOutboundMessage


@dataclass(frozen=True)
class SendReceipt:
    """The prepared service message and the result returned by its provider."""

    message: PreparedOutboundMessage
    provider_result: Any


class ServiceMessageSender:
    """Single facade for service-owned DingTalk and WeChat text dispatch."""

    def __init__(self, *, store, dingtalk=None, wechat=None) -> None:
        self.store = store
        self.dingtalk = dingtalk
        self.wechat = wechat

    def prepare(
        self,
        *,
        channel: str,
        delivery_key: str,
        body: str,
        original_text: str = "",
    ) -> PreparedOutboundMessage:
        return self.store.prepare_outbound_postfix(
            channel,
            delivery_key,
            body,
            original_text,
        )

    def send_dingtalk(
        self,
        *,
        delivery_key: str,
        body: str,
        original_text: str = "",
        conversation_id: str | None,
        **target: Any,
    ) -> SendReceipt:
        message = self.prepare(
            channel="dingtalk",
            delivery_key=delivery_key,
            body=body,
            original_text=original_text,
        )
        return self.send_dingtalk_prepared(
            message,
            conversation_id=conversation_id,
            **target,
        )

    def send_dingtalk_prepared(
        self,
        message: PreparedOutboundMessage,
        *,
        conversation_id: str | None,
        **target: Any,
    ) -> SendReceipt:
        self._require_persisted_message(message, channel="dingtalk")
        if self.dingtalk is None:
            raise RuntimeError("DingTalk adapter is required")
        provider_result = self.dingtalk.send_message(
            conversation_id,
            message.final_body,
            **target,
        )
        return SendReceipt(message=message, provider_result=provider_result)

    def send_wechat(
        self,
        *,
        delivery_key: str,
        body: str,
        original_text: str = "",
        target_label: str,
        search_query: str | None = None,
        expected_recent_text: str | None = None,
    ) -> SendReceipt:
        message = self.prepare(
            channel="wechat",
            delivery_key=delivery_key,
            body=body,
            original_text=original_text,
        )
        return self.send_wechat_prepared(
            message,
            target_label=target_label,
            search_query=search_query,
            expected_recent_text=expected_recent_text,
        )

    def send_wechat_prepared(
        self,
        message: PreparedOutboundMessage,
        *,
        target_label: str,
        search_query: str | None = None,
        expected_recent_text: str | None = None,
    ) -> SendReceipt:
        """Dispatch a prepared WeChat message through the IPC/accessibility runner."""
        self._require_persisted_message(message, channel="wechat")
        if self.wechat is None:
            raise RuntimeError("WeChat adapter is required")
        provider_result = self.wechat.send(
            target_label,
            message.final_body,
            search_query=search_query,
            expected_recent_text=expected_recent_text,
        )
        return SendReceipt(message=message, provider_result=provider_result)

    def _require_persisted_message(
        self,
        message: PreparedOutboundMessage,
        *,
        channel: str,
    ) -> None:
        if not isinstance(message, PreparedOutboundMessage) or message.channel != channel:
            raise ValueError(f"prepared message channel must be {channel}")
        persisted = self.store.get_outbound_postfix(
            message.channel,
            message.delivery_key,
        )
        if persisted != message:
            raise ValueError("persisted prepared message is required for dispatch")
