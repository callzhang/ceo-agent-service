import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import ConsumerProposal, ProposedAction
from app.audit_agent import AuditAgentRunner
from app.native_cli_metadata import AgentReadOnlyViolationError
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore


class CapturingExecutor:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        self.prompts.append(kwargs["prompt"])
        self.commands.append(command)
        for line in self.stdout.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(0, self.stdout, "")


def _audit_jsonl(
    operation_id: str,
    *,
    session: str,
    include_write: bool = True,
    write_error: bool = False,
    write_target: str = "cid-agent",
    write_count: int = 1,
    write_text: str = "done",
    include_verification: bool = True,
    proposal_revision: int = 0,
) -> str:
    result = {
        "outcome": "executed", "summary": "Executed and verified.",
        "proposal_revision": proposal_revision, "side_effect_state": "confirmed", "feedback": None,
        "external_result": {"operation_id": operation_id, "verification_summary": "Present.", "live_result_reference": {"id": "one"}},
        "error": {"code": "", "retryable": False, "authorization_required": False},
    }
    records = [json.dumps({"type": "thread.started", "thread_id": session})]
    if include_write:
        for index in range(write_count):
            arguments = {
                "argv": [
                    "dws",
                    "chat",
                    "message",
                    "send",
                    "--group",
                    write_target,
                    "--text",
                    write_text,
                    "--yes",
                ]
            }
            receipt = {
                "cli": "dws",
                "operation": "chat message send",
                "operation_digest": "placeholder",
                "target_identifiers": {"group": write_target},
                "result_digest": "result-digest",
                "stdout": "{}",
            }
            if write_error:
                receipt["error"] = {
                    "channel": "dws",
                    "code": "dws_transient_failure",
                    "retryable": True,
                    "gate_state": "unavailable",
                }
            from app.native_cli_metadata import describe_native_command

            descriptor = describe_native_command(
                {"type": "command_execution", "argv": arguments["argv"]}
            )
            assert descriptor is not None
            receipt["operation_digest"] = descriptor.command_digest
            receipt["target_identifiers"] = descriptor.target_identifiers
            item = {
                "type": "mcp_tool_call",
                "id": f"write-{index + 1}",
                "server": "agent_cli",
                "tool": "execute_reviewed_write",
                "arguments": arguments,
                "status": "in_progress",
            }
            records.append(json.dumps({"type": "item.started", "item": item}))
            records.append(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            **item,
                            "status": "completed",
                            "result": {
                                "content": [
                                    {"type": "text", "text": json.dumps(receipt)}
                                ],
                                "isError": False,
                            },
                        },
                    }
                )
            )
    if include_write and include_verification:
        arguments = {
            "argv": [
                "dws", "chat", "message", "list", "--group", write_target,
                "--time", "2026-08-06",
            ]
        }
        from app.native_cli_metadata import describe_native_command

        descriptor = describe_native_command(
            {"type": "command_execution", "argv": arguments["argv"]}
        )
        assert descriptor is not None
        receipt = {
            "cli": "dws",
            "operation": descriptor.command_path,
            "operation_digest": descriptor.command_digest,
            "target_identifiers": descriptor.target_identifiers,
            "result_digest": "verification-digest",
            "stdout": "{}",
        }
        item = {
            "type": "mcp_tool_call",
            "id": "verify-1",
            "server": "agent_cli",
            "tool": "execute_reviewed_read",
            "arguments": arguments,
            "status": "in_progress",
        }
        records.append(json.dumps({"type": "item.started", "item": item}))
        records.append(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        **item,
                        "status": "completed",
                        "result": {
                            "content": [
                                {"type": "text", "text": json.dumps(receipt)}
                            ],
                            "isError": False,
                        },
                    },
                }
            )
        )
    records.append(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(result)},
            }
        )
    )
    return "\n".join(records)


def _dry_run_suppressed_jsonl(*, proposal_revision: int = 0) -> str:
    result = {
        "outcome": "needs_human",
        "summary": "The candidate is executable but dry-run suppresses execution.",
        "proposal_revision": proposal_revision,
        "side_effect_state": "none",
        "feedback": None,
        "external_result": None,
        "error": {
            "code": "dry_run_execution_suppressed",
            "retryable": False,
            "authorization_required": False,
        },
    }
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(result)},
        }
    )


@pytest.fixture
def setup(tmp_path):
    store = AutoReplyStore(tmp_path / "agent.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-agent", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-08-06 10:00:00",
        trigger_sender="Derek", trigger_text="Send this", execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    context = AgentTaskContext(
        task_id=task.id, channel=task.channel, conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(), prior_receipts=(),
    )
    proposal = ConsumerProposal.model_validate({
        "objective": "Send result", "actions": [{"description": "Send", "capability": "agent_cli.dws", "operation": "chat message send", "target": {"group": "cid-agent"}, "payload": {"argv": ["dws", "chat", "message", "send", "--group", "cid-agent", "--text", "done", "--yes"]}, "expected_verification": "Message exists"}],
        "sourced_facts": [], "authored_judgment": "Requested by Derek",
    })
    parent = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="parent",
    ).run
    audit_context = AuditTurnContext(task=context, proposal_revision=0, operation_id="operation-1", proposal=proposal, audit_rules="Check authority.")
    return store, task, audit_context, parent


def test_audit_starts_fresh_and_does_not_replace_conversation_session(setup):
    store, task, audit_context, parent = setup
    store.upsert_conversation(task.conversation_id, "Group", False, "session-a")
    executor = CapturingExecutor(_audit_jsonl("operation-1", session="session-b"))

    result = AuditAgentRunner(store=store, workspace=Path("/workspace"), executor=executor).run(
        task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id,
    )

    command = executor.commands[0]
    assert command[:2] == ["codex", "exec"]
    assert "resume" not in command
    assert "--output-schema" not in command
    assert "features.plugins=false" in command
    assert "features.apps=false" in command
    assert 'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "execute_reviewed_write", "read_skill"]' in command
    assert 'web_search="disabled"' not in command
    assert store.get_codex_session_id(task.conversation_id) == "session-a"
    run = store.get_agent_run(result.run_id)
    assert run.role.value == "audit"
    assert run.codex_session_id == "session-b"
    assert run.operation_id == "operation-1"
    assert run.side_effect_state == "confirmed"
    assert [event["type"] for event in run.tool_events] == [
        "item.started",
        "item.completed",
        "item.started",
        "item.completed",
    ]
    assert run.tool_events[1]["item"]["status"] == "completed"
    assert run.tool_events[1]["item"]["metadata"]["operation"] == (
        "chat message send"
    )
    assert run.tool_events[1]["item"]["metadata"]["target_identifiers"] == {
        "group": "cid-agent"
    }


def test_dry_run_audit_command_exposes_only_reviewed_read_tools(setup):
    store, task, audit_context, parent = setup
    executor = CapturingExecutor(_dry_run_suppressed_jsonl())

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        dry_run=True,
    ).run(
        task,
        audit_context,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    command = executor.commands[0]
    assert result.result.outcome.value == "needs_human"
    assert result.result.error.code == "dry_run_execution_suppressed"
    assert result.result.side_effect_state.value == "none"
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill"]'
        in command
    )
    assert "execute_reviewed_write" not in command
    assert any("dry_run_execution_suppressed" in item for item in command)


def test_audit_reads_current_audit_rules_for_each_turn(
    setup,
    tmp_path,
    monkeypatch,
):
    store, task, audit_context, parent = setup
    rules_path = tmp_path / "audit_rules.md"
    rules_path.write_text("Current audit rule.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(rules_path))
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-b")
    )

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert "Current audit rule." not in executor.prompts[0]
    assert "Check authority." not in executor.prompts[0]
    assert "Effective Audit Rules" not in executor.prompts[0]
    assert any("Current audit rule." in item for item in executor.commands[0])
    assert any(
        "do not rewrite the candidate" in item for item in executor.commands[0]
    )


def test_audit_validates_rules_before_claiming_run(
    setup,
    monkeypatch,
):
    store, task, audit_context, parent = setup

    def fail_rules(_role):
        raise OSError("rules unavailable")

    monkeypatch.setattr("app.audit_agent.render_audit_rules", fail_rules)

    with pytest.raises(OSError, match="rules unavailable"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl("operation-1", session="session-b")
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    ) is None


def test_audit_rejects_executed_result_without_completed_write(setup):
    store, task, audit_context, parent = setup
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-b", include_write=False)
    )

    with pytest.raises(RuntimeError, match="audit_execution_evidence_missing"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert run.status == "failed"
    assert run.side_effect_state == "none"


def test_audit_rejects_executed_result_without_live_verification(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(RuntimeError, match="audit_execution_evidence_mismatch"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl(
                    "operation-1",
                    session="session-b",
                    include_verification=False,
                )
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_rejects_direct_shell_event(setup):
    store, task, audit_context, parent = setup
    shell = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "command_execution", "id": "shell-1"},
        }
    )
    executor = CapturingExecutor(
        shell + "\n" + _audit_jsonl("operation-1", session="session-b")
    )

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="agent_shell_execution_forbidden",
    ):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_does_not_confirm_agent_cli_error_receipt(setup):
    store, task, audit_context, parent = setup
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-b", write_error=True)
    )

    with pytest.raises(AgentReadOnlyViolationError, match="agent_cli_receipt_invalid"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert run.status == "unknown"
    assert run.side_effect_state == "unknown"


@pytest.mark.parametrize(
    ("write_target", "write_count"),
    (("cid-unrelated", 1), ("cid-agent", 2)),
)
def test_audit_rejects_unrelated_or_duplicate_writes(
    setup,
    write_target,
    write_count,
):
    store, task, audit_context, parent = setup
    executor = CapturingExecutor(
        _audit_jsonl(
            "operation-1",
            session="session-b",
            write_target=write_target,
            write_count=write_count,
        )
    )

    with pytest.raises(RuntimeError, match="audit_execution_evidence_mismatch"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_rejects_write_with_different_payload_for_same_target(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(RuntimeError, match="audit_execution_evidence_mismatch"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl(
                    "operation-1",
                    session="session-b",
                    write_text="different content",
                )
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_rejects_different_operation_for_same_target_and_payload(setup):
    store, task, audit_context, parent = setup
    action = audit_context.proposal.actions[0].model_copy(
        update={"operation": "oa approval comment"}
    )
    proposal = audit_context.proposal.model_copy(update={"actions": (action,)})

    with pytest.raises(RuntimeError, match="audit_execution_evidence_mismatch"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl("operation-1", session="session-b")
            ),
        ).run(
            task,
            replace(audit_context, proposal=proposal),
            turn_attempt=0,
            parent_agent_run_id=parent.id,
        )


def test_audit_rejects_partial_writes(setup):
    store, task, audit_context, parent = setup
    second_action = ProposedAction.model_validate(
        {
            "description": "Send second result",
            "capability": "agent_cli.dws",
            "operation": "chat message send",
            "target": {"group": "cid-second"},
            "payload": {
                "argv": [
                    "dws", "chat", "message", "send", "--group", "cid-second",
                    "--text", "second", "--yes",
                ]
            },
            "expected_verification": "Second message exists",
        }
    )
    proposal = audit_context.proposal.model_copy(
        update={"actions": (*audit_context.proposal.actions, second_action)}
    )
    partial_context = replace(audit_context, proposal=proposal)

    with pytest.raises(RuntimeError, match="audit_execution_evidence_mismatch"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl("operation-1", session="session-b")
            ),
        ).run(task, partial_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_rejects_proposal_revision_mismatch(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(RuntimeError, match="audit_proposal_revision_mismatch"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl(
                    "operation-1",
                    session="session-b",
                    proposal_revision=1,
                )
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)
