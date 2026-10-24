"""Frozen pre-edit declarations and the opt-in Business Actions contract."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

from app.action_storage import _action_definitions, _create_action_objects
from app.business_definitions import _definitions
from app.business_storage import insert_business
from app.business_candidate_storage import insert_business_candidate
from app.business_schema_release import _snapshot
from app.action_business_definitions import BUSINESS_ACTION_KINDS, BUSINESS_ACTION_AUTO
from app.action_feedback_storage import _insert_feedback_row, _materialize
from app.action_feedback_definitions import FEEDBACK_FIELDS
from app.action_email_storage import _set_sequence


SOURCE_PATH = (Path(__file__).parents[1] / "docs/superpowers/specs"
               / "2026-10-17-business-actions-current25-source.json")


def _insert_text_boundary_business_action(db, kind, values, *, source_rowid=None,
                                          ignore=False,
                                          on_conflict_key_do_nothing=False):
    """Test DML for the historical TEXT Actions intermediate only.

    Final compact Actions writer behavior is covered by the native parity
    tests; this fixture changes only the physical row representation here.
    """
    from app import action_business_storage as storage
    from app import action_storage as actions
    from app.action_business_definitions import BUSINESS_ACTION_AUTO, BUSINESS_ACTION_KEYS, business_action_column

    storage._validate_fields(kind, values)
    if on_conflict_key_do_nothing and (kind not in BUSINESS_ACTION_KEYS or ignore):
        raise ValueError("unsupported Business action conflict clause")
    actions._email_lock(db)
    supplied = values.get("id") if kind in BUSINESS_ACTION_AUTO else source_rowid
    identity = (storage._next_source_rowid(db, kind) if actions._source_bound_null(supplied)
                else actions._native_integer_primary_key(supplied))
    complete = storage._complete(db, kind, values)
    if kind in BUSINESS_ACTION_AUTO:
        complete.pop("id", None)
    created_at_sql = "json_set(?, '$.created_at', current_timestamp)" if "created_at" not in values else "?"
    conflict = (" on conflict(" + ",".join(business_action_column(kind, field)
                 for field in BUSINESS_ACTION_KEYS[kind]) + f") where record_type='{kind}' do nothing"
                if on_conflict_key_do_nothing else "")
    cursor = db.execute(
        "insert " + ("or ignore " if ignore else "")
        + "into main.actions(record_type,legacy_key,payload_json) values(?,?," + created_at_sql + ")"
        + conflict,
        (kind, str(identity), actions._native_payload(complete)),
    )
    from app.action_business_storage import BusinessActionWriteResult
    return BusinessActionWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def _update_text_boundary_business_action(db, kind, source_rowid, values, *, ignore=False):
    """Update the historical TEXT Actions intermediate using native payloads."""
    from app import action_business_storage as storage
    from app import action_storage as actions
    from app.action_business_definitions import BUSINESS_ACTION_AUTO, BUSINESS_ACTION_FIELDS, business_action_projection
    from app.action_task_delivery_storage import _quote

    storage._validate_fields(kind, values)
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
    return db.execute(
        "update " + ("or ignore " if ignore else "")
        + "main.actions set legacy_key=?,payload_json=? where record_type=? and legacy_key=?",
        (str(new_id), actions._native_payload(current), kind, str(source_rowid)),
    ).rowcount


@pytest.fixture(autouse=True)
def _historical_text_actions_writers(monkeypatch):
    """Keep current25 migration-boundary tests on their actual TEXT DDL."""
    from app import action_business_storage as storage

    monkeypatch.setattr(storage, "insert_business_action", _insert_text_boundary_business_action)
    monkeypatch.setattr(storage, "update_business_action", _update_text_boundary_business_action)


def _current25_source_store(path):
    """Load the frozen Business-migration boundary and required parent graph."""
    from app.action_business_storage import capture_business_action_source
    frozen = json.loads(SOURCE_PATH.read_text())
    fixture_path = Path(__file__).parent / "fixtures/current25_business_actions_source_schema.json"
    fixture = json.loads(fixture_path.read_text())
    expected = set(frozen["source_tables"]) | {
        "actions", "business", "business_legacy_links", "business_source_documents",
        "configs", "email_classifications", "runs", "schedules", "state", "tasks",
    }
    assert set(fixture["tables"]) == expected
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        # Preserve captured creation order within each category; PRAGMA
        # index_list sequence numbers are part of the native declaration.
        for kind in ("table", "index", "trigger"):
            for obj_kind, _, _, sql in fixture["objects"]:
                if obj_kind == kind:
                    db.execute(sql)
        assert {row[0] for row in db.execute(
            "select name from sqlite_schema where type='table' and name<>'sqlite_sequence'")} == expected
        source = capture_business_action_source(db)
        assert source["variant"] == "fresh"
        assert source["reply_variant"] == "fresh_memory_fk"
        db.commit()


def test_frozen_current25_source_receipt():
    assert hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest() == (
        "7c65b685c8cc75b8cd36ab6569f476912d853546ec276d5d1a4426248e696ae2"
    )


@pytest.mark.parametrize("variant,expected", (
    ("actual_no_memory_fk", "2c97f88e18fe052ce247e61216303783c47662ca42fdb2f895ddd628cc49dcc2"),
    ("fresh_memory_fk", "3e4873f10d7af890bc208082875ac969f94a526be922c6129745f19eac7d3d62"),
))
def test_pre_edit_current_actions_declaration(variant, expected):
    options = json.loads(SOURCE_PATH.read_text())["pre_edit_action_options"]
    assert hashlib.sha256(repr(_action_definitions(reply_variant=variant, **options)).encode()).hexdigest() == expected


def test_pre_edit_selected_and_native_business_declarations():
    assert hashlib.sha256(repr(_definitions(include_candidates=True)).encode()).hexdigest() == (
        "f594908497b0469ad772423155432132bfe16ac414c7c2f06690c4cb4697bc89"
    )
    assert hashlib.sha256(repr(_definitions()).encode()).hexdigest() == (
        "91fedd3da844ec56624767b78fac27210d342414de7b79c01f02de5f038d9d91"
    )


def test_business_action_api_imports():
    from app.action_business_definitions import business_action_projection
    from app.action_business_storage import (
        capture_business_action_source,
        migrate_business_action_schema,
        restore_business_action_schema,
    )

    assert callable(business_action_projection)
    assert callable(capture_business_action_source)
    assert callable(migrate_business_action_schema)
    assert callable(restore_business_action_schema)


def test_fresh_current25_empty_business_actions_roundtrip(tmp_path, monkeypatch):
    from app.action_business_storage import (
        capture_business_action_source, migrate_business_action_schema,
        restore_business_action_schema,
    )
    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        source = capture_business_action_source(db)
        assert source["variant"] == "fresh"
        migrate_business_action_schema(db, json.loads(json.dumps(source)))
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert not db.execute("select name from sqlite_sequence where name in ("
                              + ",".join("?" for _ in BUSINESS_ACTION_AUTO) + ")",
                              BUSINESS_ACTION_AUTO).fetchall()
        assert not db.execute("select name from main.sqlite_schema where name in ("
                              + ",".join("?" for _ in source["source_tables"]) + ")",
                              tuple(source["source_tables"])).fetchall()
        restore_business_action_schema(db, json.loads(json.dumps(source)))
        assert capture_business_action_source(db) == source
        db.rollback()



def _seed_source_feedback(db, values):
    """Seed the frozen object source, independent of current runtime writers."""
    fields = _materialize(db, "feedback_events", values)
    _insert_feedback_row(db, "feedback_events", (1, *(fields[name] for name in FEEDBACK_FIELDS["feedback_events"])), "actual")


def _seed_all_ten(db):
    _seed_source_feedback(db, {
        "key": "feedback:1", "feedback_token": "feedback-token:1", "rating": "positive",
    })
    db.execute("insert into business_source_documents(id,identity_key,body) values(1,'document:1','Source body')")
    db.execute("insert into business_task_signals(id,source_type,source_ref,source_document_id,"
               "dedupe_key,context_json) values(10,'meeting','meeting:10',1,'signal:10','{\"owner\":1}')")
    insert_business(db, "anchor", {"id": 1, "anchor_type": "project", "anchor_ref": "anchor:1", "title": "Anchor"})
    insert_business(db, "project", {"id": 2, "canonical_anchor_id": 1, "title": "Project", "registry_source": "test"})
    insert_business(db, "task", {"id": 3, "title": "Task", "stage": "candidate"})
    insert_business(db, "work_cluster", {"id": 4, "title": "Cluster"})
    insert_business(db, "task", {"id": 6, "title": "Second task", "stage": "candidate"})
    # This frozen source predates retirement of five legacy work_project
    # fields. Seed the source payload directly while retaining current writer
    # defaults for every field that still exists in the public Business API.
    from app import business_storage
    values = business_storage._complete_values(db, "work_project", {
        "id": 7, "title": "Work project",
    })
    stored = business_storage._stored_values(db, "work_project", values)
    payload = json.loads(stored["payload_json"])
    for name, value in {
        "priority": "none", "risk_level": "none", "needs_derek_attention": 0,
        "next_follow_up_at": "", "follow_up_mode": "none",
    }.items():
        payload[name] = business_storage._encoded_native_sqlite_value(value)
    stored["payload_json"] = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    )
    data = {"record_type": "work_project", "legacy_key": "7", **stored}
    db.execute(
        "insert into business(" + ",".join(data) + ") values(" +
        ",".join("?" for _ in data) + ")", tuple(data.values()),
    )
    insert_business_candidate(db, "attention_item", {
        "id": 5, "stable_key": "attention:5", "category": "watch", "title": "Attention",
        "why_attention": "why", "current_state": "open", "ceo_action": "act",
        "anchor_id": 1, "evidence_signal_id": 10,
    })
    db.execute("insert into business_attention_events(id,attention_item_id,event_type,signal_id,"
               "before_json,after_json,reason) values(7,5,'opened',10,'{}','{}','opened')")
    db.execute("insert into business_attention_proposal_tasks(attention_item_id,task_id) values(5,3)")
    db.execute("insert into business_attention_tasks(attention_item_id,task_id) values(5,3)")
    db.execute("insert into business_project_evidence(project_id,signal_id) values(2,10)")
    db.execute("insert into business_task_anchor_links(id,task_id,anchor_id,evidence_signal_id) values(8,3,1,10)")
    db.execute("insert into business_task_date_evidence(id,task_id,date_type,raw_phrase,source_signal_id,actor_kind) "
               "values(9,3,'next_check_at','next Friday',10,'human')")
    db.execute("insert into business_task_evidence(task_id,signal_id,evidence_role) values(3,10,'discovery')")
    db.execute("insert into business_task_relations(from_task_id,to_task_id,relation_type,supporting_signal_id) "
               "values(3,6,'depends_on',10)")
    db.execute("insert into business_work_cluster_tasks(cluster_id,task_id) values(4,3)")
    db.execute("insert into business_legacy_links(signal_id,work_project_id) values(10,7)")
    for kind in BUSINESS_ACTION_AUTO:
        db.execute("update sqlite_sequence set seq=90 where name=?", (kind,))


@pytest.mark.parametrize("variant,reply_variant", (
    ("fresh", "fresh_memory_fk"), ("fresh", "actual_no_memory_fk"),
    ("actual", "fresh_memory_fk"), ("actual", "actual_no_memory_fk"),
))
def test_current25_populated_ten_family_inverse(tmp_path, variant, reply_variant, monkeypatch):
    from app.action_business_storage import (
        capture_business_action_source, migrate_business_action_schema,
        restore_business_action_schema,
    )
    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as db:
        if variant == "actual" or reply_variant == "actual_no_memory_fk":
            db.execute("pragma foreign_keys=off")
        if reply_variant == "actual_no_memory_fk":
            assert db.execute("select count(*) from actions").fetchone()[0] == 0
            db.execute("drop table actions")
            _create_action_objects(
                db, reply_variant=reply_variant,
                **json.loads(SOURCE_PATH.read_text())["pre_edit_action_options"])
        if variant == "actual":
            signal = json.loads(SOURCE_PATH.read_text())["declarations"]["business_task_signals"]
            db.execute("drop table business_task_signals")
            db.execute(next(obj[3] for obj in signal["objects"] if obj[0] == "table"))
            index_sql = {obj[1]: obj[3] for obj in signal["objects"] if obj[0] == "index"}
            for index in reversed(signal["indexes"]):
                sql = index_sql.get(index["list"][1])
                if sql:
                    db.execute(sql)
            for obj in signal["objects"]:
                if obj[0] == "trigger" and obj[3]:
                    db.execute(obj[3])
        db.commit()
        db.execute("pragma foreign_keys=on")
        _seed_all_ten(db)
        _set_sequence(db, "actions:business_task_follow_ups", 777)
        _set_sequence(db, "business:task", 888)
        _set_sequence(db, "actions", 999)
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        db.execute("create table unrelated_parent(id integer primary key)")
        db.execute("create table unrelated_child(id integer primary key, parent_id integer references unrelated_parent(id))")
        db.execute("insert into unrelated_child(id,parent_id) values(1,999)")
        unrelated_before = db.execute("pragma foreign_key_check").fetchall()
        source = capture_business_action_source(db)
        assert source["variant"] == variant
        assert source["reply_variant"] == reply_variant
        assert source["fk_findings"]
        facts = {kind: _snapshot(db, kind, hidden_rowid=kind not in BUSINESS_ACTION_AUTO)
                 for kind in BUSINESS_ACTION_KINDS}
        migrate_business_action_schema(db, json.loads(json.dumps(source)))
        assert db.execute("pragma foreign_key_check").fetchall() == unrelated_before
        assert db.execute("select seq from sqlite_sequence where name='actions:business_task_follow_ups'").fetchone()[0] == 777
        assert db.execute("select seq from sqlite_sequence where name='business:task'").fetchone()[0] == 888
        assert db.execute("select seq from sqlite_sequence where name='actions'").fetchone()[0] == 999
        assert db.execute("select count(*) from actions where record_type in ("
                          + ",".join("?" for _ in BUSINESS_ACTION_KINDS) + ")",
                          BUSINESS_ACTION_KINDS).fetchone()[0] == 10
        restore_business_action_schema(db, json.loads(json.dumps(source)))
        assert db.execute("select seq from sqlite_sequence where name='actions:business_task_follow_ups'").fetchone()[0] == 777
        assert db.execute("select seq from sqlite_sequence where name='business:task'").fetchone()[0] == 888
        assert db.execute("select seq from sqlite_sequence where name='actions'").fetchone()[0] == 999
        assert not db.execute("select name from sqlite_sequence where name in ("
                              + ",".join("?" for _ in BUSINESS_ACTION_AUTO) + ")",
                              tuple("actions:" + kind for kind in BUSINESS_ACTION_AUTO)).fetchall()
        assert {kind: _snapshot(db, kind, hidden_rowid=kind not in BUSINESS_ACTION_AUTO)
                for kind in BUSINESS_ACTION_KINDS} == facts
        assert capture_business_action_source(db) == source
        assert db.execute("pragma foreign_key_check").fetchall() == unrelated_before
        db.rollback()


def test_native_and_target_current_writes_inverse_and_reforward(tmp_path, monkeypatch):
    from app.action_business_storage import (
        capture_business_action_source, migrate_business_action_schema,
        restore_business_action_schema, insert_business_action, update_business_action,
        delete_business_action, _projected_snapshot,
    )
    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as target, sqlite3.connect(tmp_path / "native.sqlite") as native:
        target.execute("pragma foreign_keys=on")
        _seed_all_ten(target)
        target.commit()
        target.backup(native)
        native.execute("pragma foreign_keys=on")
        target.execute("pragma foreign_keys=off")
        target.execute("begin")
        metadata = json.loads(json.dumps(capture_business_action_source(target)))
        migrate_business_action_schema(target, metadata)
        target.commit()
        target.execute("pragma foreign_keys=on")
        for absent_or_wrong_kind in (1, 999):
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                native.execute("insert into business_project_evidence(project_id,signal_id) values(2,?)",
                               (absent_or_wrong_kind,))
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                insert_business_action(target, "business_project_evidence", {
                    "project_id": 2, "signal_id": absent_or_wrong_kind})
        signal = dict(source_type="meeting", source_ref="meeting:91",
                      source_document_id=1, dedupe_key="signal:91", context_json='{"owner":91}',
                      source_time=b"\x00\xff", author_user_id=42)
        native_id = native.execute("insert into business_task_signals("
            + ",".join(signal) + ") values(" + ",".join("?" for _ in signal) + ")",
            tuple(signal.values())).lastrowid
        target_id = insert_business_action(target, "business_task_signals", signal).lastrowid
        assert native_id == target_id == 91
        native_event = native.execute("insert into business_attention_events("
            "attention_item_id,event_type,signal_id,before_json,after_json,reason) "
            "values(5,'updated',91,'{}','{}','new event')").lastrowid
        target_event = insert_business_action(target, "business_attention_events", dict(
            attention_item_id=5, event_type="updated", signal_id=91,
            before_json="{}", after_json="{}", reason="new event")).lastrowid
        assert native_event == target_event == 91
        for kind, sql, values in (
            ("business_task_anchor_links", "insert into business_task_anchor_links(task_id,anchor_id,evidence_signal_id) values(6,1,91)",
             dict(task_id=6, anchor_id=1, evidence_signal_id=91)),
            ("business_task_date_evidence", "insert into business_task_date_evidence(task_id,date_type,raw_phrase,source_signal_id,actor_kind) values(6,'next_check_at','Monday',91,'human')",
             dict(task_id=6, date_type="next_check_at", raw_phrase="Monday", source_signal_id=91, actor_kind="human")),
            ("business_project_evidence", "insert into business_project_evidence(project_id,signal_id) values(2,91)",
             dict(project_id=2, signal_id=91)),
            ("business_task_relations", "insert into business_task_relations(from_task_id,to_task_id,relation_type,supporting_signal_id) values(6,3,'related_to',91)",
             dict(from_task_id=6, to_task_id=3, relation_type="related_to", supporting_signal_id=91)),
        ):
            native_rowid = native.execute(sql).lastrowid
            target_rowid = insert_business_action(target, kind, values).lastrowid
            assert target_rowid == native_rowid
        native.execute("update business_task_anchor_links set status='confirmed' where id=91")
        assert update_business_action(target, "business_task_anchor_links", 91,
                                      {"status": "confirmed"}) == 1
        native.execute("delete from business_task_relations where rowid=2")
        assert delete_business_action(target, "business_task_relations", 2) == 1
        for kind in ("business_attention_events", "business_task_anchor_links"):
            native.execute("delete from " + kind + " where id=91")
            assert delete_business_action(target, kind, 91) == 1
        for operation in ("delete", "update"):
            native_sql = ("delete from business_task_date_evidence where id=91" if operation == "delete"
                          else "update business_task_date_evidence set raw_phrase='Tuesday' where id=91")
            with pytest.raises(sqlite3.IntegrityError, match="business task date evidence is immutable"):
                native.execute(native_sql)
            with pytest.raises(sqlite3.IntegrityError, match="business task date evidence is immutable"):
                if operation == "delete":
                    delete_business_action(target, "business_task_date_evidence", 91)
                else:
                    update_business_action(target, "business_task_date_evidence", 91,
                                           {"raw_phrase": "Tuesday"})
        for kind in BUSINESS_ACTION_KINDS:
            source = _snapshot(native, kind, hidden_rowid=kind not in BUSINESS_ACTION_AUTO)
            assert _projected_snapshot(target, kind, "fresh") == source, kind
        for kind in BUSINESS_ACTION_AUTO:
            native_water = native.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0]
            target_water = target.execute("select seq from sqlite_sequence where name=?",
                                          ("actions:" + kind,)).fetchone()[0]
            assert target_water == native_water == 91
        native.execute("savepoint late_failure")
        target.execute("savepoint late_failure")
        native.execute("insert into business_attention_events(attention_item_id,event_type,signal_id,"
                       "before_json,after_json,reason,created_at) "
                       "values(5,'updated',10,'{}','{}','temporary','fixed')")
        insert_business_action(target, "business_attention_events", dict(
            attention_item_id=5, event_type="updated", signal_id=10,
            before_json="{}", after_json="{}", reason="temporary", created_at="fixed"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            native.execute("insert into business_attention_events(attention_item_id,event_type,signal_id,"
                           "before_json,after_json,reason,created_at) "
                           "values(5,'updated',999,'{}','{}','invalid','fixed')")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert_business_action(target, "business_attention_events", dict(
                attention_item_id=5, event_type="updated", signal_id=999,
                before_json="{}", after_json="{}", reason="invalid", created_at="fixed"))
        native.execute("rollback to late_failure")
        target.execute("rollback to late_failure")
        native.execute("release late_failure")
        target.execute("release late_failure")
        assert native.execute("select count(*) from business_attention_events where reason='temporary'").fetchone()[0] == 0
        assert target.execute("select count(*) from actions where record_type='business_attention_events' "
                              "and ba_attention_events_reason='temporary'").fetchone()[0] == 0
        native.commit()
        target.commit()
        target.execute("pragma foreign_keys=off")
        target.execute("begin")
        restore_business_action_schema(target, metadata)
        assert all(_snapshot(target, kind, hidden_rowid=kind not in BUSINESS_ACTION_AUTO) ==
                   _snapshot(native, kind, hidden_rowid=kind not in BUSINESS_ACTION_AUTO)
                   for kind in BUSINESS_ACTION_KINDS)
        next_source = capture_business_action_source(target)
        migrate_business_action_schema(target, json.loads(json.dumps(next_source)))
        target.rollback()


def test_business_action_reader_index_paths_match_native(tmp_path, monkeypatch):
    from app.action_business_storage import capture_business_action_source, migrate_business_action_schema

    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=on")
        _seed_all_ten(db)
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        for task_id in range(100, 210):
            insert_business(db, "task", {"id": task_id, "title": f"Index task {task_id}",
                                         "stage": "candidate"})
        for offset in range(1000):
            db.execute("insert into business_task_relations("
                       "from_task_id,to_task_id,relation_type,supporting_signal_id) "
                       "values(?,?,'related_to',10)",
                       (100 + offset % 100, 200 + offset // 100))
        db.execute("analyze")
        source_queries = (
            ("select from_task_id from business_task_relations "
             "where from_task_id=3 or to_task_id=3", "idx_business_task_relations_from",
             "idx_business_task_relations_to"),
            ("select id from business_task_signals where source_document_id=1",
             "idx_business_signal_document"),
            ("select id from business_attention_events where attention_item_id=5 order by created_at,id",
             "idx_business_attention_events_item"),
            ("select id from business_task_date_evidence where task_id=3 order by id",
             "idx_business_task_date_evidence_task"),
            ("select id from business_task_signals where source_type='meeting' "
             "order by source_ref,source_time,id", "idx_business_task_signals_source"),
        )
        for query in source_queries:
            details = " ".join(row[3] for row in db.execute("explain query plan " + query[0]))
            assert all(index in details for index in query[1:]), details
            assert "USE TEMP B-TREE" not in details, details
        migrate_business_action_schema(db, capture_business_action_source(db))
        db.execute("analyze")
        target_queries = (
            ("select ba_task_relations_from_task_id from actions "
             "where record_type='business_task_relations' and "
             "(ba_task_relations_from_task_id=3 or ba_task_relations_to_task_id=3)",
             "idx_actions_business_task_relations_lookup", "idx_actions_business_task_relations_reverse"),
            ("select business_signal_public_id from actions where record_type='business_task_signals' "
             "and ba_task_signals_source_document_id=1", "idx_actions_business_task_signals_document"),
            ("select cast(legacy_key as integer) from actions where record_type='business_attention_events' "
             "and ba_attention_events_attention_item_id=5 "
             "order by ba_attention_events_created_at,cast(legacy_key as integer)",
             "idx_actions_business_attention_events_lookup"),
            ("select cast(legacy_key as integer) from actions where record_type='business_task_date_evidence' "
             "and ba_task_date_evidence_task_id=3 order by cast(legacy_key as integer)",
             "idx_actions_business_task_date_evidence_lookup"),
            ("select business_signal_public_id from actions where record_type='business_task_signals' "
             "and ba_task_signals_source_type='meeting' "
             "order by ba_task_signals_source_ref,ba_task_signals_source_time,cast(legacy_key as integer)",
             "idx_actions_business_task_signals_lookup"),
        )
        failures = []
        for query in target_queries:
            details = " ".join(row[3] for row in db.execute("explain query plan " + query[0]))
            if not all(index in details for index in query[1:]) or "USE TEMP B-TREE" in details:
                failures.append(details)
        assert not failures, failures
        db.rollback()


def test_missing_created_at_uses_actual_insert_statement_clock(tmp_path, monkeypatch):
    from app.action_business_storage import (
        capture_business_action_source, insert_business_action, migrate_business_action_schema,
    )
    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=on")
        _seed_all_ten(db)
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        migrate_business_action_schema(db, capture_business_action_source(db))
        db.execute("create table statement_clock(value text not null)")
        db.execute("create trigger capture_business_insert_clock after insert on actions "
                   "when new.record_type='business_attention_tasks' begin "
                   "insert into statement_clock(value) values(current_timestamp); end")
        with pytest.raises(sqlite3.IntegrityError):
            insert_business_action(db, "business_attention_tasks", {
                "attention_item_id": 5, "task_id": 6, "created_at": None,
            })
        delayed = False

        def delay_actual_insert(sql):
            nonlocal delayed
            if not delayed and "insert into main.actions" in sql.lower() and "business_attention_tasks" in sql:
                delayed = True
                time.sleep(1.2)

        db.set_trace_callback(delay_actual_insert)
        try:
            insert_business_action(db, "business_attention_tasks", {
                "attention_item_id": 5, "task_id": 6,
            })
        finally:
            db.set_trace_callback(None)
        assert delayed
        created_at = db.execute("select ba_attention_tasks_created_at from actions "
                                "where record_type='business_attention_tasks' "
                                "and ba_attention_tasks_task_id=6").fetchone()[0]
        statement_clock = db.execute("select value from statement_clock").fetchone()[0]
        assert created_at == statement_clock
        db.rollback()


def test_physical_actions_max_does_not_exhaust_business_public_stream(tmp_path, monkeypatch):
    from app.action_business_storage import (
        capture_business_action_source, migrate_business_action_schema,
        insert_business_action, restore_business_action_schema,
    )
    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as db:
        db.execute("insert into business_source_documents(id,identity_key,body) values(1,'document:max','Body')")
        db.execute("insert into business_task_signals(id,source_type,source_ref,dedupe_key,source_document_id) "
                   "values(1,'meeting','meeting:source','signal:source',1)")
        _seed_source_feedback(db, {
            "key": "feedback:max", "feedback_token": "feedback-token:max", "rating": "positive",
        })
        db.execute("update actions set id=? where id=(select min(id) from actions)", (2**63 - 1,))
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        source = capture_business_action_source(db)
        migrate_business_action_schema(db, source)
        result = insert_business_action(db, "business_task_signals", {
            "source_type": "meeting", "source_ref": "meeting:first", "dedupe_key": "signal:first",
            "source_document_id": 1,
        })
        assert result.lastrowid == 2
        assert db.execute("select id from actions where record_type='business_task_signals'").fetchone()[0] != 2**63 - 1
        restore_business_action_schema(db, source)
        assert db.execute("select id from actions").fetchone()[0] == 2**63 - 1
        assert [row[0] for row in db.execute("select id from business_task_signals order by id")] == [1, 2]
        db.rollback()


@pytest.mark.parametrize("kind,seed_sql,next_sql,next_values,keeps_transaction", (
    ("business_attention_events",
     "insert into business_attention_events(id,attention_item_id,event_type,signal_id,before_json,after_json,reason) values(? ,5,'updated',10,'{}','{}','max')",
     "insert into business_attention_events(attention_item_id,event_type,signal_id,before_json,after_json,reason) values(5,'updated',10,'{}','{}','next')",
     dict(attention_item_id=5, event_type="updated", signal_id=10, before_json="{}", after_json="{}", reason="next"), False),
    ("business_task_anchor_links",
     "insert into business_task_anchor_links(id,task_id,anchor_id,evidence_signal_id) values(?,6,1,10)",
     "insert into business_task_anchor_links(task_id,anchor_id,evidence_signal_id) values(3,1,10)",
     dict(task_id=3, anchor_id=1, evidence_signal_id=10), False),
    ("business_task_date_evidence",
     "insert into business_task_date_evidence(id,task_id,date_type,raw_phrase,source_signal_id,actor_kind) values(?,6,'next_check_at','max',10,'human')",
     "insert into business_task_date_evidence(task_id,date_type,raw_phrase,source_signal_id,actor_kind) values(6,'next_check_at','next',10,'human')",
     dict(task_id=6, date_type="next_check_at", raw_phrase="next", source_signal_id=10, actor_kind="human"), True),
    ("business_task_signals",
     "insert into business_task_signals(id,source_type,source_ref,dedupe_key,source_document_id) values(?,'meeting','meeting:max','signal:max',1)",
     "insert into business_task_signals(source_type,source_ref,dedupe_key,source_document_id) values('meeting','meeting:next','signal:next',1)",
     dict(source_type="meeting", source_ref="meeting:next", dedupe_key="signal:next", source_document_id=1), True),
))
def test_native_public_autoincrement_full_caller_effect(tmp_path, kind, seed_sql, next_sql,
                                                        next_values, keeps_transaction, monkeypatch):
    from app.action_business_storage import (
        capture_business_action_source, migrate_business_action_schema, insert_business_action,
    )
    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as target, sqlite3.connect(tmp_path / "native.sqlite") as native:
        _seed_all_ten(target)
        target.execute(seed_sql, (2**63 - 1,))
        target.execute("create table marker(id integer)")
        target.commit()
        target.backup(native)
        target.execute("pragma foreign_keys=off")
        target.execute("begin")
        migrate_business_action_schema(target, capture_business_action_source(target))
        target.commit()
        native.execute("insert into marker values(1)")
        target.execute("insert into marker values(1)")
        with pytest.raises(sqlite3.OperationalError, match="database or disk is full") as source_error:
            native.execute(next_sql)
        with pytest.raises(sqlite3.OperationalError, match="database or disk is full") as target_error:
            insert_business_action(target, kind, next_values)
        assert source_error.value.sqlite_errorcode == target_error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
        assert native.in_transaction == target.in_transaction == keeps_transaction
        assert native.execute("select count(*) from marker").fetchone()[0] == int(keeps_transaction)
        assert target.execute("select count(*) from marker").fetchone()[0] == int(keeps_transaction)


def test_source_mutation_and_reserved_collisions_reject_before_ddl(tmp_path, monkeypatch):
    from app.action_business_storage import (
        capture_business_action_source, migrate_business_action_schema,
        restore_business_action_schema,
    )
    path = tmp_path / "current25.sqlite"
    _current25_source_store(path)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        db.execute("savepoint mutation")
        db.execute("create index idx_mutant_signal on business_task_signals(source_ref)")
        before = tuple(db.execute("select name,sql from main.sqlite_schema order by name"))
        with pytest.raises(ValueError, match="source declarations"):
            capture_business_action_source(db)
        assert tuple(db.execute("select name,sql from main.sqlite_schema order by name")) == before
        db.execute("rollback to mutation")
        db.execute("release mutation")
        db.execute("savepoint temp_collision")
        db.execute("create temp table business_task_signals(id integer)")
        before = tuple(db.execute("select name,sql from main.sqlite_schema order by name"))
        with pytest.raises(ValueError, match="TEMP collision"):
            capture_business_action_source(db)
        assert tuple(db.execute("select name,sql from main.sqlite_schema order by name")) == before
        db.execute("rollback to temp_collision")
        db.execute("release temp_collision")
        metadata = capture_business_action_source(db)
        migrate_business_action_schema(db, metadata)
        db.execute("create table main.business_task_signals(id integer)")
        before = tuple(db.execute("select name,sql from main.sqlite_schema order by name"))
        with pytest.raises(ValueError, match="collision"):
            restore_business_action_schema(db, json.loads(json.dumps(metadata)))
        assert tuple(db.execute("select name,sql from main.sqlite_schema order by name")) == before
        db.rollback()
