from dataclasses import dataclass
import email.message
import imaplib
from pathlib import Path
from typing import Mapping

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
)
from app.email_classifier_scan import (
    EmailScanConfig,
    scan_imap_accounts,
    scan_readonly_batch,
)
from app.email_classifier_training import CategoryEligibility
from app.email_imap_readonly import (
    ImapReadonlyAdapter,
    ImapUidBatch,
    parse_rfc822_message,
)
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


def test_imap_adapter_uses_only_uid_search_strictly_after_cursor_and_uid_fetch():
    session = FakeImapSession(_raw_message())
    adapter = ImapReadonlyAdapter(session, account_id="dingtalk-account")

    batch = adapter.fetch_uid_batch(
        "INBOX",
        cursor_uidvalidity=42,
        last_seen_uid=0,
        limit=1,
    )
    messages = batch.messages

    assert batch == ImapUidBatch(
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        previous_uidvalidity=42,
        messages=messages,
    )
    assert messages[0]["messageId"] == "<message-1@example.com>"
    assert messages[0]["threadId"] == "<thread-1@example.com>"
    assert messages[0]["subject"] == "测试"
    assert messages[0]["textBody"] == "Please review this message."
    assert session.calls == [
        ("select", "INBOX", True),
        ("response", "UIDVALIDITY"),
        ("uid", "SEARCH", None, "UID 1:*"),
        ("uid", "FETCH", b"1", "(RFC822)"),
    ]
    assert not any(
        str(part).upper() in {"STORE", "COPY", "MOVE", "EXPUNGE"}
        for call in session.calls
        for part in call
    )


def test_imap_adapter_searches_after_last_seen_uid_and_resets_on_uidvalidity_change():
    same_generation = FakeImapSession(_raw_message())
    same_generation.uid = _searching_uid_method(  # type: ignore[method-assign]
        same_generation, search_result=b"8 9"
    )
    adapter = ImapReadonlyAdapter(same_generation, account_id="dingtalk-account")

    batch = adapter.fetch_uid_batch(
        "INBOX", cursor_uidvalidity=42, last_seen_uid=7, limit=10
    )

    assert [message["uid"] for message in batch.messages] == [8, 9]
    assert ("uid", "SEARCH", None, "UID 8:*") in same_generation.calls

    reset = FakeImapSession(_raw_message())
    reset.response = lambda code: (code, [b"84"])  # type: ignore[method-assign]
    reset.uid = _searching_uid_method(reset, search_result=b"1 2")  # type: ignore[method-assign]
    reset_batch = ImapReadonlyAdapter(
        reset, account_id="dingtalk-account"
    ).fetch_uid_batch(
        "INBOX", cursor_uidvalidity=42, last_seen_uid=99, limit=10
    )

    assert reset_batch.previous_uidvalidity == 42
    assert reset_batch.uidvalidity == 84
    assert [message["uid"] for message in reset_batch.messages] == [1, 2]
    assert ("uid", "SEARCH", None, "UID 1:*") in reset.calls


def _searching_uid_method(session: FakeImapSession, *, search_result: bytes):
    def uid(command: str, *args):
        session.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [search_result]
        uid_value = args[0]
        return "OK", [(f"uid {uid_value!r}".encode(), session.raw)]

    return uid


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


def _multipart_message_without_message_id() -> bytes:
    return (
        b"From: Sender <SENDER@example.com>\r\n"
        b"To: Derek <derek@example.com>, Team <team@example.com>\r\n"
        b"Cc: Ops <ops@example.com>\r\n"
        b"Subject: Re: Quarterly quote\r\n"
        b"Date: Sat, 29 Aug 2026 17:00:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=mixed\r\n\r\n"
        b"--mixed\r\n"
        b"Content-Type: multipart/alternative; boundary=alternative\r\n\r\n"
        b"--alternative\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Current reply.\r\n\r\n> Earlier question retained.\r\n"
        b"--alternative\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>Duplicate HTML alternative must not be appended.</p>\r\n"
        b"--alternative--\r\n"
        b"--mixed\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=quote.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n"
        b"QUJDREVGRw==\r\n"
        b"--mixed--\r\n"
    )


def test_parser_selects_plain_alternative_retains_thread_and_only_attachment_metadata(
    monkeypatch: pytest.MonkeyPatch,
):
    original_get_payload = email.message.Message.get_payload

    def guarded_get_payload(self, *args, **kwargs):
        decode = kwargs.get("decode", args[1] if len(args) > 1 else False)
        if self.get_content_type() == "application/pdf" and decode:
            raise AssertionError("attachment payload decoder must not be invoked")
        return original_get_payload(self, *args, **kwargs)

    monkeypatch.setattr(email.message.Message, "get_payload", guarded_get_payload)

    parsed = parse_rfc822_message(
        _multipart_message_without_message_id(),
        account_id="account-a",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
    )

    assert parsed["textBody"] == "Current reply.\n\n> Earlier question retained."
    assert "Duplicate HTML" not in str(parsed["textBody"])
    assert parsed["toRecipients"] == [
        {"name": "Derek", "email": "derek@example.com"},
        {"name": "Team", "email": "team@example.com"},
    ]
    assert parsed["ccRecipients"] == [
        {"name": "Ops", "email": "ops@example.com"}
    ]
    assert parsed["attachments"] == [
        {
            "filename": "quote.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 7,
            "inline": False,
        }
    ]
    assert "QUJDREVGRw" not in repr(parsed)
    assert parsed["stableMessageIdentity"].startswith("account-a:content-sha256:")


def test_missing_message_id_identity_is_account_scoped_and_independent_of_locator():
    raw = _multipart_message_without_message_id()

    first = parse_rfc822_message(
        raw,
        account_id="account-a",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
    )
    moved = parse_rfc822_message(
        raw,
        account_id="account-a",
        folder="Archive/2026",
        uidvalidity=84,
        uid=91,
    )
    other_account = parse_rfc822_message(
        raw,
        account_id="account-b",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
    )

    assert first["stableMessageIdentity"] == moved["stableMessageIdentity"]
    assert first["stableMessageIdentity"] != other_account["stableMessageIdentity"]


@pytest.mark.parametrize("disposition", ("attachment", "inline"))
def test_message_rfc822_container_is_one_attachment_and_never_body(
    disposition: str,
    monkeypatch: pytest.MonkeyPatch,
):
    raw = (
        b"From: sender@example.com\r\n"
        b"To: derek@example.com\r\n"
        b"Subject: Outer message\r\n"
        b"Message-ID: <outer@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
        b"--outer\r\nContent-Type: text/plain\r\n\r\nVisible outer body.\r\n"
        b"--outer\r\nContent-Type: message/rfc822\r\n"
        + f"Content-Disposition: {disposition}; filename=forwarded.eml\r\n".encode()
        + b"Content-Length: 321\r\n\r\n"
        b"From: hidden@example.com\r\nSubject: Hidden\r\n"
        b"Content-Type: text/plain\r\n\r\nSECRET NESTED BODY\r\n"
        b"--outer--\r\n"
    )
    original_get_payload = email.message.Message.get_payload

    def guarded_get_payload(self, *args, **kwargs):
        decode = kwargs.get("decode", args[1] if len(args) > 1 else False)
        if self.get_content_type() == "message/rfc822" and decode:
            raise AssertionError("attached message payload must not be decoded")
        return original_get_payload(self, *args, **kwargs)

    monkeypatch.setattr(email.message.Message, "get_payload", guarded_get_payload)

    parsed = parse_rfc822_message(
        raw,
        account_id="account-a",
        folder="INBOX",
        uidvalidity=42,
        uid=1,
    )

    assert parsed["textBody"] == "Visible outer body."
    assert "SECRET NESTED BODY" not in repr(parsed)
    assert parsed["attachments"] == [
        {
            "filename": "forwarded.eml",
            "mime_type": "message/rfc822",
            "size_bytes": 321,
            "inline": disposition == "inline",
        }
    ]


class EmptyUidSource:
    def __init__(self, account_id: str, uidvalidity: int):
        self.account_id = account_id
        self.uidvalidity = uidvalidity

    def fetch_uid_batch(
        self,
        folder: str,
        *,
        cursor_uidvalidity: int | None,
        last_seen_uid: int,
        limit: int,
    ) -> ImapUidBatch:
        del last_seen_uid, limit
        return ImapUidBatch(
            account_id=self.account_id,
            folder=folder,
            uidvalidity=self.uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=[],
        )


def test_successful_initial_and_reset_empty_scans_persist_zero_cursor(tmp_path: Path):
    store = EmailStore(tmp_path / "empty-scans.sqlite3")
    source = EmptyUidSource("account-a", 42)

    initial = scan_readonly_batch(
        source,
        FakeClassifier({}),
        store,
        EmailScanConfig.cold_start(),
    )
    assert initial.fetched_count == 0
    assert store.get_scan_cursor("account-a", "INBOX")["last_seen_uid"] == 0

    source.uidvalidity = 84
    reset = scan_readonly_batch(
        source,
        FakeClassifier({}),
        store,
        EmailScanConfig.cold_start(),
    )
    assert reset.fetched_count == 0
    cursor = store.get_scan_cursor("account-a", "INBOX")
    assert cursor["uidvalidity"] == 84
    assert cursor["last_seen_uid"] == 0


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

    uidvalidity: int = 42

    @property
    def account_id(self) -> str:
        return str(self.messages[0]["accountId"])

    def fetch_uid_batch(
        self,
        mailbox: str = "INBOX",
        *,
        cursor_uidvalidity: int | None,
        last_seen_uid: int,
        limit: int = 50,
    ):
        self.requested_mailbox = mailbox
        self.requested_limit = limit
        return ImapUidBatch(
            account_id=str(self.messages[0]["accountId"]),
            folder=mailbox,
            uidvalidity=self.uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=[
                message
                for message in self.messages
                if int(message["uid"]) > (
                    last_seen_uid if cursor_uidvalidity == self.uidvalidity else 0
                )
            ][:limit],
        )


@dataclass
class FakeClassifier:
    predictions: dict[str, FakePrediction]

    def predict_message(self, message):
        return self.predictions[str(message["messageId"])]


def _account(account_id: str, *folders: str) -> dict[str, object]:
    return {
        "account_id": account_id,
        "enabled": True,
        "scan_folders": folders,
        "imap_secret_reference": f"CEO_EMAIL_{account_id.upper().replace('-', '_')}_IMAP_SECRET",
    }


def test_multi_account_folder_scan_isolates_auth_failure_and_sanitizes_result(
    tmp_path: Path,
):
    messages = {
        ("account-a", "INBOX"): [
            {
                "messageId": "message-a-inbox",
                "accountId": "account-a",
                "folder": "INBOX",
                "uidValidity": 1,
                "uid": 1,
                "from": {"email": "sender@example.com"},
                "toRecipients": [{"email": "derek@example.com"}],
                "subject": "Inbox",
                "textBody": "One",
                "attachments": [],
            }
        ],
        ("account-a", "Archive"): [
            {
                "messageId": "message-a-archive",
                "accountId": "account-a",
                "folder": "Archive",
                "uidValidity": 2,
                "uid": 3,
                "from": {"email": "sender@example.com"},
                "subject": "Archive",
                "textBody": "Two",
                "attachments": [],
            }
        ],
    }

    class AccountSource:
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
            del last_seen_uid, limit
            rows = messages[(self.account_id, folder)]
            return ImapUidBatch(
                account_id=self.account_id,
                folder=folder,
                uidvalidity=int(rows[0]["uidValidity"]),
                previous_uidvalidity=cursor_uidvalidity,
                messages=rows,
            )

        def logout(self) -> None:
            return None

    def source_factory(account: Mapping[str, object]):
        if account["account_id"] == "account-b":
            raise imaplib.IMAP4.error("password=DO-NOT-RETURN")
        return AccountSource(str(account["account_id"]))

    classifier = FakeClassifier(
        {
            "message-a-inbox": FakePrediction(
                "work", 0.61, 0.03, {"work": 0.61}
            ),
            "message-a-archive": FakePrediction(
                "work", 0.61, 0.03, {"work": 0.61}
            ),
        }
    )
    store = EmailStore(tmp_path / "worker.sqlite3")

    result = scan_imap_accounts(
        [_account("account-a", "INBOX", "Archive"), _account("account-b", "INBOX")],
        source_factory,
        classifier,
        store,
        EmailScanConfig.cold_start(),
    )

    assert result.persisted_count == 2
    assert [(item.account_id, item.error_code) for item in result.accounts] == [
        ("account-a", ""),
        ("account-b", "connection_failed"),
    ]
    assert [folder.folder for folder in result.accounts[0].folders] == [
        "INBOX",
        "Archive",
    ]
    assert "DO-NOT-RETURN" not in repr(result)
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert total == 2
    assert {row["account_id"] for row in rows} == {"account-a"}


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
                "folder": "Archive",
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
                "folder": "Archive",
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
    source.uidvalidity = 84
    scan_readonly_batch(
        source,
        classifier,
        store,
        EmailScanConfig.cold_start(),
        mailbox="Archive/2026",
    )

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


def test_uid_search_is_sorted_deduplicated_before_limit_and_cursor_paging(
    tmp_path: Path,
):
    class PagingSession:
        def __init__(self):
            self.calls: list[tuple[object, ...]] = []
            self.search_results = iter((b"12 8 9 8", b"12"))

        def select(self, mailbox: str, readonly: bool = False):
            self.calls.append(("select", mailbox, readonly))
            return "OK", [b"4"]

        def response(self, code: str):
            self.calls.append(("response", code))
            return code, [b"42"]

        def uid(self, command: str, *args):
            self.calls.append(("uid", command, *args))
            if command == "SEARCH":
                return "OK", [next(self.search_results)]
            uid = int(args[0])
            raw = (
                b"From: sender@example.com\r\n"
                b"To: derek@example.com\r\n"
                + f"Subject: message {uid}\r\n".encode()
                + f"Message-ID: <message-{uid}@example.com>\r\n".encode()
                + b"Content-Type: text/plain\r\n\r\nbody\r\n"
            )
            return "OK", [(b"header", raw)]

        def logout(self):
            return "BYE", []

    class AnyClassifier:
        def predict_message(self, message):
            del message
            return FakePrediction("work", 0.61, 0.03, {"work": 0.61})

    session = PagingSession()
    adapter = ImapReadonlyAdapter(session, account_id="account-a")
    store = EmailStore(tmp_path / "paging.sqlite3")

    first = scan_readonly_batch(
        adapter,
        AnyClassifier(),
        store,
        EmailScanConfig.cold_start(),
        limit=2,
    )

    assert first.fetched_count == 2
    assert [
        call[2]
        for call in session.calls
        if call[:2] == ("uid", "FETCH")
    ] == [b"8", b"9"]
    assert store.get_scan_cursor("account-a", "INBOX")["last_seen_uid"] == 9

    second = scan_readonly_batch(
        adapter,
        AnyClassifier(),
        store,
        EmailScanConfig.cold_start(),
        limit=2,
    )

    assert second.fetched_count == 1
    searches = [call for call in session.calls if call[:2] == ("uid", "SEARCH")]
    assert searches[-1] == ("uid", "SEARCH", None, "UID 10:*")
    assert [
        call[2]
        for call in session.calls
        if call[:2] == ("uid", "FETCH")
    ] == [b"8", b"9", b"12"]
    assert store.get_scan_cursor("account-a", "INBOX")["last_seen_uid"] == 12
