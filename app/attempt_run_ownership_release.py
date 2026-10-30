"""Exact Store29.1 -> Store29.2 publication and current-fact inverse."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from app.attempt_run_ownership import materialize_payload, normalize_payload
from app.current_schema_manifest import current_objects, require_current_pair, schema_objects
from app.state_definitions import state_projection
from app.state_storage import upsert_state
from app.task8_storage import register_task8_functions

SOURCE_STORE_VERSION = "2026-10-29.1"
TARGET_STORE_VERSION = "2026-10-29.2"
EMAIL_VERSION = 54


@lru_cache(maxsize=1)
def source_objects():
    path = Path(__file__).with_name("schema_fixtures") / "store29_1_email54_catalog.json"
    return tuple(tuple(row) for row in json.loads(path.read_text()))


def _marker(db):
    return db.execute(
        f"select value,updated_at from ({state_projection('service_state')}) "
        "where key='store_schema_version'"
    ).fetchone()


def _write_marker(db, version, timestamp):
    upsert_state(db, "service_state", {"key": "store_schema_version", "value": version},
                 {"value": "json_quote(excluded.value)", "updated_at": "json_quote(?)"},
                 update_parameters=(timestamp,))


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _rebuild(db, *, inverse):
    """Replace only Actions, preserving native values and allocation watermarks."""
    objects = source_objects() if inverse else current_objects(email_ledger=True)
    columns = tuple(row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0)
    quoted = ",".join(map(_quote, columns))
    sequences = tuple(db.execute("select rowid,name,seq from sqlite_sequence order by rowid"))
    db.execute(f"create temp table _attempt_owner_actions as select {quoted} from main.actions")
    count = db.execute("select count(*) from temp._attempt_owner_actions").fetchone()[0]
    db.execute("drop table main.actions")
    table = next(sql for kind, _name, owner, sql in objects if kind == "table" and owner == "actions")
    db.execute(table)
    target = tuple(row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0)
    if columns != target:
        raise ValueError("Attempt ownership transition has an unmapped stored Actions column")
    transform = materialize_payload if inverse else normalize_payload
    db.create_function("ceo_attempt_owner_payload", 1, lambda blob: transform(db, blob))
    values = ["case when _kind=15 then ceo_attempt_owner_payload(payload_json) else payload_json end"
              if name == "payload_json" else _quote(name) for name in columns]
    db.execute(f"insert into main.actions({quoted}) select {','.join(values)} from temp._attempt_owner_actions")
    if db.execute("select count(*) from main.actions").fetchone()[0] != count:
        raise ValueError("Attempt ownership transition changed Action row count")
    for kind, _name, owner, sql in objects:
        if owner == "actions" and kind != "table":
            db.execute(sql)
    db.execute("delete from sqlite_sequence")
    db.executemany("insert into sqlite_sequence(rowid,name,seq) values(?,?,?)", sequences)
    db.execute("drop table temp._attempt_owner_actions")


@dataclass
class AttemptRunOwnershipRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    published: bool = False

    def _begin(self, db):
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")

    def _require(self, db, *, source):
        expected = source_objects() if source else current_objects(email_ledger=True)
        marker = self.source_marker if source else None
        actual = _marker(db)
        if (schema_objects(db) != expected
                or (tuple(actual or ()) != marker if source else actual is None or actual[0] != TARGET_STORE_VERSION)
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
            raise ValueError("Attempt ownership release requires its exact source/target declaration and marker")
        if _foreign_keys(db) != self.source_fk:
            raise ValueError("Attempt ownership release FK findings changed")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            self._begin(db)
            try:
                self._require(db, source=True)
                _rebuild(db, inverse=False)
                _write_marker(db, TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                self._require(db, source=False)
                if db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("Attempt ownership target integrity differs")
                db.commit()
                self.published = True
            except BaseException:
                db.rollback()
                raise

    def verify_loaded(self):
        require_current_pair(self.database_path)

    def finalize(self):
        self.verify_loaded()

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            self._begin(db)
            try:
                if not self.published:
                    self._require(db, source=True)
                    db.rollback()
                    return
                self._require(db, source=False)
                _rebuild(db, inverse=True)
                _write_marker(db, *self.source_marker)
                self._require(db, source=True)
                if db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("Attempt ownership inverse integrity differs")
                db.commit()
                self.published = False
            except BaseException:
                db.rollback()
                raise


def prepare_attempt_run_ownership_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        register_task8_functions(db)
        db.execute("begin")
        marker = _marker(db)
        if (schema_objects(db) != source_objects() or marker is None
                or marker[0] != SOURCE_STORE_VERSION
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
            raise ValueError("Unsupported Store29.1 / Email54 ownership source")
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Attempt ownership source integrity differs")
        return AttemptRunOwnershipRelease(path, tuple(marker), _foreign_keys(db))
