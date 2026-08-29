"""Local feedback training and atomic review-only model promotion."""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.email_classifier_contracts import EmailCategory
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_store import EmailStore


class TrainingNotReady(ValueError):
    """Confirmed feedback is insufficient for a candidate model."""


@dataclass(frozen=True)
class TrainingReadiness:
    ready: bool
    example_count: int
    category_counts: dict[str, int]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CategoryValidation:
    validated_precision: float | None
    validation_sample_count: int


@dataclass(frozen=True)
class EligibilityRequirement:
    configured_threshold: float
    minimum_precision: float
    minimum_validation_samples: int


@dataclass(frozen=True)
class CategoryEligibility:
    category: EmailCategory
    configured_threshold: float
    validated_precision: float | None
    validation_sample_count: int
    auto_action_eligible: bool
    reason: str


@dataclass(frozen=True)
class CandidateAssessment:
    promote_model: bool
    promotion_reason: str
    categories: Mapping[EmailCategory, CategoryEligibility]

    def __post_init__(self) -> None:
        object.__setattr__(self, "categories", MappingProxyType(dict(self.categories)))


@dataclass(frozen=True)
class TrainingResult:
    promoted: bool
    model_version: str
    example_count: int
    category_counts: dict[str, int]
    leave_one_out_accuracy: float


def assess_candidate(
    readiness: TrainingReadiness,
    *,
    validation_score: float,
    per_category: Mapping[EmailCategory, CategoryValidation],
    category_requirements: Mapping[EmailCategory, EligibilityRequirement],
) -> CandidateAssessment:
    """Assess model promotion and category action eligibility independently."""

    if not readiness.ready:
        promote_model = False
        promotion_reason = "feedback_not_ready"
    elif not math.isfinite(validation_score):
        promote_model = False
        promotion_reason = "candidate_validation_not_finite"
    else:
        promote_model = True
        promotion_reason = "candidate_validation_passed"

    categories: dict[EmailCategory, CategoryEligibility] = {}
    for category, requirement in category_requirements.items():
        validation = per_category.get(category, CategoryValidation(None, 0))
        precision_met = (
            validation.validated_precision is not None
            and validation.validated_precision >= requirement.minimum_precision
        )
        samples_met = (
            validation.validation_sample_count
            >= requirement.minimum_validation_samples
        )
        if precision_met and samples_met:
            reason = "precision_and_sample_gate_met"
        elif not precision_met and not samples_met:
            reason = "precision_and_sample_gate_not_met"
        elif not precision_met:
            reason = "precision_gate_not_met"
        else:
            reason = "sample_gate_not_met"
        categories[category] = CategoryEligibility(
            category=category,
            configured_threshold=requirement.configured_threshold,
            validated_precision=validation.validated_precision,
            validation_sample_count=validation.validation_sample_count,
            auto_action_eligible=precision_met and samples_met,
            reason=reason,
        )

    return CandidateAssessment(
        promote_model=promote_model,
        promotion_reason=promotion_reason,
        categories=categories,
    )


def assess_feedback_readiness(
    store: EmailStore,
    *,
    minimum_examples: int = 5,
    minimum_per_category: int = 2,
) -> TrainingReadiness:
    if minimum_examples <= 0:
        raise ValueError("minimum_examples must be positive")
    if minimum_per_category <= 0:
        raise ValueError("minimum_per_category must be positive")
    examples = store.list_training_examples()
    counts: dict[str, int] = {}
    for example in examples:
        counts[example["label"]] = counts.get(example["label"], 0) + 1
    reasons: list[str] = []
    if len(examples) < minimum_examples:
        reasons.append(f"minimum {minimum_examples} feedback examples required")
    if len(counts) < 2:
        reasons.append("at least two categories are required")
    underrepresented = sorted(
        label for label, count in counts.items() if count < minimum_per_category
    )
    if underrepresented:
        reasons.append(
            f"minimum {minimum_per_category} examples per category required: "
            + ", ".join(underrepresented)
        )
    return TrainingReadiness(
        ready=not reasons,
        example_count=len(examples),
        category_counts=counts,
        reasons=tuple(reasons),
    )


def train_and_promote(
    store: EmailStore,
    active_path: str | Path,
    previous_path: str | Path,
    *,
    model_version: str,
    c: float = 0.25,
    minimum_examples: int = 5,
    minimum_per_category: int = 2,
) -> TrainingResult:
    """Train, serialize, reload, and atomically promote a review-only model.

    No mailbox connector is called here.  The active model only changes after
    feedback readiness, leave-one-out evaluation, serialization, and reload
    all succeed.  A model's promotion does not authorize any provider action.
    """

    readiness = assess_feedback_readiness(
        store,
        minimum_examples=minimum_examples,
        minimum_per_category=minimum_per_category,
    )
    if not readiness.ready:
        raise TrainingNotReady("; ".join(readiness.reasons))

    examples = store.list_training_examples()
    leave_one_out_accuracy = _leave_one_out_accuracy(
        examples, c=c, model_version=model_version
    )
    if not math.isfinite(leave_one_out_accuracy):
        raise ValueError("candidate validation produced a non-finite accuracy")

    candidate = CpuTfidfLogisticClassifier(c=c, model_version=model_version)
    candidate.fit(
        [example["model_text"] for example in examples],
        [example["label"] for example in examples],
    )

    active = Path(active_path)
    previous = Path(previous_path)
    active.parent.mkdir(parents=True, exist_ok=True)
    candidate_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=active.parent,
            prefix=f".{active.name}.candidate.",
            suffix=".pkl",
            delete=False,
        ) as handle:
            candidate_path = Path(handle.name)
        candidate.save(candidate_path)
        # Never replace the active model until the serialized candidate loads.
        CpuTfidfLogisticClassifier.load(candidate_path)
        if active.exists():
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(active, previous)
        os.replace(candidate_path, active)
        candidate_path = None
    finally:
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)

    return TrainingResult(
        promoted=True,
        model_version=model_version,
        example_count=readiness.example_count,
        category_counts=readiness.category_counts,
        leave_one_out_accuracy=leave_one_out_accuracy,
    )


def _leave_one_out_accuracy(
    examples: list[dict[str, str]], *, c: float, model_version: str
) -> float:
    expected: list[str] = []
    predicted: list[str] = []
    for index, example in enumerate(examples):
        training = examples[:index] + examples[index + 1 :]
        classifier = CpuTfidfLogisticClassifier(c=c, model_version=model_version)
        classifier.fit(
            [item["model_text"] for item in training],
            [item["label"] for item in training],
        )
        expected.append(example["label"])
        predicted.append(classifier.predict(example["model_text"]).label)
    return sum(actual == guess for actual, guess in zip(expected, predicted)) / len(expected)
