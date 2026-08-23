import hashlib
import json
import sqlite3
import tomllib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.audit_agent as audit_agent_module
from app.agent_context import (
    _AUDIT_AGENT_RULES,
    AgentTaskContext,
    AuditTurnContext,
    MaterialReference,
)
from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    AuditReconciliation,
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
    _action_completion_accounting,
    _action_receipt_operation_id,
    _actions_have_required_readbacks,
    _dingtalk_message_readback_proof,
    _is_dingtalk_chat_send_argv,
    _json_digest,
    _message_rendered_text_digest,
    _metadata_matches_action,
    _read_matches_action,
    _validated_reconciliation,
)
from app.agent_wire_contracts import AuditAgentWireResult
from app.audit_agent import (
    AuditAgentRunner,
    _audit_recovery_error_code,
    _expected_effect_action,
    _initial_write_authorizations,
    _proposed_operation_contract_valid,
    _recovery_authorizations,
    _recovery_prompt,
)
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.consumer_agent import AUDIT_DYNAMIC_SKILL_BODY, audit_developer_instructions
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
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


def test_audit_effect_start_blocks_api_fallback(setup, monkeypatch):
    store, task, audit_context, parent = setup
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth,codex_api")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "fallback-test-key")
    write_stream = _audit_result_jsonl(
        "executed",
        operation_id="operation-1",
        session="oauth-session",
        include_write=True,
    ).splitlines()
    started_write = next(
        line
        for line in write_stream
        if (
            (payload := json.loads(line)).get("type") == "item.started"
            and payload.get("item", {}).get("tool") == "execute_reviewed_write"
        )
    )
    failure_stream = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "oauth-session"}),
            started_write,
            json.dumps(
                {
                    "type": "error",
                    "message": "stream disconnected before completion",
                }
            ),
        )
    )
    executor = CapturingExecutor(failure_stream, returncode=1)
    config, router, adapter = _audit_runtime_dependencies(store)

    with pytest.raises(RuntimeError):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            runtime_config=config,
            runtime_router=router,
            codex_adapter=adapter,
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    [attempt] = store.list_agent_runtime_attempts(run.id)
    assert attempt.route_name == "codex_oauth"
    assert attempt.status == "failed"
    assert attempt.failure_class == "transport"
    assert attempt.first_effect_started_at
    assert run.side_effect_state == "unknown"
    assert len(executor.commands) == 1


def test_failed_route_replays_hidden_completed_write_before_failover(
    setup, monkeypatch
):
    store, task, audit_context, parent = setup
    completed_write = next(
        payload
        for line in _audit_result_jsonl(
            "executed",
            operation_id="operation-1",
            session="oauth-hidden-write",
            include_write=True,
        ).splitlines()
        if (payload := json.loads(line)).get("type") == "item.completed"
        and payload.get("item", {}).get("tool") == "execute_reviewed_write"
    )
    skill_path = Path(audit_context.consumer_skills[0].path)
    skill_event, _ = _skill_read_jsonl(
        skill_path, skill_path.read_text(encoding="utf-8")
    )
    failure_stream = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "oauth-hidden-write"}),
            skill_event,
            json.dumps(
                {"type": "error", "message": "stream disconnected before completion"}
            ),
        )
    )
    monkeypatch.setattr(
        "app.agent_turn_runner.count_codex_session_lines", lambda *args, **kwargs: 5
    )
    monkeypatch.setattr(
        "app.agent_turn_runner.extract_codex_mcp_tool_results_from_session",
        lambda *args, **kwargs: [completed_write],
    )
    config, router, adapter = _audit_runtime_dependencies(store)
    executor = CapturingExecutor(failure_stream, returncode=1)

    with pytest.raises(RuntimeError, match="codex_process_failed"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            runtime_config=config,
            runtime_router=router,
            codex_adapter=adapter,
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "unknown"
    [attempt] = store.list_agent_runtime_attempts(run.id)
    assert attempt.route_name == "codex_oauth"
    assert attempt.status == "failed"
    assert attempt.session_id == "oauth-hidden-write"
    assert attempt.transcript_start == 0
    assert attempt.transcript_end == 5
    assert attempt.first_effect_started_at
    assert len(executor.commands) == 1


def test_audit_never_resumes_or_overwrites_consumer_oauth_route_session(setup):
    store, task, audit_context, parent = setup
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_oauth",
        "consumer-oauth-session",
    )
    executor = CapturingExecutor(
        _revision_required_jsonl("Keep the candidate read-only.")
    )

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert executor.commands[0][:2] == ["codex", "exec"]
    assert executor.commands[0][:3] != ["codex", "exec", "resume"]
    assert (
        store.get_conversation_runtime_session(
            task.conversation_id,
            "codex_oauth",
        )
        == "consumer-oauth-session"
    )


def test_audit_never_reads_or_overwrites_consumer_api_route_session(setup):
    store, task, audit_context, parent = setup
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_api",
        "consumer-api-session",
    )
    config, router, adapter = _audit_runtime_dependencies(
        store,
        routes="codex_api",
    )
    executor = CapturingExecutor(
        "\n".join(
            (
                json.dumps(
                    {"type": "thread.started", "thread_id": "audit-api-session"}
                ),
                _revision_required_jsonl("Keep the candidate read-only."),
            )
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert executor.commands[0][:2] == ["codex", "exec"]
    assert executor.commands[0][:3] != ["codex", "exec", "resume"]
    assert (
        store.get_conversation_runtime_session(
            task.conversation_id,
            "codex_api",
        )
        == "consumer-api-session"
    )
    run = store.get_agent_run(result.run_id)
    assert run is not None and run.codex_session_id == ""
    [attempt] = store.list_agent_runtime_attempts(result.run_id)
    assert attempt.route_name == "codex_api"
    assert attempt.session_mode == "fresh"
    assert attempt.session_id


def test_audit_requires_exact_reviewed_skill_and_write_capabilities(setup):
    store, _, audit_context, _ = setup
    runner = AuditAgentRunner(store=store, workspace=Path("/workspace"))

    required = runner._required_capabilities(audit_context, recovery_phase="")

    receipt = audit_context.consumer_skills[0]
    assert f"reviewed_skill:{receipt.name}:{receipt.sha256}" in required
    assert "mcp:agent_cli:reviewed_write" in required
    assert "native_cli:dws" in required


def test_audit_reconciliation_does_not_require_historical_skill_receipts(setup):
    store, _, audit_context, _ = setup
    runner = AuditAgentRunner(store=store, workspace=Path("/workspace"))

    required = runner._required_capabilities(audit_context, recovery_phase="reconcile")

    assert "mcp:agent_cli:reviewed_write" not in required
    assert "mcp:agent_cli:reviewed_read" in required
    assert not any(item.startswith("reviewed_skill:") for item in required)


class ExactReceiptExecutor(CapturingExecutor):
    def __init__(self, stdout, *, store, run, owner="audit-owner"):
        super().__init__(stdout)
        self.store = store
        self.run = run
        self.owner = owner

    def __call__(self, command, *, on_stdout_line, **kwargs):
        persisted = self.store.get_agent_run(self.run.id)
        assert persisted is not None
        metadata = next(
            event["item"]["metadata"]
            for event in persisted.tool_events
            if event["type"] == "item.started"
            and event["item"]["metadata"].get("effect") == "effectful"
        )
        self.store.record_agent_execution_receipt(
            self.run.id,
            receipt_id=f"receipt-{self.run.operation_id}",
            operation_id=_action_receipt_operation_id(
                self.run.operation_id,
                metadata,
                0,
            ),
            cli=metadata["capability"],
            command_path=metadata["operation"],
            command_digest=metadata["operation_digest"],
            exit_code=0,
            owner=self.owner,
            expected_status="unknown",
        )
        return super().__call__(command, on_stdout_line=on_stdout_line, **kwargs)


def test_audit_composed_instructions_are_skill_first_and_schema_authoritative(setup):
    audit_rules = "AUDIT-RULE-SENTINEL: preserve the candidate meaning."
    _store, _task, audit_context, _parent = setup
    context_facts = audit_context.render(
        current_time="2026-08-11 23:00:00 +0800"
    ).partition("## Context Facts\n")[2]
    instructions = (
        audit_developer_instructions(audit_rules)
        + "\n\n"
        + _AUDIT_AGENT_RULES
        + f"\n\n## Context Facts\n{context_facts}"
    )

    validate_prompt_structure(
        instructions,
        contract_models=(
            ("Pydantic Wire Contract", AuditAgentWireResult),
            ("Pydantic Result Contract", AuditAgentResult),
        ),
        dynamic_skill_body=AUDIT_DYNAMIC_SKILL_BODY,
        audit_rules=audit_rules,
        context_facts=context_facts,
        size_limit=36_000,
        require_runtime_safety_sections=True,
    )
    assert audit_rules in instructions
    assert AUDIT_DYNAMIC_SKILL_BODY in instructions


def test_recovery_prompt_defines_exact_wire_reconciliation_shape(setup):
    store, _task, audit_context, run = _seed_crashed_audit_write(setup)

    prompt = _recovery_prompt(
        run,
        audit_context,
        tuple(a.model_dump(mode="json") for a in audit_context.proposal.actions),
        McpToolEffectRegistry.default(),
    )

    assert "reconciliation must be an array" in prompt
    assert "RECOVERY MODE OVERRIDES NORMAL AUDIT EXECUTION" in prompt
    assert "The only valid outcome for this turn is reconciled" in prompt
    assert "Do not return executed" in prompt
    assert "reconciliation_json" not in prompt
    assert "Do not wrap the array in an operation_id/entries object" in prompt
    assert "read_result_digest" in prompt
    assert "unknown readback command is an evidence task" in prompt
    assert "Exact readback contracts:" in prompt
    assert "shares a stable identifier from its exact readback contract" in prompt
    assert "Do not substitute a different target type" in prompt
    assert "Do not start with an unbounded read" in prompt
    assert "an incomplete window cannot prove absence" in prompt
    assert "use the ambiguous disposition" in prompt
    assert "dws chat +chat-messages" in prompt
    assert "exact full message text" in prompt
    assert "no wider than two hours" in prompt


def test_audit_developer_instructions_define_wire_json_field_shapes():
    instructions = audit_developer_instructions("Verify every supported fact.")

    assert "## Pydantic Wire Contract" in instructions
    assert "## Pydantic Result Contract" in instructions
    assert '"external_result"' in instructions
    assert '"reconciliation"' in instructions
    assert '"side_effect_state"' in instructions
    assert '"read_result_digest"' in instructions
    assert '"title":"AuditAgentWireResult"' in instructions
    assert "external_result must\ncontain exactly" in instructions
    assert (
        'dws schema --cli-path "<product> <command>" --compact --format json'
        in instructions
    )
    assert "discovery is not an unavailable-tool result" in instructions
    assert (
        "operation_id, verification_summary, and\nlive_result_reference" in instructions
    )
    assert (
        "operation_id must equal the candidate proposal\noperation_id" in instructions
    )
    assert "reconciliation is always an array" in instructions
    assert "use [] unless\noutcome is reconciled" in instructions
    assert "object wrapper in reconciliation" in instructions
    assert (
        "exactly these string fields:\nrule, observation, and requested_revision"
        in instructions
    )
    assert "failed_rule, evidence, or required_change" in instructions
    assert "reconciled requires\nside_effect_state=unknown" in instructions
    assert (
        "action_index, disposition (present,\nabsent, or ambiguous), and read_result_digest"
        in instructions
    )
    assert "reconciled outcome is reserved for unknown-outcome recovery" in instructions
    assert (
        "error_code, error_retryable, and error_authorization_required" in instructions
    )
    assert "Do not return a nested error object" in instructions
    assert (
        "return revision_required and ask\nConsumer Agent A to return no_action"
        in instructions
    )
    assert "Never execute a DWS write command without --yes" in instructions
    assert "missing command syntax is a read-only evidence task" in instructions
    assert "reviewed local read command" in instructions


def test_audit_instructions_require_receipt_sha_comparison_and_fail_closed_review():
    instructions = audit_developer_instructions("Verify every supported fact.")

    assert AUDIT_DYNAMIC_SKILL_BODY in instructions
    assert instructions.count("[dynamic-skill]") == 1


def _wire_result(result: dict[str, object]) -> dict[str, object]:
    error = result["error"]
    assert isinstance(error, dict)
    return {
        "outcome": result["outcome"],
        "summary": result["summary"],
        "proposal_revision": result["proposal_revision"],
        "side_effect_state": result["side_effect_state"],
        "feedback": result["feedback"],
        "external_result": result["external_result"],
        "reconciliation": result["reconciliation"],
        "decision_options": result.get("decision_options", []),
        "error_code": error["code"],
        "error_retryable": error["retryable"],
        "error_authorization_required": error["authorization_required"],
    }


def test_recovery_mcp_env_override_is_a_toml_inline_table():
    command = [
        "codex",
        "exec",
        "prompt",
        "-c",
        'mcp_servers.xiaoqing_interview.command="server"',
    ]
    make_audit_agent_command(
        command,
        controlled_cli=ControlledCliConfig(
            command="python",
            args=("-m", "app.agent_cli"),
            cwd="/tmp/service root",
            env=(("ALLOWLIST", '[{"text":"quoted \\" value"}]'),),
        ),
        allow_write=True,
    )
    override = next(
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "-c" and command[index + 1].startswith("mcp_servers.agent_cli.env=")
    )
    parsed = tomllib.loads("value=" + override.partition("=")[2])

    assert parsed["value"] == {"ALLOWLIST": '[{"text":"quoted \\" value"}]'}


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
        content_is_present = outcome == "executed" or (
            any(
                entry.get("disposition") == "present"
                for entry in (reconciliation or [])
            )
            if reconciliation is not None
            else outcome == "reconciled" and not include_write
        )
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
    elif outcome == "reconciled":
        result = {
            "outcome": "reconciled",
            "summary": "Live reconciliation recorded.",
            "proposal_revision": proposal_revision,
            "side_effect_state": "unknown",
            "feedback": None,
            "external_result": None,
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
    if reconciliation is None:
        reconciliation = []
        if include_read:
            disposition = (
                "ambiguous"
                if outcome == "needs_human"
                else "absent"
                if include_write
                else "present"
            )
            reconciliation.append(
                {
                    "action_index": 0,
                    "disposition": disposition,
                    "read_result_digest": "recovery-read-digest",
                }
            )
    result["reconciliation"] = reconciliation
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
        "side_effect_state": "confirmed",
        "feedback": None,
        "external_result": {
            "operation_id": operation_id,
            "verification_summary": "Present.",
            "live_result_reference": {"id": "one"},
        },
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "reconciliation": [],
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
        "side_effect_state": "none",
        "feedback": None,
        "external_result": None,
        "decision_options": [],
        "error": {
            "code": "dry_run_execution_suppressed",
            "retryable": False,
            "authorization_required": False,
        },
        "reconciliation": [],
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
        "side_effect_state": "none",
        "feedback": {
            "rule": "verified Skill handoff",
            "observation": observation,
            "requested_revision": "Load the applicable business Skill and replace the proposal.",
        },
        "external_result": None,
        "reconciliation": [],
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


def test_scripted_audit_voluntarily_proceeds_after_matching_consumer_skill_sha(
    setup, tmp_path, monkeypatch
):
    store, task, audit_context, parent = setup
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    content = "# Business review\n"
    skill_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS", (tmp_path / "skills",)
    )
    skill_event, digest = _skill_read_jsonl(skill_path, content)
    context = replace(
        audit_context,
        consumer_skills=(
            LoadedSkillReceipt("business-review", str(skill_path), digest),
        ),
    )
    execution = _audit_jsonl("operation-1", session="session-match").splitlines()
    stream = "\n".join((execution[0], skill_event, *execution[1:]))

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(stream),
    ).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert result.result.outcome.value == "executed"
    persisted = store.get_agent_run(result.run_id)
    assert persisted is not None
    skill_metadata = persisted.tool_events[0]["item"]["metadata"]
    assert skill_metadata["skill_path"] == str(skill_path)
    assert skill_metadata["skill_sha256"] == digest


def test_scripted_audit_voluntarily_requires_revision_for_changed_skill_sha(
    setup, tmp_path, monkeypatch
):
    store, task, audit_context, parent = setup
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    content = "# Changed business review\n"
    skill_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS", (tmp_path / "skills",)
    )
    skill_event, _digest = _skill_read_jsonl(skill_path, content)
    context = replace(
        audit_context,
        consumer_skills=(
            LoadedSkillReceipt("business-review", str(skill_path), "a" * 64),
        ),
    )
    stream = "\n".join(
        (skill_event, _revision_required_jsonl("The Skill sha256 changed."))
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(stream),
    ).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert result.result.outcome.value == "revision_required"
    assert result.result.feedback is not None
    assert "changed" in result.result.feedback.observation
    assert store.get_agent_run(result.run_id).side_effect_state == "none"


def test_audit_runtime_requires_skill_reread_even_for_revision_result(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="audit_skill_reread_missing",
    ):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _revision_required_jsonl("Read the verified Skill first."),
                inject_skill_receipt=False,
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_retry_cannot_reuse_prior_turn_skill_receipt(setup):
    store, task, audit_context, parent = setup
    skill_path = Path(audit_context.consumer_skills[0].path)
    old_receipt, _digest = _skill_read_jsonl(
        skill_path,
        skill_path.read_text(encoding="utf-8"),
    )

    with pytest.raises(ResultParseError):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(old_receipt),
            owner="audit-owner-first",
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="audit_skill_reread_missing",
    ):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _revision_required_jsonl("Read the Skill in this turn."),
                inject_skill_receipt=False,
            ),
            owner="audit-owner-retry",
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_failed_skill_reread_can_return_revision_required(setup):
    store, task, audit_context, parent = setup
    skill_path = Path(audit_context.consumer_skills[0].path)
    stream = "\n".join(
        (
            _failed_skill_read_jsonl(skill_path),
            _revision_required_jsonl("The required Skill is unreadable."),
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(stream),
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert result.result.outcome is AuditOutcome.REVISION_REQUIRED
    persisted = store.get_agent_run(result.run_id)
    assert persisted is not None
    assert any(
        event["type"] == "item.failed"
        and event["item"]["metadata"].get("requested_skill_path")
        == str(skill_path.resolve())
        for event in persisted.tool_events
    )


def test_audit_started_skill_reread_is_not_an_attempted_failure(setup):
    store, task, audit_context, parent = setup
    skill_path = Path(audit_context.consumer_skills[0].path)
    stream = "\n".join(
        (
            _started_skill_read_jsonl(skill_path),
            _revision_required_jsonl("The Skill call never returned."),
        )
    )

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="audit_skill_reread_missing",
    ):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(stream),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_runtime_blocks_write_before_exact_skill_reread(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="audit_skill_reread_missing",
    ):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl("operation-1", session="missing-skill-reread"),
                inject_skill_receipt=False,
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert run.side_effect_state == "none"
    assert not any(
        event.get("type") == "item.started"
        and event.get("item", {}).get("metadata", {}).get("effect") == "effectful"
        for event in run.tool_events
    )


def test_audit_protocol_rejects_applicable_candidate_without_consumer_skill_receipt(
    setup,
):
    store, task, audit_context, parent = setup
    audit_context = replace(audit_context, consumer_skills=())
    executor = CapturingExecutor(_audit_jsonl("operation-1", session="would-execute"))

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert result.result.outcome.value == "revision_required"
    assert result.result.feedback is not None
    assert "verified Consumer Skill receipt" in result.result.feedback.observation
    assert executor.commands == []
    assert store.get_agent_run(result.run_id).tool_events == []


def test_audit_instructions_require_dynamic_skill_reread_before_execution():
    instructions = audit_developer_instructions("test rules")

    assert (
        "[dynamic-skill] Audit Agent B independently determines every business and "
        "operation Skill applicable to the candidate, requires the corresponding "
        "verified Consumer A receipt for each applicable Skill, rereads each exact "
        "receipt path with `agent_cli.read_skill`, verifies its sha256, and returns "
        "revision_required if any applicable receipt is absent, unreadable, changed, "
        "or mismatched. For an already-unknown effect only, B may perform strictly "
        "read-only evidence reconciliation without a receipt when no business Skill "
        "is needed to decide whether the effect happened; B must not execute or retry "
        "the candidate."
    ) in instructions
    assert instructions.count("[dynamic-skill]") == 1
    assert "feedback_json" not in instructions
    assert "external_result_json" not in instructions
    assert "reconciliation_json" not in instructions
    assert "feedback is required" in instructions
    assert "external_result must\ncontain exactly" in instructions


def test_audit_returns_dws_write_without_confirmation_to_consumer(setup):
    store, task, audit_context, parent = setup
    invalid_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Approve request",
            "actions": [
                {
                    "description": "Approve the exact OA task",
                    "capability": "agent_cli.dws",
                    "operation": "oa approval approve",
                    "target": {"instance_id": "instance-1", "task_id": "task-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "oa",
                            "approval",
                            "approve",
                            "--instance-id",
                            "instance-1",
                            "--task-id",
                            "task-1",
                            "--remark",
                            "同意",
                            "--format",
                            "json",
                        ]
                    },
                    "expected_verification": "OA task is completed",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "Materials satisfy the rule",
        }
    )
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(
        task,
        replace(audit_context, proposal=invalid_proposal),
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    assert result.result.outcome.value == "revision_required"
    assert result.result.feedback is not None
    assert "--yes" in result.result.feedback.requested_revision
    assert executor.commands == []


def test_audit_returns_write_without_executable_contract_to_consumer(setup):
    store, task, audit_context, parent = setup
    invalid_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Reply in the source group",
            "actions": [
                {
                    "description": "Send the requested Skill location",
                    "capability": "dingtalk-chat",
                    "operation": (
                        "dws chat +send-to-group --group cid-group "
                        "--content <content> --yes"
                    ),
                    "target": {
                        "conversation_id": "cid-group",
                        "recipient_type": "group",
                    },
                    "payload": {"content": "Skill location"},
                    "expected_verification": "The group message exists",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "The source group requested the link.",
        }
    )
    executor = CapturingExecutor("")
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(
        task,
        replace(audit_context, proposal=invalid_proposal),
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )
    assert result.result.outcome.value == "revision_required"
    assert result.result.feedback is not None
    assert "executable" in result.result.feedback.requested_revision
    assert executor.commands == []


def test_completed_chat_dm_content_is_recorded_in_delivery_ledger(setup):
    store, task, _audit_context, parent = setup
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id="operation-1",
        owner="audit-owner",
    ).run
    argv = [
        "dws",
        "chat",
        "+dm",
        "--to",
        task.trigger_sender,
        "--content",
        "Approval completed.",
        "--yes",
        "--format",
        "json",
    ]
    event = {
        "type": "item.completed",
        "item": {
            "metadata": {
                "effect": "effectful",
                "capability": "agent_cli.dws",
                "operation": "chat +dm",
                "result_digest": "result-digest",
            }
        },
    }
    payload = {"item": {"arguments": {"argv": argv}}}
    AgentTurnProcess(
        store=store,
        task=task,
        workspace=Path("/workspace"),
        owner="audit-owner",
    )._record_direct_send_receipt(event, payload, run=run)
    assert store.has_sent_reply_for_trigger(
        task.conversation_id,
        task.trigger_message_id,
    )


def test_audit_returns_single_chat_open_id_passed_as_user_to_consumer(setup):
    store, task, audit_context, parent = setup
    single_chat_task = task.model_copy(update={"single_chat": True})
    single_chat_context = replace(
        audit_context,
        task=replace(
            audit_context.task,
            single_chat=True,
            trigger_sender_open_dingtalk_id="open-dingtalk-1",
        ),
        proposal=ConsumerProposal.model_validate(
            {
                "objective": "Reply to the sender",
                "actions": [
                    {
                        "description": "Send the verified reply",
                        "capability": "agent_cli.dws",
                        "operation": "chat +messages-send",
                        "target": {"user": "open-dingtalk-1"},
                        "payload": {
                            "argv": [
                                "dws",
                                "chat",
                                "+messages-send",
                                "--as",
                                "user",
                                "--user",
                                "open-dingtalk-1",
                                "--text",
                                "done",
                                "--yes",
                            ]
                        },
                        "expected_verification": "Message exists",
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "The trigger identifies the direct recipient.",
            }
        ),
    )
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(
        single_chat_task,
        single_chat_context,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    assert result.result.outcome.value == "revision_required"
    assert result.result.feedback is not None
    assert "open-DingTalk ID as a user ID" in result.result.feedback.observation
    assert "--open-dingtalk-id" in result.result.feedback.requested_revision
    assert executor.commands == []


def test_audit_keeps_typed_recipient_check_when_cli_label_is_noncanonical(setup):
    store, task, audit_context, parent = setup
    single_chat_task = task.model_copy(update={"single_chat": True})
    single_chat_context = replace(
        audit_context,
        task=replace(
            audit_context.task,
            single_chat=True,
            trigger_sender_open_dingtalk_id="open-dingtalk-1",
        ),
        proposal=ConsumerProposal.model_validate(
            {
                "objective": "Reply to the sender",
                "actions": [
                    {
                        "description": "Send the verified reply",
                        "capability": "dws",
                        "operation": "chat +messages-send",
                        "target": {"user": "open-dingtalk-1"},
                        "payload": {
                            "argv": [
                                "dws",
                                "chat",
                                "+messages-send",
                                "--as",
                                "user",
                                "--user",
                                "open-dingtalk-1",
                                "--text",
                                "done",
                                "--yes",
                            ]
                        },
                        "expected_verification": "Message exists",
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "The trigger identifies the direct recipient.",
            }
        ),
    )
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(
        single_chat_task,
        single_chat_context,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    assert result.result.outcome.value == "revision_required"
    assert result.result.feedback is not None
    assert "open-DingTalk ID as a user ID" in result.result.feedback.observation
    assert executor.commands == []


def test_audit_starts_fresh_and_does_not_replace_conversation_session(
    setup, tmp_path, monkeypatch
):
    store, task, audit_context, parent = setup
    store.upsert_conversation(task.conversation_id, "Group", False, "session-a")
    skill_path = tmp_path / "skills" / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_content = "# Business review\n"
    skill_path.write_text(skill_content, encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS",
        (tmp_path / "skills",),
    )
    skill_event, skill_digest = _skill_read_jsonl(skill_path, skill_content)
    audit_context = replace(
        audit_context,
        consumer_skills=(
            LoadedSkillReceipt(
                "business-review",
                str(skill_path),
                skill_digest,
            ),
        ),
    )
    execution = _audit_jsonl("operation-1", session="session-b").splitlines()
    executor = CapturingExecutor("\n".join((execution[0], skill_event, *execution[1:])))

    result = AuditAgentRunner(
        store=store, workspace=Path("/workspace"), executor=executor
    ).run(
        task,
        audit_context,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    command = executor.commands[0]
    assert command[:2] == ["codex", "exec"]
    assert "resume" not in command
    # The service validates the final wire result after Codex returns; avoid
    # constraining dynamically loaded reviewed MCP tools in the transport.
    assert "--output-schema" not in command
    assert command.count("--disable") == 8
    assert command[command.index("--disable") + 1] == "plugins"
    assert "apps" in command
    assert "tools.enabled_tools=[]" in command
    assert 'approval_policy="on-failure"' in command
    assert 'approvals_reviewer="auto_review"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "execute_reviewed_write", "read_skill", "read_text_file", "read_spreadsheet"]'
        in command
    )
    assert 'web_search="disabled"' not in command
    assert any("## Pydantic Wire Contract" in option for option in command)
    assert any("Do not reopen AGENT.md with shell" in option for option in command)
    assert any("AuditAgentWireResult" in option for option in command)
    assert any("agent_cli.read_skill" in option for option in command)
    assert "every write command remain forbidden for Consumer" not in " ".join(command)
    assert store.get_codex_session_id(task.conversation_id) == "session-a"
    run = store.get_agent_run(result.run_id)
    assert run.role.value == "audit"
    assert run.codex_session_id == "session-b"
    assert run.operation_id == "operation-1"
    assert run.side_effect_state == "confirmed"
    skill_reads = [
        event["item"]["metadata"]
        for event in run.tool_events
        if event["item"].get("metadata", {}).get("skill_path")
    ]
    assert len(skill_reads) == 1
    assert {
        key: skill_reads[0][key] for key in ("skill_name", "skill_path", "skill_sha256")
    } == {
        "skill_name": "business-review",
        "skill_path": str(skill_path),
        "skill_sha256": skill_digest,
    }
    completed_writes = [
        event["item"]
        for event in run.tool_events
        if event["type"] == "item.completed"
        and event["item"].get("metadata", {}).get("operation") == "chat message send"
    ]
    assert len(completed_writes) == 1
    assert completed_writes[0]["status"] == "completed"
    assert completed_writes[0]["metadata"]["target_identifiers"] == {
        "group": "cid-agent"
    }


def test_audit_rejects_malformed_nested_output_locally(setup):
    store, task, audit_context, parent = setup
    malformed = {
        "outcome": "failed",
        "summary": "Invalid legacy wire shape.",
        "proposal_revision": 0,
        "side_effect_state": "none",
        "feedback": None,
        "external_result": "{}",
        "reconciliation": [],
        "error_code": "invalid_result",
        "error_retryable": False,
        "error_authorization_required": False,
    }
    executor = CapturingExecutor(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(malformed),
                },
            }
        )
    )

    with pytest.raises(ResultParseError, match="malformed or does not match"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert "--output-schema" not in executor.commands[0]


def test_audit_recovers_session_only_dingtalk_receipt_but_requires_readback(
    setup, tmp_path, monkeypatch
):
    store, task, audit_context, parent = setup
    session_id = "session-only-receipt"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    argv = [
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
    descriptor = describe_native_command({"type": "command_execution", "argv": argv})
    assert descriptor is not None
    receipt = {
        "content": [{"type": "text", "text": "accepted"}],
        "structuredContent": {
            "cli": "dws",
            "operation": descriptor.command_path,
            "operation_digest": descriptor.command_digest,
            "target_identifiers": descriptor.target_identifiers,
            "result_digest": "session-only-result",
        },
        "isError": False,
    }
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "08"
        / "11"
        / f"rollout-2026-08-11T04-00-00-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            json.dumps(line, ensure_ascii=False)
            for line in (
                {"type": "session_meta", "payload": {"id": session_id}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "mcp_tool_call_end",
                        "call_id": "unreviewed-read",
                        "invocation": {
                            "server": "unreviewed_integration",
                            "tool": "read_context",
                            "arguments": {"candidate_id": "candidate-1"},
                        },
                        "result": {
                            "Ok": {
                                "content": [{"type": "text", "text": "read"}],
                                "isError": False,
                            }
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "mcp_tool_call_end",
                        "call_id": "session-write",
                        "invocation": {
                            "server": "agent_cli",
                            "tool": "execute_reviewed_write",
                            "arguments": {"argv": argv},
                        },
                        "result": {"Ok": receipt},
                    },
                },
            )
        ),
        encoding="utf-8",
    )
    # The streaming transport omitted the completed MCP lifecycle; its durable
    # local session is the only successful controlled-send receipt.
    stdout = _audit_jsonl(
        "operation-1",
        session=session_id,
        include_write=False,
    )

    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(stdout),
    )

    with pytest.raises(RuntimeError, match="audit_external_readback_missing"):
        runner.run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "unknown"
    assert [event["type"] for event in run.tool_events] == [
        "item.completed",
        "item.started",
        "item.completed",
    ]
    assert store.has_sent_reply_for_trigger(
        task.conversation_id, task.trigger_message_id
    )


def test_audit_ignores_session_replay_of_streamed_write(setup, tmp_path, monkeypatch):
    store, task, audit_context, parent = setup
    session_id = "streamed-write-also-in-session"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    argv = [
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
    descriptor = describe_native_command({"type": "command_execution", "argv": argv})
    assert descriptor is not None
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "08"
        / "11"
        / f"rollout-2026-08-11T04-00-00-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            json.dumps(line, ensure_ascii=False)
            for line in (
                {"type": "session_meta", "payload": {"id": session_id}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "mcp_tool_call_end",
                        "call_id": "session-copy-of-streamed-write",
                        "invocation": {
                            "server": "agent_cli",
                            "tool": "execute_reviewed_write",
                            "arguments": {"argv": argv},
                        },
                        "result": {
                            "Ok": {
                                "content": [{"type": "text", "text": "accepted"}],
                                "structuredContent": {
                                    "cli": "dws",
                                    "operation": descriptor.command_path,
                                    "operation_digest": descriptor.command_digest,
                                    "target_identifiers": descriptor.target_identifiers,
                                    "result_digest": "session-copy-result",
                                },
                                "isError": False,
                            }
                        },
                    },
                },
            )
        ),
        encoding="utf-8",
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(_audit_jsonl("operation-1", session=session_id)),
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run(result.run_id)
    assert run is not None and run.status == "completed"
    effectful_starts = [
        event
        for event in run.tool_events
        if event["type"] == "item.started"
        and event["item"]["metadata"]["effect"] == "effectful"
    ]
    assert len(effectful_starts) == 1


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
    assert result.result.outcome.value == "dry_run"
    assert result.result.error.code == "dry_run_execution_suppressed"
    assert result.result.side_effect_state.value == "none"
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill", "read_text_file", "read_spreadsheet"]'
        in command
    )
    assert "execute_reviewed_write" not in command
    assert 'approval_policy="never"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "return dry_run" in executor.prompts[0]
    assert "dry_run_execution_suppressed" not in " ".join(command)


def test_audit_reads_current_audit_rules_for_each_turn(
    setup,
    tmp_path,
    monkeypatch,
):
    store, task, audit_context, parent = setup
    rules_path = tmp_path / "audit_rules.md"
    rules_path.write_text("Current audit rule.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(rules_path))
    executor = CapturingExecutor(_audit_jsonl("operation-1", session="session-b"))

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    assert "Current audit rule." not in executor.prompts[0]
    assert "Check authority." not in executor.prompts[0]
    assert "Effective Audit Rules" not in executor.prompts[0]
    assert any("Current audit rule." in item for item in executor.commands[0])
    assert any("do not rewrite the candidate" in item for item in executor.commands[0])


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

    assert (
        store.get_agent_run_for_turn(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=0,
            turn_attempt=0,
        )
        is None
    )


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
            action = audit_context.proposal.actions[0]
            store.record_agent_execution_receipt(
                run.id,
                receipt_id="receipt-operation-1",
                operation_id=_action_receipt_operation_id(
                    run.operation_id,
                    {
                        "capability": action.capability,
                        "operation": action.operation,
                        "operation_digest": descriptor.command_digest,
                        "arguments_digest": _json_digest(action.payload),
                    },
                    0,
                ),
                cli="dws",
                command_path="chat message send",
                command_digest=descriptor.command_digest,
                exit_code=0,
                owner="audit-owner",
            )
            store.record_sent_reply(
                task.conversation_id,
                task.trigger_message_id,
                "done",
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
                reconciliation=[],
            )
        ),
        owner="audit-owner",
    ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    persisted = store.get_agent_run(result.run_id)
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "confirmed"


def test_audit_rejects_exact_completed_effect_without_external_readback(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(RuntimeError, match="audit_external_readback_missing"):
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

    persisted = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert persisted is not None
    assert persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"


def test_audit_rejects_post_write_readback_for_unrelated_target(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(RuntimeError, match="audit_external_readback_missing"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl(
                    "operation-1",
                    session="session-b",
                    verification_targets=("cid-unrelated",),
                )
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


def test_audit_rejects_same_target_readback_for_wrong_native_operation(setup):
    store, task, audit_context, parent = setup

    with pytest.raises(RuntimeError, match="audit_external_readback_missing"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(
                _audit_jsonl(
                    "operation-1",
                    session="session-b",
                    verification_argv=(
                        "dws",
                        "chat",
                        "member",
                        "list",
                        "--group",
                        "cid-agent",
                    ),
                )
            ),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)


@pytest.mark.parametrize(
    ("verification_targets", "confirmed"),
    [
        (("cid-agent",), False),
        (("cid-agent", "cid-second"), True),
    ],
)
def test_each_effect_action_requires_matching_post_write_readback(
    setup,
    verification_targets,
    confirmed,
):
    store, task, audit_context, parent = setup
    proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send both results",
            "actions": [
                {
                    "description": f"Send to {target}",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"group": target},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "message",
                            "send",
                            "--group",
                            target,
                            "--text",
                            "done",
                            "--yes",
                        ]
                    },
                    "expected_verification": "Message exists",
                }
                for target in ("cid-agent", "cid-second")
            ],
            "sourced_facts": [],
            "authored_judgment": "Both sends were requested.",
        }
    )
    context = replace(audit_context, proposal=proposal)
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_jsonl(
                "operation-1",
                session="session-b",
                write_targets=("cid-agent", "cid-second"),
                verification_targets=verification_targets,
            )
        ),
    )

    if confirmed:
        result = runner.run(
            task,
            context,
            turn_attempt=0,
            parent_agent_run_id=parent.id,
        )
        persisted = store.get_agent_run(result.run_id)
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.side_effect_state == "confirmed"
    else:
        with pytest.raises(RuntimeError, match="audit_external_readback_missing"):
            runner.run(
                task,
                context,
                turn_attempt=0,
                parent_agent_run_id=parent.id,
            )


def test_audit_keeps_exact_completed_effect_unknown_without_live_read(setup):
    store, task, audit_context, parent = setup

    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_jsonl(
                "operation-1",
                session="session-b",
                include_verification=False,
            )
        ),
    )

    with pytest.raises(RuntimeError, match="audit_external_readback_missing"):
        runner.run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    persisted = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"


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


def test_audit_preserves_agent_cli_error_receipt_without_confirming_effect(setup):
    store, task, audit_context, parent = setup
    executor = CapturingExecutor(
        _audit_jsonl("operation-1", session="session-b", write_error=True)
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
    failed_events = [
        event for event in run.tool_events if event.get("type") == "item.failed"
    ]
    assert len(failed_events) == 1
    metadata = failed_events[0]["item"]["metadata"]
    assert metadata["failure_code"] == "dws_transient_failure"
    assert metadata["failure_retryable"] is True
    assert metadata["failure_gate_state"] == "unavailable"


def test_audit_process_failure_with_all_effects_closed_is_failed_not_unknown(setup):
    store, task, audit_context, parent = setup
    first_action = audit_context.proposal.actions[0]
    second_action = ProposedAction.model_validate(
        {
            "description": "Notify applicant",
            "capability": "agent_cli.dws",
            "operation": "chat message send",
            "target": {"user": "applicant-1"},
            "payload": {
                "argv": [
                    "dws",
                    "chat",
                    "message",
                    "send",
                    "--user",
                    "applicant-1",
                    "--text",
                    "pending",
                    "--yes",
                ]
            },
            "expected_verification": "Applicant receives the pending result",
        }
    )
    context = replace(
        audit_context,
        proposal=audit_context.proposal.model_copy(
            update={"actions": (first_action, second_action)}
        ),
    )
    first_lines = _audit_jsonl(
        "operation-1",
        session="session-known-partial-failure",
        include_verification=False,
    ).splitlines()
    failed_arguments = second_action.payload
    failed_descriptor = describe_native_command(
        {"type": "command_execution", "argv": failed_arguments["argv"]}
    )
    assert failed_descriptor is not None
    failed_item = {
        "type": "mcp_tool_call",
        "id": "write-2",
        "server": "agent_cli",
        "tool": "execute_reviewed_write",
        "arguments": failed_arguments,
        "status": "in_progress",
    }
    failure_receipt = {
        "cli": failed_descriptor.cli,
        "operation": failed_descriptor.command_path,
        "operation_digest": failed_descriptor.command_digest,
        "target_identifiers": failed_descriptor.target_identifiers,
        "result_digest": "failed-result-digest",
        "stdout": "",
        "error": {
            "channel": "dws",
            "code": "reconciliation_read_failed",
            "retryable": False,
            "gate_state": "unavailable",
        },
    }
    stdout = "\n".join(
        (
            *first_lines[:-1],
            json.dumps({"type": "item.started", "item": failed_item}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        **failed_item,
                        "status": "completed",
                        "result": {
                            "content": [
                                {"type": "text", "text": json.dumps(failure_receipt)}
                            ],
                            "isError": False,
                        },
                    },
                }
            ),
        )
    )

    with pytest.raises(RuntimeError, match="codex_process_failed"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(stdout, returncode=1),
        ).run(task, context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert run.status == "failed"
    assert run.side_effect_state == "confirmed"
    assert run.effect_started_count == 2
    assert run.effect_completed_count == 1
    assert run.effect_failed_count == 1
    assert json.loads(run.structured_error_json)["code"] == (
        "reconciliation_read_failed"
    )


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
    assert run.status == "unknown"
    assert run.side_effect_state == "unknown"
    assert run.codex_session_id == "audit-session-recovery"
    assert len(run.tool_events) == 2
    persisted_item = run.tool_events[1]["item"]
    assert "arguments" not in persisted_item and "result" not in persisted_item
    assert persisted_item["metadata"]["operation_id"] == "operation-1"
    assert persisted_item["metadata"]["target_identifiers"] == {"group": "cid-agent"}
    return store, task, audit_context, run


def test_claude_session_collision_never_reads_codex_delivery_history(
    setup, monkeypatch
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    [attempt] = store.list_agent_runtime_attempts(run.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update agent_runtime_attempts set runtime_kind='claude_cli' where id=?",
            (attempt.id,),
        )
    history_calls: list[str] = []
    monkeypatch.setattr(
        "app.audit_agent.extract_codex_mcp_tool_results_from_session",
        lambda session_id: history_calls.append(session_id) or (),
    )

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
    )._backfill_persisted_direct_delivery_receipt(task, audit_context, run)

    assert history_calls == []
    assert not store.has_sent_reply_for_trigger(
        task.conversation_id, task.trigger_message_id
    )


def test_historical_unreviewed_backfill_event_does_not_block_reconciliation(
    setup, monkeypatch
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)

    def raise_unreviewed(*_args, **_kwargs):
        raise AgentReadOnlyViolationError("agent_cli_command_unreviewed")

    monkeypatch.setattr(
        "app.audit_agent.extract_codex_mcp_tool_results_from_session",
        lambda _session_id: [{"type": "item.completed", "item": {}}],
    )
    monkeypatch.setattr(AgentTurnProcess, "_normalized_effect_event", raise_unreviewed)

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
    )._backfill_persisted_direct_delivery_receipt(task, audit_context, run)

    assert not store.has_sent_reply_for_trigger(
        task.conversation_id, task.trigger_message_id
    )


def test_recovery_reconciliation_without_skill_receipts_stays_strictly_read_only(
    setup,
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session="receipt-free-reconciliation",
            include_write=False,
            reconciliation=[
                {
                    "action_index": 0,
                    "disposition": "present",
                    "read_result_digest": "recovery-read-digest",
                }
            ],
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(
        task,
        replace(audit_context, consumer_skills=()),
        run=run,
    )

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert len(executor.commands) == 1
    assert "execute_reviewed_write" not in " ".join(executor.commands[0])
    assert all(
        event["item"]["metadata"].get("effect") != "effectful"
        for event in persisted.tool_events[2:]
        if event.get("type") == "item.started"
    )


def test_execute_recovery_without_skill_receipts_defers_before_model_or_write(
    setup,
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    reconcile = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_result_jsonl(
                "reconciled",
                operation_id=run.operation_id,
                session="reconcile-before-missing-receipt",
                include_write=False,
                reconciliation=[
                    {
                        "action_index": 0,
                        "disposition": "absent",
                        "read_result_digest": "recovery-read-digest",
                    }
                ],
            )
        ),
    )
    reconcile.recover(task, audit_context, run=run)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    executor = CapturingExecutor(
        _audit_jsonl(run.operation_id, session="must-not-execute")
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).execute_recovery(
        task,
        replace(audit_context, consumer_skills=()),
        run=persisted,
    )

    failed = store.get_agent_run(run.id)
    requeued = store.get_reply_task(task.id)
    assert result.result.error.code == "audit_skill_receipts_missing"
    assert failed is not None and failed.status == "failed"
    assert failed.side_effect_state == "none"
    assert requeued is not None and requeued.status == "pending"
    assert executor.commands == []


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
    assert persisted.status == "unknown"
    assert persisted.lease_owner == ""
    assert persisted.lease_expires_at == ""
    assert persisted.reconciliation_suspended is False
    assert persisted.reconciliation_next_attempt_at
    assert json.loads(persisted.structured_error_json) == {
        "code": "image_dependency_unavailable",
        "retryable": True,
    }
    assert current_task is not None and current_task.status == "processing"


def test_reconciliation_image_dependency_releases_unknown_run_lease(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor("")

    with pytest.raises(RuntimeError, match="image_dependency_unavailable"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            owner="image-reconciliation-owner",
        ).recover(task, _with_unresolved_image(audit_context), run=run)

    _assert_image_recovery_deferred(store, task, run.id)
    assert executor.commands == []


def test_recovery_execution_image_dependency_releases_unknown_run_lease(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    reconcile = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            reconciliation=[
                {
                    "action_index": 0,
                    "disposition": "absent",
                    "read_result_digest": "recovery-read-digest",
                }
            ],
        )
    )
    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=reconcile,
    ).recover(task, audit_context, run=run)
    reconciled = store.get_agent_run(run.id)
    assert reconciled is not None
    event_count = len(reconciled.tool_events)
    executor = CapturingExecutor("")

    with pytest.raises(RuntimeError, match="image_dependency_unavailable"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            owner="image-execution-owner",
        ).execute_recovery(
            task,
            _with_unresolved_image(audit_context),
            run=reconciled,
        )

    _assert_image_recovery_deferred(store, task, run.id)
    assert len(store.get_agent_run(run.id).tool_events) == event_count
    assert executor.commands == []


def test_crash_after_write_uses_fresh_read_only_recovery_and_confirms_without_replay(
    setup,
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "done")
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
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
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "confirmed"
    assert "resume" not in executor.commands[0]
    assert run.codex_session_id not in executor.commands[0]
    assert "--sandbox" in executor.commands[0]
    assert "read-only" in executor.commands[0]
    assert (
        sum(
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
            for event in persisted.tool_events
        )
        == 1
    )
    receipts = store.list_agent_execution_receipts(run.id)
    assert len(receipts) == 1
    receipt_identity = json.loads(receipts[0].operation_id)
    assert receipt_identity["proposal_operation_id"] == run.operation_id
    assert receipt_identity["capability"] == "agent_cli.dws"
    assert receipt_identity["operation"] == "chat message send"
    read_events = [
        event
        for event in persisted.tool_events
        if event["type"] == "item.completed"
        and event["item"]["metadata"]["effect"] == "read_only"
        and event["item"]["metadata"].get("operation") != "read_skill"
    ]
    assert read_events[0]["item"]["metadata"]["result_digest"] == (
        "recovery-read-digest"
    )
    assert receipts[0].receipt_id == (
        f"reconciliation:{receipts[0].operation_id}:recovery-read-digest"
    )


def test_recovery_keeps_a_specific_audit_failure_code(setup, monkeypatch):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        owner="audit-agent",
    )

    def fail_reconciliation(*args, **kwargs):
        raise RuntimeError("audit_reconciliation_result_invalid")

    monkeypatch.setattr(runner, "_execute_claimed", fail_reconciliation)

    with pytest.raises(RuntimeError, match="audit_reconciliation_result_invalid"):
        runner.recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert json.loads(persisted.structured_error_json)["code"] == (
        "audit_reconciliation_result_invalid"
    )


def test_recovery_conservatively_reconciles_a_valid_non_reconciled_result(
    setup,
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "executed",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            reconciliation=[],
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome is AuditOutcome.RECONCILED
    assert result.result.reconciliation[0].disposition.value == "present"
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "confirmed"


def test_missing_delivery_ledger_does_not_prove_unknown_chat_was_not_sent(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            reconciliation=[
                {
                    "action_index": 0,
                    "disposition": "ambiguous",
                    "read_result_digest": "recovery-read-digest",
                }
            ],
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert result.result.reconciliation[0].disposition.value == "ambiguous"
    assert persisted is not None and persisted.status == "unknown"
    assert (
        store.get_reply_task(task.id).execution_generation == task.execution_generation
    )
    assert executor.commands


def test_mixed_unknown_does_not_treat_missing_sent_reply_as_delivery_absence(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor("", returncode=1)
    mixed_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Approve and notify",
            "actions": [
                audit_context.proposal.actions[0].model_dump(mode="json"),
                {
                    "description": "Notify the applicant",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"open_dingtalk_id": "applicant-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "message",
                            "send",
                            "--open-dingtalk-id",
                            "applicant-1",
                            "--text",
                            "done",
                            "--yes",
                        ]
                    },
                    "expected_verification": "Applicant receives the result",
                },
            ],
            "sourced_facts": [],
            "authored_judgment": "The approval needs a separate applicant notice.",
        }
    )

    with pytest.raises(RuntimeError, match="codex_process_failed"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).recover(task, replace(audit_context, proposal=mixed_proposal), run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    assert (
        store.get_reply_task(task.id).execution_generation == task.execution_generation
    )
    assert executor.commands


def test_direct_chat_user_target_without_delivery_record_rotates_generation(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    user_target_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send direct result",
            "actions": [
                {
                    "description": "Send direct message",
                    "capability": "agent_cli.dws",
                    "operation": "chat +messages-send",
                    "target": {"user": "user-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "+messages-send",
                            "--as",
                            "user",
                            "--user",
                            "user-1",
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
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, replace(audit_context, proposal=user_target_proposal), run=run)

    persisted = store.get_agent_run(run.id)
    requeued = store.get_reply_task(task.id)
    assert result.result.error is not None
    assert result.result.error.code == "persisted_delivery_absent"
    assert persisted is not None and persisted.status == "failed"
    assert requeued is not None and requeued.status == "pending"
    assert requeued.execution_generation != task.execution_generation
    assert executor.commands == []


def test_persisted_single_direct_delivery_receipt_finishes_unknown_without_rerun(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    direct_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send direct result",
            "actions": [
                {
                    "description": "Send direct message",
                    "capability": "agent_cli.dws",
                    "operation": "chat +messages-send",
                    "target": {"open_dingtalk_id": "user-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "+messages-send",
                            "--open-dingtalk-id",
                            "user-1",
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
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "done")
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, replace(audit_context, proposal=direct_proposal), run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome is AuditOutcome.EXECUTED
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "confirmed"
    assert executor.commands == []


def test_persisted_chat_dm_to_recipient_finishes_unknown_without_rerun(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    direct_task = task.model_copy(update={"single_chat": True})
    direct_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send direct result",
            "actions": [
                {
                    "description": "Send direct message",
                    "capability": "agent_cli.dws",
                    "operation": "chat +dm",
                    "target": {"to": task.trigger_sender, "single_chat": True},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "+dm",
                            "--to",
                            task.trigger_sender,
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
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "done")
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(
        direct_task,
        replace(
            audit_context,
            task=replace(audit_context.task, single_chat=True),
            proposal=direct_proposal,
        ),
        run=run,
    )

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome is AuditOutcome.EXECUTED
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "confirmed"
    assert executor.commands == []


def test_legacy_direct_chat_without_delivery_record_rotates_generation(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    legacy_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send direct result",
            "actions": [
                {
                    "description": "Send direct message",
                    "capability": "dingtalk-chat",
                    "operation": "dws chat +messages-send --open-dingtalk-id user-1",
                    "target": {"recipient_open_dingtalk_id": "user-1"},
                    "payload": {"text": "done"},
                    "expected_verification": "Message exists",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "Requested by Derek",
        }
    )
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, replace(audit_context, proposal=legacy_proposal), run=run)

    persisted = store.get_agent_run(run.id)
    requeued = store.get_reply_task(task.id)
    assert result.result.error is not None
    assert result.result.error.code == "persisted_delivery_absent"
    assert persisted is not None and persisted.status == "failed"
    assert requeued is not None and requeued.status == "pending"
    assert requeued.execution_generation != task.execution_generation
    assert executor.commands == []


def test_controlled_group_chat_without_delivery_record_requires_audit_readback(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    group_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send group result",
            "actions": [
                {
                    "description": "Send group message",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"group": "group-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "+send-to-group",
                            "--group",
                            "group-1",
                            "--text",
                            "done",
                        ]
                    },
                    "expected_verification": "Message exists",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "Requested by Derek",
        }
    )
    with pytest.raises(ResultParseError):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(""),
        ).recover(task, replace(audit_context, proposal=group_proposal), run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"


def test_group_delivery_ledger_does_not_finish_direct_recovery(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    group_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Send group result",
            "actions": [
                {
                    "description": "Send group message",
                    "capability": "agent_cli.dws",
                    "operation": "chat message send",
                    "target": {"group": "group-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "chat",
                            "+send-to-group",
                            "--group",
                            "group-1",
                            "--text",
                            "done",
                        ]
                    },
                    "expected_verification": "Message exists",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "Requested by Derek",
        }
    )
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "done")
    with pytest.raises(ResultParseError):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(""),
        ).recover(task, replace(audit_context, proposal=group_proposal), run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"


def test_completed_recovery_action_overrides_older_absent_reconciliation(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_result_jsonl(
                "reconciled",
                operation_id=run.operation_id,
                session=run.codex_session_id,
                include_write=False,
                reconciliation=[
                    {
                        "action_index": 0,
                        "disposition": "absent",
                        "read_result_digest": "recovery-read-digest",
                    }
                ],
            )
        ),
    )
    runner.recover(task, audit_context, run=run)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    claim = store.claim_unknown_agent_run(run.id, owner="completed-recovery")
    assert claim.claimed
    expected = _expected_effect_action(
        audit_context.proposal.actions[0],
        McpToolEffectRegistry.default(),
        action_index=0,
    )
    metadata = {
        **expected,
        "effect": "effectful",
        "operation_id": run.operation_id,
        "action_index": 0,
        "native_cli": "dws",
        "result_digest": "completed-write-digest",
    }
    for event_type in ("item.started", "item.completed"):
        store.append_unknown_agent_run_event(
            run.id,
            {
                "type": event_type,
                "item": {
                    "type": "mcp_tool_call",
                    "id": "recovery-write",
                    "server": "agent_cli",
                    "tool": "execute_reviewed_write",
                    "status": "completed"
                    if event_type == "item.completed"
                    else "in_progress",
                    "metadata": metadata,
                },
            },
            owner="completed-recovery",
        )
    read_descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "dws",
                "chat",
                "message",
                "list",
                "--group",
                "cid-agent",
                "--time",
                "2026-08-06",
            ],
        }
    )
    assert read_descriptor is not None
    read_metadata = {
        "effect": "read_only",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_read",
        "capability": "agent_cli.dws",
        "operation": read_descriptor.command_path,
        "target_identifiers": read_descriptor.target_identifiers,
        "result_digest": "post-recovery-read-digest",
        "message_readback_window_matches": True,
        "message_text_digests": [expected["message_text_digest"]],
    }
    for event_type in ("item.started", "item.completed"):
        store.append_unknown_agent_run_event(
            run.id,
            {
                "type": event_type,
                "item": {
                    "type": "mcp_tool_call",
                    "id": "recovery-read-after-write",
                    "server": "agent_cli",
                    "tool": "execute_reviewed_read",
                    "status": (
                        "completed" if event_type == "item.completed" else "in_progress"
                    ),
                    "metadata": read_metadata,
                },
            },
            owner="completed-recovery",
        )
    store.persist_unknown_agent_run_result(
        run.id,
        json.loads(persisted.final_result_json),
        owner="completed-recovery",
        transcript_end_line=persisted.transcript_end_line,
    )

    executor = CapturingExecutor("")
    execute = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    )

    result = execute.execute_recovery(
        task, audit_context, run=store.get_agent_run(run.id)
    )

    assert result.result.outcome.value == "executed"
    assert result.result.side_effect_state.value == "confirmed"
    assert store.get_agent_run(run.id).status == "completed"
    assert executor.commands == []


def test_recovery_completes_verified_persisted_effects_without_model_reconciliation(
    setup,
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    claim = store.claim_unknown_agent_run(run.id, owner="verified-effect")
    assert claim.claimed
    expected = _expected_effect_action(
        audit_context.proposal.actions[0],
        McpToolEffectRegistry.default(),
        action_index=0,
    )
    write_metadata = {
        **expected,
        "effect": "effectful",
        "operation_id": run.operation_id,
        "action_index": 0,
        "native_cli": "dws",
    }
    for event_type in ("item.started", "item.completed"):
        store.append_unknown_agent_run_event(
            run.id,
            {
                "type": event_type,
                "item": {
                    "type": "mcp_tool_call",
                    "id": "verified-write",
                    "server": "agent_cli",
                    "tool": "execute_reviewed_write",
                    "status": "completed"
                    if event_type == "item.completed"
                    else "in_progress",
                    "metadata": write_metadata,
                },
            },
            owner="verified-effect",
        )
    read_descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "dws",
                "chat",
                "message",
                "list",
                "--group",
                "cid-agent",
                "--time",
                "2026-08-06",
            ],
        }
    )
    assert read_descriptor is not None
    read_metadata = {
        "effect": "read_only",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_read",
        "capability": "agent_cli.dws",
        "operation": read_descriptor.command_path,
        "target_identifiers": read_descriptor.target_identifiers,
        "result_digest": "verified-post-write-read",
        "message_readback_window_matches": True,
        "message_text_digests": [expected["message_text_digest"]],
    }
    for event_type in ("item.started", "item.completed"):
        store.append_unknown_agent_run_event(
            run.id,
            {
                "type": event_type,
                "item": {
                    "type": "mcp_tool_call",
                    "id": "verified-read",
                    "server": "agent_cli",
                    "tool": "execute_reviewed_read",
                    "status": "completed"
                    if event_type == "item.completed"
                    else "in_progress",
                    "metadata": read_metadata,
                },
            },
            owner="verified-effect",
        )
    store.persist_unknown_agent_run_result(
        run.id,
        {"outcome": "reconciled"},
        owner="verified-effect",
        transcript_end_line=run.transcript_end_line,
    )

    executor = CapturingExecutor("")
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=store.get_agent_run(run.id))

    assert result.result.outcome.value == "executed"
    assert result.result.side_effect_state.value == "confirmed"
    assert store.get_agent_run(run.id).status == "completed"
    assert executor.commands == []


def test_controlled_receipt_uses_command_digest_not_display_operation_name():
    action = {
        "capability": "agent_cli.dws",
        "operation": "chat message send",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"open-dingtalk-id": "user-1"},
        "operation_digest": "command-digest",
    }
    receipt = {
        **action,
        "operation": "chat +messages-send",
    }

    assert _metadata_matches_action(receipt, action)


def test_delivery_ledger_recognizes_current_text_message_command():
    metadata = {
        "effect": "effectful",
        "capability": "agent_cli.dws",
        "operation": "chat +messages-send",
        "target_identifiers": {"open-dingtalk-id": "user-1"},
    }
    argv = (
        "dws",
        "chat",
        "+messages-send",
        "--open-dingtalk-id",
        "user-1",
        "--text",
        "done",
        "--yes",
    )

    assert _is_dingtalk_chat_send_argv(metadata, argv)


def test_delivery_ledger_recognizes_quote_reply_with_conversation_identity():
    metadata = {
        "effect": "effectful",
        "capability": "agent_cli.dws",
        "operation": "chat message reply",
        "target_identifiers": {"conversation-id": "conversation-1"},
    }
    argv = (
        "dws",
        "chat",
        "message",
        "reply",
        "--conversation-id",
        "conversation-1",
        "--ref-msg-id",
        "message-1",
        "--text",
        "done",
        "--yes",
    )

    assert _is_dingtalk_chat_send_argv(metadata, argv)


def test_recovery_execution_completes_from_controlled_receipts_without_agent_json(
    setup,
):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    reconcile = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        include_read=True,
        include_write=False,
        reconciliation=[
            {
                "action_index": 0,
                "disposition": "absent",
                "read_result_digest": "recovery-read-digest",
            }
        ],
    )
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(reconcile),
    )
    runner.recover(task, audit_context, run=run)
    authorization = _recovery_authorizations(
        run,
        audit_context,
        frozenset({0}),
        McpToolEffectRegistry.default(),
    )[0]
    execution_without_agent_message = "\n".join(
        _audit_jsonl(
            run.operation_id,
            session=run.codex_session_id,
            authorization_id=authorization["authorization_id"],
        ).splitlines()[:-1]
    )
    runner.executor = CapturingExecutor(execution_without_agent_message)

    result = runner.execute_recovery(
        task,
        audit_context,
        run=store.get_agent_run(run.id),
    )

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "executed"
    assert persisted is not None and persisted.status == "completed"


def test_recovery_accepts_verified_controlled_read_receipt_with_large_stdout(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            read_stdout=json.dumps(
                {"messages": [{"text": str(index)} for index in range(2100)]}
            ),
            structured_read_receipt=True,
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None
    read_events = [
        event
        for event in persisted.tool_events
        if event["item"]["metadata"]["effect"] == "read_only"
    ]
    assert read_events[-1]["type"] == "item.completed"
    assert read_events[-1]["item"]["metadata"]["result_digest"] == (
        "recovery-read-digest"
    )


def test_ambiguous_recovery_becomes_needs_human_without_write(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            reconciliation=[
                {
                    "action_index": 0,
                    "disposition": "ambiguous",
                    "read_result_digest": "recovery-read-digest",
                }
            ],
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"
    assert (
        sum(
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
            for event in persisted.tool_events
        )
        == 1
    )


def test_recovery_readback_confirms_completed_write_without_receipt(setup):
    store, task, audit_context, parent = setup
    initial_lines = _audit_jsonl("operation-1", session="session-b").splitlines()
    with pytest.raises(RuntimeError, match="codex_process_failed"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor("\n".join(initial_lines[:3]), returncode=1),
        ).run(task, audit_context, turn_attempt=0, parent_agent_run_id=parent.id)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "unknown"
    assert store.list_agent_execution_receipts(run.id) == []

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_result_jsonl(
                "reconciled",
                operation_id=run.operation_id,
                session=run.codex_session_id,
            )
        ),
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.side_effect_state == "confirmed"
    assert len(store.list_agent_execution_receipts(run.id)) == 1


def test_legacy_dingtalk_chat_candidate_normalizes_for_reconciliation():
    action = ProposedAction.model_validate(
        {
            "description": "Ask for a missing fact",
            "capability": "dingtalk-chat",
            "operation": "dws chat message send",
            "target": {"openConversationId": "cid-agent"},
            "payload": {"group": "cid-agent", "text": "Please clarify."},
            "expected_verification": "Message exists",
        }
    )
    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )
    assert expected["capability"] == "agent_cli.dws"
    assert expected["operation"] == "chat +send-to-group"
    assert expected["target_identifiers"] == {"group": "cid-agent"}
    assert expected["reviewed_tool"] == "execute_reviewed_write"


def test_legacy_group_content_keeps_a_message_digest_for_reconciliation():
    action = ProposedAction.model_validate(
        {
            "description": "Post the reviewed conclusion",
            "capability": "dingtalk-chat",
            "operation": (
                'dws chat +send-to-group --group "cid-agent" --content <content> --yes'
            ),
            "target": {
                "conversation_id": "cid-agent",
                "recipient_type": "group",
            },
            "payload": {"content": "exact reviewed reply"},
            "expected_verification": "Message exists",
        }
    )

    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )

    assert expected["operation"] == "chat +send-to-group"
    assert expected["target_identifiers"] == {"group": "cid-agent"}
    assert (
        expected["message_text_digest"]
        == hashlib.sha256(b"exact reviewed reply").hexdigest()
    )


@pytest.mark.parametrize(
    "argv",
    (
        [
            "dws",
            "chat",
            "+messages-send",
            "--conversation-id",
            "cid-agent",
            "--markdown",
            "exact reviewed reply",
            "--yes",
        ],
        [
            "dws",
            "chat",
            "message",
            "send",
            "--group",
            "cid-agent",
            "--yes",
            "exact reviewed reply",
        ],
        [
            "dws",
            "chat",
            "message",
            "send",
            "--conversation-id",
            "cid-agent",
            "--ai-tag",
            "exact reviewed reply",
            "--yes",
        ],
    ),
)
def test_current_message_forms_keep_digest_and_canonical_path_for_reconciliation(argv):
    action = ProposedAction.model_validate(
        {
            "description": "Post the reviewed conclusion",
            "capability": "agent_cli.dws",
            "operation": "send the reviewed message",
            "target": {"conversation_id": "cid-agent"},
            "payload": {"argv": argv},
            "expected_verification": "Message exists",
        }
    )

    expected = _expected_effect_action(
        action,
        McpToolEffectRegistry.default(),
        action_index=0,
    )

    assert expected["operation"] in {
        "chat +messages-send",
        "chat message send",
    }
    assert expected["message_text_digest"] == hashlib.sha256(
        b"exact reviewed reply"
    ).hexdigest()
    assert expected["message_rendered_text_digest"]


def test_legacy_group_content_reconstructs_the_persisted_write_contract():
    action = ProposedAction.model_validate(
        {
            "description": "Post the reviewed conclusion",
            "capability": "dingtalk-chat",
            "operation": "send to the group",
            "target": {"conversation_id": "cid-agent"},
            "payload": {"content": "exact reviewed reply"},
            "expected_verification": "Message exists",
        }
    )

    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )

    argv = [
        "dws",
        "chat",
        "+send-to-group",
        "--group",
        "cid-agent",
        "--content",
        "exact reviewed reply",
        "--yes",
    ]
    descriptor = describe_native_command({"type": "command_execution", "argv": argv})
    assert descriptor is not None
    assert expected["operation"] == "chat +send-to-group"
    assert expected["operation_digest"] == descriptor.command_digest
    assert expected["arguments_digest"] == _json_digest({"argv": argv})


def test_legacy_direct_dingtalk_chat_candidate_normalizes_for_reconciliation():
    action = ProposedAction.model_validate(
        {
            "description": "Acknowledge receipt",
            "capability": "dingtalk-chat",
            "operation": "dws chat +messages-send --open-dingtalk-id recipient-1",
            "target": {
                "channel": "dingtalk",
                "recipient_open_dingtalk_id": "recipient-1",
                "single_chat": True,
            },
            "payload": {"text": "Received."},
            "expected_verification": "Message exists",
        }
    )

    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )

    assert expected["capability"] == "agent_cli.dws"
    assert expected["operation"] == "chat +messages-send"
    assert expected["operation_contract_valid"] is True
    assert expected["target_identifiers"] == {"open-dingtalk-id": "recipient-1"}
    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat +chat-messages",
            "target_identifiers": {"open-dingtalk-id": "recipient-1"},
        },
        expected,
        McpToolEffectRegistry.default(),
    )


def test_named_direct_message_uses_proposal_recipient_only_for_readback():
    action = ProposedAction.model_validate(
        {
            "description": "Send the prepared notes",
            "capability": "dws chat",
            "operation": "+dm",
            "target": {
                "recipient_display_name": "Mina",
                "recipient_open_dingtalk_id": "recipient-1",
                "single_chat": True,
            },
            "payload": {
                "argv": [
                    "dws",
                    "chat",
                    "+dm",
                    "--to",
                    "Mina",
                    "--text",
                    "done",
                    "--yes",
                ]
            },
            "expected_verification": "Message exists",
        }
    )

    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )

    assert expected["target_identifiers"] == {}
    assert expected["readback_target_identifiers"] == {
        "open-dingtalk-id": "recipient-1"
    }
    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat +chat-messages",
            "target_identifiers": {"open-dingtalk-id": "recipient-1"},
        },
        expected,
        McpToolEffectRegistry.default(),
    )


def test_named_direct_message_accepts_legacy_recipient_user_id_for_readback():
    action = ProposedAction.model_validate(
        {
            "description": "Send the prepared notes",
            "capability": "dws chat",
            "operation": "+dm",
            "target": {
                "recipient_name": "Mina",
                "recipient_user_id": "user-1",
                "single_chat": True,
            },
            "payload": {
                "argv": [
                    "dws",
                    "chat",
                    "+dm",
                    "--to",
                    "Mina",
                    "--text",
                    "done",
                    "--yes",
                ]
            },
            "expected_verification": "Message exists",
        }
    )

    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )

    assert expected["readback_target_identifiers"] == {"user": "user-1"}
    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat +chat-messages",
            "target_identifiers": {"user": "user-1"},
        },
        expected,
        McpToolEffectRegistry.default(),
    )


def test_native_action_uses_argv_digest_matching_persisted_receipt():
    action = ProposedAction.model_validate(
        {
            "description": "Reply in the group",
            "capability": "dingtalk-chat",
            "operation": "chat message reply",
            "target": {"conversation_id": "conversation-1", "ref_msg_id": "message-1"},
            "payload": {
                "argv": [
                    "dws",
                    "chat",
                    "message",
                    "reply",
                    "--conversation-id",
                    "conversation-1",
                    "--ref-msg-id",
                    "message-1",
                    "--text",
                    "done",
                    "--yes",
                ],
                "text": "done",
            },
            "expected_verification": "Message exists",
        }
    )
    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )

    assert expected["arguments_digest"] == _json_digest(
        {"argv": action.payload["argv"]}
    )


def test_named_direct_message_readback_keeps_the_completed_write_receipt():
    action = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat +dm",
        "capability": "agent_cli.dws",
        "arguments_digest": "write-1",
        "target_identifiers": {},
        "readback_target_identifiers": {"open-dingtalk-id": "recipient-1"},
    }
    events = [
        {
            "type": "item.completed",
            "item": {"metadata": {**action, "readback_target_identifiers": None}},
        },
        {
            "type": "item.completed",
            "item": {
                "metadata": {
                    "effect": "read_only",
                    "reviewed_server": "agent_cli",
                    "reviewed_tool": "execute_reviewed_read",
                    "operation": "chat +chat-messages",
                    "target_identifiers": {"open-dingtalk-id": "recipient-1"},
                    "result_digest": "read-1",
                }
            },
        },
    ]

    assert _actions_have_required_readbacks(
        events, (action,), McpToolEffectRegistry.default()
    )


def test_named_direct_message_readback_rejects_duplicate_write_events():
    action = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat +dm",
        "capability": "agent_cli.dws",
        "arguments_digest": "write-1",
        "target_identifiers": {},
        "readback_target_identifiers": {"open-dingtalk-id": "recipient-1"},
    }
    events = [
        {
            "type": event_type,
            "item": {
                "id": f"write-{index}",
                "metadata": {
                    **action,
                    "effect": "effectful",
                    "operation_id": "proposal-1",
                },
            },
        }
        for index in range(2)
        for event_type in ("item.started", "item.completed")
    ] + [
        {
            "type": "item.completed",
            "item": {
                "metadata": {
                    "effect": "read_only",
                    "reviewed_server": "agent_cli",
                    "reviewed_tool": "execute_reviewed_read",
                    "operation": "chat +chat-messages",
                    "target_identifiers": {"open-dingtalk-id": "recipient-1"},
                    "result_digest": "read-1",
                }
            },
        }
    ]

    completed, all_closed = _action_completion_accounting(
        events,
        (),
        (action,),
        operation_id="proposal-1",
        registry=McpToolEffectRegistry.default(),
    )

    assert completed == set()
    assert not all_closed


def test_identical_legacy_completion_replay_counts_as_one_external_write():
    action = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat +send-to-group",
        "capability": "agent_cli.dws",
        "operation_digest": "operation-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"group": "cid-agent"},
    }
    events = [
        {
            "type": event_type,
            "item": {
                "id": f"write-{index}",
                "metadata": {
                    **action,
                    "effect": "effectful",
                    "operation_id": "proposal-1",
                    "authorization_id": "proposal-1:initial:0",
                    "result_digest": "same-durable-result",
                },
            },
        }
        for index in range(2)
        for event_type in ("item.started", "item.completed")
    ]

    completed, all_closed = _action_completion_accounting(
        events,
        (),
        (action,),
        operation_id="proposal-1",
        registry=McpToolEffectRegistry.default(),
    )

    assert completed == {0}
    assert all_closed


def test_completed_event_and_matching_durable_receipt_count_once():
    action = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "todo +remind",
        "capability": "agent_cli.dws",
        "operation_digest": "operation-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {},
    }
    receipt = AgentExecutionReceipt(
        id=1,
        agent_run_id=3797,
        receipt_id="receipt-1",
        operation_id=_action_receipt_operation_id("proposal-1", action, 0),
        cli="dws",
        command_path="todo +remind",
        command_digest="operation-digest",
        exit_code=0,
        completed=True,
        persisted=True,
        safe_to_confirm=True,
        created_at="2026-08-21 18:32:31",
    )
    events = [
        {
            "type": event_type,
            "item": {
                "id": "write-1",
                "metadata": {
                    **action,
                    "effect": "effectful",
                    "operation_id": "proposal-1",
                    "action_index": 0,
                    "result_digest": "write-result",
                },
            },
        }
        for event_type in ("item.started", "item.completed")
    ]

    completed, all_closed = _action_completion_accounting(
        events,
        [receipt],
        (action,),
        operation_id="proposal-1",
        registry=McpToolEffectRegistry.default(),
    )

    assert completed == {0}
    assert all_closed


def test_native_command_contract_uses_parsed_cli_not_consumer_label():
    action = ProposedAction.model_validate(
        {
            "description": "Acknowledge the calendar invitation",
            "capability": "dws",
            "operation": "send a direct acknowledgement",
            "target": {"recipient_open_dingtalk_id": "recipient-1"},
            "payload": {
                "argv": [
                    "dws",
                    "chat",
                    "+messages-send",
                    "--open-dingtalk-id",
                    "recipient-1",
                    "--text",
                    "I will attend.",
                    "--yes",
                    "--format",
                    "json",
                ]
            },
            "expected_verification": "Message exists",
        }
    )

    expected = _expected_effect_action(
        action, McpToolEffectRegistry.default(), action_index=0
    )

    assert expected["capability"] == "agent_cli.dws"
    assert expected["operation"] == "chat +messages-send"
    assert expected["operation_contract_valid"] is True
    assert expected["target_identifiers"] == {"open-dingtalk-id": "recipient-1"}


def test_native_read_command_is_not_a_valid_write_operation_contract():
    action = ProposedAction.model_validate(
        {
            "description": "Inspect the DWS schema",
            "capability": "dws",
            "operation": "schema",
            "target": {"scope": "schema"},
            "payload": {"argv": ["dws", "schema", "--yes"]},
            "expected_verification": "Schema was inspected",
        }
    )

    assert not _proposed_operation_contract_valid(
        action, McpToolEffectRegistry.default()
    )


def test_native_lark_read_command_is_not_a_valid_write_operation_contract(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        audit_agent_module,
        "_OPERATION_CONTRACT_CLASSIFIER",
        NativeCliMetadataClassifier(
            reviewed_effects={
                ("lark-cli", "drive list"): EffectKind.READ_ONLY,
            }
        ),
    )
    action = ProposedAction.model_validate(
        {
            "description": "List drive files",
            "capability": "lark-cli",
            "operation": "drive list",
            "target": {"folder": "root"},
            "payload": {"argv": ["lark-cli", "drive", "list"]},
            "expected_verification": "Files were listed",
        }
    )

    assert not _proposed_operation_contract_valid(
        action, McpToolEffectRegistry.default()
    )


def test_unclassified_native_command_is_not_a_valid_write_operation_contract(
    monkeypatch: pytest.MonkeyPatch,
):
    class Unclassified:
        @staticmethod
        def classify(_item):
            return None

    monkeypatch.setattr(
        audit_agent_module,
        "_OPERATION_CONTRACT_CLASSIFIER",
        Unclassified(),
    )
    action = ProposedAction.model_validate(
        {
            "description": "Run an unknown command",
            "capability": "dws",
            "operation": "unknown command",
            "target": {"scope": "unknown"},
            "payload": {"argv": ["dws", "unknown", "command", "--yes"]},
            "expected_verification": "Command completed",
        }
    )

    assert not _proposed_operation_contract_valid(
        action, McpToolEffectRegistry.default()
    )


def test_registered_reaction_native_command_is_a_valid_write_operation_contract():
    action = ProposedAction.model_validate(
        {
            "description": "Add a useful acknowledgment reaction",
            "capability": "agent_cli.dws",
            "operation": "chat message reaction add",
            "target": {"message_id": "msg-1"},
            "payload": {
                "argv": [
                    "dws",
                    "chat",
                    "message",
                    "reaction",
                    "add",
                    "--message-id",
                    "msg-1",
                    "--emoji",
                    "👍",
                    "--yes",
                ]
            },
            "expected_verification": "The reaction appears in the message reactions.",
        }
    )

    assert _proposed_operation_contract_valid(
        action, McpToolEffectRegistry.default()
    )


def test_initial_and_recovery_authorizations_share_one_logical_effect_token(setup):
    _, _, audit_context, run = _seed_crashed_audit_write(setup)
    expected = (
        _expected_effect_action(
            audit_context.proposal.actions[0],
            McpToolEffectRegistry.default(),
            action_index=0,
        ),
    )

    [initial] = _initial_write_authorizations(run, expected)
    [recovery] = _recovery_authorizations(
        run,
        audit_context,
        frozenset({0}),
        McpToolEffectRegistry.default(),
    )

    assert initial["receipt_operation_id"] == recovery["receipt_operation_id"]
    assert initial["authorization_id"] == recovery["authorization_id"]


def test_chat_id_readback_matches_conversation_id():
    registry = McpToolEffectRegistry.default()

    assert registry.readback_targets_match(
        read_server="agent_cli",
        read_tool="execute_reviewed_read",
        write_server="agent_cli",
        write_tool="execute_reviewed_write",
        read_targets={"conversation-id": "cid-1"},
        write_targets={"chat-id": "cid-1"},
    )


def test_created_resource_readback_can_use_the_returned_resource_id():
    registry = McpToolEffectRegistry.default()

    assert registry.readback_targets_match(
        read_server="agent_cli",
        read_tool="execute_reviewed_read",
        write_server="agent_cli",
        write_tool="execute_reviewed_write",
        read_operation="todo +get",
        write_operation="todo +remind",
        read_targets={"task-id": "56500076541"},
        write_targets={},
    )
    assert not registry.readback_targets_match(
        read_server="agent_cli",
        read_tool="execute_reviewed_read",
        write_server="agent_cli",
        write_tool="execute_reviewed_write",
        read_operation="chat +chat-messages",
        write_operation="chat +dm",
        read_targets={"task-id": "56500076541"},
        write_targets={},
    )


def test_created_resource_readback_requires_a_completed_write_event():
    action = {
        "capability": "agent_cli.dws",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "todo +remind",
        "target_identifiers": {},
    }
    read = {
        "type": "item.completed",
        "item": {
            "metadata": {
                "effect": "read_only",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation": "todo +get",
                "target_identifiers": {"task-id": "unrelated-task"},
                "result_digest": "read-digest",
            }
        },
    }

    assert not _actions_have_required_readbacks(
        [read], (action,), McpToolEffectRegistry.default()
    )


def test_ambiguous_recovery_requires_matching_live_read(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
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

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert persisted.status == "unknown"
    assert persisted.reconciliation_next_attempt_at > persisted.updated_at


def test_matching_live_read_without_structured_disposition_does_not_confirm(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            reconciliation=[],
        )
    )

    with pytest.raises(RuntimeError, match="audit_recovery_evidence_missing"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).recover(task, audit_context, run=run)


def test_reconciliation_uses_content_proof_when_model_cites_a_wrong_digest(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            reconciliation=[
                {
                    "action_index": 0,
                    "disposition": "present",
                    "read_result_digest": "wrong-read-digest",
                }
            ],
        )
    )

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    assert result.result.outcome is AuditOutcome.RECONCILED
    assert result.result.reconciliation[0].disposition.value == "present"
    assert result.result.reconciliation[0].read_result_digest == "recovery-read-digest"


def test_target_scoped_failed_read_can_only_prove_ambiguous_reconciliation():
    action = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "oa approval approve",
        "target_identifiers": {"user": "applicant-1"},
    }
    failed_read = {
        "type": "item.failed",
        "item": {
            "metadata": {
                "effect": "read_only",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation": "oa approval detail",
                "target_identifiers": {"user": "applicant-1"},
                "result_digest": "failed-read-digest",
            }
        },
    }
    ambiguous = AuditReconciliation(
        action_index=0,
        disposition="ambiguous",
        read_result_digest="failed-read-digest",
    )

    validated = _validated_reconciliation(
        (ambiguous,),
        [failed_read],
        (action,),
        event_start=0,
        registry=McpToolEffectRegistry.default(),
    )

    assert validated == {0: ambiguous}
    with pytest.raises(RuntimeError, match="audit_reconciliation_evidence_mismatch"):
        _validated_reconciliation(
            (
                AuditReconciliation(
                    action_index=0,
                    disposition="absent",
                    read_result_digest="failed-read-digest",
                ),
            ),
            [failed_read],
            (action,),
            event_start=0,
            registry=McpToolEffectRegistry.default(),
        )


def test_group_message_content_match_promotes_reconciliation_to_present():
    text_digest = hashlib.sha256(b"exact reviewed reply").hexdigest()
    action = {
        "capability": "agent_cli.dws",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat message send",
        "target_identifiers": {"group": "cid-agent"},
        "message_text_digest": text_digest,
    }
    read = {
        "type": "item.completed",
        "item": {
            "metadata": {
                "effect": "read_only",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation": "chat message list",
                "target_identifiers": {"group": "cid-agent"},
                "result_digest": "read-digest",
                "message_readback_complete": True,
                "message_readback_window_matches": True,
                "message_text_digests": [text_digest],
            }
        },
    }

    validated = _validated_reconciliation(
        (
            AuditReconciliation(
                action_index=0,
                disposition="ambiguous",
                read_result_digest="read-digest",
            ),
        ),
        [read],
        (action,),
        event_start=0,
        registry=McpToolEffectRegistry.default(),
    )

    assert validated[0].disposition.value == "present"


def test_group_message_without_content_match_cannot_be_claimed_present():
    action = {
        "capability": "agent_cli.dws",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat message send",
        "target_identifiers": {"group": "cid-agent"},
        "message_text_digest": hashlib.sha256(b"exact reviewed reply").hexdigest(),
    }
    read = {
        "type": "item.completed",
        "item": {
            "metadata": {
                "effect": "read_only",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation": "chat message list",
                "target_identifiers": {"group": "cid-agent"},
                "result_digest": "read-digest",
                "message_readback_complete": True,
                "message_readback_window_matches": True,
                "message_text_digests": [
                    hashlib.sha256(b"different reply").hexdigest()
                ],
            }
        },
    }

    validated = _validated_reconciliation(
        (
            AuditReconciliation(
                action_index=0,
                disposition="present",
                read_result_digest="read-digest",
            ),
        ),
        [read],
        (action,),
        event_start=0,
        registry=McpToolEffectRegistry.default(),
    )

    assert validated[0].disposition.value == "ambiguous"


def test_group_message_match_outside_operation_window_stays_ambiguous():
    text_digest = hashlib.sha256(b"exact reviewed reply").hexdigest()
    action = {
        "capability": "agent_cli.dws",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat message send",
        "target_identifiers": {"group": "cid-agent"},
        "message_text_digest": text_digest,
    }
    read = {
        "type": "item.completed",
        "item": {
            "metadata": {
                "effect": "read_only",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation": "chat message list",
                "target_identifiers": {"group": "cid-agent"},
                "result_digest": "read-digest",
                "message_readback_complete": True,
                "message_readback_window_matches": False,
                "message_text_digests": [text_digest],
            }
        },
    }

    validated = _validated_reconciliation(
        (
            AuditReconciliation(
                action_index=0,
                disposition="present",
                read_result_digest="read-digest",
            ),
        ),
        [read],
        (action,),
        event_start=0,
        registry=McpToolEffectRegistry.default(),
    )

    assert validated[0].disposition.value == "ambiguous"


def test_group_message_markdown_rendering_match_promotes_to_present():
    approved = "结论：`unknown` 只读核对\n下一步：继续观察"
    rendered = "结论：**unknown** 只读核对 下一步：继续观察"
    action = {
        "capability": "agent_cli.dws",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat +send-to-group",
        "target_identifiers": {"group": "cid-agent"},
        "message_text_digest": hashlib.sha256(approved.encode()).hexdigest(),
        "message_rendered_text_digest": _message_rendered_text_digest(approved),
    }
    proof = _dingtalk_message_readback_proof(
        {
            "stdout": json.dumps(
                {
                    "messages": [
                        {
                            "text": rendered,
                            "messageId": "message-1",
                            "conversationId": "cid-agent",
                        }
                    ],
                    "complete": True,
                    "hasMore": False,
                    "paginationKnown": True,
                    "failures": [],
                    "queryRange": {
                        "startTime": "2026-08-21T08:00:00Z",
                        "endTime": "2026-08-21T10:00:00Z",
                    },
                }
            )
        },
        native_cli="dws",
        operation="chat +chat-messages",
        expected_message_text_digests=frozenset({str(action["message_text_digest"])}),
        expected_message_rendered_text_digests=frozenset(
            {str(action["message_rendered_text_digest"])}
        ),
        operation_started_at="2026-08-21T09:00:00Z",
    )
    read = {
        "type": "item.completed",
        "item": {
            "metadata": {
                "effect": "read_only",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation": "chat +chat-messages",
                "target_identifiers": {"group": "cid-agent"},
                "result_digest": "read-digest",
                **proof,
            }
        },
    }

    validated = _validated_reconciliation(
        (
            AuditReconciliation(
                action_index=0,
                disposition="ambiguous",
                read_result_digest="read-digest",
            ),
        ),
        [read],
        (action,),
        event_start=0,
        registry=McpToolEffectRegistry.default(),
    )

    assert proof["message_text_digests"] == []
    assert proof["message_rendered_text_digests"] == [
        action["message_rendered_text_digest"]
    ]
    assert validated[0].disposition.value == "present"


def test_markdown_rendered_digest_preserves_word_boundaries():
    assert _message_rendered_text_digest("alpha beta") != (
        _message_rendered_text_digest("alphabeta")
    )


def test_target_only_dingtalk_readback_does_not_close_message_write():
    action = {
        "capability": "agent_cli.dws",
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "chat +send-to-group",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"group": "cid-agent"},
        "message_text_digest": hashlib.sha256(b"approved body").hexdigest(),
    }
    events = [
        {
            "type": "item.completed",
            "item": {
                "metadata": {
                    "effect": "read_only",
                    "reviewed_server": "agent_cli",
                    "reviewed_tool": "execute_reviewed_read",
                    "operation": "chat +chat-messages",
                    "target_identifiers": {"group": "cid-agent"},
                    "result_digest": "read-digest",
                    "message_readback_window_matches": True,
                    "message_text_digests": [],
                }
            },
        }
    ]

    assert not _actions_have_required_readbacks(
        events, (action,), McpToolEffectRegistry.default()
    )


def test_reconciliation_accepts_repeated_matching_readbacks(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    first = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
    ).splitlines()
    second = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
    ).splitlines()
    final = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        include_read=False,
        reconciliation=[
            {
                "action_index": 0,
                "disposition": "present",
                "read_result_digest": "recovery-read-digest",
            }
        ],
    ).splitlines()
    executor = CapturingExecutor("\n".join(first[:-1] + second[1:-1] + final[-1:]))

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    result = AuditAgentResult.model_validate_json(persisted.final_result_json)
    assert result.reconciliation[0].read_result_digest == "recovery-read-digest"


def test_reconciliation_accepts_any_cited_matching_readback_from_current_turn(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    first = (
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
        )
        .replace("recovery-read-digest", "first-matching-read-digest")
        .splitlines()
    )
    second = (
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
        )
        .replace("recovery-read-digest", "second-matching-read-digest")
        .splitlines()
    )
    final = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        include_read=False,
        reconciliation=[
            {
                "action_index": 0,
                "disposition": "present",
                "read_result_digest": "first-matching-read-digest",
            }
        ],
    ).splitlines()
    executor = CapturingExecutor("\n".join(first[:-1] + second[1:-1] + final[-1:]))

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    result = AuditAgentResult.model_validate_json(persisted.final_result_json)
    assert result.reconciliation[0].read_result_digest == "first-matching-read-digest"


def test_unrelated_read_cannot_authorize_recovery_write(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    executor = CapturingExecutor(
        _audit_result_jsonl(
            "reconciled",
            operation_id=run.operation_id,
            session=run.codex_session_id,
            include_write=True,
            read_target="cid-unrelated",
        )
    )

    with pytest.raises(AgentReadOnlyViolationError, match="agent_write_forbidden"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).recover(task, audit_context, run=run)


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
    assert run is not None and run.status == "unknown"
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


def test_direct_mcp_readback_relation_confirms_unknown_write_without_replay(setup):
    store, task, context, run, registry = _seed_crashed_xiaoqing_write(setup)
    recovery = CapturingExecutor(_xiaoqing_recovery_jsonl(run, include_read=True))

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=recovery,
        mcp_effect_registry=registry,
    ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert (
        sum(
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
            for event in persisted.tool_events
        )
        == 1
    )


def test_direct_mcp_absent_recovery_rotates_generation_without_write(setup):
    store, task, context, run, registry = _seed_crashed_xiaoqing_write(setup)
    readback = _xiaoqing_recovery_jsonl(run, include_read=True, disposition="absent")
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(readback),
        mcp_effect_registry=registry,
    )
    runner.recover(task, context, run=run)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None

    class NoWriteExecutor:
        def __call__(self, *args, **kwargs):
            raise AssertionError("direct MCP recovery must not execute")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=NoWriteExecutor(),
        mcp_effect_registry=registry,
    ).execute_recovery(task, context, run=persisted)

    persisted = store.get_agent_run(run.id)
    requeued = store.get_reply_task(task.id)
    assert result.result.outcome.value == "failed"
    assert result.result.error.code == "audit_recovery_candidate_invalid"
    assert persisted is not None and persisted.status == "failed"
    assert requeued is not None and requeued.status == "pending"
    assert requeued.execution_generation != task.execution_generation
    assert (
        sum(
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
            for event in persisted.tool_events
        )
        == 1
    )


def test_readback_capable_receipt_without_live_read_stays_unknown(setup):
    store, task, context, run, registry = _seed_crashed_xiaoqing_write(setup)

    with pytest.raises(RuntimeError, match="audit_recovery_evidence_missing"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=ExactReceiptExecutor(
                _xiaoqing_recovery_jsonl(run, include_read=False),
                store=store,
                run=run,
            ),
            owner="audit-owner",
            mcp_effect_registry=registry,
        ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"
    assert persisted.lease_owner == ""
    assert persisted.lease_expires_at == ""


def test_readback_capable_receipt_with_matching_live_read_confirms(setup):
    store, task, context, run, registry = _seed_crashed_xiaoqing_write(setup)

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=ExactReceiptExecutor(
            _xiaoqing_recovery_jsonl(run, include_read=True),
            store=store,
            run=run,
        ),
        owner="audit-owner",
        mcp_effect_registry=registry,
    ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "confirmed"


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


def test_controlled_cli_readback_matches_shared_oa_instance_target():
    registry = McpToolEffectRegistry.default()

    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "oa approval tasks",
            "target_identifiers": {"instance-id": "process-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "oa approval approve",
            "target_identifiers": {
                "instance-id": "process-1",
                "task-id": "task-1",
            },
        },
        registry,
    )


def test_effect_registry_requires_registered_inner_cli_operation_relation():
    registry = McpToolEffectRegistry.default()
    relation = {
        "read_server": "agent_cli",
        "read_tool": "execute_reviewed_read",
        "write_server": "agent_cli",
        "write_tool": "execute_reviewed_write",
        "write_operation": "chat message send",
    }

    assert registry.readback_operations_match(
        **relation,
        read_operation="chat message list",
    )
    assert not registry.readback_operations_match(
        **relation,
        read_operation="chat member list",
    )
    assert registry.has_readback_for(
        write_server="agent_cli",
        write_tool="execute_reviewed_write",
        write_operation="unregistered write operation",
    )


def test_effect_registry_accepts_registered_direct_message_readback():
    registry = McpToolEffectRegistry.default()

    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat +chat-messages",
            "target_identifiers": {"open-dingtalk-id": "recipient-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat +messages-send",
            "target_identifiers": {"open-dingtalk-id": "recipient-1"},
        },
        registry,
    )

    assert not _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat +chat-messages",
            "target_identifiers": {"open-dingtalk-id": "recipient-2"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat +messages-send",
            "target_identifiers": {"open-dingtalk-id": "recipient-1"},
        },
        registry,
    )


@pytest.mark.parametrize("operation", ["chat +chat-messages", "chat +search-msg"])
def test_effect_registry_accepts_registered_group_send_readback(operation):
    registry = McpToolEffectRegistry.default()

    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": operation,
            "target_identifiers": {"group": "group-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat +send-to-group",
            "target_identifiers": {"group": "group-1"},
        },
        registry,
    )


def test_effect_registry_accepts_document_move_info_readback():
    registry = McpToolEffectRegistry.default()

    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "doc info",
            "target_identifiers": {"node": "node-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "doc +move",
            "target_identifiers": {"node": "node-1"},
        },
        registry,
    )


def test_effect_registry_accepts_registered_reply_and_todo_readbacks():
    registry = McpToolEffectRegistry.default()

    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat +chat-messages",
            "target_identifiers": {"conversation-id": "conversation-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat +messages-reply",
            "target_identifiers": {
                "conversation-id": "conversation-1",
                "message-id": "message-1",
            },
        },
        registry,
    )
    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat +chat-messages",
            "target_identifiers": {"conversation-id": "conversation-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat message reply",
            "target_identifiers": {
                "conversation-id": "conversation-1",
                "message-id": "message-1",
            },
        },
        registry,
    )
    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "oa approval detail",
            "target_identifiers": {"instance-id": "instance-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "oa approval oa-comments",
            "target_identifiers": {"instance-id": "instance-1"},
        },
        registry,
    )
    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "todo task get",
            "target_identifiers": {"task-id": "todo-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "todo task update",
            "target_identifiers": {"task-id": "todo-1"},
        },
        registry,
    )


def test_controlled_cli_mail_verify_reads_back_reply_for_same_mailbox():
    registry = McpToolEffectRegistry.default()
    write = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "mail message reply",
        "target_identifiers": {
            "from": "principal@example.test",
            "id": "mail-1",
        },
        "result_identifiers": {"stdout.internetMessageId": "internet-1"},
    }
    read = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_read",
        "operation": "mail message verify",
        "target_identifiers": {
            "email": "principal@example.test",
            "internet-message-id": "internet-1",
        },
        "result_identifiers": {
            "stdout.internetMessageId": "internet-1",
            "stdout.sendStatus": "SUCCESS",
        },
    }

    assert registry.readback_operations_match(
        read_server="agent_cli",
        read_tool="execute_reviewed_read",
        write_server="agent_cli",
        write_tool="execute_reviewed_write",
        read_operation="mail message verify",
        write_operation="mail message reply",
    )
    assert _read_matches_action(read, write, registry)

    mismatched_read = {
        **read,
        "target_identifiers": {
            "email": "principal@example.test",
            "internet-message-id": "internet-2",
        },
        "result_identifiers": {
            "stdout.internetMessageId": "internet-2",
            "stdout.sendStatus": "SUCCESS",
        },
    }
    assert not _read_matches_action(mismatched_read, write, registry)


@pytest.mark.parametrize(
    "send_status",
    ("failed", "posting", "partial_success", "unknown", None),
)
def test_controlled_cli_mail_verify_requires_success_delivery_state(send_status):
    registry = McpToolEffectRegistry.default()
    write = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
        "operation": "mail message reply",
        "target_identifiers": {
            "from": "principal@example.test",
            "id": "mail-1",
        },
        "result_identifiers": {"stdout.internetMessageId": "internet-1"},
    }
    result_identifiers = {"stdout.internetMessageId": "internet-1"}
    if send_status is not None:
        result_identifiers["stdout.sendStatus"] = send_status
    read = {
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_read",
        "operation": "mail message verify",
        "target_identifiers": {
            "email": "principal@example.test",
            "internet-message-id": "internet-1",
        },
        "result_identifiers": result_identifiers,
    }

    assert not _read_matches_action(read, write, registry)


def test_unregistered_controlled_write_cannot_confirm_without_readback(setup):
    store, task, audit_context, parent = setup
    relation_key = (
        "agent_cli",
        "execute_reviewed_read",
        "agent_cli",
        "execute_reviewed_write",
    )
    registry = McpToolEffectRegistry(
        {
            ("agent_cli", "read_skill"): EffectKind.READ_ONLY,
            ("agent_cli", "execute_reviewed_read"): EffectKind.READ_ONLY,
            ("agent_cli", "execute_reviewed_write"): EffectKind.EFFECTFUL,
        },
        readbacks={
            ("agent_cli", "execute_reviewed_read"): {
                ("agent_cli", "execute_reviewed_write")
            }
        },
        readback_operation_modes={relation_key: "registered"},
        readback_operation_relations={
            relation_key: {("oa approval detail", "oa approval approve")}
        },
    )

    with pytest.raises(RuntimeError, match="audit_external_readback_missing"):
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
            mcp_effect_registry=registry,
        ).run(
            task,
            audit_context,
            turn_attempt=0,
            parent_agent_run_id=parent.id,
        )


def test_service_owned_oa_detail_readback_matches_approval_comment_target():
    descriptor = describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                str(central_python()),
                "-m",
                "app.cli",
                "read-oa-approval-detail",
                "--instance-id",
                "process-1",
            ],
        }
    )

    assert descriptor is not None
    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": descriptor.command_path,
            "target_identifiers": descriptor.target_identifiers,
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "oa approval comment",
            "target_identifiers": {"instance-id": "process-1"},
        },
        McpToolEffectRegistry.default(),
    )


def test_controlled_cli_readback_rejects_conflicting_shared_target():
    registry = McpToolEffectRegistry.default()

    assert not _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "oa approval tasks",
            "target_identifiers": {"instance-id": "process-2"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "oa approval approve",
            "target_identifiers": {
                "instance-id": "process-1",
                "task-id": "task-1",
            },
        },
        registry,
    )


@pytest.mark.parametrize(
    "write_target_key", ["conversation-id", "open-conversation-id"]
)
def test_controlled_cli_readback_matches_conversation_target_aliases(
    write_target_key,
):
    registry = McpToolEffectRegistry.default()

    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat message list",
            "target_identifiers": {"group": "conversation-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat message send",
            "target_identifiers": {write_target_key: "conversation-1"},
        },
        registry,
    )


def test_controlled_cli_group_list_readback_matches_group_reply():
    registry = McpToolEffectRegistry.default()

    assert _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat message list",
            "target_identifiers": {"group": "conversation-1"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat message reply",
            "target_identifiers": {
                "conversation-id": "conversation-1",
                "ref-msg-id": "message-1",
            },
        },
        registry,
    )


def test_controlled_cli_readback_does_not_alias_unrelated_target_names():
    registry = McpToolEffectRegistry.default()

    assert not _read_matches_action(
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_read",
            "operation": "chat message list",
            "target_identifiers": {"group": "same-value"},
        },
        {
            "reviewed_server": "agent_cli",
            "reviewed_tool": "execute_reviewed_write",
            "operation": "chat message send",
            "target_identifiers": {"open-dingtalk-id": "same-value"},
        },
        registry,
    )


def test_action_receipt_identity_separates_same_payload_across_actions(setup):
    store, task, _context, parent = setup
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id="proposal-operation",
        owner="audit-owner",
    ).run
    first = {
        "capability": "server-a",
        "operation": "write-a",
        "operation_digest": "digest-a",
        "arguments_digest": "same-arguments",
    }
    second = dict(first)

    for action_index, action in enumerate((first, second)):
        store.record_agent_execution_receipt(
            run.id,
            receipt_id=f"receipt-{action_index}",
            operation_id=_action_receipt_operation_id(
                run.operation_id, action, action_index
            ),
            cli=action["capability"],
            command_path=action["operation"],
            command_digest=action["operation_digest"],
            exit_code=0,
            owner="audit-owner",
        )

    receipts = store.list_agent_execution_receipts(run.id)
    assert len(receipts) == 2
    assert receipts[0].operation_id != receipts[1].operation_id


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
            "reconciled",
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
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"
    assert (
        sum(
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
            for event in persisted.tool_events
        )
        == 1
    )
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

    with pytest.raises(AgentReadOnlyViolationError, match="agent_write_forbidden"):
        AuditAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor("\n".join(recovery_lines)),
            mcp_effect_registry=registry,
        ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    assert (
        sum(
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
            for event in persisted.tool_events
        )
        == 1
    )


def test_exact_receipt_confirms_no_readback_unknown(setup):
    store, task, context, run, registry, _ = _seed_crashed_memory_write(setup)

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=ExactReceiptExecutor(
            _audit_result_jsonl(
                "reconciled",
                operation_id=run.operation_id,
                session=run.codex_session_id,
                include_read=False,
            ),
            store=store,
            run=run,
        ),
        owner="audit-owner",
        mcp_effect_registry=registry,
    ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert result.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.side_effect_state == "confirmed"
    assert (
        sum(
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
            for event in persisted.tool_events
        )
        == 1
    )


def test_pre_903_no_readback_start_binds_before_exact_receipt(setup):
    store, task, context, run, registry, _ = _seed_crashed_memory_write(setup)
    with sqlite3.connect(store.path) as db:
        row = db.execute(
            "select id, event_json from agent_run_events "
            "where agent_run_id=? and event_type='item.started'",
            (run.id,),
        ).fetchone()
        event = json.loads(row[1])
        event["item"]["metadata"].pop("action_index", None)
        db.execute(
            "update agent_run_events set event_json=? where id=?",
            (json.dumps(event, separators=(",", ":")), row[0]),
        )

    AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=ExactReceiptExecutor(
            _audit_result_jsonl(
                "reconciled",
                operation_id=run.operation_id,
                session=run.codex_session_id,
                include_read=False,
            ),
            store=store,
            run=run,
        ),
        owner="audit-owner",
        mcp_effect_registry=registry,
    ).recover(task, context, run=run)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    effect_start = next(
        event
        for event in persisted.tool_events
        if event["type"] == "item.started"
        and event["item"]["metadata"].get("effect") == "effectful"
    )
    assert effect_start["item"]["metadata"]["action_index"] == 0
    assert persisted.effect_receipt_count == 1


def test_definitely_absent_recovery_reads_before_executing_same_revision_once(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    reconcile = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        include_write=False,
        reconciliation=[
            {
                "action_index": 0,
                "disposition": "absent",
                "read_result_digest": "recovery-read-digest",
            }
        ],
    )
    authorization = _recovery_authorizations(
        run,
        audit_context,
        frozenset({0}),
        McpToolEffectRegistry.default(),
    )[0]
    execute = _audit_jsonl(
        run.operation_id,
        session=run.codex_session_id,
        authorization_id=authorization["authorization_id"],
    )
    executor = SequencedExecutor(reconcile, execute)

    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    )
    phase_one = runner.recover(task, audit_context, run=run)

    persisted = store.get_agent_run(run.id)
    assert phase_one.result.outcome.value == "reconciled"
    assert persisted is not None and persisted.status == "unknown"
    assert persisted.final_result_json
    assert all(
        not (
            event["type"] == "item.started"
            and event["item"]["metadata"]["effect"] == "effectful"
        )
        for event in persisted.tool_events[2:]
    )

    result = runner.execute_recovery(task, audit_context, run=persisted)

    persisted = store.get_agent_run(run.id)
    assert result.result.external_result.operation_id == run.operation_id
    assert persisted is not None and persisted.status == "completed"
    assert len(executor.commands) == 2
    assert all(run.codex_session_id not in command for command in executor.commands)
    assert all("resume" not in command for command in executor.commands)
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill", "read_text_file", "read_spreadsheet"]'
        in executor.commands[0]
    )
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "execute_reviewed_write", "read_skill", "read_text_file", "read_spreadsheet"]'
        in executor.commands[1]
    )
    assert "CEO_AGENT_RECOVERY_WRITE_ALLOWLIST" in " ".join(executor.commands[1])


def test_invalid_absent_recovery_candidate_rotates_consumer_generation(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    owner = "reconciliation-owner"
    claimed = store.claim_unknown_agent_run(run.id, owner=owner)
    assert claimed.claimed
    reconciliation = AuditAgentResult.model_validate(
        {
            "outcome": "reconciled",
            "summary": "The exact action is absent.",
            "proposal_revision": run.proposal_revision,
            "side_effect_state": "unknown",
            "feedback": None,
            "external_result": None,
            "reconciliation": [
                {
                    "action_index": 0,
                    "disposition": "absent",
                    "read_result_digest": "current-turn-read",
                }
            ],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        }
    )
    persisted = store.persist_unknown_agent_run_result(
        run.id,
        reconciliation.model_dump(mode="json"),
        owner=owner,
        transcript_end_line=run.transcript_end_line,
    )
    invalid_proposal = ConsumerProposal.model_validate(
        {
            "objective": "Approve request",
            "actions": [
                {
                    "description": "Approve the exact OA task",
                    "capability": "agent_cli.dws",
                    "operation": "oa approval approve",
                    "target": {"instance_id": "instance-1", "task_id": "task-1"},
                    "payload": {
                        "argv": [
                            "dws",
                            "oa",
                            "approval",
                            "approve",
                            "--instance-id",
                            "instance-1",
                            "--task-id",
                            "task-1",
                            "--remark",
                            "同意",
                            "--format",
                            "json",
                        ]
                    },
                    "expected_verification": "OA task is completed",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "Materials satisfy the rule",
        }
    )
    executor = CapturingExecutor("")

    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).execute_recovery(
        task,
        replace(audit_context, proposal=invalid_proposal),
        run=persisted,
    )

    recovered_run = store.get_agent_run(run.id)
    requeued = store.get_reply_task(task.id)
    assert result.result.error.code == "audit_recovery_candidate_invalid"
    assert recovered_run is not None and recovered_run.status == "failed"
    assert requeued is not None and requeued.status == "pending"
    assert requeued.execution_generation != task.execution_generation
    assert executor.commands == []


def test_recovery_event_limit_defers_the_next_read_only_window(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    claim = store.claim_unknown_agent_run(run.id, owner="audit-agent")
    assert claim.claimed
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        owner="audit-agent",
    )

    runner._defer_claimed_unknown_recovery(
        claim.run,
        ValueError("agent run reconciliation event limit exceeded"),
    )

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.reconciliation_suspended is False
    assert json.loads(persisted.structured_error_json)["retryable"] is True
    assert persisted.reconciliation_next_attempt_at


def test_recovery_preserves_runtime_capability_diagnostic(setup):
    store, task, _audit_context, run = _seed_crashed_audit_write(setup)
    claim = store.claim_unknown_agent_run(run.id, owner="audit-agent")
    assert claim.claimed
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        owner="audit-agent",
    )

    class CapabilityFailure(RuntimeError):
        code = "runtime_capability_missing"
        reason = "no_eligible_route:codex_oauth=surface_missing:consumer_read_only_enforcement"

    failure = CapabilityFailure("runtime_capability_missing")
    assert _audit_recovery_error_code(failure) == "runtime_capability_missing"
    runner._defer_claimed_unknown_recovery(claim.run, failure)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    error = json.loads(persisted.structured_error_json)
    assert error["code"] == "runtime_capability_missing"
    assert error["detail"] == failure.reason


@pytest.mark.parametrize(
    "code",
    ["codex_process_failed", "runtime_unclassified", "runtime_session_evidence_missing"],
)
def test_recovery_preserves_runtime_failure_code(setup, code):
    assert _audit_recovery_error_code(RuntimeError(code)) == code


def test_persisted_absence_resumes_execute_phase_without_reconciling_again(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(
            _audit_result_jsonl(
                "reconciled",
                operation_id=run.operation_id,
                session=run.codex_session_id,
                include_write=False,
                reconciliation=[
                    {
                        "action_index": 0,
                        "disposition": "absent",
                        "read_result_digest": "recovery-read-digest",
                    }
                ],
            )
        ),
    )
    runner.recover(task, audit_context, run=run)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"

    authorization = _recovery_authorizations(
        run,
        audit_context,
        frozenset({0}),
        McpToolEffectRegistry.default(),
    )[0]
    resumed_executor = CapturingExecutor(
        _audit_jsonl(
            run.operation_id,
            session=run.codex_session_id,
            authorization_id=authorization["authorization_id"],
        )
    )
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=resumed_executor,
    ).execute_recovery(task, audit_context, run=persisted)

    persisted = store.get_agent_run(run.id)
    assert result.result.external_result.operation_id == run.operation_id
    assert persisted is not None and persisted.status == "completed"
    assert "use present only when the read proves" not in resumed_executor.prompts[0]


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

    with pytest.raises(AgentReadOnlyViolationError, match="agent_write_forbidden"):
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
                            "dws",
                            "chat",
                            "message",
                            "send",
                            "--group",
                            "cid-unrelated",
                            "--text",
                            "done",
                            "--yes",
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
                    reconciliation=[],
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
                    "dws",
                    "chat",
                    "message",
                    "send",
                    "--group",
                    "cid-second",
                    "--text",
                    "done",
                    "--yes",
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
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
    ).splitlines()
    authorization = _recovery_authorizations(
        run,
        recovery_context,
        frozenset({1}),
        McpToolEffectRegistry.default(),
    )[0]
    second_write = _audit_jsonl(
        run.operation_id,
        session=run.codex_session_id,
        write_target="cid-second",
        authorization_id=authorization["authorization_id"],
    ).splitlines()
    second_read = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        read_target="cid-second",
        read_stdout=json.dumps(
            {
                "complete": True,
                "hasMore": False,
                "paginationKnown": True,
                "failures": [],
                "queryRange": {
                    "startTime": (
                        datetime.now(UTC) - timedelta(minutes=30)
                    ).isoformat(),
                    "endTime": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                },
                "messages": [],
            }
        ),
    ).splitlines()
    final_result = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        include_read=False,
        reconciliation=[
            {
                "action_index": 0,
                "disposition": "present",
                "read_result_digest": "recovery-read-digest",
            },
            {
                "action_index": 1,
                "disposition": "absent",
                "read_result_digest": "recovery-read-digest",
            },
        ],
    ).splitlines()
    executor = SequencedExecutor(
        "\n".join(first_read[:-1] + second_read[1:-1] + final_result[-1:]),
        "\n".join(second_write),
    )

    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    )
    runner.recover(task, recovery_context, run=run)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    result = runner.execute_recovery(task, recovery_context, run=persisted)

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


def test_identical_recovery_actions_bind_to_authorized_index(setup):
    store, task, audit_context, run = _seed_crashed_audit_write(setup)
    identical_context = replace(
        audit_context,
        proposal=audit_context.proposal.model_copy(
            update={
                "actions": (
                    *audit_context.proposal.actions,
                    audit_context.proposal.actions[0],
                )
            }
        ),
    )
    reconciliation = _audit_result_jsonl(
        "reconciled",
        operation_id=run.operation_id,
        session=run.codex_session_id,
        reconciliation=[
            {
                "action_index": 0,
                "disposition": "present",
                "read_result_digest": "recovery-read-digest",
            },
            {
                "action_index": 1,
                "disposition": "absent",
                "read_result_digest": "recovery-read-digest",
            },
        ],
    )
    authorization = _recovery_authorizations(
        run,
        identical_context,
        frozenset({1}),
        McpToolEffectRegistry.default(),
    )[0]
    execute = _audit_jsonl(
        run.operation_id,
        session=run.codex_session_id,
        authorization_id=authorization["authorization_id"],
    )
    runner = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=SequencedExecutor(reconciliation, execute),
    )

    runner.recover(task, identical_context, run=run)
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    runner.execute_recovery(task, identical_context, run=persisted)

    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    recovered_starts = [
        event["item"]["metadata"]
        for event in persisted.tool_events
        if event["type"] == "item.started"
        and event["item"]["metadata"].get("authorization_id")
    ]
    assert [
        (item["action_index"], item["authorization_id"]) for item in recovered_starts
    ] == [(1, authorization["authorization_id"])]


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


def test_audit_derives_native_operation_from_exact_argv(setup):
    store, task, audit_context, parent = setup
    action = audit_context.proposal.actions[0].model_copy(
        update={"operation": "oa approval comment"}
    )
    proposal = audit_context.proposal.model_copy(update={"actions": (action,)})

    executor = CapturingExecutor(_audit_jsonl("operation-1", session="session-b"))
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(
        task,
        replace(audit_context, proposal=proposal),
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    assert result.result.outcome.value == "executed"
    assert len(executor.commands) == 1


def test_audit_normalizes_native_command_with_wrong_controlled_capability(setup):
    store, task, audit_context, parent = setup
    action = audit_context.proposal.actions[0].model_copy(
        update={"capability": "agent_cli.lark-cli"}
    )
    proposal = audit_context.proposal.model_copy(update={"actions": (action,)})

    executor = CapturingExecutor(_audit_jsonl("operation-1", session="session-b"))
    result = AuditAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(
        task,
        replace(audit_context, proposal=proposal),
        turn_attempt=0,
        parent_agent_run_id=parent.id,
    )

    assert result.result.outcome.value == "executed"
    assert len(executor.commands) == 1


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
                    "dws",
                    "chat",
                    "message",
                    "send",
                    "--group",
                    "cid-second",
                    "--text",
                    "second",
                    "--yes",
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
