"""Read-only current execution queues for the Dashboard.

Attention remains owned by its existing endpoint. This projection never reads a
native transcript, claims a task, repairs state, or changes retry policy.
"""

from collections.abc import Callable
from datetime import UTC, datetime
import sqlite3

from fastapi import FastAPI, Query

from app.run_storage import MEETING_RUN_SQL, SCHEDULED_RUN_SQL
from app.external_retry import MAX_AUTOMATIC_ATTEMPTS
from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
from app.action_storage import (
    task_delivery_projection,
    SYNC_ACTION_PROJECTIONS,
)
from app.configs_definitions import config_projection
from app.state_definitions import state_projection
from app.email_canonical_storage import email_classification_projection
from app.storage_payload import TASK_RECORD_TYPES
from app.task20_definitions import TASK20_FIELDS, task20_column, task20_projection


# Only executable sources; parent projects, saved inputs and completed history
# are not queue items. These expressions use the current source-owned fields.
TASK_SOURCES = {
    "reply_tasks": ("消息 / 审批", "conversation_title"),
    "work_summary_inputs": ("任务提取", "source_ref"),
    "meeting_alignment_jobs": ("会议同步", "title"),
    "okr_review_requests": ("OKR 审阅", "conversation_title"),
    "weekly_okr_analysis_jobs": ("每周 OKR", "week_end"),
    "email_agent_classification_tasks": ("邮件分类", "stable_message_identity"),
    "workbench_turns": ("Agent 工作台", "client_request_id"),
    "wechat_memory_import_jobs": ("微信 Memory 导入", "account_id"),
}


def _time(value: str | None) -> str:
    if not value:
        return ""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed.replace(tzinfo=UTC).isoformat()
        if parsed.tzinfo is None
        else parsed.astimezone(UTC).isoformat()
    )


def _runtime_metadata(db: sqlite3.Connection, now: str) -> dict[int, dict]:
    """Live runtime metadata, tied to the current parent generation/run only."""
    generation = task20_column("reply_tasks", "execution_generation")
    run_columns = {row[1] for row in db.execute("pragma table_info(runs)")}
    summary_input_id = (
        "domain.summary_input_id" if "summary_input_id" in run_columns else "NULL"
    )
    rows = db.execute(
        f"""
        select runtime.legacy_key as runtime_id, runtime.workload_kind,
               runtime.workload_key, runtime.runtime_kind, runtime.model,
               runtime.started_at, runtime.updated_at, runtime.status, runtime.lease_owner,
               parent.role, parent.task_id as reply_task_id,
               domain.task_id as domain_task_id, {summary_input_id} as summary_input_id
        from runs runtime
        left join runs parent on parent.id=runtime.parent_id
          and parent.record_type='agent_runs' and parent.status='running'
          and exists(select 1 from tasks task where task.id=parent.task_id
            and task.record_type='reply_tasks' and task.lifecycle_status='processing'
            and task.{generation}=parent.execution_generation)
        left join runs domain on runtime.parent_id is null
          and runtime.workload_key=domain.legacy_key and domain.status='running'
          and ((runtime.workload_kind='meeting' and domain.record_type='meeting_alignment_runs')
            or (runtime.workload_kind='task' and domain.record_type='task_agent_runs'))
        where runtime.record_type='agent_runtime_attempts'
          and runtime.status in ('starting','running')
          and ((runtime.lease_owner<>'' and datetime(runtime.lease_expires_at)>datetime(?))
            or (parent.task_id is not null and exists(
              select 1 from tasks live where live.id=parent.task_id
                and live.lifecycle_lease_owner<>''
                and datetime(live.lifecycle_lease_expires_at)>datetime(?))))
        order by runtime.id
    """,
        (now, now),
    ).fetchall()
    result = {}
    for row in rows:
        task_id = row["reply_task_id"] or row["domain_task_id"]
        if task_id is None and row["summary_input_id"] is not None:
            task = db.execute(
                "select id from tasks where record_type='work_summary_inputs' and legacy_key=?",
                (str(row["summary_input_id"]),),
            ).fetchone()
            task_id = task["id"] if task else None
        if task_id is None and row["workload_kind"] in (
            "email_classification",
            "workbench",
        ):
            kind = (
                "email_agent_classification_tasks"
                if row["workload_kind"] == "email_classification"
                else "workbench_turns"
            )
            identity = task20_column(
                kind,
                "task_id" if kind == "email_agent_classification_tasks" else "id",
            )
            # The task worker and native router own independent leases.
            task = db.execute(
                f"select id from tasks where record_type=? and {identity}=? "
                "and lifecycle_status='processing' and lifecycle_lease_owner<>'' "
                "and datetime(lifecycle_lease_expires_at)>datetime(?)",
                (kind, row["workload_key"], now),
            ).fetchone()
            task_id = task["id"] if task else None
        if task_id is None and row["workload_kind"] in (
            "structured",
            "weekly_okr",
            "memory",
        ):
            task = None
            if row["workload_kind"] == "structured":
                task = db.execute(
                    "select id from tasks where record_type='okr_review_requests' and legacy_key=? and lifecycle_status='processing'",
                    (row["workload_key"],),
                ).fetchone()
            elif row["workload_kind"] == "weekly_okr":
                from app.store import AutoReplyStore

                if AutoReplyStore._runtime_operation_parent_exists(
                    db, row["workload_kind"], row["workload_key"]
                ):
                    week_end, manager, digest = row["workload_key"].split(":")[:3]
                    task = db.execute(
                        f"select id from tasks where record_type='weekly_okr_analysis_jobs' and {task20_column('weekly_okr_analysis_jobs', 'week_end')}=? and {task20_column('weekly_okr_analysis_jobs', 'manager_user_id')}=? and {task20_column('weekly_okr_analysis_jobs', 'source_digest')}=? and lifecycle_status='processing'",
                        (week_end, manager, digest),
                    ).fetchone()
            elif row["workload_key"].startswith("wechat_memory_import_job:"):
                task = db.execute(
                    "select id from tasks where record_type='wechat_memory_import_jobs' and legacy_key=? and lifecycle_status='processing'",
                    (row["workload_key"].split(":")[1],),
                ).fetchone()
            task_id = task["id"] if task else None
        if task_id is not None:
            result[int(task_id)] = dict(row)
    return result


def read_dashboard_queues(
    db: sqlite3.Connection, *, now: datetime
) -> dict[str, list[dict]]:
    now_text = now.astimezone(UTC).isoformat()
    runtime = _runtime_metadata(db, now_text)
    queues: dict[str, list[dict]] = {"active": [], "queued": []}
    objects = task20_projection("business_object_tasks")
    for kind, (label, title_field) in TASK_SOURCES.items():
        title = "task." + task20_column(kind, title_field)
        if kind == "email_agent_classification_tasks":
            body = task20_column(kind, "input_json")
            title = f"coalesce(nullif(json_extract(task.{body}, '$.message.subject'),''), {title})"
        available = "task.lifecycle_available_at"
        started = "task.lifecycle_locked_at"
        phase = "task.lifecycle_status"
        if kind == "workbench_turns":
            parent_title = task20_column("workbench_tasks", "title")
            title = f"(select p.{parent_title} from tasks p where p.id=task.parent_id)"
            started = "task." + task20_column(kind, "started_at")
        if kind == "meeting_alignment_jobs":
            phase = "task.meeting_phase"
        if kind == "meeting_alignment_jobs":
            eligible = "task." + task20_column(kind, "eligible_at")
            available = f"case when coalesce(datetime(task.lifecycle_available_at),'')>coalesce(datetime({eligible}),'') then task.lifecycle_available_at else {eligible} end"
        current = ""
        if kind == "reply_tasks":
            key = task20_column(kind, "business_object_key")
            current = f"""and not exists(select 1 from ({objects}) current_object
                where current_object.business_object_key=task.{key}
                  and current_object.reply_task_id<>task.id)"""
        rows = db.execute(
            f"""
            select task.id, task.legacy_key, task.lifecycle_status as status,
                   task.lifecycle_attempts as attempts, task.lifecycle_generation as generation,
                   task.lifecycle_error as error, {available} as available_at,
                   task.lifecycle_created_at as created_at, task.lifecycle_updated_at as updated_at,
                   {started} as locked_at, {phase} as phase, task.lifecycle_lease_owner as owner,
                   task.lifecycle_lease_expires_at as lease_expires_at, {title} as title
            from tasks task indexed by idx_task_lifecycle_status
            where task._kind=? and task.lifecycle_status in ('pending','processing') {current}
        """,
            (TASK_RECORD_TYPES.code(kind),),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            native = runtime.get(row["id"])
            lease_active = bool(
                row["owner"]
                and row["lease_expires_at"]
                and _time(row["lease_expires_at"]) > now_text
            )
            delivery = (
                kind == "meeting_alignment_jobs" and row["phase"] == "ready_to_send"
            )
            state = (
                "queued"
                if row["status"] == "pending" or (delivery and not row["locked_at"])
                else "active"
            )
            if state == "active" and not (
                native or lease_active or (delivery and row["locked_at"])
            ):
                continue
            has_counter = {"attempts", "attempt_count"}.intersection(
                TASK20_FIELDS[kind]
            )
            attempts = row["attempts"] if has_counter and not delivery else None
            retry = (
                None
                if attempts is None
                else max(0, int(attempts) - (state == "active"))
            )
            public_id = row["legacy_key"] or str(row["id"])
            queues[state].append(
                {
                    "id": f"{kind}:{public_id}",
                    "source": label,
                    "title": str(row["title"] or f"{label} #{public_id}"),
                    "status": row["phase"],
                    "retry_number": retry,
                    "agent": native["role"] or native["workload_kind"]
                    if native
                    else "",
                    "runtime_kind": native["runtime_kind"] if native else "",
                    "model": native["model"] if native else "",
                    "stage": (native["status"] if native else row["phase"]),
                    "started_at": _time(
                        native["started_at"] if native else row["locked_at"]
                    ),
                    "updated_at": _time(
                        native["updated_at"] if native else row["updated_at"]
                    ),
                    "created_at": _time(row["created_at"]),
                    "available_at": _time(row["available_at"]),
                    "error": str(row["error"] or ""),
                    "detail_url": _detail_url(db, kind, public_id, row["id"]),
                }
            )
    _append_auxiliary_queues(db, queues, now_text)
    for name, rows in queues.items():
        rows.sort(
            key=lambda item: (
                (item["available_at"] or item["created_at"], item["id"])
                if name == "queued"
                else (item["started_at"], item["id"])
            )
        )
    return queues


def _append_auxiliary_queues(db, queues, now_text):
    sources = []
    for kind, label, parent in (
        ("meeting_memory_write_events", "会议 Memory 写入", "meeting_job_id"),
    ):
        projection = task_delivery_projection(kind)
        current = ""
        sources.append(
            (
                kind,
                label,
                f"""select a.id, a.status, a.attempts,
            a.created_at, a.updated_at, a.available_at, a.error, a.started_at,
            a.lease_owner as owner, a.lease_expires_at, '' as title, '' as detail_url
            from ({projection}) a where a.status in ('pending','running','processing') {current}""",
            )
        )
    for kind in ("task_todo_sync_outbox", "business_task_todo_sync_outbox"):
        projection = SYNC_ACTION_PROJECTIONS[kind]
        sources.append(
            (
                kind,
                "钉钉待办同步",
                f"""select id,status,attempt_count as attempts,
            created_at,updated_at,next_attempt_at as available_at,error,'' as started_at,
            lease_owner as owner,lease_expires_at,operation as title,'' as detail_url
            from ({projection}) where status in ('queued','running')
              or (status='failed' and attempt_count<{MAX_AUTOMATIC_ATTEMPTS} and next_attempt_at<>'')""",
            )
        )
    sources.append(
        (
            "scheduled_task_runs",
            "定时任务",
            f"""select run.id,
        case when run.lease_owner<>'' and datetime(run.lease_expires_at)>datetime('{now_text}')
             then 'running' else 'pending' end as status,
        null as attempts,run.created_at,
        coalesce(claim.updated_at,run.created_at) as updated_at,run.scheduled_for as available_at,
        run.skip_or_error_reason as error,coalesce(claim.created_at,'') as started_at,
        run.lease_owner as owner,run.lease_expires_at,
        json_extract(config.snapshot_json,'$.name') as title,
        json_extract(config.snapshot_json,'$.command') as command,
        '/scheduled-tasks?id='||run.scheduled_task_id as detail_url
        from ({SCHEDULED_RUN_SQL}) run
        join ({config_projection('scheduled_task_config_versions')}) config on config.id=run.snapshot_id
        left join ({state_projection('dispatcher_claim_leases')}) claim
          on claim.adapter_name='scheduled' and claim.source_id=cast(run.id as text)
          and claim.owner=run.lease_owner and claim.terminal_at=''
        where run.dispatch_status='pending'""",
        )
    )
    from app.email_store import DIRECT_EMAIL_ACTION_SQL, _failed_action_retriable_sql

    classifications = email_classification_projection()
    # This is the current worker's retry predicate, not a second retry policy.
    sources.append(
        (
            "email_actions",
            "邮件操作",
        f"""select mail.action_id as id,mail.status,
        mail.attempt_count as attempts,mail.created_at,mail.updated_at,mail.next_attempt_at as available_at,
        mail.error,mail.started_at,'' as owner,'' as lease_expires_at,
        coalesce(nullif(c.subject,''),mail.action_type) as title,'' as detail_url
        from ({DIRECT_EMAIL_ACTION_SQL}) mail join ({classifications}) c on c.id=mail.classification_id
        where mail.action_plan_id=c.current_action_plan_id and c.status='processed'
          and (mail.status in ('pending','processing') or (mail.status='failed' and {_failed_action_retriable_sql("mail")}))""",
        )
    )
    for kind, label, sql in sources:
        for raw in db.execute(sql).fetchall():
            row = dict(raw)
            state = "active" if row["status"] in ("running", "processing") else "queued"
            if (
                state == "active"
                and kind != "email_actions"
                and not (
                    row["owner"]
                    and row["lease_expires_at"]
                    and _time(row["lease_expires_at"]) > now_text
                )
            ):
                continue
            attempts = row["attempts"]
            queues[state].append(
                {
                    "id": f"{kind}:{row['id']}",
                    "source": label,
                    "title": row["title"] or f"{label} #{row['id']}",
                    "status": row["status"],
                    "retry_number": None
                    if attempts is None
                    else max(
                        0,
                        int(attempts)
                        - (
                            state == "active"
                            and kind
                            in (
                                "task_todo_sync_outbox",
                                "business_task_todo_sync_outbox",
                            )
                        ),
                    ),
                    "agent": "调度器" if kind == "scheduled_task_runs" else "",
                    "runtime_kind": (
                        ("内置命令" if row["command"] else "任务派发")
                        if kind == "scheduled_task_runs" else ""
                    ),
                    "model": "",
                    "stage": (
                        "dispatching" if kind == "scheduled_task_runs" and state == "active"
                        else row["status"]
                    ),
                    "started_at": _time(row["started_at"]),
                    "updated_at": _time(row["updated_at"]),
                    "created_at": _time(row["created_at"]),
                    "available_at": _time(row["available_at"]),
                    "error": str(row["error"] or ""),
                    "detail_url": row["detail_url"],
                }
            )


def _detail_url(db, kind: str, public_id: str, physical_id: int) -> str:
    if kind == "reply_tasks":
        generation = task20_column("reply_tasks", "execution_generation")
        attempt = db.execute(
            f"select a.id from ({REPLY_ATTEMPT_COMMON_SQL}) a "
            "join runs g on g.record_type='agent_runs' and cast(g.legacy_key as integer)=a.agent_run_id "
            f"join tasks t on t.id=g.task_id and t.{generation}=g.execution_generation "
            "where g.task_id=? order by a.id desc limit 1",
            (physical_id,),
        ).fetchone()
        return f"/attempts/{attempt[0]}" if attempt else ""
    if kind == "meeting_alignment_jobs":
        run = db.execute(
            "select id from ("
            + MEETING_RUN_SQL
            + ") where job_id=? order by id desc limit 1",
            (public_id,),
        ).fetchone()
        return f"/meeting-attempts/{run[0]}" if run else ""
    return ""


def register_dashboard_routes(app: FastAPI, *, store_factory: Callable) -> None:
    @app.get("/api/console/dashboard/queues")
    def dashboard_queues(
        state: str = Query("active", pattern="^(active|queued)$"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        store = store_factory()
        now = datetime.now(UTC)
        with store.read_snapshot(), store._connect() as db:
            queues = read_dashboard_queues(db, now=now)
        rows = queues[state]
        start = (page - 1) * page_size
        return {
            "items": rows[start : start + page_size],
            "counts": {name: len(items) for name, items in queues.items()},
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": len(rows),
                "next_cursor": str(page + 1) if start + page_size < len(rows) else "",
                "has_more": start + page_size < len(rows),
                "snapshot_at": now.isoformat(),
            },
        }
