import sqlite3
import json
from pathlib import Path

import pytest

from app.task_run_entity_assembly import (
    _unique_declared_objects, task_run_entity_assembly_objects,
    validate_assembled_object_names,
)
from app.task_run_entity_definitions import task_run_entity_objects
from tools.inspect_task_run_entities import inspect

ROOT = Path(__file__).resolve().parents[1]


def test_assembled_contract_has_unique_names_and_executes_with_native_prerequisites():
    objects = task_run_entity_assembly_objects()
    assert len(objects) == len(set(objects))
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    from app.task8_storage import register_task8_functions
    register_task8_functions(db)
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    validate_assembled_object_names(db)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'")} == set(objects)
    assert {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")} >= {
        "actions", "runs", "tasks", "run_adopted_results", "run_execution_progress",
        "action_receipts", "emails", "schedules", "configs",
    }


def test_object_dictionary_rejects_duplicate_actual_sqlite_names():
    with pytest.raises(ValueError, match="duplicate target schema object: idx_same"):
        _unique_declared_objects((
            {"idx_same": "CREATE INDEX idx_same ON a(id)"},
            {"idx_same": "CREATE UNIQUE INDEX idx_same ON b(id)"},
        ))


def test_target_factory_does_not_call_whole_source_or_old_task_run_factories(monkeypatch):
    import app.current_database_schema as current_database
    import app.current_schema_manifest as manifest
    import app.logical_run_definitions as old_run_source
    import app.logical_run_current_schema as old_run
    import app.task_lifecycle_current_schema as old_task

    def forbidden(*_args, **_kwargs):
        raise AssertionError("target assembly called an old whole/source Task/Run factory")

    monkeypatch.setattr(current_database, "create_current_database_schema", forbidden)
    monkeypatch.setattr(manifest, "current_objects", forbidden)
    monkeypatch.setattr(old_task, "create_current_task20_schema", forbidden)
    monkeypatch.setattr(old_run, "create_current_run11_schema", forbidden)
    monkeypatch.setattr(old_run_source, "logical_run_objects", forbidden)
    objects = task_run_entity_assembly_objects()
    assert {"actions", "tasks", "runs", "emails", "provenance", "business", "state", "provider_state"} <= set(objects)


def test_retained_native_owner_declarations_match_current_source_oracle():
    from app.current_schema_manifest import current_objects

    target = task_run_entity_assembly_objects()
    source = {name: (table, sql) for _kind, name, table, sql in current_objects(email_ledger=True)}
    target_owned_tables = {"tasks", "runs", "actions", "run_provenance", "action_receipts"}
    retained = {name: sql for name, (table, sql) in source.items() if table not in target_owned_tables}
    assert {name: target[name] for name in retained} == retained


def test_run_source_metadata_matches_current_native_factory_oracle():
    from app.current_schema_manifest import current_objects
    from app.run_entity_definitions import run_entity_field_map, validate_run_entity_contract

    validate_run_entity_contract()
    source_runs = next(sql for _kind, name, table, sql in current_objects(email_ledger=True)
                       if name == "runs" and table == "runs")
    source_db = sqlite3.connect(":memory:")
    source_db.execute(source_runs)
    source = {row[1]: (row[2].upper(), "PRIMARY KEY" if row[5] else "NOT NULL" if row[3] else "NULL allowed", row[4])
              for row in source_db.execute("PRAGMA table_info(runs)")}
    fields = {(row["source_kind"], row["source_field"]): row for row in run_entity_field_map()}
    for source_kind in ("agent_runs", "agent_runtime_attempts", "agent_run_state_events", "scheduled_task_runs",
                        "workbench_events", "codex_session_search_index", "email_classifier_runtime_samples",
                        "setup_wizard_events"):
        for field, metadata in source.items():
            row = fields[(source_kind, field)]
            assert (row["sql_type"], row["null_contract"], row["source_default"]) == metadata


def test_complete_assembly_sql_artifact_is_deterministic():
    from app.run_adoption_definitions import field_map as adoption_field_map
    from app.run_entity_definitions import run_entity_field_map
    from app.schema_sql import serialize_schema_sql
    from app.task_run_entity_definitions import task_run_field_map

    objects = task_run_entity_assembly_objects()
    expected = serialize_schema_sql(objects)
    assert (ROOT / "docs/task-run-entity-assembly-target.sql").read_text() == expected
    field_map = json.loads((ROOT / "docs/task-run-entity-assembly-field-map.json").read_text())
    adoption_rows = [
        {"source_kind": kind, "source_field": field, "target_table": destination[0],
         "target_field_or_expression": destination[1]}
        for kind, fields in adoption_field_map().items() for field, destination in fields.items()
    ]
    assert field_map == {"task_fields": list(task_run_field_map()),
                         "run_fields": run_entity_field_map(), "adoption_fields": adoption_rows}
    assert len(field_map["task_fields"]) == 287
    assert len(field_map["run_fields"]) == 8 * 79
    assert len(field_map["adoption_fields"]) == 43
    assert (ROOT / "docs/task-run-entity-assembly-retirement.sql").read_text().startswith(
        "-- Unactivated Task/Run assembly retirement inventory; not executable migration SQL.\n")


def test_committed_assembly_sql_artifact_executes_and_matches_complete_catalog():
    from app.task_run_entity_assembly import task_run_entity_assembly_objects

    expected = task_run_entity_assembly_objects()
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript((ROOT / "docs/task-run-entity-assembly-target.sql").read_text())
    actual = {name: sql for name, sql in db.execute(
        "SELECT name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'")}
    assert actual == expected
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_adoption_receipt_parent_is_real_consumer_progress_and_run_role_is_enforced():
    from tests.test_run_adoption_definitions import _run, _result, _review
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    from app.task8_storage import register_task8_functions
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    _run(db, 10, 1, "consumer")
    _run(db, 11, 1, "audit", parent=10)
    db.execute("insert into run_projections values(10,'{}')")
    _result(db, 10)
    _review(db, 10, 11)
    db.execute("insert into run_adopted_selections values(1,10,11,'yes','selected','now')")
    db.execute("insert into run_execution_progress values(10,11,11,'running','worker','lease','{}','now','now')")
    assert db.execute("select projection_json from run_projections where run_id=10").fetchone() == ("{}",)
    assert db.execute("select candidate_json from run_adopted_results where consumer_run_id=10").fetchone()[0].startswith("{")
    from app.action_entity_storage import create_action
    action_id = create_action(db, values={})["id"]
    db.execute("insert into action_receipts(action_id,execution_run_id,action_index) values(?,?,?)", (action_id, 10, 1))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into action_receipts(action_id,execution_run_id,action_index) values(?,?,?)", (action_id, 11, 2))
    _run(db, 20, 2, "audit")
    with pytest.raises(sqlite3.IntegrityError):
        _result(db, 20)
    work_task = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('work_summary','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    work_run = db.execute("insert into runs(task_id,role,status) values(?,'consumer','completed') returning id", (work_task,)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="Consumer Run for reply Task"):
        db.execute("insert into run_adopted_results values(?,?,?,?,?,?,?,?)", (work_run, 0, None, "bad", "{}", "", "", "now"))


def test_retained_action_parent_guards_execute_against_new_owner_columns():
    from app import action_storage
    from tests.test_action_receipt_source import source_values
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    from app.task8_storage import register_task8_functions
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    task_id = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    agent = db.execute("insert into runs(task_id,role,status) values(?,'agent','completed') returning id", (task_id,)).fetchone()[0]
    def insert_execution_receipt(run_id, token):
        values = action_storage.DEFAULTS["agent_execution_receipts"] | source_values("agent_execution_receipts", token=token)
        values.update(created_at="now", agent_run_id=run_id)
        payload_values = {key: value for key, value in values.items() if key != "agent_run_id"}
        payload = action_storage._action_payload(db, "agent_execution_receipts", payload_values, execution_owner=True)
        db.execute("insert into actions(_kind,legacy_key,payload_json,agent_run_id) values(?,?,?,?)",
                   (action_storage.EXECUTION_ACTION_RECORD_TYPES.code("agent_execution_receipts"), token, payload, run_id))
    insert_execution_receipt(agent, "1")
    native_task = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('work_summary','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    native = db.execute("insert into runs(task_id,role,status) values(?,'consumer','completed') returning id", (native_task,)).fetchone()[0]
    db.execute("insert into run_provenance(run_id,source_type) values(?,'task_agent_runs')", (native,))
    with pytest.raises(sqlite3.IntegrityError, match="action parent type mismatch"):
        insert_execution_receipt(native, "2")


def test_meeting_action_claim_uses_meeting_task_and_real_task_fk():
    from app import action_storage
    from app.current_action_schema import current_action_payloads
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    from app.task8_storage import register_task8_functions
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    meeting_params = '{"meeting_id":"m","title":"title","source_json":"{}","participants_json":"[]","ended_at":"now","eligible_at":"now"}'
    meeting = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('meeting_alignment','pending',jsonb(?),'now','now') returning id", (meeting_params,)).fetchone()[0]
    reply = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    payload = action_storage._action_payload(db, "meeting_alignment_delivery_claims", {"claim_token": "claim"})
    code = current_action_payloads().record_types.code("meeting_alignment_delivery_claims")
    insert = "insert into actions(_kind,legacy_key,payload_json,td_claim_job_id) values(?,?,?,?)"
    db.execute(insert, (code, str(meeting), payload, meeting))
    with pytest.raises(sqlite3.IntegrityError, match="meeting job"):
        db.execute(insert, (code, str(reply), payload, reply))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, (code, "999999", payload, 999999))


def test_feedback_action_references_actual_task_owned_domain_parents():
    from app.current_action_schema import current_action_payloads
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    from app.task8_storage import register_task8_functions
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    action_parent_sql = "\n".join(sql for name, sql in objects.items()
                                  if name.startswith("trg_actions_") and "parent" in name)
    assert "task_run_identity_map" not in action_parent_sql
    assert "source_kind" not in action_parent_sql
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    task_id = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('feedback_processing','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    db.execute("insert into feedback_processing_batches(batch_id) values('batch')")
    db.execute("insert into feedback_iteration_decisions(id,batch_id,workbench_task_id,workbench_turn_id,decision_json) values(?,'batch','workbench','turn','{}')", (task_id,))
    db.execute("insert into feedback_processing_items(task_id,feedback_key,batch_id,workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,commit_sha,test_evidence_json,restart_evidence_json,health_evidence_json,note,resolved_at,current_round_id) values(?, 'feedback','batch','','',0,0,'','{}','{}','{}','','',0)", (task_id,))
    second_task_id = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('feedback_processing','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    db.execute("insert into feedback_processing_items(task_id,feedback_key,batch_id,workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,commit_sha,test_evidence_json,restart_evidence_json,health_evidence_json,note,resolved_at,current_round_id) values(?, 'feedback2','batch','','',0,0,'','{}','{}','{}','','',0)", (second_task_id,))
    db.execute("insert into feedback_processing_rounds(id,feedback_key,round_number,status) values(?,'feedback',1,'pending')", (task_id,))
    code = current_action_payloads().record_types.code("feedback_iteration_decision_items")
    insert = "insert into actions(_kind,legacy_key,payload_json,fb_decision_id,fb_feedback_key,fb_round_id) values(?,?,jsonb('[]'),?,?,?)"
    db.execute(insert, (code, "1", task_id, "feedback", task_id))
    for key, parents in (("2", (999, "feedback", task_id)),
                         ("3", (task_id, "missing", task_id)),
                         ("4", (task_id, "feedback2", 999))):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            db.execute(insert, (code, key, *parents))


def test_reply_memory_action_references_real_reply_task_and_rejects_wrong_kind():
    from app import action_storage
    from app.current_action_schema import current_action_payloads
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    from app.task8_storage import register_task8_functions
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    reply_id = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    work_id = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('work_summary','pending',jsonb('{}'),'now','now') returning id").fetchone()[0]
    kind = "task_memory_write_events"
    values = dict.fromkeys(current_action_payloads().layouts[kind].fields)
    values.update(execution_generation="g", memories_json="[]", written_memory_ids_json="[]", status="pending",
                  attempts=0, available_at="now", error="", skip_reason="", lease_owner="",
                  lease_expires_at="", started_at="", created_at="now", updated_at="now")
    payload = action_storage._action_payload(db, kind, values)
    code = current_action_payloads().record_types.code(kind)
    insert = "insert into actions(_kind,legacy_key,payload_json,td_reply_task_id) values(?,?,?,?)"
    db.execute(insert, (code, "1", payload, reply_id))
    with pytest.raises(sqlite3.IntegrityError, match="reply task"):
        db.execute(insert, (code, "2", payload, work_id))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, (code, "3", payload, 999999))


def test_okr_items_reference_real_okr_task_and_reject_wrong_kind():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    okr = db.execute("INSERT INTO tasks(kind,status,parameters_json,created_at,updated_at) VALUES('okr_review','pending',jsonb(?),'now','now') RETURNING id", ('{"conversation_id":"c","conversation_title":"t","period_end":"e","period_label":"l","period_start":"s","trigger_message_id":"m","trigger_sender":"u","trigger_text":"x"}',)).fetchone()[0]
    work = db.execute("INSERT INTO tasks(kind,status,parameters_json,created_at,updated_at) VALUES('reply','pending',jsonb('{}'),'now','now') RETURNING id").fetchone()[0]
    db.execute("INSERT INTO okr_review_items(request_id,objective_title,kr_title) VALUES(?,?,?)", (okr, 'Objective', 'KR'))
    with pytest.raises(sqlite3.IntegrityError, match="OKR item parent"):
        db.execute("INSERT INTO okr_review_items(request_id,objective_title,kr_title) VALUES(?,?,?)", (work, 'Objective', 'KR'))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO okr_review_items(request_id,objective_title,kr_title) VALUES(?,?,?)", (999999, 'Objective', 'KR'))


def test_task_kind_is_immutable_while_lifecycle_status_remains_mutable():
    db = sqlite3.connect(":memory:")
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    task_id = db.execute("INSERT INTO tasks(kind,status,parameters_json,created_at,updated_at) VALUES('reply','pending',jsonb('{}'),'now','now') RETURNING id").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="task record type is immutable"):
        db.execute("UPDATE tasks SET kind='work_summary' WHERE id=?", (task_id,))
    db.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
    assert db.execute("SELECT kind,status FROM tasks WHERE id=?", (task_id,)).fetchone() == ('reply', 'running')


def test_inspector_reports_index_names_not_sequence_numbers(tmp_path):
    path = tmp_path / "schema.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sample(id INTEGER, left_value TEXT, right_value TEXT)")
    db.execute("CREATE INDEX idx_sample_left ON sample(left_value)")
    db.execute("CREATE INDEX idx_sample_right ON sample(right_value)")
    db.close()
    assert set(inspect(str(path))["indexes"]["sample"]) == {"idx_sample_left", "idx_sample_right"}
