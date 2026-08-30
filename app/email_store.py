"""Account-aware persistence for email classification and planned actions.

This module owns durable email business state only. It never connects to a
provider and never creates Agent, Audit, reply-task, or run records.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
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
    _action_plan_identity,
    build_email_action_plan,
)


EMAIL_SCHEMA_VERSION = 2
_CLASSIFICATION_STATUSES = frozenset(status.value for status in EmailClassificationStatus)
_CLASSIFICATION_SOURCES = frozenset({"model", "user"})
_CURRENT_ACTION_STATUSES = frozenset({"pending", "processing", "done", "failed"})
_TERMINAL_ATTEMPT_STATUSES = frozenset({"done", "failed"})
_DIRECT_ACTION_VALUES = frozenset(action.value for action in DIRECT_ACTIONS)
_UNREDACTED_EMAIL = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"
)


class EmailClassificationConflict(RuntimeError):
    """The classification was already resolved by another confirmation."""


class EmailClassificationIdentityCollision(RuntimeError):
    """A classification ID is already bound to another stable identity."""


class EmailActionPlanConflict(RuntimeError):
    """An immutable ActionPlan ID or version conflicts with stored history."""


class EmailActionAttemptConflict(RuntimeError):
    """A direct-action attempt conflicts with append-only history."""


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
    except (TypeError, json.JSONDecodeError) as exc:
        raise EmailPersistenceCorruption(f"invalid {field} JSON") from exc
    if not isinstance(value, expected_type):
        raise EmailPersistenceCorruption(
            f"{field} must contain a JSON {expected_type.__name__}"
        )
    return value


def _require_positive_int(value: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")


def _direct_action_id(action_plan_id: str, action: EmailAction) -> str:
    digest = sha256(f"{action_plan_id}:{action.value}".encode("utf-8")).hexdigest()
    return f"email-action:{digest}"


def _plan_authorization_signature(plan: EmailActionPlan) -> tuple[object, ...]:
    """Compare authorization facts while ignoring scan-time identity fields."""

    return (
        plan.classification_id,
        plan.account_id,
        plan.category,
        plan.classification_source,
        plan.confidence,
        plan.model_id,
        plan.config_version,
        plan.actions,
        _json_dump(
            {
                action.value: dict(parameters)
                for action, parameters in plan.action_parameters.items()
            }
        ),
    )


def _with_action_plan_version(
    plan: EmailActionPlan,
    *,
    action_plan_version: int,
) -> EmailActionPlan:
    parameters = {
        action: dict(values) for action, values in plan.action_parameters.items()
    }
    return EmailActionPlan(
        action_plan_id=_action_plan_identity(
            action_plan_version=action_plan_version,
            classification_id=plan.classification_id,
            account_id=plan.account_id,
            category=plan.category,
            classification_source=plan.classification_source,
            confidence=plan.confidence,
            model_id=plan.model_id,
            config_version=plan.config_version,
            actions=plan.actions,
            action_parameters=parameters,
            created_at=plan.created_at,
        ),
        action_plan_version=action_plan_version,
        classification_id=plan.classification_id,
        account_id=plan.account_id,
        category=plan.category,
        classification_source=plan.classification_source,
        confidence=plan.confidence,
        model_id=plan.model_id,
        config_version=plan.config_version,
        actions=plan.actions,
        action_parameters=parameters,
        created_at=plan.created_at,
    )


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
            db.execute("pragma journal_mode = wal")
            db.execute("begin immediate")
            self._create_base_tables(db)
            self._migrate_prototype_schema(db)
            self._create_durable_tables(db)
            self._create_indexes_and_triggers(db)
            self._backfill_prototype_rows(db)
            self._validate_durable_state(db)
            db.execute(
                """
                insert into email_schema_migrations(version, applied_at)
                values (?, ?)
                on conflict(version) do nothing
                """,
                (EMAIL_SCHEMA_VERSION, self._now()),
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
        return {row["name"] for row in db.execute(f"pragma table_info({table})")}

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
    def _migrate_prototype_schema(cls, db: sqlite3.Connection) -> None:
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

    @staticmethod
    def _create_durable_tables(db: sqlite3.Connection) -> None:
        statements = (
            """
            create table if not exists email_schema_migrations (
                version integer primary key,
                applied_at text not null
            )
            """,
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
                set current_action_plan_id=?
                where id=? and current_action_plan_id is null
                """,
                (plan.action_plan_id, row["id"]),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _validate_durable_state(self, db: sqlite3.Connection) -> None:
        current_plan_references: list[tuple[int, str]] = []
        for row in db.execute(
            """
            select id, category, predicted_category, confirmed_category, status,
                   classification_source, probabilities_json, action_plan_json,
                   current_action_plan_id
            from email_classifications
            """
        ):
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
            if row["action_plan_json"] not in {"", "null"}:
                try:
                    plan = EmailActionPlan.model_validate_json(row["action_plan_json"])
                except ValueError as exc:
                    raise EmailPersistenceCorruption(
                        f"invalid action_plan_json for classification {row['id']}"
                    ) from exc
                if (
                    row["current_action_plan_id"]
                    and row["current_action_plan_id"] != plan.action_plan_id
                ):
                    raise EmailPersistenceCorruption(
                        f"current ActionPlan mismatch for classification {row['id']}"
                    )
            if row["current_action_plan_id"]:
                current_plan_references.append(
                    (row["id"], row["current_action_plan_id"])
                )

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

        plans: dict[str, EmailActionPlan] = {}
        versions: dict[int, list[int]] = {}
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
            versions.setdefault(plan.classification_id, []).append(
                plan.action_plan_version
            )
        for classification_id, stored_versions in versions.items():
            ordered = sorted(stored_versions)
            if ordered != list(range(1, ordered[-1] + 1)):
                raise EmailPersistenceCorruption(
                    f"non-contiguous ActionPlan versions for classification "
                    f"{classification_id}"
                )
        for classification_id, action_plan_id in current_plan_references:
            plan = plans.get(action_plan_id)
            if plan is None or plan.classification_id != classification_id:
                raise EmailPersistenceCorruption(
                    f"missing current ActionPlan for classification {classification_id}"
                )

        for row in db.execute("select category, actions_json, action_parameters_json from email_category_configs"):
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

        for row in db.execute(
            "select id, recipients_json, attachment_metadata_json from email_messages"
        ):
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

        for row in db.execute("select * from email_actions"):
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
        for row in db.execute("select id, status from email_action_attempts"):
            if row["status"] not in _TERMINAL_ATTEMPT_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid action attempt status for {row['id']}"
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

    def _append_changed_model_plan(
        self,
        db: sqlite3.Connection,
        *,
        existing: sqlite3.Row,
        classification: EmailClassification,
        now: str,
    ) -> None:
        """Version a materially changed model authorization on a rescan."""

        incoming = classification.action_plan
        if (
            incoming is None
            or classification.status is not EmailClassificationStatus.PROCESSED
            or classification.classification_source != "model"
            or existing["status"] != EmailClassificationStatus.PROCESSED.value
            or existing["classification_source"] != "model"
            or existing["action_plan_json"] in {"", "null"}
        ):
            return
        try:
            current = EmailActionPlan.model_validate_json(existing["action_plan_json"])
        except ValueError as exc:
            raise EmailPersistenceCorruption(
                f"invalid action_plan_json for classification {existing['id']}"
            ) from exc
        if _plan_authorization_signature(current) == _plan_authorization_signature(
            incoming
        ):
            return
        latest_version = db.execute(
            "select max(action_plan_version) from email_action_plans where classification_id=?",
            (classification.classification_id,),
        ).fetchone()[0]
        versioned = _with_action_plan_version(
            incoming,
            action_plan_version=int(latest_version or 0) + 1,
        )
        self._persist_action_plan(db, versioned, now=now)
        db.execute(
            """
            update email_classifications
            set category=?, predicted_category=?, confirmed_category=?,
                confidence=?, margin=?, probabilities_json=?, model_id=?,
                config_version=?, status=?, classification_source=?,
                action_plan_json=?, current_action_plan_id=?, updated_at=?
            where id=?
            """,
            (
                classification.category.value,
                classification.category.value,
                classification.category.value,
                classification.confidence,
                classification.margin,
                _json_dump(classification.probabilities),
                classification.model_id,
                classification.config_version,
                classification.status.value,
                classification.classification_source,
                versioned.model_dump_json(),
                versioned.action_plan_id,
                now,
                classification.classification_id,
            ),
        )

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
    ) -> None:
        _require_positive_int(uidvalidity, field="cursor_uidvalidity")
        if (
            isinstance(last_seen_uid, bool)
            or not isinstance(last_seen_uid, int)
            or last_seen_uid < 0
        ):
            raise ValueError("cursor_last_seen_uid must be a non-negative integer")
        db.execute(
            """
            insert into email_scan_cursors (
                account_id, folder, uidvalidity, last_seen_uid,
                last_success_at, last_error
            ) values (?, ?, ?, ?, ?, ?)
            on conflict(account_id, folder) do update set
                uidvalidity=excluded.uidvalidity,
                last_seen_uid=case
                    when email_scan_cursors.uidvalidity=excluded.uidvalidity
                    then max(email_scan_cursors.last_seen_uid, excluded.last_seen_uid)
                    else excluded.last_seen_uid
                end,
                last_success_at=excluded.last_success_at,
                last_error=excluded.last_error
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
    ) -> dict[str, Any]:
        """Atomically persist scan state and advance its folder cursor last."""

        _validate_model_text(model_text)
        locator = classification.provider_locator
        if (cursor_uidvalidity is None) != (cursor_last_seen_uid is None):
            raise ValueError("cursor UIDVALIDITY and last UID must be supplied together")
        if cursor_uidvalidity is not None and cursor_uidvalidity != locator.uidvalidity:
            raise ValueError("cursor UIDVALIDITY must match the message locator")
        if cursor_last_seen_uid is not None and cursor_last_seen_uid < locator.uid:
            raise ValueError("cursor cannot advance behind the persisted message UID")
        now = self._now()
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
                        set action_plan_json=?, current_action_plan_id=?
                        where id=?
                        """,
                        (
                            classification.action_plan.model_dump_json(),
                            classification.action_plan.action_plan_id,
                            classification.classification_id,
                        ),
                    )
            else:
                self._append_changed_model_plan(
                    db,
                    existing=existing,
                    classification=classification,
                    now=now,
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
                )
            row = db.execute(
                "select * from email_classifications where id=?",
                (classification.classification_id,),
            ).fetchone()
        assert row is not None
        return self._classification_row(row)

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

    def list_training_examples(self) -> list[dict[str, str]]:
        """Return only user-confirmed, redacted texts for local retraining."""

        with self._connect() as db:
            rows = db.execute(
                """
                select stable_message_identity, model_text,
                       coalesce(confirmed_category, category) as label
                from email_classifications
                where classification_source='user' and model_text != ''
                order by id asc
                """
            ).fetchall()
        return [
            {
                "message_id": row["stable_message_identity"],
                "model_text": row["model_text"],
                "label": row["label"],
            }
            for row in rows
        ]

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
                        current_action_plan_id=?, confirmed_at=?, updated_at=?
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
