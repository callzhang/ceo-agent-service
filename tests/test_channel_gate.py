from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.channel_gate import ChannelGateState, DwsChannelGate, LarkChannelGate


def completed(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class ScriptedRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]):
        self.results = iter(results)
        self.commands: list[list[str]] = []
        self.calls: list[dict[str, object]] = []

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        self.calls.append(kwargs)
        return next(self.results)


def test_dws_gate_requires_status_and_authenticated_probe():
    runner = ScriptedRunner(
        [
            completed(
                0,
                '{"authenticated":true,"token_valid":true,"refresh_token_valid":true}',
            ),
            completed(4, "", '{"code":"invalidParameter.authCode.notFound"}'),
        ]
    )

    result = DwsChannelGate(binary="dws", runner=runner).check()

    assert result.state is ChannelGateState.NEEDS_LOGIN
    assert runner.commands == [
        ["dws", "auth", "status", "--format", "json", "--timeout", "5"],
        ["dws", "contact", "user", "get-self", "--format", "json"],
    ]


def test_dws_gate_classifies_structured_status_auth_error_from_stderr():
    runner = ScriptedRunner(
        [completed(4, "", '{"code":"invalidParameter.authCode.notFound"}')]
    )

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.NEEDS_LOGIN
    assert result.reason_code == "status_auth_failed"
    assert result.detail == '{"code": "invalidParameter.authCode.notFound"}'


def test_dws_gate_classifies_typed_not_authenticated_code_as_needs_login():
    runner = ScriptedRunner([completed(2, '{"code":"not_authenticated"}')])

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.NEEDS_LOGIN
    assert result.reason_code == "status_auth_failed"


def test_lark_gate_requires_verified_status_and_authenticated_probe():
    runner = ScriptedRunner(
        [
            completed(0, '{"authenticated":true}'),
            completed(0, '{"data":{"user_id":"u1"}}'),
        ]
    )

    result = LarkChannelGate(binary="lark-cli", runner=runner).check()

    assert result.state is ChannelGateState.READY
    assert runner.commands[0] == ["lark-cli", "auth", "status", "--json", "--verify"]
    assert runner.commands[1] == [
        "lark-cli",
        "contact",
        "+get-user",
        "--as",
        "user",
        "--json",
    ]


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [
        (
            '{"authenticated":false,"token_valid":true,"refresh_token_valid":true}',
            "status_auth_invalid",
        ),
        (
            '{"authenticated":true,"token_valid":false,"refresh_token_valid":true}',
            "status_auth_invalid",
        ),
        (
            '{"authenticated":true,"token_valid":true,"refresh_token_valid":false}',
            "status_auth_invalid",
        ),
    ],
)
def test_dws_gate_rejects_any_invalid_auth_status(status: str, reason_code: str):
    runner = ScriptedRunner([completed(0, status)])

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.NEEDS_LOGIN
    assert result.reason_code == reason_code
    assert len(runner.commands) == 1


def test_dws_gate_does_not_infer_login_from_unrecognized_status_object():
    runner = ScriptedRunner([completed(0, "{}")])

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_unrecognized"


def test_gate_blocks_when_executable_is_missing():
    def missing_runner(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    result = LarkChannelGate(binary="missing-lark", runner=missing_runner).check()

    assert result.state is ChannelGateState.BLOCKED
    assert result.reason_code == "executable_missing"


def test_lark_gate_blocks_structured_missing_configuration():
    runner = ScriptedRunner(
        [
            completed(
                3,
                '{"ok":false,"error":{"type":"config","subtype":"not_configured"}}',
            )
        ]
    )

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.BLOCKED
    assert result.reason_code == "status_configuration_missing"


def test_gate_classifies_timeout_as_unavailable():
    def timeout_runner(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout=5)

    result = DwsChannelGate(runner=timeout_runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_timeout"


def test_dws_gate_prefers_structured_provider_failure_over_auth_returncode():
    runner = ScriptedRunner(
        [
            completed(
                4,
                "",
                '{"error":{"type":"provider","code":"PROVIDER_UNAVAILABLE"}}',
            )
        ]
    )

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_provider_unavailable"


def test_dws_gate_parses_generic_stdout_and_provider_stderr_independently():
    runner = ScriptedRunner(
        [
            completed(
                4,
                '{"ok":false}',
                '{"error":{"type":"provider","code":"PROVIDER_UNAVAILABLE"}}',
            )
        ]
    )

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_provider_unavailable"


def test_dws_gate_does_not_treat_rate_limit_code_as_login_failure():
    runner = ScriptedRunner([completed(4, '{"error":{"code":"RATE_LIMITED"}}')])

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_failed"


def test_dws_gate_does_not_ignore_unknown_structured_stderr_after_ready_stdout():
    runner = ScriptedRunner(
        [
            completed(
                0,
                '{"authenticated":true,"token_valid":true,"refresh_token_valid":true}',
                '{"error":{"code":"UNKNOWN_PROVIDER_FAILURE"}}',
            )
        ]
    )

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_failed"


def test_lark_gate_prefers_structured_network_failure_over_auth_returncode():
    runner = ScriptedRunner(
        [
            completed(
                4,
                '{"ok":false,"error":{"type":"network","code":"NETWORK_UNAVAILABLE"}}',
            )
        ]
    )

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_network_unavailable"


def test_gate_classifies_structured_network_failure_as_unavailable():
    runner = ScriptedRunner(
        [
            completed(
                5, "", '{"error":{"type":"network","message":"service unavailable"}}'
            )
        ]
    )

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_network_unavailable"
    assert result.detail == '{"error.type": "network"}'


def test_gate_rejects_successful_non_object_json_probe():
    runner = ScriptedRunner(
        [
            completed(0, '{"authenticated":true}'),
            completed(0, "[]"),
        ]
    )

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "live_probe_invalid_json"


def test_lark_gate_accepts_current_verified_status_and_user_probe_shape():
    runner = ScriptedRunner(
        [
            completed(
                0,
                '{"verified":true,"identity":"user","identities":'
                '{"user":{"available":true,"verified":true,'
                '"status":"ready","tokenStatus":"valid"}}}',
            ),
            completed(
                0,
                '{"ok":true,"identity":"user","data":{"user":{"open_id":"u1"}}}',
            ),
        ]
    )

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.READY


def test_lark_gate_rejects_empty_status_object():
    runner = ScriptedRunner([completed(0, "{}")])

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_unrecognized"


def test_lark_gate_rejects_empty_probe_object():
    runner = ScriptedRunner(
        [
            completed(0, '{"authenticated":true}'),
            completed(0, "{}"),
        ]
    )

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "live_probe_unrecognized"


def test_gate_detail_excludes_tokens_urls_paths_messages_and_reasons():
    sensitive = (
        "token=secret-value https://internal.example/path "
        "/Users/derek/private/credential.json"
    )
    runner = ScriptedRunner(
        [
            completed(
                4,
                "",
                json.dumps(
                    {
                        "error": {
                            "type": "provider",
                            "code": "RATE_LIMITED",
                            "message": sensitive,
                            "reason": sensitive,
                        }
                    }
                ),
            )
        ]
    )

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.detail == '{"error.type": "provider", "error.code": "RATE_LIMITED"}'
    assert "secret-value" not in result.detail
    assert "https://" not in result.detail
    assert "/Users/" not in result.detail


def test_gate_detail_omits_unstructured_sensitive_stderr():
    sensitive = "token=secret-value https://internal.example /Users/derek/private"
    runner = ScriptedRunner([completed(7, "", sensitive)])

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.detail == "<unstructured error>"
    assert sensitive not in result.detail


def test_invalid_executable_is_blocked(tmp_path: Path):
    executable = tmp_path / "invalid-cli"
    executable.write_text("not an executable format", encoding="utf-8")
    executable.chmod(0o755)

    result = DwsChannelGate(binary=str(executable)).check()

    assert result.state is ChannelGateState.BLOCKED
    assert result.reason_code == "executable_unusable"
    assert str(tmp_path) not in result.detail


def test_temporary_dws_executable_receives_noninteractive_env_and_returns_json(
    tmp_path: Path,
    monkeypatch,
):
    executable = tmp_path / "dws-fixture"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "${DINGTALK_DWS_AGENTCODE:-}" != "integration-agent" ]; then\n'
        '  printf \'%s\' \'{"error":{"type":"config","code":"MISSING_ENV"}}\' >&2\n'
        "  exit 3\n"
        "fi\n"
        'case "$*" in\n'
        "  *\"auth status\"*) printf '%s' "
        '\'{"authenticated":true,"token_valid":true,"refresh_token_valid":true}\' ;;\n'
        '  *) printf \'%s\' \'{"data":{"user_id":"u1"}}\' ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("CEO_DWS_AGENT_CODE", "integration-agent")

    result = DwsChannelGate(binary=str(executable)).check()

    assert result.state is ChannelGateState.READY
    assert len(result.commands) == 2


def test_temporary_lark_executable_classifies_stderr_provider_json(
    tmp_path: Path,
    monkeypatch,
):
    executable = tmp_path / "lark-fixture"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "${CI:-}" != "1" ] || [ "${NO_COLOR:-}" != "1" ]; then\n'
        '  printf \'%s\' \'{"error":{"type":"config","code":"MISSING_ENV"}}\' >&2\n'
        "  exit 3\n"
        "fi\n"
        "printf '%s' '{\"ok\":false}'\n"
        "printf '%s' "
        '\'{"error":{"type":"provider","code":"PROVIDER_UNAVAILABLE"}}\' >&2\n'
        "exit 4\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)

    result = LarkChannelGate(binary=str(executable)).check()

    assert result.state is ChannelGateState.UNAVAILABLE
    assert result.reason_code == "status_provider_unavailable"


def test_dws_gate_uses_local_noninteractive_auth_environment(monkeypatch):
    monkeypatch.setenv("CEO_DWS_AGENT_CODE", "configured-agent")
    runner = ScriptedRunner(
        [
            completed(
                0,
                '{"authenticated":true,"token_valid":true,"refresh_token_valid":true}',
            ),
            completed(0, "{}"),
        ]
    )

    result = DwsChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.READY
    assert len(runner.calls) == 2
    assert all(
        call["env"]["DINGTALK_DWS_AGENTCODE"] == "configured-agent"
        for call in runner.calls
    )
    assert all(call["capture_output"] is True for call in runner.calls)
    assert all(call["text"] is True for call in runner.calls)
    assert all(call["check"] is False for call in runner.calls)
