"""The native error table and opt-in Action projection have the same facts."""

import json
import sqlite3
import copy
from contextlib import closing

import pytest

from app import action_storage
from app.action_error_definitions import error_projection
from app.action_followup_sync_storage import LINK_TRIGGER_SQL
from app.action_error_storage import (
    capture_action_error_source,
    migrate_action_error_schema,
    restore_action_error_schema,
)


SOURCE_SQL = """CREATE TABLE errors (
                    id integer primary key autoincrement,
                    conversation_id text,
                    message_id text,
                    kind text not null,
                    detail text not null,
                    created_at text not null default current_timestamp
                , resolved_at text not null default '', resolution text not null default '')"""
INDEX_SQL = (
    """CREATE INDEX idx_errors_unresolved
                    on errors(conversation_id, kind, message_id, id)
                    where coalesce(resolved_at, '')=''
                """,
    """CREATE INDEX idx_errors_reply_task_settlement
                    on errors(conversation_id, message_id, kind)
                """,
)


def _db(path, *, fresh=False):
    db = sqlite3.connect(path)
    db.execute("create table tasks(id integer primary key,record_type text,task_public_id integer)")
    db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
    db.execute("create table business(id integer primary key,record_type text,task_public_id integer)")
    db.execute("create table business_legacy_links(id integer primary key,record_type text,task_public_id integer,work_update_id integer)")
    db.execute("insert into runs(id,record_type,legacy_key) values(1,'agent_runs','1')")
    action_storage.create_action_schema(
        db, include_reply=True, reply_variant=("fresh_memory_fk" if fresh else "actual_no_memory_fk"),
        include_email=True, include_followup_sync=True,
        include_business_sync=True, include_skill_config_receipts=True,
        include_task_delivery=True,
    )
    for sql in LINK_TRIGGER_SQL[:2]:
        db.execute(sql)
    action_storage.insert_action(db, "external_action_results", {
        "external_action_key": "retained-key", "business_object_key": "business",
        "action_identity": "action", "operation": "save",
        "target_identifiers_json": "{}", "provider_result_json": "{}",
        "result_digest": "digest", "first_agent_run_id": 1,
        "created_at": "retained-now",
    })
    db.execute("update actions set id=300 where record_type='external_action_results'")
    if fresh:
        from app.action_error_storage import SOURCE_TABLE_SQL
        db.execute(SOURCE_TABLE_SQL[1])
    else:
        db.execute(SOURCE_SQL)
    for sql in INDEX_SQL:
        db.execute(sql)
    db.execute("insert into errors(id,conversation_id,message_id,kind,detail) values(7,null,'', 'failure','原文')")
    db.execute("insert into errors(id,conversation_id,message_id,kind,detail) values(12,'c','m','error','quoted '' text')")
    db.execute("delete from errors where id=12")
    db.commit()
    return db


def _schema(db):
    return tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema where sql is not null order by type,name"))


@pytest.mark.parametrize("fresh", (False, True))
def test_error_component_roundtrip_preserves_current_facts_and_schema(tmp_path, fresh):
    with closing(_db(tmp_path / "errors.db", fresh=fresh)) as db:
        before = _schema(db)
        before_seq = tuple(db.execute("select name,seq from sqlite_sequence order by name"))
        retained = db.execute("select id,payload_json from actions where record_type='external_action_results'").fetchone()
        source = capture_action_error_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_action_error_schema(db, source)
        assert db.execute("select legacy_key from actions where record_type='errors'").fetchall() == [("7",)]
        assert db.execute("select id from actions where record_type='errors'").fetchone()[0] != 7
        assert db.execute("select id,payload_json from actions where record_type='external_action_results'").fetchone() == retained
        db.execute("insert into actions(id,record_type,legacy_key,payload_json) values(100,'errors','13',?)", (action_storage._native_payload({"conversation_id": "c", "message_id": "m", "kind": "error", "detail": "new", "created_at": "now", "resolved_at": "", "resolution": ""}),))
        db.execute("update actions set payload_json=? where record_type='errors' and legacy_key='7'", (action_storage._native_payload({"conversation_id": None, "message_id": "", "kind": "failure", "detail": "changed", "created_at": "now", "resolved_at": "", "resolution": ""}),))
        result = restore_action_error_schema(db, json.loads(json.dumps(state)))
        assert result["count"] == 2
        assert _schema(db) == before
        assert dict(db.execute("select id,detail from errors")) == {7: "changed", 13: "new"}
        assert db.execute("select id,payload_json from actions where record_type='external_action_results'").fetchone() == retained
        assert dict(db.execute("select name,seq from sqlite_sequence"))["errors"] == 13
        assert dict(before_seq)["errors"] == 12
        db.rollback()


def test_error_component_preserves_scalar_affinity_and_deleted_high_water(tmp_path):
    with closing(_db(tmp_path / "scalar.db")) as db:
        db.execute("insert into errors(id,conversation_id,message_id,kind,detail,created_at,resolved_at,resolution) values(20,42,x'00ff',x'01',x'feff',x'80','',x'00')")
        db.execute("insert into errors(id,kind,detail) values(27,'temporary','x')")
        db.execute("delete from errors where id=27")
        original = tuple(db.execute("select id,conversation_id,message_id,kind,detail,created_at,resolved_at,resolution from errors order by id"))
        types = tuple(db.execute("select typeof(conversation_id),typeof(message_id),typeof(kind),typeof(detail),typeof(created_at),typeof(resolution) from errors order by id"))
        db.commit()
        source = capture_action_error_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_action_error_schema(db, source)
        from app.action_error_storage import _projected_facts
        assert _projected_facts(db)[0] == list(original)
        assert tuple(db.execute("select typeof(conversation_id),typeof(message_id),typeof(kind),typeof(detail),typeof(created_at),typeof(resolution) from (" + error_projection() + ") order by id")) == types
        restore_action_error_schema(db, state)
        assert tuple(db.execute("select id,conversation_id,message_id,kind,detail,created_at,resolved_at,resolution from errors order by id")) == original
        assert dict(db.execute("select name,seq from sqlite_sequence"))["errors"] == 27
        db.rollback()


def test_error_component_rejects_altered_source_without_mutation(tmp_path):
    with closing(_db(tmp_path / "altered.db")) as db:
        db.execute("drop index idx_errors_unresolved")
        before = _schema(db)
        with pytest.raises(ValueError, match="unsupported error source"):
            capture_action_error_source(db)
        assert _schema(db) == before


def test_error_component_keeps_both_native_lookup_orders(tmp_path):
    with closing(_db(tmp_path / "lookups.db")) as db:
        db.execute("insert into errors(id,conversation_id,message_id,kind,detail) values(18,'c','m','error','later')")
        db.commit()
        native_settlement = db.execute("select id from errors where conversation_id='c' and message_id='m' and kind='error' order by id").fetchall()
        native_unresolved = db.execute("select id from errors where conversation_id='c' and kind='error' and coalesce(resolved_at,'')='' order by id").fetchall()
        assert "idx_errors_reply_task_settlement" in str(db.execute("explain query plan select id from errors where conversation_id='c' and message_id='m' and kind='error'").fetchall())
        assert "idx_errors_unresolved" in str(db.execute("explain query plan select id from errors where conversation_id='c' and kind='error' and coalesce(resolved_at,'')='' ").fetchall())
        source = capture_action_error_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        migrate_action_error_schema(db, source)
        settlement = db.execute("select cast(legacy_key as integer) from actions where record_type='errors' and error_conversation_id='c' and error_message_id='m' and error_kind='error' order by cast(legacy_key as integer)").fetchall()
        unresolved = db.execute("select cast(legacy_key as integer) from actions where record_type='errors' and error_conversation_id='c' and error_kind='error' and coalesce(error_resolved_at,'')='' order by cast(legacy_key as integer)").fetchall()
        assert settlement == native_settlement
        assert unresolved == native_unresolved
        assert "idx_actions_errors_reply_task_settlement" in str(db.execute("explain query plan select id from actions where record_type='errors' and error_conversation_id='c' and error_message_id='m' and error_kind='error'").fetchall())
        assert "idx_actions_errors_unresolved" in str(db.execute("explain query plan select id from actions where record_type='errors' and error_conversation_id='c' and error_kind='error' and coalesce(error_resolved_at,'')='' ").fetchall())
        db.rollback()


def test_error_component_failure_rolls_back_without_schema_ghosts(tmp_path):
    with closing(_db(tmp_path / "rollback.db")) as db:
        before = _schema(db)
        source = capture_action_error_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        migrate_action_error_schema(db, source)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(record_type,legacy_key,payload_json) values('errors','55',?)", (action_storage._native_payload({"conversation_id": None, "message_id": None, "kind": None, "detail": "bad", "created_at": "now", "resolved_at": "", "resolution": ""}),))
        db.rollback()
        assert _schema(db) == before
        assert db.execute("select count(*) from errors").fetchone()[0] == 1
        assert not db.execute("select 1 from sqlite_schema where name like '_email_dependency_%'").fetchone()


def test_error_component_preserves_native_allocator_exhaustion(tmp_path):
    with closing(_db(tmp_path / "overflow.db")) as db:
        maximum = 2**63 - 1
        db.execute("insert into errors(id,kind,detail) values(?, 'overflow','x')", (maximum,))
        db.execute("delete from errors where id=?", (maximum,))
        db.commit()
        source = capture_action_error_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_action_error_schema(db, source)
        assert dict(db.execute("select name,seq from sqlite_sequence"))["actions:errors"] == maximum
        restore_action_error_schema(db, state)
        assert dict(db.execute("select name,seq from sqlite_sequence"))["errors"] == maximum
        with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
            db.execute("insert into errors(kind,detail) values('next','x')")
        db.rollback()


@pytest.mark.parametrize("fresh", (False, True))
def test_error_component_rejects_every_unrelated_writable_reference(tmp_path, fresh):
    with closing(_db(tmp_path / "references.db", fresh=fresh)) as db:
        source = capture_action_error_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        migrate_action_error_schema(db, source)
        foreign_keys = {row[3] for row in db.execute("pragma main.foreign_key_list(actions)")}
        writable = {row[1] for row in db.execute("pragma main.table_xinfo(actions)") if row[6] == 0}
        unused = sorted(foreign_keys & writable)
        assert "first_agent_run_id" in unused
        assert "sync_draft_action_id" in unused
        assert "td_reply_task_id" in unused
        assert "skill_revision_ref" in unused
        assert ("memory_attempt_ref" in unused) == fresh
        payload = action_storage._native_payload({"conversation_id": None, "message_id": None,
            "kind": "fault", "detail": "body", "created_at": "now", "resolved_at": "",
            "resolution": ""})
        for index, column in enumerate(unused, start=1):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"insert into actions(record_type,legacy_key,payload_json,{column}) values('errors',?,?,1)",
                           (str(1000 + index), payload))
        db.rollback()


@pytest.mark.parametrize("change", ("missing", "reordered", "extra", "duplicate"))
def test_error_component_rejects_tampered_inverse_columns_before_ddl(tmp_path, change):
    with closing(_db(tmp_path / "inverse-columns.db")) as db:
        source = capture_action_error_source(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        state = migrate_action_error_schema(db, source)
        before = _schema(db)
        retained = db.execute("select id,payload_json from actions where record_type='external_action_results'").fetchone()
        assert retained[0] == 300
        bad = copy.deepcopy(state)
        columns = list(bad["action_columns"])
        if change == "missing":
            columns.remove("id")
        elif change == "reordered":
            columns[0], columns[1] = columns[1], columns[0]
        elif change == "extra":
            columns.append("error_detail")
        else:
            columns.append(columns[0])
        bad["action_columns"] = columns
        with pytest.raises(ValueError, match="inverse metadata"):
            restore_action_error_schema(db, bad)
        assert _schema(db) == before
        assert db.execute("select id,payload_json from actions where record_type='external_action_results'").fetchone() == retained
        db.rollback()
