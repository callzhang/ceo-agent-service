"""Insert native Task rows for fixtures that used the legacy per-kind Task writers."""

OKR_REVIEW_PARAMETERS = {
    "conversation_id": "cid", "conversation_title": "title",
    "trigger_message_id": "msg", "trigger_sender": "sender",
    "trigger_sender_user_id": "user", "trigger_text": "text",
    "period_label": "period", "period_start": "start", "period_end": "end",
    "okr_source_json": "{}", "codex_session_id": "",
}


def seed_native_task(db, task_id, kind, status, parameters=None):
    """Insert one `tasks` row; `parameters` land in the Task's parameters blob."""
    from app import task_entity_storage

    db.execute(
        "insert into tasks(id,kind,status,parameters_json,created_at,updated_at) "
        "values(?,?,?,?,current_timestamp,current_timestamp)",
        (task_id, kind, status, task_entity_storage._parameters_blob(db, parameters or {})),
    )


def seed_okr_review_task(db, request_id, status="processing", **parameters):
    seed_native_task(db, request_id, "okr_review", status, {**OKR_REVIEW_PARAMETERS, **parameters})
