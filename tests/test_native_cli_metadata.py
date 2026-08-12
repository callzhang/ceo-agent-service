import hashlib
import json
import subprocess

import pytest

import app.native_cli_metadata as native_cli_metadata
from app.agent_cli import (
    CLI_TIMEOUT_SECONDS,
    execute_reviewed_read,
    execute_reviewed_write,
)
from app.agent_result import EffectKind
from app.native_cli_metadata import AgentReadOnlyViolationError, NativeCliMetadataClassifier
from app.native_cli_metadata import describe_native_command
from app.dws_client import DwsClient


def test_classifier_rejects_generic_local_read_pipeline():
    command = "cat /tmp/material.txt | sed -n '1,10p' | head -c 3000"

    descriptor = NativeCliMetadataClassifier(reviewed_effects={}).classify(
        {"type": "command_execution", "command": command}
    )

    assert descriptor is None


def test_describe_native_command_rejects_generic_local_read():
    descriptor = describe_native_command(
        {"type": "command_execution", "argv": ["sed", "-n", "1p", "/tmp/file"]}
    )

    assert descriptor is None


def test_describe_native_command_rejects_arbitrary_python_even_when_not_blacklisted():
    descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "python3",
                "-c",
                "open('/tmp/consumer-escape', 'w').write('escaped')",
            ],
        }
    )

    assert descriptor is None


def test_describe_native_command_rejects_unallowlisted_executable():
    assert describe_native_command(
        {
            "type": "command_execution",
            "argv": ["curl", "https://example.com/write"],
        }
    ) is None


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


def test_describe_native_command_rejects_local_pipeline_with_identifiers():
    descriptor = describe_native_command(
        {
            "type": "command_execution",
            "command": "cat /tmp/material.txt | grep --instance-id proc-1",
        }
    )

    assert descriptor is None


def test_describe_native_command_rejects_python_module_execution():
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


def test_describe_native_command_rejects_generic_local_commands():
    assert describe_native_command(
        {"type": "command_execution", "argv": ["rm", "/tmp/material"]}
    ) is None
    assert describe_native_command(
        {"type": "command_execution", "argv": ["sed", "-i", "", "/tmp/material"]}
    ) is None
    assert describe_native_command(
        {
            "type": "command_execution",
            "argv": ["sed", "-n", "1w /tmp/consumer-escape", "/tmp/material"],
        }
    ) is None


def test_dws_download_read_requires_fresh_temp_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(native_cli_metadata, "MATERIAL_OUTPUT_ROOT", tmp_path)
    classifier = NativeCliMetadataClassifier(
        reviewed_effects={
            ("dws", "doc download"): EffectKind.READ_ONLY,
        }
    )

    assert classifier.classify(
        {
            "type": "command_execution",
            "argv": ["dws", "doc", "download", "--output", "app/worker.py"],
        }
    ) is None
    assert classifier.classify(
        {
            "type": "command_execution",
            "argv": ["dws", "doc", "download"],
        }
    ) is None

    destination = tmp_path / "fresh-material.bin"
    descriptor = classifier.classify(
        {
            "type": "command_execution",
            "argv": ["dws", "doc", "download", "--output", str(destination)],
        }
    )

    assert descriptor is not None
    destination.write_bytes(b"existing")
    assert classifier.classify(
        {
            "type": "command_execution",
            "argv": ["dws", "doc", "download", "--output", str(destination)],
        }
    ) is None


@pytest.mark.parametrize(
    "command_path",
    ("doc export", "mail message export", "sheet export", "markdown fetch"),
)
def test_dws_other_local_output_verbs_remain_rejected(
    tmp_path, monkeypatch, command_path
):
    monkeypatch.setattr(native_cli_metadata, "MATERIAL_OUTPUT_ROOT", tmp_path)
    classifier = NativeCliMetadataClassifier(
        reviewed_effects={("dws", command_path): EffectKind.READ_ONLY}
    )
    argv = ["dws", *command_path.split(), "--output", str(tmp_path / "fresh.bin")]

    assert classifier.classify({"type": "command_execution", "argv": argv}) is None
    assert classifier.classify(
        {"type": "command_execution", "argv": argv[:-2]}
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


def test_dws_mail_reply_and_verify_preserve_shared_mailbox_identity():
    reply_descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": DwsClient().build_mail_reply_command(
                mailbox="principal@example.test",
                message_id="mail-1",
                subject="Re: Contract approval",
                content="Approved with the documented conditions.",
            ),
        }
    )
    verify_descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "dws",
                "mail",
                "message",
                "verify",
                "--email",
                "principal@example.test",
                "--internet-message-id",
                "internet-1",
                "--format",
                "json",
            ],
        }
    )

    assert reply_descriptor is not None
    assert reply_descriptor.command_path == "mail message reply"
    assert reply_descriptor.target_identifiers == {
        "from": "principal@example.test",
        "id": "mail-1",
    }
    assert verify_descriptor is not None
    assert verify_descriptor.command_path == "mail message verify"
    assert verify_descriptor.target_identifiers == {
        "email": "principal@example.test",
        "internet-message-id": "internet-1",
    }


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


def test_agent_cli_rejects_generic_local_read_without_launching(monkeypatch):
    argv = ["sed", "-n", "1p", "/tmp/public-key.pub"]
    monkeypatch.setattr(
        "app.agent_cli.shutil.which",
        lambda executable: "/usr/bin/sed" if executable == "sed" else None,
    )
    launched = []

    def process_runner(*args, **kwargs):
        launched.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, "verified material\n", "")

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="agent_cli_command_unreviewed",
    ):
        execute_reviewed_read(
            argv,
            classifier=NativeCliMetadataClassifier(reviewed_effects={}),
            process_runner=process_runner,
        )

    assert launched == []


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


def test_agent_cli_allows_dws_schema_contract_read(monkeypatch):
    argv = [
        "dws", "schema", "--cli-path", "oa +approval-get",
        "--compact", "--format", "json",
    ]
    monkeypatch.setattr("app.agent_cli.shutil.which", lambda _: "/bin/dws")
    launched = []

    def process_runner(*args, **_kwargs):
        launched.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, "{}", "")

    receipt = execute_reviewed_read(
        argv,
        classifier=NativeCliMetadataClassifier(reviewed_effects={}),
        process_runner=process_runner,
    )

    assert launched == [["/bin/dws", *argv[1:]]]
    assert receipt["operation"] == "schema"
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
