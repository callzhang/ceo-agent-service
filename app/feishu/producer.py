"""Persist normalized Feishu events and atomically enqueue eligible replies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.feishu.ingress import (
    IngressDecision,
    evaluate_ingress,
    normalize_sdk_envelope,
    scope_target,
)
from app.feishu.models import (
    FeishuEventRecord,
    FeishuInboundMessage,
    FeishuNormalizedEnvelope,
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
        media_enabled: bool = False,
        media_max_assets: int = 8,
        now: Callable[[], datetime] | None = None,
    ):
        if not app_id.strip():
            raise ValueError("Feishu app_id is required")
        if stale_event_seconds <= 0:
            raise ValueError("stale_event_seconds must be positive")
        if media_max_assets <= 0 or media_max_assets > 8:
            raise ValueError("media_max_assets must be between 1 and 8")
        self.store = store
        self.app_id = app_id.strip()
        self.stale_event_seconds = stale_event_seconds
        self.media_enabled = bool(media_enabled)
        self.media_max_assets = media_max_assets
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
        if message.message_type != "text":
            # Resource-bearing SDK turns must retain their envelope completion
            # metadata. Rich posts can carry attachments too, and the legacy
            # message-only seam cannot prove that the resource set was empty.
            decision = IngressDecision(
                False, "rejected", "media_requires_normalized_envelope"
            )
        record = self.store.record_feishu_event(
            message,
            eligibility_status=decision.status,
            reject_reason=decision.reason,
            store_body=decision.store_body,
            enqueue_eligible=decision.eligible,
        )
        return FeishuProduceResult(message, decision, record)

    def ingest_envelope(
        self, envelope: FeishuNormalizedEnvelope
    ) -> FeishuProduceResult:
        """Persist one normalized SDK envelope without doing network I/O.

        Resource keys cross the durable boundary only after the ordinary scope
        policy has approved the message and the media feature gate is open.
        Resource-bearing events are deliberately recorded receive-only: the
        runtime attaches their reply task only after every asset is terminal.
        """
        message = envelope.message
        if message.app_id != self.app_id:
            raise ValueError("Feishu message app_id does not match producer")

        scope = self._scope(message)
        decision = evaluate_ingress(
            message,
            scope,
            stale_event_seconds=self.stale_event_seconds,
            now=self.now(),
        )
        normalization_metadata = {
            "normalization_version": envelope.normalization_version,
            "content_truncated": envelope.content_truncated,
            "resource_truncated": envelope.resource_truncated,
        }

        # A truncated normalization result is not a complete user turn.  It
        # must never enqueue, even when all other admission checks pass.
        if envelope.content_truncated or envelope.resource_truncated:
            if decision.eligible:
                decision = IngressDecision(
                    False, "rejected", "normalization_truncated"
                )
            record = self.store.record_feishu_event(
                message,
                eligibility_status=decision.status,
                reject_reason=decision.reason,
                store_body=decision.store_body,
                enqueue_eligible=False,
                **normalization_metadata,
            )
            return FeishuProduceResult(message, decision, record)

        # Preserve the original fast path for messages with no resource
        # references.  In particular, text-only installs need no media gate.
        if not envelope.resources:
            record = self.store.record_feishu_event(
                message,
                eligibility_status=decision.status,
                reject_reason=decision.reason,
                store_body=decision.store_body,
                enqueue_eligible=decision.eligible,
                **normalization_metadata,
            )
            return FeishuProduceResult(message, decision, record)

        if not decision.eligible:
            record = self.store.record_feishu_event(
                message,
                eligibility_status=decision.status,
                reject_reason=decision.reason,
                store_body=False,
                enqueue_eligible=False,
                **normalization_metadata,
            )
            return FeishuProduceResult(message, decision, record)

        if not self.media_enabled:
            decision = IngressDecision(False, "rejected", "media_disabled")
            record = self.store.record_feishu_event(
                message,
                eligibility_status=decision.status,
                reject_reason=decision.reason,
                store_body=False,
                enqueue_eligible=False,
                **normalization_metadata,
            )
            return FeishuProduceResult(message, decision, record)

        if len(envelope.resources) > self.media_max_assets:
            decision = IngressDecision(
                False, "rejected", "media_resource_limit_exceeded"
            )
            record = self.store.record_feishu_event(
                message,
                eligibility_status=decision.status,
                reject_reason=decision.reason,
                store_body=False,
                enqueue_eligible=False,
                **normalization_metadata,
            )
            return FeishuProduceResult(message, decision, record)

        # Pure media turns need a non-secret, normalized body for the durable
        # task payload.  Opaque resource keys never enter this copy.
        persisted_message = message
        if not message.body_text.strip() and message.normalized_summary.strip():
            persisted_message = message.model_copy(
                update={"body_text": message.normalized_summary}
            )
        record = self.store.record_feishu_event(
            persisted_message,
            eligibility_status=decision.status,
            reject_reason=decision.reason,
            store_body=True,
            enqueue_eligible=False,
            media_candidates=envelope.resources,
            media_max_event_resources=self.media_max_assets,
            **normalization_metadata,
        )
        if record.eligibility_status != "eligible":
            # The first observation is immutable.  Approval granted after an
            # earlier rejected delivery must not turn a replay into a fresh
            # opportunity to persist its opaque resource keys.
            replay_decision = IngressDecision(
                False,
                record.eligibility_status,
                record.reject_reason or "event_not_eligible",
            )
            return FeishuProduceResult(
                persisted_message, replay_decision, record
            )
        if not record.inserted and (
            record.message_type != persisted_message.message_type
            or record.chat_id != persisted_message.chat_id
            or record.sender_open_id != persisted_message.sender_open_id
            or record.body_text != persisted_message.body_text
            or record.thread_id != persisted_message.thread_id
            or record.root_message_id != persisted_message.root_message_id
            or record.parent_message_id != persisted_message.parent_message_id
            or record.normalized_summary
            != persisted_message.normalized_summary
            or record.normalization_version
            != envelope.normalization_version
            or record.content_truncated != envelope.content_truncated
            or record.resource_truncated != envelope.resource_truncated
            or not record.media_required
        ):
            return FeishuProduceResult(
                persisted_message,
                IngressDecision(False, "rejected", "event_replay_mismatch"),
                record,
            )
        return FeishuProduceResult(persisted_message, decision, record)

    def ingest_sdk_message(self, sdk_message: Any) -> FeishuProduceResult:
        envelope = normalize_sdk_envelope(
            sdk_message,
            app_id=self.app_id,
            now=self.now,
        )
        return self.ingest_envelope(envelope)
