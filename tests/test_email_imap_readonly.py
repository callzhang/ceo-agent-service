from dataclasses import dataclass
from pathlib import Path

import pytest

from app.email_classifier_contracts import EmailAction, EmailCategory, EmailClassificationStatus
from app.email_classifier_scan import EmailScanConfig, scan_readonly_batch
from app.email_classifier_training import CategoryEligibility
from app.email_imap_readonly import ImapReadonlyAdapter, parse_rfc822_message
from app.email_store import EmailStore


class FakeImapSession:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.calls: list[tuple[object, ...]] = []

    def select(self, mailbox: str, readonly: bool = False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command: str, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"1"]
        return "OK", [(b"header", self.raw)]

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", []

    def response(self, code: str):
        self.calls.append(("response", code))
        return code, [b"42"]


def _raw_message() -> bytes:
    return (
        b"From: Sender =?utf-8?b?5L2Z5a6a?= <sender@example.com>\r\n"
        b"To: Derek <derek@example.com>\r\n"
        b"Subject: =?utf-8?b?5rWL6K+V?=\r\n"
        b"Message-ID: <message-1@example.com>\r\n"
        b"References: <thread-1@example.com>\r\n"
        b"List-Unsubscribe: <https://example.com/unsubscribe>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Please review this message.\r\n"
    )


def test_imap_adapter_uses_only_readonly_select_search_and_fetch():
    session = FakeImapSession(_raw_message())
    adapter = ImapReadonlyAdapter(session, account_id="dingtalk-account")

    messages = adapter.fetch_recent("INBOX", limit=1)

    assert messages[0]["messageId"] == "<message-1@example.com>"
    assert messages[0]["threadId"] == "<thread-1@example.com>"
    assert messages[0]["subject"] == "测试"
    assert messages[0]["textBody"] == "Please review this message."
    assert session.calls == [
        ("select", "INBOX", True),
        ("response", "UIDVALIDITY"),
        ("uid", "SEARCH", None, "ALL"),
        ("uid", "FETCH", b"1", "(RFC822)"),
    ]


def test_rfc822_parser_does_not_return_raw_headers_or_attachment_payload():
    parsed = parse_rfc822_message(
        _raw_message(),
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        uid=1,
    )

    assert "raw" not in parsed
    assert parsed["listUnsubscribe"] == "<https://example.com/unsubscribe>"
    assert "sender@example.com" == parsed["from"]["email"]
    assert parsed["accountId"] == "dingtalk-account"
    assert parsed["folder"] == "INBOX"
    assert parsed["uidValidity"] == 42
    assert parsed["uid"] == 1


@dataclass(frozen=True)
class FakePrediction:
    label: str
    probability: float
    margin: float
    probabilities: dict[str, float]
    model_version: str = "model-test"


@dataclass
class FakeSource:
    messages: list[dict[str, object]]
    requested_mailbox: str = ""
    requested_limit: int = 0

    def fetch_recent(self, mailbox: str = "INBOX", *, limit: int = 50):
        self.requested_mailbox = mailbox
        self.requested_limit = limit
        return self.messages


@dataclass
class FakeClassifier:
    predictions: dict[str, FakePrediction]

    def predict_message(self, message):
        return self.predictions[str(message["messageId"])]


def _scan_config() -> EmailScanConfig:
    return EmailScanConfig(
        config_version="email-scan-v1",
        thresholds={category: 0.8 for category in EmailCategory},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility={
            category: CategoryEligibility(
                category=category,
                configured_threshold=0.8,
                validated_precision=(0.99 if category is EmailCategory.WORK else None),
                validation_sample_count=(30 if category is EmailCategory.WORK else 0),
                auto_action_eligible=category is EmailCategory.WORK,
                reason=(
                    "precision_and_sample_gate_met"
                    if category is EmailCategory.WORK
                    else "insufficient_validation_samples"
                ),
            )
            for category in EmailCategory
        },
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
    )


def test_cold_start_scan_config_has_no_external_actions():
    config = EmailScanConfig.cold_start()

    assert all(value == 0.95 for value in config.thresholds.values())
    assert config.actions == {}


def test_scan_persists_processed_or_pending_without_mailbox_actions(tmp_path: Path):
    source = FakeSource(
        messages=[
            {
                "messageId": "message-1",
                "accountId": "dingtalk-account",
                "folder": "INBOX",
                "uidValidity": 42,
                "uid": 1,
                "threadId": "thread-1",
                "from": {"email": "sender@example.com"},
                "subject": "Work request",
                "textBody": "secret code 123456 and https://private.example/a",
            },
            {
                "messageId": "message-2",
                "accountId": "dingtalk-account",
                "folder": "INBOX",
                "uidValidity": 42,
                "uid": 2,
                "from": {"email": "news@example.com"},
                "subject": "Newsletter",
                "textBody": "Please decide",
            },
        ]
    )
    classifier = FakeClassifier(
        {
            "message-1": FakePrediction("work", 0.95, 0.4, {"work": 0.95}),
            "message-2": FakePrediction("subscription", 0.61, 0.03, {"subscription": 0.61}),
        }
    )
    store = EmailStore(tmp_path / "worker.sqlite3")

    result = scan_readonly_batch(
        source, classifier, store, _scan_config(), mailbox="Archive", limit=2
    )

    assert source.requested_mailbox == "Archive"
    assert source.requested_limit == 2
    assert result == type(result)(2, 2, 1, 1)
    processed, processed_total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=20, offset=0
    )
    pending, pending_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=20, offset=0
    )
    assert processed_total == 1
    assert processed[0]["action_plan"] is not None
    assert processed[0]["action_plan"]["actions"] == ["label"]
    assert pending_total == 1
    assert pending[0]["action_plan"] is None
    assert "https://" not in pending[0]["preview"]
    assert "123456" not in pending[0]["preview"]


def test_scan_preserves_business_identity_when_provider_locator_moves(tmp_path: Path):
    source = FakeSource(
        messages=[
            {
                "messageId": None,
                "accountId": "dingtalk-account",
                "folder": "INBOX",
                "uidValidity": 42,
                "uid": 1,
                "from": {"email": "sender@example.com"},
                "subject": "Move me",
                "textBody": "Stable identity",
            }
        ]
    )
    classifier = FakeClassifier(
        {"None": FakePrediction("work", 0.61, 0.03, {"work": 0.61})}
    )
    store = EmailStore(tmp_path / "worker.sqlite3")

    scan_readonly_batch(source, classifier, store, EmailScanConfig.cold_start())
    first, first_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert first_total == 1
    stable_identity = first[0]["stable_message_identity"]

    source.messages = [
        {
            "messageId": None,
            "stableMessageIdentity": stable_identity,
            "accountId": "dingtalk-account",
            "folder": "Archive/2026",
            "uidValidity": 84,
            "uid": 91,
            "from": {"email": "sender@example.com"},
            "subject": "Move me",
            "textBody": "Stable identity",
        }
    ]
    scan_readonly_batch(source, classifier, store, EmailScanConfig.cold_start())

    moved, moved_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert moved_total == 1
    assert moved[0]["stable_message_identity"] == stable_identity
    assert moved[0]["folder"] == "Archive/2026"
    assert moved[0]["uidvalidity"] == 84
    assert moved[0]["uid"] == 91


@pytest.mark.parametrize(
    ("field", "value"),
    (("uidValidity", 42.5), ("uid", 1.5)),
)
def test_scan_rejects_non_integral_provider_coordinates(
    tmp_path: Path,
    field: str,
    value: float,
):
    message: dict[str, object] = {
        "messageId": "<message-float@example.com>",
        "accountId": "dingtalk-account",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 1,
        "from": {"email": "sender@example.com"},
        "subject": "Invalid coordinates",
        "textBody": "Reject truncation",
    }
    message[field] = value
    source = FakeSource(messages=[message])
    classifier = FakeClassifier(
        {
            "<message-float@example.com>": FakePrediction(
                "work", 0.61, 0.03, {"work": 0.61}
            )
        }
    )

    with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
        scan_readonly_batch(
            source,
            classifier,
            EmailStore(tmp_path / "worker.sqlite3"),
            EmailScanConfig.cold_start(),
        )
