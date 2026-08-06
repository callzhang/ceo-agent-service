import json
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_context import AgentContextMessage, AgentTaskContext
from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_orchestrator import AgentOrchestrator
from app.agent_result import SideEffectState
from app.agent_turn_runner import AgentTurnRunResult
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
            turn_attempt=0,
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
        self.store.complete_agent_run(
            claim.run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
        )
        self.calls.append(
            {
                "revision": proposal_revision,
                "parent": parent_agent_run_id,
                "feedback": feedback,
                "session_id": session_id,
                "context": context,
            }
        )
        return AgentTurnRunResult(claim.run.id, result, 0, 1)


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
                "revision": context.proposal_revision,
                "turn_attempt": turn_attempt,
                "operation_id": context.operation_id,
                "session_id": session_id,
                "proposal": context.proposal,
                "context": context,
            }
        )
        return AgentTurnRunResult(claim.run.id, result, 0, 1)


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

    assert result.status == "skipped"
    assert result.final_role is AgentRole.CONSUMER
    assert audit.calls == []


def test_proposal_is_executed_only_by_fresh_audit_session(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "completed"
    assert result.final_role is AgentRole.AUDIT
    assert audit.calls[0]["session_id"] == "audit-session-0"
    assert audit.calls[0]["operation_id"] == (
        f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
    )


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

    assert result.status == "completed"
    assert result.feedback_cycles == 2
    assert [call["session_id"] for call in consumer.calls] == [
        "consumer-session",
        "consumer-session",
        "consumer-session",
    ]
    assert [call["revision"] for call in audit.calls] == [0, 1, 2]
    assert len({call["session_id"] for call in audit.calls}) == 3
    assert all(call["feedback"] is not None for call in consumer.calls[1:])


def test_infrastructure_retry_does_not_consume_feedback_cycle(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(
        store,
        _audit_result("failed", 0, code="temporary_unavailable", retryable=True),
        _audit_result("executed", 0),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "completed"
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

    assert result.status == "skipped"
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

    assert result.status == "deferred"
    assert result.feedback_cycles == 0
    assert len(audit.calls) == 1


def test_expired_consumer_turn_without_session_is_reclaimed_in_place(store):
    task = _task(store)
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

    assert result.status == "skipped"
    assert result.final_run_id == stale.id
    assert len(
        store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    ) == 1


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

    assert result.status == "completed"
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

    assert result.status == "blocked"
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
    assert first.status == "deferred"
    store.defer_reply_task(
        task.id,
        first.error.code,
        expected_execution_generation=task.execution_generation,
    )
    recovered_task = store.claim_reply_task(task.id)
    assert recovered_task is not None

    second = _process(orchestrator, recovered_task)

    assert second.status == "completed"
    assert [call["turn_attempt"] for call in audit.calls] == [0, 0]
    audit_runs = [
        run
        for run in store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation
        )
        if run.role is AgentRole.AUDIT
    ]
    assert len(audit_runs) == 1


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
                turn_attempt=0,
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

    assert result.status == "skipped"
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

    assert result.status == "completed"
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

    assert result.status == "deferred"
    assert result.error.code == "agent_context_refresh_failed"
    assert audit.calls == []


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

    deferred = [task for task, result in results if result.status == "deferred"]
    assert len(deferred) == 1
    retry = _process(orchestrator, deferred[0])

    assert retry.status == "skipped"
    assert consumer.max_active == 1
    assert set(consumer.sessions) == {"shared-consumer-session"}
    assert store.get_codex_session_id("same-conversation") == "shared-consumer-session"
