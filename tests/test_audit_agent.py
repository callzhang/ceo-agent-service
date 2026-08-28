import hashlib
import json
import sqlite3
import tomllib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent_context import (
    _AUDIT_AGENT_RULES,
    AgentTaskContext,
    AuditTurnContext,
    MaterialReference,
)
from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerProposal,
    ProposedAction,
)
from app.agent_effects import EffectKind, McpToolEffectRegistry
from app.agent_result import ResultParseError
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_skill_usage import LoadedSkillReceipt
from app.agent_turn_runner import (
    AgentTurnProcess,
    _dingtalk_message_readback_proof,
    _is_dingtalk_chat_send_argv,
    _json_digest,
    _message_rendered_text_digest,
    _metadata_matches_action,
)
from app.agent_wire_contracts import AuditAgentWireResult
from app.audit_agent import (
    AuditAgentRunner,
    _audit_recovery_error_code,
    _expected_effect_action,
    _initial_write_authorizations,
    _recovery_authorizations,
    _recovery_prompt,
)
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.consumer_agent import AUDIT_DYNAMIC_SKILL_BODY, audit_developer_instructions
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    describe_native_command,
)
from app.process_runner import ProcessRunResult
from app.runtime_environment import central_python
from app.store import AgentExecutionReceipt, AgentRole, AutoReplyStore
from app.wechat.codex_safety import ControlledCliConfig, make_audit_agent_command
from tests.prompt_structure import validate_prompt_structure


class CapturingExecutor:
    def __init__(
        self,
        stdout: str,
        *,
        returncode: int = 0,
        inject_skill_receipt: bool = True,
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.inject_skill_receipt = inject_skill_receipt
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        self.prompts.append(kwargs["prompt"])
        self.commands.append(command)
        explicit_skill_read = '"tool": "read_skill"' in self.stdout
        if self.inject_skill_receipt and not explicit_skill_read:
            marker = "Verified Skills read by Consumer A\n"
            if marker in kwargs["prompt"]:
                receipt_text = (
                    kwargs["prompt"]
                    .split(marker, 1)[1]
                    .split("\n\nCandidate revision\n", 1)[0]
                )
                receipts = json.loads(receipt_text)
                if receipts:
                    receipt = receipts[0]
                    path = Path(receipt["path"])
                    skill_line, digest = _skill_read_jsonl(
                        path,
                        path.read_text(encoding="utf-8"),
                    )
                    assert digest == receipt["sha256"]
                    on_stdout_line(skill_line)
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
    }


def _audit_result_jsonl(
    outcome: str,
    *,
    operation_id: str,
    session: str,
    proposal_revision: int = 0,
    include_read: bool = True,
    include_write: bool = False,
    read_target: str = "cid-agent",
    read_stdout: str | None = None,
    structured_read_receipt: bool = False,
    reconciliation: list[dict[str, object]] | None = None,
) -> str:
    if read_stdout is None:
        now = datetime.now(UTC)
        content_is_present = outcome == "executed"
        read_stdout = json.dumps(
            {
                "complete": True,
                "hasMore": False,
                "paginationKnown": True,
                "failures": [],
                "queryRange": {
                    "startTime": (now - timedelta(minutes=30)).isoformat(),
                    "endTime": (now + timedelta(minutes=30)).isoformat(),
                },
                "messages": (
                    [
                        {
                            "conversationId": read_target,
                            "messageId": "recovered-message",
                            "text": "done",
                        }
                    ]
                    if content_is_present
                    else []
                ),
            }
        )
    records = [json.dumps({"type": "thread.started", "thread_id": session})]
    if include_read:
        arguments = {
            "argv": [
                "dws",
                "chat",
                "message",
                "list",
                "--group",
                read_target,
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
            "result_digest": "recovery-read-digest",
            "stdout": read_stdout,
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
                                **(
                                    {"structuredContent": receipt}
                                    if structured_read_receipt
                                    else {}
                                ),
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
    elif outcome == "reconciled":
        result = {
            "outcome": "failed",
            "summary": "Live result could not be confirmed.",
            "proposal_revision": proposal_revision,
            "feedback": None,
            "external_result": None,
            "error": {
                "code": "audit_result_unconfirmed",
                "retryable": True,
                "authorization_required": False,
            },
        }
    else:
        result = {
            "outcome": "needs_human",
            "summary": "Live state remains ambiguous.",
            "proposal_revision": proposal_revision,
            "feedback": None,
            "external_result": None,
            "decision_options": [
                {
                    "key": "A",
                    "label": "Confirm occurred",
                    "instruction": "Confirm the external action occurred.",
                    "consequence": "The action will not be replayed.",
                },
                {
                    "key": "B",
                    "label": "Confirm absent",
                    "instruction": "Confirm the external action did not occur.",
                    "consequence": "The task may be safely reopened.",
                },
            ],
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
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(_wire_result(result)),
                },
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
            "verification_summary": "Present.",
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


def _skill_read_jsonl(path: Path, content: str) -> tuple[str, str]:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    receipt = {
        "content": content,
        "sha256": digest,
        "path": str(path.resolve()),
        "name": path.parent.name,
    }
    item = {
        "type": "mcp_tool_call",
        "id": "skill-read",
        "server": "agent_cli",
        "tool": "read_skill",
        "arguments": {"path": str(path)},
        "status": "completed",
        "result": {
            "content": [{"type": "text", "text": json.dumps(receipt)}],
            "structuredContent": receipt,
            "isError": False,
        },
    }
    return json.dumps({"type": "item.completed", "item": item}), digest


def _failed_skill_read_jsonl(path: Path) -> str:
    item = {
        "type": "mcp_tool_call",
        "id": "skill-read-failed",
        "server": "agent_cli",
        "tool": "read_skill",
        "arguments": {"path": str(path)},
        "status": "failed",
        "result": {
            "content": [{"type": "text", "text": "Skill could not be read."}],
            "isError": True,
        },
    }
    return json.dumps({"type": "item.completed", "item": item})


def _started_skill_read_jsonl(path: Path) -> str:
    return json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "mcp_tool_call",
                "id": "skill-read-started",
                "server": "agent_cli",
                "tool": "read_skill",
                "arguments": {"path": str(path)},
                "status": "in_progress",
            },
        }
    )


def _revision_required_jsonl(observation: str) -> str:
    result = {
        "outcome": "revision_required",
        "summary": observation,
        "proposal_revision": 0,
        "feedback": {
            "rule": "verified Skill handoff",
            "observation": observation,
            "requested_revision": "Load the applicable business Skill and replace the proposal.",
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
                    "expected_verification": "Message exists",
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
    skill_path = tmp_path / "installed-skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_content = "# Business review\n"
    skill_path.write_text(skill_content, encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS",
        (tmp_path / "installed-skills",),
    )
    audit_context = AuditTurnContext(
        task=context,
        proposal_revision=0,
        operation_id="operation-1",
        proposal=proposal,
        audit_rules="Check authority.",
        consumer_skills=(
            LoadedSkillReceipt(
                "business-review",
                str(skill_path),
                hashlib.sha256(skill_content.encode("utf-8")).hexdigest(),
            ),
        ),
    )
    return store, task, audit_context, parent


def _seed_crashed_audit_write(setup):
    store, task, audit_context, parent = setup
    initial_lines = _audit_jsonl(
        "operation-1",
        session="audit-session-recovery",
        include_verification=False,
    ).splitlines()
    skill_path = Path(audit_context.consumer_skills[0].path)
    skill_event, _digest = _skill_read_jsonl(
        skill_path,
        skill_path.read_text(encoding="utf-8"),
    )
    executor = CapturingExecutor(
        "\n".join((initial_lines[0], skill_event, initial_lines[1])),
        returncode=1,
    )
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
    assert run.status == "failed"
    assert run.codex_session_id == "audit-session-recovery"
    assert len(run.tool_events) == 2
    persisted_item = run.tool_events[1]["item"]
    assert "arguments" not in persisted_item and "result" not in persisted_item
    assert persisted_item["metadata"]["operation_id"] == "operation-1"
    assert persisted_item["metadata"]["target_identifiers"] == {"group": "cid-agent"}
    return store, task, audit_context, run


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


def _seed_crashed_xiaoqing_write(setup):
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
                json.dumps({"type": "thread.started", "thread_id": "direct-session"}),
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
    assert run is not None and run.status == "failed"
    return store, task, context, run, registry


def _xiaoqing_recovery_jsonl(run, *, include_read, disposition="present"):
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
    read_result = {
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
    }
    base = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        include_read=False,
        reconciliation=(
            [
                {
                    "action_index": 0,
                    "disposition": disposition,
                    "read_result_digest": _json_digest(read_result),
                }
            ]
            if include_read
            else []
        ),
    ).splitlines()
    if not include_read:
        return "\n".join(base)

    return "\n".join(
        (
            base[0],
            json.dumps({"type": "item.started", "item": read}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        **read,
                        "status": "completed",
                        "result": read_result,
                    },
                }
            ),
            base[-1],
        )
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
    assert run is not None and run.status == "failed"
    return store, task, context, run, registry, write


def test_audit_uses_typed_result_without_application_receipt_validation(setup):
    store, task, audit_context, parent = setup
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_jsonl("operation-1", session="session-audit")
        ),
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    persisted = store.get_agent_run(result.run_id)
    assert result.result.outcome is AuditOutcome.EXECUTED
    assert persisted is not None and persisted.status == "completed"


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


def test_audit_result_revision_mismatch_is_failed_without_reconciliation(setup):
    store, task, audit_context, parent = setup
    with pytest.raises(RuntimeError, match="audit_proposal_revision_mismatch"):
        AuditAgentRunner(
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
    assert run is not None and run.status == "failed"
