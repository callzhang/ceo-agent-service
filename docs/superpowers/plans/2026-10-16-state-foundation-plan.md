# Unselected State Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and verify one unselected State declaration and caller-transactional point-write interface for the fifteen retained native state families.

**Architecture:** One ordinary `state` table stores the original typed facts and independent signed native row identities. Existing native scalar encoding, generated typed aliases and per-family partial indices preserve native SQLite affinity, NULL, uniqueness and arithmetic-boundary behavior. No current caller, constructor, release coordinator or schema marker selects this foundation.

**Tech Stack:** Python 3.13.12, sqlite3 SQLite 3.50.4 with math functions, pytest, Ruff; existing `app.action_storage` codec and immutable actual/fresh source fixtures.

---

## Scope and evidence

This is a bounded prerequisite of the approved ten-core-plus-ledger design, not final consolidation acceptance. Source authority is `docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json` plus the independently accepted source oracle. Capture remains16 families actual91/fresh87 fields. This foundation covers15 retained kinds/85 original fields/62 shared typed aliases. Runtime metadata is literal code; runtime never reads design documents.

The proposal omits empty, unconsumed `corpus_sources` and four actual-only conversation fields (`in_handoff`, `handoff_message_id`, `handoff_message_create_time`, `manual_clear_message_id`). This step does not delete them: the later release must refuse a nonempty corpus or any nondefault handoff value/type and preserve exact source DDL for inverse. Other consumed conversation session/hash fields remain.

Only source constraints are represented: original NOT NULL, primary-key uniqueness including nullable single TEXT keys, dispatcher generation>=0 and strict cursor uidvalidity>0/last_seen_uid>=0. No new JSON-field, boolean, enum, status, FK, trigger, authorization or retention policy is introduced. Encoded payload completeness/type shape is a storage representation constraint.

The common stored fields are id/record_type/legacy_key/payload_json; all62 original-field aliases are VIRTUAL. Independent signed source ROWIDs, including0/negative/deleted-highest reuse, remain distinct from the global physical id. At INT64_MAX, a bounded-lifetime in-memory native SQLite allocator reproduces SQLite's random free-positive branch, matching existing Configs/Email practice; normal allocation uses the per-kind maximum and no scratch connection. There is no persistent auxiliary table or counter.

Writes require a caller-owned transaction and acquire its writer lock before guarded reads/identity allocation. A single-row update is one SQLite UPDATE after one guarded read under that lock. No helper commits, outer rollbacks, read-path repair, alternate runtime storage or compatibility routing. Future callers must begin transactions explicitly and evaluate source SQL expressions in SQLite; multirow source-statement ABORT semantics require explicit caller handling before selection. This plan does not claim those callers are already converted.

The dispatcher terminal index includes record_type, terminal_at and the source ROWID expression. A disposable counterexample showed that omitting leading record_type caused SQLite to choose the source identity index and sort in a temporary B-tree. The final plan requires the native prune tie order and an index-backed no-sort plan. Organization name/open-id indices remain nonunique; no ordering policy is added to unordered public lookups.

Root preflight extracted all three complete modules below into a task-owned overlay package:135 tests passed in2.08s; Ruff passed; the directory was removed and absence verified. An earlier prototype had a declaration-order syntax error; corrected before this plan. A subsequent terminal-index query-plan failure was corrected as described above. No repository app/test files were changed by preflight.

## Task 1: Install the exact synthetic differential test

**Files:**
- Create: `tests/test_state_storage.py`
- Read-only authority: `tests/configs_source_fixtures.py`, `tests/test_state_source_contract.py`, captured native metadata.

- [ ] **Step 1: Write the complete test module below.**

```python
"""State primitives compared with literal approved native SQLite declarations."""
from contextlib import closing
import sqlite3
import json

import pytest

from app.state_definitions import (
    STATE_FIELDS, STATE_PRIMARY_KEYS, STATE_TYPES, _quote, state_projection,
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
    removed = {"in_handoff", "handoff_message_id", "handoff_message_create_time", "manual_clear_message_id"}
    assert set(KINDS) == set(SOURCE["groups"]["state"]) - {"corpus_sources"}
    assert len(KINDS) == 15
    assert sum(map(len, STATE_FIELDS.values())) == 85
    assert len(STATE_TYPES) == 62
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
```

- [ ] **Step 2: Run the new test before creating application modules and record the missing-module RED.**

Run from the isolated worktree:
```bash
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_state_storage.py
```
Expected: collection fails because app.state_definitions is absent. Do not weaken source fixtures or assertions.

## Task 2: Install the complete declaration and write primitives

**Files:**
- Create: `app/state_definitions.py`
- Create: `app/state_storage.py`
- Preserve existing app caller/constructor/release paths.

- [ ] **Step 1: Write this complete declaration module.**

```python
"""Finite State declarations from the approved actual/fresh native inventory.

Only retained native fields are stored. Runtime does not load source artifacts.
Schema/caller selection and native-table retirement belong to the release.
"""
from app.action_email_definitions import _encoded_value, _encoded_value_check

STATE_FIELDS = {'channel_login_reservations': ('channel', 'reservation_owner', 'reserved_at'),
 'codex_session_locks': ('conversation_id', 'owner', 'locked_at'),
 'conversation_runtime_sessions': ('conversation_id',
                                   'route_name',
                                   'session_id',
                                   'contract_hash',
                                   'updated_at'),
 'conversations': ('conversation_id',
                   'title',
                   'single_chat',
                   'codex_session_id',
                   'codex_session_contract_hash'),
 'daily_scan_state': ('scanner_name', 'last_success_at', 'cursor_json', 'last_error', 'updated_at'),
 'dispatcher_claim_leases': ('adapter_name',
                             'source_id',
                             'owner',
                             'owner_pid',
                             'generation',
                             'lease_expires_at',
                             'terminal_at',
                             'last_error',
                             'created_at',
                             'updated_at'),
 'email_historical_traversals': ('account_id',
                                 'folder',
                                 'model_id',
                                 'uidvalidity',
                                 'last_seen_uid',
                                 'updated_at'),
 'email_scan_cursors': ('account_id',
                        'folder',
                        'uidvalidity',
                        'last_seen_uid',
                        'last_success_at',
                        'last_error'),
 'org_cache_metadata': ('key', 'value_json', 'updated_at'),
 'org_user_profiles': ('user_id',
                       'name',
                       'open_dingtalk_id',
                       'manager_user_id',
                       'department_ids_json',
                       'fetched_at',
                       'title',
                       'manager_name',
                       'department_names_json',
                       'org_labels_json',
                       'has_subordinate'),
 'runtime_route_pauses': ('route_name', 'failure_code', 'retry_at', 'opened_at', 'updated_at'),
 'seen_messages': ('message_id', 'conversation_id', 'seen_at'),
 'service_state': ('key', 'value', 'updated_at'),
 'setup_wizard_steps': ('step_id',
                        'status',
                        'summary',
                        'manual_confirmed_at',
                        'manual_confirmed_by',
                        'updated_at'),
 'wechat_read_state': ('account_id',
                       'account_dir',
                       'db_dir',
                       'app_version',
                       'self_user_id',
                       'capability_status',
                       'capability_reason',
                       'watermark_sent_at',
                       'watermark_message_id',
                       'last_scan_at',
                       'updated_at')}

STATE_TYPES = {'account_dir': 'TEXT',
 'account_id': 'TEXT',
 'adapter_name': 'TEXT',
 'app_version': 'TEXT',
 'capability_reason': 'TEXT',
 'capability_status': 'TEXT',
 'channel': 'TEXT',
 'codex_session_contract_hash': 'TEXT',
 'codex_session_id': 'TEXT',
 'contract_hash': 'TEXT',
 'conversation_id': 'TEXT',
 'created_at': 'TEXT',
 'cursor_json': 'TEXT',
 'db_dir': 'TEXT',
 'department_ids_json': 'TEXT',
 'department_names_json': 'TEXT',
 'failure_code': 'TEXT',
 'fetched_at': 'TEXT',
 'folder': 'TEXT',
 'generation': 'INTEGER',
 'has_subordinate': 'INTEGER',
 'key': 'TEXT',
 'last_error': 'TEXT',
 'last_scan_at': 'TEXT',
 'last_seen_uid': 'INTEGER',
 'last_success_at': 'TEXT',
 'lease_expires_at': 'TEXT',
 'locked_at': 'TEXT',
 'manager_name': 'TEXT',
 'manager_user_id': 'TEXT',
 'manual_confirmed_at': 'TEXT',
 'manual_confirmed_by': 'TEXT',
 'message_id': 'TEXT',
 'model_id': 'TEXT',
 'name': 'TEXT',
 'open_dingtalk_id': 'TEXT',
 'opened_at': 'TEXT',
 'org_labels_json': 'TEXT',
 'owner': 'TEXT',
 'owner_pid': 'INTEGER',
 'reservation_owner': 'TEXT',
 'reserved_at': 'TEXT',
 'retry_at': 'TEXT',
 'route_name': 'TEXT',
 'scanner_name': 'TEXT',
 'seen_at': 'TEXT',
 'self_user_id': 'TEXT',
 'session_id': 'TEXT',
 'single_chat': 'INTEGER',
 'source_id': 'TEXT',
 'status': 'TEXT',
 'step_id': 'TEXT',
 'summary': 'TEXT',
 'terminal_at': 'TEXT',
 'title': 'TEXT',
 'uidvalidity': 'INTEGER',
 'updated_at': 'TEXT',
 'user_id': 'TEXT',
 'value': 'TEXT',
 'value_json': 'TEXT',
 'watermark_message_id': 'TEXT',
 'watermark_sent_at': 'TEXT'}

STATE_NOT_NULL = {'channel_login_reservations': ('reservation_owner', 'reserved_at'),
 'codex_session_locks': ('owner', 'locked_at'),
 'conversation_runtime_sessions': ('conversation_id',
                                   'route_name',
                                   'session_id',
                                   'contract_hash',
                                   'updated_at'),
 'conversations': ('title', 'single_chat', 'codex_session_contract_hash'),
 'daily_scan_state': ('last_success_at', 'cursor_json', 'last_error', 'updated_at'),
 'dispatcher_claim_leases': ('adapter_name',
                             'source_id',
                             'owner',
                             'owner_pid',
                             'generation',
                             'lease_expires_at',
                             'terminal_at',
                             'last_error',
                             'created_at',
                             'updated_at'),
 'email_historical_traversals': ('account_id', 'folder', 'model_id', 'last_seen_uid', 'updated_at'),
 'email_scan_cursors': ('account_id',
                        'folder',
                        'uidvalidity',
                        'last_seen_uid',
                        'last_success_at',
                        'last_error'),
 'org_cache_metadata': ('value_json', 'updated_at'),
 'org_user_profiles': ('name',
                       'department_ids_json',
                       'fetched_at',
                       'title',
                       'manager_name',
                       'department_names_json',
                       'org_labels_json'),
 'runtime_route_pauses': ('failure_code', 'retry_at', 'opened_at', 'updated_at'),
 'seen_messages': ('conversation_id', 'seen_at'),
 'service_state': ('value', 'updated_at'),
 'setup_wizard_steps': ('status',
                        'summary',
                        'manual_confirmed_at',
                        'manual_confirmed_by',
                        'updated_at'),
 'wechat_read_state': ('account_dir',
                       'db_dir',
                       'app_version',
                       'self_user_id',
                       'capability_status',
                       'capability_reason',
                       'watermark_sent_at',
                       'watermark_message_id',
                       'last_scan_at',
                       'updated_at')}

STATE_PRIMARY_KEYS = {'channel_login_reservations': ('channel',),
 'codex_session_locks': ('conversation_id',),
 'conversation_runtime_sessions': ('conversation_id', 'route_name'),
 'conversations': ('conversation_id',),
 'daily_scan_state': ('scanner_name',),
 'dispatcher_claim_leases': ('adapter_name', 'source_id'),
 'email_historical_traversals': ('account_id', 'folder', 'model_id'),
 'email_scan_cursors': ('account_id', 'folder'),
 'org_cache_metadata': ('key',),
 'org_user_profiles': ('user_id',),
 'runtime_route_pauses': ('route_name',),
 'seen_messages': ('message_id',),
 'service_state': ('key',),
 'setup_wizard_steps': ('step_id',),
 'wechat_read_state': ('account_id',)}

STATE_DEFAULTS = {'channel_login_reservations': {},
 'codex_session_locks': {'locked_at': 'current_timestamp'},
 'conversation_runtime_sessions': {'contract_hash': "''", 'updated_at': 'current_timestamp'},
 'conversations': {'codex_session_contract_hash': "''"},
 'daily_scan_state': {'last_success_at': "''",
                      'cursor_json': "'{}'",
                      'last_error': "''",
                      'updated_at': 'current_timestamp'},
 'dispatcher_claim_leases': {'owner': "''",
                             'owner_pid': '0',
                             'generation': '0',
                             'lease_expires_at': "''",
                             'terminal_at': "''",
                             'last_error': "''",
                             'created_at': 'current_timestamp',
                             'updated_at': 'current_timestamp'},
 'email_historical_traversals': {'last_seen_uid': '0'},
 'email_scan_cursors': {'last_success_at': "''", 'last_error': "''"},
 'org_cache_metadata': {'updated_at': 'current_timestamp'},
 'org_user_profiles': {'name': "''",
                       'fetched_at': 'current_timestamp',
                       'title': "''",
                       'manager_name': "''",
                       'department_names_json': "'[]'",
                       'org_labels_json': "'[]'"},
 'runtime_route_pauses': {'opened_at': 'current_timestamp', 'updated_at': 'current_timestamp'},
 'seen_messages': {'seen_at': 'current_timestamp'},
 'service_state': {'updated_at': 'current_timestamp'},
 'setup_wizard_steps': {'summary': "''",
                        'manual_confirmed_at': "''",
                        'manual_confirmed_by': "''",
                        'updated_at': 'current_timestamp'},
 'wechat_read_state': {'self_user_id': "''",
                       'capability_status': "'blocked'",
                       'capability_reason': "''",
                       'watermark_sent_at': "''",
                       'watermark_message_id': "''",
                       'last_scan_at': "''",
                       'updated_at': 'current_timestamp'}}


STATE_CHECKS = {
    "dispatcher_claim_leases": ("generation>=0",),
    "email_scan_cursors": ("uidvalidity>0", "last_seen_uid>=0"),
}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _definitions():
    kinds = tuple(STATE_FIELDS)
    parts = [
        "id integer primary key",
        "record_type text not null",
        "legacy_key text not null check(legacy_key=cast(cast(legacy_key as integer) as text))",
        "payload_json text not null check(json_valid(payload_json) and json_type(payload_json)='object')",
    ]
    for field, affinity in STATE_TYPES.items():
        scope = ",".join(repr(kind) for kind in kinds if field in STATE_FIELDS[kind])
        expression = _encoded_value("payload_json", "$." + field)
        parts.append(_quote(field) + " " + affinity + " generated always as "
                     "(case when record_type in (" + scope + ") then " + expression + " end) virtual")
    parts.append("check(record_type in (" + ",".join(map(repr, kinds)) + "))")
    for kind, fields in STATE_FIELDS.items():
        terms = ["json_type(payload_json,'$." + field + "') is not null" for field in fields]
        terms.extend(_encoded_value_check("payload_json", "$." + field) for field in fields)
        terms.extend(_quote(field) + " is not null" for field in STATE_NOT_NULL[kind])
        paths = ",".join(repr("$." + field) for field in fields)
        terms.append("json_remove(payload_json," + paths + ")='{}'")
        terms.extend(STATE_CHECKS.get(kind, ()))
        parts.append("check(record_type<>" + repr(kind) + " or (" +
                     " and ".join("(" + term + ")" for term in terms) + "))")
    indexes = [
        "create unique index idx_state_source_rowid on state(record_type,cast(legacy_key as integer))",
    ]
    for kind, fields in STATE_PRIMARY_KEYS.items():
        indexes.append("create unique index " + _quote("idx_state_" + kind + "_pk") +
                       " on state(" + ",".join(map(_quote, fields)) +
                       ") where record_type=" + repr(kind))
    indexes.extend((
        "create index idx_state_dispatcher_terminal on state(record_type,terminal_at,cast(legacy_key as integer)) "
        "where record_type='dispatcher_claim_leases' and terminal_at<>''",
        "create index idx_state_dispatcher_expiry on state(adapter_name,lease_expires_at,source_id) "
        "where record_type='dispatcher_claim_leases'",
        "create index idx_state_org_name on state(name) where record_type='org_user_profiles'",
        "create index idx_state_org_open_id on state(open_dingtalk_id) where record_type='org_user_profiles'",
    ))
    return "create table state(" + ",".join(parts) + ")", tuple(indexes)


STATE_TABLE_DDL, STATE_INDEX_DDL = _definitions()


def state_projection(kind, *, include_rowid=False):
    if kind not in STATE_FIELDS:
        raise ValueError("unsupported State kind")
    fields = ["cast(legacy_key as integer) as rowid"] if include_rowid else []
    fields.extend(map(_quote, STATE_FIELDS[kind]))
    return "select " + ",".join(fields) + " from state where record_type=" + repr(kind)
```

- [ ] **Step 2: Write this complete storage module.**

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
    STATE_DEFAULTS, STATE_FIELDS, STATE_INDEX_DDL, STATE_TABLE_DDL,
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


def _completed(db, kind, values):
    fields = STATE_FIELDS[kind]
    expressions = ["?" if field in values else STATE_DEFAULTS[kind].get(field, "null")
                   for field in fields]
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
        (kind, str(identity), _native_payload(_completed(db, kind, values))),
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
```

- [ ] **Step 3: Run the focused tests and verify native parity, original-field shape and transaction behavior.**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_state_storage.py tests/test_state_source_contract.py tests/test_native_scalar_codec.py
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/state_definitions.py app/state_storage.py tests/test_state_storage.py
git diff --check
```
Expected:135 State foundation cases,172 source oracle cases and17 codec cases pass (324 total); source metadata6 cases are a separate adjacent selection if run. Compile the three new files without leaving bytecode. Put pytest temporary artifacts in an owned /tmp directory and remove only that directory after verification.

- [ ] **Step 4: Document the verified scope in the same commit.**

Append to `docs/database-redesign-config-state-source-preparation.md` with actual commands/counts/interpreter/cleanup evidence. Add one scoped paragraph to `docs/architecture.md` and `docs/runtime-mechanism.md` saying this foundation is unselected, fifteen original state kinds share one declaration, writes require caller-owned transactions, and current callers/releases still use their existing selected paths. Do not rewrite unrelated accepted sections or claim sixteen source tables were removed. Update only this task's checkboxes and own claim row.

- [ ] **Step 5: Self-review and commit owned hunks.**

```bash
git add app/state_definitions.py app/state_storage.py tests/test_state_storage.py
git diff --cached --check
```
Stage only owned doc hunks after inspecting them; commit with message `Add native-parity State storage foundation`. No push/main merge/production mutation/restart/deploy, source DB replay, native CLI or provider calls.

## Task 3: Independent acceptance

- [ ] **Step 1: Fresh SPEC review** checks all code against the frozen literal source and this scope, including affinity values/types, NULL/defaults, signed identities, source constraints, CAS/replace/transaction behavior and terminal index order/plan. Review may run synthetic tests only; do not edit implementation. Report blockers precisely; implementer resolves them before re-review.
- [ ] **Step 2: Fresh QUALITY review after SPEC passes** checks simplicity, hidden behavior changes, source metadata integrity, native scalar use, transaction ownership and focused test strength. No caller/migration/production acceptance is inferred.
- [ ] **Step 3: Root records bounded acceptance** and continues the separate source-transfer/current-fact inverse and atomic caller/publication closure. The accepted intermediate still has82 ordinary tables until State selection is actually implemented and copied-database verified; this foundation alone does not change that count.

## Self-review

The three full code blocks compile and match the extracted proposal. Every declaration field is traced to captured native metadata. Original source oracle remains unchanged; omitted fields need explicit later forward eligibility and exact inverse. Point writes and tests are defined here completely; migration and callers are explicitly outside this bounded implementation, and remain required by the program. Final ten-core-plus-ledger, current actual copy, genuine predecessor read/write, native CLI fake-outbound, CI, and production approval gates remain open.
