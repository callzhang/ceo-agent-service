"""Independent delivery target checks against captured source behavior."""

from contextlib import closing
import hashlib
import json
import sqlite3

import pytest

from app.action_storage import (
    ACTION_PROJECTIONS, action_physical_id, action_schema_is_current,
    create_action_schema, delete_action, insert_action, update_action,
)
from app import action_storage as storage


@pytest.fixture
def db():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("pragma foreign_keys=on")
        conn.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
        conn.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
        conn.executemany("insert into runs values(?,?,?)", [(11, "agent_runs", "11"), (12, "agent_runs", "12"), (19, "other", "19")])
        conn.executemany("insert into tasks values(?,?,?)", [(21, "reply_tasks", "21"), (29, "other", "29")])
        create_action_schema(conn, include_delivery=True)
        yield conn


def sent(db, **values):
    return insert_action(db, "sent_replies", {
        "conversation_id": "c", "trigger_message_id": "m", "reply_text": "body", **values,
    }).lastrowid


def test_delivery_is_opt_in_and_preserves_source_defaults(db):
    assert action_schema_is_current(db, include_delivery=True)
    assert not action_schema_is_current(db, include_postfix=True)
    sid = sent(db)
    row = db.execute(ACTION_PROJECTIONS["sent_replies"] + " and id=?", (sid,)).fetchone()
    assert row[0] == sid
    assert row[1:4] == ("c", "m", "body")
    assert row[5:] == ("", "", "", "", None, "", "dingtalk", 0, "")


def test_frozen_twelve_type_declaration_and_allocator_namespace():
    objects = storage._current_objects(True, True)
    digest = hashlib.sha256(json.dumps(objects, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == "10c7e892d3d600b294b55a2bbc6d1a21a736037e7707d15974da7920193eefd3"
    assert storage.ACTION_SEQUENCE_NAMES == {
        "agent_effect_intents": "actions:agent_effect_intents",
        "agent_execution_receipts": "actions:agent_execution_receipts",
    }


def test_sent_integer_lookup_uses_numeric_index_without_observer_cast(db):
    sid = sent(db)
    insert_action(db, "sent_reply_observers", {"sent_reply_id": sid, "agent_run_id": 11,
                                                "reply_task_id": 21})
    plan = db.execute("explain query plan select id from actions where " +
                      storage._identity_predicate("sent_replies"),
                      storage._identity_arguments("sent_replies", sid)).fetchall()
    assert any("idx_actions_public_id" in row[3] for row in plan)
    assert action_physical_id(db, "sent_reply_observers", (sid, 11)) != action_physical_id(db, "sent_replies", sid)


def test_observer_has_three_immediate_no_action_physical_references(db):
    refs = {(row[2], row[3], row[5], row[6]) for row in db.execute("pragma foreign_key_list(actions)")}
    assert ("actions", "sent_reply_id", "NO ACTION", "NO ACTION") in refs
    assert ("runs", "agent_run_id", "NO ACTION", "NO ACTION") in refs
    assert ("tasks", "reply_task_id", "NO ACTION", "NO ACTION") in refs
    triggers = {row[0] for row in db.execute(
        "select name from sqlite_master where type='trigger' and tbl_name='actions'")}
    for field in ("agent_run_id", "reply_task_id"):
        assert {f"trg_actions_{field}_{event}" for event in ("insert", "update")} <= triggers


@pytest.mark.parametrize("run_id", [0, 404, 19])
def test_sent_run_is_scalar_and_raw_text_survives(db, run_id):
    sid = sent(db, agent_run_id=run_id, send_result_json="not JSON\0", channel="custom\0",
               external_action_key=" key ", recalled_at=None)
    assert db.execute(ACTION_PROJECTIONS["sent_replies"] + " and id=?", (sid,)).fetchone()[12:] == (run_id, " key ")
    update_action(db, "sent_replies", sid, {"agent_run_id": 404, "recalled_at": "raw\0"})
    assert db.execute("select count(*) from actions where record_type='sent_replies' and agent_run_id is null").fetchone() == (1,)
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_all_sent_source_fields_keep_exact_raw_values(db):
    values = {"conversation_id": " c\0 ", "trigger_message_id": " m\0 ",
              "reply_text": " body\n\0 ", "sent_at": " raw sent\0 ",
              "send_result_json": " not JSON {\0 ", "recall_key": " key\0 ",
              "recall_status": " status\0 ", "recall_error": " error\0 ",
              "recalled_at": " raw recalled\0 ", "feedback_token": " token\0 ",
              "channel": " custom\0 ", "agent_run_id": 404,
              "external_action_key": " external\0 "}
    sid = sent(db, **values)
    columns = ("conversation_id", "trigger_message_id", "reply_text", "sent_at",
               "send_result_json", "recall_key", "recall_status", "recall_error",
               "recalled_at", "feedback_token", "channel", "agent_run_id", "external_action_key")
    result = db.execute(ACTION_PROJECTIONS["sent_replies"] + " and id=?", (sid,)).fetchone()
    assert result[1:] == tuple(values[field] for field in columns)
    update_action(db, "sent_replies", sid, {"recalled_at": None, "recall_status": "recalled"})
    result = db.execute(ACTION_PROJECTIONS["sent_replies"] + " and id=?", (sid,)).fetchone()
    assert result[9] is None and result[7] == "recalled"


def test_external_key_binary_partial_unique_and_allocator(db):
    first = sent(db, external_action_key="key")
    sent(db, external_action_key=" key ")
    with pytest.raises(sqlite3.IntegrityError):
        sent(db, external_action_key="key")
    delete_action(db, "sent_replies", first)
    assert sent(db) > first
    explicit = sent(db, id=100)
    assert explicit == 100
    assert sent(db) == 101


def test_observer_composite_identity_and_parent_guards(db):
    sid = sent(db)
    observer = {"sent_reply_id": sid, "agent_run_id": 11, "reply_task_id": 21}
    assert insert_action(db, "sent_reply_observers", observer).lastrowid == (sid, 11)
    assert action_physical_id(db, "sent_reply_observers", (sid, 11)) is not None
    with pytest.raises(sqlite3.IntegrityError):
        insert_action(db, "sent_reply_observers", observer)
    with pytest.raises(sqlite3.IntegrityError):
        insert_action(db, "sent_reply_observers", observer | {"agent_run_id": 19})
    with pytest.raises(sqlite3.IntegrityError):
        insert_action(db, "sent_reply_observers", observer | {"reply_task_id": 29})
    with pytest.raises(sqlite3.IntegrityError):
        update_action(db, "sent_replies", sid, {"id": sid + 1})
    assert insert_action(db, "sent_reply_observers", observer | {"agent_run_id": 12}).lastrowid == (sid, 12)


def test_sent_sequence_advances_on_ignored_explicit_ids(db):
    sent(db, id=700, external_action_key="deleted")
    delete_action(db, "sent_replies", 700)
    assert sent(db, external_action_key="duplicate") == 701
    assert insert_action(db, "sent_replies", {"id": 900, "conversation_id": "c", "trigger_message_id": "m",
                                               "reply_text": "body", "external_action_key": "duplicate"}, ignore=True).rowcount == 0
    assert sent(db) == 901
    assert db.execute("select seq from sqlite_sequence where name='actions:sent_replies'").fetchone() == (901,)


def test_ignored_automatic_collision_and_max_exhaustion(db):
    sent(db, external_action_key="duplicate")
    ignored = insert_action(db, "sent_replies", {"conversation_id": "c", "trigger_message_id": "m",
                                                 "reply_text": "body", "external_action_key": "duplicate"}, ignore=True)
    assert ignored.rowcount == 0
    assert sent(db) == 3
    ignored = insert_action(db, "sent_replies", {"id": 2**63 - 1, "conversation_id": "c",
                                                 "trigger_message_id": "m", "reply_text": "body",
                                                 "external_action_key": "duplicate"}, ignore=True)
    assert ignored.rowcount == 0
    db.commit()
    with pytest.raises(sqlite3.DatabaseError) as error:
        sent(db)
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL


def test_observer_rebind_preserves_direct_task_and_pair(db):
    first, second = sent(db), sent(db)
    insert_action(db, "sent_reply_observers", {"sent_reply_id": first, "agent_run_id": 11,
                                                "reply_task_id": 21, "created_at": "raw\0time"})
    update_action(db, "sent_reply_observers", (first, 11), {"sent_reply_id": second, "agent_run_id": 12})
    assert action_physical_id(db, "sent_reply_observers", (second, 12)) is not None
    assert db.execute(ACTION_PROJECTIONS["sent_reply_observers"]).fetchone() == (second, 12, 21, "raw\0time")
    update_action(db, "sent_replies", first, {"id": 100})
    delete_action(db, "sent_replies", 100)
    assert not db.execute("pragma foreign_key_check").fetchall()
