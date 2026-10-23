import json
import sqlite3
from pathlib import Path

import pytest

from app.run_entity_definitions import (
    run_entity_field_map, run_entity_objects,
    validate_run_entity_contract,
)
from app.task_run_entity_definitions import task_run_entity_objects
from app.run_entity_definitions import SOURCE_DECLARED_FIELDS


ROOT = Path(__file__).resolve().parents[1]


def database():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    db.executescript("CREATE TABLE schedules(id INTEGER PRIMARY KEY);"
                     "CREATE TABLE configs(schedule_snapshot_public_id INTEGER PRIMARY KEY);")
    for ddl in run_entity_objects().values():
        db.execute(ddl)
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (1,'workbench_turn','queued',jsonb('{}'),'now','now')")
    db.executescript("INSERT INTO schedules VALUES (1); INSERT INTO configs VALUES (8);")
    return db


def insert_attempt(db, values):
    columns = [row[1] for row in db.execute("PRAGMA table_info(runtime_attempts)") if row[1] != "id"]
    db.execute("INSERT INTO runtime_attempts(" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", values)


def valid_attempt(parent_id=1, workload_kind="agent_run", workload_key="1", attempt=1,
                  status="completed", mode="fresh", source_session="",
                  purpose="normal", retry_policy="", schema_id=""):
    return (parent_id, workload_kind, workload_key, attempt, "route", "codex", "oauth",
            "model", mode, source_session, purpose, retry_policy, schema_id, "session",
            "transcript", 0, 1, status, "", "", 0, "owner", "expiry", "", "result",
            "start", "finish", "create", "update")


def test_contract_is_valid_and_artifacts_are_exact():
    validate_run_entity_contract()
    source = sqlite3.connect(":memory:")
    from app.logical_run_definitions import logical_run_objects
    source.execute(logical_run_objects()["runs"])
    source_columns = {row[1]: row for row in source.execute("PRAGMA table_info(runs)")}
    assert tuple(source_columns) == SOURCE_DECLARED_FIELDS
    expected = run_entity_field_map()
    assert next(row for row in expected if row["source_kind"] == "agent_runtime_attempts"
                and row["source_field"] == "parent_id")["target_owner"] == (
                    "typed Task/Meeting workload key or source fact")
    for row in expected:
        _, name, sql_type, not_null, default, primary_key = source_columns[row["source_field"]]
        null_contract = "PRIMARY KEY" if primary_key else "NOT NULL" if not_null else "NULL allowed"
        assert (row["sql_type"], row["null_contract"], row["source_default"]) == (sql_type.upper(), null_contract, default)
    artifact = json.loads((ROOT / "docs/run-entity-field-map.json").read_text())
    assert artifact == expected
    from app.schema_sql import serialize_schema_sql
    target = serialize_schema_sql(run_entity_objects())
    assert (ROOT / "docs/run-entity-target.sql").read_text() == target
    assert """-- Physical IDs and source public keys are migration metadata only.""" in (ROOT / "docs/run-entity-retirement.sql").read_text()
    assert len(expected) == 8 * len(SOURCE_DECLARED_FIELDS)
    assert all(row["identity_order_semantics"] for row in expected if row["source_field"] == "legacy_key")


def test_committed_run_sql_executes_after_task_and_native_schedule_config_prerequisites():
    from app.configs_storage import create_configs_schema
    from app.schedules_storage import create_schedules_schema
    from app.task8_storage import register_task8_functions

    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    register_task8_functions(db)
    db.executescript((ROOT / "docs/task-run-entity-target.sql").read_text())
    create_schedules_schema(db)
    create_configs_schema(db)
    prerequisites = {name: sql for name, sql in db.execute(
        "SELECT name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'")}
    db.executescript((ROOT / "docs/run-entity-target.sql").read_text())
    actual = {name: sql for name, sql in db.execute(
        "SELECT name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'")}
    expected = prerequisites | run_entity_objects()
    assert actual == expected
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_runs_keep_natural_absence_and_separate_provenance_and_adopted_result():
    db = database()
    db.execute("INSERT INTO runs(id,task_id,parent_id,created_at) VALUES (1,1,NULL,'now')")
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (2,'meeting_alignment','queued',jsonb('{\"meeting_id\":\"m\"}'),'now','now')")
    db.execute("INSERT INTO runs(id,task_id,parent_id,created_at) VALUES (2,2,1,'now')")
    db.execute("INSERT INTO run_provenance VALUES (2,'meeting_alignment_runs',NULL)")
    db.execute("INSERT INTO run_projections VALUES (2,'{}')")
    assert db.execute("SELECT status,error,updated_at FROM runs WHERE id=1").fetchone() == (None, None, None)
    assert db.execute("SELECT source_type,job_id FROM run_provenance WHERE run_id=2").fetchone() == ('meeting_alignment_runs', None)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO run_provenance VALUES (1,'meeting_alignment_runs',NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE run_provenance SET source_type='task_agent_runs' WHERE run_id=2")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE run_provenance SET job_id=4908 WHERE run_id=2")


def test_one_run_has_many_attempts_and_standalone_invocation_is_valid():
    db = database()
    db.execute("INSERT INTO runs(id,created_at) VALUES (1,'now')")
    insert_attempt(db, valid_attempt(status='running'))
    insert_attempt(db, valid_attempt(attempt=2, status='completed', mode='resume', source_session='session'))
    insert_attempt(db, valid_attempt(parent_id=1, workload_kind='task', workload_key='professional-run', status='running'))
    insert_attempt(db, valid_attempt(parent_id=None, workload_kind='standalone', workload_key='x', status='running'))
    insert_attempt(db, valid_attempt(parent_id=None, workload_kind='standalone', workload_key='done', status='completed'))
    assert db.execute("SELECT count(*) FROM runtime_attempts WHERE parent_id=1").fetchone()[0] == 3
    with pytest.raises(sqlite3.IntegrityError):
        insert_attempt(db, valid_attempt())
    db.execute("UPDATE runtime_attempts SET source_session_id='rewritten' WHERE attempt_number=2")
    assert db.execute("SELECT source_session_id FROM runtime_attempts WHERE attempt_number=2").fetchone()[0] == "rewritten"
    with pytest.raises(sqlite3.IntegrityError):
        missing_lease = list(valid_attempt(parent_id=None, workload_kind='standalone', workload_key='y', status='running'))
        missing_lease[21:23] = ['', '']
        insert_attempt(db, missing_lease)
    with pytest.raises(sqlite3.IntegrityError):
        missing_owned_operation_lease = list(valid_attempt(
            parent_id=1, workload_kind='task', workload_key='professional-run', status='running'))
        missing_owned_operation_lease[21:23] = ['', '']
        insert_attempt(db, missing_owned_operation_lease)


def test_schedule_dispatch_state_and_domain_uniqueness_are_preserved():
    db = database()
    row = (1, 'scheduled', '2026-01-01', 'pending', '', 'event', 'reply_task', '1', None, '2026-01-01', 1, '{}', 8, 'owner', 'expiry', 'now')
    db.execute("INSERT INTO schedule_occurrences VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    assert db.execute("SELECT dispatch_status FROM schedule_occurrences WHERE id=1").fetchone()[0] == 'pending'
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO schedule_occurrences VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    db.execute("INSERT INTO workbench_events VALUES (NULL,1,1,'created','{}','now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO workbench_events VALUES (NULL,1,1,'other','{}','now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO workbench_events VALUES (NULL,2,1,'created','{}','now')")
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (3,'reply','queued',jsonb('{}'),'now','now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO workbench_events VALUES (NULL,3,1,'created','{}','now')")
    dispatch_sql = db.execute("SELECT sql FROM sqlite_schema WHERE name='idx_schedule_occurrences_dispatch'").fetchone()[0]
    claim_sql = db.execute("SELECT sql FROM sqlite_schema WHERE name='idx_schedule_occurrences_claim'").fetchone()[0]
    assert "WHERE dispatch_status='pending'" in dispatch_sql
    assert "WHERE" not in claim_sql


def test_public_id_collisions_across_source_kinds_are_supported():
    db = database()
    db.executemany("INSERT INTO run_entity_identity_map VALUES (?,?,?,?,?,?,?)", [
        ('agent_runs', 10, '7', 'text', 7, 1, 14),
        ('scheduled_task_runs', 20, '7', 'text', 7, 1, 194000),
    ])
    assert db.execute("SELECT count(*) FROM run_entity_identity_map WHERE public_key='7'").fetchone()[0] == 2


def test_attempt_source_predicates_and_correction_lineage_are_preserved():
    db = database()
    db.execute("INSERT INTO runs(id,created_at) VALUES (1,'now')")
    insert_attempt(db, valid_attempt())
    bad = [
        valid_attempt(attempt=0),
        valid_attempt(status="queued"),
        valid_attempt(workload_kind="agent_run", workload_key="different"),
        valid_attempt(parent_id=None, workload_kind="agent_run", workload_key="1"),
        valid_attempt(mode="resume", source_session="   "),
        valid_attempt(purpose="normal", retry_policy="policy"),
        valid_attempt(purpose="result_validation_correction", retry_policy=" ", schema_id="schema"),
    ]
    for values in bad:
        with pytest.raises(sqlite3.IntegrityError):
            insert_attempt(db, values)
    insert_attempt(db, valid_attempt(attempt=2, purpose="result_validation_correction",
                                    retry_policy="policy", schema_id="schema"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE runtime_attempts SET validation_retry_policy_id='other' WHERE attempt_number=2")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE runtime_attempts SET attempt_purpose='normal',validation_retry_policy_id='',validation_result_schema_id='' WHERE attempt_number=2")


def test_native_attempt_observation_updates_match_actual_source_and_assembled_target():
    from app.logical_run_definitions import logical_run_objects
    from app.task_run_entity_assembly import task_run_entity_assembly_objects
    from app.task8_storage import register_task8_functions

    source = sqlite3.connect(":memory:")
    source.execute("PRAGMA foreign_keys=ON")
    from app.task_lifecycle_current_schema import create_current_task20_schema
    from app.configs_storage import create_configs_schema
    from app.schedules_storage import create_schedules_schema
    create_schedules_schema(source)
    create_configs_schema(source)
    create_current_task20_schema(source)
    for ddl in logical_run_objects().values():
        source.execute(ddl)

    target = sqlite3.connect(":memory:")
    target.execute("PRAGMA foreign_keys=ON")
    register_task8_functions(target)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            target.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            target.execute(ddl)

    target_columns = [row[1] for row in target.execute("PRAGMA table_info(runtime_attempts)") if row[1] != "id"]
    attempt_values = dict(zip(target_columns, valid_attempt(parent_id=None, workload_kind="structured",
                                                           workload_key="x", status="starting"), strict=True))
    attempt_values.update({"session_id": "", "transcript_reference": "",
                           "transcript_start": 0, "transcript_end": 0})

    source_columns = [row[1] for row in source.execute("PRAGMA table_info(runs)")]
    source_values = {"record_type": "agent_runtime_attempts", "legacy_key": "101", **attempt_values}
    source_insert_columns = [name for name in source_columns if name in source_values and name != "id"]
    source.execute(
        "INSERT INTO runs(" + ",".join(source_insert_columns) + ") VALUES (" +
        ",".join("?" for _ in source_insert_columns) + ")",
        [source_values[name] for name in source_insert_columns],
    )
    target.execute(
        "INSERT INTO runtime_attempts(" + ",".join(target_columns) + ") VALUES (" +
        ",".join("?" for _ in target_columns) + ")",
        [attempt_values[name] for name in target_columns],
    )

    def observe(db, table, predicate, terminal_status):
        db.execute(f"UPDATE {table} SET status='running',lease_owner='owner',lease_expires_at='expiry' WHERE {predicate}")
        db.execute(
            f"UPDATE {table} SET status=?,session_id='observed-session',transcript_reference='observed-transcript',"
            f"transcript_start=2,transcript_end=5,finished_at='finish',lease_owner='',lease_expires_at='' WHERE {predicate}",
            (terminal_status,),
        )

    observe(source, "runs", "record_type='agent_runtime_attempts'", "completed")
    observe(target, "runtime_attempts", "workload_key='x'", "completed")
    assert source.execute("SELECT status,session_id,transcript_reference,transcript_start,transcript_end FROM runs").fetchone() == target.execute(
        "SELECT status,session_id,transcript_reference,transcript_start,transcript_end FROM runtime_attempts"
    ).fetchone() == ("completed", "observed-session", "observed-transcript", 2, 5)

    for db, table, predicate in ((source, "runs", "record_type='agent_runtime_attempts'"),
                                 (target, "runtime_attempts", "workload_key='x'")):
        db.execute(f"UPDATE {table} SET status='starting',session_id='',transcript_reference='',transcript_start=0,transcript_end=0,lease_owner='owner',lease_expires_at='expiry' WHERE {predicate}")
        db.execute(f"UPDATE {table} SET status='running' WHERE {predicate}")
        db.execute(
            f"UPDATE {table} SET status='failed',session_id='failed-session',transcript_reference='failed-transcript',"
            f"transcript_start=3,transcript_end=4,finished_at='finish',lease_owner='',lease_expires_at='' WHERE {predicate}"
        )
    assert source.execute("SELECT status,session_id,transcript_reference,transcript_start,transcript_end FROM runs").fetchone() == target.execute(
        "SELECT status,session_id,transcript_reference,transcript_start,transcript_end FROM runtime_attempts"
    ).fetchone() == ("failed", "failed-session", "failed-transcript", 3, 4)


def test_professional_runs_cannot_be_parents_or_attach_after_becoming_parent():
    db = database()
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (2,'work_summary','queued',jsonb('{}'),'now','now')")
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (3,'reply','queued',jsonb('{}'),'now','now')")
    db.execute("INSERT INTO runs(id,task_id,created_at) VALUES (10,2,'now')")
    db.execute("INSERT INTO run_provenance VALUES (10,'task_agent_runs',NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE runs SET task_id=3 WHERE id=10")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO runs(id,parent_id,created_at) VALUES (11,10,'now')")
    db.execute("INSERT INTO runs(id,created_at) VALUES (12,'now')")
    db.execute("INSERT INTO runs(id,parent_id,created_at) VALUES (13,12,'now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO run_provenance VALUES (12,'task_agent_runs',NULL)")


def test_source_required_run_failure_and_schedule_facts_reject_nulls():
    db = database()
    db.execute("INSERT INTO runs(id,created_at) VALUES (1,'now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO run_failures(run_id,phase,structured_error_json,created_at) VALUES (1,NULL,'{}','now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO run_failures(run_id,phase,structured_error_json,created_at) VALUES (1,'audit',NULL,'now')")
    occurrence = (1,'scheduled','2026-01-01','pending','', 'event', 'reply_task','1',None,
                  '2026-01-01',1,'{}',8,'owner','expiry','now')
    insert_sql = "INSERT INTO schedule_occurrences VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    db.execute(insert_sql, occurrence)
    fields = ["scheduled_task_id","trigger_kind","scheduled_for","dispatch_status","skip_or_error_reason","event_id","execution_kind","execution_id","dispatched_at","first_scheduled_for","occurrence_count","result_summary","snapshot_id","lease_owner","lease_expires_at","created_at"]
    for index in (0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 13, 15):
        invalid = list(occurrence)
        invalid[index] = None
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO schedule_occurrences(" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")", invalid)


def test_only_one_running_eligible_run_exists_per_task():
    db = database()
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (2,'work_summary','queued',jsonb('{}'),'now','now')")
    db.execute("INSERT INTO runs(id,task_id,status,created_at) VALUES (10,2,'running','now')")
    db.execute("INSERT INTO run_provenance VALUES (10,'task_agent_runs',NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO runs(id,task_id,status,created_at) VALUES (11,2,'running','now')")
    db.execute("INSERT INTO runs(id,task_id,status,created_at) VALUES (12,2,'completed','now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE runs SET status='running' WHERE id=12")
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (4,'reply','queued',jsonb('{}'),'now','now')")
    db.execute("INSERT INTO runs(id,task_id,status,created_at) VALUES (20,4,'running','now')")
    db.execute("INSERT INTO runs(id,task_id,status,created_at) VALUES (21,4,'running','now')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO run_provenance VALUES (20,'task_agent_runs',NULL)")


def test_validator_rejects_wrong_or_nonexistent_target_column():
    mapping = run_entity_field_map()
    mapping[0] = {**mapping[0], "target_field": "not_a_column"}
    with pytest.raises(ValueError, match="target mapping mismatch|unknown target field"):
        validate_run_entity_contract(mapping)
    mapping = run_entity_field_map()
    mapping[0] = {**mapping[0], "target_field": "role"}
    with pytest.raises(ValueError, match="target mapping mismatch"):
        validate_run_entity_contract(mapping)

