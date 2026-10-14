"""Paired Store18 -> Store19 publication of Attempt and Memory actions."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.action_reply_storage import (
    PUBLISHED_EMAIL_VERSION,
    capture_action_reply_schema,
    restore_action_reply_schema,
)
from app.action_storage import action_schema_is_current
from app.store_schema_release import _migrate_store
from app.task_schema_release import _foreign_keys


SOURCE_ACTION_REPLY_VERSION = "2026-10-13.4"
ACTION_REPLY_VERSION = "2026-10-13.5"
VERSION_KEY = "store_schema_version"


def _email_version(db):
    return db.execute("select max(version) from email_schema_migrations").fetchone()[0]


@dataclass
class ActionReplySchemaRelease:
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
            db.execute("begin")
            marker = db.execute("select value from service_state where key=?", (VERSION_KEY,)).fetchone()
            if marker != (ACTION_REPLY_VERSION,) or _email_version(db) != PUBLISHED_EMAIL_VERSION or \
                    not action_schema_is_current(
                    db, include_reply=True, reply_variant=self.source_schema['target_variant']):
                raise ValueError("Attempt/Memory action schema readback mismatch")
            if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise ValueError("Attempt/Memory action integrity/FK mismatch")

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
                if current == self.original_version:
                    if capture_action_reply_schema(db) != self.source_schema or \
                            _foreign_keys(db) != self.source_fk or \
                            db.execute("pragma quick_check").fetchone()[0] != "ok":
                        raise ValueError("Attempt/Memory inverse source declaration mismatch")
                    db.commit()
                    return
                if current is None or current[0] != ACTION_REPLY_VERSION:
                    raise ValueError("Attempt/Memory inverse source version mismatch")
                if _email_version(db) != PUBLISHED_EMAIL_VERSION or _foreign_keys(db) != self.source_fk:
                    raise ValueError("Attempt/Memory inverse integrity/FK mismatch")
                restore_action_reply_schema(db, self.source_schema)
                db.execute("update service_state set value=?,updated_at=? where key=?",
                           (*self.original_version, VERSION_KEY))
                if capture_action_reply_schema(db) != self.source_schema or \
                        _foreign_keys(db) != self.source_fk or \
                        db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("Attempt/Memory inverse declaration/integrity mismatch")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_action_reply_schema_release(root, path, original_version):
    root, path = root.resolve(), path.resolve()
    if original_version[0] != SOURCE_ACTION_REPLY_VERSION:
        raise ValueError("Attempt/Memory schema release needs the exact previous source")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
        if current != original_version:
            raise ValueError("Attempt/Memory publication source marker changed")
        return ActionReplySchemaRelease(root, path, original_version,
                                        capture_action_reply_schema(db), _foreign_keys(db))
