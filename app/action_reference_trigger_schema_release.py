"""Exact Store25.1/Email54 -> Store25.2/Email54 obsolete-trigger retirement."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3

from app.current_schema_manifest import schema_objects

SOURCE_DIGEST = "eb0d83ce2c7f1bfa2fc6bbaac352c2672472686e3636a5c5352363d6e37a6f74"
TARGET_DIGEST = "7b4c91875faf9cf1bc57f912fc692b7c2a3966a03b9b2e384b2d8470ce0c832b"

SOURCE_STORE_VERSION = "2026-10-25.1"
TARGET_STORE_VERSION = "2026-10-25.2"
EMAIL_VERSION = 54
VERSION_KEY = "store_schema_version"
RETIRED_TRIGGER_NAME = "trg_actions_public_reference_update"
# Exact SQLite-normalized declaration from the published Store25.1 schema.
RETIRED_TRIGGER_SQL = "CREATE TRIGGER trg_actions_public_reference_update before update of legacy_key on actions when new.legacy_key<>old.legacy_key and exists(select 1 from actions where predecessor_review_id=old.id or candidate_id=old.id or review_id=old.id or selection_id=old.id or execution_id=old.id) begin select raise(abort,'FOREIGN KEY constraint failed'); end"


@lru_cache(maxsize=1)
def target_objects():
    from app.signal_source_owner_schema_release import source_objects as store_252_objects

    objects = store_252_objects()
    digest = hashlib.sha256(json.dumps(
        objects, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    if digest != TARGET_DIGEST:
        raise ValueError("frozen Action trigger target declaration changed")
    return objects


@lru_cache(maxsize=1)
def source_objects():
    objects = (*target_objects(),
               ("trigger", RETIRED_TRIGGER_NAME, "actions", RETIRED_TRIGGER_SQL))
    result = tuple(sorted(objects, key=lambda obj: (obj[0], obj[1])))
    digest = hashlib.sha256(json.dumps(
        result, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    if digest != SOURCE_DIGEST:
        raise ValueError("frozen Action trigger source declaration changed")
    return result


def _marker(db):
    from app.state_definitions import state_projection
    return db.execute(f"select value,updated_at from ({state_projection('service_state')}) where key=?",
                      (VERSION_KEY,)).fetchone()


def _write_marker(db, version, updated_at):
    from app.state_storage import update_state_rows
    update_state_rows(db, "service_state", {"value": version, "updated_at": updated_at},
                      where_sql="key=?", parameters=(VERSION_KEY,))


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


@dataclass
class ActionReferenceTriggerRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    published: bool = False

    def _verify(self, db, *, target):
        expected = target_objects() if target else source_objects()
        marker = _marker(db)
        valid_marker = (marker is not None and marker[0] == TARGET_STORE_VERSION) if target else marker == self.source_marker
        if (not valid_marker or schema_objects(db) != expected
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION
                or _foreign_keys(db) != self.source_fk
                or db.execute("pragma quick_check").fetchone()[0] != "ok"):
            raise ValueError("Action trigger release schema, marker or integrity mismatch")

    def _convert(self, *, inverse):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("begin immediate")
            try:
                self._verify(db, target=inverse)
                if inverse:
                    db.execute(RETIRED_TRIGGER_SQL)
                    _write_marker(db, *self.source_marker)
                else:
                    db.execute(f"drop trigger {RETIRED_TRIGGER_NAME}")
                    _write_marker(db, TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                self._verify(db, target=not inverse)
                db.commit()
                self.published = not inverse
            except BaseException:
                db.rollback()
                raise

    def publish(self):
        self._convert(inverse=False)

    def verify_loaded(self):
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            self._verify(db, target=True)

    def rollback(self):
        if self.published:
            self._convert(inverse=True)
        else:
            with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("begin")
                self._verify(db, target=False)

    def finalize(self):
        self.verify_loaded()


def prepare_action_reference_trigger_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker = _marker(db)
        if (marker is None or marker[0] != SOURCE_STORE_VERSION
                or schema_objects(db) != source_objects()
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
            raise ValueError("unsupported Store25.1 / Email54 source schema")
        release = ActionReferenceTriggerRelease(path, tuple(marker), _foreign_keys(db))
        release._verify(db, target=False)
        return release
