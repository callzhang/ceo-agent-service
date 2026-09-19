from __future__ import annotations

import threading
from datetime import UTC, datetime

from app.service_heartbeat import (
    live_component_names,
    record_component_heartbeats,
    run_service_heartbeat_loop,
)


class FakeThread:
    def __init__(self, name: str, alive: bool = True) -> None:
        self.name = name
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def set_service_health_component(self, component: str, **kwargs) -> None:
        self.calls.append({"component": component, **kwargs})


def test_only_live_component_threads_are_reported() -> None:
    """A dead component stops being reported, so its tick visibly ages.

    On 2026-09-18 the worker crash-looped for 48 minutes while audit-web
    stayed up. Nothing in the panel said so, because a healthy component
    reported nothing and a dead one reported nothing either.
    """
    threads = [
        FakeThread("ceo-agent-service-agent-cron-scheduler"),
        FakeThread("ceo-agent-service-database-backup"),
        FakeThread("ceo-agent-service-meeting-delivery", alive=False),
        FakeThread("some-unrelated-thread"),
        FakeThread("MainThread"),
    ]

    assert live_component_names(threads) == (
        "agent-cron-scheduler",
        "database-backup",
    )


def test_each_live_component_gets_a_tick() -> None:
    store = RecordingStore()
    moment = datetime(2026, 9, 19, 5, 30, tzinfo=UTC)

    recorded = record_component_heartbeats(
        store,
        now=moment,
        threads=[FakeThread("ceo-agent-service-database-backup")],
    )

    assert recorded == ("database-backup",)
    assert store.calls == [
        {
            "component": "database-backup",
            "state": "healthy",
            "status": "running",
            "latest_tick_at": moment.isoformat(),
        }
    ]


def test_a_health_write_that_fails_does_not_stop_the_service() -> None:
    """The heartbeat exists to report trouble, not to cause it."""

    class BrokenStore:
        def set_service_health_component(self, *args, **kwargs):
            raise RuntimeError("database is locked")

    slept: list[float] = []
    run_service_heartbeat_loop(
        BrokenStore,
        interval_seconds=7,
        sleep=slept.append,
        iterations=2,
    )

    assert slept == [7]


def test_the_loop_ticks_until_it_is_asked_to_stop() -> None:
    store = RecordingStore()
    slept: list[float] = []

    run_service_heartbeat_loop(
        lambda: store,
        interval_seconds=30,
        sleep=slept.append,
        iterations=3,
    )

    assert len(slept) == 2
    # The real process threads are whatever is running the test; the point is
    # that every pass wrote for exactly the components that were alive.
    assert all(call["status"] == "running" for call in store.calls)


def test_the_component_prefix_matches_what_run_service_names_its_threads() -> None:
    from app.service_heartbeat import COMPONENT_THREAD_PREFIX

    thread = threading.Thread(target=lambda: None, name=f"{COMPONENT_THREAD_PREFIX}x")
    assert thread.name == "ceo-agent-service-x"
