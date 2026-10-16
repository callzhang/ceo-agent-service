"""Caller-transactional storage for the unselected fifteen-kind State table."""
from contextlib import closing
from dataclasses import dataclass
import sqlite3

from app.action_storage import (
    _encoded_native_sqlite_value, _native_integer_primary_key,
    _native_payload, _source_bound_null,
)
from app.state_definitions import (
    STATE_DEFAULTS, STATE_FIELDS, STATE_INDEX_DDL, STATE_NOT_NULL, STATE_TABLE_DDL,
    _quote,
)


@dataclass(frozen=True)
class StateWriteResult:
    lastrowid: int | None
    rowcount: int


_UNSET = object()


def create_state_schema(db: sqlite3.Connection) -> None:
    """Install declarations only; the caller owns schema selection and commit."""
    for sql in (STATE_TABLE_DDL, *STATE_INDEX_DDL):
        db.execute(sql)


def _check(kind, values):
    if kind not in STATE_FIELDS or set(values) - set(STATE_FIELDS[kind]):
        raise ValueError("unsupported State kind or source field")
    for value in values.values():
        _encoded_native_sqlite_value(value)


def _lock(db):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("State writes require a caller transaction")
    db.execute("update state set id=id where 0")


def _next_rowid(db, kind):
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from state where record_type=?",
        (kind,),
    ).fetchone()[0]
    if maximum != 2**63 - 1:
        return 1 if maximum is None else maximum + 1
    # The existing Configs/Email allocator uses the same native SQLite branch.
    # This scratch allocator never reads/writes another runtime database.
    with closing(sqlite3.connect(":memory:")) as allocator:
        allocator.execute("create table ids(id integer primary key)")
        allocator.executemany("insert into ids(id) values(?)", db.execute(
            "select cast(legacy_key as integer) from state where record_type=?", (kind,)))
        return allocator.execute("insert into ids default values").lastrowid


def _completed(db, kind, values, *, replace=False):
    fields = STATE_FIELDS[kind]
    defaults = STATE_DEFAULTS[kind]
    not_null = STATE_NOT_NULL[kind]
    expressions = [
        ("coalesce(?," + defaults[field] + ")" if replace and field in not_null
         and field in defaults else "?") if field in values else defaults.get(field, "null")
        for field in fields
    ]
    parameters = tuple(values[field] for field in fields if field in values)
    row = db.execute("select " + ",".join(expressions), parameters).fetchone()
    return dict(zip(fields, row, strict=True))


def _conflict(ignore, replace):
    if ignore and replace:
        raise ValueError("conflicting State write modes")
    return "or ignore " if ignore else "or replace " if replace else ""


def insert_state(db, kind, values, *, source_rowid=_UNSET, ignore=False, replace=False):
    _check(kind, values)
    mode = _conflict(ignore, replace)
    _lock(db)
    identity = (_next_rowid(db, kind) if source_rowid is _UNSET or _source_bound_null(source_rowid)
                else _native_integer_primary_key(source_rowid))
    cursor = db.execute(
        "insert " + mode + "into state(record_type,legacy_key,payload_json) values(?,?,?)",
        (kind, str(identity), _native_payload(_completed(db, kind, values, replace=replace))),
    )
    return StateWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def update_state(db, kind, source_rowid, values, *, new_source_rowid=_UNSET,
                 where_sql="", parameters=(), ignore=False, replace=False):
    _check(kind, values)
    mode = _conflict(ignore, replace)
    if new_source_rowid is not _UNSET:
        _encoded_native_sqlite_value(new_source_rowid)
    _lock(db)
    fields = STATE_FIELDS[kind]
    guard = " and (" + where_sql + ")" if where_sql else ""
    row = db.execute(
        "select cast(legacy_key as integer)," + ",".join(map(_quote, fields)) +
        " from state where record_type=? and cast(legacy_key as integer)=?" + guard,
        (kind, source_rowid, *parameters),
    ).fetchone()
    if row is None:
        return 0
    # The guarded read occurs once under the writer lock. No other writer can
    # change that row before this single SQLite UPDATE applies its constraints.
    old_identity = row[0]
    identity = (old_identity if new_source_rowid is _UNSET else
                _native_integer_primary_key(new_source_rowid, update=True))
    complete = dict(zip(fields, tuple(row)[1:], strict=True))
    complete.update(values)
    if replace:
        complete = _completed(db, kind, complete, replace=True)
    return db.execute(
        "update " + mode + "state set legacy_key=?,payload_json=? "
        "where record_type=? and legacy_key=?",
        (str(identity), _native_payload(complete), kind, str(old_identity)),
    ).rowcount


def delete_state(db, kind, source_rowid, *, where_sql="", parameters=()):
    _check(kind, {})
    _lock(db)
    guard = " and (" + where_sql + ")" if where_sql else ""
    return db.execute(
        "delete from state where record_type=? and cast(legacy_key as integer)=?" + guard,
        (kind, source_rowid, *parameters),
    ).rowcount
