import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext
from app.consumer_agent import ConsumerAgentRunner
from app.agent_result import EffectKind, ResultParseError
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
)
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore


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


class FailingExecutor(CapturingExecutor):
    def __init__(self, stdout: str, *, stderr: str = "") -> None:
        super().__init__(stdout)
        self.stderr = stderr

    def __call__(self, command, *, on_stdout_line, **kwargs):
        super().__call__(command, on_stdout_line=on_stdout_line, **kwargs)
        return ProcessRunResult(1, self.stdout, self.stderr)


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


def _failed_reviewed_read_jsonl() -> str:
    argv = ["dws", "oa", "approval", "detail", "--instance-id", "pid-1"]
    from app.native_cli_metadata import describe_native_command

    descriptor = describe_native_command({"type": "command_execution", "argv": argv})
    assert descriptor is not None
    receipt = {
        "cli": descriptor.cli,
        "operation": descriptor.command_path,
        "operation_digest": descriptor.command_digest,
        "target_identifiers": descriptor.target_identifiers,
        "result_digest": "failed-read-digest",
        "error": {"code": "credential_store_unavailable", "retryable": True},
    }
    result = {
        "outcome": "failed",
        "summary": "DWS read is unavailable.",
        "proposal": None,
        "error": {
            "code": "dws_transient_dependency_unavailable",
            "retryable": True,
            "authorization_required": False,
        },
    }
    return "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-a"}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "read-1",
                        "type": "mcp_tool_call",
                        "server": "agent_cli",
                        "tool": "execute_reviewed_read",
                        "arguments": {"argv": argv},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "read-1",
                        "type": "mcp_tool_call",
                        "server": "agent_cli",
                        "tool": "execute_reviewed_read",
                        "arguments": {"argv": argv},
                        "status": "failed",
                        "result": {
                            "structuredContent": receipt,
                            "isError": True,
                        },
                    },
                }
            ),
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
    assert "--sandbox" not in command
    assert 'sandbox_mode="read-only"' not in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "tools.enabled_tools=[]" in command
    assert "--output-schema" not in command
    assert 'approval_policy="never"' in command
    assert "features.plugins=false" not in command
    assert "features.apps=false" not in command
    assert 'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill"]' in command
    assert "execute_reviewed_write" not in " ".join(command)
    assert store.get_agent_run(result.run_id).role.value == "consumer"
    assert any(
        "Output JSON Schema (validated locally):" in option
        for option in command
    )
    assert any(
        "call `agent_cli.execute_reviewed_read`" in option
        for option in command
    )
    assert any(
        "Authoritative Consumer role boundary" in option
        and "valid ConsumerAgentResult JSON" in option
        for option in command
    )


def test_consumer_rotates_damaged_session_after_missing_final_result(
    store, task, context
):
    store.upsert_conversation(task.conversation_id, "Group", False, "session-a")
    executor = CapturingExecutor(
        json.dumps({"type": "thread.started", "thread_id": "session-a"})
    )

    with pytest.raises(ResultParseError, match="no valid typed result"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert store.get_codex_session_id(task.conversation_id) is None
    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert json.loads(run.structured_error_json)["code"] == "codex_result_missing"


def test_consumer_keeps_a_reviewed_read_failure_visible_to_the_agent(
    store, task, context
):
    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(_failed_reviewed_read_jsonl()),
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run(result.run_id)
    assert result.result.outcome.value == "failed"
    assert run is not None
    assert run.tool_events[-1]["type"] == "item.failed"
    assert run.tool_events[-1]["item"]["metadata"]["operation"] == "oa approval detail"


def test_consumer_rotates_session_when_codex_exits_without_a_final_result(
    store, task, context
):
    store.upsert_conversation(task.conversation_id, "Group", False, "session-a")
    executor = FailingExecutor(
        "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "session-a"}),
                json.dumps(
                    {
                        "type": "task_complete",
                        "last_agent_message": None,
                    }
                ),
            )
        )
    )

    with pytest.raises(ResultParseError, match="no valid typed result"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert store.get_codex_session_id(task.conversation_id) is None
    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert json.loads(run.structured_error_json)["code"] == "codex_result_missing"


def test_consumer_classifies_codex_capacity_exhaustion_as_retryable_provider_wait(
    store, task, context
):
    stdout = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-capacity"}),
            json.dumps(
                {
                    "type": "error",
                    "message": "You've hit your usage limit. Try again later.",
                }
            ),
            json.dumps({"type": "turn.failed"}),
        )
    )

    with pytest.raises(RuntimeError, match="codex_provider_unavailable"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=FailingExecutor(stdout),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert '"code":"codex_provider_unavailable"' in run.structured_error_json
    assert '"retryable":true' in run.structured_error_json


def test_consumer_preserves_codex_cli_authentication_failure(store, task, context):
    stdout = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-auth"}),
            json.dumps(
                {
                    "type": "error",
                    "message": (
                        "unexpected status 401 Unauthorized: Missing bearer or "
                        "basic authentication in header, url: "
                        "https://api.openai.com/v1/responses"
                    ),
                }
            ),
            json.dumps({"type": "turn.failed"}),
        )
    )

    with pytest.raises(RuntimeError, match="codex_provider_auth_failed"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=FailingExecutor(stdout),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    error = json.loads(run.structured_error_json)
    assert error["code"].startswith(
        "codex_provider_auth_failed"
    )
    assert error["authorization_required"] is True


def test_consumer_reads_current_audit_rules_for_each_turn(
    store,
    task,
    context,
    tmp_path,
    monkeypatch,
):
    rules_path = tmp_path / "audit_rules.md"
    rules_path.write_text("First rule version.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(rules_path))
    first_executor = CapturingExecutor(_result_jsonl(session="session-a"))

    runner = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=first_executor,
    )
    runner.run(task, context, proposal_revision=0, parent_agent_run_id=None)

    rules_path.write_text("Second rule version.", encoding="utf-8")
    store.enqueue_reply_task(
        conversation_id="cid-agent-2",
        conversation_title="Group 2",
        single_chat=False,
        trigger_message_id="msg-2",
        trigger_create_time="2026-08-06 10:01:00",
        trigger_sender="Derek",
        trigger_text="Check this too",
        execution_generation="gen-2",
    )
    second_task = store.claim_reply_tasks(limit=1)[0]
    second_context = replace(
        context,
        task_id=second_task.id,
        conversation_id=second_task.conversation_id,
        conversation_title=second_task.conversation_title,
        trigger_message_id=second_task.trigger_message_id,
        trigger_text=second_task.trigger_text,
    )
    runner.run(
        second_task,
        second_context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )

    assert any("First rule version." in item for item in first_executor.commands[0])
    assert any("do not execute" in item for item in first_executor.commands[0])
    assert any("Second rule version." in item for item in first_executor.commands[1])


def test_consumer_validates_audit_rules_before_claiming_run(
    store,
    task,
    context,
    monkeypatch,
):
    def fail_rules(_role):
        raise OSError("rules unavailable")

    monkeypatch.setattr("app.consumer_agent.render_audit_rules", fail_rules)

    with pytest.raises(OSError, match="rules unavailable"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(_result_jsonl()),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    ) is None


def test_consumer_renews_run_and_session_leases_for_every_jsonl_record(
    store,
    task,
    context,
    monkeypatch,
):
    run_calls = 0
    session_calls = 0
    original_run = store.renew_agent_run_lease
    original_session = store.renew_codex_session_lock

    def renew_run(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        return original_run(*args, **kwargs)

    def renew_session(*args, **kwargs):
        nonlocal session_calls
        session_calls += 1
        assert args == (
            task.conversation_id,
            f"consumer-agent:{task.id}:{task.execution_generation}",
        )
        renewed = original_session(*args, **kwargs)
        assert store.acquire_codex_session_lock(
            task.conversation_id,
            "concurrent-consumer",
        ) is False
        return renewed

    monkeypatch.setattr(store, "renew_agent_run_lease", renew_run)
    monkeypatch.setattr(store, "renew_codex_session_lock", renew_session)
    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(_result_jsonl()),
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert run_calls == 2
    assert session_calls == 2


def test_consumer_releases_session_lock_after_progress_callback_failure(
    store,
    task,
    context,
    monkeypatch,
):
    def fail_renewal(*_args, **_kwargs):
        return False

    monkeypatch.setattr(store, "renew_codex_session_lock", fail_renewal)

    with pytest.raises(RuntimeError, match="codex_session_lock_lost"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(_result_jsonl()),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert store.acquire_codex_session_lock(task.conversation_id, "next-consumer")


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


def test_consumer_allows_reviewed_direct_native_read(store, task, context):
    command = "dws oa approval detail --instance-id process-1 --format json"
    stream = "\n".join(
        (
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "command_execution",
                        "id": "read-1",
                        "command": command,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "id": "read-1",
                        "command": command,
                    },
                }
            ),
            _result_jsonl(),
        )
    )

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(stream),
        native_cli_classifier=NativeCliMetadataClassifier(
            reviewed_effects={
                ("dws", "oa approval detail"): EffectKind.READ_ONLY,
            }
        ),
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.outcome.value == "no_action"
    run = store.get_agent_run(result.run_id)
    assert run is not None
    assert [event["item"]["metadata"]["operation"] for event in run.tool_events] == [
        "oa approval detail",
        "oa approval detail",
    ]


def test_consumer_rejects_direct_native_write(store, task, context):
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
        match="agent_write_forbidden",
    ):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            native_cli_classifier=NativeCliMetadataClassifier(
                reviewed_effects={
                    ("dws", "chat message send"): EffectKind.EFFECTFUL,
                }
            ),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)


def test_consumer_rejects_unreviewed_direct_shell_command(store, task, context):
    shell = json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "id": "shell-1",
                "command": "date",
            },
        }
    )

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="agent_shell_execution_forbidden",
    ):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(shell + "\n" + _result_jsonl()),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)
