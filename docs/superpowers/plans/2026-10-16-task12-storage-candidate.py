"""Caller-transactional writers for the twelve native Tasks source families.

The target declaration remains unselected. Source ROWIDs/public keys and global
physical Tasks ids are distinct; no API here commits, migrates or calls a provider.
"""

from contextlib import closing
from dataclasses import dataclass
import sqlite3

from app.action_storage import (
    _encoded_native_sqlite_value,
    _native_integer_primary_key,
    _native_payload,
    _raise_autoincrement_full,
    _source_bound_null,
)
from app.task20_definitions import (
    TASK12_AUTOINCREMENT_KINDS,
    TASK12_PRIMARY_KEYS,
    TASK20_COLUMNS,
    TASK20_DEFAULTS,
    TASK20_FIELDS,
    TASK20_NEW_KINDS,
    TASK20_PAYLOAD_FIELDS,
    task20_column,
    task20_value,
)

_UNSET = object()
_NUMERIC_SCOPE = " and record_type not in ('workbench_tasks','workbench_turns')"
_KIND_PREDICATE = "record_type=?" + _NUMERIC_SCOPE
_PHYSICAL_PARENTS = {
    "business_object_tasks": "reply_task_id",
    "workbench_artifacts": "turn_id",
    "workbench_attachments": "task_id",
}


@dataclass(frozen=True)
class Task12WriteResult:
    lastrowid: int | None
    rowcount: int
    physical_id: int | None


def _check(kind, values):
    if kind not in TASK20_NEW_KINDS or set(values) - set(TASK20_FIELDS[kind]):
        raise ValueError("unsupported native Tasks kind or source field")
    for value in values.values():
        _encoded_native_sqlite_value(value)


def _lock(db):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Tasks writes require a caller transaction")
    db.execute("update tasks set id=id where 0")


def _mode(ignore, replace):
    if ignore and replace:
        raise ValueError("conflicting Tasks write modes")
    return "or ignore " if ignore else "or replace " if replace else ""


def _rowid_next(db, kind):
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from tasks where " + _KIND_PREDICATE,
        (kind,),
    ).fetchone()[0]
    if maximum != 2**63 - 1:
        return 1 if maximum is None else maximum + 1
    with closing(sqlite3.connect(":memory:")) as allocator:
        allocator.execute("create table ids(id integer primary key)")
        allocator.executemany(
            "insert into ids values(?)",
            db.execute(
                "select cast(legacy_key as integer) from tasks where "
                + _KIND_PREDICATE,
                (kind,),
            ),
        )
        return allocator.execute("insert into ids default values").lastrowid


def _physical_next(db):
    seq = db.execute("select seq from sqlite_sequence where name='tasks'").fetchone()
    maximum = db.execute("select max(id) from tasks").fetchone()[0]
    high = max(seq[0] if seq else 0, maximum or 0, 0)
    if high < 2**63 - 1:
        return high + 1
    # New source families retain their own identity streams even if the old
    # shared Tasks AUTOINCREMENT stream is exhausted. This is an explicit free
    # structural id, never a reused public source id or another runtime DB.
    with closing(sqlite3.connect(":memory:")) as allocator:
        allocator.execute("create table ids(id integer primary key)")
        allocator.executemany(
            "insert into ids values(?)", db.execute("select id from tasks")
        )
        return allocator.execute("insert into ids default values").lastrowid


def _round_before_full(value):
    # Native round BEFORE INSERT runs before its source allocator FULL error.
    # A scratch INTEGER-affinity row retains source coercion and RAISE error.
    with closing(sqlite3.connect(":memory:")) as source:
        source.execute("create table number(value integer)")
        source.execute(
            "create trigger validate_number before insert on number "
            "when typeof(new.value)<>'integer' or new.value<=0 begin "
            "select raise(abort,'feedback round number must be a positive integer'); end"
        )
        source.execute("insert into number values(?)", (value,))


def _auto_next(db, kind, values):
    seq = db.execute(
        "select seq from sqlite_sequence where name=?", ("tasks:" + kind,)
    ).fetchone()
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from tasks where " + _KIND_PREDICATE,
        (kind,),
    ).fetchone()[0]
    high = max(seq[0] if seq else 0, maximum or 0, 0)
    if high == 2**63 - 1:
        if kind == "feedback_processing_rounds":
            _round_before_full(values.get("round_number"))
        else:
            db.rollback()
        _raise_autoincrement_full()
    return high + 1


def _payload(db, kind, values, replace):
    required = {c[0] for c in TASK20_COLUMNS[kind] if c[2]}
    bound = {
        f: values[f]
        for f in TASK20_PAYLOAD_FIELDS[kind]
        if f in values
        and not (
            replace
            and f in required
            and f in TASK20_DEFAULTS[kind]
            and _source_bound_null(values[f])
        )
    }
    missing = {
        f: "json_quote(" + TASK20_DEFAULTS[kind].get(f, "null") + ")"
        for f in TASK20_PAYLOAD_FIELDS[kind]
        if f not in bound
    }
    expression = "?"
    if missing:
        expression = (
            "json_set(?,"
            + ",".join(
                "'$." + f + "',json(" + value + ")" for f, value in missing.items()
            )
            + ")"
        )
    return expression, _native_payload(bound)


def _parent(db, kind, values):
    if kind in _PHYSICAL_PARENTS:
        return values.get(_PHYSICAL_PARENTS[kind])
    if kind == "feedback_iteration_decisions":
        key = values.get("batch_id")
        if _source_bound_null(key):
            return None
        row = db.execute(
            "select id from tasks where record_type='feedback_processing_batches' and "
            + task20_column("feedback_processing_batches", "batch_id")
            + "=?",
            (key,),
        ).fetchone()
        # Public-key+type composite FK rejects this missing key even if physical
        # id0 exists, after native IGNORE has considered source row constraints.
        return row[0] if row else 0
    return None


def _key_physical(db, kind, values, identity):
    fields = TASK12_PRIMARY_KEYS[kind]
    keys = {
        f: identity
        if kind in TASK12_AUTOINCREMENT_KINDS and f == "id"
        else values.get(f)
        for f in fields
    }
    if all(not _source_bound_null(value) for value in keys.values()):
        row = db.execute(
            "select id from tasks where "
            + _KIND_PREDICATE
            + " and "
            + " and ".join(task20_column(kind, f) + "=?" for f in fields),
            (kind, *keys.values()),
        ).fetchone()
        if row is not None:
            return row[0]
    return None


def insert_task12(
    db, kind, values, *, source_rowid=_UNSET, ignore=False, replace=False
):
    _check(kind, values)
    mode = _mode(ignore, replace)
    if kind in TASK12_AUTOINCREMENT_KINDS and source_rowid is not _UNSET:
        raise ValueError("AUTOINCREMENT source id is supplied as id")
    _lock(db)
    if kind in TASK12_AUTOINCREMENT_KINDS:
        identity = (
            _auto_next(db, kind, values)
            if _source_bound_null(values.get("id"))
            else _native_integer_primary_key(values["id"])
        )
    else:
        identity = (
            _rowid_next(db, kind)
            if source_rowid is _UNSET or _source_bound_null(source_rowid)
            else _native_integer_primary_key(source_rowid)
        )
    physical = _key_physical(db, kind, values, identity) if replace else None
    if physical is None and replace:
        existing = db.execute(
            "select id from tasks where "
            + _KIND_PREDICATE
            + " and cast(legacy_key as integer)=?",
            (kind, identity),
        ).fetchone()
        physical = existing[0] if existing else None
    if physical is None:
        physical = _physical_next(db)
    expression, payload = _payload(db, kind, values, replace)
    cursor = db.execute(
        "insert "
        + mode
        + "into tasks(id,record_type,legacy_key,parent_id,payload_json) "
        "values(?,?,?,?," + expression + ")",
        (physical, kind, str(identity), _parent(db, kind, values), payload),
    )
    return Task12WriteResult(
        identity if cursor.rowcount else None,
        cursor.rowcount,
        physical if cursor.rowcount else None,
    )


def task12_projection(kind, *, include_rowid=False):
    _check(kind, {})
    fields = ["cast(legacy_key as integer) as rowid"] if include_rowid else []
    fields.extend(
        task20_value(kind, f) + ' as "' + f + '"' for f in TASK20_FIELDS[kind]
    )
    return (
        "select "
        + ",".join(fields)
        + " from tasks where record_type="
        + repr(kind)
        + _NUMERIC_SCOPE
    )


def update_task12(
    db,
    kind,
    source_rowid,
    values,
    *,
    new_source_rowid=_UNSET,
    where_sql="",
    parameters=(),
    ignore=False,
    replace=False,
):
    _check(kind, values)
    mode = _mode(ignore, replace)
    if kind in TASK12_AUTOINCREMENT_KINDS and new_source_rowid is not _UNSET:
        raise ValueError("AUTOINCREMENT source id is supplied as id")
    _lock(db)
    guard = " and (" + where_sql + ")" if where_sql else ""
    columns = ",".join(
        task20_value(kind, f) + ' as "' + f + '"' for f in TASK20_FIELDS[kind]
    )
    row = db.execute(
        "select * from(select id as __physical_id,cast(legacy_key as integer) as rowid,"
        + columns
        + " from tasks where "
        + _KIND_PREDICATE
        + ") where rowid=?"
        + guard,
        (kind, source_rowid, *parameters),
    ).fetchone()
    if row is None:
        return 0
    current = dict(zip(TASK20_FIELDS[kind], tuple(row)[2:], strict=True))
    current.update(values)
    identity = (
        _native_integer_primary_key(current["id"], update=True)
        if kind in TASK12_AUTOINCREMENT_KINDS
        else row[1]
        if new_source_rowid is _UNSET
        else _native_integer_primary_key(new_source_rowid, update=True)
    )
    physical = _key_physical(db, kind, current, identity) if replace else None
    if physical is None:
        physical = row[0]
    expression, payload = _payload(db, kind, current, replace)
    return db.execute(
        "update "
        + mode
        + "tasks set id=?,legacy_key=?,parent_id=?,payload_json="
        + expression
        + " where id=?",
        (physical, str(identity), _parent(db, kind, current), payload, row[0]),
    ).rowcount


def delete_task12(db, kind, source_rowid, *, where_sql="", parameters=()):
    _check(kind, {})
    _lock(db)
    guard = " and (" + where_sql + ")" if where_sql else ""
    return db.execute(
        "delete from tasks where "
        + _KIND_PREDICATE
        + " and cast(legacy_key as integer)=? and id in "
        "(select __physical_id from(select id as __physical_id,cast(legacy_key as integer) as rowid,"
        + ",".join(
            task20_value(kind, f) + ' as "' + f + '"' for f in TASK20_FIELDS[kind]
        )
        + " from tasks where "
        + _KIND_PREDICATE
        + ") where 1"
        + guard
        + ")",
        (kind, source_rowid, kind, *parameters),
    ).rowcount
