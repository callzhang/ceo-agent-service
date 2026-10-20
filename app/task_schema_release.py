"""Exact 11.2 -> 11.3 paired task-schema publication and business-write inverse."""
from app.state_publication_sources import publication_store_version
from app.shared_action_schema_release import (
    TARGET_STORE_VERSION, PREVIOUS_STORE_VERSIONS, publication_action_schema_is_current,
)

from app.action_storage import action_schema_is_current, reply_schema_is_current
from app.task20_definitions import task20_projection
from app.task20_current_schema import task20_schema_is_current

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.run_storage import MEETING_RUN_SQL, run_schema_is_current
from app.task_storage import (
    RETIRED_TASK_TABLES,
    REPLY_TASK_SQL,
    MEETING_JOB_SQL,
    _validate_source_declarations,
    capture_task_schema,
    restore_task_schema,
    task_schema_is_current,
    validate_reply_input_references,
    validate_meeting_run_references,
)

OLD_TASK_VERSION = "2026-10-11.2"
TASK_VERSION = "2026-10-11.3"
VERSION_KEY = "store_schema_version"



def publication_task_sources(db: sqlite3.Connection) -> tuple[str, str]:
    """Read the exact 11.2 source or 11.3 target during the deploy quiet wait."""
    version = publication_store_version(db, current_version=TARGET_STORE_VERSION,
                                        previous_versions=PREVIOUS_STORE_VERSIONS)
    tables = {
        row[0] for row in db.execute("select name from sqlite_master where type='table'")
    }
    old = set(RETIRED_TASK_TABLES)
    if version == OLD_TASK_VERSION and old <= tables and "tasks" not in tables:
        return "select * from reply_tasks", "select status from work_summary_inputs"
    if version == TASK_VERSION and "tasks" in tables and not old.intersection(tables):
        return REPLY_TASK_SQL, "select status from tasks where record_type='work_summary_inputs'"
    if (
        version in ("2026-10-12.1", "2026-10-13.1", "2026-10-13.2", "2026-10-13.3", "2026-10-13.4", "2026-10-13.5")
        and (reply_schema_is_current(db) if version == "2026-10-13.5" else version == "2026-10-12.1" or action_schema_is_current(db, include_receipts=version in ("2026-10-13.2", "2026-10-13.3", "2026-10-13.4"), include_postfix=version in ("2026-10-13.3", "2026-10-13.4"), include_delivery=version == "2026-10-13.4"))
        and {"tasks", "runs"} <= tables
        and not (
            old
            | {
                "scheduled_task_runs",
                "workbench_tasks",
                "workbench_turns",
                "workbench_events",
            }
        ).intersection(tables)
    ):
        return (
            REPLY_TASK_SQL,
            "select status from tasks where record_type='work_summary_inputs'",
        )
    if (publication_action_schema_is_current(db, version)
            and task20_schema_is_current(db)
            and {"tasks", "runs"} <= tables
            and not (old | {"scheduled_task_runs", "workbench_tasks", "workbench_turns", "workbench_events"}).intersection(tables)):
        return task20_projection("reply_tasks"), "select status from (" + task20_projection("work_summary_inputs") + ")"
    raise ValueError("unknown or mixed task publication source schema")


def publication_meeting_sources(db: sqlite3.Connection) -> tuple[str, str]:
    """Read Meeting from the exact old source or combined current layout."""
    version = publication_store_version(db, current_version=TARGET_STORE_VERSION,
                                        previous_versions=PREVIOUS_STORE_VERSIONS)
    tables = {
        row[0] for row in db.execute("select name from sqlite_master where type='table'")
    }
    if version == OLD_TASK_VERSION and set(RETIRED_TASK_TABLES) <= tables and "runs" in tables and "tasks" not in tables:
        return "select * from meeting_alignment_jobs", "select * from meeting_alignment_runs"
    if version == TASK_VERSION and {"tasks", "runs"} <= tables and not set(RETIRED_TASK_TABLES).intersection(tables):
        return MEETING_JOB_SQL, MEETING_RUN_SQL
    if (
        version in ("2026-10-12.1", "2026-10-13.1", "2026-10-13.2", "2026-10-13.3", "2026-10-13.4", "2026-10-13.5")
        and (reply_schema_is_current(db) if version == "2026-10-13.5" else version == "2026-10-12.1" or action_schema_is_current(db, include_receipts=version in ("2026-10-13.2", "2026-10-13.3", "2026-10-13.4"), include_postfix=version in ("2026-10-13.3", "2026-10-13.4"), include_delivery=version == "2026-10-13.4"))
        and {"tasks", "runs"} <= tables
        and not (
            set(RETIRED_TASK_TABLES)
            | {
                "scheduled_task_runs",
                "workbench_tasks",
                "workbench_turns",
                "workbench_events",
            }
        ).intersection(tables)
    ):
        return MEETING_JOB_SQL, MEETING_RUN_SQL
    if (publication_action_schema_is_current(db, version)
            and task20_schema_is_current(db)
            and {"tasks", "runs"} <= tables
            and not (set(RETIRED_TASK_TABLES) | {"scheduled_task_runs", "workbench_tasks", "workbench_turns", "workbench_events"}).intersection(tables)):
        return task20_projection("meeting_alignment_jobs"), MEETING_RUN_SQL
    raise ValueError("unknown or mixed meeting publication source schema")


def _migrate(root, path):
    from app.store_schema_release import _migrate_store

    _migrate_store(root, path)


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _semantic_foreign_keys(db):
    """Compare the same actual missing Meeting parent across physical layouts."""
    findings = []
    for table, row_id, parent, fk_id in _foreign_keys(db):
        if table == "meeting_alignment_runs" and parent == "meeting_alignment_jobs":
            raw = db.execute("select job_id from meeting_alignment_runs where id=?", (row_id,)).fetchone()[0]
            findings.append((table, row_id, parent, fk_id, raw))
        elif table == "runs" and parent == "tasks":
            run = db.execute("select record_type,legacy_key,job_id from runs where id=?", (row_id,)).fetchone()
            if run[0] == "meeting_alignment_runs":
                findings.append(("meeting_alignment_runs", int(run[1]), "meeting_alignment_jobs", 0, run[2]))
            else:
                findings.append((table, row_id, parent, fk_id))
        else:
            findings.append((table, row_id, parent, fk_id))
    return tuple(sorted(findings))


@dataclass
class TaskSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str]
    source_schema: dict
    source_fk: tuple[tuple, ...]
    source_semantic_fk: tuple[tuple, ...]
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
            validate_meeting_run_references(db)
            if (
                _semantic_foreign_keys(db) != self.source_semantic_fk
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
                validate_meeting_run_references(db)
                if _semantic_foreign_keys(db) != self.source_semantic_fk:
                    raise ValueError("task inverse integrity/FK mismatch")
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
        _validate_source_declarations(db)
        return TaskSchemaRelease(
            root, path, original_version, source, _foreign_keys(db),
            _semantic_foreign_keys(db),
        )
