import json
from dataclasses import replace
from pathlib import Path

import pytest

import app.consumer_agent as consumer_agent
from app.agent_context import AgentTaskContext, _CONSUMER_AGENT_RULES
from app.agent_contracts import ConsumerAgentResult
from app.consumer_agent import (
    CONSUMER_DYNAMIC_SKILL_BODY,
    ConsumerAgentRunner,
    consumer_developer_instructions,
    consumer_wire_contract_hash,
)
from app.agent_wire_contracts import ConsumerAgentWireResult
from app.agent_result import EffectKind, ResultParseError
from app.developer_prompt import DeveloperPromptTemplateError
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
)
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore
from tests.prompt_structure import validate_prompt_structure


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
        size_limit=12_000,
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
    assert instructions.count("agent_cli.read_skill") == 1


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
    assert "--output-schema" not in command
    assert 'approval_policy="never"' in command
    assert "features.plugins=false" not in command
    assert "features.apps=false" not in command
    assert 'mcp_servers.agent_cli.enabled_tools=["execute_reviewed_read", "read_skill", "read_spreadsheet"]' in command
    assert "execute_reviewed_write" not in " ".join(command)
    assert store.get_agent_run(result.run_id).role.value == "consumer"
    assert not any(
        "Output JSON Schema (validated locally):" in option for option in command
    )
    assert any("agent_cli.read_skill" in option for option in command)
    instructions = consumer_developer_instructions("Verify every supported fact.")
    assert CONSUMER_DYNAMIC_SKILL_BODY in instructions
    assert any("## Pydantic Wire Contract" in option for option in command)
    assert any("ConsumerAgentWireResult" in option for option in command)
    assert "## Runtime Invariants" in executor.prompts[0]


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


def test_retryable_consumer_run_resumes_its_own_session_after_conversation_advances(
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
    with pytest.raises(RuntimeError, match="codex_provider_unavailable"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=FailingExecutor(provider_failure),
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)
    store.upsert_conversation(
        task.conversation_id,
        task.conversation_title,
        task.single_chat,
        "session-new",
    )
    store.set_codex_session_contract_hash(
        task.conversation_id, consumer_wire_contract_hash()
    )
    executor = CapturingExecutor(_result_jsonl(session="session-old"))

    ConsumerAgentRunner(
        store=store,
        workspace=Path("/workspace"),
        executor=executor,
        codex_session_exists=lambda _: True,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    assert executor.commands[0][:3] == ["codex", "exec", "resume"]
    assert executor.commands[0][-2:] == ["session-old", "-"]
    assert store.get_codex_session_id(task.conversation_id) == "session-new"


def test_old_run_parse_failure_does_not_clear_newer_conversation_session(
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
    with pytest.raises(RuntimeError, match="codex_provider_unavailable"):
        ConsumerAgentRunner(
            store=store,
            workspace=Path("/workspace"),
            executor=FailingExecutor(provider_failure),
            codex_session_exists=lambda _: True,
        ).run(task, context, proposal_revision=0, parent_agent_run_id=None)
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

    assert store.get_codex_session_id(task.conversation_id) == "session-new"


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


def test_consumer_persists_reviewed_local_read_receipt(store, task, context):
    argv = ["sed", "-n", "1p", "/tmp/public-material"]
    descriptor = NativeCliMetadataClassifier(reviewed_effects={}).classify(
        {"type": "command_execution", "argv": argv}
    )
    assert descriptor is not None
    receipt = {
        "cli": descriptor.cli,
        "operation": descriptor.command_path,
        "operation_digest": descriptor.command_digest,
        "target_identifiers": descriptor.target_identifiers,
        "result_digest": "local-read-digest",
        "stdout": "verified material\n",
    }
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
                        "result": {"structuredContent": receipt},
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
        native_cli_classifier=NativeCliMetadataClassifier(reviewed_effects={}),
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    run = store.get_agent_run(result.run_id)
    assert run is not None
    assert [event["item"]["metadata"]["capability"] for event in run.tool_events] == [
        "agent_cli.local-shell",
        "agent_cli.local-shell",
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
