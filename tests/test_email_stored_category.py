"""Current-schema readback of already persisted category facts."""

import json
from datetime import datetime, timezone

import pytest

from app.email_canonical_storage import update_email_classification
from app.email_classifier_contracts import (
    EmailActionPlan, EmailClassificationStatus, _action_plan_identity,
)
from app.email_store import EMAIL_PLAN_SQL, EmailStore
from tests.test_email_store import _classification, _persist_scan


@pytest.mark.parametrize("category", ("important", "billing", "subscription"))
def test_current_schema_old_category_reopens_and_repairs_without_identity_change(tmp_path, category):
    database = tmp_path / "email.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    _persist_scan(store, classification)
    created_at = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    fields = dict(action_plan_version=1, classification_id=classification.classification_id,
                  account_id="dingtalk-account", category=category,
                  classification_source="model", confidence=classification.confidence,
                  model_id=classification.model_id, config_version=classification.config_version,
                  actions=(), action_parameters={}, created_at=created_at)
    plan = EmailActionPlan(action_plan_id=_action_plan_identity(**fields), **fields)
    with store._connect() as db:
        store._persist_action_plan(db, plan, now=created_at.isoformat())
        update_email_classification(db, classification.classification_id, {
            "category": category, "predicted_category": category,
            "confirmed_category": category, "probabilities_json": json.dumps({category: 1.0}),
            "status": "processed", "current_action_plan_id": plan.action_plan_id,
        })
    reopened = EmailStore(database)
    before = reopened.get_classification(classification.classification_id)
    assert before["category"] == category
    assert before["predicted_category"] == category
    assert before["confirmed_category"] == category
    assert before["probabilities"] == {category: 1.0}
    assert before["action_plan"]["action_plan_id"] == plan.action_plan_id
    assert reopened.repair_current_action_plan(plan)["action_plan"] == before["action_plan"]
    with reopened._connect() as db:
        row = db.execute(f"select authorization_snapshot_json from ({EMAIL_PLAN_SQL}) where action_plan_id=?", (plan.action_plan_id,)).fetchone()
        assert row[0] is None
