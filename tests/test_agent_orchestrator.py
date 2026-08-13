import sqlite3
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_context import AgentContextMessage, AgentTaskContext, AuditTurnContext
from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_orchestrator import AgentOrchestrator, _NextAudit
from app.agent_result import ResultParseError, SideEffectState
from app.agent_skill_usage import LoadedSkillReceipt
from app.agent_turn_runner import AgentTurnRunResult
from app.dws_client import DwsError
from app.store import AgentRole, AutoReplyStore


def _consumer_result(outcome: str, label: str = "candidate") -> ConsumerAgentResult:
    proposal = None
    if outcome == "proposal":
        proposal = {
            "objective": label,
            "actions": [
                {
                    "description": label,
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
                            label,
                            "--yes",
                        ]
                    },
                    "expected_verification": "Read the live message.",
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "",
        }
    return ConsumerAgentResult.model_validate(
        {
            "outcome": outcome,
            "summary": label,
            "proposal": proposal,
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        }
    )


def _audit_result(
    outcome: str,
    revision: int,
    *,
    code: str = "",
    retryable: bool = False,
    authorization_required: bool = False,
) -> AuditAgentResult:
    feedback = None
    external_result = None
    side_effect_state = SideEffectState.NONE
    if outcome == "revision_required":
        feedback = {
            "rule": "current evidence",
            "observation": f"revision {revision} is stale",
            "requested_revision": "Return a complete replacement proposal.",
        }
    elif outcome == "executed":
        side_effect_state = SideEffectState.CONFIRMED
        external_result = {
            "operation_id": "filled-by-runner",
            "verification_summary": "Verified from live state.",
            "live_result_reference": {"id": f"result-{revision}"},
        }
    return AuditAgentResult.model_validate(
        {
            "outcome": outcome,
            "summary": outcome,
            "proposal_revision": revision,
            "side_effect_state": side_effect_state.value,
            "feedback": feedback,
            "external_result": external_result,
            "error": {
                "code": code,
                "retryable": retryable,
                "authorization_required": authorization_required,
            },
        }
    )


class ScriptedConsumer:
    def __init__(self, store: AutoReplyStore, *results: ConsumerAgentResult) -> None:
        self.store = store
        self.results = deque(results)
        self.calls = []
        self.owner = "scripted-consumer"

    def run(
        self,
        task,
        context,
        *,
        proposal_revision,
        parent_agent_run_id,
        feedback=None,
    ):
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=proposal_revision,
            ),
            parent_agent_run_id=parent_agent_run_id,
            operation_id="",
            owner=self.owner,
        )
        assert claim.claimed
        session_id = self.store.get_codex_session_id(task.conversation_id)
        if session_id is None:
            session_id = "consumer-session"
            self.store.upsert_conversation(
                task.conversation_id,
                task.conversation_title,
                task.single_chat,
                session_id,
            )
        self.store.set_agent_run_session(
            claim.run.id,
            session_id,
            owner=self.owner,
        )
        result = self.results.popleft()
        for event in getattr(self, "tool_events", ()):
            self.store.append_agent_run_event(
                claim.run.id,
                event,
                owner=self.owner,
            )
        if result.outcome is ConsumerOutcome.FAILED:
            self.store.fail_agent_run(
                claim.run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
            )
        else:
            self.store.complete_agent_run(
                claim.run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
            )
        self.calls.append(
            {
                "run_id": claim.run.id,
                "revision": proposal_revision,
                "parent": parent_agent_run_id,
                "feedback": feedback,
                "session_id": session_id,
                "context": context,
            }
        )
        return AgentTurnRunResult(claim.run.id, result, 0, 1)


class ReceiptScriptedConsumer(ScriptedConsumer):
    def __init__(self, store, receipt, *results):
        super().__init__(store, *results)
        self.receipts = receipt if isinstance(receipt, tuple) else (receipt,)
        self.tool_events = tuple(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "id": f"skill-read-{index}",
                    "server": "agent_cli",
                    "tool": "read_skill",
                    "status": "completed",
                    "metadata": {
                        "effect": "read_only",
                        "skill_name": item.name,
                        "skill_path": item.path,
                        "skill_sha256": item.sha256,
                    },
                },
            }
            for index, item in enumerate(self.receipts)
        )


class ScriptedAudit:
    def __init__(self, store: AutoReplyStore, *results: AuditAgentResult) -> None:
        self.store = store
        self.results = deque(results)
        self.calls = []
        self.owner = "scripted-audit"

    def run(self, task, context, *, turn_attempt, parent_agent_run_id):
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner=self.owner,
        )
        assert claim.claimed
        session_id = claim.run.codex_session_id or f"audit-session-{len(self.calls)}"
        if not claim.run.codex_session_id:
            self.store.set_agent_run_session(
                claim.run.id,
                session_id,
                owner=self.owner,
            )
        result = self.results.popleft()
        if result.outcome is AuditOutcome.EXECUTED:
            result = result.model_copy(
                update={
                    "external_result": result.external_result.model_copy(
                        update={"operation_id": context.operation_id}
                    )
                }
            )
        if result.outcome is AuditOutcome.FAILED:
            self.store.fail_agent_run(
                claim.run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
            )
        else:
            self.store.complete_agent_run(
                claim.run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=result.side_effect_state.value,
            )
        self.calls.append(
            {
                "run_id": claim.run.id,
                "revision": context.proposal_revision,
                "turn_attempt": turn_attempt,
                "operation_id": context.operation_id,
                "session_id": session_id,
                "proposal": context.proposal,
                "context": context,
            }
        )
        return AgentTurnRunResult(claim.run.id, result, 0, 1)

    def recover(self, task, context, *, run):
        raise AssertionError(f"unexpected recovery for run {run.id}")

    def execute_recovery(self, task, context, *, run):
        raise AssertionError(f"unexpected recovery execution for run {run.id}")


class RecoveringScriptedAudit(ScriptedAudit):
    def __init__(self, store, result):
        super().__init__(store)
        self.recovery_result = result
        self.recovery_calls = []
        self.recovery_contexts = []

    def recover(self, task, context, *, run):
        claim = self.store.claim_unknown_agent_run(
            run.id,
            owner=self.owner,
        )
        assert claim.claimed
        result = self.recovery_result
        if result.outcome is AuditOutcome.EXECUTED:
            result = result.model_copy(
                update={
                    "external_result": result.external_result.model_copy(
                        update={"operation_id": run.operation_id}
                    )
                }
            )
            side_effect_state = "confirmed"
        else:
            side_effect_state = "unknown"
        completed = self.store.complete_agent_run(
            run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state=side_effect_state,
            expected_status="unknown",
        )
        self.recovery_calls.append(
            {
                "run_id": run.id,
                "session_id": run.codex_session_id,
                "operation_id": context.operation_id,
                "revision": context.proposal_revision,
            }
        )
        self.recovery_contexts.append(context)
        return AgentTurnRunResult(completed.id, result, 1, 2)


class ParseFailingEffectfulAudit(ScriptedAudit):
    def run(self, task, context, *, turn_attempt, parent_agent_run_id):
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner=self.owner,
        )
        assert claim.claimed
        self.store.set_agent_run_session(
            claim.run.id,
            "audit-session-with-effect",
            owner=self.owner,
        )
        self.store.mark_agent_run_unknown(
            claim.run.id,
            {"code": "codex_result_invalid", "retryable": True},
            owner=self.owner,
        )
        raise ResultParseError("latest agent result candidate is malformed")


class TwoPhaseScriptedAudit(ScriptedAudit):
    def __init__(self, store):
        super().__init__(store)
        self.recovery_calls = 0
        self.execute_calls = 0

    def recover(self, task, context, *, run):
        self.recovery_calls += 1
        claim = self.store.claim_unknown_agent_run(run.id, owner=self.owner)
        assert claim.claimed
        result = AuditAgentResult.model_validate(
            {
                "outcome": "reconciled",
                "summary": "Exact live read proved action absent.",
                "proposal_revision": run.proposal_revision,
                "side_effect_state": "unknown",
                "feedback": None,
                "external_result": None,
                "reconciliation": [
                    {
                        "action_index": 0,
                        "disposition": "absent",
                        "read_result_digest": "digest-1",
                    }
                ],
                "error": {
                    "code": "",
                    "retryable": False,
                    "authorization_required": False,
                },
            }
        )
        persisted = self.store.persist_unknown_agent_run_result(
            run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            transcript_end_line=2,
        )
        return AgentTurnRunResult(persisted.id, result, 1, 2)

    def execute_recovery(self, task, context, *, run):
        self.execute_calls += 1
        claim = self.store.claim_unknown_agent_run(run.id, owner=self.owner)
        assert claim.claimed
        result = AuditAgentResult.model_validate(
            {
                **_audit_result("executed", run.proposal_revision).model_dump(
                    mode="json"
                ),
                "external_result": {
                    "operation_id": run.operation_id,
                    "verification_summary": "Executed persisted absent action.",
                    "live_result_reference": {"id": "result"},
                },
            }
        )
        completed = self.store.complete_agent_run(
            run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state="confirmed",
            expected_status="unknown",
        )
        return AgentTurnRunResult(completed.id, result, 2, 3)


@pytest.fixture
def store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "orchestrator.sqlite3")


def _task(store: AutoReplyStore, *, message_id="msg-1", conversation_id="cid-agent"):
    store.enqueue_reply_task(
        conversation_id=conversation_id,
        conversation_title="Group",
        single_chat=False,
        trigger_message_id=message_id,
        trigger_create_time="2026-08-07 10:00:00",
        trigger_sender="Requester",
        trigger_text="Use the supplied evidence.",
        execution_generation=f"gen-{message_id}",
    )
    task = store.get_reply_task_for_message(conversation_id, message_id)
    assert task is not None
    return task


def _wrong_consumer_parent(
    store: AutoReplyStore, task, parent_kind: str
) -> int:
    other_task = _task(
        store,
        message_id=f"msg-{parent_kind}",
        conversation_id=f"cid-{parent_kind}",
    )
    run = store.claim_agent_run(
        other_task.id,
        other_task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner=f"wrong-{parent_kind}",
    ).run
    if parent_kind == "other_task":
        return run.id
    updates = {
        "other_generation": (task.id, "wrong-generation", 0),
        "other_revision": (task.id, task.execution_generation, 1),
    }
    if parent_kind == "other_turn":
        reply_task_id, generation, revision = (
            task.id,
            task.execution_generation,
            0,
        )
        turn_attempt = 1
    else:
        reply_task_id, generation, revision = updates[parent_kind]
        turn_attempt = 0
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            update agent_runs
            set reply_task_id=?, execution_generation=?, proposal_revision=?,
                turn_attempt=?
            where id=?
            """,
            (reply_task_id, generation, revision, turn_attempt, run.id),
        )
    return run.id


def _context(task) -> AgentTaskContext:
    return AgentTaskContext(
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


def _process(orchestrator, task, context=None, *, refresh_context=None):
    initial = context or _context(task)
    return orchestrator.process(
        task,
        initial,
        refresh_context=refresh_context or (lambda: initial),
    )


def test_no_action_finishes_without_launching_audit(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("no_action", "Nothing to do."))
    audit = ScriptedAudit(store)

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "no_action"
    assert result.final_role is AgentRole.CONSUMER
    assert audit.calls == []


def test_provider_capacity_failure_defers_without_in_process_retries(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        ConsumerAgentResult.model_validate(
            {
                "outcome": "failed",
                "summary": "Codex provider capacity is temporarily unavailable.",
                "proposal": None,
                "error": {
                    "code": "codex_provider_unavailable",
                    "retryable": True,
                    "authorization_required": False,
                },
            }
        ),
    )

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=consumer,
            audit=ScriptedAudit(store),
        ),
        task,
    )

    assert result.status == "failed_retryable"
    assert result.error.code == "codex_provider_unavailable"
    assert len(consumer.calls) == 1


def test_provider_capacity_failure_retries_on_later_task_attempt(store):
    _task(store)
    task = store.claim_reply_tasks(1)[0]
    consumer = ScriptedConsumer(
        store,
        ConsumerAgentResult.model_validate(
            {
                "outcome": "failed",
                "summary": "Codex provider capacity is temporarily unavailable.",
                "proposal": None,
                "error": {
                    "code": "codex_provider_unavailable",
                    "retryable": True,
                    "authorization_required": False,
                },
            }
        ),
        _consumer_result("no_action", "Recovered on the later task attempt."),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(store),
    )

    first = _process(orchestrator, task)
    assert first.status == "failed_retryable"
    store.defer_reply_task(
        task.id,
        "codex_provider_unavailable",
        expected_execution_generation=task.execution_generation,
        available_at="2026-08-08 12:00:00",
    )
    retried_task = store.get_reply_task(task.id)

    second = _process(orchestrator, retried_task)

    assert second.status == "no_action"
    assert len(consumer.calls) == 2
    assert consumer.calls[0]["run_id"] != consumer.calls[1]["run_id"]


def test_audit_provider_capacity_failure_defers_without_in_process_retries(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate"))
    audit = ScriptedAudit(
        store,
        _audit_result(
            "failed",
            0,
            code="codex_provider_unavailable",
            retryable=True,
        ),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "failed_retryable"
    assert result.error.code == "codex_provider_unavailable"
    assert len(audit.calls) == 1


def test_audit_provider_capacity_failure_retries_on_later_task_attempt(store):
    _task(store)
    task = store.claim_reply_tasks(1)[0]
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate"))
    audit = ScriptedAudit(
        store,
        _audit_result(
            "failed",
            0,
            code="codex_provider_unavailable",
            retryable=True,
        ),
        _audit_result("executed", 0),
    )
    orchestrator = AgentOrchestrator(store=store, consumer=consumer, audit=audit)

    first = _process(orchestrator, task)
    assert first.status == "failed_retryable"
    store.defer_reply_task(
        task.id,
        "codex_provider_unavailable",
        expected_execution_generation=task.execution_generation,
        available_at="2026-08-08 12:00:00",
    )
    retried_task = store.get_reply_task(task.id)

    second = _process(orchestrator, retried_task)

    assert second.status == "executed"
    assert len(audit.calls) == 2
    assert audit.calls[0]["run_id"] == audit.calls[1]["run_id"]


def test_proposal_is_executed_only_by_fresh_audit_session(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "executed"
    assert result.final_role is AgentRole.AUDIT
    assert audit.calls[0]["session_id"] == "audit-session-0"
    assert audit.calls[0]["operation_id"] == (
        f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
    )


def test_audit_receives_exact_parent_consumer_skill_on_initial_and_normal_retry(store):
    task = _task(store)
    receipt = LoadedSkillReceipt(
        name="business-review",
        path="/Users/derek/.agents/skills/business-review/SKILL.md",
        sha256="a" * 64,
    )
    consumer = ReceiptScriptedConsumer(
        store,
        receipt,
        _consumer_result("proposal", "candidate"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("failed", 0, code="temporary", retryable=True),
        _audit_result("executed", 0),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "executed"
    assert [call["context"].consumer_skills for call in audit.calls] == [
        (receipt,),
        (receipt,),
    ]


def test_cross_domain_consumer_receipts_reach_audit_together(store):
    task = _task(store)
    receipts = (
        LoadedSkillReceipt(
            name="ceo-calendar-invite",
            path="/Users/derek/.agents/skills/ceo-calendar-invite/SKILL.md",
            sha256="a" * 64,
        ),
        LoadedSkillReceipt(
            name="ceo-document-review",
            path="/Users/derek/.agents/skills/ceo-document-review/SKILL.md",
            sha256="b" * 64,
        ),
    )
    consumer = ReceiptScriptedConsumer(
        store,
        receipts,
        _consumer_result("proposal", "cross-domain candidate"),
    )
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "executed"
    assert audit.calls[0]["context"].consumer_skills == receipts


@pytest.mark.parametrize(
    "parent_kind",
    (
        "null",
        "missing",
        "wrong_role",
        "other_task",
        "other_generation",
        "other_revision",
        "other_turn",
    ),
)
def test_normal_audit_state_with_invalid_parent_defers_without_invoking_audit(
    store, monkeypatch, parent_kind
):
    task = _task(store)
    if parent_kind == "null":
        parent_id = None
    elif parent_kind == "missing":
        parent_id = 999_999
    elif parent_kind == "wrong_role":
        parent_id = store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=7,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="wrong-parent",
            owner="wrong-parent",
        ).run.id
    else:
        parent_id = _wrong_consumer_parent(store, task, parent_kind)
    state = _NextAudit(
        proposal_revision=0,
        turn_attempt=0,
        parent_run_id=parent_id,
        proposal=_consumer_result("proposal", "candidate").proposal,
    )
    audit = ScriptedAudit(store)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ScriptedConsumer(store),
        audit=audit,
    )
    monkeypatch.setattr(orchestrator, "_derive_state", lambda _task: state)

    result = _process(orchestrator, task)

    assert result.status == "failed_retryable"
    assert result.error.code == "audit_consumer_parent_invalid"
    assert audit.calls == []


def test_audit_accepts_completed_retrying_consumer_parent(store):
    task = _task(store)
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=None,
        operation_id="",
        owner="retrying-consumer",
    )
    store.complete_agent_run(
        claim.run.id,
        _consumer_result("proposal").model_dump(mode="json"),
        owner="retrying-consumer",
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ScriptedConsumer(store),
        audit=ScriptedAudit(store),
    )

    assert orchestrator._consumer_skills(task, claim.run.id, 0) == ()


def test_unknown_audit_recovery_receives_exact_parent_consumer_skill(store):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    receipt = LoadedSkillReceipt(
        name="business-review",
        path="/Users/derek/.agents/skills/business-review/SKILL.md",
        sha256="b" * 64,
    )
    consumer = ReceiptScriptedConsumer(
        store,
        receipt,
        _consumer_result("proposal", "candidate"),
    )
    consumer.run(
        task,
        _context(task),
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    parent = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert parent is not None
    audit_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="crashed-audit",
    ).run
    store.set_agent_run_session(audit_run.id, "audit-session", owner="crashed-audit")
    store.mark_agent_run_unknown(
        audit_run.id,
        {"code": "write_outcome_unknown", "retryable": False},
        owner="crashed-audit",
    )
    audit = RecoveringScriptedAudit(store, _audit_result("needs_human", 0))

    _process(
        AgentOrchestrator(store=store, consumer=ScriptedConsumer(store), audit=audit),
        task,
    )

    assert audit.recovery_contexts[0].consumer_skills == (receipt,)


@pytest.mark.parametrize(
    "parent_kind",
    (
        "null",
        "missing",
        "wrong_role",
        "other_task",
        "other_generation",
        "other_revision",
        "other_turn",
    ),
)
def test_unknown_recovery_with_invalid_parent_defers_without_invoking_audit(
    store, parent_kind
):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate"))
    consumer.run(
        task,
        _context(task),
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    parent = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert parent is not None
    audit_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="crashed-audit",
    ).run
    store.set_agent_run_session(audit_run.id, "audit-session", owner="crashed-audit")
    store.mark_agent_run_unknown(
        audit_run.id,
        {"code": "write_outcome_unknown", "retryable": False},
        owner="crashed-audit",
    )
    invalid_parent_id = (
        {
            "null": None,
            "missing": 999_999,
            "wrong_role": audit_run.id,
        }[parent_kind]
        if parent_kind in {"null", "missing", "wrong_role"}
        else _wrong_consumer_parent(store, task, parent_kind)
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update agent_runs set parent_agent_run_id=? where id=?",
            (invalid_parent_id, audit_run.id),
        )
    audit = RecoveringScriptedAudit(store, _audit_result("needs_human", 0))

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store),
            audit=audit,
        ),
        task,
    )

    assert result.status == "failed_retryable"
    assert result.error.code == (
        "agent_run_active"
        if parent_kind == "other_turn"
        else "audit_consumer_parent_invalid"
    )
    assert audit.calls == []
    assert audit.recovery_calls == []


def test_two_feedback_cycles_resume_same_consumer_and_create_fresh_auditors(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "candidate-0"),
        _consumer_result("proposal", "candidate-1"),
        _consumer_result("proposal", "candidate-2"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("revision_required", 0),
        _audit_result("revision_required", 1),
        _audit_result("executed", 2),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "executed"
    assert result.feedback_cycles == 2
    assert [call["session_id"] for call in consumer.calls] == [
        "consumer-session",
        "consumer-session",
        "consumer-session",
    ]
    assert [call["revision"] for call in audit.calls] == [0, 1, 2]
    assert len({call["session_id"] for call in audit.calls}) == 3
    assert all(call["feedback"] is not None for call in consumer.calls[1:])


def test_corrected_revision_is_not_blocked_by_old_exact_success(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(store, _audit_result("executed", 0))
    first = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit),
        task,
    )
    assert first.status == "executed"
    old_audit = store.get_agent_run(first.final_run_id)
    assert old_audit is not None

    corrected_consumer = ScriptedConsumer(
        store, _consumer_result("proposal", "corrected-candidate")
    )
    corrected_consumer.run(
        task,
        _context(task),
        proposal_revision=1,
        parent_agent_run_id=old_audit.id,
    )
    corrected_audit = ScriptedAudit(store, _audit_result("executed", 1))

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=corrected_consumer,
            audit=corrected_audit,
        ),
        task,
    )

    assert result.status == "executed"
    assert corrected_audit.calls[0]["revision"] == 1
    assert corrected_audit.calls[0]["operation_id"] == (
        f"agent-task:{task.id}:{task.execution_generation}:proposal:1"
    )
    assert corrected_audit.calls[0]["operation_id"] != old_audit.operation_id


@pytest.mark.parametrize(
    ("recovery_outcome", "expected_status"),
    (("executed", "executed"), ("needs_human", "needs_human")),
)
def test_unknown_audit_is_recovered_in_same_session_and_revision(
    store,
    recovery_outcome,
    expected_status,
):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    consumer.run(
        task,
        _context(task),
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    parent = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert parent is not None
    operation_id = f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
    audit_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=operation_id,
        owner="crashed-audit",
    ).run
    store.set_agent_run_session(
        audit_run.id,
        "audit-session-exact",
        owner="crashed-audit",
    )
    unknown = store.mark_agent_run_unknown(
        audit_run.id,
        {"code": "write_outcome_unknown", "retryable": False},
        owner="crashed-audit",
    )
    recovery = RecoveringScriptedAudit(
        store,
        _audit_result(recovery_outcome, 0, code="audit_recovery_ambiguous"),
    )

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store),
            audit=recovery,
        ),
        task,
    )

    assert result.status == expected_status
    assert recovery.recovery_calls == [
        {
            "run_id": unknown.id,
            "session_id": "audit-session-exact",
            "operation_id": operation_id,
            "revision": 0,
        }
    ]


def test_invalid_audit_result_with_unknown_effect_recovers_instead_of_failing_task(store):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    recovery = RecoveringScriptedAudit(store, _audit_result("executed", 0))
    failing_audit = ParseFailingEffectfulAudit(store)
    failing_audit.recover = recovery.recover

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store, _consumer_result("proposal", "candidate-0")),
            audit=failing_audit,
        ),
        task,
    )

    assert result.status == "executed"
    assert recovery.recovery_calls[0]["session_id"] == "audit-session-with-effect"


def test_unknown_audit_without_session_finishes_needs_human(store):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    consumer.run(
        task,
        _context(task),
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    parent = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert parent is not None
    audit_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="crashed-audit",
    ).run
    unknown = store.mark_agent_run_unknown(
        audit_run.id,
        {"code": "write_outcome_unknown", "retryable": False},
        owner="crashed-audit",
    )

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store),
            audit=ScriptedAudit(store),
        ),
        task,
    )

    assert result.status == "needs_human"
    assert result.error.code == "audit_recovery_session_missing"
    persisted = store.get_agent_run(unknown.id)
    assert persisted is not None and persisted.status == "completed"
    assert persisted.side_effect_state == "unknown"


def test_persisted_reconciliation_resumes_execute_phase_without_repeating_read(store):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    consumer.run(
        task,
        _context(task),
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    parent = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert parent is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="crashed-audit",
    ).run
    store.set_agent_run_session(run.id, "audit-session", owner="crashed-audit")
    unknown = store.mark_agent_run_unknown(
        run.id,
        {"code": "write_outcome_unknown", "retryable": False},
        owner="crashed-audit",
    )
    phase_one = TwoPhaseScriptedAudit(store)
    phase_one.recover(
        task,
        AuditTurnContext(
            task=_context(task),
            proposal_revision=0,
            operation_id=unknown.operation_id,
            proposal=_consumer_result("proposal", "candidate-0").proposal,
            audit_rules="",
        ),
        run=unknown,
    )

    resumed = TwoPhaseScriptedAudit(store)
    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store),
            audit=resumed,
        ),
        task,
    )

    assert result.status == "executed"
    assert resumed.recovery_calls == 0
    assert resumed.execute_calls == 1


def test_infrastructure_retry_does_not_consume_feedback_cycle(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(
        store,
        _audit_result("failed", 0, code="temporary_unavailable", retryable=True),
        _audit_result("executed", 0),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "executed"
    assert result.feedback_cycles == 0
    assert [call["revision"] for call in audit.calls] == [0, 0]
    assert [call["turn_attempt"] for call in audit.calls] == [0, 1]
    assert audit.calls[0]["operation_id"] == audit.calls[1]["operation_id"]
    assert audit.calls[0]["session_id"] != audit.calls[1]["session_id"]


def test_newer_context_stale_candidate_is_revised_without_write(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "publish-v1"),
        _consumer_result("no_action", "New context makes the action unnecessary."),
    )
    audit = ScriptedAudit(store, _audit_result("revision_required", 0))

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "no_action"
    assert result.final_role is AgentRole.CONSUMER
    assert len(audit.calls) == 1
    audit_run = store.get_agent_run(result.final_run_id - 1)
    assert audit_run is not None and audit_run.side_effect_state == "none"


def test_third_revision_request_becomes_needs_human(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "candidate-0"),
        _consumer_result("proposal", "candidate-1"),
        _consumer_result("proposal", "candidate-2"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("revision_required", 0),
        _audit_result("revision_required", 1),
        _audit_result("revision_required", 2),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "needs_human"
    assert result.feedback_cycles == 2
    assert result.feedback is not None


def test_authorization_wait_defers_without_consuming_feedback_cycle(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(
        store,
        _audit_result(
            "failed",
            0,
            code="authorization_wait",
            retryable=True,
            authorization_required=True,
        ),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "failed_retryable"
    assert result.feedback_cycles == 0
    assert len(audit.calls) == 1


def test_expired_consumer_turn_without_session_creates_a_recovery_turn(store):
    task = _task(store)
    claimed_task = store.claim_reply_task(task.id)
    assert claimed_task is not None
    task = claimed_task
    stale = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="stale-consumer",
        lease_seconds=1,
        now="2020-01-01 00:00:00",
    ).run
    consumer = ScriptedConsumer(store, _consumer_result("no_action", "Recovered."))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=ScriptedAudit(store)),
        task,
    )

    assert result.status == "no_action"
    assert result.final_run_id != stale.id
    stale_after_recovery = store.get_agent_run(stale.id)
    assert stale_after_recovery is not None
    assert stale_after_recovery.status == "failed"
    assert '"code":"consumer_lease_expired"' in stale_after_recovery.structured_error_json
    runs = store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation
    )
    assert [run.turn_attempt for run in runs] == [0, 1]


def test_expired_audit_turn_without_session_is_reclaimed_in_place(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    consumer.run(
        task,
        _context(task),
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    parent = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert parent is not None
    stale = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="stale-audit",
        lease_seconds=1,
        now="2020-01-01 00:00:00",
    ).run
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit),
        task,
    )

    assert result.status == "executed"
    assert result.final_run_id == stale.id
    assert audit.calls[0]["turn_attempt"] == 0


@pytest.mark.parametrize("side_effect_state", ["unknown", "confirmed"])
def test_expired_audit_turn_with_possible_effect_is_persisted_unknown_without_replay(
    store,
    side_effect_state,
):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    consumer.run(
        task,
        _context(task),
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    parent = store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert parent is not None
    stale = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent.id,
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
        owner="stale-audit",
        lease_seconds=1,
        now="2020-01-01 00:00:00",
    ).run
    with store._connect() as db:
        db.execute(
            "update agent_runs set side_effect_state=? where id=?",
            (side_effect_state, stale.id),
        )
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit),
        task,
    )

    assert result.status == "unknown"
    assert audit.calls == []
    persisted = store.get_agent_run(stale.id)
    assert persisted is not None
    assert persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"


def test_authorization_recovery_retries_same_persisted_turn_on_next_process(store):
    pending_task = _task(store)
    task = store.claim_reply_task(pending_task.id)
    assert task is not None
    audit = ScriptedAudit(
        store,
        _audit_result(
            "failed",
            0,
            code="authorization_wait",
            retryable=True,
            authorization_required=True,
        ),
        _audit_result("executed", 0),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ScriptedConsumer(store, _consumer_result("proposal", "candidate-0")),
        audit=audit,
    )

    first = _process(orchestrator, task)
    assert first.status == "failed_retryable"
    store.defer_reply_task(
        task.id,
        first.error.code,
        expected_execution_generation=task.execution_generation,
    )
    recovered_task = store.claim_reply_task(task.id)
    assert recovered_task is not None

    second = _process(orchestrator, recovered_task)

    assert second.status == "executed"
    assert [call["turn_attempt"] for call in audit.calls] == [0, 0]
    audit_runs = [
        run
        for run in store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation
        )
        if run.role is AgentRole.AUDIT
    ]
    assert len(audit_runs) == 1


def test_authorization_recovery_defers_again_after_one_failed_retry(store):
    pending_task = _task(store)
    task = store.claim_reply_task(pending_task.id)
    assert task is not None
    authorization_failure = _audit_result(
        "failed",
        0,
        code="authorization_wait",
        retryable=True,
        authorization_required=True,
    )
    audit = ScriptedAudit(
        store,
        authorization_failure,
        authorization_failure,
        authorization_failure,
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ScriptedConsumer(store, _consumer_result("proposal", "candidate-0")),
        audit=audit,
    )

    first = _process(orchestrator, task)

    assert first.status == "failed_retryable"
    assert first.error.code == "authorization_wait"
    assert first.error.authorization_required is True
    assert first.feedback_cycles == 0
    assert len(audit.calls) == 1
    store.defer_reply_task(
        task.id,
        first.error.code,
        expected_execution_generation=task.execution_generation,
    )
    recovered_task = store.claim_reply_task(task.id)
    assert recovered_task is not None

    second = _process(orchestrator, recovered_task)

    assert second.status == "failed_retryable"
    assert second.error.code == "authorization_wait"
    assert second.error.authorization_required is True
    assert second.feedback_cycles == 0
    assert len(audit.calls) == 2


def test_retryable_audit_exhaustion_returns_failed_latest_run(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(
        store,
        _audit_result("failed", 0, code="audit_unavailable", retryable=True),
        _audit_result("failed", 0, code="audit_unavailable", retryable=True),
    )

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit),
        task,
    )

    assert result.status == "failed_retryable"
    assert result.final_role is AgentRole.AUDIT
    assert result.final_run_id == audit.calls[-1]["run_id"]
    assert result.error.code == "audit_retry_exhausted"
    assert result.error.retryable is True
    assert result.feedback_cycles == 0
    assert len(audit.calls) == 2


def test_retryable_consumer_exhaustion_returns_failed_latest_run(store):
    failure = ConsumerAgentResult.model_validate(
        {
            "outcome": "failed",
            "summary": "Consumer dependency unavailable.",
            "proposal": None,
            "error": {
                "code": "consumer_unavailable",
                "retryable": True,
                "authorization_required": False,
            },
        }
    )
    task = _task(store)
    consumer = ScriptedConsumer(store, failure, failure)

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=consumer,
            audit=ScriptedAudit(store),
        ),
        task,
    )

    assert result.status == "failed_retryable"
    assert result.final_role is AgentRole.CONSUMER
    assert result.final_run_id == consumer.calls[-1]["run_id"]
    assert result.error.code == "consumer_retry_exhausted"
    assert result.error.retryable is True
    assert result.feedback_cycles == 0
    assert len(consumer.calls) == 2


def test_recovered_failed_consumer_task_reclaims_same_run(store):
    failure = ConsumerAgentResult.model_validate(
        {
            "outcome": "failed",
            "summary": "Consumer runtime failed.",
            "proposal": None,
            "error": {
                "code": "codex_process_failed",
                "retryable": True,
                "authorization_required": False,
            },
        }
    )
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(
        store,
        failure,
        failure,
        _consumer_result("no_action", "Recovered without an external action."),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(store),
    )

    failed = _process(orchestrator, task)
    assert failed.status == "failed_retryable"
    failed_run_id = failed.final_run_id
    store.fail_reply_task(
        task.id,
        failed.error.code,
        expected_execution_generation=task.execution_generation,
    )
    store.retry_failed_reply_task(
        task.id,
        failed_run_id,
        reason="operator_retry_after_runtime_fix",
    )
    recovered_task = store.claim_reply_task(task.id)
    assert recovered_task is not None

    recovered = _process(orchestrator, recovered_task)

    assert recovered.status == "no_action"
    assert recovered.final_run_id != failed_run_id
    assert consumer.calls[-1]["run_id"] != failed_run_id
    failed_run = store.get_agent_run(failed_run_id)
    assert failed_run is not None
    assert failed_run.status == "failed"


class RevisionRetryConsumer(ScriptedConsumer):
    def __init__(self, store: AutoReplyStore) -> None:
        super().__init__(store)
        self.revision_one_attempts = 0

    def run(self, task, context, **kwargs):
        revision = kwargs["proposal_revision"]
        if revision == 0:
            self.results.append(_consumer_result("proposal", "candidate-0"))
            return super().run(task, context, **kwargs)
        self.revision_one_attempts += 1
        if self.revision_one_attempts == 1:
            claim = self.store.claim_agent_run(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=revision,
                turn_attempt=self.store.next_agent_run_turn_attempt(
                    task.id,
                    task.execution_generation,
                    role=AgentRole.CONSUMER,
                    proposal_revision=revision,
                ),
                parent_agent_run_id=kwargs["parent_agent_run_id"],
                operation_id="",
                owner=self.owner,
            )
            assert claim.claimed
            self.calls.append({"feedback": kwargs["feedback"], "revision": revision})
            self.store.fail_agent_run(
                claim.run.id,
                {"code": "temporary_consumer_failure", "retryable": True},
                owner=self.owner,
            )
            raise RuntimeError("temporary_consumer_failure")
        self.results.append(_consumer_result("no_action", "revision complete"))
        return super().run(task, context, **kwargs)


def test_consumer_retry_restores_identical_feedback_from_parent_audit(store):
    task = _task(store)
    consumer = RevisionRetryConsumer(store)
    audit = ScriptedAudit(store, _audit_result("revision_required", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert result.status == "no_action"
    revision_calls = [call for call in consumer.calls if call["revision"] == 1]
    assert len(revision_calls) == 2
    assert revision_calls[0]["feedback"] is not None
    assert revision_calls[1]["feedback"] == revision_calls[0]["feedback"]


def test_audit_receives_context_refreshed_after_consumer_output(store):
    task = _task(store)
    refreshed = replace(
        _context(task),
        messages=(
            AgentContextMessage(
                message_id="msg-new",
                sender="Requester",
                text="Use the updated target.",
                create_time="2026-08-07 10:01:00",
            ),
        ),
    )
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store, _consumer_result("proposal", "candidate-0")),
            audit=audit,
        ),
        task,
        refresh_context=lambda: refreshed,
    )

    assert result.status == "executed"
    assert audit.calls[0]["context"].task.messages[0].text == "Use the updated target."


def test_context_refresh_failure_defers_before_audit(store):
    task = _task(store)
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    def fail_refresh():
        raise RuntimeError("context source unavailable")

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store, _consumer_result("proposal", "candidate-0")),
            audit=audit,
        ),
        task,
        refresh_context=fail_refresh,
    )

    assert result.status == "failed_retryable"
    assert result.error.code == "agent_context_refresh_failed"
    assert audit.calls == []


def test_context_refresh_failure_exposes_safe_dws_code_without_raw_detail(store):
    task = _task(store)
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    def fail_refresh():
        raise DwsError(
            "request failed for private target cid-secret", code="SYSTEM_ERROR"
        )

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(store, _consumer_result("proposal", "candidate-0")),
            audit=audit,
        ),
        task,
        refresh_context=fail_refresh,
    )

    assert result.error.code == "agent_context_refresh_failed"
    assert result.summary == (
        "agent_context_refresh_failed: DingTalk read unavailable (SYSTEM_ERROR)"
    )
    assert "cid-secret" not in result.summary


class SerialConsumer(ScriptedConsumer):
    def __init__(self, store: AutoReplyStore) -> None:
        super().__init__(store)
        self.active = 0
        self.max_active = 0
        self.sessions = []
        self.guard = threading.Lock()

    def run(self, task, context, **kwargs):
        owner = f"serial-consumer:{task.id}"
        try:
            with self.store.codex_session_lock(task.conversation_id, owner):
                with self.guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    session_id = self.store.get_codex_session_id(task.conversation_id)
                    if session_id is None:
                        session_id = "shared-consumer-session"
                        self.store.upsert_conversation(
                            task.conversation_id,
                            task.conversation_title,
                            task.single_chat,
                            session_id,
                        )
                    self.sessions.append(session_id)
                    time.sleep(0.03)
                    self.results.append(_consumer_result("no_action"))
                    return super().run(task, context, **kwargs)
                finally:
                    with self.guard:
                        self.active -= 1
        except RuntimeError as exc:
            if str(exc).startswith("codex session locked:"):
                raise RuntimeError("codex_session_locked") from exc
            raise


def test_concurrent_tasks_share_one_consumer_session_and_resume_serially(store):
    first = _task(store, message_id="msg-1", conversation_id="same-conversation")
    second = _task(store, message_id="msg-2", conversation_id="same-conversation")
    consumer = SerialConsumer(store)
    audit = ScriptedAudit(store)
    orchestrator = AgentOrchestrator(store=store, consumer=consumer, audit=audit)
    results = []

    threads = [
        threading.Thread(
            target=lambda task=task: results.append(
                (task, _process(orchestrator, task))
            )
        )
        for task in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    deferred = [
        task for task, result in results if result.status == "failed_retryable"
    ]
    assert len(deferred) == 1
    retry = _process(orchestrator, deferred[0])

    assert retry.status == "no_action"
    assert consumer.max_active == 1
    assert set(consumer.sessions) == {"shared-consumer-session"}
    assert store.get_codex_session_id("same-conversation") == "shared-consumer-session"
