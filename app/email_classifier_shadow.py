"""Stage privacy-bounded classifier candidates without activating them."""

from __future__ import annotations

import math
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sklearn.metrics import accuracy_score, precision_recall_fscore_support

from app.email_classifier_contracts import EmailCategory
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_training import (
    EligibilityRequirement,
    evaluate_category_validation,
)
from app.email_experiment_snapshot import load_snapshot
from app.email_model_registry import (
    MODEL_FAMILY,
    EmailModelMetadata,
    EmailModelRegistry,
    build_model_id,
)


ASSISTANT_SHADOW_LABEL_SOURCE = "assistant_authorized_manual_annotation"


@dataclass(frozen=True)
class ShadowCandidateResult:
    model_id: str
    training_sample_count: int
    validation_sample_count: int
    excluded_validation_duplicates: int
    accuracy: float
    macro_f1: float
    prediction_latency_p50_ms: float
    prediction_latency_p95_ms: float


def stage_snapshot_shadow_candidate(
    registry: EmailModelRegistry,
    *,
    training_snapshot_paths: Sequence[str | Path],
    validation_snapshot_paths: Sequence[str | Path],
    account_id: str,
    category_requirements: Mapping[EmailCategory, EligibilityRequirement],
    trained_at: datetime | None = None,
    c: float = 0.25,
) -> ShadowCandidateResult:
    """Train and register a non-authoritative candidate without an active manifest."""

    account = account_id.strip()
    if not account:
        raise ValueError("account_id must be non-empty")
    training, training_digests = _load_training_rows(training_snapshot_paths)
    validation, excluded = _load_validation_rows(
        validation_snapshot_paths,
        training_digests=training_digests,
    )
    if len({row["label"] for row in training}) < 2:
        raise ValueError("shadow training requires at least two categories")
    if not validation:
        raise ValueError("shadow validation must contain unseen examples")
    labels = sorted({row["label"] for row in training})
    validation_labels = {row["label"] for row in validation}
    if not validation_labels <= set(labels):
        raise ValueError("shadow validation contains an untrained category")
    requirement_labels = {category.value for category in category_requirements}
    if requirement_labels != set(labels):
        raise ValueError("category_requirements must match shadow training categories")

    timestamp = trained_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("trained_at must be timezone-aware")
    timestamp = timestamp.astimezone(timezone.utc)
    classifier = CpuTfidfLogisticClassifier(c=c, model_version="shadow-candidate")
    classifier.fit(
        [row["model_text"] for row in training],
        [row["label"] for row in training],
    )
    predictions = [classifier.predict(row["model_text"]) for row in validation]
    expected = [row["label"] for row in validation]
    accuracy = float(accuracy_score(expected, [item.label for item in predictions]))
    _, _, f1s, _ = precision_recall_fscore_support(
        expected,
        [item.label for item in predictions],
        labels=labels,
        zero_division=0,
    )
    macro_f1 = float(sum(float(value) for value in f1s) / len(f1s))
    category_validation = evaluate_category_validation(
        expected,
        predictions,
        category_requirements,
    )
    p50, p95 = _prediction_latency(
        classifier,
        [row["model_text"] for row in validation],
    )

    with tempfile.TemporaryDirectory(dir=registry.root) as temporary_directory:
        candidate_path = Path(temporary_directory) / "candidate.pkl"
        classifier.save(candidate_path)
        artifact_digest = sha256(candidate_path.read_bytes()).hexdigest()
        model_id = build_model_id(
            trained_at=timestamp,
            artifact_sha256=artifact_digest,
        )
        active = registry.active_manifest()
        metadata = EmailModelMetadata(
            model_id=model_id,
            parent_model_id=active.model_id if active else None,
            model_family=MODEL_FAMILY,
            tokenizer_version="jieba-default-v1",
            feature_version=CpuTfidfLogisticClassifier.FEATURE_VERSION,
            training_dataset_version=_dataset_version(training),
            trained_at=timestamp.isoformat(),
            training_started_at=timestamp.isoformat(),
            training_finished_at=timestamp.isoformat(),
            sample_count=len(training),
            new_sample_count=len(training),
            category_counts=dict(Counter(row["label"] for row in training)),
            account_counts={account: len(training)},
            validation_method="time-ordered-shadow-holdout",
            accuracy=accuracy,
            macro_f1=macro_f1,
            per_category_metrics={
                label: {
                    "precision": category_validation[
                        EmailCategory(label)
                    ].validated_precision,
                    "recall": category_validation[
                        EmailCategory(label)
                    ].validated_recall,
                    "f1": category_validation[EmailCategory(label)].validated_f1,
                    "validation_sample_count": category_validation[
                        EmailCategory(label)
                    ].validation_positive_support,
                    "validation_positive_support": category_validation[
                        EmailCategory(label)
                    ].validation_positive_support,
                    "automatic_candidate_count": category_validation[
                        EmailCategory(label)
                    ].automatic_candidate_count,
                    "evaluated_threshold": category_validation[
                        EmailCategory(label)
                    ].evaluated_threshold,
                    "configured_threshold": category_requirements[
                        EmailCategory(label)
                    ].configured_threshold,
                    "minimum_precision": category_requirements[
                        EmailCategory(label)
                    ].minimum_precision,
                    "minimum_validation_samples": category_requirements[
                        EmailCategory(label)
                    ].minimum_validation_samples,
                    "auto_action_eligible": False,
                    "eligibility_reason": "non_authoritative_validation_labels",
                }
                for label in labels
            },
            prediction_latency_p50_ms=p50,
            prediction_latency_p95_ms=p95,
            artifact_sha256=artifact_digest,
            status="candidate",
            promotion_reason="shadow_only_non_authoritative_labels",
            failure_reason="",
        )
        parity_texts = tuple(row["model_text"] for row in training)
        registry.stage_candidate(
            candidate_path,
            metadata,
            parity_texts=parity_texts,
            expected_labels=tuple(classifier.predict(text).label for text in parity_texts),
        )

    return ShadowCandidateResult(
        model_id=model_id,
        training_sample_count=len(training),
        validation_sample_count=len(validation),
        excluded_validation_duplicates=excluded,
        accuracy=accuracy,
        macro_f1=macro_f1,
        prediction_latency_p50_ms=p50,
        prediction_latency_p95_ms=p95,
    )


def _load_training_rows(
    paths: Sequence[str | Path],
) -> tuple[list[dict[str, str]], set[str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths:
        snapshot = load_snapshot(path)
        _require_shadow_source(snapshot.label_source)
        for example in snapshot.examples:
            sample_digest = example["sample_id_digest"]
            if sample_digest in seen:
                raise ValueError("duplicate shadow training example")
            seen.add(sample_digest)
            rows.append(dict(example))
    if not rows:
        raise ValueError("shadow training snapshots must not be empty")
    return rows, seen


def _load_validation_rows(
    paths: Sequence[str | Path],
    *,
    training_digests: set[str],
) -> tuple[list[dict[str, str]], int]:
    rows: list[dict[str, str]] = []
    seen = set(training_digests)
    excluded = 0
    for path in paths:
        snapshot = load_snapshot(path)
        _require_shadow_source(snapshot.label_source)
        for example in snapshot.examples:
            sample_digest = example["sample_id_digest"]
            if sample_digest in seen:
                excluded += 1
                continue
            seen.add(sample_digest)
            rows.append(dict(example))
    return rows, excluded


def _require_shadow_source(label_source: str) -> None:
    if label_source != ASSISTANT_SHADOW_LABEL_SOURCE:
        raise ValueError("shadow snapshots must use assistant-authorized labels")


def _dataset_version(rows: Sequence[Mapping[str, str]]) -> str:
    digest = sha256()
    for row in rows:
        digest.update(row["sample_id_digest"].encode("ascii"))
        digest.update(b"\0")
        digest.update(row["label"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(row["model_text"].encode("utf-8")).digest())
    return f"shadow-{ASSISTANT_SHADOW_LABEL_SOURCE}-sha256:{digest.hexdigest()}"


def _prediction_latency(
    classifier: CpuTfidfLogisticClassifier,
    texts: Sequence[str],
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
    return _percentile(timings, 0.50), _percentile(timings, 0.95)


def _percentile(values: Sequence[float], fraction: float) -> float:
    index = max(0, math.ceil(len(values) * fraction) - 1)
    return float(values[index])
