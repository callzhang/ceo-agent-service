from datetime import datetime, timezone

import pytest

from app import cli
from app.store import AutoReplyStore


def test_successful_backup_check_clears_previous_component_error(tmp_path, monkeypatch):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.record_error(None, None, "database-backup", "disk was full")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2030, 1, 1, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(cli, "datetime", FixedDatetime)
    monkeypatch.delenv("CEO_DATABASE_BACKUP_PAUSED", raising=False)
    monkeypatch.setattr(cli, "backup_database_if_due", lambda _path: None)

    class StopLoop(Exception):
        pass

    def stop_after_one_check(_seconds):
        raise StopLoop

    with pytest.raises(StopLoop):
        cli.run_database_backup_loop(db_path, sleep=stop_after_one_check)

    [component] = store.list_service_health_components()
    assert component["component"] == "database-backup"
    assert component["state"] == "healthy"
    assert component["status"] == "running"
    assert component["latest_tick_at"]
    assert component["latest_error"] == ""
    assert component["latest_error_at"] == ""
    [error] = store.list_errors()
    assert error.resolved_at


def test_paused_backup_check_does_not_report_success(tmp_path, monkeypatch):
    db_path = tmp_path / "worker.sqlite3"
    monkeypatch.setenv("CEO_DATABASE_BACKUP_PAUSED", "1")
    monkeypatch.setattr(cli, "backup_database_if_due", lambda _path: None)

    class StopLoop(Exception):
        pass

    def stop_after_one_check(_seconds):
        raise StopLoop

    with pytest.raises(StopLoop):
        cli.run_database_backup_loop(db_path, sleep=stop_after_one_check)

    assert not db_path.exists()
