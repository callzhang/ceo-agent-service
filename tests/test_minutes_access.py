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
    session_expiry,
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


def _session(tmp_path: Path, *, days: float = 30.0) -> Path:
    path = tmp_path / "session.json"
    expires = (NOW + timedelta(days=days)).timestamp()
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "access_token",
                        "value": "x",
                        "domain": "shanji-admin.dingtalk.com",
                        "expires": expires,
                    },
                    {
                        "name": "XSRF-TOKEN",
                        "value": "x",
                        "domain": "shanji-admin.dingtalk.com",
                        "expires": -1,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _rows(*keys, size="12.0 MB - Meeting"):
    return [{"row_key": key, "size": size, "initiator": "同事"} for key in keys]


def test_only_the_minutes_the_provider_refuses_are_asked_for(tmp_path: Path) -> None:
    store = _store(tmp_path)
    console = FakeConsole(_rows("readable", "restricted"))
    dws = FakeDws(readable={"readable"})

    result = request_minutes_access(
        store, dws, console, storage_state_path=_session(tmp_path), now=NOW
    )

    assert [uuid for uuid, _ in console.requested] == ["restricted"]
    assert console.requested[0][1] == ACCESS_REQUEST_REASON
    assert result.requested == 1 and result.readable == 1 and result.discovered == 2


def test_a_minute_already_asked_for_is_never_asked_again(tmp_path: Path) -> None:
    """A second request notifies the same colleague a second time."""
    store = _store(tmp_path)
    console = FakeConsole(_rows("restricted"))
    dws = FakeDws()
    session = _session(tmp_path)

    request_minutes_access(store, dws, console, storage_state_path=session, now=NOW)
    second = request_minutes_access(
        store, dws, console, storage_state_path=session, now=NOW
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
        store, dws, console, storage_state_path=_session(tmp_path), now=NOW
    )

    assert console.requested == []
    assert result.discovered == 0


def test_a_page_that_never_read_the_request_back_is_a_failure(tmp_path: Path) -> None:
    """The click is not the evidence; the page reading it back is."""
    store = _store(tmp_path)
    console = FakeConsole(_rows("restricted"), outcomes={"restricted": "failed"})
    dws = FakeDws()

    result = request_minutes_access(
        store, dws, console, storage_state_path=_session(tmp_path), now=NOW
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
    session = _session(tmp_path)

    first = request_minutes_access(
        store, dws, console, storage_state_path=session, now=NOW
    )
    console._outcomes = {}
    second = request_minutes_access(
        store, dws, console, storage_state_path=session, now=NOW
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
        store, dws, console, storage_state_path=_session(tmp_path), now=NOW
    )

    assert console.requested == []
    assert result.failed == 1


def test_an_expired_session_fails_instead_of_reporting_an_empty_pass(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    console = FakeConsole(_rows("restricted"))

    with pytest.raises(MinutesBrowserSessionExpired):
        request_minutes_access(
            store,
            FakeDws(),
            console,
            storage_state_path=_session(tmp_path, days=-1),
            now=NOW,
        )

    assert console.requested == []


def test_a_session_close_to_expiry_asks_to_be_renewed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = request_minutes_access(
        store,
        FakeDws(),
        FakeConsole([]),
        storage_state_path=_session(tmp_path, days=2),
        now=NOW,
    )

    assert result.session_needs_renewal is True
    assert "session_expires_in_days=2.0" in result.summary()


def test_a_fresh_session_does_not_ask_to_be_renewed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = request_minutes_access(
        store,
        FakeDws(),
        FakeConsole([]),
        storage_state_path=_session(tmp_path, days=29),
        now=NOW,
    )

    assert result.session_needs_renewal is False


def test_session_expiry_ignores_the_csrf_cookie(tmp_path: Path) -> None:
    """Only the console cookies carry the session; CSRF is re-issued on load."""
    assert session_expiry(_session(tmp_path, days=30)) == NOW + timedelta(days=30)


def test_session_expiry_is_unknown_when_there_is_no_session_file(
    tmp_path: Path,
) -> None:
    assert session_expiry(tmp_path / "missing.json") is None


def test_every_candidate_lands_in_exactly_one_outcome() -> None:
    with pytest.raises(ValueError):
        MinutesAccessResult(discovered=2, requested=1)
