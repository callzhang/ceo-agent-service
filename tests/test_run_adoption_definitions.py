import json
import sqlite3
from pathlib import Path

import pytest

from app.action_entity_definitions import ACTION_IDENTITY_MAP_DDL
from app.action_storage import ACTION_RECORD_TYPES, SOURCE_COLUMNS
from app.run_adoption_definitions import (
    ADOPTED_SOURCE_KINDS, INDEXES, TABLES, TRIGGERS, adoption_source_fields,
    field_map, receipt_schema_patch, schema_sql, validate_field_map,
)


ROOT = Path(__file__).resolve().parents[1]


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("pragma foreign_keys=on")
    db.executescript("create table tasks(id integer primary key)")
    db.executescript("""create table runs(
      id integer primary key, task_id integer references tasks(id), parent_id integer references runs(id),
      role text, status text, proposal_revision integer, execution_generation text)""")
    db.executescript("create table emails(id integer primary key)")
    db.executescript("create table configs(id integer primary key)")
    db.executescript("create table provenance(id integer primary key)")
    db.executescript("""create table business(id integer primary key, task_public_id text unique,
      attention_item_public_id text unique, project_public_id text unique,
      anchor_public_id text unique, work_cluster_public_id text unique)""")
    action_target = (ROOT / "docs/action-entity-target.sql").read_text()
    action_start = action_target.lower().index("create table actions")
    action_sql = action_target[action_start:action_target.index(";", action_start) + 1]
    db.execute(action_sql)
    db.execute(ACTION_IDENTITY_MAP_DDL[0])
    for sql in receipt_schema_patch():
        db.execute(sql)
    for statement in action_target.splitlines():
        statement = statement.strip()
        if (statement.lower().startswith(("create index ", "create unique index "))
                and " on actions" in statement.lower()):
            db.execute(statement)
    for sql in TABLES.values():
        db.execute(sql)
    for sql in INDEXES.values():
        db.execute(sql)
    for sql in TRIGGERS:
        db.execute(sql)
    return db


def _run(db, run_id, task_id, role, status="completed", revision=1, parent=None):
    db.execute("insert or ignore into tasks values(?)", (task_id,))
    db.execute("insert into runs values(?,?,?,?,?,?,?)",
               (run_id, task_id, parent, role, status, revision, "gen"))


def _result(db, consumer, digest="digest-a"):
    db.execute("insert into run_adopted_results values(?,?,?,?,?,?,?,?)",
               (consumer, 0, None, digest, '{"decision_options":[{"key":"yes","body":"x"}]}', "", "", "now"))


def _review(db, consumer, audit, digest="digest-a", decision="approve"):
    db.execute("insert into run_adopted_reviews values(?,?,?,?,?,?)",
               (audit, consumer, digest, decision, '{"review":"ok"}', "now"))


def test_field_map_covers_current_source_fields_and_artifacts_are_exact():
    mapping = field_map()
    assert validate_field_map(mapping)
    assert set(mapping) == set(ADOPTED_SOURCE_KINDS)
    assert all(set(mapping[kind]) == set(adoption_source_fields()[kind]) for kind in mapping)
    assert all(mapping[kind][field][0] is not None or field == "branch_json"
               for kind in mapping for field in mapping[kind])
    assert mapping["candidate_selections"]["branch_json"][0] is None
    assert all("run_projections" not in str(dest) for source in mapping.values() for dest in source.values())
    assert (ROOT / "docs/run-adoption-target.sql").read_text() == schema_sql()
    artifact = json.loads((ROOT / "docs/run-adoption-field-map.json").read_text())
    assert artifact["fields"] == _serialized_map()
    assert artifact["source_constraints"]["candidate_to_review"].startswith("1:N")
    assert set(SOURCE_COLUMNS["review_candidates"]) == set(mapping["review_candidates"])
    unknown = {kind: dict(fields) for kind, fields in mapping.items()}
    unknown["review_candidates"]["surprise"] = ("runs", "unexpected")
    with pytest.raises(ValueError, match="review_candidates"):
        validate_field_map(unknown)


def _serialized_map():
    return [
        {"source_kind": kind, "source_field": field,
         "target_table": dest[0], "target_field_or_expression": dest[1]}
        for kind, fields in field_map().items() for field, dest in fields.items()
    ]


def test_candidate_review_selection_and_progress_reference_closure():
    db = _db()
    for run_id, task_id, role, status in (
        (10, 1, "consumer", "completed"), (11, 1, "audit", "completed"),
        (12, 1, "audit", "completed"), (20, 2, "consumer", "failed"),
        (21, 2, "audit", "completed"), (22, 2, "audit", "completed"),
    ):
        _run(db, run_id, task_id, role, status, parent=10 if run_id in (11, 12) else (20 if run_id in (21, 22) else None))
    _result(db, 10)
    _review(db, 10, 11)
    _review(db, 10, 12, decision="return")  # candidate → reviews is 1:N
    _result(db, 20)
    _review(db, 20, 21)
    db.execute("insert into run_adopted_selections values(?,?,?,?,?,?)", (1, 10, 11, "yes", "selected", "now"))
    db.execute("insert into run_adopted_supplements values(1,10,'one','now')")
    db.execute("insert into run_adopted_supplements values(2,10,'two','later')")
    db.execute("insert into run_execution_progress values(10,11,11,'running','worker','lease','{}','now','now')")
    # Logical completion and external execution progress have independent state.
    assert db.execute("select status from runs where id=10").fetchone() == ("completed",)
    assert db.execute("select status from run_execution_progress where consumer_run_id=10").fetchone() == ("running",)
    assert db.execute("select count(*) from run_adopted_reviews where consumer_run_id=10").fetchone() == (2,)
    assert db.execute("select count(*) from run_adopted_supplements where consumer_run_id=10").fetchone() == (2,)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into run_adopted_reviews values(?,?,?,?,?,?)",
                   (22, 20, "wrong-digest", "approve", "{}", "now"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into run_adopted_selections values(?,?,?,?,?,?)", (2, 10, 12, "yes", "selected", "now"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into run_adopted_selections values(?,?,?,?,?,?)", (3, 20, 11, "yes", "selected", "now"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into run_execution_progress values(20,11,null,'done','','','{}','now','now')")
    # Source selection bodies are derived from the bound candidate; target stores only option_key.
    assert "branch_json" not in {r[1] for r in db.execute("pragma table_info(run_adopted_selections)")}
    assert db.execute("select option_key from run_adopted_selections").fetchone() == ("yes",)


def test_consumer_and_audit_role_parent_and_predecessor_run_types_are_exact():
    db = _db()
    _run(db, 1, 1, "audit")
    _run(db, 2, 1, "consumer")
    _run(db, 3, 1, "audit", parent=2)
    _run(db, 4, 1, "audit", parent=1)
    _run(db, 5, 1, "consumer", parent=2)
    _run(db, 6, 1, "audit", parent=2)
    with pytest.raises(sqlite3.IntegrityError):
        _result(db, 1)
    _result(db, 2)
    with pytest.raises(sqlite3.IntegrityError):
        _review(db, 2, 4)
    _review(db, 2, 3)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update run_adopted_results set predecessor_audit_run_id=5 where consumer_run_id=2")
    db.execute("update run_adopted_results set predecessor_audit_run_id=3 where consumer_run_id=2")
    with pytest.raises(sqlite3.IntegrityError):
        _review(db, 2, 6, digest="wrong-digest")


def test_receipts_keep_action_provider_and_observer_lineage_with_per_receipt_runs():
    db = _db()
    _run(db, 10, 1, "consumer")
    _run(db, 20, 2, "consumer")
    # Existing Action entity code and generated columns remain the actual owner.
    db.commit()
    db.execute("pragma foreign_keys=off")  # Action's unrelated composite parents are outside this fixture.
    db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values(1,?,?,jsonb(?),jsonb('[]'))",
               (ACTION_RECORD_TYPES.code("action"), None, '{"operation":"send","status":"done"}'))
    db.commit()
    db.execute("pragma foreign_keys=on")
    db.commit()
    db.execute("insert into action_receipts(id,action_id,execution_run_id,action_index,status,provider_result_json,created_at) values(1,1,10,0,'verified','{}','now')")
    db.execute("insert into action_receipts(id,action_id,execution_run_id,action_index,status,provider_result_json,created_at) values(2,1,20,0,'verified','{}','now')")
    db.execute("insert into action_receipts(id,action_id,provider_result_json,created_at) values(3,1,'{}','now')")
    db.execute("update action_receipts set provider_receipt_id=3 where id=1")
    db.execute("insert into action_observers values(1,999,1,'now')")
    assert db.execute("select count(distinct execution_run_id) from action_receipts where action_id=1 and execution_run_id is not null").fetchone() == (2,)
    assert db.execute("select provider_receipt_id from action_receipts where id=1").fetchone() == (3,)
    assert db.execute("select agent_run_id from action_observers").fetchone() == (999,)
    assert "execution_id" not in {r[1] for r in db.execute("pragma table_info(action_receipts)")}
    assert any("provider_receipt_id" in sql for sql in receipt_schema_patch())
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into action_receipts(id,action_id,execution_run_id,action_index) values(4,1,9999,0)")


def test_identity_map_keeps_typed_non_derivable_public_ids_and_artifact_exactness():
    db = _db()
    _run(db, 10, 1, "consumer")
    db.execute("insert into run_adoption_identity_map values('review_candidates',100,'1','integer',10,null)")
    db.execute("insert into run_adoption_identity_map values('candidate_supplements',200,'8','integer',10,3)")
    assert db.execute("select source_public_id,source_public_id_type,target_child_id from run_adoption_identity_map where source_physical_id=100").fetchone() == ("1", "integer", None)
    assert db.execute("select target_child_id from run_adoption_identity_map where source_physical_id=200").fetchone() == (3,)
    retirement = (ROOT / "docs/run-adoption-retirement.sql").read_text()
    assert "candidate_execution_alias" in retirement
    assert "branch_json" in retirement and "derived" in retirement
    assert "execution_id" in retirement and "execution_run_id" in retirement


def test_receipt_patch_replaces_only_candidate_execution_association():
    patch = "\n".join(receipt_schema_patch())
    assert "execution_run_id integer references runs(id)" in patch
    assert "idx_action_receipts_execution_run_order on action_receipts(execution_run_id,action_index)" in patch
    assert "where execution_run_id is not null" in patch
    for required in ("provider_result_json", "provider_receipt_id", "action_observers", "trg_action_receipt_lineage_immutable"):
        assert required in patch
    assert "execution_id" not in patch
    assert "candidate_execution_alias" not in patch
