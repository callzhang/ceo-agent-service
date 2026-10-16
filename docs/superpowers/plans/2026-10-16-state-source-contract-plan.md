# State Native Source Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commit an executable native oracle for all sixteen approved state families before selecting their single-table storage and atomic caller/release cutover.

**Architecture:** Reconstruct the literal actual and genuine published-fresh declarations from the accepted metadata using the existing source fixture. Assert source SQL behavior directly, independently of any future state target. This unit adds tests only; the complete redesign still requires state16-to1, current callers, transfer/inverse, and the remaining approved table consolidation.

**Tech Stack:** Python, sqlite3, pytest, existing captured SQLite declarations.

---

## Scope and evidence

Worktree: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service`.
Baseline: `d17c5f4f4a00daf27ad4aee0568ba11baa6078a8`.
Authoritative source metadata: `docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json` (SHA256 `604496766a335d63fe334a263bde8028cd53c6bc5311acb97a5ef48876ae2aff`).
The approved field map covers actual91/fresh87 state columns. Existing source metadata reconstruction remains unchanged.

All sixteen sources are ordinary ROWID tables with no AUTOINCREMENT or FKs. Twelve single TEXT primary keys permit NULL duplicates; four compound keys explicitly forbid NULL. Cursor CHECKs differ from historical traversal, and integer affinity is not a strict integer/boolean type. Source defaults and two nonunique org lookup indexes must remain explicit.

The four actual-only conversation columns remain source facts in this oracle. This unit does not select their retirement. The frozen970a probe shows523 default/null rows; current source has no consumers. A later explicit retirement case must reject nondefault source facts and reconstruct exact original columns/defaults on inverse.

Previously executed source-only evidence is in `../specs/2026-10-16-state-native-behavior-preflight.json`: native signed identities/CHECK probes, three genuine7dad public lease baseline cases and metadata-only default/null value coverage. Do not rerun the sufficient archived baseline merely to restate it.

No app changes, target DDL, source table retirement, schema version, migration, provider, native Agent CLI, actual database access, new retention, main merge or deployment belong to this unit.

## Files

- Create: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service/tests/test_state_source_contract.py`.
- Reuse unchanged: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service/tests/configs_source_fixtures.py`.
- Reuse unchanged: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service/tests/test_config_state_native_source.py`.
- Append only: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service/docs/database-redesign-config-state-source-preparation.md`.
- Claim and later update only the worker's own row: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service/docs/agent-claims.md`.

## Task 1: Commit the native state behavior oracle

- [x] **Step 1: Claim the exact test and own documentation appendix before editing.** Read the canonical shared policy and repository claims. Preserve all other files/hunks. Source fixtures and captured metadata are not editable implementation shortcuts.

- [x] **Step 2: Create the complete test file below.** Every field comes from captured metadata; test values are synthetic. Existing source fixture reconstruction creates its original captured declarations, rather than current app target tables.

```python
"""Native SQLite source contracts for the approved sixteen state families."""
import sqlite3

import pytest

from tests.configs_source_fixtures import capture, source_db

STATE_TABLES = tuple(capture()["groups"]["state"])


def _q(value):
    return '"' + value.replace('"', '""') + '"'


def _columns(variant, table):
    return capture()[variant]["tables"][table]["columns"]


def _row(variant, table, suffix):
    values = {}
    for column in _columns(variant, table):
        if column["pk"] or (column["notnull"] and column["dflt_value"] is None):
            assert column["type"] in ("TEXT", "INTEGER")
            values[column["name"]] = (
                f'{column["name"]}:{suffix}' if column["type"] == "TEXT" else 1
            )
    return values


def _insert(db, table, values, rowid=None):
    names = ("rowid", *values)
    return db.execute(
        f'insert into {_q(table)} ({",".join(map(_q, names))}) '
        f'values ({",".join("?" for _ in names)})',
        (rowid, *values.values()),
    ).lastrowid


@pytest.fixture(params=("actual", "fresh"))
def native(request):
    with source_db(request.param) as db:
        yield request.param, db


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_hidden_rowids_are_signed_and_reusable(native, table):
    variant, db = native
    definition = capture()[variant]["tables"][table]
    assert definition["identity"] == {
        "without_rowid": False, "autoincrement": False,
        "integer_primary_key_rowid_alias": False,
    }
    assert definition["foreign_keys"] == []
    assert definition["triggers"] == []
    assert _insert(db, table, _row(variant, table, "negative"), -7) == -7
    assert _insert(db, table, _row(variant, table, "zero"), 0) == 0
    assert _insert(db, table, _row(variant, table, "first")) == 1
    db.execute(f'delete from {_q(table)} where rowid=1')
    assert _insert(db, table, _row(variant, table, "reuse")) == 1
    assert db.execute(
        "select 1 from sqlite_sequence where name=?", (table,)
    ).fetchone() is None
    assert db.execute("pragma quick_check").fetchone() == ("ok",)


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_keys_preserve_nullability_and_uniqueness(native, table):
    variant, db = native
    columns = _columns(variant, table)
    primary = [column for column in columns if column["pk"]]
    assert primary and all(column["type"] == "TEXT" for column in primary)
    values = _row(variant, table, "original")
    _insert(db, table, values, -7)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        _insert(db, table, values, 99)
    if len(primary) == 1 and not primary[0]["notnull"]:
        for suffix in ("null-a", "null-b"):
            _insert(db, table, _row(variant, table, suffix) | {
                primary[0]["name"]: None,
            })
        assert db.execute(
            f'select count(*) from {_q(table)} where {_q(primary[0]["name"])} is null'
        ).fetchone() == (2,)
    else:
        assert all(column["notnull"] for column in primary)
        for column in primary:
            with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
                _insert(db, table, _row(variant, table, "missing") | {
                    column["name"]: None,
                })
    for value in ("Key", "key"):
        _insert(db, table, _row(variant, table, value) | {
            column["name"]: value for column in primary
        })


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_affinity_keeps_blob_and_fractional_values(native, table):
    variant, db = native
    columns = _columns(variant, table)
    assert all(column["type"] in ("TEXT", "INTEGER") for column in columns)
    numeric = {
        column["name"]: 1 if column["type"] == "TEXT" else "1"
        for column in columns
    }
    _insert(db, table, numeric, 1)
    binary = {
        column["name"]: b"1" if column["type"] == "TEXT" else "1"
        for column in columns
    }
    _insert(db, table, binary, 2)
    fractional = {
        column["name"]: "fraction:" + column["name"]
        if column["type"] == "TEXT" else 1.5
        for column in columns
    }
    _insert(db, table, fractional, 3)
    for rowid, values, text_type, integer_type in (
        (1, numeric, "text", "integer"),
        (2, binary, "blob", "integer"),
        (3, fractional, "text", "real"),
    ):
        for column in columns:
            name = column["name"]
            stored = db.execute(
                f'select {_q(name)},typeof({_q(name)}) from {_q(table)} where rowid=?',
                (rowid,),
            ).fetchone()
            expected_type = text_type if column["type"] == "TEXT" else integer_type
            expected_value = (
                ("1" if rowid == 1 else values[name]) if column["type"] == "TEXT"
                else 1.5 if rowid == 3 else 1
            )
            assert stored == (expected_value, expected_type), (table, name, rowid)


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_not_null_columns_refuse_null(native, table):
    variant, db = native
    for column in _columns(variant, table):
        if column["notnull"]:
            with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
                _insert(db, table, _row(variant, table, column["name"]) | {
                    column["name"]: None,
                })


@pytest.mark.parametrize(("table", "column", "value"), (
    ("dispatcher_claim_leases", "generation", -1),
    ("email_scan_cursors", "uidvalidity", 0),
    ("email_scan_cursors", "last_seen_uid", -1),
))
def test_native_state_declared_checks_refuse_invalid_values(native, table, column, value):
    variant, db = native
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insert(db, table, _row(variant, table, "invalid") | {column: value})


def test_native_state_distinct_integer_domains_are_not_unified(native):
    variant, db = native
    _insert(db, "email_historical_traversals",
            _row(variant, "email_historical_traversals", "historical") | {
                "uidvalidity": None, "last_seen_uid": -1,
            })
    _insert(db, "conversations", _row(variant, "conversations", "chat") | {
        "single_chat": 2,
    })
    _insert(db, "dispatcher_claim_leases",
            _row(variant, "dispatcher_claim_leases", "lease") | {"owner_pid": -1})


def test_native_state_actual_and_fresh_conversation_layouts_are_explicit(native):
    variant, db = native
    legacy = {
        "in_handoff", "handoff_message_id", "handoff_message_create_time",
        "manual_clear_message_id",
    }
    names = {column["name"] for column in _columns(variant, "conversations")}
    assert (legacy <= names) if variant == "actual" else not (legacy & names)
    state_fields = sum(len(_columns(variant, table)) for table in STATE_TABLES)
    assert state_fields == (91 if variant == "actual" else 87)
    rowid = _insert(db, "conversations", _row(variant, "conversations", "default"))
    if variant == "actual":
        assert db.execute(
            "select in_handoff,handoff_message_id,handoff_message_create_time,"
            "manual_clear_message_id from conversations where rowid=?", (rowid,),
        ).fetchone() == (0, None, None, None)


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_defaults_remain_literal(native, table):
    variant, db = native
    values = _row(variant, table, "defaults")
    before = db.execute("select current_timestamp").fetchone()[0]
    rowid = _insert(db, table, values)
    after = db.execute("select current_timestamp").fetchone()[0]
    for column in _columns(variant, table):
        name = column["name"]
        if name in values:
            continue
        actual = db.execute(
            f'select {_q(name)} from {_q(table)} where rowid=?', (rowid,)
        ).fetchone()[0]
        default = column["dflt_value"]
        if default is None:
            assert actual is None, (table, name)
        elif default.lower() == "current_timestamp":
            assert before <= actual <= after, (table, name)
        else:
            expected = db.execute("select " + default).fetchone()[0]
            assert actual == expected, (table, name)


def test_native_org_lookup_indexes_allow_duplicate_names_and_open_ids(native):
    variant, db = native
    for suffix in ("one", "two"):
        _insert(db, "org_user_profiles",
                _row(variant, "org_user_profiles", suffix) | {
                    "name": "Same name", "open_dingtalk_id": "same-open-id",
                })
    assert db.execute(
        "select count(*) from org_user_profiles where name='Same name' "
        "and open_dingtalk_id='same-open-id'"
    ).fetchone() == (2,)
    assert all(
        not row[2] for row in db.execute("pragma index_list(org_user_profiles)")
        if row[3] == "c"
    )
```

- [x] **Step 3: Run the new oracle and immutable metadata reconstruction.** Use the installed project interpreter and an owned temporary pytest root:

```python
from pathlib import Path
import os
import subprocess
import tempfile

root = Path("/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service")
python = "/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python"
with tempfile.TemporaryDirectory(prefix="state-source-contract-", dir="/tmp") as folder:
    result = subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--basetemp", str(Path(folder) / "pytest"),
         "tests/test_state_source_contract.py",
         "tests/test_config_state_native_source.py"],
        cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    assert result.returncode == 0
assert not Path(folder).exists()
```

Expected: the new file172 passes plus the existing six metadata cases,178 total. If native declarations disagree, report the exact source behavior; do not change captured DDL or strengthen constraints to match this proposal.

- [x] **Step 4: Prove the oracle detects meaningful source mutations.** Run this complete negative-control script with the same interpreter after the test file exists. The fixture monkeypatch changes only disposable in-memory source SQL; the original column metadata stays unchanged. Each selected mutant must produce exactly two intended failing cases, one per captured variant.

```python
from pathlib import Path
import os
import subprocess
import tempfile

root = Path("/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service")
python = "/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python"
mutants = (
    ("nullable", "channel_login_reservations",
     "channel text primary key", "channel text not null primary key",
     "keys_preserve_nullability_and_uniqueness and channel_login_reservations"),
    ("affinity", "service_state", "value text not null", "value blob not null",
     "affinity_keeps_blob_and_fractional_values and service_state"),
    ("generation", "dispatcher_claim_leases", " check(generation >= 0)", "",
     "declared_checks_refuse_invalid_values and dispatcher_claim_leases"),
)
child = """
import sys,pytest
sys.path.insert(0,sys.argv[1])
from tests import configs_source_fixtures as fixtures
original=fixtures.capture
def mutant():
    data=original()
    for variant in ("actual","fresh"):
        definition=data[variant]["tables"][sys.argv[3]]
        changed=definition["sql"].replace(sys.argv[4],sys.argv[5])
        assert changed != definition["sql"]
        definition["sql"]=changed
    return data
fixtures.capture=mutant
raise SystemExit(pytest.main([
    "-q","-p","no:cacheprovider","--basetemp",sys.argv[7],
    sys.argv[2],"-k",sys.argv[6],
]))
"""
with tempfile.TemporaryDirectory(prefix="state-source-negative-", dir="/tmp") as folder:
    for label, table, old, new, selection in mutants:
        result = subprocess.run(
            [python, "-c", child, str(root),
             str(root / "tests/test_state_source_contract.py"),
             table, old, new, selection, str(Path(folder) / label)],
            cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 1, (label, result.stdout, result.stderr)
        assert "2 failed, 170 deselected" in result.stdout, (label, result.stdout)
        print(label, result.stdout.strip().splitlines()[-1])
assert not Path(folder).exists()
```

- [x] **Step 5: Run scoped syntax, Ruff and diff checks.** No full repository test run.

```sh
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check tests/test_state_source_contract.py
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -c 'from pathlib import Path; p=Path("tests/test_state_source_contract.py"); compile(p.read_text(),str(p),"exec")'
git diff --check
```

- [x] **Step 6: Append concise validation and commit only the owned test/docs/claim.** Record exact tests, negative controls and cleanup. Explicitly state native-source-only acceptance and that target state, caller cutover, migration and inverse remain open.

```sh
git add tests/test_state_source_contract.py docs/database-redesign-config-state-source-preparation.md docs/agent-claims.md
git commit -m "test: freeze native state storage behavior"
```

## Task 2: Independent acceptance

- [ ] Root obtains fresh SPEC review against this exact contract and the committed actual/fresh metadata. The reviewer must inspect source-case independence and rerun the172 cases; the three mutants must fail without editing source artifacts.
- [ ] After SPEC PASS, root obtains fresh QUALITY review for fixture correctness, affinity/default/null identity coverage and no incidental runtime behavior.
- [ ] Root records the exact reviewed SHA and source-only boundary. The full ten-core-plus-ledger goal remains active.
- [ ] Root proceeds to a state target/caller/release plan using the independently researched current closure. Do not add compatibility tables/views, duplicate state or native read fallback.

## Next atomic state integration boundary

The research at the baseline found current physical SQL in Store/Email, dispatcher adapters, quality diagnostics, Attention, processing reactions, deployment quiet checks and publication readers. Caller getters alone cannot retire old tables.

Preserve the existing transaction owner and semantics: scheduled source/ledger renewal and release share a transaction; complete/release fencing differs from renew/assert; terminal prune is latest10000 ordered by terminal_at and source ROWID; login reservation and service state update together. Session clear methods and route-specific clear methods have different existing effects. Same-owner lock reacquisition does not renew. Wechat replaces nonblank older values. Scan CAS rolls back locator/classification/plan writes, while observation-only scan enqueue/cursor boundaries and historical traversal remain their existing separate contracts.

Activation must replace fresh CREATE/ALTER/backfill of native state tables, Store manifest/cache checks, native service_state version readers and current publication predicates atomically. ExistingSchemaUpgradeStateStore spans before/after publication without constructing Store; its transition must be explicit. Historical source/inverse helpers stay bound to their native sources and do not become runtime fallbacks.

The reusable copy/native harness changes remain required for final acceptance. Read-only analysis identified their exact Config/table/action/source-binding differences. They are deferred for the final selected table structure; the independently accepted config actual-copy receipt remains valid at its stated6515 scope. No final-CI/native/publication completion is claimed by this source unit.

## Root self-review and proposal validation

The complete proposed test body was executed from an owned temporary file:172 passed in1.00s (wrapper1.104s). Separate NULL-key tightening, TEXT-affinity alteration and generation-CHECK removal mutants each failed exactly two intended cases. Both temporary roots were removed and absence verified. These are disposable proposal checks, not repository implementation or target acceptance. All test fields map to existing metadata, and no target domain field was invented.

The existing authorized execution mode is subagent-driven development with sequential SPEC then QUALITY. Continue in the isolated worktree without asking for a repeated execution choice.
