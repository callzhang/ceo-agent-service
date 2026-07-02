import json
from dataclasses import dataclass, field
from datetime import datetime

from app.store import AutoReplyStore


_DINGTALK_LINK_AUDIT_NOTE = (
    "Internal TODO cancelled as routine process; external "
    "cancellation is not part of this change."
)
_ROUTINE_PROCESS_BACKFILL_SOURCE_TYPE = "routine_process_backfill"


@dataclass(frozen=True)
class RoutineProcessBackfillItem:
    todo_id: int
    project_id: int
    title: str
    before_status: str
    after_status: str
    suppressed_follow_up_ids: list[int] = field(default_factory=list)
    dingtalk_link_ids: list[int] = field(default_factory=list)
    reason: str = ""
    skipped_reason: str = ""


@dataclass(frozen=True)
class RoutineProcessBackfillResult:
    dry_run: bool
    planned: int
    changed: int
    items: list[RoutineProcessBackfillItem]


def _default_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def backfill_routine_process_todos(
    store: AutoReplyStore,
    *,
    todo_ids: list[int],
    reason: str,
    dry_run: bool = True,
    now: str = "",
) -> RoutineProcessBackfillResult:
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("reason is required")
    timestamp = now.strip() or _default_now()
    seen: set[int] = set()
    items: list[RoutineProcessBackfillItem] = []
    changed = 0

    for todo_id in todo_ids:
        if todo_id in seen:
            continue
        seen.add(todo_id)
        todo = store.get_work_todo(todo_id)
        if todo is None:
            items.append(
                RoutineProcessBackfillItem(
                    todo_id=todo_id,
                    project_id=0,
                    title="",
                    before_status="missing",
                    after_status="missing",
                    reason=clean_reason,
                    skipped_reason="todo not found",
                )
            )
            continue
        before_status = str(todo.status)
        if before_status == "done":
            items.append(
                RoutineProcessBackfillItem(
                    todo_id=todo.id,
                    project_id=todo.project_id,
                    title=todo.title,
                    before_status=before_status,
                    after_status=before_status,
                    reason=clean_reason,
                    skipped_reason="todo already done",
                )
            )
            continue

        follow_ups = store.list_follow_up_drafts_for_todo(
            todo.id,
            statuses=("draft", "approved"),
        )
        links = store.list_work_todo_dingtalk_links_for_todo(
            todo.id,
            statuses=("active", "creating", "failed"),
        )
        links_to_update = [
            link for link in links if link.last_error != _DINGTALK_LINK_AUDIT_NOTE
        ]
        should_cancel_todo = before_status != "cancelled"
        audit_exists = store.has_work_update(
            project_id=todo.project_id,
            source_type=_ROUTINE_PROCESS_BACKFILL_SOURCE_TYPE,
            source_ref=str(todo.id),
        )
        should_record_audit = not audit_exists
        has_remaining_effects = bool(
            should_cancel_todo
            or follow_ups
            or links_to_update
            or should_record_audit
        )
        item = RoutineProcessBackfillItem(
            todo_id=todo.id,
            project_id=todo.project_id,
            title=todo.title,
            before_status=before_status,
            after_status="cancelled",
            suppressed_follow_up_ids=[draft.id for draft in follow_ups],
            dingtalk_link_ids=[link.id for link in links_to_update],
            reason=clean_reason,
            skipped_reason=(
                ""
                if has_remaining_effects
                else "todo already cancelled and cleanup complete"
            ),
        )
        items.append(item)

        if dry_run or not has_remaining_effects:
            continue

        if should_cancel_todo:
            store.update_work_todo(
                todo.id,
                status="cancelled",
                blocker=clean_reason,
            )
        for draft in follow_ups:
            store.update_follow_up_draft(
                draft.id,
                status="skipped",
                suppressed_reason=clean_reason,
                evidence_check_json=json.dumps(
                    {
                        "source": "routine_process_backfill",
                        "reason": clean_reason,
                        "todo_id": todo.id,
                    },
                    ensure_ascii=False,
                ),
            )
        for link in links_to_update:
            store.update_work_todo_dingtalk_link(
                link.id,
                last_error=_DINGTALK_LINK_AUDIT_NOTE,
            )
        if should_record_audit:
            store.create_work_update(
                project_id=todo.project_id,
                source_type=_ROUTINE_PROCESS_BACKFILL_SOURCE_TYPE,
                source_ref=str(todo.id),
                summary=(
                    f"Cancelled routine-process TODO #{todo.id}: {todo.title}. "
                    f"Reason: {clean_reason}"
                ),
                changes_json=(
                    json.dumps(
                        {
                            "action": "cancel_routine_process_todo",
                            "todo_id": todo.id,
                            "suppressed_follow_up_ids": item.suppressed_follow_up_ids,
                            "dingtalk_link_ids": item.dingtalk_link_ids,
                            "at": timestamp,
                        },
                        ensure_ascii=False,
                    )
                ),
                merge_reason=_ROUTINE_PROCESS_BACKFILL_SOURCE_TYPE,
                confidence=1.0,
            )
        changed += 1

    return RoutineProcessBackfillResult(
        dry_run=dry_run,
        planned=sum(1 for item in items if not item.skipped_reason),
        changed=changed,
        items=items,
    )
