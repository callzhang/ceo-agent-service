"""A principal retention decision completes a step, not an opt-out effect."""

from contextlib import nullcontext
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from app.action_storage import update_email_action
from app.email_classifier_contracts import (
    EmailAction, EmailCategory, EmailClassification, EmailClassificationStatus,
    EmailProviderLocator, build_versioned_email_action_plan,
)
from app.email_store import EmailStore, email_action_identity
from app.email_unsubscribe import extract_unsubscribe_entries
from app.shared_action_definitions import email_projection
from app.state_storage import insert_state
from app.store import AutoReplyStore


class _Profile:
    def lock(self, *, timeout_seconds):
        assert timeout_seconds == 0
        return nullcontext()


@pytest.fixture
def retained_subscription(tmp_path):
    path = tmp_path / "mail.sqlite3"
    store = EmailStore(path)
    plan = build_versioned_email_action_plan(
        action_plan_version=1, classification_id=91, account_id="mailbox",
        category=EmailCategory.JUNK, classification_source="user", confidence=1.0,
        model_id="model", config_version="config",
        actions=(EmailAction.UNSUBSCRIBE, EmailAction.TRASH), action_parameters={},
        created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    identity = "mailbox:message-id:<newsletter@example.test>"
    classification = EmailClassification(
        classification_id=91, stable_message_identity=identity,
        provider_locator=EmailProviderLocator(
            account_id="mailbox", folder="INBOX", uidvalidity=7, uid=9,
            rfc_message_id="<newsletter@example.test>", thread_id="thread",
        ),
        category=EmailCategory.JUNK, confidence=1.0, margin=1.0,
        probabilities={"junk": 1.0}, model_id="model", config_version="config",
        status=EmailClassificationStatus.PROCESSED, classification_source="user",
        action_plan=plan,
    )
    store.persist_scan_result(
        classification, sender="sender@example.test", recipients=("me@example.test",),
        subject="Newsletter", normalized_text="newsletter", preview="newsletter",
        received_at="2026-11-01T00:00:00+00:00", model_text="newsletter",
    )
    action_identity = email_action_identity(
        account_id="mailbox", stable_message_identity=identity,
        action_type=EmailAction.UNSUBSCRIBE, action_plan_version=1,
    )
    entry = extract_unsubscribe_entries(
        list_unsubscribe="<https://news.example.test/unsubscribe>"
    )[0]
    payload = dict(
        schema="email_agent_action.v1", lifecycle_version="email_unsubscribe_audited_v2",
        action_type="unsubscribe", action_identity=action_identity,
        action_plan_id=plan.action_plan_id, action_plan_version=1,
        classification_id=91, account_id="mailbox", stable_message_identity=identity,
        thread_identity="thread", unsubscribe_entries=[{
            "index": entry.index, "source": entry.source.value,
            "reference": entry.reference,
            "digest": entry.reference.removeprefix("unsubscribe-entry:"),
        }],
    )
    task_store = AutoReplyStore(path)
    task = task_store.ensure_reply_task(
        channel="email", conversation_id="email:thread", conversation_title="Newsletter",
        single_chat=False, trigger_message_id=action_identity,
        trigger_create_time="2026-11-01T00:00:00+00:00", trigger_sender="sender@example.test",
        trigger_text="Unsubscribe", trigger_message_json=json.dumps(payload),
        execution_generation="original",
    )
    task = task_store.claim_reply_task(task.id)
    assert task is not None
    task_store.fail_reply_task(task.id, "provider_timeout", expected_execution_generation="original")
    assert store.claim_next_direct_action(claimed_at="2026-11-01T00:01:00+00:00") is None
    decision = store.cancel_failed_email_unsubscribe_task(
        task_id=task.id, expected_action_identity=action_identity,
        expected_execution_generation="original", expected_input_version=task.input_version,
        reason="Retain subscription, continue the authorized mailbox cleanup.",
        profile=_Profile(), apply=True,
    )
    return store, task_store, task, action_identity, payload, decision


def test_retention_releases_authorized_trash_without_unsubscribe_receipt(retained_subscription):
    store, task_store, task, identity, _, decision = retained_subscription

    action = store.claim_next_direct_action(claimed_at="2026-11-01T00:02:00+00:00")

    assert action is not None
    assert action.action_type is EmailAction.TRASH
    assert store.get_email_unsubscribe_receipt(identity) is None
    assert store.get_email_unsubscribe_cancellation(identity) == decision
    assert task_store.get_reply_task(task.id).status == "skipped"
    assert store.claim_next_direct_action(claimed_at="2026-11-01T00:02:01+00:00") is None


@pytest.mark.parametrize("field,value", [
    ("task_id", 99999), ("decision", "other"), ("action_identity", "other"),
    ("execution_generation", "other"), ("input_version", 99),
])
def test_unbound_decision_does_not_release_trash(retained_subscription, field, value):
    store, _, _, identity, _, decision = retained_subscription
    with sqlite3.connect(store.path) as db:
        rowid = db.execute(
            "select rowid from (" + email_projection(
                "email_unsubscribe_cancellations", include_rowid=True,
            ) + ") where action_identity=?", (identity,),
        ).fetchone()[0]
        update_email_action(db, "email_unsubscribe_cancellations", rowid,
                            {"decision_json": json.dumps(decision | {field: value})})
    assert store.claim_next_direct_action(claimed_at="2026-11-01T00:02:00+00:00") is None


@pytest.mark.parametrize("field,value", [
    ("account_id", "other"), ("stable_message_identity", "other"),
    ("classification_id", 999), ("action_plan_id", "other"),
    ("action_plan_version", 2),
])
def test_other_source_or_plan_does_not_release_trash(retained_subscription, field, value):
    store, _, task, _, payload, _ = retained_subscription
    with sqlite3.connect(store.path) as db:
        db.execute("update task_inputs set trigger_message_json=? where id=?",
                   (json.dumps(payload | {field: value}), task.current_input_id))
    assert store.claim_next_direct_action(claimed_at="2026-11-01T00:02:00+00:00") is None


def test_unresolved_execution_does_not_release_trash(retained_subscription):
    store, _, _, identity, _, _ = retained_subscription
    with sqlite3.connect(store.path) as db:
        db.execute("begin")
        insert_state(db, "service_state", {
            "key": "email_unsubscribe_execution_pending:v1:" + identity,
            "value": json.dumps({"state": "uncertain"}),
        })
    assert store.claim_next_direct_action(claimed_at="2026-11-01T00:02:00+00:00") is None


@pytest.mark.parametrize("status,error", [("failed", "provider_timeout"), ("skipped", "no_entry")])
def test_task_without_current_cancellation_state_does_not_release_trash(
    retained_subscription, status, error,
):
    store, _, task, _, _, _ = retained_subscription
    with sqlite3.connect(store.path) as db:
        db.execute("update tasks set status=?,error=? where id=?", (status, error, task.id))
    assert store.claim_next_direct_action(claimed_at="2026-11-01T00:02:00+00:00") is None
