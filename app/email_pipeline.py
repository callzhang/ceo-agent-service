"""Canonical email classification and feedback-to-ActionPlan decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailCategory,
    EmailClassificationStatus,
    build_email_action_plan,
)
from app.email_classifier_training import CategoryEligibility
from app.email_store import EmailFeedbackApplication, EmailStore


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
    feedback_request_id: str,
    expected_current_action_plan_id: str | None,
    now: datetime,
) -> EmailFeedbackApplication | None:
    return store.apply_human_classification(
        classification_id,
        category,
        feedback_request_id=feedback_request_id,
        expected_current_action_plan_id=expected_current_action_plan_id,
        created_at=now,
    )
