from pathlib import Path

import pytest

from app.email_classifier_model import (
    CpuTfidfLogisticClassifier,
    email_message_to_text,
)


def _messages() -> tuple[list[dict[str, object]], list[str]]:
    return (
        [
            {
                "from": {"email": "billing@example.com"},
                "toRecipients": [],
                "subject": "发票 invoice",
                "textBody": "付款记录",
            },
            {
                "from": {"email": "team@stardust.ai"},
                "toRecipients": [],
                "subject": "项目 project",
                "textBody": "请确认本周工作安排",
            },
            {
                "from": {"email": "news@example.com"},
                "toRecipients": [],
                "subject": "newsletter",
                "textBody": "promotion offer",
            },
            {
                "from": {"email": "finance@example.com"},
                "toRecipients": [],
                "subject": "receipt receipt",
                "textBody": "payment invoice",
            },
            {
                "from": {"email": "engineering@stardust.ai"},
                "toRecipients": [],
                "subject": "work sprint",
                "textBody": "project deadline",
            },
            {
                "from": {"email": "ads@example.com"},
                "toRecipients": [],
                "subject": "marketing promotion",
                "textBody": "special offer",
            },
        ],
        ["billing", "work", "junk", "billing", "work", "junk"],
    )


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


def test_cpu_classifier_predicts_and_round_trips_model_version(tmp_path: Path):
    messages, labels = _messages()
    classifier = CpuTfidfLogisticClassifier(model_version="email-model-test-1")
    classifier.fit_messages(messages, labels)

    prediction = classifier.predict_message(messages[1])
    assert prediction.label in {"billing", "work", "junk"}
    assert 0 <= prediction.probability <= 1
    assert 0 <= prediction.margin <= 1
    assert prediction.model_version == "email-model-test-1"
    assert set(prediction.probabilities) == {"billing", "junk", "work"}

    model_path = tmp_path / "email-model.pkl"
    classifier.save(model_path)
    loaded = CpuTfidfLogisticClassifier.load(model_path)
    loaded_prediction = loaded.predict_message(messages[1])
    assert loaded.model_version == "email-model-test-1"
    assert loaded_prediction.label == prediction.label
    assert loaded_prediction.probabilities == prediction.probabilities


def test_classifier_rejects_unknown_labels_and_unfitted_prediction():
    with pytest.raises(ValueError, match="unknown category"):
        CpuTfidfLogisticClassifier().fit(["text", "other"], ["work", "unknown"])
    with pytest.raises(RuntimeError, match="not fitted"):
        CpuTfidfLogisticClassifier().predict("text")
