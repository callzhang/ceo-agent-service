#!/usr/bin/env python3
"""Requeue direct email actions that failed because their message is gone.

Before schema v40 the only non-success terminal state was `failed`, so an
action whose message had left the account exhausted its retries and stopped
there. The executor now resolves that case to `skipped`, but only on a fresh
attempt, and an exhausted row is never claimed again.

This resets such a row to `pending` so the worker re-runs it and writes its
own receipt. It does not decide the outcome: if the message is still gone the
service records `skipped`, and if it is back the action simply applies.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.email_store import DIRECT_ACTION_MAX_ATTEMPTS, EmailStore

LEGACY_ERROR = "provider_read_failed:ImapMessageUnavailable"

_CANDIDATES = """
select a.action_id, a.action_type
  from email_actions as a
  join email_classifications as c on c.id = a.classification_id
 where a.action_plan_id = c.current_action_plan_id
   and c.status = 'processed'
   and a.status = 'failed'
   and a.error = ?
   and a.attempt_count >= ?
 order by a.action_id
"""


def requeue(store: EmailStore, *, dry_run: bool = True, limit: int = 0):
    """Return (requeued_action_ids, remaining_failed_count)."""
    with store._connect() as db:
        db.execute("begin immediate")
        rows = db.execute(
            _CANDIDATES, (LEGACY_ERROR, DIRECT_ACTION_MAX_ATTEMPTS)
        ).fetchall()
        if limit:
            rows = rows[:limit]
        action_ids = [str(row["action_id"]) for row in rows]
        if not dry_run:
            for action_id in action_ids:
                db.execute(
                    "update email_actions "
                    "set status='pending', attempt_count=0, started_at='', "
                    "finished_at='', next_attempt_at='', error='', "
                    "provider_operation='', provider_result_id='' "
                    "where action_id=? and status='failed'",
                    (action_id,),
                )
        remaining = db.execute(
            "select count(*) from email_actions where status='failed'"
        ).fetchone()[0]
        if dry_run:
            db.execute("rollback")
    return action_ids, int(remaining)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument("--limit", type=int, default=0, help="cap this run")
    args = parser.parse_args()
    action_ids, remaining = requeue(
        EmailStore(args.db), dry_run=not args.apply, limit=args.limit
    )
    mode = "applied" if args.apply else "preview"
    print(f"{mode}: rows={len(action_ids)} ids={action_ids} still_failed={remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
