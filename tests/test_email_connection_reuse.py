"""Email connections reuse parsed schema without sharing transaction state."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import gc
import os
import sqlite3
from threading import Barrier

import pytest

from app.email_store import EmailStore
from app.state_definitions import state_projection
from app.state_storage import upsert_state
from app.store import _close_idle_connections
from app.task8_storage import register_task8_functions


@pytest.fixture
def store(tmp_path):
    value = EmailStore(tmp_path / "email.sqlite3")
    _close_idle_connections()
    return value


def _write(db, value):
    db.execute("begin immediate")
    upsert_state(db, "service_state", {"key": "connection-probe", "value": value},
                 {"value": "json_quote(excluded.value)"})


def _read(db):
    row = db.execute("select value from (" + state_projection("service_state")
                     + ") where key='connection-probe'").fetchone()
    return row[0] if row else None


def test_repeated_email_reads_open_one_connection_and_keep_real_schema(store, monkeypatch):
    opened = []
    original = store._open_connection

    def open_connection(**options):
        connection = original(**options)
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_open_connection", open_connection)
    for _ in range(8):
        with store._connect() as db:
            assert db.execute("select count(*) from emails").fetchone()[0] == 0
            assert db.execute("pragma foreign_keys").fetchone()[0] == 1
            assert not db.in_transaction
    assert len(opened) == 1


def test_commits_are_visible_and_failed_context_rolls_back_and_closes(store):
    with store._connect() as first:
        _write(first, "committed")
    with closing(sqlite3.connect(store.path)) as observer:
        assert _read(observer) == "committed"
    with pytest.raises(RuntimeError, match="abort"):
        with store._connect() as failed:
            _write(failed, "rolled back")
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        failed.execute("select 1")
    with store._connect() as recovered:
        assert recovered is not failed
        assert _read(recovered) == "committed"
    with closing(sqlite3.connect(store.path)) as observer:
        _write(observer, "external update")
        observer.commit()
    with store._connect() as fresh:
        assert _read(fresh) == "external update"


def test_nested_and_concurrent_contexts_never_share_an_active_connection(store):
    with store._connect() as outer:
        with store._connect() as inner:
            assert inner is not outer
        assert outer.execute("select 1").fetchone()[0] == 1
    barrier = Barrier(2)

    def read_in_thread():
        try:
            with store._connect() as connection:
                barrier.wait(timeout=5)
                assert connection.execute("pragma foreign_keys").fetchone()[0] == 1
                return connection
        finally:
            _close_idle_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read_in_thread)
        second = pool.submit(read_in_thread)
        assert first.result() is not second.result()


def test_foreign_key_off_context_is_closed_without_poisoning_normal_reads(store):
    with store._connect() as normal:
        pass
    with store._connect(foreign_keys=False) as disabled:
        assert disabled.execute("pragma foreign_keys").fetchone()[0] == 0
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        disabled.execute("select 1")
    with store._connect() as reused:
        assert reused is normal
        assert reused.execute("pragma foreign_keys").fetchone()[0] == 1


def test_replaced_database_discards_the_old_idle_connection(store, tmp_path):
    with closing(sqlite3.connect(store.path)) as setup:
        setup.execute("pragma journal_mode=delete")
    with store._connect() as original:
        _write(original, "old file")
    replacement = tmp_path / "replacement.sqlite3"
    with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(replacement)) as target:
        source.backup(target)
        register_task8_functions(target)
        _write(target, "replacement file")
        target.commit()
    os.replace(replacement, store.path)
    with store._connect() as current:
        assert current is not original
        assert _read(current) == "replacement file"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        original.execute("select 1")


def test_different_connection_openers_do_not_borrow_each_others_idle_state(store, monkeypatch):
    with store._connect() as original_connection:
        pass
    original_open = store._open_connection
    opened = []

    def new_open(**options):
        connection = original_open(**options)
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_open_connection", new_open)
    with store._connect() as custom:
        assert custom is not original_connection
    with store._connect() as repeated:
        assert repeated is custom
    assert len(opened) == 1


@pytest.mark.parametrize("first_active_accounts", [None, ("account",)])
def test_provider_scan_temp_tables_end_with_each_scan_generation(store, first_active_accounts):
    observation = {
        "account_id": "account", "stable_message_identity": "message",
        "provider_folder_id": "inbox", "provider_folder_name": "Inbox",
        "folder_role": "inbox", "important_signals": {
            "provider_important": False, "raw_signal_names": [],
        },
    }
    store.record_current_provider_observations(
        [observation], unavailable_folders=(), authoritative_folders=("account:inbox",),
        active_account_ids=first_active_accounts,
        observed_at="2026-11-01T00:00:00+00:00",
    )
    store.record_current_provider_observations(
        [], unavailable_folders=(), authoritative_folders=("account:inbox",),
        active_account_ids=("account",) if first_active_accounts is None else None,
        observed_at="2026-11-01T00:01:00+00:00",
    )
    with store._connect() as db:
        assert db.execute("select name from temp.sqlite_schema where type='table'").fetchall() == []
        assert tuple(db.execute("select state,observed_at from provider_state "
                                "where record_type='observation'").fetchone()) == (
            "unavailable", "2026-11-01T00:01:00+00:00",
        )


def test_reopening_after_schema_damage_still_rejects_the_database(store):
    with store._connect() as db:
        db.execute("drop index idx_schedules_enabled")
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        EmailStore(store.path)


def test_email_search_function_is_connection_initialization_not_search_state(store):
    with store._connect() as db:
        assert db.execute("select email_casefold(?)", ("Straße",)).fetchone()[0] == "strasse"
    assert store.classification_source_candidates(status=None, preview_query="STRASSE") == ([], 0)
    with store._connect() as reused:
        assert reused is db
        assert reused.execute("select email_casefold(?)", ("Straße",)).fetchone()[0] == "strasse"


@pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="process fd listing requires /dev/fd")
def test_idle_reuse_keeps_file_descriptors_bounded_and_cleanup_releases_them(store):
    gc.collect()
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        cold = len(os.listdir("/dev/fd"))
        assert store.get_account("missing") is None
        warm = len(os.listdir("/dev/fd"))
        assert warm <= cold + 3
        for _ in range(100):
            assert store.get_account("missing") is None
            assert store.list_accounts() == []
        assert len(os.listdir("/dev/fd")) == warm
        _close_idle_connections()
        assert len(os.listdir("/dev/fd")) == cold
    finally:
        if gc_was_enabled:
            gc.enable()


def test_failed_scan_rolls_back_tombstones_and_leaves_no_temp_state(store, monkeypatch):
    import app.email_store as module

    observation = {
        "account_id": "account", "stable_message_identity": "message",
        "provider_folder_id": "inbox", "provider_folder_name": "Inbox",
        "folder_role": "inbox", "important_signals": {
            "provider_important": False, "raw_signal_names": [],
        },
    }
    store.record_current_provider_observations(
        [observation], unavailable_folders=(),
        observed_at="2026-11-01T00:00:00+00:00",
    )
    original = module.upsert_folder_generations

    def fail_generation(*args):
        raise RuntimeError("generation failed")

    monkeypatch.setattr(module, "upsert_folder_generations", fail_generation)
    with pytest.raises(RuntimeError, match="generation failed"):
        store.record_current_provider_observations(
            [], unavailable_folders=(), authoritative_folders=("account:inbox",),
            active_account_ids=("account",), observed_at="2026-11-01T00:01:00+00:00",
        )
    with store._connect() as db:
        assert tuple(db.execute("select state,observed_at from provider_state "
                                "where record_type='observation'").fetchone()) == (
            "available", "2026-11-01T00:00:00+00:00",
        )
        assert db.execute("select name from temp.sqlite_schema where type='table'").fetchall() == []
    monkeypatch.setattr(module, "upsert_folder_generations", original)
    store.record_current_provider_observations(
        [], unavailable_folders=(), authoritative_folders=("account:inbox",),
        observed_at="2026-11-01T00:01:00+00:00",
    )
    with store._connect() as db:
        assert db.execute("select state from provider_state "
                          "where record_type='observation'").fetchone()[0] == "unavailable"
