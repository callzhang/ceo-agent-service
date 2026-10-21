"""Explicit repair of historical reply receipts using provider task readback."""

from app.model_action_schema import selected_branch
from app.execution_owner_storage import update_plan_execution

import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.action_storage import (
    CANDIDATE_ACTION_ATTEMPT_SQL, candidate_action_attempt_row, CANDIDATE_EXECUTION_SQL, CANDIDATE_REVIEW_SQL,
    CANDIDATE_SELECTION_SQL, EXTERNAL_ACTION_RESULT_SQL, REVIEW_CANDIDATE_SQL,
    REPLY_ATTEMPT_COMMON_SQL, SENT_REPLY_SQL,
)
from app.agent_contracts import SystemExecutionResult
from app.logical_run_storage import AGENT_RUN_SQL
from app.task20_definitions import task20_column, task20_projection
from app.task8_storage import task8_set_parts

from app import shared_action_lifecycle


REPLY_TASK_SQL = task20_projection('reply_tasks')



def _same_timestamp_instant(actual: object, expected: object) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    try:
        actual_time = datetime.fromisoformat(actual.replace("Z", "+00:00"))
        expected_time = datetime.fromisoformat(expected.replace("Z", "+00:00"))
    except ValueError:
        return False
    if actual_time.tzinfo is None or expected_time.tzinfo is None:
        return False
    return actual_time.astimezone(timezone.utc) == expected_time.astimezone(timezone.utc)


def native_reply_text_matches(actual: object, sent_body: object, trigger_sender: object) -> bool:
    """Match a stored reply or its exact native quoted-sender rendering."""
    if not isinstance(actual, str) or not isinstance(sent_body, str):
        return False
    normalized_actual = "".join(actual.split())
    normalized_body = "".join(sent_body.split())
    if normalized_actual == normalized_body:
        return True
    if not isinstance(trigger_sender, str) or not trigger_sender:
        return False
    expected = f"@{trigger_sender} {sent_body}"
    return normalized_actual == "".join(expected.split())


def collect_native_chat_readback(store, dws, task_id: int) -> dict:
    """Read one reply's exact conversation window and bind it to the current principal."""
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
        attempts = [candidate_action_attempt_row(row) for row in db.execute(f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where external_action_key=?", (key,))]
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
                update_plan_execution(db, execution["id"], {
                    "status": action_status, "result_json": pending.model_dump_json(),
                    "lease_owner": "", "lease_expires_at": "", "updated_at": proof["queried_at"],
                })
        if verification["state"] != "sent":
            # Withdraw only a completion of this exact generation. Original
            # Agent runs and historical Attempt rows are never rewritten.
            run = db.execute(f"select * from ({AGENT_RUN_SQL}) where id=?", (row["first_agent_run_id"],)).fetchone()
            if run is not None:
                task = db.execute(f"select tasks.*, {task20_column('reply_tasks', 'execution_generation')} as execution_generation, {task20_column('reply_tasks', 'status')} as status, {task20_column('reply_tasks', 'error')} as error from tasks where record_type='reply_tasks' and legacy_key=?", (str(run["reply_task_id"]),)).fetchone()
                if task is not None and task["execution_generation"] == run["execution_generation"]:
                    if task["status"] in ("pending", "processing"):
                        raise ValueError("delivery task is active")
                    if task["status"] == "done":
                        changed["verification_repair"]["previous_task_state"] = {
                            "id": run["reply_task_id"], "execution_generation": task["execution_generation"],
                            "status": task["status"], "error": task["error"],
                        }
                        db.execute(f"""update tasks set {task8_set_parts('reply_tasks', {}, update_sql={'status': "'failed'", 'error': '?', 'updated_at': '?'}).sql} where id=?""",
                                   (error_code, proof["queried_at"], task["id"]))
        body = json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        provider_receipt_id = shared_action_lifecycle.record_reviewed_provider_receipt(
            db, external_action_key=key, operation=row["operation"],
            target_json=row["target_identifiers_json"], provider_json=body,
            result_digest=hashlib.sha256(body.encode()).hexdigest(),
            agent_run_id=row["first_agent_run_id"], observed_at=proof["queried_at"],
            status=action_status,
            supersedes_provider_receipt_id=row["provider_receipt_id"],
        )
        for attempt in attempts:
            shared_action_lifecycle.record_reviewed_action_outcome(
                db, attempt=attempt, result_json=None if action_status == "verified" else body,
                provider_receipt_id=provider_receipt_id if action_status == "verified" else None,
                status=action_status, agent_run_id=attempt["agent_run_id"],
                updated_at=proof["queried_at"],
            )
    return True


def apply_native_chat_readback(store, proof: dict) -> bool:
    """Settle one uncertain native reply from a complete, exact DWS history read."""
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
    if (not isinstance(query_range, dict)
            or not _same_timestamp_instant(query_range.get("startTime"), start)
            or not _same_timestamp_instant(query_range.get("endTime"), end)
            or query_range.get("order") != "asc"):
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
            and native_reply_text_matches(
                row.get("text"), sent["reply_text"], task["trigger_sender"]
            )
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
        if receipt is None or receipt["business_object_key"] != task["business_object_key"]:
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
                       s.option_key, a.id as action_attempt_id, a.status as action_status
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
        if not candidate:
            historical_candidates = db.execute(
                f"select id from ({REVIEW_CANDIDATE_SQL}) where task_id=?", (task_id,)
            ).fetchall()
            attempts = db.execute(
                f"select * from ({REPLY_ATTEMPT_COMMON_SQL}) where agent_run_id=? "
                "and channel=? and conversation_id=? and trigger_message_id=?",
                (receipt["first_agent_run_id"], task["channel"], task["conversation_id"],
                 task["trigger_message_id"]),
            ).fetchall()
            run = db.execute(
                f"select * from ({AGENT_RUN_SQL}) where id=? and reply_task_id=? "
                "and execution_generation=?",
                (receipt["first_agent_run_id"], task_id, task["execution_generation"]),
            ).fetchone()
            expected_target = {
                "conversation_id": task["conversation_id"],
                "message_id": task["trigger_message_id"],
            }
            if (historical_candidates or len(attempts) != 1 or run is None
                    or run["status"] != "completed"
                    or attempts[0]["send_status"] != "completed"
                    or attempts[0]["permission_action"] != ""
                    or attempts[0]["reviewed_at"] is not None
                    or attempts[0]["audit_tool_events_json"] not in (None, "", "[]")
                    or attempts[0]["audit_documents_json"] not in (None, "", "[]")
                    or json.loads(receipt["target_identifiers_json"]) != expected_target):
                raise ValueError("legacy reply lacks one exact completed run and delivery record")
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
                "approval_reconstructed": False,
                "settlement_basis": "verified_existing_legacy_reply_delivery",
                "previous_task_state": {
                    "status": task["status"], "error": task["error"],
                    "execution_generation": task["execution_generation"],
                    "updated_at": task["updated_at"],
                },
            }}
            result_json = json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            shared_action_lifecycle.record_reviewed_provider_receipt(
                db, external_action_key=sent["external_action_key"],
                operation=receipt["operation"],
                target_json=receipt["target_identifiers_json"],
                provider_json=result_json,
                result_digest=hashlib.sha256(result_json.encode()).hexdigest(),
                agent_run_id=receipt["first_agent_run_id"], observed_at=queried_at,
                status="verified",
                supersedes_provider_receipt_id=receipt["provider_receipt_id"],
            )
            task_cursor = db.execute(
                f"""update tasks set {task8_set_parts('reply_tasks', {}, update_sql={'status': "'done'", 'error': "''", 'available_at': "''", 'locked_at': 'null', 'updated_at': '?'}).sql} where record_type='reply_tasks' and id=? and {task20_column('reply_tasks', 'status')}='failed' and {task20_column('reply_tasks', 'error')}='external_action_uncertain' and {task20_column('reply_tasks', 'execution_generation')}=?""",
                (queried_at, task_id, task["execution_generation"]),
            )
            if task_cursor.rowcount != 1:
                raise ValueError("legacy reply task changed during reconciliation")
            store._compact_settled_reply_task_input(db, task_id)
            return True
        if len(candidate) != 1:
            raise ValueError("reply receipt is not bound to one current approved action")
        candidate = candidate[0]
        if receipt["operation"] != "reply_to_message":
            raise ValueError("approved reply receipt operation does not match")
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
        selection = selected_branch(db, candidate["selection_id"]) if candidate["selection_id"] is not None else None
        plan = selection.get("plan") if selection else candidate_json.get("proposal")
        actions = plan.get("actions") if isinstance(plan, dict) else None
        if not isinstance(actions, list) or len(actions) != 1:
            raise ValueError("approved candidate is not a single-action reply")
        attempts = [candidate_action_attempt_row(row) for row in db.execute(
            f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=? "
            "order by action_index", (candidate["execution_id"],),
        )]
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
        provider_receipt_id = shared_action_lifecycle.record_reviewed_provider_receipt(
            db, external_action_key=sent["external_action_key"],
            operation=receipt["operation"], target_json=receipt["target_identifiers_json"],
            provider_json=result_json, result_digest=result_digest,
            agent_run_id=receipt["first_agent_run_id"], observed_at=queried_at,
            status="verified",
            supersedes_provider_receipt_id=receipt["provider_receipt_id"],
        )

        for attempt in attempts:
            status = "verified" if attempt["id"] == candidate["action_attempt_id"] else attempt["status"]
            confirmed = attempt["id"] == candidate["action_attempt_id"]
            result = None if confirmed else attempt["result_json"]
            shared_action_lifecycle.record_reviewed_action_outcome(
                db, attempt=attempt, status=status, result_json=result,
                provider_receipt_id=(provider_receipt_id if confirmed else attempt["provider_receipt_id"]),
                agent_run_id=attempt["agent_run_id"], updated_at=queried_at,
            )
        execution_result = SystemExecutionResult(
            outcome="executed", summary="Existing DingTalk reply verified by complete native chat history readback",
            external_result={"operation_id": sent["external_action_key"],
                             "live_result_reference": verification},
            completed_action_keys=(sent["external_action_key"],),
        ).model_dump_json()
        update_plan_execution(db, candidate["execution_id"], {
            "status": "done", "result_json": execution_result,
            "lease_owner": "", "lease_expires_at": "", "updated_at": queried_at,
        })
        task_cursor = db.execute(
            f"""update tasks set {task8_set_parts('reply_tasks', {}, update_sql={'status': "'done'", 'error': "''", 'available_at': "''", 'locked_at': 'null', 'updated_at': '?'}).sql} where record_type='reply_tasks' and id=? and {task20_column('reply_tasks', 'status')}='failed' and {task20_column('reply_tasks', 'error')}='external_action_uncertain' and {task20_column('reply_tasks', 'execution_generation')}=?""",
            (queried_at, task_id, task["execution_generation"]),
        )
        if task_cursor.rowcount != 1:
            raise ValueError("reply task changed during reconciliation")
        store._compact_settled_reply_task_input(db, task_id)
    return True
