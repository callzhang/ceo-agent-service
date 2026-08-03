from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from app.repository_upgrade import (
    GitRepository,
    LOCK_FILENAME,
    RepositorySnapshot,
    RepositoryUpgradeConflict,
    RepositoryUpgradeService,
    RepositoryUpgradeState,
    UpgradeStatus,
)
from app.store import AutoReplyStore


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def repositories(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "--initial-branch=main", str(local))
    _git(local, "config", "user.name", "Test User")
    _git(local, "config", "user.email", "test@example.com")
    _write(local / "tracked.txt", "initial\n")
    _git(local, "add", "tracked.txt")
    _git(local, "commit", "-m", "initial")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-u", "origin", "main")
    return local, remote


def _service(local: Path, tmp_path: Path) -> RepositoryUpgradeService:
    return RepositoryUpgradeService(
        repository=GitRepository(local),
        store=AutoReplyStore(tmp_path / "state.sqlite3"),
        remote="origin",
        branch="main",
    )


def _push_remote_commit(remote: Path, tmp_path: Path, message: str = "remote") -> None:
    updater = tmp_path / f"updater-{message}"
    _git(tmp_path, "clone", "--branch", "main", str(remote), str(updater))
    _git(updater, "config", "user.name", "Remote User")
    _git(updater, "config", "user.email", "remote@example.com")
    _write(updater / "remote.txt", f"{message}\n")
    _git(updater, "add", "remote.txt")
    _git(updater, "commit", "-m", message)
    _git(updater, "push", "origin", "main")


def test_check_classifies_current_repository(repositories, tmp_path: Path):
    local, _ = repositories

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.CURRENT
    assert snapshot.local_commit == snapshot.remote_commit
    assert snapshot.commits_behind == 0
    assert snapshot.dirty_paths == []
    assert snapshot.error is None
    assert len(snapshot.fingerprint) == 64


def test_check_classifies_clean_repository_behind_remote(repositories, tmp_path: Path):
    local, remote = repositories
    _push_remote_commit(remote, tmp_path)

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.UPDATE_AVAILABLE
    assert snapshot.commits_behind == 1
    assert snapshot.release_summary == ["remote"]


def test_check_classifies_dirty_tracked_repository(repositories, tmp_path: Path):
    local, remote = repositories
    _push_remote_commit(remote, tmp_path)
    _write(local / "tracked.txt", "changed\n")

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.LOCAL_CHANGES
    assert snapshot.dirty_paths == ["tracked.txt"]


def test_check_classifies_dirty_untracked_repository(repositories, tmp_path: Path):
    local, remote = repositories
    _push_remote_commit(remote, tmp_path)
    _write(local / "untracked.txt", "new\n")

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.LOCAL_CHANGES
    assert snapshot.dirty_paths == ["untracked.txt"]


def test_ignored_runtime_file_does_not_make_repository_dirty(repositories, tmp_path: Path):
    local, remote = repositories
    _write(local / ".gitignore", "runtime.log\n")
    _git(local, "add", ".gitignore")
    _git(local, "commit", "-m", "ignore runtime")
    _git(local, "push", "origin", "main")
    _push_remote_commit(remote, tmp_path)
    _write(local / "runtime.log", "ignored\n")

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.UPDATE_AVAILABLE
    assert "runtime.log" not in snapshot.dirty_paths
    assert "runtime.log" not in GitRepository(local).visible_paths()


def test_check_classifies_diverged_repository(repositories, tmp_path: Path):
    local, remote = repositories
    _write(local / "local.txt", "local\n")
    _git(local, "add", "local.txt")
    _git(local, "commit", "-m", "local")
    _push_remote_commit(remote, tmp_path)

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.DIVERGED
    assert snapshot.commits_behind == 1


def test_check_redacts_and_bounds_fetch_failure(repositories, tmp_path: Path):
    local, _ = repositories
    secret_remote = tmp_path / ("secret-token-" + "x" * 700)
    _git(local, "remote", "set-url", "origin", str(secret_remote))

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.CHECK_FAILED
    assert snapshot.error
    assert "secret-token" not in snapshot.error
    assert str(tmp_path) not in snapshot.error
    assert len(snapshot.error) <= 500


def test_porcelain_parser_handles_rename_and_fingerprint_uses_exact_records(
    repositories,
):
    local, _ = repositories
    repository = GitRepository(local)
    clean_fingerprint = repository.fingerprint("main", "HEAD", "origin/main")
    _git(local, "mv", "tracked.txt", "renamed.txt")

    records = repository.status_records()

    assert [(record.code, record.path, record.original_path) for record in records] == [
        ("R ", "renamed.txt", "tracked.txt")
    ]
    assert repository.dirty_paths(records) == ["renamed.txt", "tracked.txt"]
    assert repository.visible_paths() == ["renamed.txt"]
    assert repository.fingerprint("main", "HEAD", "origin/main") != clean_fingerprint


def test_repository_upgrade_state_round_trip(repositories, tmp_path: Path):
    local, _ = repositories
    service = _service(local, tmp_path)
    snapshot = RepositorySnapshot(
        status=UpgradeStatus.CHECKING,
        checked_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        local_commit="abc",
        remote_commit="def",
        commits_behind=2,
        release_summary=["one", "two"],
        dirty_paths=["tracked.txt"],
        fingerprint="fingerprint",
    )
    state = RepositoryUpgradeState(snapshot=snapshot)

    service.save_state(state)

    assert service.load_state() == state


def test_operation_reservation_is_idempotent_for_same_operation(
    repositories,
    tmp_path: Path,
):
    local, _ = repositories
    service = _service(local, tmp_path)

    first = service.reserve_operation("operation-1", "fingerprint-1")
    second = service.reserve_operation("operation-1", "fingerprint-1")

    assert first.acquired is True
    assert first.idempotent is False
    assert second.acquired is True
    assert second.idempotent is True
    assert service.load_state().operation == first.operation


def test_operation_reservation_refuses_conflicting_operation(
    repositories,
    tmp_path: Path,
):
    local, _ = repositories
    service = _service(local, tmp_path)
    service.reserve_operation("operation-1", "fingerprint-1")

    with pytest.raises(RepositoryUpgradeConflict, match="operation-1"):
        service.reserve_operation("operation-2", "fingerprint-1")


def test_operation_reservation_preserves_malformed_existing_lock(
    repositories,
    tmp_path: Path,
):
    local, _ = repositories
    repository = GitRepository(local)
    lock_path = repository.git_dir / LOCK_FILENAME
    lock_path.write_bytes(b"not-json")
    service = RepositoryUpgradeService(
        repository=repository,
        store=AutoReplyStore(tmp_path / "state.sqlite3"),
        remote="origin",
        branch="main",
    )

    with pytest.raises(RepositoryUpgradeConflict, match="malformed"):
        service.reserve_operation("operation-1", "fingerprint-1")

    assert lock_path.read_bytes() == b"not-json"
