from pathlib import Path
import subprocess
import json

import pytest

from app.repository_updater import (
    UpgradeFailed,
    RepositoryUpdater,
    UpgradeOperation,
    UpgradePreconditionError,
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "--initial-branch=main", str(local))
    git(local, "config", "user.name", "Test User")
    git(local, "config", "user.email", "test@example.com")
    (local / "version.txt").write_text("old\n", encoding="utf-8")
    git(local, "add", "version.txt")
    git(local, "commit", "-m", "old")
    git(local, "remote", "add", "origin", str(remote))
    git(local, "push", "-u", "origin", "main")
    updater = tmp_path / "updater"
    git(tmp_path, "clone", str(remote), str(updater))
    git(updater, "config", "user.name", "Remote")
    git(updater, "config", "user.email", "remote@example.com")
    (updater / "version.txt").write_text("new\n", encoding="utf-8")
    git(updater, "add", "version.txt")
    git(updater, "commit", "-m", "new")
    git(updater, "push", "origin", "main")
    return local, remote


class StateStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get_service_state(self, key: str) -> str | None:
        return self.values.get(key)

    def set_service_state(self, key: str, value: str) -> None:
        self.values[key] = value


def operation(local: Path) -> UpgradeOperation:
    from app.repository_upgrade import GitRepository

    repo = GitRepository(local)
    repo.fetch("origin")
    local_commit = repo.resolve_ref("refs/heads/main")
    remote_commit = repo.resolve_ref("refs/remotes/origin/main")
    fingerprint = repo.fingerprint("main", local_commit, remote_commit, [])
    return UpgradeOperation(
        operation_id="op-1",
        expected_fingerprint=fingerprint,
        original_commit=local_commit,
        target_commit=remote_commit,
    )


def test_clean_upgrade_fast_forwards_and_verifies(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    calls: list[str] = []
    updater = RepositoryUpdater(
        local,
        StateStore(),
        database_path=tmp_path / "missing.sqlite3",
        restart=lambda: calls.append("restart"),
        health=lambda: calls.append("health") or True,
    )

    result = updater.execute(op)

    assert result.status == "succeeded"
    assert git(local, "rev-parse", "HEAD") == op.target_commit
    assert calls == ["restart", "health"]


def test_diverged_target_is_rejected_without_merge(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    git(local, "config", "user.name", "Local")
    git(local, "config", "user.email", "local@example.com")
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    git(local, "add", "local.txt")
    git(local, "commit", "-m", "local")
    op = operation(local)

    with pytest.raises(UpgradePreconditionError, match="diverged"):
        RepositoryUpdater(local, StateStore()).execute(op)


def test_failed_verification_rolls_back_installed_revision(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    calls: list[str] = []

    def fail_verification() -> None:
        raise RuntimeError("tests failed")

    store = StateStore()
    updater = RepositoryUpdater(
        local,
        store,
        database_path=tmp_path / "missing.sqlite3",
        verification=fail_verification,
        restart=lambda: calls.append("restart"),
        health=lambda: True,
    )

    with pytest.raises(UpgradeFailed, match="verification"):
        updater.execute(op)

    assert git(local, "rev-parse", "HEAD") == op.original_commit
    assert json.loads(store.values["repository_upgrade_operation:v1"])["status"] == "rolled_back"
