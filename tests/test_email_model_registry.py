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


def _metadata(*, digest: str, model_id: str) -> EmailModelMetadata:
    return EmailModelMetadata(
        model_id=model_id,
        parent_model_id=None,
        model_family="tfidf-logistic-regression",
        tokenizer_version="jieba-default-v1",
        feature_version=CpuTfidfLogisticClassifier.FEATURE_VERSION,
        training_dataset_version="feedback-sha256:dataset",
        trained_at=TRAINED_AT.isoformat(),
        training_started_at=(TRAINED_AT - timedelta(seconds=2)).isoformat(),
        training_finished_at=TRAINED_AT.isoformat(),
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
                "configured_threshold": 0.85,
                "auto_action_eligible": False,
                "eligibility_reason": "sample_gate_not_met",
            },
            "junk": {
                "precision": 0.7,
                "recall": 0.8,
                "f1": 0.74,
                "validation_sample_count": 2,
                "configured_threshold": 0.85,
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


def _stage(registry: EmailModelRegistry, tmp_path: Path, *, suffix: str = "") -> str:
    source = tmp_path / f"candidate{suffix}.pkl"
    _classifier().save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    model_id = build_model_id(trained_at=TRAINED_AT, artifact_sha256=digest)
    registry.stage_candidate(
        source,
        _metadata(digest=digest, model_id=model_id),
        parity_texts=("work project", "junk offer"),
        expected_labels=("work", "junk"),
    )
    return model_id


def test_model_id_contains_utc_second_and_final_artifact_digest():
    assert build_model_id(
        trained_at=datetime(
            2026, 8, 29, 14, 45, 30, tzinfo=timezone(timedelta(hours=-7))
        ),
        artifact_sha256="7f3a91c2" + "0" * 56,
    ) == "email-tfidf-lr-20260829T214530Z-7f3a91c2"


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
    assert sha256(record.artifact_path.read_bytes()).hexdigest() == record.metadata.artifact_sha256
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


def test_promote_switches_small_manifests_and_preserves_previous_artifacts(tmp_path: Path):
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
        {**metadata.to_dict(), "trained_at": later.isoformat(), "parent_model_id": first}
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
    registry.stage_candidate(
        source,
        slow,
        parity_texts=("work project",),
        expected_labels=("work",),
    )
    registry.reject(model_id, reason="latency_p95_exceeded")
    assert registry.get_model(model_id).status == "rejected"
