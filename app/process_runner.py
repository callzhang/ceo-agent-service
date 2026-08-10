import codecs
import fcntl
import os
import selectors
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROCESS_TOTAL_TIMEOUT_REASON_PREFIX = "process timed out after "
PROCESS_IDLE_TIMEOUT_REASON_PREFIX = "process produced no output for "
PROCESS_TIMEOUT_REASON_SUFFIX = " seconds"
CODEX_EXECUTION_LOCK_PATH = Path(
    os.getenv("CEO_CODEX_EXECUTION_LOCK_PATH", "/tmp/ceo-agent-service-codex.lock")
)
_CODEX_EXECUTION_THREAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProcessRunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    timeout_kind: str = ""
    timeout_reason: str = ""


def run_process_with_idle_timeout(
    command: list[str],
    *,
    prompt: str,
    env: dict[str, str] | None,
    total_timeout_seconds: float,
    idle_timeout_seconds: float,
    on_stdout_line: Callable[[str], None] | None = None,
) -> ProcessRunResult:
    with _codex_execution_slot(command):
        return _run_process_with_idle_timeout(
            command,
            prompt=prompt,
            env=env,
            total_timeout_seconds=total_timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            on_stdout_line=on_stdout_line,
        )


def _run_process_with_idle_timeout(
    command: list[str],
    *,
    prompt: str,
    env: dict[str, str] | None,
    total_timeout_seconds: float,
    idle_timeout_seconds: float,
    on_stdout_line: Callable[[str], None] | None = None,
) -> ProcessRunResult:
    started_at = time.monotonic()
    last_output_at = started_at
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    stdout_line_buffer = ""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.write(prompt.encode())
    process.stdin.close()
    assert process.stdout is not None
    assert process.stderr is not None

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, stdout_chunks)
    selector.register(process.stderr, selectors.EVENT_READ, stderr_chunks)

    timeout_kind = ""
    timeout_reason = ""
    parent_exited_with_open_streams = False
    try:
        while selector.get_map():
            now = time.monotonic()
            if now - started_at >= total_timeout_seconds:
                timeout_kind = "total"
                timeout_reason = (
                    f"{PROCESS_TOTAL_TIMEOUT_REASON_PREFIX}"
                    f"{int(total_timeout_seconds)}"
                    f"{PROCESS_TIMEOUT_REASON_SUFFIX}"
                )
                _terminate_process_group(process)
                break
            if now - last_output_at >= idle_timeout_seconds:
                timeout_kind = "idle"
                timeout_reason = (
                    f"{PROCESS_IDLE_TIMEOUT_REASON_PREFIX}"
                    f"{int(idle_timeout_seconds)}"
                    f"{PROCESS_TIMEOUT_REASON_SUFFIX}"
                )
                _terminate_process_group(process)
                break
            timeout = min(
                max(total_timeout_seconds - (now - started_at), 0.01),
                max(idle_timeout_seconds - (now - last_output_at), 0.01),
                0.5,
            )
            for key, _ in selector.select(timeout):
                chunk = os.read(key.fd, 4096)
                if chunk:
                    key.data.append(chunk)
                    if on_stdout_line is not None and key.data is stdout_chunks:
                        decoded = stdout_decoder.decode(chunk)
                        stdout_line_buffer = _emit_stdout_lines(
                            stdout_line_buffer + decoded,
                            on_stdout_line,
                        )
                    last_output_at = time.monotonic()
                else:
                    selector.unregister(key.fileobj)
            if process.poll() is not None:
                # An MCP child can inherit Codex's stdio pipes. Do not treat
                # that child retaining the pipes as an unfinished Codex turn.
                parent_exited_with_open_streams = bool(selector.get_map())
                break
        returncode = process.wait(timeout=5)
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_process_group(process)
            returncode = process.wait(timeout=5)
        elif parent_exited_with_open_streams:
            _terminate_process_group(process)

    if on_stdout_line is not None:
        stdout_line_buffer += stdout_decoder.decode(b"", final=True)
        if stdout_line_buffer:
            on_stdout_line(stdout_line_buffer.removesuffix("\r"))

    return ProcessRunResult(
        returncode=returncode,
        stdout=b"".join(stdout_chunks).decode(errors="replace").strip(),
        stderr=b"".join(stderr_chunks).decode(errors="replace").strip(),
        timed_out=bool(timeout_kind),
        timeout_kind=timeout_kind,
        timeout_reason=timeout_reason,
    )


def _is_codex_command(command: list[str]) -> bool:
    return bool(command) and Path(command[0]).name == "codex"


@contextmanager
def _codex_execution_slot(command: list[str]):
    if not _is_codex_command(command):
        yield
        return
    with _CODEX_EXECUTION_THREAD_LOCK:
        lock_fd = os.open(CODEX_EXECUTION_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def _emit_stdout_lines(
    buffered_text: str,
    callback: Callable[[str], None],
) -> str:
    while "\n" in buffered_text:
        line, buffered_text = buffered_text.split("\n", 1)
        callback(line.removesuffix("\r"))
    return buffered_text


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
