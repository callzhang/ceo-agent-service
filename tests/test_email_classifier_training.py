from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from app.email_classifier_contracts import (
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
)
from app.email_classifier_training import (
    CandidateAssessment,
    CategoryEligibility,
    CategoryValidation,
    EligibilityRequirement,
    TrainingNotReady,
    TrainingReadiness,
    assess_candidate,
    assess_feedback_readiness,
    train_and_promote,
)
from app.email_model_registry import EmailModelRegistry
from app.email_classifier_retrain import (
    RetrainPolicy,
    RetrainState,
    evaluate_retrain,
    load_retrain_state,
    retrain_if_due,
    save_retrain_state,
)
from app.email_store import EmailStore, EmailTrainingInclusionConflict


def _assess_important(
    *,
    readiness: bool = True,
    validation_score: float = 0.61,
    validated_precision: float | None = 0.95,
    validation_sample_count: int = 30,
    include_metrics: bool = True,
) -> CandidateAssessment:
    per_category = (
        {
            EmailCategory.IMPORTANT: CategoryValidation(
                validated_precision=validated_precision,
                validation_sample_count=validation_sample_count,
            )
        }
        if include_metrics
        else {}
    )
    return assess_candidate(
        TrainingReadiness(
            ready=readiness,
            example_count=73,
            category_counts={"important": 8, "work": 10, "junk": 18},
            reasons=() if readiness else ("feedback not ready",),
        ),
        validation_score=validation_score,
        per_category=per_category,
        category_requirements={
            EmailCategory.IMPORTANT: EligibilityRequirement(
                configured_threshold=0.85,
                minimum_precision=0.95,
                minimum_validation_samples=30,
            )
        },
    )


def _classification(message_id: str, category: EmailCategory) -> EmailClassification:
    classification_id = int.from_bytes(
        sha256(message_id.encode("utf-8")).digest()[:8], "big"
    ) & ((1 << 63) - 1) or 1
    return EmailClassification(
        classification_id=classification_id,
        stable_message_identity=f"test-account:imap:INBOX:1:{classification_id}",
        provider_locator=EmailProviderLocator(
            account_id="test-account",
            folder="INBOX",
            uidvalidity=1,
            uid=classification_id,
        ),
        category=category,
        confidence=0.61,
        margin=0.11,
        probabilities={category.value: 0.61},
        model_id="email/logistic/model-before-feedback",
        config_version="email-v1",
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        classification_source="model",
        action_plan=None,
    )


def _confirm(
    store: EmailStore,
    row_id: int,
    category: EmailCategory,
) -> dict[str, object] | None:
    return store.confirm_classification(
        row_id,
        category,
        feedback_request_id=f"training-feedback-{row_id}-{category.value}",
        expected_current_action_plan_id=None,
    )


def _store_with_confirmed_feedback(tmp_path: Path) -> EmailStore:
    store = EmailStore(tmp_path / "email.sqlite3")
    examples = [
        ("work-1", EmailCategory.WORK, "__from_domain__work.test __subject__项目 工作"),
        ("work-2", EmailCategory.WORK, "__from_domain__work.test __subject__项目 会议"),
        ("work-3", EmailCategory.WORK, "__from_domain__work.test __subject__项目 计划"),
        ("junk-1", EmailCategory.JUNK, "__from_domain__ads.test __subject__促销 优惠"),
        ("junk-2", EmailCategory.JUNK, "__from_domain__ads.test __subject__促销 折扣"),
        ("junk-3", EmailCategory.JUNK, "__from_domain__ads.test __subject__促销 广告"),
    ]
    for message_id, category, model_text in examples:
        row = store.upsert_classification(
            _classification(message_id, category),
            sender="sender@example.test",
            subject="redacted",
            preview="redacted",
            model_text=model_text,
        )
        assert _confirm(store, row["id"], category) is not None
    return store


def test_feedback_keeps_redacted_model_text_for_training(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    classification = _classification("message-1", EmailCategory.WORK)
    row = store.upsert_classification(
        classification,
        model_text="__from_domain__example.test __subject__项目 工作",
    )

    _confirm(store, row["id"], EmailCategory.IMPORTANT)

    assert store.list_training_examples() == [
        {
            "message_id": classification.stable_message_identity,
            "model_text": "__from_domain__example.test __subject__项目 工作",
            "label": "important",
        }
    ]


def test_training_readiness_requires_two_examples_per_category(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    row = store.upsert_classification(
        _classification("message-1", EmailCategory.WORK), model_text="work"
    )
    _confirm(store, row["id"], EmailCategory.WORK)

    readiness = assess_feedback_readiness(store)

    assert readiness.ready is False
    assert readiness.example_count == 1
    assert "at least two categories" in " ".join(readiness.reasons)


def test_model_promotion_does_not_imply_category_action_eligibility():
    assessment = assess_candidate(
        TrainingReadiness(
            ready=True,
            example_count=73,
            category_counts={"important": 8, "work": 10, "junk": 18},
            reasons=(),
        ),
        validation_score=0.61,
        per_category={
            EmailCategory.IMPORTANT: CategoryValidation(
                validated_precision=0.84,
                validation_sample_count=19,
            )
        },
        category_requirements={
            EmailCategory.IMPORTANT: EligibilityRequirement(
                configured_threshold=0.85,
                minimum_precision=0.95,
                minimum_validation_samples=30,
            )
        },
    )

    assert assessment.promote_model is True
    important = assessment.categories[EmailCategory.IMPORTANT]
    assert important.auto_action_eligible is False
    assert important.validated_precision == 0.84
    assert important.validation_sample_count == 19
    assert important.reason == "precision_and_sample_gate_not_met"


@pytest.mark.parametrize(
    ("precision", "sample_count", "eligible", "reason"),
    [
        (0.96, 31, True, "precision_and_sample_gate_met"),
        (0.94, 31, False, "precision_gate_not_met"),
        (0.96, 29, False, "sample_gate_not_met"),
        (0.94, 29, False, "precision_and_sample_gate_not_met"),
    ],
)
def test_category_eligibility_truth_table(
    precision: float,
    sample_count: int,
    eligible: bool,
    reason: str,
):
    assessment = _assess_important(
        validated_precision=precision,
        validation_sample_count=sample_count,
    )

    result = assessment.categories[EmailCategory.IMPORTANT]
    assert result.auto_action_eligible is eligible
    assert result.reason == reason


def test_category_eligibility_accepts_exact_precision_and_sample_boundaries():
    assessment = _assess_important(
        validated_precision=0.95,
        validation_sample_count=30,
    )

    result = assessment.categories[EmailCategory.IMPORTANT]
    assert result.auto_action_eligible is True
    assert result.reason == "precision_and_sample_gate_met"


def test_missing_category_metrics_fail_closed():
    assessment = _assess_important(include_metrics=False)

    result = assessment.categories[EmailCategory.IMPORTANT]
    assert result.auto_action_eligible is False
    assert result.validated_precision is None
    assert result.validation_sample_count == 0
    assert result.reason == "precision_and_sample_gate_not_met"


def test_missing_category_precision_fails_closed():
    assessment = _assess_important(
        validated_precision=None,
        validation_sample_count=30,
    )

    result = assessment.categories[EmailCategory.IMPORTANT]
    assert result.auto_action_eligible is False
    assert result.reason == "precision_gate_not_met"


def test_feedback_readiness_prevents_model_promotion():
    assessment = _assess_important(readiness=False)

    assert assessment.promote_model is False
    assert assessment.promotion_reason == "feedback_not_ready"
    assert assessment.categories[EmailCategory.IMPORTANT].auto_action_eligible is True


@pytest.mark.parametrize(
    "validation_score",
    [float("nan"), float("inf"), float("-inf"), -0.01, 1.01, True, 1],
)
def test_global_validation_score_must_be_a_finite_float_in_unit_interval(
    validation_score: object,
):
    with pytest.raises(ValueError, match="validation_score"):
        _assess_important(validation_score=validation_score)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "validated_precision",
    [float("nan"), float("inf"), float("-inf"), -0.01, 1.01, True, 1],
)
def test_category_precision_must_be_a_finite_float_in_unit_interval(
    validated_precision: object,
):
    with pytest.raises(ValueError, match="validated_precision"):
        CategoryValidation(
            validated_precision=validated_precision,  # type: ignore[arg-type]
            validation_sample_count=30,
        )


@pytest.mark.parametrize("validation_sample_count", [-1, 1.5, True])
def test_validation_sample_count_must_be_a_non_negative_non_bool_integer(
    validation_sample_count: object,
):
    with pytest.raises(ValueError, match="validation_sample_count"):
        CategoryValidation(
            validated_precision=0.95,
            validation_sample_count=validation_sample_count,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("field_name", ["configured_threshold", "minimum_precision"])
@pytest.mark.parametrize(
    "invalid_value",
    [float("nan"), float("inf"), float("-inf"), -0.01, 1.01, True, 1],
)
def test_eligibility_probabilities_must_be_finite_floats_in_unit_interval(
    field_name: str,
    invalid_value: object,
):
    values: dict[str, object] = {
        "configured_threshold": 0.85,
        "minimum_precision": 0.95,
        "minimum_validation_samples": 30,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError, match=field_name):
        EligibilityRequirement(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("minimum_validation_samples", [-1, 0, 1.5, True])
def test_minimum_validation_samples_must_be_a_positive_non_bool_integer(
    minimum_validation_samples: object,
):
    with pytest.raises(ValueError, match="minimum_validation_samples"):
        EligibilityRequirement(
            configured_threshold=0.85,
            minimum_precision=0.95,
            minimum_validation_samples=minimum_validation_samples,  # type: ignore[arg-type]
        )


def test_candidate_assessment_defensively_copies_and_freezes_categories():
    eligibility = CategoryEligibility(
        category=EmailCategory.IMPORTANT,
        configured_threshold=0.85,
        validated_precision=0.95,
        validation_sample_count=30,
        auto_action_eligible=True,
        reason="precision_and_sample_gate_met",
    )
    source = {EmailCategory.IMPORTANT: eligibility}
    assessment = CandidateAssessment(
        promote_model=True,
        promotion_reason="candidate_validation_passed",
        categories=source,
    )

    source.clear()

    assert assessment.categories[EmailCategory.IMPORTANT] is eligibility
    with pytest.raises(TypeError):
        assessment.categories[EmailCategory.IMPORTANT] = eligibility  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        assessment.categories = {}  # type: ignore[misc]


def test_train_and_promote_round_trips_candidate_and_previous_model(tmp_path: Path):
    store = _store_with_confirmed_feedback(tmp_path)
    active = tmp_path / "models" / "model.active.pkl"
    previous = tmp_path / "models" / "model.previous.pkl"

    result = train_and_promote(
        store,
        active,
        previous,
        model_version="email-model-2026-08-29",
    )

    assert result.promoted is True
    assert result.example_count == 6
    assert result.category_counts == {"work": 3, "junk": 3}
    assert active.exists()
    assert not previous.exists()

    second = train_and_promote(
        store,
        active,
        previous,
        model_version="email-model-2026-08-29-2",
    )

    assert second.promoted is True
    assert previous.exists()


def test_registry_promotion_marks_only_authoritative_samples_after_success(tmp_path: Path):
    store = _store_with_confirmed_feedback(tmp_path)
    registry = EmailModelRegistry(tmp_path / "registry")

    result = train_and_promote(
        store,
        registry,
        trained_at=datetime(2026, 8, 29, 21, 45, 30, tzinfo=timezone.utc),
    )

    assert result.promoted is True
    assert result.model_id.startswith("email-tfidf-lr-20260829T214530Z-")
    assert result.prediction_latency_p95_ms < 100
    assert registry.active_manifest().model_id == result.model_id  # type: ignore[union-attr]
    for example in store.list_training_examples(include_inclusion=True):
        assert example["included_in_model_id"] == result.model_id


def test_next_registry_model_marks_only_new_snapshot_without_reassigning_old_samples(
    tmp_path: Path, monkeypatch
):
    store = _store_with_confirmed_feedback(tmp_path)
    registry = EmailModelRegistry(tmp_path / "registry")
    first = train_and_promote(
        store,
        registry,
        trained_at=datetime(2026, 8, 29, 21, 45, 30, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.email_classifier_training._promotion_rejection",
        lambda _registry, _metadata: None,
    )
    for index, category in enumerate((EmailCategory.WORK, EmailCategory.JUNK), 7):
        row = store.upsert_classification(
            _classification(f"message-{index}", category),
            model_text=f"__subject__new-{index} {category.value}",
        )
        _confirm(store, row["id"], category)

    second = train_and_promote(
        store,
        registry,
        trained_at=datetime(2026, 8, 29, 21, 46, 30, tzinfo=timezone.utc),
    )

    included = store.list_training_examples(include_inclusion=True)
    by_text = {row["model_text"]: row["included_in_model_id"] for row in included}
    assert all(
        model_id == first.model_id
        for text, model_id in by_text.items()
        if "__subject__new-" not in text
    )
    assert all(
        model_id == second.model_id
        for text, model_id in by_text.items()
        if "__subject__new-" in text
    )


def test_later_retrain_rejects_correction_to_previously_included_training_sample(
    tmp_path: Path, monkeypatch
):
    store = _store_with_confirmed_feedback(tmp_path)
    registry = EmailModelRegistry(tmp_path / "registry")
    first = train_and_promote(
        store,
        registry,
        trained_at=datetime(2026, 8, 29, 21, 45, 30, tzinfo=timezone.utc),
    )
    prior_active = registry.active_manifest()
    historical = store.list_training_examples(include_inclusion=True)[0]
    assert historical["included_in_model_id"] == first.model_id
    for index, category in enumerate((EmailCategory.WORK, EmailCategory.JUNK), 30):
        row = store.upsert_classification(
            _classification(f"later-{index}", category),
            model_text=f"__subject__later-{index} {category.value}",
        )
        _confirm(store, row["id"], category)

    original_stage = registry.stage_candidate

    def stage_before_historical_correction(*args, **kwargs):
        result = original_stage(*args, **kwargs)
        with store._connect() as db:
            db.execute(
                """
                update email_classifications
                set model_text=model_text || ' corrected'
                where id=?
                """,
                (historical["classification_id"],),
            )
        return result

    monkeypatch.setattr(registry, "stage_candidate", stage_before_historical_correction)
    monkeypatch.setattr(
        "app.email_classifier_training._promotion_rejection",
        lambda _registry, _metadata: None,
    )

    with pytest.raises(EmailTrainingInclusionConflict):
        train_and_promote(
            store,
            registry,
            trained_at=datetime(2026, 8, 29, 21, 46, 30, tzinfo=timezone.utc),
        )

    assert registry.active_manifest() == prior_active
    corrected = {
        row["message_id"]: row for row in store.list_unincluded_training_examples()
    }[historical["message_id"]]
    assert corrected["included_in_model_id"] is None
    assert corrected["sample_digest"] != historical["sample_digest"]


def test_rejected_or_failed_candidate_never_marks_sqlite_samples(
    tmp_path: Path, monkeypatch
):
    store = _store_with_confirmed_feedback(tmp_path)
    registry = EmailModelRegistry(tmp_path / "registry")
    monkeypatch.setattr(
        "app.email_classifier_training._promotion_rejection",
        lambda registry, metadata: "macro_f1_regressed",
    )

    rejected = train_and_promote(
        store,
        registry,
        trained_at=datetime(2026, 8, 29, 21, 45, 31, tzinfo=timezone.utc),
    )

    assert rejected.promoted is False
    assert registry.get_model(rejected.model_id).status == "rejected"
    assert len(store.list_unincluded_training_examples()) == 6

    failed_store = _store_with_confirmed_feedback(tmp_path / "failed")
    failed_registry = EmailModelRegistry(tmp_path / "failed-registry")
    monkeypatch.setattr(
        failed_registry,
        "stage_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("stage failed")),
    )
    with pytest.raises(RuntimeError, match="stage failed"):
        train_and_promote(
            failed_store,
            failed_registry,
            trained_at=datetime(2026, 8, 29, 21, 45, 32, tzinfo=timezone.utc),
        )
    assert len(failed_store.list_unincluded_training_examples()) == 6


def test_concurrent_feedback_correction_fails_snapshot_inclusion_and_leaves_it_pending(
    tmp_path: Path, monkeypatch
):
    store = _store_with_confirmed_feedback(tmp_path)
    registry = EmailModelRegistry(tmp_path / "registry")
    original_stage = registry.stage_candidate

    def stage_before_correction(*args, **kwargs):
        result = original_stage(*args, **kwargs)
        with store._connect() as db:
            db.execute(
                """
                update email_classifications
                set confirmed_category='important', category='important'
                where id=(select min(id) from email_classifications)
                """
            )
        return result

    monkeypatch.setattr(registry, "stage_candidate", stage_before_correction)

    with pytest.raises(
        EmailTrainingInclusionConflict,
        match="training sample changed before inclusion",
    ):
        train_and_promote(
            store,
            registry,
            trained_at=datetime(2026, 8, 29, 21, 45, 33, tzinfo=timezone.utc),
        )

    latest = store.list_unincluded_training_examples()
    assert any(example["label"] == "important" for example in latest)


def test_inclusion_failure_after_promotion_restores_exact_prior_manifests(
    tmp_path: Path, monkeypatch
):
    store = _store_with_confirmed_feedback(tmp_path)
    registry = EmailModelRegistry(tmp_path / "registry")
    first = train_and_promote(
        store,
        registry,
        trained_at=datetime(2026, 8, 29, 21, 45, 30, tzinfo=timezone.utc),
    )
    prior_active = registry.active_manifest()
    prior_previous = registry.previous_manifest()
    for index, category in enumerate((EmailCategory.WORK, EmailCategory.JUNK), 20):
        row = store.upsert_classification(
            _classification(f"rollback-{index}", category),
            model_text=f"__subject__rollback-{index} {category.value}",
        )
        _confirm(store, row["id"], category)
    monkeypatch.setattr(
        "app.email_classifier_training._promotion_rejection",
        lambda _registry, _metadata: None,
    )
    monkeypatch.setattr(
        store,
        "_update_training_inclusion",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.DatabaseError("forced")),
    )

    with pytest.raises(sqlite3.DatabaseError, match="forced"):
        train_and_promote(
            store,
            registry,
            trained_at=datetime(2026, 8, 29, 21, 46, 30, tzinfo=timezone.utc),
        )

    assert registry.active_manifest() == prior_active
    assert registry.previous_manifest() == prior_previous
    assert registry.active_manifest().model_id == first.model_id  # type: ignore[union-attr]


def test_training_not_ready_does_not_create_active_model(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    row = store.upsert_classification(
        _classification("message-1", EmailCategory.WORK), model_text="work"
    )
    _confirm(store, row["id"], EmailCategory.WORK)
    active = tmp_path / "model.active.pkl"

    with pytest.raises(TrainingNotReady):
        train_and_promote(store, active, tmp_path / "model.previous.pkl", model_version="x")

    assert not active.exists()


def test_retrain_policy_coalesces_feedback_until_batch_or_idle_window():
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    state = RetrainState().record_feedback(now)
    policy = RetrainPolicy(minimum_new_examples=5, idle_seconds=30)

    not_due = evaluate_retrain(state, feedback_count=1, now=now, policy=policy)
    due = evaluate_retrain(state, feedback_count=5, now=now, policy=policy)
    idle_not_due = evaluate_retrain(
        state, feedback_count=1, now=now + timedelta(seconds=31), policy=policy
    )
    idle_due = evaluate_retrain(
        state, feedback_count=5, now=now + timedelta(seconds=31), policy=policy
    )

    assert not_due.due is False
    assert due.due is False
    assert idle_not_due.due is False
    assert idle_due.reason == "idle_debounce"


def test_retrain_state_round_trips_atomically(tmp_path: Path):
    path = tmp_path / "retrain-state.json"
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    state = RetrainState(last_trained_feedback_count=6).record_feedback(now)

    save_retrain_state(path, state)

    assert load_retrain_state(path) == state


def test_retrain_if_due_advances_state_only_after_promotion(tmp_path: Path):
    store = _store_with_confirmed_feedback(tmp_path)
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    active = tmp_path / "models" / "model.active.pkl"
    previous = tmp_path / "models" / "model.previous.pkl"

    result = retrain_if_due(
        store,
        RetrainState().record_feedback(now),
        active,
        previous,
        now=now + timedelta(seconds=31),
        model_version="email-model-triggered",
        policy=RetrainPolicy(minimum_new_examples=5),
    )

    assert result.decision.reason == "idle_debounce"
    assert result.training_result is not None
    assert result.state.last_trained_feedback_count == 6
    assert active.exists()


def test_retrain_failure_does_not_advance_state_or_create_model(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    row = store.upsert_classification(
        _classification("message-1", EmailCategory.WORK), model_text="work"
    )
    _confirm(store, row["id"], EmailCategory.WORK)
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    state = RetrainState().record_feedback(now)
    active = tmp_path / "models" / "model.active.pkl"

    with pytest.raises(TrainingNotReady):
        retrain_if_due(
            store,
            state,
            active,
            tmp_path / "models" / "model.previous.pkl",
            now=now + timedelta(seconds=31),
            model_version="should-not-promote",
            policy=RetrainPolicy(minimum_new_examples=1),
        )

    assert not active.exists()
    assert state.last_trained_feedback_count == 0
