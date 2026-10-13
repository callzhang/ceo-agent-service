"""Independent sent-delivery source oracle; no Store or target code imported."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest


INVENTORY = Path(__file__).parents[1] / (
    "docs/superpowers/specs/2026-10-13-database-redesign-phase5d-delivery-source.json"
)
_INVENTORY = json.loads(INVENTORY.read_text())
SOURCE = {variant: _INVENTORY[variant]["tables"] for variant in ("actual", "fresh_candidate")}
SENT_COLUMNS = (
    "id", "conversation_id", "trigger_message_id", "reply_text", "sent_at",
    "send_result_json", "recall_key", "recall_status", "recall_error", "recalled_at",
    "feedback_token", "channel", "agent_run_id", "external_action_key",
)
OBSERVER_COLUMNS = ("sent_reply_id", "agent_run_id", "reply_task_id", "created_at")


@pytest.fixture(params=tuple(SOURCE))
def source(request):
    return SOURCE[request.param]


@pytest.fixture
def db(source):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("pragma foreign_keys=on")
        # These are only the columns consumed by the frozen FK/type-guard SQL,
        # not a replacement for any Store bootstrap or typed-core declaration.
        for table in ("runs", "tasks"):
            connection.execute(f"create table {table}(id integer primary key,record_type text)")
        connection.executemany("insert into runs(id,record_type) values(?,?)", (
            (11, "agent_runs"), (12, "agent_runs"), (19, "agent_runtime_attempts"),
        ))
        connection.executemany("insert into tasks(id,record_type) values(?,?)", (
            (21, "reply_tasks"), (22, "reply_tasks"), (29, "scheduled_tasks"),
        ))
        for definition in source.values():
            connection.execute(next(obj["sql"] for obj in definition["objects"] if obj["type"] == "table"))
        for definition in source.values():
            for obj in definition["objects"]:
                if obj["type"] != "table":
                    connection.execute(obj["sql"])
        yield connection


def _insert(db, table, values, *, ignore=False):
    return db.execute(
        f"insert {'or ignore ' if ignore else ''}into {table}({','.join(values)}) "
        f"values({','.join('?' for _ in values)})", tuple(values.values()),
    )


def _sent(db, *, ignore=False, **changes):
    return _insert(db, "sent_replies", {
        "conversation_id": "conversation", "trigger_message_id": "trigger",
        "reply_text": "reply", **changes,
    }, ignore=ignore)


def _observer(db, sent_reply_id, *, ignore=False, **changes):
    return _insert(db, "sent_reply_observers", {
        "sent_reply_id": sent_reply_id, "agent_run_id": 11, "reply_task_id": 21,
        **changes,
    }, ignore=ignore)


def _observers(db):
    return db.execute(
        f"select {','.join(OBSERVER_COLUMNS)} from sent_reply_observers order by sent_reply_id,agent_run_id"
    ).fetchall()


def _sequence(db):
    return db.execute("select seq from sqlite_sequence where name='sent_replies'").fetchone()[0]


def test_exact_captured_source_objects_columns_and_foreign_keys(db, source):
    assert set(source) == {"sent_replies", "sent_reply_observers"}
    assert {column["name"] for column in source["sent_replies"]["columns"]} == set(SENT_COLUMNS)
    assert {column["name"] for column in source["sent_reply_observers"]["columns"]} == set(OBSERVER_COLUMNS)
    for table, definition in source.items():
        columns = [dict(zip(("cid", "name", "type", "notnull", "default", "pk"), row, strict=True))
                   for row in db.execute(f"pragma table_info({table})")]
        assert columns == definition["columns"]
        assert [list(row) for row in db.execute(f"pragma foreign_key_list({table})")] == definition["foreign_keys"]
        objects = [dict(zip(("type", "name", "sql"), row, strict=True)) for row in db.execute(
            "select type,name,sql from sqlite_schema where tbl_name=? and sql is not null order by type,name", (table,),
        )]
        assert objects == sorted(definition["objects"], key=lambda obj: (obj["type"], obj["name"]))
    assert not source["sent_replies"]["foreign_keys"]
    assert len(source["sent_reply_observers"]["foreign_keys"]) == 3
    assert db.execute("select count(*) from sqlite_schema where type='trigger'").fetchone() == (4,)


def test_sent_defaults_and_nullable_recalled_at(db):
    before = db.execute("select current_timestamp").fetchone()[0]
    sent_id = _sent(db).lastrowid
    row = dict(zip(SENT_COLUMNS, db.execute(
        f"select {','.join(SENT_COLUMNS)} from sent_replies where id=?", (sent_id,),
    ).fetchone(), strict=True))
    after = db.execute("select current_timestamp").fetchone()[0]
    assert before <= row.pop("sent_at") <= after
    assert row == dict(id=sent_id, conversation_id="conversation", trigger_message_id="trigger",
                       reply_text="reply", send_result_json="", recall_key="", recall_status="",
                       recall_error="", recalled_at=None, feedback_token="", channel="dingtalk",
                       agent_run_id=0, external_action_key="")


@pytest.mark.parametrize("run_id", [0, 404, 19], ids=["legacy-zero", "unknown", "other-run-kind"])
def test_sent_run_id_is_an_unconstrained_scalar_on_insert_and_update(db, run_id):
    sent_id = _sent(db, agent_run_id=run_id).lastrowid
    assert db.execute("select agent_run_id from sent_replies where id=?", (sent_id,)).fetchone() == (run_id,)
    db.execute("update sent_replies set agent_run_id=11 where id=?", (sent_id,))
    db.execute("update sent_replies set agent_run_id=? where id=?", (run_id, sent_id))
    assert db.execute("select agent_run_id from sent_replies where id=?", (sent_id,)).fetchone() == (run_id,)
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_raw_text_nul_non_json_custom_channel_and_current_recall_are_preserved(db):
    values = dict(conversation_id=" 会话\0 ", trigger_message_id=" 原始\0触发 ",
                  reply_text=" 原始正文\n\0 ", sent_at=" raw sent time\0 ",
                  send_result_json=" not JSON {\0原始 ", recall_key=" recall\0key ",
                  recall_status=" arbitrary\0status ", recall_error=" raw\0error ",
                  recalled_at=None, feedback_token=" token\0 ", channel=" Custom\0channel ",
                  agent_run_id=404, external_action_key=" opaque\0key ")
    sent_id = _sent(db, **values).lastrowid
    fields = tuple(values)
    assert db.execute(f"select {','.join(fields)} from sent_replies where id=?", (sent_id,)).fetchone() == tuple(values.values())
    db.execute("update sent_replies set recall_status=?,recall_error=?,recalled_at=?,send_result_json=? where id=?",
               ("recalled", " updated\0error ", " raw recalled time ", " still not JSON\0 ", sent_id))
    assert db.execute("select recall_status,recall_error,recalled_at,send_result_json from sent_replies where id=?",
                      (sent_id,)).fetchone() == ("recalled", " updated\0error ", " raw recalled time ", " still not JSON\0 ")
    db.execute("update sent_replies set recalled_at=null,channel='' where id=?", (sent_id,))
    assert db.execute("select recalled_at,channel from sent_replies where id=?", (sent_id,)).fetchone() == (None, "")


@pytest.mark.parametrize("field", [field for field in SENT_COLUMNS if field not in ("id", "recalled_at")])
def test_sent_declared_not_null_fields_reject_null(db, field):
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        _sent(db, **{field: None})


def test_only_exact_empty_external_keys_are_exempt_from_binary_uniqueness(db):
    _sent(db)
    _sent(db)
    keys = ("A", "a", "key", " key ", " ", "  ", "\t", "\0", "key\0suffix")
    for key in keys:
        _sent(db, external_action_key=key)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _sent(db, external_action_key=key)
    assert db.execute("select count(*) from sent_replies where external_action_key=''").fetchone() == (2,)
    assert {row[0] for row in db.execute("select external_action_key from sent_replies where external_action_key<>''")} == set(keys)
    assert db.execute("select count(*) from sent_replies where conversation_id='conversation' and trigger_message_id='trigger'").fetchone() == (len(keys) + 2,)


def test_external_key_conflicting_update_aborts_but_keys_can_be_cleared_and_reused(db):
    first = _sent(db, external_action_key="key").lastrowid
    second = _sent(db).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        db.execute("update sent_replies set external_action_key='key' where id=?", (second,))
    assert db.execute("select external_action_key from sent_replies where id=?", (second,)).fetchone() == ("",)
    db.execute("update sent_replies set external_action_key='' where id=?", (first,))
    db.execute("update sent_replies set external_action_key='key' where id=?", (second,))
    db.execute("update sent_replies set external_action_key='' where id=?", (second,))
    assert db.execute("select external_action_key from sent_replies order by id").fetchall() == [("",), ("",)]


def test_autoincrement_retains_deleted_and_ignored_explicit_high_watermarks(db):
    _sent(db, id=700, external_action_key="deleted")
    db.execute("delete from sent_replies where id=700")
    assert _sent(db, external_action_key="duplicate").lastrowid == 701
    assert _sent(db, external_action_key="duplicate", ignore=True).rowcount == 0
    assert _sequence(db) == 702
    assert _sent(db, external_action_key="next").lastrowid == 703
    assert _sent(db, id=900, external_action_key="duplicate", ignore=True).rowcount == 0
    assert _sequence(db) == 900
    assert _sent(db, external_action_key="later").lastrowid == 901
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _sent(db, id=1000, external_action_key="duplicate")
    assert _sequence(db) == 901
    assert db.execute("select id from sent_replies order by id").fetchall() == [(701,), (703,), (901,)]


def test_ignored_maximum_integer_id_exhausts_the_native_autoincrement_allocator(db):
    _sent(db, external_action_key="duplicate")
    assert _sent(db, id=2**63 - 1, external_action_key="duplicate", ignore=True).rowcount == 0
    assert _sequence(db) == 2**63 - 1
    db.commit()
    with pytest.raises(sqlite3.DatabaseError) as exc:
        _sent(db)
    assert exc.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert _sequence(db) == 2**63 - 1
    assert db.execute("select id from sent_replies").fetchall() == [(1,)]


def test_observer_membership_is_the_delivery_run_pair_with_independent_task_binding(db):
    first, second = _sent(db).lastrowid, _sent(db).lastrowid
    _observer(db, first, created_at=" first\0time ")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _observer(db, first, reply_task_id=22)
    assert _observer(db, first, reply_task_id=22, ignore=True).rowcount == 0
    _observer(db, first, agent_run_id=12, reply_task_id=22, created_at=" second time ")
    _observer(db, second, reply_task_id=22, created_at=" other delivery ")
    expected = [(first, 11, 21, " first\0time "), (first, 12, 22, " second time "),
                (second, 11, 22, " other delivery ")]
    assert _observers(db) == expected
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        db.execute("update sent_reply_observers set agent_run_id=11 where sent_reply_id=? and agent_run_id=12", (first,))
    assert _observers(db) == expected


def test_observer_default_time_and_all_four_not_null_fields(db):
    sent_id = _sent(db).lastrowid
    before = db.execute("select current_timestamp").fetchone()[0]
    _observer(db, sent_id)
    after = db.execute("select current_timestamp").fetchone()[0]
    assert before <= _observers(db)[0][3] <= after
    for field in OBSERVER_COLUMNS:
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            _insert(db, "sent_reply_observers", {
                "sent_reply_id": sent_id, "agent_run_id": 12, "reply_task_id": 22,
                "created_at": " time ", field: None,
            })


@pytest.mark.parametrize("field,value,error", [
    ("agent_run_id", 19, "run parent must be a logical agent run"),
    ("reply_task_id", 29, "task parent must be a reply task"),
])
@pytest.mark.parametrize("ignore", [False, True])
def test_observer_wrong_parent_kinds_abort_insert_and_update_even_with_ignore(db, field, value, error, ignore):
    sent_id = _sent(db).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match=error):
        _observer(db, sent_id, ignore=ignore, **{field: value})
    _observer(db, sent_id, created_at=" original time ")
    with pytest.raises(sqlite3.IntegrityError, match=error):
        db.execute(f"update {'or ignore ' if ignore else ''}sent_reply_observers set {field}=?", (value,))
    assert _observers(db) == [(sent_id, 11, 21, " original time ")]


@pytest.mark.parametrize("field", OBSERVER_COLUMNS[:3])
@pytest.mark.parametrize("ignore", [False, True])
def test_observer_missing_parents_abort_immediately_on_insert_and_update(db, field, ignore):
    sent_id = _sent(db).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _insert(db, "sent_reply_observers", {
            "sent_reply_id": sent_id, "agent_run_id": 11, "reply_task_id": 21, field: 404,
        }, ignore=ignore)
    _observer(db, sent_id, created_at=" original time ")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(f"update {'or ignore ' if ignore else ''}sent_reply_observers set {field}=404")
    assert _observers(db) == [(sent_id, 11, 21, " original time ")]


@pytest.mark.parametrize("table,parent_id", [("sent_replies", 1), ("runs", 11), ("tasks", 21)])
def test_referenced_parent_delete_and_id_updates_abort_without_cascading(db, table, parent_id):
    sent_id = _sent(db).lastrowid
    _observer(db, sent_id, created_at=" original time ")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(f"delete from {table} where id=?", (parent_id,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(f"update {table} set id=id+100 where id=?", (parent_id,))
    assert db.execute(f"select id from {table} where id in (?,?)", (parent_id, parent_id + 100)).fetchall() == [(parent_id,)]
    assert _observers(db) == [(sent_id, 11, 21, " original time ")]


def test_observer_can_rebind_and_unreferenced_parent_ids_can_change_and_be_deleted(db):
    first, second = _sent(db).lastrowid, _sent(db).lastrowid
    _observer(db, first, created_at=" unchanged time ")
    db.execute("update sent_reply_observers set sent_reply_id=?,agent_run_id=12,reply_task_id=22 where sent_reply_id=? and agent_run_id=11",
               (second, first))
    for table, parent_id in (("sent_replies", first), ("runs", 11), ("tasks", 21)):
        assert db.execute(f"update {table} set id=id+100 where id=?", (parent_id,)).rowcount == 1
        assert db.execute(f"delete from {table} where id=?", (parent_id + 100,)).rowcount == 1
    assert _observers(db) == [(second, 12, 22, " unchanged time ")]
    assert not db.execute("pragma foreign_key_check").fetchall()
