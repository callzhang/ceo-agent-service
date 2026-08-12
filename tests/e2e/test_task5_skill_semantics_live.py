"""Opt-in native Codex checks for Task 5 business-Skill semantics."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from app.agent_wire_contracts import parse_consumer_agent_wire_result
from app.codex_runner import CodexRunner
from app.consumer_agent import consumer_developer_instructions
from app.process_runner import run_process_with_idle_timeout
from tests.support.native_codex_read_fixture import (
    assert_isolated_read_only_fixture_command,
    isolate_read_only_fixture_command,
)


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("CEO_LIVE_TASK5_SKILL_E2E") != "1",
        reason="set CEO_LIVE_TASK5_SKILL_E2E=1 to run native Task 5 Skill checks",
    ),
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTALLED_SKILLS = Path.home() / ".agents" / "skills" / "dws" / "multi"
MEETING_INFO = ["dws", "minutes", "get", "info", "--id", "minutes-1", "--format", "json"]
MEETING_SUMMARY = [
    "dws", "minutes", "get", "summary", "--id", "minutes-1", "--format", "json"
]
MEETING_TASKS = ["dws", "minutes", "get", "todos", "--id", "minutes-1", "--format", "json"]
MEETING_TRANSCRIPT = [
    "dws", "minutes", "get", "transcription", "--id", "minutes-1", "--format", "json"
]
MAILBOX_LIST = ["dws", "mail", "mailbox", "list", "--format", "json"]
MAIL_SEARCH = [
    "dws", "mail", "message", "search", "--email", "principal@example.test",
    "--query", "subject:Contract approval", "--limit", "20", "--format", "json",
]
MAIL_GET = [
    "dws", "mail", "message", "get", "--email", "principal@example.test",
    "--id", "mail-1", "--format", "json",
]
MAIL_GET_2 = [
    "dws", "mail", "message", "get", "--email", "principal@example.test",
    "--id", "mail-2", "--format", "json",
]
MAIL_GET_3 = [
    "dws", "mail", "message", "get", "--email", "principal@example.test",
    "--id", "mail-3", "--format", "json",
]
MAIL_SENT = [
    "dws", "mail", "message", "list", "--email", "principal@example.test",
    "--folder-id", "1", "--limit", "20", "--format", "json",
]
DOC_READ = [
    "dws", "doc", "read", "--node", "contract-1", "--format", "json",
]


def _skill_paths(business_skill: str, *operation_skills: str) -> list[Path]:
    return [
        REPOSITORY_ROOT / "skills" / business_skill / "SKILL.md",
        *(INSTALLED_SKILLS / name / "SKILL.md" for name in operation_skills),
    ]


def _native_consumer(
    tmp_path: Path,
    *,
    request: str,
    context: str,
    skill_paths: list[Path],
    operation_responses: list[tuple[list[str], object]],
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "fixture.json"
    log_path = tmp_path / "events.jsonl"
    config_path.write_text(
        json.dumps(
            {
                "skill_paths": [str(path.resolve()) for path in skill_paths],
                "operation_responses": [
                    {"argv": argv, "stdout": json.dumps(response, ensure_ascii=False)}
                    for argv, response in operation_responses
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompt = (
        f"Current request:\n{request}\n\n"
        f"Visible trigger context:\n{context}\n\n"
        "Available verified Skill paths:\n"
        + "\n".join(str(path.resolve()) for path in skill_paths)
        + "\n\nAvailable exact reviewed read commands:\n"
        + "\n".join(json.dumps(argv) for argv, _response in operation_responses)
        + "\n\nUse the business and operation Skills to perform the review. Read only "
        "evidence needed for the current request. Return the strict Consumer result."
    )
    runner = CodexRunner(workspace=workspace)
    command = runner.build_command(
        prompt=prompt,
        session_id=None,
        use_output_schema=False,
        approval_policy="never",
        developer_instructions=consumer_developer_instructions(
            "Perform business judgment only from verified evidence."
        ),
        use_approval_bypass=False,
    )
    assert "--output-schema" not in command
    command.insert(command.index("--cd"), "--skip-git-repo-check")
    command = isolate_read_only_fixture_command(
        command,
        server_command=sys.executable,
        server_args=(
            "-m",
            "tests.support.task5_read_fixture_mcp",
            str(config_path),
            str(log_path),
        ),
        server_cwd=str(REPOSITORY_ROOT),
    )
    assert_isolated_read_only_fixture_command(command)
    process = run_process_with_idle_timeout(
        command,
        prompt=prompt,
        env={"PATH": os.environ["PATH"]},
        total_timeout_seconds=300,
        idle_timeout_seconds=120,
        on_stdout_line=lambda _line: None,
    )
    assert process.returncode == 0, process.stderr
    records = [json.loads(line) for line in process.stdout.splitlines()]
    messages = [
        record["item"]["text"]
        for record in records
        if record.get("type") == "item.completed"
        and isinstance(record.get("item"), dict)
        and record["item"].get("type") == "agent_message"
    ]
    assert messages, process.stdout
    result = parse_consumer_agent_wire_result(process.stdout)
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    skill_events = [event for event in events if event["tool"] == "read_skill"]
    assert {
        (event["result"]["path"], event["result"]["sha256"])
        for event in skill_events
    } == {
        (
            str(path.resolve()),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in skill_paths
    }
    return result, events


def _read_argv(events: list[dict[str, object]]) -> list[list[str]]:
    return [
        event["arguments"]["argv"]
        for event in events
        if event["tool"] == "execute_reviewed_read"
    ]


def _proposal_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _proposal_strings(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _proposal_strings(item)]
    return []


def test_native_meeting_uses_summary_and_tasks_without_unneeded_transcript(tmp_path: Path):
    skills = _skill_paths(
        "ceo-meeting-work", "dingtalk-shared", "dingtalk-minutes", "dingtalk-chat"
    )
    result, events = _native_consumer(
        tmp_path,
        request="Prepare the concrete follow-up from this silent meeting.",
        context="Minutes reference: minutes-1. Source group: cid-1.",
        skill_paths=skills,
        operation_responses=[
            (MEETING_INFO, {"title": "Launch readiness"}),
            (MEETING_SUMMARY, {"summary": "Launch plan and risk notice agreed."}),
            (MEETING_TASKS, {"todos": [
                {"owner": "Alex", "task": "Publish the launch plan Friday"},
                {"owner": "Mina", "task": "Send the risk thresholds Thursday"},
            ]}),
            (MEETING_TRANSCRIPT, {"paragraphs": [{"speaker": "Alex", "text": "Duplicate detail"}]}),
        ],
    )

    assert MEETING_TRANSCRIPT not in _read_argv(events)
    assert result.outcome.value == "proposal"
    proposal_strings = [
        text
        for action in result.proposal.actions
        for text in _proposal_strings(action.payload)
    ]
    alex_mentions = [text for text in proposal_strings if "Alex" in text]
    mina_mentions = [text for text in proposal_strings if "Mina" in text]
    assert alex_mentions and all("launch plan" in text.casefold() for text in alex_mentions)
    assert mina_mentions and all("risk thresholds" in text.casefold() for text in mina_mentions)
    assert all(not text.lstrip().startswith("@Alex @Mina") for text in proposal_strings)
    loaded = {event["result"]["name"] for event in events if event["tool"] == "read_skill"}
    assert loaded == {
        "ceo-meeting-work",
        "dingtalk-shared",
        "dingtalk-minutes",
        "dingtalk-chat",
    }


def test_native_meeting_reads_transcript_only_to_resolve_missing_attribution(tmp_path: Path):
    result, events = _native_consumer(
        tmp_path,
        request="Prepare the concrete follow-up from this silent meeting.",
        context="Minutes reference: minutes-1. Source group: cid-1.",
        skill_paths=_skill_paths(
            "ceo-meeting-work", "dingtalk-shared", "dingtalk-minutes", "dingtalk-chat"
        ),
        operation_responses=[
            (MEETING_INFO, {"title": "Launch readiness"}),
            (MEETING_SUMMARY, {"summary": "Someone will publish the plan Friday."}),
            (MEETING_TASKS, {"todos": [{"owner": None, "task": "Publish the launch plan Friday"}]}),
            (MEETING_TRANSCRIPT, {"paragraphs": [{"speaker": "Alex", "text": "I will publish the launch plan Friday."}]}),
        ],
    )

    assert MEETING_TRANSCRIPT in _read_argv(events)
    assert result.outcome.value == "proposal"
    rendered = json.dumps(result.proposal.model_dump(), ensure_ascii=False)
    assert "Alex" in rendered and "Publish the launch plan Friday" in rendered


def test_native_meeting_with_no_action_returns_no_action(tmp_path: Path):
    result, events = _native_consumer(
        tmp_path,
        request="Review this silent meeting.",
        context="Minutes reference: minutes-1.",
        skill_paths=_skill_paths(
            "ceo-meeting-work", "dingtalk-shared", "dingtalk-minutes"
        ),
        operation_responses=[
            (MEETING_INFO, {"title": "Industry briefing"}),
            (MEETING_SUMMARY, {"summary": "Informational briefing; no decision, task, or question."}),
            (MEETING_TASKS, {"todos": []}),
            (MEETING_TRANSCRIPT, {"paragraphs": []}),
        ],
    )

    assert result.outcome.value == "no_action"
    assert result.proposal is None
    assert all(event["tool"] != "execute_reviewed_write" for event in events)


def test_native_meeting_requests_only_the_specific_missing_material(tmp_path: Path):
    result, _events = _native_consumer(
        tmp_path,
        request="Close the concrete follow-up from this silent meeting.",
        context="Minutes reference: minutes-1. Source group: cid-1.",
        skill_paths=_skill_paths(
            "ceo-meeting-work", "dingtalk-shared", "dingtalk-minutes", "dingtalk-chat"
        ),
        operation_responses=[
            (MEETING_INFO, {"title": "Risk approval"}),
            (MEETING_SUMMARY, {
                "summary": (
                    "Approval depends only on the risk-approval attachment, which is "
                    "referenced but absent from the available meeting material."
                )
            }),
            (MEETING_TASKS, {"todos": []}),
        ],
    )

    assert result.outcome.value == "proposal"
    assert len(result.proposal.actions) == 1
    proposal_text = " ".join(_proposal_strings(result.proposal.model_dump()))
    assert "risk-approval attachment" in proposal_text.casefold()
    assert "recap" not in proposal_text.casefold()
    assert "a/b" not in proposal_text.casefold()
    assert result.decision_options == ()


def test_native_review_only_mail_reads_original_and_material_without_reply(tmp_path: Path):
    result, events = _native_consumer(
        tmp_path,
        request="Review this mail and its linked contract. Do not reply or draft a reply.",
        context="Truncated card: Contract approval - Please approve...",
        skill_paths=_skill_paths(
            "ceo-mail-review", "dingtalk-shared", "dingtalk-mail", "dingtalk-doc"
        ),
        operation_responses=[
            (MAILBOX_LIST, {"mailboxes": [{"email": "principal@example.test"}]}),
            (MAIL_SEARCH, {"messages": [{"messageId": "mail-1"}]}),
            (MAIL_GET, {"messageId": "mail-1", "body": "Review contract-1; no reply requested."}),
            (DOC_READ, {"nodeId": "contract-1", "content": "Standard terms; no exception."}),
        ],
    )

    reads = _read_argv(events)
    assert MAIL_GET in reads and DOC_READ in reads
    assert result.outcome.value == "no_action"
    assert result.proposal is None
    assert all(event["tool"] != "execute_reviewed_write" for event in events)


def test_native_complete_mail_thread_suppresses_duplicate_reply(tmp_path: Path):
    result, events = _native_consumer(
        tmp_path,
        request="Reply to the Contract approval mail after reviewing the complete thread.",
        context="Truncated card: Contract approval - Please approve...",
        skill_paths=_skill_paths("ceo-mail-review", "dingtalk-shared", "dingtalk-mail"),
        operation_responses=[
            (MAILBOX_LIST, {"mailboxes": [{"email": "principal@example.test"}]}),
            (MAIL_SEARCH, {"messages": [
                {"messageId": "mail-1", "threadId": "thread-1"},
                {"messageId": "mail-2", "threadId": "thread-1"},
                {"messageId": "mail-3", "threadId": "thread-1"},
            ]}),
                (MAIL_GET, {
                    "messageId": "mail-1",
                    "threadId": "thread-1",
                    "direction": "inbound",
                    "from": "requester@example.test",
                    "body": "Please approve the original standard terms.",
                }),
                (MAIL_GET_2, {
                    "messageId": "mail-2",
                    "threadId": "thread-1",
                    "direction": "inbound",
                    "from": "legal@example.test",
                    "body": "Legal changed the liability cap to five million.",
                }),
                (MAIL_GET_3, {
                    "messageId": "mail-3",
                    "threadId": "thread-1",
                    "direction": "inbound",
                    "from": "requester@example.test",
                    "body": "Please use the five-million cap in the final review.",
                }),
                (MAIL_SENT, {"messages": [{
                    "threadId": "thread-1",
                    "body": "Approved subject to the five-million liability cap.",
                }]}),
        ],
    )

    reads = _read_argv(events)
    assert all(command in reads for command in (MAIL_GET, MAIL_GET_2, MAIL_GET_3))
    assert MAIL_SENT in reads
    assert result.outcome.value == "no_action"
    assert result.proposal is None


def test_native_mail_chat_response_is_draft_only_not_authorized_send(tmp_path: Path):
    result, events = _native_consumer(
        tmp_path,
        request=(
            "Draft a possible reply in this chat after review. Do not send or propose "
            "sending any mail."
        ),
        context="Truncated card: Contract approval - Please approve... Source group: cid-1.",
        skill_paths=_skill_paths(
            "ceo-mail-review", "dingtalk-shared", "dingtalk-mail", "dingtalk-doc",
            "dingtalk-chat",
        ),
        operation_responses=[
            (MAILBOX_LIST, {"mailboxes": [{"email": "principal@example.test"}]}),
            (MAIL_SEARCH, {"messages": [{"messageId": "mail-1"}]}),
            (MAIL_GET, {"messageId": "mail-1", "body": "Review contract-1."}),
            (DOC_READ, {"nodeId": "contract-1", "content": "Standard terms."}),
        ],
    )

    assert MAIL_GET in _read_argv(events) and DOC_READ in _read_argv(events)
    assert result.outcome.value == "proposal"
    assert all(action.operation != "mail message reply" for action in result.proposal.actions)
    assert "draft" in " ".join(_proposal_strings(result.proposal.model_dump())).casefold()
