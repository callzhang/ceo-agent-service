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
from tests.test_agent_runtime_worker import (
    CalendarClarificationProtocolExecutor,
    _enqueue,
    _message,
    _prompt_json_section,
    _worker_with_protocol_executor,
)


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


def test_scripted_calendar_clarification_is_executed_without_human_handoff(
    tmp_path: Path,
    monkeypatch,
):
    skills_root = tmp_path / "installed-skills"
    skill_paths: dict[str, Path] = {}
    repository_root = Path(__file__).resolve().parents[2]
    for name in ("ceo-calendar-invite", "dingtalk-calendar", "dingtalk-chat"):
        path = skills_root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        content = (
            (repository_root / "skills" / name / "SKILL.md").read_text(
                encoding="utf-8"
            )
            if name == "ceo-calendar-invite"
            else f"---\nname: {name}\n---\n# {name}\n"
        )
        path.write_text(content, encoding="utf-8")
        skill_paths[name] = path
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS",
        (skills_root,),
    )

    trigger = _message(
        "dingtalk://dingtalkclient/action/open_mini_app?page=detail%3FuniqueId%3Devent-1",
        raw_payload={"eventId": "event-1"},
    )
    executor = CalendarClarificationProtocolExecutor(skill_paths)
    worker, _dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        executor,
    )
    _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None and attempt.send_status == "completed"
    assert attempt.send_status != "needs_human"
    assert executor.consumer_loaded_skills[0] == "ceo-calendar-invite"
    assert executor.audit_loaded_skills == executor.consumer_loaded_skills
    assert executor.event_reads == 2
    assert executor.sent_questions == 1
    assert CalendarClarificationProtocolExecutor.question in executor.prompts[1]
    candidate = _prompt_json_section(executor.prompts[1], "Candidate revision\n")
    action = candidate["proposal"]["actions"][0]
    argv = action["payload"]["argv"]
    assert action["target"] == {"group": "cid-1"}
    assert argv[argv.index("--group") + 1] == "cid-1"
    assert argv[argv.index("--at-open-dingtalk-ids") + 1] == "inviter-1"
    assert "<@inviter-1>" in argv[argv.index("--text") + 1]
    assert "--user" not in argv
    assert "--group cid-1" in executor.question_write_command
    assert "--user" not in executor.question_write_command
    assert executor.question_verify_command == (
        "dws chat message list --group cid-1 --time 2026-07-29"
    )
    assert "--user" not in executor.question_verify_command


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
