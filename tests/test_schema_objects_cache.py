"""`schema_objects` may reuse a result only while the database file's schema is unchanged."""

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3

from app import current_schema_manifest as manifest


def _database(path: Path, *statements: str) -> Path:
    with closing(sqlite3.connect(path)) as db:
        for statement in statements:
            db.execute(statement)
        db.commit()
    return path


def _names(db) -> set[str]:
    return {item[1] for item in manifest.schema_objects(db)}


def test_unchanged_schema_is_read_once(tmp_path: Path):
    path = _database(tmp_path / "a.sqlite3", "create table first(x)")
    with closing(sqlite3.connect(path)) as db:
        assert manifest.schema_objects(db) is manifest.schema_objects(db)


def test_ddl_from_another_connection_is_seen_by_the_next_call(tmp_path: Path):
    path = _database(tmp_path / "a.sqlite3", "create table first(x)")
    with closing(sqlite3.connect(path)) as reader, closing(sqlite3.connect(path)) as writer:
        assert _names(reader) == {"first"}
        writer.execute("create table second(x)")
        writer.commit()
        assert _names(reader) == {"first", "second"}


def test_a_changed_definition_with_the_same_names_is_seen(tmp_path: Path):
    path = _database(tmp_path / "a.sqlite3", "create table t(x integer check(x > 0))")
    with closing(sqlite3.connect(path)) as db:
        before = manifest.schema_objects(db)
        db.execute("drop table t")
        db.execute("create table t(x integer check(x > -1))")
        db.commit()
        assert manifest.schema_objects(db) != before


def test_a_replaced_file_is_seen(tmp_path: Path):
    path = _database(tmp_path / "a.sqlite3", "create table first(x)")
    other = _database(tmp_path / "other.sqlite3", "create table replacement(x)")
    with closing(sqlite3.connect(path)) as db:
        assert _names(db) == {"first"}
    shutil.copyfile(other, tmp_path / "staged.sqlite3")
    (tmp_path / "staged.sqlite3").replace(path)
    with closing(sqlite3.connect(path)) as db:
        assert _names(db) == {"replacement"}


def test_a_restore_into_the_same_file_is_seen(tmp_path: Path):
    path = _database(tmp_path / "a.sqlite3", "create table first(x)")
    source = _database(tmp_path / "source.sqlite3", "create table restored(x)")
    with closing(sqlite3.connect(path)) as db:
        assert _names(db) == {"first"}
    with closing(sqlite3.connect(source)) as backup_source, closing(sqlite3.connect(path)) as target:
        backup_source.backup(target)
    with closing(sqlite3.connect(path)) as db:
        assert _names(db) == {"restored"}


def test_in_memory_databases_are_never_cached():
    manifest._SCHEMA_OBJECTS_CACHE.clear()
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table first(x)")
        assert _names(db) == {"first"}
        db.execute("create table second(x)")
        assert _names(db) == {"first", "second"}
    assert not manifest._SCHEMA_OBJECTS_CACHE


def test_the_cache_stays_bounded(tmp_path: Path):
    manifest._SCHEMA_OBJECTS_CACHE.clear()
    for index in range(manifest._SCHEMA_OBJECTS_CACHE_LIMIT + 5):
        path = _database(tmp_path / f"{index}.sqlite3", "create table t(x)")
        with closing(sqlite3.connect(path)) as db:
            manifest.schema_objects(db)
    assert len(manifest._SCHEMA_OBJECTS_CACHE) == manifest._SCHEMA_OBJECTS_CACHE_LIMIT
