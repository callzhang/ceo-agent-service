import json
from datetime import datetime

import pytest

import app.meeting_alignment as meeting_alignment
from app.dws_client import DwsCalendarAttendee, DwsCalendarEvent, DwsError
from app.meeting_alignment import produce_meeting_alignment_jobs
from app.store import AutoReplyStore


NOW = datetime.fromisoformat("2026-07-14T10:10:00+08:00")


def ended_meeting(
    *,
    meeting_id: str = "minutes-1",
    title: str = "上线评审",
    start: str = "2026-07-14T09:00:00+08:00",
    end: str = "2026-07-14T10:00:00+08:00",
) -> dict:
    return {
        "taskUuid": meeting_id,
        "title": title,
        "startTimeISO": start,
        "endTimeISO": end,
        "status": "ended",
    }


def matching_calendar_event(
    *,
    event_id: str = "event-1",
    title: str = "上线评审",
    self_user: str | None = "u-derek",
) -> DwsCalendarEvent:
    attendees = [
        DwsCalendarAttendee(
            display_name="Derek",
            is_self=True,
            user_id=self_user or "",
            open_dingtalk_id="open-derek",
        ),
        DwsCalendarAttendee(
            display_name="A",
            user_id="u-a",
            open_dingtalk_id="open-a",
        ),
    ]
    return DwsCalendarEvent(
        event_id=event_id,
        title=title,
        start_time="2026-07-14T09:00:00+08:00",
        end_time="2026-07-14T10:00:00+08:00",
        status="confirmed",
        attendee_details=attendees,
    )


class FakeDws:
    def __init__(
        self,
        *,
        minutes_pages: dict[str, dict] | None = None,
        calendar_pages: dict[str, dict] | None = None,
        info: dict[str, dict] | None = None,
    ):
        meeting = ended_meeting()
        self.minutes_pages = minutes_pages or {
            "": {"items": [meeting], "has_more": False, "next_token": ""}
        }
        self.calendar_pages = calendar_pages or {
            "": {
                "events": [matching_calendar_event()],
                "has_more": False,
                "next_cursor": "",
            }
        }
        self.info = info or {meeting["taskUuid"]: meeting}
        self.minutes_calls: list[str] = []
        self.calendar_calls: list[str] = []
        self.info_calls: list[str] = []

    def list_minutes_page(self, *, max_results: int, next_token: str) -> dict:
        assert max_results == 50
        self.minutes_calls.append(next_token)
        return self.minutes_pages[next_token]

    def get_minutes_info(self, meeting_id: str) -> dict:
        self.info_calls.append(meeting_id)
        return self.info[meeting_id]

    def get_current_user_id(self) -> str:
        return "u-derek"

    def list_calendar_events_page(
        self, *, start: str, end: str, limit: int, cursor: str
    ) -> dict:
        assert start
        assert end
        assert limit == 50
        self.calendar_calls.append(cursor)
        return self.calendar_pages[cursor]


def test_producer_leaves_no_calendar_match_unqueued(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        calendar_pages={
            "": {"events": [], "has_more": False, "next_cursor": ""}
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


def test_producer_leaves_ambiguous_calendar_matches_unqueued(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        calendar_pages={
            "": {
                "events": [
                    matching_calendar_event(event_id="event-1"),
                    matching_calendar_event(event_id="event-2"),
                ],
                "has_more": False,
                "next_cursor": "",
            }
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


def test_producer_leaves_meeting_without_exactly_one_self_attendee_unqueued(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event = matching_calendar_event()
    event.attendee_details[0].is_self = False
    dws = FakeDws(
        calendar_pages={
            "": {"events": [event], "has_more": False, "next_cursor": ""}
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


def test_producer_stores_waiting_job_before_ten_minutes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert (
        produce_meeting_alignment_jobs(
            store,
            FakeDws(),
            now=datetime.fromisoformat("2026-07-14T10:09:59+08:00"),
            settle_seconds=600,
        )
        == 1
    )
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "waiting"


def test_producer_queues_at_exactly_ten_minutes_and_preserves_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert produce_meeting_alignment_jobs(
        store, FakeDws(), now=NOW, settle_seconds=600
    ) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "pending"
    assert job.eligible_at == "2026-07-14T10:10:00+08:00"
    source = json.loads(job.source_json)
    assert source["calendar_evidence"]["event_id"] == "event-1"
    assert source["discovery"]["meeting_id"] == "minutes-1"
    assert json.loads(job.participants_json) == source["calendar_evidence"][
        "participants"
    ]


def test_producer_deduplicates_by_stable_minutes_id(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    first = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    second = store.get_meeting_alignment_job_by_meeting_id("minutes-1")

    assert first is not None
    assert second is not None
    assert second.id == first.id


def test_producer_paginates_minutes_and_calendar_before_matching(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        minutes_pages={
            "": {"items": [], "has_more": True, "next_token": "minutes-2"},
            "minutes-2": {
                "items": [ended_meeting()],
                "has_more": False,
                "next_token": "",
            },
        },
        calendar_pages={
            "": {"events": [], "has_more": True, "next_cursor": "calendar-2"},
            "calendar-2": {
                "events": [matching_calendar_event()],
                "has_more": False,
                "next_cursor": "",
            },
        },
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    assert dws.minutes_calls == ["", "minutes-2"]
    assert dws.calendar_calls == ["", "calendar-2"]


@pytest.mark.parametrize(
    ("pages_attribute", "initial_page"),
    [
        (
            "minutes_pages",
            {"items": [], "has_more": True, "next_token": "repeat"},
        ),
        (
            "calendar_pages",
            {"events": [], "has_more": True, "next_cursor": "repeat"},
        ),
    ],
)
def test_producer_hard_fails_repeated_pagination_cursor(
    tmp_path, pages_attribute, initial_page
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    setattr(dws, pages_attribute, {"": initial_page, "repeat": initial_page})

    with pytest.raises(DwsError, match="repeated"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
def test_producer_rejects_terminal_page_with_continuation_cursor(
    tmp_path, pages_attribute
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        pages = {
            "": {"items": [], "has_more": False, "next_token": "unexpected"}
        }
    else:
        pages = {
            "": {
                "events": [matching_calendar_event()],
                "has_more": False,
                "next_cursor": "unexpected",
            }
        }
    setattr(dws, pages_attribute, pages)

    with pytest.raises(DwsError, match="continuation"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
def test_producer_rejects_continuing_page_without_cursor(
    tmp_path, pages_attribute
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        pages = {"": {"items": [], "has_more": True, "next_token": ""}}
    else:
        pages = {"": {"events": [], "has_more": True, "next_cursor": ""}}
    setattr(dws, pages_attribute, pages)

    with pytest.raises(DwsError, match="without next"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
@pytest.mark.parametrize("invalid_has_more", [None, "false", 0])
def test_producer_rejects_missing_or_nonboolean_has_more(
    tmp_path, pages_attribute, invalid_has_more
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        page = {"items": [], "next_token": "page-2"}
        if invalid_has_more is not None:
            page["has_more"] = invalid_has_more
        pages = {"": page}
    else:
        page = {"events": [], "next_cursor": "page-2"}
        if invalid_has_more is not None:
            page["has_more"] = invalid_has_more
        pages = {"": page}
    setattr(dws, pages_attribute, pages)

    with pytest.raises(DwsError, match="has_more must be boolean"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
def test_producer_hard_fails_pagination_page_limit(
    tmp_path, monkeypatch, pages_attribute
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        pages = {
            "": {"items": [], "has_more": True, "next_token": "page-2"},
            "page-2": {
                "items": [],
                "has_more": True,
                "next_token": "page-3",
            },
        }
    else:
        pages = {
            "": {"events": [], "has_more": True, "next_cursor": "page-2"},
            "page-2": {
                "events": [],
                "has_more": True,
                "next_cursor": "page-3",
            },
        }
    setattr(dws, pages_attribute, pages)
    monkeypatch.setattr(meeting_alignment, "DISCOVERY_PAGE_LIMIT", 2)

    with pytest.raises(DwsError, match="exceeded 2 pages"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


def test_producer_never_reenters_terminal_job(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    produce_meeting_alignment_jobs(store, dws, now=NOW)
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    store.update_meeting_alignment_job(job.id, status="no_action")

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job(job.id).status == "no_action"
