from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from threading import Barrier, Event, Thread
from urllib.parse import quote_from_bytes, unquote_to_bytes

import pytest

import app.repository_upgrade as repository_upgrade_module
from app.repository_upgrade import (
    GitRepository,
    LOCK_FILENAME,
    RepositoryDiagnostic,
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


def _encoded_path(raw_path: bytes | str) -> str:
    value = raw_path.encode() if isinstance(raw_path, str) else raw_path
    return "path-bytes:" + quote_from_bytes(value, safe="/-._~")


def _add_index_path(local: Path, raw_name: bytes) -> None:
    blob = _git(local, "rev-parse", "HEAD:tracked.txt").encode()
    subprocess.run(
        [
            b"git",
            b"update-index",
            b"--add",
            b"--cacheinfo",
            b"100644," + blob + b"," + raw_name,
        ],
        cwd=os.fsencode(local),
        check=True,
        capture_output=True,
    )


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


def _service(
    local: Path,
    tmp_path: Path,
    *,
    db_path: Path | None = None,
    timeout_seconds: float = 30,
) -> RepositoryUpgradeService:
    return RepositoryUpgradeService(
        repository=GitRepository(local, timeout_seconds=timeout_seconds),
        store=AutoReplyStore(db_path or tmp_path / "state.sqlite3"),
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


def _push_remote_commits(remote: Path, tmp_path: Path, messages: list[str]) -> None:
    updater = tmp_path / "updater-release-subjects"
    _git(tmp_path, "clone", "--branch", "main", str(remote), str(updater))
    _git(updater, "config", "user.name", "Remote User")
    _git(updater, "config", "user.email", "remote@example.com")
    for index, message in enumerate(messages):
        _write(updater / "remote.txt", f"{index}\n")
        _git(updater, "add", "remote.txt")
        _git(updater, "commit", "-m", message)
    _git(updater, "push", "origin", "main")


def test_check_classifies_current_repository(repositories, tmp_path: Path):
    local, _ = repositories

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.CURRENT
    assert snapshot.local_commit == snapshot.remote_commit
    assert snapshot.commits_behind == 0
    assert snapshot.dirty_paths == ()
    assert snapshot.error is None
    assert len(snapshot.fingerprint) == 64


def test_check_classifies_clean_repository_behind_remote(repositories, tmp_path: Path):
    local, remote = repositories
    _push_remote_commit(remote, tmp_path)

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.UPDATE_AVAILABLE
    assert snapshot.commits_behind == 1
    assert snapshot.release_summary == ["remote"]


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_check_classifies_dirty_repository(
    repositories,
    tmp_path: Path,
    dirty_kind: str,
):
    local, remote = repositories
    _push_remote_commit(remote, tmp_path)
    path = local / ("tracked.txt" if dirty_kind == "tracked" else "untracked.txt")
    _write(path, "changed\n")

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.LOCAL_CHANGES
    assert snapshot.dirty_paths == (_encoded_path(path.name),)


def test_ignored_and_unchanged_files_are_not_dirty(repositories, tmp_path: Path):
    local, remote = repositories
    _write(local / ".gitignore", "runtime.log\n")
    _git(local, "add", ".gitignore")
    _git(local, "commit", "-m", "ignore runtime")
    _git(local, "push", "origin", "main")
    _push_remote_commit(remote, tmp_path)
    _write(local / "runtime.log", "ignored\n")
    _write(local / "changed.txt", "changed\n")

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.LOCAL_CHANGES
    assert snapshot.dirty_paths == (_encoded_path("changed.txt"),)
    assert _encoded_path("tracked.txt") not in snapshot.dirty_paths
    assert _encoded_path("runtime.log") not in snapshot.dirty_paths


def test_check_classifies_diverged_repository(repositories, tmp_path: Path):
    local, remote = repositories
    _write(local / "local.txt", "local\n")
    _git(local, "add", "local.txt")
    _git(local, "commit", "-m", "local")
    _push_remote_commit(remote, tmp_path)

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.DIVERGED
    assert snapshot.commits_behind == 1


def test_fetch_failure_persists_only_structured_safe_diagnostic(
    repositories,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    local, _ = repositories
    secrets = (
        "Authorization: Bearer bearer-secret ",
        "X-Api-Key: header-secret ",
        "https://example.test/repo?access_token=query-secret",
    )
    real_run = repository_upgrade_module.subprocess.run

    def failed_fetch(command, **kwargs):
        if command[1] == "fetch":
            return subprocess.CompletedProcess(
                command,
                128,
                stdout=b"",
                stderr="".join(secrets).encode(),
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(repository_upgrade_module.subprocess, "run", failed_fetch)

    service = _service(local, tmp_path)
    snapshot = service.check()
    persisted = service.store.get_service_state("repository_upgrade_state:v1")

    assert snapshot.status is UpgradeStatus.CHECK_FAILED
    assert snapshot.error == RepositoryDiagnostic(
        command_category="fetch",
        code=128,
        reason="git_command_failed",
    )
    assert persisted is not None
    for secret in ("bearer-secret", "header-secret", "query-secret", "Bearer"):
        assert secret not in persisted


def test_git_timeout_is_safe_check_failure(
    repositories,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    local, _ = repositories
    real_run = repository_upgrade_module.subprocess.run

    def timed_out_fetch(command, **kwargs):
        if command[1] == "fetch":
            raise subprocess.TimeoutExpired(
                command,
                kwargs["timeout"],
                stderr=b"Authorization: Bearer timeout-secret",
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(repository_upgrade_module.subprocess, "run", timed_out_fetch)

    snapshot = _service(local, tmp_path, timeout_seconds=0.25).check()

    assert snapshot.status is UpgradeStatus.CHECK_FAILED
    assert snapshot.error == RepositoryDiagnostic(
        command_category="fetch",
        code="timeout",
        reason="git_command_timed_out",
    )
    assert "timeout-secret" not in snapshot.model_dump_json()


def test_repository_metadata_timeout_returns_check_failed(
    repositories,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    local, _ = repositories
    real_run = repository_upgrade_module.subprocess.run

    def timed_out_metadata(command, **kwargs):
        if "--git-common-dir" in command:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(repository_upgrade_module.subprocess, "run", timed_out_metadata)

    snapshot = _service(local, tmp_path).check()

    assert snapshot.status is UpgradeStatus.CHECK_FAILED
    assert snapshot.error == RepositoryDiagnostic(
        command_category="repository_metadata",
        code="timeout",
        reason="git_command_timed_out",
    )


def test_every_git_command_uses_configured_timeout(
    repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    local, _ = repositories
    real_run = repository_upgrade_module.subprocess.run
    observed_timeouts: list[float] = []

    def recording_run(command, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(repository_upgrade_module.subprocess, "run", recording_run)

    GitRepository(local, timeout_seconds=1.75).current_branch()

    assert observed_timeouts == [1.75]


def test_porcelain_parser_handles_rename_and_fingerprint_uses_exact_records(
    repositories,
):
    local, _ = repositories
    repository = GitRepository(local)
    clean_fingerprint = repository.fingerprint("main", "HEAD", "origin/main")
    _git(local, "mv", "tracked.txt", "renamed.txt")

    records = repository.status_records()

    assert [(record.code, record.path, record.original_path) for record in records] == [
        ("R ", _encoded_path("renamed.txt"), _encoded_path("tracked.txt"))
    ]
    assert repository.dirty_paths(records) == (
        _encoded_path("renamed.txt"),
        _encoded_path("tracked.txt"),
    )
    assert repository.fingerprint("main", "HEAD", "origin/main") != clean_fingerprint


def test_all_paths_have_distinct_reversible_byte_encoding(repositories, tmp_path: Path):
    local, _ = repositories
    raw_names = (b"invalid-\xff.txt", b"bytes:invalid-%FF.txt")
    for raw_name in raw_names:
        _add_index_path(local, raw_name)
    service = _service(local, tmp_path)

    snapshot = service.check()
    loaded = service.load_state()

    expected = tuple(sorted(_encoded_path(raw_name) for raw_name in raw_names))
    assert snapshot.dirty_paths == expected
    decoded = tuple(
        unquote_to_bytes(path.removeprefix("path-bytes:"))
        for path in snapshot.dirty_paths
    )
    assert decoded == tuple(
        unquote_to_bytes(path.removeprefix("path-bytes:")) for path in expected
    )
    assert set(decoded) == set(raw_names)
    assert loaded.snapshot == snapshot
    assert all(path in loaded.model_dump_json() for path in expected)


def test_release_summary_redacts_credential_subjects_before_persistence(
    repositories,
    tmp_path: Path,
):
    local, remote = repositories
    secrets = (
        "Bearer abcdefghijk",
        "api_key=abcdefghijk",
        "token: token-secret-value",
        "release(https://user:password@example.test/path)",
        "https://example.test/release?access_token=query-secret",
    )
    _push_remote_commits(remote, tmp_path, ["ordinary release", *secrets])
    service = _service(local, tmp_path)

    snapshot = service.check()
    persisted = service.store.get_service_state("repository_upgrade_state:v1")

    assert snapshot.release_summary == [
        "[redacted release subject]",
        "[redacted release subject]",
        "[redacted release subject]",
        "[redacted release subject]",
        "[redacted release subject]",
        "ordinary release",
    ]
    assert persisted is not None
    assert "[redacted release subject]" in persisted
    for secret in ("abcdefghijk", "token-secret-value", "password", "query-secret"):
        assert secret not in persisted


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
        dirty_paths=(_encoded_path("tracked.txt"),),
        fingerprint="fingerprint",
    )
    state = RepositoryUpgradeState(snapshot=snapshot)

    service.save_state(state)

    assert service.load_state() == state


def test_save_state_sanitizes_manual_release_summary(repositories, tmp_path: Path):
    local, _ = repositories
    service = _service(local, tmp_path)
    state = RepositoryUpgradeState(
        snapshot=RepositorySnapshot(
            status=UpgradeStatus.CURRENT,
            checked_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
            release_summary=["api_key=manual-secret-value"],
        )
    )

    service.save_state(state)

    persisted = service.store.get_service_state("repository_upgrade_state:v1")
    assert service.load_state().snapshot.release_summary == [
        "[redacted release subject]"
    ]
    assert persisted is not None
    assert "manual-secret-value" not in persisted


def test_operation_reservation_is_idempotent_only_for_same_fingerprint(
    repositories,
    tmp_path: Path,
):
    local, _ = repositories
    service = _service(local, tmp_path)

    first = service.reserve_operation("operation-1", "fingerprint-1")
    second = service.reserve_operation("operation-1", "fingerprint-1")

    assert first.idempotent is False
    assert second.idempotent is True
    assert second.operation == first.operation

    with pytest.raises(RepositoryUpgradeConflict, match="fingerprint"):
        service.reserve_operation("operation-1", "fingerprint-2")


def test_operation_reservation_refuses_different_operation(repositories, tmp_path: Path):
    local, _ = repositories
    service = _service(local, tmp_path)
    service.reserve_operation("operation-1", "fingerprint-1")

    with pytest.raises(RepositoryUpgradeConflict, match="operation-1"):
        service.reserve_operation("operation-2", "fingerprint-1")


def test_lock_file_payload_is_not_used_as_operation_state(repositories, tmp_path: Path):
    local, _ = repositories
    repository = GitRepository(local)
    lock_path = repository.common_git_dir / LOCK_FILENAME
    lock_path.write_bytes(b"persistent mutex file, not JSON")
    service = _service(local, tmp_path)

    reservation = service.reserve_operation("operation-1", "fingerprint-1")

    assert reservation.idempotent is False
    assert lock_path.exists()


def test_operation_reservation_waits_for_repository_inspection(
    repositories,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    local, remote = repositories
    _push_remote_commit(remote, tmp_path)
    service = _service(local, tmp_path)
    fetch_entered = Event()
    release_fetch = Event()
    reservation_started = Event()
    reservation_finished = Event()
    original_fetch = service.repository.fetch

    def paused_fetch(remote_name: str) -> None:
        fetch_entered.set()
        assert release_fetch.wait(timeout=5)
        original_fetch(remote_name)

    monkeypatch.setattr(service.repository, "fetch", paused_fetch)
    snapshots: list[RepositorySnapshot] = []
    reservations = []
    check_thread = Thread(target=lambda: snapshots.append(service.check()))
    check_thread.start()
    assert fetch_entered.wait(timeout=5)

    def reserve() -> None:
        reservation_started.set()
        reservations.append(service.reserve_operation("operation-1", "fingerprint-1"))
        reservation_finished.set()

    reservation_thread = Thread(target=reserve)
    reservation_thread.start()
    assert reservation_started.wait(timeout=5)
    assert not reservation_finished.wait(timeout=0.1)

    release_fetch.set()
    check_thread.join(timeout=10)
    reservation_thread.join(timeout=10)

    assert snapshots[0].status is UpgradeStatus.UPDATE_AVAILABLE
    assert reservations[0].operation == service.load_state().operation
    assert not check_thread.is_alive()
    assert not reservation_thread.is_alive()


def test_second_check_cannot_fetch_until_first_check_transaction_completes(
    repositories,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    local, remote = repositories
    _push_remote_commit(remote, tmp_path)
    db_path = tmp_path / "shared-check-state.sqlite3"
    first = _service(local, tmp_path, db_path=db_path)
    second = _service(local, tmp_path, db_path=db_path)
    first_fetch_barrier = Barrier(2)
    release_first_fetch = Event()
    first_final_state_written = Event()
    second_fetch_entered = Event()
    original_first_fetch = first.repository.fetch
    original_second_fetch = second.repository.fetch
    original_first_save = first._save_state_unlocked

    def paused_first_fetch(remote_name: str) -> None:
        first_fetch_barrier.wait(timeout=5)
        assert release_first_fetch.wait(timeout=5)
        original_first_fetch(remote_name)

    def observed_first_save(state: RepositoryUpgradeState) -> None:
        original_first_save(state)
        if state.snapshot and state.snapshot.status is not UpgradeStatus.CHECKING:
            first_final_state_written.set()

    def observed_second_fetch(remote_name: str) -> None:
        assert first_final_state_written.is_set()
        second_fetch_entered.set()
        original_second_fetch(remote_name)

    monkeypatch.setattr(first.repository, "fetch", paused_first_fetch)
    monkeypatch.setattr(first, "_save_state_unlocked", observed_first_save)
    monkeypatch.setattr(second.repository, "fetch", observed_second_fetch)
    first_thread = Thread(target=first.check)
    first_thread.start()
    first_fetch_barrier.wait(timeout=5)
    second_thread = Thread(target=second.check)
    second_thread.start()

    assert not second_fetch_entered.wait(timeout=0.1)
    release_first_fetch.set()
    assert second_fetch_entered.wait(timeout=10)
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()


def test_linked_worktrees_share_mutex_and_refuse_concurrent_operations(
    repositories,
    tmp_path: Path,
):
    local, _ = repositories
    linked = tmp_path / "linked"
    _git(local, "worktree", "add", "-b", "linked-test", str(linked))
    db_path = tmp_path / "shared-state.sqlite3"
    first = _service(local, tmp_path, db_path=db_path)
    second = _service(linked, tmp_path, db_path=db_path)
    rendezvous = Barrier(2)

    assert first.repository.common_git_dir == second.repository.common_git_dir
    assert first.repository.lock_path == second.repository.lock_path

    def reserve(service: RepositoryUpgradeService, operation_id: str):
        rendezvous.wait(timeout=5)
        try:
            return service.reserve_operation(operation_id, "same-fingerprint")
        except RepositoryUpgradeConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                reserve,
                (first, second),
                ("operation-1", "operation-2"),
            )
        )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, RepositoryUpgradeConflict) for result in results) == 1
    assert first.repository.lock_path.exists()


def test_linked_worktree_mutex_blocks_until_holder_releases(
    repositories,
    tmp_path: Path,
):
    local, _ = repositories
    linked = tmp_path / "linked-lock"
    _git(local, "worktree", "add", "-b", "linked-lock-test", str(linked))
    first = GitRepository(local)
    second = GitRepository(linked)
    holder_ready = Event()
    release_holder = Event()
    contender_started = Event()
    contender_acquired = Event()

    def hold_mutex() -> None:
        with first.mutex():
            holder_ready.set()
            assert release_holder.wait(timeout=5)

    def contend_for_mutex() -> None:
        contender_started.set()
        with second.mutex():
            contender_acquired.set()

    holder = Thread(target=hold_mutex)
    holder.start()
    assert holder_ready.wait(timeout=5)
    contender = Thread(target=contend_for_mutex)
    contender.start()
    assert contender_started.wait(timeout=5)

    assert not contender_acquired.wait(timeout=0.1)

    release_holder.set()
    assert contender_acquired.wait(timeout=5)
    holder.join(timeout=5)
    contender.join(timeout=5)
    assert not holder.is_alive()
    assert not contender.is_alive()
