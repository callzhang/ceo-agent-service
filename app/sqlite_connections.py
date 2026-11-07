"""Thread-local idle SQLite connections with independent transaction boundaries."""

from contextlib import contextmanager, suppress
import os
from pathlib import Path
import sqlite3
import threading

# Opening a SQLite connection re-parses the whole schema (hundreds of objects
# with JSON generated columns), which dominated service CPU when every store
# call opened its own.  Each thread keeps one idle connection per database
# file; nested or concurrent use in the same thread opens its own, as before.
_IDLE_CONNECTIONS = threading.local()


def _database_file_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino)


def _take_idle_connection(
    key: tuple[str, int | None, object], path: Path
) -> tuple[sqlite3.Connection, tuple[int, int], int] | None:
    pool = getattr(_IDLE_CONNECTIONS, "pool", None)
    entry = pool.pop(key, None) if pool else None
    if entry is None:
        return None
    connection, identity, _changes = entry
    if identity != _database_file_identity(path):
        with suppress(sqlite3.Error):
            connection.close()
        return None
    return entry


def _close_idle_connections() -> None:
    """Close this thread's idle connections (tests, and callers that must be
    the only connection to a database file)."""
    pool = getattr(_IDLE_CONNECTIONS, "pool", None) or {}
    for connection, _identity, _changes in pool.values():
        with suppress(sqlite3.Error):
            connection.close()
    pool.clear()


def _return_idle_connection(
    key: tuple[str, int | None, object],
    connection: sqlite3.Connection,
    identity: tuple[int, int] | None,
) -> None:
    pool = _IDLE_CONNECTIONS.__dict__.setdefault("pool", {})
    try:
        reusable = (
            identity is not None
            and key not in pool
            and not connection.in_transaction
            and connection.execute("pragma foreign_keys").fetchone()[0] == 1
        )
    except sqlite3.Error:
        reusable = False
    if reusable:
        # What is worth keeping is the parsed schema; the page cache (up to
        # SQLITE_READ_CACHE_SIZE per connection) is not, and an idle connection
        # per thread per database would otherwise hold it for good.
        with suppress(sqlite3.Error):
            connection.execute("pragma shrink_memory")
        pool[key] = (connection, identity, connection.total_changes)
    else:
        with suppress(sqlite3.Error):
            connection.close()



def _open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.execute("pragma foreign_keys=on")
    return connection


@contextmanager
def readonly_connection(path: Path):
    """Reuse parsed schema, while each caller owns a fresh read transaction."""
    key = (str(path), None, _open_readonly)
    idle = _take_idle_connection(key, path)
    if idle is None:
        identity = _database_file_identity(path)
        connection = _open_readonly(path)
    else:
        connection, identity, _changes = idle
    completed = False
    try:
        with connection:
            yield connection
        completed = True
    finally:
        if completed:
            _return_idle_connection(key, connection, identity)
        else:
            with suppress(sqlite3.Error):
                connection.close()
