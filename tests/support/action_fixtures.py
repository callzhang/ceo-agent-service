"""Narrow test-only operations on the current Action records."""
import sqlite3
from app.action_task_delivery_definitions import task_delivery_projection
from app.action_storage import (
    _sync_fields, _task_delivery_fields, _email_lock, _email_unit,
    _EMAIL_NUMERIC_SCOPE, task_delivery_set_parts,
)

def delete_sync_fixture(db: sqlite3.Connection, kind: str, public_id) -> sqlite3.Cursor:
    _sync_fields(kind, {})

    def operation():
        _email_lock(db, (public_id,))
        return db.execute("delete from actions where record_type=? and " + _EMAIL_NUMERIC_SCOPE +
                          " and cast(legacy_key as integer)=?", (kind, public_id))

    return _email_unit(db, "action_sync_delete", operation)


def update_delivery_fixture(db: sqlite3.Connection, kind: str, public_id, values: dict, *,
                                where_sql="", parameters=()) -> sqlite3.Cursor:
    _task_delivery_fields(kind, values)

    def operation():
        _email_lock(db, (*values.values(), public_id, *parameters))
        predicate = "record_type=? and " + _EMAIL_NUMERIC_SCOPE + " and cast(legacy_key as integer)=?"
        identity_args = [kind, public_id]
        if where_sql:
            projection = task_delivery_projection(kind)
            predicate += (" and cast(legacy_key as integer) in "
                          f"(select {('job_id' if kind == 'meeting_alignment_delivery_claims' else 'id')} "
                          f"from ({projection}) where {('job_id' if kind == 'meeting_alignment_delivery_claims' else 'id')}=? "
                          f"and ({where_sql}))")
            identity_args.extend((public_id, *parameters))
        identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
        if identity in values and db.execute(
            "select 1 from actions where " + predicate + " limit 1", identity_args,
        ).fetchone() is None:
            return db.execute("update main.sqlite_sequence set seq=seq where 0")
        assignment, arguments = task_delivery_set_parts(kind, values)
        return db.execute("update actions set " + assignment + " where " + predicate,
                          (*arguments, *identity_args))

    return _email_unit(db, "action_task_delivery_update", operation)


def delete_delivery_fixture(db: sqlite3.Connection, kind: str, public_id) -> sqlite3.Cursor:
    _task_delivery_fields(kind, {})

    def operation():
        _email_lock(db, (public_id,))
        return db.execute("delete from actions where record_type=? and " + _EMAIL_NUMERIC_SCOPE +
                          " and cast(legacy_key as integer)=?", (kind, public_id))

    return _email_unit(db, "action_task_delivery_delete", operation)
