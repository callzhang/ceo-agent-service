"""Test-only DML for historical TEXT Actions migration boundaries.

The historical target fixture deliberately keeps the original TEXT
``record_type`` and object-shaped ``payload_json`` table. Runtime callers and
final-schema tests must not install this adapter.
"""

from __future__ import annotations

import json

from app import action_storage as storage


def _insert_text_action(db, record_type, values, *, ignore=False, selection_references=False):
    """Apply the generic non-Reply writer to the historical TEXT layout."""
    assert not selection_references
    storage._check_fields(record_type, values)
    if record_type in storage.REPLY_ACTION_TYPES or record_type not in storage.ALL_ACTION_TYPES_WITH_DELIVERY:
        raise ValueError("historical TEXT fixture only supports non-Reply delivery Actions")

    now = db.execute("select current_timestamp").fetchone()[0]
    payload = storage.DEFAULTS[record_type] | {
        field: now for field in ("created_at", "updated_at", "prepared_at", "sent_at")
        if field in storage.SOURCE_COLUMNS[record_type]
    }
    payload.update({field: value for field, value in values.items()
                    if field in storage.PAYLOAD_FIELDS[record_type]})
    fields = [field for field in (storage.REFERENCES | storage.DELIVERY_REFERENCES)
              if field in storage._kind_references(record_type)]
    arguments = [record_type, storage._native_payload(payload)]
    arguments += [storage._parent_id(db, field, values.get(field)) for field in fields]

    public_id = (
        tuple(values.get(field) for field in storage.POSTFIX_IDENTITY_FIELDS)
        if record_type in storage.POSTFIX_ACTION_TYPES else
        (values.get("sent_reply_id"), values.get("agent_run_id"))
        if record_type == "sent_reply_observers" else
        values.get(storage.IDENTITY_FIELDS[record_type])
    )
    if record_type == "external_action_results" and public_id is None:
        raise ValueError("unrepresentable null external action key")
    if public_id is None and (record_type in storage.AUTOINCREMENT_TYPES
                              or record_type == "sent_replies"):
        maximum = storage._autoincrement_high_water(db, record_type)
        if maximum == 2**63 - 1:
            if record_type == "sent_replies" and db.in_transaction:
                db.execute("rollback")
            storage._raise_autoincrement_full()
        public_id = maximum + 1
    if public_id is None and db.execute(
        "select 1 from actions where record_type=? and legacy_key='9223372036854775807'",
        (record_type,),
    ).fetchone():
        # Retain SQLite's native random-rowid fallback at the integer limit.
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(":memory:")) as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany(
                "insert into ids values(?)",
                db.execute("select cast(legacy_key as integer) from actions where record_type=?",
                           (record_type,)),
            )
            public_id = allocator.execute("insert into ids default values").lastrowid

    if public_id is None:
        identity = "cast(coalesce((select max(cast(legacy_key as integer))+1 " \
                   "from actions where record_type=?),1) as text)"
        arguments.append(record_type)
    else:
        identity = "?"
        arguments.append(
            json.dumps(public_id, ensure_ascii=False, separators=(",", ":"))
            if record_type in storage.POSTFIX_ACTION_TYPES or record_type == "sent_reply_observers"
            else str(public_id)
        )

    returning = (
        "channel,delivery_key" if record_type in storage.POSTFIX_ACTION_TYPES else
        "legacy_key" if record_type in ("sent_reply_observers", "external_action_results") else
        "cast(legacy_key as integer)"
    )
    columns = ("record_type", "payload_json", *fields, "legacy_key")
    sql = (f"insert {'or ignore ' if ignore else ''}into actions({','.join(columns)}) "
           f"values({','.join('?' for _ in range(2 + len(fields)))},{identity}) "
           f"returning {returning}")
    cursor = db.execute(sql, arguments)
    result = cursor.fetchone()
    cursor.fetchall()
    identity_value = (
        tuple(result) if record_type in storage.POSTFIX_ACTION_TYPES else
        tuple(json.loads(result[0])) if record_type == "sent_reply_observers" else
        result[0]
    ) if result else None
    return storage.ActionWriteResult(identity_value, cursor.rowcount)


def _update_text_action(db, record_type, public_id, values, *, where_sql="", parameters=()):
    """Apply generic explicit updates against TEXT identity and JSON paths."""
    storage._check_fields(record_type, values)
    if record_type in storage.REPLY_ACTION_TYPES or record_type not in storage.ALL_ACTION_TYPES_WITH_DELIVERY:
        raise ValueError("historical TEXT fixture only supports non-Reply delivery Actions")
    assignments, arguments, paths = [], [], []
    identity_arguments = storage._identity_arguments(record_type, public_id)
    if record_type == "sent_reply_observers" and ("sent_reply_id" in values or "agent_run_id" in values):
        new_key = (values.get("sent_reply_id", public_id[0]), values.get("agent_run_id", public_id[1]))
        assignments.append("legacy_key=?")
        arguments.append(json.dumps(new_key, separators=(",", ":")))
    if record_type in storage.POSTFIX_ACTION_TYPES and any(
        field in values for field in storage.POSTFIX_IDENTITY_FIELDS
    ):
        new_key = tuple(values.get(field, public_id[index])
                        for index, field in enumerate(storage.POSTFIX_IDENTITY_FIELDS))
        assignments.append("legacy_key=?")
        arguments.append(json.dumps(new_key, ensure_ascii=False, separators=(",", ":")))
    for field, value in values.items():
        if record_type in storage.POSTFIX_ACTION_TYPES and field in storage.POSTFIX_IDENTITY_FIELDS:
            continue
        if field in storage._kind_references(record_type) or field == storage.IDENTITY_FIELDS.get(record_type):
            is_identity = field == storage.IDENTITY_FIELDS.get(record_type)
            assignments.append(("legacy_key" if is_identity else field) + "=?")
            arguments.append(str(value) if is_identity and value is not None else
                             value if is_identity else storage._parent_id(db, field, value))
        else:
            paths.extend(("$." + field, json.dumps(
                storage._encoded_native_sqlite_value(value), ensure_ascii=False,
                allow_nan=False, separators=(",", ":"),
            )))
    if paths:
        assignments.append("payload_json=json_set(payload_json," + ",".join("?,json(?)" for _ in range(len(paths) // 2)) + ")")
        arguments.extend(paths)
    if not assignments:
        raise ValueError("action update requires fields")
    predicate = storage._identity_predicate(record_type)
    arguments.extend(identity_arguments)
    if where_sql:
        if record_type in storage.POSTFIX_ACTION_TYPES:
            predicate += (f" and (channel,delivery_key) in (select channel,delivery_key "
                          f"from ({storage.ACTION_PROJECTIONS[record_type]}) "
                          "where channel=? and delivery_key=? and (" + where_sql + "))")
            arguments.extend((*public_id, *parameters))
        elif record_type == "sent_reply_observers":
            predicate += (f" and exists (select 1 from ({storage.ACTION_PROJECTIONS[record_type]}) "
                          "where sent_reply_id=? and agent_run_id=? and (" + where_sql + "))")
            arguments.extend((*public_id, *parameters))
        else:
            identity_field = storage.IDENTITY_FIELDS[record_type]
            identity_expression = "legacy_key" if record_type == "external_action_results" else "cast(legacy_key as integer)"
            predicate += (f" and {identity_expression} in (select {identity_field} "
                          f"from ({storage.ACTION_PROJECTIONS[record_type]}) "
                          f"where {identity_field}=? and ({where_sql}))")
            arguments.extend((public_id, *parameters))
    return db.execute(f"update actions set {','.join(assignments)} where {predicate}", arguments)


def install(monkeypatch):
    """Scope historical DML and projections to the requesting test fixture."""
    projections = {
        kind: storage._projection(kind)
        for kind in storage.ALL_ACTION_TYPES_WITH_DELIVERY
    }
    monkeypatch.setattr(storage, "_insert_action", _insert_text_action)
    monkeypatch.setattr(storage, "update_action", _update_text_action)
    monkeypatch.setattr(storage, "ACTION_PROJECTIONS", projections)
    # This module imports the mapping by value; keep its historical boundary
    # queries aligned with the fixture while normal runtime imports stay intact.
    from app import action_delivery_schema_release
    monkeypatch.setattr(action_delivery_schema_release, "ACTION_PROJECTIONS", projections)
