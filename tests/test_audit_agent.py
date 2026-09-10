import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent_context import (
    AgentTaskContext,
    AuditTurnContext,
    MaterialReference,
)
from app.agent_result import ResultParseError
from app.agent_contracts import (
    AuditOutcome,
    ConsumerProposal,
    ProposedAction,
)
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import AgentRuntimeRouter
from app import audit_agent
from app.audit_agent import AuditAgentRunner
from app.external_action_identity import expected_external_action
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.native_cli_metadata import describe_native_command
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore
from app.wechat.codex_safety import (
    ControlledCliConfig,
    disable_automatic_review,
    make_audit_agent_command,
    make_consumer_agent_command,
)


class CapturingExecutor:
    def __init__(
        self,
        stdout: str,
        *,
        returncode: int = 0,
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []
        self.environments: list[dict[str, str]] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        self.prompts.append(kwargs["prompt"])
        self.commands.append(command)
        self.environments.append(dict(kwargs["env"]))
        for line in self.stdout.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(self.returncode, self.stdout, "")


class SequencedExecutor(CapturingExecutor):
    def __init__(self, *outputs: str) -> None:
        super().__init__("")
        self.outputs = list(outputs)

    def __call__(self, command, *, on_stdout_line, **kwargs):
        self.stdout = self.outputs.pop(0)
        return super().__call__(command, on_stdout_line=on_stdout_line, **kwargs)


def _audit_runtime_dependencies(
    store,
    *,
    routes="codex_oauth,codex_api",
    workspace=Path("/workspace"),
):
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": routes,
            "CEO_CODEX_API_KEY": "fallback-test-key",
        }
    )
    capabilities = frozenset(
        {
            "structured_output",
            "local_schema_validation",
            "audit_effect_visibility",
            "reviewed_read_tools",
            "reviewed_write_tools",
            "agent_cli.dws",
            "task_context",
            "channel:dingtalk",
            "mcp:agent_cli:reviewed_read",
            "mcp:agent_cli:reviewed_write",
            "native_cli:reviewed",
            "native_cli:dws",
            "mcp:memory_connector:read",
            "reviewed_skill:business-review:ef1bf870671c6af5ad40d59f73d237cff5ae286f835936a7893a98988389ab8a",
        }
    )
    snapshots = {
        route.name: RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=capabilities,
            healthy=True,
            checked_at="2026-08-20 00:00:00",
            expires_at="2099-08-20 00:00:00",
        )
        for route in config.routes
    }
    return (
        config,
        AgentRuntimeRouter(routes=config.routes, store=store, snapshots=snapshots),
        CodexRuntimeAdapter(workspace, config),
    )


def _wire_result(result: dict[str, object]) -> dict[str, object]:
    error = result["error"]
    assert isinstance(error, dict)
    return {
        "outcome": result["outcome"],
        "summary": result["summary"],
        "proposal_revision": result["proposal_revision"],
        "feedback": result["feedback"],
        "external_result": result["external_result"],
        "decision_options": result.get("decision_options", []),
        "error_code": error["code"],
        "error_retryable": error["retryable"],
        "error_authorization_required": error["authorization_required"],
        "risk": "high" if result["outcome"] == "needs_human" else "low",
        "confidence": 0.1 if result["outcome"] == "needs_human" else 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    }


def test_expected_external_action_preserves_typed_identity_without_a_command():
    action = ProposedAction.model_validate(
        {
            "description": "Ask HR to schedule the interview.",
            "action_identity": "request-interview",
            "capability": "dingtalk-chat",
            "operation": "send_direct_message",
            "payload": {"content": "请安排一轮面试。"},
            "target": {"open_dingtalk_id": "open-recipient"},
        }
    )
    expected = expected_external_action(
        action,
        action_index=0,
        business_object_key="oa:process-1:task-1",
    )

    assert expected["action_index"] == 0
    assert expected["action_identity"] == "request-interview"
    assert expected["operation"] == "send_direct_message"
    assert expected["target_identifiers"] == {
        "open_dingtalk_id": "open-recipient"
    }
    assert expected["external_action_key"]
    assert "argv" not in expected
    assert "authorization_id" not in expected


def test_external_action_identity_is_stable_without_run_or_revision_state():
    action = ProposedAction.model_validate(
        {
            "description": "Notify applicant.",
            "action_identity": "notify-approval-result",
            "capability": "dingtalk-chat",
            "operation": "send_direct_message",
            "payload": {"content": "审批已完成。"},
            "target": {"open_dingtalk_id": "open-recipient"},
        }
    )
    first = expected_external_action(
        action,
        action_index=0,
        business_object_key="oa:process-1:task-1",
    )
    second = expected_external_action(
        action,
        action_index=0,
        business_object_key="oa:process-1:task-1",
    )

    assert first["external_action_key"] == second["external_action_key"]
    assert "authorization_id" not in first
    assert "argv" not in first


def test_audit_retry_after_invalid_result_carries_validation_locations(
    setup, monkeypatch
):
    store, task, audit_context, parent = setup
    failed = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=audit_context.operation_id,
        owner="audit-test",
    )
    assert failed.claimed
    store.fail_agent_run(
        failed.run.id,
        {
            "code": "codex_result_invalid",
            "retryable": True,
            "authorization_required": False,
            "detail": "executed.error_code: string_type",
            "session_continuable": True,
        },
        owner="audit-test",
    )
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=parent.id,
        operation_id=audit_context.operation_id,
        owner="audit-test",
    )
    assert claim.claimed
    captured: dict[str, object] = {}

    class FakeProcess:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, **_kwargs):
            pass

        def execute(self, **kwargs):
            captured.update(kwargs)
            return "executed"

    monkeypatch.setattr(audit_agent, "AgentTurnProcess", FakeProcess)
    runner = AuditAgentRunner(store=store, workspace=Path("/workspace"), owner="audit-test")

    runner._execute_claimed(task, audit_context, run=claim.run, rendered_rules="rules")

    prompt = str(captured["prompt"])
    assert "## Result Correction" in prompt
    assert "executed.error_code: string_type" in prompt


def test_audit_retry_after_missing_result_asks_for_json_object(setup, monkeypatch):
    store, task, audit_context, parent = setup
    failed = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=audit_context.operation_id,
        owner="audit-test",
    )
    assert failed.claimed
    store.fail_agent_run(
        failed.run.id,
        {
            "code": "codex_result_missing",
            "retryable": True,
            "authorization_required": False,
            "detail": "no valid typed result JSON found in Codex JSONL",
            "session_continuable": True,
        },
        owner="audit-test",
    )
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=parent.id,
        operation_id=audit_context.operation_id,
        owner="audit-test",
    )
    assert claim.claimed
    captured: dict[str, object] = {}

    class FakeProcess:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, **_kwargs):
            pass

        def execute(self, **kwargs):
            captured.update(kwargs)
            return "executed"

    monkeypatch.setattr(audit_agent, "AgentTurnProcess", FakeProcess)
    runner = AuditAgentRunner(store=store, workspace=Path("/workspace"), owner="audit-test")

    runner._execute_claimed(task, audit_context, run=claim.run, rendered_rules="rules")

    prompt = str(captured["prompt"])
    assert "## Result Correction" in prompt
    assert "没有返回任何 JSON 对象" in prompt


def test_audit_runner_adds_stable_action_identity_without_command_authorization(
    setup, monkeypatch
):
    store, task, audit_context, parent = setup
    proposal = ConsumerProposal.model_validate(
        {
            "objective": "Coordinate interview",
                "actions": [{
                    "description": "Ask HR to schedule the interview.",
                    "action_identity": "request-interview-scheduling",
                    "capability": "dingtalk-chat",
                "operation": "send_direct_message",
                "target": {"open_dingtalk_id": "open-recipient"},
                "payload": {"content": "请安排一轮面试。"},
            }],
            "sourced_facts": [],
            "authored_judgment": "Proceed with interview coordination.",
        }
    )
    direct_context = replace(audit_context, proposal=proposal)
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=direct_context.operation_id,
        owner="audit-test",
    )
    assert claim.claimed
    captured: dict[str, object] = {}

    class FakeProcess:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, **_kwargs):
            pass

        def execute(self, **kwargs):
            captured.update(kwargs)
            return "executed"

    monkeypatch.setattr(audit_agent, "AgentTurnProcess", FakeProcess)
    runner = AuditAgentRunner(store=store, workspace=Path("/workspace"), owner="audit-test")

    result = runner._execute_claimed(
        task,
        direct_context,
        run=claim.run,
        rendered_rules="rules",
    )

    assert result == "executed"
    prompt = str(captured["prompt"])
    assert "External action identities" in prompt
    assert "external_action_key" in prompt
    assert "command authorizations" in prompt
    assert "execute_reviewed_write" not in prompt
    assert "--open-dingtalk-id" not in prompt




def _audit_jsonl(
    operation_id: str,
    *,
    session: str,
    include_write: bool = True,
    write_error: bool = False,
    write_target: str = "cid-agent",
    write_count: int = 1,
    write_targets: tuple[str, ...] | None = None,
    write_text: str = "done",
    include_verification: bool = True,
    verification_targets: tuple[str, ...] | None = None,
    verification_argv: tuple[str, ...] | None = None,
    proposal_revision: int = 0,
    authorization_id: str = "",
) -> str:
    result = {
        "outcome": "executed",
        "summary": "Executed and verified.",
        "proposal_revision": proposal_revision,
        "feedback": None,
        "external_result": {
            "operation_id": operation_id,
            "live_result_reference": {"id": "one"},
        },
        "error": {"code": "", "retryable": False, "authorization_required": False},
    }
    records = [json.dumps({"type": "thread.started", "thread_id": session})]
    if include_write:
        effective_write_targets = write_targets or (write_target,) * write_count
        for index, effective_write_target in enumerate(effective_write_targets):
            arguments = {
                "argv": [
                    "dws",
                    "chat",
                    "message",
                    "send",
                    "--group",
                    effective_write_target,
                    "--text",
                    write_text,
                    "--yes",
                ]
            }
            if authorization_id:
                arguments["authorization_id"] = authorization_id
            receipt = {
                "cli": "dws",
                "operation": "chat message send",
                "operation_digest": "placeholder",
                "target_identifiers": {"group": effective_write_target},
                "result_digest": "result-digest",
                "stdout": "{}",
            }
            if authorization_id:
                receipt["authorization_id"] = authorization_id
                receipt["action_index"] = -1
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
                "id": (
                    f"recovery-write-{index + 1}"
                    if authorization_id
                    else f"write-{index + 1}"
                ),
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
        effective_verification_targets = verification_targets or (write_target,)
        for index, verification_target in enumerate(effective_verification_targets):
            arguments = {
                "argv": list(verification_argv)
                if verification_argv is not None
                else [
                    "dws",
                    "chat",
                    "message",
                    "list",
                    "--group",
                    verification_target,
                    "--time",
                    "2026-08-06",
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
                "result_digest": f"verification-digest-{index}",
                "stdout": json.dumps(
                    {
                        "complete": True,
                        "hasMore": False,
                        "paginationKnown": True,
                        "failures": [],
                        "queryRange": {
                            "startTime": (
                                datetime.now(UTC) - timedelta(minutes=30)
                            ).isoformat(),
                            "endTime": (
                                datetime.now(UTC) + timedelta(minutes=30)
                            ).isoformat(),
                        },
                        "messages": [
                            {
                                "conversationId": verification_target,
                                "messageId": f"verified-{index}",
                                "text": write_text,
                            }
                        ],
                    }
                ),
            }
            item = {
                "type": "mcp_tool_call",
                "id": f"verify-{index + 1}",
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
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(_wire_result(result)),
                },
            }
        )
    )
    return "\n".join(records)


def _dry_run_suppressed_jsonl(*, proposal_revision: int = 0) -> str:
    result = {
        "outcome": "dry_run",
        "summary": "The candidate is executable but dry-run suppresses execution.",
        "proposal_revision": proposal_revision,
        "feedback": None,
        "external_result": None,
        "decision_options": [],
        "error": {
            "code": "dry_run_execution_suppressed",
            "retryable": False,
            "authorization_required": False,
        },
    }
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(_wire_result(result))},
        }
    )


def _feedback_provided_jsonl(
    observation: str, *, proposal_revision: int = 0
) -> str:
    result = {
        "outcome": "feedback_provided",
        "summary": observation,
        "proposal_revision": proposal_revision,
        "feedback": {
            "rule": "Rule 15 (calendar_conflicts) - new meeting more important",
            "observation": observation,
            "requested_revision": "Decline the existing meeting and notify its inviter.",
        },
        "external_result": None,
        "error": {
            "code": "",
            "retryable": False,
            "authorization_required": False,
        },
    }
    return json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(_wire_result(result)),
            },
        }
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "agent.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-agent",
        conversation_title="Group",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-06 10:00:00",
        trigger_sender="Derek",
        trigger_text="Send this",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    context = AgentTaskContext(
        task_id=task.id,
        channel=task.channel,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        trigger_create_time=task.trigger_create_time,
        messages=(),
        materials=(),
        prior_receipts=(),
    )
    proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send result",
            "actions": [
                {
                    "description": "Send",
                    "action_identity": "send-result",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"group": "cid-agent"},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "message",
                            "send",
                            "--group",
                            "cid-agent",
                            "--text",
                            "done",
                            "--yes",
                        ]
                    },
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "Requested by Derek",
        }
    )
    parent = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="parent",
    ).run
    audit_context = AuditTurnContext(
        task=context,
        proposal_revision=0,
        operation_id="operation-1",
        proposal=proposal,
        audit_rules="Check authority.",
    )
    return store, task, audit_context, parent


def _with_unresolved_image(context: AuditTurnContext) -> AuditTurnContext:
    return replace(
        context,
        task=replace(
            context.task,
            materials=(
                *context.task.materials,
                MaterialReference(
                    kind="dingtalk_image",
                    reference='{"media_id":"@image-1"}',
                    source_message_id=context.task.trigger_message_id,
                    read_commands=(),
                ),
            ),
            image_paths=(),
            image_sha256s=(),
        ),
    )


def _assert_image_recovery_deferred(store, task, run_id: int) -> None:
    persisted = store.get_agent_run(run_id)
    current_task = store.get_reply_task(task.id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.lease_owner == ""
    assert persisted.lease_expires_at == ""
    assert json.loads(persisted.structured_error_json) == {
        "code": "critical_info_unavailable",
        "retryable": True,
    }
    assert current_task is not None and current_task.status == "processing"


def test_audit_uses_typed_result_without_application_receipt_validation(setup):
    store, task, audit_context, parent = setup
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-audit")
    )
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    persisted = store.get_agent_run(result.run_id)
    assert result.result.outcome is AuditOutcome.EXECUTED
    assert persisted is not None and persisted.status == "completed"
    assert "execute_audited_email_unsubscribe" not in json.dumps(executor.commands)
    assert "terminal skip" not in executor.prompts[0]


def test_audit_prompt_uses_quality_gate_priority(setup):
    store, task, audit_context, parent = setup
    executor = CapturingExecutor(_audit_jsonl("operation-1", session="quality"))
    AuditAgentRunner(store=store, workspace=Path("/workspace"), executor=executor).run(
        task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id
    )
    prompt = executor.prompts[0]
    assert "rule_coverage (0..1)" in prompt
    assert "information_completeness < 0.5" in prompt
    assert "2-4 mutually exclusive" in prompt
    assert "Technical/provider/read/route/schema/Audit/retry" in prompt
    assert "needs_human is valid only when risk is high" not in prompt
    assert "only risk and confidence" not in prompt


def test_audit_runtime_environment_overrides_ambient_send_mode(
    setup, monkeypatch
):
    store, task, audit_context, parent = setup
    monkeypatch.setenv("CEO_DRY_RUN", "0")
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "0")
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-audit-mode")
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        execution_environment={
            "CEO_DRY_RUN": "1",
            "CEO_NOT_SEND_MESSAGE": "1",
        },
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert result.result.outcome is AuditOutcome.EXECUTED
    assert executor.environments[0]["CEO_DRY_RUN"] == "1"
    assert executor.environments[0]["CEO_NOT_SEND_MESSAGE"] == "1"


def _audited_email_setup(setup):
    store, task, audit_context, parent = setup
    store.complete_agent_run(
        parent.id,
        {"outcome": "proposal"},
        owner="parent",
    )
    payload = {
        "schema": "email_agent_action.v1",
        "action_type": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
    }
    email_task = task.model_copy(
        update={"channel": "email", "trigger_message_json": json.dumps(payload)}
    )
    email_context = replace(
        audit_context,
        task=replace(audit_context.task, channel="email", trigger_raw_payload=payload),
    )
    return store, email_task, email_context, parent


class _EvidenceDriver:
    def __init__(self, evidence: bool) -> None:
        self.evidence = evidence
        self.calls: list[tuple[int, int]] = []

    def audit_run_has_execution_evidence(self, task, *, audit_run_id: int) -> bool:
        self.calls.append((task.id, audit_run_id))
        return self.evidence


def test_audited_email_executed_without_tool_evidence_is_an_invalid_result(setup):
    store, email_task, email_context, parent = _audited_email_setup(setup)
    driver = _EvidenceDriver(evidence=False)
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-email-audit")
    )

    with pytest.raises(ResultParseError, match="execute_audited_email_unsubscribe"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            domain_continuation=driver,
        ).run(email_task, email_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        email_task.id,
        email_task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "failed"
    error = json.loads(run.structured_error_json)
    assert error["code"] == "codex_result_invalid"
    assert "execute_audited_email_unsubscribe" in error["detail"]
    assert driver.calls == [(email_task.id, run.id)]
    assert "Return executed only after execute_audited_email_unsubscribe" in executor.prompts[0]
    assert (
        "A receipt whose outcome is a terminal skip ends the operation"
        in executor.prompts[0]
    )


def test_audited_email_executed_with_tool_evidence_completes(setup):
    store, email_task, email_context, parent = _audited_email_setup(setup)
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-email-audit")
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        domain_continuation=_EvidenceDriver(evidence=True),
    ).run(email_task, email_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert result.result.outcome is AuditOutcome.EXECUTED


def test_audited_email_turn_receives_task_bound_cli_and_prompt_identity(setup):
    store, task, audit_context, parent = setup
    store.complete_agent_run(
        parent.id,
        {"outcome": "proposal"},
        owner="parent",
    )
    payload = {
        "schema": "email_agent_action.v1",
        "action_type": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
    }
    email_task = task.model_copy(
        update={
            "channel": "email",
            "trigger_message_json": json.dumps(payload),
        }
    )
    email_context = replace(
        audit_context,
        task=replace(
            audit_context.task,
            channel="email",
            trigger_raw_payload=payload,
        ),
    )
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-email-audit")
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(
        email_task,
        email_context,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    rendered_command = json.dumps(executor.commands)
    rendered_prompt = "\n".join(executor.prompts)
    assert "mcp_servers.agent_cli" in rendered_command
    assert "allowed_tools" not in rendered_command
    assert f"task_id={task.id}" in rendered_prompt
    assert f"execution_generation={task.execution_generation}" in rendered_prompt
    assert f"audit_agent_run_id={result.run_id}" in rendered_prompt


def test_consumer_command_does_not_expose_audited_unsubscribe_write() -> None:
    command = ["codex", "exec", "--json"]

    make_consumer_agent_command(command)

    rendered = json.dumps(command)
    assert "execute_audited_email_unsubscribe" not in rendered
    assert "execute_reviewed_read" not in rendered
    assert "execute_reviewed_write" not in rendered
    assert 'approval_policy="on-failure"' in command
    assert 'approvals_reviewer="auto_review"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_disable_automatic_review_keeps_sandbox_and_never_asks_for_approval() -> None:
    command = ["codex", "exec", "--json", "-"]
    make_audit_agent_command(command)
    assert 'approvals_reviewer="auto_review"' in command

    disable_automatic_review(command)

    assert 'approval_policy="never"' in command
    assert not any(value.startswith("approvals_reviewer=") for value in command)
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert command[-1] == "-"


def test_service_api_route_runs_audit_without_automatic_review(setup):
    store, task, audit_context, parent = setup
    config, router, adapter = _audit_runtime_dependencies(store, routes="codex_api")
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-api-audit")
    )

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    command = executor.commands[0]
    assert 'approval_policy="never"' in command
    assert 'approvals_reviewer="auto_review"' not in command


def test_oauth_route_keeps_automatic_review_for_audit(setup):
    store, task, audit_context, parent = setup
    config, router, adapter = _audit_runtime_dependencies(store, routes="codex_oauth")
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-oauth-audit")
    )

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    command = executor.commands[0]
    assert 'approval_policy="on-failure"' in command
    assert 'approvals_reviewer="auto_review"' in command


def test_audit_command_configures_runtime_reviewed_agent_cli_without_tool_allowlist() -> None:
    command = ["codex", "exec", "--json"]

    make_audit_agent_command(
        command,
        controlled_cli=ControlledCliConfig(
            command="python",
            args=("-m", "app.agent_cli"),
            cwd="/workspace",
        ),
    )

    rendered = json.dumps(command)
    assert "mcp_servers.agent_cli" in rendered
    assert "allowed_tools" not in rendered
    assert "execute_reviewed_read" not in rendered
    assert "execute_reviewed_write" not in rendered


def test_audit_process_failure_is_a_regular_failed_run(setup):
    store, task, audit_context, parent = setup
    with pytest.raises(RuntimeError, match="codex_process_failed"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                json.dumps(
                    {
                        "type": "thread.started",
                        "thread_id": "session-failed",
                    }
                ),
                returncode=1,
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "failed"
    assert run.structured_error_json


def test_audit_result_binds_proposal_revision_from_the_run(setup):
    store, task, audit_context, parent = setup
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_jsonl(
                "operation-1",
                session="session-revision",
                proposal_revision=1,
            )
        ),
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "completed"
    assert result.result.proposal_revision == run.proposal_revision == 0
    assert json.loads(run.final_result_json)["proposal_revision"] == 0


def test_audit_feedback_with_retyped_revision_is_accepted(setup, caplog):
    """DingTalk task 383511: a schema-valid feedback_provided audit echoed the
    revision it requested (1) for the revision-0 candidate it reviewed."""
    store, task, audit_context, parent = setup
    observation = "候选人判断正确，但缺少拒绝HR例会的动作"
    with caplog.at_level(logging.WARNING, logger="app.agent_turn_runner"):
        result = AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _feedback_provided_jsonl(observation, proposal_revision=1)
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "completed"
    assert result.result.outcome is AuditOutcome.FEEDBACK_PROVIDED
    assert result.result.feedback is not None
    assert result.result.feedback.observation == observation
    assert result.result.proposal_revision == 0
    assert json.loads(run.final_result_json)["proposal_revision"] == 0
    bound = [
        record
        for record in caplog.records
        if "proposal_revision bound from run" in record.getMessage()
    ]
    assert len(bound) == 1
    assert bound[0].levelno == logging.WARNING
    assert f"run={run.id}" in bound[0].getMessage()
    assert "run_revision=0" in bound[0].getMessage()
    assert "echoed_revision=1" in bound[0].getMessage()


def test_audit_result_missing_proposal_revision_is_result_invalid(setup):
    store, task, audit_context, parent = setup
    wire = _wire_result(
        {
            "outcome": "feedback_provided",
            "summary": "Missing revision",
            "proposal_revision": 0,
            "feedback": {
                "rule": "Rule 15",
                "observation": "conflict",
                "requested_revision": "decline the existing meeting",
            },
            "external_result": None,
            "error": {"code": "", "retryable": False, "authorization_required": False},
        }
    )
    del wire["proposal_revision"]
    jsonl = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-no-revision"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(wire)},
                }
            ),
        )
    )

    with pytest.raises(ResultParseError):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(jsonl),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "failed"
    error = json.loads(run.structured_error_json)
    assert error["code"] == "codex_result_invalid"
    assert error["session_continuable"] is True
    assert "proposal_revision" in error["detail"]


def test_audited_email_executed_binds_operation_id_from_the_run(setup):
    store, email_task, email_context, parent = _audited_email_setup(setup)
    executor = CapturingExecutor(
        _audit_jsonl("email-action:retyped-by-model", session="session-email-audit")
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        domain_continuation=_EvidenceDriver(evidence=True),
    ).run(email_task, email_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        email_task.id,
        email_task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.operation_id
    assert result.result.outcome is AuditOutcome.EXECUTED
    assert result.result.external_result is not None
    assert result.result.external_result.operation_id == run.operation_id
    persisted = json.loads(run.final_result_json)
    assert persisted["external_result"]["operation_id"] == run.operation_id
    assert persisted["proposal_revision"] == run.proposal_revision
