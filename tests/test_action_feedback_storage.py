"""Opt-in Feedback storage must retain exact source facts and declarations."""

import json
import hashlib
import sqlite3
from pathlib import Path

import pytest

from app import action_storage
from app import action_feedback_storage as feedback_storage
from app.action_feedback_storage import (
    SOURCE_SQL, SOURCE_INDEX_SQL, SOURCE_TRIGGER_SQL,
    capture_action_feedback_source, migrate_action_feedback_schema,
    restore_action_feedback_schema, insert_feedback_action, set_feedback_action,
    upsert_feedback_event, insert_feedback_decision_items,
    _expected_sources,
)
from app.action_feedback_definitions import FEEDBACK_KINDS, feedback_projection
from app.action_followup_sync_storage import LINK_TRIGGER_SQL


CURRENT31_OBJECT_HASHES = {
    (False, False): "6bf3df024fcea78aaf4cf3c2063ffd929bdc1fbfb7830a1d9c16727774eba900",
    (False, True): "da274d97456754615d5522c98066696c27b9fd6bc9519e974772a26823a178d9",
    (True, False): "6d9f7cea3336b06e25c4922e08fdb96604830c216780852fbb237c1d4abbe11a",
    (True, True): "14e1e294c5f08ad5c3be2dd5a3408e2f5323cd1515b6e78224562ed9320e0dd4",
}


def test_feedback_source_declarations_match_frozen_metadata():
    metadata = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                           "2026-10-17-feedback-actions-source-metadata.json").read_text())
    for variant in ("actual", "fresh"):
        expected = _expected_sources(variant)
        events = metadata["actual"]["feedback_events"]["declaration"] if variant == "actual" else metadata["fresh"]["feedback_events"]
        transitions = metadata["actual"]["feedback_processing_transitions"]["declaration"] if variant == "actual" else metadata["fresh"]["feedback_processing_transitions"]
        assert expected["feedback_events"] == events
        assert expected["feedback_processing_transitions"] == transitions
        assert expected["feedback_iteration_decision_items"] == metadata["post_task20_child"]


@pytest.mark.parametrize("fresh,receipts", [(False, False), (False, True),
                                             (True, False), (True, True)])
def test_feedback_opt_in_does_not_change_default_actions_declaration(fresh, receipts):
    kwargs = dict(include_reply=True, reply_variant=("fresh_memory_fk" if fresh else "actual_no_memory_fk"),
                  include_email=True, include_followup_sync=True,
                  include_business_sync=True, include_task_delivery=True,
                  include_errors=True, include_skill_config_receipts=receipts)
    default = action_storage._current_objects(**kwargs)
    assert default == action_storage._current_objects(**kwargs, include_feedback=False)
    assert hashlib.sha256(json.dumps(default, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == CURRENT31_OBJECT_HASHES[(fresh, receipts)]
    selected = action_storage._current_objects(**kwargs, include_feedback=True)
    assert selected != default
    table = next(item["sql"] for item in selected if item["type"] == "table")
    assert all(f"'{kind}'" in table for kind in (
        "feedback_events", "feedback_processing_transitions",
        "feedback_iteration_decision_items"))


def _db(fresh=False, receipts=False):
    db = sqlite3.connect(":memory:")
    db.execute("create table tasks(id integer primary key,record_type text not null,"
               "task_public_id integer,"
               "feedback_iteration_decisions__id integer unique,"
               "feedback_processing_items__feedback_key text unique,"
               "feedback_processing_rounds__id integer unique)")
    db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
    db.execute("create table business(id integer primary key,record_type text,task_public_id integer)")
    db.execute("create unique index business_task_public on business(task_public_id)")
    db.execute("create table business_tasks(id integer primary key)")
    db.execute("create table business_task_signals(id integer primary key)")
    db.execute("create table email_classifications(id integer primary key)")
    db.execute("create table business_legacy_links(id integer primary key,record_type text,task_public_id integer,work_update_id integer)")
    db.execute("insert into runs values(1,'agent_runs','1')")
    variant = "fresh_memory_fk" if fresh else "actual_no_memory_fk"
    action_storage.create_action_schema(db, include_reply=True, reply_variant=variant,
                                        include_email=True, include_followup_sync=True,
                                        include_business_sync=True, include_task_delivery=True,
                                        include_errors=True,
                                        include_skill_config_receipts=receipts)
    for sql in LINK_TRIGGER_SQL[:2]:
        db.execute(sql)
    for kind in FEEDBACK_KINDS:
        db.execute(SOURCE_SQL["fresh" if fresh else "actual"][kind])
        for sql in SOURCE_INDEX_SQL[kind]:
            db.execute(sql)
        if kind == "feedback_iteration_decision_items":
            for sql in SOURCE_TRIGGER_SQL:
                db.execute(sql)
    db.commit()
    return db


@pytest.mark.parametrize("fresh,receipts", [(False, False), (False, True),
                                             (True, False), (True, True)])
def test_feedback_roundtrip(fresh, receipts):
    with _db(fresh, receipts) as db:
        before = tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema where sql is not null order by type,name"))
        db.execute("insert into feedback_events(key,feedback_token) values(null,'token')")
        db.execute("insert into feedback_events(key,feedback_token) values(null,'token')")
        db.execute("insert into feedback_events(key,feedback_token) values('k','token')")
        db.execute("insert into feedback_processing_transitions(feedback_key,to_status) values('k','pending')")
        db.execute("insert into feedback_processing_transitions(feedback_key,to_status) values('k','resolved')")
        db.execute("delete from feedback_processing_transitions where id=2")
        db.execute("insert into tasks(id,record_type,feedback_iteration_decisions__id) values(11,'feedback_iteration_decisions',7)")
        db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) values(12,'feedback_processing_items','k')")
        db.execute("insert into tasks(id,record_type,feedback_processing_rounds__id) values(13,'feedback_processing_rounds',4)")
        db.execute("insert into feedback_iteration_decision_items values(7,'k',4)")
        db.commit()
        source = capture_action_feedback_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_action_feedback_schema(db, source)
        assert db.execute("select count(*) from actions where record_type='feedback_events'").fetchone()[0] == 3
        assert db.execute("select seq from sqlite_sequence where name='actions:feedback_processing_transitions'").fetchone()[0] == 2
        assert db.execute("select count(*) from actions where record_type='feedback_iteration_decision_items'").fetchone()[0] == 1
        restore_action_feedback_schema(db, json.loads(json.dumps(state)))
        assert tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema where sql is not null order by type,name")) == before
        assert db.execute("select count(*) from feedback_events where key is null").fetchone()[0] == 2
        assert db.execute("select seq from sqlite_sequence where name='feedback_processing_transitions'").fetchone()[0] == 2
        db.rollback()


def test_feedback_source_name_collision_rejected_before_ddl():
    with _db() as db:
        db.execute("create index idx_actions_fb_event_key on business_task_signals(id)")
        before = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
        with pytest.raises(ValueError, match="target name collision"):
            capture_action_feedback_source(db)
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before


def test_feedback_inverse_rejects_corrupt_column_metadata_before_ddl():
    with _db() as db:
        source = capture_action_feedback_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_action_feedback_schema(db, source)
        before = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
        tampered = json.loads(json.dumps(state))
        tampered["action_columns"] = tampered["action_columns"][1:]
        with pytest.raises(ValueError, match="Action columns"):
            restore_action_feedback_schema(db, tampered)
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before
        db.rollback()
        assert db.execute("select 1 from sqlite_schema where name='feedback_events'").fetchone()


def _target():
    db = _db()
    source = capture_action_feedback_source(db)
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    migrate_action_feedback_schema(db, source)
    db.commit()
    db.execute("pragma foreign_keys=on")
    return db


@pytest.fixture(scope="module", autouse=True)
def _historical_text_feedback_writers():
    """Keep helper contract tests on the pre-compaction TEXT writer format."""
    patch = pytest.MonkeyPatch()

    def insert_text_action(db, kind, values, *, source_rowid=None):
        if kind not in FEEDBACK_KINDS:
            raise ValueError("unknown Feedback kind")
        action_storage._email_lock(db)
        fields = feedback_storage._materialize(db, kind, values)
        supplied_id = (source_rowid if source_rowid is not None else
                       fields.get("id") if kind == "feedback_processing_transitions" else None)
        identity = (feedback_storage._next_feedback_id(db, kind) if db.execute(
            "select typeof(?)", (supplied_id,)).fetchone()[0] == "null"
                    else action_storage._native_integer_primary_key(supplied_id))
        if kind == "feedback_processing_transitions":
            fields["id"] = identity
        row = (identity, *(fields.get(field) for field in feedback_storage.FEEDBACK_FIELDS[kind]))
        feedback_storage._insert_feedback_row(db, kind, row, "actual")
        return identity

    def set_text_action(db, kind, source_rowid, values, *, update_timestamp=False):
        if (kind not in FEEDBACK_KINDS[:2] or (not values and not update_timestamp)
                or set(values) - set(feedback_storage.FEEDBACK_FIELDS[kind])):
            raise ValueError("unsupported Feedback update")
        if kind == "feedback_processing_transitions" and "id" in values:
            raise ValueError("Transition ID update requires a separate source operation")
        fields = feedback_storage.FEEDBACK_FIELDS[kind]
        row = db.execute(
            "select " + ",".join(feedback_storage._quote(field) for field in fields)
            + " from (" + feedback_projection(kind, with_rowid=True)
            + ") where _source_rowid=?", (source_rowid,),
        ).fetchone()
        if row is None:
            return 0
        current = dict(zip(fields, row, strict=True))
        current.update(values)
        if kind == "feedback_processing_transitions":
            current.pop("id")
        expression = ("json_set(?,'$.updated_at',current_timestamp)" if update_timestamp else "?")
        return db.execute(
            "update main.actions set payload_json=" + expression
            + " where record_type=? and legacy_key=?",
            (action_storage._native_payload(current), kind, str(source_rowid)),
        ).rowcount

    # Tests call both imported aliases and module-global names inside helpers.
    for owner, name in ((feedback_storage, "insert_feedback_action"),
                        (feedback_storage, "set_feedback_action"),
                        (globals(), "insert_feedback_action"),
                        (globals(), "set_feedback_action")):
        if isinstance(owner, dict):
            patch.setitem(owner, name, insert_text_action if name == "insert_feedback_action" else set_text_action)
        else:
            patch.setattr(owner, name, insert_text_action if name == "insert_feedback_action" else set_text_action)
    yield
    patch.undo()


def test_feedback_helpers_and_constraints():
    with _target() as db:
        first = insert_feedback_action(db, "feedback_events", {"feedback_token": "t", "key": None})
        second = insert_feedback_action(db, "feedback_events", {"feedback_token": "t", "key": None})
        assert (first, second) == (1, 2)
        insert_feedback_action(db, "feedback_events", {"feedback_token": "t", "key": "k"})
        with pytest.raises(sqlite3.IntegrityError):
            insert_feedback_action(db, "feedback_events", {"feedback_token": "t", "key": "k"})
        assert set_feedback_action(db, "feedback_events", first, {"comment": b"\x00\xff"}) == 1
        assert db.execute("select typeof(comment),comment from (" +
                          feedback_projection("feedback_events", with_rowid=True) +
                          ") where key is null order by _source_rowid limit 1").fetchone() == ("blob", b"\x00\xff")
        transition = insert_feedback_action(db, "feedback_processing_transitions",
                                            {"feedback_key": "k", "to_status": "pending"})
        assert transition == 1
        db.execute("delete from actions where record_type='feedback_processing_transitions' and legacy_key='1'")
        assert insert_feedback_action(db, "feedback_processing_transitions",
                                      {"feedback_key": "k", "to_status": "pending"}) == 2
        with pytest.raises(sqlite3.IntegrityError):
            insert_feedback_action(db, "feedback_processing_transitions",
                                   {"feedback_key": "k", "to_status": "unknown"})
        db.execute("insert into tasks(id,record_type,feedback_iteration_decisions__id) values(11,'feedback_iteration_decisions',7)")
        db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) values(12,'feedback_processing_items','k')")
        db.execute("insert into tasks(id,record_type,feedback_processing_rounds__id) values(13,'feedback_processing_rounds',4)")
        item = {"decision_id": 7, "feedback_key": "k", "round_id": 4}
        rowid = insert_feedback_action(db, "feedback_iteration_decision_items", item)
        assert rowid == 1
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("delete from actions where record_type='feedback_iteration_decision_items'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("update actions set fb_round_id=4 where record_type='feedback_iteration_decision_items'")
        with pytest.raises(sqlite3.IntegrityError):
            insert_feedback_action(db, "feedback_iteration_decision_items", item)
        with pytest.raises(sqlite3.IntegrityError):
            insert_feedback_action(db, "feedback_iteration_decision_items",
                                   {**item, "round_id": 99, "feedback_key": "other"})


@pytest.mark.parametrize("fresh", (False, True))
def test_feedback_all_source_scalar_classes_new_writes_and_reforward(fresh):
    with _db(fresh) as db:
        db.execute("insert into feedback_events(rowid,key,feedback_token,rating,comment,"
                   "original_text,reply_text,raw_json,created_at,updated_at,resolved_at) "
                   "values(21,null,x'00ff',4.5,x'8000',2.25,x'01ff',x'ff',x'0080',3.25,x'007f')")
        db.execute("insert into feedback_processing_transitions(id,feedback_key,round_id,"
                   "batch_id,from_status,to_status,reason,created_at) "
                   "values(17,x'00',2.0,x'80','pending','resolved',x'ff',2.75)")
        db.execute("insert into feedback_processing_transitions(feedback_key,to_status) values('temporary','pending')")
        db.execute("delete from feedback_processing_transitions where id=18")
        original = {kind: tuple(db.execute("select rowid,* from " + kind + " order by rowid"))
                    for kind in FEEDBACK_KINDS}
        source = capture_action_feedback_source(db)
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_action_feedback_schema(db, source)
        from app.action_feedback_storage import _projected_facts
        for kind in FEEDBACK_KINDS:
            assert tuple(_projected_facts(db, kind, "fresh" if fresh else "actual")[0]) == original[kind]
        assert db.execute("select typeof(created_at),typeof(updated_at),typeof(comment) from (" +
                          feedback_projection("feedback_events") + ")").fetchone() == ("blob", "text", "blob")
        assert db.execute("select typeof(created_at),created_at from actions "
                          "where record_type='feedback_events'").fetchone() == ("blob", b"\x00\x80")
        # REAL input in a TEXT-affinity source column is stored as TEXT.
        assert db.execute("select updated_at from (" + feedback_projection("feedback_events") + ")").fetchone()[0] == "3.25"
        assert insert_feedback_action(db, "feedback_events", {"feedback_token": "new", "key": None}) == 22
        assert set_feedback_action(db, "feedback_events", 21, {"created_at": b"\x81"}) == 1
        assert insert_feedback_action(db, "feedback_processing_transitions",
                                      {"feedback_key": "new", "to_status": "pending"}) == 19
        restore_action_feedback_schema(db, json.loads(json.dumps(state)))
        assert db.execute("select rowid,created_at from feedback_events where rowid=21").fetchone() == (21, b"\x81")
        assert db.execute("select seq from sqlite_sequence where name='feedback_processing_transitions'").fetchone()[0] == 19
        native_new = db.execute("insert into feedback_events(key,feedback_token) values(null,'native') returning rowid").fetchone()[0]
        assert native_new == 23
        new_source = capture_action_feedback_source(db)
        migrate_action_feedback_schema(db, new_source)
        assert db.execute("select count(*) from actions where record_type='feedback_events'").fetchone()[0] == 3
        db.rollback()


def test_feedback_parent_endpoints_and_wrong_kind_guards():
    with _target() as db:
        db.execute("insert into tasks(id,record_type,feedback_iteration_decisions__id) values(11,'feedback_iteration_decisions',7)")
        db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) values(12,'feedback_processing_items','k')")
        db.execute("insert into tasks(id,record_type,feedback_processing_rounds__id) values(13,'feedback_processing_rounds',4)")
        good = {"decision_id": 7, "feedback_key": "k", "round_id": 4}
        for field, bad in (("decision_id", 8), ("feedback_key", "missing"), ("round_id", 5)):
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                insert_feedback_action(db, "feedback_iteration_decision_items", {**good, field: bad})
        for field, endpoint, bad in (("decision_id", "feedback_iteration_decisions__id", 8),
                                     ("feedback_key", "feedback_processing_items__feedback_key", "bad"),
                                     ("round_id", "feedback_processing_rounds__id", 5)):
            db.execute(f"insert into tasks(record_type,{endpoint}) values('wrong',?)", (bad,))
            with pytest.raises(sqlite3.IntegrityError, match="feedback parent type mismatch"):
                insert_feedback_action(db, "feedback_iteration_decision_items", {**good, field: bad})
        insert_feedback_action(db, "feedback_iteration_decision_items", good)


def test_feedback_ordinary_rowid_reuse_and_overflow():
    with _target() as db:
        for key in ("a", "b"):
            insert_feedback_action(db, "feedback_events", {"key": key, "feedback_token": "t"})
        db.execute("delete from actions where record_type='feedback_events' and legacy_key='2'")
        assert insert_feedback_action(db, "feedback_events",
                                      {"key": "c", "feedback_token": "t"}) == 2
        assert db.execute("select 1 from sqlite_sequence where name='actions:feedback_events'").fetchone() is None
        maximum = 2**63 - 1
        insert_feedback_action(db, "feedback_events",
                               {"key": "max", "feedback_token": "t"}, source_rowid=maximum)
        overflow_id = insert_feedback_action(db, "feedback_events",
                                             {"key": "overflow", "feedback_token": "t"})
        assert 0 < overflow_id < maximum
        db.execute("insert into tasks(id,record_type,feedback_iteration_decisions__id) values(11,'feedback_iteration_decisions',7)")
        db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) values(12,'feedback_processing_items','k')")
        db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) values(14,'feedback_processing_items','other')")
        db.execute("insert into tasks(id,record_type,feedback_processing_rounds__id) values(13,'feedback_processing_rounds',4)")
        insert_feedback_action(db, "feedback_iteration_decision_items",
                               {"decision_id": 7, "feedback_key": "k", "round_id": 4},
                               source_rowid=maximum)
        child_id = insert_feedback_action(db, "feedback_iteration_decision_items",
                                          {"decision_id": 7, "feedback_key": "other", "round_id": 4})
        assert 0 < child_id < maximum
        assert db.execute("select 1 from sqlite_sequence where name='actions:feedback_iteration_decision_items'").fetchone() is None


def test_feedback_event_upsert_preserves_existing_creation_and_resolution():
    with _target() as db:
        first = upsert_feedback_event(db, {"key": "k", "feedback_token": "a",
                                           "comment": "old", "created_at": b"\x80",
                                           "resolved_at": "resolved"})
        statements = []
        db.set_trace_callback(statements.append)
        again = upsert_feedback_event(db, {"key": "k", "feedback_token": "b",
                                           "comment": b"\xff", "created_at": "ignored",
                                           "resolved_at": "ignored"})
        db.set_trace_callback(None)
        assert len({statement for statement in statements
                    if statement.lower().startswith("update main.actions set payload_json=")
                    and "current_timestamp" in statement.lower()}) == 1
        assert not any(statement.lower().startswith("select current_timestamp") for statement in statements)
        assert again == first
        row = db.execute("select feedback_token,comment,created_at,resolved_at "
                         "from (" + feedback_projection("feedback_events") + ") where key='k'").fetchone()
        assert row == ("b", b"\xff", b"\x80", "resolved")
        assert upsert_feedback_event(db, {"key": None, "feedback_token": "n"}) != upsert_feedback_event(
            db, {"key": None, "feedback_token": "n"})
        db.execute("insert into tasks(id,record_type,feedback_iteration_decisions__id) values(11,'feedback_iteration_decisions',7)")
        db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) values(12,'feedback_processing_items','k')")
        db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) values(14,'feedback_processing_items','other')")
        db.execute("insert into tasks(id,record_type,feedback_processing_rounds__id) values(13,'feedback_processing_rounds',4)")
        ids = insert_feedback_decision_items(db, (
            {"decision_id": 7, "feedback_key": "k", "round_id": 4},
            {"decision_id": 7, "feedback_key": "other", "round_id": 4},
        ))
        assert ids == [1, 2]


def test_feedback_insert_defaults_share_one_sqlite_statement_clock():
    with _target() as db:
        statements = []
        db.set_trace_callback(statements.append)
        rowid = insert_feedback_action(db, "feedback_events",
                                       {"key": "clock", "feedback_token": "t"})
        db.set_trace_callback(None)
        assert len([statement for statement in statements
                    if statement.lower().startswith("select ") and
                    statement.lower().count("current_timestamp") == 2]) == 1
        row = db.execute("select created_at,updated_at from (" +
                         feedback_projection("feedback_events", with_rowid=True) +
                         ") where _source_rowid=?", (rowid,)).fetchone()
        assert row[0] == row[1]


def test_feedback_upsert_resets_unsupplied_business_fields_like_source():
    with _db() as native, _target() as target:
        initial = {"key": "k", "feedback_token": "old", "rating": "5",
                   "rating_label": "excellent", "comment": "old comment",
                   "original_text": "source", "reply_text": "reply",
                   "source": "web", "received_at": "yesterday", "raw_json": '{"x":1}'}
        native.execute("insert into feedback_events(key,feedback_token,rating,rating_label,"
                       "comment,original_text,reply_text,source,received_at,raw_json,"
                       "created_at,resolved_at) values(?,?,?,?,?,?,?,?,?,?,'old-clock','closed')",
                       tuple(initial.values()))
        rowid = upsert_feedback_event(target, {**initial, "created_at": "old-clock",
                                                "resolved_at": "closed"})
        native.execute("insert into feedback_events(key,feedback_token) values('k','new') "
                       "on conflict(key) do update set feedback_token=excluded.feedback_token,"
                       "rating=excluded.rating,rating_label=excluded.rating_label,comment=excluded.comment,"
                       "original_text=excluded.original_text,reply_text=excluded.reply_text,"
                       "source=excluded.source,received_at=excluded.received_at,"
                       "raw_json=excluded.raw_json,updated_at=current_timestamp")
        assert upsert_feedback_event(target, {"key": "k", "feedback_token": "new"}) == rowid
        native.execute("insert into feedback_events(key,feedback_token,rating,rating_label) "
                       "values('k','new','4','good') on conflict(key) do update set "
                       "feedback_token=excluded.feedback_token,rating=excluded.rating,"
                       "rating_label=excluded.rating_label,comment=excluded.comment,"
                       "original_text=excluded.original_text,reply_text=excluded.reply_text,"
                       "source=excluded.source,received_at=excluded.received_at,"
                       "raw_json=excluded.raw_json,updated_at=current_timestamp")
        assert upsert_feedback_event(target, {"key": "k", "feedback_token": "new",
                                              "rating": "4", "rating_label": "good"}) == rowid
        native_row = native.execute("select rowid,key,feedback_token,rating,rating_label,comment,"
                                    "original_text,reply_text,source,received_at,raw_json,"
                                    "created_at,resolved_at from feedback_events").fetchone()
        target_row = target.execute("select _source_rowid,key,feedback_token,rating,rating_label,"
                                    "comment,original_text,reply_text,source,received_at,raw_json,"
                                    "created_at,resolved_at from (" +
                                    feedback_projection("feedback_events", with_rowid=True) + ")").fetchone()
        assert target_row == native_row


def test_feedback_bound_null_id_allocates_with_existing_family_rows():
    nan = float("nan")
    with _db() as native, _target() as target:
        for db in (native, target):
            db.execute("insert into tasks(id,record_type,feedback_iteration_decisions__id) "
                       "values(11,'feedback_iteration_decisions',7)")
            db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) "
                       "values(12,'feedback_processing_items','a')")
            db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) "
                       "values(13,'feedback_processing_items','b')")
            db.execute("insert into tasks(id,record_type,feedback_processing_items__feedback_key) "
                       "values(15,'feedback_processing_items','c')")
            db.execute("insert into tasks(id,record_type,feedback_processing_rounds__id) "
                       "values(14,'feedback_processing_rounds',4)")
        native.execute("insert into feedback_events(rowid,key,feedback_token) values(41,'a','t')")
        insert_feedback_action(target, "feedback_events", {"key": "a", "feedback_token": "t"},
                               source_rowid=41)
        assert native.execute("insert into feedback_events(rowid,key,feedback_token) "
                              "values(?,'b','t') returning rowid", (nan,)).fetchone()[0] == 42
        assert insert_feedback_action(target, "feedback_events",
                                      {"key": "b", "feedback_token": "t"}, source_rowid=nan) == 42
        assert native.execute("insert into feedback_events(rowid,key,feedback_token) "
                              "values(?,'c','t') returning rowid", (None,)).fetchone()[0] == 43
        assert insert_feedback_action(target, "feedback_events",
                                      {"key": "c", "feedback_token": "t"}, source_rowid=None) == 43
        native.execute("insert into feedback_processing_transitions(id,feedback_key,to_status) "
                       "values(41,'a','pending')")
        insert_feedback_action(target, "feedback_processing_transitions",
                               {"id": 41, "feedback_key": "a", "to_status": "pending"})
        assert native.execute("insert into feedback_processing_transitions(id,feedback_key,to_status) "
                              "values(?,'b','resolved') returning id", (nan,)).fetchone()[0] == 42
        assert insert_feedback_action(target, "feedback_processing_transitions",
                                      {"id": nan, "feedback_key": "b", "to_status": "resolved"}) == 42
        assert native.execute("insert into feedback_processing_transitions(id,feedback_key,to_status) "
                              "values(?,'c','resolved') returning id", (None,)).fetchone()[0] == 43
        assert insert_feedback_action(target, "feedback_processing_transitions",
                                      {"id": None, "feedback_key": "c", "to_status": "resolved"}) == 43
        native.execute("insert into feedback_iteration_decision_items(rowid,decision_id,feedback_key,round_id) "
                       "values(41,7,'a',4)")
        insert_feedback_action(target, "feedback_iteration_decision_items",
                               {"decision_id": 7, "feedback_key": "a", "round_id": 4},
                               source_rowid=41)
        assert native.execute("insert into feedback_iteration_decision_items(rowid,decision_id,feedback_key,round_id) "
                              "values(?,7,'b',4) returning rowid", (nan,)).fetchone()[0] == 42
        assert insert_feedback_action(target, "feedback_iteration_decision_items",
                                      {"decision_id": 7, "feedback_key": "b", "round_id": 4},
                                      source_rowid=nan) == 42
        assert native.execute("insert into feedback_iteration_decision_items(rowid,decision_id,feedback_key,round_id) "
                              "values(?,7,'c',4) returning rowid", (None,)).fetchone()[0] == 43
        assert insert_feedback_action(target, "feedback_iteration_decision_items",
                                      {"decision_id": 7, "feedback_key": "c", "round_id": 4},
                                      source_rowid=None) == 43


def test_feedback_autoincrement_full_rolls_back_caller_transaction():
    maximum = 2**63 - 1
    with _db() as native, _target() as target:
        native.execute("insert into feedback_processing_transitions(id,feedback_key,to_status) "
                       "values(?,'k','pending')", (maximum,))
        native.execute("delete from feedback_processing_transitions")
        target.execute("insert into actions(record_type,legacy_key,payload_json) values("
                       "'feedback_processing_transitions',?,?)",
                       (str(maximum), action_storage._native_payload({
                           "feedback_key": "k", "round_id": 0, "batch_id": "",
                           "from_status": "", "to_status": "pending", "reason": "",
                           "workbench_task_id": "", "workbench_turn_id": "",
                           "created_at": "clock"})))
        target.execute("delete from actions where record_type='feedback_processing_transitions'")
        native.commit()
        target.commit()
        for db, use_actions in ((native, False), (target, True)):
            db.execute("begin")
            if use_actions:
                insert_feedback_action(db, "feedback_events", {"key": "rollback", "feedback_token": "t"})
            else:
                db.execute("insert into feedback_events(key,feedback_token) values('rollback','t')")
            with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
                if use_actions:
                    insert_feedback_action(db, "feedback_processing_transitions",
                                           {"feedback_key": "k", "to_status": "pending"})
                else:
                    db.execute("insert into feedback_processing_transitions(feedback_key,to_status) "
                               "values('k','pending')")
            assert db.in_transaction is False
            assert db.execute("select count(*) from " +
                              ("actions where record_type='feedback_events'" if use_actions else
                               "feedback_events")).fetchone()[0] == 0
