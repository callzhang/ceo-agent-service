from datetime import datetime, timezone
from pathlib import Path

from app.email_classifier_contracts import EmailCategory
from app.email_classifier_shadow import stage_snapshot_shadow_candidate
from app.email_classifier_training import EligibilityRequirement
from app.email_experiment_snapshot import build_snapshot, save_snapshot
from app.email_model_registry import EmailModelRegistry


TRAINED_AT = datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc)


def _snapshot(
    path: Path,
    rows: list[tuple[str, str, str, str]],
) -> Path:
    snapshot = build_snapshot(
        [
            {
                "message_id": message_id,
                "model_text": model_text,
                "label": label,
                "received_at": received_at,
                "source_group": source_group,
            }
            for message_id, model_text, label, source_group in rows
            for received_at in ("2026-09-02T12:00:00+00:00",)
        ],
        captured_at=TRAINED_AT,
        label_source="assistant_authorized_manual_annotation",
    )
    save_snapshot(path, snapshot)
    return path


def test_snapshot_shadow_candidate_is_versioned_but_never_activated(tmp_path: Path):
    training = _snapshot(
        tmp_path / "training.json",
        [
            ("train-n-1", "__subject__安全 验证 登录 通知", "notification", "alerts"),
            ("train-n-2", "__subject__安全 验证 设备 通知", "notification", "alerts"),
            ("train-n-3", "__subject__系统 状态 服务 通知", "notification", "status"),
            ("train-j-1", "__subject__促销 广告 优惠", "junk", "ads"),
            ("train-j-2", "__subject__促销 折扣 营销", "junk", "ads"),
            ("train-j-3", "__subject__推广 广告 营销", "junk", "marketing"),
        ],
    )
    validation = _snapshot(
        tmp_path / "validation.json",
        [
            # Deliberately repeated across train/validation. The shadow path must
            # exclude it and record the exclusion instead of leaking it.
            ("train-j-1", "__subject__促销 广告 优惠", "junk", "ads"),
            ("valid-n-1", "__subject__安全 登录 验证 通知", "notification", "alerts"),
            ("valid-n-2", "__subject__系统 服务 状态 通知", "notification", "status"),
            ("valid-j-1", "__subject__广告 推广 优惠", "junk", "marketing"),
        ],
    )
    registry = EmailModelRegistry(tmp_path / "registry")

    result = stage_snapshot_shadow_candidate(
        registry,
        training_snapshot_paths=(training,),
        validation_snapshot_paths=(validation,),
        account_id="dingtalk_primary",
        category_requirements={
            EmailCategory.NOTIFICATION: EligibilityRequirement(
                configured_threshold=0.25,
                minimum_precision=0.95,
                minimum_validation_samples=30,
            ),
            EmailCategory.JUNK: EligibilityRequirement(
                configured_threshold=0.85,
                minimum_precision=0.995,
                minimum_validation_samples=30,
            ),
        },
        trained_at=TRAINED_AT,
    )

    assert result.training_sample_count == 6
    assert result.validation_sample_count == 3
    assert result.excluded_validation_duplicates == 1
    assert result.model_id.startswith("email-tfidf-lr-20260902T210000Z-")
    assert registry.active_manifest() is None

    record = registry.get_model(result.model_id)
    assert record.status == "candidate"
    assert record.status_reason == "shadow_only_non_authoritative_labels"
    assert record.metadata.sample_count == 6
    assert record.metadata.new_sample_count == 6
    assert record.metadata.account_counts == {"dingtalk_primary": 6}
    assert record.metadata.validation_method == "time-ordered-shadow-holdout"
    assert record.metadata.training_dataset_version.startswith(
        "shadow-assistant_authorized_manual_annotation-sha256:"
    )
    notification = record.metadata.per_category_metrics["notification"]
    assert notification["validation_positive_support"] == 2
    assert notification["automatic_candidate_count"] == 2
    assert notification["precision"] == 1.0
    assert notification["auto_action_eligible"] is False
    assert notification["eligibility_reason"] == "non_authoritative_validation_labels"
    assert registry.load_classifier(result.model_id).model_version == result.model_id
