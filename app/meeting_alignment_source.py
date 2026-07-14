from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.dws_client import DwsCalendarEvent, DwsClient, DwsError
from app.meeting_alignment_models import (
    MeetingParticipant,
    MeetingSource,
    TranscriptLine,
)


class MeetingSourceIncomplete(RuntimeError):
    """The Minutes record cannot yet prove a complete, eligible meeting source."""


CALENDAR_BOUNDARY_DRIFT_SECONDS = 4 * 60 * 60


class CalendarMeetingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    title: str
    started_at: str
    ended_at: str
    participants: list[MeetingParticipant]


class MeetingSourceDws(Protocol):
    def get_minutes_info(self, meeting_id: str) -> dict[str, Any]: ...

    def get_minutes_summary(self, meeting_id: str) -> dict[str, Any]: ...

    def get_all_minutes_transcription(self, meeting_id: str) -> dict[str, Any]: ...

    def get_current_user_id(self) -> str: ...


def read_meeting_source(
    dws: MeetingSourceDws,
    meeting_id: str,
    *,
    calendar_evidence: CalendarMeetingEvidence,
) -> MeetingSource:
    info = dws.get_minutes_info(meeting_id)
    summary = dws.get_minutes_summary(meeting_id)
    try:
        transcription = dws.get_all_minutes_transcription(meeting_id)
    except DwsError as exc:
        raise MeetingSourceIncomplete(
            f"complete transcript is unavailable: {exc}"
        ) from exc
    current_user_id = dws.get_current_user_id()
    return normalize_meeting_source(
        _merge_calendar_evidence(info, calendar_evidence, current_user_id),
        transcription,
        current_user_id=current_user_id,
        summary=summary,
        meeting_id=meeting_id,
    )


def normalize_meeting_source(
    info: dict[str, Any],
    transcription: dict[str, Any] | list[dict[str, Any]],
    *,
    current_user_id: str,
    summary: dict[str, Any] | str | None = None,
    meeting_id: str = "",
) -> MeetingSource:
    data = _payload_data(info)
    _validate_metadata_aliases(data)
    payload_meeting_id = _metadata_text(
        data,
        "meeting id",
        "taskUuid",
        "meetingId",
        "minutesId",
        "meeting_id",
        "uuid",
        "id",
    )
    normalized_meeting_id = meeting_id.strip() or payload_meeting_id
    if (
        meeting_id.strip()
        and payload_meeting_id
        and payload_meeting_id != meeting_id.strip()
    ):
        raise MeetingSourceIncomplete("conflicting meeting id")
    if not normalized_meeting_id:
        raise MeetingSourceIncomplete("meeting id is missing")

    status = _metadata_text(
        data,
        "meeting status",
        "status",
        "meetingStatus",
        "state",
        "taskStatus",
        normalizer=lambda value: str(value).strip().casefold(),
    )
    if status and status.casefold() != "ended":
        raise MeetingSourceIncomplete("meeting is not explicitly ended")

    started_at = _metadata_time(
        data,
        "meeting start time",
        "startTimeISO",
        "started_at",
        "startedAt",
        "startTime",
        "start_time",
    )
    if not started_at:
        raise MeetingSourceIncomplete("meeting start time is missing")

    ended_at = _metadata_time(
        data,
        "meeting end time",
        "endTimeISO",
        "ended_at",
        "endedAt",
        "endTime",
        "end_time",
    )
    if not ended_at:
        raise MeetingSourceIncomplete("meeting end time is missing")

    participants = _participants(data)
    if not participants:
        raise MeetingSourceIncomplete("meeting participant data is missing")

    normalized_current_user_id = current_user_id.strip()
    if not normalized_current_user_id:
        raise MeetingSourceIncomplete("current user id is missing")
    if not any(
        participant.user_id == normalized_current_user_id
        for participant in participants
    ):
        raise MeetingSourceIncomplete("current user is not a meeting participant")

    transcript = _transcript_lines(transcription)
    return MeetingSource(
        meeting_id=normalized_meeting_id,
        title=_metadata_text(data, "meeting title", "title", "name"),
        status="ended",
        started_at=started_at,
        ended_at=ended_at,
        participants=participants,
        current_user_id=normalized_current_user_id,
        summary=_summary_text(summary),
        transcript=transcript,
        source_url=_metadata_text(
            data,
            "meeting source url",
            "url",
            "shareUrl",
            "sourceUrl",
            "source_url",
        ),
    )


def _payload_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    data = payload.get("data")
    if isinstance(result, dict):
        nested_data = result.get("data")
        return nested_data if isinstance(nested_data, dict) else result
    return data if isinstance(data, dict) else payload


def _merge_calendar_evidence(
    info: dict[str, Any],
    evidence: CalendarMeetingEvidence,
    current_user_id: str,
) -> dict[str, Any]:
    live = dict(_payload_data(info))
    _validate_metadata_aliases(live)
    _verify_calendar_evidence(live, evidence, current_user_id)
    live["participants"] = [
        participant.model_dump() for participant in evidence.participants
    ]
    return {"result": live}


def build_calendar_meeting_evidence(
    info: dict[str, Any],
    events: list[DwsCalendarEvent],
    current_user_id: str,
) -> CalendarMeetingEvidence:
    data = _payload_data(info)
    _validate_metadata_aliases(data)
    matches = [event for event in events if _calendar_event_matches(data, event)]
    if len(matches) != 1:
        raise MeetingSourceIncomplete("meeting requires exactly one calendar event")
    event = matches[0]
    self_attendees = [detail for detail in event.attendee_details if detail.is_self]
    if len(self_attendees) != 1:
        raise MeetingSourceIncomplete("calendar event requires one self attendee")
    self_attendee = self_attendees[0]
    if self_attendee.user_id and self_attendee.user_id != current_user_id:
        raise MeetingSourceIncomplete("calendar self attendee conflicts with current user")
    participants: list[MeetingParticipant] = []
    for detail in event.attendee_details:
        if not detail.display_name.strip():
            continue
        participants.append(
            MeetingParticipant(
                name=detail.display_name.strip(),
                user_id=current_user_id if detail.is_self else detail.user_id.strip(),
                open_dingtalk_id=detail.open_dingtalk_id.strip(),
            )
        )
    return CalendarMeetingEvidence(
        event_id=event.event_id,
        title=event.title,
        started_at=_normalized_time(event.start_time),
        ended_at=_normalized_time(event.end_time),
        participants=participants,
    )


def _calendar_event_matches(data: dict[str, Any], event: DwsCalendarEvent) -> bool:
    if not event.event_id.strip() or event.status.strip().casefold() != "confirmed":
        return False
    title = _metadata_text(data, "meeting title", "title", "name")
    normalized_title = _normalized_title(title)
    normalized_event_title = _normalized_title(event.title)
    if not normalized_title or normalized_title != normalized_event_title:
        return False
    try:
        meeting_start = _parsed_time(_metadata_time(
            data, "meeting start time", "startTimeISO", "started_at", "startedAt",
            "startTime", "start_time"
        ))
        meeting_end = _parsed_time(_metadata_time(
            data, "meeting end time", "endTimeISO", "ended_at", "endedAt",
            "endTime", "end_time"
        ))
        event_start = _parsed_time(_normalized_time(event.start_time))
        event_end = _parsed_time(_normalized_time(event.end_time))
    except MeetingSourceIncomplete:
        return False
    return (
        meeting_start < event_end
        and event_start < meeting_end
        and abs(meeting_start.timestamp() - event_start.timestamp())
        <= CALENDAR_BOUNDARY_DRIFT_SECONDS
        and abs(meeting_end.timestamp() - event_end.timestamp())
        <= CALENDAR_BOUNDARY_DRIFT_SECONDS
    )


def _verify_calendar_evidence(
    data: dict[str, Any], evidence: CalendarMeetingEvidence, current_user_id: str
) -> None:
    event = DwsCalendarEvent(
        event_id=evidence.event_id,
        title=evidence.title,
        start_time=evidence.started_at,
        end_time=evidence.ended_at,
        status="confirmed",
    )
    if not _calendar_event_matches(data, event):
        raise MeetingSourceIncomplete("calendar evidence is stale or mismatched")
    if sum(p.user_id == current_user_id for p in evidence.participants) != 1:
        raise MeetingSourceIncomplete("calendar evidence lacks stable current user")


def _normalized_title(value: str) -> str:
    return " ".join(value.split()).casefold()


def _parsed_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _value(payload: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        value = payload.get(alias)
        if value is not None:
            return value
    return None


def _explicit_value(payload: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        value = payload.get(alias)
        if value is None or value == "" or value == []:
            continue
        return value
    return None


def _metadata_text(
    payload: dict[str, Any],
    label: str,
    *aliases: str,
    normalizer=lambda value: str(value).strip(),
) -> str:
    values = [
        normalizer(payload[alias])
        for alias in aliases
        if _is_explicit(payload.get(alias))
    ]
    if not values:
        return ""
    if any(value != values[0] for value in values[1:]):
        raise MeetingSourceIncomplete(f"conflicting {label}")
    return values[0]


def _metadata_time(
    payload: dict[str, Any],
    label: str,
    *aliases: str,
) -> str:
    values = [
        _normalized_time(payload[alias])
        for alias in aliases
        if _is_explicit(payload.get(alias))
    ]
    if not values:
        return ""
    signatures = [_time_signature(value) for value in values]
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise MeetingSourceIncomplete(f"conflicting {label}")
    return values[0]


def _is_explicit(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _validate_metadata_aliases(data: dict[str, Any]) -> None:
    if not data:
        return
    _metadata_text(
        data,
        "meeting id",
        "taskUuid",
        "meetingId",
        "minutesId",
        "meeting_id",
        "uuid",
        "id",
    )
    _metadata_text(data, "meeting title", "title", "name")
    _metadata_text(
        data,
        "meeting status",
        "status",
        "meetingStatus",
        "state",
        "taskStatus",
        normalizer=lambda value: str(value).strip().casefold(),
    )
    _metadata_time(
        data,
        "meeting start time",
        "startTimeISO",
        "started_at",
        "startedAt",
        "startTime",
        "start_time",
    )
    _metadata_time(
        data,
        "meeting end time",
        "endTimeISO",
        "ended_at",
        "endedAt",
        "endTime",
        "end_time",
    )
    _metadata_text(
        data,
        "meeting source url",
        "url",
        "shareUrl",
        "sourceUrl",
        "source_url",
    )
    _participants(data)


def _text(payload: dict[str, Any], *aliases: str) -> str:
    value = _value(payload, *aliases)
    return str(value).strip() if value is not None else ""


def _normalized_time(value: Any) -> str:
    if value is None or isinstance(value, bool):
        raise MeetingSourceIncomplete("invalid meeting time")
    if isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) >= 100_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError) as exc:
            raise MeetingSourceIncomplete("invalid meeting time") from exc
    if not isinstance(value, str):
        raise MeetingSourceIncomplete("invalid meeting time")
    text = value.strip()
    if not text:
        raise MeetingSourceIncomplete("invalid meeting time")
    try:
        numeric = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MeetingSourceIncomplete("invalid meeting time") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise MeetingSourceIncomplete("invalid meeting time")
        return parsed.isoformat()
    return _normalized_time(numeric)


def _time_signature(value: str) -> str | float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        return value
    return parsed.timestamp()


def _participants(data: dict[str, Any]) -> list[MeetingParticipant]:
    aliases = (
        "participants",
        "participantList",
        "attendees",
        "attendeeList",
        "members",
        "memberList",
    )
    normalized_lists = [
        _parse_participants(data[alias])
        for alias in aliases
        if _is_explicit(data.get(alias))
    ]
    if not normalized_lists:
        return []
    signatures = [
        sorted(
            (
                participant.name,
                participant.user_id,
                participant.open_dingtalk_id,
            )
            for participant in participants
        )
        for participants in normalized_lists
    ]
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise MeetingSourceIncomplete("conflicting participants")
    return normalized_lists[0]


def _parse_participants(raw_participants: Any) -> list[MeetingParticipant]:
    if not isinstance(raw_participants, list) or not raw_participants:
        return []
    participants: list[MeetingParticipant] = []
    for raw in raw_participants:
        if not isinstance(raw, dict):
            raise MeetingSourceIncomplete("meeting participant data is incomplete")
        name = _metadata_text(
            raw,
            "participant name",
            "name",
            "displayName",
            "nickName",
            "userName",
        )
        user_id = _metadata_text(
            raw,
            "participant user id",
            "userId",
            "user_id",
            "staffId",
            "employeeId",
            "uid",
        )
        if not name:
            raise MeetingSourceIncomplete("meeting participant data is incomplete")
        participants.append(
            MeetingParticipant(
                name=name,
                user_id=user_id,
                open_dingtalk_id=_metadata_text(
                    raw,
                    "participant open dingtalk id",
                    "openDingTalkId",
                    "openDingtalkId",
                    "open_dingtalk_id",
                ),
            )
        )
    return participants


def _summary_text(summary: dict[str, Any] | str | None) -> str:
    if isinstance(summary, str):
        return summary.strip()
    data = _payload_data(summary)
    return _text(data, "fullSummary", "summary", "markdown", "content", "text")


def _transcript_lines(
    transcription: dict[str, Any] | list[dict[str, Any]],
) -> list[TranscriptLine]:
    if isinstance(transcription, list):
        paragraphs = transcription
    elif isinstance(transcription, dict):
        try:
            has_next = DwsClient.parse_minutes_transcription_has_next(transcription)
            next_token = DwsClient.parse_minutes_next_token(transcription)
        except DwsError as exc:
            raise MeetingSourceIncomplete(
                f"complete transcript is unavailable: {exc}"
            ) from exc
        if has_next is True or next_token:
            raise MeetingSourceIncomplete("complete transcript is unavailable")
        paragraphs = DwsClient.parse_minutes_transcription_paragraphs(transcription)
        if not any(
            isinstance(candidate, list)
            for candidate in (
                transcription.get("paragraphs"),
                transcription.get("paragraphList"),
                _payload_data(transcription).get("paragraphs"),
                _payload_data(transcription).get("paragraphList"),
            )
        ):
            raise MeetingSourceIncomplete("complete transcript is unavailable")
    else:
        raise MeetingSourceIncomplete("complete transcript is unavailable")

    lines: list[TranscriptLine] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            raise MeetingSourceIncomplete("complete transcript contains invalid data")
        text = _text(paragraph, "text", "paragraph", "sentence", "content")
        if not text:
            continue
        speaker_display = paragraph.get("speakerDisplay")
        display = speaker_display if isinstance(speaker_display, dict) else {}
        lines.append(
            TranscriptLine(
                speaker_name=(
                    _text(paragraph, "speakerName", "nickName", "speaker", "name")
                    or _text(display, "nickName", "displayName", "name")
                ),
                speaker_user_id=_text(
                    paragraph,
                    "speakerUserId",
                    "userId",
                    "unionId",
                    "speaker_user_id",
                ),
                timestamp=_text(
                    paragraph,
                    "timestamp",
                    "startTimeISO",
                    "startTime",
                    "beginTime",
                ),
                text=text,
            )
        )
    return lines
