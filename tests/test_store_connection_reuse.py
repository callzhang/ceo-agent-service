import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.state_storage import state_json_field, upsert_state
from app.store import AutoReplyStore
from app.workbench.executor import WorkbenchExecutor
from app.workbench.models import TurnStatus
from app.workbench.runtime import RuntimeRegistry
from tests.test_workbench_executor import FakeRuntime, _queued, _store


def test_workbench_turn_reuses_one_connection_without_changing_completion(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _, queued = _queued(store)
    executor = WorkbenchExecutor(store, RuntimeRegistry([FakeRuntime()]), workspace=tmp_path)
    turn = store.claim_next_turn(owner=executor.owner)
    assert turn.id == queued.id
    executor._reserved_turns = 1
    executor._reserved_turn_ids.add(turn.id)
    connections = []
    original_open = store._open_connection

    def opened():
        connection = original_open()
        connections.append(connection)
        return connection

    monkeypatch.setattr(store, "_open_connection", opened)
    try:
        executor._execute_turn_reserved(turn)
        assert len(connections) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[0].execute("select 1")
        assert store.get_turn(turn.id).status is TurnStatus.COMPLETED
        assert executor._reserved_turns == 0
    finally:
        executor.close()


def test_reuse_keeps_each_write_commit_and_failure_rollback(tmp_path):
    store = AutoReplyStore(tmp_path / "transactions.sqlite3")
    observer = AutoReplyStore(store.path)
    with store.reuse_connection():
        store.set_service_state("connection-test", "committed")
        assert observer.get_service_state("connection-test") == "committed"
        with pytest.raises(RuntimeError, match="abort operation"):
            with store._connect() as db:
                db.execute("begin immediate")
                upsert_state(db, "service_state", {"key": "connection-test", "value": "rolled back"},
                             {"value": state_json_field("value")})
                raise RuntimeError("abort operation")
        assert store.get_service_state("connection-test") == "committed"
        store.set_service_state("connection-test", "next commit")
        assert observer.get_service_state("connection-test") == "next commit"


def test_reuse_does_not_share_connections_or_read_snapshots_across_threads(tmp_path):
    store = AutoReplyStore(tmp_path / "threads.sqlite3")

    def other_thread():
        store.set_service_state("other-thread", "visible")
        with store._connect() as db:
            return db

    with store.reuse_connection():
        with store._connect() as main_connection:
            pass
        with ThreadPoolExecutor(max_workers=1) as pool:
            other_connection = pool.submit(other_thread).result()
        assert other_connection is not main_connection
        assert store.get_service_state("other-thread") == "visible"
        with store.read_snapshot():
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                store.set_service_state("snapshot-write", "forbidden")
        store.set_service_state("after-snapshot", "committed")
    assert store.get_service_state("after-snapshot") == "committed"
