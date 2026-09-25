import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import cli
from app.database_backup import prune_database_backups


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
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("pragma journal_mode").fetchone()[0] == "delete"
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert backup.execute("select body from messages").fetchone()[0] == "durable state"


def test_backup_retention_keeps_only_newest_database_backup(
    tmp_path: Path,
):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    today = date(2026, 7, 23)
    for age in range(16):
        backup_date = today - timedelta(days=age)
        (backup_dir / f"auto-reply-{backup_date.isoformat()}.sqlite3").touch()

    manual_backup = backup_dir / "auto-reply-before-recovery.sqlite3"
    manual_backup.touch()
    newest = backup_dir / f"auto-reply-{today.isoformat()}.sqlite3"
    newest.touch()

    deleted = prune_database_backups(
        backup_dir,
        today=today,
        keep_path=newest,
    )

    assert list(backup_dir.iterdir()) == [newest]
    assert manual_backup in deleted
    assert len(deleted) == 16


def test_backup_retention_removes_sqlite_sidecars(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    keep = backup_dir / "auto-reply-2026-07-23.sqlite3"
    keep.touch()
    sidecars = [
        backup_dir / "auto-reply-before-recovery.sqlite3-wal",
        backup_dir / "auto-reply-before-recovery.sqlite3-shm",
    ]
    for sidecar in sidecars:
        sidecar.touch()

    deleted = prune_database_backups(
        backup_dir,
        today=date(2026, 7, 23),
        keep_path=keep,
    )

    assert set(deleted) == set(sidecars)
    assert list(backup_dir.iterdir()) == [keep]


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


def test_a_backup_removes_the_earlier_ones_first_and_leaves_one_file(tmp_path: Path):
    # Derek, 2026-09-25: no temp files; earlier backups go before the new one.
    from app.database_backup import backup_is_complete, create_database_backup

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for name in (
        "auto-reply-2026-07-22.sqlite3",
        "auto-reply-before-upgrade-x.sqlite3",
        ".auto-reply-2026-07-21.sqlite3.deadbeef.tmp",
        "auto-reply-2026-07-22.sqlite3-wal",
    ):
        (backup_dir / name).write_bytes(b"old")
    destination = backup_dir / "auto-reply-2026-07-23.sqlite3"

    create_database_backup(db_path, destination)

    assert list(backup_dir.iterdir()) == [destination]
    assert backup_is_complete(destination)
    with sqlite3.connect(destination) as backup:
        assert backup.execute("select body from messages").fetchone()[0] == "durable state"


def test_a_failed_backup_leaves_no_file_behind(tmp_path: Path, monkeypatch):
    import pytest

    import app.database_backup as module

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    destination = tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"

    class BrokenSource:
        def __init__(self, real):
            self._real = real

        def execute(self, *args):
            return self._real.execute(*args)

        def backup(self, target):
            target.execute("create table partial (x)")
            raise sqlite3.OperationalError("database or disk is full")

        def close(self):
            self._real.close()

    real_connect = sqlite3.connect

    def connect(path, *args, **kwargs):
        connection = real_connect(path, *args, **kwargs)
        return BrokenSource(connection) if Path(path) == db_path else connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)

    with pytest.raises(sqlite3.OperationalError):
        module.create_database_backup(db_path, destination)

    assert list(destination.parent.iterdir()) == []


def test_a_backup_cut_short_is_not_counted_complete_and_is_redone(tmp_path: Path):
    # A restart that kills a backup mid-copy leaves a half-written file under
    # the final name; it must not satisfy "today's backup already exists".
    from app.database_backup import backup_is_complete

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    partial = tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"
    partial.parent.mkdir()
    partial.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)

    assert not backup_is_complete(partial)
    backup_path = cli.backup_database_if_due(db_path, now=now)

    assert backup_path == partial
    assert backup_is_complete(partial)
    with sqlite3.connect(partial) as backup:
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
