"""Collect one Beijing day of service-recorded facts for the CEO daily report.

The daily report is an Agent task; this module is the deterministic half of
it. It reads what the service itself already recorded for the day and hands
the Agent one JSON document, so the report never waits on Derek to supply its
inputs. It only reads: nothing here changes a record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from typing import Any
from zoneinfo import ZoneInfo

from app.email_store import EmailStore
from app.store import AutoReplyStore
from app.task_semantic_models import AttentionStatus


REPORT_TIME_ZONE = ZoneInfo("Asia/Shanghai")
_TEXT_LIMIT = 600


FIRST_REPORT_SPAN = timedelta(hours=24)


@dataclass(frozen=True)
class ReportWindow:
    report_date: date
    start: datetime
    end: datetime


def report_window_for_run(store: AutoReplyStore, scheduled_run_id: int) -> ReportWindow:
    """Cover everything since the last report that was delivered.

    The window ends at this trigger. It starts where the latest successful run
    of the same task with an earlier report date ended, so days the report did
    not go out are folded into the next one, and a same-day rerun keeps the
    original start. A task that never succeeded looks back one day.
    """
    run = store.get_scheduled_task_run(scheduled_run_id)
    if run is None:
        raise ValueError(f"scheduled task run {scheduled_run_id} does not exist")
    end = run.scheduled_for.astimezone(UTC)
    report_date = _report_date(end)
    delivered = [
        earlier.scheduled_for.astimezone(UTC)
        for earlier in store.list_scheduled_task_runs(run.scheduled_task_id)
        if earlier.id != run.id
        and _report_date(earlier.scheduled_for) < report_date
        and _delivered(store, earlier)
    ]
    start = max(delivered) if delivered else end - FIRST_REPORT_SPAN
    return ReportWindow(report_date=report_date, start=start, end=end)


def collect_daily_report_facts(
    store: AutoReplyStore, email_store: EmailStore, window: ReportWindow
) -> dict[str, Any]:
    start, end = window.start, window.end

    def in_window(value: str) -> bool:
        moment = _utc(value)
        return moment is not None and start <= moment < end

    meetings = [
        {
            "meeting_id": job.meeting_id,
            "title": job.title,
            "ended_at": job.ended_at,
            "participants": _json(job.participants_json, []),
            "delivered_to": job.target_title,
            "follow_up_message": job.final_message,
        }
        for job in store.list_sent_meeting_alignment_jobs()
        if in_window(job.ended_at)
    ]

    tasks = [
        task
        for task in store.list_business_tasks_for_projection()
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
            "events_today": [
                {"event_type": event.event_type, "reason": _clip(event.reason)}
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
            "new_today": in_window(item.created_at),
            "updated_at": item.updated_at,
        }
        for item in store.list_business_attention_items()
        if item.status == AttentionStatus.ACTIVE
    ]

    important_emails = [
        {
            "sender": mail["sender"],
            "subject": mail["subject"],
            "category": mail["category"],
            "preview": _clip(mail["preview"] or ""),
            "received_at": mail["received_at"],
        }
        for mail in email_store.list_flagged_important_between(start, end)
    ]

    attempts = [
        attempt
        for attempt in store.list_reply_attempts_since(_sqlite_utc(start))
        if in_window(attempt.created_at)
    ]
    handled = [
        {
            "attempt_id": attempt.id,
            "channel": attempt.channel,
            "conversation": attempt.conversation_title,
            "sender": attempt.trigger_sender,
            "trigger_text": _clip(attempt.trigger_text),
            "outcome": attempt.send_status,
            "oa_action": attempt.oa_action,
            "reply": _clip(attempt.final_reply_text),
            "audit_summary": _clip(attempt.audit_summary),
        }
        for attempt in attempts
        if attempt.send_status != "skipped"
    ]

    waiting_on_derek = {
        attempt.id: attempt
        for attempt in (
            *store.list_current_unresolved_problem_attempts(),
            *store.list_open_oa_needs_human_attempts(),
        )
        if attempt.send_status == "needs_human"
    }

    return {
        "report_date": window.report_date.isoformat(),
        "time_zone": str(REPORT_TIME_ZONE),
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "principal_user_id": store.get_current_user_id() or "",
        "meetings": meetings,
        "tasks_active_today": task_facts,
        "business_attention": attention,
        "important_emails": important_emails,
        "handled_today": handled,
        "waiting_on_derek": [
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
            "tasks_active_today": len(task_facts),
            "business_attention": len(attention),
            "important_emails": len(important_emails),
            "handled_today": len(handled),
            "skipped_today": len(attempts) - len(handled),
            "waiting_on_derek": len(waiting_on_derek),
        },
    }


def _report_date(moment: datetime) -> date:
    return moment.astimezone(REPORT_TIME_ZONE).date()


def _delivered(store: AutoReplyStore, run) -> bool:
    if run.execution_kind != "reply_task" or not run.execution_id:
        return False
    task = store.get_reply_task(int(run.execution_id))
    return task is not None and task.status == "done"


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
