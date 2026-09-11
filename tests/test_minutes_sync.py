from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest

from app.dws_client import DwsError
from app.minutes_sync import (
    MINUTES_SYNC_SCANNER,
    MinutesSyncResult,
    render_archive,
    should_request_access,
    sync_minutes_once,
)
from app.store import AutoReplyStore


class FakeDws:
    """Only the minutes surface the sync uses."""

    def __init__(self, items, *, basic=None, summary=None, paragraphs=None, errors=None):
        self._items = items
        self._basic = basic or {}
        self._summary = summary or {}
        self._paragraphs = paragraphs or {}
        self._errors = errors or {}
        self.permission_requests: list[str] = []

    @staticmethod
    def parse_minutes_list(payload):
        return payload

    def list_minutes(self):
        return self._items

    def get_minutes_info(self, task_uuid):
        error = self._errors.get(task_uuid)
        if error is not None and task_uuid not in self._basic:
            raise error
        return {"result": self._basic.get(task_uuid, {})}

    def get_minutes_summary(self, task_uuid):
        error = self._errors.get(task_uuid)
        if error is not None:
            raise error
        return {"result": self._summary.get(task_uuid, {})}

    def get_all_minutes_transcription(self, task_uuid):
        error = self._errors.get(task_uuid)
        if error is not None:
            raise error
        return {"paragraphs": self._paragraphs.get(task_uuid, [])}

    def add_minutes_member_permission(self, task_uuid):
        self.permission_requests.append(task_uuid)
        return {"ok": True}


class PaginatedFakeDws(FakeDws):
    def __init__(self, pages):
        super().__init__([])
        self.pages = pages
        self.calls: list[str] = []

    def list_minutes_page(self, *, cursor="", **kwargs):
        del kwargs
        self.calls.append(cursor)
        page = self.pages.get(cursor)
        if isinstance(page, Exception):
            raise page
        return page


def _denied() -> DwsError:
    return DwsError("no permission", "PAT_HIGH_RISK_NO_PERMISSION")


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "minutes.sqlite3")


def _cursor(store: AutoReplyStore) -> dict:
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    return json.loads(state.get("cursor_json") or "{}")


def test_archive_matches_the_existing_local_layout(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "周会", "startTime": 1789025858000, "duration": 600000}},
        summary={"u1": {"fullSummary": "要点"}},
        paragraphs={
            "u1": [
                {"startTime": 2000, "nickName": "张静", "paragraph": "开始了"},
                {"startTime": 1273000, "speakerDisplay": "Mina", "paragraph": "收到"},
                {"startTime": 5000, "nickName": "空", "paragraph": "   "},
            ]
        },
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 1 and result.discovered == 1
    [written] = list((tmp_path / "AI听记").rglob("*.md"))
    text = written.read_text(encoding="utf-8")
    assert text.startswith("<!-- row_key: u1 -->\n")
    assert "<!-- source_url: https://shanji.dingtalk.com/app/transcribes/u1 -->" in text
    assert "# AI Summary" in text and "# Transcript" in text
    assert "\n要点\n" in text
    assert "[00:02] 张静: 开始了" in text
    assert "[21:13] Mina: 收到" in text
    # An empty paragraph contributes no line.
    assert "空:" not in text
    assert _cursor(store)["archived_ids"] == ["u1"]


def test_an_archived_minute_is_not_fetched_again(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "周会", "startTime": 1789025858000}},
        paragraphs={"u1": [{"startTime": 0, "nickName": "A", "paragraph": "x"}]},
    )
    sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    second = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert second.discovered == 0 and second.synced == 0


@pytest.mark.parametrize(
    ("duration", "expected"),
    (
        (timedelta(minutes=5), True),
        (timedelta(minutes=30), True),
        (timedelta(minutes=4, seconds=59), False),
        (None, False),
    ),
)
def test_access_is_requested_only_above_the_duration_threshold(duration, expected):
    assert should_request_access(duration) is expected


def test_no_error_is_classified_as_a_restricted_minute_yet(tmp_path: Path) -> None:
    """The provider's per-minute denial code is not known, so nothing requests.

    DwsError.needs_authorization covers credential-level failures that hit
    every call. If those were read as "this minute is restricted", one broken
    credential would mail an access request to the owner of every minute in
    the list. Until a real per-minute denial is observed, the sync sends none.
    """
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "a"}, {"taskUuid": "b"}],
        basic={
            "a": {"title": "长会", "duration": 3600000},
            "b": {"title": "更长的会", "duration": 7200000},
        },
        errors={"a": _denied(), "b": _denied()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert dws.permission_requests == []
    assert result.permission_requested == 0
    assert result.failed == 2


def test_a_short_restricted_minute_is_skipped_without_asking_its_owner(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "short"}],
        basic={"short": {"title": "闲聊", "duration": 120000}},
        errors={"short": _denied()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.permission_requested == 0
    assert dws.permission_requests == []


def test_a_long_restricted_minute_asks_for_access_but_is_not_synced(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "long"}],
        basic={"long": {"title": "评审", "duration": 1800000}},
        errors={"long": _denied()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    # No request is sent while the per-minute denial code is unverified.
    assert dws.permission_requests == []
    assert result.permission_requested == 0 and result.synced == 0
    assert list((tmp_path / "AI听记").rglob("*.md")) == []


def test_an_unreadable_duration_never_asks_for_access(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = FakeDws([{"taskUuid": "unknown"}], errors={"unknown": _denied()})

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert dws.permission_requests == []
    assert result.failed == 1


def test_a_non_permission_failure_is_failed_not_a_permission_decision(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "broken"}],
        basic={"broken": {"title": "坏的", "duration": 1800000}},
        errors={"broken": DwsError("transcript unavailable", "SOME_OTHER_CODE")},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.failed == 1
    assert dws.permission_requests == []


def test_every_discovered_minute_lands_in_exactly_one_outcome() -> None:
    with pytest.raises(ValueError, match="account for every item"):
        MinutesSyncResult(discovered=2, synced=1)


def test_render_archive_writes_the_provider_summary_markdown() -> None:
    text = render_archive(
        task_uuid="u",
        summary={"fullSummary": "> **主题**: 讨论\n\n## 背景\n\n- 一点"},
        paragraphs=[],
    )
    assert "> **主题**: 讨论\n\n## 背景\n\n- 一点" in text
    # Serialising the payload instead of reading it wrote escaped JSON into the
    # archive, which looks synced and is unreadable.
    assert "fullSummary" not in text
    assert "\\n" not in text


def test_render_archive_accepts_a_minute_without_a_summary_yet() -> None:
    text = render_archive(task_uuid="u", summary={}, paragraphs=[])
    assert "# AI Summary\n\n\n\n# Transcript" in text


def test_an_unreadable_summary_shape_fails_the_item_instead_of_archiving_it(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "会", "startTime": 1_700_000_000_000}},
        summary={"u1": {"sections": ["未知结构"]}},
        paragraphs={"u1": {"paragraphs": [{"startTime": 0, "paragraph": "在"}]}},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.failed == 1 and result.synced == 0
    assert list((tmp_path / "AI听记").rglob("*.md")) == []
    assert _cursor(store)["archived_ids"] == []


def test_incomplete_minutes_pagination_does_not_claim_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = PaginatedFakeDws(
        {
            "": {
                "items": [{"taskUuid": "u1"}],
                "has_more": True,
                "next_token": "page-2",
            },
            "page-2": DwsError("temporary page failure", "NETWORK_ERROR"),
        }
    )
    dws._basic = {"u1": {"title": "周会", "startTime": 1789025858000}}
    dws._paragraphs = {"u1": [{"startTime": 0, "paragraph": "内容"}]}

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 1 and result.discovered == 1
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    assert state["last_success_at"] == ""
    assert "temporary page failure" in state["last_error"]
    cursor = json.loads(state["cursor_json"])
    assert cursor["pagination_deferred"] is True
    assert cursor["pagination_error"] == "temporary page failure"
    assert cursor["archived_ids"] == ["u1"]


def test_incremental_sync_stops_at_first_already_accounted_page(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = PaginatedFakeDws(
        {
            "": {
                "items": [{"taskUuid": "u2"}, {"taskUuid": "u1"}],
                "has_more": True,
                "next_token": "page-2",
            },
            "page-2": RuntimeError("older page must not be fetched"),
        }
    )
    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        last_success_at="2026-09-10T00:00:00+00:00",
        cursor_json=json.dumps({"archived_ids": ["u1"]}),
    )
    dws._basic = {"u2": {"title": "新会议", "startTime": 1789025858000}}
    dws._paragraphs = {"u2": [{"startTime": 0, "paragraph": "内容"}]}

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.discovered == 1
    assert result.synced == 1
    assert dws.calls == [""]
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])["archived_ids"] == ["u1", "u2"]
