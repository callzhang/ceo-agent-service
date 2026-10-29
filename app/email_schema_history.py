"""Historical Email schema operations for explicit release and inverse tools only.

Runtime EmailStore construction does not load or run these transitions. Source
versions remain validated here so an inverse can check its restored declaration.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from app.action_storage import REPLY_VARIANTS
from app.action_email_definitions import EMAIL_ACTION_PROJECTIONS
from app.email_category_config import (
    CATEGORY_CONFIG_CHECKS, CATEGORY_CONFIG_COLUMN_CONTRACTS,
    FOLDER_BINDING_CHECKS, FOLDER_BINDING_COLUMN_CONTRACTS,
)
from app.business_legacy_fields import current_action_objects
from app.configs_definitions import CONFIG_FIELDS
from app.email_current_schema import current_canonical_email_parent_is_current
from app.email_store import (
    EmailStore, EmailPersistenceCorruption,
    _json_load,
    _schema_identifier, _schema_metadata_text,
)
from app.logical_run_current_schema import current_configs_schema_is_current
from app.provider_state_schema_current import provider_state_schema_is_current
from app.state_definitions import STATE_FIELDS
from app.state_schema_current import state_schema_is_current
from app.task20_definitions import TASK20_NEW_KINDS
from app.task_lifecycle_current_schema import task20_schema_is_current
from app.action_email_storage import finite_target_external_sql

_REQUIRED_WITHOUT_ROWID_TABLES = frozenset(
    {
        "email_model_promotion_configs",
        "email_model_mode_transitions",
        "email_category_description_revisions",
    }
)
DIRECT_RETRY_TABLE_SQL = """
    create table if not exists email_direct_retry_requests (
        action_id text not null check(trim(action_id) != ''),
        attempt_number integer not null check(attempt_number > 0),
        source_readback_digest text not null check(length(source_readback_digest) = 64),
        account_configuration_snapshot text not null check(json_valid(account_configuration_snapshot))
            check(json_type(account_configuration_snapshot) = 'array')
            check(json_array_length(account_configuration_snapshot) = 9),
        requested_at text not null check(trim(requested_at) != ''),
        primary key(action_id, attempt_number),
        foreign key(action_id) references email_actions(action_id) on delete restrict
    )
"""
_ColumnContract = tuple[str, bool, str | None]
_REQUIRED_COLUMN_CONTRACTS: Mapping[str, Mapping[str, _ColumnContract]] = {
    "email_category_description_revisions": {
        "revision_id": ("text", True, None),
        "category_key": ("text", True, None),
        "config_version": ("text", True, None),
        "config_json": ("text", True, None),
        "created_at": ("text", True, None),
    },
    "email_model_promotion_configs": {
        "config_version": ("text", True, None),
        "micro_f1_min": ("real", True, None),
        "category_precision_min": ("real", True, None),
        "category_validation_samples_min": ("integer", True, None),
        "p95_latency_max_ms": ("real", True, None),
        "created_at": ("text", True, None),
    },
    "email_model_mode_transitions": {
        "request_id": ("text", True, None),
        "actor": ("text", True, None),
        "from_mode": ("text", True, None),
        "to_mode": ("text", True, None),
        "from_model_id": ("text", False, None),
        "target_model_id": ("text", False, None),
        "promotion_config_version": ("text", True, None),
        "status": ("text", True, None),
        "reason": ("text", True, None),
        "created_at": ("text", True, None),
    },
    "email_schema_migrations": {
        "version": ("integer", False, None),
        "applied_at": ("text", True, None),
    },
    "email_classifications": {
        "id": ("integer", False, None),
        "account_id": ("text", True, None),
        "folder": ("text", True, None),
        "uidvalidity": ("integer", True, None),
        "uid": ("integer", True, None),
        "rfc_message_id": ("text", False, None),
        "thread_id": ("text", False, None),
        "stable_message_identity": ("text", True, None),
        "sender": ("text", True, "''"),
        "subject": ("text", True, "''"),
        "preview": ("text", True, "''"),
        "model_text": ("text", True, "''"),
        "received_at": ("text", True, "''"),
        "category": ("text", False, None),
        "predicted_category": ("text", False, None),
        "confirmed_category": ("text", False, None),
        "confidence": ("real", True, None),
        "margin": ("real", True, None),
        "probabilities_json": ("text", True, None),
        "model_id": ("text", True, None),
        "config_version": ("text", True, None),
        "status": ("text", True, None),
        "classification_source": ("text", True, None),
        "agent_result_json": ("text", True, "'null'"),
        "current_action_plan_id": ("text", False, None),
        "included_in_model_id": ("text", False, None),
        "legacy_processed_without_plan": ("integer", True, "0"),
        "confirmed_at": ("text", True, "''"),
        "created_at": ("text", True, "current_timestamp"),
        "updated_at": ("text", True, "current_timestamp"),
    },
    "email_agent_classification_tasks": {
        "task_id": ("text", False, None),
        "channel": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "status": ("text", True, None),
        "owner": ("text", True, "''"),
        "generation": ("integer", True, "0"),
        "attempt_count": ("integer", True, "0"),
        "lease_expires_at": ("text", True, "''"),
        "available_at": ("text", True, "''"),
        "input_json": ("text", True, None),
        "result_json": ("text", True, "'null'"),
        "error": ("text", True, "''"),
        "created_at": ("text", True, "current_timestamp"),
        "updated_at": ("text", True, "current_timestamp"),
    },
    "email_category_configs": CATEGORY_CONFIG_COLUMN_CONTRACTS,
    "email_category_folder_bindings": FOLDER_BINDING_COLUMN_CONTRACTS,
    "email_accounts": {
        "account_id": ("text", False, None),
        "display_name": ("text", True, None),
        "email_address": ("text", True, None),
        "imap_host": ("text", True, None),
        "imap_port": ("integer", True, None),
        "imap_tls": ("integer", True, None),
        "imap_username": ("text", True, None),
        "imap_secret_reference": ("text", True, None),
        "imap_move_mode": ("text", True, "'move'"),
        "smtp_host": ("text", True, None),
        "smtp_port": ("integer", True, None),
        "smtp_tls": ("integer", True, None),
        "smtp_username": ("text", True, None),
        "smtp_secret_reference": ("text", True, None),
        "enabled": ("integer", True, None),
        "scan_folders_json": ("text", True, None),
        "scan_interval_seconds": ("integer", True, None),
        "created_at": ("text", True, None),
        "updated_at": ("text", True, None),
        "agent_lookback_days": ("integer", True, "30"),
        "model_lookback_days": ("integer", True, "365"),
        "scan_read_state": ("text", True, "'unread'"),
    },
    "email_scan_cursors": {
        "account_id": ("text", True, None),
        "folder": ("text", True, None),
        "uidvalidity": ("integer", True, None),
        "last_seen_uid": ("integer", True, None),
        "last_success_at": ("text", True, "''"),
        "last_error": ("text", True, "''"),
    },
    "email_messages": {
        "id": ("integer", False, None),
        "account_id": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "folder": ("text", True, None),
        "uidvalidity": ("integer", True, None),
        "uid": ("integer", True, None),
        "rfc_message_id": ("text", True, None),
        "in_reply_to": ("text", True, "''"),
        "references_json": ("text", True, "'[]'"),
        "thread_identity": ("text", True, None),
        "sender": ("text", True, None),
        "recipients_json": ("text", True, None),
        "subject": ("text", True, None),
        "normalized_text": ("text", True, None),
        "preview": ("text", True, None),
        "attachment_metadata_json": ("text", True, None),
        "received_at": ("text", True, None),
        "created_at": ("text", True, None),
        "updated_at": ("text", True, None),
    },
    "email_action_plans": {
        "action_plan_id": ("text", False, None),
        "action_plan_version": ("integer", True, None),
        "classification_id": ("integer", True, None),
        "account_id": ("text", True, None),
        "category": ("text", True, None),
        "classification_source": ("text", True, None),
        "confidence": ("real", True, None),
        "model_id": ("text", True, None),
        "config_version": ("text", True, None),
        "actions_json": ("text", True, None),
        "action_parameters_json": ("text", True, None),
        "authorization_snapshot_json": ("text", False, None),
        "legacy_serialization_pre_v16": ("integer", True, "0"),
        "created_at": ("text", True, None),
    },
    "email_actions": {
        "action_id": ("text", False, None),
        "action_plan_id": ("text", True, None),
        "classification_id": ("integer", True, None),
        "account_id": ("text", True, None),
        "action_type": ("text", True, None),
        "parameters_json": ("text", True, None),
        "config_version": ("text", True, None),
        "status": ("text", True, None),
        "attempt_count": ("integer", True, "0"),
        "started_at": ("text", True, "''"),
        "finished_at": ("text", True, "''"),
        "next_attempt_at": ("text", True, "''"),
        "provider_operation": ("text", True, "''"),
        "provider_target": ("text", True, "''"),
        "provider_result_id": ("text", True, "''"),
        "error": ("text", True, "''"),
        "created_at": ("text", True, None),
        "updated_at": ("text", True, None),
    },
    "email_action_attempts": {
        "id": ("integer", False, None),
        "action_id": ("text", True, None),
        "attempt_number": ("integer", True, None),
        "status": ("text", True, None),
        "provider_operation": ("text", True, None),
        "provider_target": ("text", True, None),
        "provider_result_id": ("text", True, None),
        "error": ("text", True, None),
        "started_at": ("text", True, None),
        "finished_at": ("text", True, None),
    },
    "email_feedback_requests": {
        "feedback_request_id": ("text", False, None),
        "classification_id": ("integer", True, None),
        "category": ("text", True, None),
        "expected_current_action_plan_id": ("text", False, None),
        "resulting_action_plan_id": ("text", True, None),
        "applied_at": ("text", True, None),
    },
    "email_reply_receipts": {
        "action_identity": ("text", False, None),
        "effect_digest": ("text", True, None),
        "action_plan_id": ("text", True, None),
        "classification_id": ("integer", True, None),
        "account_id": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "outgoing_message_id": ("text", True, None),
        "provider_operation": ("text", True, None),
        "provider_target": ("text", True, None),
        "provider_result_id": ("text", True, None),
        "provider_receipt_json": ("text", True, None),
        "display_excerpt": ("text", True, None),
        "created_at": ("text", True, None),
    },
    "email_reply_dispatch_claims": {
        "action_identity": ("text", False, None),
        "effect_digest": ("text", True, None),
        "action_plan_id": ("text", True, None),
        "classification_id": ("integer", True, None),
        "account_id": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "outgoing_message_id": ("text", True, None),
        "owner_id": ("text", True, None),
        "owner_generation": ("integer", True, None),
        "lease_token": ("text", True, None),
        "sender": ("text", True, None),
        "thread_identity": ("text", True, None),
        "account_updated_at": ("text", True, None),
        "account_snapshot_json": ("text", True, None),
        "status": ("text", True, None),
        "claimed_at": ("text", True, None),
        "updated_at": ("text", True, None),
    },
    "email_unsubscribe_claims": {
        "action_identity": ("text", False, None),
        "effect_digest": ("text", True, None),
        "action_plan_id": ("text", True, None),
        "action_plan_version": ("integer", True, None),
        "classification_id": ("integer", True, None),
        "account_id": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "thread_identity": ("text", True, None),
        "entry_reference": ("text", True, None),
        "operations_json": ("text", True, None),
        "owner_id": ("text", True, None),
        "owner_generation": ("integer", True, None),
        "lease_token": ("text", True, None),
        "account_updated_at": ("text", True, None),
        "status": ("text", True, None),
        "phase": ("text", True, "'prepared'"),
        "audit_agent_run_id": ("integer", False, None),
        "claimed_at": ("text", True, None),
        "updated_at": ("text", True, None),
    },
    "email_unsubscribe_effects": {
        "action_identity": ("text", True, None),
        "effect_digest": ("text", True, None),
        "previous_effect_digest": ("text", True, "''"),
        "operations_json": ("text", True, None),
        "audit_agent_run_id": ("integer", False, None),
        "created_at": ("text", True, None),
    },
    "email_unsubscribe_continuations": {
        "action_identity": ("text", False, None),
        "effect_digest": ("text", True, None),
        "observation_reference": ("text", True, None),
        "controls_json": ("text", True, None),
        "created_at": ("text", True, None),
        "updated_at": ("text", True, None),
    },
    "email_unsubscribe_steps": {
        "id": ("integer", False, None),
        "action_identity": ("text", True, None),
        "effect_digest": ("text", True, None),
        "sequence": ("integer", True, None),
        "operation": ("text", True, None),
        "state": ("text", True, None),
        "reference": ("text", True, None),
        "created_at": ("text", True, None),
    },
    "email_unsubscribe_cancellations": {
        "action_identity": ("text", False, None),
        "decision_json": ("text", True, None),
    },
    "email_direct_retry_requests": {
        "action_id": ("text", True, None),
        "attempt_number": ("integer", True, None),
        "source_readback_digest": ("text", True, None),
        "account_configuration_snapshot": ("text", True, None),
        "requested_at": ("text", True, None),
    },
    "email_unsubscribe_receipts": {
        "action_identity": ("text", False, None),
        "effect_digest": ("text", True, None),
        "action_plan_id": ("text", True, None),
        "action_plan_version": ("integer", True, None),
        "classification_id": ("integer", True, None),
        "account_id": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "thread_identity": ("text", True, None),
        "entry_reference": ("text", True, None),
        "outcome": ("text", True, None),
        "receipt_id": ("text", True, None),
        "evidence": ("text", True, None),
        "result_text": ("text", True, "''"),
        "observation_digest": ("text", True, "''"),
        "started_at": ("text", True, "''"),
        "completed_at": ("text", True, "''"),
        "result_text_truncated": ("integer", True, "0"),
        "result_text_digest": ("text", True, "''"),
        "entry_url": ("text", True, "''"),
        "created_at": ("text", True, None),
    },
    "email_training_snapshots": {
        "snapshot_id": ("text", False, None),
        "snapshot_version": ("text", True, None),
        "description_version": ("text", True, None),
        "input_schema_version": ("text", True, None),
        "seed": ("integer", True, None),
        "observed_at": ("text", True, None),
        "snapshot_digest": ("text", True, None),
        "manifest_json": ("text", True, None),
        "frozen": ("integer", True, "1"),
        "created_at": ("text", True, None),
        "folder_label_watermark": ("integer", True, "0"),
        "important_label_watermark": ("integer", True, "0"),
    },
    "email_training_snapshot_observations": {
        "snapshot_id": ("text", True, None),
        "account_id": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "provider_folder_id": ("text", True, None),
        "provider_folder_name": ("text", True, None),
        "category_key": ("text", False, None),
        "important": ("integer", True, None),
        "normalized_model_input": ("text", True, None),
        "normalized_model_input_hash": ("text", True, None),
        "input_schema_version": ("text", True, None),
        "provider_thread_id": ("text", False, None),
        "normalized_body_digest": ("text", True, None),
        "sender_template_signature": ("text", False, None),
        "explicit_matter_group": ("text", False, None),
        "group_key": ("text", True, None),
        "observed_at": ("text", True, None),
        "source": ("text", True, None),
        "split": ("text", True, None),
        "selected_for_training": ("integer", True, None),
        "ordered_record_digest": ("text", True, None),
    },
    "email_historical_classification_history": {
        "event_id": ("text", False, None),
        "stable_message_identity": ("text", True, None),
        "model_id": ("text", True, None),
        "predicted_category": ("text", False, None),
        "threshold": ("real", False, None),
        "probability": ("real", False, None),
        "important": ("integer", False, None),
        "action_outcome": ("text", True, None),
        "created_at": ("text", True, None),
    },
    "email_classifier_runtime_samples": {
        "id": ("integer", False, None),
        "model_id": ("text", True, None),
        "outcome": ("text", True, None),
        "fallback_code": ("text", True, "''"),
        "cache_hit": ("integer", True, None),
        "runtime_warm": ("integer", True, None),
        "queue_ms": ("real", True, None),
        "http_ms": ("real", True, None),
        "embedding_ms": ("real", True, None),
        "head_ms": ("real", True, None),
        "total_ms": ("real", True, None),
        "recorded_at": ("text", True, None),
    },
}
_REQUIRED_TABLE_COLUMNS: Mapping[str, frozenset[str]] = {
    table: frozenset(columns) for table, columns in _REQUIRED_COLUMN_CONTRACTS.items()
}
_REQUIRED_TABLE_CHECKS: Mapping[str, tuple[str, ...]] = {
    "email_category_description_revisions": (
        "trim(revision_id) != ''",
        "trim(category_key) != ''",
        "trim(config_version) != ''",
        "json_valid(config_json)",
        "trim(created_at) != ''",
    ),
    "email_model_promotion_configs": (
        "trim(config_version) != ''",
        "micro_f1_min >= 0 and micro_f1_min <= 1",
        "category_precision_min >= 0 and category_precision_min <= 1",
        "typeof(category_validation_samples_min) = 'integer' and category_validation_samples_min > 0",
        "p95_latency_max_ms > 0 and (p95_latency_max_ms - p95_latency_max_ms) is 0.0",
        "trim(created_at) != ''",
    ),
    "email_model_mode_transitions": (
        "trim(request_id) != ''",
        "trim(actor) != ''",
        "from_mode in ('agent_primary', 'model_primary')",
        "to_mode in ('agent_primary', 'model_primary')",
        "from_model_id is null or trim(from_model_id) != ''",
        "target_model_id is null or trim(target_model_id) != ''",
        "trim(promotion_config_version) != ''",
        "status in ('applied', 'rejected', 'failed')",
        "trim(created_at) != ''",
    ),
    "email_classifications": ("legacy_processed_without_plan in (0, 1)",),
    "email_agent_classification_tasks": (
        "trim(task_id) != ''",
        "channel='email'",
        "trim(stable_message_identity) != ''",
        "status in ('pending','running','done','failed')",
        "generation >= 0",
        "attempt_count >= 0",
        "json_valid(input_json)",
        "json_valid(result_json)",
    ),
    "email_category_configs": CATEGORY_CONFIG_CHECKS,
    "email_category_folder_bindings": FOLDER_BINDING_CHECKS,
    "email_accounts": (
        "imap_port between 1 and 65535",
        "imap_tls in (0, 1)",
        "imap_move_mode in ('move', 'copy_as_move')",
        "smtp_port between 1 and 65535",
        "smtp_tls in (0, 1)",
        "enabled in (0, 1)",
        "json_valid(scan_folders_json)",
        "scan_interval_seconds > 0",
        "agent_lookback_days between 1 and 365",
        "model_lookback_days between 1 and 3650",
        "scan_read_state in ('unread', 'all')",
    ),
    "email_scan_cursors": (
        "uidvalidity > 0",
        "last_seen_uid >= 0",
    ),
    "email_messages": (
        "uidvalidity > 0",
        "uid > 0",
        "json_valid(recipients_json)",
        "json_valid(references_json)",
        "json_valid(attachment_metadata_json)",
    ),
    "email_action_plans": (
        "action_plan_version > 0",
        "classification_source in ('model', 'user', 'agent')",
        "confidence >= 0.0 and confidence <= 1.0",
        "json_valid(actions_json)",
        "json_valid(action_parameters_json)",
        "authorization_snapshot_json is null or json_valid(authorization_snapshot_json)",
        "legacy_serialization_pre_v16 in (0, 1)",
    ),
    "email_actions": (
        "action_type in ('label', 'mark_read', 'archive', 'move', 'trash', 'flag_important')",
        "json_valid(parameters_json)",
        "status in ('pending', 'processing', 'done', 'skipped', 'failed')",
        "attempt_count >= 0",
    ),
    "email_action_attempts": (
        "attempt_number > 0",
        "status in ('done', 'skipped', 'failed')",
    ),
    "email_feedback_requests": (
        "trim(feedback_request_id) != ''",
        "expected_current_action_plan_id is null or trim(expected_current_action_plan_id) != ''",
        "trim(resulting_action_plan_id) != ''",
        "trim(applied_at) != ''",
    ),
    "email_reply_receipts": (
        "trim(action_identity) != ''",
        "trim(effect_digest) != ''",
        "trim(outgoing_message_id) != ''",
        "trim(provider_operation) != ''",
        "trim(provider_result_id) != ''",
        "json_valid(provider_receipt_json)",
    ),
    "email_reply_dispatch_claims": (
        "trim(action_identity) != ''",
        "trim(effect_digest) != ''",
        "trim(outgoing_message_id) != ''",
        "trim(owner_id) != ''",
        "owner_generation > 0",
        "trim(lease_token) != ''",
        "trim(sender) != ''",
        "trim(thread_identity) != ''",
        "trim(account_updated_at) != ''",
        "json_valid(account_snapshot_json)",
        "status in ('dispatching', 'retryable', 'uncertain', 'done')",
        "trim(claimed_at) != ''",
        "trim(updated_at) != ''",
    ),
    "email_unsubscribe_claims": (
        "trim(action_identity) != ''",
        "trim(effect_digest) != ''",
        "action_plan_version > 0",
        "trim(thread_identity) != ''",
        "trim(entry_reference) != ''",
        "json_valid(operations_json)",
        "trim(owner_id) != ''",
        "owner_generation > 0",
        "trim(lease_token) != ''",
        "trim(account_updated_at) != ''",
        "status in ('dispatching', 'awaiting_audit', 'uncertain', 'done')",
        "phase in ('prepared', 'navigating', 'effect_uncertain', 'terminal')",
        "audit_agent_run_id is null or audit_agent_run_id > 0",
        "trim(claimed_at) != ''",
        "trim(updated_at) != ''",
    ),
    "email_unsubscribe_effects": (
        "trim(action_identity) != ''",
        "trim(effect_digest) != ''",
        "previous_effect_digest = '' or length(previous_effect_digest) = 64",
        "json_valid(operations_json)",
        "audit_agent_run_id is null or audit_agent_run_id > 0",
        "trim(created_at) != ''",
    ),
    "email_unsubscribe_continuations": (
        "trim(action_identity) != ''",
        "trim(effect_digest) != ''",
        "trim(observation_reference) != ''",
        "json_valid(controls_json)",
        "trim(created_at) != ''",
        "trim(updated_at) != ''",
    ),
    "email_unsubscribe_steps": (
        "trim(action_identity) != ''",
        "trim(effect_digest) != ''",
        "sequence > 0",
        "trim(operation) != ''",
        "trim(state) != ''",
        "trim(reference) != ''",
        "trim(created_at) != ''",
    ),
    "email_unsubscribe_cancellations": (
        "trim(action_identity) != ''",
        "json_valid(decision_json)",
    ),
    "email_direct_retry_requests": (
        "trim(action_id) != ''",
        "attempt_number > 0",
        "length(source_readback_digest) = 64",
        "json_valid(account_configuration_snapshot)",
        "json_type(account_configuration_snapshot) = 'array'",
        "json_array_length(account_configuration_snapshot) = 9",
        "trim(requested_at) != ''",
    ),
    "email_unsubscribe_receipts": (
        "trim(action_identity) != ''",
        "trim(effect_digest) != ''",
        "action_plan_version > 0",
        "trim(thread_identity) != ''",
        "trim(entry_reference) != ''",
        "outcome in ('done', 'already_unsubscribed', 'skipped_no_reliable_entry', 'skipped_login_required', 'skipped_captcha', 'skipped_payment', 'failed_browser', 'failed_provider_auth')",
        "trim(receipt_id) != ''",
        "trim(evidence) != ''",
        "length(result_text) <= 16384",
        "observation_digest = '' or length(observation_digest) = 64",
        "result_text_truncated in (0, 1)",
        "result_text_digest = '' or length(result_text_digest) = 64",
        "entry_url = '' or length(entry_url) <= 2048",
        "trim(created_at) != ''",
    ),
    "email_training_snapshots": (
        "trim(snapshot_id) != ''",
        "trim(snapshot_version) != ''",
        "trim(description_version) != ''",
        "trim(input_schema_version) != ''",
        "seed >= 0",
        "trim(observed_at) != ''",
        "length(snapshot_digest) = 64",
        "json_valid(manifest_json)",
        "frozen in (0, 1)",
        "trim(created_at) != ''",
        "folder_label_watermark >= 0",
        "important_label_watermark >= 0",
    ),
    "email_training_snapshot_observations": (
        "trim(snapshot_id) != ''",
        "trim(account_id) != ''",
        "trim(stable_message_identity) != ''",
        "trim(provider_folder_id) != ''",
        "trim(provider_folder_name) != ''",
        "category_key is null or trim(category_key) != ''",
        "important in (0, 1)",
        "trim(normalized_model_input) != ''",
        "length(normalized_model_input_hash) = 64",
        "trim(input_schema_version) != ''",
        "length(normalized_body_digest) = 64",
        "sender_template_signature is null or length(sender_template_signature) = 64",
        "length(group_key) = 64",
        "trim(observed_at) != ''",
        "source in ('natural', 'targeted', 'agent_auto_label', 'user_feedback')",
        "split in ('train', 'validation', 'test')",
        "selected_for_training in (0, 1)",
        "length(ordered_record_digest) = 64",
    ),
    "email_historical_classification_history": (
        "trim(event_id) != ''",
        "trim(stable_message_identity) != ''",
        "trim(model_id) != ''",
        "predicted_category is null or trim(predicted_category) != ''",
        "threshold is null or (threshold >= 0.0 and threshold <= 1.0)",
        "probability is null or (probability >= 0.0 and probability <= 1.0)",
        "important is null or important in (0, 1)",
        "trim(action_outcome) != ''",
        "trim(created_at) != ''",
    ),
    "email_classifier_runtime_samples": (
        "trim(model_id) != ''",
        "outcome in ('success', 'rejected', 'failure')",
        "length(fallback_code) <= 64",
        "cache_hit in (0, 1)",
        "runtime_warm in (0, 1)",
        "queue_ms >= 0",
        "http_ms >= 0",
        "embedding_ms >= 0",
        "head_ms >= 0",
        "total_ms >= 0",
        "trim(recorded_at) != ''",
    ),
}
_REQUIRED_AUTOINCREMENT_COLUMNS = frozenset(
    {
        ("email_messages", "id"),
        ("email_action_attempts", "id"),
        ("email_unsubscribe_steps", "id"),
        ("email_classifier_runtime_samples", "id"),
    }
)
_REQUIRED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "email_category_description_revisions": ("revision_id",),
    "email_model_promotion_configs": ("config_version",),
    "email_model_mode_transitions": ("request_id",),
    "email_schema_migrations": ("version",),
    "email_classifications": ("id",),
    "email_agent_classification_tasks": ("task_id",),
    "email_category_configs": ("category_key",),
    "email_category_folder_bindings": ("account_id", "category_key"),
    "email_accounts": ("account_id",),
    "email_scan_cursors": ("account_id", "folder"),
    "email_messages": ("id",),
    "email_action_plans": ("action_plan_id",),
    "email_actions": ("action_id",),
    "email_action_attempts": ("id",),
    "email_feedback_requests": ("feedback_request_id",),
    "email_reply_receipts": ("action_identity",),
    "email_reply_dispatch_claims": ("action_identity",),
    "email_unsubscribe_claims": ("action_identity",),
    "email_unsubscribe_effects": ("action_identity", "effect_digest"),
    "email_unsubscribe_continuations": ("action_identity",),
    "email_unsubscribe_steps": ("id",),
    "email_unsubscribe_receipts": ("action_identity",),
    "email_unsubscribe_cancellations": ("action_identity",),
    "email_direct_retry_requests": ("action_id", "attempt_number"),
    "email_training_snapshots": ("snapshot_id",),
    "email_training_snapshot_observations": (
        "snapshot_id",
        "account_id",
        "stable_message_identity",
    ),
    "email_historical_classification_history": ("event_id",),
    "email_classifier_runtime_samples": ("id",),
}
_REQUIRED_UNIQUE_KEYS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "email_classifications": (("stable_message_identity",),),
    "email_agent_classification_tasks": (("stable_message_identity",),),
    "email_messages": (("stable_message_identity",),),
    "email_action_plans": (("classification_id", "action_plan_version"),),
    "email_actions": (("action_plan_id", "action_type"),),
    "email_action_attempts": (("action_id", "attempt_number"),),
    "email_feedback_requests": (
        ("resulting_action_plan_id",),
        ("expected_current_action_plan_id",),
    ),
    "email_reply_receipts": (("outgoing_message_id",),),
    "email_reply_dispatch_claims": (("outgoing_message_id",),),
    "email_unsubscribe_steps": (("action_identity", "sequence"),),
    "email_training_snapshots": (("snapshot_digest",),),
}
_REQUIRED_FOREIGN_KEYS: Mapping[
    str,
    tuple[tuple[str, str, str, str], ...],
] = {
    "email_model_mode_transitions": (
        (
            "promotion_config_version",
            "email_model_promotion_configs",
            "config_version",
            "RESTRICT",
        ),
    ),
    "email_category_folder_bindings": (
        ("account_id", "email_accounts", "account_id", "CASCADE"),
        ("category_key", "email_category_configs", "category_key", "CASCADE"),
    ),
    "email_action_plans": (
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_actions": (
        ("action_plan_id", "email_action_plans", "action_plan_id", "RESTRICT"),
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_action_attempts": (("action_id", "email_actions", "action_id", "RESTRICT"),),
    "email_direct_retry_requests": (("action_id", "email_actions", "action_id", "RESTRICT"),),
    "email_feedback_requests": (
        ("classification_id", "email_classifications", "id", "RESTRICT"),
        (
            "expected_current_action_plan_id",
            "email_action_plans",
            "action_plan_id",
            "RESTRICT",
        ),
        (
            "resulting_action_plan_id",
            "email_action_plans",
            "action_plan_id",
            "RESTRICT",
        ),
    ),
    "email_reply_receipts": (
        ("action_plan_id", "email_action_plans", "action_plan_id", "RESTRICT"),
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_reply_dispatch_claims": (
        ("action_plan_id", "email_action_plans", "action_plan_id", "RESTRICT"),
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_unsubscribe_claims": (
        ("action_plan_id", "email_action_plans", "action_plan_id", "RESTRICT"),
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_unsubscribe_effects": (
        ("action_identity", "email_unsubscribe_claims", "action_identity", "RESTRICT"),
    ),
    "email_unsubscribe_continuations": (
        ("action_identity", "email_unsubscribe_claims", "action_identity", "RESTRICT"),
    ),
    "email_unsubscribe_steps": (
        ("action_identity", "email_unsubscribe_claims", "action_identity", "RESTRICT"),
    ),
    "email_unsubscribe_receipts": (
        ("action_plan_id", "email_action_plans", "action_plan_id", "RESTRICT"),
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_training_snapshot_observations": (
        ("snapshot_id", "email_training_snapshots", "snapshot_id", "RESTRICT"),
    ),
}
_REQUIRED_INDEXES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "idx_email_classifications_status": (
        "email_classifications",
        ("status", "updated_at", "id"),
    ),
    "idx_email_classifications_account_status": (
        "email_classifications",
        ("account_id", "status", "updated_at"),
    ),
    "idx_email_classifications_account_locator": (
        "email_classifications",
        ("account_id", "folder", "uidvalidity", "status", "updated_at", "uid"),
    ),
    "idx_email_agent_classification_tasks_status": (
        "email_agent_classification_tasks",
        ("status", "available_at", "lease_expires_at", "task_id"),
    ),
    "idx_email_messages_account_locator": (
        "email_messages",
        ("account_id", "folder", "uidvalidity", "uid"),
    ),
    "idx_email_actions_status": (
        "email_actions",
        ("status", "updated_at", "action_id"),
    ),
    "idx_email_actions_classification_plan": (
        "email_actions",
        ("classification_id", "action_plan_id"),
    ),
    "idx_email_reply_dispatch_claims_status": (
        "email_reply_dispatch_claims",
        ("status", "updated_at", "action_identity"),
    ),
    "idx_email_unsubscribe_claims_status": (
        "email_unsubscribe_claims",
        ("status", "updated_at", "action_identity"),
    ),
    "idx_email_unsubscribe_receipts_classification_action": (
        "email_unsubscribe_receipts",
        ("classification_id", "action_identity"),
    ),
    "idx_email_training_observations_split": (
        "email_training_snapshot_observations",
        ("snapshot_id", "split", "category_key", "group_key"),
    ),
    "idx_email_training_observations_provider_truth": (
        "email_training_snapshot_observations",
        ("account_id", "stable_message_identity", "observed_at", "snapshot_id"),
    ),
    "idx_email_classifier_runtime_model_id": (
        "email_classifier_runtime_samples",
        ("model_id", "id"),
    ),
}
_REQUIRED_INDEX_KEY_PROPERTIES: Mapping[str, tuple[tuple[int, str], ...]] = {
    "idx_email_classifications_account_locator": (
        (0, "binary"), (0, "binary"), (0, "binary"),
        (0, "binary"), (1, "binary"), (0, "binary"),
    ),
}
_REQUIRED_PARTIAL_UNIQUE_INDEXES: Mapping[
    str,
    tuple[str, tuple[str, ...], str],
] = {
    "idx_email_category_folder_bindings_active_provider": (
        "email_category_folder_bindings",
        ("account_id", "provider_folder_id"),
        "where binding_status = 'active'",
    ),
}
_MODEL_CONTROL_IMMUTABLE_TRIGGER_TABLES = {
    f"trg_{table}_immutable_{operation}": table
    for table in (
        "email_model_promotion_configs",
        "email_model_mode_transitions",
        "email_category_description_revisions",
    )
    for operation in ("update", "delete", "insert")
}
_MODEL_CONTROL_IMMUTABLE_TRIGGER_SQL = {
    name: f"""create trigger {name} before {name.rsplit("_", 1)[1]} on {table}
        {f'when exists (select 1 from {table} where {_REQUIRED_PRIMARY_KEYS[table][0]}=new.{_REQUIRED_PRIMARY_KEYS[table][0]})' if name.endswith('_insert') else ''}
        begin select raise(abort, 'immutable model control history'); end"""
    for name, table in _MODEL_CONTROL_IMMUTABLE_TRIGGER_TABLES.items()
}
_REQUIRED_TRIGGER_SQL: Mapping[str, str] = {
    **_MODEL_CONTROL_IMMUTABLE_TRIGGER_SQL,
    **{
        f"trg_email_direct_retry_immutable_{event}": f"""
            create trigger trg_email_direct_retry_immutable_{event}
            before {event} on email_direct_retry_requests
            begin select raise(abort, 'email_direct_retry_request_immutable'); end
        """ for event in ("update", "delete")
    },
    "trg_email_direct_retry_immutable_insert": """
        create trigger trg_email_direct_retry_immutable_insert
        before insert on email_direct_retry_requests
        when exists (select 1 from email_direct_retry_requests
                     where action_id=new.action_id and attempt_number=new.attempt_number)
        begin select raise(abort, 'email_direct_retry_request_immutable'); end
    """,
    "trg_email_training_snapshots_immutable_update": """
        create trigger trg_email_training_snapshots_immutable_update
        before update on email_training_snapshots
        when not (
            old.frozen=0 and new.frozen=1
            and old.snapshot_id is new.snapshot_id
            and old.snapshot_version is new.snapshot_version
            and old.description_version is new.description_version
            and old.input_schema_version is new.input_schema_version
            and old.seed is new.seed
            and old.observed_at is new.observed_at
            and old.snapshot_digest is new.snapshot_digest
            and old.manifest_json is new.manifest_json
            and old.created_at is new.created_at
            and old.folder_label_watermark is new.folder_label_watermark
            and old.important_label_watermark is new.important_label_watermark
        )
        begin
            select raise(abort, 'email training snapshot is immutable');
        end
    """,
    "trg_email_training_snapshots_immutable_delete": """
        create trigger trg_email_training_snapshots_immutable_delete
        before delete on email_training_snapshots
        begin
            select raise(abort, 'email training snapshot is immutable');
        end
    """,
    "trg_email_training_observations_immutable_update": """
        create trigger trg_email_training_observations_immutable_update
        before update on email_training_snapshot_observations
        begin
            select raise(abort, 'email training snapshot observation is immutable');
        end
    """,
    "trg_email_training_observations_immutable_delete": """
        create trigger trg_email_training_observations_immutable_delete
        before delete on email_training_snapshot_observations
        begin
            select raise(abort, 'email training snapshot observation is immutable');
        end
    """,
    "trg_email_training_observations_require_unfrozen_snapshot": """
        create trigger trg_email_training_observations_require_unfrozen_snapshot
        before insert on email_training_snapshot_observations
        when not exists (
            select 1 from email_training_snapshots
            where snapshot_id=new.snapshot_id and frozen=0
        )
        begin
            select raise(abort, 'email training snapshot is frozen');
        end
    """,
    "trg_email_classification_status_insert": """
        create trigger trg_email_classification_status_insert
        before insert on email_classifications
        when new.status not in ('pending_feedback', 'processed')
        begin
            select raise(abort, 'invalid email classification status');
        end
    """,
    "trg_email_classification_status_update": """
        create trigger trg_email_classification_status_update
        before update of status on email_classifications
        when new.status not in ('pending_feedback', 'processed')
        begin
            select raise(abort, 'invalid email classification status');
        end
    """,
    "trg_email_classification_source_insert": """
        create trigger trg_email_classification_source_insert
        before insert on email_classifications
        when new.classification_source not in ('model', 'user', 'agent')
        begin
            select raise(abort, 'invalid email classification source');
        end
    """,
    "trg_email_classification_source_update": """
        create trigger trg_email_classification_source_update
        before update of classification_source on email_classifications
        when new.classification_source not in ('model', 'user', 'agent')
        begin
            select raise(abort, 'invalid email classification source');
        end
    """,
    "trg_email_training_inclusion_invalidate": """
        create trigger trg_email_training_inclusion_invalidate
        after update of confirmed_category, model_text, confirmed_at,
                        classification_source, status on email_classifications
        when old.included_in_model_id is not null and (
            old.confirmed_category is not new.confirmed_category or
            old.model_text is not new.model_text or
            old.confirmed_at is not new.confirmed_at or
            old.classification_source is not new.classification_source or
            old.status is not new.status
        )
        begin
            update email_classifications
            set included_in_model_id=null
            where id=new.id;
        end
    """,
    "trg_email_action_attempt_count_insert": """
        create trigger trg_email_action_attempt_count_insert
        after insert on email_actions
        when exists (
            select 1 from email_action_attempts where action_id=new.action_id
        ) and new.attempt_count != (
            select count(*) from email_action_attempts where action_id=new.action_id
        )
        begin
            select raise(abort, 'email_action_attempt_count_drift');
        end
    """,
    "trg_email_action_attempt_count_update": """
        create trigger trg_email_action_attempt_count_update
        after update on email_actions
        when new.attempt_count != (
            select count(*) from email_action_attempts where action_id=new.action_id
        )
        begin
            select raise(abort, 'email_action_attempt_count_drift');
        end
    """,
    "trg_email_direct_action_blocks_plan_switch": """
        create trigger trg_email_direct_action_blocks_plan_switch
        before update of status, current_action_plan_id on email_classifications
        when (
            old.status is not new.status
            or old.current_action_plan_id is not new.current_action_plan_id
        ) and exists (
            select 1 from email_actions
            where classification_id=old.id and status='processing'
        )
        begin
            select raise(abort, 'email_direct_action_in_flight');
        end
    """,
    "trg_email_direct_action_blocks_account_update": """
        create trigger trg_email_direct_action_blocks_account_update
        before update on email_accounts
        when exists (
            select 1 from email_actions
            where account_id=old.account_id and status='processing'
        )
        begin
            select raise(abort, 'email_direct_action_in_flight');
        end
    """,
    "trg_email_direct_action_blocks_account_delete": """
        create trigger trg_email_direct_action_blocks_account_delete
        before delete on email_accounts
        when exists (
            select 1 from email_actions
            where account_id=old.account_id and status='processing'
        )
        begin
            select raise(abort, 'email_direct_action_in_flight');
        end
    """,
    "trg_email_reply_dispatch_blocks_plan_switch": """
        create trigger trg_email_reply_dispatch_blocks_plan_switch
        before update of current_action_plan_id on email_classifications
        when old.current_action_plan_id is not new.current_action_plan_id
         and exists (
            select 1 from email_reply_dispatch_claims
            where classification_id=old.id and status='dispatching'
         )
        begin
            select raise(abort, 'email_reply_dispatch_in_flight');
        end
    """,
    "trg_email_reply_dispatch_blocks_account_update": """
        create trigger trg_email_reply_dispatch_blocks_account_update
        before update on email_accounts
        when exists (
            select 1 from email_reply_dispatch_claims
            where account_id=old.account_id and status='dispatching'
        )
        begin
            select raise(abort, 'email_reply_dispatch_in_flight');
        end
    """,
    "trg_email_reply_dispatch_blocks_account_delete": """
        create trigger trg_email_reply_dispatch_blocks_account_delete
        before delete on email_accounts
        when exists (
            select 1 from email_reply_dispatch_claims
            where account_id=old.account_id and status='dispatching'
        )
        begin
            select raise(abort, 'email_reply_dispatch_in_flight');
        end
    """,
    "trg_email_reply_dispatch_blocks_thread_update": """
        create trigger trg_email_reply_dispatch_blocks_thread_update
        before update of account_id, stable_message_identity, thread_identity
        on email_messages
        when (
            old.account_id is not new.account_id
            or old.stable_message_identity is not new.stable_message_identity
            or old.thread_identity is not new.thread_identity
        ) and exists (
            select 1 from email_reply_dispatch_claims
            where account_id=old.account_id
              and stable_message_identity=old.stable_message_identity
              and status='dispatching'
        )
        begin
            select raise(abort, 'email_reply_dispatch_in_flight');
        end
    """,
    "trg_email_reply_dispatch_blocks_message_delete": """
        create trigger trg_email_reply_dispatch_blocks_message_delete
        before delete on email_messages
        when exists (
            select 1 from email_reply_dispatch_claims
            where account_id=old.account_id
              and stable_message_identity=old.stable_message_identity
              and status='dispatching'
        )
        begin
            select raise(abort, 'email_reply_dispatch_in_flight');
        end
    """,
    "trg_email_unsubscribe_blocks_plan_switch": """
        create trigger trg_email_unsubscribe_blocks_plan_switch
        before update of status, current_action_plan_id on email_classifications
        when (
            old.status is not new.status
            or old.current_action_plan_id is not new.current_action_plan_id
        ) and exists (
            select 1 from email_unsubscribe_claims
            where classification_id=old.id and status='dispatching'
         )
        begin
            select raise(abort, 'email_unsubscribe_write_in_flight');
        end
    """,
    "trg_email_unsubscribe_blocks_account_update": """
        create trigger trg_email_unsubscribe_blocks_account_update
        before update on email_accounts
        when exists (
            select 1 from email_unsubscribe_claims
            where account_id=old.account_id and status='dispatching'
        )
        begin
            select raise(abort, 'email_unsubscribe_write_in_flight');
        end
    """,
    "trg_email_unsubscribe_blocks_account_delete": """
        create trigger trg_email_unsubscribe_blocks_account_delete
        before delete on email_accounts
        when exists (
            select 1 from email_unsubscribe_claims
            where account_id=old.account_id and status='dispatching'
        )
        begin
            select raise(abort, 'email_unsubscribe_write_in_flight');
        end
    """,
    "trg_email_unsubscribe_blocks_message_update": """
        create trigger trg_email_unsubscribe_blocks_message_update
        before update of account_id, stable_message_identity, thread_identity
        on email_messages
        when (
            old.account_id is not new.account_id
            or old.stable_message_identity is not new.stable_message_identity
            or old.thread_identity is not new.thread_identity
        ) and exists (
            select 1 from email_unsubscribe_claims
            where account_id=old.account_id
              and stable_message_identity=old.stable_message_identity
              and status='dispatching'
        )
        begin
            select raise(abort, 'email_unsubscribe_write_in_flight');
        end
    """,
    "trg_email_unsubscribe_blocks_message_delete": """
        create trigger trg_email_unsubscribe_blocks_message_delete
        before delete on email_messages
        when exists (
            select 1 from email_unsubscribe_claims
            where account_id=old.account_id
              and stable_message_identity=old.stable_message_identity
              and status='dispatching'
        )
        begin
            select raise(abort, 'email_unsubscribe_write_in_flight');
        end
    """,
}
_REQUIRED_TRIGGER_TABLES: Mapping[str, str] = {
    **_MODEL_CONTROL_IMMUTABLE_TRIGGER_TABLES,
    "trg_email_direct_retry_immutable_update": "email_direct_retry_requests",
    "trg_email_direct_retry_immutable_delete": "email_direct_retry_requests",
    "trg_email_direct_retry_immutable_insert": "email_direct_retry_requests",
    "trg_email_training_snapshots_immutable_update": "email_training_snapshots",
    "trg_email_training_snapshots_immutable_delete": "email_training_snapshots",
    "trg_email_training_observations_immutable_update": (
        "email_training_snapshot_observations"
    ),
    "trg_email_training_observations_immutable_delete": (
        "email_training_snapshot_observations"
    ),
    "trg_email_training_observations_require_unfrozen_snapshot": (
        "email_training_snapshot_observations"
    ),
    "trg_email_action_attempt_count_insert": "email_actions",
    "trg_email_action_attempt_count_update": "email_actions",
    "trg_email_direct_action_blocks_account_update": "email_accounts",
    "trg_email_direct_action_blocks_account_delete": "email_accounts",
    "trg_email_reply_dispatch_blocks_account_update": "email_accounts",
    "trg_email_reply_dispatch_blocks_account_delete": "email_accounts",
    "trg_email_reply_dispatch_blocks_thread_update": "email_messages",
    "trg_email_reply_dispatch_blocks_message_delete": "email_messages",
    "trg_email_unsubscribe_blocks_account_update": "email_accounts",
    "trg_email_unsubscribe_blocks_account_delete": "email_accounts",
    "trg_email_unsubscribe_blocks_message_update": "email_messages",
    "trg_email_unsubscribe_blocks_message_delete": "email_messages",
}

def _schema_sql_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character == "'":
            end = index + 1
            while end < len(value):
                if value[end] == "'":
                    if end + 1 < len(value) and value[end + 1] == "'":
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            else:
                raise EmailPersistenceCorruption("malformed email table SQL")
            tokens.append(value[index:end])
            index = end
            continue
        if character in {'"', "`"}:
            end = index + 1
            identifier: list[str] = []
            while end < len(value):
                if value[end] == character:
                    if end + 1 < len(value) and value[end + 1] == character:
                        identifier.append(character)
                        end += 2
                        continue
                    end += 1
                    break
                identifier.append(value[end])
                end += 1
            else:
                raise EmailPersistenceCorruption("malformed email table SQL")
            tokens.append("".join(identifier).casefold())
            index = end
            continue
        if character == "[":
            end = value.find("]", index + 1)
            if end < 0:
                raise EmailPersistenceCorruption("malformed email table SQL")
            tokens.append(value[index + 1 : end].casefold())
            index = end + 1
            continue
        if value[index : index + 2] == "--":
            end = value.find("\n", index + 2)
            index = len(value) if end < 0 else end + 1
            continue
        if value[index : index + 2] == "/*":
            end = value.find("*/", index + 2)
            if end < 0:
                raise EmailPersistenceCorruption("malformed email table SQL")
            index = end + 2
            continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(value) and (
                value[end].isalnum() or value[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(value[index:end].lower())
            index = end
            continue
        if character.isdigit():
            end = index + 1
            while end < len(value) and (value[end].isdigit() or value[end] == "."):
                end += 1
            tokens.append(value[index:end])
            index = end
            continue
        two_character_operator = value[index : index + 2]
        if two_character_operator in {"<=", ">=", "!=", "<>", "=="}:
            tokens.append(two_character_operator)
            index += 2
            continue
        if character in "(),.;:+-*/%<>=|&~!?":
            tokens.append(character)
            index += 1
            continue
        raise EmailPersistenceCorruption("malformed email table SQL")
    return tuple(tokens)

def _schema_metadata_enum(value: object, *, field: str) -> str:
    return _schema_metadata_text(value, field=field, kind="text").upper()

def _strip_wrapping_parentheses(tokens: tuple[str, ...]) -> tuple[str, ...]:
    while len(tokens) >= 2 and tokens[0] == "(" and tokens[-1] == ")":
        depth = 0
        wraps_expression = True
        for index, token in enumerate(tokens):
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
                if depth == 0 and index != len(tokens) - 1:
                    wraps_expression = False
                    break
            if depth < 0:
                raise EmailPersistenceCorruption("malformed email table SQL")
        if depth != 0:
            raise EmailPersistenceCorruption("malformed email table SQL")
        if not wraps_expression:
            break
        tokens = tokens[1:-1]
    return tokens

def _extract_schema_checks(value: str) -> frozenset[tuple[str, ...]]:
    tokens = _schema_sql_tokens(value)
    checks: set[tuple[str, ...]] = set()
    index = 0
    while index < len(tokens):
        if tokens[index] != "check":
            index += 1
            continue
        if index + 1 >= len(tokens) or tokens[index + 1] != "(":
            raise EmailPersistenceCorruption("malformed email table CHECK")
        depth = 1
        end = index + 2
        while end < len(tokens) and depth:
            if tokens[end] == "(":
                depth += 1
            elif tokens[end] == ")":
                depth -= 1
            end += 1
        if depth:
            raise EmailPersistenceCorruption("malformed email table CHECK")
        checks.add(_strip_wrapping_parentheses(tokens[index + 2 : end - 1]))
        index = end
    return frozenset(checks)

def _schema_column_declarations(value: str) -> Mapping[str, tuple[str, ...]]:
    tokens = _schema_sql_tokens(value)
    try:
        table_start = tokens.index("(")
    except ValueError as exc:
        raise EmailPersistenceCorruption("malformed email table declaration") from exc
    declarations: dict[str, tuple[str, ...]] = {}
    current: list[str] = []
    depth = 1
    for token in tokens[table_start + 1 :]:
        if token == "(":
            depth += 1
            current.append(token)
            continue
        if token == ")":
            depth -= 1
            if depth == 0:
                if current:
                    first = current[0]
                    if first not in {
                        "check",
                        "constraint",
                        "foreign",
                        "primary",
                        "unique",
                    }:
                        declarations[first] = tuple(current)
                break
            if depth < 0:
                raise EmailPersistenceCorruption("malformed email table declaration")
            current.append(token)
            continue
        if token == "," and depth == 1:
            if not current:
                raise EmailPersistenceCorruption("malformed email table declaration")
            first = current[0]
            if first not in {"check", "constraint", "foreign", "primary", "unique"}:
                declarations[first] = tuple(current)
            current = []
            continue
        current.append(token)
    else:
        raise EmailPersistenceCorruption("malformed email table declaration")
    return declarations

def _expected_column_declaration(
    *,
    table: str,
    column: str,
    contract: _ColumnContract,
) -> tuple[str, ...]:
    declared_type, not_null, default = contract
    tokens = [column, declared_type]
    if _REQUIRED_PRIMARY_KEYS[table] == (column,):
        tokens.extend(("primary", "key"))
        if (table, column) in _REQUIRED_AUTOINCREMENT_COLUMNS:
            tokens.append("autoincrement")
    if not_null:
        tokens.extend(("not", "null"))
    if (column,) in _REQUIRED_UNIQUE_KEYS.get(table, ()):
        tokens.append("unique")
    if default is not None:
        tokens.append("default")
        tokens.extend(_schema_sql_tokens(default))
    for check in _REQUIRED_TABLE_CHECKS.get(table, ()):
        check_tokens = _schema_sql_tokens(check)
        if column in check_tokens:
            tokens.extend(("check", "(", *check_tokens, ")"))
    return tuple(tokens)

def _normalize_column_default(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EmailPersistenceCorruption("malformed email column default")
    return " ".join(_schema_sql_tokens(value))


class HistoricalEmailStore(EmailStore):
    """Explicit schema-48/49/50 transition and source-declaration validator."""

    def _migrate_v48_to_v49(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Keep current plans only in the immutable ActionPlan relation."""
        self._validate_durable_state(db)
        self._validate_cached_action_plan_snapshots(db)
        db.execute("alter table email_classifications drop column action_plan_json")
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=49, applied_at=? "
                "where version=48", (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (49, ?)",
                (self._now(),),
            )

    def _migrate_v49_to_v50(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Record the owned cancellation relation created by the migration DDL."""
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=50, applied_at=? where version=49",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version,applied_at) values (50,?)",
                (self._now(),),
            )

    def _validate_cached_action_plan_snapshots(self, db: sqlite3.Connection) -> None:
        """Validate the <=48 duplicate exactly before removing it."""
        plan_rows = {
            row["action_plan_id"]: row
            for row in db.execute("select * from email_action_plans")
        }
        for row in db.execute(
            "select id, action_plan_json, current_action_plan_id from email_classifications"
        ):
            has_plan_snapshot = row["action_plan_json"] not in {"", "null"}
            if row["current_action_plan_id"] is None:
                if has_plan_snapshot:
                    raise EmailPersistenceCorruption(
                        f"classification {row['id']} has a snapshot without current ActionPlan"
                    )
                continue
            if not has_plan_snapshot:
                raise EmailPersistenceCorruption(
                    f"processed classification {row['id']} has inconsistent ActionPlan state"
                )
            plan_row = plan_rows[row["current_action_plan_id"]]
            current_plan = self._stored_action_plan(plan_row)
            raw_action_plan = _json_load(
                row["action_plan_json"],
                field="action_plan_json",
                expected_type=dict,
            )
            authorization_fields = {
                "authorization_snapshot_format",
                "action_authorizations",
            }
            present_authorization_fields = authorization_fields & set(raw_action_plan)
            if not present_authorization_fields:
                if (
                    plan_row["legacy_serialization_pre_v16"] != 1
                    or plan_row["authorization_snapshot_json"] is not None
                ):
                    raise EmailPersistenceCorruption(
                        f"current ActionPlan snapshot mismatch for classification "
                        f"{row['id']}"
                    )
                canonical_action_plan_json = current_plan.model_dump_json(
                    exclude=authorization_fields
                )
            elif present_authorization_fields == authorization_fields:
                canonical_action_plan_json = current_plan.model_dump_json()
            else:
                raise EmailPersistenceCorruption(
                    f"current ActionPlan snapshot has partial authorization fields "
                    f"for classification {row['id']}"
                )
            if row["action_plan_json"] != canonical_action_plan_json:
                raise EmailPersistenceCorruption(
                    f"current ActionPlan snapshot mismatch for classification {row['id']}"
                )

    @staticmethod
    def _create_durable_tables(db: sqlite3.Connection) -> None:
        statements = (
            """
            create table if not exists email_accounts (
                account_id text primary key,
                display_name text not null,
                email_address text not null,
                imap_host text not null,
                imap_port integer not null check(imap_port between 1 and 65535),
                imap_tls integer not null check(imap_tls in (0, 1)),
                imap_username text not null,
                imap_secret_reference text not null,
                imap_move_mode text not null default 'move'
                    check(imap_move_mode in ('move', 'copy_as_move')),
                smtp_host text not null,
                smtp_port integer not null check(smtp_port between 1 and 65535),
                smtp_tls integer not null check(smtp_tls in (0, 1)),
                smtp_username text not null,
                smtp_secret_reference text not null,
                enabled integer not null check(enabled in (0, 1)),
                scan_folders_json text not null check(json_valid(scan_folders_json)),
                scan_interval_seconds integer not null check(scan_interval_seconds > 0),
                created_at text not null,
                updated_at text not null,
                agent_lookback_days integer not null default 30
                    check(agent_lookback_days between 1 and 365),
                model_lookback_days integer not null default 365
                    check(model_lookback_days between 1 and 3650),
                scan_read_state text not null default 'unread'
                    check(scan_read_state in ('unread', 'all'))
            )
            """,
            """
            create table if not exists email_scan_cursors (
                account_id text not null,
                folder text not null,
                uidvalidity integer not null check(uidvalidity > 0),
                last_seen_uid integer not null check(last_seen_uid >= 0),
                last_success_at text not null default '',
                last_error text not null default '',
                primary key (account_id, folder)
            )
            """,
            """
            create table if not exists email_messages (
                id integer primary key autoincrement,
                account_id text not null,
                stable_message_identity text not null unique,
                folder text not null,
                uidvalidity integer not null check(uidvalidity > 0),
                uid integer not null check(uid > 0),
                rfc_message_id text not null,
                in_reply_to text not null default '',
                references_json text not null default '[]'
                    check(json_valid(references_json)),
                thread_identity text not null,
                sender text not null,
                recipients_json text not null check(json_valid(recipients_json)),
                subject text not null,
                normalized_text text not null,
                preview text not null,
                attachment_metadata_json text not null
                    check(json_valid(attachment_metadata_json)),
                received_at text not null,
                created_at text not null,
                updated_at text not null
            )
            """,
            """
            create table if not exists email_action_plans (
                action_plan_id text primary key,
                action_plan_version integer not null check(action_plan_version > 0),
                classification_id integer not null,
                account_id text not null,
                category text not null,
                classification_source text not null
                    check(classification_source in ('model', 'user', 'agent')),
                confidence real not null check(confidence >= 0.0 and confidence <= 1.0),
                model_id text not null,
                config_version text not null,
                actions_json text not null check(json_valid(actions_json)),
                action_parameters_json text not null
                    check(json_valid(action_parameters_json)),
                authorization_snapshot_json text
                    check(authorization_snapshot_json is null
                          or json_valid(authorization_snapshot_json)),
                legacy_serialization_pre_v16 integer not null default 0
                    check(legacy_serialization_pre_v16 in (0, 1)),
                created_at text not null,
                unique(classification_id, action_plan_version),
                foreign key(classification_id) references email_classifications(id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_actions (
                action_id text primary key,
                action_plan_id text not null,
                classification_id integer not null,
                account_id text not null,
                action_type text not null
                    check(action_type in (
                        'label', 'mark_read', 'archive', 'move', 'trash',
                        'flag_important'
                    )),
                parameters_json text not null check(json_valid(parameters_json)),
                config_version text not null,
                status text not null
                    check(status in (
                        'pending', 'processing', 'done', 'skipped', 'failed'
                    )),
                attempt_count integer not null default 0 check(attempt_count >= 0),
                started_at text not null default '',
                finished_at text not null default '',
                next_attempt_at text not null default '',
                provider_operation text not null default '',
                provider_target text not null default '',
                provider_result_id text not null default '',
                error text not null default '',
                created_at text not null,
                updated_at text not null,
                unique(action_plan_id, action_type),
                foreign key(action_plan_id) references email_action_plans(action_plan_id)
                    on delete restrict,
                foreign key(classification_id) references email_classifications(id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_action_attempts (
                id integer primary key autoincrement,
                action_id text not null,
                attempt_number integer not null check(attempt_number > 0),
                status text not null
                    check(status in ('done', 'skipped', 'failed')),
                provider_operation text not null,
                provider_target text not null,
                provider_result_id text not null,
                error text not null,
                started_at text not null,
                finished_at text not null,
                unique(action_id, attempt_number),
                foreign key(action_id) references email_actions(action_id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_feedback_requests (
                feedback_request_id text primary key
                    check(trim(feedback_request_id) != ''),
                classification_id integer not null,
                category text not null,
                expected_current_action_plan_id text
                    unique
                    check(
                        expected_current_action_plan_id is null
                        or trim(expected_current_action_plan_id) != ''
                    ),
                resulting_action_plan_id text not null unique
                    check(trim(resulting_action_plan_id) != ''),
                applied_at text not null check(trim(applied_at) != ''),
                check(
                    expected_current_action_plan_id is null
                    or expected_current_action_plan_id != resulting_action_plan_id
                ),
                foreign key(classification_id) references email_classifications(id)
                    on delete restrict,
                foreign key(expected_current_action_plan_id)
                    references email_action_plans(action_plan_id)
                    on delete restrict,
                foreign key(resulting_action_plan_id)
                    references email_action_plans(action_plan_id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_reply_receipts (
                action_identity text primary key
                    check(trim(action_identity) != ''),
                effect_digest text not null
                    check(trim(effect_digest) != ''),
                action_plan_id text not null,
                classification_id integer not null,
                account_id text not null,
                stable_message_identity text not null,
                outgoing_message_id text not null unique
                    check(trim(outgoing_message_id) != ''),
                provider_operation text not null
                    check(trim(provider_operation) != ''),
                provider_target text not null,
                provider_result_id text not null
                    check(trim(provider_result_id) != ''),
                provider_receipt_json text not null
                    check(json_valid(provider_receipt_json)),
                display_excerpt text not null,
                created_at text not null,
                foreign key(action_plan_id)
                    references email_action_plans(action_plan_id)
                    on delete restrict,
                foreign key(classification_id)
                    references email_classifications(id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_reply_dispatch_claims (
                action_identity text primary key
                    check(trim(action_identity) != ''),
                effect_digest text not null
                    check(trim(effect_digest) != ''),
                action_plan_id text not null,
                classification_id integer not null,
                account_id text not null,
                stable_message_identity text not null,
                outgoing_message_id text not null unique
                    check(trim(outgoing_message_id) != ''),
                owner_id text not null check(trim(owner_id) != ''),
                owner_generation integer not null check(owner_generation > 0),
                lease_token text not null check(trim(lease_token) != ''),
                sender text not null check(trim(sender) != ''),
                thread_identity text not null check(trim(thread_identity) != ''),
                account_updated_at text not null
                    check(trim(account_updated_at) != ''),
                account_snapshot_json text not null
                    check(json_valid(account_snapshot_json)),
                status text not null
                    check(status in (
                        'dispatching', 'retryable', 'uncertain', 'done'
                    )),
                claimed_at text not null check(trim(claimed_at) != ''),
                updated_at text not null check(trim(updated_at) != ''),
                foreign key(action_plan_id)
                    references email_action_plans(action_plan_id)
                    on delete restrict,
                foreign key(classification_id)
                    references email_classifications(id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_unsubscribe_claims (
                action_identity text primary key
                    check(trim(action_identity) != ''),
                effect_digest text not null check(trim(effect_digest) != ''),
                action_plan_id text not null,
                action_plan_version integer not null
                    check(action_plan_version > 0),
                classification_id integer not null,
                account_id text not null,
                stable_message_identity text not null,
                thread_identity text not null check(trim(thread_identity) != ''),
                entry_reference text not null check(trim(entry_reference) != ''),
                operations_json text not null check(json_valid(operations_json)),
                owner_id text not null check(trim(owner_id) != ''),
                owner_generation integer not null check(owner_generation > 0),
                lease_token text not null check(trim(lease_token) != ''),
                account_updated_at text not null
                    check(trim(account_updated_at) != ''),
                status text not null
                    check(status in (
                        'dispatching', 'awaiting_audit', 'uncertain', 'done'
                    )),
                phase text not null default 'prepared'
                    check(phase in (
                        'prepared', 'navigating', 'effect_uncertain', 'terminal'
                    )),
                audit_agent_run_id integer
                    check(audit_agent_run_id is null or audit_agent_run_id > 0),
                claimed_at text not null check(trim(claimed_at) != ''),
                updated_at text not null check(trim(updated_at) != ''),
                foreign key(action_plan_id)
                    references email_action_plans(action_plan_id)
                    on delete restrict,
                foreign key(classification_id)
                    references email_classifications(id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_unsubscribe_effects (
                action_identity text not null check(trim(action_identity) != ''),
                effect_digest text not null check(trim(effect_digest) != ''),
                previous_effect_digest text not null default ''
                    check(previous_effect_digest = '' or length(previous_effect_digest) = 64),
                operations_json text not null check(json_valid(operations_json)),
                audit_agent_run_id integer
                    check(audit_agent_run_id is null or audit_agent_run_id > 0),
                created_at text not null check(trim(created_at) != ''),
                primary key(action_identity, effect_digest),
                foreign key(action_identity)
                    references email_unsubscribe_claims(action_identity)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_unsubscribe_continuations (
                action_identity text primary key check(trim(action_identity) != ''),
                effect_digest text not null check(trim(effect_digest) != ''),
                observation_reference text not null
                    check(trim(observation_reference) != ''),
                controls_json text not null check(json_valid(controls_json)),
                created_at text not null check(trim(created_at) != ''),
                updated_at text not null check(trim(updated_at) != ''),
                foreign key(action_identity)
                    references email_unsubscribe_claims(action_identity)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_unsubscribe_steps (
                id integer primary key autoincrement,
                action_identity text not null check(trim(action_identity) != ''),
                effect_digest text not null check(trim(effect_digest) != ''),
                sequence integer not null check(sequence > 0),
                operation text not null check(trim(operation) != ''),
                state text not null check(trim(state) != ''),
                reference text not null check(trim(reference) != ''),
                created_at text not null check(trim(created_at) != ''),
                unique(action_identity, sequence),
                foreign key(action_identity)
                    references email_unsubscribe_claims(action_identity)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_unsubscribe_cancellations (
                action_identity text primary key check(trim(action_identity) != ''),
                decision_json text not null check(json_valid(decision_json))
            )
            """,
            DIRECT_RETRY_TABLE_SQL,
            """
            create table if not exists email_unsubscribe_receipts (
                action_identity text primary key
                    check(trim(action_identity) != ''),
                effect_digest text not null check(trim(effect_digest) != ''),
                action_plan_id text not null,
                action_plan_version integer not null
                    check(action_plan_version > 0),
                classification_id integer not null,
                account_id text not null,
                stable_message_identity text not null,
                thread_identity text not null check(trim(thread_identity) != ''),
                entry_reference text not null check(trim(entry_reference) != ''),
                outcome text not null check(outcome in (
                    'done', 'already_unsubscribed',
                    'skipped_no_reliable_entry', 'skipped_login_required',
                    'skipped_captcha', 'skipped_payment',
                    'failed_browser', 'failed_provider_auth'
                )),
                receipt_id text not null check(trim(receipt_id) != ''),
                evidence text not null check(trim(evidence) != ''),
                result_text text not null default ''
                    check(length(result_text) <= 16384),
                observation_digest text not null default ''
                    check(observation_digest = '' or length(observation_digest) = 64),
                started_at text not null default '',
                completed_at text not null default '',
                created_at text not null check(trim(created_at) != ''),
                foreign key(action_plan_id)
                    references email_action_plans(action_plan_id)
                    on delete restrict,
                foreign key(classification_id)
                    references email_classifications(id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_training_snapshots (
                snapshot_id text primary key check(trim(snapshot_id) != ''),
                snapshot_version text not null check(trim(snapshot_version) != ''),
                description_version text not null
                    check(trim(description_version) != ''),
                input_schema_version text not null
                    check(trim(input_schema_version) != ''),
                seed integer not null check(seed >= 0),
                observed_at text not null check(trim(observed_at) != ''),
                snapshot_digest text not null unique
                    check(length(snapshot_digest) = 64),
                manifest_json text not null check(json_valid(manifest_json)),
                frozen integer not null default 1 check(frozen in (0, 1)),
                created_at text not null check(trim(created_at) != ''),
                folder_label_watermark integer not null default 0
                    check(folder_label_watermark >= 0),
                important_label_watermark integer not null default 0
                    check(important_label_watermark >= 0)
            )
            """,
            """
            create table if not exists email_provider_observations (
                account_id text not null check(trim(account_id) != ''),
                stable_message_identity text not null
                    check(trim(stable_message_identity) != ''),
                state text not null
                    check(state in ('available','unavailable','excluded')),
                provider_folder_id text,
                provider_folder_name text,
                category_key text,
                important integer check(important is null or important in (0, 1)),
                important_signals_json text
                    check(important_signals_json is null or json_valid(important_signals_json)),
                observed_at text not null check(trim(observed_at) != ''),
                primary key(account_id, stable_message_identity)
            )
            """,
            """
            create table if not exists email_provider_folder_observation_generations (
                account_id text not null check(trim(account_id) != ''),
                provider_folder_id text not null
                    check(trim(provider_folder_id) != ''),
                observed_at text not null check(trim(observed_at) != ''),
                primary key(account_id, provider_folder_id)
            )
            """,
            """
            create table if not exists email_training_snapshot_observations (
                snapshot_id text not null check(trim(snapshot_id) != ''),
                account_id text not null check(trim(account_id) != ''),
                stable_message_identity text not null
                    check(trim(stable_message_identity) != ''),
                provider_folder_id text not null
                    check(trim(provider_folder_id) != ''),
                provider_folder_name text not null
                    check(trim(provider_folder_name) != ''),
                category_key text
                    check(category_key is null or trim(category_key) != ''),
                important integer not null check(important in (0, 1)),
                normalized_model_input text not null
                    check(trim(normalized_model_input) != ''),
                normalized_model_input_hash text not null
                    check(length(normalized_model_input_hash) = 64),
                input_schema_version text not null
                    check(trim(input_schema_version) != ''),
                provider_thread_id text,
                normalized_body_digest text not null
                    check(length(normalized_body_digest) = 64),
                sender_template_signature text
                    check(
                        sender_template_signature is null
                        or length(sender_template_signature) = 64
                    ),
                explicit_matter_group text,
                group_key text not null check(length(group_key) = 64),
                observed_at text not null check(trim(observed_at) != ''),
                source text not null check(source in (
                    'natural', 'targeted', 'agent_auto_label', 'user_feedback'
                )),
                split text not null
                    check(split in ('train', 'validation', 'test')),
                selected_for_training integer not null
                    check(selected_for_training in (0, 1)),
                ordered_record_digest text not null
                    check(length(ordered_record_digest) = 64),
                primary key(snapshot_id, account_id, stable_message_identity),
                foreign key(snapshot_id)
                    references email_training_snapshots(snapshot_id)
                    on delete restrict
            )
            """,
            """
            create table if not exists email_historical_classification_history (
                event_id text primary key check(trim(event_id) != ''),
                stable_message_identity text not null
                    check(trim(stable_message_identity) != ''),
                model_id text not null check(trim(model_id) != ''),
                predicted_category text
                    check(
                        predicted_category is null
                        or trim(predicted_category) != ''
                    ),
                threshold real
                    check(threshold is null or (threshold >= 0.0 and threshold <= 1.0)),
                probability real
                    check(
                        probability is null
                        or (probability >= 0.0 and probability <= 1.0)
                    ),
                important integer check(important is null or important in (0, 1)),
                action_outcome text not null check(trim(action_outcome) != ''),
                created_at text not null check(trim(created_at) != '')
            )
            """,
            """
            create table if not exists email_classifier_runtime_samples (
                id integer primary key autoincrement,
                model_id text not null check(trim(model_id) != ''),
                outcome text not null
                    check(outcome in ('success', 'rejected', 'failure')),
                fallback_code text not null default ''
                    check(length(fallback_code) <= 64),
                cache_hit integer not null check(cache_hit in (0, 1)),
                runtime_warm integer not null check(runtime_warm in (0, 1)),
                queue_ms real not null check(queue_ms >= 0),
                http_ms real not null check(http_ms >= 0),
                embedding_ms real not null check(embedding_ms >= 0),
                head_ms real not null check(head_ms >= 0),
                total_ms real not null check(total_ms >= 0),
                recorded_at text not null check(trim(recorded_at) != '')
            )
            """,
        )
        for statement in statements:
            db.execute(statement)
        for event in ("update", "delete", "insert"):
            name = f"trg_email_direct_retry_immutable_{event}"
            if db.execute("select 1 from sqlite_master where type='trigger' and name=?", (name,)).fetchone() is None:
                db.execute(_REQUIRED_TRIGGER_SQL[name])

    @staticmethod
    def _index_columns(db: sqlite3.Connection, index_name: str) -> tuple[str, ...]:
        return tuple(
            _schema_identifier(row["name"], field="pragma index_info column name")
            for row in db.execute(f"pragma index_info({json.dumps(index_name)})")
        )

    @classmethod
    def _validate_schema_shape(cls, db: sqlite3.Connection) -> None:
        try:
            if not task20_schema_is_current(db):
                raise EmailPersistenceCorruption("required Tasks20 declaration is missing or malformed")
            if not state_schema_is_current(db):
                raise EmailPersistenceCorruption("required State declaration is missing or malformed")
            if not provider_state_schema_is_current(db):
                raise EmailPersistenceCorruption("required provider-state declaration is missing or malformed")
            table_rows = {
                _schema_identifier(row["name"], field="sqlite_master table name"): row
                for row in db.execute(
                    "select name, sql from sqlite_master where type='table'"
                )
            }
            table_names = set(table_rows)
            # Historical maps remain the source contract. Current validation
            # retains the original nonaction coverage and validates actions
            # against the exact accepted finite manifest separately.
            retired_config_tables = {*CONFIG_FIELDS, *STATE_FIELDS, *TASK20_NEW_KINDS,
                                     "email_training_snapshot_observations",
                                     "email_classifier_runtime_samples",
                                     "email_historical_classification_history",
                                     "email_historical_operations",
                                     "email_classifications", "email_messages"}
            if not current_canonical_email_parent_is_current(db, metadata_owner=True):
                raise EmailPersistenceCorruption(
                    "required canonical Email declaration is missing or malformed"
                )
            required_tables = {
                table: columns
                for table, columns in _REQUIRED_TABLE_COLUMNS.items()
                if table not in EMAIL_ACTION_PROJECTIONS and table not in retired_config_tables
            }
            missing_tables = set(required_tables) - table_names
            if missing_tables:
                missing = ", ".join(sorted(missing_tables))
                raise EmailPersistenceCorruption(
                    f"missing required email table: {missing}"
                )
            without_rowid_tables = {
                _schema_identifier(row["name"], field="pragma table_list table name")
                for row in db.execute("pragma table_list")
                if row["wr"]
            }
            missing_without_rowid = (
                _REQUIRED_WITHOUT_ROWID_TABLES & required_tables.keys()
            ) - without_rowid_tables
            if missing_without_rowid:
                raise EmailPersistenceCorruption(
                    "required WITHOUT ROWID table contract is missing: "
                    + ", ".join(sorted(missing_without_rowid))
                )

            if not any(
                current_configs_schema_is_current(
                    db,
                    action_variant=variant,
                    include_followup_sync=True,
                    include_business_sync=True,
                    include_task_delivery=True,
                    include_errors=True, include_feedback=True,
                    include_business_actions=True,
                    include_email_history=True,
                    canonical_email=True,
                    inline_legacy_owner=True, physical_provenance=True, compact_payload=True, selection_references=True, result_references=True, execution_owner=True, sent_result_references=True, shared_lifecycle=True, specialized_claims=True, postfix_owner=True,
                    expected_action_objects=current_action_objects(variant),
                )
                for variant in REPLY_VARIANTS
            ):
                raise EmailPersistenceCorruption(
                    "required Email31/config schema is missing or malformed"
                )

            indexes_by_table: dict[str, dict[str, sqlite3.Row]] = {}
            table_order = [
                table
                for table in required_tables
                if table != "email_category_folder_bindings"
            ]
            for table in table_order:
                required_columns = required_tables[table]
                column_rows = list(
                    db.execute(f"pragma table_info({json.dumps(table)})")
                )
                column_names = {
                    _schema_identifier(
                        row["name"], field="pragma table_info column name"
                    )
                    for row in column_rows
                }
                missing_columns = required_columns - column_names
                if missing_columns:
                    missing = ", ".join(sorted(missing_columns))
                    raise EmailPersistenceCorruption(
                        f"{table} is missing required columns: {missing}"
                    )
                primary_key = tuple(
                    _schema_identifier(
                        row["name"], field="pragma table_info column name"
                    )
                    for row in sorted(column_rows, key=lambda row: row["pk"])
                    if row["pk"]
                )
                if primary_key != _REQUIRED_PRIMARY_KEYS[table]:
                    raise EmailPersistenceCorruption(
                        f"required primary key for {table} is missing or malformed"
                    )
                table_sql = table_rows[table]["sql"]
                if not isinstance(table_sql, str):
                    raise EmailPersistenceCorruption(
                        f"required declarations for {table} are missing or malformed"
                    )
                declarations = _schema_column_declarations(table_sql)
                columns_by_name = {
                    _schema_identifier(
                        row["name"], field="pragma table_info column name"
                    ): row
                    for row in column_rows
                }
                for column, expected in _REQUIRED_COLUMN_CONTRACTS[table].items():
                    column_row = columns_by_name[column]
                    declared_type = column_row["type"]
                    if not isinstance(declared_type, str):
                        raise EmailPersistenceCorruption(
                            f"{table} column {column} has malformed declaration"
                        )
                    actual = (
                        declared_type.strip().lower(),
                        bool(column_row["notnull"]),
                        _normalize_column_default(column_row["dflt_value"]),
                    )
                    if actual != expected:
                        raise EmailPersistenceCorruption(
                            f"{table} column {column} has malformed declaration"
                        )
                indexes = {
                    _schema_identifier(
                        row["name"], field="pragma index_list index name"
                    ): row
                    for row in db.execute(f"pragma index_list({json.dumps(table)})")
                }
                indexes_by_table[table] = indexes
                unique_keys = {
                    cls._index_columns(db, index_name)
                    for index_name, index_row in indexes.items()
                    if index_row["unique"] and not index_row["partial"]
                }
                for required_key in _REQUIRED_UNIQUE_KEYS.get(table, ()):
                    if required_key not in unique_keys:
                        raise EmailPersistenceCorruption(
                            f"required unique key for {table} is missing or malformed"
                        )

                foreign_keys = {
                    (
                        _schema_identifier(
                            row["from"],
                            field="pragma foreign_key_list source column",
                        ),
                        _schema_identifier(
                            row["table"],
                            field="pragma foreign_key_list target table",
                        ),
                        _schema_identifier(
                            row["to"],
                            field="pragma foreign_key_list target column",
                        ),
                        _schema_metadata_enum(
                            row["on_delete"],
                            field="pragma foreign_key_list on_delete",
                        ),
                    )
                    for row in db.execute(
                        f"pragma foreign_key_list({json.dumps(table)})"
                    )
                }
                for required_key in _REQUIRED_FOREIGN_KEYS.get(table, ()):
                    if required_key not in foreign_keys:
                        raise EmailPersistenceCorruption(
                            f"required foreign key for {table} is missing or malformed"
                        )

                required_checks = _REQUIRED_TABLE_CHECKS.get(table, ())
                if required_checks:
                    actual_checks = _extract_schema_checks(table_sql)
                    for required_check in required_checks:
                        expected_check = _strip_wrapping_parentheses(
                            _schema_sql_tokens(required_check)
                        )
                        if expected_check not in actual_checks:
                            raise EmailPersistenceCorruption(
                                f"required check for {table} is missing or malformed"
                            )

                # PRAGMA table_info omits semantic clauses such as COLLATE.
                # Compare the complete required declaration after the more
                # specific key and CHECK diagnostics above.
                for column, expected in _REQUIRED_COLUMN_CONTRACTS[table].items():
                    if declarations.get(column) != _expected_column_declaration(
                        table=table,
                        column=column,
                        contract=expected,
                    ):
                        raise EmailPersistenceCorruption(
                            f"{table} column {column} has unapproved declaration clauses"
                        )

            for index_name, (table, required_columns) in _REQUIRED_INDEXES.items():
                if table not in required_tables:
                    continue
                index_row = indexes_by_table[table].get(index_name)
                if (
                    index_row is None
                    or index_row["unique"]
                    or index_row["partial"]
                    or cls._index_columns(db, index_name) != required_columns
                ):
                    raise EmailPersistenceCorruption(
                        f"required index {index_name} is missing or malformed"
                    )
                key_properties = _REQUIRED_INDEX_KEY_PROPERTIES.get(index_name)
                if key_properties is not None:
                    actual_properties = tuple(
                        (row["desc"], str(row["coll"]).casefold())
                        for row in db.execute(f"pragma index_xinfo({index_name})")
                        if row["key"]
                    )
                    if actual_properties != key_properties:
                        raise EmailPersistenceCorruption(
                            f"required index {index_name} has malformed key order or collation"
                        )

            for index_name, (
                table,
                required_columns,
                required_predicate,
            ) in _REQUIRED_PARTIAL_UNIQUE_INDEXES.items():
                if table not in required_tables:
                    continue
                index_row = indexes_by_table[table].get(index_name)
                sql_row = db.execute(
                    "select sql from sqlite_master where type='index' and name=?",
                    (index_name,),
                ).fetchone()
                if (
                    index_row is None
                    or not index_row["unique"]
                    or not index_row["partial"]
                    or cls._index_columns(db, index_name) != required_columns
                    or sql_row is None
                    or not isinstance(sql_row["sql"], str)
                    or "".join(_schema_sql_tokens(required_predicate))
                    not in "".join(_schema_sql_tokens(sql_row["sql"]))
                ):
                    raise EmailPersistenceCorruption(
                        f"required partial unique index {index_name} "
                        "is missing or malformed"
                    )

            trigger_rows = {
                _schema_identifier(row["name"], field="sqlite_master trigger name"): row
                for row in db.execute(
                    "select name, tbl_name, sql from sqlite_master where type='trigger'"
                )
            }
            translated_triggers = finite_target_external_sql()
            required_triggers = {
                name: sql
                for name, sql in _REQUIRED_TRIGGER_SQL.items()
                if _REQUIRED_TRIGGER_TABLES.get(name, "email_classifications")
                in required_tables
            }
            required_triggers.update(
                (name, sql) for name, sql in translated_triggers.items()
                if _REQUIRED_TRIGGER_TABLES.get(name, "email_classifications") not in retired_config_tables
            )
            for trigger_name, expected_sql in required_triggers.items():
                trigger = trigger_rows.get(trigger_name)
                if (
                    trigger is None
                    or _schema_identifier(
                        trigger["tbl_name"],
                        field="sqlite_master trigger table name",
                    )
                    != _REQUIRED_TRIGGER_TABLES.get(
                        trigger_name,
                        "email_classifications",
                    )
                    or not isinstance(trigger["sql"], str)
                    or _schema_sql_tokens(trigger["sql"])
                    != _schema_sql_tokens(expected_sql)
                ):
                    raise EmailPersistenceCorruption(
                        f"required trigger {trigger_name} is missing or malformed"
                    )
        except (sqlite3.ProgrammingError, sqlite3.NotSupportedError):
            raise
        except sqlite3.DatabaseError as exc:
            raise EmailPersistenceCorruption(
                "unable to inspect required email schema"
            ) from exc
