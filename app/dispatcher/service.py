from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import logging
import os
from threading import Event, Lock
from time import monotonic

from app.dispatcher.models import ClaimGuard, DispatchEnvelope, QueueAdapter


DEFAULT_DISPATCHER_DRAIN_GRACE_SECONDS = 21 * 60
LOGGER = logging.getLogger(__name__)


class DispatcherDrainTimeout(RuntimeError):
    """The dispatcher could not drain its admitted work before shutdown."""


class AdapterWorkerPools:
    """Own one bounded worker pool for each independently dispatched queue."""

    def __init__(
        self,
        worker_counts: Mapping[str, int],
        *,
        thread_name_prefix: str = "agent-dispatcher",
    ) -> None:
        names = tuple(worker_counts)
        if not names or any(not name.strip() for name in names):
            raise ValueError("dispatcher adapter names must not be empty")
        if len(set(names)) != len(names):
            raise ValueError("dispatcher adapter names must be unique")
        if any(worker_counts[name] <= 0 for name in names):
            raise ValueError("dispatcher worker capacity must be positive")
        self.executors: dict[str, ThreadPoolExecutor] = {
            name: ThreadPoolExecutor(
                max_workers=worker_counts[name],
                thread_name_prefix=f"{thread_name_prefix}-{name}",
            )
            for name in names
        }
        self.max_in_flight = dict(worker_counts)

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        for executor in self.executors.values():
            executor.shutdown(wait=wait, cancel_futures=cancel_futures)


class ConsumerDispatcher:
    """Fairly claims source facts and hands references to isolated worker pools."""

    def __init__(
        self,
        *,
        adapters: Sequence[QueueAdapter],
        consumers: Mapping[str, Callable[[DispatchEnvelope, ClaimGuard], object]],
        executors: Mapping[str, Executor],
        max_in_flight: Mapping[str, int],
        shared_capacity_adapters: frozenset[str] = frozenset(),
        shared_max_in_flight: int | None = None,
        owner: str,
        owner_pid: int | None = None,
        lease: timedelta,
        wake_event: Event | None = None,
        fallback_wait_seconds: float = 5.0,
        tick_observer: Callable[[datetime], None] | None = None,
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
        missing = (
            set(names) - set(consumers)
            | (set(names) - set(executors))
            | (set(names) - set(max_in_flight))
        )
        if missing:
            raise ValueError(
                "dispatcher adapters require consumers and executors: "
                + ", ".join(sorted(missing))
            )
        self.adapters = tuple(adapters)
        self.consumers = dict(consumers)
        self.executors = dict(executors)
        self.max_in_flight = dict(max_in_flight)
        if any(self.max_in_flight[name] <= 0 for name in names):
            raise ValueError("dispatcher worker capacity must be positive")
        if not shared_capacity_adapters:
            if shared_max_in_flight is not None:
                raise ValueError(
                    "shared dispatcher capacity requires adapter names"
                )
        elif shared_max_in_flight is None or shared_max_in_flight <= 0:
            raise ValueError("shared dispatcher capacity must be positive")
        unknown_shared = shared_capacity_adapters - set(names)
        if unknown_shared:
            raise ValueError(
                "shared dispatcher capacity has unknown adapters: "
                + ", ".join(sorted(unknown_shared))
            )
        self.shared_capacity_adapters = shared_capacity_adapters
        self.shared_max_in_flight = shared_max_in_flight
        self._shared_in_flight = 0
        self._in_flight = {name: 0 for name in names}
        self._active: dict[
            str,
            dict[
                Future[object],
                tuple[DispatchEnvelope, ClaimGuard, datetime | None],
            ],
        ] = {name: {} for name in names}
        self._adapter_by_name = {adapter.name: adapter for adapter in adapters}
        self._capacity_lock = Lock()
        self._completion_event = Event()
        self.owner = owner
        self.owner_pid = os.getpid() if owner_pid is None else owner_pid
        self.lease = lease
        self.wake_event = wake_event or Event()
        self.fallback_wait_seconds = fallback_wait_seconds
        self.tick_observer = tick_observer
        self._next_adapter = 0

    def dispatch_available(self, now: datetime, *, limit: int) -> int:
        self.renew_in_flight(now)
        if limit <= 0 or not self.adapters:
            return 0
        submitted = 0
        attempted = 0
        empty_in_row = 0
        while attempted < limit and empty_in_row < len(self.adapters):
            index = self._next_adapter
            adapter = self.adapters[index]
            self._next_adapter = (index + 1) % len(self.adapters)
            if not self._reserve_capacity(adapter.name):
                empty_in_row += 1
                continue
            envelope = adapter.claim(
                now,
                owner=self.owner,
                owner_pid=self.owner_pid,
                lease=self.lease,
            )
            if envelope is None:
                self._release_capacity(adapter.name)
                empty_in_row += 1
                continue
            attempted += 1
            empty_in_row = 0
            guard = ClaimGuard(adapter=adapter, envelope=envelope, owner=self.owner)
            try:
                future = self.executors[adapter.name].submit(
                    self.consumers[adapter.name], envelope, guard
                )
            except RuntimeError:
                adapter.release(
                    envelope,
                    owner=self.owner,
                    now=now,
                )
                self._release_capacity(adapter.name)
                continue
            with self._capacity_lock:
                self._active[adapter.name][future] = (
                    envelope,
                    guard,
                    now + self.lease / 2,
                )
            future.add_done_callback(
                lambda completed, name=adapter.name: self._complete_future(
                    name, completed
                )
            )
            submitted += 1
        return submitted

    def renew_in_flight(self, now: datetime) -> None:
        due: list[tuple[str, Future[object], DispatchEnvelope, ClaimGuard]] = []
        with self._capacity_lock:
            for name, active in self._active.items():
                due.extend(
                    (name, future, envelope, guard)
                    for future, (envelope, guard, renew_at) in active.items()
                    if not future.done() and renew_at is not None and renew_at <= now
                )
        for name, future, envelope, guard in due:
            try:
                self._adapter_by_name[name].renew(
                    envelope,
                    owner=self.owner,
                    now=now,
                    lease=self.lease,
                )
            except Exception as exc:  # noqa: BLE001 - isolate one lost source lease
                self._adapter_by_name[name].record_lease_error(
                    envelope,
                    owner=self.owner,
                    error=str(exc),
                    now=now,
                )
                guard.mark_lost()
                with self._capacity_lock:
                    current = self._active[name].get(future)
                    if current is not None and current[0] == envelope:
                        self._active[name][future] = (envelope, guard, None)
                continue
            with self._capacity_lock:
                current = self._active[name].get(future)
                if current is not None and current[0] == envelope:
                    self._active[name][future] = (
                        envelope,
                        guard,
                        now + self.lease / 2,
                    )

    def _reserve_capacity(self, adapter_name: str) -> bool:
        with self._capacity_lock:
            if self._in_flight[adapter_name] >= self.max_in_flight[adapter_name]:
                return False
            if (
                adapter_name in self.shared_capacity_adapters
                and self.shared_max_in_flight is not None
                and self._shared_in_flight >= self.shared_max_in_flight
            ):
                return False
            self._in_flight[adapter_name] += 1
            if adapter_name in self.shared_capacity_adapters:
                self._shared_in_flight += 1
            return True

    def _release_capacity(self, adapter_name: str) -> None:
        with self._capacity_lock:
            self._in_flight[adapter_name] -= 1
            if adapter_name in self.shared_capacity_adapters:
                self._shared_in_flight -= 1

    def _complete_future(
        self,
        adapter_name: str,
        future: Future[object],
    ) -> None:
        with self._capacity_lock:
            removed = self._active[adapter_name].pop(future, None)
            if removed is None:
                return
            self._in_flight[adapter_name] -= 1
            if adapter_name in self.shared_capacity_adapters:
                self._shared_in_flight -= 1
        _envelope, guard, _renew_at = removed
        now = datetime.now(UTC)
        if future.cancelled():
            try:
                guard.release(now)
            except Exception as exc:  # noqa: BLE001 - preserve lease error evidence
                guard.adapter.record_lease_error(
                    guard.envelope,
                    owner=guard.token.owner,
                    error=str(exc),
                    now=now,
                )
                guard.mark_lost()
            self._signal_future_completion()
            return
        if guard.resolved:
            self._signal_future_completion()
            return
        future_error = future.exception()
        if future_error is None:
            try:
                guard.complete(now)
            except Exception as exc:  # noqa: BLE001 - completion fencing is isolated
                guard.adapter.record_lease_error(
                    guard.envelope,
                    owner=guard.token.owner,
                    error=str(exc),
                    now=now,
                )
                guard.mark_lost()
        else:
            guard.adapter.record_lease_error(
                guard.envelope,
                owner=guard.token.owner,
                error=str(future_error),
                now=now,
            )
            guard.mark_lost()
        self._signal_future_completion()

    def _signal_future_completion(self) -> None:
        self.wake_event.set()
        self._completion_event.set()

    def _active_count(self) -> int:
        with self._capacity_lock:
            return sum(len(active) for active in self._active.values())

    def _cancel_pending_futures(self) -> None:
        with self._capacity_lock:
            futures = tuple(
                future
                for active in self._active.values()
                for future in active
            )
        for future in futures:
            future.cancel()

    def drain(
        self,
        *,
        timeout_seconds: float = DEFAULT_DISPATCHER_DRAIN_GRACE_SECONDS,
    ) -> None:
        """Renew admitted claims until completion or fail within a fixed grace."""
        if timeout_seconds <= 0:
            raise ValueError("dispatcher drain timeout must be positive")
        deadline = monotonic() + timeout_seconds
        renew_wait = self.lease.total_seconds() / 2
        while True:
            self.renew_in_flight(datetime.now(UTC))
            if self._active_count() == 0:
                return
            remaining = deadline - monotonic()
            if remaining <= 0:
                self._cancel_pending_futures()
                active = self._active_count()
                raise DispatcherDrainTimeout(
                    "timed out draining dispatcher work; "
                    f"{active} running future(s) remain"
                )
            self._completion_event.clear()
            if self._active_count() == 0:
                return
            self._completion_event.wait(timeout=min(remaining, renew_wait))

    def run(self, *, stop_event: Event, dispatch_limit: int = 100) -> None:
        while not stop_event.is_set():
            self.wake_event.clear()
            if self.tick_observer is not None:
                tick_at = datetime.now(UTC)
                try:
                    self.tick_observer(tick_at)
                except Exception:  # noqa: BLE001 - health reporting must not stop dispatch
                    LOGGER.exception("dispatcher_tick_observer_failed")
            submitted = self.dispatch_available(datetime.now(UTC), limit=dispatch_limit)
            if submitted:
                continue
            if stop_event.is_set():
                break
            self.wake_event.wait(
                timeout=min(
                    self.fallback_wait_seconds,
                    self.lease.total_seconds() / 2,
                )
            )
