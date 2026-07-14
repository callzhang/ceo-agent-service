import json
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.dws_client import DwsCalendarEvent, DwsError
from app.meeting_alignment_source import (
    CalendarMeetingEvidence,
    MeetingSourceIncomplete,
    build_calendar_meeting_evidence,
    minutes_meeting_id,
    normalize_minutes_discovery_metadata,
)
from app.store import AutoReplyStore


DISCOVERY_PAGE_LIMIT = 100
DISCOVERY_PAGE_SIZE = 50
DEFAULT_MEETING_DISCOVERY_LOOKBACK = timedelta(days=7)
TERMINAL_STATUSES = frozenset({"no_action", "sent", "failed"})


class MeetingProducerDws(Protocol):
    def list_minutes_page(
        self, *, limit: int, cursor: str, start: str, end: str
    ) -> dict[str, Any]: ...

    def get_minutes_info(self, meeting_id: str) -> dict[str, Any]: ...

    def get_current_user_id(self) -> str: ...

    def list_calendar_events_page(
        self, *, start: str, end: str, limit: int, cursor: str
    ) -> dict[str, Any]: ...


def produce_meeting_alignment_jobs(
    store: AutoReplyStore,
    dws: MeetingProducerDws,
    *,
    now: datetime,
    settle_seconds: int = 600,
    discovery_lookback: timedelta = DEFAULT_MEETING_DISCOVERY_LOOKBACK,
) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("meeting producer now must include a timezone")
    if settle_seconds < 0:
        raise ValueError("settle_seconds must not be negative")
    if discovery_lookback.total_seconds() <= 0:
        raise ValueError("meeting discovery lookback must be positive")

    current_user_id = dws.get_current_user_id().strip()
    if not current_user_id:
        raise DwsError("meeting producer current user id is missing")

    created = 0
    for list_item in _list_all_minutes(
        dws,
        start=(now - discovery_lookback).isoformat(),
        end=now.isoformat(),
    ):
        try:
            meeting_id = minutes_meeting_id(list_item)
        except MeetingSourceIncomplete:
            continue
        if not meeting_id:
            continue
        existing = store.get_meeting_alignment_job_by_meeting_id(meeting_id)
        if existing is not None and existing.status in TERMINAL_STATUSES:
            continue

        info = dws.get_minutes_info(meeting_id)
        try:
            metadata = normalize_minutes_discovery_metadata(list_item, info)
        except MeetingSourceIncomplete:
            continue
        if metadata.meeting_id != meeting_id:
            continue
        if metadata.status and metadata.status != "ended":
            continue
        started_at = datetime.fromisoformat(metadata.started_at)
        ended_at = datetime.fromisoformat(metadata.ended_at)
        if started_at >= ended_at:
            continue

        events = _list_all_calendar_events(
            dws,
            start=(started_at - timedelta(hours=4)).isoformat(),
            end=(ended_at + timedelta(hours=4)).isoformat(),
        )
        matcher_info = {
            "taskUuid": meeting_id,
            "title": metadata.title,
            "startTimeISO": metadata.started_at,
            "endTimeISO": metadata.ended_at,
        }
        try:
            evidence = build_calendar_meeting_evidence(
                matcher_info, events, current_user_id
            )
        except MeetingSourceIncomplete:
            continue
        if sum(
            participant.user_id == current_user_id
            for participant in evidence.participants
        ) != 1:
            continue

        eligible_at = ended_at + timedelta(seconds=settle_seconds)
        status = "pending" if now >= eligible_at else "waiting"
        source_json = _source_json(
            meeting_id=meeting_id,
            metadata=metadata.model_dump(mode="json"),
            list_item=list_item,
            info=info,
            evidence=evidence,
        )
        store.upsert_meeting_alignment_job(
            meeting_id=meeting_id,
            title=metadata.title,
            source_json=source_json,
            participants_json=json.dumps(
                [
                    participant.model_dump(mode="json")
                    for participant in evidence.participants
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            ended_at=ended_at.isoformat(),
            eligible_at=eligible_at.isoformat(),
            status=status,
        )
        if existing is None:
            created += 1
    return created


def _list_all_minutes(
    dws: MeetingProducerDws, *, start: str, end: str
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_token = ""
    seen_tokens: set[str] = set()
    for _ in range(DISCOVERY_PAGE_LIMIT):
        page = dws.list_minutes_page(
            limit=DISCOVERY_PAGE_SIZE,
            cursor=next_token,
            start=start,
            end=end,
        )
        page_items = page.get("items") or []
        items.extend(item for item in page_items if isinstance(item, dict))
        has_more, next_token = _validate_pagination(
            page,
            source="minutes",
            cursor_key="next_token",
        )
        if not has_more:
            return items
        if next_token in seen_tokens:
            raise DwsError("minutes pagination repeated next token")
        seen_tokens.add(next_token)
    raise DwsError(
        f"minutes pagination exceeded {DISCOVERY_PAGE_LIMIT} pages"
    )


def _list_all_calendar_events(
    dws: MeetingProducerDws, *, start: str, end: str
) -> list[DwsCalendarEvent]:
    events: list[DwsCalendarEvent] = []
    cursor = ""
    seen_cursors: set[str] = set()
    for _ in range(DISCOVERY_PAGE_LIMIT):
        page = dws.list_calendar_events_page(
            start=start,
            end=end,
            limit=DISCOVERY_PAGE_SIZE,
            cursor=cursor,
        )
        page_events = page.get("events") or []
        events.extend(
            event for event in page_events if isinstance(event, DwsCalendarEvent)
        )
        has_more, cursor = _validate_pagination(
            page,
            source="calendar",
            cursor_key="next_cursor",
        )
        if not has_more:
            return events
        if cursor in seen_cursors:
            raise DwsError("calendar pagination repeated next cursor")
        seen_cursors.add(cursor)
    raise DwsError(
        f"calendar pagination exceeded {DISCOVERY_PAGE_LIMIT} pages"
    )


def _validate_pagination(
    page: dict[str, Any],
    *,
    source: str,
    cursor_key: str,
) -> tuple[bool, str]:
    has_more = page.get("has_more")
    if not isinstance(has_more, bool):
        raise DwsError(f"{source} pagination has_more must be boolean")
    raw_cursor = page.get(cursor_key)
    if raw_cursor is None:
        cursor = ""
    elif isinstance(raw_cursor, str):
        cursor = raw_cursor
    else:
        raise DwsError(f"{source} pagination cursor must be a string")
    if has_more and not cursor:
        raise DwsError(f"{source} pagination hasMore without next cursor")
    if not has_more and cursor:
        raise DwsError(
            f"{source} pagination terminal page has continuation cursor"
        )
    return has_more, cursor


def _source_json(
    *,
    meeting_id: str,
    metadata: dict[str, str],
    list_item: dict[str, Any],
    info: dict[str, Any],
    evidence: CalendarMeetingEvidence,
) -> str:
    return json.dumps(
        {
            "calendar_evidence": evidence.model_dump(mode="json"),
            "discovery": metadata,
            "meeting_id": meeting_id,
            "minutes_info": info,
            "minutes_list_item": list_item,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
