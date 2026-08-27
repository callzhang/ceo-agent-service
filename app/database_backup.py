import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4


BACKUP_DIRECTORY_NAME = "backups"
BACKUP_CHECK_INTERVAL_SECONDS = 60 * 60


def create_database_backup(db_path: Path, destination: Path) -> Path:
    """Create and integrity-check a consistent SQLite backup atomically."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with sqlite3.connect(db_path) as source, sqlite3.connect(temporary) as target:
            source.execute("pragma busy_timeout = 30000")
            source.backup(target)
            target.execute("pragma journal_mode = delete")
            integrity = target.execute("pragma integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError(f"database backup integrity check failed: {integrity}")
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def backup_database_if_due(
    db_path: Path,
    *,
    now: datetime | None = None,
) -> Path | None:
    current_date = (now or datetime.now().astimezone()).date()
    backup_dir = db_path.parent / BACKUP_DIRECTORY_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{db_path.stem}-{current_date.isoformat()}.sqlite3"
    if destination.exists():
        prune_database_backups(
            backup_dir,
            today=current_date,
            keep_path=destination,
        )
        return None

    create_database_backup(db_path, destination)

    prune_database_backups(
        backup_dir,
        today=current_date,
        keep_path=destination,
    )
    return destination


def prune_database_backups(
    backup_dir: Path,
    *,
    today: date,
    keep_path: Path | None = None,
) -> list[Path]:
    """Keep exactly one database backup and remove stale SQLite sidecars."""
    del today  # Retained for source compatibility with existing callers.
    database_paths = sorted(backup_dir.glob("*.sqlite3"))
    keep = keep_path
    if keep is None and database_paths:
        keep = max(
            database_paths,
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )

    candidates = [
        *database_paths,
        *backup_dir.glob("*.sqlite3-wal"),
        *backup_dir.glob("*.sqlite3-shm"),
    ]
    deleted: list[Path] = []
    for path in sorted(set(candidates)):
        if keep is not None and path == keep:
            continue
        path.unlink()
        deleted.append(path)
    return deleted
