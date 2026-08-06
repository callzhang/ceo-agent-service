import subprocess

import pytest

from app.agent_cli import execute_reviewed_read, execute_reviewed_write
from app.agent_result import EffectKind
from app.native_cli_metadata import AgentReadOnlyViolationError, NativeCliMetadataClassifier


def test_agent_cli_uses_agent_cli_error_codes(monkeypatch):
    classifier = NativeCliMetadataClassifier(reviewed_effects={("dws", "chat message get"): EffectKind.READ_ONLY})
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    receipt = execute_reviewed_read(
        ["dws", "chat", "message", "get"], classifier=classifier,
        process_runner=lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], 120)),
    )
    assert receipt["error"]["code"] == "agent_cli_timeout"


def test_agent_cli_rejects_sensitive_argv_before_process_launch():
    launched = False

    def process_runner(*args, **kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("process must not launch")

    with pytest.raises(AgentReadOnlyViolationError, match="sensitive_argument"):
        execute_reviewed_write(
            ["dws", "chat", "message", "send", "--token", "opaque-value"],
            classifier=NativeCliMetadataClassifier(
                reviewed_effects={
                    ("dws", "chat message send"): EffectKind.EFFECTFUL
                }
            ),
            process_runner=process_runner,
        )

    assert launched is False
