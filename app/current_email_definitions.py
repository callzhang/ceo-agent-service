"""Current Email classification projection, distinct from frozen sources."""

from app.email_canonical_definitions import MESSAGE_FIELDS, SHARED_FIELDS, MESSAGE_METADATA_FIELDS

CLASS_FIELDS = ('id',
 'account_id',
 'folder',
 'uidvalidity',
 'uid',
 'rfc_message_id',
 'thread_id',
 'stable_message_identity',
 'sender',
 'subject',
 'preview',
 'model_text',
 'received_at',
 'category',
 'predicted_category',
 'confirmed_category',
 'confidence',
 'margin',
 'probabilities_json',
 'model_id',
 'config_version',
 'status',
 'classification_source',
 'agent_result_json',
 'current_action_plan_id',
 'included_in_model_id',
 'confirmed_at',
 'created_at',
 'updated_at')

def email_projection(kind: str) -> str:
    fields = CLASS_FIELDS if kind == "email_classifications" else MESSAGE_FIELDS if kind == "email_messages" else ()
    if not fields:
        raise ValueError("unsupported Email source kind")
    def column(field):
        if kind == "email_classifications" and field == "id":
            return "classification_id"
        if field in SHARED_FIELDS:
            return field
        if kind == "email_classifications" and field in MESSAGE_METADATA_FIELDS:
            return "message_" + field
        return ("class_" if kind == "email_classifications" else "message_") + field
    selected = ",".join(f'"{column(field)}" as "{field}"' for field in fields)
    predicate = " where classification_id is not null" if kind == "email_classifications" else ""
    return "select " + selected + " from main.emails" + predicate
