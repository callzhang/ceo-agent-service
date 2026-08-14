import inspect
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import app.workbench.isolated_home as isolated_home_module
from app.process_runner import ProcessRunResult
from app.workbench.codex_runtime import (
    CodexRuntime,
    _CancellableProcessExecutor,
    _config_without_confirmation_server,
    _isolated_codex_environment,
    _safe_tool_name,
)
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


def write_resume_session(codex_home: Path, content: str = "original session\n") -> Path:
    session_dir = codex_home / "sessions" / "2026" / "08" / "13"
    session_dir.mkdir(parents=True, mode=0o700)
    session = session_dir / f"rollout-2026-08-13T00-00-00-{SESSION_ID}.jsonl"
    session.write_text(content, encoding="utf-8")
    session.chmod(0o600)
    return session


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


def test_command_events_publish_white_box_action_and_result(tmp_path: Path):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "command-white-box",
                "type": "command_execution",
                "command": "rg --files frontend/src",
                "cwd": str(tmp_path),
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "command-white-box",
                "type": "command_execution",
                "aggregated_output": "frontend/src/app.tsx\n",
                "exit_code": 0,
            },
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}},
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    started = next(event for event in events if event.event_type == "tool_started")
    completed = next(event for event in events if event.event_type == "tool_completed")
    assert result.status == "completed"
    assert started.payload_json_value() == {
        "tool_call_id": "tool-call-1",
        "kind": "command",
        "name": "rg",
        "native_id": "command-white-box",
        "status": "running",
        "command": "rg --files frontend/src",
        "cwd": str(tmp_path),
        "provider_item": records[1]["item"],
    }
    assert completed.payload["command"] == "rg --files frontend/src"
    assert completed.payload["cwd"] == str(tmp_path)
    assert completed.payload["output"] == "frontend/src/app.tsx\n"
    assert completed.payload["exit_code"] == 0
    assert completed.payload["status"] == "completed"
    assert completed.payload_json_value()["provider_item"] == {
        **records[1]["item"],
        **records[2]["item"],
    }


def test_mcp_events_publish_exact_identity_arguments_and_result(tmp_path: Path):
    arguments = {"time_min": "2026-08-14T00:00:00+08:00", "calendars": ["primary"]}
    result_payload = {"structuredContent": {"events": [], "next_page_token": "next-1"}}
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "calendar-white-box",
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "google_calendar.search_events",
                "arguments": arguments,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "calendar-white-box",
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "google_calendar.search_events",
                "result": result_payload,
            },
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}},
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    started = next(event for event in events if event.event_type == "tool_started")
    completed = next(event for event in events if event.event_type == "tool_completed")
    started_payload = started.payload_json_value()
    completed_payload = completed.payload_json_value()
    assert result.status == "completed"
    assert started_payload["kind"] == "mcp"
    assert started_payload["name"] == "codex_apps.google_calendar.search_events"
    assert started_payload["server"] == "codex_apps"
    assert started_payload["tool"] == "google_calendar.search_events"
    assert started_payload["arguments"] == arguments
    assert completed_payload["arguments"] == arguments
    assert completed_payload["result"] == result_payload
    assert completed_payload["name"] == "codex_apps.google_calendar.search_events"
    assert completed_payload["provider_item"] == {
        **records[1]["item"],
        **records[2]["item"],
    }


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
    server_overlays = [
        option
        for option in command
        if option.startswith("mcp_servers.workbench_confirmation=")
    ]

    assert 'approval_policy="untrusted"' in command
    assert 'approvals_reviewer="auto_review"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--output-schema" not in command
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command
    assert "workbench_confirmation.request_reviewed_action" in command_text
    assert len(server_overlays) == 1
    overlay = server_overlays[0]
    assert 'command = ' in overlay
    assert 'args = ["-m", "app.workbench.confirmation_mcp"]' in overlay
    assert 'enabled_tools = ["request_reviewed_action"]' in overlay
    assert 'enabled = true' in overlay
    assert all(
        not option.startswith("mcp_servers.workbench_confirmation.")
        for option in command
    )
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
                'model_reasoning_effort = "high"',
                "",
                "[mcp_servers.workbench_confirmation]",
                "enabled = false",
                'command = "conflicting-command"',
                'disabled_tools = ["request_reviewed_action"]',
                'url = "https://conflicting.example/mcp"',
                'bearer_token_env_var = "CONFIRMATION_TOKEN"',
                'env = { CONFIRMATION_TOKEN = "must-not-survive" }',
                'transport = "streamable_http"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    runtime = CodexRuntime(workspace=tmp_path)

    command = runtime.build_command(prompt="inspect", provider_session_ref="")

    assert "--ignore-user-config" not in command
    [overlay] = [
        option
        for option in command
        if option.startswith("mcp_servers.workbench_confirmation=")
    ]
    assert 'enabled = true' in overlay
    assert 'enabled_tools = ["request_reviewed_action"]' in overlay
    assert "conflicting-command" not in overlay
    for forbidden in (" url =", " bearer", " env =", " transport =", " disabled_tools ="):
        assert forbidden not in overlay


def test_confirmation_inline_table_is_accepted_by_real_codex_parser(tmp_path: Path):
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("codex executable is unavailable")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_reasoning_effort = "high"',
                "",
                "[mcp_servers.workbench_confirmation]",
                'url = "https://conflicting.example/mcp"',
                'bearer_token_env_var = "CONFLICTING_TOKEN"',
                'env = { CONFLICTING_TOKEN = "not-a-real-secret" }',
                "enabled = false",
            ]
        ),
        encoding="utf-8",
    )
    skills = codex_home / "skills"
    skills.mkdir()
    (skills / "marker").write_text("preserved", encoding="utf-8")
    runtime = CodexRuntime(workspace=tmp_path, codex_bin=codex)
    command = runtime.build_command(prompt="inspect", provider_session_ref="")
    [overlay] = [
        option
        for option in command
        if option.startswith("mcp_servers.workbench_confirmation=")
    ]

    with _isolated_codex_environment(
        {**os.environ, "CODEX_HOME": str(codex_home)}
    ) as isolated_env:
        isolated_home = Path(isolated_env["CODEX_HOME"])
        assert isolated_home != codex_home
        assert not (isolated_home / "skills").is_symlink()
        assert (isolated_home / "skills" / "marker").read_text(
            encoding="utf-8"
        ) == "preserved"
        assert 'model_reasoning_effort = "high"' in (
            isolated_home / "config.toml"
        ).read_text(encoding="utf-8")
        parsed = subprocess.run(
            [codex, "-c", overlay, "mcp", "get", "workbench_confirmation", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=isolated_env,
        )

    assert parsed.returncode == 0, parsed.stderr
    assert not isolated_home.exists()
    configuration = json.loads(parsed.stdout)
    rendered = json.dumps(configuration)
    assert configuration["enabled"] is True
    assert configuration["enabled_tools"] == ["request_reviewed_action"]
    assert configuration["transport"]["type"] == "stdio"
    assert "conflicting.example" not in rendered
    assert "CONFLICTING_TOKEN" not in rendered


def test_confirmation_config_isolation_preserves_other_tables_and_inline_assignment():
    source = """model = "gpt-example"
[mcp_servers]
workbench_confirmation = { url = "https://conflicting.example/mcp", bearer_token_env_var = "CONFLICTING_TOKEN" }
[mcp_servers.other]
command = "other-server"
"""

    sanitized = _config_without_confirmation_server(source)

    assert 'model = "gpt-example"' in sanitized
    assert '[mcp_servers.other]' in sanitized
    assert 'command = "other-server"' in sanitized
    assert "workbench_confirmation" not in sanitized
    assert "CONFLICTING_TOKEN" not in sanitized


def test_runtime_uses_and_cleans_isolated_codex_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "[mcp_servers.workbench_confirmation]\n"
        'url = "https://conflicting.example/mcp"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    observed_home: Path | None = None

    def executor(command: list[str], **kwargs: object) -> ProcessRunResult:
        nonlocal observed_home
        env = kwargs["env"]
        assert isinstance(env, dict)
        observed_home = Path(env["CODEX_HOME"])
        assert observed_home != codex_home
        assert "workbench_confirmation" not in (
            observed_home / "config.toml"
        ).read_text(encoding="utf-8")
        on_stdout_line = kwargs["on_stdout_line"]
        assert callable(on_stdout_line)
        for record in happy_records():
            on_stdout_line(json.dumps(record))
        return ProcessRunResult(returncode=0, stdout="", stderr="")

    runtime = CodexRuntime(workspace=tmp_path, executor=executor)

    result = runtime.wait(runtime.start(request(tmp_path), on_event=lambda _event: None))

    assert result.status == "completed"
    assert observed_home is not None
    assert not observed_home.exists()


def test_runtime_failure_cleans_isolated_home_without_exposing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    credential = "private-config-material"
    (codex_home / "config.toml").write_text(
        "[mcp_servers.workbench_confirmation]\n"
        f'env = {{ PRIVATE_VALUE = "{credential}" }}\n',
        encoding="utf-8",
    )
    source_session = write_resume_session(codex_home)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    observed_home: Path | None = None

    def executor(_command: list[str], **kwargs: object) -> ProcessRunResult:
        nonlocal observed_home
        env = kwargs["env"]
        assert isinstance(env, dict)
        observed_home = Path(env["CODEX_HOME"])
        [isolated_session] = list(
            (observed_home / "sessions").rglob(f"*{SESSION_ID}.jsonl")
        )
        isolated_session.write_text("failed mutation\n", encoding="utf-8")
        raise RuntimeError("native failure")

    runtime = CodexRuntime(workspace=tmp_path, executor=executor)

    result = runtime.wait(
        runtime.start(
            request(tmp_path, provider_session_ref=SESSION_ID),
            on_event=lambda _event: None,
        )
    )

    assert result.status == "failed"
    assert result.error_code == "runtime_failure"
    assert credential not in result.error_detail
    assert observed_home is not None
    assert not observed_home.exists()
    assert source_session.read_text(encoding="utf-8") == "original session\n"


def test_successful_runtime_atomically_syncs_resumed_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    (codex_home / "config.toml").write_text(
        "[mcp_servers.workbench_confirmation]\n"
        'url = "https://conflicting.example/mcp"\n',
        encoding="utf-8",
    )
    source_session = write_resume_session(codex_home)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    def executor(_command: list[str], **kwargs: object) -> ProcessRunResult:
        env = kwargs["env"]
        assert isinstance(env, dict)
        isolated_home = Path(env["CODEX_HOME"])
        [isolated_session] = list(
            (isolated_home / "sessions").rglob(f"*{SESSION_ID}.jsonl")
        )
        isolated_session.write_text("successful mutation\n", encoding="utf-8")
        on_stdout_line = kwargs["on_stdout_line"]
        assert callable(on_stdout_line)
        for record in happy_records():
            on_stdout_line(json.dumps(record))
        return ProcessRunResult(returncode=0, stdout="", stderr="")

    runtime = CodexRuntime(workspace=tmp_path, executor=executor)

    result = runtime.wait(
        runtime.start(
            request(tmp_path, provider_session_ref=SESSION_ID),
            on_event=lambda _event: None,
        )
    )

    assert result.status == "completed"
    assert source_session.read_text(encoding="utf-8") == "successful mutation\n"


def test_runtime_reports_failure_and_rolls_back_all_sessions_when_sync_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    (codex_home / "config.toml").write_text(
        "[mcp_servers.workbench_confirmation]\n"
        'url = "https://conflicting.example/mcp"\n',
        encoding="utf-8",
    )
    source_session = write_resume_session(codex_home)
    second_source = source_session.parent / "second.jsonl"
    second_source.write_text("second original\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    real_replace = isolated_home_module.os.replace
    stage_replacements = 0

    def fail_second_stage(source_name, destination_name, *args, **kwargs):
        nonlocal stage_replacements
        if str(source_name).startswith(".workbench-sync-stage-"):
            stage_replacements += 1
            if stage_replacements == 2:
                raise OSError("injected sync commit failure")
        return real_replace(source_name, destination_name, *args, **kwargs)

    monkeypatch.setattr(isolated_home_module.os, "replace", fail_second_stage)

    def executor(_command: list[str], **kwargs: object) -> ProcessRunResult:
        env = kwargs["env"]
        assert isinstance(env, dict)
        isolated_home = Path(env["CODEX_HOME"])
        [isolated_session] = list(
            (isolated_home / "sessions").rglob(f"*{SESSION_ID}.jsonl")
        )
        isolated_session.write_text("first changed\n", encoding="utf-8")
        isolated_second = isolated_session.parent / second_source.name
        isolated_second.write_text("second changed\n", encoding="utf-8")
        on_stdout_line = kwargs["on_stdout_line"]
        assert callable(on_stdout_line)
        for record in happy_records():
            on_stdout_line(json.dumps(record))
        return ProcessRunResult(returncode=0, stdout="", stderr="")

    runtime = CodexRuntime(workspace=tmp_path, executor=executor)

    result = runtime.wait(
        runtime.start(
            request(tmp_path, provider_session_ref=SESSION_ID),
            on_event=lambda _event: None,
        )
    )

    assert result.status == "failed"
    assert result.error_code == "runtime_failure"
    assert "injected" not in result.error_detail
    assert source_session.read_text(encoding="utf-8") == "original session\n"
    assert second_source.read_text(encoding="utf-8") == "second original\n"


def test_runtime_stays_completed_when_committed_journal_cleanup_is_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    (codex_home / "config.toml").write_text(
        "[mcp_servers.workbench_confirmation]\n"
        'url = "https://conflicting.example/mcp"\n',
        encoding="utf-8",
    )
    source_session = write_resume_session(codex_home)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    real_unlink = isolated_home_module.os.unlink
    failed_cleanup = False

    def fail_first_backup_unlink(path, *args, **kwargs):
        nonlocal failed_cleanup
        if str(path).startswith(".workbench-sync-backup-") and not failed_cleanup:
            failed_cleanup = True
            raise OSError("injected committed cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(isolated_home_module.os, "unlink", fail_first_backup_unlink)

    def executor(_command: list[str], **kwargs: object) -> ProcessRunResult:
        env = kwargs["env"]
        assert isinstance(env, dict)
        isolated_home = Path(env["CODEX_HOME"])
        [isolated_session] = list(
            (isolated_home / "sessions").rglob(f"*{SESSION_ID}.jsonl")
        )
        isolated_session.write_text("committed mutation\n", encoding="utf-8")
        on_stdout_line = kwargs["on_stdout_line"]
        assert callable(on_stdout_line)
        for record in happy_records():
            on_stdout_line(json.dumps(record))
        return ProcessRunResult(returncode=0, stdout="", stderr="")

    runtime = CodexRuntime(workspace=tmp_path, executor=executor)
    result = runtime.wait(
        runtime.start(
            request(tmp_path, provider_session_ref=SESSION_ID),
            on_event=lambda _event: None,
        )
    )

    journal = codex_home / ".workbench-session-sync" / "journal.json"
    assert result.status == "completed"
    assert failed_cleanup
    assert source_session.read_text(encoding="utf-8") == "committed mutation\n"
    assert json.loads(journal.read_text(encoding="utf-8"))["phase"] == "committed"

    monkeypatch.setattr(isolated_home_module.os, "unlink", real_unlink)
    with isolated_home_module._session_sync_lock(codex_home, tmp_path):
        pass

    assert not journal.exists()
    assert not any(
        path.name.startswith(".workbench-sync-")
        for path in (codex_home / "sessions").rglob("*")
    )


def test_runtime_stop_cleans_isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    (codex_home / "config.toml").write_text(
        "[mcp_servers.workbench_confirmation]\n"
        'url = "https://conflicting.example/mcp"\n',
        encoding="utf-8",
    )
    source_session = write_resume_session(codex_home)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    class IsolatedBlockingExecutor(BlockingExecutor):
        observed_home: Path | None = None

        def __call__(self, command: list[str], **kwargs: object) -> ProcessRunResult:
            env = kwargs["env"]
            assert isinstance(env, dict)
            self.observed_home = Path(env["CODEX_HOME"])
            [isolated_session] = list(
                (self.observed_home / "sessions").rglob(f"*{SESSION_ID}.jsonl")
            )
            isolated_session.write_text("stopped mutation\n", encoding="utf-8")
            return super().__call__(command, **kwargs)

    executor = IsolatedBlockingExecutor()
    runtime = CodexRuntime(workspace=tmp_path, executor=executor)
    handle = runtime.start(
        request(tmp_path, provider_session_ref=SESSION_ID),
        on_event=lambda _event: None,
    )
    assert executor.started.wait(timeout=1)

    runtime.stop(handle)
    result = runtime.wait(handle)

    assert result.status == "stopped"
    assert executor.observed_home is not None
    assert not executor.observed_home.exists()
    assert source_session.read_text(encoding="utf-8") == "original session\n"


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


def test_request_reviewed_action_uses_argument_context_for_opaque_values():
    opaque = "AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="

    proposal = request_reviewed_action(
        ["tool", "--conversation", opaque], opaque, opaque, opaque
    )

    assert proposal["argv"][-1] == opaque
    assert proposal["target"] == opaque
    for argv in (["tool", "--token", opaque], ["tool", f"--password={opaque}"]):
        with pytest.raises(ValueError, match="complete reviewed action details are required"):
            request_reviewed_action(argv, "target", "summary", "risk")


def test_request_reviewed_action_preserves_benign_header_arguments_exactly():
    argv = [
        "curl",
        "--header",
        "Content-Type: application/json",
        "-HX-Correlation-ID: cidAbCDefghIJklMNopQRstUVwxYZ0123456789+/==",
    ]

    proposal = request_reviewed_action(argv, "target", "summary", "risk")

    assert proposal["argv"] == argv


@pytest.mark.parametrize(
    "argv",
    [
        ["curl", "-H", "Authorization: AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
        ["curl", "--header=X-API-Key: AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
    ],
)
def test_request_reviewed_action_rejects_sensitive_header_arguments(argv: list[str]):
    with pytest.raises(ValueError, match="^complete reviewed action details are required$"):
        request_reviewed_action(argv, "target", "summary", "risk")


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


def test_long_opaque_identifier_in_assistant_text_is_not_a_credential(tmp_path: Path):
    conversation_id = "cidAbCDefghIJklMNopQRstUVwxYZ0123456789+/==conversation"
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": conversation_id,
            },
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    assert result.final_text == conversation_id
    assert any(event.payload.get("text") == conversation_id for event in events)


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
        {"error": {"message": "native failure"}},
    ],
)
def test_failed_confirmation_mcp_result_emits_no_confirmation_and_turn_continues(
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
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": "Unable to propose"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    assert not any(event.event_type == "confirmation_required" for event in events)
    [completed] = [event for event in events if event.event_type == "tool_completed"]
    assert completed.payload["status"] == "failed"
    assert "native" not in result.error_detail


@pytest.mark.parametrize(
    "business_content",
    [
        {"task": {"status": "failed"}},
        {"error": "historical incident"},
    ],
)
def test_mcp_business_content_does_not_masquerade_as_wrapper_failure(
    tmp_path: Path, business_content: dict[str, object]
):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {"id": "read-1", "type": "mcp_tool_call", "tool": "read"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "read-1",
                "type": "mcp_tool_call",
                "tool": "read",
                "result": {"isError": False, "structuredContent": business_content},
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": "Done"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    [completed] = [event for event in events if event.event_type == "tool_completed"]
    assert completed.payload["status"] == "completed"


def test_failed_ordinary_mcp_is_correlated_and_does_not_abort_successful_turn(
    tmp_path: Path,
):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {"type": "item.started", "item": {"id": "read-1", "type": "mcp_tool_call", "tool": "read"}},
        {
            "type": "item.completed",
            "item": {
                "id": "read-1",
                "type": "mcp_tool_call",
                "tool": "read",
                "result": {"status": "failed", "error": {"message": "native detail"}},
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": "Recovered",
            },
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    started = next(event for event in events if event.event_type == "tool_started")
    completed = next(event for event in events if event.event_type == "tool_completed")
    assert result.status == "completed"
    assert result.final_text == "Recovered"
    assert completed.payload["status"] == "failed"
    assert completed.payload["tool_call_id"] == started.payload["tool_call_id"]
    assert completed.payload_json_value()["result"] == {
        "status": "failed",
        "error": {"message": "native detail"},
    }


def test_confirmation_structured_content_rejects_extra_business_fields(tmp_path: Path):
    confirmation = {
        "kind": "reviewed_cli",
        "argv": ["tool", "read"],
        "target": "target",
        "summary": "summary",
        "risk": "risk",
        "executed": False,
        "task": {"status": "ok"},
    }
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "confirm",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "confirm",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
                "result": {"structuredContent": confirmation},
            },
        },
    ]
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=lambda _event: None))

    assert result.status == "failed"
    assert result.error_code == "invalid_confirmation"


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
    assert [event.payload["native_id"] for event in tool_events] == [
        "native-a",
        "native-b",
        "native-b",
        "native-a",
    ]
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
            "incomplete_provider_output",
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


def test_calendar_result_cursor_is_preserved_in_white_box_event(tmp_path: Path):
    cursor = "opaque-pagination-value"
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "calendar-1",
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "google_calendar.search_events",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "calendar-1",
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "google_calendar.search_events",
                "result": {
                    "Ok": {
                        "structuredContent": {
                            "events": [],
                            "next_page_token": cursor,
                        }
                    }
                },
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "完成"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    [completed] = [
        event.payload_json_value()
        for event in events
        if event.event_type == "tool_completed"
    ]
    assert completed["name"] == "codex_apps.google_calendar.search_events"
    assert completed["result"]["Ok"]["structuredContent"]["next_page_token"] == cursor


def test_credential_shaped_mcp_result_is_redacted_without_failing_turn(
    tmp_path: Path,
):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "memory-1",
                "type": "mcp_tool_call",
                "server": "memory_connector",
                "tool": "memory_recall",
                "arguments": {"query": "最近的管理话题"},
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "memory-1",
                "type": "mcp_tool_call",
                "server": "memory_connector",
                "tool": "memory_recall",
                "result": {
                    "structuredContent": {
                        "summary": "配置示例 api_key=credential-value-1234",
                        "access_token": "short-secret-value",
                        "source": "/Users/derek/.codex/memories/MEMORY.md",
                    }
                },
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "完成"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    [completed] = [
        event.payload_json_value()
        for event in events
        if event.event_type == "tool_completed"
    ]
    encoded = json.dumps(completed)
    assert "credential-value-1234" not in encoded
    assert "short-secret-value" not in encoded
    assert "[REDACTED]" in encoded
    assert "/Users/derek/.codex/memories/MEMORY.md" in encoded


def test_failed_command_without_provider_diagnostics_explains_the_boundary(
    tmp_path: Path,
):
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "command-rejected",
                "type": "command_execution",
                "command": "/usr/bin/printf opaque",
                "cwd": str(tmp_path),
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "command-rejected",
                "type": "command_execution",
                "aggregated_output": "",
                "exit_code": None,
                "status": "failed",
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "命令未执行"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    completed = next(
        event.payload_json_value()
        for event in events
        if event.event_type == "tool_completed"
    )
    assert completed["status"] == "failed"
    assert completed["summary"] == (
        "Codex Provider 报告命令失败，但未返回退出码或诊断输出。"
    )


def test_credential_in_white_box_command_is_redacted_without_failing_turn(
    tmp_path: Path,
):
    credential = "sk-proj-nativecredential1234"
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "metadata": {"credential": credential},
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "aggregated_output": credential,
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "完成"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    assert credential not in repr(events)
    assert credential not in result.error_detail
    tool_events = [
        event.payload_json_value()
        for event in events
        if event.event_type in {"tool_started", "tool_completed"}
    ]
    assert len(tool_events) == 2
    assert all("[REDACTED]" in json.dumps(event) for event in tool_events)


def test_unsupported_private_item_is_ignored_without_leaking(tmp_path: Path):
    cursor = "opaque-pagination-value"
    credential = "sk-proj-privateitemcredential1234"
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {
                "type": "reasoning",
                "private": {
                    "next_page_token": cursor,
                    "credential": credential,
                },
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "完成"},
        },
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    assert cursor not in repr(events)
    assert credential not in repr(events)
    assert cursor not in result.error_detail
    assert credential not in result.error_detail


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
            "type": "item.started",
            "item": {
                "id": "confirm-credential",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "confirm-credential",
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


def test_uncorrelated_confirmation_completion_fails_before_inspecting_result(
    tmp_path: Path,
):
    credential = "sk-proj-unrelatedconfirmationcredential1234"
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.completed",
            "item": {
                "id": "unstarted-confirmation",
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
                "result": {
                    "structuredContent": {
                        "kind": "reviewed_cli",
                        "argv": ["tool", "--value", credential],
                        "target": "target",
                        "summary": "summary",
                        "risk": "risk",
                        "executed": False,
                    }
                },
            },
        },
    ]
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=lambda _event: None))

    assert result.status == "failed"
    assert result.error_code == "invalid_provider_output"
    assert credential not in result.error_detail


@pytest.mark.parametrize(
    ("item", "expected_label", "private_names"),
    [
        ({"type": "command_execution"}, "command_execution", ()),
        (
            {
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "google_calendar.search_events",
            },
            "codex_apps.google_calendar.search_events",
            ("codex_apps", "google_calendar.search_events"),
        ),
        (
            {
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "gmail.search_emails",
            },
            "codex_apps.gmail.search_emails",
            ("codex_apps", "gmail.search_emails"),
        ),
        (
            {
                "type": "mcp_tool_call",
                "server": "workbench_confirmation",
                "tool": "request_reviewed_action",
            },
            "workbench_confirmation.request_reviewed_action",
            ("workbench_confirmation", "request_reviewed_action"),
        ),
        (
            {
                "type": "mcp_tool_call",
                "server": "private-provider",
                "tool": "private.search_records",
            },
            "private-provider.private.search_records",
            ("private-provider", "private.search_records"),
        ),
    ],
)
def test_safe_tool_name_exposes_exact_provider_identity(
    item: dict[str, str], expected_label: str, private_names: tuple[str, ...]
):
    label = _safe_tool_name(item)

    assert label == expected_label
    assert all(private_name in label for private_name in private_names)


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


def test_watchdog_removes_isolated_home_when_runtime_parent_abruptly_exits(
    tmp_path: Path,
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    credential = "credential-material-must-not-appear"
    (codex_home / "config.toml").write_text(
        'model_provider = "private"\n'
        "[mcp_servers.workbench_confirmation]\n"
        f'env = {{ PRIVATE_VALUE = "{credential}" }}\n',
        encoding="utf-8",
    )
    source_session = write_resume_session(codex_home)
    child_pid_path = tmp_path / "abrupt-child.pid"
    isolated_home_path = tmp_path / "isolated-home.path"
    parent_script = tmp_path / "abrupt_runtime_parent.py"
    parent_script.write_text(
        "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                "from app.workbench.codex_runtime import (",
                "    _CancellableProcessExecutor, _isolated_codex_environment)",
                "base_env = os.environ.copy()",
                "base_env['CODEX_HOME'] = sys.argv[1]",
                "executor = _CancellableProcessExecutor(cwd=Path(sys.argv[2]))",
                "with _isolated_codex_environment(",
                "        base_env, provider_session_ref=sys.argv[5]) as process_env:",
                "    executor.set_isolated_home(process_env.isolated_home)",
                "    [session] = list((Path(process_env['CODEX_HOME']) / 'sessions').rglob('*' + sys.argv[5] + '.jsonl'))",
                "    session.write_text('crash mutation\\n')",
                "    original = executor._start_watchdog",
                "    def observed(process):",
                "        Path(sys.argv[3]).write_text(str(process.pid))",
                "        Path(sys.argv[4]).write_text(process_env['CODEX_HOME'])",
                "        original(process)",
                "        os._exit(23)",
                "    executor._start_watchdog = observed",
                "    executor([sys.executable, '-c', 'import time; time.sleep(30)'], ",
                "        prompt='', env=process_env, total_timeout_seconds=30, ",
                "        idle_timeout_seconds=30, on_stdout_line=lambda line: None)",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    parent = subprocess.Popen(
        [
            sys.executable,
            str(parent_script),
            str(codex_home),
            str(tmp_path),
            str(child_pid_path),
            str(isolated_home_path),
            SESSION_ID,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = parent.communicate(timeout=5)

    assert parent.returncode == 23
    assert child_pid_path.exists()
    assert isolated_home_path.exists()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    isolated_home = Path(isolated_home_path.read_text(encoding="utf-8"))
    try:
        assert _wait_for_pid_exit(child_pid, timeout=5)
        deadline = time.monotonic() + 5
        while isolated_home.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not isolated_home.exists()
        assert credential not in stdout
        assert credential not in stderr
        assert source_session.read_text(encoding="utf-8") == "original session\n"
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
