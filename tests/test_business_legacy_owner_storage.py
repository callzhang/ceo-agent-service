"""Synthetic source/target parity for the unselected WorkTodo import owner."""

from copy import deepcopy
from functools import lru_cache
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.action_reply_storage import _source_declaration
from app.business_storage import insert_business
from app.action_feedback_storage import insert_feedback_action
from app import store as store_module
from app.store import AutoReplyStore
from app.action_storage import _current_objects
from app.business_definitions import BUSINESS_COMMON, BUSINESS_FRESH_FIELDS, _definitions
from app.business_legacy_owner_storage import (
    capture_legacy_owner_source, migrate_legacy_owner, restore_legacy_owner,
    read_import_task, assign_import_task,
)


@lru_cache(maxsize=1)
def _current12_bytes():
    with TemporaryDirectory(prefix="legacy-owner-current12-") as directory:
        path = Path(directory) / "source.sqlite"
        AutoReplyStore(path)
        # The Store deliberately pools its thread-local WAL connection. This
        # synthetic historical-source fixture must close it before switching
        # SQLite out of WAL mode.
        store_module._close_idle_connections()
        with sqlite3.connect(path) as source:
            source.execute("pragma journal_mode=delete")
            # Rebuild the supported source declaration. The current constructor
            # is still at the older 129-column release, while the exact source
            # accepted by this component is the current 136-column declaration.
            source.execute("pragma foreign_keys=off")
            source.execute("drop table business")
            business_table, business_indexes, business_triggers = _definitions(
                include_candidates=True, include_business_actions=True,
            )
            for sql in (business_table, *business_indexes, *business_triggers):
                source.execute(sql)
            frozen = json.loads((Path(__file__).parents[1] /
                "docs/superpowers/specs/2026-10-17-email-canonical-current-copy.json").read_text())
            objects = frozen["post_target_remaining_business_declarations"]["business_legacy_links"]["objects"]
            for kind in ("table", "index", "trigger"):
                for obj_kind, name, _owner, sql in objects:
                    if obj_kind == kind and sql:
                        source.execute(sql)
            guard = next(obj["sql"] for obj in _current_objects(
                include_reply=True, reply_variant="fresh_memory_fk", include_email=True,
                include_skill_config_receipts=True, include_followup_sync=True,
                include_business_sync=True, include_task_delivery=True, include_errors=True,
                include_feedback=True, include_business_actions=True, include_email_history=True,
            ) if obj["name"] == "trg_sync_legacy_update_public_id")
            source.execute(guard)
            return source.serialize()


def source_db():
    db = sqlite3.connect(":memory:")
    db.deserialize(_current12_bytes())
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    insert_feedback_action(db, "feedback_events", {
        "key": "legacy-owner:feedback:1", "feedback_token": "feedback:1", "rating": "positive",
    })
    insert_business(db, "task", {"id": 31, "title": "task", "stage": "candidate"})
    insert_business(db, "task", {"id": 32, "title": "task", "stage": "candidate"})
    project = {
        "id": 7, "title": "project", "category": "other", "tags_json": "[]",
        "status": "active", "priority": "none", "risk_level": "none",
        "needs_derek_attention": 0, "owner_user_id": "", "owner_name": "",
        "owner_evidence_json": "{}", "related_people_json": "[]", "goal": "",
        "background": "", "facts_json": "[]", "current_state": "", "blocker": "",
        "next_step": "", "next_follow_up_at": "", "follow_up_mode": "none",
        "source_conversations_json": "[]", "created_at": "", "updated_at": "",
        "last_activity_at": "",
    }
    _insert_current_business(db, "work_project", project)
    for todo_id in (11, 12):
        todo = {
            "id": todo_id, "project_id": 7, "title": "todo", "description": "",
            "owner_user_id": "", "owner_name": "", "owner_evidence_json": "{}",
            "status": "open", "priority": "none", "deadline_at": "",
            "next_follow_up_at": "", "follow_up_question": "", "blocker": "",
            "completion_evidence_json": "{}", "created_from_update_id": 0,
            "created_at": "", "updated_at": "", "completed_at": "",
        }
        _insert_current_business(db, "work_todo", todo)
    db.commit()
    db.execute("begin")
    return db


def _insert_current_business(db, kind, values):
    payload = {key: values[key] for key in BUSINESS_FRESH_FIELDS[kind]
               if key != "id" and key not in BUSINESS_COMMON}
    db.execute(
        "insert into business(record_type,legacy_key,title,status,created_at,updated_at,payload_json) "
        "values(?,?,?,?,?,?,?)",
        (kind, str(values["id"]), values.get("title", ""), values.get("status", ""),
         values.get("created_at", ""), values.get("updated_at", ""), json.dumps(payload)),
    )


def test_empty_source_exact_inverse_and_new_mapping_roundtrip():
    with source_db() as db:
        original = _source_declaration(db, "business")
        old_links = _source_declaration(db, "business_legacy_links")
        original_rows = db.execute("select id,record_type,legacy_key,payload_json from business order by id").fetchall()
        assert db.execute("select seq from sqlite_sequence where name='business_legacy_links'").fetchone() is None
        metadata = capture_legacy_owner_source(db)
        assert "rows" not in repr(metadata)
        migrate_legacy_owner(db, metadata)
        assert read_import_task(db, 11) is None
        with pytest.raises(ValueError, match="missing formal Task"):
            assign_import_task(db, 11, 999)
        assert assign_import_task(db, 11, 31) == "created"
        assert assign_import_task(db, 11, 31) == "skipped"
        with pytest.raises(ValueError, match="conflict"):
            assign_import_task(db, 11, 32)
        assert assign_import_task(db, 12, 31) == "created"
        assert db.execute("select id,record_type,legacy_key,payload_json from business order by id").fetchall() == original_rows
        assert db.execute("pragma foreign_key_check").fetchall() == []
        restore_legacy_owner(db, metadata)
        assert _source_declaration(db, "business") == original
        assert _source_declaration(db, "business_legacy_links") == old_links
        assert db.execute("select task_id,work_todo_id from business_legacy_links order by work_todo_id").fetchall() == [(31, 11), (31, 12)]
        assert db.execute("select task_id from business_legacy_links where work_todo_id=11").fetchone() == (31,)
        metadata2 = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata2)
        assert read_import_task(db, 11) == 31


def test_empty_source_preserves_original_absent_allocator_and_unrelated_objects():
    with source_db() as db:
        columns = [row[1] for row in db.execute("pragma table_xinfo(business)") if row[6] == 0]
        values_sql = ",".join('"' + column + '"' for column in columns)
        types_sql = ",".join('typeof("' + column + '")' for column in columns)
        before_values = db.execute("select " + values_sql + " from business order by id").fetchall()
        before_types = db.execute("select " + types_sql + " from business order by id").fetchall()
        action_columns = [row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0]
        action_values_sql = ",".join('"' + column + '"' for column in action_columns)
        action_types_sql = ",".join('typeof("' + column + '")' for column in action_columns)
        before_action_values = db.execute("select " + action_values_sql + " from actions order by id").fetchall()
        before_action_types = db.execute("select " + action_types_sql + " from actions order by id").fetchall()
        assert before_action_values
        before_waters = db.execute("select name,seq from sqlite_sequence order by name").fetchall()
        before = tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema "
                                  "where tbl_name not in ('business','business_legacy_links') "
                                  "and name<>'trg_sync_legacy_update_public_id' order by type,name"))
        business_rows = db.execute("select id,record_type,legacy_key,payload_json from business order by id").fetchall()
        metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata)
        assert db.execute("select " + action_values_sql + " from actions order by id").fetchall() == before_action_values
        assert db.execute("select " + action_types_sql + " from actions order by id").fetchall() == before_action_types
        assert db.execute("select " + values_sql + " from business order by id").fetchall() == before_values
        assert db.execute("select " + types_sql + " from business order by id").fetchall() == before_types
        restore_legacy_owner(db, metadata)
        assert db.execute("select count(*) from business_legacy_links").fetchone() == (0,)
        assert db.execute("select seq from sqlite_sequence where name='business_legacy_links'").fetchone() is None
        assert db.execute("select id,record_type,legacy_key,payload_json from business order by id").fetchall() == business_rows
        assert db.execute("select " + values_sql + " from business order by id").fetchall() == before_values
        assert db.execute("select " + types_sql + " from business order by id").fetchall() == before_types
        assert db.execute("select name,seq from sqlite_sequence order by name").fetchall() == before_waters
        assert db.execute("select " + action_values_sql + " from actions order by id").fetchall() == before_action_values
        assert db.execute("select " + action_types_sql + " from actions order by id").fetchall() == before_action_types
        assert tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema "
                                "where tbl_name not in ('business','business_legacy_links') "
                                "and name<>'trg_sync_legacy_update_public_id' order by type,name")) == before


def test_kind_target_and_retired_source_owner_restriction():
    with source_db() as db:
        db.execute("pragma foreign_keys=off")
        db.rollback()
        db.execute("pragma foreign_keys=on")
        db.execute("insert into business_legacy_links(task_id,work_todo_id) values(31,11)")
        db.execute("savepoint task_delete")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("delete from business where record_type='task' and public_id=31")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update business set legacy_key='41' where record_type='task' and public_id=31")
        db.execute("rollback to task_delete")
        db.execute("release task_delete")
        db.execute("savepoint owner_delete")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("delete from business where record_type='work_todo' and public_id=11")
        db.execute("rollback to owner_delete")
        db.execute("release owner_delete")
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata)
        db.commit()
        db.execute("pragma foreign_keys=on")
        assert read_import_task(db, 11) == 31
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update business set import_task_id=999 where record_type='work_todo' and public_id=11")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update business set import_task_id=31 where record_type='task' and public_id=32")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("delete from business where record_type='task' and public_id=31")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update business set legacy_key='41' where record_type='task' and public_id=31")
        assert db.execute("delete from business where record_type='work_todo' and public_id=11").rowcount == 1
        assert db.execute("select public_id from business where record_type='task' and public_id=31").fetchone() == (31,)


def test_supported_populated_mapping_and_malformed_source_refusal():
    with source_db() as db:
        db.execute("insert into business_legacy_links(task_id,work_todo_id) values(31,11)")
        metadata = capture_legacy_owner_source(db)
        assert migrate_legacy_owner(db, metadata) == {"mapped": 1}
        assert read_import_task(db, 11) == 31
        restore_legacy_owner(db, metadata)
        db.execute("delete from business_legacy_links")
        db.execute("insert into business_legacy_links(task_id,work_project_id) values(31,7)")
        with pytest.raises(ValueError, match="alternative"):
            capture_legacy_owner_source(db)
        db.execute("delete from business_legacy_links")
        db.execute("alter table business_legacy_links add column extra text")
        with pytest.raises(ValueError, match="declaration"):
            capture_legacy_owner_source(db)
        assert db.execute("select 1 from sqlite_schema where name='business_legacy_links'").fetchone()


@pytest.mark.parametrize("corruption", ("action_guard", "legacy"))
def test_inverse_rejects_corrupt_metadata_before_ddl(corruption):
    with source_db() as db:
        metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata)
        bad = deepcopy(metadata)
        if corruption == "action_guard":
            bad["action_guard"] = "CREATE TRIGGER trg_sync_legacy_update_public_id AFTER UPDATE ON actions BEGIN SELECT 1; END"
        else:
            bad["legacy"]["sql"] = bad["legacy"]["sql"].replace(
                "created_at text not null default current_timestamp",
                "created_at text not null default 'forged'",
            )
        before_schema = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
        before_business = tuple(db.execute("select * from business order by id"))
        with pytest.raises(ValueError, match="metadata"):
            restore_legacy_owner(db, bad)
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before_schema
        assert tuple(db.execute("select * from business order by id")) == before_business


def test_live_supported_link_insert_after_capture():
    with source_db() as db:
        metadata = capture_legacy_owner_source(db)
        db.execute("insert into business_legacy_links(task_id,work_todo_id) values(31,11)")
        assert migrate_legacy_owner(db, metadata) == {"mapped": 1}
        assert read_import_task(db, 11) == 31
        restore_legacy_owner(db, metadata)
        assert db.execute("select task_id from business_legacy_links where work_todo_id=11").fetchone() == (31,)
        new_metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, new_metadata)
        assert read_import_task(db, 11) == 31


@pytest.mark.parametrize("owner,target", ((999, 31), (11, 999)))
def test_dangling_source_link_refused_before_ddl(owner, target):
    with source_db() as db:
        db.execute("insert into business_legacy_links(task_id,work_todo_id) values(?,?)",
                   (target, owner))
        before_schema = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
        before_rows = tuple(db.execute("select * from business_legacy_links"))
        with pytest.raises(ValueError, match="dangling"):
            capture_legacy_owner_source(db)
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before_schema
        assert tuple(db.execute("select * from business_legacy_links")) == before_rows


def test_external_link_dependency_refused_before_ddl():
    with source_db() as db:
        db.execute("create view unexpected_legacy_reader as select task_id from business_legacy_links")
        before_schema = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
        with pytest.raises(ValueError):
            capture_legacy_owner_source(db)
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before_schema


def test_inverse_refuses_new_current_owner_column_dependency_before_ddl():
    with source_db() as db:
        metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata)
        db.execute("create view unexpected_import_reader as select import_task_id from business")
        before_schema = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
        with pytest.raises(ValueError, match="dependency"):
            restore_legacy_owner(db, metadata)
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before_schema


@pytest.mark.parametrize("temporary_sql", (
    "create temp view unexpected_temp_import_reader as select import_task_id from main.business",
    "create temp trigger unexpected_temp_import_trigger after update on main.business "
    "begin select new.import_task_id; end",
))
def test_inverse_refuses_temp_owner_column_dependency_before_ddl(temporary_sql):
    with source_db() as db:
        metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata)
        db.execute(temporary_sql)
        before_main = tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name"))
        before_temp = tuple(db.execute("select type,name,sql from temp.sqlite_schema order by type,name"))
        before_business = tuple(db.execute("select * from main.business order by id"))
        with pytest.raises(ValueError, match="dependency"):
            restore_legacy_owner(db, metadata)
        assert tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name")) == before_main
        assert tuple(db.execute("select type,name,sql from temp.sqlite_schema order by type,name")) == before_temp
        assert tuple(db.execute("select * from main.business order by id")) == before_business


def test_inverse_probes_main_business_when_temp_name_shadows_it():
    with source_db() as db:
        metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata)
        db.execute("create temp table business(id integer primary key, note text)")
        db.execute("insert into temp.business(id,note) values(1,'keep')")
        before_temp = tuple(db.execute("select type,name,sql from temp.sqlite_schema order by type,name"))
        before_main = tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name"))
        with pytest.raises(ValueError, match="dependency"):
            restore_legacy_owner(db, metadata)
        assert db.execute("select id,note from temp.business").fetchall() == [(1, "keep")]
        assert tuple(db.execute("select type,name,sql from temp.sqlite_schema order by type,name")) == before_temp
        assert tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name")) == before_main


def test_inbound_legacy_fk_refused_before_ddl_with_populated_child():
    with source_db() as db:
        db.execute("insert into business_legacy_links(task_id,work_todo_id) values(31,11)")
        metadata = capture_legacy_owner_source(db)
        link_id = db.execute("select id from business_legacy_links where work_todo_id=11").fetchone()[0]
        db.execute("create table unexpected_child(id integer primary key,link_id integer "
                   "references business_legacy_links(id))")
        db.execute("insert into unexpected_child(id,link_id) values(1,?)", (link_id,))
        before_schema = tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name"))
        before_rows = tuple(db.execute("select * from unexpected_child"))
        with pytest.raises(ValueError, match="inbound"):
            capture_legacy_owner_source(db)
        with pytest.raises(ValueError, match="inbound"):
            migrate_legacy_owner(db, metadata)
        assert tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name")) == before_schema
        assert tuple(db.execute("select * from unexpected_child")) == before_rows


def test_temp_inbound_legacy_fk_refused_before_ddl():
    with source_db() as db:
        db.execute("create temp table unexpected_temp_child(link_id integer "
                   "references business_legacy_links(id))")
        before_main = tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name"))
        before_temp = tuple(db.execute("select type,name,sql from temp.sqlite_schema order by type,name"))
        with pytest.raises(ValueError, match="inbound"):
            capture_legacy_owner_source(db)
        assert tuple(db.execute("select type,name,sql from main.sqlite_schema order by type,name")) == before_main
        assert tuple(db.execute("select type,name,sql from temp.sqlite_schema order by type,name")) == before_temp


def test_assign_rejects_none_float_and_bool_even_when_equal_to_current():
    with source_db() as db:
        metadata = capture_legacy_owner_source(db)
        migrate_legacy_owner(db, metadata)
        with pytest.raises(ValueError, match="missing formal Task"):
            assign_import_task(db, 11, None)
        assert read_import_task(db, 11) is None
        assert assign_import_task(db, 11, 31) == "created"
        with pytest.raises(ValueError, match="missing formal Task"):
            assign_import_task(db, 11, 31.0)
        assert read_import_task(db, 11) == 31
        insert_business(db, "task", {"id": 1, "title": "boolean control", "stage": "candidate"})
        assert assign_import_task(db, 12, 1) == "created"
        with pytest.raises(ValueError, match="missing formal Task"):
            assign_import_task(db, 12, True)
        assert assign_import_task(db, 11, 31) == "skipped"
        with pytest.raises(ValueError, match="conflicting"):
            assign_import_task(db, 11, 32)
