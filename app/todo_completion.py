import json
import sqlite3
from typing import Any

from app.store import AutoReplyStore
from app.task_models import TodoStatus


def close_business_task_with_completion_evidence(
    store: AutoReplyStore,
    *,
    business_task_id: int,
    evidence: dict[str, object],
    source_type: str,
    _db: sqlite3.Connection,
) -> bool:
    task = store.get_business_task_in_transaction(task_id=business_task_id, _db=_db)
    if task is None or task.status.value in {"done", "merged"}:
        return False
    source_ref = str(evidence.get("source") or "").strip()
    if not source_ref or not str(evidence.get("reason") or "").strip():
        raise ValueError("Business Task completion requires sourced evidence and reason")
    dedupe_key = f"business-task-completion:{business_task_id}:{source_type}:{source_ref}"
    signal = store.get_business_task_signal_by_dedupe_key(dedupe_key=dedupe_key, _db=_db)
    signal_id = signal.id if signal is not None else store.create_business_task_signal_in_transaction(
        source_type=source_type, source_ref=source_ref,
        evidence_text=json.dumps(evidence, ensure_ascii=False),
        dedupe_key=dedupe_key, _db=_db,
    )
    store.link_business_task_evidence_in_transaction(
        task_id=business_task_id, signal_id=signal_id, evidence_role="completion", _db=_db,
    )
    before = {"status": task.status.value, "commitment_status": task.commitment_status.value}
    after = {"status": "done", "commitment_status": "completed", "completion_evidence": evidence}
    _db.execute(
        "update business_tasks set status='done', commitment_status='completed', "
        "updated_at=current_timestamp where id=?", (business_task_id,),
    )
    store.append_business_task_event(
        task_id=business_task_id, event_type="status_changed", signal_id=signal_id,
        before_json=json.dumps(before, ensure_ascii=False),
        after_json=json.dumps(after, ensure_ascii=False),
        reason=str(evidence["reason"]), _db=_db,
    )
    _db.execute(
        "update business_task_follow_ups set status='completed', evidence_check_json=?, "
        "suppressed_reason=?, updated_at=current_timestamp where business_task_id=? "
        "and status in ('draft','approved','sent')",
        (json.dumps(evidence, ensure_ascii=False), str(evidence["reason"]), business_task_id),
    )
    return True


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
        link = db.execute(
            "select * from business_task_dingtalk_links where business_task_id=? "
            "and status in ('active','done') and dingtalk_task_id=? order by id desc limit 1",
            (business_task_id, source_ref.removeprefix("dingtalk_todo:")),
        ).fetchone()
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
        db.execute(
            "update business_tasks set status='done', commitment_status='completed', updated_at=current_timestamp where id=?",
            (business_task_id,),
        )
        after = {"status": "done", "commitment_status": "completed", "completion_evidence": evidence}
        store.append_business_task_event(
            task_id=business_task_id, event_type="status_changed", signal_id=signal_id,
            before_json=json.dumps(before, ensure_ascii=False),
            after_json=json.dumps(after, ensure_ascii=False),
            reason=str(evidence.get("reason") or "DingTalk Todo marked done by owner"), _db=db,
        )
        db.execute(
            "update business_task_follow_ups set status='completed', evidence_check_json=?, "
            "suppressed_reason=?, updated_at=current_timestamp where business_task_id=? "
            "and status in ('draft','approved','sent')",
            (json.dumps(evidence, ensure_ascii=False), str(evidence.get("reason") or ""), business_task_id),
        )
    from app.task_agent import _project_task_attention

    _project_task_attention(store, (), (business_task_id,))
    return True

def close_todo_with_completion_evidence(
    store: AutoReplyStore,
    *,
    todo_id: int,
    evidence: dict[str, Any],
    now: str,
    source_type: str,
    source_ref: str,
    merge_reason: str,
    confidence: float = 1.0,
    _db: sqlite3.Connection | None = None,
) -> bool:
    todo = store.get_work_todo(todo_id, _db=_db)
    if todo is None or str(todo.status) == TodoStatus.DONE.value:
        return False
    normalized_evidence = _completion_evidence(evidence, now=now)
    store.update_work_todo(
        todo.id,
        status=TodoStatus.DONE.value,
        completion_evidence_json=json.dumps(normalized_evidence, ensure_ascii=False),
        completed_at=now,
        _db=_db,
    )
    complete_follow_ups_for_todo(
        store,
        todo_id=todo.id,
        evidence=normalized_evidence,
        now=now,
        _db=_db,
    )
    store.create_work_update(
        project_id=todo.project_id,
        source_type=source_type,
        source_ref=source_ref,
        summary=f"Todo completed: {todo.title}",
        changes_json=json.dumps(
            {
                "todo_id": todo.id,
                "status": TodoStatus.DONE.value,
                "completion_evidence": normalized_evidence,
            },
            ensure_ascii=False,
        ),
        merge_reason=merge_reason,
        confidence=confidence,
        _db=_db,
    )
    return True


def complete_follow_ups_for_todo(
    store: AutoReplyStore,
    *,
    todo_id: int,
    evidence: dict[str, Any],
    now: str,
    _db: sqlite3.Connection | None = None,
) -> int:
    normalized_evidence = _completion_evidence(evidence, now=now)
    completed = 0
    for draft in store.list_follow_up_drafts_for_todo(
        todo_id,
        statuses=("draft", "approved", "sent"),
        _db=_db,
    ):
        payload = {
            "completed": True,
            "reason": normalized_evidence["reason"],
            "source": normalized_evidence["source"],
            "checked_at": now,
            "completion_evidence": normalized_evidence,
        }
        store.update_follow_up_draft(
            draft.id,
            status="completed",
            evidence_check_json=json.dumps(payload, ensure_ascii=False),
            suppressed_reason=normalized_evidence["reason"],
            _db=_db,
        )
        completed += 1
    return completed


def _completion_evidence(evidence: dict[str, Any], *, now: str) -> dict[str, Any]:
    source = str(evidence.get("source") or "").strip()
    reason = str(
        evidence.get("reason")
        or evidence.get("description")
        or evidence.get("summary")
        or ""
    ).strip()
    completed_at = str(evidence.get("completed_at") or now).strip()
    normalized = dict(evidence)
    normalized["source"] = source or "unknown"
    normalized["reason"] = reason or "completion confirmed"
    normalized["completed_at"] = completed_at
    normalized.setdefault("checked_at", now)
    return normalized

