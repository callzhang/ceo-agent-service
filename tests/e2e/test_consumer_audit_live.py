"""Deterministic runner contracts plus opt-in native Codex checks."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext, MaterialReference
from app.agent_orchestrator import AgentOrchestrator
from app.audit_agent import AuditAgentRunner
from app.codex_runner import CodexRunner, _config_string
from app.consumer_agent import ConsumerAgentRunner
from app.native_cli_metadata import describe_native_command
from app.process_runner import run_process_with_idle_timeout
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore
from tests.support.audit_sink_mcp import AuditSink


QUESTION = "What specific decision or input do you need from Derek in this meeting?"
MESSAGE_TEXT = f"<@inviter-1> {QUESTION}"
SHARED_SKILL = """---
name: dingtalk-shared
description: Representative shared DWS operation fixture.
---
# Shared DWS Operations

Check the active organization and authentication with `dws auth status` before
using a product-specific DWS Skill. Preserve exact DingTalk identifiers returned
by reviewed reads.
"""
CALENDAR_SKILL = """---
name: dingtalk-calendar
description: Representative calendar operation fixture.
metadata:
  requires: dingtalk-shared
---
# Calendar Operations

Load `dingtalk-shared` before DWS calendar operations.
Read an invitation with `dws calendar event get --id <event-id> --format json`.
Respond with `dws calendar event respond --id <event-id> --status <status> --yes`.
This fixture has no calendar-comment capability.
Read the event again after every response.
"""
CHAT_SKILL = """---
name: dingtalk-chat
description: Representative source-chat operation fixture.
metadata:
  requires: dingtalk-shared
---
# Chat Operations

Load `dingtalk-shared` before DWS chat operations.
For calendar fallback, send in the source group with `dws chat message send --group <conversation-id> --at-open-dingtalk-ids <inviter-id> --text <question> --yes`.
Never open a direct chat when the source is a group. Read back with `dws chat message list --group <conversation-id> --time <date>` and verify the exact addressed question.
"""


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


def _json_section(prompt: str, heading: str):
    start = prompt.index(heading) + len(heading)
    value, _end = json.JSONDecoder().raw_decode(prompt[start:].lstrip())
    return value


def _skill_record(path: Path, call_id: str) -> tuple[dict[str, object], str]:
    content = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    receipt = {
        "content": content,
        "sha256": digest,
        "path": str(path.resolve()),
        "name": path.parent.name,
    }
    return (
        {
            "type": "item.completed",
            "item": {
                "id": call_id,
                "type": "mcp_tool_call",
                "server": "agent_cli",
                "tool": "read_skill",
                "arguments": {"path": str(path.resolve())},
                "status": "completed",
                "result": {
                    "content": [{"type": "text", "text": json.dumps(receipt)}],
                    "structuredContent": receipt,
                    "isError": False,
                },
            },
        },
        digest,
    )


def _reviewed_records(
    call_id: str,
    argv: list[str],
    *,
    write: bool = False,
    stdout: str = "{}",
) -> tuple[dict[str, object], dict[str, object]]:
    descriptor = describe_native_command(
        {"type": "command_execution", "argv": argv}
    )
    assert descriptor is not None
    receipt = {
        "cli": descriptor.cli,
        "operation": descriptor.command_path,
        "operation_digest": descriptor.command_digest,
        "target_identifiers": descriptor.target_identifiers,
        "result_digest": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stdout": stdout,
    }
    item = {
        "id": call_id,
        "type": "mcp_tool_call",
        "server": "agent_cli",
        "tool": "execute_reviewed_write" if write else "execute_reviewed_read",
        "arguments": {"argv": argv},
        "status": "in_progress",
    }
    return (
        {"type": "item.started", "item": item},
        {
            "type": "item.completed",
            "item": {
                **item,
                "status": "completed",
                "result": {
                    "content": [{"type": "text", "text": json.dumps(receipt)}],
                    "structuredContent": receipt,
                    "isError": False,
                },
            },
        },
    )


def _consumer_result_record(proposal: dict[str, object]) -> dict[str, object]:
    wire = {
        "outcome": "proposal",
        "summary": "Prepared one source-group clarification.",
        "proposal_json": json.dumps(proposal),
        "decision_options_json": "[]",
        "error_code": "",
        "error_retryable": False,
        "error_authorization_required": False,
    }
    return {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": json.dumps(wire)},
    }


def _audit_result_record(operation_id: str) -> dict[str, object]:
    external_result = {
        "operation_id": operation_id,
        "verification_summary": "The exact question is present in source group cid-1.",
        "live_result_reference": {
            "conversation_id": "cid-1",
            "message_id": "question-1",
        },
    }
    wire = {
        "outcome": "executed",
        "summary": "The source-group clarification was sent and verified.",
        "proposal_revision": 0,
        "side_effect_state": "confirmed",
        "feedback_json": None,
        "external_result_json": json.dumps(external_result),
        "reconciliation_json": "[]",
        "error_code": "",
        "error_retryable": False,
        "error_authorization_required": False,
    }
    return {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": json.dumps(wire)},
    }


class CalendarRunnerContractExecutor:
    def __init__(self, skill_paths: dict[str, Path]) -> None:
        self.skill_paths = skill_paths
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []
        self.handed_off_skills: list[dict[str, str]] = []

    def __call__(self, command, *, prompt: str, on_stdout_line, **_kwargs):
        self.commands.append(list(command))
        self.prompts.append(prompt)
        audit_turn = "Candidate revision\n" in prompt
        records = self._audit_records(prompt) if audit_turn else self._consumer_records(prompt)
        session = "calendar-audit-session" if audit_turn else "calendar-consumer-session"
        lines = [
            json.dumps({"type": "thread.started", "thread_id": session}),
            *(json.dumps(record) for record in records),
        ]
        stdout = "\n".join(lines)
        for line in lines:
            on_stdout_line(line)
        return ProcessRunResult(0, stdout, "")

    def _skill_records(self, role: str) -> list[dict[str, object]]:
        records = []
        for name in (
            "ceo-calendar-invite",
            "dingtalk-shared",
            "dingtalk-calendar",
            "dingtalk-chat",
        ):
            record, _digest = _skill_record(
                self.skill_paths[name],
                f"{role}-skill-{name}",
            )
            records.append(record)
        return records

    def _event_records(self, role: str) -> list[dict[str, object]]:
        event = {
            "event_id": "event-1",
            "title": "Portfolio review",
            "organizer": {
                "name": "Inviter",
                "open_dingtalk_id": "inviter-1",
            },
            "attendees": ["Derek", "Inviter"],
            "description": "Review the portfolio.",
            "comments": [],
            "linked_materials": [],
            "self_response": "needs_action",
            "conflicting_accepted_events": [],
            "requested_principal_input": None,
        }
        return list(
            _reviewed_records(
                f"{role}-event-read",
                ["dws", "calendar", "event", "get", "--id", "event-1", "--format", "json"],
                stdout=json.dumps(event),
            )
        )

    def _consumer_records(self, prompt: str) -> list[dict[str, object]]:
        materials = _json_section(
            prompt,
            "Raw material references and exact read commands\n",
        )
        assert materials[0]["read_commands"] == [
            "dws calendar event get --id event-1 --format json"
        ]
        proposal = {
            "objective": "Clarify the principal's requested meeting input.",
            "actions": [
                {
                    "description": "Ask the verified inviter in the source group.",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"group": "cid-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "message",
                            "send",
                            "--group",
                            "cid-1",
                            "--at-open-dingtalk-ids",
                            "inviter-1",
                            "--text",
                            MESSAGE_TEXT,
                            "--yes",
                        ]
                    },
                    "expected_verification": "Read source group cid-1 for the exact question.",
                }
            ],
            "sourced_facts": [
                {
                    "assertion": "The verified inviter openDingTalk ID is inviter-1.",
                    "references": ["calendar event event-1"],
                }
            ],
            "authored_judgment": "The requested principal input remains unclear.",
        }
        return [
            *self._skill_records("consumer"),
            *self._event_records("consumer"),
            _consumer_result_record(proposal),
        ]

    def _audit_records(self, prompt: str) -> list[dict[str, object]]:
        self.handed_off_skills = _json_section(
            prompt,
            "Verified Skills read by Consumer A\n",
        )
        candidate = _json_section(prompt, "Candidate revision\n")
        argv = candidate["proposal"]["actions"][0]["payload"]["argv"]
        assert candidate["proposal"]["actions"][0]["target"] == {"group": "cid-1"}
        assert "--user" not in argv
        verify_argv = [
            "dws",
            "chat",
            "message",
            "list",
            "--group",
            "cid-1",
            "--time",
            "2026-08-11",
        ]
        return [
            *self._skill_records("audit"),
            *self._event_records("audit"),
            *_reviewed_records(
                "audit-question-write",
                argv,
                write=True,
                stdout=json.dumps({"success": True, "message_id": "question-1"}),
            ),
            *_reviewed_records(
                "audit-question-verify",
                verify_argv,
                stdout=json.dumps(
                    {
                        "messages": [
                            {
                                "message_id": "question-1",
                                "conversation_id": "cid-1",
                                "mentioned_open_dingtalk_ids": ["inviter-1"],
                                "text": MESSAGE_TEXT,
                            }
                        ]
                    }
                ),
            ),
            _audit_result_record(candidate["operation_id"]),
        ]


def _persisted_skill_receipts(run) -> dict[str, tuple[str, str]]:
    receipts = {}
    for event in run.tool_events:
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict) or "skill_name" not in metadata:
            continue
        receipts[str(metadata["skill_name"])] = (
            str(metadata["skill_path"]),
            str(metadata["skill_sha256"]),
        )
    return receipts


def test_deterministic_native_runner_calendar_clarification_contract(
    tmp_path: Path,
    monkeypatch,
):
    skills_root = tmp_path / "installed-skills"
    repository_root = Path(__file__).resolve().parents[2]
    skill_contents = {
        "ceo-calendar-invite": (
            repository_root / "skills" / "ceo-calendar-invite" / "SKILL.md"
        ).read_text(encoding="utf-8"),
        "dingtalk-shared": SHARED_SKILL,
        "dingtalk-calendar": CALENDAR_SKILL,
        "dingtalk-chat": CHAT_SKILL,
    }
    skill_paths: dict[str, Path] = {}
    for name, content in skill_contents.items():
        path = skills_root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")
        skill_paths[name] = path.resolve()
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS",
        (skills_root,),
    )
    assert "event respond" in CALENDAR_SKILL
    assert "event comment" not in CALENDAR_SKILL
    assert "no calendar-comment capability" in CALENDAR_SKILL
    assert "source group" in CHAT_SKILL
    assert "dingtalk-shared" in CALENDAR_SKILL + CHAT_SKILL

    store = AutoReplyStore(tmp_path / "calendar-contract.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Calendar source group",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-11 10:00:00",
        trigger_sender="Inviter",
        trigger_text="Calendar invitation event-1",
        execution_generation="calendar-contract",
    )
    task = store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    context = AgentTaskContext(
        task_id=task.id,
        channel="dingtalk",
        conversation_id="cid-1",
        conversation_title="Calendar source group",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_sender="Inviter",
        trigger_text="Calendar invitation event-1",
        trigger_create_time="2026-08-11 10:00:00",
        messages=(),
        materials=(
            MaterialReference(
                kind="dingtalk_calendar",
                reference=json.dumps({"event_id": "event-1"}),
                source_message_id="msg-1",
                read_commands=(
                    "dws calendar event get --id event-1 --format json",
                ),
            ),
        ),
        prior_receipts=(),
        trigger_raw_payload={"eventId": "event-1"},
    )
    executor = CalendarRunnerContractExecutor(skill_paths)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
            owner="calendar-contract-consumer",
            codex_session_exists=lambda _session_id: True,
        ),
        audit=AuditAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
            owner="calendar-contract-audit",
        ),
    )
    result = orchestrator.process(task, context, refresh_context=lambda: context)

    assert result.status == "executed"
    assert result.final_role is AgentRole.AUDIT
    assert result.audit_result is not None
    assert result.audit_result.outcome.value == "executed"
    assert len(executor.commands) == 2
    assert all("--output-schema" in command for command in executor.commands)
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill", "read_spreadsheet"]'
        in executor.commands[0]
    )
    assert "execute_reviewed_write" not in " ".join(executor.commands[0])
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "execute_reviewed_write", "read_skill", "read_spreadsheet"]'
        in executor.commands[1]
    )
    assert "Raw material references and exact read commands" in executor.prompts[0]
    assert "Candidate revision" in executor.prompts[1]

    expected_receipts = {
        name: (
            str(path.resolve()),
            hashlib.sha256(skill_contents[name].encode("utf-8")).hexdigest(),
        )
        for name, path in skill_paths.items()
    }
    runs = store.list_agent_runs_for_task_generation(
        task.id,
        task.execution_generation,
    )
    assert [run.role for run in runs] == [AgentRole.CONSUMER, AgentRole.AUDIT]
    assert all(run.status == "completed" for run in runs)
    assert set(_persisted_skill_receipts(runs[0])) == {
        "ceo-calendar-invite",
        "dingtalk-shared",
        "dingtalk-calendar",
        "dingtalk-chat",
    }
    assert set(_persisted_skill_receipts(runs[1])) == {
        "ceo-calendar-invite",
        "dingtalk-shared",
        "dingtalk-calendar",
        "dingtalk-chat",
    }
    assert _persisted_skill_receipts(runs[0]) == expected_receipts
    assert _persisted_skill_receipts(runs[1]) == expected_receipts
    assert {
        item["name"]: (item["path"], item["sha256"])
        for item in executor.handed_off_skills
    } == expected_receipts

    audit_operations = [
        event["item"]["metadata"]["operation"]
        for event in runs[1].tool_events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and isinstance(event["item"].get("metadata"), dict)
    ]
    assert "calendar event get" in audit_operations
    assert "chat message send" in audit_operations
    assert "chat message list" in audit_operations
    audit_event_operations = [
        event["item"]["metadata"]["operation"]
        for event in runs[1].tool_events
        if isinstance(event.get("item"), dict)
        and isinstance(event["item"].get("metadata"), dict)
    ]
    write_index = audit_event_operations.index("chat message send")
    assert audit_event_operations[:write_index].count("read_skill") == len(
        expected_receipts
    )


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
