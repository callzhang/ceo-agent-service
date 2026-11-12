"""No-send reconciliation for a failed, reviewed WeChat Reply Task."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from app.agent_contracts import parse_persisted_consumer_result
from app.system_action_handlers import WechatSendMessageHandler


NO_SEND_READBACK_FIELDS = (
    "account_id", "conversation_id", "target_type", "target_id",
    "execution_generation", "action_started_at", "status", "error",
)


def _settle_skipped_chain(store, handler, task, deliveries):
    readbacks = []
    root = deliveries[0]
    for delivery in deliveries:
        result = handler._readback_delivery(delivery, delivery.reply_text, require_scope=True)
        readback = result.provider_result
        if (result.status != "uncertain"
                or readback.get("match_count") != 0
                or readback.get("window_complete") is not True):
            return {
                "outcome": "failed", "task_id": task.id, "delivery_id": root.id,
                "reason": "wechat_no_send_readback_unproven",
            }
        readbacks.append({
            **readback,
            "reply_text_sha256": sha256(delivery.reply_text.encode()).hexdigest(),
            **{field: getattr(delivery, field) for field in NO_SEND_READBACK_FIELDS},
        })
    evidence = {"delivery_id": root.id, "no_send_readbacks": readbacks}
    settled = store.settle_failed_wechat_task_from_delivery(
        task.id, expected_execution_generation=task.execution_generation,
        delivery_id=root.id, task_status="skipped", evidence=evidence,
    )
    return {
        "outcome": "skipped" if settled else "failed",
        "task_id": task.id, "delivery_id": root.id, "evidence": evidence,
    }


def _current_wechat_action(store: Any, task_id: int):
    task = store.get_reply_task(task_id)
    if task is None or task.channel != "wechat":
        raise ValueError("wechat_task_missing")
    review = store.current_reviewed_candidate(task.id, task.execution_generation)
    if (review is None or review.get("decision") != "approve"
            or review.get("execution_generation") != task.execution_generation):
        raise ValueError("wechat_review_not_current")
    try:
        candidate = parse_persisted_consumer_result(review["candidate_json"])
    except (KeyError, ValueError):
        raise ValueError("wechat_candidate_invalid") from None
    proposal = candidate.proposal
    if proposal is None or len(proposal.actions) != 1:
        raise ValueError("wechat_action_not_unique")
    action = proposal.actions[0]
    if action.capability != "wechat" or action.operation != "send_message":
        raise ValueError("wechat_action_mismatch")
    delivery = store.get_wechat_delivery_for_task(task.id)
    if (delivery is None or delivery.execution_generation != task.execution_generation
            or delivery.reply_text != action.payload.get("text")
            or action.target.get("account_id") != delivery.account_id
            or action.target.get("target_type") != delivery.target_type
            or action.target.get("target_id") != delivery.target_id
            or action.target.get("conversation_id") != delivery.conversation_id
            or not store.wechat_delivery_review_approved(delivery.id)):
        raise ValueError("wechat_delivery_candidate_mismatch")
    evidence = delivery.evidence
    if (evidence.get("standard_candidate_id") != str(review["candidate_id"])
            or evidence.get("standard_review_id") != str(review["review_id"])
            or evidence.get("standard_action_identity") != action.action_identity):
        raise ValueError("wechat_delivery_review_mismatch")
    return task, review, action, delivery


def _settle_sent_delivery(store, handler, task, review, action, delivery):
    result = handler.reconcile(action, action_key="", candidate=review)
    if result is None or result.status != "verified":
        return {
            "outcome": "failed", "task_id": task.id,
            "reason": result.provider_result.get("reason") if result else "wechat_readback_missing",
        }
    settled = store.settle_failed_wechat_task_from_delivery(
        task.id, expected_execution_generation=task.execution_generation,
        delivery_id=delivery.id, task_status="done", evidence=result.provider_result,
    )
    return {
        "outcome": "done" if settled else "failed", "task_id": task.id,
        "delivery_id": delivery.id, "evidence": result.provider_result,
    }


def reconcile_sent_wechat_tasks(store, reader, account) -> None:
    """Use native readback to close sent deliveries that outlived a failed task."""
    handler = WechatSendMessageHandler(
        store, reader_factory=lambda: reader, account_factory=lambda: account,
    )
    for task_id in store.list_failed_wechat_tasks_with_sent_delivery(account.account_id):
        try:
            task, review, action, delivery = _current_wechat_action(store, task_id)
            if delivery.status == "sent" and delivery.account_id == account.account_id:
                _settle_sent_delivery(store, handler, task, review, action, delivery)
        except ValueError:
            # A stale review or invalid readback cannot settle this task.
            continue


def reconcile_failed_wechat_attempt(store: Any, attempt_id: int) -> dict[str, object]:
    """Verify exact current WeChat effects and settle only their failed Task projection.

    This function has no Sender dependency. A sent delivery must have one exact
    native Reader match. An explicit skip, or a supersession chain ending in
    a skip, requires complete readback windows proving no delivery. A sent
    successor requires its exact presence and the obsolete body's absence.
    """
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None or attempt.channel != "wechat":
        raise ValueError("wechat_attempt_missing_or_wrong_channel")
    task = store.get_reply_task_by_trigger_message_id(
        attempt.trigger_message_id, channel="wechat"
    )
    if (task is None or task.status != "failed"
            or task.conversation_id != attempt.conversation_id):
        raise ValueError("wechat_task_not_current_failed")
    current, review, action, delivery = _current_wechat_action(store, task.id)
    if attempt.agent_run_id != review.get("audit_run_id"):
        raise ValueError("wechat_attempt_review_mismatch")
    handler = WechatSendMessageHandler(store)

    if delivery.status == "sent":
        return _settle_sent_delivery(store, handler, current, review, action, delivery)

    if delivery.status == "skipped":
        return _settle_skipped_chain(store, handler, current, [delivery])

    if delivery.status != "superseded":
        return {"outcome": "failed", "task_id": task.id, "delivery_id": delivery.id,
                "reason": "wechat_delivery_not_reconcilable"}

    root_delivery = delivery
    chain = [delivery]
    for _ in range(10):
        prefix = "superseded_by_newer_wechat_trigger:"
        if not delivery.error.startswith(prefix):
            return {"outcome": "failed", "task_id": task.id,
                    "reason": "wechat_superseded_link_missing"}
        try:
            successor_task_id = int(delivery.error[len(prefix):])
        except ValueError:
            return {"outcome": "failed", "task_id": task.id,
                    "reason": "wechat_superseded_link_invalid"}
        successor = store.get_reply_task(successor_task_id)
        if successor is None or successor.channel != "wechat":
            return {"outcome": "failed", "task_id": task.id,
                    "reason": "wechat_superseded_successor_missing"}
        next_review, next_action, next_delivery = _current_wechat_action_for_task(store, successor)
        if (next_delivery.account_id != root_delivery.account_id
                or next_delivery.target_type != root_delivery.target_type
                or next_delivery.target_id != root_delivery.target_id
                or next_delivery.conversation_id != root_delivery.conversation_id):
            return {"outcome": "failed", "task_id": task.id,
                    "reason": "wechat_superseded_target_changed"}
        chain.append(next_delivery)
        if next_delivery.status == "skipped":
            return _settle_skipped_chain(store, handler, current, chain)
        if next_delivery.status == "superseded":
            delivery = next_delivery
            continue
        if next_delivery.status != "sent":
            return {"outcome": "failed", "task_id": task.id,
                    "reason": "wechat_successor_not_sent"}
        successor_result = handler.reconcile(
            next_action, action_key="", candidate=next_review,
        )
        if successor_result is None or successor_result.status != "verified":
            return {"outcome": "failed", "task_id": task.id,
                    "reason": (successor_result.provider_result.get("reason")
                               if successor_result else "wechat_successor_readback_missing")}
        root_readback = handler._readback_delivery(root_delivery, root_delivery.reply_text)
        if root_readback.status == "verified":
            settled = store.settle_failed_wechat_task_from_delivery(
                task.id, expected_execution_generation=task.execution_generation,
                delivery_id=root_delivery.id, task_status="done",
                evidence=root_readback.provider_result,
            )
            return {"outcome": "done" if settled else "failed", "task_id": task.id,
                    "delivery_id": root_delivery.id, "evidence": root_readback.provider_result}
        if (root_readback.status != "uncertain"
                or root_readback.provider_result.get("match_count") != 0
                or root_readback.provider_result.get("window_complete") is not True):
            return {"outcome": "failed", "task_id": task.id,
                    "reason": root_readback.provider_result.get("reason", "wechat_old_delivery_unresolved")}
        evidence = {
            "reason": "superseded_by_newer_wechat_delivery",
            "delivery_id": root_delivery.id,
            "successor_delivery_chain": [item.id for item in chain],
            "successor_readback": successor_result.provider_result,
            "old_delivery_readback": root_readback.provider_result,
        }
        settled = store.settle_failed_wechat_task_from_delivery(
            task.id, expected_execution_generation=task.execution_generation,
            delivery_id=root_delivery.id, task_status="skipped",
            successor_task_id=successor.id, evidence=evidence,
        )
        return {"outcome": "skipped" if settled else "failed", "task_id": task.id,
                "delivery_id": root_delivery.id, "successor_task_id": successor.id,
                "evidence": evidence}
    return {"outcome": "failed", "task_id": task.id,
            "reason": "wechat_superseded_chain_too_long"}


def _current_wechat_action_for_task(store: Any, task: Any):
    current, review, action, delivery = _current_wechat_action(store, task.id)
    return review, action, delivery
