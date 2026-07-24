from pathlib import Path

from app import config
from app import audit_web
from app.cli import WorkerSettings
from scripts import backfill_follow_up_todo_ids


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


def test_audit_web_uses_shared_runtime_database(monkeypatch, tmp_path):
    expected = tmp_path / "runtime.sqlite3"
    monkeypatch.setattr(audit_web, "worker_db_path", lambda: expected)

    assert audit_web._configured_worker_db_path() == expected


def test_follow_up_backfill_defaults_to_shared_runtime_database(
    monkeypatch, tmp_path
):
    expected = tmp_path / "runtime.sqlite3"
    monkeypatch.setattr(
        backfill_follow_up_todo_ids,
        "worker_db_path",
        lambda: expected,
    )

    args = backfill_follow_up_todo_ids.build_parser().parse_args([])

    assert args.db == str(expected)
