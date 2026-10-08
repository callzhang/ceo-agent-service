from contextvars import ContextVar
from pathlib import Path

import pytest

import app.email_store as email_module
import app.store as store_module


@pytest.mark.parametrize("kind", ["reply", "email"])
def test_slow_context_separates_body_transaction_finish_and_close(
    kind, monkeypatch, capsys, caplog
):
    clock = [0.0]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            clock[0] += 3.0

        def close(self):
            clock[0] += 4.0

    if kind == "reply":
        store = store_module.AutoReplyStore.__new__(store_module.AutoReplyStore)
        store._read_snapshot_connection = ContextVar("test_snapshot", default=None)
        monkeypatch.setattr(store_module.time, "monotonic", lambda: clock[0])
    else:
        store = email_module.EmailStore.__new__(email_module.EmailStore)
        monkeypatch.setattr(email_module, "monotonic", lambda: clock[0])
    store.path = Path("unused.sqlite3")
    monkeypatch.setattr(store, "_open_connection", lambda: Connection())
    with store._connect():
        clock[0] += 2.0
    output = capsys.readouterr().err + caplog.text
    assert "body_seconds=2.000" in output
    assert "finish_seconds=3.000" in output
    assert "close_seconds=4.000" in output


@pytest.mark.parametrize("kind", ["reply", "email"])
def test_context_phase_diagnostics_preserve_body_failure(kind, monkeypatch):
    class Connection:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            self.closed = True

    connection = Connection()
    if kind == "reply":
        store = store_module.AutoReplyStore.__new__(store_module.AutoReplyStore)
        store._read_snapshot_connection = ContextVar("test_snapshot", default=None)
    else:
        store = email_module.EmailStore.__new__(email_module.EmailStore)
    store.path = Path("unused.sqlite3")
    monkeypatch.setattr(store, "_open_connection", lambda: connection)
    with pytest.raises(ValueError, match="original failure"):
        with store._connect():
            raise ValueError("original failure")
    assert connection.closed
