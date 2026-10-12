"""Exact 11.2 -> 11.3 paired task-schema publication and business-write inverse."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.run_storage import run_schema_is_current
from app.task_storage import (
    RETIRED_TASK_TABLES,
    REPLY_TASK_SQL,
    capture_task_schema,
    restore_task_schema,
    task_schema_is_current,
    validate_reply_input_references,
)

OLD_TASK_VERSION = "2026-10-11.2"
TASK_VERSION = "2026-10-11.3"
VERSION_KEY = "store_schema_version"



def publication_task_sources(db: sqlite3.Connection) -> tuple[str, str]:
    """Read the exact 11.2 source or 11.3 target during the deploy quiet wait."""
    version = db.execute(
        "select value from service_state where key=?", (VERSION_KEY,)
    ).fetchone()
    version = version[0] if version is not None else None
    tables = {
        row[0] for row in db.execute("select name from sqlite_master where type='table'")
    }
    old = {"reply_tasks", "reply_task_inputs", "work_summary_inputs", "task_agent_runs"}
    if version == OLD_TASK_VERSION and old <= tables and "tasks" not in tables:
        return "select * from reply_tasks", "select status from work_summary_inputs"
    if version == TASK_VERSION and "tasks" in tables and not old.intersection(tables):
        return REPLY_TASK_SQL, "select status from tasks where record_type='work_summary_inputs'"
    raise ValueError("unknown or mixed task publication source schema")


def _migrate(root, path):
    from app.store_schema_release import _migrate_store

    _migrate_store(root, path)


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


@dataclass
class TaskSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str]
    source_schema: dict
    source_fk: tuple[tuple, ...]
    migrate: Callable[[Path, Path], None] = _migrate

    def publish(self):
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self):
        with closing(
            sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)
        ) as db:
            version = db.execute(
                "select value from service_state where key=?", (VERSION_KEY,)
            ).fetchone()
            if (
                version != (TASK_VERSION,)
                or not task_schema_is_current(db)
                or not run_schema_is_current(db, include_task_runs=True)
            ):
                raise ValueError("typed task schema readback mismatch")
            validate_reply_input_references(db)
            if (
                _foreign_keys(db) != self.source_fk
                or db.execute("pragma quick_check").fetchone()[0] != "ok"
            ):
                raise ValueError("typed task schema integrity/FK mismatch")

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            current = db.execute(
                "select value,updated_at from service_state where key=?", (VERSION_KEY,)
            ).fetchone()
            if current == self.original_version:
                return
            if current is None or current[0] != TASK_VERSION:
                raise ValueError("task inverse source version mismatch")
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                restore_task_schema(db, self.source_schema)
                db.execute(
                    "update service_state set value=?,updated_at=? where key=?",
                    (*self.original_version, VERSION_KEY),
                )
                for table, schema in self.source_schema["tables"].items():
                    actual = [
                        dict(zip(("type", "name", "sql"), row, strict=True))
                        for row in db.execute(
                            "select type,name,sql from sqlite_master where tbl_name=? and sql is not null order by type,name",
                            (table,),
                        )
                    ]
                    if (
                        actual != schema["objects"]
                        or [
                            row[1]
                            for row in db.execute(f'pragma table_info("{table}")')
                        ]
                        != schema["columns"]
                    ):
                        raise ValueError(f"task inverse declaration mismatch: {table}")
                if (
                    _foreign_keys(db) != self.source_fk
                    or db.execute("pragma quick_check").fetchone()[0] != "ok"
                ):
                    raise ValueError("task inverse integrity/FK mismatch")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_task_schema_release(root, path, original_version):
    root, path = root.resolve(), path.resolve()
    if original_version[0] != OLD_TASK_VERSION:
        raise ValueError(
            "task schema release needs a verified inverse for this transition"
        )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        source = capture_task_schema(db)
        if not {*RETIRED_TASK_TABLES, "runs"} <= source[
            "tables"
        ].keys() or not run_schema_is_current(db):
            raise ValueError("original task source schema is incomplete")
        return TaskSchemaRelease(
            root, path, original_version, source, _foreign_keys(db)
        )
