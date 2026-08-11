from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_orchestrator import AgentOrchestrator
from app.agent_result import SideEffectState
from app.agent_turn_runner import AgentTurnRunResult
from app.store import AgentRole, AutoReplyStore
from tests.support.audit_sink_mcp import AuditSink


@dataclass(frozen=True)
class EvalCase:
    id: str
    trigger: str
    candidate: str
    operation: str
    consumer_outcome: str
    audit_outcome: str
    allows_write: bool
    reason: str
    requires_oa_live_detail: bool = False
    requires_applicant_notification: bool = False


def _cases() -> tuple[EvalCase, ...]:
    path = Path(__file__).parent / "fixtures" / "consumer_audit_cases.json"
    return tuple(EvalCase(**item) for item in json.loads(path.read_text()))


def _operation_name(case: EvalCase) -> str:
    return f"eval.{case.operation}"


def _consumer_result(case: EvalCase) -> ConsumerAgentResult:
    proposal = None
    decision_options: list[dict[str, str]] = []
    if case.consumer_outcome == "proposal":
        proposal = {
            "objective": case.trigger,
            "actions": [
                {
                    "description": case.candidate,
                    "capability": "audit_sink",
                    "operation": _operation_name(case),
                    "target": {"case_id": case.id},
                    "payload": {"candidate": case.candidate},
                    "expected_verification": "Read the controlled sink by operation ID.",
                }
            ],
            "sourced_facts": [
                {"assertion": case.trigger, "references": [f"eval:{case.id}"]}
            ],
            "authored_judgment": case.reason,
        }
    elif case.consumer_outcome == "needs_human":
        decision_options = [
            {
                "key": "A",
                "label": "Proceed with the first supported management choice",
                "instruction": "Execute the first evidence-supported management choice.",
                "consequence": "The agent will execute and verify that choice.",
            },
            {
                "key": "B",
                "label": "Proceed with the second supported management choice",
                "instruction": "Execute the second evidence-supported management choice.",
                "consequence": "The agent will execute and verify the alternative.",
            },
        ]
    return ConsumerAgentResult.model_validate(
        {
            "outcome": case.consumer_outcome,
            "summary": case.reason,
            "proposal": proposal,
            "decision_options": decision_options,
            "error": {"code": "", "retryable": False, "authorization_required": False},
        }
    )


def _audit_result(case: EvalCase, operation_id: str) -> AuditAgentResult:
    payload: dict[str, object] = {
        "outcome": case.audit_outcome,
        "summary": case.reason,
        "proposal_revision": 0,
        "side_effect_state": "none",
        "feedback": None,
        "external_result": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    }
    if case.audit_outcome == "executed":
        payload.update(
            side_effect_state="confirmed",
            external_result={
                "operation_id": operation_id,
                "verification_summary": "Controlled sink readback matched the operation ID.",
                "live_result_reference": {"operation_id": operation_id},
            },
        )
    elif case.audit_outcome == "revision_required":
        payload["feedback"] = {
            "rule": "authority boundary",
            "observation": case.reason,
            "requested_revision": "Return a candidate limited to the established facts.",
        }
    return AuditAgentResult.model_validate(payload)


class FixtureConsumer:
    def __init__(self, store: AutoReplyStore, case: EvalCase) -> None:
        self.store = store
        self.case = case
        self.session_ids: list[str] = []

    def run(self, task, context, *, proposal_revision, parent_agent_run_id, feedback=None):
        del context, feedback
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=0,
            parent_agent_run_id=parent_agent_run_id,
            operation_id="",
            owner="fixture-consumer",
        )
        assert claim.claimed
        session_id = self.store.get_codex_session_id(task.conversation_id) or "consumer-session"
        self.store.upsert_conversation(
            task.conversation_id, task.conversation_title, task.single_chat, session_id
        )
        self.store.set_agent_run_session(claim.run.id, session_id, owner="fixture-consumer")
        result = _consumer_result(self.case)
        self.store.complete_agent_run(
            claim.run.id, result.model_dump(mode="json"), owner="fixture-consumer"
        )
        self.session_ids.append(session_id)
        return AgentTurnRunResult(claim.run.id, result, 0, 1)


class FixtureAudit:
    def __init__(self, store: AutoReplyStore, case: EvalCase, sink: AuditSink) -> None:
        self.store = store
        self.case = case
        self.sink = sink
        self.session_ids: list[str] = []
        self.live_oa_detail_reads: list[str] = []
        self.applicant_notifications: list[str] = []

    def run(self, task, context: AuditTurnContext, *, turn_attempt, parent_agent_run_id):
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner="fixture-audit",
        )
        assert claim.claimed
        session_id = f"audit-session-{claim.run.id}"
        self.store.set_agent_run_session(claim.run.id, session_id, owner="fixture-audit")
        if self.case.requires_oa_live_detail:
            self.live_oa_detail_reads.append(context.operation_id)
        if self.case.allows_write:
            record = self.sink.write_state(
                context.operation_id,
                {"case_id": self.case.id, "candidate": self.case.candidate},
            )
            assert record.operation_id == context.operation_id
            if self.case.requires_applicant_notification:
                self.applicant_notifications.append(context.operation_id)
        result = _audit_result(self.case, context.operation_id)
        self.store.complete_agent_run(
            claim.run.id,
            result.model_dump(mode="json"),
            owner="fixture-audit",
            side_effect_state=result.side_effect_state.value,
        )
        self.session_ids.append(session_id)
        return AgentTurnRunResult(claim.run.id, result, 0, 1)

    def recover(self, task, context, *, run):
        raise AssertionError("fixture eval does not enter unknown recovery")

    def execute_recovery(self, task, context, *, run):
        raise AssertionError("fixture eval does not execute recovery")


def _task_context(store: AutoReplyStore, case: EvalCase):
    store.enqueue_reply_task(
        conversation_id=f"eval-{case.id}",
        conversation_title="Consumer Audit Eval",
        single_chat=True,
        trigger_message_id=f"message-{case.id}",
        trigger_create_time="2026-08-07 10:00:00",
        trigger_sender="Eval Sender",
        trigger_text=case.trigger,
        execution_generation=f"generation-{case.id}",
    )
    task = store.get_reply_task_for_message(f"eval-{case.id}", f"message-{case.id}")
    assert task is not None
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
    return task, context


def _contains_production_identifier(value: str) -> bool:
    return any(marker in value for marker in ("@", "http://", "https://", "cid", "msg"))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case.id)
def test_eval_fixture_has_complete_sanitized_authority_expectation(case: EvalCase):
    assert case.id and case.trigger and case.candidate and case.reason
    assert case.consumer_outcome in {"proposal", "needs_human", "no_action"}
    assert case.audit_outcome in {"executed", "revision_required", "needs_human"}
    assert not _contains_production_identifier(json.dumps(case.__dict__, ensure_ascii=False))
    assert case.operation in {
        "dingtalk_send", "oa_action", "oa_comment", "document_edit",
        "mail_reply", "message_reaction", "memory_write",
    }
    assert not case.requires_applicant_notification or case.requires_oa_live_detail


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case.id)
def test_eval_cases_traverse_orchestration_with_exactly_the_expected_write(case: EvalCase, tmp_path: Path):
    store = AutoReplyStore(tmp_path / f"{case.id}.sqlite3")
    sink = AuditSink(tmp_path / "audit-sink.sqlite3")
    task, context = _task_context(store, case)
    consumer = FixtureConsumer(store, case)
    audit = FixtureAudit(store, case, sink)

    result = AgentOrchestrator(store=store, consumer=consumer, audit=audit).process(
        task, context, refresh_context=lambda: context
    )

    if case.consumer_outcome == "needs_human":
        assert result.status == "needs_human"
        assert audit.session_ids == []
        assert sink.row_count(f"agent-task:{task.id}:{task.execution_generation}:proposal:0") == 0
        return
    if case.consumer_outcome == "no_action":
        assert result.status == "no_action"
        assert audit.session_ids == []
        assert sink.row_count(f"agent-task:{task.id}:{task.execution_generation}:proposal:0") == 0
        return
    if case.audit_outcome == "executed":
        assert result.status == "executed"
        assert result.final_role is AgentRole.AUDIT
        assert sink.row_count(f"agent-task:{task.id}:{task.execution_generation}:proposal:0") == 1
        assert consumer.session_ids == ["consumer-session"]
        assert len(audit.session_ids) == 1
        assert audit.session_ids[0] != consumer.session_ids[0]
        assert audit.live_oa_detail_reads == (
            [f"agent-task:{task.id}:{task.execution_generation}:proposal:0"]
            if case.requires_oa_live_detail
            else []
        )
        assert audit.applicant_notifications == (
            [f"agent-task:{task.id}:{task.execution_generation}:proposal:0"]
            if case.requires_applicant_notification
            else []
        )
        return
    assert result.status == "needs_human"
    assert sink.row_count(f"agent-task:{task.id}:{task.execution_generation}:proposal:0") == 0
    expected_oa_reads = (
        [
            f"agent-task:{task.id}:{task.execution_generation}:proposal:{revision}"
            for revision in range(3)
        ]
        if case.requires_oa_live_detail
        else []
    )
    assert audit.live_oa_detail_reads == expected_oa_reads
    assert audit.applicant_notifications == []


def test_controlled_sink_keeps_first_write_for_exact_operation_id(tmp_path: Path):
    sink = AuditSink(tmp_path / "audit-sink.sqlite3")

    first = sink.write_state("operation-1", {"content": "first"})
    second = sink.write_state("operation-1", {"content": "changed"})

    assert first.payload == {"content": "first"}
    assert second.payload == {"content": "first"}
    assert sink.read_state("operation-1") == first
    assert sink.row_count("operation-1") == 1


def test_controlled_sink_stdio_mcp_exposes_idempotent_read_and_write_tools(tmp_path: Path):
    sink_path = tmp_path / "audit-sink.sqlite3"
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.support.audit_sink_mcp", str(sink_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None

    def request(identifier: int, method: str, params: dict[str, object]) -> dict[str, object]:
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())

    try:
        assert request(1, "initialize", {})["result"]["capabilities"] == {"tools": {}}
        tools = request(2, "tools/list", {})["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"read_state", "write_state"}
        first = request(
            3,
            "tools/call",
            {"name": "write_state", "arguments": {"operation_id": "op-1", "payload": {"v": 1}}},
        )
        second = request(
            4,
            "tools/call",
            {"name": "write_state", "arguments": {"operation_id": "op-1", "payload": {"v": 2}}},
        )
        assert first["result"] == second["result"]
        assert "\"v\": 1" in request(
            5, "tools/call", {"name": "read_state", "arguments": {"operation_id": "op-1"}}
        )["result"]["content"][0]["text"]
    finally:
        process.terminate()
        process.wait(timeout=5)
