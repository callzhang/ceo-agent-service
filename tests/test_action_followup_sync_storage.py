from app.action_followup_sync_definitions import (
    SYNC_DEFAULTS,
    SYNC_FIELDS,
    SYNC_SOURCE_FKS,
    SYNC_TYPES,
    sync_action_projection,
)


def test_business_task_events_maps_exact_fields_and_parent_relations():
    assert SYNC_FIELDS["business_task_events"] == (
        "id",
        "task_id",
        "event_type",
        "signal_id",
        "before_json",
        "after_json",
        "reason",
        "created_at",
    )
    assert SYNC_TYPES["task_id"] == "integer"
    assert SYNC_TYPES["signal_id"] == "integer"
    assert SYNC_SOURCE_FKS["business_task_events"] == (
        ("task_id", "business_tasks"),
        ("signal_id", "business_task_signals"),
    )
    assert SYNC_DEFAULTS["actual"]["business_task_events"] == {
        "created_at": "current_timestamp",
    }
    assert SYNC_DEFAULTS["fresh_candidate"]["business_task_events"] == {
        "created_at": "current_timestamp",
    }
    assert sync_action_projection("business_task_events").endswith(
        "where a.record_type='business_task_events'"
    )


def test_opt_in_task_action_schema_targets_business_public_id():
    import sqlite3
    from contextlib import closing
    from app.action_storage import create_action_schema

    def foreign_keys(db):
        return {(row[3], row[2], row[4])
                for row in db.execute("pragma foreign_key_list(actions)")}

    with closing(sqlite3.connect(":memory:")) as old_schema:
        create_action_schema(old_schema, include_reply=True,
                             reply_variant="actual_no_memory_fk", include_email=True,
                             include_followup_sync=True)
        old_fks = foreign_keys(old_schema)
    assert ("sync_task_id", "business_tasks", "id") in old_fks

    with closing(sqlite3.connect(":memory:")) as typed_schema:
        create_action_schema(typed_schema, include_reply=True,
                             reply_variant="actual_no_memory_fk", include_email=True,
                             include_followup_sync=True, include_business_sync=True)
        typed_fks = foreign_keys(typed_schema)
    assert ("sync_task_id", "business", "task_public_id") in typed_fks
    assert ("sync_business_task_id", "business", "task_public_id") in typed_fks
    assert ("sync_signal_id", "business_task_signals", "id") in typed_fks
    assert ("sync_source_signal_id", "business_task_signals", "id") in typed_fks
    assert not any(parent == "business_tasks" for _, parent, _ in typed_fks)


def _insert_historical_text_event(db, values):
    """Write a new event at this test's frozen TEXT migration boundary."""
    from app import action_storage as storage

    kind = "business_task_events"
    storage._sync_fields(kind, values)

    def insert():
        storage._email_lock(db, tuple(values.values()))
        defaults = SYNC_DEFAULTS[storage._sync_variant(db)][kind]
        missing = tuple(field for field in SYNC_FIELDS[kind]
                        if field in defaults and field not in values)
        evaluated = dict(zip(missing, db.execute(
            "select " + ",".join(defaults[field] for field in missing)
        ).fetchone(), strict=True)) if missing else {}
        payload = {field: values[field] if field in values else evaluated.get(field)
                   for field in SYNC_FIELDS[kind] if field != "id"}
        public_id = storage._email_auto_next(db, kind)
        cursor = db.execute(
            "insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
            (kind, str(public_id), storage._native_payload(payload)),
        )
        return storage.ActionWriteResult(public_id, cursor.rowcount)

    return storage._email_unit(db, "historical_sync_event_insert", insert)


