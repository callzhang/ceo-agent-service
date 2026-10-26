"""Attention rows for a report schedule whose planned run delivered no report.

A failed Reply task already has its own Attention row. These rows cover the
runs that end without a task failure and so never appeared: the run was skipped
because the runtime or a Skill was unavailable (no task exists), the dispatch
itself failed, or the task ended `skipped`. The row stays until a later run of
the same schedule, planned or manual, ends `done`. A display projection only: it
changes no task, occurrence or schedule.
"""

from __future__ import annotations

from app.agent_cron.scheduler import PREVIOUS_EXECUTION_ACTIVE
from app.agent_cron.seeds import REPORT_SCHEDULE_MIGRATION_KEYS
from app.store import NATIVE_REPLY_TASK_CURRENT_SQL, AutoReplyStore


def report_schedule_attention_rows(store: AutoReplyStore) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    keys = sorted(REPORT_SCHEDULE_MIGRATION_KEYS)
    with store._connect() as db:
        schedules = db.execute(
            "select id,name from schedules where enabled=1 and migration_key in "
            f"({','.join('?' for _ in keys)})",
            keys,
        ).fetchall()
        for schedule in schedules:
            latest = db.execute(
                "select o.id,o.scheduled_for,o.created_at,o.dispatch_status,"
                "o.skip_or_error_reason,o.execution_kind,o.execution_id,"
                "t.status as task_status,t.error as task_error "
                "from schedule_occurrences o "
                f"left join ({NATIVE_REPLY_TASK_CURRENT_SQL}) t "
                "on o.execution_kind='reply_task' and t.id=cast(o.execution_id as integer) "
                "where o.scheduled_task_id=? and o.trigger_kind='scheduled' "
                "and o.skip_or_error_reason<>? order by o.id desc limit 1",
                (schedule["id"], PREVIOUS_EXECUTION_ACTIVE),
            ).fetchone()
            if latest is None:
                continue
            status = latest["dispatch_status"]
            task_status = latest["task_status"]
            if status == "failed" or status == "skipped":
                detail = latest["skip_or_error_reason"]
            elif status == "dispatched" and task_status == "skipped":
                detail = latest["task_error"] or "任务以「已跳过」结束，没有产出报告。"
            else:
                continue
            delivered_later = db.execute(
                "select 1 from schedule_occurrences o "
                f"join ({NATIVE_REPLY_TASK_CURRENT_SQL}) t "
                "on o.execution_kind='reply_task' and t.id=cast(o.execution_id as integer) "
                "where o.scheduled_task_id=? and o.id>? and o.dispatch_status='dispatched' "
                "and t.status='done' limit 1",
                (schedule["id"], latest["id"]),
            ).fetchone()
            if delivered_later:
                continue
            rows.append({
                "category": "Scheduled task",
                "id": f"report-schedule:{latest['id']}",
                "status": "failed",
                "context": schedule["name"],
                "root_cause": "scheduled report not delivered",
                "summary": f"{schedule['name']}：{latest['scheduled_for']} 的计划运行没有产出报告",
                "updated_at": latest["created_at"],
                "error": detail or "没有产出报告",
                "detail_url": f"/scheduled-tasks?id={schedule['id']}",
            })
    return rows
