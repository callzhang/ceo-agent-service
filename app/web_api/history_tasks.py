"""History reads a reply task as one record; its runs and attempts hang off it.

Derek, 2026-10-23: History is organised by task, not by attempt.  A list row
names the task it belongs to and how many runs it has had, and opens a task
page that lists those runs and attempts.  Everything here is a read-only
projection of stored rows.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
from app.run_storage import AGENT_RUN_SQL
from app.store import REPLY_TASK_CURRENT_SQL
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
            for row in db.execute(
                f"""select attempts.id as attempt_id, runs.reply_task_id as task_id
                    from ({REPLY_ATTEMPT_COMMON_SQL}) as attempts
                    join ({AGENT_RUN_SQL}) as runs on runs.id=attempts.agent_run_id
                    where attempts.id in ({marks})""",
                chunk,
            ).fetchall():
                attempt_task[int(row["attempt_id"])] = int(row["task_id"])
        for chunk in _chunks(sorted(set(attempt_task.values()))):
            marks = ",".join("?" for _ in chunk)
            for row in db.execute(
                f"""select reply_task_id, count(*) as run_count
                    from ({AGENT_RUN_SQL}) as agent_runs
                    where reply_task_id in ({marks}) group by reply_task_id""",
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
            f"select * from ({REPLY_TASK_CURRENT_SQL}) as reply_tasks where id=?",
            (task_id,),
        ).fetchone()
        if task is None:
            return None
        runs = db.execute(
            f"""select id, execution_generation, role, proposal_revision,
                       turn_attempt, status, started_at, completed_at, created_at
                from ({AGENT_RUN_SQL}) as agent_runs
                where reply_task_id=? order by id""",
            (task_id,),
        ).fetchall()
        run_ids = [int(run["id"]) for run in runs]
        attempts = []
        for chunk in _chunks(run_ids):
            marks = ",".join("?" for _ in chunk)
            attempts.extend(db.execute(
                f"""select id, agent_run_id, action, send_status, updated_at,
                           final_reply_text, audit_summary, send_error
                    from ({REPLY_ATTEMPT_COMMON_SQL}) as attempts
                    where agent_run_id in ({marks})""",
                chunk,
            ).fetchall())
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
                "agent_run_id": int(attempt["agent_run_id"]),
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
