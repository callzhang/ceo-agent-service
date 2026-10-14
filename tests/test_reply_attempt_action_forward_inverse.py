"""Current-fact round trips for the opt-in reply action migration."""

from contextlib import closing
import copy
import sqlite3
import subprocess
import sys

import pytest

from app import action_storage
from app.action_reply_storage import migrate_action_reply_schema, restore_action_reply_schema
from tests.test_reply_attempt_action_migration import _database


def _sent_observer_shape(db):
    db.execute("alter table tasks add column legacy_key text")
    db.execute("alter table runs add column legacy_key text")
    db.execute("insert into tasks(id,record_type,legacy_key) values(1,'reply_tasks','1')")
    db.execute("insert into runs(id,record_type,legacy_key) values(1,'agent_runs','1')")
    for sent_id in (1, 2):
        action_storage.insert_action(db, "sent_replies", {
            "id": sent_id, "conversation_id": "c", "trigger_message_id": str(sent_id),
            "reply_text": f"sent {sent_id}",
        })
        if sent_id == 1:
            action_storage.insert_action(db, "sent_reply_observers", {
                "sent_reply_id": 1, "agent_run_id": 1, "reply_task_id": 1,
            })
    assert db.execute("select id,record_type from actions order by id").fetchall() == [
        (1, "sent_replies"), (2, "sent_reply_observers"), (3, "sent_replies")]


def _physical_fact_snapshot(db):
    columns = [row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0
               and row[1] != "memory_attempt_ref"]
    values = db.execute(f"select {','.join(columns)} from actions where record_type in "
                        "('sent_replies','sent_reply_observers') order by id").fetchall()
    types = db.execute(f"select {','.join('typeof(' + col + ')' for col in columns)} "
                       "from actions where record_type in "
                       "('sent_replies','sent_reply_observers') order by id").fetchall()
    waters = db.execute("select name,seq from sqlite_sequence where name='actions:sent_replies'").fetchall()
    return values, types, waters


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
def test_forward_copies_rebound_observer_before_its_later_physical_parent(tmp_path, variant):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, variant)) as db:
        _sent_observer_shape(db)
        action_storage.update_action(db, "sent_reply_observers", (1, 1), {"sent_reply_id": 2})
        before = _physical_fact_snapshot(db)
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        migrate_action_reply_schema(db)
        assert _physical_fact_snapshot(db) == before
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
def test_inverse_copies_current_rebound_observer_before_its_later_parent(tmp_path, variant):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, variant)) as db:
        _sent_observer_shape(db)
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        action_storage.update_action(db, "sent_reply_observers", (1, 1), {"sent_reply_id": 2})
        before = _physical_fact_snapshot(db)
        restore_action_reply_schema(db, state)
        assert _physical_fact_snapshot(db) == before
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
def test_reply_forward_inverse_preserves_current_facts_and_allocator(tmp_path, variant):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, variant)) as db:
        db.execute("insert into tasks(id,record_type) values(88,'unrelated')")
        unrelated = db.execute("select * from tasks").fetchall()
        action_storage.insert_action(db, "sent_replies", {
            "id": 19, "conversation_id": "old", "trigger_message_id": "old",
            "reply_text": "retained",
        })
        action_columns = [row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0]
        before_action = db.execute(f"select {','.join(action_columns)} from actions").fetchall()
        attempt_fields = {"id": 7, "conversation_id": "c", "conversation_title": "t",
                          "trigger_message_id": "m", "trigger_sender": "s", "trigger_text": "x",
                          "action": "reply", "sensitivity_kind": "ordinary", "send_status": "pending",
                          "audit_tool_events_json": '[{"tool":"audit_review","result":"queued"},'
                                                    '{"tool":"x","result":"body"},'
                                                    '{"tool":"audit_review","result":"queued"}]',
                          "final_reply_text": b"raw\x00body", "send_error": "broken {"}
        if variant == "actual":
            attempt_fields["universal_execution_scope_id"] = "historical"
        db.execute(f"insert into reply_attempts({','.join(attempt_fields)}) values"
                   f"({','.join('?' for _ in attempt_fields)})", tuple(attempt_fields.values()))
        db.execute("insert into memory_write_events(id,attempt_id,event_type,payload_json)"
                   " values(7,7,'write',?)", (b"raw\x00payload",))
        db.execute("update sqlite_sequence set seq=30 where name='reply_attempts'")
        db.execute("update sqlite_sequence set seq=40 where name='memory_write_events'")
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        assert action_storage.action_schema_is_current(
            db, include_reply=True, reply_variant=("actual_no_memory_fk" if variant == "actual"
                                                   else "fresh_memory_fk"))
        assert db.execute(f"select {','.join(action_columns)} from actions where id=1").fetchone() == before_action[0]
        projection = action_storage.reply_action_projection("reply_attempts", state["target_variant"])
        migrated = db.execute(f"select audit_tool_events_json,final_reply_text,send_error from ({projection})").fetchone()
        assert migrated == ('[{"tool":"audit_review","result":"queued"},'
                            '{"tool":"audit_review","result":"queued"}]', b"raw\x00body", "broken {")
        assert db.execute("select count(*) from sqlite_schema where name in"
                          " ('reply_attempts','memory_write_events')").fetchone()[0] == 0
        action_storage.update_action(db, "reply_attempts", 7, {"final_reply_text": "new reply"})
        action_storage.insert_action(db, "reply_attempts", attempt_fields | {"id": 31,
                                   "audit_tool_events_json": "[]"})
        action_storage.insert_action(db, "memory_write_events", {
            "id": 41, "attempt_id": 31, "event_type": "new", "payload_json": "private",
        })
        action_storage.insert_action(db, "sent_replies", {
            "id": 20, "conversation_id": "current", "trigger_message_id": "m",
            "reply_text": "current action",
        })
        current_action = db.execute(f"select {','.join(action_columns)} from actions "
                                    "where record_type='sent_replies' and legacy_key='20'").fetchone()
        action_storage.update_action(db, "memory_write_events", 7, {
            "attempt_id": 31, "status": "completed", "memory_episode_id": "receipt-7",
        })
        action_storage.update_action(db, "reply_attempts", 31, {
            "audit_tool_events_json": '[{"tool":"audit_review","result":"queued"}]',
            "reviewer_feedback": "revised",
        })
        restore_action_reply_schema(db, state)
        assert db.execute("select * from tasks").fetchall() == unrelated
        assert db.execute(f"select {','.join(action_columns)} from actions "
                          "where record_type='sent_replies' and legacy_key='20'").fetchone() == current_action
        assert db.execute("select final_reply_text from reply_attempts where id=7").fetchone() == ("new reply",)
        assert db.execute("select audit_tool_events_json from reply_attempts where id=7").fetchone() == (
            '[{"tool":"audit_review","result":"queued"},'
            '{"tool":"audit_review","result":"queued"}]',)
        assert db.execute("select payload_json from memory_write_events where id=7").fetchone() == ("{}",)
        assert db.execute("select attempt_id,status,memory_episode_id from memory_write_events "
                          "where id=7").fetchone() == (31, "completed", "receipt-7")
        assert db.execute("select attempt_id from memory_write_events where id=41").fetchone() == (31,)
        assert db.execute("select audit_tool_events_json,reviewer_feedback from reply_attempts "
                          "where id=31").fetchone() == (
                              '[{"tool":"audit_review","result":"queued"}]', "revised")
        assert db.execute("select seq from sqlite_sequence where name='reply_attempts'").fetchone() == (31,)
        assert db.execute("select seq from sqlite_sequence where name='memory_write_events'").fetchone() == (41,)
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
def test_empty_then_deleted_high_water_round_trip_and_reforward(tmp_path, variant):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, variant)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        assert all(db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone() is None
                   for name in action_storage.REPLY_SEQUENCE_NAMES.values())
        fields = {"conversation_id": "c", "conversation_title": "t", "trigger_message_id": "m",
                  "trigger_sender": "s", "trigger_text": "x", "action": "reply",
                  "sensitivity_kind": "ordinary", "send_status": "pending"}
        action_storage.insert_action(db, "reply_attempts", fields | {"id": 100})
        action_storage.insert_action(db, "memory_write_events", {
            "id": 200, "attempt_id": 100, "event_type": "write",
        })
        db.execute("delete from actions where record_type='memory_write_events'")
        db.execute("delete from actions where record_type='reply_attempts'")
        restore_action_reply_schema(db, state)
        assert db.execute("select seq from sqlite_sequence where name='reply_attempts'").fetchone() == (100,)
        assert db.execute("select seq from sqlite_sequence where name='memory_write_events'").fetchone() == (200,)
        assert db.execute("select count(*) from reply_attempts").fetchone() == (0,)
        migrate_action_reply_schema(db)
        assert action_storage.insert_action(db, "reply_attempts", fields).lastrowid == 101
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
def test_blob_marker_and_source_type_values_survive_forward_inverse(tmp_path, variant):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, variant)) as db:
        fields = {"id": 5, "conversation_id": b"raw\x00id", "conversation_title": "t",
                  "trigger_message_id": "m", "trigger_sender": "s", "trigger_text": "x",
                  "action": "reply", "sensitivity_kind": "ordinary", "send_status": "pending",
                  "audit_tool_events_json": b'[{"tool":"audit_review","result":"queued"},'
                                            b'{"tool":"other","result":"secret"}]',
                  "draft_reply_text": b"\x00binary", "codex_transcript_end_line": float("inf"),
                  "agent_run_id": 987654321}
        db.execute(f"insert into reply_attempts({','.join(fields)}) values"
                   f"({','.join('?' for _ in fields)})", tuple(fields.values()))
        db.execute("insert into memory_write_events(id,attempt_id,event_type,payload_json,"
                   "last_error) values(5,5,'write',?,?)", (b"\x00secret", b"\x00err"))
        source_columns = [row[1] for row in db.execute("pragma table_xinfo(reply_attempts)")]
        expected = list(db.execute(f"select {','.join(source_columns)} from reply_attempts").fetchone())
        expected[source_columns.index("audit_tool_events_json")] = \
            '[{"tool":"audit_review","result":"queued"}]'
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        projection = action_storage.reply_action_projection("reply_attempts", state["target_variant"])
        assert db.execute(f"select * from ({projection})").fetchone() == tuple(expected)
        restore_action_reply_schema(db, state)
        assert db.execute(f"select {','.join(source_columns)} from reply_attempts").fetchone() == tuple(expected)
        assert db.execute("select payload_json,last_error from memory_write_events").fetchone() == (
            "{}", b"\x00err")
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
def test_unknown_inverse_metadata_and_target_are_refused_before_mutation(tmp_path, variant):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, variant)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        before = list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name"))
        bad = copy.deepcopy(state)
        bad["source_tables"]["reply_attempts"]["sql"] += " "
        with pytest.raises(ValueError, match="unsupported"):
            restore_action_reply_schema(db, bad)
        assert list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name")) == before
        db.execute("create view unrelated as select 1")
        with pytest.raises(ValueError, match="unsupported"):
            restore_action_reply_schema(db, state | {"source_variant": "unknown"})
        assert db.execute("select name from sqlite_schema where name='unrelated'").fetchone() == ("unrelated",)
        db.rollback()


def test_actual_orphan_memory_scalar_and_max_water_survive_round_trip(tmp_path):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, "actual")) as db:
        db.execute("insert into memory_write_events(id,attempt_id,event_type,payload_json) "
                   "values(9,987654321,'orphan','{}')")
        db.execute("insert into sqlite_sequence(name,seq) values('reply_attempts',9223372036854775807)")
        db.execute("update sqlite_sequence set seq=9223372036854775807 "
                   "where name='memory_write_events'")
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        assert db.execute("select memory_attempt_id from actions where "
                          "record_type='memory_write_events'").fetchone() == (987654321,)
        assert db.execute("select seq from sqlite_sequence where name='actions:reply_attempts'").fetchone() == (
            9223372036854775807,)
        restore_action_reply_schema(db, state)
        assert db.execute("select attempt_id from memory_write_events where id=9").fetchone() == (987654321,)
        assert db.execute("select seq from sqlite_sequence where name='reply_attempts'").fetchone() == (
            9223372036854775807,)
        assert db.execute("select seq from sqlite_sequence where name='memory_write_events'").fetchone() == (
            9223372036854775807,)
        db.rollback()


def test_forward_target_name_collision_is_refused_without_schema_write(tmp_path):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, "actual")) as db:
        db.execute("create index idx_actions_reply_status on tasks(record_type)")
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        before = list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name"))
        with pytest.raises(ValueError, match="collision"):
            migrate_action_reply_schema(db)
        assert list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name")) == before
        db.rollback()


def test_inverse_unknown_target_declaration_is_refused_without_schema_write(tmp_path):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, "actual")) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        db.execute("create index unrelated_actions_index on actions(record_type)")
        before = list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name"))
        with pytest.raises(ValueError, match="unsupported"):
            restore_action_reply_schema(db, state)
        assert list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name")) == before
        db.rollback()


def test_forward_requires_caller_owned_fk_off_transaction(tmp_path):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, "actual")) as db:
        before = list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name"))
        with pytest.raises(ValueError, match="transaction"):
            migrate_action_reply_schema(db)
        db.execute("begin")
        with pytest.raises(ValueError, match="transaction"):
            migrate_action_reply_schema(db)
        assert list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by name")) == before
        db.rollback()


def test_restored_source_can_reforward_in_fresh_process(tmp_path):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, "actual")) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_action_reply_schema(db)
        restore_action_reply_schema(db, state)
        db.commit()
    code = (
        "import sqlite3,sys\n"
        "from app.action_reply_storage import migrate_action_reply_schema\n"
        "db=sqlite3.connect(sys.argv[1])\n"
        "db.execute('pragma foreign_keys=off')\n"
        "db.execute('begin immediate')\n"
        "migrate_action_reply_schema(db)\n"
        "db.commit()\n"
        "db.close()\n"
    )
    subprocess.run((sys.executable, "-c", code, str(path)), check=True, capture_output=True)
    with closing(sqlite3.connect(path)) as db:
        assert action_storage.action_schema_is_current(
            db, include_reply=True, reply_variant="actual_no_memory_fk")
