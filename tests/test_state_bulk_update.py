"""Set-based State changes against unchanged native source DDL."""
from contextlib import closing
import math
import sqlite3

import pytest

from app.state_definitions import STATE_DEFAULTS, STATE_FIELDS, STATE_PRIMARY_KEYS, STATE_TYPES, _quote
from app.state_storage import create_state_schema, insert_state, update_state_rows
from tests.test_state_storage import facts, native_insert, pair as pair, required


def native_update(db, kind, values, *, where_sql="", parameters=(), replace=False):
    sql = "update " + ("or replace " if replace else "") + _quote(kind)
    sql += " set " + ",".join(_quote(field) + "=?" for field in values)
    if where_sql:
        sql += " where " + where_sql
    return db.execute(sql, (*values.values(), *parameters)).rowcount


def seed(pair, kind, count=3):
    source, target, variant = pair
    result = []
    for position in range(count):
        values = required(variant, kind, str(position))
        rowid = (-7, 8, 0)[position]
        native_insert(source, kind, values, rowid)
        insert_state(target, kind, values, source_rowid=rowid)
        result.append(values)
    return result


@pytest.mark.parametrize("kind", tuple(STATE_FIELDS))
def test_bulk_counts_guards_scope_and_native_facts(pair, kind):
    source, target, variant = pair
    rows = seed(pair, kind)
    other = "service_state" if kind != "service_state" else "seen_messages"
    seed(pair, other, count=1)
    prior = facts(target, other, True)
    key = STATE_PRIMARY_KEYS[kind][0]
    field = next(field for field in STATE_FIELDS[kind] if field not in STATE_PRIMARY_KEYS[kind])
    value = 42 if STATE_TYPES[field] == "INTEGER" else "changed"
    for guard, parameters, expected_count in (
        (_quote(key) + "=?", ("absent-key",), 0),
        (_quote(key) + "=?", (rows[0][key],), 1),
        ("", (), 3),
    ):
        values = {field: value}
        assert native_update(source, kind, values, where_sql=guard, parameters=parameters) == expected_count
        assert update_state_rows(target, kind, values, where_sql=guard, parameters=parameters) == expected_count
        assert facts(source, kind) == facts(target, kind, True)
        assert facts(target, other, True) == prior
        assert source.in_transaction and target.in_transaction


@pytest.mark.parametrize("value", (None, math.nan, b"\x00\xff", -2.2101102838387424e-85,
                                   math.inf, -math.inf, 7, "new"))
def test_bulk_scalar_affinity_and_untouched_encoded_leaf(pair, value):
    source, target, variant = pair
    values = required(variant, "conversations", "one") | {
        "codex_session_contract_hash": b"\x00\xff",
    }
    native_insert(source, "conversations", values, -7)
    insert_state(target, "conversations", values, source_rowid=-7)
    leaf = target.execute(
        "select payload_json->'$.codex_session_contract_hash' from state"
    ).fetchone()[0]
    changes = {"codex_session_id": value}
    assert native_update(source, "conversations", changes) == 1
    assert update_state_rows(target, "conversations", changes) == 1
    assert facts(source, "conversations") == facts(target, "conversations", True)
    assert target.execute(
        "select payload_json->'$.codex_session_contract_hash' from state"
    ).fetchone()[0] == leaf


@pytest.mark.parametrize("kind,field", (
    ("conversations", "title"),
    ("dispatcher_claim_leases", "generation"),
    ("dispatcher_claim_leases", "created_at"),
))
@pytest.mark.parametrize("value", (None, math.nan))
@pytest.mark.parametrize("replace", (False, True))
def test_bulk_replace_original_defaults_and_final_statement_clock(pair, kind, field, value, replace):
    source, target, variant = pair
    seed(pair, kind, count=1)
    before_source, before_target = facts(source, kind), facts(target, kind, True)
    before = source.execute("select current_timestamp").fetchone()[0]
    trace = []
    target.set_trace_callback(trace.append)
    changes = {field: value}
    substitutes_default = replace and field in STATE_DEFAULTS[kind]
    if substitutes_default:
        assert native_update(source, kind, changes, replace=True) == 1
        assert update_state_rows(target, kind, changes, replace=True) == 1
    else:
        with pytest.raises(sqlite3.IntegrityError):
            native_update(source, kind, changes, replace=replace)
        with pytest.raises(sqlite3.IntegrityError):
            update_state_rows(target, kind, changes, replace=replace)
    target.set_trace_callback(None)
    after = source.execute("select current_timestamp").fetchone()[0]
    assert not any(statement.lstrip().lower().startswith("select") for statement in trace)
    mutations = [statement for statement in trace if "set payload_json=" in statement.lower()]
    assert len(mutations) == 1
    if replace and field == "created_at":
        a = source.execute("select created_at from dispatcher_claim_leases").fetchone()[0]
        b = target.execute(
            "select created_at from state where record_type='dispatcher_claim_leases'"
        ).fetchone()[0]
        assert before <= a <= after and before <= b <= after
        rows = facts(source, kind)
        current = facts(target, kind, True)
        index = ("rowid", *STATE_FIELDS[kind]).index(field)
        assert rows[0][:index] + rows[0][index+1:] == current[0][:index] + current[0][index+1:]
    else:
        assert facts(source, kind) == facts(target, kind, True)
    if not substitutes_default:
        assert facts(source, kind) == before_source
        assert facts(target, kind, True) == before_target
    assert source.in_transaction and target.in_transaction


def test_bulk_statement_abort_preserves_all_candidates_and_outer_work(pair):
    source, target, variant = pair
    kind = "conversation_runtime_sessions"
    for position, route in enumerate(("left", "right")):
        values = required(variant, kind, "same") | {"route_name": route}
        native_insert(source, kind, values, position + 1)
        insert_state(target, kind, values, source_rowid=position + 1)
    seed(pair, "service_state", count=1)
    before = facts(source, kind)
    sentinel = facts(source, "service_state")
    with pytest.raises(sqlite3.IntegrityError):
        native_update(source, kind, {"route_name": "collision"})
    with pytest.raises(sqlite3.IntegrityError):
        update_state_rows(target, kind, {"route_name": "collision"})
    assert facts(source, kind) == facts(target, kind, True) == before
    assert facts(source, "service_state") == facts(target, "service_state", True) == sentinel
    assert source.in_transaction and target.in_transaction


def test_bulk_actual_route_rename_preserves_source_identity_and_replace_count(pair):
    source, target, variant = pair
    kind = "conversation_runtime_sessions"
    for rowid, conversation, route in ((99,"c1","old"),(-7,"c2","old"),(3,"c1","new"),(0,"c2","new")):
        values = required(variant, kind, str(rowid)) | {
            "conversation_id": conversation, "route_name": route,
        }
        native_insert(source, kind, values, rowid)
        insert_state(target, kind, values, source_rowid=rowid)
    assert native_update(source, kind, {"route_name":"new"}, where_sql="route_name=?",
                         parameters=("old",), replace=True) == 2
    assert update_state_rows(target, kind, {"route_name":"new"}, where_sql="route_name=?",
                             parameters=("old",), replace=True) == 2
    assert facts(source, kind) == facts(target, kind, True)
    assert {row[0] for row in facts(target, kind, True)} == {99,-7}


def test_bulk_requires_transaction_and_nonempty_fields():
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        with pytest.raises(sqlite3.ProgrammingError, match="caller transaction"):
            update_state_rows(db, "service_state", {"value":"new"})
        assert not db.in_transaction
        db.execute("begin")
        with pytest.raises(ValueError, match="requires fields"):
            update_state_rows(db, "service_state", {})
        assert db.in_transaction
        assert db.execute("select count(*) from state").fetchone() == (0,)
