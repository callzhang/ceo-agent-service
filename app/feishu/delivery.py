"""Exactly-once-oriented, fail-closed Feishu delivery state machine."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable
from uuid import UUID, uuid5

from app.feishu.client import FeishuSendResult


DELIVERY_UUID_NAMESPACE = UUID("49f6141e-9852-5e2d-8e9e-3c4207468328")
KNOWN_ERROR_CODES = frozenset(
    {
        "format_error",
        "target_revoked",
        "rate_limited",
        "permission_denied",
        "upload_failed",
        "download_failed",
        "ssrf_blocked",
        "send_timeout",
        "not_connected",
        "unknown",
    }
)
RETRYABLE_CODES = frozenset({"rate_limited", "not_connected"})
TERMINAL_CODES = frozenset(
    {
        "format_error",
        "target_revoked",
        "permission_denied",
        "upload_failed",
        "download_failed",
        "ssrf_blocked",
    }
)
DEFAULT_SEND_TIMEOUT_SECONDS = 60.0
DEFAULT_SEND_LEASE_STALE_SECONDS = 5 * 60


@dataclass(frozen=True)
class FeishuDeliveryOutcome:
    status: str
    error_code: str = ""
    error: str = ""
    message_id: str = ""
    request_log_id: str = ""


def delivery_idempotency_key(
    *, app_id: str, reply_task_id: int, trigger_message_id: str
) -> str:
    """Create the stable UUID stored once with a delivery and reused forever."""
    if not app_id or reply_task_id <= 0 or not trigger_message_id:
        raise ValueError("delivery idempotency identity is incomplete")
    name = f"{app_id}\0{reply_task_id}\0{trigger_message_id}"
    return str(uuid5(DELIVERY_UUID_NAMESPACE, name))


def error_code(value: Any) -> str:
    code = getattr(value, "code", "")
    if not code:
        code = getattr(getattr(value, "error", None), "code", "")
    code = str(getattr(code, "value", code) or "").strip().lower()
    if code in KNOWN_ERROR_CODES:
        return code
    if isinstance(value, (TimeoutError, asyncio.TimeoutError)):
        return "send_timeout"
    name = type(value).__name__.lower()
    if "timeout" in name:
        return "send_timeout"
    if "notconnected" in name or "not_connected" in name:
        return "not_connected"
    return "unknown"


class FeishuDeliverySender:
    """Only class in the channel allowed to invoke ``client.send_reply``."""

    def __init__(
        self,
        store,
        client,
        *,
        sender_enabled: bool = False,
        live_send_allowed: bool = False,
        send_mode: str = "confirm",
        max_sends_per_minute: int = 10,
        max_attempts: int = 3,
        send_timeout_seconds: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        send_lease_stale_seconds: int = DEFAULT_SEND_LEASE_STALE_SECONDS,
        now: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ):
        if send_mode not in {"confirm", "auto"}:
            raise ValueError("Feishu send_mode must be confirm or auto")
        if (
            max_sends_per_minute <= 0
            or max_attempts <= 0
            or send_timeout_seconds <= 0
            or send_lease_stale_seconds <= send_timeout_seconds
        ):
            raise ValueError("Feishu delivery limits must be positive")
        self.store = store
        self.client = client
        self.sender_enabled = sender_enabled
        self.live_send_allowed = live_send_allowed
        self.send_mode = send_mode
        self.max_sends_per_minute = max_sends_per_minute
        self.max_attempts = max_attempts
        self.send_timeout_seconds = send_timeout_seconds
        self.send_lease_stale_seconds = send_lease_stale_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic_clock = monotonic_clock
        self._sent_times: deque[float] = deque()

    @property
    def outbound_gate_open(self) -> bool:
        return self.sender_enabled and self.live_send_allowed

    def _authenticated_app_id(self) -> str:
        app_id = str(getattr(self.client, "app_id", "") or "").strip()
        if not app_id:
            raise PermissionError("Feishu authenticated client App ID is unavailable")
        return app_id

    def _require_delivery_binding(
        self, delivery, *, require_send_lease: bool = True
    ) -> str:
        app_id = self._authenticated_app_id()
        if str(getattr(delivery, "app_id", "") or "").strip() != app_id:
            raise PermissionError("Feishu delivery App ID does not match client")
        lease_token = str(getattr(delivery, "lease_token", "") or "").strip()
        if require_send_lease and not lease_token:
            raise ValueError("Feishu delivery has no active send lease")
        validated = self.store.validate_feishu_delivery_for_send(
            int(getattr(delivery, "id", 0) or 0),
            app_id=app_id,
            lease_token=(lease_token if require_send_lease else ""),
        )
        if validated.attempt_id != int(
            getattr(delivery, "attempt_id", 0) or 0
        ):
            raise ValueError("Feishu delivery attempt identity changed")
        return app_id

    def _rate_slot(self) -> bool:
        current = self.monotonic_clock()
        while self._sent_times and current - self._sent_times[0] >= 60:
            self._sent_times.popleft()
        if len(self._sent_times) >= self.max_sends_per_minute:
            return False
        self._sent_times.append(current)
        return True

    def _retry_at(self, attempts: int, *, rate_limited: bool = False) -> str:
        seconds = 60 if rate_limited else min(300, max(5, 2 ** max(1, attempts)))
        return (self.now().astimezone(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    def _transition(self, delivery, status: str, **fields):
        return self.store.transition_feishu_delivery(
            delivery.id,
            from_statuses=("sending",),
            to_status=status,
            expected_lease_token=delivery.lease_token,
            **fields,
        )

    def _finish_result(
        self, delivery, result: FeishuSendResult
    ) -> FeishuDeliveryOutcome:
        if result.success:
            if result.message_id:
                self._transition(
                    delivery,
                    "sent",
                    feishu_message_id=result.message_id,
                    request_log_id=result.request_log_id,
                )
                return FeishuDeliveryOutcome(
                    "sent",
                    message_id=result.message_id,
                    request_log_id=result.request_log_id,
                )
            self._transition(
                delivery,
                "send_unknown",
                request_log_id=result.request_log_id,
                error_code="unknown",
                error="successful_response_missing_message_id",
            )
            return FeishuDeliveryOutcome(
                "send_unknown", "unknown", "successful_response_missing_message_id"
            )

        code = result.error_code if result.error_code in KNOWN_ERROR_CODES else "unknown"
        error = f"feishu_send_failed:{code}"
        if code in RETRYABLE_CODES and delivery.attempts < self.max_attempts:
            self._transition(
                delivery,
                "retry",
                available_at=self._retry_at(
                    delivery.attempts, rate_limited=code == "rate_limited"
                ),
                request_log_id=result.request_log_id,
                error_code=code,
                error=error,
            )
            return FeishuDeliveryOutcome("retry", code, error)
        if code in TERMINAL_CODES or code in RETRYABLE_CODES:
            self._transition(
                delivery,
                "failed",
                request_log_id=result.request_log_id,
                error_code=code,
                error=error,
            )
            return FeishuDeliveryOutcome("failed", code, error)
        # Only timeouts and unknown results lack proof of non-delivery.
        self._transition(
            delivery,
            "send_unknown",
            request_log_id=result.request_log_id,
            error_code=code,
            error=error,
        )
        return FeishuDeliveryOutcome("send_unknown", code, error)

    async def send_claimed(self, delivery) -> FeishuDeliveryOutcome:
        if not self.outbound_gate_open:
            raise PermissionError("Feishu outbound gates are closed")
        if delivery.status != "sending":
            raise ValueError("Feishu delivery must be atomically claimed first")
        self._require_delivery_binding(delivery)
        if not self._rate_slot():
            self._transition(
                delivery,
                "retry",
                available_at=self._retry_at(delivery.attempts, rate_limited=True),
                error_code="rate_limited",
                error="local_rate_limit",
            )
            return FeishuDeliveryOutcome(
                "retry", "rate_limited", "local_rate_limit"
            )
        try:
            result = await asyncio.wait_for(
                self.client.send_reply(delivery),
                timeout=self.send_timeout_seconds,
            )
        except asyncio.CancelledError:
            # The caller timed out while an upstream action may already have
            # happened.  Preserve uncertainty rather than leaving ``sending``
            # or making the delivery eligible for a blind retry.
            self._transition(
                delivery,
                "send_unknown",
                error_code="send_timeout",
                error="feishu_send_cancelled_result_unknown",
            )
            raise
        except Exception as exc:
            code = error_code(exc)
            error = f"feishu_send_exception:{code}:{type(exc).__name__}"
            if code in RETRYABLE_CODES and delivery.attempts < self.max_attempts:
                self._transition(
                    delivery,
                    "retry",
                    available_at=self._retry_at(delivery.attempts),
                    error_code=code,
                    error=error,
                )
                return FeishuDeliveryOutcome("retry", code, error)
            if code in TERMINAL_CODES or code in RETRYABLE_CODES:
                self._transition(
                    delivery, "failed", error_code=code, error=error
                )
                return FeishuDeliveryOutcome("failed", code, error)
            # An exception during send may occur after the upstream accepted it.
            self._transition(
                delivery, "send_unknown", error_code=code, error=error
            )
            return FeishuDeliveryOutcome("send_unknown", code, error)
        return self._finish_result(delivery, result)

    async def process_once(self, limit: int = 10) -> int:
        """Claim rows for this client; confirm mode requires durable approval."""
        if limit <= 0 or not self.outbound_gate_open:
            return 0
        app_id = self._authenticated_app_id()
        processed = 0
        for _ in range(limit):
            recover_orphaned_sending(
                self.store,
                app_id=app_id,
                max_age_seconds=self.send_lease_stale_seconds,
                now=self.now(),
            )
            deliveries = self.store.claim_feishu_deliveries(
                1,
                statuses=("ready_to_send", "retry"),
                app_id=app_id,
                approved_only=self.send_mode == "confirm",
            )
            if not deliveries:
                break
            await self.send_claimed(deliveries[0])
            processed += 1
        return processed

    async def approve_and_send(
        self,
        delivery_id: int,
        *,
        approved_by: str = "local-audit-runtime",
    ) -> FeishuDeliveryOutcome:
        """Approve durably, then send through this already-connected client."""
        if not self.outbound_gate_open:
            raise PermissionError("Feishu outbound gates are closed")
        app_id = self._authenticated_app_id()
        pending = self.store.get_feishu_delivery(delivery_id)
        if pending is None:
            raise ValueError(f"Feishu delivery {delivery_id} is not sendable")
        self._require_delivery_binding(pending, require_send_lease=False)
        if not pending.approved_at:
            pending = self.store.approve_feishu_delivery(
                delivery_id,
                app_id=app_id,
                approved_by=approved_by,
            )
        delivery = self.store.claim_feishu_delivery(
            delivery_id,
            statuses=("ready_to_send", "retry"),
            app_id=app_id,
            approved_only=True,
        )
        if delivery is None:
            raise ValueError(f"Feishu delivery {delivery_id} is not sendable")
        return await self.send_claimed(delivery)

    def reject(self, delivery_id: int) -> None:
        updated = self.store.reject_feishu_delivery(
            delivery_id,
            app_id=self._authenticated_app_id(),
            error="user_rejected",
        )
        if updated.status != "rejected":
            raise ValueError(f"Feishu delivery {delivery_id} is not rejectable")


def recover_orphaned_sending(
    store,
    *,
    app_id: str = "",
    max_age_seconds: int = DEFAULT_SEND_LEASE_STALE_SECONDS,
    now: datetime | None = None,
) -> int:
    """Never blindly resend after a crash; require explicit human verification."""
    recovered = 0
    for delivery in store.list_stale_feishu_sending(
        max_age_seconds, app_id=app_id, now=now
    ):
        try:
            updated = store.transition_feishu_delivery(
                delivery.id,
                from_statuses=("sending",),
                to_status="send_unknown",
                error_code="unknown",
                error="orphaned_sending_requires_review",
                expected_lease_token=delivery.lease_token,
            )
        except ValueError:
            continue
        recovered += int(updated is not None)
    return recovered
