"""Publication marker selection has an explicit current declaration boundary."""
from contextlib import closing
import sqlite3

import pytest

from app.state_publication_sources import publication_dispatcher_source, publication_store_version
from app.state_storage import create_state_schema, insert_state

CURRENT = "2026-10-14.1"


@pytest.mark.parametrize("version", (None, "2026-10-11.2", "2026-10-13.5", "unknown", CURRENT))
def test_native_source_marker_and_current_refusal(version):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table service_state(key text primary key,value text,updated_at text)")
        if version is not None:
            db.execute("insert into service_state values('store_schema_version',?,'t')", (version,))
        before = tuple(db.iterdump())
        if version == CURRENT:
            with pytest.raises(ValueError, match="requires State"):
                publication_store_version(db, current_version=CURRENT)
        else:
            assert publication_store_version(db, current_version=CURRENT) == version
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("version", (None, "2026-10-13.5", "unknown", CURRENT))
def test_state_only_selects_current_version(version):
    with closing(sqlite3.connect(":memory:")) as db:
        create_state_schema(db)
        db.execute("begin")
        if version is not None:
            insert_state(db, "service_state", {"key": "store_schema_version", "value": version})
        before = tuple(db.iterdump())
        if version == CURRENT:
            assert publication_store_version(db, current_version=CURRENT) == CURRENT
        else:
            with pytest.raises(ValueError, match="State publication version"):
                publication_store_version(db, current_version=CURRENT)
        assert tuple(db.iterdump()) == before
        assert db.in_transaction


@pytest.mark.parametrize("shape", ("view", "table", "mixed", "temp", "changed_index"))
def test_declared_invalid_current_never_selects_native_source(shape):
    with closing(sqlite3.connect(":memory:")) as db:
        if shape == "view":
            db.execute("create view state as select 1 as value")
        elif shape == "table":
            db.execute("create table state(value text)")
        else:
            create_state_schema(db)
            if shape == "mixed":
                db.execute("create table service_state(key text,value text)")
                db.execute("insert into service_state values('store_schema_version','2026-10-13.5')")
            elif shape == "temp":
                db.execute("create temp table state(value text)")
            else:
                db.execute("drop index idx_state_dispatcher_expiry")
        before = tuple(db.iterdump())
        with pytest.raises(ValueError, match="State publication source schema"):
            publication_store_version(db, current_version=CURRENT)
        assert tuple(db.iterdump()) == before


def test_empty_bootstrap_has_no_version():
    with closing(sqlite3.connect(":memory:")) as db:
        assert publication_store_version(db, current_version=CURRENT) is None
        assert not db.in_transaction


def test_current_read_only_file_is_unchanged(tmp_path):
    path = tmp_path / "current.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        create_state_schema(db)
        db.execute("begin")
        insert_state(db, "service_state", {"key": "store_schema_version", "value": CURRENT})
        db.commit()
    before = path.read_bytes()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        assert publication_store_version(db, current_version=CURRENT) == CURRENT
    assert path.read_bytes() == before


@pytest.mark.parametrize("current", (False, True))
def test_dispatcher_reader_preserves_selected_facts_and_types(current):
    with closing(sqlite3.connect(":memory:")) as db:
        if current:
            create_state_schema(db)
            db.execute("begin")
            insert_state(db, "service_state", {"key": "store_schema_version", "value": CURRENT})
            insert_state(db, "dispatcher_claim_leases", {
                "adapter_name": "reply", "source_id": "007", "owner": "worker",
                "owner_pid": 42, "lease_expires_at": "future", "terminal_at": "",
            })
        else:
            db.execute("create table service_state(key text,value text)")
            db.execute("insert into service_state values('store_schema_version','2026-10-13.5')")
            db.execute("create table dispatcher_claim_leases(adapter_name text,source_id text,"
                       "owner text,owner_pid integer,lease_expires_at text,terminal_at text)")
            db.execute("insert into dispatcher_claim_leases values('reply','007','worker',42,'future','')")
        before = tuple(db.iterdump())
        source = publication_dispatcher_source(db, current_version=CURRENT)
        assert db.execute("select adapter_name,source_id,owner,owner_pid,lease_expires_at,"
                          "terminal_at,typeof(source_id),typeof(owner_pid) from (" + source + ")").fetchone() == (
            "reply", "007", "worker", 42, "future", "", "text", "integer")
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("expiry,terminal,expected", (
    ("2099-01-01 00:00:00", "", 1),
    ("2000-01-01 00:00:00", "", 0),
    ("2099-01-01 00:00:00", "done", 0),
))
def test_current_quiet_wait_reads_dispatcher_without_native_table(tmp_path, expiry, terminal, expected):
    from app.repository_updater import in_flight_work
    from app.store import AutoReplyStore
    path = tmp_path / "quiet.sqlite3"
    store = AutoReplyStore(path)
    with store._connect() as db:
        db.execute("begin")
        insert_state(db, "dispatcher_claim_leases", {
            "adapter_name": "reply", "source_id": "007", "owner": "worker",
            "owner_pid": 42, "lease_expires_at": expiry, "terminal_at": terminal,
        })
    before = path.read_bytes()
    assert in_flight_work(path) == expected
    assert path.read_bytes() == before
