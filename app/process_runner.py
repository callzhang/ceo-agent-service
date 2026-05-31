import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass


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
) -> ProcessRunResult:
    started_at = time.monotonic()
    last_output_at = started_at
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
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
    try:
        while selector.get_map():
            now = time.monotonic()
            if now - started_at >= total_timeout_seconds:
                timeout_kind = "total"
                timeout_reason = (
                    f"process timed out after {int(total_timeout_seconds)} seconds"
                )
                _terminate_process_group(process)
                break
            if now - last_output_at >= idle_timeout_seconds:
                timeout_kind = "idle"
                timeout_reason = (
                    "process produced no output for "
                    f"{int(idle_timeout_seconds)} seconds"
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
                    last_output_at = time.monotonic()
                else:
                    selector.unregister(key.fileobj)
        returncode = process.wait(timeout=5)
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_process_group(process)
            returncode = process.wait(timeout=5)

    return ProcessRunResult(
        returncode=returncode,
        stdout=b"".join(stdout_chunks).decode(errors="replace").strip(),
        stderr=b"".join(stderr_chunks).decode(errors="replace").strip(),
        timed_out=bool(timeout_kind),
        timeout_kind=timeout_kind,
        timeout_reason=timeout_reason,
    )


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
