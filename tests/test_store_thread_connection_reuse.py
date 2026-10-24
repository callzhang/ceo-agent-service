import sqlite3
import threading

import pytest

import app.native_trajectory as native_trajectory
from app.store import AutoReplyStore


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "reuse.sqlite3")


def _counting_opens(store, monkeypatch):
    opened = []
    original = store._open_connection

    def counting():
        connection = original()
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_open_connection", counting)
    return opened


def test_sequential_calls_in_one_thread_share_one_connection(store, monkeypatch):
    opened = _counting_opens(store, monkeypatch)
    seen = []
    for _ in range(3):
        with store._connect() as db:
            seen.append(db)
            db.execute("select 1").fetchone()
    assert len(opened) <= 1  # construction may already have left one idle
    assert seen[0] is seen[1] is seen[2]


def test_other_threads_get_their_own_connection(store):
    with store._connect() as main_db:
        pass
    seen = []

    def worker():
        with store._connect() as db:
            seen.append(db)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen and seen[0] is not main_db


def test_nested_use_opens_a_separate_connection(store):
    with store._connect() as outer:
        with store._connect() as inner:
            assert inner is not outer


def test_failed_body_rolls_back_and_discards_the_connection(store):
    with store._connect() as db:
        db.execute("create table reuse_probe(x)")
    with store._connect() as before:
        pass
    with pytest.raises(RuntimeError):
        with store._connect() as db:
            db.execute("insert into reuse_probe values (1)")
            raise RuntimeError("boom")
    with store._connect() as after:
        assert after is not before
        assert after.execute("select count(*) from reuse_probe").fetchone()[0] == 0


def test_replaced_database_file_is_not_served_from_a_stale_connection(tmp_path):
    path = tmp_path / "swap.sqlite3"
    store = AutoReplyStore(path)
    with store._connect() as stale:
        stale.execute("create table swap_marker(x)")
    path.unlink()
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    sqlite3.connect(path).close()
    with store._connect() as db:
        assert db is not stale
        assert (
            db.execute(
                "select count(*) from sqlite_master where name='swap_marker'"
            ).fetchone()[0]
            == 0
        )


def test_connection_with_foreign_keys_drift_is_not_reused(store):
    with store._connect() as first:
        first.execute("pragma foreign_keys=off")
    with store._connect() as second:
        assert second is not first
        assert second.execute("pragma foreign_keys").fetchone()[0] == 1


def test_terminal_event_hook_runs_only_for_the_use_that_wrote(store, monkeypatch):
    calls = []
    monkeypatch.setattr(
        native_trajectory,
        "terminal_cached_run_ids",
        lambda connection, path: calls.append(path) or [],
    )
    with store._connect() as db:
        db.execute("create table if not exists hook_probe(x)")
        db.execute("insert into hook_probe values (1)")
    written = len(calls)
    assert written == 1
    with store._connect() as db:
        db.execute("select count(*) from hook_probe").fetchone()
    assert len(calls) == written


def test_a_connection_going_idle_releases_its_page_cache(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    statements: list[str] = []

    with store._connect() as db:
        db.set_trace_callback(statements.append)

    # The parsed schema is what the idle slot keeps; the page cache (up to
    # SQLITE_READ_CACHE_SIZE per connection) is let go.
    assert "pragma shrink_memory" in [s.lower() for s in statements]
