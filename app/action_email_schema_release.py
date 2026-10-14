"""Paired Email29 publication and its current-fact inverse."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable

from app.action_email_storage import (
    EXTERNAL_TRIGGER_NAMES,
    SOURCE_TABLES,
    _target_external_sql,
    capture_email_action_source,
    migrate_email_action_schema,
    restore_email_action_schema,
)
from app.action_storage import action_schema_is_current, _encoded_sqlite_value
from app.task_schema_release import _foreign_keys


SOURCE_STORE_VERSION = "2026-10-13.5"
SOURCE_EMAIL_VERSION = 51
TARGET_STORE_VERSION = "2026-10-14.1"
TARGET_EMAIL_VERSION = 52
VERSION_KEY = "store_schema_version"


def require_current_email_pair(path: Path, *, store_version: str, email_version: int) -> None:
    """Read-only source selection before constructor caches or journal changes."""
    path = Path(path).resolve()
    if not path.exists():
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
        marker = db.execute("select value from main.service_state where key=?", (VERSION_KEY,)).fetchone() if "service_state" in tables else None
        store = marker[0] if marker is not None else None
        email = db.execute("select coalesce(max(version),0) from main.email_schema_migrations").fetchone()[0] if "email_schema_migrations" in tables else None
        if (store, email) == (SOURCE_STORE_VERSION, SOURCE_EMAIL_VERSION):
            raise sqlite3.IntegrityError("Store19/Email51 requires the paired schema release before target constructors")
        from app.email_current_schema import _CURRENT_TABLE_SQL
        email_tables = {"email_classifications", "email_category_configs", "email_category_folder_bindings", *(name for name, _ in _CURRENT_TABLE_SQL), *SOURCE_TABLES}
        if store is None and email is None and not email_tables.intersection(tables):
            # The Store's existing bootstrap-source check separately owns core
            # collisions and partial unversioned bootstrap refusal.
            return
        if store == store_version:
            if email == email_version:
                return
            if email is None and not (email_tables - {"email_classifications"}).intersection(tables):
                return  # Complete Store-first, before Email's fresh transaction.
        raise sqlite3.IntegrityError("unsupported or mixed Store/Email source schema")


def _markers(db):
    version = db.execute(
        "select value,updated_at from main.service_state where key=?", (VERSION_KEY,)
    ).fetchone()
    versions = tuple(db.execute(
        "select version,applied_at from main.email_schema_migrations order by version"
    ))
    return version, versions


def _inverse_metadata(source):
    return {key: source[key] for key in (
        "action_variant", "email_variant", "source_actions", "source_tables", "external_triggers"
    )}


def _migrate_pair(release: "ActionEmailSchemaRelease") -> None:
    """Carry prepared metadata, never business rows, into the real cold opener."""
    payload = {
        "root": str(release.root), "database_path": str(release.database_path),
        "original_version": [release.original_version[0], _encoded_sqlite_value(release.original_version[1])],
        "versions": [[version, _encoded_sqlite_value(stamp)] for version, stamp in release.versions],
        "source_schema": _inverse_metadata(release.source_schema), "source_fk": release.source_fk,
        "target_store_version": release.target_store_version, "target_email_version": release.target_email_version,
    }
    subprocess.run(
        [sys.executable, "-c", "from app.action_email_schema_release import _paired_initializer; _paired_initializer()"],
        input=json.dumps(payload, ensure_ascii=False, allow_nan=False), text=True,
        cwd=release.root, env={**os.environ, "PYTHONPATH": str(release.root)},
        check=True, capture_output=True, timeout=300,
    )


@dataclass
class ActionEmailSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str | bytes]
    versions: tuple[tuple[int, str | bytes], ...]
    source_schema: dict
    source_fk: tuple[tuple, ...]
    target_store_version: str
    target_email_version: int
    migrate: Callable[["ActionEmailSchemaRelease"], None] = _migrate_pair

    def _verify_integrity(self, db):
        if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Email paired declaration integrity/FK mismatch")

    def _verify_source(self, db):
        if _markers(db) != (self.original_version, self.versions):
            raise ValueError("Email paired source markers changed")
        # Only declarations are compared: current business rows and allocator
        # waters are inputs to conversion, never a stored pre-publication copy.
        current = capture_email_action_source(db)
        if _inverse_metadata(current) != _inverse_metadata(self.source_schema):
            raise ValueError("Email paired source declaration changed")
        self._verify_integrity(db)

    def _verify_target(self, db):
        marker, versions = _markers(db)
        if marker is None or marker[0] != self.target_store_version or \
                versions[:-1] != self.versions or not versions or \
                versions[-1] != (self.target_email_version, marker[1]):
            raise ValueError("Email paired target markers mismatch")
        if not action_schema_is_current(
            db, include_reply=True, reply_variant=self.source_schema["action_variant"], include_email=True
        ) or db.execute(
            "select 1 from main.sqlite_schema where name in (" +
            ",".join("?" for _ in SOURCE_TABLES) + ") limit 1", SOURCE_TABLES
        ).fetchone():
            raise ValueError("Email paired target declaration mismatch")
        expected = _target_external_sql()
        triggers = tuple(db.execute(
            "select name,sql from main.sqlite_schema where type='trigger' and name in (" +
            ",".join("?" for _ in EXTERNAL_TRIGGER_NAMES) + ") order by name", EXTERNAL_TRIGGER_NAMES
        ))
        if triggers != tuple(sorted(expected.items())):
            raise ValueError("Email paired external trigger mismatch")
        self._verify_integrity(db)

    def publish(self):
        self.migrate(self)
        self.verify_loaded()

    def apply(self):
        """Apply the same captured atomic protocol, including both markers."""
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify_source(db)
                inverse = migrate_email_action_schema(db)
                if inverse != _inverse_metadata(self.source_schema):
                    raise ValueError("Email paired captured inverse declaration mismatch")
                now = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "update main.service_state set value=?,updated_at=? where key=?",
                    (self.target_store_version, now, VERSION_KEY),
                )
                db.execute(
                    "insert into main.email_schema_migrations(version,applied_at) values(?,?)",
                    (self.target_email_version, now),
                )
                self._verify_target(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        self.verify_loaded()

    def verify_loaded(self):
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            self._verify_target(db)

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                if _markers(db) == (self.original_version, self.versions):
                    self._verify_source(db)
                    db.commit()
                    return
                self._verify_target(db)
                restore_email_action_schema(db, _inverse_metadata(self.source_schema))
                db.execute(
                    "update main.service_state set value=?,updated_at=? where key=?",
                    (*self.original_version, VERSION_KEY),
                )
                # Target acceptance required the complete exact source ledger
                # plus this one target row. Deleting it restores every original
                # version/timestamp without touching current business facts.
                db.execute("delete from main.email_schema_migrations where version=?", (self.target_email_version,))
                self._verify_source(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def _timestamp_from_pipe(value):
    # TEXT affinity preserves ordinary scalars as text; native BLOB timestamps
    # are legal and accepted by the predecessor constructors.
    return bytes.fromhex(value["v"]) if isinstance(value, dict) and value.get("t") == "blob" else value


def _paired_initializer() -> None:
    payload = json.load(sys.stdin)
    release = ActionEmailSchemaRelease(
        Path(payload["root"]), Path(payload["database_path"]),
        (payload["original_version"][0], _timestamp_from_pipe(payload["original_version"][1])),
        tuple((version, _timestamp_from_pipe(stamp)) for version, stamp in payload["versions"]),
        payload["source_schema"], tuple(tuple(row) for row in payload["source_fk"]),
        payload["target_store_version"], payload["target_email_version"],
    )
    release.apply()
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    AutoReplyStore(release.database_path)
    EmailStore(release.database_path)


def prepare_action_email_schema_release(
    root: Path, path: Path, *, target_store_version: str, target_email_version: int
) -> ActionEmailSchemaRelease:
    """Capture the exact Store19/Email51 pair; callers must supply a target pair.

    The coordinator selects the real pair and subprocess. Explicit sentinel
    targets still exercise this same atomic protocol in confined tests.
    """
    root, path = root.resolve(), path.resolve()
    if type(target_store_version) is not str or not target_store_version.strip() or \
            target_store_version == SOURCE_STORE_VERSION or \
            type(target_email_version) is not int or target_email_version <= SOURCE_EMAIL_VERSION:
        raise ValueError("Email paired protocol requires an explicit different target pair")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        version, versions = _markers(db)
        if version is None or version[0] != SOURCE_STORE_VERSION or \
                not versions or versions[-1][0] != SOURCE_EMAIL_VERSION:
            raise ValueError("Email paired protocol requires the exact Store19/Email51 source")
        source = capture_email_action_source(db)
        release = ActionEmailSchemaRelease(
            root, path, version, versions, source, _foreign_keys(db),
            target_store_version, target_email_version,
        )
        release._verify_integrity(db)
        return release
