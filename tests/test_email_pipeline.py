from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
)
from app.email_classifier_training import CategoryEligibility
from app.email_pipeline import (
    EmailCategoryConfig,
    EmailModelPrediction,
    apply_human_confirmation,
    decide_classification,
)
from app.email_store import EmailStore


NOW = datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc)
MODEL_ID = "email-tfidf-lr-20260830T160000Z-1234567890abcdef"


def _prediction(*, confidence: float = 0.93) -> EmailModelPrediction:
    return EmailModelPrediction(
        category=EmailCategory.WORK,
        confidence=confidence,
        margin=0.41,
        probabilities={"work": confidence, "important": 1.0 - confidence},
        model_id=MODEL_ID,
    )


def _config(
    *,
    category: EmailCategory = EmailCategory.WORK,
    enabled: bool = True,
    threshold: float = 0.8,
    config_version: str = "email-config-v1",
    actions: tuple[EmailAction, ...] = (EmailAction.LABEL,),
) -> EmailCategoryConfig:
    parameters = (
        {EmailAction.LABEL: {"labels": [category.value]}}
        if EmailAction.LABEL in actions
        else {}
    )
    return EmailCategoryConfig(
        category=category,
        description="test category",
        threshold=threshold,
        actions=actions,
        action_parameters=parameters,
        enabled=enabled,
        config_version=config_version,
    )


def _eligibility(*, eligible: bool = True) -> CategoryEligibility:
    return CategoryEligibility(
        category=EmailCategory.WORK,
        configured_threshold=0.8,
        validated_precision=0.99 if eligible else 0.70,
        validation_sample_count=30,
        auto_action_eligible=eligible,
        reason="eligible" if eligible else "precision_gate_not_met",
    )


def _decision(
    *,
    confidence: float = 0.93,
    enabled: bool = True,
    eligible: bool = True,
):
    return decide_classification(
        _prediction(confidence=confidence),
        _config(enabled=enabled),
        _eligibility(eligible=eligible),
        classification_id=101,
        account_id="account-a",
        created_at=NOW,
    )


def _persist_decision(store: EmailStore, decision, *, classification_id: int = 101):
    classification = EmailClassification(
        classification_id=classification_id,
        stable_message_identity="account-a:message-id:<pipeline@example.com>",
        provider_locator=EmailProviderLocator(
            account_id="account-a",
            folder="INBOX",
            uidvalidity=42,
            uid=7,
            rfc_message_id="<pipeline@example.com>",
        ),
        category=decision.category,
        confidence=decision.confidence,
        margin=decision.margin,
        probabilities=dict(decision.probabilities),
        model_id=decision.model_id,
        config_version=decision.config_version,
        status=decision.status,
        classification_source="model",
        action_plan=decision.action_plan,
    )
    return store.persist_scan_result(
        classification,
        sender="sender@example.com",
        subject="pipeline test",
        model_text="__subject__pipeline test __body__body",
    )


def test_high_confidence_enabled_and_eligible_is_processed_with_immutable_plan():
    decision = _decision()

    assert decision.status is EmailClassificationStatus.PROCESSED
    assert decision.action_plan is not None
    assert decision.action_plan.action_plan_version == 1
    assert decision.action_plan.category is EmailCategory.WORK
    assert decision.action_plan.actions == (EmailAction.LABEL,)
    assert decision.action_plan.model_id == MODEL_ID
    assert decision.action_plan.config_version == "email-config-v1"
    with pytest.raises(ValidationError):
        decision.action_plan.config_version = "mutated"


@pytest.mark.parametrize(
    ("enabled", "eligible"),
    ((False, True), (True, False)),
)
def test_high_confidence_disabled_or_model_ineligible_is_pending_without_plan(
    enabled: bool,
    eligible: bool,
):
    decision = _decision(enabled=enabled, eligible=eligible)

    assert decision.status is EmailClassificationStatus.PENDING_FEEDBACK
    assert decision.action_plan is None


def test_below_threshold_is_pending_without_plan():
    decision = _decision(confidence=0.79)

    assert decision.status is EmailClassificationStatus.PENDING_FEEDBACK
    assert decision.action_plan is None


def test_pending_confirmation_records_feedback_then_current_config_plan_without_task(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))
    store.upsert_config(
        category=EmailCategory.IMPORTANT,
        description="important",
        threshold=0.97,
        actions=(EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE),
        action_parameters={
            EmailAction.AUTO_REPLY: {"instruction": "reply briefly"},
        },
        enabled=True,
        config_version="important-v3",
    )

    confirmed = apply_human_confirmation(
        store,
        pending["id"],
        EmailCategory.IMPORTANT,
        now=NOW,
    )

    assert confirmed is not None
    assert confirmed["status"] == "processed"
    assert confirmed["classification_source"] == "user"
    assert confirmed["action_plan"]["category"] == "important"
    assert confirmed["action_plan"]["config_version"] == "important-v3"
    assert confirmed["action_plan"]["model_id"] == MODEL_ID
    assert confirmed["action_plan"]["actions"] == ["auto_reply", "unsubscribe"]
    assert store.list_training_examples()[0]["label"] == "important"
    with sqlite3.connect(store.path) as db:
        table_names = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        assert db.execute("select count(*) from email_actions").fetchone()[0] == 0
    assert "reply_tasks" not in table_names
    assert "agent_tasks" not in table_names


def test_processed_correction_appends_feedback_and_plan_without_replaying_history(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    processed = _persist_decision(store, _decision())
    with sqlite3.connect(store.path) as db:
        old_action = db.execute(
            "select action_id from email_actions"
        ).fetchone()[0]
    store.append_action_attempt(
        action_id=old_action,
        attempt_number=1,
        status="done",
        provider_operation="add_label",
        provider_target="work",
        provider_result_id="provider-receipt-1",
        error="",
        started_at=NOW.isoformat(),
        finished_at=NOW.isoformat(),
    )
    store.upsert_config(
        category=EmailCategory.IMPORTANT,
        description="important",
        threshold=0.97,
        actions=(EmailAction.ARCHIVE, EmailAction.UNSUBSCRIBE),
        action_parameters={},
        enabled=True,
        config_version="important-v4",
    )

    corrected = apply_human_confirmation(
        store,
        processed["id"],
        EmailCategory.IMPORTANT,
        now=NOW,
    )

    assert corrected is not None
    assert corrected["classification_source"] == "user"
    assert corrected["confirmed_category"] == "important"
    assert corrected["current_action_plan_id"] != processed["current_action_plan_id"]
    assert corrected["action_plan"]["action_plan_version"] == 2
    assert corrected["action_plan"]["model_id"] == MODEL_ID
    assert corrected["action_plan"]["config_version"] == "important-v4"
    assert store.list_training_examples()[0]["label"] == "important"

    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        plans = db.execute(
            "select * from email_action_plans order by action_plan_version"
        ).fetchall()
        actions = db.execute(
            "select * from email_actions order by created_at, action_id"
        ).fetchall()
        attempts = db.execute(
            "select * from email_action_attempts order by id"
        ).fetchall()

    assert [
        (row["action_plan_version"], row["model_id"], row["config_version"])
        for row in plans
    ] == [
        (1, MODEL_ID, "email-config-v1"),
        (2, MODEL_ID, "important-v4"),
    ]
    assert {row["action_type"] for row in actions} == {"label", "archive"}
    assert next(row for row in actions if row["action_id"] == old_action)[
        "status"
    ] == "done"
    assert [(row["action_id"], row["provider_result_id"]) for row in attempts] == [
        (old_action, "provider-receipt-1")
    ]
    assert all(
        row["config_version"] in {"email-config-v1", "important-v4"}
        for row in actions
    )


def test_human_confirmation_uses_primary_key_lookup_not_classification_paging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))

    def reject_paging(**_kwargs):
        raise AssertionError("human confirmation must not page through classifications")

    monkeypatch.setattr(store, "list_classifications", reject_paging)

    confirmed = apply_human_confirmation(
        store,
        pending["id"],
        EmailCategory.WORK,
        now=NOW,
    )

    assert confirmed is not None
    assert confirmed["status"] == "processed"
