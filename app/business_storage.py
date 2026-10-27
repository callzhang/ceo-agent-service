"""Source-shaped, caller-transactional writes for unselected Business storage."""

from dataclasses import dataclass
import json
import sqlite3

from app.action_storage import (
    _encoded_native_sqlite_value, _native_integer_primary_key, _raise_autoincrement_full,
    _source_bound_null,
)
from app.business_definitions import (
    BUSINESS_COMMON, BUSINESS_DEFAULTS, BUSINESS_REFERENCES,
    _definitions,
)

from app.business_legacy_fields import (
    CURRENT_BUSINESS_FIELDS as BUSINESS_FIELDS,
    CURRENT_BUSINESS_PROJECTIONS as BUSINESS_PROJECTIONS,
    current_business_payload_fields as business_payload_fields,
)


@dataclass(frozen=True)
class BusinessWriteResult:
    lastrowid: int | None
    rowcount: int


def create_business_schema(db: sqlite3.Connection, *, include_candidates: bool = False):
    """Install declarations only; caller owns BEGIN/commit/rollback."""
    table, indexes, triggers = _definitions(include_candidates=include_candidates)
    for sql in (table, *indexes, *triggers):
        db.execute(sql)


def _check_values(kind, values):
    if kind not in BUSINESS_FIELDS or set(values) - set(BUSINESS_FIELDS[kind]):
        raise ValueError("unsupported Business source fields")
    for value in values.values():
        _encoded_native_sqlite_value(value)


def _lock(db):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Business writes require a caller transaction")
    db.execute("update sqlite_sequence set seq=seq where 0")


def _next_id(db, kind):
    water = db.execute("select seq from sqlite_sequence where name=?", ("business:" + kind,)).fetchone()
    maximum = db.execute("select max(public_id) from business where record_type=?", (kind,)).fetchone()[0]
    high = max(water[0] if water else 0, maximum or 0, 0)
    if high == 2**63 - 1:
        # Actual callers use plain INSERT/lastrowid. Their captured native
        # families roll back the whole transaction on SQLITE_FULL; a native
        # INSERT RETURNING control behaves differently and is not this API.
        db.rollback()
        _raise_autoincrement_full()
    return high + 1


def _complete_values(db, kind, supplied):
    fields = BUSINESS_FIELDS[kind][1:]
    expressions = ["?" if f in supplied else BUSINESS_DEFAULTS[kind].get(f, "null") for f in fields]
    bindings = tuple(supplied[f] for f in fields if f in supplied)
    row = db.execute("select " + ",".join(expressions), bindings).fetchone()
    return dict(zip(fields, row, strict=True))


def _stored_values(db, kind, values):
    payload = json.dumps({f: _encoded_native_sqlite_value(values[f]) for f in business_payload_fields(kind)},
                         ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    physical = {ref[1]: None for ref in BUSINESS_REFERENCES.values()}
    if kind in BUSINESS_REFERENCES:
        field, target, parent = BUSINESS_REFERENCES[kind]
        raw = values[field]
        if not _source_bound_null(raw):
            row = db.execute("select id from business where record_type=? and public_id=?", (parent, raw)).fetchone()
            # Zero cannot be a physical Business ID. Defer its missing-parent
            # failure to the native composite FK, after IGNORE's row checks.
            physical[target] = row[0] if row else 0
    common = {f: values[f] if f in values else "" for f in BUSINESS_COMMON}
    return {"payload_json": payload, **common, **physical}


def insert_business(db: sqlite3.Connection, kind: str, values: dict, *, ignore=False) -> BusinessWriteResult:
    _check_values(kind, values)
    _lock(db)
    public_id = (_next_id(db, kind) if _source_bound_null(values.get("id"))
                 else _native_integer_primary_key(values["id"]))
    stored = _stored_values(db, kind, _complete_values(db, kind, values))
    data = {"record_type": kind, "legacy_key": str(public_id), **stored}
    cursor = db.execute("insert " + ("or ignore " if ignore else "") + "into business(" +
                        ",".join(data) + ") values(" + ",".join("?" for _ in data) + ") returning public_id",
                        tuple(data.values()))
    row = cursor.fetchone()
    cursor.fetchall()
    return BusinessWriteResult(row[0] if row else None, cursor.rowcount)


def update_business(db: sqlite3.Connection, kind: str, public_id, values: dict, *, ignore=False) -> int:
    _check_values(kind, values)
    _lock(db)
    cursor = db.execute("select * from (" + BUSINESS_PROJECTIONS[kind] + ") where id=?", (public_id,))
    row = cursor.fetchone()
    if row is None:
        return 0
    current = dict(zip((c[0] for c in cursor.description), row, strict=True))
    physical_id = db.execute("select id from business where record_type=? and public_id=?", (kind, public_id)).fetchone()[0]
    current.update(values)
    new_id = _native_integer_primary_key(current["id"], update=True)
    stored = {"legacy_key": str(new_id), **_stored_values(db, kind, current)}
    result = db.execute("update " + ("or ignore " if ignore else "") + "business set " +
                        ",".join(f"{field}=?" for field in stored) + " where id=?",
                        (*stored.values(), physical_id))
    return result.rowcount


def delete_business(db: sqlite3.Connection, kind: str, public_id) -> int:
    _check_values(kind, {})
    _lock(db)
    return db.execute("delete from business where record_type=? and public_id=?", (kind, public_id)).rowcount
