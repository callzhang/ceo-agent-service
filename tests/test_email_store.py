from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    _action_plan_identity,
    build_email_action_plan,
)
from app.email_store import (
    EmailActionAttemptConflict,
    EmailActionPlanConflict,
    EmailClassificationConflict,
    EmailClassificationIdentityCollision,
    EmailPersistenceCorruption,
    EmailStore,
)


def _classification(
    *,
    status: EmailClassificationStatus,
    message_id: str = "msg-1",
    confidence: float = 0.93,
    model_id: str = "email/logistic/model-1",
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
            config_version="email-v1",
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
            "config_version": "email-v1",
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
) -> dict[str, object]:
    locator = classification.provider_locator
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
    )


def _fetchall(path: Path, sql: str, parameters: tuple[object, ...] = ()):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, parameters).fetchall()


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


def test_cursor_uidvalidity_reset_replaces_old_numeric_progress(tmp_path: Path):
    database = tmp_path / "cursor.sqlite3"
    store = EmailStore(database)
    first = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK, uid=9)
    _persist_scan(store, first, cursor_uidvalidity=42, cursor_last_seen_uid=9)

    reset = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="after-reset",
        uidvalidity=99,
        uid=2,
    )
    _persist_scan(store, reset, cursor_uidvalidity=99, cursor_last_seen_uid=2)

    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 99
    assert cursor["last_seen_uid"] == 2


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
