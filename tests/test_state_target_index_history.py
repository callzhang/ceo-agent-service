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
