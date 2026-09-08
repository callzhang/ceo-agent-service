from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.agent_cli as agent_cli
from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import (
    EmailPersistenceCorruption,
    EmailStore,
    EmailUnsubscribeClaimConflict,
    email_action_identity,
    email_unsubscribe_effect_digest,
)
from app.email_task_adapter import (
    accepted_email_unsubscribe_effect,
    email_conversation_id,
)
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    UnsubscribeExecutor,
    UnsubscribeExecutionResult,
    UnsubscribeObservation,
    UnsubscribeOutcome,
    UnsubscribePageState,
    UnsubscribeTerminalReceipt,
    extract_unsubscribe_entries,
    normalize_unsubscribe_result_text,
)
from app.email_unsubscribe_audit import EmailUnsubscribeAuditOperation
from app.email_unsubscribe_audit import _store_arguments as audit_store_arguments
from app.store import AgentRole, AutoReplyStore


PRIVATE_URL = "https://news.example.com/unsubscribe?token=private-token"
ACCOUNT_ID = "account-primary"
MESSAGE_IDENTITY = "account-primary:message-id:<mail-41@example.com>"
THREAD_IDENTITY = "thread-41"
CLASSIFICATION_ID = 41
PLAN = build_versioned_email_action_plan(
    action_plan_version=1,
    classification_id=CLASSIFICATION_ID,
    account_id=ACCOUNT_ID,
    category=EmailCategory.JUNK,
    classification_source="user",
    confidence=1.0,
    model_id="email-model:test-audit",
    config_version="email-config:test-audit",
    actions=(EmailAction.UNSUBSCRIBE,),
    action_parameters={},
    created_at=datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc),
)
ACTION_IDENTITY = email_action_identity(
    account_id=ACCOUNT_ID,
    stable_message_identity=MESSAGE_IDENTITY,
    action_type=EmailAction.UNSUBSCRIBE,
    action_plan_version=PLAN.action_plan_version,
)
ENTRY = extract_unsubscribe_entries(list_unsubscribe=f"<{PRIVATE_URL}>")[0]
NETWORK_POLICY = BrowserNetworkPolicy(frozenset({"https://news.example.com"}))
SECONDARY_ENTRY = extract_unsubscribe_entries(
    list_unsubscribe="<https://news.example.com/preferences>"
)[0]


def test_legacy_unsubscribe_write_tool_is_not_registered() -> None:
    tool_names = {tool.name for tool in asyncio.run(agent_cli.server.list_tools())}

    assert "execute_email_unsubscribe" not in tool_names
    assert "execute_audited_email_unsubscribe" in tool_names


@dataclass(frozen=True)
class AuditFixture:
    email_store: EmailStore
    task_store: AutoReplyStore
    task: object
    consumer_run: object
    audit_run: object
    operation: EmailUnsubscribeAuditOperation
    resolved: list[object]
    executed: list[object]


def _payload() -> dict[str, object]:
    return {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": "unsubscribe",
        "action_identity": ACTION_IDENTITY,
        "action_plan_id": PLAN.action_plan_id,
        "action_plan_version": PLAN.action_plan_version,
        "classification_id": CLASSIFICATION_ID,
        "account_id": ACCOUNT_ID,
        "stable_message_identity": MESSAGE_IDENTITY,
        "thread_identity": THREAD_IDENTITY,
        "unsubscribe_entries": [
            {
                "index": ENTRY.index,
                "source": "header_https",
                "digest": ENTRY.reference.removeprefix("unsubscribe-entry:"),
                "reference": ENTRY.reference,
            }
        ],
        "unsubscribe_authentication": None,
        "unsubscribe_network_policy_reference": NETWORK_POLICY.reference,
        "unsubscribe_network_policy_origin_references": list(
            NETWORK_POLICY.origin_references
        ),
    }


def _accepted_action() -> dict[str, object]:
    return ProposedAction.model_validate(
        {
            "action_identity": ACTION_IDENTITY,
            "description": "Unsubscribe the current subscription",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": ACTION_IDENTITY,
                "account_id": ACCOUNT_ID,
                "stable_message_identity": MESSAGE_IDENTITY,
                "thread_identity": THREAD_IDENTITY,
                "entry_reference": ENTRY.reference,
                "network_policy_reference": NETWORK_POLICY.reference,
                "network_policy_origin_references": list(
                    NETWORK_POLICY.origin_references
                ),
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "unsubscribe-operation:open-entry",
                        "kind": "open_entry",
                        "target_reference": ENTRY.reference,
                    }
                ]
            },
        }
    ).model_dump(mode="json")


def _consumer_proposal_result(
    *actions: dict[str, object],
) -> dict[str, object]:
    return {
        "outcome": "proposal",
        "summary": "Propose one audited unsubscribe operation.",
        "proposal": {
            "objective": "Unsubscribe the current subscription.",
            "actions": list(actions or (_accepted_action(),)),
            "sourced_facts": [],
            "authored_judgment": "The ActionPlan authorizes unsubscribe.",
        },
        "decision_options": [],
        "error": {
            "code": "",
            "retryable": False,
            "authorization_required": False,
            "stage": "",
            "source": "",
            "source_code": "",
            "session_continuable": False,
        },
        "risk": "low",
        "confidence": 1.0,
    }


def _seed_email_state(path: Path) -> EmailStore:
    store = EmailStore(path)
    store.create_account(
        {
            "account_id": ACCOUNT_ID,
            "display_name": "Primary",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-test",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://smtp-test",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": CLASSIFICATION_ID,
                "stable_message_identity": MESSAGE_IDENTITY,
                "provider_locator": {
                    "account_id": ACCOUNT_ID,
                    "folder": "INBOX",
                    "uidvalidity": 42,
                    "uid": 41,
                    "rfc_message_id": "<mail-41@example.com>",
                    "thread_id": THREAD_IDENTITY,
                },
                "category": EmailCategory.JUNK,
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"junk": 1.0},
                "model_id": PLAN.model_id,
                "config_version": PLAN.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": "user",
                "action_plan": PLAN,
            }
        ),
        sender="sender@example.com",
        subject="Newsletter",
        model_text="__subject__newsletter",
        received_at="2026-09-02T08:00:00+00:00",
    )
    return store


def _make_fixture(tmp_path: Path) -> AuditFixture:
    path = tmp_path / "audited-unsubscribe.sqlite3"
    email_store = _seed_email_state(path)
    task_store = AutoReplyStore(path)
    task = task_store.ensure_reply_task(
        channel="email",
        conversation_id=email_conversation_id(ACCOUNT_ID, THREAD_IDENTITY),
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=ACTION_IDENTITY,
        trigger_create_time="2026-09-02T08:00:00+00:00",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=json.dumps(_payload(), sort_keys=True),
        execution_generation="generation-audit-1",
    )
    task = task_store.claim_reply_task(task.id)
    assert task is not None
    consumer = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer-owner",
    ).run
    consumer = task_store.complete_agent_run(
        consumer.id,
        _consumer_proposal_result(_accepted_action()),
        owner="consumer-owner",
    )
    audit = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="audit-operation-0",
        owner="audit-owner",
    ).run
    resolved: list[object] = []
    executed: list[object] = []

    def resolve_entries(locator, expected_reference, **_kwargs):
        resolved.append((locator, expected_reference))
        return (ENTRY,)

    def execute_effect(effect, entries, *, owner):
        executed.append((effect, entries, owner))
        _text, observation_digest = normalize_unsubscribe_result_text(
            "You have been unsubscribed"
        )
        return {
            "status": "done",
            "outcome": "done",
            "receipt_id": "provider-receipt:audit-41",
            "evidence": "terminal-page",
            "result_text": "You have been unsubscribed",
            "observation_digest": observation_digest,
            "started_at": "2026-09-02T08:00:01+00:00",
            "completed_at": "2026-09-02T08:00:02+00:00",
            "summary": "Unsubscribe completed",
            "final_step": {
                "sequence": 1,
                "operation": "open_entry",
                "state": "done",
                "reference": "provider-receipt:audit-41",
            },
        }

    operation = EmailUnsubscribeAuditOperation(
        task_store=task_store,
        email_store=email_store,
        resolve_entries=resolve_entries,
        execute_effect=execute_effect,
    )
    return AuditFixture(
        email_store,
        task_store,
        task,
        consumer,
        audit,
        operation,
        resolved,
        executed,
    )


def _error_code(result: dict[str, object]) -> str:
    error = result.get("error")
    assert isinstance(error, dict)
    return str(error.get("code"))


class _TerminalBrowser:
    def __init__(self, visible_text: str) -> None:
        self.visible_text = visible_text
        self.calls: list[str] = []

    def find_confirmation_receipt(self, _effect):
        self.calls.append("receipt")
        return None

    def inspect_current_state(self, _effect, _private_url):
        raise AssertionError("preclaimed Audit execution must not inspect before write")

    def execute_operation(self, effect, _private_url, operation):
        self.calls.append(operation.operation_reference)
        return UnsubscribeObservation(
            state=UnsubscribePageState.DONE,
            state_reference="state-done",
            receipt=UnsubscribeTerminalReceipt(
                receipt_id="provider-receipt:executor-audit-41",
                evidence="terminal-page",
                entry_reference=effect.entry_reference,
                effect_digest=effect.effect_digest,
            ),
            visible_text=self.visible_text,
        )


def _execute_with_durable_executor(
    fixture: AuditFixture,
    browser: _TerminalBrowser,
    *,
    after_persist=None,
):
    def execute_effect(
        effect,
        entries,
        *,
        owner,
        executed_prefix_length,
    ):
        result = UnsubscribeExecutor(
            fixture.email_store,
            browser,
            owner=owner,
        ).execute(
            effect,
            entries,
            executed_prefix_length=executed_prefix_length,
        )
        assert isinstance(result, UnsubscribeExecutionResult)
        if after_persist is not None:
            return after_persist(result, owner)
        return result

    fixture.operation.execute_effect = execute_effect
    return fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )


def _public_terminal_mapping(
    result: UnsubscribeExecutionResult,
) -> dict[str, object]:
    assert result.receipt is not None
    return {
        "status": "done",
        "outcome": result.outcome.value,
        "receipt_id": result.receipt.receipt_id,
        "evidence": result.receipt.evidence,
        "result_text": result.result_text,
        "observation_digest": result.observation_digest,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "summary": result.result_text or result.outcome.value,
        "final_step": {
            "sequence": len(result.journal),
            "operation": result.journal[-1].operation,
            "state": result.journal[-1].state,
            "reference": result.journal[-1].reference,
        },
    }


def _two_step_action() -> dict[str, object]:
    action = _accepted_action()
    operations = action["payload"]["operations"]
    assert isinstance(operations, list)
    operations.append(
        {
            "operation_reference": "unsubscribe-operation:submit-form",
            "kind": "submit_form",
            "target_reference": "unsubscribe-control:form",
        }
    )
    return action


def _start_second_audit_round(fixture: AuditFixture):
    def persist_first_continuation(effect, _entries, *, owner, **_kwargs):
        continuation = fixture.email_store.persist_email_unsubscribe_continuation(
            **audit_store_arguments(effect),
            controls=(
                {
                    "reference": "unsubscribe-control:form",
                    "kind": "form",
                    "intent": "unsubscribe",
                },
            ),
            observation_reference="unsubscribe-state:form",
            final_step={
                "sequence": 1,
                "operation": "open_entry",
                "state": "action_required",
                "reference": "unsubscribe-state:form",
            },
            owner=owner,
        )
        return {
            "status": "awaiting_audit",
            "summary": "One further audited operation is required.",
            "continuation": continuation,
        }

    fixture.operation.execute_effect = persist_first_continuation
    first = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )
    assert first["status"] == "awaiting_audit", first
    first_audit = fixture.task_store.complete_agent_run(
        fixture.audit_run.id,
        {"outcome": "executed", "proposal_revision": 0},
        owner="audit-owner",
    )
    second_consumer = fixture.task_store.claim_agent_run(
        fixture.task.id,
        fixture.task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=1,
        turn_attempt=0,
        parent_agent_run_id=first_audit.id,
        operation_id="",
        owner="consumer-second-owner",
    ).run
    second_consumer = fixture.task_store.complete_agent_run(
        second_consumer.id,
        _consumer_proposal_result(_two_step_action()),
        owner="consumer-second-owner",
    )
    second_audit = fixture.task_store.claim_agent_run(
        fixture.task.id,
        fixture.task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=1,
        turn_attempt=0,
        parent_agent_run_id=second_consumer.id,
        operation_id="audit-operation-1",
        owner="audit-second-owner",
    ).run
    return first_audit, second_consumer, second_audit


def _complete_two_step_terminal(fixture: AuditFixture):
    first_audit, second_consumer, second_audit = _start_second_audit_round(fixture)

    def persist_terminal(_effect, _entries, **_kwargs):
        result_text = "You have been unsubscribed"
        _, observation_digest = normalize_unsubscribe_result_text(result_text)
        return {
            "status": "done",
            "outcome": "done",
            "receipt_id": "provider-receipt:two-step",
            "evidence": "terminal-page",
            "result_text": result_text,
            "observation_digest": observation_digest,
            "started_at": "2026-09-02T08:00:01+00:00",
            "completed_at": "2026-09-02T08:00:02+00:00",
            "summary": "Unsubscribe completed",
            "final_step": {
                "sequence": 2,
                "operation": "submit_form",
                "state": "done",
                "reference": "provider-receipt:two-step",
            },
        }

    fixture.operation.execute_effect = persist_terminal
    action = _two_step_action()
    second = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=second_audit.id,
        accepted_action=action,
    )
    assert second["status"] == "done", second
    second_audit = fixture.task_store.complete_agent_run(
        second_audit.id,
        {"outcome": "executed", "proposal_revision": 1},
        owner="audit-second-owner",
    )
    return action, first_audit, second_consumer, second_audit, second


def _claim_recovery_audit(
    fixture: AuditFixture,
    *,
    parent_consumer_id: int,
):
    return fixture.task_store.claim_agent_run(
        fixture.task.id,
        fixture.task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=1,
        turn_attempt=1,
        parent_agent_run_id=parent_consumer_id,
        operation_id="audit-operation-recovery",
        owner="audit-recovery-owner",
    ).run


def test_two_step_terminal_recovers_in_new_audit_run_without_callback(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    action, _first_audit, second_consumer, _second_audit, terminal = (
        _complete_two_step_terminal(fixture)
    )
    recovery_audit = _claim_recovery_audit(
        fixture,
        parent_consumer_id=second_consumer.id,
    )

    def forbidden_callback(*_args, **_kwargs):
        raise AssertionError("terminal recovery must not replay the browser callback")

    fixture.operation.execute_effect = forbidden_callback
    recovered = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=recovery_audit.id,
        accepted_action=action,
    )

    assert recovered["status"] == "done", recovered
    assert recovered["receipt_id"] == terminal["receipt_id"]


@pytest.mark.parametrize("mutation", ("prefix", "target", "operation"))
def test_two_step_terminal_recovery_rejects_changed_accepted_action(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    action, _first_audit, second_consumer, _second_audit, _terminal = (
        _complete_two_step_terminal(fixture)
    )
    recovery_audit = _claim_recovery_audit(
        fixture,
        parent_consumer_id=second_consumer.id,
    )
    if mutation == "target":
        target = action["target"]
        assert isinstance(target, dict)
        target["account_id"] = "account-changed"
    else:
        operations = action["payload"]["operations"]
        assert isinstance(operations, list)
        operation = operations[0] if mutation == "prefix" else operations[-1]
        assert isinstance(operation, dict)
        if mutation == "prefix":
            operation["operation_reference"] = "unsubscribe-operation:prefix-changed"
        else:
            operation["kind"] = "click_confirmation"

    def forbidden_callback(*_args, **_kwargs):
        raise AssertionError("changed terminal action must not reach the browser")

    fixture.operation.execute_effect = forbidden_callback
    recovered = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=recovery_audit.id,
        accepted_action=action,
    )

    assert recovered["status"] == "failed", recovered


@pytest.mark.parametrize("tamper", ("consumer", "other_task_audit"))
def test_two_step_terminal_snapshot_rejects_earlier_effect_audit_tamper(
    tmp_path: Path,
    tamper: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    _action, first_audit, second_consumer, second_audit, _terminal = (
        _complete_two_step_terminal(fixture)
    )
    replacement_run_id = second_consumer.id
    if tamper == "other_task_audit":
        other = fixture.task_store.ensure_reply_task(
            channel="dingtalk",
            conversation_id="other-conversation",
            conversation_title="Other",
            single_chat=True,
            trigger_message_id="other-message",
            trigger_create_time="2026-09-02T08:00:00+00:00",
            trigger_sender="other",
            trigger_text="other",
            execution_generation="other-generation",
        )
        other_consumer = fixture.task_store.claim_agent_run(
            other.id,
            other.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner="other-consumer-owner",
        ).run
        other_consumer = fixture.task_store.complete_agent_run(
            other_consumer.id,
            {"outcome": "proposal"},
            owner="other-consumer-owner",
        )
        other_audit = fixture.task_store.claim_agent_run(
            other.id,
            other.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=other_consumer.id,
            operation_id="other-audit-operation",
            owner="other-audit-owner",
        ).run
        other_audit = fixture.task_store.complete_agent_run(
            other_audit.id,
            {"outcome": "executed"},
            owner="other-audit-owner",
        )
        replacement_run_id = other_audit.id
    with sqlite3.connect(fixture.email_store.path) as db:
        db.execute(
            "update email_unsubscribe_effects set audit_agent_run_id=? "
            "where action_identity=? and audit_agent_run_id=?",
            (replacement_run_id, ACTION_IDENTITY, first_audit.id),
        )

    claim = fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert claim is not None
    with pytest.raises(EmailPersistenceCorruption):
        fixture.email_store.get_email_unsubscribe_terminal_snapshot(
            ACTION_IDENTITY,
            claim["effect_digest"],
            task_id=fixture.task.id,
            task_execution_generation=fixture.task.execution_generation,
        )

    assert second_audit.status == "completed"


def test_two_step_terminal_snapshot_rejects_orphan_effect_branch(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    _action, first_audit, second_consumer, _second_audit, _terminal = (
        _complete_two_step_terminal(fixture)
    )
    with sqlite3.connect(fixture.email_store.path) as db:
        db.row_factory = sqlite3.Row
        root = db.execute(
            "select * from email_unsubscribe_effects "
            "where action_identity=? and audit_agent_run_id=?",
            (ACTION_IDENTITY, first_audit.id),
        ).fetchone()
        assert root is not None
        root_operations = json.loads(root["operations_json"])
        branch_operations = root_operations + [
            {
                "operation_reference": "unsubscribe-operation:orphan-branch",
                "kind": "click_confirmation",
                "target_reference": "unsubscribe-control:orphan-branch",
            }
        ]
        branch_digest = email_unsubscribe_effect_digest(
            action_identity=ACTION_IDENTITY,
            action_plan_id=PLAN.action_plan_id,
            action_plan_version=PLAN.action_plan_version,
            classification_id=CLASSIFICATION_ID,
            account_id=ACCOUNT_ID,
            stable_message_identity=MESSAGE_IDENTITY,
            thread_identity=THREAD_IDENTITY,
            entry_reference=ENTRY.reference,
            operations=branch_operations,
            previous_effect_digest=root["effect_digest"],
            network_policy_reference=root["network_policy_reference"],
            network_policy_origin_references=json.loads(
                root["network_policy_origins_json"]
            ),
        )
        db.execute(
            "insert into email_unsubscribe_effects ("
            "action_identity, effect_digest, previous_effect_digest, "
            "operations_json, network_policy_reference, "
            "network_policy_origins_json, audit_agent_run_id, created_at"
            ") values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ACTION_IDENTITY,
                branch_digest,
                root["effect_digest"],
                json.dumps(branch_operations, sort_keys=True),
                root["network_policy_reference"],
                root["network_policy_origins_json"],
                second_consumer.id,
                "2026-09-02T08:00:03+00:00",
            ),
        )

    claim = fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert claim is not None
    with pytest.raises(EmailPersistenceCorruption, match="effect lineage"):
        fixture.email_store.get_email_unsubscribe_terminal_snapshot(
            ACTION_IDENTITY,
            claim["effect_digest"],
            task_id=fixture.task.id,
            task_execution_generation=fixture.task.execution_generation,
        )


def test_two_step_terminal_snapshot_binds_claim_owner_to_head_audit_run(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    _action, first_audit, _second_consumer, _second_audit, _terminal = (
        _complete_two_step_terminal(fixture)
    )
    with sqlite3.connect(fixture.email_store.path) as db:
        db.execute(
            "update email_unsubscribe_claims "
            "set owner_id=?, owner_generation=?, lease_token=? "
            "where action_identity=?",
            (
                f"email-unsubscribe-audit:{first_audit.id}",
                999,
                "unsubscribe-audit-lease:tampered",
                ACTION_IDENTITY,
            ),
        )

    claim = fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert claim is not None
    with pytest.raises(EmailPersistenceCorruption, match="owner.*Audit"):
        fixture.email_store.get_email_unsubscribe_terminal_snapshot(
            ACTION_IDENTITY,
            claim["effect_digest"],
            task_id=fixture.task.id,
            task_execution_generation=fixture.task.execution_generation,
        )


def test_two_step_terminal_snapshot_accepts_complete_audited_lineage(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    _action, _first_audit, _second_consumer, _second_audit, terminal = (
        _complete_two_step_terminal(fixture)
    )
    claim = fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert claim is not None

    snapshot = fixture.email_store.get_email_unsubscribe_terminal_snapshot(
        ACTION_IDENTITY,
        claim["effect_digest"],
        task_id=fixture.task.id,
        task_execution_generation=fixture.task.execution_generation,
    )

    assert snapshot is not None
    assert snapshot["receipt"]["receipt_id"] == terminal["receipt_id"]


def test_first_audit_call_reconciles_executor_persisted_long_terminal_result(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    full_text = "Unsubscribed " * 2_000
    expected_text, expected_full_digest = normalize_unsubscribe_result_text(full_text)
    browser = _TerminalBrowser(full_text)

    result = _execute_with_durable_executor(fixture, browser)

    assert result["status"] == "done", result
    assert result["receipt_id"] == "provider-receipt:executor-audit-41"
    assert result["result_text"] == expected_text
    assert result["observation_digest"] == expected_full_digest
    assert browser.calls == ["receipt", "unsubscribe-operation:open-entry"]
    receipt = fixture.email_store.get_email_unsubscribe_receipt(ACTION_IDENTITY)
    assert receipt is not None
    assert receipt["result_text"] == expected_text
    assert receipt["observation_digest"] == expected_full_digest
    assert receipt["result_text_digest"] != expected_full_digest
    assert receipt["result_text_truncated"] is True


def test_terminal_receipt_recovers_in_new_audit_run_without_callback(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    first_result = _execute_with_durable_executor(
        fixture,
        _TerminalBrowser("Unsubscribed"),
    )
    assert first_result["status"] == "done", first_result
    fixture.task_store.fail_agent_run(
        fixture.audit_run.id,
        {"code": "restart", "retryable": True},
        owner="audit-owner",
    )
    recovery_audit = fixture.task_store.claim_agent_run(
        fixture.task.id,
        fixture.task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=fixture.consumer_run.id,
        operation_id="audit-operation-recovery",
        owner="audit-recovery-owner",
    ).run

    def forbidden_callback(*_args, **_kwargs):
        raise AssertionError("terminal recovery must not replay the browser callback")

    fixture.operation.execute_effect = forbidden_callback
    recovered = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=recovery_audit.id,
        accepted_action=_accepted_action(),
    )

    assert recovered["status"] == "done", recovered
    assert recovered["receipt_id"] == first_result["receipt_id"]


def test_mapping_result_without_durable_receipt_uses_one_store_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_fixture(tmp_path)
    original_persist = fixture.email_store.persist_email_unsubscribe_terminal
    persist_calls: list[dict[str, object]] = []

    def counted_persist(**kwargs):
        persist_calls.append(kwargs)
        return original_persist(**kwargs)

    monkeypatch.setattr(
        fixture.email_store,
        "persist_email_unsubscribe_terminal",
        counted_persist,
    )

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert result["status"] == "done", result
    assert len(persist_calls) == 1
    assert (
        fixture.email_store.get_email_unsubscribe_receipt(ACTION_IDENTITY) is not None
    )


@pytest.mark.parametrize(
    "mismatch",
    (
        "receipt_id",
        "outcome",
        "observation_digest",
        "final_step",
        "partial_final_step",
    ),
)
def test_executor_persisted_receipt_rejects_mismatched_public_result(
    tmp_path: Path,
    mismatch: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    browser = _TerminalBrowser("Unsubscribed")

    def mismatch_result(result, _owner):
        public = _public_terminal_mapping(result)
        if mismatch in {"final_step", "partial_final_step"}:
            final_step = public["final_step"]
            assert isinstance(final_step, dict)
            if mismatch == "final_step":
                final_step["reference"] = "provider-receipt:mismatched-step"
            else:
                final_step.pop("reference")
        else:
            public[mismatch] = {
                "receipt_id": "provider-receipt:mismatched",
                "outcome": "already_unsubscribed",
                "observation_digest": "f" * 64,
            }[mismatch]
        return public

    result = _execute_with_durable_executor(
        fixture,
        browser,
        after_persist=mismatch_result,
    )

    assert result["status"] == "failed", result


@pytest.mark.parametrize("race", ("other_audit", "stale_owner"))
def test_executor_persisted_receipt_rejects_changed_audit_or_owner_binding(
    tmp_path: Path,
    race: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    browser = _TerminalBrowser("Unsubscribed")

    def change_binding(result, _owner):
        with sqlite3.connect(fixture.email_store.path) as db:
            if race == "other_audit":
                db.execute(
                    "update email_unsubscribe_claims set audit_agent_run_id=? "
                    "where action_identity=?",
                    (fixture.consumer_run.id, ACTION_IDENTITY),
                )
            else:
                db.execute(
                    "update email_unsubscribe_claims set lease_token=? "
                    "where action_identity=?",
                    ("unsubscribe-audit-lease:stale", ACTION_IDENTITY),
                )
        return result

    result = _execute_with_durable_executor(
        fixture,
        browser,
        after_persist=change_binding,
    )

    assert result["status"] == "failed", result


def test_executor_persisted_receipt_rejects_step_effect_digest_tamper(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)

    def tamper_step(result, _owner):
        with sqlite3.connect(fixture.email_store.path) as db:
            db.execute(
                "update email_unsubscribe_steps set effect_digest=? "
                "where action_identity=?",
                ("f" * 64, ACTION_IDENTITY),
            )
        return result

    result = _execute_with_durable_executor(
        fixture,
        _TerminalBrowser("Unsubscribed"),
        after_persist=tamper_step,
    )

    assert result["status"] == "failed", result


def test_current_running_audit_executes_one_operation_and_persists_exact_run_id(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert result["status"] == "done", result
    assert len(fixture.resolved) == 1
    assert len(fixture.executed) == 1
    claim = fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert claim is not None
    assert claim["audit_agent_run_id"] == fixture.audit_run.id
    with sqlite3.connect(fixture.email_store.path) as db:
        effect_run_id = db.execute(
            "select audit_agent_run_id from email_unsubscribe_effects "
            "where action_identity=?",
            (ACTION_IDENTITY,),
        ).fetchone()[0]
    assert effect_run_id == fixture.audit_run.id


def test_dedicated_executor_can_reenter_the_exact_audited_claim_owner(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    terminal_callback = fixture.operation.execute_effect
    reentrant_claims = []

    def execute_effect(effect, entries, *, owner):
        reentrant_claims.append(
            fixture.email_store.claim_email_unsubscribe_write(
                **audit_store_arguments(effect),
                owner=owner,
            )
        )
        with pytest.raises(
            EmailUnsubscribeClaimConflict,
            match="requires its exact owner fence",
        ):
            fixture.email_store.claim_email_unsubscribe_write(
                **audit_store_arguments(effect),
                owner={
                    "owner_id": "different-owner",
                    "generation": owner["generation"],
                    "lease_token": owner["lease_token"],
                },
            )
        return terminal_callback(
            effect,
            entries,
            owner=owner,
        )

    fixture.operation.execute_effect = execute_effect

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert result["status"] == "done", result
    assert len(reentrant_claims) == 1
    assert reentrant_claims[0] is not None
    assert reentrant_claims[0]["acquired"] is False
    assert reentrant_claims[0]["audit_agent_run_id"] == fixture.audit_run.id


def test_audited_continuation_executes_only_appended_operation_from_blank_browser(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    first_audit, _second_consumer, second_audit = _start_second_audit_round(fixture)
    initial_effect = fixture.email_store.get_email_unsubscribe_effect(
        ACTION_IDENTITY,
        fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)[
            "effect_digest"
        ],
    )
    assert initial_effect is not None
    extension_action = _two_step_action()

    class BlankBrowser:
        def __init__(self):
            self.calls = []

        def find_confirmation_receipt(self, _effect):
            self.calls.append("receipt")
            return None

        def inspect_current_state(self, _effect, _private_url):
            self.calls.append("inspect")
            return UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="blank-browser",
                next_operation_reference="unsubscribe-operation:open-entry",
            )

        def execute_operation(self, effect, _private_url, operation):
            self.calls.append(operation.operation_reference)
            assert operation.operation_reference == "unsubscribe-operation:submit-form"
            return UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-done",
                receipt=UnsubscribeTerminalReceipt(
                    receipt_id="provider-receipt:continued-audit",
                    evidence="terminal-page",
                    entry_reference=effect.entry_reference,
                    effect_digest=effect.effect_digest,
                ),
                visible_text="Unsubscribed",
            )

    browser = BlankBrowser()

    def execute_effect(
        effect,
        entries,
        *,
        owner,
        executed_prefix_length,
    ):
        return UnsubscribeExecutor(
            fixture.email_store,
            browser,
            owner=owner,
        ).execute(
            effect,
            entries,
            executed_prefix_length=executed_prefix_length,
        )

    fixture.operation.execute_effect = execute_effect

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=second_audit.id,
        accepted_action=extension_action,
    )

    assert result["status"] == "done", result
    assert result["outcome"] == UnsubscribeOutcome.DONE.value
    assert browser.calls == ["receipt", "unsubscribe-operation:submit-form"]
    with sqlite3.connect(fixture.email_store.path) as db:
        durable_steps = db.execute(
            "select sequence, effect_digest, operation from "
            "email_unsubscribe_steps where action_identity=? order by sequence",
            (ACTION_IDENTITY,),
        ).fetchall()
    assert [(step[0], step[2]) for step in durable_steps] == [
        (1, "open_entry"),
        (2, "submit_form"),
    ]
    assert durable_steps[0][1] == initial_effect["effect_digest"]
    assert durable_steps[1][1] != initial_effect["effect_digest"]
    claim = fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert claim is not None
    assert durable_steps[1][1] == claim["effect_digest"]
    assert first_audit.status == "completed"


@pytest.mark.parametrize("tamper", ("prefix", "effect_audit_run"))
def test_preclaimed_audit_prefix_or_effect_tamper_fails_before_browser(
    tmp_path: Path,
    tamper: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    effect = accepted_email_unsubscribe_effect(
        fixture.task,
        ProposedAction.model_validate(_accepted_action()),
    )
    owner = {
        "owner_id": f"email-unsubscribe-audit:{fixture.audit_run.id}",
        "generation": max(1, fixture.audit_run.turn_attempt + 1),
        "lease_token": "unsubscribe-audit-lease:test-prefix",
    }
    claim = fixture.email_store.claim_email_unsubscribe_write(
        **audit_store_arguments(effect),
        owner=owner,
        task_id=fixture.task.id,
        task_execution_generation=fixture.task.execution_generation,
        task_lifecycle_version="email_unsubscribe_audited_v2",
        task_action_type=EmailAction.UNSUBSCRIBE.value,
        audit_agent_run_id=fixture.audit_run.id,
    )
    assert claim is not None and claim["executed_prefix_length"] == 0
    prefix = 1 if tamper == "prefix" else 0
    if tamper == "effect_audit_run":
        with sqlite3.connect(fixture.email_store.path) as db:
            db.execute(
                "update email_unsubscribe_effects set audit_agent_run_id=? "
                "where action_identity=? and effect_digest=?",
                (fixture.audit_run.id + 999, ACTION_IDENTITY, effect.effect_digest),
            )

    class ForbiddenBrowser:
        def __getattr__(self, name):
            raise AssertionError(f"browser reached after durable tamper: {name}")

    result = UnsubscribeExecutor(
        fixture.email_store,
        ForbiddenBrowser(),
        owner=owner,
    ).execute(
        effect,
        (ENTRY,),
        executed_prefix_length=prefix,
    )

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.error_code == "email_unsubscribe_authorization_stale"


def test_accepted_entry_can_be_one_of_multiple_projected_entries(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    payload = _payload()
    entries = payload["unsubscribe_entries"]
    assert isinstance(entries, list)
    entries.append(
        {
            "index": 1,
            "source": "body_html_https",
            "digest": SECONDARY_ENTRY.reference.removeprefix("unsubscribe-entry:"),
            "reference": SECONDARY_ENTRY.reference,
        }
    )
    with sqlite3.connect(fixture.email_store.path) as db:
        db.execute(
            "update reply_tasks set trigger_message_json=? where id=?",
            (json.dumps(payload, sort_keys=True), fixture.task.id),
        )

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert result["status"] == "done", result
    assert len(fixture.executed) == 1


@pytest.mark.parametrize(
    "invalid_parent_result",
    (
        "missing",
        "malformed_json",
        "non_object",
        "non_proposal",
        "multiple_actions",
    ),
)
def test_audit_fails_closed_when_parent_consumer_has_no_unique_proposal_action(
    tmp_path: Path,
    invalid_parent_result: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    if invalid_parent_result == "missing":
        final_result_json = ""
    elif invalid_parent_result == "malformed_json":
        final_result_json = "{"
    elif invalid_parent_result == "non_object":
        final_result_json = "[]"
    elif invalid_parent_result == "non_proposal":
        final_result_json = json.dumps(
            {
                **_consumer_proposal_result(_accepted_action()),
                "outcome": "no_action",
                "proposal": None,
            },
            sort_keys=True,
        )
    else:
        final_result_json = json.dumps(
            _consumer_proposal_result(_accepted_action(), _accepted_action()),
            sort_keys=True,
        )
    with sqlite3.connect(fixture.email_store.path) as db:
        db.execute(
            "update agent_runs set final_result_json=? where id=?",
            (final_result_json, fixture.consumer_run.id),
        )

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert result["status"] == "failed", result
    assert fixture.resolved == []
    assert fixture.executed == []
    assert fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY) is None


@pytest.mark.parametrize(
    "mutation",
    (
        "description",
        "capability",
        "operation",
        "operation_reference",
        "target_and_payload",
        "identity",
    ),
)
def test_audit_accepted_action_is_exactly_bound_to_parent_consumer_proposal(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    action = _accepted_action()
    if mutation == "description":
        action["description"] = "A different accepted description"
    elif mutation == "capability":
        action["capability"] = "email"
    elif mutation == "operation":
        action["operation"] = "reply"
    elif mutation == "operation_reference":
        operations = action["payload"]["operations"]
        assert isinstance(operations, list)
        operations[0]["operation_reference"] = "unsubscribe-operation:replacement"
    elif mutation == "target_and_payload":
        payload = _payload()
        entries = payload["unsubscribe_entries"]
        assert isinstance(entries, list)
        entries.append(
            {
                "source": "body_https",
                "reference": SECONDARY_ENTRY.reference,
                "priority": 20,
            }
        )
        with sqlite3.connect(fixture.email_store.path) as db:
            db.execute(
                "update reply_tasks set trigger_message_json=? where id=?",
                (json.dumps(payload, sort_keys=True), fixture.task.id),
            )
        target = action["target"]
        operations = action["payload"]["operations"]
        assert isinstance(target, dict)
        assert isinstance(operations, list)
        target["entry_reference"] = SECONDARY_ENTRY.reference
        operations[0]["target_reference"] = SECONDARY_ENTRY.reference
        fixture.operation.resolve_entries = lambda *_args, **_kwargs: (
            ENTRY,
            SECONDARY_ENTRY,
        )
    else:
        target = action["target"]
        assert isinstance(target, dict)
        target["account_id"] = "account-replacement"

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=action,
    )

    assert result["status"] == "failed", result
    assert fixture.resolved == []
    assert fixture.executed == []
    assert fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY) is None


def test_canonical_action_binding_accepts_mapping_key_order_differences(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    action = _accepted_action()
    target = action["target"]
    payload = action["payload"]
    assert isinstance(target, dict)
    assert isinstance(payload, dict)
    operations = payload["operations"]
    assert isinstance(operations, list)
    reordered = dict(reversed(tuple(action.items())))
    reordered["target"] = dict(reversed(tuple(target.items())))
    reordered["payload"] = {
        "operations": [dict(reversed(tuple(operations[0].items())))]
    }

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=reordered,
    )

    assert result["status"] == "done", result
    assert len(fixture.executed) == 1


def test_consumer_run_cannot_execute_unsubscribe(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.consumer_run.id,
        accepted_action=_accepted_action(),
    )

    assert _error_code(result) == "unsubscribe_audit_run_invalid"
    assert fixture.resolved == []
    assert fixture.executed == []


def test_stale_audit_run_cannot_execute_unsubscribe(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    fixture.task_store.fail_agent_run(
        fixture.audit_run.id,
        {"code": "stale", "retryable": False},
        owner="audit-owner",
    )

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert _error_code(result) == "unsubscribe_audit_run_invalid"
    assert fixture.executed == []


def test_audit_run_for_another_task_cannot_execute_unsubscribe(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    other = fixture.task_store.ensure_reply_task(
        channel="dingtalk",
        conversation_id="other-conversation",
        conversation_title="Other",
        single_chat=True,
        trigger_message_id="other-message",
        trigger_create_time="2026-09-02T08:00:00+00:00",
        trigger_sender="other",
        trigger_text="other",
        execution_generation="other-generation",
    )
    other_consumer = fixture.task_store.claim_agent_run(
        other.id,
        other.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="other-consumer",
    ).run
    other_consumer = fixture.task_store.complete_agent_run(
        other_consumer.id,
        {"outcome": "proposal"},
        owner="other-consumer",
    )
    other_audit = fixture.task_store.claim_agent_run(
        other.id,
        other.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=other_consumer.id,
        operation_id="other-audit-operation",
        owner="other-audit",
    ).run

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=other_audit.id,
        accepted_action=_accepted_action(),
    )

    assert _error_code(result) == "unsubscribe_audit_run_invalid"
    assert fixture.executed == []


def test_wrong_execution_generation_cannot_execute_unsubscribe(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)

    result = fixture.operation.execute(
        fixture.task.id,
        "wrong-generation",
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert _error_code(result) == "unsubscribe_audit_run_invalid"
    assert fixture.executed == []


def test_audit_parent_must_be_the_current_completed_consumer(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    newer_consumer = fixture.task_store.claim_agent_run(
        fixture.task.id,
        fixture.task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=None,
        operation_id="",
        owner="newer-consumer",
    ).run
    fixture.task_store.complete_agent_run(
        newer_consumer.id,
        {"outcome": "proposal"},
        owner="newer-consumer",
    )

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert _error_code(result) == "unsubscribe_audit_run_invalid"
    assert fixture.executed == []


@pytest.mark.parametrize(
    "mutation",
    (
        "task_action",
        "action_identity",
        "plan",
        "account",
        "message",
        "thread",
        "entry",
        "origin",
    ),
)
def test_identity_tampering_is_rejected_before_browser_execution(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    action = _accepted_action()
    if mutation in {"task_action", "plan"}:
        payload = _payload()
        if mutation == "task_action":
            payload["action_type"] = "auto_reply"
        else:
            payload["action_plan_id"] = "email-action-plan:tampered"
        with sqlite3.connect(fixture.email_store.path) as db:
            db.execute(
                "update reply_tasks set trigger_message_json=? where id=?",
                (json.dumps(payload, sort_keys=True), fixture.task.id),
            )
    else:
        target = action["target"]
        assert isinstance(target, dict)
        field = {
            "action_identity": "action_identity",
            "account": "account_id",
            "message": "stable_message_identity",
            "thread": "thread_identity",
            "entry": "entry_reference",
            "origin": "network_policy_origin_references",
        }[mutation]
        target[field] = ["origin:tampered"] if mutation == "origin" else "tampered"

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=action,
    )

    assert result["status"] == "failed"
    assert fixture.executed == []
    assert fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY) is None


def test_current_provider_entry_is_re_resolved_before_browser_execution(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    fixture.operation.resolve_entries = lambda *_args, **_kwargs: ()

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert _error_code(result) == "unsubscribe_entry_changed"
    assert fixture.executed == []
    assert fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY) is None


def test_current_provider_policy_drift_fails_before_claim_or_browser(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    changed_origin_entry = extract_unsubscribe_entries(
        list_unsubscribe="<https://changed.example.net/unsubscribe>"
    )[0]
    fixture.operation.resolve_entries = lambda *_args, **_kwargs: (
        ENTRY,
        changed_origin_entry,
    )

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
    )

    assert _error_code(result) == "unsubscribe_network_policy_changed"
    assert fixture.executed == []
    assert fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY) is None


def test_unbound_historical_claim_and_effect_rows_keep_null_audit_run_id(
    tmp_path: Path,
) -> None:
    store = _seed_email_state(tmp_path / "historical.sqlite3")
    effect = {
        "action_identity": ACTION_IDENTITY,
        "effect_digest": "",
        "action_plan_id": PLAN.action_plan_id,
        "action_plan_version": PLAN.action_plan_version,
        "classification_id": CLASSIFICATION_ID,
        "account_id": ACCOUNT_ID,
        "stable_message_identity": MESSAGE_IDENTITY,
        "thread_identity": THREAD_IDENTITY,
        "entry_reference": ENTRY.reference,
        "operations": [
            {
                "operation_reference": "unsubscribe-operation:open-entry",
                "kind": "open_entry",
                "target_reference": ENTRY.reference,
            }
        ],
        "network_policy_reference": "network-policy:test",
        "network_policy_origin_references": ("origin:test",),
    }
    from app.email_store import email_unsubscribe_effect_digest

    effect["effect_digest"] = email_unsubscribe_effect_digest(
        **{key: value for key, value in effect.items() if key != "effect_digest"}
    )
    claim = store.claim_email_unsubscribe_write(
        **effect,
        owner={
            "owner_id": "historical-owner",
            "generation": 1,
            "lease_token": "historical-lease",
        },
    )

    assert claim is not None and claim["audit_agent_run_id"] is None
    with sqlite3.connect(store.path) as db:
        effect_run_id = db.execute(
            "select audit_agent_run_id from email_unsubscribe_effects "
            "where action_identity=?",
            (ACTION_IDENTITY,),
        ).fetchone()[0]
    assert effect_run_id is None
