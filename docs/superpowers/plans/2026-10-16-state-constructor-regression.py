"""Unselected State constructor proposal: synthetic startup boundaries only."""
from contextlib import closing
import hashlib
import sqlite3

import pytest

from app.email_store import EmailStore
from app.store import AutoReplyStore
from app.state_definitions import state_projection
from app.state_schema_release import SOURCE_TABLES, TARGET_INDEXES, state_schema_is_current
from app.state_storage import insert_state


def state_facts(path):
    with closing(sqlite3.connect(path)) as db:
        return (
            db.execute("select type,name,tbl_name,sql from sqlite_schema order by type,name").fetchall(),
            db.execute("select * from state order by id").fetchall(),
        )


@pytest.fixture
def current(tmp_path):
    path = tmp_path / "current.sqlite3"
    EmailStore(path)
    return path


@pytest.mark.parametrize("order", ("store-first", "email-first"))
def test_both_constructor_orders_retire_native_state_and_reopen(tmp_path, order):
    path = tmp_path / "fresh.sqlite3"
    if order == "store-first":
        AutoReplyStore(path)
    EmailStore(path)
    AutoReplyStore(path)
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        names = {r[0] for r in db.execute("select name from sqlite_schema")}
        assert "state" in names
        assert not names.intersection(SOURCE_TABLES)
        assert state_schema_is_current(db)
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
        assert db.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("missing", (
    ("idx_state_dispatcher_terminal",), ("idx_state_dispatcher_expiry",),
    ("idx_state_dispatcher_terminal", "idx_state_dispatcher_expiry"),
))
def test_existing_lease_indexes_repaired_without_changing_lease_or_synthesizing_session(current, missing):
    with closing(sqlite3.connect(current)) as db:
        db.execute("begin")
        insert_state(db, "dispatcher_claim_leases", {
            "adapter_name": "scheduled", "source_id": "42", "owner": "owner",
            "generation": 2, "terminal_at": "terminal",
        }, source_rowid=-7)
        insert_state(db, "conversations", {
            "conversation_id": "conversation", "title": "t", "single_chat": 1,
            "codex_session_id": "session-existing-only-in-original-conversation",
        }, source_rowid=0)
        lease = db.execute("select * from state where record_type='dispatcher_claim_leases'").fetchall()
        for name in missing:
            db.execute("drop index " + name)
        db.commit()
    AutoReplyStore(current)
    EmailStore(current)
    with closing(sqlite3.connect(current)) as db:
        assert state_schema_is_current(db)
        assert db.execute("select * from state where record_type='dispatcher_claim_leases'").fetchall() == lease
        assert db.execute(
            "select * from state where record_type='conversation_runtime_sessions'"
        ).fetchall() == []
        assert db.execute("select count(*) from (" + state_projection("conversations") +
                          ") where conversation_id='conversation'").fetchone()[0] == 1


@pytest.mark.parametrize("damage", (
    ("drop index idx_state_service_state_pk",),
    ("create table service_state(key,value,updated_at)",),
    ("create index extra on state(value)",),
    ("drop index idx_state_dispatcher_terminal",
     "create index idx_state_dispatcher_terminal on state(value)"),
    ("drop index idx_state_dispatcher_terminal",
     "create table unrelated_owner(value)",
     "create index idx_state_dispatcher_terminal on unrelated_owner(value)"),
    ("drop index idx_state_dispatcher_terminal",
     "create table idx_state_dispatcher_terminal(value)"),
))
@pytest.mark.parametrize("constructor", (AutoReplyStore, EmailStore))
def test_malformed_current_refused_without_data_or_schema_repair(current, damage, constructor):
    with closing(sqlite3.connect(current)) as db:
        for sql in damage:
            db.execute(sql)
    before = state_facts(current)
    with pytest.raises(sqlite3.IntegrityError):
        constructor(current)
    assert state_facts(current) == before


def test_store_connection_temp_source_shadow_refused_before_repair(current, monkeypatch):
    before = state_facts(current)
    original = AutoReplyStore._open_connection

    def shadow(self):
        db = original(self)
        db.execute("create temp table service_state(key,value,updated_at)")
        return db
    monkeypatch.setattr(AutoReplyStore, "_open_connection", shadow)
    with pytest.raises(sqlite3.IntegrityError):
        AutoReplyStore(current)
    assert state_facts(current) == before


@pytest.mark.parametrize("constructor", (AutoReplyStore, EmailStore))
def test_native_paired_source_refused_before_journal_or_schema_changes(tmp_path, constructor):
    path = tmp_path / "source.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table service_state(key text primary key,value text,updated_at text)")
        db.execute("insert into service_state values('store_schema_version','2026-10-13.5','source')")
        db.execute("create table email_schema_migrations(version integer primary key,applied_at text)")
        db.execute("insert into email_schema_migrations values(51,'source')")
        db.commit()
        assert db.execute("pragma journal_mode").fetchone()[0] == "delete"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.IntegrityError, match="paired schema release"):
        constructor(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()


@pytest.mark.parametrize("constructor", (AutoReplyStore, EmailStore))
@pytest.mark.parametrize("name", TARGET_INDEXES)
@pytest.mark.parametrize("owner_type", ("index", "table", "view"))
def test_fresh_reserved_index_name_refused_before_wal_or_partial_ddl(tmp_path, constructor, name, owner_type):
    from app.state_definitions import _quote
    path = tmp_path / "fresh-name.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table unrelated(value)")
        db.execute("insert into unrelated values('keep')")
        identifier = _quote(name.upper())
        if owner_type == "index":
            db.execute("create index " + identifier + " on unrelated(value)")
        elif owner_type == "table":
            db.execute("create table " + identifier + "(value)")
        else:
            db.execute("create view " + identifier + " as select value from unrelated")
        db.commit()
        assert db.execute("pragma journal_mode").fetchone()[0] == "delete"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.IntegrityError):
        constructor(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma journal_mode").fetchone()[0] == "delete"
        assert db.execute("select * from unrelated").fetchall() == [("keep",)]
        assert db.execute("select 1 from sqlite_schema where name='state'").fetchone() is None
    assert not path.with_name(path.name + "-wal").exists()


@pytest.mark.parametrize("constructor", (AutoReplyStore, EmailStore))
@pytest.mark.parametrize("name", (*SOURCE_TABLES, "state", *TARGET_INDEXES))
def test_fresh_temp_state_name_refused_before_wal_or_partial_ddl(tmp_path, monkeypatch, constructor, name):
    from app.state_definitions import _quote
    path = tmp_path / "fresh-temp.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table unrelated(value)")
        db.execute("insert into unrelated values('keep')")
        db.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    original = AutoReplyStore._open_connection

    def shadow(self):
        db = original(self)
        db.execute("create temp table " + _quote(name.upper()) + "(value)")
        return db
    monkeypatch.setattr(AutoReplyStore, "_open_connection", shadow)
    with pytest.raises(sqlite3.IntegrityError, match="TEMP name collision"):
        constructor(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma journal_mode").fetchone()[0] == "delete"
        assert db.execute("select * from unrelated").fetchall() == [("keep",)]
        assert db.execute("select 1 from sqlite_schema where name='state'").fetchone() is None
    assert not path.with_name(path.name + "-wal").exists()
