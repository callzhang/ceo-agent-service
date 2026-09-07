"""Read-only mailbox scan into the local email classifier store."""

from __future__ import annotations

import imaplib
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence

from app.email_classifier_contracts import (
    EmailAction,
    EmailAttachmentMetadata,
    EmailCategoryKey,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    INITIAL_EMAIL_CATEGORY_KEYS,
    validate_email_category_key,
)
from app.email_classifier_model import email_message_to_text
from app.email_classifier_training import CategoryEligibility
from app.email_provider_folders import FolderRole
from app.email_unsubscribe import extract_unsubscribe_entries
from app.email_imap_readonly import (
    ImapUidBatch,
    ephemeral_body_html,
    ephemeral_unsubscribe_authentication,
    fallback_stable_message_identity,
)
from app.email_pipeline import (
    EmailCategoryConfig,
    EmailModelPrediction,
    decide_classification,
)
from app.email_store import (
    EmailActionPlanConflict,
    EmailClassificationIdentityCollision,
    EmailCursorConflict,
    EmailPersistenceCorruption,
    EmailStore,
)


class PredictionLike(Protocol):
    label: str
    probability: float
    margin: float
    probabilities: Mapping[str, float]
    model_version: str


class MessageClassifier(Protocol):
    def predict_message(self, message: Mapping[str, object]) -> PredictionLike: ...


@dataclass(frozen=True)
class AgentScanContext:
    allowed_category_keys: tuple[str, ...]
    category_descriptions: Mapping[str, object]
    folder_targets: Mapping[str, str]
    config_version: str

    def __post_init__(self) -> None:
        categories = tuple(
            validate_email_category_key(key) for key in self.allowed_category_keys
        )
        if not categories or len(categories) != len(set(categories)):
            raise ValueError("allowed category keys must be unique and nonempty")
        if set(self.category_descriptions) != set(categories):
            raise ValueError("category descriptions must cover allowed categories")
        if not self.config_version.strip():
            raise ValueError("config_version must be nonblank")


def should_enqueue_agent_classification(
    *,
    provider_unread: bool,
    folder_role: FolderRole,
    configured_unclassified_source: bool,
    has_stable_record: bool,
) -> bool:
    """Apply the complete cold-start gate without any message-date condition."""

    if type(provider_unread) is not bool:
        raise TypeError("provider_unread must be a strict bool")
    if type(folder_role) is not FolderRole:
        raise TypeError("folder_role must be a FolderRole")
    source_is_eligible = folder_role is FolderRole.INBOX or (
        folder_role is FolderRole.UNBOUND and configured_unclassified_source
    )
    return provider_unread and source_is_eligible and not has_stable_record


def scan_agent_classification_batch(
    source: object,
    store: object,
    task_producer: object,
    context: AgentScanContext,
    *,
    mailbox: str = "INBOX",
    folder_role: FolderRole,
    configured_unclassified_source: bool,
    limit: int = 50,
) -> EmailScanResult:
    """Observe provider state and enqueue Agent work without model inference."""

    cursor = store.get_scan_cursor(_source_account_id(source), mailbox)
    cursor_uidvalidity = None if cursor is None else int(cursor["uidvalidity"])
    last_seen_uid = 0 if cursor is None else int(cursor["last_seen_uid"])
    stable_provider_uids = getattr(task_producer.adapter, "stable_provider_uids", None)
    excluded_uids = (
        stable_provider_uids(
            account_id=_source_account_id(source),
            folder=mailbox,
            uidvalidity=cursor_uidvalidity,
        )
        if cursor_uidvalidity is not None and callable(stable_provider_uids)
        else frozenset()
    )
    stable_classification_uids = getattr(store, "stable_classification_uids", None)
    if cursor_uidvalidity is not None and callable(stable_classification_uids):
        excluded_uids = excluded_uids | stable_classification_uids(
            account_id=_source_account_id(source),
            folder=mailbox,
            uidvalidity=cursor_uidvalidity,
        )
    batch = source.fetch_uid_batch(
        mailbox,
        cursor_uidvalidity=cursor_uidvalidity,
        last_seen_uid=last_seen_uid,
        limit=limit,
        unread_only=True,
        excluded_uids=excluded_uids,
    )
    if not isinstance(batch, ImapUidBatch):
        raise TypeError("fetch_uid_batch must return ImapUidBatch")
    enqueued = 0
    highest_uid = last_seen_uid
    for message in batch.messages:
        locator = _provider_locator(message)
        stable_identity = _stable_message_identity(message, locator)
        highest_uid = max(highest_uid, locator.uid)
        has_record = task_producer.adapter.has_stable_record(stable_identity)
        has_canonical = getattr(store, "has_stable_classification", None)
        if callable(has_canonical):
            has_record = has_record or has_canonical(stable_identity)
        if not should_enqueue_agent_classification(
            provider_unread=message.get("providerUnread"),
            folder_role=folder_role,
            configured_unclassified_source=configured_unclassified_source,
            has_stable_record=has_record,
        ):
            continue
        body_html = ephemeral_body_html(message)
        entries = extract_unsubscribe_entries(
            list_unsubscribe=str(message.get("listUnsubscribe") or ""),
            list_unsubscribe_post=str(message.get("listUnsubscribePost") or ""),
            body_text=str(message.get("markdownBody") or message.get("textBody") or ""),
            body_html=str(body_html),
            authentication_evidence=ephemeral_unsubscribe_authentication(message),
        )
        task_producer.produce(
            message,
            allowed_category_keys=context.allowed_category_keys,
            category_descriptions=context.category_descriptions,
            folder_targets=context.folder_targets,
            config_version=context.config_version,
            unsubscribe_candidates=entries,
        )
        enqueued += 1
    uidvalidity = batch.uidvalidity
    record_cursor = getattr(store, "record_scan_cursor", None)
    if callable(record_cursor):
        record_cursor(
            account_id=batch.account_id,
            folder=batch.folder,
            uidvalidity=uidvalidity,
            last_seen_uid=highest_uid,
            expected_uidvalidity=(
                cursor_uidvalidity
                if cursor_uidvalidity is not None and cursor_uidvalidity != uidvalidity
                else None
            ),
        )
    return EmailScanResult(len(batch.messages), enqueued, 0, 0)


@dataclass(frozen=True)
class EmailScanConfig:
    config_version: str
    thresholds: Mapping[EmailCategoryKey, float]
    actions: Mapping[EmailCategoryKey, tuple[EmailAction, ...]]
    category_eligibility: Mapping[EmailCategoryKey, CategoryEligibility] = field(
        default_factory=dict
    )
    action_parameters: Mapping[
        EmailCategoryKey, Mapping[EmailAction, Mapping[str, object]]
    ] = field(default_factory=dict)
    category_enabled: Mapping[EmailCategoryKey, bool] = field(default_factory=dict)

    @classmethod
    def cold_start(
        cls, *, config_version: str = "email-cold-start-v1"
    ) -> "EmailScanConfig":
        """Conservative defaults for the review-only validation phase."""
        return cls(
            config_version=config_version,
            thresholds={category: 0.95 for category in INITIAL_EMAIL_CATEGORY_KEYS},
            actions={},
            category_eligibility={
                category: CategoryEligibility(
                    category=category,
                    configured_threshold=0.95,
                    validated_precision=None,
                    validation_sample_count=0,
                    auto_action_eligible=False,
                    reason="cold_start",
                )
                for category in INITIAL_EMAIL_CATEGORY_KEYS
            },
            action_parameters={},
            category_enabled={
                category: True for category in INITIAL_EMAIL_CATEGORY_KEYS
            },
        )

    def __post_init__(self) -> None:
        if not self.config_version.strip():
            raise ValueError("config_version must be non-empty")
        current_categories = set(INITIAL_EMAIL_CATEGORY_KEYS)
        thresholds = {
            validate_email_category_key(category): threshold
            for category, threshold in self.thresholds.items()
        }
        if set(thresholds) != current_categories:
            raise ValueError("thresholds must cover every current email category")
        for category in INITIAL_EMAIL_CATEGORY_KEYS:
            threshold = thresholds.get(category)
            if threshold is None or not 0 <= threshold <= 1:
                raise ValueError(f"missing or invalid threshold for {category}")
        actions = {
            validate_email_category_key(category): tuple(category_actions)
            for category, category_actions in self.actions.items()
        }
        if not set(actions) <= current_categories:
            raise ValueError("actions contain a non-current email category")
        eligibility = {
            validate_email_category_key(category): value
            for category, value in self.category_eligibility.items()
        }
        if not eligibility:
            eligibility = {
                category: CategoryEligibility(
                    category=category,
                    configured_threshold=thresholds[category],
                    validated_precision=None,
                    validation_sample_count=0,
                    auto_action_eligible=False,
                    reason="eligibility_not_provided",
                )
                for category in INITIAL_EMAIL_CATEGORY_KEYS
            }
        if set(eligibility) != current_categories:
            raise ValueError("category_eligibility must cover every email category")
        for category, category_eligibility in eligibility.items():
            if category_eligibility.category != category:
                raise ValueError("category_eligibility category keys must match values")
            if category_eligibility.configured_threshold != thresholds[category]:
                raise ValueError(
                    "category eligibility threshold must match scan threshold"
                )
        action_parameters = {
            validate_email_category_key(category): parameters
            for category, parameters in self.action_parameters.items()
        }
        if not set(action_parameters) <= current_categories:
            raise ValueError("action parameters contain a non-current email category")
        unexpected_categories = set(action_parameters) - set(actions)
        if unexpected_categories:
            raise ValueError("action parameters contain a category with no actions")
        if any(
            EmailAction.AUTO_REPLY in category_actions
            for category_actions in actions.values()
        ):
            raise ValueError("auto_reply is disabled for email scanning")
        category_enabled = {
            validate_email_category_key(category): enabled
            for category, enabled in self.category_enabled.items()
        }
        if not category_enabled:
            category_enabled = {
                category: True for category in INITIAL_EMAIL_CATEGORY_KEYS
            }
        if set(category_enabled) != current_categories:
            raise ValueError("category_enabled must cover every email category")
        object.__setattr__(self, "thresholds", MappingProxyType(thresholds))
        object.__setattr__(self, "actions", MappingProxyType(actions))
        object.__setattr__(
            self,
            "category_eligibility",
            MappingProxyType(eligibility),
        )
        object.__setattr__(
            self,
            "category_enabled",
            MappingProxyType(category_enabled),
        )
        object.__setattr__(
            self,
            "action_parameters",
            MappingProxyType(action_parameters),
        )


@dataclass(frozen=True)
class EmailScanResult:
    fetched_count: int
    persisted_count: int
    processed_count: int
    pending_feedback_count: int


@dataclass(frozen=True)
class EmailFolderScanResult:
    folder: str
    fetched_count: int = 0
    persisted_count: int = 0
    processed_count: int = 0
    pending_feedback_count: int = 0
    error_code: str = ""


@dataclass(frozen=True)
class EmailAccountScanResult:
    account_id: str
    folders: tuple[EmailFolderScanResult, ...] = ()
    error_code: str = ""


@dataclass(frozen=True)
class EmailAccountsScanResult:
    accounts: tuple[EmailAccountScanResult, ...]

    @property
    def persisted_count(self) -> int:
        return sum(
            folder.persisted_count
            for account in self.accounts
            for folder in account.folders
        )


def scan_readonly_batch(
    source: object,
    classifier: MessageClassifier,
    store: EmailStore,
    config: EmailScanConfig,
    *,
    mailbox: str = "INBOX",
    limit: int = 50,
    task_producer: Callable[[EmailClassification, Mapping[str, object]], object]
    | None = None,
) -> EmailScanResult:
    if limit <= 0:
        raise ValueError("limit must be positive")
    fetch_uid_batch = getattr(source, "fetch_uid_batch", None)
    if callable(fetch_uid_batch):
        account_id = _source_account_id(source)
        cursor = store.get_scan_cursor(account_id, mailbox)
        cursor_uidvalidity = None if cursor is None else int(cursor["uidvalidity"])
        last_seen_uid = 0 if cursor is None else int(cursor["last_seen_uid"])
        batch = fetch_uid_batch(
            mailbox,
            cursor_uidvalidity=cursor_uidvalidity,
            last_seen_uid=last_seen_uid,
            limit=limit,
        )
    else:
        fetch_recent = getattr(source, "fetch_recent", None)
        if not callable(fetch_recent):
            raise TypeError("source must provide fetch_uid_batch")
        legacy_messages = fetch_recent(mailbox, limit=limit)
        if not legacy_messages:
            return EmailScanResult(0, 0, 0, 0)
        account_id = str(legacy_messages[0].get("accountId") or "").strip()
        uidvalidity = _positive_int(
            legacy_messages[0].get("uidValidity"), "uidValidity"
        )
        cursor = store.get_scan_cursor(account_id, mailbox)
        cursor_uidvalidity = None if cursor is None else int(cursor["uidvalidity"])
        batch = ImapUidBatch(
            account_id=account_id,
            folder=mailbox,
            uidvalidity=uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=legacy_messages,
        )
    if not isinstance(batch, ImapUidBatch):
        raise TypeError("fetch_uid_batch must return ImapUidBatch")
    if batch.account_id != account_id or batch.folder != mailbox:
        raise ValueError(
            "IMAP batch identity does not match requested account and folder"
        )
    messages = batch.messages
    if not messages:
        reset_expectation = (
            cursor_uidvalidity
            if cursor_uidvalidity is not None
            and cursor_uidvalidity != batch.uidvalidity
            else None
        )
        store.persist_empty_scan_cursor(
            account_id=batch.account_id,
            folder=batch.folder,
            uidvalidity=batch.uidvalidity,
            last_success_at=datetime.now(timezone.utc).isoformat(),
            expected_cursor_uidvalidity=reset_expectation,
        )
        return EmailScanResult(0, 0, 0, 0)
    persisted = 0
    processed = 0
    pending = 0
    reset_expectation = (
        cursor_uidvalidity
        if cursor_uidvalidity is not None and cursor_uidvalidity != batch.uidvalidity
        else None
    )
    for message in messages:
        prediction = classifier.predict_message(message)
        category = validate_email_category_key(prediction.label)
        if category not in INITIAL_EMAIL_CATEGORY_KEYS:
            raise ValueError(f"category is not enabled for live scanning: {category}")
        threshold = config.thresholds[category]
        actions = config.actions.get(category, ())
        action_parameters = config.action_parameters.get(category, {})
        locator = _provider_locator(message)
        if (
            locator.account_id != batch.account_id
            or locator.folder != batch.folder
            or locator.uidvalidity != batch.uidvalidity
        ):
            raise ValueError("message locator does not match its IMAP batch")
        stable_message_identity = _stable_message_identity(message, locator)
        classification_id = _classification_id(stable_message_identity)
        model_id = str(prediction.model_version).strip()
        created_at = datetime.now(timezone.utc)
        decision = decide_classification(
            EmailModelPrediction(
                category=category,
                confidence=float(prediction.probability),
                margin=float(prediction.margin),
                probabilities={
                    str(key): float(value)
                    for key, value in prediction.probabilities.items()
                },
                model_id=model_id,
            ),
            EmailCategoryConfig(
                category=category,
                description="",
                threshold=threshold,
                actions=actions,
                action_parameters=action_parameters,
                enabled=config.category_enabled[category],
                config_version=config.config_version,
            ),
            config.category_eligibility[category],
            classification_id=classification_id,
            account_id=locator.account_id,
            created_at=created_at,
        )
        classification = EmailClassification(
            classification_id=classification_id,
            stable_message_identity=stable_message_identity,
            provider_locator=locator,
            category=category,
            confidence=decision.confidence,
            margin=decision.margin,
            probabilities=dict(decision.probabilities),
            model_id=decision.model_id,
            config_version=decision.config_version,
            status=decision.status,
            classification_source="model",
            action_plan=decision.action_plan,
        )
        sender = message.get("from") or {}
        sender_value = (
            str(sender.get("email") or sender.get("name") or "")
            if isinstance(sender, Mapping)
            else ""
        )
        recipients = _recipient_values(message)
        attachments = _attachment_values(message)
        model_text = email_message_to_text(message)
        persistence = {
            "sender": sender_value,
            "recipients": recipients,
            "subject": str(message.get("subject") or ""),
            "normalized_text": _normalized_message_text(message),
            "preview": _redacted_preview(message),
            "attachment_metadata": attachments,
            "in_reply_to": str(message.get("inReplyTo") or ""),
            "references": _message_id_values(message.get("references")),
            "received_at": str(message.get("date") or message.get("received_at") or ""),
            "model_text": model_text,
        }
        produces_agent_task = (
            task_producer is not None
            and classification.action_plan is not None
            and bool(classification.action_plan.agent_actions)
        )
        store.persist_scan_result(
            classification,
            **persistence,
            **(
                {}
                if produces_agent_task
                else {
                    "cursor_uidvalidity": batch.uidvalidity,
                    "cursor_last_seen_uid": locator.uid,
                    "cursor_last_success_at": created_at.isoformat(),
                    "expected_cursor_uidvalidity": reset_expectation,
                }
            ),
        )
        if produces_agent_task:
            assert task_producer is not None
            task_producer(classification, message)
            store.persist_scan_result(
                classification,
                **persistence,
                cursor_uidvalidity=batch.uidvalidity,
                cursor_last_seen_uid=locator.uid,
                cursor_last_success_at=created_at.isoformat(),
                expected_cursor_uidvalidity=reset_expectation,
            )
        reset_expectation = None
        persisted += 1
        if decision.status is EmailClassificationStatus.PROCESSED:
            processed += 1
        else:
            pending += 1
    return EmailScanResult(len(messages), persisted, processed, pending)


def scan_imap_accounts(
    accounts: Sequence[Mapping[str, object]],
    source_factory: Callable[[Mapping[str, object]], object],
    classifier: MessageClassifier,
    store: EmailStore,
    config: EmailScanConfig,
    *,
    limit: int = 50,
    task_producer: Callable[[EmailClassification, Mapping[str, object]], object]
    | None = None,
) -> EmailAccountsScanResult:
    """Scan enabled accounts independently and expose only sanitized outcomes."""

    outcomes: list[EmailAccountScanResult] = []
    for account in accounts:
        if not bool(account.get("enabled", True)):
            continue
        account_id = str(account.get("account_id") or "").strip()
        if not account_id:
            raise ValueError("account_id must be non-empty")
        try:
            source = source_factory(account)
        except (ConnectionError, OSError, imaplib.IMAP4.error):
            outcomes.append(
                EmailAccountScanResult(
                    account_id=account_id,
                    error_code="connection_failed",
                )
            )
            continue
        folder_outcomes: list[EmailFolderScanResult] = []
        try:
            folders = account.get("scan_folders") or ()
            if not isinstance(folders, Sequence) or isinstance(folders, str | bytes):
                raise ValueError("scan_folders must be a sequence")
            for folder_value in folders:
                folder = str(folder_value).strip()
                try:
                    result = scan_readonly_batch(
                        source,
                        classifier,
                        store,
                        config,
                        mailbox=folder,
                        limit=limit,
                        task_producer=task_producer,
                    )
                except (
                    EmailPersistenceCorruption,
                    EmailClassificationIdentityCollision,
                    EmailActionPlanConflict,
                    EmailCursorConflict,
                    sqlite3.DatabaseError,
                ):
                    raise
                except Exception:
                    folder_outcomes.append(
                        EmailFolderScanResult(
                            folder=folder,
                            error_code="scan_failed",
                        )
                    )
                    continue
                folder_outcomes.append(
                    EmailFolderScanResult(
                        folder=folder,
                        fetched_count=result.fetched_count,
                        persisted_count=result.persisted_count,
                        processed_count=result.processed_count,
                        pending_feedback_count=result.pending_feedback_count,
                    )
                )
        finally:
            _close_source(source)
        outcomes.append(
            EmailAccountScanResult(
                account_id=account_id,
                folders=tuple(folder_outcomes),
            )
        )
    return EmailAccountsScanResult(accounts=tuple(outcomes))


def _provider_locator(message: Mapping[str, object]) -> EmailProviderLocator:
    return EmailProviderLocator(
        account_id=str(message.get("accountId") or ""),
        folder=str(message.get("folder") or ""),
        uidvalidity=_positive_int(message.get("uidValidity"), "uidValidity"),
        uid=_positive_int(message.get("uid"), "uid"),
        rfc_message_id=message.get("messageId"),
        thread_id=message.get("threadId"),
    )


def _stable_message_identity(
    message: Mapping[str, object], locator: EmailProviderLocator
) -> str:
    if locator.rfc_message_id is not None:
        return locator.stable_message_identity
    existing = message.get("stableMessageIdentity")
    if existing is None:
        return fallback_stable_message_identity(
            message,
            account_id=locator.account_id,
        )
    if not isinstance(existing, str) or not existing.strip():
        raise ValueError("stableMessageIdentity must be a non-blank string")
    stable_message_identity = existing.strip()
    if not stable_message_identity.startswith(f"{locator.account_id}:"):
        raise ValueError("stableMessageIdentity must be scoped to accountId")
    return stable_message_identity


def _source_account_id(source: object) -> str:
    account_id = getattr(source, "account_id", None)
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError("source must expose a non-empty account_id")
    return account_id.strip()


def _recipient_values(message: Mapping[str, object]) -> tuple[str, ...]:
    recipients: list[str] = []
    for recipient_field in ("toRecipients", "ccRecipients"):
        values = message.get(recipient_field) or ()
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            raise ValueError(f"{recipient_field} must be a sequence")
        for item in values:
            if not isinstance(item, Mapping):
                raise ValueError(f"{recipient_field} entries must be mappings")
            value = str(item.get("email") or item.get("name") or "").strip()
            if value:
                recipients.append(value)
    return tuple(recipients)


def _attachment_values(
    message: Mapping[str, object],
) -> tuple[EmailAttachmentMetadata, ...]:
    values = message.get("attachments") or ()
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise ValueError("attachments must be a sequence")
    return tuple(EmailAttachmentMetadata.model_validate(item) for item in values)


def _message_id_values(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Sequence) or isinstance(value, bytes | bytearray):
        raise ValueError("references must be text or a sequence")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("references must contain text values")
    return tuple(value)


def _normalized_message_text(message: Mapping[str, object]) -> str:
    sender = message.get("from") or {}
    sender_value = ""
    if isinstance(sender, Mapping):
        sender_value = str(sender.get("email") or sender.get("name") or "").strip()
    to_values = _named_recipients(message, "toRecipients")
    cc_values = _named_recipients(message, "ccRecipients")
    subject = str(message.get("subject") or "").strip()
    body = str(message.get("markdownBody") or message.get("textBody") or "").strip()
    return "\n".join(
        (
            f"From: {sender_value}",
            f"To: {', '.join(to_values)}",
            f"Cc: {', '.join(cc_values)}",
            f"Subject: {subject}",
            "",
            body,
        )
    ).strip()


def _named_recipients(message: Mapping[str, object], field: str) -> tuple[str, ...]:
    values = message.get(field) or ()
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise ValueError(f"{field} must be a sequence")
    return tuple(
        str(item.get("email") or item.get("name") or "").strip()
        for item in values
        if isinstance(item, Mapping)
        and str(item.get("email") or item.get("name") or "").strip()
    )


def _close_source(source: object) -> None:
    logout = getattr(source, "logout", None)
    if callable(logout):
        try:
            logout()
        except Exception:
            shutdown = getattr(source, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"{field} must be a positive integer")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _classification_id(stable_message_identity: str) -> int:
    value = int.from_bytes(
        sha256(stable_message_identity.encode("utf-8")).digest()[:8], "big"
    ) & ((1 << 63) - 1)
    return value or 1


def _redacted_preview(message: Mapping[str, object], *, limit: int = 280) -> str:
    body = str(message.get("markdownBody") or message.get("textBody") or "")
    subject = str(message.get("subject") or "")
    text = " ".join(value for value in (subject, body) if value).strip()
    text = re.sub(r"https?://\S+", " URL ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\b", " EMAIL ", text)
    text = re.sub(r"(?<!\d)\d{4,}(?!\d)", " NUMBER ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]
