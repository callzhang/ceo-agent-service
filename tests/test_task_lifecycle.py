from __future__ import annotations

from copy import deepcopy
import json

import pytest

from app.agent_context import AgentTaskContext
from app.email_classifier_contracts import EmailAction
from app.email_store import email_action_identity
from app.email_task_adapter import email_conversation_id
from app.email_unsubscribe import UnsubscribeEntrySource
from app.store import ReplyTask
from app.task_lifecycle import (
    TaskLifecycle,
    select_task_lifecycle,
    validate_audited_email_task,
)


AUDITED_LIFECYCLE_VERSION = "email_unsubscribe_audited_v2"
LEGACY_LIFECYCLE_VERSION = "email_unsubscribe_consumer_direct_v1"
CANONICAL_CONVERSATION_ID = email_conversation_id(
    "account-primary",
    "thread-42",
)


def _raw_payload_with_confidence_digits(digit_count: int) -> str:
    encoded = json.dumps(_payload(), separators=(",", ":"))
    marker = '"confidence":0.98'
    assert marker in encoded
    return encoded.replace(marker, f'"confidence":{"9" * digit_count}')


def _payload(**overrides: object) -> dict[str, object]:
    identity = email_action_identity(
        account_id="account-primary",
        stable_message_identity="message-42",
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=2,
    )
    value: dict[str, object] = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": AUDITED_LIFECYCLE_VERSION,
        "action_type": "unsubscribe",
        "action_identity": identity,
        "action_plan_id": "email-action-plan:42",
        "action_plan_version": 2,
        "classification_id": 42,
        "account_id": "account-primary",
        "stable_message_identity": "message-42",
        "thread_identity": "thread-42",
        "category": "subscription",
        "classification_source": "model",
        "confidence": 0.98,
        "model_id": "email-model:2026-08-30:test",
        "config_version": "email-config:v3",
        "action_parameters": {},
        "unsubscribe_entries": [],
        "unsubscribe_authentication": None,
        "unsubscribe_network_policy_reference": "network-policy:test",
        "unsubscribe_network_policy_origin_references": [
            "network-origin:test"
        ],
    }
    value.update(overrides)
    return value


def _task(
    value: dict[str, object] | str,
    *,
    channel: str = "email",
    task_id: int = 42,
    conversation_id: str = CANONICAL_CONVERSATION_ID,
    trigger_message_id: str | None = None,
) -> ReplyTask:
    encoded = value if isinstance(value, str) else json.dumps(value)
    identity = (
        str(value.get("action_identity", ""))
        if isinstance(value, dict)
        else "invalid"
    )
    return ReplyTask(
        id=task_id,
        channel=channel,
        conversation_id=conversation_id,
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=(
            identity if trigger_message_id is None else trigger_message_id
        ),
        trigger_create_time="2026-08-30T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=encoded,
        status="pending",
        attempts=0,
        created_at="2026-08-30T12:00:00+00:00",
        updated_at="2026-08-30T12:00:00+00:00",
    )


def _context(
    value: dict[str, object],
    *,
    task_id: int = 42,
    channel: str = "email",
    conversation_id: str = CANONICAL_CONVERSATION_ID,
    trigger_message_id: str | None = None,
) -> AgentTaskContext:
    return AgentTaskContext(
        task_id=task_id,
        channel=channel,
        conversation_id=conversation_id,
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=(
            str(value.get("action_identity", ""))
            if trigger_message_id is None
            else trigger_message_id
        ),
        trigger_sender="newsletter@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_create_time="2026-08-30T12:00:00+00:00",
        messages=(),
        materials=(),
        prior_receipts=(),
        trigger_raw_payload=deepcopy(value),
    )


def _canonically_bound_task_and_context(
    payload: dict[str, object],
) -> tuple[ReplyTask, AgentTaskContext]:
    value = deepcopy(payload)
    value["action_identity"] = email_action_identity(
        account_id=str(value["account_id"]),
        stable_message_identity=str(value["stable_message_identity"]),
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=int(value["action_plan_version"]),
    )
    conversation_id = email_conversation_id(
        str(value["account_id"]),
        str(value["thread_identity"]),
    )
    return (
        _task(value, conversation_id=conversation_id),
        _context(value, conversation_id=conversation_id),
    )


def test_valid_audited_v2_unsubscribe_uses_global_consumer_audit_lifecycle():
    value = _payload()
    task = _task(value)
    context = _context(value)

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is True


@pytest.mark.parametrize(
    "field",
    (
        "account_id",
        "stable_message_identity",
        "thread_identity",
        "action_plan_id",
        "model_id",
        "config_version",
    ),
)
@pytest.mark.parametrize(
    "private_material",
    (
        "api_token=do-not-persist",
        "https://private.example/model?secret=value",
        "identifier?private=value",
        "/Users/derek/private/config",
        "~/private/config",
        "file:///Users/derek/private/config",
    ),
)
def test_private_material_in_unconstrained_text_fields_fails_closed(
    field: str,
    private_material: str,
):
    payload = _payload(**{field: private_material})
    task, context = _canonically_bound_task_and_context(payload)

    assert task.trigger_message_id == context.trigger_message_id
    assert task.trigger_message_id == context.trigger_raw_payload["action_identity"]
    assert task.conversation_id == email_conversation_id(
        str(context.trigger_raw_payload["account_id"]),
        str(context.trigger_raw_payload["thread_identity"]),
    )
    assert validate_audited_email_task(task, context) is False


def test_nonempty_unsubscribe_action_parameters_fail_closed():
    payload = _payload(action_parameters={"target": "opaque"})

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


def test_unknown_top_level_payload_key_fails_closed():
    payload = _payload(unexpected="value")

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize(
    "missing_field",
    (
        "unsubscribe_entries",
        "unsubscribe_authentication",
        "unsubscribe_network_policy_reference",
        "unsubscribe_network_policy_origin_references",
    ),
)
def test_missing_unsubscribe_specific_field_fails_closed(missing_field: str):
    payload = _payload()
    payload.pop(missing_field)

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize(
    "invalid_entries",
    (None, {}, "unsubscribe-entry:opaque", ["not-a-dict"]),
)
def test_unsubscribe_entries_must_be_a_json_list_of_objects(invalid_entries):
    payload = _payload(unsubscribe_entries=invalid_entries)

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


def _valid_unsubscribe_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "source": UnsubscribeEntrySource.HEADER_HTTPS.value,
        "reference": "unsubscribe-entry:" + "a" * 64,
        "priority": 10,
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    ("source", "priority"),
    (
        (UnsubscribeEntrySource.HEADER_HTTPS.value, 10),
        (UnsubscribeEntrySource.HEADER_MAILTO.value, 20),
        (UnsubscribeEntrySource.BODY_HTML_HTTPS.value, 30),
        (UnsubscribeEntrySource.BODY_TEXT_HTTPS.value, 40),
    ),
)
def test_ordinary_unsubscribe_entry_source_and_priority_are_valid(
    source: str,
    priority: int,
):
    payload = _payload(
        unsubscribe_entries=[
            _valid_unsubscribe_entry(source=source, priority=priority)
        ]
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is True


def test_authenticated_one_click_entry_is_valid():
    payload = _payload(
        unsubscribe_entries=[
            _valid_unsubscribe_entry(
                source=UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value,
                priority=0,
            )
        ],
        unsubscribe_authentication={
            "evidence_reference": "dkim-evidence:mail-42",
            "one_click_verified": True,
        },
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is True


@pytest.mark.parametrize(
    "invalid_reference",
    (
        "unsubscribe-entry:abc123",
        "unsubscribe-entry:" + "g" * 64,
    ),
)
def test_unsubscribe_entry_reference_must_be_canonical_sha256(
    invalid_reference: str,
):
    payload = _payload(
        unsubscribe_entries=[
            _valid_unsubscribe_entry(reference=invalid_reference)
        ]
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize(
    "authentication",
    (
        None,
        {
            "evidence_reference": "dkim-evidence:mail-42",
            "one_click_verified": False,
        },
    ),
)
def test_one_click_entry_requires_verified_authentication(authentication):
    payload = _payload(
        unsubscribe_entries=[
            _valid_unsubscribe_entry(
                source=UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value,
                priority=0,
            )
        ],
        unsubscribe_authentication=authentication,
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize(
    ("source", "wrong_priority"),
    (
        (UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value, 1),
        (UnsubscribeEntrySource.HEADER_HTTPS.value, 11),
        (UnsubscribeEntrySource.HEADER_MAILTO.value, 21),
        (UnsubscribeEntrySource.BODY_HTML_HTTPS.value, 31),
        (UnsubscribeEntrySource.BODY_TEXT_HTTPS.value, 41),
    ),
)
def test_unsubscribe_entry_source_priority_mismatch_fails_closed(
    source: str,
    wrong_priority: int,
):
    payload = _payload(
        unsubscribe_entries=[
            _valid_unsubscribe_entry(
                source=source,
                priority=wrong_priority,
            )
        ],
        unsubscribe_authentication=(
            {
                "evidence_reference": "dkim-evidence:mail-42",
                "one_click_verified": True,
            }
            if source == UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value
            else None
        ),
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize("missing_field", ("source", "reference", "priority"))
def test_unsubscribe_entry_requires_every_exact_field(missing_field: str):
    entry = _valid_unsubscribe_entry()
    entry.pop(missing_field)
    payload = _payload(unsubscribe_entries=[entry])

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


def test_unsubscribe_entry_rejects_extra_field():
    payload = _payload(
        unsubscribe_entries=[_valid_unsubscribe_entry(private_url="redacted")]
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("source", "unknown"),
        ("source", 1),
        ("reference", ""),
        ("reference", "opaque-reference:without-required-prefix"),
        ("reference", "https://example.com/unsubscribe?token=private"),
        ("reference", "file:///Users/derek/private/unsubscribe"),
        ("priority", -1),
        ("priority", True),
        ("priority", 1.0),
    ),
)
def test_unsubscribe_entry_scalar_contract_fails_closed(
    field: str,
    invalid_value: object,
):
    payload = _payload(
        unsubscribe_entries=[_valid_unsubscribe_entry(**{field: invalid_value})]
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


def test_duplicate_unsubscribe_entry_references_fail_closed():
    entry = _valid_unsubscribe_entry()
    payload = _payload(unsubscribe_entries=[entry, dict(entry)])

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


def test_exact_unsubscribe_authentication_object_is_valid():
    payload = _payload(
        unsubscribe_authentication={
            "evidence_reference": "dkim-evidence:mail-42",
            "one_click_verified": True,
        }
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is True


@pytest.mark.parametrize(
    "invalid_authentication",
    (
        {},
        {"evidence_reference": "dkim-evidence:mail-42"},
        {"one_click_verified": True},
        {
            "evidence_reference": "dkim-evidence:mail-42",
            "one_click_verified": True,
            "extra": "value",
        },
        {"evidence_reference": "", "one_click_verified": True},
        {
            "evidence_reference": "https://example.com/private-evidence",
            "one_click_verified": True,
        },
        {
            "evidence_reference": "file:///Users/derek/private/evidence",
            "one_click_verified": True,
        },
        {"evidence_reference": 42, "one_click_verified": True},
        {"evidence_reference": "dkim-evidence:mail-42", "one_click_verified": 1},
        "dkim-evidence:mail-42",
        [],
    ),
)
def test_unsubscribe_authentication_contract_fails_closed(
    invalid_authentication,
):
    payload = _payload(unsubscribe_authentication=invalid_authentication)

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize(
    "invalid_reference",
    (
        "",
        "https://example.com/private-policy",
        "file:///Users/derek/private/policy",
        ["network-policy:test"],
    ),
)
def test_unsubscribe_network_policy_reference_fails_closed(invalid_reference):
    payload = _payload(unsubscribe_network_policy_reference=invalid_reference)

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


@pytest.mark.parametrize(
    "invalid_references",
    (
        "network-origin:test",
        [],
        ["network-origin:test", "network-origin:test"],
        [""],
        ["https://example.com/private-origin"],
        ["file:///Users/derek/private/origin"],
        [42],
    ),
)
def test_unsubscribe_network_policy_origins_fail_closed(invalid_references):
    payload = _payload(
        unsubscribe_network_policy_origin_references=invalid_references
    )

    assert validate_audited_email_task(
        _task(payload),
        _context(payload),
    ) is False


def test_legacy_v1_unsubscribe_cannot_select_a_direct_lifecycle():
    value = _payload(lifecycle_version=LEGACY_LIFECYCLE_VERSION)
    task = _task(value)
    context = _context(value)

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is False


@pytest.mark.parametrize(
    "field_value",
    [
        ("schema", "email_agent_action.v0"),
        ("lifecycle_version", LEGACY_LIFECYCLE_VERSION),
        ("action_type", "auto_reply"),
        ("action_identity", ""),
        ("action_plan_id", ""),
        ("action_plan_version", 0),
        ("action_plan_version", True),
        ("classification_id", 0),
        ("classification_id", True),
        ("account_id", ""),
        ("stable_message_identity", ""),
        ("thread_identity", ""),
        ("category", "work"),
        ("classification_source", "imported"),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("confidence", True),
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("model_id", ""),
        ("config_version", ""),
        ("action_parameters", []),
    ],
)
def test_malformed_authorization_fails_closed(field_value):
    field, value = field_value
    payload = _payload(**{field: value})
    task = _task(payload)
    context = _context(payload)

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is False


def test_derived_action_identity_must_match_payload_identity():
    payload = _payload(action_identity="email-action:forged")
    task = _task(payload)
    context = _context(payload)

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is False


def test_task_trigger_id_must_match_payload_action_identity():
    payload = _payload()
    task = _task(payload, trigger_message_id="email-action:different")
    context = _context(payload, trigger_message_id="email-action:different")

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is False


def test_context_payload_mismatch_fails_closed():
    task_payload = _payload()
    context_payload = _payload(action_plan_id="different-plan")
    task = _task(task_payload)
    context = _context(context_payload)

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is False


def test_matching_noncanonical_conversation_identity_fails_closed():
    payload = _payload()
    wrong_conversation_id = "email-thread:forged"
    task = _task(payload, conversation_id=wrong_conversation_id)
    context = _context(payload, conversation_id=wrong_conversation_id)

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is False


@pytest.mark.parametrize(
    ("task_overrides", "context_overrides"),
    [
        ({"task_id": 41}, {}),
        ({}, {"task_id": 41}),
        ({"conversation_id": "email-conversation:other"}, {}),
        ({}, {"conversation_id": "email-conversation:other"}),
        ({"channel": "dingtalk"}, {}),
        ({}, {"channel": "dingtalk"}),
        ({}, {"trigger_message_id": "email-action:other"}),
    ],
)
def test_task_and_context_identity_must_match(
    task_overrides: dict[str, object],
    context_overrides: dict[str, object],
):
    payload = _payload()
    task = _task(payload, **task_overrides)
    context = _context(payload, **context_overrides)

    assert select_task_lifecycle(task, context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(task, context) is False


def test_invalid_json_and_other_channels_remain_audited():
    payload = _payload()
    invalid_task = _task("{invalid")
    email_context = _context(payload)
    other_task = _task(payload, channel="dingtalk")
    other_context = _context(payload, channel="dingtalk")

    assert select_task_lifecycle(invalid_task, email_context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(invalid_task, email_context) is False
    assert select_task_lifecycle(other_task, other_context) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(other_task, other_context) is False


def test_four_thousand_digit_confidence_fails_closed_without_exception():
    raw_payload = _raw_payload_with_confidence_digits(4_000)
    decoded_payload = json.loads(raw_payload)
    task = _task(
        raw_payload,
        trigger_message_id=str(decoded_payload["action_identity"]),
    )

    assert validate_audited_email_task(
        task,
        _context(decoded_payload),
    ) is False


def test_integer_over_python_digit_limit_fails_closed_without_exception():
    payload = _payload()
    task = _task(
        _raw_payload_with_confidence_digits(5_000),
        trigger_message_id=str(payload["action_identity"]),
    )

    assert validate_audited_email_task(task, _context(payload)) is False


def test_deeply_nested_json_fails_closed_without_exception():
    payload = _payload()
    raw_payload = "[" * 10_000 + "0" + "]" * 10_000
    task = _task(
        raw_payload,
        trigger_message_id=str(payload["action_identity"]),
    )

    assert validate_audited_email_task(task, _context(payload)) is False
