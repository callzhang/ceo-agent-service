# State Native UPSERT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the native UPSERT write primitive required to translate the existing State callers without skipping INSERT-candidate validation or losing encoded scalar facts.

**Architecture:** The existing State source-PK partial unique index is the native SQLite conflict target. One INSERT statement adds missing declared defaults and caller-provided encoded JSON expressions, then updates only explicitly listed payload fields on conflict while leaving the existing source identity untouched. JSON leaf assignments preserve the accepted native codec; no generic SQL parser, UDF, compatibility view, alternate store, schema field or policy is introduced.

**Tech Stack:** Existing Python sqlite3/SQLite3.50.4 JSON operators and State metadata/codec, pytest and Ruff.

---

## Confirmed source behavior and integration scope

The previous goal turn is progress: accepted State foundation and pure migration/current-fact inverse are committed through a893e106, including a real frozen-copy proof. This unit moves the current-caller conversion forward; it does not redefine the ten-core-plus-ledger goal as a storage scaffold.

Root reread actual sources and bounded read-only research independently confirmed that a locked read/update cannot replace the source UPSERT. Exact Store setup-wizard UPSERT with manual_confirmed_by=None fails original NOT NULL validation in both actual/fresh schemas even though the conflict UPDATE CASE would preserve the previous confirmer. A direct UPDATE succeeds. Native target partial-index UPSERT preserves this original INSERT-candidate failure, including before a false update guard.

Actual source patterns are existing excluded copies; coalesce for the conversation session; blank-preserving WeChat fields; setup-wizard CASE/SQLite clock; historical generation-sensitive max; dispatcher reset of terminal/error fields while keeping created_at; service/daily/org/session/reservation updates. Source anchors are Store lines17943,17995,18324,14496,28944,30596,31021,31164,31263,30901; EmailStore9130; dispatcher/adapters1805; repository_updater76 at a893e106. The source dispatcher computes generation with Python int+1 before the INSERT; preserve that source behavior. Email _advance_cursor and route pauses deliberately do not use UPSERT and must retain their existing separate CAS/insert/update branches.

The root used graphify only for bounded navigation:10 selected code files/166711 words,1882 nodes/5798 edges, no semantic LLM extraction, temporary graph removed. Conclusions above were verified against source, not inferred from graph ranking.

The new helper receives trusted **encoded JSON SQL expressions**. Defaults currently contain only NULL, integer literals and TEXT/SQLite-clock values, so json_quote(original_default_sql) adds omitted fields without a new REAL serializer. Explicit values use _native_payload; existing/excluded field assignments select raw encoded JSON leaves. Caller expressions creating a new REAL must encode it through the existing Python codec and bind that encoded JSON; do not reconstruct decoded REAL values with SQL json_object/json_quote. Comparisons use the affinitized generated aliases, while assignments select their original encoded leaves. Historical scalar max ties select the first source argument using >=.

Missing INSERT defaults, caller INSERT CASE expressions and conflict-update clocks execute inside the final single INSERT. Explicit NULL remains supplied and is never treated as an omitted field. INSERT candidate checks happen before conflict UPDATE; existing legacy_key remains unchanged, including negative/zero source identities. Nullable source TEXT primary keys continue to allow repeated NULL rows. The helper requires the existing caller transaction/writer lock and does not commit/rollback or add a runtime policy.

This is a bounded addition to app/state_storage.py plus a new differential test. Existing declaration/fingerprints, source capture and accepted migration code remain unchanged. Current callers/constructors/version/updater/publication still require atomic conversion after this primitive is accepted; no temporary runtime dual path is selected here. Latest combined copied candidate remains82 ordinary tables; no67-table or final-ten acceptance follows from these tests.

Root extracted the complete proposal into an owned temporary package:107 passed in1.28s, Ruff passed and directory removed/absence verified. Earlier101 passed before adding explicit WeChat behavior coverage; two test-only lambda-style Ruff findings were corrected before this plan. No repository app/test file was changed by preflight.

## Task 1: Add differential tests first

**File:** Create tests/test_state_upsert.py. Read-only fixtures/source metadata remain unchanged.

- [x] **Step 1: Write this complete test file.**

```python
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
```

- [x] **Step 2: Verify missing-API RED.**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_state_upsert.py
```
Expected collection failure importing state_json_field/upsert_state from the unchanged storage module. Use owned /tmp storage; do not alter source fixtures.

## Task 2: Install the complete storage file

**File:** app/state_storage.py. Preserve all existing function bodies; the only existing-line change is importing STATE_PRIMARY_KEYS/STATE_TYPES, followed by the three new functions below in this complete file.

- [x] **Step 1: Write the complete canonical module.**

```python
"""Caller-transactional storage for the unselected fifteen-kind State table."""
from contextlib import closing
from dataclasses import dataclass
import sqlite3

from app.action_storage import (
    _encoded_native_sqlite_value, _native_integer_primary_key,
    _native_payload, _source_bound_null,
)
from app.state_definitions import (
    STATE_DEFAULTS, STATE_FIELDS, STATE_INDEX_DDL, STATE_NOT_NULL, STATE_PRIMARY_KEYS, STATE_TABLE_DDL,
    STATE_TYPES,
    _quote,
)


@dataclass(frozen=True)
class StateWriteResult:
    lastrowid: int | None
    rowcount: int


_UNSET = object()


def create_state_schema(db: sqlite3.Connection) -> None:
    """Install declarations only; the caller owns schema selection and commit."""
    for sql in (STATE_TABLE_DDL, *STATE_INDEX_DDL):
        db.execute(sql)


def _check(kind, values):
    if kind not in STATE_FIELDS or set(values) - set(STATE_FIELDS[kind]):
        raise ValueError("unsupported State kind or source field")
    for value in values.values():
        _encoded_native_sqlite_value(value)


def _lock(db):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("State writes require a caller transaction")
    db.execute("update state set id=id where 0")


def _next_rowid(db, kind):
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from state where record_type=?",
        (kind,),
    ).fetchone()[0]
    if maximum != 2**63 - 1:
        return 1 if maximum is None else maximum + 1
    # The existing Configs/Email allocator uses the same native SQLite branch.
    # This scratch allocator never reads/writes another runtime database.
    with closing(sqlite3.connect(":memory:")) as allocator:
        allocator.execute("create table ids(id integer primary key)")
        allocator.executemany("insert into ids(id) values(?)", db.execute(
            "select cast(legacy_key as integer) from state where record_type=?", (kind,)))
        return allocator.execute("insert into ids default values").lastrowid


def _completed(db, kind, values, *, replace=False):
    fields = STATE_FIELDS[kind]
    defaults = STATE_DEFAULTS[kind]
    not_null = STATE_NOT_NULL[kind]
    expressions = [
        ("coalesce(?," + defaults[field] + ")" if replace and field in not_null
         and field in defaults else "?") if field in values else defaults.get(field, "null")
        for field in fields
    ]
    parameters = tuple(values[field] for field in fields if field in values)
    row = db.execute("select " + ",".join(expressions), parameters).fetchone()
    return dict(zip(fields, row, strict=True))


def _conflict(ignore, replace):
    if ignore and replace:
        raise ValueError("conflicting State write modes")
    return "or ignore " if ignore else "or replace " if replace else ""


def insert_state(db, kind, values, *, source_rowid=_UNSET, ignore=False, replace=False):
    _check(kind, values)
    mode = _conflict(ignore, replace)
    _lock(db)
    identity = (_next_rowid(db, kind) if source_rowid is _UNSET or _source_bound_null(source_rowid)
                else _native_integer_primary_key(source_rowid))
    cursor = db.execute(
        "insert " + mode + "into state(record_type,legacy_key,payload_json) values(?,?,?)",
        (kind, str(identity), _native_payload(_completed(db, kind, values, replace=replace))),
    )
    return StateWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def update_state(db, kind, source_rowid, values, *, new_source_rowid=_UNSET,
                 where_sql="", parameters=(), ignore=False, replace=False):
    _check(kind, values)
    mode = _conflict(ignore, replace)
    if new_source_rowid is not _UNSET:
        _encoded_native_sqlite_value(new_source_rowid)
    _lock(db)
    fields = STATE_FIELDS[kind]
    guard = " and (" + where_sql + ")" if where_sql else ""
    row = db.execute(
        "select cast(legacy_key as integer)," + ",".join(map(_quote, fields)) +
        " from state where record_type=? and cast(legacy_key as integer)=?" + guard,
        (kind, source_rowid, *parameters),
    ).fetchone()
    if row is None:
        return 0
    # The guarded read occurs once under the writer lock. No other writer can
    # change that row before this single SQLite UPDATE applies its constraints.
    old_identity = row[0]
    identity = (old_identity if new_source_rowid is _UNSET else
                _native_integer_primary_key(new_source_rowid, update=True))
    complete = dict(zip(fields, tuple(row)[1:], strict=True))
    complete.update(values)
    if replace:
        complete = _completed(db, kind, complete, replace=True)
    return db.execute(
        "update " + mode + "state set legacy_key=?,payload_json=? "
        "where record_type=? and legacy_key=?",
        (str(identity), _native_payload(complete), kind, str(old_identity)),
    ).rowcount


def delete_state(db, kind, source_rowid, *, where_sql="", parameters=()):
    _check(kind, {})
    _lock(db)
    guard = " and (" + where_sql + ")" if where_sql else ""
    return db.execute(
        "delete from state where record_type=? and cast(legacy_key as integer)=?" + guard,
        (kind, source_rowid, *parameters),
    ).rowcount


def state_json_field(field, source="excluded"):
    """SQL JSON leaf for a declared original field; retains the exact codec tag."""
    if field not in STATE_TYPES:
        raise ValueError("unsupported State source field")
    return _quote(source) + ".payload_json->'$.{}'".format(field)


def _json_assignments(base, expressions):
    if not expressions:
        return base
    return "json_set(" + base + "," + ",".join(
        "'$.{}',json({})".format(field, expression)
        for field, expression in expressions.items()
    ) + ")"


def upsert_state(db, kind, values, updates, *, insert_json=None,
                 insert_parameters=(), update_parameters=(), where_sql="",
                 where_parameters=()):
    """Native source-PK UPSERT; expression values are trusted encoded-JSON SQL.

    Defaults and caller expressions run in this one INSERT statement. Existing
    source identity stays unchanged on conflict; only payload fields are set.
    """
    insert_json = {} if insert_json is None else insert_json
    _check(kind, values)
    _check(kind, insert_json)
    _check(kind, updates)
    if not updates:
        raise ValueError("State UPSERT requires conflict update fields")
    if set(values) & set(insert_json):
        raise ValueError("State INSERT field supplied twice")
    defaults = {
        field: "json_quote(" + STATE_DEFAULTS[kind].get(field, "null") + ")"
        for field in STATE_FIELDS[kind] if field not in values and field not in insert_json
    }
    # Original defaults are only NULL, INTEGER literals and TEXT/SQLite clocks;
    # supplied REAL/BLOB facts are already exactly encoded by _native_payload.
    candidate = _json_assignments("?", defaults | insert_json)
    payload = _native_payload(values)
    _lock(db)
    identity = _next_rowid(db, kind)
    conflict = ",".join(map(_quote, STATE_PRIMARY_KEYS[kind]))
    sql = ("insert into state(record_type,legacy_key,payload_json) values(" +
           repr(kind) + ",?," + candidate + ") on conflict(" + conflict +
           ") where record_type=" + repr(kind))
    sql += " do update set payload_json=" + _json_assignments("state.payload_json", updates)
    if where_sql:
        sql += " where " + where_sql
    return db.execute(sql, (str(identity), payload, *insert_parameters,
                            *update_parameters, *where_parameters)).rowcount
```

- [x] **Step 2: Run focused acceptance and static checks.**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_state_upsert.py tests/test_state_schema_release.py tests/test_state_storage.py tests/test_state_source_contract.py tests/test_native_scalar_codec.py
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/state_storage.py tests/test_state_upsert.py
git diff --check
```
Expected107 new +365 adjacent =472 passing cases. Compile both files without bytecode; compare the two Python blocks exactly. Check State declaration, source capture, pure release, and all old storage function bodies unchanged. Remove owned /tmp artifacts and verify absence.

Implementation verification passed, but cleanup of the owned `/tmp/state-upsert-impl.694bXO` test directory was rejected by automatic approval review; its absence is unverified and cleanup remains with the root agent.

## Task 3: Document and commit the bounded addition

**Files:** Own append docs/database-redesign-config-state-source-preparation.md, own scoped State paragraph in architecture.md/runtime-mechanism.md, own claim and Task1/2/3 checkboxes in this plan.

- [x] **Step 1:** Record actual RED/GREEN, native INSERT-candidate regression, source identity/NULL keys, exact encoded leaf and one-statement clock evidence. Documents must say no current caller/constructor/version/updater/coordinator activation has happened. Do not rewrite unrelated accepted evidence or modify historical source/foundation plans.
- [x] **Step 2:** Self-review and commit owned app/test/docs hunks after staged diff check.

```bash
git add app/state_storage.py tests/test_state_upsert.py
git diff --cached --check
```
Stage only owned doc hunks; commit `Preserve native State UPSERT candidate and encoded values`. No actual DB, native CLI, provider/network/send, push/main merge or production action.

## Task 4: Independent review before caller conversion

- [x] **Step 1:** Fresh SPEC verifies native INSERT-candidate validation, source-PK conflict target/identity, NULL/default and one-statement expressions, exact JSON leaf selection, constraints and caller transactions. PASS107 in1.33s plus116 source/default/zero-ROWID/NULL-before-CASE checks and exact REAL/parameter/guard/rollback probes;365 adjacent cases were not rerun by this reviewer.
- [x] **Step 2:** Fresh QUALITY only after SPEC passes checks simplicity, hidden policy/schema changes and regression strength. PASS107 in1.91s plus independent parameter/guard/native-fact and raw encoded REAL-leaf probes; original9 bodies unchanged.
- [x] **Step 3:** Root records bounded acceptance in ../specs/2026-10-16-state-upsert-acceptance.json and continues atomic State current callers/constructors/version/updater/publication conversion. Implementation temp cleanup is explicitly pending direct user authorization after auto-review denied rm-rf; no bypass/retry. Combined actual-copy/genuine-old-code/native fake-outbound/CI/final-ten proof remains required.

## Self-review

The complete file keeps existing accepted storage code intact. Every source field comes from accepted State metadata. New expressions are explicit caller SQL with encoded JSON values, not a new interpreter or fallback. Tests compare actual/fresh native declarations for every retained kind and the confirmed wizard/session/WeChat/historical source cases. Full goal and current runtime acceptance remain open.
