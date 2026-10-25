"""Business Actions: native writers for the ten Business Action families."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass

from app import action_storage as actions
from app.action_business_definitions import (
    BUSINESS_ACTION_AUTO, BUSINESS_ACTION_FIELDS, BUSINESS_ACTION_KINDS,
    BUSINESS_ACTION_DEFAULTS, BUSINESS_ACTION_KEYS, business_action_column,
    business_action_projection,
)
from app.action_email_definitions import EMAIL_NUMERIC_SCOPE
from app.action_business_definitions import BUSINESS_NUMERIC_SCOPE
from app.action_email_storage import _sequence
from app.action_task_delivery_storage import _quote


SOURCE_OPTIONS = dict(
    include_receipts=True, include_reply=True, include_email=True,
    include_followup_sync=True, include_skill_config_receipts=True,
    include_business_sync=True, include_task_delivery=True,
    include_errors=True, include_feedback=True,
)


@dataclass(frozen=True)
class BusinessActionWriteResult:
    lastrowid: int | None
    rowcount: int


def _validate_fields(kind, values):
    if kind not in BUSINESS_ACTION_KINDS or set(values) - set(BUSINESS_ACTION_FIELDS[kind]):
        raise ValueError("unsupported Business action source fields")
    for value in values.values():
        actions._encoded_native_sqlite_value(value)


def _complete(db, kind, values):
    fields = BUSINESS_ACTION_FIELDS[kind]
    fields = fields[1:] if kind in BUSINESS_ACTION_AUTO else fields
    missing = [field for field in fields if field not in values
               and BUSINESS_ACTION_DEFAULTS[kind].get(field) != "current_timestamp"]
    result = dict(values)
    if missing:
        defaults = db.execute("select " + ",".join(
            BUSINESS_ACTION_DEFAULTS[kind].get(field, "null") for field in missing)).fetchone()
        result.update(zip(missing, defaults, strict=True))
    return result


def _next_source_rowid(db, kind):
    if kind not in BUSINESS_ACTION_KINDS:
        raise ValueError("unsupported Business action kind")
    # The kind is a fixed literal: a bound record_type cannot satisfy the partial index's
    # predicate, and without the index SQLite scans the kind through
    # idx_actions_email_public_key instead of seeking the maximum.
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from main.actions indexed by idx_actions_public_id "
        f"where record_type='{kind}' and " + BUSINESS_NUMERIC_SCOPE).fetchone()[0]
    if kind not in BUSINESS_ACTION_AUTO:
        if maximum != 2**63 - 1:
            return (maximum or 0) + 1
        with closing(sqlite3.connect(":memory:")) as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany("insert into ids(id) values(?)", db.execute(
                "select cast(legacy_key as integer) from main.actions where record_type=? and "
                + EMAIL_NUMERIC_SCOPE,
                (kind,)))
            return allocator.execute("insert into ids default values").lastrowid
    water = _sequence(db, "actions:" + kind) or 0
    high = max(water, maximum or 0)
    if high == 2**63 - 1:
        # SQLite's immutable BEFORE triggers on these two native tables turn
        # SQLITE_FULL into a statement failure; the other AUTO tables roll
        # back the caller transaction when their allocator is exhausted.
        if kind not in ("business_task_date_evidence", "business_task_signals"):
            db.rollback()
        actions._raise_autoincrement_full()
    return high + 1


def insert_business_action(db: sqlite3.Connection, kind: str, values: dict,
                           *, source_rowid=None, ignore=False,
                           on_conflict_key_do_nothing=False) -> BusinessActionWriteResult:
    """Apply one native-shaped INSERT; composite records keep their hidden ROWID."""
    _validate_fields(kind, values)
    if on_conflict_key_do_nothing and (kind not in BUSINESS_ACTION_KEYS or ignore):
        raise ValueError("unsupported Business action conflict clause")
    actions._email_lock(db)
    supplied = values.get("id") if kind in BUSINESS_ACTION_AUTO else source_rowid
    identity = (_next_source_rowid(db, kind) if actions._source_bound_null(supplied)
                else actions._native_integer_primary_key(supplied))
    complete = _complete(db, kind, values)
    if kind in BUSINESS_ACTION_AUTO:
        complete.pop("id", None)
    omitted_created_at = "created_at" not in values
    payload_sql = ("jsonb_set(?, " + repr(actions._action_path(kind, "created_at")) + ", current_timestamp)"
                   if omitted_created_at else "?")
    conflict = (" on conflict(" + ",".join(business_action_column(kind, field)
                 for field in BUSINESS_ACTION_KEYS[kind]) + f") where record_type='{kind}' do nothing"
                if on_conflict_key_do_nothing else "")
    cursor = db.execute("insert " + ("or ignore " if ignore else "")
                        + "into main.actions(_kind,legacy_key,payload_json) values(?,?," + payload_sql + ")"
                        + conflict,
                        (actions.ACTION_RECORD_TYPES.code(kind), str(identity), actions._action_payload(db, kind, complete)))
    return BusinessActionWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def copy_business_action_date_evidence(
    db: sqlite3.Connection, *, source_task_id: int, target_task_id: int,
) -> int:
    """Copy nonduplicate date facts with the source's single INSERT SELECT."""
    kind = "business_task_date_evidence"
    actions._email_lock(db)
    fields = (
        "date_type", "value_at", "raw_phrase", "source_signal_id",
        "actor_kind", "actor_user_id", "actor_name", "created_at",
    )
    equal = " and ".join(
        f"target.{business_action_column(kind, field)}="
        f"source.{business_action_column(kind, field)}" for field in fields
    )
    candidates = (
        "from main.actions source where source.record_type=? "
        f"and source.{business_action_column(kind, 'task_id')}=? "
        "and not exists (select 1 from main.actions target "
        "where target.record_type=? "
        f"and target.{business_action_column(kind, 'task_id')}=? and {equal})"
    )
    parameters = (kind, source_task_id, kind, target_task_id)
    count = db.execute("select count(*) " + candidates, parameters).fetchone()[0]
    if not count:
        return 0
    base = _next_source_rowid(db, kind) - 1
    if count > 2**63 - 1 - base:
        # Native AUTO raises SQLITE_FULL and ABORTs the whole INSERT SELECT.
        # Actions.id is an ordinary IPK, so its overflow would otherwise
        # surface as a CHECK failure after numeric promotion to REAL.
        actions._raise_autoincrement_full()
    cursor = db.execute(
        "insert into main.actions(_kind,legacy_key,payload_json) "
        "select ?,cast(?+row_number() over (order by cast(source.legacy_key as integer)) as text),"
        "jsonb_set(source.payload_json," + repr(actions._action_path(kind, "task_id")) + ",?) " + candidates
        + " order by cast(source.legacy_key as integer)",
        (actions.ACTION_RECORD_TYPES.code(kind), base, target_task_id, *parameters),
    )
    return cursor.rowcount


def upsert_business_action_anchor_link(
    db: sqlite3.Connection, *, task_id: int, anchor_id: int, status: str,
    active: int, evidence_signal_id: int, reason: str,
) -> int:
    """Use the native link key to update four fields without changing its identity."""
    kind = "business_task_anchor_links"
    actions._email_lock(db)
    identity = _next_source_rowid(db, kind)
    payload = actions._action_payload(db, kind, {
        "task_id": task_id, "anchor_id": anchor_id, "status": status,
        "active": active, "evidence_signal_id": evidence_signal_id,
        "reason": reason,
    })
    updates = ",".join(
        repr(actions._action_path(kind, field)) + ",json_extract(excluded.payload_json," + repr(actions._action_path(kind, field)) + ")"
        for field in ("status", "active", "evidence_signal_id", "reason")
    )
    task_column = business_action_column(kind, "task_id")
    anchor_column = business_action_column(kind, "anchor_id")
    db.execute(
        "insert into main.actions(_kind,legacy_key,payload_json) "
        "values(?,?,jsonb_set(?," + repr(actions._action_path(kind, "created_at")) + ",current_timestamp)) "
        f"on conflict({task_column},{anchor_column}) where record_type='{kind}' "
        f"do update set payload_json=jsonb_set(actions.payload_json,{updates})",
        (actions.ACTION_RECORD_TYPES.code(kind), str(identity), payload),
    )
    row = db.execute(
        f"select cast(legacy_key as integer) from main.actions where record_type=? "
        f"and {task_column}=? and {anchor_column}=?",
        (kind, task_id, anchor_id),
    ).fetchone()
    assert row is not None
    return int(row[0])


def update_business_action(db: sqlite3.Connection, kind: str, source_rowid: int,
                           values: dict, *, ignore=False) -> int:
    """Update source columns while retaining composite hidden ROWID."""
    _validate_fields(kind, values)
    if not values:
        raise ValueError("empty Business action update")
    actions._email_lock(db)
    projection = business_action_projection(kind, include_rowid=True, public_id_index=True)
    fields = BUSINESS_ACTION_FIELDS[kind]
    row = db.execute("select " + ",".join(_quote(field) for field in fields)
                     + " from (" + projection + ") where _source_rowid=?",
                     (source_rowid,)).fetchone()
    if row is None:
        return 0
    current = dict(zip(fields, row, strict=True))
    current.update(values)
    new_id = (actions._native_integer_primary_key(current.pop("id"), update=True)
              if kind in BUSINESS_ACTION_AUTO else source_rowid)
    # Naming legacy_key in SET makes SQLite prepare trg_actions_public_reference_update,
    # whose body names columns the native actions table lacks; only change it when the
    # public id really changes.
    renamed = str(new_id) != str(source_rowid)
    cursor = db.execute("update " + ("or ignore " if ignore else "")
                        + "main.actions set " + ("legacy_key=?," if renamed else "") + "payload_json=? "
                        "where record_type=? and legacy_key=?",
                        (*((str(new_id),) if renamed else ()),
                         actions._action_payload(db, kind, current), kind, str(source_rowid)))
    return cursor.rowcount


def delete_business_action(db: sqlite3.Connection, kind: str, source_rowid: int) -> int:
    """Delete the exact source row subject to existing FK and immutable triggers."""
    _validate_fields(kind, {})
    actions._email_lock(db)
    return db.execute("delete from main.actions where record_type=? and legacy_key=?",
                      (kind, str(source_rowid))).rowcount
