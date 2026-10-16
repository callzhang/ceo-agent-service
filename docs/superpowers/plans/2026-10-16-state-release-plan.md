# Unselected State Migration and Inverse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an unselected State schema migration and current-fact inverse for the sixteen native families using the accepted fifteen-kind storage foundation.

**Architecture:** One pure module captures only exact original declarations, validates the confirmed empty/default retirements, moves the retained original facts into State and verifies identities/values/types before dropping native tables. Inverse reconstructs original declarations from metadata and current State facts, including facts created or changed after migration. The caller owns the FK-off transaction, rollback and release selection; no runtime caller or publication mechanism is selected in this unit.

**Tech Stack:** Python3.13.12 / SQLite3.50.4, existing source metadata fingerprints, accepted native State codec/primitives, pytest, Ruff.

---

## Scope, eligibility and acceptance boundary

Foundation acceptance is exact `b14198d`, recorded by `4d11f05d`. Literal source metadata SHA604496766a335d63fe334a263bde8028cd53c6bc5311acb97a5ef48876ae2aff remains unchanged. Root query-only frozen970a probe independently matches aggregate16-family native source fingerprints actual b7e149e432369018ba105f9e1ac12b052f38bfe245ee7e6d7b7b7b8b3ab9caa6 and fresh48e1608e66d2a35fdb467708e24fd6bef1f2f4ecd58e8a6e45c4af9fb6449a70. Source actual91/fresh87 fields include85 retained fields. All source families are ordinary ROWID tables without AUTOINCREMENT or source FK/trigger requirements.

User has authorized cancellation of unused/redundant fields and features. Current app search finds corpus_sources only in its unused CREATE TABLE; no current app occurrence of the four actual-only conversation fields. Frozen source corpus has0 rows; all523 conversations have integer0/NULL/NULL/NULL for the four old fields. Forward **rechecks** these exact empty/default conditions before any State DDL, including if facts changed after metadata capture. Nonempty corpus or any nondefault/type-shifted conversation value refuses this migration without source deletion. Inverse rebuilds the exact old corpus declaration empty and exact conversation declaration with its original default/null constants. No arbitrary source bodies, snapshots, logs, original trajectory or source row image go into metadata.

All85 retained facts and each family's independent signed source ROWID are compared in both directions with SQLite values and typeof before native tables disappear. Since source ROWIDs and State source identities are unique, the symmetric difference cannot conceal duplicate rows. The inverse reads current State values and restores rows changed/created/deleted after forward; it never uses an old row image. Original table SQL, column order and source indices are restored exactly. Any exception remains inside the caller transaction; the caller rollback is tested after injected partial forward and inverse failures.

The existing dependency probes are reused for exact source/target release closure; this adds no user authorization, review policy, outbound behavior, runtime storage fallback or auto-repair. State schema predicate is read-only. Current constructors, source-shaped callers, version-marker readers, repository updater and paired publication are **outside this unit** and remain required before runtime selection. No schema version, ledger write, current manifest, Store constructor, release coordinator or service restart changes here. The last accepted full copied candidate is82 ordinary tables;67 is only the expected intermediate count after a future integrated State cutover, not a result established by this pure module.

The plan's complete code was extracted to an owned disposable overlay and passed29 synthetic tests in0.83s plus Ruff; temporary directory removed/absence verified. Earlier23-case preflight also passed before adding scalar-storage-class, eligibility-recheck and inverse-dependency coverage. No repository application/test files were created by preflight.

## Task 1: Add the source-bound tests first

**Files:**
- Create `tests/test_state_schema_release.py`.
- Read-only: existing `tests/configs_source_fixtures.py`, immutable captured metadata, accepted State modules.

- [x] **Step 1: Write the complete test module.**

```python
"""Synthetic source-bound State release and inverse before runtime selection."""
from contextlib import closing
import json
import sqlite3

import pytest

from app import state_schema_release as release
from app.action_reply_storage import _source_declaration
from app.state_definitions import STATE_FIELDS, _quote
from app.state_storage import delete_state, insert_state, update_state
from tests.configs_source_fixtures import capture


@pytest.fixture(params=("actual", "fresh"))
def native(request):
    metadata = capture()[request.param]["tables"]
    with closing(sqlite3.connect(":memory:")) as db:
        for name in release.SOURCE_TABLES:
            db.execute(metadata[name]["sql"])
            for index in reversed(metadata[name]["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        for name, fields in STATE_FIELDS.items():
            values = {}
            for column in metadata[name]["columns"]:
                if column["name"] not in fields:
                    continue
                if column["pk"] or column["notnull"] and column["dflt_value"] is None:
                    values[column["name"]] = column["name"] if column["type"] == "TEXT" else 1
            columns = ("rowid", *values)
            db.execute("insert into " + _quote(name) + "(" + ",".join(map(_quote, columns)) +
                       ") values(" + ",".join("?" for _ in columns) + ")", (-7, *values.values()))
        db.commit()
        db.execute("begin")
        yield db, request.param


def image(db):
    result = {}
    for (name,) in db.execute("select name from sqlite_schema where type='table' and name not glob 'sqlite_*'"):
        fields = [row[1] for row in db.execute("pragma table_xinfo(" + _quote(name) + ")") if row[6] == 0]
        selected = ("rowid", *fields)
        projection = ",".join(map(_quote, selected)) + "," + ",".join(
            "typeof(" + _quote(field) + ")" for field in selected)
        rows = [tuple(row) for row in db.execute("select " + projection +
                                               " from " + _quote(name) + " order by rowid")]
        result[name] = (_source_declaration(db, name), rows)
    return result


def test_forward_exact_inverse_reforward_and_current_facts(native):
    db, variant = native
    db.execute("update service_state set value='not-a-saved-input-body' where rowid=-7")
    before = image(db)
    metadata = release.capture_state_source(db)
    assert set(metadata) == {"variant", "tables"}
    assert metadata["variant"] == variant
    assert "not-a-saved-input-body" not in json.dumps(metadata)
    assert not release.state_schema_is_current(db)
    counts = release.migrate_state_schema(db, metadata)
    assert set(image(db)) == {"state"}
    assert release.state_schema_is_current(db)
    assert counts == {name: 0 if name == "corpus_sources" else 1 for name in release.SOURCE_TABLES}
    assert db.in_transaction
    assert release.restore_state_schema(db, metadata) == counts
    assert image(db) == before
    assert db.in_transaction
    release.migrate_state_schema(db, metadata)
    assert update_state(db, "service_state", -7, {"value": "changed-current-value"}) == 1
    assert delete_state(db, "seen_messages", -7) == 1
    assert insert_state(db, "service_state", {
        "key": "new-current-key", "value": "not-a-saved-input-body", "updated_at": "t",
    }, source_rowid=0).lastrowid == 0
    assert insert_state(db, "conversations", {
        "conversation_id": "new", "title": "new", "single_chat": 1,
    }, source_rowid=0).lastrowid == 0
    release.restore_state_schema(db, metadata)
    assert db.execute("select value from service_state where rowid=-7").fetchone() == ("changed-current-value",)
    assert db.execute("select value from service_state where rowid=0").fetchone() == ("not-a-saved-input-body",)
    assert db.execute("select count(*) from seen_messages").fetchone() == (0,)
    if variant == "actual":
        assert db.execute("select in_handoff,handoff_message_id,handoff_message_create_time,"
                          "manual_clear_message_id from conversations where rowid=0").fetchone() == (0, None, None, None)
    current = image(db)
    release.migrate_state_schema(db, metadata)
    release.restore_state_schema(db, metadata)
    assert image(db) == current
    assert db.execute("pragma quick_check").fetchone() == ("ok",)
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_corpus_nonempty_refused_before_ddl(native):
    db, _ = native
    db.execute("insert into corpus_sources(source_key,last_collected_at) values('source','t')")
    before = image(db)
    with pytest.raises(ValueError, match="nonempty corpus"):
        release.capture_state_source(db)
    assert image(db) == before


@pytest.mark.parametrize("field,value", (
    ("in_handoff", 1), ("in_handoff", b"0"), ("handoff_message_id", ""),
    ("handoff_message_create_time", "t"), ("manual_clear_message_id", "id"),
))
def test_actual_unused_nondefault_fields_refused_before_ddl(field, value):
    tables = capture()["actual"]["tables"]
    with closing(sqlite3.connect(":memory:")) as db:
        for kind in release.SOURCE_TABLES:
            db.execute(tables[kind]["sql"])
            for index in reversed(tables[kind]["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        db.execute("insert into conversations(conversation_id,title,single_chat) values('c','t',1)")
        db.execute("update conversations set " + _quote(field) + "=?", (value,))
        before = image(db)
        with pytest.raises(ValueError, match="nondefault unused"):
            release.capture_state_source(db)
        assert image(db) == before


def test_source_drift_metadata_drift_and_target_drift_refused(native):
    db, _ = native
    metadata = release.capture_state_source(db)
    wrong = json.loads(json.dumps(metadata))
    wrong["tables"]["service_state"]["sql"] += " "
    before = image(db)
    with pytest.raises(ValueError, match="metadata"):
        release.migrate_state_schema(db, wrong)
    assert image(db) == before
    db.execute("create index unsupported on service_state(value)")
    before = image(db)
    with pytest.raises(ValueError, match="source declarations"):
        release.capture_state_source(db)
    assert image(db) == before
    db.execute("drop index unsupported")
    release.migrate_state_schema(db, metadata)
    db.execute("create index unsupported on state(value)")
    assert not release.state_schema_is_current(db)
    before = image(db)
    with pytest.raises(ValueError, match="target declaration"):
        release.restore_state_schema(db, metadata)
    assert image(db) == before


@pytest.mark.parametrize("sql", (
    "create temp table SERVICE_STATE(key)",
    "create view quoted_ref as select value from 'service_state'",
    "create table inbound(id text references conversations(conversation_id))",
))
def test_external_reference_or_temp_shadow_refused(native, sql):
    db, _ = native
    db.execute(sql)
    before = image(db)
    with pytest.raises(ValueError):
        release.capture_state_source(db)
    assert image(db) == before


def test_forward_failure_rollback_restores_original(native, monkeypatch):
    db, _ = native
    before = image(db)
    metadata = release.capture_state_source(db)
    real = release.insert_state
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("injected-copy-failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(release, "insert_state", fail_second)
    with pytest.raises(RuntimeError, match="injected-copy-failure"):
        release.migrate_state_schema(db, metadata)
    assert db.in_transaction
    db.rollback()
    assert image(db) == before


def test_inverse_failure_rollback_restores_current_target(native, monkeypatch):
    db, _ = native
    metadata = release.capture_state_source(db)
    release.migrate_state_schema(db, metadata)
    update_state(db, "service_state", -7, {"value": "current-fact"})
    db.commit()
    before = image(db)
    db.execute("begin")
    real = release._same
    calls = []

    def fail_second(*args):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("injected-inverse-failure")
        return real(*args)

    monkeypatch.setattr(release, "_same", fail_second)
    with pytest.raises(RuntimeError, match="injected-inverse-failure"):
        release.restore_state_schema(db, metadata)
    assert db.in_transaction
    db.rollback()
    assert image(db) == before


def test_requires_caller_fk_off_transaction(native):
    db, _ = native
    db.rollback()
    before = image(db)
    with pytest.raises(sqlite3.ProgrammingError, match="caller FK-off"):
        release.capture_state_source(db)
    assert image(db) == before
    db.execute("pragma foreign_keys=on")
    db.execute("begin")
    with pytest.raises(sqlite3.ProgrammingError, match="caller FK-off"):
        release.capture_state_source(db)
    assert image(db) == before


def test_source_scalar_storage_classes_survive_copy_and_inverse(native):
    db, _ = native
    db.execute("update dispatcher_claim_leases set owner_pid=?,last_error=? where rowid=-7",
               (-2.2101102838387424e-85, 7.294214896064974e-137))
    db.execute("update org_user_profiles set has_subordinate=?,org_labels_json=? where rowid=-7",
               (b"\\x00", b"[]"))
    db.execute("update service_state set value=? where rowid=-7", (b"runtime-bytes",))
    before = image(db)
    metadata = release.capture_state_source(db)
    release.migrate_state_schema(db, metadata)
    release.restore_state_schema(db, metadata)
    assert image(db) == before


def test_eligibility_is_rechecked_after_capture(native):
    db, _ = native
    metadata = release.capture_state_source(db)
    db.execute("insert into corpus_sources(source_key,last_collected_at) values('late','t')")
    before = image(db)
    with pytest.raises(ValueError, match="nonempty corpus"):
        release.migrate_state_schema(db, metadata)
    assert image(db) == before


def test_inverse_external_reference_refused_before_ddl(native):
    db, _ = native
    metadata = release.capture_state_source(db)
    release.migrate_state_schema(db, metadata)
    db.execute("create view current_ref as select value from state")
    before = image(db)
    with pytest.raises(ValueError):
        release.restore_state_schema(db, metadata)
    assert image(db) == before
```

- [x] **Step 2: Verify missing-module RED before adding the release module.**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_state_schema_release.py
```
Expected collection error: app.state_schema_release does not exist. Use owned /tmp pytest storage. Do not change captured source fixtures or metadata.

## Task 2: Install the complete pure release

**File:** Create `app/state_schema_release.py`.

- [x] **Step 1: Write this complete module.**

```python
"""Unselected, source-bound State migration and current-fact inverse.

The caller owns one FK-off transaction and its rollback. Metadata contains only
original declarations; no source row image, schema marker or runtime selection.
"""
import sqlite3

from app.action_email_storage import _external_dependencies
from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_storage import _no_inbound
from app.state_definitions import STATE_FIELDS, STATE_INDEX_DDL, _quote
from app.state_storage import create_state_schema, insert_state

SOURCE_TABLES = (*STATE_FIELDS, "corpus_sources")
SOURCE_HASHES = {
    "actual": "b7e149e432369018ba105f9e1ac12b052f38bfe245ee7e6d7b7b7b8b3ab9caa6",
    "fresh": "48e1608e66d2a35fdb467708e24fd6bef1f2f4ecd58e8a6e45c4af9fb6449a70",
}
TARGET_HASH = "ae25aecef5d8e9a0615ca3c180bb018eb6412a36bb25e850692d7f050eb8e5ab"
TARGET_INDEXES = tuple(sql.split(" on state(", 1)[0].split()[-1].strip('"')
                       for sql in STATE_INDEX_DDL)


def _transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise sqlite3.ProgrammingError("State release requires caller FK-off transaction")


def _present(db, schema, name):
    return db.execute("select 1 from " + schema + ".sqlite_schema "
                      "where name=? collate nocase", (name,)).fetchone() is not None


def _no_temp(db, names):
    if any(_present(db, "temp", name) for name in names):
        raise ValueError("unsupported State TEMP name collision")


def _metadata(metadata):
    if (not isinstance(metadata, dict) or set(metadata) != {"variant", "tables"}
            or metadata["variant"] not in SOURCE_HASHES
            or not isinstance(metadata["tables"], dict)
            or set(metadata["tables"]) != set(SOURCE_TABLES)
            or _fingerprint(metadata["tables"]) != SOURCE_HASHES[metadata["variant"]]):
        raise ValueError("unsupported State source declaration metadata")


def _eligible(db, variant):
    if db.execute("select 1 from main.corpus_sources limit 1").fetchone():
        raise ValueError("nonempty corpus_sources cannot be retired")
    if variant == "actual" and db.execute(
        "select 1 from main.conversations where typeof(in_handoff)<>'integer' "
        "or in_handoff is not 0 or handoff_message_id is not null "
        "or handoff_message_create_time is not null "
        "or manual_clear_message_id is not null limit 1"
    ).fetchone():
        raise ValueError("nondefault unused conversation fields cannot be retired")


def capture_state_source(db):
    """Capture exact native declarations after empty/default eligibility checks."""
    _transaction(db)
    _no_temp(db, (*SOURCE_TABLES, "state", *TARGET_INDEXES))
    if any(_present(db, "main", name) for name in ("state", *TARGET_INDEXES)):
        raise ValueError("State target name already exists")
    tables = {name: _source_declaration(db, name) for name in SOURCE_TABLES}
    fingerprint = _fingerprint(tables)
    variant = next((name for name, expected in SOURCE_HASHES.items()
                    if fingerprint == expected), None)
    if variant is None:
        raise ValueError("unsupported State source declarations")
    _eligible(db, variant)
    _no_inbound(db, SOURCE_TABLES)
    _external_dependencies(db, targets=SOURCE_TABLES, allowed_trigger_names=())
    return {"variant": variant, "tables": tables}


def _target_shape(db):
    _no_temp(db, (*SOURCE_TABLES, "state", *TARGET_INDEXES))
    if any(_present(db, "main", name) for name in SOURCE_TABLES):
        raise ValueError("native State source name remains")
    if _fingerprint(_source_declaration(db, "state")) != TARGET_HASH:
        raise ValueError("unsupported State target declaration")


def state_schema_is_current(db):
    """Recognize only the final selected declaration without changing data."""
    try:
        _target_shape(db)
    except (ValueError, sqlite3.Error):
        return False
    return True


def _same(db, kind):
    fields = STATE_FIELDS[kind]
    source_fields = ("rowid", *fields)
    native = "select " + ",".join(map(_quote, source_fields))
    native += "," + ",".join("typeof(" + _quote(f) + ")" for f in source_fields)
    native += " from main." + _quote(kind)
    target = "select cast(legacy_key as integer)," + ",".join(map(_quote, fields))
    target += ",typeof(cast(legacy_key as integer))," + ",".join(
        "typeof(" + _quote(f) + ")" for f in fields)
    target += " from main.state where record_type=" + repr(kind)
    for left, right in ((native, target), (target, native)):
        if db.execute("select 1 from (" + left + " except " + right + ") limit 1").fetchone():
            raise ValueError("State source identity, values or storage classes differ: " + kind)


def migrate_state_schema(db, metadata):
    """Verify native facts before removing sixteen original physical tables."""
    _transaction(db)
    _metadata(metadata)
    if capture_state_source(db) != metadata:
        raise ValueError("State source declaration changed")
    create_state_schema(db)
    counts = {}
    for kind, fields in STATE_FIELDS.items():
        selected = ",".join(map(_quote, ("rowid", *fields)))
        counts[kind] = 0
        for row in db.execute("select " + selected + " from main." + _quote(kind) + " order by rowid"):
            insert_state(db, kind, dict(zip(fields, tuple(row)[1:], strict=True)),
                         source_rowid=row[0])
            counts[kind] += 1
        _same(db, kind)
    counts["corpus_sources"] = 0
    for kind in SOURCE_TABLES:
        db.execute("drop table main." + _quote(kind))
    _target_shape(db)
    return counts


def restore_state_schema(db, metadata):
    """Rebuild exact old declarations using current target facts, never a snapshot."""
    _transaction(db)
    _metadata(metadata)
    _target_shape(db)
    _no_inbound(db, ("state",))
    _external_dependencies(db, targets=("state",), allowed_trigger_names=())
    index_names = tuple(index["name"] for spec in metadata["tables"].values()
                        for index in spec["indexes"] if index["sql"] is not None)
    _no_temp(db, index_names)
    if any(_present(db, "main", name) for name in index_names):
        raise ValueError("native State index name already exists")
    for kind, definition in metadata["tables"].items():
        db.execute(definition["sql"])
        for index in reversed(definition["indexes"]):
            if index["sql"] is not None:
                db.execute(index["sql"])
    counts = {}
    for kind, fields in STATE_FIELDS.items():
        columns = ",".join(map(_quote, ("rowid", *fields)))
        selected = "cast(legacy_key as integer)," + ",".join(map(_quote, fields))
        cursor = db.execute("insert into main." + _quote(kind) + "(" + columns + ") "
                            "select " + selected + " from main.state where record_type=?", (kind,))
        counts[kind] = cursor.rowcount
        _same(db, kind)
    counts["corpus_sources"] = 0
    _eligible(db, metadata["variant"])
    if {kind: _source_declaration(db, kind) for kind in SOURCE_TABLES} != metadata["tables"]:
        raise ValueError("restored State source declarations differ")
    db.execute("drop table main.state")
    return counts
```

- [x] **Step 2: Run the exact focused matrix.**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_state_schema_release.py tests/test_state_storage.py tests/test_state_source_contract.py tests/test_native_scalar_codec.py
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/state_schema_release.py tests/test_state_schema_release.py
git diff --check
```
Expected29 release +147 foundation +172 source +17 codec =365 passing cases. Compile the new Python files without leaving bytecode, compare both complete Python blocks byte-for-byte, remove only task-owned /tmp artifacts and verify absence. No actual DB, native CLI, provider or external network.

## Task 3: Correct exactly six existing source dispositions and scoped documentation

**Files:**
- `docs/superpowers/specs/2026-10-11-database-redesign-field-map.csv`: only six rows below; preserve old identity/type/NN/PK fields and all other rows.
- Own append `docs/database-redesign-config-state-source-preparation.md`.
- Own scoped State paragraphs `docs/architecture.md`, `docs/runtime-mechanism.md`.
- Own claim and implementation checkboxes only.

- [x] **Step 1: Replace the six disposition rows with this exact CSV content.**

```csv
conversations,in_handoff,INTEGER,1,0,state,取消,当前无读取方且冻结523行均为INTEGER 0；迁移前逐行复核默认值和类型；逆迁移按原DDL恢复默认0。
conversations,handoff_message_id,TEXT,0,0,state,取消,当前无读取方且冻结523行均为NULL；迁移前拒绝任何非NULL值；逆迁移按原DDL恢复NULL。
conversations,handoff_message_create_time,TEXT,0,0,state,取消,当前无读取方且冻结523行均为NULL；迁移前拒绝任何非NULL值；逆迁移按原DDL恢复NULL。
conversations,manual_clear_message_id,TEXT,0,0,state,取消,当前无读取方且冻结523行均为NULL；迁移前拒绝任何非NULL值；逆迁移按原DDL恢复NULL。
corpus_sources,source_key,TEXT,0,1,state,取消,无运行读写方且冻结表为空；迁移前拒绝任何行；逆迁移按原DDL重建空表。
corpus_sources,last_collected_at,TEXT,0,0,state,取消,无运行读写方且冻结表为空；迁移前拒绝任何行；逆迁移按原DDL重建空表。
```

- [x] **Step 2: Write verified scoped documentation.**

Append actual RED/GREEN command evidence, interpreter, no-body metadata, original declaration/current-fact inverse, guarded retirements, injected-failure transaction rollback, canonical byte equality and cleanup in source-preparation. Add one scoped paragraph in each behavior document that pure State migration/inverse is unselected and caller-owned; no current constructor/caller/version/publication cutover yet. Preserve existing foundation and other accepted feature descriptions; do not call the copied82-table candidate67 or the final goal complete. Check off Task1/2/3 implementation items and update your claim.

- [x] **Step 3: Self-review and commit owned hunks.**

```bash
git add app/state_schema_release.py tests/test_state_schema_release.py
git diff --cached --check
```
Stage only the six owned CSV rows and owned doc hunks after inspection; commit `Add source-bound State migration and current-fact inverse`. No push/main merge, actual-copy migration or production action.

## Task 4: Independent acceptance and later integration

- [ ] **Step 1:** Fresh SPEC review after implementation verifies exact source hashes/declarations, all six guarded dispositions, retained identity/value/type copy, exact original-schema inverse from current facts, no-body metadata, read-only predicate, external dependencies and caller rollback.
- [ ] **Step 2:** Fresh QUALITY review only after SPEC passes checks transaction boundaries, source constraint preservation, simplicity and regression strength.
- [ ] **Step 3:** Root records this bounded acceptance, then continues the separate atomic caller/constructor/version-reader/repository-updater/paired-publication closure and actual-copy/genuine-predecessor verification. The final reusable harness, native CLI fake-outbound branches, CI, final ten-core-plus-ledger and production approval remain required.

## Self-review

Complete code exists for both new files. Every kept/retired field is from the unchanged captured source; only six CSV dispositions change. Actual/fresh variation is bound by exact declaration metadata, not runtime fallback. Target fingerprint is the accepted single-State declaration. Preconditions and current-fact inverse have executable positive/negative/rollback coverage. The pure release can be accepted independently without claiming runtime selection or final consolidation.
