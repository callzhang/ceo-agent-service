import json
from dataclasses import dataclass, field
from datetime import datetime

from app.store import AutoReplyStore


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
        if str(todo.status) in {"done", "cancelled"}:
            items.append(
                RoutineProcessBackfillItem(
                    todo_id=todo.id,
                    project_id=todo.project_id,
                    title=todo.title,
                    before_status=str(todo.status),
                    after_status=str(todo.status),
                    reason=clean_reason,
                    skipped_reason=f"todo already {todo.status}",
                )
            )
            continue

        follow_ups = store.list_follow_up_drafts(
            todo_id=todo.id,
            statuses=("draft", "approved"),
            limit=1000,
        )
        links = store.list_work_todo_dingtalk_links(
            work_todo_id=todo.id,
            statuses=("active", "creating", "failed"),
            limit=100,
        )
        item = RoutineProcessBackfillItem(
            todo_id=todo.id,
            project_id=todo.project_id,
            title=todo.title,
            before_status=str(todo.status),
            after_status="cancelled",
            suppressed_follow_up_ids=[draft.id for draft in follow_ups],
            dingtalk_link_ids=[link.id for link in links],
            reason=clean_reason,
        )
        items.append(item)

        if dry_run:
            continue

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
        for link in links:
            store.update_work_todo_dingtalk_link(
                link.id,
                last_error=(
                    "Internal TODO cancelled as routine process; external "
                    "cancellation is not part of this change."
                ),
            )
        store.create_work_update(
            project_id=todo.project_id,
            source_type="routine_process_backfill",
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
            merge_reason="routine_process_backfill",
            confidence=1.0,
        )
        changed += 1

    return RoutineProcessBackfillResult(
        dry_run=dry_run,
        planned=sum(1 for item in items if not item.skipped_reason),
        changed=changed,
        items=items,
    )
