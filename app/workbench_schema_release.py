"""Paired source13 publication; inverse retains current business/index facts."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.store_schema_release import _migrate_store
from app.task_schema_release import _foreign_keys, _semantic_foreign_keys
from app.task_storage import (
    validate_reply_input_references,
    validate_meeting_run_references,
)
from app.run_storage import _schema_objects
from app.workbench_storage import (
    SOURCE_VERSION,
    WORKBENCH_VERSION,
    capture_workbench_schema,
    restore_workbench_schema,
    workbench_schema_is_current,
)

VERSION_KEY = "store_schema_version"


@dataclass
class WorkbenchSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str]
    source_schema: dict
    source_fk: tuple[tuple, ...]
    source_semantic_fk: tuple[tuple, ...]
    migrate: Callable[[Path, Path], None] = _migrate_store

    def publish(self):
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self):
        with closing(
            sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)
        ) as db:
            marker = db.execute(
                "select value from service_state where key=?", (VERSION_KEY,)
            ).fetchone()
            if marker != (WORKBENCH_VERSION,) or not workbench_schema_is_current(db):
                raise ValueError("Workbench schema readback mismatch")
            validate_reply_input_references(db)
            validate_meeting_run_references(db)
            if (
                _semantic_foreign_keys(db) != self.source_semantic_fk
                or db.execute("pragma quick_check").fetchone()[0] != "ok"
            ):
                raise ValueError("Workbench integrity/FK mismatch")

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            current = db.execute(
                "select value,updated_at from service_state where key=?", (VERSION_KEY,)
            ).fetchone()
            if current == self.original_version:
                return
            if current is None or current[0] != WORKBENCH_VERSION:
                raise ValueError("Workbench inverse source version mismatch")
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                validate_reply_input_references(db)
                validate_meeting_run_references(db)
                if _semantic_foreign_keys(db) != self.source_semantic_fk:
                    raise ValueError("Workbench inverse integrity/FK mismatch")
                restore_workbench_schema(db, self.source_schema)
                db.execute(
                    "update service_state set value=?,updated_at=? where key=?",
                    (*self.original_version, VERSION_KEY),
                )
                for table, schema in self.source_schema["tables"].items():
                    if _schema_objects(db, table) != schema["objects"]:
                        raise ValueError(
                            f"Workbench inverse declaration mismatch: {table}"
                        )
                if (
                    _foreign_keys(db) != self.source_fk
                    or db.execute("pragma quick_check").fetchone()[0] != "ok"
                ):
                    raise ValueError("Workbench inverse integrity/FK mismatch")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_workbench_schema_release(root, path, original_version):
    root, path = root.resolve(), path.resolve()
    if original_version[0] != SOURCE_VERSION:
        raise ValueError(
            "Workbench schema release needs a verified inverse for this transition"
        )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return WorkbenchSchemaRelease(
            root,
            path,
            original_version,
            capture_workbench_schema(db),
            _foreign_keys(db),
            _semantic_foreign_keys(db),
        )
