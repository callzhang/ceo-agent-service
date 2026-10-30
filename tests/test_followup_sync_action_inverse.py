"""Current-fact inverse checks for the disabled nine-family migration."""
from tests.support.action_fixtures import delete_sync_fixture

from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from app.action_followup_sync_definitions import sync_action_projection
from app.action_followup_sync_storage import (
    capture_followup_sync_source, migrate_followup_sync_schema, restore_followup_sync_schema,
)
from app.action_storage import insert_sync_action, update_sync_action
from test_email_action_migration import historical_text_action_writers as historical_text_action_writers
from test_followup_sync_action_migration import source as _source_fixture
from test_followup_sync_source import NAMES, values


@pytest.fixture(params=("actual_no_memory_fk", "fresh_memory_fk"))
def migration_source(request, tmp_path):
    return _source_fixture.__wrapped__(request, tmp_path)


def test_target_zero_water_remains_distinct_from_absent_after_inverse(migration_source):
    path, _ = migration_source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        db.execute("insert into main.sqlite_sequence(name,seq) values('actions:work_updates',0)")
        assert db.execute("select seq from main.sqlite_sequence where name='actions:business_task_follow_ups'").fetchone() is None
        restore_followup_sync_schema(db, state)
        assert db.execute("select seq from main.sqlite_sequence where name='work_updates'").fetchone() == (0,)
        assert db.execute("select seq from main.sqlite_sequence where name='business_task_follow_ups'").fetchone() is None
        db.rollback()


@pytest.mark.parametrize("alias", ("actions:WORK_UPDATES", "actions:unknown", "ACTIONS:work_updates"))
def test_inverse_refuses_unknown_action_allocator_alias_before_mutation(migration_source, alias):
    path, _ = migration_source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        db.execute("insert into main.sqlite_sequence(name,seq) values(?,77)", (alias,))
        before_schema = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        before_sequences = db.execute("select name,seq from main.sqlite_sequence order by name").fetchall()
        with pytest.raises(ValueError, match="unsupported inverse action allocator alias"):
            restore_followup_sync_schema(db, state)
        assert db.in_transaction
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before_schema
        assert db.execute("select name,seq from main.sqlite_sequence order by name").fetchall() == before_sequences
        db.rollback()


def test_inverse_preserves_unrelated_literal_sequence_entry(migration_source):
    path, _ = migration_source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        db.execute("insert into main.sqlite_sequence(name,seq) values('unrelated_counter',91)")
        restore_followup_sync_schema(db, state)
        assert db.execute("select seq from main.sqlite_sequence where name='unrelated_counter'").fetchone() == (91,)
        assert capture_followup_sync_source(db)["source_variant"] == state["source_variant"]
        db.rollback()


@pytest.mark.parametrize("dependency", (
    "create table extra_child(id integer references work_updates(id))",
    "create view extra_view as select summary from work_updates",
    "create trigger extra_trigger after insert on work_todos begin select summary from work_updates; end",
))
def test_inverse_refuses_future_source_dependency_before_mutation(migration_source, dependency):
    path, _ = migration_source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        db.execute(dependency)
        before_schema = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        before_sequences = db.execute("select name,seq from main.sqlite_sequence order by name").fetchall()
        with pytest.raises((ValueError, sqlite3.OperationalError), match="unsupported|no such table"):
            restore_followup_sync_schema(db, state)
        assert db.in_transaction
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before_schema
        assert db.execute("select name,seq from main.sqlite_sequence order by name").fetchall() == before_sequences
        db.rollback()


@pytest.mark.parametrize("parent_state", ("missing", "wrong_kind"))
def test_inverse_refuses_orphaned_business_attempt_without_losing_receipt(
    migration_source, parent_state, historical_text_action_writers, monkeypatch,
):
    monkeypatch.setattr(
        "test_followup_sync_action_inverse.insert_sync_action",
        historical_text_action_writers.insert_sync_action,
    )
    monkeypatch.setattr(
        "test_followup_sync_action_inverse.delete_sync_fixture",
        historical_text_action_writers.delete_sync_action,
    )
    path, _ = migration_source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        if parent_state == "wrong_kind":
            insert_sync_action(db, "work_updates", values("work_updates"))
        parent = insert_sync_action(db, "business_task_follow_ups", values("business_task_follow_ups"))
        parent_physical = db.execute("select id from main.actions where record_type='business_task_follow_ups' "
                                     "and legacy_key=?", (str(parent.lastrowid),)).fetchone()[0]
        receipt = b"provider\x00receipt"
        child = values("business_task_follow_up_send_attempts") | {"draft_id": parent.lastrowid,
                                                                     "result_json": receipt}
        insert_sync_action(db, "business_task_follow_up_send_attempts", child)
        insert_sync_action(db, "business_task_todo_sync_outbox", values("business_task_todo_sync_outbox"))
        assert delete_sync_fixture(db, "business_task_follow_ups", parent.lastrowid).rowcount == 1
        if parent_state == "wrong_kind":
            db.execute("insert into main.actions(id,record_type,legacy_key,payload_json) "
                       "select ?,record_type,'2',payload_json from main.actions "
                       "where record_type='work_updates'", (parent_physical,))
        before_schema = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        before_actions = db.execute("select id,record_type,legacy_key,payload_json,sync_draft_action_id "
                                    "from main.actions order by id").fetchall()
        before_sequences = db.execute("select name,seq from main.sqlite_sequence order by name").fetchall()
        before_fk = db.execute("pragma foreign_key_check").fetchall()
        assert before_fk
        assert db.execute("select sync_result_json,typeof(sync_result_json) from main.actions "
                          "where record_type='business_task_follow_up_send_attempts'").fetchone() == (receipt, "blob")
        with pytest.raises(ValueError, match="unsupported business follow-up parent"):
            restore_followup_sync_schema(db, state)
        assert db.in_transaction and db.execute("pragma foreign_keys").fetchone() == (0,)
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before_schema
        assert db.execute("select id,record_type,legacy_key,payload_json,sync_draft_action_id "
                          "from main.actions order by id").fetchall() == before_actions
        assert db.execute("select name,seq from main.sqlite_sequence order by name").fetchall() == before_sequences
        assert db.execute("pragma foreign_key_check").fetchall() == before_fk
        db.rollback()


def test_inverse_refuses_swapped_legacy_link_column_metadata(migration_source):
    path, _ = migration_source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("insert into main.business_tasks(id) values(11)")
        db.execute("insert into main.business_task_signals(id) values(11)")
        db.execute("insert into main.work_todos(id) values(22)")
        db.execute("insert into main.business_legacy_links(task_id,work_todo_id) values(11,22)")
        db.commit()
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        altered = deepcopy(state)
        columns = altered["external"]["business_legacy_links"]["columns"]
        columns[1], columns[2] = columns[2], columns[1]
        before_schema = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        before_links = db.execute("select * from main.business_legacy_links").fetchall()
        before_actions = db.execute("select id,record_type,legacy_key,payload_json from main.actions order by id").fetchall()
        before_sequences = db.execute("select name,seq from main.sqlite_sequence order by name").fetchall()
        before_fk = db.execute("pragma foreign_key_check").fetchall()
        assert before_links[0][1:3] == (None, 11)
        assert before_fk == []
        with pytest.raises(ValueError, match="unsupported inverse legacy link metadata"):
            restore_followup_sync_schema(db, altered)
        assert db.in_transaction and db.execute("pragma foreign_keys").fetchone() == (0,)
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before_schema
        assert db.execute("select * from main.business_legacy_links").fetchall() == before_links
        assert db.execute("select id,record_type,legacy_key,payload_json from main.actions order by id").fetchall() == before_actions
        assert db.execute("select name,seq from main.sqlite_sequence order by name").fetchall() == before_sequences
        assert db.execute("pragma foreign_key_check").fetchall() == before_fk
        db.rollback()


@pytest.mark.parametrize("initial", (False, True))
def test_current_facts_all_families_and_legacy_link_survive_inverse_and_reforward(
    migration_source, initial, historical_text_action_writers, monkeypatch,
):
    monkeypatch.setattr(
        "test_followup_sync_action_inverse.insert_sync_action",
        historical_text_action_writers.insert_sync_action,
    )
    monkeypatch.setattr(
        "test_followup_sync_action_inverse.update_sync_action",
        historical_text_action_writers.update_sync_action,
    )
    monkeypatch.setattr(
        "test_followup_sync_action_inverse.delete_sync_fixture",
        historical_text_action_writers.delete_sync_action,
    )
    path, variant = migration_source
    source_variant = "actual" if variant == "actual_no_memory_fk" else "fresh_candidate"
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.executemany("insert into main.business_tasks(id) values(?)", ((11,), (700,)))
        db.execute("insert into main.business_task_signals(id) values(12)")
        if initial:
            for kind in NAMES:
                row = values(kind)
                db.execute(f"insert into main.{kind}({','.join(row)}) values({','.join('?' for _ in row)})",
                           tuple(row.values()))
            db.execute("insert into main.business_legacy_links(task_id,work_update_id) values(11,1)")
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.commit()
        db.execute("begin")
        state = migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        if initial:
            for kind in reversed(NAMES):
                assert delete_sync_fixture(db, kind, 1).rowcount == 1
            db.execute("delete from main.business_legacy_links")
        for kind in NAMES:
            row = values(kind, "replacement")
            if kind == "business_task_follow_up_send_attempts":
                row["draft_id"] = 2 if initial else 1
            inserted = insert_sync_action(db, kind, row)
            assert inserted.rowcount == 1
            changed = {
                "work_updates": {"changes_json": b"raw\x00change"},
                "follow_up_drafts": {"send_result_json": "receipt\x00not-json"},
                "follow_up_send_attempts": {"draft_id": 999,
                                            "late_result_json": b"late\x00receipt"},
                "business_task_follow_ups": {"send_result_json": b"provider\x00receipt"},
                "business_task_follow_up_send_attempts": {"result_json": "sent\x00receipt"},
                "task_todo_sync_outbox": {"receipt_json": b"outbox\x00receipt"},
                "business_task_todo_sync_outbox": {"receipt_json": "not-json\x00receipt"},
                "work_todo_dingtalk_links": {"dingtalk_task_id": "provider-legacy\x00id",
                                              "last_dingtalk_payload_json": b"provider\x00payload"},
                "business_task_dingtalk_links": {"dingtalk_task_id": "provider-business\x00id",
                                                  "last_dingtalk_payload_json": "receipt\x00text"},
            }[kind]
            assert update_sync_action(db, kind, inserted.lastrowid, changed).rowcount == 1
            db.execute("update main.sqlite_sequence set seq=500 where name=?", ("actions:" + kind,))
        for kind in NAMES:
            high = values(kind, "high") | {"id": 700}
            if kind == "business_task_follow_up_send_attempts":
                high["draft_id"] = 2 if initial else 1
            if kind == "work_todo_dingtalk_links":
                high["work_todo_id"] = 700
            if kind == "business_task_dingtalk_links":
                high["business_task_id"] = 700
            assert insert_sync_action(db, kind, high).rowcount == 1
        for kind in reversed(NAMES):
            assert delete_sync_fixture(db, kind, 700).rowcount == 1
        public_update = 2 if initial else 1
        physical = db.execute("select id from main.actions where record_type='work_updates' "
                              "and legacy_key=?", (str(public_update),)).fetchone()[0]
        db.execute("insert into main.business_legacy_links(task_id,work_update_id) values(11,?)", (physical,))
        expected = {}
        for kind in NAMES:
            query = sync_action_projection(kind, source_variant=source_variant)
            cursor = db.execute(f"select * from ({query}) order by id")
            columns = tuple(item[0] for item in cursor.description)
            rows = cursor.fetchall()
            types = db.execute(f"select {','.join('typeof(' + field + ')' for field in columns)} "
                               f"from ({query}) order by id").fetchall()
            expected[kind] = columns, rows, types
        assert db.execute("pragma foreign_key_check").fetchall() == []
        restore_followup_sync_schema(db, state)
        for kind in NAMES:
            columns, rows, types = expected[kind]
            assert db.execute(f"select * from main.{kind} order by id").fetchall() == rows
            assert db.execute(f"select {','.join('typeof(' + field + ')' for field in columns)} "
                              f"from main.{kind} order by id").fetchall() == types
            assert db.execute("select seq from main.sqlite_sequence where name=?", (kind,)).fetchone() == (700,)
        assert db.execute("select work_update_id from main.business_legacy_links").fetchone() == (public_update,)
        assert db.execute("select sql from main.sqlite_schema where name='business_legacy_links'").fetchone()[0] == state["external"]["business_legacy_links"]["sql"]
        assert capture_followup_sync_source(db)["source_variant"] == source_variant
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.execute("insert into main.work_updates(project_id,source_type,source_ref,summary) values(1,'new','r','old-code-write')")
        migrate_followup_sync_schema(db, capture_followup_sync_source(db))
        assert db.execute("select count(*) from main.actions where record_type='work_updates'").fetchone()[0] == 2
        for kind in NAMES:
            columns, rows, types = expected[kind]
            query = sync_action_projection(kind, source_variant=source_variant)
            migrated = db.execute(f"select * from ({query}) order by id").fetchall()
            assert migrated[:len(rows)] == rows
            assert db.execute(f"select {','.join('typeof(' + field + ')' for field in columns)} "
                              f"from ({query}) order by id").fetchall()[:len(types)] == types
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.rollback()
