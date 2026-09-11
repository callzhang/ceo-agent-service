from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib
import json
import sqlite3

import pytest

from app.agent_contracts import AuditAgentResult
from app.email_store import EmailPersistenceCorruption, email_action_identity
from app.email_task_adapter import email_conversation_id
from app.email_unsubscribe import EmailUnsubscribeEffect, UnsubscribeOperation
from app.email_unsubscribe import UnsubscribeDiscoveredControl
from app.email_task_adapter import EmailAgentTaskMetadataError
from app.store import AgentRole, AgentRun, ReplyTask


ACCOUNT_ID = "account-primary"
MESSAGE_IDENTITY = "account-primary:message-id:<mail-41@example.com>"
THREAD_IDENTITY = "thread-41"
ACTION_IDENTITY = email_action_identity(
    account_id=ACCOUNT_ID,
    stable_message_identity=MESSAGE_IDENTITY,
    action_type="unsubscribe",
    action_plan_version=1,
)
ENTRY_REFERENCE = "unsubscribe-entry:" + sha256(b"entry").hexdigest()
OPERATION_REFERENCE = "unsubscribe-operation:" + sha256(b"open-entry").hexdigest()
CONTROL_REFERENCE = "unsubscribe-control:" + sha256(b"confirm").hexdigest()
EFFECT_DIGEST = EmailUnsubscribeEffect(
    action_identity=ACTION_IDENTITY,
    action_plan_id="email-plan:subscription:1",
    action_plan_version=1,
    classification_id=41,
    account_id=ACCOUNT_ID,
    stable_message_identity=MESSAGE_IDENTITY,
    thread_identity=THREAD_IDENTITY,
    entry_reference=ENTRY_REFERENCE,
    operations=(
        UnsubscribeOperation.from_mapping(
            {
                "operation_reference": OPERATION_REFERENCE,
                "kind": "open_entry",
                "target_reference": ENTRY_REFERENCE,
            }
        ),
    ),
).effect_digest


def _module():
    return importlib.import_module("app.email_unsubscribe_continuation")


def _payload() -> dict[str, object]:
    return {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "account_id": ACCOUNT_ID,
        "stable_message_identity": MESSAGE_IDENTITY,
        "thread_identity": THREAD_IDENTITY,
        "action_identity": ACTION_IDENTITY,
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
                "digest": ENTRY_REFERENCE.removeprefix("unsubscribe-entry:"),
                "reference": ENTRY_REFERENCE,
            }
        ],
        "unsubscribe_authentication": None,
    }


def _one_click_payload(
    *,
    source: str = "header_one_click_https",
    authentication: object = True,
) -> dict[str, object]:
    payload = _payload()
    payload["unsubscribe_entries"] = [
        {
            "index": 0,
            "source": source,
            "digest": ENTRY_REFERENCE.removeprefix("unsubscribe-entry:"),
            "reference": ENTRY_REFERENCE,
        }
    ]
    if authentication is True:
        payload["unsubscribe_authentication"] = {
            "evidence_reference": "dkim-evidence:mail-41",
            "one_click_verified": True,
        }
    elif authentication is False:
        payload["unsubscribe_authentication"] = {
            "evidence_reference": "dkim-evidence:mail-41",
            "one_click_verified": False,
        }
    else:
        payload["unsubscribe_authentication"] = authentication
    return payload


def _mailto_payload() -> dict[str, object]:
    payload = _payload()
    payload["unsubscribe_entries"] = [
        {
            "source": "header_mailto",
            "reference": ENTRY_REFERENCE,
            "priority": 20,
        }
    ]
    return payload


def _task(
    *, payload: dict[str, object] | None = None, channel: str = "email"
) -> ReplyTask:
    value = payload or _payload()
    return ReplyTask(
        id=7,
        channel=channel,
        conversation_id=email_conversation_id(ACCOUNT_ID, THREAD_IDENTITY),
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=ACTION_IDENTITY,
        trigger_create_time="2026-09-02T08:00:00+00:00",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=json.dumps(value, sort_keys=True),
        execution_generation="generation-1",
        status="running",
        attempts=1,
        created_at="2026-09-02T08:00:00+00:00",
        updated_at="2026-09-02T08:00:00+00:00",
    )


def _audit_run(**updates: object) -> AgentRun:
    values: dict[str, object] = {
        "id": 23,
        "reply_task_id": 7,
        "execution_generation": "generation-1",
        "role": AgentRole.AUDIT,
        "proposal_revision": 0,
        "turn_attempt": 0,
        "parent_agent_run_id": 22,
        "operation_id": "agent-task:7:generation-1:proposal:0",
        "status": "completed",
        "final_result_json": "{}",
        "created_at": "2026-09-02T08:00:00+00:00",
        "updated_at": "2026-09-02T08:00:01+00:00",
    }
    values.update(updates)
    return AgentRun.model_validate(values)


def _audit_result(outcome: str = "executed") -> AuditAgentResult:
    external_result = None
    error_code = ""
    if outcome == "executed":
        external_result = {
            "operation_id": "agent-task:7:generation-1:proposal:0",
            "live_result_reference": {"id": "opaque-result"},
        }
    elif outcome == "dry_run":
        error_code = "dry_run_execution_suppressed"
    return AuditAgentResult.model_validate(
        {
            "outcome": outcome,
            "summary": outcome,
            "proposal_revision": 0,
            "feedback": None,
            "external_result": external_result,
            "error": {"code": error_code, "retryable": False},
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
        }
    )


def _claim(*, audit_agent_run_id: int = 23) -> dict[str, object]:
    return {
        "action_identity": ACTION_IDENTITY,
        "effect_digest": EFFECT_DIGEST,
        "action_plan_id": "email-plan:subscription:1",
        "action_plan_version": 1,
        "classification_id": 41,
        "account_id": ACCOUNT_ID,
        "stable_message_identity": MESSAGE_IDENTITY,
        "thread_identity": THREAD_IDENTITY,
        "entry_reference": ENTRY_REFERENCE,
        "operations": _operations(),
        "status": "awaiting_audit",
        "audit_agent_run_id": audit_agent_run_id,
    }


def _operations() -> list[dict[str, str]]:
    return [
        {
            "operation_reference": OPERATION_REFERENCE,
            "kind": "open_entry",
            "target_reference": ENTRY_REFERENCE,
        }
    ]


def _continuation() -> dict[str, object]:
    return {
        "action_identity": ACTION_IDENTITY,
        "effect_digest": EFFECT_DIGEST,
        "previous_effect_digest": "",
        "operations": _operations(),
        "controls": [
            {
                "reference": CONTROL_REFERENCE,
                "kind": "button",
                "intent": "confirm",
            }
        ],
        "observation_reference": "unsubscribe-state:" + sha256(b"state").hexdigest(),
    }


def _effect() -> dict[str, object]:
    return {
        "action_identity": ACTION_IDENTITY,
        "effect_digest": EFFECT_DIGEST,
        "previous_effect_digest": "",
        "operations": _operations(),
        "audit_agent_run_id": 23,
    }


def _replace_recomputed_root_effect(
    store,
    operations: list[dict[str, str]],
) -> None:
    typed = EmailUnsubscribeEffect(
        action_identity=ACTION_IDENTITY,
        action_plan_id="email-plan:subscription:1",
        action_plan_version=1,
        classification_id=41,
        account_id=ACCOUNT_ID,
        stable_message_identity=MESSAGE_IDENTITY,
        thread_identity=THREAD_IDENTITY,
        entry_reference=ENTRY_REFERENCE,
        operations=tuple(
            UnsubscribeOperation.from_mapping(operation) for operation in operations
        ),
    )
    mappings = [dict(operation) for operation in typed.operation_mappings]
    store.effect.update(
        {
            "effect_digest": typed.effect_digest,
            "previous_effect_digest": "",
            "operations": deepcopy(mappings),
        }
    )
    store.claim.update(
        {
            "effect_digest": typed.effect_digest,
            "operations": deepcopy(mappings),
        }
    )
    store.continuation.update(
        {
            "effect_digest": typed.effect_digest,
            "previous_effect_digest": "",
            "operations": deepcopy(mappings),
        }
    )


class FakeEmailStore:
    def __init__(self) -> None:
        self.claim = _claim()
        self.continuation = _continuation()
        self.effect = _effect()

    def get_email_unsubscribe_state_snapshot(self, action_identity: str):
        assert action_identity == ACTION_IDENTITY
        return {
            "claim": deepcopy(self.claim),
            "continuation": deepcopy(self.continuation),
            "effects": (() if self.effect is None else (deepcopy(self.effect),)),
        }

    def get_email_unsubscribe_claim(self, action_identity: str):
        assert action_identity == ACTION_IDENTITY
        return deepcopy(self.claim)

    def get_email_unsubscribe_continuation(self, action_identity: str):
        assert action_identity == ACTION_IDENTITY
        return deepcopy(self.continuation)

    def get_email_unsubscribe_effect(
        self,
        action_identity: str,
        effect_digest: str,
    ):
        assert action_identity == ACTION_IDENTITY
        return deepcopy(self.effect)


def _terminal_store() -> FakeEmailStore:
    store = FakeEmailStore()
    store.claim.update({"status": "done", "phase": "terminal"})
    store.continuation = None
    return store


@pytest.mark.parametrize("terminal", (False, True))
def test_recomputed_single_effect_two_operation_lineage_is_invalid(terminal):
    module = _module()
    store = FakeEmailStore()
    _replace_recomputed_root_effect(
        store,
        [
            *_operations(),
            {
                "operation_reference": (
                    "unsubscribe-operation:" + sha256(b"second").hexdigest()
                ),
                "kind": "click_confirmation",
                "target_reference": CONTROL_REFERENCE,
            },
        ],
    )
    if terminal:
        store.claim.update({"status": "done", "phase": "terminal"})
        store.continuation = None

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


def test_recomputed_root_click_confirmation_is_invalid():
    module = _module()
    store = FakeEmailStore()
    _replace_recomputed_root_effect(
        store,
        [
            {
                "operation_reference": OPERATION_REFERENCE,
                "kind": "click_confirmation",
                "target_reference": CONTROL_REFERENCE,
            }
        ],
    )

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


def test_recomputed_root_target_must_equal_claim_entry_reference():
    module = _module()
    store = FakeEmailStore()
    _replace_recomputed_root_effect(
        store,
        [
            {
                "operation_reference": OPERATION_REFERENCE,
                "kind": "open_entry",
                "target_reference": CONTROL_REFERENCE,
            }
        ],
    )

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


def test_recomputed_root_requires_literal_empty_previous_effect_digest():
    module = _module()
    store = FakeEmailStore()
    store.effect["previous_effect_digest"] = None

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


@pytest.mark.parametrize("kind", ("open_entry", "post_one_click"))
def test_recomputed_legal_single_operation_root_remains_valid(kind):
    module = _module()
    store = FakeEmailStore()
    _replace_recomputed_root_effect(
        store,
        [
            {
                "operation_reference": OPERATION_REFERENCE,
                "kind": kind,
                "target_reference": ENTRY_REFERENCE,
            }
        ],
    )

    task = _task(payload=_one_click_payload()) if kind == "post_one_click" else _task()
    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        task,
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.CONTINUE


@pytest.mark.parametrize("terminal", (False, True))
@pytest.mark.parametrize(
    "payload",
    (
        _one_click_payload(
            source="header_https",
        ),
        _one_click_payload(authentication=None),
        _one_click_payload(authentication=False),
    ),
    ids=("ordinary-header", "missing-auth", "false-auth"),
)
def test_recomputed_post_one_click_root_requires_exact_authorization(
    payload,
    terminal,
):
    module = _module()
    store = FakeEmailStore()
    _replace_recomputed_root_effect(
        store,
        [
            {
                "operation_reference": OPERATION_REFERENCE,
                "kind": "post_one_click",
                "target_reference": ENTRY_REFERENCE,
            }
        ],
    )
    if terminal:
        store.claim.update({"status": "done", "phase": "terminal"})
        store.continuation = None

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(payload=deepcopy(payload)),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


@pytest.mark.parametrize("terminal", (False, True))
def test_recomputed_open_entry_root_rejects_mailto_source(terminal):
    module = _module()
    store = FakeEmailStore()
    _replace_recomputed_root_effect(
        store,
        [
            {
                "operation_reference": OPERATION_REFERENCE,
                "kind": "open_entry",
                "target_reference": ENTRY_REFERENCE,
            }
        ],
    )
    if terminal:
        store.claim.update({"status": "done", "phase": "terminal"})
        store.continuation = None

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(payload=_mailto_payload()),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_audit_id",
        "effect_wrong_audit_id",
        "wrong_action",
        "wrong_account",
        "wrong_message",
        "wrong_thread",
        "wrong_plan",
        "wrong_classification",
        "wrong_operations",
        "wrong_effect_digest",
        "wrong_entry",
        "wrong_phase",
        "unexpected_continuation",
    ),
)
def test_terminal_done_claim_requires_full_audit_and_effect_identity(mutation):
    module = _module()
    store = _terminal_store()
    if mutation == "wrong_audit_id":
        store.claim["audit_agent_run_id"] = 999
    elif mutation == "effect_wrong_audit_id":
        store.effect["audit_agent_run_id"] = 999
    elif mutation == "wrong_action":
        store.claim["action_identity"] = "other-action"
    elif mutation == "wrong_account":
        store.claim["account_id"] = "other-account"
    elif mutation == "wrong_message":
        store.claim["stable_message_identity"] = "other-message"
    elif mutation == "wrong_thread":
        store.claim["thread_identity"] = "other-thread"
    elif mutation == "wrong_plan":
        store.claim["action_plan_id"] = "other-plan"
    elif mutation == "wrong_classification":
        store.claim["classification_id"] = 999
    elif mutation == "wrong_operations":
        store.claim["operations"] = [
            {
                "operation_reference": OPERATION_REFERENCE,
                "kind": "click_confirmation",
                "target_reference": CONTROL_REFERENCE,
            }
        ]
        store.effect["operations"] = deepcopy(store.claim["operations"])
    elif mutation == "wrong_effect_digest":
        wrong_digest = sha256(b"wrong-effect").hexdigest()
        store.claim["effect_digest"] = wrong_digest
        store.effect["effect_digest"] = wrong_digest
    elif mutation == "wrong_entry":
        store.claim["entry_reference"] = (
            "unsubscribe-entry:" + sha256(b"wrong-entry").hexdigest()
        )
    elif mutation == "wrong_phase":
        store.claim["phase"] = "navigating"
    elif mutation == "unexpected_continuation":
        store.continuation = _continuation()

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


def test_driver_returns_explicit_continue_terminal_invalid_and_unavailable_states():
    module = _module()
    store = FakeEmailStore()
    driver = module.EmailUnsubscribeContinuationDriver(store)

    continued = driver.continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )
    assert continued.state is module.DomainContinuationState.CONTINUE
    assert continued.required_receipt_id == (
        "email-unsubscribe-continuation:" + EFFECT_DIGEST
    )
    assert continued.required_receipt_binding.startswith("domain-continuation-receipt:")

    store.claim.update({"status": "done", "phase": "terminal"})
    store.continuation = None
    terminal = driver.continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )
    assert terminal.state is module.DomainContinuationState.TERMINAL

    store.claim = _claim()
    store.continuation = None
    invalid = driver.continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )
    assert invalid.state is module.DomainContinuationState.INVALID

    store.claim = None
    missing = driver.continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )
    assert missing.state is module.DomainContinuationState.INVALID

    class UnavailableStore(FakeEmailStore):
        def get_email_unsubscribe_state_snapshot(self, action_identity: str):
            raise sqlite3.OperationalError("database is busy")

    unavailable = module.EmailUnsubscribeContinuationDriver(
        UnavailableStore()
    ).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )
    assert unavailable.state is module.DomainContinuationState.UNAVAILABLE


@pytest.mark.parametrize(
    ("error", "expected_state"),
    (
        (EmailPersistenceCorruption("malformed decoded JSON"), "invalid"),
        (RuntimeError("unexpected programming defect"), "invalid"),
        (sqlite3.OperationalError("no such table: programming_error"), "invalid"),
        (sqlite3.OperationalError("database is locked"), "unavailable"),
        (sqlite3.OperationalError("disk I/O error"), "unavailable"),
    ),
)
def test_driver_distinguishes_corruption_programming_errors_and_transient_store_errors(
    error,
    expected_state,
):
    class FailingSnapshotStore(FakeEmailStore):
        def get_email_unsubscribe_state_snapshot(self, action_identity: str):
            raise error

    module = _module()

    decision = module.EmailUnsubscribeContinuationDriver(
        FailingSnapshotStore()
    ).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state.value == expected_state


@pytest.mark.parametrize(
    "controls",
    (
        [
            {
                "reference": CONTROL_REFERENCE,
                "kind": "button",
                "intent": "confirm",
                "private": "extra",
            }
        ],
        [
            {
                "reference": CONTROL_REFERENCE,
                "kind": "script",
                "intent": "confirm",
            }
        ],
        [
            {
                "reference": CONTROL_REFERENCE,
                "kind": "button",
                "intent": "execute",
            }
        ],
        [
            {
                "reference": CONTROL_REFERENCE,
                "kind": "button",
                "intent": "<button>confirm</button>",
            }
        ],
        [
            {
                "reference": CONTROL_REFERENCE,
                "kind": "button",
                "intent": "confirm",
            },
            {
                "reference": CONTROL_REFERENCE,
                "kind": "link",
                "intent": "unsubscribe",
            },
        ],
        [
            {
                "reference": CONTROL_REFERENCE,
                "kind": 7,
                "intent": "confirm",
            }
        ],
        [
            {
                "reference": CONTROL_REFERENCE,
                "kind": "button",
                "intent": 7,
            }
        ],
        [
            {
                "reference": 7,
                "kind": "button",
                "intent": "confirm",
            }
        ],
    ),
)
def test_malformed_controls_fail_closed_before_receipt_projection(controls):
    module = _module()
    store = FakeEmailStore()
    store.continuation["controls"] = controls

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.INVALID


def test_receipt_projection_revalidates_typed_control_values():
    from app.email_task_adapter import (
        email_unsubscribe_continuation_receipt,
        validated_email_unsubscribe_continuation,
    )

    typed = validated_email_unsubscribe_continuation(
        _payload(),
        _claim(),
        _continuation(),
    )
    poisoned = replace(
        typed,
        controls=(
            UnsubscribeDiscoveredControl(
                reference=CONTROL_REFERENCE,
                kind="script",
                intent="<button>confirm</button>",
            ),
        ),
    )

    with pytest.raises(EmailAgentTaskMetadataError):
        email_unsubscribe_continuation_receipt(poisoned)


@pytest.mark.parametrize(
    "kind",
    ("email_otp", "captcha_handoff", "credential_handoff"),
)
def test_driver_projects_typed_auth_continuation_without_secret(kind):
    module = _module()
    store = FakeEmailStore()
    store.continuation["controls"] = [
        {
            "reference": "auth-control:" + sha256(kind.encode()).hexdigest(),
            "kind": kind,
            "intent": "confirm",
        }
    ]

    decision = module.EmailUnsubscribeContinuationDriver(store).continuation_state(
        _task(),
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.CONTINUE
    assert decision.required_receipt_id == (
        "email-unsubscribe-continuation:" + EFFECT_DIGEST
    )
    assert "847201" not in decision.required_receipt_binding

    from app.email_task_adapter import (
        email_unsubscribe_continuation_receipt,
        validated_email_unsubscribe_continuation,
    )

    typed = validated_email_unsubscribe_continuation(
        _payload(),
        _claim(),
        store.continuation,
    )
    summary = json.loads(email_unsubscribe_continuation_receipt(typed).summary)
    assert summary["requires_human"] is (kind == "credential_handoff")
    assert "847201" not in json.dumps(summary, sort_keys=True)


def test_driver_accepts_only_current_executed_audit_with_matching_durable_continuation() -> (
    None
):
    module = _module()
    driver = module.EmailUnsubscribeContinuationDriver(FakeEmailStore())

    assert (
        driver.continuation_state(
            _task(),
            audit_run=_audit_run(),
            audit_result=_audit_result(),
        ).state
        is module.DomainContinuationState.CONTINUE
    )


def test_non_email_task_is_terminal_even_when_its_payload_is_not_email_json():
    module = _module()
    task = _task(channel="dingtalk").model_copy(
        update={"trigger_message_json": "not-json"}
    )

    decision = module.EmailUnsubscribeContinuationDriver(
        FakeEmailStore()
    ).continuation_state(
        task,
        audit_run=_audit_run(),
        audit_result=_audit_result(),
    )

    assert decision.state is module.DomainContinuationState.TERMINAL


@pytest.mark.parametrize(
    "mutation",
    (
        "non_executed",
        "non_email",
        "legacy_lifecycle",
        "audit_running",
        "audit_wrong_role",
        "audit_wrong_task",
        "audit_wrong_generation",
        "audit_revision_mismatch",
        "claim_missing",
        "claim_not_awaiting",
        "claim_wrong_audit",
        "continuation_missing",
        "continuation_wrong_effect",
        "continuation_private_control",
    ),
)
def test_driver_fails_closed_on_malformed_missing_or_mismatched_state(
    mutation: str,
) -> None:
    store = FakeEmailStore()
    task = _task()
    audit_run = _audit_run()
    audit_result = _audit_result()
    if mutation == "non_executed":
        audit_result = _audit_result("dry_run")
    elif mutation == "non_email":
        task = _task(channel="dingtalk")
    elif mutation == "legacy_lifecycle":
        payload = _payload()
        payload["lifecycle_version"] = "email_unsubscribe_consumer_direct_v1"
        task = _task(payload=payload)
    elif mutation == "audit_running":
        audit_run = _audit_run(status="running")
    elif mutation == "audit_wrong_role":
        audit_run = _audit_run(role=AgentRole.CONSUMER)
    elif mutation == "audit_wrong_task":
        audit_run = _audit_run(reply_task_id=999)
    elif mutation == "audit_wrong_generation":
        audit_run = _audit_run(execution_generation="other-generation")
    elif mutation == "audit_revision_mismatch":
        audit_result = audit_result.model_copy(update={"proposal_revision": 1})
    elif mutation == "claim_missing":
        store.claim = None
    elif mutation == "claim_not_awaiting":
        store.claim["status"] = "done"
    elif mutation == "claim_wrong_audit":
        store.claim["audit_agent_run_id"] = 999
    elif mutation == "continuation_missing":
        store.continuation = None
    elif mutation == "continuation_wrong_effect":
        store.continuation["effect_digest"] = sha256(b"other").hexdigest()
    elif mutation == "continuation_private_control":
        store.continuation["controls"][0]["reference"] = (
            "https://example.com/unsubscribe?token=private"
        )

    module = _module()
    driver = module.EmailUnsubscribeContinuationDriver(store)

    decision = driver.continuation_state(
        task,
        audit_run=audit_run,
        audit_result=audit_result,
    )
    expected = (
        module.DomainContinuationState.TERMINAL
        if mutation in {"non_email", "legacy_lifecycle"}
        else module.DomainContinuationState.INVALID
    )
    assert decision.state is expected


def test_durable_unsubscribe_operation_limit_is_independent_and_bounded():
    from app.email_unsubscribe import (
        MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS,
    )

    operations = tuple(
        UnsubscribeOperation.from_mapping(
            {
                "operation_reference": (
                    "unsubscribe-operation:"
                    + sha256(f"operation-{index}".encode()).hexdigest()
                ),
                "kind": "open_entry" if index == 0 else "click_confirmation",
                "target_reference": (
                    ENTRY_REFERENCE if index == 0 else CONTROL_REFERENCE
                ),
            }
        )
        for index in range(MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS + 1)
    )

    accepted = EmailUnsubscribeEffect(
        action_identity=ACTION_IDENTITY,
        action_plan_id="email-plan:subscription:1",
        action_plan_version=1,
        classification_id=41,
        account_id=ACCOUNT_ID,
        stable_message_identity=MESSAGE_IDENTITY,
        thread_identity=THREAD_IDENTITY,
        entry_reference=ENTRY_REFERENCE,
        operations=operations[:-1],
    )
    assert len(accepted.operations) == MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS

    with pytest.raises(ValueError, match="durable continuation operation limit"):
        EmailUnsubscribeEffect(
            action_identity=ACTION_IDENTITY,
            action_plan_id="email-plan:subscription:1",
            action_plan_version=1,
            classification_id=41,
            account_id=ACCOUNT_ID,
            stable_message_identity=MESSAGE_IDENTITY,
            thread_identity=THREAD_IDENTITY,
            entry_reference=ENTRY_REFERENCE,
            operations=operations,
        )


@pytest.mark.parametrize(
    ("claim_run", "effect_run", "audit_run_id", "expected"),
    [
        (23, 23, 23, True),
        (23, None, 23, True),
        (None, 23, 23, True),
        (23, 23, 24, False),
        (None, None, 23, False),
    ],
)
def test_execution_evidence_requires_claim_or_effect_bound_to_the_audit_run(
    claim_run, effect_run, audit_run_id, expected
):
    module = _module()
    store = FakeEmailStore()
    store.claim = None if claim_run is None else _claim(audit_agent_run_id=claim_run)
    store.effect = None if effect_run is None else {**_effect(), "audit_agent_run_id": effect_run}

    driver = module.EmailUnsubscribeContinuationDriver(store)

    assert driver.audit_run_has_execution_evidence(_task(), audit_run_id=audit_run_id) is expected


def test_execution_evidence_is_not_required_outside_the_audited_lifecycle():
    module = _module()
    driver = module.EmailUnsubscribeContinuationDriver(FakeEmailStore())
    task = _task().model_copy(update={"channel": "dingtalk"})

    assert driver.audit_run_has_execution_evidence(task, audit_run_id=99) is True
