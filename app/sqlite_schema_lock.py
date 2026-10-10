"""Cross-process lock shared by schema initializers for one SQLite database."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def schema_initialize_lock(database_path: Path) -> Iterator[None]:
    lock_path = Path(database_path).with_name(
        f".{Path(database_path).name}.initialize.lock"
    )
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
