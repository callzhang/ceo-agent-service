import json
from dataclasses import dataclass, field
from datetime import datetime

from app.store import AutoReplyStore


_OWNER_BACKFILL_SOURCE_TYPE = "todo_owner_backfill"


@dataclass(frozen=True)
class TodoOwnerBackfillItem:
    todo_id: int
    project_id: int
    title: str
    before_owner_user_id: str
    owner_user_id: str
    owner_name: str
    follow_up_ids: list[int] = field(default_factory=list)
    reason: str = ""
    skipped_reason: str = ""


@dataclass(frozen=True)
class TodoOwnerBackfillResult:
    dry_run: bool
    planned: int
    changed: int
    items: list[TodoOwnerBackfillItem]


def _default_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def backfill_todo_owner_ids_from_follow_ups(
    store: AutoReplyStore,
    *,
    dry_run: bool = True,
    limit: int | None = None,
    now: str = "",
) -> TodoOwnerBackfillResult:
    timestamp = now.strip() or _default_now()
    rows = _candidate_rows(store, limit=limit)
    items: list[TodoOwnerBackfillItem] = []
    planned = 0
    changed = 0
    for row in rows:
        follow_up_ids = _parse_id_list(str(row["follow_up_ids"] or ""))
        owner_id_count = int(row["owner_id_count"] or 0)
        owner_user_id = str(row["owner_user_id"] or "").strip()
        existing_owner_name = str(row["todo_owner_name"] or "").strip()
        follow_up_owner_name = str(row["follow_up_owner_name"] or "").strip()
        owner_name = existing_owner_name or follow_up_owner_name
        skipped_reason = ""
        if owner_id_count != 1:
            skipped_reason = "conflicting follow-up owner_user_id"
        elif not owner_user_id:
            skipped_reason = "missing follow-up owner_user_id"
        elif not follow_up_ids:
            skipped_reason = "missing follow-up evidence"
        if skipped_reason:
            items.append(
                TodoOwnerBackfillItem(
                    todo_id=int(row["todo_id"]),
                    project_id=int(row["project_id"]),
                    title=str(row["title"] or ""),
                    before_owner_user_id=str(row["todo_owner_user_id"] or ""),
                    owner_user_id=owner_user_id,
                    owner_name=owner_name,
                    follow_up_ids=follow_up_ids,
                    skipped_reason=skipped_reason,
                )
            )
            continue

        planned += 1
        reason = (
            "Backfilled TODO owner_user_id from the unique linked follow-up "
            "owner_user_id for the same TODO."
        )
        items.append(
            TodoOwnerBackfillItem(
                todo_id=int(row["todo_id"]),
                project_id=int(row["project_id"]),
                title=str(row["title"] or ""),
                before_owner_user_id=str(row["todo_owner_user_id"] or ""),
                owner_user_id=owner_user_id,
                owner_name=owner_name,
                follow_up_ids=follow_up_ids,
                reason=reason,
            )
        )
        if dry_run:
            continue
        evidence = {
            "user_id": owner_user_id,
            "name": owner_name,
            "source": "follow_up_drafts:" + ",".join(str(item) for item in follow_up_ids),
            "reason": reason,
            "description": (
                "The TODO had no stable owner_user_id, while linked follow-up "
                "drafts for the same TODO had exactly one non-empty owner_user_id."
            ),
            "created_at": timestamp,
        }
        store.update_work_todo(
            int(row["todo_id"]),
            owner_user_id=owner_user_id,
            owner_name=owner_name,
            owner_evidence_json=json.dumps(evidence, ensure_ascii=False),
        )
        store.create_work_update(
            project_id=int(row["project_id"]),
            source_type=_OWNER_BACKFILL_SOURCE_TYPE,
            source_ref=str(row["todo_id"]),
            summary=(
                f"Backfilled owner_user_id for TODO #{row['todo_id']} from linked "
                f"follow-up evidence."
            ),
            changes_json=json.dumps(
                {
                    "action": "backfill_todo_owner_user_id",
                    "todo_id": int(row["todo_id"]),
                    "owner_user_id": owner_user_id,
                    "owner_name": owner_name,
                    "follow_up_ids": follow_up_ids,
                    "owner_evidence": evidence,
                },
                ensure_ascii=False,
            ),
            merge_reason=reason,
            confidence=1.0,
        )
        changed += 1
    return TodoOwnerBackfillResult(
        dry_run=dry_run,
        planned=planned,
        changed=changed,
        items=items,
    )


def _candidate_rows(store: AutoReplyStore, *, limit: int | None) -> list:
    query = """
        select
          t.id as todo_id,
          t.project_id as project_id,
          t.title as title,
          trim(coalesce(t.owner_user_id, '')) as todo_owner_user_id,
          trim(coalesce(t.owner_name, '')) as todo_owner_name,
          count(distinct nullif(trim(coalesce(f.owner_user_id, '')), '')) as owner_id_count,
          max(nullif(trim(coalesce(f.owner_user_id, '')), '')) as owner_user_id,
          max(nullif(trim(coalesce(f.owner_name, '')), '')) as follow_up_owner_name,
          group_concat(f.id) as follow_up_ids
        from work_todos t
        join follow_up_drafts f on f.todo_id=t.id
        where lower(t.status) not in ('done', 'cancelled')
          and trim(coalesce(t.owner_user_id, ''))=''
          and trim(coalesce(f.owner_user_id, ''))<>''
          and lower(f.status) not in ('failed')
        group by t.id
        order by t.id
    """
    args: list[int] = []
    if limit is not None:
        query = f"{query} limit ?"
        args.append(max(0, int(limit)))
    with store._connect() as db:
        return list(db.execute(query, args).fetchall())


def _parse_id_list(value: str) -> list[int]:
    ids: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError:
            continue
    return ids
