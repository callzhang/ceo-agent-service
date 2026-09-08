"""Account-aware persistence for email classification and planned actions.

This module owns durable email business state only. It never connects to a
provider and never creates Agent, Audit, reply-task, or run records.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.parser import Parser
from email.utils import getaddresses
from hashlib import sha256
import json
import math
import re
import sqlite3
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote, unquote_plus, urlsplit

from app.email_classifier_contracts import (
    ACTION_DEPENDENCY_PARAMETER,
    DIRECT_ACTIONS,
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailCategoryKey,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    build_email_action_plan,
    build_user_confirmation_authorizations,
    build_versioned_email_action_plan,
    effective_direct_action_dependencies,
    rehydrate_legacy_email_action_plan_json,
    rehydrate_legacy_email_category_key,
    validate_email_category_key,
)
from app.email_category_config import (
    ACTIVE_BINDING_INDEX_SQL,
    CATEGORY_CONFIG_CHECKS,
    CATEGORY_CONFIG_COLUMN_CONTRACTS,
    CATEGORY_CONFIG_TABLE_SQL,
    DESCRIPTION_VERSION,
    FOLDER_BINDING_CHECKS,
    FOLDER_BINDING_COLUMN_CONTRACTS,
    FOLDER_BINDING_TABLE_SQL,
    INITIAL_CATEGORY_CONFIGS,
    SEEDED_CONFIG_VERSION,
    SEEDED_THRESHOLD,
    STRUCTURED_CATEGORY_CONFIG_COLUMNS,
    CategoryConfigDataError,
    VerifiedEmailFolderBinding,
    category_config_row,
    legacy_config_row,
    validate_category_descriptions,
)
from app.email_provider_folders import FolderRole
from app.leak_check import assert_no_credentials, is_sensitive_url_component_name


EMAIL_SCHEMA_VERSION = 32
MAX_CLASSIFIER_RUNTIME_SAMPLES = 2048
HISTORICAL_DEFER_RETRY_SECONDS = 60
DIRECT_ACTION_MAX_ATTEMPTS = 3
# Cross-restart bound for one accepted unsubscribe effect lineage.  This is a
# durable data limit, independent of any Agent process turn budget.
MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS = 32
DIRECT_ACTION_RETRY_BASE_SECONDS = 2
_CLASSIFICATION_STATUSES = frozenset(
    status.value for status in EmailClassificationStatus
)
_CLASSIFICATION_SOURCES = frozenset({"model", "user", "agent"})
_CURRENT_ACTION_STATUSES = frozenset({"pending", "processing", "done", "failed"})
_TERMINAL_ATTEMPT_STATUSES = frozenset({"done", "failed"})
_DIRECT_ACTION_VALUES = frozenset(action.value for action in DIRECT_ACTIONS)
_EMAIL_REPLY_CLAIM_STATUSES = frozenset(
    {"dispatching", "retryable", "uncertain", "done"}
)
_EMAIL_UNSUBSCRIBE_CLAIM_STATUSES = frozenset(
    {"dispatching", "awaiting_audit", "uncertain", "done"}
)
_EMAIL_UNSUBSCRIBE_OUTCOMES = frozenset(
    {
        "done",
        "already_unsubscribed",
        "skipped_no_reliable_entry",
        "skipped_login_required",
        "skipped_captcha",
        "skipped_payment",
        "failed_browser",
        "failed_provider_auth",
    }
)
_OPAQUE_PROVIDER_ID = re.compile(r"[A-Za-z0-9._:@<>\[\]{}/+=,!#$%&'*?^-]+")
_UNSUBSCRIBE_OPAQUE_ID = re.compile(r"[A-Za-z0-9:_-]+")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_RUNTIME_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_MAX_PROVIDER_IDENTIFIER_BYTES = 256
_MAX_UNSUBSCRIBE_RESULT_TEXT_BYTES = 16 * 1024
_AUDITED_UNSUBSCRIBE_LINEAGE_SQL = """
    select effects.action_identity as effect_action_identity,
           effects.effect_digest as effect_digest,
           effects.audit_agent_run_id,
           audit_runs.id as audit_run_id,
           audit_runs.reply_task_id,
           audit_runs.execution_generation as audit_execution_generation,
           audit_runs.role as audit_role,
           tasks.id as task_id,
           tasks.channel as task_channel,
           tasks.conversation_id as task_conversation_id,
           tasks.trigger_message_id,
           tasks.trigger_message_json,
           tasks.execution_generation as task_execution_generation,
           tasks.status as task_status
    from email_unsubscribe_effects as effects
    join agent_runs as audit_runs
      on audit_runs.id=effects.audit_agent_run_id
    join reply_tasks as tasks
      on tasks.id=audit_runs.reply_task_id
    where effects.action_identity=? and effects.effect_digest=?
"""
_DIRECT_ACTION_PRIORITY = {
    action.value: priority for priority, action in enumerate(DIRECT_ACTIONS)
}
_UNREDACTED_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_UNREDACTED_SECRET_TOKEN = re.compile(
    r"\b(?:sub|ch|pi|sk|tok|token|sess|session|order|invoice|qrp)"
    r"[_.-]?[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]{6,})*\b",
    flags=re.IGNORECASE,
)
_ColumnContract = tuple[str, bool, str | None]
_REQUIRED_COLUMN_CONTRACTS: Mapping[str, Mapping[str, _ColumnContract]] = {
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
        "action_plan_json": ("text", True, "'null'"),
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
        "network_policy_reference": ("text", True, None),
        "network_policy_origins_json": ("text", True, None),
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
    "email_provider_observations": {
        "account_id": ("text", True, None),
        "stable_message_identity": ("text", True, None),
        "state": ("text", True, None),
        "provider_folder_id": ("text", False, None),
        "provider_folder_name": ("text", False, None),
        "category_key": ("text", False, None),
        "important": ("integer", False, None),
        "observed_at": ("text", True, None),
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
        "smtp_port between 1 and 65535",
        "smtp_tls in (0, 1)",
        "enabled in (0, 1)",
        "json_valid(scan_folders_json)",
        "scan_interval_seconds > 0",
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
        "status in ('pending', 'processing', 'done', 'failed')",
        "attempt_count >= 0",
    ),
    "email_action_attempts": (
        "attempt_number > 0",
        "status in ('done', 'failed')",
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
        "trim(network_policy_reference) != ''",
        "json_valid(network_policy_origins_json)",
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
    "email_provider_observations": (
        "trim(account_id) != ''",
        "trim(stable_message_identity) != ''",
        "state in ('available','unavailable','excluded')",
        "important is null or important in (0, 1)",
        "trim(observed_at) != ''",
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
        "source in ('natural', 'targeted')",
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
    "email_training_snapshots": ("snapshot_id",),
    "email_provider_observations": ("account_id", "stable_message_identity"),
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
    "idx_email_provider_observations_lookup": (
        "email_provider_observations",
        ("account_id", "stable_message_identity", "observed_at"),
    ),
    "idx_email_classifier_runtime_model_id": (
        "email_classifier_runtime_samples",
        ("model_id", "id"),
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
_REQUIRED_TRIGGER_SQL: Mapping[str, str] = {
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


class EmailClassificationConflict(RuntimeError):
    """The classification was already resolved by another confirmation."""


@dataclass(frozen=True)
class EmailFeedbackApplication:
    """Durable result of one explicit human-feedback intent."""

    feedback_request_id: str
    expected_current_action_plan_id: str | None
    resulting_action_plan_id: str
    confirmed: dict[str, Any]
    applied: bool

    @property
    def replayed(self) -> bool:
        return not self.applied


class EmailTrainingInclusionConflict(RuntimeError):
    """Authoritative training samples cannot be marked as one atomic batch."""


class EmailTrainingConsistencyError(RuntimeError):
    """Registry manifests could not be proven restored after a DB failure."""


class EmailTrainingSnapshotConflict(RuntimeError):
    """A frozen snapshot identity is already bound to different content."""


class EmailClassificationIdentityCollision(RuntimeError):
    """A classification ID is already bound to another stable identity."""


class EmailCursorConflict(RuntimeError):
    """The persisted scan cursor no longer matches the scanner's observation."""


class EmailActionPlanConflict(RuntimeError):
    """An immutable ActionPlan ID or version conflicts with stored history."""


class EmailActionAttemptConflict(RuntimeError):
    """A direct-action attempt conflicts with append-only history."""


class EmailReplyReceiptConflict(RuntimeError):
    """A stable automatic-reply identity conflicts with durable provider evidence."""


class EmailReplyDispatchConflict(RuntimeError):
    """An automatic reply dispatch claim conflicts with authorization history."""


class EmailUnsubscribeReceiptConflict(RuntimeError):
    """An unsubscribe action is bound to different durable terminal evidence."""


class EmailUnsubscribeClaimConflict(RuntimeError):
    """An unsubscribe browser-write claim conflicts with authorization history."""


@dataclass(frozen=True)
class LegacyEmailUnsubscribeTaskAttempt:
    """Immutable queue attempt observed by the startup legacy inventory."""

    task_id: int
    execution_generation: str
    status: str


@dataclass(frozen=True)
class StoredEmailLocator:
    """Provider coordinates plus the durable message identity used for receipts."""

    account_id: str
    folder: str
    uidvalidity: int
    uid: int
    rfc_message_id: str | None
    thread_id: str | None
    stable_message_identity: str


@dataclass(frozen=True)
class StoredEmailAction:
    """One claimed deterministic action built from immutable persisted state."""

    action_id: str
    action_plan_id: str
    classification_id: int
    account_id: str
    action_type: EmailAction
    parameters: Mapping[str, object]
    config_version: str
    locator: StoredEmailLocator
    attempt_number: int
    claim_started_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameters",
            _freeze_action_parameters(self.parameters),
        )


class EmailAccountConflict(RuntimeError):
    """An account ID or unshared email address conflicts with stored config."""

    def __init__(self, code: str):
        super().__init__("email account configuration conflicts with stored state")
        self.code = code


class EmailFolderBindingConflict(RuntimeError):
    """A category or active provider folder conflicts with durable configuration."""


def _validate_verified_folder_binding_role(
    category_key: str,
    binding: VerifiedEmailFolderBinding,
) -> None:
    if binding.binding_status != "active":
        return
    if category_key == "junk":
        if binding.provider_folder_role is not FolderRole.TRASH:
            raise ValueError("junk accepts only a verified system Trash binding")
        return
    if binding.provider_folder_role not in {FolderRole.UNBOUND, FolderRole.CATEGORY}:
        raise ValueError("business category requires an ordinary provider folder")


@dataclass(frozen=True)
class EmailCategoryEnablementState:
    """One category state changed by an account mutation."""

    category_key: str
    enabled: bool
    updated_at: str
    mutation_updated_at: str


@dataclass(frozen=True)
class EmailCategoryEnablementSnapshot:
    """Optimistic restore token for one exact account mutation."""

    states: tuple[EmailCategoryEnablementState, ...]


class EmailPersistenceCorruption(RuntimeError):
    """Durable email state violates its JSON or enum contract."""


def _validate_model_text(model_text: str) -> None:
    lowered = model_text.lower()
    if (
        _UNREDACTED_EMAIL.search(model_text)
        or _UNREDACTED_SECRET_TOKEN.search(model_text)
        or "http://" in lowered
        or "https://" in lowered
    ):
        raise ValueError("model_text must be redacted")


def _validated_agent_result_json(
    classification: EmailClassification, agent_result: object | None
) -> str:
    if classification.classification_source != "agent":
        if agent_result is not None:
            raise ValueError("non-Agent classification cannot carry an Agent result")
        return "null"
    if agent_result is None:
        raise ValueError("Agent classification requires its exact typed result")
    from app.email_classifier_agent import DurableAgentClassificationResult

    result = (
        agent_result
        if isinstance(agent_result, DurableAgentClassificationResult)
        else DurableAgentClassificationResult.model_validate(agent_result)
    )
    expected_status = (
        EmailClassificationStatus.PROCESSED
        if result.certainty == "certain"
        else EmailClassificationStatus.PENDING_FEEDBACK
    )
    if classification.status is not expected_status:
        raise ValueError("Agent certainty and canonical status diverge")
    if classification.category != result.category:
        raise ValueError("Agent and canonical categories diverge")
    if classification.confidence != result.confidence:
        raise ValueError("Agent and canonical confidence diverge")
    return result.model_dump_json()


def _normalized_message_id(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError("message ID must be text")
    locator = EmailProviderLocator.model_validate(
        {
            "account_id": "message-id-normalizer",
            "folder": "message-id-normalizer",
            "uidvalidity": 1,
            "uid": 1,
            "rfc_message_id": value,
        }
    )
    return locator.rfc_message_id or ""


def _normalized_message_ids(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str | bytes | bytearray):
        raise ValueError("references must be a sequence of message IDs")
    normalized = tuple(
        message_id for value in values if (message_id := _normalized_message_id(value))
    )
    return tuple(dict.fromkeys(normalized))


def _json_dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _historical_candidate_projection(
    message: Mapping[str, object],
    *,
    stable_message_identity: str,
    account_id: str,
    folder: str,
) -> dict[str, object]:
    """Keep only provider coordinates needed for an exact later reread."""

    if str(message.get("accountId") or "") != account_id:
        raise ValueError("historical candidate account does not match queue")
    if str(message.get("folder") or "") != folder:
        raise ValueError("historical candidate folder does not match queue")
    message_identity = str(message.get("stableMessageIdentity") or "")
    if message_identity and message_identity != stable_message_identity:
        raise ValueError("historical candidate identity does not match queue")
    projection: dict[str, object] = {
        "accountId": account_id,
        "folder": folder,
        "uidValidity": int(message.get("uidValidity") or 0),
        "uid": int(message.get("uid") or 0),
        "messageId": str(message.get("messageId") or "") or None,
        "threadId": str(message.get("threadId") or "") or None,
        "stableMessageIdentity": stable_message_identity,
    }
    EmailProviderLocator.model_validate(
        {
            "account_id": projection["accountId"],
            "folder": projection["folder"],
            "uidvalidity": projection["uidValidity"],
            "uid": projection["uid"],
            "rfc_message_id": projection["messageId"],
            "thread_id": projection["threadId"],
        }
    )
    return projection


def _legacy_unsubscribe_private_values(url: object) -> tuple[str, ...]:
    """Return exact legacy URL/token values that must not survive migration."""

    candidate = str(url)
    values = {candidate, unquote(candidate)}
    parsed = urlsplit(candidate)
    encoded_components = parsed.query.split("&")
    if parsed.fragment:
        encoded_components.extend(parsed.fragment.split("&"))
    for component in encoded_components:
        name, separator, private_value = component.partition("=")
        if not separator or not is_sensitive_url_component_name(name):
            continue
        for decoded in (private_value, unquote_plus(private_value)):
            if decoded:
                values.add(decoded)
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _redact_legacy_unsubscribe_json(
    value: object,
    replacements: Sequence[tuple[str, str]],
) -> object:
    """Redact legacy private values in decoded JSON, including nested fields."""

    if isinstance(value, str):
        for private_value, reference in replacements:
            value = value.replace(private_value, reference)
        return value
    if isinstance(value, list):
        return [_redact_legacy_unsubscribe_json(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_legacy_unsubscribe_json(item, replacements)
            for key, item in value.items()
        }
    return value


def _freeze_action_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_action_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_action_value(item) for item in value)
    return value


def _freeze_action_parameters(
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    return MappingProxyType(
        {str(key): _freeze_action_value(value) for key, value in parameters.items()}
    )


def _required_utc_timestamp(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _next_attempt_at(
    finished_at: str,
    *,
    attempt_number: int,
    retryable: bool,
) -> str:
    if not retryable or attempt_number >= DIRECT_ACTION_MAX_ATTEMPTS:
        return ""
    parsed = datetime.fromisoformat(finished_at)
    delay = DIRECT_ACTION_RETRY_BASE_SECONDS * (2 ** (attempt_number - 1))
    return (parsed + timedelta(seconds=delay)).isoformat(timespec="seconds")


def _retry_is_due(next_attempt_at: object, claimed_at: str) -> bool:
    if not next_attempt_at:
        return False
    try:
        return (
            _required_utc_timestamp(str(next_attempt_at), field="next_attempt_at")
            <= claimed_at
        )
    except ValueError as exc:
        raise EmailPersistenceCorruption(
            "invalid direct action retry timestamp"
        ) from exc


def _json_load(raw: str, *, field: str, expected_type: type[Any]) -> Any:
    try:
        value = json.loads(raw)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise EmailPersistenceCorruption(f"invalid {field} JSON") from exc
    if not isinstance(value, expected_type):
        raise EmailPersistenceCorruption(
            f"{field} must contain a JSON {expected_type.__name__}"
        )
    return value


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


def _schema_metadata_text(value: object, *, field: str, kind: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise EmailPersistenceCorruption(f"invalid schema {kind} metadata: {field}")
    return value


def _schema_identifier(value: object, *, field: str) -> str:
    return _schema_metadata_text(value, field=field, kind="identifier").casefold()


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


def _require_positive_int(value: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")


def _validate_feedback_request_id(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("feedback_request_id must be a non-empty stable identifier")
    if len(value) > 200:
        raise ValueError("feedback_request_id must be at most 200 characters")
    return value


def _validate_expected_action_plan_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            "expected_current_action_plan_id must be null or a non-empty identifier"
        )
    return value


def _validate_opaque_provider_identifier(
    value: str,
    *,
    field: str,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    if not value:
        if allow_empty:
            return value
        raise ValueError(f"{field} must be non-empty")
    if (
        value != value.strip()
        or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
        or _OPAQUE_PROVIDER_ID.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a bounded opaque identifier")
    return value


def _validate_provider_location(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
        or any(ord(character) < 32 or ord(character) > 126 for character in value)
    ):
        raise ValueError(f"{field} must be a bounded single-line provider location")
    return value


def _validate_unsubscribe_opaque(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
        or _UNSUBSCRIBE_OPAQUE_ID.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a bounded opaque unsubscribe reference")
    try:
        assert_no_credentials(value)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a bounded opaque unsubscribe reference"
        ) from exc
    return value


def is_valid_unsubscribe_opaque_reference(value: object) -> bool:
    """Return whether a durable unsubscribe reference is bounded and opaque."""

    try:
        _validate_unsubscribe_opaque(value, field="unsubscribe_reference")
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _validate_durable_unsubscribe_result_text(
    result_text: object,
    observation_digest: object,
    result_text_truncated: object,
    result_text_digest: object,
) -> None:
    """Require the stored display text to already be canonical and redacted."""

    if (
        not isinstance(result_text, str)
        or not isinstance(observation_digest, str)
        or not isinstance(result_text_digest, str)
        or not isinstance(result_text_truncated, int)
        or result_text_truncated not in {0, 1}
    ):
        raise EmailPersistenceCorruption(
            "unsubscribe receipt result integrity metadata is invalid"
        )
    encoded_length = len(result_text.encode("utf-8"))
    if encoded_length > _MAX_UNSUBSCRIBE_RESULT_TEXT_BYTES:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt result text exceeds its durable bound"
        )
    from app.email_unsubscribe import normalize_unsubscribe_result_text

    canonical_text, bounded_digest = normalize_unsubscribe_result_text(result_text)
    if canonical_text != result_text:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt result text is not canonical and redacted"
        )
    if not result_text:
        if observation_digest or result_text_digest or result_text_truncated:
            raise EmailPersistenceCorruption(
                "unsubscribe receipt empty result has non-empty integrity metadata"
            )
        return
    if (
        _SHA256_HEX.fullmatch(result_text_digest) is None
        or result_text_digest != bounded_digest
    ):
        raise EmailPersistenceCorruption(
            "unsubscribe receipt bounded result digest does not match"
        )
    if _SHA256_HEX.fullmatch(observation_digest) is None:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt observation digest is invalid"
        )
    if not result_text_truncated and observation_digest != result_text_digest:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt untruncated observation digest does not match"
        )
    if result_text_truncated and observation_digest == result_text_digest:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt truncated observation digest is not distinct"
        )


def _audited_unsubscribe_run_chain(
    db: sqlite3.Connection,
    *,
    task_id: int,
    execution_generation: str,
    final_audit_run_id: int,
) -> tuple[list[int], list[int]] | None:
    rows = db.execute(
        """
        select id, role, proposal_revision, turn_attempt,
               parent_agent_run_id, operation_id
        from agent_runs
        where reply_task_id=? and execution_generation=?
        """,
        (task_id, execution_generation),
    ).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    current = by_id.get(final_audit_run_id)
    if current is None or current["role"] != "audit":
        return None
    chain: list[sqlite3.Row] = []
    seen: set[int] = set()
    while current is not None:
        run_id = int(current["id"])
        if run_id in seen:
            return None
        seen.add(run_id)
        chain.append(current)
        role = str(current["role"])
        revision = int(current["proposal_revision"])
        parent_id = current["parent_agent_run_id"]
        operation_id = str(current["operation_id"])
        if role == "audit":
            if not operation_id or not isinstance(parent_id, int):
                return None
            parent = by_id.get(parent_id)
            if (
                parent is None
                or parent["role"] != "consumer"
                or int(parent["proposal_revision"]) != revision
            ):
                return None
            current = parent
            continue
        if role != "consumer" or operation_id:
            return None
        if revision == 0:
            if parent_id is not None:
                return None
            current = None
            continue
        if not isinstance(parent_id, int):
            return None
        parent = by_id.get(parent_id)
        if (
            parent is None
            or parent["role"] != "audit"
            or int(parent["proposal_revision"]) != revision - 1
        ):
            return None
        current = parent

    chain.reverse()
    return (
        [int(row["id"]) for row in chain if row["role"] == "consumer"],
        [int(row["id"]) for row in chain if row["role"] == "audit"],
    )


def _audited_unsubscribe_lineage(
    db: sqlite3.Connection,
    *,
    receipt: sqlite3.Row,
    classification: sqlite3.Row,
) -> dict[str, object] | None:
    lineage = db.execute(
        _AUDITED_UNSUBSCRIBE_LINEAGE_SQL,
        (receipt["action_identity"], receipt["effect_digest"]),
    ).fetchone()
    if lineage is None or lineage["audit_agent_run_id"] is None:
        return None
    current_plan = db.execute(
        """
        select action_plan_id, action_plan_version, classification_id,
               account_id, actions_json
        from email_action_plans
        where action_plan_id=?
        """,
        (classification["current_action_plan_id"],),
    ).fetchone()
    if current_plan is None:
        return None
    try:
        payload = json.loads(lineage["trigger_message_json"])
        plan_actions = _json_load(
            current_plan["actions_json"],
            field="actions_json",
            expected_type=list,
        )
        from app.email_task_adapter import email_conversation_id

        expected_conversation_id = email_conversation_id(
            str(receipt["account_id"]),
            str(receipt["thread_identity"]),
        )
        expected_action_identity = email_action_identity(
            account_id=str(receipt["account_id"]),
            stable_message_identity=str(receipt["stable_message_identity"]),
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(receipt["action_plan_version"]),
        )
    except (TypeError, ValueError, RecursionError, KeyError):
        return None
    if not isinstance(payload, dict):
        return None
    expected_payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": EmailAction.UNSUBSCRIBE.value,
        "action_identity": receipt["action_identity"],
        "action_plan_id": receipt["action_plan_id"],
        "action_plan_version": receipt["action_plan_version"],
        "classification_id": receipt["classification_id"],
        "account_id": receipt["account_id"],
        "stable_message_identity": receipt["stable_message_identity"],
        "thread_identity": receipt["thread_identity"],
    }
    if any(payload.get(key) != value for key, value in expected_payload.items()):
        return None
    integer_payload_fields = ("action_plan_version", "classification_id")
    if any(
        not isinstance(payload.get(field), int) or isinstance(payload.get(field), bool)
        for field in integer_payload_fields
    ):
        return None
    task_id = int(lineage["task_id"])
    audit_run_id = int(lineage["audit_agent_run_id"])
    task_generation = str(lineage["task_execution_generation"])
    if (
        receipt["action_identity"] != expected_action_identity
        or lineage["effect_action_identity"] != receipt["action_identity"]
        or lineage["effect_digest"] != receipt["effect_digest"]
        or lineage["audit_run_id"] != audit_run_id
        or lineage["audit_role"] != "audit"
        or lineage["reply_task_id"] != task_id
        or lineage["audit_execution_generation"] != task_generation
        or not task_generation
        or lineage["task_channel"] != "email"
        or lineage["task_conversation_id"] != expected_conversation_id
        or lineage["trigger_message_id"] != receipt["action_identity"]
        or classification["id"] != receipt["classification_id"]
        or classification["account_id"] != receipt["account_id"]
        or classification["stable_message_identity"]
        != receipt["stable_message_identity"]
        or classification["message_thread_identity"] != receipt["thread_identity"]
        or classification["current_action_plan_id"] != receipt["action_plan_id"]
        or current_plan["action_plan_id"] != receipt["action_plan_id"]
        or current_plan["action_plan_version"] != receipt["action_plan_version"]
        or current_plan["classification_id"] != receipt["classification_id"]
        or current_plan["account_id"] != receipt["account_id"]
        or EmailAction.UNSUBSCRIBE.value not in plan_actions
    ):
        return None
    run_chain = _audited_unsubscribe_run_chain(
        db,
        task_id=task_id,
        execution_generation=task_generation,
        final_audit_run_id=audit_run_id,
    )
    if run_chain is None:
        return None
    consumer_run_ids, audit_run_ids = run_chain
    return {
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "task_id": task_id,
        "task_status": str(lineage["task_status"]),
        "consumer_run_ids": consumer_run_ids,
        "audit_run_ids": audit_run_ids,
    }


def _current_unsubscribe_task_lineage(
    db: sqlite3.Connection,
    *,
    task: sqlite3.Row,
    classification: sqlite3.Row,
) -> dict[str, object] | None:
    current_plan = db.execute(
        """
        select action_plan_id, action_plan_version, classification_id,
               account_id, actions_json
        from email_action_plans
        where action_plan_id=?
        """,
        (classification["current_action_plan_id"],),
    ).fetchone()
    if current_plan is None:
        return None
    try:
        payload = json.loads(str(task["trigger_message_json"]))
        actions = _json_load(
            current_plan["actions_json"],
            field="actions_json",
            expected_type=list,
        )
        thread_identity = str(classification["message_thread_identity"] or "")
        from app.email_task_adapter import email_conversation_id

        expected_action_identity = email_action_identity(
            account_id=str(classification["account_id"]),
            stable_message_identity=str(classification["stable_message_identity"]),
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(current_plan["action_plan_version"]),
        )
        expected_conversation_id = email_conversation_id(
            str(classification["account_id"]),
            thread_identity,
        )
    except (TypeError, ValueError, RecursionError, KeyError):
        return None
    if not isinstance(payload, dict) or EmailAction.UNSUBSCRIBE.value not in actions:
        return None
    expected_payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": EmailAction.UNSUBSCRIBE.value,
        "action_identity": expected_action_identity,
        "action_plan_id": current_plan["action_plan_id"],
        "action_plan_version": current_plan["action_plan_version"],
        "classification_id": current_plan["classification_id"],
        "account_id": current_plan["account_id"],
        "stable_message_identity": classification["stable_message_identity"],
        "thread_identity": thread_identity,
    }
    if (
        any(payload.get(field) != value for field, value in expected_payload.items())
        or task["trigger_message_id"] != expected_action_identity
        or task["conversation_id"] != expected_conversation_id
        or not str(task["execution_generation"] or "").strip()
    ):
        return None
    runs = db.execute(
        """
        select id, role
        from agent_runs
        where reply_task_id=? and execution_generation=?
        order by id
        """,
        (task["id"], task["execution_generation"]),
    ).fetchall()
    continuation_row = db.execute(
        """
        select continuation.controls_json, effects.operations_json
        from email_unsubscribe_continuations as continuation
        join email_unsubscribe_effects as effects
          on effects.action_identity=continuation.action_identity
         and effects.effect_digest=continuation.effect_digest
        where continuation.action_identity=?
        """,
        (expected_action_identity,),
    ).fetchone()
    continuation: dict[str, object] | None = None
    if continuation_row is not None:
        controls = _json_load(
            continuation_row["controls_json"],
            field="controls_json",
            expected_type=list,
        )
        operations = _json_load(
            continuation_row["operations_json"],
            field="operations_json",
            expected_type=list,
        )
        control_kinds = sorted(
            {
                str(control["kind"])
                for control in controls
                if isinstance(control, dict) and "kind" in control
            }
        )
        continuation = {
            "state": "awaiting_audit",
            "control_kinds": control_kinds,
            "operation_count": len(operations),
            "requires_human": bool(
                {"captcha_handoff", "credential_handoff"} & set(control_kinds)
            ),
        }
    result = {
        "kind": "unsubscribe",
        "operation": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "task_id": int(task["id"]),
        "task_status": str(task["status"]),
        "consumer_run_ids": [
            int(row["id"]) for row in runs if row["role"] == "consumer"
        ],
        "audit_run_ids": [int(row["id"]) for row in runs if row["role"] == "audit"],
        "status": str(task["status"]),
        "_sort_created_at": str(task["created_at"] or ""),
    }
    if continuation is not None:
        result["continuation"] = continuation
    return result


def _validate_unsubscribe_operations(
    operations: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise TypeError("unsubscribe operations must be a sequence")
    approved_kinds = {
        "post_one_click",
        "open_entry",
        "follow_redirect",
        "submit_form",
        "click_confirmation",
        "confirm_email",
        "reconcile_handoff",
    }
    validated: list[dict[str, str]] = []
    for operation in operations:
        if not isinstance(operation, Mapping) or set(operation) != {
            "operation_reference",
            "kind",
            "target_reference",
        }:
            raise ValueError("unsubscribe operation has invalid fields")
        kind = operation["kind"]
        if kind not in approved_kinds:
            raise ValueError("unsubscribe operation kind is invalid")
        validated.append(
            {
                "operation_reference": _validate_unsubscribe_opaque(
                    operation["operation_reference"], field="operation_reference"
                ),
                "kind": str(kind),
                "target_reference": _validate_unsubscribe_opaque(
                    operation["target_reference"], field="target_reference"
                ),
            }
        )
    if not validated or len({item["operation_reference"] for item in validated}) != len(
        validated
    ):
        raise ValueError("unsubscribe operations must be non-empty and unique")
    if len(validated) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
        raise ValueError("durable continuation operation limit exceeded")
    return validated


def _validate_terminal_expected_effect(
    value: Mapping[str, object],
) -> dict[str, object]:
    required = {
        "action_identity",
        "action_plan_id",
        "action_plan_version",
        "classification_id",
        "account_id",
        "stable_message_identity",
        "thread_identity",
        "entry_reference",
        "operations",
        "network_policy_reference",
        "network_policy_origin_references",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("expected terminal effect fields are invalid")
    binding = _validate_unsubscribe_binding(
        action_identity=value["action_identity"],
        effect_digest="0" * 64,
        action_plan_id=value["action_plan_id"],
        action_plan_version=value["action_plan_version"],
        classification_id=value["classification_id"],
        account_id=value["account_id"],
        stable_message_identity=value["stable_message_identity"],
        thread_identity=value["thread_identity"],
        entry_reference=value["entry_reference"],
    )
    operations = _validate_unsubscribe_operations(value["operations"])
    network_policy_reference = _validate_unsubscribe_opaque(
        value["network_policy_reference"],
        field="network_policy_reference",
    )
    origins = [
        _validate_unsubscribe_opaque(
            origin,
            field="network_policy_origin_reference",
        )
        for origin in value["network_policy_origin_references"]
    ]
    if not origins:
        raise ValueError("expected terminal effect needs network policy origins")
    return {
        **{key: binding[key] for key in binding if key != "effect_digest"},
        "operations": operations,
        "network_policy_reference": network_policy_reference,
        "network_policy_origin_references": origins,
    }


def _validate_unsubscribe_controls(
    controls: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    if not isinstance(controls, Sequence) or isinstance(controls, (str, bytes)):
        raise TypeError("unsubscribe controls must be a sequence")
    validated: list[dict[str, str]] = []
    for control in controls:
        if not isinstance(control, Mapping) or set(control) != {
            "reference",
            "kind",
            "intent",
        }:
            raise ValueError("unsubscribe control has invalid fields")
        if control["kind"] not in {
            "form",
            "link",
            "button",
            "confirmation_email",
            "email_otp",
            "captcha_handoff",
            "credential_handoff",
        }:
            raise ValueError("unsubscribe control kind is invalid")
        if control["intent"] not in {"continue", "unsubscribe", "confirm"}:
            raise ValueError("unsubscribe control intent is invalid")
        validated.append(
            {
                "reference": _validate_unsubscribe_opaque(
                    control["reference"], field="control_reference"
                ),
                "kind": str(control["kind"]),
                "intent": str(control["intent"]),
            }
        )
    if not validated or len({item["reference"] for item in validated}) != len(
        validated
    ):
        raise ValueError("unsubscribe controls must be non-empty and unique")
    return validated


def _validate_unsubscribe_binding(
    *,
    action_identity: object,
    effect_digest: object,
    action_plan_id: object,
    action_plan_version: object,
    classification_id: object,
    account_id: object,
    stable_message_identity: object,
    thread_identity: object,
    entry_reference: object,
) -> dict[str, object]:
    text = {
        "action_identity": action_identity,
        "effect_digest": effect_digest,
        "action_plan_id": action_plan_id,
        "account_id": account_id,
        "stable_message_identity": stable_message_identity,
        "thread_identity": thread_identity,
    }
    if any(
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\r" in value
        or "\n" in value
        for value in text.values()
    ):
        raise ValueError("unsubscribe binding fields must be non-empty text")
    if not re.fullmatch(r"[0-9a-f]{64}", str(effect_digest)):
        raise ValueError("effect_digest must be canonical sha256 hex")
    _require_positive_int(action_plan_version, field="action_plan_version")
    _require_positive_int(classification_id, field="classification_id")
    entry_reference = _validate_unsubscribe_opaque(
        entry_reference,
        field="entry_reference",
    )
    return {
        **text,
        "action_plan_version": action_plan_version,
        "classification_id": classification_id,
        "entry_reference": entry_reference,
    }


def _validate_email_reply_owner(owner: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(owner, Mapping):
        raise TypeError("email reply owner must be a mapping")
    owner_id = _validate_opaque_provider_identifier(
        owner.get("owner_id"),
        field="owner_id",
    )
    lease_token = _validate_opaque_provider_identifier(
        owner.get("lease_token"),
        field="lease_token",
    )
    generation = owner.get("generation")
    _require_positive_int(generation, field="owner_generation")
    return {
        "owner_id": owner_id,
        "generation": generation,
        "lease_token": lease_token,
    }


def _validate_email_unsubscribe_owner(owner: Mapping[str, object]) -> dict[str, object]:
    try:
        return _validate_email_reply_owner(owner)
    except (TypeError, ValueError) as exc:
        raise type(exc)(str(exc).replace("email reply", "email unsubscribe")) from exc


def email_action_identity(
    *,
    account_id: str,
    stable_message_identity: str,
    action_type: EmailAction,
    action_plan_version: int,
) -> str:
    """Identify one immutable email action across scans and restarts."""

    account_id = account_id.strip()
    stable_message_identity = stable_message_identity.strip()
    action_type = EmailAction(action_type)
    if not account_id or not stable_message_identity:
        raise ValueError("email action identity fields must be non-empty")
    if action_plan_version <= 0:
        raise ValueError("action_plan_version must be positive")
    canonical = json.dumps(
        {
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "action_type": action_type.value,
            "action_plan_version": action_plan_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"email-action:{sha256(canonical.encode('utf-8')).hexdigest()}"


def email_unsubscribe_effect_digest(
    *,
    action_identity: str,
    action_plan_id: str,
    action_plan_version: int,
    classification_id: int,
    account_id: str,
    stable_message_identity: str,
    thread_identity: str,
    entry_reference: str,
    operations: Sequence[Mapping[str, object]],
    previous_effect_digest: str = "",
    network_policy_reference: str = "network-policy:legacy",
    network_policy_origin_references: Sequence[str] = ("network-origin:legacy",),
) -> str:
    """Hash the complete immutable, Audit-accepted unsubscribe effect."""

    validated_operations = _validate_unsubscribe_operations(operations)
    binding = _validate_unsubscribe_binding(
        action_identity=action_identity,
        effect_digest="0" * 64,
        action_plan_id=action_plan_id,
        action_plan_version=action_plan_version,
        classification_id=classification_id,
        account_id=account_id,
        stable_message_identity=stable_message_identity,
        thread_identity=thread_identity,
        entry_reference=entry_reference,
    )
    binding.pop("effect_digest")
    if (
        previous_effect_digest
        and re.fullmatch(r"[0-9a-f]{64}", previous_effect_digest) is None
    ):
        raise ValueError("previous_effect_digest must be canonical sha256 hex")
    network_policy_reference = _validate_unsubscribe_opaque(
        network_policy_reference,
        field="network_policy_reference",
    )
    origin_references = [
        _validate_unsubscribe_opaque(value, field="network_policy_origin_reference")
        for value in network_policy_origin_references
    ]
    if not origin_references or len(origin_references) != len(set(origin_references)):
        raise ValueError("network policy origin references are invalid")
    digest_payload: dict[str, object] = {
        **binding,
        "operations": validated_operations,
    }
    if (
        previous_effect_digest
        or network_policy_reference != "network-policy:legacy"
        or origin_references != ["network-origin:legacy"]
    ):
        digest_payload.update(
            {
                "previous_effect_digest": previous_effect_digest,
                "network_policy_reference": network_policy_reference,
                "network_policy_origin_references": origin_references,
            }
        )
    canonical = _json_dump(digest_payload)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _direct_action_id(action_plan_id: str, action: EmailAction) -> str:
    digest = sha256(f"{action_plan_id}:{action.value}".encode("utf-8")).hexdigest()
    return f"email-action:{digest}"


def _training_sample_digest(sample: Mapping[str, object]) -> str:
    stable_fields = (
        sample.get("message_id"),
        sample.get("label"),
        sample.get("model_text"),
        sample.get("confirmed_at"),
        sample.get("classification_source"),
        sample.get("status"),
    )
    payload = json.dumps(
        stable_fields, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class EmailStore:
    """Persist messages, classifier results, immutable plans, and direct actions."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("pragma busy_timeout = 30000")
        db.execute("pragma foreign_keys = on")
        db.row_factory = sqlite3.Row
        return db

    def list_nonterminal_legacy_unsubscribe_task_attempts(
        self,
    ) -> tuple[LegacyEmailUnsubscribeTaskAttempt, ...]:
        """Inventory immutable nonterminal legacy unsubscribe attempts."""

        with self._connect() as db:
            rows = db.execute(
                """
                select id, execution_generation, status
                from reply_tasks
                where channel='email'
                  and status in ('pending', 'processing')
                  and case
                          when json_valid(trigger_message_json)
                          then json_extract(trigger_message_json, '$.schema')
                          else null
                      end='email_agent_action.v1'
                  and case
                          when json_valid(trigger_message_json)
                          then json_extract(trigger_message_json, '$.action_type')
                          else null
                      end='unsubscribe'
                  and case
                          when json_valid(trigger_message_json)
                          then json_extract(
                              trigger_message_json,
                              '$.lifecycle_version'
                          )
                          else null
                      end='email_unsubscribe_consumer_direct_v1'
                order by id
                """
            ).fetchall()
        return tuple(
            LegacyEmailUnsubscribeTaskAttempt(
                task_id=int(row["id"]),
                execution_generation=str(row["execution_generation"]),
                status=str(row["status"]),
            )
            for row in rows
        )

    def get_nonterminal_legacy_unsubscribe_task_attempt(
        self,
        task_id: int,
    ) -> LegacyEmailUnsubscribeTaskAttempt | None:
        """Read the current exact legacy attempt for one task, if still unsafe."""

        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id <= 0:
            raise ValueError("task_id must be positive")
        with self._connect() as db:
            row = db.execute(
                """
                select id, execution_generation, status
                from reply_tasks
                where id=?
                  and channel='email'
                  and status in ('pending', 'processing')
                  and case
                          when json_valid(trigger_message_json)
                          then json_extract(trigger_message_json, '$.schema')
                          else null
                      end='email_agent_action.v1'
                  and case
                          when json_valid(trigger_message_json)
                          then json_extract(trigger_message_json, '$.action_type')
                          else null
                      end='unsubscribe'
                  and case
                          when json_valid(trigger_message_json)
                          then json_extract(
                              trigger_message_json,
                              '$.lifecycle_version'
                          )
                          else null
                      end='email_unsubscribe_consumer_direct_v1'
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return LegacyEmailUnsubscribeTaskAttempt(
            task_id=int(row["id"]),
            execution_generation=str(row["execution_generation"]),
            status=str(row["status"]),
        )

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("begin")
            latest_version = self._read_schema_version(db)
            if latest_version == EMAIL_SCHEMA_VERSION:
                self._validate_durable_state(db)
                return
            if latest_version is not None and latest_version > EMAIL_SCHEMA_VERSION:
                raise EmailPersistenceCorruption(
                    f"database has newer schema version {latest_version}; "
                    f"this runtime supports {EMAIL_SCHEMA_VERSION}"
                )
            db.rollback()
            try:
                db.execute("pragma journal_mode = wal")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
            db.execute("pragma foreign_keys = off")
            db.execute("begin immediate")
            self._create_migration_table(db)
            latest_version = self._read_schema_version(db)
            assert latest_version is not None
            if latest_version > EMAIL_SCHEMA_VERSION:
                raise EmailPersistenceCorruption(
                    f"database has newer schema version {latest_version}; "
                    f"this runtime supports {EMAIL_SCHEMA_VERSION}"
                )
            if latest_version == EMAIL_SCHEMA_VERSION:
                self._validate_durable_state(db)
                return
            legacy_reply_claims = False
            if latest_version == 8:
                legacy_reply_claims = self._prepare_v8_reply_claim_migration(db)
            legacy_unsubscribe_claims = False
            if latest_version == 10:
                legacy_unsubscribe_claims = self._prepare_v10_unsubscribe_migration(db)
            legacy_unsubscribe_schema = False
            if latest_version < 14:
                legacy_unsubscribe_schema = self._prepare_unsubscribe_schema_migration(
                    db
                )
            self._create_base_tables(db)
            self._create_durable_tables(db)
            self._ensure_email_context_columns(db)
            self._ensure_direct_action_retry_column(db)
            self._ensure_action_authorization_snapshot_column(db)
            self._ensure_legacy_action_plan_provenance_column(db)
            self._ensure_unsubscribe_claim_columns(db)
            self._ensure_unsubscribe_audit_columns(db)
            self._ensure_unsubscribe_receipt_columns(db)
            self._ensure_training_snapshot_frozen_column(db)
            self._ensure_training_snapshot_watermark_columns(db)
            if legacy_reply_claims:
                self._finish_v8_reply_claim_migration(db)
            if legacy_unsubscribe_claims:
                self._finish_v10_unsubscribe_migration(db)
            if legacy_unsubscribe_schema:
                self._finish_unsubscribe_schema_migration(db)
            self._create_indexes_and_triggers(db)
            is_prototype = False
            if latest_version < 16:
                is_prototype = latest_version == 0
                if latest_version < 2:
                    self._migrate_prototype_schema(db)
                self._ensure_legacy_processed_without_plan_column(db)
                self._ensure_training_inclusion_column(db)
                self._ensure_training_inclusion_trigger(db)
                if is_prototype:
                    self._backfill_prototype_rows(db)
                if latest_version in {0, 2}:
                    self._mark_legacy_processed_without_plan(db)
                db.execute(
                    "update email_action_plans set legacy_serialization_pre_v16=1"
                )
                if not is_prototype:
                    db.execute(
                        "insert into email_schema_migrations(version, applied_at) "
                        "values (16, ?)",
                        (self._now(),),
                    )
                latest_version = 16
            if latest_version == 16:
                if is_prototype:
                    self._migrate_v16_to_v17(db, record_version=False)
                    latest_version = 17
                else:
                    self._migrate_v16_to_v17(db)
                    latest_version = 17
            if latest_version == 17:
                self._migrate_v17_to_v18(db)
                latest_version = 18
            if latest_version == 18:
                self._migrate_v18_to_v19(db, replace_version=is_prototype)
                latest_version = 19
            if latest_version == 19:
                self._migrate_v19_to_v20(db, replace_version=is_prototype)
                latest_version = 20
            if latest_version == 20:
                self._migrate_v20_to_v21(db, replace_version=is_prototype)
                latest_version = 21
            if latest_version == 21:
                self._migrate_v21_to_v22(db, replace_version=is_prototype)
                latest_version = 22
            if latest_version == 22:
                self._migrate_v22_to_v23(db, replace_version=is_prototype)
                latest_version = 23
            if latest_version == 23:
                self._migrate_v23_to_v24(db, replace_version=is_prototype)
                latest_version = 24
            if latest_version == 24:
                self._migrate_v24_to_v25(db, replace_version=is_prototype)
                latest_version = 25
            if latest_version == 25:
                self._migrate_v25_to_v26(db, replace_version=is_prototype)
                latest_version = 26
            if latest_version == 26:
                self._migrate_v26_to_v27(db, replace_version=is_prototype)
                latest_version = 27
            if latest_version == 27:
                self._migrate_v27_to_v28(db, replace_version=is_prototype)
                latest_version = 28
            if latest_version == 28:
                self._migrate_v28_to_v29(db, replace_version=is_prototype)
                latest_version = 29
            if latest_version == 29:
                self._migrate_v29_to_v30(db, replace_version=is_prototype)
                latest_version = 30
            if latest_version == 30:
                self._migrate_v30_to_v31(db, replace_version=is_prototype)
                latest_version = 31
            if latest_version == 31:
                self._migrate_v31_to_v32(db, replace_version=is_prototype)
            self._validate_durable_state(db)

    @classmethod
    def _prepare_v8_reply_claim_migration(cls, db: sqlite3.Connection) -> bool:
        if "email_reply_dispatch_claims" not in {
            row["name"]
            for row in db.execute("select name from sqlite_master where type='table'")
        }:
            return False
        if "owner_id" in cls._table_columns(db, "email_reply_dispatch_claims"):
            return False
        db.execute("drop trigger if exists trg_email_reply_dispatch_blocks_plan_switch")
        db.execute("drop index if exists idx_email_reply_dispatch_claims_status")
        db.execute(
            "alter table email_reply_dispatch_claims "
            "rename to email_reply_dispatch_claims_v8"
        )
        return True

    @classmethod
    def _finish_v8_reply_claim_migration(cls, db: sqlite3.Connection) -> None:
        rows = db.execute(
            "select * from email_reply_dispatch_claims_v8 order by action_identity"
        ).fetchall()
        for row in rows:
            account = db.execute(
                "select * from email_accounts where account_id=?",
                (row["account_id"],),
            ).fetchone()
            message = db.execute(
                """
                select thread_identity from email_messages
                where account_id=? and stable_message_identity=?
                """,
                (row["account_id"], row["stable_message_identity"]),
            ).fetchone()
            if account is None or message is None:
                raise EmailPersistenceCorruption(
                    "v8 email reply claim cannot be fenced without account history"
                )
            account_snapshot = cls._account_row(account)
            lease_token = sha256(
                f"v8:{row['action_identity']}".encode("utf-8")
            ).hexdigest()
            db.execute(
                """
                insert into email_reply_dispatch_claims (
                    action_identity, effect_digest, action_plan_id,
                    classification_id, account_id, stable_message_identity,
                    outgoing_message_id, owner_id, owner_generation,
                    lease_token, sender, thread_identity, account_updated_at,
                    account_snapshot_json, status, claimed_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["action_identity"],
                    row["effect_digest"],
                    row["action_plan_id"],
                    row["classification_id"],
                    row["account_id"],
                    row["stable_message_identity"],
                    row["outgoing_message_id"],
                    "legacy-v8",
                    lease_token,
                    account["email_address"],
                    message["thread_identity"],
                    account["updated_at"],
                    _json_dump(account_snapshot),
                    row["status"],
                    row["claimed_at"],
                    row["updated_at"],
                ),
            )
        db.execute("drop table email_reply_dispatch_claims_v8")

    @classmethod
    def _prepare_v10_unsubscribe_migration(cls, db: sqlite3.Connection) -> bool:
        tables = {
            row["name"]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        if "email_unsubscribe_claims" not in tables:
            return False
        for name in _REQUIRED_TRIGGER_SQL:
            if name.startswith("trg_email_unsubscribe_"):
                db.execute(
                    f"drop trigger if exists {_schema_identifier(name, field='trigger')}"
                )
        db.execute("drop index if exists idx_email_unsubscribe_claims_status")
        for table in (
            "email_unsubscribe_steps",
            "email_unsubscribe_receipts",
            "email_unsubscribe_claims",
        ):
            if table in tables:
                db.execute(f"alter table {table} rename to {table}_v10")
        return True

    @classmethod
    def _finish_v10_unsubscribe_migration(cls, db: sqlite3.Connection) -> None:
        legacy_columns = cls._table_columns(db, "email_unsubscribe_claims_v10")
        phase_expression = (
            "phase"
            if "phase" in legacy_columns
            else "case status when 'uncertain' then 'effect_uncertain' "
            "when 'done' then 'terminal' else 'prepared' end"
        )
        audit_run_expression = (
            "audit_agent_run_id" if "audit_agent_run_id" in legacy_columns else "null"
        )
        db.execute(
            f"""
            insert into email_unsubscribe_claims (
                action_identity, effect_digest, action_plan_id,
                action_plan_version, classification_id, account_id,
                stable_message_identity, thread_identity, entry_reference,
                operations_json, owner_id, owner_generation, lease_token,
                account_updated_at, status, phase, audit_agent_run_id,
                claimed_at, updated_at
            )
            select action_identity, effect_digest, action_plan_id,
                   action_plan_version, classification_id, account_id,
                   stable_message_identity, thread_identity, entry_reference,
                   operations_json, owner_id, owner_generation, lease_token,
                   account_updated_at, status, {phase_expression},
                   {audit_run_expression}, claimed_at, updated_at
            from email_unsubscribe_claims_v10
            """
        )
        db.execute(
            """
            insert into email_unsubscribe_effects (
                action_identity, effect_digest, previous_effect_digest,
                operations_json, network_policy_reference,
                network_policy_origins_json, audit_agent_run_id, created_at
            )
            select action_identity, effect_digest, '', operations_json,
                   'network-policy:legacy', '["network-origin:legacy"]',
                   {audit_run_expression}, claimed_at
            from email_unsubscribe_claims_v10
            """.format(audit_run_expression=audit_run_expression)
        )
        tables = {
            row["name"]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        if "email_unsubscribe_steps_v10" in tables:
            db.execute(
                """
                insert into email_unsubscribe_steps
                select * from email_unsubscribe_steps_v10
                """
            )
        if "email_unsubscribe_receipts_v10" in tables:
            db.execute(
                """
                insert into email_unsubscribe_receipts
                select * from email_unsubscribe_receipts_v10
                """
            )
        for table in (
            "email_unsubscribe_steps_v10",
            "email_unsubscribe_receipts_v10",
            "email_unsubscribe_claims_v10",
        ):
            if table in tables:
                db.execute(f"drop table {table}")

    @classmethod
    def _prepare_unsubscribe_schema_migration(cls, db: sqlite3.Connection) -> bool:
        """Rebuild pre-v14 unsubscribe tables with their current constraints."""
        tables = {
            row["name"]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        claims_sql = next(
            (
                row["sql"]
                for row in db.execute(
                    "select sql from sqlite_master where type='table' and name=?",
                    ("email_unsubscribe_claims",),
                )
            ),
            "",
        )
        receipts_sql = next(
            (
                row["sql"]
                for row in db.execute(
                    "select sql from sqlite_master where type='table' and name=?",
                    ("email_unsubscribe_receipts",),
                )
            ),
            "",
        )
        if "phase" in claims_sql and "result_text" in receipts_sql:
            return False
        legacy_suffix = "_pre_v14"
        family = (
            "email_unsubscribe_steps",
            "email_unsubscribe_receipts",
            "email_unsubscribe_continuations",
            "email_unsubscribe_effects",
            "email_unsubscribe_claims",
        )
        if any(f"{table}{legacy_suffix}" in tables for table in family):
            raise EmailPersistenceCorruption(
                "incomplete unsubscribe schema migration is present"
            )
        for name in _REQUIRED_TRIGGER_SQL:
            if name.startswith("trg_email_unsubscribe_"):
                db.execute(
                    f"drop trigger if exists {_schema_identifier(name, field='trigger')}"
                )
        db.execute("drop index if exists idx_email_unsubscribe_claims_status")
        for table in family:
            if table in tables:
                db.execute(f"alter table {table} rename to {table}{legacy_suffix}")
        return True

    @classmethod
    def _finish_unsubscribe_schema_migration(cls, db: sqlite3.Connection) -> None:
        """Copy the pre-v14 unsubscribe family without losing durable evidence."""
        suffix = "_pre_v14"
        tables = {
            row["name"]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        if "email_unsubscribe_claims" + suffix in tables:
            db.execute(
                """
                insert into email_unsubscribe_claims (
                    action_identity, effect_digest, action_plan_id,
                    action_plan_version, classification_id, account_id,
                    stable_message_identity, thread_identity, entry_reference,
                    operations_json, owner_id, owner_generation, lease_token,
                    account_updated_at, status, phase, claimed_at, updated_at
                )
                select action_identity, effect_digest, action_plan_id,
                    action_plan_version, classification_id, account_id,
                    stable_message_identity, thread_identity, entry_reference,
                    operations_json, owner_id, owner_generation, lease_token,
                    account_updated_at, status,
                    case status when 'uncertain' then 'effect_uncertain'
                        when 'done' then 'terminal' else 'prepared' end,
                    claimed_at, updated_at
                from email_unsubscribe_claims_pre_v14
                """
            )
        if "email_unsubscribe_effects" + suffix in tables:
            db.execute(
                """
                insert into email_unsubscribe_effects (
                    action_identity, effect_digest, previous_effect_digest,
                    operations_json, network_policy_reference,
                    network_policy_origins_json, created_at
                )
                select action_identity, effect_digest, previous_effect_digest,
                    operations_json, network_policy_reference,
                    network_policy_origins_json, created_at
                from email_unsubscribe_effects_pre_v14
                """
            )
        if "email_unsubscribe_continuations" + suffix in tables:
            db.execute(
                """
                insert into email_unsubscribe_continuations (
                    action_identity, effect_digest, observation_reference,
                    controls_json, created_at, updated_at
                )
                select action_identity, effect_digest, observation_reference,
                    controls_json, created_at, updated_at
                from email_unsubscribe_continuations_pre_v14
                """
            )
        if "email_unsubscribe_steps" + suffix in tables:
            db.execute(
                """
                insert into email_unsubscribe_steps (
                    id, action_identity, effect_digest, sequence, operation,
                    state, reference, created_at
                )
                select id, action_identity, effect_digest, sequence, operation,
                    state, reference, created_at
                from email_unsubscribe_steps_pre_v14
                """
            )
        if "email_unsubscribe_receipts" + suffix in tables:
            db.execute(
                """
                insert into email_unsubscribe_receipts (
                    action_identity, effect_digest, action_plan_id,
                    action_plan_version, classification_id, account_id,
                    stable_message_identity, thread_identity, entry_reference,
                    outcome, receipt_id, evidence, result_text,
                    observation_digest, started_at, completed_at, created_at
                )
                select action_identity, effect_digest, action_plan_id,
                    action_plan_version, classification_id, account_id,
                    stable_message_identity, thread_identity, entry_reference,
                    outcome, receipt_id, evidence, '', '', created_at,
                    created_at, created_at
                from email_unsubscribe_receipts_pre_v14
                """
            )
        for table in (
            "email_unsubscribe_steps",
            "email_unsubscribe_receipts",
            "email_unsubscribe_continuations",
            "email_unsubscribe_effects",
            "email_unsubscribe_claims",
        ):
            legacy = table + suffix
            if legacy in tables:
                db.execute(f"drop table {legacy}")

    @staticmethod
    def _read_schema_version(db: sqlite3.Connection) -> int | None:
        migration_tables = {
            _schema_identifier(row[0], field="sqlite_master table name")
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        if "email_schema_migrations" not in migration_tables:
            return None
        column_names = {
            _schema_identifier(row["name"], field="pragma table_info column name")
            for row in db.execute("pragma table_info(email_schema_migrations)")
        }
        missing_columns = {"version", "applied_at"} - column_names
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise EmailPersistenceCorruption(
                "email_schema_migrations is missing required columns: " + missing
            )
        row = db.execute(
            "select coalesce(max(version), 0) from email_schema_migrations"
        ).fetchone()
        try:
            return int(row[0])
        except (IndexError, TypeError, ValueError, OverflowError) as exc:
            raise EmailPersistenceCorruption("invalid email schema version") from exc

    @staticmethod
    def _create_migration_table(db: sqlite3.Connection) -> None:
        db.execute(
            """
            create table if not exists email_schema_migrations (
                version integer primary key,
                applied_at text not null
            )
            """
        )

    @staticmethod
    def _create_base_tables(db: sqlite3.Connection) -> None:
        db.execute(
            """
            create table if not exists email_classifications (
                id integer primary key,
                account_id text not null,
                folder text not null,
                uidvalidity integer not null,
                uid integer not null,
                rfc_message_id text,
                thread_id text,
                stable_message_identity text not null unique,
                sender text not null default '',
                subject text not null default '',
                preview text not null default '',
                model_text text not null default '',
                received_at text not null default '',
                category text,
                predicted_category text,
                confirmed_category text,
                confidence real not null,
                margin real not null,
                probabilities_json text not null,
                model_id text not null,
                config_version text not null,
                status text not null,
                classification_source text not null,
                agent_result_json text not null default 'null',
                action_plan_json text not null default 'null',
                current_action_plan_id text,
                included_in_model_id text,
                legacy_processed_without_plan integer not null default 0
                    check(legacy_processed_without_plan in (0, 1)),
                confirmed_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            )
            """
        )
        db.execute(
            """
            create table if not exists email_agent_classification_tasks (
                task_id text primary key check(trim(task_id) != ''),
                channel text not null check(channel='email'),
                stable_message_identity text not null unique
                    check(trim(stable_message_identity) != ''),
                status text not null
                    check(status in ('pending','running','done','failed')),
                owner text not null default '',
                generation integer not null default 0 check(generation >= 0),
                attempt_count integer not null default 0 check(attempt_count >= 0),
                lease_expires_at text not null default '',
                available_at text not null default '',
                input_json text not null check(json_valid(input_json)),
                result_json text not null default 'null' check(json_valid(result_json)),
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            )
            """
        )
        db.execute(
            """
            create table if not exists email_category_configs (
                category text primary key,
                description text not null default '',
                threshold real not null,
                actions_json text not null,
                action_parameters_json text not null default '{}',
                enabled integer not null default 1,
                config_version text not null,
                updated_at text not null default current_timestamp
            )
            """
        )

    @staticmethod
    def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {
            _schema_identifier(
                row["name"], field="pragma table_info migration column name"
            )
            for row in db.execute(f"pragma table_info({table})")
        }

    @classmethod
    def _ensure_column(
        cls,
        db: sqlite3.Connection,
        *,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        if column not in cls._table_columns(db, table):
            db.execute(f"alter table {table} add column {column} {declaration}")

    @classmethod
    def _migrate_prototype_schema(
        cls,
        db: sqlite3.Connection,
    ) -> None:
        for column in ("predicted_category", "confirmed_category"):
            cls._ensure_column(
                db,
                table="email_classifications",
                column=column,
                declaration="text",
            )
        cls._ensure_column(
            db,
            table="email_classifications",
            column="current_action_plan_id",
            declaration="text",
        )
        db.execute(
            """
            update email_classifications
            set predicted_category=category
            where predicted_category is null or predicted_category=''
            """
        )
        db.execute(
            """
            update email_classifications
            set confirmed_category=case when status='processed' then category else null end
            where confirmed_category is null or confirmed_category=''
            """
        )

    @classmethod
    def _ensure_legacy_processed_without_plan_column(
        cls,
        db: sqlite3.Connection,
    ) -> None:
        cls._ensure_column(
            db,
            table="email_classifications",
            column="legacy_processed_without_plan",
            declaration=(
                "integer not null default 0 "
                "check(legacy_processed_without_plan in (0, 1))"
            ),
        )

    @classmethod
    def _ensure_email_context_columns(cls, db: sqlite3.Connection) -> None:
        cls._ensure_column(
            db,
            table="email_messages",
            column="in_reply_to",
            declaration="text not null default ''",
        )
        cls._ensure_column(
            db,
            table="email_messages",
            column="references_json",
            declaration="text not null default '[]' check(json_valid(references_json))",
        )

    @classmethod
    def _ensure_direct_action_retry_column(cls, db: sqlite3.Connection) -> None:
        cls._ensure_column(
            db,
            table="email_actions",
            column="next_attempt_at",
            declaration="text not null default ''",
        )

    @classmethod
    def _ensure_action_authorization_snapshot_column(
        cls,
        db: sqlite3.Connection,
    ) -> None:
        cls._ensure_column(
            db,
            table="email_action_plans",
            column="authorization_snapshot_json",
            declaration=(
                "text check(authorization_snapshot_json is null "
                "or json_valid(authorization_snapshot_json))"
            ),
        )

    @classmethod
    def _ensure_legacy_action_plan_provenance_column(
        cls,
        db: sqlite3.Connection,
    ) -> None:
        cls._ensure_column(
            db,
            table="email_action_plans",
            column="legacy_serialization_pre_v16",
            declaration=(
                "integer not null default 0 "
                "check(legacy_serialization_pre_v16 in (0, 1))"
            ),
        )

    @classmethod
    def _ensure_unsubscribe_receipt_columns(cls, db: sqlite3.Connection) -> None:
        for column, declaration in (
            ("result_text", "text not null default ''"),
            ("observation_digest", "text not null default ''"),
            ("started_at", "text not null default ''"),
            ("completed_at", "text not null default ''"),
        ):
            cls._ensure_column(
                db,
                table="email_unsubscribe_receipts",
                column=column,
                declaration=declaration,
            )
        db.execute(
            "update email_unsubscribe_receipts set started_at=created_at "
            "where trim(started_at) = ''"
        )
        db.execute(
            "update email_unsubscribe_receipts set completed_at=created_at "
            "where trim(completed_at) = ''"
        )

    def _migrate_v16_to_v17(
        self,
        db: sqlite3.Connection,
        *,
        record_version: bool = True,
    ) -> None:
        """Add explicit bounded-result integrity metadata and its lookup index."""

        columns = self._table_columns(db, "email_unsubscribe_receipts")
        unexpected = {"result_text_truncated", "result_text_digest"} & columns
        if unexpected:
            raise EmailPersistenceCorruption(
                "schema v16 contains unexpected unsubscribe result metadata: "
                + ", ".join(sorted(unexpected))
            )
        db.execute(
            "alter table email_unsubscribe_receipts add column "
            "result_text_truncated integer not null default 0 "
            "check(result_text_truncated in (0, 1))"
        )
        db.execute(
            "alter table email_unsubscribe_receipts add column "
            "result_text_digest text not null default '' "
            "check(result_text_digest = '' or length(result_text_digest) = 64)"
        )
        from app.email_unsubscribe import normalize_unsubscribe_result_text

        for row in db.execute(
            "select action_identity, result_text, observation_digest "
            "from email_unsubscribe_receipts order by action_identity"
        ).fetchall():
            result_text = row["result_text"]
            observation_digest = row["observation_digest"]
            if not isinstance(result_text, str) or not isinstance(
                observation_digest, str
            ):
                raise EmailPersistenceCorruption(
                    "schema v16 unsubscribe result integrity metadata is invalid"
                )
            encoded_length = len(result_text.encode("utf-8"))
            if encoded_length > _MAX_UNSUBSCRIBE_RESULT_TEXT_BYTES:
                raise EmailPersistenceCorruption(
                    "schema v16 unsubscribe result exceeds its durable bound"
                )
            canonical_text, result_text_digest = normalize_unsubscribe_result_text(
                result_text
            )
            if canonical_text != result_text:
                raise EmailPersistenceCorruption(
                    "schema v16 unsubscribe result text is not canonical and redacted"
                )
            if not result_text:
                if observation_digest:
                    raise EmailPersistenceCorruption(
                        "schema v16 empty result has a non-empty observation digest"
                    )
                result_text_digest = ""
                result_text_truncated = 0
            else:
                if _SHA256_HEX.fullmatch(observation_digest) is None:
                    raise EmailPersistenceCorruption(
                        "schema v16 unsubscribe observation digest is invalid"
                    )
                if observation_digest == result_text_digest:
                    result_text_truncated = 0
                elif encoded_length == _MAX_UNSUBSCRIBE_RESULT_TEXT_BYTES:
                    result_text_truncated = 1
                else:
                    raise EmailPersistenceCorruption(
                        "schema v16 untruncated observation digest does not match"
                    )
            db.execute(
                "update email_unsubscribe_receipts "
                "set result_text_truncated=?, result_text_digest=? "
                "where action_identity=?",
                (
                    result_text_truncated,
                    result_text_digest,
                    row["action_identity"],
                ),
            )
        db.execute(
            "create index if not exists "
            "idx_email_unsubscribe_receipts_classification_action "
            "on email_unsubscribe_receipts(classification_id, action_identity)"
        )
        if record_version:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) "
                "values (17, ?)",
                (self._now(),),
            )

    def _migrate_v17_to_v18(self, db: sqlite3.Connection) -> None:
        """Record direct-action authorization fences created for schema v18."""

        db.execute(
            "insert into email_schema_migrations(version, applied_at) values (18, ?)",
            (self._now(),),
        )

    def _migrate_v18_to_v19(
        self,
        db: sqlite3.Connection,
        *,
        replace_version: bool = False,
    ) -> None:
        """Allow strictly validated category-key text in feedback history."""

        db.execute(
            "alter table email_feedback_requests rename to email_feedback_requests_v18"
        )
        db.execute(
            """
                create table email_feedback_requests (
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
                        or expected_current_action_plan_id
                            != resulting_action_plan_id
                    ),
                    foreign key(classification_id)
                        references email_classifications(id) on delete restrict,
                    foreign key(expected_current_action_plan_id)
                        references email_action_plans(action_plan_id)
                        on delete restrict,
                    foreign key(resulting_action_plan_id)
                        references email_action_plans(action_plan_id)
                        on delete restrict
                )
            """
        )
        db.execute(
            """
                insert into email_feedback_requests (
                    feedback_request_id, classification_id, category,
                    expected_current_action_plan_id, resulting_action_plan_id,
                    applied_at
                )
                select feedback_request_id, classification_id, category,
                       expected_current_action_plan_id, resulting_action_plan_id,
                       applied_at
                from email_feedback_requests_v18
            """
        )
        db.execute("drop table email_feedback_requests_v18")
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=19, applied_at=? "
                "where version=18",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) "
                "values (19, ?)",
                (self._now(),),
            )

    @staticmethod
    def _create_category_config_tables(db: sqlite3.Connection) -> None:
        db.execute(CATEGORY_CONFIG_TABLE_SQL)
        db.execute(FOLDER_BINDING_TABLE_SQL)
        db.execute(ACTIVE_BINDING_INDEX_SQL)

    def _migrate_v19_to_v20(
        self,
        db: sqlite3.Connection,
        *,
        replace_version: bool = False,
    ) -> None:
        """Replace mutable category configuration without rewriting history."""

        current_columns = self._table_columns(db, "email_category_configs")
        if "category_key" in current_columns:
            if current_columns != STRUCTURED_CATEGORY_CONFIG_COLUMNS:
                raise EmailPersistenceCorruption(
                    "structured category config schema is malformed"
                )
            tables = {
                row["name"]
                for row in db.execute(
                    "select name from sqlite_master where type='table'"
                )
            }
            if "email_category_folder_bindings" not in tables:
                db.execute(
                    "alter table email_category_configs "
                    "rename to email_category_configs_v20"
                )
                self._create_category_config_tables(db)
                db.execute(
                    "insert into email_category_configs "
                    "select * from email_category_configs_v20"
                )
                db.execute("drop table email_category_configs_v20")
            if replace_version:
                db.execute(
                    "update email_schema_migrations set version=20, applied_at=? "
                    "where version=19",
                    (self._now(),),
                )
            else:
                db.execute(
                    "insert into email_schema_migrations(version, applied_at) "
                    "values (20, ?)",
                    (self._now(),),
                )
            return
        legacy_rows = {
            str(row["category"]): row
            for row in db.execute("select * from email_category_configs")
        }
        db.execute(
            "alter table email_category_configs rename to email_category_configs_v19"
        )
        self._create_category_config_tables(db)
        now = self._now()
        has_enabled_account = (
            db.execute(
                "select 1 from email_accounts where enabled=1 limit 1"
            ).fetchone()
            is not None
        )
        migrated_keys = set(INITIAL_CATEGORY_CONFIGS)
        migrated_keys.update(
            key
            for key in legacy_rows
            if key not in {"important", "subscription", "billing"}
        )
        for category_key in sorted(migrated_keys):
            try:
                validated_key = validate_email_category_key(category_key)
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "invalid configured email category"
                ) from exc
            legacy = legacy_rows.get(category_key)
            if category_key == "external_billing" and legacy is None:
                legacy = legacy_rows.get("billing")
            definition = INITIAL_CATEGORY_CONFIGS.get(category_key)
            if definition is None:
                assert legacy is not None
                legacy_description = str(legacy["description"]).strip()
                core = legacy_description or f"Messages classified as {validated_key}."
                display_name = validated_key.replace("_", " ").title()
                include = (core,)
                exclude = ("Messages outside this category.",)
            else:
                display_name = str(definition["display_name"])
                core = str(definition["core"])
                include = tuple(definition["include"])
                exclude = tuple(definition["exclude"])
            db.execute(
                """
                insert into email_category_configs (
                    category_key, display_name, core_description,
                    include_json, exclude_json, threshold, actions_json,
                    action_parameters_json, enabled, description_version,
                    config_version, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validated_key,
                    display_name,
                    core,
                    _json_dump(list(include)),
                    _json_dump(list(exclude)),
                    (
                        float(legacy["threshold"])
                        if legacy is not None
                        else SEEDED_THRESHOLD
                    ),
                    str(legacy["actions_json"]) if legacy is not None else "[]",
                    (
                        str(legacy["action_parameters_json"])
                        if legacy is not None
                        else "{}"
                    ),
                    (
                        int(bool(legacy["enabled"]) and not has_enabled_account)
                        if legacy is not None
                        else int(not has_enabled_account)
                    ),
                    DESCRIPTION_VERSION,
                    (
                        str(legacy["config_version"])
                        if legacy is not None
                        else SEEDED_CONFIG_VERSION
                    ),
                    str(legacy["updated_at"]) if legacy is not None else now,
                ),
            )
        db.execute("drop table email_category_configs_v19")
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=20, applied_at=? "
                "where version=19",
                (now,),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) "
                "values (20, ?)",
                (now,),
            )

    def _migrate_v20_to_v21(
        self,
        db: sqlite3.Connection,
        *,
        replace_version: bool = False,
    ) -> None:
        """Preserve exact provider-owned mailbox identifiers and display names."""

        db.execute(
            "alter table email_category_folder_bindings "
            "rename to email_category_folder_bindings_v20"
        )
        db.execute(FOLDER_BINDING_TABLE_SQL)
        db.execute(
            """
            insert into email_category_folder_bindings (
                account_id, category_key, provider_folder_id,
                provider_folder_name, binding_status, last_verified_at
            )
            select account_id, category_key, provider_folder_id,
                   provider_folder_name, binding_status, last_verified_at
            from email_category_folder_bindings_v20
            """
        )
        db.execute("drop table email_category_folder_bindings_v20")
        db.execute(ACTIVE_BINDING_INDEX_SQL)
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=21, applied_at=? "
                "where version=20",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) "
                "values (21, ?)",
                (self._now(),),
            )

    def _migrate_v21_to_v22(
        self,
        db: sqlite3.Connection,
        *,
        replace_version: bool = False,
    ) -> None:
        """Allow the provider-neutral deterministic important-flag action."""

        for trigger in (
            "trg_email_direct_action_blocks_plan_switch",
            "trg_email_direct_action_blocks_account_update",
            "trg_email_direct_action_blocks_account_delete",
        ):
            db.execute(f"drop trigger if exists {trigger}")
        db.execute("drop index if exists idx_email_actions_status")
        db.execute(
            "alter table email_action_attempts rename to email_action_attempts_v21"
        )
        db.execute("alter table email_actions rename to email_actions_v21")
        statements = (
            """
            create table email_actions (
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
                    check(status in ('pending', 'processing', 'done', 'failed')),
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
            "insert into email_actions select * from email_actions_v21",
            """
            create table email_action_attempts (
                id integer primary key autoincrement,
                action_id text not null,
                attempt_number integer not null check(attempt_number > 0),
                status text not null check(status in ('done', 'failed')),
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
            "insert into email_action_attempts select * from email_action_attempts_v21",
            "drop table email_action_attempts_v21",
            "drop table email_actions_v21",
        )
        for statement in statements:
            db.execute(statement)
        self._create_indexes_and_triggers(db)
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=22, applied_at=? "
                "where version=21",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (22, ?)",
                (self._now(),),
            )

    def _migrate_v22_to_v23(
        self,
        db: sqlite3.Connection,
        *,
        replace_version: bool = False,
    ) -> None:
        """Add append-only folder-derived training snapshot storage."""

        if replace_version:
            db.execute(
                "update email_schema_migrations set version=23, applied_at=? "
                "where version=22",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (23, ?)",
                (self._now(),),
            )

    def _migrate_v23_to_v24(
        self,
        db: sqlite3.Connection,
        *,
        replace_version: bool = False,
    ) -> None:
        """Freeze existing snapshots and gate all later observation inserts."""

        db.execute(
            "drop trigger if exists trg_email_training_snapshots_immutable_update"
        )
        db.execute(
            """
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
            """
        )
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=24, applied_at=? "
                "where version=23",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (24, ?)",
                (self._now(),),
            )

    def _migrate_v24_to_v25(
        self,
        db: sqlite3.Connection,
        *,
        replace_version: bool = False,
    ) -> None:
        """Add truthful Agent provenance and nullable uncertain categories."""

        db.execute(
            """
            create table email_classifications_v25 (
                id integer primary key,
                account_id text not null,
                folder text not null,
                uidvalidity integer not null,
                uid integer not null,
                rfc_message_id text,
                thread_id text,
                stable_message_identity text not null unique,
                sender text not null default '',
                subject text not null default '',
                preview text not null default '',
                model_text text not null default '',
                received_at text not null default '',
                category text,
                predicted_category text,
                confirmed_category text,
                confidence real not null,
                margin real not null,
                probabilities_json text not null,
                model_id text not null,
                config_version text not null,
                status text not null,
                classification_source text not null,
                agent_result_json text not null default 'null',
                action_plan_json text not null default 'null',
                current_action_plan_id text,
                included_in_model_id text,
                legacy_processed_without_plan integer not null default 0
                    check(legacy_processed_without_plan in (0, 1)),
                confirmed_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            )
            """
        )
        db.execute(
            """
            insert into email_classifications_v25 (
                id, account_id, folder, uidvalidity, uid, rfc_message_id,
                thread_id, stable_message_identity, sender, subject, preview,
                model_text, received_at, category, predicted_category,
                confirmed_category, confidence, margin, probabilities_json,
                model_id, config_version, status, classification_source,
                action_plan_json, current_action_plan_id, included_in_model_id,
                legacy_processed_without_plan, confirmed_at, created_at, updated_at
            )
            select id, account_id, folder, uidvalidity, uid, rfc_message_id,
                   thread_id, stable_message_identity, sender, subject, preview,
                   model_text, received_at, category, predicted_category,
                   confirmed_category, confidence, margin, probabilities_json,
                   model_id, config_version, status, classification_source,
                   action_plan_json, current_action_plan_id, included_in_model_id,
                   legacy_processed_without_plan, confirmed_at, created_at, updated_at
            from email_classifications
            """
        )
        db.execute(
            """
            create table email_action_plans_v25 (
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
            """
        )
        db.execute(
            """
            insert into email_action_plans_v25 (
                action_plan_id, action_plan_version, classification_id,
                account_id, category, classification_source, confidence,
                model_id, config_version, actions_json, action_parameters_json,
                authorization_snapshot_json, legacy_serialization_pre_v16,
                created_at
            )
            select action_plan_id, action_plan_version, classification_id,
                   account_id, category, classification_source, confidence,
                   model_id, config_version, actions_json, action_parameters_json,
                   authorization_snapshot_json, legacy_serialization_pre_v16,
                   created_at
            from email_action_plans
            """
        )
        db.execute("drop table email_action_plans")
        db.execute("drop table email_classifications")
        db.execute(
            "alter table email_classifications_v25 rename to email_classifications"
        )
        db.execute("alter table email_action_plans_v25 rename to email_action_plans")
        self._create_indexes_and_triggers(db)
        self._ensure_training_inclusion_trigger(db)
        foreign_key_violations = db.execute("pragma foreign_key_check").fetchall()
        if foreign_key_violations:
            raise EmailPersistenceCorruption(
                "v25 migration foreign key violation: "
                + repr([tuple(row) for row in foreign_key_violations])
            )
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=25, applied_at=? "
                "where version=24",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (25, ?)",
                (self._now(),),
            )

    def _migrate_v25_to_v26(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Adopt the classifier queue into the validated Email schema."""

        db.execute("pragma secure_delete = on")
        tables = {
            row["name"]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        legacy_rows = (
            db.execute("select * from email_agent_classification_tasks").fetchall()
            if "email_agent_classification_tasks" in tables
            else ()
        )
        if "email_agent_classification_tasks" in tables:
            db.execute("drop table email_agent_classification_tasks")
        self._create_base_tables(db)
        for row in legacy_rows:
            payload = json.loads(row["input_json"])
            candidates = payload.get("unsubscribe_candidates", [])
            redacted_candidates = [
                {
                    "index": index,
                    "source": "legacy",
                    "digest": sha256(str(url).encode("utf-8")).hexdigest(),
                    "reference": "unsubscribe-entry:"
                    + sha256(str(url).encode("utf-8")).hexdigest(),
                }
                for index, url in enumerate(candidates)
            ]
            replacements: list[tuple[str, str]] = []
            for candidate, redacted in zip(
                candidates, redacted_candidates, strict=True
            ):
                replacements.extend(
                    (private_value, str(redacted["reference"]))
                    for private_value in _legacy_unsubscribe_private_values(candidate)
                )
            replacements.sort(key=lambda item: len(item[0]), reverse=True)
            payload = _redact_legacy_unsubscribe_json(payload, replacements)
            assert isinstance(payload, dict)
            payload["unsubscribe_candidates"] = redacted_candidates
            result = json.loads(row["result_json"])
            if isinstance(result, dict):
                selected_url = result.pop("unsubscribe_url", None)
                result = _redact_legacy_unsubscribe_json(result, replacements)
                assert isinstance(result, dict)
                if selected_url is not None:
                    selected_url = str(selected_url)
                    digest = sha256(selected_url.encode("utf-8")).hexdigest()
                    reference = "unsubscribe-entry:" + digest
                    result = _redact_legacy_unsubscribe_json(
                        result,
                        [
                            (private_value, reference)
                            for private_value in _legacy_unsubscribe_private_values(
                                selected_url
                            )
                        ],
                    )
                    assert isinstance(result, dict)
                    result["unsubscribe_candidate_digest"] = digest
                    result["unsubscribe_candidate_reference"] = reference
                    result["unsubscribe_candidate_source"] = "legacy"
                else:
                    result["unsubscribe_candidate_index"] = None
                    result["unsubscribe_candidate_source"] = None
                    result["unsubscribe_candidate_digest"] = None
                    result["unsubscribe_candidate_reference"] = None
            db.execute(
                """
                insert into email_agent_classification_tasks (
                    task_id, channel, stable_message_identity, status, owner,
                    input_json, result_json, error, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["task_id"],
                    row["channel"],
                    row["stable_message_identity"],
                    "pending" if row["status"] == "running" else row["status"],
                    "",
                    _json_dump(payload),
                    _json_dump(result),
                    row["error"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        for row in db.execute(
            "select id, agent_result_json from email_classifications where agent_result_json != 'null'"
        ).fetchall():
            result = json.loads(row["agent_result_json"])
            selected_url = result.pop("unsubscribe_url", None)
            if selected_url is not None:
                selected_url = str(selected_url)
                digest = sha256(selected_url.encode("utf-8")).hexdigest()
                reference = "unsubscribe-entry:" + digest
                result = _redact_legacy_unsubscribe_json(
                    result,
                    [
                        (private_value, reference)
                        for private_value in _legacy_unsubscribe_private_values(
                            selected_url
                        )
                    ],
                )
                assert isinstance(result, dict)
                result["unsubscribe_candidate_source"] = "legacy"
                result["unsubscribe_candidate_digest"] = digest
                result["unsubscribe_candidate_reference"] = reference
            else:
                result["unsubscribe_candidate_index"] = None
                result["unsubscribe_candidate_source"] = None
                result["unsubscribe_candidate_digest"] = None
                result["unsubscribe_candidate_reference"] = None
            db.execute(
                "update email_classifications set agent_result_json=? where id=?",
                (_json_dump(result), row["id"]),
            )
        self._create_indexes_and_triggers(db)
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=26, applied_at=? where version=25",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (26, ?)",
                (self._now(),),
            )

    def _migrate_v26_to_v27(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Add append-only outcomes for staged historical classification."""

        self._create_durable_tables(db)
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=27, applied_at=? "
                "where version=26",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (27, ?)",
                (self._now(),),
            )

    def _migrate_v27_to_v28(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        self._create_task10_historical_tables(db)
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=28, applied_at=? where version=27",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (28, ?)",
                (self._now(),),
            )

    def _migrate_v28_to_v29(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        self._ensure_column(
            db,
            table="email_historical_candidates",
            column="attempted_at",
            declaration="text not null default ''",
        )
        self._ensure_column(
            db,
            table="email_historical_candidates",
            column="next_retry_at",
            declaration="text not null default ''",
        )
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=29, applied_at=? where version=28",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (29, ?)",
                (self._now(),),
            )

    def _migrate_v29_to_v30(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Redact historical queue rows down to locator plus input digest."""

        db.execute("pragma secure_delete = on")
        rows = db.execute(
            "select rowid, * from email_historical_candidates"
        ).fetchall()
        for row in rows:
            message = _json_load(
                row["provider_message_json"],
                field="provider_message_json",
                expected_type=dict,
            )
            projection = _historical_candidate_projection(
                message,
                stable_message_identity=str(row["stable_message_identity"]),
                account_id=str(row["account_id"]),
                folder=str(row["folder"]),
            )
            db.execute(
                "update email_historical_candidates "
                "set normalized_text=?, provider_message_json=? where rowid=?",
                (
                    sha256(str(row["normalized_text"]).encode("utf-8")).hexdigest(),
                    _json_dump(projection),
                    row["rowid"],
                ),
            )
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=30, applied_at=? "
                "where version=29",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (30, ?)",
                (self._now(),),
            )

    def _migrate_v30_to_v31(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Record the bounded cross-process classifier runtime evidence schema."""

        if replace_version:
            db.execute(
                "update email_schema_migrations set version=31, applied_at=? "
                "where version=30",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (31, ?)",
                (self._now(),),
            )

    @classmethod
    def _ensure_training_snapshot_watermark_columns(
        cls, db: sqlite3.Connection
    ) -> None:
        columns = cls._table_columns(db, "email_training_snapshots")
        if "folder_label_watermark" not in columns:
            db.execute(
                "alter table email_training_snapshots add column "
                "folder_label_watermark integer not null default 0 "
                "check(folder_label_watermark >= 0)"
            )
        if "important_label_watermark" not in columns:
            db.execute(
                "alter table email_training_snapshots add column "
                "important_label_watermark integer not null default 0 "
                "check(important_label_watermark >= 0)"
            )

    def _migrate_v31_to_v32(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Record current provider truth and frozen snapshot watermarks."""

        if replace_version:
            db.execute(
                "update email_schema_migrations set version=32, applied_at=? "
                "where version=31",
                (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (32, ?)",
                (self._now(),),
            )
    @staticmethod
    def _create_task10_historical_tables(db: sqlite3.Connection) -> None:
        db.execute(
            """
            create table if not exists email_historical_traversals (
                account_id text not null,
                folder text not null,
                model_id text not null,
                uidvalidity integer,
                last_seen_uid integer not null default 0,
                updated_at text not null,
                primary key(account_id, folder, model_id)
            )
            """
        )
        db.execute(
            """
            create table if not exists email_historical_operations (
                operation_id text primary key,
                account_id text not null,
                stable_message_identity text not null,
                model_id text not null,
                classification_id integer not null,
                action_plan_id text not null,
                action_ids_json text not null check(json_valid(action_ids_json)),
                predicted_category text not null,
                threshold real not null,
                probability real not null,
                important integer not null check(important in (0,1)),
                status text not null check(status in ('processing','terminal')),
                action_outcome text not null default '',
                created_at text not null,
                updated_at text not null,
                unique(model_id, stable_message_identity)
            )
            """
        )
        db.execute(
            """
            create table if not exists email_historical_candidates (
                account_id text not null,
                folder text not null,
                model_id text not null,
                stable_message_identity text not null,
                normalized_text text not null,
                provider_message_json text not null check(json_valid(provider_message_json)),
                uidvalidity integer not null,
                uid integer not null,
                state text not null check(state in ('pending','deferred','terminal')),
                reason text not null default '',
                attempted_at text not null default '',
                next_retry_at text not null default '',
                updated_at text not null,
                primary key(account_id, folder, model_id, stable_message_identity)
            )
            """
        )

    @classmethod
    def _ensure_training_snapshot_frozen_column(cls, db: sqlite3.Connection) -> None:
        cls._ensure_column(
            db,
            table="email_training_snapshots",
            column="frozen",
            declaration="integer not null default 1 check(frozen in (0, 1))",
        )

    @classmethod
    def _ensure_unsubscribe_claim_columns(cls, db: sqlite3.Connection) -> None:
        had_phase = "phase" in cls._table_columns(db, "email_unsubscribe_claims")
        cls._ensure_column(
            db,
            table="email_unsubscribe_claims",
            column="phase",
            declaration="text not null default 'prepared'",
        )
        if not had_phase:
            db.execute(
                "update email_unsubscribe_claims set phase = case status "
                "when 'uncertain' then 'effect_uncertain' "
                "when 'done' then 'terminal' else 'prepared' end"
            )
        else:
            db.execute(
                "update email_unsubscribe_claims set phase='prepared' "
                "where trim(phase) = '' or phase is null"
            )

    @classmethod
    def _ensure_unsubscribe_audit_columns(cls, db: sqlite3.Connection) -> None:
        """Add nullable Audit ownership without rewriting historical rows."""

        for table in ("email_unsubscribe_claims", "email_unsubscribe_effects"):
            cls._ensure_column(
                db,
                table=table,
                column="audit_agent_run_id",
                declaration=(
                    "integer check(audit_agent_run_id is null "
                    "or audit_agent_run_id > 0)"
                ),
            )

    @classmethod
    def _ensure_training_inclusion_column(cls, db: sqlite3.Connection) -> None:
        cls._ensure_column(
            db,
            table="email_classifications",
            column="included_in_model_id",
            declaration="text",
        )

    @classmethod
    def _ensure_email_context_columns(cls, db: sqlite3.Connection) -> None:
        cls._ensure_column(
            db,
            table="email_messages",
            column="in_reply_to",
            declaration="text not null default ''",
        )
        cls._ensure_column(
            db,
            table="email_messages",
            column="references_json",
            declaration="text not null default '[]' check(json_valid(references_json))",
        )

    @staticmethod
    def _ensure_training_inclusion_trigger(db: sqlite3.Connection) -> None:
        db.execute(
            """
            create trigger if not exists trg_email_training_inclusion_invalidate
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
            """
        )

    @staticmethod
    def _mark_legacy_processed_without_plan(db: sqlite3.Connection) -> None:
        db.execute(
            """
            update email_classifications
            set legacy_processed_without_plan=1
            where status='processed'
              and coalesce(action_plan_json, '') in ('', 'null')
              and current_action_plan_id is null
              and legacy_processed_without_plan=0
              and not exists (
                  select 1
                  from email_action_plans
                  where email_action_plans.classification_id=email_classifications.id
              )
            """
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
                smtp_host text not null,
                smtp_port integer not null check(smtp_port between 1 and 65535),
                smtp_tls integer not null check(smtp_tls in (0, 1)),
                smtp_username text not null,
                smtp_secret_reference text not null,
                enabled integer not null check(enabled in (0, 1)),
                scan_folders_json text not null check(json_valid(scan_folders_json)),
                scan_interval_seconds integer not null check(scan_interval_seconds > 0),
                created_at text not null,
                updated_at text not null
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
                    check(status in ('pending', 'processing', 'done', 'failed')),
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
                status text not null check(status in ('done', 'failed')),
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
                network_policy_reference text not null
                    check(trim(network_policy_reference) != ''),
                network_policy_origins_json text not null
                    check(json_valid(network_policy_origins_json)),
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
                observed_at text not null check(trim(observed_at) != ''),
                primary key(account_id, stable_message_identity)
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
                source text not null check(source in ('natural', 'targeted')),
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

    @staticmethod
    def _create_indexes_and_triggers(db: sqlite3.Connection) -> None:
        statements = (
            """
            create index if not exists idx_email_agent_classification_tasks_status
            on email_agent_classification_tasks(
                status, available_at, lease_expires_at, task_id
            )
            """,
            """
            create index if not exists idx_email_classifications_status
            on email_classifications(status, updated_at desc, id desc)
            """,
            """
            create index if not exists idx_email_classifications_account_status
            on email_classifications(account_id, status, updated_at desc)
            """,
            """
            create index if not exists idx_email_messages_account_locator
            on email_messages(account_id, folder, uidvalidity, uid)
            """,
            """
            create index if not exists idx_email_actions_status
            on email_actions(status, updated_at, action_id)
            """,
            """
            create index if not exists idx_email_reply_dispatch_claims_status
            on email_reply_dispatch_claims(status, updated_at, action_identity)
            """,
            """
            create index if not exists idx_email_unsubscribe_claims_status
            on email_unsubscribe_claims(status, updated_at, action_identity)
            """,
            """
            create index if not exists idx_email_training_observations_split
            on email_training_snapshot_observations(
                snapshot_id, split, category_key, group_key
            )
            """,
            """
            create index if not exists idx_email_training_observations_provider_truth
            on email_training_snapshot_observations(
                account_id, stable_message_identity, observed_at desc, snapshot_id desc
            )
            """,
            """
            create index if not exists idx_email_provider_observations_lookup
            on email_provider_observations(
                account_id, stable_message_identity, observed_at desc
            )
            """,
            """
            create index if not exists idx_email_classifier_runtime_model_id
            on email_classifier_runtime_samples(model_id, id desc)
            """,
            """
            create trigger if not exists trg_email_training_snapshots_immutable_update
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
            """
            create trigger if not exists trg_email_training_snapshots_immutable_delete
            before delete on email_training_snapshots
            begin
                select raise(abort, 'email training snapshot is immutable');
            end
            """,
            """
            create trigger if not exists trg_email_training_observations_immutable_update
            before update on email_training_snapshot_observations
            begin
                select raise(abort, 'email training snapshot observation is immutable');
            end
            """,
            """
            create trigger if not exists trg_email_training_observations_immutable_delete
            before delete on email_training_snapshot_observations
            begin
                select raise(abort, 'email training snapshot observation is immutable');
            end
            """,
            """
            create trigger if not exists
                trg_email_training_observations_require_unfrozen_snapshot
            before insert on email_training_snapshot_observations
            when not exists (
                select 1 from email_training_snapshots
                where snapshot_id=new.snapshot_id and frozen=0
            )
            begin
                select raise(abort, 'email training snapshot is frozen');
            end
            """,
            """
            create trigger if not exists trg_email_classification_status_insert
            before insert on email_classifications
            when new.status not in ('pending_feedback', 'processed')
            begin
                select raise(abort, 'invalid email classification status');
            end
            """,
            """
            create trigger if not exists trg_email_classification_status_update
            before update of status on email_classifications
            when new.status not in ('pending_feedback', 'processed')
            begin
                select raise(abort, 'invalid email classification status');
            end
            """,
            """
            create trigger if not exists trg_email_classification_source_insert
            before insert on email_classifications
            when new.classification_source not in ('model', 'user', 'agent')
            begin
                select raise(abort, 'invalid email classification source');
            end
            """,
            """
            create trigger if not exists trg_email_classification_source_update
            before update of classification_source on email_classifications
            when new.classification_source not in ('model', 'user', 'agent')
            begin
                select raise(abort, 'invalid email classification source');
            end
            """,
            """
            create trigger if not exists trg_email_direct_action_blocks_plan_switch
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
            """
            create trigger if not exists trg_email_direct_action_blocks_account_update
            before update on email_accounts
            when exists (
                select 1 from email_actions
                where account_id=old.account_id and status='processing'
            )
            begin
                select raise(abort, 'email_direct_action_in_flight');
            end
            """,
            """
            create trigger if not exists trg_email_direct_action_blocks_account_delete
            before delete on email_accounts
            when exists (
                select 1 from email_actions
                where account_id=old.account_id and status='processing'
            )
            begin
                select raise(abort, 'email_direct_action_in_flight');
            end
            """,
            """
            create trigger if not exists trg_email_reply_dispatch_blocks_plan_switch
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
            """
            create trigger if not exists trg_email_reply_dispatch_blocks_account_update
            before update on email_accounts
            when exists (
                select 1 from email_reply_dispatch_claims
                where account_id=old.account_id and status='dispatching'
            )
            begin
                select raise(abort, 'email_reply_dispatch_in_flight');
            end
            """,
            """
            create trigger if not exists trg_email_reply_dispatch_blocks_account_delete
            before delete on email_accounts
            when exists (
                select 1 from email_reply_dispatch_claims
                where account_id=old.account_id and status='dispatching'
            )
            begin
                select raise(abort, 'email_reply_dispatch_in_flight');
            end
            """,
            """
            create trigger if not exists trg_email_reply_dispatch_blocks_thread_update
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
            """
            create trigger if not exists trg_email_reply_dispatch_blocks_message_delete
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
            """
            create trigger if not exists trg_email_unsubscribe_blocks_plan_switch
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
            """
            create trigger if not exists trg_email_unsubscribe_blocks_account_update
            before update on email_accounts
            when exists (
                select 1 from email_unsubscribe_claims
                where account_id=old.account_id and status='dispatching'
            )
            begin
                select raise(abort, 'email_unsubscribe_write_in_flight');
            end
            """,
            """
            create trigger if not exists trg_email_unsubscribe_blocks_account_delete
            before delete on email_accounts
            when exists (
                select 1 from email_unsubscribe_claims
                where account_id=old.account_id and status='dispatching'
            )
            begin
                select raise(abort, 'email_unsubscribe_write_in_flight');
            end
            """,
            """
            create trigger if not exists trg_email_unsubscribe_blocks_message_update
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
            """
            create trigger if not exists trg_email_unsubscribe_blocks_message_delete
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
        )
        for statement in statements:
            db.execute(statement)

    def _backfill_prototype_rows(self, db: sqlite3.Connection) -> None:
        now = self._now()
        db.execute(
            """
            insert into email_messages (
                account_id, stable_message_identity, folder, uidvalidity, uid,
                rfc_message_id, thread_identity, sender, recipients_json,
                subject, normalized_text, preview, attachment_metadata_json,
                received_at, created_at, updated_at
            )
            select account_id, stable_message_identity, folder, uidvalidity, uid,
                   coalesce(rfc_message_id, ''), coalesce(thread_id, ''), sender,
                   '[]', subject, model_text, preview, '[]', received_at,
                   created_at, updated_at
            from email_classifications
            where true
            on conflict(stable_message_identity) do nothing
            """
        )
        rows = db.execute(
            """
            select id, action_plan_json
            from email_classifications
            where action_plan_json != 'null' and action_plan_json != ''
            order by id
            """
        ).fetchall()
        for row in rows:
            try:
                plan = rehydrate_legacy_email_action_plan_json(row["action_plan_json"])
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid action_plan_json for classification {row['id']}"
                ) from exc
            self._persist_action_plan(db, plan, now=now)
            db.execute(
                """
                update email_classifications
                set action_plan_json=?, current_action_plan_id=?,
                    legacy_processed_without_plan=0
                where id=?
                """,
                (row["action_plan_json"], plan.action_plan_id, row["id"]),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _account_now() -> datetime:
        return datetime.now(timezone.utc)

    def _next_account_timestamp(self, existing: str | None = None) -> str:
        candidate = self._account_now().astimezone(timezone.utc)
        if existing:
            previous = datetime.fromisoformat(existing)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if candidate <= previous:
                candidate = previous + timedelta(microseconds=1)
        return candidate.isoformat(timespec="microseconds")

    @staticmethod
    def _index_columns(db: sqlite3.Connection, index_name: str) -> tuple[str, ...]:
        return tuple(
            _schema_identifier(row["name"], field="pragma index_info column name")
            for row in db.execute(f"pragma index_info({json.dumps(index_name)})")
        )

    @classmethod
    def _validate_schema_shape(cls, db: sqlite3.Connection) -> None:
        try:
            table_rows = {
                _schema_identifier(row["name"], field="sqlite_master table name"): row
                for row in db.execute(
                    "select name, sql from sqlite_master where type='table'"
                )
            }
            table_names = set(table_rows)
            missing_tables = set(_REQUIRED_TABLE_COLUMNS) - table_names
            if missing_tables:
                missing = ", ".join(sorted(missing_tables))
                raise EmailPersistenceCorruption(
                    f"missing required email table: {missing}"
                )

            indexes_by_table: dict[str, dict[str, sqlite3.Row]] = {}
            table_order = [
                table
                for table in _REQUIRED_TABLE_COLUMNS
                if table != "email_category_folder_bindings"
            ]
            table_order.append("email_category_folder_bindings")
            for table in table_order:
                required_columns = _REQUIRED_TABLE_COLUMNS[table]
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

            for index_name, (
                table,
                required_columns,
                required_predicate,
            ) in _REQUIRED_PARTIAL_UNIQUE_INDEXES.items():
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
            for trigger_name, expected_sql in _REQUIRED_TRIGGER_SQL.items():
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

    def _validate_durable_state(self, db: sqlite3.Connection) -> None:
        self._validate_schema_shape(db)
        try:
            self._validate_durable_rows(db)
        except IndexError as exc:
            raise EmailPersistenceCorruption(
                "durable email row is missing a required field"
            ) from exc

    def _validate_durable_rows(self, db: sqlite3.Connection) -> None:
        for row in db.execute("select * from email_agent_classification_tasks"):
            payload = _json_load(
                row["input_json"], field="input_json", expected_type=dict
            )
            if payload.get("stable_message_identity") != row["stable_message_identity"]:
                raise EmailPersistenceCorruption(
                    "classifier task stable identity diverges from input"
                )
            candidates = payload.get("unsubscribe_candidates")
            if not isinstance(candidates, list) or any(
                not isinstance(candidate, dict)
                or set(candidate) != {"index", "source", "digest", "reference"}
                or candidate["index"] != index
                or not isinstance(candidate["digest"], str)
                or not _SHA256_HEX.fullmatch(candidate["digest"])
                or candidate["reference"] != "unsubscribe-entry:" + candidate["digest"]
                for index, candidate in enumerate(candidates)
            ):
                raise EmailPersistenceCorruption(
                    "classifier task unsubscribe candidates are not redacted"
                )
            result = json.loads(row["result_json"])
            if isinstance(result, dict) and "unsubscribe_url" in result:
                raise EmailPersistenceCorruption(
                    "classifier task result contains private unsubscribe URL"
                )
            if row["status"] == "running" and (
                not row["owner"] or not row["lease_expires_at"]
            ):
                raise EmailPersistenceCorruption(
                    "running classifier task has no durable lease"
                )
            if row["status"] != "running" and (row["owner"] or row["lease_expires_at"]):
                raise EmailPersistenceCorruption(
                    "non-running classifier task retains a lease"
                )
        for row in db.execute(
            "select account_id, scan_folders_json from email_accounts"
        ):
            folders = _json_load(
                row["scan_folders_json"],
                field="scan_folders_json",
                expected_type=list,
            )
            if not folders or any(
                not isinstance(folder, str) or not folder.strip() for folder in folders
            ):
                raise EmailPersistenceCorruption(
                    f"scan_folders_json for account {row['account_id']} must contain "
                    "one or more folder names"
                )

        messages: dict[str, sqlite3.Row] = {}
        for row in db.execute("select * from email_messages"):
            recipients = _json_load(
                row["recipients_json"],
                field="recipients_json",
                expected_type=list,
            )
            if any(not isinstance(value, str) for value in recipients):
                raise EmailPersistenceCorruption("recipients_json must contain strings")
            references = _json_load(
                row["references_json"],
                field="references_json",
                expected_type=list,
            )
            if any(
                not isinstance(value, str)
                or not value
                or _normalized_message_id(value) != value
                for value in references
            ):
                raise EmailPersistenceCorruption(
                    "references_json must contain normalized message IDs"
                )
            if row["in_reply_to"] and (
                _normalized_message_id(row["in_reply_to"]) != row["in_reply_to"]
            ):
                raise EmailPersistenceCorruption(
                    "in_reply_to must contain a normalized message ID"
                )
            attachments = _json_load(
                row["attachment_metadata_json"],
                field="attachment_metadata_json",
                expected_type=list,
            )
            try:
                tuple(
                    EmailAttachmentMetadata.model_validate(item) for item in attachments
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid attachment_metadata_json for message {row['id']}"
                ) from exc
            messages[row["stable_message_identity"]] = row

        plans: dict[str, EmailActionPlan] = {}
        plan_rows: dict[str, sqlite3.Row] = {}
        plans_by_classification: dict[int, list[EmailActionPlan]] = {}
        for row in db.execute("select * from email_action_plans"):
            plan = self._stored_action_plan(row)
            plans[plan.action_plan_id] = plan
            plan_rows[plan.action_plan_id] = row
            plans_by_classification.setdefault(plan.classification_id, []).append(plan)
        for classification_id, stored_plans in plans_by_classification.items():
            ordered = sorted(plan.action_plan_version for plan in stored_plans)
            if ordered != list(range(1, ordered[-1] + 1)):
                raise EmailPersistenceCorruption(
                    f"non-contiguous ActionPlan versions for classification "
                    f"{classification_id}"
                )

        classifications = {
            row["id"]: row for row in db.execute("select * from email_classifications")
        }
        classifications_by_identity: dict[str, list[sqlite3.Row]] = {}
        for row in classifications.values():
            classifications_by_identity.setdefault(
                row["stable_message_identity"], []
            ).append(row)
        for row in classifications.values():
            try:
                if row["category"] is not None:
                    rehydrate_legacy_email_category_key(row["category"])
                if row["predicted_category"] is not None:
                    rehydrate_legacy_email_category_key(row["predicted_category"])
                if row["confirmed_category"]:
                    rehydrate_legacy_email_category_key(row["confirmed_category"])
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid classification category for {row['id']}"
                ) from exc
            if row["status"] not in _CLASSIFICATION_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid classification status for {row['id']}"
                )
            if row["classification_source"] not in _CLASSIFICATION_SOURCES:
                raise EmailPersistenceCorruption(
                    f"invalid classification source for {row['id']}"
                )
            probabilities = _json_load(
                row["probabilities_json"],
                field="probabilities_json",
                expected_type=dict,
            )
            try:
                tuple(
                    rehydrate_legacy_email_category_key(category)
                    for category in probabilities
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid classification probabilities for {row['id']}"
                ) from exc
            if row["agent_result_json"] != "null":
                from app.email_classifier_agent import DurableAgentClassificationResult

                try:
                    agent_result = DurableAgentClassificationResult.model_validate_json(
                        row["agent_result_json"]
                    )
                except ValueError as exc:
                    raise EmailPersistenceCorruption(
                        f"invalid Agent result for classification {row['id']}"
                    ) from exc
                if row["classification_source"] == "model":
                    raise EmailPersistenceCorruption(
                        f"model classification {row['id']} carries Agent result"
                    )
                if row["classification_source"] == "agent":
                    expected_status = (
                        EmailClassificationStatus.PROCESSED.value
                        if agent_result.certainty == "certain"
                        else EmailClassificationStatus.PENDING_FEEDBACK.value
                    )
                    if (
                        row["category"] != agent_result.category
                        or row["confidence"] != agent_result.confidence
                        or row["status"] != expected_status
                    ):
                        raise EmailPersistenceCorruption(
                            f"Agent result diverges for classification {row['id']}"
                        )
            elif row["classification_source"] == "agent":
                raise EmailPersistenceCorruption(
                    f"Agent classification {row['id']} has no Agent result"
                )
            message = messages.get(row["stable_message_identity"])
            if (
                message is None
                or message["account_id"] != row["account_id"]
                or message["stable_message_identity"] != row["stable_message_identity"]
            ):
                raise EmailPersistenceCorruption(
                    f"message identity mismatch for classification {row['id']}"
                )
            try:
                message_provider_locator = EmailProviderLocator.model_validate(
                    {
                        "account_id": message["account_id"],
                        "folder": message["folder"],
                        "uidvalidity": message["uidvalidity"],
                        "uid": message["uid"],
                        "rfc_message_id": message["rfc_message_id"] or None,
                        "thread_id": message["thread_identity"] or None,
                    }
                )
                classification_provider_locator = EmailProviderLocator.model_validate(
                    {
                        "account_id": row["account_id"],
                        "folder": row["folder"],
                        "uidvalidity": row["uidvalidity"],
                        "uid": row["uid"],
                        "rfc_message_id": row["rfc_message_id"] or None,
                        "thread_id": row["thread_id"] or None,
                    }
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid provider locator metadata for classification {row['id']}"
                ) from exc
            message_locator = (
                message_provider_locator.folder,
                message_provider_locator.uidvalidity,
                message_provider_locator.uid,
                message_provider_locator.rfc_message_id or "",
                message_provider_locator.thread_id or "",
            )
            classification_locator = (
                classification_provider_locator.folder,
                classification_provider_locator.uidvalidity,
                classification_provider_locator.uid,
                classification_provider_locator.rfc_message_id or "",
                classification_provider_locator.thread_id or "",
            )
            if message_locator != classification_locator:
                raise EmailPersistenceCorruption(
                    f"message locator mismatch for classification {row['id']}"
                )
            classification_plans = plans_by_classification.get(row["id"], [])
            has_plan_snapshot = row["action_plan_json"] not in {"", "null"}
            if row["status"] == EmailClassificationStatus.PENDING_FEEDBACK.value:
                if row["classification_source"] not in {"model", "agent"}:
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} must have model "
                        "or agent source"
                    )
                if row["classification_source"] == "agent" and (
                    row["category"] is not None
                    or row["predicted_category"] is not None
                    or probabilities
                ):
                    raise EmailPersistenceCorruption(
                        f"uncertain Agent classification {row['id']} has a category"
                    )
                if row["legacy_processed_without_plan"] != 0:
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} carries a legacy marker"
                    )
                if (
                    has_plan_snapshot
                    or row["current_action_plan_id"] is not None
                    or classification_plans
                ):
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} carries an ActionPlan"
                    )
                if row["confirmed_category"] is not None:
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} is confirmed"
                    )
                continue
            if row["category"] is None:
                raise EmailPersistenceCorruption(
                    f"processed classification {row['id']} has no category"
                )
            if (
                row["classification_source"] == "user"
                and row["confirmed_category"] != row["category"]
            ):
                raise EmailPersistenceCorruption(
                    f"user-confirmed classification {row['id']} has inconsistent category"
                )
            if (
                row["classification_source"] in {"model", "agent"}
                and row["confirmed_category"] is not None
                and row["confirmed_category"] != row["category"]
            ):
                raise EmailPersistenceCorruption(
                    f"model-processed classification {row['id']} has inconsistent category"
                )
            if row["current_action_plan_id"] is None:
                if (
                    has_plan_snapshot
                    or classification_plans
                    or row["legacy_processed_without_plan"] != 1
                ):
                    raise EmailPersistenceCorruption(
                        f"processed classification {row['id']} has no current ActionPlan"
                    )
                continue
            if row["legacy_processed_without_plan"] != 0 or not has_plan_snapshot:
                raise EmailPersistenceCorruption(
                    f"processed classification {row['id']} has inconsistent ActionPlan state"
                )
            current_plan = plans.get(row["current_action_plan_id"])
            if current_plan is None or current_plan.classification_id != row["id"]:
                raise EmailPersistenceCorruption(
                    f"missing current ActionPlan for classification {row['id']}"
                )
            highest_version = max(
                plan.action_plan_version for plan in classification_plans
            )
            if current_plan.action_plan_version != highest_version:
                raise EmailPersistenceCorruption(
                    f"current ActionPlan is not highest ActionPlan version for "
                    f"classification {row['id']}"
                )
            expected_fields = (
                row["account_id"],
                row["category"],
                row["classification_source"],
                row["confidence"],
                row["model_id"],
                row["config_version"],
            )
            plan_fields = (
                current_plan.account_id,
                current_plan.category,
                current_plan.classification_source,
                current_plan.confidence,
                current_plan.model_id,
                current_plan.config_version,
            )
            if plan_fields != expected_fields:
                raise EmailPersistenceCorruption(
                    f"current ActionPlan classification fields mismatch for {row['id']}"
                )
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
                current_plan_row = plan_rows[current_plan.action_plan_id]
                if (
                    current_plan_row["legacy_serialization_pre_v16"] != 1
                    or current_plan_row["authorization_snapshot_json"] is not None
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

        for stable_identity in messages:
            related = classifications_by_identity.get(stable_identity, [])
            if not related:
                raise EmailPersistenceCorruption(
                    f"orphan email message {stable_identity} has no classification"
                )
            if len(related) != 1:
                raise EmailPersistenceCorruption(
                    f"email message {stable_identity} has multiple classifications"
                )

        for plan in plans.values():
            classification = classifications.get(plan.classification_id)
            if (
                classification is None
                or classification["account_id"] != plan.account_id
            ):
                raise EmailPersistenceCorruption(
                    f"ActionPlan {plan.action_plan_id} has no matching classification"
                )

        for row in db.execute("select * from email_feedback_requests"):
            try:
                request_id = _validate_feedback_request_id(row["feedback_request_id"])
                expected_plan_id = _validate_expected_action_plan_id(
                    row["expected_current_action_plan_id"]
                )
                category = rehydrate_legacy_email_category_key(row["category"])
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "invalid durable email feedback request"
                ) from exc
            classification = classifications.get(row["classification_id"])
            resulting_plan = plans.get(row["resulting_action_plan_id"])
            expected_plan = (
                plans.get(expected_plan_id) if expected_plan_id is not None else None
            )
            valid_result = (
                classification is not None
                and resulting_plan is not None
                and resulting_plan.classification_id == row["classification_id"]
                and resulting_plan.category == category
                and resulting_plan.classification_source == "user"
            )
            if not valid_result:
                raise EmailPersistenceCorruption(
                    f"feedback request {request_id} has an inconsistent result"
                )
            if expected_plan_id is None:
                valid_version = resulting_plan.action_plan_version == 1
            else:
                valid_version = (
                    expected_plan is not None
                    and expected_plan.classification_id == row["classification_id"]
                    and resulting_plan.action_plan_version
                    == expected_plan.action_plan_version + 1
                )
            if not valid_version:
                raise EmailPersistenceCorruption(
                    f"feedback request {request_id} has an inconsistent plan version"
                )

        for row in db.execute("select * from email_reply_receipts"):
            receipt = self._email_reply_receipt_row(row)
            provider_receipt = receipt["provider_receipt"]
            if set(provider_receipt) != {
                "sent_folder",
                "sent_uidvalidity",
                "sent_uid",
                "smtp_acceptance_id",
            }:
                raise EmailPersistenceCorruption(
                    "invalid durable email reply provider receipt"
                )
            try:
                assert_no_credentials(provider_receipt)
                _validate_opaque_provider_identifier(
                    receipt["outgoing_message_id"],
                    field="outgoing_message_id",
                )
                _validate_opaque_provider_identifier(
                    receipt["provider_target"],
                    field="provider_target",
                )
                _validate_opaque_provider_identifier(
                    receipt["provider_result_id"],
                    field="provider_result_id",
                )
                _validate_provider_location(
                    provider_receipt["sent_folder"],
                    field="sent_folder",
                )
                _validate_opaque_provider_identifier(
                    provider_receipt["smtp_acceptance_id"],
                    field="smtp_acceptance_id",
                    allow_empty=True,
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "email reply provider receipt is not a safe opaque receipt"
                ) from exc
            plan = plans.get(receipt["action_plan_id"])
            classification = classifications.get(receipt["classification_id"])
            if (
                plan is None
                or classification is None
                or plan.classification_id != receipt["classification_id"]
                or plan.account_id != receipt["account_id"]
                or classification["stable_message_identity"]
                != receipt["stable_message_identity"]
                or EmailAction.AUTO_REPLY not in plan.agent_actions
                or not isinstance(provider_receipt["sent_folder"], str)
                or not provider_receipt["sent_folder"]
                or not isinstance(provider_receipt["sent_uidvalidity"], int)
                or provider_receipt["sent_uidvalidity"] <= 0
                or not isinstance(provider_receipt["sent_uid"], int)
                or provider_receipt["sent_uid"] <= 0
                or not isinstance(provider_receipt["smtp_acceptance_id"], str)
            ):
                raise EmailPersistenceCorruption(
                    "email reply receipt does not match its immutable ActionPlan"
                )

        for row in db.execute("select * from email_reply_dispatch_claims"):
            claim = self._email_reply_dispatch_claim_row(row)
            plan = plans.get(claim["action_plan_id"])
            classification = classifications.get(claim["classification_id"])
            try:
                _validate_email_reply_owner(
                    {
                        "owner_id": claim["owner_id"],
                        "generation": claim["owner_generation"],
                        "lease_token": claim["lease_token"],
                    }
                )
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "email reply dispatch claim has an invalid owner fence"
                ) from exc
            account_snapshot = claim["account_snapshot"]
            if (
                claim["status"] not in _EMAIL_REPLY_CLAIM_STATUSES
                or plan is None
                or classification is None
                or plan.classification_id != claim["classification_id"]
                or plan.account_id != claim["account_id"]
                or classification["stable_message_identity"]
                != claim["stable_message_identity"]
                or EmailAction.AUTO_REPLY not in plan.agent_actions
                or account_snapshot.get("account_id") != claim["account_id"]
                or account_snapshot.get("email_address") != claim["sender"]
                or account_snapshot.get("updated_at") != claim["account_updated_at"]
            ):
                raise EmailPersistenceCorruption(
                    "email reply dispatch claim does not match its ActionPlan"
                )
            if (
                claim["status"] == "done"
                and db.execute(
                    "select 1 from email_reply_receipts where action_identity=?",
                    (claim["action_identity"],),
                ).fetchone()
                is None
            ):
                raise EmailPersistenceCorruption(
                    "done email reply dispatch claim has no durable receipt"
                )
            if claim["status"] == "dispatching":
                live_account = db.execute(
                    "select * from email_accounts where account_id=?",
                    (claim["account_id"],),
                ).fetchone()
                live_message = messages.get(claim["stable_message_identity"])
                if (
                    classification["current_action_plan_id"] != claim["action_plan_id"]
                    or live_account is None
                    or not live_account["enabled"]
                    or live_account["email_address"] != claim["sender"]
                    or live_account["updated_at"] != claim["account_updated_at"]
                    or live_message is None
                    or live_message["thread_identity"] != claim["thread_identity"]
                ):
                    raise EmailPersistenceCorruption(
                        "dispatching email reply claim lost its live authorization"
                    )

        unsubscribe_effects: dict[tuple[str, str], dict[str, Any]] = {}
        for row in db.execute("select * from email_unsubscribe_effects"):
            effect_claim = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (row["action_identity"],),
            ).fetchone()
            if effect_claim is None:
                raise EmailPersistenceCorruption(
                    "unsubscribe claim does not match its immutable ActionPlan"
                )
            try:
                operations = _validate_unsubscribe_operations(
                    _json_load(
                        row["operations_json"],
                        field="operations_json",
                        expected_type=list,
                    )
                )
                origins = _json_load(
                    row["network_policy_origins_json"],
                    field="network_policy_origins_json",
                    expected_type=list,
                )
                expected = email_unsubscribe_effect_digest(
                    action_identity=row["action_identity"],
                    action_plan_id=effect_claim["action_plan_id"],
                    action_plan_version=effect_claim["action_plan_version"],
                    classification_id=effect_claim["classification_id"],
                    account_id=effect_claim["account_id"],
                    stable_message_identity=effect_claim["stable_message_identity"],
                    thread_identity=effect_claim["thread_identity"],
                    entry_reference=effect_claim["entry_reference"],
                    operations=operations,
                    previous_effect_digest=row["previous_effect_digest"],
                    network_policy_reference=row["network_policy_reference"],
                    network_policy_origin_references=origins,
                )
            except (IndexError, TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe effect prefix contains invalid durable state"
                ) from exc
            if expected != row["effect_digest"]:
                raise EmailPersistenceCorruption(
                    "unsubscribe effect prefix digest is invalid"
                )
            unsubscribe_effects[(row["action_identity"], row["effect_digest"])] = {
                "operations": operations,
                "previous_effect_digest": row["previous_effect_digest"],
                "network_policy_reference": row["network_policy_reference"],
                "network_policy_origin_references": origins,
            }

        for (action_identity, _), effect in unsubscribe_effects.items():
            previous_digest = effect["previous_effect_digest"]
            if not previous_digest:
                if effect["network_policy_reference"] != "network-policy:legacy" and (
                    len(effect["operations"]) != 1
                    or effect["operations"][0]["kind"]
                    not in {"open_entry", "post_one_click"}
                ):
                    raise EmailPersistenceCorruption(
                        "unsubscribe initial effect prefix is invalid"
                    )
                continue
            previous = unsubscribe_effects.get((action_identity, previous_digest))
            if (
                previous is None
                or effect["operations"][:-1] != previous["operations"]
                or len(effect["operations"]) != len(previous["operations"]) + 1
                or effect["network_policy_reference"]
                != previous["network_policy_reference"]
                or effect["network_policy_origin_references"]
                != previous["network_policy_origin_references"]
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe effect prefix chain is not append-only"
                )

        unsubscribe_claims: dict[str, dict[str, Any]] = {}
        for row in db.execute("select * from email_unsubscribe_claims"):
            claim = self._email_unsubscribe_claim_row(row)
            unsubscribe_claims[claim["action_identity"]] = claim
            plan = plans.get(claim["action_plan_id"])
            classification = classifications.get(claim["classification_id"])
            message = messages.get(claim["stable_message_identity"])
            try:
                _validate_email_unsubscribe_owner(
                    {
                        "owner_id": claim["owner_id"],
                        "generation": claim["owner_generation"],
                        "lease_token": claim["lease_token"],
                    }
                )
                _validate_unsubscribe_opaque(
                    claim["entry_reference"], field="entry_reference"
                )
                operations = _validate_unsubscribe_operations(claim["operations"])
                current_effect = unsubscribe_effects.get(
                    (claim["action_identity"], claim["effect_digest"])
                )
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe claim contains non-redacted durable state"
                ) from exc
            if (
                claim["status"] not in _EMAIL_UNSUBSCRIBE_CLAIM_STATUSES
                or current_effect is None
                or current_effect["operations"] != operations
                or plan is None
                or classification is None
                or message is None
                or plan.classification_id != claim["classification_id"]
                or plan.action_plan_version != claim["action_plan_version"]
                or plan.account_id != claim["account_id"]
                or classification["stable_message_identity"]
                != claim["stable_message_identity"]
                or message["account_id"] != claim["account_id"]
                or message["thread_identity"] != claim["thread_identity"]
                or EmailAction.UNSUBSCRIBE not in plan.agent_actions
                or claim["action_identity"]
                != email_action_identity(
                    account_id=claim["account_id"],
                    stable_message_identity=claim["stable_message_identity"],
                    action_type=EmailAction.UNSUBSCRIBE,
                    action_plan_version=claim["action_plan_version"],
                )
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe claim does not match its immutable ActionPlan"
                )
            receipt_exists = db.execute(
                "select 1 from email_unsubscribe_receipts where action_identity=?",
                (claim["action_identity"],),
            ).fetchone()
            if claim["status"] == "done" and receipt_exists is None:
                raise EmailPersistenceCorruption(
                    "done unsubscribe claim has no durable terminal receipt"
                )
            if claim["status"] == "dispatching":
                live_account = db.execute(
                    "select * from email_accounts where account_id=?",
                    (claim["account_id"],),
                ).fetchone()
                if (
                    classification["status"] != "processed"
                    or classification["current_action_plan_id"]
                    != claim["action_plan_id"]
                    or live_account is None
                    or not live_account["enabled"]
                    or live_account["updated_at"] != claim["account_updated_at"]
                ):
                    raise EmailPersistenceCorruption(
                        "dispatching unsubscribe claim lost live authorization"
                    )

            continuation = db.execute(
                "select * from email_unsubscribe_continuations where action_identity=?",
                (claim["action_identity"],),
            ).fetchone()
            if (claim["status"] == "awaiting_audit") != (continuation is not None):
                raise EmailPersistenceCorruption(
                    "unsubscribe awaiting-audit claim has inconsistent continuation"
                )
            if continuation is not None:
                try:
                    _validate_unsubscribe_opaque(
                        continuation["observation_reference"],
                        field="observation_reference",
                    )
                    _validate_unsubscribe_controls(
                        _json_load(
                            continuation["controls_json"],
                            field="controls_json",
                            expected_type=list,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise EmailPersistenceCorruption(
                        "unsubscribe continuation contains unsafe durable state"
                    ) from exc
                if continuation["effect_digest"] != claim["effect_digest"]:
                    raise EmailPersistenceCorruption(
                        "unsubscribe continuation effect binding is invalid"
                    )

        next_sequences: dict[str, int] = {}
        for row in db.execute(
            "select * from email_unsubscribe_steps order by action_identity, sequence"
        ):
            claim = unsubscribe_claims.get(row["action_identity"])
            expected_sequence = next_sequences.get(row["action_identity"], 1)
            try:
                for field in ("operation", "state", "reference"):
                    _validate_unsubscribe_opaque(row[field], field=field)
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe journal contains non-redacted state"
                ) from exc
            if (
                claim is None
                or (row["action_identity"], row["effect_digest"])
                not in unsubscribe_effects
                or row["sequence"] != expected_sequence
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe journal is not contiguous or effect-bound"
                )
            effect = unsubscribe_effects[(row["action_identity"], row["effect_digest"])]
            if row["state"] == "action_required" and (
                row["sequence"] > len(effect["operations"])
                or row["operation"] != effect["operations"][row["sequence"] - 1]["kind"]
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe journal does not match its audited operation prefix"
                )
            next_sequences[row["action_identity"]] = expected_sequence + 1

        for action_identity, claim in unsubscribe_claims.items():
            if claim["status"] == "awaiting_audit" and (
                next_sequences.get(action_identity, 1) - 1 != len(claim["operations"])
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe continuation prefix is not fully journaled"
                )

        for row in db.execute("select * from email_unsubscribe_receipts"):
            receipt = self._email_unsubscribe_receipt_row(row)
            claim = unsubscribe_claims.get(receipt["action_identity"])
            try:
                _validate_unsubscribe_opaque(
                    receipt["entry_reference"], field="entry_reference"
                )
                _validate_unsubscribe_opaque(receipt["receipt_id"], field="receipt_id")
                _validate_unsubscribe_opaque(receipt["evidence"], field="evidence")
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe receipt contains non-redacted state"
                ) from exc
            if (
                claim is None
                or claim["status"] != "done"
                or receipt["outcome"] not in _EMAIL_UNSUBSCRIBE_OUTCOMES
                or any(
                    receipt[key] != claim[key]
                    for key in (
                        "effect_digest",
                        "action_plan_id",
                        "action_plan_version",
                        "classification_id",
                        "account_id",
                        "stable_message_identity",
                        "thread_identity",
                        "entry_reference",
                    )
                )
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe receipt does not match its exact accepted effect"
                )

        for row in db.execute("select * from email_category_configs"):
            try:
                validate_email_category_key(row["category_key"])
                validate_category_descriptions(
                    display_name=row["display_name"],
                    core_description=row["core_description"],
                    include=_json_load(
                        row["include_json"],
                        field="include_json",
                        expected_type=list,
                    ),
                    exclude=_json_load(
                        row["exclude_json"],
                        field="exclude_json",
                        expected_type=list,
                    ),
                    description_version=row["description_version"],
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "invalid configured email category"
                ) from exc
            actions = _json_load(
                row["actions_json"], field="actions_json", expected_type=list
            )
            parameters = _json_load(
                row["action_parameters_json"],
                field="action_parameters_json",
                expected_type=dict,
            )
            try:
                tuple(EmailAction(action) for action in actions)
                tuple(EmailAction(action) for action in parameters)
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "invalid configured email action"
                ) from exc

        for row in db.execute("select * from email_category_folder_bindings"):
            try:
                VerifiedEmailFolderBinding(
                    account_id=row["account_id"],
                    provider_folder_id=row["provider_folder_id"],
                    provider_folder_name=row["provider_folder_name"],
                    binding_status=row["binding_status"],
                    last_verified_at=row["last_verified_at"],
                )
                validate_email_category_key(row["category_key"])
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "invalid email category folder binding"
                ) from exc

        self._validate_category_binding_completeness(db)

        action_rows = list(db.execute("select * from email_actions"))
        actions_by_plan: dict[str, list[sqlite3.Row]] = {}
        actions_by_id: dict[str, sqlite3.Row] = {}
        for row in action_rows:
            if row["action_type"] not in _DIRECT_ACTION_VALUES:
                raise EmailPersistenceCorruption(
                    f"non-direct email action row {row['action_id']}"
                )
            if row["status"] not in _CURRENT_ACTION_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid current action status for {row['action_id']}"
                )
            parameters = _json_load(
                row["parameters_json"],
                field="parameters_json",
                expected_type=dict,
            )
            plan = plans.get(row["action_plan_id"])
            action = EmailAction(row["action_type"])
            expected_parameters = (
                dict(plan.action_parameters.get(action, {}))
                if plan is not None
                else None
            )
            immutable_fields_match = plan is not None and (
                action in plan.direct_actions
                and row["action_id"] == _direct_action_id(plan.action_plan_id, action)
                and row["classification_id"] == plan.classification_id
                and row["account_id"] == plan.account_id
                and row["config_version"] == plan.config_version
                and _json_dump(parameters) == _json_dump(expected_parameters)
            )
            if not immutable_fields_match:
                raise EmailPersistenceCorruption(
                    f"immutable direct action {row['action_id']} does not match its "
                    "ActionPlan"
                )
            actions_by_plan.setdefault(row["action_plan_id"], []).append(row)
            actions_by_id[row["action_id"]] = row
        for plan in plans.values():
            expected_actions = {action.value for action in plan.direct_actions}
            actual_actions = {
                row["action_type"]
                for row in actions_by_plan.get(plan.action_plan_id, [])
            }
            if actual_actions != expected_actions:
                raise EmailPersistenceCorruption(
                    f"direct action row set mismatch for ActionPlan {plan.action_plan_id}"
                )

        attempts_by_action: dict[str, list[sqlite3.Row]] = {}
        for row in db.execute(
            "select * from email_action_attempts order by action_id, attempt_number"
        ):
            if row["status"] not in _TERMINAL_ATTEMPT_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid action attempt status for {row['id']}"
                )
            if row["action_id"] not in actions_by_id:
                raise EmailPersistenceCorruption(
                    f"attempt {row['id']} has no current direct action"
                )
            attempts_by_action.setdefault(row["action_id"], []).append(row)
        execution_fields = (
            "provider_operation",
            "provider_target",
            "provider_result_id",
            "error",
            "started_at",
            "finished_at",
        )
        for action_id, row in actions_by_id.items():
            attempts = attempts_by_action.get(action_id, [])
            numbers = [attempt["attempt_number"] for attempt in attempts]
            if numbers != list(range(1, len(attempts) + 1)):
                raise EmailPersistenceCorruption(
                    f"non-contiguous attempt ledger for action {action_id}"
                )
            if row["status"] == "pending":
                if (
                    attempts
                    or row["attempt_count"] != 0
                    or any(row[field] for field in execution_fields)
                ):
                    raise EmailPersistenceCorruption(
                        f"pending action {action_id} has attempt state"
                    )
                continue
            if row["attempt_count"] != len(attempts):
                raise EmailPersistenceCorruption(
                    f"attempt count mismatch for action {action_id}"
                )
            if row["status"] == "processing":
                if (
                    not row["started_at"]
                    or row["finished_at"]
                    or row["provider_result_id"]
                    or row["error"]
                ):
                    raise EmailPersistenceCorruption(
                        f"processing action {action_id} has impossible terminal state"
                    )
                continue
            if not attempts:
                raise EmailPersistenceCorruption(
                    f"terminal action {action_id} has no terminal attempt"
                )
            latest = attempts[-1]
            if row["status"] != latest["status"] or any(
                row[field] != latest[field] for field in execution_fields
            ):
                raise EmailPersistenceCorruption(
                    f"latest attempt mismatch for action {action_id}"
                )

    @staticmethod
    def _classification_row(row: sqlite3.Row) -> dict[str, Any]:
        probabilities = _json_load(
            row["probabilities_json"],
            field="probabilities_json",
            expected_type=dict,
        )
        if row["status"] not in _CLASSIFICATION_STATUSES:
            raise EmailPersistenceCorruption("invalid classification status")
        if row["classification_source"] not in _CLASSIFICATION_SOURCES:
            raise EmailPersistenceCorruption("invalid classification source")
        action_plan: dict[str, Any] | None
        if row["action_plan_json"] in {"", "null"}:
            action_plan = None
        else:
            try:
                plan = rehydrate_legacy_email_action_plan_json(row["action_plan_json"])
            except ValueError as exc:
                raise EmailPersistenceCorruption("invalid action_plan_json") from exc
            action_plan = plan.model_dump(mode="json")
        if row["agent_result_json"] != "null":
            agent_result = _json_load(
                row["agent_result_json"],
                field="agent_result_json",
                expected_type=dict,
            )
        else:
            agent_result = None
        return {
            "id": row["id"],
            "account_id": row["account_id"],
            "folder": row["folder"],
            "uidvalidity": row["uidvalidity"],
            "uid": row["uid"],
            "rfc_message_id": row["rfc_message_id"],
            "thread_id": row["thread_id"],
            "stable_message_identity": row["stable_message_identity"],
            "sender": row["sender"],
            "subject": row["subject"],
            "preview": row["preview"],
            "received_at": row["received_at"],
            "category": row["category"],
            "predicted_category": row["predicted_category"],
            "confirmed_category": row["confirmed_category"],
            "confidence": row["confidence"],
            "margin": row["margin"],
            "probabilities": probabilities,
            "model_id": row["model_id"],
            "config_version": row["config_version"],
            "status": row["status"],
            "classification_source": row["classification_source"],
            "agent_result": agent_result,
            "action_plan": action_plan,
            "current_action_plan_id": row["current_action_plan_id"],
            "confirmed_at": row["confirmed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _classification_evidence_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = cls._classification_row(row)
        raw_metadata = row["message_attachment_metadata_json"]
        if raw_metadata is None:
            item["attachment_metadata"] = []
            return item
        attachments = _json_load(
            raw_metadata,
            field="attachment_metadata_json",
            expected_type=list,
        )
        try:
            item["attachment_metadata"] = [
                EmailAttachmentMetadata.model_validate(attachment).model_dump(
                    mode="json"
                )
                for attachment in attachments
            ]
        except ValueError as exc:
            raise EmailPersistenceCorruption(
                f"invalid attachment_metadata_json for classification {row['id']}"
            ) from exc
        return item

    @staticmethod
    def _stored_action_plan(row: sqlite3.Row) -> EmailActionPlan:
        payload: dict[str, object] = {
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "category": row["category"],
            "classification_source": row["classification_source"],
            "confidence": row["confidence"],
            "model_id": row["model_id"],
            "config_version": row["config_version"],
            "actions": _json_load(
                row["actions_json"],
                field="actions_json",
                expected_type=list,
            ),
            "action_parameters": _json_load(
                row["action_parameters_json"],
                field="action_parameters_json",
                expected_type=dict,
            ),
            "created_at": row["created_at"],
        }
        if row["authorization_snapshot_json"] is not None:
            payload.update(
                {
                    "authorization_snapshot_format": "authorization_snapshot_v2",
                    "action_authorizations": _json_load(
                        row["authorization_snapshot_json"],
                        field="authorization_snapshot_json",
                        expected_type=list,
                    ),
                }
            )
        try:
            return rehydrate_legacy_email_action_plan_json(_json_dump(payload))
        except ValueError as exc:
            raise EmailPersistenceCorruption(
                f"invalid immutable ActionPlan {row['action_plan_id']}"
            ) from exc

    def _feedback_application(
        self,
        db: sqlite3.Connection,
        request: sqlite3.Row,
        *,
        applied: bool,
    ) -> EmailFeedbackApplication:
        classification = db.execute(
            "select * from email_classifications where id=?",
            (request["classification_id"],),
        ).fetchone()
        plan_row = db.execute(
            "select * from email_action_plans where action_plan_id=?",
            (request["resulting_action_plan_id"],),
        ).fetchone()
        if classification is None or plan_row is None:
            raise EmailPersistenceCorruption(
                f"feedback request {request['feedback_request_id']} has no result"
            )
        action_plan = self._stored_action_plan(plan_row)
        confirmed = self._classification_row(classification)
        confirmed.update(
            {
                "category": action_plan.category,
                "confirmed_category": action_plan.category,
                "confidence": action_plan.confidence,
                "model_id": action_plan.model_id,
                "config_version": action_plan.config_version,
                "status": EmailClassificationStatus.PROCESSED.value,
                "classification_source": "user",
                "action_plan": action_plan.model_dump(mode="json"),
                "current_action_plan_id": action_plan.action_plan_id,
                "confirmed_at": request["applied_at"],
                "updated_at": request["applied_at"],
            }
        )
        return EmailFeedbackApplication(
            feedback_request_id=request["feedback_request_id"],
            expected_current_action_plan_id=request["expected_current_action_plan_id"],
            resulting_action_plan_id=request["resulting_action_plan_id"],
            confirmed=confirmed,
            applied=applied,
        )

    @staticmethod
    def _check_classification_identity(
        db: sqlite3.Connection,
        classification: EmailClassification,
    ) -> sqlite3.Row | None:
        by_id = db.execute(
            "select id, stable_message_identity from email_classifications where id=?",
            (classification.classification_id,),
        ).fetchone()
        if by_id is not None and (
            by_id["stable_message_identity"] != classification.stable_message_identity
        ):
            raise EmailClassificationIdentityCollision(
                f"classification ID {classification.classification_id} is already bound "
                f"to {by_id['stable_message_identity']}"
            )
        by_identity = db.execute(
            "select * from email_classifications where stable_message_identity=?",
            (classification.stable_message_identity,),
        ).fetchone()
        if by_identity is not None and (
            by_identity["id"] != classification.classification_id
        ):
            raise EmailClassificationIdentityCollision(
                f"stable identity {classification.stable_message_identity} is already bound "
                f"to classification ID {by_identity['id']}"
            )
        return by_identity

    @staticmethod
    def _upsert_message(
        db: sqlite3.Connection,
        classification: EmailClassification,
        *,
        sender: str,
        recipients: Sequence[str],
        subject: str,
        normalized_text: str,
        preview: str,
        attachment_metadata: Sequence[EmailAttachmentMetadata],
        in_reply_to: str,
        references: Sequence[str],
        received_at: str,
        now: str,
    ) -> None:
        if any(not isinstance(recipient, str) for recipient in recipients):
            raise ValueError("recipients must contain strings")
        if any(
            not isinstance(item, EmailAttachmentMetadata)
            for item in attachment_metadata
        ):
            raise ValueError("attachment_metadata must contain metadata records")
        normalized_in_reply_to = _normalized_message_id(in_reply_to)
        normalized_references = _normalized_message_ids(references)
        locator = classification.provider_locator
        db.execute(
            """
            insert into email_messages (
                account_id, stable_message_identity, folder, uidvalidity, uid,
                rfc_message_id, in_reply_to, references_json, thread_identity,
                sender, recipients_json,
                subject, normalized_text, preview, attachment_metadata_json,
                received_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(stable_message_identity) do update set
                folder=excluded.folder,
                uidvalidity=excluded.uidvalidity,
                uid=excluded.uid,
                in_reply_to=excluded.in_reply_to,
                references_json=excluded.references_json,
                thread_identity=excluded.thread_identity,
                updated_at=excluded.updated_at
            """,
            (
                locator.account_id,
                classification.stable_message_identity,
                locator.folder,
                locator.uidvalidity,
                locator.uid,
                locator.rfc_message_id or "",
                normalized_in_reply_to,
                _json_dump(list(normalized_references)),
                locator.thread_id or "",
                sender,
                _json_dump(list(recipients)),
                subject,
                normalized_text,
                preview,
                _json_dump(
                    [item.model_dump(mode="json") for item in attachment_metadata]
                ),
                received_at,
                now,
                now,
            ),
        )

    def _persist_action_plan(
        self,
        db: sqlite3.Connection,
        plan: EmailActionPlan,
        *,
        now: str,
    ) -> None:
        existing_by_id = db.execute(
            "select * from email_action_plans where action_plan_id=?",
            (plan.action_plan_id,),
        ).fetchone()
        encoded_actions = _json_dump([action.value for action in plan.actions])
        encoded_parameters = _json_dump(
            {
                action.value: dict(parameters)
                for action, parameters in plan.action_parameters.items()
            }
        )
        encoded_authorizations = (
            None
            if plan.authorization_snapshot_format == "legacy_unavailable_v1"
            else _json_dump(
                [
                    authorization.model_dump(mode="json")
                    for authorization in plan.action_authorizations
                ]
            )
        )
        expected = (
            plan.action_plan_version,
            plan.classification_id,
            plan.account_id,
            plan.category,
            plan.classification_source,
            plan.confidence,
            plan.model_id,
            plan.config_version,
            encoded_actions,
            encoded_parameters,
            encoded_authorizations,
            plan.created_at.isoformat(),
        )
        if existing_by_id is not None:
            actual = (
                existing_by_id["action_plan_version"],
                existing_by_id["classification_id"],
                existing_by_id["account_id"],
                existing_by_id["category"],
                existing_by_id["classification_source"],
                existing_by_id["confidence"],
                existing_by_id["model_id"],
                existing_by_id["config_version"],
                existing_by_id["actions_json"],
                existing_by_id["action_parameters_json"],
                existing_by_id["authorization_snapshot_json"],
                existing_by_id["created_at"],
            )
            if actual != expected:
                raise EmailActionPlanConflict(
                    f"ActionPlan ID {plan.action_plan_id} has different immutable fields"
                )
            self._ensure_direct_action_rows(db, plan, now=now)
            return
        version_row = db.execute(
            """
            select action_plan_id from email_action_plans
            where classification_id=? and action_plan_version=?
            """,
            (plan.classification_id, plan.action_plan_version),
        ).fetchone()
        if version_row is not None:
            raise EmailActionPlanConflict(
                f"ActionPlan version {plan.action_plan_version} already exists for "
                f"classification {plan.classification_id}"
            )
        latest = db.execute(
            "select max(action_plan_version) from email_action_plans where classification_id=?",
            (plan.classification_id,),
        ).fetchone()[0]
        expected_version = 1 if latest is None else int(latest) + 1
        if plan.action_plan_version != expected_version:
            raise EmailActionPlanConflict(
                f"ActionPlan version must be {expected_version} for classification "
                f"{plan.classification_id}"
            )
        db.execute(
            """
            insert into email_action_plans (
                action_plan_id, action_plan_version, classification_id,
                account_id, category, classification_source, confidence,
                model_id, config_version, actions_json, action_parameters_json,
                authorization_snapshot_json, legacy_serialization_pre_v16, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.action_plan_id,
                plan.action_plan_version,
                plan.classification_id,
                plan.account_id,
                plan.category,
                plan.classification_source,
                plan.confidence,
                plan.model_id,
                plan.config_version,
                encoded_actions,
                encoded_parameters,
                encoded_authorizations,
                0,
                plan.created_at.isoformat(),
            ),
        )
        self._ensure_direct_action_rows(db, plan, now=now)

    @staticmethod
    def _ensure_direct_action_rows(
        db: sqlite3.Connection,
        plan: EmailActionPlan,
        *,
        now: str,
    ) -> None:
        for action in plan.direct_actions:
            parameters = dict(plan.action_parameters.get(action, {}))
            db.execute(
                """
                insert into email_actions (
                    action_id, action_plan_id, classification_id, account_id,
                    action_type, parameters_json, config_version, status,
                    created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                on conflict(action_plan_id, action_type) do nothing
                """,
                (
                    _direct_action_id(plan.action_plan_id, action),
                    plan.action_plan_id,
                    plan.classification_id,
                    plan.account_id,
                    action.value,
                    _json_dump(parameters),
                    plan.config_version,
                    now,
                    now,
                ),
            )

    @staticmethod
    def _advance_cursor(
        db: sqlite3.Connection,
        *,
        account_id: str,
        folder: str,
        uidvalidity: int,
        last_seen_uid: int,
        last_success_at: str,
        last_error: str,
        expected_uidvalidity: int | None,
    ) -> None:
        _require_positive_int(uidvalidity, field="cursor_uidvalidity")
        if expected_uidvalidity is not None:
            _require_positive_int(
                expected_uidvalidity,
                field="expected_cursor_uidvalidity",
            )
        if (
            isinstance(last_seen_uid, bool)
            or not isinstance(last_seen_uid, int)
            or last_seen_uid < 0
        ):
            raise ValueError("cursor_last_seen_uid must be a non-negative integer")
        current = db.execute(
            """
            select uidvalidity, last_seen_uid from email_scan_cursors
            where account_id=? and folder=?
            """,
            (account_id, folder),
        ).fetchone()
        if current is None:
            if expected_uidvalidity is not None:
                raise EmailCursorConflict(
                    f"cursor generation expected {expected_uidvalidity}, but no cursor exists"
                )
            db.execute(
                """
                insert into email_scan_cursors (
                    account_id, folder, uidvalidity, last_seen_uid,
                    last_success_at, last_error
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    folder,
                    uidvalidity,
                    last_seen_uid,
                    last_success_at,
                    last_error,
                ),
            )
            return
        current_uidvalidity = int(current["uidvalidity"])
        if (
            expected_uidvalidity is not None
            and current_uidvalidity != expected_uidvalidity
        ):
            raise EmailCursorConflict(
                f"cursor generation conflict: expected {expected_uidvalidity}, "
                f"found {current_uidvalidity}"
            )
        if current_uidvalidity == uidvalidity:
            db.execute(
                """
                update email_scan_cursors
                set last_seen_uid=max(last_seen_uid, ?),
                    last_success_at=?, last_error=?
                where account_id=? and folder=? and uidvalidity=?
                """,
                (
                    last_seen_uid,
                    last_success_at,
                    last_error,
                    account_id,
                    folder,
                    uidvalidity,
                ),
            )
            return
        if expected_uidvalidity is None:
            raise EmailCursorConflict(
                "cursor generation change requires expected_cursor_uidvalidity; "
                f"current generation is {current_uidvalidity}"
            )
        updated = db.execute(
            """
            update email_scan_cursors
            set uidvalidity=?, last_seen_uid=?, last_success_at=?, last_error=?
            where account_id=? and folder=? and uidvalidity=?
            """,
            (
                uidvalidity,
                last_seen_uid,
                last_success_at,
                last_error,
                account_id,
                folder,
                expected_uidvalidity,
            ),
        ).rowcount
        if updated != 1:
            raise EmailCursorConflict(
                f"cursor generation changed while replacing {expected_uidvalidity}"
            )

    def persist_scan_result(
        self,
        classification: EmailClassification,
        *,
        agent_result: object | None = None,
        sender: str = "",
        recipients: Sequence[str] = (),
        subject: str = "",
        normalized_text: str = "",
        preview: str = "",
        attachment_metadata: Sequence[EmailAttachmentMetadata] = (),
        in_reply_to: str = "",
        references: Sequence[str] = (),
        received_at: str = "",
        model_text: str = "",
        cursor_uidvalidity: int | None = None,
        cursor_last_seen_uid: int | None = None,
        cursor_last_success_at: str = "",
        cursor_last_error: str = "",
        expected_cursor_uidvalidity: int | None = None,
    ) -> dict[str, Any]:
        """Atomically persist scan state and advance its folder cursor last."""

        _validate_model_text(model_text)
        agent_result_json = _validated_agent_result_json(classification, agent_result)
        now = self._now()
        locator = classification.provider_locator
        if (cursor_uidvalidity is None) != (cursor_last_seen_uid is None):
            raise ValueError(
                "cursor UIDVALIDITY and last UID must be supplied together"
            )
        if expected_cursor_uidvalidity is not None and cursor_uidvalidity is None:
            raise ValueError(
                "expected cursor generation requires cursor UIDVALIDITY and last UID"
            )
        if cursor_uidvalidity is not None and cursor_uidvalidity != locator.uidvalidity:
            raise ValueError("cursor UIDVALIDITY must match the message locator")
        if cursor_last_seen_uid is not None and cursor_last_seen_uid < locator.uid:
            raise ValueError("cursor cannot advance behind the persisted message UID")
        with self._connect() as db:
            db.execute("begin immediate")
            existing = self._check_classification_identity(db, classification)
            self._upsert_message(
                db,
                classification,
                sender=sender,
                recipients=recipients,
                subject=subject,
                normalized_text=normalized_text,
                preview=preview,
                attachment_metadata=attachment_metadata,
                in_reply_to=in_reply_to,
                references=references,
                received_at=received_at,
                now=now,
            )
            if existing is None:
                confirmed_category = (
                    classification.category
                    if classification.status is EmailClassificationStatus.PROCESSED
                    else None
                )
                db.execute(
                    """
                    insert into email_classifications (
                        id, account_id, folder, uidvalidity, uid, rfc_message_id,
                        thread_id, stable_message_identity, sender, subject, preview,
                        model_text, received_at, category, predicted_category,
                        confirmed_category, confidence, margin, probabilities_json,
                        model_id, config_version, status, classification_source,
                        agent_result_json, action_plan_json,
                        current_action_plan_id, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'null', null, ?)
                    """,
                    (
                        classification.classification_id,
                        locator.account_id,
                        locator.folder,
                        locator.uidvalidity,
                        locator.uid,
                        locator.rfc_message_id,
                        locator.thread_id,
                        classification.stable_message_identity,
                        sender,
                        subject,
                        preview,
                        model_text,
                        received_at,
                        classification.category,
                        classification.category,
                        confirmed_category,
                        classification.confidence,
                        classification.margin,
                        _json_dump(classification.probabilities),
                        classification.model_id,
                        classification.config_version,
                        classification.status.value,
                        classification.classification_source,
                        agent_result_json,
                        now,
                    ),
                )
                if classification.action_plan is not None:
                    self._persist_action_plan(db, classification.action_plan, now=now)
                    db.execute(
                        """
                        update email_classifications
                        set action_plan_json=?, current_action_plan_id=?,
                            legacy_processed_without_plan=0
                        where id=?
                        """,
                        (
                            classification.action_plan.model_dump_json(),
                            classification.action_plan.action_plan_id,
                            classification.classification_id,
                        ),
                    )
            else:
                if (
                    existing["classification_source"] == "agent"
                    and existing["agent_result_json"] != agent_result_json
                ):
                    raise EmailClassificationConflict(
                        "canonical Agent result diverges from retry"
                    )
                db.execute(
                    """
                    update email_classifications
                    set folder=?, uidvalidity=?, uid=?, thread_id=?, updated_at=?
                    where id=?
                    """,
                    (
                        locator.folder,
                        locator.uidvalidity,
                        locator.uid,
                        locator.thread_id,
                        now,
                        classification.classification_id,
                    ),
                )
            if cursor_uidvalidity is not None and cursor_last_seen_uid is not None:
                self._advance_cursor(
                    db,
                    account_id=locator.account_id,
                    folder=locator.folder,
                    uidvalidity=cursor_uidvalidity,
                    last_seen_uid=cursor_last_seen_uid,
                    last_success_at=cursor_last_success_at,
                    last_error=cursor_last_error,
                    expected_uidvalidity=expected_cursor_uidvalidity,
                )
            row = db.execute(
                "select * from email_classifications where id=?",
                (classification.classification_id,),
            ).fetchone()
        assert row is not None
        return self._classification_row(row)

    def persist_empty_scan_cursor(
        self,
        *,
        account_id: str,
        folder: str,
        uidvalidity: int,
        last_success_at: str,
        expected_cursor_uidvalidity: int | None = None,
    ) -> dict[str, Any]:
        """Commit a successful empty readonly scan without inventing a message."""

        account_id = account_id.strip()
        folder = folder.strip()
        if not account_id or not folder:
            raise ValueError("account_id and folder must be non-empty")
        with self._connect() as db:
            db.execute("begin immediate")
            self._advance_cursor(
                db,
                account_id=account_id,
                folder=folder,
                uidvalidity=uidvalidity,
                last_seen_uid=0,
                last_success_at=last_success_at,
                last_error="",
                expected_uidvalidity=expected_cursor_uidvalidity,
            )
            row = db.execute(
                """
                select * from email_scan_cursors
                where account_id=? and folder=?
                """,
                (account_id, folder),
            ).fetchone()
        assert row is not None
        return dict(row)

    def record_historical_classification_outcome(
        self, outcome: object
    ) -> dict[str, Any]:
        """Append one redacted model decision/action outcome for a manual history run."""

        stable_identity = str(
            getattr(outcome, "stable_message_identity", "") or ""
        ).strip()
        model_id = str(getattr(outcome, "model_id", "") or "").strip()
        action_outcome = str(getattr(outcome, "action_outcome", "") or "").strip()
        if not stable_identity or not model_id or not action_outcome:
            raise ValueError("historical outcome identity is incomplete")
        predicted_value = getattr(outcome, "predicted_category", None)
        predicted_category = (
            None
            if predicted_value is None
            else validate_email_category_key(predicted_value)
        )
        threshold = _optional_probability(
            getattr(outcome, "threshold", None), field="threshold"
        )
        probability = _optional_probability(
            getattr(outcome, "probability", None), field="probability"
        )
        important_value = getattr(outcome, "important", None)
        if important_value is not None and type(important_value) is not bool:
            raise TypeError("important must be a strict bool or None")
        identity_payload = _json_dump(
            {
                "stable_message_identity": stable_identity,
                "model_id": model_id,
                "predicted_category": predicted_category,
                "threshold": threshold,
                "probability": probability,
                "important": important_value,
                "action_outcome": action_outcome,
            }
        )
        event_id = "email-historical:" + sha256(
            identity_payload.encode("utf-8")
        ).hexdigest()
        created_at = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            db.execute(
                """
                insert or ignore into email_historical_classification_history (
                    event_id, stable_message_identity, model_id,
                    predicted_category, threshold, probability, important,
                    action_outcome, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    stable_identity,
                    model_id,
                    predicted_category,
                    threshold,
                    probability,
                    None if important_value is None else int(important_value),
                    action_outcome,
                    created_at,
                ),
            )
            row = db.execute(
                "select * from email_historical_classification_history "
                "where event_id=?",
                (event_id,),
            ).fetchone()
        assert row is not None
        return _historical_outcome_row(row)

    def historical_traversal_cursor(
        self, *, account_id: str, folder: str, model_id: str
    ) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "select * from email_historical_traversals "
                "where account_id=? and folder=? and model_id=?",
                (account_id, folder, model_id),
            ).fetchone()
        return {
            "uidvalidity": None if row is None else row["uidvalidity"],
            "last_seen_uid": 0 if row is None else int(row["last_seen_uid"]),
        }

    def enqueue_historical_page(
        self,
        *,
        account_id: str,
        folder: str,
        model_id: str,
        uidvalidity: int,
        last_seen_uid: int,
        candidates: Sequence[object],
    ) -> None:
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            for candidate in candidates:
                message = getattr(candidate, "provider_message", None)
                if not isinstance(message, Mapping):
                    raise ValueError("historical queued candidate message is missing")
                projection = _historical_candidate_projection(
                    message,
                    stable_message_identity=str(candidate.stable_message_identity),
                    account_id=account_id,
                    folder=folder,
                )
                normalized_input_hash = sha256(
                    str(candidate.normalized_text).encode("utf-8")
                ).hexdigest()
                db.execute(
                    """
                    insert into email_historical_candidates (
                        account_id, folder, model_id, stable_message_identity,
                        normalized_text, provider_message_json, uidvalidity, uid,
                        state, reason, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?)
                    on conflict(account_id, folder, model_id, stable_message_identity)
                    do update set normalized_text=excluded.normalized_text,
                        provider_message_json=excluded.provider_message_json,
                        uidvalidity=excluded.uidvalidity, uid=excluded.uid,
                        updated_at=excluded.updated_at
                    """,
                    (
                        account_id,
                        folder,
                        model_id,
                        candidate.stable_message_identity,
                        normalized_input_hash,
                        _json_dump(projection),
                        int(message.get("uidValidity") or uidvalidity),
                        int(message.get("uid") or 0),
                        now,
                    ),
                )
            db.execute(
                """
                insert into email_historical_traversals (
                    account_id, folder, model_id, uidvalidity, last_seen_uid, updated_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(account_id, folder, model_id) do update set
                    uidvalidity=excluded.uidvalidity,
                    last_seen_uid=case
                        when email_historical_traversals.uidvalidity=excluded.uidvalidity
                        then max(email_historical_traversals.last_seen_uid, excluded.last_seen_uid)
                        else excluded.last_seen_uid end,
                    updated_at=excluded.updated_at
                """,
                (account_id, folder, model_id, uidvalidity, last_seen_uid, now),
            )

    def list_historical_candidates(
        self, *, account_id: str, folder: str, model_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("historical candidate limit must be positive")
        now = self._now()
        deferred_quota = min(2, limit)
        with self._connect() as db:
            deferred_rows = db.execute(
                """
                select * from email_historical_candidates
                where account_id=? and folder=? and model_id=?
                  and state='deferred'
                  and next_retry_at != '' and next_retry_at <= ?
                order by
                    case when attempted_at='' then updated_at else attempted_at end,
                    uid, stable_message_identity
                limit ?
                """,
                (account_id, folder, model_id, now, limit),
            ).fetchall()
            reserved = list(deferred_rows[:deferred_quota])
            pending_rows = db.execute(
                """
                select * from email_historical_candidates
                where account_id=? and folder=? and model_id=? and state='pending'
                order by uid, stable_message_identity
                limit ?
                """,
                (account_id, folder, model_id, limit - len(reserved)),
            ).fetchall()
            rows = reserved + list(pending_rows)
            if len(rows) < limit:
                rows.extend(deferred_rows[len(reserved) : limit - len(rows) + len(reserved)])
        return [
            {
                **dict(row),
                "normalized_input_hash": row["normalized_text"],
                "provider_message": _json_load(
                    row["provider_message_json"],
                    field="provider_message_json",
                    expected_type=dict,
                ),
            }
            for row in rows
        ]

    def set_historical_candidate_state(
        self,
        *,
        account_id: str,
        folder: str,
        model_id: str,
        stable_message_identity: str,
        state: str,
        reason: str,
    ) -> None:
        if state not in {"pending", "deferred", "terminal"}:
            raise ValueError("historical candidate state is invalid")
        now = self._now()
        next_retry_at = ""
        attempted_at = ""
        if state == "deferred":
            attempted_at = now
            next_retry_at = (
                datetime.fromisoformat(now)
                + timedelta(seconds=HISTORICAL_DEFER_RETRY_SECONDS)
            ).isoformat(timespec="seconds")
        with self._connect() as db:
            updated = db.execute(
                """
                update email_historical_candidates
                set state=?, reason=?, attempted_at=?, next_retry_at=?, updated_at=?
                where account_id=? and folder=? and model_id=?
                  and stable_message_identity=?
                """,
                (
                    state,
                    reason,
                    attempted_at,
                    next_retry_at,
                    now,
                    account_id,
                    folder,
                    model_id,
                    stable_message_identity,
                ),
            ).rowcount
        if updated != 1:
            raise ValueError("historical queued candidate is missing")

    def list_historical_classification_history(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "select * from email_historical_classification_history "
                "order by created_at, event_id"
            ).fetchall()
        return [_historical_outcome_row(row) for row in rows]

    def begin_historical_operation(
        self,
        *,
        account_id: str,
        stable_message_identity: str,
        model_id: str,
        classification_id: int,
        action_plan_id: str,
        action_ids: Sequence[str],
        predicted_category: str,
        threshold: float,
        probability: float,
        important: bool,
    ) -> dict[str, Any]:
        identity = f"{model_id}\0{stable_message_identity}"
        operation_id = "email-historical-operation:" + sha256(
            identity.encode("utf-8")
        ).hexdigest()
        now = self._now()
        immutable = (
            account_id,
            stable_message_identity,
            model_id,
            classification_id,
            action_plan_id,
            _json_dump(list(action_ids)),
            predicted_category,
            float(threshold),
            float(probability),
            int(important),
        )
        with self._connect() as db:
            db.execute("begin immediate")
            db.execute(
                """
                insert or ignore into email_historical_operations (
                    operation_id, account_id, stable_message_identity, model_id,
                    classification_id, action_plan_id, action_ids_json,
                    predicted_category, threshold, probability, important,
                    status, action_outcome, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', '', ?, ?)
                """,
                (operation_id, *immutable, now, now),
            )
            row = db.execute(
                "select * from email_historical_operations where operation_id=?",
                (operation_id,),
            ).fetchone()
        assert row is not None
        observed = (
            row["account_id"], row["stable_message_identity"], row["model_id"],
            int(row["classification_id"]), row["action_plan_id"], row["action_ids_json"],
            row["predicted_category"], float(row["threshold"]),
            float(row["probability"]), int(row["important"]),
        )
        if observed != immutable:
            raise EmailPersistenceCorruption("historical operation identity conflict")
        return self._historical_operation_row(row)

    def list_processing_historical_operations(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "select * from email_historical_operations where status='processing' "
                "order by created_at, operation_id"
            ).fetchall()
        return [self._historical_operation_row(row) for row in rows]

    def complete_historical_operation(self, outcome: object) -> None:
        stable_identity = str(getattr(outcome, "stable_message_identity"))
        model_id = str(getattr(outcome, "model_id"))
        with self._connect() as db:
            db.execute(
                """
                update email_historical_operations
                set status='terminal', action_outcome=?, updated_at=?
                where model_id=? and stable_message_identity=? and status='processing'
                """,
                (
                    str(getattr(outcome, "action_outcome")),
                    self._now(), model_id, stable_identity,
                ),
            )

    def touch_historical_operation(
        self, *, model_id: str, stable_message_identity: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update email_historical_operations set updated_at=?
                where model_id=? and stable_message_identity=? and status='processing'
                """,
                (self._now(), model_id, stable_message_identity),
            )

    @staticmethod
    def _historical_operation_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            **dict(row),
            "action_ids": tuple(
                _json_load(row["action_ids_json"], field="action_ids_json", expected_type=list)
            ),
            "important": bool(row["important"]),
        }

    def upsert_classification(
        self,
        classification: EmailClassification,
        *,
        sender: str = "",
        subject: str = "",
        preview: str = "",
        model_text: str = "",
        received_at: str = "",
    ) -> dict[str, Any]:
        """Compatibility entrypoint for prototype callers without cursor state."""

        return self.persist_scan_result(
            classification,
            sender=sender,
            subject=subject,
            normalized_text=model_text,
            preview=preview,
            model_text=model_text,
            received_at=received_at,
        )

    def get_scan_cursor(self, account_id: str, folder: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from email_scan_cursors where account_id=? and folder=?",
                (account_id, folder),
            ).fetchone()
        return None if row is None else dict(row)

    def record_scan_cursor(
        self,
        *,
        account_id: str,
        folder: str,
        uidvalidity: int,
        last_seen_uid: int,
        expected_uidvalidity: int | None = None,
    ) -> dict[str, Any]:
        """Advance an observation-only scan after durable task enqueue."""

        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            self._advance_cursor(
                db,
                account_id=account_id,
                folder=folder,
                uidvalidity=uidvalidity,
                last_seen_uid=last_seen_uid,
                last_success_at=now,
                last_error="",
                expected_uidvalidity=expected_uidvalidity,
            )
            row = db.execute(
                "select * from email_scan_cursors where account_id=? and folder=?",
                (account_id, folder),
            ).fetchone()
        assert row is not None
        return dict(row)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "select * from email_accounts order by account_id"
            ).fetchall()
        return [self._account_row(row) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from email_accounts where account_id=?",
                (account_id,),
            ).fetchone()
        return None if row is None else self._account_row(row)

    def get_email_reply_delivery_authorization(
        self,
        classification_id: int,
        action_plan_id: str,
    ) -> dict[str, Any] | None:
        """Read one immutable auto-reply authorization and current-plan relation."""

        with self._connect() as db:
            row = db.execute(
                """
                select
                    classifications.id as classification_id,
                    classifications.account_id as account_id,
                    classifications.stable_message_identity
                        as stable_message_identity,
                    classifications.current_action_plan_id
                        as current_action_plan_id,
                    messages.thread_identity as thread_identity,
                    plans.action_plan_id as action_plan_id,
                    plans.action_plan_version as action_plan_version,
                    plans.actions_json as actions_json
                from email_classifications as classifications
                join email_messages as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                join email_action_plans as plans
                  on plans.classification_id=classifications.id
                 and plans.action_plan_id=?
                where classifications.id=?
                  and classifications.status='processed'
                """,
                (action_plan_id, classification_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "thread_identity": row["thread_identity"],
            "current_action_plan_id": row["current_action_plan_id"],
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "actions": _json_load(
                row["actions_json"],
                field="actions_json",
                expected_type=list,
            ),
        }

    def get_email_reply_receipt(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from email_reply_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_reply_receipt_row(row)

    def claim_email_reply_dispatch(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        outgoing_message_id: str,
        sender: str,
        thread_identity: str,
        expected_account_updated_at: str,
        owner: Mapping[str, object],
    ) -> dict[str, Any] | None:
        """Atomically bind the complete current dispatch authorization to an owner."""

        claimed_at = self._now()
        owner = _validate_email_reply_owner(owner)
        immutable = {
            "action_identity": action_identity,
            "effect_digest": effect_digest,
            "action_plan_id": action_plan_id,
            "classification_id": classification_id,
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "outgoing_message_id": outgoing_message_id,
            "sender": sender,
            "thread_identity": thread_identity,
        }
        if (
            any(
                not isinstance(value, str) or not value or value != value.strip()
                for key, value in immutable.items()
                if key != "classification_id"
            )
            or classification_id <= 0
        ):
            raise ValueError("email reply dispatch identity is invalid")
        if (
            not expected_account_updated_at
            or expected_account_updated_at != expected_account_updated_at.strip()
        ):
            raise ValueError("expected_account_updated_at must be non-empty")
        with self._connect() as db:
            db.execute("begin immediate")
            authorization = db.execute(
                """
                select plans.actions_json, plans.action_plan_version,
                       messages.thread_identity
                from email_classifications as classifications
                join email_action_plans as plans
                  on plans.action_plan_id=classifications.current_action_plan_id
                 and plans.classification_id=classifications.id
                join email_messages as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where classifications.id=?
                  and classifications.status='processed'
                  and classifications.current_action_plan_id=?
                  and classifications.account_id=?
                  and classifications.stable_message_identity=?
                """,
                (
                    classification_id,
                    action_plan_id,
                    account_id,
                    stable_message_identity,
                ),
            ).fetchone()
            account = db.execute(
                "select * from email_accounts where account_id=?",
                (account_id,),
            ).fetchone()
            if (
                authorization is None
                or action_identity
                != email_action_identity(
                    account_id=account_id,
                    stable_message_identity=stable_message_identity,
                    action_type=EmailAction.AUTO_REPLY,
                    action_plan_version=int(authorization["action_plan_version"]),
                )
                or authorization["thread_identity"] != thread_identity
                or EmailAction.AUTO_REPLY.value
                not in _json_load(
                    authorization["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
                or account is None
                or not account["enabled"]
                or account["email_address"] != sender
                or account["updated_at"] != expected_account_updated_at
            ):
                return None
            account_snapshot = self._account_row(account)
            account_snapshot_json = _json_dump(account_snapshot)
            receipt = db.execute(
                "select 1 from email_reply_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
            if receipt is not None:
                return {**immutable, "status": "done", "acquired": False}
            existing = db.execute(
                "select * from email_reply_dispatch_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            if existing is not None:
                claim = self._email_reply_dispatch_claim_row(existing)
                if any(claim[key] != value for key, value in immutable.items()):
                    raise EmailReplyDispatchConflict(
                        "email reply action identity is bound to another dispatch"
                    )
                if claim["status"] != "retryable":
                    return {**claim, "acquired": False}
                updated = db.execute(
                    """
                    update email_reply_dispatch_claims
                    set owner_id=?, owner_generation=?, lease_token=?,
                        account_updated_at=?, account_snapshot_json=?,
                        status='dispatching', claimed_at=?, updated_at=?
                    where action_identity=? and status='retryable'
                      and owner_id=? and owner_generation=? and lease_token=?
                    """,
                    (
                        owner["owner_id"],
                        owner["generation"],
                        owner["lease_token"],
                        expected_account_updated_at,
                        account_snapshot_json,
                        claimed_at,
                        claimed_at,
                        action_identity,
                        claim["owner_id"],
                        claim["owner_generation"],
                        claim["lease_token"],
                    ),
                ).rowcount
                if updated != 1:
                    raise EmailReplyDispatchConflict(
                        "email reply retryable claim changed concurrently"
                    )
                row = db.execute(
                    "select * from email_reply_dispatch_claims where action_identity=?",
                    (action_identity,),
                ).fetchone()
                assert row is not None
                return {**self._email_reply_dispatch_claim_row(row), "acquired": True}
            db.execute(
                """
                insert into email_reply_dispatch_claims (
                    action_identity, effect_digest, action_plan_id,
                    classification_id, account_id, stable_message_identity,
                    outgoing_message_id, owner_id, owner_generation, lease_token,
                    sender, thread_identity, account_updated_at,
                    account_snapshot_json, status, claimed_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'dispatching', ?, ?)
                """,
                (
                    action_identity,
                    effect_digest,
                    action_plan_id,
                    classification_id,
                    account_id,
                    stable_message_identity,
                    outgoing_message_id,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                    sender,
                    thread_identity,
                    expected_account_updated_at,
                    account_snapshot_json,
                    claimed_at,
                    claimed_at,
                ),
            )
            row = db.execute(
                "select * from email_reply_dispatch_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            return {**self._email_reply_dispatch_claim_row(row), "acquired": True}

    def get_email_reply_dispatch_claim(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from email_reply_dispatch_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_reply_dispatch_claim_row(row)

    def mark_email_reply_dispatch_uncertain(
        self,
        action_identity: str,
        *,
        owner: Mapping[str, object],
    ) -> None:
        """CAS one owned possible dispatch into reconciliation-only state."""

        owner = _validate_email_reply_owner(owner)
        with self._connect() as db:
            db.execute("begin immediate")
            updated = db.execute(
                """
                update email_reply_dispatch_claims
                set status='uncertain', updated_at=?
                where action_identity=? and status='dispatching'
                  and owner_id=? and owner_generation=? and lease_token=?
                """,
                (
                    self._now(),
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailReplyDispatchConflict(
                    "email reply dispatch owner fence changed"
                )

    def release_email_reply_dispatch_retryable(
        self,
        action_identity: str,
        *,
        owner: Mapping[str, object],
    ) -> None:
        """Release only an explicitly non-dispatched owned claim for retry."""

        owner = _validate_email_reply_owner(owner)
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                """
                select claims.*, classifications.current_action_plan_id,
                       classifications.status as classification_status,
                       plans.actions_json, accounts.enabled,
                       accounts.email_address, accounts.updated_at as live_account_updated_at,
                       messages.thread_identity as live_thread_identity
                from email_reply_dispatch_claims as claims
                join email_classifications as classifications
                  on classifications.id=claims.classification_id
                join email_action_plans as plans
                  on plans.action_plan_id=claims.action_plan_id
                join email_accounts as accounts
                  on accounts.account_id=claims.account_id
                join email_messages as messages
                  on messages.account_id=claims.account_id
                 and messages.stable_message_identity=claims.stable_message_identity
                where claims.action_identity=? and claims.status='dispatching'
                  and claims.owner_id=? and claims.owner_generation=?
                  and claims.lease_token=?
                """,
                (
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).fetchone()
            if (
                claim is None
                or claim["classification_status"] != "processed"
                or claim["current_action_plan_id"] != claim["action_plan_id"]
                or EmailAction.AUTO_REPLY.value
                not in _json_load(
                    claim["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
                or not claim["enabled"]
                or claim["email_address"] != claim["sender"]
                or claim["live_account_updated_at"] != claim["account_updated_at"]
                or claim["live_thread_identity"] != claim["thread_identity"]
            ):
                raise EmailReplyDispatchConflict(
                    "email reply retry authorization changed"
                )
            updated = db.execute(
                """
                update email_reply_dispatch_claims
                set status='retryable', updated_at=?
                where action_identity=? and status='dispatching'
                  and owner_id=? and owner_generation=? and lease_token=?
                """,
                (
                    self._now(),
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailReplyDispatchConflict(
                    "email reply dispatch owner fence changed"
                )

    def recover_terminated_email_reply_dispatch_claims(
        self,
        *,
        owner: Mapping[str, object],
        termination_verifier: Callable[[Mapping[str, object]], bool],
        recovered_at: str,
    ) -> int:
        """Recover an owner only after the caller proves that generation terminated."""

        owner = _validate_email_reply_owner(owner)
        if not termination_verifier(owner):
            raise EmailReplyDispatchConflict("owner_termination_not_proven")
        recovered_at = _required_utc_timestamp(recovered_at, field="recovered_at")
        with self._connect() as db:
            db.execute("begin immediate")
            updated = db.execute(
                """
                update email_reply_dispatch_claims
                set status='uncertain', updated_at=?
                where status='dispatching' and owner_id=?
                  and owner_generation=? and lease_token=?
                """,
                (
                    recovered_at,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            return updated

    @staticmethod
    def _email_reply_dispatch_claim_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "outgoing_message_id": row["outgoing_message_id"],
            "owner_id": row["owner_id"],
            "owner_generation": row["owner_generation"],
            "lease_token": row["lease_token"],
            "sender": row["sender"],
            "thread_identity": row["thread_identity"],
            "account_updated_at": row["account_updated_at"],
            "account_snapshot": _json_load(
                row["account_snapshot_json"],
                field="account_snapshot_json",
                expected_type=dict,
            ),
            "status": row["status"],
            "claimed_at": row["claimed_at"],
            "updated_at": row["updated_at"],
        }

    def persist_email_reply_receipt(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        outgoing_message_id: str,
        provider_operation: str,
        provider_target: str,
        provider_result_id: str,
        sent_folder: str,
        sent_uidvalidity: int,
        sent_uid: int,
        smtp_acceptance_id: str,
        claim_owner: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist/replay Sent evidence and complete its matching claim."""

        text_fields = {
            "action_identity": action_identity,
            "effect_digest": effect_digest,
            "action_plan_id": action_plan_id,
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "outgoing_message_id": outgoing_message_id,
            "provider_operation": provider_operation,
            "provider_target": provider_target,
            "provider_result_id": provider_result_id,
        }
        if any(
            not isinstance(value, str) or not value or value != value.strip()
            for value in text_fields.values()
        ):
            raise ValueError("email reply receipt fields must be non-empty")
        if provider_operation not in {
            "sent_readback",
            "sent_equivalent_readback",
        }:
            raise ValueError("provider_operation is not an approved receipt operation")
        _validate_opaque_provider_identifier(
            outgoing_message_id,
            field="outgoing_message_id",
        )
        _validate_opaque_provider_identifier(provider_target, field="provider_target")
        _validate_opaque_provider_identifier(
            provider_result_id,
            field="provider_result_id",
        )
        _validate_provider_location(sent_folder, field="sent_folder")
        if classification_id <= 0 or sent_uidvalidity <= 0 or sent_uid <= 0:
            raise ValueError("email reply receipt numeric fields must be positive")
        _validate_opaque_provider_identifier(
            smtp_acceptance_id,
            field="smtp_acceptance_id",
            allow_empty=True,
        )
        requested_provider_receipt = {
            "sent_folder": sent_folder,
            "sent_uidvalidity": sent_uidvalidity,
            "sent_uid": sent_uid,
            "smtp_acceptance_id": smtp_acceptance_id,
        }
        assert_no_credentials(
            {
                "provider_operation": provider_operation,
                "provider_target": provider_target,
                "provider_result_id": provider_result_id,
                "provider_receipt": requested_provider_receipt,
            }
        )
        validated_claim_owner = (
            None if claim_owner is None else _validate_email_reply_owner(claim_owner)
        )
        created_at = self._now()
        receipt_base = {
            **text_fields,
            "classification_id": classification_id,
            "display_excerpt": "Automatic email reply verified in Sent.",
        }
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_reply_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
            if row is None:
                db.execute(
                    """
                    insert into email_reply_receipts (
                        action_identity, effect_digest, action_plan_id,
                        classification_id, account_id, stable_message_identity,
                        outgoing_message_id, provider_operation, provider_target,
                        provider_result_id, provider_receipt_json, display_excerpt,
                        created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_identity,
                        effect_digest,
                        action_plan_id,
                        classification_id,
                        account_id,
                        stable_message_identity,
                        outgoing_message_id,
                        provider_operation,
                        provider_target,
                        provider_result_id,
                        _json_dump(requested_provider_receipt),
                        receipt_base["display_excerpt"],
                        created_at,
                    ),
                )
            else:
                persisted = self._email_reply_receipt_row(row)
                comparable = {
                    key: value
                    for key, value in persisted.items()
                    if key not in {"created_at", "provider_receipt"}
                }
                if comparable != receipt_base:
                    raise EmailReplyReceiptConflict(
                        "email reply identity is bound to different provider evidence"
                    )
                persisted_receipt = persisted["provider_receipt"]
                persisted_without_smtp = {
                    key: value
                    for key, value in persisted_receipt.items()
                    if key != "smtp_acceptance_id"
                }
                requested_without_smtp = {
                    key: value
                    for key, value in requested_provider_receipt.items()
                    if key != "smtp_acceptance_id"
                }
                current_smtp_id = persisted_receipt["smtp_acceptance_id"]
                requested_smtp_id = requested_provider_receipt["smtp_acceptance_id"]
                if persisted_without_smtp != requested_without_smtp or (
                    current_smtp_id
                    and requested_smtp_id
                    and current_smtp_id != requested_smtp_id
                ):
                    raise EmailReplyReceiptConflict(
                        "email reply identity is bound to different provider evidence"
                    )
                if not current_smtp_id and requested_smtp_id:
                    enriched_receipt = {
                        **persisted_receipt,
                        "smtp_acceptance_id": requested_smtp_id,
                    }
                    updated = db.execute(
                        """
                        update email_reply_receipts set provider_receipt_json=?
                        where action_identity=? and provider_receipt_json=?
                        """,
                        (
                            _json_dump(enriched_receipt),
                            action_identity,
                            _json_dump(persisted_receipt),
                        ),
                    ).rowcount
                    if updated != 1:
                        raise EmailReplyReceiptConflict(
                            "email reply receipt changed concurrently"
                        )
            claim = db.execute(
                "select * from email_reply_dispatch_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            if claim is not None:
                claim_row = self._email_reply_dispatch_claim_row(claim)
                claim_expected = {
                    "effect_digest": effect_digest,
                    "action_plan_id": action_plan_id,
                    "classification_id": classification_id,
                    "account_id": account_id,
                    "stable_message_identity": stable_message_identity,
                    "outgoing_message_id": outgoing_message_id,
                }
                if any(
                    claim_row[key] != value for key, value in claim_expected.items()
                ):
                    raise EmailReplyDispatchConflict(
                        "email reply receipt does not match its dispatch claim"
                    )
                if claim_row["status"] != "done":
                    if validated_claim_owner is None:
                        raise EmailReplyDispatchConflict(
                            "email reply claim completion requires its owner fence"
                        )
                    updated = db.execute(
                        """
                        update email_reply_dispatch_claims
                        set status='done', updated_at=?
                        where action_identity=?
                          and status in ('dispatching', 'retryable', 'uncertain')
                          and owner_id=? and owner_generation=? and lease_token=?
                        """,
                        (
                            self._now(),
                            action_identity,
                            validated_claim_owner["owner_id"],
                            validated_claim_owner["generation"],
                            validated_claim_owner["lease_token"],
                        ),
                    ).rowcount
                    if updated != 1:
                        raise EmailReplyDispatchConflict(
                            "email reply dispatch owner fence changed"
                        )
            row = db.execute(
                "select * from email_reply_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            persisted = self._email_reply_receipt_row(row)
        return persisted

    @staticmethod
    def _email_reply_receipt_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "outgoing_message_id": row["outgoing_message_id"],
            "provider_operation": row["provider_operation"],
            "provider_target": row["provider_target"],
            "provider_result_id": row["provider_result_id"],
            "provider_receipt": _json_load(
                row["provider_receipt_json"],
                field="provider_receipt_json",
                expected_type=dict,
            ),
            "display_excerpt": row["display_excerpt"],
            "created_at": row["created_at"],
        }

    def claim_email_unsubscribe_write(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        action_plan_version: int,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        thread_identity: str,
        entry_reference: str,
        operations: Sequence[Mapping[str, object]],
        owner: Mapping[str, object],
        previous_effect_digest: str = "",
        network_policy_reference: str = "network-policy:legacy",
        network_policy_origin_references: Sequence[str] = ("network-origin:legacy",),
        task_id: int | None = None,
        task_execution_generation: str | None = None,
        task_lifecycle_version: str | None = None,
        task_action_type: str | None = None,
        audit_agent_run_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Atomically verify current authorization and fence one browser write."""

        if (task_id is None) != (task_execution_generation is None):
            raise ValueError(
                "task_id and task_execution_generation must be supplied together"
            )
        if task_id is not None:
            if not isinstance(task_id, int) or task_id <= 0:
                raise ValueError("task_id must be positive")
            if (
                not isinstance(task_execution_generation, str)
                or not task_execution_generation.strip()
            ):
                raise ValueError("task_execution_generation must be non-empty")
            if (
                not isinstance(task_lifecycle_version, str)
                or not task_lifecycle_version.strip()
            ):
                raise ValueError("task_lifecycle_version must be non-empty")
            if task_action_type != EmailAction.UNSUBSCRIBE.value:
                raise ValueError("task_action_type must be unsubscribe")
            if task_lifecycle_version == "email_unsubscribe_audited_v2":
                if (
                    not isinstance(audit_agent_run_id, int)
                    or isinstance(audit_agent_run_id, bool)
                    or audit_agent_run_id <= 0
                ):
                    raise ValueError("audit_agent_run_id must be positive")
            elif audit_agent_run_id is not None:
                raise ValueError("audit_agent_run_id requires the audited-v2 lifecycle")
        elif audit_agent_run_id is not None:
            raise ValueError("audit_agent_run_id requires a task binding")

        binding = _validate_unsubscribe_binding(
            action_identity=action_identity,
            effect_digest=effect_digest,
            action_plan_id=action_plan_id,
            action_plan_version=action_plan_version,
            classification_id=classification_id,
            account_id=account_id,
            stable_message_identity=stable_message_identity,
            thread_identity=thread_identity,
            entry_reference=entry_reference,
        )
        validated_operations = _validate_unsubscribe_operations(operations)
        expected_digest = email_unsubscribe_effect_digest(
            **{key: binding[key] for key in binding if key != "effect_digest"},
            operations=validated_operations,
            previous_effect_digest=previous_effect_digest,
            network_policy_reference=network_policy_reference,
            network_policy_origin_references=network_policy_origin_references,
        )
        if effect_digest != expected_digest:
            raise EmailUnsubscribeClaimConflict(
                "unsubscribe effect digest does not match accepted operations"
            )
        owner = _validate_email_unsubscribe_owner(owner)
        network_policy_reference = _validate_unsubscribe_opaque(
            network_policy_reference, field="network_policy_reference"
        )
        network_policy_origin_references = tuple(
            _validate_unsubscribe_opaque(value, field="network_policy_origin_reference")
            for value in network_policy_origin_references
        )
        operations_json = _json_dump(validated_operations)
        claimed_at = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            task_join = ""
            task_predicates = ""
            task_args: tuple[object, ...] = ()
            if task_id is not None:
                task_join = "join reply_tasks as tasks on tasks.id=?"
                task_predicates = """
                  and tasks.status='processing'
                  and tasks.channel='email'
                  and tasks.execution_generation=?
                  and tasks.trigger_message_id=?
                  and json_extract(tasks.trigger_message_json, '$.schema')=?
                  and json_extract(tasks.trigger_message_json, '$.action_type')=?
                  and json_extract(tasks.trigger_message_json, '$.lifecycle_version')=?
                """
                if audit_agent_run_id is not None:
                    task_join += """
                      join agent_runs as audit_runs on audit_runs.id=?
                      join agent_runs as consumer_runs
                        on consumer_runs.id=audit_runs.parent_agent_run_id
                    """
                    task_predicates += """
                  and audit_runs.reply_task_id=tasks.id
                  and audit_runs.execution_generation=tasks.execution_generation
                  and audit_runs.role='audit'
                  and audit_runs.status='running'
                  and trim(audit_runs.operation_id)<>''
                  and consumer_runs.reply_task_id=tasks.id
                  and consumer_runs.execution_generation=tasks.execution_generation
                  and consumer_runs.role='consumer'
                  and consumer_runs.status='completed'
                  and consumer_runs.proposal_revision=audit_runs.proposal_revision
                  and consumer_runs.id=(
                      select current_consumer.id
                      from agent_runs as current_consumer
                      where current_consumer.reply_task_id=tasks.id
                        and current_consumer.execution_generation=
                            tasks.execution_generation
                        and current_consumer.role='consumer'
                        and current_consumer.status='completed'
                        and current_consumer.proposal_revision=
                            audit_runs.proposal_revision
                      order by current_consumer.turn_attempt desc,
                               current_consumer.id desc
                      limit 1
                  )
                  and audit_runs.id=(
                      select current_audit.id
                      from agent_runs as current_audit
                      where current_audit.reply_task_id=tasks.id
                        and current_audit.execution_generation=
                            tasks.execution_generation
                        and current_audit.role='audit'
                        and current_audit.status='running'
                        and current_audit.proposal_revision=
                            audit_runs.proposal_revision
                      order by current_audit.turn_attempt desc,
                               current_audit.id desc
                      limit 1
                  )
                    """
                task_args = (
                    task_execution_generation,
                    action_identity,
                    "email_agent_action.v1",
                    EmailAction.UNSUBSCRIBE.value,
                    task_lifecycle_version,
                )
            live = db.execute(
                f"""
                select classifications.status as classification_status,
                       classifications.current_action_plan_id,
                       classifications.account_id as classification_account_id,
                       classifications.stable_message_identity
                           as classification_message_identity,
                       plans.action_plan_version, plans.account_id as plan_account_id,
                       plans.actions_json,
                       messages.account_id as message_account_id,
                       messages.thread_identity,
                       accounts.enabled, accounts.updated_at as account_updated_at
                from email_classifications as classifications
                join email_action_plans as plans
                  on plans.action_plan_id=?
                 and plans.classification_id=classifications.id
                join email_messages as messages
                  on messages.stable_message_identity=?
                join email_accounts as accounts on accounts.account_id=?
                {task_join}
                where classifications.id=?
                {task_predicates}
                """,
                tuple(
                    [action_plan_id, stable_message_identity, account_id]
                    + (
                        [task_id]
                        + (
                            [audit_agent_run_id]
                            if audit_agent_run_id is not None
                            else []
                        )
                        if task_id is not None
                        else []
                    )
                    + [classification_id]
                    + list(task_args)
                ),
            ).fetchone()
            expected_identity = email_action_identity(
                account_id=account_id,
                stable_message_identity=stable_message_identity,
                action_type=EmailAction.UNSUBSCRIBE,
                action_plan_version=action_plan_version,
            )
            authorized = (
                live is not None
                and action_identity == expected_identity
                and live["classification_status"] == "processed"
                and live["current_action_plan_id"] == action_plan_id
                and live["classification_account_id"] == account_id
                and live["classification_message_identity"] == stable_message_identity
                and live["action_plan_version"] == action_plan_version
                and live["plan_account_id"] == account_id
                and EmailAction.UNSUBSCRIBE.value
                in _json_load(
                    live["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
                and live["message_account_id"] == account_id
                and live["thread_identity"] == thread_identity
                and bool(live["enabled"])
            )
            if not authorized:
                return None
            receipt = db.execute(
                "select * from email_unsubscribe_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
            if receipt is not None:
                persisted = self._email_unsubscribe_receipt_row(receipt)
                if persisted["effect_digest"] != effect_digest:
                    raise EmailUnsubscribeReceiptConflict(
                        "unsubscribe receipt belongs to a different accepted effect"
                    )
                return {"acquired": False, "status": "done"}
            claim = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            expected_claim = {
                **binding,
                "operations": validated_operations,
            }
            if audit_agent_run_id is not None:
                expected_claim["audit_agent_run_id"] = audit_agent_run_id
            if claim is not None:
                persisted = self._email_unsubscribe_claim_row(claim)
                if persisted["status"] == "awaiting_audit":
                    continuation = db.execute(
                        "select * from email_unsubscribe_continuations where action_identity=?",
                        (action_identity,),
                    ).fetchone()
                    prior_effect = db.execute(
                        "select * from email_unsubscribe_effects where action_identity=? and effect_digest=?",
                        (action_identity, persisted["effect_digest"]),
                    ).fetchone()
                    if continuation is None or prior_effect is None:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation durable state is missing"
                        )
                    prior_operations = _json_load(
                        prior_effect["operations_json"],
                        field="operations_json",
                        expected_type=list,
                    )
                    controls = _json_load(
                        continuation["controls_json"],
                        field="controls_json",
                        expected_type=list,
                    )
                    if previous_effect_digest != persisted["effect_digest"]:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation prefix digest changed"
                        )
                    if (
                        validated_operations[:-1] != prior_operations
                        or len(validated_operations) != len(prior_operations) + 1
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation prefix is not append-only"
                        )
                    if network_policy_reference != prior_effect[
                        "network_policy_reference"
                    ] or list(network_policy_origin_references) != _json_load(
                        prior_effect["network_policy_origins_json"],
                        field="network_policy_origins_json",
                        expected_type=list,
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation network policy changed"
                        )
                    next_operation = validated_operations[-1]
                    control = next(
                        (
                            item
                            for item in controls
                            if item.get("reference")
                            == next_operation["target_reference"]
                        ),
                        None,
                    )
                    allowed_kinds = {
                        "form": {"submit_form"},
                        "link": {"click_confirmation"},
                        "button": {"click_confirmation"},
                        "confirmation_email": {"confirm_email"},
                        "email_otp": {"submit_form"},
                        "captcha_handoff": {
                            "click_confirmation",
                            "reconcile_handoff",
                        },
                        "credential_handoff": {"reconcile_handoff"},
                    }
                    if control is None:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation control is unknown"
                        )
                    if next_operation["kind"] not in allowed_kinds.get(
                        control.get("kind"), set()
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation control kind is invalid"
                        )
                    captcha_attempted = any(
                        item["kind"] == "click_confirmation"
                        and item["target_reference"] == control.get("reference")
                        for item in validated_operations[:-1]
                    )
                    if control.get("kind") == "captcha_handoff" and (
                        (
                            next_operation["kind"] == "click_confirmation"
                            and captcha_attempted
                        )
                        or (
                            next_operation["kind"] == "reconcile_handoff"
                            and not captcha_attempted
                        )
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe CAPTCHA continuation stage is invalid"
                        )
                    if any(
                        persisted[key] != binding[key]
                        for key in (
                            "action_identity",
                            "action_plan_id",
                            "action_plan_version",
                            "classification_id",
                            "account_id",
                            "stable_message_identity",
                            "thread_identity",
                            "entry_reference",
                        )
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation belongs to a different effect"
                        )
                    if (
                        persisted["owner_id"] == owner["owner_id"]
                        and persisted["owner_generation"] == owner["generation"]
                        and persisted["lease_token"] == owner["lease_token"]
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation requires a fresh owner fence"
                        )
                    db.execute(
                        """
                        update email_unsubscribe_claims
                        set effect_digest=?, operations_json=?, owner_id=?,
                            owner_generation=?, lease_token=?, account_updated_at=?,
                            audit_agent_run_id=?,
                            status='dispatching', phase='prepared',
                            claimed_at=?, updated_at=?
                        where action_identity=? and status='awaiting_audit'
                        """,
                        (
                            effect_digest,
                            operations_json,
                            owner["owner_id"],
                            owner["generation"],
                            owner["lease_token"],
                            live["account_updated_at"],
                            audit_agent_run_id,
                            claimed_at,
                            claimed_at,
                            action_identity,
                        ),
                    )
                    db.execute(
                        """
                        insert into email_unsubscribe_effects (
                            action_identity, effect_digest, previous_effect_digest,
                            operations_json, network_policy_reference,
                            network_policy_origins_json, audit_agent_run_id, created_at
                        ) values (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            action_identity,
                            effect_digest,
                            previous_effect_digest,
                            operations_json,
                            network_policy_reference,
                            _json_dump(list(network_policy_origin_references)),
                            audit_agent_run_id,
                            claimed_at,
                        ),
                    )
                    db.execute(
                        "delete from email_unsubscribe_continuations where action_identity=?",
                        (action_identity,),
                    )
                    row = db.execute(
                        "select * from email_unsubscribe_claims where action_identity=?",
                        (action_identity,),
                    ).fetchone()
                    assert row is not None
                    return {
                        **self._email_unsubscribe_claim_row(row),
                        "acquired": True,
                        "executed_prefix_length": len(prior_operations),
                    }
                if (
                    audit_agent_run_id is None
                    and persisted["audit_agent_run_id"] is not None
                    and (
                        persisted["owner_id"] != owner["owner_id"]
                        or persisted["owner_generation"] != owner["generation"]
                        or persisted["lease_token"] != owner["lease_token"]
                    )
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "audited unsubscribe claim requires its exact owner fence"
                    )
                if any(
                    persisted[key] != value for key, value in expected_claim.items()
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe identity is bound to another accepted effect"
                    )
                return {**persisted, "acquired": False}
            else:
                if previous_effect_digest:
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe initial effect cannot skip its prefix"
                    )
                if len(validated_operations) != 1 or validated_operations[0][
                    "kind"
                ] not in {"open_entry", "post_one_click"}:
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe initial effect must contain one entry operation"
                    )
                db.execute(
                    """
                    insert into email_unsubscribe_claims (
                        action_identity, effect_digest, action_plan_id,
                        action_plan_version, classification_id, account_id,
                        stable_message_identity, thread_identity, entry_reference,
                        operations_json, owner_id, owner_generation, lease_token,
                        account_updated_at, status, phase, audit_agent_run_id,
                        claimed_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'dispatching', 'prepared', ?, ?, ?)
                    """,
                    (
                        action_identity,
                        effect_digest,
                        action_plan_id,
                        action_plan_version,
                        classification_id,
                        account_id,
                        stable_message_identity,
                        thread_identity,
                        entry_reference,
                        operations_json,
                        owner["owner_id"],
                        owner["generation"],
                        owner["lease_token"],
                        live["account_updated_at"],
                        audit_agent_run_id,
                        claimed_at,
                        claimed_at,
                    ),
                )
                db.execute(
                    """
                    insert into email_unsubscribe_effects (
                        action_identity, effect_digest, previous_effect_digest,
                        operations_json, network_policy_reference,
                        network_policy_origins_json, audit_agent_run_id, created_at
                    ) values (?, ?, '', ?, ?, ?, ?, ?)
                    """,
                    (
                        action_identity,
                        effect_digest,
                        operations_json,
                        network_policy_reference,
                        _json_dump(list(network_policy_origin_references)),
                        audit_agent_run_id,
                        claimed_at,
                    ),
                )
            row = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            return {
                **self._email_unsubscribe_claim_row(row),
                "acquired": True,
                "executed_prefix_length": 0,
            }

    def get_email_unsubscribe_claim(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_unsubscribe_claim_row(row)

    def get_email_unsubscribe_state_snapshot(
        self,
        action_identity: str,
    ) -> dict[str, Any]:
        """Read the claim, continuation, and complete bounded lineage once.

        The explicit deferred transaction pins one SQLite read view.  The
        continuation driver can therefore validate up to the durable maximum
        without opening one connection per historical effect.
        This method is read-only and deliberately performs no recovery writes.
        """

        db = self._connect()
        try:
            db.execute("begin")
            claim_row = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            continuation_row = db.execute(
                """
                select continuation.*, effect.previous_effect_digest,
                       effect.operations_json, effect.network_policy_reference,
                       effect.network_policy_origins_json
                from email_unsubscribe_continuations as continuation
                join email_unsubscribe_effects as effect
                  on effect.action_identity=continuation.action_identity
                 and effect.effect_digest=continuation.effect_digest
                where continuation.action_identity=?
                """,
                (action_identity,),
            ).fetchone()
            effect_rows = db.execute(
                "select * from email_unsubscribe_effects "
                "where action_identity=? limit ?",
                (
                    action_identity,
                    MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS + 1,
                ),
            ).fetchall()
            if len(effect_rows) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
                raise EmailPersistenceCorruption(
                    "unsubscribe snapshot exceeds durable continuation operation limit"
                )
            claim = (
                None
                if claim_row is None
                else self._email_unsubscribe_claim_row(claim_row)
            )
            continuation = (
                None
                if continuation_row is None
                else self._email_unsubscribe_continuation_row(continuation_row)
            )
            effects = tuple(
                self._email_unsubscribe_effect_row(row) for row in effect_rows
            )
            db.rollback()
            return {
                "claim": claim,
                "continuation": continuation,
                "effects": effects,
            }
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def get_email_unsubscribe_terminal_snapshot(
        self,
        action_identity: str,
        effect_digest: str | None = None,
        *,
        task_id: int | None = None,
        task_execution_generation: str | None = None,
        expected_audit_agent_run_id: int | None = None,
        expected_owner: Mapping[str, object] | None = None,
        expected_effect: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Atomically validate and project one durable terminal readback."""

        action_identity = _validate_unsubscribe_opaque(
            action_identity,
            field="action_identity",
        )
        if effect_digest is not None and (
            not isinstance(effect_digest, str)
            or _SHA256_HEX.fullmatch(effect_digest) is None
        ):
            raise ValueError("effect_digest must be canonical sha256 hex")
        if (task_id is None) != (task_execution_generation is None):
            raise ValueError(
                "task_id and task_execution_generation must be supplied together"
            )
        if task_id is not None and (
            not isinstance(task_id, int)
            or isinstance(task_id, bool)
            or task_id <= 0
            or not isinstance(task_execution_generation, str)
            or not task_execution_generation.strip()
        ):
            raise ValueError("terminal snapshot task binding is invalid")
        if expected_audit_agent_run_id is not None and (
            not isinstance(expected_audit_agent_run_id, int)
            or isinstance(expected_audit_agent_run_id, bool)
            or expected_audit_agent_run_id <= 0
        ):
            raise ValueError("expected_audit_agent_run_id must be positive")
        owner = (
            None
            if expected_owner is None
            else _validate_email_unsubscribe_owner(expected_owner)
        )
        if (expected_audit_agent_run_id is None) != (owner is None):
            raise ValueError("expected Audit run and owner must be supplied together")
        validated_expected_effect = (
            None
            if expected_effect is None
            else _validate_terminal_expected_effect(expected_effect)
        )
        if (
            validated_expected_effect is not None
            and validated_expected_effect["action_identity"] != action_identity
        ):
            raise ValueError("expected terminal effect identity changed")

        db = self._connect()
        try:
            db.execute("begin")
            claim_row = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            claim = (
                None
                if claim_row is None
                else self._email_unsubscribe_claim_row(claim_row)
            )
            receipt_row = db.execute(
                "select * from email_unsubscribe_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
            if receipt_row is None:
                if claim is not None and claim["status"] == "done":
                    raise EmailPersistenceCorruption(
                        "done unsubscribe claim has no durable terminal receipt"
                    )
                db.rollback()
                return None
            if claim is None:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal receipt has no durable claim"
                )
            durable_effect_digest = str(claim["effect_digest"])
            if effect_digest is None:
                effect_digest = durable_effect_digest
            elif effect_digest != durable_effect_digest:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect digest changed"
                )
            receipt = self._email_unsubscribe_receipt_row(receipt_row)
            effect_rows = db.execute(
                "select * from email_unsubscribe_effects "
                "where action_identity=? limit ?",
                (
                    action_identity,
                    MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS + 1,
                ),
            ).fetchall()
            if len(effect_rows) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect lineage exceeds its durable limit"
                )
            effects = {
                str(row["effect_digest"]): self._email_unsubscribe_effect_row(row)
                for row in effect_rows
            }
            steps = db.execute(
                "select * from email_unsubscribe_steps "
                "where action_identity=? order by sequence",
                (action_identity,),
            ).fetchall()
            self._validate_email_unsubscribe_terminal_snapshot(
                db,
                claim=claim,
                receipt=receipt,
                effects=effects,
                steps=steps,
                effect_digest=effect_digest,
                task_id=task_id,
                task_execution_generation=task_execution_generation,
                expected_audit_agent_run_id=expected_audit_agent_run_id,
                expected_owner=owner,
                expected_effect=validated_expected_effect,
            )
            projected_steps = [
                {
                    key: row[key]
                    for key in ("sequence", "operation", "state", "reference")
                }
                for row in steps
            ]
            safe_receipt = {
                key: receipt[key]
                for key in (
                    "outcome",
                    "receipt_id",
                    "evidence",
                    "result_text",
                    "observation_digest",
                    "started_at",
                    "completed_at",
                )
            }
            db.rollback()
            return {
                "receipt": safe_receipt,
                "steps": projected_steps,
            }
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def _validate_email_unsubscribe_terminal_snapshot(
        self,
        db: sqlite3.Connection,
        *,
        claim: Mapping[str, object],
        receipt: Mapping[str, object],
        effects: Mapping[str, Mapping[str, object]],
        steps: Sequence[sqlite3.Row],
        effect_digest: str,
        task_id: int | None,
        task_execution_generation: str | None,
        expected_audit_agent_run_id: int | None,
        expected_owner: Mapping[str, object] | None,
        expected_effect: Mapping[str, object] | None,
    ) -> None:
        if (
            claim.get("status") != "done"
            or claim.get("phase") != "terminal"
            or claim.get("action_identity") != receipt.get("action_identity")
            or claim.get("effect_digest") != effect_digest
            or receipt.get("effect_digest") != effect_digest
            or receipt.get("outcome") not in _EMAIL_UNSUBSCRIBE_OUTCOMES
            or any(
                receipt.get(key) != claim.get(key)
                for key in (
                    "action_plan_id",
                    "action_plan_version",
                    "classification_id",
                    "account_id",
                    "stable_message_identity",
                    "thread_identity",
                    "entry_reference",
                )
            )
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal receipt identity is invalid"
            )
        try:
            _validate_email_unsubscribe_owner(
                {
                    "owner_id": claim["owner_id"],
                    "generation": claim["owner_generation"],
                    "lease_token": claim["lease_token"],
                }
            )
            _validate_unsubscribe_opaque(
                receipt["receipt_id"],
                field="receipt_id",
            )
            _validate_unsubscribe_opaque(receipt["evidence"], field="evidence")
        except (KeyError, TypeError, ValueError) as exc:
            raise EmailPersistenceCorruption(
                "unsubscribe terminal snapshot contains unsafe durable state"
            ) from exc
        if expected_owner is not None and any(
            claim.get(claim_key) != expected_owner.get(owner_key)
            for claim_key, owner_key in (
                ("owner_id", "owner_id"),
                ("owner_generation", "generation"),
                ("lease_token", "lease_token"),
            )
        ):
            raise EmailPersistenceCorruption("unsubscribe terminal owner fence changed")

        validated_effects: dict[str, dict[str, object]] = {}
        for digest, effect in effects.items():
            try:
                operations = _validate_unsubscribe_operations(effect["operations"])
                origins = tuple(
                    _validate_unsubscribe_opaque(
                        value,
                        field="network_policy_origin_reference",
                    )
                    for value in effect["network_policy_origin_references"]
                )
                expected_digest = email_unsubscribe_effect_digest(
                    action_identity=str(claim["action_identity"]),
                    action_plan_id=str(claim["action_plan_id"]),
                    action_plan_version=int(claim["action_plan_version"]),
                    classification_id=int(claim["classification_id"]),
                    account_id=str(claim["account_id"]),
                    stable_message_identity=str(claim["stable_message_identity"]),
                    thread_identity=str(claim["thread_identity"]),
                    entry_reference=str(claim["entry_reference"]),
                    operations=operations,
                    previous_effect_digest=str(effect["previous_effect_digest"]),
                    network_policy_reference=str(effect["network_policy_reference"]),
                    network_policy_origin_references=origins,
                )
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect contains invalid durable state"
                ) from exc
            if digest != expected_digest or effect.get("effect_digest") != digest:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect digest is invalid"
                )
            validated_effects[digest] = {
                **effect,
                "operations": operations,
                "network_policy_origin_references": list(origins),
            }
        head = validated_effects.get(effect_digest)
        if head is None or head["operations"] != claim.get("operations"):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal head effect does not match its claim"
            )
        if expected_effect is not None and (
            any(
                claim.get(field) != expected_effect.get(field)
                for field in (
                    "action_identity",
                    "action_plan_id",
                    "action_plan_version",
                    "classification_id",
                    "account_id",
                    "stable_message_identity",
                    "thread_identity",
                    "entry_reference",
                )
            )
            or head["operations"] != expected_effect.get("operations")
            or head["network_policy_reference"]
            != expected_effect.get("network_policy_reference")
            or head["network_policy_origin_references"]
            != expected_effect.get("network_policy_origin_references")
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect does not match accepted action"
            )
        chain: list[dict[str, object]] = []
        current = head
        seen: set[str] = set()
        while True:
            digest = str(current["effect_digest"])
            if digest in seen:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect lineage contains a cycle"
                )
            seen.add(digest)
            chain.append(current)
            previous_digest = str(current["previous_effect_digest"])
            if not previous_digest:
                break
            previous = validated_effects.get(previous_digest)
            if (
                previous is None
                or current["operations"][:-1] != previous["operations"]
                or len(current["operations"]) != len(previous["operations"]) + 1
                or current["network_policy_reference"]
                != previous["network_policy_reference"]
                or current["network_policy_origin_references"]
                != previous["network_policy_origin_references"]
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect lineage is not append-only"
                )
            current = previous
        chain.reverse()
        if len(chain) != len(validated_effects):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect lineage contains an orphan branch"
            )
        chain_by_length = {
            len(effect["operations"]): str(effect["effect_digest"]) for effect in chain
        }
        if len(steps) != len(head["operations"]):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal journal is not contiguous or effect-bound"
            )
        for sequence, step in enumerate(steps, start=1):
            try:
                for field in ("operation", "state", "reference"):
                    _validate_unsubscribe_opaque(step[field], field=field)
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal journal contains unsafe durable state"
                ) from exc
            if (
                step["sequence"] != sequence
                or chain_by_length.get(sequence) != step["effect_digest"]
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal journal is not contiguous or effect-bound"
                )

        original_audit_run_id = claim.get("audit_agent_run_id")
        if head.get("audit_agent_run_id") != original_audit_run_id:
            raise EmailPersistenceCorruption(
                "unsubscribe terminal Audit binding is inconsistent"
            )
        if original_audit_run_id is None:
            if task_id is not None or expected_audit_agent_run_id is not None:
                raise EmailPersistenceCorruption(
                    "audited unsubscribe terminal is missing its original Audit run"
                )
            return
        self._validate_email_unsubscribe_effect_audit_lineage(
            db,
            chain=chain,
            claim=claim,
            task_id=task_id,
            task_execution_generation=task_execution_generation,
            expected_audit_agent_run_id=expected_audit_agent_run_id,
        )
        classification = db.execute(
            """
            select classifications.id, classifications.account_id,
                   classifications.stable_message_identity,
                   classifications.current_action_plan_id,
                   messages.thread_identity as message_thread_identity
            from email_classifications as classifications
            join email_messages as messages
              on messages.account_id=classifications.account_id
             and messages.stable_message_identity=
                 classifications.stable_message_identity
            where classifications.id=?
            """,
            (claim["classification_id"],),
        ).fetchone()
        lineage = (
            None
            if classification is None
            else _audited_unsubscribe_lineage(
                db,
                receipt=db.execute(
                    "select * from email_unsubscribe_receipts where action_identity=?",
                    (claim["action_identity"],),
                ).fetchone(),
                classification=classification,
            )
        )
        if (
            lineage is None
            or (task_id is not None and lineage["task_id"] != task_id)
            or (
                task_execution_generation is not None
                and db.execute(
                    "select execution_generation from reply_tasks where id=?",
                    (lineage["task_id"],),
                ).fetchone()[0]
                != task_execution_generation
            )
            or (
                expected_audit_agent_run_id is not None
                and original_audit_run_id != expected_audit_agent_run_id
            )
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal historical Audit lineage is invalid"
            )

    def _validate_email_unsubscribe_effect_audit_lineage(
        self,
        db: sqlite3.Connection,
        *,
        chain: Sequence[Mapping[str, object]],
        claim: Mapping[str, object],
        task_id: int | None,
        task_execution_generation: str | None,
        expected_audit_agent_run_id: int | None,
    ) -> None:
        """Bind every accepted effect prefix to its exact Consumer/Audit round."""

        previous_audit_run_id: int | None = None
        lineage_task_id: int | None = None
        lineage_generation: str | None = None
        for depth, effect in enumerate(chain, start=1):
            audit_agent_run_id = effect.get("audit_agent_run_id")
            if (
                not isinstance(audit_agent_run_id, int)
                or isinstance(audit_agent_run_id, bool)
                or audit_agent_run_id <= 0
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect is missing its Audit run"
                )
            row = db.execute(
                """
                select audit.id as audit_id, audit.reply_task_id,
                       audit.execution_generation, audit.role as audit_role,
                       audit.status as audit_status,
                       audit.proposal_revision as audit_revision,
                       audit.turn_attempt as audit_turn_attempt,
                       audit.parent_agent_run_id as audit_parent_id,
                       audit.operation_id as audit_operation_id,
                       consumer.id as consumer_id,
                       consumer.reply_task_id as consumer_task_id,
                       consumer.execution_generation as consumer_generation,
                       consumer.role as consumer_role,
                       consumer.status as consumer_status,
                       consumer.proposal_revision as consumer_revision,
                       consumer.parent_agent_run_id as consumer_parent_id,
                       consumer.operation_id as consumer_operation_id,
                       tasks.channel as task_channel,
                       tasks.trigger_message_id,
                       tasks.trigger_message_json,
                       tasks.execution_generation as task_generation
                from agent_runs as audit
                join agent_runs as consumer
                  on consumer.id=audit.parent_agent_run_id
                join reply_tasks as tasks on tasks.id=audit.reply_task_id
                where audit.id=?
                """,
                (audit_agent_run_id,),
            ).fetchone()
            expected_revision = depth - 1
            is_current_head = (
                depth == len(chain)
                and expected_audit_agent_run_id == audit_agent_run_id
            )
            allowed_statuses = {"completed"}
            if depth == len(chain):
                allowed_statuses.add("failed")
            if is_current_head:
                allowed_statuses.add("running")
            task_payload = None
            payload_entries = None
            if row is not None:
                try:
                    task_payload = json.loads(str(row["trigger_message_json"]))
                    payload_entries = task_payload.get("unsubscribe_entries")
                except (AttributeError, TypeError, ValueError, RecursionError):
                    task_payload = None
                    payload_entries = None
            expected_payload = {
                "schema": "email_agent_action.v1",
                "lifecycle_version": "email_unsubscribe_audited_v2",
                "action_type": EmailAction.UNSUBSCRIBE.value,
                "action_identity": claim["action_identity"],
                "action_plan_id": claim["action_plan_id"],
                "action_plan_version": claim["action_plan_version"],
                "classification_id": claim["classification_id"],
                "account_id": claim["account_id"],
                "stable_message_identity": claim["stable_message_identity"],
                "thread_identity": claim["thread_identity"],
                "unsubscribe_network_policy_reference": effect[
                    "network_policy_reference"
                ],
                "unsubscribe_network_policy_origin_references": effect[
                    "network_policy_origin_references"
                ],
            }
            if (
                row is None
                or row["audit_id"] != audit_agent_run_id
                or row["audit_role"] != "audit"
                or row["audit_status"] not in allowed_statuses
                or row["audit_revision"] != expected_revision
                or not isinstance(row["audit_turn_attempt"], int)
                or row["audit_turn_attempt"] < 0
                or not str(row["audit_operation_id"]).strip()
                or row["consumer_id"] != row["audit_parent_id"]
                or row["consumer_role"] != "consumer"
                or row["consumer_status"] != "completed"
                or row["consumer_revision"] != expected_revision
                or str(row["consumer_operation_id"])
                or row["consumer_task_id"] != row["reply_task_id"]
                or row["consumer_generation"] != row["execution_generation"]
                or row["task_channel"] != "email"
                or row["trigger_message_id"] != claim["action_identity"]
                or row["task_generation"] != row["execution_generation"]
                or not isinstance(task_payload, dict)
                or any(
                    task_payload.get(field) != expected
                    for field, expected in expected_payload.items()
                )
                or not isinstance(payload_entries, list)
                or not any(
                    isinstance(entry, dict)
                    and entry.get("reference") == claim["entry_reference"]
                    for entry in payload_entries
                )
                or (depth == 1 and row["consumer_parent_id"] is not None)
                or (depth > 1 and row["consumer_parent_id"] != previous_audit_run_id)
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect Audit lineage is invalid"
                )
            if depth == len(chain):
                owner_id = claim.get("owner_id")
                expected_owner_suffix = f":{audit_agent_run_id}"
                if (
                    not isinstance(owner_id, str)
                    or not owner_id.endswith(expected_owner_suffix)
                    or len(owner_id) <= len(expected_owner_suffix)
                    or claim.get("owner_generation")
                    != max(1, int(row["audit_turn_attempt"]) + 1)
                ):
                    raise EmailPersistenceCorruption(
                        "unsubscribe terminal owner is not bound to its Audit run"
                    )
            current_task_id = int(row["reply_task_id"])
            current_generation = str(row["execution_generation"])
            if lineage_task_id is None:
                lineage_task_id = current_task_id
                lineage_generation = current_generation
            elif (
                current_task_id != lineage_task_id
                or current_generation != lineage_generation
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect Audit lineage changed task"
                )
            previous_audit_run_id = audit_agent_run_id

        if (
            lineage_task_id is None
            or (task_id is not None and lineage_task_id != task_id)
            or (
                task_execution_generation is not None
                and lineage_generation != task_execution_generation
            )
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect Audit task binding is invalid"
            )

    def get_email_unsubscribe_effect(
        self,
        action_identity: str,
        effect_digest: str,
    ) -> dict[str, Any] | None:
        """Return one redacted durable effect without exposing provider URLs."""

        with self._connect() as db:
            row = db.execute(
                "select * from email_unsubscribe_effects "
                "where action_identity=? and effect_digest=?",
                (action_identity, effect_digest),
            ).fetchone()
        if row is None:
            return None
        return self._email_unsubscribe_effect_row(row)

    @staticmethod
    def _email_unsubscribe_effect_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "previous_effect_digest": row["previous_effect_digest"],
            "operations": _json_load(
                row["operations_json"],
                field="operations_json",
                expected_type=list,
            ),
            "network_policy_reference": row["network_policy_reference"],
            "network_policy_origin_references": _json_load(
                row["network_policy_origins_json"],
                field="network_policy_origins_json",
                expected_type=list,
            ),
            "audit_agent_run_id": row["audit_agent_run_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _email_unsubscribe_claim_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "thread_identity": row["thread_identity"],
            "entry_reference": row["entry_reference"],
            "operations": _json_load(
                row["operations_json"],
                field="operations_json",
                expected_type=list,
            ),
            "owner_id": row["owner_id"],
            "owner_generation": row["owner_generation"],
            "lease_token": row["lease_token"],
            "account_updated_at": row["account_updated_at"],
            "status": row["status"],
            "phase": row["phase"],
            "audit_agent_run_id": row["audit_agent_run_id"],
            "claimed_at": row["claimed_at"],
            "updated_at": row["updated_at"],
        }

    def persist_email_unsubscribe_continuation(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        action_plan_version: int,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        thread_identity: str,
        entry_reference: str,
        operations: Sequence[Mapping[str, object]],
        controls: Sequence[Mapping[str, object]],
        observation_reference: str,
        final_step: Mapping[str, object],
        owner: Mapping[str, object],
        previous_effect_digest: str = "",
        network_policy_reference: str = "network-policy:legacy",
        network_policy_origin_references: Sequence[str] = ("network-origin:legacy",),
    ) -> dict[str, Any]:
        del action_plan_id, action_plan_version, classification_id, account_id
        del stable_message_identity, thread_identity, entry_reference
        validated_operations = _validate_unsubscribe_operations(operations)
        validated_controls = _validate_unsubscribe_controls(controls)
        observation_reference = _validate_unsubscribe_opaque(
            observation_reference, field="observation_reference"
        )
        owner = _validate_email_unsubscribe_owner(owner)
        if set(final_step) != {"sequence", "operation", "state", "reference"}:
            raise ValueError("unsubscribe continuation step fields are invalid")
        sequence = final_step["sequence"]
        _require_positive_int(sequence, field="sequence")
        step = {
            "operation": _validate_unsubscribe_opaque(
                final_step["operation"], field="operation"
            ),
            "state": _validate_unsubscribe_opaque(final_step["state"], field="state"),
            "reference": _validate_unsubscribe_opaque(
                final_step["reference"], field="reference"
            ),
        }
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                """
                select * from email_unsubscribe_claims
                where action_identity=? and effect_digest=? and status='dispatching'
                  and owner_id=? and owner_generation=? and lease_token=?
                """,
                (
                    action_identity,
                    effect_digest,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).fetchone()
            effect_row = db.execute(
                """
                select * from email_unsubscribe_effects
                where action_identity=? and effect_digest=?
                """,
                (action_identity, effect_digest),
            ).fetchone()
            if claim is None or effect_row is None:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation owner fence changed"
                )
            if (
                _json_load(
                    effect_row["operations_json"],
                    field="operations_json",
                    expected_type=list,
                )
                != validated_operations
                or effect_row["previous_effect_digest"] != previous_effect_digest
                or effect_row["network_policy_reference"] != network_policy_reference
                or _json_load(
                    effect_row["network_policy_origins_json"],
                    field="network_policy_origins_json",
                    expected_type=list,
                )
                != list(network_policy_origin_references)
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation effect binding changed"
                )
            previous = db.execute(
                "select coalesce(max(sequence), 0) from email_unsubscribe_steps where action_identity=?",
                (action_identity,),
            ).fetchone()[0]
            if sequence != previous + 1 or sequence != len(validated_operations):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation journal is not contiguous"
                )
            db.execute(
                """
                insert into email_unsubscribe_steps (
                    action_identity, effect_digest, sequence, operation,
                    state, reference, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_identity,
                    effect_digest,
                    sequence,
                    step["operation"],
                    step["state"],
                    step["reference"],
                    now,
                ),
            )
            db.execute(
                """
                insert into email_unsubscribe_continuations (
                    action_identity, effect_digest, observation_reference,
                    controls_json, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(action_identity) do update set
                    effect_digest=excluded.effect_digest,
                    observation_reference=excluded.observation_reference,
                    controls_json=excluded.controls_json,
                    updated_at=excluded.updated_at
                """,
                (
                    action_identity,
                    effect_digest,
                    observation_reference,
                    _json_dump(validated_controls),
                    now,
                    now,
                ),
            )
            updated = db.execute(
                """
                update email_unsubscribe_claims
                set status='awaiting_audit', updated_at=?
                where action_identity=? and effect_digest=? and status='dispatching'
                  and owner_id=? and owner_generation=? and lease_token=?
                """,
                (
                    now,
                    action_identity,
                    effect_digest,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation owner fence changed"
                )
        continuation = self.get_email_unsubscribe_continuation(action_identity)
        assert continuation is not None
        return continuation

    def get_email_unsubscribe_continuation(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select continuation.*, effect.previous_effect_digest,
                       effect.operations_json, effect.network_policy_reference,
                       effect.network_policy_origins_json
                from email_unsubscribe_continuations as continuation
                join email_unsubscribe_effects as effect
                  on effect.action_identity=continuation.action_identity
                 and effect.effect_digest=continuation.effect_digest
                where continuation.action_identity=?
                """,
                (action_identity,),
            ).fetchone()
        if row is None:
            return None
        return self._email_unsubscribe_continuation_row(row)

    @staticmethod
    def _email_unsubscribe_continuation_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "previous_effect_digest": row["previous_effect_digest"],
            "operations": _json_load(
                row["operations_json"], field="operations_json", expected_type=list
            ),
            "controls": _json_load(
                row["controls_json"], field="controls_json", expected_type=list
            ),
            "observation_reference": row["observation_reference"],
            "network_policy_reference": row["network_policy_reference"],
            "network_policy_origin_references": _json_load(
                row["network_policy_origins_json"],
                field="network_policy_origins_json",
                expected_type=list,
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def append_email_unsubscribe_step(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        sequence: int,
        operation: str,
        state: str,
        reference: str,
        owner: Mapping[str, object],
    ) -> dict[str, Any]:
        owner = _validate_email_unsubscribe_owner(owner)
        _require_positive_int(sequence, field="sequence")
        operation = _validate_unsubscribe_opaque(operation, field="operation")
        state = _validate_unsubscribe_opaque(state, field="state")
        reference = _validate_unsubscribe_opaque(reference, field="reference")
        created_at = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                """
                select * from email_unsubscribe_claims
                where action_identity=? and effect_digest=?
                  and status='dispatching' and owner_id=?
                  and owner_generation=? and lease_token=?
                """,
                (
                    action_identity,
                    effect_digest,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).fetchone()
            if claim is None:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe journal owner fence changed"
                )
            existing = db.execute(
                """
                select * from email_unsubscribe_steps
                where action_identity=? and sequence=?
                """,
                (action_identity, sequence),
            ).fetchone()
            if existing is not None:
                persisted = self._email_unsubscribe_step_row(existing)
                if {
                    key: persisted[key] for key in ("operation", "state", "reference")
                } != {
                    "operation": operation,
                    "state": state,
                    "reference": reference,
                } or existing["effect_digest"] != effect_digest:
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe journal sequence is bound differently"
                    )
                return persisted
            previous = db.execute(
                """
                select coalesce(max(sequence), 0) from email_unsubscribe_steps
                where action_identity=?
                """,
                (action_identity,),
            ).fetchone()[0]
            if sequence != previous + 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe journal sequence is not contiguous"
                )
            cursor = db.execute(
                """
                insert into email_unsubscribe_steps (
                    action_identity, effect_digest, sequence, operation,
                    state, reference, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_identity,
                    effect_digest,
                    sequence,
                    operation,
                    state,
                    reference,
                    created_at,
                ),
            )
            row = db.execute(
                "select * from email_unsubscribe_steps where id=?",
                (cursor.lastrowid,),
            ).fetchone()
            assert row is not None
            return self._email_unsubscribe_step_row(row)

    def list_email_unsubscribe_steps(
        self,
        action_identity: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select * from email_unsubscribe_steps
                where action_identity=? order by sequence
                """,
                (action_identity,),
            ).fetchall()
        return [self._email_unsubscribe_step_row(row) for row in rows]

    @staticmethod
    def _email_unsubscribe_step_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sequence": row["sequence"],
            "operation": row["operation"],
            "state": row["state"],
            "reference": row["reference"],
            "created_at": row["created_at"],
        }

    def recover_terminated_email_unsubscribe_claims(
        self,
        *,
        owner: Mapping[str, object],
        termination_verifier: Callable[[Mapping[str, object]], bool],
        recovered_at: str,
    ) -> int:
        owner = _validate_email_unsubscribe_owner(owner)
        if not termination_verifier(owner):
            raise EmailUnsubscribeClaimConflict("owner_termination_not_proven")
        recovered_at = _required_utc_timestamp(recovered_at, field="recovered_at")
        with self._connect() as db:
            db.execute("begin immediate")
            return db.execute(
                """
                update email_unsubscribe_claims
                set status='uncertain', updated_at=?
                where status='dispatching' and owner_id=?
                  and owner_generation=? and lease_token=?
                """,
                (
                    recovered_at,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount

    def mark_email_unsubscribe_uncertain(
        self,
        action_identity: str,
        *,
        owner: Mapping[str, object],
    ) -> None:
        owner = _validate_email_unsubscribe_owner(owner)
        with self._connect() as db:
            db.execute("begin immediate")
            updated = db.execute(
                """
                update email_unsubscribe_claims
                set status='uncertain', phase='effect_uncertain', updated_at=?
                where action_identity=? and status='dispatching'
                  and owner_id=? and owner_generation=? and lease_token=?
                """,
                (
                    self._now(),
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe claim owner fence changed"
                )

    def advance_email_unsubscribe_phase(
        self,
        action_identity: str,
        phase: str,
        *,
        owner: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Advance the durable unsubscribe phase without permitting rollback."""

        phases = {
            "prepared": 0,
            "navigating": 1,
            "effect_uncertain": 2,
            "terminal": 3,
        }
        if phase not in phases:
            raise ValueError("unsubscribe phase is invalid")
        validated_owner = (
            None if owner is None else _validate_email_unsubscribe_owner(owner)
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            if row is None:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe phase claim is missing"
                )
            current = row["phase"]
            if phases[current] > phases[phase]:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe phase cannot move backward"
                )
            if validated_owner is not None and any(
                row[key] != validated_owner[owner_key]
                for key, owner_key in (
                    ("owner_id", "owner_id"),
                    ("owner_generation", "generation"),
                    ("lease_token", "lease_token"),
                )
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe phase owner fence changed"
                )
            if current != phase:
                db.execute(
                    "update email_unsubscribe_claims set phase=?, updated_at=? "
                    "where action_identity=?",
                    (phase, self._now(), action_identity),
                )
            updated = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert updated is not None
            return self._email_unsubscribe_claim_row(updated)

    def persist_email_unsubscribe_terminal(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        action_plan_version: int,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        thread_identity: str,
        entry_reference: str,
        operations: Sequence[Mapping[str, object]],
        previous_effect_digest: str = "",
        network_policy_reference: str = "network-policy:legacy",
        network_policy_origin_references: Sequence[str] = ("network-origin:legacy",),
        outcome: str,
        receipt_id: str,
        evidence: str,
        result_text: str = "",
        observation_digest: str = "",
        result_text_digest: str | None = None,
        result_text_truncated: bool | None = None,
        started_at: str = "",
        completed_at: str = "",
        final_step: Mapping[str, object] | None = None,
        claim_owner: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        binding = _validate_unsubscribe_binding(
            action_identity=action_identity,
            effect_digest=effect_digest,
            action_plan_id=action_plan_id,
            action_plan_version=action_plan_version,
            classification_id=classification_id,
            account_id=account_id,
            stable_message_identity=stable_message_identity,
            thread_identity=thread_identity,
            entry_reference=entry_reference,
        )
        validated_operations = _validate_unsubscribe_operations(operations)
        expected_digest = email_unsubscribe_effect_digest(
            **{key: binding[key] for key in binding if key != "effect_digest"},
            operations=validated_operations,
            previous_effect_digest=previous_effect_digest,
            network_policy_reference=network_policy_reference,
            network_policy_origin_references=network_policy_origin_references,
        )
        if effect_digest != expected_digest:
            raise EmailUnsubscribeReceiptConflict(
                "unsubscribe receipt digest does not match accepted operations"
            )
        if outcome not in _EMAIL_UNSUBSCRIBE_OUTCOMES:
            raise ValueError("unsubscribe terminal outcome is invalid")
        receipt_id = _validate_unsubscribe_opaque(receipt_id, field="receipt_id")
        evidence = _validate_unsubscribe_opaque(evidence, field="evidence")
        if not isinstance(result_text, str):
            raise ValueError("unsubscribe result_text must be text")
        from app.email_unsubscribe import normalize_unsubscribe_result_text

        explicit_integrity_metadata = (
            result_text_digest is not None or result_text_truncated is not None
        )
        normalized_result_text, expected_observation_digest = (
            normalize_unsubscribe_result_text(result_text)
        )
        if explicit_integrity_metadata:
            if (
                result_text_digest is None
                or result_text_truncated is None
                or type(result_text_truncated) is not bool
                or normalized_result_text != result_text
            ):
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe result integrity metadata is inconsistent"
                )
            try:
                _validate_durable_unsubscribe_result_text(
                    normalized_result_text,
                    observation_digest,
                    int(result_text_truncated),
                    result_text_digest,
                )
            except EmailPersistenceCorruption as exc:
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe result integrity metadata is inconsistent"
                ) from exc
        else:
            if observation_digest and observation_digest != expected_observation_digest:
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe observation digest does not match result text"
                )
            if normalized_result_text:
                observation_digest = expected_observation_digest
                result_text_digest = sha256(
                    normalized_result_text.encode("utf-8")
                ).hexdigest()
                result_text_truncated = observation_digest != result_text_digest
            else:
                observation_digest = ""
                result_text_digest = ""
                result_text_truncated = False
        created_at = self._now()
        started_at = started_at or created_at
        completed_at = completed_at or created_at
        owner = (
            None
            if claim_owner is None
            else _validate_email_unsubscribe_owner(claim_owner)
        )
        validated_step: dict[str, object] | None = None
        if final_step is not None:
            if set(final_step) != {"sequence", "operation", "state", "reference"}:
                raise ValueError("unsubscribe terminal step fields are invalid")
            _require_positive_int(final_step["sequence"], field="sequence")
            validated_step = {
                "sequence": final_step["sequence"],
                "operation": _validate_unsubscribe_opaque(
                    final_step["operation"], field="operation"
                ),
                "state": _validate_unsubscribe_opaque(
                    final_step["state"], field="state"
                ),
                "reference": _validate_unsubscribe_opaque(
                    final_step["reference"], field="reference"
                ),
            }
        with self._connect() as db:
            db.execute("begin immediate")
            plan = db.execute(
                """
                select plans.*, classifications.stable_message_identity
                  as classification_message_identity,
                       messages.account_id as message_account_id,
                       messages.thread_identity as message_thread_identity
                from email_action_plans as plans
                join email_classifications as classifications
                  on classifications.id=plans.classification_id
                join email_messages as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where plans.action_plan_id=? and plans.classification_id=?
                """,
                (action_plan_id, classification_id),
            ).fetchone()
            expected_action_identity = email_action_identity(
                account_id=account_id,
                stable_message_identity=stable_message_identity,
                action_type=EmailAction.UNSUBSCRIBE,
                action_plan_version=action_plan_version,
            )
            if action_identity != expected_action_identity:
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe terminal action identity is invalid"
                )
            if (
                plan is None
                or plan["action_plan_version"] != action_plan_version
                or plan["account_id"] != account_id
                or plan["classification_message_identity"] != stable_message_identity
                or plan["message_account_id"] != account_id
                or plan["message_thread_identity"] != thread_identity
                or EmailAction.UNSUBSCRIBE.value
                not in _json_load(
                    plan["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
            ):
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe terminal evidence does not match immutable plan"
                )
            claim = db.execute(
                "select * from email_unsubscribe_claims where action_identity=?",
                (action_identity,),
            ).fetchone()
            expected_claim = {**binding, "operations": validated_operations}
            if claim is None:
                if previous_effect_digest:
                    raise EmailUnsubscribeReceiptConflict(
                        "unsubscribe continuation terminal is missing its prefix claim"
                    )
                account = db.execute(
                    "select updated_at from email_accounts where account_id=?",
                    (account_id,),
                ).fetchone()
                db.execute(
                    """
                    insert into email_unsubscribe_claims (
                        action_identity, effect_digest, action_plan_id,
                        action_plan_version, classification_id, account_id,
                        stable_message_identity, thread_identity, entry_reference,
                        operations_json, owner_id, owner_generation, lease_token,
                        account_updated_at, status, phase, claimed_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'reconciliation', 1, ?, ?, 'done', 'terminal', ?, ?)
                    """,
                    (
                        action_identity,
                        effect_digest,
                        action_plan_id,
                        action_plan_version,
                        classification_id,
                        account_id,
                        stable_message_identity,
                        thread_identity,
                        entry_reference,
                        _json_dump(validated_operations),
                        f"receipt-{effect_digest[:24]}",
                        account["updated_at"] if account is not None else "historical",
                        created_at,
                        created_at,
                    ),
                )
                db.execute(
                    """
                    insert into email_unsubscribe_effects (
                        action_identity, effect_digest, previous_effect_digest,
                        operations_json, network_policy_reference,
                        network_policy_origins_json, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_identity,
                        effect_digest,
                        previous_effect_digest,
                        _json_dump(validated_operations),
                        network_policy_reference,
                        _json_dump(list(network_policy_origin_references)),
                        created_at,
                    ),
                )
            else:
                persisted_claim = self._email_unsubscribe_claim_row(claim)
                if any(
                    persisted_claim[key] != value
                    for key, value in expected_claim.items()
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe receipt does not match its claim"
                    )
                if persisted_claim["status"] == "dispatching":
                    if owner is None or any(
                        persisted_claim[key] != owner[owner_key]
                        for key, owner_key in (
                            ("owner_id", "owner_id"),
                            ("owner_generation", "generation"),
                            ("lease_token", "lease_token"),
                        )
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe claim completion requires its owner fence"
                        )
            existing_receipt = db.execute(
                "select * from email_unsubscribe_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
            requested_receipt = {
                **binding,
                "outcome": outcome,
                "receipt_id": receipt_id,
                "evidence": evidence,
                "result_text": normalized_result_text,
                "observation_digest": observation_digest,
                "result_text_truncated": result_text_truncated,
                "result_text_digest": result_text_digest,
                "started_at": started_at,
                "completed_at": completed_at,
            }
            if existing_receipt is None:
                db.execute(
                    """
                    insert into email_unsubscribe_receipts (
                        action_identity, effect_digest, action_plan_id,
                        action_plan_version, classification_id, account_id,
                        stable_message_identity, thread_identity, entry_reference,
                        outcome, receipt_id, evidence, result_text,
                        observation_digest, result_text_truncated,
                        result_text_digest, started_at, completed_at, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_identity,
                        effect_digest,
                        action_plan_id,
                        action_plan_version,
                        classification_id,
                        account_id,
                        stable_message_identity,
                        thread_identity,
                        entry_reference,
                        outcome,
                        receipt_id,
                        evidence,
                        normalized_result_text,
                        observation_digest,
                        int(result_text_truncated),
                        result_text_digest,
                        started_at,
                        completed_at,
                        created_at,
                    ),
                )
            else:
                persisted_receipt = self._email_unsubscribe_receipt_row(
                    existing_receipt
                )
                if any(
                    persisted_receipt[key] != value
                    for key, value in requested_receipt.items()
                ):
                    raise EmailUnsubscribeReceiptConflict(
                        "unsubscribe identity is bound to different terminal evidence"
                    )
            if validated_step is not None:
                previous = db.execute(
                    """
                    select coalesce(max(sequence), 0) from email_unsubscribe_steps
                    where action_identity=?
                    """,
                    (action_identity,),
                ).fetchone()[0]
                existing_step = db.execute(
                    """
                    select * from email_unsubscribe_steps
                    where action_identity=? and sequence=?
                    """,
                    (action_identity, validated_step["sequence"]),
                ).fetchone()
                if existing_step is None:
                    if validated_step["sequence"] != previous + 1:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe terminal journal is not contiguous"
                        )
                    db.execute(
                        """
                        insert into email_unsubscribe_steps (
                            action_identity, effect_digest, sequence, operation,
                            state, reference, created_at
                        ) values (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            action_identity,
                            effect_digest,
                            validated_step["sequence"],
                            validated_step["operation"],
                            validated_step["state"],
                            validated_step["reference"],
                            created_at,
                        ),
                    )
                elif existing_step["effect_digest"] != effect_digest or any(
                    existing_step[key] != validated_step[key]
                    for key in ("operation", "state", "reference")
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe terminal journal is bound differently"
                    )
            db.execute(
                """
                update email_unsubscribe_claims
                set status='done', phase='terminal', updated_at=?
                where action_identity=?
                  and status in ('dispatching', 'awaiting_audit', 'uncertain')
                """,
                (created_at, action_identity),
            )
            db.execute(
                "delete from email_unsubscribe_continuations where action_identity=?",
                (action_identity,),
            )
            row = db.execute(
                "select * from email_unsubscribe_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            return self._email_unsubscribe_receipt_row(row)

    def get_email_unsubscribe_receipt(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from email_unsubscribe_receipts where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_unsubscribe_receipt_row(row)

    @staticmethod
    def _email_unsubscribe_receipt_row(row: sqlite3.Row) -> dict[str, Any]:
        _validate_durable_unsubscribe_result_text(
            row["result_text"],
            row["observation_digest"],
            row["result_text_truncated"],
            row["result_text_digest"],
        )
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "thread_identity": row["thread_identity"],
            "entry_reference": row["entry_reference"],
            "outcome": row["outcome"],
            "receipt_id": row["receipt_id"],
            "evidence": row["evidence"],
            "result_text": row["result_text"],
            "observation_digest": row["observation_digest"],
            "result_text_truncated": bool(row["result_text_truncated"]),
            "result_text_digest": row["result_text_digest"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
        }

    def create_account(
        self,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> dict[str, Any]:
        row, _snapshot = self.create_account_with_category_enablement_snapshot(
            values,
            allow_shared_email=allow_shared_email,
        )
        return row

    def create_account_with_category_enablement_snapshot(
        self,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> tuple[dict[str, Any], EmailCategoryEnablementSnapshot]:
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            if (
                db.execute(
                    "select 1 from email_accounts where account_id=?",
                    (values["account_id"],),
                ).fetchone()
                is not None
            ):
                raise EmailAccountConflict("account_id_conflict")
            self._assert_email_address_available(
                db,
                str(values["email_address"]),
                allow_shared_email=allow_shared_email,
            )
            now = self._next_account_timestamp()
            self._insert_account(db, values, created_at=now, updated_at=now)
            category_snapshot = self._disable_categories_missing_active_bindings(
                db,
                updated_at=now,
            )
            self._validate_category_binding_completeness(db)
            row = db.execute(
                "select * from email_accounts where account_id=?",
                (values["account_id"],),
            ).fetchone()
        assert row is not None
        return self._account_row(row), category_snapshot

    def update_account(
        self,
        account_id: str,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        result = self.update_account_with_category_enablement_snapshot(
            account_id,
            values,
            allow_shared_email=allow_shared_email,
        )
        if result is None:
            return None
        row, previous, _snapshot = result
        return row, previous

    def update_account_with_category_enablement_snapshot(
        self,
        account_id: str,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> (
        tuple[
            dict[str, Any],
            dict[str, Any],
            EmailCategoryEnablementSnapshot,
        ]
        | None
    ):
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                "select * from email_accounts where account_id=?",
                (account_id,),
            ).fetchone()
            if existing is None:
                return None
            previous = self._account_row(existing)
            updated_at = self._next_account_timestamp(existing["updated_at"])
            self._assert_email_address_available(
                db,
                str(values["email_address"]),
                allow_shared_email=allow_shared_email,
                excluding_account_id=account_id,
            )
            db.execute(
                """
                update email_accounts set
                    display_name=?, email_address=?, imap_host=?, imap_port=?,
                    imap_tls=?, imap_username=?, imap_secret_reference=?,
                    smtp_host=?, smtp_port=?, smtp_tls=?, smtp_username=?,
                    smtp_secret_reference=?, enabled=?, scan_folders_json=?,
                    scan_interval_seconds=?, updated_at=?
                where account_id=?
                """,
                (
                    values["display_name"],
                    values["email_address"],
                    values["imap_host"],
                    values["imap_port"],
                    int(bool(values["imap_tls"])),
                    values["imap_username"],
                    values["imap_secret_reference"],
                    values["smtp_host"],
                    values["smtp_port"],
                    int(bool(values["smtp_tls"])),
                    values["smtp_username"],
                    values["smtp_secret_reference"],
                    int(bool(values["enabled"])),
                    _json_dump(list(values["scan_folders"])),
                    values["scan_interval_seconds"],
                    updated_at,
                    account_id,
                ),
            )
            category_snapshot = self._disable_categories_missing_active_bindings(
                db,
                updated_at=updated_at,
            )
            self._validate_category_binding_completeness(db)
            row = db.execute(
                "select * from email_accounts where account_id=?",
                (account_id,),
            ).fetchone()
        assert row is not None
        return self._account_row(row), previous, category_snapshot

    def delete_account_if_unchanged(
        self,
        account_id: str,
        *,
        expected_updated_at: str,
        category_enablement_snapshot: EmailCategoryEnablementSnapshot | None = None,
    ) -> bool:
        with self._connect() as db:
            db.execute("begin immediate")
            if category_enablement_snapshot is not None and not (
                self._category_enablement_snapshot_is_current(
                    db,
                    category_enablement_snapshot,
                )
            ):
                return False
            cursor = db.execute(
                "delete from email_accounts where account_id=? and updated_at=?",
                (account_id, expected_updated_at),
            )
            if cursor.rowcount == 1 and category_enablement_snapshot is not None:
                self._restore_category_enablement_snapshot(
                    db,
                    category_enablement_snapshot,
                )
                self._validate_category_binding_completeness(db)
        return cursor.rowcount == 1

    def restore_account_if_unchanged(
        self,
        snapshot: Mapping[str, object],
        *,
        expected_updated_at: str,
        category_enablement_snapshot: EmailCategoryEnablementSnapshot | None = None,
    ) -> bool:
        with self._connect() as db:
            db.execute("begin immediate")
            if category_enablement_snapshot is not None and not (
                self._category_enablement_snapshot_is_current(
                    db,
                    category_enablement_snapshot,
                )
            ):
                return False
            cursor = db.execute(
                """
                update email_accounts set
                    display_name=?, email_address=?, imap_host=?, imap_port=?,
                    imap_tls=?, imap_username=?, imap_secret_reference=?,
                    smtp_host=?, smtp_port=?, smtp_tls=?, smtp_username=?,
                    smtp_secret_reference=?, enabled=?, scan_folders_json=?,
                    scan_interval_seconds=?, created_at=?, updated_at=?
                where account_id=? and updated_at=?
                """,
                (
                    snapshot["display_name"],
                    snapshot["email_address"],
                    snapshot["imap_host"],
                    snapshot["imap_port"],
                    int(bool(snapshot["imap_tls"])),
                    snapshot["imap_username"],
                    snapshot["imap_secret_reference"],
                    snapshot["smtp_host"],
                    snapshot["smtp_port"],
                    int(bool(snapshot["smtp_tls"])),
                    snapshot["smtp_username"],
                    snapshot["smtp_secret_reference"],
                    int(bool(snapshot["enabled"])),
                    _json_dump(list(snapshot["scan_folders"])),
                    snapshot["scan_interval_seconds"],
                    snapshot["created_at"],
                    snapshot["updated_at"],
                    snapshot["account_id"],
                    expected_updated_at,
                ),
            )
            if cursor.rowcount == 1:
                if category_enablement_snapshot is None:
                    self._disable_categories_missing_active_bindings(
                        db,
                        updated_at=self._now(),
                    )
                else:
                    self._restore_category_enablement_snapshot(
                        db,
                        category_enablement_snapshot,
                    )
                self._validate_category_binding_completeness(db)
        return cursor.rowcount == 1

    @staticmethod
    def _disable_categories_missing_active_bindings(
        db: sqlite3.Connection,
        *,
        updated_at: str,
    ) -> EmailCategoryEnablementSnapshot:
        rows = db.execute(
            """
            select category_key, enabled, updated_at
            from email_category_configs
            where enabled=1 and exists (
                select 1 from email_accounts as accounts
                where accounts.enabled=1
                  and not exists (
                      select 1 from email_category_folder_bindings as bindings
                      where bindings.account_id=accounts.account_id
                        and bindings.category_key=
                            email_category_configs.category_key
                        and bindings.binding_status='active'
                  )
            )
            order by category_key
            """
        ).fetchall()
        db.execute(
            """
            update email_category_configs set enabled=0, updated_at=?
            where enabled=1 and exists (
                select 1 from email_accounts as accounts
                where accounts.enabled=1
                  and not exists (
                      select 1 from email_category_folder_bindings as bindings
                      where bindings.account_id=accounts.account_id
                        and bindings.category_key=
                            email_category_configs.category_key
                        and bindings.binding_status='active'
                  )
            )
            """,
            (updated_at,),
        )
        return EmailCategoryEnablementSnapshot(
            states=tuple(
                EmailCategoryEnablementState(
                    category_key=row["category_key"],
                    enabled=bool(row["enabled"]),
                    updated_at=row["updated_at"],
                    mutation_updated_at=updated_at,
                )
                for row in rows
            )
        )

    @staticmethod
    def _category_enablement_snapshot_is_current(
        db: sqlite3.Connection,
        snapshot: EmailCategoryEnablementSnapshot,
    ) -> bool:
        if type(snapshot) is not EmailCategoryEnablementSnapshot:
            return False
        for state in snapshot.states:
            if type(state) is not EmailCategoryEnablementState:
                return False
            row = db.execute(
                "select enabled, updated_at from email_category_configs "
                "where category_key=?",
                (state.category_key,),
            ).fetchone()
            if (
                row is None
                or bool(row["enabled"]) is not False
                or row["updated_at"] != state.mutation_updated_at
            ):
                return False
        return True

    @staticmethod
    def _restore_category_enablement_snapshot(
        db: sqlite3.Connection,
        snapshot: EmailCategoryEnablementSnapshot,
    ) -> None:
        for state in snapshot.states:
            updated = db.execute(
                """
                update email_category_configs set enabled=?, updated_at=?
                where category_key=? and enabled=0 and updated_at=?
                """,
                (
                    int(state.enabled),
                    state.updated_at,
                    state.category_key,
                    state.mutation_updated_at,
                ),
            ).rowcount
            if updated != 1:
                raise EmailPersistenceCorruption(
                    "category enablement changed during account compensation"
                )

    @staticmethod
    def _validate_category_binding_completeness(db: sqlite3.Connection) -> None:
        incomplete = db.execute(
            """
            select configs.category_key, accounts.account_id
            from email_category_configs as configs
            cross join email_accounts as accounts
            left join email_category_folder_bindings as bindings
              on bindings.category_key=configs.category_key
             and bindings.account_id=accounts.account_id
             and bindings.binding_status='active'
            where configs.enabled=1 and accounts.enabled=1
              and bindings.account_id is null
            limit 1
            """
        ).fetchone()
        if incomplete is not None:
            raise EmailPersistenceCorruption(
                "enabled email category is missing an active account folder binding"
            )

    @staticmethod
    def _assert_email_address_available(
        db: sqlite3.Connection,
        email_address: str,
        *,
        allow_shared_email: bool,
        excluding_account_id: str = "",
    ) -> None:
        if allow_shared_email:
            return
        row = db.execute(
            """
            select 1 from email_accounts
            where lower(email_address)=lower(?) and account_id != ?
            """,
            (email_address, excluding_account_id),
        ).fetchone()
        if row is not None:
            raise EmailAccountConflict("email_address_conflict")

    @staticmethod
    def _insert_account(
        db: sqlite3.Connection,
        values: Mapping[str, object],
        *,
        created_at: str,
        updated_at: str,
    ) -> None:
        db.execute(
            """
            insert into email_accounts (
                account_id, display_name, email_address, imap_host, imap_port,
                imap_tls, imap_username, imap_secret_reference, smtp_host,
                smtp_port, smtp_tls, smtp_username, smtp_secret_reference,
                enabled, scan_folders_json, scan_interval_seconds, created_at,
                updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["account_id"],
                values["display_name"],
                values["email_address"],
                values["imap_host"],
                values["imap_port"],
                int(bool(values["imap_tls"])),
                values["imap_username"],
                values["imap_secret_reference"],
                values["smtp_host"],
                values["smtp_port"],
                int(bool(values["smtp_tls"])),
                values["smtp_username"],
                values["smtp_secret_reference"],
                int(bool(values["enabled"])),
                _json_dump(list(values["scan_folders"])),
                values["scan_interval_seconds"],
                created_at,
                updated_at,
            ),
        )

    @staticmethod
    def _account_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "display_name": row["display_name"],
            "email_address": row["email_address"],
            "imap_host": row["imap_host"],
            "imap_port": row["imap_port"],
            "imap_tls": bool(row["imap_tls"]),
            "imap_username": row["imap_username"],
            "imap_secret_reference": row["imap_secret_reference"],
            "smtp_host": row["smtp_host"],
            "smtp_port": row["smtp_port"],
            "smtp_tls": bool(row["smtp_tls"]),
            "smtp_username": row["smtp_username"],
            "smtp_secret_reference": row["smtp_secret_reference"],
            "enabled": bool(row["enabled"]),
            "scan_folders": _json_load(
                row["scan_folders_json"],
                field="scan_folders_json",
                expected_type=list,
            ),
            "scan_interval_seconds": row["scan_interval_seconds"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_classifications(
        self, *, status: EmailClassificationStatus, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        with self._connect() as db:
            total = int(
                db.execute(
                    "select count(*) from email_classifications where status=?",
                    (status.value,),
                ).fetchone()[0]
            )
            rows = db.execute(
                """
                select classifications.*,
                       messages.attachment_metadata_json
                           as message_attachment_metadata_json
                from email_classifications as classifications
                left join email_messages as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where classifications.status=?
                order by classifications.updated_at desc, classifications.id desc
                limit ? offset ?
                """,
                (status.value, limit, offset),
            ).fetchall()
        return [self._classification_evidence_row(row) for row in rows], total

    @staticmethod
    def _email_context_message_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "folder": row["folder"],
            "uidvalidity": row["uidvalidity"],
            "uid": row["uid"],
            "rfc_message_id": row["rfc_message_id"],
            "in_reply_to": row["in_reply_to"],
            "references": _json_load(
                row["references_json"], field="references_json", expected_type=list
            ),
            "thread_identity": row["thread_identity"],
            "sender": row["sender"],
            "recipients": _json_load(
                row["recipients_json"], field="recipients_json", expected_type=list
            ),
            "subject": row["subject"],
            "normalized_text": row["normalized_text"],
            "attachment_metadata": _json_load(
                row["attachment_metadata_json"],
                field="attachment_metadata_json",
                expected_type=list,
            ),
            "received_at": row["received_at"],
        }

    def list_email_context_thread(
        self,
        *,
        account_id: str,
        stable_message_identity: str,
    ) -> list[dict[str, Any]]:
        """Return the complete stored relation component for one message."""

        account_id = account_id.strip()
        stable_message_identity = stable_message_identity.strip()
        if not account_id or not stable_message_identity:
            raise ValueError("email context identity must be non-empty")
        with self._connect() as db:
            rows = db.execute(
                "select * from email_messages where account_id=? order by received_at, id",
                (account_id,),
            ).fetchall()
        trigger = next(
            (
                row
                for row in rows
                if row["stable_message_identity"] == stable_message_identity
            ),
            None,
        )
        if trigger is None:
            return []
        thread_identity = str(trigger["thread_identity"] or "")
        known_ids = {
            value
            for value in (
                trigger["rfc_message_id"],
                trigger["in_reply_to"],
                *_json_load(
                    trigger["references_json"],
                    field="references_json",
                    expected_type=list,
                ),
            )
            if value
        }
        related = {stable_message_identity}
        changed = True
        while changed:
            changed = False
            for row in rows:
                row_identity = str(row["stable_message_identity"])
                row_links = set(
                    _json_load(
                        row["references_json"],
                        field="references_json",
                        expected_type=list,
                    )
                )
                row_links.update(
                    value
                    for value in (row["rfc_message_id"], row["in_reply_to"])
                    if value
                )
                if not (
                    row_identity in related
                    or (thread_identity and row["thread_identity"] == thread_identity)
                    or known_ids & row_links
                ):
                    continue
                if row_identity not in related or not row_links <= known_ids:
                    related.add(row_identity)
                    known_ids.update(row_links)
                    changed = True
        return [
            self._email_context_message_row(row)
            for row in rows
            if row["stable_message_identity"] in related
        ]

    def list_email_context_receipts(
        self,
        *,
        account_id: str,
        stable_message_identity: str,
    ) -> list[dict[str, object]]:
        """Project safe prior reply and unsubscribe readbacks."""

        with self._connect() as db:
            reply_rows = db.execute(
                """
                select provider_result_id, provider_operation, display_excerpt, created_at
                from email_reply_receipts
                where account_id=? and stable_message_identity=?
                """,
                (account_id, stable_message_identity),
            ).fetchall()
            unsubscribe_rows = db.execute(
                """
                select receipt_id, outcome, created_at
                from email_unsubscribe_receipts
                where account_id=? and stable_message_identity=?
                """,
                (account_id, stable_message_identity),
            ).fetchall()
        receipts = [
            {
                "receipt_id": row["provider_result_id"],
                "operation": row["provider_operation"],
                "summary": row["display_excerpt"],
                "completed": True,
                "created_at": row["created_at"],
            }
            for row in reply_rows
        ]
        receipts.extend(
            {
                "receipt_id": row["receipt_id"],
                "operation": "unsubscribe_readback",
                "summary": f"Automatic unsubscribe outcome: {row['outcome']}.",
                "completed": True,
                "created_at": row["created_at"],
            }
            for row in unsubscribe_rows
        )
        receipts.sort(key=lambda item: str(item["created_at"]))
        return [
            {key: value for key, value in item.items() if key != "created_at"}
            for item in receipts
        ]

    def get_classification(self, classification_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select classifications.*, messages.normalized_text as message_text,
                       messages.attachment_metadata_json
                           as message_attachment_metadata_json
                from email_classifications as classifications
                left join email_messages as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where classifications.id=?
                """,
                (classification_id,),
            ).fetchone()
        if row is None:
            return None
        message = Parser().parsestr(row["message_text"] or "", headersonly=True)
        return {
            **self._classification_evidence_row(row),
            "message_text": message.get_payload(),
            "cc": message.get("Cc", ""),
            "recipients": [
                address for _, address in getaddresses(message.get_all("To", []))
            ],
        }

    def get_classification_by_stable_identity(
        self, stable_message_identity: str
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from email_classifications where stable_message_identity=?",
                (stable_message_identity,),
            ).fetchone()
        return None if row is None else self._classification_row(row)

    def has_stable_classification(self, stable_message_identity: str) -> bool:
        return (
            self.get_classification_by_stable_identity(stable_message_identity)
            is not None
        )

    def stable_classification_uids(
        self, *, account_id: str, folder: str, uidvalidity: int
    ) -> frozenset[int]:
        with self._connect() as db:
            rows = db.execute(
                """
                select uid from email_classifications
                where account_id=? and folder=? and uidvalidity=?
                """,
                (account_id, folder, uidvalidity),
            ).fetchall()
        return frozenset(int(row["uid"]) for row in rows)

    def list_email_classification_observability(
        self, classification_id: int
    ) -> list[dict[str, Any]]:
        """Project safe action and external-write evidence for one email.

        The projection deliberately excludes provider targets, raw parameters,
        task payloads, and browser-private coordinates. Unsubscribe result text
        is already normalized and redacted at persistence time.
        """

        _require_positive_int(classification_id, field="classification_id")
        with self._connect() as db:
            classification = db.execute(
                """
                select classifications.id, classifications.account_id,
                       classifications.stable_message_identity,
                       classifications.current_action_plan_id,
                       messages.thread_identity as message_thread_identity
                from email_classifications as classifications
                join email_messages as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where classifications.id=?
                """,
                (classification_id,),
            ).fetchone()
            if classification is None:
                return []
            action_rows = db.execute(
                """
                select action_id, action_type, status, attempt_count,
                       provider_operation, provider_result_id, error,
                       started_at, finished_at, created_at
                from email_actions
                where classification_id=?
                order by created_at, action_id
                """,
                (classification_id,),
            ).fetchall()
            attempts = db.execute(
                """
                select attempts.action_id, attempts.attempt_number,
                       attempts.status, attempts.provider_operation,
                       attempts.provider_result_id, attempts.error,
                       attempts.started_at, attempts.finished_at
                from email_action_attempts as attempts
                join email_actions as actions
                  on actions.action_id=attempts.action_id
                where actions.classification_id=?
                order by attempts.action_id, attempts.attempt_number
                """,
                (classification_id,),
            ).fetchall()
            reply_rows = db.execute(
                """
                select action_identity, provider_operation, provider_result_id,
                       display_excerpt, created_at
                from email_reply_receipts
                where classification_id=?
                order by created_at, action_identity
                """,
                (classification_id,),
            ).fetchall()
            unsubscribe_rows = db.execute(
                """
                select action_identity, effect_digest, action_plan_id,
                       action_plan_version, classification_id, account_id,
                       stable_message_identity, thread_identity, receipt_id,
                       evidence, result_text, observation_digest,
                       result_text_truncated, result_text_digest, created_at
                from email_unsubscribe_receipts
                where classification_id=?
                order by created_at, action_identity
                """,
                (classification_id,),
            ).fetchall()
            generic_tables = {
                str(row["name"])
                for row in db.execute(
                    """
                    select name from sqlite_master
                    where type='table' and name in ('reply_tasks', 'agent_runs')
                    """
                ).fetchall()
            }
            task_rows: Sequence[sqlite3.Row] = ()
            if generic_tables == {"reply_tasks", "agent_runs"}:
                task_rows = db.execute(
                    """
                    select id, conversation_id, trigger_message_id,
                           trigger_message_json, execution_generation,
                           status, created_at
                    from reply_tasks
                    where channel='email'
                      and json_valid(trigger_message_json)
                      and json_extract(
                            trigger_message_json, '$.classification_id'
                          )=?
                      and json_extract(
                            trigger_message_json, '$.action_type'
                          )='unsubscribe'
                    order by created_at, id
                    """,
                    (classification_id,),
                ).fetchall()
            lineage_by_action: dict[str, dict[str, object]] = {}
            if unsubscribe_rows and generic_tables == {"reply_tasks", "agent_runs"}:
                for row in unsubscribe_rows:
                    _validate_durable_unsubscribe_result_text(
                        row["result_text"],
                        row["observation_digest"],
                        row["result_text_truncated"],
                        row["result_text_digest"],
                    )
                    lineage = _audited_unsubscribe_lineage(
                        db,
                        receipt=row,
                        classification=classification,
                    )
                    if lineage is not None:
                        lineage_by_action[str(row["action_identity"])] = lineage
            else:
                for row in unsubscribe_rows:
                    _validate_durable_unsubscribe_result_text(
                        row["result_text"],
                        row["observation_digest"],
                        row["result_text_truncated"],
                        row["result_text_digest"],
                    )
            unsubscribe_steps = db.execute(
                """
                select steps.action_identity, steps.sequence, steps.operation,
                       steps.state, steps.reference
                from email_unsubscribe_steps as steps
                join email_unsubscribe_receipts as receipts
                  on receipts.action_identity=steps.action_identity
                where receipts.classification_id=?
                order by steps.action_identity, steps.sequence
                """,
                (classification_id,),
            ).fetchall()
            terminal_unsubscribe_actions = {
                str(row["action_identity"]) for row in unsubscribe_rows
            }
            inflight_unsubscribe_events = []
            for task in task_rows:
                if str(task["trigger_message_id"]) in terminal_unsubscribe_actions:
                    continue
                lineage = _current_unsubscribe_task_lineage(
                    db,
                    task=task,
                    classification=classification,
                )
                if lineage is not None:
                    inflight_unsubscribe_events.append(lineage)

        attempts_by_action: dict[str, list[dict[str, Any]]] = {}
        for row in attempts:
            attempts_by_action.setdefault(row["action_id"], []).append(
                {
                    "attempt_number": row["attempt_number"],
                    "status": row["status"],
                    "provider_operation": row["provider_operation"],
                    "provider_result_id": row["provider_result_id"],
                    "error": row["error"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                }
            )
        events: list[dict[str, Any]] = []
        events.extend(inflight_unsubscribe_events)
        for row in action_rows:
            events.append(
                {
                    "kind": "provider_action",
                    "operation": row["action_type"],
                    "action_id": row["action_id"],
                    "status": row["status"],
                    "attempt_count": row["attempt_count"],
                    "provider_operation": row["provider_operation"],
                    "provider_result_id": row["provider_result_id"],
                    "error": row["error"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "created_at": row["created_at"],
                    "attempts": attempts_by_action.get(row["action_id"], []),
                }
            )
        for row in reply_rows:
            events.append(
                {
                    "kind": "auto_reply",
                    "operation": "auto_reply",
                    "action_identity": row["action_identity"],
                    "status": "done",
                    "provider_operation": row["provider_operation"],
                    "receipt_id": row["provider_result_id"],
                    "summary": row["display_excerpt"],
                    "created_at": row["created_at"],
                }
            )
        steps_by_action: dict[str, list[dict[str, Any]]] = {}
        for row in unsubscribe_steps:
            steps_by_action.setdefault(row["action_identity"], []).append(
                {
                    "sequence": row["sequence"],
                    "operation": row["operation"],
                    "state": row["state"],
                    "reference": row["reference"],
                }
            )
        for row in unsubscribe_rows:
            action_identity = str(row["action_identity"])
            lineage = lineage_by_action.get(action_identity)
            event = {
                "kind": "unsubscribe",
                "operation": "unsubscribe",
                "consumer_run_ids": (
                    lineage["consumer_run_ids"] if lineage is not None else []
                ),
                "audit_run_ids": (
                    lineage["audit_run_ids"] if lineage is not None else []
                ),
                "status": "done",
                "receipt_id": row["receipt_id"],
                "result_text": row["result_text"],
                "evidence": row["evidence"],
                "observation_digest": row["observation_digest"],
                "steps": steps_by_action.get(action_identity, []),
                "_sort_created_at": row["created_at"],
            }
            if lineage is not None:
                event.update(lineage)
            events.append(event)
        events.sort(
            key=lambda item: (
                str(item.get("created_at") or item.get("_sort_created_at") or ""),
                str(item["kind"]),
            )
        )
        for event in events:
            event.pop("_sort_created_at", None)
        return events

    def list_training_examples(
        self, *, include_inclusion: bool = False
    ) -> list[dict[str, Any]]:
        """Return only user-confirmed, redacted texts for local retraining."""

        with self._connect() as db:
            rows = db.execute(
                """
                select id, account_id, stable_message_identity, model_text,
                       confirmed_category as label, included_in_model_id,
                       confirmed_at, classification_source, status
                from email_classifications
                where classification_source='user'
                  and status='processed'
                  and confirmed_category is not null
                  and confirmed_category != ''
                  and trim(model_text) != ''
                order by id asc
                """
            ).fetchall()
        result = []
        for row in rows:
            sample = {
                "message_id": row["stable_message_identity"],
                "model_text": row["model_text"],
                "label": row["label"],
                **(
                    {
                        "classification_id": row["id"],
                        "account_id": row["account_id"],
                        "included_in_model_id": row["included_in_model_id"],
                        "confirmed_at": row["confirmed_at"],
                        "classification_source": row["classification_source"],
                        "status": row["status"],
                    }
                    if include_inclusion
                    else {}
                ),
            }
            if include_inclusion:
                sample["sample_digest"] = _training_sample_digest(sample)
            result.append(sample)
        return result

    def persist_training_snapshot(self, snapshot: object) -> dict[str, object]:
        """Atomically append one validated immutable training snapshot."""

        from app.email_training_snapshot import (
            FolderTrainingSnapshot,
            validate_folder_training_snapshot,
        )

        if type(snapshot) is not FolderTrainingSnapshot:
            raise TypeError("snapshot must be a FolderTrainingSnapshot")
        validate_folder_training_snapshot(snapshot)
        manifest = snapshot.manifest
        manifest_json = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        created_at = self._now()
        with self._connect() as db:
            existing = db.execute(
                "select snapshot_digest from email_training_snapshots "
                "where snapshot_id=?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if existing is not None:
                if existing["snapshot_digest"] != snapshot.snapshot_digest:
                    raise EmailTrainingSnapshotConflict(
                        "training snapshot id already exists with different content"
                    )
                loaded = self._get_training_snapshot(db, snapshot.snapshot_id)
                assert loaded is not None
                return loaded
            previous_snapshot = db.execute(
                """
                select snapshot_id, folder_label_watermark,
                       important_label_watermark
                from email_training_snapshots
                where frozen=1
                order by observed_at desc, snapshot_id desc
                limit 1
                """
            ).fetchone()
            previous_rows: dict[tuple[str, str], sqlite3.Row] = {}
            folder_label_watermark = 0
            important_label_watermark = 0
            if previous_snapshot is not None:
                folder_label_watermark = int(
                    previous_snapshot["folder_label_watermark"]
                )
                important_label_watermark = int(
                    previous_snapshot["important_label_watermark"]
                )
                previous_rows = {
                    (str(row["account_id"]), str(row["stable_message_identity"])): row
                    for row in db.execute(
                        """
                        select account_id, stable_message_identity,
                               category_key, important
                        from email_training_snapshot_observations
                        where snapshot_id=?
                        """,
                        (previous_snapshot["snapshot_id"],),
                    )
                }
            for row in snapshot.observations:
                previous = previous_rows.get(
                    (row.account_id, row.stable_message_identity)
                )
                if row.category_key is not None and (
                    previous is None or previous["category_key"] != row.category_key
                ):
                    folder_label_watermark += 1
                if previous is None or bool(previous["important"]) != row.important:
                    important_label_watermark += 1
            try:
                db.execute(
                    """
                    insert into email_training_snapshots (
                        snapshot_id, snapshot_version, description_version,
                        input_schema_version, seed, observed_at, snapshot_digest,
                        manifest_json, frozen, created_at,
                        folder_label_watermark, important_label_watermark
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.snapshot_version,
                        snapshot.description_version,
                        snapshot.input_schema_version,
                        snapshot.seed,
                        snapshot.observed_at,
                        snapshot.snapshot_digest,
                        manifest_json,
                        created_at,
                        folder_label_watermark,
                        important_label_watermark,
                    ),
                )
                db.executemany(
                    """
                    insert into email_training_snapshot_observations (
                        snapshot_id, account_id, stable_message_identity,
                        provider_folder_id, provider_folder_name, category_key,
                        important, normalized_model_input,
                        normalized_model_input_hash, input_schema_version,
                        provider_thread_id, normalized_body_digest,
                        sender_template_signature, explicit_matter_group,
                        group_key, observed_at, source, split,
                        selected_for_training, ordered_record_digest
                    ) values (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        (
                            row.snapshot_id,
                            row.account_id,
                            row.stable_message_identity,
                            row.provider_folder_id,
                            row.provider_folder_name,
                            row.category_key,
                            int(row.important),
                            row.normalized_model_input,
                            row.normalized_model_input_hash,
                            row.input_schema_version,
                            row.provider_thread_id,
                            row.normalized_body_digest,
                            row.sender_template_signature,
                            row.explicit_matter_group,
                            row.group_key,
                            row.observed_at,
                            row.source,
                            row.split,
                            int(row.selected_for_training),
                            row.ordered_record_digest,
                        )
                        for row in snapshot.observations
                    ],
                )
                frozen = db.execute(
                    "update email_training_snapshots set frozen=1 "
                    "where snapshot_id=? and frozen=0",
                    (snapshot.snapshot_id,),
                ).rowcount
                if frozen != 1:
                    raise EmailTrainingSnapshotConflict(
                        "training snapshot could not be atomically frozen"
                    )
            except sqlite3.IntegrityError as exc:
                raise EmailTrainingSnapshotConflict(
                    "training snapshot already exists or violates frozen constraints"
                ) from exc
            stored = self._get_training_snapshot(db, snapshot.snapshot_id)
            assert stored is not None
            return stored

    def get_training_snapshot(self, snapshot_id: str) -> dict[str, object] | None:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty text")
        with self._connect() as db:
            return self._get_training_snapshot(db, snapshot_id)

    def list_provider_folder_correction_conflicts(
        self, snapshot_id: str
    ) -> list[dict[str, object]]:
        """Return current folder truth that conflicts with the original prediction."""

        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty text")
        with self._connect() as db:
            rows = db.execute(
                """
                select observations.stable_message_identity as sample_id,
                       observations.group_key,
                       classifications.predicted_category,
                       observations.category_key as confirmed_category,
                       observations.normalized_model_input as redacted_text
                from email_training_snapshot_observations as observations
                join email_classifications as classifications
                  on classifications.account_id=observations.account_id
                 and classifications.stable_message_identity=
                     observations.stable_message_identity
                where observations.snapshot_id=?
                  and observations.category_key is not null
                  and classifications.predicted_category is not null
                  and classifications.predicted_category != observations.category_key
                order by observations.stable_message_identity
                """,
                (snapshot_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_classifier_runtime_sample(
        self,
        *,
        model_id: str,
        outcome: str,
        fallback_code: str = "",
        cache_hit: bool,
        runtime_warm: bool,
        queue_ms: float,
        http_ms: float,
        embedding_ms: float,
        head_ms: float,
        total_ms: float,
    ) -> None:
        """Persist one bounded, content-free runtime timing observation."""

        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be non-empty")
        if outcome not in {"success", "rejected", "failure"}:
            raise ValueError("runtime outcome is invalid")
        if fallback_code and _RUNTIME_CODE.fullmatch(fallback_code) is None:
            raise ValueError("fallback_code must be a controlled code")
        if type(cache_hit) is not bool or type(runtime_warm) is not bool:
            raise TypeError("runtime flags must be boolean")
        values = (queue_ms, http_ms, embedding_ms, head_ms, total_ms)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) < 0
            for value in values
        ):
            raise ValueError("runtime timings must be finite and non-negative")
        with self._connect() as db:
            db.execute(
                """
                insert into email_classifier_runtime_samples (
                    model_id, outcome, fallback_code, cache_hit, runtime_warm,
                    queue_ms, http_ms, embedding_ms, head_ms, total_ms, recorded_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id.strip(),
                    outcome,
                    fallback_code,
                    int(cache_hit),
                    int(runtime_warm),
                    *(float(value) for value in values),
                    self._now(),
                ),
            )
            db.execute(
                """
                delete from email_classifier_runtime_samples
                where id not in (
                    select id from email_classifier_runtime_samples
                    order by id desc limit ?
                )
                """,
                (MAX_CLASSIFIER_RUNTIME_SAMPLES,),
            )

    def record_classifier_runtime_fallback(
        self, *, model_id: str, fallback_code: str
    ) -> None:
        """Persist a zero-time fallback when no model timing sample exists."""

        self.record_classifier_runtime_sample(
            model_id=model_id,
            outcome="rejected",
            fallback_code=fallback_code,
            cache_hit=False,
            runtime_warm=True,
            queue_ms=0.0,
            http_ms=0.0,
            embedding_ms=0.0,
            head_ms=0.0,
            total_ms=0.0,
        )

    def classifier_runtime_observability(
        self, *, model_id: str
    ) -> dict[str, object]:
        """Read bounded cross-process timing percentiles and fallback counters."""

        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be non-empty")
        with self._connect() as db:
            rows = db.execute(
                """
                select outcome, fallback_code, cache_hit, runtime_warm,
                       queue_ms, http_ms, embedding_ms, head_ms, total_ms
                from email_classifier_runtime_samples
                where model_id=? order by id desc limit ?
                """,
                (model_id.strip(), MAX_CLASSIFIER_RUNTIME_SAMPLES),
            ).fetchall()

        def percentile(values: list[float], quantile: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            position = (len(ordered) - 1) * quantile
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                return ordered[lower]
            weight = position - lower
            return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

        def segment(selected: list[sqlite3.Row]) -> dict[str, object]:
            return {
                "sample_count": len(selected),
                "stages": {
                    stage: {
                        key: percentile(
                            [float(row[f"{stage}_ms"]) for row in selected], quantile
                        )
                        for key, quantile in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
                    }
                    for stage in ("queue", "http", "embedding", "head", "total")
                },
            }

        all_rows = list(rows)
        warm_success = [
            row
            for row in rows
            if row["runtime_warm"] == 1 and row["outcome"] == "success"
        ]
        timing = {
            "all": segment(all_rows),
            "warm_success": segment(warm_success),
            "warm_success_cache": segment(
                [row for row in warm_success if row["cache_hit"] == 1]
            ),
            "warm_success_remote": segment(
                [row for row in warm_success if row["cache_hit"] == 0]
            ),
            "slo_status": (
                "not_enough_data"
                if not warm_success
                else "compliant"
                if percentile(
                    [float(row["total_ms"]) for row in warm_success], 0.95
                )
                < 500.0
                else "non_compliant"
            ),
        }
        fallback_counts: dict[str, int] = {}
        for row in rows:
            code = str(row["fallback_code"] or "")
            if code:
                fallback_counts[code] = fallback_counts.get(code, 0) + 1
        return {"timing": timing, "fallback_counts": fallback_counts}

    def latest_training_snapshot_state(self) -> dict[str, object] | None:
        """Return the latest frozen snapshot and cumulative label-change watermarks."""

        with self._connect() as db:
            latest = db.execute(
                """
                select snapshot_id, snapshot_version, snapshot_digest,
                       description_version, input_schema_version, observed_at,
                       folder_label_watermark, important_label_watermark
                from email_training_snapshots
                where frozen=1
                order by observed_at desc, snapshot_id desc
                limit 1
                """
            ).fetchone()
            if latest is None:
                return None
            aggregate = db.execute(
                """
                select count(*) as sample_count,
                       count(distinct group_key) as group_count
                from email_training_snapshot_observations
                where snapshot_id=?
                """,
                (latest["snapshot_id"],),
            ).fetchone()
            category_rows = db.execute(
                """
                select category_key, count(*) as sample_count,
                       count(distinct group_key) as group_count
                from email_training_snapshot_observations
                where snapshot_id=? and category_key is not null
                group by category_key
                order by category_key
                """,
                (latest["snapshot_id"],),
            ).fetchall()
            split_rows = db.execute(
                """
                select split,
                       count(*) as sample_count,
                       min(important) as minimum_important,
                       max(important) as maximum_important,
                       group_concat(distinct case
                           when split != 'train' or selected_for_training=1
                           then category_key end
                       ) as categories
                from email_training_snapshot_observations
                where snapshot_id=?
                group by split
                """,
                (latest["snapshot_id"],),
            ).fetchall()
            description_rows = db.execute(
                """
                select category_key, core_description, include_json, exclude_json,
                       description_version
                from email_category_configs where enabled=1
                order by category_key
                """
            ).fetchall()
            enabled_categories = {row["category_key"] for row in description_rows}
            from app.email_description_optimizer import description_set_digest
            from app.email_embedding_classifier import CategoryDescription

            descriptions = {
                row["category_key"]: CategoryDescription(
                    core=row["core_description"],
                    include=tuple(
                        _json_load(
                            row["include_json"],
                            field="include_json",
                            expected_type=list,
                        )
                    ),
                    exclude=tuple(
                        _json_load(
                            row["exclude_json"],
                            field="exclude_json",
                            expected_type=list,
                        )
                    ),
                    version=row["description_version"],
                )
                for row in description_rows
            }
            current_description_version = (
                "description-set-sha256:" + description_set_digest(descriptions)
                if descriptions
                else "description-set-unavailable"
            )
            split_summary = {str(row["split"]): row for row in split_rows}

            def split_categories(name: str) -> set[str]:
                row = split_summary.get(name)
                if row is None or not row["categories"]:
                    return set()
                return set(str(row["categories"]).split(","))

            def split_has_both_important(name: str) -> bool:
                row = split_summary.get(name)
                return bool(
                    row is not None
                    and int(row["sample_count"]) > 0
                    and row["minimum_important"] == 0
                    and row["maximum_important"] == 1
                )

            train_categories = split_categories("train")
            validation_categories = split_categories("validation")
            test_categories = split_categories("test")
            minimum_ready = bool(
                train_categories
                and validation_categories
                and test_categories
                and enabled_categories
                and enabled_categories <= train_categories
                and enabled_categories <= validation_categories
                and enabled_categories <= test_categories
                and all(split_has_both_important(name) for name in ("train", "validation", "test"))
            )
            latest_category_counts = {
                str(row["category_key"]): int(row["sample_count"])
                for row in category_rows
            }
            latest_category_group_counts = {
                str(row["category_key"]): int(row["group_count"])
                for row in category_rows
            }
            return {
                "snapshot_id": latest["snapshot_id"],
                "snapshot_version": latest["snapshot_version"],
                "snapshot_sha": latest["snapshot_digest"],
                "description_version": current_description_version,
                "input_schema_version": latest["input_schema_version"],
                "folder_label_watermark": int(latest["folder_label_watermark"]),
                "important_label_watermark": int(
                    latest["important_label_watermark"]
                ),
                "minimum_ready": minimum_ready,
                "sample_count": int(aggregate["sample_count"]),
                "group_count": int(aggregate["group_count"]),
                "category_sample_counts": latest_category_counts,
                "category_group_counts": latest_category_group_counts,
            }

    def get_provider_classification_state(
        self, classification_id: int
    ) -> dict[str, object]:
        """Project current provider-folder truth without provider network I/O."""

        _require_positive_int(classification_id, field="classification_id")
        with self._connect() as db:
            classification = db.execute(
                "select account_id, stable_message_identity "
                "from email_classifications where id=?",
                (classification_id,),
            ).fetchone()
            if classification is None:
                return {"state": "unavailable", "reason": "classification_missing"}
            row = db.execute(
                """
                select state, category_key, important, provider_folder_id,
                       provider_folder_name, observed_at
                from email_provider_observations
                where account_id=? and stable_message_identity=?
                """,
                (
                    classification["account_id"],
                    classification["stable_message_identity"],
                ),
            ).fetchone()
        if row is None:
            return {"state": "unavailable", "reason": "provider_truth_not_observed"}
        if row["state"] != "available":
            return {
                "state": str(row["state"]),
                "reason": "provider_truth_" + str(row["state"]),
                "observed_at": row["observed_at"],
            }
        category_key = row["category_key"]
        state = (
            "junk"
            if category_key == "junk"
            else "categorized"
            if category_key is not None
            else "unclassified"
        )
        return {
            "state": state,
            "category_key": category_key,
            "important": bool(row["important"]),
            "provider_folder_id": row["provider_folder_id"],
            "provider_folder_name": row["provider_folder_name"],
            "observed_at": row["observed_at"],
        }

    def record_current_provider_observations(
        self,
        observations: Sequence[Mapping[str, object]],
        *,
        unavailable_folders: Sequence[str],
        authoritative_folders: Sequence[str] | None = None,
        active_account_ids: Sequence[str] | None = None,
        observed_at: str,
    ) -> None:
        """Publish one scan generation and reconcile its authoritative membership.

        ``observed_at`` is the durable generation marker. Only folders explicitly
        listed as authoritative may tombstone identities absent from this
        generation; unavailable or still-partial folders retain an unknown row.
        """

        if not isinstance(observed_at, str) or not observed_at.strip():
            raise ValueError("observed_at must be non-empty text")
        rows: list[tuple[object, ...]] = []
        for observation in observations:
            account_id = str(observation.get("account_id") or "").strip()
            identity = str(
                observation.get("stable_message_identity") or ""
            ).strip()
            folder_id = str(observation.get("provider_folder_id") or "").strip()
            folder_name = str(
                observation.get("provider_folder_name") or ""
            ).strip()
            if not account_id or not identity or not folder_id or not folder_name:
                raise ValueError("provider observation identity is incomplete")
            role_value = observation.get("folder_role")
            role = str(getattr(role_value, "value", role_value))
            binding_status = str(
                observation.get("folder_binding_status") or "unbound"
            )
            state = "excluded" if role in {"sent", "draft"} else "available"
            category_key = observation.get("bound_category_key")
            if role in {"spam", "trash"}:
                category_key = "junk"
            elif binding_status != "active":
                category_key = None
            signals = observation.get("important_signals")
            important_value = getattr(signals, "provider_important", None)
            if isinstance(signals, Mapping):
                important_value = signals.get("provider_important")
            if type(important_value) is not bool:
                raise ValueError("provider important state is invalid")
            rows.append(
                (
                    account_id,
                    identity,
                    state,
                    folder_id,
                    folder_name,
                    category_key,
                    int(important_value),
                    observed_at,
                )
            )
        unavailable = tuple(str(value) for value in unavailable_folders)
        authoritative = tuple(str(value) for value in (authoritative_folders or ()))
        active_accounts = (
            None
            if active_account_ids is None
            else tuple(str(value) for value in active_account_ids)
        )
        with self._connect() as db:
            db.execute("begin immediate")
            db.executemany(
                """
                insert into email_provider_observations (
                    account_id, stable_message_identity, state,
                    provider_folder_id, provider_folder_name, category_key,
                    important, observed_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(account_id, stable_message_identity) do update set
                    state=excluded.state,
                    provider_folder_id=excluded.provider_folder_id,
                    provider_folder_name=excluded.provider_folder_name,
                    category_key=excluded.category_key,
                    important=excluded.important,
                    observed_at=excluded.observed_at
                """,
                rows,
            )
            current_membership: dict[tuple[str, str], set[str]] = {}
            for row in rows:
                current_membership.setdefault((str(row[0]), str(row[3])), set()).add(
                    str(row[1])
                )
            for value in authoritative:
                account_id, separator, folder_id = value.partition(":")
                if not separator or not account_id or not folder_id:
                    raise ValueError("authoritative folder identity is invalid")
                identities = current_membership.get((account_id, folder_id), set())
                if identities:
                    placeholders = ",".join("?" for _ in identities)
                    db.execute(
                        "update email_provider_observations set state='unavailable', "
                        "provider_folder_id=null, provider_folder_name=null, "
                        "category_key=null, important=null, observed_at=? "
                        "where account_id=? and provider_folder_id=? and "
                        f"stable_message_identity not in ({placeholders})",
                        (observed_at, account_id, folder_id, *sorted(identities)),
                    )
                else:
                    db.execute(
                        "update email_provider_observations set state='unavailable', "
                        "provider_folder_id=null, provider_folder_name=null, "
                        "category_key=null, important=null, observed_at=? "
                        "where account_id=? and provider_folder_id=?",
                        (observed_at, account_id, folder_id),
                    )
            if active_accounts is not None:
                if active_accounts:
                    placeholders = ",".join("?" for _ in active_accounts)
                    db.execute(
                        "update email_provider_observations set state='unavailable', "
                        "provider_folder_id=null, provider_folder_name=null, "
                        "category_key=null, important=null, observed_at=? "
                        f"where account_id not in ({placeholders})",
                        (observed_at, *active_accounts),
                    )
                else:
                    db.execute(
                        "update email_provider_observations set state='unavailable', "
                        "provider_folder_id=null, provider_folder_name=null, "
                        "category_key=null, important=null, observed_at=?",
                        (observed_at,),
                    )
            for value in unavailable:
                account_id, separator, folder_id = value.partition(":")
                if not separator or not account_id or not folder_id:
                    raise ValueError("unavailable folder identity is invalid")
                db.execute(
                    """
                    update email_provider_observations
                    set state='unavailable', category_key=null, important=null,
                        observed_at=?
                    where account_id=? and provider_folder_id=?
                    """,
                    (observed_at, account_id, folder_id),
                )

    @staticmethod
    def _get_training_snapshot(
        db: sqlite3.Connection, snapshot_id: str
    ) -> dict[str, object] | None:
        snapshot = db.execute(
            "select * from email_training_snapshots where snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            return None
        if snapshot["frozen"] != 1:
            raise EmailPersistenceCorruption("training snapshot is not frozen")
        rows = db.execute(
            """
            select * from email_training_snapshot_observations
            where snapshot_id=?
            order by stable_message_identity
            """,
            (snapshot_id,),
        ).fetchall()
        observation_values = []
        for row in rows:
            value = dict(row)
            value["important"] = bool(value["important"])
            value["selected_for_training"] = bool(value["selected_for_training"])
            observation_values.append(value)
        try:
            from app.email_training_snapshot import (
                FolderTrainingSnapshot,
                TrainingSnapshotObservation,
                validate_folder_training_snapshot,
            )

            restored = FolderTrainingSnapshot(
                snapshot_id=snapshot["snapshot_id"],
                snapshot_version=snapshot["snapshot_version"],
                description_version=snapshot["description_version"],
                input_schema_version=snapshot["input_schema_version"],
                seed=snapshot["seed"],
                observed_at=snapshot["observed_at"],
                observations=tuple(
                    TrainingSnapshotObservation(**value) for value in observation_values
                ),
                snapshot_digest=snapshot["snapshot_digest"],
                _manifest_json=snapshot["manifest_json"],
            )
            validate_folder_training_snapshot(restored, allow_legacy_manifest=True)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EmailPersistenceCorruption(
                "persisted training snapshot does not match its manifest"
            ) from exc
        result = restored.to_dict()
        result["folder_label_watermark"] = int(
            snapshot["folder_label_watermark"]
        )
        result["important_label_watermark"] = int(
            snapshot["important_label_watermark"]
        )
        return result

    def list_unincluded_training_examples(self) -> list[dict[str, Any]]:
        return [
            row
            for row in self.list_training_examples(include_inclusion=True)
            if row["included_in_model_id"] is None
        ]

    def mark_training_examples_included(
        self, samples: Sequence[Mapping[str, object]], *, model_id: str
    ) -> None:
        self.commit_training_promotion(
            samples,
            model_id=model_id,
            promote=lambda: None,
            restore=lambda: None,
        )

    def commit_training_promotion(
        self,
        validation_snapshots: Sequence[Mapping[str, object]],
        *,
        inclusion_snapshots: Sequence[Mapping[str, object]] | None = None,
        model_id: str,
        promote: Callable[[], object],
        restore: Callable[[], object],
    ) -> None:
        validation_by_identity = {
            str(item.get("message_id", "")): item for item in validation_snapshots
        }
        inclusion_items = (
            validation_snapshots if inclusion_snapshots is None else inclusion_snapshots
        )
        inclusion_by_identity = {
            str(item.get("message_id", "")): item for item in inclusion_items
        }
        validation_identities = tuple(validation_by_identity)
        inclusion_identities = tuple(inclusion_by_identity)
        if (
            not validation_identities
            or not inclusion_identities
            or not model_id.strip()
        ):
            raise ValueError("sample identities and model_id are required")
        if len(validation_by_identity) != len(validation_snapshots) or len(
            inclusion_by_identity
        ) != len(inclusion_items):
            raise ValueError("training sample identities must be unique")
        if not set(inclusion_identities).issubset(validation_by_identity):
            raise ValueError("inclusion snapshots must be part of validation snapshots")
        for identity, inclusion_snapshot in inclusion_by_identity.items():
            validation_snapshot = validation_by_identity[identity]
            if (
                inclusion_snapshot.get("sample_digest")
                != validation_snapshot.get("sample_digest")
                or inclusion_snapshot.get("included_in_model_id") is not None
            ):
                raise ValueError(
                    "inclusion snapshots must match unincluded validation snapshots"
                )
        db = self._connect()
        promotion_attempted = False
        try:
            db.execute("begin immediate")
            self._verify_training_snapshots(
                db,
                validation_by_identity,
                validation_identities,
                inclusion_identities=inclusion_identities,
                model_id=model_id,
            )
            promotion_attempted = True
            promote()
            self._update_training_inclusion(db, inclusion_identities, model_id=model_id)
            db.commit()
        except Exception as exc:
            db.rollback()
            if promotion_attempted:
                try:
                    restore()
                except Exception as restore_exc:
                    raise EmailTrainingConsistencyError(
                        "training promotion manifest restore could not be proven"
                    ) from restore_exc
            raise exc
        finally:
            db.close()

    @staticmethod
    def _verify_training_snapshots(
        db,
        snapshots,
        identities,
        *,
        inclusion_identities,
        model_id: str,
    ) -> None:
        placeholders = ",".join("?" for _ in identities)
        rows = db.execute(
            f"""
                select id, account_id, stable_message_identity, model_text,
                       confirmed_category as label, included_in_model_id,
                       confirmed_at, classification_source, status
                from email_classifications
                where stable_message_identity in ({placeholders})
                  and classification_source='user'
                  and status='processed'
                  and confirmed_category is not null
                  and confirmed_category != ''
                  and trim(model_text) != ''
                """,
            identities,
        ).fetchall()
        if {row["stable_message_identity"] for row in rows} != set(identities):
            raise EmailTrainingInclusionConflict(
                "training sample set changed before inclusion"
            )
        inclusion_identity_set = set(inclusion_identities)
        for row in rows:
            current = {
                "message_id": row["stable_message_identity"],
                "model_text": row["model_text"],
                "label": row["label"],
                "classification_id": row["id"],
                "account_id": row["account_id"],
                "included_in_model_id": row["included_in_model_id"],
                "confirmed_at": row["confirmed_at"],
                "classification_source": row["classification_source"],
                "status": row["status"],
            }
            expected_digest = snapshots[row["stable_message_identity"]].get(
                "sample_digest"
            )
            if expected_digest != _training_sample_digest(current):
                raise EmailTrainingInclusionConflict(
                    "training sample changed before inclusion"
                )
            expected_inclusion = snapshots[row["stable_message_identity"]].get(
                "included_in_model_id"
            )
            current_inclusion = row["included_in_model_id"]
            idempotent_inclusion = (
                row["stable_message_identity"] in inclusion_identity_set
                and expected_inclusion is None
                and current_inclusion == model_id
            )
            if current_inclusion != expected_inclusion and not idempotent_inclusion:
                raise EmailTrainingInclusionConflict(
                    "training sample inclusion changed before promotion"
                )

    @staticmethod
    def _update_training_inclusion(db, identities, *, model_id: str) -> None:
        placeholders = ",".join("?" for _ in identities)
        db.execute(
            f"""
            update email_classifications
            set included_in_model_id=?
            where stable_message_identity in ({placeholders})
              and included_in_model_id is null
            """,
            (model_id, *identities),
        )

    def confirm_classification(
        self,
        row_id: int,
        category: EmailCategoryKey,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
    ) -> dict[str, Any] | None:
        application = self._apply_human_classification(
            row_id,
            category,
            feedback_request_id=feedback_request_id,
            expected_current_action_plan_id=expected_current_action_plan_id,
            allow_processed_correction=False,
            created_at=None,
        )
        return None if application is None else application.confirmed

    def apply_human_classification(
        self,
        row_id: int,
        category: EmailCategoryKey,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
        created_at: datetime | None = None,
    ) -> EmailFeedbackApplication | None:
        """Confirm or correct a classification under one durable write lease."""

        return self._apply_human_classification(
            row_id,
            category,
            feedback_request_id=feedback_request_id,
            expected_current_action_plan_id=expected_current_action_plan_id,
            allow_processed_correction=True,
            created_at=created_at,
        )

    def _apply_human_classification(
        self,
        row_id: int,
        category: EmailCategoryKey,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
        allow_processed_correction: bool,
        created_at: datetime | None,
    ) -> EmailFeedbackApplication | None:
        feedback_request_id = _validate_feedback_request_id(feedback_request_id)
        expected_current_action_plan_id = _validate_expected_action_plan_id(
            expected_current_action_plan_id
        )
        category = validate_email_category_key(category)
        with self._connect() as db:
            db.execute("begin immediate")
            request = db.execute(
                "select * from email_feedback_requests where feedback_request_id=?",
                (feedback_request_id,),
            ).fetchone()
            if request is not None:
                same_intent = (
                    request["classification_id"] == row_id
                    and request["category"] == category
                    and request["expected_current_action_plan_id"]
                    == expected_current_action_plan_id
                )
                if not same_intent:
                    raise EmailClassificationConflict(
                        "feedback_request_id is already bound to another intent"
                    )
                return self._feedback_application(db, request, applied=False)
            row = db.execute(
                "select * from email_classifications where id=?", (row_id,)
            ).fetchone()
            if row is None:
                return None
            if row["status"] == EmailClassificationStatus.PENDING_FEEDBACK.value:
                if expected_current_action_plan_id is not None:
                    raise EmailClassificationConflict(
                        "pending confirmation requires a null expected ActionPlan"
                    )
                action_plan_version = 1
            elif (
                row["status"] == EmailClassificationStatus.PROCESSED.value
                and allow_processed_correction
            ):
                if not expected_current_action_plan_id:
                    raise EmailClassificationConflict(
                        "processed correction requires the current ActionPlan"
                    )
                if row["current_action_plan_id"] != expected_current_action_plan_id:
                    raise EmailClassificationConflict(
                        "email classification current ActionPlan changed"
                    )
                current_plan = db.execute(
                    """
                    select action_plan_version
                    from email_action_plans
                    where action_plan_id=? and classification_id=?
                    """,
                    (expected_current_action_plan_id, row_id),
                ).fetchone()
                if current_plan is None:
                    raise EmailActionPlanConflict(
                        "processed classification current ActionPlan is missing"
                    )
                action_plan_version = int(current_plan["action_plan_version"]) + 1
            else:
                raise EmailClassificationConflict(
                    "email classification is no longer pending feedback"
                )
            self._assert_no_email_reply_dispatch_in_flight(db, row_id)
            actions, action_parameters, config_version = self._category_action_snapshot(
                db,
                category=category,
                fallback_config_version=row["config_version"],
            )
            plan_created_at = created_at or datetime.now(timezone.utc)
            action_plan = build_versioned_email_action_plan(
                action_plan_version=action_plan_version,
                classification_id=row["id"],
                account_id=row["account_id"],
                category=category,
                classification_source="user",
                confidence=row["confidence"],
                model_id=row["model_id"],
                config_version=config_version,
                actions=actions,
                action_parameters=action_parameters,
                created_at=plan_created_at,
                action_authorizations=build_user_confirmation_authorizations(
                    category=category,
                    actions=actions,
                    action_parameters=action_parameters,
                    model_id=row["model_id"],
                    config_version=config_version,
                ),
            )
            applied_at = self._now()
            self._persist_action_plan(db, action_plan, now=applied_at)
            updated_count = db.execute(
                """
                update email_classifications
                set category=?, confirmed_category=?, status=?,
                    classification_source='user', config_version=?,
                    action_plan_json=?, current_action_plan_id=?,
                    legacy_processed_without_plan=0,
                    confirmed_at=?, updated_at=?
                where id=?
                  and status=?
                  and current_action_plan_id is ?
                """,
                (
                    category,
                    category,
                    EmailClassificationStatus.PROCESSED.value,
                    config_version,
                    action_plan.model_dump_json(),
                    action_plan.action_plan_id,
                    applied_at,
                    applied_at,
                    row_id,
                    row["status"],
                    expected_current_action_plan_id,
                ),
            ).rowcount
            if updated_count != 1:
                raise EmailClassificationConflict(
                    "email classification was changed concurrently"
                )
            db.execute(
                """
                insert into email_feedback_requests (
                    feedback_request_id, classification_id, category,
                    expected_current_action_plan_id, resulting_action_plan_id,
                    applied_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_request_id,
                    row_id,
                    category,
                    expected_current_action_plan_id,
                    action_plan.action_plan_id,
                    applied_at,
                ),
            )
            request = db.execute(
                "select * from email_feedback_requests where feedback_request_id=?",
                (feedback_request_id,),
            ).fetchone()
            assert request is not None
            return self._feedback_application(db, request, applied=True)

    @staticmethod
    def _category_action_snapshot(
        db: sqlite3.Connection,
        *,
        category: EmailCategoryKey,
        fallback_config_version: str,
    ) -> tuple[
        tuple[EmailAction, ...],
        dict[EmailAction, dict[str, object]],
        str,
    ]:
        selected_config = db.execute(
            """
            select actions_json, action_parameters_json, enabled, config_version
            from email_category_configs where category_key=?
            """,
            (category,),
        ).fetchone()
        if selected_config is None:
            return (), {}, fallback_config_version
        stored_actions = _json_load(
            selected_config["actions_json"],
            field="actions_json",
            expected_type=list,
        )
        stored_parameters = _json_load(
            selected_config["action_parameters_json"],
            field="action_parameters_json",
            expected_type=dict,
        )
        if not selected_config["enabled"]:
            return (), {}, selected_config["config_version"]
        return (
            tuple(EmailAction(value) for value in stored_actions),
            {
                EmailAction(action): dict(parameters)
                for action, parameters in stored_parameters.items()
            },
            selected_config["config_version"],
        )

    def append_action_plan_version(
        self,
        classification_id: int,
        action_plan: EmailActionPlan,
        *,
        confirmed_category: EmailCategoryKey,
    ) -> dict[str, Any]:
        """Append a correction plan and atomically make it current."""

        confirmed_category_value = validate_email_category_key(confirmed_category)
        if action_plan.classification_id != classification_id:
            raise EmailActionPlanConflict("ActionPlan classification does not match")
        if action_plan.category != confirmed_category_value:
            raise EmailActionPlanConflict(
                "ActionPlan category does not match correction"
            )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_classifications where id=?",
                (classification_id,),
            ).fetchone()
            if row is None:
                raise EmailActionPlanConflict(
                    f"unknown classification {classification_id}"
                )
            if row["account_id"] != action_plan.account_id:
                raise EmailActionPlanConflict("ActionPlan account does not match")
            self._assert_no_email_reply_dispatch_in_flight(db, classification_id)
            existing = db.execute(
                "select action_plan_id from email_action_plans where action_plan_id=?",
                (action_plan.action_plan_id,),
            ).fetchone()
            if existing is not None and (
                row["current_action_plan_id"] != action_plan.action_plan_id
            ):
                raise EmailActionPlanConflict(
                    "historical ActionPlan cannot replace the current version"
                )
            now = self._now()
            self._persist_action_plan(db, action_plan, now=now)
            if row["current_action_plan_id"] != action_plan.action_plan_id:
                db.execute(
                    """
                    update email_classifications
                    set category=?, confirmed_category=?, status='processed',
                        classification_source=?, confidence=?, model_id=?,
                        config_version=?, action_plan_json=?,
                        current_action_plan_id=?, legacy_processed_without_plan=0,
                        confirmed_at=?, updated_at=?
                    where id=?
                    """,
                    (
                        action_plan.category,
                        confirmed_category_value,
                        action_plan.classification_source,
                        action_plan.confidence,
                        action_plan.model_id,
                        action_plan.config_version,
                        action_plan.model_dump_json(),
                        action_plan.action_plan_id,
                        now,
                        now,
                        classification_id,
                    ),
                )
            updated = db.execute(
                "select * from email_classifications where id=?",
                (classification_id,),
            ).fetchone()
        assert updated is not None
        return self._classification_row(updated)

    @staticmethod
    def _assert_no_email_reply_dispatch_in_flight(
        db: sqlite3.Connection,
        classification_id: int,
    ) -> None:
        if (
            db.execute(
                """
            select 1 from email_reply_dispatch_claims
            where classification_id=? and status='dispatching'
            """,
                (classification_id,),
            ).fetchone()
            is not None
        ):
            raise EmailReplyDispatchConflict("email_reply_dispatch_in_flight")

    def append_action_attempt(
        self,
        *,
        action_id: str,
        attempt_number: int,
        status: str,
        provider_operation: str,
        provider_target: str,
        provider_result_id: str,
        error: str,
        started_at: str,
        finished_at: str,
    ) -> dict[str, Any]:
        _require_positive_int(attempt_number, field="attempt_number")
        if status not in _TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("attempt status must be done or failed")
        with self._connect() as db:
            db.execute("begin immediate")
            action = db.execute(
                "select * from email_actions where action_id=?", (action_id,)
            ).fetchone()
            if action is None:
                raise EmailActionAttemptConflict(
                    f"unknown direct email action {action_id}"
                )
            if action["action_type"] not in _DIRECT_ACTION_VALUES:
                raise EmailActionAttemptConflict(f"action {action_id} is not direct")
            if action["status"] not in _CURRENT_ACTION_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid current action status for {action_id}"
                )
            latest = db.execute(
                "select max(attempt_number) from email_action_attempts where action_id=?",
                (action_id,),
            ).fetchone()[0]
            expected_attempt = 1 if latest is None else int(latest) + 1
            if attempt_number != expected_attempt:
                if latest is not None and attempt_number <= latest:
                    raise EmailActionAttemptConflict(
                        f"attempt {attempt_number} already exists for {action_id}"
                    )
                raise EmailActionAttemptConflict(
                    f"attempt number must be {expected_attempt} for {action_id}"
                )
            cursor = db.execute(
                """
                insert into email_action_attempts (
                    action_id, attempt_number, status, provider_operation,
                    provider_target, provider_result_id, error, started_at,
                    finished_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    attempt_number,
                    status,
                    provider_operation,
                    provider_target,
                    provider_result_id,
                    error,
                    started_at,
                    finished_at,
                ),
            )
            db.execute(
                """
                update email_actions
                set status=?, attempt_count=?, started_at=?, finished_at=?,
                    provider_operation=?, provider_target=?, provider_result_id=?,
                    error=?, updated_at=?
                where action_id=?
                """,
                (
                    status,
                    attempt_number,
                    started_at,
                    finished_at,
                    provider_operation,
                    provider_target,
                    provider_result_id,
                    error,
                    self._now(),
                    action_id,
                ),
            )
            row = db.execute(
                "select * from email_action_attempts where id=?",
                (cursor.lastrowid,),
            ).fetchone()
        assert row is not None
        return dict(row)

    def claim_next_direct_action(
        self,
        *,
        claimed_at: str,
        account_ids: Sequence[str] | None = None,
    ) -> StoredEmailAction | None:
        """Claim one retryable action from the current immutable ActionPlan."""

        claimed_at = _required_utc_timestamp(claimed_at, field="claimed_at")
        if account_ids is not None:
            account_ids = tuple(
                account_id.strip()
                for account_id in account_ids
                if isinstance(account_id, str) and account_id.strip()
            )
            if not account_ids:
                return None
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity,
                       c.current_action_plan_id, p.actions_json,
                       p.action_plan_version
                from email_actions as a
                join email_classifications as c on c.id=a.classification_id
                join email_action_plans as p on p.action_plan_id=a.action_plan_id
                where c.status='processed'
                """
            ).fetchall()
            if not rows:
                return None
            rows_by_classification: dict[int, list[sqlite3.Row]] = {}
            for candidate in rows:
                rows_by_classification.setdefault(
                    int(candidate["classification_id"]),
                    [],
                ).append(candidate)
            eligible: list[sqlite3.Row] = []
            for siblings in rows_by_classification.values():
                if any(sibling["status"] == "processing" for sibling in siblings):
                    continue
                if account_ids is not None and any(
                    sibling["account_id"] not in account_ids for sibling in siblings
                ):
                    continue
                current_siblings = [
                    sibling
                    for sibling in siblings
                    if sibling["action_plan_id"] == sibling["current_action_plan_id"]
                ]
                unfinished = [
                    sibling
                    for sibling in current_siblings
                    if (
                        sibling["status"] == "pending"
                        or (
                            sibling["status"] == "failed"
                            and int(sibling["attempt_count"])
                            < DIRECT_ACTION_MAX_ATTEMPTS
                            and _retry_is_due(sibling["next_attempt_at"], claimed_at)
                        )
                    )
                    and self._direct_action_predecessors_done(db, sibling)
                    and self._direct_action_dependency_satisfied(db, sibling)
                ]
                if not unfinished:
                    continue
                eligible.append(
                    min(
                        unfinished,
                        key=lambda sibling: (
                            _DIRECT_ACTION_PRIORITY[sibling["action_type"]],
                            sibling["action_id"],
                        ),
                    )
                )
            if not eligible:
                return None
            row = min(
                eligible,
                key=lambda candidate: (
                    candidate["updated_at"],
                    candidate["classification_id"],
                    candidate["action_id"],
                ),
            )
            attempt_number = int(row["attempt_count"]) + 1
            updated = db.execute(
                """
                update email_actions
                set status='processing', started_at=?, finished_at='',
                    next_attempt_at='',
                    provider_operation='', provider_target='',
                    provider_result_id='', error='', updated_at=?
                where action_id=? and status=? and attempt_count=?
                """,
                (
                    claimed_at,
                    claimed_at,
                    row["action_id"],
                    row["status"],
                    row["attempt_count"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailActionAttemptConflict(
                    f"direct action claim changed for {row['action_id']}"
                )
            return self._claimed_direct_action(
                row,
                attempt_number=attempt_number,
                claimed_at=claimed_at,
            )

    def direct_action_ids_for_plan(self, action_plan_id: str) -> tuple[str, ...]:
        """Return only the direct action IDs owned by one immutable plan."""

        with self._connect() as db:
            rows = db.execute(
                "select action_id, action_type from email_actions where action_plan_id=?",
                (action_plan_id,),
            ).fetchall()
        action_types = {row["action_type"] for row in rows}

        def plan_priority(row: sqlite3.Row) -> tuple[int, str]:
            action_type = row["action_type"]
            if EmailAction.MOVE.value in action_types:
                historical_order = {
                    EmailAction.MOVE.value: 0,
                    EmailAction.FLAG_IMPORTANT.value: 1,
                    EmailAction.MARK_READ.value: 2,
                }
                if action_type in historical_order:
                    return historical_order[action_type], row["action_id"]
            if EmailAction.TRASH.value in action_types:
                historical_junk_order = {
                    EmailAction.TRASH.value: 0,
                    EmailAction.MARK_READ.value: 1,
                }
                if action_type in historical_junk_order:
                    return historical_junk_order[action_type], row["action_id"]
            return _DIRECT_ACTION_PRIORITY[action_type] + 10, row["action_id"]

        return tuple(
            row["action_id"]
            for row in sorted(rows, key=plan_priority)
        )

    def direct_action_statuses_for_plan(self, action_plan_id: str) -> dict[str, str]:
        with self._connect() as db:
            rows = db.execute(
                "select action_id, status from email_actions where action_plan_id=?",
                (action_plan_id,),
            ).fetchall()
        return {str(row["action_id"]): str(row["status"]) for row in rows}

    def direct_action_receipts_for_plan(
        self, action_plan_id: str
    ) -> tuple[dict[str, Any], ...]:
        """Return durable successful receipts for the plan's completed direct actions."""

        with self._connect() as db:
            rows = db.execute(
                """
                select a.action_id, a.action_type, a.status,
                       t.provider_operation, t.provider_target,
                       t.provider_result_id, t.finished_at
                from email_actions as a
                join email_action_attempts as t
                  on t.id=(
                    select max(latest.id)
                    from email_action_attempts as latest
                    where latest.action_id=a.action_id and latest.status='done'
                  )
                where a.action_plan_id=? and a.status='done'
                order by a.action_id
                """,
                (action_plan_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def list_missing_unsubscribe_action_tasks(self) -> list[dict[str, Any]]:
        """Find current durable unsubscribe plans without their stable task."""

        missing: list[dict[str, Any]] = []
        with self._connect() as db:
            rows = db.execute(
                """
                select c.*, p.action_plan_version, p.actions_json
                from email_classifications as c
                join email_action_plans as p
                  on p.action_plan_id=c.current_action_plan_id
                where c.status='processed'
                  and c.classification_source='model'
                  and instr(p.actions_json, '"unsubscribe"') > 0
                order by c.id
                """
            ).fetchall()
            for row in rows:
                action_identity = email_action_identity(
                    account_id=row["account_id"],
                    stable_message_identity=row["stable_message_identity"],
                    action_type=EmailAction.UNSUBSCRIBE,
                    action_plan_version=int(row["action_plan_version"]),
                )
                task = db.execute(
                    """
                    select 1 from reply_tasks
                    where channel='email' and trigger_message_id=?
                    """,
                    (action_identity,),
                ).fetchone()
                if task is None:
                    missing.append(self._classification_row(row))
        return missing

    def repair_current_action_plan(
        self, action_plan: EmailActionPlan
    ) -> dict[str, Any]:
        """Idempotently restore direct rows for one verified current plan."""

        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_classifications where id=?",
                (action_plan.classification_id,),
            ).fetchone()
            if row is None or row["current_action_plan_id"] != action_plan.action_plan_id:
                raise EmailActionPlanConflict("model ActionPlan is not current")
            self._persist_action_plan(db, action_plan, now=self._now())
            updated = db.execute(
                "select * from email_classifications where id=?",
                (action_plan.classification_id,),
            ).fetchone()
        assert updated is not None
        return self._classification_row(updated)

    def claim_direct_action(
        self, *, action_id: str, claimed_at: str
    ) -> StoredEmailAction | None:
        """Claim one exact current-plan action without selecting account work."""

        claimed_at = _required_utc_timestamp(claimed_at, field="claimed_at")
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError("action_id must be nonblank")
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                """
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity,
                       c.current_action_plan_id, p.actions_json,
                       p.action_plan_version
                from email_actions as a
                join email_classifications as c on c.id=a.classification_id
                join email_action_plans as p on p.action_plan_id=a.action_plan_id
                where a.action_id=? and c.status='processed'
                """,
                (action_id,),
            ).fetchone()
            if row is None or row["action_plan_id"] != row["current_action_plan_id"]:
                return None
            siblings = db.execute(
                "select action_type, status from email_actions where action_plan_id=?",
                (row["action_plan_id"],),
            ).fetchall()
            if any(item["status"] == "processing" for item in siblings):
                return None
            retryable = row["status"] == "pending" or (
                row["status"] == "failed"
                and int(row["attempt_count"]) < DIRECT_ACTION_MAX_ATTEMPTS
                and _retry_is_due(row["next_attempt_at"], claimed_at)
            )
            if (
                not retryable
                or not self._direct_action_predecessors_done(db, row)
                or not self._direct_action_dependency_satisfied(db, row)
            ):
                return None
            attempt_number = int(row["attempt_count"]) + 1
            updated = db.execute(
                """
                update email_actions
                set status='processing', started_at=?, finished_at='',
                    next_attempt_at='', provider_operation='', provider_target='',
                    provider_result_id='', error='', updated_at=?
                where action_id=? and status=? and attempt_count=?
                """,
                (claimed_at, claimed_at, row["action_id"], row["status"], row["attempt_count"]),
            ).rowcount
            if updated != 1:
                raise EmailActionAttemptConflict(
                    f"direct action claim changed for {row['action_id']}"
                )
            return self._claimed_direct_action(
                row, attempt_number=attempt_number, claimed_at=claimed_at
            )

    @staticmethod
    def _direct_action_predecessors_done(
        db: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        """Require semantic and explicit provider-safe dependencies to be done."""

        actions = _json_load(
            row["actions_json"], field="actions_json", expected_type=list
        )
        current_action = str(row["action_type"])
        if current_action not in actions:
            raise EmailPersistenceCorruption(
                "direct action is absent from its ActionPlan"
            )
        siblings = db.execute(
            "select action_type, status, parameters_json from email_actions "
            "where action_plan_id=?",
            (row["action_plan_id"],),
        ).fetchall()
        status_by_type = {
            str(sibling["action_type"]): str(sibling["status"])
            for sibling in siblings
        }
        try:
            typed_actions = tuple(
                EmailAction(action)
                for action in actions
                if action in _DIRECT_ACTION_VALUES
            )
            parameters_by_action = {
                EmailAction(str(sibling["action_type"])): _json_load(
                    sibling["parameters_json"],
                    field="parameters_json",
                    expected_type=dict,
                )
                for sibling in siblings
            }
            dependency_graph = effective_direct_action_dependencies(
                typed_actions,
                parameters_by_action,
            )
            dependencies = dependency_graph[EmailAction(current_action)]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmailPersistenceCorruption(str(exc)) from exc

        return all(
            status_by_type.get(action_type.value) == "done"
            for action_type in dependencies
        )

    @staticmethod
    def _direct_action_dependency_satisfied(
        db: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        """Require audited terminal evidence before a dependent junk Trash."""

        if row["action_type"] != EmailAction.TRASH.value:
            return True
        actions = _json_load(
            row["actions_json"], field="actions_json", expected_type=list
        )
        if EmailAction.UNSUBSCRIBE.value not in actions:
            return True
        action_identity = email_action_identity(
            account_id=row["account_id"],
            stable_message_identity=row["stable_message_identity"],
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(row["action_plan_version"]),
        )
        receipt = db.execute(
            """
            select outcome from email_unsubscribe_receipts
            where action_identity=? and action_plan_id=? and classification_id=?
            """,
            (action_identity, row["action_plan_id"], row["classification_id"]),
        ).fetchone()
        return receipt is not None and receipt["outcome"] in {
            "done",
            "already_unsubscribed",
            "skipped_no_reliable_entry",
        }

    @staticmethod
    def _claimed_direct_action(
        row: sqlite3.Row,
        *,
        attempt_number: int,
        claimed_at: str,
    ) -> StoredEmailAction:
        try:
            action_type = EmailAction(row["action_type"])
        except ValueError as exc:
            raise EmailPersistenceCorruption(
                f"invalid direct action type for {row['action_id']}"
            ) from exc
        if action_type.value not in _DIRECT_ACTION_VALUES:
            raise EmailPersistenceCorruption(
                f"non-direct action claimed for {row['action_id']}"
            )
        parameters = _json_load(
            row["parameters_json"],
            field="parameters_json",
            expected_type=dict,
        )
        parameters.pop(ACTION_DEPENDENCY_PARAMETER, None)
        return StoredEmailAction(
            action_id=row["action_id"],
            action_plan_id=row["action_plan_id"],
            classification_id=row["classification_id"],
            account_id=row["account_id"],
            action_type=action_type,
            parameters=parameters,
            config_version=row["config_version"],
            locator=StoredEmailLocator(
                account_id=row["account_id"],
                folder=row["folder"],
                uidvalidity=row["uidvalidity"],
                uid=row["uid"],
                rfc_message_id=row["rfc_message_id"] or None,
                thread_id=row["thread_id"] or None,
                stable_message_identity=row["stable_message_identity"],
            ),
            attempt_number=attempt_number,
            claim_started_at=claimed_at,
        )

    def complete_direct_action_attempt(
        self,
        action: StoredEmailAction,
        *,
        status: str,
        provider_operation: str,
        provider_target: str,
        provider_result_id: str,
        error: str,
        finished_at: str,
        retryable: bool = True,
        updated_locator: StoredEmailLocator | None = None,
    ) -> dict[str, Any]:
        """Append a terminal attempt and update its current projection atomically."""

        if status not in _TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("attempt status must be done or failed")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be a boolean")
        if not provider_operation:
            raise ValueError("provider_operation must be non-empty")
        if provider_target != action.locator.stable_message_identity:
            raise ValueError("provider_target must match the stable message identity")
        if status == "done" and (not provider_result_id or error):
            raise ValueError("done attempts require readback receipt and no error")
        if status == "failed" and not error:
            raise ValueError("failed attempts require an error")
        if updated_locator is not None:
            if status != "done":
                raise ValueError("only done attempts can update the provider locator")
            if (
                updated_locator.account_id != action.account_id
                or updated_locator.stable_message_identity
                != action.locator.stable_message_identity
                or not updated_locator.folder.strip()
                or updated_locator.uidvalidity <= 0
                or updated_locator.uid <= 0
            ):
                raise ValueError("updated provider locator does not match the action")
        finished_at = _required_utc_timestamp(finished_at, field="finished_at")
        next_attempt_at = _next_attempt_at(
            finished_at,
            attempt_number=action.attempt_number,
            retryable=retryable and status == "failed",
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                """
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity,
                       c.status as classification_status,
                       c.current_action_plan_id
                from email_actions as a
                join email_classifications as c on c.id=a.classification_id
                where a.action_id=?
                """,
                (action.action_id,),
            ).fetchone()
            if row is None:
                raise EmailActionAttemptConflict(
                    f"unknown direct email action {action.action_id}"
                )
            expected = self._claimed_direct_action(
                row,
                attempt_number=action.attempt_number,
                claimed_at=action.claim_started_at,
            )
            immutable_fields = (
                "action_id",
                "action_plan_id",
                "classification_id",
                "account_id",
                "action_type",
                "parameters",
                "config_version",
                "attempt_number",
            )
            if any(
                getattr(expected, field) != getattr(action, field)
                for field in immutable_fields
            ) or (
                expected.locator.stable_message_identity
                != action.locator.stable_message_identity
            ):
                raise EmailActionAttemptConflict(
                    f"immutable direct action changed for {action.action_id}"
                )
            if (
                row["status"] != "processing"
                or row["started_at"] != action.claim_started_at
                or int(row["attempt_count"]) != action.attempt_number - 1
            ):
                raise EmailActionAttemptConflict(
                    f"direct action claim changed for {action.action_id}"
                )
            if (
                row["classification_status"] != "processed"
                or row["current_action_plan_id"] != action.action_plan_id
            ):
                raise EmailActionAttemptConflict(
                    f"direct action is no longer current for {action.action_id}"
                )
            if updated_locator is not None:
                classification_updated = db.execute(
                    """
                    update email_classifications
                    set folder=?, uidvalidity=?, uid=?, updated_at=?
                    where id=? and account_id=? and stable_message_identity=?
                      and folder=? and uidvalidity=? and uid=?
                    """,
                    (
                        updated_locator.folder,
                        updated_locator.uidvalidity,
                        updated_locator.uid,
                        finished_at,
                        action.classification_id,
                        action.account_id,
                        action.locator.stable_message_identity,
                        action.locator.folder,
                        action.locator.uidvalidity,
                        action.locator.uid,
                    ),
                ).rowcount
                message_updated = db.execute(
                    """
                    update email_messages
                    set folder=?, uidvalidity=?, uid=?, updated_at=?
                    where account_id=? and stable_message_identity=?
                      and folder=? and uidvalidity=? and uid=?
                    """,
                    (
                        updated_locator.folder,
                        updated_locator.uidvalidity,
                        updated_locator.uid,
                        finished_at,
                        action.account_id,
                        action.locator.stable_message_identity,
                        action.locator.folder,
                        action.locator.uidvalidity,
                        action.locator.uid,
                    ),
                ).rowcount
                if classification_updated != 1 or message_updated != 1:
                    raise EmailActionAttemptConflict(
                        f"provider locator changed for {action.action_id}"
                    )
            cursor = db.execute(
                """
                insert into email_action_attempts (
                    action_id, attempt_number, status, provider_operation,
                    provider_target, provider_result_id, error, started_at,
                    finished_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action.action_id,
                    action.attempt_number,
                    status,
                    provider_operation,
                    provider_target,
                    provider_result_id,
                    error,
                    action.claim_started_at,
                    finished_at,
                ),
            )
            updated = db.execute(
                """
                update email_actions
                set status=?, attempt_count=?, finished_at=?,
                    next_attempt_at=?,
                    provider_operation=?, provider_target=?, provider_result_id=?,
                    error=?, updated_at=?
                where action_id=? and status='processing' and started_at=?
                  and attempt_count=?
                """,
                (
                    status,
                    action.attempt_number,
                    finished_at,
                    next_attempt_at,
                    provider_operation,
                    provider_target,
                    provider_result_id,
                    error,
                    finished_at,
                    action.action_id,
                    action.claim_started_at,
                    action.attempt_number - 1,
                ),
            ).rowcount
            if updated != 1:
                raise EmailActionAttemptConflict(
                    f"direct action claim changed for {action.action_id}"
                )
            attempt = db.execute(
                "select * from email_action_attempts where id=?",
                (cursor.lastrowid,),
            ).fetchone()
        assert attempt is not None
        return dict(attempt)

    def recover_stale_processing_actions(
        self,
        *,
        stale_before: str,
        recovered_at: str,
    ) -> int:
        """Record interrupted claims as failed so current actions can be retried."""

        stale_before = _required_utc_timestamp(
            stale_before,
            field="stale_before",
        )
        recovered_at = _required_utc_timestamp(
            recovered_at,
            field="recovered_at",
        )
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select a.*, c.stable_message_identity
                from email_actions as a
                join email_classifications as c on c.id=a.classification_id
                where a.status='processing' and a.started_at < ?
                order by a.action_id
                """,
                (stale_before,),
            ).fetchall()
            for row in rows:
                attempt_number = int(row["attempt_count"]) + 1
                db.execute(
                    """
                    insert into email_action_attempts (
                        action_id, attempt_number, status, provider_operation,
                        provider_target, provider_result_id, error, started_at,
                        finished_at
                    ) values (?, ?, 'failed', 'startup_recovery', ?, '',
                              'stale_processing_recovered', ?, ?)
                    """,
                    (
                        row["action_id"],
                        attempt_number,
                        row["stable_message_identity"],
                        row["started_at"],
                        recovered_at,
                    ),
                )
                updated = db.execute(
                    """
                    update email_actions
                set status='failed', attempt_count=?, finished_at=?,
                        next_attempt_at=?,
                        provider_operation='startup_recovery', provider_target=?,
                        provider_result_id='', error='stale_processing_recovered',
                        updated_at=?
                    where action_id=? and status='processing' and started_at=?
                      and attempt_count=?
                    """,
                    (
                        attempt_number,
                        recovered_at,
                        _next_attempt_at(
                            recovered_at,
                            attempt_number=attempt_number,
                            retryable=True,
                        ),
                        row["stable_message_identity"],
                        recovered_at,
                        row["action_id"],
                        row["started_at"],
                        row["attempt_count"],
                    ),
                ).rowcount
                if updated != 1:
                    raise EmailActionAttemptConflict(
                        f"direct action claim changed for {row['action_id']}"
                    )
            return len(rows)

    def list_action_attempts(self, action_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select * from email_action_attempts
                where action_id=? order by attempt_number
                """,
                (action_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_category_configs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "select * from email_category_configs order by category_key"
            ).fetchall()
        return [self._category_config_row(row) for row in rows]

    def get_category_config(self, category_key: str) -> dict[str, Any] | None:
        category_key = validate_email_category_key(category_key)
        with self._connect() as db:
            row = db.execute(
                "select * from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
        return None if row is None else self._category_config_row(row)

    def create_category_with_bindings(
        self,
        *,
        category_key: str,
        display_name: str,
        core_description: str,
        include: Sequence[str],
        exclude: Sequence[str],
        threshold: float,
        actions: tuple[EmailAction, ...],
        action_parameters: Mapping[EmailAction, Mapping[str, object]],
        enabled: bool,
        description_version: str,
        config_version: str,
        bindings: Sequence[VerifiedEmailFolderBinding],
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category_key)
        include_values, exclude_values = validate_category_descriptions(
            display_name=display_name,
            core_description=core_description,
            include=include,
            exclude=exclude,
            description_version=description_version,
        )
        _validate_config(
            category=category_key,
            threshold=threshold,
            actions=actions,
            action_parameters=action_parameters,
            config_version=config_version,
        )
        if any(
            not isinstance(binding, VerifiedEmailFolderBinding) for binding in bindings
        ):
            raise TypeError(
                "bindings must contain coordinator-produced verified folder binding values"
            )
        if category_key == "junk" and bindings:
            raise ValueError("junk does not accept a business-folder binding")
        for binding in bindings:
            _validate_verified_folder_binding_role(category_key, binding)
        account_ids = [binding.account_id for binding in bindings]
        if len(account_ids) != len(set(account_ids)):
            raise EmailFolderBindingConflict("duplicate category/account binding")
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            enabled_accounts = {
                row["account_id"]
                for row in db.execute(
                    "select account_id from email_accounts where enabled=1"
                )
            }
            existing_accounts = {
                row["account_id"]
                for row in db.execute("select account_id from email_accounts")
            }
            if not set(account_ids) <= existing_accounts:
                raise EmailFolderBindingConflict("folder binding account is unknown")
            active_accounts = {
                binding.account_id
                for binding in bindings
                if binding.binding_status == "active"
            }
            final_enabled = bool(enabled and enabled_accounts <= active_accounts)
            try:
                db.execute(
                    """
                    insert into email_category_configs (
                        category_key, display_name, core_description,
                        include_json, exclude_json, threshold, actions_json,
                        action_parameters_json, enabled, description_version,
                        config_version, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        category_key,
                        display_name,
                        core_description,
                        _json_dump(list(include_values)),
                        _json_dump(list(exclude_values)),
                        threshold,
                        _json_dump([action.value for action in actions]),
                        _json_dump(
                            {
                                action.value: dict(parameters)
                                for action, parameters in action_parameters.items()
                            }
                        ),
                        int(final_enabled),
                        description_version,
                        config_version,
                        now,
                    ),
                )
                for binding in bindings:
                    db.execute(
                        """
                        insert into email_category_folder_bindings (
                            account_id, category_key, provider_folder_id,
                            provider_folder_name, binding_status, last_verified_at
                        ) values (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            binding.account_id,
                            category_key,
                            binding.provider_folder_id,
                            binding.provider_folder_name,
                            binding.binding_status,
                            binding.last_verified_at,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email category or folder binding conflicts with stored state"
                ) from exc
            row = db.execute(
                "select * from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
        assert row is not None
        return self._category_config_row(row)

    @staticmethod
    def _bindings_cover_enabled_accounts(
        db: sqlite3.Connection,
        category_key: str,
    ) -> bool:
        return (
            db.execute(
                """
                select not exists (
                    select 1 from email_accounts as accounts
                    where accounts.enabled=1
                      and not exists (
                          select 1 from email_category_folder_bindings as bindings
                          where bindings.account_id=accounts.account_id
                            and bindings.category_key=?
                            and bindings.binding_status='active'
                      )
                )
                """,
                (category_key,),
            ).fetchone()[0]
            == 1
        )

    def update_category_descriptions(
        self,
        category_key: str,
        *,
        core_description: str,
        include: Sequence[str],
        exclude: Sequence[str],
        threshold: float,
        enabled: bool,
        description_version: str,
        config_version: str,
    ) -> dict[str, Any] | None:
        category_key = validate_email_category_key(category_key)
        include_values, exclude_values = validate_category_descriptions(
            display_name="unchanged",
            core_description=core_description,
            include=include,
            exclude=exclude,
            description_version=description_version,
        )
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise ValueError("threshold must be numeric")
        if not 0.0 <= float(threshold) <= 1.0:
            raise ValueError("threshold must be between zero and one")
        if type(config_version) is not str or not config_version.strip():
            raise ValueError("config_version must be non-empty")
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                "select 1 from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
            if existing is None:
                return None
            final_enabled = bool(
                enabled and self._bindings_cover_enabled_accounts(db, category_key)
            )
            db.execute(
                """
                update email_category_configs set
                    core_description=?, include_json=?, exclude_json=?,
                    threshold=?, enabled=?, description_version=?,
                    config_version=?, updated_at=?
                where category_key=?
                """,
                (
                    core_description,
                    _json_dump(list(include_values)),
                    _json_dump(list(exclude_values)),
                    float(threshold),
                    int(final_enabled),
                    description_version,
                    config_version,
                    self._now(),
                    category_key,
                ),
            )
            row = db.execute(
                "select * from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
        assert row is not None
        return self._category_config_row(row)

    def refresh_category_with_bindings(
        self,
        category_key: str,
        *,
        core_description: str,
        include: Sequence[str],
        exclude: Sequence[str],
        threshold: float,
        enabled: bool,
        description_version: str,
        config_version: str,
        bindings: Sequence[VerifiedEmailFolderBinding],
    ) -> dict[str, Any] | None:
        """Atomically replace enabled-account bindings and category configuration."""

        category_key = validate_email_category_key(category_key)
        include_values, exclude_values = validate_category_descriptions(
            display_name="unchanged",
            core_description=core_description,
            include=include,
            exclude=exclude,
            description_version=description_version,
        )
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise ValueError("threshold must be numeric")
        if not 0.0 <= float(threshold) <= 1.0:
            raise ValueError("threshold must be between zero and one")
        if type(config_version) is not str or not config_version.strip():
            raise ValueError("config_version must be non-empty")
        if isinstance(bindings, (str, bytes)) or not isinstance(bindings, Sequence):
            raise TypeError("bindings must be a sequence")
        if any(type(binding) is not VerifiedEmailFolderBinding for binding in bindings):
            raise TypeError("bindings must contain verified folder binding values")
        account_ids = tuple(binding.account_id for binding in bindings)
        if len(account_ids) != len(set(account_ids)):
            raise EmailFolderBindingConflict("duplicate category/account binding")
        for binding in bindings:
            _validate_verified_folder_binding_role(category_key, binding)
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                "select 1 from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
            if existing is None:
                return None
            enabled_accounts = {
                row["account_id"]
                for row in db.execute(
                    "select account_id from email_accounts where enabled=1"
                )
            }
            if set(account_ids) != enabled_accounts:
                raise EmailFolderBindingConflict(
                    "folder bindings must cover exactly the enabled accounts"
                )
            try:
                for binding in bindings:
                    db.execute(
                        """
                        insert into email_category_folder_bindings (
                            account_id, category_key, provider_folder_id,
                            provider_folder_name, binding_status, last_verified_at
                        ) values (?, ?, ?, ?, ?, ?)
                        on conflict(account_id, category_key) do update set
                            provider_folder_id=excluded.provider_folder_id,
                            provider_folder_name=excluded.provider_folder_name,
                            binding_status=excluded.binding_status,
                            last_verified_at=excluded.last_verified_at
                        """,
                        (
                            binding.account_id,
                            category_key,
                            binding.provider_folder_id,
                            binding.provider_folder_name,
                            binding.binding_status,
                            binding.last_verified_at,
                        ),
                    )
                final_enabled = bool(
                    enabled and self._bindings_cover_enabled_accounts(db, category_key)
                )
                db.execute(
                    """
                    update email_category_configs set
                        core_description=?, include_json=?, exclude_json=?,
                        threshold=?, enabled=?, description_version=?,
                        config_version=?, updated_at=?
                    where category_key=?
                    """,
                    (
                        core_description,
                        _json_dump(list(include_values)),
                        _json_dump(list(exclude_values)),
                        float(threshold),
                        int(final_enabled),
                        description_version,
                        config_version,
                        now,
                        category_key,
                    ),
                )
                self._validate_category_binding_completeness(db)
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email category or folder binding conflicts with stored state"
                ) from exc
            row = db.execute(
                "select * from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
        assert row is not None
        return self._category_config_row(row)

    def set_folder_binding_status(
        self,
        *,
        account_id: str,
        category_key: str,
        provider_folder_id: str,
        provider_folder_name: str,
        binding_status: str,
        last_verified_at: str,
        provider_folder_role: FolderRole,
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category_key)
        binding = VerifiedEmailFolderBinding(
            account_id=account_id,
            provider_folder_id=provider_folder_id,
            provider_folder_name=provider_folder_name,
            binding_status=binding_status,
            last_verified_at=last_verified_at,
            provider_folder_role=provider_folder_role,
        )
        _validate_verified_folder_binding_role(category_key, binding)
        with self._connect() as db:
            db.execute("begin immediate")
            try:
                updated = db.execute(
                    """
                    update email_category_folder_bindings set
                        provider_folder_id=?, provider_folder_name=?,
                        binding_status=?, last_verified_at=?
                    where account_id=? and category_key=?
                    """,
                    (
                        binding.provider_folder_id,
                        binding.provider_folder_name,
                        binding.binding_status,
                        binding.last_verified_at,
                        binding.account_id,
                        category_key,
                    ),
                ).rowcount
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email folder binding conflicts with stored state"
                ) from exc
            if updated != 1:
                raise EmailFolderBindingConflict("email folder binding does not exist")
            if binding.binding_status != "active":
                db.execute(
                    "update email_category_configs set enabled=0, updated_at=? "
                    "where category_key=?",
                    (self._now(), category_key),
                )
            row = db.execute(
                """
                select * from email_category_folder_bindings
                where account_id=? and category_key=?
                """,
                (binding.account_id, category_key),
            ).fetchone()
        assert row is not None
        return dict(row)

    def upsert_verified_folder_binding(
        self,
        category_key: str,
        binding: VerifiedEmailFolderBinding,
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category_key)
        if type(binding) is not VerifiedEmailFolderBinding:
            raise TypeError("binding must be a VerifiedEmailFolderBinding")
        _validate_verified_folder_binding_role(category_key, binding)
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            try:
                db.execute(
                    """
                    insert into email_category_folder_bindings (
                        account_id, category_key, provider_folder_id,
                        provider_folder_name, binding_status, last_verified_at
                    ) values (?, ?, ?, ?, ?, ?)
                    on conflict(account_id, category_key) do update set
                        provider_folder_id=excluded.provider_folder_id,
                        provider_folder_name=excluded.provider_folder_name,
                        binding_status=excluded.binding_status,
                        last_verified_at=excluded.last_verified_at
                    """,
                    (
                        binding.account_id,
                        category_key,
                        binding.provider_folder_id,
                        binding.provider_folder_name,
                        binding.binding_status,
                        binding.last_verified_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email folder binding conflicts with stored state"
                ) from exc
            complete = self._bindings_cover_enabled_accounts(db, category_key)
            if not complete:
                db.execute(
                    """
                    update email_category_configs
                    set enabled=0, updated_at=?
                    where category_key=? and enabled=1
                    """,
                    (now, category_key),
                )
            row = db.execute(
                """
                select * from email_category_folder_bindings
                where account_id=? and category_key=?
                """,
                (binding.account_id, category_key),
            ).fetchone()
            self._validate_category_binding_completeness(db)
        assert row is not None
        return dict(row)

    def list_account_folder_bindings(
        self,
        category_key: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            if category_key is None:
                rows = db.execute(
                    """
                    select * from email_category_folder_bindings
                    order by account_id, category_key
                    """
                ).fetchall()
            else:
                validated_key = validate_email_category_key(category_key)
                rows = db.execute(
                    """
                    select * from email_category_folder_bindings
                    where category_key=? order by account_id
                    """,
                    (validated_key,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_configs(self) -> list[dict[str, Any]]:
        """Return the established worker-facing shape during its later migration."""

        rows = self.list_category_configs()
        configured = [
            row for row in rows if row["config_version"] != SEEDED_CONFIG_VERSION
        ]
        return [self._legacy_config_row(row) for row in configured or rows]

    def upsert_config(
        self,
        *,
        category: EmailCategory,
        description: str,
        threshold: float,
        actions: tuple[EmailAction, ...],
        action_parameters: Mapping[EmailAction, Mapping[str, object]],
        enabled: bool,
        config_version: str,
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category.value)
        _validate_config(
            category=category_key,
            threshold=threshold,
            actions=actions,
            action_parameters=action_parameters,
            config_version=config_version,
        )
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                "select * from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
            if existing is None:
                raise ValueError("email category config does not exist")
            final_enabled = bool(
                enabled and self._bindings_cover_enabled_accounts(db, category_key)
            )
            db.execute(
                """
                update email_category_configs set
                    core_description=?, threshold=?, actions_json=?,
                    action_parameters_json=?, enabled=?, config_version=?,
                    updated_at=? where category_key=?
                """,
                (
                    description,
                    threshold,
                    _json_dump([action.value for action in actions]),
                    _json_dump(
                        {
                            action.value: dict(parameters)
                            for action, parameters in action_parameters.items()
                        }
                    ),
                    int(final_enabled),
                    config_version,
                    now,
                    category_key,
                ),
            )
            row = db.execute(
                "select * from email_category_configs where category_key=?",
                (category_key,),
            ).fetchone()
        assert row is not None
        return self._legacy_config_row(self._category_config_row(row))

    @staticmethod
    def _category_config_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            return category_config_row(row)
        except (CategoryConfigDataError, KeyError, TypeError, ValueError) as exc:
            raise EmailPersistenceCorruption(
                "invalid structured email category config"
            ) from exc

    @staticmethod
    def _legacy_config_row(row: Mapping[str, Any]) -> dict[str, Any]:
        return legacy_config_row(row)


def _optional_probability(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} must be numeric or None")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be between zero and one")
    return result


def _historical_outcome_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    if result["important"] is not None:
        result["important"] = bool(result["important"])
    return result


def _validate_config(
    *,
    category: str,
    threshold: float,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    config_version: str,
) -> None:
    if EmailAction.AUTO_REPLY in actions:
        raise ValueError("auto_reply is disabled")
    build_email_action_plan(
        classification_id=1,
        account_id="configuration-validation",
        category=category,
        classification_source="model",
        confidence=threshold,
        model_id="configuration-validation",
        config_version=config_version,
        actions=actions,
        action_parameters={
            action: dict(parameters) for action, parameters in action_parameters.items()
        },
        created_at=datetime.now(timezone.utc),
    )
