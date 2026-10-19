# State bulk UPDATE SQL expressions

Preserve existing native guarded recovery UPDATE clocks in the final SQLite statement. The23-method unselected mixed Store proposal is blocked because its preselected timestamp can cross a second boundary before the guarded write. Extend only existing update_state_rows using the same trusted encoded-JSON SQL convention as upsert_state. No schema field, current caller/constructor/release/publication selection, policy, fallback or new helper is needed.

Base app/state_storage.py SHA256 cc45a425358cd43c7432100a6f062786c103e2610b084555ff424754d37c8e18; complete applied candidate a634f116a12c55ff41a78d7579d2a1c160bc56117253afcd150fd8d57f0fc865. Root disposable preflight: all34 new cases RED on the missing capability, then34new+386adjacent=420 pass6.65s with the complete candidate; Ruff/compile pass and owned temporary removed. New test file SHA256 cd3c26ce855ae53f96d4dc4070ee2eb99570464859ba91dda144bd9b300d95dc.

Implement mechanically: replace only the existing function below in app/state_storage.py; every other function/import/declaration remains unchanged. Write the second block exactly to tests/test_state_bulk_update_expressions.py. Parameter order is bound encoded leaves, expression parameters, kind, then guard parameters. Reject duplicate/unknown/missing assignments before SQL. OR REPLACE encoded NULL expressions receive original defaults only for existing NOT NULL/defaulted fields, using coalesce/nullif to evaluate the supplied expression once in the final statement. Defaultless NOT NULL fields still fail with native statement ABORT, preserving earlier caller work. Plain updates retain native nullable NULLs and constraints. All current no-expression callers retain their behavior.

```python
def update_state_rows(db, kind, values, *, update_json=None, update_parameters=(),
                      where_sql="", parameters=(), replace=False):
    """One native UPDATE; trusted encoded-JSON expressions run in this statement."""
    update_json = {} if update_json is None else update_json
    _check(kind, values)
    _check(kind, update_json)
    if not values and not update_json:
        raise ValueError("State bulk UPDATE requires fields")
    if set(values) & set(update_json):
        raise ValueError("State UPDATE field supplied twice")
    expressions = {}
    bindings = []
    for field, value in values.items():
        if (replace and field in STATE_NOT_NULL[kind]
                and field in STATE_DEFAULTS[kind] and _source_bound_null(value)):
            expressions[field] = "json_quote(" + STATE_DEFAULTS[kind][field] + ")"
        else:
            expressions[field] = "?"
            bindings.append(json.dumps(
                _encoded_native_sqlite_value(value), ensure_ascii=False,
                allow_nan=False, separators=(",", ":"),
            ))
    for field, expression in update_json.items():
        if replace and field in STATE_NOT_NULL[kind] and field in STATE_DEFAULTS[kind]:
            expression = ("coalesce(nullif(json(" + expression +
                          "),'null'),json_quote(" + STATE_DEFAULTS[kind][field] + "))")
        expressions[field] = expression
    _lock(db)
    sql = "update " + ("or replace " if replace else "") + "state set payload_json="
    sql += _json_assignments("state.payload_json", expressions)
    sql += " where record_type=?"
    if where_sql:
        sql += " and (" + where_sql + ")"
    return db.execute(sql, (*bindings, *update_parameters, kind, *parameters)).rowcount
```

```python
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
    rows = seed(pair, "conversations")
    for db in (source, target):
        if db is source:
            db.execute("update conversations set codex_session_contract_hash=?", (b"\x00\xff",))
        else:
            update_state_rows(db, "conversations", {"codex_session_contract_hash": b"\x00\xff"})
    leaf = target.execute("select payload_json->'$.codex_session_contract_hash' from state").fetchone()[0]
    assert source.execute(
        "update conversations set codex_session_id=null where conversation_id=?",
        (rows[0]["conversation_id"],),
    ).rowcount == 1
    assert update_state_rows(
        target, "conversations", {}, update_json={"codex_session_id": "json_quote(null)"},
        where_sql="conversation_id=?", parameters=(rows[0]["conversation_id"],),
    ) == 1
    assert facts(source, "conversations") == facts(target, "conversations", True)
    assert target.execute("select payload_json->'$.codex_session_contract_hash' from state").fetchone()[0] == leaf


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
```

- [x] Apply exact function/tests, keep other ASTs and State/source declarations unchanged.
- [x] Verify34RED then420GREEN using pinnedPython3.13.12/SQLite3.50.4; pytest -p no:cacheprovider --basetemp within an owned TemporaryDirectory(dir=/tmp), PYTHONDONTWRITEBYTECODE=1. Focused matrix: newtests, bulk_update, upsert, schema_release, storage, native_integer_binding. No full suite.
- [x] Ruff check --no-cache, in-memory compile, byte/AST identity, diff check; update scoped architecture/runtime and source-preparation docs after tests; commit own files only.
- [x] Root obtains fresh independent SPEC then QUALITY; exact ee25a861 accepted in ../specs/2026-10-16-state-bulk-expression-acceptance.json.
- [ ] Do not activate any caller; replace the mixed-proposal clock workaround after this acceptance and rerun that proposal's differential review. Production/main/push/deploy, real DB/provider/nativeCLI and sends remain excluded.

