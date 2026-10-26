"""The ordinary tables of a database built by the current Store and Email constructors."""

CURRENT_TABLES = frozenset({
    "action_entity_identity_map", "action_observers", "action_receipts", "actions",
    "business", "codex_session_search_index", "configs", "email_classification_inputs",
    "email_classifier_runtime_samples", "email_historical_candidates",
    "email_schema_migrations", "emails", "feedback_iteration_decisions",
    "feedback_processing_batches", "feedback_processing_items",
    "feedback_processing_rounds", "oa_notification_events", "okr_review_items",
    "provenance", "provider_state", "reply_attempt_receipt_refs", "run_adopted_results",
    "run_adopted_reviews", "run_adopted_selections", "run_adopted_supplements",
    "run_adoption_identity_map", "run_entity_identity_map", "run_execution_progress",
    "run_failures", "run_projections", "run_provenance", "runs", "runtime_attempts",
    "schedule_occurrences", "schedules", "setup_wizard_events", "state",
    "task_business_keys", "task_inputs", "task_results", "task_run_identity_map",
    "tasks", "work_summary_inputs", "workbench_artifacts", "workbench_attachments",
    "workbench_events", "workbench_tasks", "workbench_turns",
})
