from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from app.quality.cli import (
    CONFIG_ERROR,
    INFRASTRUCTURE_UNAVAILABLE,
    PASS,
    QUALITY_FAILURE,
    InfrastructureUnavailable,
    _baseline_failed_ids,
    _emit,
    _junit,
    _launchd_label,
    _launchd_pid,
    _live_outputs,
    _release_check,
    _restart_launchd,
    _run_gate,
    _wait_for_service_quality,
    entrypoint,
    main,
)
from app.quality.database import DatabaseCheckResult
from app.quality.evaluator import load_cases
from app.quality.models import ComponentHealth, QualitySnapshot
from app.quality.release import CanaryResult
from app.store import AutoReplyStore


def _snapshot(*, commit: str = "commit-a", pid: int = 22, ready: bool = True):
    return QualitySnapshot(
        commit=commit,
        pid=pid,
        schema_version=1,
        components=(ComponentHealth(component="consumer", status="ready"),),
        backlog={
            "failed": 0,
            "processing": 0,
            "unknown": 0,
            "failed_actions": 0,
            "unknown_actions": 0,
        },
        oldest_queue_age_seconds=0,
        failed_actions=0,
        unknown_actions=0,
        slo_status="pass" if ready else "warn",
        ready=ready,
    )


def test_snapshot_json_command(tmp_path, capsys) -> None:
    code = main(["snapshot", "--db", str(tmp_path / "service.sqlite3"), "--format", "json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == PASS
    assert payload["schema_version"] >= 1


def test_eval_missing_cases_is_configuration_error(tmp_path, capsys) -> None:
    code = main(
        [
            "eval",
            "--suite",
            "protocol",
            "--cases",
            str(tmp_path / "missing.jsonl"),
            "--format",
            "json",
        ]
    )

    assert code == CONFIG_ERROR
    assert json.loads(capsys.readouterr().out)["status"] == "configuration_error"


def test_emit_supports_text_json_and_junit(capsys) -> None:
    passed = {"status": "passed", "suite": "safety", "summary": "safe"}
    failed = {"status": "failed", "command": "check", "detail": "synthetic"}

    assert 'failures="0"' in _junit(passed)
    assert '<failure message="failed">' in _junit(failed)
    _emit(passed, "text")
    _emit(passed, "json")
    _emit(failed, "junit")

    output = capsys.readouterr().out
    assert "passed: safe" in output
    assert '"suite": "safety"' in output
    assert "testsuite" in output


def test_run_gate_covers_success_failure_and_infrastructure(monkeypatch) -> None:
    calls = []

    def successful(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("app.quality.cli.subprocess.run", successful)
    code, payload = _run_gate("full")
    assert code == PASS
    assert payload["status"] == "passed"
    assert len(calls) == 9

    attempts = 0

    def fail_second(command, **kwargs):
        nonlocal attempts
        attempts += 1
        return subprocess.CompletedProcess(command, 1 if attempts == 2 else 0)

    monkeypatch.setattr("app.quality.cli.subprocess.run", fail_second)
    code, payload = _run_gate("pr")
    assert code == QUALITY_FAILURE
    assert len(payload["checks"]) == 2

    def unavailable(*args, **kwargs):
        raise OSError("synthetic")

    monkeypatch.setattr("app.quality.cli.subprocess.run", unavailable)
    code, payload = _run_gate("release")
    assert code == INFRASTRUCTURE_UNAVAILABLE
    assert payload["summary"] == "OSError"


def test_live_outputs_enforces_platform_trust_event_and_commit(monkeypatch) -> None:
    cases = load_cases(Path("quality/cases/golden.jsonl"))[:1]
    monkeypatch.setattr("app.quality.cli.sys.platform", "linux")
    with pytest.raises(InfrastructureUnavailable, match="trusted Mac"):
        _live_outputs(cases, expected_commit="commit-a")

    monkeypatch.setattr("app.quality.cli.sys.platform", "darwin")
    monkeypatch.delenv("CEO_QUALITY_TRUSTED", raising=False)
    with pytest.raises(ValueError, match="TRUSTED"):
        _live_outputs(cases, expected_commit="commit-a")

    monkeypatch.setenv("CEO_QUALITY_TRUSTED", "1")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    with pytest.raises(ValueError, match="pull request"):
        _live_outputs(cases, expected_commit="commit-a")

    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setattr(
        "app.quality.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="commit-a\n"
        ),
    )
    with pytest.raises(ValueError, match="does not match"):
        _live_outputs(cases, expected_commit="commit-b")
    with pytest.raises(ValueError, match="LIVE_EVAL_COMMAND"):
        _live_outputs(cases, expected_commit="commit-a")


def test_live_outputs_replaces_recordings_and_validates_output(monkeypatch) -> None:
    cases = load_cases(Path("quality/cases/golden.jsonl"))[:1]
    case = cases[0]
    monkeypatch.setattr("app.quality.cli.sys.platform", "darwin")
    monkeypatch.setenv("CEO_QUALITY_TRUSTED", "1")
    monkeypatch.setenv("CEO_QUALITY_LIVE_EVAL_COMMAND", "quality-model --jsonl")
    output = json.dumps(
        {"id": case.id, "recorded_output": case.recorded_output},
        ensure_ascii=False,
    )
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="commit-a\n"),
            subprocess.CompletedProcess([], 0, stdout=output + "\n"),
        ]
    )
    monkeypatch.setattr(
        "app.quality.cli.subprocess.run", lambda *args, **kwargs: next(responses)
    )

    evaluated = _live_outputs(cases, expected_commit="commit-a")

    assert evaluated[0].recorded_output == case.recorded_output


@pytest.mark.parametrize(
    ("stdout", "returncode", "message"),
    [
        ("not-json\n", 0, "invalid live evaluator output"),
        ('{"id":"case"}\n', 0, "invalid live evaluator output"),
        ("", 0, "exactly one output"),
        ("", 2, "non-zero status"),
    ],
)
def test_live_outputs_rejects_bad_evaluator_results(
    monkeypatch, stdout, returncode, message
) -> None:
    cases = load_cases(Path("quality/cases/golden.jsonl"))[:1]
    monkeypatch.setattr("app.quality.cli.sys.platform", "darwin")
    monkeypatch.setenv("CEO_QUALITY_TRUSTED", "1")
    monkeypatch.setenv("CEO_QUALITY_LIVE_EVAL_COMMAND", "quality-model")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="commit-a\n"),
            subprocess.CompletedProcess([], returncode, stdout=stdout),
        ]
    )
    monkeypatch.setattr(
        "app.quality.cli.subprocess.run", lambda *args, **kwargs: next(responses)
    )

    expected = InfrastructureUnavailable if returncode else ValueError
    with pytest.raises(expected, match=message):
        _live_outputs(cases, expected_commit="commit-a")


def test_live_outputs_maps_process_errors_to_infrastructure(monkeypatch) -> None:
    cases = load_cases(Path("quality/cases/golden.jsonl"))[:1]
    monkeypatch.setattr("app.quality.cli.sys.platform", "darwin")
    monkeypatch.setenv("CEO_QUALITY_TRUSTED", "1")
    monkeypatch.setenv("CEO_QUALITY_LIVE_EVAL_COMMAND", "quality-model")
    calls = 0

    def fail_model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess([], 0, stdout="commit-a\n")
        raise subprocess.TimeoutExpired("quality-model", 1800)

    monkeypatch.setattr("app.quality.cli.subprocess.run", fail_model)
    with pytest.raises(InfrastructureUnavailable, match="TimeoutExpired"):
        _live_outputs(cases, expected_commit="commit-a")


def test_baseline_failure_ids_are_validated(tmp_path) -> None:
    assert _baseline_failed_ids(None) == set()
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "failures": [
                    {"case_id": "known"},
                    {"detail": "ignored"},
                    "ignored",
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _baseline_failed_ids(baseline) == {"known"}
    baseline.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="failures list"):
        _baseline_failed_ids(baseline)


class _ReleaseManager:
    instances = []

    def __init__(self, *, repo, release_root):
        self.repo = repo
        self.release_root = release_root
        self.activated = []
        self.rolled_back = []
        self.instances.append(self)

    def build(self, commit):
        self.commit = commit
        return self.release_root / "releases" / commit

    def activate(self, release):
        self.activated.append(release)
        return self.release_root / "releases" / "previous"

    def rollback(self, previous):
        self.rolled_back.append(previous)


def _release_args(tmp_path, profile, *, restart=False):
    return argparse.Namespace(
        sandbox_profile=str(profile),
        db=tmp_path / "service.sqlite3",
        release_root=tmp_path / "runtime",
        commit="commit-a",
        restart=restart,
    )


def _sandbox(tmp_path):
    profile = tmp_path / "sandbox.json"
    profile.write_text(
        json.dumps(
            {
                "name": "test",
                "live_enabled": True,
                "connectors": {
                    "synthetic": {
                        "enabled": True,
                        "test_target_ref": "synthetic-target",
                        "allowlist": ["synthetic-target"],
                        "canary_command": ["true"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return profile


def _clean_git_run(command, **kwargs):
    stdout = "" if command[:2] == ["git", "status"] else "commit-a\n"
    return subprocess.CompletedProcess(command, 0, stdout=stdout)


def test_release_check_handles_missing_profile_gate_and_database(
    tmp_path, monkeypatch
) -> None:
    args = _release_args(tmp_path, tmp_path / "missing.json")
    assert _release_check(args)[0] == CONFIG_ERROR

    profile = _sandbox(tmp_path)
    args = _release_args(tmp_path, profile)

    def mismatched_git(command, **kwargs):
        stdout = "commit-b\n" if "--verify" in command else "commit-a\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr("app.quality.cli.subprocess.run", mismatched_git)
    assert _release_check(args)[0] == CONFIG_ERROR

    def dirty_git(command, **kwargs):
        stdout = " M app/quality/cli.py\n" if "status" in command else "commit-a\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr("app.quality.cli.subprocess.run", dirty_git)
    assert _release_check(args)[0] == CONFIG_ERROR

    monkeypatch.setattr("app.quality.cli.subprocess.run", _clean_git_run)
    monkeypatch.setattr(
        "app.quality.cli._run_gate",
        lambda tier: (QUALITY_FAILURE, {"status": "failed"}),
    )
    assert _release_check(args)[0] == QUALITY_FAILURE

    monkeypatch.setattr(
        "app.quality.cli._run_gate", lambda tier: (PASS, {"status": "passed"})
    )
    monkeypatch.setattr(
        "app.quality.cli.rehearse_database",
        lambda *args, **kwargs: DatabaseCheckResult(ok=False),
    )
    assert _release_check(args)[1]["summary"] == "database rehearsal failed"


def test_release_check_without_restart_records_success(tmp_path, monkeypatch) -> None:
    args = _release_args(tmp_path, _sandbox(tmp_path))
    AutoReplyStore(args.db)
    _ReleaseManager.instances.clear()
    monkeypatch.setattr("app.quality.cli.LocalReleaseManager", _ReleaseManager)
    monkeypatch.setattr("app.quality.cli._run_gate", lambda tier: (PASS, {}))
    monkeypatch.setattr(
        "app.quality.cli.rehearse_database",
        lambda *args, **kwargs: DatabaseCheckResult(ok=True, schema_version=1),
    )
    monkeypatch.setattr("app.quality.cli.subprocess.run", _clean_git_run)
    monkeypatch.setattr("app.quality.cli._launchd_pid", lambda: 0)
    monkeypatch.setattr(
        "app.quality.cli.build_quality_snapshot", lambda *args, **kwargs: _snapshot(pid=0)
    )

    code, payload = _release_check(args)

    assert code == PASS
    assert payload["status"] == "passed"
    assert payload["schema_version"] == 1
    runs = AutoReplyStore(args.db).list_quality_runs()
    assert runs[0]["status"] == "passed"

    failed_snapshot = _snapshot(pid=0).model_copy(
        update={
            "backlog": {
                "failed": 1,
                "processing": 0,
                "unknown": 0,
                "failed_actions": 0,
                "unknown_actions": 0,
            },
            "ready": False,
            "slo_status": "fail",
        }
    )
    monkeypatch.setattr(
        "app.quality.cli.build_quality_snapshot",
        lambda *args, **kwargs: failed_snapshot,
    )
    assert _release_check(args)[0] == QUALITY_FAILURE


def test_release_check_restart_success_and_restart_failure_rollback(
    tmp_path, monkeypatch
) -> None:
    args = _release_args(tmp_path, _sandbox(tmp_path), restart=True)
    AutoReplyStore(args.db)
    _ReleaseManager.instances.clear()
    monkeypatch.setattr("app.quality.cli.LocalReleaseManager", _ReleaseManager)
    monkeypatch.setattr("app.quality.cli._run_gate", lambda tier: (PASS, {}))
    monkeypatch.setattr(
        "app.quality.cli.rehearse_database",
        lambda *args, **kwargs: DatabaseCheckResult(ok=True, schema_version=1),
    )
    monkeypatch.setattr("app.quality.cli.subprocess.run", _clean_git_run)
    pids = iter((11, 22))
    monkeypatch.setattr("app.quality.cli._launchd_pid", lambda: next(pids))
    monkeypatch.setattr("app.quality.cli._restart_launchd", lambda pid: None)
    monkeypatch.setattr(
        "app.quality.cli._wait_for_service_quality",
        lambda **kwargs: _snapshot(commit="commit-a", pid=22),
    )

    code, payload = _release_check(args)

    assert code == PASS
    assert payload["pid_changed"] is True
    assert _ReleaseManager.instances[-1].activated

    _ReleaseManager.instances.clear()
    pids = iter((11,))
    monkeypatch.setattr("app.quality.cli._launchd_pid", lambda: next(pids))
    restart_results = iter(
        (
            (
                INFRASTRUCTURE_UNAVAILABLE,
                {"status": "infrastructure_error", "summary": "restart failed"},
            ),
            None,
        )
    )
    monkeypatch.setattr(
        "app.quality.cli._restart_launchd", lambda pid: next(restart_results)
    )

    code, _ = _release_check(args)

    assert code == INFRASTRUCTURE_UNAVAILABLE
    assert _ReleaseManager.instances[-1].rolled_back


def test_release_check_rolls_back_when_canary_fails(tmp_path, monkeypatch) -> None:
    args = _release_args(tmp_path, _sandbox(tmp_path), restart=True)
    AutoReplyStore(args.db)
    _ReleaseManager.instances.clear()
    monkeypatch.setattr("app.quality.cli.LocalReleaseManager", _ReleaseManager)
    monkeypatch.setattr("app.quality.cli._run_gate", lambda tier: (PASS, {}))
    monkeypatch.setattr(
        "app.quality.cli.rehearse_database",
        lambda *args, **kwargs: DatabaseCheckResult(ok=True, schema_version=1),
    )
    monkeypatch.setattr("app.quality.cli.subprocess.run", _clean_git_run)
    pids = iter((11, 22))
    monkeypatch.setattr("app.quality.cli._launchd_pid", lambda: next(pids))
    monkeypatch.setattr("app.quality.cli._restart_launchd", lambda pid: None)
    monkeypatch.setattr(
        "app.quality.cli._wait_for_service_quality",
        lambda **kwargs: _snapshot(commit="commit-a", pid=22),
    )
    monkeypatch.setattr(
        "app.quality.cli.run_sandbox_canaries",
        lambda profile: (CanaryResult(connector="dws", status="failed"),),
    )

    code, payload = _release_check(args)

    assert code == QUALITY_FAILURE
    assert payload["canaries"][0]["status"] == "failed"
    assert _ReleaseManager.instances[-1].rolled_back


def test_launchd_helpers_parse_pid_and_handle_restart_states(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.quality.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout="501\n"),
    )
    assert _launchd_label() == "gui/501/com.ceo-agent-service.main"

    responses = iter(
        [
            subprocess.CompletedProcess([], 1, stdout=""),
            subprocess.CompletedProcess([], 0, stdout="pid = invalid\n"),
            subprocess.CompletedProcess([], 0, stdout="state = running\npid = 42\n"),
        ]
    )
    monkeypatch.setattr(
        "app.quality.cli.subprocess.run", lambda *args, **kwargs: next(responses)
    )
    monkeypatch.setattr("app.quality.cli._launchd_label", lambda: "gui/501/service")
    assert _launchd_pid() == 0
    assert _launchd_pid() == 0
    assert _launchd_pid() == 42

    monkeypatch.setattr(
        "app.quality.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1),
    )
    assert _restart_launchd(1)[0] == INFRASTRUCTURE_UNAVAILABLE

    monkeypatch.setattr(
        "app.quality.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
    )
    monkeypatch.setattr("app.quality.cli._launchd_pid", lambda: 2)
    monkeypatch.setattr(
        "app.quality.cli.time",
        SimpleNamespace(monotonic=lambda: 0, sleep=lambda seconds: None),
    )
    assert _restart_launchd(1) is None


def test_restart_timeout_and_quality_endpoint_polling(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.quality.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
    )
    times = iter((0, 0, 31))
    fake_time = SimpleNamespace(
        monotonic=lambda: next(times), sleep=lambda seconds: None
    )
    monkeypatch.setattr("app.quality.cli.time", fake_time)
    monkeypatch.setattr("app.quality.cli._launchd_pid", lambda: 1)
    monkeypatch.setattr("app.quality.cli._launchd_label", lambda: "gui/501/service")
    assert _restart_launchd(1)[0] == QUALITY_FAILURE

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return _snapshot().model_dump_json().encode()

    fake_time.monotonic = lambda: 0
    monkeypatch.setattr("app.quality.cli.urlopen", lambda *args, **kwargs: Response())
    assert _wait_for_service_quality(commit="commit-a", pid=22) is not None

    times = iter((0, 0, 2))
    fake_time.monotonic = lambda: next(times)

    def unavailable(*args, **kwargs):
        raise URLError("synthetic")

    monkeypatch.setattr("app.quality.cli.urlopen", unavailable)
    assert _wait_for_service_quality(commit="x", pid=1, timeout_seconds=1) is None


def test_main_eval_db_check_formats_and_records_run(tmp_path, capsys) -> None:
    db_path = tmp_path / "service.sqlite3"
    AutoReplyStore(db_path)
    code = main(
        [
            "eval",
            "--suite",
            "protocol",
            "--db",
            str(db_path),
            "--format",
            "junit",
        ]
    )
    assert code == PASS
    assert "testsuite" in capsys.readouterr().out
    assert AutoReplyStore(db_path).list_quality_runs()[0]["suite"] == "protocol"

    assert main(
        ["db-check", "--mode", "check", "--db", str(db_path), "--format", "json"]
    ) == PASS
    assert json.loads(capsys.readouterr().out)["quick_check"] == "ok"
    assert main(
        [
            "db-check",
            "--mode",
            "rehearse",
            "--db",
            str(db_path),
            "--backup-dir",
            str(tmp_path / "backups"),
        ]
    ) == PASS
    assert "passed" in capsys.readouterr().out


def test_main_dispatches_check_release_and_infrastructure_errors(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "app.quality.cli._run_gate", lambda tier: (PASS, {"status": "passed"})
    )
    assert main(["check", "--tier", "pr"]) == PASS
    capsys.readouterr()

    monkeypatch.setattr(
        "app.quality.cli._release_check",
        lambda args: (QUALITY_FAILURE, {"status": "failed"}),
    )
    assert main(
        [
            "release-check",
            "--db",
            str(tmp_path / "db.sqlite3"),
            "--sandbox-profile",
            "test",
        ]
    ) == QUALITY_FAILURE
    capsys.readouterr()

    def subprocess_failure(args):
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr("app.quality.cli._release_check", subprocess_failure)
    assert main(
        [
            "release-check",
            "--db",
            str(tmp_path / "db.sqlite3"),
            "--sandbox-profile",
            "test",
            "--format",
            "json",
        ]
    ) == INFRASTRUCTURE_UNAVAILABLE
    assert json.loads(capsys.readouterr().out)["status"] == "infrastructure_error"

    def infrastructure_failure(args):
        raise InfrastructureUnavailable("offline")

    monkeypatch.setattr("app.quality.cli._release_check", infrastructure_failure)
    assert main(
        [
            "release-check",
            "--db",
            str(tmp_path / "db.sqlite3"),
            "--sandbox-profile",
            "test",
        ]
    ) == INFRASTRUCTURE_UNAVAILABLE
    assert "offline" in capsys.readouterr().out


def test_entrypoint_exits_with_main_result(monkeypatch) -> None:
    monkeypatch.setattr("app.quality.cli.main", lambda: QUALITY_FAILURE)
    with pytest.raises(SystemExit) as exc:
        entrypoint()
    assert exc.value.code == QUALITY_FAILURE
