"""Reused read snapshots retain connection ownership while reporting slow reads."""

import sqlite3
from contextvars import ContextVar

import pytest

from app import store as store_module


class TrackedConnection(sqlite3.Connection):
    commits = 0
    rollbacks = 0
    closed = False

    def commit(self):
        self.commits += 1
        return super().commit()

    def rollback(self):
        self.rollbacks += 1
        return super().rollback()

    def close(self):
        self.closed = True
        return super().close()


@pytest.fixture
def snapshot_store(tmp_path, monkeypatch):
    store = store_module.AutoReplyStore.__new__(store_module.AutoReplyStore)
    store.path = tmp_path / "unused.sqlite3"
    store._read_snapshot_connection = ContextVar("snapshot_timing_test", default=None)
    connection = sqlite3.connect(":memory:", factory=TrackedConnection)
    monkeypatch.setattr(store, "_open_connection", lambda: connection)
    clock = [0.0]
    monkeypatch.setattr(store_module.time, "monotonic", lambda: clock[0])
    try:
        yield store, connection, clock
    finally:
        if not connection.closed:
            connection.close()


@pytest.mark.parametrize("elapsed,logged", [(0.999, False), (1.0, True)])
def test_snapshot_connect_uses_existing_slow_context_threshold(
    snapshot_store, capsys, elapsed, logged,
):
    store, connection, clock = snapshot_store
    with store.read_snapshot():
        with store._connect() as db:
            assert db is connection
            clock[0] += elapsed
        assert db.execute("select 1").fetchone()[0] == 1
        assert db.in_transaction
        assert not db.closed
        assert db.commits == db.rollbacks == 0

    output = capsys.readouterr().err
    assert bool(output) is logged
    if logged:
        assert "slow sqlite context elapsed_seconds=1.000 body_seconds=1.000" in output
        assert "finish_seconds=0.000 close_seconds=0.000" in output
        assert "callers=" in output
        assert "test_snapshot_connect_uses_existing_slow_context_threshold" in output
    assert connection.closed
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_nested_snapshot_connect_logs_each_body_without_ending_snapshot(
    snapshot_store, capsys,
):
    store, connection, clock = snapshot_store
    with store.read_snapshot():
        with store._connect() as outer:
            clock[0] += 1.0
            with store.read_snapshot():
                with store._connect() as inner:
                    assert inner is outer is connection
                    clock[0] += 2.0
            assert outer.execute("select 1").fetchone()[0] == 1
            assert outer.in_transaction
            assert not outer.closed
            assert outer.commits == outer.rollbacks == 0

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert "elapsed_seconds=2.000 body_seconds=2.000" in lines[0]
    assert "elapsed_seconds=3.000 body_seconds=3.000" in lines[1]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed


@pytest.mark.parametrize("error", [ValueError("body failed"), sqlite3.OperationalError("read failed")])
def test_snapshot_timing_preserves_exception_and_connection_ownership(
    snapshot_store, capsys, error,
):
    store, connection, clock = snapshot_store
    with store.read_snapshot():
        with pytest.raises(type(error)) as caught:
            with store._connect():
                clock[0] += 2.0
                raise error
        assert caught.value is error
        assert connection.execute("select 1").fetchone()[0] == 1
        assert connection.in_transaction
        assert connection.commits == connection.rollbacks == 0
        assert not connection.closed

    assert "slow sqlite context elapsed_seconds=2.000 body_seconds=2.000" in capsys.readouterr().err
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed
