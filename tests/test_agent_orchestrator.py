import sqlite3
import threading
import time
from collections import deque
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import pytest

from app.agent_context import (
    AgentContextMessage,
    AgentTaskContext,
    AuditTurnContext,
    PriorReceipt,
)
from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_orchestrator import (
    MAX_TURNS_PER_PROCESS,
    AgentOrchestrator,
    OrchestrationResult,
    _NextAudit,
    _NextConsumer,
)
from app.agent_result import AgentError
from app.agent_turn_runner import AgentTurnRunResult
from app.dws_client import DwsError
from app.email_unsubscribe_continuation import (
    DomainContinuationConsumptionDecision,
    DomainContinuationConsumptionState,
    DomainContinuationDecision,
    DomainContinuationState,
    EmailUnsubscribeContinuationDriver,
    domain_continuation_receipt_binding,
)
from app.email_store import email_action_identity
from app.email_task_adapter import email_conversation_id
from app.email_unsubscribe import EmailUnsubscribeEffect, UnsubscribeOperation
from app.store import AgentRole, AutoReplyStore


def _consumer_result(outcome: str, label: str = "candidate") -> ConsumerAgentResult:
    proposal = None
    if outcome == "proposal":
        proposal = {
            "objective": label,
            "actions": [
                {
                    "description": label,
                    "action_identity": f"notify-{label}",
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


def _bounded_needs_human_result() -> ConsumerAgentResult:
    return ConsumerAgentResult.model_validate(
        {
            "outcome": "needs_human",
            "summary": "possible external inquiry",
            "proposal": None,
            "decision_options": [
                {
                    "key": "fact_finding",
                    "label": "仅作事实调研",
                    "instruction": "仅询问字段和价格，不构成采购、预算或合作承诺，不得下单或付款。",
                    "consequence": "补齐事实后再决定。",
                },
                {
                    "key": "stop",
                    "label": "暂停",
                    "instruction": "暂不联系。",
                    "consequence": "等待后续确认。",
                },
            ],
            "risk": "high",
            "confidence": 0.1,
            "error": {
                "code": "management_decision_required",
                "retryable": False,
                "authorization_required": True,
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
    decision_options = []
    if outcome in {"revision_required", "feedback_provided"}:
        feedback = {
            "rule": "current evidence",
            "observation": f"revision {revision} is stale",
            "requested_revision": "Return a complete replacement proposal.",
        }
    elif outcome == "executed":
        external_result = {
            "operation_id": "filled-by-runner",
            "live_result_reference": {"id": f"result-{revision}"},
        }
    elif outcome == "needs_human":
        decision_options = [
            {
                "key": "A",
                "label": "Proceed after confirmation",
                "instruction": "Proceed with the verified recovery path.",
                "consequence": "Audit will verify the recovered action.",
            },
            {
                "key": "B",
                "label": "Stop safely",
                "instruction": "Stop without executing another external action.",
                "consequence": "No new external action will run.",
            },
        ]
    elif outcome == "dry_run":
        code = "dry_run_execution_suppressed"
    return AuditAgentResult.model_validate(
        {
            "outcome": outcome,
            "summary": outcome,
            "proposal_revision": revision,
            "feedback": feedback,
            "external_result": external_result,
            "decision_options": decision_options,
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


class ScriptedAudit:
    def __init__(self, store: AutoReplyStore, *results: AuditAgentResult) -> None:
        self.store = store
        self.results = deque(results)
        self.calls = []
        self.owner = "scripted-audit"

    def run(
        self,
        task,
        context,
        *,
        turn_attempt,
        parent_agent_run_id,
    ):
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


class ScriptedDomainContinuation:
    def __init__(
        self,
        *continuation_revisions: int,
        invalid_revisions: tuple[int, ...] = (),
        unavailable_revisions: tuple[int, ...] = (),
    ) -> None:
        self.continuation_revisions = set(continuation_revisions)
        self.invalid_revisions = set(invalid_revisions)
        self.unavailable_revisions = set(unavailable_revisions)
        self.calls: list[tuple[int, int, AuditOutcome]] = []

    def load_snapshot(self, task):
        return None

    def continuation_state(self, task, *, audit_run, audit_result, snapshot=None):
        self.calls.append((task.id, audit_run.id, audit_result.outcome))
        if audit_run.proposal_revision in self.invalid_revisions:
            return DomainContinuationDecision(DomainContinuationState.INVALID)
        if audit_run.proposal_revision in self.unavailable_revisions:
            return DomainContinuationDecision(DomainContinuationState.UNAVAILABLE)
        if audit_run.proposal_revision in self.continuation_revisions:
            receipt = _scripted_continuation_receipt()
            return DomainContinuationDecision(
                DomainContinuationState.CONTINUE,
                required_receipt_id=receipt.receipt_id,
                required_receipt_binding=domain_continuation_receipt_binding(receipt),
            )
        return DomainContinuationDecision(DomainContinuationState.TERMINAL)

    def consumption_state(
        self,
        task,
        *,
        parent_audit_run,
        parent_audit_result,
        child_audit_run,
        child_audit_result,
        snapshot=None,
    ):
        if (
            parent_audit_run.proposal_revision in self.continuation_revisions
            and child_audit_run.status == "completed"
            and child_audit_result is not None
            and child_audit_result.outcome is AuditOutcome.EXECUTED
        ):
            return DomainContinuationConsumptionDecision(
                DomainContinuationConsumptionState.CONSUMED
            )
        return DomainContinuationConsumptionDecision(
            DomainContinuationConsumptionState.NOT_CONSUMED
        )


class MalformedProtocolDomainContinuation(ScriptedDomainContinuation):
    def __init__(self, *, continuation_decision=None, consumption_decision=None):
        super().__init__()
        self.continuation_decision = continuation_decision
        self.consumption_decision = consumption_decision

    def continuation_state(self, task, *, audit_run, audit_result, snapshot=None):
        return self.continuation_decision

    def consumption_state(self, task, **kwargs):
        return self.consumption_decision


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


class MutableEmailContinuationStore:
    def __init__(self, claim, continuation, effect) -> None:
        self.claim = claim
        self.continuation = continuation
        self.effect = effect
        self.effects = {effect["effect_digest"]: effect}
        self.unavailable = False
        self.unavailable_effect_digest = ""
        self.read_count = 0
        self.effect_rows_read = 0

    def get_email_unsubscribe_state_snapshot(self, action_identity):
        self.read_count += 1
        if self.unavailable or self.unavailable_effect_digest in self.effects:
            raise sqlite3.OperationalError("database is busy")
        assert action_identity == self.claim["action_identity"]
        effects = tuple(dict(effect) for effect in self.effects.values())
        self.effect_rows_read += len(effects)
        return {
            "claim": dict(self.claim),
            "continuation": (
                dict(self.continuation) if self.continuation is not None else None
            ),
            "effects": effects,
        }

    def get_email_unsubscribe_claim(self, action_identity):
        self.read_count += 1
        if self.unavailable:
            raise sqlite3.OperationalError("database is busy")
        assert action_identity == self.claim["action_identity"]
        return dict(self.claim)

    def get_email_unsubscribe_continuation(self, action_identity):
        if self.unavailable:
            raise sqlite3.OperationalError("database is busy")
        assert action_identity == self.claim["action_identity"]
        return dict(self.continuation) if self.continuation is not None else None

    def get_email_unsubscribe_effect(self, action_identity, effect_digest):
        if self.unavailable or effect_digest == self.unavailable_effect_digest:
            raise sqlite3.OperationalError("database is busy")
        assert action_identity == self.claim["action_identity"]
        effect = self.effects.get(effect_digest)
        return dict(effect) if effect is not None else None

    def await_audit(self, audit_run_id: int) -> None:
        if self.effect["audit_agent_run_id"] not in {None, audit_run_id}:
            previous = self.effect
            operations = [
                *previous["operations"],
                {
                    "operation_reference": (
                        "unsubscribe-operation:"
                        + sha256(f"append-{audit_run_id}".encode()).hexdigest()
                    ),
                    "kind": "click_confirmation",
                    "target_reference": self.continuation["controls"][0]["reference"],
                },
            ]
            typed = EmailUnsubscribeEffect(
                action_identity=self.claim["action_identity"],
                action_plan_id=self.claim["action_plan_id"],
                action_plan_version=self.claim["action_plan_version"],
                classification_id=self.claim["classification_id"],
                account_id=self.claim["account_id"],
                stable_message_identity=self.claim["stable_message_identity"],
                thread_identity=self.claim["thread_identity"],
                entry_reference=self.claim["entry_reference"],
                operations=tuple(
                    UnsubscribeOperation.from_mapping(item) for item in operations
                ),
                previous_effect_digest=previous["effect_digest"],
                network_policy_reference=previous["network_policy_reference"],
                network_policy_origin_references=tuple(
                    previous["network_policy_origin_references"]
                ),
            )
            self.effect = {
                "action_identity": self.claim["action_identity"],
                "effect_digest": typed.effect_digest,
                "previous_effect_digest": previous["effect_digest"],
                "operations": operations,
                "network_policy_reference": previous["network_policy_reference"],
                "network_policy_origin_references": list(
                    previous["network_policy_origin_references"]
                ),
                "audit_agent_run_id": audit_run_id,
            }
            self.effects[typed.effect_digest] = self.effect
            self.continuation.update(
                {
                    "effect_digest": typed.effect_digest,
                    "previous_effect_digest": previous["effect_digest"],
                    "operations": operations,
                }
            )
            self.claim.update(
                {
                    "effect_digest": typed.effect_digest,
                    "operations": operations,
                }
            )
        self.claim.update(
            {
                "status": "awaiting_audit",
                "phase": "navigating",
                "audit_agent_run_id": audit_run_id,
            }
        )
        self.effect["audit_agent_run_id"] = audit_run_id

    def complete(self) -> None:
        self.claim.update({"status": "done", "phase": "terminal"})
        self.continuation = None


def _audited_email_task(
    store: AutoReplyStore,
) -> tuple[object, MutableEmailContinuationStore]:
    account_id = "account-primary"
    stable_message_identity = "account-primary:message-id:<mail-41@example.com>"
    thread_identity = "thread-41"
    action_identity = email_action_identity(
        account_id=account_id,
        stable_message_identity=stable_message_identity,
        action_type="unsubscribe",
        action_plan_version=1,
    )
    entry_digest = sha256(b"entry").hexdigest()
    entry_reference = "unsubscribe-entry:" + entry_digest
    operation_reference = "unsubscribe-operation:" + sha256(b"open").hexdigest()
    control_reference = "unsubscribe-control:" + sha256(b"confirm").hexdigest()
    network_policy_reference = "network-policy:" + sha256(b"policy").hexdigest()
    network_origin_reference = "network-origin:" + sha256(b"origin").hexdigest()
    operations = [
        {
            "operation_reference": operation_reference,
            "kind": "open_entry",
            "target_reference": entry_reference,
        }
    ]
    effect = EmailUnsubscribeEffect(
        action_identity=action_identity,
        action_plan_id="email-plan:subscription:1",
        action_plan_version=1,
        classification_id=41,
        account_id=account_id,
        stable_message_identity=stable_message_identity,
        thread_identity=thread_identity,
        entry_reference=entry_reference,
        operations=(UnsubscribeOperation.from_mapping(operations[0]),),
        network_policy_reference=network_policy_reference,
        network_policy_origin_references=(network_origin_reference,),
    )
    payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "account_id": account_id,
        "stable_message_identity": stable_message_identity,
        "thread_identity": thread_identity,
        "action_identity": action_identity,
        "action_type": "unsubscribe",
        "action_plan_id": "email-plan:subscription:1",
        "action_plan_version": 1,
        "classification_id": 41,
        "category": "junk",
        "classification_source": "user",
        "confidence": 1.0,
        "model_id": "email-model:test",
        "config_version": "email-config:test",
        "action_parameters": {},
        "unsubscribe_entries": [
            {
                "index": 0,
                "source": "header_https",
                "digest": entry_digest,
                "reference": entry_reference,
            }
        ],
        "unsubscribe_authentication": None,
        "unsubscribe_network_policy_reference": network_policy_reference,
        "unsubscribe_network_policy_origin_references": [network_origin_reference],
    }
    conversation_id = email_conversation_id(account_id, thread_identity)
    store.enqueue_reply_task(
        channel="email",
        conversation_id=conversation_id,
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=action_identity,
        trigger_create_time="2026-09-02T08:00:00+00:00",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=json.dumps(payload, sort_keys=True),
        execution_generation="generation-email",
    )
    task = store.get_reply_task_for_message(
        conversation_id,
        action_identity,
        channel="email",
    )
    assert task is not None
    claim = {
        "action_identity": action_identity,
        "effect_digest": effect.effect_digest,
        "action_plan_id": "email-plan:subscription:1",
        "action_plan_version": 1,
        "classification_id": 41,
        "account_id": account_id,
        "stable_message_identity": stable_message_identity,
        "thread_identity": thread_identity,
        "entry_reference": entry_reference,
        "operations": operations,
        "status": "awaiting_audit",
        "phase": "navigating",
        "audit_agent_run_id": None,
    }
    continuation = {
        "action_identity": action_identity,
        "effect_digest": effect.effect_digest,
        "previous_effect_digest": "",
        "operations": operations,
        "controls": [
            {
                "reference": control_reference,
                "kind": "button",
                "intent": "confirm",
            }
        ],
        "observation_reference": "unsubscribe-state:" + sha256(b"state").hexdigest(),
        "network_policy_reference": network_policy_reference,
        "network_policy_origin_references": [network_origin_reference],
    }
    durable_effect = {
        "action_identity": action_identity,
        "effect_digest": effect.effect_digest,
        "previous_effect_digest": "",
        "operations": operations,
        "network_policy_reference": network_policy_reference,
        "network_policy_origin_references": [network_origin_reference],
        "audit_agent_run_id": None,
    }
    return task, MutableEmailContinuationStore(claim, continuation, durable_effect)


def _wrong_consumer_parent(store: AutoReplyStore, task, parent_kind: str) -> int:
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


def test_invalid_completed_consumer_result_is_regenerated_without_rewriting_history(
    store,
):
    task = _task(store)
    task = store.claim_reply_tasks(limit=1)[0]
    stale = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="stale-consumer",
    ).run
    store.complete_agent_run(
        stale.id,
        {"outcome": "proposal", "summary": "pre-contract result"},
        owner="stale-consumer",
    )
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "current"))
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
    ).process(task, _context(task), refresh_context=lambda: _context(task))

    assert result.status == "executed"
    assert len(consumer.calls) == 1
    assert store.get_agent_run(stale.id).final_result_json == (
        '{"outcome":"proposal","summary":"pre-contract result"}'
    )


def test_invalid_completed_audit_result_is_regenerated_without_rerunning_consumer(
    store,
):
    task = _task(store)
    task = store.claim_reply_tasks(limit=1)[0]
    consumer_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="completed-consumer",
    ).run
    store.complete_agent_run(
        consumer_run.id,
        _consumer_result("proposal", "current").model_dump(mode="json"),
        owner="completed-consumer",
    )
    stale = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer_run.id,
        operation_id="stale-audit",
        owner="stale-audit",
    ).run
    store.complete_agent_run(
        stale.id,
        {"outcome": "executed", "summary": "pre-contract result"},
        owner="stale-audit",
    )
    consumer = ScriptedConsumer(store)
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
    ).process(task, _context(task), refresh_context=lambda: _context(task))

    assert result.status == "executed"
    assert consumer.calls == []
    assert len(audit.calls) == 1
    assert store.get_agent_run(stale.id).final_result_json == (
        '{"outcome":"executed","summary":"pre-contract result"}'
    )


def _scripted_continuation_receipt() -> PriorReceipt:
    return PriorReceipt(
        receipt_id="email-unsubscribe-continuation:scripted-current",
        operation="unsubscribe_continuation",
        summary=json.dumps(
            {
                "accepted_operations": [
                    {
                        "operation_reference": "unsubscribe-operation:accepted",
                        "kind": "open_entry",
                        "target_reference": "unsubscribe-entry:accepted",
                    }
                ],
                "controls": [
                    {
                        "reference": "unsubscribe-control:opaque",
                        "kind": "button",
                        "intent": "confirm",
                    }
                ],
                "instruction": (
                    "Audit accepted the durable prefix; propose exactly one next "
                    "operation from the listed opaque controls."
                ),
                "previous_effect_digest": "current-effect",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        completed=False,
    )


def _domain_context(task) -> AgentTaskContext:
    return replace(
        _context(task),
        prior_receipts=(_scripted_continuation_receipt(),),
    )


def _process(orchestrator, task, context=None, *, refresh_context=None):
    initial = context or _context(task)
    return orchestrator.process(
        task,
        initial,
        refresh_context=refresh_context or (lambda: initial),
    )


def test_runtime_provider_unreachable_defers_without_same_process_retry(store):
    task = _task(store)
    failed = _consumer_result("failed", "No eligible route.").model_copy(
        update={
            "error": AgentError(code="runtime_provider_unreachable", retryable=True)
        }
    )
    consumer = ScriptedConsumer(store, failed)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(store),
    )

    result = _process(orchestrator, task)

    assert result.status == "failed_retryable"
    assert result.error.code == "runtime_provider_unreachable"
    assert len(consumer.calls) == 1


def test_recovered_runtime_provider_unreachable_starts_one_new_consumer_turn(store):
    unavailable = _consumer_result("failed", "No eligible route.").model_copy(
        update={
            "error": AgentError(code="runtime_provider_unreachable", retryable=True)
        }
    )
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(
        store,
        unavailable,
        _consumer_result("no_action", "Healthy route completed the retry."),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(store),
    )

    first = _process(orchestrator, task)
    assert first.status == "failed_retryable"
    assert first.error.code == "runtime_provider_unreachable"
    assert len(consumer.calls) == 1
    store.defer_reply_task(
        task.id,
        first.error.code,
        expected_execution_generation=task.execution_generation,
    )
    recovered_task = store.claim_reply_task(task.id)
    assert recovered_task is not None

    second = _process(orchestrator, recovered_task)

    assert second.status == "no_action"
    assert len(consumer.calls) == 2


def test_safely_reopened_runtime_route_starts_one_new_consumer_turn(store):
    unavailable = _consumer_result("failed", "No eligible route.").model_copy(
        update={
            "error": AgentError(code="runtime_provider_unreachable", retryable=True)
        }
    )
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    consumer = ScriptedConsumer(
        store,
        unavailable,
        _consumer_result("no_action", "Healthy route completed the retry."),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(store),
    )

    first = _process(orchestrator, task)
    assert first.status == "failed_retryable"
    failed_run_id = first.final_run_id
    store.fail_reply_task(
        task.id,
        first.error.code,
        expected_execution_generation=task.execution_generation,
    )
    store.retry_failed_reply_task(
        task.id,
        failed_run_id,
        reason="operator_retry_after_runtime_fix",
        recovery_code="operator_retry",
    )
    recovered_task = store.claim_reply_task(task.id)
    assert recovered_task is not None

    second = _process(orchestrator, recovered_task)

    assert second.status == "no_action"
    assert len(consumer.calls) == 2


def test_no_action_finishes_without_launching_audit(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("no_action", "Nothing to do."))
    audit = ScriptedAudit(store)

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

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

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

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
    assert audit.calls[0]["run_id"] != audit.calls[1]["run_id"]
    runs = store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation
    )
    audit_runs = [run for run in runs if run.role is AgentRole.AUDIT]
    assert [run.turn_attempt for run in audit_runs] == [0, 1]
    assert [run.status for run in audit_runs] == ["failed", "completed"]


def test_proposal_is_executed_only_by_fresh_audit_session(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert result.status == "executed"
    assert result.final_role is AgentRole.AUDIT
    assert audit.calls[0]["session_id"] == "audit-session-0"
    assert audit.calls[0]["operation_id"] == (
        f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
    )


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

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

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


def test_canonical_audit_feedback_provided_regenerates_consumer_reply(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "candidate-0"),
        _consumer_result("proposal", "candidate-1"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("feedback_provided", 0),
        _audit_result("executed", 1),
    )

    result = _process(AgentOrchestrator(store=store, consumer=consumer, audit=audit), task)

    assert result.status == "executed"
    assert [call["revision"] for call in consumer.calls] == [0, 1]
    assert consumer.calls[1]["feedback"] is not None
    assert [call["revision"] for call in audit.calls] == [0, 1]


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


def test_executed_audit_with_domain_continuation_starts_next_consumer_revision(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
        _consumer_result("proposal", "confirm-control"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("executed", 0),
        _audit_result("executed", 1),
    )
    continuation = ScriptedDomainContinuation(0)

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=consumer,
            audit=audit,
            domain_continuation=continuation,
        ),
        task,
        refresh_context=lambda: _domain_context(task),
    )

    assert result.status == "executed"
    assert result.feedback_cycles == 0
    assert [call["revision"] for call in consumer.calls] == [0, 1]
    assert consumer.calls[1]["parent"] == audit.calls[0]["run_id"]
    assert consumer.calls[1]["feedback"] is None
    assert [call["revision"] for call in audit.calls] == [0, 1]


def test_domain_continuation_refreshes_consumer_with_one_current_receipt(store):
    task = _task(store)
    current_receipt = _scripted_continuation_receipt()
    initial = _context(task)
    refreshed = replace(initial, prior_receipts=(current_receipt,))
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
        _consumer_result("proposal", "confirm-control"),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(
            store,
            _audit_result("executed", 0),
            _audit_result("executed", 1),
        ),
        domain_continuation=ScriptedDomainContinuation(0),
    )

    result = _process(
        orchestrator,
        task,
        context=initial,
        refresh_context=lambda: refreshed,
    )

    assert result.status == "executed"
    continuation_receipts = tuple(
        receipt
        for receipt in consumer.calls[1]["context"].prior_receipts
        if receipt.operation == "unsubscribe_continuation"
    )
    assert continuation_receipts == (current_receipt,)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("accepted_operations", []),
        ("controls", []),
        ("previous_effect_digest", "mutated-effect"),
        ("instruction", "propose two operations"),
        ("unexpected", "same receipt id but different content"),
    ),
)
def test_domain_continuation_receipt_binds_complete_canonical_content(
    store,
    field,
    replacement,
):
    task = _task(store)
    current = _scripted_continuation_receipt()
    summary = json.loads(current.summary)
    summary[field] = replacement
    mutated = replace(
        current,
        summary=json.dumps(summary, sort_keys=True, separators=(",", ":")),
    )
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
        _consumer_result("proposal", "must-not-run"),
    )
    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=consumer,
            audit=ScriptedAudit(
                store,
                _audit_result("executed", 0),
                _audit_result("executed", 1),
            ),
            domain_continuation=ScriptedDomainContinuation(0),
        ),
        task,
        refresh_context=lambda: replace(
            _context(task),
            prior_receipts=(mutated,),
        ),
    )

    assert result.status == "failed_retryable"
    assert result.error.code == "agent_context_refresh_failed"
    assert [call["revision"] for call in consumer.calls] == [0]


@pytest.mark.parametrize(
    "mutated",
    (
        replace(
            _scripted_continuation_receipt(),
            operation="different_continuation",
        ),
        replace(_scripted_continuation_receipt(), completed=True),
    ),
)
def test_domain_continuation_receipt_binds_operation_and_completion(
    store,
    mutated,
):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "open-entry"))
    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=consumer,
            audit=ScriptedAudit(store, _audit_result("executed", 0)),
            domain_continuation=ScriptedDomainContinuation(0),
        ),
        task,
        refresh_context=lambda: replace(
            _context(task),
            prior_receipts=(mutated,),
        ),
    )

    assert result.status == "failed_retryable"
    assert result.error.code == "agent_context_refresh_failed"
    assert [call["revision"] for call in consumer.calls] == [0]


def _persist_domain_continuation_consumer_retry(store, retry_path: str):
    pending = _task(store)
    task = store.claim_reply_task(pending.id)
    assert task is not None
    context = _context(task)
    first_consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
    )
    first_consumer.run(
        task,
        context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    first_audit = ScriptedAudit(store, _audit_result("executed", 0))
    first_audit.run(
        task,
        AuditTurnContext(
            task=context,
            proposal_revision=0,
            operation_id=(
                f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
            ),
            proposal=_consumer_result("proposal", "open-entry").proposal,
            audit_rules="",
        ),
        turn_attempt=0,
        parent_agent_run_id=first_consumer.calls[0]["run_id"],
    )
    parent_audit_id = first_audit.calls[0]["run_id"]
    failed = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=1,
        turn_attempt=0,
        parent_agent_run_id=parent_audit_id,
        operation_id="",
        owner="domain-consumer-retry",
        lease_seconds=1,
        now=("2020-01-01 00:00:00" if retry_path == "expired" else None),
    )
    assert failed.claimed
    task_error = ""
    if retry_path != "expired":
        errors = {
            "authorization": AgentError(
                code="authorization_wait",
                retryable=True,
                authorization_required=True,
            ),
            "route": AgentError(
                code="runtime_provider_unreachable",
                retryable=True,
            ),
            "provider": AgentError(
                code="codex_provider_unavailable",
                retryable=True,
            ),
            "retryable": AgentError(
                code="consumer_dependency_unavailable",
                retryable=True,
            ),
        }
        error = errors[retry_path]
        store.fail_agent_run(
            failed.run.id,
            error.model_dump(mode="json"),
            owner="domain-consumer-retry",
        )
        if retry_path in {"authorization", "route", "provider"}:
            task_error = error.code
    if task_error:
        task = task.model_copy(update={"error": task_error})
    return task


@pytest.mark.parametrize(
    "retry_path",
    ("authorization", "route", "provider", "retryable", "expired"),
)
def test_domain_continuation_consumer_retry_keeps_fresh_receipt_fence(
    store,
    retry_path,
):
    task = _persist_domain_continuation_consumer_retry(store, retry_path)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("no_action", "retry completed"),
    )
    refresh_calls = 0

    def refresh_context():
        nonlocal refresh_calls
        refresh_calls += 1
        receipt = replace(_scripted_continuation_receipt())
        return replace(_context(task), prior_receipts=(receipt,))

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=consumer,
            audit=ScriptedAudit(store),
            domain_continuation=ScriptedDomainContinuation(0),
        ),
        task,
        refresh_context=refresh_context,
    )

    assert result.status == "no_action"
    assert refresh_calls == 1
    assert len(consumer.calls) == 1
    continuation_receipts = tuple(
        receipt
        for receipt in consumer.calls[0]["context"].prior_receipts
        if receipt.operation == "unsubscribe_continuation"
    )
    assert continuation_receipts == (_scripted_continuation_receipt(),)


@pytest.mark.parametrize(
    "malformed",
    (
        DomainContinuationDecision("continue"),
        DomainContinuationDecision(None),
        DomainContinuationDecision(AgentRole.CONSUMER),
        {"state": "continue"},
    ),
)
def test_malformed_domain_continuation_decision_is_invalid(store, malformed):
    task, _email_state, _existing, audit, _digests = _three_step_email_chain(store)
    audit_run = store.get_agent_run(audit.calls[2]["run_id"])
    assert audit_run is not None
    audit_result = AuditAgentResult.model_validate_json(audit_run.final_result_json)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ScriptedConsumer(store),
        audit=ScriptedAudit(store),
        domain_continuation=MalformedProtocolDomainContinuation(
            continuation_decision=malformed,
        ),
    )

    decision = orchestrator._domain_continuation_state(
        task,
        audit_run,
        audit_result,
    )

    assert decision.state is DomainContinuationState.INVALID


@pytest.mark.parametrize(
    "malformed",
    (
        DomainContinuationConsumptionDecision("consumed"),
        DomainContinuationConsumptionDecision(None),
        DomainContinuationConsumptionDecision(AgentRole.AUDIT),
        {"state": "consumed"},
    ),
)
def test_malformed_domain_consumption_decision_is_invalid(store, malformed):
    task, _email_state, _existing, audit, _digests = _three_step_email_chain(store)
    parent = store.get_agent_run(audit.calls[0]["run_id"])
    child = store.get_agent_run(audit.calls[1]["run_id"])
    assert parent is not None and child is not None
    parent_result = AuditAgentResult.model_validate_json(parent.final_result_json)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=ScriptedConsumer(store),
        audit=ScriptedAudit(store),
        domain_continuation=MalformedProtocolDomainContinuation(
            consumption_decision=malformed,
        ),
    )

    decision = orchestrator._domain_continuation_consumption_state(
        task,
        parent,
        parent_result,
        (child,),
    )

    assert decision.state.value == "invalid"


def test_malformed_consumption_cannot_fall_back_to_current_continuation(store):
    task, _email_state, existing, _audit, _digests = _three_step_email_chain(store)
    receipt = _scripted_continuation_receipt()
    existing.domain_continuation = MalformedProtocolDomainContinuation(
        consumption_decision=DomainContinuationConsumptionDecision("consumed"),
        continuation_decision=DomainContinuationDecision(
            DomainContinuationState.CONTINUE,
            required_receipt_id=receipt.receipt_id,
            required_receipt_binding=domain_continuation_receipt_binding(receipt),
        ),
    )

    recovered = existing._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.error.code == "domain_continuation_state_invalid"


def test_domain_continuations_do_not_consume_content_feedback_cycles(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        *(
            _consumer_result("proposal", f"candidate-{revision}")
            for revision in range(5)
        ),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("executed", 0),
        _audit_result("executed", 1),
        _audit_result("executed", 2),
        _audit_result("revision_required", 3),
        _audit_result("executed", 4),
    )
    continuation = ScriptedDomainContinuation(0, 1, 2)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        domain_continuation=continuation,
    )

    result = _process(
        orchestrator,
        task,
        refresh_context=lambda: _domain_context(task),
    )

    assert result.status == "executed"
    assert result.feedback_cycles == 1
    assert orchestrator._feedback_cycles(task) == 1
    assert [call["feedback"] is not None for call in consumer.calls] == [
        False,
        False,
        False,
        False,
        True,
    ]


def test_real_feedback_exhaustion_fails_after_domain_continuation(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        *(
            _consumer_result("proposal", f"candidate-{revision}")
            for revision in range(5)
        ),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("executed", 0),
        _audit_result("revision_required", 1),
        _audit_result("revision_required", 2),
        _audit_result("revision_required", 3),
        _audit_result("revision_required", 4),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        domain_continuation=ScriptedDomainContinuation(0),
    )

    result = _process(
        orchestrator,
        task,
        refresh_context=lambda: _domain_context(task),
    )

    assert result.status == "failed_terminal"
    assert result.feedback_cycles == 3
    assert result.error.code == "audit_revision_exhausted"
    assert result.feedback is None
    assert orchestrator._feedback_cycles(task) == 4


def test_restart_rejects_consumer_materialized_after_feedback_quota_exhaustion(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        *(
            _consumer_result("proposal", f"candidate-{revision}")
            for revision in range(5)
        ),
    )
    audit = ScriptedAudit(
        store,
        *(_audit_result("revision_required", revision) for revision in range(4)),
    )
    orchestrator = AgentOrchestrator(store=store, consumer=consumer, audit=audit)
    exhausted = _process(orchestrator, task)
    assert exhausted.error.code == "audit_revision_exhausted"
    latest_audit = next(
        run
        for run in reversed(
            store.list_agent_runs_for_task_generation(
                task.id,
                task.execution_generation,
            )
        )
        if run.role is AgentRole.AUDIT
    )
    assert latest_audit.proposal_revision == 3
    consumer.run(
        task,
        _context(task),
        proposal_revision=4,
        parent_agent_run_id=latest_audit.id,
        feedback=_audit_result("revision_required", 3).feedback,
    )

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == "failed_terminal"
    assert recovered.error.code == "audit_revision_exhausted"


def test_restart_rejects_orphan_consumer_when_continuation_disappears(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
        _consumer_result("proposal", "orphan-next-step"),
    )
    audit = ScriptedAudit(store, _audit_result("executed", 0))
    continuation = ScriptedDomainContinuation(0)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        domain_continuation=continuation,
    )
    consumer.run(task, _context(task), proposal_revision=0, parent_agent_run_id=None)
    first_consumer_id = consumer.calls[0]["run_id"]
    audit.run(
        task,
        AuditTurnContext(
            task=_context(task),
            proposal_revision=0,
            operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
            proposal=_consumer_result("proposal", "open-entry").proposal,
            audit_rules="",
        ),
        turn_attempt=0,
        parent_agent_run_id=first_consumer_id,
    )
    state = orchestrator._derive_state(task)
    assert isinstance(state, _NextConsumer)
    consumer.run(
        task,
        _context(task),
        proposal_revision=1,
        parent_agent_run_id=state.parent_run_id,
        feedback=state.feedback,
    )
    continuation.continuation_revisions.clear()

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == "failed_terminal"
    assert recovered.error.code == "domain_continuation_state_invalid"


@pytest.mark.parametrize(
    ("driver", "expected_status", "expected_code"),
    (
        (
            ScriptedDomainContinuation(invalid_revisions=(0,)),
            "failed_terminal",
            "domain_continuation_state_invalid",
        ),
        (
            ScriptedDomainContinuation(unavailable_revisions=(0,)),
            "failed_retryable",
            "domain_continuation_state_unavailable",
        ),
    ),
)
def test_restart_before_next_consumer_fails_closed_on_nonterminal_continuation_state(
    store,
    driver,
    expected_status,
    expected_code,
):
    task = _task(store)
    context = _context(task)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "open-entry"))
    consumer.run(
        task,
        context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    first_consumer_id = consumer.calls[0]["run_id"]
    ScriptedAudit(store, _audit_result("executed", 0)).run(
        task,
        AuditTurnContext(
            task=context,
            proposal_revision=0,
            operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
            proposal=_consumer_result("proposal", "open-entry").proposal,
            audit_rules="",
        ),
        turn_attempt=0,
        parent_agent_run_id=first_consumer_id,
    )

    recovered = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(store),
        domain_continuation=driver,
    )._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == expected_status
    assert recovered.error.code == expected_code


def _email_chain_with_unconsumed_child_audit(store, *, child_state: str):
    task, email_state = _audited_email_task(store)
    context = _context(task)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
        _consumer_result("proposal", "confirm-control"),
    )
    first_audit = ScriptedAudit(store, _audit_result("executed", 0))
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=ScriptedAudit(store),
        domain_continuation=EmailUnsubscribeContinuationDriver(email_state),
    )
    consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    first_audit.run(
        task,
        AuditTurnContext(
            task=context,
            proposal_revision=0,
            operation_id=(
                f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
            ),
            proposal=_consumer_result("proposal", "open-entry").proposal,
            audit_rules="",
        ),
        turn_attempt=0,
        parent_agent_run_id=consumer.calls[0]["run_id"],
    )
    email_state.await_audit(first_audit.calls[0]["run_id"])
    next_consumer = orchestrator._derive_state(task)
    assert isinstance(next_consumer, _NextConsumer)
    consumer.run(
        task,
        context,
        proposal_revision=1,
        parent_agent_run_id=next_consumer.parent_run_id,
        feedback=None,
    )
    child_claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=1,
        turn_attempt=0,
        parent_agent_run_id=consumer.calls[1]["run_id"],
        operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:1",
        owner="unconsumed-child-audit",
    )
    assert child_claim.claimed
    if child_state == "failed":
        store.fail_agent_run(
            child_claim.run.id,
            {"code": "failed_before_effect", "retryable": False},
            owner="unconsumed-child-audit",
        )
    elif child_state == "expired":
        with store._connect() as db:
            db.execute(
                "update agent_runs set lease_expires_at=? where id=?",
                ("2000-01-01 00:00:00", child_claim.run.id),
            )
    elif child_state == "malformed_completed":
        store.complete_agent_run(
            child_claim.run.id,
            {"malformed": True},
            owner="unconsumed-child-audit",
        )
    elif child_state == "unowned_completed":
        with store._connect() as db:
            db.execute(
                "update agent_runs set parent_agent_run_id=? where id=?",
                (consumer.calls[0]["run_id"], child_claim.run.id),
            )
        result = _audit_result("executed", 1)
        result = result.model_copy(
            update={
                "external_result": result.external_result.model_copy(
                    update={"operation_id": child_claim.run.operation_id}
                )
            }
        )
        store.complete_agent_run(
            child_claim.run.id,
            result.model_dump(mode="json"),
            owner="unconsumed-child-audit",
        )
    elif child_state != "running":
        raise AssertionError(f"unsupported child state: {child_state}")
    return task, email_state, orchestrator


def test_running_child_still_validates_current_parent_continuation(store):
    task, email_state, orchestrator = _email_chain_with_unconsumed_child_audit(
        store,
        child_state="running",
    )
    email_state.read_count = 0

    recovered = orchestrator._derive_state(task)

    assert email_state.read_count > 0
    assert not (
        isinstance(recovered, OrchestrationResult)
        and recovered.error.code == "domain_continuation_state_invalid"
    )


def test_malformed_completed_child_validates_parent_then_retries_audit(store):
    task, email_state, orchestrator = _email_chain_with_unconsumed_child_audit(
        store,
        child_state="malformed_completed",
    )
    email_state.read_count = 0

    recovered = orchestrator._derive_state(task)

    assert email_state.read_count > 0
    assert isinstance(recovered, _NextAudit)
    assert recovered.turn_attempt == 1


@pytest.mark.parametrize(
    "child_state",
    (
        "running",
        "expired",
        "failed",
        "malformed_completed",
        "unowned_completed",
    ),
)
def test_unconsumed_child_without_current_continuation_fails_closed(
    store,
    child_state,
):
    task, email_state, orchestrator = _email_chain_with_unconsumed_child_audit(
        store,
        child_state=child_state,
    )
    email_state.continuation = None

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == "failed_terminal"
    assert recovered.error.code == "domain_continuation_state_invalid"


def test_store_unavailable_while_proving_child_consumption_is_retryable(store):
    task, email_state, orchestrator = _email_chain_with_unconsumed_child_audit(
        store,
        child_state="running",
    )
    email_state.unavailable = True

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == "failed_retryable"
    assert recovered.error.code == "domain_continuation_state_unavailable"


@pytest.mark.parametrize("terminal_after_audit_one", (False, True))
def test_historical_executed_parent_uses_child_audit_lineage_not_current_fence(
    store,
    terminal_after_audit_one,
):
    task, email_state = _audited_email_task(store)
    context = _context(task)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
        _consumer_result("proposal", "confirm-control"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("executed", 0),
        _audit_result("executed", 1),
    )
    driver = EmailUnsubscribeContinuationDriver(email_state)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        domain_continuation=driver,
    )

    consumer.run(
        task,
        context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    audit.run(
        task,
        AuditTurnContext(
            task=context,
            proposal_revision=0,
            operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
            proposal=_consumer_result("proposal", "open-entry").proposal,
            audit_rules="",
        ),
        turn_attempt=0,
        parent_agent_run_id=consumer.calls[0]["run_id"],
    )
    email_state.await_audit(audit.calls[0]["run_id"])
    next_consumer = orchestrator._derive_state(task)
    assert isinstance(next_consumer, _NextConsumer)
    consumer.run(
        task,
        context,
        proposal_revision=1,
        parent_agent_run_id=next_consumer.parent_run_id,
        feedback=None,
    )
    audit.run(
        task,
        AuditTurnContext(
            task=context,
            proposal_revision=1,
            operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:1",
            proposal=_consumer_result("proposal", "confirm-control").proposal,
            audit_rules="",
        ),
        turn_attempt=0,
        parent_agent_run_id=consumer.calls[1]["run_id"],
    )
    email_state.await_audit(audit.calls[1]["run_id"])
    if terminal_after_audit_one:
        email_state.complete()

    recovered = orchestrator._derive_state(task)

    if terminal_after_audit_one:
        assert isinstance(recovered, OrchestrationResult)
        assert recovered.status == "executed"
        assert recovered.final_run_id == audit.calls[1]["run_id"]
    else:
        assert isinstance(recovered, _NextConsumer)
        assert recovered.proposal_revision == 2
        assert recovered.parent_run_id == audit.calls[1]["run_id"]


def _three_step_email_chain(store, *, terminal: bool = False):
    task, email_state = _audited_email_task(store)
    context = _context(task)
    consumer = ScriptedConsumer(
        store,
        *(
            _consumer_result("proposal", f"unsubscribe-step-{revision}")
            for revision in range(3)
        ),
    )
    audit = ScriptedAudit(
        store,
        *(_audit_result("executed", revision) for revision in range(3)),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        domain_continuation=EmailUnsubscribeContinuationDriver(email_state),
    )
    effect_digests = []
    parent_run_id = None
    for revision in range(3):
        consumer.run(
            task,
            context,
            proposal_revision=revision,
            parent_agent_run_id=parent_run_id,
            feedback=None,
        )
        audit.run(
            task,
            AuditTurnContext(
                task=context,
                proposal_revision=revision,
                operation_id=(
                    f"agent-task:{task.id}:{task.execution_generation}:"
                    f"proposal:{revision}"
                ),
                proposal=_consumer_result(
                    "proposal",
                    f"unsubscribe-step-{revision}",
                ).proposal,
                audit_rules="",
                ),
            turn_attempt=0,
            parent_agent_run_id=consumer.calls[revision]["run_id"],
        )
        email_state.await_audit(audit.calls[revision]["run_id"])
        effect_digests.append(email_state.effect["effect_digest"])
        if revision < 2:
            next_consumer = orchestrator._derive_state(task)
            assert isinstance(next_consumer, _NextConsumer)
            assert next_consumer.proposal_revision == revision + 1
            parent_run_id = next_consumer.parent_run_id
    if terminal:
        email_state.complete()
    return task, email_state, orchestrator, audit, tuple(effect_digests)


def _many_step_email_chain(store, revisions: int):
    task, email_state = _audited_email_task(store)
    context = _context(task)
    consumer = ScriptedConsumer(
        store,
        *(
            _consumer_result("proposal", f"unsubscribe-step-{revision}")
            for revision in range(revisions)
        ),
    )
    audit = ScriptedAudit(
        store,
        *(_audit_result("executed", revision) for revision in range(revisions)),
    )
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        domain_continuation=EmailUnsubscribeContinuationDriver(email_state),
    )
    parent_run_id = None
    for revision in range(revisions):
        consumer.run(
            task,
            context,
            proposal_revision=revision,
            parent_agent_run_id=parent_run_id,
            feedback=None,
        )
        audit.run(
            task,
            AuditTurnContext(
                task=context,
                proposal_revision=revision,
                operation_id=(
                    f"agent-task:{task.id}:{task.execution_generation}:"
                    f"proposal:{revision}"
                ),
                proposal=_consumer_result(
                    "proposal",
                    f"unsubscribe-step-{revision}",
                ).proposal,
                audit_rules="",
                ),
            turn_attempt=0,
            parent_agent_run_id=consumer.calls[revision]["run_id"],
        )
        email_state.await_audit(audit.calls[revision]["run_id"])
        if revision < revisions - 1:
            next_consumer = orchestrator._derive_state(task)
            assert isinstance(next_consumer, _NextConsumer)
            parent_run_id = next_consumer.parent_run_id
    return task, email_state, orchestrator


@pytest.mark.parametrize("revisions", (20, 32))
def test_large_unsubscribe_history_is_loaded_and_validated_once_per_snapshot(
    store,
    revisions,
):
    task, email_state, orchestrator = _many_step_email_chain(store, revisions)
    email_state.read_count = 0
    email_state.effect_rows_read = 0

    recovered = orchestrator._derive_state(task)

    if revisions < 32:
        assert isinstance(recovered, _NextConsumer)
        assert recovered.proposal_revision == revisions
    else:
        assert isinstance(recovered, OrchestrationResult)
        assert recovered.status == "failed_terminal"
        assert recovered.error.code == "domain_continuation_limit_reached"
    assert email_state.read_count == 1
    assert email_state.effect_rows_read == revisions


def test_restart_at_durable_operation_limit_never_creates_consumer_revision_33(
    store,
):
    task, email_state, _orchestrator = _many_step_email_chain(store, 32)
    retry_consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "must-not-run-revision-33"),
    )
    restarted = AgentOrchestrator(
        store=store,
        consumer=retry_consumer,
        audit=ScriptedAudit(store),
        domain_continuation=EmailUnsubscribeContinuationDriver(email_state),
    )

    result = _process(
        restarted,
        task,
        refresh_context=lambda: _domain_context(task),
    )

    assert result.status == "failed_terminal"
    assert result.error.code == "domain_continuation_limit_reached"
    assert retry_consumer.calls == []


def test_awaiting_audit_with_31_operations_may_continue(store):
    task, _email_state, orchestrator = _many_step_email_chain(store, 31)

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, _NextConsumer)
    assert recovered.proposal_revision == 31


@pytest.mark.parametrize("terminal", (False, True))
def test_three_step_history_proves_consumed_parents_after_current_fence_advances(
    store,
    terminal,
):
    task, _email_state, orchestrator, audit, _digests = _three_step_email_chain(
        store,
        terminal=terminal,
    )

    recovered = orchestrator._derive_state(task)

    if terminal:
        assert isinstance(recovered, OrchestrationResult)
        assert recovered.status == "executed"
        assert recovered.final_run_id == audit.calls[2]["run_id"]
    else:
        assert isinstance(recovered, _NextConsumer)
        assert recovered.proposal_revision == 3
        assert recovered.parent_run_id == audit.calls[2]["run_id"]


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_historical_effect",
        "missing_parent_effect",
        "broken_digest_chain",
        "cycle",
        "wrong_audit_binding",
        "wrong_parent_audit_binding",
        "operation_prefix_mutation",
        "parent_operation_prefix_mutation",
    ),
)
def test_historical_consumption_rejects_broken_effect_lineage(store, mutation):
    task, email_state, orchestrator, _audit, digests = _three_step_email_chain(
        store
    )
    if mutation == "missing_historical_effect":
        del email_state.effects[digests[1]]
    elif mutation == "missing_parent_effect":
        del email_state.effects[digests[0]]
    elif mutation == "broken_digest_chain":
        email_state.effects[digests[2]]["previous_effect_digest"] = "a" * 64
    elif mutation == "cycle":
        email_state.effects[digests[2]]["previous_effect_digest"] = digests[2]
    elif mutation == "wrong_audit_binding":
        email_state.effects[digests[1]]["audit_agent_run_id"] = 999_999
    elif mutation == "wrong_parent_audit_binding":
        email_state.effects[digests[0]]["audit_agent_run_id"] = 999_999
    elif mutation == "operation_prefix_mutation":
        email_state.effects[digests[1]]["operations"][-1] = {
            "operation_reference": (
                "unsubscribe-operation:" + sha256(b"mutated-prefix").hexdigest()
            ),
            "kind": "click_confirmation",
            "target_reference": (
                "unsubscribe-control:" + sha256(b"mutated-target").hexdigest()
            ),
        }
    elif mutation == "parent_operation_prefix_mutation":
        email_state.effects[digests[0]]["operations"][0] = {
            "operation_reference": (
                "unsubscribe-operation:" + sha256(b"mutated-parent").hexdigest()
            ),
            "kind": "open_entry",
            "target_reference": email_state.claim["entry_reference"],
        }

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == "failed_terminal"
    assert recovered.error.code == "domain_continuation_state_invalid"


def test_historical_effect_store_error_is_retryable(store):
    task, email_state, orchestrator, _audit, digests = _three_step_email_chain(
        store
    )
    email_state.unavailable_effect_digest = digests[1]

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == "failed_retryable"
    assert recovered.error.code == "domain_continuation_state_unavailable"


def test_real_driver_rejects_orphan_consumer_without_current_continuation(store):
    task, email_state = _audited_email_task(store)
    context = _context(task)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "open-entry"),
        _consumer_result("proposal", "orphan-control"),
    )
    audit = ScriptedAudit(store, _audit_result("executed", 0))
    driver = EmailUnsubscribeContinuationDriver(email_state)
    orchestrator = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=audit,
        domain_continuation=driver,
    )
    consumer.run(
        task,
        context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    audit.run(
        task,
        AuditTurnContext(
            task=context,
            proposal_revision=0,
            operation_id=f"agent-task:{task.id}:{task.execution_generation}:proposal:0",
            proposal=_consumer_result("proposal", "open-entry").proposal,
            audit_rules="",
        ),
        turn_attempt=0,
        parent_agent_run_id=consumer.calls[0]["run_id"],
    )
    email_state.await_audit(audit.calls[0]["run_id"])
    next_consumer = orchestrator._derive_state(task)
    assert isinstance(next_consumer, _NextConsumer)
    consumer.run(
        task,
        context,
        proposal_revision=1,
        parent_agent_run_id=next_consumer.parent_run_id,
        feedback=None,
    )
    email_state.complete()

    recovered = orchestrator._derive_state(task)

    assert isinstance(recovered, OrchestrationResult)
    assert recovered.status == "failed_terminal"
    assert recovered.error.code == "domain_continuation_state_invalid"


def test_non_email_executed_audit_remains_terminal_without_domain_driver(store):
    task = _task(store)
    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(
                store,
                _consumer_result("proposal", "candidate"),
            ),
            audit=ScriptedAudit(store, _audit_result("executed", 0)),
        ),
        task,
    )

    assert result.status == "executed"
    runs = store.list_agent_runs_for_task_generation(
        task.id,
        task.execution_generation,
    )
    assert [(run.role, run.proposal_revision) for run in runs] == [
        (AgentRole.CONSUMER, 0),
        (AgentRole.AUDIT, 0),
    ]


def test_domain_continuation_is_bounded_by_global_turn_limit(store):
    task = _task(store)
    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=ScriptedConsumer(
                store,
                *(
                    _consumer_result("proposal", f"candidate-{revision}")
                    for revision in range(MAX_TURNS_PER_PROCESS)
                ),
            ),
            audit=ScriptedAudit(
                store,
                *(
                    _audit_result("executed", revision)
                    for revision in range(MAX_TURNS_PER_PROCESS)
                ),
            ),
            domain_continuation=ScriptedDomainContinuation(
                *range(MAX_TURNS_PER_PROCESS)
            ),
        ),
        task,
        refresh_context=lambda: _domain_context(task),
    )

    assert result.status == "failed_retryable"
    assert result.error.code == "agent_turn_limit_reached"
    assert result.feedback_cycles == 0


def test_domain_revision_rejected_when_continuation_driver_is_terminal(store):
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
            domain_continuation=ScriptedDomainContinuation(),
        ),
        task,
    )

    assert result.status == "failed_terminal"
    assert result.error.code == "domain_continuation_state_invalid"
    assert corrected_audit.calls == []


def test_bounded_fact_finding_option_is_regenerated_without_human_escalation(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _bounded_needs_human_result(),
        _consumer_result("proposal", "candidate-0"),
    )

    result = _process(
        AgentOrchestrator(
            store=store,
            consumer=consumer,
            audit=ScriptedAudit(store, _audit_result("executed", 0)),
        ),
        task,
    )

    assert result.status == "executed"
    assert len(consumer.calls) == 2


def test_infrastructure_retry_does_not_consume_feedback_cycle(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(
        store,
        _audit_result("failed", 0, code="temporary_unavailable", retryable=True),
        _audit_result("executed", 0),
    )

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert result.status == "executed"
    assert result.feedback_cycles == 0
    assert [call["revision"] for call in audit.calls] == [0, 0]
    assert [call["turn_attempt"] for call in audit.calls] == [0, 1]
    assert audit.calls[0]["operation_id"] == audit.calls[1]["operation_id"]
    assert audit.calls[0]["session_id"] == audit.calls[1]["session_id"]


def test_newer_context_stale_candidate_is_revised_without_write(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "publish-v1"),
        _consumer_result("no_action", "New context makes the action unnecessary."),
    )
    audit = ScriptedAudit(store, _audit_result("revision_required", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert result.status == "no_action"
    assert result.final_role is AgentRole.CONSUMER
    assert len(audit.calls) == 1
    audit_run = store.get_agent_run(result.final_run_id - 1)
    assert audit_run is not None and audit_run.status == "completed"


def test_fourth_revision_request_is_failed_without_a_valid_human_decision(store):
    task = _task(store)
    consumer = ScriptedConsumer(
        store,
        _consumer_result("proposal", "candidate-0"),
        _consumer_result("proposal", "candidate-1"),
        _consumer_result("proposal", "candidate-2"),
        _consumer_result("proposal", "candidate-3"),
    )
    audit = ScriptedAudit(
        store,
        _audit_result("revision_required", 0),
        _audit_result("revision_required", 1),
        _audit_result("revision_required", 2),
        _audit_result("revision_required", 3),
    )

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert result.status == "failed_terminal"
    assert result.feedback_cycles == 3
    assert result.error.code == "audit_revision_exhausted"
    assert result.audit_result is not None
    assert result.audit_result.outcome is AuditOutcome.FAILED
    assert result.feedback is None
    latest_audit = max(
        (
            run
            for run in store.list_agent_runs_for_task_generation(
                task.id,
                task.execution_generation,
            )
            if run.role is AgentRole.AUDIT
        ),
        key=lambda run: run.id,
    )
    assert result.final_run_id == latest_audit.id


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

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert result.status == "failed_retryable"
    assert result.feedback_cycles == 0
    assert len(audit.calls) == 1


def test_authorization_recovery_retries_audit_with_next_turn_attempt(store):
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
        _audit_result("executed", 0),
    )
    orchestrator = AgentOrchestrator(store=store, consumer=consumer, audit=audit)

    first = _process(orchestrator, task)
    assert first.status == "failed_retryable"

    # The worker persists the authorization failure before scheduling a retry.
    task = task.model_copy(update={"error": "authorization_wait"})
    recovered = _process(orchestrator, task)

    assert recovered.status == "executed"
    assert [call["turn_attempt"] for call in audit.calls] == [0, 1]


def test_expired_audit_turn_creates_a_new_append_only_run(store):
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
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit),
        task,
    )

    assert result.status == "executed"
    assert result.final_run_id != stale.id
    assert store.get_agent_run(stale.id).status == "failed"
    assert audit.calls[0]["turn_attempt"] == 1


def test_dry_run_audit_finishes_without_creating_a_human_decision(store):
    task = _task(store)
    consumer = ScriptedConsumer(store, _consumer_result("proposal", "candidate-0"))
    audit = ScriptedAudit(store, _audit_result("dry_run", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit), task
    )

    assert result.status == "dry_run"
    assert result.audit_result is not None
    assert result.audit_result.outcome is AuditOutcome.DRY_RUN
    assert result.audit_result.decision_options == ()


def test_safely_reopened_runtime_route_reuses_proposal_in_new_audit_run(store):
    pending_task = _task(store)
    task = store.claim_reply_task(pending_task.id)
    assert task is not None
    audit = ScriptedAudit(
        store,
        _audit_result(
            "failed",
            0,
            code="runtime_provider_unreachable",
            retryable=True,
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
    store.fail_reply_task(
        task.id,
        first.error.code,
        expected_execution_generation=task.execution_generation,
    )
    store.retry_failed_reply_task(
        task.id,
        first.final_run_id,
        reason="operator_retry_after_runtime_fix",
        recovery_code="operator_retry",
    )
    recovered_task = store.claim_reply_task(task.id)
    assert recovered_task is not None

    second = _process(orchestrator, recovered_task)

    assert second.status == "executed"
    assert [call["turn_attempt"] for call in audit.calls] == [0, 1]
    assert audit.calls[0]["run_id"] != audit.calls[1]["run_id"]


def test_live_okr_source_failure_reuses_prior_proposal_for_audit(store):
    """A transient live-source failure must not discard a valid proposal."""
    pending_task = _task(store)
    task = store.claim_reply_task(pending_task.id)
    assert task is not None
    proposal = _consumer_result("proposal", "fallback applicant notification")
    unavailable = _consumer_result("failed", "Live OKR source unavailable.").model_copy(
        update={
            "error": AgentError(
                code="provider_read_failed",
                retryable=True,
                authorization_required=False,
            )
        }
    )
    consumer = ScriptedConsumer(store, proposal, unavailable)
    context = _context(task)
    consumer.run(
        task,
        context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    consumer.run(
        task,
        context,
        proposal_revision=0,
        parent_agent_run_id=None,
    )
    audit = ScriptedAudit(store, _audit_result("executed", 0))

    result = _process(
        AgentOrchestrator(store=store, consumer=consumer, audit=audit),
        task,
    )

    assert result.status == "executed"
    assert len(consumer.calls) == 2
    assert len(audit.calls) == 1
    assert audit.calls[0]["proposal"].objective == "fallback applicant notification"


def test_retryable_audit_exhaustion_terminalizes_latest_run(store):
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

    assert result.status == "failed_terminal"
    assert result.final_role is AgentRole.AUDIT
    assert result.final_run_id == audit.calls[-1]["run_id"]
    assert result.error.code == "audit_unavailable"
    assert result.error.retryable is False
    assert result.feedback_cycles == 0
    assert len(audit.calls) == 2


def test_retryable_consumer_exhaustion_terminalizes_latest_run(store):
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

    assert result.status == "failed_terminal"
    assert result.final_role is AgentRole.CONSUMER
    assert result.final_run_id == consumer.calls[-1]["run_id"]
    assert result.error.code == "consumer_unavailable"
    assert result.error.retryable is False
    assert result.feedback_cycles == 0
    assert len(consumer.calls) == 2


def test_retryable_consumer_exhaustion_preserves_live_okr_read_error(store):
    failure = ConsumerAgentResult.model_validate(
        {
            "outcome": "failed",
            "summary": "Live OKR source unavailable.",
            "proposal": None,
            "error": {
                "code": "live_okr_and_supporting_evidence_unavailable",
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

    assert result.status == "failed_terminal"
    assert result.error.code == "live_okr_and_supporting_evidence_unavailable"
    assert result.summary == (
        "live_okr_and_supporting_evidence_unavailable; consumer retry attempts exhausted"
    )


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
    assert failed.status == "failed_terminal"
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
            consumer=ScriptedConsumer(
                store, _consumer_result("proposal", "candidate-0")
            ),
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
            consumer=ScriptedConsumer(
                store, _consumer_result("proposal", "candidate-0")
            ),
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
            consumer=ScriptedConsumer(
                store, _consumer_result("proposal", "candidate-0")
            ),
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

    deferred = [task for task, result in results if result.status == "failed_retryable"]
    assert len(deferred) == 1
    retry = _process(orchestrator, deferred[0])

    assert retry.status == "no_action"
    assert consumer.max_active == 1
    assert set(consumer.sessions) == {"shared-consumer-session"}
    assert store.get_codex_session_id("same-conversation") == "shared-consumer-session"
