from dataclasses import dataclass
from pathlib import Path
import sqlite3

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
)
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_scan import (
    EmailScanConfig,
    scan_imap_accounts,
    scan_readonly_batch,
)
from app.email_classifier_training import CategoryEligibility
from app.email_imap_readonly import ImapUidBatch
from app.email_store import EmailStore


class FakeSource:
    def __init__(self, messages: list[dict[str, object]]):
        self.messages = messages
        self.account_id = str(messages[0]["accountId"])
        self.calls: list[tuple[str, int | None, int, int]] = []

    def fetch_uid_batch(
        self,
        mailbox: str = "INBOX",
        *,
        cursor_uidvalidity: int | None,
        last_seen_uid: int,
        limit: int = 50,
    ) -> ImapUidBatch:
        uidvalidity = int(self.messages[0]["uidValidity"])
        self.calls.append((mailbox, cursor_uidvalidity, last_seen_uid, limit))
        minimum_uid = last_seen_uid if cursor_uidvalidity == uidvalidity else 0
        return ImapUidBatch(
            account_id=str(self.messages[0]["accountId"]),
            folder=mailbox,
            uidvalidity=uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=[
                message
                for message in self.messages
                if int(message["uid"]) > minimum_uid
            ][:limit],
        )


@dataclass
class StaticPrediction:
    label: str
    probability: float
    margin: float = 0.1
    model_version: str = "static-model-v1"

    @property
    def probabilities(self) -> dict[str, float]:
        return {self.label: self.probability}


@dataclass
class StaticClassifier:
    prediction: StaticPrediction

    def predict_message(self, message):
        del message
        return self.prediction


def _category_eligibility(
    *,
    eligible: tuple[EmailCategory, ...] = (),
    threshold: float = 0.8,
) -> dict[EmailCategory, CategoryEligibility]:
    return {
        category: CategoryEligibility(
            category=category,
            configured_threshold=threshold,
            validated_precision=0.99 if category in eligible else None,
            validation_sample_count=30 if category in eligible else 0,
            auto_action_eligible=category in eligible,
            reason=(
                "precision_and_sample_gate_met"
                if category in eligible
                else "insufficient_validation_samples"
            ),
        )
        for category in EmailCategory
    }


def _message() -> dict[str, object]:
    return {
        "messageId": "<message-1@example.com>",
        "accountId": "dingtalk-account",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 1,
        "from": {"email": "team@stardust.ai"},
        "subject": "project deadline",
        "textBody": "please confirm the work sprint",
    }


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
        category_eligibility=_category_eligibility(
            eligible=tuple(EmailCategory),
            threshold=0.0,
        ),
    )

    result = scan_readonly_batch(source, classifier, store, config, limit=2)

    assert source.calls == [("INBOX", None, 0, 2)]
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
    assert store.get_scan_cursor("dingtalk-account", "INBOX") == {
        "account_id": "dingtalk-account",
        "folder": "INBOX",
        "uidvalidity": 42,
        "last_seen_uid": 2,
        "last_success_at": store.get_scan_cursor("dingtalk-account", "INBOX")[
            "last_success_at"
        ],
        "last_error": "",
    }


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
    confirmed = store.confirm_classification(
        pending[0]["id"],
        EmailCategory.IMPORTANT,
        feedback_request_id="scan-reset-feedback",
        expected_current_action_plan_id=None,
    )
    assert confirmed is not None

    for index, message in enumerate(source.messages, start=1):
        message["uidValidity"] = 84
        message["uid"] = index
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


def test_classification_failure_does_not_persist_message_or_advance_cursor(
    tmp_path: Path,
):
    class FailingClassifier:
        def predict_message(self, message):
            del message
            raise RuntimeError("model unavailable")

    store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(RuntimeError, match="model unavailable"):
        scan_readonly_batch(
            FakeSource([_message()]),
            FailingClassifier(),
            store,
            EmailScanConfig.cold_start(),
        )

    assert store.get_scan_cursor("dingtalk-account", "INBOX") is None
    with sqlite3.connect(tmp_path / "email.sqlite3") as db:
        assert db.execute("select count(*) from email_messages").fetchone()[0] == 0
        assert db.execute("select count(*) from email_classifications").fetchone()[0] == 0


@pytest.mark.parametrize(
    "failure_kind",
    ("value_error", "runtime_error", "persistence_runtime_error"),
)
def test_multi_account_scan_isolates_ordinary_folder_failures(
    tmp_path: Path,
    failure_kind: str,
):
    class FolderSource:
        def __init__(self, account_id: str):
            self.account_id = account_id

        def fetch_uid_batch(
            self,
            folder: str,
            *,
            cursor_uidvalidity: int | None,
            last_seen_uid: int,
            limit: int,
        ) -> ImapUidBatch:
            del cursor_uidvalidity, last_seen_uid, limit
            if folder == "bad" and failure_kind == "value_error":
                raise ValueError("malformed provider payload SECRET")
            return ImapUidBatch(
                account_id=self.account_id,
                folder=folder,
                uidvalidity=42,
                previous_uidvalidity=None,
                messages=[
                    {
                        "messageId": f"<{self.account_id}-{folder}@example.com>",
                        "accountId": self.account_id,
                        "folder": folder,
                        "uidValidity": 42,
                        "uid": 1,
                        "from": {"email": "sender@example.com"},
                        "subject": f"{failure_kind}-{folder}",
                        "textBody": "body",
                    }
                ],
            )

        def logout(self) -> None:
            return None

    class FolderClassifier:
        def predict_message(self, message):
            if message["folder"] == "bad" and failure_kind == "runtime_error":
                raise RuntimeError("classifier failure SECRET")
            return StaticPrediction("work", 0.61)

    class FolderStore(EmailStore):
        def persist_scan_result(self, classification, **kwargs):
            if (
                classification.provider_locator.folder == "bad"
                and failure_kind == "persistence_runtime_error"
            ):
                raise RuntimeError("persistence operation failure SECRET")
            return super().persist_scan_result(classification, **kwargs)

    store = FolderStore(tmp_path / "isolated.sqlite3")
    result = scan_imap_accounts(
        [
            {"account_id": "account-a", "enabled": True, "scan_folders": ("bad", "good")},
            {"account_id": "account-b", "enabled": True, "scan_folders": ("good",)},
        ],
        lambda account: FolderSource(str(account["account_id"])),
        FolderClassifier(),
        store,
        EmailScanConfig.cold_start(),
    )

    assert [
        (account.account_id, [(folder.folder, folder.error_code) for folder in account.folders])
        for account in result.accounts
    ] == [
        ("account-a", [("bad", "scan_failed"), ("good", "")]),
        ("account-b", [("good", "")]),
    ]
    assert "SECRET" not in repr(result)
    assert store.get_scan_cursor("account-a", "bad") is None
    assert store.get_scan_cursor("account-a", "good")["last_seen_uid"] == 1
    assert store.get_scan_cursor("account-b", "good")["last_seen_uid"] == 1


def test_high_confidence_is_pending_when_category_lacks_validation_samples(
    tmp_path: Path,
):
    eligibility = _category_eligibility()
    eligibility[EmailCategory.WORK] = CategoryEligibility(
        category=EmailCategory.WORK,
        configured_threshold=0.8,
        validated_precision=0.99,
        validation_sample_count=2,
        auto_action_eligible=False,
        reason="sample_gate_not_met",
    )
    config = EmailScanConfig(
        config_version="eligibility-samples-v1",
        thresholds={category: 0.8 for category in EmailCategory},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=eligibility,
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
    )
    store = EmailStore(tmp_path / "email.sqlite3")

    result = scan_readonly_batch(
        FakeSource([_message()]),
        StaticClassifier(StaticPrediction("work", 0.99)),
        store,
        config,
    )

    assert result.processed_count == 0
    assert result.pending_feedback_count == 1
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0]["action_plan"] is None


def test_high_confidence_is_pending_when_category_is_disabled(tmp_path: Path):
    config = EmailScanConfig(
        config_version="disabled-category-v1",
        thresholds={category: 0.8 for category in EmailCategory},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=_category_eligibility(
            eligible=(EmailCategory.WORK,)
        ),
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
        category_enabled={
            category: category is not EmailCategory.WORK
            for category in EmailCategory
        },
    )
    store = EmailStore(tmp_path / "email.sqlite3")

    result = scan_readonly_batch(
        FakeSource([_message()]),
        StaticClassifier(StaticPrediction("work", 0.99)),
        store,
        config,
    )

    assert result.processed_count == 0
    assert result.pending_feedback_count == 1
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0]["action_plan"] is None


def test_high_confidence_eligible_category_creates_action_plan(tmp_path: Path):
    config = EmailScanConfig(
        config_version="eligibility-approved-v1",
        thresholds={category: 0.8 for category in EmailCategory},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=_category_eligibility(eligible=(EmailCategory.WORK,)),
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
    )
    store = EmailStore(tmp_path / "email.sqlite3")

    result = scan_readonly_batch(
        FakeSource([_message()]),
        StaticClassifier(StaticPrediction("work", 0.99)),
        store,
        config,
    )

    assert result.processed_count == 1
    assert result.pending_feedback_count == 0
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0]["action_plan"]["actions"] == ["label"]


def test_processed_model_rescan_preserves_original_authorization_snapshot(
    tmp_path: Path,
):
    classifier = StaticClassifier(StaticPrediction("work", 0.91))
    source = FakeSource([_message()])
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig(
        config_version="plan-identity-v1",
        thresholds={category: 0.8 for category in EmailCategory},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=_category_eligibility(
            eligible=(EmailCategory.WORK,)
        ),
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
    )

    scan_readonly_batch(source, classifier, store, config)
    first_rows, _ = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    first_plan = first_rows[0]["action_plan"]

    classifier.prediction = StaticPrediction(
        "work", 0.99, model_version="static-model-v2"
    )
    source.messages[0]["uidValidity"] = 84
    source.messages[0]["uid"] = 2
    scan_readonly_batch(source, classifier, store, config)
    second_rows, _ = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    second_plan = second_rows[0]["action_plan"]

    assert first_plan["confidence"] == 0.91
    assert second_plan["confidence"] == 0.91
    assert first_plan["model_id"] == "static-model-v1"
    assert second_plan["model_id"] == "static-model-v1"
    assert first_plan["action_plan_version"] == 1
    assert second_plan["action_plan_version"] == 1
    assert first_plan["action_plan_id"] == second_plan["action_plan_id"]
    with sqlite3.connect(tmp_path / "email.sqlite3") as db:
        plan_history = db.execute(
            """
            select action_plan_id, action_plan_version
            from email_action_plans
            order by action_plan_version
            """
        ).fetchall()
        current_action_plan_id = db.execute(
            "select current_action_plan_id from email_classifications"
        ).fetchone()[0]
    assert plan_history == [(first_plan["action_plan_id"], 1)]
    assert current_action_plan_id == first_plan["action_plan_id"]
