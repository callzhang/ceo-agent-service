"""Remove persisted trajectory copies and reclaim SQLite pages."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

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
        "okr_review_runs": {"audit_tool_events_json": "[]"},
        "meeting_alignment_runs": {"audit_tool_events_json": "[]"},
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
        db.execute("insert into codex_session_search_fts(codex_session_search_fts) values('delete-all')")
        for table, fields in columns.items():
            for column, empty in fields.items():
                counts["payload_bytes_removed"] += db.execute(
                    f"select coalesce(sum(length(cast({column} as blob))-length(cast(? as blob))),0) "
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(compact_native_duplicates(arguments.db)))


if __name__ == "__main__":
    main()
