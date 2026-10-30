"""Email projections, declarations, and native writes."""

import sqlite3
from contextlib import closing
from functools import lru_cache

from app import action_storage as actions
from app.action_email_storage import (
    _sequence, _set_sequence,
)
from app.action_task_delivery_storage import _hash, _quote, _source_declaration
from app.email_canonical_definitions import (
    MESSAGE_FIELDS, SHARED_FIELDS, canonical_column,
    MESSAGE_METADATA_FIELDS, email_metadata_declaration,
)

from app.current_email_definitions import CLASS_FIELDS, email_projection

ACTION_OPTIONS = dict(include_receipts=True, include_reply=True, include_email=True,
                      include_followup_sync=True, include_skill_config_receipts=True,
                      include_business_sync=True, include_task_delivery=True,
                      include_errors=True, include_feedback=True,
                      include_business_actions=True, include_email_history=True)
KINDS = ("email_classifications", "email_messages")
INT64_MAX = 2**63 - 1
_UNSET = object()
CLASS_DEFAULTS = {
    "sender": "''", "subject": "''", "preview": "''", "model_text": "''",
    "received_at": "''", "agent_result_json": "'null'",
    "confirmed_at": "''",
    "created_at": "current_timestamp", "updated_at": "current_timestamp",
}


def email_classification_projection() -> str:
    return email_projection(KINDS[0])


def email_message_projection() -> str:
    return email_projection(KINDS[1])


def _action_objects(variant: str, *, target: bool, inline_legacy_owner: bool = False,
                    physical_provenance: bool = False, compact_payload: bool = False):
    objects = actions._current_objects(
        reply_variant=variant, inline_legacy_owner=inline_legacy_owner,
        physical_provenance=physical_provenance, compact_payload=compact_payload, **ACTION_OPTIONS
    )
    if not target:
        return objects
    return [({**obj, "sql": obj["sql"].replace(
        "references email_classifications(id)", "references emails(classification_id)"
    )} if obj["type"] == "table" else obj) for obj in objects]


@lru_cache(maxsize=2)
def _target_hash(*, metadata_owner: bool = False):
    with closing(sqlite3.connect(":memory:")) as scratch:
        _create_emails(scratch, metadata_owner=metadata_owner)
        return _hash(_source_declaration(scratch, "emails"))


def _create_emails(db, *, metadata_owner: bool = False, action_owner: bool = False):
    table, indexes, triggers = email_metadata_declaration(
        metadata_owner=metadata_owner, action_owner=action_owner,
    )
    db.execute(table)
    for sql in indexes:
        db.execute(sql)
    for sql in triggers:
        db.execute(sql)


def _physical_id(db, preferred):
    if db.execute("select 1 from emails where id=?", (preferred,)).fetchone() is None:
        return preferred
    maximum = db.execute("select max(id) from emails").fetchone()[0]
    if maximum == INT64_MAX:
        # Structural row IDs are private. Native random-at-MAX allocation is
        # kept for the public classification alias, but migration needs a
        # repeatable physical mapping from identical source rows.
        candidate = 1
        for (occupied,) in db.execute("select id from emails where id>=1 order by id"):
            if occupied > candidate:
                break
            candidate += 1
        return candidate
    return (maximum or 0) + 1



def _native_class_next(db):
    maximum = db.execute("select max(classification_id) from emails").fetchone()[0]
    if maximum == INT64_MAX:
        with sqlite3.connect(":memory:") as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany("insert into ids values(?)", db.execute(
                "select classification_id from emails where classification_id is not null"))
            return allocator.execute("insert into ids default values").lastrowid
    return (maximum or 0) + 1


def _native_message_next(db):
    water = _sequence(db, "email_messages") or 0
    maximum = db.execute("select max(message_id) from emails").fetchone()[0] or 0
    high = max(water, maximum)
    if high == INT64_MAX:
        if db.in_transaction:
            db.rollback()
        actions._raise_autoincrement_full()
    return high + 1


def _shared_with_native_affinity(values):
    """Bind shared source values through SQLite's TEXT/INTEGER affinities."""
    if not values:
        return {}
    columns = tuple(field for field in ("account_id", "stable_message_identity", "uidvalidity", "uid")
                    if field in values)
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute("create table locator(account_id text,stable_message_identity text,"
                        "uidvalidity integer,uid integer)")
        scratch.execute("insert into locator(" + ",".join(columns) + ") values("
                        + ",".join("?" for _ in columns) + ")",
                        tuple(values[field] for field in columns))
        row = scratch.execute("select " + ",".join(
            part for field in columns for part in (field, "typeof(" + field + ")"))
            + " from locator").fetchone()
    return {field: (row[index * 2], row[index * 2 + 1]) for index, field in enumerate(columns)}


def _write_email_message(db, values, *, mode):
    unknown = set(values) - set(MESSAGE_FIELDS)
    if unknown:
        raise ValueError("unsupported Email message fields")
    actions._email_lock(db)
    message_id = values.get("id")
    if message_id is None:
        message_id = _native_message_next(db)
    else:
        message_id = actions._native_integer_primary_key(message_id)
    mapped = {canonical_column(KINDS[1], field): value
              for field, value in values.items() if field != "id"}
    mapped = {"id": _physical_id(db, message_id), "message_id": message_id, **mapped}
    columns = tuple(mapped)
    sql = ("insert into emails("
           + ",".join(map(_quote, columns)) + ") values("
           + ",".join("?" for _ in columns) + ")")
    if mode == "upsert":
        updates = ("folder", "uidvalidity", "uid", "in_reply_to", "references_json",
                   "thread_identity", "updated_at")
        sql += " on conflict(stable_message_identity) do update set " + ",".join(
            _quote(canonical_column(KINDS[1], field)) + "=excluded."
            + _quote(canonical_column(KINDS[1], field)) for field in updates)
    db.execute(sql, tuple(mapped.values()))
    _set_sequence(db, "email_messages", max(_sequence(db, "email_messages") or 0, message_id))
    row = db.execute("select message_id from emails where stable_message_identity=?",
                     (values["stable_message_identity"],)).fetchone()
    return row[0] if row is not None else None


def insert_email_message(db: sqlite3.Connection, values: dict) -> int:
    """Insert a message-only row using the independent native AUTO stream."""
    return _write_email_message(db, values, mode="insert")


def upsert_email_message(db: sqlite3.Connection, values: dict) -> int:
    """Match the source's stable-identity conflict update and AUTO burn."""
    return _write_email_message(db, values, mode="upsert")


def insert_email_classification(db: sqlite3.Connection, values: dict) -> int:
    """Fill the optional classification projection on an existing message."""
    unknown = set(values) - set(CLASS_FIELDS)
    if unknown:
        raise ValueError("unsupported Email classification fields")
    actions._email_lock(db)
    classification_id = values.get("id")
    if classification_id is None:
        classification_id = _native_class_next(db)
    else:
        classification_id = actions._native_integer_primary_key(classification_id)
    stable = values["stable_message_identity"]
    target = db.execute("select account_id,uidvalidity,uid,classification_id from emails "
                        "where stable_message_identity=?", (stable,)).fetchone()
    if target is None:
        raise ValueError("classification has no canonical message")
    if target[3] is not None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed: emails.stable_message_identity")
    shared = _shared_with_native_affinity(values)
    target_types = db.execute("select typeof(account_id),typeof(uidvalidity),typeof(uid) "
                              "from emails where stable_message_identity=?", (stable,)).fetchone()
    if any(shared[field] != (target[index], target_types[index])
           for index, field in enumerate(("account_id", "uidvalidity", "uid"))):
        raise ValueError("classification locator differs from message")
    mapped = {canonical_column(KINDS[0], field, metadata_owner=True): value for field, value in values.items()
              if field != "id" and field not in SHARED_FIELDS}
    supplied = ",".join(_quote(col) + "=?" for col in ("classification_id", *mapped))
    defaults = ",".join(_quote(canonical_column(KINDS[0], field, metadata_owner=True)) + "=" + expression
                        for field, expression in CLASS_DEFAULTS.items()
                        if field not in values and field not in MESSAGE_METADATA_FIELDS)
    assignments = ",".join(part for part in (supplied, defaults) if part)
    db.execute("update emails set " + assignments + " where stable_message_identity=?",
               (classification_id, *mapped.values(), stable))
    return classification_id


def update_email_message(db: sqlite3.Connection, message_id: int, values: dict) -> int:
    """Apply source Message UPDATE columns to its exact public alias."""
    if not values:
        return 0
    if set(values) - set(MESSAGE_FIELDS):
        raise ValueError("unsupported Email message fields")
    mapped = {canonical_column(KINDS[1], field):
              actions._native_integer_primary_key(value, update=True) if field == "id" else value
              for field, value in values.items()}
    assignments = [_quote(column) + "=?" for column in mapped]
    reference_parameters = ()
    if 'normalized_text' in values:
        assignments.append("message_source_reference_json=case when message_normalized_text is ? "
            "then message_source_reference_json "
            "when json_type(message_source_reference_json,'$.model_input')='object' "
            "then json_object('model_input',json_extract(message_source_reference_json,'$.model_input')) "
            "else null end")
        reference_parameters = (values['normalized_text'],)
    cursor = db.execute("update emails set " + ",".join(assignments)
                        + " where message_id=?", (*mapped.values(), *reference_parameters, message_id))
    return cursor.rowcount


def update_email_classification(db: sqlite3.Connection, classification_id: int,
                                values: dict) -> int:
    """Apply classification fields, with metadata written to its message owner."""
    if not values:
        return 0
    if set(values) - set(CLASS_FIELDS):
        raise ValueError("unsupported Email classification fields")
    if SHARED_FIELDS & set(values):
        actions._email_lock(db)
    mapped = {canonical_column(KINDS[0], field, metadata_owner=True):
              actions._native_integer_primary_key(value, update=True) if field == "id" else value
              for field, value in values.items()}
    shared = _shared_with_native_affinity({field: values[field] for field in SHARED_FIELDS & set(values)})
    for field, converted in shared.items():
        current = db.execute("select " + _quote(field) + ",typeof(" + _quote(field)
                             + ") from emails where classification_id=?", (classification_id,)).fetchone()
        if current is not None and tuple(current) != converted:
            raise ValueError("classification shared fact differs from canonical message")
    assignments = [_quote(column) + "=?" for column in mapped]
    reference_parameters = ()
    if 'model_text' in values:
        assignments.append("message_source_reference_json=case when class_model_text is ? "
                           "then message_source_reference_json else "
                           "json_remove(message_source_reference_json,'$.model_input') end")
        reference_parameters = (values['model_text'],)
    cursor = db.execute("update emails set "
                        + ",".join(assignments)
                        + " where classification_id=?", (*mapped.values(), *reference_parameters, classification_id))
    return cursor.rowcount


def update_email_paired_locator(
    db: sqlite3.Connection, *, classification_id: int, account_id: str,
    stable_message_identity: str, class_folder: str, message_folder: str,
    uidvalidity: int, uid: int, class_thread_id=_UNSET,
    class_updated_at=_UNSET, message_updated_at=_UNSET,
    expected_class_folder=_UNSET, expected_message_folder=_UNSET,
    expected_uidvalidity=_UNSET, expected_uid=_UNSET,
) -> int:
    """Apply the existing class-then-message locator move in one canonical UPDATE.

    Optional old locator values are the caller's existing conditional WHERE
    values. A failed match returns zero for the caller's current conflict path.
    """
    if class_folder != message_folder or type(class_folder) is not type(message_folder):
        raise ValueError("paired Email folder differs from its message owner")
    assignments = ["message_folder=?", "uidvalidity=?", "uid=?"]
    bindings = [message_folder, uidvalidity, uid]
    for column, value in (("class_thread_id", class_thread_id),
                          ("class_updated_at", class_updated_at),
                          ("message_updated_at", message_updated_at)):
        if value is not _UNSET:
            assignments.append(_quote(column) + "=?")
            bindings.append(value)
    where = ["classification_id=?", "account_id=?", "stable_message_identity=?"]
    bindings.extend((classification_id, account_id, stable_message_identity))
    for column, value in (("message_folder", expected_class_folder),
                          ("message_folder", expected_message_folder),
                          ("uidvalidity", expected_uidvalidity), ("uid", expected_uid)):
        if value is not _UNSET:
            where.append(_quote(column) + "=?")
            bindings.append(value)
    cursor = db.execute("update emails set " + ",".join(assignments)
                        + " where " + " and ".join(where), bindings)
    return cursor.rowcount


def delete_email_classification(db: sqlite3.Connection, classification_id: int) -> int:
    """Remove only the optional classification projection."""
    columns = tuple(canonical_column(KINDS[0], field, metadata_owner=True) for field in CLASS_FIELDS
                    if field not in SHARED_FIELDS and field not in MESSAGE_METADATA_FIELDS)
    cursor = db.execute("update emails set "
                        + ",".join(_quote(column) + "=null" for column in columns)
                        + " where classification_id=?", (classification_id,))
    return cursor.rowcount


def delete_email_message(db: sqlite3.Connection, message_id: int) -> int:
    """Delete a message-only row; a paired row has no representable class-only state."""
    actions._email_lock(db)
    row = db.execute("select classification_id from emails where message_id=?", (message_id,)).fetchone()
    if row is not None and row[0] is not None:
        raise ValueError("paired Email must remove classification before message")
    cursor = db.execute("delete from emails where message_id=?", (message_id,))
    return cursor.rowcount
