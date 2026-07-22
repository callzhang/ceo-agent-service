"""Bounded, network-free maintenance for persisted Feishu channel data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class FeishuMaintenanceResult:
    cutoff: str
    deleted_events: int
    batches: int
    more_may_remain: bool


def purge_expired_feishu_events(
    store,
    *,
    retention_days: int,
    app_id: str = "",
    now: datetime | None = None,
    batch_limit: int = 500,
    max_batches: int = 20,
) -> FeishuMaintenanceResult:
    """Logically remove normalized event rows using short DB transactions."""
    if retention_days <= 0:
        raise ValueError("Feishu retention_days must be positive")
    if batch_limit <= 0 or max_batches <= 0:
        raise ValueError("Feishu retention batch settings must be positive")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Feishu maintenance now must include timezone")
    cutoff = (
        current.astimezone(timezone.utc) - timedelta(days=retention_days)
    ).isoformat()
    total = 0
    batches = 0
    last_deleted = 0
    while batches < max_batches:
        last_deleted = store.purge_feishu_events_before(
            cutoff,
            app_id=app_id,
            batch_limit=batch_limit,
        )
        batches += 1
        total += last_deleted
        if last_deleted < batch_limit:
            break
    return FeishuMaintenanceResult(
        cutoff=cutoff,
        deleted_events=total,
        batches=batches,
        more_may_remain=last_deleted == batch_limit,
    )
