# Set-based State update for existing Store lifecycle callers

> Required workflow: subagent-driven-development; fresh SPEC then fresh QUALITY before acceptance.

**Goal:** Finish the storage prerequisite needed to translate existing multirow session/reset/lock/route writes without splitting one native UPDATE into separately committing candidate changes. Existing point update would need N operations and would drift on statement ABORT; source actual operations can use one SQLite UPDATE.

**Decision and actual caller scope:** Add only update_state_rows to app/state_storage.py plus import json, preserving every existing function body. Supplied field names remain the existing15-kind/85-field declaration. Use existing scalar encoding and _json_assignments to change only supplied leaves, scoped to record_type. Caller owns transaction/commit/rollback. Plain UPDATE statement ABORT and matched-row counts come directly from SQLite. No new field, table, constraint, index, source identity/DDL, marker, status policy, constructor/current caller or release selection.

Read-only source research inspects Store17967–18840 and four recovery lock deletions. Runtime-session clear/CAS and OAuth mirror need bulk conversations UPDATE; legacy reset/clear and lock renewal need guarded UPDATE; route rename needs two UPDATE OR REPLACE operations. Route rename only changes route_name from one validated distinct string to another. Original non-null (conversation_id,route_name) keys ensure candidate destination keys are distinct and destination victims cannot themselves be old-route candidates; runtime pause has at most one candidate. Thus source vs physical scan order cannot select different candidate survivors in these actual operations. Do not add hypothetical ordering machinery or represent this primitive as an engine for arbitrary competing key-collapsing rewrites. Original source ROWID remains legacy_key and never changes.

Relevant exact source anchors: Store18057/18083 clear max(delete_count,update_count);17986 OAuth mirror;18307 hash setter returns first update count;18342 lock acquire stale DELETE+INSERT IGNORE and same-owner read;18377 lock release;18401 renewal;18772/18783/18795 legacy resets;18199 route rename updates18247/18255 and capability UPSERT/DELETE. Recovery lock DELETEs13003/13155/13227/17779 can use direct scoped State DELETE;17737/17761 recovery subqueries still need State projections. Existing transaction/retry wrappers stay unchanged. Deferred caller BEGIN is needed where original _connect relies on implicit DML; do not replace it with a new immediate-lock policy.

## Complete application change

Add import json beside the existing standard-library imports in app/state_storage.py. Append exactly this function; no other function or declaration changes.

```python
def update_state_rows(db, kind, values, *, where_sql="", parameters=(), replace=False):
    """One native UPDATE for source-field predicates; caller owns transaction."""
    _check(kind, values)
    if not values:
        raise ValueError("State bulk UPDATE requires fields")
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
    _lock(db)
    sql = "update " + ("or replace " if replace else "") + "state set payload_json="
    sql += _json_assignments("state.payload_json", expressions)
    sql += " where record_type=?"
    if where_sql:
        sql += " and (" + where_sql + ")"
    return db.execute(sql, (*bindings, kind, *parameters)).rowcount
```

Original NOT NULL + DEFAULT substitution on supplied NULL/NaN under REPLACE is evaluated in the final UPDATE using original SQL defaults. Defaultless NOT NULL and nullable fields keep native behavior. Only supplied leaves are encoded; untouched stored tags remain byte-exact. No per-row Python reserialization, fresh default evaluation for unrelated fields, UDF, parser, generic compatibility adapter or per-row SAVEPOINT is added. Predicate SQL is trusted original-field SQL from callers; it is not a source ROWID alias translator.

## Complete tests/test_state_bulk_update.py

```python
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
```

## Verification and remaining work

- [x] Root complete executable proposal: missing API causes collection RED; corrected75 new cases plus342 adjacent pass417 in18.22s, Ruff passes, own /tmp TemporaryDirectory removed and absence verified.
- [x] Implement exact function/import/new75 cases and scoped behavior docs in docs/architecture.md and docs/runtime-mechanism.md; new API remains unselected.
- [x] Focused417 suite, Ruff/compile/plain diff, exact canonical byte comparison, all existing storage-function ASTs and State/source declarations unchanged.
- [ ] Fresh SPEC then fresh QUALITY.
- [ ] Prepare complete Store lifecycle/recovery and Email cursor caller patches; integrate the entire current State closure with constructors/manifests/version/progress/publication atomically.
- [ ] Complete combined copied-data/current-fact inverse/genuine-old-code/cold/native-CLI/name-set acceptance before selecting/deploying.

The first full preflight had4 failing test expectations because conversations.title has NOT NULL but NO original default. The test now reads STATE_DEFAULTS and verifies that even OR REPLACE must fail there; no fictitious default was added and the proposed implementation needed no change. The final75 cases cover90 native count/guard/scope checkpoints across15 actual/fresh families, scalar affinities and untouched encoded BLOB leaf, defaulted/defaultless NULL/NaN cases, final-statement clock and absence of SELECT/default pre-evaluation, multirow uniqueness ABORT preserving prior caller work, real route rename destination replacement/count/identities with reversed physical/native order, and explicit transaction/field requirements.

Separate completed predecessor756a6c7e corrects native integer-subclass encoding and is independently SPEC/QUALITY accepted; no change to it here. Existing State target fingerprint327c94 and native captureSHA604496 remain unchanged. No actual/copied production database, native CLI/provider/send, main/push/deploy or production action in this prerequisite. Denied cleanup /private/tmp/state-upsert-impl.694bXO remains untouched.
