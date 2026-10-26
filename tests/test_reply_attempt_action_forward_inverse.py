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
