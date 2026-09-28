from __future__ import annotations

import threading
import time

import pytest

from app.email_account_connector import (
    ConnectorPriority,
    EmailAccountConnector,
    EmailConnectorRegistry,
    EmailConnectorTimeout,
)


class FakeClock:
    """A controllable clock: advances only when told to."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeSession:
    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive
        self.closed = False
        self.noop_calls = 0

    def noop(self) -> tuple[str, list[bytes]]:
        self.noop_calls += 1
        if not self.alive:
            raise ConnectionError("dead session")
        return ("OK", [b""])

    def logout(self) -> tuple[str, list[bytes]]:
        self.closed = True
        return ("BYE", [b""])


def _connector(
    *,
    clock: FakeClock | None = None,
    connect_calls: list[int] | None = None,
    sessions: list[FakeSession] | None = None,
    idle_seconds: float = 45.0,
    max_age_seconds: float = 300.0,
    acquire_timeout: float = 600.0,
) -> EmailAccountConnector:
    clock = clock or FakeClock()
    connect_calls = connect_calls if connect_calls is not None else []
    sessions = sessions if sessions is not None else [FakeSession() for _ in range(20)]

    def connect_fn() -> FakeSession:
        connect_calls.append(1)
        return sessions[len(connect_calls) - 1]

    return EmailAccountConnector(
        "account-a",
        connect_fn,
        idle_seconds=idle_seconds,
        max_age_seconds=max_age_seconds,
        acquire_timeout=acquire_timeout,
        clock=clock,
    )


def test_two_threads_serialize_and_reuse_the_one_connection() -> None:
    connect_calls: list[int] = []
    connector = _connector(connect_calls=connect_calls)
    overlap = threading.Event()
    both_saw_only_one_holder = threading.Event()
    holders: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        with connector.acquire(kind="raw") as session:
            with lock:
                holders.append(1)
                if len(holders) > 1:
                    both_saw_only_one_holder.set()
            overlap.wait(timeout=1)
            with lock:
                holders.pop()
            assert session is not None

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(0.05)
    overlap.set()
    for thread in threads:
        thread.join(timeout=2)

    assert not both_saw_only_one_holder.is_set()
    assert len(connect_calls) == 1


def test_healthy_recent_session_is_reused_without_reconnecting() -> None:
    clock = FakeClock()
    connect_calls: list[int] = []
    connector = _connector(clock=clock, connect_calls=connect_calls)

    with connector.acquire(kind="raw") as first:
        pass
    clock.advance(5.0)
    with connector.acquire(kind="raw") as second:
        pass

    assert first is second
    assert len(connect_calls) == 1


def test_stale_or_dead_session_is_discarded_and_replaced() -> None:
    clock = FakeClock()
    connect_calls: list[int] = []
    sessions = [FakeSession(), FakeSession()]
    connector = _connector(
        clock=clock, connect_calls=connect_calls, sessions=sessions, idle_seconds=45.0
    )

    with connector.acquire(kind="raw") as first:
        pass
    clock.advance(46.0)  # past idle_seconds
    with connector.acquire(kind="raw") as second:
        pass

    assert first is sessions[0]
    assert second is sessions[1]
    assert first.closed is True
    assert len(connect_calls) == 2


def test_dead_probe_forces_a_reconnect_even_within_idle_window() -> None:
    clock = FakeClock()
    connect_calls: list[int] = []
    sessions = [FakeSession(alive=False), FakeSession()]
    connector = _connector(clock=clock, connect_calls=connect_calls, sessions=sessions)

    with connector.acquire(kind="raw") as first:
        pass
    # First session reports alive=False on NOOP; even one second later it must
    # be discarded and replaced, not reused.
    clock.advance(1.0)
    with connector.acquire(kind="raw") as second:
        pass

    assert second is sessions[1]
    assert len(connect_calls) == 2


def test_a_failure_inside_the_block_discards_the_session() -> None:
    connect_calls: list[int] = []
    sessions = [FakeSession(), FakeSession()]
    connector = _connector(connect_calls=connect_calls, sessions=sessions)

    with pytest.raises(RuntimeError):
        with connector.acquire(kind="raw") as session:
            raise RuntimeError("boom")

    assert session is sessions[0]
    assert session.closed is True

    with connector.acquire(kind="raw") as second:
        pass

    assert second is sessions[1]
    assert len(connect_calls) == 2


def test_wraps_readonly_and_deterministic_around_the_same_raw_session() -> None:
    from app.email_imap_readonly import ImapReadonlyAdapter
    from app.email_provider_actions import ImapDeterministicProvider

    class CapabilitySession(FakeSession):
        def capability(self) -> tuple[str, list[bytes]]:
            return ("OK", [b"IMAP4rev1 UIDPLUS MOVE"])

    sessions = [CapabilitySession()]
    connector = _connector(sessions=sessions, connect_calls=[])

    with connector.acquire(kind="readonly") as readonly:
        assert isinstance(readonly, ImapReadonlyAdapter)
        assert readonly.session is sessions[0]
        assert readonly.account_id == "account-a"

    with connector.acquire(
        kind="deterministic", move_mode="copy_as_move"
    ) as deterministic:
        assert isinstance(deterministic, ImapDeterministicProvider)
        assert deterministic.session is sessions[0]
        assert deterministic.move_mode == "copy_as_move"
        assert deterministic.account_id == "account-a"


def test_acquire_times_out_when_the_connection_never_frees_up() -> None:
    # A real (wall-clock) short timeout: Condition.wait's own timeout is
    # always real time, so this test uses the real clock (a frozen fake
    # clock would make `remaining` never reach zero) and keeps the window
    # small instead of simulating a long wait.
    connector = _connector(acquire_timeout=0.2, clock=time.monotonic)
    released = threading.Event()

    def hold_first() -> None:
        with connector.acquire(kind="raw"):
            released.wait(timeout=2)

    thread = threading.Thread(target=hold_first)
    thread.start()
    time.sleep(0.05)

    with pytest.raises(EmailConnectorTimeout) as excinfo:
        connector.acquire(kind="raw").__enter__()
    released.set()
    thread.join(timeout=2)

    assert excinfo.value.account_id == "account-a"
    assert excinfo.value.priority == ConnectorPriority.LOW
    assert excinfo.value.waited_seconds == 0.2


def test_higher_priority_waiter_goes_first_when_the_connection_frees() -> None:
    clock = FakeClock()
    connector = _connector(clock=clock)
    order: list[str] = []
    order_lock = threading.Lock()
    first_holder_release = threading.Event()
    both_queued = threading.Event()

    def hold_first() -> None:
        with connector.acquire(kind="raw", priority=ConnectorPriority.LOW):
            with order_lock:
                order.append("first-holder")
            both_queued.wait(timeout=2)
            first_holder_release.wait(timeout=2)

    def wait_low() -> None:
        with connector.acquire(kind="raw", priority=ConnectorPriority.LOW):
            with order_lock:
                order.append("low")

    def wait_high() -> None:
        with connector.acquire(kind="raw", priority=ConnectorPriority.HIGH):
            with order_lock:
                order.append("high")

    holder = threading.Thread(target=hold_first)
    holder.start()
    time.sleep(0.05)

    low = threading.Thread(target=wait_low)
    low.start()
    time.sleep(0.05)
    high = threading.Thread(target=wait_high)
    high.start()
    time.sleep(0.05)

    both_queued.set()
    first_holder_release.set()
    holder.join(timeout=2)
    low.join(timeout=2)
    high.join(timeout=2)

    assert order == ["first-holder", "high", "low"]


def test_registry_creates_one_connector_per_account_lazily() -> None:
    connect_log: list[str] = []

    def connect_fn_factory(account_id: str):
        def connect_fn() -> FakeSession:
            connect_log.append(account_id)
            return FakeSession()

        return connect_fn

    registry = EmailConnectorRegistry(connect_fn_factory)

    with registry.acquire("account-a", kind="raw"):
        pass
    with registry.acquire("account-b", kind="raw"):
        pass
    with registry.acquire("account-a", kind="raw"):
        pass

    assert connect_log == ["account-a", "account-b"]


def test_checkout_checkin_lets_a_caller_decide_reuse_per_outcome() -> None:
    connect_calls: list[int] = []
    sessions = [FakeSession(), FakeSession()]
    connector = _connector(connect_calls=connect_calls, sessions=sessions)

    session, wrapped = connector.checkout(kind="raw")
    assert wrapped is sessions[0]
    connector.checkin(session, keep=False)

    session2, wrapped2 = connector.checkout(kind="raw")
    connector.checkin(session2, keep=True)

    assert sessions[0].closed is True
    assert wrapped2 is sessions[1]
    assert sessions[1].closed is False
    assert len(connect_calls) == 2


def test_checkout_then_checkin_discard_then_checkout_again_never_holds_two_at_once() -> None:
    """Models the direct-action write-then-readback flow: the write session

    is fully checked in (discarded) before the read-back session is checked
    out -- the two are never open at the same time, so this must not need a
    reentrant lock.
    """

    connect_calls: list[int] = []
    sessions = [FakeSession(), FakeSession()]
    connector = _connector(connect_calls=connect_calls, sessions=sessions)

    write_session, _ = connector.checkout(kind="raw", priority=ConnectorPriority.HIGH)
    connector.checkin(write_session, keep=False)  # server reply was not enough
    readback_session, _ = connector.checkout(kind="raw", priority=ConnectorPriority.HIGH)
    connector.checkin(readback_session, keep=True)

    assert write_session is sessions[0]
    assert readback_session is sessions[1]
    assert len(connect_calls) == 2


def test_checkout_releases_the_slot_if_wrapping_fails() -> None:
    connector = _connector(connect_calls=[])

    with pytest.raises(ValueError):
        connector.checkout(kind="not-a-real-kind")

    # The failed checkout must not leave the connector permanently busy.
    session, _ = connector.checkout(kind="raw")
    connector.checkin(session, keep=True)
