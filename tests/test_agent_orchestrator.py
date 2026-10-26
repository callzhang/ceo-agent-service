"""Current reviewed-candidate orchestration and technical recovery contracts."""

from collections import deque
from dataclasses import replace
import sqlite3
import hashlib
import json

import pytest

from app.agent_context import AgentTaskContext
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_orchestrator import AgentOrchestrator, _operation_id, _scheduled_consumer_work_receipts
from app.agent_reported_error import agent_error_payload
from app.agent_result import AgentError
from app.agent_turn_runner import AgentTurnRunResult
from app.dws_client import DwsError
from app.store import AgentRole, AutoReplyStore
from app.system_executor import ActionOutcome, SystemExecutor


def candidate(label="one", *, outcome="proposal"):
    values = dict(
        outcome=outcome, summary=label,
        proposal=(dict(objective="Notify", actions=[dict(
            description=label, action_identity="notice", capability="test",
            operation="act", target={"id": "object-1"}, payload={"value": label},
        )], sourced_facts=[], authored_judgment="Supported") if outcome == "proposal" else None),
        risk="low", confidence=1.0, rule_coverage=1.0,
        information_completeness=1.0,
        error=AgentError(code="", retryable=False),
    )
    return ConsumerAgentResult.model_validate(values)


def task_and_context(tmp_path, *, message_id="trigger"):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="conversation", conversation_title="Test",
        single_chat=True, trigger_message_id=message_id, trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00", trigger_text="Review",
    )
    task = store.claim_reply_tasks(1)[0]
    context = AgentTaskContext(
        task_id=task.id, channel=task.channel, conversation_id=task.conversation_id,
        conversation_title="Test", single_chat=True,
        trigger_message_id=task.trigger_message_id, trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00", trigger_text="Review",
        messages=(), materials=(), prior_receipts=(),
    )
    return store, task, context


class ScriptedConsumer:
    def __init__(self, store, *steps):
        self.store, self.steps, self.calls = store, deque(steps), []

    def run(self, task, context, *, proposal_revision, parent_agent_run_id, feedback=None):
        claim = self.store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id, task.execution_generation, role=AgentRole.CONSUMER,
                proposal_revision=proposal_revision,
            ), parent_agent_run_id=parent_agent_run_id, operation_id="", owner="consumer",
        )
        assert claim.claimed
        step = self.steps.popleft()
        if callable(step):
            step = step(context)
        if isinstance(step, AgentError):
            self.store.fail_agent_run(claim.run.id, step.model_dump(mode="json"), owner="consumer")
            result = None
        else:
            self.store.complete_agent_run(claim.run.id, step.model_dump(mode="json"), owner="consumer")
            result = step
        self.calls.append((claim.run.id, proposal_revision, parent_agent_run_id, feedback, context))
        return AgentTurnRunResult(claim.run.id, result, 0, 1)


class ScriptedAudit:
    def __init__(self, store, *steps):
        self.store, self.steps, self.calls = store, deque(steps), []

    def run(self, task, context, *, turn_attempt, parent_agent_run_id):
        claim = self.store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision, turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id, operation_id=context.operation_id,
            owner="audit",
        )
        assert claim.claimed
        step = self.steps.popleft()
        if isinstance(step, AgentError):
            self.store.fail_agent_run(claim.run.id, step.model_dump(mode="json"), owner="audit")
            result = None
        else:
            result = AuditAgentResult.model_validate(dict(
                outcome=step, summary=step, proposal_revision=context.proposal_revision,
                candidate_digest=context.candidate_digest, evidence_refs=[],
                feedback=(dict(rule="evidence", observation="Incomplete",
                               requested_revision="Replace candidate")
                          if step in ("return", "reject") else None),
                error=AgentError(code="", retryable=False), risk="low",
                confidence=1.0, rule_coverage=1.0, information_completeness=1.0,
            ))
            self.store.complete_agent_run(claim.run.id, result.model_dump(mode="json"), owner="audit")
        self.calls.append((claim.run.id, turn_attempt, parent_agent_run_id, context))
        return AgentTurnRunResult(claim.run.id, result, 0, 1)


class VerifiedHandler:
    def __init__(self):
        self.calls = []

    def dispatch(self, action, *, action_key, candidate):
        self.calls.append((action.action_identity, action.payload))
        return ActionOutcome("verified", {"provider_receipt_id": action_key})

    def reconcile(self, action, *, action_key, candidate):
        return None


def orchestrator(store, consumer, audit, handler=None):
    handler = handler or VerifiedHandler()
    return AgentOrchestrator(
        store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {("test", "act"): handler}, owner="test-executor"),
    ), handler


@pytest.mark.parametrize("outcome", ["no_action", "proposal"])
def test_persisted_terminal_read_needs_no_context_or_new_effect(tmp_path, outcome):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate(outcome=outcome))
    audit = ScriptedAudit(store, "approve")
    runner, handler = orchestrator(store, consumer, audit)
    assert runner.persisted_terminal_result(task) is None
    assert not consumer.calls and not audit.calls and not handler.calls
    result = runner.process(task, context, refresh_context=lambda: context)
    calls = (len(consumer.calls), len(audit.calls), len(handler.calls))
    terminal = runner.persisted_terminal_result(task)
    assert terminal == result
    assert (len(consumer.calls), len(audit.calls), len(handler.calls)) == calls
    assert runner.persisted_terminal_result(
        task.model_copy(update={"execution_generation": "new-unprocessed-generation"})
    ) is None
    assert (len(consumer.calls), len(audit.calls), len(handler.calls)) == calls


def test_scheduled_audit_receives_compact_service_report_receipt_only():
    task = type("Task", (), {"id": 42, "channel": "scheduled", "execution_generation": "generation-1"})()
    run = type("Run", (), {
        "id": 77,
        "reply_task_id": 42,
        "execution_generation": "generation-1",
        "role": AgentRole.CONSUMER,
        "status": "completed",
        "tool_events": [{
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call", "server": "agent_cli",
                "tool": "consumer_document_write", "id": "write-1",
                "arguments": {"content": "report body"},
                "result": {"structuredContent": {
                    "ok": True, "status": "success",
                    "operation": "updated", "node_id": "doc-7", "title": "Report",
                    "provider_result": {"nodeId": "doc-7"},
                    "readback": None,
                }},
            },
        }],
    })()

    receipts = _scheduled_consumer_work_receipts(task, run)

    assert receipts == ({
        "consumer_run_id": 77, "tool_call_id": "write-1",
        "operation": "updated", "document_node_id": "doc-7",
        "document_title": "Report",
        "provider_result_sha256": hashlib.sha256(b'{"nodeId":"doc-7"}').hexdigest(),
        "provider_result_bytes": len(b'{"nodeId":"doc-7"}'),
    },)
    assert "report body" not in str(receipts)


@pytest.mark.parametrize("text_result", [False, True])
def test_scheduled_audit_reads_claude_text_tool_return_without_report_body(text_result):
    task = type("Task", (), {"id": 42, "channel": "scheduled", "execution_generation": "generation-1"})()
    returned = {
        "ok": True, "status": "success",
        "operation": "created", "node_id": "doc-8", "title": "Weekly Report",
        "provider_result": {"nodeId": "doc-8"},
        "readback": None,
    }
    run = type("Run", (), {
        "id": 78, "reply_task_id": 42, "execution_generation": "generation-1",
        "role": AgentRole.CONSUMER, "status": "completed",
        "tool_events": [{"type": "item.completed", "item": {
            "type": "mcp_tool_call", "server": "agent_cli",
            "tool": "consumer_document_write", "id": "write-2",
            "result": (
                json.dumps(returned) if text_result else
                {"content": [{"type": "text", "text": json.dumps(returned)}]}
            ),
        }}],
    })()

    [receipt] = _scheduled_consumer_work_receipts(task, run)
    assert receipt["consumer_run_id"] == 78
    assert receipt["operation"] == "created"
    assert receipt["document_node_id"] == "doc-8"
    assert "private synthetic weekly report" not in str(receipt)


def test_scheduled_receipts_ignore_other_server_and_failed_tool_result():
    task = type("Task", (), {"id": 42, "channel": "scheduled", "execution_generation": "generation-1"})()
    item = {
        "type": "mcp_tool_call", "tool": "consumer_document_write",
        "id": "write-1", "arguments": {"content": "report body"},
        "result": {"structuredContent": {
            "ok": True, "status": "success",
                    "operation": "updated", "node_id": "doc-7", "title": "Report",
            "provider_result": {"nodeId": "doc-7"},
            "readback": None,
        }},
    }
    run = type("Run", (), {
        "id": 77, "reply_task_id": 42, "execution_generation": "generation-1",
        "role": AgentRole.CONSUMER, "status": "completed",
        "tool_events": [
            {"type": "item.completed", "item": {**item, "server": "other_server"}},
            {"type": "item.completed", "item": {**item, "server": "agent_cli", "result": {**item["result"], "isError": True}}},
            {"type": "item.completed", "item": {**item, "server": "agent_cli", "result": {
                "content": [{"type": "text", "text": "provider failed: no document"}],
            }}},
        ],
    })()

    assert _scheduled_consumer_work_receipts(task, run) == ()


@pytest.mark.parametrize("channel,status,tool,task_id,generation,role", [
    ("dingtalk", "completed", "consumer_document_write", 42, "generation-1", AgentRole.CONSUMER),
    ("scheduled", "failed", "consumer_document_write", 42, "generation-1", AgentRole.CONSUMER),
    ("scheduled", "completed", "other_tool", 42, "generation-1", AgentRole.CONSUMER),
    ("scheduled", "completed", "consumer_document_write", 43, "generation-1", AgentRole.CONSUMER),
    ("scheduled", "completed", "consumer_document_write", 42, "stale-generation", AgentRole.CONSUMER),
    ("scheduled", "completed", "consumer_document_write", 42, "generation-1", AgentRole.AUDIT),
])
def test_scheduled_audit_receipt_projection_excludes_unbound_events(
    channel, status, tool, task_id, generation, role,
):
    task = type("Task", (), {"id": 42, "channel": channel, "execution_generation": "generation-1"})()
    run = type("Run", (), {
        "id": 77,
        "reply_task_id": task_id,
        "execution_generation": generation,
        "role": role,
        "status": status,
        "tool_events": [{
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call", "server": "agent_cli", "tool": tool,
                "id": "write-1", "arguments": {"content": "x"},
                "result": {"structuredContent": {
                    "ok": True, "status": "success",
                    "operation": "updated", "node_id": "doc-7", "title": "Report",
                    "readback": {"content": "x"},
                }},
            },
        }],
    })()

    assert _scheduled_consumer_work_receipts(task, run) == ()


def test_approved_candidate_executes_after_audit_and_persists_receipt(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate())
    audit = ScriptedAudit(store, "approve")
    driver, handler = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert len(consumer.calls) == len(audit.calls) == len(handler.calls) == 1
    assert result.execution_result.completed_action_keys == ("notice",)
    assert store.get_candidate_execution(result.candidate_id)["status"] == "done"
    assert len(store.list_agent_runs_for_task_generation(task.id, task.execution_generation)) == 2
    resumed = driver.process(task, context, refresh_context=lambda: context)
    assert resumed.status == "executed"
    assert len(handler.calls) == 1


def test_audit_return_creates_append_only_consumer_revision_and_preserves_feedback(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate("first"), candidate("corrected"))
    audit = ScriptedAudit(store, "return", "approve")
    driver, handler = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert [call[1] for call in consumer.calls] == [0, 1]
    assert consumer.calls[1][3].observation == "Incomplete"
    assert len(store.list_agent_runs_for_task_generation(task.id, task.execution_generation)) == 4
    assert handler.calls == [("notice", {"value": "corrected"})]


def test_audit_reject_requires_changed_replacement(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate("same"), candidate("same"))
    audit = ScriptedAudit(store, "reject")
    driver, handler = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_terminal"
    assert result.error.code == "rejected_candidate_unchanged"
    assert handler.calls == []


def test_uncertain_dingtalk_send_returns_to_consumer_for_receipt_followup(tmp_path):
    store, task, context = task_and_context(tmp_path)
    proposal = ConsumerAgentResult.model_validate({
        **candidate().model_dump(mode="python"),
        "proposal": {
            "objective": "Send the approved update",
            "actions": [{
                "description": "Send the approved update",
                "action_identity": "oa-update",
                "capability": "dingtalk-chat",
                "operation": "send_group_message",
                "target": {"conversation_id": "conversation"},
                "payload": {"body": "Approved update"},
            }],
            "sourced_facts": [],
            "authored_judgment": "Supported",
        },
    })
    no_action = candidate(outcome="no_action")
    consumer = ScriptedConsumer(store, proposal, *(no_action for _ in range(3)))
    audit = ScriptedAudit(store, *("approve" for _ in range(4)))

    class UncertainOnce(VerifiedHandler):
        def dispatch(self, action, *, action_key, candidate):
            self.calls.append((action.action_identity, action.payload))
            return ActionOutcome("uncertain", {"receipt_state": "unknown"})

    handler = UncertainOnce()
    runtime = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        system_executor=SystemExecutor(
            store,
            {("dingtalk-chat", "send_group_message"): handler},
            owner="test-executor",
        ),
    )

    result = runtime.process(task, context, refresh_context=lambda: context)

    assert result.status == "failed_terminal"
    assert result.error.code == "external_action_uncertain"
    assert store.get_reply_task(task.id).status == "processing"
    assert len(handler.calls) == 1
    assert len(consumer.calls) == 4
    followup = consumer.calls[1]
    assert followup[1] == 1
    assert followup[3] is not None
    assert "complete" in followup[3].observation
    assert "never claim sent" in followup[3].requested_revision
    uncertain_receipts = [
        receipt for receipt in followup[4].prior_receipts
        if receipt.operation == "dingtalk_message_delivery_verification"
    ]
    assert len(uncertain_receipts) == 1
    assert uncertain_receipts[0].completed is False
    assert all(call[3] is not None for call in consumer.calls[2:])


@pytest.mark.parametrize("code", ["codex_provider_capacity_exhausted", "runtime_provider_unreachable"])
def test_waiting_consumer_capacity_defers_without_same_process_retry(tmp_path, code):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, AgentError(code=code, retryable=True))
    audit = ScriptedAudit(store)
    driver, handler = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_retryable"
    assert len(consumer.calls) == 1
    assert len(audit.calls) == len(handler.calls) == 0


def test_retryable_consumer_error_creates_new_turn_without_spending_feedback(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, AgentError(code="temporary_read_error", retryable=True), candidate())
    audit = ScriptedAudit(store, "approve")
    driver, _ = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert result.feedback_cycles == 0
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    assert [run.turn_attempt for run in runs if run.role is AgentRole.CONSUMER] == [0, 1]


def test_nonretryable_authorization_failure_stays_technical(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, AgentError(
        code="authorization_required", retryable=False, authorization_required=True,
    ))
    driver, _ = orchestrator(store, consumer, ScriptedAudit(store))
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_terminal"
    assert result.error.authorization_required is True
    assert store.current_reviewed_candidate(task.id, task.execution_generation) is None


def test_missing_declared_operation_stops_after_one_consumer_turn(tmp_path):
    store, task, context = task_and_context(tmp_path)
    error = AgentError.model_validate(
        agent_error_payload("operation_unavailable", failed=True)
    )
    consumer = ScriptedConsumer(store, error)
    audit = ScriptedAudit(store)
    driver, handler = orchestrator(store, consumer, audit)

    result = driver.process(task, context, refresh_context=lambda: context)

    assert result.status == "failed_terminal"
    assert result.error.code == "operation_unavailable"
    assert len(consumer.calls) == 1
    assert audit.calls == handler.calls == []


def test_technical_failure_keeps_structured_provider_source_code(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, AgentError(
        code="consumer_runtime_failed", retryable=False,
        source="codex", source_code="NATIVE_PROVIDER_REFUSAL",
    ))
    driver, _ = orchestrator(store, consumer, ScriptedAudit(store))
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_terminal"
    assert result.error.code == "consumer_runtime_failed"
    assert result.error.source == "codex"
    assert result.error.source_code == "NATIVE_PROVIDER_REFUSAL"


def test_completed_history_survives_store_reopen_without_reexecution(tmp_path):
    store, task, context = task_and_context(tmp_path)
    handler = VerifiedHandler()
    driver, _ = orchestrator(
        store, ScriptedConsumer(store, candidate()), ScriptedAudit(store, "approve"), handler,
    )
    first = driver.process(task, context, refresh_context=lambda: context)
    reopened = AutoReplyStore(store.path)
    second_driver, _ = orchestrator(
        reopened, ScriptedConsumer(reopened), ScriptedAudit(reopened), handler,
    )
    second = second_driver.process(task, context, refresh_context=lambda: context)
    assert first.status == second.status == "executed"
    assert first.candidate_id == second.candidate_id
    assert len(handler.calls) == 1


def test_retryable_audit_failure_appends_attempt_and_keeps_consumer_candidate(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate())
    audit = ScriptedAudit(store, AgentError(code="temporary_audit_failure", retryable=True), "approve")
    driver, _ = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert len(consumer.calls) == 1
    assert [call[1] for call in audit.calls] == [0, 1]
    assert result.feedback_cycles == 0


def test_audit_provider_capacity_waits_without_new_attempt(tmp_path):
    store, task, context = task_and_context(tmp_path)
    audit = ScriptedAudit(store, AgentError(
        code="codex_provider_capacity_exhausted", retryable=True,
    ))
    driver, handler = orchestrator(store, ScriptedConsumer(store, candidate()), audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_retryable"
    assert len(audit.calls) == 1
    assert handler.calls == []


def test_active_audit_lease_defers_and_expiry_creates_append_only_attempt(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(store, candidate())
    consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    first_consumer = consumer.calls[0][0]
    active = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=first_consumer,
        operation_id=_operation_id(task, 0), owner="stalled-audit",
    ).run
    audit = ScriptedAudit(store, "approve")
    driver, _ = orchestrator(store, consumer, audit)
    waiting = driver.process(task, context, refresh_context=lambda: context)
    assert waiting.status == "failed_retryable"
    assert len(audit.calls) == 0
    with sqlite3.connect(store.path) as db:
        db.execute("update runs set lease_expires_at='2000-01-01 00:00:00' where id=?", (active.id,))
    resumed = driver.process(task, context, refresh_context=lambda: context)
    assert resumed.status == "executed"
    assert [run.turn_attempt for run in store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation,
    ) if run.role is AgentRole.AUDIT] == [0, 1]
    assert store.get_agent_run(active.id).status == "failed"


def test_context_is_refreshed_between_consumer_and_audit(tmp_path):
    store, task, context = task_and_context(tmp_path)
    fresh = replace(context, trigger_text="new fact")
    calls = []
    def refresh():
        calls.append(1)
        return context if len(calls) == 1 else fresh
    audit = ScriptedAudit(store, "approve")
    driver, _ = orchestrator(store, ScriptedConsumer(store, candidate()), audit)
    assert driver.process(task, context, refresh_context=refresh).status == "executed"
    assert audit.calls[0][3].task.trigger_text == "new fact"


@pytest.mark.parametrize("retryable", [False, True])
def test_context_refresh_failure_before_audit_preserves_completed_consumer(tmp_path, retryable):
    store, task, context = task_and_context(tmp_path)
    calls = []
    def refresh():
        calls.append(1)
        if len(calls) > 1:
            raise DwsError("Source unavailable; Authorization: Bearer test-source-secret", code="DWS_SOURCE_UNAVAILABLE", retryable_external_dependency=retryable)
        return context
    consumer = ScriptedConsumer(store, candidate())
    audit = ScriptedAudit(store, "approve")
    driver, _ = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=refresh)
    assert result.status == ("failed_retryable" if retryable else "failed_terminal")
    assert result.error.retryable is retryable
    assert result.error.code == "agent_context_refresh_failed"
    assert result.error.source_code == "DWS_SOURCE_UNAVAILABLE"
    assert "test-source-secret" not in result.summary
    assert len(consumer.calls) == 1 and len(audit.calls) == 0


def test_two_tasks_can_share_runner_without_crossing_parent_or_context(tmp_path):
    store, first, first_context = task_and_context(tmp_path, message_id="first")
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="conversation", conversation_title="Test",
        single_chat=True, trigger_message_id="second", trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:01", trigger_text="Second",
    )
    second = store.claim_reply_tasks(1)[0]
    second_context = replace(first_context, task_id=second.id,
                             trigger_message_id=second.trigger_message_id, trigger_text="Second")
    consumer = ScriptedConsumer(store, candidate("first"), candidate("second"))
    audit = ScriptedAudit(store, "approve", "approve")
    driver, handler = orchestrator(store, consumer, audit)
    assert driver.process(first, first_context, refresh_context=lambda: first_context).status == "executed"
    assert driver.process(second, second_context, refresh_context=lambda: second_context).status == "executed"
    assert consumer.calls[0][4].task_id == first.id
    assert consumer.calls[1][4].task_id == second.id
    assert len(handler.calls) == 2


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("temporary_read_error", "failed_terminal"),
        ("runtime_provider_unreachable", "failed_terminal"),
        ("codex_provider_capacity_exhausted", "failed_terminal"),
        ("dependency_read_unavailable", "failed_terminal"),
            ("agent_context_refresh_failed", "failed_terminal"),
    ],
)
@pytest.mark.parametrize("role", [AgentRole.CONSUMER, AgentRole.AUDIT])
def test_four_persisted_role_failures_prevent_another_provider_turn(
    tmp_path, code, expected_status, role
):
    store, task, context = task_and_context(tmp_path)
    parent = None
    if role is AgentRole.AUDIT:
        parent = ScriptedConsumer(store, candidate()).run(task, context, proposal_revision=0, parent_agent_run_id=None).run_id
    for turn in range(4):
        claim = store.claim_agent_run(task.id, task.execution_generation, role=role,
            proposal_revision=0, turn_attempt=turn, parent_agent_run_id=parent,
            operation_id=_operation_id(task, 0) if role is AgentRole.AUDIT else '', owner='test')
        assert claim.claimed
        store.fail_agent_run(claim.run.id, AgentError(code=code, retryable=True, source_code='ROOT_CAUSE').model_dump(mode='json'), owner='test')
    driver, _ = orchestrator(store, ScriptedConsumer(store), ScriptedAudit(store))
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == expected_status
    assert result.error.code == code
    assert result.error.source_code == "ROOT_CAUSE"
    if expected_status == "failed_retryable":
        assert "ROOT_CAUSE" in result.summary
    assert result.error.retryable is (expected_status == "failed_retryable")
    assert len(store.list_agent_runs_for_task_generation(task.id, task.execution_generation)) == (
        5 if role is AgentRole.AUDIT else 4
    )


@pytest.mark.parametrize(
    ("error_code", "failed_turns", "expected_delay"),
    [
        ("agent_context_refresh_failed", 2, 120),
    ],
)
def test_dependency_read_wait_backoff_increases_without_exhausting_task(
    tmp_path, error_code, failed_turns, expected_delay
):
    store, task, context = task_and_context(tmp_path)
    latest = None
    for turn in range(failed_turns):
        claim = store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=turn,
            parent_agent_run_id=None,
            operation_id="",
            owner=f"dependency-{turn}",
        )
        assert claim.claimed
        store.fail_agent_run(
            claim.run.id,
            AgentError(
                code=error_code,
                retryable=True,
                source_code="forbidden.accessDenied",
            ).model_dump(mode="json"),
            owner=f"dependency-{turn}",
        )
        latest = store.get_agent_run(claim.run.id, load_events=False)

    driver, _ = orchestrator(store, ScriptedConsumer(store), ScriptedAudit(store))
    deferred = driver._technical_state(task, latest, 0)
    result = driver._deferred_result(deferred)

    assert result.status == "failed_retryable"
    assert result.error.code == error_code
    assert result.error.source_code == "forbidden.accessDenied"
    assert result.retry_after_seconds == expected_delay
