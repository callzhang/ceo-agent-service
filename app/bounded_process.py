from __future__ import annotations

import subprocess
import threading
from collections.abc import Sequence


MAX_PROCESS_OUTPUT_BYTES = 2 * 1024 * 1024


class ProcessOutputLimitError(RuntimeError):
    def __init__(self, *, stdout_bytes: int, stderr_bytes: int) -> None:
        super().__init__("process output limit exceeded")
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes


def run_bounded_process(
    argv: Sequence[str], *, timeout: int
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = threading.Event()

    def drain(name: str, pipe) -> None:
        while chunk := pipe.read(64 * 1024):
            target = streams[name]
            remaining = MAX_PROCESS_OUTPUT_BYTES - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
            if len(chunk) > remaining:
                exceeded.set()
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    threads = [
        threading.Thread(target=drain, args=(name, pipe), daemon=True)
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        for thread in threads:
            thread.join()
    if exceeded.is_set():
        raise ProcessOutputLimitError(
            stdout_bytes=len(streams["stdout"]),
            stderr_bytes=len(streams["stderr"]),
        )
    return subprocess.CompletedProcess(
        args=list(argv),
        returncode=returncode,
        stdout=streams["stdout"].decode("utf-8", errors="replace"),
        stderr=streams["stderr"].decode("utf-8", errors="replace"),
    )
