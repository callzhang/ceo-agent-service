import json
from dataclasses import replace
from pathlib import Path

import pytest

import app.consumer_agent as consumer_agent
from app.agent_context import _CONSUMER_AGENT_RULES, AgentTaskContext
from app.agent_contracts import ConsumerAgentResult
from app.agent_result import EffectKind, ResultParseError
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_skill_usage import LoadedSkillReceipt
from app.agent_turn_runner import RuntimeRouteUnavailableError, _agent_cli_receipt
from app.agent_wire_contracts import ConsumerAgentWireResult
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.consumer_agent import (
    CONSUMER_DYNAMIC_SKILL_BODY,
    ConsumerAgentRunner,
    audit_developer_instructions,
    consumer_developer_instructions,
    consumer_wire_contract_hash,
)
from app.developer_prompt import DeveloperPromptTemplateError
from app.feedback_spike import PreparedOutgoingReplyText
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
)
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore
from tests.prompt_structure import validate_prompt_structure


def test_agent_cli_receipt_accepts_json_encoded_mcp_result() -> None:
    receipt = {
        "cli": "dws",
        "operation": "chat message list",
        "operation_digest": "digest",
        "target_identifiers": {"conversation": "cid-1"},
        "result_digest": "result-digest",
    }

    assert _agent_cli_receipt(json.dumps({"structuredContent": receipt})) == receipt
    wrapped = "Wall time: 0.01 seconds\nOutput:\n" + json.dumps(
        {"structuredContent": receipt}
    )
    assert _agent_cli_receipt(wrapped) == receipt


def test_consumer_records_specific_missing_agent_cli_receipt(
    store, task, context
):
    argv = ["dws", "chat", "message", "list", "--conversation-id", "cid-1"]
    item = {
        "type": "mcp_tool_call",
        "id": "missing-receipt",
        "server": "agent_cli",
        "tool": "execute_reviewed_read",
        "arguments": {"argv": argv},
    }
    stream = "\n".join(
        (
            json.dumps({"type": "item.started", "item": item}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {**item, "status": "completed", "result": {}},
                }
            ),
        )
    )

    with pytest.raises(RuntimeError, match="agent_cli_receipt_missing"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(stream),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    [run] = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    assert json.loads(run.structured_error_json)["code"] == "agent_cli_receipt_missing"


def test_consumer_records_agent_cli_tool_error_instead_of_missing_receipt(
    store, task, context
):
    argv = ["dws", "schema", "--compact", "--format", "json"]
    item = {
        "type": "mcp_tool_call",
        "id": "rejected-command",
        "server": "agent_cli",
        "tool": "execute_reviewed_read",
        "arguments": {"argv": argv},
    }
    tool_error = "Error executing tool execute_reviewed_read: agent_cli_command_unreviewed"
    stream = "\n".join(
        (
            json.dumps({"type": "item.started", "item": item}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        **item,
                        "status": "completed",
                        "result": "Wall time: 0.01 seconds\nOutput:\n"
                        + json.dumps([{"type": "text", "text": tool_error}]),
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
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    [run] = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    assert result.result.outcome == "no_action"
    assert run.structured_error_json == ""
    assert run.tool_events[-1]["item"]["metadata"]["failure_code"] == (
        "agent_cli_command_unreviewed"
    )


def test_consumer_can_continue_after_rejected_read_command(store, task, context):
    item = {
        "type": "mcp_tool_call",
        "id": "rejected-read",
        "server": "agent_cli",
        "tool": "execute_reviewed_read",
        "arguments": {"argv": ["dws", "chat", "+messages-send-status"]},
    }
    tool_error = "Error executing tool execute_reviewed_read: agent_cli_command_invalid"
    stream = "\n".join(
        (
            json.dumps({"type": "item.started", "item": item}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        **item,
                        "status": "completed",
                        "result": "Wall time: 0.01 seconds\nOutput:\n"
                        + json.dumps([{"type": "text", "text": tool_error}]),
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
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.outcome == "no_action"
    [run] = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    assert run.structured_error_json == ""
    assert run.tool_events[-1]["item"]["metadata"]["failure_code"] == (
        "agent_cli_command_invalid"
    )


class CapturingExecutor:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        self.prompts.append(str(kwargs["prompt"]))
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


class SequencedRuntimeExecutor(CapturingExecutor):
    def __init__(self, *results: ProcessRunResult) -> None:
        super().__init__("")
        self.results = list(results)
        self.environments: list[dict[str, str]] = []

    def __call__(self, command, *, on_stdout_line, **kwargs):
        result = self.results.pop(0)
        self.stdout = result.stdout
        self.environments.append(dict(kwargs["env"]))
        super().__call__(command, on_stdout_line=on_stdout_line, **kwargs)
        return result


def _consumer_runtime_dependencies(
    store, workspace=Path("/workspace"), *, routes="codex_oauth,codex_api"
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
            "consumer_read_only_enforcement",
            "reviewed_read_tools",
            "task_context",
            "channel:dingtalk",
            "mcp:agent_cli:reviewed_read",
            "native_cli:reviewed",
            "native_cli:dws",
            "mcp:memory_connector:read",
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
        "proposal": result["proposal"],
        "decision_options": result.get("decision_options", []),
        "error_code": error["code"],
        "error_retryable": error["retryable"],
        "error_authorization_required": error["authorization_required"],
    }


def test_consumer_composed_instructions_are_skill_first_and_schema_authoritative(
    context,
):
    audit_rules = "AUDIT-RULE-SENTINEL: verify supported facts."
    context_facts = context.render_business_context(
        current_time="2026-08-11 23:00:00 +0800"
    ).removeprefix("## Context Facts\n")
    instructions = (
        consumer_developer_instructions(audit_rules)
        + "\n\n"
        + _CONSUMER_AGENT_RULES
        + f"\n\n## Context Facts\n{context_facts}"
    )

    validate_prompt_structure(
        instructions,
        contract_models=(
            ("Pydantic Wire Contract", ConsumerAgentWireResult),
            ("Pydantic Result Contract", ConsumerAgentResult),
        ),
        dynamic_skill_body=CONSUMER_DYNAMIC_SKILL_BODY,
        audit_rules=audit_rules,
        context_facts=context_facts,
        size_limit=32_000,
        require_runtime_safety_sections=True,
    )
    assert audit_rules in instructions
    assert CONSUMER_DYNAMIC_SKILL_BODY in instructions


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
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(_wire_result(result))}}),
        )
    )


def test_consumer_persists_native_mcp_reads_from_codex_session(
    store, task, context, tmp_path, monkeypatch
):
    session_id = "session-native-mcp"
    session_dir = tmp_path / "sessions" / "2026" / "08" / "20"
    session_dir.mkdir(parents=True)
    session_path = session_dir / f"rollout-{session_id}.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": session_id}},
        {
            "type": "event_msg",
            "payload": {"type": "turn_started", "turn_id": "turn-business"},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "turn-business",
                "item": {
                    "type": "McpToolCall",
                    "id": "controlled-wrapper-1",
                    "server": "agent_cli",
                    "tool": "execute_reviewed_read",
                    "arguments": {"argv": ["unsupported", "read"]},
                    "status": "completed",
                    "result": {"content": [{"type": "text", "text": "ignored"}]},
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "turn-business",
                "item": {
                    "type": "McpToolCall",
                    "id": "xiaoqing-read-1",
                    "server": "xiaoqing_interview",
                    "tool": "search_candidates",
                    "arguments": {"query": "candidate"},
                    "status": "completed",
                    "result": {"content": [{"type": "text", "text": "found"}]},
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "turn_started", "turn_id": "turn-hook"},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "turn-hook",
                "item": {
                    "type": "McpToolCall",
                    "id": "hook-write-1",
                    "server": "memory_connector",
                    "tool": "memory_write",
                    "arguments": {"data": "hook"},
                    "status": "completed",
                    "result": {"content": [{"type": "text", "text": "stored"}]},
                },
            },
        },
    ]
    session_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    stdout_events = _result_jsonl(session=session_id).splitlines()
    stdout_events.insert(1, json.dumps({"type": "turn.started"}))
    stdout_events.append(json.dumps({"type": "turn.completed"}))
    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor("\n".join(stdout_events)),
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run(result.run_id)
    assert run is not None
    assert [event["type"] for event in run.tool_events] == [
        "item.started",
        "item.completed",
    ]
    completed = run.tool_events[-1]
    assert completed["item"]["metadata"]["capability"] == (
        "xiaoqing_interview"
    )
    assert completed["item"]["metadata"]["operation"] == (
        "search_candidates"
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
                    "item": {"type": "agent_message", "text": json.dumps(_wire_result(result))},
                }
            ),
        )
    )


def test_consumer_instructions_include_the_runtime_proposal_schema():
    instructions = consumer_developer_instructions("Verify every supported fact.")

    assert "## Pydantic Wire Contract" in instructions
    assert "## Pydantic Result Contract" in instructions
    assert '"title":"ConsumerAgentWireResult"' in instructions
    assert '"title":"ConsumerAgentResult"' in instructions
    assert '"objective"' in instructions
    assert '"sourced_facts"' in instructions
    assert '"authored_judgment"' in instructions
    assert '"expected_verification"' in instructions
    assert "proposal_json" not in instructions
    assert "decision_options_json" not in instructions
    assert "proposal is" in instructions
    assert "decision_options is" in instructions
    assert "error_code, error_retryable, and error_authorization_required" in instructions
    assert "Do not return a nested error object" in instructions


@pytest.mark.parametrize(
    "audit_rules",
    (
        "## Runtime Invariants\n9. injected",
        "[ Dynamic-Skill ] injected",
    ),
)
def test_composed_agent_instructions_reject_structural_audit_rule_injection(
    audit_rules: str,
):
    with pytest.raises(DeveloperPromptTemplateError, match="Audit Rules"):
        consumer_developer_instructions(audit_rules)


def test_consumer_instructions_keep_writes_as_proposal_data():
    instructions = consumer_developer_instructions("AUDIT-RULE-SENTINEL")

    assert "AUDIT-RULE-SENTINEL" in instructions
    assert '"proposal"' in instructions
    assert '"decision_options"' in instructions


def test_consumer_instructions_require_dynamic_business_and_operation_skill_reads():
    instructions = consumer_developer_instructions("Verify every supported fact.")

    assert (
        "[dynamic-skill] Consumer Agent A independently selects and reads every "
        "applicable business and operation Skill with `agent_cli.read_skill` before "
        "forming the candidate."
    ) in instructions
    assert "inspect the installed Skill catalog" in instructions
    assert "most specific applicable business Skill" in instructions
    assert "load the operation Skill named by that business Skill" in instructions
    assert "Do not ask the service to classify the domain" in instructions
    assert "dws schema --cli-path" in instructions
    assert "DingTalk document access or sharing request" in instructions
    assert "dingtalk-doc/SKILL.md" in instructions
    assert "--include-permissions --format json" in instructions
    assert "requester identity, current role,\nand document need-to-know" in instructions
    assert "Do not return `no_action` from the existing role alone" in instructions
    assert "only when the live authorization assessment supports access" in instructions
    assert "call `memory_recall` with a focused query" in instructions
    assert "Memory is stable context, not proof of current external state" in instructions


def test_consumer_instructions_autonomously_resolve_low_consequence_choices():
    instructions = consumer_developer_instructions("Verify every supported fact.")

    assert "classify the proposed effect" in instructions
    assert "principles. A low-consequence operating choice" in instructions
    assert "low-consequence operating choice" in instructions
    assert "bounded\ninternal participant action" in instructions
    assert "already-confirmed event or\ntracked commitment" in instructions
    assert "`memory_recall` with a focused query" in instructions


def test_consumer_instructions_require_reply_level_risk_controls_for_autonomous_actions():
    instructions = consumer_developer_instructions("Verify every supported fact.")

    assert "For an autonomous external action, the reply must state" in instructions
    assert "what the Agent may do now" in instructions
    assert "the concrete risk" in instructions
    assert "what the recipient must not do" in instructions
    assert "what still requires Derek's decision" in instructions
    assert "Do not hide the boundary in a generic risk disclaimer" in instructions
    assert "Memory is context, not proof of the current external state" in instructions
    assert "Do not escalate merely because another reasonable default" in instructions
    assert "exists. When optional paths are otherwise equivalent" in instructions
    assert "choose the one that adds\nno new work or deliverable" in instructions


def test_audit_instructions_accept_the_authorized_low_consequence_standard():
    instructions = audit_developer_instructions("Verify every supported fact.")

    assert "authorized judgment standard" in instructions
    assert "minimum reversible path" in instructions
    assert "do not require a prior\nmessage containing the same choice" in instructions


def test_audit_recovery_instructions_override_normal_audit_outcomes():
    instructions = audit_developer_instructions(
        "Verify every supported fact.",
        allow_write=False,
        recovery_reconciliation=True,
    )

    assert instructions.startswith("This is an unknown-outcome recovery")
    assert "perform a target-matched live read" in instructions
    assert "External writes are unavailable" in instructions
    assert "Return only outcome=reconciled" in instructions
    assert "Do not return executed, revision_required, failed, or needs_human" in instructions


def test_consumer_instructions_do_not_enumerate_specialist_workflows():
    instructions = consumer_developer_instructions("Verify every supported fact.")

    assert "OA approval work" not in instructions
    assert "candidate interview or evaluation" not in instructions
    assert "OKR review or scoring" not in instructions
    assert CONSUMER_DYNAMIC_SKILL_BODY in instructions


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
                    "item": {"type": "agent_message", "text": json.dumps(_wire_result(result))},
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
    store.set_codex_session_contract_hash(
        task.conversation_id, consumer_wire_contract_hash()
    )
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
    # Result parsing remains strict in the service, but output-schema is not
    # sent to Codex because it conflicts with dynamically loaded MCP tools.
    assert "--output-schema" not in command
    assert 'approval_policy="never"' in command
    assert command.count("--disable") == 8
    assert command[command.index("--disable") + 1] == "plugins"
    assert "apps" in command
    assert (
        'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill", "read_text_file", "read_spreadsheet"]'
        in command
    )
    assert "execute_reviewed_write" not in " ".join(command)
    assert 'mcp_servers.exa.url="https://mcp.exa.ai/mcp"' in command
    assert "mcp_servers.exa.enabled=false" not in command
    assert not any(
        command[index] == command[index + 1] == "-c"
        for index in range(len(command) - 1)
    )
    assert store.get_agent_run(result.run_id).role.value == "consumer"
    assert not any(
        "Output JSON Schema (validated locally):" in option for option in command
    )
    assert any("agent_cli.read_skill" in option for option in command)
    assert any("## Pydantic Wire Contract" in option for option in command)
    assert any("ConsumerAgentWireResult" in option for option in command)
    assert "## Runtime Invariants" in executor.prompts[0]
    assert "## Installed CEO business Skill catalog" not in executor.prompts[0]
    assert any("## Installed CEO business Skill catalog" in option for option in command)
    assert any("Do not reopen AGENT.md with shell" in option for option in command)
    assert any("PROTOCOL PRECONDITION" in option for option in command)
    assert any("ceo-message-triage" in option for option in command)
    assert any("ceo-work-tracking" in option for option in command)
    assert any(
        "call `agent_cli.execute_reviewed_read`" in option
        for option in command
    )
    assert any("agent_cli.read_text_file" in option for option in command)
    assert any("Arbitrary local shell and" in option for option in command)
    assert any(
        "dingtalk-chat/SKILL.md" in option
        and "not a reason to return `needs_human`" in option
        for option in command
    )
    instructions = consumer_developer_instructions("Consumer Agent A is read-only.")
    assert "referenced skill, document,\nconfiguration" in instructions
    assert "normal Agent work" in instructions
    assert "Xiaoqing interview MCP tools" in instructions
    assert "mandatory preconditions for every candidate outcome" in instructions
    assert "real-person" in instructions
    assert 'Do not propose sending "I will review"' in instructions
    assert "First prepare a sourced evidence packet" in instructions
    assert "Only the remaining sensitive hiring or advancement decision" in instructions
    assert "return a retryable service-dependency failure" in instructions
    assert any(
        "Authoritative Consumer role boundary" in option
        and "valid ConsumerAgentResult JSON" in option
        for option in command
    )
    assert "proposal_json" not in executor.prompts[0]
    assert "proposal must match the supplied JSON Schema exactly" in executor.prompts[0]
    assert '"expected_verification"' in executor.prompts[0]
    assert any(
        "each array item must contain exactly these non-empty string fields" in option
        and "`key`" in option
        and "use concise identifiers such as `option_1`" in option
        for option in command
    )


def test_consumer_rotates_session_when_wire_contract_changes(store, task, context):
    store.upsert_conversation(task.conversation_id, "Group", False, "session-old")
    store.set_codex_session_contract_hash(task.conversation_id, "old-contract")
    executor = CapturingExecutor(_result_jsonl(session="session-fresh"))

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert executor.commands[0][:2] == ["codex", "exec"]
    assert executor.commands[0][2] != "resume"
    assert store.get_codex_session_id(task.conversation_id) == "session-fresh"
    assert (
        store.get_codex_session_contract_hash(task.conversation_id)
        == consumer_wire_contract_hash()
    )


def test_consumer_forced_rerun_starts_a_fresh_session(store, task, context):
    store.upsert_conversation(task.conversation_id, "Group", False, "session-old")
    store.set_codex_session_contract_hash(
        task.conversation_id, consumer_wire_contract_hash()
    )
    executor = CapturingExecutor(_result_jsonl(session="session-fresh"))

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        codex_session_exists=lambda _: True,
    ).run(
        task.model_copy(update={"force_new_decision": True}),
        context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )

    assert executor.commands[0][:2] == ["codex", "exec"]
    assert executor.commands[0][2] != "resume"
    assert store.get_codex_session_id(task.conversation_id) == "session-fresh"


def test_consumer_accepts_read_only_session_handoff(store, task, context):
    result = {
        "outcome": "no_action",
        "summary": "Nothing to do.",
        "proposal": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    }
    executor = CapturingExecutor(
        "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "session-first"}),
                json.dumps({"type": "thread.started", "thread_id": "session-final"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(_wire_result(result)),
                        },
                    }
                ),
            )
        )
    )

    outcome = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run(outcome.run_id)
    assert run is not None and run.codex_session_id == "session-final"
    assert store.get_codex_session_id(task.conversation_id) == "session-final"


def test_consumer_rotates_session_when_service_read_contract_changes(
    store, task, context, monkeypatch
):
    monkeypatch.setattr(
        consumer_agent,
        "service_read_command_contract",
        lambda: ("previous-read-command",),
    )
    old_contract = consumer_agent.consumer_wire_contract_hash()
    store.upsert_conversation(task.conversation_id, "Group", False, "session-old")
    store.set_codex_session_contract_hash(task.conversation_id, old_contract)
    monkeypatch.setattr(
        consumer_agent,
        "service_read_command_contract",
        lambda: ("current-read-command",),
    )
    executor = CapturingExecutor(_result_jsonl(session="session-fresh"))

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert executor.commands[0][:2] == ["codex", "exec"]
    assert executor.commands[0][2] != "resume"
    assert store.get_codex_session_id(task.conversation_id) == "session-fresh"
    assert store.get_codex_session_contract_hash(task.conversation_id) == (
        consumer_agent.consumer_wire_contract_hash()
    )


def test_consumer_retryable_failure_without_tool_progress_rotates_session(
    store, task, context
):
    store.upsert_conversation(task.conversation_id, "Group", False, "session-old")
    store.set_codex_session_contract_hash(
        task.conversation_id, consumer_wire_contract_hash()
    )
    failed = {
        "outcome": "failed",
        "summary": "Could not start the required read.",
        "proposal": None,
        "error": {
            "code": "read_unavailable",
            "retryable": True,
            "authorization_required": False,
        },
    }
    executor = CapturingExecutor(
        "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "session-failed"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(_wire_result(failed)),
                        },
                    }
                ),
            )
        )
    )

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.outcome.value == "failed"
    assert executor.commands[0][:3] == ["codex", "exec", "resume"]
    assert store.get_codex_session_id(task.conversation_id) is None


def test_consumer_read_events_can_fail_over_within_same_run(
    store, task, context, monkeypatch
):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth,codex_api")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "fallback-test-key")
    read_lines = _failed_reviewed_read_jsonl().splitlines()[:3]
    oauth_failure = "\n".join(
        (
            *read_lines,
            json.dumps(
                {
                    "type": "error",
                    "message": "Failed to refresh token: Your session has ended",
                }
            ),
        )
    )
    executor = SequencedRuntimeExecutor(
        ProcessRunResult(1, oauth_failure, ""),
        ProcessRunResult(0, _result_jsonl(session="session-api"), ""),
    )
    config, router, adapter = _consumer_runtime_dependencies(store)

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    attempts = store.list_agent_runtime_attempts(result.run_id)
    persisted_task = store.get_reply_task(task.id)
    assert result.run_id == attempts[0].agent_run_id
    assert [attempt.route_name for attempt in attempts] == [
        "codex_oauth",
        "codex_api",
    ]
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert attempts[0].failure_class == "authentication"
    assert attempts[0].failure_code == "codex_login_required"
    assert [attempt.session_id for attempt in attempts] == [
        "session-a",
        "session-api",
    ]
    assert attempts[0].transcript_start == 0
    assert attempts[0].transcript_end == len(oauth_failure.splitlines())
    assert attempts[1].transcript_start == 0
    assert attempts[1].transcript_end == len(
        _result_jsonl(session="session-api").splitlines()
    )
    assert store.active_runtime_route_pause(
        "codex_oauth", now="2026-08-20 00:00:00"
    ) == "codex_login_required"
    assert persisted_task is not None
    assert persisted_task.execution_generation == task.execution_generation
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_api"
    ) == "session-api"
    assert store.get_codex_session_id(task.conversation_id) == "session-a"
    persisted_run = store.get_agent_run(result.run_id)
    assert persisted_run is not None
    assert persisted_run.codex_session_id == "session-a"
    assert "OPENAI_API_KEY" not in executor.environments[0]
    assert executor.environments[1]["OPENAI_API_KEY"] == "fallback-test-key"


def test_transport_failure_opens_route_pause_before_api_successor(
    store, task, context
):
    executor = SequencedRuntimeExecutor(
        ProcessRunResult(
            1,
            "\n".join(
                (
                    json.dumps(
                        {"type": "thread.started", "thread_id": "oauth-transport"}
                    ),
                    json.dumps(
                        {
                            "type": "error",
                            "message": "stream disconnected before completion",
                        }
                    ),
                )
            ),
            "",
        ),
        ProcessRunResult(0, _result_jsonl(session="api-success"), ""),
    )
    config, router, adapter = _consumer_runtime_dependencies(store)

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.outcome.value == "no_action"
    assert len(executor.commands) == 2
    assert store.active_runtime_route_pause(
        "codex_oauth", now="2026-08-20 00:00:00"
    ) == "codex_transport_disconnected"


def test_consumer_does_not_start_unprobed_api_fallback(
    store, task, context, monkeypatch
):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth,codex_api")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "fallback-test-key")
    executor = SequencedRuntimeExecutor(
        ProcessRunResult(
            1,
            json.dumps(
                {
                    "type": "error",
                    "message": "Failed to refresh token: Your session has ended",
                }
            ),
            "",
        )
    )

    with pytest.raises(ResultParseError):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert [
        attempt.route_name for attempt in store.list_agent_runtime_attempts(run.id)
    ] == ["codex_oauth"]
    assert len(executor.environments) == 1

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

    with pytest.raises(RuntimeError, match="codex_provider_capacity_exhausted"):
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
    assert '"code":"codex_provider_capacity_exhausted"' in run.structured_error_json
    assert '"retryable":true' in run.structured_error_json
    assert store.active_runtime_route_pause(
        "codex_oauth", now="2026-08-20 00:00:00"
    ) == "codex_provider_capacity_exhausted"


def test_retryable_consumer_turn_uses_the_current_conversation_session(
    store, task, context
):
    provider_failure = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-old"}),
            json.dumps(
                {
                    "type": "error",
                    "message": "You've hit your usage limit. Try again later.",
                }
            ),
            json.dumps({"type": "turn.failed"}),
        )
    )
    with pytest.raises(RuntimeError, match="codex_provider_capacity_exhausted"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=FailingExecutor(provider_failure),
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)
    store.close_runtime_route_pause("codex_oauth")
    store.upsert_conversation(
        task.conversation_id,
        task.conversation_title,
        task.single_chat,
        "session-new",
    )
    store.set_codex_session_contract_hash(
        task.conversation_id, consumer_wire_contract_hash()
    )
    executor = CapturingExecutor(_result_jsonl(session="session-new"))

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert executor.commands[0][:3] == ["codex", "exec", "resume"]
    assert executor.commands[0][-2:] == ["session-new", "-"]
    assert store.get_codex_session_id(task.conversation_id) == "session-new"
    failed = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    recovered = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=1,
    )
    assert failed is not None and failed.status == "failed"
    assert recovered is not None and recovered.status == "completed"


def test_retry_turn_parse_failure_clears_only_its_current_conversation_session(
    store, task, context
):
    provider_failure = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-old"}),
            json.dumps(
                {
                    "type": "error",
                    "message": "You've hit your usage limit. Try again later.",
                }
            ),
            json.dumps({"type": "turn.failed"}),
        )
    )
    with pytest.raises(RuntimeError, match="codex_provider_capacity_exhausted"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=FailingExecutor(provider_failure),
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)
    store.close_runtime_route_pause("codex_oauth")
    store.upsert_conversation(
        task.conversation_id,
        task.conversation_title,
        task.single_chat,
        "session-new",
    )
    store.set_codex_session_contract_hash(
        task.conversation_id, consumer_wire_contract_hash()
    )
    missing_result = json.dumps({"type": "turn.failed"})

    with pytest.raises(ResultParseError, match="no valid typed result"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(missing_result),
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert store.get_codex_session_id(task.conversation_id) is None
    failed = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    retry = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=1,
    )
    assert failed is not None and failed.codex_session_id == "session-old"
    assert retry is not None and retry.codex_session_id == ""


def test_retry_after_failed_session_creates_a_new_turn_and_session(store, task, context):
    first = CapturingExecutor(json.dumps({"type": "thread.started", "thread_id": "session-old"}))
    with pytest.raises(ResultParseError, match="no valid typed result"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=first,
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert store.get_codex_session_id(task.conversation_id) is None
    recovered = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(_result_jsonl(session="session-fresh")),
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    failed = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    retry = store.get_agent_run(recovered.run_id)
    assert failed is not None and failed.status == "failed"
    assert failed.codex_session_id == "session-old"
    assert retry is not None and retry.turn_attempt == 1
    assert retry.codex_session_id == "session-fresh"
    assert store.get_codex_session_id(task.conversation_id) == "session-fresh"


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
    assert error["authorization_required"] is False
    assert error["retryable"] is False


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


def test_consumer_rejects_malformed_nested_output_locally(
    store,
    task,
    context,
):
    malformed = {
        "outcome": "proposal",
        "summary": "Invalid legacy wire shape.",
        "proposal": json.dumps({"objective": "Legacy string payload"}),
        "decision_options": [],
        "error_code": "",
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
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert "--output-schema" not in executor.commands[0]


def test_consumer_accepts_valid_nested_output_locally(store, task, context):
    executor = CapturingExecutor(_proposal_jsonl({"text": "Verified notice."}))

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.outcome.value == "proposal"
    assert result.result.proposal is not None
    assert result.result.proposal.actions[0].payload == {
        "text": "Verified notice."
    }
    assert "--output-schema" not in executor.commands[0]


@pytest.mark.parametrize(
    ("command_argv", "body_index"),
    (
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--group",
                "cid-agent",
                "--text",
                "Verified notice.",
                "--yes",
            ],
            7,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--conversation-id",
                "cid-agent",
                "--ai-tag",
                "Verified notice.",
                "--yes",
            ],
            7,
        ),
        (
            [
                "dws",
                "chat",
                "+messages-send",
                "--conversation-id",
                "cid-agent",
                "--markdown",
                "Verified notice.",
                "--yes",
            ],
            6,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "Verified notice.",
                "--group",
                "cid-agent",
                "--yes",
            ],
            4,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--group",
                "cid-agent",
                "--yes",
                "Verified notice.",
            ],
            7,
        ),
    ),
)
def test_consumer_prepares_dingtalk_message_postfix_before_persisting(
    store,
    task,
    context,
    monkeypatch,
    command_argv,
    body_index,
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    executor = CapturingExecutor(
        _proposal_jsonl({"argv": command_argv})
    )

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.proposal is not None
    argv = result.result.proposal.actions[0].payload["argv"]
    text = argv[body_index]
    assert text.startswith("Verified notice.（by明哥分身）")
    assert text.count("（by明哥分身）") == 1
    assert text.count("/api/dingtalk-feedback-spike") == 2
    persisted = store.get_agent_run(result.run_id)
    assert persisted is not None
    persisted_result = ConsumerAgentResult.model_validate_json(
        persisted.final_result_json
    )
    assert persisted_result.proposal is not None
    assert persisted_result.proposal.actions[0].payload["argv"] == argv


def test_consumer_prepares_command_string_and_persists_one_argv_contract(
    store,
    task,
    context,
    monkeypatch,
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    executor = CapturingExecutor(
        _proposal_jsonl(
            {
                "command": (
                    "dws chat message send --conversation-id cid-agent "
                    "--content 'Verified notice.' --yes"
                )
            }
        )
    )

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.proposal is not None
    payload = result.result.proposal.actions[0].payload
    assert "command" not in payload
    assert isinstance(payload["argv"], list)
    text = payload["argv"][payload["argv"].index("--content") + 1]
    assert text.startswith("Verified notice.（by明哥分身）")
    assert text.count("/api/dingtalk-feedback-spike") == 2


def test_consumer_preserves_dash_prefixed_explicit_content_as_message_body(
    store,
    task,
    context,
    monkeypatch,
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    executor = CapturingExecutor(
        _proposal_jsonl(
            {
                "argv": [
                    "dws",
                    "chat",
                    "message",
                    "send",
                    "--conversation-id",
                    "cid-agent",
                    "--content",
                    "--hello",
                    "--yes",
                ]
            }
        )
    )

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.proposal is not None
    argv = result.result.proposal.actions[0].payload["argv"]
    text = argv[argv.index("--content") + 1]
    assert text.startswith("--hello（by明哥分身）")
    assert text.count("/api/dingtalk-feedback-spike") == 2


def test_consumer_rejects_sensitive_value_added_by_postfix_preparation(
    store,
    task,
    context,
    monkeypatch,
):
    monkeypatch.setattr(
        consumer_agent,
        "prepare_outgoing_reply_text",
        lambda **_kwargs: PreparedOutgoingReplyText(
            feedback_token="",
            text="Bearer sk-sensitive-value-added-after-model-validation",
        ),
    )
    executor = CapturingExecutor(
        _proposal_jsonl(
            {
                "argv": [
                    "dws",
                    "chat",
                    "message",
                    "send",
                    "--group",
                    "cid-agent",
                    "--text",
                    "Verified notice.",
                    "--yes",
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="agent_result_contains_sensitive_value"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
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


def test_consumer_ignores_effectful_event_from_later_hook_turn(store, task, context):
    business_result = json.loads(_result_jsonl().splitlines()[-1])
    hook_write = {
        "type": "item.started",
        "item": {
            "type": "mcp_tool_call",
            "id": "hook-write-1",
            "server": "memory_connector",
            "tool": "memory_write",
            "arguments": {},
        },
    }
    stream = "\n".join(
        json.dumps(event)
        for event in (
            {"type": "thread.started", "thread_id": "session-a"},
            {"type": "turn.started"},
            business_result,
            {"type": "turn.completed"},
            {"type": "turn.started"},
            hook_write,
            {"type": "turn.completed"},
        )
    )

    result = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=CapturingExecutor(stream),
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert result.result.outcome.value == "no_action"
    persisted = store.get_agent_run(result.run_id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.tool_events == []


def test_consumer_rejects_reviewed_direct_native_read(store, task, context):
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

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="agent_shell_execution_forbidden",
    ):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(stream),
            native_cli_classifier=NativeCliMetadataClassifier(
                reviewed_effects={
                    ("dws", "oa approval detail"): EffectKind.READ_ONLY,
                }
            ),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)


def test_consumer_rejects_generic_local_read_tool_call(store, task, context):
    argv = ["sed", "-n", "1p", "/tmp/public-material"]
    item = {
        "type": "mcp_tool_call",
        "id": "local-read-1",
        "server": "agent_cli",
        "tool": "execute_reviewed_read",
        "arguments": {"argv": argv},
    }
    stream = "\n".join(
        (
            json.dumps({"type": "item.started", "item": item}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        **item,
                        "status": "completed",
                        "result": {"structuredContent": {}},
                    },
                }
            ),
            _result_jsonl(),
        )
    )

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="agent_cli_command_invalid",
    ):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=CapturingExecutor(stream),
            native_cli_classifier=NativeCliMetadataClassifier(reviewed_effects={}),
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)


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
        match="agent_shell_execution_forbidden",
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

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None
    assert '"code":"agent_shell_execution_forbidden"' in run.structured_error_json


def test_consumer_rejects_direct_shell_command(store, task, context):
    shell = json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "id": "shell-1",
                    "command": "rm /tmp/material",
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


def test_consumer_rejects_direct_generic_local_read_command(store, task, context):
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


@pytest.mark.parametrize("eligibility", ["unprobed", "paused", "missing_capability"])
def test_api_only_ineligible_route_is_typed_and_starts_no_process(
    store, task, context, eligibility
):
    config, healthy_router, adapter = _consumer_runtime_dependencies(
        store, routes="codex_api"
    )
    snapshots = dict(healthy_router._snapshots)
    if eligibility == "unprobed":
        snapshots = {}
    elif eligibility == "missing_capability":
        snapshots["codex_api"] = snapshots["codex_api"].model_copy(
            update={"capabilities": frozenset({"structured_output"})}
        )
    else:
        store.open_runtime_route_pause(
            "codex_api", "provider_unavailable", "2099-08-20 00:00:00"
        )
    router = AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots=snapshots,
    )
    executor = CapturingExecutor(_result_jsonl(session="must-not-start"))

    with pytest.raises(RuntimeRouteUnavailableError):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=executor,
            runtime_config=config,
            runtime_router=router,
            codex_adapter=adapter,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert run is not None and run.status == "failed"
    error = json.loads(run.structured_error_json)
    assert error["code"] == (
        "runtime_capability_missing"
        if eligibility == "missing_capability"
        else "runtime_route_unavailable"
    )
    assert error["retryable"] is True
    expected_reason = {
        "unprobed": "snapshot_missing",
        "paused": "paused",
        "missing_capability": "missing_capabilities",
    }[eligibility]
    assert expected_reason in error["detail"]
    assert store.list_agent_runtime_attempts(run.id) == []
    assert executor.commands == []


def test_consumer_derives_concrete_turn_capabilities_for_images_and_channel(
    store, context
):
    runner = ConsumerAgentRunner(store=store, workspace=Path("/workspace"))
    required = runner._required_capabilities(
        replace(context, image_paths=("/tmp/evidence.png",))
    )

    assert {
        "image_input",
        "channel:dingtalk",
        "native_cli:dws",
        "native_cli:reviewed",
        "mcp:agent_cli:reviewed_read",
        "mcp:memory_connector:read",
    } <= required


def test_consumer_requires_only_explicit_exact_reviewed_skills(store, context):
    config, router, _ = _consumer_runtime_dependencies(store, routes="codex_api")
    runner = ConsumerAgentRunner(store=store, workspace=Path("/workspace"))
    assert not any(
        capability.startswith("reviewed_skill:")
        for capability in runner._required_capabilities(context)
    )
    receipt = LoadedSkillReceipt(
        name="ceo-message-triage",
        path="/reviewed/ceo-message-triage/SKILL.md",
        sha256="a" * 64,
    )
    required = runner._required_capabilities(
        replace(context, required_reviewed_skills=(receipt,))
    )
    exact = f"reviewed_skill:{receipt.name}:{receipt.sha256}"

    assert exact in required
    assert router.first_eligible_route(required_capabilities=required) is None
    snapshot = router._snapshots["codex_api"].model_copy(
        update={"capabilities": router._snapshots["codex_api"].capabilities | {exact}}
    )
    proven = AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots={"codex_api": snapshot},
    )
    assert proven.first_eligible_route(required_capabilities=required).name == "codex_api"


def test_api_retry_without_progress_clears_only_api_consumer_session(
    store, task, context
):
    store.upsert_conversation(task.conversation_id, "Group", False, "oauth-keep")
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_oauth",
        "oauth-keep",
        consumer_wire_contract_hash(),
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_api",
        "api-old",
        consumer_wire_contract_hash(),
    )
    store.set_codex_session_contract_hash(
        task.conversation_id, consumer_wire_contract_hash()
    )
    config, router, adapter = _consumer_runtime_dependencies(store, routes="codex_api")
    failed = {
        "outcome": "failed",
        "summary": "No progress.",
        "proposal": None,
        "error": {
            "code": "read_unavailable",
            "retryable": True,
            "authorization_required": False,
        },
    }
    executor = CapturingExecutor(
        "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "api-failed"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(_wire_result(failed)),
                        },
                    }
                ),
            )
        )
    )

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert (
        store.get_conversation_runtime_session(task.conversation_id, "codex_api")
        is None
    )
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_oauth"
    ) == "oauth-keep"
    assert store.get_codex_session_id(task.conversation_id) == "oauth-keep"


@pytest.mark.parametrize(
    "invalidation", ["force_new_decision", "wire_mismatch", "missing_session"]
)
def test_api_consumer_invalidation_starts_fresh_and_preserves_oauth(
    store, task, context, invalidation
):
    store.upsert_conversation(task.conversation_id, "Group", False, "oauth-keep")
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_oauth",
        "oauth-keep",
        consumer_wire_contract_hash(),
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_api",
        "api-old",
        (
            "outdated-contract"
            if invalidation == "wire_mismatch"
            else consumer_wire_contract_hash()
        ),
    )
    store.set_codex_session_contract_hash(
        task.conversation_id,
        "outdated-contract"
        if invalidation == "wire_mismatch"
        else consumer_wire_contract_hash(),
    )
    config, router, adapter = _consumer_runtime_dependencies(store, routes="codex_api")
    executor = CapturingExecutor(_result_jsonl(session="api-new"))
    selected_task = (
        task.model_copy(update={"force_new_decision": True})
        if invalidation == "force_new_decision"
        else task
    )

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
        codex_session_exists=(
            (lambda _: False)
            if invalidation == "missing_session"
            else (lambda _: True)
        ),
    ).run(selected_task, context, proposal_revision=0, parent_agent_run_id=None)

    assert executor.commands[0][:2] == ["codex", "exec"]
    assert "resume" not in executor.commands[0]
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_api"
    ) == "api-new"
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_oauth"
    ) == "oauth-keep"
    assert store.get_codex_session_id(task.conversation_id) == "oauth-keep"


def test_api_contract_refresh_does_not_make_old_oauth_session_current(
    store, task, context
):
    old_hash = "old-wire-contract"
    current_hash = consumer_wire_contract_hash()
    store.upsert_conversation(task.conversation_id, "Group", False, "oauth-old")
    store.upsert_conversation_runtime_session(
        task.conversation_id, "codex_oauth", "oauth-old", old_hash
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id, "codex_api", "api-old", old_hash
    )
    api_config, api_router, api_adapter = _consumer_runtime_dependencies(
        store, routes="codex_api"
    )
    api_executor = CapturingExecutor(_result_jsonl(session="api-new"))

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=api_executor,
        runtime_config=api_config,
        runtime_router=api_router,
        codex_adapter=api_adapter,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert store.get_conversation_runtime_session_contract_hash(
        task.conversation_id, "codex_api"
    ) == current_hash
    assert store.get_conversation_runtime_session_contract_hash(
        task.conversation_id, "codex_oauth"
    ) == old_hash
    assert store.get_conversation_runtime_session(
        task.conversation_id,
        "codex_oauth",
        required_contract_hash=current_hash,
    ) is None

    oauth_config, oauth_router, oauth_adapter = _consumer_runtime_dependencies(
        store, routes="codex_oauth"
    )
    oauth_executor = CapturingExecutor(_result_jsonl(session="oauth-new"))
    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=oauth_executor,
        runtime_config=oauth_config,
        runtime_router=oauth_router,
        codex_adapter=oauth_adapter,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert "resume" not in api_executor.commands[0]
    assert "resume" not in oauth_executor.commands[0]
    assert store.get_conversation_runtime_session_contract_hash(
        task.conversation_id, "codex_oauth"
    ) == current_hash


def test_route_session_without_contract_hash_is_never_resumed(store, task, context):
    store.upsert_conversation_runtime_session(
        task.conversation_id, "codex_api", "upgraded-row-without-hash"
    )
    config, router, adapter = _consumer_runtime_dependencies(store, routes="codex_api")
    executor = CapturingExecutor(_result_jsonl(session="api-new"))

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        runtime_config=config,
        runtime_router=router,
        codex_adapter=adapter,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert "resume" not in executor.commands[0]
    assert store.get_conversation_runtime_session_contract_hash(
        task.conversation_id, "codex_api"
    ) == consumer_wire_contract_hash()


def test_claude_route_session_is_not_checked_as_local_codex_history(store, task):
    contract_hash = consumer_wire_contract_hash()
    store.upsert_conversation_runtime_session(
        task.conversation_id, "claude_api", "claude-session", contract_hash
    )
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api",
            "CEO_CLAUDE_API_KEY": "test-claude-secret",
        }
    )
    checked: list[str] = []
    runner = ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        runtime_config=config,
        codex_session_exists=lambda session_id: checked.append(session_id) or False,
    )

    assert runner._route_session_exists("claude_api", "claude-session") is True
    assert checked == []
