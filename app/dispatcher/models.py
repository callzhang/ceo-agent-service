from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
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


class ClaimLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimToken:
    adapter_name: str
    source_id: str
    owner: str
    generation: int


class QueueAdapter(Protocol):
    name: str

    def metrics(self, now: datetime) -> QueueMetrics: ...

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        owner_pid: int | None = None,
        lease: timedelta,
    ) -> DispatchEnvelope | None: ...

    def release(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
    ) -> None: ...

    def renew(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        lease: timedelta,
    ) -> None: ...

    def assert_current(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime
    ) -> None: ...

    def complete(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime
    ) -> None: ...

    def record_lease_error(
        self, envelope: DispatchEnvelope, *, owner: str, error: str, now: datetime
    ) -> None: ...


class ClaimGuard:
    def __init__(
        self,
        *,
        adapter: QueueAdapter,
        envelope: DispatchEnvelope,
        owner: str,
    ) -> None:
        self.adapter = adapter
        self.envelope = envelope
        self.token = ClaimToken(
            adapter_name=envelope.adapter_name,
            source_id=envelope.source_id,
            owner=owner,
            generation=envelope.generation,
        )
        self._lost = False
        self._lock = Lock()

    def mark_lost(self) -> None:
        with self._lock:
            self._lost = True

    def assert_current(self, now: datetime) -> None:
        with self._lock:
            if self._lost:
                raise ClaimLostError("dispatcher claim is no longer current")
        self.adapter.assert_current(self.envelope, owner=self.token.owner, now=now)

    def complete(self, now: datetime) -> None:
        self.assert_current(now)
        self.adapter.complete(self.envelope, owner=self.token.owner, now=now)
        self.mark_lost()
