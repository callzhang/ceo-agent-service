"""History reads a reply task as one record; its runs and attempts hang off it.

Derek, 2026-10-23: History is organised by task, not by attempt.  A list row
names the task it belongs to and how many runs it has had, and opens a task
page that lists those runs and attempts.  Everything here is a read-only
projection of stored rows.
"""

import json
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
from app.web_api.common import json_safe, normalize_display_value, snapshot_at

_CHUNK = 500


def _chunks(ids: list[int]):
    for start in range(0, len(ids), _CHUNK):
        yield ids[start:start + _CHUNK]


def annotate_task_identity(store: Any, items: list[dict[str, Any]]) -> None:
    """Add `task_id` and `run_count` to each reply row of one History page."""
    reply_items = {
        int(item["id"]): item
        for item in items
        if item.get("kind") == "reply" and str(item.get("id", "")).isdigit()
    }
    if not reply_items:
        return
    attempt_task: dict[int, int] = {}
    run_counts: dict[int, int] = {}
    with store._connect() as db:
        for chunk in _chunks(list(reply_items)):
            marks = ",".join("?" for _ in chunk)
            # An attempt with no run link (most stored ones) belongs to the task
            # of the same message, as the Attempt detail page resolves it.
            for row in db.execute(
                f"""select attempts.id as attempt_id,
                           coalesce(runs.task_id, (
                               select max(tasks.id)
                               from tasks
                               join task_inputs inputs on inputs.id=tasks.current_input_id
                                 and inputs.task_id=tasks.id
                               where tasks.kind='reply'
                                 and inputs.channel=attempts.channel
                                 and inputs.conversation_id=attempts.conversation_id
                                 and inputs.trigger_message_id=attempts.trigger_message_id
                           )) as task_id
                    from ({REPLY_ATTEMPT_COMMON_SQL}) as attempts
                    left join runs on runs.id=attempts.agent_run_id
                      and not exists (select 1 from run_provenance p where p.run_id=runs.id)
                    where attempts.id in ({marks})""",
                chunk,
            ).fetchall():
                if row["task_id"] is not None:
                    attempt_task[int(row["attempt_id"])] = int(row["task_id"])
        for chunk in _chunks(sorted(set(attempt_task.values()))):
            marks = ",".join("?" for _ in chunk)
            for row in db.execute(
                f"""select task_id as reply_task_id, count(*) as run_count
                    from runs as agent_runs
                    where not exists (select 1 from run_provenance p where p.run_id=agent_runs.id)
                      and task_id in ({marks}) group by task_id""",
                chunk,
            ).fetchall():
                run_counts[int(row["reply_task_id"])] = int(row["run_count"])
    for attempt_id, item in reply_items.items():
        task_id = attempt_task.get(attempt_id)
        if task_id is None:
            continue
        item["task_id"] = task_id
        item["run_count"] = run_counts.get(task_id, 0)
        item["detail_url"] = f"/history/tasks/{task_id}"


def build_task_history(store: Any, task_id: int) -> dict[str, Any] | None:
    with store._connect() as db:
        task = db.execute(
                """select tasks.*, inputs.channel, inputs.conversation_id,
                          inputs.conversation_title, inputs.trigger_sender,
                          inputs.trigger_text, inputs.trigger_message_id
                     from tasks join task_inputs inputs
                       on inputs.id=tasks.current_input_id and inputs.task_id=tasks.id
                    where tasks.id=? and tasks.kind='reply'""",
            (task_id,),
        ).fetchone()
        if task is None:
            return None
        runs = db.execute(
            """select id, execution_generation, role, proposal_revision,
                       turn_attempt, status, started_at, completed_at, created_at
                from runs as agent_runs
                where not exists (select 1 from run_provenance p where p.run_id=agent_runs.id)
                  and task_id=? order by id""",
            (task_id,),
        ).fetchall()
        attempts = db.execute(
            f"""select id, agent_run_id, action, send_status, updated_at,
                       final_reply_text, audit_summary, send_error
                from ({REPLY_ATTEMPT_COMMON_SQL}) as attempts
                where channel=? and conversation_id=? and trigger_message_id=?""",
            (task["channel"], task["conversation_id"], task["trigger_message_id"]),
        ).fetchall()
        attempts += [
            row for row in db.execute(
                f"""select id, agent_run_id, action, send_status, updated_at,
                           final_reply_text, audit_summary, send_error
                    from ({REPLY_ATTEMPT_COMMON_SQL}) as attempts
                    where agent_run_id in (select id from runs where task_id=?
                      and not exists (select 1 from run_provenance p where p.run_id=runs.id))""",
                (task_id,),
            ).fetchall()
            if int(row["id"]) not in {int(a["id"]) for a in attempts}
        ]
    attempts.sort(key=lambda row: int(row["id"]))
    current_generation = str(task["execution_generation"] or "")
    return {
        "task": {
            "id": int(task["id"]),
            "title": normalize_display_value(task["conversation_title"]),
            "channel": str(task["channel"] or ""),
            "sender": normalize_display_value(task["trigger_sender"]),
            "trigger_text": normalize_display_value(task["trigger_text"]),
            "status": str(task["status"] or ""),
            "error": normalize_display_value(task["error"]),
            "execution_generation": current_generation,
            "created_at": str(task["created_at"] or ""),
            "updated_at": str(task["updated_at"] or ""),
        },
        "runs": [
            {
                "id": int(run["id"]),
                "execution_generation": str(run["execution_generation"] or ""),
                "current": str(run["execution_generation"] or "") == current_generation,
                "role": str(run["role"] or ""),
                "proposal_revision": int(run["proposal_revision"] or 0),
                "turn_attempt": int(run["turn_attempt"] or 0),
                "status": str(run["status"] or ""),
                "started_at": str(run["started_at"] or run["created_at"] or ""),
                "completed_at": str(run["completed_at"] or ""),
            }
            for run in runs
        ],
        "attempts": [
            {
                "id": int(attempt["id"]),
                "agent_run_id": int(attempt["agent_run_id"] or 0),
                "action": str(attempt["action"] or ""),
                "status": str(attempt["send_status"] or ""),
                "updated_at": str(attempt["updated_at"] or ""),
                "summary": normalize_display_value(
                    attempt["final_reply_text"] or attempt["audit_summary"] or attempt["send_error"]
                ),
                "detail_url": f"/attempts/{int(attempt['id'])}",
            }
            for attempt in attempts
        ],
    }


def register_history_task_routes(app: FastAPI, store_factory: Any) -> None:
    @app.get("/api/console/history/tasks/{task_id}")
    def console_history_task(task_id: int):
        payload = build_task_history(store_factory(), task_id)
        if payload is None:
            return JSONResponse(
                {"ok": False, "code": "not_found", "message": "Task not found", "details": {}},
                status_code=404,
            )
        return {"item": json_safe(payload), "meta": {"snapshot_at": snapshot_at()}}

    @app.post("/api/console/history/tasks/{task_id}/retry")
    def console_history_task_retry(task_id: int):
        store = store_factory()
        task = store.get_reply_task(task_id)
        if task is None:
            return JSONResponse(
                {"ok": False, "code": "not_found", "message": "Task not found", "details": {}},
                status_code=404,
            )
        if task.status != "failed":
            return JSONResponse(
                {
                    "ok": False,
                    "code": "reply_task_retry_not_allowed",
                    "message": "Only a failed Reply Task can be retried",
                    "details": {},
                },
                status_code=409,
            )
        runs = store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation, load_events=False
        )
        latest = runs[-1] if runs else None
        if latest is None or latest.status != "failed":
            return JSONResponse(
                {
                    "ok": False,
                    "code": "reply_task_retry_not_allowed",
                    "message": "The current Task generation has no failed latest Agent run",
                    "details": {},
                },
                status_code=409,
            )
        try:
            error = json.loads(latest.structured_error_json or "{}")
        except (TypeError, json.JSONDecodeError):
            error = {}
        code = str(error.get("code") or "").strip() if isinstance(error, dict) else ""
        if not code or error.get("retryable") is not True:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "reply_task_retry_not_allowed",
                    "message": "The latest Agent failure is not marked retryable",
                    "details": {},
                },
                status_code=409,
            )
        from app.agent_reported_error import (
            PROVIDER_RISK_REJECTION_RERUN_BLOCK_REASON,
            is_provider_risk_rejection,
        )

        if is_provider_risk_rejection(
            latest.structured_error_json, attempt_error=task.error
        ):
            return JSONResponse(
                {
                    "ok": False,
                    "code": "provider_risk_rejected",
                    "message": PROVIDER_RISK_REJECTION_RERUN_BLOCK_REASON,
                    "details": {},
                },
                status_code=409,
            )
        try:
            retried = store.retry_failed_reply_task(
                task.id,
                latest.id,
                reason=f"task_retry:{code}",
                recovery_code=code,
            )
        except ValueError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "reply_task_retry_not_allowed",
                    "message": str(exc),
                    "details": {},
                },
                status_code=409,
            )
        return {
            "item": {
                "task_id": retried.id,
                "status": retried.status,
                "execution_generation": retried.execution_generation,
                "retried_run_id": latest.id,
                "failure_code": code,
            },
            "meta": {"snapshot_at": snapshot_at()},
        }
