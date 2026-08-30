from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gc
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

import app.email_store as email_store_module
from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    _action_plan_identity,
    build_email_action_plan,
)
from app.email_store import (
    EmailActionAttemptConflict,
    EmailActionPlanConflict,
    EmailClassificationConflict,
    EmailClassificationIdentityCollision,
    EmailPersistenceCorruption,
    EmailTrainingInclusionConflict,
    EmailStore,
)


def _classification(
    *,
    status: EmailClassificationStatus,
    message_id: str = "msg-1",
    confidence: float = 0.93,
    model_id: str = "email/logistic/model-1",
    config_version: str = "email-v1",
    category: EmailCategory = EmailCategory.WORK,
    actions: tuple[EmailAction, ...] = (EmailAction.LABEL,),
    action_parameters: dict[EmailAction, dict[str, object]] | None = None,
    classification_id: int | None = None,
    stable_message_identity: str | None = None,
    folder: str = "INBOX",
    uidvalidity: int = 42,
    uid: int | None = None,
    rfc_message_id: str | None = None,
    thread_id: str | None = None,
) -> EmailClassification:
    generated_id = int.from_bytes(
        sha256(message_id.encode("utf-8")).digest()[:8], "big"
    ) & ((1 << 63) - 1) or 1
    classification_id = classification_id or generated_id
    uid = uid or classification_id
    if rfc_message_id is None and stable_message_identity is None:
        rfc_message_id = f"<{message_id}@example.com>"
    stable_message_identity = stable_message_identity or (
        f"dingtalk-account:message-id:{rfc_message_id}"
        if rfc_message_id is not None
        else f"dingtalk-account:imap:{folder}:{uidvalidity}:{uid}"
    )
    if action_parameters is None:
        action_parameters = {
            EmailAction.LABEL: {"labels": [category.value]},
        }
    action_plan = None
    if status is EmailClassificationStatus.PROCESSED:
        created_at = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
        action_plan = build_email_action_plan(
            classification_id=classification_id,
            account_id="dingtalk-account",
            category=category,
            classification_source="model",
            confidence=confidence,
            model_id=model_id,
            config_version=config_version,
            actions=actions,
            action_parameters=action_parameters,
            created_at=created_at,
        )
    return EmailClassification.model_validate(
        {
            "classification_id": classification_id,
            "stable_message_identity": (
                stable_message_identity
            ),
            "provider_locator": {
                "account_id": "dingtalk-account",
                "folder": folder,
                "uidvalidity": uidvalidity,
                "uid": uid,
                "rfc_message_id": rfc_message_id,
                "thread_id": thread_id,
            },
            "category": category,
            "confidence": confidence,
            "margin": 0.41,
            "probabilities": {"work": 0.93, "important": 0.52},
            "model_id": model_id,
            "config_version": config_version,
            "status": status,
            "classification_source": "model",
            "action_plan": action_plan,
        }
    )


def _versioned_plan(
    base: EmailActionPlan,
    *,
    version: int,
    category: EmailCategory,
    actions: tuple[EmailAction, ...],
    action_parameters: dict[EmailAction, dict[str, object]],
) -> EmailActionPlan:
    created_at = base.created_at + timedelta(minutes=version)
    identity = _action_plan_identity(
        action_plan_version=version,
        classification_id=base.classification_id,
        account_id=base.account_id,
        category=category,
        classification_source="user",
        confidence=base.confidence,
        model_id=base.model_id,
        config_version=f"email-v{version}",
        actions=actions,
        action_parameters=action_parameters,
        created_at=created_at,
    )
    return EmailActionPlan.model_validate(
        {
            "action_plan_id": identity,
            "action_plan_version": version,
            "classification_id": base.classification_id,
            "account_id": base.account_id,
            "category": category,
            "classification_source": "user",
            "confidence": base.confidence,
            "model_id": base.model_id,
            "config_version": f"email-v{version}",
            "actions": actions,
            "action_parameters": action_parameters,
            "created_at": created_at,
        }
    )


def _persist_scan(
    store: EmailStore,
    classification: EmailClassification,
    *,
    cursor_uidvalidity: int | None = None,
    cursor_last_seen_uid: int | None = None,
    expected_cursor_uidvalidity: int | None = None,
) -> dict[str, object]:
    locator = classification.provider_locator
    cursor_expectation = (
        {"expected_cursor_uidvalidity": expected_cursor_uidvalidity}
        if expected_cursor_uidvalidity is not None
        else {}
    )
    return store.persist_scan_result(
        classification,
        sender="sender@example.com",
        recipients=("recipient@example.com",),
        subject="Need a decision",
        normalized_text="__subject__need a decision",
        preview="Please review",
        attachment_metadata=(
            EmailAttachmentMetadata(
                filename="brief.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                inline=False,
            ),
        ),
        received_at="2026-08-29T15:59:00+00:00",
        model_text="__subject__need a decision",
        cursor_uidvalidity=cursor_uidvalidity or locator.uidvalidity,
        cursor_last_seen_uid=cursor_last_seen_uid or locator.uid,
        cursor_last_success_at="2026-08-29T16:00:00+00:00",
        **cursor_expectation,
    )


def _fetchall(path: Path, sql: str, parameters: tuple[object, ...] = ()):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, parameters).fetchall()


def _rewrite_required_identifier_case(database: Path, *, quote: bool = False) -> None:
    required_identifiers = set(email_store_module._REQUIRED_TABLE_COLUMNS)
    for columns in email_store_module._REQUIRED_TABLE_COLUMNS.values():
        required_identifiers.update(columns)
    required_identifiers.update(email_store_module._REQUIRED_INDEXES)
    required_identifiers.update(email_store_module._REQUIRED_TRIGGER_SQL)
    replacements = {
        identifier: (
            identifier.upper()
            if index % 2 == 0
            else "_".join(part.capitalize() for part in identifier.split("_"))
        )
        for index, identifier in enumerate(sorted(required_identifiers))
    }
    quote_styles = (("\"", "\""), ("`", "`"), ("[", "]"))
    sql_replacements = {
        identifier: (
            f"{quote_styles[index % len(quote_styles)][0]}{replacement}"
            f"{quote_styles[index % len(quote_styles)][1]}"
            if quote
            else replacement
        )
        for index, (identifier, replacement) in enumerate(replacements.items())
    }

    def rewrite_sql(value: str) -> str:
        return " ".join(
            sql_replacements.get(token, token)
            for token in email_store_module._schema_sql_tokens(value)
        )

    with sqlite3.connect(database) as db:
        schema_version = db.execute("pragma schema_version").fetchone()[0]
        rows = db.execute(
            "select rowid, name, tbl_name, sql from sqlite_master"
        ).fetchall()
        db.execute("pragma writable_schema = on")
        for rowid, name, table_name, sql in rows:
            db.execute(
                "update sqlite_master set name=?, tbl_name=?, sql=? where rowid=?",
                (
                    replacements.get(name, name),
                    replacements.get(table_name, table_name),
                    rewrite_sql(sql) if sql is not None else None,
                    rowid,
                ),
            )
        db.execute(f"pragma schema_version = {schema_version + 1}")
        db.commit()
        db.execute("pragma writable_schema = off")


def _corrupt_schema_object_name(
    database: Path,
    *,
    object_type: str,
    object_name: str,
) -> None:
    with sqlite3.connect(database) as db:
        schema_version = db.execute("pragma schema_version").fetchone()[0]
        db.execute("pragma writable_schema = on")
        db.execute(
            "update sqlite_master set name=? where type=? and name=?",
            (sqlite3.Binary(object_name.encode()), object_type, object_name),
        )
        db.execute(f"pragma schema_version = {schema_version + 1}")
        db.commit()
        db.execute("pragma writable_schema = off")


def _replace_email_scan_cursors(database: Path, *, columns_sql: str) -> None:
    with sqlite3.connect(database) as db:
        db.executescript(
            f"""
            alter table email_scan_cursors rename to old_email_scan_cursors;
            create table email_scan_cursors (
                {columns_sql},
                primary key (account_id, folder)
            );
            drop table old_email_scan_cursors;
            """
        )


def _replace_email_action_attempts(
    database: Path,
    *,
    attempt_number_declaration: str,
    status_declaration: str,
) -> None:
    with sqlite3.connect(database) as db:
        db.executescript(
            f"""
            alter table email_action_attempts rename to old_email_action_attempts;
            create table email_action_attempts (
                id integer primary key autoincrement,
                action_id text not null,
                attempt_number {attempt_number_declaration},
                status {status_declaration},
                provider_operation text not null,
                provider_target text not null,
                provider_result_id text not null,
                error text not null,
                started_at text not null,
                finished_at text not null,
                unique(action_id, attempt_number),
                foreign key(action_id) references email_actions(action_id)
                    on delete restrict
            );
            drop table old_email_action_attempts;
            """
        )


def _replace_email_actions(
    database: Path,
    *,
    action_type_declaration: str,
    status_declaration: str,
) -> None:
    with sqlite3.connect(database) as db:
        db.executescript(
            f"""
            drop table email_action_attempts;
            alter table email_actions rename to old_email_actions;
            create table email_actions (
                action_id text primary key,
                action_plan_id text not null,
                classification_id integer not null,
                account_id text not null,
                action_type {action_type_declaration},
                parameters_json text not null check(json_valid(parameters_json)),
                config_version text not null,
                status {status_declaration},
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
            );
            drop table old_email_actions;
            create index idx_email_actions_status
                on email_actions(status, updated_at, action_id);
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
            );
            """
        )


def _insert_account_with_scan_folders_json(database: Path, value: object) -> None:
    with sqlite3.connect(database) as db:
        db.execute("pragma ignore_check_constraints = on")
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
                "shape-test-account",
                "Shape test",
                "redacted@example.com",
                "imap.example.com",
                993,
                1,
                "redacted@example.com",
                "IMAP_SECRET_REFERENCE",
                "smtp.example.com",
                465,
                1,
                "redacted@example.com",
                "SMTP_SECRET_REFERENCE",
                1,
                value,
                60,
                "2026-08-29T16:00:00+00:00",
                "2026-08-29T16:00:00+00:00",
            ),
        )


def _create_prototype_database(
    database: Path,
    classification: EmailClassification,
    *,
    action_plan_json: str,
) -> None:
    locator = classification.provider_locator
    now = "2026-08-29T16:00:00+00:00"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            create table email_classifications (
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
                confidence real not null,
                margin real not null,
                probabilities_json text not null,
                model_id text not null,
                config_version text not null,
                status text not null,
                classification_source text not null,
                action_plan_json text not null default 'null',
                confirmed_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            create table email_category_configs (
                category text primary key,
                description text not null default '',
                threshold real not null,
                actions_json text not null,
                action_parameters_json text not null default '{}',
                enabled integer not null default 1,
                config_version text not null,
                updated_at text not null default current_timestamp
            );
            """
        )
        db.execute(
            """
            insert into email_classifications (
                id, account_id, folder, uidvalidity, uid, rfc_message_id,
                thread_id, stable_message_identity, sender, subject, preview,
                model_text, received_at, category, confidence, margin,
                probabilities_json, model_id, config_version, status,
                classification_source, action_plan_json, confirmed_at,
                created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "prototype-sender",
                "Prototype subject",
                "Prototype preview",
                "__subject__prototype",
                now,
                classification.category.value,
                classification.confidence,
                classification.margin,
                json.dumps(classification.probabilities),
                classification.model_id,
                classification.config_version,
                classification.status.value,
                classification.classification_source,
                action_plan_json,
                now if classification.status is EmailClassificationStatus.PROCESSED else "",
                now,
                now,
            ),
        )


def _create_v2_processed_without_plan_database(database: Path) -> None:
    now = "2026-08-29T16:00:00+00:00"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            create table email_schema_migrations (
                version integer primary key,
                applied_at text not null
            );
            create table email_classifications (
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
            );
            create table email_messages (
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
            );
            create table email_category_configs (
                category text primary key,
                description text not null default '',
                threshold real not null,
                actions_json text not null,
                action_parameters_json text not null default '{}',
                enabled integer not null default 1,
                config_version text not null,
                updated_at text not null default current_timestamp
            );
            create table email_retraining_state (
                state_key text primary key,
                state_json text not null
            );
            """
        )
        db.execute(
            "insert into email_schema_migrations values (?, ?)",
            (2, now),
        )
        db.execute(
            """
            insert into email_classifications (
                id, account_id, folder, uidvalidity, uid, rfc_message_id,
                thread_id, stable_message_identity, sender, subject, preview,
                model_text, received_at, category, predicted_category,
                confirmed_category, confidence, margin, probabilities_json,
                model_id, config_version, status, classification_source,
                action_plan_json, current_action_plan_id, confirmed_at,
                created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2002,
                "dingtalk-account",
                "INBOX",
                42,
                7,
                "<msg-v2@example.com>",
                "thread-v2",
                "dingtalk-account:message-id:<msg-v2@example.com>",
                "classification-sender",
                "Classification subject",
                "Classification preview",
                "__subject__v2 confirmed example",
                "2026-08-29T15:59:00+00:00",
                "important",
                "work",
                "important",
                0.61,
                0.09,
                '{"important":0.61,"work":0.52}',
                "email/logistic/v2-model",
                "v2-classification-config",
                "processed",
                "user",
                "null",
                None,
                now,
                now,
                now,
            ),
        )
        db.execute(
            """
            insert into email_messages (
                account_id, stable_message_identity, folder, uidvalidity, uid,
                rfc_message_id, thread_identity, sender, recipients_json,
                subject, normalized_text, preview, attachment_metadata_json,
                received_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dingtalk-account",
                "dingtalk-account:message-id:<msg-v2@example.com>",
                "INBOX",
                42,
                7,
                "<msg-v2@example.com>",
                "thread-v2",
                "message-snapshot-sender",
                '["recipient@example.com"]',
                "Original v2 message subject",
                "__subject__original v2 message",
                "Original v2 preview",
                "[]",
                "2026-08-29T15:59:00+00:00",
                now,
                now,
            ),
        )
        db.execute(
            """
            insert into email_category_configs (
                category, description, threshold, actions_json,
                action_parameters_json, enabled, config_version, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "important",
                "v2 important config",
                0.97,
                "[]",
                "{}",
                1,
                "v2-config",
                now,
            ),
        )
        db.execute(
            "insert into email_retraining_state values (?, ?)",
            ("current", '{"last_feedback_count":7}'),
        )


def _create_action_with_attempts(
    database: Path,
    statuses: tuple[str, ...],
) -> tuple[EmailStore, str]:
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    action_id = _fetchall(database, "select action_id from email_actions")[0][
        "action_id"
    ]
    for attempt_number, status in enumerate(statuses, start=1):
        store.append_action_attempt(
            action_id=action_id,
            attempt_number=attempt_number,
            status=status,
            provider_operation=f"operation-{attempt_number}",
            provider_target=f"target-{attempt_number}",
            provider_result_id=(f"receipt-{attempt_number}" if status == "done" else ""),
            error=(f"error-{attempt_number}" if status == "failed" else ""),
            started_at=f"2026-08-29T16:0{attempt_number}:00+00:00",
            finished_at=f"2026-08-29T16:0{attempt_number}:01+00:00",
        )
    return store, action_id


def test_email_cursor_conflict_is_a_clear_domain_error():
    conflict_type = getattr(email_store_module, "EmailCursorConflict", None)

    assert conflict_type is not None
    assert issubclass(conflict_type, RuntimeError)


def test_email_store_lists_pending_and_processed_separately(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        sender="sender@example.com",
        subject="Need a decision",
        preview="Please review",
    )
    second = _classification(
        status=EmailClassificationStatus.PROCESSED, message_id="msg-2"
    )
    store.upsert_classification(second)

    pending, pending_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=20, offset=0
    )
    processed, processed_total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=20, offset=0
    )

    assert pending_total == 1
    assert pending[0]["subject"] == "Need a decision"
    assert processed_total == 1
    assert processed[0]["status"] == "processed"
    assert processed[0]["model_id"] == "email/logistic/model-1"
    assert "model_version" not in processed[0]


def test_email_store_rejects_unredacted_model_text(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(ValueError, match="model_text must be redacted"):
        store.upsert_classification(
            _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
            model_text="sender@example.com https://private.example/message",
        )


def test_feedback_moves_a_message_to_processed_and_records_user_source(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )

    confirmed = store.confirm_classification(row["id"], EmailCategory.IMPORTANT)

    assert confirmed is not None
    assert confirmed["category"] == "important"
    assert confirmed["status"] == "processed"
    assert confirmed["classification_source"] == "user"
    assert confirmed["action_plan"]["category"] == "important"
    assert confirmed["action_plan"]["classification_source"] == "user"
    assert store.confirm_classification(999, EmailCategory.WORK) is None


def test_feedback_rebuilds_action_plan_for_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    store.upsert_config(
        category=EmailCategory.IMPORTANT,
        description="需要尽快处理",
        threshold=0.97,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["important"]}},
        enabled=True,
        config_version="important-v2",
    )
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        model_text="__subject__合同确认",
    )

    confirmed = store.confirm_classification(row["id"], EmailCategory.IMPORTANT)

    assert confirmed is not None
    assert confirmed["category"] == "important"
    assert confirmed["config_version"] == "important-v2"
    assert confirmed["action_plan"]["action_plan_version"] == 1
    assert confirmed["action_plan"]["classification_id"] == confirmed["id"]
    assert confirmed["action_plan"]["account_id"] == "dingtalk-account"
    assert confirmed["action_plan"]["category"] == "important"
    assert confirmed["action_plan"]["classification_source"] == "user"
    assert confirmed["action_plan"]["model_id"] == "email/logistic/model-1"
    assert confirmed["action_plan"]["config_version"] == "important-v2"
    assert confirmed["action_plan"]["actions"] == ["label"]
    assert confirmed["action_plan"]["action_parameters"] == {
        "label": {"labels": ["important"]}
    }
    assert "is_execution_authorization" not in confirmed["action_plan"]


def test_processed_email_cannot_be_confirmed_as_new_feedback(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PROCESSED)
    )

    with pytest.raises(EmailClassificationConflict):
        store.confirm_classification(row["id"], EmailCategory.IMPORTANT)


def test_concurrent_feedback_allows_one_confirmation_and_one_conflict(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        model_text="__subject__concurrent-confirmation",
    )
    ready = Barrier(2)

    def confirm(category: EmailCategory):
        ready.wait()
        try:
            return store.confirm_classification(row["id"], category)
        except EmailClassificationConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                confirm,
                (EmailCategory.IMPORTANT, EmailCategory.PERSONAL),
            )
        )

    confirmed = [result for result in results if isinstance(result, dict)]
    conflicts = [
        result
        for result in results
        if isinstance(result, EmailClassificationConflict)
    ]
    assert len(confirmed) == 1
    assert len(conflicts) == 1
    persisted, total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert persisted[0]["category"] == confirmed[0]["category"]
    assert len(store.list_training_examples()) == 1


def test_training_examples_exclude_pending_or_unconfirmed_user_rows_without_reopen(
    tmp_path: Path,
):
    database = tmp_path / "training-boundary.sqlite3"
    store = EmailStore(database)
    contaminated = _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="pending-contamination",
        ),
    )
    confirmed = _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="confirmed-training-example",
        ),
    )
    confirmed_row = store.confirm_classification(
        confirmed["id"],
        EmailCategory.IMPORTANT,
    )
    assert confirmed_row is not None
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_classifications
            set classification_source='user'
            where id=?
            """,
            (contaminated["id"],),
        )

    assert store.list_training_examples() == [
        {
            "message_id": confirmed_row["stable_message_identity"],
            "model_text": "__subject__need a decision",
            "label": "important",
        }
    ]


def test_training_inclusion_marks_exact_confirmed_samples_atomically(tmp_path: Path):
    store = EmailStore(tmp_path / "training-inclusion.sqlite3")
    rows = []
    for index, category in enumerate((EmailCategory.WORK, EmailCategory.JUNK), 1):
        row = store.upsert_classification(
            _classification(
                status=EmailClassificationStatus.PENDING_FEEDBACK,
                message_id=f"training-{index}",
                category=category,
            ),
            model_text=f"__subject__{category.value}-{index}",
        )
        rows.append(store.confirm_classification(row["id"], category))
    snapshots = store.list_unincluded_training_examples()

    assert len(snapshots) == 2
    assert all(len(row["sample_digest"]) == 64 for row in snapshots)
    store.mark_training_examples_included(snapshots, model_id="email-tfidf-lr-x-12345678")
    store.mark_training_examples_included(snapshots, model_id="email-tfidf-lr-x-12345678")

    assert store.list_unincluded_training_examples() == []
    assert {
        row["included_in_model_id"]
        for row in store.list_training_examples(include_inclusion=True)
    } == {
        "email-tfidf-lr-x-12345678"
    }


def test_training_inclusion_conflict_rolls_back_partial_batch(tmp_path: Path):
    store = EmailStore(tmp_path / "training-inclusion-conflict.sqlite3")
    identities = []
    for index, category in enumerate((EmailCategory.WORK, EmailCategory.JUNK), 1):
        row = store.upsert_classification(
            _classification(
                status=EmailClassificationStatus.PENDING_FEEDBACK,
                message_id=f"conflict-{index}",
                category=category,
            ),
            model_text=f"__subject__{category.value}-{index}",
        )
        confirmed = store.confirm_classification(row["id"], category)
        assert confirmed is not None
        identities.append(confirmed["stable_message_identity"])
    snapshots = store.list_unincluded_training_examples()
    store.mark_training_examples_included(
        [snapshots[0]], model_id="email-tfidf-lr-old-12345678"
    )

    with pytest.raises(EmailTrainingInclusionConflict):
        store.mark_training_examples_included(
            snapshots, model_id="email-tfidf-lr-new-87654321"
        )

    rows = {
        row["message_id"]: row
        for row in store.list_training_examples(include_inclusion=True)
    }
    assert rows[identities[0]]["included_in_model_id"] == "email-tfidf-lr-old-12345678"
    assert rows[identities[1]]["included_in_model_id"] is None


def test_training_inclusion_digest_cas_rejects_concurrent_correction_and_clears_old_model(
    tmp_path: Path,
):
    database = tmp_path / "training-cas.sqlite3"
    store = EmailStore(database)
    row = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="cas-sample",
            category=EmailCategory.WORK,
        ),
        model_text="__subject__original",
    )
    store.confirm_classification(row["id"], EmailCategory.WORK)
    snapshot = store.list_unincluded_training_examples()[0]
    store.mark_training_examples_included([snapshot], model_id="old-model")

    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set confirmed_category=?, category=? where id=?",
            ("important", "important", row["id"]),
        )

    latest = store.list_unincluded_training_examples()[0]
    assert latest["included_in_model_id"] is None
    assert latest["sample_digest"] != snapshot["sample_digest"]
    with pytest.raises(EmailTrainingInclusionConflict):
        store.mark_training_examples_included([snapshot], model_id="new-model")


def test_rescan_preserves_a_user_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    original = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )
    store.confirm_classification(original["id"], EmailCategory.IMPORTANT)

    rescanned = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )

    assert rescanned["id"] == original["id"]
    assert rescanned["category"] == "important"
    assert rescanned["status"] == "processed"
    assert rescanned["classification_source"] == "user"


def test_rescan_preserves_all_user_confirmed_action_plan_fields(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    original = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            confidence=0.61,
            model_id="email/logistic/model-v1",
        )
    )
    confirmed = store.confirm_classification(original["id"], EmailCategory.IMPORTANT)
    assert confirmed is not None

    rescanned = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            confidence=0.88,
            model_id="email/logistic/model-v2",
        )
    )

    plan = rescanned["action_plan"]
    assert plan is not None
    assert rescanned["id"] == plan["classification_id"]
    assert rescanned["account_id"] == plan["account_id"]
    assert rescanned["category"] == plan["category"]
    assert rescanned["classification_source"] == plan["classification_source"]
    assert rescanned["confidence"] == plan["confidence"] == 0.61
    assert rescanned["model_id"] == plan["model_id"] == "email/logistic/model-v1"
    assert rescanned["config_version"] == plan["config_version"]
    assert rescanned["action_plan"] == confirmed["action_plan"]


def test_email_store_persists_category_configuration(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")

    config = store.upsert_config(
        category=EmailCategory.SUBSCRIPTION,
        description="营销订阅和定期通讯",
        threshold=0.98,
        actions=(EmailAction.LABEL, EmailAction.UNSUBSCRIBE),
        action_parameters={EmailAction.LABEL: {"labels": ["subscription"]}},
        enabled=True,
        config_version="email-v2",
    )

    assert config["actions"] == ["label", "unsubscribe"]
    assert config["action_parameters"] == {
        "label": {"labels": ["subscription"]}
    }
    assert store.list_configs() == [config]


def test_fresh_schema_contains_account_aware_persistence_tables(tmp_path: Path):
    database = tmp_path / "fresh.sqlite3"

    EmailStore(database)

    table_names = {
        row["name"]
        for row in _fetchall(
            database,
            "select name from sqlite_master where type='table'",
        )
    }
    assert {
        "email_schema_migrations",
        "email_accounts",
        "email_scan_cursors",
        "email_messages",
        "email_classifications",
        "email_category_configs",
        "email_action_plans",
        "email_actions",
        "email_action_attempts",
    } <= table_names
    assert "reply_tasks" not in table_names
    assert "agent_runs" not in table_names

    account_columns = {
        row["name"] for row in _fetchall(database, "pragma table_info(email_accounts)")
    }
    assert {"imap_secret_reference", "smtp_secret_reference"} <= account_columns
    assert not {"password", "imap_secret", "smtp_secret"} & account_columns

    classification_columns = {
        row["name"]
        for row in _fetchall(database, "pragma table_info(email_classifications)")
    }
    assert {
        "account_id",
        "stable_message_identity",
        "predicted_category",
        "confirmed_category",
        "model_id",
        "current_action_plan_id",
    } <= classification_columns


def test_migration_preserves_prototype_feedback_config_and_unrelated_state(
    tmp_path: Path,
):
    database = tmp_path / "prototype.sqlite3"
    confirmed = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        confidence=0.61,
        model_id="email/logistic/prototype-v1",
    )
    now = "2026-08-29T16:00:00+00:00"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            create table email_classifications (
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
                confidence real not null,
                margin real not null,
                probabilities_json text not null,
                model_id text not null,
                config_version text not null,
                status text not null,
                classification_source text not null,
                action_plan_json text not null default 'null',
                confirmed_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            create table email_category_configs (
                category text primary key,
                description text not null default '',
                threshold real not null,
                actions_json text not null,
                action_parameters_json text not null default '{}',
                enabled integer not null default 1,
                config_version text not null,
                updated_at text not null default current_timestamp
            );
            create table email_retraining_state (
                state_key text primary key,
                state_json text not null
            );
            """
        )
        db.execute(
            """
            insert into email_classifications (
                id, account_id, folder, uidvalidity, uid, rfc_message_id,
                thread_id, stable_message_identity, sender, subject, preview,
                model_text, received_at, category, confidence, margin,
                probabilities_json, model_id, config_version, status,
                classification_source, action_plan_json, confirmed_at,
                created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                confirmed.classification_id,
                confirmed.provider_locator.account_id,
                confirmed.provider_locator.folder,
                confirmed.provider_locator.uidvalidity,
                confirmed.provider_locator.uid,
                confirmed.provider_locator.rfc_message_id,
                "thread-1",
                confirmed.stable_message_identity,
                "redacted-sender",
                "prototype subject",
                "prototype preview",
                "__subject__prototype",
                now,
                EmailCategory.IMPORTANT.value,
                confirmed.confidence,
                confirmed.margin,
                json.dumps(confirmed.probabilities),
                confirmed.model_id,
                confirmed.config_version,
                EmailClassificationStatus.PROCESSED.value,
                "user",
                "null",
                now,
                now,
                now,
            ),
        )
        db.execute(
            """
            insert into email_category_configs (
                category, description, threshold, actions_json,
                action_parameters_json, enabled, config_version, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "important",
                "prototype config",
                0.97,
                '["archive"]',
                "{}",
                1,
                "prototype-config-v1",
                now,
            ),
        )
        db.execute(
            "insert into email_retraining_state values (?, ?)",
            ("current", '{"last_feedback_count": 1}'),
        )

    store = EmailStore(database)

    processed, total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert processed[0]["classification_source"] == "user"
    assert processed[0]["predicted_category"] == "important"
    assert processed[0]["confirmed_category"] == "important"
    assert store.list_training_examples() == [
        {
            "message_id": confirmed.stable_message_identity,
            "model_text": "__subject__prototype",
            "label": "important",
        }
    ]
    assert store.list_configs()[0]["description"] == "prototype config"
    state = _fetchall(database, "select state_json from email_retraining_state")
    assert state[0]["state_json"] == '{"last_feedback_count": 1}'
    assert len(_fetchall(database, "select * from email_messages")) == 1
    legacy = _fetchall(
        database,
        "select legacy_processed_without_plan from email_classifications",
    )[0]
    assert legacy["legacy_processed_without_plan"] == 1


def test_prototype_migration_rejects_non_text_column_metadata_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "prototype-invalid-column-metadata.sqlite3"
    classification = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="prototype-invalid-column-metadata",
    )
    _create_prototype_database(database, classification, action_plan_json="null")
    with sqlite3.connect(database) as db:
        schema_before = db.execute(
            "select type, name, tbl_name, sql from sqlite_master order by type, name"
        ).fetchall()

    original = email_store_module._schema_identifier

    def inject_invalid_identifier(value: object, *, field: str) -> str:
        if field == "pragma table_info migration column name":
            value = sqlite3.Binary(b"predicted_category")
        return original(value, field=field)

    monkeypatch.setattr(
        email_store_module,
        "_schema_identifier",
        inject_invalid_identifier,
    )

    with pytest.raises(EmailPersistenceCorruption, match="schema identifier"):
        EmailStore(database)

    with sqlite3.connect(database) as db:
        schema_after = db.execute(
            "select type, name, tbl_name, sql from sqlite_master order by type, name"
        ).fetchall()
        assert db.execute(
            "select 1 from sqlite_master "
            "where type='table' and name='email_schema_migrations'"
        ).fetchone() is None
    assert schema_after == schema_before


def test_prototype_migration_recognizes_quoted_mixed_case_existing_columns(
    tmp_path: Path,
):
    database = tmp_path / "prototype-quoted-existing-columns.sqlite3"
    classification = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="prototype-quoted-existing-columns",
    )
    _create_prototype_database(database, classification, action_plan_json="null")
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            alter table email_classifications
                add column "PREDICTED_CATEGORY" text;
            alter table email_classifications
                add column `Confirmed_Category` text;
            alter table email_classifications
                add column [CURRENT_ACTION_PLAN_ID] text;
            alter table email_classifications
                add column "Legacy_Processed_Without_Plan"
                    integer not null default 0
                    check("Legacy_Processed_Without_Plan" in (0, 1));
            """
        )

    EmailStore(database)

    with sqlite3.connect(database) as db:
        columns = [
            row[1].casefold()
            for row in db.execute("pragma table_info(email_classifications)")
        ]
    for column in (
        "predicted_category",
        "confirmed_category",
        "current_action_plan_id",
        "legacy_processed_without_plan",
    ):
        assert columns.count(column) == 1


def test_email_store_migration_is_idempotent(tmp_path: Path):
    database = tmp_path / "idempotent.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )

    EmailStore(database)
    EmailStore(database)

    assert len(_fetchall(database, "select * from email_schema_migrations")) == 1
    assert len(_fetchall(database, "select * from email_messages")) == 1
    assert len(_fetchall(database, "select * from email_classifications")) == 1
    assert len(_fetchall(database, "select * from email_action_plans")) == 1
    assert len(_fetchall(database, "select * from email_actions")) == 1


def test_current_schema_initialization_preserves_delete_journal_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "current-schema-read-snapshot.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        journal_mode = db.execute("pragma journal_mode = delete").fetchone()[0]
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]
    assert journal_mode == "delete"

    statements: list[str] = []
    original_connect = EmailStore._connect

    def traced_connect(self: EmailStore) -> sqlite3.Connection:
        db = original_connect(self)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(EmailStore, "_connect", traced_connect)

    EmailStore(database)

    with sqlite3.connect(database) as db:
        journal_mode = db.execute("pragma journal_mode").fetchone()[0]
        schema_version_after = db.execute("pragma schema_version").fetchone()[0]
    normalized = [" ".join(statement.lower().split()) for statement in statements]
    assert journal_mode == "delete"
    assert schema_version_after == schema_version_before
    assert "begin" in normalized
    assert "begin immediate" not in normalized
    assert not any(
        statement.startswith("pragma journal_mode") for statement in normalized
    )
    read_pragma_prefixes = (
        "pragma table_info",
        "pragma index_list",
        "pragma index_info",
        "pragma foreign_key_list",
    )
    assert all(
        statement in {"begin", "commit"}
        or statement.startswith("select ")
        or statement.startswith(read_pragma_prefixes)
        for statement in normalized
    )
    assert all(
        any(statement.startswith(prefix) for statement in normalized)
        for prefix in read_pragma_prefixes
    )
    assert not any(
        statement.startswith(("create ", "alter ", "insert ", "update ", "delete "))
        for statement in normalized
    )


def test_current_schema_accepts_case_insensitive_required_bare_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "mixed-case-required-identifiers.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        assert db.execute("pragma journal_mode = delete").fetchone()[0] == "delete"
    _rewrite_required_identifier_case(database)
    with sqlite3.connect(database) as db:
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]

    statements: list[str] = []
    original_connect = EmailStore._connect

    def traced_connect(self: EmailStore) -> sqlite3.Connection:
        db = original_connect(self)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(EmailStore, "_connect", traced_connect)

    EmailStore(database)

    with sqlite3.connect(database) as db:
        assert db.execute("pragma journal_mode").fetchone()[0] == "delete"
        assert db.execute("pragma schema_version").fetchone()[0] == schema_version_before
    normalized = [" ".join(statement.lower().split()) for statement in statements]
    assert "begin immediate" not in normalized
    assert not any(
        statement.startswith(
            ("create ", "alter ", "insert ", "update ", "delete ", "pragma journal_mode")
        )
        for statement in normalized
    )


def test_current_schema_accepts_quoted_required_identifiers(tmp_path: Path):
    database = tmp_path / "quoted-required-identifiers.sqlite3"
    EmailStore(database)
    gc.collect()
    _rewrite_required_identifier_case(database, quote=True)

    EmailStore(database)


def test_schema_tokenizer_canonicalizes_quoted_identifiers_not_string_literals():
    assert email_store_module._schema_sql_tokens(
        '"STA""TUS" `STA``TUS` [STATUS] \'DONE\''
    ) == ('sta"tus', "sta`tus", "status", "'DONE'")


@pytest.mark.parametrize(
    ("object_type", "object_name"),
    (
        ("table", "email_messages"),
        ("trigger", "trg_email_classification_status_insert"),
    ),
)
def test_current_schema_rejects_non_text_sqlite_master_identifiers(
    tmp_path: Path,
    object_type: str,
    object_name: str,
):
    database = tmp_path / f"blob-{object_type}-name.sqlite3"
    EmailStore(database)
    gc.collect()
    _corrupt_schema_object_name(
        database,
        object_type=object_type,
        object_name=object_name,
    )

    with pytest.raises(EmailPersistenceCorruption, match="schema identifier"):
        EmailStore(database)


@pytest.mark.parametrize(
    "metadata_field",
    (
        "sqlite_master table name",
        "pragma table_info column name",
        "pragma index_list index name",
        "pragma index_info column name",
        "pragma foreign_key_list source column",
        "pragma foreign_key_list target table",
        "pragma foreign_key_list target column",
        "sqlite_master trigger name",
        "sqlite_master trigger table name",
    ),
)
def test_schema_metadata_paths_reject_non_text_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_field: str,
):
    database = tmp_path / f"invalid-{metadata_field.replace(' ', '-')}.sqlite3"
    EmailStore(database)
    original = email_store_module._schema_identifier

    def inject_invalid_identifier(value: object, *, field: str) -> str:
        if field == metadata_field:
            value = sqlite3.Binary(b"invalid")
        return original(value, field=field)

    monkeypatch.setattr(
        email_store_module,
        "_schema_identifier",
        inject_invalid_identifier,
    )

    with pytest.raises(EmailPersistenceCorruption, match="schema identifier"):
        EmailStore(database)


def test_foreign_key_on_delete_metadata_rejects_non_text_value(tmp_path: Path):
    database = tmp_path / "invalid-foreign-key-on-delete.sqlite3"
    EmailStore(database)

    class CorruptOnDeleteRow:
        def __init__(self, row: sqlite3.Row):
            self._row = row

        def __getitem__(self, key: object):
            if key == "on_delete":
                return 7
            return self._row[key]

    class CorruptOnDeleteStore(EmailStore):
        def _connect(self) -> sqlite3.Connection:
            db = super()._connect()

            def row_factory(cursor: sqlite3.Cursor, values: tuple[object, ...]):
                row = sqlite3.Row(cursor, values)
                if "on_delete" in row.keys():
                    return CorruptOnDeleteRow(row)
                return row

            db.row_factory = row_factory
            return db

    with pytest.raises(EmailPersistenceCorruption, match="schema text.*on_delete"):
        CorruptOnDeleteStore(database)


def test_current_schema_rejects_uppercase_action_status_literals(tmp_path: Path):
    database = tmp_path / "uppercase-action-status-literals.sqlite3"
    EmailStore(database)
    gc.collect()
    _replace_email_actions(
        database,
        action_type_declaration=(
            "text not null check(action_type in "
            "('label', 'mark_read', 'archive', 'move', 'trash'))"
        ),
        status_declaration=(
            "text not null check(status in "
            "('PENDING', 'PROCESSING', 'DONE', 'FAILED'))"
        ),
    )

    with pytest.raises(EmailPersistenceCorruption, match="required check.*email_actions"):
        EmailStore(database)


@pytest.mark.parametrize("populated", (False, True))
def test_current_schema_missing_required_column_is_domain_corruption(
    tmp_path: Path,
    populated: bool,
):
    database = tmp_path / f"missing-column-{populated}.sqlite3"
    store = EmailStore(database)
    if populated:
        _persist_scan(
            store,
            _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        )
    gc.collect()
    with sqlite3.connect(database) as db:
        db.execute("alter table email_messages drop column normalized_text")

    with pytest.raises(
        EmailPersistenceCorruption,
        match="email_messages.*normalized_text",
    ):
        EmailStore(database)


def test_current_schema_allows_unrelated_extra_tables_and_columns(tmp_path: Path):
    database = tmp_path / "extra-schema.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            alter table email_messages add column unrelated_extension
                text collate "NOCASE" default 'Mixed Case'
                check(length(unrelated_extension) >= 0);
            create table unrelated_email_extension (
                extension_id integer primary key,
                payload text
            );
            """
        )

    EmailStore(database)


def test_current_schema_rejects_weakened_cursor_column_declarations_and_checks(
    tmp_path: Path,
):
    database = tmp_path / "weakened-cursor-schema.sqlite3"
    EmailStore(database)
    gc.collect()
    _replace_email_scan_cursors(
        database,
        columns_sql="""
            account_id text not null,
            folder text not null,
            uidvalidity text,
            last_seen_uid text,
            last_success_at text,
            last_error text
        """,
    )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="email_scan_cursors.*uidvalidity",
    ):
        EmailStore(database)


@pytest.mark.parametrize(
    ("action_type_declaration", "status_declaration"),
    (
        (
            "text not null check(action_type in "
            "('label', 'mark_read', 'archive', 'move', 'trash', 'auto_reply'))",
            "text not null check(status in ('pending', 'processing', 'done', 'failed'))",
        ),
        (
            "text not null check(action_type in "
            "('label', 'mark_read', 'archive', 'move', 'trash'))",
            "text not null check(status in "
            "('pending', 'processing', 'done', 'failed', 'skipped'))",
        ),
    ),
)
def test_current_schema_rejects_weakened_direct_action_checks(
    tmp_path: Path,
    action_type_declaration: str,
    status_declaration: str,
):
    database = tmp_path / "weakened-direct-action-check.sqlite3"
    EmailStore(database)
    gc.collect()
    _replace_email_actions(
        database,
        action_type_declaration=action_type_declaration,
        status_declaration=status_declaration,
    )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="required check.*email_actions",
    ):
        EmailStore(database)


@pytest.mark.parametrize(
    ("column", "action_type_declaration", "status_declaration"),
    (
        (
            "action_type",
            "text collate nocase not null check(action_type in "
            "('label', 'mark_read', 'archive', 'move', 'trash'))",
            "text not null check(status in ('pending', 'processing', 'done', 'failed'))",
        ),
        (
            "status",
            "text not null check(action_type in "
            "('label', 'mark_read', 'archive', 'move', 'trash'))",
            "text collate nocase not null "
            "check(status in ('pending', 'processing', 'done', 'failed'))",
        ),
    ),
)
def test_current_schema_rejects_collation_on_required_direct_action_columns(
    tmp_path: Path,
    column: str,
    action_type_declaration: str,
    status_declaration: str,
):
    database = tmp_path / f"collated-email-actions-{column}.sqlite3"
    EmailStore(database)
    gc.collect()
    _replace_email_actions(
        database,
        action_type_declaration=action_type_declaration,
        status_declaration=status_declaration,
    )

    with pytest.raises(
        EmailPersistenceCorruption,
        match=rf"email_actions.*{column}",
    ):
        EmailStore(database)


@pytest.mark.parametrize(
    ("attempt_number_declaration", "status_declaration"),
    (
        (
            "integer not null check(attempt_number >= 0)",
            "text not null check(status in ('done', 'failed'))",
        ),
        (
            "integer not null check(attempt_number > 0)",
            "text not null check(status in ('processing', 'done', 'failed'))",
        ),
    ),
)
def test_current_schema_rejects_weakened_action_attempt_checks(
    tmp_path: Path,
    attempt_number_declaration: str,
    status_declaration: str,
):
    database = tmp_path / "weakened-action-attempt-check.sqlite3"
    EmailStore(database)
    gc.collect()
    _replace_email_action_attempts(
        database,
        attempt_number_declaration=attempt_number_declaration,
        status_declaration=status_declaration,
    )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="required check.*email_action_attempts",
    ):
        EmailStore(database)


def test_current_schema_rejects_collation_on_required_action_attempt_status(
    tmp_path: Path,
):
    database = tmp_path / "collated-email-action-attempt-status.sqlite3"
    EmailStore(database)
    gc.collect()
    _replace_email_action_attempts(
        database,
        attempt_number_declaration="integer not null check(attempt_number > 0)",
        status_declaration=(
            "text collate nocase not null check(status in ('done', 'failed'))"
        ),
    )

    with pytest.raises(
        EmailPersistenceCorruption,
        match=r"email_action_attempts.*status",
    ):
        EmailStore(database)


def test_current_schema_rejects_wrong_account_column_nullability(tmp_path: Path):
    database = tmp_path / "wrong-account-nullability.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            alter table email_accounts rename to old_email_accounts;
            create table email_accounts (
                account_id text primary key,
                display_name text,
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
            );
            drop table old_email_accounts;
            """
        )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="email_accounts.*display_name",
    ):
        EmailStore(database)


@pytest.mark.parametrize(
    "damaged_declaration",
    (
        "threshold text not null",
        "description text not null default 'missing-default-contract'",
    ),
)
def test_current_schema_rejects_wrong_config_column_type_or_default(
    tmp_path: Path,
    damaged_declaration: str,
):
    database = tmp_path / "wrong-config-declaration.sqlite3"
    EmailStore(database)
    gc.collect()
    description_declaration = (
        damaged_declaration
        if damaged_declaration.startswith("description")
        else "description text not null default ''"
    )
    threshold_declaration = (
        damaged_declaration
        if damaged_declaration.startswith("threshold")
        else "threshold real not null"
    )
    with sqlite3.connect(database) as db:
        db.executescript(
            f"""
            alter table email_category_configs rename to old_email_category_configs;
            create table email_category_configs (
                category text primary key,
                {description_declaration},
                {threshold_declaration},
                actions_json text not null,
                action_parameters_json text not null default '{{}}',
                enabled integer not null default 1,
                config_version text not null,
                updated_at text not null default current_timestamp
            );
            drop table old_email_category_configs;
            """
        )

    expected_column = (
        "description"
        if damaged_declaration.startswith("description")
        else "threshold"
    )
    with pytest.raises(
        EmailPersistenceCorruption,
        match=rf"email_category_configs.*{expected_column}",
    ):
        EmailStore(database)


def test_durable_validation_boundary_normalizes_missing_row_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "missing-row-field.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    gc.collect()
    with sqlite3.connect(database) as db:
        db.execute("alter table email_messages drop column thread_identity")
    monkeypatch.setattr(
        EmailStore,
        "_validate_schema_shape",
        staticmethod(lambda _db: None),
    )

    with pytest.raises(EmailPersistenceCorruption, match="missing a required field"):
        EmailStore(database)


@pytest.mark.parametrize(
    ("damage_sql", "match"),
    (
        ("drop table email_scan_cursors", "email_scan_cursors"),
        ("drop index idx_email_actions_status", "idx_email_actions_status"),
        (
            "drop trigger trg_email_classification_status_insert",
            "trg_email_classification_status_insert",
        ),
    ),
)
def test_current_schema_missing_required_structure_is_domain_corruption(
    tmp_path: Path,
    damage_sql: str,
    match: str,
):
    database = tmp_path / f"missing-structure-{match}.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.execute(damage_sql)

    with pytest.raises(EmailPersistenceCorruption, match=match):
        EmailStore(database)


def test_current_schema_missing_required_foreign_key_is_domain_corruption(
    tmp_path: Path,
):
    database = tmp_path / "missing-action-attempt-foreign-key.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            alter table email_action_attempts rename to old_email_action_attempts;
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
                unique(action_id, attempt_number)
            );
            drop table old_email_action_attempts;
            """
        )

    with pytest.raises(
        EmailPersistenceCorruption, match="foreign key.*email_action_attempts"
    ):
        EmailStore(database)


def test_current_schema_missing_required_primary_key_is_domain_corruption(
    tmp_path: Path,
):
    database = tmp_path / "missing-cursor-primary-key.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            alter table email_scan_cursors rename to old_email_scan_cursors;
            create table email_scan_cursors as
                select * from old_email_scan_cursors where false;
            drop table old_email_scan_cursors;
            """
        )

    with pytest.raises(
        EmailPersistenceCorruption, match="primary key.*email_scan_cursors"
    ):
        EmailStore(database)


def test_current_schema_missing_required_unique_key_is_domain_corruption(
    tmp_path: Path,
):
    database = tmp_path / "missing-message-unique-key.sqlite3"
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            alter table email_messages rename to old_email_messages;
            create table email_messages (
                id integer primary key autoincrement,
                account_id text not null,
                stable_message_identity text not null,
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
            );
            drop table old_email_messages;
            create index idx_email_messages_account_locator
                on email_messages(account_id, folder, uidvalidity, uid);
            """
        )

    with pytest.raises(EmailPersistenceCorruption, match="unique key.*email_messages"):
        EmailStore(database)


def test_invalid_utf8_json_blob_is_domain_corruption(tmp_path: Path):
    database = tmp_path / "invalid-utf8-json.sqlite3"
    EmailStore(database)
    gc.collect()
    _insert_account_with_scan_folders_json(database, sqlite3.Binary(b"\xff"))

    with pytest.raises(EmailPersistenceCorruption, match="scan_folders_json"):
        EmailStore(database)


def test_malformed_schema_version_is_domain_corruption(tmp_path: Path):
    database = tmp_path / "malformed-schema-version.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            create table email_schema_migrations (
                version text primary key,
                applied_at text not null
            );
            insert into email_schema_migrations values (
                'not-an-integer',
                '2026-08-29T16:00:00+00:00'
            );
            """
        )

    with pytest.raises(EmailPersistenceCorruption, match="schema version"):
        EmailStore(database)


def test_schema_version_table_missing_version_column_is_domain_corruption(
    tmp_path: Path,
):
    database = tmp_path / "missing-schema-version-column.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            create table email_schema_migrations (
                schema_revision integer primary key,
                applied_at text not null
            );
            insert into email_schema_migrations values (
                3,
                '2026-08-29T16:00:00+00:00'
            );
            """
        )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="email_schema_migrations.*version",
    ):
        EmailStore(database)


def test_v2_processed_without_plan_upgrades_to_explicit_legacy_once(
    tmp_path: Path,
):
    database = tmp_path / "v2-processed-without-plan.sqlite3"
    _create_v2_processed_without_plan_database(database)

    store = EmailStore(database)

    classification = _fetchall(
        database,
        "select * from email_classifications",
    )[0]
    assert classification["legacy_processed_without_plan"] == 1
    assert classification["predicted_category"] == "work"
    assert classification["confirmed_category"] == "important"
    assert classification["classification_source"] == "user"
    assert classification["model_text"] == "__subject__v2 confirmed example"
    assert classification["model_id"] == "email/logistic/v2-model"
    assert classification["config_version"] == "v2-classification-config"
    assert classification["confirmed_at"] == "2026-08-29T16:00:00+00:00"
    assert store.list_training_examples() == [
        {
            "message_id": "dingtalk-account:message-id:<msg-v2@example.com>",
            "model_text": "__subject__v2 confirmed example",
            "label": "important",
        }
    ]
    assert store.list_configs()[0]["description"] == "v2 important config"
    assert _fetchall(
        database,
        "select state_json from email_retraining_state",
    )[0]["state_json"] == '{"last_feedback_count":7}'
    message_before = dict(_fetchall(database, "select * from email_messages")[0])
    assert message_before["sender"] == "message-snapshot-sender"
    assert _fetchall(database, "select * from email_action_plans") == []
    assert _fetchall(database, "select * from email_actions") == []
    assert [
        row["version"]
        for row in _fetchall(
            database,
            "select version from email_schema_migrations order by version",
        )
    ] == [2, 5]

    EmailStore(database)

    reopened = _fetchall(database, "select * from email_classifications")[0]
    assert dict(reopened) == dict(classification)
    assert dict(_fetchall(database, "select * from email_messages")[0]) == message_before
    assert _fetchall(database, "select * from email_action_plans") == []
    assert _fetchall(database, "select * from email_actions") == []


def test_v2_upgrade_does_not_reapply_prototype_classification_backfill(
    tmp_path: Path,
):
    database = tmp_path / "v2-model-processed.sqlite3"
    _create_v2_processed_without_plan_database(database)
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_classifications
            set classification_source='model', confirmed_category=null,
                confirmed_at=''
            """
        )

    store = EmailStore(database)

    classification = _fetchall(
        database,
        "select confirmed_category, legacy_processed_without_plan "
        "from email_classifications",
    )[0]
    assert classification["confirmed_category"] is None
    assert classification["legacy_processed_without_plan"] == 1
    assert store.list_training_examples() == []
    assert _fetchall(database, "select * from email_action_plans") == []
    assert _fetchall(database, "select * from email_actions") == []


def test_future_email_schema_version_fails_closed_before_schema_changes(
    tmp_path: Path,
):
    database = tmp_path / "future.sqlite3"
    future_version = email_store_module.EMAIL_SCHEMA_VERSION + 1
    with sqlite3.connect(database) as db:
        db.execute(
            """
            create table email_schema_migrations (
                version integer primary key,
                applied_at text not null
            )
            """
        )
        db.execute(
            "insert into email_schema_migrations values (?, ?)",
            (future_version, "2026-08-29T16:00:00+00:00"),
        )

    with pytest.raises(EmailPersistenceCorruption, match="newer schema version"):
        EmailStore(database)

    assert not _fetchall(
        database,
        "select name from sqlite_master where type='table' and name='email_classifications'",
    )


def test_prototype_plan_migration_backfills_plan_and_direct_actions(tmp_path: Path):
    database = tmp_path / "prototype-plan.sqlite3"
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    assert classification.action_plan is not None
    _create_prototype_database(
        database,
        classification,
        action_plan_json=classification.action_plan.model_dump_json(),
    )

    EmailStore(database)

    persisted = _fetchall(
        database,
        "select action_plan_json, current_action_plan_id from email_classifications",
    )[0]
    assert persisted["action_plan_json"] == classification.action_plan.model_dump_json()
    assert persisted["current_action_plan_id"] == classification.action_plan.action_plan_id
    assert len(_fetchall(database, "select * from email_action_plans")) == 1
    actions = _fetchall(
        database,
        "select action_type, status, attempt_count from email_actions",
    )
    assert [(row["action_type"], row["status"], row["attempt_count"]) for row in actions] == [
        ("label", "pending", 0)
    ]


def test_migration_failure_rolls_back_and_can_recover(tmp_path: Path):
    database = tmp_path / "migration-recovery.sqlite3"
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    assert classification.action_plan is not None
    _create_prototype_database(
        database,
        classification,
        action_plan_json="{not-json",
    )

    with pytest.raises(EmailPersistenceCorruption, match="action_plan_json"):
        EmailStore(database)

    columns = {
        row["name"]
        for row in _fetchall(database, "pragma table_info(email_classifications)")
    }
    assert "current_action_plan_id" not in columns
    assert not _fetchall(
        database,
        "select name from sqlite_master where type='table' and name='email_messages'",
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set action_plan_json=?",
            (classification.action_plan.model_dump_json(),),
        )

    EmailStore(database)

    assert len(_fetchall(database, "select * from email_schema_migrations")) == 1
    assert len(_fetchall(database, "select * from email_messages")) == 1
    assert len(_fetchall(database, "select * from email_action_plans")) == 1
    assert len(_fetchall(database, "select * from email_actions")) == 1


def test_concurrent_first_initialization_is_transactionally_idempotent(
    tmp_path: Path,
):
    database = tmp_path / "concurrent-init.sqlite3"
    ready = Barrier(2)

    def initialize(_: int) -> EmailStore:
        ready.wait()
        return EmailStore(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        stores = list(executor.map(initialize, range(2)))

    assert len(stores) == 2
    assert len(_fetchall(database, "select * from email_schema_migrations")) == 1
    assert EmailStore(database).list_training_examples() == []


@pytest.mark.parametrize("missing_table", ["email_messages", "email_actions"])
def test_normal_startup_does_not_repair_missing_durable_rows(
    tmp_path: Path,
    missing_table: str,
):
    database = tmp_path / f"missing-{missing_table}.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute(f"delete from {missing_table}")

    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(database)

    assert len(_fetchall(database, f"select * from {missing_table}")) == 0


def test_versioned_task3_upgrade_does_not_run_prototype_backfill(tmp_path: Path):
    database = tmp_path / "versioned-upgrade-corruption.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute("delete from email_actions")
        db.execute("update email_schema_migrations set version=2")

    with pytest.raises(EmailPersistenceCorruption, match="direct action row set"):
        EmailStore(database)

    assert len(_fetchall(database, "select * from email_actions")) == 0
    version = _fetchall(database, "select version from email_schema_migrations")[0]
    assert version["version"] == 2


def test_startup_rejects_message_account_identity_mismatch(tmp_path: Path):
    database = tmp_path / "message-mismatch.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute("update email_messages set account_id='wrong-account'")

    with pytest.raises(EmailPersistenceCorruption, match="message identity"):
        EmailStore(database)


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "update email_messages set folder='Archive'",
        "update email_messages set uidvalidity=84",
        "update email_messages set uid=9",
        "update email_messages set rfc_message_id='<tampered@example.com>'",
        "update email_messages set thread_identity='thread-tampered'",
    ],
)
def test_startup_rejects_message_locator_and_thread_mismatch(
    tmp_path: Path,
    tamper_sql: str,
):
    database = tmp_path / "message-locator-mismatch.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            thread_id="thread-original",
        ),
    )
    with sqlite3.connect(database) as db:
        db.execute(tamper_sql)

    with pytest.raises(EmailPersistenceCorruption, match="message locator mismatch"):
        EmailStore(database)


def test_startup_rejects_orphan_message_without_classification(tmp_path: Path):
    database = tmp_path / "orphan-message.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute("delete from email_classifications")

    with pytest.raises(EmailPersistenceCorruption, match="orphan email message"):
        EmailStore(database)


def test_startup_accepts_canonical_and_empty_locator_metadata_equivalence(
    tmp_path: Path,
):
    database = tmp_path / "canonical-locator.sqlite3"
    store = EmailStore(database)
    canonical = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="canonical-rfc",
        stable_message_identity=(
            "dingtalk-account:message-id:<Canonical@example.com>"
        ),
        rfc_message_id="<Canonical@EXAMPLE.COM>",
        thread_id="  thread-canonical  ",
    )
    fallback = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="canonical-empty",
        stable_message_identity="dingtalk-account:imap:Archive:42:9",
        folder="Archive",
        uid=9,
        rfc_message_id=None,
        thread_id="   ",
    )
    store.upsert_classification(canonical)
    store.upsert_classification(fallback)

    rows = _fetchall(
        database,
        """
        select c.stable_message_identity, c.rfc_message_id as classification_rfc,
               c.thread_id as classification_thread,
               m.rfc_message_id as message_rfc,
               m.thread_identity as message_thread
        from email_classifications c
        join email_messages m using (stable_message_identity)
        order by c.stable_message_identity
        """,
    )
    by_identity = {row["stable_message_identity"]: row for row in rows}
    canonical_row = by_identity[canonical.stable_message_identity]
    assert canonical_row["classification_rfc"] == "<Canonical@example.com>"
    assert canonical_row["message_rfc"] == "<Canonical@example.com>"
    assert canonical_row["classification_thread"] == "thread-canonical"
    assert canonical_row["message_thread"] == "thread-canonical"
    fallback_row = by_identity[fallback.stable_message_identity]
    assert fallback_row["classification_rfc"] is None
    assert fallback_row["message_rfc"] == ""
    assert fallback_row["classification_thread"] is None
    assert fallback_row["message_thread"] == ""

    EmailStore(database)


def test_startup_normalizes_both_persisted_locator_metadata_sides(tmp_path: Path):
    database = tmp_path / "semantic-locator-equivalence.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            thread_id="thread-semantic",
        ),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_messages
            set rfc_message_id='<msg-1@EXAMPLE.COM>',
                thread_identity='  thread-semantic  '
            """
        )
        db.execute(
            """
            update email_classifications
            set rfc_message_id=' msg-1@example.com ',
                thread_id='thread-semantic'
            """
        )

    message = _fetchall(database, "select * from email_messages")[0]
    classification = _fetchall(database, "select * from email_classifications")[0]
    assert message["rfc_message_id"] != classification["rfc_message_id"]
    assert message["thread_identity"] != classification["thread_id"]
    message_canonical = EmailProviderLocator.model_validate(
        {
            "account_id": message["account_id"],
            "folder": message["folder"],
            "uidvalidity": message["uidvalidity"],
            "uid": message["uid"],
            "rfc_message_id": message["rfc_message_id"],
            "thread_id": message["thread_identity"],
        }
    )
    classification_canonical = EmailProviderLocator.model_validate(
        {
            "account_id": classification["account_id"],
            "folder": classification["folder"],
            "uidvalidity": classification["uidvalidity"],
            "uid": classification["uid"],
            "rfc_message_id": classification["rfc_message_id"],
            "thread_id": classification["thread_id"],
        }
    )
    assert message_canonical.rfc_message_id == "<msg-1@example.com>"
    assert message_canonical.rfc_message_id == classification_canonical.rfc_message_id
    assert message_canonical.thread_id == "thread-semantic"
    assert message_canonical.thread_id == classification_canonical.thread_id

    EmailStore(database)


def test_startup_rejects_current_action_plan_pointer_rollback(tmp_path: Path):
    database = tmp_path / "pointer-rollback.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    second_plan = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.IMPORTANT,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )
    store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.IMPORTANT,
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_classifications
            set current_action_plan_id=?, action_plan_json=?
            """,
            (
                classification.action_plan.action_plan_id,
                classification.action_plan.model_dump_json(),
            ),
        )

    with pytest.raises(EmailPersistenceCorruption, match="highest ActionPlan"):
        EmailStore(database)


@pytest.mark.parametrize(
    ("classification_update", "message_update"),
    [
        ("account_id='other-account'", "account_id='other-account'"),
        ("category='personal', confirmed_category='personal'", None),
        ("classification_source='user'", None),
        ("confidence=0.22", None),
        ("model_id='email/logistic/other-model'", None),
        ("config_version='other-config'", None),
    ],
)
def test_startup_rejects_current_plan_classification_field_mismatch(
    tmp_path: Path,
    classification_update: str,
    message_update: str | None,
):
    database = tmp_path / "plan-classification-mismatch.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute(f"update email_classifications set {classification_update}")
        if message_update is not None:
            db.execute(f"update email_messages set {message_update}")

    with pytest.raises(EmailPersistenceCorruption, match="classification fields"):
        EmailStore(database)


def test_startup_rejects_pending_feedback_with_action_plan(tmp_path: Path):
    database = tmp_path / "pending-with-plan.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set status='pending_feedback', confirmed_category=null"
        )

    with pytest.raises(EmailPersistenceCorruption, match="pending feedback.*ActionPlan"):
        EmailStore(database)


def test_startup_rejects_pending_feedback_with_user_source(tmp_path: Path):
    database = tmp_path / "pending-user-source.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set classification_source='user'"
        )

    with pytest.raises(EmailPersistenceCorruption, match="pending feedback.*model"):
        EmailStore(database)


def test_startup_rejects_pending_feedback_with_legacy_marker(tmp_path: Path):
    database = tmp_path / "pending-legacy.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set legacy_processed_without_plan=1"
        )

    with pytest.raises(EmailPersistenceCorruption, match="pending feedback.*legacy"):
        EmailStore(database)


def test_startup_rejects_legacy_user_processed_with_mismatched_confirmation(
    tmp_path: Path,
):
    database = tmp_path / "legacy-user-confirmation-mismatch.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_classifications
            set status='processed', classification_source='user',
                category='important', confirmed_category='work',
                legacy_processed_without_plan=1
            """
        )

    with pytest.raises(EmailPersistenceCorruption, match="user-confirmed.*category"):
        EmailStore(database)


def test_startup_accepts_model_processed_without_user_confirmation(tmp_path: Path):
    database = tmp_path / "model-processed-unconfirmed.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute("update email_classifications set confirmed_category=null")

    EmailStore(database)


def test_startup_rejects_normal_processed_classification_without_plan(
    tmp_path: Path,
):
    database = tmp_path / "processed-without-plan.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute("delete from email_actions")
        db.execute("delete from email_action_plans")
        db.execute(
            """
            update email_classifications
            set action_plan_json='null', current_action_plan_id=null
            """
        )

    with pytest.raises(EmailPersistenceCorruption, match="processed.*ActionPlan"):
        EmailStore(database)


def test_startup_rejects_noncanonical_current_action_plan_snapshot(tmp_path: Path):
    database = tmp_path / "plan-json-mismatch.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set action_plan_json=' ' || action_plan_json"
        )

    with pytest.raises(EmailPersistenceCorruption, match="snapshot mismatch"):
        EmailStore(database)


def test_pending_feedback_persists_message_and_cursor_without_plan_or_actions(
    tmp_path: Path,
):
    database = tmp_path / "pending.sqlite3"
    store = EmailStore(database)

    persisted = _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )

    assert persisted["status"] == "pending_feedback"
    assert persisted["current_action_plan_id"] is None
    assert len(_fetchall(database, "select * from email_messages")) == 1
    assert len(_fetchall(database, "select * from email_action_plans")) == 0
    assert len(_fetchall(database, "select * from email_actions")) == 0
    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 42
    assert cursor["last_seen_uid"] == persisted["uid"]


def test_processed_scan_persists_plan_and_only_direct_action_rows(tmp_path: Path):
    database = tmp_path / "processed.sqlite3"
    store = EmailStore(database)
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        actions=(
            EmailAction.LABEL,
            EmailAction.MARK_READ,
            EmailAction.AUTO_REPLY,
            EmailAction.UNSUBSCRIBE,
        ),
        action_parameters={
            EmailAction.LABEL: {"labels": ["work"]},
            EmailAction.AUTO_REPLY: {"instruction": "Acknowledge receipt"},
        },
    )

    persisted = _persist_scan(store, classification)

    assert persisted["current_action_plan_id"] == classification.action_plan.action_plan_id
    plans = _fetchall(database, "select * from email_action_plans")
    assert len(plans) == 1
    assert json.loads(plans[0]["actions_json"]) == [
        "label",
        "mark_read",
        "auto_reply",
        "unsubscribe",
    ]
    actions = _fetchall(
        database,
        "select action_type, status from email_actions order by action_type",
    )
    assert [(row["action_type"], row["status"]) for row in actions] == [
        ("label", "pending"),
        ("mark_read", "pending"),
    ]


def test_exact_scan_replay_is_idempotent(tmp_path: Path):
    database = tmp_path / "replay.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)

    first = _persist_scan(store, classification)
    second = _persist_scan(store, classification)

    assert second["id"] == first["id"]
    assert len(_fetchall(database, "select * from email_messages")) == 1
    assert len(_fetchall(database, "select * from email_classifications")) == 1
    assert len(_fetchall(database, "select * from email_action_plans")) == 1
    assert len(_fetchall(database, "select * from email_actions")) == 1
    assert len(store.list_training_examples()) == 0


def test_processed_model_rescan_preserves_business_snapshot_plan_and_actions(
    tmp_path: Path,
):
    database = tmp_path / "processed-model-rescan.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="processed-model-rescan",
        confidence=0.93,
        model_id="email/logistic/model-original",
        config_version="email-config-original",
        category=EmailCategory.WORK,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["work"]}},
        thread_id="thread-original",
    )
    _persist_scan(store, original)
    classification_before = dict(
        _fetchall(database, "select * from email_classifications")[0]
    )
    message_before = dict(_fetchall(database, "select * from email_messages")[0])
    plans_before = [
        dict(row)
        for row in _fetchall(
            database,
            "select * from email_action_plans order by action_plan_version",
        )
    ]
    actions_before = [
        dict(row)
        for row in _fetchall(database, "select * from email_actions order by action_id")
    ]
    training_before = store.list_training_examples()

    changed = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="processed-model-rescan-changed",
        confidence=0.51,
        model_id="email/logistic/model-changed",
        config_version="email-config-changed",
        category=EmailCategory.PERSONAL,
        actions=(EmailAction.MOVE,),
        action_parameters={
            EmailAction.MOVE: {"target_folder": "Archive/Personal"}
        },
        classification_id=original.classification_id,
        stable_message_identity=original.stable_message_identity,
        folder="Archive",
        uidvalidity=84,
        uid=9,
        rfc_message_id=original.provider_locator.rfc_message_id,
        thread_id="thread-current",
    )
    rescanned = _persist_scan(
        store,
        changed,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=9,
    )

    classification_after = dict(
        _fetchall(database, "select * from email_classifications")[0]
    )
    message_after = dict(_fetchall(database, "select * from email_messages")[0])
    assert {
        field: classification_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_id")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_id": "thread-current",
    }
    for field, value in classification_before.items():
        if field not in {"folder", "uidvalidity", "uid", "thread_id", "updated_at"}:
            assert classification_after[field] == value
    assert {
        field: message_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_identity")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_identity": "thread-current",
    }
    for field, value in message_before.items():
        if field not in {"folder", "uidvalidity", "uid", "thread_identity", "updated_at"}:
            assert message_after[field] == value
    assert rescanned["category"] == classification_before["category"]
    assert rescanned["model_id"] == classification_before["model_id"]
    assert rescanned["confidence"] == classification_before["confidence"]
    assert rescanned["config_version"] == classification_before["config_version"]
    assert rescanned["current_action_plan_id"] == classification_before[
        "current_action_plan_id"
    ]
    assert [
        dict(row)
        for row in _fetchall(
            database,
            "select * from email_action_plans order by action_plan_version",
        )
    ] == plans_before
    assert [
        dict(row)
        for row in _fetchall(database, "select * from email_actions order by action_id")
    ] == actions_before
    assert store.list_training_examples() == training_before
    assert len(_fetchall(database, "select * from email_messages")) == 1
    assert len(_fetchall(database, "select * from email_classifications")) == 1


def test_changed_plan_appends_next_version_and_preserves_history(tmp_path: Path):
    database = tmp_path / "history.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    first = _persist_scan(store, classification)
    assert classification.action_plan is not None
    second_plan = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.IMPORTANT,
        actions=(EmailAction.ARCHIVE, EmailAction.UNSUBSCRIBE),
        action_parameters={},
    )

    corrected = store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.IMPORTANT,
    )
    replayed = store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.IMPORTANT,
    )

    assert first["current_action_plan_id"] != corrected["current_action_plan_id"]
    assert replayed["current_action_plan_id"] == second_plan.action_plan_id
    assert corrected["confirmed_category"] == "important"
    plans = _fetchall(
        database,
        "select action_plan_id, action_plan_version from email_action_plans order by action_plan_version",
    )
    assert [(row["action_plan_id"], row["action_plan_version"]) for row in plans] == [
        (classification.action_plan.action_plan_id, 1),
        (second_plan.action_plan_id, 2),
    ]
    assert len(_fetchall(database, "select * from email_actions")) == 2


def test_changed_snapshot_cannot_reuse_an_existing_plan_version(tmp_path: Path):
    database = tmp_path / "plan-conflict.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    conflicting = _versioned_plan(
        classification.action_plan,
        version=1,
        category=EmailCategory.IMPORTANT,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )

    with pytest.raises(EmailActionPlanConflict, match="version"):
        store.append_action_plan_version(
            classification.classification_id,
            conflicting,
            confirmed_category=EmailCategory.IMPORTANT,
        )


def test_rescan_updates_locator_without_replacing_final_decision_or_plan(
    tmp_path: Path,
):
    database = tmp_path / "move.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="fallback",
        stable_message_identity="dingtalk-account:imap:INBOX:42:7",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        rfc_message_id=None,
    )
    first = _persist_scan(store, original, cursor_last_seen_uid=7)
    moved = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="fallback-moved",
        confidence=0.51,
        model_id="email/logistic/model-2",
        classification_id=original.classification_id,
        stable_message_identity=original.stable_message_identity,
        folder="Archive",
        uidvalidity=84,
        uid=9,
        rfc_message_id=None,
    )

    rescanned = _persist_scan(
        store,
        moved,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=9,
    )

    assert rescanned["folder"] == "Archive"
    assert rescanned["uidvalidity"] == 84
    assert rescanned["uid"] == 9
    assert rescanned["status"] == "processed"
    assert rescanned["model_id"] == first["model_id"]
    assert rescanned["current_action_plan_id"] == first["current_action_plan_id"]
    assert len(_fetchall(database, "select * from email_messages")) == 1
    assert len(_fetchall(database, "select * from email_action_plans")) == 1
    assert len(_fetchall(database, "select * from email_actions")) == 1


def test_rescan_only_updates_mutable_locator_and_preserves_business_snapshot(
    tmp_path: Path,
):
    database = tmp_path / "immutable-message-snapshot.sqlite3"
    store = EmailStore(database)
    store.upsert_config(
        category=EmailCategory.IMPORTANT,
        description="Requires attention",
        threshold=0.97,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["important"]}},
        enabled=True,
        config_version="important-v1",
    )
    original = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="immutable-snapshot",
        thread_id="thread-original",
    )
    store.persist_scan_result(
        original,
        sender="original-sender@example.com",
        recipients=("original-to@example.com", "original-cc@example.com"),
        subject="Original subject",
        normalized_text="__subject__original normalized text",
        preview="Original preview",
        attachment_metadata=(
            EmailAttachmentMetadata(
                filename="original.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                inline=False,
            ),
        ),
        received_at="2026-08-29T15:59:00+00:00",
        model_text="__subject__original normalized text",
        cursor_uidvalidity=42,
        cursor_last_seen_uid=original.provider_locator.uid,
        cursor_last_success_at="2026-08-29T16:00:00+00:00",
    )
    confirmed = store.confirm_classification(
        original.classification_id,
        EmailCategory.IMPORTANT,
    )
    assert confirmed is not None

    message_before = dict(_fetchall(database, "select * from email_messages")[0])
    classification_before = dict(
        _fetchall(database, "select * from email_classifications")[0]
    )
    plans_before = [
        dict(row)
        for row in _fetchall(
            database,
            "select * from email_action_plans order by action_plan_version",
        )
    ]
    actions_before = [
        dict(row)
        for row in _fetchall(database, "select * from email_actions order by action_id")
    ]
    training_before = store.list_training_examples()
    counts_before = {
        table: _fetchall(database, f"select count(*) as count from {table}")[0][
            "count"
        ]
        for table in (
            "email_messages",
            "email_classifications",
            "email_action_plans",
            "email_actions",
        )
    }

    moved = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="immutable-snapshot-rescan",
        confidence=0.21,
        model_id="email/logistic/model-rescan",
        category=EmailCategory.PERSONAL,
        classification_id=original.classification_id,
        stable_message_identity=original.stable_message_identity,
        folder="Archive",
        uidvalidity=84,
        uid=9,
        rfc_message_id=original.provider_locator.rfc_message_id,
        thread_id="thread-current",
    )
    rescanned = store.persist_scan_result(
        moved,
        sender="different-sender@example.net",
        recipients=("different-to@example.net",),
        subject="Different subject",
        normalized_text="__subject__different normalized text",
        preview="Different preview",
        attachment_metadata=(
            EmailAttachmentMetadata(
                filename="different.png",
                mime_type="image/png",
                size_bytes=9999,
                inline=True,
            ),
        ),
        received_at="2026-08-30T12:00:00+00:00",
        model_text="__subject__different normalized text",
        cursor_uidvalidity=84,
        cursor_last_seen_uid=9,
        cursor_last_success_at="2026-08-30T12:01:00+00:00",
    )

    message_after = dict(_fetchall(database, "select * from email_messages")[0])
    classification_after = dict(
        _fetchall(database, "select * from email_classifications")[0]
    )
    assert {
        field: message_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_identity")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_identity": "thread-current",
    }
    for field in (
        "id",
        "account_id",
        "stable_message_identity",
        "rfc_message_id",
        "sender",
        "recipients_json",
        "subject",
        "normalized_text",
        "preview",
        "attachment_metadata_json",
        "received_at",
        "created_at",
    ):
        assert message_after[field] == message_before[field]

    assert {
        field: classification_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_id")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_id": "thread-current",
    }
    for field, value in classification_before.items():
        if field not in {"folder", "uidvalidity", "uid", "thread_id", "updated_at"}:
            assert classification_after[field] == value
    assert rescanned["current_action_plan_id"] == confirmed["current_action_plan_id"]
    assert [
        dict(row)
        for row in _fetchall(
            database,
            "select * from email_action_plans order by action_plan_version",
        )
    ] == plans_before
    assert [
        dict(row)
        for row in _fetchall(database, "select * from email_actions order by action_id")
    ] == actions_before
    assert store.list_training_examples() == training_before
    assert {
        table: _fetchall(database, f"select count(*) as count from {table}")[0][
            "count"
        ]
        for table in counts_before
    } == counts_before


def test_classification_id_collision_fails_closed_with_domain_error(tmp_path: Path):
    database = tmp_path / "collision.sqlite3"
    store = EmailStore(database)
    first = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="first",
        classification_id=12345,
    )
    second = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="second",
        classification_id=12345,
    )
    _persist_scan(store, first)

    with pytest.raises(EmailClassificationIdentityCollision, match="12345"):
        _persist_scan(store, second)

    rows = _fetchall(database, "select stable_message_identity from email_classifications")
    assert [row["stable_message_identity"] for row in rows] == [
        first.stable_message_identity
    ]


def test_cursor_creation_and_same_generation_advancement_are_monotonic(
    tmp_path: Path,
):
    database = tmp_path / "cursor.sqlite3"
    store = EmailStore(database)
    first = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK, uid=9)
    _persist_scan(store, first, cursor_uidvalidity=42, cursor_last_seen_uid=9)

    earlier = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="earlier-same-generation",
        uid=5,
    )
    _persist_scan(store, earlier, cursor_uidvalidity=42, cursor_last_seen_uid=5)

    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 42
    assert cursor["last_seen_uid"] == 9


def test_empty_scan_cursor_initialization_and_reset_use_compare_and_set(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "empty-cursor.sqlite3")

    store.persist_empty_scan_cursor(
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        last_success_at="2026-08-30T00:00:00+00:00",
    )
    assert store.get_scan_cursor("dingtalk-account", "INBOX")["last_seen_uid"] == 0

    store.persist_empty_scan_cursor(
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=84,
        last_success_at="2026-08-30T00:01:00+00:00",
        expected_cursor_uidvalidity=42,
    )
    reset = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert reset["uidvalidity"] == 84
    assert reset["last_seen_uid"] == 0

    with pytest.raises(email_store_module.EmailCursorConflict, match="expected 42"):
        store.persist_empty_scan_cursor(
            account_id="dingtalk-account",
            folder="INBOX",
            uidvalidity=126,
            last_success_at="2026-08-30T00:02:00+00:00",
            expected_cursor_uidvalidity=42,
        )
    unchanged = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert unchanged["uidvalidity"] == 84
    assert unchanged["last_seen_uid"] == 0


def test_cursor_generation_reset_requires_compare_and_set(tmp_path: Path):
    database = tmp_path / "cursor-reset.sqlite3"
    store = EmailStore(database)
    first = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK, uid=9)
    _persist_scan(store, first, cursor_uidvalidity=42, cursor_last_seen_uid=9)

    reset = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="after-reset",
        uidvalidity=84,
        uid=2,
    )
    with pytest.raises(email_store_module.EmailCursorConflict, match="expected"):
        _persist_scan(store, reset, cursor_uidvalidity=84, cursor_last_seen_uid=2)

    _persist_scan(
        store,
        reset,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=2,
        expected_cursor_uidvalidity=42,
    )

    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 84
    assert cursor["last_seen_uid"] == 2


def test_same_generation_update_rejects_stale_uidvalidity_expectation_atomically(
    tmp_path: Path,
):
    database = tmp_path / "same-generation-stale-expectation.sqlite3"
    store = EmailStore(database)
    initial = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="same-generation-initial",
        uid=9,
    )
    _persist_scan(store, initial, cursor_uidvalidity=42, cursor_last_seen_uid=9)
    reset = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="same-generation-reset",
        uidvalidity=84,
        uid=2,
    )
    _persist_scan(
        store,
        reset,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=2,
        expected_cursor_uidvalidity=42,
    )
    counts_before = {
        table: _fetchall(database, f"select count(*) as count from {table}")[0][
            "count"
        ]
        for table in (
            "email_messages",
            "email_classifications",
            "email_action_plans",
            "email_actions",
        )
    }
    stale = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="same-generation-stale",
        uidvalidity=84,
        uid=10,
    )

    with pytest.raises(email_store_module.EmailCursorConflict, match="expected 42"):
        _persist_scan(
            store,
            stale,
            cursor_uidvalidity=84,
            cursor_last_seen_uid=10,
            expected_cursor_uidvalidity=42,
        )

    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 84
    assert cursor["last_seen_uid"] == 2
    assert {
        table: _fetchall(database, f"select count(*) as count from {table}")[0][
            "count"
        ]
        for table in counts_before
    } == counts_before
    assert not _fetchall(
        database,
        "select id from email_classifications where id=?",
        (stale.classification_id,),
    )


def test_cursor_expectation_requires_cursor_progress(tmp_path: Path):
    store = EmailStore(tmp_path / "cursor-expectation.sqlite3")

    with pytest.raises(ValueError, match="expected cursor generation requires"):
        store.persist_scan_result(
            _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
            expected_cursor_uidvalidity=42,
        )


def test_stale_cursor_generation_commit_rolls_back_all_scan_state(tmp_path: Path):
    database = tmp_path / "stale-cursor.sqlite3"
    store_a = EmailStore(database)
    store_b = EmailStore(database)
    initial = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="cursor-initial",
        uid=9,
    )
    _persist_scan(store_a, initial, cursor_uidvalidity=42, cursor_last_seen_uid=9)

    reset = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="cursor-reset",
        uidvalidity=84,
        uid=2,
    )
    _persist_scan(
        store_a,
        reset,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=2,
        expected_cursor_uidvalidity=42,
    )
    counts_before = {
        table: _fetchall(database, f"select count(*) as count from {table}")[0][
            "count"
        ]
        for table in (
            "email_messages",
            "email_classifications",
            "email_action_plans",
            "email_actions",
        )
    }
    stale = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="stale-generation",
        uidvalidity=42,
        uid=10,
    )

    with pytest.raises(email_store_module.EmailCursorConflict, match="expected 42"):
        _persist_scan(
            store_b,
            stale,
            cursor_uidvalidity=42,
            cursor_last_seen_uid=10,
            expected_cursor_uidvalidity=42,
        )

    cursor = store_b.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 84
    assert cursor["last_seen_uid"] == 2
    assert {
        table: _fetchall(database, f"select count(*) as count from {table}")[0][
            "count"
        ]
        for table in counts_before
    } == counts_before
    assert not _fetchall(
        database,
        "select id from email_classifications where id=?",
        (stale.classification_id,),
    )


def test_action_attempts_append_and_duplicate_or_invalid_values_are_rejected(
    tmp_path: Path,
):
    database = tmp_path / "attempts.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    action_id = _fetchall(database, "select action_id from email_actions")[0][
        "action_id"
    ]

    first = store.append_action_attempt(
        action_id=action_id,
        attempt_number=1,
        status="failed",
        provider_operation="STORE labels",
        provider_target="dingtalk-account:message-id:<msg-1@example.com>",
        provider_result_id="",
        error="timeout",
        started_at="2026-08-29T16:00:00+00:00",
        finished_at="2026-08-29T16:00:01+00:00",
    )
    second = store.append_action_attempt(
        action_id=action_id,
        attempt_number=2,
        status="done",
        provider_operation="readback_noop",
        provider_target="dingtalk-account:message-id:<msg-1@example.com>",
        provider_result_id="revision-2",
        error="",
        started_at="2026-08-29T16:01:00+00:00",
        finished_at="2026-08-29T16:01:01+00:00",
    )

    assert first["status"] == "failed"
    assert second["status"] == "done"
    assert [row["attempt_number"] for row in store.list_action_attempts(action_id)] == [
        1,
        2,
    ]
    current = _fetchall(database, "select * from email_actions where action_id=?", (action_id,))[0]
    assert current["status"] == "done"
    assert current["attempt_count"] == 2

    with pytest.raises(EmailActionAttemptConflict, match="already exists"):
        store.append_action_attempt(
            action_id=action_id,
            attempt_number=2,
            status="done",
            provider_operation="duplicate",
            provider_target="same",
            provider_result_id="same",
            error="",
            started_at="2026-08-29T16:02:00+00:00",
            finished_at="2026-08-29T16:02:01+00:00",
        )
    with pytest.raises(ValueError, match="attempt status"):
        store.append_action_attempt(
            action_id=action_id,
            attempt_number=3,
            status="processing",
            provider_operation="invalid",
            provider_target="same",
            provider_result_id="",
            error="",
            started_at="2026-08-29T16:03:00+00:00",
            finished_at="2026-08-29T16:03:01+00:00",
        )
    with pytest.raises(sqlite3.IntegrityError, match="status"):
        with sqlite3.connect(database) as db:
            db.execute(
                "update email_actions set status='skipped' where action_id=?",
                (action_id,),
            )


def test_startup_rejects_pending_action_with_historical_terminal_attempt(
    tmp_path: Path,
):
    database = tmp_path / "pending-with-attempt.sqlite3"
    _, action_id = _create_action_with_attempts(database, ("done",))
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_actions
            set status='pending', attempt_count=0, started_at='', finished_at='',
                provider_operation='', provider_target='', provider_result_id='',
                error=''
            where action_id=?
            """,
            (action_id,),
        )

    with pytest.raises(EmailPersistenceCorruption, match="pending action.*attempt"):
        EmailStore(database)


def test_startup_rejects_action_attempt_count_mismatch(tmp_path: Path):
    database = tmp_path / "attempt-count.sqlite3"
    _, action_id = _create_action_with_attempts(database, ("done",))
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_actions set attempt_count=2 where action_id=?",
            (action_id,),
        )

    with pytest.raises(EmailPersistenceCorruption, match="attempt count mismatch"):
        EmailStore(database)


def test_startup_rejects_gap_in_action_attempt_numbers(tmp_path: Path):
    database = tmp_path / "attempt-gap.sqlite3"
    _create_action_with_attempts(database, ("failed", "done"))
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_action_attempts set attempt_number=3 where attempt_number=2"
        )

    with pytest.raises(EmailPersistenceCorruption, match="non-contiguous attempt"):
        EmailStore(database)


def test_startup_rejects_latest_attempt_receipt_or_status_mismatch(tmp_path: Path):
    database = tmp_path / "attempt-latest-mismatch.sqlite3"
    _, action_id = _create_action_with_attempts(database, ("failed", "done"))
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_actions set provider_result_id='wrong-receipt' where action_id=?",
            (action_id,),
        )

    with pytest.raises(EmailPersistenceCorruption, match="latest attempt mismatch"):
        EmailStore(database)


def test_startup_rejects_terminal_action_without_attempt(tmp_path: Path):
    database = tmp_path / "terminal-without-attempt.sqlite3"
    _, action_id = _create_action_with_attempts(database, ())
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_actions set status='done' where action_id=?",
            (action_id,),
        )

    with pytest.raises(EmailPersistenceCorruption, match="terminal action.*no.*attempt"):
        EmailStore(database)


def test_processing_action_may_follow_terminal_attempts_but_has_no_terminal_receipt(
    tmp_path: Path,
):
    database = tmp_path / "processing-retry.sqlite3"
    _, action_id = _create_action_with_attempts(database, ("failed",))
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_actions
            set status='processing', started_at='2026-08-29T16:02:00+00:00',
                finished_at='', provider_operation='operation-2',
                provider_target='target-2', provider_result_id='', error=''
            where action_id=?
            """,
            (action_id,),
        )

    EmailStore(database)


def test_atomic_scan_persistence_rolls_back_message_plan_actions_and_cursor(
    tmp_path: Path,
):
    database = tmp_path / "rollback.sqlite3"
    store = EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            """
            create trigger reject_email_action_insert
            before insert on email_actions
            begin
                select raise(abort, 'injected action failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected action failure"):
        _persist_scan(
            store,
            _classification(status=EmailClassificationStatus.PROCESSED),
        )

    assert len(_fetchall(database, "select * from email_messages")) == 0
    assert len(_fetchall(database, "select * from email_classifications")) == 0
    assert len(_fetchall(database, "select * from email_action_plans")) == 0
    assert len(_fetchall(database, "select * from email_actions")) == 0
    assert len(_fetchall(database, "select * from email_scan_cursors")) == 0


def test_corrupt_stored_json_is_reported_as_domain_corruption(tmp_path: Path):
    database = tmp_path / "corrupt.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set probabilities_json='not-json'"
        )

    with pytest.raises(EmailPersistenceCorruption, match="probabilities_json"):
        EmailStore(database)


def test_corrupt_historical_action_plan_is_rejected_on_open(tmp_path: Path):
    database = tmp_path / "corrupt-plan.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    second_plan = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.IMPORTANT,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )
    store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.IMPORTANT,
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_action_plans
            set actions_json='["not-an-email-action"]'
            where action_plan_version=1
            """
        )

    with pytest.raises(EmailPersistenceCorruption, match="ActionPlan"):
        EmailStore(database)


def test_direct_action_parameters_must_match_immutable_plan(tmp_path: Path):
    database = tmp_path / "corrupt-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_actions set parameters_json=?",
            ('{"labels":["silently-changed"]}',),
        )

    with pytest.raises(EmailPersistenceCorruption, match="immutable direct action"):
        EmailStore(database)


def test_account_scan_folders_json_must_be_a_list_of_folder_names(tmp_path: Path):
    database = tmp_path / "corrupt-account.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
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
                "ding-main",
                "DingTalk",
                "redacted@example.com",
                "imap.example.com",
                993,
                1,
                "redacted@example.com",
                "CEO_EMAIL_DING_IMAP_SECRET",
                "smtp.example.com",
                465,
                1,
                "redacted@example.com",
                "CEO_EMAIL_DING_SMTP_SECRET",
                1,
                "{}",
                60,
                "2026-08-29T16:00:00+00:00",
                "2026-08-29T16:00:00+00:00",
            ),
        )

    with pytest.raises(EmailPersistenceCorruption, match="scan_folders_json"):
        EmailStore(database)
