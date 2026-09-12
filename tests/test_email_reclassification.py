from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore
from app.web_api.email import register_email_routes


def _processed_store(path: Path) -> tuple[EmailStore, str]:
    store = EmailStore(path)
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=501,
        account_id="account-1",
        category=EmailCategory.WORK,
        classification_source="model",
        confidence=0.91,
        model_id="email-model-v1",
        config_version="email-config-v1",
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "Work"}},
        created_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    classification = EmailClassification.model_validate(
        {
            "classification_id": 501,
            "stable_message_identity": "account-1:message-id:<501@example.com>",
            "provider_locator": {
                "account_id": "account-1",
                "folder": "INBOX",
                "uidvalidity": 1,
                "uid": 501,
                "rfc_message_id": "<501@example.com>",
                "thread_id": "501",
            },
            "category": EmailCategory.WORK,
            "confidence": 0.91,
            "margin": 0.4,
            "probabilities": {"work": 0.91},
            "model_id": "email-model-v1",
            "config_version": "email-config-v1",
            "status": EmailClassificationStatus.PROCESSED,
            "classification_source": "model",
            "action_plan": plan,
        }
    )
    store.persist_scan_result(
        classification,
        sender="sender@example.com",
        subject="Reclassify me",
        normalized_text="正文",
        preview="摘要",
        model_text="模型文本",
    )
    return store, plan.action_plan_id


def test_processed_work_to_legal_creates_user_plan_version_and_keeps_old_plan(
    tmp_path: Path,
) -> None:
    store, old_plan_id = _processed_store(tmp_path / "reclassification.sqlite3")
    app = FastAPI()
    register_email_routes(app, lambda: store)
    client = TestClient(app)

    detail = client.get("/api/console/email/classifications/501")
    assert detail.status_code == 200
    assert detail.json()["item"]["current_action_plan_id"] == old_plan_id

    response = client.post(
        "/api/console/email/classifications/501/feedback",
        json={
            "category": "legal",
            "feedback_request_id": "reclassify-501-work-to-legal",
            "expected_current_action_plan_id": old_plan_id,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["item"]["category"] == "legal"
    assert payload["item"]["classification_source"] == "user"
    assert payload["feedback"]["resulting_action_plan_id"] != old_plan_id
    assert payload["item"]["current_action_plan_id"] == payload["feedback"]["resulting_action_plan_id"]

    with sqlite3.connect(store.path) as db:
        plans = db.execute(
            "select action_plan_id, action_plan_version, category, classification_source "
            "from email_action_plans where classification_id=? order by action_plan_version",
            (501,),
        ).fetchall()
    assert [plan[1] for plan in plans] == [1, 2]
    assert plans[0][0] == old_plan_id
    assert plans[1][2:] == ("legal", "user")
