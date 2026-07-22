"""Persist normalized Feishu events and atomically enqueue eligible replies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.feishu.ingress import (
    IngressDecision,
    evaluate_ingress,
    normalize_sdk_message,
    scope_target,
)
from app.feishu.models import (
    FeishuEventRecord,
    FeishuInboundMessage,
    FeishuReplyScope,
)


@dataclass(frozen=True)
class FeishuProduceResult:
    message: FeishuInboundMessage
    decision: IngressDecision
    record: FeishuEventRecord

    @property
    def enqueued(self) -> bool:
        return bool(self.record.enqueued)


class FeishuReplyProducer:
    """Fast ingress path: no Codex, no network calls, no outbound capability."""

    def __init__(
        self,
        store,
        *,
        app_id: str,
        stale_event_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ):
        if not app_id.strip():
            raise ValueError("Feishu app_id is required")
        if stale_event_seconds <= 0:
            raise ValueError("stale_event_seconds must be positive")
        self.store = store
        self.app_id = app_id.strip()
        self.stale_event_seconds = stale_event_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _scope(self, message: FeishuInboundMessage) -> FeishuReplyScope | None:
        target_type, target_id = scope_target(message)
        scope = self.store.get_feishu_reply_scope(
            message.app_id, target_type, target_id
        )
        seen_at = message.received_at or self.now().astimezone(timezone.utc).isoformat()
        if scope is None:
            # Discovery is intentionally pending+disabled.  Unknown targets are
            # visible for local review, but their message body is not retained.
            scope = FeishuReplyScope(
                app_id=message.app_id,
                target_type=target_type,
                target_id=target_id,
                display_name=(
                    message.sender_name
                    if target_type == "direct_sender"
                    else message.chat_title
                ),
                trigger_mode=(
                    "every_inbound_text"
                    if target_type == "direct_sender"
                    else "mention_bot"
                ),
                enabled=False,
                binding_status="pending",
                last_seen_at=seen_at,
            )
            self.store.upsert_feishu_reply_scope(scope)
            return None

        updates: dict[str, str] = {"last_seen_at": seen_at}
        discovered_name = (
            message.sender_name if target_type == "direct_sender" else message.chat_title
        )
        if discovered_name and not scope.display_name:
            updates["display_name"] = discovered_name
        refreshed = scope.model_copy(update=updates)
        self.store.upsert_feishu_reply_scope(refreshed)
        return refreshed

    def ingest(self, message: FeishuInboundMessage) -> FeishuProduceResult:
        if message.app_id != self.app_id:
            raise ValueError("Feishu message app_id does not match producer")
        scope = self._scope(message)
        decision = evaluate_ingress(
            message,
            scope,
            stale_event_seconds=self.stale_event_seconds,
            now=self.now(),
        )
        record = self.store.record_feishu_event(
            message,
            eligibility_status=decision.status,
            reject_reason=decision.reason,
            store_body=decision.store_body,
            enqueue_eligible=decision.eligible,
        )
        return FeishuProduceResult(message, decision, record)

    def ingest_sdk_message(self, sdk_message: Any) -> FeishuProduceResult:
        message = normalize_sdk_message(
            sdk_message,
            app_id=self.app_id,
            now=self.now,
        )
        return self.ingest(message)
