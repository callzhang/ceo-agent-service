"""Explicit repair of historical reply receipts using provider task readback."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.action_storage import (
    CANDIDATE_ACTION_ATTEMPT_SQL, CANDIDATE_EXECUTION_SQL, CANDIDATE_REVIEW_SQL,
    CANDIDATE_SELECTION_SQL, EXTERNAL_ACTION_RESULT_SQL, REVIEW_CANDIDATE_SQL,
    SENT_REPLY_SQL, update_action,
)
from app.agent_contracts import SystemExecutionResult
from app.run_storage import AGENT_RUN_SQL


def collect_native_chat_readback(store, dws, task_id: int) -> dict:
    """Read one reply's exact conversation window and bind it to the current principal."""
    from app.store import REPLY_TASK_SQL

    with store._connect() as db:
        task = db.execute(f"select * from ({REPLY_TASK_SQL}) where id=?", (task_id,)).fetchone()
        if task is None:
            raise ValueError("reply task not found")
        sent_rows = db.execute(
            f"select * from ({SENT_REPLY_SQL}) where channel=? and conversation_id=? "
            "and trigger_message_id=?",
            (task["channel"], task["conversation_id"], task["trigger_message_id"]),
        ).fetchall()
        if len(sent_rows) != 1:
            raise ValueError("reply task does not have one exact sent-reply record")
        sent_at = sent_rows[0]["sent_at"]

    sent_time = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
    if sent_time.tzinfo is None:
        sent_time = sent_time.replace(tzinfo=timezone.utc)
    start = (sent_time - timedelta(minutes=3)).isoformat(timespec="seconds")
    end = (sent_time + timedelta(minutes=3)).isoformat(timespec="seconds")
    payload = dws.run_json([
        dws.dws_bin, "chat", "+chat-messages", "--conversation-id", task["conversation_id"],
        "--start", start, "--end", end, "--order", "asc", "--page-all",
        "--page-limit", "50", "--max-items", "1000", "--no-reactions", "--format", "json",
    ])
    principal_user_id = dws.get_current_user_id()
    principal = dws.get_user_profile(principal_user_id)
    if not principal.open_dingtalk_id:
        raise ValueError("current DingTalk principal has no verified open ID")
    return {
        "task_id": task_id, "payload": payload,
        "queried_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start": start, "end": end,
        "principal_user_id": principal_user_id,
        "principal_open_dingtalk_id": principal.open_dingtalk_id,
    }


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


def apply_native_chat_readback(store, proof: dict) -> bool:
    """Settle one uncertain native reply from a complete, exact DWS history read."""
    from app.store import REPLY_TASK_SQL

    task_id = proof.get("task_id")
    payload = proof.get("payload")
    if not isinstance(task_id, int) or task_id < 1:
        raise ValueError("invalid reply task identity")
    if not isinstance(payload, dict) or payload.get("contractVersion") != "im.message-list.v1":
        raise ValueError("native chat response contract mismatch")
    if (payload.get("complete") is not True or payload.get("partial") is not False
            or payload.get("hasMore") is not False or payload.get("failures") != []
            or payload.get("failedCount") != 0 or payload.get("paginationKnown") is not True
            or payload.get("truncated") is not False
            or payload.get("truncatedByPageLimit") is not False
            or payload.get("truncatedByResultLimit") is not False
            or not isinstance(payload.get("pagesFetched"), int)
            or payload["pagesFetched"] < 1):
        raise ValueError("native chat response is incomplete")
    rows = payload.get("messages")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("native chat response has no complete message list")
    if payload.get("count") != len(rows):
        raise ValueError("native chat response count does not match its message list")
    queried_at = proof.get("queried_at")
    start, end = proof.get("start"), proof.get("end")
    principal_user_id = proof.get("principal_user_id")
    principal_open_id = proof.get("principal_open_dingtalk_id")
    if not all(isinstance(value, str) and value.strip() for value in (
        queried_at, start, end, principal_user_id, principal_open_id,
    )):
        raise ValueError("native chat readback metadata is incomplete")
    query_range = payload.get("queryRange")
    if (not isinstance(query_range, dict) or query_range.get("startTime") != start
            or query_range.get("endTime") != end or query_range.get("order") != "asc"):
        raise ValueError("native chat response range does not match the requested read")

    with store._immediate_write_transaction() as db:
        task = db.execute(
            f"select * from ({REPLY_TASK_SQL}) where id=?", (task_id,)
        ).fetchone()
        if task is not None and task["status"] == "done":
            settled = db.execute(
                f"select 1 from ({SENT_REPLY_SQL}) s join ({EXTERNAL_ACTION_RESULT_SQL}) r "
                "on r.external_action_key=s.external_action_key "
                "where s.channel=? and s.conversation_id=? and s.trigger_message_id=? "
                "and json_extract(r.provider_result_json,'$.verification.source')='native_chat_readback'",
                (task["channel"], task["conversation_id"], task["trigger_message_id"]),
            ).fetchone()
            if settled is not None:
                return False
        if task is None or task["status"] != "failed" or task["error"] != "external_action_uncertain":
            raise ValueError("reply task is no longer the same uncertain failure")
        if task["locked_at"] not in (None, ""):
            raise ValueError("reply task has an active lock")

        sent_rows = db.execute(
            f"select * from ({SENT_REPLY_SQL}) where channel=? and conversation_id=? "
            "and trigger_message_id=?",
            (task["channel"], task["conversation_id"], task["trigger_message_id"]),
        ).fetchall()
        if len(sent_rows) != 1:
            raise ValueError("reply task does not have one exact sent-reply record")
        sent = sent_rows[0]
        def matches_principal(row: dict) -> bool:
            sender_user_id = row.get("senderUserId")
            sender_open_id = row.get("senderOpenDingTalkId", row.get("senderId"))
            return (
                (sender_user_id == principal_user_id or sender_open_id == principal_open_id)
                and (not sender_user_id or sender_user_id == principal_user_id)
                and (not sender_open_id or sender_open_id == principal_open_id)
            )

        matches = [row for row in rows if
            row.get("conversationId") == task["conversation_id"]
            and (row.get("quotedMessage") or {}).get("messageId") == task["trigger_message_id"]
            and row.get("text") == sent["reply_text"]
            and matches_principal(row)]
        if len(matches) != 1:
            raise ValueError("native chat response does not uniquely prove this reply")
        message = matches[0]
        if not isinstance(message.get("messageId"), str) or not message["messageId"].strip():
            raise ValueError("native chat proof has no outgoing message identity")

        receipt = db.execute(
            f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            (sent["external_action_key"],),
        ).fetchone()
        if (receipt is None or receipt["business_object_key"] != task["business_object_key"]
                or receipt["operation"] != "reply_to_message"):
            raise ValueError("external receipt does not belong to this task")
        original = receipt["provider_result_json"]
        old = json.loads(original)
        if old.get("verification", {}).get("state") != "ambiguous":
            raise ValueError("external receipt is not an unresolved ambiguous delivery")
        if old.get("verification_repair", {}).get("source") == "native_chat_readback":
            return False

        candidate = db.execute(
            f"""select c.*, r.id as review_id, r.decision, r.candidate_digest as review_digest,
                       e.id as execution_id, e.status as execution_status,
                       e.lease_owner, e.lease_expires_at, e.selection_id, e.result_json,
                       s.branch_json, a.id as action_attempt_id, a.status as action_status
                from ({CANDIDATE_EXECUTION_SQL}) e
                join ({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id
                join ({CANDIDATE_REVIEW_SQL}) r on r.id=e.review_id
                left join ({CANDIDATE_SELECTION_SQL}) s on s.id=e.selection_id
                join ({CANDIDATE_ACTION_ATTEMPT_SQL}) a on a.execution_id=e.id
                    and a.external_action_key=?
                where c.task_id=? and c.execution_generation=? and c.invalidated_at=''
                  and c.id=(select max(id) from ({REVIEW_CANDIDATE_SQL})
                            where task_id=? and execution_generation=?)""",
            (sent["external_action_key"], task_id, task["execution_generation"],
             task_id, task["execution_generation"]),
        ).fetchall()
        if len(candidate) != 1:
            raise ValueError("reply receipt is not bound to one current approved action")
        candidate = candidate[0]
        if (candidate["decision"] != "approve"
                or candidate["review_digest"] != candidate["candidate_digest"]):
            raise ValueError("current action approval does not match its candidate")
        if receipt["first_agent_run_id"] != candidate["consumer_run_id"]:
            raise ValueError("external receipt is not from the approved candidate run")
        if candidate["execution_status"] not in ("uncertain", "failed") or candidate["lease_owner"]:
            raise ValueError("candidate execution is not safely settleable")
        if candidate["action_status"] != "uncertain":
            raise ValueError("candidate action is not awaiting delivery verification")

        run = db.execute(
            f"select * from ({AGENT_RUN_SQL}) where id=? and reply_task_id=? "
            "and execution_generation=?",
            (receipt["first_agent_run_id"], task_id, task["execution_generation"]),
        ).fetchone()
        if run is None or run["status"] == "running":
            raise ValueError("original Agent run identity is not settled")

        candidate_json = json.loads(candidate["candidate_json"])
        selection = json.loads(candidate["branch_json"]) if candidate["branch_json"] else None
        plan = selection.get("plan") if selection else candidate_json.get("proposal")
        actions = plan.get("actions") if isinstance(plan, dict) else None
        if not isinstance(actions, list) or len(actions) != 1:
            raise ValueError("approved candidate is not a single-action reply")
        attempts = db.execute(
            f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=? "
            "order by action_index", (candidate["execution_id"],),
        ).fetchall()
        if len(attempts) != 1:
            raise ValueError("approved action attempt set is incomplete")
        action_index = next((i for i, attempt in enumerate(attempts)
                             if attempt["external_action_key"] == sent["external_action_key"]), None)
        if action_index is None or action_index >= len(actions):
            raise ValueError("sent reply is not an approved candidate action")
        action = actions[action_index]
        target, body = action.get("target"), action.get("payload")
        if (action.get("operation") != "reply_to_message"
                or not isinstance(target, dict)
                or target.get("conversation_id") != task["conversation_id"]
                or target.get("message_id") != task["trigger_message_id"]
                or not isinstance(body, dict)
                or body.get("content") != sent["reply_text"]):
            raise ValueError("approved candidate does not match the recorded sent reply")
        if json.loads(receipt["target_identifiers_json"]) != target:
            raise ValueError("external receipt target differs from the approved reply target")
        if any(attempt["status"] not in ("verified", "uncertain") for attempt in attempts):
            raise ValueError("candidate has an action with unresolved non-delivery state")

        source_digest = hashlib.sha256(original.encode()).hexdigest()
        proof_digest = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        verification = {
            "state": "sent", "source": "native_chat_readback",
            "message_id": message["messageId"], "queried_at": queried_at,
            "start": start, "end": end,
            "conversation_id": task["conversation_id"],
            "quoted_trigger_id": task["trigger_message_id"],
            "sender_user_id": message.get("senderUserId", ""),
            "sender_open_dingtalk_id": message.get("senderOpenDingTalkId", message.get("senderId", "")),
            "principal_user_id": principal_user_id,
            "principal_open_dingtalk_id": principal_open_id,
            "body_sha256": hashlib.sha256(sent["reply_text"].encode()).hexdigest(),
        }
        changed = {**old, "verification": verification, "verification_repair": {
            **old.get("verification_repair", {}),
            "source": "native_chat_readback", "source_digest": source_digest,
            "queried_at": queried_at, "native_payload_sha256": proof_digest,
            "previous_verification": old.get("verification", {}),
            "native_chat_readback": verification,
            "previous_candidate_execution": {
                "id": candidate["execution_id"], "status": candidate["execution_status"],
                "result_json": candidate["result_json"],
            },
            "previous_candidate_action_attempt": {
                "id": candidate["action_attempt_id"], "status": candidate["action_status"],
            },
            "previous_task_state": {
                "status": task["status"], "error": task["error"],
                "execution_generation": task["execution_generation"],
                "updated_at": task["updated_at"],
            },
        }}
        result_json = json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result_digest = hashlib.sha256(result_json.encode()).hexdigest()
        receipt_cursor = update_action(db, "external_action_results", sent["external_action_key"], {
            "provider_result_json": result_json, "result_digest": result_digest,
        }, where_sql="provider_result_json=?", parameters=(original,))
        if receipt_cursor.rowcount != 1:
            raise ValueError("external receipt changed during reconciliation")

        for attempt in attempts:
            status = "verified" if attempt["id"] == candidate["action_attempt_id"] else attempt["status"]
            result = result_json if attempt["id"] == candidate["action_attempt_id"] else attempt["result_json"]
            update_action(db, "candidate_action_attempts", attempt["id"], {
                "status": status, "result_json": result, "updated_at": queried_at,
            })
        execution_result = SystemExecutionResult(
            outcome="executed", summary="Existing DingTalk reply verified by complete native chat history readback",
            external_result={"operation_id": sent["external_action_key"],
                             "live_result_reference": verification},
            completed_action_keys=(sent["external_action_key"],),
        ).model_dump_json()
        update_action(db, "candidate_executions", candidate["execution_id"], {
            "status": "done", "result_json": execution_result,
            "lease_owner": "", "lease_expires_at": "", "updated_at": queried_at,
        })
        task_cursor = db.execute(
            "update tasks set status='done',error='',available_at='',locked_at=null,updated_at=? "
            "where record_type='reply_tasks' and id=? and status='failed' "
            "and error='external_action_uncertain' and execution_generation=?",
            (queried_at, task_id, task["execution_generation"]),
        )
        if task_cursor.rowcount != 1:
            raise ValueError("reply task changed during reconciliation")
        store._compact_settled_reply_task_input(db, task_id)
        for sent_row in db.execute(
            f"select id from ({SENT_REPLY_SQL}) where external_action_key=?",
            (sent["external_action_key"],),
        ).fetchall():
            update_action(db, "sent_replies", sent_row["id"], {"send_result_json": result_json})
    return True
