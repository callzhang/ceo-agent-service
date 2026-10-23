#!/usr/bin/env python3
"""Exercise published old candidate DAO writes across two adoption inverse cycles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(raw.encode()).hexdigest()


def snapshot(db: sqlite3.Connection) -> dict:
    tables = ("actions", "action_receipts", "action_observers", "action_entity_identity_map")
    result = {}
    for table in tables:
        columns = [row[1] for row in db.execute(f'pragma table_info("{table}")')]
        result[table] = [tuple(row) for row in db.execute(f'select * from "{table}" order by rowid')]
        result[table + "_columns"] = columns
    for table in ("run_adopted_results", "run_adopted_reviews", "run_adopted_selections",
                  "run_adopted_supplements", "run_execution_progress"):
        exists = db.execute("select 1 from main.sqlite_schema where type='table' and name=?",
                            (table,)).fetchone()
        result[table] = ([tuple(row) for row in db.execute(f'select * from "{table}" order by rowid')]
                         if exists else [])
    result["sqlite_sequence"] = [tuple(row) for row in db.execute(
        "select rowid,name,seq from main.sqlite_sequence order by rowid")]
    return result


def old_dao(root: Path, database: Path, output: Path, cycle: int) -> dict:
    program = r'''import contextlib, json, sqlite3, sys
from pathlib import Path
root, database, output, cycle = map(str, sys.argv[1:])
sys.path.insert(0, root)
from app.reviewed_candidate_store import ReviewedCandidateStoreMixin
class Harness(ReviewedCandidateStoreMixin):
    @contextlib.contextmanager
    def _connect(self):
        db=sqlite3.connect(database); db.row_factory=sqlite3.Row
        try: yield db
        finally: db.close()
    @contextlib.contextmanager
    def _immediate_write_transaction(self):
        db=sqlite3.connect(database); db.row_factory=sqlite3.Row
        try:
            db.execute("begin immediate"); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()
store=Harness()
db=sqlite3.connect(database); db.row_factory=sqlite3.Row
tables=[r[0] for r in db.execute("select name from sqlite_schema where type='table' and name like 'run_adopted_%' order by name")]
action_types=[tuple(r) for r in db.execute("select id,record_type from actions order by id")]
module=sys.modules["app.reviewed_candidate_store"].__file__
Path(output).write_text(json.dumps({"module":module,"run_adopted_tables":tables,
 "action_records":action_types},sort_keys=True))
db.close()
fixture=json.loads(Path(output).with_name("fixture.json").read_text())
candidate_id=fixture["candidate_id"]
before=store.get_review_candidate(candidate_id)
execution=store.get_candidate_execution(candidate_id)
if not before or before["candidate_digest"] != "EXPECTED_DIGEST":
    raise AssertionError("old public DAO did not read the exact adopted candidate")
if before["consumer_run_id"] != fixture["consumer_run_id"]:
    raise AssertionError("old public DAO candidate maps to a different source Consumer Run")
if not execution or execution["selection_id"] != fixture["selection_id"]:
    raise AssertionError("old public DAO did not read the exact selected execution")
supplement=store.record_candidate_supplement(candidate_id, "old-code supplement cycle " + cycle)
after=store.get_review_candidate(candidate_id)
if after["invalidation_reason"] != "supplement" or not after["invalidated_at"]:
    raise AssertionError("old public DAO supplement did not invalidate its candidate")
result={"module":sys.modules["app.reviewed_candidate_store"].__file__,
 "candidate_id":before["id"],"review_id":fixture["review_id"],
 "audit_run_id":fixture["audit_run_id"],"selection_id":fixture["selection_id"],
 "supplement_id":supplement["id"],"invalidated_at":after["invalidated_at"],
 "reason":after["invalidation_reason"],"candidate_digest":before["candidate_digest"]}
Path(output).write_text(json.dumps(result,sort_keys=True))
'''.replace("EXPECTED_DIGEST", "__DIGEST__")
    # The digest is injected as a JSON string literal, never inferred from old code.
    current = json.loads((output.parent / "fixture.json").read_text())
    program = program.replace("__DIGEST__", current["candidate_digest"])
    child_env = {**os.environ, "PYTHONPATH": str(root)}
    completed = subprocess.run([sys.executable, "-c", program, str(root), str(database),
                                str(output), str(cycle)], cwd=root, env=child_env,
                               text=True, capture_output=True)
    if completed.returncode:
        observed = json.loads(output.read_text()) if output.exists() else {}
        raise RuntimeError(json.dumps({"error": completed.stderr or completed.stdout,
                                       "observed": observed}, sort_keys=True))
    result = json.loads(output.read_text())
    if Path(result["module"]).resolve().parent.parent != root.resolve():
        raise AssertionError("cold child imported old DAO outside explicit predecessor root")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predecessor-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    predecessor = args.predecessor_root.resolve(strict=True)
    if not (predecessor / "app/reviewed_candidate_store.py").is_file():
        raise SystemExit("predecessor root lacks reviewed_candidate_store.py")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tests.test_run_adoption_schema import _populated_source
    from app.run_adoption_inverse import restore_run_adoptions
    from app.run_entity_schema import restore_run_entities
    from app.task_entity_schema import restore_task_entities
    db, context = _populated_source(remap_tasks=True, remap_runs=True,
                                    stop_before_adoption=True)
    path = out / "cold.sqlite3"
    db.row_factory = sqlite3.Row
    forward = __import__("app.run_adoption_schema", fromlist=["migrate_run_adoptions"]).migrate_run_adoptions(
        db, context["adoption_source"], task_metadata=context["task_metadata"],
        run_metadata=context["run_metadata"])
    # Accepted B2B current-fact recipe: retire one current supplement, add a
    # genuinely new one, and add a new Audit Run plus Review with complete
    # source facts before the first inverse.
    old_supplements = [row[0] for row in db.execute(
        "select id from run_adopted_supplements where consumer_run_id=9106 order by id")]
    db.execute("delete from run_adopted_supplements where id=?", (old_supplements[-1],))
    db.execute("insert into run_adopted_supplements(id,consumer_run_id,instruction,created_at) "
               "values(?,?,?,?)", (old_supplements[-1] + 20, 9106,
                "native supplement survives both cycles", "native-supplement-clock"))
    from app.run_entity_storage import create_run
    consumer_run = db.execute("select * from runs where id=9106").fetchone()
    run_values = dict(zip((item[0] for item in db.execute("select * from runs where id=9106").description),
                          consumer_run, strict=True))
    run_values.update(role="audit", parent_id=9106)
    run_values.pop("id")
    run_values["turn_attempt"] = db.execute(
        "select coalesce(max(turn_attempt),0)+1 from runs where task_id=? and role='audit' "
        "and execution_generation=? and proposal_revision=?",
        (run_values["task_id"], run_values["execution_generation"],
         run_values["proposal_revision"])).fetchone()[0]
    audit = create_run(db, values=run_values)
    review_body = '{"candidate_digest":"' + context["digest"] + '","outcome":"approve"}'
    new_review_id = db.execute("insert into run_adopted_reviews "
        "(consumer_run_id,audit_run_id,candidate_digest,decision,result_json,created_at) "
        "values(?,?,?,?,?,?) returning id",
        (9106, audit["id"], context["digest"], "approve", review_body,
         "native-review-clock")).fetchone()[0]

    # Accepted C2C collision recipe: a lawful current Action occupies the
    # candidate's old physical ID and owns provider receipt lineage.
    from app.action_entity_storage import ACTION_KIND_CODE, add_action_observer, append_action_receipt
    occupied_id = next(int(row[1]) for row in forward["identities"]
                       if row[0] == "review_candidates")
    for (trigger_name,) in db.execute(
            "select name from main.sqlite_schema where type='trigger' and tbl_name='actions'"):
        db.execute('drop trigger main."' + trigger_name.replace('"', '""') + '"')
    db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) "
               "values(?,?,null,jsonb('{}'),jsonb('[]'))", (occupied_id, ACTION_KIND_CODE))
    from app.task_run_entity_assembly import _action_objects
    for sql in _action_objects().values():
        if sql.lstrip().lower().startswith("create trigger"):
            db.execute(sql)
    first_receipt = append_action_receipt(db, occupied_id, values={
        "operation": "send", "status": "done", "provider_result_json": "{}",
        "observed_at": "collision-provider-clock"})
    second_receipt = append_action_receipt(db, occupied_id, values={
        "operation": "send", "status": "done", "observed_at": "collision-lineage-clock"})
    db.execute("update action_receipts set provider_receipt_id=? where id=?",
               (first_receipt, second_receipt))
    add_action_observer(db, occupied_id, agent_run_id=0, task_id=0,
                        created_at="collision-observer-clock")
    db.execute("insert into action_entity_identity_map values(?,?,?,?)",
               ("generic", "collision-owner", occupied_id, second_receipt))
    db.commit()
    with sqlite3.connect(path) as destination:
        db.backup(destination)
    db.close()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    fixture = {"candidate_digest": context["digest"], "candidate_id": 40,
        "consumer_run_id": 9006, "review_id": 7, "audit_run_id": 9007,
        "selection_id": 18, "new_review_id": new_review_id,
        "new_audit_run_id": audit["id"], "occupied_action_id": occupied_id,
        "provider_receipt_id": first_receipt, "lineage_receipt_id": second_receipt,
        "new_supplement_id": old_supplements[-1] + 20}
    (out / "fixture.json").write_text(json.dumps(fixture))
    report = {"predecessor_root": str(predecessor), "cycles": []}
    for cycle in (1, 2):
        task_inverse = restore_task_entities(db, context["task_source_metadata"], context["task_metadata"])
        run_inverse = restore_run_entities(db, context["run_source_metadata"], context["run_metadata"],
            task_metadata=context["task_metadata"], task_inverse_metadata=task_inverse)
        restore_run_adoptions(db, context["adoption_source"], forward,
            task_metadata=context["task_metadata"], task_inverse_metadata=task_inverse,
            run_inverse_metadata=run_inverse)
        db.commit()
        if db.execute("pragma integrity_check").fetchone()[0] != "ok" or db.execute(
                "pragma foreign_key_check").fetchall():
            raise AssertionError("current-fact inverse database integrity/FK checks failed")
        collision = db.execute("select record_type from actions where id=?", (occupied_id,)).fetchone()
        lineage = db.execute("select action_id,provider_receipt_id from action_receipts where id=?",
                             (second_receipt,)).fetchone()
        identity = db.execute("select action_id,receipt_id from action_entity_identity_map "
                              "where source_type='generic' and source_identity='collision-owner'").fetchone()
        if collision is None or collision[0] != "action" or tuple(lineage or ()) != (
                occupied_id, first_receipt) or tuple(identity or ()) != (occupied_id, second_receipt):
            raise AssertionError("current Action collision, provider receipt lineage, or identity changed in inverse")
        source_supplements = [row[0] for row in db.execute(
            "select legacy_key from actions where record_type='candidate_supplements' order by id")]
        if str(old_supplements[-1]) in source_supplements or str(old_supplements[-1] + 20) not in source_supplements:
            raise AssertionError("retired supplement returned or new native supplement was lost in inverse")
        native_review = db.execute("select audit_run_id,decision,result_json,created_at "
            "from actions where record_type='candidate_reviews' and legacy_key=?",
            (str(new_review_id),)).fetchone()
        audit_source_id = next((edge["source_physical_id"]
            for edge in run_inverse["run_handoff"]["identity_edges"]
            if edge["source_kind"] == "agent_runs" and edge["canonical_id"] == audit["id"]),
            audit["id"])
        if native_review is None or native_review[0] != audit_source_id or native_review[3] != "native-review-clock":
            raise AssertionError("new native Audit Run/Review fact changed in inverse")
        portable = json.loads(json.dumps({
            "task_source_metadata": context["task_source_metadata"],
            "task_forward_metadata": context["task_metadata"],
            "run_source_metadata": context["run_source_metadata"],
            "run_forward_metadata": context["run_metadata"],
            "adoption_source_metadata": context["adoption_source"],
            "adoption_forward_metadata": forward,
            "task_inverse_handoff": task_inverse,
            "run_inverse_handoff": run_inverse,
        }))
        db.close()
        try:
            old = old_dao(predecessor, path, out / f"old-dao-{cycle}.json", cycle)
        except RuntimeError as exc:
            report["failed_cycle"] = cycle
            report["failure"] = json.loads(str(exc))
            report_path = out / "report.json"
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
            print(json.dumps(report, sort_keys=True))
            print(f"report={report_path}")
            return 1
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        if db.execute("pragma integrity_check").fetchone()[0] != "ok" or db.execute("pragma foreign_key_check").fetchall():
            raise AssertionError("old DAO write left source database integrity/FK violations")
        reopened = snapshot(db)
        report["cycles"].append({"cycle": cycle, "old_dao": old,
            "portable_metadata_sha256": digest(portable),
            "source_snapshot_sha256": digest(reopened), "source_counts": {
                table: len(reopened[table]) for table in ("actions", "action_receipts", "action_observers")}})
        # The published methods have added one supplement and invalidated the candidate.
        supplement_count = db.execute("select count(*) from actions where record_type='candidate_supplements'").fetchone()[0]
        if supplement_count != 2 + cycle:
            raise AssertionError("expected precisely one durable supplement per old-code cycle")
        # Re-forward the actual old-source DB using the inverse handoff identities.
        from app.run_adoption_schema import capture_run_adoptions, migrate_run_adoptions
        from app.run_entity_schema import capture_run_entities, migrate_run_entities
        from app.task_entity_schema import capture_task_entities, migrate_task_entities
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        adoption_source = capture_run_adoptions(db)
        task_source = capture_task_entities(db)
        run_source = capture_run_entities(db)
        task_forward = migrate_task_entities(db, task_source,
            source_context=portable["task_inverse_handoff"]["source_context"],
            inverse_identity=portable["task_inverse_handoff"]["inverse_identity"])
        run_forward = migrate_run_entities(db, run_source,
            task_metadata=task_forward,
            run_handoff=portable["run_inverse_handoff"]["run_handoff"])
        from app.run_adoption_schema import _identity_maps, _run_id, _source_actions, _task_physical_ref
        task_map, run_map, _ = _identity_maps(db, task_forward, run_forward)
        source_candidate = next(item for item in _source_actions(db)["review_candidates"]
                                if item["legacy_key"] == "40")
        source_consumer = _run_id(run_map, source_candidate["consumer_run_id"])
        native_consumer = db.execute(
            "select task_id,execution_generation,proposal_revision,role from runs where id=?",
            (source_consumer,)).fetchone()
        mapped_candidate_task = _task_physical_ref(db, task_forward, task_map,
            "reply_tasks", source_candidate["task_id"])
        handoff_debug = {"candidate": {key: source_candidate.get(key) for key in
                ("id", "legacy_key", "task_id", "consumer_run_id", "execution_generation",
                 "proposal_revision", "candidate_digest", "invalidated_at", "invalidation_reason")},
            "mapped_consumer_run_id": source_consumer,
            "mapped_candidate_task_id": mapped_candidate_task,
            "task_identity_201": task_map.get(("reply_tasks", 201)),
            "native_consumer": None if native_consumer is None else tuple(native_consumer)}
        try:
            forward = migrate_run_adoptions(db, adoption_source,
                task_metadata=task_forward, run_metadata=run_forward)
        except ValueError as exc:
            report["reforward_failure"] = {"message": str(exc), **handoff_debug}
            report_path = out / "report.json"
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=repr))
            print(json.dumps(report, sort_keys=True, default=repr))
            print(f"report={report_path}")
            return 1
        db.commit()
        adopted = db.execute("select consumer_run_id,invalidated_at,invalidation_reason "
                             "from run_adopted_results where consumer_run_id=9106").fetchone()
        if adopted is None or not adopted[1] or adopted[2] != "supplement":
            raise AssertionError("fresh forward lost the old DAO's candidate invalidation")
        current_collision = db.execute("select record_type from actions where id=?", (occupied_id,)).fetchone()
        current_lineage = db.execute("select action_id,provider_receipt_id from action_receipts where id=?",
                                     (second_receipt,)).fetchone()
        current_identity = db.execute("select action_id,receipt_id from action_entity_identity_map "
                                      "where source_type='generic' and source_identity='collision-owner'").fetchone()
        current_review = db.execute("select audit_run_id,decision,result_json,created_at "
            "from run_adopted_reviews where id=?", (new_review_id,)).fetchone()
        if (current_collision is None or current_collision[0] != "action"
                or tuple(current_lineage or ()) != (occupied_id, first_receipt)
                or tuple(current_identity or ()) != (occupied_id, second_receipt)
                or current_review is None or current_review[0] != audit["id"]
                or current_review[1] != "approve" or current_review[3] != "native-review-clock"):
            raise AssertionError("fresh forward lost current Action lineage or native Review/Audit facts")
        if db.execute("pragma integrity_check").fetchone()[0] != "ok" or db.execute(
                "pragma foreign_key_check").fetchall():
            raise AssertionError("fresh forward database integrity/FK checks failed")
        context["task_source_metadata"] = task_source
        context["task_metadata"] = task_forward
        context["run_source_metadata"] = run_source
        context["run_metadata"] = run_forward
        context["adoption_source"] = adoption_source
        post_forward = snapshot(db)
        if len(post_forward["run_adopted_supplements"]) != 2 + cycle:
            raise AssertionError("fresh forward lost an old-code supplement")
        report["cycles"][-1]["forward_snapshot_sha256"] = digest(post_forward)
        report["cycles"][-1]["forward_counts"] = {
            table: len(post_forward[table]) for table in ("actions", "action_receipts",
                "action_observers", "run_adopted_results", "run_adopted_reviews",
                "run_adopted_selections", "run_adopted_supplements", "run_execution_progress")}
        # The next inverse starts from the freshly forwarded, committed facts.
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
    db.close()
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, sort_keys=True))
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
