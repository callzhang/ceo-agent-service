"""Paired actions publication; inverse reconstructs current business facts."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.action_storage import (
    REVIEW_CANDIDATE_SQL,
    action_schema_is_current,
    capture_action_schema,
    restore_action_schema,
)
from app.run_storage import _schema_objects
from app.store_schema_release import _migrate_store
from app.task_schema_release import _foreign_keys

SOURCE_ACTION_VERSION = "2026-10-12.1"
ACTION_VERSION = "2026-10-13.1"
VERSION_KEY = "store_schema_version"


def publication_candidate_source(db: sqlite3.Connection) -> str:
    """Read the declared previous or target layout during the deploy window."""
    marker = db.execute("select value from service_state where key=?", (VERSION_KEY,)).fetchone()
    version = marker[0] if marker else None
    if version == SOURCE_ACTION_VERSION:
        capture_action_schema(db)
        return "select * from review_candidates"
    if version == ACTION_VERSION and action_schema_is_current(db):
        return REVIEW_CANDIDATE_SQL
    raise ValueError("unknown or mixed action publication source schema")


@dataclass
class ActionSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str]
    source_schema: dict
    source_fk: tuple[tuple, ...]
    migrate: Callable[[Path, Path], None] = _migrate_store

    def publish(self):
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self):
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            marker = db.execute("select value from service_state where key=?", (VERSION_KEY,)).fetchone()
            if marker != (ACTION_VERSION,) or not action_schema_is_current(db):
                raise ValueError("Actions schema readback mismatch")
            if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise ValueError("Actions integrity/FK mismatch")

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
            if current == self.original_version:
                if capture_action_schema(db) != self.source_schema:
                    raise ValueError("Actions inverse source declaration mismatch")
                return
            if current is None or current[0] != ACTION_VERSION:
                raise ValueError("Actions inverse source version mismatch")
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("Actions inverse integrity/FK mismatch")
                restore_action_schema(db, self.source_schema)
                db.execute("update service_state set value=?,updated_at=? where key=?",
                           (*self.original_version, VERSION_KEY))
                for table, schema in self.source_schema["tables"].items():
                    if _schema_objects(db, table) != schema["objects"]:
                        raise ValueError(f"Actions inverse declaration mismatch: {table}")
                if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("Actions inverse integrity/FK mismatch")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_action_schema_release(root, path, original_version):
    root, path = root.resolve(), path.resolve()
    if original_version[0] != SOURCE_ACTION_VERSION:
        raise ValueError("Actions schema release needs a verified inverse for this transition")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
        if current != original_version:
            raise ValueError("Actions publication source marker changed")
        return ActionSchemaRelease(root, path, original_version,
                                   capture_action_schema(db), _foreign_keys(db))
