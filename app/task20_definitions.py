"""Current finite twenty-kind Tasks declarations.

All source business fields are explicit. Runtime never loads evidence artifacts.
Caller conversion, allocators and the paired release select these declarations.
"""

from app.action_email_definitions import _encoded_value, _encoded_value_check
from app.storage_payload import PayloadLayout, TASK_RECORD_TYPES

# Source name, SQLite affinity, NOT NULL, default SQL, authoritative storage.
TASK20_COLUMNS = {
    "reply_tasks": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("channel", "TEXT", True, "'dingtalk'", "payload"),
        ("conversation_id", "TEXT", True, None, "payload"),
        ("conversation_title", "TEXT", True, None, "payload"),
        ("single_chat", "INTEGER", True, None, "payload"),
        ("trigger_message_id", "TEXT", True, None, "payload"),
        ("trigger_create_time", "TEXT", True, None, "payload"),
        ("trigger_sender", "TEXT", True, None, "payload"),
        ("available_at", "TEXT", True, "''", "payload"),
        ("force_new_decision", "INTEGER", True, "0", "payload"),
        ("oa_url", "TEXT", True, "''", "payload"),
        ("manual_rerun_attempt_id", "INTEGER", True, "0", "payload"),
        ("status", "TEXT", True, "'pending'", "payload"),
        ("attempts", "INTEGER", True, "0", "payload"),
        ("locked_at", "TEXT", False, None, "payload"),
        ("error", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
        ("execution_generation", "TEXT", True, "'initial'", "payload"),
        ("manual_rerun_revision_key", "TEXT", True, "''", "payload"),
        ("recovery_code", "TEXT", True, "''", "payload"),
        ("business_object_key", "TEXT", True, "''", "payload"),
        ("input_version", "INTEGER", True, "1", "payload"),
        ("claimed_input_version", "INTEGER", True, "0", "payload"),
        ("current_input_id", "INTEGER", False, None, "current_input_id"),
        ("claimed_input_id", "INTEGER", False, None, "claimed_input_id"),
    ),
    "reply_task_inputs": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("reply_task_id", "INTEGER", True, None, "parent_id"),
        ("channel", "TEXT", True, None, "payload"),
        ("conversation_id", "TEXT", True, None, "payload"),
        ("conversation_title", "TEXT", True, None, "payload"),
        ("single_chat", "INTEGER", True, None, "payload"),
        ("trigger_message_id", "TEXT", True, None, "payload"),
        ("trigger_create_time", "TEXT", True, None, "payload"),
        ("trigger_sender", "TEXT", True, None, "payload"),
        ("trigger_text", "TEXT", True, None, "payload"),
        ("trigger_message_json", "TEXT", True, "'{}'", "payload"),
        ("oa_url", "TEXT", True, "''", "payload"),
        ("business_object_key", "TEXT", True, None, "payload"),
        ("input_revision_key", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("input_provenance_json", "TEXT", True, "'{}'", "payload"),
        ("input_compacted", "INTEGER", True, "0", "payload"),
    ),
    "work_summary_inputs": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("source_type", "TEXT", True, None, "payload"),
        ("source_ref", "TEXT", True, None, "payload"),
        ("payload_json", "TEXT", True, None, "payload"),
        ("status", "TEXT", True, "'pending'", "payload"),
        ("attempts", "INTEGER", True, "0", "payload"),
        ("error", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
        ("available_at", "TEXT", True, "''", "payload"),
        ("source_created_at", "TEXT", True, "''", "payload"),
        ("body_sha256", "TEXT", True, "''", "payload"),
        ("body_bytes", "INTEGER", True, "0", "payload"),
        ("body_compacted", "INTEGER", True, "0", "payload"),
    ),
    "meeting_alignment_jobs": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("meeting_id", "TEXT", True, None, "payload"),
        ("title", "TEXT", True, "''", "payload"),
        ("source_json", "TEXT", True, "'{}'", "payload"),
        ("participants_json", "TEXT", True, "'[]'", "payload"),
        ("ended_at", "TEXT", True, "''", "payload"),
        ("eligible_at", "TEXT", True, "''", "payload"),
        ("status", "TEXT", True, "'waiting'", "payload"),
        ("attempts", "INTEGER", True, "0", "payload"),
        ("locked_at", "TEXT", False, None, "payload"),
        ("available_at", "TEXT", True, "''", "payload"),
        ("error", "TEXT", True, "''", "payload"),
        ("decision_json", "TEXT", True, "'{}'", "payload"),
        ("target_kind", "TEXT", True, "''", "payload"),
        ("target_id", "TEXT", True, "''", "payload"),
        ("target_title", "TEXT", True, "''", "payload"),
        ("mentions_json", "TEXT", True, "'[]'", "payload"),
        ("final_message", "TEXT", True, "''", "payload"),
        ("send_result_json", "TEXT", True, "'{}'", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
        ("calendar_summary_status", "TEXT", True, "'not_started'", "payload"),
        ("calendar_summary_result_json", "TEXT", True, "'{}'", "payload"),
    ),
    "okr_review_requests": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("conversation_id", "TEXT", True, None, "payload"),
        ("conversation_title", "TEXT", True, None, "payload"),
        ("trigger_message_id", "TEXT", True, None, "payload"),
        ("trigger_sender", "TEXT", True, None, "payload"),
        ("trigger_sender_user_id", "TEXT", True, "''", "payload"),
        ("trigger_text", "TEXT", True, None, "payload"),
        ("period_label", "TEXT", True, None, "payload"),
        ("period_start", "TEXT", True, None, "payload"),
        ("period_end", "TEXT", True, None, "payload"),
        ("okr_source_json", "TEXT", True, "'{}'", "payload"),
        ("status", "TEXT", True, "'pending'", "payload"),
        ("error", "TEXT", True, "''", "payload"),
        ("codex_session_id", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "weekly_okr_analysis_jobs": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("week_end", "TEXT", True, None, "payload"),
        ("manager_user_id", "TEXT", True, None, "payload"),
        ("source_digest", "TEXT", True, None, "payload"),
        ("status", "TEXT", True, "'running'", "payload"),
        ("lease_owner", "TEXT", True, "''", "payload"),
        ("lease_expires_at", "TEXT", True, "''", "payload"),
        ("error", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("finished_at", "TEXT", True, "''", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "workbench_tasks": (
        ("id", "TEXT", False, None, "legacy_key"),
        ("title", "TEXT", True, None, "payload"),
        ("runtime_kind", "TEXT", True, None, "payload"),
        ("provider_session_ref", "TEXT", True, "''", "payload"),
        ("archived_at", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "workbench_turns": (
        ("id", "TEXT", False, None, "legacy_key"),
        ("task_id", "INTEGER", True, None, "parent_id"),
        ("client_request_id", "TEXT", True, None, "payload"),
        ("task_sequence", "INTEGER", True, None, "payload"),
        ("user_text", "TEXT", True, None, "payload"),
        ("status", "TEXT", True, None, "payload"),
        ("stop_requested", "INTEGER", True, "0", "payload"),
        ("final_text", "TEXT", False, None, "none"),
        ("error_code", "TEXT", True, "''", "payload"),
        ("error_detail", "TEXT", True, "''", "payload"),
        ("resume_context", "TEXT", True, "''", "payload"),
        ("lease_owner", "TEXT", True, "''", "payload"),
        ("lease_expires_at", "TEXT", True, "''", "payload"),
        ("execution_run_id", "TEXT", True, "''", "payload"),
        ("runtime_quiesced_run_id", "TEXT", True, "''", "payload"),
        ("started_at", "TEXT", True, "''", "payload"),
        ("completed_at", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "business_object_tasks": (
        ("business_object_key", "TEXT", False, None, "payload"),
        ("reply_task_id", "INTEGER", True, None, "parent_id"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "email_agent_classification_tasks": (
        ("task_id", "TEXT", False, None, "payload"),
        ("channel", "TEXT", True, None, "payload"),
        ("stable_message_identity", "TEXT", True, None, "payload"),
        ("status", "TEXT", True, None, "payload"),
        ("owner", "TEXT", True, "''", "payload"),
        ("generation", "INTEGER", True, "0", "payload"),
        ("attempt_count", "INTEGER", True, "0", "payload"),
        ("lease_expires_at", "TEXT", True, "''", "payload"),
        ("available_at", "TEXT", True, "''", "payload"),
        ("input_json", "TEXT", True, None, "payload"),
        ("result_json", "TEXT", True, "'null'", "payload"),
        ("error", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "email_historical_candidates": (
        ("account_id", "TEXT", True, None, "payload"),
        ("folder", "TEXT", True, None, "payload"),
        ("model_id", "TEXT", True, None, "payload"),
        ("stable_message_identity", "TEXT", True, None, "payload"),
        ("normalized_text", "TEXT", True, None, "payload"),
        ("provider_message_json", "TEXT", True, None, "payload"),
        ("uidvalidity", "INTEGER", True, None, "payload"),
        ("uid", "INTEGER", True, None, "payload"),
        ("state", "TEXT", True, None, "payload"),
        ("reason", "TEXT", True, "''", "payload"),
        ("attempted_at", "TEXT", True, "''", "payload"),
        ("next_retry_at", "TEXT", True, "''", "payload"),
        ("updated_at", "TEXT", True, None, "payload"),
    ),
    "feedback_iteration_decisions": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("batch_id", "TEXT", True, None, "payload"),
        ("workbench_task_id", "TEXT", True, None, "payload"),
        ("workbench_turn_id", "TEXT", True, None, "payload"),
        ("decision_json", "TEXT", True, None, "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "feedback_processing_batches": (
        ("batch_id", "TEXT", False, None, "payload"),
        ("status", "TEXT", True, "'pending'", "payload"),
        ("requested_count", "INTEGER", True, "0", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
        ("resolved_at", "TEXT", True, "''", "payload"),
    ),
    "feedback_processing_items": (
        ("feedback_key", "TEXT", False, None, "payload"),
        ("batch_id", "TEXT", True, "''", "payload"),
        ("status", "TEXT", True, "'pending'", "payload"),
        ("workbench_task_id", "TEXT", True, "''", "payload"),
        ("workbench_turn_id", "TEXT", True, "''", "payload"),
        ("attempt_id", "INTEGER", True, "0", "payload"),
        ("agent_run_id", "INTEGER", True, "0", "payload"),
        ("commit_sha", "TEXT", True, "''", "payload"),
        ("test_evidence_json", "TEXT", True, "'{}'", "payload"),
        ("restart_evidence_json", "TEXT", True, "'{}'", "payload"),
        ("health_evidence_json", "TEXT", True, "'{}'", "payload"),
        ("note", "TEXT", True, "''", "payload"),
        ("resolved_at", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
        ("current_round_id", "INTEGER", True, "0", "payload"),
    ),
    "feedback_processing_rounds": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("feedback_key", "TEXT", True, None, "payload"),
        ("round_number", "INTEGER", True, None, "payload"),
        ("batch_id", "TEXT", True, "''", "payload"),
        ("status", "TEXT", True, None, "payload"),
        ("workbench_task_id", "TEXT", True, "''", "payload"),
        ("workbench_turn_id", "TEXT", True, "''", "payload"),
        ("attempt_id", "INTEGER", True, "0", "payload"),
        ("agent_run_id", "INTEGER", True, "0", "payload"),
        ("commit_sha", "TEXT", True, "''", "payload"),
        ("test_evidence_json", "TEXT", True, "'{}'", "payload"),
        ("restart_evidence_json", "TEXT", True, "'{}'", "payload"),
        ("health_evidence_json", "TEXT", True, "'{}'", "payload"),
        ("backlog_evidence_json", "TEXT", True, "'{}'", "payload"),
        ("receipt_version", "INTEGER", True, "1", "payload"),
        ("note", "TEXT", True, "''", "payload"),
        ("started_at", "TEXT", True, "''", "payload"),
        ("resolved_at", "TEXT", True, "''", "payload"),
        ("reopened_at", "TEXT", True, "''", "payload"),
        ("reopen_reason", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
        ("scope_receipt_json", "TEXT", True, "'{}'", "payload"),
    ),
    "oa_notification_events": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("event_kind", "TEXT", True, None, "payload"),
        ("requires_reply", "INTEGER", True, "0", "payload"),
        ("process_instance_id", "TEXT", True, "''", "payload"),
        ("task_id", "TEXT", True, "''", "payload"),
        ("channel", "TEXT", True, "'dingtalk'", "payload"),
        ("conversation_id", "TEXT", True, None, "payload"),
        ("conversation_title", "TEXT", True, "''", "payload"),
        ("trigger_message_id", "TEXT", True, None, "payload"),
        ("trigger_create_time", "TEXT", True, "''", "payload"),
        ("trigger_sender", "TEXT", True, "''", "payload"),
        ("trigger_text", "TEXT", True, "''", "payload"),
        ("trigger_message_json", "TEXT", True, "'{}'", "payload"),
        ("oa_url", "TEXT", True, "''", "payload"),
        ("status", "TEXT", True, "'pending'", "payload"),
        ("claimed_attempt_id", "INTEGER", True, "0", "payload"),
        ("result_attempt_id", "INTEGER", True, "0", "payload"),
        ("last_error", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
        ("sent_at", "TEXT", True, "''", "payload"),
    ),
    "okr_review_items": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("request_id", "INTEGER", True, None, "payload"),
        ("objective_title", "TEXT", True, None, "payload"),
        ("objective_weight", "REAL", True, "0", "payload"),
        ("kr_title", "TEXT", True, None, "payload"),
        ("kr_weight", "REAL", True, "0", "payload"),
        ("item_json", "TEXT", True, "'{}'", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "wechat_memory_import_jobs": (
        ("id", "INTEGER", False, None, "legacy_key"),
        ("import_run_id", "TEXT", True, None, "payload"),
        ("account_id", "TEXT", True, None, "payload"),
        ("status", "TEXT", True, "'running'", "payload"),
        ("error", "TEXT", True, "''", "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
        ("finished_at", "TEXT", True, "''", "payload"),
        ("updated_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "workbench_artifacts": (
        ("id", "TEXT", False, None, "payload"),
        ("turn_id", "INTEGER", True, None, "parent_id"),
        ("label", "TEXT", True, None, "payload"),
        ("path", "TEXT", True, None, "payload"),
        ("media_type", "TEXT", True, None, "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
    ),
    "workbench_attachments": (
        ("id", "TEXT", False, None, "payload"),
        ("task_id", "INTEGER", True, None, "parent_id"),
        ("client_request_id", "TEXT", True, None, "payload"),
        ("filename", "TEXT", True, None, "payload"),
        ("media_type", "TEXT", True, None, "payload"),
        ("size_bytes", "INTEGER", True, None, "payload"),
        ("content_sha256", "TEXT", True, None, "payload"),
        ("storage_path", "TEXT", True, None, "payload"),
        ("created_at", "TEXT", True, "current_timestamp", "payload"),
    ),
}

TASK20_NEW_KINDS = (
    "business_object_tasks",
    "email_agent_classification_tasks",
    "email_historical_candidates",
    "feedback_iteration_decisions",
    "feedback_processing_batches",
    "feedback_processing_items",
    "feedback_processing_rounds",
    "oa_notification_events",
    "okr_review_items",
    "wechat_memory_import_jobs",
    "workbench_artifacts",
    "workbench_attachments",
)

TASK20_TEXT_PUBLIC_KINDS = ("workbench_tasks", "workbench_turns")

TASK20_NATIVE_INDEXES = (
    (
        "business_object_tasks",
        "sqlite_autoindex_business_object_tasks_1",
        True,
        (("business_object_key", False),),
    ),
    (
        "email_agent_classification_tasks",
        "idx_email_agent_classification_tasks_status",
        False,
        (
            ("status", False),
            ("available_at", False),
            ("lease_expires_at", False),
            ("task_id", False),
        ),
    ),
    (
        "email_agent_classification_tasks",
        "sqlite_autoindex_email_agent_classification_tasks_2",
        True,
        (("stable_message_identity", False),),
    ),
    (
        "email_agent_classification_tasks",
        "sqlite_autoindex_email_agent_classification_tasks_1",
        True,
        (("task_id", False),),
    ),
    (
        "email_historical_candidates",
        "sqlite_autoindex_email_historical_candidates_1",
        True,
        (
            ("account_id", False),
            ("folder", False),
            ("model_id", False),
            ("stable_message_identity", False),
        ),
    ),
    (
        "feedback_iteration_decisions",
        "idx_feedback_iteration_decisions_batch",
        False,
        (("batch_id", False), ("id", False)),
    ),
    (
        "feedback_processing_batches",
        "sqlite_autoindex_feedback_processing_batches_1",
        True,
        (("batch_id", False),),
    ),
    (
        "feedback_processing_items",
        "idx_feedback_processing_items_batch",
        False,
        (("batch_id", False),),
    ),
    (
        "feedback_processing_items",
        "idx_feedback_processing_items_status",
        False,
        (("status", False),),
    ),
    (
        "feedback_processing_items",
        "sqlite_autoindex_feedback_processing_items_1",
        True,
        (("feedback_key", False),),
    ),
    (
        "feedback_processing_rounds",
        "idx_feedback_processing_rounds_batch",
        False,
        (("batch_id", False),),
    ),
    (
        "feedback_processing_rounds",
        "idx_feedback_processing_rounds_feedback",
        False,
        (("feedback_key", False), ("round_number", True)),
    ),
    (
        "feedback_processing_rounds",
        "sqlite_autoindex_feedback_processing_rounds_2",
        True,
        (("feedback_key", False), ("batch_id", False)),
    ),
    (
        "feedback_processing_rounds",
        "sqlite_autoindex_feedback_processing_rounds_1",
        True,
        (("feedback_key", False), ("round_number", False)),
    ),
    (
        "oa_notification_events",
        "idx_oa_notification_events_claim",
        False,
        (("status", False), ("claimed_attempt_id", False), ("id", False)),
    ),
    (
        "oa_notification_events",
        "idx_oa_notification_events_case_status",
        False,
        (
            ("process_instance_id", False),
            ("task_id", False),
            ("requires_reply", False),
            ("status", False),
            ("id", False),
        ),
    ),
    (
        "oa_notification_events",
        "sqlite_autoindex_oa_notification_events_1",
        True,
        (("channel", False), ("conversation_id", False), ("trigger_message_id", False)),
    ),
    (
        "wechat_memory_import_jobs",
        "idx_wechat_memory_import_jobs_status",
        False,
        (("status", False), ("id", False)),
    ),
    (
        "workbench_artifacts",
        "idx_workbench_artifacts_turn_created_id",
        False,
        (("turn_id", False), ("created_at", False), ("id", False)),
    ),
    (
        "workbench_artifacts",
        "idx_workbench_artifacts_created_id_turn",
        False,
        (("created_at", False), ("id", False), ("turn_id", False)),
    ),
    (
        "workbench_artifacts",
        "sqlite_autoindex_workbench_artifacts_1",
        True,
        (("id", False),),
    ),
    (
        "workbench_attachments",
        "idx_workbench_attachments_task_request",
        True,
        (("task_id", False), ("client_request_id", False)),
    ),
    (
        "workbench_attachments",
        "idx_workbench_attachments_task_created_id",
        False,
        (("task_id", False), ("created_at", False), ("id", False)),
    ),
    (
        "workbench_attachments",
        "sqlite_autoindex_workbench_attachments_1",
        True,
        (("id", False),),
    ),
)

TASK12_PRIMARY_KEYS = {
    "business_object_tasks": ("business_object_key",),
    "email_agent_classification_tasks": ("task_id",),
    "email_historical_candidates": (
        "account_id",
        "folder",
        "model_id",
        "stable_message_identity",
    ),
    "feedback_iteration_decisions": ("id",),
    "feedback_processing_batches": ("batch_id",),
    "feedback_processing_items": ("feedback_key",),
    "feedback_processing_rounds": ("id",),
    "oa_notification_events": ("id",),
    "okr_review_items": ("id",),
    "wechat_memory_import_jobs": ("id",),
    "workbench_artifacts": ("id",),
    "workbench_attachments": ("id",),
}

TASK12_AUTOINCREMENT_KINDS = (
    "feedback_iteration_decisions",
    "feedback_processing_rounds",
    "oa_notification_events",
    "okr_review_items",
    "wechat_memory_import_jobs",
)

TASK20_FIELDS = {
    kind: tuple(c[0] for c in columns) for kind, columns in TASK20_COLUMNS.items()
}
TASK20_PAYLOAD_FIELDS = {
    kind: tuple(c[0] for c in columns if c[4] == "payload")
    for kind, columns in TASK20_COLUMNS.items()
}
TASK20_LAYOUTS = {
    kind: PayloadLayout(fields) for kind, fields in TASK20_PAYLOAD_FIELDS.items()
}


TASK20_DEFAULTS = {
    kind: {c[0]: c[3] for c in columns if c[3] is not None}
    for kind, columns in TASK20_COLUMNS.items()
}
TASK20_PARENTS = {
    "reply_task_inputs": "reply_tasks",
    "workbench_turns": "workbench_tasks",
    "business_object_tasks": "reply_tasks",
    "workbench_artifacts": "workbench_turns",
    "workbench_attachments": "workbench_tasks",
    "feedback_iteration_decisions": "feedback_processing_batches",
}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def task20_column(kind, field):
    """The finite generated source-shaped field, including public identities."""
    if kind not in TASK20_FIELDS or field not in TASK20_FIELDS[kind]:
        raise ValueError("unsupported Tasks kind or source field")
    return _quote(kind + "__" + field)


def task20_projection_barrier(kind):
    """Prevent SQLite flattening from dropping virtual REAL read conversion."""
    return (
        " limit -1 offset 0"
        if any(column[1] == "REAL" for column in TASK20_COLUMNS[kind])
        else ""
    )


def task20_source_projection(kind, query):
    """Keep source suffix filtering/sorting outside the REAL flattening barrier."""
    barrier = task20_projection_barrier(kind)
    return "select * from(" + query + barrier + ") where 1" if barrier else query


def _checks(kind):
    def f(field):
        return task20_column(kind, field)

    if kind == "weekly_okr_analysis_jobs":
        return (f("status") + " in ('running','completed','failed')",)
    if kind == "workbench_turns":
        return (
            f("task_sequence") + ">0",
            f("stop_requested") + " in (0,1)",
            f("status")
            + " in ('queued','running','completed','stopped','failed')",
        )
    if kind == "email_agent_classification_tasks":
        return (
            f("task_id") + " is null or trim(" + f("task_id") + ")<>''",
            f("channel") + "='email'",
            "trim(" + f("stable_message_identity") + ")<>''",
            f("status") + " in ('pending','running','done','failed')",
            f("generation") + ">=0",
            f("attempt_count") + ">=0",
            "json_valid(" + f("input_json") + ")",
            "json_valid(" + f("result_json") + ")",
        )
    if kind == "email_historical_candidates":
        return (
            "json_valid(" + f("provider_message_json") + ")",
            f("state") + " in ('pending','deferred','terminal')",
        )
    if kind in ("feedback_processing_batches", "feedback_processing_items"):
        return (f("status") + " in ('pending','processing','resolved')",)
    if kind == "feedback_processing_rounds":
        return (
            "typeof(" + f("round_number") + ")='integer'",
            f("round_number") + ">0",
            f("status") + " in ('processing','resolved')",
            f("receipt_version") + " in (1,2)",
        )
    if kind == "oa_notification_events":
        return (
            f("event_kind") + " in ('system_notification','chat_reminder')",
            f("status")
            + " in ('pending','adopted','sending','sent','failed','ignored')",
        )
    if kind == "wechat_memory_import_jobs":
        return (f("status") + " in ('running','completed','failed')",)
    if kind == "workbench_attachments":
        return (f("size_bytes") + ">=0",)
    return ()


# These are existing published eight-kind UNIQUE/query contracts. The remaining
# native index columns/orders are captured as literal metadata above.
_PUBLISHED_INDEXES = (
    ("reply_tasks", True, ("channel", "conversation_id", "trigger_message_id")),
    ("reply_tasks", False, ("status", "id")),
    ("reply_tasks", False, ("channel", "status", "id")),
    ("reply_tasks", False, ("business_object_key",)),
    (
        "reply_task_inputs",
        True,
        ("channel", "conversation_id", "trigger_message_id", "input_revision_key"),
    ),
    ("reply_task_inputs", False, ("reply_task_id", "id")),
    ("work_summary_inputs", True, ("source_type", "source_ref")),
    ("work_summary_inputs", False, ("status", "id")),
    ("work_summary_inputs", False, ("updated_at", "id")),
    ("meeting_alignment_jobs", True, ("meeting_id",)),
    ("meeting_alignment_jobs", False, ("status", "available_at", "eligible_at", "id")),
    ("okr_review_requests", True, ("conversation_id", "trigger_message_id")),
    ("okr_review_requests", False, ("status", "id")),
    (
        "weekly_okr_analysis_jobs",
        True,
        ("week_end", "manager_user_id", "source_digest"),
    ),
    ("workbench_tasks", False, ("updated_at", "id")),
    ("workbench_turns", True, ("client_request_id",)),
    ("workbench_turns", False, ("status", "created_at", "id")),
    ("workbench_turns", False, ("status", "lease_expires_at")),
    ("workbench_turns", False, ("task_id", "created_at", "id")),
    ("workbench_turns", True, ("task_id", "task_sequence")),
)


def _definitions():
    kinds = tuple(TASK20_COLUMNS)
    parts = [
        "id integer primary key autoincrement",
        *TASK_RECORD_TYPES.declarations(kinds),
        "legacy_key text",
        "payload_json blob not null",
        "parent_id integer references tasks(id)",
        "current_input_id integer references tasks(id)",
        "claimed_input_id integer references tasks(id)",
    ]
    constraints = []
    for kind, columns in TASK20_COLUMNS.items():
        for field, affinity, required, default, storage in columns:
            if storage == "payload":
                value = _encoded_value("payload_json", TASK20_LAYOUTS[kind].path(field))
            elif storage == "legacy_key":
                value = (
                    "legacy_key"
                    if kind in TASK20_TEXT_PUBLIC_KINDS
                    else "cast(legacy_key as integer)"
                )
            elif storage == "none":
                value = "''"
            else:
                value = storage
            parts.append(
                task20_column(kind, field)
                + " "
                + affinity
                + " generated always as (case when record_type="
                + repr(kind)
                + " then "
                + value
                + " end) virtual"
            )
        payload = TASK20_PAYLOAD_FIELDS[kind]
        layout = TASK20_LAYOUTS[kind]
        terms = [layout.shape_sql()]
        terms.extend(
            _encoded_value_check("payload_json", layout.path(field)) for field in payload
        )
        terms.extend(
            task20_column(kind, c[0]) + " is not null" for c in columns if c[2]
        )
        terms.extend(_checks(kind))
        if kind in TASK20_NEW_KINDS:
            terms.append(
                "legacy_key is not null and legacy_key=cast(cast(legacy_key as integer) as text)"
            )
        if kind in TASK20_PARENTS:
            terms.append("parent_id is not null")
        else:
            terms.append("parent_id is null")
        if kind != "reply_tasks":
            terms.extend(("current_input_id is null", "claimed_input_id is null"))
        else:
            terms.append("legacy_key is null or legacy_key=cast(id as text)")
        constraints.append(
            "check(record_type<>"
            + repr(kind)
            + " or ("
            + " and ".join("(" + term + ")" for term in terms)
            + "))"
        )
    parts.append(
        "parent_type text generated always as (case when record_type="
        "'feedback_iteration_decisions' then 'feedback_processing_batches' end) virtual"
    )
    batch = task20_column("feedback_processing_batches", "batch_id")
    decision_batch = task20_column("feedback_iteration_decisions", "batch_id")
    constraints.append(
        "foreign key(parent_id,parent_type,"
        + decision_batch
        + ") references tasks(id,record_type,"
        + batch
        + ")"
    )
    indexes = [
        "create unique index idx_task20_numeric_identity on tasks(record_type,cast(legacy_key as integer)) "
        "where record_type not in ('workbench_tasks','workbench_turns')",
        "create unique index idx_task20_text_identity on tasks(record_type,legacy_key) "
        "where record_type in ('workbench_tasks','workbench_turns')",
        "create unique index idx_task20_batch_endpoint on tasks(id,record_type,"
        + batch
        + ")",
    ]
    for ordinal, (kind, unique, fields) in enumerate(_PUBLISHED_INDEXES):
        directions = (
            " desc"
            if kind == "work_summary_inputs" and fields == ("updated_at", "id")
            else ""
        )
        terms = [
            task20_column(kind, field)
            + (
                (" desc" if field == "task_sequence" else "")
                if kind == "workbench_turns"
                else directions
            )
            for field in fields
        ]
        indexes.append(
            "create "
            + ("unique " if unique else "")
            + "index idx_task20_published_"
            + str(ordinal)
            + " on tasks("
            + ",".join(terms)
            + ") where record_type="
            + repr(kind)
        )
    indexes.append(
        "create unique index idx_task20_active_turn on tasks(parent_id) "
        "where record_type='workbench_turns' and "
        + task20_column("workbench_turns", "status")
        + " in ('queued','running')"
    )
    for ordinal, (kind, source_name, unique, fields) in enumerate(
        TASK20_NATIVE_INDEXES
    ):
        terms = [
            task20_column(kind, field) + (" desc" if descending else "")
            for field, descending in fields
        ]
        if not unique:
            terms.append("cast(legacy_key as integer)")
        indexes.append(
            "create "
            + ("unique " if unique else "")
            + "index idx_task20_native_"
            + str(ordinal)
            + " on tasks("
            + ",".join(terms)
            + ") where record_type="
            + repr(kind)
        )
    # Retained source child references need full UNIQUE indexes, not partial ones.
    for kind, field in (
        ("feedback_iteration_decisions", "id"),
        ("feedback_processing_items", "feedback_key"),
        ("feedback_processing_rounds", "id"),
    ):
        indexes.append(
            "create unique index idx_task20_endpoint_"
            + kind
            + " on tasks("
            + task20_column(kind, field)
            + ")"
        )
    triggers = [
        "create trigger trg_task20_type before update of record_type on tasks "
        "when new.record_type<>old.record_type begin select raise(abort,'task record type is immutable'); end",
        "create trigger trg_task20_public_key after insert on tasks when new.legacy_key is null "
        "and new.record_type in ('reply_tasks','reply_task_inputs','work_summary_inputs',"
        "'meeting_alignment_jobs','okr_review_requests','weekly_okr_analysis_jobs') begin "
        "update tasks set legacy_key=cast(new.id as text) where id=new.id; end",
    ]
    for event, suffix in (("insert", "insert"), ("update of parent_id", "update")):
        for kind, parent in TASK20_PARENTS.items():
            triggers.append(
                "create trigger trg_task20_parent_"
                + kind
                + "_"
                + suffix
                + " before "
                + event
                + " on tasks when new.record_type="
                + repr(kind)
                + " and exists(select 1 from tasks where id=new.parent_id and record_type<>"
                + repr(parent)
                + ") begin select raise(abort,'task parent type mismatch'); end"
            )
    for column in ("current_input_id", "claimed_input_id"):
        for event, suffix in (("insert", "insert"), ("update of " + column, "update")):
            triggers.append(
                "create trigger trg_task20_"
                + column
                + "_"
                + suffix
                + " before "
                + event
                + " on tasks when exists(select 1 from tasks where id=new."
                + column
                + " and record_type<>'reply_task_inputs') begin "
                "select raise(abort,'task input type mismatch'); end"
            )
    for event in ("update", "delete"):
        triggers.append(
            "create trigger trg_task20_decision_"
            + event
            + " before "
            + event
            + " on tasks when old.record_type='feedback_iteration_decisions' begin "
            "select raise(abort,'feedback iteration decisions are append-only'); end"
        )
    number = "new." + task20_column("feedback_processing_rounds", "round_number")
    for event, suffix in (("insert", "insert"), ("update of payload_json", "update")):
        triggers.append(
            "create trigger trg_task20_round_"
            + suffix
            + " before "
            + event
            + " on tasks when new.record_type='feedback_processing_rounds' and "
            "(typeof(" + number + ")<>'integer' or " + number + "<=0) begin "
            "select raise(abort,'feedback_processing_round_number_must_be_positive'); end"
        )
    for kind in TASK12_AUTOINCREMENT_KINDS:
        name = "tasks:" + kind
        triggers.append(
            "create trigger trg_task12_sequence_" + kind + " before insert on tasks "
            "when new.record_type=" + repr(kind) + " begin "
            "insert into sqlite_sequence(name,seq) select " + repr(name) + ",0 "
            "where not exists(select 1 from sqlite_sequence where name="
            + repr(name)
            + "); "
            "update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
            "where name=" + repr(name) + "; end"
        )
    # Published runtime inserts are single VALUES statements without physical id.
    # New source kinds share physical ids but must not advance the old allocator.
    high = (
        "max(coalesce((select seq from sqlite_sequence where name='tasks'),0),"
        "coalesce((select max(id) from tasks),0),0)"
    )
    allocated = (
        "case when new.id<>-1 then new.id when " + high
        + "<9223372036854775807 then " + high + "+1 else 0 end"
    )
    published = tuple(kind for kind in kinds if kind not in TASK20_NEW_KINDS)
    triggers.append(
        "create trigger trg_task8_sequence before insert on tasks "
        "when new.record_type in (" + ",".join(map(repr, published)) + ") begin "
        "update sqlite_sequence set seq=max(seq," + allocated + ") "
        "where name='tasks:published'; end"
    )
    # A newly assigned shared physical id can equal its source parent value.
    # Before-insert guards cannot see that new row; source separate tables could
    # not satisfy a missing Tasks parent by inserting the child itself.
    for kind in (
        "business_object_tasks",
        "workbench_artifacts",
        "workbench_attachments",
    ):
        triggers.append(
            "create trigger trg_task12_parent_after_"
            + kind
            + " after insert on tasks when new.record_type="
            + repr(kind)
            + " and exists(select 1 from tasks where id=new.parent_id and record_type<>"
            + repr(TASK20_PARENTS[kind])
            + ") begin select raise(abort,'task parent type mismatch'); end"
        )
    return (
        "create table tasks(" + ",".join(parts + constraints) + ")",
        tuple(indexes),
        tuple(TASK_RECORD_TYPES.trigger_sql(sql) for sql in triggers),
    )


TASK20_TABLE_DDL, TASK20_INDEX_DDL, TASK20_TRIGGER_DDL = _definitions()


def create_task20_schema(db):
    """Install the explicit target on a new connection; never select or commit."""
    for sql in (TASK20_TABLE_DDL, *TASK20_INDEX_DDL, *TASK20_TRIGGER_DDL):
        db.execute(sql)



def initialize_task8_sequence(db, source_water):
    """Set captured source water after bulk transfer, in the caller transaction.

    The migration metadata retains absent versus zero independently. Runtime
    uses single-VALUES INSERT/IGNORE/UPSERT; multi-row INSERT and OR FAIL are
    outside the observed published caller contract.
    """
    if not db.in_transaction:
        raise ValueError("Tasks allocator initialization requires a caller transaction")
    db.execute("delete from main.sqlite_sequence where name='tasks:published'")
    db.execute(
        "insert into main.sqlite_sequence(name,seq) values('tasks:published',?)",
        (source_water,),
    )


def task20_projection(kind):
    """Native source-shaped projection; existing public DAO parent aliases remain."""
    if kind not in TASK20_FIELDS:
        raise ValueError("unsupported Tasks kind")
    result = []
    for field in TASK20_FIELDS[kind]:
        value = task20_column(kind, field)
        if kind == "reply_tasks" and field == "id":
            # Match the existing numeric identity index directly. Keep the
            # public key's NULL semantics instead of substituting physical ID.
            value = "cast(legacy_key as integer)"
        elif kind == "reply_task_inputs" and field == "reply_task_id":
            value = "(select cast(p.legacy_key as integer) from tasks p where p.id=tasks.parent_id)"
        elif kind == "workbench_turns" and field == "task_id":
            value = "(select p.legacy_key from tasks p where p.id=tasks.parent_id)"
        elif kind == "reply_tasks" and field in (
            "current_input_id",
            "claimed_input_id",
        ):
            value = (
                "(select cast(p.legacy_key as integer) from tasks p where p.id=tasks."
                + field
                + ")"
            )
        result.append(value + " as " + _quote(field))
    scope = "record_type=" + repr(kind)
    if kind == "reply_tasks":
        # SQLite does not infer this partial-index predicate from equality.
        # The declared numeric identity index uses this same finite scope.
        scope += " and record_type not in (" + ",".join(map(repr, TASK20_TEXT_PUBLIC_KINDS)) + ")"
    return task20_source_projection(
        kind,
        "select " + ",".join(result) + " from tasks where " + scope,
    )
