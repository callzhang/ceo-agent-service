from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_model_registry import (
    EmailModelMetadata,
    EmailModelRegistry,
    ModelRegistryError,
    build_model_id,
)


TRAINED_AT = datetime(2026, 8, 29, 21, 45, 30, tzinfo=timezone.utc)


def _classifier(version: str = "candidate") -> CpuTfidfLogisticClassifier:
    return CpuTfidfLogisticClassifier(model_version=version).fit(
        ["work project", "work meeting", "junk offer", "junk promotion"],
        ["work", "work", "junk", "junk"],
    )


def _metadata(
    *,
    digest: str,
    model_id: str,
    trained_at: datetime = TRAINED_AT,
) -> EmailModelMetadata:
    return EmailModelMetadata(
        model_id=model_id,
        parent_model_id=None,
        model_family="tfidf-logistic-regression",
        tokenizer_version="jieba-default-v1",
        feature_version=CpuTfidfLogisticClassifier.FEATURE_VERSION,
        training_dataset_version="feedback-sha256:dataset",
        trained_at=trained_at.isoformat(),
        training_started_at=(trained_at - timedelta(seconds=2)).isoformat(),
        training_finished_at=trained_at.isoformat(),
        sample_count=4,
        new_sample_count=4,
        category_counts={"work": 2, "junk": 2},
        account_counts={"account-a": 3, "account-b": 1},
        validation_method="leave-one-out",
        accuracy=0.75,
        macro_f1=0.73,
        per_category_metrics={
            "work": {
                "precision": 0.8,
                "recall": 0.7,
                "f1": 0.75,
                "validation_sample_count": 2,
                "validation_positive_support": 2,
                "automatic_candidate_count": 0,
                "evaluated_threshold": 0.85,
                "configured_threshold": 0.85,
                "minimum_precision": 0.95,
                "minimum_validation_samples": 30,
                "auto_action_eligible": False,
                "eligibility_reason": "sample_gate_not_met",
            },
            "junk": {
                "precision": 0.7,
                "recall": 0.8,
                "f1": 0.74,
                "validation_sample_count": 2,
                "validation_positive_support": 2,
                "automatic_candidate_count": 0,
                "evaluated_threshold": 0.85,
                "configured_threshold": 0.85,
                "minimum_precision": 0.95,
                "minimum_validation_samples": 30,
                "auto_action_eligible": False,
                "eligibility_reason": "sample_gate_not_met",
            },
        },
        prediction_latency_p50_ms=0.4,
        prediction_latency_p95_ms=0.7,
        artifact_sha256=digest,
        status="candidate",
        promotion_reason="candidate_validation_pending",
        failure_reason="",
    )


def _stage(
    registry: EmailModelRegistry,
    tmp_path: Path,
    *,
    suffix: str = "",
    trained_at: datetime = TRAINED_AT,
) -> str:
    source = tmp_path / f"candidate{suffix}.pkl"
    _classifier().save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    model_id = build_model_id(trained_at=trained_at, artifact_sha256=digest)
    registry.stage_candidate(
        source,
        _metadata(digest=digest, model_id=model_id, trained_at=trained_at),
        parity_texts=("work project", "junk offer"),
        expected_labels=("work", "junk"),
    )
    return model_id


def test_list_models_returns_every_validated_record_newest_first(tmp_path: Path):
    registry = EmailModelRegistry(tmp_path / "registry")
    rejected = _stage(registry, tmp_path, suffix="-rejected")
    registry.reject(rejected, reason="macro_f1_regressed")
    failed = _stage(
        registry,
        tmp_path,
        suffix="-failed",
        trained_at=TRAINED_AT + timedelta(seconds=1),
    )
    registry.mark_failed(failed, reason="artifact_reload_failed")
    candidate = _stage(
        registry,
        tmp_path,
        suffix="-candidate",
        trained_at=TRAINED_AT + timedelta(seconds=2),
    )

    records = registry.list_models()

    assert [record.metadata.model_id for record in records] == [
        candidate,
        failed,
        rejected,
    ]
    assert [(record.status, record.status_reason) for record in records] == [
        ("candidate", "candidate_validation_pending"),
        ("failed", "artifact_reload_failed"),
        ("rejected", "macro_f1_regressed"),
    ]


def test_list_models_fails_closed_when_artifact_digest_is_corrupt(tmp_path: Path):
    registry = EmailModelRegistry(tmp_path / "registry")
    model_id = _stage(registry, tmp_path)
    registry.get_model(model_id).artifact_path.write_bytes(b"corrupt")

    with pytest.raises(ModelRegistryError, match="artifact digest verification failed"):
        registry.list_models()


def test_model_inventory_keeps_healthy_records_visible_with_corrupt_history(
    tmp_path: Path,
):
    registry = EmailModelRegistry(tmp_path / "registry")
    healthy = _stage(registry, tmp_path, trained_at=TRAINED_AT)
    corrupt = _stage(
        registry,
        tmp_path,
        suffix="-corrupt",
        trained_at=TRAINED_AT + timedelta(seconds=1),
    )
    registry.get_model(corrupt).artifact_path.write_bytes(b"corrupt")

    inventory = registry.list_model_inventory()

    assert [entry.model_id for entry in inventory] == [corrupt, healthy]
    assert inventory[0].integrity_status == "corrupt"
    assert inventory[0].integrity_error == "artifact_digest_mismatch"
    assert inventory[0].metadata is not None
    assert inventory[1].integrity_status == "verified"
    assert inventory[1].record is not None


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing_artifact", "artifact_missing"),
        ("malformed_metadata", "metadata_invalid"),
        ("malformed_lifecycle", "lifecycle_invalid"),
    ],
)
def test_model_inventory_reports_per_record_integrity_failures(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
):
    registry = EmailModelRegistry(tmp_path / mutation)
    model_id = _stage(registry, tmp_path, suffix=f"-{mutation}")
    record = registry.get_model(model_id)
    if mutation == "missing_artifact":
        record.artifact_path.unlink()
    elif mutation == "malformed_metadata":
        record.metadata_path.write_text("{not-json", encoding="utf-8")
    else:
        lifecycle_path = next(registry.lifecycle.glob(f"{model_id}-*.json"))
        lifecycle_path.write_text("{not-json", encoding="utf-8")

    inventory = registry.list_model_inventory()

    assert len(inventory) == 1
    assert inventory[0].model_id == model_id
    assert inventory[0].integrity_status == "corrupt"
    assert inventory[0].integrity_error == expected_error


def test_model_id_contains_utc_second_and_final_artifact_digest():
    assert (
        build_model_id(
            trained_at=datetime(
                2026, 8, 29, 14, 45, 30, tzinfo=timezone(timedelta(hours=-7))
            ),
            artifact_sha256="7f3a91c2" + "0" * 56,
        )
        == "email-tfidf-lr-20260829T214530Z-7f3a91c2"
    )


def test_stage_candidate_writes_immutable_artifact_metadata_and_reload_parity(
    tmp_path: Path,
):
    registry = EmailModelRegistry(tmp_path / "registry")
    model_id = _stage(registry, tmp_path)

    record = registry.get_model(model_id)
    assert record.status == "candidate"
    assert record.artifact_path.name == f"{model_id}.pkl"
    assert record.metadata.sample_count == 4
    assert record.metadata.account_counts == {"account-a": 3, "account-b": 1}
    assert record.metadata.per_category_metrics["work"]["auto_action_eligible"] is False
    assert (
        sha256(record.artifact_path.read_bytes()).hexdigest()
        == record.metadata.artifact_sha256
    )
    loaded = registry.load_classifier(model_id)
    assert loaded.model_version == model_id
    assert loaded.predict("work project").label == "work"

    with pytest.raises(ModelRegistryError, match="already exists"):
        registry.stage_candidate(
            record.artifact_path,
            record.metadata,
            parity_texts=("work project",),
            expected_labels=("work",),
        )


def test_legacy_metrics_remain_readable_but_are_forced_ineligible():
    source = _metadata(
        digest="a" * 64,
        model_id="email-tfidf-lr-20260829T214530Z-aaaaaaaa",
    )
    mapping = source.to_dict()
    metrics = {key: dict(value) for key, value in source.per_category_metrics.items()}
    for metric in metrics.values():
        metric.pop("validation_positive_support")
        metric.pop("automatic_candidate_count")
        metric.pop("evaluated_threshold")
        metric.pop("minimum_precision")
        metric["auto_action_eligible"] = True
    mapping["per_category_metrics"] = metrics

    loaded = EmailModelMetadata.from_mapping(mapping)

    for metric in loaded.per_category_metrics.values():
        assert metric["auto_action_eligible"] is False
        assert metric["eligibility_reason"] == "threshold_metrics_missing"
        assert metric["automatic_candidate_count"] == 0
        assert (
            metric["validation_positive_support"] == metric["validation_sample_count"]
        )


def test_promote_switches_small_manifests_and_preserves_previous_artifacts(
    tmp_path: Path,
):
    registry = EmailModelRegistry(tmp_path / "registry")
    first = _stage(registry, tmp_path, suffix="-first")
    registry.promote(first, reason="initial_candidate_passed")

    first_manifest = registry.active_manifest()
    first_artifact = registry.get_model(first).artifact_path.read_bytes()
    assert first_manifest is not None and first_manifest.model_id == first
    assert registry.get_model(first).status == "active"

    later = TRAINED_AT + timedelta(seconds=1)
    source = tmp_path / "candidate-second.pkl"
    _classifier().fit(
        ["work roadmap", "work sprint", "junk discount", "junk advertising"],
        ["work", "work", "junk", "junk"],
    ).save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    second = build_model_id(trained_at=later, artifact_sha256=digest)
    metadata = _metadata(digest=digest, model_id=second)
    metadata = EmailModelMetadata.from_mapping(
        {
            **metadata.to_dict(),
            "trained_at": later.isoformat(),
            "parent_model_id": first,
        }
    )
    registry.stage_candidate(
        source,
        metadata,
        parity_texts=("work roadmap", "junk discount"),
        expected_labels=("work", "junk"),
    )
    registry.promote(second, reason="validated_candidate_passed")

    assert registry.active_manifest().model_id == second  # type: ignore[union-attr]
    assert registry.previous_manifest().model_id == first  # type: ignore[union-attr]
    assert registry.get_model(second).status == "active"
    assert registry.get_model(first).status == "previous"
    assert registry.get_model(first).artifact_path.read_bytes() == first_artifact
    assert json.loads((registry.root / "active.json").read_text())["model_id"] == second


def test_rejected_candidate_and_runtime_fallback_leave_history_durable(tmp_path: Path):
    registry = EmailModelRegistry(tmp_path / "registry")
    first = _stage(registry, tmp_path, suffix="-first")
    registry.promote(first, reason="initial")

    source = tmp_path / "candidate-second.pkl"
    _classifier().fit(
        ["work one", "work two", "junk one", "junk two"],
        ["work", "work", "junk", "junk"],
    ).save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    second_time = TRAINED_AT + timedelta(seconds=2)
    second = build_model_id(trained_at=second_time, artifact_sha256=digest)
    metadata = EmailModelMetadata.from_mapping(
        {
            **_metadata(digest=digest, model_id=second).to_dict(),
            "trained_at": second_time.isoformat(),
            "parent_model_id": first,
        }
    )
    registry.stage_candidate(
        source,
        metadata,
        parity_texts=("work one",),
        expected_labels=("work",),
    )
    registry.reject(second, reason="latency_p95_exceeded")
    assert registry.get_model(second).status == "rejected"
    assert registry.active_manifest().model_id == first  # type: ignore[union-attr]

    # Promote a second verified model, then atomically restore the verified previous.
    third_time = TRAINED_AT + timedelta(seconds=3)
    third = build_model_id(trained_at=third_time, artifact_sha256=digest)
    third_metadata = EmailModelMetadata.from_mapping(
        {
            **metadata.to_dict(),
            "model_id": third,
            "trained_at": third_time.isoformat(),
            "parent_model_id": first,
        }
    )
    third_source = tmp_path / "candidate-third.pkl"
    third_source.write_bytes(source.read_bytes())
    registry.stage_candidate(
        third_source,
        third_metadata,
        parity_texts=("work one",),
        expected_labels=("work",),
    )
    registry.promote(third, reason="validated")
    restored = registry.fallback_to_previous(
        reason="active_prediction_failed_repeatedly",
        failed_model_id=third,
    )

    assert restored.model_id == first
    assert registry.active_manifest().model_id == first  # type: ignore[union-attr]
    assert registry.get_model(third).status == "failed"
    events = registry.list_runtime_failures()
    assert events[-1].failed_model_id == third
    assert events[-1].fallback_model_id == first
    assert events[-1].reason == "active_prediction_failed_repeatedly"


def test_candidate_protocol_rejects_unknown_labels_and_slow_latency(tmp_path: Path):
    registry = EmailModelRegistry(tmp_path / "registry")
    source = tmp_path / "candidate.pkl"
    _classifier().save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    model_id = build_model_id(trained_at=TRAINED_AT, artifact_sha256=digest)

    invalid = EmailModelMetadata.from_mapping(
        {
            **_metadata(digest=digest, model_id=model_id).to_dict(),
            "category_counts": {"not-a-category": 4},
        }
    )
    with pytest.raises(ModelRegistryError, match="category protocol"):
        registry.stage_candidate(
            source,
            invalid,
            parity_texts=("work project",),
            expected_labels=("work",),
        )

    slow = EmailModelMetadata.from_mapping(
        {
            **_metadata(digest=digest, model_id=model_id).to_dict(),
            "prediction_latency_p95_ms": 100.0,
        }
    )
    slow_registry = EmailModelRegistry(tmp_path / "slow-registry")
    slow_registry.stage_candidate(
        source,
        slow,
        parity_texts=("work project",),
        expected_labels=("work",),
    )
    slow_registry.reject(model_id, reason="latency_p95_exceeded")
    assert slow_registry.get_model(model_id).status == "rejected"


def test_candidate_protocol_requires_exact_artifact_metadata_and_metric_shape(
    tmp_path: Path,
):
    source = tmp_path / "candidate-exact.pkl"
    _classifier().save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    model_id = build_model_id(trained_at=TRAINED_AT, artifact_sha256=digest)
    base = _metadata(digest=digest, model_id=model_id)

    mismatched = EmailModelMetadata.from_mapping(
        {**base.to_dict(), "category_counts": {"work": 4}}
    )
    registry = EmailModelRegistry(tmp_path / "mismatch-registry")
    with pytest.raises(ModelRegistryError, match="category protocol mismatch"):
        registry.stage_candidate(
            source,
            mismatched,
            parity_texts=("work project",),
            expected_labels=("work",),
        )
    assert registry.get_model(model_id).status == "failed"

    metrics = {key: dict(value) for key, value in base.per_category_metrics.items()}
    metrics["work"].pop("minimum_validation_samples")
    malformed = EmailModelMetadata.from_mapping(
        {**base.to_dict(), "per_category_metrics": metrics}
    )
    malformed_registry = EmailModelRegistry(tmp_path / "metric-registry")
    with pytest.raises(ModelRegistryError, match="metrics protocol mismatch"):
        malformed_registry.stage_candidate(
            source,
            malformed,
            parity_texts=("work project",),
            expected_labels=("work",),
        )
    assert malformed_registry.get_model(model_id).status == "failed"

    unsafe_metrics = {
        key: {**value, "auto_action_eligible": True}
        for key, value in base.per_category_metrics.items()
    }
    unsafe = EmailModelMetadata.from_mapping(
        {**base.to_dict(), "per_category_metrics": unsafe_metrics}
    )
    unsafe_registry = EmailModelRegistry(tmp_path / "unsafe-eligibility-registry")
    with pytest.raises(ModelRegistryError, match="eligibility protocol mismatch"):
        unsafe_registry.stage_candidate(
            source,
            unsafe,
            parity_texts=("work project",),
            expected_labels=("work",),
        )
    assert unsafe_registry.get_model(model_id).status == "failed"
