"""Ask for access to the DingTalk AI minutes this account may not read.

The read API lists only minutes we already have access to, so a minute nobody
shared with us is invisible there. The 听记 admin console lists all of them,
which makes it the only place a missing minute can be discovered, and the
minute's own page is the only place a request can actually be sent: the
`dws minutes +apply-permission` API answers `requested: true` for a request its
owner never receives (85 minutes were "requested" through it on 2026-09-18 and
every one of their pages still offered `Send Application` afterwards).

So this pass reads the console for candidates, asks the provider which of them
we may not read, and sends a request on the page, counting it only when the
page reads the request back.  Every step is a fixed rule, and the browser it
drives is the service's shared headless Chrome, which carries the daily copy
of Derek's own Chrome cookies -- so this pass no longer keeps a console session
of its own, and there is nothing here that expires or has to be renewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Protocol

from app.dws_client import DwsError
from app.minutes_sync import _is_restricted_minute_error


MINUTES_ACCESS_SCANNER = "ai_minutes_access"

MINUTES_CONSOLE_HOST = "shanji-admin.dingtalk.com"

# The reason the minute's owner reads. It says what is asking and invites a
# refusal, because the request reaches a colleague, not a system.
ACCESS_REQUEST_REASON = (
    "AI自动抓取，用于会议纪要整理，如和工作内容无关或者涉及个人隐私，请拒绝"
)

class MinutesBrowserSessionExpired(RuntimeError):
    """The service's headless Chrome could not reach the 听记 admin console.

    It carries the daily copy of Derek's own Chrome cookies, so the remedy is
    to give that copy a DingTalk login again -- open DingTalk in Chrome, and
    let the daily `sync-chrome-cookies` pass run -- not to sign in anywhere on
    the console's behalf.
    """


class MinutesConsoleUnavailable(RuntimeError):
    """The session signed in, but the console served no minutes listing.

    Distinct from an expired session on purpose. The console is the 听记
    organisation's management view, so an account without that role reaches it
    and gets no table -- and no amount of signing in again will change that.
    Reporting it as an expired session would ask a person, every day, to renew
    a session that is already valid.
    """


@dataclass(frozen=True)
class MinutesAccessResult:
    """Every candidate lands in exactly one outcome."""

    discovered: int = 0
    requested: int = 0
    already_requested: int = 0
    readable: int = 0
    unresolved: int = 0
    failed: int = 0

    def __post_init__(self) -> None:
        accounted = (
            self.requested
            + self.already_requested
            + self.readable
            + self.unresolved
            + self.failed
        )
        if accounted != self.discovered:
            raise ValueError("minutes access outcomes do not account for every item")

    def summary(self) -> str:
        return (
            f"discovered={self.discovered} requested={self.requested} "
            f"already_requested={self.already_requested} readable={self.readable} "
            f"unresolved={self.unresolved} failed={self.failed}"
        )


@dataclass(frozen=True)
class MinutesAccessRequest:
    """What the minute's own page said after the request was sent."""

    outcome: str
    title: str = ""
    owner: str = ""
    detail: str = ""


class MinutesConsole(Protocol):
    """The 听记 admin console and minute pages, behind a signed-in browser."""

    def list_backend_minutes(self) -> list[dict[str, Any]]:
        """Every minute the console lists, newest first."""

    def request_access(self, task_uuid: str, *, reason: str) -> MinutesAccessRequest:
        """Send one request on the minute's page and read the result back."""


def _task_uuid(row: dict[str, Any]) -> str:
    return str(row.get("row_key") or row.get("taskUuid") or "").strip()


def _is_cleaned(row: dict[str, Any]) -> bool:
    """A cleaned minute has no content left, so asking for it asks for nothing."""
    return str(row.get("size") or "").strip() == "-"


def _readable(dws, task_uuid: str) -> bool | None:
    """True when the provider serves this minute, False when it refuses it.

    None is any other failure: the pass must not read a transport error as a
    permission decision.
    """
    try:
        dws.get_minutes_info(task_uuid)
    except DwsError as exc:
        return False if _is_restricted_minute_error(exc) else None
    return True


def request_minutes_access(
    store,
    dws,
    console: MinutesConsole,
    *,
    now: datetime | None = None,
) -> MinutesAccessResult:
    """Request access to every console-listed minute this account cannot read."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = store.get_daily_scan_state(MINUTES_ACCESS_SCANNER) or {}
    try:
        cursor = json.loads(state.get("cursor_json") or "{}")
    except json.JSONDecodeError:
        cursor = {}
    requested_ids = {str(value) for value in (cursor.get("requested_ids") or [])}
    readable_ids = {str(value) for value in (cursor.get("readable_ids") or [])}

    rows = console.list_backend_minutes()
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        task_uuid = _task_uuid(row)
        if not task_uuid or task_uuid in seen:
            continue
        seen.add(task_uuid)
        # Asking again for a minute already asked for would notify its owner a
        # second time, and a cleaned minute has nothing left to grant.
        if task_uuid in requested_ids or task_uuid in readable_ids:
            continue
        if _is_cleaned(row):
            continue
        candidates.append(row)

    counts = {"requested": 0, "already_requested": 0, "readable": 0,
              "unresolved": 0, "failed": 0}
    last_error = ""
    for row in candidates:
        task_uuid = _task_uuid(row)
        readable = _readable(dws, task_uuid)
        if readable is None:
            counts["failed"] += 1
            continue
        if readable:
            counts["readable"] += 1
            readable_ids.add(task_uuid)
            continue
        try:
            result = console.request_access(task_uuid, reason=ACCESS_REQUEST_REASON)
        except MinutesBrowserSessionExpired:
            raise
        except Exception as exc:  # the console is a browser; it fails in many ways
            counts["failed"] += 1
            last_error = str(exc)[:200]
            continue
        if result.outcome in ("requested", "already_requested"):
            counts[result.outcome] += 1
            requested_ids.add(task_uuid)
        elif result.outcome == "readable":
            counts["readable"] += 1
            readable_ids.add(task_uuid)
        elif result.outcome == "unresolved":
            # The console could not resolve an owner to ask. Left out of the
            # requested set so a later pass tries again.
            counts["unresolved"] += 1
        else:
            counts["failed"] += 1
            last_error = result.detail[:200] or result.outcome

    store.set_daily_scan_state(
        MINUTES_ACCESS_SCANNER,
        last_success_at=moment.isoformat(),
        cursor_json=json.dumps(
            {
                "requested_ids": sorted(requested_ids),
                "readable_ids": sorted(readable_ids),
            },
            sort_keys=True,
        ),
        last_error=last_error,
    )
    return MinutesAccessResult(discovered=len(candidates), **counts)
