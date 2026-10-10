"""Remove verified duplicate trajectories and reclaim SQLite pages."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.agent_effect_guard import provider_receipts
from app.native_trajectory import event_metadata, native_run_available, read_run_events


def completed_payloads(events: list[dict]) -> set[str]:
    return {
        json.dumps({key: item[key] for key in (
            "type", "command", "arguments", "result", "aggregated_output", "exit_code"
        ) if key in item}, ensure_ascii=False, sort_keys=True)
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(item := event.get("item"), dict)
        and item.get("type") in {"command_execution", "mcp_tool_call"}
    }


def compact_native_duplicates(database: Path) -> dict[str, int]:
    counts = {"runs_compacted": 0, "runs_retained": 0, "payload_bytes_removed": 0}
    with sqlite3.connect(database, timeout=60) as db:
        db.row_factory = sqlite3.Row
        runs = db.execute("select * from agent_runs where status in ('completed','failed')").fetchall()
        for run in runs:
            records = db.execute(
                "select id,event_json from agent_run_events where agent_run_id=? order by sequence",
                (run["id"],),
            ).fetchall()
            originals = [json.loads(record["event_json"]) for record in records]
            native = read_run_events(db, run) if native_run_available(db, run) else []
            if not native or provider_receipts(originals) != provider_receipts(native):
                counts["runs_retained"] += 1
                continue
            native_payloads = completed_payloads(native)
            replacements = [
                json.dumps(event_metadata(event), ensure_ascii=False, separators=(",", ":"))
                if completed_payloads([event]) and completed_payloads([event]).issubset(native_payloads)
                else record["event_json"]
                for record, event in zip(records, originals)
            ]
            saving = sum(len(record["event_json"].encode()) - len(replacement.encode())
                         for record, replacement in zip(records, replacements))
            if saving <= 0:
                continue
            db.executemany("update agent_run_events set event_json=? where id=?",
                           [(replacement, record["id"]) for record, replacement in zip(records, replacements)])
            db.commit()
            counts["runs_compacted"] += 1
            counts["payload_bytes_removed"] += saving
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed")
        db.execute("vacuum")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(compact_native_duplicates(arguments.db)))


if __name__ == "__main__":
    main()
