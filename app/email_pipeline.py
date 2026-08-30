"""Canonical email classification and feedback-to-ActionPlan decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailCategory,
    EmailClassificationStatus,
    build_email_action_plan,
    build_versioned_email_action_plan,
)
from app.email_classifier_training import CategoryEligibility
from app.email_store import EmailStore


@dataclass(frozen=True)
class EmailModelPrediction:
    category: EmailCategory
    confidence: float
    margin: float
    probabilities: Mapping[str, float]
    model_id: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if not 0.0 <= self.margin <= 1.0:
            raise ValueError("margin must be between zero and one")
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        object.__setattr__(
            self,
            "probabilities",
            {str(label): float(value) for label, value in self.probabilities.items()},
        )


@dataclass(frozen=True)
class EmailCategoryConfig:
    category: EmailCategory
    description: str
    threshold: float
    actions: tuple[EmailAction, ...]
    action_parameters: Mapping[EmailAction, Mapping[str, object]]
    enabled: bool
    config_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be between zero and one")
        if not self.config_version.strip():
            raise ValueError("config_version must be non-empty")
        unexpected = set(self.action_parameters) - set(self.actions)
        if unexpected:
            raise ValueError("action parameters require a configured action")
        object.__setattr__(
            self,
            "action_parameters",
            {
                action: dict(parameters)
                for action, parameters in self.action_parameters.items()
            },
        )


@dataclass(frozen=True)
class EmailClassificationDecision:
    category: EmailCategory
    confidence: float
    margin: float
    probabilities: Mapping[str, float]
    model_id: str
    config_version: str
    status: EmailClassificationStatus
    action_plan: EmailActionPlan | None


def decide_classification(
    prediction: EmailModelPrediction,
    category_config: EmailCategoryConfig,
    eligibility: CategoryEligibility,
    *,
    classification_id: int,
    account_id: str,
    created_at: datetime,
) -> EmailClassificationDecision:
    if prediction.category is not category_config.category:
        raise ValueError("prediction and category config must identify the same category")
    if eligibility.category is not prediction.category:
        raise ValueError("prediction and eligibility must identify the same category")
    if eligibility.configured_threshold != category_config.threshold:
        raise ValueError("eligibility threshold must match category configuration")

    automatic = (
        category_config.enabled
        and eligibility.auto_action_eligible
        and prediction.confidence >= category_config.threshold
    )
    status = (
        EmailClassificationStatus.PROCESSED
        if automatic
        else EmailClassificationStatus.PENDING_FEEDBACK
    )
    action_plan = None
    if automatic:
        action_plan = build_email_action_plan(
            classification_id=classification_id,
            account_id=account_id,
            category=prediction.category,
            classification_source="model",
            confidence=prediction.confidence,
            model_id=prediction.model_id,
            config_version=category_config.config_version,
            actions=category_config.actions,
            action_parameters=category_config.action_parameters,
            created_at=created_at,
        )
    return EmailClassificationDecision(
        category=prediction.category,
        confidence=prediction.confidence,
        margin=prediction.margin,
        probabilities=prediction.probabilities,
        model_id=prediction.model_id,
        config_version=category_config.config_version,
        status=status,
        action_plan=action_plan,
    )


def apply_human_confirmation(
    store: EmailStore,
    classification_id: int,
    category: EmailCategory,
    *,
    now: datetime,
) -> dict[str, object] | None:
    current = store.get_classification(classification_id)
    if current is None:
        return None
    if current["status"] == EmailClassificationStatus.PENDING_FEEDBACK.value:
        return store.confirm_classification(classification_id, category)

    current_plan = current.get("action_plan")
    if not isinstance(current_plan, dict):
        raise ValueError("processed classification has no current ActionPlan")
    category_config = _stored_category_config(store, category, current)
    version = int(current_plan["action_plan_version"]) + 1
    created_at = now.astimezone(timezone.utc)
    action_plan = build_versioned_email_action_plan(
        action_plan_version=version,
        classification_id=classification_id,
        account_id=str(current["account_id"]),
        category=category,
        classification_source="user",
        confidence=float(current["confidence"]),
        model_id=str(current["model_id"]),
        config_version=category_config.config_version,
        actions=category_config.actions,
        action_parameters=category_config.action_parameters,
        created_at=created_at,
    )
    return store.append_action_plan_version(
        classification_id,
        action_plan,
        confirmed_category=category,
    )


def _stored_category_config(
    store: EmailStore,
    category: EmailCategory,
    classification: Mapping[str, object],
) -> EmailCategoryConfig:
    selected = next(
        (
            config
            for config in store.list_configs()
            if config["category"] == category.value
        ),
        None,
    )
    if selected is None:
        return EmailCategoryConfig(
            category=category,
            description="",
            threshold=1.0,
            actions=(),
            action_parameters={},
            enabled=False,
            config_version=str(classification["config_version"]),
        )
    enabled = bool(selected["enabled"])
    actions = (
        tuple(EmailAction(value) for value in selected["actions"])
        if enabled
        else ()
    )
    parameters = (
        {
            EmailAction(action): dict(values)
            for action, values in selected["action_parameters"].items()
        }
        if enabled
        else {}
    )
    return EmailCategoryConfig(
        category=category,
        description=str(selected["description"]),
        threshold=float(selected["threshold"]),
        actions=actions,
        action_parameters=parameters,
        enabled=enabled,
        config_version=str(selected["config_version"]),
    )
