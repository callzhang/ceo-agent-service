import json
import subprocess
from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import ValidationError

from app.dws_client import DwsCalendarEvent, DwsError
from app.meeting_alignment_agent import (
    MeetingAlignmentAgent,
    MeetingAlignmentTargetError,
)
from app.meeting_alignment_delivery import (
    MeetingDeliveryAmbiguous,
    MeetingDeliveryError,
    MeetingDeliveryResult,
    MeetingDeliveryRetry,
    deliver_meeting_alignment,
)
from app.meeting_alignment_models import MeetingAlignmentDecision
from app.meeting_alignment_source import (
    CalendarMeetingEvidence,
    MeetingSourceIncomplete,
    build_calendar_meeting_evidence,
    minutes_meeting_id,
    normalize_minutes_discovery_metadata,
    read_meeting_source,
)
from app.store import AutoReplyStore


DISCOVERY_PAGE_LIMIT = 100
DISCOVERY_PAGE_SIZE = 50
DEFAULT_MEETING_DISCOVERY_LOOKBACK = timedelta(days=7)
TERMINAL_STATUSES = frozenset({"no_action", "sent", "failed"})
DEFAULT_MEETING_RETRY_DELAY = timedelta(minutes=1)
DEFAULT_MEETING_MAX_ATTEMPTS = 3


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


def consume_meeting_alignment_jobs(
    store: AutoReplyStore,
    dws: Any,
    runner: Any,
    *,
    now: datetime,
    limit: int = 1,
    retry_delay: timedelta = DEFAULT_MEETING_RETRY_DELAY,
    max_attempts: int = DEFAULT_MEETING_MAX_ATTEMPTS,
) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("meeting consumer now must include a timezone")
    if limit <= 0:
        return 0
    if retry_delay.total_seconds() < 0:
        raise ValueError("meeting retry delay must not be negative")
    if max_attempts <= 0:
        raise ValueError("meeting max attempts must be positive")

    processed_ids: set[int] = set()
    jobs = store.claim_meeting_alignment_jobs(limit=limit, now=now.isoformat())
    for job in jobs:
        processed_ids.add(job.id)
        _analyze_meeting_job(
            store,
            dws,
            runner,
            job,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )

    delivery_jobs = store.claim_ready_to_send_meeting_alignment_jobs(
        limit=limit,
        now=now.isoformat(),
    )
    for job in delivery_jobs:
        processed_ids.add(job.id)
        _deliver_meeting_job(
            store,
            dws,
            job,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
    return len(processed_ids)


def recover_meeting_alignment_jobs(store: AutoReplyStore) -> int:
    recovered = [
        *store.reset_processing_meeting_alignment_jobs(),
        *store.reset_ready_to_send_meeting_alignment_jobs(),
    ]
    error = _error_json(
        "meeting_alignment_service_startup_requeue",
        "service restarted while meeting work was claimed",
    )
    for job in recovered:
        store.update_meeting_alignment_job(job.id, error=error)
    return len(recovered)


def _analyze_meeting_job(
    store: AutoReplyStore,
    dws: Any,
    runner: Any,
    job: Any,
    *,
    now: datetime,
    retry_delay: timedelta,
    max_attempts: int,
) -> None:
    try:
        payload = json.loads(job.source_json)
        evidence = CalendarMeetingEvidence.model_validate(
            payload["calendar_evidence"]
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        _fail_job(store, job.id, "meeting_source", exc)
        return

    try:
        source = read_meeting_source(
            dws,
            job.meeting_id,
            calendar_evidence=evidence,
        )
    except (MeetingSourceIncomplete, DwsError) as exc:
        _retry_or_fail(
            store,
            job,
            kind="meeting_source",
            exc=exc,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
        return
    except Exception as exc:
        _fail_job(store, job.id, "meeting_source", exc)
        return

    agent = MeetingAlignmentAgent(runner)
    try:
        decision = agent.decide(source)
    except MeetingAlignmentTargetError as exc:
        error = _error_json("meeting_target", str(exc))
        _record_agent_run(
            store,
            runner,
            job_id=job.id,
            decision=None,
            status="failed",
            error=error,
        )
        store.update_meeting_alignment_job(
            job.id, status="failed", error=error
        )
        return
    except (ValidationError, ValueError) as exc:
        error = _error_json("meeting_agent", str(exc))
        _record_agent_run(
            store,
            runner,
            job_id=job.id,
            decision=None,
            status="failed",
            error=error,
        )
        store.update_meeting_alignment_job(
            job.id, status="failed", error=error
        )
        return
    except RuntimeError as exc:
        error = _error_json("meeting_agent", str(exc))
        _record_agent_run(
            store,
            runner,
            job_id=job.id,
            decision=None,
            status="retry" if job.attempts < max_attempts else "failed",
            error=error,
        )
        _retry_or_fail(
            store,
            job,
            kind="meeting_agent",
            exc=exc,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
        return
    except Exception as exc:
        error = _error_json("meeting_agent", str(exc))
        _record_agent_run(
            store,
            runner,
            job_id=job.id,
            decision=None,
            status="failed",
            error=error,
        )
        store.update_meeting_alignment_job(
            job.id, status="failed", error=error
        )
        return

    decision_json = decision.model_dump_json()
    if decision.action == "no_action":
        _record_agent_run(
            store,
            runner,
            job_id=job.id,
            decision=decision,
            status="no_action",
            error="",
        )
        store.update_meeting_alignment_job(
            job.id,
            status="no_action",
            decision_json=decision_json,
            error="",
        )
        return

    target = decision.target
    target_id = ""
    if target is not None:
        target_id = (
            target.conversation_id
            if target.kind == "group"
            else target.direct_user_id
        )
    _record_agent_run(
        store,
        runner,
        job_id=job.id,
        decision=decision,
        status="ready_to_send",
        error="",
    )
    store.update_meeting_alignment_job(
        job.id,
        status="ready_to_send",
        decision_json=decision_json,
        target_kind=target.kind if target is not None else "",
        target_id=target_id,
        target_title=target.title if target is not None else "",
        mentions_json=json.dumps(decision.mention_names, ensure_ascii=False),
        final_message=decision.final_message,
        send_result_json="{}",
        error="",
    )


def _deliver_meeting_job(
    store: AutoReplyStore,
    dws: Any,
    job: Any,
    *,
    now: datetime,
    retry_delay: timedelta,
    max_attempts: int,
) -> None:
    try:
        previous = _saved_delivery_result(job.send_result_json)
    except (ValidationError, ValueError) as exc:
        _fail_job(store, job.id, "meeting_send_evidence", exc)
        return
    if previous is not None and previous.status == "ambiguous":
        _reconcile_ambiguous_delivery(
            store,
            dws,
            job,
            previous,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
        return

    try:
        decision = MeetingAlignmentDecision.model_validate_json(
            job.decision_json
        )
    except (ValidationError, ValueError) as exc:
        _fail_job(store, job.id, "meeting_target", exc)
        return
    try:
        source_payload = json.loads(job.source_json)
        evidence = CalendarMeetingEvidence.model_validate(
            source_payload["calendar_evidence"]
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        _fail_job(store, job.id, "meeting_source", exc)
        return
    try:
        source = read_meeting_source(
            dws,
            job.meeting_id,
            calendar_evidence=evidence,
        )
    except (MeetingSourceIncomplete, DwsError) as exc:
        _retry_or_fail(
            store,
            job,
            kind="meeting_source",
            exc=exc,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
        return
    except Exception as exc:
        _fail_job(store, job.id, "meeting_source", exc)
        return

    try:
        result = deliver_meeting_alignment(decision, source, dws)
    except MeetingDeliveryAmbiguous as exc:
        result = exc.result
        result_json = result.model_dump_json()
        if not _delivery_open_task_id(result):
            store.update_meeting_alignment_job(
                job.id,
                status="failed",
                send_result_json=result_json,
                error=_error_json(
                    "meeting_send_ambiguous_no_id",
                    "ambiguous delivery has no verifiable identifier; quarantined",
                ),
            )
            return
        _schedule_ready_reconciliation_or_fail(
            store,
            job,
            result_json=result_json,
            message=str(exc),
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
        return
    except MeetingDeliveryRetry as exc:
        values: dict[str, object] = {}
        if exc.result is not None:
            values["send_result_json"] = exc.result.model_dump_json()
        _retry_or_fail(
            store,
            job,
            kind="meeting_send",
            exc=exc,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
            extra_values=values,
        )
        return
    except MeetingDeliveryError as exc:
        _fail_job(store, job.id, "meeting_target", exc)
        return
    except (DwsError, subprocess.TimeoutExpired, TimeoutError) as exc:
        _retry_or_fail(
            store,
            job,
            kind="meeting_send",
            exc=exc,
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
        return
    except Exception as exc:
        _fail_job(store, job.id, "meeting_send", exc)
        return

    store.update_meeting_alignment_job(
        job.id,
        status="sent",
        send_result_json=result.model_dump_json(),
        error="",
    )


def _reconcile_ambiguous_delivery(
    store: AutoReplyStore,
    dws: Any,
    job: Any,
    previous: MeetingDeliveryResult,
    *,
    now: datetime,
    retry_delay: timedelta,
    max_attempts: int,
) -> None:
    open_task_id = _delivery_open_task_id(previous)
    if not open_task_id:
        store.update_meeting_alignment_job(
            job.id,
            status="failed",
            error=_error_json(
                "meeting_send_ambiguous_no_id",
                "stored ambiguous delivery has no verifiable identifier",
            ),
        )
        return
    try:
        verification = dws.verify_message_send_result(previous.send_result)
    except (DwsError, subprocess.TimeoutExpired, TimeoutError) as exc:
        _schedule_ready_reconciliation_or_fail(
            store,
            job,
            result_json=previous.model_dump_json(),
            message=str(exc),
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
        )
        return
    except Exception as exc:
        store.update_meeting_alignment_job(
            job.id,
            status="failed",
            error=_error_json("meeting_send_reconcile", str(exc)),
        )
        return

    state = verification.get("state")
    updated = previous.model_copy(
        update={
            "status": "sent" if state == "sent" else "ambiguous",
            "send_verification": verification,
        }
    )
    if state == "sent":
        store.update_meeting_alignment_job(
            job.id,
            status="sent",
            send_result_json=updated.model_dump_json(),
            error="",
        )
        return
    if state == "failed":
        # This attempt only reconciles the old operation. A counted retry may
        # safely reanalyze and send because the prior operation is confirmed
        # failed; backoff/max-attempt policy prevents a hot infinite loop.
        _retry_or_fail(
            store,
            job,
            kind="meeting_send_reconcile_failed",
            exc=MeetingDeliveryRetry("previous send was confirmed failed"),
            now=now,
            retry_delay=retry_delay,
            max_attempts=max_attempts,
            extra_values={"send_result_json": updated.model_dump_json()},
        )
        return
    _schedule_ready_reconciliation_or_fail(
        store,
        job,
        result_json=updated.model_dump_json(),
        message="previous send remains ambiguous",
        now=now,
        retry_delay=retry_delay,
        max_attempts=max_attempts,
    )


def _schedule_ready_reconciliation_or_fail(
    store: AutoReplyStore,
    job: Any,
    *,
    result_json: str,
    message: str,
    now: datetime,
    retry_delay: timedelta,
    max_attempts: int,
) -> None:
    store.update_meeting_alignment_job(
        job.id,
        send_result_json=result_json,
    )
    if job.attempts >= max_attempts:
        store.update_meeting_alignment_job(
            job.id,
            status="failed",
            error=_error_json(
                "meeting_send_reconcile_max",
                f"{message}; reconciliation attempt limit reached",
            ),
        )
        return
    store.schedule_ready_to_send_meeting_alignment_reconciliation(
        job.id,
        error=_error_json("meeting_send_reconcile", message),
        available_at=(now + retry_delay).isoformat(),
    )


def _record_agent_run(
    store: AutoReplyStore,
    runner: Any,
    *,
    job_id: int,
    decision: MeetingAlignmentDecision | None,
    status: str,
    error: str,
) -> None:
    store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id=str(getattr(runner, "last_session_id", "") or ""),
        codex_transcript_start_line=int(
            getattr(runner, "last_transcript_start_line", 0) or 0
        ),
        codex_transcript_end_line=int(
            getattr(runner, "last_transcript_end_line", 0) or 0
        ),
        decision_json=decision.model_dump_json() if decision is not None else "{}",
        audit_tool_events_json=json.dumps(
            getattr(runner, "last_audit_tool_events", []) or [],
            ensure_ascii=False,
        ),
        audit_summary=decision.audit_summary if decision is not None else str(error),
        status=status,
        error=error,
    )


def _retry_or_fail(
    store: AutoReplyStore,
    job: Any,
    *,
    kind: str,
    exc: Exception,
    now: datetime,
    retry_delay: timedelta,
    max_attempts: int,
    extra_values: dict[str, object] | None = None,
) -> None:
    error = _error_json(kind, str(exc))
    if job.attempts >= max_attempts:
        store.update_meeting_alignment_job(
            job.id,
            status="failed",
            error=error,
            **(extra_values or {}),
        )
        return
    store.update_meeting_alignment_job(
        job.id,
        status="retry",
        available_at=(now + retry_delay).isoformat(),
        error=error,
        **(extra_values or {}),
    )


def _fail_job(
    store: AutoReplyStore,
    job_id: int,
    kind: str,
    exc: Exception,
) -> None:
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        error=_error_json(kind, str(exc)),
    )


def _saved_delivery_result(raw: str) -> MeetingDeliveryResult | None:
    if not raw.strip() or raw.strip() == "{}":
        return None
    return MeetingDeliveryResult.model_validate_json(raw)


def _delivery_open_task_id(result: MeetingDeliveryResult) -> str:
    value = result.send_verification.get("open_task_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _find_nested_string(result.send_result, "openTaskId")


def _find_nested_string(payload: Any, key: str) -> str:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        for nested in payload.values():
            found = _find_nested_string(nested, key)
            if found:
                return found
    elif isinstance(payload, list):
        for nested in payload:
            found = _find_nested_string(nested, key)
            if found:
                return found
    return ""


def _error_json(kind: str, message: str) -> str:
    return json.dumps(
        {"kind": kind, "message": message},
        ensure_ascii=False,
        sort_keys=True,
    )


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
