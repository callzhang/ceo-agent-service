from datetime import datetime, timezone
from typing import Any, Protocol

from app.dws_client import DwsClient, DwsError
from app.meeting_alignment_models import (
    MeetingParticipant,
    MeetingSource,
    TranscriptLine,
)


class MeetingSourceIncomplete(RuntimeError):
    """The Minutes record cannot yet prove a complete, eligible meeting source."""


class MeetingSourceDws(Protocol):
    def get_minutes_info(self, meeting_id: str) -> dict[str, Any]: ...

    def get_minutes_summary(self, meeting_id: str) -> dict[str, Any]: ...

    def get_all_minutes_transcription(self, meeting_id: str) -> dict[str, Any]: ...

    def get_current_user_id(self) -> str: ...


def read_meeting_source(
    dws: MeetingSourceDws,
    meeting_id: str,
    *,
    discovery_metadata: dict[str, Any] | None = None,
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
        _merge_discovery_metadata(info, discovery_metadata),
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
    if status.casefold() != "ended":
        raise MeetingSourceIncomplete("meeting is not explicitly ended")

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
        started_at=_metadata_time(
            data,
            "meeting start time",
            "startTimeISO",
            "started_at",
            "startedAt",
            "startTime",
            "start_time",
        ),
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


def _merge_discovery_metadata(
    info: dict[str, Any],
    discovery_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    live = dict(_payload_data(info))
    discovered = _payload_data(discovery_metadata)
    _validate_metadata_aliases(live)
    _validate_metadata_aliases(discovered)
    if not discovered:
        return {"result": live}
    field_aliases = {
        "meeting_id": (
            "taskUuid",
            "meetingId",
            "minutesId",
            "meeting_id",
            "uuid",
            "id",
        ),
        "title": ("title", "name"),
        "status": ("status", "meetingStatus", "state", "taskStatus"),
        "started_at": (
            "startTimeISO",
            "started_at",
            "startedAt",
            "startTime",
            "start_time",
        ),
        "ended_at": (
            "endTimeISO",
            "ended_at",
            "endedAt",
            "endTime",
            "end_time",
        ),
        "participants": (
            "participants",
            "participantList",
            "attendees",
            "attendeeList",
            "members",
            "memberList",
        ),
        "source_url": ("url", "shareUrl", "sourceUrl", "source_url"),
    }
    for canonical_name, aliases in field_aliases.items():
        if _explicit_value(live, *aliases) is not None:
            continue
        discovered_value = _explicit_value(discovered, *aliases)
        if discovered_value is not None:
            live[canonical_name] = discovered_value
    return {"result": live}


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
        return ""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) >= 100_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return ""
    try:
        numeric = float(text)
    except ValueError:
        return text
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
        if not name or not user_id:
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
