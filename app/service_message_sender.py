"""Prepare immutable service messages before handing them to channel adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

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
        feedback_base_url: str | None = None,
    ) -> PreparedOutboundMessage:
        return self.store.prepare_outbound_postfix(
            channel,
            delivery_key,
            body,
            original_text,
            feedback_base_url=feedback_base_url,
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
        persisted_receipt = self.store.get_outbound_postfix_receipt(
            message.channel, message.delivery_key
        )
        if persisted_receipt is not None:
            return SendReceipt(message=message, provider_result=persisted_receipt)
        target.setdefault(
            "idempotency_uuid",
            str(uuid5(NAMESPACE_URL, f"{message.channel}:{message.delivery_key}")),
        )
        provider_result = self.dingtalk.send_message(
            conversation_id,
            message.final_body,
            **target,
        )
        if isinstance(provider_result, dict) and provider_result.get("success") is False:
            return SendReceipt(message=message, provider_result=provider_result)
        receipt = self.store.record_outbound_postfix_receipt(
            message.channel,
            message.delivery_key,
            provider_result,
        )
        return SendReceipt(message=message, provider_result=receipt)

    def send_dingtalk_ding(
        self,
        *,
        delivery_key: str,
        body: str,
        user_id: str | None = None,
    ) -> SendReceipt:
        message = self.prepare(
            channel="dingtalk",
            delivery_key=delivery_key,
            body=body,
        )
        return self.send_dingtalk_ding_prepared(message, user_id=user_id)

    def send_dingtalk_ding_prepared(
        self,
        message: PreparedOutboundMessage,
        *,
        user_id: str | None = None,
    ) -> SendReceipt:
        """Dispatch a persisted diagnostic DING through the raw adapter."""
        self._require_persisted_message(message, channel="dingtalk")
        if self.dingtalk is None:
            raise RuntimeError("DingTalk adapter is required")
        if user_id:
            provider_result = self.dingtalk.ding_user(user_id, message.final_body)
        else:
            provider_result = self.dingtalk.ding_self(message.final_body)
        return SendReceipt(message=message, provider_result=provider_result)

    def send_dingtalk_reply_to_trigger_prepared(
        self,
        message: PreparedOutboundMessage,
        *,
        conversation: Any,
        trigger: Any,
    ) -> SendReceipt:
        """Dispatch a persisted reply through the native DingTalk reply path."""
        self._require_persisted_message(message, channel="dingtalk")
        if self.dingtalk is None:
            raise RuntimeError("DingTalk adapter is required")
        persisted_receipt = self.store.get_outbound_postfix_receipt(
            message.channel,
            message.delivery_key,
        )
        if persisted_receipt is not None:
            return SendReceipt(message=message, provider_result=persisted_receipt)
        provider_result = self.dingtalk.send_reply_to_trigger(
            conversation,
            trigger,
            message.final_body,
        )
        verification = self._verify_native_reply_result(provider_result)
        if verification != "sent":
            raise RuntimeError(f"native DingTalk reply send is {verification}")
        receipt = self.store.record_outbound_postfix_receipt(
            message.channel,
            message.delivery_key,
            provider_result,
        )
        return SendReceipt(message=message, provider_result=receipt)

    def _verify_native_reply_result(self, provider_result: Any) -> str:
        if isinstance(provider_result, dict):
            if provider_result.get("success") is False:
                return "failed"
            result = provider_result.get("result")
            if isinstance(result, dict) and str(
                result.get("processQueryKey") or ""
            ).strip():
                return "sent"
        verifier = getattr(self.dingtalk, "verify_message_send_result", None)
        if callable(verifier):
            verification = verifier(provider_result)
            if isinstance(verification, dict):
                state = str(verification.get("state") or "").strip()
                if state in {"sent", "failed", "ambiguous"}:
                    return state
            return "ambiguous"
        if not isinstance(provider_result, dict):
            return "ambiguous"
        result_text = str(provider_result)
        return "sent" if "processQueryKey" in result_text else "ambiguous"

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


def agent_message_delivery_key(
    *,
    task_id: int,
    execution_generation: str,
    proposal_revision: int,
    action_index: int,
) -> str:
    """Return the immutable delivery identity for one Agent proposal action."""
    if task_id <= 0 or proposal_revision < 0 or action_index < 0:
        raise ValueError("agent message delivery identity is invalid")
    generation = execution_generation.strip()
    if not generation:
        raise ValueError("execution generation is required")
    return (
        f"agent-message:{task_id}:{generation}:"
        f"revision:{proposal_revision}:action:{action_index}"
    )
