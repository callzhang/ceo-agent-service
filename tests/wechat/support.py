"""Test helpers for the WeChat pipeline.

A delivery can only be claimed for sending once an independent Audit run has
approved the exact candidate (``AutoReplyStore.claim_wechat_delivery``). Most
tests exercise what happens after that gate, so ``approve_delivery`` grants the
approval through the real store API: a completed Consumer parent run, a completed
Audit run bound to ``wechat-delivery:<id>``, and ``record_wechat_delivery_review``.
"""

from __future__ import annotations

from app.dingtalk_models import CodexAction, CodexDecision
from app.store import AgentRole
from app.wechat.audit import (
    WechatAuditDecision, wechat_audit_candidate, wechat_audit_digest,
)


def _completed_consumer(store, task):
    runs = store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation, load_events=False
    )
    done = [r for r in runs if r.role is AgentRole.CONSUMER and r.status == "completed"]
    if done:
        return max(done, key=lambda run: run.id)
    claim = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="test-consumer",
    )
    store.complete_agent_run(
        claim.run.id,
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="reply",
            reason="test", audit_summary="test",
        ).model_dump(mode="json"),
        owner="test-consumer",
    )
    return store.get_agent_run(claim.run.id)


def approve_delivery(store, delivery_id: int, *, outcome: str = "approve") -> None:
    """Record an independent Audit decision for the delivery as it is right now."""
    if store.wechat_delivery_review_approved(delivery_id):
        return
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    task = store.get_reply_task(delivery.task_id)
    parent = _completed_consumer(store, task)
    decision = WechatAuditDecision(
        outcome=outcome,
        candidate_digest=wechat_audit_digest(wechat_audit_candidate(task, delivery)),
        summary="approved by test helper",
    )
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=f"wechat-delivery:{delivery_id}", owner="test-audit",
    ).run
    store.complete_agent_run(
        audit.id, decision.model_dump(mode="json"), owner="test-audit"
    )
    original = store.get_agent_run

    def native_result(run_id, **kwargs):
        run = original(run_id, **kwargs)
        if run_id == audit.id:
            return run.model_copy(update={"final_result_json": decision.model_dump_json()})
        return run

    store.get_agent_run = native_result
    try:
        store.record_wechat_delivery_review(delivery_id, audit.id, decision)
    finally:
        store.get_agent_run = original
