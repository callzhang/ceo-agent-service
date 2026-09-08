"""Map immutable email ActionPlans onto the existing audited task lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import PurePosixPath, PureWindowsPath
import sqlite3
from urllib.parse import parse_qsl, unquote, urlsplit

from app.agent_context import (
    AgentContextMessage,
    AgentTaskContext,
    PriorReceipt,
    email_attachment_metadata_materials,
)
from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailProviderLocator,
    validate_email_category_key,
)
from app.email_store import EmailStore, is_valid_unsubscribe_opaque_reference
from app.email_reply_delivery import EmailReplyEffect, email_action_identity
from app.email_unsubscribe import (
    EmailUnsubscribeContinuation,
    EmailUnsubscribeEffect,
    UnsubscribeAuthenticationEvidence,
    UnsubscribeDiscoveredControl,
    UnsubscribeEntrySource,
    UnsubscribeEntry,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
    browser_unsubscribe_entries,
    extract_unsubscribe_entries,
    select_exact_unsubscribe_entry,
)
from app.leak_check import (
    assert_no_credentials,
    contains_local_runtime_leak,
    is_sensitive_url_component_name,
)
from app.store import (
    AutoReplyStore,
    EmailReplyTaskAuthorizationConflict,
    ReplyTask,
    ReplyTaskIdentityConflict,
    ReplyTaskSpec,
)
from app.skill_features import FeatureRegistry


_PAYLOAD_SCHEMA = "email_agent_action.v1"
_ACTION_LIFECYCLE_VERSIONS = {
    EmailAction.AUTO_REPLY: "consumer_audit_v1",
    EmailAction.UNSUBSCRIBE: "email_unsubscribe_audited_v2",
}
_MAX_METADATA_TEXT_LENGTH = 64 * 1024
_MAX_METADATA_JSON_LENGTH = 256 * 1024
_MAX_METADATA_DECODE_ROUNDS = 8
_UNSUBSCRIBE_CONTROL_KINDS = frozenset(
    {
        "form",
        "link",
        "button",
        "confirmation_email",
        "email_otp",
        "captcha_handoff",
        "credential_handoff",
    }
)
_UNSUBSCRIBE_CONTROL_INTENTS = frozenset({"continue", "unsubscribe", "confirm"})
_UNSUBSCRIBE_ENTRY_PRIORITIES = {
    "header_one_click_https": 0,
    "header_https": 10,
    "body_html_https": 30,
    "body_text_https": 40,
}
_UNSUBSCRIBE_ENTRY_OPERATION_SOURCES = {
    UnsubscribeOperationKind.POST_ONE_CLICK: frozenset(
        {UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value}
    ),
    UnsubscribeOperationKind.OPEN_ENTRY: frozenset(
        {
            UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS.value,
            UnsubscribeEntrySource.HEADER_HTTPS.value,
            UnsubscribeEntrySource.BODY_HTML_HTTPS.value,
            UnsubscribeEntrySource.BODY_TEXT_HTTPS.value,
        }
    ),
}


@dataclass(frozen=True)
class EmailClassificationTaskInput:
    """Durable, metadata-bounded input for one classifier Agent call."""

    stable_message_identity: str
    provider_locator: EmailProviderLocator
    provider_unread: bool
    message: Mapping[str, object]
    allowed_category_keys: tuple[str, ...]
    category_descriptions: Mapping[str, object]
    folder_targets: Mapping[str, str]
    config_version: str
    unsubscribe_candidates: tuple[Mapping[str, object], ...]

    @classmethod
    def from_message(
        cls,
        message: Mapping[str, object],
        *,
        allowed_category_keys: Sequence[str],
        category_descriptions: Mapping[str, object],
        folder_targets: Mapping[str, str],
        config_version: str,
        unsubscribe_candidates: Sequence[object],
    ) -> "EmailClassificationTaskInput":
        locator = EmailProviderLocator.model_validate(
            {
                "account_id": message.get("accountId"),
                "folder": message.get("folder"),
                "uidvalidity": message.get("uidValidity"),
                "uid": message.get("uid"),
                "rfc_message_id": message.get("messageId"),
                "thread_id": message.get("threadId"),
            }
        )
        stable_identity = str(
            message.get("stableMessageIdentity") or locator.stable_message_identity
        ).strip()
        if not stable_identity.startswith(f"{locator.account_id}:"):
            raise ValueError("classification task identity is not account-scoped")
        if type(message.get("providerUnread")) is not bool:
            raise ValueError(
                "classification task requires strict provider unread state"
            )
        categories = tuple(
            validate_email_category_key(key) for key in allowed_category_keys
        )
        if not categories or len(categories) != len(set(categories)):
            raise ValueError("allowed category keys must be unique and nonempty")
        if set(category_descriptions) != set(categories):
            raise ValueError(
                "category descriptions must cover the allowed category set"
            )
        if not isinstance(config_version, str) or not config_version.strip():
            raise ValueError("config_version must be nonblank")
        attachments = message.get("attachments") or ()
        if not isinstance(attachments, Sequence) or isinstance(
            attachments, str | bytes
        ):
            raise ValueError("attachments must be metadata records")
        safe_attachments = tuple(
            EmailAttachmentMetadata.model_validate(item).model_dump(mode="json")
            for item in attachments
        )
        sender = message.get("from")
        safe_text = str(message.get("markdownBody") or message.get("textBody") or "")
        for index, candidate in enumerate(unsubscribe_candidates):
            private_url = (
                candidate.private_url
                if isinstance(candidate, UnsubscribeEntry)
                else str(candidate)
            )
            if private_url:
                safe_text = safe_text.replace(
                    private_url, f"[UNSUBSCRIBE_CANDIDATE:{index}]"
                )
        safe_message = {
            "sender": dict(sender)
            if isinstance(sender, Mapping)
            else str(sender or ""),
            "to_recipients": list(message.get("toRecipients") or ()),
            "cc_recipients": list(message.get("ccRecipients") or ()),
            "subject": str(message.get("subject") or ""),
            "date": str(message.get("date") or ""),
            "text": safe_text,
            "headers": {
                "auto_submitted": str(message.get("autoSubmitted") or ""),
            },
            "attachments": list(safe_attachments),
        }
        redacted_candidates = tuple(
            (
                dict(candidate.redacted)
                | {
                    "index": index,
                    "digest": candidate.reference.removeprefix("unsubscribe-entry:"),
                }
                if isinstance(candidate, UnsubscribeEntry)
                else {
                    "index": index,
                    "source": "legacy",
                    "digest": sha256(str(candidate).encode("utf-8")).hexdigest(),
                    "reference": "unsubscribe-entry:"
                    + sha256(str(candidate).encode("utf-8")).hexdigest(),
                }
            )
            for index, candidate in enumerate(unsubscribe_candidates)
        )
        return cls(
            stable_message_identity=stable_identity,
            provider_locator=locator,
            provider_unread=message["providerUnread"],
            message=safe_message,
            allowed_category_keys=categories,
            category_descriptions=dict(category_descriptions),
            folder_targets=dict(folder_targets),
            config_version=config_version.strip(),
            unsubscribe_candidates=redacted_candidates,
        )

    def payload(self) -> dict[str, object]:
        return {
            "stable_message_identity": self.stable_message_identity,
            "provider_locator": self.provider_locator.model_dump(mode="json"),
            "provider_unread": self.provider_unread,
            "message": dict(self.message),
            "allowed_category_keys": list(self.allowed_category_keys),
            "category_descriptions": dict(self.category_descriptions),
            "folder_targets": dict(self.folder_targets),
            "config_version": self.config_version,
            "unsubscribe_candidates": list(self.unsubscribe_candidates),
        }


@dataclass(frozen=True)
class EmailClassificationTask:
    task_id: str
    channel: str
    stable_message_identity: str
    status: str
    owner: str
    generation: int
    attempt_count: int
    lease_expires_at: str
    available_at: str
    input_json: str
    result_json: str
    error: str


class EmailClassificationTaskAdapter:
    """Dedicated Email queue, intentionally separate from generic reply tasks."""

    def __init__(
        self,
        email_store: EmailStore,
        *,
        now=lambda: datetime.now(timezone.utc),
        lease_seconds: int = 1_200,
        max_attempts: int = 3,
        retry_base_seconds: int = 2,
    ):
        self.email_store = email_store
        self._now = now
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def ensure_task(
        self, task_input: EmailClassificationTaskInput
    ) -> EmailClassificationTask:
        input_json = json.dumps(
            task_input.payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        task_id = (
            "email-classification:"
            + sha256(task_input.stable_message_identity.encode("utf-8")).hexdigest()
        )
        with self.email_store._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_agent_classification_tasks where stable_message_identity=?",
                (task_input.stable_message_identity,),
            ).fetchone()
            if row is None:
                db.execute(
                    """
                    insert into email_agent_classification_tasks (
                        task_id, channel, stable_message_identity, status, input_json
                    ) values (?, 'email', ?, 'pending', ?)
                    """,
                    (task_id, task_input.stable_message_identity, input_json),
                )
                row = db.execute(
                    "select * from email_agent_classification_tasks where task_id=?",
                    (task_id,),
                ).fetchone()
            elif row["input_json"] != input_json:
                raise ValueError("stable classification task input changed")
        assert row is not None
        return self._row(row)

    def has_stable_record(self, stable_message_identity: str) -> bool:
        with self.email_store._connect() as db:
            return (
                db.execute(
                    "select 1 from email_agent_classification_tasks where stable_message_identity=?",
                    (stable_message_identity,),
                ).fetchone()
                is not None
            )

    def stable_provider_uids(
        self, *, account_id: str, folder: str, uidvalidity: int
    ) -> frozenset[int]:
        """Return provider UIDs already protected by durable classifier state."""

        with self.email_store._connect() as db:
            rows = db.execute(
                """
                select json_extract(input_json, '$.provider_locator.uid') as uid
                from email_agent_classification_tasks
                where json_extract(input_json, '$.provider_locator.account_id')=?
                  and json_extract(input_json, '$.provider_locator.folder')=?
                  and json_extract(input_json, '$.provider_locator.uidvalidity')=?
                  and status in ('pending','running','done')
                """,
                (account_id, folder, uidvalidity),
            ).fetchall()
        return frozenset(int(row["uid"]) for row in rows)

    def get_task(self, task_id: str) -> EmailClassificationTask | None:
        with self.email_store._connect() as db:
            row = db.execute(
                "select * from email_agent_classification_tasks where task_id=?",
                (task_id,),
            ).fetchone()
        return None if row is None else self._row(row)

    def recover_running_tasks(self) -> int:
        """Retry eligible expired claims and terminalize exhausted claims."""

        with self.email_store._connect() as db:
            db.execute("begin immediate")
            return self._expire_running_claims(db, now=self._timestamp())

    def claim_next(self, *, owner: str) -> EmailClassificationTask | None:
        if not owner.strip():
            raise ValueError("owner must be nonblank")
        now = self._timestamp()
        lease_expires_at = self._timestamp(
            self._now() + timedelta(seconds=self.lease_seconds)
        )
        with self.email_store._connect() as db:
            db.execute("begin immediate")
            self._expire_running_claims(db, now=now)
            row = db.execute(
                """
                select * from email_agent_classification_tasks
                where status='pending' and attempt_count < ?
                  and (available_at='' or available_at <= ?)
                order by created_at, task_id limit 1
                """,
                (self.max_attempts, now),
            ).fetchone()
            if row is None:
                return None
            updated = db.execute(
                """
                update email_agent_classification_tasks
                set status='running', owner=?, generation=generation+1,
                    attempt_count=attempt_count+1, lease_expires_at=?, updated_at=?
                where task_id=? and status='pending'
                """,
                (owner.strip(), lease_expires_at, now, row["task_id"]),
            ).rowcount
            if updated != 1:
                return None
            claimed = db.execute(
                "select * from email_agent_classification_tasks where task_id=?",
                (row["task_id"],),
            ).fetchone()
        assert claimed is not None
        return self._row(claimed)

    def _expire_running_claims(self, db: sqlite3.Connection, *, now: str) -> int:
        exhausted = db.execute(
            """
            update email_agent_classification_tasks
            set status='failed', owner='', lease_expires_at='', available_at='',
                error='classification task lease expired after final attempt',
                updated_at=?
            where status='running' and lease_expires_at <= ?
              and attempt_count >= ?
            """,
            (now, now, self.max_attempts),
        ).rowcount
        retryable = db.execute(
            """
            update email_agent_classification_tasks
            set status='pending', owner='', lease_expires_at='', updated_at=?
            where status='running' and lease_expires_at <= ?
              and attempt_count < ?
            """,
            (now, now, self.max_attempts),
        ).rowcount
        return exhausted + retryable

    def complete(
        self, task: EmailClassificationTask, result: Mapping[str, object]
    ) -> None:
        self._finish(task, status="done", result=result, error="")

    def fail(
        self, task: EmailClassificationTask, *, error: str, retryable: bool
    ) -> None:
        retryable = retryable and task.attempt_count < self.max_attempts
        available_at = (
            self._timestamp(
                self._now()
                + timedelta(
                    seconds=self.retry_base_seconds
                    * (2 ** max(task.attempt_count - 1, 0))
                )
            )
            if retryable
            else ""
        )
        self._finish(
            task,
            status="pending" if retryable else "failed",
            result=None,
            error=error,
            available_at=available_at,
        )

    def _finish(
        self,
        task: EmailClassificationTask,
        *,
        status: str,
        result: Mapping[str, object] | None,
        error: str,
        available_at: str = "",
    ) -> None:
        result_json = (
            "null"
            if result is None
            else json.dumps(
                dict(result), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        with self.email_store._connect() as db:
            updated = db.execute(
                """
                update email_agent_classification_tasks
                set status=?, result_json=?, error=?, owner='', lease_expires_at='',
                    available_at=?, updated_at=?
                where task_id=? and status='running' and owner=? and generation=?
                """,
                (
                    status,
                    result_json,
                    error,
                    available_at,
                    self._timestamp(),
                    task.task_id,
                    task.owner,
                    task.generation,
                ),
            ).rowcount
            if updated != 1:
                raise ValueError("classification task lease changed")

    def _timestamp(self, value: datetime | None = None) -> str:
        candidate = value or self._now()
        return candidate.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _row(row: sqlite3.Row) -> EmailClassificationTask:
        return EmailClassificationTask(
            task_id=row["task_id"],
            channel=row["channel"],
            stable_message_identity=row["stable_message_identity"],
            status=row["status"],
            owner=row["owner"],
            generation=int(row["generation"]),
            attempt_count=int(row["attempt_count"]),
            lease_expires_at=row["lease_expires_at"],
            available_at=row["available_at"],
            input_json=row["input_json"],
            result_json=row["result_json"],
            error=row["error"],
        )


class EmailAgentTaskConflict(RuntimeError):
    """An existing queue identity is bound to different action metadata."""


class EmailAgentTaskMetadataError(ValueError):
    """Action metadata is unsafe for the durable task and Agent context."""


def _validated_unsubscribe_controls(
    value: object,
) -> tuple[UnsubscribeDiscoveredControl, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("unsubscribe continuation controls are incomplete")
    controls: list[UnsubscribeDiscoveredControl] = []
    references: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "reference",
            "kind",
            "intent",
        }:
            raise ValueError("unsubscribe continuation control fields are invalid")
        reference = item["reference"]
        kind = item["kind"]
        intent = item["intent"]
        if (
            not isinstance(reference, str)
            or not isinstance(kind, str)
            or not isinstance(intent, str)
            or not is_valid_unsubscribe_opaque_reference(reference)
            or kind not in _UNSUBSCRIBE_CONTROL_KINDS
            or intent not in _UNSUBSCRIBE_CONTROL_INTENTS
            or reference in references
        ):
            raise ValueError("unsubscribe continuation control is invalid")
        references.add(reference)
        controls.append(
            UnsubscribeDiscoveredControl(
                reference=reference,
                kind=kind,
                intent=intent,
            )
        )
    return tuple(controls)


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
                    _decode_metadata_token(key) if isinstance(key, str) else key
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
        candidate = _decode_metadata_token(token.strip("'\"()[]{}<>,.;"))
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
                is_sensitive_url_component_name(decoded_name)
                or is_sensitive_url_component_name(decoded_value)
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


def _contains_unsubscribe_url_like_text(value: str) -> bool:
    decoded = _decode_metadata_token(value)
    if any(marker in decoded.casefold() for marker in ("http:", "https:", "file:")):
        return True
    for token in decoded.split():
        candidate = token.strip("'\"()[]{}<>,.;")
        normalized = candidate.replace("\\", "/")
        if (
            normalized.startswith(("/", "//", "~/"))
            or "/" in normalized
            or "?" in normalized
            or "#" in normalized
            or urlsplit(normalized).scheme
        ):
            return True
    return False


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
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise EmailAgentTaskMetadataError(
            "email action metadata is not safe for persistence"
        ) from exc
    if _contains_forbidden_metadata(canonical):
        raise EmailAgentTaskMetadataError(
            "email action metadata is not safe for persistence"
        )


def _contains_raw_url_or_query_metadata(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_raw_url_or_query_metadata(key)
            or _contains_raw_url_or_query_metadata(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str):
        return isinstance(value, bytes | bytearray) or any(
            _contains_raw_url_or_query_metadata(item) for item in value
        )
    if not isinstance(value, str):
        return False
    for token in value.split():
        candidate = token.strip("'\"()[]{}<>,.;")
        parsed = urlsplit(candidate)
        if (
            parsed.scheme.casefold() in {"http", "https", "file", "mailto"}
            or (parsed.scheme and "://" in candidate)
            or parsed.query
            or parsed.fragment
        ):
            return True
    return False


def assert_safe_email_unsubscribe_metadata(value: object) -> None:
    """Reject private material from durable unsubscribe task metadata."""

    _assert_safe_email_metadata(value)
    try:
        canonical = _canonicalize_metadata(value)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise EmailAgentTaskMetadataError(
            "email action metadata is not safe for persistence"
        ) from exc
    if _contains_raw_url_or_query_metadata(canonical):
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
    list_unsubscribe: str = field(default="", repr=False)
    list_unsubscribe_post: str = field(default="", repr=False)
    body_text: str = field(default="", repr=False)
    body_html: str = field(default="", repr=False)
    unsubscribe_authentication: UnsubscribeAuthenticationEvidence | None = None
    unsubscribe_network_policy_reference: str = "network-policy:legacy"
    unsubscribe_network_policy_origin_references: tuple[str, ...] = (
        "network-origin:legacy",
    )
    unsubscribe_allow_loopback_for_tests: bool = False

    def __post_init__(self) -> None:
        if not self.stable_message_identity.strip():
            raise ValueError("stable_message_identity must be non-empty")
        if not self.thread_identity.strip():
            raise ValueError("thread_identity must be non-empty")
        object.__setattr__(self, "thread_identity", self.thread_identity.strip())
        if self.trigger.message_id != self.stable_message_identity:
            raise ValueError("trigger message must match stable_message_identity")
        if any(
            not isinstance(item, EmailThreadMessage) for item in self.thread_messages
        ):
            raise TypeError("thread_messages must contain EmailThreadMessage")
        if any(
            not isinstance(item, EmailAttachmentMetadata) for item in self.attachments
        ):
            raise TypeError("attachments must contain EmailAttachmentMetadata")
        if any(not isinstance(item, PriorReceipt) for item in self.prior_receipts):
            raise TypeError("prior_receipts must contain PriorReceipt")
        if self.unsubscribe_authentication is not None and not isinstance(
            self.unsubscribe_authentication,
            UnsubscribeAuthenticationEvidence,
        ):
            raise TypeError(
                "unsubscribe_authentication must be UnsubscribeAuthenticationEvidence"
            )
        if not self.unsubscribe_network_policy_reference.strip() or not all(
            isinstance(item, str) and item.strip()
            for item in self.unsubscribe_network_policy_origin_references
        ):
            raise ValueError("unsubscribe network policy references are invalid")


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


def accepted_email_reply_effect(
    task: ReplyTask,
    accepted_action: ProposedAction,
) -> EmailReplyEffect:
    """Freeze one exact reply proposal after Audit accepts it for execution."""

    try:
        metadata = json.loads(task.trigger_message_json)
    except json.JSONDecodeError as exc:
        raise ValueError("email task metadata is not valid JSON") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != _PAYLOAD_SCHEMA
        or metadata.get("action_type") != EmailAction.AUTO_REPLY.value
        or task.channel != "email"
        or task.trigger_message_id != metadata.get("action_identity")
        or accepted_action.capability != "email"
        or accepted_action.operation != "reply"
    ):
        raise ValueError("accepted action is not an automatic email reply")

    expected_target = {
        "action_identity": metadata.get("action_identity"),
        "account_id": metadata.get("account_id"),
        "stable_message_identity": metadata.get("stable_message_identity"),
        "thread_identity": metadata.get("thread_identity"),
    }
    if any(
        accepted_action.target.get(name) != expected
        for name, expected in expected_target.items()
    ):
        raise ValueError("accepted reply target does not match its email task")

    def required_text(source: Mapping[str, object], name: str) -> str:
        value = source.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("accepted email reply fields must be non-empty text")
        return value

    return EmailReplyEffect(
        action_identity=required_text(metadata, "action_identity"),
        action_plan_id=required_text(metadata, "action_plan_id"),
        action_plan_version=int(metadata["action_plan_version"]),
        classification_id=int(metadata["classification_id"]),
        account_id=required_text(accepted_action.target, "account_id"),
        stable_message_identity=required_text(
            accepted_action.target,
            "stable_message_identity",
        ),
        sender=required_text(accepted_action.target, "sender"),
        recipient=required_text(accepted_action.target, "recipient"),
        thread_identity=required_text(accepted_action.target, "thread_identity"),
        in_reply_to=required_text(accepted_action.payload, "in_reply_to"),
        subject=required_text(accepted_action.payload, "subject"),
        body=required_text(accepted_action.payload, "body"),
    )


def validate_unsubscribe_entry_operation_semantics(
    metadata: Mapping[str, object],
    *,
    entry_reference: str,
    operation: UnsubscribeOperation,
) -> None:
    """Validate the exact redacted entry semantics behind one root operation."""

    entries = metadata.get("unsubscribe_entries")
    if not isinstance(entries, list):
        raise ValueError("unsubscribe entries are invalid")
    entry = next(
        (
            item
            for item in entries
            if type(item) is dict
            and set(item) == {"index", "source", "digest", "reference"}
            and item.get("reference") == entry_reference
        ),
        None,
    )
    if entry is None or operation.target_reference != entry_reference:
        raise ValueError("unsubscribe entry operation is invalid")
    source = entry.get("source")
    index = entry.get("index")
    digest = entry.get("digest")
    if (
        not isinstance(source, str)
        or source not in _UNSUBSCRIBE_ENTRY_PRIORITIES
        or type(index) is not int
        or index < 0
        or not isinstance(digest, str)
        or entry_reference != f"unsubscribe-entry:{digest}"
    ):
        raise ValueError("unsubscribe entry semantics are invalid")
    allowed_sources = _UNSUBSCRIBE_ENTRY_OPERATION_SOURCES.get(operation.kind)
    if allowed_sources is None or source not in allowed_sources:
        raise ValueError("unsubscribe entry operation is not executable")
    if operation.kind is not UnsubscribeOperationKind.POST_ONE_CLICK:
        return
    authentication = metadata.get("unsubscribe_authentication")
    if (
        type(authentication) is not dict
        or set(authentication) != {"evidence_reference", "one_click_verified"}
        or type(authentication.get("one_click_verified")) is not bool
        or not isinstance(authentication.get("evidence_reference"), str)
    ):
        raise ValueError("one-click unsubscribe authentication is invalid")
    one_click_verified = authentication["one_click_verified"]
    evidence_reference = authentication["evidence_reference"]
    typed_authentication = UnsubscribeAuthenticationEvidence(
        dkim_covers_list_unsubscribe=one_click_verified,
        dkim_covers_list_unsubscribe_post=one_click_verified,
        evidence_reference=evidence_reference,
    )
    if (
        source != "header_one_click_https"
        or not typed_authentication.one_click_verified
    ):
        raise ValueError("one-click unsubscribe is not authenticated")


def accepted_email_unsubscribe_effect(
    task: ReplyTask,
    accepted_action: ProposedAction,
    *,
    continuation: EmailUnsubscribeContinuation | None = None,
) -> EmailUnsubscribeEffect:
    """Freeze one exact unsubscribe proposal after Audit accepts it."""

    try:
        _assert_safe_email_metadata(accepted_action.model_dump(mode="json"))
        if _contains_unsubscribe_url_like_text(accepted_action.description):
            raise EmailAgentTaskMetadataError(
                "email unsubscribe proposal contains URL-like text"
            )
    except EmailAgentTaskMetadataError as exc:
        raise ValueError("accepted unsubscribe proposal is not redacted") from exc
    try:
        metadata = json.loads(task.trigger_message_json)
    except json.JSONDecodeError as exc:
        raise ValueError("email task metadata is not valid JSON") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != _PAYLOAD_SCHEMA
        or metadata.get("action_type") != EmailAction.UNSUBSCRIBE.value
        or task.channel != "email"
        or task.trigger_message_id != metadata.get("action_identity")
        or accepted_action.capability != "email_browser"
        or accepted_action.operation != "unsubscribe"
    ):
        raise ValueError("accepted action is not an automatic email unsubscribe")

    expected_target = {
        "action_identity": metadata.get("action_identity"),
        "account_id": metadata.get("account_id"),
        "stable_message_identity": metadata.get("stable_message_identity"),
        "thread_identity": metadata.get("thread_identity"),
        "network_policy_reference": metadata.get(
            "unsubscribe_network_policy_reference"
        ),
        "network_policy_origin_references": metadata.get(
            "unsubscribe_network_policy_origin_references"
        ),
    }
    if set(accepted_action.target) != {*expected_target, "entry_reference"}:
        raise ValueError("accepted unsubscribe proposal is invalid")
    if any(
        accepted_action.target.get(name) != expected
        for name, expected in expected_target.items()
    ):
        raise ValueError("accepted unsubscribe target does not match its email task")

    entry_reference = accepted_action.target.get("entry_reference")
    if not isinstance(entry_reference, str):
        raise ValueError("accepted unsubscribe proposal is invalid")

    def required_text(source: Mapping[str, object], name: str) -> str:
        value = source.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("accepted unsubscribe fields must be non-empty text")
        return value

    if set(accepted_action.payload) != {"operations"}:
        raise ValueError("accepted unsubscribe payload must contain exact operations")
    operations_value = accepted_action.payload.get("operations")
    if (
        not isinstance(operations_value, Sequence)
        or isinstance(operations_value, str | bytes | bytearray)
        or not operations_value
        or any(not isinstance(item, Mapping) for item in operations_value)
    ):
        raise ValueError("accepted unsubscribe operations must be a non-empty list")
    try:
        operations = tuple(
            UnsubscribeOperation.from_mapping(item)
            for item in operations_value
            if isinstance(item, Mapping)
        )
        for operation in operations:
            if operation.kind.value in {"open_entry", "post_one_click"}:
                validate_unsubscribe_entry_operation_semantics(
                    metadata,
                    entry_reference=entry_reference,
                    operation=operation,
                )
        if continuation is None:
            if len(operations) != 1 or operations[0].kind.value not in {
                "open_entry",
                "post_one_click",
            }:
                raise ValueError(
                    "initial unsubscribe proposal must contain one entry operation"
                )
            previous_effect_digest = ""
        else:
            identity_fields = (
                "action_identity",
                "action_plan_id",
                "action_plan_version",
                "classification_id",
                "account_id",
                "stable_message_identity",
                "thread_identity",
                "entry_reference",
                "network_policy_reference",
            )
            candidate_values = {
                "action_identity": metadata.get("action_identity"),
                "action_plan_id": metadata.get("action_plan_id"),
                "action_plan_version": metadata.get("action_plan_version"),
                "classification_id": metadata.get("classification_id"),
                "account_id": accepted_action.target.get("account_id"),
                "stable_message_identity": accepted_action.target.get(
                    "stable_message_identity"
                ),
                "thread_identity": accepted_action.target.get("thread_identity"),
                "entry_reference": entry_reference,
                "network_policy_reference": accepted_action.target.get(
                    "network_policy_reference"
                ),
            }
            if (
                any(
                    candidate_values[field_name] != getattr(continuation, field_name)
                    for field_name in identity_fields
                )
                or tuple(
                    accepted_action.target.get("network_policy_origin_references", ())
                )
                != continuation.network_policy_origin_references
            ):
                raise ValueError("unsubscribe continuation identity changed")
            if (
                operations[:-1] != continuation.executed_operations
                or len(operations) != len(continuation.executed_operations) + 1
            ):
                raise ValueError("unsubscribe continuation prefix is not append-only")
            next_operation = operations[-1]
            control = next(
                (
                    item
                    for item in continuation.controls
                    if item.reference == next_operation.target_reference
                ),
                None,
            )
            allowed = {
                "form": {"submit_form"},
                "link": {"click_confirmation"},
                "button": {"click_confirmation"},
                "confirmation_email": {"confirm_email"},
                "email_otp": {"submit_form"},
                "captcha_handoff": {"click_confirmation", "reconcile_handoff"},
                "credential_handoff": {"reconcile_handoff"},
            }
            prior_operations = continuation.executed_operations
            if (
                control is None
                or next_operation.kind.value not in allowed[control.kind]
            ):
                raise ValueError("unsubscribe continuation control is invalid")
            captcha_attempted = any(
                item.kind.value == "click_confirmation"
                and item.target_reference == control.reference
                for item in prior_operations
            )
            if control.kind == "captcha_handoff" and (
                (
                    next_operation.kind.value == "click_confirmation"
                    and captcha_attempted
                )
                or (
                    next_operation.kind.value == "reconcile_handoff"
                    and not captcha_attempted
                )
            ):
                raise ValueError("unsubscribe CAPTCHA continuation stage is invalid")
            previous_effect_digest = continuation.effect_digest
        return EmailUnsubscribeEffect(
            action_identity=required_text(metadata, "action_identity"),
            action_plan_id=required_text(metadata, "action_plan_id"),
            action_plan_version=int(metadata["action_plan_version"]),
            classification_id=int(metadata["classification_id"]),
            account_id=required_text(accepted_action.target, "account_id"),
            stable_message_identity=required_text(
                accepted_action.target,
                "stable_message_identity",
            ),
            thread_identity=required_text(
                accepted_action.target,
                "thread_identity",
            ),
            entry_reference=required_text(
                accepted_action.target,
                "entry_reference",
            ),
            operations=operations,
            previous_effect_digest=previous_effect_digest,
            network_policy_reference=required_text(
                accepted_action.target,
                "network_policy_reference",
            ),
            network_policy_origin_references=tuple(
                str(item)
                for item in accepted_action.target["network_policy_origin_references"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("accepted unsubscribe proposal is invalid") from exc


def validated_email_unsubscribe_continuation(
    payload: Mapping[str, object],
    claim: Mapping[str, object],
    continuation: Mapping[str, object],
) -> EmailUnsubscribeContinuation:
    """Validate one durable continuation without exposing private browser state."""

    identity_fields = (
        "action_identity",
        "action_plan_id",
        "action_plan_version",
        "classification_id",
        "account_id",
        "stable_message_identity",
        "thread_identity",
    )
    try:
        if (
            payload.get("schema") != _PAYLOAD_SCHEMA
            or payload.get("lifecycle_version")
            != _ACTION_LIFECYCLE_VERSIONS[EmailAction.UNSUBSCRIBE]
            or payload.get("action_type") != EmailAction.UNSUBSCRIBE.value
            or claim.get("status") != "awaiting_audit"
            or any(claim.get(name) != payload.get(name) for name in identity_fields)
            or continuation.get("action_identity") != payload.get("action_identity")
            or continuation.get("effect_digest") != claim.get("effect_digest")
            or continuation.get("operations") != claim.get("operations")
        ):
            raise ValueError("unsubscribe continuation identity changed")
        entries = payload.get("unsubscribe_entries")
        entry_reference = claim.get("entry_reference")
        if (
            not isinstance(entries, list)
            or not isinstance(entry_reference, str)
            or not any(
                isinstance(item, Mapping) and item.get("reference") == entry_reference
                for item in entries
            )
        ):
            raise ValueError("unsubscribe continuation entry changed")
        operations_value = continuation.get("operations")
        controls_value = continuation.get("controls")
        origins_value = continuation.get("network_policy_origin_references")
        if (
            not isinstance(operations_value, list)
            or not operations_value
            or not isinstance(controls_value, list)
            or not controls_value
            or not isinstance(origins_value, list)
            or not origins_value
        ):
            raise ValueError("unsubscribe continuation evidence is incomplete")
        operations = tuple(
            UnsubscribeOperation.from_mapping(item)
            for item in operations_value
            if isinstance(item, Mapping)
        )
        controls = _validated_unsubscribe_controls(controls_value)
        if len(operations) != len(operations_value) or len(controls) != len(
            controls_value
        ):
            raise ValueError("unsubscribe continuation evidence is malformed")
        observation_reference = continuation.get("observation_reference")
        if not is_valid_unsubscribe_opaque_reference(observation_reference):
            raise ValueError("unsubscribe continuation observation is invalid")
        typed = EmailUnsubscribeContinuation(
            action_identity=str(payload["action_identity"]),
            action_plan_id=str(payload["action_plan_id"]),
            action_plan_version=int(payload["action_plan_version"]),
            classification_id=int(payload["classification_id"]),
            account_id=str(payload["account_id"]),
            stable_message_identity=str(payload["stable_message_identity"]),
            thread_identity=str(payload["thread_identity"]),
            entry_reference=entry_reference,
            effect_digest=str(continuation["effect_digest"]),
            previous_effect_digest=str(
                continuation.get("previous_effect_digest") or ""
            ),
            executed_operations=operations,
            controls=controls,
            network_policy_reference=str(continuation["network_policy_reference"]),
            network_policy_origin_references=tuple(str(item) for item in origins_value),
        )
        if typed.network_policy_reference != payload.get(
            "unsubscribe_network_policy_reference"
        ) or list(typed.network_policy_origin_references) != payload.get(
            "unsubscribe_network_policy_origin_references"
        ):
            raise ValueError("unsubscribe continuation network policy changed")
        effect = EmailUnsubscribeEffect(
            action_identity=typed.action_identity,
            action_plan_id=typed.action_plan_id,
            action_plan_version=typed.action_plan_version,
            classification_id=typed.classification_id,
            account_id=typed.account_id,
            stable_message_identity=typed.stable_message_identity,
            thread_identity=typed.thread_identity,
            entry_reference=typed.entry_reference,
            operations=typed.executed_operations,
            previous_effect_digest=typed.previous_effect_digest,
            network_policy_reference=typed.network_policy_reference,
            network_policy_origin_references=typed.network_policy_origin_references,
        )
        if effect.effect_digest != typed.effect_digest:
            raise ValueError("unsubscribe continuation digest changed")
        return typed
    except (KeyError, TypeError, ValueError) as exc:
        raise EmailAgentTaskMetadataError(
            "email unsubscribe continuation is invalid"
        ) from exc


def email_unsubscribe_continuation_receipt(
    continuation: EmailUnsubscribeContinuation,
) -> PriorReceipt:
    """Project one redacted durable continuation into Consumer context."""

    try:
        controls = _validated_unsubscribe_controls(
            [
                {
                    "reference": item.reference,
                    "kind": item.kind,
                    "intent": item.intent,
                }
                for item in continuation.controls
            ]
        )
    except (TypeError, ValueError) as exc:
        raise EmailAgentTaskMetadataError(
            "email unsubscribe continuation is invalid"
        ) from exc
    summary = json.dumps(
        {
            "accepted_operations": [
                {
                    "operation_reference": item.operation_reference,
                    "kind": item.kind.value,
                    "target_reference": item.target_reference,
                }
                for item in continuation.executed_operations
            ],
            "controls": [
                {
                    "reference": item.reference,
                    "kind": item.kind,
                    "intent": item.intent,
                }
                for item in controls
            ],
            "requires_human": continuation.requires_human,
            "instruction": (
                "The supported authentication attempt is exhausted; return "
                "needs_human with the opaque continuation reference."
                if continuation.requires_human
                else "Audit accepted the durable prefix; propose exactly one next "
                "operation from the listed opaque controls."
            ),
            "previous_effect_digest": continuation.effect_digest,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    receipt = PriorReceipt(
        receipt_id=("email-unsubscribe-continuation:" + continuation.effect_digest),
        operation="unsubscribe_continuation",
        summary=summary,
        completed=False,
    )
    _assert_safe_email_metadata(
        {
            "receipt_id": receipt.receipt_id,
            "operation": receipt.operation,
            "summary": receipt.summary,
            "completed": receipt.completed,
        }
    )
    return receipt


class EmailAgentTaskAdapter:
    """Create Email tasks while leaving execution and Audit to the existing runtime."""

    def __init__(
        self,
        store: AutoReplyStore,
        email_store: EmailStore,
        *,
        feature_registry: FeatureRegistry | None = None,
    ):
        if store.path.resolve() != email_store.path.resolve():
            raise ValueError(
                "email task and classification stores must share one database"
            )
        self.store = store
        self.email_store = email_store
        self.feature_registry = feature_registry or FeatureRegistry()

    def ensure_action_plan_tasks(
        self,
        action_plan: EmailActionPlan,
        task_input: EmailAgentTaskInput,
    ) -> tuple[EmailAgentTaskRoute, ...]:
        task_actions = tuple(
            action_type
            for action_type in action_plan.agent_actions
            if action_type is EmailAction.UNSUBSCRIBE
        )
        if not task_actions:
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
        for action_type in task_actions:
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
        if not self.feature_registry.feature_enabled("mail_review"):
            existing_tasks = [
                self.store.get_reply_task_for_message(
                    spec.conversation_id,
                    spec.trigger_message_id,
                    channel="email",
                )
                for _, _, spec in prepared
            ]
            return tuple(
                EmailAgentTaskRoute(
                    action_type=action_type,
                    task=task,
                    context=self._build_context(
                        task=task,
                        payload=payload,
                        task_input=task_input,
                    ),
                )
                for (action_type, payload, _), task in zip(
                    prepared, existing_tasks, strict=True
                )
                if task is not None
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
            "lifecycle_version": _ACTION_LIFECYCLE_VERSIONS[action_type],
            "account_id": action_plan.account_id,
            "stable_message_identity": task_input.stable_message_identity,
            "thread_identity": task_input.thread_identity,
            "action_identity": action_identity,
            "action_type": action_type.value,
            "action_plan_id": action_plan.action_plan_id,
            "action_plan_version": action_plan.action_plan_version,
            "classification_id": action_plan.classification_id,
            "category": action_plan.category,
            "classification_source": action_plan.classification_source,
            "confidence": action_plan.confidence,
            "model_id": action_plan.model_id,
            "config_version": action_plan.config_version,
            "action_parameters": parameters,
        }
        if action_type is EmailAction.UNSUBSCRIBE:
            if action_plan.category != "junk":
                raise EmailAgentTaskMetadataError(
                    "only junk may create an unsubscribe task"
                )
            entries = extract_unsubscribe_entries(
                list_unsubscribe=task_input.list_unsubscribe,
                list_unsubscribe_post=task_input.list_unsubscribe_post,
                body_text=task_input.body_text,
                body_html=task_input.body_html,
                authentication_evidence=task_input.unsubscribe_authentication,
                allow_loopback_for_tests=(
                    task_input.unsubscribe_allow_loopback_for_tests
                ),
            )
            entries = browser_unsubscribe_entries(
                entries,
                allow_loopback_for_tests=(
                    task_input.unsubscribe_allow_loopback_for_tests
                ),
                normalize_indexes=True,
            )
            if parameters:
                entries = (select_exact_unsubscribe_entry(entries, parameters),)
            if not entries:
                raise EmailAgentTaskMetadataError(
                    "email unsubscribe has no HTTPS browser candidate"
                )
            payload["unsubscribe_entries"] = [entry.redacted for entry in entries]
            evidence = task_input.unsubscribe_authentication
            payload["unsubscribe_authentication"] = (
                None
                if evidence is None
                else {
                    "evidence_reference": evidence.evidence_reference,
                    "one_click_verified": evidence.one_click_verified,
                }
            )
            payload["unsubscribe_network_policy_reference"] = (
                task_input.unsubscribe_network_policy_reference
            )
            payload["unsubscribe_network_policy_origin_references"] = list(
                task_input.unsubscribe_network_policy_origin_references
            )
        if action_type is EmailAction.UNSUBSCRIBE:
            assert_safe_email_unsubscribe_metadata(payload)
        else:
            _assert_safe_email_metadata(payload)
        return payload

    def _build_context(
        self,
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
        prior_receipts = task_input.prior_receipts
        if payload.get("action_type") == EmailAction.UNSUBSCRIBE.value:
            claim = self.email_store.get_email_unsubscribe_claim(
                task.trigger_message_id
            )
            continuation_value = self.email_store.get_email_unsubscribe_continuation(
                task.trigger_message_id
            )
            awaiting_audit = (
                claim is not None and claim.get("status") == "awaiting_audit"
            )
            if awaiting_audit != (continuation_value is not None):
                raise EmailAgentTaskMetadataError(
                    "email unsubscribe continuation is incomplete"
                )
            if awaiting_audit and claim is not None and continuation_value is not None:
                continuation = validated_email_unsubscribe_continuation(
                    payload,
                    claim,
                    continuation_value,
                )
                continuation_receipt = email_unsubscribe_continuation_receipt(
                    continuation
                )
                prior_receipts = tuple(
                    receipt
                    for receipt in prior_receipts
                    if receipt.operation != "unsubscribe_continuation"
                ) + (continuation_receipt,)
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
            prior_receipts=prior_receipts,
            trigger_raw_payload=payload,
            image_paths=(),
            image_sha256s=(),
        )
