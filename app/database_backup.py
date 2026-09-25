import fcntl
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path


BACKUP_DIRECTORY_NAME = "backups"
BACKUP_CHECK_INTERVAL_SECONDS = 60 * 60
# Stamped into a backup only once it is complete and integrity-checked, so a
# backup cut short by a restart is recognisable without a temporary file.
BACKUP_COMPLETE_APPLICATION_ID = 0x43454F42  # "CEOB"


@contextmanager
def _backup_directory_lock(directory: Path):
    """One backup at a time per folder; locks the folder itself, adds no file."""
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _remove_sqlite_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def backup_is_complete(path: Path) -> bool:
    """Whether ``path`` is a finished backup rather than one cut short."""
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            return db.execute("pragma application_id").fetchone()[0] == BACKUP_COMPLETE_APPLICATION_ID
    except sqlite3.Error:
        return False


def create_database_backup(db_path: Path, destination: Path) -> Path:
    """Replace every earlier backup with one consistent, integrity-checked copy.

    Derek, 2026-09-25: no temporary files, and the previous backups go before
    the new one is written. Serial backups that were killed by restarts left a
    2.9 GB half-written temp file each and filled the disk. Now the copy is
    written straight to its final name, so at most one file exists, and it is
    removed again if the copy fails. A copy cut short by a killed process is
    not stamped complete and is deleted by the next backup.
    """
    directory = destination.parent
    with _backup_directory_lock(directory):
        _remove_older_backups(directory, keep=None)
        try:
            with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(destination)) as target:
                source.execute("pragma busy_timeout = 30000")
                source.backup(target)
                target.execute("pragma journal_mode = delete")
                integrity = target.execute("pragma integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise RuntimeError(f"database backup integrity check failed: {integrity}")
                target.execute(f"pragma application_id = {BACKUP_COMPLETE_APPLICATION_ID}")
                target.commit()
        except BaseException:
            _remove_sqlite_files(destination)
            raise
        return destination


def backup_database_if_due(
    db_path: Path,
    *,
    now: datetime | None = None,
) -> Path | None:
    current_date = (now or datetime.now().astimezone()).date()
    backup_dir = db_path.parent / BACKUP_DIRECTORY_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{db_path.stem}-{current_date.isoformat()}.sqlite3"
    if backup_is_complete(destination):
        prune_database_backups(
            backup_dir,
            today=current_date,
            keep_path=destination,
        )
        return None

    create_database_backup(db_path, destination)
    return destination


def _remove_older_backups(backup_dir: Path, *, keep: Path | None) -> list[Path]:
    candidates = [
        *backup_dir.glob("*.sqlite3"),
        *backup_dir.glob("*.sqlite3-wal"),
        *backup_dir.glob("*.sqlite3-shm"),
        *backup_dir.glob("*.sqlite3-journal"),
        # Leftovers of the earlier scheme, which wrote to a hidden temp name.
        *backup_dir.glob(".*.tmp"),
        *backup_dir.glob(".*.tmp-journal"),
    ]
    deleted: list[Path] = []
    for path in sorted(set(candidates)):
        if keep is not None and path == keep:
            continue
        path.unlink(missing_ok=True)
        deleted.append(path)
    return deleted


def prune_database_backups(
    backup_dir: Path,
    *,
    today: date,
    keep_path: Path | None = None,
) -> list[Path]:
    """Keep exactly one database backup and remove stale SQLite sidecars."""
    del today  # Retained for source compatibility with existing callers.
    with _backup_directory_lock(backup_dir):
        keep = keep_path
        database_paths = sorted(backup_dir.glob("*.sqlite3"))
        if keep is None and database_paths:
            keep = max(
                database_paths,
                key=lambda path: (path.stat().st_mtime_ns, path.name),
            )
        return _remove_older_backups(backup_dir, keep=keep)
