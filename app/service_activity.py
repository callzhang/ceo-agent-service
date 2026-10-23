"""Read service-recorded activity over caller-selected intervals; no report policy."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

from app.email_store import EmailStore
from app.store import AutoReplyStore
from app.task_semantic_models import AttentionStatus

_TEXT_LIMIT = 600
SOURCES = ("meetings", "tasks", "attention", "emails", "attempts", "waiting")


def validate_scheduled_read_window(
    start: datetime, end: datetime, scheduled_for: datetime
) -> None:
    """Keep historical reads from claiming coverage after their trigger time."""
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or scheduled_for.tzinfo is None
        or start >= end
    ):
        raise ValueError("scheduled read interval requires aware start < end")
    if end.astimezone(UTC) > scheduled_for.astimezone(UTC):
        raise ValueError("scheduled read window cannot end after scheduled_for")


def meeting_activity(
    store: AutoReplyStore, start: datetime, end: datetime
) -> list[dict]:
    from app.config import workspace_path
    from app.minutes_sync import MINUTES_ARCHIVE_DIRECTORY, archive_path_for

    entries = []
    for job in store.list_meeting_alignment_jobs_ended_between(
        start.isoformat(), end.isoformat()
    ):
        source = json.loads(job.source_json or "{}")
        basic = (source.get("minutes_info") or {}).get("result") or {}
        discovery = source.get("discovery") or {}
        path = (
            archive_path_for(
                workspace_path() / MINUTES_ARCHIVE_DIRECTORY,
                basic,
                task_uuid=job.meeting_id,
            )
            if basic
            else None
        )
        entries.append(
            {
                "meeting_id": job.meeting_id,
                "title": job.title,
                "started_at": discovery.get("started_at") or "",
                "ended_at": job.ended_at,
                "participants": json.loads(job.participants_json or "[]"),
                "source_url": basic.get("url") or "",
                "archive_path": str(path)
                if path is not None and path.is_file()
                else "",
                "follow_up_status": job.status,
                "follow_up_message": job.final_message,
            }
        )
    return entries


def scheduled_context(store: AutoReplyStore, scheduled_run_id: int) -> dict:
    run = store.get_scheduled_task_run(scheduled_run_id)
    if run is None:
        raise ValueError("scheduled run does not exist")
    history = []
    for prior in store.list_scheduled_task_runs(run.scheduled_task_id):
        task = (
            store.get_reply_task(int(prior.execution_id))
            if prior.execution_kind == "reply_task" and prior.execution_id
            else None
        )
        attempts = (
            store.list_reply_attempts_for_conversation(task.conversation_id, limit=1)
            if task
            else []
        )
        history.append(
            {
                "run_id": prior.id,
                "scheduled_for": prior.scheduled_for.isoformat(),
                "dispatch_status": prior.dispatch_status,
                "execution_kind": prior.execution_kind,
                "execution_id": prior.execution_id,
                "task_status": task.status if task else None,
                "result_status": attempts[0].send_status if attempts else None,
                "result_summary": prior.result_summary,
            }
        )
    return {
        "run_id": run.id,
        "scheduled_task_id": run.scheduled_task_id,
        "scheduled_for": run.scheduled_for.isoformat(),
        "timezone_name": run.snapshot.timezone_name,
        "task_version": run.snapshot.task_version,
        "principal_user_id": store.get_current_user_id() or "",
        "history": history,
    }


def collect_service_activity(
    store: AutoReplyStore,
    email_store: EmailStore,
    start: datetime,
    end: datetime,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("activity interval requires aware start < end")
    selected = set(SOURCES if sources is None else sources)
    if selected - set(SOURCES):
        raise ValueError("unknown activity source")

    def in_window(value: str) -> bool:
        moment = _utc(value)
        return moment is not None and start <= moment < end

    meetings = meeting_activity(store, start, end) if "meetings" in selected else []

    tasks = [
        task
        for task in (
            store.list_business_tasks_for_projection() if "tasks" in selected else []
        )
        if in_window(task.last_activity_at)
    ]
    task_facts = [
        {
            "task_id": task.id,
            "title": task.title,
            "stage": task.stage,
            "status": task.status,
            "commitment_status": task.commitment_status,
            "business_relevance": task.business_relevance,
            "owner": task.owner_name,
            "description": _clip(task.description),
            "last_activity_at": task.last_activity_at,
            "events": [
                {"event_id": event.id, "created_at": event.created_at,
                 "event_type": event.event_type, "reason": _clip(event.reason)}
                for event in store.list_business_task_events(task.id)
                if in_window(event.created_at)
            ],
        }
        for task in tasks
    ]

    attention = [
        {
            "attention_id": item.id,
            "category": item.category,
            "title": item.title,
            "business_area": item.business_area,
            "why_attention": _clip(item.why_attention),
            "current_state": _clip(item.current_state),
            "ceo_action": _clip(item.ceo_action),
            "new_in_window": in_window(item.created_at),
            "updated_at": item.updated_at,
        }
        for item in (
            store.list_business_attention_items() if "attention" in selected else []
        )
        if item.status == AttentionStatus.ACTIVE
    ]

    important_emails = [
        {
            "classification_id": mail["classification_id"],
            "action_id": mail["action_id"], "flagged_at": mail["finished_at"],
            "sender": mail["sender"],
            "subject": mail["subject"],
            "category": mail["category"],
            "preview": _clip(mail["preview"] or ""),
            "received_at": mail["received_at"],
        }
        for mail in (
            email_store.list_flagged_important_between(start, end)
            if "emails" in selected
            else []
        )
    ]

    attempts = [
        attempt
        for attempt in (
            store.list_reply_attempts_since(_sqlite_utc(start))
            if "attempts" in selected
            else []
        )
        if in_window(attempt.created_at)
    ]
    handled = [
        {
            "attempt_id": attempt.id,
            "channel": attempt.channel,
            "conversation": attempt.conversation_title,
            "conversation_id": attempt.conversation_id,
            "trigger_message_id": attempt.trigger_message_id,
            "created_at": attempt.created_at,
            "agent_run_id": attempt.agent_run_id,
            "sender": attempt.trigger_sender,
            "trigger_text": _clip(attempt.trigger_text),
            "outcome": attempt.send_status,
            "oa_action": attempt.oa_action,
            "reply": _clip(attempt.final_reply_text),
            "audit_summary": _clip(attempt.audit_summary),
        }
        for attempt in attempts
    ]

    waiting_on_derek = {
        attempt.id: attempt
        for attempt in (
            *(
                store.list_current_unresolved_problem_attempts()
                if "waiting" in selected
                else []
            ),
            *(
                store.list_open_oa_needs_human_attempts()
                if "waiting" in selected
                else []
            ),
        )
        if attempt.send_status == "needs_human"
    }

    return {
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "principal_user_id": store.get_current_user_id() or "",
        "meetings": meetings,
        "tasks": task_facts,
        "attention": attention,
        "important_emails": important_emails,
        "attempts": handled,
        "waiting": [
            {
                "attempt_id": attempt.id,
                "channel": attempt.channel,
                "conversation": attempt.conversation_title,
                "sender": attempt.trigger_sender,
                "trigger_text": _clip(attempt.trigger_text),
                "oa_url": attempt.oa_url,
                "audit_summary": _clip(attempt.audit_summary),
                "decision_options": _json(attempt.human_decision_options_json, []),
                "since": attempt.created_at,
            }
            for attempt in sorted(waiting_on_derek.values(), key=lambda item: item.id)
        ],
        "coverage": {
            "meetings": len(meetings),
            "tasks": len(task_facts),
            "attention": len(attention),
            "important_emails": len(important_emails),
            "attempts": len(handled),
            "skipped": sum(item.send_status == "skipped" for item in attempts),
            "waiting": len(waiting_on_derek),
        },
    }


def _utc(value: str) -> datetime | None:
    """Read a stored timestamp; the service writes naive values in UTC."""
    if not value or not value.strip():
        return None
    moment = datetime.fromisoformat(value.strip())
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _sqlite_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= _TEXT_LIMIT else text[:_TEXT_LIMIT] + "…"


def _json(raw: str, empty: Any) -> Any:
    return json.loads(raw) if raw and raw.strip() else empty
