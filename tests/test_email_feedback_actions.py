from datetime import datetime, timezone
from pathlib import Path

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailProviderLocator,
)
from app.email_classifier_training import CategoryEligibility
from app.email_classifier_training import EmailActionEligibility
from app.email_pipeline import (
    EmailCategoryConfig,
    EmailModelPrediction,
    apply_human_confirmation,
    decide_classification,
)
from app.email_store import EmailStore


NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _prediction() -> EmailModelPrediction:
    return EmailModelPrediction(
        category=EmailCategory.WORK,
        confidence=0.999,
        margin=0.90,
        probabilities={"work": 0.999, "legal": 0.001},
        model_id="email-model:active-v1",
    )


def test_each_configured_action_is_independently_gated():
    category_config = EmailCategoryConfig(
        category=EmailCategory.WORK,
        description="Work",
        threshold=0.95,
        actions=(EmailAction.LABEL, EmailAction.TRASH),
        action_parameters={EmailAction.LABEL: {"labels": ["Work"]}},
        enabled=True,
        config_version="email-config:v1",
    )
    label_eligible_but_trash_ineligible = CategoryEligibility(
        category=EmailCategory.WORK,
        configured_threshold=0.95,
        validated_precision=0.96,
        validation_sample_count=30,
        auto_action_eligible=True,
        reason="label_gate_met_trash_gate_not_met",
        source_model_id=_prediction().model_id,
        automatic_candidate_count=30,
        evaluated_threshold=0.95,
        action_eligibility={
            EmailAction.LABEL: EmailActionEligibility(
                action=EmailAction.LABEL,
                auto_action_eligible=True,
                reason="action_precision_and_support_gate_met",
                source_model_id=_prediction().model_id,
                evidence_reference="email-model-eligibility:v1:label",
            ),
            EmailAction.TRASH: EmailActionEligibility(
                action=EmailAction.TRASH,
                auto_action_eligible=False,
                reason="action_precision_gate_not_met",
                source_model_id=_prediction().model_id,
                evidence_reference="email-model-eligibility:v1:trash",
            ),
        },
    )

    decision = decide_classification(
        _prediction(),
        category_config,
        label_eligible_but_trash_ineligible,
        classification_id=7,
        account_id="account-1",
        created_at=NOW,
    )

    assert decision.action_plan is not None
    assert decision.action_plan.actions == (EmailAction.LABEL,)
    assert all(
        action != EmailAction.AUTO_REPLY for action in decision.action_plan.actions
    )


def test_threshold_change_never_expands_an_existing_action_plan():
    original_config = EmailCategoryConfig(
        category=EmailCategory.WORK,
        description="Work",
        threshold=0.95,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["Work"]}},
        enabled=True,
        config_version="email-config:v1",
    )
    eligibility = CategoryEligibility(
        category=EmailCategory.WORK,
        configured_threshold=0.95,
        validated_precision=0.99,
        validation_sample_count=30,
        auto_action_eligible=True,
        reason="precision_and_sample_gate_met",
        source_model_id=_prediction().model_id,
        automatic_candidate_count=30,
        evaluated_threshold=0.95,
        action_eligibility={
            EmailAction.LABEL: EmailActionEligibility(
                action=EmailAction.LABEL,
                auto_action_eligible=True,
                reason="action_precision_and_support_gate_met",
                source_model_id=_prediction().model_id,
                evidence_reference="email-model-eligibility:v1:label",
            )
        },
    )
    original = decide_classification(
        _prediction(),
        original_config,
        eligibility,
        classification_id=8,
        account_id="account-1",
        created_at=NOW,
    )
    assert original.action_plan is not None
    original_plan = original.action_plan
    original_authorizations = original_plan.model_dump(mode="json")[
        "action_authorizations"
    ]

    changed_config = EmailCategoryConfig(
        category=EmailCategory.WORK,
        description="Work",
        threshold=0.90,
        actions=(EmailAction.LABEL, EmailAction.TRASH),
        action_parameters={EmailAction.LABEL: {"labels": ["Work"]}},
        enabled=True,
        config_version="email-config:v2",
    )
    changed_eligibility = CategoryEligibility(
        category=EmailCategory.WORK,
        configured_threshold=0.90,
        validated_precision=0.99,
        validation_sample_count=30,
        auto_action_eligible=False,
        reason="threshold_changed_since_training",
        source_model_id=_prediction().model_id,
        automatic_candidate_count=30,
        evaluated_threshold=0.95,
    )
    changed = decide_classification(
        _prediction(),
        changed_config,
        changed_eligibility,
        classification_id=8,
        account_id="account-1",
        created_at=NOW,
    )

    assert changed.action_plan is None
    assert original_plan.actions == (EmailAction.LABEL,)
    assert original_plan.config_version == "email-config:v1"
    assert (
        original_plan.model_dump(mode="json")["action_authorizations"]
        == original_authorizations
    )


def test_confirmed_plan_binds_user_model_and_current_config_without_reply(
    tmp_path: Path,
):
    model_version = "email-model:active-v1"
    prediction = _prediction()
    pending_decision = decide_classification(
        prediction,
        EmailCategoryConfig(
            category=EmailCategory.WORK,
            description="Work",
            threshold=1.0,
            actions=(EmailAction.LABEL,),
            action_parameters={EmailAction.LABEL: {"labels": ["Work"]}},
            enabled=True,
            config_version="email-config:prediction-v1",
        ),
        CategoryEligibility(
            category=EmailCategory.WORK,
            configured_threshold=1.0,
            validated_precision=0.99,
            validation_sample_count=30,
            auto_action_eligible=False,
            reason="model_only_prediction",
        ),
        classification_id=9,
        account_id="account-1",
        created_at=NOW,
    )
    store = EmailStore(tmp_path / "email.sqlite3")
    classification = EmailClassification(
        classification_id=9,
        stable_message_identity="account-1:message-id:<feedback@example.com>",
        provider_locator=EmailProviderLocator(
            account_id="account-1",
            folder="INBOX",
            uidvalidity=1,
            uid=9,
            rfc_message_id="<feedback@example.com>",
        ),
        category=pending_decision.category,
        confidence=pending_decision.confidence,
        margin=pending_decision.margin,
        probabilities=dict(pending_decision.probabilities),
        model_id=pending_decision.model_id,
        config_version=pending_decision.config_version,
        status=pending_decision.status,
        classification_source="model",
        action_plan=None,
    )
    persisted = store.upsert_classification(
        classification,
        sender="sender@example.com",
        subject="Feedback boundary",
        model_text="__subject__Feedback boundary __body__Text only",
    )
    category_config = store.upsert_config(
        category=EmailCategory.WORK,
        description="Work",
        threshold=0.95,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["Work"]}},
        enabled=True,
        config_version="email-config:confirmed-v2",
    )

    application = apply_human_confirmation(
        store,
        persisted["id"],
        EmailCategory.WORK,
        feedback_request_id="feedback-boundary-1",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert application is not None
    confirmed_plan = application.confirmed
    assert confirmed_plan["classification_source"] == "user"
    assert confirmed_plan["action_plan"]["model_id"] == model_version
    assert (
        confirmed_plan["action_plan"]["config_version"]
        == category_config["config_version"]
    )
    assert all(
        action != EmailAction.AUTO_REPLY.value
        for action in confirmed_plan["action_plan"]["actions"]
    )
