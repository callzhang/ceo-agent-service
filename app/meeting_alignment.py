import json
import subprocess
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from pydantic import ValidationError

from app.config import principal_display_name
from app.dws_client import DwsCalendarEvent, DwsError, DwsUserProfile
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
    meeting_delivery_conversation_id,
)
from app.meeting_alignment_models import (
    MeetingAlignmentDecision,
    MeetingParticipant,
)
from app.meeting_alignment_source import (
    CalendarMeetingEvidence,
    MeetingSourceIncomplete,
    build_calendar_meeting_evidence,
    build_transcript_one_to_one_evidence,
    minutes_meeting_id,
    normalize_minutes_discovery_metadata,
    read_meeting_source,
    transcript_speaker_names,
)
from app.notification import (
    dingtalk_conversation_notification_url,
    send_macos_notification,
)
from app.store import AutoReplyStore


DISCOVERY_PAGE_LIMIT = 100
DISCOVERY_PAGE_SIZE = 50
REPLAY_PAGE_SIZE_LIMIT = 100
DEFAULT_MEETING_DISCOVERY_LOOKBACK = timedelta(days=7)
MINIMUM_MEETING_DURATION = timedelta(minutes=5)
TERMINAL_STATUSES = frozenset({"no_action", "sent", "failed"})
DEFAULT_MEETING_RETRY_DELAY = timedelta(minutes=1)
DEFAULT_MEETING_MAX_ATTEMPTS = 3
MEETING_DISCOVERY_ACTIVATED_AT_STATE_KEY = (
    "meeting_alignment_discovery_activated_at"
)


class MeetingProducerDws(Protocol):
    def list_minutes_page(
        self, *, limit: int, cursor: str, start: str, end: str
    ) -> dict[str, Any]: ...

    def get_minutes_info(self, meeting_id: str) -> dict[str, Any]: ...

    def get_current_user_id(self) -> str: ...

    def get_all_minutes_transcription(self, meeting_id: str) -> dict[str, Any]: ...

    def search_user_profiles(self, query: str) -> list[DwsUserProfile]: ...

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

    activated_at: datetime | None = None
    activated_at_text = store.get_service_state(
        MEETING_DISCOVERY_ACTIVATED_AT_STATE_KEY
    )
    if activated_at_text:
        try:
            activated_at = datetime.fromisoformat(activated_at_text)
        except ValueError as exc:
            raise DwsError("meeting discovery activation watermark is invalid") from exc
        if activated_at.tzinfo is None or activated_at.utcoffset() is None:
            raise DwsError("meeting discovery activation watermark must include timezone")

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
        if ended_at - started_at < MINIMUM_MEETING_DURATION:
            continue
        if activated_at is not None and ended_at < activated_at:
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
            evidence = _build_meeting_roster_evidence(
                dws,
                matcher_info,
                events,
                current_user_id=current_user_id,
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


def queue_recent_meeting_alignment_replay(
    store: AutoReplyStore,
    dws: MeetingProducerDws,
    *,
    now: datetime,
    limit: int,
    offset: int = 0,
    settle_seconds: int = 600,
    discovery_lookback: timedelta = DEFAULT_MEETING_DISCOVERY_LOOKBACK,
) -> list[dict[str, Any]]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("meeting replay now must include a timezone")
    if limit <= 0:
        raise ValueError("meeting replay limit must be positive")
    if offset < 0:
        raise ValueError("meeting replay offset must not be negative")
    fetch_limit = limit + offset
    if fetch_limit > REPLAY_PAGE_SIZE_LIMIT:
        raise ValueError(
            f"meeting replay window must not exceed {REPLAY_PAGE_SIZE_LIMIT}"
        )
    if settle_seconds < 0:
        raise ValueError("settle_seconds must not be negative")
    if discovery_lookback.total_seconds() <= 0:
        raise ValueError("meeting discovery lookback must be positive")

    current_user_id = dws.get_current_user_id().strip()
    if not current_user_id:
        raise DwsError("meeting producer current user id is missing")
    page = dws.list_minutes_page(
        limit=fetch_limit,
        cursor="",
        start=(now - discovery_lookback).isoformat(),
        end=now.isoformat(),
    )
    raw_items = page.get("items")
    if not isinstance(raw_items, list):
        raise DwsError("meeting replay minutes page items must be a list")

    results: list[dict[str, Any]] = []
    for list_item in raw_items[offset:fetch_limit]:
        if not isinstance(list_item, dict):
            results.append(
                {
                    "meeting_id": "",
                    "title": "",
                    "duration_seconds": None,
                    "outcome": "source_incomplete",
                    "job_id": None,
                    "error": "minutes list item is not an object",
                }
            )
            continue
        result: dict[str, Any] = {
            "meeting_id": "",
            "title": str(list_item.get("title") or ""),
            "duration_seconds": None,
            "outcome": "source_incomplete",
            "job_id": None,
            "error": "",
        }
        try:
            meeting_id = minutes_meeting_id(list_item)
            result["meeting_id"] = meeting_id
            info = dws.get_minutes_info(meeting_id)
            metadata = normalize_minutes_discovery_metadata(list_item, info)
            started_at = datetime.fromisoformat(metadata.started_at)
            ended_at = datetime.fromisoformat(metadata.ended_at)
        except (MeetingSourceIncomplete, ValueError, DwsError) as exc:
            result["error"] = str(exc)
            results.append(result)
            continue

        result["title"] = metadata.title
        duration = ended_at - started_at
        result["duration_seconds"] = duration.total_seconds()
        if started_at >= ended_at:
            results.append(result)
            continue
        if duration < MINIMUM_MEETING_DURATION:
            result["outcome"] = "short_recording"
            results.append(result)
            continue
        if metadata.status and metadata.status != "ended":
            results.append(result)
            continue

        existing = store.get_meeting_alignment_job_by_meeting_id(meeting_id)
        if existing is not None:
            result["job_id"] = existing.id
            if existing.status == "sent" or existing.send_result_json != "{}":
                result["outcome"] = "already_sent"
                results.append(result)
                continue
            if existing.status != "no_action":
                result["outcome"] = existing.status
                results.append(result)
                continue

        try:
            events = _list_all_calendar_events(
                dws,
                start=(started_at - timedelta(hours=4)).isoformat(),
                end=(ended_at + timedelta(hours=4)).isoformat(),
            )
        except DwsError as exc:
            result["outcome"] = "failed"
            result["error"] = str(exc)
            results.append(result)
            continue
        matcher_info = {
            "taskUuid": meeting_id,
            "title": metadata.title,
            "startTimeISO": metadata.started_at,
            "endTimeISO": metadata.ended_at,
        }
        try:
            evidence = _build_meeting_roster_evidence(
                dws,
                matcher_info,
                events,
                current_user_id=current_user_id,
            )
        except MeetingSourceIncomplete:
            result["outcome"] = "calendar_not_unique"
            results.append(result)
            continue
        if sum(
            participant.user_id == current_user_id
            for participant in evidence.participants
        ) != 1:
            result["outcome"] = "derek_not_attendee"
            results.append(result)
            continue

        eligible_at = ended_at + timedelta(seconds=settle_seconds)
        source_json = _source_json(
            meeting_id=meeting_id,
            metadata=metadata.model_dump(mode="json"),
            list_item=list_item,
            info=info,
            evidence=evidence,
        )
        participants_json = json.dumps(
            [
                participant.model_dump(mode="json")
                for participant in evidence.participants
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        if existing is not None and existing.status == "no_action":
            reopened = store.reopen_meeting_alignment_job_for_replay(
                existing.id,
                title=metadata.title,
                source_json=source_json,
                participants_json=participants_json,
                ended_at=ended_at.isoformat(),
                eligible_at=eligible_at.isoformat(),
            )
            if reopened is None:
                refreshed = store.get_meeting_alignment_job(existing.id)
                result["outcome"] = refreshed.status
                results.append(result)
                continue
            job_id = reopened.id
        else:
            job_id = store.upsert_meeting_alignment_job(
                meeting_id=meeting_id,
                title=metadata.title,
                source_json=source_json,
                participants_json=participants_json,
                ended_at=ended_at.isoformat(),
                eligible_at=eligible_at.isoformat(),
                status="pending",
            )
        result["job_id"] = job_id
        result["outcome"] = "queued"
        results.append(result)
    return results


def consume_meeting_alignment_jobs(
    store: AutoReplyStore,
    dws: Any,
    runner: Any,
    *,
    now: datetime,
    limit: int = 1,
    retry_delay: timedelta = DEFAULT_MEETING_RETRY_DELAY,
    max_attempts: int = DEFAULT_MEETING_MAX_ATTEMPTS,
    deliver: bool = True,
    embedding_client: Callable[[list[str]], list[list[float]]] | None = None,
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
            embedding_client=embedding_client,
        )

    delivery_jobs = (
        store.claim_ready_to_send_meeting_alignment_jobs(
            limit=limit,
            now=now.isoformat(),
        )
        if deliver
        else []
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


def _search_similar_meeting_sessions(
    store: AutoReplyStore,
    source: Any,
    *,
    embedding_client: Callable[[list[str]], list[list[float]]] | None,
):
    query_text = _meeting_session_search_text(source)
    query_embedding = None
    if embedding_client is not None:
        try:
            vectors = embedding_client([query_text])
            query_embedding = vectors[0] if vectors else None
        except Exception:
            query_embedding = None
    return store.search_codex_sessions(
        fts_query=_meeting_fts_query(query_text),
        query_embedding=query_embedding,
        limit=3,
    )


def _meeting_session_search_text(source: Any) -> str:
    participants = " ".join(
        participant.name for participant in source.participants if participant.name
    )
    transcript = " ".join(line.text for line in source.transcript[:40] if line.text)
    return "\n".join(
        part
        for part in (
            source.title,
            source.summary,
            participants,
            transcript,
        )
        if str(part).strip()
    )


def _meeting_fts_query(text: str) -> str:
    import jieba

    terms = []
    seen = set()
    for token in jieba.lcut(text):
        value = str(token).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        terms.append(value)
        if len(terms) >= 12:
            break
    return " OR ".join(terms)


def _meeting_fts_text(text: str) -> str:
    import jieba

    tokens = [str(token).strip() for token in jieba.lcut(text)]
    return " ".join(token for token in tokens if token)


def _index_meeting_codex_session(
    store: AutoReplyStore,
    runner: Any,
    job: Any,
    source: Any,
    decision: MeetingAlignmentDecision,
    *,
    source_id: int,
    embedding_client: Callable[[list[str]], list[list[float]]] | None,
) -> None:
    session_id = str(getattr(runner, "last_session_id", "") or "").strip()
    if not session_id:
        return
    summary_text = _meeting_session_index_text(source, decision)
    embedding = None
    if embedding_client is not None:
        try:
            vectors = embedding_client([summary_text])
            embedding = vectors[0] if vectors else None
        except Exception:
            embedding = None
    store.upsert_codex_session_search_index(
        session_id=session_id,
        source_type="meeting_alignment",
        source_id=str(source_id),
        title=source.title,
        summary_text=summary_text,
        fts_text=_meeting_fts_text(summary_text),
        embedding=embedding,
    )


def _meeting_session_index_text(
    source: Any,
    decision: MeetingAlignmentDecision,
) -> str:
    topics = "；".join(
        f"{topic.title}（{topic.state}）："
        f"{' / '.join(f'{view.speaker}:{view.view}' for view in topic.views)}"
        for topic in decision.topics
    )
    questions = "；".join(question.question for question in decision.key_questions)
    derek_view = (
        decision.derek_viewpoint.expressed_view
        if decision.derek_viewpoint is not None
        else ""
    )
    participants = "、".join(
        participant.name for participant in source.participants if participant.name
    )
    return "\n".join(
        part
        for part in (
            f"会议：{source.title}",
            f"参会人：{participants}",
            f"摘要：{source.summary}",
            f"话题：{topics}",
            f"Derek 观点：{derek_view}",
            f"关键问题：{questions}",
            f"结论/消息：{decision.final_message}",
            f"审计摘要：{decision.audit_summary}",
        )
        if part.strip() and not part.endswith("：")
    )


def _analyze_meeting_job(
    store: AutoReplyStore,
    dws: Any,
    runner: Any,
    job: Any,
    *,
    now: datetime,
    retry_delay: timedelta,
    max_attempts: int,
    embedding_client: Callable[[list[str]], list[list[float]]] | None = None,
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

    similar_sessions = _search_similar_meeting_sessions(
        store,
        source,
        embedding_client=embedding_client,
    )
    agent = MeetingAlignmentAgent(runner)
    try:
        decision = agent.decide(source, similar_sessions=similar_sessions)
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
        run_id = _record_agent_run(
            store,
            runner,
            job_id=job.id,
            decision=decision,
            status="no_action",
            error="",
        )
        _index_meeting_codex_session(
            store,
            runner,
            job,
            source,
            decision,
            source_id=run_id,
            embedding_client=embedding_client,
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
    run_id = _record_agent_run(
        store,
        runner,
        job_id=job.id,
        decision=decision,
        status="ready_to_send",
        error="",
    )
    _index_meeting_codex_session(
        store,
        runner,
        job,
        source,
        decision,
        source_id=run_id,
        embedding_client=embedding_client,
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
        final_message=result.message_text or job.final_message,
        send_result_json=result.model_dump_json(),
        error="",
    )
    _notify_meeting_sent(job, result)


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
            final_message=updated.message_text or job.final_message,
            send_result_json=updated.model_dump_json(),
            error="",
        )
        _notify_meeting_sent(job, updated)
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
) -> int:
    return store.record_meeting_alignment_run(
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


def _build_meeting_roster_evidence(
    dws: MeetingProducerDws,
    info: dict[str, Any],
    events: list[DwsCalendarEvent],
    *,
    current_user_id: str,
) -> CalendarMeetingEvidence:
    try:
        return build_calendar_meeting_evidence(info, events, current_user_id)
    except MeetingSourceIncomplete as calendar_error:
        try:
            transcription = dws.get_all_minutes_transcription(
                minutes_meeting_id(info)
            )
            current_user_name = principal_display_name().strip()
            speaker_names = transcript_speaker_names(transcription)
            current_key = _canonical_name(current_user_name)
            other_names = [
                name
                for name in speaker_names
                if _canonical_name(name) != current_key
            ]
            if (
                not current_key
                or sum(
                    _canonical_name(name) == current_key
                    for name in speaker_names
                )
                != 1
                or len(speaker_names) != 2
                or len(other_names) != 1
            ):
                raise MeetingSourceIncomplete(
                    "transcript does not prove an ad-hoc one-to-one meeting"
                )
            counterpart = _resolve_transcript_counterpart(
                dws,
                other_names[0],
            )
            return build_transcript_one_to_one_evidence(
                info,
                transcription,
                current_user_id=current_user_id,
                current_user_name=current_user_name,
                counterpart=counterpart,
            )
        except (DwsError, MeetingSourceIncomplete):
            raise calendar_error


def _resolve_transcript_counterpart(
    dws: MeetingProducerDws,
    name: str,
) -> MeetingParticipant:
    wanted = _canonical_name(name)
    matches = [
        profile
        for profile in dws.search_user_profiles(name)
        if wanted
        in {
            _canonical_name(profile.name),
            _canonical_name(profile.nick),
        }
    ]
    if len(matches) != 1:
        raise MeetingSourceIncomplete(
            "transcript one-to-one counterpart identity is not unique"
        )
    profile = matches[0]
    if not profile.user_id.strip():
        raise MeetingSourceIncomplete(
            "transcript one-to-one counterpart user id is missing"
        )
    return MeetingParticipant(
        name=name.strip(),
        user_id=profile.user_id.strip(),
        open_dingtalk_id=(profile.open_dingtalk_id or "").strip(),
    )


def _canonical_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _notify_meeting_sent(job: Any, result: MeetingDeliveryResult) -> None:
    conversation_id = meeting_delivery_conversation_id(result)
    send_macos_notification(
        title=f"CEO meeting follow-up: {job.title}",
        message=result.message_text or job.final_message,
        url=dingtalk_conversation_notification_url(conversation_id),
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
            return items
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
            return events
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
