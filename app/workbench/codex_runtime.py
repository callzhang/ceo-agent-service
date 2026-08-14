"""Codex JSONL adapter for the provider-neutral workbench runtime."""

from __future__ import annotations

import codecs
import contextlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.codex_runner import CodexRunner
from app.leak_check import (
    assert_no_credentials,
    contains_credential,
    redact_credentials_in_value,
)
from app.process_runner import ProcessRunResult
from app.workbench.confirmation_mcp import _validate_argv
from app.workbench.isolated_home import IsolatedCodexHome, create_isolated_codex_home
from app.workbench.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeHandle,
    RuntimeRequest,
    RuntimeResult,
    _release_runtime_owner,
    _runtime_owner,
)


_MAX_PREAMBLE_BYTES = 8 * 1024
_DEFAULT_TOTAL_TIMEOUT_SECONDS = 1200
_DEFAULT_IDLE_TIMEOUT_SECONDS = 900
_MAX_PROVIDER_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_PROMPT_BYTES = 1024 * 1024
_CONFIRMATION_SERVER = "workbench_confirmation"
_CONFIRMATION_TOOL = "request_reviewed_action"
_WORKBENCH_MCP_STARTUP_TIMEOUT_SECONDS = 120
_DEVELOPER_INSTRUCTIONS = """
You are running inside the local Agent Workbench. Preserve the user's configured
Codex skills, rules, plugins, and authenticated read tools. You may perform local,
reversible work directly. For every reviewed external write, deletion, destructive
operation, important overwrite, approval decision, or message send, call
workbench_confirmation.request_reviewed_action with the proposed argv, target,
summary, and risk. Never execute such an action directly. The confirmation tool
only records a proposal and never performs the action.
""".strip()


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return "{ " + ", ".join(
            f"{key} = {_toml_value(item)}" for key, item in value.items()
        ) + " }"
    raise TypeError("unsupported TOML overlay value")


def _confirmation_server_overlay() -> str:
    server = {
        "command": sys.executable,
        "args": ["-m", "app.workbench.confirmation_mcp"],
        "cwd": str(Path(__file__).resolve().parents[2]),
        "enabled": True,
        "enabled_tools": [_CONFIRMATION_TOOL],
    }
    return f"mcp_servers.{_CONFIRMATION_SERVER}={_toml_value(server)}"


_TOML_TABLE_HEADER = re.compile(r"^\s*\[\[?([^\[\]]+)\]\]?\s*(?:#.*)?$")
_CONFIRMATION_ASSIGNMENT = re.compile(
    r"^\s*(?:mcp_servers\s*\.\s*)?[\"']?workbench_confirmation[\"']?\s*="
)
_MCP_SERVER_CONFIG_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_CODEX_CONFIG_BYTES = 4 * 1024 * 1024


def _toml_table_path(header: str) -> tuple[str, ...]:
    return tuple(part.strip().strip("\"'") for part in header.split("."))


def _toml_fragment_is_complete(fragment: str, *, in_mcp_table: bool) -> bool:
    candidate = f"[mcp_servers]\n{fragment}" if in_mcp_table else fragment
    try:
        tomllib.loads(candidate)
    except tomllib.TOMLDecodeError:
        return False
    return True


def _config_without_confirmation_server(source: str) -> str:
    """Remove exactly one inherited server definition while preserving source text."""
    # Reject invalid input before transforming it, without ever echoing its contents.
    parsed = tomllib.loads(source)
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, Mapping) or _CONFIRMATION_SERVER not in servers:
        return source

    output: list[str] = []
    current_table: tuple[str, ...] = ()
    suppress_section = False
    assignment_fragment: list[str] | None = None
    assignment_in_mcp_table = False
    for line in source.splitlines(keepends=True):
        if assignment_fragment is not None:
            assignment_fragment.append(line)
            fragment = "".join(assignment_fragment)
            if _toml_fragment_is_complete(
                fragment, in_mcp_table=assignment_in_mcp_table
            ):
                assignment_fragment = None
            continue

        header_match = _TOML_TABLE_HEADER.match(line)
        if header_match:
            current_table = _toml_table_path(header_match.group(1))
            suppress_section = current_table[:2] == (
                "mcp_servers",
                _CONFIRMATION_SERVER,
            )
            if not suppress_section:
                output.append(line)
            continue
        if suppress_section:
            continue

        in_mcp_table = current_table == ("mcp_servers",)
        root_assignment = not current_table and re.match(
            r"^\s*mcp_servers\s*\.\s*[\"']?workbench_confirmation[\"']?\s*=",
            line,
        )
        parent_assignment = in_mcp_table and _CONFIRMATION_ASSIGNMENT.match(line)
        if root_assignment or parent_assignment:
            if not _toml_fragment_is_complete(line, in_mcp_table=in_mcp_table):
                assignment_fragment = [line]
                assignment_in_mcp_table = in_mcp_table
            continue
        output.append(line)

    if assignment_fragment is not None:
        raise ValueError("Codex configuration could not be isolated safely")
    sanitized = "".join(output)
    sanitized_parsed = tomllib.loads(sanitized)
    sanitized_servers = sanitized_parsed.get("mcp_servers")
    if isinstance(sanitized_servers, Mapping) and _CONFIRMATION_SERVER in sanitized_servers:
        raise ValueError("Codex configuration could not be isolated safely")
    return sanitized


def _mcp_startup_timeout_overlays(config_path: Path) -> list[str]:
    """Give enabled native MCP servers enough time for a cold Workbench start."""
    try:
        if not config_path.is_file():
            return []
        if config_path.stat().st_size > _MAX_CODEX_CONFIG_BYTES:
            raise ValueError("Codex configuration could not be isolated safely")
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("Codex configuration could not be isolated safely") from exc

    servers = parsed.get("mcp_servers")
    if not isinstance(servers, Mapping):
        return []
    overlays: list[str] = []
    for name, configuration in sorted(servers.items()):
        if name == _CONFIRMATION_SERVER or not isinstance(configuration, Mapping):
            continue
        if not _MCP_SERVER_CONFIG_KEY.fullmatch(str(name)):
            continue
        if configuration.get("enabled", True) is False:
            continue
        configured_timeout = configuration.get("startup_timeout_sec")
        if (
            isinstance(configured_timeout, (int, float))
            and not isinstance(configured_timeout, bool)
            and configured_timeout >= _WORKBENCH_MCP_STARTUP_TIMEOUT_SECONDS
        ):
            continue
        overlays.append(
            f"mcp_servers.{name}.startup_timeout_sec="
            f"{_WORKBENCH_MCP_STARTUP_TIMEOUT_SECONDS}"
        )
    return overlays


def _command_with_mcp_startup_timeouts(
    command: Sequence[str], config_path: Path
) -> list[str]:
    effective = list(command)
    arguments: list[str] = []
    for overlay in _mcp_startup_timeout_overlays(config_path):
        arguments.extend(("-c", overlay))
    insert_at = 3 if len(effective) > 2 and effective[2] == "resume" else 2
    effective[insert_at:insert_at] = arguments
    return effective


@contextlib.contextmanager
def _isolated_codex_environment(
    base_env: Mapping[str, str],
    *,
    provider_session_ref: str = "",
):
    """Isolate a conflicting MCP definition while retaining user-owned Codex state."""
    env = dict(base_env)
    codex_home = Path(env.get("CODEX_HOME", "~/.codex")).expanduser()
    config_path = codex_home / "config.toml"
    try:
        if not config_path.is_file():
            sanitized = None
        else:
            if config_path.stat().st_size > _MAX_CODEX_CONFIG_BYTES:
                raise ValueError("Codex configuration could not be isolated safely")
            source = config_path.read_text(encoding="utf-8")
            sanitized_source = _config_without_confirmation_server(source)
            sanitized = None if sanitized_source == source else sanitized_source
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("Codex configuration could not be isolated safely") from exc

    if sanitized is None:
        yield _CodexProcessEnvironment(env)
        return

    try:
        isolated_home = create_isolated_codex_home(
            codex_home,
            sanitized,
            provider_session_ref=provider_session_ref,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Codex configuration could not be isolated safely") from exc

    isolated_env = dict(env)
    isolated_env["CODEX_HOME"] = str(isolated_home.path)
    process_environment = _CodexProcessEnvironment(isolated_env, isolated_home)
    try:
        yield process_environment
    finally:
        isolated_home.cleanup(sync_sessions=process_environment.sync_sessions)


class _CodexProcessEnvironment(dict[str, str]):
    def __init__(
        self,
        values: Mapping[str, str],
        isolated_home: IsolatedCodexHome | None = None,
    ):
        super().__init__(values)
        self.isolated_home = isolated_home
        self.sync_sessions = False


class _AdapterFailure(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class _CodexNormalizer:
    def __init__(self, on_event: Callable[[RuntimeEvent], None]):
        self._on_event = on_event
        self.provider_session_ref = ""
        self.final_text = ""
        self.saw_valid_record = False
        self.terminal_status = ""
        self._preamble_bytes = 0
        self._output_bytes = 0
        self._text_states: dict[str, str] = {}
        self._idless_text_key = ""
        self._text_sequence = 0
        self._tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
        self._tool_sequence = 0

    def accept_line(self, line: str) -> None:
        line_bytes = len(line.encode("utf-8")) + 1
        self._output_bytes += line_bytes
        if (
            line_bytes > _MAX_PROVIDER_OUTPUT_BYTES
            or self._output_bytes > _MAX_PROVIDER_OUTPUT_BYTES
        ):
            raise _AdapterFailure(
                "provider_output_limit", "provider output exceeded the safe limit"
            )
        if not line.strip():
            return
        if self.terminal_status:
            raise _AdapterFailure(
                "invalid_provider_output", "provider emitted data after terminal state"
            )
        try:
            record = json.loads(line)
        except (TypeError, ValueError) as exc:
            if self.saw_valid_record:
                raise _AdapterFailure(
                    "invalid_provider_output", "provider output was not valid JSONL"
                ) from exc
            self._preamble_bytes += line_bytes
            if self._preamble_bytes > _MAX_PREAMBLE_BYTES:
                raise _AdapterFailure(
                    "provider_output_limit", "provider preamble exceeded the safe limit"
                ) from exc
            return
        if not isinstance(record, Mapping):
            raise _AdapterFailure(
                "invalid_provider_output", "provider event must be a JSON object"
            )
        self.saw_valid_record = True
        self._normalize(record)

    def _normalize(self, record: Mapping[str, Any]) -> None:
        event_type = record.get("type")
        if event_type == "thread.started":
            self._capture_session(record.get("thread_id"))
            self._emit("status_changed", {"status": "running"})
            return
        item = record.get("item")
        if not isinstance(item, Mapping):
            item = {}
        item_type = item.get("type")
        if event_type == "item.started" and item_type in {
            "command_execution",
            "mcp_tool_call",
        }:
            self._start_tool(item)
            return
        if event_type == "item.completed" and item_type in {
            "command_execution",
            "mcp_tool_call",
        }:
            self._complete_tool(item)
            return
        if event_type == "item.delta" and item_type in {
            "assistant_message",
            "agent_message",
        }:
            self._accept_delta(item)
            return
        if event_type == "item.completed" and item_type in {
            "assistant_message",
            "agent_message",
        }:
            self._accept_completed_text(item)
            return
        if event_type == "turn.completed":
            response = record.get("response")
            output_text = response.get("output_text") if isinstance(response, Mapping) else None
            if not isinstance(output_text, str):
                output_text = record.get("last_agent_message")
            if isinstance(output_text, str) and output_text:
                self._accept_terminal_text(output_text)
            self.terminal_status = "completed"
            return
        if event_type == "turn.failed":
            self.terminal_status = "failed"

    def _capture_session(self, value: object) -> None:
        if not isinstance(value, str) or not _is_canonical_session_ref(value):
            raise _AdapterFailure(
                "invalid_provider_session", "provider session reference was invalid"
            )
        if self.provider_session_ref and self.provider_session_ref != value:
            self.provider_session_ref = ""
            raise _AdapterFailure(
                "conflicting_provider_session",
                "provider returned conflicting session references",
            )
        self.provider_session_ref = value

    def _accept_delta(self, item: Mapping[str, Any]) -> None:
        value = item.get("text")
        if not isinstance(value, str) or not value:
            return
        _reject_credential_bearing_text(value)
        key = self._text_key(item, completing=False)
        text = self._text_states.get(key, "") + value
        self._text_states[key] = text
        self.final_text = text
        self._emit("text_delta", {"text": value})

    def _accept_completed_text(self, item: Mapping[str, Any]) -> None:
        value = item.get("text")
        if not isinstance(value, str) or not value:
            return
        _reject_credential_bearing_text(value)
        key = self._text_key(item, completing=True)
        streamed = self._text_states.pop(key, "")
        if value.startswith(streamed):
            suffix = value[len(streamed) :]
            if suffix:
                self._emit("text_delta", {"text": suffix})
        elif value != streamed:
            self._emit("text_delta", {"text": value})
        self.final_text = value
        if key == self._idless_text_key:
            self._idless_text_key = ""

    def _accept_terminal_text(self, value: str) -> None:
        _reject_credential_bearing_text(value)
        if value != self.final_text:
            self._emit("text_delta", {"text": value})
        self.final_text = value

    def _text_key(self, item: Mapping[str, Any], *, completing: bool) -> str:
        native_id = item.get("id")
        if isinstance(native_id, str) and native_id:
            key = f"native:{native_id}"
        elif self._idless_text_key:
            key = self._idless_text_key
        elif completing:
            self._text_sequence += 1
            key = f"completed:{self._text_sequence}"
        else:
            self._text_sequence += 1
            key = f"active:{self._text_sequence}"
            self._idless_text_key = key
        if key not in self._text_states and len(self._text_states) >= 64:
            raise _AdapterFailure(
                "invalid_provider_output", "provider opened too many text items"
            )
        return key

    def _start_tool(self, item: Mapping[str, Any]) -> None:
        native_id = _required_native_item_id(item)
        if native_id in self._tool_calls or len(self._tool_calls) >= 128:
            raise _AdapterFailure(
                "invalid_provider_output", "provider tool start was invalid"
            )
        self._tool_sequence += 1
        correlation_id = f"tool-call-{self._tool_sequence}"
        snapshot = _tool_payload(item, correlation_id=correlation_id, status="running")
        self._tool_calls[native_id] = (correlation_id, dict(item))
        self._emit("tool_started", snapshot)

    def _complete_tool(self, item: Mapping[str, Any]) -> None:
        native_id = _required_native_item_id(item)
        started = self._tool_calls.pop(native_id, None)
        if started is None:
            raise _AdapterFailure(
                "invalid_provider_output", "provider tool completion was not correlated"
            )
        correlation_id, started_item = started
        provider_item = {**started_item, **dict(item)}
        proposal: dict[str, object] | None = None
        failed = _native_tool_failed(item)
        if item.get("type") == "mcp_tool_call":
            if _is_confirmation_call(item) and not failed:
                proposal = _extract_confirmation(item.get("result"))
        self._emit(
            "tool_completed",
            _tool_payload(
                provider_item,
                correlation_id=correlation_id,
                status="failed" if failed else "completed",
            ),
        )
        if proposal is not None:
            self._emit("confirmation_required", proposal)

    def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        try:
            if event_type in {"tool_started", "tool_completed"}:
                payload = redact_credentials_in_value(payload)
            else:
                assert_no_credentials(payload)
        except ValueError as exc:
            raise _AdapterFailure(
                "sensitive_provider_output", "provider output contained sensitive data"
            ) from exc
        self._on_event(RuntimeEvent(event_type, payload))


def _safe_tool_name(item: Mapping[str, Any]) -> str:
    if item.get("type") == "command_execution":
        command = item.get("command")
        if isinstance(command, str) and command.strip():
            return command.strip().split(maxsplit=1)[0]
        return "command_execution"
    server = item.get("server")
    tool = item.get("tool")
    if isinstance(server, str) and isinstance(tool, str):
        return f"{server}.{tool}"
    if isinstance(tool, str) and tool:
        return tool
    if isinstance(server, str) and server:
        return server
    return "mcp_tool_call"


def _tool_payload(
    item: Mapping[str, Any], *, correlation_id: str, status: str
) -> dict[str, Any]:
    native_id = _required_native_item_id(item)
    item_type = item.get("type")
    kind = "command" if item_type == "command_execution" else "mcp"
    payload: dict[str, Any] = {
        "tool_call_id": correlation_id,
        "kind": kind,
        "name": _safe_tool_name(item),
        "native_id": native_id,
        "status": status,
    }
    if kind == "command":
        fields = {
            "command": "command",
            "cwd": "cwd",
            "exit_code": "exit_code",
            "aggregated_output": "output",
        }
    else:
        fields = {
            "server": "server",
            "tool": "tool",
            "arguments": "arguments",
            "result": "result",
        }
    for source, target in fields.items():
        if source in item:
            payload[target] = item[source]
    if (
        status == "failed"
        and kind == "command"
        and not isinstance(payload.get("exit_code"), int)
        and not payload.get("output")
    ):
        payload["summary"] = (
            "Codex Provider 报告命令失败，但未返回退出码或诊断输出。"
        )
    payload["provider_item"] = dict(item)
    return payload


def _required_native_item_id(item: Mapping[str, Any]) -> str:
    native_id = item.get("id")
    if not isinstance(native_id, str) or not native_id:
        raise _AdapterFailure(
            "invalid_provider_output", "provider tool item identifier was missing"
        )
    return native_id


def _is_canonical_session_ref(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, ValueError):
        return False


def _native_tool_failed(item: Mapping[str, Any]) -> bool:
    if _documented_wrapper_failed(item):
        return True
    result = item.get("result")
    if not isinstance(result, Mapping):
        return False
    if _documented_wrapper_failed(result):
        return True
    nested_result = result.get("result")
    return isinstance(nested_result, Mapping) and _documented_wrapper_failed(
        nested_result
    )


def _documented_wrapper_failed(value: Mapping[str, object]) -> bool:
    if value.get("isError") is True or bool(value.get("error")):
        return True
    status = value.get("status")
    return isinstance(status, str) and status.casefold() in {
        "cancelled",
        "error",
        "failed",
    }


def _is_confirmation_call(item: Mapping[str, Any]) -> bool:
    return (
        item.get("server") == _CONFIRMATION_SERVER
        and item.get("tool") == _CONFIRMATION_TOOL
    )


def _reject_credential_bearing_text(value: str) -> None:
    if contains_credential(value):
        raise _AdapterFailure(
            "sensitive_provider_output", "provider output contained sensitive text"
        )


def _extract_confirmation(native_result: object) -> dict[str, object]:
    candidate: object = native_result
    if isinstance(candidate, Mapping):
        for key in ("structuredContent", "structured_content"):
            structured = candidate.get(key)
            if isinstance(structured, Mapping):
                candidate = structured.get("result", structured)
                break
        else:
            content = candidate.get("content")
            if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
                for entry in content:
                    if isinstance(entry, Mapping) and isinstance(entry.get("text"), str):
                        try:
                            decoded = json.loads(entry["text"])
                        except (TypeError, ValueError):
                            continue
                        candidate = (
                            decoded.get("result", decoded)
                            if isinstance(decoded, Mapping)
                            else decoded
                        )
                        break
    if not isinstance(candidate, Mapping):
        raise _AdapterFailure("invalid_confirmation", "confirmation proposal was invalid")
    expected_fields = {"kind", "argv", "target", "summary", "risk", "executed"}
    if set(candidate) != expected_fields:
        raise _AdapterFailure("invalid_confirmation", "confirmation proposal was invalid")
    try:
        assert_no_credentials(candidate)
    except ValueError as exc:
        raise _AdapterFailure(
            "sensitive_provider_output", "confirmation proposal contained sensitive data"
        ) from exc
    try:
        argv = _validate_argv(candidate.get("argv"))
    except ValueError as exc:
        raise _AdapterFailure("invalid_confirmation", "confirmation proposal was invalid") from exc
    fields = ("target", "summary", "risk")
    if (
        candidate.get("kind") != "reviewed_cli"
        or candidate.get("executed") is not False
        or any(
            not isinstance(candidate.get(field), str) or not candidate[field].strip()
            for field in fields
        )
    ):
        raise _AdapterFailure("invalid_confirmation", "confirmation proposal was invalid")
    for text_field in fields:
        _reject_credential_bearing_text(candidate[text_field])
    return {
        "kind": "reviewed_cli",
        "argv": argv,
        "target": candidate["target"].strip(),
        "summary": candidate["summary"].strip(),
        "risk": candidate["risk"].strip(),
        "executed": False,
    }


@dataclass
class _RuntimeOwner:
    executor: Any
    normalizer: _CodexNormalizer
    command: list[str]
    request: RuntimeRequest
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    result: RuntimeResult | None = None
    thread: threading.Thread | None = None
    stop_requested: bool = False
    stop_dispatched: bool = False
    wait_claimed: bool = False


class _CancellableProcessExecutor:
    def __init__(self, *, cwd: Path):
        self._cwd = cwd
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._owned_pgid: int | None = None
        self._watchdog_write_fd: int | None = None
        self._stop_requested = False
        self._isolated_home: IsolatedCodexHome | None = None

    def set_isolated_home(self, isolated_home: IsolatedCodexHome) -> None:
        with self._lock:
            if self._process is not None:
                raise RuntimeError("process cleanup ownership was attached too late")
            self._isolated_home = isolated_home

    def stop(self) -> None:
        with self._lock:
            if self._stop_requested:
                return
            self._stop_requested = True
        self._terminate_owned_group()

    def _terminate_owned_group(self) -> None:
        with self._lock:
            owned_pgid = self._owned_pgid
            self._owned_pgid = None
            watchdog_write_fd = self._watchdog_write_fd
            self._watchdog_write_fd = None
        _close_fd(watchdog_write_fd)
        if owned_pgid is not None:
            _terminate_owned_process_group(owned_pgid)

    def _release_owned_group(self) -> None:
        with self._lock:
            owned_pgid = self._owned_pgid
            self._owned_pgid = None
            watchdog_write_fd = self._watchdog_write_fd
            self._watchdog_write_fd = None
        if owned_pgid is not None and _owned_process_group_exists(owned_pgid):
            _close_fd(watchdog_write_fd)
            _terminate_owned_process_group(owned_pgid)
            return
        if watchdog_write_fd is not None:
            try:
                os.write(watchdog_write_fd, b"R")
            except (BrokenPipeError, OSError):
                pass
            _close_fd(watchdog_write_fd)

    def _start_watchdog(self, process: subprocess.Popen[bytes]) -> None:
        """Hook for lifecycle tests; the wrapper has already started supervision."""
        if process.pid <= 0:
            raise RuntimeError("process did not establish a valid owned process group")

    def __call__(
        self,
        command: list[str],
        *,
        prompt: str,
        env: dict[str, str] | None,
        total_timeout_seconds: float,
        idle_timeout_seconds: float,
        on_stdout_line: Callable[[str], None],
    ) -> ProcessRunResult:
        started_at = time.monotonic()
        last_output_at = started_at
        prompt_bytes = prompt.encode("utf-8")
        if len(prompt_bytes) > MAX_PROMPT_BYTES:
            raise _AdapterFailure(
                "prompt_limit", "prompt exceeds safe byte limit"
            )
        stdout = bytearray()
        stderr = bytearray()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        line_buffer = ""
        parent_read_fd, parent_write_fd = os.pipe()
        identity = str(uuid.uuid4())
        with self._lock:
            isolated_home = self._isolated_home
        cleanup_options: list[str] = []
        inherited_fds = [parent_read_fd]
        if isolated_home is not None and isolated_home.lock_fd >= 0:
            cleanup_options = [
                "--cleanup-home",
                str(isolated_home.path),
                "--cleanup-marker",
                isolated_home.marker_token,
                "--cleanup-lock-fd",
                str(isolated_home.lock_fd),
            ]
            inherited_fds.append(isolated_home.lock_fd)
        wrapped_command = [
            sys.executable,
            str(Path(__file__).with_name("process_watchdog.py")),
            "--parent-fd",
            str(parent_read_fd),
            "--identity",
            identity,
            *cleanup_options,
            "--",
            *command,
        ]
        try:
            process = subprocess.Popen(
                wrapped_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self._cwd,
                start_new_session=True,
                pass_fds=tuple(inherited_fds),
            )
        except BaseException:
            _close_fd(parent_read_fd)
            _close_fd(parent_write_fd)
            raise
        os.close(parent_read_fd)
        selector: selectors.BaseSelector | None = None
        selector_ready = False
        returncode = -1
        try:
            owned_pgid = process.pid
            if type(owned_pgid) is not int or owned_pgid <= 0:
                raise RuntimeError("process did not establish a valid owned process group")
            with self._lock:
                self._process = process
                self._owned_pgid = owned_pgid
                self._watchdog_write_fd = parent_write_fd
                should_stop = self._stop_requested
            self._start_watchdog(process)
            if should_stop:
                self._terminate_owned_group()
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            os.set_blocking(process.stdin.fileno(), False)
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout))
            selector.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr))
            prompt_offset = 0
            if prompt_bytes:
                selector.register(process.stdin, selectors.EVENT_WRITE, ("stdin", None))
            else:
                process.stdin.close()
            selector_ready = True
            timed_out = False
            timeout_kind = ""
            while selector.get_map():
                now = time.monotonic()
                if now - started_at >= total_timeout_seconds:
                    timed_out, timeout_kind = True, "total"
                    self._terminate_owned_group()
                    break
                if now - last_output_at >= idle_timeout_seconds:
                    timed_out, timeout_kind = True, "idle"
                    self._terminate_owned_group()
                    break
                for key, _ in selector.select(0.25):
                    stream_kind, target = key.data
                    if stream_kind == "stdin":
                        try:
                            written = os.write(
                                key.fd,
                                prompt_bytes[prompt_offset : prompt_offset + 65536],
                            )
                        except BrokenPipeError:
                            written = 0
                        except BlockingIOError:
                            continue
                        prompt_offset += written
                        if written == 0 or prompt_offset == len(prompt_bytes):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    assert isinstance(target, bytearray)
                    if len(target) + len(chunk) > _MAX_PROVIDER_OUTPUT_BYTES:
                        raise _AdapterFailure(
                            "provider_output_limit",
                            "provider output exceeded the safe limit",
                        )
                    target.extend(chunk)
                    if target is stdout:
                        line_buffer += decoder.decode(chunk)
                        while "\n" in line_buffer:
                            line, line_buffer = line_buffer.split("\n", 1)
                            on_stdout_line(line.removesuffix("\r"))
                    last_output_at = time.monotonic()
                if process.poll() is not None:
                    if selector.get_map():
                        self._terminate_owned_group()
                    else:
                        break
            returncode = process.wait(timeout=5)
        finally:
            streams_lingering = not selector_ready
            if selector_ready and selector is not None:
                streams_lingering = bool(selector.get_map())
            if selector is not None:
                selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            if process.poll() is None or streams_lingering:
                self._terminate_owned_group()
            else:
                self._release_owned_group()
            if process.poll() is None:
                returncode = process.wait(timeout=5)
            with self._lock:
                self._process = None
        line_buffer += decoder.decode(b"", final=True)
        if line_buffer:
            on_stdout_line(line_buffer.removesuffix("\r"))
        return ProcessRunResult(
            returncode=returncode,
            stdout=bytes(stdout).decode(errors="replace").strip(),
            stderr=bytes(stderr).decode(errors="replace").strip(),
            timed_out=timed_out,
            timeout_kind=timeout_kind,
            timeout_reason=("process timed out" if timed_out else ""),
        )


def _terminate_owned_process_group(owned_pgid: int) -> None:
    try:
        os.killpg(owned_pgid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(owned_pgid, 0)
        except (PermissionError, ProcessLookupError):
            return
        time.sleep(0.02)
    try:
        os.killpg(owned_pgid, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        return


def _owned_process_group_exists(owned_pgid: int) -> bool:
    try:
        os.killpg(owned_pgid, 0)
        return True
    except (PermissionError, ProcessLookupError):
        return False


def _close_fd(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


class CodexRuntime:
    kind = "codex"

    def __init__(
        self,
        *,
        workspace: Path,
        executor: Callable[..., ProcessRunResult] | None = None,
        codex_bin: str = "codex",
        approved_input_roots: Sequence[Path] = (),
        total_timeout_seconds: float = _DEFAULT_TOTAL_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
    ):
        self.workspace = Path(workspace).resolve()
        self._executor = executor
        self.codex_bin = codex_bin
        self._approved_input_roots = tuple(
            root.resolve() for root in (self.workspace, *approved_input_roots)
        )
        self.total_timeout_seconds = total_timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            session_resume=True,
            streamed_text=True,
            structured_tools=True,
            image_input=True,
            model_selection=True,
            mcp_configuration=True,
            stoppable=True,
            recoverable=True,
        )

    def build_command(
        self,
        *,
        prompt: str,
        provider_session_ref: str,
        model: str = "",
        image_paths: Sequence[Path] = (),
        workspace: Path | None = None,
    ) -> list[str]:
        effective_workspace = Path(workspace or self.workspace).resolve()
        self._validate_workspace(effective_workspace)
        if provider_session_ref and not _is_canonical_session_ref(provider_session_ref):
            raise ValueError("invalid provider session reference")
        validated_images = self._validated_images(image_paths, effective_workspace)
        runner = CodexRunner(workspace=effective_workspace, codex_bin=self.codex_bin)
        command = runner.build_command(
            prompt=prompt,
            session_id=provider_session_ref or None,
            image_paths=validated_images,
            use_output_schema=False,
            approval_policy="untrusted",
            developer_instructions=_DEVELOPER_INSTRUCTIONS,
            use_approval_bypass=False,
            preserve_native_model_config=True,
        )
        insert_at = 3 if provider_session_ref else 2
        overlay = ["-c", _confirmation_server_overlay()]
        if model.strip():
            overlay[0:0] = ["-m", model.strip()]
        command[insert_at:insert_at] = overlay
        return command

    def start(
        self,
        request: RuntimeRequest,
        *,
        on_event: Callable[[RuntimeEvent], None],
    ) -> RuntimeHandle:
        request_workspace = request.workspace.resolve()
        self._validate_workspace(request_workspace)
        if request.attachment_paths:
            raise ValueError("attachments are not supported by the Codex runtime")
        command = self.build_command(
            prompt=request.prompt,
            provider_session_ref=request.provider_session_ref,
            model=request.model,
            image_paths=request.image_paths,
            workspace=request_workspace,
        )
        executor = self._executor or _CancellableProcessExecutor(cwd=request_workspace)
        owner = _RuntimeOwner(
            executor=executor,
            normalizer=_CodexNormalizer(on_event),
            command=command,
            request=request,
        )
        handle = RuntimeHandle.create(run_id=uuid.uuid4().hex, owner=owner)
        owner.thread = threading.Thread(
            target=self._run,
            args=(owner,),
            name=f"codex-workbench-{handle.run_id}",
            daemon=True,
        )
        owner.thread.start()
        return handle

    def wait(self, handle: RuntimeHandle) -> RuntimeResult:
        owner = _runtime_owner(handle)
        if not isinstance(owner, _RuntimeOwner):
            raise ValueError("runtime handle does not belong to Codex")
        with owner.lock:
            if owner.wait_claimed:
                raise ValueError("runtime handle is already being waited")
            owner.wait_claimed = True
        owner.done.wait()
        try:
            assert owner.result is not None
            return owner.result
        finally:
            _release_runtime_owner(handle)

    def stop(self, handle: RuntimeHandle) -> None:
        try:
            owner = _runtime_owner(handle)
        except ValueError:
            return
        if not isinstance(owner, _RuntimeOwner):
            raise ValueError("runtime handle does not belong to Codex")
        with owner.lock:
            if owner.done.is_set() or owner.stop_dispatched:
                return
            owner.stop_requested = True
            owner.stop_dispatched = True
            stop = getattr(owner.executor, "stop", None)
        if callable(stop):
            stop()

    def _run(self, owner: _RuntimeOwner) -> None:
        try:
            base_env = CodexRunner(
                workspace=owner.request.workspace,
                codex_bin=self.codex_bin,
            ).build_env(preserve_local_cli_auth=True)
            with _isolated_codex_environment(
                base_env,
                provider_session_ref=owner.request.provider_session_ref,
            ) as process_env:
                if process_env.isolated_home is not None:
                    attach_cleanup = getattr(owner.executor, "set_isolated_home", None)
                    if callable(attach_cleanup):
                        attach_cleanup(process_env.isolated_home)
                command = _command_with_mcp_startup_timeouts(
                    owner.command,
                    Path(process_env.get("CODEX_HOME", "~/.codex")).expanduser()
                    / "config.toml",
                )
                process_result = owner.executor(
                    command,
                    prompt=owner.request.prompt,
                    env=process_env,
                    total_timeout_seconds=self.total_timeout_seconds,
                    idle_timeout_seconds=self.idle_timeout_seconds,
                    on_stdout_line=owner.normalizer.accept_line,
                )
                with owner.lock:
                    stopped = owner.stop_requested
                process_env.sync_sessions = bool(
                    not stopped
                    and process_result.returncode == 0
                    and not process_result.timed_out
                    and owner.normalizer.terminal_status == "completed"
                    and owner.normalizer.provider_session_ref
                )
            if stopped:
                result = RuntimeResult(status="stopped")
            elif process_result.timed_out:
                result = RuntimeResult(
                    status="failed",
                    error_code="provider_timeout",
                    error_detail="provider execution timed out",
                )
            elif process_result.returncode != 0:
                result = RuntimeResult(
                    status="failed",
                    error_code="provider_process_failed",
                    error_detail="provider execution failed",
                )
            elif owner.normalizer.terminal_status == "failed":
                result = RuntimeResult(
                    status="failed",
                    error_code="provider_turn_failed",
                    error_detail="provider turn failed",
                )
            elif owner.normalizer.terminal_status != "completed":
                result = RuntimeResult(
                    status="failed",
                    error_code="incomplete_provider_output",
                    error_detail="provider execution ended without completion",
                )
            elif not owner.normalizer.provider_session_ref:
                result = RuntimeResult(
                    status="failed",
                    error_code="missing_provider_session",
                    error_detail="provider execution did not identify its session",
                )
            else:
                result = RuntimeResult(
                    status="completed",
                    final_text=owner.normalizer.final_text,
                    provider_session_ref=owner.normalizer.provider_session_ref,
                )
        except _AdapterFailure as exc:
            result = RuntimeResult(
                status="failed",
                error_code=exc.code,
                error_detail=exc.detail,
            )
        except Exception:
            result = RuntimeResult(
                status="failed",
                error_code="runtime_failure",
                error_detail="provider execution could not be completed safely",
            )
        owner.result = result
        owner.done.set()

    def _validate_workspace(self, workspace: Path) -> None:
        try:
            workspace.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("workspace is outside runtime boundary") from exc

    def _validated_images(
        self, image_paths: Sequence[Path], workspace: Path
    ) -> list[Path]:
        validated: list[Path] = []
        for raw_path in image_paths:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            try:
                metadata = candidate.lstat()
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError("invalid image input") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError("invalid image input")
            if candidate.absolute() != resolved:
                raise ValueError("invalid image input")
            if not any(_path_is_within(resolved, root) for root in self._approved_input_roots):
                raise ValueError("invalid image input")
            validated.append(resolved)
        return validated


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
