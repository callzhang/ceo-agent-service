import hashlib
import json
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


def test_agent_cli_allows_incomplete_dws_help_as_read_only(monkeypatch):
    argv = ["dws", "chat", "message", "--help"]
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    launched = []

    def process_runner(*args, **kwargs):
        launched.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, "Usage: dws chat message", "")

    receipt = execute_reviewed_read(
        argv,
        classifier=NativeCliMetadataClassifier(reviewed_effects={}),
        process_runner=process_runner,
    )

    assert launched == [["/bin/dws", "chat", "message", "--help"]]
    assert receipt["operation"] == "chat message"
    assert "error" not in receipt


def test_agent_cli_help_cannot_use_write_capability():
    with pytest.raises(AgentReadOnlyViolationError, match="effect_mismatch"):
        execute_reviewed_write(
            ["dws", "chat", "message", "--help"],
            classifier=NativeCliMetadataClassifier(reviewed_effects={}),
        )


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


def _recovery_allowlist(argv, *, authorization_id="auth-action-1"):
    from app.native_cli_metadata import describe_native_command

    descriptor = describe_native_command({"type": "command_execution", "argv": argv})
    assert descriptor is not None
    arguments_digest = hashlib.sha256(
        json.dumps(
            {"argv": argv}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return json.dumps(
        [
            {
                "authorization_id": authorization_id,
                "action_index": 1,
                "capability": f"agent_cli.{descriptor.cli}",
                "operation": descriptor.command_path,
                "operation_digest": descriptor.command_digest,
                "target_identifiers": descriptor.target_identifiers,
                "arguments_digest": arguments_digest,
            }
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


@pytest.mark.parametrize("authorization_id", (None, "wrong-authorization"))
def test_recovery_write_allowlist_rejects_before_process_launch(
    monkeypatch, authorization_id
):
    argv = [
        "dws", "chat", "message", "send", "--group", "cid-one",
        "--text", "done", "--yes",
    ]
    monkeypatch.setenv("CEO_AGENT_RECOVERY_WRITE_ALLOWLIST", _recovery_allowlist(argv))
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    launched = False

    def process_runner(*args, **kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("process must not launch")

    with pytest.raises(AgentReadOnlyViolationError, match="recovery_write_not_authorized"):
        execute_reviewed_write(
            argv,
            authorization_id=authorization_id,
            classifier=NativeCliMetadataClassifier(
                reviewed_effects={("dws", "chat message send"): EffectKind.EFFECTFUL}
            ),
            process_runner=process_runner,
        )

    assert launched is False


def test_recovery_write_allowlist_authorizes_exact_descriptor(monkeypatch):
    argv = [
        "dws", "chat", "message", "send", "--group", "cid-one",
        "--text", "done", "--yes",
    ]
    monkeypatch.setenv("CEO_AGENT_RECOVERY_WRITE_ALLOWLIST", _recovery_allowlist(argv))
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    launched = []

    def process_runner(*args, **kwargs):
        launched.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, "{}", "")

    receipt = execute_reviewed_write(
        argv,
        authorization_id="auth-action-1",
        classifier=NativeCliMetadataClassifier(
            reviewed_effects={("dws", "chat message send"): EffectKind.EFFECTFUL}
        ),
        process_runner=process_runner,
    )

    assert launched == [["/bin/dws", *argv[1:]]]
    assert receipt["authorization_id"] == "auth-action-1"
    assert receipt["action_index"] == 1


def test_recovery_write_allowlist_rejects_wrong_target_before_launch(monkeypatch):
    authorized = [
        "dws", "chat", "message", "send", "--group", "cid-one",
        "--text", "done", "--yes",
    ]
    attempted = [*authorized]
    attempted[attempted.index("cid-one")] = "cid-two"
    monkeypatch.setenv(
        "CEO_AGENT_RECOVERY_WRITE_ALLOWLIST", _recovery_allowlist(authorized)
    )
    launched = False

    def process_runner(*args, **kwargs):
        nonlocal launched
        launched = True

    with pytest.raises(AgentReadOnlyViolationError, match="recovery_write_not_authorized"):
        execute_reviewed_write(
            attempted,
            authorization_id="auth-action-1",
            classifier=NativeCliMetadataClassifier(
                reviewed_effects={("dws", "chat message send"): EffectKind.EFFECTFUL}
            ),
            process_runner=process_runner,
        )
    assert launched is False
