from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import inspect
from threading import Event
from urllib.parse import quote

import pytest

from app.agent_context import PriorReceipt
from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    build_versioned_email_action_plan,
)
from app.email_store import EmailPersistenceCorruption, EmailStore
from app.email_imap_readonly import parse_rfc822_message
from app.email_task_adapter import (
    EmailAgentTaskAdapter,
    EmailAgentTaskConflict,
    EmailAgentTaskInput,
    EmailAgentTaskMetadataError,
    EmailThreadMessage,
    _assert_safe_email_metadata,
    accepted_email_unsubscribe_effect,
    email_action_identity,
    email_conversation_id,
    EmailClassificationTaskAdapter,
    EmailClassificationTaskInput,
)
from app.email_task_producer import EmailActionTaskProducer
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    EmailUnsubscribeEffect,
    UnsubscribeAuthenticationEvidence,
    UnsubscribeOperation,
)
from app.store import AutoReplyStore
from app.skill_features import FeatureRegistry


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "email-agent.sqlite3")


def _plan(
    actions: tuple[EmailAction, ...],
    *,
    version: int = 1,
    classification_id: int = 41,
    account_id: str = "account-primary",
    instruction: str = "Reply in Chinese and acknowledge receipt.",
    classification_source: str = "model",
    category: EmailCategory | None = None,
):
    category = category or (
        EmailCategory.JUNK if EmailAction.UNSUBSCRIBE in actions else EmailCategory.WORK
    )
    parameters = {}
    for action in actions:
        if action is EmailAction.AUTO_REPLY:
            parameters[action] = {"instruction": instruction}
        elif action is EmailAction.LABEL:
            parameters[action] = {"labels": ["Work"]}
        else:
            parameters[action] = {}
    return build_versioned_email_action_plan(
        action_plan_version=version,
        classification_id=classification_id,
        account_id=account_id,
        category=category,
        classification_source=classification_source,
        confidence=0.98,
        model_id="email-model:2026-08-30:sha256:test",
        config_version="email-config:v7",
        actions=actions,
        action_parameters=parameters,
        created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
    )


def _task_input(
    *,
    account_id: str = "account-primary",
) -> EmailAgentTaskInput:
    stable_identity = f"{account_id}:message-id:<mail-41@example.com>"
    body_text = (
        "请确认合同。退订入口 "
        "https://example.com/unsubscribe?token=private-token "
        "以及 password=do-not-persist /Users/derek/private/attachment.bin"
    )
    policy = BrowserNetworkPolicy(frozenset({"https://example.com"}))
    return EmailAgentTaskInput(
        stable_message_identity=stable_identity,
        thread_identity="thread-customer-41",
        subject="Re: 合同确认",
        trigger=EmailThreadMessage(
            message_id=stable_identity,
            sender="customer@example.com",
            text=body_text,
            create_time="2026-08-30T08:00:00+00:00",
        ),
        thread_messages=(
            EmailThreadMessage(
                message_id=f"{account_id}:message-id:<mail-40@example.com>",
                sender="derek@stardust.ai",
                text="上一封邮件的纯文本回复。",
                create_time="2026-08-29T08:00:00+00:00",
            ),
        ),
        attachments=(
            EmailAttachmentMetadata(
                filename="contract.pdf",
                mime_type="application/pdf",
                size_bytes=1234,
                inline=False,
            ),
        ),
        prior_receipts=(
            PriorReceipt(
                receipt_id="sent-state-1",
                operation="sent_state_readback",
                summary="No equivalent reply exists.",
                completed=True,
            ),
        ),
        body_text=body_text,
        unsubscribe_network_policy_reference=policy.reference,
        unsubscribe_network_policy_origin_references=policy.origin_references,
    )


def _email_store(tmp_path: Path) -> EmailStore:
    return EmailStore(tmp_path / "email-agent.sqlite3")


def _downgrade_email_database_to_v25(database: Path) -> None:
    with sqlite3.connect(database) as db:
        db.execute("pragma foreign_keys=off")
        for table in (
            "email_historical_classification_history",
            "email_historical_candidates",
            "email_historical_operations",
            "email_historical_traversals",
        ):
            db.execute(f"drop table {table}")
        db.execute("delete from email_schema_migrations where version > 25")
        db.execute(
            "insert or ignore into email_schema_migrations(version, applied_at) "
            "values (25, '2026-09-08T00:00:00+00:00')"
        )


def _classification_input(*, uid: int) -> EmailClassificationTaskInput:
    return EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": uid,
            "messageId": f"<mail-{uid}@example.com>",
            "providerUnread": True,
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )


def _percent_encode(value: str, rounds: int) -> str:
    encoded = value
    for _ in range(rounds):
        encoded = quote(encoded, safe="")
    return encoded


def test_classification_task_adapter_persists_one_stable_task_across_reopen(
    tmp_path: Path,
):
    store = _email_store(tmp_path)
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 41,
            "messageId": "<mail-41@example.com>",
            "threadId": "thread-41",
            "providerUnread": True,
            "from": {"email": "customer@example.com"},
            "subject": "Project decision",
            "textBody": "Please decide the launch scope.",
            "attachments": [
                {
                    "filename": "scope.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 12,
                    "inline": False,
                }
            ],
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )

    first = EmailClassificationTaskAdapter(store).ensure_task(task_input)
    reopened = EmailClassificationTaskAdapter(_email_store(tmp_path)).ensure_task(
        task_input
    )

    assert first.task_id == reopened.task_id
    assert first.status == reopened.status == "pending"
    assert EmailClassificationTaskAdapter(store).has_stable_record(
        task_input.stable_message_identity
    )
    assert EmailClassificationTaskAdapter(store).stable_provider_uids(
        account_id="account-primary", folder="INBOX", uidvalidity=42
    ) == frozenset({41})
    assert "scope.pdf" in first.input_json
    assert "attachment content" not in first.input_json


def test_classifier_queue_schema_is_owned_by_email_store_not_adapter(tmp_path: Path):
    store = _email_store(tmp_path)
    with store._connect() as db:
        columns = {
            row["name"]
            for row in db.execute("pragma table_info(email_agent_classification_tasks)")
        }
    assert {
        "generation",
        "attempt_count",
        "lease_expires_at",
        "available_at",
    } <= columns
    assert (
        "create table"
        not in inspect.getsource(EmailClassificationTaskAdapter).casefold()
    )


def test_email_store_rejects_malformed_classifier_queue_schema(tmp_path: Path):
    database = tmp_path / "malformed-classifier.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute("drop index idx_email_agent_classification_tasks_status")

    with pytest.raises(
        EmailPersistenceCorruption, match="classifier.*index|idx_email_agent"
    ):
        EmailStore(database)


def test_v25_classifier_queue_migration_preserves_task_and_scrubs_private_url(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v25-classifier.sqlite3"
    store = EmailStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=144))
    private_url = "https://news.example.test/unsubscribe?token=legacy-secret"
    payload = json.loads(task.input_json)
    payload["unsubscribe_candidates"] = [private_url]
    payload["message"]["text"] = "Click " + private_url
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_agent_classification_tasks set input_json=? where task_id=?",
            (json.dumps(payload), task.task_id),
        )
    _downgrade_email_database_to_v25(database)

    migrated = EmailClassificationTaskAdapter(EmailStore(database)).get_task(
        task.task_id
    )

    assert migrated is not None and migrated.status == "pending"
    assert private_url not in migrated.input_json
    assert "unsubscribe-entry:" in migrated.input_json
    assert private_url.encode() not in database.read_bytes()


def test_v25_completed_null_unsubscribe_result_migrates_nullable_projection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v25-null-result.sqlite3"
    store = EmailStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=145))
    legacy_result = {
        "decision_status": "processed",
        "category": "work",
        "important": False,
        "certainty": "certain",
        "confidence": 0.91,
        "reason": "Business correspondence.",
        "unsubscribe_candidate_index": None,
        "unsubscribe_url": None,
        "classification_id": 145,
    }
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_agent_classification_tasks
            set status='done', result_json=?
            where task_id=?
            """,
            (json.dumps(legacy_result), task.task_id),
        )
    _downgrade_email_database_to_v25(database)

    migrated = EmailClassificationTaskAdapter(EmailStore(database)).get_task(
        task.task_id
    )

    assert migrated is not None and migrated.status == "done"
    result = json.loads(migrated.result_json)
    assert "unsubscribe_url" not in result
    assert result["unsubscribe_candidate_index"] is None
    assert result["unsubscribe_candidate_source"] is None
    assert result["unsubscribe_candidate_digest"] is None
    assert result["unsubscribe_candidate_reference"] is None


def test_v25_completed_selected_result_preserves_schema_keys_and_replays(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import (
        AgentClassificationResult,
        DurableAgentClassificationResult,
    )

    database = tmp_path / "v25-selected-result.sqlite3"
    store = EmailStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=147))
    private_token = "legacy-secret"
    private_url = f"https://news.example.test/unsubscribe?token={private_token}"
    legacy_agent_result = {
        "category": "junk",
        "important": False,
        "certainty": "certain",
        "confidence": 0.97,
        "reason": f"Unwanted subscription: {private_url}",
        "unsubscribe_candidate_index": 0,
        "unsubscribe_url": private_url,
    }
    assert (
        AgentClassificationResult.model_validate(legacy_agent_result).unsubscribe_url
        == private_url
    )
    queue_result = legacy_agent_result | {
        "decision_status": "processed",
        "classification_id": 147,
    }
    payload = json.loads(task.input_json)
    payload["unsubscribe_candidates"] = [private_url]
    payload["message"]["text"] = f"Select {private_url}"

    classification_id = 9147
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=classification_id,
        account_id="account-primary",
        category=EmailCategory.JUNK,
        classification_source="agent",
        confidence=0.97,
        model_id="email-classifier-agent:v1",
        config_version="config-v1",
        actions=(),
        action_parameters={},
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    store.persist_scan_result(
        EmailClassification(
            classification_id=classification_id,
            stable_message_identity=(
                "account-primary:message-id:<legacy-canonical-147@example.com>"
            ),
            provider_locator=EmailProviderLocator(
                account_id="account-primary",
                folder="INBOX",
                uidvalidity=42,
                uid=9147,
                rfc_message_id="<legacy-canonical-147@example.com>",
            ),
            category=EmailCategory.JUNK,
            confidence=0.97,
            margin=0.97,
            probabilities={EmailCategory.JUNK: 1.0},
            model_id="email-classifier-agent:v1",
            config_version="config-v1",
            status=EmailClassificationStatus.PROCESSED,
            classification_source="agent",
            action_plan=plan,
        ),
        agent_result=DurableAgentClassificationResult(
            category="junk",
            important=False,
            certainty="certain",
            confidence=0.97,
            reason="Unwanted subscription.",
            unsubscribe_candidate_index=None,
            unsubscribe_candidate_source=None,
            unsubscribe_candidate_digest=None,
            unsubscribe_candidate_reference=None,
        ),
        sender="sender@example.com",
        subject="Legacy selected Agent result",
        model_text="__subject__legacy selected agent result",
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_agent_classification_tasks
            set status='done', input_json=?, result_json=? where task_id=?
            """,
            (json.dumps(payload), json.dumps(queue_result), task.task_id),
        )
        db.execute(
            "update email_classifications set agent_result_json=? where id=?",
            (json.dumps(legacy_agent_result), classification_id),
        )
    _downgrade_email_database_to_v25(database)

    EmailStore(database)
    with sqlite3.connect(database) as db:
        migrated_task = db.execute(
            "select input_json, result_json from email_agent_classification_tasks "
            "where task_id=?",
            (task.task_id,),
        ).fetchone()
        migrated_canonical = db.execute(
            "select agent_result_json from email_classifications where id=?",
            (classification_id,),
        ).fetchone()
    assert migrated_task is not None and migrated_canonical is not None
    migrated_payload = json.loads(migrated_task[0])
    migrated_queue_result = json.loads(migrated_task[1])
    migrated_canonical_result = json.loads(migrated_canonical[0])
    durable_keys = {
        "category",
        "important",
        "certainty",
        "confidence",
        "reason",
        "unsubscribe_candidate_index",
        "unsubscribe_candidate_source",
        "unsubscribe_candidate_digest",
        "unsubscribe_candidate_reference",
    }

    assert set(migrated_queue_result) == durable_keys | {
        "decision_status",
        "classification_id",
    }
    assert set(migrated_canonical_result) == durable_keys
    queue_projection = {key: migrated_queue_result[key] for key in durable_keys}
    assert DurableAgentClassificationResult.model_validate(queue_projection)
    assert DurableAgentClassificationResult.model_validate(migrated_canonical_result)
    assert migrated_queue_result["unsubscribe_candidate_index"] == 0
    assert migrated_canonical_result["unsubscribe_candidate_index"] == 0
    assert migrated_queue_result["unsubscribe_candidate_source"] == "legacy"
    assert migrated_canonical_result["unsubscribe_candidate_source"] == "legacy"
    all_durable_json = json.dumps(
        [migrated_payload, migrated_queue_result, migrated_canonical_result],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert private_url not in all_durable_json
    assert private_token not in all_durable_json


def test_v25_migration_redacts_only_sensitive_query_and_fragment_values(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v25-path-segment-privacy.sqlite3"
    store = EmailStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=148))
    unsubscribe_token = "legacy-secret"
    provider_auth_token = "provider-secret"
    signature = "signature-secret"
    private_url = (
        "https://news.example.test/email/unsubscribe"
        f"?unsubscribe_token={unsubscribe_token}&utm_medium=email"
        f"&campaign=newsletter&X-AmZ-SiGnAtUrE={signature}"
        f"#Provider-Auth-Token={provider_auth_token}&source=footer"
    )
    payload = json.loads(task.input_json)
    payload["unsubscribe_candidates"] = [private_url]
    payload["message"]["text"] = (
        f"Private candidate: {private_url}; echoed values: "
        f"{unsubscribe_token} {provider_auth_token} {signature}"
    )
    expected_unrelated_values = {
        "config_version": "config-email-newsletter-footer-v1",
        "category_descriptions": {
            "work": {"core": "Classify ordinary email and newsletter requests."},
            "junk": {"core": "Newsletter footer metadata."},
        },
        "folder_targets": {"work": "email/newsletter/footer"},
        "subject": "email newsletter footer metadata",
        "metadata": {"routing_note": "email/newsletter/footer are ordinary values"},
    }
    payload["config_version"] = expected_unrelated_values["config_version"]
    payload["category_descriptions"] = expected_unrelated_values[
        "category_descriptions"
    ]
    payload["folder_targets"] = expected_unrelated_values["folder_targets"]
    payload["message"]["subject"] = expected_unrelated_values["subject"]
    payload["message"]["metadata"] = expected_unrelated_values["metadata"]
    legacy_result = {
        "decision_status": "processed",
        "category": "junk",
        "important": False,
        "certainty": "certain",
        "confidence": 0.97,
        "reason": f"Unwanted candidate: {private_url}",
        "unsubscribe_candidate_index": 0,
        "unsubscribe_url": private_url,
        "classification_id": 148,
    }
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_agent_classification_tasks
            set status='done', input_json=?, result_json=? where task_id=?
            """,
            (json.dumps(payload), json.dumps(legacy_result), task.task_id),
        )
    _downgrade_email_database_to_v25(database)

    migrated = EmailClassificationTaskAdapter(EmailStore(database)).get_task(
        task.task_id
    )

    assert migrated is not None
    migrated_payload = json.loads(migrated.input_json)
    migrated_result = json.loads(migrated.result_json)
    assert (
        migrated_payload["config_version"]
        == expected_unrelated_values["config_version"]
    )
    assert (
        migrated_payload["category_descriptions"]
        == expected_unrelated_values["category_descriptions"]
    )
    assert (
        migrated_payload["folder_targets"]
        == expected_unrelated_values["folder_targets"]
    )
    assert (
        migrated_payload["message"]["subject"] == expected_unrelated_values["subject"]
    )
    assert (
        migrated_payload["message"]["metadata"] == expected_unrelated_values["metadata"]
    )
    durable_json = json.dumps(
        [migrated_payload, migrated_result], ensure_ascii=False, sort_keys=True
    )
    assert private_url not in durable_json
    assert unsubscribe_token not in durable_json
    assert provider_auth_token not in durable_json
    assert signature not in durable_json


@pytest.mark.parametrize(
    ("uid", "parameter_name", "component", "sensitive"),
    (
        (160, "auth_code", "query", True),
        (161, "Authorization-Code", "query", True),
        (162, "oauth%5Fcode", "query", True),
        (163, "VERIFICATION-CODE", "query", True),
        (164, "login%2Dcode", "fragment", True),
        (165, "Access_Code", "fragment", True),
        (166, "unsubscribe-code", "fragment", True),
        (167, "Provider%5FAuth%5FCode", "fragment", True),
        (168, "campaign_code", "query", False),
        (169, "Promo-Code", "fragment", False),
        (170, "zip%5Fcode", "query", False),
        (171, "session_token", "query", True),
        (172, "client_secret", "fragment", True),
        (173, "account_password", "query", True),
        (174, "session_cookie", "fragment", True),
        (175, "proxy_authorization", "query", True),
        (176, "X-AmZ-SiGnAtUrE", "fragment", True),
        (177, "access_key", "query", True),
        (178, "private-key", "fragment", True),
        (179, "api%5Fkey", "query", True),
        (180, "service_credential", "fragment", True),
        (181, "signed", "query", True),
        (182, "PreSigned", "fragment", True),
        (183, "signed%5Fquery", "query", True),
        (184, "bearer", "fragment", True),
        (185, "X-Bearer", "query", True),
        (186, "webhook", "fragment", True),
        (187, "x%5Fwebhook", "query", True),
        (188, "client_auth", "fragment", True),
        (189, "Provider-Auth", "query", True),
        (190, "app_key", "fragment", True),
        (191, "Client-Key", "query", True),
        (192, "subscription%5Fkey", "fragment", True),
        (193, "service-token-value", "query", True),
        (194, "provider_credential_hint", "fragment", True),
        (195, "request-secret-value", "query", True),
        (196, "utm_medium", "fragment", False),
        (197, "campaign", "query", False),
        (198, "source", "fragment", False),
        (199, "tracking_id", "query", False),
        (200, "auth", "query", True),
        (201, "key", "fragment", True),
        (202, "sig", "query", True),
        (203, "code", "fragment", True),
        (204, "hmac", "query", True),
        (205, "nonce", "fragment", True),
    ),
)
def test_v25_migration_classifies_shared_sensitive_parameter_families(
    tmp_path: Path,
    uid: int,
    parameter_name: str,
    component: str,
    sensitive: bool,
) -> None:
    database = tmp_path / f"v25-code-parameter-{uid}.sqlite3"
    store = EmailStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=uid))
    parameter_value = f"standalone-value-{uid}"
    ordinary_query = "utm_medium=email&campaign=newsletter&source=footer"
    if component == "query":
        private_url = (
            "https://news.example.test/email/unsubscribe?"
            f"{parameter_name}={parameter_value}&{ordinary_query}#source=footer"
        )
    else:
        private_url = (
            "https://news.example.test/email/unsubscribe?"
            f"{ordinary_query}#{parameter_name}={parameter_value}&source=footer"
        )
    unrelated_values = {
        "config_version": "config-email-newsletter-footer-v1",
        "category_descriptions": {
            "work": {"core": "Ordinary email campaign code."},
            "junk": {"core": "Newsletter footer source."},
        },
        "folder_targets": {"work": "email/newsletter/footer"},
        "subject": "email newsletter footer campaign source",
        "metadata": {"campaign_code": "visible-campaign-code"},
    }
    payload = json.loads(task.input_json)
    payload["unsubscribe_candidates"] = [private_url]
    payload["config_version"] = unrelated_values["config_version"]
    payload["category_descriptions"] = unrelated_values["category_descriptions"]
    payload["folder_targets"] = unrelated_values["folder_targets"]
    payload["message"]["subject"] = unrelated_values["subject"]
    payload["message"]["metadata"] = unrelated_values["metadata"]
    payload["message"]["text"] = (
        f"Candidate: {private_url}; standalone echo: {parameter_value}"
    )
    legacy_result = {
        "decision_status": "processed",
        "category": "junk",
        "important": False,
        "certainty": "certain",
        "confidence": 0.97,
        "reason": f"Unwanted candidate: {private_url}",
        "unsubscribe_candidate_index": 0,
        "unsubscribe_url": private_url,
        "classification_id": uid,
    }
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_agent_classification_tasks
            set status='done', input_json=?, result_json=? where task_id=?
            """,
            (json.dumps(payload), json.dumps(legacy_result), task.task_id),
        )
    _downgrade_email_database_to_v25(database)

    migrated = EmailClassificationTaskAdapter(EmailStore(database)).get_task(
        task.task_id
    )

    assert migrated is not None
    migrated_payload = json.loads(migrated.input_json)
    migrated_result = json.loads(migrated.result_json)
    assert migrated_payload["config_version"] == unrelated_values["config_version"]
    assert (
        migrated_payload["category_descriptions"]
        == unrelated_values["category_descriptions"]
    )
    assert migrated_payload["folder_targets"] == unrelated_values["folder_targets"]
    assert migrated_payload["message"]["subject"] == unrelated_values["subject"]
    assert migrated_payload["message"]["metadata"] == unrelated_values["metadata"]
    durable_json = json.dumps(
        [migrated_payload, migrated_result], ensure_ascii=False, sort_keys=True
    )
    assert private_url not in durable_json
    if sensitive:
        assert parameter_value not in durable_json
    else:
        assert parameter_value in migrated_payload["message"]["text"]


def test_v25_migration_structurally_redacts_unicode_url_and_token_everywhere(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import DurableAgentClassificationResult

    database = tmp_path / "v25-unicode-private-data.sqlite3"
    store = EmailStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=146))
    private_token = "密钥Ω"
    private_url = f"https://例子.测试/退订?令牌={private_token}"
    payload = json.loads(task.input_json)
    payload["unsubscribe_candidates"] = [private_url]
    payload["message"]["text"] = f"请点击 {private_url}"
    payload["message"]["subject"] = f"私人值 {private_token}"
    payload["migration_probe"] = {
        "nested_url": [private_url],
        "nested_token": {"value": private_token},
    }
    legacy_result = {
        "decision_status": "processed",
        "category": "junk",
        "important": False,
        "certainty": "certain",
        "confidence": 0.97,
        "reason": f"垃圾邮件 {private_url} token={private_token}",
        "unsubscribe_candidate_index": 0,
        "unsubscribe_url": private_url,
        "classification_id": 146,
    }
    classification_id = 9146
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=classification_id,
        account_id="account-primary",
        category=EmailCategory.JUNK,
        classification_source="agent",
        confidence=0.97,
        model_id="email-classifier-agent:v1",
        config_version="config-v1",
        actions=(),
        action_parameters={},
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    durable_result = DurableAgentClassificationResult(
        category="junk",
        important=False,
        certainty="certain",
        confidence=0.97,
        reason="Unwanted subscription.",
        unsubscribe_candidate_index=None,
        unsubscribe_candidate_source=None,
        unsubscribe_candidate_digest=None,
        unsubscribe_candidate_reference=None,
    )
    store.persist_scan_result(
        EmailClassification(
            classification_id=classification_id,
            stable_message_identity=(
                "account-primary:message-id:<legacy-canonical-146@example.com>"
            ),
            provider_locator=EmailProviderLocator(
                account_id="account-primary",
                folder="INBOX",
                uidvalidity=42,
                uid=9146,
                rfc_message_id="<legacy-canonical-146@example.com>",
            ),
            category=EmailCategory.JUNK,
            confidence=0.97,
            margin=0.97,
            probabilities={EmailCategory.JUNK: 1.0},
            model_id="email-classifier-agent:v1",
            config_version="config-v1",
            status=EmailClassificationStatus.PROCESSED,
            classification_source="agent",
            action_plan=plan,
        ),
        agent_result=durable_result,
        sender="sender@example.com",
        subject="Legacy canonical Agent result",
        model_text="__subject__legacy canonical agent result",
    )
    canonical_legacy_result = {
        key: value
        for key, value in legacy_result.items()
        if key not in {"decision_status", "classification_id"}
    }
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update email_agent_classification_tasks
            set status='done', input_json=?, result_json=?
            where task_id=?
            """,
            (json.dumps(payload), json.dumps(legacy_result), task.task_id),
        )
        db.execute(
            "update email_classifications set agent_result_json=? where id=?",
            (json.dumps(canonical_legacy_result), classification_id),
        )
    _downgrade_email_database_to_v25(database)

    EmailStore(database)
    with sqlite3.connect(database) as db:
        task_documents = db.execute(
            """
            select input_json, result_json
            from email_agent_classification_tasks where task_id=?
            """,
            (task.task_id,),
        ).fetchone()
        canonical_document = db.execute(
            "select agent_result_json from email_classifications where id=?",
            (classification_id,),
        ).fetchone()
    assert task_documents is not None and canonical_document is not None
    decoded_documents = [
        json.loads(task_documents[0]),
        json.loads(task_documents[1]),
        json.loads(canonical_document[0]),
    ]

    def decoded_strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [text for item in value for text in decoded_strings(item)]
        if isinstance(value, dict):
            return [
                text
                for key, item in value.items()
                for text in [*decoded_strings(key), *decoded_strings(item)]
            ]
        return []

    all_strings = [
        text for document in decoded_documents for text in decoded_strings(document)
    ]
    assert all(private_url not in text for text in all_strings)
    assert all(private_token not in text for text in all_strings)
    assert all("unsubscribe_url" not in document for document in decoded_documents[1:])
    assert decoded_documents[1]["unsubscribe_candidate_source"] == "legacy"
    assert decoded_documents[2]["unsubscribe_candidate_source"] == "legacy"


def test_classification_task_claim_is_single_owner_and_recoverable(tmp_path: Path):
    adapter = EmailClassificationTaskAdapter(
        _email_store(tmp_path), retry_base_seconds=0
    )
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 42,
            "messageId": "<mail-42@example.com>",
            "providerUnread": True,
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )
    adapter.ensure_task(task_input)

    claimed = adapter.claim_next(owner="worker-1")

    assert claimed is not None and claimed.status == "running"
    assert adapter.claim_next(owner="worker-2") is None
    adapter.fail(claimed, error="provider unavailable", retryable=True)
    assert adapter.claim_next(owner="worker-2") is not None


def test_live_classifier_lease_cannot_be_stolen_or_completed_by_stale_claim(
    tmp_path: Path,
):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        _email_store(tmp_path), now=lambda: now[0], lease_seconds=60
    )
    adapter.ensure_task(_classification_input(uid=142))
    first = adapter.claim_next(owner="worker-1")
    assert first is not None
    assert adapter.recover_running_tasks() == 0
    assert adapter.claim_next(owner="worker-2") is None

    now[0] = datetime(2026, 9, 8, 0, 2, tzinfo=timezone.utc)
    assert adapter.recover_running_tasks() == 1
    second = adapter.claim_next(owner="worker-2")
    assert second is not None and second.generation == first.generation + 1
    with pytest.raises(ValueError, match="lease changed"):
        adapter.complete(first, {"decision_status": "processed"})


def test_fast_restart_reclaims_claim_when_lease_expires_during_normal_polling(
    tmp_path: Path,
):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    store = _email_store(tmp_path)
    original = EmailClassificationTaskAdapter(
        store, now=lambda: now[0], lease_seconds=60
    )
    original.ensure_task(_classification_input(uid=144))
    first = original.claim_next(owner="worker-before-restart")
    assert first is not None

    now[0] = datetime(2026, 9, 8, 0, 0, 30, tzinfo=timezone.utc)
    restarted = EmailClassificationTaskAdapter(
        _email_store(tmp_path), now=lambda: now[0], lease_seconds=60
    )
    assert restarted.claim_next(owner="worker-after-restart") is None

    now[0] = datetime(2026, 9, 8, 0, 1, 1, tzinfo=timezone.utc)
    reclaimed = restarted.claim_next(owner="worker-after-restart")

    assert reclaimed is not None
    assert reclaimed.owner == "worker-after-restart"
    assert reclaimed.generation == first.generation + 1
    with pytest.raises(ValueError, match="lease changed"):
        original.complete(first, {"decision_status": "processed"})


def test_final_attempt_crash_becomes_terminal_when_lease_expires(
    tmp_path: Path,
):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        _email_store(tmp_path),
        now=lambda: now[0],
        lease_seconds=60,
        max_attempts=2,
        retry_base_seconds=1,
    )
    task = adapter.ensure_task(_classification_input(uid=145))
    first = adapter.claim_next(owner="worker-first-attempt")
    assert first is not None
    adapter.fail(first, error="ConnectionError:offline", retryable=True)

    now[0] = datetime(2026, 9, 8, 0, 0, 2, tzinfo=timezone.utc)
    final = adapter.claim_next(owner="worker-final-attempt")
    assert final is not None
    assert final.attempt_count == 2

    now[0] = datetime(2026, 9, 8, 0, 1, 3, tzinfo=timezone.utc)
    assert adapter.claim_next(owner="worker-after-final-crash") is None

    terminal = adapter.get_task(task.task_id)
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.attempt_count == 2
    assert terminal.owner == ""
    assert terminal.lease_expires_at == ""
    assert terminal.error == "classification task lease expired after final attempt"
    with pytest.raises(ValueError, match="lease changed"):
        adapter.complete(final, {"decision_status": "processed"})


def test_default_classifier_lease_outlasts_maximum_agent_turn(tmp_path: Path):
    adapter = EmailClassificationTaskAdapter(_email_store(tmp_path))

    assert adapter.lease_seconds > 900


def test_classifier_retry_is_bounded_and_backed_off(tmp_path: Path):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        _email_store(tmp_path),
        now=lambda: now[0],
        max_attempts=2,
        retry_base_seconds=10,
    )
    adapter.ensure_task(_classification_input(uid=143))
    first = adapter.claim_next(owner="worker")
    assert first is not None
    adapter.fail(first, error="ConnectionError:offline", retryable=True)
    assert adapter.claim_next(owner="worker") is None
    now[0] = datetime(2026, 9, 8, 0, 0, 11, tzinfo=timezone.utc)
    second = adapter.claim_next(owner="worker")
    assert second is not None
    adapter.fail(second, error="ConnectionError:offline", retryable=True)
    assert adapter.get_task(second.task_id).status == "failed"


def test_classification_task_bootstrap_recovers_crash_interrupted_claim(tmp_path: Path):
    store = _email_store(tmp_path)
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        store, now=lambda: now[0], lease_seconds=60
    )
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 43,
            "messageId": "<mail-43@example.com>",
            "providerUnread": True,
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )
    adapter.ensure_task(task_input)
    assert adapter.claim_next(owner="crashed-worker") is not None

    now[0] = datetime(2026, 9, 8, 0, 2, tzinfo=timezone.utc)
    reopened = EmailClassificationTaskAdapter(
        _email_store(tmp_path), now=lambda: now[0], lease_seconds=60
    )
    assert reopened.recover_running_tasks() == 1
    reclaimed = reopened.claim_next(owner="replacement-worker")

    assert reclaimed is not None
    assert reclaimed.owner == "replacement-worker"


def _persist_authorization(
    store: EmailStore,
    plan,
    task_input: EmailAgentTaskInput,
) -> None:
    initial_plan = plan
    if plan.action_plan_version > 1:
        initial_plan = build_versioned_email_action_plan(
            action_plan_version=1,
            classification_id=plan.classification_id,
            account_id=plan.account_id,
            category=plan.category,
            classification_source=plan.classification_source,
            confidence=plan.confidence,
            model_id=plan.model_id,
            config_version=plan.config_version,
            actions=plan.actions,
            action_parameters=plan.action_parameters,
            created_at=plan.created_at,
        )
    store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": plan.classification_id,
                "stable_message_identity": task_input.stable_message_identity,
                "provider_locator": {
                    "account_id": plan.account_id,
                    "folder": "INBOX",
                    "uidvalidity": 42,
                    "uid": plan.classification_id,
                    "rfc_message_id": "<mail-41@example.com>",
                    "thread_id": task_input.thread_identity,
                },
                "category": plan.category,
                "confidence": plan.confidence,
                "margin": 0.42,
                "probabilities": {plan.category: plan.confidence},
                "model_id": plan.model_id,
                "config_version": plan.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": plan.classification_source,
                "action_plan": initial_plan,
            }
        ),
        sender=task_input.trigger.sender,
        subject=task_input.subject,
        model_text="__subject__contract confirmation",
        received_at=task_input.trigger.create_time,
    )
    if plan.action_plan_version > 1:
        store.append_action_plan_version(
            plan.classification_id,
            plan,
            confirmed_category=plan.category,
        )


def _authorized_adapter(
    tmp_path: Path,
    plan,
    task_input: EmailAgentTaskInput,
    *,
    task_store: AutoReplyStore | None = None,
    email_store: EmailStore | None = None,
) -> EmailAgentTaskAdapter:
    durable_email_store = email_store or _email_store(tmp_path)
    _persist_authorization(durable_email_store, plan, task_input)
    return EmailAgentTaskAdapter(
        task_store or _store(tmp_path),
        durable_email_store,
    )


def test_only_unsubscribe_creates_task_from_mixed_legacy_action_plan(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (
            EmailAction.LABEL,
            EmailAction.MARK_READ,
            EmailAction.AUTO_REPLY,
            EmailAction.UNSUBSCRIBE,
        )
    )
    task_input = _task_input()
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    )

    first = adapter.ensure_action_plan_tasks(plan, task_input)
    replay = adapter.ensure_action_plan_tasks(plan, task_input)

    assert [route.action_type for route in first] == [EmailAction.UNSUBSCRIBE]
    assert [route.task.id for route in replay] == [route.task.id for route in first]
    assert store.count_reply_tasks(channel="email") == 1
    assert {route.task.channel for route in first} == {"email"}
    assert {route.task.status for route in first} == {"pending"}
    assert store.count_sent_replies() == 0
    assert all(
        store.list_agent_runs_for_task_generation(
            route.task.id,
            route.task.execution_generation,
        )
        == []
        for route in first
    )
    assert {route.task.conversation_id for route in first} == {
        email_conversation_id(plan.account_id, task_input.thread_identity)
    }
    for route in first:
        assert route.task.trigger_message_id == email_action_identity(
            account_id=plan.account_id,
            stable_message_identity=task_input.stable_message_identity,
            action_type=route.action_type,
            action_plan_version=plan.action_plan_version,
        )


def test_task_producer_builds_email_task_from_persisted_message_context(
    tmp_path: Path,
):
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    task_store = _store(tmp_path)
    producer = EmailActionTaskProducer(task_store, email_store)

    routes = producer.produce(
        plan,
        {
            "accountId": plan.account_id,
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 41,
            "messageId": "<mail-41@example.com>",
            "stableMessageIdentity": task_input.stable_message_identity,
            "threadId": task_input.thread_identity,
            "from": {"email": task_input.trigger.sender},
            "subject": task_input.subject,
            "textBody": task_input.trigger.text,
            "markdownBody": task_input.trigger.text,
        },
    )

    assert len(routes) == 1
    assert routes[0].action_type is EmailAction.UNSUBSCRIBE
    assert routes[0].task.channel == "email"
    assert task_store.count_reply_tasks(channel="email") == 1


def test_task_producer_projects_html_only_unsubscribe_as_opaque_reference(
    tmp_path: Path,
):
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    private_url = "https://news.example.com/unsubscribe?token=html-only-private"
    message = parse_rfc822_message(
        (
            b"From: customer@example.com\r\n"
            b"To: derek@example.com\r\n"
            b"Subject: Newsletter\r\n"
            b"Date: Sun, 30 Aug 2026 08:00:00 +0000\r\n"
            b"Message-ID: <mail-41@example.com>\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            + (
                '<p>Newsletter</p><a href="' + private_url + '">Unsubscribe</a>'
            ).encode()
        ),
        account_id=plan.account_id,
        folder="INBOX",
        uidvalidity=42,
        uid=41,
    )

    [route] = EmailActionTaskProducer(
        _store(tmp_path),
        email_store,
    ).produce(plan, message)
    payload = json.loads(route.task.trigger_message_json)

    assert payload["unsubscribe_entries"] == [
        {
            "index": 0,
            "digest": payload["unsubscribe_entries"][0]["digest"],
            "reference": payload["unsubscribe_entries"][0]["reference"],
            "source": "body_html_https",
        }
    ]
    assert payload["unsubscribe_entries"][0]["reference"].startswith(
        "unsubscribe-entry:"
    )
    policy = BrowserNetworkPolicy(frozenset({"https://news.example.com"}))
    assert payload["unsubscribe_network_policy_reference"] == policy.reference
    assert payload["unsubscribe_network_policy_origin_references"] == list(
        policy.origin_references
    )
    assert private_url not in route.task.trigger_message_json
    assert "html-only-private" not in route.task.trigger_message_json
    assert private_url not in repr(route.context)
    assert private_url.encode() not in (tmp_path / "email-agent.sqlite3").read_bytes()


def test_task_producer_policy_is_exact_deterministic_https_candidate_origin_set(
    tmp_path: Path,
) -> None:
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    first = "https://news.example.com/unsubscribe?token=first-private"
    second = "https://preferences.example.net/opt-out?token=second-private"
    message = parse_rfc822_message(
        (
            b"From: customer@example.com\r\n"
            b"To: derek@example.com\r\n"
            b"Subject: Newsletter\r\n"
            b"Date: Sun, 30 Aug 2026 08:00:00 +0000\r\n"
            b"Message-ID: <mail-41@example.com>\r\n"
            + f"List-Unsubscribe: <{first}>, <mailto:list@example.com>\r\n".encode()
            + b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            + f'<a href="{second}">Unsubscribe</a>'.encode()
        ),
        account_id=plan.account_id,
        folder="INBOX",
        uidvalidity=42,
        uid=41,
    )

    [route] = EmailActionTaskProducer(
        _store(tmp_path),
        email_store,
    ).produce(plan, message)
    payload = json.loads(route.task.trigger_message_json)
    policy = BrowserNetworkPolicy(
        frozenset(
            {
                "https://news.example.com",
                "https://preferences.example.net",
            }
        )
    )

    assert payload["unsubscribe_network_policy_reference"] == policy.reference
    assert payload["unsubscribe_network_policy_origin_references"] == list(
        policy.origin_references
    )
    assert len(payload["unsubscribe_entries"]) == 2
    encoded = route.task.trigger_message_json
    for forbidden in (first, second, "first-private", "second-private", "mailto:"):
        assert forbidden not in encoded


def test_task_producer_does_not_persist_auto_reply_under_current_policy(
    tmp_path: Path,
):
    plan = _plan((EmailAction.AUTO_REPLY,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    producer = EmailActionTaskProducer(_store(tmp_path), email_store)

    routes = producer.produce(
        plan,
        {
            "accountId": plan.account_id,
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 41,
            "messageId": "<mail-41@example.com>",
            "stableMessageIdentity": task_input.stable_message_identity,
            "threadId": task_input.thread_identity,
            "from": {"email": task_input.trigger.sender},
            "subject": task_input.subject,
            "textBody": task_input.trigger.text,
            "markdownBody": task_input.trigger.text,
        },
    )

    assert routes == ()
    assert _store(tmp_path).count_reply_tasks(channel="email") == 0


def test_disabled_mail_review_does_not_create_email_tasks(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("mail_review", False)

    adapter = EmailAgentTaskAdapter(store, email_store, feature_registry=registry)
    assert adapter.ensure_action_plan_tasks(plan, task_input) == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_disabling_mail_review_does_not_change_existing_email_task(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    enabled_adapter = EmailAgentTaskAdapter(
        store, email_store, feature_registry=registry
    )
    [existing] = enabled_adapter.ensure_action_plan_tasks(plan, task_input)
    registry.set_enabled("mail_review", False)

    [replayed] = enabled_adapter.ensure_action_plan_tasks(plan, task_input)
    assert replayed.task.id == existing.task.id
    assert replayed.task.status == existing.task.status == "pending"


def test_classification_with_zero_agent_actions_creates_no_reply_task(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.LABEL, EmailAction.ARCHIVE),
        classification_source="user",
    )

    routes = EmailAgentTaskAdapter(
        store, _email_store(tmp_path)
    ).ensure_action_plan_tasks(
        plan,
        _task_input(),
    )

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_adapter_requires_one_database_for_atomic_email_authorization(
    tmp_path: Path,
):
    task_store = AutoReplyStore(tmp_path / "tasks.sqlite3")
    email_store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(ValueError, match="share one database"):
        EmailAgentTaskAdapter(task_store, email_store)


def test_action_plan_version_and_account_are_part_of_task_identity(tmp_path: Path):
    store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    first_plan = _plan((EmailAction.UNSUBSCRIBE,), version=1)
    second_plan = _plan((EmailAction.UNSUBSCRIBE,), version=2)
    other_account_plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        version=1,
        classification_id=42,
        account_id="account-secondary",
    )

    adapter = _authorized_adapter(
        tmp_path,
        first_plan,
        _task_input(),
        task_store=store,
        email_store=email_store,
    )
    first = adapter.ensure_action_plan_tasks(first_plan, _task_input())[0]
    _persist_authorization(email_store, second_plan, _task_input())
    second = adapter.ensure_action_plan_tasks(second_plan, _task_input())[0]
    _persist_authorization(
        email_store,
        other_account_plan,
        _task_input(account_id="account-secondary"),
    )
    other = adapter.ensure_action_plan_tasks(
        other_account_plan,
        _task_input(account_id="account-secondary"),
    )[0]

    assert len({first.task.id, second.task.id, other.task.id}) == 3
    assert (
        len(
            {
                first.task.trigger_message_id,
                second.task.trigger_message_id,
                other.task.trigger_message_id,
            }
        )
        == 3
    )


def test_persisted_payload_is_traceable_without_message_secrets_or_attachments(
    tmp_path: Path,
):
    plan = _plan((EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE))
    task_input = _task_input()
    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]

    payload = json.loads(route.task.trigger_message_json)
    encoded = route.task.trigger_message_json

    assert payload["schema"] == "email_agent_action.v1"
    assert payload["account_id"] == "account-primary"
    assert payload["action_plan_version"] == 1
    assert payload["action_type"] == "unsubscribe"
    assert payload["action_identity"] == route.task.trigger_message_id
    assert payload["model_id"] == "email-model:2026-08-30:sha256:test"
    assert payload["action_parameters"] == {}
    for forbidden in (
        "private-token",
        "do-not-persist",
        "/Users/derek/private",
        "attachment.bin",
        "contract.pdf",
        "application/pdf",
    ):
        assert forbidden not in encoded


def test_unsubscribe_task_projects_only_redacted_real_entry_references(
    tmp_path: Path,
) -> None:
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    private_url = "https://news.example.com/unsubscribe?token=private-token"
    task_input = replace(
        _task_input(),
        list_unsubscribe=f"<{private_url}>",
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        body_text="",
        unsubscribe_authentication=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:mail-41",
        ),
        unsubscribe_network_policy_reference=BrowserNetworkPolicy(
            frozenset({"https://news.example.com"})
        ).reference,
        unsubscribe_network_policy_origin_references=BrowserNetworkPolicy(
            frozenset({"https://news.example.com"})
        ).origin_references,
    )

    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]
    payload = json.loads(route.task.trigger_message_json)
    entries = payload["unsubscribe_entries"]

    assert entries == [
        {
            "index": 0,
            "digest": entries[0]["digest"],
            "reference": entries[0]["reference"],
            "source": "header_one_click_https",
        }
    ]
    assert entries[0]["reference"].startswith("unsubscribe-entry:")
    assert payload["unsubscribe_authentication"] == {
        "evidence_reference": "dkim-evidence:mail-41",
        "one_click_verified": True,
    }
    assert private_url not in route.task.trigger_message_json
    assert private_url not in repr(task_input)

    accepted = ProposedAction.model_validate(
        {
            "description": "Unsubscribe the current sender",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": payload["account_id"],
                "stable_message_identity": payload["stable_message_identity"],
                "thread_identity": payload["thread_identity"],
                "entry_reference": entries[0]["reference"],
                "network_policy_reference": payload[
                    "unsubscribe_network_policy_reference"
                ],
                "network_policy_origin_references": payload[
                    "unsubscribe_network_policy_origin_references"
                ],
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "step-1",
                        "kind": "post_one_click",
                        "target_reference": entries[0]["reference"],
                    }
                ]
            },
            "expected_verification": "Read terminal provider evidence.",
        }
    )

    effect = accepted_email_unsubscribe_effect(route.task, accepted)
    assert effect.entry_reference == entries[0]["reference"]


def test_accepted_unsubscribe_rejects_opening_mailto_entry(tmp_path: Path) -> None:
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = replace(
        _task_input(),
        list_unsubscribe="<https://example.com/unsubscribe?token=private-token>",
        body_text="",
    )
    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]
    payload = json.loads(route.task.trigger_message_json)
    [entry] = payload["unsubscribe_entries"]
    entry.update({"source": "header_mailto", "priority": 20})
    persisted_task = route.task.model_copy(
        update={"trigger_message_json": json.dumps(payload, sort_keys=True)}
    )
    accepted = ProposedAction.model_validate(
        {
            "description": "Open the projected unsubscribe entry",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": payload["account_id"],
                "stable_message_identity": payload["stable_message_identity"],
                "thread_identity": payload["thread_identity"],
                "entry_reference": entry["reference"],
                "network_policy_reference": payload[
                    "unsubscribe_network_policy_reference"
                ],
                "network_policy_origin_references": payload[
                    "unsubscribe_network_policy_origin_references"
                ],
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "step-mailto",
                        "kind": "open_entry",
                        "target_reference": entry["reference"],
                    }
                ]
            },
            "expected_verification": "Read terminal provider evidence.",
        }
    )

    effect = accepted_email_unsubscribe_effect(route.task, accepted)
    assert effect.operations[0].kind.value == "open_entry"

    with pytest.raises(ValueError, match="accepted unsubscribe proposal is invalid"):
        accepted_email_unsubscribe_effect(persisted_task, accepted)


def test_non_junk_unsubscribe_is_rejected_before_task_creation(
    tmp_path: Path,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        category=EmailCategory.NOTIFICATION,
    )
    private_url = "https://news.example.com/unsubscribe?token=private-token"
    task_input = replace(
        _task_input(),
        list_unsubscribe=f"<{private_url}>",
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        body_text="",
        unsubscribe_authentication=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:mail-41",
        ),
        unsubscribe_network_policy_reference=BrowserNetworkPolicy(
            frozenset({"https://news.example.com"})
        ).reference,
        unsubscribe_network_policy_origin_references=BrowserNetworkPolicy(
            frozenset({"https://news.example.com"})
        ).origin_references,
    )

    adapter = _authorized_adapter(tmp_path, plan, task_input)
    with pytest.raises(
        EmailAgentTaskMetadataError,
        match="only junk may create an unsubscribe task",
    ):
        adapter.ensure_action_plan_tasks(plan, task_input)
    assert adapter.store.count_reply_tasks(channel="email") == 0


@pytest.mark.parametrize(
    "unsafe_instruction",
    (
        "Use password=do-not-store",
        "Read /Users/derek/private/reply.txt before replying",
        "Open https://example.com/unsubscribe?token=private-token",
    ),
)
def test_disabled_auto_reply_metadata_is_ignored_before_task_persistence(
    tmp_path: Path,
    unsafe_instruction: str,
):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY,), instruction=unsafe_instruction)
    task_input = _task_input()

    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(plan, task_input)

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_local_path_in_trace_identity_is_rejected_before_persistence(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = replace(
        _task_input(),
        thread_identity="/Users/derek/private/provider-thread.json",
    )

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 0


def test_unsafe_prior_receipt_is_rejected_before_context_or_task_persistence(
    tmp_path: Path,
):
    store = _store(tmp_path)
    unsafe_receipt = PriorReceipt(
        receipt_id="receipt-unsafe",
        operation="sent_state_readback",
        summary="authorization_token=do-not-persist",
        completed=False,
    )
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = replace(_task_input(), prior_receipts=(unsafe_receipt,))

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 0


def test_email_context_contains_text_metadata_receipts_and_no_image_inputs(
    tmp_path: Path,
):
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]
    context = route.context
    rendered = context.render_business_context(current_time="2026-08-30T09:00:00+00:00")

    assert context.channel == "email"
    assert context.image_paths == ()
    assert context.image_sha256s == ()
    assert [message.text for message in context.messages] == [
        "上一封邮件的纯文本回复。",
        _task_input().trigger.text,
    ]
    assert len(context.materials) == 1
    assert context.materials[0].kind == "attachment_metadata"
    assert context.materials[0].read_commands == ()
    assert "contract.pdf" in context.materials[0].reference
    assert "application/pdf" in context.materials[0].reference
    assert "Safe prior execution receipts" in rendered
    assert "sent_state_readback" in rendered
    assert "attachment.bin" in rendered  # email text is allowed as text evidence
    assert "Actual Codex image inputs" not in rendered
    assert "read attachment" not in rendered.casefold()


def test_refreshed_email_context_contains_one_opaque_continuation_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        classification_source="user",
        category=EmailCategory.JUNK,
    )
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        email_store=email_store,
    )
    first = adapter.ensure_action_plan_tasks(plan, task_input)[0]
    payload = json.loads(first.task.trigger_message_json)
    entry_reference = payload["unsubscribe_entries"][0]["reference"]
    operation_reference = "unsubscribe-operation:" + "a" * 64
    control_reference = "unsubscribe-control:" + "b" * 64
    operations = [
        {
            "operation_reference": operation_reference,
            "kind": "open_entry",
            "target_reference": entry_reference,
        }
    ]
    effect_digest = EmailUnsubscribeEffect(
        action_identity=payload["action_identity"],
        action_plan_id=payload["action_plan_id"],
        action_plan_version=payload["action_plan_version"],
        classification_id=payload["classification_id"],
        account_id=payload["account_id"],
        stable_message_identity=payload["stable_message_identity"],
        thread_identity=payload["thread_identity"],
        entry_reference=entry_reference,
        operations=(UnsubscribeOperation.from_mapping(operations[0]),),
        network_policy_reference=payload["unsubscribe_network_policy_reference"],
        network_policy_origin_references=tuple(
            payload["unsubscribe_network_policy_origin_references"]
        ),
    ).effect_digest
    claim = {
        "action_identity": payload["action_identity"],
        "effect_digest": effect_digest,
        "action_plan_id": payload["action_plan_id"],
        "action_plan_version": payload["action_plan_version"],
        "classification_id": payload["classification_id"],
        "account_id": payload["account_id"],
        "stable_message_identity": payload["stable_message_identity"],
        "thread_identity": payload["thread_identity"],
        "entry_reference": entry_reference,
        "operations": operations,
        "status": "awaiting_audit",
        "audit_agent_run_id": 101,
    }
    continuation = {
        "action_identity": payload["action_identity"],
        "effect_digest": effect_digest,
        "previous_effect_digest": "",
        "operations": operations,
        "controls": [
            {
                "reference": control_reference,
                "kind": "button",
                "intent": "confirm",
            }
        ],
        "observation_reference": "unsubscribe-state:" + "d" * 64,
        "network_policy_reference": payload["unsubscribe_network_policy_reference"],
        "network_policy_origin_references": payload[
            "unsubscribe_network_policy_origin_references"
        ],
    }
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_claim",
        lambda _identity: claim,
    )
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_continuation",
        lambda _identity: continuation,
    )

    refreshed = adapter.ensure_action_plan_tasks(plan, task_input)[0].context

    continuation_receipts = [
        receipt
        for receipt in refreshed.prior_receipts
        if receipt.operation == "unsubscribe_continuation"
    ]
    assert len(continuation_receipts) == 1
    receipt_payload = json.loads(continuation_receipts[0].summary)
    assert receipt_payload == {
        "accepted_operations": operations,
        "controls": continuation["controls"],
        "instruction": (
            "Audit accepted the durable prefix; propose exactly one next "
            "operation from the listed opaque controls."
        ),
        "previous_effect_digest": effect_digest,
        "requires_human": False,
    }
    assert "private-token" not in continuation_receipts[0].summary
    assert "https://" not in continuation_receipts[0].summary
    assert "/Users/" not in continuation_receipts[0].summary


def test_refreshed_email_context_rejects_private_continuation_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        classification_source="user",
        category=EmailCategory.JUNK,
    )
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        email_store=email_store,
    )
    first = adapter.ensure_action_plan_tasks(plan, task_input)[0]
    payload = json.loads(first.task.trigger_message_json)
    claim = {
        "action_identity": payload["action_identity"],
        "effect_digest": "c" * 64,
        "action_plan_id": payload["action_plan_id"],
        "action_plan_version": payload["action_plan_version"],
        "classification_id": payload["classification_id"],
        "account_id": payload["account_id"],
        "stable_message_identity": payload["stable_message_identity"],
        "thread_identity": payload["thread_identity"],
        "entry_reference": payload["unsubscribe_entries"][0]["reference"],
        "operations": [],
        "status": "awaiting_audit",
        "audit_agent_run_id": 101,
    }
    continuation = {
        "action_identity": payload["action_identity"],
        "effect_digest": "c" * 64,
        "previous_effect_digest": "",
        "operations": [],
        "controls": [
            {
                "reference": "https://example.com/unsubscribe?token=private",
                "kind": "button",
                "intent": "confirm",
            }
        ],
        "observation_reference": "unsubscribe-state:" + "d" * 64,
        "network_policy_reference": payload["unsubscribe_network_policy_reference"],
        "network_policy_origin_references": payload[
            "unsubscribe_network_policy_origin_references"
        ],
    }
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_claim",
        lambda _identity: claim,
    )
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_continuation",
        lambda _identity: continuation,
    )

    with pytest.raises(EmailAgentTaskMetadataError):
        adapter.ensure_action_plan_tasks(plan, task_input)


def test_terminal_unsubscribe_claim_without_continuation_adds_no_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        classification_source="user",
        category=EmailCategory.JUNK,
    )
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        email_store=email_store,
    )
    adapter.ensure_action_plan_tasks(plan, task_input)
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_claim",
        lambda _identity: {"status": "done"},
    )
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_continuation",
        lambda _identity: None,
    )

    refreshed = adapter.ensure_action_plan_tasks(plan, task_input)[0].context

    assert [
        receipt
        for receipt in refreshed.prior_receipts
        if receipt.operation == "unsubscribe_continuation"
    ] == []


def test_task_creation_rejects_wrong_persisted_message_and_historical_plan(
    tmp_path: Path,
):
    task_store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    current_plan = _plan((EmailAction.UNSUBSCRIBE,), version=2)
    persisted_input = _task_input()
    _persist_authorization(email_store, current_plan, persisted_input)
    adapter = EmailAgentTaskAdapter(task_store, email_store)

    wrong_message = replace(
        persisted_input,
        stable_message_identity="account-primary:message-id:<wrong@example.com>",
        trigger=replace(
            persisted_input.trigger,
            message_id="account-primary:message-id:<wrong@example.com>",
        ),
    )
    historical_plan = _plan((EmailAction.UNSUBSCRIBE,), version=1)

    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(current_plan, wrong_message)
    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(historical_plan, persisted_input)

    assert task_store.count_reply_tasks(channel="email") == 0


def test_current_plan_switch_cannot_interleave_after_authorization_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "email-agent.sqlite3"
    task_store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    task_input = _task_input()
    first_plan = _plan((EmailAction.UNSUBSCRIBE,), version=1)
    second_plan = _plan((EmailAction.UNSUBSCRIBE,), version=2)
    _persist_authorization(email_store, first_plan, task_input)
    adapter = EmailAgentTaskAdapter(task_store, email_store)
    authorization_checked = Event()
    allow_insert = Event()

    def observed_authorization_read(db, classification_id: int):
        row = db.execute(
            """
            select
                classifications.id as classification_id,
                classifications.account_id as account_id,
                classifications.stable_message_identity
                    as stable_message_identity,
                messages.thread_identity as thread_identity,
                classifications.current_action_plan_id
                    as current_action_plan_id
            from email_classifications as classifications
            join email_messages as messages
              on messages.account_id=classifications.account_id
             and messages.stable_message_identity=
                 classifications.stable_message_identity
            where classifications.id=?
              and classifications.status='processed'
              and classifications.current_action_plan_id is not null
            """,
            (classification_id,),
        ).fetchone()
        authorization_checked.set()
        if not allow_insert.wait(timeout=2):
            raise RuntimeError("test did not release email task insertion")
        return row

    monkeypatch.setattr(
        task_store,
        "_get_current_email_task_authorization",
        observed_authorization_read,
        raising=False,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            adapter.ensure_action_plan_tasks,
            first_plan,
            task_input,
        )
        try:
            assert authorization_checked.wait(timeout=1)
            competing_email_store = EmailStore(database)

            def zero_timeout_connect() -> sqlite3.Connection:
                connection = sqlite3.connect(database, timeout=0)
                connection.execute("pragma busy_timeout = 0")
                connection.execute("pragma foreign_keys = on")
                connection.row_factory = sqlite3.Row
                return connection

            monkeypatch.setattr(
                competing_email_store,
                "_connect",
                zero_timeout_connect,
            )
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing_email_store.append_action_plan_version(
                    first_plan.classification_id,
                    second_plan,
                    confirmed_category=second_plan.category,
                )
        finally:
            allow_insert.set()
        route = future.result(timeout=2)[0]

    email_store.append_action_plan_version(
        first_plan.classification_id,
        second_plan,
        confirmed_category=second_plan.category,
    )

    assert json.loads(route.task.trigger_message_json)["action_plan_id"] == (
        first_plan.action_plan_id
    )
    assert (
        email_store.get_classification(first_plan.classification_id)[
            "current_action_plan_id"
        ]
        == second_plan.action_plan_id
    )


def test_disabled_auto_reply_payload_cannot_block_unsubscribe_persistence(
    tmp_path: Path,
):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.UNSUBSCRIBE, EmailAction.AUTO_REPLY),
        instruction="Read ~/private/reply.txt before replying",
    )
    task_input = _task_input()

    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(plan, task_input)

    assert [route.action_type for route in routes] == [EmailAction.UNSUBSCRIBE]
    assert store.count_reply_tasks(channel="email") == 1


def test_agent_action_identity_conflict_rolls_back_the_whole_plan(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE))
    task_input = _task_input()
    conversation_id = email_conversation_id(
        plan.account_id,
        task_input.thread_identity,
    )
    unsubscribe_identity = email_action_identity(
        account_id=plan.account_id,
        stable_message_identity=task_input.stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=plan.action_plan_version,
    )
    auto_reply_identity = email_action_identity(
        account_id=plan.account_id,
        stable_message_identity=task_input.stable_message_identity,
        action_type=EmailAction.AUTO_REPLY,
        action_plan_version=plan.action_plan_version,
    )
    store.ensure_reply_task(
        channel="email",
        conversation_id=conversation_id,
        conversation_title="Conflicting historical input",
        single_chat=False,
        trigger_message_id=unsubscribe_identity,
        trigger_create_time=task_input.trigger.create_time,
        trigger_sender=task_input.trigger.sender,
        trigger_text="Conflicting historical input",
        trigger_message_json=json.dumps({"action_identity": "different"}),
    )
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    )

    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 1
    with sqlite3.connect(tmp_path / "email-agent.sqlite3") as db:
        assert (
            db.execute(
                "select count(*) from reply_tasks where trigger_message_id=?",
                (auto_reply_identity,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "unsafe_metadata",
    (
        {"outer": [{"path": "~/private/reply.txt"}]},
        {"outer": {"path": "~someone/private/reply.txt"}},
        {"outer": [{"uri": "FiLe:///Users/derek/private/reply.txt"}]},
        {"outer": [{"uri": "file%3A///Users/derek/private/reply.txt"}]},
    ),
)
def test_nested_home_relative_paths_and_file_uris_are_rejected(
    unsafe_metadata: object,
):
    with pytest.raises(EmailAgentTaskMetadataError):
        _assert_safe_email_metadata(unsafe_metadata)


@pytest.mark.parametrize(
    "unsafe_value",
    (
        _percent_encode("file:///Users/derek/private/reply.txt", 3),
        _percent_encode("~/private/reply.txt", 3),
        _percent_encode("https://example.com/unsubscribe/confirm", 3),
        _percent_encode(
            "https://example.com/resource?token=do-not-persist",
            3,
        ),
    ),
)
def test_metadata_canonicalization_rejects_triple_encoded_unsafe_values(
    unsafe_value: str,
):
    with pytest.raises(EmailAgentTaskMetadataError) as error:
        _assert_safe_email_metadata({"outer": [{"value": unsafe_value}]})

    assert unsafe_value not in str(error.value)


def test_metadata_canonicalization_rejects_excessive_encoding_depth():
    unsafe_value = _percent_encode("file:///private/reply.txt", 10)

    with pytest.raises(EmailAgentTaskMetadataError) as error:
        _assert_safe_email_metadata(unsafe_value)

    assert unsafe_value not in str(error.value)


def test_metadata_canonicalization_rejects_oversized_text():
    oversized = "a" * 70_000

    with pytest.raises(EmailAgentTaskMetadataError):
        _assert_safe_email_metadata(oversized)


def test_safe_public_https_url_is_allowed_in_action_metadata(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.AUTO_REPLY,),
        instruction="Reference https://docs.example.com/help/getting-started?lang=zh.",
    )
    task_input = _task_input()

    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(
        plan,
        task_input,
    )

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


@pytest.mark.parametrize(
    "sensitive_url",
    (
        "https://example.com/resource?token=do-not-persist",
        "https://example.com/resource?X-Amz-Signature=do-not-persist",
        "https://example.com/resource?X-Amz-SignedHeaders=host",
        "https://example.com/resource#access_token=do-not-persist",
        "https://example.com/unsubscribe/confirm",
        "https://unsubscribe.example.com/confirm",
        "https://example.com/preferences?action=opt-out",
    ),
)
def test_disabled_auto_reply_sensitive_url_is_not_persisted(
    tmp_path: Path,
    sensitive_url: str,
):
    plan = _plan(
        (EmailAction.AUTO_REPLY,),
        instruction=f"Reference {sensitive_url}",
    )
    task_input = _task_input()
    store = _store(tmp_path)
    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(plan, task_input)

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_thread_identity_is_normalized_once_for_identity_payload_and_context(
    tmp_path: Path,
):
    store = _store(tmp_path)
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    spaced = replace(_task_input(), thread_identity="  thread-customer-41  ")
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        spaced,
        task_store=store,
    )

    first = adapter.ensure_action_plan_tasks(plan, spaced)[0]
    replay = adapter.ensure_action_plan_tasks(plan, _task_input())[0]

    assert first.task.id == replay.task.id
    assert json.loads(first.task.trigger_message_json)["thread_identity"] == (
        "thread-customer-41"
    )
    assert first.context.conversation_id == replay.context.conversation_id
