"""Codex JSONL adapter for the provider-neutral workbench runtime."""

from __future__ import annotations

import codecs
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.bounded_process import MAX_PROCESS_OUTPUT_BYTES
from app.codex_runner import CodexRunner, _config_string
from app.leak_check import contains_credential, is_sensitive_field_name
from app.process_runner import ProcessRunResult
from app.workbench.confirmation_mcp import _validate_argv
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
_CONFIRMATION_SERVER = "workbench_confirmation"
_CONFIRMATION_TOOL = "request_reviewed_action"
_DEVELOPER_INSTRUCTIONS = """
You are running inside the local Agent Workbench. Preserve the user's configured
Codex skills, rules, plugins, and authenticated read tools. You may perform local,
reversible work directly. For every reviewed external write, deletion, destructive
operation, important overwrite, approval decision, or message send, call
workbench_confirmation.request_reviewed_action with the proposed argv, target,
summary, and risk. Never execute such an action directly. The confirmation tool
only records a proposal and never performs the action.
""".strip()


class _AdapterFailure(Exception):
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
        self.saw_turn_completed = False
        self._preamble_bytes = 0
        self._output_bytes = 0
        self._streamed_text = ""

    def accept_line(self, line: str) -> None:
        line_bytes = len(line.encode("utf-8")) + 1
        self._output_bytes += line_bytes
        if line_bytes > MAX_PROCESS_OUTPUT_BYTES or self._output_bytes > MAX_PROCESS_OUTPUT_BYTES:
            raise _AdapterFailure(
                "provider_output_limit", "provider output exceeded the safe limit"
            )
        if not line.strip():
            return
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
        if _has_sensitive_field_name(record):
            raise _AdapterFailure(
                "sensitive_provider_output", "provider output contained a sensitive field"
            )
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
            self._emit(
                "tool_started",
                {
                    "tool": _safe_tool_name(item),
                    "summary": "Tool started",
                },
            )
            return
        if event_type == "item.completed" and item_type in {
            "command_execution",
            "mcp_tool_call",
        }:
            self._emit(
                "tool_completed",
                {
                    "tool": _safe_tool_name(item),
                    "summary": "Tool completed",
                },
            )
            if _is_confirmation_call(item):
                self._emit_confirmation(item.get("result"))
            return
        if event_type == "item.delta" and item_type in {
            "assistant_message",
            "agent_message",
        }:
            self._accept_delta(item.get("text"))
            return
        if event_type == "item.completed" and item_type in {
            "assistant_message",
            "agent_message",
        }:
            self._accept_completed_text(item.get("text"))
            return
        if event_type == "turn.completed":
            self.saw_turn_completed = True
            response = record.get("response")
            output_text = response.get("output_text") if isinstance(response, Mapping) else None
            if not isinstance(output_text, str):
                output_text = record.get("last_agent_message")
            if isinstance(output_text, str) and output_text:
                self._accept_completed_text(output_text)

    def _capture_session(self, value: object) -> None:
        if not isinstance(value, str) or not value.strip():
            raise _AdapterFailure(
                "invalid_provider_output", "provider session reference was missing"
            )
        if self.provider_session_ref and self.provider_session_ref != value:
            self.provider_session_ref = ""
            raise _AdapterFailure(
                "conflicting_provider_session",
                "provider returned conflicting session references",
            )
        self.provider_session_ref = value

    def _accept_delta(self, value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        _reject_credential_bearing_text(value)
        self._streamed_text += value
        self.final_text = self._streamed_text
        self._emit("text_delta", {"text": value})

    def _accept_completed_text(self, value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        _reject_credential_bearing_text(value)
        if value.startswith(self._streamed_text):
            suffix = value[len(self._streamed_text) :]
            if suffix:
                self._emit("text_delta", {"text": suffix})
                self._streamed_text += suffix
        elif self._streamed_text.endswith(value):
            pass
        elif value != self.final_text:
            self._emit("text_delta", {"text": value})
            self._streamed_text += value
        self.final_text = value

    def _emit_confirmation(self, native_result: object) -> None:
        proposal = _extract_confirmation(native_result)
        self._emit("confirmation_required", proposal)

    def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self._on_event(RuntimeEvent(event_type, payload))


def _has_sensitive_field_name(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_field_name(key):
                return True
            if _has_sensitive_field_name(item):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_has_sensitive_field_name(item) for item in value)
    return False


def _safe_tool_name(item: Mapping[str, Any]) -> str:
    if item.get("type") == "command_execution":
        return "command"
    name = item.get("tool")
    if (
        isinstance(name, str)
        and name.strip()
        and not is_sensitive_field_name(name)
        and not contains_credential(name)
        and len(name) <= 80
    ):
        return name.strip()
    return "mcp_tool"


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
    for field in fields:
        _reject_credential_bearing_text(candidate[field])
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


class _CancellableProcessExecutor:
    def __init__(self, *, cwd: Path):
        self._cwd = cwd
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._stop_requested = False

    def stop(self) -> None:
        with self._lock:
            if self._stop_requested:
                return
            self._stop_requested = True
            process = self._process
        if process is not None:
            _terminate_owned_process_group(process)

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
        stdout = bytearray()
        stderr = bytearray()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        line_buffer = ""
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=self._cwd,
            start_new_session=True,
        )
        with self._lock:
            self._process = process
            should_stop = self._stop_requested
        if should_stop:
            _terminate_owned_process_group(process)
        assert process.stdin is not None
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
        except BrokenPipeError:
            pass
        assert process.stdout is not None
        assert process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        timed_out = False
        timeout_kind = ""
        try:
            while selector.get_map():
                now = time.monotonic()
                if now - started_at >= total_timeout_seconds:
                    timed_out, timeout_kind = True, "total"
                    _terminate_owned_process_group(process)
                    break
                if now - last_output_at >= idle_timeout_seconds:
                    timed_out, timeout_kind = True, "idle"
                    _terminate_owned_process_group(process)
                    break
                for key, _ in selector.select(0.25):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target: bytearray = key.data
                    if len(target) + len(chunk) > MAX_PROCESS_OUTPUT_BYTES:
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
                    break
            returncode = process.wait(timeout=5)
        finally:
            selector.close()
            if process.poll() is None:
                _terminate_owned_process_group(process)
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


def _terminate_owned_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


class CodexRuntime:
    kind = "codex"

    def __init__(
        self,
        *,
        workspace: Path,
        executor: Callable[..., ProcessRunResult] | None = None,
        codex_bin: str = "codex",
        total_timeout_seconds: float = _DEFAULT_TOTAL_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
    ):
        self.workspace = Path(workspace).resolve()
        self._executor = executor
        self.codex_bin = codex_bin
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
        runner = CodexRunner(workspace=effective_workspace, codex_bin=self.codex_bin)
        command = runner.build_command(
            prompt=prompt,
            session_id=provider_session_ref or None,
            image_paths=list(image_paths),
            use_output_schema=False,
            approval_policy="untrusted",
            developer_instructions=_DEVELOPER_INSTRUCTIONS,
            use_approval_bypass=False,
            preserve_native_model_config=True,
        )
        insert_at = 3 if provider_session_ref else 2
        overlay = [
            "-c",
            _config_string(
                "mcp_servers.workbench_confirmation.command", sys.executable
            ),
            "-c",
            _config_string(
                "mcp_servers.workbench_confirmation.args",
                ["-m", "app.workbench.confirmation_mcp"],
            ),
            "-c",
            _config_string(
                "mcp_servers.workbench_confirmation.cwd",
                str(Path(__file__).resolve().parents[2]),
            ),
            "-c",
            'mcp_servers.workbench_confirmation.enabled_tools=["request_reviewed_action"]',
        ]
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
            process_result = owner.executor(
                owner.command,
                prompt=owner.request.prompt,
                env=CodexRunner(
                    workspace=owner.request.workspace,
                    codex_bin=self.codex_bin,
                ).build_env(preserve_local_cli_auth=True),
                total_timeout_seconds=self.total_timeout_seconds,
                idle_timeout_seconds=self.idle_timeout_seconds,
                on_stdout_line=owner.normalizer.accept_line,
            )
            with owner.lock:
                stopped = owner.stop_requested
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
            elif not owner.normalizer.saw_turn_completed:
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
