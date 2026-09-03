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
    EmailStore,
    EmailUnsubscribeClaimConflict,
    email_action_identity,
)
from app.email_task_adapter import (
    accepted_email_unsubscribe_effect,
    email_conversation_id,
)
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    UnsubscribeExecutor,
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
    category=EmailCategory.SUBSCRIPTION,
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
                "source": "header_https",
                "reference": ENTRY.reference,
                "priority": 10,
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
            "expected_verification": "Read terminal provider evidence",
        }
    ).model_dump(mode="json")


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
                "category": EmailCategory.SUBSCRIPTION,
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"subscription": 1.0},
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
        {"outcome": "proposal"},
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
    initial_action = ProposedAction.model_validate(_accepted_action())
    initial_effect = accepted_email_unsubscribe_effect(
        fixture.task,
        initial_action,
    )
    prior_owner = {
        "owner_id": "prior-audit",
        "generation": 1,
        "lease_token": "prior-audit-lease",
    }
    prior_claim = fixture.email_store.claim_email_unsubscribe_write(
        **audit_store_arguments(initial_effect),
        owner=prior_owner,
    )
    assert prior_claim is not None and prior_claim["acquired"] is True
    fixture.email_store.persist_email_unsubscribe_continuation(
        **audit_store_arguments(initial_effect),
        controls=(
            {
                "reference": "control-form",
                "kind": "form",
                "intent": "unsubscribe",
            },
        ),
        observation_reference="state-form",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "action_required",
            "reference": "state-form",
        },
        owner=prior_owner,
    )
    extension_action = _accepted_action()
    operations = extension_action["payload"]["operations"]
    assert isinstance(operations, list)
    operations.append(
        {
            "operation_reference": "step-2",
            "kind": "submit_form",
            "target_reference": "control-form",
        }
    )

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
            assert operation.operation_reference == "step-2"
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
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=extension_action,
    )

    assert result["status"] == "done", result
    assert result["outcome"] == UnsubscribeOutcome.DONE.value
    assert browser.calls == ["receipt", "step-2"]
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
    assert durable_steps[0][1] == initial_effect.effect_digest
    assert durable_steps[1][1] != initial_effect.effect_digest
    claim = fixture.email_store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert claim is not None
    assert durable_steps[1][1] == claim["effect_digest"]


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

    result = fixture.operation.execute(
        fixture.task.id,
        fixture.task.execution_generation,
        audit_agent_run_id=fixture.audit_run.id,
        accepted_action=_accepted_action(),
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
