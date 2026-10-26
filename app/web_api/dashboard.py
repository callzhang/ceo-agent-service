"""Read-only current execution queues for the Dashboard.

Attention remains owned by its existing endpoint. This projection never reads a
native transcript, claims a task, repairs state, or changes retry policy.
"""

from collections.abc import Callable
from datetime import UTC, datetime
import sqlite3

from fastapi import FastAPI, Query

from app.store import NATIVE_MEETING_RUN_SQL
from app.external_retry import MAX_AUTOMATIC_ATTEMPTS
from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
from app.action_storage import (
    SYNC_ACTION_PROJECTIONS,
)
from app.configs_definitions import config_projection
from app.state_definitions import state_projection
from app.email_canonical_storage import email_classification_projection


# Only executable sources; parent projects, saved inputs and completed history
# are not queue items. These expressions use the current source-owned fields.
TASK_SOURCES = {
    "reply": ("reply_tasks", "消息 / 审批"),
    "work_summary": ("work_summary_inputs", "任务提取"),
    "meeting_alignment": ("meeting_alignment_jobs", "会议同步"),
    "okr_review": ("okr_review_requests", "OKR 审阅"),
    "okr_analysis": ("weekly_okr_analysis_jobs", "每周 OKR"),
    "email_classification": ("email_agent_classification_tasks", "邮件分类"),
    "workbench_turn": ("workbench_turns", "Agent 工作台"),
    "wechat_import": ("wechat_memory_import_jobs", "微信 Memory 导入"),
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
    """Current native runtime metadata keyed by the owning Task ID."""
    rows = db.execute(
        """select runtime.id as runtime_id, runtime.workload_kind,
                  runtime.workload_key, runtime.runtime_kind, runtime.model,
                  runtime.started_at, runtime.updated_at, runtime.status,
                  runtime.lease_owner, parent.role, parent.task_id,
                  domain.task_id as domain_task_id
           from runtime_attempts runtime
           left join runs parent on parent.id=runtime.parent_id
             and parent.status='running'
             and not exists(select 1 from run_provenance p where p.run_id=parent.id)
           left join runs domain on runtime.parent_id is null
             and domain.id=case when runtime.workload_key glob '[0-9]*'
                                then cast(runtime.workload_key as integer) else -1 end
             and exists(select 1 from run_provenance p where p.run_id=domain.id
                        and p.source_type in ('meeting_alignment_runs','task_agent_runs'))
           where runtime.status in ('starting','running')
             and ((runtime.lease_owner<>'' and datetime(runtime.lease_expires_at)>datetime(?))
               or (parent.task_id is not null and exists(
                   select 1 from tasks live where live.id=parent.task_id
                     and live.kind='reply' and live.status='processing'
                     and live.execution_generation=parent.execution_generation
                     and live.lease_owner<>''
                     and datetime(live.lease_expires_at)>datetime(?))))
           order by runtime.id""",
        (now, now),
    ).fetchall()
    result: dict[int, dict] = {}
    for row in rows:
        task_id = row["task_id"] or row["domain_task_id"]
        if task_id is None and row["workload_kind"] in (
            "email_classification", "workbench", "structured", "weekly_okr", "memory",
        ):
            kind = {
                "email_classification": "email_classification",
                "workbench": "workbench_turn",
                "structured": "okr_review",
                "weekly_okr": "okr_analysis",
                "memory": "wechat_import",
            }[row["workload_kind"]]
            if str(row["workload_key"]).isdigit():
                task = db.execute(
                    "select id from tasks where id=? and kind=? and status='processing'",
                    (int(row["workload_key"]), kind),
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
    sources = {
        "reply": ("join task_inputs input on input.id=task.current_input_id and input.task_id=task.id", "input.conversation_title"),
        "work_summary": ("join work_summary_inputs summary on summary.task_id=task.id", "summary.source_ref"),
        "meeting_alignment": ("", "coalesce(json_extract(task.parameters_json,'$.title'),'')"),
        "okr_review": ("", "coalesce(json_extract(task.parameters_json,'$.conversation_title'),'')"),
        "okr_analysis": ("", "coalesce(json_extract(task.parameters_json,'$.week_end'),'')"),
        "email_classification": ("join email_classification_inputs email on email.task_id=task.id", "coalesce(nullif(json_extract(email.input_json,'$.message.subject'),''),email.stable_message_identity)"),
        "workbench_turn": ("join workbench_turns turn on turn.task_id=task.id join workbench_tasks workbench on workbench.id=turn.workbench_task_id", "workbench.title"),
        "wechat_import": ("", "coalesce(json_extract(task.parameters_json,'$.account_id'),'')"),
    }
    for kind, (display_kind, label) in TASK_SOURCES.items():
        join, title = sources[kind]
        delivery = kind == "meeting_alignment"
        states = "('waiting','pending','processing','ready_to_send','retry')" if delivery else "('pending','processing')"
        available = "task.available_at"
        if delivery:
            eligible = "coalesce(json_extract(task.parameters_json,'$.eligible_at'),'')"
            available = f"case when coalesce(datetime(task.available_at),'')>coalesce(datetime({eligible}),'') then task.available_at else {eligible} end"
        current = ""
        if kind == "reply":
            current = """and not exists(select 1 from task_business_keys current_object
                join tasks owner on owner.id=current_object.task_id and owner.kind='reply'
                where current_object.business_key=input.business_object_key and owner.id<>task.id)"""
        rows = db.execute(
            f"""select task.id,task.status,task.attempts,task.error,{available} as available_at,
                       task.created_at,task.updated_at,task.locked_at,task.lease_owner as owner,
                       task.lease_expires_at,{title} as title
                from tasks task {join}
                where task.kind=? and task.status in {states} {current}""",
            (kind,),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            native = runtime.get(row["id"])
            lease_active = bool(
                row["owner"] and row["lease_expires_at"]
                and _time(row["lease_expires_at"]) > now_text
            )
            is_delivery = kind == "meeting_alignment" and row["status"] == "ready_to_send"
            state = "queued" if row["status"] in ("pending", "waiting", "retry") or (is_delivery and not row["locked_at"]) else "active"
            if state == "active" and not (native or lease_active or (is_delivery and row["locked_at"])):
                continue
            attempts = row["attempts"]
            retry = None if attempts is None or is_delivery else max(0, int(attempts) - (state == "active"))
            public_id = str(row["id"])
            queues[state].append({
                "id": f"{display_kind}:{public_id}", "source": label,
                "title": str(row["title"] or f"{label} #{public_id}"),
                "status": row["status"], "retry_number": retry,
                "agent": (native["role"] or native["workload_kind"]) if native else "",
                "runtime_kind": native["runtime_kind"] if native else "",
                "model": native["model"] if native else "",
                "stage": native["status"] if native else row["status"],
                "started_at": _time(native["started_at"] if native else row["locked_at"]),
                "updated_at": _time(native["updated_at"] if native else row["updated_at"]),
                "created_at": _time(row["created_at"]),
                "available_at": _time(row["available_at"]),
                "error": str(row["error"] or ""),
                "detail_url": _detail_url(db, kind, public_id, row["id"]),
            })
    _append_auxiliary_queues(db, queues, now_text)
    for name, rows in queues.items():
        rows.sort(key=lambda item: ((item["available_at"] or item["created_at"], item["id"])
                                    if name == "queued" else (item["started_at"], item["id"])))
    return queues


def _append_auxiliary_queues(db, queues, now_text):
    sources = []
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
        from schedule_occurrences run
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
    if kind == "reply":
        attempt = db.execute(
            f"""select attempts.id from ({REPLY_ATTEMPT_COMMON_SQL}) attempts
                join runs run on run.id=attempts.agent_run_id
                  and not exists(select 1 from run_provenance p where p.run_id=run.id)
                join tasks task on task.id=run.task_id
                  and task.execution_generation=run.execution_generation
                where run.task_id=? order by attempts.id desc limit 1""",
            (physical_id,),
        ).fetchone()
        return f"/attempts/{attempt[0]}" if attempt else ""
    if kind == "meeting_alignment":
        run = db.execute(
            f"select id from ({NATIVE_MEETING_RUN_SQL}) where job_id=? order by id desc limit 1",
            (physical_id,),
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
