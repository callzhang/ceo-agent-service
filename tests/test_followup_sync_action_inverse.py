"""Current-fact inverse checks for the disabled nine-family migration."""

from contextlib import closing
import sqlite3

import pytest

from app.action_followup_sync_definitions import sync_action_projection
from app.action_followup_sync_storage import (
    capture_followup_sync_source, migrate_followup_sync_schema, restore_followup_sync_schema,
)
from app.action_storage import insert_sync_action, update_sync_action, delete_sync_action
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


@pytest.mark.parametrize("initial", (False, True))
def test_current_facts_all_families_and_legacy_link_survive_inverse_and_reforward(migration_source, initial):
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
                assert delete_sync_action(db, kind, 1).rowcount == 1
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
            assert delete_sync_action(db, kind, 700).rowcount == 1
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
