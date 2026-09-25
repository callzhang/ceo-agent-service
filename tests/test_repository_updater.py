from pathlib import Path
import subprocess
import json
import sqlite3

import pytest

from app.repository_updater import (
    UpgradeFailed,
    RepositoryUpdater,
    UpgradeOperation,
    UpgradePreconditionError,
    load_persisted_operation,
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


def test_dirty_upgrade_preserves_exact_branch_and_message(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    (local / "draft.txt").write_text("draft\n", encoding="utf-8")
    op_base = operation(local)
    from app.repository_upgrade import GitRepository

    repo = GitRepository(local)
    records = repo.status_records()
    op = op_base.__class__(
        **{**op_base.__dict__, "expected_fingerprint": repo.fingerprint("main", op_base.original_commit, op_base.target_commit, records), "branch_name": "preserve/local", "commit_message": "chore: preserve local draft"}
    )
    result = RepositoryUpdater(
        local,
        StateStore(),
        restart=lambda: None,
        health=lambda: True,
    ).execute(op)

    assert result.status == "succeeded"
    assert git(local, "show", "preserve/local", "--format=%s", "--no-patch") == "chore: preserve local draft"
    assert git(local, "status", "--porcelain") == ""


def test_persisted_operation_can_be_loaded_by_id():
    store = StateStore()
    store.values["repository_upgrade_operation:v1"] = json.dumps(
        {
            "operation_id": "op-1",
            "status": "preparing",
            "original_commit": "a" * 40,
            "target_commit": "b" * 40,
            "branch_name": "",
            "commit_message": "",
        }
    )

    loaded = load_persisted_operation(store, "op-1")

    assert loaded == UpgradeOperation(
        operation_id="op-1",
        expected_fingerprint="",
        original_commit="a" * 40,
        target_commit="b" * 40,
    )


def test_upgrade_backups_keep_only_one_snapshot(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table state (value text)")
        db.execute("insert into state values ('before')")
        db.commit()
    updater = RepositoryUpdater(local, StateStore(), database_path=db_path)
    first = updater._backup(operation(local))
    second_operation = operation(local).__class__(
        **{**operation(local).__dict__, "operation_id": "op-2"}
    )
    second = updater._backup(second_operation)

    snapshots = sorted((tmp_path / "backups").glob("*.sqlite3"))
    assert first != second
    assert snapshots == [second]


def test_busy_service_is_never_updated_or_restarted(tmp_path: Path):
    # Derek, 2026-09-25: a restart over running work killed Agent turns and let
    # a supervisor child load new code beside old ones. Wait first; if the
    # service never goes quiet, change nothing.
    from app.repository_updater import wait_until_quiet

    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    calls: list[str] = []
    ticks = iter(range(0, 10_000, 10))
    updater = RepositoryUpdater(
        local,
        StateStore(),
        database_path=tmp_path / "missing.sqlite3",
        wait_for_quiet=lambda: wait_until_quiet(
            tmp_path / "db", timeout_seconds=30, count=lambda _path: 1,
            sleep=lambda _seconds: None, clock=lambda: next(ticks),
        ),
        restart=lambda: calls.append("restart"),
        health=lambda: True,
    )

    with pytest.raises(UpgradePreconditionError, match="did not become idle"):
        updater.execute(op)

    assert git(local, "rev-parse", "HEAD") == op.original_commit
    assert calls == []


def test_quiet_wait_returns_once_work_drains():
    from app.repository_updater import wait_until_quiet

    counts = iter([2, 1, 0])
    wait_until_quiet(
        Path("db"), count=lambda _path: next(counts),
        sleep=lambda _seconds: None, clock=lambda: 0.0,
    )


def test_health_waits_for_a_slow_start_instead_of_rolling_back():
    from app.repository_updater import wait_for_health

    answers = iter([False, False, True])
    assert wait_for_health(lambda: next(answers), sleep=lambda _s: None, clock=lambda: 0.0)
    ticks = iter([0.0, 5.0, 11.0])
    assert not wait_for_health(
        lambda: False, timeout_seconds=10, sleep=lambda _s: None, clock=lambda: next(ticks)
    )


def _commit_frontend(root: Path, text: str) -> None:
    import subprocess

    (root / "frontend").mkdir(exist_ok=True)
    (root / "frontend" / "app.tsx").write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "frontend"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"],
        cwd=root, check=True,
    )


def test_console_is_rebuilt_when_it_was_not_built_from_the_current_frontend(tmp_path: Path):
    """The stamp, not this deploy's diff, decides: an earlier deploy may have moved the checkout."""
    import subprocess

    from app.repository_updater import _built_stamp, _frontend_tree, frontend_needs_build

    _commit_frontend(tmp_path, "one")
    assert frontend_needs_build(tmp_path)  # nothing built yet
    built = tmp_path / "app" / "static" / "workbench"
    built.mkdir(parents=True)
    (built / "index.html").write_text("<html></html>", encoding="utf-8")
    assert frontend_needs_build(tmp_path)  # built by something that left no stamp
    _built_stamp(tmp_path).write_text(_frontend_tree(tmp_path) + "\n", encoding="utf-8")
    assert not frontend_needs_build(tmp_path)
    (tmp_path / "frontend" / "app.tsx").write_text("two", encoding="utf-8")
    subprocess.run(["git", "add", "frontend"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "y"],
        cwd=tmp_path, check=True,
    )
    assert frontend_needs_build(tmp_path)  # the checkout moved on without a build


def test_service_root_defaults_to_the_services_checkout(monkeypatch, tmp_path: Path):
    from app.config import service_root

    monkeypatch.delenv("CEO_SERVICE_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert service_root() == tmp_path / "Services" / "ceo-agent-service"
    monkeypatch.setenv("CEO_SERVICE_ROOT", str(tmp_path / "elsewhere"))
    assert service_root() == tmp_path / "elsewhere"


def test_deploy_that_loses_the_race_reports_the_winner(tmp_path: Path, monkeypatch):
    # Two sessions deployed at once on 2026-09-25; the second printed a
    # traceback although nothing was wrong.
    import app.deploy as deploy_module

    local, _ = fixture_repo(tmp_path)
    moved_to: list[str] = []

    class RacingUpdater:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, operation):
            git(local, "merge", "--ff-only", "origin/main")
            moved_to.append(git(local, "rev-parse", "HEAD"))
            raise UpgradePreconditionError("repository revision changed")

    monkeypatch.setattr(deploy_module, "RepositoryUpdater", RacingUpdater)
    monkeypatch.setattr("app.store.AutoReplyStore", lambda _path: StateStore())

    message = deploy_module.deploy(local, tmp_path / "db.sqlite3")

    assert message == f"another deploy got there first; production is at {moved_to[0][:8]}"


def test_deploy_names_commits_made_in_the_production_checkout(tmp_path: Path):
    import app.deploy as deploy_module

    local, _ = fixture_repo(tmp_path)
    (local / "stray.txt").write_text("made in production\n", encoding="utf-8")
    git(local, "add", "stray.txt")
    git(local, "commit", "-m", "stray production commit")

    with pytest.raises(SystemExit) as stopped:
        deploy_module.deploy(local, tmp_path / "db.sqlite3")

    assert "has diverged from origin/main" in str(stopped.value)
    assert "stray production commit" in str(stopped.value)
    assert git(local, "log", "-1", "--format=%s") == "stray production commit"


def test_production_checkout_refuses_commits_but_still_fast_forwards(tmp_path: Path):
    from app.deploy import ensure_production_guards

    local, _ = fixture_repo(tmp_path)
    ensure_production_guards(local)
    ensure_production_guards(local)  # a second deploy leaves one hook, unchanged
    (local / "stray.txt").write_text("edited in production\n", encoding="utf-8")
    git(local, "add", "stray.txt")

    refused = subprocess.run(
        ["git", "commit", "-m", "stray"], cwd=local, capture_output=True, text=True
    )

    assert refused.returncode != 0
    assert "production checkout" in refused.stderr
    git(local, "reset", "-q", "--hard")
    op = operation(local)
    result = RepositoryUpdater(
        local, StateStore(), database_path=tmp_path / "missing.sqlite3",
        restart=lambda: None, health=lambda: True,
    ).execute(op)
    assert result.status == "succeeded"


def test_tests_refuse_to_run_in_the_production_checkout(tmp_path: Path, monkeypatch):
    from app.config import is_production_checkout

    monkeypatch.setenv("CEO_SERVICE_ROOT", str(tmp_path / "Services" / "ceo-agent-service"))
    assert is_production_checkout(tmp_path / "Services" / "ceo-agent-service")
    assert not is_production_checkout(tmp_path / "Projects" / "ceo-agent-service")
