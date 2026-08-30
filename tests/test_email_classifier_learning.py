from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from app.email_classifier_contracts import (
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
)
from app.email_classifier_learning import EmailClassifierLearningService
from app.email_classifier_retrain import RetrainPolicy, load_retrain_state
from app.email_store import EmailStore


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


def _service_with_pending(
    tmp_path: Path, count: int = 1, *, minimum_new_examples: int = 5
):
    store = EmailStore(tmp_path / "email.sqlite3")
    categories = [EmailCategory.WORK, EmailCategory.JUNK]
    rows = []
    for index in range(count):
        category = categories[index % 2]
        row = store.upsert_classification(
            _classification(f"message-{index}", category),
            model_text=f"__subject__token-{index} {category.value}",
        )
        rows.append(row)
    service = EmailClassifierLearningService(
        store,
        active_path=tmp_path / "models" / "model.active.pkl",
        previous_path=tmp_path / "models" / "model.previous.pkl",
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
        policy=RetrainPolicy(minimum_new_examples=minimum_new_examples),
    )
    return service, store, rows


def test_feedback_api_service_confirms_first_and_records_state_without_retraining(tmp_path: Path):
    service, store, rows = _service_with_pending(tmp_path)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    result = service.confirm_and_maybe_retrain(
        rows[0]["id"], EmailCategory.IMPORTANT, now=now, model_version="model-test"
    )

    assert result is not None
    assert result.confirmed["classification_source"] == "user"
    assert result.retrain is not None
    assert result.retrain.decision.due is False
    assert result.error is None
    assert load_retrain_state(tmp_path / "models" / "retrain-state.json").last_feedback_at
    assert store.list_training_examples()[0]["label"] == "important"


def test_feedback_service_retrains_after_batch_threshold(tmp_path: Path):
    service, _, rows = _service_with_pending(tmp_path, count=6)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    promoted_result = None
    for index, row in enumerate(rows):
        result = service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory.WORK if index % 2 == 0 else EmailCategory.JUNK,
            now=now,
            model_version="model-batch-test",
        )
        if result is not None and result.retrain is not None and result.retrain.training_result is not None:
            promoted_result = result

    polled = service.poll_retrain(
        now=now + timedelta(seconds=31), model_version="model-batch-test"
    )
    if polled.training_result is not None:
        promoted_result = polled

    assert promoted_result is not None
    assert (tmp_path / "models" / "model.active.pkl").exists()
    assert load_retrain_state(tmp_path / "models" / "retrain-state.json").last_trained_feedback_count == 6


def test_feedback_service_keeps_confirmation_when_training_is_not_ready(tmp_path: Path):
    service, store, rows = _service_with_pending(
        tmp_path, minimum_new_examples=1
    )
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    result = service.confirm_and_maybe_retrain(
        rows[0]["id"], EmailCategory.WORK, now=now
    )

    assert result is not None
    assert result.confirmed["status"] == "processed"
    assert result.error is None
    assert store.list_training_examples()[0]["label"] == "work"
