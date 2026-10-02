"""Read one input's persisted Attention receipts without opening the service store."""

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--input-id", type=int, required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        item = db.execute(
            "select id, source_type, status from work_summary_inputs where id=?",
            (args.input_id,),
        ).fetchone()
        if item is None:
            print(json.dumps({"error": "input_not_found", "input_id": args.input_id}))
            return 1
        runs = db.execute(
            "select id, status, projection_json, audit_summary "
            "from task_agent_runs where summary_input_id=? order by id",
            (args.input_id,),
        ).fetchall()
    print(json.dumps({
        "input_id": item["id"],
        "source_type": item["source_type"],
        "input_status": item["status"],
        "runs": [{
            "run_id": run["id"],
            "run_status": run["status"],
            "projection": json.loads(run["projection_json"]),
            "audit_summary": run["audit_summary"],
        } for run in runs],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
