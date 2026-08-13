import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from app import service_supervisor


class FakeChild:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self):
        self.waited = True
        return self.returncode


def _run_installer_fixture(
    tmp_path: Path,
    *,
    index_html: str | None = None,
    asset_files: dict[str, str] | None = None,
    symlink_assets: dict[str, str] | None = None,
    include_source_plist: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    repository = tmp_path / "repository"
    scripts_dir = repository / "scripts"
    scripts_dir.mkdir(parents=True)
    source_script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "install-auto-reply-agents.sh"
    )
    installer = scripts_dir / source_script.name
    installer.write_bytes(source_script.read_bytes())
    if index_html is not None:
        asset_dir = repository / "app" / "static" / "workbench"
        asset_dir.mkdir(parents=True)
        (asset_dir / "index.html").write_text(index_html)
        for relative_path, content in (asset_files or {}).items():
            asset_path = asset_dir / relative_path
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(content)
        for relative_path, content in (symlink_assets or {}).items():
            outside = repository / f"outside-{Path(relative_path).name}"
            outside.write_text(content)
            asset_path = asset_dir / relative_path
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.symlink_to(outside)
    if include_source_plist:
        launchd_dir = repository / "launchd"
        launchd_dir.mkdir()
        (launchd_dir / "com.ceo-agent-service.main.plist").write_text("<plist/>")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl_log = tmp_path / "launchctl.log"
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LAUNCHCTL_LOG\"\n"
    )
    fake_launchctl.chmod(0o755)
    fake_home = tmp_path / "home"
    env = {
        **os.environ,
        "HOME": str(fake_home),
        "LAUNCHCTL_LOG": str(launchctl_log),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
    }
    completed = subprocess.run(
        ["bash", str(installer)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed, fake_home, launchctl_log


def test_shutdown_grace_finishes_before_launchd_forces_exit():
    assert service_supervisor.SHUTDOWN_GRACE_SECONDS < 5.0


def test_build_child_command_uses_same_runtime_and_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(service_supervisor.sys, "executable", "/tmp/ceo-python")
    args = Namespace(
        host="127.0.0.1",
        port=8765,
        db=tmp_path / "auto-reply.sqlite3",
        workspace=tmp_path / "workspace",
        corpus_dir=tmp_path / "corpus",
    )

    command = service_supervisor.build_child_command("audit-web", args)

    assert command == [
        "/tmp/ceo-python",
        "-m",
        "app.cli",
        "audit-web",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--db",
        str(args.db),
        "--workspace",
        str(args.workspace),
        "--corpus-dir",
        str(args.corpus_dir),
    ]


def test_supervisor_restarts_only_the_child_that_exits(monkeypatch):
    worker = FakeChild(returncode=7)
    replacement_worker = FakeChild()
    audit_web = FakeChild()
    children = iter((worker, audit_web, replacement_worker))
    handlers = {}
    sleep_calls = 0

    def register_signal(current_signal, handler):
        previous = handlers.get(current_signal)
        handlers[current_signal] = handler
        return previous

    def sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            assert audit_web.terminated is False
            handlers[service_supervisor.signal.SIGTERM](
                service_supervisor.signal.SIGTERM,
                None,
            )

    monkeypatch.setattr(service_supervisor.signal, "signal", register_signal)

    result = service_supervisor.run_supervisor(
        ["worker"],
        ["audit-web"],
        popen=lambda _command: next(children),
        sleep=sleep,
    )

    assert result == 0
    assert worker.waited is True
    assert audit_web.terminated is True
    assert audit_web.waited is True
    assert replacement_worker.terminated is True


def test_supervisor_retries_audit_web_start_without_reaping_worker(monkeypatch):
    worker = FakeChild()
    audit_web = FakeChild()
    calls = 0
    handlers = {}
    sleep_calls = 0

    def popen(_command):
        nonlocal calls
        calls += 1
        if calls == 1:
            return worker
        if calls == 2:
            raise OSError("audit web cannot start")
        return audit_web

    def register_signal(current_signal, handler):
        previous = handlers.get(current_signal)
        handlers[current_signal] = handler
        return previous

    def sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            handlers[service_supervisor.signal.SIGTERM](
                service_supervisor.signal.SIGTERM,
                None,
            )

    monkeypatch.setattr(service_supervisor.signal, "signal", register_signal)

    result = service_supervisor.run_supervisor(
        ["worker"],
        ["audit-web"],
        popen=popen,
        sleep=sleep,
    )

    assert result == 0
    assert calls == 3
    assert worker.terminated is True
    assert worker.waited is True


def test_installer_rejects_missing_workbench_build_before_service_mutation(
    tmp_path: Path,
):
    completed, fake_home, launchctl_log = _run_installer_fixture(tmp_path)

    assert completed.returncode == 1
    assert completed.stderr.strip() == (
        "workbench assets missing; run npm install --prefix frontend && "
        "npm run build:workbench"
    )
    assert not (fake_home / "Library" / "LaunchAgents").exists()
    assert not (fake_home / "Library" / "Logs" / "ceo-agent-service").exists()
    assert not launchctl_log.exists()


def test_installer_rejects_index_with_missing_assets_before_service_mutation(
    tmp_path: Path,
):
    completed, fake_home, launchctl_log = _run_installer_fixture(
        tmp_path,
        index_html=(
            '<!doctype html><link rel="stylesheet" '
            'href="/workbench-assets/assets/index-missing.css">'
            '<script src="/workbench-assets/assets/index-missing.js"></script>'
        ),
    )

    assert completed.returncode == 1
    assert completed.stderr.strip() == (
        "workbench assets missing; run npm install --prefix frontend && "
        "npm run build:workbench"
    )
    assert not (fake_home / "Library" / "LaunchAgents").exists()
    assert not (fake_home / "Library" / "Logs" / "ceo-agent-service").exists()
    assert not launchctl_log.exists()


def test_installer_rejects_empty_index_before_service_mutation(tmp_path: Path):
    completed, fake_home, launchctl_log = _run_installer_fixture(
        tmp_path,
        index_html="<!doctype html><html><body></body></html>",
        include_source_plist=True,
    )

    assert completed.returncode == 1
    assert completed.stderr.strip() == (
        "workbench assets missing; run npm install --prefix frontend && "
        "npm run build:workbench"
    )
    assert not (fake_home / "Library" / "LaunchAgents").exists()
    assert not (fake_home / "Library" / "Logs" / "ceo-agent-service").exists()
    assert not launchctl_log.exists()


def test_installer_rejects_symlinked_referenced_asset_before_service_mutation(
    tmp_path: Path,
):
    completed, fake_home, launchctl_log = _run_installer_fixture(
        tmp_path,
        index_html=(
            '<!doctype html><link rel="stylesheet" '
            'href="/workbench-assets/assets/index.css">'
            '<script type="module" '
            'src="/workbench-assets/assets/index.js"></script>'
        ),
        asset_files={"assets/index.js": "document.body.dataset.ready = '1';"},
        symlink_assets={"assets/index.css": "body {}"},
        include_source_plist=True,
    )

    assert completed.returncode == 1
    assert completed.stderr.strip() == (
        "workbench assets missing; run npm install --prefix frontend && "
        "npm run build:workbench"
    )
    assert not (fake_home / "Library" / "LaunchAgents").exists()
    assert not (fake_home / "Library" / "Logs" / "ceo-agent-service").exists()
    assert not launchctl_log.exists()


def test_installer_validates_source_plist_before_service_mutation(tmp_path: Path):
    completed, fake_home, launchctl_log = _run_installer_fixture(
        tmp_path,
        index_html=(
            '<!doctype html><link rel="stylesheet" '
            'href="/workbench-assets/assets/index.css">'
            '<script type="module" '
            'src="/workbench-assets/assets/index.js"></script>'
        ),
        asset_files={
            "assets/index.css": "body {}",
            "assets/index.js": "document.body.dataset.ready = '1';",
        },
    )

    assert completed.returncode == 1
    assert "install prerequisite missing:" in completed.stderr
    assert "launchd/com.ceo-agent-service.main.plist" in completed.stderr
    assert not (fake_home / "Library" / "LaunchAgents").exists()
    assert not (fake_home / "Library" / "Logs" / "ceo-agent-service").exists()
    assert not launchctl_log.exists()
