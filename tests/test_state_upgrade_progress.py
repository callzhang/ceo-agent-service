"""Explicit-schema upgrade progress without application constructors."""
from contextlib import closing
import sqlite3

import pytest

from app import repository_updater as updater
from app.state_definitions import state_projection
from app.state_schema_release import (
    SOURCE_TABLES, capture_state_source, migrate_state_schema, restore_state_schema,
)
from tests.configs_source_fixtures import capture


@pytest.fixture(params=("actual", "fresh"))
def source(tmp_path, request):
    path = tmp_path / "progress.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        for name in SOURCE_TABLES:
            spec = capture()[request.param]["tables"][name]
            db.execute(spec["sql"])
            for index in reversed(spec["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        db.commit()
    return path


def test_progress_explicit_rebind_and_current_fact_inverse(source):
    store = updater.ExistingSchemaUpgradeStateStore(source)
    assert store.get_service_state("missing") is None
    store.set_service_state("upgrade", "before")
    with closing(sqlite3.connect(source)) as db:
        original_identity = db.execute(
            "select rowid from service_state where key='upgrade'"
        ).fetchone()[0]
        db.execute("begin")
        metadata = capture_state_source(db)
        migrate_state_schema(db, metadata)
        db.commit()
    # An explicit binding error must remain visible; there is no table fallback.
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.get_service_state("upgrade")
    store.bind_schema_current()
    assert store.get_service_state("upgrade") == "before"
    store.set_service_state("upgrade", "after-commit")
    store.set_service_state("new", "created-current")
    assert store.get_service_state("upgrade") == "after-commit"
    with closing(sqlite3.connect(source)) as db:
        projection = "(" + state_projection("service_state", include_rowid=True) + ")"
        assert db.execute(
            "select rowid from " + projection + " where key='upgrade'"
        ).fetchone()[0] == original_identity
        db.execute("begin")
        restore_state_schema(db, metadata)
        db.commit()
    store.bind_schema_source()
    assert store.get_service_state("upgrade") == "after-commit"
    assert store.get_service_state("new") == "created-current"
    store.set_service_state("upgrade", "rolled_back")
    assert store.get_service_state("upgrade") == "rolled_back"
    with closing(sqlite3.connect(source)) as db:
        assert not db.execute(
            "select 1 from sqlite_schema where name='state'"
        ).fetchone()
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("current", (False, True))
@pytest.mark.parametrize("problem", ("database is locked", "database is busy", "other failure"))
def test_progress_preserves_bounded_retry(source, current, problem, monkeypatch):
    store = updater.ExistingSchemaUpgradeStateStore(source)
    if current:
        with closing(sqlite3.connect(source)) as db:
            db.execute("begin")
            metadata = capture_state_source(db)
            migrate_state_schema(db, metadata)
            db.commit()
        store.bind_schema_current()
    connect = updater.sqlite3.connect
    calls = []
    delays = []

    def injected_connect(*args, **kwargs):
        calls.append((args, kwargs))
        raise sqlite3.OperationalError(problem)

    monkeypatch.setattr(updater.sqlite3, "connect", injected_connect)
    monkeypatch.setattr(updater.time, "sleep", delays.append)
    with pytest.raises(sqlite3.OperationalError, match=problem):
        store.set_service_state("upgrade", "unwritten")
    retry = problem != "other failure"
    assert len(calls) == (3 if retry else 1)
    assert delays == ([0.25, 0.5] if retry else [])
    assert all(item[1] == {"uri": True, "timeout": 30} for item in calls)
    assert all(item[0][0].endswith("?mode=rw") for item in calls)
    monkeypatch.setattr(updater.sqlite3, "connect", connect)
    assert store.get_service_state("upgrade") is None


@pytest.mark.parametrize("current", (False, True))
def test_progress_getter_preserves_source_conversion_and_no_initializer(source, current):
    store = updater.ExistingSchemaUpgradeStateStore(source)
    with closing(sqlite3.connect(source)) as db:
        db.execute(
            "insert into service_state(key,value,updated_at) values(?,?,?)",
            ("blob", sqlite3.Binary(b"native"), "before"),
        )
        db.commit()
        if current:
            db.execute("begin")
            metadata = capture_state_source(db)
            migrate_state_schema(db, metadata)
            db.commit()
    if current:
        store.bind_schema_current()
    with closing(sqlite3.connect(source)) as db:
        before = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
    assert store.get_service_state("blob") == str(b"native")
    assert store.get_service_state("absent") is None
    store.set_service_state("write", "no-constructor")
    with closing(sqlite3.connect(source)) as db:
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before


def test_progress_open_missing_database_does_not_create(tmp_path):
    path = tmp_path / "does-not-exist.sqlite3"
    store = updater.ExistingSchemaUpgradeStateStore(path)
    for current in (False, True):
        if current:
            store.bind_schema_current()
        with pytest.raises(sqlite3.OperationalError):
            store.set_service_state("upgrade", "missing")
        assert not path.exists()

