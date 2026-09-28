"""One shared IMAP connection per email account, not one per subsystem.

Before this module, every subsystem that touched an account's mailbox
(periodic scan, training observation, direct-action delivery, historical
reconciliation, OTP reading, model-action repair, unsubscribe entry
resolution) dialed its own connection whenever it needed one. On
2026-09-28 a `py-spy dump` of the live worker caught two of those threads
holding two separate live sockets to the same Gmail account at the same
moment; the account's own provider throttled it in response, and the
resulting slow reads (already made resilient to malformed messages and a
20s-too-tight timeout by `email_imap_readonly.py`'s own fixes that same
day) still stalled every caller waiting behind them.

`EmailAccountConnector` owns the one raw IMAP session for one account
behind a real mutex: callers queue for it (by priority, then arrival
order) instead of opening their own. `EmailConnectorRegistry` holds one
connector per account, created lazily.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterator

DEFAULT_IDLE_SECONDS = 45.0
DEFAULT_MAX_AGE_SECONDS = 300.0
DEFAULT_ACQUIRE_TIMEOUT_SECONDS = 600.0


class ConnectorPriority(IntEnum):
    """Who gets the connection first when it frees up and more than one caller is waiting.

    LOW is background, bulk work the owner is not waiting on (training
    observation, historical reconciliation, model-action repair). HIGH is
    anything the owner is waiting on directly or that blocks other work
    from completing (periodic classification scan, direct mailbox action
    delivery, OTP reads, unsubscribe entry resolution).

    This orders who goes next once the connection is free; it cannot
    interrupt a read already in progress (Python cannot preempt a blocking
    socket call from another thread). A LOW-priority caller already holding
    the connection finishes its current IMAP command before anyone else's
    turn is considered -- bounded by the per-command socket timeout, not by
    priority.
    """

    LOW = 0
    HIGH = 1


class EmailConnectorTimeout(TimeoutError):
    """Raised when a caller waited `acquire_timeout` seconds and never got the connection."""

    def __init__(
        self, account_id: str, priority: ConnectorPriority, waited_seconds: float
    ) -> None:
        self.account_id = account_id
        self.priority = priority
        self.waited_seconds = waited_seconds
        super().__init__(
            f"timed out waiting {waited_seconds:.1f}s for the {account_id} email "
            f"connection ({priority.name} priority)"
        )


@dataclass(frozen=True, order=True)
class _Ticket:
    sort_key: tuple[int, int]
    seq: int

    @classmethod
    def new(cls, priority: ConnectorPriority, seq: int) -> "_Ticket":
        # Higher priority sorts first; within a priority, earlier arrival sorts first.
        return cls((-int(priority), seq), seq)


class EmailAccountConnector:
    """Serializes every use of one account's IMAP connection behind one mutex."""

    def __init__(
        self,
        account_id: str,
        connect_fn: Callable[[], Any],
        *,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        account_id = account_id.strip()
        if not account_id:
            raise ValueError("account_id must be non-empty")
        self.account_id = account_id
        self._connect_fn = connect_fn
        self.idle_seconds = idle_seconds
        self.max_age_seconds = max_age_seconds
        self.acquire_timeout = acquire_timeout
        self._clock = clock

        self._condition = threading.Condition()
        self._busy = False
        self._waiters: list[_Ticket] = []
        self._seq = itertools.count()

        self._session: Any | None = None
        self._kept_at: float | None = None
        self._chain_began: float | None = None

    def _wait_for_turn(self, priority: ConnectorPriority) -> None:
        deadline = self._clock() + self.acquire_timeout
        with self._condition:
            ticket = _Ticket.new(priority, next(self._seq))
            self._waiters.append(ticket)
            self._waiters.sort()
            try:
                while self._busy or self._waiters[0] != ticket:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise EmailConnectorTimeout(
                            self.account_id, priority, self.acquire_timeout
                        )
                    self._condition.wait(timeout=remaining)
                self._busy = True
                self._waiters.remove(ticket)
            except BaseException:
                if ticket in self._waiters:
                    self._waiters.remove(ticket)
                    self._condition.notify_all()
                raise

    def _release(self) -> None:
        with self._condition:
            self._busy = False
            self._condition.notify_all()

    def _probe_alive(self, session: Any) -> bool:
        try:
            status, _ = session.noop()
            return str(status).upper() == "OK"
        except Exception:  # noqa: BLE001 - any failure means the session is dead
            return False

    def _close(self, session: Any) -> None:
        close = getattr(session, "logout", None) or getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - closing a dead session must not raise
                pass

    def _take_or_connect(self) -> Any:
        now = self._clock()
        session = self._session
        if session is not None:
            fresh_enough = (
                self._kept_at is not None
                and now - self._kept_at <= self.idle_seconds
                and self._chain_began is not None
                and now - self._chain_began <= self.max_age_seconds
            )
            alive = fresh_enough and self._probe_alive(session)
            if not alive:
                self._close(session)
                session = None
                self._session = None
        if session is None:
            session = self._connect_fn()
            self._chain_began = now
        return session

    @contextmanager
    def acquire(
        self,
        kind: str,
        priority: ConnectorPriority = ConnectorPriority.LOW,
        **wrap_kwargs: Any,
    ) -> Iterator[Any]:
        """Block for this account's connection, then yield it wrapped as `kind`.

        `kind="raw"` yields the bare session (tests only). `kind="readonly"`
        wraps it in `ImapReadonlyAdapter`; `kind="deterministic"` wraps it in
        `ImapDeterministicProvider` (refreshing its capability list, since
        that is one cheap IMAP command and capabilities cannot go stale
        across a reused session's lifetime otherwise). Extra keyword
        arguments pass through to the wrapper's constructor.
        """

        self._wait_for_turn(priority)
        session: Any | None = None
        ok = False
        try:
            session = self._take_or_connect()
            wrapped = self._wrap(session, kind, **wrap_kwargs)
            yield wrapped
            ok = True
        finally:
            if ok:
                self._session = session
                self._kept_at = self._clock()
            elif session is not None:
                self._close(session)
                self._session = None
            self._release()

    def _wrap(self, session: Any, kind: str, **wrap_kwargs: Any) -> Any:
        if kind == "raw":
            return session
        if kind == "readonly":
            from app.email_imap_readonly import ImapReadonlyAdapter

            return ImapReadonlyAdapter(
                session, account_id=self.account_id, **wrap_kwargs
            )
        if kind == "deterministic":
            from app.email_provider_actions import ImapDeterministicProvider, _require_ok, _capability_tokens

            status, data = session.capability()
            _require_ok(status, "IMAP capability refresh failed", data)
            capabilities = _capability_tokens(data)
            return ImapDeterministicProvider(
                session,
                account_id=self.account_id,
                capabilities=capabilities,
                **wrap_kwargs,
            )
        raise ValueError(f"unsupported connector kind: {kind!r}")


class EmailConnectorRegistry:
    """One `EmailAccountConnector` per account, created the first time it is used."""

    def __init__(
        self,
        connect_fn_factory: Callable[[str], Callable[[], Any]],
        *,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect_fn_factory = connect_fn_factory
        self._idle_seconds = idle_seconds
        self._max_age_seconds = max_age_seconds
        self._acquire_timeout = acquire_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._connectors: dict[str, EmailAccountConnector] = {}

    def _connector_for(self, account_id: str) -> EmailAccountConnector:
        with self._lock:
            connector = self._connectors.get(account_id)
            if connector is None:
                connector = EmailAccountConnector(
                    account_id,
                    self._connect_fn_factory(account_id),
                    idle_seconds=self._idle_seconds,
                    max_age_seconds=self._max_age_seconds,
                    acquire_timeout=self._acquire_timeout,
                    clock=self._clock,
                )
                self._connectors[account_id] = connector
            return connector

    def acquire(
        self,
        account_id: str,
        kind: str,
        priority: ConnectorPriority = ConnectorPriority.LOW,
        **wrap_kwargs: Any,
    ):
        return self._connector_for(account_id).acquire(
            kind, priority, **wrap_kwargs
        )
