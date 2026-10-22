import json
import sqlite3
from pathlib import Path

import pytest

from app.run_entity_definitions import (
    run_entity_field_map, run_entity_objects,
    validate_run_entity_contract,
)
from app.task_run_entity_definitions import task_run_entity_objects
from app.logical_run_definitions import LOGICAL_RUN_TABLE_DDL
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


def test_contract_is_valid_and_artifacts_are_exact():
    validate_run_entity_contract()
    source = sqlite3.connect(":memory:")
    source.execute(LOGICAL_RUN_TABLE_DDL)
    source_columns = {row[1]: row for row in source.execute("PRAGMA table_info(runs)")}
    assert tuple(source_columns) == SOURCE_DECLARED_FIELDS
    expected = run_entity_field_map()
    for row in expected:
        _, name, sql_type, not_null, default, primary_key = source_columns[row["source_field"]]
        null_contract = "PRIMARY KEY" if primary_key else "NOT NULL" if not_null else "NULL allowed"
        assert (row["sql_type"], row["null_contract"], row["source_default"]) == (sql_type.upper(), null_contract, default)
    artifact = json.loads((ROOT / "docs/run-entity-field-map.json").read_text())
    assert artifact == expected
    target = "\n".join(run_entity_objects().values()) + "\n"
    assert (ROOT / "docs/run-entity-target.sql").read_text() == target
    assert """-- Physical IDs and source public keys are migration metadata only.""" in (ROOT / "docs/run-entity-retirement.sql").read_text()
    assert len(expected) == 8 * len(SOURCE_DECLARED_FIELDS)
    assert all(row["identity_order_semantics"] for row in expected if row["source_field"] == "legacy_key")


def test_runs_keep_natural_absence_and_separate_provenance_and_adopted_result():
    db = database()
    db.execute("INSERT INTO runs(id,task_id,parent_id,created_at) VALUES (1,1,NULL,'now')")
    db.execute("INSERT INTO tasks(id,kind,status,parameters_json,created_at,updated_at) VALUES (2,'meeting_alignment','queued',jsonb('{\"meeting_id\":\"m\"}'),'now','now')")
    db.execute("INSERT INTO runs(id,task_id,parent_id,created_at) VALUES (2,2,1,'now')")
    db.execute("INSERT INTO run_provenance VALUES (2,'meeting_alignment_runs',4908)")
    db.execute("INSERT INTO run_adopted_results VALUES (2,'{}')")
    assert db.execute("SELECT status,error,updated_at FROM runs WHERE id=1").fetchone() == (None, None, None)
    assert db.execute("SELECT source_type,job_id FROM run_provenance WHERE run_id=2").fetchone() == ('meeting_alignment_runs', 4908)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO run_provenance VALUES (1,'meeting_alignment_runs',NULL)")


def test_one_run_has_many_attempts_and_standalone_invocation_is_valid():
    db = database()
    db.execute("INSERT INTO runs(id,created_at) VALUES (1,'now')")
    insert_attempt(db, (1, 'task', 'key', 1, 'route-a', 'codex', 'oauth', 'model', 'fresh', '', 'execute', 'retry', 'schema', 'session-a', 'ref', 0, 1, 'running', '', '', 0, 'owner', 'later', '', 'schema', 'now', '', 'now', 'now'))
    insert_attempt(db, (1,'task','key',2,'route-b','codex','oauth','model','resume','session-a','execute','retry','schema','session-b','ref',2,3,'completed','','',0,'','','','schema','now','now','now','now'))
    insert_attempt(db, (None,'standalone','x',1,'route-c','codex','oauth','model','fresh','','execute','retry','schema','session-c','ref',0,1,'running','','',0,'lease','expires','','schema','now','','now','now'))
    assert db.execute("SELECT count(*) FROM runtime_attempts WHERE parent_id=1").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        insert_attempt(db, (1,'task','key',1,'route-z','codex','oauth','model','fresh','','execute','retry','schema','session-z','ref',0,1,'running','','',0,'owner','later','','schema','now','','now','now'))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE runtime_attempts SET source_session_id='rewritten' WHERE attempt_number=2")
    with pytest.raises(sqlite3.IntegrityError):
        insert_attempt(db, (None,'standalone','y',1,'route-d','codex','oauth','model','fresh','','execute','retry','schema','session-d','ref',0,1,'running','','',0,'','','','schema','now','','now','now'))


def test_schedule_dispatch_state_and_domain_uniqueness_are_preserved():
    db = database()
    row = (1, 'scheduled', '2026-01-01', 'pending', None, 'event', None, None, None, '2026-01-01', 1, None, 8, None, None, 'now')
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

