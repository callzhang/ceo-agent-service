from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable, Sequence

from mcp.server.fastmcp import FastMCP

from app.agent_result import EffectKind
from app.agent_runner import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
)


def execute_reviewed_read(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
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
    process = process_runner(
        reviewed_argv,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError("reconciliation_read_failed")
    return {
        "cli": command.cli,
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "result_digest": hashlib.sha256(process.stdout.encode("utf-8")).hexdigest(),
        "stdout": process.stdout,
    }


server = FastMCP(
    "reconciliation_cli",
    instructions="Run reviewed DWS or Lark read commands only.",
)


@server.tool(name="execute_reviewed_read")
def execute_reviewed_read_tool(argv: list[str]) -> dict[str, object]:
    return execute_reviewed_read(argv)


if __name__ == "__main__":
    server.run(transport="stdio")
