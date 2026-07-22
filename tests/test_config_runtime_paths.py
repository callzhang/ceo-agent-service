from pathlib import Path

from app import config
from app.cli import WorkerSettings


def test_worker_database_defaults_to_application_support(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/example")
    monkeypatch.delenv("CEO_WORKER_DB", raising=False)

    assert config.worker_db_path() == Path(
        "/Users/example/Library/Application Support/ceo-agent-service/auto-reply.sqlite3"
    )


def test_worker_settings_database_default_is_outside_repository():
    assert WorkerSettings().db_path == (
        Path.home()
        / "Library"
        / "Application Support"
        / "ceo-agent-service"
        / "auto-reply.sqlite3"
    )
