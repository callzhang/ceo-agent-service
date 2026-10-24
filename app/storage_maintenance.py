"""Explicit historical input compaction; native-copy storage is retired."""

from __future__ import annotations
from app.task8_storage import register_task8_functions

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.store import AutoReplyStore, REPLY_TASK_CURRENT_SQL
from app.action_storage import REPLY_ATTEMPT_COMMON_SQL


REPLY_TASK_INPUT_SQL = "select id,task_id,trigger_text,trigger_message_json,input_provenance_json,input_compacted from task_inputs"
WORK_SUMMARY_INPUT_SQL = "select t.id,t.status,w.payload_json,w.source_created_at,w.body_sha256,w.body_bytes,w.body_compacted from tasks t join work_summary_inputs w on w.task_id=t.id where t.kind='work_summary'"


def compact_done_email_classification_inputs(database: Path) -> dict[str, int]:
    """Explicit, idempotent cleanup of historical completed classifier inputs.

    Caller owns backup, quiet-service coordination and physical page reclamation.
    Store initialization does not run this historical batch.
    """
    from app.email_task_adapter import EmailClassificationTaskAdapter

    counts = {"tasks_compacted": 0, "logical_bytes_removed": 0}
    with closing(sqlite3.connect(database, timeout=60)) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        db.execute("begin immediate")
        rows = db.execute(
            "select i.task_id, i.input_json from email_classification_inputs i "
            "join tasks t on t.id=i.task_id "
            "where t.kind='email_classification' and t.status='done' "
            "and json_extract(i.input_json, '$.input_compacted') is not 1 "
            "order by i.task_id"
        ).fetchall()
        for row in rows:
            compact = EmailClassificationTaskAdapter.compact_done_input_json(
                row["input_json"]
            )
            updated = db.execute(
                "update email_classification_inputs set input_json=? where task_id=? "
                "and exists(select 1 from tasks where id=? and kind='email_classification' and status='done')",
                (compact, row["task_id"], row["task_id"]),
            ).rowcount
            counts["tasks_compacted"] += updated
            counts["logical_bytes_removed"] += updated * max(
                0, len(row["input_json"].encode("utf-8")) - len(compact.encode("utf-8"))
            )
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed")
        db.commit()
    return counts


def compact_settled_reply_inputs(database: Path) -> dict[str, int]:
    """Explicit, idempotent cleanup of historical settled reply input copies.

    The caller owns backup, quiet-service coordination and page reclamation.
    Store initialization never runs this historical batch.
    """
    counts = {"tasks_compacted": 0, "inputs_compacted": 0,
              "attempts_cleared": 0, "logical_bytes_removed": 0}
    with closing(sqlite3.connect(database, timeout=60)) as db:
        register_task8_functions(db)
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        task_ids = [int(row["id"]) for row in db.execute(
            f"select id from ({REPLY_TASK_CURRENT_SQL}) where status in ('done','skipped') "
            "and input_compacted=0 order by id"
        ).fetchall()]
        before = _reply_input_storage_counts(db)
        for task_id in task_ids:
            AutoReplyStore._compact_settled_reply_task_input(db, task_id)
        after = _reply_input_storage_counts(db)
        counts["tasks_compacted"] = before[0] - after[0]
        counts["inputs_compacted"] = before[1] - after[1]
        counts["attempts_cleared"] = before[2] - after[2]
        counts["logical_bytes_removed"] = max(0, before[3] - after[3])
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed")
        db.commit()
    return counts


def _reply_input_storage_counts(db: sqlite3.Connection) -> tuple[int, int, int, int]:
    task = db.execute(
        "select count(*)-coalesce(sum(input_compacted),0) as uncompact, "
        "coalesce(sum(length(cast(trigger_text as blob)) + "
        "length(cast(trigger_message_json as blob)) + "
        "length(cast(input_provenance_json as blob))),0) as bytes "
        f"from ({REPLY_TASK_CURRENT_SQL}) where status in ('done','skipped')",
    ).fetchone()
    inputs = db.execute(
        "select count(*)-coalesce(sum(input_compacted),0) as uncompact, "
        "coalesce(sum(length(cast(trigger_text as blob)) + "
        "length(cast(trigger_message_json as blob)) + "
        "length(cast(input_provenance_json as blob))),0) as bytes "
        f"from ({REPLY_TASK_INPUT_SQL})",
    ).fetchone()
    attempts = db.execute(
        f"""select coalesce(sum(case when trigger_text<>'' then 1 else 0 end),0) as uncleared,
                  coalesce(sum(length(cast(trigger_text as blob)) +
                               length(cast(trigger_text_provenance_json as blob))),0) as bytes
           from ({REPLY_ATTEMPT_COMMON_SQL})""",
    ).fetchone()
    return (
        int(task["uncompact"]), int(inputs["uncompact"]),
        int(attempts["uncleared"] or 0),
        int(inputs["bytes"]) + int(attempts["bytes"]),
    )


def compact_terminal_work_summary_inputs(database: Path) -> dict[str, int]:
    """Explicit, idempotent migration of historical done/skipped input bodies.

    This function does not run on store initialization or during deployment.
    Caller owns backup, quiet-service coordination and physical page reclamation.
    """
    counts = {"rows_compacted": 0, "logical_bytes_removed": 0}
    with closing(sqlite3.connect(database, timeout=60)) as db:
        register_task8_functions(db)
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        rows = db.execute(
            "select id, payload_json, source_created_at, body_sha256, body_bytes "
            f"from ({WORK_SUMMARY_INPUT_SQL}) where status in ('done', 'skipped') "
            "and body_compacted=0 order by id"
        ).fetchall()
        for row in rows:
            payload = row["payload_json"]
            source_created_at, digest, body_bytes = AutoReplyStore._work_summary_input_provenance(payload)
            db.execute(
                """update work_summary_inputs set payload_json='{}',source_created_at=?,
                   body_sha256=?,body_bytes=?,body_compacted=1 where task_id=? and body_compacted=0
                   and exists(select 1 from tasks where id=? and kind='work_summary' and status in ('done','skipped'))""",
                (source_created_at, digest, body_bytes, row["id"], row["id"]),
            )
            counts["rows_compacted"] += 1
            counts["logical_bytes_removed"] += max(0, body_bytes - 2)
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed")
        db.commit()
    return counts

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--work-summary-inputs", action="store_true",
                        help="compact historical done/skipped work inputs only")
    targets.add_argument("--settled-reply-inputs", action="store_true",
                        help="compact historical settled reply input copies only")
    targets.add_argument("--done-email-classification-inputs", action="store_true",
                        help="compact historical done email classification inputs only")
    arguments = parser.parse_args()
    result = (
        compact_terminal_work_summary_inputs(arguments.db)
        if arguments.work_summary_inputs else
        compact_settled_reply_inputs(arguments.db)
        if arguments.settled_reply_inputs else
        compact_done_email_classification_inputs(arguments.db)
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
