import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import cli


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
