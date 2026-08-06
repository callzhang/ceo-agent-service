import json
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext
from app.consumer_agent import ConsumerAgentRunner
from app.native_cli_metadata import AgentReadOnlyViolationError
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore


class CapturingExecutor:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.commands: list[list[str]] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        del kwargs
        self.commands.append(command)
        for line in self.stdout.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(0, self.stdout, "")


def _result_jsonl(*, session: str = "session-a") -> str:
    result = {
        "outcome": "no_action",
        "summary": "Nothing to do.",
        "proposal": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    }
    return "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": session}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(result)}}),
        )
    )


def _proposal_jsonl(payload: dict[str, object]) -> str:
    result = {
        "outcome": "proposal",
        "summary": "Prepared a candidate.",
        "proposal": {
            "objective": "Send result",
            "actions": [
                {
                    "description": "Send",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"group": "cid-agent"},
                    "payload": payload,
                    "expected_verification": "Message exists",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "",
        },
        "error": {"code": "", "retryable": False, "authorization_required": False},
    }
    return "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-a"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(result)},
                }
            ),
        )
    )


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "agent.sqlite3")


@pytest.fixture
def task(store):
    store.enqueue_reply_task(
        conversation_id="cid-agent", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-08-06 10:00:00",
        trigger_sender="Derek", trigger_text="Check this", execution_generation="gen-1",
    )
    return store.claim_reply_tasks(limit=1)[0]


@pytest.fixture
def context(task):
    return AgentTaskContext(
        task_id=task.id, channel=task.channel, conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(), prior_receipts=(),
    )


def test_consumer_is_read_only_and_reuses_conversation_session(store, task, context):
    store.upsert_conversation(task.conversation_id, "Group", False, "session-a")
    executor = CapturingExecutor(_result_jsonl())

    result = ConsumerAgentRunner(
        store=store, workspace=Path("/workspace"), executor=executor,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    command = executor.commands[0]
    assert command[:3] == ["codex", "exec", "resume"]
    assert command[-2:] == ["session-a", "-"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--output-schema" not in command
    assert 'approval_policy="never"' in command
    assert "features.plugins=false" in command
    assert "features.apps=false" in command
    assert 'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill"]' in command
    assert "execute_reviewed_write" not in " ".join(command)
    assert store.get_agent_run(result.run_id).role.value == "consumer"
    assert any(
        "Output JSON Schema (validated locally):" in option
        for option in command
    )


def test_consumer_renews_lease_for_every_jsonl_record(
    store,
    task,
    context,
    monkeypatch,
):
    calls = 0
    original = store.renew_agent_run_lease

    def renew(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "renew_agent_run_lease", renew)
    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(_result_jsonl()),
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert calls == 2


def test_consumer_reports_session_lock_release_failure(
    store,
    task,
    context,
    monkeypatch,
):
    monkeypatch.setattr(store, "release_codex_session_lock", lambda *_: False)

    with pytest.raises(RuntimeError, match="codex session lock release failed"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(_result_jsonl()),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)


def test_consumer_rejects_schema_artifact_drift(
    store,
    task,
    context,
    tmp_path,
    monkeypatch,
):
    schema_path = tmp_path / "consumer.schema.json"
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    monkeypatch.setattr("app.consumer_agent.SCHEMA_PATH", schema_path)

    with pytest.raises(ValueError, match="schema does not match"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(_result_jsonl()),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)


@pytest.mark.parametrize(
    "payload",
    (
        {"access_token": "secret"},
        {"encoded": json.dumps({"cookie": "secret"})},
        {"url": "https://example.com/file?X-Amz-Signature=secret"},
        {"header": "Authorization: Bearer abcdef1234567890"},
        {
            "argv": [
                "dws", "chat", "message", "send", "--token", "opaque-value",
            ]
        },
    ),
)
def test_consumer_rejects_sensitive_result_payload(store, task, context, payload):
    with pytest.raises(ValueError, match="agent_result_contains_sensitive_value"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(_proposal_jsonl(payload)),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)


def test_consumer_rejects_effectful_stream_event(store, task, context):
    effect = json.dumps({
        "type": "item.started",
        "item": {"type": "mcp_tool_call", "id": "write-1", "server": "memory_connector", "tool": "memory_write", "arguments": {}},
    })
    executor = CapturingExecutor(effect + "\n" + _result_jsonl())

    with pytest.raises(AgentReadOnlyViolationError):
        ConsumerAgentRunner(store=store, workspace=Path("/workspace"), executor=executor).run(
            task, context, proposal_revision=0, parent_agent_run_id=None,
        )


def test_consumer_rejects_direct_shell_event(store, task, context):
    shell = json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "id": "shell-1",
                "command": "dws chat message send",
            },
        }
    )
    executor = CapturingExecutor(shell + "\n" + _result_jsonl())

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="agent_shell_execution_forbidden",
    ):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)
