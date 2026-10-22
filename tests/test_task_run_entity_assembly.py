import sqlite3

import pytest

from app.task_run_entity_assembly import task_run_entity_assembly_objects
from app.task_run_entity_definitions import task_run_entity_objects
from tools.inspect_task_run_entities import inspect


def test_assembled_contract_has_unique_names_and_executes_with_native_prerequisites():
    objects = task_run_entity_assembly_objects()
    assert len(objects) == len(set(objects))
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE schedules(id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE configs(schedule_snapshot_public_id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE actions(id INTEGER PRIMARY KEY, record_type TEXT)")
    for ddl in objects.values():
        db.execute(ddl)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert "run_projections" in {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    assert "run_adopted_results" in {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}


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
