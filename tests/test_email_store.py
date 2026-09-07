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
    EmailActionAuthorization,
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
    EmailUnsubscribeClaimConflict,
    EmailUnsubscribeReceiptConflict,
    EmailStore,
    email_unsubscribe_effect_digest,
)
from app.email_task_adapter import email_action_identity
from app.email_pipeline import apply_human_confirmation
from app.email_unsubscribe import normalize_unsubscribe_result_text
from app.store import AutoReplyStore


def _pending_category_classification(
    category: str,
    *,
    classification_id: int,
) -> EmailClassification:
    message_id = f"<{category}-{classification_id}@example.com>"
    return EmailClassification.model_validate(
        {
            "classification_id": classification_id,
            "stable_message_identity": (
                f"dingtalk-account:message-id:{message_id}"
            ),
            "provider_locator": {
                "account_id": "dingtalk-account",
                "folder": "INBOX",
                "uidvalidity": 42,
                "uid": classification_id,
                "rfc_message_id": message_id,
            },
            "category": category,
            "confidence": 0.79,
            "margin": 0.31,
            "probabilities": {category: 0.79, "legal": 0.21},
            "model_id": "email/logistic/model-1",
            "config_version": "email-v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model",
            "action_plan": None,
        }
    )


def test_real_store_human_confirmation_persists_plain_string_category(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "plain-category.sqlite3")
    classification = _pending_category_classification(
        "work",
        classification_id=101,
    )
    persisted = store.persist_scan_result(
        classification,
        model_text="__subject__plain category integration",
    )

    application = apply_human_confirmation(
        store,
        persisted["id"],
        "work",
        feedback_request_id="feedback-plain-category",
        expected_current_action_plan_id=None,
        now=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert application is not None
    assert type(application.confirmed["category"]) is str
    assert type(application.confirmed["action_plan"]["category"]) is str
    with sqlite3.connect(store.path) as db:
        stored_category = db.execute(
            "select category from email_action_plans where action_plan_id=?",
            (application.resulting_action_plan_id,),
        ).fetchone()[0]
    assert type(stored_category) is str
    assert stored_category == "work"


def test_real_store_persists_and_reopens_custom_category(tmp_path: Path):
    database = tmp_path / "custom-category.sqlite3"
    store = EmailStore(database)
    classification = _pending_category_classification(
        "board_governance",
        classification_id=102,
    )

    persisted = store.persist_scan_result(
        classification,
        model_text="__subject__board governance",
    )
    reopened = EmailStore(database)
    restored = reopened.get_classification(persisted["id"])

    assert restored is not None
    assert type(restored["category"]) is str
    assert restored["category"] == "board_governance"
    assert set(restored["probabilities"]) == {"board_governance", "legal"}


def _replace_feedback_requests_with_v18_table(database: Path) -> None:
    with sqlite3.connect(database) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("drop table email_feedback_requests")
        db.execute(
            """
            create table email_feedback_requests (
                feedback_request_id text primary key
                    check(trim(feedback_request_id) != ''),
                classification_id integer not null,
                category text not null check(category in (
                    'important', 'work', 'personal', 'notification',
                    'billing', 'shopping', 'subscription', 'junk'
                )),
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
            """
        )
        db.execute("delete from email_schema_migrations")
        db.execute(
            "insert into email_schema_migrations(version, applied_at) values (18, ?)",
            (datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc).isoformat(),),
        )


def test_v18_feedback_table_migrates_and_accepts_custom_human_confirmation(
    tmp_path: Path,
):
    database = tmp_path / "v18-custom-feedback.sqlite3"
    EmailStore(database)
    _replace_feedback_requests_with_v18_table(database)
    store = EmailStore(database)
    pending = store.persist_scan_result(
        _pending_category_classification("work", classification_id=103),
        model_text="__subject__custom confirmation",
    )

    application = apply_human_confirmation(
        store,
        pending["id"],
        "board_governance",
        feedback_request_id="feedback-board-governance",
        expected_current_action_plan_id=None,
        now=datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc),
    )
    reopened = EmailStore(database)
    restored = reopened.get_classification(pending["id"])

    assert application is not None
    assert application.confirmed["category"] == "board_governance"
    assert type(application.confirmed["action_plan"]["category"]) is str
    assert restored is not None
    assert restored["category"] == "board_governance"
    with sqlite3.connect(database) as db:
        assert db.execute(
            "select max(version) from email_schema_migrations"
        ).fetchone()[0] == 19
        table_sql = db.execute(
            "select sql from sqlite_master where name='email_feedback_requests'"
        ).fetchone()[0]
    assert "category in" not in table_sql.lower()


@pytest.mark.parametrize("legacy_category", ("important", "billing", "subscription"))
def test_reopen_rehydrates_legacy_reserved_classification_plan_and_feedback(
    tmp_path: Path,
    legacy_category: str,
):
    database = tmp_path / "legacy-category-history.sqlite3"
    store = EmailStore(database)
    pending = store.persist_scan_result(
        _pending_category_classification("work", classification_id=104),
        model_text="__subject__legacy history",
    )
    application = apply_human_confirmation(
        store,
        pending["id"],
        "work",
        feedback_request_id="feedback-legacy-history",
        expected_current_action_plan_id=None,
        now=datetime(2026, 9, 7, 12, 2, tzinfo=timezone.utc),
    )
    assert application is not None
    plan_payload = dict(application.confirmed["action_plan"])
    actions = tuple(EmailAction(action) for action in plan_payload["actions"])
    action_parameters = {
        EmailAction(action): parameters
        for action, parameters in plan_payload["action_parameters"].items()
    }
    authorizations = tuple(
        EmailActionAuthorization.model_validate(item)
        for item in plan_payload["action_authorizations"]
    )
    legacy_plan_id = _action_plan_identity(
        action_plan_version=plan_payload["action_plan_version"],
        classification_id=plan_payload["classification_id"],
        account_id=plan_payload["account_id"],
        category=legacy_category,  # type: ignore[arg-type]
        classification_source=plan_payload["classification_source"],
        confidence=plan_payload["confidence"],
        model_id=plan_payload["model_id"],
        config_version=plan_payload["config_version"],
        actions=actions,
        action_parameters=action_parameters,
        created_at=datetime.fromisoformat(plan_payload["created_at"]),
        authorization_snapshot_format=plan_payload["authorization_snapshot_format"],
        action_authorizations=authorizations,
    )
    plan_payload["action_plan_id"] = legacy_plan_id
    plan_payload["category"] = legacy_category
    with sqlite3.connect(database) as db:
        db.execute("pragma foreign_keys=off")
        db.execute(
            "update email_action_plans set action_plan_id=?, category=? "
            "where action_plan_id=?",
            (legacy_plan_id, legacy_category, application.resulting_action_plan_id),
        )
        db.execute(
            """
            update email_classifications
            set category=?, predicted_category=?, confirmed_category=?,
                probabilities_json=?,
                action_plan_json=?, current_action_plan_id=?
            where id=?
            """,
            (
                legacy_category,
                legacy_category,
                legacy_category,
                json.dumps(
                    {legacy_category: 0.79, "work": 0.21},
                    separators=(",", ":"),
                ),
                json.dumps(plan_payload, separators=(",", ":")),
                legacy_plan_id,
                pending["id"],
            ),
        )
        db.execute(
            "update email_feedback_requests set category=?, "
            "resulting_action_plan_id=? where feedback_request_id=?",
            (legacy_category, legacy_plan_id, "feedback-legacy-history"),
        )

    reopened = EmailStore(database)
    restored = reopened.get_classification(pending["id"])

    assert restored is not None
    assert restored["category"] == legacy_category
    assert type(restored["action_plan"]["category"]) is str
    assert restored["action_plan"]["category"] == legacy_category


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
    action_authorizations: tuple[dict[str, object], ...] | None = None,
    classification_id: int | None = None,
    stable_message_identity: str | None = None,
    folder: str = "INBOX",
    uidvalidity: int = 42,
    uid: int | None = None,
    rfc_message_id: str | None = None,
    thread_id: str | None = None,
) -> EmailClassification:
    generated_id = (
        int.from_bytes(sha256(message_id.encode("utf-8")).digest()[:8], "big")
        & ((1 << 63) - 1)
        or 1
    )
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
            action_authorizations=action_authorizations,
        )
    return EmailClassification.model_validate(
        {
            "classification_id": classification_id,
            "stable_message_identity": (stable_message_identity),
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
            "probabilities": {"work": 0.93, "legal": 0.52},
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


def test_persist_scan_result_stores_thread_reference_metadata(tmp_path: Path):
    database = tmp_path / "thread-metadata.sqlite3"
    store = EmailStore(database)
    classification = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="thread-metadata",
        thread_id="thread-1",
    )

    store.persist_scan_result(
        classification,
        sender="sender@example.com",
        subject="Re: project",
        normalized_text="__subject__re project",
        preview="Project update",
        model_text="__subject__re project",
        in_reply_to="<parent@example.com>",
        references=("<thread@example.com>", "<parent@example.com>"),
        cursor_uidvalidity=42,
        cursor_last_seen_uid=classification.provider_locator.uid,
        cursor_last_success_at="2026-08-31T20:00:00+00:00",
    )

    row = _fetchall(
        database, "select in_reply_to, references_json from email_messages"
    )[0]
    assert row["in_reply_to"] == "<parent@example.com>"
    assert json.loads(row["references_json"]) == [
        "<thread@example.com>",
        "<parent@example.com>",
    ]


def _confirm(
    store: EmailStore,
    row_id: int,
    category: EmailCategory,
    *,
    request_id: str | None = None,
) -> dict[str, object] | None:
    return store.confirm_classification(
        row_id,
        category,
        feedback_request_id=(request_id or f"test-feedback-{row_id}-{category.value}"),
        expected_current_action_plan_id=None,
    )


def _fetchall(path: Path, sql: str, parameters: tuple[object, ...] = ()):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, parameters).fetchall()


def test_lists_only_nonterminal_legacy_unsubscribe_attempt_bindings_read_only(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-unsubscribe-inventory.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)

    def create_task(
        name: str,
        *,
        lifecycle_version: str,
        channel: str = "email",
    ) -> int:
        payload = {
            "schema": "email_agent_action.v1",
            "action_type": "unsubscribe",
            "lifecycle_version": lifecycle_version,
            "name": name,
        }
        return task_store.ensure_reply_task(
            channel=channel,
            conversation_id=f"conversation:{name}",
            conversation_title=name,
            single_chat=False,
            trigger_message_id=f"trigger:{name}",
            trigger_create_time="2026-09-02T12:00:00+00:00",
            trigger_sender="newsletter@example.com",
            trigger_text="Legacy unsubscribe inventory fixture.",
            trigger_message_json=json.dumps(payload, sort_keys=True),
            execution_generation=f"generation:{name}",
        ).id

    legacy = "email_unsubscribe_consumer_direct_v1"
    pending_id = create_task("legacy-pending", lifecycle_version=legacy)
    malformed_payload = "{malformed"
    malformed_id = task_store.ensure_reply_task(
        channel="email",
        conversation_id="conversation:malformed-pending",
        conversation_title="malformed-pending",
        single_chat=False,
        trigger_message_id="trigger:malformed-pending",
        trigger_create_time="2026-09-02T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Malformed durable Email task fixture.",
        trigger_message_json=malformed_payload,
    ).id
    create_task(
        "audited-v2",
        lifecycle_version="email_unsubscribe_audited_v2",
    )
    processing_id = create_task("legacy-processing", lifecycle_version=legacy)
    done_id = create_task("legacy-done", lifecycle_version=legacy)
    failed_id = create_task("legacy-failed", lifecycle_version=legacy)
    sent_id = create_task("legacy-sent", lifecycle_version=legacy)
    create_task("non-email", lifecycle_version=legacy, channel="dingtalk")
    with sqlite3.connect(database) as db:
        db.executemany(
            "update reply_tasks set status=? where id=?",
            (
                ("processing", processing_id),
                ("done", done_id),
                ("failed", failed_id),
                ("sent", sent_id),
            ),
        )

    before = [
        tuple(row)
        for row in _fetchall(
            database,
            """
            select id, channel, status, trigger_message_json
            from reply_tasks
            order by id
            """,
        )
    ]

    result = email_store.list_nonterminal_legacy_unsubscribe_task_attempts()

    after = [
        tuple(row)
        for row in _fetchall(
            database,
            """
            select id, channel, status, trigger_message_json
            from reply_tasks
            order by id
            """,
        )
    ]
    assert tuple(
        (attempt.task_id, attempt.execution_generation, attempt.status)
        for attempt in result
    ) == (
        (pending_id, "generation:legacy-pending", "pending"),
        (processing_id, "generation:legacy-processing", "processing"),
    )
    inventoried_ids = {attempt.task_id for attempt in result}
    assert malformed_id not in inventoried_ids
    malformed_before = next(row for row in before if row[0] == malformed_id)
    malformed_after = next(row for row in after if row[0] == malformed_id)
    assert malformed_before == malformed_after
    assert malformed_after[2] == "pending"
    assert malformed_after[3] == malformed_payload
    assert sent_id not in inventoried_ids
    sent_before = next(row for row in before if row[0] == sent_id)
    sent_after = next(row for row in after if row[0] == sent_id)
    assert sent_before == sent_after
    assert sent_after[2] == "sent"
    assert sent_after[3] == json.dumps(
        {
            "schema": "email_agent_action.v1",
            "action_type": "unsubscribe",
            "lifecycle_version": legacy,
            "name": "legacy-sent",
        },
        sort_keys=True,
    )
    assert after == before


def _create_legacy_terminalization_task(
    task_store: AutoReplyStore,
    name: str,
    *,
    lifecycle_version: str = "email_unsubscribe_consumer_direct_v1",
    channel: str = "email",
    trigger_message_json: str | None = None,
):
    payload = trigger_message_json or json.dumps(
        {
            "schema": "email_agent_action.v1",
            "action_type": "unsubscribe",
            "lifecycle_version": lifecycle_version,
        },
        sort_keys=True,
    )
    return task_store.ensure_reply_task(
        channel=channel,
        conversation_id=f"legacy-terminalization:{name}",
        conversation_title=name,
        single_chat=False,
        trigger_message_id=f"legacy-terminalization:{name}",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe terminalization fixture.",
        trigger_message_json=payload,
        execution_generation=f"generation:{name}",
    )


def test_terminalizes_pending_legacy_unsubscribe_with_exact_generation(tmp_path: Path):
    database = tmp_path / "pending-legacy-terminalization.sqlite3"
    task_store = AutoReplyStore(database)
    EmailStore(database)
    task = _create_legacy_terminalization_task(task_store, "pending")

    assert (
        task_store.terminalize_legacy_email_unsubscribe_task(
            task.id,
            expected_execution_generation=task.execution_generation,
            expected_status=task.status,
        )
        is True
    )

    terminal = task_store.get_reply_task(task.id)
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.error == "legacy_email_unsubscribe_lifecycle"
    assert terminal.locked_at is None
    assert terminal.available_at == ""


def test_terminalizes_processing_legacy_unsubscribe_with_exact_generation(
    tmp_path: Path,
):
    database = tmp_path / "processing-legacy-terminalization.sqlite3"
    task_store = AutoReplyStore(database)
    EmailStore(database)
    pending = _create_legacy_terminalization_task(task_store, "processing")
    task = task_store.claim_reply_task(pending.id)
    assert task is not None
    assert task.status == "processing"

    assert (
        task_store.terminalize_legacy_email_unsubscribe_task(
            task.id,
            expected_execution_generation=task.execution_generation,
            expected_status=task.status,
        )
        is True
    )

    terminal = task_store.get_reply_task(task.id)
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.error == "legacy_email_unsubscribe_lifecycle"
    assert terminal.locked_at is None
    assert terminal.available_at == ""


@pytest.mark.parametrize(
    ("name", "task_kwargs", "terminal_status"),
    (
        ("terminal", {}, "done"),
        (
            "audited",
            {"lifecycle_version": "email_unsubscribe_audited_v2"},
            None,
        ),
        ("non-email", {"channel": "dingtalk"}, None),
        ("malformed", {"trigger_message_json": "{malformed"}, None),
    ),
)
def test_legacy_terminalization_leaves_nonmatching_tasks_unchanged(
    tmp_path: Path,
    name: str,
    task_kwargs: dict[str, object],
    terminal_status: str | None,
):
    database = tmp_path / f"nonmatching-{name}.sqlite3"
    task_store = AutoReplyStore(database)
    EmailStore(database)
    task = _create_legacy_terminalization_task(task_store, name, **task_kwargs)
    if terminal_status is not None:
        with sqlite3.connect(database) as db:
            db.execute(
                "update reply_tasks set status=? where id=?",
                (terminal_status, task.id),
            )
    before = tuple(
        _fetchall(
            database,
            "select * from reply_tasks where id=?",
            (task.id,),
        )[0]
    )

    assert (
        task_store.terminalize_legacy_email_unsubscribe_task(
            task.id,
            expected_execution_generation=task.execution_generation,
            expected_status=task.status,
        )
        is False
    )

    after = tuple(
        _fetchall(
            database,
            "select * from reply_tasks where id=?",
            (task.id,),
        )[0]
    )
    assert after == before


@pytest.mark.parametrize("race", ("generation", "lifecycle"))
def test_legacy_terminalization_race_fails_closed_without_modifying_replacement(
    tmp_path: Path,
    race: str,
):
    database = tmp_path / f"legacy-{race}-race.sqlite3"
    task_store = AutoReplyStore(database)
    EmailStore(database)
    original = _create_legacy_terminalization_task(task_store, race)
    with sqlite3.connect(database) as db:
        if race == "generation":
            db.execute(
                "update reply_tasks set execution_generation=? where id=?",
                ("replacement-generation", original.id),
            )
        else:
            replacement_payload = json.dumps(
                {
                    "schema": "email_agent_action.v1",
                    "action_type": "unsubscribe",
                    "lifecycle_version": "email_unsubscribe_audited_v2",
                },
                sort_keys=True,
            )
            db.execute(
                "update reply_tasks set trigger_message_json=? where id=?",
                (replacement_payload, original.id),
            )
    before = tuple(
        _fetchall(
            database,
            "select * from reply_tasks where id=?",
            (original.id,),
        )[0]
    )

    assert (
        task_store.terminalize_legacy_email_unsubscribe_task(
            original.id,
            expected_execution_generation=original.execution_generation,
            expected_status=original.status,
        )
        is False
    )

    after = tuple(
        _fetchall(
            database,
            "select * from reply_tasks where id=?",
            (original.id,),
        )[0]
    )
    assert after == before


def test_pending_legacy_attempt_does_not_terminalize_processing_replacement(
    tmp_path: Path,
):
    database = tmp_path / "legacy-status-race.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    pending = _create_legacy_terminalization_task(task_store, "status-race")
    attempt = email_store.list_nonterminal_legacy_unsubscribe_task_attempts()[0]
    assert attempt.task_id == pending.id
    assert attempt.status == "pending"

    processing = task_store.claim_reply_task(pending.id)
    assert processing is not None
    assert processing.status == "processing"
    before = tuple(
        _fetchall(
            database,
            "select * from reply_tasks where id=?",
            (pending.id,),
        )[0]
    )

    assert (
        task_store.terminalize_legacy_email_unsubscribe_task(
            attempt.task_id,
            expected_execution_generation=attempt.execution_generation,
            expected_status=attempt.status,
        )
        is False
    )

    after = tuple(
        _fetchall(
            database,
            "select * from reply_tasks where id=?",
            (pending.id,),
        )[0]
    )
    assert after == before


_UNSUBSCRIBE_OWNER_A = {
    "owner_id": "email-worker",
    "generation": 21,
    "lease_token": "unsubscribe-lease-a",
}
_UNSUBSCRIBE_OWNER_B = {
    "owner_id": "email-worker",
    "generation": 22,
    "lease_token": "unsubscribe-lease-b",
}


def _unsubscribe_authorization(store: EmailStore) -> dict[str, object]:
    store.create_account(
        {
            "account_id": "dingtalk-account",
            "display_name": "DingTalk",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-test",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://smtp-test",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        thread_id="thread-unsubscribe-41",
    )
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    plan = classification.action_plan
    action_identity = email_action_identity(
        account_id=classification.provider_locator.account_id,
        stable_message_identity=classification.stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=plan.action_plan_version,
    )
    authorization = {
        "action_identity": action_identity,
        "action_plan_id": plan.action_plan_id,
        "action_plan_version": plan.action_plan_version,
        "classification_id": classification.classification_id,
        "account_id": classification.provider_locator.account_id,
        "stable_message_identity": classification.stable_message_identity,
        "thread_identity": "thread-unsubscribe-41",
        "entry_reference": "unsubscribe-entry:" + "b" * 64,
        "operations": [
            {
                "operation_reference": "step-1",
                "kind": "open_entry",
                "target_reference": "entry",
            },
        ],
    }
    authorization["effect_digest"] = email_unsubscribe_effect_digest(**authorization)
    return authorization


def _persist_unsubscribe_result_fixture(
    database: Path,
    *,
    result_text: str,
    observation_digest: str | None = None,
    result_text_digest: str | None = None,
    result_text_truncated: bool | None = None,
) -> tuple[EmailStore, dict[str, object], dict[str, object]]:
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    result_metadata: dict[str, object] = {}
    if observation_digest is not None:
        result_metadata["observation_digest"] = observation_digest
    if result_text_digest is not None:
        result_metadata["result_text_digest"] = result_text_digest
    if result_text_truncated is not None:
        result_metadata["result_text_truncated"] = result_text_truncated
    receipt = store.persist_email_unsubscribe_terminal(
        **authorization,
        outcome="done",
        receipt_id="unsubscribe-receipt:result-text",
        evidence="terminal-page",
        result_text=result_text,
        **result_metadata,
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "done",
            "reference": "unsubscribe-receipt:result-text",
        },
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )
    return store, authorization, receipt


def _downgrade_email_database_to_v16(database: Path) -> None:
    """Recreate the exact parent-v16 receipt shape from a current fixture."""

    with sqlite3.connect(database) as db:
        db.execute(
            "drop index if exists idx_email_unsubscribe_receipts_classification_action"
        )
        receipt_columns = {
            row[1]
            for row in db.execute("pragma table_info(email_unsubscribe_receipts)")
        }
        for column in ("result_text_digest", "result_text_truncated"):
            if column in receipt_columns:
                db.execute(
                    f"alter table email_unsubscribe_receipts drop column {column}"
                )
        db.execute("delete from email_schema_migrations")
        db.execute(
            "insert into email_schema_migrations(version, applied_at) values (16, ?)",
            ("2026-09-03T00:00:00+00:00",),
        )


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
    quote_styles = (('"', '"'), ("`", "`"), ("[", "]"))
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
                classification.category,
                classification.confidence,
                classification.margin,
                json.dumps(classification.probabilities),
                classification.model_id,
                classification.config_version,
                classification.status.value,
                classification.classification_source,
                action_plan_json,
                now
                if classification.status is EmailClassificationStatus.PROCESSED
                else "",
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


def _create_provider_mailbox_prototype_database(database: Path) -> None:
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            create table email_classifications (
                id integer primary key autoincrement,
                provider text not null,
                mailbox text not null,
                message_id text not null,
                thread_id text not null default '',
                sender text not null default '',
                subject text not null default '',
                preview text not null default '',
                model_text text not null default '',
                received_at text not null default '',
                category text not null,
                confidence real not null,
                margin real not null,
                probabilities_json text not null,
                model_version text not null,
                config_version text not null,
                status text not null,
                classification_source text not null,
                action_plan_json text not null,
                confirmed_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(provider, mailbox, message_id)
            );
            create table email_category_configs (
                category text primary key,
                description text not null default '',
                threshold real not null,
                actions_json text not null,
                enabled integer not null default 1,
                config_version text not null,
                updated_at text not null default current_timestamp
            );
            insert into email_classifications (
                provider, mailbox, message_id, thread_id, sender, subject,
                preview, model_text, received_at, category, confidence, margin,
                probabilities_json, model_version, config_version, status,
                classification_source, action_plan_json, confirmed_at,
                created_at, updated_at
            ) values (
                'dingtalk', 'INBOX', '<legacy@example.com>', 'thread-legacy',
                'sender@example.com', 'Legacy subject', 'Legacy preview',
                '__subject__legacy subject', '2026-08-29T15:59:00+00:00',
                'work', 0.82, 0.32, '{"work":0.82}',
                'email/logistic/legacy', 'legacy-config', 'pending_feedback',
                'model', 'null', '', '2026-08-29T16:00:00+00:00',
                '2026-08-29T16:00:00+00:00'
            );
            insert into email_category_configs (
                category, description, threshold, actions_json, enabled,
                config_version, updated_at
            ) values (
                'work', 'Legacy work config', 0.7, '["label"]', 1,
                'legacy-config', '2026-08-29T16:00:00+00:00'
            );
            """
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
            provider_result_id=(
                f"receipt-{attempt_number}" if status == "done" else ""
            ),
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


def test_email_store_gets_one_classification_directly_by_primary_key(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    pending = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="primary-key-pending",
        )
    )
    processed = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            message_id="primary-key-processed",
        )
    )

    assert store.get_classification(pending["id"])["status"] == "pending_feedback"
    assert store.get_classification(processed["id"])["status"] == "processed"
    assert store.get_classification(999) is None


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

    confirmed = _confirm(store, row["id"], EmailCategory.NOTIFICATION)

    assert confirmed is not None
    assert confirmed["category"] == "notification"
    assert confirmed["status"] == "processed"
    assert confirmed["classification_source"] == "user"
    assert confirmed["action_plan"]["category"] == "notification"
    assert confirmed["action_plan"]["classification_source"] == "user"
    assert _confirm(store, 999, EmailCategory.WORK) is None


def test_feedback_rebuilds_action_plan_for_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    store.upsert_config(
        category=EmailCategory.NOTIFICATION,
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

    confirmed = _confirm(store, row["id"], EmailCategory.NOTIFICATION)

    assert confirmed is not None
    assert confirmed["category"] == "notification"
    assert confirmed["config_version"] == "important-v2"
    assert confirmed["action_plan"]["action_plan_version"] == 1
    assert confirmed["action_plan"]["classification_id"] == confirmed["id"]
    assert confirmed["action_plan"]["account_id"] == "dingtalk-account"
    assert confirmed["action_plan"]["category"] == "notification"
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
        _confirm(store, row["id"], EmailCategory.NOTIFICATION)


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
            return _confirm(store, row["id"], category)
        except EmailClassificationConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                confirm,
                (EmailCategory.NOTIFICATION, EmailCategory.PERSONAL),
            )
        )

    confirmed = [result for result in results if isinstance(result, dict)]
    conflicts = [
        result for result in results if isinstance(result, EmailClassificationConflict)
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
    confirmed_row = _confirm(
        store,
        confirmed["id"],
        EmailCategory.NOTIFICATION,
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
            "label": "notification",
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
        rows.append(_confirm(store, row["id"], category))
    snapshots = store.list_unincluded_training_examples()

    assert len(snapshots) == 2
    assert all(len(row["sample_digest"]) == 64 for row in snapshots)
    store.mark_training_examples_included(
        snapshots, model_id="email-tfidf-lr-x-12345678"
    )
    store.mark_training_examples_included(
        snapshots, model_id="email-tfidf-lr-x-12345678"
    )

    assert store.list_unincluded_training_examples() == []
    assert {
        row["included_in_model_id"]
        for row in store.list_training_examples(include_inclusion=True)
    } == {"email-tfidf-lr-x-12345678"}


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
        confirmed = _confirm(store, row["id"], category)
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
    _confirm(store, row["id"], EmailCategory.WORK)
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


def test_training_promotion_lease_rejects_changed_snapshot_before_promote(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "lease-cas.sqlite3")
    row = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="lease-cas",
        ),
        model_text="__subject__before",
    )
    _confirm(store, row["id"], EmailCategory.WORK)
    snapshot = store.list_unincluded_training_examples()[0]
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update email_classifications set model_text='__subject__after' where id=?",
            (row["id"],),
        )
    promoted = []

    with pytest.raises(EmailTrainingInclusionConflict):
        store.commit_training_promotion(
            [snapshot],
            model_id="candidate",
            promote=lambda: promoted.append(True),
            restore=lambda: None,
        )

    assert promoted == []


def test_training_promotion_lease_blocks_feedback_write_during_manifest_switch(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "lease-lock.sqlite3")
    row = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="lease-lock",
        ),
        model_text="__subject__locked",
    )
    _confirm(store, row["id"], EmailCategory.WORK)
    snapshot = store.list_unincluded_training_examples()[0]
    blocked = []

    def promote():
        connection = sqlite3.connect(store.path, timeout=0)
        try:
            connection.execute("begin immediate")
        except sqlite3.OperationalError as exc:
            blocked.append("locked" in str(exc).lower())
        finally:
            connection.close()

    store.commit_training_promotion(
        [snapshot], model_id="candidate", promote=promote, restore=lambda: None
    )

    assert blocked == [True]


def test_rescan_preserves_a_user_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    original = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )
    _confirm(store, original["id"], EmailCategory.NOTIFICATION)

    rescanned = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )

    assert rescanned["id"] == original["id"]
    assert rescanned["category"] == "notification"
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
    confirmed = _confirm(store, original["id"], EmailCategory.NOTIFICATION)
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
        category=EmailCategory.EXTERNAL_BILLING,
        description="营销订阅和定期通讯",
        threshold=0.98,
        actions=(EmailAction.LABEL, EmailAction.UNSUBSCRIBE),
        action_parameters={EmailAction.LABEL: {"labels": ["subscription"]}},
        enabled=True,
        config_version="email-v2",
    )

    assert config["actions"] == ["label", "unsubscribe"]
    assert config["action_parameters"] == {"label": {"labels": ["subscription"]}}
    assert store.list_configs() == [config]


def test_email_store_rejects_auto_reply_category_configuration(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")

    with pytest.raises(ValueError, match="auto_reply is disabled"):
        store.upsert_config(
            category=EmailCategory.WORK,
            description="Work",
            threshold=0.95,
            actions=(EmailAction.AUTO_REPLY,),
            action_parameters={
                EmailAction.AUTO_REPLY: {"instruction": "Reply automatically"}
            },
            enabled=True,
            config_version="email-config:auto-reply-rejected",
        )


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
        "email_feedback_requests",
        "email_reply_receipts",
        "email_reply_dispatch_claims",
        "email_unsubscribe_claims",
        "email_unsubscribe_effects",
        "email_unsubscribe_continuations",
        "email_unsubscribe_steps",
        "email_unsubscribe_receipts",
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

    feedback_columns = {
        row["name"]
        for row in _fetchall(database, "pragma table_info(email_feedback_requests)")
    }
    assert feedback_columns == {
        "feedback_request_id",
        "classification_id",
        "category",
        "expected_current_action_plan_id",
        "resulting_action_plan_id",
        "applied_at",
    }
    reply_claim_columns = {
        row["name"]
        for row in _fetchall(
            database,
            "pragma table_info(email_reply_dispatch_claims)",
        )
    }
    assert {
        "owner_id",
        "owner_generation",
        "lease_token",
        "sender",
        "thread_identity",
        "account_updated_at",
        "account_snapshot_json",
    } <= reply_claim_columns
    assert [
        row["version"]
        for row in _fetchall(database, "select version from email_schema_migrations")
    ] == [email_store_module.EMAIL_SCHEMA_VERSION]


def test_v10_unsubscribe_claim_migrates_to_v11_effect_prefix_chain(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v10-unsubscribe-prefix-migration.sqlite3"
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    assert store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )["acquired"]
    _downgrade_email_database_to_v16(database)
    with sqlite3.connect(database) as db:
        db.execute("drop table email_unsubscribe_continuations")
        db.execute("drop table email_unsubscribe_effects")
        db.execute("update email_schema_migrations set version=10")

    migrated = EmailStore(database)

    claim = migrated.get_email_unsubscribe_claim(authorization["action_identity"])
    assert claim is not None
    assert claim["effect_digest"] == authorization["effect_digest"]
    with sqlite3.connect(database) as db:
        effect = db.execute(
            "select previous_effect_digest from email_unsubscribe_effects where action_identity=?",
            (authorization["action_identity"],),
        ).fetchone()
        assert effect is not None
        assert effect[0] == ""
        assert (
            db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            == email_store_module.EMAIL_SCHEMA_VERSION
        )


def test_exact_v14_unsubscribe_schema_migrates_with_nullable_positive_audit_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v14-unsubscribe-audit-migration.sqlite3"
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    assert store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )["acquired"]

    _downgrade_email_database_to_v16(database)

    with sqlite3.connect(database) as db:
        db.execute("pragma foreign_keys=off")
        db.executescript(
            """
            drop trigger trg_email_unsubscribe_blocks_plan_switch;
            drop trigger trg_email_unsubscribe_blocks_account_update;
            drop trigger trg_email_unsubscribe_blocks_account_delete;
            drop trigger trg_email_unsubscribe_blocks_message_update;
            drop trigger trg_email_unsubscribe_blocks_message_delete;

            create table email_unsubscribe_claims_v14 (
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
                claimed_at text not null check(trim(claimed_at) != ''),
                updated_at text not null check(trim(updated_at) != ''),
                foreign key(action_plan_id)
                    references email_action_plans(action_plan_id)
                    on delete restrict,
                foreign key(classification_id)
                    references email_classifications(id)
                    on delete restrict
            );
            insert into email_unsubscribe_claims_v14 (
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
                   account_updated_at, status, phase, claimed_at, updated_at
            from email_unsubscribe_claims;

            create table email_unsubscribe_effects_v14 (
                action_identity text not null check(trim(action_identity) != ''),
                effect_digest text not null check(trim(effect_digest) != ''),
                previous_effect_digest text not null default ''
                    check(previous_effect_digest = '' or length(previous_effect_digest) = 64),
                operations_json text not null check(json_valid(operations_json)),
                network_policy_reference text not null
                    check(trim(network_policy_reference) != ''),
                network_policy_origins_json text not null
                    check(json_valid(network_policy_origins_json)),
                created_at text not null check(trim(created_at) != ''),
                primary key(action_identity, effect_digest),
                foreign key(action_identity)
                    references email_unsubscribe_claims_v14(action_identity)
                    on delete restrict
            );
            insert into email_unsubscribe_effects_v14 (
                action_identity, effect_digest, previous_effect_digest,
                operations_json, network_policy_reference,
                network_policy_origins_json, created_at
            )
            select action_identity, effect_digest, previous_effect_digest,
                   operations_json, network_policy_reference,
                   network_policy_origins_json, created_at
            from email_unsubscribe_effects;

            drop table email_unsubscribe_effects;
            drop table email_unsubscribe_claims;
            alter table email_unsubscribe_claims_v14
                rename to email_unsubscribe_claims;
            alter table email_unsubscribe_effects_v14
                rename to email_unsubscribe_effects;
            update email_schema_migrations set version=14;
            """
        )

    migrated = EmailStore(database)
    claim = migrated.get_email_unsubscribe_claim(authorization["action_identity"])
    assert claim is not None
    assert claim["audit_agent_run_id"] is None
    with sqlite3.connect(database) as db:
        effect_audit_run_id = db.execute(
            "select audit_agent_run_id from email_unsubscribe_effects "
            "where action_identity=?",
            (authorization["action_identity"],),
        ).fetchone()[0]
        assert effect_audit_run_id is None
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "update email_unsubscribe_claims set audit_agent_run_id=0 "
                "where action_identity=?",
                (authorization["action_identity"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "update email_unsubscribe_effects set audit_agent_run_id=-1 "
                "where action_identity=?",
                (authorization["action_identity"],),
            )


def test_v11_unsubscribe_schema_migrates_missing_phase_and_receipt_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v11-unsubscribe-schema-migration.sqlite3"
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True

    # Reproduce the deployed v11 shape: the tables already exist, but the
    # later phase/result evidence constraints were never rebuilt in place.
    with sqlite3.connect(database) as db:
        db.execute("pragma foreign_keys = off")
        for trigger in (
            "trg_email_unsubscribe_blocks_plan_switch",
            "trg_email_unsubscribe_blocks_account_update",
            "trg_email_unsubscribe_blocks_account_delete",
            "trg_email_unsubscribe_blocks_message_update",
            "trg_email_unsubscribe_blocks_message_delete",
        ):
            db.execute(f"drop trigger if exists {trigger}")
        db.execute("drop index if exists idx_email_unsubscribe_claims_status")
        db.execute(
            """
            create table email_unsubscribe_claims_pre_v14 as
            select action_identity, effect_digest, action_plan_id,
                action_plan_version, classification_id, account_id,
                stable_message_identity, thread_identity, entry_reference,
                operations_json, owner_id, owner_generation, lease_token,
                account_updated_at, status, claimed_at, updated_at
            from email_unsubscribe_claims
            """
        )
        db.execute(
            """
            create table email_unsubscribe_effects_pre_v14 as
            select action_identity, effect_digest, previous_effect_digest,
                operations_json, network_policy_reference,
                network_policy_origins_json, created_at
            from email_unsubscribe_effects
            """
        )
        db.execute(
            """
            create table email_unsubscribe_continuations_pre_v14 as
            select action_identity, effect_digest, observation_reference,
                controls_json, created_at, updated_at
            from email_unsubscribe_continuations
            """
        )
        db.execute(
            """
            create table email_unsubscribe_steps_pre_v14 as
            select id, action_identity, effect_digest, sequence, operation,
                state, reference, created_at
            from email_unsubscribe_steps
            """
        )
        db.execute(
            """
            create table email_unsubscribe_receipts_pre_v14 as
            select action_identity, effect_digest, action_plan_id,
                action_plan_version, classification_id, account_id,
                stable_message_identity, thread_identity, entry_reference,
                outcome, receipt_id, evidence, created_at
            from email_unsubscribe_receipts
            """
        )
        for table in (
            "email_unsubscribe_steps",
            "email_unsubscribe_receipts",
            "email_unsubscribe_continuations",
            "email_unsubscribe_effects",
            "email_unsubscribe_claims",
        ):
            db.execute(f"drop table {table}")
        for table in (
            "email_unsubscribe_claims",
            "email_unsubscribe_effects",
            "email_unsubscribe_continuations",
            "email_unsubscribe_steps",
            "email_unsubscribe_receipts",
        ):
            db.execute(f"alter table {table}_pre_v14 rename to {table}")
        db.execute("update email_schema_migrations set version=11")

    migrated = EmailStore(database)

    migrated_claim = migrated.get_email_unsubscribe_claim(
        authorization["action_identity"]
    )
    assert migrated_claim is not None
    assert migrated_claim["phase"] == "prepared"
    with sqlite3.connect(database) as db:
        assert (
            db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            == email_store_module.EMAIL_SCHEMA_VERSION
        )
        claims_sql = db.execute(
            "select sql from sqlite_master where type='table' and name=?",
            ("email_unsubscribe_claims",),
        ).fetchone()[0]
        receipts_sql = db.execute(
            "select sql from sqlite_master where type='table' and name=?",
            ("email_unsubscribe_receipts",),
        ).fetchone()[0]
        assert "phase text not null default 'prepared'" in claims_sql
        assert "phase in (" in claims_sql
        assert "result_text text not null default ''" in receipts_sql


def test_v9_store_atomically_adds_unsubscribe_durability_without_data_loss(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v9-unsubscribe-migration.sqlite3"
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    account_before = store.get_account(str(authorization["account_id"]))
    with sqlite3.connect(database) as db:
        for trigger in (
            "trg_email_unsubscribe_blocks_plan_switch",
            "trg_email_unsubscribe_blocks_account_update",
            "trg_email_unsubscribe_blocks_account_delete",
            "trg_email_unsubscribe_blocks_message_update",
            "trg_email_unsubscribe_blocks_message_delete",
        ):
            db.execute(f"drop trigger {trigger}")
        db.execute("drop table email_unsubscribe_steps")
        db.execute("drop table email_unsubscribe_receipts")
        db.execute("drop table email_unsubscribe_claims")
        db.execute("update email_schema_migrations set version=9")

    migrated = EmailStore(database)

    assert migrated.get_account(str(authorization["account_id"])) == account_before
    with sqlite3.connect(database) as db:
        assert (
            db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            == email_store_module.EMAIL_SCHEMA_VERSION
        )
        assert {
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        } >= {
            "email_unsubscribe_claims",
            "email_unsubscribe_steps",
            "email_unsubscribe_receipts",
        }


def test_reopen_rejects_feedback_request_linked_to_another_classification(
    tmp_path: Path,
):
    database = tmp_path / "feedback-corruption.sqlite3"
    store = EmailStore(database)
    first = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="feedback-first",
        )
    )
    second = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="feedback-second",
        )
    )
    applied = store.apply_human_classification(
        first["id"],
        EmailCategory.NOTIFICATION,
        feedback_request_id="feedback-request-1",
        expected_current_action_plan_id=None,
    )
    assert applied is not None
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_feedback_requests set classification_id=?",
            (second["id"],),
        )

    with pytest.raises(EmailPersistenceCorruption, match="feedback request"):
        EmailStore(database)


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
                "important",
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
        assert (
            db.execute(
                "select 1 from sqlite_master "
                "where type='table' and name='email_schema_migrations'"
            ).fetchone()
            is None
        )
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


def test_email_schema_version_is_19() -> None:
    assert email_store_module.EMAIL_SCHEMA_VERSION == 19


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
        assert (
            db.execute("pragma schema_version").fetchone()[0] == schema_version_before
        )
    normalized = [" ".join(statement.lower().split()) for statement in statements]
    assert "begin immediate" not in normalized
    assert not any(
        statement.startswith(
            (
                "create ",
                "alter ",
                "insert ",
                "update ",
                "delete ",
                "pragma journal_mode",
            )
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
            "text not null check(status in ('PENDING', 'PROCESSING', 'DONE', 'FAILED'))"
        ),
    )

    with pytest.raises(
        EmailPersistenceCorruption, match="required check.*email_actions"
    ):
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
        "description" if damaged_declaration.startswith("description") else "threshold"
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
        db.execute("drop trigger trg_email_reply_dispatch_blocks_thread_update")
        db.execute("drop trigger trg_email_unsubscribe_blocks_message_update")
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


def test_current_schema_requires_unsubscribe_receipt_classification_index(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-unsubscribe-receipt-index.sqlite3"
    EmailStore(database)
    index_name = "idx_email_unsubscribe_receipts_classification_action"
    with sqlite3.connect(database) as db:
        indexes = {
            row[1]
            for row in db.execute("pragma index_list(email_unsubscribe_receipts)")
        }
        assert index_name in indexes
        db.execute(f"drop index {index_name}")

    with pytest.raises(EmailPersistenceCorruption, match=index_name):
        EmailStore(database)
    with sqlite3.connect(database) as db:
        assert index_name not in {
            row[1]
            for row in db.execute("pragma index_list(email_unsubscribe_receipts)")
        }


@pytest.mark.parametrize(
    "missing_column",
    ("result_text_truncated", "result_text_digest"),
)
def test_current_v17_schema_missing_result_integrity_column_fails_without_repair(
    tmp_path: Path,
    missing_column: str,
) -> None:
    database = tmp_path / f"missing-{missing_column}.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            f"alter table email_unsubscribe_receipts drop column {missing_column}"
        )

    with pytest.raises(EmailPersistenceCorruption, match=missing_column):
        EmailStore(database)
    with sqlite3.connect(database) as db:
        assert missing_column not in {
            row[1]
            for row in db.execute("pragma table_info(email_unsubscribe_receipts)")
        }


def test_legitimate_v16_upgrades_to_v17_with_receipt_integrity_metadata(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v16-to-v17.sqlite3"
    _, authorization, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    _downgrade_email_database_to_v16(database)

    reopened = EmailStore(database)

    migrated = reopened.get_email_unsubscribe_receipt(
        str(authorization["action_identity"])
    )
    assert migrated is not None
    bounded_digest = sha256(receipt["result_text"].encode("utf-8")).hexdigest()
    assert migrated["result_text_truncated"] is False
    assert migrated["result_text_digest"] == bounded_digest
    with sqlite3.connect(database) as db:
        assert [
            row[0]
            for row in db.execute(
                "select version from email_schema_migrations order by version"
            )
        ] == [16, 17, 18, 19]
        assert {
            row[1]
            for row in db.execute("pragma table_info(email_unsubscribe_receipts)")
        } >= {"result_text_truncated", "result_text_digest"}
        assert "idx_email_unsubscribe_receipts_classification_action" in {
            row[1]
            for row in db.execute("pragma index_list(email_unsubscribe_receipts)")
        }


def test_audited_unsubscribe_lineage_query_uses_exact_primary_key_chain(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-lineage-query-plan.sqlite3"
    EmailStore(database)
    AutoReplyStore(database)
    with sqlite3.connect(database) as db:
        details = [
            str(row[3])
            for row in db.execute(
                "explain query plan "
                + email_store_module._AUDITED_UNSUBSCRIBE_LINEAGE_SQL,
                ("email-action:test", "a" * 64),
            )
        ]

    assert any(
        "SEARCH effects USING INDEX sqlite_autoindex_email_unsubscribe_effects_1"
        in detail
        for detail in details
    )
    assert any(
        "SEARCH audit_runs USING INTEGER PRIMARY KEY" in detail for detail in details
    )
    assert any("SEARCH tasks USING INTEGER PRIMARY KEY" in detail for detail in details)
    assert not any("SCAN tasks" in detail for detail in details)


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
    assert (
        _fetchall(
            database,
            "select state_json from email_retraining_state",
        )[0]["state_json"]
        == '{"last_feedback_count":7}'
    )
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
    ] == [2, 16, 17, 18, email_store_module.EMAIL_SCHEMA_VERSION]

    EmailStore(database)

    reopened = _fetchall(database, "select * from email_classifications")[0]
    assert dict(reopened) == dict(classification)
    assert (
        dict(_fetchall(database, "select * from email_messages")[0]) == message_before
    )
    assert _fetchall(database, "select * from email_action_plans") == []
    assert _fetchall(database, "select * from email_actions") == []


def test_exact_v15_legacy_action_plan_upgrades_without_rewriting_history(
    tmp_path: Path,
):
    database = tmp_path / "v15-action-plan.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    legacy_json = classification.action_plan.model_dump_json(
        exclude={"authorization_snapshot_format", "action_authorizations"}
    )
    historical_plan_id = classification.action_plan.action_plan_id
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set action_plan_json=?",
            (legacy_json,),
        )
        db.execute(
            "alter table email_action_plans drop column authorization_snapshot_json"
        )
        columns = {
            row[1] for row in db.execute("pragma table_info(email_action_plans)")
        }
        if "legacy_serialization_pre_v16" in columns:
            db.execute(
                "alter table email_action_plans "
                "drop column legacy_serialization_pre_v16"
            )
    _downgrade_email_database_to_v16(database)
    with sqlite3.connect(database) as db:
        db.execute("update email_schema_migrations set version=15")

    reopened = EmailStore(database)

    persisted = _fetchall(
        database,
        "select action_plan_json, current_action_plan_id from email_classifications",
    )[0]
    assert persisted["action_plan_json"] == legacy_json
    assert persisted["current_action_plan_id"] == historical_plan_id
    [stored_plan] = _fetchall(database, "select * from email_action_plans")
    assert stored_plan["action_plan_id"] == historical_plan_id
    assert stored_plan["authorization_snapshot_json"] is None
    assert stored_plan["legacy_serialization_pre_v16"] == 1
    assert [
        row["version"]
        for row in _fetchall(
            database,
            "select version from email_schema_migrations order by version",
        )
    ] == [15, 16, 17, 18, 19]
    projected = reopened.get_classification(classification.classification_id)
    assert projected is not None
    assert projected["action_plan"]["action_plan_id"] == historical_plan_id


def test_new_v16_action_plan_rejects_legacy_canonical_json_without_provenance(
    tmp_path: Path,
):
    database = tmp_path / "v16-action-plan-missing-fields.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    stripped_json = classification.action_plan.model_dump_json(
        exclude={"authorization_snapshot_format", "action_authorizations"}
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set action_plan_json=?",
            (stripped_json,),
        )

    with pytest.raises(EmailPersistenceCorruption, match="snapshot mismatch"):
        EmailStore(database)

    assert (
        _fetchall(database, "select action_plan_json from email_classifications")[0][
            "action_plan_json"
        ]
        == stripped_json
    )


def test_upgraded_database_does_not_extend_legacy_permission_to_new_v16_plan(
    tmp_path: Path,
):
    database = tmp_path / "v15-and-v16-action-plans.sqlite3"
    store = EmailStore(database)
    legacy = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="legacy-before-v16",
    )
    _persist_scan(store, legacy)
    assert legacy.action_plan is not None
    legacy_json = legacy.action_plan.model_dump_json(
        exclude={"authorization_snapshot_format", "action_authorizations"}
    )
    legacy_plan_id = legacy.action_plan.action_plan_id
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set action_plan_json=? where id=?",
            (legacy_json, legacy.classification_id),
        )
        db.execute(
            "alter table email_action_plans drop column authorization_snapshot_json"
        )
        columns = {
            row[1] for row in db.execute("pragma table_info(email_action_plans)")
        }
        if "legacy_serialization_pre_v16" in columns:
            db.execute(
                "alter table email_action_plans "
                "drop column legacy_serialization_pre_v16"
            )
    _downgrade_email_database_to_v16(database)
    with sqlite3.connect(database) as db:
        db.execute("update email_schema_migrations set version=15")

    upgraded = EmailStore(database)
    persisted_legacy = upgraded.get_classification(legacy.classification_id)
    assert persisted_legacy is not None
    assert persisted_legacy["action_plan"]["action_plan_id"] == legacy_plan_id
    assert (
        _fetchall(
            database,
            "select action_plan_json from email_classifications where id=?",
            (legacy.classification_id,),
        )[0]["action_plan_json"]
        == legacy_json
    )

    current = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="current-after-v16",
        action_authorizations=(
            {
                "action_type": EmailAction.LABEL,
                "parameters": {"labels": [EmailCategory.WORK.value]},
                "authorization_source": "model_eligibility",
                "eligibility_evidence_reference": "evidence:model-1:label:v16",
                "authorized": True,
                "ineligible_reason": "",
                "source_model_id": "email/logistic/model-1",
                "config_version": "email-v1",
            },
        ),
    )
    _persist_scan(upgraded, current)
    assert current.action_plan is not None
    stripped_current_json = current.action_plan.model_dump_json(
        exclude={"authorization_snapshot_format", "action_authorizations"}
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_classifications set action_plan_json=? where id=?",
            (stripped_current_json, current.classification_id),
        )

    with pytest.raises(EmailPersistenceCorruption, match="snapshot mismatch"):
        EmailStore(database)

    persisted_plans = {
        row["action_plan_id"]: row
        for row in _fetchall(database, "select * from email_action_plans")
    }
    assert persisted_plans[legacy_plan_id]["legacy_serialization_pre_v16"] == 1
    assert (
        persisted_plans[current.action_plan.action_plan_id][
            "legacy_serialization_pre_v16"
        ]
        == 0
    )
    assert persisted_plans[legacy_plan_id]["authorization_snapshot_json"] is None
    assert (
        persisted_plans[current.action_plan.action_plan_id][
            "authorization_snapshot_json"
        ]
        is not None
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_action_plans set legacy_serialization_pre_v16=1 "
            "where action_plan_id=?",
            (current.action_plan.action_plan_id,),
        )
    with pytest.raises(EmailPersistenceCorruption, match="snapshot mismatch"):
        EmailStore(database)
    assert (
        _fetchall(
            database,
            "select action_plan_json from email_classifications where id=?",
            (legacy.classification_id,),
        )[0]["action_plan_json"]
        == legacy_json
    )


def test_current_v16_missing_authorization_column_fails_without_schema_repair(
    tmp_path: Path,
):
    database = tmp_path / "v16-missing-authorization-column.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "alter table email_action_plans drop column authorization_snapshot_json"
        )
        schema_before = list(
            db.execute(
                "select type, name, sql from sqlite_master "
                "where name not like 'sqlite_%' order by type, name"
            )
        )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="email_action_plans.*authorization_snapshot_json",
    ):
        EmailStore(database)

    with sqlite3.connect(database) as db:
        schema_after = list(
            db.execute(
                "select type, name, sql from sqlite_master "
                "where name not like 'sqlite_%' order by type, name"
            )
        )
        columns = {
            row[1] for row in db.execute("pragma table_info(email_action_plans)")
        }
    assert schema_after == schema_before
    assert "authorization_snapshot_json" not in columns


def test_current_v16_missing_legacy_provenance_column_fails_without_schema_repair(
    tmp_path: Path,
):
    database = tmp_path / "v16-missing-legacy-provenance-column.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        columns = {
            row[1] for row in db.execute("pragma table_info(email_action_plans)")
        }
        if "legacy_serialization_pre_v16" not in columns:
            db.execute(
                "alter table email_action_plans add column "
                "legacy_serialization_pre_v16 integer not null default 0 "
                "check(legacy_serialization_pre_v16 in (0, 1))"
            )
        db.execute(
            "alter table email_action_plans drop column legacy_serialization_pre_v16"
        )
        schema_before = list(
            db.execute(
                "select type, name, sql from sqlite_master "
                "where name not like 'sqlite_%' order by type, name"
            )
        )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="email_action_plans.*legacy_serialization_pre_v16",
    ):
        EmailStore(database)

    with sqlite3.connect(database) as db:
        schema_after = list(
            db.execute(
                "select type, name, sql from sqlite_master "
                "where name not like 'sqlite_%' order by type, name"
            )
        )
        columns = {
            row[1] for row in db.execute("pragma table_info(email_action_plans)")
        }
    assert schema_after == schema_before
    assert "legacy_serialization_pre_v16" not in columns


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

    store = EmailStore(database)

    persisted = _fetchall(
        database,
        "select action_plan_json, current_action_plan_id from email_classifications",
    )[0]
    assert persisted["action_plan_json"] == classification.action_plan.model_dump_json()
    assert (
        persisted["current_action_plan_id"] == classification.action_plan.action_plan_id
    )
    assert len(_fetchall(database, "select * from email_action_plans")) == 1
    actions = _fetchall(
        database,
        "select action_type, status, attempt_count from email_actions",
    )
    assert [
        (row["action_type"], row["status"], row["attempt_count"]) for row in actions
    ] == [("label", "pending", 0)]
    projected = store.get_classification(classification.classification_id)
    assert projected is not None
    assert projected["action_plan"]["action_plan_id"] == (
        classification.action_plan.action_plan_id
    )
    assert (
        projected["action_plan"]["authorization_snapshot_format"]
        == "legacy_unavailable_v1"
    )
    assert projected["action_plan"]["action_authorizations"] == []
    [stored_plan] = _fetchall(database, "select * from email_action_plans")
    assert stored_plan["authorization_snapshot_json"] is None


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


def test_concurrent_v16_to_v17_migration_is_transactionally_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent-v16-v17.sqlite3"
    EmailStore(database)
    _downgrade_email_database_to_v16(database)
    ready = Barrier(2)

    def initialize(_: int) -> EmailStore:
        ready.wait()
        return EmailStore(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        stores = list(executor.map(initialize, range(2)))

    assert len(stores) == 2
    assert [
        row["version"]
        for row in _fetchall(
            database,
            "select version from email_schema_migrations order by version",
        )
    ] == [16, 17, 18, 19]


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
    _downgrade_email_database_to_v16(database)
    with sqlite3.connect(database) as db:
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
        stable_message_identity=("dingtalk-account:message-id:<Canonical@example.com>"),
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
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )
    store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
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

    with pytest.raises(
        EmailPersistenceCorruption, match="pending feedback.*ActionPlan"
    ):
        EmailStore(database)


def test_startup_rejects_pending_feedback_with_user_source(tmp_path: Path):
    database = tmp_path / "pending-user-source.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute("update email_classifications set classification_source='user'")

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
        db.execute("update email_classifications set legacy_processed_without_plan=1")

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

    assert (
        persisted["current_action_plan_id"] == classification.action_plan.action_plan_id
    )
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
        action_parameters={EmailAction.MOVE: {"target_folder": "Archive/Personal"}},
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
        if field not in {
            "folder",
            "uidvalidity",
            "uid",
            "thread_identity",
            "updated_at",
        }:
            assert message_after[field] == value
    assert rescanned["category"] == classification_before["category"]
    assert rescanned["model_id"] == classification_before["model_id"]
    assert rescanned["confidence"] == classification_before["confidence"]
    assert rescanned["config_version"] == classification_before["config_version"]
    assert (
        rescanned["current_action_plan_id"]
        == classification_before["current_action_plan_id"]
    )
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
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE, EmailAction.UNSUBSCRIBE),
        action_parameters={},
    )

    corrected = store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
    )
    replayed = store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
    )

    assert first["current_action_plan_id"] != corrected["current_action_plan_id"]
    assert replayed["current_action_plan_id"] == second_plan.action_plan_id
    assert corrected["confirmed_category"] == "notification"
    plans = _fetchall(
        database,
        "select action_plan_id, action_plan_version from email_action_plans order by action_plan_version",
    )
    assert [(row["action_plan_id"], row["action_plan_version"]) for row in plans] == [
        (classification.action_plan.action_plan_id, 1),
        (second_plan.action_plan_id, 2),
    ]
    assert len(_fetchall(database, "select * from email_actions")) == 2


@pytest.mark.parametrize("invalid_category", ("subscription", " Work", 123))
def test_append_action_plan_version_rejects_invalid_correction_category(
    tmp_path: Path,
    invalid_category: object,
):
    store = EmailStore(tmp_path / "invalid-correction-category.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    corrected = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )

    with pytest.raises(ValueError):
        store.append_action_plan_version(
            classification.classification_id,
            corrected,
            confirmed_category=invalid_category,  # type: ignore[arg-type]
        )


def test_changed_snapshot_cannot_reuse_an_existing_plan_version(tmp_path: Path):
    database = tmp_path / "plan-conflict.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    conflicting = _versioned_plan(
        classification.action_plan,
        version=1,
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )

    with pytest.raises(EmailActionPlanConflict, match="version"):
        store.append_action_plan_version(
            classification.classification_id,
            conflicting,
            confirmed_category=EmailCategory.NOTIFICATION,
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
        category=EmailCategory.NOTIFICATION,
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
    confirmed = _confirm(
        store,
        original.classification_id,
        EmailCategory.NOTIFICATION,
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
        table: _fetchall(database, f"select count(*) as count from {table}")[0]["count"]
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
        table: _fetchall(database, f"select count(*) as count from {table}")[0]["count"]
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

    rows = _fetchall(
        database, "select stable_message_identity from email_classifications"
    )
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
        table: _fetchall(database, f"select count(*) as count from {table}")[0]["count"]
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
        table: _fetchall(database, f"select count(*) as count from {table}")[0]["count"]
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
        table: _fetchall(database, f"select count(*) as count from {table}")[0]["count"]
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
        table: _fetchall(database, f"select count(*) as count from {table}")[0]["count"]
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
    current = _fetchall(
        database, "select * from email_actions where action_id=?", (action_id,)
    )[0]
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


def test_claim_direct_action_uses_current_immutable_plan_and_stable_locator(
    tmp_path: Path,
):
    database = tmp_path / "claim-current-plan.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        stable_message_identity="dingtalk-account:imap:INBOX:42:7",
        uid=7,
    )
    _persist_scan(store, original)
    store.upsert_config(
        category=EmailCategory.NOTIFICATION,
        description="Move important mail",
        threshold=0.9,
        actions=(EmailAction.MOVE,),
        action_parameters={
            EmailAction.MOVE: {"target_folder": "Important"},
        },
        enabled=True,
        config_version="important-v2",
    )
    assert original.action_plan is not None
    application = store.apply_human_classification(
        original.classification_id,
        EmailCategory.NOTIFICATION,
        feedback_request_id="feedback-current-plan",
        expected_current_action_plan_id=original.action_plan.action_plan_id,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
    )
    assert application is not None

    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:01:00+00:00")

    assert claimed is not None
    assert claimed.action_plan_id == application.resulting_action_plan_id
    assert claimed.action_type is EmailAction.MOVE
    assert dict(claimed.parameters) == {"target_folder": "Important"}
    assert claimed.config_version == "important-v2"
    assert claimed.locator.stable_message_identity == (
        "dingtalk-account:imap:INBOX:42:7"
    )
    assert claimed.locator.folder == "INBOX"
    assert claimed.attempt_number == 1
    with pytest.raises(TypeError):
        claimed.parameters["target_folder"] = "Mutated"

    old_action = _fetchall(
        database,
        "select * from email_actions where action_plan_id=?",
        (original.action_plan.action_plan_id,),
    )[0]
    assert old_action["status"] == "pending"


def _correct_to_move_plan(
    store: EmailStore,
    original: EmailClassification,
    *,
    request_id: str,
):
    assert original.action_plan is not None
    store.upsert_config(
        category=EmailCategory.NOTIFICATION,
        description="Move important mail",
        threshold=0.9,
        actions=(EmailAction.MOVE,),
        action_parameters={
            EmailAction.MOVE: {"target_folder": "Important"},
        },
        enabled=True,
        config_version="important-v2",
    )
    application = store.apply_human_classification(
        original.classification_id,
        EmailCategory.NOTIFICATION,
        feedback_request_id=request_id,
        expected_current_action_plan_id=original.action_plan.action_plan_id,
        created_at=datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc),
    )
    assert application is not None
    return application


@pytest.mark.parametrize("resolution", ("complete", "recover"))
def test_processing_direct_action_blocks_plan_switch_until_closed(
    tmp_path: Path,
    resolution: str,
):
    database = tmp_path / f"historical-processing-{resolution}.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id=f"historical-processing-{resolution}",
    )
    _persist_scan(store, original)
    historical = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert historical is not None
    with pytest.raises(sqlite3.IntegrityError, match="email_direct_action_in_flight"):
        _correct_to_move_plan(
            store,
            original,
            request_id=f"feedback-historical-processing-{resolution}",
        )

    if resolution == "complete":
        store.complete_direct_action_attempt(
            historical,
            status="done",
            provider_operation="STORE LABELS",
            provider_target=historical.locator.stable_message_identity,
            provider_result_id="revision-v1",
            error="",
            finished_at="2026-08-30T12:03:00+00:00",
        )
    else:
        assert (
            store.recover_stale_processing_actions(
                stale_before="2026-08-30T12:00:01+00:00",
                recovered_at="2026-08-30T12:03:00+00:00",
            )
            == 1
        )

    application = _correct_to_move_plan(
        store,
        original,
        request_id=f"feedback-historical-processing-{resolution}",
    )
    current = store.claim_next_direct_action(claimed_at="2026-08-30T12:04:00+00:00")
    assert current is not None
    assert current.action_plan_id == application.resulting_action_plan_id
    assert current.action_type is EmailAction.MOVE


@pytest.mark.parametrize("historical_status", ("pending", "failed"))
def test_historical_non_processing_action_does_not_block_current_plan(
    tmp_path: Path,
    historical_status: str,
):
    database = tmp_path / f"historical-{historical_status}.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id=f"historical-{historical_status}",
    )
    _persist_scan(store, original)
    if historical_status == "failed":
        historical = store.claim_next_direct_action(
            claimed_at="2026-08-30T12:00:00+00:00"
        )
        assert historical is not None
        store.complete_direct_action_attempt(
            historical,
            status="failed",
            provider_operation="STORE LABELS",
            provider_target=historical.locator.stable_message_identity,
            provider_result_id="",
            error="provider_readback_mismatch",
            finished_at="2026-08-30T12:00:01+00:00",
        )
    application = _correct_to_move_plan(
        store,
        original,
        request_id=f"feedback-historical-{historical_status}",
    )

    current = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")

    assert current is not None
    assert current.action_plan_id == application.resulting_action_plan_id
    assert current.action_type is EmailAction.MOVE


def test_processing_direct_action_plan_fence_is_scoped_to_its_classification(
    tmp_path: Path,
):
    database = tmp_path / "processing-plan-fence-scope.sqlite3"
    store = EmailStore(database)
    blocked = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="blocked-historical-processing",
    )
    available = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="available-current-plan",
    )
    _persist_scan(store, blocked)
    _persist_scan(store, available)
    processing = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert processing is not None
    correction_target = (
        available
        if processing.classification_id == blocked.classification_id
        else blocked
    )
    application = _correct_to_move_plan(
        store,
        correction_target,
        request_id="feedback-available-while-other-processing",
    )

    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")

    assert claimed is not None
    assert claimed.classification_id == correction_target.classification_id
    assert claimed.action_plan_id == application.resulting_action_plan_id
    assert claimed.action_type is EmailAction.MOVE


def test_concurrent_direct_action_claim_has_one_winner(tmp_path: Path):
    database = tmp_path / "concurrent-action-claim.sqlite3"
    seed = EmailStore(database)
    _persist_scan(
        seed,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    stores = (EmailStore(database), EmailStore(database))
    barrier = Barrier(2)

    def claim(store: EmailStore):
        barrier.wait()
        return store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, stores))

    assert sum(result is not None for result in results) == 1
    current = _fetchall(database, "select * from email_actions")[0]
    assert current["status"] == "processing"
    assert current["attempt_count"] == 0


def test_processing_action_blocks_concurrent_sibling_claims(tmp_path: Path):
    database = tmp_path / "processing-blocks-siblings.sqlite3"
    seed = EmailStore(database)
    _persist_scan(
        seed,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(
                EmailAction.LABEL,
                EmailAction.MARK_READ,
                EmailAction.ARCHIVE,
            ),
            action_parameters={
                EmailAction.LABEL: {"labels": ["work"]},
            },
        ),
    )
    first = seed.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert first is not None
    assert first.action_type is EmailAction.LABEL
    stores = (EmailStore(database), EmailStore(database))
    barrier = Barrier(2)

    def claim(store: EmailStore):
        barrier.wait()
        return store.claim_next_direct_action(claimed_at="2026-08-30T12:01:00+00:00")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, stores))

    assert results == [None, None]
    statuses = _fetchall(
        database,
        "select action_type, status from email_actions order by action_type",
    )
    assert {row["action_type"]: row["status"] for row in statuses} == {
        "archive": "pending",
        "label": "processing",
        "mark_read": "pending",
    }


def test_processing_action_blocks_only_its_own_classification(tmp_path: Path):
    database = tmp_path / "processing-allows-other-classification.sqlite3"
    store = EmailStore(database)
    first_classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="first",
        actions=(EmailAction.LABEL, EmailAction.MARK_READ),
        action_parameters={
            EmailAction.LABEL: {"labels": ["work"]},
        },
    )
    second_classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="second",
        actions=(EmailAction.LABEL,),
        action_parameters={
            EmailAction.LABEL: {"labels": ["work"]},
        },
    )
    _persist_scan(store, first_classification)
    first = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert first is not None
    assert first.classification_id == first_classification.classification_id
    _persist_scan(store, second_classification)
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_actions set updated_at='2026-08-30T11:00:00+00:00'
            where classification_id=? and action_type='mark_read'
            """,
            (first_classification.classification_id,),
        )
        db.execute(
            """
            update email_actions set updated_at='2026-08-30T13:00:00+00:00'
            where classification_id=?
            """,
            (second_classification.classification_id,),
        )

    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:01:00+00:00")

    assert claimed is not None
    assert claimed.classification_id == second_classification.classification_id
    assert claimed.action_type is EmailAction.LABEL


@pytest.mark.parametrize(
    ("destination", "destination_parameters"),
    (
        (EmailAction.ARCHIVE, {}),
        (EmailAction.MOVE, {"target_folder": "Archive/Work"}),
        (EmailAction.TRASH, {}),
    ),
)
def test_failed_higher_priority_actions_retry_before_destination(
    tmp_path: Path,
    destination: EmailAction,
    destination_parameters: dict[str, object],
):
    database = tmp_path / "failed-action-dependency.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(
                EmailAction.LABEL,
                EmailAction.MARK_READ,
                destination,
            ),
            action_parameters={
                EmailAction.LABEL: {"labels": ["work"]},
                destination: destination_parameters,
            },
        ),
    )

    claimed_types: list[EmailAction] = []

    label_one = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert label_one is not None
    claimed_types.append(label_one.action_type)
    store.complete_direct_action_attempt(
        label_one,
        status="failed",
        provider_operation="STORE LABELS",
        provider_target=label_one.locator.stable_message_identity,
        provider_result_id="",
        error="provider_readback_mismatch",
        finished_at="2026-08-30T12:00:01+00:00",
    )

    label_two = store.claim_next_direct_action(claimed_at="2026-08-30T12:01:00+00:00")
    assert label_two is not None
    claimed_types.append(label_two.action_type)
    store.complete_direct_action_attempt(
        label_two,
        status="done",
        provider_operation="readback_noop",
        provider_target=label_two.locator.stable_message_identity,
        provider_result_id="revision-label",
        error="",
        finished_at="2026-08-30T12:01:01+00:00",
    )

    read_one = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")
    assert read_one is not None
    claimed_types.append(read_one.action_type)
    store.complete_direct_action_attempt(
        read_one,
        status="failed",
        provider_operation="STORE \\Seen",
        provider_target=read_one.locator.stable_message_identity,
        provider_result_id="",
        error="provider_readback_mismatch",
        finished_at="2026-08-30T12:02:01+00:00",
    )

    read_two = store.claim_next_direct_action(claimed_at="2026-08-30T12:03:00+00:00")
    assert read_two is not None
    claimed_types.append(read_two.action_type)
    store.complete_direct_action_attempt(
        read_two,
        status="done",
        provider_operation="readback_noop",
        provider_target=read_two.locator.stable_message_identity,
        provider_result_id="revision-read",
        error="",
        finished_at="2026-08-30T12:03:01+00:00",
    )

    destination_claim = store.claim_next_direct_action(
        claimed_at="2026-08-30T12:04:00+00:00"
    )
    assert destination_claim is not None
    claimed_types.append(destination_claim.action_type)

    assert claimed_types == [
        EmailAction.LABEL,
        EmailAction.LABEL,
        EmailAction.MARK_READ,
        EmailAction.MARK_READ,
        destination,
    ]


def test_destination_action_is_claimed_after_locator_preserving_actions(
    tmp_path: Path,
):
    database = tmp_path / "destination-action-last.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(
                EmailAction.ARCHIVE,
                EmailAction.MARK_READ,
                EmailAction.LABEL,
            ),
            action_parameters={
                EmailAction.LABEL: {"labels": ["work"]},
            },
            stable_message_identity="dingtalk-account:imap:INBOX:42:7",
            uid=7,
        ),
    )

    claimed_types: list[EmailAction] = []
    for attempt_index in range(3):
        claimed = store.claim_next_direct_action(
            claimed_at=f"2026-08-30T12:0{attempt_index}:00+00:00"
        )
        assert claimed is not None
        claimed_types.append(claimed.action_type)
        store.complete_direct_action_attempt(
            claimed,
            status="done",
            provider_operation="readback_noop",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id=f"revision-{attempt_index}",
            error="",
            finished_at=f"2026-08-30T12:0{attempt_index}:01+00:00",
        )

    assert claimed_types == [
        EmailAction.LABEL,
        EmailAction.MARK_READ,
        EmailAction.ARCHIVE,
    ]


def test_complete_direct_action_appends_attempt_and_updates_current_atomically(
    tmp_path: Path,
):
    database = tmp_path / "complete-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None

    attempt = store.complete_direct_action_attempt(
        claimed,
        status="done",
        provider_operation="STORE LABELS",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="revision-1",
        error="",
        finished_at="2026-08-30T12:00:01+00:00",
    )

    assert attempt["attempt_number"] == 1
    assert attempt["status"] == "done"
    current = _fetchall(
        database,
        "select * from email_actions where action_id=?",
        (claimed.action_id,),
    )[0]
    assert current["status"] == "done"
    assert current["attempt_count"] == 1
    assert current["provider_result_id"] == "revision-1"
    EmailStore(database)
    assert (
        store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00") is None
    )


def test_direct_action_completion_rejects_superseded_plan(tmp_path: Path):
    database = tmp_path / "complete-superseded-plan.sqlite3"
    store = EmailStore(database)
    original = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, original)
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    with sqlite3.connect(database) as db:
        db.execute("drop trigger trg_email_direct_action_blocks_plan_switch")
        db.execute(
            "update email_classifications set current_action_plan_id=null where id=?",
            (claimed.classification_id,),
        )

    with pytest.raises(EmailActionAttemptConflict, match="no longer current"):
        store.complete_direct_action_attempt(
            claimed,
            status="done",
            provider_operation="STORE LABELS",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id="revision-1",
            error="",
            finished_at="2026-08-30T12:00:01+00:00",
        )

    assert store.list_action_attempts(claimed.action_id) == []


def test_non_retryable_direct_action_is_not_reclaimed(tmp_path: Path):
    database = tmp_path / "non-retryable-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    store.complete_direct_action_attempt(
        claimed,
        status="failed",
        provider_operation="STORE LABELS",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="",
        error="provider_apply_failed:ImapPermanentFlagsUnsupported",
        finished_at="2026-08-30T12:00:01+00:00",
        retryable=False,
    )

    assert (
        store.claim_next_direct_action(claimed_at="2026-08-30T12:10:00+00:00") is None
    )


def test_processing_direct_action_blocks_account_rebinding_and_delete(tmp_path: Path):
    database = tmp_path / "processing-account-fence.sqlite3"
    store = EmailStore(database)
    store.create_account(
        {
            "account_id": "dingtalk-account",
            "display_name": "DingTalk",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-test",
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "",
            "smtp_secret_reference": "",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    original = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, original)
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    current = store.get_account(claimed.account_id)
    assert current is not None

    with pytest.raises(sqlite3.IntegrityError, match="email_direct_action_in_flight"):
        store.update_account(
            claimed.account_id,
            {
                **current,
                "imap_host": "different.example.com",
                "imap_username": "different@example.com",
            },
        )
    with pytest.raises(sqlite3.IntegrityError, match="email_direct_action_in_flight"):
        store.delete_account_if_unchanged(
            claimed.account_id,
            expected_updated_at=str(current["updated_at"]),
        )


def test_locator_refresh_does_not_invalidate_stable_claim_identity(tmp_path: Path):
    database = tmp_path / "locator-refresh-during-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_classifications
            set folder='Archive', uidvalidity=43, uid=8
            where id=?
            """,
            (claimed.classification_id,),
        )

    attempt = store.complete_direct_action_attempt(
        claimed,
        status="done",
        provider_operation="STORE LABELS",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="revision-1",
        error="",
        finished_at="2026-08-30T12:00:01+00:00",
    )

    assert attempt["status"] == "done"
    assert attempt["provider_target"] == claimed.locator.stable_message_identity


def test_direct_action_completion_rolls_back_attempt_and_current_state_together(
    tmp_path: Path,
):
    database = tmp_path / "complete-action-rollback.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    with sqlite3.connect(database) as db:
        db.execute(
            """
            create trigger reject_email_action_attempt
            before insert on email_action_attempts
            begin
                select raise(abort, 'injected attempt failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected attempt failure"):
        store.complete_direct_action_attempt(
            claimed,
            status="failed",
            provider_operation="STORE LABELS",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id="",
            error="provider_apply_failed:TimeoutError",
            finished_at="2026-08-30T12:00:01+00:00",
        )

    assert store.list_action_attempts(claimed.action_id) == []
    current = _fetchall(
        database,
        "select * from email_actions where action_id=?",
        (claimed.action_id,),
    )[0]
    assert current["status"] == "processing"
    assert current["attempt_count"] == 0
    assert current["started_at"] == claimed.claim_started_at


def test_stale_processing_recovery_records_failure_and_makes_action_claimable(
    tmp_path: Path,
):
    database = tmp_path / "recover-stale-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None

    assert (
        store.recover_stale_processing_actions(
            stale_before="2026-08-30T11:59:59+00:00",
            recovered_at="2026-08-30T12:01:00+00:00",
        )
        == 0
    )
    assert (
        store.recover_stale_processing_actions(
            stale_before="2026-08-30T12:00:01+00:00",
            recovered_at="2026-08-30T12:02:00+00:00",
        )
        == 1
    )

    attempts = store.list_action_attempts(claimed.action_id)
    assert [(row["attempt_number"], row["status"]) for row in attempts] == [
        (1, "failed")
    ]
    assert attempts[0]["provider_operation"] == "startup_recovery"
    assert attempts[0]["provider_target"] == (claimed.locator.stable_message_identity)
    assert attempts[0]["error"] == "stale_processing_recovered"
    EmailStore(database)
    retried = store.claim_next_direct_action(claimed_at="2026-08-30T12:03:00+00:00")
    assert retried is not None
    assert retried.action_id == claimed.action_id
    assert retried.attempt_number == 2


def test_stale_claim_cannot_complete_after_recovery_and_retry(tmp_path: Path):
    database = tmp_path / "stale-claim-cas.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    stale = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert stale is not None
    store.recover_stale_processing_actions(
        stale_before="2026-08-30T12:00:01+00:00",
        recovered_at="2026-08-30T12:01:00+00:00",
    )
    current = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")
    assert current is not None

    with pytest.raises(EmailActionAttemptConflict, match="claim changed"):
        store.complete_direct_action_attempt(
            stale,
            status="done",
            provider_operation="STORE LABELS",
            provider_target=stale.locator.stable_message_identity,
            provider_result_id="revision-stale",
            error="",
            finished_at="2026-08-30T12:03:00+00:00",
        )

    assert len(store.list_action_attempts(stale.action_id)) == 1


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

    with pytest.raises(
        EmailPersistenceCorruption, match="terminal action.*no.*attempt"
    ):
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
        db.execute("update email_classifications set probabilities_json='not-json'")

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
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )
    store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
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


def test_unsubscribe_schema_migrates_and_durable_journal_receipt_survive_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-durable.sqlite3"
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)

    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    persisted = store.persist_email_unsubscribe_terminal(
        **{
            key: authorization[key]
            for key in (
                "action_identity",
                "effect_digest",
                "action_plan_id",
                "action_plan_version",
                "classification_id",
                "account_id",
                "stable_message_identity",
                "thread_identity",
                "entry_reference",
                "operations",
            )
        },
        outcome="done",
        receipt_id="unsubscribe-receipt:done-41",
        evidence="terminal-page",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "done",
            "reference": "unsubscribe-receipt:done-41",
        },
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )

    reopened = EmailStore(database)
    assert (
        reopened.get_email_unsubscribe_receipt(authorization["action_identity"])
        == persisted
    )
    assert reopened.list_email_unsubscribe_steps(authorization["action_identity"]) == [
        {
            "sequence": 1,
            "operation": "open_entry",
            "state": "done",
            "reference": "unsubscribe-receipt:done-41",
            "created_at": reopened.list_email_unsubscribe_steps(
                authorization["action_identity"]
            )[0]["created_at"],
        },
    ]
    with sqlite3.connect(database) as db:
        assert (
            db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            == email_store_module.EMAIL_SCHEMA_VERSION
        )


@pytest.mark.parametrize(
    "unsafe_result_text",
    (
        "Open https://news.example.com/unsubscribe?token=private-query",
        "Credential password=private-password-value",
        "Browser profile /Users/derek/private-email-profile",
    ),
)
def test_startup_rejects_noncanonical_or_unsafe_unsubscribe_result_text(
    tmp_path: Path,
    unsafe_result_text: str,
) -> None:
    database = tmp_path / "unsafe-unsubscribe-result.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_unsubscribe_receipts "
            "set result_text=?, observation_digest=? where action_identity=?",
            (
                unsafe_result_text,
                sha256(unsafe_result_text.encode("utf-8")).hexdigest(),
                authorization["action_identity"],
            ),
        )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="unsubscribe receipt",
    ):
        EmailStore(database)


def test_startup_rejects_wrong_untruncated_unsubscribe_observation_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "wrong-unsubscribe-result-digest.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_unsubscribe_receipts set observation_digest=? "
            "where action_identity=?",
            ("f" * 64, authorization["action_identity"]),
        )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="unsubscribe receipt",
    ):
        EmailStore(database)


@pytest.mark.parametrize(
    ("result_text", "observation_digest"),
    (
        ("", "a" * 64),
        ("Unsubscribed", ""),
    ),
)
def test_startup_rejects_empty_nonempty_unsubscribe_digest_mismatch(
    tmp_path: Path,
    result_text: str,
    observation_digest: str,
) -> None:
    database = tmp_path / "unsubscribe-empty-digest-mismatch.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_unsubscribe_receipts "
            "set result_text=?, observation_digest=? where action_identity=?",
            (
                result_text,
                observation_digest,
                authorization["action_identity"],
            ),
        )

    with pytest.raises(
        EmailPersistenceCorruption,
        match="unsubscribe receipt",
    ):
        EmailStore(database)


def test_unsubscribe_result_read_fails_closed_after_durable_row_corruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-result-read-corruption.sqlite3"
    store, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    unsafe_result_text = "https://news.example.com/unsubscribe?token=private-query"
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_unsubscribe_receipts "
            "set result_text=?, observation_digest=? where action_identity=?",
            (
                unsafe_result_text,
                sha256(unsafe_result_text.encode("utf-8")).hexdigest(),
                authorization["action_identity"],
            ),
        )

    with pytest.raises(EmailPersistenceCorruption):
        store.get_email_unsubscribe_receipt(str(authorization["action_identity"]))
    with pytest.raises(EmailPersistenceCorruption):
        store.list_email_classification_observability(
            int(authorization["classification_id"])
        )


def test_valid_16kib_truncated_unsubscribe_result_preserves_full_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-result-truncated.sqlite3"
    full_observation = "A" * (16 * 1024 + 777)
    _, authorization, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text=full_observation,
    )
    expected_text, expected_digest = normalize_unsubscribe_result_text(full_observation)

    assert len(expected_text.encode("utf-8")) == 16 * 1024
    assert receipt["result_text"] == expected_text
    assert receipt["observation_digest"] == expected_digest
    assert receipt["result_text_truncated"] is True
    assert (
        receipt["result_text_digest"]
        == sha256(expected_text.encode("utf-8")).hexdigest()
    )
    assert receipt["result_text_digest"] != expected_digest
    assert expected_digest == sha256(full_observation.encode("utf-8")).hexdigest()
    reopened = EmailStore(database)
    assert (
        reopened.get_email_unsubscribe_receipt(str(authorization["action_identity"]))[
            "result_text_digest"
        ]
        == receipt["result_text_digest"]
    )


def test_exact_16kib_untruncated_result_requires_matching_observation_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-exact-16kib-untruncated.sqlite3"
    _, authorization, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text="A" * (16 * 1024),
    )
    assert receipt["result_text_truncated"] is False
    assert receipt["observation_digest"] == receipt["result_text_digest"]
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_unsubscribe_receipts set observation_digest=? "
            "where action_identity=?",
            ("f" * 64, authorization["action_identity"]),
        )

    with pytest.raises(EmailPersistenceCorruption, match="observation digest"):
        EmailStore(database)


def test_stored_bounded_result_digest_is_always_verified(tmp_path: Path) -> None:
    database = tmp_path / "unsubscribe-bounded-digest.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_unsubscribe_receipts set result_text_digest=? "
            "where action_identity=?",
            ("f" * 64, authorization["action_identity"]),
        )

    with pytest.raises(EmailPersistenceCorruption, match="bounded result digest"):
        EmailStore(database)


def test_empty_result_persists_empty_integrity_metadata(tmp_path: Path) -> None:
    database = tmp_path / "unsubscribe-empty-integrity.sqlite3"
    _, _, receipt = _persist_unsubscribe_result_fixture(database, result_text="")

    assert receipt["result_text"] == ""
    assert receipt["observation_digest"] == ""
    assert receipt["result_text_digest"] == ""
    assert receipt["result_text_truncated"] is False


def test_explicit_bounded_unsubscribe_result_preserves_full_observation_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-explicit-bounded-result.sqlite3"
    full_observation = "A" * (16 * 1024 + 777)
    bounded_text, full_digest = normalize_unsubscribe_result_text(full_observation)
    bounded_digest = sha256(bounded_text.encode("utf-8")).hexdigest()

    _, _, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text=bounded_text,
        observation_digest=full_digest,
        result_text_digest=bounded_digest,
        result_text_truncated=True,
    )

    assert receipt["result_text"] == bounded_text
    assert receipt["result_text_digest"] == bounded_digest
    assert receipt["observation_digest"] == full_digest
    assert receipt["result_text_truncated"] is True


def test_explicit_exact_16kib_result_rejects_mismatched_observation_digest(
    tmp_path: Path,
) -> None:
    result_text = "A" * (16 * 1024)
    bounded_digest = sha256(result_text.encode("utf-8")).hexdigest()

    with pytest.raises(EmailUnsubscribeReceiptConflict, match="integrity"):
        _persist_unsubscribe_result_fixture(
            tmp_path / "unsubscribe-explicit-exact-bound.sqlite3",
            result_text=result_text,
            observation_digest="f" * 64,
            result_text_digest=bounded_digest,
            result_text_truncated=False,
        )


@pytest.mark.parametrize(
    ("result_text", "observation_digest", "result_text_digest", "truncated"),
    [
        ("A" * (16 * 1024), "e" * 64, "f" * 64, True),
        ("A" * (16 * 1024), "e" * 64, sha256(b"A" * (16 * 1024)).hexdigest(), False),
        (
            "Unsubscribed",
            sha256(b"Unsubscribed").hexdigest(),
            sha256(b"Unsubscribed").hexdigest(),
            True,
        ),
    ],
)
def test_explicit_unsubscribe_result_rejects_inconsistent_caller_metadata(
    tmp_path: Path,
    result_text: str,
    observation_digest: str,
    result_text_digest: str,
    truncated: bool,
) -> None:
    with pytest.raises(EmailUnsubscribeReceiptConflict, match="integrity"):
        _persist_unsubscribe_result_fixture(
            tmp_path / f"unsubscribe-explicit-mismatch-{truncated}.sqlite3",
            result_text=result_text,
            observation_digest=observation_digest,
            result_text_digest=result_text_digest,
            result_text_truncated=truncated,
        )


@pytest.mark.parametrize("result_text", ("", "Unsubscribed"))
def test_explicit_empty_and_short_unsubscribe_results_remain_unchanged(
    tmp_path: Path,
    result_text: str,
) -> None:
    digest = sha256(result_text.encode("utf-8")).hexdigest() if result_text else ""

    _, _, receipt = _persist_unsubscribe_result_fixture(
        tmp_path / f"unsubscribe-explicit-short-{len(result_text)}.sqlite3",
        result_text=result_text,
        observation_digest=digest,
        result_text_digest=digest,
        result_text_truncated=False,
    )

    assert receipt["result_text"] == result_text
    assert receipt["observation_digest"] == digest
    assert receipt["result_text_digest"] == digest
    assert receipt["result_text_truncated"] is False


def test_v16_migration_preserves_legacy_full_observation_digest_for_truncation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v16-legacy-truncated.sqlite3"
    full_observation = "A" * (16 * 1024 + 777)
    _, authorization, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text=full_observation,
    )
    expected_observation_digest = receipt["observation_digest"]
    _downgrade_email_database_to_v16(database)

    migrated = EmailStore(database).get_email_unsubscribe_receipt(
        str(authorization["action_identity"])
    )

    assert migrated is not None
    assert migrated["result_text_truncated"] is True
    assert migrated["observation_digest"] == expected_observation_digest
    assert (
        migrated["result_text_digest"]
        == sha256(migrated["result_text"].encode("utf-8")).hexdigest()
    )
    assert migrated["result_text_digest"] != migrated["observation_digest"]


def test_v16_migration_rejects_corrupt_untruncated_observation_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v16-corrupt-untruncated.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    _downgrade_email_database_to_v16(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_unsubscribe_receipts set observation_digest=? "
            "where action_identity=?",
            ("f" * 64, authorization["action_identity"]),
        )

    with pytest.raises(EmailPersistenceCorruption, match="observation digest"):
        EmailStore(database)
    assert [
        row["version"]
        for row in _fetchall(database, "select version from email_schema_migrations")
    ] == [16]


def _persist_unsubscribe_continuation_fixture(
    store: EmailStore,
) -> dict[str, object]:
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    store.persist_email_unsubscribe_continuation(
        **authorization,
        controls=(
            {
                "reference": "unsubscribe-control:" + "c" * 64,
                "kind": "button",
                "intent": "confirm",
            },
        ),
        observation_reference="unsubscribe-state:" + "d" * 64,
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "action_required",
            "reference": "unsubscribe-state:" + "d" * 64,
        },
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    return authorization


def test_unsubscribe_state_snapshot_decodes_one_read_consistent_view(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-state-snapshot.sqlite3")
    authorization = _persist_unsubscribe_continuation_fixture(store)

    snapshot = store.get_email_unsubscribe_state_snapshot(
        str(authorization["action_identity"])
    )

    assert snapshot["claim"]["effect_digest"] == authorization["effect_digest"]
    assert snapshot["continuation"]["effect_digest"] == authorization["effect_digest"]
    assert [effect["effect_digest"] for effect in snapshot["effects"]] == [
        authorization["effect_digest"]
    ]


def test_unsubscribe_terminal_snapshot_rejects_step_effect_digest_tamper(
    tmp_path: Path,
) -> None:
    store, authorization, _ = _persist_unsubscribe_result_fixture(
        tmp_path / "unsubscribe-terminal-step-effect.sqlite3",
        result_text="Unsubscribed",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update email_unsubscribe_steps set effect_digest=? "
            "where action_identity=?",
            ("f" * 64, authorization["action_identity"]),
        )

    with pytest.raises(EmailPersistenceCorruption, match="effect-bound"):
        store.get_email_unsubscribe_terminal_snapshot(
            str(authorization["action_identity"]),
            str(authorization["effect_digest"]),
        )


def test_unsubscribe_terminal_snapshot_uses_one_sqlite_read_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, authorization, receipt = _persist_unsubscribe_result_fixture(
        tmp_path / "unsubscribe-terminal-atomic-read.sqlite3",
        result_text="Unsubscribed",
    )
    original_decode = store._email_unsubscribe_claim_row
    raced = False

    def mutate_after_first_read(row):
        nonlocal raced
        if not raced:
            raced = True
            with sqlite3.connect(store.path) as writer:
                writer.execute(
                    "update email_unsubscribe_steps set effect_digest=? "
                    "where action_identity=?",
                    ("f" * 64, authorization["action_identity"]),
                )
        return original_decode(row)

    monkeypatch.setattr(store, "_email_unsubscribe_claim_row", mutate_after_first_read)

    snapshot = store.get_email_unsubscribe_terminal_snapshot(
        str(authorization["action_identity"]),
        str(authorization["effect_digest"]),
    )

    assert snapshot is not None
    assert snapshot["receipt"]["receipt_id"] == receipt["receipt_id"]
    assert "result_text_digest" not in snapshot["receipt"]
    assert "result_text_truncated" not in snapshot["receipt"]
    assert raced is True
    monkeypatch.setattr(store, "_email_unsubscribe_claim_row", original_decode)
    with pytest.raises(EmailPersistenceCorruption, match="effect-bound"):
        store.get_email_unsubscribe_terminal_snapshot(
            str(authorization["action_identity"]),
            str(authorization["effect_digest"]),
        )


@pytest.mark.parametrize(
    ("table", "column"),
    (
        ("email_unsubscribe_claims", "operations_json"),
        ("email_unsubscribe_effects", "operations_json"),
        ("email_unsubscribe_continuations", "controls_json"),
    ),
)
def test_unsubscribe_state_snapshot_reports_malformed_durable_json_as_corruption(
    tmp_path: Path,
    table: str,
    column: str,
) -> None:
    store = EmailStore(tmp_path / f"unsubscribe-corrupt-{table}-{column}.sqlite3")
    authorization = _persist_unsubscribe_continuation_fixture(store)
    with sqlite3.connect(store.path) as db:
        db.execute(
            f"update {table} set {column}='{{}}' where action_identity=?",
            (authorization["action_identity"],),
        )

    with pytest.raises(EmailPersistenceCorruption):
        store.get_email_unsubscribe_state_snapshot(
            str(authorization["action_identity"])
        )


def test_unsubscribe_state_snapshot_surfaces_real_sqlite_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-state-busy.sqlite3")
    gc.collect()
    setup = sqlite3.connect(store.path)
    try:
        setup.execute("pragma journal_mode=delete")
    finally:
        setup.close()
    authorization = _persist_unsubscribe_continuation_fixture(store)
    gc.collect()

    original_connect = store._connect

    def no_wait_connect():
        db = original_connect()
        db.execute("pragma busy_timeout=0")
        return db

    monkeypatch.setattr(store, "_connect", no_wait_connect)
    blocker = sqlite3.connect(store.path, timeout=0)
    try:
        blocker.execute("begin exclusive")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.get_email_unsubscribe_state_snapshot(
                str(authorization["action_identity"])
            )
    finally:
        blocker.rollback()
        blocker.close()


def test_unsubscribe_state_snapshot_rejects_row_33_before_effect_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-state-over-limit.sqlite3")
    authorization = _persist_unsubscribe_continuation_fixture(store)
    with sqlite3.connect(store.path) as db:
        db.executemany(
            """
            insert into email_unsubscribe_effects (
                action_identity, effect_digest, previous_effect_digest,
                operations_json, network_policy_reference,
                network_policy_origins_json, audit_agent_run_id, created_at
            ) values (?, ?, '', '{}', 'network-policy:legacy',
                      '["network-origin:legacy"]', null, ?)
            """,
            (
                (
                    authorization["action_identity"],
                    sha256(f"overflow-effect-{index}".encode()).hexdigest(),
                    f"2026-09-03T00:00:{index:02d}+00:00",
                )
                for index in range(32)
            ),
        )

    decoded = 0

    def reject_decode(_row):
        nonlocal decoded
        decoded += 1
        raise AssertionError("effect payload decoded before cardinality gate")

    monkeypatch.setattr(store, "_email_unsubscribe_effect_row", reject_decode)
    monkeypatch.setattr(store, "_email_unsubscribe_continuation_row", reject_decode)

    with pytest.raises(
        EmailPersistenceCorruption,
        match="durable continuation operation limit",
    ):
        store.get_email_unsubscribe_state_snapshot(
            str(authorization["action_identity"])
        )
    assert decoded == 0


@pytest.mark.parametrize("mutation", ("plan", "status", "account", "message"))
def test_unsubscribe_claim_fences_current_authorization_during_browser_write(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = EmailStore(tmp_path / f"unsubscribe-fence-{mutation}.sqlite3")
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True

    with pytest.raises(
        sqlite3.IntegrityError, match="email_unsubscribe_write_in_flight"
    ):
        if mutation == "plan":
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "update email_classifications set current_action_plan_id=null "
                    "where id=?",
                    (authorization["classification_id"],),
                )
        elif mutation == "status":
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "update email_classifications set status='pending_feedback' "
                    "where id=?",
                    (authorization["classification_id"],),
                )
        elif mutation == "account":
            current = store.get_account(str(authorization["account_id"]))
            assert current is not None
            store.update_account(
                str(authorization["account_id"]),
                {**current, "enabled": False},
            )
        else:
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "update email_messages set thread_identity='changed-thread' "
                    "where stable_message_identity=?",
                    (authorization["stable_message_identity"],),
                )


def test_unsubscribe_claim_recovery_requires_proven_owner_termination(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-owner-recovery.sqlite3")
    authorization = _unsubscribe_authorization(store)
    store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )

    with pytest.raises(EmailUnsubscribeClaimConflict, match="termination"):
        store.recover_terminated_email_unsubscribe_claims(
            owner=_UNSUBSCRIBE_OWNER_A,
            termination_verifier=lambda _owner: False,
            recovered_at="2026-08-30T10:00:00+00:00",
        )
    assert (
        store.get_email_unsubscribe_claim(authorization["action_identity"])["status"]
        == "dispatching"
    )
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=_UNSUBSCRIBE_OWNER_A,
            termination_verifier=lambda owner: owner == _UNSUBSCRIBE_OWNER_A,
            recovered_at="2026-08-30T10:01:00+00:00",
        )
        == 1
    )
    assert (
        store.get_email_unsubscribe_claim(authorization["action_identity"])["status"]
        == "uncertain"
    )


def test_uncertain_unsubscribe_claim_cannot_be_reacquired_for_blind_write(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-uncertain-replay.sqlite3")
    authorization = _unsubscribe_authorization(store)
    first = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert first is not None and first["acquired"] is True
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=_UNSUBSCRIBE_OWNER_A,
            termination_verifier=lambda owner: owner == _UNSUBSCRIBE_OWNER_A,
            recovered_at="2026-08-30T10:02:00+00:00",
        )
        == 1
    )

    replay = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_B,
    )

    assert replay is not None
    assert replay["acquired"] is False
    assert replay["status"] == "uncertain"
    persisted = store.get_email_unsubscribe_claim(authorization["action_identity"])
    assert persisted is not None
    assert persisted["status"] == "uncertain"
    assert persisted["owner_id"] == _UNSUBSCRIBE_OWNER_A["owner_id"]
    assert persisted["owner_generation"] == _UNSUBSCRIBE_OWNER_A["generation"]
    assert persisted["lease_token"] == _UNSUBSCRIBE_OWNER_A["lease_token"]


def test_dispatching_unsubscribe_claim_is_not_reacquired_by_same_owner(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-same-owner.sqlite3")
    authorization = _unsubscribe_authorization(store)

    first = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    second = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )

    assert first is not None and first["acquired"] is True
    assert second is not None and second["acquired"] is False
    assert second["status"] == "dispatching"


def test_no_claim_terminal_rejects_forged_unsubscribe_action_identity(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-forged-terminal.sqlite3")
    authorization = _unsubscribe_authorization(store)
    forged = {**authorization, "action_identity": "email-action:forged"}
    forged["effect_digest"] = email_unsubscribe_effect_digest(
        **{key: value for key, value in forged.items() if key != "effect_digest"}
    )

    with pytest.raises(EmailUnsubscribeReceiptConflict, match="identity"):
        store.persist_email_unsubscribe_terminal(
            **forged,
            outcome="skipped_no_reliable_entry",
            receipt_id="unsubscribe-receipt:forged",
            evidence="entry-selection",
            final_step=None,
            claim_owner=None,
        )


def test_startup_rejects_forged_durable_unsubscribe_action_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-forged-startup.sqlite3"
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    forged_identity = "email-action:forged"
    forged_digest = email_unsubscribe_effect_digest(
        **{
            **{
                key: value
                for key, value in authorization.items()
                if key != "effect_digest"
            },
            "action_identity": forged_identity,
        }
    )
    with sqlite3.connect(database) as db:
        db.execute("pragma foreign_keys=off")
        db.execute(
            "update email_unsubscribe_claims set action_identity=?, effect_digest=?",
            (forged_identity, forged_digest),
        )

    with pytest.raises(EmailPersistenceCorruption, match="ActionPlan"):
        EmailStore(database)


def test_unsubscribe_receipt_is_exactly_bound_and_terminal_write_is_atomic(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-receipt-binding.sqlite3")
    authorization = _unsubscribe_authorization(store)
    store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    receipt_arguments = {
        key: authorization[key]
        for key in (
            "action_identity",
            "effect_digest",
            "action_plan_id",
            "action_plan_version",
            "classification_id",
            "account_id",
            "stable_message_identity",
            "thread_identity",
            "entry_reference",
            "operations",
        )
    }
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            create trigger test_abort_unsubscribe_claim_done
            before update of status on email_unsubscribe_claims
            when new.status='done'
            begin
                select raise(abort, 'simulated_unsubscribe_terminal_crash');
            end
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="simulated_unsubscribe_terminal_crash",
    ):
        store.persist_email_unsubscribe_terminal(
            **receipt_arguments,
            outcome="done",
            receipt_id="unsubscribe-receipt:done-41",
            evidence="terminal-page",
            final_step={
                "sequence": 1,
                "operation": "open_entry",
                "state": "done",
                "reference": "unsubscribe-receipt:done-41",
            },
            claim_owner=_UNSUBSCRIBE_OWNER_A,
        )
    assert store.get_email_unsubscribe_receipt(authorization["action_identity"]) is None
    assert store.list_email_unsubscribe_steps(authorization["action_identity"]) == []

    with sqlite3.connect(store.path) as db:
        db.execute("drop trigger test_abort_unsubscribe_claim_done")
    store.persist_email_unsubscribe_terminal(
        **receipt_arguments,
        outcome="done",
        receipt_id="unsubscribe-receipt:done-41",
        evidence="terminal-page",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "done",
            "reference": "unsubscribe-receipt:done-41",
        },
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )
    with pytest.raises(EmailUnsubscribeReceiptConflict):
        store.persist_email_unsubscribe_terminal(
            **{**receipt_arguments, "effect_digest": "c" * 64},
            outcome="done",
            receipt_id="unsubscribe-receipt:done-41",
            evidence="terminal-page",
            final_step=None,
            claim_owner=None,
        )


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
