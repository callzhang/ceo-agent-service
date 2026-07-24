"""Durable, offline-only local fallback delivery for Feishu handoffs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.notification import (
    LocalNotificationNotStartedError,
    LocalNotificationResultUnknownError,
    send_macos_local_notification,
)


DEFAULT_LOCAL_NOTIFICATION_LEASE_STALE_SECONDS = 5 * 60
DEFAULT_LOCAL_NOTIFICATION_MAX_ATTEMPTS = 3
MAX_LOCAL_NOTIFICATION_BATCH = 20


class FeishuLocalNotificationWorker:
    """Drain the local handoff outbox without possessing a Feishu client.

    The worker can invoke only the offline macOS notification sink.  Remote
    handoff dependency and supersession checks remain transactional store
    policy and are repeated immediately before each OS effect.
    """

    def __init__(
        self,
        store,
        *,
        app_id: str,
        notifier: Callable[..., None] | None = None,
        batch_limit: int = 10,
        max_attempts: int = DEFAULT_LOCAL_NOTIFICATION_MAX_ATTEMPTS,
        lease_stale_seconds: int = (
            DEFAULT_LOCAL_NOTIFICATION_LEASE_STALE_SECONDS
        ),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_app_id = str(app_id or "").strip()
        if not normalized_app_id:
            raise ValueError("Feishu local notification worker requires app_id")
        if not 1 <= batch_limit <= MAX_LOCAL_NOTIFICATION_BATCH:
            raise ValueError(
                "Feishu local notification batch_limit must be between 1 and 20"
            )
        if max_attempts <= 0 or lease_stale_seconds <= 0:
            raise ValueError(
                "Feishu local notification retry limits must be positive"
            )
        self.store = store
        self.app_id = normalized_app_id
        self.notifier = notifier or send_macos_local_notification
        self.batch_limit = batch_limit
        self.max_attempts = max_attempts
        self.lease_stale_seconds = lease_stale_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        current = self.now()
        if current.tzinfo is None:
            current = current.astimezone()
        return current

    def _retry_at(self, attempts: int) -> str:
        delay_seconds = min(300, max(5, 2 ** max(1, attempts)))
        return (
            self._now().astimezone(timezone.utc)
            + timedelta(seconds=delay_seconds)
        ).isoformat()

    def _transition(self, notification, status: str, **fields):
        return self.store.transition_feishu_local_notification(
            notification.id,
            app_id=self.app_id,
            lease_token=notification.lease_token,
            to_status=status,
            **fields,
        )

    def _finish_failure(self, notification, exc: BaseException) -> str:
        safe_error = f"local_notification_exception:{type(exc).__name__}"
        if isinstance(exc, LocalNotificationNotStartedError) and (
            notification.attempts < self.max_attempts
        ):
            self._transition(
                notification,
                "retry",
                available_at=self._retry_at(notification.attempts),
                error_code="local_notification_not_started",
                error=safe_error,
            )
            return "retry"
        if isinstance(exc, LocalNotificationNotStartedError):
            self._transition(
                notification,
                "failed",
                error_code="local_notification_not_started",
                error=safe_error,
            )
            return "failed"
        uncertainty_code = (
            exc.code
            if isinstance(exc, LocalNotificationResultUnknownError)
            else "unknown"
        )
        self._transition(
            notification,
            "result_unknown",
            error_code=uncertainty_code,
            error=(
                "local_notification_result_unknown:"
                f"{type(exc).__name__}"
            ),
            audit_event_type="result_unknown",
        )
        return "result_unknown"

    async def send_claimed(self, notification) -> str:
        """Revalidate one lease, emit locally, and persist a safe receipt."""
        if notification.app_id != self.app_id or not notification.lease_token:
            raise ValueError("Feishu local notification claim is invalid")
        try:
            current = self.store.begin_feishu_local_notification_mutation(
                notification.id,
                app_id=self.app_id,
                lease_token=notification.lease_token,
            )
        except (ValueError, PermissionError):
            return "cancelled"
        if current is None:
            return "cancelled"

        sink_task = asyncio.create_task(
            asyncio.to_thread(
                self.notifier,
                title=current.title,
                message=current.message,
                url=None,
            )
        )
        try:
            await asyncio.shield(sink_task)
        except asyncio.CancelledError as cancelled:
            # The executor thread cannot be recalled.  The sink itself is
            # bounded, so wait for its definite result and persist that result
            # before propagating runtime cancellation.
            try:
                await sink_task
                self._transition(current, "sent")
            except Exception as exc:
                try:
                    self._finish_failure(current, exc)
                except Exception:
                    # A later startup recovery still owns this lease.  Never
                    # replace cancellation with a persistence error.
                    pass
            raise cancelled
        except Exception as exc:
            return self._finish_failure(current, exc)

        self._transition(current, "sent")
        return "sent"

    async def process_once(self, limit: int | None = None) -> int:
        selected_limit = self.batch_limit if limit is None else int(limit)
        if selected_limit <= 0:
            return 0
        selected_limit = min(selected_limit, self.batch_limit)
        recover_orphaned_local_notifications(
            self.store,
            app_id=self.app_id,
            stale_after_seconds=self.lease_stale_seconds,
            now=self._now(),
        )
        claimed = self.store.claim_feishu_local_notifications(
            selected_limit,
            app_id=self.app_id,
            now=self._now().isoformat(),
        )
        processed = 0
        for notification in claimed:
            await self.send_claimed(notification)
            processed += 1
        return processed


def recover_orphaned_local_notifications(
    store,
    *,
    app_id: str,
    stale_after_seconds: int = DEFAULT_LOCAL_NOTIFICATION_LEASE_STALE_SECONDS,
    now: datetime | None = None,
) -> int:
    """Return stale local leases to bounded retry with durable audit evidence."""
    return store.recover_stale_feishu_local_notifications(
        app_id=app_id,
        stale_after_seconds=stale_after_seconds,
        now=now,
    )
