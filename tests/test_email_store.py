from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
)
from app.email_store import EmailStore


def _classification(
    *,
    status: EmailClassificationStatus,
    message_id: str = "msg-1",
    confidence: float = 0.93,
    model_id: str = "email/logistic/model-1",
) -> EmailClassification:
    classification_id = int.from_bytes(
        sha256(message_id.encode("utf-8")).digest()[:8], "big"
    ) & ((1 << 63) - 1) or 1
    action_plan = None
    if status is EmailClassificationStatus.PROCESSED:
        action_plan = {
            "action_plan_id": f"plan-{classification_id}",
            "action_plan_version": 1,
            "classification_id": classification_id,
            "account_id": "dingtalk-account",
            "category": EmailCategory.WORK,
            "classification_source": "model",
            "confidence": confidence,
            "model_id": model_id,
            "config_version": "email-v1",
            "actions": (EmailAction.LABEL,),
            "action_parameters": {
                EmailAction.LABEL: {"labels": ["work"]},
            },
            "created_at": datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc),
        }
    return EmailClassification.model_validate(
        {
            "classification_id": classification_id,
            "provider_locator": {
                "account_id": "dingtalk-account",
                "folder": "INBOX",
                "uidvalidity": 42,
                "uid": classification_id,
                "rfc_message_id": f"<{message_id}@example.com>",
            },
            "category": EmailCategory.WORK,
            "confidence": confidence,
            "margin": 0.41,
            "probabilities": {"work": 0.93, "important": 0.52},
            "model_id": model_id,
            "config_version": "email-v1",
            "status": status,
            "classification_source": "model",
            "action_plan": action_plan,
        }
    )


def test_email_store_lists_pending_and_processed_separately(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        sender="sender@example.com",
        subject="Need a decision",
        preview="Please review",
    )
    second = _classification(
        status=EmailClassificationStatus.PROCESSED, message_id="msg-2"
    )
    store.upsert_classification(second)

    pending, pending_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=20, offset=0
    )
    processed, processed_total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=20, offset=0
    )

    assert pending_total == 1
    assert pending[0]["subject"] == "Need a decision"
    assert processed_total == 1
    assert processed[0]["status"] == "processed"
    assert processed[0]["model_id"] == "email/logistic/model-1"
    assert "model_version" not in processed[0]


def test_email_store_rejects_unredacted_model_text(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(ValueError, match="model_text must be redacted"):
        store.upsert_classification(
            _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
            model_text="sender@example.com https://private.example/message",
        )


def test_feedback_moves_a_message_to_processed_and_records_user_source(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )

    confirmed = store.confirm_classification(row["id"], EmailCategory.IMPORTANT)

    assert confirmed is not None
    assert confirmed["category"] == "important"
    assert confirmed["status"] == "processed"
    assert confirmed["classification_source"] == "user"
    assert confirmed["action_plan"]["category"] == "important"
    assert confirmed["action_plan"]["classification_source"] == "user"
    assert store.confirm_classification(999, EmailCategory.WORK) is None


def test_feedback_rebuilds_action_plan_for_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    store.upsert_config(
        category=EmailCategory.IMPORTANT,
        description="需要尽快处理",
        threshold=0.97,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["important"]}},
        enabled=True,
        config_version="important-v2",
    )
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        model_text="__subject__合同确认",
    )

    confirmed = store.confirm_classification(row["id"], EmailCategory.IMPORTANT)

    assert confirmed is not None
    assert confirmed["category"] == "important"
    assert confirmed["config_version"] == "important-v2"
    assert confirmed["action_plan"]["action_plan_version"] == 1
    assert confirmed["action_plan"]["classification_id"] == confirmed["id"]
    assert confirmed["action_plan"]["account_id"] == "dingtalk-account"
    assert confirmed["action_plan"]["category"] == "important"
    assert confirmed["action_plan"]["classification_source"] == "user"
    assert confirmed["action_plan"]["model_id"] == "email/logistic/model-1"
    assert confirmed["action_plan"]["config_version"] == "important-v2"
    assert confirmed["action_plan"]["actions"] == ["label"]
    assert confirmed["action_plan"]["action_parameters"] == {
        "label": {"labels": ["important"]}
    }
    assert "is_execution_authorization" not in confirmed["action_plan"]


def test_processed_email_cannot_be_confirmed_as_new_feedback(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PROCESSED)
    )

    assert store.confirm_classification(row["id"], EmailCategory.IMPORTANT) is None


def test_rescan_preserves_a_user_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    original = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )
    store.confirm_classification(original["id"], EmailCategory.IMPORTANT)

    rescanned = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )

    assert rescanned["id"] == original["id"]
    assert rescanned["category"] == "important"
    assert rescanned["status"] == "processed"
    assert rescanned["classification_source"] == "user"


def test_rescan_preserves_all_user_confirmed_action_plan_fields(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    original = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            confidence=0.61,
            model_id="email/logistic/model-v1",
        )
    )
    confirmed = store.confirm_classification(original["id"], EmailCategory.IMPORTANT)
    assert confirmed is not None

    rescanned = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            confidence=0.88,
            model_id="email/logistic/model-v2",
        )
    )

    plan = rescanned["action_plan"]
    assert plan is not None
    assert rescanned["id"] == plan["classification_id"]
    assert rescanned["account_id"] == plan["account_id"]
    assert rescanned["category"] == plan["category"]
    assert rescanned["classification_source"] == plan["classification_source"]
    assert rescanned["confidence"] == plan["confidence"] == 0.61
    assert rescanned["model_id"] == plan["model_id"] == "email/logistic/model-v1"
    assert rescanned["config_version"] == plan["config_version"]
    assert rescanned["action_plan"] == confirmed["action_plan"]


def test_email_store_persists_category_configuration(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")

    config = store.upsert_config(
        category=EmailCategory.SUBSCRIPTION,
        description="营销订阅和定期通讯",
        threshold=0.98,
        actions=(EmailAction.LABEL, EmailAction.UNSUBSCRIBE),
        action_parameters={EmailAction.LABEL: {"labels": ["subscription"]}},
        enabled=True,
        config_version="email-v2",
    )

    assert config["actions"] == ["label", "unsubscribe"]
    assert config["action_parameters"] == {
        "label": {"labels": ["subscription"]}
    }
    assert store.list_configs() == [config]
