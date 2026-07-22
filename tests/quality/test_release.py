from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError

from app.quality.release import (
    LocalReleaseManager,
    SandboxProfile,
    run_sandbox_canaries,
)


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_sandbox_profile_fails_closed_without_test_target_allowlist() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        SandboxProfile.model_validate(
            {"name": "release", "connectors": {"dingtalk": {}}}
        )

    with pytest.raises(ValidationError):
        SandboxProfile.model_validate(
            {
                "name": "release",
                "live_enabled": True,
                "connectors": {
                    "dingtalk": {
                        "enabled": True,
                        "test_target_ref": "target-a",
                        "allowlist": [],
                        "canary_command": ["true"],
                    }
                },
            }
        )

    with pytest.raises(ValidationError, match="canary command"):
        SandboxProfile.model_validate(
            {
                "name": "release",
                "live_enabled": True,
                "connectors": {
                    "dingtalk": {
                        "enabled": True,
                        "test_target_ref": "target-a",
                        "allowlist": ["target-a"],
                    }
                },
            }
        )

    with pytest.raises(ValidationError, match="live_enabled"):
        SandboxProfile.model_validate(
            {
                "name": "release",
                "connectors": {
                    "dingtalk": {
                        "enabled": True,
                        "test_target_ref": "target-a",
                        "allowlist": ["target-a"],
                        "canary_command": ["true"],
                    }
                },
            }
        )


def test_sandbox_canaries_skip_disabled_and_report_process_results(monkeypatch) -> None:
    profile = SandboxProfile.model_validate(
        {
            "name": "release",
            "live_enabled": True,
            "connectors": {
                "disabled": {},
                "ok": {
                    "enabled": True,
                    "test_target_ref": "target-ok",
                    "allowlist": ["target-ok"],
                    "canary_command": ["probe", "{target_ref}"],
                },
                "bad": {
                    "enabled": True,
                    "test_target_ref": "target-bad",
                    "allowlist": ["target-bad"],
                    "canary_command": ["probe", "{target_ref}"],
                },
            },
        }
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0 if command[-1] == "target-ok" else 9)

    monkeypatch.setattr("app.quality.release.subprocess.run", fake_run)

    results = run_sandbox_canaries(profile)

    assert [result.status for result in results] == ["passed", "failed"]
    assert calls[0][0] == ["probe", "target-ok"]
    assert calls[0][1]["CEO_QUALITY_CANARY"] == "1"


def test_sandbox_canary_reports_unavailable_command(monkeypatch) -> None:
    profile = SandboxProfile.model_validate(
        {
            "name": "release",
            "live_enabled": True,
            "connectors": {
                "missing": {
                    "enabled": True,
                    "test_target_ref": "target",
                    "allowlist": ["target"],
                    "canary_command": ["missing-command"],
                }
            },
        }
    )

    def fail_run(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr("app.quality.release.subprocess.run", fail_run)

    result = run_sandbox_canaries(profile)[0]

    assert result.status == "failed"
    assert result.reason == "FileNotFoundError"


def test_release_build_switch_and_rollback_are_atomic(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Test")
    (repo / "service.txt").write_text("v1", encoding="utf-8")
    _git(repo, "add", "service.txt")
    _git(repo, "commit", "-m", "v1")
    first_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    manager = LocalReleaseManager(repo=repo, release_root=tmp_path / "runtime")
    first = manager.build(first_commit)
    manager.activate(first)

    (repo / "service.txt").write_text("v2", encoding="utf-8")
    _git(repo, "add", "service.txt")
    _git(repo, "commit", "-m", "v2")
    second_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    second = manager.build(second_commit)
    previous = manager.activate(second)

    assert previous == first
    assert manager.current.resolve() == second.resolve()
    manager.rollback(previous)
    assert manager.current.resolve() == first.resolve()

    assert manager.build(first_commit) == first
    with pytest.raises(ValueError, match="managed release"):
        manager.activate(tmp_path / "unmanaged")

    for index in range(4):
        extra = manager.releases / f"extra-{index}"
        extra.mkdir()
        (extra / "nested").mkdir()
        (extra / "nested" / "asset.txt").write_text("x", encoding="utf-8")
    manager.keep = 2
    manager.prune()
    retained = [path for path in manager.releases.iterdir() if path.is_dir()]
    assert len(retained) == 2

    manager.rollback(None)
    assert not manager.current.exists()

    (first / ".ceo-release.json").write_text(
        '{"commit":"different"}\n', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="marker"):
        manager.build(first_commit)


def test_release_build_cleans_partial_directory_on_archive_failure(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Test")
    (repo / "service.txt").write_text("v1", encoding="utf-8")
    _git(repo, "add", "service.txt")
    _git(repo, "commit", "-m", "v1")
    manager = LocalReleaseManager(repo=repo, release_root=tmp_path / "runtime")

    def fail_open(*args, **kwargs):
        raise OSError("synthetic")

    monkeypatch.setattr("app.quality.release.tarfile.open", fail_open)
    with pytest.raises(OSError, match="synthetic"):
        manager.build("HEAD")

    assert not list(manager.releases.glob(".building-*"))
