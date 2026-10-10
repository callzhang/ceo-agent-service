"""Remove persisted trajectory copies and reclaim SQLite pages."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.store import AutoReplyStore


def compact_done_email_classification_inputs(database: Path) -> dict[str, int]:
    """Explicit, idempotent cleanup of historical completed classifier inputs.

    Caller owns backup, quiet-service coordination and physical page reclamation.
    Store initialization does not run this historical batch.
    """
    from app.email_task_adapter import EmailClassificationTaskAdapter

    counts = {"tasks_compacted": 0, "logical_bytes_removed": 0}
    with closing(sqlite3.connect(database, timeout=60)) as db:
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        rows = db.execute(
            "select task_id, input_json from email_agent_classification_tasks "
            "where status='done' and json_extract(input_json, '$.input_compacted') is not 1 "
            "order by task_id"
        ).fetchall()
        for row in rows:
            compact = EmailClassificationTaskAdapter.compact_done_input_json(
                row["input_json"]
            )
            updated = db.execute(
                "update email_agent_classification_tasks set input_json=? "
                "where task_id=? and status='done'",
                (compact, row["task_id"]),
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
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        task_ids = [int(row["id"]) for row in db.execute(
            "select id from reply_tasks where status in ('done','skipped') "
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
        "from reply_tasks where status in ('done','skipped')",
    ).fetchone()
    inputs = db.execute(
        "select count(*)-coalesce(sum(input_compacted),0) as uncompact, "
        "coalesce(sum(length(cast(trigger_text as blob)) + "
        "length(cast(trigger_message_json as blob)) + "
        "length(cast(input_provenance_json as blob))),0) as bytes "
        "from reply_task_inputs",
    ).fetchone()
    attempts = db.execute(
        """select coalesce(sum(case when trigger_text<>'' then 1 else 0 end),0) as uncleared,
                  coalesce(sum(length(cast(trigger_text as blob)) +
                               length(cast(trigger_text_provenance_json as blob))),0) as bytes
           from reply_attempts""",
    ).fetchone()
    return (
        int(task["uncompact"]), int(inputs["uncompact"]),
        int(attempts["uncleared"] or 0),
        int(task["bytes"]) + int(inputs["bytes"]) + int(attempts["bytes"]),
    )


def compact_terminal_work_summary_inputs(database: Path) -> dict[str, int]:
    """Explicit, idempotent migration of historical done/skipped input bodies.

    This function does not run on store initialization or during deployment.
    Caller owns backup, quiet-service coordination and physical page reclamation.
    """
    counts = {"rows_compacted": 0, "logical_bytes_removed": 0}
    with closing(sqlite3.connect(database, timeout=60)) as db:
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        rows = db.execute(
            "select id, payload_json, source_created_at, body_sha256, body_bytes "
            "from work_summary_inputs where status in ('done', 'skipped') "
            "and body_compacted=0 order by id"
        ).fetchall()
        for row in rows:
            payload = str(row["payload_json"])
            source_created_at, digest, body_bytes = AutoReplyStore._work_summary_input_provenance(payload)
            db.execute(
                "update work_summary_inputs set payload_json='{}', source_created_at=?, "
                "body_sha256=?, body_bytes=?, body_compacted=1 "
                "where id=? and status in ('done', 'skipped') and body_compacted=0",
                (source_created_at, digest, body_bytes, row["id"]),
            )
            counts["rows_compacted"] += 1
            counts["logical_bytes_removed"] += max(0, body_bytes - 2)
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed")
        db.commit()
    return counts

def compact_native_duplicates(database: Path) -> dict[str, int]:
    """Remove all persisted trajectory copies, including missing-native history.

    Task state and native references are retained. Missing original sessions are
    explicitly unavailable; SQLite is no longer a second trajectory source.
    """
    counts = {"event_runs_cleared": 0, "payload_fields_cleared": 0, "payload_bytes_removed": 0}
    columns = {
        "agent_runs": {"final_result_json": ""},
        "agent_runtime_attempts": {"result_envelope_json": ""},
        "reply_attempts": {"audit_tool_events_json": "[]"},
        "okr_review_runs": {"envelope_json": "{}", "audit_summary": "", "audit_tool_events_json": "[]"},
        "meeting_alignment_runs": {"decision_json": "{}", "audit_summary": "", "audit_tool_events_json": "[]"},
        "task_agent_runs": {"decision_json": "{}", "audit_summary": "", "memory_recall_used": 0},
        "workbench_turns": {"final_text": "", "error_detail": ""},
        "codex_session_search_index": {
            "summary_text": "", "fts_text": "", "embedding_json": "",
            "embedding_model": "", "embedding_updated_at": "",
        },
    }
    with closing(sqlite3.connect(database, timeout=60)) as db:
        db.execute("begin immediate")
        counts["event_runs_cleared"] = db.execute(
            "select count(distinct agent_run_id) from agent_run_events"
        ).fetchone()[0]
        counts["payload_bytes_removed"] = db.execute(
            "select coalesce(sum(length(cast(event_json as blob))),0) from agent_run_events"
        ).fetchone()[0]
        db.execute("delete from agent_run_events")
        _clear_workbench_event_bodies(db, counts)
        db.execute("insert into codex_session_search_fts(codex_session_search_fts) values('delete-all')")
        for table, fields in columns.items():
            for column, empty in fields.items():
                counts["payload_bytes_removed"] += db.execute(
                    f"select coalesce(sum(max(length(cast({column} as blob))-length(cast(? as blob)),0)),0) "
                    f"from {table} where {column}<>?", (empty, empty),
                ).fetchone()[0]
                counts["payload_fields_cleared"] += db.execute(
                    f"update {table} set {column}=? where {column}<>?", (empty, empty)
                ).rowcount
        db.execute(
            "insert into codex_session_search_fts(rowid,title,summary_text,fts_text) "
            "select id,title,summary_text,fts_text from codex_session_search_index"
        )
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed")
        db.commit()
        db.execute("vacuum")
        checkpoint = db.execute("pragma wal_checkpoint(truncate)").fetchone()
        if checkpoint[0] != 0:
            raise RuntimeError("database payloads cleared; WAL reclamation incomplete because another connection is busy")
    return counts


def _clear_workbench_event_bodies(db: sqlite3.Connection, counts: dict[str, int]) -> None:
    """Keep event identity/order and exact native ordinals; erase Agent content."""
    from app.workbench.native_events import AGENT_EVENTS

    rows = db.execute(
        "select id,turn_id,event_type,payload_json from workbench_events "
        "order by turn_id,sequence,id"
    ).fetchall()
    turn_id = None
    completed = 0
    pending: dict[str, int] = {}
    replacements: dict[int, str] = {}
    originals: dict[int, str] = {}
    for event_id, current_turn, event_type, raw in rows:
        if current_turn != turn_id:
            turn_id, completed, pending = current_turn, 0, {}
        if event_type not in AGENT_EVENTS:
            continue
        originals[event_id] = raw
        payload = json.loads(raw)
        ordinal = payload.get("native_ordinal")
        reference = {"native_ordinal": ordinal} if type(ordinal) is int and ordinal > 0 else {}
        replacements[event_id] = json.dumps(reference, separators=(",", ":"))
        call_id = payload.get("tool_call_id")
        if event_type == "tool_started" and isinstance(call_id, str) and call_id:
            pending[call_id] = event_id
        elif event_type == "tool_completed":
            completed = max(completed + 1, ordinal if reference else 0)
            reference = {"native_ordinal": ordinal if reference else completed}
            encoded = json.dumps(reference, separators=(",", ":"))
            replacements[event_id] = encoded
            if isinstance(call_id, str) and call_id in pending:
                replacements[pending.pop(call_id)] = encoded
    for event_id, encoded in replacements.items():
        raw = originals[event_id]
        if raw == encoded:
            continue
        counts["payload_fields_cleared"] += 1
        counts["payload_bytes_removed"] += max(0, len(raw.encode("utf-8")) - len(encoded.encode("utf-8")))
        db.execute("update workbench_events set payload_json=? where id=?", (encoded, event_id))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--work-summary-inputs", action="store_true",
                        help="compact historical done/skipped work inputs only")
    parser.add_argument("--settled-reply-inputs", action="store_true",
                        help="compact historical settled reply input copies only")
    parser.add_argument("--done-email-classification-inputs", action="store_true",
                        help="compact historical done email classification inputs only")
    arguments = parser.parse_args()
    if sum((arguments.work_summary_inputs, arguments.settled_reply_inputs,
            arguments.done_email_classification_inputs)) > 1:
        parser.error("select one historical compaction target")
    result = (
        compact_terminal_work_summary_inputs(arguments.db)
        if arguments.work_summary_inputs else
        compact_settled_reply_inputs(arguments.db)
        if arguments.settled_reply_inputs else
        compact_done_email_classification_inputs(arguments.db)
        if arguments.done_email_classification_inputs else
        compact_native_duplicates(arguments.db)
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
