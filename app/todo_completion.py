import json

from app.business_storage import update_business
from app.store import AutoReplyStore


def complete_business_task_from_external_todo(
    store: AutoReplyStore,
    *,
    business_task_id: int,
    evidence: dict[str, object],
) -> bool:
    """Close one authoritative Task from a completed mirrored DingTalk TODO."""
    with store._immediate_write_transaction() as db:
        task = store.get_business_task_in_transaction(task_id=business_task_id, _db=db)
        if task is None or task.status.value == "merged" or task.status.value == "done":
            return False
        source_ref = str(evidence.get("source") or "").strip()
        link = store.get_business_task_dingtalk_link_for_provider_task(
            business_task_id=business_task_id,
            dingtalk_task_id=source_ref.removeprefix("dingtalk_todo:"),
            statuses=("active", "done"),
            _db=db,
        )
        if not source_ref.startswith("dingtalk_todo:") or link is None:
            return False
        signal = store.get_business_task_signal_by_dedupe_key(
            dedupe_key=f"external-todo-completion:{business_task_id}:{source_ref}", _db=db
        )
        signal_id = signal.id if signal is not None else store.create_business_task_signal_in_transaction(
            source_type="dingtalk_todo", source_ref=source_ref,
            evidence_text=json.dumps(evidence, ensure_ascii=False),
            dedupe_key=f"external-todo-completion:{business_task_id}:{source_ref}", _db=db,
        )
        store.link_business_task_evidence_in_transaction(
            task_id=business_task_id, signal_id=signal_id, evidence_role="completion", _db=db
        )
        before = {"status": task.status.value, "commitment_status": task.commitment_status.value}
        update_business(db, "task", business_task_id, {
            "status": "done",
            "commitment_status": "completed",
            "updated_at": db.execute("select current_timestamp").fetchone()[0],
        })
        after = {"status": "done", "commitment_status": "completed", "completion_evidence": evidence}
        store.append_business_task_event(
            task_id=business_task_id, event_type="status_changed", signal_id=signal_id,
            before_json=json.dumps(before, ensure_ascii=False),
            after_json=json.dumps(after, ensure_ascii=False),
            reason=str(evidence.get("reason") or "DingTalk Todo marked done by owner"), _db=db,
        )
        store.complete_business_task_follow_ups(
            business_task_id=business_task_id,
            evidence_check_json=json.dumps(evidence, ensure_ascii=False),
            suppressed_reason=str(evidence.get("reason") or ""),
            _db=db,
        )
    from app.task_attention_projection import BusinessAttentionProjection

    BusinessAttentionProjection(store).recompute_for_tasks((business_task_id,))
    return True
