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


def test_meeting_memory_worker_count_has_an_independent_bounded_setting(
    monkeypatch,
):
    monkeypatch.setenv("CEO_MEETING_MEMORY_WORKERS", "3")
    assert config.meeting_memory_worker_count() == 3

    monkeypatch.setenv("CEO_MEETING_MEMORY_WORKERS", "5")
    try:
        config.meeting_memory_worker_count()
    except ValueError as exc:
        assert str(exc) == "CEO_MEETING_MEMORY_WORKERS must be between 1 and 4"
    else:
        raise AssertionError("out-of-range Meeting Memory worker count was accepted")
