"""Shared local mutation budget for Feishu outbound operations."""
from __future__ import annotations

from collections import deque
from threading import Lock
from time import monotonic
from typing import Callable


class SlidingWindowMutationBudget:
    """Thread-safe sliding window with an optional durable admission gate."""

    def __init__(
        self,
        max_mutations_per_minute: int,
        *,
        monotonic_clock: Callable[[], float] = monotonic,
        durable_acquire: Callable[[], bool] | None = None,
    ) -> None:
        if max_mutations_per_minute <= 0:
            raise ValueError("Feishu mutation budget must be positive")
        self.max_mutations_per_minute = max_mutations_per_minute
        self.monotonic_clock = monotonic_clock
        self.durable_acquire = durable_acquire
        self._mutation_times: deque[float] = deque()
        self._lock = Lock()

    def try_acquire(self) -> bool:
        """Consume one mutation slot, or return ``False`` without blocking."""
        current = self.monotonic_clock()
        with self._lock:
            while (
                self._mutation_times
                and current - self._mutation_times[0] >= 60
            ):
                self._mutation_times.popleft()
            if len(self._mutation_times) >= self.max_mutations_per_minute:
                return False
            if self.durable_acquire is not None and not self.durable_acquire():
                return False
            self._mutation_times.append(current)
            return True
