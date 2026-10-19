"""Native State UPSERTs validate INSERT candidates and retain encoded JSON facts."""
from contextlib import closing
import sqlite3

import pytest

from app.state_definitions import STATE_FIELDS, STATE_PRIMARY_KEYS, _quote, state_projection
from app.state_storage import create_state_schema, insert_state, state_json_field, upsert_state
from tests.configs_source_fixtures import capture, source_db

SOURCE = capture()
KINDS = tuple(STATE_FIELDS)


@pytest.fixture(params=("actual", "fresh"))
def pair(request):
    with source_db(request.param) as source, closing(sqlite3.connect(":memory:")) as target:
        create_state_schema(target)
        for db in (source, target):
            db.create_function("current_timestamp", 0, lambda: "2026-10-16 12:34:56")
            db.execute("begin")
        yield source, target, request.param


def minimal(variant, kind):
    return {c["name"]: c["name"] if c["type"] == "TEXT" else 1
            for c in SOURCE[variant]["tables"][kind]["columns"]
            if c["name"] in STATE_FIELDS[kind]
            and (c["pk"] or c["notnull"] and c["dflt_value"] is None)}


def native_upsert(db, kind, values, updates, *, insert_sql=None,
                  insert_parameters=(), update_parameters=(), where_sql="",
                  where_parameters=()):
    insert_sql = {} if insert_sql is None else insert_sql
    columns = (*values, *insert_sql)
    expressions = ("?",) * len(values) + tuple(insert_sql.values())
    sql = ("insert into " + _quote(kind) + "(" + ",".join(map(_quote, columns)) +
           ") values(" + ",".join(expressions) + ") on conflict(" +
           ",".join(map(_quote, STATE_PRIMARY_KEYS[kind])) + ") do update set " +
           ",".join(_quote(field) + "=" + expression for field, expression in updates.items()))
    if where_sql:
        sql += " where " + where_sql
    return db.execute(sql, (*values.values(), *insert_parameters,
                           *update_parameters, *where_parameters)).rowcount


def facts(db, kind, target=False):
    fields = ("rowid", *STATE_FIELDS[kind])
    selected = ",".join(map(_quote, fields)) + "," + ",".join(
        "typeof(" + _quote(field) + ")" for field in fields)
    table = "(" + state_projection(kind, include_rowid=True) + ")" if target else _quote(kind)
    return db.execute("select " + selected + " from " + table + " order by rowid").fetchall()


@pytest.mark.parametrize("kind", KINDS)
def test_native_partial_pk_upsert_preserves_source_identity_and_defaults(pair, kind):
    source, target, variant = pair
    values = minimal(variant, kind)
    columns = ("rowid", *values)
    source.execute("insert into " + _quote(kind) + "(" + ",".join(map(_quote, columns)) +
                   ") values(" + ",".join("?" for _ in columns) + ")", (-7, *values.values()))
    insert_state(target, kind, values, source_rowid=-7)
    updated = {field: "excluded." + _quote(field) for field in STATE_FIELDS[kind]
               if field not in STATE_PRIMARY_KEYS[kind]}
    encoded = {field: state_json_field(field) for field in updated}
    assert native_upsert(source, kind, values, updated) == upsert_state(
        target, kind, values, encoded) == 1
    assert facts(source, kind) == facts(target, kind, True)
    assert target.execute("select cast(legacy_key as integer) from state").fetchone() == (-7,)
    # Conflict must not consume a source ROWID from an ordinary non-AUTOINCREMENT family.
    changed = values | {STATE_PRIMARY_KEYS[kind][0]: "new"}
    assert native_upsert(source, kind, changed, updated) == upsert_state(
        target, kind, changed, encoded) == 1
    assert facts(source, kind) == facts(target, kind, True)
    assert [row[0] for row in facts(target, kind, True)] == [-7, -6]


@pytest.mark.parametrize("kind", tuple(k for k in KINDS if len(STATE_PRIMARY_KEYS[k]) == 1))
def test_nullable_primary_keys_keep_multiple_null_rows(pair, kind):
    source, target, variant = pair
    field = STATE_PRIMARY_KEYS[kind][0]
    values = minimal(variant, kind) | {field: None}
    changed_field = next(f for f in STATE_FIELDS[kind] if f != field)
    for _ in range(2):
        assert native_upsert(source, kind, values, {changed_field: "excluded." + _quote(changed_field)}) == 1
        assert upsert_state(target, kind, values, {changed_field: state_json_field(changed_field)}) == 1
    assert facts(source, kind) == facts(target, kind, True)
    assert len(facts(target, kind, True)) == 2


@pytest.mark.parametrize("value", (-2.2101102838387424e-85, 7.294214896064974e-137,
                                  b"blob", float("inf"), float("-inf"), None))
def test_session_coalesce_retains_native_affinity_and_encoded_leaf(pair, value):
    source, target, _ = pair
    old = {"conversation_id": "same", "title": "old", "single_chat": 1,
           "codex_session_id": b"old", "codex_session_contract_hash": "untouched"}
    source.execute("insert into conversations(" + ",".join(old) + ") values(?,?,?,?,?)", tuple(old.values()))
    insert_state(target, "conversations", old)
    values = {"conversation_id": "same", "title": "new" if value is None else value, "single_chat": 1, "codex_session_id": value}
    native = {"title": "excluded.title", "codex_session_id": "coalesce(excluded.codex_session_id,conversations.codex_session_id)"}
    encoded = {"title": state_json_field("title"), "codex_session_id":
               "case when excluded.codex_session_id is not null then " +
               state_json_field("codex_session_id") + " else " +
               state_json_field("codex_session_id", "state") + " end"}
    assert native_upsert(source, "conversations", values, native) == upsert_state(
        target, "conversations", values, encoded) == 1
    assert facts(source, "conversations") == facts(target, "conversations", True)


@pytest.mark.parametrize("by", (None, float("nan"), "", "bob", b"bob"))
@pytest.mark.parametrize("guard", (False, True))
def test_wizard_insert_candidate_validation_case_and_guard(pair, by, guard):
    source, target, _ = pair
    old = {"step_id": "same", "status": "ok", "summary": "old",
           "manual_confirmed_at": "oldtime", "manual_confirmed_by": "alice"}
    source.execute("insert into setup_wizard_steps(" + ",".join(old) + ") values(?,?,?,?,?)",
                   tuple(old.values()))
    insert_state(target, "setup_wizard_steps", old)
    values = {"step_id": "same", "status": "new", "summary": "new", "manual_confirmed_by": by}
    native = {"status": "excluded.status", "summary": "excluded.summary",
              "manual_confirmed_at": "case when excluded.manual_confirmed_by!='' then current_timestamp else setup_wizard_steps.manual_confirmed_at end",
              "manual_confirmed_by": "case when excluded.manual_confirmed_by!='' then excluded.manual_confirmed_by else setup_wizard_steps.manual_confirmed_by end",
              "updated_at": "current_timestamp"}
    encoded = {"status": state_json_field("status"), "summary": state_json_field("summary"),
               "manual_confirmed_at": "case when excluded.manual_confirmed_by!='' then json_quote(current_timestamp) else " + state_json_field("manual_confirmed_at", "state") + " end",
               "manual_confirmed_by": "case when excluded.manual_confirmed_by!='' then " + state_json_field("manual_confirmed_by") + " else " + state_json_field("manual_confirmed_by", "state") + " end",
               "updated_at": "json_quote(current_timestamp)"}
    expr = "case when ?!='' then current_timestamp else '' end"
    where = "1" if guard else "0"
    def native_call():
        return native_upsert(source, "setup_wizard_steps", values, native,
            insert_sql={"manual_confirmed_at": expr}, insert_parameters=(by,), where_sql=where)

    def target_call():
        return upsert_state(target, "setup_wizard_steps", values, encoded,
            insert_json={"manual_confirmed_at": "json_quote(" + expr + ")"},
            insert_parameters=(by,), where_sql=where)
    if by is None or isinstance(by, float):
        with pytest.raises(sqlite3.IntegrityError):
            native_call()
        with pytest.raises(sqlite3.IntegrityError):
            target_call()
    else:
        assert native_call() == target_call() == int(guard)
    assert source.in_transaction and target.in_transaction
    assert facts(source, "setup_wizard_steps") == facts(target, "setup_wizard_steps", True)


@pytest.mark.parametrize("uidvalidity,new_uidvalidity,old_uid,new_uid", (
    (-2.2101102838387424e-85, -2.2101102838387424e-85, 2.5, 3.5),
    (1, 1, 3.5, 2.5), (1, 1, 2.5, 2.5), (None, None, 100, -2),
    (1, 2, 100, -2), (1, 1, "z", 3),
))
def test_historical_generation_and_max_select_original_json_leaf(
        pair, uidvalidity, new_uidvalidity, old_uid, new_uid):
    source, target, _ = pair
    kind = "email_historical_traversals"
    old = {"account_id": "a", "folder": "f", "model_id": "m", "uidvalidity": uidvalidity,
           "last_seen_uid": old_uid, "updated_at": "old"}
    source.execute("insert into " + kind + "(" + ",".join(old) + ") values(?,?,?,?,?,?)", tuple(old.values()))
    insert_state(target, kind, old, source_rowid=1)
    values = old | {"uidvalidity": new_uidvalidity, "last_seen_uid": new_uid, "updated_at": "new"}
    native = {"uidvalidity": "excluded.uidvalidity", "last_seen_uid":
              "case when email_historical_traversals.uidvalidity=excluded.uidvalidity then "
              "max(email_historical_traversals.last_seen_uid,excluded.last_seen_uid) else excluded.last_seen_uid end",
              "updated_at": "excluded.updated_at"}
    encoded = {"uidvalidity": state_json_field("uidvalidity"), "last_seen_uid":
               "case when state.uidvalidity=excluded.uidvalidity and "
               "state.last_seen_uid>=excluded.last_seen_uid then " +
               state_json_field("last_seen_uid", "state") + " else " + state_json_field("last_seen_uid") + " end",
               "updated_at": state_json_field("updated_at")}
    assert native_upsert(source, kind, values, native) == upsert_state(target, kind, values, encoded) == 1
    assert facts(source, kind) == facts(target, kind, True)


def test_upsert_clock_defaults_and_update_expressions_share_final_statement(pair):
    source, target, _ = pair
    traces = []
    target.set_trace_callback(traces.append)
    for value in ("insert", "update"):
        values = {"key": "same", "value": value}
        native_upsert(source, "service_state", values,
                      {"value": "excluded.value", "updated_at": "current_timestamp"})
        assert upsert_state(target, "service_state", values, {
            "value": state_json_field("value"), "updated_at": "json_quote(current_timestamp)",
        }) == 1
        assert facts(source, "service_state") == facts(target, "service_state", True)
    clocks = [sql for sql in traces if "current_timestamp" in sql.lower()]
    assert len(clocks) == 2 and all(sql.startswith("insert into state(") for sql in clocks)


def test_constraint_abort_and_outer_rollback_are_caller_owned(pair):
    source, target, _ = pair
    for db in (source, target):
        db.execute("create table earlier(value)")
        db.execute("insert into earlier values('pending')")
    values = {"key": "same", "value": "old"}
    native_upsert(source, "service_state", values, {"value": "excluded.value"})
    upsert_state(target, "service_state", values, {"value": state_json_field("value")})
    with pytest.raises(sqlite3.IntegrityError):
        native_upsert(source, "service_state", values, {"value": "null"})
    with pytest.raises(sqlite3.IntegrityError):
        upsert_state(target, "service_state", values, {"value": "json_quote(null)"})
    for db in (source, target):
        assert db.in_transaction
        assert db.execute("select value from earlier").fetchone() == ("pending",)
    assert facts(source, "service_state") == facts(target, "service_state", True)
    target.rollback()
    assert target.execute("select count(*) from state").fetchone() == (0,)


def test_upsert_missing_caller_transaction_writes_nothing():
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        with pytest.raises(sqlite3.ProgrammingError, match="caller transaction"):
            upsert_state(db, "service_state", {"key": "k", "value": "v"},
                         {"value": state_json_field("value")})
        assert not db.in_transaction
        assert db.execute("select count(*) from state").fetchone() == (0,)


@pytest.mark.parametrize("value", ("", "earlier", None))
def test_wechat_blank_preserves_but_nonblank_older_replaces(pair, value):
    source, target, variant = pair
    kind = "wechat_read_state"
    preserved = ("self_user_id", "watermark_sent_at", "watermark_message_id", "last_scan_at")
    old = minimal(variant, kind) | {field: "later" for field in preserved}
    source.execute("insert into " + kind + "(" + ",".join(old) + ") values(" +
                   ",".join("?" for _ in old) + ")", tuple(old.values()))
    insert_state(target, kind, old)
    values = minimal(variant, kind) | {field: value for field in preserved}
    native = {field: "coalesce(nullif(excluded." + field + ",''),wechat_read_state." + field + ")"
              for field in preserved}
    encoded = {field: "case when excluded." + field + " is not null and excluded." + field +
               "!='' then " + state_json_field(field) + " else " +
               state_json_field(field, "state") + " end" for field in preserved}
    if value is None:
        with pytest.raises(sqlite3.IntegrityError):
            native_upsert(source, kind, values, native)
        with pytest.raises(sqlite3.IntegrityError):
            upsert_state(target, kind, values, encoded)
    else:
        assert native_upsert(source, kind, values, native) == upsert_state(
            target, kind, values, encoded) == 1
    assert facts(source, kind) == facts(target, kind, True)
