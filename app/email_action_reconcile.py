"""Reconcile terminal-failed email move/trash actions with what the mailbox shows.

A direct action can end `failed` after the provider had already carried it out:
Gmail, once it throttles, appends its own text after the COPYUID code, the
parser rejected that, and every move Gmail had completed was recorded failed
(fixed in `68b9fa00`). Those failures are terminal, so the fix does not clear
them, and retrying would move a message twice or misjudge it.

This looks at the mailbox READ-ONLY and records `done` only where the message is
verified in the folder the action asked for. Everything else stays as it is.
No STORE, MOVE, COPY, EXPUNGE or APPEND is ever issued: the session is wrapped
so that anything but LIST, EXAMINE, UID SEARCH and UID FETCH raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from app.email_classifier_contracts import EmailAction
from app.email_provider_actions import (
    ImapDeterministicProvider,
    ProviderMessageState,
    _changed_locator,
)
from app.email_store import EmailStore, FailedDirectAction

RECONCILED_OPERATION = "reconciled_readback"
RECONCILED_RESULT_PREFIX = "reconciled:"
RECONCILE_ACTIONS = (EmailAction.MOVE, EmailAction.TRASH)

VERIFIED = "verified"
IN_SOURCE = "in_source"
ELSEWHERE = "elsewhere"
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"
ERROR = "error"
OUTCOMES = (VERIFIED, IN_SOURCE, ELSEWHERE, NOT_FOUND, AMBIGUOUS, ERROR)

DEFAULT_LIMIT = 100
DEFAULT_PAUSE_SECONDS = 0.5
# Stop after this many failures in a row: the provider is refusing us, and
# hammering a throttling mailbox only lengthens the throttle.
MAX_CONSECUTIVE_ERRORS = 5
MAX_BACKOFF_SECONDS = 30.0
# One message (connect included) may take at most this long. The socket has its
# own read timeout, but a server that trickles bytes or a stalled TLS read once
# held a run for 30 minutes, so the wall clock is enforced here as well.
DEFAULT_MESSAGE_TIMEOUT_SECONDS = 60.0
PROGRESS_EVERY = 25

_READ_ONLY_UID_COMMANDS = frozenset({"FETCH", "SEARCH"})
_READ_ONLY_METHODS = frozenset(
    {"list", "response", "capability", "logout", "shutdown"}
)


class ReadOnlyViolation(RuntimeError):
    """Something tried to change the mailbox through a reconciliation session."""


class ReadOnlyImapSession:
    """An IMAP session that can look but cannot touch.

    Allowed: LIST, EXAMINE (SELECT with readonly), UID SEARCH, UID FETCH,
    CAPABILITY, response lookup and LOGOUT. Anything else raises before it is
    sent.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    def select(self, mailbox: str = "INBOX", readonly: bool = False):
        if readonly is not True:
            raise ReadOnlyViolation("reconciliation may only EXAMINE a mailbox")
        return self._session.select(mailbox, readonly=True)

    def uid(self, command: str, *args: object):
        if str(command).upper() not in _READ_ONLY_UID_COMMANDS:
            raise ReadOnlyViolation(f"IMAP UID {command} is not read-only")
        return self._session.uid(command, *args)

    def __getattr__(self, name: str) -> Any:
        if name in _READ_ONLY_METHODS or name == "capabilities":
            return getattr(self._session, name)
        raise ReadOnlyViolation(f"IMAP {name} is not read-only")


class ReconcileMailbox(Protocol):
    def destination_folder(
        self, action_type: EmailAction, parameters: Mapping[str, object]
    ) -> str: ...

    def read_state_at(self, locator: Any, *, action_type: EmailAction) -> Any: ...

    def find_states(
        self, locator: Any, *, action_type: EmailAction, folders: Any = None
    ) -> list[ProviderMessageState]: ...

    def close(self) -> None: ...


def read_only_provider(provider: ImapDeterministicProvider) -> ImapDeterministicProvider:
    """Wrap a connected provider's session so it can only read."""

    provider.session = ReadOnlyImapSession(provider.session)
    return provider


@dataclass
class ReconcileReport:
    apply: bool
    examined: int = 0
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    applied: int = 0
    changed_meanwhile: int = 0
    record_failed: int = 0
    aborted: bool = False
    interrupted: bool = False
    next_after: str = ""

    def count(self, action_type: EmailAction, outcome: str) -> None:
        by_outcome = self.counts.setdefault(action_type.value, {})
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

    def summary(self) -> str:
        parts = [
            f"mode={'apply' if self.apply else 'dry-run'}",
            f"examined={self.examined}",
        ]
        for action_type in (action.value for action in RECONCILE_ACTIONS):
            by_outcome = self.counts.get(action_type, {})
            parts.append(
                f"{action_type}["
                + " ".join(f"{name}={by_outcome.get(name, 0)}" for name in OUTCOMES)
                + "]"
            )
        parts.append(f"recorded_done={self.applied}")
        parts.append(f"changed_meanwhile={self.changed_meanwhile}")
        parts.append(f"record_failed={self.record_failed}")
        parts.append(f"aborted={str(self.aborted).lower()}")
        parts.append(f"interrupted={str(self.interrupted).lower()}")
        parts.append(f"next_after={self.next_after or '-'}")
        return " ".join(parts)


@dataclass(frozen=True)
class _Finding:
    outcome: str
    state: ProviderMessageState | None = None


def _examine(provider: ReconcileMailbox, failed: FailedDirectAction) -> _Finding:
    """Say where the message is now, using reads only.

    The source is checked first: a message that is still there was not moved,
    even if a copy also sits in the target.
    """

    action = failed.action
    locator = action.locator
    destination = provider.destination_folder(action.action_type, action.parameters)

    def satisfied(state: ProviderMessageState) -> bool:
        return state.satisfies(
            action.action_type, action.parameters, destination_folder=destination
        )

    at_source = provider.read_state_at(locator, action_type=action.action_type)
    if at_source is not None:
        return _Finding(VERIFIED if satisfied(at_source) else IN_SOURCE, at_source)
    if locator.rfc_message_id is None:
        # Without a Message-ID the only handle is the old UID, and it is gone.
        return _Finding(NOT_FOUND)
    in_target = provider.find_states(
        locator, action_type=action.action_type, folders={destination}
    )
    if len(in_target) == 1 and satisfied(in_target[0]):
        return _Finding(VERIFIED, in_target[0])
    if len(in_target) > 1:
        return _Finding(AMBIGUOUS)
    anywhere = provider.find_states(locator, action_type=action.action_type)
    if not anywhere:
        return _Finding(NOT_FOUND)
    if len(anywhere) > 1:
        return _Finding(AMBIGUOUS)
    return _Finding(ELSEWHERE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MessageTimeout(TimeoutError):
    """One message took longer than its wall-clock allowance."""


def _examine_with_deadline(
    provider: ReconcileMailbox | None,
    provider_factory: Callable[[], ReconcileMailbox],
    failed: FailedDirectAction,
    timeout_seconds: float,
) -> tuple[ReconcileMailbox | None, _Finding]:
    """Connect if needed and examine one message, never blocking past the deadline.

    The work runs on a daemon thread. If it overruns, the session is shut down
    from here (which wakes a blocked socket read) and the thread is abandoned;
    the caller treats that as an error and reconnects.
    """

    box: dict[str, Any] = {"provider": provider}

    def work() -> None:
        try:
            if box["provider"] is None:
                box["provider"] = provider_factory()
                if box.get("abandoned"):
                    _force_close(box["provider"])
                    return
            box["finding"] = _examine(box["provider"], failed)
        except BaseException as exc:  # handed to the caller, not swallowed
            box["error"] = exc

    thread = threading.Thread(target=work, name="email-reconcile-message", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        box["abandoned"] = True
        _force_close(box["provider"])
        raise MessageTimeout("message examination exceeded its deadline")
    if "error" in box:
        raise box["error"]
    return box["provider"], box["finding"]


def reconcile_email_actions(
    store: EmailStore,
    *,
    account_id: str,
    provider_factory: Callable[[], ReconcileMailbox],
    apply: bool = False,
    limit: int = DEFAULT_LIMIT,
    after_action_id: str = "",
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    message_timeout_seconds: float = DEFAULT_MESSAGE_TIMEOUT_SECONDS,
    progress: Callable[[ReconcileReport], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], str] = _utc_now,
) -> ReconcileReport:
    """Examine failed move/trash actions and, with `apply`, record verified ones.

    Bounded by `limit` (rows examined, in `action_id` order; pass the printed
    `next_after` to continue). Idempotent: a row recorded done is no longer
    failed, and rows left failed are left byte-for-byte as they were.
    `progress` is called with the running report every PROGRESS_EVERY rows.
    """

    report = ReconcileReport(apply=apply)
    failed_rows = store.list_failed_direct_actions(
        account_id=account_id,
        action_types=RECONCILE_ACTIONS,
        limit=limit,
        after_action_id=after_action_id,
    )
    provider: ReconcileMailbox | None = None
    consecutive_errors = 0
    backoff = max(pause_seconds, 1.0)
    try:
        for index, failed in enumerate(failed_rows):
            if index and pause_seconds > 0:
                sleep(pause_seconds)
            report.examined += 1
            report.next_after = failed.action.action_id
            action_type = failed.action.action_type
            try:
                provider, finding = _examine_with_deadline(
                    provider, provider_factory, failed, message_timeout_seconds
                )
            except ReadOnlyViolation:
                raise
            except Exception:
                # One message that times out or is throttled must not end the
                # run. The session may be half-dead, so drop it and reconnect
                # lazily.
                report.count(action_type, ERROR)
                consecutive_errors += 1
                _force_close(provider)
                provider = None
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.aborted = True
                    break
                sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            else:
                consecutive_errors = 0
                backoff = max(pause_seconds, 1.0)
                report.count(action_type, finding.outcome)
                if finding.outcome == VERIFIED and apply and finding.state is not None:
                    try:
                        if _record_done(store, failed, finding.state, now()):
                            report.applied += 1
                        else:
                            report.changed_meanwhile += 1
                    except Exception:
                        report.record_failed += 1
            if progress is not None and report.examined % PROGRESS_EVERY == 0:
                progress(report)
    except KeyboardInterrupt:
        report.interrupted = True
    finally:
        _force_close(provider)
    return report


def _record_done(
    store: EmailStore,
    failed: FailedDirectAction,
    verified: ProviderMessageState,
    finished_at: str,
) -> bool:
    action = failed.action
    claimed = store.claim_failed_direct_action_for_reconciliation(
        action_id=action.action_id,
        expected_attempt_count=action.attempt_number - 1,
        claimed_at=finished_at,
    )
    if claimed is None:
        return False
    try:
        store.complete_direct_action_attempt(
            claimed,
            status="done",
            provider_operation=RECONCILED_OPERATION,
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id=RECONCILED_RESULT_PREFIX + verified.revision,
            error="",
            finished_at=finished_at,
            updated_locator=_changed_locator(claimed.locator, verified.locator),
        )
    except Exception as exc:
        # Do not leave the claim `processing`: it would block the message's other
        # actions until a restart recovers it. Put the row back to failed.
        store.complete_direct_action_attempt(
            claimed,
            status="failed",
            provider_operation="reconcile_record",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id="",
            error=f"reconcile_record_failed:{type(exc).__name__}",
            finished_at=finished_at,
            retryable=False,
        )
        raise
    return True


def _force_close(provider: object) -> None:
    """Drop a session without waiting for the server (LOGOUT may hang too)."""

    session = getattr(provider, "session", None)
    shutdown = getattr(session, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception:
            pass
    close = getattr(provider, "close", None)
    if callable(close) and not callable(shutdown):
        try:
            close()
        except Exception:
            pass


def build_gmail_read_only_provider_factory(
    store: EmailStore,
    account_id: str,
    environment: Mapping[str, str],
) -> Callable[[], ReconcileMailbox]:
    """Connect to the account's IMAP server with a session that cannot write."""

    from app.email_connector_config import resolve_secret

    account = store.get_account(account_id)
    if not isinstance(account, Mapping) or str(account.get("account_id")) != account_id:
        raise LookupError("email IMAP account is unavailable")
    if not bool(account.get("imap_tls")):
        raise ConnectionError("email IMAP TLS is required")
    secret = resolve_secret(str(account.get("imap_secret_reference") or ""), environment)
    if not secret:
        raise ConnectionError("email IMAP credential is unavailable")

    def connect() -> ReconcileMailbox:
        return read_only_provider(
            ImapDeterministicProvider.connect(
                str(account["imap_host"]),
                str(account["imap_username"]),
                secret,
                port=int(account["imap_port"]),
                account_id=account_id,
                move_mode=str(account.get("imap_move_mode") or "move"),
            )
        )

    return connect
