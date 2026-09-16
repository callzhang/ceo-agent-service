#!/usr/bin/env python3
"""Reclassify exhausted Todo outbox rows that never reached DingTalk.

Before the `skipped` terminal status existed, a `task_todo_sync_outbox` row
was written as `failed` whenever nothing was delivered, including when the
Todo never qualified for a DingTalk mirror and no provider call was made.
Those rows claim an external delivery failed when none was attempted.

A row is reclassified only when it is provably in that state: the retry cap
is exhausted, the error is the historical not-delivered code, the Todo has no
DingTalk link of any status, and the Todo is still ineligible for a mirror
today. Anything else is left alone and reported.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store import AutoReplyStore

LEGACY_ERROR = "task_todo_sync_retry_exhausted:dingtalk_todo_effect_not_delivered"
CREATE_REASON = "dingtalk_todo_not_eligible_for_mirror"
COMPLETE_REASON = "dingtalk_todo_not_mirrored"

_CANDIDATES = """
select outbox.id, outbox.operation
  from task_todo_sync_outbox outbox
  left join work_todos todo on todo.id = outbox.work_todo_id
 where outbox.status = 'failed'
   and outbox.error = ?
   and outbox.attempt_count >= 3
   and not exists (
         select 1 from work_todo_dingtalk_links link
          where link.work_todo_id = outbox.work_todo_id
       )
   and (
         todo.id is null
         or todo.status not in ('open', 'waiting_owner')
         or trim(coalesce(todo.owner_user_id, '')) = ''
         or trim(coalesce(todo.deadline_at, '')) = ''
         or trim(coalesce(todo.completion_evidence_json, ''))
            not in ('', '{}', 'null')
       )
 order by outbox.id
"""


def reclassify(store: AutoReplyStore, *, dry_run: bool = True, limit: int = 0):
    """Return (reclassified_ids, still_failed_count)."""
    with store._connect() as db:
        rows = db.execute(_CANDIDATES, (LEGACY_ERROR,)).fetchall()
        if limit:
            rows = rows[:limit]
        ids = [int(row["id"]) for row in rows]
        if not dry_run:
            for row in rows:
                reason = (
                    CREATE_REASON
                    if str(row["operation"]) == "create"
                    else COMPLETE_REASON
                )
                db.execute(
                    "update task_todo_sync_outbox set status='skipped', error=?, "
                    "updated_at=current_timestamp "
                    "where id=? and status='failed'",
                    (f"{reason} (reclassified from {LEGACY_ERROR})", int(row["id"])),
                )
        remaining = db.execute(
            "select count(*) from task_todo_sync_outbox where status='failed'"
        ).fetchone()[0]
    return ids, int(remaining)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument("--limit", type=int, default=0, help="cap this run")
    args = parser.parse_args()
    ids, remaining = reclassify(
        AutoReplyStore(args.db), dry_run=not args.apply, limit=args.limit
    )
    mode = "applied" if args.apply else "preview"
    print(f"{mode}: rows={len(ids)} ids={ids} still_failed={remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
