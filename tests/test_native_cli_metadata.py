import hashlib
import json
import subprocess

import pytest

from app.agent_cli import (
    CLI_TIMEOUT_SECONDS,
    execute_reviewed_read,
    execute_reviewed_write,
)
from app.agent_result import EffectKind
from app.native_cli_metadata import AgentReadOnlyViolationError, NativeCliMetadataClassifier
from app.native_cli_metadata import describe_native_command


@pytest.fixture(autouse=True)
def _principal_local_read_policy(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                "[ceo_agent.local_read_policy]",
                'blocked_commands = ["bash", "sh", "zsh", "rm", "unzip"]',
                "[ceo_agent.local_read_policy.blocked_argument_prefixes]",
                'sed = ["-i", "--in-place"]',
                'find = ["-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprint0", "-fprintf", "-ok", "-okdir"]',
                'grep = ["--pre", "--generate"]',
                'rg = ["--pre", "--generate"]',
                'sort = ["-o", "--output"]',
                'tail = ["-f", "--follow"]',
                'python = ["-m"]',
                'python3 = ["-m"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_AGENT_CODEX_CONFIG_PATH", str(config))


def test_classifier_accepts_unblocked_local_read_pipeline():
    command = "cat /tmp/material.txt | sed -n '1,10p' | head -c 3000"

    descriptor = NativeCliMetadataClassifier(reviewed_effects={}).classify(
        {"type": "command_execution", "command": command}
    )

    assert descriptor is not None
    assert descriptor.effect is EffectKind.READ_ONLY
    assert descriptor.cli == "local-shell"


def test_describe_native_command_accepts_reviewed_local_read():
    descriptor = describe_native_command(
        {"type": "command_execution", "argv": ["sed", "-n", "1p", "/tmp/file"]}
    )

    assert descriptor is not None
    assert descriptor.cli == "local-shell"
    assert descriptor.command_path == "sed"
    assert descriptor.effect is EffectKind.READ_ONLY


def test_describe_native_command_allows_python_read_code_when_not_blacklisted():
    descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "python3",
                "-c",
                "print('read material')",
            ],
        }
    )

    assert descriptor is not None
    assert descriptor.cli == "local-shell"
    assert descriptor.effect is EffectKind.READ_ONLY


def test_describe_native_command_allows_service_owned_oa_detail_read():
    descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                ".venv/bin/python",
                "-m",
                "app.cli",
                "read-oa-approval-detail",
                "--instance-id",
                "proc-1",
            ],
        }
    )

    assert descriptor is not None
    assert descriptor.effect is EffectKind.READ_ONLY
    assert descriptor.command_path == "app.cli read-oa-approval-detail"
    assert descriptor.target_identifiers == {"instance-id": "proc-1"}


def test_describe_native_command_rejects_blacklisted_python_module_execution():
    descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "python3",
                "-m",
                "app.cli",
                "send-attempt",
                "--instance-id",
                "proc-1",
            ],
        }
    )

    assert descriptor is None


def test_describe_native_command_rejects_blacklisted_command_and_argument():
    assert describe_native_command(
        {"type": "command_execution", "argv": ["rm", "/tmp/material"]}
    ) is None
    assert describe_native_command(
        {"type": "command_execution", "argv": ["sed", "-i", "", "/tmp/material"]}
    ) is None


def test_dws_direct_message_targets_preserve_user_and_idempotency_identity():
    write_descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-1",
                "--text",
                "done",
                "--uuid",
                "operation-1",
                "--yes",
            ],
        }
    )
    user_read_descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": ["dws", "chat", "+chat-messages", "--user", "user-1"],
        }
    )
    status_read_descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "dws",
                "chat",
                "message",
                "query-send-status",
                "--uuid",
                "operation-1",
            ],
        }
    )

    assert write_descriptor is not None
    assert write_descriptor.target_identifiers == {
        "user": "user-1",
        "uuid": "operation-1",
    }
    assert user_read_descriptor is not None
    assert user_read_descriptor.target_identifiers == {"user": "user-1"}
    assert status_read_descriptor is not None
    assert status_read_descriptor.target_identifiers == {"uuid": "operation-1"}


@pytest.mark.parametrize(
    "command",
    (
        "unzip /tmp/deck.pptx",
        "unzip -d /tmp/extracted /tmp/deck.pptx",
    ),
)
def test_classifier_rejects_pptx_commands_that_extract_files(command):
    descriptor = NativeCliMetadataClassifier(reviewed_effects={}).classify(
        {"type": "command_execution", "command": command}
    )

    assert descriptor is None


def test_agent_cli_uses_agent_cli_error_codes(monkeypatch):
    classifier = NativeCliMetadataClassifier(reviewed_effects={("dws", "chat message get"): EffectKind.READ_ONLY})
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    receipt = execute_reviewed_read(
        ["dws", "chat", "message", "get"], classifier=classifier,
        process_runner=lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], 120)),
    )
    assert receipt["error"]["code"] == "agent_cli_timeout"


def test_agent_cli_allows_native_command_to_run_for_fifteen_minutes(monkeypatch):
    classifier = NativeCliMetadataClassifier(
        reviewed_effects={("dws", "chat message get"): EffectKind.READ_ONLY}
    )
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    observed_timeout = None

    def process_runner(*args, **kwargs):
        nonlocal observed_timeout
        observed_timeout = kwargs["timeout"]
        return subprocess.CompletedProcess(args[0], 0, "{}", "")

    execute_reviewed_read(
        ["dws", "chat", "message", "get"],
        classifier=classifier,
        process_runner=process_runner,
    )

    assert CLI_TIMEOUT_SECONDS == 15 * 60
    assert observed_timeout == CLI_TIMEOUT_SECONDS


def test_agent_cli_executes_reviewed_local_read_with_original_binary(monkeypatch):
    argv = ["sed", "-n", "1p", "/tmp/public-key.pub"]
    monkeypatch.setattr(
        "app.agent_cli.shutil.which",
        lambda executable: "/usr/bin/sed" if executable == "sed" else None,
    )
    launched = []

    def process_runner(*args, **kwargs):
        launched.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, "verified material\n", "")

    receipt = execute_reviewed_read(
        argv,
        classifier=NativeCliMetadataClassifier(reviewed_effects={}),
        process_runner=process_runner,
    )

    assert launched == [["/usr/bin/sed", *argv[1:]]]
    assert receipt["cli"] == "local-shell"
    assert receipt["operation"] == "sed"
    assert receipt["stdout"] == "verified material\n"
    assert "error" not in receipt


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


def test_agent_cli_rejects_interactive_dws_write_before_process_launch(monkeypatch):
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    launched = False

    def process_runner(*args, **kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("process must not launch")

    with pytest.raises(AgentReadOnlyViolationError, match="agent_cli_confirmation_required"):
        execute_reviewed_write(
            ["dws", "chat", "+send-to-group", "--group", "group-1", "--text", "done"],
            classifier=NativeCliMetadataClassifier(
                reviewed_effects={
                    ("dws", "chat +send-to-group"): EffectKind.EFFECTFUL
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
