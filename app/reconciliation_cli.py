from __future__ import annotations

import hashlib
import errno
import shutil
import subprocess
from collections.abc import Callable, Sequence

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.agent_result import EffectKind
from app.bounded_process import (
    MAX_PROCESS_OUTPUT_BYTES,
    ProcessOutputLimitError,
    run_bounded_process,
)
from app.channel_gate import classify_cli_read_failure
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
    NativeCliMetadataUnavailableError,
    describe_native_command,
)

MAX_CLI_OUTPUT_BYTES = MAX_PROCESS_OUTPUT_BYTES
CliOutputLimitError = ProcessOutputLimitError


def _process_failure_receipt(
    command, *, code: str, retryable: bool
) -> dict[str, object]:
    return {
        "cli": command.cli,
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "result_digest": hashlib.sha256(b"").hexdigest(),
        "stdout": "",
        "error": {
            "channel": command.cli,
            "code": code,
            "retryable": retryable,
            "gate_state": "unavailable",
        },
    }


def execute_reviewed_read(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    return _execute_reviewed(
        argv,
        expected_effect=EffectKind.READ_ONLY,
        classifier=classifier,
        process_runner=process_runner,
    )


def execute_reviewed_write(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    return _execute_reviewed(
        argv,
        expected_effect=EffectKind.EFFECTFUL,
        classifier=classifier,
        process_runner=process_runner,
    )


def _execute_reviewed(
    argv: Sequence[str],
    *,
    expected_effect: EffectKind,
    classifier: NativeCliMetadataClassifier | None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None,
) -> dict[str, object]:
    if not argv:
        raise AgentReadOnlyViolationError("reconciliation_command_invalid")
    reviewed = classifier or NativeCliMetadataClassifier()
    item = {"type": "command_execution", "argv": list(argv)}
    descriptor = describe_native_command(item)
    if descriptor is None:
        raise AgentReadOnlyViolationError("reconciliation_command_invalid")
    try:
        reviewed.prewarm()
        command = reviewed.classify(item)
    except NativeCliMetadataUnavailableError as exc:
        return _process_failure_receipt(
            descriptor,
            code=exc.code,
            retryable=exc.retryable,
        )
    if command is None:
        raise AgentReadOnlyViolationError("reconciliation_command_unreviewed")
    if command.effect is not expected_effect:
        raise AgentReadOnlyViolationError("reviewed_cli_effect_mismatch")
    executable = shutil.which(command.cli)
    if executable is None:
        return _process_failure_receipt(
            command,
            code="reconciliation_cli_start_unavailable",
            retryable=True,
        )
    reviewed_argv = [executable, *argv[1:]]
    try:
        process = (
            run_bounded_process(reviewed_argv, timeout=120)
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
            raise CliOutputLimitError(stdout_bytes=0, stderr_bytes=0)
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
    except subprocess.TimeoutExpired:
        return _process_failure_receipt(
            command,
            code="reconciliation_cli_timeout",
            retryable=True,
        )
    except OSError as exc:
        retryable = exc.errno not in {errno.EINVAL, errno.EACCES, errno.ENOEXEC}
        return _process_failure_receipt(
            command,
            code=(
                "reconciliation_cli_start_unavailable"
                if retryable
                else "reconciliation_cli_start_invalid"
            ),
            retryable=retryable,
        )
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


server = FastMCP(
    "reconciliation_cli",
    instructions="Run DWS or Lark commands only after reviewing installed effect metadata.",
)


@server.tool(
    name="execute_reviewed_read",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def execute_reviewed_read_tool(argv: list[str]) -> dict[str, object]:
    return execute_reviewed_read(argv)


@server.tool(
    name="execute_reviewed_write",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def execute_reviewed_write_tool(argv: list[str]) -> dict[str, object]:
    return execute_reviewed_write(argv)


if __name__ == "__main__":
    server.run(transport="stdio")
