"""Fail-closed hourly service coverage checks.

The audit UI is useful for investigation, but an hourly repair run also needs a
machine-readable answer to a narrower question: did it inspect every durable
queue, and is there an item that has stopped making progress?  This module
keeps that answer independent from the UI and intentionally treats a missing
table as a failed check rather than silently dropping a queue from coverage.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


REQUIRED_SOURCES = (
    "reply_tasks",
    "reply_attempts",
    "sent_replies",
    "agent_runs",
    "work_summary_inputs",
    "follow_up_drafts",
    "meeting_alignment_jobs",
    "okr_review_requests",
    "work_todo_dingtalk_links",
    "wechat_deliveries",
    "memory_write_events",
    "feedback_events",
    "daily_scan_state",
    "wechat_read_state",
    "errors",
)

REPLY_PROCESSING_STALE_SECONDS = 30 * 60
WORK_ITEM_PROCESSING_STALE_SECONDS = 21 * 60
PENDING_STALE_SECONDS = 15 * 60
MEETING_PROCESSING_STALE_SECONDS = 21 * 60
OKR_PROCESSING_STALE_SECONDS = 21 * 60
RECENT_ERROR_WINDOW_SECONDS = 4 * 60 * 60
RECOVERED_REPLY_ATTEMPT_STATUSES = (
    "calendar",
    "commented",
    "completed",
    "document",
    "reacted",
    "sent",
    "skipped",
)


@dataclass(frozen=True)
class QualityIssue:
    source: str
    code: str
    count: int
    severity: str
    detail: str


@dataclass(frozen=True)
class QualityGateReport:
    checked_at: str
    checked_sources: tuple[str, ...]
    missing_sources: tuple[str, ...]
    violations: tuple[QualityIssue, ...]
    attention: tuple[QualityIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_sources and not self.violations

    def to_dict(self) -> dict[str, object]:
        return {
            "checked_at": self.checked_at,
            "mode": "fail_closed_queue_coverage",
            "ok": self.ok,
            "checked_sources": list(self.checked_sources),
            "missing_sources": list(self.missing_sources),
            "violations": [asdict(item) for item in self.violations],
            "attention": [asdict(item) for item in self.attention],
        }


def scan_hourly_quality(
    db_path: Path | str,
    *,
    now: datetime | None = None,
) -> QualityGateReport:
    """Return a queue-coverage report without changing task state.

    `attention` is used for work actively progressing. `violations` are items
    that require recovery. This distinction prevents a normal fresh retry from
    being hidden, while avoiding a false "failed" gate during an active run.
    """

    checked_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_text = checked_now.isoformat()
    with sqlite3.connect(str(db_path)) as db:
        db.row_factory = sqlite3.Row
        existing = {
            str(row["name"])
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        missing = tuple(source for source in REQUIRED_SOURCES if source not in existing)
        checked = tuple(source for source in REQUIRED_SOURCES if source in existing)
        if missing:
            return QualityGateReport(
                checked_at=now_text,
                checked_sources=checked,
                missing_sources=missing,
                violations=tuple(
                    QualityIssue(
                        source=source,
                        code="source_missing",
                        count=1,
                        severity="error",
                        detail="required queue source is unavailable to the quality check",
                    )
                    for source in missing
                ),
                attention=(),
            )

        violations: list[QualityIssue] = []
        attention: list[QualityIssue] = []
        _check_reply_tasks(db, checked_now, violations, attention)
        _check_reply_attempts(db, checked_now, violations, attention)
        _check_agent_runs(db, checked_now, violations, attention)
        _check_work_items(db, checked_now, violations, attention)
        _check_follow_ups(db, checked_now, violations, attention)
        _check_meetings(db, checked_now, violations, attention)
        _check_okr_reviews(db, checked_now, violations, attention)
        _check_external_delivery_queues(db, checked_now, violations, attention)
        _check_feedback(db, violations)
        _check_scan_health(db, violations, attention)
        _check_recent_errors(db, checked_now, violations)
    return QualityGateReport(
        checked_at=now_text,
        checked_sources=checked,
        missing_sources=(),
        violations=tuple(violations),
        attention=tuple(attention),
    )


def required_live_channels(db_path: Path | str) -> frozenset[str]:
    """Return integrations needed by the service and its unfinished work.

    DingTalk and Codex are the service's always-on ingress and decision path.
    Optional integrations are probed only when an unfinished task actually
    references them, so an unused local CLI cannot make the CEO queue appear
    unhealthy.
    """
    channels = {"dingtalk", "codex"}
    with sqlite3.connect(str(db_path)) as db:
        rows = db.execute(
            """select channel, oa_url, trigger_message_json
               from reply_tasks
               where lower(status) in ('pending', 'processing', 'failed')"""
        )
        for channel, oa_url, trigger_json in rows:
            if isinstance(channel, str) and channel.strip():
                channels.add(channel.strip().casefold())
            for reference in _reference_strings((oa_url, trigger_json)):
                host = (urlsplit(reference).hostname or "").casefold()
                if _host_matches(host, ("feishu.cn", "larksuite.com", "larkoffice.com")):
                    channels.add("lark")
    return frozenset(channels)


def _reference_strings(value: object):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        if parsed is value:
            for token in value.split():
                yield token.strip("()[]{}<>\"',.;，。；：")
        else:
            yield from _reference_strings(parsed)
    elif isinstance(value, tuple | list):
        for nested in value:
            yield from _reference_strings(nested)
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _reference_strings(nested)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def write_hourly_quality_state(report: QualityGateReport, state_path: Path | str) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def add_channel_health(
    report: QualityGateReport,
    channel_states: dict[str, str],
) -> QualityGateReport:
    """Attach live channel checks without making the database scanner depend on DWS."""

    checked = (*report.checked_sources, *(f"channel:{name}" for name in sorted(channel_states)))
    violations = list(report.violations)
    for name, state in sorted(channel_states.items()):
        if state != "ready":
            violations.append(
                QualityIssue(
                    source=f"channel:{name}",
                    code="not_ready",
                    count=1,
                    severity="error",
                    detail="live channel health check did not report ready",
                )
            )
    return replace(report, checked_sources=checked, violations=tuple(violations))


def _count(db: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    row = db.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _add(
    target: list[QualityIssue],
    *,
    source: str,
    code: str,
    count: int,
    severity: str,
    detail: str,
) -> None:
    if count:
        target.append(QualityIssue(source, code, count, severity, detail))


def _cutoff(now: datetime, seconds: int) -> str:
    return (now - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _check_reply_tasks(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="reply_tasks", code="failed", count=_count(
        db, "select count(*) from reply_tasks where lower(status)='failed'"
    ), severity="error", detail="reply task has no terminal recovery")
    _add(violations, source="reply_tasks", code="processing_stale", count=_count(
        db,
        "select count(*) from reply_tasks where lower(status)='processing' and datetime(updated_at) < datetime(?)",
        (_cutoff(now, REPLY_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="reply task exceeded the worker recovery lease")
    _add(violations, source="reply_tasks", code="pending_overdue", count=_count(
        db,
        """select count(*) from reply_tasks
           where lower(status)='pending'
             and (available_at='' or datetime(available_at) <= datetime(?))
             and datetime(updated_at) < datetime(?)""",
        (now.strftime("%Y-%m-%d %H:%M:%S"), _cutoff(now, PENDING_STALE_SECONDS)),
    ), severity="error", detail="reply task was due but was not claimed")
    _add(attention, source="reply_tasks", code="active", count=_count(
        db, "select count(*) from reply_tasks where lower(status) in ('pending','processing')"
    ), severity="info", detail="reply work is currently queued or processing")


def _check_reply_attempts(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    # Only the newest attempt for a trigger can be actionable. This prevents an
    # old blocked/dry-run row from masking a later sent, skipped, or failed row.
    latest = """
        with latest as (
            select *, row_number() over (
                partition by channel, conversation_id, trigger_message_id
                order by datetime(updated_at) desc, id desc
            ) as ordinal
            from reply_attempts
        )
    """
    direct_terminal = _count(
        db,
        latest + """
            select count(*) from latest a
            where ordinal=1
              and lower(action) in ('send_reply','ask_clarifying_question')
              and lower(send_status) in ('failed','blocked')
              and not exists (
                select 1 from sent_replies sr
                where sr.conversation_id=a.conversation_id
                  and sr.trigger_message_id=a.trigger_message_id
              )
              and not exists (
                select 1 from reply_tasks t
                where t.channel=a.channel
                  and t.conversation_id=a.conversation_id
                  and t.trigger_message_id=a.trigger_message_id
                  and lower(t.status) in ('pending','processing')
              )
        """,
    )
    # Approvals do not create sent_replies. Their durable identity is the OA
    # process instance, so a later successful OA attempt resolves the earlier
    # failed or blocked record without conflating it with a reply delivery.
    oa_terminal = _count(
        db,
        """
            with oa_latest as (
                select a.*, row_number() over (
                    partition by case
                        when trim(coalesce(a.oa_process_instance_id, '')) != ''
                            then a.oa_process_instance_id
                        else 'attempt:' || a.id
                    end
                    order by datetime(a.updated_at) desc, a.id desc
                ) as ordinal
                from reply_attempts a
                where lower(a.action)='oa_approval'
            )
            select count(*) from oa_latest a
            where ordinal=1
              and lower(a.send_status) in ('failed','blocked')
              and not exists (
                select 1 from reply_attempts resolved
                where resolved.oa_process_instance_id=a.oa_process_instance_id
                  and json_valid(resolved.oa_action_result_json)
                  and coalesce(
                    json_extract(resolved.oa_action_result_json, '$.success'),
                    json_extract(resolved.oa_action_result_json, '$.result.success'),
                    json_extract(resolved.oa_action_result_json, '$.dws_action_result.success')
                  )=1
                  and upper(coalesce(
                    json_extract(resolved.oa_action_result_json, '$.taskStatus'),
                    json_extract(resolved.oa_action_result_json, '$.result.taskStatus'),
                    ''
                  ))='COMPLETED'
              )
              and not exists (
                select 1 from reply_tasks t
                where t.channel=a.channel
                  and t.conversation_id=a.conversation_id
                  and t.trigger_message_id=a.trigger_message_id
                  and lower(t.status) in ('pending','processing')
              )
        """,
    )
    terminal = direct_terminal + oa_terminal
    _add(violations, source="reply_attempts", code="unresolved_latest_attempt", count=terminal,
         severity="error", detail="latest reply attempt has no active task recovery")
    dry_run = _count(
        db,
        latest + """
            select count(*) from latest
            where ordinal=1 and lower(send_status)='dry_run'
              and datetime(updated_at) >= datetime(?)
        """,
        (_cutoff(now, 24 * 60 * 60),),
    )
    _add(violations, source="reply_attempts", code="recent_dry_run", count=dry_run,
         severity="error", detail="latest live trigger remains a dry-run result")
    recovering = _count(
        db,
        latest + """
            select count(*) from latest a
            where ordinal=1 and lower(send_status) in ('failed','blocked')
              and exists (
                select 1 from reply_tasks t
                where t.channel=a.channel
                  and t.conversation_id=a.conversation_id
                  and t.trigger_message_id=a.trigger_message_id
                  and lower(t.status) in ('pending','processing')
              )
        """,
    )
    _add(attention, source="reply_attempts", code="recovery_in_progress", count=recovering,
         severity="info", detail="a newer task is recovering the latest failed attempt")


def _check_agent_runs(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="agent_runs", code="unknown_side_effect", count=_count(
        db, "select count(*) from agent_runs where lower(status)='unknown' or lower(side_effect_state)='unknown'"
    ), severity="error", detail="agent write outcome requires reconciliation")
    _add(violations, source="agent_runs", code="running_stale", count=_count(
        db,
        "select count(*) from agent_runs where lower(status) in ('pending','running') and datetime(updated_at) < datetime(?)",
        (_cutoff(now, REPLY_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="agent run exceeded its execution lease")
    _add(attention, source="agent_runs", code="active", count=_count(
        db, "select count(*) from agent_runs where lower(status) in ('pending','running')"
    ), severity="info", detail="agent execution is in progress")


def _check_work_items(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="work_summary_inputs", code="failed", count=_count(
        db, "select count(*) from work_summary_inputs where lower(status)='failed'"
    ), severity="error", detail="work item has no terminal handling")
    _add(violations, source="work_summary_inputs", code="processing_stale", count=_count(
        db, "select count(*) from work_summary_inputs where lower(status)='processing' and datetime(updated_at) < datetime(?)",
        (_cutoff(now, WORK_ITEM_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="work item exceeded the task agent timeout")
    _add(attention, source="work_summary_inputs", code="active", count=_count(
        db, "select count(*) from work_summary_inputs where lower(status) in ('pending','processing')"
    ), severity="info", detail="work item is queued or processing")


def _check_follow_ups(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="follow_up_drafts", code="failed", count=_count(
        db, "select count(*) from follow_up_drafts where lower(status)='failed'"
    ), severity="error", detail="follow-up send reached an unrecovered failure")
    _add(violations, source="follow_up_drafts", code="scheduled_overdue", count=_count(
        db,
        """select count(*) from follow_up_drafts
           where lower(status) in ('draft','approved') and scheduled_at != ''
             and datetime(scheduled_at) < datetime(?)""",
        (_cutoff(now, PENDING_STALE_SECONDS),),
    ), severity="error", detail="scheduled follow-up is overdue")
    _add(attention, source="follow_up_drafts", code="future_scheduled", count=_count(
        db,
        "select count(*) from follow_up_drafts where lower(status) in ('draft','approved') and scheduled_at != '' and datetime(scheduled_at) >= datetime(?)",
        (now.strftime("%Y-%m-%d %H:%M:%S"),),
    ), severity="info", detail="future follow-up is intentionally scheduled")


def _check_meetings(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="meeting_alignment_jobs", code="failed", count=_count(
        db, "select count(*) from meeting_alignment_jobs where lower(status)='failed'"
    ), severity="error", detail="meeting delivery has no terminal recovery")
    _add(violations, source="meeting_alignment_jobs", code="active_stale", count=_count(
        db,
        "select count(*) from meeting_alignment_jobs where lower(status) in ('pending','processing','ready_to_send','retry') and datetime(updated_at) < datetime(?)",
        (_cutoff(now, MEETING_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="meeting alignment job stopped progressing")
    _add(attention, source="meeting_alignment_jobs", code="active", count=_count(
        db, "select count(*) from meeting_alignment_jobs where lower(status) in ('waiting','pending','processing','ready_to_send','retry')"
    ), severity="info", detail="meeting alignment work is pending")
    _add(attention, source="meeting_alignment_jobs", code="quarantined", count=_count(
        db, "select count(*) from meeting_alignment_jobs where lower(status)='quarantined'"
    ), severity="info", detail="meeting delivery outcome cannot be verified")


def _check_okr_reviews(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="okr_review_requests", code="failed", count=_count(
        db, "select count(*) from okr_review_requests where lower(status)='failed'"
    ), severity="error", detail="OKR review request has no terminal handling")
    _add(violations, source="okr_review_requests", code="processing_stale", count=_count(
        db, "select count(*) from okr_review_requests where lower(status)='processing' and datetime(updated_at) < datetime(?)",
        (_cutoff(now, OKR_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="OKR review exceeded the Codex timeout")
    _add(attention, source="okr_review_requests", code="active", count=_count(
        db, "select count(*) from okr_review_requests where lower(status) in ('pending','processing')"
    ), severity="info", detail="OKR review work is pending")
    _add(attention, source="okr_review_requests", code="discarded", count=_count(
        db, "select count(*) from okr_review_requests where lower(status)='discarded'"
    ), severity="info", detail="OKR request is outside the principal's review scope")


def _check_external_delivery_queues(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    for source, status_column, failed_statuses, active_statuses, resolved_filter in (
        ("work_todo_dingtalk_links", "status", ("failed",), ("creating", "active"), ""),
        # A rejected delivery is a deliberate user decision. Store already maps
        # its reply attempt to skipped, so the external queue must do the same.
        ("wechat_deliveries", "status", ("failed", "send_unknown"),
         ("pending", "sending", "ready_to_send"),
         "and lower(coalesce(error, '')) != 'user_rejected'"),
        ("memory_write_events", "status", ("failed",), ("pending", "processing"), ""),
    ):
        if source == "work_todo_dingtalk_links":
            failed = _count(
                db,
                """select count(*)
                   from work_todo_dingtalk_links failed_link
                   where lower(failed_link.status)='failed'
                     and not exists (
                        select 1
                        from work_todo_dingtalk_links recovered_link
                        where recovered_link.work_todo_id=failed_link.work_todo_id
                          and recovered_link.id > failed_link.id
                          and lower(recovered_link.status) in ('creating','active','done')
                     )""",
            )
        else:
            failed = _count(
                db,
                f"""select count(*) from {source}
                    where lower({status_column}) in ({','.join('?' for _ in failed_statuses)})
                    {resolved_filter}""",
                failed_statuses,
            )
        _add(violations, source=source, code="failed", count=failed,
             severity="error", detail="external delivery queue has an unrecovered failure")
        active = _count(
            db,
            f"select count(*) from {source} where lower({status_column}) in ({','.join('?' for _ in active_statuses)})",
            active_statuses,
        )
        _add(attention, source=source, code="active", count=active,
             severity="info", detail="external delivery work is active")


def _check_feedback(
    db: sqlite3.Connection,
    violations: list[QualityIssue],
) -> None:
    _add(violations, source="feedback_events", code="unresolved", count=_count(
        db, "select count(*) from feedback_events where resolved_at='' or resolved_at is null"
    ), severity="error", detail="user feedback has not reached a recorded resolution")


def _check_scan_health(
    db: sqlite3.Connection,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="daily_scan_state", code="last_error", count=_count(
        db, "select count(*) from daily_scan_state where trim(last_error) != ''"
    ), severity="error", detail="source scanner reports an unresolved error")
    _add(attention, source="daily_scan_state", code="oa_detail_read", count=_count(
        db,
        """select count(*) from daily_scan_state
           where scanner_name='oa_pending'
             and json_array_length(
                 coalesce(json_extract(cursor_json, '$.read_failure_process_instance_ids'), '[]')
             ) > 0""",
    ), severity="info", detail="some OA approval details need a later read")
    # A disabled reader only blocks quality when it has work to process. This
    # avoids treating an intentionally unconfigured channel as an outage.
    blocked_reader = _count(
        db,
        "select count(*) from wechat_read_state where lower(capability_status) not in ('ready','')",
    )
    active_wechat = _count(
        db, "select count(*) from reply_tasks where lower(channel)='wechat' and lower(status) in ('pending','processing')"
    )
    if blocked_reader and active_wechat:
        _add(violations, source="wechat_read_state", code="reader_blocked_with_work", count=blocked_reader,
             severity="error", detail="WeChat reader is unavailable while reply work is queued")
    elif blocked_reader:
        _add(attention, source="wechat_read_state", code="reader_not_ready", count=blocked_reader,
             severity="info", detail="WeChat reader is not ready but has no queued reply work")


def _check_recent_errors(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
) -> None:
    _add(violations, source="errors", code="recent_error", count=_count(
        db,
        """select count(*)
           from errors error_event
           where datetime(error_event.created_at) >= datetime(?)
             and coalesce(error_event.resolved_at, '')=''
             and not exists (
                select 1
                from reply_attempts recovery
                where recovery.conversation_id=error_event.conversation_id
                  and recovery.trigger_message_id=error_event.message_id
                  and datetime(recovery.updated_at) >= datetime(error_event.created_at)
                  and lower(recovery.send_status) in ({})
             )""".format(
            ",".join("?" for _ in RECOVERED_REPLY_ATTEMPT_STATUSES)
        ),
        (
            _cutoff(now, RECENT_ERROR_WINDOW_SECONDS),
            *RECOVERED_REPLY_ATTEMPT_STATUSES,
        ),
    ), severity="error", detail="a service error was recorded within the four-hour repair window")
