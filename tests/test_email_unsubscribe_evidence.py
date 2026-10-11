"""Pure unsubscribe evidence regression tests without the retired Agent driver.

Historical redacted metadata remains readable; these tests never authorize or
replay an operation and do not depend on an Audit run or orchestration turn.
"""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from app.email_store import email_action_identity
from app.email_task_adapter import EmailAgentTaskMetadataError
from app.email_task_adapter import (
    email_unsubscribe_continuation_receipt,
    validate_unsubscribe_entry_operation_semantics,
    validated_email_unsubscribe_continuation,
)
from app.email_unsubscribe import (
    EmailUnsubscribeEffect, UnsubscribeDiscoveredControl, UnsubscribeOperation,
)


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


@pytest.mark.parametrize("kind", ("email_otp", "captcha_handoff", "credential_handoff"))
def test_typed_authentication_evidence_keeps_opaque_receipt_identity(kind):
    continuation = _continuation()
    continuation["controls"] = [{
        "reference": "auth-control:" + sha256(kind.encode()).hexdigest(),
        "kind": kind, "intent": "confirm",
    }]
    typed = validated_email_unsubscribe_continuation(_payload(), _claim(), continuation)
    receipt = email_unsubscribe_continuation_receipt(typed)
    summary = json.loads(receipt.summary)
    assert receipt.receipt_id == "email-unsubscribe-continuation:" + EFFECT_DIGEST
    assert receipt.completed is False
    assert summary["previous_effect_digest"] == EFFECT_DIGEST
    assert summary["accepted_operations"] == _operations()
    assert summary["requires_human"] is (kind == "credential_handoff")
    assert "847201" not in receipt.summary


def test_continuation_rejects_a_recomputed_but_unbound_effect_digest():
    claim, continuation = _claim(), _continuation()
    claim["effect_digest"] = continuation["effect_digest"] = sha256(b"other-effect").hexdigest()
    with pytest.raises(EmailAgentTaskMetadataError):
        validated_email_unsubscribe_continuation(_payload(), claim, continuation)


@pytest.mark.parametrize("field", (
    "action_identity", "action_plan_id", "action_plan_version", "classification_id",
    "account_id", "stable_message_identity", "thread_identity",
))
def test_continuation_receipt_identity_requires_the_original_message_and_plan(field):
    claim = _claim()
    claim[field] = 2 if type(claim[field]) is int else "another-identity"
    with pytest.raises(EmailAgentTaskMetadataError):
        validated_email_unsubscribe_continuation(_payload(), claim, _continuation())


def test_effect_digest_is_canonical_across_operation_mapping_order():
    original = _operations()[0]
    reordered = dict(reversed(list(original.items())))
    values = dict(
        action_identity=ACTION_IDENTITY, action_plan_id="email-plan:subscription:1",
        action_plan_version=1, classification_id=41, account_id=ACCOUNT_ID,
        stable_message_identity=MESSAGE_IDENTITY, thread_identity=THREAD_IDENTITY,
        entry_reference=ENTRY_REFERENCE,
    )
    first = EmailUnsubscribeEffect(**values, operations=(UnsubscribeOperation.from_mapping(original),))
    second = EmailUnsubscribeEffect(**values, operations=(UnsubscribeOperation.from_mapping(reordered),))
    assert first.effect_digest == second.effect_digest == EFFECT_DIGEST


@pytest.mark.parametrize("kind", ("open_entry", "post_one_click"))
def test_root_operation_keeps_its_exact_original_entry_semantics(kind):
    payload = _payload()
    if kind == "post_one_click":
        payload["unsubscribe_entries"][0]["source"] = "header_one_click_https"
        payload["unsubscribe_authentication"] = {
            "evidence_reference": "dkim-evidence:mail-41", "one_click_verified": True,
        }
    operation = UnsubscribeOperation.from_mapping({**_operations()[0], "kind": kind})
    validate_unsubscribe_entry_operation_semantics(payload, entry_reference=ENTRY_REFERENCE, operation=operation)


@pytest.mark.parametrize("mutation", ("ordinary_header", "missing_auth", "false_auth", "wrong_entry", "mailto_source", "later_control"))
def test_root_entry_semantics_rejects_unverified_or_different_effects(mutation):
    payload = _payload()
    operation = {**_operations()[0], "kind": "post_one_click"}
    payload["unsubscribe_entries"][0]["source"] = "header_one_click_https"
    payload["unsubscribe_authentication"] = {
        "evidence_reference": "dkim-evidence:mail-41", "one_click_verified": True,
    }
    if mutation == "ordinary_header":
        payload["unsubscribe_entries"][0]["source"] = "header_https"
    elif mutation == "missing_auth":
        payload["unsubscribe_authentication"] = None
    elif mutation == "false_auth":
        payload["unsubscribe_authentication"]["one_click_verified"] = False
    elif mutation == "wrong_entry":
        operation["target_reference"] = CONTROL_REFERENCE
    elif mutation == "mailto_source":
        payload["unsubscribe_entries"][0]["source"] = "header_mailto"
        operation["kind"] = "open_entry"
    else:
        operation["kind"] = "click_confirmation"
        operation["target_reference"] = CONTROL_REFERENCE
    with pytest.raises(ValueError):
        validate_unsubscribe_entry_operation_semantics(payload, entry_reference=ENTRY_REFERENCE,
            operation=UnsubscribeOperation.from_mapping(operation))
