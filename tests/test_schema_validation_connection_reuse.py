"""Reuse parsed read-only schemas, never the outcome of mandatory validation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from threading import Barrier

import pytest

from app import current_schema_manifest as schema
from app.sqlite_connections import _close_idle_connections
from app.store import AutoReplyStore


@pytest.fixture
def current_store(tmp_path):
    store = AutoReplyStore(tmp_path / "current.sqlite3")
    _close_idle_connections()
    return store


def _readonly_opens(monkeypatch):
    opened = []
    statements = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        if kwargs.get("uri") and "mode=ro" in str(args[0]):
            opened.append(db)
            db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    return opened, statements


def test_validation_reuses_one_readonly_connection_but_checks_every_call(current_store, monkeypatch):
    opened, statements = _readonly_opens(monkeypatch)
    checks = []
    original = schema.schema_is_current

    def checked(db, **kwargs):
        checks.append(db)
        return original(db, **kwargs)

    monkeypatch.setattr(schema, "schema_is_current", checked)
    before = current_store.path.read_bytes()
    for _ in range(6):
        schema.require_current_pair(current_store.path)
    assert len(opened) == 1
    assert len(checks) == 6
    assert sum(sql.lower() == "begin" for sql in statements) == 6
    assert current_store.path.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        opened[0].execute("create table forbidden_write(id integer)")


def test_reused_connection_reads_changed_version_without_a_schema_change(current_store):
    schema.require_current_pair(current_store.path)
    current_store.set_service_state("store_schema_version", "not-current")
    with pytest.raises(sqlite3.IntegrityError, match="unsupported Store version"):
        schema.require_current_pair(current_store.path)
    current_store.set_service_state("store_schema_version", schema.STORE_VERSION)
    schema.require_current_pair(current_store.path)


def test_reused_connection_rechecks_ddl_and_recovers_only_after_real_repair(current_store):
    schema.require_current_pair(current_store.path)
    with closing(sqlite3.connect(current_store.path)) as db:
        db.execute("create index unexpected_guard_index on state(record_type)")
        db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        schema.require_current_pair(current_store.path)
    with closing(sqlite3.connect(current_store.path)) as db:
        db.execute("drop index unexpected_guard_index")
        db.commit()
    schema.require_current_pair(current_store.path)


def test_replaced_file_cannot_reuse_an_old_valid_snapshot(current_store, tmp_path):
    replacement = AutoReplyStore(tmp_path / "replacement.sqlite3")
    replacement.set_service_state("store_schema_version", "replacement-is-invalid")
    _close_idle_connections()
    for path in (current_store.path, replacement.path):
        with closing(sqlite3.connect(path)) as db:
            db.execute("pragma wal_checkpoint(truncate)")
            db.execute("pragma journal_mode=delete")
    schema.require_current_pair(current_store.path)
    replacement.path.replace(current_store.path)
    with pytest.raises(sqlite3.IntegrityError, match="unsupported Store version"):
        schema.require_current_pair(current_store.path)


def test_each_thread_keeps_its_own_readonly_connection(current_store, monkeypatch):
    opened, _statements = _readonly_opens(monkeypatch)
    barrier = Barrier(2)

    def read():
        try:
            barrier.wait(timeout=5)
            for _ in range(3):
                schema.require_current_pair(current_store.path)
        finally:
            _close_idle_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _index: read(), range(2)))
    assert len(opened) == 2


def test_nested_readonly_transactions_use_distinct_connections(current_store):
    from app.sqlite_connections import readonly_connection

    with readonly_connection(current_store.path) as outer:
        outer.execute("begin")
        with readonly_connection(current_store.path) as inner:
            inner.execute("begin")
            assert inner is not outer
            assert inner.execute("select count(*) from state").fetchone()[0] > 0
        assert outer.execute("select count(*) from state").fetchone()[0] > 0


def test_restore_into_existing_file_is_checked_again(current_store, tmp_path):
    replacement = AutoReplyStore(tmp_path / "restore.sqlite3")
    replacement.set_service_state("store_schema_version", "restored-invalid-version")
    _close_idle_connections()
    schema.require_current_pair(current_store.path)
    with closing(sqlite3.connect(replacement.path)) as source:
        with closing(sqlite3.connect(current_store.path)) as target:
            source.backup(target)
    with pytest.raises(sqlite3.IntegrityError, match="unsupported Store version"):
        schema.require_current_pair(current_store.path)


def test_failed_readonly_context_is_closed_and_not_pooled(current_store):
    from app.sqlite_connections import readonly_connection

    with pytest.raises(RuntimeError, match="read failed"):
        with readonly_connection(current_store.path) as failed:
            failed.execute("begin")
            raise RuntimeError("read failed")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        failed.execute("select 1")
    with readonly_connection(current_store.path) as retry:
        assert retry is not failed
        assert not retry.in_transaction


def test_file_replaced_while_opening_is_not_pooled_under_new_identity(current_store, tmp_path, monkeypatch):
    from app import sqlite_connections

    replacement = AutoReplyStore(tmp_path / "open-race.sqlite3")
    replacement.set_service_state("store_schema_version", "replacement-is-invalid")
    _close_idle_connections()
    for path in (current_store.path, replacement.path):
        with closing(sqlite3.connect(path)) as db:
            db.execute("pragma wal_checkpoint(truncate)")
            db.execute("pragma journal_mode=delete")
    original = sqlite_connections._open_readonly
    replaced = False

    def replace_after_open(path):
        nonlocal replaced
        connection = original(path)
        if not replaced:
            replaced = True
            replacement.path.replace(current_store.path)
        return connection

    monkeypatch.setattr(sqlite_connections, "_open_readonly", replace_after_open)
    schema.require_current_pair(current_store.path)
    with pytest.raises(sqlite3.IntegrityError, match="unsupported Store version"):
        schema.require_current_pair(current_store.path)
