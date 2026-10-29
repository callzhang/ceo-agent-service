from datetime import datetime, timezone

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


def test_old_current_provider_action_stays_in_live_backlog(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    store.create_account({
        "account_id": "account-1", "display_name": "Mail",
        "email_address": "me@example.test", "imap_host": "imap.example.test",
        "imap_port": 993, "imap_tls": True, "imap_username": "me@example.test",
        "imap_secret_reference": "keychain://x", "smtp_host": "", "smtp_port": 465,
        "smtp_tls": True, "smtp_username": "", "smtp_secret_reference": "",
        "enabled": True, "scan_folders": ["INBOX"], "scan_interval_seconds": 60,
    })
    classification_id = 4242
    store.persist_scan_result(EmailClassification.model_validate({
        "classification_id": classification_id,
        "stable_message_identity": "account-1:message-id:<old@example.test>",
        "provider_locator": {"account_id": "account-1", "folder": "INBOX",
                             "uidvalidity": 1, "uid": 5,
                             "rfc_message_id": "<old@example.test>", "thread_id": "old"},
        "category": EmailCategory.NOTIFICATION, "confidence": 0.5, "margin": 0.1,
        "probabilities": {"notification": 0.5}, "model_id": "m", "config_version": "v1",
        "status": EmailClassificationStatus.PENDING_FEEDBACK,
        "classification_source": "model", "action_plan": None,
    }), sender="s@example.com", subject="Old", preview="p", model_text="t")
    plan = build_versioned_email_action_plan(
        action_plan_version=1, classification_id=classification_id,
        account_id="account-1", category=EmailCategory.NOTIFICATION,
        classification_source="user", confidence=0.5, model_id="m", config_version="v1",
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "Archive"}},
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    store.append_action_plan_version(
        classification_id, plan, confirmed_category=EmailCategory.NOTIFICATION
    )
    (action_id,) = store.direct_action_ids_for_plan(plan.action_plan_id)
    with store._connect() as db:
        db.execute(
            "update actions set action_data=jsonb_set(action_data,'$.created_at',json(?)) "
            "where record_type='action' and id=?",
            ('"2020-01-01T00:00:00+00:00"', action_id),
        )
        assert db.execute(
            "select action_created_at from actions where id=?", (action_id,)
        ).fetchone()[0] == "2020-01-01T00:00:00+00:00"
    app = FastAPI()
    register_email_routes(app, lambda: store)

    progress = TestClient(app).get("/api/console/email/processing-progress").json()

    assert progress["provider_actions"]["pending"] == 0
    assert progress["provider_action_backlog"] == {"pending": 1, "processing": 0}
