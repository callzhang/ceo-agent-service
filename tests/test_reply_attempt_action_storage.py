"""Independent source/finite comparisons for the opt-in Phase 5E storage."""

from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import pytest

from app import action_storage as storage


CAPTURE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-13-database-redesign-phase5e-reply-source.json").read_text())
REQUIRED = {"conversation_id": "c", "conversation_title": "t", "trigger_message_id": "m",
            "trigger_sender": "s", "trigger_text": "body", "action": "reply",
            "sensitivity_kind": "ordinary", "send_status": "pending"}


@pytest.fixture(params=[("actual", "actual_no_memory_fk")])
def pair(request):
    with closing(sqlite3.connect(":memory:")) as source, closing(sqlite3.connect(":memory:")) as target:
        source.execute("pragma foreign_keys=on")
        target.execute("pragma foreign_keys=on")
        for name in ("reply_attempts", "memory_write_events"):
            source.execute(CAPTURE[request.param[0]]["tables"][name]["sql"])
        for definition in CAPTURE[request.param[0]]["tables"].values():
            for index in definition["indexes"]:
                if index["sql"]:
                    source.execute(index["sql"])
        target.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
        target.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
        storage.create_action_schema(
            target, include_reply=True, reply_variant=request.param[1], compact_payload=True,
        )
        yield source, target, request.param[0]


def insert_source(db, table, values, ignore=False):
    return db.execute(f"insert {'or ignore ' if ignore else ''}into {table}({','.join(values)}) values({','.join('?' for _ in values)})", tuple(values.values()))


def projection(db, kind, identity, variant):
    columns = [c["name"] for c in CAPTURE[variant]["tables"][kind]["columns"]]
    values = db.execute("select * from (" + storage.reply_action_projection(kind, "actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk") + ") where id=?", (identity,)).fetchone()
    return dict(zip(columns, values, strict=True))


def _compact_variant(variant):
    return "actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk"


def _replace_compact_payload_slot(db, kind, identity, variant, field, encoded_json):
    path = storage._action_path(kind, field, reply_variant=_compact_variant(variant))
    return db.execute(
        "update actions set payload_json=jsonb_set(payload_json,?,json(?)) "
        "where record_type=? and cast(legacy_key as integer)=?",
        (path, encoded_json, kind, identity),
    )


def _append_compact_payload_slot(db, kind, identity, value):
    return db.execute(
        "update actions set payload_json=jsonb_insert(payload_json,'$[#]',json(?)) "
        "where record_type=? and cast(legacy_key as integer)=?",
        (json.dumps(value), kind, identity),
    )


def _compact_payload_json(db, kind):
    return db.execute(
        "select json(payload_json) from actions where record_type=?", (kind,)
    ).fetchone()[0]


def source_row(db, kind, identity):
    db.row_factory = sqlite3.Row
    row = db.execute(f"select * from {kind} where id=?", (identity,)).fetchone()
    return dict(row)


def test_defaults_and_raw_storage_classes_match_source(pair):
    source, target, variant = pair
    values = REQUIRED | {"trigger_text": "not JSON\0 { trailing", "audit_documents_json": b"\x00\xffnot json",
                         "final_reply_text": b"\x00\xffnot json", "agent_run_id": 404,
                         "reviewed_at": None}
    if variant == "actual":
        values |= {"conversation_single_chat": 1, "trigger_create_time": b"\x00\xffnot json"}
    sid = insert_source(source, "reply_attempts", values).lastrowid
    tid = storage.insert_action(target, "reply_attempts", values).lastrowid
    assert sid == tid
    expected = source_row(source, "reply_attempts", sid)
    actual = projection(target, "reply_attempts", tid, variant)
    assert set(actual) == set(expected)
    expected["audit_tool_events_json"] = "[]"
    assert actual == expected
    for field in expected:
        if field == "audit_tool_events_json":
            continue
        source_type = source.execute(f"select typeof({field}) from reply_attempts where id=?", (sid,)).fetchone()[0]
        target_type = target.execute(f"select typeof({field}) from ({storage.reply_action_projection('reply_attempts', 'actual_no_memory_fk' if variant == 'actual' else 'fresh_memory_fk')}) where id=?", (tid,)).fetchone()[0]
        assert target_type == source_type, field


def test_old_layout_remains_unchanged(pair):
    _, _, variant = pair
    reply_variant = _compact_variant(variant)
    with closing(sqlite3.connect(":memory:")) as target:
        target.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
        target.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
        storage.create_action_schema(target, include_reply=True, reply_variant=reply_variant)
        assert not storage.action_schema_is_current(target, include_delivery=True)
        assert storage.action_schema_is_current(
            target, include_reply=True, reply_variant=reply_variant
        )
    assert storage.ACTION_SEQUENCE_NAMES == {"agent_effect_intents": "actions:agent_effect_intents",
                                             "agent_execution_receipts": "actions:agent_execution_receipts"}


@pytest.mark.parametrize("kind", ["reply_attempts"])
def test_native_allocator_low_high_ignore_reject_and_delete(pair, kind):
    source, target, _ = pair
    if kind == "memory_write_events":
        parent = insert_source(source, "reply_attempts", REQUIRED).lastrowid
        assert storage.insert_action(target, "reply_attempts", REQUIRED).lastrowid == parent
        base = {"attempt_id": parent, "event_type": "one", "payload_json": "{}"}
        next_values = {"attempt_id": parent, "event_type": "two", "payload_json": "{}"}
        invalid = {"event_type": None}
    else:
        base = REQUIRED
        next_values = REQUIRED
        invalid = {"conversation_id": None}

    def pair_insert(values, ignore=False):
        left = insert_source(source, kind, values, ignore=ignore)
        right = storage.insert_action(target, kind, values, ignore=ignore)
        assert right.lastrowid == (left.lastrowid if left.rowcount else None)
        assert right.rowcount == left.rowcount
        return left.lastrowid

    assert pair_insert(base | {"id": 700}) == 700
    source.execute(f"delete from {kind} where id=700")
    storage.delete_action(target, kind, 700)
    assert pair_insert(base) == 701
    pair_insert(next_values | {"id": 5})
    pair_insert(next_values | invalid | {"id": 900}, ignore=True)
    assert source.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0] == target.execute(
        "select seq from sqlite_sequence where name=?", (storage.REPLY_SEQUENCE_NAMES[kind],)).fetchone()[0] == 900
    values = next_values | ({"event_type": "three"} if kind == "memory_write_events" else {})
    assert pair_insert(values) == 901
    for target_db, operation in ((source, lambda: insert_source(source, kind, values | invalid | {"id": 1000})),
                                 (target, lambda: storage.insert_action(target, kind, values | invalid | {"id": 1000}))):
        with pytest.raises(sqlite3.IntegrityError):
            operation()
    assert source.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0] == target.execute(
        "select seq from sqlite_sequence where name=?", (storage.REPLY_SEQUENCE_NAMES[kind],)).fetchone()[0] == 901


@pytest.mark.parametrize("kind", ["reply_attempts"])
def test_ignored_max_exhausts_native_allocator(pair, kind):
    source, target, _ = pair
    if kind == "memory_write_events":
        parent = insert_source(source, "reply_attempts", REQUIRED).lastrowid
        storage.insert_action(target, "reply_attempts", REQUIRED)
        values = {"attempt_id": parent, "event_type": "one", "payload_json": "{}"}
        invalid = {"event_type": None}
    else:
        values = REQUIRED
        invalid = {"conversation_id": None}
    insert_source(source, kind, values | {"id": 1})
    storage.insert_action(target, kind, values | {"id": 1})
    insert_source(source, kind, values | invalid | {"id": 2**63 - 1}, ignore=True)
    storage.insert_action(target, kind, values | invalid | {"id": 2**63 - 1}, ignore=True)
    source.commit()
    target.commit()
    for operation in (lambda: insert_source(source, kind, values), lambda: storage.insert_action(target, kind, values)):
        with pytest.raises(sqlite3.DatabaseError) as error:
            operation()
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL


@pytest.mark.parametrize("kind", ["reply_attempts"])
@pytest.mark.parametrize("mode", ["transaction", "savepoint", "autocommit"])
def test_full_rolls_back_the_same_native_scope(kind, mode):
    outcomes = []
    for target in (False, True):
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as db:
            db.execute("pragma foreign_keys=on")
            if target:
                db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
                db.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
                storage.create_action_schema(db, include_reply=True, reply_variant="actual_no_memory_fk",
                                             compact_payload=True)
            else:
                for table in ("reply_attempts", "memory_write_events"):
                    db.execute(CAPTURE["actual"]["tables"][table]["sql"])
            db.execute("create table pending(value text)")
            if kind == "memory_write_events":
                parent = storage.insert_action(db, "reply_attempts", REQUIRED).lastrowid if target else insert_source(db, "reply_attempts", REQUIRED).lastrowid
                values = {"attempt_id": parent, "event_type": "one", "payload_json": "{}"}
                next_values = values | {"event_type": "two"}
            else:
                values = REQUIRED
                next_values = REQUIRED
            if target:
                storage.insert_action(db, kind, values | {"id": 2**63 - 1})
            else:
                insert_source(db, kind, values | {"id": 2**63 - 1})
            if mode != "autocommit":
                db.execute("begin")
            if mode == "savepoint":
                db.execute("savepoint nested")
            db.execute("insert into pending values('uncommitted')")
            with pytest.raises(sqlite3.DatabaseError) as error:
                if target:
                    storage.insert_action(db, kind, next_values)
                else:
                    insert_source(db, kind, next_values)
            assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
            table = "actions" if target else kind
            sequence = storage.REPLY_SEQUENCE_NAMES[kind] if target else kind
            outcomes.append((db.in_transaction, db.execute("select count(*) from pending").fetchone()[0],
                             db.execute(f"select count(*) from {table} " + ("where record_type=?" if target else ""),
                                        (kind,) if target else ()).fetchone()[0],
                             db.execute("select seq from sqlite_sequence where name=?", (sequence,)).fetchone()))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("field,value", [
    ("trigger_text", 12), ("trigger_text", 1.25), ("trigger_text", "x\0z"),
    ("trigger_text", b"\x00\xffnot json"), ("retry_count", "00001"),
    ("retry_count", "1x"), ("retry_count", 1.5),
])
def test_attempt_affinity_value_and_type_match_source(pair, field, value):
    source, target, variant = pair
    values = REQUIRED | {field: value}
    sid = insert_source(source, "reply_attempts", values).lastrowid
    tid = storage.insert_action(target, "reply_attempts", values).lastrowid
    expected = source.execute(f"select {field},typeof({field}) from reply_attempts where id=?", (sid,)).fetchone()
    actual = target.execute("select " + field + ",typeof(" + field + ") from (" + storage.reply_action_projection(
        "reply_attempts", "actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk") + ") where id=?", (tid,)).fetchone()
    assert actual == expected


def test_reply_index_shapes_and_variant_status(pair):
    _, target, variant = pair
    indexes = {row[1]: row for row in target.execute("pragma index_list(actions)")}
    expected = {"idx_actions_reply_trigger_message", "idx_actions_reply_status", "idx_actions_reply_created",
                "idx_actions_reply_oa_history", "idx_actions_reply_trigger_history", "idx_actions_reply_current_trigger",
                "idx_actions_reply_run_recovery", "idx_actions_memory_unique", "idx_actions_memory_attempt",
                "idx_actions_memory_status"}
    assert expected <= indexes.keys()
    assert indexes["idx_actions_memory_unique"][2] == 1
    oa = list(target.execute("pragma index_xinfo(idx_actions_reply_oa_history)"))
    assert [(row[2], row[3], row[4]) for row in oa[:3]] == [
        ("attempt_oa_process_instance_id", 0, "BINARY"), ("attempt_created_at", 1, "BINARY"), (None, 1, "BINARY")]
    status = list(target.execute("pragma index_xinfo(idx_actions_memory_status)"))
    assert status[1][2] == ("memory_updated_at" if variant == "fresh_candidate17" else None)
    sql = target.execute("select sql from sqlite_schema where name='idx_actions_reply_oa_history'").fetchone()[0]
    assert "attempt_oa_process_instance_id<>''" in sql


def test_finite_payload_has_no_duplicate_physical_source_values(pair):
    _, target, variant = pair
    columns = {row[1]: row[6] for row in target.execute("pragma table_xinfo(actions)")}
    assert columns["payload_json"] == 0
    assert "attempt_values_json" not in columns
    assert "memory_values_json" not in columns
    assert columns["attempt_final_reply_text"] == 2
    assert columns["memory_last_error"] == 2
    assert ("memory_attempt_ref" in columns) == (variant == "fresh_candidate17")
    assert ("memory_attempt_id" in columns) == (variant == "actual")
    assert columns.get("memory_attempt_id") == (2 if variant == "actual" else None)


def test_raw_update_and_exact_queued_marker_projection(pair):
    source, target, variant = pair
    sid = insert_source(source, "reply_attempts", REQUIRED).lastrowid
    assert storage.insert_action(target, "reply_attempts", REQUIRED).lastrowid == sid
    marker = {"tool": "audit_review", "result": "queued"}
    events = json.dumps([{"tool": "read", "output": "secret"}, marker, marker, {"tool": "audit_review", "result": "done"}])
    update = {"final_reply_text": b"\x00\xffnot json", "codex_reason": "not JSON\0 { trailing",
              "audit_tool_events_json": events, "retry_count": float("inf"), "reviewed_at": None}
    source.execute("update reply_attempts set " + ",".join(field + "=?" for field in update) + " where id=?",
                   (*update.values(), sid))
    storage.update_action(target, "reply_attempts", sid, update)
    expected = source_row(source, "reply_attempts", sid)
    expected["audit_tool_events_json"] = json.dumps([marker, marker], separators=(",", ":"))
    actual = projection(target, "reply_attempts", sid, variant)
    assert actual == expected
    assert math.isinf(actual["retry_count"])
    assert "secret" not in _compact_payload_json(target, "reply_attempts")




@pytest.mark.parametrize("kind", ["reply_attempts"])
@pytest.mark.parametrize("updated_id", [900, 2**63 - 1])
def test_ignored_duplicate_after_public_id_update_advances_native_water(pair, kind, updated_id):
    source, target, _ = pair
    values = REQUIRED if kind == "reply_attempts" else {"attempt_id": 404, "event_type": "x", "payload_json": "{}"}
    if kind == "memory_write_events":
        insert_source(source, "reply_attempts", REQUIRED | {"id": 404})
        storage.insert_action(target, "reply_attempts", REQUIRED | {"id": 404})
    assert insert_source(source, kind, values).lastrowid == 1
    assert storage.insert_action(target, kind, values).lastrowid == 1
    source.execute(f"update {kind} set id=? where id=1", (updated_id,))
    storage.update_action(target, kind, 1, {"id": updated_id})
    duplicate = values | {"id": updated_id}
    assert insert_source(source, kind, duplicate, ignore=True).rowcount == 0
    assert storage.insert_action(target, kind, duplicate, ignore=True).rowcount == 0
    assert source.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone() == (updated_id,)
    assert target.execute("select seq from sqlite_sequence where name=?", (storage.REPLY_SEQUENCE_NAMES[kind],)).fetchone() == (updated_id,)
    source.execute(f"delete from {kind} where id=?", (updated_id,))
    storage.delete_action(target, kind, updated_id)
    if updated_id == 2**63 - 1:
        for operation in (lambda: insert_source(source, kind, values), lambda: storage.insert_action(target, kind, values)):
            with pytest.raises(sqlite3.DatabaseError) as error:
                operation()
            assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    else:
        assert insert_source(source, kind, values).lastrowid == updated_id + 1
        assert storage.insert_action(target, kind, values).lastrowid == updated_id + 1


@pytest.mark.parametrize("replacement", [None, 2.5, "nonnumeric"])
def test_absent_attempt_update_defers_row_constraints(pair, replacement):
    source, target, _ = pair
    assert source.execute("update reply_attempts set id=? where id=999", (replacement,)).rowcount == 0
    assert storage.update_action(target, "reply_attempts", 999, {"id": replacement}).rowcount == 0


def test_absent_update_still_rejects_unbindable_input(pair):
    source, target, _ = pair
    with pytest.raises(sqlite3.ProgrammingError):
        source.execute("update reply_attempts set id=? where id=999", (object(),))
    with pytest.raises(sqlite3.ProgrammingError):
        storage.update_action(target, "reply_attempts", 999, {"id": object()})


@pytest.mark.parametrize("bad_argument", ["set", "where"])
@pytest.mark.parametrize("connection_options, expected_transaction", [
    ({}, True), ({"isolation_level": None}, False), ({"autocommit": True}, False),
])
def test_bad_update_binding_opens_native_deferred_transaction(bad_argument, connection_options,
                                                               expected_transaction):
    outcomes = []
    for target in (False, True):
        with closing(sqlite3.connect(":memory:", **connection_options)) as db:
            if target:
                db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
                db.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
                storage.create_action_schema(db, include_reply=True, reply_variant="actual_no_memory_fk",
                                             compact_payload=True)
            else:
                db.execute(CAPTURE["actual"]["tables"]["reply_attempts"]["sql"])
            db.commit()
            with pytest.raises(sqlite3.ProgrammingError):
                if target:
                    storage.update_action(db, "reply_attempts", 999,
                                          {"final_reply_text": object() if bad_argument == "set" else "ok"},
                                          where_sql="id=?" if bad_argument == "where" else "",
                                          parameters=(object(),) if bad_argument == "where" else ())
                else:
                    db.execute("update reply_attempts set final_reply_text=? where id=999" +
                               (" and id=?" if bad_argument == "where" else ""),
                               ("ok", object()) if bad_argument == "where" else (object(),))
            outcomes.append(db.in_transaction)
    assert outcomes == [expected_transaction] * 2


@pytest.mark.parametrize("connection_options", [{"isolation_level": None}, {"autocommit": True}])
def test_autocommit_update_holds_writer_lock_through_predicate_scan(connection_options):
    with TemporaryDirectory(prefix="phase5e-update-", dir="/tmp") as temporary:
        path = Path(temporary) / "reply.db"
        with closing(sqlite3.connect(path, **connection_options)) as db, closing(
            sqlite3.connect(path, isolation_level=None, timeout=0)
        ) as other:
            db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
            db.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
            storage.create_action_schema(db, include_reply=True, reply_variant="actual_no_memory_fk",
                                         compact_payload=True)
            identity = storage.insert_action(db, "reply_attempts", REQUIRED).lastrowid
            observed = []

            def probe(sql):
                if sql.startswith("select 1 from actions where"):
                    try:
                        other.execute("begin immediate")
                    except sqlite3.OperationalError as error:
                        observed.append(error.sqlite_errorcode)
                    else:
                        observed.append(None)
                        other.execute("rollback")

            db.set_trace_callback(probe)
            try:
                result = storage.update_action(db, "reply_attempts", identity, {"final_reply_text": "done"})
            finally:
                db.set_trace_callback(None)
            assert result.rowcount == 1
            assert observed == [sqlite3.SQLITE_BUSY]
            assert db.in_transaction is False


@pytest.mark.parametrize("scope", ["transaction", "savepoint"])
def test_failed_update_preserves_caller_transaction(scope):
    outcomes = []
    for target in (False, True):
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as db:
            if target:
                db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
                db.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
                storage.create_action_schema(db, include_reply=True, reply_variant="actual_no_memory_fk",
                                             compact_payload=True)
            else:
                db.execute(CAPTURE["actual"]["tables"]["reply_attempts"]["sql"])
            db.execute("create table pending(value text)")
            db.execute("begin")
            if scope == "savepoint":
                db.execute("savepoint caller")
            db.execute("insert into pending values('caller')")
            with pytest.raises(sqlite3.ProgrammingError):
                if target:
                    storage.update_action(db, "reply_attempts", 999, {"final_reply_text": object()})
                else:
                    db.execute("update reply_attempts set final_reply_text=? where id=999", (object(),))
            outcomes.append((db.in_transaction, db.execute("select count(*) from pending").fetchone()[0]))
            if scope == "savepoint":
                db.execute("release caller")
            db.execute("rollback")
    assert outcomes == [(True, 1)] * 2


@pytest.mark.parametrize("scope", ["deferred", "transaction", "savepoint"])
@pytest.mark.parametrize("bad_argument", ["set", "where"])
def test_bad_update_binding_does_not_take_writer_lock(scope, bad_argument):
    outcomes = []
    with TemporaryDirectory(prefix="phase5e-binding-", dir="/tmp") as temporary:
        for target in (False, True):
            path = Path(temporary) / ("target.db" if target else "source.db")
            with closing(sqlite3.connect(path)) as db, closing(sqlite3.connect(path, isolation_level=None, timeout=0)) as other:
                if target:
                    db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
                    db.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
                    storage.create_action_schema(db, include_reply=True, reply_variant="actual_no_memory_fk",
                                                 compact_payload=True)
                else:
                    db.execute(CAPTURE["actual"]["tables"]["reply_attempts"]["sql"])
                db.commit()
                if scope == "transaction":
                    db.execute("begin")
                elif scope == "savepoint":
                    db.execute("savepoint caller")
                with pytest.raises(sqlite3.ProgrammingError):
                    if target:
                        storage.update_action(db, "reply_attempts", 999,
                                              {"final_reply_text": object() if bad_argument == "set" else "ok"},
                                              where_sql="id=?" if bad_argument == "where" else "",
                                              parameters=(object(),) if bad_argument == "where" else ())
                    else:
                        db.execute("update reply_attempts set final_reply_text=? where id=999" +
                                   (" and id=?" if bad_argument == "where" else ""),
                                   ("ok", object()) if bad_argument == "where" else (object(),))
                try:
                    other.execute("begin immediate")
                except sqlite3.OperationalError as error:
                    writer_result = error.sqlite_errorcode
                else:
                    writer_result = None
                    other.execute("rollback")
                outcomes.append((db.in_transaction, writer_result))
                db.rollback()
    assert outcomes == [(True, None)] * 2


@pytest.mark.parametrize("kind, variant", [
    ("reply_attempts", "actual"), ("memory_write_events", "fresh_candidate17"),
])
@pytest.mark.parametrize("explicit_id", [False, True])
@pytest.mark.parametrize("scope", ["deferred", "autocommit", "outer"])
def test_bad_reply_insert_binding_matches_native_transaction_and_lock(kind, variant, explicit_id, scope):
    outcomes = []
    with TemporaryDirectory(prefix="phase5e-insert-binding-", dir="/tmp") as temporary:
        for target in (False, True):
            path = Path(temporary) / ("target.db" if target else "source.db")
            options = {"isolation_level": None} if scope == "autocommit" else {}
            with closing(sqlite3.connect(path, **options)) as db, closing(
                sqlite3.connect(path, isolation_level=None, timeout=0)
            ) as other:
                db.execute("pragma foreign_keys=on")
                if target:
                    db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
                    db.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
                    storage.create_action_schema(db, include_reply=True,
                                                 reply_variant="fresh_memory_fk" if kind == "memory_write_events"
                                                 else "actual_no_memory_fk", compact_payload=True)
                else:
                    for table in ("reply_attempts", "memory_write_events"):
                        db.execute(CAPTURE[variant]["tables"][table]["sql"])
                db.commit()
                if scope == "outer":
                    db.execute("begin")
                values = (REQUIRED | {"final_reply_text": object()} if kind == "reply_attempts" else
                          {"attempt_id": 999, "event_type": "memory_write", "last_error": object()})
                if explicit_id:
                    values["id"] = 8
                with pytest.raises(sqlite3.ProgrammingError):
                    if target:
                        storage.insert_action(db, kind, values)
                    else:
                        insert_source(db, kind, values)
                try:
                    other.execute("begin immediate")
                except sqlite3.OperationalError as error:
                    writer_result = error.sqlite_errorcode
                else:
                    writer_result = None
                    other.execute("rollback")
                outcomes.append((db.in_transaction, writer_result))
                if db.in_transaction:
                    db.rollback()
    assert outcomes == [(scope != "autocommit", None)] * 2


@pytest.mark.parametrize("mode", ["deferred", "autocommit", "outer", "savepoint"])
def test_fresh_explicit_insert_parent_failure_transaction_matches_source(mode):
    outcomes = []
    for target in (False, True):
        with closing(sqlite3.connect(":memory:", isolation_level=None if mode == "autocommit" else "")) as db:
            db.execute("pragma foreign_keys=on")
            if target:
                db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
                db.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
                storage.create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk",
                                             compact_payload=True)
            else:
                for table in ("reply_attempts", "memory_write_events"):
                    db.execute(CAPTURE["fresh_candidate17"]["tables"][table]["sql"])
            db.execute("create table pending(value text)")
            db.commit()
            if mode == "outer":
                db.execute("begin")
                db.execute("insert into pending values('caller')")
            elif mode == "savepoint":
                db.execute("savepoint caller")
                db.execute("insert into pending values('caller')")
            values = {"id": 8, "attempt_id": 999, "event_type": "memory_write", "payload_json": "{}"}
            with pytest.raises(sqlite3.IntegrityError):
                if target:
                    storage.insert_action(db, "memory_write_events", values)
                else:
                    insert_source(db, "memory_write_events", values)
            outcomes.append((db.in_transaction, db.execute("select count(*) from pending").fetchone()[0]))
    expected = (mode != "autocommit", int(mode in ("outer", "savepoint")))
    assert outcomes == [expected, expected]


def test_blob_json_queued_markers_keep_order_and_multiplicity(pair):
    source, target, variant = pair
    marker = {"tool": "audit_review", "result": "queued"}
    raw = json.dumps([marker, {"tool": "read", "output": "copied"}, marker]).encode()
    aid = insert_source(source, "reply_attempts", REQUIRED | {"audit_tool_events_json": raw}).lastrowid
    assert storage.insert_action(target, "reply_attempts", REQUIRED | {"audit_tool_events_json": raw}).lastrowid == aid
    expected = json.dumps([marker, marker], separators=(",", ":"))
    assert projection(target, "reply_attempts", aid, variant)["audit_tool_events_json"] == expected
    storage.update_action(target, "reply_attempts", aid, {"audit_tool_events_json": raw})
    assert projection(target, "reply_attempts", aid, variant)["audit_tool_events_json"] == expected
    assert "copied" not in _compact_payload_json(target, "reply_attempts")


@pytest.mark.parametrize("kind", ["reply_attempts"])
@pytest.mark.parametrize("source_id", [2.0, "2", "2.0", True, float("nan")])
def test_native_integer_primary_key_input_matches_source(pair, kind, source_id):
    source, target, _ = pair
    if kind == "memory_write_events":
        insert_source(source, "reply_attempts", REQUIRED | {"id": 404})
        storage.insert_action(target, "reply_attempts", REQUIRED | {"id": 404})
    values = REQUIRED if kind == "reply_attempts" else {"attempt_id": 404, "event_type": "x", "payload_json": "{}"}
    source_error = target_error = None
    try:
        source_result = insert_source(source, kind, values | {"id": source_id})
    except sqlite3.DatabaseError as error:
        source_error = type(error)
    try:
        target_result = storage.insert_action(target, kind, values | {"id": source_id})
    except sqlite3.DatabaseError as error:
        target_error = type(error)
    assert target_error == source_error
    if source_error is None:
        assert target_result.lastrowid == source_result.lastrowid
        assert target.execute("select legacy_key from actions where record_type=?", (kind,)).fetchone() == (str(source_result.lastrowid),)


@pytest.mark.parametrize("source_id", [2.0, "2", "2.0", True])
def test_native_integer_primary_key_update_matches_source(pair, source_id):
    source, target, variant = pair
    sid = insert_source(source, "reply_attempts", REQUIRED | {"id": 41}).lastrowid
    storage.insert_action(target, "reply_attempts", REQUIRED | {"id": 41})
    source.execute("update reply_attempts set id=? where id=?", (source_id, sid))
    storage.update_action(target, "reply_attempts", sid, {"id": source_id})
    new_id = source.execute("select id from reply_attempts").fetchone()[0]
    assert projection(target, "reply_attempts", new_id, variant)["id"] == new_id


@pytest.mark.parametrize("kind", ["reply_attempts"])
def test_nan_public_id_uses_current_native_highwater(pair, kind):
    source, target, _ = pair
    if kind == "memory_write_events":
        insert_source(source, "reply_attempts", REQUIRED | {"id": 404})
        storage.insert_action(target, "reply_attempts", REQUIRED | {"id": 404})
        values = {"attempt_id": 404, "event_type": "x", "payload_json": "{}"}
    else:
        values = REQUIRED
    insert_source(source, kind, values | {"id": 41})
    storage.insert_action(target, kind, values | {"id": 41})
    if kind == "memory_write_events":
        values = values | {"event_type": "y"}
    assert insert_source(source, kind, values | {"id": float("nan")}).lastrowid == 42
    assert storage.insert_action(target, kind, values | {"id": float("nan")}).lastrowid == 42
