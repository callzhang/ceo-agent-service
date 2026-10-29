"""Fail-closed hourly service coverage checks.

The audit UI is useful for investigation, but an hourly repair run also needs a
machine-readable answer to a narrower question: did it inspect every durable
queue, and is there an item that has stopped making progress?  This module
keeps that answer independent from the UI and intentionally treats a missing
table as a failed check rather than silently dropping a queue from coverage.
"""

from __future__ import annotations
from app.state_definitions import state_projection
from app.action_feedback_definitions import feedback_projection


import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.agent_cron.scheduler import SCHEDULED_CAPABILITY_UNAVAILABLE_KINDS
from app.config import forbidden_path_prefixes
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.store import (
    SERVICE_HEALTH_STATE_PREFIX, REPLY_TASK_CURRENT_SQL, ERROR_SQL,
    NATIVE_AGENT_RUN_SQL, NATIVE_REPLY_TASK_CURRENT_SQL,
    NATIVE_REPLY_BUSINESS_KEY_SQL, NATIVE_MEETING_TASK_SQL,
    NATIVE_REVIEW_CANDIDATE_SQL, NATIVE_CANDIDATE_REVIEW_SQL,
    NATIVE_CANDIDATE_EXECUTION_SQL,
    NATIVE_CANDIDATE_SELECTION_SQL,
)

from app.action_storage import (
    SENT_REPLY_SQL, SENT_REPLY_OBSERVER_SQL,
    NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL,
    REPLY_ATTEMPT_COMMON_SQL,
    REPLY_ATTEMPT_PROBLEM_TRIGGER_KEYS_SQL,
    task_delivery_projection,
)
from app.current_action_schema import current_reply_action_projection
from app.current_schema_manifest import current_objects
from app.action_storage import _objects

MEETING_JOB_SQL = NATIVE_MEETING_TASK_SQL
OKR_REQUEST_SQL = "select id,status,created_at,updated_at from tasks where kind='okr_review'"
REPLY_TASK_SQL = NATIVE_REPLY_TASK_CURRENT_SQL
WORK_SUMMARY_INPUT_SQL = "select tasks.id,tasks.status,tasks.created_at,tasks.updated_at from tasks join work_summary_inputs inputs on inputs.task_id=tasks.id where tasks.kind='work_summary'"
FEEDBACK_PROCESSING_ROUND_SQL = "select * from feedback_processing_rounds"
RUNTIME_ATTEMPT_SQL = """select id,parent_id as agent_run_id,workload_kind,workload_key,
    attempt_number,route_name,runtime_kind,credential_mode,model,session_mode,
    source_session_id,attempt_purpose,validation_retry_policy_id,
    validation_result_schema_id,session_id,status,failure_class,failure_code,
    failover_permitted,transcript_reference,first_effect_started_at,
    started_at,finished_at,created_at,updated_at
    from runtime_attempts"""


REQUIRED_SOURCES = (
    "reply_tasks",
    "reply_attempts",
    "sent_replies",
    "agent_runs",
    "work_summary_inputs",
    "follow_up_drafts",
    "meeting_alignment_jobs",
    "okr_review_requests",
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
    native_delivery_coverage: dict[str, int] | None = None

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
            "native_delivery_coverage": self.native_delivery_coverage,
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
        physical_source = {"sent_replies": "actions", "reply_attempts": "actions", "memory_write_events": "actions", "agent_runs": "runs", "reply_tasks": "tasks", "work_summary_inputs": "tasks", "meeting_alignment_jobs": "tasks", "okr_review_requests": "tasks", "daily_scan_state": "state", "wechat_read_state": "state", "email_agent_classification_tasks": "tasks", "email_classifier_runtime_samples": "runs", "follow_up_drafts": "actions", "wechat_deliveries": "actions", "errors": "actions", "feedback_events": "actions"}
        missing = tuple(source for source in REQUIRED_SOURCES if physical_source.get(source, source) not in existing)
        checked = (
            *(source for source in REQUIRED_SOURCES if physical_source.get(source, source) in existing),
            *(source for source in OPTIONAL_QUEUE_SOURCES if physical_source.get(source, source) in existing),
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
        if physical_source["email_agent_classification_tasks"] in existing:
            _check_email_classification_tasks(db, checked_now, violations)
        if physical_source["email_classifier_runtime_samples"] in existing:
            _check_email_model_runtime(db, checked_now, violations, attention)
        _check_email_model_left_active(Path(db_path), violations)
        # Legacy follow_up_drafts are history only (Derek 2026-09-25: a
        # follow-up is sent only when he clicks it), so an unsent or failed
        # one is not a delivery backlog and is not checked here.
        _check_meetings(db, checked_now, violations, attention)
        _check_okr_reviews(db, checked_now, violations, attention)
        _check_external_delivery_queues(db, checked_now, violations, attention)
        native_delivery_coverage = _check_delivery_records(db, violations)
        _check_feedback(db, violations, attention)
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
        native_delivery_coverage=native_delivery_coverage,
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
            f"""select channel, oa_url, trigger_message_json
               from ({REPLY_TASK_CURRENT_SQL})
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
    state = report.to_dict()
    # Native-source coverage is a live observation, not hourly persisted state.
    state.pop("native_delivery_coverage", None)
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
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
        f"""select count(*) from ({REPLY_TASK_SQL})
           where {reply_channels}
             and lower(status)='failed' and trim(coalesce(error, ''))=''""",
    ), severity="error", detail="reply task has no concrete terminal failure")
    stale_cutoff = _cutoff(now, REPLY_PROCESSING_STALE_SECONDS)
    _add(violations, source="reply_tasks", code="processing_stale", count=_count(
        db,
        f"""select count(*) from ({REPLY_TASK_SQL}) as reply_tasks
            where {reply_channels}
              and lower(status)='processing'
              and datetime(updated_at) < datetime(?)
              and not exists (
                  select 1 from runs current_run
                  where current_run.task_id=reply_tasks.id
                    and current_run.execution_generation=reply_tasks.execution_generation
                    and current_run.role in ('consumer','audit')
                    and lower(current_run.status) in ('pending','running')
                    and not exists (
                        select 1 from run_provenance provenance
                        where provenance.run_id=current_run.id
                    )
                    and (
                        datetime(current_run.updated_at) >= datetime(?)
                        or exists (
                            select 1 from runtime_attempts active_attempt
                            where active_attempt.parent_id=current_run.id
                              and lower(active_attempt.status) in ('starting','running')
                              and datetime(active_attempt.updated_at) >= datetime(?)
                        )
                    )
              )""",
        (stale_cutoff, stale_cutoff, stale_cutoff),
    ), severity="error", detail="reply task and its current agent execution exceeded the worker recovery lease")
    pending_overdue = _count(
        db,
        f"""select count(*) from ({REPLY_TASK_SQL})
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
        db, f"select count(*) from ({REPLY_TASK_SQL}) where lower(status) in ('pending','processing')"
    ), severity="info", detail="reply work is currently queued or processing")


def _check_reply_attempts(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    # Only the newest attempt for a trigger can be actionable. This prevents an
    # old blocked/dry-run row from masking a later sent, skipped, or failed row.
    latest = f"""
        with candidate_keys as (
            select channel, conversation_id, trigger_message_id
            from ({REPLY_ATTEMPT_PROBLEM_TRIGGER_KEYS_SQL})
            group by channel, conversation_id, trigger_message_id
        ), latest as (
            select current.id, current.channel, current.conversation_id,
                   current.trigger_message_id, current.action,
                   current.send_status, current.updated_at,
                   current.reviewed_at, current.resolved_at,
                   current.agent_run_id, 1 as ordinal
            from candidate_keys candidate
            cross join ({REPLY_ATTEMPT_COMMON_SQL}) current on current.id=(
                  select newest.id
                  from ({REPLY_ATTEMPT_COMMON_SQL}) newest
                  where newest.channel is candidate.channel
                    and newest.conversation_id is candidate.conversation_id
                    and newest.trigger_message_id is candidate.trigger_message_id
                  order by datetime(newest.updated_at) desc, newest.id desc
                  limit 1
              )
        )
    """
    direct_terminal = _count(
        db,
        latest + f"""
            select count(*) from latest a
            where ordinal=1
              and lower(action) in ('send_reply','ask_clarifying_question')
              and lower(send_status) in ('failed','blocked')
              and not exists (
                select 1 from ({SENT_REPLY_SQL}) sr
                where sr.conversation_id=a.conversation_id
                  and sr.trigger_message_id=a.trigger_message_id
              )
              and not exists (
                select 1 from ({REPLY_TASK_SQL}) t
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
        f"""
            with oa_latest as (
                select a.id, a.channel, a.conversation_id, a.trigger_message_id,
                       a.oa_process_instance_id, a.send_status, row_number() over (
                    partition by case
                        when trim(coalesce(a.oa_process_instance_id, '')) != ''
                            then a.oa_process_instance_id
                        else 'attempt:' || a.id
                    end
                    order by datetime(a.updated_at) desc, a.id desc
                ) as ordinal
                from ({REPLY_ATTEMPT_COMMON_SQL}) a
                where lower(a.action)='oa_approval'
            )
            select count(*) from oa_latest a
            where ordinal=1
              and lower(a.send_status) in ('failed','blocked')
              and not exists (
                select 1 from ({REPLY_ATTEMPT_COMMON_SQL}) resolved
                where resolved.oa_process_instance_id=a.oa_process_instance_id
                  and exists (
                    select 1 from actions owner
                    join reply_attempt_receipt_refs ref on ref.attempt_id=owner.id
                    join action_receipts body on body.id=ref.receipt_id
                    join action_receipts fact on fact.action_id=body.action_id and fact.provider_receipt_id=body.id
                    where owner.record_type='reply_attempts' and cast(owner.legacy_key as integer)=resolved.id
                      and body.provider_result_json is not null and fact.provider_result_json is null
                      and fact.agent_run_id is null and fact.execution_run_id is null and fact.action_index is null
                      and fact.provider_result_id is null and json_valid(fact.target)
                      and json_extract(fact.target, '$.process_instance_id')=resolved.oa_process_instance_id
                      and json_extract(fact.target, '$.task_id')=resolved.oa_task_id
                      and case when json_valid(fact.outcome_json) then
                        json_type(fact.outcome_json)='object'
                        and json_type(fact.outcome_json, '$.success') is not null
                        and json_type(fact.outcome_json, '$.taskStatus') is not null
                      else 0 end
                      and json_extract(fact.outcome_json, '$.success')=1
                      and upper(coalesce(json_extract(fact.outcome_json, '$.taskStatus'),''))='COMPLETED'
                  )
              )
              and not exists (
                select 1 from ({REPLY_TASK_SQL}) t
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
        latest + f"""
            select count(*) from latest a
            where ordinal=1 and lower(send_status)='dry_run'
              and datetime(updated_at) >= datetime(?)
              and not exists (
                select 1 from ({REPLY_TASK_SQL}) t
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
        latest + f"""
            select count(*) from latest a
            where ordinal=1 and lower(send_status) in ('failed','blocked')
              and exists (
                select 1 from ({REPLY_TASK_SQL}) t
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
        + f"""
            select c.candidate_json as reviewed_candidate_json,
                   choice.id as selection_id
            from latest a
            left join ({NATIVE_CANDIDATE_REVIEW_SQL}) review on review.audit_run_id=a.agent_run_id
              and review.decision='approve'
            left join ({NATIVE_REVIEW_CANDIDATE_SQL}) c on c.id=review.candidate_id
              and c.invalidated_at='' and c.candidate_digest=review.candidate_digest
              and c.id=(select max(newest.id) from ({NATIVE_REVIEW_CANDIDATE_SQL}) newest
                  where newest.task_id=c.task_id and newest.execution_generation=c.execution_generation)
              and exists(select 1 from ({REPLY_TASK_SQL}) task where task.id=c.task_id
                  and task.execution_generation=c.execution_generation
                  and task.status='needs_human')
            left join ({NATIVE_CANDIDATE_SELECTION_SQL}) choice on choice.candidate_id=c.id
            where a.ordinal=1 and lower(a.send_status)='needs_human'
              and a.reviewed_at is null
              and trim(coalesce(a.resolved_at, ''))=''
              and not exists (
                  select 1
                  from ({REPLY_TASK_SQL}) as historical_task
                  join ({NATIVE_REPLY_BUSINESS_KEY_SQL}) as current_business_object
                    on current_business_object.business_object_key=
                       historical_task.business_object_key
                  where historical_task.channel=a.channel
                    and historical_task.conversation_id=a.conversation_id
                    and historical_task.trigger_message_id=a.trigger_message_id
                    and current_business_object.reply_task_id<>historical_task.id
              )
              and not exists (
                  select 1 from ({REPLY_TASK_SQL}) t
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
        try:
            candidate = json.loads(row["reviewed_candidate_json"] or "")
        except (TypeError, json.JSONDecodeError):
            candidate = {}
        if candidate.get("outcome") == "needs_human" and row["selection_id"] is None:
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
        f"select count(*) from ({NATIVE_AGENT_RUN_SQL}) as agent_runs where lower(status) in ('pending','running') and datetime(updated_at) < datetime(?)",
        (_cutoff(now, REPLY_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="agent run exceeded its execution lease")
    _add(attention, source="agent_runs", code="active", count=_count(
        db, f"select count(*) from ({NATIVE_AGENT_RUN_SQL}) as agent_runs where lower(status) in ('pending','running')"
    ), severity="info", detail="agent execution is in progress")
    _check_runtime_attempt_invariants(db, violations)


def _check_runtime_attempt_invariants(db: sqlite3.Connection, violations: list[QualityIssue]) -> None:
    checks = {
        "runtime_attempt_without_parent": f"select count(*) from ({RUNTIME_ATTEMPT_SQL}) a where a.agent_run_id is not null and not exists (select 1 from runs r where r.id=a.agent_run_id)",
        "multiple_active_runtime_attempts": f"select count(*) from (select workload_kind, workload_key from ({RUNTIME_ATTEMPT_SQL}) as agent_runtime_attempts where status in ('starting','running') group by workload_kind, workload_key having count(*)>1)",
        "completed_runtime_attempt_without_final_run": f"select count(*) from ({RUNTIME_ATTEMPT_SQL}) a where a.workload_kind='agent_run' and a.status='completed' and a.agent_run_id is not null and not exists (select 1 from runs r where r.id=a.agent_run_id and r.status in ('completed','failed','unknown'))",
    }
    for code, sql in checks.items():
        _add(violations, source="agent_runtime_attempts", code=code, count=_count(db, sql), severity="error", detail="runtime attempt invariant violated")
    leaks = 0
    path_prefixes = forbidden_path_prefixes()
    checked_values: dict[str, bool] = {}

    def contains_leak(value: object) -> bool:
        text = str(value or "")
        if text not in checked_values:
            checked_values[text] = (
                contains_credential(text)
                or contains_local_runtime_leak(text, path_prefixes=path_prefixes)
            )
        return checked_values[text]

    for row in db.execute(f'select route_name, runtime_kind, credential_mode, model, session_id, failure_code, transcript_reference from ({RUNTIME_ATTEMPT_SQL}) as agent_runtime_attempts'):
        leaks += any(contains_leak(value) for value in row)
    _add(violations, source="agent_runtime_attempts", code="runtime_secret_leak", count=leaks, severity="error", detail="runtime attempt evidence contains sensitive material")


def _check_work_items(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="work_summary_inputs", code="failed", count=_count(
        db, f"select count(*) from ({WORK_SUMMARY_INPUT_SQL}) where lower(status)='failed'"
    ), severity="error", detail="work item has no terminal handling")
    _add(violations, source="work_summary_inputs", code="processing_stale", count=_count(
        db, f"select count(*) from ({WORK_SUMMARY_INPUT_SQL}) where lower(status)='processing' and datetime(updated_at) < datetime(?)",
        (_cutoff(now, WORK_ITEM_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="work item exceeded the task agent timeout")
    _add(attention, source="work_summary_inputs", code="active", count=_count(
        db, f"select count(*) from ({WORK_SUMMARY_INPUT_SQL}) where lower(status) in ('pending','processing')"
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
            "select count(*) from tasks where kind='email_classification' and lower(status)='failed'",
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
            """select count(*) from tasks where kind='email_classification'
               and lower(status)='running'
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
            """select count(*) from tasks where kind='email_classification'
               and lower(status)='pending'
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
            "select outcome, count(*) as total from email_classifier_runtime_samples "
            "where created_at >= ? group by outcome",
            (cutoff,),
        )
    }
    considered = sum(counts.values())
    if not considered:
        return
    recent_outcomes = [
        str(row["outcome"] or "")
        for row in db.execute(
            "select outcome from email_classifier_runtime_samples "
            "where created_at >= ? order by created_at desc,id desc",
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
        f"""select count(*) from ({MEETING_JOB_SQL})
           where lower(status)='failed' and trim(coalesce(error, ''))=''""",
    ), severity="error", detail="meeting delivery has no concrete terminal failure")
    _add(violations, source="meeting_alignment_jobs", code="active_stale", count=_count(
        db,
        f"select count(*) from ({MEETING_JOB_SQL}) where lower(status) in ('pending','processing','ready_to_send','retry') and datetime(updated_at) < datetime(?)",
        (_cutoff(now, MEETING_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="meeting alignment job stopped progressing")
    _add(attention, source="meeting_alignment_jobs", code="active", count=_count(
        db, f"select count(*) from ({MEETING_JOB_SQL}) where lower(status) in ('waiting','pending','processing','ready_to_send','retry')"
    ), severity="info", detail="meeting alignment work is pending")
    _add(attention, source="meeting_alignment_jobs", code="quarantined", count=_count(
        db, f"select count(*) from ({MEETING_JOB_SQL}) where lower(status)='quarantined'"
    ), severity="info", detail="meeting delivery outcome cannot be verified")


def _check_okr_reviews(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="okr_review_requests", code="failed", count=_count(
        db, f"select count(*) from ({OKR_REQUEST_SQL}) where lower(status)='failed'"
    ), severity="error", detail="OKR review request has no terminal handling")
    _add(violations, source="okr_review_requests", code="processing_stale", count=_count(
        db, f"select count(*) from ({OKR_REQUEST_SQL}) where lower(status)='processing' and datetime(updated_at) < datetime(?)",
        (_cutoff(now, OKR_PROCESSING_STALE_SECONDS),),
    ), severity="error", detail="OKR review exceeded the Codex timeout")
    _add(attention, source="okr_review_requests", code="active", count=_count(
        db, f"select count(*) from ({OKR_REQUEST_SQL}) where lower(status) in ('pending','processing')"
    ), severity="info", detail="OKR review work is pending")


def _check_external_delivery_queues(
    db: sqlite3.Connection,
    now: datetime,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    for source, status_column, failed_statuses, active_statuses, resolved_filter in (
        # A rejected delivery is a deliberate user decision. Store already maps
        # its reply attempt to skipped, so the external queue must do the same.
        ("wechat_deliveries", "status", ("failed", "send_unknown"),
         ("pending", "sending", "ready_to_send"),
         "and lower(coalesce(error, '')) != 'user_rejected'"),
        ("memory_write_events", "status", ("failed",), ("pending", "processing"), ""),
    ):
        if source == "memory_write_events":
            target_action_objects = [
                {"type": kind, "name": name, "sql": sql}
                for kind, name, table, sql in current_objects()
                if table == "actions" and sql is not None
            ]
            if _objects(db, "actions") != target_action_objects:
                raise sqlite3.IntegrityError("unsupported or mixed Attempt/Memory action schema")
            source_sql = f"({current_reply_action_projection('memory_write_events')})"
        else:
            source_sql = f"({task_delivery_projection(source)})"
        failed = _count(
            db,
            f"""select count(*) from {source_sql}
                where lower({status_column}) in ({','.join('?' for _ in failed_statuses)})
                {resolved_filter}""",
            failed_statuses,
        )
        _add(violations, source=source, code="failed", count=failed,
             severity="error", detail="external delivery queue has an unrecovered failure")
        active = _count(
            db,
            f"select count(*) from {source_sql} where lower({status_column}) in ({','.join('?' for _ in active_statuses)})",
            active_statuses,
        )
        _add(attention, source=source, code="active", count=active,
             severity="info", detail="external delivery work is active")


def _check_delivery_records(
    db: sqlite3.Connection,
    violations: list[QualityIssue],
) -> dict[str, int]:
    """Report an executed send the sender never recorded.

    A delivery row is written once, by the turn that performed the send, from
    the provider's own answer. Nothing rebuilds it afterwards: the sweep that
    used to do that wrote seven rows for sends that never happened, and it only
    existed because the live write was broken for months. So the missing row is
    reported here and left missing -- filling it in is how a failure to send
    becomes a record of sending.
    """
    from app.native_trajectory import read_native_result_json
    rows = db.execute(f"""select audit.* from ({NATIVE_AGENT_RUN_SQL}) audit
        join ({REPLY_TASK_SQL}) task on task.id=audit.reply_task_id
        where audit.role='audit' and audit.status='completed'
          and task.channel='dingtalk'
          and audit.completed_at >= datetime('now', '-7 days')
          and not exists(select 1 from ({SENT_REPLY_OBSERVER_SQL}) observer where observer.agent_run_id=audit.id)
        """).fetchall()
    missing = 0
    unavailable = 0
    for row in rows:
        physical_run = db.execute("select * from runs where id=?", (row["id"],)).fetchone()
        native_result = read_native_result_json(db, physical_run)
        if not native_result:
            unavailable += 1
            continue
        payload = json.loads(native_result)
        external = payload.get("external_result")
        reference = external.get("live_result_reference") if isinstance(external, dict) else None
        if not isinstance(reference, dict):
            continue
        receipt = next((reference[key]
            for key in ("open_task_id", "openTaskId", "sent_message_id", "openMessageId")
            if reference.get(key) is not None), "")
        if payload.get("outcome") == "executed" and str(receipt).strip():
            missing += 1
    _add(violations, source="sent_replies", code="executed_without_record", count=missing,
         severity="error", detail=(
             "an Audit turn reported a send with a provider receipt and no delivery "
             "record was written; investigate the live write path, do not backfill"
         ))
    return {"checked": len(rows) - unavailable, "unavailable": unavailable}


def _check_feedback(
    db: sqlite3.Connection,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    """Count feedback nobody has acted on; list the rest as attention.

    A manual Workbench item (``manual:<attempt id>``) that was rerun after it
    arrived, whose new execution finished with every approved action verified,
    has been acted on even though no resolution is recorded: closing one needs
    code, Skill or config evidence an operational rerun does not produce
    (Derek 2026-10-24). It stays visible as attention. This says the feedback
    was not left unattended, not that the rerun did what it asked. A reopened
    item is counted again, and so is any other feedback.

    A manual item about a failed attempt is likewise attention, not an error,
    once a task for the same trigger has finished ``done`` after the feedback
    arrived: the newest outcome for a trigger decides (Derek 2026-10-28).
    """
    unresolved = _count(
        db, f"select count(*) from ({feedback_projection('feedback_events')}) "
            "where resolved_at='' or resolved_at is null"
    )
    manual_open = f"""
        (fe.resolved_at='' or fe.resolved_at is null)
          and fe.key like 'manual:%'
          and not exists (
              select 1 from ({FEEDBACK_PROCESSING_ROUND_SQL}) rnd
              where rnd.feedback_key=fe.key and rnd.reopened_at<>'')
    """
    acted_predicate = f"""
        {manual_open}
          and exists (
              select 1
              from ({REPLY_ATTEMPT_COMMON_SQL}) ra
              join ({NATIVE_CANDIDATE_REVIEW_SQL}) review on review.audit_run_id=ra.agent_run_id
              join ({NATIVE_CANDIDATE_EXECUTION_SQL}) execution
                on execution.candidate_id=review.candidate_id
              where ra.id=cast(substr(fe.key, 8) as integer)
                and execution.status='done'
                and julianday(execution.created_at) >= julianday(fe.received_at)
                and exists (
                    select 1 from ({NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL}) action
                    where action.execution_id=execution.id and action.status='verified')
                and not exists (
                    select 1 from ({NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL}) action
                    where action.execution_id=execution.id and action.status<>'verified'))
    """
    # Same rule as the reply-attempt check above: a failed attempt is
    # superseded once a task for the same trigger finished ``done``. Feedback
    # about that failure, given before the recovery, has been answered by it
    # (Derek 2026-10-28). Feedback on an attempt that did not fail is not
    # covered: a 👎 on a sent reply stays an error until resolved.
    recovered_predicate = f"""
        {manual_open}
          and not ({acted_predicate})
          and exists (
              select 1
              from ({REPLY_ATTEMPT_COMMON_SQL}) ra
              join ({REPLY_TASK_SQL}) t
                on t.channel is ra.channel
               and t.conversation_id is ra.conversation_id
               and t.trigger_message_id is ra.trigger_message_id
              where ra.id=cast(substr(fe.key, 8) as integer)
                and lower(ra.send_status) in ('failed','blocked')
                and lower(t.status)='done'
                and julianday(t.updated_at) >= julianday(fe.received_at))
    """
    feedback_sql = f"select count(*) from ({feedback_projection('feedback_events')}) fe where "
    acted = _count(db, feedback_sql + acted_predicate)
    recovered = _count(db, feedback_sql + recovered_predicate)
    _add(violations, source="feedback_events", code="unresolved",
         count=unresolved - acted - recovered, severity="error",
         detail="user feedback has not reached a recorded resolution")
    _add(attention, source="feedback_events", code="acted_unresolved", count=acted,
         severity="info", detail=(
             "manual feedback was rerun and its actions verified, but no resolution "
             "is recorded; confirm the rerun did what it asked"
         ))
    _add(attention, source="feedback_events", code="recovered_unresolved", count=recovered,
         severity="info", detail=(
             "manual feedback was about a failed attempt whose trigger later finished; "
             "no resolution is recorded, confirm the later result is what it asked"
         ))


def _check_scan_health(
    db: sqlite3.Connection,
    violations: list[QualityIssue],
    attention: list[QualityIssue],
) -> None:
    _add(violations, source="daily_scan_state", code="last_error", count=_count(
        db, 'select count(*) from (' + state_projection('daily_scan_state') + ") where trim(last_error) != ''"
    ), severity="error", detail="source scanner reports an unresolved error")
    _add(attention, source="daily_scan_state", code="pagination_deferred", count=_count(
        db,
        'select count(*) from (' + state_projection('daily_scan_state') + ")\n           where scanner_name='ai_minutes'\n             and coalesce(json_extract(cursor_json, '$.pagination_deferred'), 0) = 1",
    ), severity="info", detail="AI minutes first page completed; a later page will retry")
    _add(attention, source="daily_scan_state", code="oa_detail_read", count=_count(
        db,
        'select count(*) from (' + state_projection('daily_scan_state') + ")\n           where scanner_name='oa_pending'\n             and json_array_length(\n                 coalesce(json_extract(cursor_json, '$.read_failure_process_instance_ids'), '[]')\n             ) > 0",
    ), severity="info", detail="some OA approval details need a later read")
    # A disabled reader only blocks quality when it has work to process. This
    # avoids treating an intentionally unconfigured channel as an outage.
    blocked_reader = _count(
        db,
        'select count(*) from (' + state_projection('wechat_read_state') + ") where lower(capability_status) not in ('ready','')",
    )
    active_wechat = _count(
        db, f"select count(*) from ({REPLY_TASK_SQL}) where lower(channel)='wechat' and lower(status) in ('pending','processing')"
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
        f"""select count(*) from ({ERROR_SQL}) error_event
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
           from ({ERROR_SQL}) error_event
           where datetime(error_event.created_at) >= datetime(?)
             and coalesce(error_event.resolved_at, '') = ''
             and error_event.kind <> 'codex_capacity_pause'
             and not ({})
             and not exists (
                select 1
                from ({REPLY_ATTEMPT_COMMON_SQL}) recovery
                where recovery.conversation_id=error_event.conversation_id
                  and recovery.trigger_message_id=error_event.message_id
                  and datetime(recovery.updated_at) >= datetime(error_event.created_at)
                  and lower(recovery.send_status) in ({})
             )""".format(
            scheduled_predicate,
            ",".join("?" for _ in RECOVERED_REPLY_ATTEMPT_STATUSES),
            REPLY_ATTEMPT_COMMON_SQL=REPLY_ATTEMPT_COMMON_SQL,
            ERROR_SQL=ERROR_SQL,
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
        'select value from (' + state_projection('service_state') + ') where key=?',
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
        f"""select count(*)
           from ({state_projection('runtime_route_pauses')}) paused
           where datetime(paused.retry_at) > datetime(?)
             and exists (
                select 1
                from ({RUNTIME_ATTEMPT_SQL}) alternative
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
        'select value from (' + state_projection('service_state') + ") where key='codex_capacity_pause'"
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
