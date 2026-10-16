# State target index enumeration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development for implementation and sequential independent SPEC then QUALITY.

**Goal:** Allow an identical State index to be recreated without misclassifying the schema, preserving the existing native constructor's dispatcher-index repair behavior.
**Architecture:** Normalize only the target declaration fingerprint: sort index records by name and omit their PRAGMA index_list enumeration seq. Keep SQL, ordered index columns/seqno, unique/origin/partial and table columns/FKs/triggers exact. Source capture, inverse metadata and State physical DDL remain unchanged.
**Tech Stack:** Python3.13.12, SQLite3.50.4, pytest.

The current pure State predicate rejects an identical dispatcher terminal index after reconstruction because SQLite changes index enumeration order. This prerequisite changes no caller, constructor, schema version, publication, release selection, production database or provider behavior.

Modify only app/state_schema_release.py TARGET_HASH and the fingerprint lines in _target_shape. Keep SOURCE_HASHES and every other function AST unchanged.

Replace TARGET_HASH with f867c00d08bbf1cc66d479b25e471644944329485065e21cd30c18f571772c88, the fingerprint of the same physical State declaration with the target-only normalization below.

Replace this exact line:
```python
    if _fingerprint(_source_declaration(db, "state")) != TARGET_HASH:
```
with:
```python
    declaration = _source_declaration(db, "state")
    # PRAGMA index_list sequence/order reflects creation history, not index shape.
    # Preserve each ordered index column, SQL, uniqueness, origin and predicate.
    declaration["indexes"] = sorted(
        ({key: value for key, value in index.items() if key != "seq"}
         for index in declaration["indexes"]),
        key=lambda index: index["name"],
    )
    if _fingerprint(declaration) != TARGET_HASH:
```

Create tests/test_state_target_index_history.py exactly:
```python
"""State target recognition ignores only SQLite index enumeration history."""
from contextlib import closing
import sqlite3

import pytest

from app import state_schema_release as release
from app.action_reply_storage import _fingerprint, _source_declaration
from app.state_definitions import STATE_INDEX_DDL
from app.state_storage import create_state_schema, update_state
from tests.test_state_schema_release import native as native


@pytest.mark.parametrize("sql", STATE_INDEX_DDL)
def test_identical_index_recreation_keeps_target_current(sql):
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        assert release.state_schema_is_current(db)
        name = sql.split(" on state(", 1)[0].split()[-1]
        db.execute("drop index " + name)
        assert not release.state_schema_is_current(db)
        db.execute(sql)
        assert release.state_schema_is_current(db)


def test_inverse_after_recreated_index_uses_current_facts_and_exact_source(native):
    db, variant = native
    metadata = release.capture_state_source(db)
    assert _fingerprint(metadata["tables"]) == release.SOURCE_HASHES[variant]
    release.migrate_state_schema(db, metadata)
    sql = next(sql for sql in STATE_INDEX_DDL if "idx_state_dispatcher_terminal" in sql)
    db.execute("drop index idx_state_dispatcher_terminal")
    db.execute(sql)
    assert update_state(db, "service_state", -7, {"value": b"current-value"}) == 1
    assert release.state_schema_is_current(db)
    release.restore_state_schema(db, metadata)
    assert db.execute("select value,typeof(value) from service_state where rowid=-7").fetchone() == (b"current-value", "blob")
    assert {name: _source_declaration(db, name) for name in release.SOURCE_TABLES} == metadata["tables"]
    assert release.capture_state_source(db) == metadata
    release.migrate_state_schema(db, metadata)
    assert release.state_schema_is_current(db)


@pytest.mark.parametrize("replacement", [
    "create index idx_state_dispatcher_terminal on state(terminal_at,adapter_name,source_id) where record_type='dispatcher_claim_leases'",
    "create unique index idx_state_dispatcher_terminal on state(terminal_at) where record_type='dispatcher_claim_leases'",
    "create index idx_state_dispatcher_terminal on state(terminal_at) where record_type='service_state'",
    "create index idx_state_dispatcher_terminal on state(terminal_at desc) where record_type='dispatcher_claim_leases'",
    "create index idx_state_dispatcher_terminal on state(terminal_at collate nocase) where record_type='dispatcher_claim_leases'",
])
def test_changed_index_definition_remains_unsupported(replacement):
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        db.execute("drop index idx_state_dispatcher_terminal")
        db.execute(replacement)
        assert not release.state_schema_is_current(db)


@pytest.mark.parametrize("extra", [
    "create index extra on state(value)",
    "create trigger extra after insert on state begin select 1; end",
    "create temp table service_state(key)",
    "create table service_state(key)",
    "alter table state add column extra text",
])
def test_other_declaration_or_namespace_changes_remain_unsupported(extra):
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        db.execute(extra)
        assert not release.state_schema_is_current(db)
```

- [x] Root disposable preflight: run new tests on unchanged module and record intended index reconstruction failures, then run complete candidate plus test_state_schema_release, test_state_org_index_order and test_state_upgrade_progress in an owned TemporaryDirectory. Require new cases all green and prior source/inverse/transaction checks intact.
- [x] Apply exact module delta and regression file after root preflight. Use pinned Python /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python, PYTHONDONTWRITEBYTECODE=1, pytest -p no:cacheprovider --basetemp inside owned TemporaryDirectory.
- [x] Verify Ruff --no-cache, compile in memory, git diff --check and source/DDL unchanged. Update the scoped State paragraphs in docs/architecture.md and docs/runtime-mechanism.md after tests; claim and commit only own files.
- [x] Fresh independent SPEC, then fresh QUALITY, including current-fact inverse after reconstruction and rejection of changed index SQL/columns/order/collation/predicate, extra objects and native/TEMP source collisions.


Root corrected preflight: 21 intended RED /11 existing passes, then88 GREEN in2.26s. Initial fixture used nonexistent source_kind in a malformed-index example; changed it to the existing adapter_name before accepting evidence. Original module6f84c26b11890280e6495340552790626ef6d0e8cd4b352b9243f4bad1876a87; candidate39f211440dfe2b571972bacc6fc85508b9b0faa59f4c598cf3263db8c6f2bbaf; tests8f56ddde50ec6e741f807dc58b3e494b7e7d21f9b3dff629bf0657cb1b3ee763. Owned temporary directory removed.

Root acceptance: independent SPEC88/2.46s and QUALITY44/0.71s PASS, including12 new adversarial cases. Receipt ../specs/2026-10-16-state-target-index-history-acceptance.json. No current State selection or production change.
