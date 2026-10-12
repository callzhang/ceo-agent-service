"""Matched Store input-schema publication without reverting business writes."""

from __future__ import annotations

import ast
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.run_schema_release import RunSchemaRelease

OLD_INPUT_VERSION = "2026-10-10.2"
INPUT_VERSION = "2026-10-11.1"
VERSION_KEY = "store_schema_version"
PAYLOAD_COLUMNS = frozenset(
    {"trigger_text", "trigger_message_json", "input_provenance_json", "input_compacted"}
)
REFERENCE_COLUMNS = frozenset({"current_input_id", "claimed_input_id"})


def _target_store_version(root: Path) -> str | None:
    path = root / "app" / "store.py"
    if not path.exists():
        return None
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "STORE_SCHEMA_VERSION"
            for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if type(version) is str:
                return version
    raise ValueError("target Store schema version is not a string constant")


def _migrate_store(root: Path, path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; "
            "from app.store import AutoReplyStore; AutoReplyStore(Path(sys.argv[1]))",
            str(path),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        check=True,
        capture_output=True,
        timeout=300,
    )


def _columns(db: sqlite3.Connection) -> frozenset[str]:
    return frozenset(row[1] for row in db.execute("pragma table_info(reply_tasks)"))


def _foreign_keys(db: sqlite3.Connection) -> tuple[tuple, ...]:
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


@dataclass
class StoreInputSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str]
    original_tasks_sql: str
    original_indexes: tuple[str, ...]
    original_columns: tuple[tuple, ...]
    migrate: Callable[[Path, Path], None] = _migrate_store

    def publish(self) -> None:
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db:
            row = db.execute(
                "select value from service_state where key=?", (VERSION_KEY,)
            ).fetchone()
            cols = _columns(db)
            if (
                row != (INPUT_VERSION,)
                or not REFERENCE_COLUMNS <= cols
                or PAYLOAD_COLUMNS & cols
            ):
                raise ValueError("Store canonical input schema readback mismatch")
            references = {
                (item[3], item[2], item[4])
                for item in db.execute("pragma foreign_key_list(reply_tasks)")
            }
            if (
                not {(name, "reply_task_inputs", "id") for name in REFERENCE_COLUMNS}
                <= references
            ):
                raise ValueError("Store canonical input foreign keys are missing")
            missing = db.execute(
                "select count(*) from reply_tasks t left join reply_task_inputs i "
                "on i.id=t.current_input_id and i.reply_task_id=t.id "
                "where i.id is null"
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from reply_tasks t left join reply_task_inputs i "
                "on i.id=t.claimed_input_id and i.reply_task_id=t.id "
                "where t.claimed_input_id is not null and i.id is null"
            ).fetchone()[0]
            if missing or claimed:
                raise ValueError("Store canonical input ownership readback mismatch")

    def rollback(self) -> None:
        from app.store import AutoReplyStore

        with closing(sqlite3.connect(self.database_path)) as db, db:
            # This connection is private to the stopped schema inverse. Rebuild
            # without renaming the original parent, then compare all FK facts.
            db.execute("pragma foreign_keys=off")
            if db.execute("pragma foreign_keys").fetchone()[0] != 0:
                raise ValueError(
                    "Store inverse requires an isolated FK-off transaction"
                )
            db.execute("begin immediate")
            row = db.execute(
                "select value,updated_at from service_state where key=?", (VERSION_KEY,)
            ).fetchone()
            cols = _columns(db)
            if (
                row == self.original_version
                and PAYLOAD_COLUMNS <= cols
                and not REFERENCE_COLUMNS & cols
            ):
                return
            if (
                row is None
                or row[0] != INPUT_VERSION
                or not REFERENCE_COLUMNS <= cols
                or PAYLOAD_COLUMNS & cols
            ):
                raise ValueError(
                    "Store rollback requires the exact canonical input transition"
                )
            before_fk = _foreign_keys(db)
            db.row_factory = sqlite3.Row
            AutoReplyStore.restore_reply_task_input_schema(
                db,
                original_tasks_sql=self.original_tasks_sql,
                original_indexes=self.original_indexes,
            )
            db.execute(
                "update service_state set value=?,updated_at=? where key=?",
                (*self.original_version, VERSION_KEY),
            )
            db.row_factory = None
            restored = tuple(
                tuple(item) for item in db.execute("pragma table_info(reply_tasks)")
            )
            if restored != self.original_columns or _foreign_keys(db) != before_fk:
                raise ValueError("Store inverse schema/FK readback mismatch")
            indexes = tuple(
                sorted(
                    row[0]
                    for row in db.execute(
                        "select sql from sqlite_master where type='index' and tbl_name='reply_tasks' and sql is not null"
                    )
                )
            )
            if indexes != tuple(sorted(self.original_indexes)):
                raise ValueError("Store inverse index readback mismatch")

    def finalize(self) -> None:
        self.verify_loaded()


def prepare_store_schema_release(
    root: Path, path: Path
) -> StoreInputSchemaRelease | RunSchemaRelease | None:
    root, path = root.resolve(), path.resolve()
    target = _target_store_version(root)
    if target is None or not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute(
            "select 1 from sqlite_master where type='table' and name='service_state'"
        ).fetchone():
            return None
        version = db.execute(
            "select value,updated_at from service_state where key=?", (VERSION_KEY,)
        ).fetchone()
        if version is None or version[0] == target:
            return None
        from app.run_schema_release import OLD_RUN_VERSION, RUN_VERSION, prepare_run_schema_release

        if (version[0], target) == (OLD_RUN_VERSION, RUN_VERSION):
            return prepare_run_schema_release(root, path, version)
        if (version[0], target) != (OLD_INPUT_VERSION, INPUT_VERSION):
            raise ValueError(
                "Store schema release needs a verified inverse for this transition"
            )
        columns = _columns(db)
        if not PAYLOAD_COLUMNS <= columns or REFERENCE_COLUMNS & columns:
            raise ValueError("Store original input schema mismatch")
        sql = db.execute(
            "select sql from sqlite_master where type='table' and name='reply_tasks'"
        ).fetchone()[0]
        indexes = tuple(
            row[0]
            for row in db.execute(
                "select sql from sqlite_master where type='index' and tbl_name='reply_tasks' and sql is not null"
            )
        )
        declarations = tuple(
            tuple(row) for row in db.execute("pragma table_info(reply_tasks)")
        )
        return StoreInputSchemaRelease(root, path, version, sql, indexes, declarations)


def prepare_database_schema_release(root: Path, path: Path):
    from app import email_schema_release

    store = prepare_store_schema_release(root, path)
    email = email_schema_release.prepare_email_schema_release(root, path)
    if store is not None and email is not None:
        raise ValueError(
            "simultaneous Store/Email changes need a verified paired inverse"
        )
    return store if store is not None else email
