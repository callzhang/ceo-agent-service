import pytest

from app.dws_client import DwsError
from app.meeting_alignment_models import MeetingSource
from app.meeting_alignment_source import (
    MeetingSourceIncomplete,
    normalize_meeting_source,
    read_meeting_source,
)


def complete_info() -> dict:
    return {
        "result": {
            "taskUuid": "minutes-1",
            "title": "上线评审",
            "meetingStatus": "ENDED",
            "startTimeISO": "2026-07-14T01:00:00+08:00",
            "endTimeISO": "2026-07-14T02:00:00+08:00",
            "participantList": [
                {
                    "displayName": "Derek",
                    "userId": "u-derek",
                    "openDingTalkId": "open-derek",
                },
                {
                    "displayName": "A",
                    "userId": "u-a",
                    "openDingTalkId": "open-a",
                },
            ],
            "url": "https://shanji.dingtalk.com/app/transcribes/minutes-1",
        }
    }


class FakeDws:
    def __init__(self, *, transcript_error: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.transcript_error = transcript_error

    def get_minutes_info(self, meeting_id: str) -> dict:
        self.calls.append(("info", meeting_id))
        return complete_info()

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

    source = read_meeting_source(dws, "minutes-1")

    assert isinstance(source, MeetingSource)
    assert source.model_dump() == {
        "meeting_id": "minutes-1",
        "title": "上线评审",
        "status": "ended",
        "started_at": "2026-07-14T01:00:00+08:00",
        "ended_at": "2026-07-14T02:00:00+08:00",
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
    class SparseInfoDws(FakeDws):
        def get_minutes_info(self, meeting_id: str) -> dict:
            self.calls.append(("info", meeting_id))
            return {
                "result": {
                    "taskUuid": meeting_id,
                    "title": "DWS 最新标题",
                    "startTime": 1783990800000,
                    "endTime": 1783994400000,
                    "url": "https://shanji.dingtalk.com/app/transcribes/minutes-1",
                }
            }

    dws = SparseInfoDws()

    source = read_meeting_source(
        dws,
        "minutes-1",
        discovery_metadata={
            "meeting_id": "minutes-1",
            "status": "ended",
            "ended_at": "2026-07-14T02:00:00+08:00",
            "participants": [
                {"name": "Derek", "user_id": "u-derek"},
                {"name": "A", "user_id": "u-a"},
            ],
        },
    )

    assert source.title == "DWS 最新标题"
    assert source.status == "ended"
    assert source.ended_at == "2026-07-14T02:00:00+00:00"
    assert [participant.user_id for participant in source.participants] == [
        "u-derek",
        "u-a",
    ]


def test_discovery_metadata_does_not_override_conflicting_live_status():
    class RunningMeetingDws(FakeDws):
        def get_minutes_info(self, meeting_id: str) -> dict:
            info = complete_info()
            info["result"]["meetingStatus"] = "running"
            return info

    with pytest.raises(MeetingSourceIncomplete, match="explicitly ended"):
        read_meeting_source(
            RunningMeetingDws(),
            "minutes-1",
            discovery_metadata={"status": "ended"},
        )


def test_discovery_metadata_does_not_guess_missing_status():
    class SparseInfoDws(FakeDws):
        def get_minutes_info(self, meeting_id: str) -> dict:
            info = complete_info()
            del info["result"]["meetingStatus"]
            return info

    with pytest.raises(MeetingSourceIncomplete, match="explicitly ended"):
        read_meeting_source(
            SparseInfoDws(),
            "minutes-1",
            discovery_metadata={"title": "只有标题"},
        )


def test_normalization_requires_explicit_end_time():
    info = complete_info()
    del info["result"]["endTimeISO"]

    with pytest.raises(MeetingSourceIncomplete, match="end time"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


@pytest.mark.parametrize("status", [None, "running"])
def test_normalization_requires_explicit_ended_status(status):
    info = complete_info()
    if status is None:
        del info["result"]["meetingStatus"]
    else:
        info["result"]["meetingStatus"] = status

    with pytest.raises(MeetingSourceIncomplete, match="explicitly ended"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_normalization_requires_participant_data():
    info = complete_info()
    info["result"]["participantList"] = []

    with pytest.raises(MeetingSourceIncomplete, match="participant data"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_normalization_requires_current_user_to_be_a_participant():
    info = complete_info()

    with pytest.raises(MeetingSourceIncomplete, match="current user.*participant"):
        normalize_meeting_source(info, [], current_user_id="u-someone-else")


def test_normalization_rejects_participant_without_stable_user_id():
    info = complete_info()
    del info["result"]["participantList"][1]["userId"]

    with pytest.raises(MeetingSourceIncomplete, match="participant data"):
        normalize_meeting_source(info, [], current_user_id="u-derek")


def test_read_meeting_source_converts_pagination_integrity_error_to_typed_error():
    dws = FakeDws(
        transcript_error=DwsError(
            "minutes transcription pagination repeated next token"
        )
    )

    with pytest.raises(MeetingSourceIncomplete, match="complete transcript"):
        read_meeting_source(dws, "minutes-1")
