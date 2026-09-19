"""Report that the service components are alive, not only that they failed.

Every component reported its health only when something went wrong. A
component that was running normally wrote nothing at all, so the console
could not tell "running fine" from "never started" -- both read `unknown`
with no tick. On 2026-09-18 the worker crash-looped for 48 minutes while
audit-web stayed up, and nothing in the panel said so: `healthz` was ok,
Attention was empty, and the queues looked idle because nothing was
running to fill them.

The components are threads with known names, so one observer can watch
them all. Each pass records a tick for every live component thread; a
thread that has died simply stops being reported, and its tick ages
visibly instead of staying absent forever.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

#: Thread name prefix given to every service component in `run_service`.
COMPONENT_THREAD_PREFIX = "ceo-agent-service-"
HEARTBEAT_INTERVAL_SECONDS = 60


def live_component_names(
    threads: Iterable[threading.Thread] | None = None,
) -> tuple[str, ...]:
    """The components whose thread is alive right now, in a stable order."""

    names = {
        thread.name[len(COMPONENT_THREAD_PREFIX) :]
        for thread in (threads if threads is not None else threading.enumerate())
        if thread.name.startswith(COMPONENT_THREAD_PREFIX) and thread.is_alive()
    }
    return tuple(sorted(name for name in names if name))


def record_component_heartbeats(
    store,
    *,
    now: datetime | None = None,
    threads: Iterable[threading.Thread] | None = None,
) -> tuple[str, ...]:
    """Record one tick for every live component. Returns what was recorded."""

    moment = (now or datetime.now(UTC)).isoformat()
    recorded = live_component_names(threads)
    for component in recorded:
        store.set_service_health_component(
            component,
            state="healthy",
            status="running",
            latest_tick_at=moment,
        )
    return recorded


def run_service_heartbeat_loop(
    store_factory: Callable[[], object],
    *,
    interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
    iterations: int | None = None,
) -> None:
    """Tick for every live component until the process ends.

    A failure to write health is never a reason to take the service down:
    the heartbeat exists to report trouble, not to cause it.
    """

    clock = now or (lambda: datetime.now(UTC))
    remaining = iterations
    while remaining is None or remaining > 0:
        try:
            record_component_heartbeats(store_factory(), now=clock())
        except Exception:  # noqa: BLE001 - the observer must not kill the service
            pass
        if remaining is not None:
            remaining -= 1
            if remaining <= 0:
                return
        sleep(interval_seconds)
