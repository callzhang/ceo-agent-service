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

from app.agent_cron.scheduler import SCHEDULED_CAPABILITY_UNAVAILABLE_KINDS
from app.decision_quality import (
    StoredNeedsHumanProjection,
    classify_stored_needs_human_projection,
)
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.store import SERVICE_HEALTH_STATE_PREFIX


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
OPTIONAL_QUEUE_SOURCES = (
    "email_agent_classification_tasks",
    "email_classifier_runtime_samples",
    "task_todo_sync_outbox",
)

REPLY_PROCESSING_STALE_SECONDS = 30 * 60
WORK_ITEM_PROCESSING_STALE_SECONDS = 21 * 60
PENDING_STALE_SECONDS = 15 * 60
MEETING_PROCESSING_STALE_SECONDS = 21 * 60
OKR_PROCESSING_STALE_SECONDS = 21 * 60
RECENT_ERROR_WINDOW_SECONDS = 4 * 60 * 60
MODEL_RUNTIME_WINDOW_SECONDS = 24 * 60 * 60
# Below this share of decisions actually taken, the model is live in name
# only and the Agent is doing the work it was promoted to take over.
MODEL_DECIDED_SHARE_FLOOR = 0.5
AGENT_CRON_SCHEDULER_STALE_SECONDS = 5 * 60
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
        checked = (
            *(source for source in REQUIRED_SOURCES if source in existing),
            *(source for source in OPTIONAL_QUEUE_SOURCES if source in existing),
        )
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
        capacity_paused = _has_active_codex_capacity_pause(db, checked_now)
        _check_reply_tasks(
            db,
            checked_now,
            violations,
            attention,
            capacity_paused=capacity_paused,
        )
        _check_reply_attempts(db, checked_now, violations, attention)
        _check_agent_runs(db, checked_now, violations, attention)
        _check_work_items(db, checked_now, violations, attention)
        if "email_agent_classification_tasks" in existing:
            _check_email_classification_tasks(db, checked_now, violations)
        if "email_classifier_runtime_samples" in existing:
            _check_email_model_runtime(db, checked_now, violations, attention)
        _check_email_model_left_active(Path(db_path), violations)
        # Legacy follow_up_drafts are history only (Derek 2026-09-25: a
        # follow-up is sent only when he clicks it), so an unsent or failed
        # one is not a delivery backlog and is not checked here.
        _check_meetings(db, checked_now, violations, attention)
        _check_okr_reviews(db, checked_now, violations, attention)
        _check_external_delivery_queues(db, checked_now, violations, attention)
        _check_delivery_records(db, violations)
        _check_feedback(db, violations)
        _check_scan_health(db, violations, attention)
        _check_scheduler_health(db, checked_now, violations)
        _check_codex_capacity_pause(db, checked_now, attention)
        _check_runtime_route_pauses(db, checked_now, attention)
        _check_recent_errors(db, checked_now, violations, attention)
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
    *,
    capacity_paused: bool = False,
) -> None:
    # Email unsubscribe tasks are consumed by the dedicated email worker. The
    # generic reply dispatcher only owns these channels, so do not report the
    # email backlog as an unclaimed reply task.
    reply_channels = "channel in ('dingtalk','wechat','scheduled')"
    _add(violations, source="reply_tasks", code="failed", count=_count(
        db,
        f"""select count(*) from reply_tasks
           where {reply_channels}
             and lower(status)='failed' and trim(coalesce(error, ''))=''""",
    ), severity="error", detail="reply task has no concrete terminal failure")
    _add(violations, source="reply_tasks", code="processing_stale", count=_count(
        db,
        f"select count(*) from reply_tasks where {reply_channels} and lower(status)='processing' and datetime(updated_at) < datetime(?)",
        (_cutoff(now, REPLY_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="reply task exceeded the worker recovery lease")
    pending_overdue = _count(
        db,
        f"""select count(*) from reply_tasks
           where {reply_channels}
             and lower(status)='pending'
             and (available_at='' or datetime(available_at) <= datetime(?))
             and datetime(updated_at) < datetime(?)""",
        (now.strftime("%Y-%m-%d %H:%M:%S"), _cutoff(now, PENDING_STALE_SECONDS)),
    )
    _add(
        attention if capacity_paused else violations,
        source="reply_tasks",
        code="capacity_paused" if capacity_paused else "pending_overdue",
        count=pending_overdue,
        severity="info" if capacity_paused else "error",
        detail=(
            "reply work is intentionally held by the shared Codex capacity pause"
            if capacity_paused
            else "reply task was due but was not claimed"
        ),
    )
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
            select count(*) from latest a
            where ordinal=1 and lower(send_status)='dry_run'
              and datetime(updated_at) >= datetime(?)
              and not exists (
                select 1 from reply_tasks t
                where t.channel=a.channel
                  and t.conversation_id=a.conversation_id
                  and t.trigger_message_id=a.trigger_message_id
                  and lower(t.status)='done'
              )
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
    _check_structured_needs_human(db, latest, violations, attention)


def _check_structured_needs_human(
    db: sqlite3.Connection,
    latest: str,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    """Gate Attention on the latest business projection's structured result.

    A status string is only a projection. The quality fields and executable
    options must come from the run referenced by that current projection; old
    attempts and failed/technical projections must not become human work.
    """
    rows = db.execute(
        latest
        + """
            select a.send_status, a.reviewed_at, a.agent_run_id,
                   a.send_error, a.human_decision_options_json,
                   r.final_result_json
            from latest a
            left join agent_runs r on r.id=a.agent_run_id
            where a.ordinal=1 and lower(a.send_status)='needs_human'
              and a.reviewed_at is null
              and trim(coalesce(a.resolved_at, ''))=''
              and not exists (
                  select 1
                  from reply_tasks as historical_task
                  join business_object_tasks as current_business_object
                    on current_business_object.business_object_key=
                       historical_task.business_object_key
                  where historical_task.channel=a.channel
                    and historical_task.conversation_id=a.conversation_id
                    and historical_task.trigger_message_id=a.trigger_message_id
                    and current_business_object.reply_task_id<>historical_task.id
              )
              and not exists (
                  select 1 from reply_tasks t
                  where t.channel=a.channel
                    and t.conversation_id=a.conversation_id
                    and t.trigger_message_id=a.trigger_message_id
                    and lower(t.status) in ('pending', 'processing')
              )
        """
    ).fetchall()
    actionable = 0
    invalid = 0
    for row in rows:
        classification = classify_stored_needs_human_projection(
            row["final_result_json"]
        )
        if classification is StoredNeedsHumanProjection.NEEDS_HUMAN:
            actionable += 1
        else:
            invalid += 1
    _add(
        attention,
        source="reply_attempts",
        code="needs_human",
        count=actionable,
        severity="info",
        detail=(
            "latest trigger requires a concrete Derek decision supported by "
            "structured risk, confidence, coverage, and executable options"
        ),
    )
    _add(
        violations,
        source="reply_attempts",
        code="invalid_needs_human_result",
        count=invalid,
        severity="error",
        detail="needs_human projection has no valid structured decision result",
    )


def _check_agent_runs(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="agent_runs", code="running_stale", count=_count(
        db,
        "select count(*) from agent_runs where lower(status) in ('pending','running') and datetime(updated_at) < datetime(?)",
        (_cutoff(now, REPLY_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="agent run exceeded its execution lease")
    _add(attention, source="agent_runs", code="active", count=_count(
        db, "select count(*) from agent_runs where lower(status) in ('pending','running')"
    ), severity="info", detail="agent execution is in progress")
    _check_runtime_attempt_invariants(db, violations)


def _check_runtime_attempt_invariants(db: sqlite3.Connection, violations: list[QualityIssue]) -> None:
    checks = {
        "runtime_attempt_without_parent": "select count(*) from agent_runtime_attempts a where a.agent_run_id is not null and not exists (select 1 from agent_runs r where r.id=a.agent_run_id)",
        "multiple_active_runtime_attempts": "select count(*) from (select workload_kind, workload_key from agent_runtime_attempts where status in ('starting','running') group by workload_kind, workload_key having count(*)>1)",
        "completed_runtime_attempt_without_final_run": "select count(*) from agent_runtime_attempts a where a.status='completed' and a.agent_run_id is not null and not exists (select 1 from agent_runs r where r.id=a.agent_run_id and r.status in ('completed','failed','unknown'))",
    }
    for code, sql in checks.items():
        _add(violations, source="agent_runtime_attempts", code=code, count=_count(db, sql), severity="error", detail="runtime attempt invariant violated")
    leaks = 0
    for row in db.execute("select route_name, runtime_kind, credential_mode, model, session_id, failure_code, transcript_reference from agent_runtime_attempts"):
        leaks += any(contains_credential(str(value or "")) or contains_local_runtime_leak(str(value or "")) for value in row)
    _add(violations, source="agent_runtime_attempts", code="runtime_secret_leak", count=leaks, severity="error", detail="runtime attempt evidence contains sensitive material")


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


def _check_email_classification_tasks(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
) -> None:
    _add(
        violations,
        source="email_agent_classification_tasks",
        code="failed",
        count=_count(
            db,
            "select count(*) from email_agent_classification_tasks where lower(status)='failed'",
        ),
        severity="error",
        detail="email classification reached a non-retryable failure",
    )
    _add(
        violations,
        source="email_agent_classification_tasks",
        code="running_stale",
        count=_count(
            db,
            """select count(*) from email_agent_classification_tasks
               where lower(status)='running'
                 and datetime(updated_at) < datetime(?)""",
            (_cutoff(now, WORK_ITEM_PROCESSING_STALE_SECONDS),),
        ),
        severity="error",
        detail="email classification exceeded its execution lease",
    )
    _add(
        violations,
        source="email_agent_classification_tasks",
        code="pending_stale",
        count=_count(
            db,
            """select count(*) from email_agent_classification_tasks
               where lower(status)='pending'
                 and (available_at='' or datetime(available_at) <= datetime(?))
                 and datetime(updated_at) < datetime(?)""",
            (
                now.isoformat(),
                _cutoff(now, WORK_ITEM_PROCESSING_STALE_SECONDS),
            ),
        ),
        severity="error",
        detail="email classification remained due without being claimed",
    )


def _check_email_model_left_active(
    db_path: Path, violations: list[QualityIssue]
) -> None:
    """Say when a model someone activated is no longer the one deciding.

    The activation file records what was switched on. The service re-derives
    the mode from the registry every time and falls back to the Agent when the
    evidence no longer verifies, so the two can disagree with nothing failing:
    from 2026-09-21 the file said `model_primary` while every message went to
    the Agent, and the runtime-sample check above stayed silent because a
    model that is never asked leaves no samples.
    """

    root = db_path.parent / "email-models"
    activation = root / "online-active.json"
    try:
        recorded = json.loads(activation.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(recorded, dict) or recorded.get("mode") != "model_primary":
        return
    from app.email_classifier_runtime import (
        EmailClassifierRuntimeMode,
        derive_runtime_mode,
    )
    from app.email_model_registry import EmailModelRegistry

    try:
        derived = derive_runtime_mode(EmailModelRegistry(root))
    except Exception:  # noqa: BLE001 - an unreadable registry is itself the finding
        derived = EmailClassifierRuntimeMode.AGENT_PRIMARY
    if derived is not EmailClassifierRuntimeMode.MODEL_PRIMARY:
        _add(
            violations,
            source="email_model_activation",
            code="activated_model_not_deciding",
            count=1,
            severity="error",
            detail="the activation file says model_primary but the service runs the Agent",
        )


def _check_email_model_runtime(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    """Say when a promoted email model stops deciding what it was promoted for.

    An empty sample table reads the same whether no mail arrived or the model
    was never asked, so silence is not a finding here. What the samples can
    say is what happened to the mail the model did see.
    """

    cutoff = _cutoff(now, MODEL_RUNTIME_WINDOW_SECONDS)
    counts = {
        str(row["outcome"]): int(row["total"])
        for row in db.execute(
            """select outcome, count(*) as total
               from email_classifier_runtime_samples
               where recorded_at >= ?
               group by outcome""",
            (cutoff,),
        )
    }
    considered = sum(counts.values())
    if not considered:
        return
    recent_outcomes = [
        str(row["outcome"] or "")
        for row in db.execute(
            """select outcome
               from email_classifier_runtime_samples
               where recorded_at >= ?
               order by recorded_at desc, id desc""",
            (cutoff,),
        )
    ]
    active_failures = 0
    for outcome in recent_outcomes:
        if outcome != "failure":
            break
        active_failures += 1
    # Runtime samples are append-only evidence. A later normal decision closes
    # the service-level failure while preserving the older failed samples.
    _add(
        violations,
        source="email_classifier_runtime_samples",
        code="model_failing",
        count=active_failures,
        severity="error",
        detail="the promoted email model raised instead of deciding",
    )
    decided = counts.get("success", 0)
    if decided / considered < MODEL_DECIDED_SHARE_FLOOR and not counts.get("failure", 0):
        _add(
            attention,
            source="email_classifier_runtime_samples",
            code="model_rarely_decides",
            count=considered - decided,
            severity="info",
            detail="the promoted email model handed most mail back to the Agent",
        )


def _check_meetings(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="meeting_alignment_jobs", code="failed", count=_count(
        db,
        """select count(*) from meeting_alignment_jobs
           where lower(status)='failed' and trim(coalesce(error, ''))=''""",
    ), severity="error", detail="meeting delivery has no concrete terminal failure")
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
        (
            "meeting_memory_write_events",
            "status",
            ("failed",),
            ("pending",),
            "",
        ),
        # A failed row below the retry cap is still being retried; only an
        # exhausted failure or an unreconciled effect needs recovery.
        (
            "task_todo_sync_outbox",
            "status",
            ("failed", "unknown"),
            ("queued", "running"),
            "and not (lower(status)='failed' and attempt_count < 3)",
        ),
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


def _check_delivery_records(
    db: sqlite3.Connection,
    violations: list[QualityIssue],
) -> None:
    """Report an executed send the sender never recorded.

    A delivery row is written once, by the turn that performed the send, from
    the provider's own answer. Nothing rebuilds it afterwards: the sweep that
    used to do that wrote seven rows for sends that never happened, and it only
    existed because the live write was broken for months. So the missing row is
    reported here and left missing -- filling it in is how a failure to send
    becomes a record of sending.
    """
    _add(violations, source="sent_replies", code="executed_without_record", count=_count(
        db,
        """
        select count(*) from agent_runs audit
        join reply_tasks task on task.id=audit.reply_task_id
        where audit.role='audit' and audit.status='completed'
          and task.channel='dingtalk'
          and json_valid(audit.final_result_json)
          and json_extract(audit.final_result_json, '$.outcome')='executed'
          and trim(coalesce(json_extract(
                audit.final_result_json,
                '$.external_result.live_result_reference.open_task_id'
              ), coalesce(json_extract(
                audit.final_result_json,
                '$.external_result.live_result_reference.openTaskId'
              ), coalesce(json_extract(
                audit.final_result_json,
                '$.external_result.live_result_reference.sent_message_id'
              ), coalesce(json_extract(
                audit.final_result_json,
                '$.external_result.live_result_reference.openMessageId'
              ), '')))))<>''
          and audit.completed_at >= datetime('now', '-7 days')
          and not exists (
            select 1 from sent_reply_observers observer
            where observer.agent_run_id=audit.id
          )
        """,
    ), severity="error", detail=(
        "an Audit turn reported a send with a provider receipt and no delivery "
        "record was written; investigate the live write path, do not backfill"
    ))


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
    _add(attention, source="daily_scan_state", code="pagination_deferred", count=_count(
        db,
        """select count(*) from daily_scan_state
           where scanner_name='ai_minutes'
             and coalesce(json_extract(cursor_json, '$.pagination_deferred'), 0) = 1""",
    ), severity="info", detail="AI minutes first page completed; a later page will retry")
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
    attention: list[QualityIssue],
) -> None:
    scheduled_kinds = tuple(sorted(SCHEDULED_CAPABILITY_UNAVAILABLE_KINDS))
    scheduled_predicate = """error_event.conversation_id like 'scheduled-task:%'
        and error_event.kind in ({})""".format(
        ",".join("?" for _ in scheduled_kinds)
    )
    scheduled_count = _count(
        db,
        f"""select count(*) from errors error_event
            where datetime(error_event.created_at) >= datetime(?)
              and coalesce(error_event.resolved_at, '') = ''
              and {scheduled_predicate}""",
        (_cutoff(now, RECENT_ERROR_WINDOW_SECONDS), *scheduled_kinds),
    )
    _add(attention, source="scheduled_task_runs",
         code="scheduled_task_execution_unavailable", count=scheduled_count,
         severity="warning",
         detail="a scheduled task could not use its pinned Runtime or Skill")
    _add(violations, source="errors", code="recent_error", count=_count(
        db,
        """select count(*)
           from errors error_event
           where datetime(error_event.created_at) >= datetime(?)
             and coalesce(error_event.resolved_at, '') = ''
             and error_event.kind <> 'codex_capacity_pause'
             and not ({})
             and not exists (
                select 1
                from reply_attempts recovery
                where recovery.conversation_id=error_event.conversation_id
                  and recovery.trigger_message_id=error_event.message_id
                  and datetime(recovery.updated_at) >= datetime(error_event.created_at)
                  and lower(recovery.send_status) in ({})
             )""".format(
            scheduled_predicate,
            ",".join("?" for _ in RECOVERED_REPLY_ATTEMPT_STATUSES),
        ),
        (
            _cutoff(now, RECENT_ERROR_WINDOW_SECONDS),
            *scheduled_kinds,
            *RECOVERED_REPLY_ATTEMPT_STATUSES,
        ),
    ), severity="error", detail="a service error was recorded within the four-hour repair window")


def _check_scheduler_health(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
) -> None:
    row = db.execute(
        "select value from service_state where key=?",
        (f"{SERVICE_HEALTH_STATE_PREFIX}agent-cron-scheduler",),
    ).fetchone()
    if row is None:
        return
    try:
        payload = json.loads(str(row["value"] or ""))
        latest_tick = datetime.fromisoformat(
            str(payload.get("latest_tick_at") or "").replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        latest_tick = None
    stale = latest_tick is None or (
        now - latest_tick
    ).total_seconds() > AGENT_CRON_SCHEDULER_STALE_SECONDS
    _add(
        violations,
        source="service_state",
        code="scheduler_tick_stale",
        count=int(stale),
        severity="error",
        detail="the Agent Cron scheduler health observation stopped updating",
    )


def _check_codex_capacity_pause(
    db: sqlite3.Connection,
    now: datetime,
    attention: list[QualityIssue],
) -> None:
    if not _has_active_codex_capacity_pause(db, now):
        return
    _add(
        attention,
        source="codex_capacity",
        code="paused",
        count=1,
        severity="info",
        detail="Codex workspace capacity is paused until the recorded retry time",
    )


def _check_runtime_route_pauses(
    db: sqlite3.Connection,
    now: datetime,
    attention: list[QualityIssue],
) -> None:
    count = _count(
        db,
        """select count(*)
           from runtime_route_pauses paused
           where datetime(paused.retry_at) > datetime(?)
             and exists (
                select 1
                from agent_runtime_attempts alternative
                where alternative.route_name <> paused.route_name
                  and alternative.status='completed'
                  and datetime(alternative.finished_at) >= datetime(paused.opened_at)
             )""",
        (now.strftime("%Y-%m-%d %H:%M:%S"),),
    )
    _add(
        attention,
        source="runtime_route_pauses",
        code="route_paused_with_healthy_alternative",
        count=count,
        severity="info",
        detail="one runtime route is paused while another route has completed successfully",
    )


def _has_active_codex_capacity_pause(
    db: sqlite3.Connection,
    now: datetime,
) -> bool:
    row = db.execute(
        "select value from service_state where key='codex_capacity_pause'"
    ).fetchone()
    if row is None:
        return False
    try:
        value = json.loads(str(row["value"] or ""))
        retry_at = datetime.fromisoformat(str(value.get("retry_at") or ""))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return retry_at.astimezone(timezone.utc) > now
