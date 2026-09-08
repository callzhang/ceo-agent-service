from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import pickle

import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from app.email_classifier_contracts import (
    EmailAction,
    INITIAL_EMAIL_CATEGORY_KEYS,
)
from app.email_classifier_model import (
    CpuTfidfLogisticClassifier,
    EmailModelPrediction,
    email_message_to_text,
)
from app.email_model_registry import (
    EmailModelMetadata,
    EmailModelRegistry,
    build_model_id,
)
from app.email_worker import _scan_config


def _messages() -> tuple[list[dict[str, object]], list[str]]:
    terms = {
        "work": ("project meeting", "项目计划"),
        "human_resources": ("candidate interview", "候选人面试"),
        "legal": ("urgent contract", "紧急合同"),
        "financing": ("investor update", "融资进展"),
        "personal": ("family dinner", "朋友聚会"),
        "notification": ("security alert", "状态通知"),
        "external_billing": ("payment invoice", "账单发票"),
        "shopping": ("order delivery", "订单物流"),
        "junk": ("promotion offer", "促销广告"),
    }
    messages = [
        {
            "from": {"email": f"{label}@example.com"},
            "toRecipients": [],
            "subject": term,
            "textBody": f"{label} {term}",
        }
        for label, label_terms in terms.items()
        for term in label_terms
    ]
    labels = [label for label, label_terms in terms.items() for _ in label_terms]
    return messages, labels


def test_message_text_is_segmented_and_redacted():
    text = email_message_to_text(
        {
            "from": {"email": "person@example.com"},
            "toRecipients": [{"email": "derek@stardust.ai"}],
            "subject": "项目更新",
            "textBody": "访问 https://private.example/a，验证码 123456。",
        }
    )

    assert "person@example.com" not in text
    assert "derek@stardust.ai" not in text
    assert "https://private.example/a" not in text
    assert "123456" not in text
    assert "EMAIL" in text
    assert "URL" in text
    assert "NUMBER" in text


def test_message_text_redacts_quota_access_tokens():
    opaque_token = "qrp_ExampleAccessToken123456789"
    signed_token = "qrp.eyJleGFtcGxlIjoiYWNjb3VudCJ9.Signature123456"

    text = email_message_to_text(
        {
            "from": {"email": "alerts@example.com"},
            "toRecipients": [],
            "subject": "Quota Report Hub access token",
            "textBody": f"Paste {opaque_token} or {signed_token} into setup.",
        }
    )

    assert opaque_token not in text
    assert signed_token not in text
    assert text.count("TOKEN") >= 2


def test_legacy_artifact_with_old_category_labels_loads_and_predicts(tmp_path: Path):
    texts = ["urgent approval", "urgent contract", "project plan", "team meeting"]
    labels = ["important", "important", "work", "work"]
    vectorizer = TfidfVectorizer(token_pattern=r"\S+")
    classifier = LogisticRegression(random_state=42).fit(
        vectorizer.fit_transform(texts), labels
    )
    artifact = tmp_path / "legacy-email-model.pkl"
    artifact.write_bytes(
        pickle.dumps(
            {
                "format_version": CpuTfidfLogisticClassifier.FORMAT_VERSION,
                "feature_version": CpuTfidfLogisticClassifier.FEATURE_VERSION,
                "c": 0.25,
                "model_version": "legacy-model-v1",
                "vectorizer": vectorizer,
                "classifier": classifier,
            }
        )
    )

    loaded = CpuTfidfLogisticClassifier.load(artifact)
    prediction = loaded.predict("urgent approval")

    assert prediction.label in {"important", "work"}
    assert set(prediction.probabilities) == {"important", "work"}


def test_low_level_prediction_annotations_allow_raw_legacy_labels():
    annotations = EmailModelPrediction.__annotations__

    assert annotations["label"] == "str"
    assert annotations["probabilities"] == "Mapping[str, float]"


def test_cpu_classifier_predicts_and_round_trips_model_version(tmp_path: Path):
    messages, labels = _messages()
    classifier = CpuTfidfLogisticClassifier(model_version="email-model-test-1")
    classifier.fit_messages(
        messages,
        labels,
        enabled_category_keys=INITIAL_EMAIL_CATEGORY_KEYS,
    )

    prediction = classifier.predict_message(messages[1])
    assert prediction.label in set(INITIAL_EMAIL_CATEGORY_KEYS)
    assert 0 <= prediction.probability <= 1
    assert 0 <= prediction.margin <= 1
    assert prediction.model_version == "email-model-test-1"
    assert set(prediction.probabilities) == set(INITIAL_EMAIL_CATEGORY_KEYS)

    model_path = tmp_path / "email-model.pkl"
    classifier.save(model_path)
    loaded = CpuTfidfLogisticClassifier.load(model_path)
    loaded_prediction = loaded.predict_message(messages[1])
    assert loaded.model_version == "email-model-test-1"
    assert loaded_prediction.label == prediction.label
    assert loaded_prediction.probabilities == prediction.probabilities


def test_classifier_rejects_unknown_labels_and_unfitted_prediction():
    with pytest.raises(ValueError, match="unknown category"):
        CpuTfidfLogisticClassifier().fit(
            ["text", "other"],
            ["work", "unknown"],
            enabled_category_keys=("work", "legal"),
        )
    with pytest.raises(RuntimeError, match="not fitted"):
        CpuTfidfLogisticClassifier().predict("text")


def _stage_candidate(
    registry: EmailModelRegistry,
    tmp_path: Path,
    *,
    trained_at: datetime,
    suffix: str,
    parent_model_id: str | None = None,
) -> str:
    messages, labels = _messages()
    classifier = CpuTfidfLogisticClassifier(model_version="candidate")
    classifier.fit_messages(
        messages,
        labels,
        enabled_category_keys=INITIAL_EMAIL_CATEGORY_KEYS,
    )
    artifact = tmp_path / f"candidate-{suffix}.pkl"
    classifier.save(artifact)
    digest = sha256(artifact.read_bytes()).hexdigest()
    model_id = build_model_id(
        trained_at=trained_at,
        artifact_sha256=digest,
    )
    category_labels = tuple(sorted(set(labels)))
    metadata = EmailModelMetadata(
        model_id=model_id,
        parent_model_id=parent_model_id,
        model_family="tfidf-logistic-regression",
        tokenizer_version="jieba-default-v1",
        feature_version=CpuTfidfLogisticClassifier.FEATURE_VERSION,
        training_dataset_version="feedback-sha256:test",
        trained_at=trained_at.isoformat(),
        training_started_at=(trained_at - timedelta(seconds=2)).isoformat(),
        training_finished_at=trained_at.isoformat(),
        sample_count=90,
        new_sample_count=90,
        category_counts={label: 30 for label in category_labels},
        account_counts={"account-1": 90},
        validation_method="time-ordered-holdout",
        accuracy=0.99,
        macro_f1=0.99,
        per_category_metrics={
            label: {
                "precision": 0.99,
                "recall": 0.99,
                "f1": 0.99,
                "validation_sample_count": 30,
                "validation_positive_support": 30,
                "automatic_candidate_count": 30,
                "evaluated_threshold": 0.95,
                "configured_threshold": 0.95,
                "minimum_precision": 0.95,
                "minimum_validation_samples": 30,
                "auto_action_eligible": True,
                "eligibility_reason": "precision_and_sample_gate_met",
            }
            for label in category_labels
        },
        prediction_latency_p50_ms=0.5,
        prediction_latency_p95_ms=0.8,
        artifact_sha256=digest,
        status="candidate",
        promotion_reason="candidate_validation_pending",
        failure_reason="",
    )
    parity_texts = tuple(email_message_to_text(message) for message in messages)
    registry.stage_candidate(
        artifact,
        metadata,
        parity_texts=parity_texts,
        expected_labels=tuple(
            classifier.predict(text).label for text in parity_texts
        ),
    )
    return model_id


def _eligibility_rows() -> list[dict[str, object]]:
    return [
        {
            "category": category,
            "description": category,
            "enabled": True,
            "threshold": 0.95,
            "actions": [EmailAction.LABEL.value]
            if category == "work"
            else [],
            "action_parameters": (
                {EmailAction.LABEL.value: {"labels": ["Work"]}}
                if category == "work"
                else {}
            ),
            "config_version": "email-config:registry-boundary-v1",
        }
        for category in INITIAL_EMAIL_CATEGORY_KEYS
    ]


def _work_label_eligibility(registry: EmailModelRegistry, model_id: str):
    rows = _eligibility_rows()

    class ConfigStore:
        def list_configs(self):
            return rows

    config = _scan_config(ConfigStore(), registry.get_model(model_id))
    return config.category_eligibility["work"].action_eligibility[
        EmailAction.LABEL
    ]


def test_candidate_and_rejected_models_are_never_auto_action_eligible(
    tmp_path: Path,
):
    registry = EmailModelRegistry(tmp_path / "registry")
    first = _stage_candidate(
        registry,
        tmp_path,
        trained_at=datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc),
        suffix="first",
    )
    candidate_model = _work_label_eligibility(registry, first)
    registry.promote(first, reason="validated_candidate_passed")
    active_model = _work_label_eligibility(registry, first)
    second = _stage_candidate(
        registry,
        tmp_path,
        trained_at=datetime(2026, 8, 30, 16, 1, tzinfo=timezone.utc),
        suffix="second",
        parent_model_id=first,
    )
    registry.reject(second, reason="validation_rejected")
    rejected_model = _work_label_eligibility(registry, second)

    assert rejected_model.auto_action_eligible is False
    assert candidate_model.auto_action_eligible is False
    assert active_model.auto_action_eligible is True
