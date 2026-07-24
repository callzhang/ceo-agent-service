from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4


BACKUP_DIRECTORY_NAME = "backups"
BACKUP_CHECK_INTERVAL_SECONDS = 60 * 60
BACKUP_DIRECTORY_MODE = 0o700
BACKUP_FILE_MODE = 0o600
_DATE_SUFFIX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALIDATED_BACKUPS: dict[str, tuple[int, int, int, int, int]] = {}


def _descriptor_path(descriptor: int) -> Path:
    """Resolve an already-open descriptor without re-following the public path."""
    try:
        if hasattr(fcntl, "F_GETPATH"):
            raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
            value = raw.split(b"\0", 1)[0].decode()
            if value:
                return Path(value)
    except (ImportError, OSError, UnicodeDecodeError, ValueError):
        pass
    proc_path = Path(f"/proc/self/fd/{descriptor}")
    try:
        return Path(os.readlink(proc_path))
    except OSError as exc:  # pragma: no cover - macOS and Linux both resolve above
        raise RuntimeError("cannot resolve secured backup descriptor") from exc


def _require_owner(info: os.stat_result, *, kind: str) -> None:
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise RuntimeError(f"database backup {kind} is not owned by this user")


@contextmanager
def _private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=BACKUP_DIRECTORY_MODE)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not no_follow or not directory_flag:
        raise RuntimeError("safe database backup directory operations are unsupported")
    try:
        descriptor = os.open(path, os.O_RDONLY | directory_flag | no_follow)
    except OSError as exc:
        raise RuntimeError(f"database backup directory is unsafe: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("database backup path is not a directory")
        _require_owner(info, kind="directory")
        os.fchmod(descriptor, BACKUP_DIRECTORY_MODE)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise RuntimeError("cannot lock database backup directory") from exc
        yield descriptor
    finally:
        # Closing the descriptor releases flock even while unwinding an error.
        os.close(descriptor)


def _open_private_regular_file(directory_fd: int, name: str) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise RuntimeError("safe database backup file operations are unsupported")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | no_follow,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError(f"database backup file is unsafe: {name}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"database backup path is not a regular file: {name}")
        _require_owner(info, kind="file")
        os.fchmod(descriptor, BACKUP_FILE_MODE)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _create_private_file(directory_fd: int, name: str) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise RuntimeError("safe database backup file operations are unsupported")
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | no_follow,
        BACKUP_FILE_MODE,
        dir_fd=directory_fd,
    )
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise RuntimeError("database backup temporary path is not a regular file")
    _require_owner(info, kind="temporary file")
    os.fchmod(descriptor, BACKUP_FILE_MODE)
    return descriptor


def _schema_signature(
    database: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    rows = database.execute(
        """
        select type, name, tbl_name, coalesce(sql, '')
        from sqlite_master
        where name not like 'sqlite_%'
        order by type, name
        """
    ).fetchall()
    return tuple(tuple(str(value) for value in row) for row in rows)


def _database_schema_signature(path: Path) -> tuple[tuple[str, str, str, str], ...]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as database:
        return _schema_signature(database)


def _descriptor_sqlite_uri(descriptor: int) -> str:
    for descriptor_root in ("/dev/fd", "/proc/self/fd"):
        if os.path.isdir(descriptor_root):
            return (
                f"file:{descriptor_root}/{descriptor}"
                "?mode=ro&immutable=1"
            )
    raise RuntimeError("descriptor-backed SQLite validation is unsupported")


def _file_fingerprint(descriptor: int) -> tuple[int, int, int, int, int]:
    info = os.fstat(descriptor)
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _require_same_file(
    expected_descriptor: int,
    actual_info: os.stat_result,
    *,
    context: str,
) -> None:
    expected_info = os.fstat(expected_descriptor)
    if not stat.S_ISREG(actual_info.st_mode):
        raise RuntimeError(f"database backup {context} is not a regular file")
    _require_owner(actual_info, kind=context)
    if (actual_info.st_dev, actual_info.st_ino) != (
        expected_info.st_dev,
        expected_info.st_ino,
    ):
        raise RuntimeError(f"database backup {context} changed unexpectedly")


def _require_temporary_entry_unchanged(
    directory_fd: int,
    temporary_name: str,
    temporary_fd: int,
) -> None:
    try:
        reopened_fd = _open_private_regular_file(directory_fd, temporary_name)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "database backup temporary file disappeared before publication"
        ) from exc
    try:
        _require_same_file(
            temporary_fd,
            os.fstat(reopened_fd),
            context="temporary file",
        )
    finally:
        os.close(reopened_fd)


def _entry_matches_descriptor(
    directory_fd: int,
    name: str,
    expected_descriptor: int,
) -> bool:
    try:
        current_fd = _open_private_regular_file(directory_fd, name)
    except (FileNotFoundError, RuntimeError):
        return False
    try:
        try:
            _require_same_file(
                expected_descriptor,
                os.fstat(current_fd),
                context="directory entry",
            )
        except RuntimeError:
            return False
        return True
    finally:
        os.close(current_fd)


def _unlink_entry_if_same(
    directory_fd: int,
    name: str,
    expected_descriptor: int,
) -> bool:
    try:
        current_fd = _open_private_regular_file(directory_fd, name)
    except (FileNotFoundError, RuntimeError):
        return False
    try:
        try:
            _require_same_file(
                expected_descriptor,
                os.fstat(current_fd),
                context="unexpected published file",
            )
        except RuntimeError:
            return False
    finally:
        os.close(current_fd)
    os.unlink(name, dir_fd=directory_fd)
    os.fsync(directory_fd)
    return True


def _descriptor_database_is_valid(
    descriptor: int,
    *,
    expected_schema: tuple[tuple[str, str, str, str], ...],
    integrity_pragma: str,
) -> bool:
    fingerprint = _file_fingerprint(descriptor)
    try:
        with sqlite3.connect(
            _descriptor_sqlite_uri(descriptor),
            uri=True,
        ) as database:
            if _schema_signature(database) != expected_schema:
                return False
            result = database.execute(f"pragma {integrity_pragma}").fetchone()
    except (OSError, RuntimeError, sqlite3.DatabaseError):
        return False
    return (
        result is not None
        and result[0] == "ok"
        and _file_fingerprint(descriptor) == fingerprint
    )


def _existing_backup_is_valid(source: Path, destination_fd: int) -> bool:
    destination = _descriptor_path(destination_fd)
    fingerprint = _file_fingerprint(destination_fd)
    cache_key = str(destination)
    try:
        source_schema = _database_schema_signature(source)
    except (OSError, RuntimeError, sqlite3.DatabaseError):
        return False
    if not source_schema:
        return False
    if _VALIDATED_BACKUPS.get(cache_key) != fingerprint:
        if not _descriptor_database_is_valid(
            destination_fd,
            expected_schema=source_schema,
            integrity_pragma="quick_check(1)",
        ):
            return False
        fingerprint = _file_fingerprint(destination_fd)
        if len(_VALIDATED_BACKUPS) >= 128:
            _VALIDATED_BACKUPS.clear()
        _VALIDATED_BACKUPS[cache_key] = fingerprint
    return True


def _mark_validated(descriptor: int) -> None:
    path = _descriptor_path(descriptor)
    if len(_VALIDATED_BACKUPS) >= 128:
        _VALIDATED_BACKUPS.clear()
    _VALIDATED_BACKUPS[str(path)] = _file_fingerprint(descriptor)


def backup_database_if_due(
    db_path: Path,
    *,
    now: datetime | None = None,
) -> Path | None:
    current_date = (now or datetime.now().astimezone()).date()
    backup_dir = db_path.parent / BACKUP_DIRECTORY_NAME
    destination_name = f"{db_path.stem}-{current_date.isoformat()}.sqlite3"
    temporary_name = f".{destination_name}.{uuid4().hex}.tmp"

    with _private_directory(backup_dir) as directory_fd:
        try:
            destination_fd = _open_private_regular_file(
                directory_fd,
                destination_name,
            )
        except FileNotFoundError:
            destination_fd = None
        if destination_fd is not None:
            try:
                if _existing_backup_is_valid(
                    db_path,
                    destination_fd,
                ) and _entry_matches_descriptor(
                    directory_fd,
                    destination_name,
                    destination_fd,
                ):
                    _prune_database_backups(
                        directory_fd,
                        _descriptor_path(directory_fd),
                        today=current_date,
                        database_stem=db_path.stem,
                    )
                    return None
            finally:
                os.close(destination_fd)

        temporary_fd = _create_private_file(directory_fd, temporary_name)
        try:
            temporary_path = _descriptor_path(temporary_fd)
            with sqlite3.connect(db_path) as source, sqlite3.connect(
                temporary_path
            ) as target:
                _require_same_file(
                    temporary_fd,
                    os.stat(temporary_path),
                    context="temporary SQLite target",
                )
                source.execute("pragma busy_timeout = 30000")
                source.backup(target)
                target.execute("pragma journal_mode = delete")
                integrity = target.execute("pragma integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise RuntimeError(
                        f"database backup integrity check failed: {integrity}"
                    )
                snapshot_schema = _schema_signature(target)
            if not _descriptor_database_is_valid(
                temporary_fd,
                expected_schema=snapshot_schema,
                integrity_pragma="integrity_check",
            ):
                raise RuntimeError(
                    "database backup temporary file failed descriptor-bound "
                    "integrity validation"
                )
            _require_temporary_entry_unchanged(
                directory_fd,
                temporary_name,
                temporary_fd,
            )
            os.fchmod(temporary_fd, BACKUP_FILE_MODE)
            os.fsync(temporary_fd)
            os.replace(
                temporary_name,
                destination_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            try:
                published_fd = _open_private_regular_file(
                    directory_fd,
                    destination_name,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "database backup disappeared during publication"
                ) from exc
            try:
                try:
                    _require_same_file(
                        temporary_fd,
                        os.fstat(published_fd),
                        context="published file",
                    )
                except RuntimeError:
                    _unlink_entry_if_same(
                        directory_fd,
                        destination_name,
                        published_fd,
                    )
                    raise
            finally:
                os.close(published_fd)
            os.fsync(directory_fd)
            _mark_validated(temporary_fd)
        finally:
            os.close(temporary_fd)
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

        _prune_database_backups(
            directory_fd,
            _descriptor_path(directory_fd),
            today=current_date,
            database_stem=db_path.stem,
        )
        destination = _descriptor_path(directory_fd) / destination_name
    return destination


def _prune_database_backups(
    directory_fd: int,
    backup_dir: Path,
    *,
    today: date,
    database_stem: str,
) -> list[Path]:
    dated_paths: list[tuple[int, str]] = []
    prefix = f"{database_stem}-"
    extension = ".sqlite3"
    for name in os.listdir(directory_fd):
        if not name.startswith(prefix) or not name.endswith(extension):
            continue
        date_text = name[len(prefix) : -len(extension)]
        if not _DATE_SUFFIX.fullmatch(date_text):
            continue
        try:
            backup_date = date.fromisoformat(date_text)
        except ValueError:
            continue
        descriptor = _open_private_regular_file(directory_fd, name)
        os.close(descriptor)
        dated_paths.append(((today - backup_date).days, name))

    keep: set[str] = {
        name for age, name in dated_paths if age < 0 or 0 <= age <= 3
    }
    for lower, upper in ((4, 7), (8, 14)):
        candidates = [item for item in dated_paths if lower <= item[0] <= upper]
        if candidates:
            keep.add(max(candidates, key=lambda item: item[0])[1])

    deleted: list[Path] = []
    for _, name in dated_paths:
        if name in keep:
            continue
        os.unlink(name, dir_fd=directory_fd)
        path = backup_dir / name
        _VALIDATED_BACKUPS.pop(str(path), None)
        deleted.append(path)
    return deleted


def prune_database_backups(
    backup_dir: Path,
    *,
    today: date,
    database_stem: str = "auto-reply",
) -> list[Path]:
    with _private_directory(backup_dir) as directory_fd:
        return _prune_database_backups(
            directory_fd,
            backup_dir,
            today=today,
            database_stem=database_stem,
        )
