"""WeChat is driven by one Codex computer-use turn; delivery is not its claim."""

import json
import re
from types import SimpleNamespace

import pytest

from app.agent_runtime_contracts import (
    DESKTOP_COMPUTER_USE,
    CredentialMode,
    RuntimeKind,
    RuntimeRoute,
    runtime_route_surface_capabilities,
)
from app.agent_runtime_router import RoutedCodexExecutionError
from app.store import AutoReplyStore
from app.wechat import codex_runner
from app.wechat.accessibility import SenderExecutionError
from app.wechat.codex_runner import CodexWechatRunner


class Execution:
    def __init__(self, result=None, error=None):
        self.result = result or {"opened_title": "Alex", "pressed_return": True,
                                 "failure_reason": ""}
        self.error = error
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(value=json.dumps(self.result))


@pytest.fixture(autouse=True)
def wechat_ready(monkeypatch):
    monkeypatch.setattr(codex_runner, "_screen_locked", lambda: False)
    monkeypatch.setattr(codex_runner, "_wechat_pid", lambda: 4242)


def _runner(execution):
    return CodexWechatRunner(routed_execution=execution)


def test_send_is_one_codex_computer_use_turn_and_never_claims_delivery():
    execution = Execution()
    result = _runner(execution).send(
        "Alex", "好的\n明天见", expected_recent_text="明天有空吗",
    )

    assert (result.action_performed, result.visible_confirmation) == (True, False)
    call, = execution.calls
    assert call["workload_kind"] == "wechat_ui"
    assert DESKTOP_COMPUTER_USE in call["required_capabilities"]
    assert "好的\n明天见" in call["prompt"]
    assert "明天有空吗" in call["prompt"]
    assert "one character at a time" in call["prompt"]
    assert re.fullmatch(r"wechat-ui:send:[0-9a-f]{64}", call["workload_key"])


def test_the_workload_key_is_one_the_store_accepts():
    AutoReplyStore._validate_runtime_operation_workload(
        "wechat_ui", codex_runner._workload_key("send", "Alex", "hi"),
    )
    with pytest.raises(ValueError):
        AutoReplyStore._validate_runtime_operation_workload("wechat_ui", "wechat-ui:send:x")


def test_a_turn_that_stopped_before_return_is_a_failure_before_action():
    execution = Execution({"opened_title": "Alex", "pressed_return": False,
                           "failure_reason": "composer_text_mismatch"})
    result = _runner(execution).send("Alex", "hi")

    assert result.action_performed is False
    assert result.failure_reason == "composer_text_mismatch"


def test_a_different_chat_is_reported_as_a_binding_mismatch():
    stopped = _runner(Execution({"opened_title": "Alexa", "pressed_return": False,
                                 "failure_reason": "title_mismatch"})).send("Alex", "hi")
    pressed = _runner(Execution({"opened_title": "Alexa", "pressed_return": True,
                                 "failure_reason": ""})).send("Alex", "hi")

    assert (stopped.action_performed, stopped.failure_reason) == (
        False, "target_binding_mismatch")
    assert (pressed.action_performed, pressed.failure_reason) == (
        True, "target_binding_mismatch")


def test_no_available_route_means_nothing_reached_wechat():
    unavailable = RoutedCodexExecutionError("runtime_execution_failed", runtime_unavailable=True)
    broken = RoutedCodexExecutionError("runtime_result_validation_failed")

    with pytest.raises(SenderExecutionError) as before:
        _runner(Execution(error=unavailable)).send("Alex", "hi")
    with pytest.raises(SenderExecutionError) as during:
        _runner(Execution(error=broken)).send("Alex", "hi")

    assert before.value.action_may_have_started is False
    assert during.value.action_may_have_started is True


def test_wechat_not_running_fails_before_any_turn(monkeypatch):
    monkeypatch.setattr(codex_runner, "_wechat_pid", lambda: None)
    execution = Execution()

    result = _runner(execution).send("Alex", "hi")

    assert result.failure_reason == "wechat_not_running"
    assert execution.calls == []


def test_open_and_identify_reports_the_title_without_sending():
    execution = Execution({"opened_title": "Alex", "pressed_return": False,
                           "failure_reason": ""})

    assert _runner(execution).open_and_identify("Alex", search_query="Alex") == "Alex"
    call, = execution.calls
    assert re.fullmatch(r"wechat-ui:open:[0-9a-f]{64}", call["workload_key"])
    assert "Do not type or send anything" in call["prompt"]


def _route(name, kind, mode):
    return RuntimeRoute(name=name, runtime_kind=kind, credential_mode=mode, model="m")


def test_only_codex_signed_in_with_chatgpt_offers_desktop_computer_use():
    oauth = _route("codex_oauth", RuntimeKind.CODEX_CLI, CredentialMode.LOCAL_OAUTH)
    api = _route("qwen", RuntimeKind.CODEX_CLI, CredentialMode.SERVICE_API)
    claude = _route("claude_oauth", RuntimeKind.CLAUDE_CLI, CredentialMode.LOCAL_OAUTH)

    assert DESKTOP_COMPUTER_USE in runtime_route_surface_capabilities(oauth)
    assert DESKTOP_COMPUTER_USE not in runtime_route_surface_capabilities(api)
    assert DESKTOP_COMPUTER_USE not in runtime_route_surface_capabilities(claude)
