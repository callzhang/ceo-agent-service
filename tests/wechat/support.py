"""Test helpers for the WeChat delivery pipeline.

A delivery can be claimed for sending only once the standard Consumer -> Audit
review approved exactly its target and text (``AutoReplyStore.claim_wechat_delivery``
-> ``wechat_delivery_review_approved``). Most tests exercise what happens after that
gate, so ``create_wechat_delivery`` makes a prepared delivery directly and
``approve_delivery`` grants the approval through the real store API: a completed
Consumer run, an adopted candidate with one ``wechat / send_message`` action, a
completed Audit run and the recorded review, and the delivery's ``standard_*`` evidence.
"""

from __future__ import annotations

import json

from app.action_storage import (
    insert_task_delivery_action, task_delivery_set_parts, update_task_delivery_action,
)
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.reviewed_candidates import candidate_digest
from app.store import (
    NATIVE_REPLY_TASK_CURRENT_SQL, WECHAT_DELIVERY_SQL, AgentRole,
)

IDENTITY = "send-wechat-reply"


def create_wechat_delivery(
    store, *, reply_task_id: int, account_id: str, target_type: str, target_id: str,
    conversation_id: str, reply_text: str, evidence: dict[str, str] | None = None,
) -> int:
    """Create (or retry) one generation-scoped delivery holding the prepared final body."""
    with store._immediate_write_transaction() as db:
        task = db.execute(
            f"""select execution_generation, trigger_text from ({NATIVE_REPLY_TASK_CURRENT_SQL}) as reply_tasks
               where id=? and channel='wechat'""",
            (reply_task_id,),
        ).fetchone()
        if task is None:
            raise ValueError("WeChat reply task was not found")
        generation = str(task["execution_generation"])
        store._prepare_new_wechat_delivery(
            db, reply_task_id=reply_task_id, account_id=account_id, target_type=target_type,
            target_id=target_id, conversation_id=conversation_id,
        )
        existing = db.execute(
            f"""select * from ({WECHAT_DELIVERY_SQL})
               where reply_task_id=? and execution_generation=?""",
            (reply_task_id, generation),
        ).fetchone()
        if existing is not None:
            delivery_id = int(existing["id"])
            if existing["status"] == "failed" and bool(existing["pre_action_failure"]):
                assignments, set_args = task_delivery_set_parts(
                    "wechat_deliveries",
                    {"status": "ready_to_send", "pre_action_failure": 0, "error": ""},
                    update_sql={"updated_at": "current_timestamp"},
                )
                db.execute(
                    f"update actions set {assignments} "
                    "where record_type='wechat_deliveries' and cast(legacy_key as integer)=?",
                    (*set_args, delivery_id),
                )
            return delivery_id
        delivery_id = int(insert_task_delivery_action(db, "wechat_deliveries", {
            "reply_task_id": reply_task_id, "account_id": account_id,
            "target_type": target_type, "target_id": target_id,
            "conversation_id": conversation_id, "reply_text": reply_text,
            "execution_generation": generation, "status": "preparing",
            "evidence_json": json.dumps(evidence or {}, ensure_ascii=False),
        }).lastrowid)
        prepared = store._prepare_outbound_postfix_in_transaction(
            db, channel="wechat", delivery_key=f"wechat:{delivery_id}", body=reply_text,
            original_text=str(task["trigger_text"] or ""),
        )
        assignments, set_args = task_delivery_set_parts(
            "wechat_deliveries", {"reply_text": prepared.final_body, "status": "ready_to_send"},
            update_sql={"updated_at": "current_timestamp"},
        )
        db.execute(
            f"update actions set {assignments} "
            "where record_type='wechat_deliveries' and cast(legacy_key as integer)=? "
            "and td_status='preparing'",
            (*set_args, delivery_id),
        )
        return delivery_id


def wechat_result(text: str, target: dict[str, str], *, identity: str = IDENTITY,
                  capability: str = "wechat", operation: str = "send_message"):
    plan = {"objective": "Reply", "actions": [{
        "description": "Reply on WeChat", "action_identity": identity,
        "capability": capability, "operation": operation,
        "target": target, "payload": {"text": text},
    }], "sourced_facts": [], "authored_judgment": "Short acknowledgement"}
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": "Reply", "proposal": plan,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0, "information_completeness": 1.0,
    })


def review_candidate(store, task, result, *, decision: str = "approve"):
    """Run a Consumer and an Audit turn over ``result`` and record the review."""
    revision = len([
        run for run in store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation, load_events=False)
        if run.role is AgentRole.CONSUMER
    ])
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=revision, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, result.model_dump(mode="json"), owner="consumer")
    candidate = store.persist_review_candidate(task, consumer, result)
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=revision, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="audit", owner="audit",
    ).run
    verdict = AuditAgentResult(
        outcome=decision, summary="Reviewed", proposal_revision=revision,
        candidate_digest=candidate_digest(result), evidence_refs=(),
        feedback=None if decision == "approve" else {
            "rule": "Wording", "observation": "Too curt",
            "requested_revision": "Soften the wording"},
        error={"code": "", "retryable": False}, risk="low", confidence=1.0,
        rule_coverage=1.0, information_completeness=1.0,
    ).model_dump(mode="json")
    store.complete_agent_run(audit.id, verdict, owner="audit")
    review = store.record_candidate_review(candidate["id"], audit.id, verdict)
    return candidate, review


def approve_delivery(store, delivery_id: int) -> None:
    """Approve the delivery exactly as it is right now, through the standard review."""
    if store.wechat_delivery_review_approved(delivery_id):
        return
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    task = store.get_reply_task(delivery.task_id)
    result = wechat_result(delivery.reply_text, {
        "account_id": delivery.account_id, "target_type": delivery.target_type,
        "target_id": delivery.target_id, "conversation_id": delivery.conversation_id,
    })
    candidate, review = review_candidate(store, task, result)
    evidence = dict(delivery.evidence)
    evidence.update({
        "standard_candidate_id": str(candidate["id"]),
        "standard_review_id": str(review["id"]),
        "standard_action_identity": IDENTITY,
    })
    with store._connect() as db:
        update_task_delivery_action(
            db, "wechat_deliveries", delivery_id,
            {"evidence_json": json.dumps(evidence, ensure_ascii=False)},
        )
