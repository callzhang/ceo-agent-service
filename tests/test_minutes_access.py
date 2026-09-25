from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.dws_client import DwsError
from app.minutes_access import (
    ACCESS_REQUEST_REASON,
    MINUTES_ACCESS_SCANNER,
    MinutesAccessRequest,
    MinutesAccessResult,
    MinutesBrowserSessionExpired,
    request_minutes_access,
)
from app.store import AutoReplyStore


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


class FakeConsole:
    def __init__(self, rows, outcomes=None):
        self._rows = rows
        self._outcomes = outcomes or {}
        self.requested: list[tuple[str, str]] = []

    def list_backend_minutes(self):
        return self._rows

    def request_access(self, task_uuid, *, reason):
        self.requested.append((task_uuid, reason))
        outcome = self._outcomes.get(task_uuid, "requested")
        if isinstance(outcome, Exception):
            raise outcome
        return MinutesAccessRequest(outcome, title=f"会议{task_uuid}", owner="同事")


class FakeDws:
    """Only `get_minutes_info`, which is all the access pass asks the provider."""

    def __init__(self, readable=(), errors=None):
        self._readable = set(readable)
        self._errors = errors or {}

    def get_minutes_info(self, task_uuid):
        if task_uuid in self._readable:
            return {"result": {"title": "可读"}}
        error = self._errors.get(task_uuid)
        if error is not None:
            raise error
        raise _restricted()


def _restricted(message: str = "no permission") -> DwsError:
    return DwsError(
        "dws minutes get info failed",
        "1",
        business_message=message,
        server_key="minutes",
    )


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "access.sqlite3")


def _cursor(store: AutoReplyStore) -> dict:
    state = store.get_daily_scan_state(MINUTES_ACCESS_SCANNER) or {}
    return json.loads(state.get("cursor_json") or "{}")
def _rows(*keys, size="12.0 MB - Meeting"):
    return [{"row_key": key, "size": size, "initiator": "同事"} for key in keys]


def test_only_the_minutes_the_provider_refuses_are_asked_for(tmp_path: Path) -> None:
    store = _store(tmp_path)
    console = FakeConsole(_rows("readable", "restricted"))
    dws = FakeDws(readable={"readable"})

    result = request_minutes_access(
        store, dws, console, now=NOW
    )

    assert [uuid for uuid, _ in console.requested] == ["restricted"]
    assert console.requested[0][1] == ACCESS_REQUEST_REASON
    assert result.requested == 1 and result.readable == 1 and result.discovered == 2


def test_a_minute_already_asked_for_is_never_asked_again(tmp_path: Path) -> None:
    """A second request notifies the same colleague a second time."""
    store = _store(tmp_path)
    console = FakeConsole(_rows("restricted"))
    dws = FakeDws()

    request_minutes_access(store, dws, console, now=NOW)
    second = request_minutes_access(
        store, dws, console, now=NOW
    )

    assert len(console.requested) == 1
    assert second.discovered == 0
    assert _cursor(store)["requested_ids"] == ["restricted"]


def test_a_cleaned_minute_is_never_asked_for(tmp_path: Path) -> None:
    """Its content is gone, so approving it would grant access to nothing."""
    store = _store(tmp_path)
    console = FakeConsole(_rows("cleaned", size="-"))
    dws = FakeDws()

    result = request_minutes_access(
        store, dws, console, now=NOW
    )

    assert console.requested == []
    assert result.discovered == 0


def test_a_page_that_never_read_the_request_back_is_a_failure(tmp_path: Path) -> None:
    """The click is not the evidence; the page reading it back is."""
    store = _store(tmp_path)
    console = FakeConsole(_rows("restricted"), outcomes={"restricted": "failed"})
    dws = FakeDws()

    result = request_minutes_access(
        store, dws, console, now=NOW
    )

    assert result.failed == 1 and result.requested == 0
    assert _cursor(store)["requested_ids"] == []


def test_an_unresolved_owner_is_retried_rather_than_recorded_as_asked(
    tmp_path: Path,
) -> None:
    """The console resolves the owner late; a later pass finds it resolved."""
    store = _store(tmp_path)
    console = FakeConsole(_rows("slow"), outcomes={"slow": "unresolved"})
    dws = FakeDws()

    first = request_minutes_access(
        store, dws, console, now=NOW
    )
    console._outcomes = {}
    second = request_minutes_access(
        store, dws, console, now=NOW
    )

    assert first.unresolved == 1
    assert second.requested == 1
    assert _cursor(store)["requested_ids"] == ["slow"]


def test_a_transport_failure_is_not_read_as_a_permission_decision(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    console = FakeConsole(_rows("flaky"))
    dws = FakeDws(errors={"flaky": DwsError("mcp 后端依赖暂时不可用", "1")})

    result = request_minutes_access(
        store, dws, console, now=NOW
    )

    assert console.requested == []
    assert result.failed == 1
def test_every_candidate_lands_in_exactly_one_outcome() -> None:
    with pytest.raises(ValueError):
        MinutesAccessResult(discovered=2, requested=1)


def test_a_console_without_the_admin_role_is_not_an_expired_session() -> None:
    """Two different remedies, so they must be two different failures.

    An expired session is renewed by a person signing in. A console this
    account may not open cannot be fixed by signing in at all, so reporting it
    as an expired session would ask for a renewal every day, forever.
    """
    from app.minutes_access import MinutesConsoleUnavailable

    assert not issubclass(MinutesConsoleUnavailable, MinutesBrowserSessionExpired)
    assert not issubclass(MinutesBrowserSessionExpired, MinutesConsoleUnavailable)


class FakePage:
    """Just enough of a Playwright page to drive the sign-in hop."""

    def __init__(self, urls, org_offered=True):
        self._urls = list(urls)
        self.url = self._urls.pop(0)
        self.org_offered = org_offered
        self.clicked = ""

    def goto(self, url, **kwargs):
        return None

    def wait_for_timeout(self, ms):
        return None

    def get_by_text(self, text, exact=False):
        page = self

        class Locator:
            first = None

            def click(self, timeout=0):
                if not page.org_offered:
                    raise RuntimeError("no such organisation on the page")
                page.clicked = text
                if page._urls:
                    page.url = page._urls.pop(0)

        locator = Locator()
        locator.first = locator
        return locator

    def wait_for_url(self, pattern, timeout=0):
        return None


def test_the_console_chooses_the_organisation_that_owns_these_minutes() -> None:
    """Chrome's cookie copy signs Derek in as far as the organisation picker.

    He administers three organisations and only one of them holds the minutes
    this service archives, so the console has to say which; left unanswered,
    the pass reads an empty console and reports a clean nothing-to-do.
    """
    import app.minutes_console_browser as browser

    page = FakePage(
        [
            "https://login.dingtalk.com/oauth2/challenge.htm?x=1",
            "https://shanji-admin.dingtalk.com/history",
        ]
    )
    browser._pick_organisation(page, browser.MINUTES_CONSOLE_ORG)

    assert page.clicked == browser.MINUTES_CONSOLE_ORG
    assert page.url.startswith("https://shanji-admin.dingtalk.com/")


def test_a_cookie_copy_without_a_dingtalk_login_fails_the_pass() -> None:
    """Otherwise the pass reads a signed-out console as "no minutes exist".

    The remedy is not a sign-in here: the browser carries a copy of Derek's own
    Chrome cookies, so the login has to come back in Chrome.
    """
    import app.minutes_console_browser as browser

    page = FakePage(
        ["https://login.dingtalk.com/oauth2/challenge.htm"], org_offered=False
    )

    with pytest.raises(MinutesBrowserSessionExpired):
        browser._pick_organisation(page, browser.MINUTES_CONSOLE_ORG)
