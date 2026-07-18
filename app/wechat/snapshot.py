"""Restricted, read-only DB snapshots.

Copies a WeChat database plus its -wal/-shm siblings into a private temporary
directory so SQLite is never opened against the live file. Never writes to the
source. The snapshot directory is removed on context exit.
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator


@contextmanager
def readonly_snapshot(source_db: Path, *, temp_root: Path) -> Iterator[Path]:
    temp_root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="wechat-snapshot-", dir=temp_root.parent) as raw:
        root = Path(raw)
        root.chmod(0o700)
        for candidate in (
            source_db,
            source_db.with_name(source_db.name + "-wal"),
            source_db.with_name(source_db.name + "-shm"),
        ):
            if candidate.exists():
                shutil.copy2(candidate, root / candidate.name)
        snapshot = root / source_db.name
        if not snapshot.exists():
            raise FileNotFoundError(source_db)
        yield snapshot


def cleanup_stale_snapshots(temp_parent: Path) -> int:
    """Remove leftover wechat-snapshot-* dirs (e.g. after a crash). Returns count."""
    removed = 0
    if not temp_parent.is_dir():
        return removed
    for path in temp_parent.iterdir():
        if path.is_dir() and path.name.startswith("wechat-snapshot-"):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed
