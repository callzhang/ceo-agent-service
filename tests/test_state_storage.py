"""State primitives compared with literal approved native SQLite declarations."""
from contextlib import closing
import sqlite3
import json

import pytest

from app.state_definitions import (
    STATE_DEFAULTS, STATE_FIELDS, STATE_NOT_NULL, STATE_PRIMARY_KEYS,
    STATE_TYPES, _quote, state_projection,
)
from app.state_storage import create_state_schema, insert_state, update_state, delete_state
from tests.configs_source_fixtures import capture, source_db

SOURCE = capture()
KINDS = tuple(STATE_FIELDS)
UNSET = object()


@pytest.fixture(params=("actual", "fresh"))
def pair(request):
    with source_db(request.param) as source, closing(sqlite3.connect(":memory:")) as target:
        create_state_schema(target)
        source.execute("begin")
        target.execute("begin")
        yield source, target, request.param


def columns(variant, kind):
    return [c for c in SOURCE[variant]["tables"][kind]["columns"]
            if c["name"] in STATE_FIELDS[kind]]


def required(variant, kind, suffix):
    values = {}
    for c in columns(variant, kind):
        if c["pk"] or c["notnull"] and c["dflt_value"] is None:
            values[c["name"]] = c["name"] + suffix if c["type"] == "TEXT" else 1
        elif (c["dflt_value"] or "").lower() == "current_timestamp":
            values[c["name"]] = "2026-10-16T00:00:00Z"
    return values


def native_insert(db, kind, values, rowid=UNSET, ignore=False):
    data = dict(values)
    if rowid is not UNSET:
        data = {"rowid": rowid, **data}
    return db.execute(
        "insert " + ("or ignore " if ignore else "") + "into " + _quote(kind) +
        "(" + ",".join(map(_quote, data)) + ") values(" +
        ",".join("?" for _ in data) + ")", tuple(data.values()))


def facts(db, kind, target=False):
    fields = ("rowid", *STATE_FIELDS[kind])
    selected = ",".join(map(_quote, fields)) + "," + ",".join(
        "typeof(" + _quote(field) + ")" for field in fields)
    table = "(" + state_projection(kind, include_rowid=True) + ")" if target else _quote(kind)
    return db.execute("select " + selected + " from " + table + " order by rowid").fetchall()


def test_scope_and_real_state_fields_only():
    removed = {"in_handoff", "handoff_message_id", "handoff_message_create_time", "manual_clear_message_id",
               "codex_session_id", "codex_session_contract_hash"}
    assert set(KINDS) == set(SOURCE["groups"]["state"]) - {"corpus_sources"}
    assert len(KINDS) == 15
    assert sum(map(len, STATE_FIELDS.values())) == 83
    assert len(STATE_TYPES) == 60
    assert "corpus_sources" not in KINDS
    assert not removed.intersection(STATE_FIELDS["conversations"])
    for variant in ("actual", "fresh"):
        for kind in KINDS:
            expected = tuple(c["name"] for c in SOURCE[variant]["tables"][kind]["columns"]
                             if not (kind == "conversations" and c["name"] in removed))
            assert set(STATE_FIELDS[kind]) == set(expected)


@pytest.mark.parametrize("kind", KINDS)
def test_signed_rowids_allocator_reuse_and_kind_namespace(pair, kind):
    source, target, variant = pair
    for n, rowid in enumerate((-7, UNSET, 0, UNSET)):
        values = required(variant, kind, str(n))
        a = native_insert(source, kind, values, rowid)
        b = (insert_state(target, kind, values) if rowid is UNSET else
             insert_state(target, kind, values, source_rowid=rowid))
        assert b.lastrowid == a.lastrowid
        assert b.rowcount == a.rowcount == 1
    assert facts(source, kind) == facts(target, kind, True)
    source.execute("delete from " + _quote(kind) + " where rowid=1")
    assert delete_state(target, kind, 1) == 1
    values = required(variant, kind, "reused")
    assert native_insert(source, kind, values).lastrowid == 1
    assert insert_state(target, kind, values).lastrowid == 1
    assert facts(source, kind) == facts(target, kind, True)
    assert target.execute("select 1 from sqlite_schema where name='sqlite_sequence'").fetchone() is None


@pytest.mark.parametrize("kind", KINDS)
def test_native_fields_affinity_and_constraints(pair, kind):
    source, target, variant = pair
    for field in STATE_FIELDS[kind]:
        for value in (None, 1, 1.5, "1", b"\x00\xff", -2.2101102838387424e-85):
            source.execute("delete from " + _quote(kind))
            target.execute("delete from state")
            values = required(variant, kind, "field") | {field: value}
            try:
                native_insert(source, kind, values, -7)
            except sqlite3.IntegrityError:
                with pytest.raises(sqlite3.IntegrityError):
                    insert_state(target, kind, values, source_rowid=-7)
            else:
                insert_state(target, kind, values, source_rowid=-7)
                assert facts(source, kind) == facts(target, kind, True), (variant, kind, field, value)


@pytest.mark.parametrize("kind", KINDS)
def test_native_key_nullability_uniqueness_and_binary_case(pair, kind):
    source, target, variant = pair
    values = required(variant, kind, "key")
    native_insert(source, kind, values)
    insert_state(target, kind, values)
    assert native_insert(source, kind, values, ignore=True).rowcount == 0
    assert insert_state(target, kind, values, ignore=True).rowcount == 0
    for text in ("Case", "case"):
        changed = values | {STATE_PRIMARY_KEYS[kind][0]: text}
        native_insert(source, kind, changed)
        insert_state(target, kind, changed)
    for c in columns(variant, kind):
        if c["pk"]:
            changed = required(variant, kind, "null") | {c["name"]: None}
            if c["notnull"]:
                with pytest.raises(sqlite3.IntegrityError):
                    native_insert(source, kind, changed)
                with pytest.raises(sqlite3.IntegrityError):
                    insert_state(target, kind, changed)
            else:
                for _ in range(2):
                    native_insert(source, kind, changed)
                    insert_state(target, kind, changed)
    assert facts(source, kind) == facts(target, kind, True)


@pytest.mark.parametrize("kind", KINDS)
def test_omitted_defaults_follow_native_literals_and_sqlite_clock(pair, kind):
    _, target, variant = pair
    minimal = {c["name"]: c["name"] if c["type"] == "TEXT" else 1
               for c in columns(variant, kind)
               if c["pk"] or c["notnull"] and c["dflt_value"] is None}
    before = target.execute("select current_timestamp").fetchone()[0]
    insert_state(target, kind, minimal)
    after = target.execute("select current_timestamp").fetchone()[0]
    row = target.execute(state_projection(kind)).fetchone()
    data = dict(zip(STATE_FIELDS[kind], row, strict=True))
    for c in columns(variant, kind):
        if c["name"] in minimal:
            continue
        default = c["dflt_value"]
        if (default or "").lower() == "current_timestamp":
            assert before <= data[c["name"]] <= after
        else:
            expected = target.execute("select " + default).fetchone()[0] if default is not None else None
            assert data[c["name"]] == expected


def test_cas_predicates_and_statement_abort_preserve_outer_work(pair):
    source, target, _ = pair
    for key in ("left", "right"):
        native_insert(source, "service_state", {"key": key, "value": key, "updated_at": "t"})
        insert_state(target, "service_state", {"key": key, "value": key, "updated_at": "t"})
    source.execute("create table pending(value)")
    target.execute("create table pending(value)")
    source.execute("insert into pending values('outer')")
    target.execute("insert into pending values('outer')")
    assert source.execute("update service_state set value=null where rowid=1 and value='wrong'").rowcount == 0
    assert update_state(target, "service_state", 1, {"value": None},
                        where_sql="value=?", parameters=("wrong",)) == 0
    with pytest.raises(sqlite3.IntegrityError):
        source.execute("update service_state set key='right' where rowid=1")
    with pytest.raises(sqlite3.IntegrityError):
        update_state(target, "service_state", 1, {"key": "right"})
    for db in (source, target):
        assert db.in_transaction
        assert db.execute("select value from pending").fetchone() == ("outer",)
    assert facts(source, "service_state") == facts(target, "service_state", True)
    assert update_state(target, "service_state", "01", {"value": "done"},
                        where_sql="value=?", parameters=("left",)) == source.execute(
                            "update service_state set value='done' where rowid='01' and value='left'").rowcount
    assert facts(source, "service_state") == facts(target, "service_state", True)


def test_update_replace_source_rowid_and_outer_savepoint(pair):
    source, target, _ = pair
    for key in ("left", "right"):
        native_insert(source, "service_state", {"key": key, "value": key, "updated_at": "t"})
        insert_state(target, "service_state", {"key": key, "value": key, "updated_at": "t"})
    for db in (source, target):
        db.execute("savepoint outer_work")
    source.execute("update or replace service_state set key='right' where rowid=1")
    assert update_state(target, "service_state", 1, {"key": "right"}, replace=True) == 1
    assert facts(source, "service_state") == facts(target, "service_state", True)
    source.execute("update service_state set rowid=-9 where rowid=1")
    assert update_state(target, "service_state", 1, {}, new_source_rowid=-9) == 1
    assert facts(source, "service_state") == facts(target, "service_state", True)
    for db in (source, target):
        db.execute("rollback to outer_work")
        db.execute("release outer_work")
        assert db.in_transaction
    assert facts(source, "service_state") == facts(target, "service_state", True)


@pytest.mark.parametrize("bound", (None, float("nan")), ids=("null", "nan"))
@pytest.mark.parametrize("operation", ("insert", "update"))
def test_replace_null_uses_native_notnull_defaults(pair, bound, operation):
    source, target, variant = pair
    for db in (source, target):
        db.create_function("current_timestamp", 0, lambda: "2026-10-16 12:34:56")
    for kind, defaults in STATE_DEFAULTS.items():
        for field in defaults:
            if field not in STATE_NOT_NULL[kind]:
                continue
            source.execute("delete from " + _quote(kind))
            target.execute("delete from state")
            values = required(variant, kind, field)
            if operation == "insert":
                changed = values | {field: bound}
                source.execute(
                    "insert or replace into " + _quote(kind) + "(" +
                    ",".join(map(_quote, changed)) + ") values(" +
                    ",".join("?" for _ in changed) + ")", tuple(changed.values()))
                assert insert_state(target, kind, changed, replace=True).rowcount == 1
            else:
                native_insert(source, kind, values)
                insert_state(target, kind, values)
                assert source.execute("update or replace " + _quote(kind) + " set " +
                                      _quote(field) + "=? where rowid=1", (bound,)).rowcount == 1
                assert update_state(target, kind, 1, {field: bound}, replace=True) == 1
            assert facts(source, kind) == facts(target, kind, True), (
                variant, operation, kind, field, bound)


def test_replace_preserves_nullable_null_and_defaultless_notnull_abort(pair):
    source, target, _ = pair
    for bound in (None, float("nan")):
        source.execute("delete from service_state")
        target.execute("delete from state")
        values = {"key": "key", "value": "value", "updated_at": "time"}
        native_insert(source, "service_state", values)
        insert_state(target, "service_state", values)
        assert source.execute("update or replace service_state set key=? where rowid=1",
                              (bound,)).rowcount == 1
        assert update_state(target, "service_state", 1, {"key": bound}, replace=True) == 1
        assert facts(source, "service_state") == facts(target, "service_state", True)
        with pytest.raises(sqlite3.IntegrityError):
            source.execute("update or replace service_state set value=? where rowid=1", (bound,))
        with pytest.raises(sqlite3.IntegrityError):
            update_state(target, "service_state", 1, {"value": bound}, replace=True)
        assert source.in_transaction and target.in_transaction
        assert facts(source, "service_state") == facts(target, "service_state", True)


def test_replace_failed_cas_does_not_evaluate_default(pair):
    source, target, _ = pair
    values = {"key": "key", "value": "value", "updated_at": "time"}
    native_insert(source, "service_state", values)
    insert_state(target, "service_state", values)

    def unexpected_clock():
        raise AssertionError("no matching row should evaluate a default")

    for db in (source, target):
        db.create_function("current_timestamp", 0, unexpected_clock)
    assert source.execute("update or replace service_state set updated_at=null "
                          "where rowid=1 and value='wrong'").rowcount == 0
    assert update_state(target, "service_state", 1, {"updated_at": None}, replace=True,
                        where_sql="value=?", parameters=("wrong",)) == 0
    assert facts(source, "service_state") == facts(target, "service_state", True)


def test_maximum_ordinary_rowid_uses_native_random_free_positive_identity(pair):
    source, target, _ = pair
    values = {"key": "maximum", "value": "x", "updated_at": "t"}
    native_insert(source, "service_state", values, 2**63 - 1)
    insert_state(target, "service_state", values, source_rowid=2**63 - 1)
    next_values = values | {"key": "next"}
    a = native_insert(source, "service_state", next_values).lastrowid
    b = insert_state(target, "service_state", next_values).lastrowid
    assert 0 < a < 2**63 - 1
    assert 0 < b < 2**63 - 1


def test_source_rowids_can_repeat_across_kinds_and_reads_do_not_commit(pair):
    source, target, variant = pair
    for kind in KINDS:
        values = required(variant, kind, "namespace")
        native_insert(source, kind, values, 0)
        assert insert_state(target, kind, values, source_rowid=0).lastrowid == 0
    assert target.execute("select count(*),count(distinct id) from state").fetchone() == (15, 15)
    assert source.in_transaction and target.in_transaction
    target.rollback()
    assert target.execute("select count(*) from state").fetchone() == (0,)


def test_missing_caller_transaction_is_refused_without_a_write():
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        with pytest.raises(sqlite3.ProgrammingError, match="caller transaction"):
            insert_state(db, "service_state", {"key": "x", "value": "y"})
        assert not db.in_transaction
        assert db.execute("select count(*) from state").fetchone() == (0,)


def test_org_lookup_indices_remain_nonunique(pair):
    source, target, variant = pair
    for user in ("a", "b"):
        values = required(variant, "org_user_profiles", user) | {
            "user_id": user, "name": "Same", "open_dingtalk_id": "same-open-id",
        }
        native_insert(source, "org_user_profiles", values)
        insert_state(target, "org_user_profiles", values)
    assert facts(source, "org_user_profiles") == facts(target, "org_user_profiles", True)
    plan = target.execute("explain query plan select user_id from state "
        "where record_type='org_user_profiles' and open_dingtalk_id='same-open-id'").fetchall()
    assert any("idx_state_org_open_id" in row[3] for row in plan)


def test_dispatcher_terminal_prune_tie_uses_source_rowid_and_index(pair):
    source, target, variant = pair
    kind = "dispatcher_claim_leases"
    for rid in (7, 0, -7):
        values = required(variant, kind, str(rid)) | {
            "adapter_name": "adapter", "source_id": str(rid), "terminal_at": "same-time",
        }
        native_insert(source, kind, values, rid)
        insert_state(target, kind, values, source_rowid=rid)
    native_sql = ("select rowid from dispatcher_claim_leases where terminal_at<>'' "
                  "order by terminal_at desc,rowid desc limit 2")
    target_sql = ("select rowid from (" + state_projection(kind, include_rowid=True) +
                  ") where terminal_at<>'' order by terminal_at desc,rowid desc limit 2")
    assert source.execute(native_sql).fetchall() == target.execute(target_sql).fetchall() == [(7,), (0,)]
    details = [row[3] for row in target.execute("explain query plan " + target_sql)]
    assert any("idx_state_dispatcher_terminal" in detail for detail in details), details
    assert not any("TEMP B-TREE" in detail for detail in details)


def test_structural_shape_has_no_extra_business_fields_or_sequences():
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        info = db.execute("pragma table_xinfo(state)").fetchall()
        assert [c[1] for c in info if c[6] == 0] == ["id", "record_type", "legacy_key", "payload_json"]
        assert {c[1] for c in info if c[6] == 2} == set(STATE_TYPES)
        assert db.execute("pragma foreign_key_list(state)").fetchall() == []
        assert db.execute("select name from sqlite_schema where type='trigger'").fetchall() == []
        assert len(db.execute("pragma index_list(state)").fetchall()) == 20
        payload = json.dumps({"key": "x", "value": "v", "updated_at": "t"})
        for kind, legacy, body in (
            ("corpus_sources", "1", "{}"),
            ("service_state", "01", payload),
            ("service_state", "1", json.dumps({"key": "x", "value": "v", "updated_at": "t", "extra": 1})),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("insert into state(record_type,legacy_key,payload_json) values(?,?,?)",
                           (kind, legacy, body))
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
