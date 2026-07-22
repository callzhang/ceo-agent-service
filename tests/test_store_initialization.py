import multiprocessing
import sqlite3
import stat
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from app import store as store_module
from app.store import AutoReplyStore


def _initialize_store_process(
    path: str,
    started: Connection,
    result: Connection,
) -> None:
    started.send("started")
    started.close()
    try:
        AutoReplyStore(Path(path))
    except Exception as exc:
        result.send(("error", type(exc).__name__, str(exc)))
    else:
        result.send(("ok",))
    finally:
        result.close()


def _copy_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_db, sqlite3.connect(
        destination
    ) as destination_db:
        source_db.backup(destination_db)


def test_concurrent_processes_serialize_legacy_schema_upgrade(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.sqlite3"
    seed = AutoReplyStore(seed_path)
    assert seed.enqueue_reply_task(
        conversation_id="cid-legacy",
        conversation_title="Legacy",
        single_chat=False,
        trigger_message_id="msg-legacy",
        trigger_create_time="2026-07-22 10:00:00",
        trigger_sender="Derek",
        trigger_text="Upgrade this task",
    )

    database_path = tmp_path / "legacy.sqlite3"
    _copy_database(seed_path, database_path)
    missing_columns = (
        ("reply_tasks", "lease_token"),
        ("feishu_events", "resource_truncated"),
        ("feishu_deliveries", "remote_failures"),
        ("feishu_message_actions", "remote_failures"),
    )
    with sqlite3.connect(database_path) as legacy:
        for table, column in missing_columns:
            legacy.execute(f"alter table {table} drop column {column}")
        for table, column in missing_columns:
            assert column not in {
                row[1]
                for row in legacy.execute(f"pragma table_info({table})").fetchall()
            }

    context = multiprocessing.get_context("spawn")
    processes: list[multiprocessing.Process] = []
    started_receivers = []
    result_receivers = []
    child_connections = []
    try:
        # Hold the production lock while both spawned processes reach store
        # construction.  Neither may finish until the lock is released; after
        # release they must upgrade the same legacy schema one at a time.
        with store_module._store_initialization_lock(database_path):
            for _ in range(2):
                started_receiver, started_sender = context.Pipe(duplex=False)
                result_receiver, result_sender = context.Pipe(duplex=False)
                process = context.Process(
                    target=_initialize_store_process,
                    args=(str(database_path), started_sender, result_sender),
                )
                process.start()
                started_sender.close()
                result_sender.close()
                processes.append(process)
                started_receivers.append(started_receiver)
                result_receivers.append(result_receiver)
                child_connections.extend((started_receiver, result_receiver))

            for receiver in started_receivers:
                assert receiver.poll(20)
                assert receiver.recv() == "started"
            assert all(not receiver.poll(0.2) for receiver in result_receivers)

        results = []
        for receiver in result_receivers:
            assert receiver.poll(40)
            results.append(receiver.recv())
        assert results == [("ok",), ("ok",)]

        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for connection in child_connections:
            connection.close()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)

    with sqlite3.connect(database_path) as migrated:
        assert migrated.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert migrated.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert migrated.execute("pragma foreign_key_check").fetchall() == []
        for table, column in missing_columns:
            assert column in {
                row[1]
                for row in migrated.execute(f"pragma table_info({table})").fetchall()
            }
        assert migrated.execute(
            "select lease_token from reply_tasks where trigger_message_id=?",
            ("msg-legacy",),
        ).fetchone()[0] == ""

    lock_files = list(tmp_path.glob(".ceo-agent-schema-*.lock"))
    assert lock_files
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in lock_files)


def test_initialization_lock_propagates_non_duplicate_schema_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_initialization(_store: AutoReplyStore) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        AutoReplyStore,
        "_initialize_locked",
        fail_initialization,
    )

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        AutoReplyStore(tmp_path / "broken.sqlite3")
