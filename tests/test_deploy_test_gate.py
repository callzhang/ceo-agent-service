"""`python -m app.deploy` is refused unless the whole suite passes at the target."""

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.deploy import TEST_GATE_ARGS, TEST_GATE_MANIFEST, require_green_suite
from tests.test_repository_updater import fixture_repo


@pytest.fixture(autouse=True)
def _gate_work_directories_stay_inside_the_test(tmp_path, monkeypatch):
    """The gate keeps its directory when it refuses; do not leave those in the system temp."""
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _runner(*, pytest_code=0, pytest_output="", checkout_code=0, timeout=False):
    calls = []

    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        if command[0] == "git":
            if command[1] == "clone" and not checkout_code:
                clone = Path(command[-1])
                (clone / "tests").mkdir(parents=True)
                (clone / TEST_GATE_MANIFEST).write_text("tests/test_a.py\ntests/test_b.py\n")
            return SimpleNamespace(returncode=checkout_code, stderr=b"checkout failed" if checkout_code else b"")
        if timeout:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        kwargs["stdout"].write(pytest_output.encode())
        return SimpleNamespace(returncode=pytest_code)

    return run, calls


def test_gate_passes_and_leaves_nothing_behind(tmp_path: Path):
    run, calls = _runner(pytest_output="1 passed in 0.1s\n")

    require_green_suite(tmp_path / "root", "a" * 40, run=run)

    suite = calls[-1]
    assert suite[0][1:] == [*TEST_GATE_ARGS, "tests/test_a.py", "tests/test_b.py"]
    assert "-n" not in suite[0]
    workdir = suite[1]["cwd"].parent
    assert not workdir.exists()


def test_gate_runs_in_a_clean_environment_outside_the_production_checkout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CEO_WORKER_DB", "/live/service.sqlite3")
    monkeypatch.setenv("CEO_SERVICE_ROOT", "/live/root")
    run, calls = _runner()

    require_green_suite(tmp_path / "root", "a" * 40, run=run)

    env = calls[-1][1]["env"]
    assert not [key for key in env if key.startswith("CEO_")]
    assert calls[0][0][:5] == ["git", "clone", "--quiet", "--shared", "--no-checkout"]
    assert str(tmp_path / "root") in calls[0][0]
    assert calls[-1][1]["cwd"] != tmp_path / "root"


def test_failing_suite_refuses_the_deploy_and_keeps_the_log(tmp_path: Path):
    run, calls = _runner(pytest_code=1, pytest_output="FAILED tests/x.py::t\n3 failed, 5 passed in 1.0s\n")

    with pytest.raises(SystemExit) as refused:
        require_green_suite(tmp_path / "root", "b" * 40, run=run)

    message = str(refused.value)
    assert "3 failed, 5 passed" in message and "nothing was deployed" in message
    log = Path(message.rsplit("Log: ", 1)[1])
    assert "FAILED tests/x.py::t" in log.read_text()
    shutil.rmtree(log.parent)


def test_suite_that_does_not_finish_refuses_the_deploy(tmp_path: Path):
    run, _ = _runner(timeout=True)

    with pytest.raises(SystemExit, match="did not finish.*nothing was deployed"):
        require_green_suite(tmp_path / "root", "c" * 40, run=run)


def test_checkout_failure_refuses_the_deploy(tmp_path: Path):
    run, _ = _runner(checkout_code=128)

    with pytest.raises(SystemExit, match="could not check out.*nothing was deployed"):
        require_green_suite(tmp_path / "root", "d" * 40, run=run)


def test_deploy_runs_the_gate_before_anything_can_be_stopped_or_changed(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module

    local, _ = fixture_repo(tmp_path)
    seen = []

    def refusing_gate(root, commit, **kwargs):
        seen.append(("gate", commit))
        raise SystemExit("the test suite fails; nothing was deployed")

    class MustNotStart:
        def __init__(self, *args, **kwargs):
            seen.append(("updater",))

    monkeypatch.setattr(deploy_module, "TEST_GATE_ENABLED", True)
    monkeypatch.setattr(deploy_module, "require_green_suite", refusing_gate)
    monkeypatch.setattr(deploy_module, "RepositoryUpdater", MustNotStart)

    with pytest.raises(SystemExit, match="nothing was deployed"):
        deploy_module.deploy(local, tmp_path / "db.sqlite3")

    assert [entry[0] for entry in seen] == ["gate"]


def test_deploy_installs_the_commit_the_gate_tested_even_if_main_moves(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module
    from app.repository_updater import UpgradeResult
    from tests.test_repository_updater import git

    local, _ = fixture_repo(tmp_path)
    updater_clone = tmp_path / "updater"
    order = []
    pins = []

    def gate_while_main_moves(root, commit, **kwargs):
        order.append(("gate", commit))
        (updater_clone / "version.txt").write_text("newer than the tested commit\n", encoding="utf-8")
        git(updater_clone, "add", "version.txt")
        git(updater_clone, "commit", "-m", "pushed while the gate was running")
        git(updater_clone, "push", "origin", "main")

    class Recording:
        def __init__(self, *args, **kwargs):
            pins.append(kwargs["pin_target_commit"])

        def execute_locked(self, op):
            order.append(("install", op.target_commit))
            return UpgradeResult(op.operation_id, "succeeded", installed_commit=op.target_commit)

    monkeypatch.setattr(deploy_module, "TEST_GATE_ENABLED", True)
    monkeypatch.setattr(deploy_module, "require_green_suite", gate_while_main_moves)
    monkeypatch.setattr(deploy_module, "RepositoryUpdater", Recording)

    deploy_module.deploy(local, tmp_path / "db.sqlite3")

    assert [kind for kind, _ in order] == ["gate", "install"]
    assert order[0][1] == order[1][1]
    assert pins == [True]
    assert git(updater_clone, "rev-parse", "HEAD") != order[0][1]   # main really moved


def test_suspended_gate_does_not_run(tmp_path: Path, monkeypatch):
    import app.deploy as deploy_module
    from app.repository_updater import UpgradeResult

    local, _ = fixture_repo(tmp_path)
    calls = []

    class Recording:
        def __init__(self, *args, **kwargs):
            pass

        def execute_locked(self, op):
            return UpgradeResult(op.operation_id, "succeeded", installed_commit=op.target_commit)

    monkeypatch.setattr(deploy_module, "TEST_GATE_ENABLED", False)
    monkeypatch.setattr(deploy_module, "require_green_suite", lambda root, commit, **kwargs: calls.append(commit))
    monkeypatch.setattr(deploy_module, "RepositoryUpdater", Recording)

    deploy_module.deploy(local, tmp_path / "db.sqlite3")

    assert calls == []


def test_a_missing_manifest_refuses_the_deploy(tmp_path: Path):
    def run(command, **kwargs):
        return SimpleNamespace(returncode=0, stderr=b"")

    with pytest.raises(SystemExit, match="gate_files.txt is missing or empty.*nothing was deployed"):
        require_green_suite(tmp_path / "root", "e" * 40, run=run)


def test_the_gate_is_on_and_the_run_stops_by_itself_after_ten_minutes():
    # Derek, 2026-10-25. The process ends itself at 10 minutes of real time
    # (tests/conftest.py); the deploy only adds a small backstop for a hung interpreter.
    import app.deploy as deploy_module
    from tests.conftest import MEMORY_LIMIT

    assert deploy_module.TEST_GATE_ENABLED is True
    assert MEMORY_LIMIT.time_limit_seconds == 10 * 60
    assert 10 * 60 < deploy_module.TEST_GATE_TIMEOUT_SECONDS <= 12 * 60


def test_a_passed_gate_is_remembered_and_reused_for_the_same_commit(tmp_path: Path):
    from app.deploy import gate_pass_record

    database_path = tmp_path / "service" / "auto-reply.sqlite3"
    database_path.parent.mkdir()
    run, calls = _runner(pytest_output="7 passed in 1.0s\n")

    note = require_green_suite(tmp_path / "root", "a" * 40, run=run, database_path=database_path)

    assert note.startswith("gate passed at aaaaaaaa")
    record = gate_pass_record(database_path, "a" * 40)
    assert record.is_file()
    assert record.parent == database_path.parent / "deploy-gate-passed"
    pytest_runs = len([call for call in calls if call[0][0] != "git"])
    assert pytest_runs == 1

    note = require_green_suite(tmp_path / "root", "a" * 40, run=run, database_path=database_path)

    assert "already passed" in note and "not re-run" in note
    assert len([call for call in calls if call[0][0] != "git"]) == pytest_runs
    # A different commit is gated on its own.
    require_green_suite(tmp_path / "root", "b" * 40, run=run, database_path=database_path)
    assert len([call for call in calls if call[0][0] != "git"]) == pytest_runs + 1


def test_a_refused_or_stopped_gate_is_never_remembered(tmp_path: Path):
    from app.deploy import gate_pass_record

    database_path = tmp_path / "service" / "auto-reply.sqlite3"
    database_path.parent.mkdir()
    failing, _ = _runner(pytest_code=1, pytest_output="1 failed in 1.0s\n")
    with pytest.raises(SystemExit):
        require_green_suite(tmp_path / "root", "c" * 40, run=failing, database_path=database_path)
    stopped, _ = _runner(timeout=True)
    with pytest.raises(SystemExit):
        require_green_suite(tmp_path / "root", "c" * 40, run=stopped, database_path=database_path)
    assert not gate_pass_record(database_path, "c" * 40).exists()

    passing, calls = _runner(pytest_output="1 passed in 1.0s\n")
    require_green_suite(tmp_path / "root", "c" * 40, run=passing, database_path=database_path)
    assert len([call for call in calls if call[0][0] != "git"]) == 1
    for log in (tmp_path).glob("deploy-test-gate.*"):
        shutil.rmtree(log, ignore_errors=True)


def test_without_a_database_path_the_gate_runs_every_time(tmp_path: Path):
    run, calls = _runner(pytest_output="1 passed in 1.0s\n")
    require_green_suite(tmp_path / "root", "d" * 40, run=run)
    require_green_suite(tmp_path / "root", "d" * 40, run=run)
    assert len([call for call in calls if call[0][0] != "git"]) == 2


def test_deploy_gates_while_holding_the_repository_mutex(tmp_path: Path, monkeypatch):
    """Derek, 2026-10-28: several sessions deployed at once and their gates ran in
    parallel; seven of nine runs that day hit the ten-minute limit. The gate now
    runs under the same lock as the install, so deploys queue instead of racing."""
    import fcntl
    import os

    import app.deploy as deploy_module
    from app.repository_updater import UpgradeResult
    from app.repository_upgrade import GitRepository

    local, _ = fixture_repo(tmp_path)
    lock_path = GitRepository(local).lock_path
    observed = []

    def gate_checks_lock(root, commit, **kwargs):
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                observed.append("gate ran under the mutex")
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                observed.append("gate ran without the mutex")
        finally:
            os.close(descriptor)
        observed.append(("database_path", kwargs.get("database_path")))

    class Recording:
        def __init__(self, *args, **kwargs):
            observed.append("updater built")

        def execute_locked(self, op):
            return UpgradeResult(op.operation_id, "succeeded", installed_commit=op.target_commit)

    monkeypatch.setattr(deploy_module, "TEST_GATE_ENABLED", True)
    monkeypatch.setattr(deploy_module, "require_green_suite", gate_checks_lock)
    monkeypatch.setattr(deploy_module, "RepositoryUpdater", Recording)

    deploy_module.deploy(local, tmp_path / "db.sqlite3")

    assert observed == [
        "gate ran under the mutex",
        ("database_path", tmp_path / "db.sqlite3"),
        "updater built",
    ]


def test_deploy_does_not_gate_when_another_deploy_landed_while_it_waited_for_the_lock(tmp_path: Path, monkeypatch):
    from contextlib import contextmanager

    import app.deploy as deploy_module
    from app.repository_upgrade import GitRepository
    from tests.test_repository_updater import git

    local, _ = fixture_repo(tmp_path)
    gated = []

    @contextmanager
    def mutex_during_which_someone_else_deploys(self):
        # The other session installed origin/main into production before we got the lock.
        git(local, "merge", "--ff-only", "origin/main")
        yield

    class MustNotBuild:
        def __init__(self, *args, **kwargs):
            raise AssertionError("no install after another deploy landed")

    monkeypatch.setattr(deploy_module, "TEST_GATE_ENABLED", True)
    monkeypatch.setattr(deploy_module, "require_green_suite", lambda root, commit, **kwargs: gated.append(commit))
    monkeypatch.setattr(deploy_module, "RepositoryUpdater", MustNotBuild)
    monkeypatch.setattr(GitRepository, "mutex", mutex_during_which_someone_else_deploys)

    message = deploy_module.deploy(local, tmp_path / "db.sqlite3")

    assert message.startswith("another deploy got there first")
    assert gated == []
