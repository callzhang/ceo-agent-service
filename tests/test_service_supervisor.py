from argparse import Namespace

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
