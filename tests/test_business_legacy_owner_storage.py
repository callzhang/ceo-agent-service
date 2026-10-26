"""The WorkTodo import owner on the current Business table."""

import sqlite3

import pytest

from app.business_legacy_owner_storage import assign_import_task, read_import_task
from app.business_storage import insert_business
from app.store import AutoReplyStore, _close_idle_connections


def current_db(tmp_path):
    AutoReplyStore(tmp_path / "owner.sqlite")
    _close_idle_connections()
    db = sqlite3.connect(tmp_path / "owner.sqlite")
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    insert_business(db, "task", {"id": 31, "title": "task", "stage": "candidate"})
    insert_business(db, "task", {"id": 32, "title": "task", "stage": "candidate"})
    insert_business(db, "work_project", {"id": 7, "title": "project"})
    insert_business(db, "work_todo", {"id": 11, "project_id": 7, "title": "todo"})
    insert_business(db, "work_todo", {"id": 12, "project_id": 7, "title": "todo"})
    return db


def test_assign_rejects_none_float_and_bool_even_when_equal_to_current(tmp_path):
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
