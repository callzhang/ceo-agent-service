"""Retained current Email52 non-action schema factory."""

import sqlite3

from app.email_classification_schema import create_current_email_classifications
from app.provider_state_definitions import PROVIDER_STATE_DDL, PROVIDER_STATE_INDEXES
from app.email_category_config import (
    ACTIVE_BINDING_INDEX_SQL,
    CATEGORY_CONFIG_TABLE_SQL,
    FOLDER_BINDING_TABLE_SQL,
)

_CURRENT_TABLE_SQL = (
    (
        "email_agent_classification_tasks",
        """CREATE TABLE email_agent_classification_tasks (
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
            )""",
    ),
    (
        "email_accounts",
        """CREATE TABLE email_accounts (
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
            )""",
    ),
    (
        "email_scan_cursors",
        """CREATE TABLE email_scan_cursors (
                account_id text not null,
                folder text not null,
                uidvalidity integer not null check(uidvalidity > 0),
                last_seen_uid integer not null check(last_seen_uid >= 0),
                last_success_at text not null default '',
                last_error text not null default '',
                primary key (account_id, folder)
            )""",
    ),
    (
        "email_messages",
        """CREATE TABLE email_messages (
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
            )""",
    ),
    (
        "email_training_snapshots",
        """CREATE TABLE email_training_snapshots (
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
            )""",
    ),
    (
        "email_training_snapshot_observations",
        """CREATE TABLE email_training_snapshot_observations (
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
                    check(sender_template_signature is null or length(sender_template_signature) = 64),
                explicit_matter_group text,
                group_key text not null check(length(group_key) = 64),
                observed_at text not null check(trim(observed_at) != ''),
                source text not null check(source in (
                    'natural', 'targeted', 'agent_auto_label', 'user_feedback'
                )),
                split text not null check(split in ('train', 'validation', 'test')),
                selected_for_training integer not null check(selected_for_training in (0, 1)),
                ordered_record_digest text not null check(length(ordered_record_digest) = 64),
                primary key(snapshot_id, account_id, stable_message_identity),
                foreign key(snapshot_id)
                    references email_training_snapshots(snapshot_id)
                    on delete restrict
            )""",
    ),
    (
        "email_historical_classification_history",
        """CREATE TABLE email_historical_classification_history (
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
            )""",
    ),
    (
        "email_classifier_runtime_samples",
        """CREATE TABLE email_classifier_runtime_samples (
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
            )""",
    ),
    (
        "email_model_promotion_configs",
        """CREATE TABLE email_model_promotion_configs (
                config_version text primary key not null check(trim(config_version) != ''),
                micro_f1_min real not null check(micro_f1_min >= 0 and micro_f1_min <= 1),
                category_precision_min real not null check(category_precision_min >= 0 and category_precision_min <= 1),
                category_validation_samples_min integer not null check(typeof(category_validation_samples_min) = 'integer' and category_validation_samples_min > 0),
                p95_latency_max_ms real not null check(p95_latency_max_ms > 0 and (p95_latency_max_ms - p95_latency_max_ms) is 0.0),
                created_at text not null check(trim(created_at) != '')
            ) without rowid
        """,
    ),
    (
        "email_model_mode_transitions",
        """CREATE TABLE email_model_mode_transitions (
                request_id text primary key not null check(trim(request_id) != ''),
                actor text not null check(trim(actor) != ''),
                from_mode text not null check(from_mode in ('agent_primary', 'model_primary')),
                to_mode text not null check(to_mode in ('agent_primary', 'model_primary')),
                from_model_id text check(from_model_id is null or trim(from_model_id) != ''),
                target_model_id text check(target_model_id is null or trim(target_model_id) != ''),
                promotion_config_version text not null check(trim(promotion_config_version) != ''),
                status text not null check(status in ('applied', 'rejected', 'failed')),
                reason text not null,
                created_at text not null check(trim(created_at) != ''),
                foreign key(promotion_config_version) references email_model_promotion_configs(config_version) on delete restrict
            ) without rowid
        """,
    ),
    (
        "email_category_description_revisions",
        """CREATE TABLE email_category_description_revisions (
                revision_id text primary key not null check(trim(revision_id) != ''),
                category_key text not null check(trim(category_key) != ''),
                config_version text not null check(trim(config_version) != ''),
                config_json text not null check(json_valid(config_json)),
                created_at text not null check(trim(created_at) != '')
            ) without rowid
        """,
    ),
    (
        "email_historical_traversals",
        """CREATE TABLE email_historical_traversals (
                account_id text not null,
                folder text not null,
                model_id text not null,
                uidvalidity integer,
                last_seen_uid integer not null default 0,
                updated_at text not null,
                primary key(account_id, folder, model_id)
            )""",
    ),
    (
        "email_historical_operations",
        """CREATE TABLE email_historical_operations (
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
            )""",
    ),
    (
        "email_historical_candidates",
        """CREATE TABLE email_historical_candidates (
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
            )""",
    ),
)
_CURRENT_INDEX_SQL = (
    (
        "idx_email_agent_classification_tasks_status",
        """CREATE INDEX idx_email_agent_classification_tasks_status
            on email_agent_classification_tasks(
                status, available_at, lease_expires_at, task_id
            )
            """,
    ),
    (
        "idx_email_classifications_status",
        """CREATE INDEX idx_email_classifications_status
            on email_classifications(status, updated_at desc, id desc)
            """,
    ),
    (
        "idx_email_classifications_account_status",
        """CREATE INDEX idx_email_classifications_account_status
            on email_classifications(account_id, status, updated_at desc)
            """,
    ),
    (
        "idx_email_classifications_account_locator",
        """CREATE INDEX idx_email_classifications_account_locator
            on email_classifications(
                account_id, folder, uidvalidity, status, updated_at desc, uid
            )
            """,
    ),
    (
        "idx_email_messages_account_locator",
        """CREATE INDEX idx_email_messages_account_locator
            on email_messages(account_id, folder, uidvalidity, uid)
            """,
    ),
    (
        "idx_email_training_observations_split",
        """CREATE INDEX idx_email_training_observations_split
            on email_training_snapshot_observations(
                snapshot_id, split, category_key, group_key
            )
            """,
    ),
    (
        "idx_email_training_observations_provider_truth",
        """CREATE INDEX idx_email_training_observations_provider_truth
            on email_training_snapshot_observations(
                account_id, stable_message_identity, observed_at desc, snapshot_id desc
            )
            """,
    ),
    (
        "idx_email_classifier_runtime_model_id",
        """CREATE INDEX idx_email_classifier_runtime_model_id
            on email_classifier_runtime_samples(model_id, id desc)
            """,
    ),
)
_CURRENT_TRIGGER_NAMES = (
    "trg_email_classification_status_insert",
    "trg_email_classification_status_update",
    "trg_email_classification_source_insert",
    "trg_email_classification_source_update",
    "trg_email_training_inclusion_invalidate",
    "trg_email_training_snapshots_immutable_update",
    "trg_email_training_snapshots_immutable_delete",
    "trg_email_training_observations_immutable_update",
    "trg_email_training_observations_immutable_delete",
    "trg_email_training_observations_require_unfrozen_snapshot",
    "trg_email_model_promotion_configs_immutable_update",
    "trg_email_model_promotion_configs_immutable_delete",
    "trg_email_model_promotion_configs_immutable_insert",
    "trg_email_model_mode_transitions_immutable_update",
    "trg_email_model_mode_transitions_immutable_delete",
    "trg_email_model_mode_transitions_immutable_insert",
    "trg_email_category_description_revisions_immutable_update",
    "trg_email_category_description_revisions_immutable_delete",
    "trg_email_category_description_revisions_immutable_insert",
)
_LEDGER_SQL = """CREATE TABLE email_schema_migrations (
                version integer primary key,
                applied_at text not null
            )"""


def _create_missing(db: sqlite3.Connection, kind: str, name: str, sql: str) -> None:
    if (
        db.execute(
            "select 1 from sqlite_schema where type=? and name=?", (kind, name)
        ).fetchone()
        is None
    ):
        db.execute(sql)


def create_current_email_non_action_schema(db: sqlite3.Connection) -> None:
    """Create retained Email tables, provider state, indexes, and triggers."""
    create_current_email_classifications(db)
    for name, sql in _CURRENT_TABLE_SQL:
        _create_missing(db, "table", name, sql)
    _create_missing(db, "table", "provider_state", PROVIDER_STATE_DDL)
    for sql in PROVIDER_STATE_INDEXES:
        name = sql.split(" INDEX ", 1)[1].split(" ON ", 1)[0]
        _create_missing(db, "index", name, sql)
    _create_missing(db, "table", "email_category_configs", CATEGORY_CONFIG_TABLE_SQL)
    _create_missing(
        db, "table", "email_category_folder_bindings", FOLDER_BINDING_TABLE_SQL
    )
    for name, sql in _CURRENT_INDEX_SQL:
        _create_missing(db, "index", name, sql)
    _create_missing(
        db,
        "index",
        "idx_email_category_folder_bindings_active_provider",
        ACTIVE_BINDING_INDEX_SQL,
    )
    from app.email_store import _REQUIRED_TRIGGER_SQL

    for name in _CURRENT_TRIGGER_NAMES:
        _create_missing(db, "trigger", name, _REQUIRED_TRIGGER_SQL[name])


def create_current_email_runtime_schema(db: sqlite3.Connection) -> None:
    """Install Email-owned retained tables for a fresh current config target."""
    from app.configs_definitions import CONFIG_FIELDS
    from app.state_definitions import STATE_FIELDS
    from app.task20_definitions import TASK20_NEW_KINDS
    from app.runs11_definitions import RUN11_NEW_KINDS
    from app.email_store import _REQUIRED_TRIGGER_SQL, _REQUIRED_TRIGGER_TABLES
    from app.provider_state_storage import create_provider_state_schema

    retired = {*CONFIG_FIELDS, *STATE_FIELDS, *TASK20_NEW_KINDS, *RUN11_NEW_KINDS, "email_training_snapshot_observations"}
    create_current_email_classifications(db)
    for name, sql in _CURRENT_TABLE_SQL:
        if name not in retired:
            _create_missing(db, "table", name, sql)
    create_provider_state_schema(db)
    retired_indexes = {
        "idx_email_agent_classification_tasks_status",
        "idx_email_training_observations_split",
        "idx_email_training_observations_provider_truth",
        "idx_email_classifier_runtime_model_id",
    }
    for name, sql in _CURRENT_INDEX_SQL:
        if name not in retired_indexes:
            _create_missing(db, "index", name, sql)
    for name in _CURRENT_TRIGGER_NAMES:
        if _REQUIRED_TRIGGER_TABLES.get(name, "email_classifications") not in retired:
            _create_missing(db, "trigger", name, _REQUIRED_TRIGGER_SQL[name])


def create_current_email_migration_ledger(db: sqlite3.Connection) -> None:
    """Create an empty ledger declaration; release coordination owns rows."""
    _create_missing(db, "table", "email_schema_migrations", _LEDGER_SQL)
