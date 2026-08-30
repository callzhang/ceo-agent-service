"""Account-aware persistence for email classification and planned actions.

This module owns durable email business state only. It never connects to a
provider and never creates Agent, Audit, reply-task, or run records.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.email_classifier_contracts import (
    DIRECT_ACTIONS,
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    build_email_action_plan,
)


EMAIL_SCHEMA_VERSION = 5
_CLASSIFICATION_STATUSES = frozenset(status.value for status in EmailClassificationStatus)
_CLASSIFICATION_SOURCES = frozenset({"model", "user"})
_CURRENT_ACTION_STATUSES = frozenset({"pending", "processing", "done", "failed"})
_TERMINAL_ATTEMPT_STATUSES = frozenset({"done", "failed"})
_DIRECT_ACTION_VALUES = frozenset(action.value for action in DIRECT_ACTIONS)
_UNREDACTED_EMAIL = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"
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
        "category": ("text", True, None),
        "predicted_category": ("text", False, None),
        "confirmed_category": ("text", False, None),
        "confidence": ("real", True, None),
        "margin": ("real", True, None),
        "probabilities_json": ("text", True, None),
        "model_id": ("text", True, None),
        "config_version": ("text", True, None),
        "status": ("text", True, None),
        "classification_source": ("text", True, None),
        "action_plan_json": ("text", True, "'null'"),
        "current_action_plan_id": ("text", False, None),
        "included_in_model_id": ("text", False, None),
        "legacy_processed_without_plan": ("integer", True, "0"),
        "confirmed_at": ("text", True, "''"),
        "created_at": ("text", True, "current_timestamp"),
        "updated_at": ("text", True, "current_timestamp"),
    },
    "email_category_configs": {
        "category": ("text", False, None),
        "description": ("text", True, "''"),
        "threshold": ("real", True, None),
        "actions_json": ("text", True, None),
        "action_parameters_json": ("text", True, "'{}'"),
        "enabled": ("integer", True, "1"),
        "config_version": ("text", True, None),
        "updated_at": ("text", True, "current_timestamp"),
    },
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
}
_REQUIRED_TABLE_COLUMNS: Mapping[str, frozenset[str]] = {
    table: frozenset(columns)
    for table, columns in _REQUIRED_COLUMN_CONTRACTS.items()
}
_REQUIRED_TABLE_CHECKS: Mapping[str, tuple[str, ...]] = {
    "email_classifications": ("legacy_processed_without_plan in (0, 1)",),
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
        "json_valid(attachment_metadata_json)",
    ),
    "email_action_plans": (
        "action_plan_version > 0",
        "classification_source in ('model', 'user')",
        "confidence >= 0.0 and confidence <= 1.0",
        "json_valid(actions_json)",
        "json_valid(action_parameters_json)",
    ),
    "email_actions": (
        "action_type in ('label', 'mark_read', 'archive', 'move', 'trash')",
        "json_valid(parameters_json)",
        "status in ('pending', 'processing', 'done', 'failed')",
        "attempt_count >= 0",
    ),
    "email_action_attempts": (
        "attempt_number > 0",
        "status in ('done', 'failed')",
    ),
}
_REQUIRED_AUTOINCREMENT_COLUMNS = frozenset(
    {
        ("email_messages", "id"),
        ("email_action_attempts", "id"),
    }
)
_REQUIRED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "email_schema_migrations": ("version",),
    "email_classifications": ("id",),
    "email_category_configs": ("category",),
    "email_accounts": ("account_id",),
    "email_scan_cursors": ("account_id", "folder"),
    "email_messages": ("id",),
    "email_action_plans": ("action_plan_id",),
    "email_actions": ("action_id",),
    "email_action_attempts": ("id",),
}
_REQUIRED_UNIQUE_KEYS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "email_classifications": (("stable_message_identity",),),
    "email_messages": (("stable_message_identity",),),
    "email_action_plans": (("classification_id", "action_plan_version"),),
    "email_actions": (("action_plan_id", "action_type"),),
    "email_action_attempts": (("action_id", "attempt_number"),),
}
_REQUIRED_FOREIGN_KEYS: Mapping[
    str,
    tuple[tuple[str, str, str, str], ...],
] = {
    "email_action_plans": (
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_actions": (
        ("action_plan_id", "email_action_plans", "action_plan_id", "RESTRICT"),
        ("classification_id", "email_classifications", "id", "RESTRICT"),
    ),
    "email_action_attempts": (("action_id", "email_actions", "action_id", "RESTRICT"),),
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
    "idx_email_messages_account_locator": (
        "email_messages",
        ("account_id", "folder", "uidvalidity", "uid"),
    ),
    "idx_email_actions_status": (
        "email_actions",
        ("status", "updated_at", "action_id"),
    ),
}
_REQUIRED_TRIGGER_SQL: Mapping[str, str] = {
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
        when new.classification_source not in ('model', 'user')
        begin
            select raise(abort, 'invalid email classification source');
        end
    """,
    "trg_email_classification_source_update": """
        create trigger trg_email_classification_source_update
        before update of classification_source on email_classifications
        when new.classification_source not in ('model', 'user')
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
}


class EmailClassificationConflict(RuntimeError):
    """The classification was already resolved by another confirmation."""


class EmailTrainingInclusionConflict(RuntimeError):
    """Authoritative training samples cannot be marked as one atomic batch."""


class EmailTrainingConsistencyError(RuntimeError):
    """Registry manifests could not be proven restored after a DB failure."""


class EmailClassificationIdentityCollision(RuntimeError):
    """A classification ID is already bound to another stable identity."""


class EmailCursorConflict(RuntimeError):
    """The persisted scan cursor no longer matches the scanner's observation."""


class EmailActionPlanConflict(RuntimeError):
    """An immutable ActionPlan ID or version conflicts with stored history."""


class EmailActionAttemptConflict(RuntimeError):
    """A direct-action attempt conflicts with append-only history."""


class EmailAccountConflict(RuntimeError):
    """An account ID or unshared email address conflicts with stored config."""

    def __init__(self, code: str):
        super().__init__("email account configuration conflicts with stored state")
        self.code = code


class EmailPersistenceCorruption(RuntimeError):
    """Durable email state violates its JSON or enum contract."""


def _validate_model_text(model_text: str) -> None:
    lowered = model_text.lower()
    if (
        _UNREDACTED_EMAIL.search(model_text)
        or "http://" in lowered
        or "https://" in lowered
    ):
        raise ValueError("model_text must be redacted")


def _json_dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


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
            while end < len(value) and (
                value[end].isdigit() or value[end] == "."
            ):
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
                    if first not in {"check", "constraint", "foreign", "primary", "unique"}:
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
            self._create_base_tables(db)
            self._create_durable_tables(db)
            self._create_indexes_and_triggers(db)
            if latest_version < EMAIL_SCHEMA_VERSION:
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
                    "insert into email_schema_migrations(version, applied_at) values (?, ?)",
                    (EMAIL_SCHEMA_VERSION, self._now()),
                )
            self._validate_durable_state(db)

    @staticmethod
    def _read_schema_version(db: sqlite3.Connection) -> int | None:
        migration_tables = {
            _schema_identifier(row[0], field="sqlite_master table name")
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        if "email_schema_migrations" not in migration_tables:
            return None
        column_names = {
            _schema_identifier(
                row["name"], field="pragma table_info column name"
            )
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
                category text not null,
                predicted_category text,
                confirmed_category text,
                confidence real not null,
                margin real not null,
                probabilities_json text not null,
                model_id text not null,
                config_version text not null,
                status text not null,
                classification_source text not null,
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
    def _ensure_training_inclusion_column(cls, db: sqlite3.Connection) -> None:
        cls._ensure_column(
            db,
            table="email_classifications",
            column="included_in_model_id",
            declaration="text",
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
                    check(classification_source in ('model', 'user')),
                confidence real not null check(confidence >= 0.0 and confidence <= 1.0),
                model_id text not null,
                config_version text not null,
                actions_json text not null check(json_valid(actions_json)),
                action_parameters_json text not null
                    check(json_valid(action_parameters_json)),
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
                    check(action_type in ('label', 'mark_read', 'archive', 'move', 'trash')),
                parameters_json text not null check(json_valid(parameters_json)),
                config_version text not null,
                status text not null
                    check(status in ('pending', 'processing', 'done', 'failed')),
                attempt_count integer not null default 0 check(attempt_count >= 0),
                started_at text not null default '',
                finished_at text not null default '',
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
        )
        for statement in statements:
            db.execute(statement)

    @staticmethod
    def _create_indexes_and_triggers(db: sqlite3.Connection) -> None:
        statements = (
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
            when new.classification_source not in ('model', 'user')
            begin
                select raise(abort, 'invalid email classification source');
            end
            """,
            """
            create trigger if not exists trg_email_classification_source_update
            before update of classification_source on email_classifications
            when new.classification_source not in ('model', 'user')
            begin
                select raise(abort, 'invalid email classification source');
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
                plan = EmailActionPlan.model_validate_json(row["action_plan_json"])
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
                (plan.model_dump_json(), plan.action_plan_id, row["id"]),
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
            _schema_identifier(
                row["name"], field="pragma index_info column name"
            )
            for row in db.execute(f"pragma index_info({json.dumps(index_name)})")
        )

    @classmethod
    def _validate_schema_shape(cls, db: sqlite3.Connection) -> None:
        try:
            table_rows = {
                _schema_identifier(
                    row["name"], field="sqlite_master table name"
                ): row
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
            for table, required_columns in _REQUIRED_TABLE_COLUMNS.items():
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

            trigger_rows = {
                _schema_identifier(
                    row["name"], field="sqlite_master trigger name"
                ): row
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
                    != "email_classifications"
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
        for row in db.execute("select account_id, scan_folders_json from email_accounts"):
            folders = _json_load(
                row["scan_folders_json"],
                field="scan_folders_json",
                expected_type=list,
            )
            if not folders or any(
                not isinstance(folder, str) or not folder.strip()
                for folder in folders
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
            attachments = _json_load(
                row["attachment_metadata_json"],
                field="attachment_metadata_json",
                expected_type=list,
            )
            try:
                tuple(EmailAttachmentMetadata.model_validate(item) for item in attachments)
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid attachment_metadata_json for message {row['id']}"
                ) from exc
            messages[row["stable_message_identity"]] = row

        plans: dict[str, EmailActionPlan] = {}
        plans_by_classification: dict[int, list[EmailActionPlan]] = {}
        for row in db.execute("select * from email_action_plans"):
            actions = _json_load(
                row["actions_json"],
                field="actions_json",
                expected_type=list,
            )
            parameters = _json_load(
                row["action_parameters_json"],
                field="action_parameters_json",
                expected_type=dict,
            )
            try:
                plan = EmailActionPlan.model_validate_json(
                    _json_dump(
                        {
                            "action_plan_id": row["action_plan_id"],
                            "action_plan_version": row["action_plan_version"],
                            "classification_id": row["classification_id"],
                            "account_id": row["account_id"],
                            "category": row["category"],
                            "classification_source": row["classification_source"],
                            "confidence": row["confidence"],
                            "model_id": row["model_id"],
                            "config_version": row["config_version"],
                            "actions": actions,
                            "action_parameters": parameters,
                            "created_at": row["created_at"],
                        }
                    )
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid immutable ActionPlan {row['action_plan_id']}"
                ) from exc
            plans[plan.action_plan_id] = plan
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
                EmailCategory(row["category"])
                EmailCategory(row["predicted_category"])
                if row["confirmed_category"]:
                    EmailCategory(row["confirmed_category"])
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
            _json_load(
                row["probabilities_json"],
                field="probabilities_json",
                expected_type=dict,
            )
            message = messages.get(row["stable_message_identity"])
            if (
                message is None
                or message["account_id"] != row["account_id"]
                or message["stable_message_identity"]
                != row["stable_message_identity"]
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
                if row["classification_source"] != "model":
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} must use model source"
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
            if (
                row["classification_source"] == "user"
                and row["confirmed_category"] != row["category"]
            ):
                raise EmailPersistenceCorruption(
                    f"user-confirmed classification {row['id']} has inconsistent category"
                )
            if (
                row["classification_source"] == "model"
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
                current_plan.category.value,
                current_plan.classification_source,
                current_plan.confidence,
                current_plan.model_id,
                current_plan.config_version,
            )
            if plan_fields != expected_fields:
                raise EmailPersistenceCorruption(
                    f"current ActionPlan classification fields mismatch for {row['id']}"
                )
            if row["action_plan_json"] != current_plan.model_dump_json():
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
            if classification is None or classification["account_id"] != plan.account_id:
                raise EmailPersistenceCorruption(
                    f"ActionPlan {plan.action_plan_id} has no matching classification"
                )

        for row in db.execute(
            "select category, actions_json, action_parameters_json "
            "from email_category_configs"
        ):
            try:
                EmailCategory(row["category"])
            except ValueError as exc:
                raise EmailPersistenceCorruption("invalid configured email category") from exc
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
                raise EmailPersistenceCorruption("invalid configured email action") from exc

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
                plan = EmailActionPlan.model_validate_json(row["action_plan_json"])
            except ValueError as exc:
                raise EmailPersistenceCorruption("invalid action_plan_json") from exc
            action_plan = plan.model_dump(mode="json")
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
            "action_plan": action_plan,
            "current_action_plan_id": row["current_action_plan_id"],
            "confirmed_at": row["confirmed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

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
        locator = classification.provider_locator
        db.execute(
            """
            insert into email_messages (
                account_id, stable_message_identity, folder, uidvalidity, uid,
                rfc_message_id, thread_identity, sender, recipients_json,
                subject, normalized_text, preview, attachment_metadata_json,
                received_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(stable_message_identity) do update set
                folder=excluded.folder,
                uidvalidity=excluded.uidvalidity,
                uid=excluded.uid,
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
        expected = (
            plan.action_plan_version,
            plan.classification_id,
            plan.account_id,
            plan.category.value,
            plan.classification_source,
            plan.confidence,
            plan.model_id,
            plan.config_version,
            encoded_actions,
            encoded_parameters,
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
                created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.action_plan_id,
                plan.action_plan_version,
                plan.classification_id,
                plan.account_id,
                plan.category.value,
                plan.classification_source,
                plan.confidence,
                plan.model_id,
                plan.config_version,
                encoded_actions,
                encoded_parameters,
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
        sender: str = "",
        recipients: Sequence[str] = (),
        subject: str = "",
        normalized_text: str = "",
        preview: str = "",
        attachment_metadata: Sequence[EmailAttachmentMetadata] = (),
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
        now = self._now()
        locator = classification.provider_locator
        if (cursor_uidvalidity is None) != (cursor_last_seen_uid is None):
            raise ValueError("cursor UIDVALIDITY and last UID must be supplied together")
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
                received_at=received_at,
                now=now,
            )
            if existing is None:
                confirmed_category = (
                    classification.category.value
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
                        action_plan_json, current_action_plan_id, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'null', null, ?)
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
                        classification.category.value,
                        classification.category.value,
                        confirmed_category,
                        classification.confidence,
                        classification.margin,
                        _json_dump(classification.probabilities),
                        classification.model_id,
                        classification.config_version,
                        classification.status.value,
                        classification.classification_source,
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

    def create_account(
        self,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> dict[str, Any]:
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            if db.execute(
                "select 1 from email_accounts where account_id=?",
                (values["account_id"],),
            ).fetchone() is not None:
                raise EmailAccountConflict("account_id_conflict")
            self._assert_email_address_available(
                db,
                str(values["email_address"]),
                allow_shared_email=allow_shared_email,
            )
            now = self._next_account_timestamp()
            self._insert_account(db, values, created_at=now, updated_at=now)
            row = db.execute(
                "select * from email_accounts where account_id=?",
                (values["account_id"],),
            ).fetchone()
        assert row is not None
        return self._account_row(row)

    def update_account(
        self,
        account_id: str,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
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
            row = db.execute(
                "select * from email_accounts where account_id=?",
                (account_id,),
            ).fetchone()
        assert row is not None
        return self._account_row(row), previous

    def delete_account_if_unchanged(
        self,
        account_id: str,
        *,
        expected_updated_at: str,
    ) -> bool:
        with self._connect() as db:
            db.execute("begin immediate")
            cursor = db.execute(
                "delete from email_accounts where account_id=? and updated_at=?",
                (account_id, expected_updated_at),
            )
        return cursor.rowcount == 1

    def restore_account_if_unchanged(
        self,
        snapshot: Mapping[str, object],
        *,
        expected_updated_at: str,
    ) -> bool:
        with self._connect() as db:
            db.execute("begin immediate")
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
        return cursor.rowcount == 1

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
                select * from email_classifications
                where status=? order by updated_at desc, id desc
                limit ? offset ?
                """,
                (status.value, limit, offset),
            ).fetchall()
        return [self._classification_row(row) for row in rows], total

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
            validation_snapshots
            if inclusion_snapshots is None
            else inclusion_snapshots
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
            self._update_training_inclusion(
                db, inclusion_identities, model_id=model_id
            )
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
        self, row_id: int, category: EmailCategory
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_classifications where id=?", (row_id,)
            ).fetchone()
            if row is None:
                return None
            if row["status"] != EmailClassificationStatus.PENDING_FEEDBACK.value:
                raise EmailClassificationConflict(
                    "email classification is no longer pending feedback"
                )
            selected_config = db.execute(
                """
                select actions_json, action_parameters_json, enabled, config_version
                from email_category_configs where category=?
                """,
                (category.value,),
            ).fetchone()
            if selected_config is None:
                actions: tuple[EmailAction, ...] = ()
                action_parameters: dict[EmailAction, dict[str, object]] = {}
                config_version = row["config_version"]
            else:
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
                actions = (
                    tuple(EmailAction(value) for value in stored_actions)
                    if selected_config["enabled"]
                    else ()
                )
                action_parameters = (
                    {
                        EmailAction(action): dict(parameters)
                        for action, parameters in stored_parameters.items()
                    }
                    if selected_config["enabled"]
                    else {}
                )
                config_version = selected_config["config_version"]
            action_plan = build_email_action_plan(
                classification_id=row["id"],
                account_id=row["account_id"],
                category=category,
                classification_source="user",
                confidence=row["confidence"],
                model_id=row["model_id"],
                config_version=config_version,
                actions=actions,
                action_parameters=action_parameters,
                created_at=datetime.now(timezone.utc),
            )
            now = self._now()
            self._persist_action_plan(db, action_plan, now=now)
            updated_count = db.execute(
                """
                update email_classifications
                set category=?, confirmed_category=?, status=?,
                    classification_source='user', config_version=?,
                    action_plan_json=?, current_action_plan_id=?,
                    legacy_processed_without_plan=0,
                    confirmed_at=?, updated_at=?
                where id=? and status=?
                """,
                (
                    category.value,
                    category.value,
                    EmailClassificationStatus.PROCESSED.value,
                    config_version,
                    action_plan.model_dump_json(),
                    action_plan.action_plan_id,
                    now,
                    now,
                    row_id,
                    EmailClassificationStatus.PENDING_FEEDBACK.value,
                ),
            ).rowcount
            if updated_count != 1:
                raise EmailClassificationConflict(
                    "email classification was confirmed concurrently"
                )
            updated = db.execute(
                "select * from email_classifications where id=?", (row_id,)
            ).fetchone()
        assert updated is not None
        return self._classification_row(updated)

    def append_action_plan_version(
        self,
        classification_id: int,
        action_plan: EmailActionPlan,
        *,
        confirmed_category: EmailCategory,
    ) -> dict[str, Any]:
        """Append a correction plan and atomically make it current."""

        if action_plan.classification_id != classification_id:
            raise EmailActionPlanConflict("ActionPlan classification does not match")
        if action_plan.category != confirmed_category:
            raise EmailActionPlanConflict("ActionPlan category does not match correction")
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_classifications where id=?",
                (classification_id,),
            ).fetchone()
            if row is None:
                raise EmailActionPlanConflict(f"unknown classification {classification_id}")
            if row["account_id"] != action_plan.account_id:
                raise EmailActionPlanConflict("ActionPlan account does not match")
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
                        action_plan.category.value,
                        confirmed_category.value,
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

    def list_configs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "select * from email_category_configs order by category"
            ).fetchall()
        return [self._config_row(row) for row in rows]

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
        _validate_config(
            category=category,
            threshold=threshold,
            actions=actions,
            action_parameters=action_parameters,
            config_version=config_version,
        )
        now = self._now()
        with self._connect() as db:
            db.execute(
                """
                insert into email_category_configs (
                    category, description, threshold, actions_json,
                    action_parameters_json, enabled, config_version, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(category) do update set
                    description=excluded.description,
                    threshold=excluded.threshold,
                    actions_json=excluded.actions_json,
                    action_parameters_json=excluded.action_parameters_json,
                    enabled=excluded.enabled,
                    config_version=excluded.config_version,
                    updated_at=excluded.updated_at
                """,
                (
                    category.value,
                    description,
                    threshold,
                    _json_dump([action.value for action in actions]),
                    _json_dump(
                        {
                            action.value: dict(parameters)
                            for action, parameters in action_parameters.items()
                        }
                    ),
                    int(enabled),
                    config_version,
                    now,
                ),
            )
            row = db.execute(
                "select * from email_category_configs where category=?",
                (category.value,),
            ).fetchone()
        assert row is not None
        return self._config_row(row)

    @staticmethod
    def _config_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "category": row["category"],
            "description": row["description"],
            "threshold": row["threshold"],
            "actions": _json_load(
                row["actions_json"], field="actions_json", expected_type=list
            ),
            "action_parameters": _json_load(
                row["action_parameters_json"],
                field="action_parameters_json",
                expected_type=dict,
            ),
            "enabled": bool(row["enabled"]),
            "config_version": row["config_version"],
            "updated_at": row["updated_at"],
        }


def _validate_config(
    *,
    category: EmailCategory,
    threshold: float,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    config_version: str,
) -> None:
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
            action: dict(parameters)
            for action, parameters in action_parameters.items()
        },
        created_at=datetime.now(timezone.utc),
    )
