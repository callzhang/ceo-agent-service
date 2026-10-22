import sqlite3
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import cli
from app.database_backup import prune_database_backups


def test_paused_daily_backup_does_not_touch_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_DATABASE_BACKUP_PAUSED", "1")
    db_path = tmp_path / "missing.sqlite3"
    existing = set(tmp_path.iterdir())
    assert cli.backup_database_if_due(db_path) is None
    assert set(tmp_path.iterdir()) == existing


def test_daily_backup_resumes_after_pause_is_removed(tmp_path, monkeypatch):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    monkeypatch.setenv("CEO_DATABASE_BACKUP_PAUSED", "1")
    assert cli.backup_database_if_due(db_path) is None
    monkeypatch.delenv("CEO_DATABASE_BACKUP_PAUSED")
    backup = cli.backup_database_if_due(db_path)
    assert backup is not None
    assert backup.exists()


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


def test_existing_daily_backup_cleans_owned_abandoned_stage(tmp_path: Path):
    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    destination = cli.backup_database_if_due(db_path, now=now)
    assert destination is not None
    old_bytes = destination.read_bytes()
    orphan = destination.parent / ".ceo-backup-stage-old-operation.tmp"
    orphan.write_bytes(b"abandoned copy")
    unrelated = destination.parent / ".unrelated.tmp"
    unrelated.write_bytes(b"unrelated")

    assert cli.backup_database_if_due(db_path, now=now.replace(hour=20)) is None

    assert destination.read_bytes() == old_bytes
    assert not orphan.exists()
    assert unrelated.read_bytes() == b"unrelated"


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


def test_a_backup_verifies_before_removing_earlier_copies(tmp_path: Path, monkeypatch):
    import app.database_backup as module
    from app.database_backup import backup_is_complete, create_database_backup

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for name in (
        "auto-reply-2026-07-22.sqlite3",
        "auto-reply-before-upgrade-x.sqlite3",
        ".ceo-backup-stage-old-operation.tmp",
        ".ceo-backup-stage-old-operation.tmp-wal",
        ".ceo-backup-stage-old-operation.tmp-shm",
        ".ceo-backup-stage-old-operation.tmp-journal",
        "auto-reply-2026-07-22.sqlite3-wal",
    ):
        (backup_dir / name).write_bytes(b"old")
    destination = backup_dir / "auto-reply-2026-07-23.sqlite3"

    remove_older = module._remove_older_backups

    def verify_before_prune(directory, *, keep):
        assert keep == destination
        assert backup_is_complete(destination)
        with sqlite3.connect(f"file:{destination}?mode=ro", uri=True) as backup:
            assert backup.execute("pragma integrity_check").fetchone() == ("ok",)
        return remove_older(directory, keep=keep)

    monkeypatch.setattr(module, "_remove_older_backups", verify_before_prune)

    create_database_backup(db_path, destination)

    assert list(backup_dir.iterdir()) == [destination]
    assert backup_is_complete(destination)
    with sqlite3.connect(destination) as backup:
        assert backup.execute("select body from messages").fetchone()[0] == "durable state"


def test_replacing_same_destination_captures_current_wal_state(tmp_path: Path):
    from app.database_backup import backup_is_complete, create_database_backup

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    destination = tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"
    create_database_backup(db_path, destination)
    old_bytes = destination.read_bytes()
    source_mode = stat.S_IMODE(db_path.stat().st_mode)
    with sqlite3.connect(db_path) as source:
        source.execute("update messages set body = 'current WAL state'")
        source.commit()
        create_database_backup(db_path, destination)
        assert source.execute("pragma journal_mode").fetchone() == ("wal",)

    assert backup_is_complete(destination)
    assert stat.S_IMODE(db_path.stat().st_mode) == source_mode
    assert stat.S_IMODE(destination.stat().st_mode) == source_mode
    assert destination.read_bytes() != old_bytes
    assert list(destination.parent.iterdir()) == [destination]
    with sqlite3.connect(destination) as backup:
        assert backup.execute("pragma integrity_check").fetchone() == ("ok",)
        assert backup.execute("select body from messages").fetchone() == ("current WAL state",)


def test_a_failed_backup_preserves_previous_verified_copy(tmp_path: Path, monkeypatch):
    import pytest

    import app.database_backup as module

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    destination = tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"
    module.create_database_backup(db_path, destination)
    old_bytes = destination.read_bytes()
    with sqlite3.connect(db_path) as db:
        db.execute("update messages set body = 'new state'")

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

    assert destination.read_bytes() == old_bytes
    assert list(destination.parent.iterdir()) == [destination]


def test_integrity_failure_preserves_previous_copy_and_source(tmp_path: Path, monkeypatch):
    import pytest

    import app.database_backup as module

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    destination = tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"
    older = destination.with_name("auto-reply-2026-07-22.sqlite3")
    module.create_database_backup(db_path, older)
    old_bytes = older.read_bytes()
    source_bytes = db_path.read_bytes()

    real_connect = sqlite3.connect

    class CorruptResult:
        def fetchone(self):
            return ("malformed",)

    class BrokenTarget(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "pragma integrity_check":
                return CorruptResult()
            return super().execute(sql, *args)

    def connect(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            kwargs["factory"] = BrokenTarget
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", connect)

    with pytest.raises(RuntimeError, match="integrity check failed"):
        module.create_database_backup(db_path, destination)

    assert older.read_bytes() == old_bytes
    assert db_path.read_bytes() == source_bytes
    assert list(destination.parent.iterdir()) == [older]


def test_missing_completion_marker_preserves_same_destination(tmp_path: Path, monkeypatch):
    import pytest

    import app.database_backup as module

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    destination = tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"
    module.create_database_backup(db_path, destination)
    old_bytes = destination.read_bytes()
    with sqlite3.connect(db_path) as source:
        source.execute("update messages set body = 'new state'")

    real_complete = module.backup_is_complete

    def marker_check(path):
        if path.suffix == ".tmp":
            return False
        return real_complete(path)

    monkeypatch.setattr(module, "backup_is_complete", marker_check)

    with pytest.raises(RuntimeError, match="completion marker is missing"):
        module.create_database_backup(db_path, destination)

    assert destination.read_bytes() == old_bytes
    assert list(destination.parent.iterdir()) == [destination]


def test_failed_copy_cleans_only_owned_abandoned_stage_before_retry(tmp_path: Path, monkeypatch):
    import pytest

    import app.database_backup as module

    db_path = tmp_path / "auto-reply.sqlite3"
    _create_database(db_path)
    destination = tmp_path / "backups" / "auto-reply-2026-07-23.sqlite3"
    module.create_database_backup(db_path, destination)
    old_bytes = destination.read_bytes()
    orphan = destination.parent / ".ceo-backup-stage-old-operation.tmp"
    orphan.write_bytes(b"abandoned copy")
    unrelated = destination.parent / ".unrelated.tmp"
    unrelated.write_bytes(b"unrelated")

    real_connect = sqlite3.connect

    class FullDiskSource:
        def __init__(self, real):
            self._real = real

        def execute(self, *args):
            return self._real.execute(*args)

        def backup(self, _target):
            raise sqlite3.OperationalError("database or disk is full")

        def close(self):
            self._real.close()

    def connect(path, *args, **kwargs):
        connection = real_connect(path, *args, **kwargs)
        return FullDiskSource(connection) if Path(path) == db_path else connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)

    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        module.create_database_backup(db_path, destination)

    assert destination.read_bytes() == old_bytes
    assert not orphan.exists()
    assert unrelated.read_bytes() == b"unrelated"
    assert set(destination.parent.iterdir()) == {destination, unrelated}


def test_a_backup_cut_short_is_not_counted_complete_and_is_redone(tmp_path: Path):
    # An incomplete final-name copy from the old scheme must not satisfy
    # "today's backup already exists".
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
