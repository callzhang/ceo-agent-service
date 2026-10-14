"""Explicit repair of historical reply receipts using provider task readback."""

import hashlib
import json

from app.action_storage import (
    CANDIDATE_ACTION_ATTEMPT_SQL, CANDIDATE_EXECUTION_SQL, EXTERNAL_ACTION_RESULT_SQL,
    SENT_REPLY_SQL, update_action,
)
from app.agent_contracts import SystemExecutionResult
from app.run_storage import AGENT_RUN_SQL


def apply_delivery_reverification(store, proof: dict) -> bool:
    """Apply one captured task query without running or dispatching an Agent."""
    key, original = proof["key"], proof["original_provider_json"]
    source_digest = hashlib.sha256(original.encode()).hexdigest()
    old = json.loads(original)
    task_id = old["provider_result"]["result"]["openTaskId"]
    verification = proof["verification"]
    if verification["open_task_id"] != task_id:
        raise ValueError("delivery query task identity mismatch")
    if verification["state"] not in ("sent", "ambiguous", "failed"):
        raise ValueError("invalid delivery verification state")
    action_status = {"sent": "verified", "ambiguous": "uncertain", "failed": "failed"}[verification["state"]]
    error_code = "external_action_uncertain" if action_status == "uncertain" else "external_action_failed"
    with store._immediate_write_transaction() as db:
        row = db.execute(f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?", (key,)).fetchone()
        if row is None:
            raise ValueError("delivery receipt missing")
        current = json.loads(row["provider_result_json"])
        if current.get("verification_repair", {}).get("source_digest") == source_digest:
            return False
        if row["provider_result_json"] != original:
            raise ValueError("delivery receipt changed")
        if not old["provider_result"].get("referencedMessage", {}).get("messageId"):
            raise ValueError("receipt is not a native reply result")
        before = old["verification"]
        if before["state"] != "sent" or before.get("open_task_id") or before.get("status_result"):
            raise ValueError("receipt already has delivery evidence")
        changed = {**old, "verification": verification, "verification_repair": {
            "source_digest": source_digest, "queried_at": proof["queried_at"],
            "previous_verification": before, "previous_execution_results": [], "previous_task_state": None,
        }}
        attempts = db.execute(f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where external_action_key=?", (key,)).fetchall()
        for attempt in attempts:
            if verification["state"] != "sent":
                execution = db.execute(f"select * from ({CANDIDATE_EXECUTION_SQL}) where id=?", (attempt["execution_id"],)).fetchone()
                if execution["status"] == "running":
                    raise ValueError("delivery execution is active")
                changed["verification_repair"]["previous_execution_results"].append({
                    "execution_id": execution["id"], "status": execution["status"],
                    "result_json": execution["result_json"],
                })
                pending = SystemExecutionResult(
                    outcome="failed", summary="Historical delivery is not verified by provider readback",
                    error={"code": error_code, "retryable": False,
                           "stage": "system_execution", "authorization_required": False},
                )
                update_action(db, "candidate_executions", execution["id"], {
                    "status": action_status, "result_json": pending.model_dump_json(),
                    "lease_owner": "", "lease_expires_at": "", "updated_at": proof["queried_at"],
                })
        if verification["state"] != "sent":
            # Withdraw only a completion of this exact generation. Original
            # Agent runs and historical Attempt rows are never rewritten.
            run = db.execute(f"select * from ({AGENT_RUN_SQL}) where id=?", (row["first_agent_run_id"],)).fetchone()
            if run is not None:
                task = db.execute("select * from tasks where record_type='reply_tasks' and legacy_key=?", (str(run["reply_task_id"]),)).fetchone()
                if task is not None and task["execution_generation"] == run["execution_generation"]:
                    if task["status"] in ("pending", "processing"):
                        raise ValueError("delivery task is active")
                    if task["status"] == "done":
                        changed["verification_repair"]["previous_task_state"] = {
                            "id": run["reply_task_id"], "execution_generation": task["execution_generation"],
                            "status": task["status"], "error": task["error"],
                        }
                        db.execute("update tasks set status='failed',error=?,updated_at=? where id=?",
                                   (error_code, proof["queried_at"], task["id"]))
        body = json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        update_action(db, "external_action_results", key, {
            "provider_result_json": body, "result_digest": hashlib.sha256(body.encode()).hexdigest(),
        })
        for attempt in attempts:
            update_action(db, "candidate_action_attempts", attempt["id"], {
                "result_json": body, "status": action_status,
                "updated_at": proof["queried_at"],
            })
        for sent in db.execute(f"select id from ({SENT_REPLY_SQL}) where external_action_key=?", (key,)).fetchall():
            update_action(db, "sent_replies", sent["id"], {"send_result_json": body})
    return True
