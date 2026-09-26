from __future__ import annotations

from pathlib import Path

import pytest

from app.email_classifier_contracts import (
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
)
from app.email_similar_examples import clear_cache, similar_owner_examples
from app.email_store import EmailStore

ALLOWED = ("work", "finance", "notification", "personal")


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _add(store: EmailStore, classification_id: int, *, sender: str, subject: str, text: str,
         category: str, owner_labelled: bool) -> str:
    identity = f"account-1:message-id:<{classification_id}@example.com>"
    store.persist_scan_result(
        EmailClassification.model_validate({
            "classification_id": classification_id,
            "stable_message_identity": identity,
            "provider_locator": {
                "account_id": "account-1", "folder": "INBOX", "uidvalidity": 1,
                "uid": classification_id, "rfc_message_id": f"<{classification_id}@example.com>",
                "thread_id": str(classification_id),
            },
            "category": EmailCategory.NOTIFICATION, "confidence": 0.5, "margin": 0.1,
            "probabilities": {"notification": 0.5}, "model_id": "m", "config_version": "v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model", "action_plan": None,
        }),
        sender=sender, subject=subject, preview="p", model_text=text,
    )
    if owner_labelled:
        with store._connect() as db:
            db.execute(
                "update email_classifications set classification_source='user', "
                "confirmed_category=?, status='processed' where id=?",
                (category, classification_id),
            )
    return identity


def _store(tmp_path: Path) -> EmailStore:
    store = EmailStore(tmp_path / "examples.sqlite3")
    _add(store, 1, sender="pay@vendor.example", subject="结算单 9 月", text="请核对本月结算单和回款", category="finance", owner_labelled=True)
    _add(store, 2, sender="pay@vendor.example", subject="结算单 8 月", text="请核对上月结算单和回款", category="finance", owner_labelled=True)
    _add(store, 3, sender="ci@github.example", subject="Run failed: CI main", text="build failed on main", category="notification", owner_labelled=True)
    _add(store, 4, sender="model@example.com", subject="结算单 7 月", text="结算单 回款", category="work", owner_labelled=False)
    return store


def test_the_most_similar_owner_labels_come_first_and_only_owner_labels_count(tmp_path: Path) -> None:
    store = _store(tmp_path)

    found = similar_owner_examples(
        store, stable_message_identity="other", sender="pay@vendor.example",
        subject="结算单 10 月", text="请核对结算单和回款", allowed_category_keys=ALLOWED,
    )

    assert [item["category"] for item in found][:2] == ["finance", "finance"]
    assert "notification" not in [item["category"] for item in found][:2]
    # The model's own row (4) is not an owner label, so it is never offered.
    assert all(item["subject"] != "结算单 7 月" for item in found)
    assert set(found[0]) == {"sender", "subject", "start", "category"}


def test_the_message_itself_and_retired_categories_are_left_out(tmp_path: Path) -> None:
    store = _store(tmp_path)
    own = "account-1:message-id:<1@example.com>"

    found = similar_owner_examples(
        store, stable_message_identity=own, sender="pay@vendor.example",
        subject="结算单 9 月", text="请核对本月结算单和回款", allowed_category_keys=("notification",),
    )

    assert [item["category"] for item in found] == ["notification"]
    assert all(item["subject"] != "结算单 9 月" for item in found)


def test_no_owner_labels_means_no_examples(tmp_path: Path) -> None:
    store = EmailStore(tmp_path / "empty.sqlite3")
    assert similar_owner_examples(
        store, stable_message_identity="x", sender="a@b.c", subject="s", text="t",
        allowed_category_keys=ALLOWED,
    ) == []


def test_a_new_owner_label_shows_up_without_a_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    args = dict(stable_message_identity="other", sender="hr@corp.example",
                subject="Offer letter", text="offer letter for the candidate", allowed_category_keys=ALLOWED)
    before = similar_owner_examples(store, **args)

    _add(store, 5, sender="hr@corp.example", subject="Offer letter draft", text="offer letter for the candidate",
         category="personal", owner_labelled=True)
    after = similar_owner_examples(store, **args)

    assert after[0]["category"] == "personal"
    assert all(item["subject"] != "Offer letter draft" for item in before)
