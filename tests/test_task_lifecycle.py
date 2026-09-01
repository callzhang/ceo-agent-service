from __future__ import annotations

from copy import deepcopy
import json

import pytest

from app.agent_context import AgentTaskContext
from app.email_classifier_contracts import EmailAction
from app.email_store import email_action_identity
from app.store import ReplyTask
from app.task_lifecycle import TaskLifecycle, select_task_lifecycle


DIRECT_LIFECYCLE_VERSION = "email_unsubscribe_consumer_direct_v1"


def _payload(**overrides: object) -> dict[str, object]:
    identity = email_action_identity(
        account_id="account-primary",
        stable_message_identity="message-42",
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=2,
    )
    value: dict[str, object] = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": DIRECT_LIFECYCLE_VERSION,
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
    }
    value.update(overrides)
    return value


def _task(value: dict[str, object] | str, *, channel: str = "email") -> ReplyTask:
    encoded = value if isinstance(value, str) else json.dumps(value)
    identity = str(value.get("action_identity", "")) if isinstance(value, dict) else "invalid"
    return ReplyTask(
        id=42,
        channel=channel,
        conversation_id="email-conversation:42",
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=identity,
        trigger_create_time="2026-08-30T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=encoded,
        status="pending",
        attempts=0,
        created_at="2026-08-30T12:00:00+00:00",
        updated_at="2026-08-30T12:00:00+00:00",
    )


def _context(value: dict[str, object], *, task_id: int = 42) -> AgentTaskContext:
    return AgentTaskContext(
        task_id=task_id,
        channel="email",
        conversation_id="email-conversation:42",
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=str(value.get("action_identity", "")),
        trigger_sender="newsletter@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_create_time="2026-08-30T12:00:00+00:00",
        messages=(),
        materials=(),
        prior_receipts=(),
        trigger_raw_payload=deepcopy(value),
    )


def test_current_unsubscribe_authorization_selects_direct_consumer():
    value = _payload()
    assert select_task_lifecycle(_task(value), _context(value)) is TaskLifecycle.EMAIL_UNSUBSCRIBE_CONSUMER_DIRECT


@pytest.mark.parametrize(
    "field_value",
    [
        ("action_plan_id", ""),
        ("action_plan_version", 0),
        ("action_plan_version", True),
        ("classification_id", 0),
        ("account_id", ""),
        ("stable_message_identity", ""),
        ("thread_identity", ""),
        ("action_identity", ""),
        ("category", "work"),
        ("classification_source", "imported"),
        ("confidence", True),
        ("confidence", float("nan")),
        ("model_id", ""),
        ("config_version", ""),
        ("action_parameters", []),
    ],
)
def test_malformed_authorization_fails_closed(field_value):
    field, value = field_value
    payload = _payload(**{field: value})
    assert select_task_lifecycle(_task(payload), _context(payload)) is TaskLifecycle.CONSUMER_AUDIT


def test_context_payload_mismatch_fails_closed():
    task_payload = _payload()
    context_payload = _payload(action_plan_id="different-plan")
    assert select_task_lifecycle(_task(task_payload), _context(context_payload)) is TaskLifecycle.CONSUMER_AUDIT


def test_invalid_json_and_other_channels_remain_audited():
    payload = _payload()
    assert select_task_lifecycle(_task("{invalid"), _context(payload)) is TaskLifecycle.CONSUMER_AUDIT
    assert select_task_lifecycle(_task(payload, channel="dingtalk"), _context(payload)) is TaskLifecycle.CONSUMER_AUDIT
