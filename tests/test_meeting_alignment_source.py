import pytest

from app.dws_client import DwsCalendarAttendee, DwsCalendarEvent, DwsClient, DwsError
from app.meeting_alignment_models import MeetingSource
from app.meeting_alignment_source import (
    CalendarMeetingEvidence,
    MeetingSourceIncomplete,
    build_calendar_meeting_evidence,
    normalize_meeting_source,
    read_meeting_source,
)


def live_minutes_info(*, status: str | None = None) -> dict:
    result = {
        "taskUuid": "minutes-1",
        "title": "上线评审",
        "startTime": 1784029760000,
        "endTime": 1784035644000,
        "url": "https://shanji.dingtalk.com/app/transcribes/minutes-1",
    }
    if status is not None:
        result["status"] = status
    return {"result": result}


def calendar_evidence(**overrides) -> CalendarMeetingEvidence:
    payload = {
        "event_id": "event-1",
        "title": "上线评审",
        "started_at": "2026-07-14T11:48:00+00:00",
        "ended_at": "2026-07-14T13:28:00+00:00",
        "participants": [
            {
                "name": "Derek",
                "user_id": "u-derek",
                "open_dingtalk_id": "open-derek",
            },
            {"name": "A", "user_id": "u-a", "open_dingtalk_id": "open-a"},
        ],
    }
    payload.update(overrides)
    return CalendarMeetingEvidence.model_validate(payload)


def calendar_event(**overrides) -> DwsCalendarEvent:
    payload = {
        "event_id": "event-1",
        "title": "上线评审",
        "start_time": "2026-07-14T19:48:00+08:00",
        "end_time": "2026-07-14T21:28:00+08:00",
        "status": "confirmed",
        "attendee_details": [
            DwsCalendarAttendee(
                display_name="Derek",
                is_self=True,
                response_status="accepted",
                user_id="u-derek",
                open_dingtalk_id="open-derek",
            ),
            DwsCalendarAttendee(
                display_name="A",
                response_status="accepted",
            ),
        ],
    }
    payload.update(overrides)
    return DwsCalendarEvent(**payload)


def normalized_info(*, status: str | None = None) -> dict:
    info = live_minutes_info(status=status)
    info["result"]["participantList"] = [
        {
            "displayName": "Derek",
            "userId": "u-derek",
            "openDingTalkId": "open-derek",
        },
        {"displayName": "A", "userId": "u-a", "openDingTalkId": "open-a"},
    ]
    return info


class FakeDws:
    def __init__(self, *, transcript_error: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.transcript_error = transcript_error

    def get_minutes_info(self, meeting_id: str) -> dict:
        self.calls.append(("info", meeting_id))
        return live_minutes_info()

    def get_minutes_summary(self, meeting_id: str) -> dict:
        self.calls.append(("summary", meeting_id))
        return {"result": {"fullSummary": "存在上线范围分歧。"}}

    def get_all_minutes_transcription(self, meeting_id: str) -> dict:
        self.calls.append(("transcript", meeting_id))
        if self.transcript_error is not None:
            raise self.transcript_error
        return {
            "paragraphs": [
                {
                    "nickName": "A",
                    "unionId": "union-a",
                    "startTime": 1200,
                    "paragraph": "先全量",
                },
                {
                    "speakerName": "B",
                    "speakerUserId": "u-b",
                    "timestamp": "00:02",
                    "text": "先灰度",
                },
            ]
        }

    def get_current_user_id(self) -> str:
        self.calls.append(("current_user", ""))
        return "u-derek"


def test_read_meeting_source_combines_metadata_summary_transcript_and_current_user():
    dws = FakeDws()

    source = read_meeting_source(
        dws,
        "minutes-1",
        calendar_evidence=calendar_evidence(),
    )

    assert isinstance(source, MeetingSource)
    assert source.model_dump() == {
        "meeting_id": "minutes-1",
        "title": "上线评审",
        "status": "ended",
        "started_at": "2026-07-14T11:49:20+00:00",
        "ended_at": "2026-07-14T13:27:24+00:00",
        "participants": [
            {
                "name": "Derek",
                "user_id": "u-derek",
                "open_dingtalk_id": "open-derek",
            },
            {"name": "A", "user_id": "u-a", "open_dingtalk_id": "open-a"},
        ],
        "current_user_id": "u-derek",
        "summary": "存在上线范围分歧。",
        "transcript": [
            {
                "speaker_name": "A",
                "speaker_user_id": "union-a",
                "timestamp": "1200",
                "text": "先全量",
            },
            {
                "speaker_name": "B",
                "speaker_user_id": "u-b",
                "timestamp": "00:02",
                "text": "先灰度",
            },
        ],
        "source_url": "https://shanji.dingtalk.com/app/transcribes/minutes-1",
    }
    assert dws.calls == [
        ("info", "minutes-1"),
        ("summary", "minutes-1"),
        ("transcript", "minutes-1"),
        ("current_user", ""),
    ]


def test_read_meeting_source_merges_producer_discovery_metadata():
    dws = FakeDws()

    source = read_meeting_source(
        dws,
        "minutes-1",
        calendar_evidence=calendar_evidence(),
    )

    assert source.title == "上线评审"
    assert source.status == "ended"
    assert source.ended_at == "2026-07-14T13:27:24+00:00"
    assert [participant.user_id for participant in source.participants] == [
        "u-derek",
        "u-a",
    ]


def test_discovery_metadata_does_not_override_conflicting_live_status():
    class RunningMeetingDws(FakeDws):
        def get_minutes_info(self, meeting_id: str) -> dict:
            return live_minutes_info(status="running")

    with pytest.raises(MeetingSourceIncomplete, match="explicitly ended"):
        read_meeting_source(
            RunningMeetingDws(),
            "minutes-1",
            calendar_evidence=calendar_evidence(),
        )


@pytest.mark.parametrize("status", ["running", "cancelled"])
def test_explicit_non_ended_live_status_is_rejected(status):
    class NonEndedDws(FakeDws):
        def get_minutes_info(self, meeting_id: str) -> dict:
            return live_minutes_info(status=status)

    with pytest.raises(MeetingSourceIncomplete, match="explicitly ended"):
        read_meeting_source(
            NonEndedDws(),
            "minutes-1",
            calendar_evidence=calendar_evidence(),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_id": ""},
        {"title": "另一个会议"},
        {"started_at": "2026-07-14T07:49:19+00:00"},
        {"ended_at": "2026-07-14T17:27:25+00:00"},
    ],
)
def test_read_meeting_source_rejects_stale_calendar_evidence(overrides):
    with pytest.raises(MeetingSourceIncomplete, match="calendar evidence"):
        read_meeting_source(
            FakeDws(),
            "minutes-1",
            calendar_evidence=calendar_evidence(**overrides),
        )


def test_build_calendar_evidence_requires_exactly_one_match():
    with pytest.raises(MeetingSourceIncomplete, match="exactly one"):
        build_calendar_meeting_evidence(live_minutes_info(), [], "u-derek")
    with pytest.raises(MeetingSourceIncomplete, match="exactly one"):
        build_calendar_meeting_evidence(
            live_minutes_info(),
            [calendar_event(), calendar_event(event_id="event-2")],
            "u-derek",
        )
    untitled_info = live_minutes_info()
    untitled_info["result"]["title"] = ""
    with pytest.raises(MeetingSourceIncomplete, match="exactly one"):
        build_calendar_meeting_evidence(
            untitled_info, [calendar_event(title="")], "u-derek"
        )


def test_build_calendar_evidence_rejects_same_title_all_day_event():
    with pytest.raises(MeetingSourceIncomplete, match="exactly one"):
        build_calendar_meeting_evidence(
            live_minutes_info(),
            [
                calendar_event(
                    start_time="2026-07-14T00:00:00+08:00",
                    end_time="2026-07-14T23:59:59+08:00",
                )
            ],
            "u-derek",
        )


def test_build_calendar_evidence_accepts_real_delayed_meeting_shape():
    evidence = build_calendar_meeting_evidence(
        live_minutes_info(),
        [
            calendar_event(
                start_time="2026-07-14T18:00:00+08:00",
                end_time="2026-07-14T20:00:00+08:00",
            )
        ],
        "u-derek",
    )

    assert evidence.event_id == "event-1"


def test_build_calendar_evidence_rejects_non_overlapping_event_within_four_hours():
    with pytest.raises(MeetingSourceIncomplete, match="exactly one"):
        build_calendar_meeting_evidence(
            live_minutes_info(),
            [
                calendar_event(
                    start_time="2026-07-14T16:00:00+08:00",
                    end_time="2026-07-14T19:00:00+08:00",
                )
            ],
            "u-derek",
        )


@pytest.mark.parametrize(
    ("start_time", "matches"),
    [
        ("2026-07-14T15:49:20+08:00", True),
        ("2026-07-14T15:49:19+08:00", False),
    ],
)
def test_calendar_meeting_start_delta_four_hour_boundary(start_time, matches):
    event = calendar_event(
        start_time=start_time,
        end_time="2026-07-14T21:27:24+08:00",
    )
    if matches:
        assert build_calendar_meeting_evidence(
            live_minutes_info(), [event], "u-derek"
        ).event_id == "event-1"
    else:
        with pytest.raises(MeetingSourceIncomplete, match="exactly one"):
            build_calendar_meeting_evidence(
                live_minutes_info(), [event], "u-derek"
            )


@pytest.mark.parametrize(
    ("end_time", "matches"),
    [
        ("2026-07-15T01:27:24+08:00", True),
        ("2026-07-15T01:27:25+08:00", False),
    ],
)
def test_calendar_meeting_end_delta_four_hour_boundary(end_time, matches):
    event = calendar_event(
        start_time="2026-07-14T19:49:20+08:00",
        end_time=end_time,
    )
    if matches:
        assert build_calendar_meeting_evidence(
            live_minutes_info(), [event], "u-derek"
        ).event_id == "event-1"
    else:
        with pytest.raises(MeetingSourceIncomplete, match="exactly one"):
            build_calendar_meeting_evidence(
                live_minutes_info(), [event], "u-derek"
            )


def test_build_calendar_evidence_requires_self_attendee():
    event = calendar_event(
        attendee_details=[
            DwsCalendarAttendee(display_name="Derek", user_id="u-derek")
        ]
    )
    with pytest.raises(MeetingSourceIncomplete, match="self attendee"):
        build_calendar_meeting_evidence(live_minutes_info(), [event], "u-derek")


def test_raw_calendar_parse_to_unique_match_to_source():
    events = DwsClient.parse_calendar_events(
        {
            "result": {
                "events": [
                    {
                        "id": "event-1",
                        "title": "  上线评审  ",
                        "status": "confirmed",
                        "startTime": "2026-07-14T19:48:00+08:00",
                        "endTime": "2026-07-14T21:28:00+08:00",
                        "attendees": [
                            {"displayName": "Derek", "self": True},
                            {"displayName": "A", "responseStatus": "accepted"},
                        ],
                    }
                ]
            }
        }
    )
    evidence = build_calendar_meeting_evidence(
        live_minutes_info(), events, "u-derek"
    )

    source = read_meeting_source(
        FakeDws(), "minutes-1", calendar_evidence=evidence
    )

    assert source.participants[0].user_id == "u-derek"
    assert source.participants[1].user_id == ""


def test_live_info_rejects_conflicting_participant_aliases():
    info = normalized_info()
    info["result"]["participants"] = [
        {"name": "Derek", "user_id": "u-derek"},
        {"name": "B", "user_id": "u-b"},
    ]

    with pytest.raises(MeetingSourceIncomplete, match="conflicting participants"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


@pytest.mark.parametrize(
    ("alias", "conflicting_value", "message"),
    [
        ("name", "另一个标题", "conflicting meeting title"),
        ("meeting_id", "minutes-2", "conflicting meeting id"),
        ("ended_at", "2026-07-14T03:00:00+08:00", "conflicting meeting end time"),
    ],
)
def test_live_info_rejects_conflicting_critical_aliases(
    alias,
    conflicting_value,
    message,
):
    info = normalized_info()
    info["result"][alias] = conflicting_value

    with pytest.raises(MeetingSourceIncomplete, match=message):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_same_normalized_alias_values_are_allowed():
    info = normalized_info()
    info["result"].update(
        status="ended",
        participants=[
            {
                "name": "Derek",
                "user_id": "u-derek",
                "open_dingtalk_id": "open-derek",
            },
            {
                "name": "A",
                "user_id": "u-a",
                "open_dingtalk_id": "open-a",
            },
        ],
        ended_at=1784035644000,
    )

    source = normalize_meeting_source(info, [], current_user_id="u-derek")

    assert source.status == "ended"
    assert [participant.user_id for participant in source.participants] == [
        "u-derek",
        "u-a",
    ]


def test_normalization_requires_explicit_end_time():
    info = normalized_info()
    del info["result"]["endTime"]

    with pytest.raises(MeetingSourceIncomplete, match="end time"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_normalization_requires_explicit_start_time():
    info = normalized_info()
    del info["result"]["startTime"]

    with pytest.raises(MeetingSourceIncomplete, match="start time"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("startTime", "not-a-time"),
        ("endTime", "not-a-time"),
        ("startTime", "2026-07-14T01:00:00"),
        ("endTime", "2026-07-14T02:00:00"),
    ],
)
def test_normalization_rejects_invalid_or_naive_times(field, value):
    info = normalized_info()
    info["result"][field] = value

    with pytest.raises(MeetingSourceIncomplete, match="time"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_normalization_accepts_missing_status_with_complete_transcript():
    source = normalize_meeting_source(
        normalized_info(),
        [{"speakerName": "A", "text": "完成"}],
        current_user_id="u-derek",
    )

    assert source.status == "ended"


def test_normalization_rejects_transcript_page_with_more_results():
    with pytest.raises(MeetingSourceIncomplete, match="complete transcript"):
        normalize_meeting_source(
            normalized_info(),
            {
                "result": {
                    "hasNext": True,
                    "nextToken": "page-2",
                    "paragraphList": [{"speakerName": "A", "text": "未完成"}],
                }
            },
            current_user_id="u-derek",
        )


def test_normalization_accepts_explicit_ended_status():
    source = normalize_meeting_source(
        normalized_info(status="ENDED"),
        [],
        current_user_id="u-derek",
    )

    assert source.status == "ended"


def test_normalization_rejects_explicit_running_status():
    info = normalized_info(status="running")
    with pytest.raises(MeetingSourceIncomplete, match="explicitly ended"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_normalization_requires_participant_data():
    info = normalized_info()
    info["result"]["participantList"] = []

    with pytest.raises(MeetingSourceIncomplete, match="participant data"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_normalization_requires_current_user_to_be_a_participant():
    info = normalized_info()

    with pytest.raises(MeetingSourceIncomplete, match="current user.*participant"):
        normalize_meeting_source(info, [], current_user_id="u-someone-else")


def test_normalization_allows_non_current_participant_without_stable_user_id():
    info = normalized_info()
    del info["result"]["participantList"][1]["userId"]

    source = normalize_meeting_source(info, [], current_user_id="u-derek")

    assert source.participants[1].user_id == ""


def test_read_meeting_source_converts_pagination_integrity_error_to_typed_error():
    dws = FakeDws(
        transcript_error=DwsError(
            "minutes transcription pagination repeated next token"
        )
    )

    with pytest.raises(MeetingSourceIncomplete, match="complete transcript"):
        read_meeting_source(
            dws,
            "minutes-1",
            calendar_evidence=calendar_evidence(),
        )
