"""Exact runtime declarations; schema conversion belongs to release tools."""
from contextlib import closing
from functools import lru_cache
import os
from pathlib import Path
import sqlite3

STORE_VERSION = "2026-10-30.1"
EMAIL_VERSION = 54
EMAIL_LEDGER_SQL = """CREATE TABLE email_schema_migrations (
                version integer primary key,
                applied_at text not null
            )"""


def create_current_email_migration_ledger(db):
    db.execute(EMAIL_LEDGER_SQL)



_SCHEMA_OBJECTS_CACHE: dict[tuple[str, int, int, int], tuple] = {}
_SCHEMA_OBJECTS_CACHE_LIMIT = 16


def _schema_cache_key(db):
    """Identify one on-disk schema state: file identity plus SQLite's schema cookie.

    The cookie changes on every DDL statement and on a restore into the file,
    and a replaced file has a new inode, so an equal key means an equal schema.
    In-memory and temporary databases have no file and are never cached.
    """
    main = next((row[2] for row in db.execute("pragma database_list") if row[1] == "main"), "")
    if not main:
        return None
    try:
        status = os.stat(main)
    except OSError:
        return None
    cookie = db.execute("pragma main.schema_version").fetchone()[0]
    return (main, status.st_dev, status.st_ino, cookie)


def schema_objects(db):
    key = _schema_cache_key(db)
    if key is not None and key in _SCHEMA_OBJECTS_CACHE:
        return _SCHEMA_OBJECTS_CACHE[key]
    objects = tuple(tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema "
        "where name not glob 'sqlite_*' order by type,name"))
    if key is not None:
        if len(_SCHEMA_OBJECTS_CACHE) >= _SCHEMA_OBJECTS_CACHE_LIMIT:
            _SCHEMA_OBJECTS_CACHE.pop(next(iter(_SCHEMA_OBJECTS_CACHE)))
        _SCHEMA_OBJECTS_CACHE[key] = objects
    return objects


@lru_cache(maxsize=2)
def current_objects(*, email_ledger=False):
    from app.current_database_schema import create_current_database_schema
    from app.task8_storage import register_task8_functions
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        create_current_database_schema(db)
        if email_ledger:
            create_current_email_migration_ledger(db)
        return schema_objects(db)


def schema_is_current(db, *, email_ledger=None):
    if email_ledger is None:
        email_ledger = db.execute(
            "select 1 from main.sqlite_schema where type='table' and name='email_schema_migrations'"
        ).fetchone() is not None
    return schema_objects(db) == current_objects(email_ledger=email_ledger)


def require_current_pair(path):
    """Reject mixed, partial and malformed databases before any runtime write."""
    from app.state_definitions import state_projection
    path = Path(path).resolve()
    if not path.exists():
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        objects = schema_objects(db)
        if not objects:
            return
        if not schema_is_current(db):
            raise sqlite3.IntegrityError(
                "unsupported or malformed Store source schema; explicit paired schema release required")
        marker = db.execute(f"select value from ({state_projection('service_state')}) "
                            "where key='store_schema_version'").fetchone()
        if marker != (STORE_VERSION,):
            raise sqlite3.IntegrityError("unsupported Store version; explicit paired schema release required")
        if any(item[1] == "email_schema_migrations" for item in objects):
            version = db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            if version != EMAIL_VERSION:
                raise sqlite3.IntegrityError("unsupported Email version; explicit paired schema release required")
