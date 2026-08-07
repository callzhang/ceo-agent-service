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


def test_supervisor_recovers_single_launchd_job_when_a_child_exits():
    worker = FakeChild(returncode=7)
    audit_web = FakeChild()
    children = iter((worker, audit_web))

    result = service_supervisor.run_supervisor(
        ["worker"],
        ["audit-web"],
        popen=lambda _command: next(children),
        sleep=lambda _seconds: None,
    )

    assert result == 7
    assert audit_web.terminated is True
    assert audit_web.waited is True


def test_supervisor_reaps_worker_when_audit_web_cannot_start():
    worker = FakeChild()
    calls = 0

    def popen(_command):
        nonlocal calls
        calls += 1
        if calls == 1:
            return worker
        raise OSError("audit web cannot start")

    with pytest.raises(OSError, match="audit web cannot start"):
        service_supervisor.run_supervisor(
            ["worker"],
            ["audit-web"],
            popen=popen,
            sleep=lambda _seconds: None,
        )

    assert worker.terminated is True
    assert worker.waited is True
