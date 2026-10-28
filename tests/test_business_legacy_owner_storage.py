"""Current WorkTodo import-owner behavior."""

import sqlite3

import pytest

from app import store as store_module
from app.business_legacy_owner_storage import assign_import_task, read_import_task
from app.business_storage import insert_business
from app.store import AutoReplyStore


def current_db(tmp_path):
    path = tmp_path / "owner.sqlite"
    AutoReplyStore(path)
    store_module._close_idle_connections()
    db = sqlite3.connect(path)
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    insert_business(db, "task", {"id": 31, "title": "task", "stage": "candidate"})
    insert_business(db, "task", {"id": 32, "title": "task", "stage": "candidate"})
    insert_business(db, "work_project", {"id": 7, "title": "project"})
    insert_business(db, "work_todo", {"id": 11, "project_id": 7, "title": "todo"})
    insert_business(db, "work_todo", {"id": 12, "project_id": 7, "title": "todo"})
    return db


def test_current_assign_rejects_none_float_and_bool_even_when_equal_to_current(tmp_path):
    db = current_db(tmp_path)
    with pytest.raises(ValueError, match="missing formal Task"):
        assign_import_task(db, 11, None)
    assert read_import_task(db, 11) is None
    assert assign_import_task(db, 11, 31) == "created"
    with pytest.raises(ValueError, match="missing formal Task"):
        assign_import_task(db, 11, 31.0)
    assert read_import_task(db, 11) == 31
    insert_business(db, "task", {"id": 1, "title": "boolean control", "stage": "candidate"})
    assert assign_import_task(db, 12, 1) == "created"
    with pytest.raises(ValueError, match="missing formal Task"):
        assign_import_task(db, 12, True)
    assert assign_import_task(db, 11, 31) == "skipped"
    with pytest.raises(ValueError, match="conflicting"):
        assign_import_task(db, 11, 32)
    db.close()


def test_assignment_requires_transaction_and_existing_owners(tmp_path):
    db = current_db(tmp_path)
    assert read_import_task(db, 999) is None
    with pytest.raises(ValueError, match='missing WorkTodo'):
        assign_import_task(db, 999, 31)
    with pytest.raises(ValueError, match='missing formal Task'):
        assign_import_task(db, 11, 999)
    with pytest.raises(ValueError, match='missing formal Task'):
        assign_import_task(db, 11, 7)
    db.commit()
    with pytest.raises(sqlite3.ProgrammingError, match='caller transaction'):
        assign_import_task(db, 11, 31)
    db.close()


def test_current_import_owner_enforces_target_and_allows_owner_deletion(tmp_path):
    db = current_db(tmp_path)
    db.commit()
    db.execute("pragma foreign_keys=on")
    db.execute("begin")
    assert assign_import_task(db, 11, 31) == "created"
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update business set import_task_id=999 where record_type='work_todo' and public_id=11")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update business set import_task_id=31 where record_type='task' and public_id=32")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("delete from business where record_type='task' and public_id=31")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update business set legacy_key='41' where record_type='task' and public_id=31")
    assert db.execute("delete from business where record_type='work_todo' and public_id=11").rowcount == 1
    assert db.execute("select public_id from business where record_type='task' and public_id=31").fetchone() == (31,)
    db.close()
