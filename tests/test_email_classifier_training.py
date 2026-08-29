from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from app.email_classifier_contracts import (
    EmailActionPlan,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
)
from app.email_classifier_training import (
    CategoryValidation,
    EligibilityRequirement,
    TrainingNotReady,
    TrainingReadiness,
    assess_candidate,
    assess_feedback_readiness,
    train_and_promote,
)
from app.email_classifier_retrain import (
    RetrainPolicy,
    RetrainState,
    evaluate_retrain,
    load_retrain_state,
    retrain_if_due,
    save_retrain_state,
)
from app.email_store import EmailStore


def _classification(message_id: str, category: EmailCategory) -> EmailClassification:
    return EmailClassification(
        message_id=message_id,
        provider_locator=EmailProviderLocator(
            provider="test", mailbox="INBOX", message_id=message_id
        ),
        category=category,
        confidence=0.61,
        margin=0.11,
        probabilities={category.value: 0.61},
        model_version="model-before-feedback",
        config_version="email-v1",
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        classification_source="model",
        action_plan=EmailActionPlan(
            category=category,
            threshold=0.95,
            configured_actions=(),
            eligible_for_actions=False,
            config_version="email-v1",
        ),
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
        assert store.confirm_classification(row["id"], category) is not None
    return store


def test_feedback_keeps_redacted_model_text_for_training(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    row = store.upsert_classification(
        _classification("message-1", EmailCategory.WORK),
        model_text="__from_domain__example.test __subject__项目 工作",
    )

    store.confirm_classification(row["id"], EmailCategory.IMPORTANT)

    assert store.list_training_examples() == [
        {
            "message_id": "message-1",
            "model_text": "__from_domain__example.test __subject__项目 工作",
            "label": "important",
        }
    ]


def test_training_readiness_requires_two_examples_per_category(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    row = store.upsert_classification(
        _classification("message-1", EmailCategory.WORK), model_text="work"
    )
    store.confirm_classification(row["id"], EmailCategory.WORK)

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


def test_training_not_ready_does_not_create_active_model(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    row = store.upsert_classification(
        _classification("message-1", EmailCategory.WORK), model_text="work"
    )
    store.confirm_classification(row["id"], EmailCategory.WORK)
    active = tmp_path / "model.active.pkl"

    with pytest.raises(TrainingNotReady):
        train_and_promote(store, active, tmp_path / "model.previous.pkl", model_version="x")

    assert not active.exists()


def test_retrain_policy_coalesces_feedback_until_batch_or_idle_window():
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    state = RetrainState().record_feedback(now)
    policy = RetrainPolicy(minimum_new_examples=5, idle_seconds=30)

    not_due = evaluate_retrain(state, feedback_count=1, now=now, policy=policy)
    due = evaluate_retrain(
        state, feedback_count=5, now=now, policy=policy
    )
    idle_due = evaluate_retrain(
        state,
        feedback_count=1,
        now=now + timedelta(seconds=31),
        policy=policy,
    )

    assert not_due.due is False
    assert due.reason == "minimum_new_examples"
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
        now=now,
        model_version="email-model-triggered",
        policy=RetrainPolicy(minimum_new_examples=5),
    )

    assert result.decision.reason == "minimum_new_examples"
    assert result.training_result is not None
    assert result.state.last_trained_feedback_count == 6
    assert active.exists()


def test_retrain_failure_does_not_advance_state_or_create_model(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    row = store.upsert_classification(
        _classification("message-1", EmailCategory.WORK), model_text="work"
    )
    store.confirm_classification(row["id"], EmailCategory.WORK)
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    state = RetrainState().record_feedback(now)
    active = tmp_path / "models" / "model.active.pkl"

    with pytest.raises(TrainingNotReady):
        retrain_if_due(
            store,
            state,
            active,
            tmp_path / "models" / "model.previous.pkl",
            now=now,
            model_version="should-not-promote",
            policy=RetrainPolicy(minimum_new_examples=1),
        )

    assert not active.exists()
    assert state.last_trained_feedback_count == 0
