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


def test_task_action_binding_after_business_parent_migration():
    """The final action migration must bind to Business public IDs, not retired parents."""
    from test_business_schema_release import _seed_complete_source, _source_db
    from app.business_schema_release import migrate_business_schema

    def foreign_keys(db, table):
        return {(row[3], row[2], row[4])
                for row in db.execute(f"pragma foreign_key_list({table})")}

    for variant in ("actual", "fresh"):
        with _source_db(variant) as db:
            _seed_complete_source(db)
            db.execute("pragma foreign_keys=off")
            db.execute("begin")
            migrate_business_schema(db)

            for table, column in (
                ("business_task_events", "task_id"),
                ("business_task_follow_ups", "business_task_id"),
                ("business_task_todo_sync_outbox", "business_task_id"),
                ("business_task_dingtalk_links", "business_task_id"),
            ):
                assert (column, "business", "task_public_id") in foreign_keys(db, table)
                assert not any(parent == "business_tasks"
                               for _, parent, _ in foreign_keys(db, table))
            assert ("signal_id", "business_task_signals", "id") in foreign_keys(
                db, "business_task_events"
            )
            assert ("source_signal_id", "business_task_signals", "id") in foreign_keys(
                db, "business_task_follow_ups"
            )
            legacy = foreign_keys(db, "business_legacy_links")
            assert ("task_id", "business", "task_public_id") in legacy
            assert ("work_update_id", "work_updates", "id") in legacy
            db.rollback()


def test_post_business_child_declarations_match_frozen_actual_and_fresh_source():
    import json
    from pathlib import Path
    from test_business_schema_release import _seed_complete_source, _source_db
    from app.business_schema_release import migrate_business_schema
    from app.action_followup_sync_storage import _declaration

    fixture = (Path(__file__).parents[1] / "docs/superpowers/specs/"
               "2026-10-17-action-task-events-post-business-source.json")
    expected = json.loads(fixture.read_text())
    assert expected["source_variants"] == ["actual", "fresh"]
    names = (
        "business_task_events",
        "business_task_follow_ups",
        "business_task_todo_sync_outbox",
        "business_task_dingtalk_links",
        "business_legacy_links",
    )
    for variant in ("actual", "fresh"):
        with _source_db(variant) as db:
            _seed_complete_source(db)
            db.execute("pragma foreign_keys=off")
            db.execute("begin")
            migrate_business_schema(db)
            declarations = {name: _declaration(db, name) for name in names}
            assert declarations == expected["shared_declarations"]
            db.rollback()


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


def test_source_capture_accepts_exact_business_migrated_pair():
    import json
    from pathlib import Path
    from test_business_schema_release import _seed_complete_source, _source_db
    from test_followup_sync_source import CAPTURE, NAMES
    from app.action_storage import create_action_schema
    from app.business_schema_release import migrate_business_schema
    from app.action_followup_sync_storage import (
        SOURCE_TABLES, capture_followup_sync_source,
    )

    event_source = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                               "2026-10-17-action-task-events-source-extension.json").read_text())
    for variant, action_variant in (("actual", "actual_no_memory_fk"),
                                    ("fresh", "fresh_memory_fk")):
        snapshot = (CAPTURE["actual"] if variant == "actual"
                    else CAPTURE["fresh_production_constructor"]["snapshot"])
        with _source_db(variant) as db:
            db.execute("drop table work_updates")
            db.execute(snapshot["tables"]["work_updates"]["sql"])
            for index in reversed(snapshot["tables"]["work_updates"]["indexes"]):
                if index["sql"]:
                    db.execute(index["sql"])
            for name in NAMES:
                if db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone():
                    continue
                definition = snapshot["tables"][name]
                db.execute(definition["sql"])
                for index in reversed(definition["indexes"]):
                    if index["sql"]:
                        db.execute(index["sql"])
            for name in ("tasks", "runs", "email_classifications"):
                if not db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone():
                    db.execute(f"create table {name}(id integer primary key, record_type text)")
            create_action_schema(db, include_reply=True, reply_variant=action_variant,
                                 include_email=True)
            db.execute("insert into work_updates(id,project_id,source_type,source_ref,summary) "
                       "values(-31,1,'fixture','post-business-source','summary')")
            event = event_source["actual" if variant == "actual" else "fresh_candidate"]
            if not db.execute("select 1 from sqlite_schema where name='business_task_events'").fetchone():
                db.execute(event["sql"])
                for index in reversed(event["indexes"]):
                    if index["sql"]:
                        db.execute(index["sql"])
            _seed_complete_source(db)
            db.execute("insert into business_task_events(id,task_id,event_type,signal_id,"
                       "before_json,after_json,reason) values(-45,-11,'status_changed',-31,"
                       "'{}','{}','source event')")
            db.commit()
            baseline = capture_followup_sync_source(db)
            assert baseline["business_binding"] == "legacy"
            db.execute("pragma foreign_keys=off")
            db.execute("begin")
            migrate_business_schema(db)
            captured = capture_followup_sync_source(db)
            assert captured["business_binding"] == "typed"
            assert captured["source_variant"] == ("actual" if variant == "actual" else "fresh_candidate")
            assert captured["source_declaration_variant"] == f"after_business_{variant}"
            from app.action_followup_sync_storage import (
                migrate_followup_sync_schema, restore_followup_sync_schema,
            )
            inverse = migrate_followup_sync_schema(db, captured)
            action_fks = {(row[3], row[2], row[4])
                          for row in db.execute("pragma foreign_key_list(actions)")}
            assert ("sync_task_id", "business", "task_public_id") in action_fks
            assert ("sync_business_task_id", "business", "task_public_id") in action_fks
            assert ("sync_signal_id", "business_task_signals", "id") in action_fks
            assert db.execute("select sync_task_id,sync_signal_id from actions "
                              "where record_type='business_task_events' and cast(legacy_key as integer)=-45"
                              ).fetchone() == (-11, -31)
            current_write = _insert_historical_text_event(db, {
                "task_id": -11,
                "event_type": "status_changed",
                "signal_id": -31,
                "before_json": "{}",
                "after_json": '{"status":"done"}',
                "reason": "current typed action write",
            })
            assert current_write.rowcount == 1
            assert db.execute(
                "select sync_event_type,sync_task_id,sync_signal_id,sync_after_json,sync_reason "
                "from actions a where a.record_type='business_task_events' "
                "and cast(a.legacy_key as integer)=?", (current_write.lastrowid,),
            ).fetchone() == ("status_changed", -11, -31, '{"status":"done"}',
                             "current typed action write")
            assert not any(db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone()
                           for name in SOURCE_TABLES)
            restore_followup_sync_schema(db, inverse)
            restored = capture_followup_sync_source(db)
            assert restored["source_declaration_variant"] == f"after_business_{variant}"
            assert db.execute(
                "select task_id,signal_id,after_json,reason from business_task_events "
                "where id=?", (current_write.lastrowid,),
            ).fetchone() == (-11, -31, '{"status":"done"}', "current typed action write")
            db.rollback()
            assert capture_followup_sync_source(db) == baseline
