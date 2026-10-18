"""Read-only source capture for the disabled follow-up/Todo migration."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app.action_storage import create_action_schema
from app import action_storage as action_storage
from app.action_email_definitions import EMAIL_AUTOINCREMENT_TYPES
from app.action_followup_sync_definitions import sync_action_projection
from test_followup_sync_source import values
from app.action_followup_sync_storage import (
    capture_followup_sync_source, migrate_followup_sync_schema, restore_followup_sync_schema,
)
from test_followup_sync_source import CAPTURE, NAMES

EVENT_SOURCE = json.loads((Path(__file__).parents[1] /
                           "docs/superpowers/specs/2026-10-17-action-task-events-source-extension.json").read_text())


@pytest.fixture(params=("actual_no_memory_fk", "fresh_memory_fk"))
def source(request, tmp_path):
    variant = request.param
    path = tmp_path / "source.sqlite"
    frozen = CAPTURE["actual"] if variant == "actual_no_memory_fk" else CAPTURE["fresh_production_constructor"]["snapshot"]
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=on")
        for name in ("tasks", "runs", "email_classifications", "business_tasks", "business_task_signals",
                     "business_work_clusters", "business_anchors", "business_projects", "business_project_candidates",
                     "business_attention_items", "work_projects", "work_todos"):
            db.execute(f"create table {name}(id integer primary key, record_type text)")
        create_action_schema(db, include_reply=True, reply_variant=variant, include_email=True)
        for name in NAMES:
            table = frozen["tables"][name]
            db.execute(table["sql"])
            for index in reversed(table["indexes"]):
                if index["sql"]:
                    db.execute(index["sql"])
        event = EVENT_SOURCE["actual"]
        db.execute(event["sql"])
        for index in reversed(event["indexes"]):
            if index["sql"]:
                db.execute(index["sql"])
        external = frozen["external_schema_references"][0]
        db.execute(external["sql"])
        for obj in frozen["related_table_objects"]["business_legacy_links"]["objects"]:
            if obj["type"] == "index" and obj["sql"]:
                db.execute(obj["sql"])
        db.commit()
    return path, variant


def _read(path, row_factory=None):
    uri = f"file:{path}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = row_factory
    db.execute("pragma query_only=on")
    db.execute("begin")
    return db


def test_capture_both_variants_and_native_watermarks(source):
    path, variant = source
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("insert into work_updates(project_id,source_type,source_ref,summary) values(1,'s','r','body')")
        writer.execute("delete from work_updates")
        writer.execute("update main.sqlite_sequence set seq=500 where name='work_updates'")
        writer.execute("insert into main.sqlite_sequence(name,seq) values('follow_up_drafts',0)")
        writer.commit()
    for factory in (None, sqlite3.Row):
        with closing(_read(path, factory)) as db:
            before = db.in_transaction, db.execute("pragma foreign_keys").fetchone()[0], db.execute("pragma query_only").fetchone()[0]
            captured = capture_followup_sync_source(db)
            assert captured["source_variant"] == ("actual" if variant == "actual_no_memory_fk" else "fresh_candidate")
            assert len(captured["source_tables"]) == 10
            assert sum(len(table["columns"]) for table in captured["source_tables"].values()) == 158
            assert captured["sequences"]["work_updates"] == 500
            assert captured["sequences"]["follow_up_drafts"] == 0
            assert captured["sequences"]["business_task_follow_ups"] is None
            assert captured["counts"]["work_updates"] == 0
            assert len(captured["external"]["business_legacy_links"]["columns"]) == 12
            assert (db.in_transaction, db.execute("pragma foreign_keys").fetchone()[0], db.execute("pragma query_only").fetchone()[0]) == before


@pytest.mark.parametrize("factory", (None, sqlite3.Row))
def test_forward_inverse_roundtrip_both_frozen_sources(source, factory):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = factory
        db.execute("pragma foreign_keys=off")
        db.execute("insert into work_updates(project_id,source_type,source_ref,summary) "
                   "values(1,'source','ref',?)", (b"raw\x00value",))
        db.commit()
        db.execute("begin")
        captured = capture_followup_sync_source(db)
        state = migrate_followup_sync_schema(db, captured)
        assert db.execute("select count(*) from actions where record_type='work_updates'").fetchone()[0] == 1
        assert db.execute("select 1 from sqlite_schema where name='work_updates'").fetchone() is None
        restore_followup_sync_schema(db, state)
        assert db.execute("select summary from work_updates").fetchone()[0] == b"raw\x00value"
        assert capture_followup_sync_source(db)["source_variant"] == captured["source_variant"]
        db.rollback()


def test_task_event_current_fact_and_receipt_survive_forward_inverse(source):
    path, variant = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("insert into business_tasks(id) values(11)")
        db.execute("insert into business_task_signals(id) values(12)")
        db.execute("insert into business_task_events"
                   "(id,task_id,event_type,signal_id,before_json,after_json,reason,created_at) "
                   "values(41,11,'status_changed',12,'{}','{\"status\":\"open\"}','source event','2026-10-16 12:00:00')")
        db.commit()
        db.execute("begin")
        captured = capture_followup_sync_source(db)
        state = migrate_followup_sync_schema(db, captured)
        source_variant = "actual" if variant == "actual_no_memory_fk" else "fresh_candidate"
        projected = db.execute(sync_action_projection("business_task_events", source_variant=source_variant)).fetchone()
        assert projected == (41, 11, "status_changed", 12, "{}", '{"status":"open"}',
                             "source event", "2026-10-16 12:00:00")
        restore_followup_sync_schema(db, state)
        assert db.execute("select * from business_task_events").fetchone() == projected
        assert db.execute("select seq from sqlite_sequence where name='business_task_events'").fetchone() == (41,)
        db.rollback()


def test_main_binding_survives_temp_shadows(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("create temp table actions(id integer)")
        db.execute("create temp table work_updates(id integer)")
        db.execute("create temp table business_legacy_links(id integer)")
        db.execute("insert into temp.actions values(99)")
        db.execute("insert into temp.work_updates values(98)")
        db.execute("insert into temp.business_legacy_links values(97)")
        db.commit()
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        restore_followup_sync_schema(db, state)
        assert db.execute("select id from temp.actions").fetchone() == (99,)
        assert db.execute("select id from temp.work_updates").fetchone() == (98,)
        assert db.execute("select id from temp.business_legacy_links").fetchone() == (97,)
        db.rollback()


def test_predecessor_allocator_absent_zero_high_and_deleted_max(source):
    path, _ = source
    names = ("actions", *action_storage.ACTION_SEQUENCE_NAMES.values(),
             action_storage.DELIVERY_SEQUENCE_NAME, *action_storage.REPLY_SEQUENCE_NAMES.values(),
             *("actions:" + kind for kind in EMAIL_AUTOINCREMENT_TYPES))
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        for name in names:
            db.execute("delete from main.sqlite_sequence where name=?", (name,))
        db.execute("insert into main.sqlite_sequence(name,seq) values(?,0)", (names[1],))
        db.execute("insert into main.sqlite_sequence(name,seq) values(?,900)", (names[2],))
        db.commit()
        before = {name: db.execute("select seq from main.sqlite_sequence where name=?", (name,)).fetchone()
                  for name in names}
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        after = {name: db.execute("select seq from main.sqlite_sequence where name=?", (name,)).fetchone()
                 for name in names}
        assert after == before
        restore_followup_sync_schema(db, state)
        after = {name: db.execute("select seq from main.sqlite_sequence where name=?", (name,)).fetchone()
                 for name in names}
        assert after == before
        db.rollback()


def test_inverse_refuses_reserved_name_dependency_on_unrelated_owner(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        db.execute("create trigger trg_email_direct_action_blocks_account_delete "
                   "after insert on work_todos begin select id from actions; end")
        before = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        with pytest.raises(ValueError, match="unsupported"):
            restore_followup_sync_schema(db, state)
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before
        db.rollback()


def test_legacy_link_ignore_check_precedes_parent_validation(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=on")
        source_cursor = db.execute("insert or ignore into main.business_legacy_links "
                                   "(signal_id,task_id,work_update_id) values(11,12,999)")
        assert source_cursor.rowcount == 0
        db.commit()
        with closing(sqlite3.connect(":memory:")) as native:
            db.backup(native)
            native.execute("pragma foreign_keys=on")
            assert native.execute("insert or ignore into main.business_legacy_links "
                                  "(signal_id,task_id,work_update_id) values(11,12,999)").rowcount == 0
            source_water = native.execute("select seq from main.sqlite_sequence where name='business_legacy_links'").fetchone()
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        db.commit()
        db.execute("pragma foreign_keys=on")
        target_cursor = db.execute("insert or ignore into main.business_legacy_links "
                                   "(signal_id,task_id,work_update_id) values(11,12,999)")
        assert target_cursor.rowcount == 0
        assert db.execute("select seq from main.sqlite_sequence where name='business_legacy_links'").fetchone() == source_water
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into main.business_legacy_links(task_id,work_update_id) values(11,999)")
        other = action_storage.insert_sync_action(db, "work_todo_dingtalk_links",
                                                  values("work_todo_dingtalk_links"))
        wrong_kind_id = db.execute("select id from main.actions where record_type='work_todo_dingtalk_links' "
                                   "and legacy_key=?", (str(other.lastrowid),)).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="work update parent type mismatch"):
            db.execute("insert into main.business_legacy_links(task_id,work_update_id) values(11,?)",
                       (wrong_kind_id,))


@pytest.mark.parametrize("stage", ("copy", "rebuild", "drop"))
def test_forward_failure_rolls_back_in_caller_transaction(source, stage):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("insert into main.work_updates(project_id,source_type,source_ref,summary) "
                   "values(1,'s','r','saved')")
        db.commit()
        before_schema = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        before_rows = db.execute("select * from main.work_updates").fetchall()
        before_water = db.execute("select name,seq from main.sqlite_sequence order by name").fetchall()
        db.execute("begin")
        captured = capture_followup_sync_source(db)
        denied = False

        def authorize(operation, name, _column, _database, _trigger):
            nonlocal denied
            if not denied and ((stage == "copy" and operation == sqlite3.SQLITE_INSERT and name == "actions") or
                               (stage == "rebuild" and operation == sqlite3.SQLITE_CREATE_TABLE and name == "business_legacy_links") or
                               (stage == "drop" and operation == sqlite3.SQLITE_DROP_TABLE and name == "work_updates")):
                denied = True
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                migrate_followup_sync_schema(db, captured)
        finally:
            db.set_authorizer(None)
        assert denied and db.in_transaction
        db.rollback()
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before_schema
        assert db.execute("select * from main.work_updates").fetchall() == before_rows
        assert db.execute("select name,seq from main.sqlite_sequence order by name").fetchall() == before_water


@pytest.mark.parametrize("mutation", (
    "alter table work_updates add column surprise text",
    "create index extra_work_update on work_updates(summary)",
    "create view extra_view as select * from work_updates",
    "create table extra_child(id integer references work_updates(id))",
    "create trigger extra_trigger after insert on work_updates begin select 1; end",
    "create table idx_actions_sync_business_task_dingtalk_links_0(id integer)",
))
def test_unsupported_main_source_refuses_without_writes(source, mutation):
    path, _ = source
    with closing(sqlite3.connect(path)) as writer:
        writer.execute(mutation)
        writer.commit()
    with closing(_read(path)) as db:
        before = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        with pytest.raises(ValueError, match="unsupported"):
            capture_followup_sync_source(db)
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before


def test_temp_shadow_is_preserved_but_temp_dependency_refuses(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("create temp table work_updates(id integer)")
        db.execute("insert into temp.work_updates values(71)")
        db.commit()
        db.execute("pragma query_only=on")
        db.execute("begin")
        assert capture_followup_sync_source(db)["counts"]["work_updates"] == 0
        assert db.execute("select * from temp.work_updates").fetchall() == [(71,)]
        db.execute("pragma query_only=off")
        db.execute("create temp view dependent as select * from main.work_updates")
        db.execute("pragma query_only=on")
        with pytest.raises(ValueError, match="unsupported"):
            capture_followup_sync_source(db)


def test_historical_main_owner_temp_trigger_survives_shadow_and_is_refused(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("create temp trigger dependent after insert on main.work_updates begin select 1; end")
        db.execute("create temp table work_updates(id integer)")
        db.commit()
        db.execute("pragma query_only=on")
        db.execute("begin")
        before = db.execute("select type,name,sql from temp.sqlite_schema order by type,name").fetchall()
        with pytest.raises(ValueError, match="unsupported"):
            capture_followup_sync_source(db)
        assert db.execute("select type,name,sql from temp.sqlite_schema order by type,name").fetchall() == before


def test_unknown_action_type_and_allocator_alias_refuse(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma ignore_check_constraints=on")
        db.execute("insert into actions(record_type,legacy_key,payload_json) values('unknown', '1', '{}')")
        db.execute("pragma ignore_check_constraints=off")
        db.commit()
    with closing(_read(path)) as db:
        with pytest.raises(ValueError, match="unsupported mixed actions"):
            capture_followup_sync_source(db)
    with closing(sqlite3.connect(path)) as db:
        db.execute("delete from actions where record_type='unknown'")
        db.execute("insert into main.sqlite_sequence(name,seq) values('actions:unknown',7)")
        db.commit()
    with closing(_read(path)) as db:
        with pytest.raises(ValueError, match="unsupported source allocator alias"):
            capture_followup_sync_source(db)


@pytest.mark.parametrize("referenced", ("actions", "business_legacy_links", "work_updates"))
def test_email_trigger_name_cannot_hide_followup_dependency(source, referenced):
    path, _ = source
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("create trigger trg_email_direct_action_blocks_account_delete "
                       f"after insert on work_todos begin select id from {referenced}; end")
        writer.commit()
    with closing(_read(path)) as db:
        before = db.execute("select sql from main.sqlite_schema where type='trigger'").fetchall()
        with pytest.raises(ValueError, match="unsupported"):
            capture_followup_sync_source(db)
        assert db.execute("select sql from main.sqlite_schema where type='trigger'").fetchall() == before
