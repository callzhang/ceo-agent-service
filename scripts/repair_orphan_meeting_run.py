"""Explicit single-Run repair; caller owns verified backup and coordination.

No schema changes or runtime auto-repair. The historical meeting Job remains in
run_provenance; only an absent native Task's optional reference is detached.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app.current_schema_manifest import require_current_pair
from app.task8_storage import register_task8_functions


def detach_missing_task(db, *, run_id: int, missing_task_id: int, source_job_id: int) -> bool:
    """Repair one observed historical edge inside the caller's transaction."""
    row = db.execute(
        "select r.task_id,r.status,p.source_type,p.job_id from runs r "
        "join run_provenance p on p.run_id=r.id where r.id=?", (run_id,),
    ).fetchone()
    if row is None or row[2:] != ("meeting_alignment_runs", source_job_id):
        raise ValueError("historical Run provenance does not match")
    if row[1] in ("starting", "running") or db.execute(
        "select 1 from runtime_attempts where parent_id=? "
        "and status in ('starting','running') limit 1", (run_id,),
    ).fetchone():
        raise ValueError("Run or RuntimeAttempt is active")
    if row[0] is None:
        return False
    if row[0] != missing_task_id:
        raise ValueError("Run Task reference does not match")
    if db.execute("select 1 from tasks where id=?", (missing_task_id,)).fetchone():
        raise ValueError("referenced Task exists")
    db.execute("update runs set task_id=null where id=?", (run_id,))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--missing-task-id", type=int, required=True)
    parser.add_argument("--source-job-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="commit; default rolls back the rehearsal")
    args = parser.parse_args()
    require_current_pair(args.db)
    with closing(sqlite3.connect(args.db, timeout=30)) as db:
        register_task8_functions(db)
        db.execute("pragma foreign_keys=on")
        db.execute("begin immediate")
        before = set(db.execute("pragma foreign_key_check"))
        changed = detach_missing_task(
            db, run_id=args.run_id, missing_task_id=args.missing_task_id,
            source_job_id=args.source_job_id,
        )
        after = set(db.execute("pragma foreign_key_check"))
        removed = before - after
        if after - before or len(removed) != int(changed) or any(
            row[:3] != ("runs", args.run_id, "tasks") for row in removed
        ):
            raise ValueError("unexpected foreign-key finding change")
        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(json.dumps({"run_id": args.run_id, "changed": changed, "committed": args.apply,
                          "fk_before": len(before), "fk_after": len(after)}))


if __name__ == "__main__":
    main()
