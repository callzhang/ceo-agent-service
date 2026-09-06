import subprocess

import pytest

from app.wechat.permission_onboarding import (
    FULL_DISK_ACCESS_SETTINGS_URL,
    PermissionLaunchResult,
    PermissionOnboardingError,
    open_full_disk_access_settings,
    restart_reader_and_wait,
)
from app.wechat.reader_ipc import ReaderIpcError


class CommandResult:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


def test_permission_launch_result_is_frozen():
    result = PermissionLaunchResult("CEO WeChat Reader", "/tmp/reader.app", True)
    with pytest.raises(AttributeError):
        result.app_name = "other"


def _reader_files(tmp_path):
    app_path = tmp_path / "CEO WeChat Reader.app"
    app_path.mkdir()
    launch_agent = tmp_path / "com.stardust.ceo-agent.wechat-reader.plist"
    launch_agent.write_text("plist")
    return app_path, launch_agent


def test_open_settings_rejects_missing_reader_app(tmp_path):
    launch_agent = tmp_path / "reader.plist"
    launch_agent.write_text("plist")

    def run_command(*args, **kwargs):
        raise AssertionError("open must not run when reader app is missing")

    with pytest.raises(PermissionOnboardingError, match="reader app"):
        open_full_disk_access_settings(
            reader_app=tmp_path / "missing.app", launch_agent=launch_agent,
            run_command=run_command,
        )


def test_open_settings_rejects_missing_launch_agent(tmp_path):
    app_path = tmp_path / "CEO WeChat Reader.app"
    app_path.mkdir()

    def run_command(*args, **kwargs):
        raise AssertionError("open must not run when launch agent is missing")

    with pytest.raises(PermissionOnboardingError, match="launch agent"):
        open_full_disk_access_settings(
            reader_app=app_path, launch_agent=tmp_path / "missing.plist",
            run_command=run_command,
        )


def test_open_settings_runs_exact_open_command_and_returns_result(tmp_path):
    app_path, launch_agent = _reader_files(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CommandResult()

    result = open_full_disk_access_settings(
        reader_app=app_path, launch_agent=launch_agent, run_command=run
    )

    assert calls == [(
        ["/usr/bin/open", FULL_DISK_ACCESS_SETTINGS_URL],
        {"capture_output": True, "text": True, "timeout": 10, "check": False},
    )]
    assert result.app_name == "CEO WeChat Reader"
    assert result.app_path == str(app_path)
    assert result.settings_opened is True


def test_open_settings_reports_stderr_on_command_failure(tmp_path):
    app_path, launch_agent = _reader_files(tmp_path)

    def run(command, **kwargs):
        return CommandResult(returncode=23, stderr="unable to open settings")

    with pytest.raises(PermissionOnboardingError, match="unable to open settings"):
        open_full_disk_access_settings(
            reader_app=app_path, launch_agent=launch_agent, run_command=run
        )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired(["/usr/bin/open"], 10), "timed out"),
        (OSError("cannot execute /Users/test/private-tool"), "could not start"),
    ],
)
def test_open_settings_normalizes_command_runner_errors(tmp_path, error, message):
    app_path, launch_agent = _reader_files(tmp_path)

    def run(command, **kwargs):
        raise error

    with pytest.raises(PermissionOnboardingError, match=message) as caught:
        open_full_disk_access_settings(
            reader_app=app_path,
            launch_agent=launch_agent,
            run_command=run,
        )

    assert "/Users/test" not in str(caught.value)


def test_restart_reader_runs_exact_launchctl_command_and_waits_for_ready(tmp_path):
    del tmp_path
    calls = []
    health_results = [{"status": "ready"}]
    now = iter([100.0, 100.0, 100.0])

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CommandResult()

    class Reader:
        def health(self, **kwargs):
            return health_results.pop(0)

    result = restart_reader_and_wait(
        Reader(), uid=501, run_command=run, monotonic=lambda: next(now), pause=lambda _: None
    )

    assert result == {"status": "ready"}
    assert calls == [(
        ["/bin/launchctl", "kickstart", "-k", "gui/501/com.stardust.ceo-agent.wechat-reader"],
        {"capture_output": True, "text": True, "timeout": 10, "check": False},
    )]


def test_restart_reader_retries_transient_reader_ipc_error(tmp_path):
    del tmp_path
    health_calls = 0
    pauses = []
    clock = iter([0.0, 0.0, 0.1, 0.1, 0.1])

    def run(command, **kwargs):
        return CommandResult()

    class Reader:
        def health(self, **kwargs):
            nonlocal health_calls
            health_calls += 1
            if health_calls == 1:
                raise ReaderIpcError("starting")
            return {"status": "ready"}

    restart_reader_and_wait(
        Reader(), uid=501, run_command=run, monotonic=lambda: next(clock), pause=pauses.append
    )

    assert health_calls == 2
    assert pauses == [0.2]


def test_restart_reader_rejects_non_ready_health_result(tmp_path):
    del tmp_path

    def run(command, **kwargs):
        return CommandResult()

    class Reader:
        def health(self, **kwargs):
            return {"status": "blocked"}

    clock = iter([0.0, 0.0, 20.0])
    with pytest.raises(PermissionOnboardingError, match="did not become ready"):
        restart_reader_and_wait(
            Reader(), uid=501, run_command=run, monotonic=lambda: next(clock), pause=lambda _: None
        )


def test_restart_reader_reports_launchctl_failure(tmp_path):
    del tmp_path

    def run(command, **kwargs):
        return CommandResult(returncode=1, stderr="kickstart denied")

    class Reader:
        def health(self, **kwargs):
            raise AssertionError("health must not be called")

    def monotonic():
        raise AssertionError("clock must not run when launchctl fails")

    def pause(_seconds):
        raise AssertionError("pause must not run when launchctl fails")

    with pytest.raises(PermissionOnboardingError, match="kickstart denied"):
        restart_reader_and_wait(
            Reader(), uid=501, run_command=run, monotonic=monotonic, pause=pause
        )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired(["/bin/launchctl"], 10), "timed out"),
        (OSError("cannot execute /Users/test/private-tool"), "could not start"),
    ],
)
def test_restart_reader_normalizes_command_runner_errors(error, message):
    def run(command, **kwargs):
        raise error

    class Reader:
        def health(self, **kwargs):
            raise AssertionError("health must not run after launchctl error")

    with pytest.raises(PermissionOnboardingError, match=message) as caught:
        restart_reader_and_wait(Reader(), uid=501, run_command=run)

    assert "/Users/test" not in str(caught.value)


def test_restart_reader_has_bounded_timeout(tmp_path):
    del tmp_path
    pauses = []
    current = [0.0]

    def run(command, **kwargs):
        return CommandResult()

    class Reader:
        def health(self, **kwargs):
            return {"status": "starting"}

    def monotonic():
        value = current[0]
        current[0] += 0.2
        return value

    with pytest.raises(PermissionOnboardingError, match="did not become ready"):
        restart_reader_and_wait(
            Reader(), uid=501, run_command=run, monotonic=monotonic,
            pause=pauses.append, timeout_seconds=0.5,
        )

    assert pauses == [0.2]


def test_restart_reader_health_cannot_return_ready_after_total_deadline(tmp_path):
    del tmp_path
    health_timeouts = []
    current = [0.0]

    def run(command, **kwargs):
        return CommandResult()

    class Reader:
        def health(self, *, timeout_seconds):
            health_timeouts.append(timeout_seconds)
            current[0] = 30.0
            return {"status": "ready"}

    with pytest.raises(PermissionOnboardingError, match="did not become ready"):
        restart_reader_and_wait(
            Reader(), uid=501, run_command=run,
            monotonic=lambda: current[0], pause=lambda _: None,
            timeout_seconds=15.0,
        )

    assert health_timeouts == [15.0]
