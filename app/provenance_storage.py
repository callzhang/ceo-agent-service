"""Transaction-owned body-free Business Signal storage."""

from __future__ import annotations

from dataclasses import fields
from contextlib import closing
from functools import lru_cache
import sqlite3

from app import action_storage as actions
from app.action_business_storage import _next_source_rowid
from app.business_source_reader import SourceVersionBinding
from app.storage_payload import ACTION_RECORD_TYPES, PayloadLayout
from app.provenance_definitions import (
    SOURCE_FIELDS, SIGNAL_FACT_FIELDS, SIGNAL_REFERENCE_FIELDS, provenance_objects,
)


_KIND = "business_task_signals"
_COLUMN = "ba_task_signals_"
_LOGICAL_FIELDS = SOURCE_FIELDS[:-2]
_LAYOUT = PayloadLayout(SIGNAL_REFERENCE_FIELDS)


def create_provenance_schema(db: sqlite3.Connection) -> None:
    """Install the final parent declaration in the caller's schema transaction."""
    for sql in provenance_objects():
        db.execute(sql)


@lru_cache(maxsize=1)
def _schema_objects():
    with closing(sqlite3.connect(":memory:")) as db:
        create_provenance_schema(db)
        return actions._objects(db, "provenance")


def provenance_schema_is_current(db: sqlite3.Connection) -> bool:
    return actions._objects(db, "provenance") == _schema_objects()


def signal_projection():
    select = ["cast(a.legacy_key as integer) as id",
              f"a.{_COLUMN}provenance_id as provenance_id"]
    select += [f"p.{field} as {field}" for field in SOURCE_FIELDS]
    select += [f"a.{_COLUMN}{field} as {field}" for field in SIGNAL_FACT_FIELDS]
    return ("select " + ",".join(select) + " from actions a join provenance p "
            f"on p.id=a.{_COLUMN}provenance_id where a.record_type='{_KIND}'")


def _signal_row(db, clause, arguments):
    cursor = db.execute(signal_projection() + " and " + clause, arguments)
    row = cursor.fetchone()
    return dict(zip((item[0] for item in cursor.description), row, strict=True)) if row else None


def _binding(row):
    if row is None:
        return None
    names = tuple(field.name for field in fields(SourceVersionBinding))
    return SourceVersionBinding(**dict(zip(names, (row[name] for name in names), strict=True)))


def signal_binding(db: sqlite3.Connection, public_id: int) -> SourceVersionBinding | None:
    row = _signal_row(db, "a.business_signal_public_id=?", (public_id,))
    return _binding(row)


def signals_for_source(db: sqlite3.Connection, *, source_type: str, source_ref: str,
                       source_time: str) -> tuple[int, ...]:
    """Use the immutable parent source index and referenced-Signal index."""
    rows = db.execute(
        "select a.business_signal_public_id from provenance p indexed by idx_provenance_source "
        "join actions a indexed by idx_actions_signal_shared on a.ba_task_signals_provenance_id=p.id "
        "where a.record_type='business_task_signals' "
        "and p.source_type=? and p.source_ref=? and p.source_time=?",
        (source_type, source_ref, source_time),
    )
    return tuple(row[0] for row in rows)


def _insert_signal(db, payload):
    public_id = _next_source_rowid(db, _KIND)
    db.execute(
        "insert into actions(_kind,legacy_key,payload_json) values(?,?,jsonb(?))",
        (ACTION_RECORD_TYPES.code(_KIND), str(public_id),
         _LAYOUT.pack(payload,
                      scalar_codec=actions._encoded_native_sqlite_value)),
    )
    return public_id


def _same_logical_source(persisted, incoming):
    if any(getattr(persisted, name) != getattr(incoming, name) for name in _LOGICAL_FIELDS):
        raise ValueError("source version key collides with different source facts")


def insert_signal_version(
    db: sqlite3.Connection, binding: SourceVersionBinding, *, dedupe_key: str,
    conversation_title: str = "", context_json: str = "{}",
    created_at: str | None = None,
) -> int:
    """Insert one distinct Signal referencing its immutable source version.

    The caller owns an active transaction. A savepoint makes a failed parent/write
    atomic even when the caller catches the exception and commits other work.
    """
    if not db.in_transaction:
        raise ValueError("Signal insertion requires caller-owned transaction")
    if type(binding) is not SourceVersionBinding:
        raise TypeError("SourceVersionBinding required")
    actions._email_lock(db)
    db.execute("savepoint provenance_signal_insert")
    try:
        independent = {"conversation_title": conversation_title,
                       "context_json": context_json, "dedupe_key": dedupe_key}
        if created_at is None:
            independent["created_at"] = db.execute("select current_timestamp").fetchone()[0]
        else:
            independent["created_at"] = created_at
        parent = db.execute(
            "select id," + ",".join(SOURCE_FIELDS)
            + " from provenance where identity_key=?",
            (binding.identity_key,),
        ).fetchone()
        if parent is None:
            values = tuple(getattr(binding, name) for name in SOURCE_FIELDS)
            parent_id = db.execute(
                "insert into provenance(" + ",".join(SOURCE_FIELDS) + ") values("
                + ",".join("?" for _ in SOURCE_FIELDS) + ")",
                values,
            ).lastrowid
        else:
            parent_id = parent[0]
            canonical = _binding(dict(zip(SOURCE_FIELDS, parent[1:], strict=True)))
            _same_logical_source(canonical, binding)
        result = _insert_signal(db, {"provenance_id": parent_id, **independent})
    except BaseException:
        db.execute("rollback to savepoint provenance_signal_insert")
        db.execute("release savepoint provenance_signal_insert")
        raise
    db.execute("release savepoint provenance_signal_insert")
    return result
