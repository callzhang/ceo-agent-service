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
        "included_in_model_id": None, "legacy_processed_without_plan": 0,
        "confirmed_at": "", "created_at": "class-created", "updated_at": "class-updated",
    } | overrides
