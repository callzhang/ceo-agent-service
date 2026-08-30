"""CPU email-classifier training, validation, and immutable promotion."""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

from sklearn.metrics import accuracy_score, precision_recall_fscore_support

from app.email_classifier_contracts import EmailCategory
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_model_registry import (
    MODEL_FAMILY,
    EmailModelMetadata,
    EmailModelRegistry,
    build_model_id,
)
from app.email_store import EmailStore


class TrainingNotReady(ValueError):
    """Confirmed feedback is insufficient for a candidate model."""


def _validate_unit_interval_float(name: str, value: object) -> None:
    if not isinstance(value, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a finite float between 0 and 1")


def _validate_integer(name: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} integer")


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

    def __post_init__(self) -> None:
        if self.validated_precision is not None:
            _validate_unit_interval_float("validated_precision", self.validated_precision)
        _validate_integer("validation_sample_count", self.validation_sample_count, minimum=0)


@dataclass(frozen=True)
class EligibilityRequirement:
    configured_threshold: float
    minimum_precision: float
    minimum_validation_samples: int

    def __post_init__(self) -> None:
        _validate_unit_interval_float("configured_threshold", self.configured_threshold)
        _validate_unit_interval_float("minimum_precision", self.minimum_precision)
        _validate_integer(
            "minimum_validation_samples", self.minimum_validation_samples, minimum=1
        )


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
    model_id: str
    example_count: int
    new_sample_count: int
    category_counts: dict[str, int]
    account_counts: dict[str, int]
    validation_method: str
    accuracy: float
    macro_f1: float
    prediction_latency_p50_ms: float
    prediction_latency_p95_ms: float
    status: str
    promotion_reason: str

    @property
    def model_version(self) -> str:
        return self.model_id

    @property
    def leave_one_out_accuracy(self) -> float:
        return self.accuracy


def assess_candidate(
    readiness: TrainingReadiness,
    *,
    validation_score: float,
    per_category: Mapping[EmailCategory, CategoryValidation],
    category_requirements: Mapping[EmailCategory, EligibilityRequirement],
) -> CandidateAssessment:
    """Assess global promotion and category action eligibility independently."""
    _validate_unit_interval_float("validation_score", validation_score)
    promote_model = readiness.ready
    promotion_reason = (
        "candidate_validation_passed" if readiness.ready else "feedback_not_ready"
    )
    categories: dict[EmailCategory, CategoryEligibility] = {}
    for category, requirement in category_requirements.items():
        validation = per_category.get(category, CategoryValidation(None, 0))
        precision_met = (
            validation.validated_precision is not None
            and validation.validated_precision >= requirement.minimum_precision
        )
        samples_met = validation.validation_sample_count >= requirement.minimum_validation_samples
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
    return CandidateAssessment(promote_model, promotion_reason, categories)


def assess_feedback_readiness(
    store: EmailStore,
    *,
    minimum_examples: int = 5,
    minimum_per_category: int = 2,
) -> TrainingReadiness:
    return assess_examples_readiness(
        store.list_training_examples(),
        minimum_examples=minimum_examples,
        minimum_per_category=minimum_per_category,
    )


def assess_examples_readiness(
    examples: Sequence[Mapping[str, str]],
    *,
    minimum_examples: int = 5,
    minimum_per_category: int = 2,
) -> TrainingReadiness:
    if minimum_examples <= 0:
        raise ValueError("minimum_examples must be positive")
    if minimum_per_category <= 0:
        raise ValueError("minimum_per_category must be positive")
    counts = dict(Counter(example["label"] for example in examples))
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
    return TrainingReadiness(not reasons, len(examples), counts, tuple(reasons))


def train_and_promote(
    store: EmailStore,
    registry: EmailModelRegistry | str | Path,
    previous_path: str | Path | None = None,
    *,
    model_version: str | None = None,
    trained_at: datetime | None = None,
    c: float = 0.25,
    minimum_examples: int = 5,
    minimum_per_category: int = 2,
    category_requirements: Mapping[EmailCategory, EligibilityRequirement] | None = None,
) -> TrainingResult:
    """Train a candidate, validate its immutable artifact, and promote atomically."""
    if not isinstance(registry, EmailModelRegistry):
        if previous_path is None or model_version is None:
            raise TypeError("path-based promotion requires previous_path and model_version")
        return _train_and_promote_paths(
            store,
            Path(registry),
            Path(previous_path),
            model_version=model_version,
            c=c,
            minimum_examples=minimum_examples,
            minimum_per_category=minimum_per_category,
        )
    started = datetime.now(timezone.utc)
    examples = store.list_training_examples()
    readiness = assess_examples_readiness(
        examples,
        minimum_examples=minimum_examples,
        minimum_per_category=minimum_per_category,
    )
    if not readiness.ready:
        raise TrainingNotReady("; ".join(readiness.reasons))
    sample_ids = tuple(example["message_id"] for example in examples)
    new_sample_ids = registry.unincluded_sample_ids(sample_ids)
    if not new_sample_ids:
        raise TrainingNotReady("no unincluded authoritative feedback")

    validation_method, expected, predicted = _validation_predictions(examples, c=c)
    labels = sorted(readiness.category_counts)
    accuracy = float(accuracy_score(expected, predicted))
    precisions, recalls, f1s, supports = precision_recall_fscore_support(
        expected, predicted, labels=labels, zero_division=0
    )
    macro_f1 = float(sum(float(value) for value in f1s) / len(f1s))
    per_category_validation = {
        EmailCategory(label): CategoryValidation(float(precisions[index]), int(supports[index]))
        for index, label in enumerate(labels)
    }
    requirements = dict(category_requirements or _default_requirements(labels))
    assessment = assess_candidate(
        readiness,
        validation_score=accuracy,
        per_category=per_category_validation,
        category_requirements=requirements,
    )

    classifier = CpuTfidfLogisticClassifier(c=c, model_version="candidate")
    classifier.fit(
        [example["model_text"] for example in examples],
        [example["label"] for example in examples],
    )
    p50, p95 = _prediction_latency(
        classifier, [example["model_text"] for example in examples]
    )
    finished = trained_at or datetime.now(timezone.utc)
    if finished.tzinfo is None or finished.utcoffset() is None:
        raise ValueError("trained_at must be timezone-aware")

    with tempfile.TemporaryDirectory(dir=registry.root) as temporary_directory:
        candidate_path = Path(temporary_directory) / "candidate.pkl"
        classifier.save(candidate_path)
        digest = sha256(candidate_path.read_bytes()).hexdigest()
        model_id = build_model_id(trained_at=finished, artifact_sha256=digest)
        parent = registry.active_manifest()
        metadata = EmailModelMetadata(
            model_id=model_id,
            parent_model_id=parent.model_id if parent else None,
            model_family=MODEL_FAMILY,
            tokenizer_version="jieba-default-v1",
            feature_version=CpuTfidfLogisticClassifier.FEATURE_VERSION,
            training_dataset_version=_dataset_version(examples),
            trained_at=finished.astimezone(timezone.utc).isoformat(),
            training_started_at=started.isoformat(),
            training_finished_at=finished.astimezone(timezone.utc).isoformat(),
            sample_count=len(examples),
            new_sample_count=len(new_sample_ids),
            category_counts=readiness.category_counts,
            account_counts=dict(Counter(_account_id(item["message_id"]) for item in examples)),
            validation_method=validation_method,
            accuracy=accuracy,
            macro_f1=macro_f1,
            per_category_metrics={
                label: {
                    "precision": float(precisions[index]),
                    "recall": float(recalls[index]),
                    "f1": float(f1s[index]),
                    "validation_sample_count": int(supports[index]),
                    "configured_threshold": assessment.categories[EmailCategory(label)].configured_threshold,
                    "minimum_validation_samples": requirements[EmailCategory(label)].minimum_validation_samples,
                    "auto_action_eligible": assessment.categories[EmailCategory(label)].auto_action_eligible,
                    "eligibility_reason": assessment.categories[EmailCategory(label)].reason,
                }
                for index, label in enumerate(labels)
            },
            prediction_latency_p50_ms=p50,
            prediction_latency_p95_ms=p95,
            artifact_sha256=digest,
            status="candidate",
            promotion_reason="candidate_validation_pending",
            failure_reason="",
        )
        parity_texts = tuple(example["model_text"] for example in examples)
        parity_labels = tuple(classifier.predict(text).label for text in parity_texts)
        registry.stage_candidate(
            candidate_path,
            metadata,
            parity_texts=parity_texts,
            expected_labels=parity_labels,
        )

    rejection = _promotion_rejection(registry, metadata)
    if rejection is not None:
        registry.reject(model_id, reason=rejection)
        return _result(metadata, promoted=False, status="rejected", reason=rejection)
    registry.promote(model_id, reason="candidate_validation_passed")
    registry.mark_samples_included(sample_ids, model_id=model_id)
    return _result(metadata, promoted=True, status="active", reason="candidate_validation_passed")


def _train_and_promote_paths(
    store: EmailStore,
    active: Path,
    previous: Path,
    *,
    model_version: str,
    c: float,
    minimum_examples: int,
    minimum_per_category: int,
) -> TrainingResult:
    """Preserve the existing prototype API while callers migrate to the registry."""
    readiness = assess_feedback_readiness(
        store,
        minimum_examples=minimum_examples,
        minimum_per_category=minimum_per_category,
    )
    if not readiness.ready:
        raise TrainingNotReady("; ".join(readiness.reasons))
    examples = store.list_training_examples()
    method, expected, predicted = _validation_predictions(examples, c=c)
    accuracy = float(accuracy_score(expected, predicted))
    classifier = CpuTfidfLogisticClassifier(c=c, model_version=model_version).fit(
        [item["model_text"] for item in examples],
        [item["label"] for item in examples],
    )
    p50, p95 = _prediction_latency(classifier, [item["model_text"] for item in examples])
    active.parent.mkdir(parents=True, exist_ok=True)
    candidate: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=active.parent, delete=False) as handle:
            candidate = Path(handle.name)
        classifier.save(candidate)
        CpuTfidfLogisticClassifier.load(candidate)
        if active.exists():
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(active, previous)
        os.replace(candidate, active)
        candidate = None
    finally:
        if candidate is not None:
            candidate.unlink(missing_ok=True)
    return TrainingResult(
        promoted=True,
        model_id=model_version,
        example_count=len(examples),
        new_sample_count=len(examples),
        category_counts=readiness.category_counts,
        account_counts=dict(Counter(_account_id(item["message_id"]) for item in examples)),
        validation_method=method,
        accuracy=accuracy,
        macro_f1=accuracy,
        prediction_latency_p50_ms=p50,
        prediction_latency_p95_ms=p95,
        status="active",
        promotion_reason="prototype_path_promotion",
    )


def _validation_predictions(
    examples: Sequence[Mapping[str, str]], *, c: float
) -> tuple[str, list[str], list[str]]:
    if len(examples) >= 50:
        split = max(1, int(len(examples) * 0.8))
        training = list(examples[:split])
        validation = list(examples[split:])
        if len({item["label"] for item in training}) >= 2 and validation:
            classifier = CpuTfidfLogisticClassifier(c=c, model_version="validation")
            classifier.fit(
                [item["model_text"] for item in training],
                [item["label"] for item in training],
            )
            return (
                "time-ordered-holdout",
                [item["label"] for item in validation],
                [classifier.predict(item["model_text"]).label for item in validation],
            )
    expected: list[str] = []
    predicted: list[str] = []
    for index, example in enumerate(examples):
        training = list(examples[:index]) + list(examples[index + 1 :])
        if len({item["label"] for item in training}) < 2:
            raise TrainingNotReady("leave-one-out fold has fewer than two categories")
        classifier = CpuTfidfLogisticClassifier(c=c, model_version="validation")
        classifier.fit(
            [item["model_text"] for item in training],
            [item["label"] for item in training],
        )
        expected.append(example["label"])
        predicted.append(classifier.predict(example["model_text"]).label)
    return "leave-one-out", expected, predicted


def _prediction_latency(
    classifier: CpuTfidfLogisticClassifier, texts: Sequence[str]
) -> tuple[float, float]:
    probes = tuple(texts[: min(len(texts), 20)])
    for text in probes:
        classifier.predict(text)
    timings: list[float] = []
    for _ in range(5):
        for text in probes:
            started = time.perf_counter_ns()
            classifier.predict(text)
            timings.append((time.perf_counter_ns() - started) / 1_000_000)
    timings.sort()
    return (_percentile(timings, 0.50), _percentile(timings, 0.95))


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("latency measurements must not be empty")
    index = max(0, math.ceil(len(values) * fraction) - 1)
    return float(values[index])


def _promotion_rejection(
    registry: EmailModelRegistry, candidate: EmailModelMetadata
) -> str | None:
    if candidate.prediction_latency_p95_ms >= 100.0:
        return "latency_p95_exceeded"
    active = registry.active_manifest()
    if active is None:
        return None
    current = registry.get_model(active.model_id).metadata
    if candidate.macro_f1 < current.macro_f1:
        return "macro_f1_regressed"
    for label, previous in current.per_category_metrics.items():
        incoming = candidate.per_category_metrics.get(label)
        if incoming is None:
            continue
        previous_count = int(previous.get("validation_sample_count", 0))
        incoming_count = int(incoming.get("validation_sample_count", 0))
        minimum = int(previous.get("minimum_validation_samples", 1))
        if previous_count < minimum or incoming_count < minimum:
            continue
        if float(incoming.get("precision", 0.0)) < float(previous.get("precision", 0.0)):
            return f"category_precision_regressed:{label}"
    return None


def _default_requirements(labels: Sequence[str]) -> dict[EmailCategory, EligibilityRequirement]:
    return {
        EmailCategory(label): EligibilityRequirement(
            configured_threshold=0.85,
            minimum_precision=0.95,
            minimum_validation_samples=30,
        )
        for label in labels
    }


def _dataset_version(examples: Sequence[Mapping[str, str]]) -> str:
    digest = sha256()
    for example in examples:
        digest.update(example["message_id"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(example["label"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(example["model_text"].encode("utf-8")).digest())
    return "feedback-sha256:" + digest.hexdigest()


def _account_id(message_id: str) -> str:
    account_id, separator, _ = message_id.partition(":")
    return account_id if separator and account_id else "unknown-account"


def _result(
    metadata: EmailModelMetadata, *, promoted: bool, status: str, reason: str
) -> TrainingResult:
    return TrainingResult(
        promoted=promoted,
        model_id=metadata.model_id,
        example_count=metadata.sample_count,
        new_sample_count=metadata.new_sample_count,
        category_counts=dict(metadata.category_counts),
        account_counts=dict(metadata.account_counts),
        validation_method=metadata.validation_method,
        accuracy=metadata.accuracy,
        macro_f1=metadata.macro_f1,
        prediction_latency_p50_ms=metadata.prediction_latency_p50_ms,
        prediction_latency_p95_ms=metadata.prediction_latency_p95_ms,
        status=status,
        promotion_reason=reason,
    )
