"""Seed direct Email actions through the real store, as the worker would create them."""
from datetime import datetime, timedelta

from app.email_classifier_contracts import (
    EmailAction, EmailCategory, EmailClassification, EmailClassificationStatus,
    EmailProviderLocator, build_email_action_plan,
)
from app.email_store import EmailStore

_CREATED_AT = datetime.fromisoformat("2026-09-16T07:27:13+00:00")


def persist_classification(store, *, classification_id, status, action=EmailAction.MOVE,
                           account_id="acct", config_version="v1",
                           sender="sender@example.com", subject=None):
    """Persist one classification (with its ActionPlan when processed); return the plan id."""
    plan = None
    if status is EmailClassificationStatus.PROCESSED:
        plan = build_email_action_plan(
            classification_id=classification_id, account_id=account_id,
            category=EmailCategory.WORK, classification_source="model",
            confidence=0.9, model_id="model-1", config_version=config_version,
            actions=(action,),
            action_parameters={EmailAction.MOVE: {"target_folder": "工作"}} if action is EmailAction.MOVE else {},
            created_at=_CREATED_AT,
        )
    store.persist_scan_result(
        EmailClassification(
            classification_id=classification_id,
            stable_message_identity=f"{account_id}:message-{classification_id}",
            provider_locator=EmailProviderLocator(
                account_id=account_id, folder="INBOX", uidvalidity=2, uid=classification_id,
            ),
            category=EmailCategory.WORK, confidence=0.9, margin=0.5,
            probabilities={EmailCategory.WORK: 0.9}, model_id="model-1",
            config_version=config_version, status=status,
            classification_source="model", action_plan=plan,
        ),
        sender=sender,
        subject=f"Subject {classification_id}" if subject is None else subject,
        normalized_text="body", preview="body", model_text="body",
        received_at="Mon, 08 Sep 2025 12:08:13 +0000",
    )
    return None if plan is None else plan.action_plan_id


def fail_action(store: EmailStore, action_id, *, attempts, retryable_last,
                error="imap_timeout"):
    """Claim and fail `attempts` times, like the worker; the last may leave a retry scheduled."""
    base = datetime.fromisoformat("2026-09-16T07:00:00+00:00")
    for number in range(1, attempts + 1):
        claimed_at = base + timedelta(days=number)
        action = store.claim_direct_action(action_id=action_id, claimed_at=claimed_at.isoformat())
        assert action is not None and action.attempt_number == number
        store.complete_direct_action_attempt(
            action, status="failed", provider_operation="READ",
            provider_target=action.locator.stable_message_identity,
            provider_result_id="", error=error,
            finished_at=(claimed_at + timedelta(seconds=75)).isoformat(),
            retryable=number < attempts or retryable_last,
        )


def complete_action(store: EmailStore, action_id):
    """Claim and complete one action as done, like the worker after a verified readback."""
    claimed_at = datetime.fromisoformat("2026-09-16T07:00:00+00:00")
    action = store.claim_direct_action(action_id=action_id, claimed_at=claimed_at.isoformat())
    assert action is not None
    store.complete_direct_action_attempt(
        action, status="done", provider_operation="READ",
        provider_target=action.locator.stable_message_identity,
        provider_result_id="receipt-1", error="",
        finished_at=(claimed_at + timedelta(seconds=75)).isoformat(),
    )
