from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol


@dataclass(frozen=True)
class DispatchEnvelope:
    """A claim reference; the business payload stays in its source table."""

    adapter_name: str
    source_id: str
    available_at: datetime
    priority: int
    attempt: int
    generation: int


@dataclass(frozen=True)
class QueueMetrics:
    pending: int
    due: int
    oldest_available_at: datetime | None
    running: int
    latest_error: str

    @property
    def claimed(self) -> int:
        return self.running


class QueueAdapter(Protocol):
    name: str

    def metrics(self, now: datetime) -> QueueMetrics: ...

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None: ...

    def release(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
    ) -> None: ...
