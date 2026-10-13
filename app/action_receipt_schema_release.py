"""Paired Store15 -> Store16 publication of independent action receipts."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.action_receipt_storage import (
    capture_action_receipt_schema,
    restore_action_receipt_schema,
)
from app.action_storage import (
    ACTION_PROJECTIONS,
    RECEIPT_ACTION_TYPES,
    action_schema_is_current,
)
from app.store_schema_release import _migrate_store
from app.task_schema_release import _foreign_keys


SOURCE_ACTION_RECEIPT_VERSION = "2026-10-13.1"
ACTION_RECEIPT_VERSION = "2026-10-13.2"
VERSION_KEY = "store_schema_version"


def publication_receipt_sources(db):
    """The exact previous/target declarations used only in the deploy window."""
    marker = db.execute("select value from service_state where key=?", (VERSION_KEY,)).fetchone()
    version = marker[0] if marker else None
    if version == SOURCE_ACTION_RECEIPT_VERSION:
        capture_action_receipt_schema(db)
        return {kind: f"select * from {kind}" for kind in RECEIPT_ACTION_TYPES}
    if version == ACTION_RECEIPT_VERSION and action_schema_is_current(db, include_receipts=True):
        return {kind: ACTION_PROJECTIONS[kind] for kind in RECEIPT_ACTION_TYPES}
    if version == "2026-10-13.3" and action_schema_is_current(db, include_postfix=True):
        return {kind: ACTION_PROJECTIONS[kind] for kind in RECEIPT_ACTION_TYPES}
    raise ValueError("unknown or mixed receipt publication source schema")


@dataclass
class ActionReceiptSchemaRelease:
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
            if marker != (ACTION_RECEIPT_VERSION,) or not action_schema_is_current(db, include_receipts=True):
                raise ValueError("Receipt action schema readback mismatch")
            if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise ValueError("Receipt action integrity/FK mismatch")

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
            if current == self.original_version:
                restored = capture_action_receipt_schema(db)
                if restored['actions'] != self.source_schema['actions'] or restored['tables'] != self.source_schema['tables']:
                    raise ValueError("Receipt inverse source declaration mismatch")
                return
            if current is None or current[0] != ACTION_RECEIPT_VERSION:
                raise ValueError("Receipt inverse source version mismatch")
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("Receipt inverse integrity/FK mismatch")
                restore_action_receipt_schema(db, self.source_schema)
                db.execute("update service_state set value=?,updated_at=? where key=?",
                           (*self.original_version, VERSION_KEY))
                restored = capture_action_receipt_schema(db)
                if restored['actions'] != self.source_schema['actions'] or restored['tables'] != self.source_schema['tables']:
                    raise ValueError("Receipt inverse declaration mismatch")
                if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("Receipt inverse integrity/FK mismatch")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_action_receipt_schema_release(root, path, original_version):
    root, path = root.resolve(), path.resolve()
    if original_version[0] != SOURCE_ACTION_RECEIPT_VERSION:
        raise ValueError("Receipt schema release needs the exact previous source")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
        if current != original_version:
            raise ValueError("Receipt publication source marker changed")
        return ActionReceiptSchemaRelease(root, path, original_version,
                                         capture_action_receipt_schema(db), _foreign_keys(db))
