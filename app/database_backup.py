import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4


BACKUP_DIRECTORY_NAME = "backups"
BACKUP_CHECK_INTERVAL_SECONDS = 60 * 60


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
        prune_database_backups(backup_dir, today=current_date)
        return None

    temporary = backup_dir / f".{destination.name}.{uuid4().hex}.tmp"
    try:
        with sqlite3.connect(db_path) as source, sqlite3.connect(temporary) as target:
            source.execute("pragma busy_timeout = 30000")
            source.backup(target)
            target.execute("pragma journal_mode = delete")
            integrity = target.execute("pragma integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError(f"database backup integrity check failed: {integrity}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    prune_database_backups(backup_dir, today=current_date)
    return destination


def prune_database_backups(backup_dir: Path, *, today: date) -> list[Path]:
    dated_paths: list[tuple[int, Path]] = []
    for path in backup_dir.glob("auto-reply-*.sqlite3"):
        backup_date = date.fromisoformat(path.stem.removeprefix("auto-reply-"))
        dated_paths.append(((today - backup_date).days, path))

    keep: set[Path] = {
        path for age, path in dated_paths if age < 0 or 0 <= age <= 3
    }
    for lower, upper in ((4, 7), (8, 14)):
        candidates = [item for item in dated_paths if lower <= item[0] <= upper]
        if candidates:
            keep.add(max(candidates, key=lambda item: item[0])[1])

    deleted: list[Path] = []
    for _, path in dated_paths:
        if path in keep:
            continue
        path.unlink()
        deleted.append(path)
    return deleted
