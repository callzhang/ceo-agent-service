"""Strict lifecycle selection at the task orchestration boundary."""

from __future__ import annotations

from enum import StrEnum
import json
import math
from typing import Mapping

from app.agent_context import AgentTaskContext
from app.email_classifier_contracts import EmailAction
from app.email_store import email_action_identity
from app.store import ReplyTask


class TaskLifecycle(StrEnum):
    CONSUMER_AUDIT = "consumer_audit"
    EMAIL_UNSUBSCRIBE_CONSUMER_DIRECT = "email_unsubscribe_consumer_direct"


EMAIL_UNSUBSCRIBE_CONSUMER_DIRECT_LIFECYCLE_VERSION = (
    "email_unsubscribe_consumer_direct_v1"
)
_EMAIL_ACTION_PAYLOAD_SCHEMA = "email_agent_action.v1"
_REQUIRED_TEXT_FIELDS = (
    "action_identity",
    "action_plan_id",
    "account_id",
    "stable_message_identity",
    "thread_identity",
    "model_id",
    "config_version",
)
_REQUIRED_POSITIVE_INTEGER_FIELDS = ("action_plan_version", "classification_id")


def _is_non_blank_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_current_unsubscribe_authorization(payload: Mapping[str, object]) -> bool:
    confidence = payload.get("confidence")
    return (
        payload.get("schema") == _EMAIL_ACTION_PAYLOAD_SCHEMA
        and payload.get("lifecycle_version")
        == EMAIL_UNSUBSCRIBE_CONSUMER_DIRECT_LIFECYCLE_VERSION
        and payload.get("action_type") == EmailAction.UNSUBSCRIBE.value
        and payload.get("category") == "subscription"
        and payload.get("classification_source") in {"model", "user"}
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0 <= float(confidence) <= 1
        and isinstance(payload.get("action_parameters"), Mapping)
        and all(_is_non_blank_text(payload.get(field)) for field in _REQUIRED_TEXT_FIELDS)
        and all(
            _is_positive_integer(payload.get(field))
            for field in _REQUIRED_POSITIVE_INTEGER_FIELDS
        )
    )


def select_task_lifecycle(
    task: ReplyTask,
    context: AgentTaskContext,
) -> TaskLifecycle:
    """Select direct unsubscribe only for an intact, current authorization."""

    audited = TaskLifecycle.CONSUMER_AUDIT
    if (
        task.channel != "email"
        or context.channel != task.channel
        or context.task_id != task.id
        or context.conversation_id != task.conversation_id
        or context.trigger_message_id != task.trigger_message_id
    ):
        return audited

    try:
        payload = json.loads(task.trigger_message_json)
    except (json.JSONDecodeError, TypeError):
        return audited
    if not isinstance(payload, dict) or payload != context.trigger_raw_payload:
        return audited
    if not _is_current_unsubscribe_authorization(payload):
        return audited
    try:
        expected_action_identity = email_action_identity(
            account_id=str(payload["account_id"]),
            stable_message_identity=str(payload["stable_message_identity"]),
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(payload["action_plan_version"]),
        )
    except (KeyError, TypeError, ValueError):
        return audited
    if payload["action_identity"] != expected_action_identity:
        return audited
    if task.trigger_message_id != payload["action_identity"]:
        return audited

    return TaskLifecycle.EMAIL_UNSUBSCRIBE_CONSUMER_DIRECT
