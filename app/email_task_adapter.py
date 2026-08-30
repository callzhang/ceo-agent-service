"""Map immutable email ActionPlans onto the existing audited task lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import parse_qsl, unquote, urlsplit

from app.agent_context import (
    AgentContextMessage,
    AgentTaskContext,
    PriorReceipt,
    email_attachment_metadata_materials,
)
from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
)
from app.email_store import EmailStore
from app.leak_check import (
    assert_no_credentials,
    contains_local_runtime_leak,
    is_sensitive_field_name,
)
from app.store import (
    AutoReplyStore,
    EmailReplyTaskAuthorizationConflict,
    ReplyTask,
    ReplyTaskIdentityConflict,
    ReplyTaskSpec,
)


_PAYLOAD_SCHEMA = "email_agent_action.v1"
_MAX_METADATA_TEXT_LENGTH = 64 * 1024
_MAX_METADATA_JSON_LENGTH = 256 * 1024
_MAX_METADATA_DECODE_ROUNDS = 8


class EmailAgentTaskConflict(RuntimeError):
    """An existing queue identity is bound to different action metadata."""


class EmailAgentTaskMetadataError(ValueError):
    """Action metadata is unsafe for the durable task and Agent context."""


def _decode_metadata_token(value: str) -> str:
    if len(value.encode("utf-8")) > _MAX_METADATA_TEXT_LENGTH:
        raise EmailAgentTaskMetadataError(
            "email action metadata is not safe for persistence"
        )
    decoded = value
    for _ in range(_MAX_METADATA_DECODE_ROUNDS):
        expanded = unquote(decoded)
        if expanded == decoded:
            return decoded
        decoded = expanded
    if unquote(decoded) != decoded:
        raise EmailAgentTaskMetadataError(
            "email action metadata is not safe for persistence"
        )
    return decoded


def _canonicalize_metadata(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            {
                (
                    _decode_metadata_token(key)
                    if isinstance(key, str)
                    else key
                ): _canonicalize_metadata(item)
            }
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str):
        if isinstance(value, bytes | bytearray):
            return value
        return tuple(_canonicalize_metadata(item) for item in value)
    if isinstance(value, str):
        return _decode_metadata_token(value)
    return value


def _contains_home_relative_path(candidate: str) -> bool:
    normalized = candidate.replace("\\", "/")
    if not normalized.startswith("~") or "/" not in normalized:
        return False
    home_prefix = normalized.split("/", 1)[0]
    return home_prefix == "~" or len(home_prefix) > 1


def _contains_local_path_token(candidate: str) -> bool:
    decoded = _decode_metadata_token(candidate)
    return (
        _contains_home_relative_path(decoded)
        or PurePosixPath(decoded).is_absolute()
        or PureWindowsPath(decoded).is_absolute()
    )


def _contains_absolute_local_path(text: str) -> bool:
    for token in text.split():
        candidate = token.strip("'\"()[]{}<>,.;")
        if _contains_local_path_token(candidate):
            return True
    return False


def _url_component_is_sensitive(name: str) -> bool:
    normalized = "".join(
        character for character in name.casefold() if character.isalnum()
    )
    return (
        is_sensitive_field_name(name)
        or normalized in {"auth", "key", "sig"}
        or "signed" in normalized
        or normalized.startswith("xamz") and "signature" in normalized
    )


def _is_unsubscribe_target(value: str) -> bool:
    normalized = value.casefold().replace("\\", "/")
    for delimiter in ".?&=#_":
        normalized = normalized.replace(delimiter, "/")
    segments = {
        "".join(character for character in segment if character.isalnum())
        for segment in normalized.split("/")
        if segment
    }
    return bool(segments & {"unsubscribe", "optout"})


def _contains_forbidden_url(text: str) -> bool:
    for token in text.split():
        candidate = _decode_metadata_token(
            token.strip("'\"()[]{}<>,.;")
        )
        parsed = urlsplit(candidate)
        scheme = parsed.scheme.casefold()
        if scheme == "file":
            return True
        if scheme == "http":
            return True
        if scheme != "https":
            continue
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return True
        if _is_unsubscribe_target(
            _decode_metadata_token(f"{parsed.hostname or ''}/{parsed.path}")
        ):
            return True
        components = list(parse_qsl(parsed.query, keep_blank_values=True))
        fragment = _decode_metadata_token(parsed.fragment)
        components.extend(parse_qsl(fragment, keep_blank_values=True))
        if fragment and "=" not in fragment:
            components.append((fragment, ""))
        for name, component_value in components:
            decoded_name = _decode_metadata_token(name)
            decoded_value = _decode_metadata_token(component_value)
            if (
                _url_component_is_sensitive(decoded_name)
                or _url_component_is_sensitive(decoded_value)
                or _is_unsubscribe_target(decoded_name)
                or _is_unsubscribe_target(decoded_value)
                or _contains_local_path_token(decoded_value)
                or urlsplit(decoded_value).scheme.casefold() == "file"
            ):
                return True
    return False


def _contains_forbidden_metadata(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden_metadata(key) or _contains_forbidden_metadata(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str):
        return isinstance(value, bytes | bytearray) or any(
            _contains_forbidden_metadata(item) for item in value
        )
    if not isinstance(value, str):
        return False
    return (
        contains_local_runtime_leak(value)
        or _contains_absolute_local_path(value)
        or _contains_forbidden_url(value)
    )


def _assert_safe_email_metadata(value: object) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > _MAX_METADATA_JSON_LENGTH:
            raise ValueError("email action metadata is too large")
        canonical = _canonicalize_metadata(value)
        assert_no_credentials(canonical)
    except (TypeError, ValueError) as exc:
        raise EmailAgentTaskMetadataError(
            "email action metadata is not safe for persistence"
        ) from exc
    if _contains_forbidden_metadata(canonical):
        raise EmailAgentTaskMetadataError(
            "email action metadata is not safe for persistence"
        )


@dataclass(frozen=True)
class EmailThreadMessage:
    message_id: str
    sender: str
    text: str
    create_time: str

    def __post_init__(self) -> None:
        for field_name in ("message_id", "sender", "create_time"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must be non-empty")


@dataclass(frozen=True)
class EmailAgentTaskInput:
    stable_message_identity: str
    thread_identity: str
    subject: str
    trigger: EmailThreadMessage
    thread_messages: tuple[EmailThreadMessage, ...] = ()
    attachments: tuple[EmailAttachmentMetadata, ...] = ()
    prior_receipts: tuple[PriorReceipt, ...] = ()

    def __post_init__(self) -> None:
        if not self.stable_message_identity.strip():
            raise ValueError("stable_message_identity must be non-empty")
        if not self.thread_identity.strip():
            raise ValueError("thread_identity must be non-empty")
        object.__setattr__(self, "thread_identity", self.thread_identity.strip())
        if self.trigger.message_id != self.stable_message_identity:
            raise ValueError("trigger message must match stable_message_identity")
        if any(not isinstance(item, EmailThreadMessage) for item in self.thread_messages):
            raise TypeError("thread_messages must contain EmailThreadMessage")
        if any(
            not isinstance(item, EmailAttachmentMetadata)
            for item in self.attachments
        ):
            raise TypeError("attachments must contain EmailAttachmentMetadata")
        if any(not isinstance(item, PriorReceipt) for item in self.prior_receipts):
            raise TypeError("prior_receipts must contain PriorReceipt")


@dataclass(frozen=True)
class EmailAgentTaskRoute:
    action_type: EmailAction
    task: ReplyTask
    context: AgentTaskContext


def _digest_identity(prefix: str, payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{prefix}:{sha256(canonical.encode('utf-8')).hexdigest()}"


def email_conversation_id(account_id: str, thread_identity: str) -> str:
    """Return the stable queue conversation for one account-scoped mail thread."""

    account_id = account_id.strip()
    thread_identity = thread_identity.strip()
    if not account_id or not thread_identity:
        raise ValueError("account_id and thread_identity must be non-empty")
    return _digest_identity(
        "email-thread",
        {"account_id": account_id, "thread_identity": thread_identity},
    )


def email_action_identity(
    *,
    account_id: str,
    stable_message_identity: str,
    action_type: EmailAction,
    action_plan_version: int,
) -> str:
    """Identify one authorized email action across scans, restarts, and retraining."""

    account_id = account_id.strip()
    stable_message_identity = stable_message_identity.strip()
    action_type = EmailAction(action_type)
    if not account_id or not stable_message_identity:
        raise ValueError("email action identity fields must be non-empty")
    if action_plan_version <= 0:
        raise ValueError("action_plan_version must be positive")
    return _digest_identity(
        "email-action",
        {
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "action_type": action_type.value,
            "action_plan_version": action_plan_version,
        },
    )


class EmailAgentTaskAdapter:
    """Create Email tasks while leaving execution and Audit to the existing runtime."""

    def __init__(self, store: AutoReplyStore, email_store: EmailStore):
        if store.path.resolve() != email_store.path.resolve():
            raise ValueError(
                "email task and classification stores must share one database"
            )
        self.store = store

    def ensure_action_plan_tasks(
        self,
        action_plan: EmailActionPlan,
        task_input: EmailAgentTaskInput,
    ) -> tuple[EmailAgentTaskRoute, ...]:
        if not action_plan.agent_actions:
            return ()
        _assert_safe_email_metadata(
            [
                {
                    "receipt_id": receipt.receipt_id,
                    "operation": receipt.operation,
                    "summary": receipt.summary,
                    "completed": receipt.completed,
                }
                for receipt in task_input.prior_receipts
            ]
        )

        conversation_id = email_conversation_id(
            action_plan.account_id,
            task_input.thread_identity,
        )
        prepared: list[tuple[EmailAction, dict[str, object], ReplyTaskSpec]] = []
        for action_type in action_plan.agent_actions:
            payload = self._safe_action_metadata(
                action_plan=action_plan,
                task_input=task_input,
                action_type=action_type,
            )
            payload_json = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            prepared.append(
                (
                    action_type,
                    payload,
                    ReplyTaskSpec(
                        channel="email",
                        conversation_id=conversation_id,
                        conversation_title=f"Email {action_type.value}",
                        single_chat=False,
                        trigger_message_id=str(payload["action_identity"]),
                        trigger_create_time=task_input.trigger.create_time,
                        trigger_sender=task_input.trigger.sender,
                        trigger_text=(
                            f"Immutable ActionPlan authorizes {action_type.value}."
                        ),
                        trigger_message_json=payload_json,
                    ),
                )
            )
        try:
            tasks = self.store.ensure_authorized_email_reply_tasks(
                classification_id=action_plan.classification_id,
                account_id=action_plan.account_id,
                stable_message_identity=task_input.stable_message_identity,
                thread_identity=task_input.thread_identity,
                action_plan_id=action_plan.action_plan_id,
                task_specs=tuple(spec for _, _, spec in prepared),
            )
        except (
            EmailReplyTaskAuthorizationConflict,
            ReplyTaskIdentityConflict,
        ) as exc:
            raise EmailAgentTaskConflict(
                "email action identity is bound to different metadata"
            ) from exc

        routes: list[EmailAgentTaskRoute] = []
        for (action_type, payload, _), task in zip(prepared, tasks, strict=True):
            routes.append(
                EmailAgentTaskRoute(
                    action_type=action_type,
                    task=task,
                    context=self._build_context(
                        task=task,
                        payload=payload,
                        task_input=task_input,
                    ),
                )
            )
        return tuple(routes)

    @staticmethod
    def _safe_action_metadata(
        *,
        action_plan: EmailActionPlan,
        task_input: EmailAgentTaskInput,
        action_type: EmailAction,
    ) -> dict[str, object]:
        parameters = dict(action_plan.action_parameters.get(action_type, {}))
        action_identity = email_action_identity(
            account_id=action_plan.account_id,
            stable_message_identity=task_input.stable_message_identity,
            action_type=action_type,
            action_plan_version=action_plan.action_plan_version,
        )
        payload: dict[str, object] = {
            "schema": _PAYLOAD_SCHEMA,
            "account_id": action_plan.account_id,
            "stable_message_identity": task_input.stable_message_identity,
            "thread_identity": task_input.thread_identity,
            "action_identity": action_identity,
            "action_type": action_type.value,
            "action_plan_id": action_plan.action_plan_id,
            "action_plan_version": action_plan.action_plan_version,
            "classification_id": action_plan.classification_id,
            "category": action_plan.category.value,
            "classification_source": action_plan.classification_source,
            "confidence": action_plan.confidence,
            "model_id": action_plan.model_id,
            "config_version": action_plan.config_version,
            "action_parameters": parameters,
        }
        _assert_safe_email_metadata(payload)
        return payload

    @staticmethod
    def _build_context(
        *,
        task: ReplyTask,
        payload: dict[str, object],
        task_input: EmailAgentTaskInput,
    ) -> AgentTaskContext:
        messages = tuple(
            AgentContextMessage(
                message_id=message.message_id,
                sender=message.sender,
                text=message.text,
                create_time=message.create_time,
            )
            for message in (*task_input.thread_messages, task_input.trigger)
        )
        return AgentTaskContext(
            task_id=task.id,
            channel="email",
            conversation_id=task.conversation_id,
            conversation_title=task_input.subject,
            single_chat=False,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task_input.trigger.sender,
            trigger_text=task_input.trigger.text,
            trigger_create_time=task_input.trigger.create_time,
            messages=messages,
            materials=email_attachment_metadata_materials(
                task_input.attachments,
                source_message_id=task_input.stable_message_identity,
            ),
            prior_receipts=task_input.prior_receipts,
            trigger_raw_payload=payload,
            image_paths=(),
            image_sha256s=(),
        )
