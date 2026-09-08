from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Executor
from datetime import UTC, datetime, timedelta
from threading import Event

from app.dispatcher.models import DispatchEnvelope, QueueAdapter


class ConsumerDispatcher:
    """Fairly claims source facts and hands references to isolated worker pools."""

    def __init__(
        self,
        *,
        adapters: Sequence[QueueAdapter],
        consumers: Mapping[str, Callable[[DispatchEnvelope], object]],
        executors: Mapping[str, Executor],
        owner: str,
        lease: timedelta,
        wake_event: Event | None = None,
        fallback_wait_seconds: float = 5.0,
    ) -> None:
        if not owner.strip():
            raise ValueError("dispatcher owner must not be empty")
        if lease.total_seconds() <= 0:
            raise ValueError("dispatcher lease must be positive")
        if fallback_wait_seconds <= 0:
            raise ValueError("dispatcher fallback wait must be positive")
        names = tuple(adapter.name for adapter in adapters)
        if len(set(names)) != len(names):
            raise ValueError("dispatcher adapter names must be unique")
        missing = set(names) - set(consumers) | (set(names) - set(executors))
        if missing:
            raise ValueError(
                "dispatcher adapters require consumers and executors: "
                + ", ".join(sorted(missing))
            )
        self.adapters = tuple(adapters)
        self.consumers = dict(consumers)
        self.executors = dict(executors)
        self.owner = owner
        self.lease = lease
        self.wake_event = wake_event or Event()
        self.fallback_wait_seconds = fallback_wait_seconds
        self._next_adapter = 0

    def dispatch_available(self, now: datetime, *, limit: int) -> int:
        if limit <= 0 or not self.adapters:
            return 0
        submitted = 0
        attempted = 0
        empty_in_row = 0
        while attempted < limit and empty_in_row < len(self.adapters):
            index = self._next_adapter
            adapter = self.adapters[index]
            self._next_adapter = (index + 1) % len(self.adapters)
            envelope = adapter.claim(now, owner=self.owner, lease=self.lease)
            if envelope is None:
                empty_in_row += 1
                continue
            attempted += 1
            empty_in_row = 0
            try:
                self.executors[adapter.name].submit(
                    self.consumers[adapter.name], envelope
                )
            except RuntimeError:
                adapter.release(
                    envelope,
                    owner=self.owner,
                    now=now,
                )
                continue
            submitted += 1
        return submitted

    def run(self, *, stop_event: Event, dispatch_limit: int = 100) -> None:
        while not stop_event.is_set():
            submitted = self.dispatch_available(datetime.now(UTC), limit=dispatch_limit)
            if submitted:
                continue
            self.wake_event.clear()
            if stop_event.is_set():
                break
            self.wake_event.wait(timeout=self.fallback_wait_seconds)
