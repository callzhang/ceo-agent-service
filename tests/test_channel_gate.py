from __future__ import annotations

import subprocess

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
    assert (
        result.detail
        == '{"error.type": "network", "error.message": "service unavailable"}'
    )


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


def test_lark_gate_accepts_empty_json_object_from_successful_probe():
    runner = ScriptedRunner(
        [
            completed(0, '{"authenticated":true}'),
            completed(0, "{}"),
        ]
    )

    result = LarkChannelGate(runner=runner).check()

    assert result.state is ChannelGateState.READY


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
