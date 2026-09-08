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
    due: int
    claimed: int


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
