import inspect
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.process_runner import ProcessRunResult
from app.workbench.codex_runtime import CodexRuntime, _CancellableProcessExecutor
from app.workbench.confirmation_mcp import request_reviewed_action
from app.workbench.runtime import RuntimeRequest, _runtime_owner


SESSION_ID = "019ff6ad-c139-7411-9169-6220e8b39688"
OTHER_SESSION_ID = "019ff6ad-c139-7411-9169-6220e8b39689"

class FakeProcessExecutor:
    def __init__(self, records: list[object], *, returncode: int = 0):
        self.records = records
        self.returncode = returncode
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> ProcessRunResult:
        self.commands.append(command)
        on_stdout_line = kwargs["on_stdout_line"]
        assert callable(on_stdout_line)
        for record in self.records:
            line = record if isinstance(record, str) else json.dumps(record)
            on_stdout_line(line)
        return ProcessRunResult(
            returncode=self.returncode,
            stdout="",
            stderr="",
        )


def request(tmp_path: Path, **overrides: object) -> RuntimeRequest:
    values: dict[str, object] = {
        "turn_id": "turn-1",
        "workspace": tmp_path,
        "prompt": "inspect the repo",
        "provider_session_ref": "",
        "model": "",
        "attachment_paths": (),
        "image_paths": (),
    }
    values.update(overrides)
    return RuntimeRequest(**values)


def happy_records() -> list[dict[str, object]]:
    return [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "call-1",
                "type": "command_execution",
                "command": "pwd",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "call-1",
                "type": "command_execution",
                "aggregated_output": "/private/workspace",
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Done"},
        },
        {"type": "turn.completed", "response": {"output_text": "Done"}},
    ]


def test_codex_runtime_streams_text_tools_and_session(tmp_path: Path):
    executor = FakeProcessExecutor(happy_records())
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=executor)

    handle = runtime.start(request(tmp_path), on_event=events.append)
    result = runtime.wait(handle)

    assert [event.event_type for event in events] == [
        "status_changed",
        "tool_started",
        "tool_completed",
        "text_delta",
    ]
    assert result.status == "completed"
    assert result.provider_session_ref == SESSION_ID
    assert result.final_text == "Done"
    assert all(SESSION_ID not in repr(event) for event in events)
    with pytest.raises(ValueError, match="owner is unavailable"):
        _runtime_owner(handle)


def test_codex_resume_command_keeps_provider_reference_process_private(tmp_path: Path):
    executor = FakeProcessExecutor(happy_records())
    runtime = CodexRuntime(workspace=tmp_path, executor=executor)
    events = []

    command = runtime.build_command(
        prompt="continue", provider_session_ref=SESSION_ID
    )
    result = runtime.wait(
        runtime.start(
            request(tmp_path, provider_session_ref=SESSION_ID),
            on_event=events.append,
        )
    )

    assert command[:3] == ["codex", "exec", "resume"]
    assert SESSION_ID in command
    assert executor.commands[0][:3] == ["codex", "exec", "resume"]
    assert SESSION_ID in executor.commands[0]
    assert SESSION_ID not in repr(events)
    assert SESSION_ID not in result.error_detail


def test_command_uses_safe_confirmation_overlay_and_supported_inputs(tmp_path: Path):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    runtime = CodexRuntime(workspace=tmp_path)
    command = runtime.build_command(
        prompt="inspect",
        provider_session_ref="",
        model="gpt-example",
        image_paths=[image],
    )
    command_text = " ".join(command)

    assert 'approval_policy="untrusted"' in command
    assert 'approvals_reviewer="auto_review"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--output-schema" not in command
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command
    assert "workbench_confirmation.request_reviewed_action" in command_text
    assert "mcp_servers.workbench_confirmation.command=" in command_text
    assert "mcp_servers.workbench_confirmation.args=" in command_text
    assert "mcp_servers.workbench_confirmation.enabled_tools=" in command_text
    assert 'mcp_servers.workbench_confirmation.enabled=true' in command
    assert 'mcp_servers.workbench_confirmation.disabled_tools=[]' in command
    assert "-m" in command and "gpt-example" in command
    assert "--image" in command and str(image) in command


def test_confirmation_overlay_overrides_inherited_disable_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.workbench_confirmation]",
                "enabled = false",
                'command = "conflicting-command"',
                'disabled_tools = ["request_reviewed_action"]',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    runtime = CodexRuntime(workspace=tmp_path)

    command = runtime.build_command(prompt="inspect", provider_session_ref="")

    assert "--ignore-user-config" not in command
    assert 'mcp_servers.workbench_confirmation.enabled=true' in command
    assert 'mcp_servers.workbench_confirmation.disabled_tools=[]' in command
    assert (
        'mcp_servers.workbench_confirmation.enabled_tools=["request_reviewed_action"]'
        in command
    )
    assert any(
        option.startswith("mcp_servers.workbench_confirmation.command=")
        and "conflicting-command" not in option
        for option in command
    )


@pytest.mark.parametrize(
    "provider_session_ref",
    ["--last", "not-a-uuid", SESSION_ID.upper(), f" {SESSION_ID}"],
)
def test_resume_rejects_noncanonical_or_option_shaped_session_refs(
    tmp_path: Path, provider_session_ref: str
):
    runtime = CodexRuntime(workspace=tmp_path)

    with pytest.raises(ValueError, match="invalid provider session reference"):
        runtime.build_command(prompt="continue", provider_session_ref=provider_session_ref)


def test_provider_output_rejects_noncanonical_session_ref_without_leak(tmp_path: Path):
    native_ref = "--last"
    runtime = CodexRuntime(
        workspace=tmp_path,
        executor=FakeProcessExecutor(
            [{"type": "thread.started", "thread_id": native_ref}]
        ),
    )

    result = runtime.wait(runtime.start(request(tmp_path), on_event=lambda _event: None))

    assert result.status == "failed"
    assert result.error_code == "invalid_provider_session"
    assert native_ref not in result.error_detail


def test_images_must_be_regular_files_under_approved_roots(tmp_path: Path):
    workspace = tmp_path / "workspace"
    uploads = tmp_path / "uploads"
    outside = tmp_path / "outside"
    workspace.mkdir()
    uploads.mkdir()
    outside.mkdir()
    inside_image = workspace / "inside.png"
    upload_image = uploads / "upload.png"
    outside_image = outside / "outside.png"
    inside_image.write_bytes(b"inside")
    upload_image.write_bytes(b"upload")
    outside_image.write_bytes(b"outside")
    symlink = workspace / "linked.png"
    symlink.symlink_to(upload_image)
    runtime = CodexRuntime(workspace=workspace, approved_input_roots=(uploads,))

    inside_command = runtime.build_command(
        prompt="inspect", provider_session_ref="", image_paths=[inside_image]
    )
    upload_command = runtime.build_command(
        prompt="inspect", provider_session_ref="", image_paths=[upload_image]
    )

    assert str(inside_image) in inside_command
    assert str(upload_image) in upload_command
    for invalid in (
        outside_image,
        symlink,
        workspace / "missing.png",
        Path("../outside/outside.png"),
    ):
        with pytest.raises(ValueError, match="invalid image input"):
            runtime.build_command(
                prompt="inspect", provider_session_ref="", image_paths=[invalid]
            )


def test_request_reviewed_action_is_data_only_and_rejects_sensitive_names():
    proposal = request_reviewed_action(
        ["dws", "chat", "message", "send", "--group", "cid-1"],
        " Executive group ",
        " Send the reviewed update ",
        " External message ",
    )

    assert proposal == {
        "kind": "reviewed_cli",
        "argv": ["dws", "chat", "message", "send", "--group", "cid-1"],
        "target": "Executive group",
        "summary": "Send the reviewed update",
        "risk": "External message",
        "executed": False,
    }
    for argv in (
        ["tool", "--token=value"],
        ["tool", "--password", "opaque"],
        ["tool", 'payload={"api_key":"opaque"}'],
        ["tool", "--value", "sk-proj-credentialvalue1234"],
    ):
        with pytest.raises(ValueError, match="complete reviewed action details are required"):
            request_reviewed_action(argv, "target", "summary", "risk")


@pytest.mark.parametrize("field", ["target", "summary", "risk"])
def test_request_reviewed_action_rejects_credentials_in_display_fields(field: str):
    details = {"target": "target", "summary": "summary", "risk": "risk"}
    details[field] = "Bearer credentialvalue1234"

    with pytest.raises(ValueError, match="^complete reviewed action details are required$"):
        request_reviewed_action(["tool", "--safe", "value"], **details)


def test_request_reviewed_action_rejects_incomplete_details_and_never_executes():
    with pytest.raises(ValueError, match="^complete reviewed action details are required$"):
        request_reviewed_action([], "target", "summary", "risk")
    with pytest.raises(ValueError, match="^complete reviewed action details are required$"):
        request_reviewed_action(["dws"], " ", "summary", "risk")

    for argv in (["tool", "   "], ["tool", 7]):
        with pytest.raises(
            ValueError, match="^complete reviewed action details are required$"
        ):
            request_reviewed_action(argv, "target", "summary", "risk")  # type: ignore[arg-type]

    source = inspect.getsource(
        __import__("app.workbench.confirmation_mcp", fromlist=["confirmation_mcp"])
    )
    assert "import subprocess" not in source
    assert "app.agent_cli" not in source
    assert "dws_client" not in source
    assert "external_connector" not in source


def test_request_reviewed_action_preserves_valid_argv_exactly():
    argv = ["tool", "--label", " value with intentional whitespace "]

    proposal = request_reviewed_action(argv, "target", "summary", "risk")

    assert proposal["argv"] == argv


def test_delta_and_completed_message_emit_logical_text_once(tmp_path: Path):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.delta",
            "item": {
                "id": "message-1",
                "type": "assistant_message",
                "text": "Inspecting. ",
            },
        },
        {
            "type": "item.delta",
            "item": {"id": "message-1", "type": "assistant_message", "text": "Do"},
        },
        {
            "type": "item.delta",
            "item": {"id": "message-1", "type": "assistant_message", "text": "ne"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "message-1",
                "type": "assistant_message",
                "text": "Inspecting. Done",
            },
        },
        {"type": "turn.completed", "response": {"output_text": "Inspecting. Done"}},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert [event.payload["text"] for event in events if event.event_type == "text_delta"] == [
        "Inspecting. ",
        "Do",
        "ne",
    ]
    assert result.final_text == "Inspecting. Done"


def test_completed_only_assistant_message_streams_text(tmp_path: Path):
    events = []
    runtime = CodexRuntime(
        workspace=tmp_path,
        executor=FakeProcessExecutor(happy_records()),
    )

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert [event.payload["text"] for event in events if event.event_type == "text_delta"] == [
        "Done"
    ]
    assert result.final_text == "Done"


def test_confirmation_completion_emits_only_validated_safe_fields(tmp_path: Path):
    confirmation = {
        "kind": "reviewed_cli",
        "argv": ["dws", "chat", "message", "send", "--group", "cid-1"],
        "target": "Executive group",
        "summary": "Send update",
        "risk": "External message",
        "executed": False,
    }
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "confirm-1",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "confirm-1",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
                "arguments": {"unused": "not projected"},
                "result": {"structuredContent": confirmation, "ignored": "native"},
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Waiting for review"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    confirmation_events = [
        event for event in events if event.event_type == "confirmation_required"
    ]
    assert len(confirmation_events) == 1
    assert confirmation_events[0].payload_json_value() == confirmation
    assert result.status == "completed"


@pytest.mark.parametrize(
    "failure_result",
    [
        {"isError": True},
        {"status": "failed"},
        {"result": {"isError": True}},
        {"content": [{"result": {"isError": True}}]},
        {"error": {"message": "native failure"}},
    ],
)
def test_failed_confirmation_mcp_result_is_rejected_without_native_detail(
    tmp_path: Path, failure_result: dict[str, object]
):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "confirm-failed",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "confirm-failed",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
                "result": failure_result,
            },
        },
    ]
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=lambda _event: None))

    assert result.status == "failed"
    assert result.error_code == "provider_tool_failed"
    assert "native" not in result.error_detail


def test_text_dedup_is_scoped_to_native_item_id(tmp_path: Path):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.delta",
            "item": {
                "id": "message-a",
                "type": "assistant_message",
                "text": "Done",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "message-a",
                "type": "assistant_message",
                "text": "Done",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "message-b",
                "type": "assistant_message",
                "text": "Done",
            },
        },
        {"type": "turn.completed", "response": {"output_text": "Done"}},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert [event.payload["text"] for event in events if event.event_type == "text_delta"] == [
        "Done",
        "Done",
    ]
    assert result.final_text == "Done"


def test_tool_events_use_correlated_opaque_ids_for_interleaved_calls(tmp_path: Path):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {"id": "native-a", "type": "command_execution"},
        },
        {
            "type": "item.started",
            "item": {"id": "native-b", "type": "mcp_tool_call", "tool": "read"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "native-b",
                "type": "mcp_tool_call",
                "tool": "read",
                "result": {"isError": False},
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "native-a", "type": "command_execution"},
        },
        {
            "type": "item.completed",
            "item": {"id": "message", "type": "agent_message", "text": "Done"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    tool_events = [event for event in events if event.event_type.startswith("tool_")]
    ids = [event.payload["tool_call_id"] for event in tool_events]
    assert ids[0] == ids[3]
    assert ids[1] == ids[2]
    assert ids[0] != ids[1]
    assert "native-a" not in repr(tool_events)
    assert "native-b" not in repr(tool_events)
    assert result.status == "completed"


def test_unknown_tool_completion_fails_safely(tmp_path: Path):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {"id": "unknown", "type": "command_execution"},
        },
    ]
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=lambda _event: None))

    assert result.status == "failed"
    assert result.error_code == "invalid_provider_output"
    assert "unknown" not in result.error_detail


def test_native_turn_failed_and_post_terminal_data_fail_safely(tmp_path: Path):
    failed = CodexRuntime(
        workspace=tmp_path,
        executor=FakeProcessExecutor(
            [
                {"type": "thread.started", "thread_id": SESSION_ID},
                {"type": "turn.failed", "error": {"message": "native detail"}},
            ]
        ),
    )
    post_terminal = CodexRuntime(
        workspace=tmp_path,
        executor=FakeProcessExecutor(
            [
                *happy_records(),
                {
                    "type": "item.completed",
                    "item": {"id": "late", "type": "agent_message", "text": "late"},
                },
            ]
        ),
    )

    failed_result = failed.wait(
        failed.start(request(tmp_path), on_event=lambda _event: None)
    )
    post_result = post_terminal.wait(
        post_terminal.start(request(tmp_path), on_event=lambda _event: None)
    )

    assert failed_result.status == "failed"
    assert failed_result.error_code == "provider_turn_failed"
    assert "native" not in failed_result.error_detail
    assert post_result.status == "failed"
    assert post_result.error_code == "invalid_provider_output"


@pytest.mark.parametrize(
    "records,error_code",
    [
        (
            [
                {"type": "thread.started", "thread_id": SESSION_ID},
                "not json",
            ],
            "invalid_provider_output",
        ),
        (
            [
                {"type": "thread.started", "thread_id": SESSION_ID},
                {"type": "thread.started", "thread_id": OTHER_SESSION_ID},
            ],
            "conflicting_provider_session",
        ),
        (
            [
                {"type": "thread.started", "thread_id": SESSION_ID},
                {"type": "item.completed", "item": {"api_token": "opaque"}},
            ],
            "sensitive_provider_output",
        ),
        (
                [
                    {"type": "thread.started", "thread_id": SESSION_ID},
                    {
                        "type": "item.started",
                        "item": {
                            "id": "invalid-confirmation",
                            "type": "mcp_tool_call",
                            "server": "workbench_confirmation",
                            "tool": "request_reviewed_action",
                        },
                    },
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "invalid-confirmation",
                        "type": "mcp_tool_call",
                        "server": "workbench_confirmation",
                        "tool": "request_reviewed_action",
                        "result": {
                            "structuredContent": {
                                "kind": "reviewed_cli",
                                "argv": ["tool"],
                                "target": "target",
                                "summary": "summary",
                                "risk": "risk",
                                "executed": True,
                            }
                        },
                    },
                },
            ],
            "invalid_confirmation",
        ),
    ],
)
def test_unsafe_or_invalid_provider_output_fails_without_leaks(
    tmp_path: Path, records: list[object], error_code: str
):
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "failed"
    assert result.error_code == error_code
    assert SESSION_ID not in result.error_detail
    assert OTHER_SESSION_ID not in result.error_detail
    assert "opaque" not in result.error_detail
    assert all("session-" not in repr(event) for event in events)


def test_credential_bearing_assistant_text_never_enters_events_or_errors(
    tmp_path: Path,
):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "api_token=credential-value-1234",
            },
        },
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "failed"
    assert result.error_code == "sensitive_provider_output"
    assert "credential-value-1234" not in result.error_detail
    assert "credential-value-1234" not in repr(events)


@pytest.mark.parametrize(
    "sensitive_leaf",
    [
        "sk-proj-nestedcredential1234",
        "Bearer nestedcredential1234",
        "service_token=nestedcredential1234",
    ],
)
def test_credential_in_any_nested_provider_string_is_rejected_before_emission(
    tmp_path: Path, sensitive_leaf: str
):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "metadata": {"nested": ["safe", {"value": sensitive_leaf}]},
            },
        },
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "failed"
    assert result.error_code == "sensitive_provider_output"
    assert sensitive_leaf not in result.error_detail
    assert sensitive_leaf not in repr(events)


def test_confirmation_argv_credential_value_is_rejected_without_leak(tmp_path: Path):
    credential = "sk-proj-confirmationcredential1234"
    confirmation = {
        "kind": "reviewed_cli",
        "argv": ["tool", "--value", credential],
        "target": "target",
        "summary": "summary",
        "risk": "risk",
        "executed": False,
    }
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
                "result": {"structuredContent": confirmation},
            },
        },
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "failed"
    assert result.error_code == "sensitive_provider_output"
    assert credential not in result.error_detail
    assert credential not in repr(events)


def test_bounded_preamble_is_allowed_but_oversized_line_and_output_fail(tmp_path: Path):
    preamble_runtime = CodexRuntime(
        workspace=tmp_path,
        executor=FakeProcessExecutor(["Codex starting", *happy_records()]),
    )
    preamble_result = preamble_runtime.wait(
        preamble_runtime.start(request(tmp_path), on_event=lambda _event: None)
    )
    oversized_runtime = CodexRuntime(
        workspace=tmp_path,
        executor=FakeProcessExecutor(["x" * (2 * 1024 * 1024 + 1)]),
    )
    oversized_result = oversized_runtime.wait(
        oversized_runtime.start(request(tmp_path), on_event=lambda _event: None)
    )

    assert preamble_result.status == "completed"
    assert oversized_result.status == "failed"
    assert oversized_result.error_code == "provider_output_limit"
    assert len(oversized_result.error_detail) < 200


class BlockingExecutor:
    def __init__(self):
        self.started = threading.Event()
        self.released = threading.Event()
        self.stop_calls = 0

    def __call__(self, command: list[str], **kwargs: object) -> ProcessRunResult:
        self.started.set()
        self.released.wait(timeout=5)
        return ProcessRunResult(returncode=-15, stdout="", stderr="")

    def stop(self) -> None:
        self.stop_calls += 1
        self.released.set()


def test_stop_terminates_only_owned_execution_and_is_idempotent(tmp_path: Path):
    executor = BlockingExecutor()
    runtime = CodexRuntime(workspace=tmp_path, executor=executor)
    handle = runtime.start(request(tmp_path), on_event=lambda _event: None)
    assert executor.started.wait(timeout=1)

    runtime.stop(handle)
    runtime.stop(handle)
    result = runtime.wait(handle)
    runtime.stop(handle)

    assert executor.stop_calls == 1
    assert result.status == "stopped"
    with pytest.raises(ValueError, match="owner is unavailable"):
        _runtime_owner(handle)


def _wait_for_pid_exit(pid: int, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _kill_test_child_if_alive(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _leader_with_inherited_pipe_child() -> list[str]:
    script = (
        "import subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)']); "
        "print(child.pid, flush=True)"
    )
    return [sys.executable, "-c", script]


def _leader_with_detached_stdio_child() -> list[str]:
    script = (
        "import subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "print(child.pid, flush=True)"
    )
    return [sys.executable, "-c", script]


def test_owned_executor_records_spawned_pid_without_live_pgid_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fail_getpgid(_pid: int) -> int:
        raise AssertionError("live process-group lookup is racy")

    monkeypatch.setattr(os, "getpgid", fail_getpgid)
    executor = _CancellableProcessExecutor(cwd=tmp_path)

    result = executor(
        [sys.executable, "-c", "pass"],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
        on_stdout_line=lambda _line: None,
    )

    assert result.returncode == 0


def test_repeated_fast_exit_runs_do_not_leak_parent_file_descriptors(tmp_path: Path):
    initial_fd_count = len(os.listdir("/dev/fd"))

    for _ in range(12):
        result = _CancellableProcessExecutor(cwd=tmp_path)(
            [sys.executable, "-c", "pass"],
            prompt="",
            env=None,
            total_timeout_seconds=5,
            idle_timeout_seconds=5,
            on_stdout_line=lambda _line: None,
        )
        assert result.returncode == 0

    assert len(os.listdir("/dev/fd")) <= initial_fd_count


def test_owned_executor_cleans_inherited_pipe_child_after_leader_exit(tmp_path: Path):
    executor = _CancellableProcessExecutor(cwd=tmp_path)
    child_pids: list[int] = []
    started_at = time.monotonic()

    result = executor(
        _leader_with_inherited_pipe_child(),
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
        on_stdout_line=lambda line: child_pids.append(int(line)),
    )

    assert time.monotonic() - started_at < 3
    assert result.returncode == 0
    assert len(child_pids) == 1
    try:
        assert _wait_for_pid_exit(child_pids[0])
    finally:
        _kill_test_child_if_alive(child_pids[0])


def test_owned_executor_cleans_child_that_closed_inherited_streams(tmp_path: Path):
    executor = _CancellableProcessExecutor(cwd=tmp_path)
    child_pids: list[int] = []

    result = executor(
        _leader_with_detached_stdio_child(),
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
        on_stdout_line=lambda line: child_pids.append(int(line)),
    )

    assert result.returncode == 0
    assert len(child_pids) == 1
    try:
        assert _wait_for_pid_exit(child_pids[0])
    finally:
        _kill_test_child_if_alive(child_pids[0])


def test_owned_executor_stop_cleans_child_after_leader_exit_idempotently(
    tmp_path: Path,
):
    executor = _CancellableProcessExecutor(cwd=tmp_path)
    child_pids: list[int] = []
    callback_entered = threading.Event()
    release_callback = threading.Event()
    completed: list[ProcessRunResult] = []

    def capture_child(line: str) -> None:
        child_pids.append(int(line))
        callback_entered.set()
        release_callback.wait(timeout=3)

    thread = threading.Thread(
        target=lambda: completed.append(
            executor(
                _leader_with_inherited_pipe_child(),
                prompt="",
                env=None,
                total_timeout_seconds=5,
                idle_timeout_seconds=5,
                on_stdout_line=capture_child,
            )
        )
    )
    thread.start()
    assert callback_entered.wait(timeout=1)
    assert len(child_pids) == 1
    leader = executor._process
    assert isinstance(leader, subprocess.Popen)
    assert leader.wait(timeout=1) == 0

    started_at = time.monotonic()
    executor.stop()
    executor.stop()
    release_callback.set()
    thread.join(timeout=3)

    assert time.monotonic() - started_at < 3
    assert not thread.is_alive()
    assert len(completed) == 1
    try:
        assert _wait_for_pid_exit(child_pids[0])
    finally:
        release_callback.set()
        _kill_test_child_if_alive(child_pids[0])


def test_prompt_delivery_is_bounded_when_child_never_reads_stdin(tmp_path: Path):
    executor = _CancellableProcessExecutor(cwd=tmp_path)
    started_at = time.monotonic()

    result = executor(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        prompt="x" * (256 * 1024),
        env=None,
        total_timeout_seconds=0.3,
        idle_timeout_seconds=5,
        on_stdout_line=lambda _line: None,
    )

    assert time.monotonic() - started_at < 2
    assert result.timed_out is True
    assert result.timeout_kind == "total"


def test_prompt_larger_than_safe_limit_fails_before_spawn(tmp_path: Path):
    executor = _CancellableProcessExecutor(cwd=tmp_path)

    with pytest.raises(ValueError, match="prompt exceeds safe byte limit"):
        executor(
            [sys.executable, "-c", "pass"],
            prompt="x" * (1024 * 1024 + 1),
            env=None,
            total_timeout_seconds=5,
            idle_timeout_seconds=5,
            on_stdout_line=lambda _line: None,
        )


def test_runtime_reports_safe_prompt_limit_failure(tmp_path: Path):
    runtime = CodexRuntime(workspace=tmp_path)
    handle = runtime.start(
        request(tmp_path, prompt="x" * (1024 * 1024 + 1)),
        on_event=lambda _event: None,
    )

    result = runtime.wait(handle)

    assert result.status == "failed"
    assert result.error_code == "prompt_limit"
    assert result.error_detail == "prompt exceeds safe byte limit"


def test_selector_setup_failure_reaps_owned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured_pids: list[int] = []
    real_popen = subprocess.Popen

    def capture_popen(*args: object, **kwargs: object):
        process = real_popen(*args, **kwargs)
        captured_pids.append(process.pid)
        return process

    class BrokenSelector:
        def register(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("selector setup failed")

        def close(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    monkeypatch.setattr(selectors, "DefaultSelector", BrokenSelector)
    executor = _CancellableProcessExecutor(cwd=tmp_path)

    with pytest.raises(RuntimeError, match="selector setup failed"):
        executor(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            prompt="",
            env=None,
            total_timeout_seconds=5,
            idle_timeout_seconds=5,
            on_stdout_line=lambda _line: None,
        )

    assert captured_pids
    assert _wait_for_pid_exit(captured_pids[0])


def test_concurrent_wait_rejects_second_waiter_before_blocking(tmp_path: Path):
    executor = BlockingExecutor()
    runtime = CodexRuntime(workspace=tmp_path, executor=executor)
    handle = runtime.start(request(tmp_path), on_event=lambda _event: None)
    assert executor.started.wait(timeout=1)
    first_results: list[object] = []
    first = threading.Thread(target=lambda: first_results.append(runtime.wait(handle)))
    first.start()
    time.sleep(0.05)

    started_at = time.monotonic()
    with pytest.raises(ValueError, match="runtime handle is already being waited"):
        runtime.wait(handle)

    assert time.monotonic() - started_at < 0.5
    runtime.stop(handle)
    first.join(timeout=2)
    assert len(first_results) == 1


def test_watchdog_kills_owned_group_when_runtime_parent_dies(tmp_path: Path):
    child_pid_path = tmp_path / "child.pid"
    parent_script = tmp_path / "runtime_parent.py"
    parent_script.write_text(
        "\n".join(
            [
                "import os, sys, time",
                "from pathlib import Path",
                "from app.workbench.codex_runtime import _CancellableProcessExecutor",
                "executor = _CancellableProcessExecutor(cwd=Path(sys.argv[1]))",
                "original = executor._start_watchdog",
                "def observed(*args, **kwargs):",
                "    Path(sys.argv[2]).write_text(str(executor._process.pid))",
                "    return original(*args, **kwargs)",
                "executor._start_watchdog = observed",
                "executor([sys.executable, '-c', 'import time; time.sleep(30)'], "
                "prompt='', env=None, total_timeout_seconds=30, "
                "idle_timeout_seconds=30, on_stdout_line=lambda line: None)",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    parent = subprocess.Popen(
        [sys.executable, str(parent_script), str(tmp_path), str(child_pid_path)],
        env=env,
    )
    deadline = time.monotonic() + 3
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text())

    parent.kill()
    parent.wait(timeout=2)

    try:
        assert _wait_for_pid_exit(child_pid, timeout=4)
    finally:
        _kill_test_child_if_alive(child_pid)


def test_wait_releases_owner_after_failure(tmp_path: Path):
    runtime = CodexRuntime(
        workspace=tmp_path,
        executor=FakeProcessExecutor(
            [{"type": "thread.started", "thread_id": SESSION_ID}, "bad"]
        ),
    )
    handle = runtime.start(request(tmp_path), on_event=lambda _event: None)

    assert runtime.wait(handle).status == "failed"
    with pytest.raises(ValueError, match="owner is unavailable"):
        _runtime_owner(handle)


def test_workspace_must_stay_within_runtime_boundary(tmp_path: Path):
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor([]))
    outside = tmp_path.parent / "outside"

    with pytest.raises(ValueError, match="workspace is outside runtime boundary"):
        runtime.start(request(outside), on_event=lambda _event: None)


def test_unsupported_generic_attachments_are_rejected(tmp_path: Path):
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor([]))

    with pytest.raises(ValueError, match="attachments are not supported"):
        runtime.start(
            request(tmp_path, attachment_paths=(tmp_path / "report.txt",)),
            on_event=lambda _event: None,
        )
