from datetime import datetime, timezone

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
)
from app.email_classifier_training import CategoryEligibility
from app.email_pipeline import (
    EmailCategoryConfig,
    EmailModelPrediction,
    decide_classification,
)


def test_model_only_prediction_without_action_eligibility_stays_pending_feedback():
    prediction = EmailModelPrediction(
        category=EmailCategory.WORK,
        confidence=0.99,
        margin=0.80,
        probabilities={"work": 0.99, "legal": 0.01},
        model_id="email-model:candidate",
    )
    category_config = EmailCategoryConfig(
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
        auto_action_eligible=False,
        reason="model_not_active",
    )

    model_only_prediction = decide_classification(
        prediction,
        category_config,
        eligibility,
        classification_id=1,
        account_id="account-1",
        created_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert model_only_prediction.status == EmailClassificationStatus.PENDING_FEEDBACK
    assert model_only_prediction.status.value == "pending_feedback"
    assert model_only_prediction.action_plan is None
