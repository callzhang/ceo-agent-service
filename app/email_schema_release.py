"""Reversible release of the provider identity-index migration.

This changes only index/schema-version metadata. Rollback never restores a
pre-start database snapshot over business writes made by the replacement.
"""
from __future__ import annotations

import ast
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Callable


LOOKUP_INDEX = "idx_email_provider_observations_lookup"


def _target_schema_version(root: Path) -> int:
    module = ast.parse((root / "app" / "email_store.py").read_text())
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "EMAIL_SCHEMA_VERSION"
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            if type(value) is int:
                return value
    raise ValueError("target Email schema version is not an integer constant")


def _migrate(root: Path, database_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-c",
         "from pathlib import Path; import sys; from app.email_store import EmailStore; "
         "EmailStore(Path(sys.argv[1]))", str(database_path)],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
        check=True, capture_output=True, timeout=300,
    )


@dataclass
class EmailSchemaRelease:
    root: Path
    database_path: Path
    versions: tuple[tuple[int, str], ...]
    index_sql: str
    migrate: Callable[[Path, Path], None] = _migrate

    def publish(self) -> None:
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db:
            version = db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            index = db.execute("select sql from sqlite_master where name=?", (LOOKUP_INDEX,)).fetchone()
            if version != 48 or index is not None:
                raise ValueError("Email schema 48 provider-index readback mismatch")

    def rollback(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute("begin immediate")
            versions = tuple(db.execute(
                "select version,applied_at from email_schema_migrations order by version"
            ))
            index = db.execute("select sql from sqlite_master where name=?", (LOOKUP_INDEX,)).fetchone()
            if versions == self.versions and index == (self.index_sql,):
                return
            if not versions or versions[-1][0] != 48 or versions[:-1] != self.versions or index is not None:
                raise ValueError("Email schema rollback requires the exact 47-to-48 index transition")
            db.execute(self.index_sql)
            db.execute("delete from email_schema_migrations where version=48")
            restored = tuple(db.execute(
                "select version,applied_at from email_schema_migrations order by version"
            ))
            if restored != self.versions:
                raise ValueError("Email schema rollback version readback mismatch")

    def finalize(self) -> None:
        self.verify_loaded()


def prepare_email_schema_release(root: Path, database_path: Path) -> EmailSchemaRelease | None:
    root = root.resolve()
    database_path = database_path.resolve()
    if not database_path.exists():
        return None
    with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("select 1 from sqlite_master where name='email_schema_migrations'").fetchone():
            return None
        versions = tuple(db.execute("select version,applied_at from email_schema_migrations order by version"))
        current = versions[-1][0] if versions else 0
        target = _target_schema_version(root)
        if current == target:
            return None
        if (current, target) != (47, 48):
            raise ValueError("Email schema release needs a verified inverse for this transition")
        index = db.execute("select sql from sqlite_master where type='index' and name=?", (LOOKUP_INDEX,)).fetchone()
        if index is None:
            raise ValueError("Email schema 47 provider lookup index is missing")
        return EmailSchemaRelease(root, database_path, versions, index[0])
