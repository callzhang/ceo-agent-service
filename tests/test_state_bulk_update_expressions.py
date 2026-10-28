"""Native SQL-expression State updates execute inside the final statement."""
import sqlite3

import pytest

from app.state_definitions import _quote
from app.state_storage import update_state_rows
from tests.test_state_bulk_update import seed
from tests.test_state_storage import facts, pair as pair


def test_statement_clock_guard_and_no_preselection(pair):
    source, target, _variant = pair
    rows = seed(pair, "dispatcher_claim_leases")
    source_calls, target_calls = [], []
    for db, calls in ((source, source_calls), (target, target_calls)):
        db.create_function("statement_clock", 0, lambda calls=calls:
                           calls.append("tick") or "2030-01-01 00:00:00")
    trace = []
    target.set_trace_callback(trace.append)
    for key, count in ((rows[0]["source_id"], 1), ("absent", 0)):
        assert source.execute(
            "update dispatcher_claim_leases set owner=?,updated_at=statement_clock() "
            "where source_id=?", ("new-owner", key),
        ).rowcount == count
        assert update_state_rows(
            target, "dispatcher_claim_leases", {"owner": "new-owner"},
            update_json={"updated_at": "json_quote(statement_clock())"},
            where_sql="source_id=?", parameters=(key,),
        ) == count
    assert source_calls == target_calls == ["tick"]
    assert not any(sql.lstrip().lower().startswith("select") for sql in trace)
    assert facts(source, "dispatcher_claim_leases") == facts(target, "dispatcher_claim_leases", True)


def test_bound_values_expression_and_guard_parameter_order(pair):
    source, target, _variant = pair
    rows = seed(pair, "service_state")
    key = rows[1]["key"]
    assert source.execute(
        "update service_state set value=?,updated_at=? where key=?",
        (b"\x00\xff", "clock", key),
    ).rowcount == 1
    assert update_state_rows(
        target, "service_state", {"value": b"\x00\xff"},
        update_json={"updated_at": "json_quote(?)"}, update_parameters=("clock",),
        where_sql="key=?", parameters=(key,),
    ) == 1
    assert facts(source, "service_state") == facts(target, "service_state", True)


@pytest.mark.parametrize("kind", ["service_state", "dispatcher_claim_leases", "setup_wizard_steps"])
@pytest.mark.parametrize("expression", ["null", "json_quote(null)", "json_quote(?)"])
def test_replace_expression_null_uses_original_default_once(pair, kind, expression):
    source, target, _variant = pair
    seed(pair, kind)
    source_calls, target_calls = [], []
    for db, calls in ((source, source_calls), (target, target_calls)):
        db.create_function("current_timestamp", 0, lambda calls=calls:
                           calls.append("default") or "2031-02-03 04:05:06")
    assert source.execute(f"update or replace {_quote(kind)} set updated_at=null").rowcount == 3
    assert update_state_rows(
        target, kind, {}, update_json={"updated_at": expression},
        update_parameters=(None,) if expression.endswith("(?)") else (), replace=True,
    ) == 3
    assert source_calls == target_calls == ["default"] * 3
    assert facts(source, kind) == facts(target, kind, True)


@pytest.mark.parametrize("replace", [False, True])
def test_defaultless_expression_abort_preserves_prior_caller_work(pair, replace):
    source, target, _variant = pair
    rows = seed(pair, "conversations")
    for db in (source, target):
        db.execute("create table caller_note(value)")
        db.execute("insert into caller_note values('keep')")
    prior = facts(source, "conversations")
    sql = "case when conversation_id=? then 'changed' else null end"
    with pytest.raises(sqlite3.IntegrityError):
        source.execute(
            "update " + ("or replace " if replace else "") +
            "conversations set title=" + sql, (rows[0]["conversation_id"],),
        )
    with pytest.raises(sqlite3.IntegrityError):
        update_state_rows(
            target, "conversations", {},
            update_json={"title": "json_quote(" + sql + ")"},
            update_parameters=(rows[0]["conversation_id"],), replace=replace,
        )
    assert facts(source, "conversations") == prior == facts(target, "conversations", True)
    for db in (source, target):
        assert db.in_transaction
        assert db.execute("select value from caller_note").fetchone()[0] == "keep"


def test_nullable_expression_and_exact_existing_blob_leaf(pair):
    source, target, _variant = pair
    rows = seed(pair, "org_user_profiles")
    for db in (source, target):
        if db is source:
            db.execute("update org_user_profiles set manager_user_id=?", (b"\x00\xff",))
        else:
            update_state_rows(db, "org_user_profiles", {"manager_user_id": b"\x00\xff"})
    leaf = target.execute("select payload_json->'$.manager_user_id' from state").fetchone()[0]
    assert source.execute(
        "update org_user_profiles set open_dingtalk_id=null where user_id=?",
        (rows[0]["user_id"],),
    ).rowcount == 1
    assert update_state_rows(
        target, "org_user_profiles", {}, update_json={"open_dingtalk_id": "json_quote(null)"},
        where_sql="user_id=?", parameters=(rows[0]["user_id"],),
    ) == 1
    assert facts(source, "org_user_profiles") == facts(target, "org_user_profiles", True)
    assert target.execute("select payload_json->'$.manager_user_id' from state").fetchone()[0] == leaf


@pytest.mark.parametrize("values,expressions", [
    ({}, {}),
    ({}, {"not_a_field": "json_quote(1)"}),
    ({"key": "one"}, {"key": "json_quote('two')"}),
])
def test_expression_validation_before_sql(pair, values, expressions):
    _source, target, _variant = pair
    trace = []
    target.set_trace_callback(trace.append)
    with pytest.raises(ValueError):
        update_state_rows(target, "service_state", values, update_json=expressions)
    assert trace == []
