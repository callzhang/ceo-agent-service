import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import ConsumerProposal, ProposedAction
from app.agent_runner import McpToolEffectRegistry
from app.agent_turn_runner import _read_matches_action
from app.audit_agent import AuditAgentRunner
from app.native_cli_metadata import AgentReadOnlyViolationError, describe_native_command
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore


class CapturingExecutor:
    def __init__(self, stdout: str, *, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        self.prompts.append(kwargs["prompt"])
        self.commands.append(command)
        for line in self.stdout.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(self.returncode, self.stdout, "")


def _audit_result_jsonl(
    outcome: str,
    *,
    operation_id: str,
    session: str,
    proposal_revision: int = 0,
    include_read: bool = True,
    include_write: bool = False,
    read_target: str = "cid-agent",
) -> str:
    records = [json.dumps({"type": "thread.started", "thread_id": session})]
    if include_read:
        arguments = {
            "argv": [
                "dws", "chat", "message", "list", "--group", read_target,
                "--time", "2026-08-06",
            ]
        }
        descriptor = describe_native_command(
            {"type": "command_execution", "argv": arguments["argv"]}
        )
        assert descriptor is not None
        receipt = {
            "cli": "dws",
            "operation": descriptor.command_path,
            "operation_digest": descriptor.command_digest,
            "target_identifiers": descriptor.target_identifiers,
            "result_digest": "recovery-read-digest",
            "stdout": "{}",
        }
        item = {
            "type": "mcp_tool_call",
            "id": "recovery-read",
            "server": "agent_cli",
            "tool": "execute_reviewed_read",
            "arguments": arguments,
            "status": "in_progress",
        }
        records.extend(
            (
                json.dumps({"type": "item.started", "item": item}),
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
                ),
            )
        )
    if include_write:
        write_lines = _audit_jsonl(
            operation_id,
            session=session,
            include_verification=True,
            proposal_revision=proposal_revision,
        ).splitlines()
        records.extend(write_lines[1:-1])
    if outcome == "executed":
        result = {
            "outcome": "executed",
            "summary": "Live state confirms the exact operation.",
            "proposal_revision": proposal_revision,
            "side_effect_state": "confirmed",
            "feedback": None,
            "external_result": {
                "operation_id": operation_id,
                "verification_summary": "Confirmed from live state.",
                "live_result_reference": {"id": "confirmed-message"},
            },
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        }
    else:
        result = {
            "outcome": "needs_human",
            "summary": "Live state remains ambiguous.",
            "proposal_revision": proposal_revision,
            "side_effect_state": "none",
            "feedback": None,
            "external_result": None,
            "error": {
                "code": "audit_recovery_ambiguous",
                "retryable": False,
                "authorization_required": False,
            },
        }
    records.append(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(result)},
            }
        )
    )
    return "\n".join(records)


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


def test_audit_does_not_renew_conversation_session_lock(setup, monkeypatch):
    store, task, audit_context, parent = setup
    renewals = 0

    def renew(*_args, **_kwargs):
        nonlocal renewals
        renewals += 1
        return True

    monkeypatch.setattr(store, "renew_codex_session_lock", renew)
    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(_audit_jsonl("operation-1", session="session-b")),
    ).run(
        task,
        audit_context,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    assert renewals == 0


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


def test_audit_accepts_matching_persisted_execution_receipt_with_live_read(setup):
    store, task, audit_context, parent = setup

    class ReceiptExecutor(CapturingExecutor):
        def __call__(self, command, *, on_stdout_line, **kwargs):
            run = store.get_agent_run_for_turn(
                task.id,
                task.execution_generation,
                role=AgentRole.AUDIT,
                proposal_revision=0,
                turn_attempt=0,
            )
            assert run is not None
            descriptor = describe_native_command(
                {
                    "type": "command_execution",
                    **audit_context.proposal.actions[0].payload,
                }
            )
            assert descriptor is not None
            store.record_agent_execution_receipt(
                run.id,
                receipt_id="receipt-operation-1",
                operation_id=run.operation_id,
                cli="dws",
                command_path="chat message send",
                command_digest=descriptor.command_digest,
                exit_code=0,
                owner="audit-owner",
            )
            return super().__call__(
                command,
                on_stdout_line=on_stdout_line,
                **kwargs,
            )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=ReceiptExecutor(
            _audit_result_jsonl(
                "executed",
                operation_id="operation-1",
                session="session-b",
            )
        ),
        owner="audit-owner",
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    persisted = store.get_agent_run(result.run_id)
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "confirmed"


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


def _seed_crashed_audit_write(setup):
    store, task, audit_context, parent = setup
    initial_lines = _audit_jsonl(
        "operation-1",
        session="audit-session-recovery",
        include_verification=False,
    ).splitlines()
    executor = CapturingExecutor("\n".join(initial_lines[:2]), returncode=1)
    with pytest.raises(RuntimeError, match="codex_process_failed"):
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
    assert run.codex_session_id == "audit-session-recovery"
    assert len(run.tool_events) == 1
    persisted_item = run.tool_events[0]["item"]
    assert "arguments" not in persisted_item and "result" not in persisted_item
    assert persisted_item["metadata"]["operation_id"] == "operation-1"
    assert persisted_item["metadata"]["target_identifiers"] == {
        "group": "cid-agent"
    }
    return store, task, audit_context, run


def test_crash_after_write_resumes_same_audit_session_and_confirms_without_replay(
    setup,
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "executed",
            operation_id=run.operation_id,
            session=run.codex_session_id,
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.run_id == run.id
    assert result.result.outcome.value == "executed"
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "confirmed"
    assert "resume" in executor.commands[0]
    assert run.codex_session_id in executor.commands[0]
    assert sum(
        event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
        for event in persisted.tool_events
    ) == 1
    receipts = store.list_agent_execution_receipts(run.id)
    assert len(receipts) == 1
    assert receipts[0].operation_id.startswith(f"{run.operation_id}:")
    read_events = [
        event
        for event in persisted.tool_events
        if event["type"] == "item.completed"
        and event["item"]["metadata"]["effect"] == "read_only"
    ]
    assert read_events[0]["item"]["metadata"]["result_digest"] == (
        "recovery-read-digest"
    )
    assert receipts[0].receipt_id == (
        f"reconciliation:{run.operation_id}:recovery-read-digest"
    )


def test_ambiguous_recovery_becomes_needs_human_without_write(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "needs_human",
            operation_id=run.operation_id,
            session=run.codex_session_id,
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "needs_human"
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "unknown"
    assert sum(
        event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
        for event in persisted.tool_events
    ) == 1


def test_ambiguous_recovery_requires_matching_live_read(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "needs_human",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            include_read=False,
        )
    )

    with pytest.raises(RuntimeError, match="audit_recovery_evidence_missing"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).recover(task, audit_context, run=run)


def test_unrelated_read_cannot_authorize_recovery_write(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "executed",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            include_write=True,
            read_target="cid-unrelated",
        )
    )

    with pytest.raises(RuntimeError, match="audit_recovery_read_required"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).recover(task, audit_context, run=run)


def test_direct_mcp_readback_relation_confirms_unknown_write_without_replay(setup):
    store, task, audit_context, parent = setup
    registry = McpToolEffectRegistry.default()
    action = ProposedAction.model_validate(
        {
            "description": "Upload interview result",
            "capability": "xiaoqing_interview",
            "operation": "upload_interview_result",
            "target": {
                "candidate_id": "candidate-1",
                "interview_id": "interview-1",
            },
            "payload": {
                "candidate_id": "candidate-1",
                "interview_id": "interview-1",
                "evaluation": "approved",
            },
            "expected_verification": "Read the same interview context",
        }
    )
    context = replace(
        audit_context,
        proposal=audit_context.proposal.model_copy(update={"actions": (action,)}),
    )
    started = {
        "type": "item.started",
        "item": {
            "type": "mcp_tool_call",
            "id": "direct-write",
            "server": "xiaoqing_interview",
            "tool": "upload_interview_result",
            "arguments": action.payload,
            "status": "in_progress",
        },
    }
    initial = CapturingExecutor(
        "\n".join(
            (
                json.dumps(
                    {"type": "thread.started", "thread_id": "direct-session"}
                ),
                json.dumps(started),
            )
        ),
        returncode=1,
    )
    with pytest.raises(RuntimeError, match="codex_process_failed"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=initial,
            mcp_effect_registry=registry,
        ).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "unknown"

    read = {
        "type": "mcp_tool_call",
        "id": "direct-read",
        "server": "xiaoqing_interview",
        "tool": "get_interview_context",
        "arguments": {
            "candidate_id": "candidate-1",
            "interview_id": "interview-1",
        },
        "status": "in_progress",
    }
    base = _audit_result_jsonl(
        "executed",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        include_read=False,
    ).splitlines()
    recovery = CapturingExecutor(
        "\n".join(
            (
                base[0],
                json.dumps({"type": "item.started", "item": read}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            **read,
                            "status": "completed",
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps(
                                            {
                                                "candidate_id": "candidate-1",
                                                "interview_id": "interview-1",
                                            }
                                        ),
                                    }
                                ],
                                "structuredContent": {
                                    "candidate_id": "candidate-1",
                                    "interview_id": "interview-1",
                                },
                                "isError": False,
                            },
                        },
                    }
                ),
                base[-1],
            )
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=recovery,
        mcp_effect_registry=registry,
    ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "executed"
    assert persisted is not None and persisted.side_effect_state == "confirmed"
    assert sum(
        event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
        for event in persisted.tool_events
    ) == 1


def test_direct_mcp_readback_requires_exact_target_identifiers():
    registry = McpToolEffectRegistry.default()

    assert not _read_matches_action(
        {
            "reviewed_server": "xiaoqing_interview",
            "reviewed_tool": "get_interview_context",
            "target_identifiers": {
                "candidate_id": "candidate-1",
                "interview_id": "interview-2",
            },
        },
        {
            "reviewed_server": "xiaoqing_interview",
            "reviewed_tool": "upload_interview_result",
            "target_identifiers": {
                "candidate_id": "candidate-1",
                "interview_id": "interview-1",
            },
        },
        registry,
    )


def _seed_crashed_memory_write(setup):
    store, task, audit_context, parent = setup
    registry = McpToolEffectRegistry.default()
    action = ProposedAction.model_validate(
        {
            "description": "Write durable memory",
            "capability": "memory_connector",
            "operation": "memory_write",
            "target": {"scope": "current-user"},
            "payload": {
                "data": "durable fact",
                "type": "text",
                "created_at": "2026-08-07T00:00:00Z",
            },
            "expected_verification": "Read the returned memory identity",
        }
    )
    context = replace(
        audit_context,
        proposal=audit_context.proposal.model_copy(update={"actions": (action,)}),
    )
    write = {
        "type": "mcp_tool_call",
        "id": "memory-write",
        "server": "memory_connector",
        "tool": "memory_write",
        "arguments": action.payload,
        "status": "in_progress",
    }
    initial = CapturingExecutor(
        "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "memory-session"}),
                json.dumps({"type": "item.started", "item": write}),
            )
        ),
        returncode=1,
    )
    with pytest.raises(RuntimeError, match="codex_process_failed"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=initial,
            mcp_effect_registry=registry,
        ).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)
    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "unknown"
    return store, task, context, run, registry, write


def test_no_readback_unknown_becomes_needs_human_without_write(setup):
    store, task, context, run, registry, _ = _seed_crashed_memory_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "needs_human",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            include_read=False,
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        mcp_effect_registry=registry,
    ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "needs_human"
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "unknown"
    assert sum(
        event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
        for event in persisted.tool_events
    ) == 1
    assert "Automatic readback is unavailable" in executor.prompts[0]


def test_memory_unknown_cannot_authorize_automatic_replay(setup):
    store, task, context, run, registry, write = _seed_crashed_memory_write(setup)
    read = {
        "type": "mcp_tool_call",
        "id": "memory-read",
        "server": "memory_connector",
        "tool": "memory_get",
        "arguments": {"uuid": "memory-1"},
        "status": "completed",
        "result": {
            "content": [{"type": "text", "text": "memory-1"}],
            "structuredContent": {"uuid": "memory-1"},
            "isError": False,
        },
    }
    recovery_lines = [
        json.dumps({"type": "thread.started", "thread_id": "memory-session"}),
        json.dumps({"type": "item.completed", "item": read}),
        json.dumps({"type": "item.started", "item": {**write, "id": "memory-replay"}}),
    ]

    with pytest.raises(RuntimeError, match="audit_recovery_read_required"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor("\n".join(recovery_lines)),
            mcp_effect_registry=registry,
        ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    assert sum(
        event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
        for event in persisted.tool_events
    ) == 1


def test_exact_receipt_confirms_no_readback_unknown(setup):
    store, task, context, run, registry, _ = _seed_crashed_memory_write(setup)

    class ReceiptExecutor(CapturingExecutor):
        def __call__(self, command, *, on_stdout_line, **kwargs):
            persisted = store.get_agent_run(run.id)
            assert persisted is not None
            metadata = persisted.tool_events[0]["item"]["metadata"]
            store.record_agent_execution_receipt(
                run.id,
                receipt_id="memory-write-receipt",
                operation_id=(
                    f"{run.operation_id}:{metadata['arguments_digest']}"
                ),
                cli="memory_connector",
                command_path="memory_write",
                command_digest=metadata["operation_digest"],
                exit_code=0,
                owner="audit-owner",
                expected_status="unknown",
            )
            return super().__call__(command, on_stdout_line=on_stdout_line, **kwargs)

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=ReceiptExecutor(
            _audit_result_jsonl(
                "executed",
                operation_id=run.operation_id,
                session=run.codex_session_id,
                include_read=False,
            )
        ),
        owner="audit-owner",
        mcp_effect_registry=registry,
    ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "executed"
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "confirmed"
    assert sum(
        event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
        for event in persisted.tool_events
    ) == 1


def test_definitely_absent_recovery_reads_before_executing_same_revision_once(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "executed",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            include_write=True,
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.external_result.operation_id == run.operation_id
    assert persisted is not None and persisted.status == "completed"
    recovery_events = persisted.tool_events[1:]
    assert recovery_events[0]["item"]["metadata"]["effect"] == "read_only"
    assert sum(
        event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
        for event in recovery_events
    ) == 1


def test_unknown_recovery_rejects_blind_write_before_live_read(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "executed",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            include_read=False,
            include_write=True,
        )
    )

    with pytest.raises(RuntimeError, match="audit_recovery_read_required"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"


@pytest.mark.parametrize("mismatch", ("digest", "target"))
def test_receipt_with_wrong_digest_or_target_does_not_confirm_action(setup, mismatch):
    store, task, audit_context, parent = setup

    class WrongReceiptExecutor(CapturingExecutor):
        def __call__(self, command, *, on_stdout_line, **kwargs):
            run = store.get_agent_run_for_turn(
                task.id,
                task.execution_generation,
                role=AgentRole.AUDIT,
                proposal_revision=0,
                turn_attempt=0,
            )
            assert run is not None
            if mismatch == "target":
                descriptor = describe_native_command(
                    {
                        "type": "command_execution",
                        "argv": [
                            "dws", "chat", "message", "send", "--group",
                            "cid-unrelated", "--text", "done", "--yes",
                        ],
                    }
                )
                assert descriptor is not None
                command_digest = descriptor.command_digest
            else:
                command_digest = "wrong-command-digest"
            store.record_agent_execution_receipt(
                run.id,
                receipt_id="wrong-receipt",
                operation_id=run.operation_id,
                cli="dws",
                command_path="chat message send",
                command_digest=command_digest,
                exit_code=0,
                owner="audit-owner",
            )
            return super().__call__(command, on_stdout_line=on_stdout_line, **kwargs)

    with pytest.raises(RuntimeError, match="audit_execution_evidence_missing"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=WrongReceiptExecutor(
                _audit_result_jsonl(
                    "executed",
                    operation_id="operation-1",
                    session="session-b",
                )
            ),
            owner="audit-owner",
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_two_action_recovery_confirms_unknown_first_and_executes_second_once(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    second = ProposedAction.model_validate(
        {
            "description": "Send second",
            "capability": "agent_cli.dws",
            "operation": "chat message send",
            "target": {"group": "cid-second"},
            "payload": {
                "argv": [
                    "dws", "chat", "message", "send", "--group", "cid-second",
                    "--text", "done", "--yes",
                ]
            },
            "expected_verification": "Second message exists",
        }
    )
    recovery_context = replace(
        audit_context,
        proposal=audit_context.proposal.model_copy(
            update={"actions": (*audit_context.proposal.actions, second)}
        ),
    )
    first_read = _audit_result_jsonl(
        "executed",
        operation_id=run.operation_id,
        session=run.codex_session_id,
    ).splitlines()
    second_write = _audit_jsonl(
        run.operation_id,
        session=run.codex_session_id,
        write_target="cid-second",
    ).splitlines()
    second_read = _audit_result_jsonl(
        "executed",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        read_target="cid-second",
    ).splitlines()
    executor = CapturingExecutor(
        "\n".join(first_read[:-1] + second_read[1:-1] + second_write[1:])
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, recovery_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "executed"
    assert persisted is not None and persisted.side_effect_state == "confirmed"
    started_targets = [
        event["item"]["metadata"].get("target_identifiers")
        for event in persisted.tool_events
        if event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
    ]
    assert started_targets.count({"group": "cid-agent"}) == 1
    assert started_targets.count({"group": "cid-second"}) == 1


def test_audit_two_starts_with_one_completion_remains_unknown(setup):
    store, task, audit_context, parent = setup
    lines = _audit_jsonl("operation-1", session="session-b").splitlines()
    started_index = next(
        index
        for index, line in enumerate(lines)
        if json.loads(line).get("type") == "item.started"
    )
    lines.insert(started_index + 1, lines[started_index])

    with pytest.raises(RuntimeError, match="audit_execution_evidence_mismatch"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor("\n".join(lines)),
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
