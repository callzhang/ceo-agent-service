"""Current Email test value templates."""


def _message(stable="stable-1", **overrides):
    return {
        "account_id": "account", "stable_message_identity": stable,
        "folder": " INBOX ", "uidvalidity": 10, "uid": 20,
        "rfc_message_id": "<message>", "in_reply_to": "", "references_json": "[]",
        "thread_identity": "thread", "sender": "sender", "recipients_json": "[]",
        "subject": "subject", "normalized_text": "body", "preview": "preview",
        "attachment_metadata_json": "[]", "received_at": "received",
        "created_at": "message-created", "updated_at": "message-updated",
    } | overrides

def _classification(stable="stable-1", **overrides):
    return {
        "id": 101, "account_id": "account", "folder": "INBOX",
        "uidvalidity": 10, "uid": 20, "stable_message_identity": stable,
        "rfc_message_id": None, "thread_id": "class-thread", "sender": "class-sender",
        "subject": "class-subject", "preview": "class-preview", "model_text": "model-body",
        "received_at": "class-received", "category": None, "predicted_category": None,
        "confirmed_category": None, "confidence": 0.5, "margin": 0.1,
        "probabilities_json": "{}", "model_id": "model", "config_version": "v1",
        "status": "pending_feedback", "classification_source": "model",
        "agent_result_json": "null", "current_action_plan_id": None,
        "included_in_model_id": None,
        "confirmed_at": "", "created_at": "class-created", "updated_at": "class-updated",
    } | overrides


def mark_processed_with_plan(db, classification_id):
    """Give a current classification a real, empty ActionPlan."""
    from app.shared_action_lifecycle import persist_email_plan
    from app.email_classifier_contracts import build_email_action_plan
    from datetime import datetime, timezone

    row = db.execute("select * from emails where classification_id=?", (classification_id,)).fetchone()
    category = row["class_category"] or "work"
    plan = build_email_action_plan(classification_id=classification_id, account_id=row["account_id"],
        category=category, classification_source=row["class_classification_source"],
        confidence=row["class_confidence"], model_id=row["class_model_id"],
        config_version=row["class_config_version"], actions=(), action_parameters={}, action_authorizations=(),
        created_at=datetime(2026, 10, 18, tzinfo=timezone.utc))
    plan_id = plan.action_plan_id
    persist_email_plan(db, {
        "action_plan_id": plan_id, "action_plan_version": 1,
        "classification_id": classification_id, "account_id": row["account_id"],
        "category": category, "classification_source": row["class_classification_source"],
        "confidence": row["class_confidence"], "model_id": row["class_model_id"],
        "config_version": row["class_config_version"], "actions_json": "[]",
        "action_parameters_json": "{}", "authorization_snapshot_json": "[]",
        "created_at": "2026-10-18T00:00:00+00:00",
    })
    db.execute("update emails set class_status='processed',class_category=?,class_current_action_plan_id=? where classification_id=?",
               (category, plan_id, classification_id))
