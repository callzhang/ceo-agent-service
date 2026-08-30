from pathlib import Path

from app.email_classifier_contracts import EmailCategory, EmailClassificationStatus
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_scan import EmailScanConfig, scan_readonly_batch
from app.email_store import EmailStore


class FakeSource:
    def __init__(self, messages: list[dict[str, object]]):
        self.messages = messages
        self.calls: list[tuple[str, int]] = []

    def fetch_recent(self, mailbox: str = "INBOX", *, limit: int = 50):
        self.calls.append((mailbox, limit))
        return self.messages


def _training_messages() -> tuple[list[dict[str, object]], list[str]]:
    return (
        [
            {"from": {"email": "billing@example.com"}, "subject": "发票 invoice", "textBody": "付款记录"},
            {"from": {"email": "team@stardust.ai"}, "subject": "项目 project", "textBody": "本周工作安排"},
            {"from": {"email": "ads@example.com"}, "subject": "marketing promotion", "textBody": "special offer"},
            {"from": {"email": "finance@example.com"}, "subject": "receipt receipt", "textBody": "payment invoice"},
            {"from": {"email": "engineering@stardust.ai"}, "subject": "work sprint", "textBody": "project deadline"},
            {"from": {"email": "news@example.com"}, "subject": "newsletter", "textBody": "promotion offer"},
        ],
        ["billing", "work", "junk", "billing", "work", "junk"],
    )


def test_cpu_model_feeds_readonly_scan_and_persists_only_classification(tmp_path: Path):
    training_messages, labels = _training_messages()
    classifier = CpuTfidfLogisticClassifier(model_version="model-integration-test")
    classifier.fit_messages(training_messages, labels)

    messages = [
        {
            "messageId": "message-1",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 1,
            "from": {"email": "team@stardust.ai"},
            "subject": "project deadline",
            "textBody": "please confirm the work sprint",
        },
        {
            "messageId": "message-2",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 2,
            "from": {"email": "ads@example.com"},
            "subject": "special offer",
            "textBody": "marketing promotion",
        },
    ]
    source = FakeSource(messages)
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig(
        config_version="scan-model-test-v1",
        thresholds={category: 0.0 for category in EmailCategory},
        actions={},
    )

    result = scan_readonly_batch(source, classifier, store, config, limit=2)

    assert source.calls == [("INBOX", 2)]
    assert result.fetched_count == result.persisted_count == 2
    assert result.pending_feedback_count == 0
    assert result.processed_count == 2
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=10, offset=0
    )
    assert total == 2
    assert {row["category"] for row in rows} == {"work", "junk"}
    assert all(row["model_id"] == "model-integration-test" for row in rows)
    assert all("https://" not in row["preview"] for row in rows)


def test_repeated_readonly_scan_is_idempotent_and_preserves_feedback(tmp_path: Path):
    training_messages, labels = _training_messages()
    classifier = CpuTfidfLogisticClassifier(model_version="model-idempotence-test")
    classifier.fit_messages(training_messages, labels)
    messages = [
        {
            "messageId": "message-1",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 1,
            "from": {"email": "team@stardust.ai"},
            "subject": "project deadline",
            "textBody": "please confirm the work sprint",
        },
        {
            "messageId": "message-2",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 2,
            "from": {"email": "ads@example.com"},
            "subject": "special offer",
            "textBody": "marketing promotion",
        },
    ]
    source = FakeSource(messages)
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig.cold_start(config_version="idempotence-v1")

    first = scan_readonly_batch(source, classifier, store, config, limit=2)
    pending, pending_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=10, offset=0
    )
    assert first.pending_feedback_count == 2
    assert pending_total == 2
    confirmed = store.confirm_classification(pending[0]["id"], EmailCategory.IMPORTANT)
    assert confirmed is not None

    second = scan_readonly_batch(source, classifier, store, config, limit=2)

    processed, processed_total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=10, offset=0
    )
    pending_after, pending_after_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=10, offset=0
    )
    assert second.persisted_count == 2
    assert processed_total == 1
    assert pending_after_total == 1
    assert processed[0]["category"] == "important"
    assert processed[0]["classification_source"] == "user"
    assert (
        pending_after[0]["stable_message_identity"]
        != processed[0]["stable_message_identity"]
    )
    assert len(store.list_training_examples()) == 1
