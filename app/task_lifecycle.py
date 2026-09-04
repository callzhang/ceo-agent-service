"""Strict lifecycle selection at the task orchestration boundary."""

from __future__ import annotations

from enum import StrEnum
import json
import math
import re

from app.agent_context import AgentTaskContext
from app.email_classifier_contracts import EmailAction
from app.email_store import (
    email_action_identity,
    is_valid_unsubscribe_opaque_reference,
)
from app.email_task_adapter import (
    EmailAgentTaskMetadataError,
    assert_safe_email_unsubscribe_metadata,
    email_conversation_id,
)
from app.email_unsubscribe import UnsubscribeEntrySource
from app.store import ReplyTask


class TaskLifecycle(StrEnum):
    CONSUMER_AUDIT = "consumer_audit"
EMAIL_UNSUBSCRIBE_AUDITED_LIFECYCLE_VERSION = "email_unsubscribe_audited_v2"
_EMAIL_ACTION_PAYLOAD_SCHEMA = "email_agent_action.v1"
_AUDITED_UNSUBSCRIBE_PAYLOAD_KEYS = frozenset(
    {
        "schema",
        "lifecycle_version",
        "account_id",
        "stable_message_identity",
        "thread_identity",
        "action_identity",
        "action_type",
        "action_plan_id",
        "action_plan_version",
        "classification_id",
        "category",
        "classification_source",
        "confidence",
        "model_id",
        "config_version",
        "action_parameters",
        "unsubscribe_entries",
        "unsubscribe_authentication",
        "unsubscribe_network_policy_reference",
        "unsubscribe_network_policy_origin_references",
    }
)
_UNSUBSCRIBE_ENTRY_KEYS = frozenset({"source", "reference", "priority"})
_UNSUBSCRIBE_AUTHENTICATION_KEYS = frozenset(
    {"evidence_reference", "one_click_verified"}
)
_UNSUBSCRIBE_ENTRY_REFERENCE = re.compile(r"unsubscribe-entry:[0-9a-f]{64}")
_UNSUBSCRIBE_ENTRY_PRIORITIES = {
    UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value: 0,
    UnsubscribeEntrySource.HEADER_HTTPS.value: 10,
    UnsubscribeEntrySource.HEADER_MAILTO.value: 20,
    UnsubscribeEntrySource.BODY_HTML_HTTPS.value: 30,
    UnsubscribeEntrySource.BODY_TEXT_HTTPS.value: 40,
}
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


def _is_finite_confidence(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return 0 <= value <= 1 and math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _is_unsubscribe_entry_reference(value: object) -> bool:
    return (
        isinstance(value, str)
        and _UNSUBSCRIBE_ENTRY_REFERENCE.fullmatch(value) is not None
        and is_valid_unsubscribe_opaque_reference(value)
    )


def _has_valid_unsubscribe_entries(
    value: object,
    authentication: object,
) -> bool:
    if type(value) is not list:
        return False
    references: list[str] = []
    for item in value:
        if type(item) is not dict or set(item) != _UNSUBSCRIBE_ENTRY_KEYS:
            return False
        source = item.get("source")
        reference = item.get("reference")
        priority = item.get("priority")
        if (
            not isinstance(source, str)
            or source not in _UNSUBSCRIBE_ENTRY_PRIORITIES
            or not _is_unsubscribe_entry_reference(reference)
            or type(priority) is not int
            or priority != _UNSUBSCRIBE_ENTRY_PRIORITIES[source]
        ):
            return False
        references.append(reference)
    if len(references) != len(set(references)):
        return False
    has_one_click = any(
        item.get("source")
        == UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value
        for item in value
    )
    return not has_one_click or (
        type(authentication) is dict
        and authentication.get("one_click_verified") is True
    )


def _has_valid_unsubscribe_authentication(value: object) -> bool:
    if value is None:
        return True
    return (
        type(value) is dict
        and set(value) == _UNSUBSCRIBE_AUTHENTICATION_KEYS
        and is_valid_unsubscribe_opaque_reference(
            value.get("evidence_reference")
        )
        and type(value.get("one_click_verified")) is bool
    )


def _has_valid_network_policy_references(payload: dict[str, object]) -> bool:
    policy_reference = payload.get("unsubscribe_network_policy_reference")
    origin_references = payload.get(
        "unsubscribe_network_policy_origin_references"
    )
    return (
        is_valid_unsubscribe_opaque_reference(policy_reference)
        and type(origin_references) is list
        and bool(origin_references)
        and all(
            is_valid_unsubscribe_opaque_reference(reference)
            for reference in origin_references
        )
        and len(origin_references) == len(set(origin_references))
    )


def _is_audited_unsubscribe_task_payload(payload: dict[str, object]) -> bool:
    confidence = payload.get("confidence")
    classification_source = payload.get("classification_source")
    return (
        set(payload) == _AUDITED_UNSUBSCRIBE_PAYLOAD_KEYS
        and payload.get("schema") == _EMAIL_ACTION_PAYLOAD_SCHEMA
        and payload.get("lifecycle_version")
        == EMAIL_UNSUBSCRIBE_AUDITED_LIFECYCLE_VERSION
        and payload.get("action_type") == EmailAction.UNSUBSCRIBE.value
        and payload.get("category") == "subscription"
        and isinstance(classification_source, str)
        and classification_source in {"model", "user"}
        and _is_finite_confidence(confidence)
        and type(payload.get("action_parameters")) is dict
        and payload.get("action_parameters") == {}
        and all(_is_non_blank_text(payload.get(field)) for field in _REQUIRED_TEXT_FIELDS)
        and all(
            _is_positive_integer(payload.get(field))
            for field in _REQUIRED_POSITIVE_INTEGER_FIELDS
        )
        and _has_valid_unsubscribe_entries(
            payload.get("unsubscribe_entries"),
            payload.get("unsubscribe_authentication"),
        )
        and _has_valid_unsubscribe_authentication(
            payload.get("unsubscribe_authentication")
        )
        and _has_valid_network_policy_references(payload)
    )


def validate_audited_email_task(
    task: ReplyTask,
    context: AgentTaskContext,
) -> bool:
    """Validate an audited-v2 Email unsubscribe task without side effects."""

    if (
        task.channel != "email"
        or context.channel != "email"
        or context.task_id != task.id
        or context.conversation_id != task.conversation_id
        or context.trigger_message_id != task.trigger_message_id
    ):
        return False

    try:
        payload = json.loads(task.trigger_message_json)
    except (TypeError, ValueError, RecursionError):
        return False
    if not isinstance(payload, dict):
        return False
    try:
        payload_matches_context = payload == context.trigger_raw_payload
    except (TypeError, ValueError, RecursionError):
        return False
    if not payload_matches_context:
        return False
    try:
        assert_safe_email_unsubscribe_metadata(payload)
    except EmailAgentTaskMetadataError:
        return False
    if not _is_audited_unsubscribe_task_payload(payload):
        return False
    try:
        expected_conversation_id = email_conversation_id(
            str(payload["account_id"]),
            str(payload["thread_identity"]),
        )
        expected_action_identity = email_action_identity(
            account_id=str(payload["account_id"]),
            stable_message_identity=str(payload["stable_message_identity"]),
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(payload["action_plan_version"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    if task.conversation_id != expected_conversation_id:
        return False
    if payload["action_identity"] != expected_action_identity:
        return False
    if task.trigger_message_id != payload["action_identity"]:
        return False

    return True


def select_task_lifecycle(
    task: ReplyTask,
    context: AgentTaskContext,
) -> TaskLifecycle:
    """Select the ordinary global Consumer/Audit lifecycle for every task."""

    return TaskLifecycle.CONSUMER_AUDIT
