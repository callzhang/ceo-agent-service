import fcntl
import os
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path


BACKUP_DIRECTORY_NAME = "backups"
BACKUP_CHECK_INTERVAL_SECONDS = 60 * 60
PREDEPLOYMENT_SNAPSHOT_RETENTION_SECONDS = 24 * 60 * 60
BACKUP_STAGE_PREFIX = ".ceo-backup-stage-"
# Stamped into a backup only once it is complete and integrity-checked.
BACKUP_COMPLETE_APPLICATION_ID = 0x43454F42  # "CEOB"


@contextmanager
def _backup_directory_lock(directory: Path):
    """Serialize backups and deployments retaining a recovery copy."""
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


def _remove_abandoned_stages(directory: Path) -> None:
    """Remove only candidates written by this backup workflow."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        for path in directory.glob(f"{BACKUP_STAGE_PREFIX}*.tmp{suffix}"):
            path.unlink(missing_ok=True)


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
    """Publish a consistent copy before removing the previous backup."""
    directory = destination.parent
    with _backup_directory_lock(directory):
        _remove_abandoned_stages(directory)
        descriptor, candidate_name = tempfile.mkstemp(
            prefix=f"{BACKUP_STAGE_PREFIX}{destination.name}.", suffix=".tmp", dir=directory
        )
        os.close(descriptor)
        candidate = Path(candidate_name)
        try:
            with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(candidate)) as target:
                source.execute("pragma busy_timeout = 30000")
                source.backup(target)
                target.execute("pragma journal_mode = delete")
                integrity = target.execute("pragma integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise RuntimeError(f"database backup integrity check failed: {integrity}")
                target.execute(f"pragma application_id = {BACKUP_COMPLETE_APPLICATION_ID}")
                target.commit()
            if not backup_is_complete(candidate):
                raise RuntimeError("database backup completion marker is missing")
            os.chmod(candidate, stat.S_IMODE(db_path.stat().st_mode))
            os.replace(candidate, destination)
            _remove_older_backups(directory, keep=destination)
        except BaseException:
            _remove_sqlite_files(candidate)
            raise
        return destination


def backup_database_if_due(
    db_path: Path,
    *,
    now: datetime | None = None,
) -> Path | None:
    if os.environ.get("CEO_DATABASE_BACKUP_PAUSED", "0") == "1":
        return None
    current = now or datetime.now().astimezone()
    current_date = current.date()
    backup_dir = db_path.parent / BACKUP_DIRECTORY_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{db_path.stem}-{current_date.isoformat()}.sqlite3"
    with _backup_directory_lock(backup_dir):
        _remove_abandoned_stages(backup_dir)
        already_backed_up = backup_is_complete(destination)
        if already_backed_up:
            _remove_older_backups(backup_dir, keep=destination)
    if already_backed_up:
        prune_predeployment_snapshots(db_path, now=current)
        return None

    create_database_backup(db_path, destination)
    # The daily copy is stamped complete only after integrity_check succeeds.
    # Expire the separately named pre-deployment recovery copies only after
    # that verified backup exists.
    prune_predeployment_snapshots(db_path, now=current)
    return destination


def prune_predeployment_snapshots(
    db_path: Path,
    *,
    now: datetime | None = None,
) -> list[Path]:
    """Remove root-level pre-deployment database copies older than 24 hours.

    These named recovery snapshots live beside the active database rather
    than in ``backups/``. Their SQLite sidecars share the same prefix and are
    subject to the same age limit.
    """
    cutoff = (
        (now or datetime.now().astimezone()).timestamp()
        - PREDEPLOYMENT_SNAPSHOT_RETENTION_SECONDS
    )
    deleted: list[Path] = []
    for path in sorted(db_path.parent.glob(f"{db_path.name}.pre-*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_mtime >= cutoff:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted.append(path)
    return deleted


def _remove_older_backups(backup_dir: Path, *, keep: Path | None) -> list[Path]:
    candidates = [
        *backup_dir.glob("*.sqlite3"),
        *backup_dir.glob("*.sqlite3-wal"),
        *backup_dir.glob("*.sqlite3-shm"),
        *backup_dir.glob("*.sqlite3-journal"),
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
