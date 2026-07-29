from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence

from mcp.server.fastmcp import FastMCP

from app.agent_result import EffectKind
from app.channel_gate import classify_cli_read_failure
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
)

MAX_CLI_OUTPUT_BYTES = 256 * 1024


class CliOutputLimitError(RuntimeError):
    pass


def execute_reviewed_read(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    if not argv:
        raise AgentReadOnlyViolationError("reconciliation_command_invalid")
    reviewed = classifier or NativeCliMetadataClassifier()
    reviewed.prewarm()
    command = reviewed.classify_cached(
        {"type": "command_execution", "argv": list(argv)}
    )
    if command is None or command.effect is not EffectKind.READ_ONLY:
        raise AgentReadOnlyViolationError("reconciliation_write_forbidden")
    executable = shutil.which(command.cli)
    if executable is None:
        raise RuntimeError("reconciliation_cli_unavailable")
    reviewed_argv = [executable, *argv[1:]]
    try:
        process = (
            _run_bounded_process(reviewed_argv, timeout=120)
            if process_runner is None
            else process_runner(
                reviewed_argv,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        )
        if (
            len(process.stdout.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES
            or len(process.stderr.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES
        ):
            raise CliOutputLimitError
    except CliOutputLimitError:
        return {
            "cli": command.cli,
            "operation": command.command_path,
            "operation_digest": command.command_digest,
            "target_identifiers": command.target_identifiers,
            "result_digest": hashlib.sha256(b"").hexdigest(),
            "stdout": "",
            "error": {
                "channel": command.cli,
                "code": "reconciliation_cli_output_limit_exceeded",
                "retryable": False,
                "gate_state": "blocked",
            },
        }
    receipt: dict[str, object] = {
        "cli": command.cli,
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "result_digest": hashlib.sha256(process.stdout.encode("utf-8")).hexdigest(),
        "stdout": process.stdout,
    }
    if process.returncode != 0:
        failure = classify_cli_read_failure(command.cli, process)
        receipt["error"] = {
            "channel": failure.channel,
            "code": failure.code,
            "retryable": failure.retryable,
            "gate_state": failure.gate_state.value,
        }
    return receipt


def _run_bounded_process(
    argv: Sequence[str], *, timeout: int
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = threading.Event()

    def drain(name: str, pipe) -> None:
        while chunk := pipe.read(64 * 1024):
            target = streams[name]
            remaining = MAX_CLI_OUTPUT_BYTES + 1 - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
            if len(target) > MAX_CLI_OUTPUT_BYTES:
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
        raise CliOutputLimitError
    return subprocess.CompletedProcess(
        args=list(argv),
        returncode=returncode,
        stdout=streams["stdout"].decode("utf-8", errors="replace"),
        stderr=streams["stderr"].decode("utf-8", errors="replace"),
    )


server = FastMCP(
    "reconciliation_cli",
    instructions="Run reviewed DWS or Lark read commands only.",
)


@server.tool(name="execute_reviewed_read")
def execute_reviewed_read_tool(argv: list[str]) -> dict[str, object]:
    return execute_reviewed_read(argv)


if __name__ == "__main__":
    server.run(transport="stdio")
