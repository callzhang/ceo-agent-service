"""Paired Store17 -> Store18 publication of sent delivery actions."""
from app.state_publication_sources import publication_store_version

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.action_delivery_storage import (
    capture_action_delivery_schema,
    restore_action_delivery_schema,
)
from app.selection_schema_release import (
    SOURCE_STORE_VERSION, TARGET_STORE_VERSION, publication_action_schema_is_current,
)
from app.action_storage import (
    ACTION_PROJECTIONS,
    DELIVERY_ACTION_TYPES,
    SOURCE_COLUMNS,
    action_schema_is_current,
    reply_schema_is_current,
)
from app.store_schema_release import _migrate_store
from app.task_schema_release import _foreign_keys


SOURCE_ACTION_DELIVERY_VERSION = "2026-10-13.3"
ACTION_DELIVERY_VERSION = "2026-10-13.4"
VERSION_KEY = "store_schema_version"


def publication_delivery_sources(db):
    """Exact source/target delivery reads during the paired deploy window."""
    version = publication_store_version(db, current_version=TARGET_STORE_VERSION,
                                        previous_versions=(SOURCE_STORE_VERSION,))
    if version == SOURCE_ACTION_DELIVERY_VERSION:
        capture_action_delivery_schema(db)
        return {
            kind: f"select {','.join(SOURCE_COLUMNS[kind])} from {kind}"
            for kind in DELIVERY_ACTION_TYPES
        }
    if version == ACTION_DELIVERY_VERSION and action_schema_is_current(db, include_delivery=True):
        return {kind: ACTION_PROJECTIONS[kind] for kind in DELIVERY_ACTION_TYPES}
    if version == "2026-10-13.5" and reply_schema_is_current(db):
        return {kind: ACTION_PROJECTIONS[kind] for kind in DELIVERY_ACTION_TYPES}
    if publication_action_schema_is_current(db, version):
        return {kind: ACTION_PROJECTIONS[kind] for kind in DELIVERY_ACTION_TYPES}
    raise ValueError("unknown or mixed sent delivery publication source schema")


@dataclass
class ActionDeliverySchemaRelease:
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
            if marker != (ACTION_DELIVERY_VERSION,) or not action_schema_is_current(db, include_delivery=True):
                raise ValueError("Sent delivery action schema readback mismatch")
            if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise ValueError("Sent delivery action integrity/FK mismatch")

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
            if current == self.original_version:
                restored = capture_action_delivery_schema(db)
                if (restored["actions"] != self.source_schema["actions"]
                        or restored["tables"] != self.source_schema["tables"]):
                    raise ValueError("Sent delivery inverse source declaration mismatch")
                return
            if current is None or current[0] != ACTION_DELIVERY_VERSION:
                raise ValueError("Sent delivery inverse source version mismatch")
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("Sent delivery inverse integrity/FK mismatch")
                restore_action_delivery_schema(db, self.source_schema)
                db.execute("update service_state set value=?,updated_at=? where key=?",
                           (*self.original_version, VERSION_KEY))
                restored = capture_action_delivery_schema(db)
                if (restored["actions"] != self.source_schema["actions"]
                        or restored["tables"] != self.source_schema["tables"]):
                    raise ValueError("Sent delivery inverse declaration mismatch")
                if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("Sent delivery inverse integrity/FK mismatch")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_action_delivery_schema_release(root, path, original_version):
    root, path = root.resolve(), path.resolve()
    if original_version[0] != SOURCE_ACTION_DELIVERY_VERSION:
        raise ValueError("Sent delivery schema release needs the exact previous source")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        current = db.execute("select value,updated_at from service_state where key=?", (VERSION_KEY,)).fetchone()
        if current != original_version:
            raise ValueError("Sent delivery publication source marker changed")
        return ActionDeliverySchemaRelease(root, path, original_version,
                                           capture_action_delivery_schema(db), _foreign_keys(db))
