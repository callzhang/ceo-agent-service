"""Opt-in native Codex checks against the test-only AuditSink MCP server."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.codex_runner import CodexRunner, _config_string
from app.process_runner import run_process_with_idle_timeout
from tests.support.audit_sink_mcp import AuditSink


def _enabled() -> bool:
    return os.getenv("CEO_LIVE_CONSUMER_AUDIT_E2E") == "1"


def _session_id(stdout: str) -> str:
    for line in stdout.splitlines():
        payload = json.loads(line)
        if payload.get("type") == "thread.started" and payload.get("thread_id"):
            return str(payload["thread_id"])
    raise AssertionError("native Codex output did not include a session ID")


def _run(command: list[str], prompt: str):
    return run_process_with_idle_timeout(
        command,
        prompt=prompt,
        env={"PATH": os.environ["PATH"]},
        total_timeout_seconds=300,
        idle_timeout_seconds=120,
        on_stdout_line=lambda _line: None,
    )


def _allow_isolated_test_workspace(command: list[str]) -> list[str]:
    """Permit only this test's temporary non-Git Codex workspace."""
    allowed = list(command)
    allowed.insert(allowed.index("--cd"), "--skip-git-repo-check")
    return allowed


@pytest.mark.live
@pytest.mark.skipif(
    not _enabled(),
    reason="set CEO_LIVE_CONSUMER_AUDIT_E2E=1 to run native Consumer/Audit controlled-sink E2E",
)
def test_native_consumer_reuses_session_and_audit_writes_controlled_sink_once(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository_root = Path(__file__).resolve().parents[2]
    runner = CodexRunner(workspace=workspace)
    consumer_prompt = "Return exactly the word ready. Do not call tools."
    first = _run(
        _allow_isolated_test_workspace(runner.build_command(
            prompt=consumer_prompt,
            session_id=None,
            use_output_schema=False,
            approval_policy="never",
            developer_instructions="You are Consumer Agent A. Do not call tools.",
            use_approval_bypass=False,
            preserve_native_model_config=True,
        )),
        consumer_prompt,
    )
    assert first.returncode == 0, first.stderr
    session_id = _session_id(first.stdout)
    second = _run(
        runner.build_command(
            prompt="Return exactly the word resumed. Do not call tools.",
            session_id=session_id,
            use_output_schema=False,
            approval_policy="never",
            developer_instructions="You are Consumer Agent A. Do not call tools.",
            use_approval_bypass=False,
            preserve_native_model_config=True,
        ),
        "Return exactly the word resumed. Do not call tools.",
    )
    assert second.returncode == 0, second.stderr
    assert _session_id(second.stdout) == session_id
    assert '"effect":"effectful"' not in first.stdout + second.stdout

    sink = AuditSink(tmp_path / "audit-sink.sqlite3")
    operation_id = "native-audit-controlled-0"
    audit_prompt = (
        "Use audit_sink.write_state exactly once with operation_id "
        f"{operation_id} and payload {{\"result\":\"verified\"}}. Then use "
        "audit_sink.read_state for that same operation_id and report the returned JSON."
    )
    audit_command = _allow_isolated_test_workspace(runner.build_command(
        prompt=audit_prompt,
        session_id=None,
        use_output_schema=False,
        approval_policy="untrusted",
        developer_instructions="You are Audit Agent B. Use only the supplied audit_sink MCP tool.",
        use_approval_bypass=True,
        preserve_native_model_config=True,
    ))
    insert_at = audit_command.index("--cd")
    audit_command[insert_at:insert_at] = [
        "-c", _config_string("mcp_servers.audit_sink.command", sys.executable),
        "-c", _config_string(
            "mcp_servers.audit_sink.args",
            ["-m", "tests.support.audit_sink_mcp", str(sink.path)],
        ),
        # Codex itself runs from an isolated workspace. The test MCP is a Python
        # module in this repository, so it must declare the repository cwd.
        "-c", _config_string("mcp_servers.audit_sink.cwd", str(repository_root)),
        "-c", 'mcp_servers.audit_sink.enabled_tools=["read_state","write_state"]',
    ]
    audit = _run(audit_command, audit_prompt)
    assert audit.returncode == 0, audit.stderr
    assert _session_id(audit.stdout) != session_id
    assert sink.row_count(operation_id) == 1, audit.stdout + audit.stderr
    assert sink.read_state(operation_id) is not None
