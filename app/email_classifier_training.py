"""CPU email-classifier training, validation, and immutable promotion."""

from __future__ import annotations

import math
import json
import os
import shutil
import tempfile
import time
import warnings
from importlib.metadata import version as dependency_version
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import numpy as np
from sklearn.metrics import accuracy_score, precision_recall_fscore_support

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailCategoryKey,
    INITIAL_EMAIL_CATEGORY_KEYS,
    validate_email_category_key,
)
from app.email_classifier_model import CpuTfidfLogisticClassifier, EmailModelPrediction
from app.email_description_optimizer import (
    DescriptionSetOverlay,
    description_set_digest,
)
from app.email_model_registry import (
    MODEL_FAMILY,
    EmailModelMetadata,
    EmailModelRegistry,
    build_model_id,
    build_embedding_model_id,
    CandidateCompatibility,
    CandidateMaturityEvidence,
    HistoricalEligibility,
    HistoricalSystematicErrorState,
    assess_staged_candidate_readiness,
)
from app.email_store import EmailStore


class TrainingNotReady(ValueError):
    """Confirmed feedback is insufficient for a candidate model."""


@dataclass(frozen=True)
class FrozenEmbeddingCandidateResult:
    model_id: str
    training_count: int
    validation_count: int
    test_count: int
    category_metrics: Mapping[str, Mapping[str, object]]
    important_metrics: Mapping[str, object]
    head_latency_ms: Mapping[str, float | int]
    failure_reason: str
    maturity: CandidateMaturityEvidence


def train_frozen_embedding_candidate(
    *,
    store: EmailStore,
    snapshot_id: str,
    registry: EmailModelRegistry,
    cache: object,
    descriptions: Mapping[str, object],
    embedding_model_id: str,
    embedding_revision: str,
    parent_model_id: str | None,
    historical_systematic_error_state: HistoricalSystematicErrorState,
    trained_at: datetime | None = None,
    expected_snapshot_sha: str | None = None,
    expected_description_version: str | None = None,
    description_overlay: DescriptionSetOverlay | None = None,
    benchmark_candidate: Callable[[object, Sequence[Mapping[str, object]]], Mapping[str, object]] | None = None,
) -> FrozenEmbeddingCandidateResult:
    """Train both heads from one frozen Task 5 split and only stage evidence."""

    training_started_at = datetime.now(timezone.utc)
    training_started_clock = time.perf_counter()
    if type(historical_systematic_error_state) is not HistoricalSystematicErrorState:
        raise TypeError(
            "historical_systematic_error_state must be HistoricalSystematicErrorState"
        )
    unresolved_historical_systematic_error = (
        historical_systematic_error_state.unresolved
    )

    from app.email_embedding_cache import EmbeddingCacheKey
    from app.email_embedding_classifier import (
        CategoryDescription,
        DescriptionAwareEmailClassifier,
        DescriptionVectors,
    )

    snapshot = store.get_training_snapshot(snapshot_id)
    if snapshot is None:
        raise TrainingNotReady("frozen training snapshot does not exist")
    if (
        expected_snapshot_sha is not None
        and snapshot["snapshot_digest"] != expected_snapshot_sha
    ):
        raise TrainingNotReady("frozen snapshot SHA changed before training")
    categories = tuple(descriptions)
    if not categories or any(
        type(descriptions[key]) is not CategoryDescription for key in categories
    ):
        raise ValueError("descriptions must contain ordered CategoryDescription values")
    description_digest = description_set_digest(descriptions)
    description_version = "description-set-sha256:" + description_digest
    if description_overlay is not None and (
        type(description_overlay) is not DescriptionSetOverlay
        or description_overlay.description_set_digest != description_digest
        or description_overlay.description_set_version != description_version
        or description_overlay.source_snapshot_sha != snapshot["snapshot_digest"]
    ):
        raise TrainingNotReady("description proposal overlay does not match training")
    if (
        expected_description_version is not None
        and description_version != expected_description_version
    ):
        raise TrainingNotReady("description version changed before training")
    all_rows = tuple(snapshot["observations"])
    rows = tuple(
        row for row in snapshot["observations"] if row["category_key"] in descriptions
    )
    if {str(row["category_key"]) for row in rows} != set(categories):
        raise TrainingNotReady("snapshot categories do not match enabled descriptions")
    training = tuple(
        row for row in rows if row["split"] == "train" and row["selected_for_training"]
    )
    validation = tuple(row for row in rows if row["split"] == "validation")
    test = tuple(row for row in rows if row["split"] == "test")
    important_training = tuple(row for row in all_rows if row["split"] == "train")
    important_validation = tuple(
        row for row in all_rows if row["split"] == "validation"
    )
    important_test = tuple(row for row in all_rows if row["split"] == "test")
    if not training or not validation or not test:
        raise TrainingNotReady(
            "frozen snapshot requires train, validation, and test rows"
        )
    if {str(row["category_key"]) for row in training} != set(categories):
        raise TrainingNotReady("training split must cover every enabled category")
    for split_name, split_rows in (
        ("training", training),
        ("validation", validation),
        ("test", test),
    ):
        if {str(row["category_key"]) for row in split_rows} != set(categories):
            raise TrainingNotReady(
                f"{split_name} split must cover every enabled category"
            )
    for split_name, split_rows in (
        ("training", important_training),
        ("validation", important_validation),
        ("test", important_test),
    ):
        if not split_rows or {bool(row["important"]) for row in split_rows} != {
            False,
            True,
        }:
            raise TrainingNotReady(
                f"{split_name} split must cover both important labels"
            )

    input_schema = str(snapshot["input_schema_version"])

    def vector_for(row: Mapping[str, object]) -> np.ndarray:
        key = EmbeddingCacheKey.for_text(
            normalized_text=str(row["normalized_model_input"]),
            input_schema_version=input_schema,
            embedding_model_id=embedding_model_id,
            embedding_revision=embedding_revision,
        )
        vector = cache.get(key)
        if vector is None:
            raise TrainingNotReady("snapshot embedding cache is incomplete")
        return vector

    description_vectors: dict[str, DescriptionVectors] = {}
    for category in categories:
        description = descriptions[category]

        def description_vector(text: str) -> np.ndarray:
            key = EmbeddingCacheKey.for_description(
                text=text,
                description_version=description.version,
                input_schema_version=input_schema,
                embedding_model_id=embedding_model_id,
                embedding_revision=embedding_revision,
            )
            vector = cache.get(key)
            if vector is None:
                raise TrainingNotReady("description embedding cache is incomplete")
            return vector

        description_vectors[category] = DescriptionVectors(
            core=description_vector(description.core),
            include=np.stack(
                [description_vector(item) for item in description.include]
            ),
            exclude=np.stack(
                [description_vector(item) for item in description.exclude]
            ),
        )

    train_matrix = np.stack([vector_for(row) for row in training])
    important_train_matrix = np.stack([vector_for(row) for row in important_training])
    dimension = int(train_matrix.shape[1])
    base = DescriptionAwareEmailClassifier(
        enabled_categories=categories,
        descriptions=descriptions,
        description_vectors=description_vectors,
        dimension=dimension,
        input_schema_version=input_schema,
        embedding_model_id=embedding_model_id,
        embedding_revision=embedding_revision,
    ).fit(
        train_matrix,
        [str(row["category_key"]) for row in training],
        [bool(row["important"]) for row in important_training],
        important_embeddings=important_train_matrix,
        tuning_folds=_category_tuning_folds(training),
    )
    validation_predictions = tuple(base.predict(vector_for(row)) for row in validation)
    important_validation_predictions = tuple(
        base.predict(vector_for(row)) for row in important_validation
    )
    thresholds = {
        category: _calibrated_threshold(
            probabilities=[
                prediction.category_probabilities[category]
                for prediction in validation_predictions
            ],
            positives=[str(row["category_key"]) == category for row in validation],
            eligible=[
                prediction.category == category for prediction in validation_predictions
            ],
        )
        for category in categories
    }
    important_threshold = _calibrated_threshold(
        probabilities=[
            item.important_probability for item in important_validation_predictions
        ],
        positives=[bool(row["important"]) for row in important_validation],
        eligible=[True for _item in important_validation_predictions],
    )
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=categories,
        descriptions=descriptions,
        description_vectors=description_vectors,
        dimension=dimension,
        input_schema_version=input_schema,
        embedding_model_id=embedding_model_id,
        embedding_revision=embedding_revision,
        category_thresholds=thresholds,
        important_threshold=important_threshold,
        alpha=base.alpha,
        beta=base.beta,
    ).fit(
        train_matrix,
        [str(row["category_key"]) for row in training],
        [bool(row["important"]) for row in important_training],
        important_embeddings=important_train_matrix,
    )

    test_predictions = tuple(classifier.predict(vector_for(row)) for row in test)
    important_test_predictions = tuple(
        classifier.predict(vector_for(row)) for row in important_test
    )
    category_metrics = {
        category: _category_acceptance_metrics(
            category=category,
            rows=test,
            predictions=test_predictions,
            threshold=thresholds[category],
        )
        for category in categories
    }
    important_metrics = _important_acceptance_metrics(
        rows=important_test,
        predictions=important_test_predictions,
        threshold=important_threshold,
    )
    head_latencies = [float(item.head_ms) for item in important_test_predictions]
    head_latency_ms = {
        "p50": _numeric_percentile(head_latencies, 0.50),
        "p95": _numeric_percentile(head_latencies, 0.95),
        "p99": _numeric_percentile(head_latencies, 0.99),
        "max": max(head_latencies),
        "sample_count": len(head_latencies),
    }
    if trained_at is not None and (
        trained_at.tzinfo is None or trained_at.utcoffset() is None
    ):
        raise ValueError("trained_at must be timezone-aware")
    timestamp = (trained_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    temporary_directory = tempfile.TemporaryDirectory(dir=registry.root)
    artifact_path = Path(temporary_directory.name) / "candidate.artifact"
    classifier.save(artifact_path)
    artifact_sha = sha256(artifact_path.read_bytes()).hexdigest()
    model_id = build_embedding_model_id(
        trained_at=timestamp, artifact_sha256=artifact_sha
    )

    compatibility = CandidateCompatibility(
        enabled_categories=categories,
        description_version=description_version,
        input_schema_version=input_schema,
        embedding_model_id=embedding_model_id,
        embedding_revision=embedding_revision,
        head_format="description-mlp-v1",
        parent_model_id=parent_model_id,
    )
    maturity = CandidateMaturityEvidence(
        model_id=model_id,
        source_snapshot_id=snapshot_id,
        source_snapshot_digest=str(snapshot["snapshot_digest"]),
        source_snapshot_observed_at=str(snapshot["observed_at"]),
        folder_label_watermark=int(snapshot["folder_label_watermark"]),
        important_label_watermark=int(snapshot["important_label_watermark"]),
        compatibility=compatibility,
        category_eligibility={
            category: HistoricalEligibility(
                precision=float(category_metrics[category]["accepted_precision"]),
                accepted_hits=int(category_metrics[category]["accepted_hits"]),
                independent_groups=int(
                    category_metrics[category]["independent_groups"]
                ),
            )
            for category in categories
        },
        important_eligibility=HistoricalEligibility(
            precision=float(important_metrics["accepted_precision"]),
            accepted_hits=int(important_metrics["accepted_hits"]),
            independent_groups=int(important_metrics["independent_groups"]),
        ),
        unresolved_historical_systematic_error=unresolved_historical_systematic_error,
    )
    compatibility_evidence = {
        **compatibility.__dict__,
        "enabled_categories": list(categories),
    }
    current_readiness_evidence = {
        "model_id": model_id,
        "source_snapshot_id": snapshot_id,
        "source_snapshot_digest": str(snapshot["snapshot_digest"]),
        "source_snapshot_observed_at": str(snapshot["observed_at"]),
        "folder_label_watermark": int(snapshot["folder_label_watermark"]),
        "important_label_watermark": int(snapshot["important_label_watermark"]),
        "compatibility": compatibility_evidence,
        "metrics": {
            "categories": category_metrics,
            "important": important_metrics,
        },
        "unresolved_historical_systematic_error": (
            unresolved_historical_systematic_error
        ),
    }
    classification_conflicts = [
        {
            "sample_id": str(row["stable_message_identity"]),
            "group_key": str(row["group_key"]),
            "predicted_category": prediction.category,
            "confirmed_category": str(row["category_key"]),
            "source": "frozen_test_fp_fn",
        }
        for row, prediction in zip(test, test_predictions, strict=True)
        if prediction.category != str(row["category_key"])
    ]
    whole_readiness = assess_staged_candidate_readiness(
        (*registry.list_staged_evidence(), current_readiness_evidence)
    )
    benchmark_evidence: dict[str, object] = {
        "status": "unmeasured", "reason": "benchmark_not_configured",
    }
    if benchmark_candidate is not None:
        from app.email_candidate_benchmark import (
            BENCHMARK_BOUNDARY, BENCHMARK_PROTOCOL, BENCHMARK_STAGES,
        )

        benchmark_failure_reason = "benchmark_callback_failed"
        try:
            report = benchmark_candidate(
                classifier, tuple(MappingProxyType(dict(row)) for row in test),
            )
            benchmark_failure_reason = "benchmark_evidence_invalid"
            benchmark_evidence = json.loads(json.dumps(dict(report), allow_nan=False))
            if benchmark_evidence.get("status") == "measured":
                measured = benchmark_evidence["end_to_end_latency_ms"]
                if (
                    measured["protocol"] != BENCHMARK_PROTOCOL
                    or measured["boundary"] != BENCHMARK_BOUNDARY
                    or measured["runtime_warm"] is not True
                    or measured.get("input_contract_verified") is not True
                    or measured["cache_hit"] is not False
                    or measured["stages"] != list(BENCHMARK_STAGES)
                ):
                    raise ValueError("benchmark boundary does not cover end-to-end prediction")
                count = measured["sample_count"]
                if type(count) is not int or count <= 0:
                    raise ValueError("benchmark sample count is invalid")
                percentiles = [measured[key] for key in ("p50", "p95", "p99")]
                if any(type(value) not in (int, float) or not math.isfinite(value)
                       or value < 0 for value in percentiles) or percentiles != sorted(percentiles):
                    raise ValueError("benchmark percentiles are invalid")
            elif benchmark_evidence.get("status") != "unmeasured":
                raise ValueError("benchmark status is invalid")
        except Exception as exc:
            benchmark_evidence = {
                "status": "unmeasured", "reason": benchmark_failure_reason,
                "error_type": type(exc).__name__,
            }
    training_completed_at = datetime.now(timezone.utc)
    evidence = {
        "model_id": model_id,
        "source_snapshot_id": snapshot_id,
        "source_snapshot_digest": str(snapshot["snapshot_digest"]),
        "source_snapshot_observed_at": str(snapshot["observed_at"]),
        "folder_label_watermark": int(snapshot["folder_label_watermark"]),
        "important_label_watermark": int(snapshot["important_label_watermark"]),
        "status": "candidate",
        "training_never_activates": True,
        "compatibility": compatibility_evidence,
        "split_counts": {
            "train": len(training),
            "validation": len(validation),
            "test": len(test),
            "test_evaluations": 1,
            "important": {
                "train": len(important_training),
                "validation": len(important_validation),
                "test": len(important_test),
            },
        },
        "metrics": {
            "accuracy": float(accuracy_score(
                [str(row["category_key"]) for row in test],
                [item.category for item in test_predictions],
            )),
            "macro_f1": float(np.mean([
                float(category_metrics[category]["f1"]) for category in categories
            ])),
            "categories": category_metrics,
            "important": important_metrics,
        },
        "evaluation": {
            "protocol": "email-folder-heldout-v1",
            "test_digest": _heldout_test_digest(important_test),
            "category_keys": list(categories),
        },
        "training": {
            "started_at": training_started_at.isoformat(),
            "completed_at": training_completed_at.isoformat(),
            "duration_ms": (time.perf_counter() - training_started_clock) * 1000.0,
            "sample_count": len(all_rows),
            "category_sample_count": len(rows),
            "account_count": len({str(row["account_id"]) for row in all_rows}),
            "group_count": len({str(row["group_key"]) for row in all_rows}),
        },
        "classification_conflicts": classification_conflicts,
        "historical_eligibility": {
            "categories": {
                key: {
                    "precision": value.precision,
                    "accepted_hits": value.accepted_hits,
                    "independent_groups": value.independent_groups,
                    "eligible": value.eligible,
                }
                for key, value in maturity.category_eligibility.items()
            },
            "important": {
                "precision": maturity.important_eligibility.precision,
                "accepted_hits": maturity.important_eligibility.accepted_hits,
                "independent_groups": maturity.important_eligibility.independent_groups,
                "eligible": maturity.important_eligibility.eligible,
            },
        },
        "whole_model_readiness": {
            "ready": whole_readiness.ready,
            "passing_model_ids": list(whole_readiness.passing_model_ids),
            "reason": whole_readiness.reason,
        },
        "head_latency_ms": head_latency_ms,
        "candidate_benchmark": benchmark_evidence,
        "parameters": {
            "alpha": classifier.alpha,
            "beta": classifier.beta,
            "category_thresholds": thresholds,
            "important_threshold": important_threshold,
            "head_format": "description-mlp-v1",
            "hidden_layer_sizes": [8],
            "solver": "lbfgs",
            "regularization_alpha": 0.001,
            "max_iter": 1000,
            "random_seed": 20260905,
        },
        "dependencies": {
            "numpy": dependency_version("numpy"),
            "scikit_learn": dependency_version("scikit-learn"),
            "embedding_model_id": embedding_model_id,
            "embedding_model_revision": embedding_revision,
        },
        "hashes": {
            "snapshot_sha256": str(snapshot["snapshot_digest"]),
            "artifact_sha256": artifact_sha,
            "description_sha256": description_digest,
        },
        "trained_at": timestamp.isoformat(),
        "failure_reason": "",
        "historical_systematic_error_state_sha256": (
            historical_systematic_error_state.state_sha256
        ),
        "historical_systematic_error_state": {
            **historical_systematic_error_state.to_dict(),
            "state_sha256": historical_systematic_error_state.state_sha256,
        },
        "unresolved_historical_systematic_error": (
            unresolved_historical_systematic_error
        ),
    }
    if benchmark_evidence.get("status") == "measured":
        evidence["end_to_end_latency_ms"] = benchmark_evidence["end_to_end_latency_ms"]
    if description_overlay is not None:
        evidence["description_proposal"] = {
            "proposal_id": description_overlay.proposal_id,
            "source_description_version": (
                description_overlay.source_description_version
            ),
            "source_description_digest": (
                description_overlay.source_description_digest
            ),
            "source_snapshot_id": description_overlay.source_snapshot_id,
            "description_set_digest": description_overlay.description_set_digest,
            "source_snapshot_sha": description_overlay.source_snapshot_sha,
            "conflict_category_pair": list(description_overlay.conflict_category_pair),
            "conflict_description_digests": list(
                description_overlay.conflict_description_digests
            ),
        }
    registry.stage_embedding_candidate(model_id, artifact_path, evidence)
    temporary_directory.cleanup()
    return FrozenEmbeddingCandidateResult(
        model_id=model_id,
        training_count=len(training),
        validation_count=len(validation),
        test_count=len(test),
        category_metrics=category_metrics,
        important_metrics=important_metrics,
        head_latency_ms=head_latency_ms,
        failure_reason="",
        maturity=maturity,
    )


def _calibrated_threshold(
    *,
    probabilities: Sequence[float],
    positives: Sequence[bool],
    eligible: Sequence[bool],
) -> float:
    candidates = sorted({float(item) for item in probabilities}, reverse=True)
    selected = 1.0
    best_hits = -1
    for threshold in candidates:
        accepted = [
            index
            for index, value in enumerate(probabilities)
            if eligible[index] and value >= threshold
        ]
        if not accepted:
            continue
        hits = sum(bool(positives[index]) for index in accepted)
        precision = hits / len(accepted)
        if precision >= 0.95 and hits > best_hits:
            selected, best_hits = threshold, hits
    return selected


def _category_tuning_folds(rows) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    by_category: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        by_category.setdefault(str(row["category_key"]), []).append(index)
    if any(len(indices) < 2 for indices in by_category.values()):
        return ()
    folds = []
    all_indices = set(range(len(rows)))
    for parity in (0, 1):
        validation = sorted(
            index
            for indices in by_category.values()
            for offset, index in enumerate(indices)
            if offset % 2 == parity
        )
        training = sorted(all_indices - set(validation))
        if validation and training:
            folds.append(
                (
                    np.asarray(training, dtype=np.int64),
                    np.asarray(validation, dtype=np.int64),
                )
            )
    return tuple(folds)


def _category_acceptance_metrics(*, category, rows, predictions, threshold):
    expected = [str(row["category_key"]) for row in rows]
    predicted = [item.category for item in predictions]
    precision, recall, f1, _ = precision_recall_fscore_support(
        expected, predicted, labels=[category], zero_division=0
    )
    accepted = [
        index
        for index, item in enumerate(predictions)
        if item.category == category and item.category_probability >= threshold
    ]
    hits = [index for index in accepted if expected[index] == category]
    return {
        "support": sum(value == category for value in expected),
        "test_independent_groups": len({
            str(row["group_key"]) for row in rows if row["category_key"] == category
        }),
        "precision": float(precision[0]),
        "recall": float(recall[0]),
        "f1": float(f1[0]),
        "accepted_hits": len(hits),
        "accepted_precision": len(hits) / len(accepted) if accepted else 0.0,
        "independent_groups": len({str(rows[index]["group_key"]) for index in hits}),
        "threshold": float(threshold),
    }


def _heldout_test_digest(rows: Sequence[Mapping[str, object]]) -> str:
    """Hash held-out membership, inputs, groups and labels, independent of row order.

    Snapshot IDs and training rows are deliberately excluded: a new training
    snapshot can still evaluate the exact same held-out examples. Both heads'
    test rows participate, including examples without a business category.
    """
    fields = (
        "account_id", "stable_message_identity", "normalized_model_input_hash",
        "group_key", "category_key", "important",
    )
    records = sorted(
        json.dumps({key: row[key] for key in fields}, sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    return sha256(json.dumps(records, separators=(",", ":")).encode("utf-8")).hexdigest()


def _important_acceptance_metrics(*, rows, predictions, threshold):
    expected = [bool(row["important"]) for row in rows]
    predicted = [item.important for item in predictions]
    precision, recall, f1, _ = precision_recall_fscore_support(
        expected, predicted, labels=[True], zero_division=0
    )
    accepted = [
        index
        for index, item in enumerate(predictions)
        if item.important_probability >= threshold
    ]
    hits = [index for index in accepted if expected[index]]
    return {
        "sample_count": len(rows),
        "precision": float(precision[0]),
        "recall": float(recall[0]),
        "f1": float(f1[0]),
        "accepted_hits": len(hits),
        "accepted_precision": len(hits) / len(accepted) if accepted else 0.0,
        "independent_groups": len({str(rows[index]["group_key"]) for index in hits}),
        "threshold": float(threshold),
    }


def _numeric_percentile(values: Sequence[float], fraction: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), fraction * 100.0))


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
    validated_recall: float | None = None
    validated_f1: float | None = None
    automatic_candidate_count: int = 0
    evaluated_threshold: float | None = None

    def __post_init__(self) -> None:
        for field_name in ("validated_precision", "validated_recall", "validated_f1"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_unit_interval_float(field_name, value)
        _validate_integer(
            "validation_sample_count", self.validation_sample_count, minimum=0
        )
        _validate_integer(
            "automatic_candidate_count", self.automatic_candidate_count, minimum=0
        )
        if self.evaluated_threshold is not None:
            _validate_unit_interval_float(
                "evaluated_threshold", self.evaluated_threshold
            )

    @property
    def validation_positive_support(self) -> int:
        return self.validation_sample_count


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
class EmailActionEligibility:
    action: EmailAction
    auto_action_eligible: bool
    reason: str
    source_model_id: str | None = None
    evidence_reference: str = "email-model-eligibility:unavailable"


@dataclass(frozen=True)
class CategoryEligibility:
    category: EmailCategoryKey
    configured_threshold: float
    validated_precision: float | None
    validation_sample_count: int
    auto_action_eligible: bool
    reason: str
    source_model_id: str | None = None
    validated_recall: float | None = None
    validated_f1: float | None = None
    automatic_candidate_count: int = 0
    evaluated_threshold: float | None = None
    action_eligibility: Mapping[EmailAction, EmailActionEligibility] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        action_eligibility = dict(self.action_eligibility)
        if any(
            action is not eligibility.action
            for action, eligibility in action_eligibility.items()
        ):
            raise ValueError("action eligibility keys must match values")
        object.__setattr__(
            self,
            "action_eligibility",
            MappingProxyType(action_eligibility),
        )

    @property
    def validation_positive_support(self) -> int:
        return self.validation_sample_count


_ACTION_REQUIREMENTS: Mapping[EmailAction, tuple[float, int]] = MappingProxyType(
    {
        EmailAction.LABEL: (0.95, 30),
        EmailAction.MARK_READ: (0.95, 30),
        EmailAction.ARCHIVE: (0.97, 30),
        EmailAction.MOVE: (0.97, 30),
        EmailAction.TRASH: (0.995, 30),
        EmailAction.UNSUBSCRIBE: (0.95, 20),
    }
)


def assess_email_action_eligibility(
    *,
    category: EmailCategoryKey,
    actions: Sequence[EmailAction],
    model_status: str,
    validation_method: str,
    configured_threshold: float,
    evaluated_threshold: float | None,
    validated_precision: float | None,
    validation_positive_support: int,
    metadata_auto_action_eligible: bool,
    source_model_id: str | None = None,
    config_version: str = "unbound-config",
) -> Mapping[EmailAction, EmailActionEligibility]:
    """Evaluate each configured action at the active-model runtime boundary."""

    results: dict[EmailAction, EmailActionEligibility] = {}
    for action in actions:
        if source_model_id is None:
            eligible = False
            reason = "model_eligibility_unbound"
        elif model_status != "active":
            eligible = False
            reason = "model_not_active"
        elif validation_method != "time-ordered-holdout":
            eligible = False
            reason = "time_ordered_validation_required"
        elif evaluated_threshold != configured_threshold:
            eligible = False
            reason = "threshold_changed_since_training"
        elif not metadata_auto_action_eligible:
            eligible = False
            reason = "model_eligibility_missing"
        elif action is EmailAction.AUTO_REPLY:
            eligible = False
            reason = "auto_reply_disabled"
        elif action is EmailAction.UNSUBSCRIBE and category != EmailCategory.JUNK.value:
            eligible = False
            reason = "junk_category_required"
        else:
            requirement = _ACTION_REQUIREMENTS.get(action)
            if requirement is None:
                eligible = False
                reason = "action_not_eligible"
            else:
                minimum_precision, minimum_support = requirement
                precision_met = (
                    validated_precision is not None
                    and validated_precision >= minimum_precision
                )
                support_met = validation_positive_support >= minimum_support
                eligible = precision_met and support_met
                if eligible:
                    reason = "action_precision_and_support_gate_met"
                elif not precision_met and not support_met:
                    reason = "action_precision_and_support_gate_not_met"
                elif not precision_met:
                    reason = "action_precision_gate_not_met"
                else:
                    reason = "action_support_gate_not_met"
        evidence_snapshot = json.dumps(
            {
                "action_type": action.value,
                "category": category,
                "config_version": config_version,
                "configured_threshold": configured_threshold,
                "evaluated_threshold": evaluated_threshold,
                "metadata_auto_action_eligible": metadata_auto_action_eligible,
                "model_status": model_status,
                "reason": reason,
                "source_model_id": source_model_id,
                "validated_precision": validated_precision,
                "validation_method": validation_method,
                "validation_positive_support": validation_positive_support,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_reference = (
            "email-model-eligibility:unavailable"
            if source_model_id is None
            else "email-model-eligibility:"
            + source_model_id
            + ":sha256:"
            + sha256(evidence_snapshot.encode("utf-8")).hexdigest()
        )
        results[action] = EmailActionEligibility(
            action=action,
            auto_action_eligible=eligible,
            reason=reason,
            source_model_id=source_model_id,
            evidence_reference=evidence_reference,
        )
    return MappingProxyType(results)


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
    validation_method: str,
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
        samples_met = (
            validation.validation_sample_count >= requirement.minimum_validation_samples
        )
        threshold_matches = (
            validation.evaluated_threshold is not None
            and validation.evaluated_threshold == requirement.configured_threshold
        )
        has_threshold_metrics = (
            validation.validated_precision is not None
            or validation.validation_sample_count > 0
            or validation.automatic_candidate_count > 0
        )
        if validation_method != "time-ordered-holdout":
            eligible = False
            reason = "time_ordered_validation_required"
        elif has_threshold_metrics and validation.evaluated_threshold is None:
            eligible = False
            reason = "threshold_metrics_missing"
        elif validation.evaluated_threshold is not None and not threshold_matches:
            eligible = False
            reason = "threshold_changed_since_training"
        elif precision_met and samples_met:
            eligible = True
            reason = "precision_and_sample_gate_met"
        elif not precision_met and not samples_met:
            eligible = False
            reason = "precision_and_sample_gate_not_met"
        elif not precision_met:
            eligible = False
            reason = "precision_gate_not_met"
        else:
            eligible = False
            reason = "sample_gate_not_met"
        categories[category] = CategoryEligibility(
            category=category,
            configured_threshold=requirement.configured_threshold,
            validated_precision=validation.validated_precision,
            validation_sample_count=validation.validation_sample_count,
            auto_action_eligible=eligible,
            reason=reason,
            validated_recall=validation.validated_recall,
            validated_f1=validation.validated_f1,
            automatic_candidate_count=validation.automatic_candidate_count,
            evaluated_threshold=validation.evaluated_threshold,
        )
    return CandidateAssessment(promote_model, promotion_reason, categories)


def evaluate_category_validation(
    expected: Sequence[str],
    predictions: Sequence[EmailModelPrediction],
    requirements: Mapping[EmailCategory, EligibilityRequirement],
) -> dict[EmailCategory, CategoryValidation]:
    """Compute action evidence from candidates at each exact configured threshold."""
    if len(expected) != len(predictions):
        raise ValueError("validation labels and predictions must be aligned")
    result: dict[EmailCategory, CategoryValidation] = {}
    for category, requirement in requirements.items():
        label = category.value
        positive_support = sum(actual == label for actual in expected)
        candidates = [
            index
            for index, prediction in enumerate(predictions)
            if prediction.label == label
            and prediction.probability >= requirement.configured_threshold
        ]
        true_positives = sum(expected[index] == label for index in candidates)
        candidate_count = len(candidates)
        precision = float(true_positives / candidate_count) if candidate_count else 0.0
        recall = float(true_positives / positive_support) if positive_support else 0.0
        f1 = (
            0.0
            if precision + recall == 0
            else float(2 * precision * recall / (precision + recall))
        )
        result[category] = CategoryValidation(
            validated_precision=precision,
            validation_sample_count=positive_support,
            validated_recall=recall,
            validated_f1=f1,
            automatic_candidate_count=candidate_count,
            evaluated_threshold=requirement.configured_threshold,
        )
    return result


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
    effective_minimum_per_category = max(2, minimum_per_category)
    counts = dict(Counter(example["label"] for example in examples))
    reasons: list[str] = []
    if len(examples) < minimum_examples:
        reasons.append(f"minimum {minimum_examples} feedback examples required")
    if len(counts) < 2:
        reasons.append("at least two categories are required")
    required_labels = set(INITIAL_EMAIL_CATEGORY_KEYS)
    unknown_labels = sorted(set(counts) - required_labels)
    if unknown_labels:
        reasons.append("unknown email categories: " + ", ".join(unknown_labels))
    underrepresented = sorted(
        label
        for label in required_labels
        if counts.get(label, 0) < effective_minimum_per_category
    )
    if underrepresented:
        reasons.append(
            f"minimum {effective_minimum_per_category} examples per category required: "
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
    training_examples: Sequence[Mapping[str, object]] | None = None,
) -> TrainingResult:
    """Train a candidate, validate its immutable artifact, and promote atomically."""
    if not isinstance(registry, EmailModelRegistry):
        if previous_path is None or model_version is None:
            raise TypeError(
                "path-based promotion requires previous_path and model_version"
            )
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
    examples = list(
        training_examples
        if training_examples is not None
        else store.list_training_examples(include_inclusion=True)
    )
    readiness = assess_examples_readiness(
        examples,
        minimum_examples=minimum_examples,
        minimum_per_category=minimum_per_category,
    )
    if not readiness.ready:
        raise TrainingNotReady("; ".join(readiness.reasons))
    enabled_category_keys = _validated_training_category_keys(examples)
    validation_snapshots = tuple(dict(example) for example in examples)
    inclusion_snapshots = tuple(
        example
        for example in validation_snapshots
        if example["included_in_model_id"] is None
    )
    if not inclusion_snapshots:
        raise TrainingNotReady("no unincluded authoritative feedback")

    validation_method, expected, validation_predictions = _validation_predictions(
        examples,
        c=c,
        enabled_category_keys=enabled_category_keys,
    )
    predicted = [prediction.label for prediction in validation_predictions]
    labels = sorted(readiness.category_counts)
    accuracy = float(accuracy_score(expected, predicted))
    _, _, f1s, _ = precision_recall_fscore_support(
        expected, predicted, labels=labels, zero_division=0
    )
    macro_f1 = float(sum(float(value) for value in f1s) / len(f1s))
    requirements = _default_requirements(labels)
    requirements.update(category_requirements or {})
    per_category_validation = evaluate_category_validation(
        expected,
        validation_predictions,
        requirements,
    )
    assessment = assess_candidate(
        readiness,
        validation_score=accuracy,
        validation_method=validation_method,
        per_category=per_category_validation,
        category_requirements=requirements,
    )

    classifier = CpuTfidfLogisticClassifier(c=c, model_version="candidate")
    classifier.fit(
        [example["model_text"] for example in examples],
        [example["label"] for example in examples],
        enabled_category_keys=enabled_category_keys,
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
            new_sample_count=len(inclusion_snapshots),
            category_counts=readiness.category_counts,
            account_counts=dict(Counter(str(item["account_id"]) for item in examples)),
            validation_method=validation_method,
            accuracy=accuracy,
            macro_f1=macro_f1,
            per_category_metrics={
                label: {
                    "precision": per_category_validation[
                        EmailCategory(label)
                    ].validated_precision,
                    "recall": per_category_validation[
                        EmailCategory(label)
                    ].validated_recall,
                    "f1": per_category_validation[EmailCategory(label)].validated_f1,
                    "validation_sample_count": per_category_validation[
                        EmailCategory(label)
                    ].validation_positive_support,
                    "validation_positive_support": per_category_validation[
                        EmailCategory(label)
                    ].validation_positive_support,
                    "automatic_candidate_count": per_category_validation[
                        EmailCategory(label)
                    ].automatic_candidate_count,
                    "evaluated_threshold": per_category_validation[
                        EmailCategory(label)
                    ].evaluated_threshold,
                    "configured_threshold": assessment.categories[
                        EmailCategory(label)
                    ].configured_threshold,
                    "minimum_precision": requirements[
                        EmailCategory(label)
                    ].minimum_precision,
                    "minimum_validation_samples": requirements[
                        EmailCategory(label)
                    ].minimum_validation_samples,
                    "auto_action_eligible": assessment.categories[
                        EmailCategory(label)
                    ].auto_action_eligible,
                    "eligibility_reason": assessment.categories[
                        EmailCategory(label)
                    ].reason,
                }
                for label in labels
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
    manifest_snapshot = registry.snapshot_manifests()
    try:
        store.commit_training_promotion(
            validation_snapshots,
            inclusion_snapshots=inclusion_snapshots,
            model_id=model_id,
            promote=lambda: registry.promote(
                model_id, reason="candidate_validation_passed"
            ),
            restore=lambda: registry.restore_manifest_snapshot(
                manifest_snapshot,
                failed_model_id=model_id,
                reason="promotion_inclusion_consistency_failed",
            ),
        )
    except Exception as exc:
        if registry.get_model(model_id).status == "candidate":
            registry.mark_failed(
                model_id,
                reason=f"promotion_inclusion_failed:{type(exc).__name__}",
            )
        raise
    return _result(
        metadata, promoted=True, status="active", reason="candidate_validation_passed"
    )


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
    warnings.warn(
        "path-based email model promotion is deprecated; use EmailModelRegistry",
        DeprecationWarning,
        stacklevel=2,
    )
    readiness = assess_feedback_readiness(
        store,
        minimum_examples=minimum_examples,
        minimum_per_category=minimum_per_category,
    )
    if not readiness.ready:
        raise TrainingNotReady("; ".join(readiness.reasons))
    examples = store.list_training_examples()
    enabled_category_keys = _validated_training_category_keys(examples)
    method, expected, predictions = _validation_predictions(
        examples,
        c=c,
        enabled_category_keys=enabled_category_keys,
    )
    accuracy = float(accuracy_score(expected, [item.label for item in predictions]))
    classifier = CpuTfidfLogisticClassifier(c=c, model_version=model_version).fit(
        [item["model_text"] for item in examples],
        [item["label"] for item in examples],
        enabled_category_keys=enabled_category_keys,
    )
    p50, p95 = _prediction_latency(
        classifier, [item["model_text"] for item in examples]
    )
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
        account_counts=dict(
            Counter(_account_id(item["message_id"]) for item in examples)
        ),
        validation_method=method,
        accuracy=accuracy,
        macro_f1=accuracy,
        prediction_latency_p50_ms=p50,
        prediction_latency_p95_ms=p95,
        status="active",
        promotion_reason="prototype_path_promotion",
    )


def _validation_predictions(
    examples: Sequence[Mapping[str, str]],
    *,
    c: float,
    enabled_category_keys: Sequence[EmailCategoryKey],
) -> tuple[str, list[str], list[EmailModelPrediction]]:
    if len(examples) >= 50:
        split = max(1, int(len(examples) * 0.8))
        training = list(examples[:split])
        validation = list(examples[split:])
        if len({item["label"] for item in training}) >= 2 and validation:
            classifier = CpuTfidfLogisticClassifier(c=c, model_version="validation")
            classifier.fit(
                [item["model_text"] for item in training],
                [item["label"] for item in training],
                enabled_category_keys=enabled_category_keys,
            )
            return (
                "time-ordered-holdout",
                [item["label"] for item in validation],
                [classifier.predict(item["model_text"]) for item in validation],
            )
    expected: list[str] = []
    predicted: list[EmailModelPrediction] = []
    for index, example in enumerate(examples):
        training = list(examples[:index]) + list(examples[index + 1 :])
        if len({item["label"] for item in training}) < 2:
            raise TrainingNotReady("leave-one-out fold has fewer than two categories")
        classifier = CpuTfidfLogisticClassifier(c=c, model_version="validation")
        classifier.fit(
            [item["model_text"] for item in training],
            [item["label"] for item in training],
            enabled_category_keys=enabled_category_keys,
        )
        expected.append(example["label"])
        predicted.append(classifier.predict(example["model_text"]))
    return "leave-one-out", expected, predicted


def _validated_training_category_keys(
    examples: Sequence[Mapping[str, object]],
) -> tuple[EmailCategoryKey, ...]:
    validated = {validate_email_category_key(example["label"]) for example in examples}
    return tuple(sorted(validated))


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
        if float(incoming.get("precision", 0.0)) < float(
            previous.get("precision", 0.0)
        ):
            return f"category_precision_regressed:{label}"
    return None


def _default_requirements(
    labels: Sequence[str],
) -> dict[EmailCategory, EligibilityRequirement]:
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
