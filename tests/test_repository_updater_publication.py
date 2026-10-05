from pathlib import Path
import json
import subprocess
import sqlite3
import sys

import pytest

from app.repository_upgrade import GitRepository
from app.repository_updater import (
    ExistingSchemaUpgradeStateStore, RepositoryUpdater, UpgradeFailed, UpgradeOperation,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, UpgradeOperation]:
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    _git(tmp_path, "init", "--initial-branch=main", str(local))
    _git(local, "config", "user.name", "Test")
    _git(local, "config", "user.email", "test@example.com")
    (local / "version").write_text("old\n")
    _git(local, "add", "version")
    _git(local, "commit", "-m", "old")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-u", "origin", "main")
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(remote), str(other))
    _git(other, "config", "user.name", "Test")
    _git(other, "config", "user.email", "test@example.com")
    (other / "version").write_text("new\n")
    _git(other, "add", "version")
    _git(other, "commit", "-m", "new")
    _git(other, "push", "origin", "main")
    repo = GitRepository(local)
    repo.fetch("origin")
    old = repo.resolve_ref("refs/heads/main")
    new = repo.resolve_ref("refs/remotes/origin/main")
    return local, UpgradeOperation(
        operation_id="offline-publication",
        expected_fingerprint=repo.fingerprint("main", old, new, []),
        original_commit=old,
        target_commit=new,
    )


class _State:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set_service_state(self, key: str, value: str) -> None:
        self.values[key] = value

    def get_service_state(self, key: str) -> str | None:
        return self.values.get(key)


class _Receipt:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def publish(self) -> None:
        self.events.append("publish")

    def verify_loaded(self) -> None:
        self.events.append("loaded")

    def rollback(self) -> None:
        self.events.append("rollback_publication")

    def finalize(self) -> None:
        self.events.append("finalize_publication")


def test_publication_runs_in_stopped_window_and_verifies_after_health(tmp_path: Path):
    local, operation = _repo(tmp_path)
    events: list[str] = []
    updater = RepositoryUpdater(
        local, _State(), database_path=tmp_path / "absent.db",
        wait_for_quiet=lambda: events.append("quiet"),
        stop=lambda: events.append("stop"),
        dependency_sync=lambda: events.append("dependencies"),
        verification=lambda: events.append("imports"),
        publication=lambda: _Receipt(events),
        restart=lambda: events.append("start"),
        health=lambda: events.append("health") or True,
    )

    result = updater.execute(operation)

    assert result.status == "succeeded"
    assert events == [
        "quiet", "stop", "dependencies", "imports", "publish", "start",
        "health", "loaded", "finalize_publication",
    ]


def test_health_failure_stops_replacement_and_restores_publication_before_old_start(
    tmp_path: Path,
):
    local, operation = _repo(tmp_path)
    events: list[str] = []
    updater = RepositoryUpdater(
        local, _State(), database_path=tmp_path / "absent.db",
        stop=lambda: events.append("stop"),
        publication=lambda: _Receipt(events),
        restart=lambda: events.append("start"),
        health=lambda: events.append("health") or events.count("health") > 1,
    )

    with pytest.raises(UpgradeFailed):
        updater.execute(operation)

    assert events == [
        "stop", "publish", "start", "health", "stop",
        "rollback_publication", "start", "health",
    ]
    assert (local / "version").read_text() == "old\n"


def test_failed_bootstrap_restores_publication_without_stopping_absent_replacement(
    tmp_path: Path,
):
    local, operation = _repo(tmp_path)
    events: list[str] = []

    def start() -> None:
        events.append("start")
        if events.count("start") == 1:
            raise OSError("replacement bootstrap failed")

    updater = RepositoryUpdater(
        local, _State(), database_path=tmp_path / "absent.db",
        stop=lambda: events.append("stop"),
        publication=lambda: _Receipt(events),
        restart=start,
        health=lambda: events.append("health") or True,
    )

    with pytest.raises(UpgradeFailed):
        updater.execute(operation)

    assert events == [
        "stop", "publish", "start", "rollback_publication", "start", "health",
    ]
    assert (local / "version").read_text() == "old\n"


def test_deploy_state_store_does_not_initialize_new_application_schema(tmp_path: Path):
    db = tmp_path / "Application Support/old-service.sqlite3"
    db.parent.mkdir()
    with sqlite3.connect(db) as connection:
        connection.execute(
            "create table service_state (key text primary key, value text not null, updated_at text)"
        )

    store = ExistingSchemaUpgradeStateStore(db)
    store.set_service_state("upgrade", "waiting")

    assert store.get_service_state("upgrade") == "waiting"
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute(
            "select name from sqlite_master where type='table'"
        )}
    assert tables == {"service_state"}


def test_deploy_cli_passes_explicit_publication_only_when_requested(monkeypatch, tmp_path: Path):
    import app.deploy as deploy_module

    calls = []
    monkeypatch.setattr(
        deploy_module, "deploy",
        lambda root, db, *, publish_contracts=False: calls.append(
            (root, db, publish_contracts)
        ) or "offline",
    )
    root = tmp_path / "checkout"
    db = tmp_path / "service.sqlite3"
    monkeypatch.setattr(
        sys, "argv",
        ["app.deploy", "--root", str(root), "--db", str(db), "--publish-consumer-system-contracts"],
    )

    assert deploy_module.main() == 0
    assert calls == [(root, db, True)]


def test_receipt_finalize_failure_keeps_truthful_healthy_upgrade_status(tmp_path: Path):
    local, operation = _repo(tmp_path)
    state = _State()
    events: list[str] = []

    class FinalizeFails(_Receipt):
        def finalize(self) -> None:
            events.append("finalize_failure")
            raise OSError("backup directory is busy")

    updater = RepositoryUpdater(
        local, state, database_path=tmp_path / "absent.db",
        publication=lambda: FinalizeFails(events),
        restart=lambda: events.append("start"),
        health=lambda: events.append("health") or True,
    )

    result = updater.execute(operation)

    assert result.status == "succeeded"
    assert "finalization failed" in result.error
    assert events == ["publish", "start", "health", "loaded", "finalize_failure"]
    stored = json.loads(next(iter(state.values.values())))
    assert stored["status"] == "succeeded"
    assert "finalization failed" in stored["error"]
    assert (local / "version").read_text() == "new\n"


def test_deploy_output_shows_publication_finalization_error_without_changing_success(
    monkeypatch, tmp_path: Path,
):
    import app.deploy as deploy_module

    local, _operation = _repo(tmp_path)
    db = tmp_path / "old-service.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "create table service_state (key text primary key, value text not null, updated_at text)"
        )

    class FinalizeFails(_Receipt):
        def finalize(self) -> None:
            raise OSError("receipt write failed")

    monkeypatch.setattr(deploy_module, "ensure_production_guards", lambda _root: None)
    monkeypatch.setattr(deploy_module, "unlock_source_tree", lambda _root: None)
    monkeypatch.setattr(deploy_module, "lock_source_tree", lambda _root: None)
    monkeypatch.setattr(deploy_module, "wait_until_quiet", lambda _db: None)
    monkeypatch.setattr(deploy_module, "_default_stop", lambda: None)
    monkeypatch.setattr(deploy_module, "_default_start", lambda: None)
    monkeypatch.setattr(deploy_module, "build_frontend", lambda _root, _changed: None)
    monkeypatch.setattr(deploy_module, "verify_imports", lambda _root: None)
    monkeypatch.setattr(deploy_module, "wait_for_health", lambda: True)
    monkeypatch.setattr(
        deploy_module, "prepare_consumer_system_contracts",
        lambda **_kwargs: FinalizeFails([]),
    )

    message = deploy_module.deploy(local, db, publish_contracts=True)

    assert message.startswith("deployed ")
    assert "publication receipt finalization failed: receipt write failed" in message
    assert (local / "version").read_text() == "new\n"


def test_formal_deploy_does_not_migrate_live_database_before_backup(
    monkeypatch, tmp_path: Path,
):
    import app.deploy as deploy_module

    local, _operation = _repo(tmp_path)
    db = tmp_path / "old-service.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "create table service_state (key text primary key, value text not null, updated_at text)"
        )
    monkeypatch.setattr(deploy_module, "ensure_production_guards", lambda _root: None)
    monkeypatch.setattr(deploy_module, "unlock_source_tree", lambda _root: None)
    monkeypatch.setattr(deploy_module, "lock_source_tree", lambda _root: None)
    monkeypatch.setattr(deploy_module, "wait_until_quiet", lambda _db: None)
    monkeypatch.setattr(deploy_module, "_default_stop", lambda: None)
    monkeypatch.setattr(deploy_module, "_default_start", lambda: None)
    monkeypatch.setattr(deploy_module, "build_frontend", lambda _root, _changed: None)
    monkeypatch.setattr(deploy_module, "verify_imports", lambda _root: None)
    monkeypatch.setattr(deploy_module, "wait_for_health", lambda: True)

    result = deploy_module.deploy(local, db)

    assert result.startswith("deployed ")
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute(
            "select name from sqlite_master where type='table'"
        )}
    assert tables == {"service_state"}


def test_partial_publish_failure_restores_receipt_before_old_service_start(tmp_path):
    local, operation = _repo(tmp_path)
    events = []

    class PartialFails(_Receipt):
        def publish(self):
            self.events.append("partial_publish")
            raise OSError("partial asset swap failed")

    updater = RepositoryUpdater(
        local, _State(), database_path=tmp_path / "absent.db",
        stop=lambda: events.append("stop"), publication=lambda: PartialFails(events),
        restart=lambda: events.append("start"), health=lambda: events.append("health") or True,
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events == ["stop", "partial_publish", "rollback_publication", "start", "health"]
    assert (local / "version").read_text() == "old\n"
