from pathlib import Path
from datetime import datetime, timedelta, timezone
import os
import subprocess
import json
import sqlite3
import threading

import pytest

from app.database_backup import backup_database_if_due, backup_is_complete, create_database_backup
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
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
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


@pytest.mark.parametrize("default_branch", ["master", "main"])
def test_fixture_repo_does_not_depend_on_git_default_branch(tmp_path, monkeypatch, default_branch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "init.defaultBranch")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", default_branch)

    local, remote = fixture_repo(tmp_path)

    assert git(remote, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(local, "branch", "--show-current") == "main"
    assert git(tmp_path / "updater", "branch", "--show-current") == "main"


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


def test_pinned_upgrade_merges_selected_commit_even_if_remote_moves_after_stop(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    updater_clone = tmp_path / "updater"

    def advance_remote() -> None:
        (updater_clone / "version.txt").write_text("newer\n", encoding="utf-8")
        git(updater_clone, "add", "version.txt")
        git(updater_clone, "commit", "-m", "newer")
        git(updater_clone, "push", "origin", "main")
        git(local, "fetch", "origin")

    result = RepositoryUpdater(
        local,
        StateStore(),
        database_path=tmp_path / "missing.sqlite3",
        stop=advance_remote,
        restart=lambda: None,
        health=lambda: True,
        pin_target_commit=True,
    ).execute(op)

    assert result.installed_commit == op.target_commit
    assert git(local, "rev-parse", "HEAD") == op.target_commit
    assert git(local, "rev-parse", "origin/main") != op.target_commit


def test_pinned_upgrade_accepts_main_ahead_before_repository_recheck(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    updater_clone = tmp_path / "updater"
    (updater_clone / "version.txt").write_text("newer\n", encoding="utf-8")
    git(updater_clone, "add", "version.txt")
    git(updater_clone, "commit", "-m", "newer")
    git(updater_clone, "push", "origin", "main")

    result = RepositoryUpdater(
        local,
        StateStore(),
        database_path=tmp_path / "missing.sqlite3",
        restart=lambda: None,
        health=lambda: True,
        pin_target_commit=True,
    ).execute(op)

    assert result.installed_commit == op.target_commit
    assert git(local, "rev-parse", "HEAD") == op.target_commit
    assert git(local, "rev-parse", "origin/main") != op.target_commit


def test_upgrade_stops_service_after_quiet_before_backup_and_restart(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    calls: list[str] = []

    updater = RepositoryUpdater(
        local,
        StateStore(),
        database_path=tmp_path / "missing.sqlite3",
        wait_for_quiet=lambda: calls.append("quiet"),
        stop=lambda: calls.append("stop"),
        restart=lambda: calls.append("restart"),
        health=lambda: calls.append("health") or True,
    )

    result = updater.execute(op)

    assert result.status == "succeeded"
    assert calls == ["quiet", "stop", "restart", "health"]


def test_unpinned_upgrade_installs_main_pushed_while_waiting_for_quiet(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    updater_clone = tmp_path / "updater"

    def push_while_waiting() -> None:
        (updater_clone / "version.txt").write_text("newer\n", encoding="utf-8")
        git(updater_clone, "add", "version.txt")
        git(updater_clone, "commit", "-m", "newer")
        git(updater_clone, "push", "origin", "main")

    result = RepositoryUpdater(
        local,
        StateStore(),
        database_path=tmp_path / "missing.sqlite3",
        wait_for_quiet=push_while_waiting,
        restart=lambda: None,
        health=lambda: True,
    ).execute(op)

    newest = git(updater_clone, "rev-parse", "HEAD")
    assert newest != op.target_commit
    assert result.installed_commit == newest
    assert git(local, "rev-parse", "HEAD") == newest


def test_failed_fast_forward_restarts_original_service(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    calls: list[str] = []
    updater = RepositoryUpdater(
        local,
        StateStore(),
        database_path=tmp_path / "missing.sqlite3",
        wait_for_quiet=lambda: calls.append("quiet"),
        stop=lambda: calls.append("stop"),
        restart=lambda: calls.append("restart"),
        health=lambda: calls.append("health") or True,
    )
    original_run = updater.repository._run

    def fail_merge(args, *, category, **kwargs):
        if category == "upgrade_merge":
            raise RuntimeError("simulated fast-forward refusal")
        return original_run(args, category=category, **kwargs)

    updater.repository._run = fail_merge

    with pytest.raises(UpgradeFailed):
        updater.execute(op)

    assert calls == ["quiet", "stop", "restart", "health"]


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


def test_pinned_upgrade_rolls_back_selected_commit_after_remote_advances(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    store = StateStore()

    def advance_remote() -> None:
        updater_clone = tmp_path / "updater"
        (updater_clone / "version.txt").write_text("newer\n", encoding="utf-8")
        git(updater_clone, "add", "version.txt")
        git(updater_clone, "commit", "-m", "newer")
        git(updater_clone, "push", "origin", "main")
        git(local, "fetch", "origin")

    def fail_verification() -> None:
        raise RuntimeError("verification failed")

    updater = RepositoryUpdater(
        local,
        store,
        database_path=tmp_path / "missing.sqlite3",
        stop=advance_remote,
        verification=fail_verification,
        restart=lambda: None,
        health=lambda: True,
        pin_target_commit=True,
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


def test_upgrade_takes_current_snapshot_for_each_operation(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table state (value text)")
        db.execute("insert into state values ('before')")
        db.commit()
    updater = RepositoryUpdater(local, StateStore(), database_path=db_path)
    first = updater._backup(operation(local))
    with sqlite3.connect(db_path) as db:
        db.execute("update state set value = 'current'")
    second_operation = operation(local).__class__(
        **{**operation(local).__dict__, "operation_id": "op-2"}
    )
    second = updater._backup(second_operation)

    snapshots = sorted((tmp_path / "backups").glob("*.sqlite3"))
    assert first != second
    assert snapshots == [second]
    with sqlite3.connect(second) as backup:
        assert backup.execute("select value from state").fetchone() == ("current",)


def test_upgrade_replaces_recent_daily_backup_with_current_snapshot(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table state (value text)")
        db.execute("insert into state values ('before')")
    daily = db_path.parent / "backups" / (
        f"{db_path.stem}-{datetime.now(timezone.utc).date().isoformat()}.sqlite3"
    )
    create_database_backup(db_path, daily)
    with sqlite3.connect(db_path) as db:
        db.execute("update state set value = 'after'")
    calls = []
    updater = RepositoryUpdater(
        local, StateStore(), database_path=db_path,
        stop=lambda: calls.append("stop"),
        restart=lambda: calls.append("restart"),
        health=lambda: True,
    )

    result = updater.execute(op)

    assert result.status == "succeeded"
    fresh = Path(result.backup_path)
    assert fresh != daily
    assert backup_is_complete(fresh)
    with sqlite3.connect(fresh) as backup:
        assert backup.execute("select value from state").fetchone() == ("after",)
    assert list(daily.parent.glob("*.sqlite3")) == [fresh]
    assert calls == ["stop", "restart"]
    assert git(local, "rev-parse", "HEAD") == op.target_commit


def test_daily_backup_waits_until_failed_deploy_rollback_uses_snapshot(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table state (value text)")
        db.execute("insert into state values ('before')")

    daily_started = threading.Event()
    daily_finished = threading.Event()
    completed_during_verify: list[bool] = []
    rollback_values: list[str] = []
    daily_errors: list[BaseException] = []
    worker: threading.Thread | None = None

    def publication(backup_path: Path | None):
        assert backup_path is not None

        class Release:
            def publish(self):
                with sqlite3.connect(db_path) as db:
                    db.execute("update state set value = 'migrated'")

            def verify_loaded(self):
                nonlocal worker

                def daily_backup():
                    daily_started.set()
                    try:
                        backup_database_if_due(db_path)
                    except BaseException as exc:
                        daily_errors.append(exc)
                    finally:
                        daily_finished.set()

                worker = threading.Thread(target=daily_backup)
                worker.start()
                assert daily_started.wait(2)
                completed_during_verify.append(daily_finished.wait(0.2))
                raise RuntimeError("installed database verification failed")

            def rollback(self):
                with sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True) as db:
                    rollback_values.append(db.execute("select value from state").fetchone()[0])

            def finalize(self):
                pass

        return Release()

    store = StateStore()
    updater = RepositoryUpdater(
        local, store, database_path=db_path, database_publication=publication,
        restart=lambda: None, health=lambda: True,
    )

    with pytest.raises(UpgradeFailed, match="verification"):
        updater.execute(op)

    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert not daily_errors
    assert completed_during_verify == [False]
    assert rollback_values == ["before"]
    assert json.loads(store.values["repository_upgrade_operation:v1"])["status"] == "rolled_back"


@pytest.mark.parametrize("existing", ["missing", "stale", "incomplete", "future"])
def test_upgrade_creates_backup_when_none_qualifies(tmp_path: Path, existing: str):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table state (value text)")
        db.execute("insert into state values ('before')")
    daily = db_path.parent / "backups" / (
        f"{db_path.stem}-{datetime.now(timezone.utc).date().isoformat()}.sqlite3"
    )
    if existing != "missing":
        create_database_backup(db_path, daily)
        if existing == "incomplete":
            with sqlite3.connect(daily) as backup:
                backup.execute("pragma application_id = 0")
        else:
            age = timedelta(hours=25 if existing == "stale" else -1)
            timestamp = (datetime.now(timezone.utc) - age).timestamp()
            os.utime(daily, (timestamp, timestamp))
    with sqlite3.connect(db_path) as db:
        db.execute("update state set value = 'current'")
    calls = []
    updater = RepositoryUpdater(
        local, StateStore(), database_path=db_path,
        stop=lambda: calls.append("stop"),
        restart=lambda: calls.append("restart"),
        health=lambda: True,
    )

    result = updater.execute(op)

    assert result.status == "succeeded"
    fresh = Path(result.backup_path)
    assert fresh != daily
    assert backup_is_complete(fresh)
    with sqlite3.connect(fresh) as backup:
        assert backup.execute("select value from state").fetchone() == ("current",)
    assert list(fresh.parent.glob("*.sqlite3")) == [fresh]
    assert calls == ["stop", "restart"]
    assert git(local, "rev-parse", "HEAD") == op.target_commit


def test_backup_replaces_existing_snapshots_for_its_database(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table state (value text)")
    older = tmp_path / "backups" / "worker-before-upgrade-older.sqlite3"
    create_database_backup(db_path, older)
    newer = older.with_name("worker-before-upgrade-newer.sqlite3")
    newer.write_bytes(older.read_bytes())
    unrelated = older.with_name("other-before-upgrade-newest.sqlite3")
    unrelated.write_bytes(older.read_bytes())
    now = datetime.now(timezone.utc).timestamp()
    os.utime(older, (now - 7200, now - 7200))
    os.utime(newer, (now - 3600, now - 3600))
    incomplete = older.with_name("worker-before-upgrade-incomplete.sqlite3")
    with sqlite3.connect(incomplete) as db:
        db.execute("create table state (value text)")
    updater = RepositoryUpdater(local, StateStore(), database_path=db_path)

    fresh = updater._backup(operation(local))
    assert fresh != newer
    assert backup_is_complete(fresh)
    assert list((tmp_path / "backups").glob("*.sqlite3")) == [fresh]
    assert not backup_is_complete(incomplete)


def test_backup_failure_restarts_original_service_without_updating(tmp_path: Path):
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    db_path = tmp_path / "worker.sqlite3"
    db_path.write_bytes(b"not a sqlite database")
    calls = []
    store = StateStore()
    updater = RepositoryUpdater(
        local, store, database_path=db_path,
        wait_for_quiet=lambda: calls.append("quiet"),
        stop=lambda: calls.append("stop"),
        restart=lambda: calls.append("restart"),
        health=lambda: calls.append("health") or True,
    )

    with pytest.raises(UpgradeFailed):
        updater.execute(op)

    assert git(local, "rev-parse", "HEAD") == op.original_commit
    assert calls == ["quiet", "stop", "restart", "health"]
    assert json.loads(store.values["repository_upgrade_operation:v1"])["status"] == "rolled_back"
    assert list((db_path.parent / "backups").glob("*.sqlite3")) == []


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


def test_quiet_counts_processing_current_direct_email_action(tmp_path):
    from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
    from app.email_store import EmailStore
    from app.repository_updater import in_flight_work
    from app.store import AutoReplyStore
    from tests.test_email_store import _classification

    path = tmp_path / "current-email-action-quiet.sqlite3"
    AutoReplyStore(path)
    email = EmailStore(path)
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        classification_id=91,
        message_id="quiet-current-direct-action",
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )
    email.persist_scan_result(classification, model_text="synthetic")
    action_id = email.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id
    )[0]

    claimed = email.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-22T12:00:00+00:00"
    )

    assert claimed is not None
    from app.email_publication_sources import publication_email_action_source

    with sqlite3.connect(path) as db:
        source = publication_email_action_source(db)
        assert db.execute(
            f"select count(*) from ({source}) where status='processing'"
        ).fetchone() == (1,)
    assert in_flight_work(path) == 1


def test_quiet_store_only_database_has_no_email_action_contribution(tmp_path):
    from app.repository_updater import in_flight_work
    from app.store import AutoReplyStore

    path = tmp_path / "store-only-quiet.sqlite3"
    AutoReplyStore(path)

    assert in_flight_work(path) == 0


@pytest.mark.parametrize("status,locked_at,expected", [
    ("processing", "2026-01-01 00:00:00", 1),
    ("ready_to_send", "2026-01-01 00:00:00", 1),
    ("ready_to_send", None, 0),
    ("retry", None, 0),
    ("sent", None, 0),
])
def test_quiet_counts_meeting_preparation_and_claimed_delivery(tmp_path, status, locked_at, expected):
    from app.repository_updater import in_flight_work
    from app.store import AutoReplyStore

    path = tmp_path / "meeting-quiet.sqlite3"
    store = AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        from app.task8_storage import task8_insert_parts, register_task8_functions

        register_task8_functions(db)
        parts = task8_insert_parts("meeting_alignment_jobs", {
            "legacy_key": 42, "meeting_id": "meeting", "status": status,
            "locked_at": locked_at,
        })
        db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
    if status == "processing":
        store.claim_logical_run("meeting_alignment_runs", 42)
    assert in_flight_work(path) == expected


def test_quiet_does_not_count_unclaimed_historical_meeting_processing(tmp_path):
    from app.repository_updater import in_flight_work
    from app.store import AutoReplyStore

    path = tmp_path / "historical-meeting.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        from app.task8_storage import task8_insert_parts, register_task8_functions

        register_task8_functions(db)
        parts = task8_insert_parts("meeting_alignment_jobs", {
            "legacy_key": 42, "meeting_id": "historical", "status": "processing",
            "locked_at": "2026-01-01 00:00:00",
        })
        db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
    assert in_flight_work(path) == 0


@pytest.mark.parametrize("lease,terminal,expected", [
    ("2999-01-01T00:00:00+08:00", "", 1),
    ("2000-01-01T00:00:00+00:00", "", 0),
    ("2999-01-01T00:00:00+00:00", "completed", 0),
])
def test_quiet_counts_live_dispatcher_claim_before_runtime_exists(tmp_path, lease, terminal, expected):
    from app.repository_updater import in_flight_work
    from app.store import AutoReplyStore

    path = tmp_path / "claim-quiet.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        from app.state_storage import insert_state
        from app.task8_storage import register_task8_functions, task8_insert_parts
        from app.task_lifecycle_storage import acquire_claim
        register_task8_functions(db)
        db.execute("begin")
        parts = task8_insert_parts("meeting_alignment_jobs", {
            "legacy_key": 42, "meeting_id": "claim-quiet", "status": "pending",
        })
        db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
        if terminal:
            insert_state(db, "dispatcher_claim_leases", {
                "adapter_name": "meeting", "source_id": "42",
                "lease_expires_at": lease, "terminal_at": terminal,
            })
        else:
            acquire_claim(db, adapter_name="meeting", source_id="42", owner="",
                owner_pid=0, lease_expires_at=lease, now="1999-01-01 00:00:00")
    assert in_flight_work(path) == expected


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


def test_publish_is_redone_when_another_deploy_wins_the_race(tmp_path: Path, monkeypatch):
    # 2026-10-22: a deploy with --publish-consumer-system-contracts lost the
    # race, printed "another deploy got there first" and published nothing.
    import app.deploy as deploy_module
    from types import SimpleNamespace

    local, _ = fixture_repo(tmp_path)
    executes: list[int] = []

    class RacingUpdater:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, operation):
            executes.append(1)
            if len(executes) == 1:
                git(local, "merge", "--ff-only", "origin/main")
                raise UpgradePreconditionError("repository revision changed")
            return SimpleNamespace(installed_commit=git(local, "rev-parse", "HEAD"), error="")

    monkeypatch.setattr(deploy_module, "RepositoryUpdater", RacingUpdater)
    monkeypatch.setattr("app.store.AutoReplyStore", lambda _path: StateStore())

    message = deploy_module.deploy(local, tmp_path / "db.sqlite3", publish_contracts=True)

    assert len(executes) == 2
    assert not message.startswith("another deploy got there first")


def test_deploy_target_commit_rejects_commit_outside_main_before_wait_or_stop(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module

    local, _ = fixture_repo(tmp_path)
    original = git(local, "rev-parse", "HEAD")
    git(local, "checkout", "-b", "unrelated")
    (local / "unrelated.txt").write_text("other branch\n", encoding="utf-8")
    git(local, "add", "unrelated.txt")
    git(local, "commit", "-m", "unrelated")
    unrelated = git(local, "rev-parse", "HEAD")
    git(local, "checkout", "main")
    monkeypatch.setattr(deploy_module, "wait_until_quiet", lambda _db: pytest.fail("quiet wait reached"))
    monkeypatch.setattr(deploy_module, "_default_stop", lambda: pytest.fail("service stop reached"))

    with pytest.raises(SystemExit, match="target commit is not in origin/main"):
        deploy_module.deploy(local, tmp_path / "db.sqlite3", target_commit=unrelated)

    assert git(local, "rev-parse", "HEAD") == original


def test_deploy_target_commit_can_select_older_pushed_main_commit(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module
    from app.repository_updater import UpgradeResult

    local, _ = fixture_repo(tmp_path)
    target = git(tmp_path / "updater", "rev-parse", "HEAD")
    updater_clone = tmp_path / "updater"
    (updater_clone / "version.txt").write_text("newer\n", encoding="utf-8")
    git(updater_clone, "add", "version.txt")
    git(updater_clone, "commit", "-m", "newer")
    git(updater_clone, "push", "origin", "main")
    selected: list[tuple[str, bool]] = []

    class CapturingUpdater:
        def __init__(self, *args, **kwargs):
            self.pinned = kwargs["pin_target_commit"]

        def execute_locked(self, op):
            selected.append((op.target_commit, self.pinned))
            return UpgradeResult(op.operation_id, "succeeded", installed_commit=op.target_commit)

    monkeypatch.setattr(deploy_module, "RepositoryUpdater", CapturingUpdater)
    deploy_module.deploy(local, tmp_path / "db.sqlite3", target_commit=target)

    assert selected == [(target, True)]
    assert git(local, "rev-parse", "origin/main") != target


def test_deploy_target_commit_rejects_invalid_sha_before_repository_work(tmp_path: Path):
    import app.deploy as deploy_module

    with pytest.raises(SystemExit, match="full 40-character SHA"):
        deploy_module.deploy(tmp_path / "missing", tmp_path / "db.sqlite3", target_commit="abc")


def test_deploy_target_commit_rejects_annotated_tag_object_before_lifecycle_work(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module

    local, _ = fixture_repo(tmp_path)
    target = git(tmp_path / "updater", "rev-parse", "HEAD")
    git(local, "fetch", "origin")
    git(local, "tag", "-a", "candidate", target, "-m", "candidate")
    tag_object = git(local, "rev-parse", "refs/tags/candidate")
    assert git(local, "cat-file", "-t", tag_object) == "tag"
    monkeypatch.setattr(deploy_module, "wait_until_quiet", lambda _db: pytest.fail("quiet wait reached"))
    monkeypatch.setattr(deploy_module, "_default_stop", lambda: pytest.fail("service stop reached"))

    with pytest.raises(SystemExit, match="commit object"):
        deploy_module.deploy(local, tmp_path / "db.sqlite3", target_commit=tag_object)


def test_pinned_deploy_rechecks_quiet_after_target_fetch(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module
    from app.repository_upgrade import GitRepository
    from app.repository_updater import UpgradeResult

    local, _ = fixture_repo(tmp_path)
    target = git(tmp_path / "updater", "rev-parse", "HEAD")
    active = {"count": 0}
    fetches = 0
    stops: list[int] = []
    original_fetch = GitRepository.fetch

    def fetch_with_new_work(repository, remote):
        nonlocal fetches
        original_fetch(repository, remote)
        fetches += 1
        if fetches == 2:
            active["count"] = 1

    def wait_for_quiet(_db, **_kwargs):
        if active["count"]:
            raise deploy_module.UpgradePreconditionError("service did not become idle")

    class CapturingUpdater:
        def __init__(self, *args, **kwargs):
            self.wait_for_quiet = kwargs["wait_for_quiet"]
            self.stop = kwargs["stop"]

        def execute_locked(self, op):
            self.wait_for_quiet()
            self.stop()
            return UpgradeResult(op.operation_id, "succeeded", installed_commit=op.target_commit)

    monkeypatch.setattr(GitRepository, "fetch", fetch_with_new_work)
    monkeypatch.setattr(deploy_module, "RepositoryUpdater", CapturingUpdater)
    monkeypatch.setattr(deploy_module, "wait_until_quiet", wait_for_quiet)
    monkeypatch.setattr(deploy_module, "_default_stop", lambda: stops.append(active["count"]))
    monkeypatch.setattr(deploy_module, "in_flight_work", lambda _db: active["count"], raising=False)

    with pytest.raises(SystemExit, match="service did not become idle"):
        deploy_module.deploy(local, tmp_path / "db.sqlite3", target_commit=target)

    assert fetches == 2
    assert stops == []


def test_deploy_target_commit_accepts_main_advance_during_quiet_wait(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module
    from app.repository_updater import UpgradeResult

    local, _ = fixture_repo(tmp_path)
    target = git(tmp_path / "updater", "rev-parse", "HEAD")
    original = git(local, "rev-parse", "HEAD")
    selected: list[tuple[str, bool]] = []

    def advance_during_wait(_db: Path, **_kwargs) -> None:
        updater_clone = tmp_path / "updater"
        (updater_clone / "version.txt").write_text("newer\n", encoding="utf-8")
        git(updater_clone, "add", "version.txt")
        git(updater_clone, "commit", "-m", "newer")
        git(updater_clone, "push", "origin", "main")

    monkeypatch.setattr(deploy_module, "wait_until_quiet", advance_during_wait)
    monkeypatch.setattr(deploy_module, "in_flight_work", lambda _db: 0)
    class CapturingUpdater:
        def __init__(self, *args, **kwargs):
            self.wait_for_quiet = kwargs["wait_for_quiet"]
            self.pinned = kwargs["pin_target_commit"]

        def execute_locked(self, op):
            self.wait_for_quiet()
            selected.append((op.target_commit, self.pinned))
            return UpgradeResult(op.operation_id, "succeeded", installed_commit=op.target_commit)

    monkeypatch.setattr(deploy_module, "RepositoryUpdater", CapturingUpdater)
    message = deploy_module.deploy(local, tmp_path / "db.sqlite3", target_commit=target)

    assert message == f"deployed {original[:8]} -> {target[:8]}"
    assert selected == [(target, True)]
    assert git(local, "rev-parse", "origin/main") != target
    assert git(local, "rev-parse", "HEAD") == original


def test_deploy_target_commit_rejects_main_rewrite_during_quiet_wait(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module

    local, _ = fixture_repo(tmp_path)
    target = git(tmp_path / "updater", "rev-parse", "HEAD")
    original = git(local, "rev-parse", "HEAD")
    stopped: list[str] = []

    def rewrite_during_wait(_db: Path, **_kwargs) -> None:
        git(tmp_path / "updater", "push", "--force", "origin", f"{original}:main")

    monkeypatch.setattr(deploy_module, "wait_until_quiet", rewrite_during_wait)
    monkeypatch.setattr(deploy_module, "_default_stop", lambda: stopped.append("stop"))

    with pytest.raises(SystemExit, match="target commit is not in origin/main"):
        deploy_module.deploy(local, tmp_path / "db.sqlite3", target_commit=target)

    assert stopped == []
    assert git(local, "rev-parse", "HEAD") == original


def test_deploy_cli_passes_optional_target_commit(monkeypatch, tmp_path: Path):
    import sys
    import app.deploy as deploy_module

    target = "a" * 40
    captured: list[str] = []
    monkeypatch.setattr(deploy_module, "deploy", lambda _root, _db, **kwargs: captured.append(kwargs["target_commit"]) or "ok")
    monkeypatch.setattr(sys, "argv", ["app.deploy", "--root", str(tmp_path), "--target-commit", target])

    assert deploy_module.main() == 0
    assert captured == [target]


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


def fixture_repo_with_protected_source(tmp_path: Path) -> tuple[Path, list[Path]]:
    """Like `fixture_repo`, plus tracked files under each protected source tree.

    The files must be committed, not merely present, or the pre-existing "has
    local changes" guard refuses the deploy before the lock/unlock code under
    test ever runs.
    """
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    git(tmp_path, "init", "--initial-branch=main", str(local))
    git(local, "config", "user.name", "Test User")
    git(local, "config", "user.email", "test@example.com")
    files = [
        local / "app" / "cli.py",
        local / "frontend" / "src" / "app.tsx",
        local / "tests" / "test_cli.py",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("old\n", encoding="utf-8")
        git(local, "add", str(path.relative_to(local)))
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
    return local, files


def test_lock_and_unlock_source_tree_toggle_write_permission(tmp_path: Path):
    import stat as stat_module

    from app.deploy import lock_source_tree, unlock_source_tree

    local, files = fixture_repo_with_protected_source(tmp_path)
    # Nothing outside app/, frontend/src/ and tests/ is touched.
    unprotected = local / "data" / "config.json"
    unprotected.parent.mkdir(parents=True, exist_ok=True)
    unprotected.write_text("{}\n", encoding="utf-8")

    lock_source_tree(local)

    for path in files:
        assert path.stat().st_mode & stat_module.S_IWUSR == 0
        with pytest.raises(PermissionError):
            path.write_text("edited\n", encoding="utf-8")
    assert unprotected.stat().st_mode & stat_module.S_IWUSR != 0
    unprotected.write_text("{}\n", encoding="utf-8")  # still writable

    unlock_source_tree(local)

    for path in files:
        assert path.stat().st_mode & stat_module.S_IWUSR != 0
        path.write_text("edited\n", encoding="utf-8")  # no longer raises


def test_deploy_locks_the_source_tree_after_a_successful_deploy(tmp_path: Path, monkeypatch):
    import stat as stat_module

    import app.deploy as deploy_module
    from app.repository_updater import UpgradeResult

    local, files = fixture_repo_with_protected_source(tmp_path)

    class SucceedingUpdater:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, operation):
            return UpgradeResult(
                operation_id=operation.operation_id,
                status="succeeded",
                installed_commit=git(local, "rev-parse", "origin/main"),
            )

    monkeypatch.setattr(deploy_module, "RepositoryUpdater", SucceedingUpdater)
    monkeypatch.setattr("app.store.AutoReplyStore", lambda _path: StateStore())

    deploy_module.deploy(local, tmp_path / "db.sqlite3")

    for path in files:
        assert path.stat().st_mode & stat_module.S_IWUSR == 0


def test_deploy_can_explicitly_skip_only_the_quiet_wait(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module
    from app.repository_updater import UpgradeResult

    local, _files = fixture_repo_with_protected_source(tmp_path)
    events: list[str] = []

    class CapturingUpdater:
        def __init__(self, *args, **kwargs):
            self.wait_for_quiet = kwargs["wait_for_quiet"]
            self.stop = kwargs["stop"]

        def execute(self, operation):
            self.wait_for_quiet()
            self.stop()
            events.append("service-stopped")
            events.append("updater-continued")
            return UpgradeResult(
                operation_id=operation.operation_id,
                status="succeeded",
                installed_commit=git(local, "rev-parse", "origin/main"),
            )

    monkeypatch.setattr(deploy_module, "RepositoryUpdater", CapturingUpdater)
    monkeypatch.setattr("app.store.AutoReplyStore", lambda _path: StateStore())
    monkeypatch.setattr(deploy_module, "_default_stop", lambda: events.append("stop"))

    deploy_module.deploy(
        local, tmp_path / "db.sqlite3", skip_quiet_wait=True
    )

    assert events == ["stop", "service-stopped", "updater-continued"]


def test_deploy_relocks_the_source_tree_even_when_the_race_is_lost(tmp_path: Path, monkeypatch):
    import stat as stat_module

    import app.deploy as deploy_module

    local, files = fixture_repo_with_protected_source(tmp_path)

    class RacingUpdater:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, operation):
            git(local, "merge", "--ff-only", "origin/main")
            raise UpgradePreconditionError("repository revision changed")

    monkeypatch.setattr(deploy_module, "RepositoryUpdater", RacingUpdater)
    monkeypatch.setattr("app.store.AutoReplyStore", lambda _path: StateStore())

    deploy_module.deploy(local, tmp_path / "db.sqlite3")

    for path in files:
        assert path.stat().st_mode & stat_module.S_IWUSR == 0


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


def test_restart_only_waits_for_quiet_then_restarts_and_checks_health(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module

    root, _remote = fixture_repo(tmp_path)
    monkeypatch.setattr(deploy_module, "service_root", lambda: root)
    events: list[str] = []
    monkeypatch.setattr(deploy_module, "wait_until_quiet", lambda _db: events.append("quiet"))
    monkeypatch.setattr(deploy_module, "wait_for_health", lambda: events.append("health") or True)

    assert deploy_module.restart_only(tmp_path / "db", restart=lambda: events.append("restart")) == "restarted"
    assert events == ["quiet", "restart", "health"]

    events.clear()
    assert deploy_module.restart_only(
        tmp_path / "db", restart=lambda: events.append("restart"),
        skip_quiet_wait=True,
    ) == "restarted"
    assert events == ["restart", "health"]

    monkeypatch.setattr(deploy_module, "wait_for_health", lambda: False)
    with pytest.raises(SystemExit, match="did not become healthy"):
        deploy_module.restart_only(tmp_path / "db", restart=lambda: None)


def test_default_restart_bootstraps_when_launchd_label_is_unloaded(monkeypatch):
    from types import SimpleNamespace

    import app.repository_updater as updater_module

    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "kickstart":
            return SimpleNamespace(returncode=113, stdout="", stderr="service not found")
        if args[1] == "print":
            return SimpleNamespace(
                returncode=113, stdout="", stderr='Could not find service "x"'
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(updater_module.subprocess, "run", run)
    updater_module._default_restart()

    assert [call[1] for call in calls] == ["kickstart", "print", "bootstrap"]


def test_default_stop_waits_until_launchd_finishes_removing_service(monkeypatch):
    from types import SimpleNamespace

    import app.repository_updater as updater_module

    calls: list[list[str]] = []
    states = iter((
        SimpleNamespace(returncode=0, stdout="state = running", stderr=""),
        SimpleNamespace(returncode=0, stdout="state = inactive (removing)", stderr=""),
        SimpleNamespace(returncode=113, stdout="", stderr='Could not find service "x"'),
    ))

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "bootout":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return next(states)

    sleeps: list[float] = []
    monkeypatch.setattr(updater_module.subprocess, "run", run)
    monkeypatch.setattr(updater_module.time, "sleep", sleeps.append)

    updater_module._default_stop()

    assert [call[1] for call in calls] == ["print", "bootout", "print", "print"]
    assert sleeps == [0.1]


def test_default_stop_waits_for_known_service_child_after_label_disappears(monkeypatch):
    from types import SimpleNamespace

    import app.repository_updater as updater_module

    events: list[str] = []
    child_running = True

    def run(args, **kwargs):
        if args[:2] == ["launchctl", "print"]:
            if "bootout" not in events:
                return SimpleNamespace(returncode=0, stdout="state = running\n\tpid = 24680\n", stderr="")
            return SimpleNamespace(returncode=113, stdout="", stderr='Could not find service "x"')
        if args[:2] == ["launchctl", "bootout"]:
            events.append("bootout")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["ps", "-axo"]:
            return SimpleNamespace(returncode=0, stdout="24680 1\n24681 24680\n", stderr="")
        if args[0] == "ps" and args[1] == "-p":
            return SimpleNamespace(
                returncode=0 if child_running else 1,
                stdout="24681\n" if child_running else "",
                stderr="",
            )
        raise AssertionError(args)

    def sleep(_seconds):
        nonlocal child_running
        events.append("waited_for_child")
        child_running = False
        events.append("child_exited")

    monkeypatch.setattr(updater_module.subprocess, "run", run)
    monkeypatch.setattr(updater_module.time, "sleep", sleep)

    updater_module._default_stop()
    events.append("schema_publication")

    assert events.index("child_exited") < events.index("schema_publication")
