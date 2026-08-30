"""Read-only mailbox scan into the local email classifier store."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Mapping, Protocol

from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
)
from app.email_classifier_model import email_message_to_text
from app.email_store import EmailStore


class PredictionLike(Protocol):
    label: str
    probability: float
    margin: float
    probabilities: Mapping[str, float]
    model_version: str


class MessageClassifier(Protocol):
    def predict_message(self, message: Mapping[str, object]) -> PredictionLike: ...


@dataclass(frozen=True)
class EmailScanConfig:
    config_version: str
    thresholds: Mapping[EmailCategory, float]
    actions: Mapping[EmailCategory, tuple[EmailAction, ...]]
    action_parameters: Mapping[
        EmailCategory, Mapping[EmailAction, Mapping[str, object]]
    ] = field(default_factory=dict)

    @classmethod
    def cold_start(cls, *, config_version: str = "email-cold-start-v1") -> "EmailScanConfig":
        """Conservative defaults for the review-only validation phase."""
        return cls(
            config_version=config_version,
            thresholds={category: 0.95 for category in EmailCategory},
            actions={},
            action_parameters={},
        )

    def __post_init__(self) -> None:
        if not self.config_version.strip():
            raise ValueError("config_version must be non-empty")
        for category in EmailCategory:
            threshold = self.thresholds.get(category)
            if threshold is None or not 0 <= threshold <= 1:
                raise ValueError(f"missing or invalid threshold for {category.value}")
        unexpected_categories = set(self.action_parameters) - set(self.actions)
        if unexpected_categories:
            raise ValueError("action parameters contain a category with no actions")


@dataclass(frozen=True)
class EmailScanResult:
    fetched_count: int
    persisted_count: int
    processed_count: int
    pending_feedback_count: int


def scan_readonly_batch(
    source: object,
    classifier: MessageClassifier,
    store: EmailStore,
    config: EmailScanConfig,
    *,
    mailbox: str = "INBOX",
    limit: int = 50,
) -> EmailScanResult:
    if limit <= 0:
        raise ValueError("limit must be positive")
    fetch_recent = getattr(source, "fetch_recent", None)
    if not callable(fetch_recent):
        raise TypeError("source must provide fetch_recent")
    messages = fetch_recent(mailbox, limit=limit)
    persisted = 0
    processed = 0
    pending = 0
    for message in messages:
        prediction = classifier.predict_message(message)
        category = EmailCategory(str(prediction.label))
        threshold = config.thresholds[category]
        eligible = float(prediction.probability) >= threshold
        status = (
            EmailClassificationStatus.PROCESSED
            if eligible
            else EmailClassificationStatus.PENDING_FEEDBACK
        )
        actions = config.actions.get(category, ())
        action_parameters = config.action_parameters.get(category, {})
        locator = _provider_locator(message)
        classification_id = _classification_id(locator.stable_message_identity)
        model_id = str(prediction.model_version).strip()
        created_at = datetime.now(timezone.utc)
        action_plan = None
        if status is EmailClassificationStatus.PROCESSED:
            action_plan = EmailActionPlan(
                action_plan_id=_action_plan_id(
                    classification_id=classification_id,
                    account_id=locator.account_id,
                    category=category,
                    model_id=model_id,
                    config_version=config.config_version,
                    actions=actions,
                    action_parameters=action_parameters,
                ),
                action_plan_version=1,
                classification_id=classification_id,
                account_id=locator.account_id,
                category=category,
                classification_source="model",
                confidence=float(prediction.probability),
                model_id=model_id,
                config_version=config.config_version,
                actions=actions,
                action_parameters={
                    action: dict(parameters)
                    for action, parameters in action_parameters.items()
                },
                created_at=created_at,
            )
        classification = EmailClassification(
            classification_id=classification_id,
            provider_locator=locator,
            category=category,
            confidence=float(prediction.probability),
            margin=float(prediction.margin),
            probabilities={str(key): float(value) for key, value in prediction.probabilities.items()},
            model_id=model_id,
            config_version=config.config_version,
            status=status,
            classification_source="model",
            action_plan=action_plan,
        )
        sender = message.get("from") or {}
        sender_value = str(sender.get("email") or sender.get("name") or "") if isinstance(sender, Mapping) else ""
        store.upsert_classification(
            classification,
            sender=sender_value,
            subject=str(message.get("subject") or ""),
            preview=_redacted_preview(message),
            model_text=email_message_to_text(message),
            received_at=str(message.get("date") or message.get("received_at") or ""),
        )
        persisted += 1
        if eligible:
            processed += 1
        else:
            pending += 1
    return EmailScanResult(len(messages), persisted, processed, pending)


def _provider_locator(message: Mapping[str, object]) -> EmailProviderLocator:
    return EmailProviderLocator(
        account_id=str(message.get("accountId") or ""),
        folder=str(message.get("folder") or ""),
        uidvalidity=_positive_int(message.get("uidValidity"), "uidValidity"),
        uid=_positive_int(message.get("uid"), "uid"),
        rfc_message_id=message.get("messageId"),
        thread_id=message.get("threadId"),
    )


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
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


def _action_plan_id(
    *,
    classification_id: int,
    account_id: str,
    category: EmailCategory,
    model_id: str,
    config_version: str,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
) -> str:
    snapshot = json.dumps(
        {
            "classification_id": classification_id,
            "account_id": account_id,
            "category": category.value,
            "model_id": model_id,
            "config_version": config_version,
            "actions": [action.value for action in actions],
            "action_parameters": {
                action.value: dict(parameters)
                for action, parameters in action_parameters.items()
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(snapshot.encode("utf-8")).hexdigest()
    return f"email-action-plan:{digest}"


def _redacted_preview(message: Mapping[str, object], *, limit: int = 280) -> str:
    body = str(message.get("markdownBody") or message.get("textBody") or "")
    subject = str(message.get("subject") or "")
    text = " ".join(value for value in (subject, body) if value).strip()
    text = re.sub(r"https?://\S+", " URL ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\b", " EMAIL ", text)
    text = re.sub(r"(?<!\d)\d{4,}(?!\d)", " NUMBER ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]
