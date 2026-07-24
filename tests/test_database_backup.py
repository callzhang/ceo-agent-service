import fcntl
import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import cli
from app import database_backup


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("pragma journal_mode = wal")
        db.execute("create table messages (id integer primary key, body text not null)")
        db.execute("insert into messages (body) values ('durable state')")


def test_daily_backup_is_consistent_and_runs_only_once_per_day(tmp_path: Path):
    assert hasattr(cli, "backup_database_if_due")
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)

    backup_path = cli.backup_database_if_due(db_path, now=now)
    duplicate = cli.backup_database_if_due(
        db_path,
        now=now.replace(hour=20),
    )

    assert backup_path == tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"
    assert duplicate is None
    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("pragma journal_mode").fetchone()[0] == "delete"
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert backup.execute("select body from messages").fetchone()[0] == "durable state"


def test_concurrent_backups_are_serialized_and_keep_valid_winner(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    destination = backup_dir / "auto-reply-2026-07-23.sqlite3"

    directory_fd = os.open(backup_dir, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(directory_fd, fcntl.LOCK_EX)
    original_flock = database_backup.fcntl.flock
    lock_attempts = 0
    attempts_changed = threading.Condition()

    def track_lock_attempt(descriptor: int, operation: int) -> None:
        nonlocal lock_attempts
        if operation == fcntl.LOCK_EX:
            with attempts_changed:
                lock_attempts += 1
                attempts_changed.notify_all()
        original_flock(descriptor, operation)

    monkeypatch.setattr(database_backup.fcntl, "flock", track_lock_attempt)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            calls = [
                executor.submit(cli.backup_database_if_due, db_path, now=now)
                for _ in range(2)
            ]
            try:
                with attempts_changed:
                    assert attempts_changed.wait_for(
                        lambda: lock_attempts == 2,
                        timeout=5,
                    )
                assert all(not call.done() for call in calls)
            finally:
                fcntl.flock(directory_fd, fcntl.LOCK_UN)
            results = [call.result(timeout=10) for call in calls]
    finally:
        os.close(directory_fd)

    assert results.count(destination) == 1
    assert results.count(None) == 1
    assert destination.exists()
    assert not list(backup_dir.glob(".*.tmp"))
    with sqlite3.connect(destination) as backup:
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert backup.execute("select body from messages").fetchone()[0] == (
            "durable state"
        )


def test_backup_retention_keeps_daily_three_day_window_and_7_14_day_points(
    tmp_path: Path,
):
    assert hasattr(cli, "prune_database_backups")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    today = date(2026, 7, 23)
    for age in range(16):
        backup_date = today - timedelta(days=age)
        (backup_dir / f"auto-reply-{backup_date.isoformat()}.sqlite3").touch()

    cli.prune_database_backups(backup_dir, today=today)

    remaining_ages = sorted(
        (today - date.fromisoformat(path.stem.removeprefix("auto-reply-"))).days
        for path in backup_dir.glob("auto-reply-*.sqlite3")
    )
    assert remaining_ages == [0, 1, 2, 3, 7, 14]
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in backup_dir.glob("auto-reply-*.sqlite3")
    )


def test_backup_repairs_existing_permissions_even_when_not_due(tmp_path: Path):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    backup_path = cli.backup_database_if_due(db_path, now=now)
    backup_dir = tmp_path / "backups"
    backup_dir.chmod(0o777)
    backup_path.chmod(0o666)

    assert cli.backup_database_if_due(db_path, now=now.replace(hour=20)) is None

    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600


def test_backup_replaces_invalid_existing_daily_file(tmp_path: Path):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    destination = backup_dir / "auto-reply-2026-07-23.sqlite3"
    destination.touch()

    rebuilt = cli.backup_database_if_due(db_path, now=now)

    assert rebuilt == destination
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with sqlite3.connect(destination) as backup:
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert backup.execute("select body from messages").fetchone()[0] == "durable state"


def test_backup_rejects_symlink_directory(tmp_path: Path):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    target = tmp_path / "attacker-controlled"
    target.mkdir()
    (tmp_path / "backups").symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeError, match="directory is unsafe"):
        cli.backup_database_if_due(
            db_path,
            now=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
        )


def test_backup_directory_swap_cannot_redirect_snapshot(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    moved_dir = tmp_path / "secured-backups"
    attacker_dir = tmp_path / "attacker-controlled"
    original_create = database_backup._create_private_file

    def swap_public_path(directory_fd: int, name: str) -> int:
        backup_dir.rename(moved_dir)
        attacker_dir.mkdir()
        backup_dir.symlink_to(attacker_dir, target_is_directory=True)
        return original_create(directory_fd, name)

    monkeypatch.setattr(
        database_backup,
        "_create_private_file",
        swap_public_path,
    )

    backup_path = cli.backup_database_if_due(
        db_path,
        now=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
    )

    assert backup_path == moved_dir / "auto-reply-2026-07-23.sqlite3"
    assert backup_path.exists()
    assert list(attacker_dir.iterdir()) == []


def test_backup_rejects_directory_swap_after_temporary_path_resolution(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    moved_dir = tmp_path / "secured-backups"
    attacker_dir = tmp_path / "attacker-controlled"
    original_descriptor_path = database_backup._descriptor_path
    swapped = False

    def resolve_then_swap(descriptor: int) -> Path:
        nonlocal swapped
        resolved = original_descriptor_path(descriptor)
        if not swapped and resolved.name.endswith(".tmp"):
            backup_dir.rename(moved_dir)
            attacker_dir.mkdir()
            backup_dir.symlink_to(attacker_dir, target_is_directory=True)
            swapped = True
        return resolved

    monkeypatch.setattr(
        database_backup,
        "_descriptor_path",
        resolve_then_swap,
    )

    with pytest.raises(
        RuntimeError,
        match="temporary SQLite target changed unexpectedly",
    ):
        cli.backup_database_if_due(
            db_path,
            now=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
        )

    assert swapped is True
    assert not (moved_dir / "auto-reply-2026-07-23.sqlite3").exists()
    assert not (attacker_dir / "auto-reply-2026-07-23.sqlite3").exists()


def test_backup_rejects_restored_directory_swap_that_hides_sqlite_target(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    moved_dir = tmp_path / "secured-backups"
    attacker_dir = tmp_path / "attacker-controlled"
    original_descriptor_path = database_backup._descriptor_path
    original_stat = database_backup.os.stat
    temporary_path: Path | None = None
    swapped = False

    def resolve_then_swap(descriptor: int) -> Path:
        nonlocal swapped, temporary_path
        resolved = original_descriptor_path(descriptor)
        if not swapped and resolved.name.endswith(".tmp"):
            temporary_path = resolved
            backup_dir.rename(moved_dir)
            attacker_dir.mkdir()
            backup_dir.symlink_to(attacker_dir, target_is_directory=True)
            swapped = True
        return resolved

    def restore_before_path_identity_check(path, *args, **kwargs):
        if swapped and temporary_path is not None and Path(path) == temporary_path:
            backup_dir.unlink()
            moved_dir.rename(backup_dir)
            temporary_path.write_bytes(b"SQLite format 3\0")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        database_backup,
        "_descriptor_path",
        resolve_then_swap,
    )
    monkeypatch.setattr(
        database_backup.os,
        "stat",
        restore_before_path_identity_check,
    )

    with pytest.raises(
        RuntimeError,
        match="failed descriptor-bound integrity validation",
    ):
        cli.backup_database_if_due(
            db_path,
            now=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
        )

    assert swapped is True
    assert not (backup_dir / "auto-reply-2026-07-23.sqlite3").exists()
    assert not (attacker_dir / "auto-reply-2026-07-23.sqlite3").exists()


def test_backup_rejects_temporary_entry_swap_before_publish(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    original_check = database_backup._require_temporary_entry_unchanged

    def swap_then_check(
        directory_fd: int,
        temporary_name: str,
        temporary_fd: int,
    ) -> None:
        database_backup.os.unlink(temporary_name, dir_fd=directory_fd)
        replacement_fd = database_backup._create_private_file(
            directory_fd,
            temporary_name,
        )
        database_backup.os.close(replacement_fd)
        original_check(directory_fd, temporary_name, temporary_fd)

    monkeypatch.setattr(
        database_backup,
        "_require_temporary_entry_unchanged",
        swap_then_check,
    )

    with pytest.raises(
        RuntimeError,
        match="temporary file changed unexpectedly",
    ):
        cli.backup_database_if_due(
            db_path,
            now=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
        )

    assert not (backup_dir / "auto-reply-2026-07-23.sqlite3").exists()
    assert list(backup_dir.iterdir()) == []


def test_backup_rejects_temporary_entry_swap_after_prepublication_check(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    displaced_name = ".verified-snapshot.sqlite3"
    original_check = database_backup._require_temporary_entry_unchanged

    def check_then_swap(
        directory_fd: int,
        temporary_name: str,
        temporary_fd: int,
    ) -> None:
        original_check(directory_fd, temporary_name, temporary_fd)
        database_backup.os.rename(
            temporary_name,
            displaced_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        replacement_fd = database_backup._create_private_file(
            directory_fd,
            temporary_name,
        )
        database_backup.os.write(replacement_fd, b"attacker replacement")
        database_backup.os.close(replacement_fd)

    monkeypatch.setattr(
        database_backup,
        "_require_temporary_entry_unchanged",
        check_then_swap,
    )

    with pytest.raises(
        RuntimeError,
        match="published file changed unexpectedly",
    ):
        cli.backup_database_if_due(
            db_path,
            now=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
        )

    assert not (backup_dir / "auto-reply-2026-07-23.sqlite3").exists()
    with sqlite3.connect(backup_dir / displaced_name) as displaced:
        assert displaced.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert displaced.execute("select body from messages").fetchone()[0] == (
            "durable state"
        )


def test_backup_fsyncs_directory_after_atomic_publish(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    events: list[str] = []
    original_fsync = database_backup.os.fsync
    original_replace = database_backup.os.replace

    def record_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(database_backup.os.fstat(descriptor).st_mode):
            events.append("fsync-directory")
        else:
            events.append("fsync-file")
        original_fsync(descriptor)

    def record_replace(*args, **kwargs) -> None:
        events.append("replace")
        original_replace(*args, **kwargs)

    monkeypatch.setattr(database_backup.os, "fsync", record_fsync)
    monkeypatch.setattr(database_backup.os, "replace", record_replace)

    cli.backup_database_if_due(
        db_path,
        now=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
    )

    assert events == ["fsync-file", "replace", "fsync-directory"]


def test_existing_backup_validation_is_bound_to_opened_inode(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    destination = backup_dir / "auto-reply-2026-07-23.sqlite3"
    valid_decoy = backup_dir / "valid-decoy.sqlite3"
    corrupt_saved = backup_dir / "corrupt-saved.sqlite3"
    destination.write_bytes(b"corrupt-original")
    with sqlite3.connect(db_path) as source, sqlite3.connect(valid_decoy) as target:
        source.backup(target)
    original_descriptor_path = database_backup._descriptor_path
    swapped = False

    def swap_after_destination_open(descriptor: int) -> Path:
        nonlocal swapped
        resolved = original_descriptor_path(descriptor)
        if not swapped and resolved == destination:
            destination.rename(corrupt_saved)
            valid_decoy.rename(destination)
            swapped = True
        return resolved

    monkeypatch.setattr(
        database_backup,
        "_descriptor_path",
        swap_after_destination_open,
    )

    rebuilt = cli.backup_database_if_due(db_path, now=now)

    assert swapped is True
    assert rebuilt == destination
    with sqlite3.connect(destination) as backup:
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert backup.execute("select body from messages").fetchone()[0] == (
            "durable state"
        )
    assert corrupt_saved.read_bytes() == b"corrupt-original"


def test_cached_existing_backup_entry_swap_is_rebuilt(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    destination = cli.backup_database_if_due(db_path, now=now)
    saved = destination.parent / "displaced-valid.sqlite3"
    original_validation = database_backup._existing_backup_is_valid
    swapped = False

    def validate_then_swap(source: Path, destination_fd: int) -> bool:
        nonlocal swapped
        valid = original_validation(source, destination_fd)
        if valid and not swapped:
            destination.rename(saved)
            destination.write_bytes(b"replacement after validation")
            swapped = True
        return valid

    monkeypatch.setattr(
        database_backup,
        "_existing_backup_is_valid",
        validate_then_swap,
    )

    rebuilt = cli.backup_database_if_due(db_path, now=now.replace(hour=20))

    assert swapped is True
    assert rebuilt == destination
    with sqlite3.connect(destination) as backup:
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert backup.execute("select body from messages").fetchone()[0] == (
            "durable state"
        )
    with sqlite3.connect(saved) as displaced:
        assert displaced.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_existing_corrupt_daily_backup_is_rebuilt(tmp_path: Path):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    backup_path = cli.backup_database_if_due(db_path, now=now)
    with sqlite3.connect(backup_path) as backup:
        page_size = int(backup.execute("pragma page_size").fetchone()[0])
        root_page = int(
            backup.execute(
                "select rootpage from sqlite_master where name='messages'"
            ).fetchone()[0]
        )
    with backup_path.open("r+b") as handle:
        handle.seek((root_page - 1) * page_size)
        handle.write(b"\0" * 64)

    rebuilt = cli.backup_database_if_due(db_path, now=now.replace(hour=20))

    assert rebuilt == backup_path
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("pragma quick_check").fetchone()[0] == "ok"
        assert backup.execute("select body from messages").fetchone()[0] == "durable state"


def test_backup_retention_uses_database_stem(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    today = date(2026, 7, 23)
    for age in range(16):
        backup_date = today - timedelta(days=age)
        (backup_dir / f"custom-{backup_date.isoformat()}.sqlite3").touch()

    cli.prune_database_backups(
        backup_dir,
        today=today,
        database_stem="custom",
    )

    remaining_ages = sorted(
        (today - date.fromisoformat(path.stem.removeprefix("custom-"))).days
        for path in backup_dir.glob("custom-*.sqlite3")
    )
    assert remaining_ages == [0, 1, 2, 3, 7, 14]


def test_backup_retention_ignores_malformed_manual_files(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    manual = backup_dir / "auto-reply-manual.sqlite3"
    manual.write_text("operator copy", encoding="utf-8")
    manual.chmod(0o644)

    deleted = cli.prune_database_backups(
        backup_dir,
        today=date(2026, 7, 23),
    )

    assert deleted == []
    assert manual.read_text(encoding="utf-8") == "operator copy"
    assert stat.S_IMODE(manual.stat().st_mode) == 0o644


def test_database_backup_loop_checks_hourly(tmp_path: Path, monkeypatch):
    assert hasattr(cli, "run_database_backup_loop")
    calls: list[Path | int] = []

    class StopLoop(Exception):
        pass

    def backup(db_path: Path):
        calls.append(db_path)

    def sleep(seconds: int):
        calls.append(seconds)
        if calls.count(3600) == 2:
            raise StopLoop

    monkeypatch.setattr(cli, "backup_database_if_due", backup)

    with __import__("pytest").raises(StopLoop):
        cli.run_database_backup_loop(tmp_path / "worker.sqlite3", sleep=sleep)

    assert calls == [
        tmp_path / "worker.sqlite3",
        3600,
        tmp_path / "worker.sqlite3",
        3600,
    ]
