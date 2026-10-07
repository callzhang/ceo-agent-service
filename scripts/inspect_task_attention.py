"""Read one input's persisted Attention receipts without opening the service store."""

import argparse
import json
import sqlite3
from pathlib import Path


def inspect_run(db, run):
    decision = json.loads(run["decision_json"])
    projection = json.loads(run["projection_json"])
    inspected = {
        "run_id": run["id"], "run_status": run["status"], "projection": projection,
    }
    if "project_assessments" in decision:
        inspected["project_assessments"] = decision["project_assessments"]
    if "project_decisions" in decision:
        inspected["project_decisions"] = decision["project_decisions"]
        # Only actual applied identities in the receipt select stored context.
        project_ids = [entry["project_id"] for entry in projection.get("project_decisions", [])]
        anchor_ids = [
            entry["anchor_id"] for entry in projection.get("project_assessments", [])
            if entry.get("anchor_id") is not None
        ]
        rows = db.execute(
            "select project.id as project_id, project.canonical_anchor_id as anchor_id, "
            "revision.id as revision_id, revision.context_json "
            "from business_projects project "
            "left join business_project_context_revisions revision on revision.id=("
            "select id from business_project_context_revisions where project_id=project.id "
            "order by id desc limit 1) "
            "where project.id in (select value from json_each(?)) "
            "or project.canonical_anchor_id in (select value from json_each(?)) order by project.id",
            (json.dumps(project_ids), json.dumps(anchor_ids)),
        ).fetchall() if project_ids or anchor_ids else []
        inspected["project_contexts"] = [
            {
                "project_id": row["project_id"], "anchor_id": row["anchor_id"],
                "revision_id": row["revision_id"],
                "context": json.loads(row["context_json"]) if row["context_json"] is not None else None,
            }
            for row in rows
        ]
    return inspected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--input-id", type=int, required=True)
    parser.add_argument("--replay-result", type=Path, help="Exact evaluation result artifact for delivered context metrics; no history reconstruction")
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
            "select id, status, decision_json, projection_json "
            "from task_agent_runs where summary_input_id=? order by id",
            (args.input_id,),
        ).fetchall()
        inspected_runs = [inspect_run(db, run) for run in runs]
        if args.replay_result:
            artifact = json.loads(args.replay_result.read_text())
            deliveries = artifact["source_steps"] if "source_steps" in artifact else [artifact]
            matched = [step for step in deliveries if step.get("input_id") == args.input_id]
            if not matched:
                raise ValueError("replay result does not match this input")
            by_run = {run["run_id"]: run for run in inspected_runs}
            for step in matched:
                if step.get("run_id") not in by_run:
                    raise ValueError("replay result does not match a persisted run")
                by_run[step["run_id"]]["context_deliveries"] = step["context_deliveries"]
    print(json.dumps({
        "input_id": item["id"],
        "source_type": item["source_type"],
        "input_status": item["status"],
        "runs": inspected_runs,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
