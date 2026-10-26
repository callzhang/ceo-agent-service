"""Re-creating an existing direct Email Action is idempotent; only its identity may conflict."""

import pytest

from app import shared_action_lifecycle
from app.email_classifier_contracts import EmailClassificationStatus
from app.email_store import DIRECT_EMAIL_ACTION_SQL, EmailStore
from tests.support.native_email_actions import persist_classification


def _existing_values(store):
    with store._connect() as db:
        row = db.execute(f"select * from ({DIRECT_EMAIL_ACTION_SQL})").fetchone()
    return {
        "action_plan_id": row["action_plan_id"],
        "classification_id": row["classification_id"],
        "account_id": row["account_id"],
        "action_type": row["action_type"],
        "parameters_json": row["parameters_json"],
        "status": "pending",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def test_recreating_an_existing_plan_action_with_a_later_clock_returns_the_same_owner(tmp_path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    persist_classification(store, classification_id=1, status=EmailClassificationStatus.PROCESSED)
    values = _existing_values(store)
    with store._connect() as db:
        db.execute("begin immediate")
        first = shared_action_lifecycle.create_direct_email_action(db, dict(values))
        again = shared_action_lifecycle.create_direct_email_action(
            db, values | {"created_at": "2099-01-01T00:00:00+00:00", "updated_at": "2099-01-01T00:00:00+00:00"},
        )
    assert again["id"] == first["id"]
    assert again["created_at"] == values["created_at"]


def test_recreating_an_existing_plan_action_with_other_parameters_still_conflicts(tmp_path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    persist_classification(store, classification_id=1, status=EmailClassificationStatus.PROCESSED)
    values = _existing_values(store)
    with store._connect() as db:
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="identity conflicts"):
            shared_action_lifecycle.create_direct_email_action(
                db, values | {"parameters_json": '{"target_folder":"其他"}'},
            )
