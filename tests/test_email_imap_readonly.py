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


_HEADER_FETCH = (
    "(BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID REFERENCES "
    "IN-REPLY-TO LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST AUTO-SUBMITTED)])"
)


class FakeImapSession:
    def __init__(
        self,
        *,
        headers: bytes,
        bodystructure: bytes,
        section_payloads: Mapping[str, bytes],
        search_result: bytes = b"1",
        uidvalidity: int = 42,
    ):
        self.headers = headers
        self.bodystructure = bodystructure
        self.section_payloads = dict(section_payloads)
        self.search_result = search_result
        self.uidvalidity = uidvalidity
        self.calls: list[tuple[object, ...]] = []

    def select(self, mailbox: str, readonly: bool = False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command: str, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [self.search_result]
        if command != "FETCH":
            raise AssertionError(f"mailbox write attempted: {command}")
        uid, query = args
        if query == "(BODYSTRUCTURE)":
            return "OK", [
                b"1 (UID " + bytes(uid) + b" BODYSTRUCTURE " + self.bodystructure + b")"
            ]
        if query == _HEADER_FETCH:
            return "OK", [
                (
                    b"1 (UID "
                    + bytes(uid)
                    + b" BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID "
                    b"REFERENCES IN-REPLY-TO LIST-UNSUBSCRIBE "
                    b"LIST-UNSUBSCRIBE-POST AUTO-SUBMITTED)] {"
                    + str(len(self.headers)).encode("ascii")
                    + b"}",
                    self.headers,
                ),
                b")",
            ]
        if isinstance(query, str) and query.startswith("(BODY.PEEK["):
            section = query.removeprefix("(BODY.PEEK[").split("]", 1)[0]
            payload = self.section_payloads[section]
            return "OK", [
                (
                    b"1 (UID "
                    + bytes(uid)
                    + b" BODY["
                    + section.encode("ascii")
                    + b"]<0> {"
                    + str(len(payload)).encode("ascii")
                    + b"}",
                    payload,
                ),
                b")",
            ]
        raise AssertionError(f"forbidden whole-message fetch: {query}")

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", []

    def response(self, code: str):
        self.calls.append(("response", code))
        return code, [str(self.uidvalidity).encode("ascii")]


def _raw_message() -> bytes:
    return (
        b"From: Sender =?utf-8?b?5L2Z5a6a?= <sender@example.com>\r\n"
        b"To: Derek <derek@example.com>\r\n"
        b"Subject: =?utf-8?b?5rWL6K+V?=\r\n"
        b"Message-ID: <message-1@example.com>\r\n"
        b"References: <thread-1@example.com>\r\n"
        b"List-Unsubscribe: <https://example.com/unsubscribe>\r\n"
        b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Please review this message.\r\n"
    )


def _raw_headers(raw: bytes) -> bytes:
    return raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"


def _plain_session(
    *, search_result: bytes = b"1", uidvalidity: int = 42
) -> FakeImapSession:
    raw = _raw_message()
    return FakeImapSession(
        headers=_raw_headers(raw),
        bodystructure=(
            b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 29 1 NIL NIL NIL NIL)'
        ),
        section_payloads={"1": raw.split(b"\r\n\r\n", 1)[1]},
        search_result=search_result,
        uidvalidity=uidvalidity,
    )


def test_imap_adapter_fetches_only_headers_bodystructure_and_bounded_text_section():
    session = _plain_session()
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
    assert messages[0] == parse_rfc822_message(
        _raw_message(),
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        uid=1,
    )
    assert session.calls == [
        ("select", "INBOX", True),
        ("response", "UIDVALIDITY"),
        ("uid", "SEARCH", None, "UID 1:*"),
        ("uid", "FETCH", b"1", "(BODYSTRUCTURE)"),
        ("uid", "FETCH", b"1", _HEADER_FETCH),
        ("uid", "FETCH", b"1", "(BODY.PEEK[1]<0.65536>)"),
    ]
    assert {call[1] for call in session.calls if call[0] == "uid"} == {
        "SEARCH",
        "FETCH",
    }


def test_imap_adapter_searches_after_last_seen_uid_and_resets_on_uidvalidity_change():
    same_generation = _plain_session(search_result=b"8 9")
    adapter = ImapReadonlyAdapter(same_generation, account_id="dingtalk-account")

    batch = adapter.fetch_uid_batch(
        "INBOX", cursor_uidvalidity=42, last_seen_uid=7, limit=10
    )

    assert [message["uid"] for message in batch.messages] == [8, 9]
    assert ("uid", "SEARCH", None, "UID 8:*") in same_generation.calls

    reset = _plain_session(search_result=b"1 2", uidvalidity=84)
    reset_batch = ImapReadonlyAdapter(
        reset, account_id="dingtalk-account"
    ).fetch_uid_batch("INBOX", cursor_uidvalidity=42, last_seen_uid=99, limit=10)

    assert reset_batch.previous_uidvalidity == 42
    assert reset_batch.uidvalidity == 84
    assert [message["uid"] for message in reset_batch.messages] == [1, 2]
    assert ("uid", "SEARCH", None, "UID 1:*") in reset.calls


def test_imap_adapter_prefers_plain_alternative_and_never_fetches_attachments():
    raw = _multipart_message_without_message_id()
    session = FakeImapSession(
        headers=_raw_headers(raw),
        bodystructure=(
            b'((("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL '
            b'"QUOTED-PRINTABLE" 50 3 '
            b'NIL NIL NIL NIL)("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL '
            b'"7BIT" 63 1 NIL NIL NIL NIL) "ALTERNATIVE" ("BOUNDARY" '
            b'"alternative") NIL NIL NIL)("APPLICATION" "PDF" ("NAME" '
            b'"quote.pdf") NIL NIL "BASE64" 7 NIL ("ATTACHMENT" '
            b'("FILENAME" "quote.pdf")) NIL NIL)("IMAGE" "PNG" ("NAME" '
            b'"logo.png") NIL NIL "BASE64" 4 NIL ("INLINE" ("FILENAME" '
            b'"logo.png")) NIL NIL) "MIXED" ("BOUNDARY" "mixed") NIL NIL NIL)'
        ),
        section_payloads={
            "1.1": b"Current=20reply.\r\n\r\n> Earlier question retained.\r\n",
            "1.2": b"<p>Duplicate HTML alternative must not be appended.</p>\r\n",
            "2": b"SENTINEL-ATTACHMENT-CONTENT",
            "3": b"SENTINEL-INLINE-CONTENT",
        },
    )

    batch = ImapReadonlyAdapter(session, account_id="account-a").fetch_uid_batch(
        "INBOX", cursor_uidvalidity=42, last_seen_uid=0, limit=1
    )

    assert batch.messages[0] == parse_rfc822_message(
        raw,
        account_id="account-a",
        folder="INBOX",
        uidvalidity=42,
        uid=1,
    )
    assert batch.messages[0]["attachments"] == [
        {
            "filename": "quote.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 7,
            "inline": False,
        },
        {
            "filename": "logo.png",
            "mime_type": "image/png",
            "size_bytes": 4,
            "inline": True,
        },
    ]
    assert [call for call in session.calls if call[:2] == ("uid", "FETCH")] == [
        ("uid", "FETCH", b"1", "(BODYSTRUCTURE)"),
        ("uid", "FETCH", b"1", _HEADER_FETCH),
        ("uid", "FETCH", b"1", "(BODY.PEEK[1.1]<0.65536>)"),
    ]
    assert "SENTINEL-ATTACHMENT-CONTENT" not in repr(batch)
    assert "SENTINEL-INLINE-CONTENT" not in repr(batch)


def test_imap_adapter_sanitizes_html_when_plain_alternative_is_absent():
    raw = (
        b"From: sender@example.com\r\n"
        b"To: derek@example.com\r\n"
        b"Subject: HTML only\r\n"
        b"Message-ID: <html@example.com>\r\n\r\n"
    )
    session = FakeImapSession(
        headers=raw,
        bodystructure=(
            b'(("TEXT" "CALENDAR" ("CHARSET" "UTF-8") NIL NIL "7BIT" 20 1 '
            b'NIL NIL NIL NIL)("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL '
            b'"BASE64" 52 1 NIL NIL NIL NIL) "ALTERNATIVE" ("BOUNDARY" "alt") '
            b"NIL NIL NIL)"
        ),
        section_payloads={
            "1": b"BEGIN:VCALENDAR",
            "2": b"PHA+SGVsbG8gPHN0cm9uZz53b3JsZDwvc3Ryb25nPjwvcD4=",
        },
    )

    message = (
        ImapReadonlyAdapter(session, account_id="account-a")
        .fetch_uid_batch("INBOX", cursor_uidvalidity=42, last_seen_uid=0, limit=1)
        .messages[0]
    )

    assert message["textBody"] == "Hello world"
    assert ("uid", "FETCH", b"1", "(BODY.PEEK[2]<0.65536>)") in session.calls
    assert not any(
        call == ("uid", "FETCH", b"1", "(BODY.PEEK[1]<0.65536>)")
        for call in session.calls
    )


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
    assert parsed["listUnsubscribePost"] == "List-Unsubscribe=One-Click"
    assert "sender@example.com" == parsed["from"]["email"]
    assert parsed["accountId"] == "dingtalk-account"
    assert parsed["folder"] == "INBOX"
    assert parsed["uidValidity"] == 42
    assert parsed["uid"] == 1


def test_imap_adapter_truncates_a_server_response_that_exceeds_requested_range():
    session = _plain_session()
    session.section_payloads["1"] = b"x" * (64 * 1024 + 4096)

    message = (
        ImapReadonlyAdapter(session, account_id="account-a")
        .fetch_uid_batch("INBOX", cursor_uidvalidity=42, last_seen_uid=0, limit=1)
        .messages[0]
    )

    assert len(message["textBody"]) == 64 * 1024
    assert message["textBody"] == "x" * (64 * 1024)


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
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        b"Current=20reply.\r\n\r\n> Earlier question retained.\r\n"
        b"--alternative\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>Duplicate HTML alternative must not be appended.</p>\r\n"
        b"--alternative--\r\n"
        b"--mixed\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=quote.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n"
        b"QUJDREVGRw==\r\n"
        b"--mixed\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: inline; filename=logo.png\r\n"
        b"Content-Length: 4\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n"
        b"SU1H\r\n"
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
    assert parsed["ccRecipients"] == [{"name": "Ops", "email": "ops@example.com"}]
    assert parsed["attachments"] == [
        {
            "filename": "quote.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 7,
            "inline": False,
        },
        {
            "filename": "logo.png",
            "mime_type": "image/png",
            "size_bytes": 4,
            "inline": True,
        },
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
                if int(message["uid"])
                > (last_seen_uid if cursor_uidvalidity == self.uidvalidity else 0)
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
            "message-a-inbox": FakePrediction("work", 0.61, 0.03, {"work": 0.61}),
            "message-a-archive": FakePrediction("work", 0.61, 0.03, {"work": 0.61}),
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
            "message-2": FakePrediction(
                "subscription", 0.61, 0.03, {"subscription": 0.61}
            ),
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
            query = args[1]
            headers = (
                b"From: sender@example.com\r\n"
                b"To: derek@example.com\r\n"
                + f"Subject: message {uid}\r\n".encode()
                + f"Message-ID: <message-{uid}@example.com>\r\n".encode()
                + b"\r\n"
            )
            if query == "(BODYSTRUCTURE)":
                return "OK", [
                    b'1 (UID 1 BODYSTRUCTURE ("TEXT" "PLAIN" '
                    b'("CHARSET" "UTF-8") NIL NIL "7BIT" 6 1 NIL NIL NIL NIL))'
                ]
            if query == _HEADER_FETCH:
                return "OK", [(b"header", headers), b")"]
            if query == "(BODY.PEEK[1]<0.65536>)":
                return "OK", [(b"body", b"body\r\n"), b")"]
            raise AssertionError(f"unexpected fetch query: {query}")

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
        if call[:2] == ("uid", "FETCH") and call[3] == "(BODYSTRUCTURE)"
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
        if call[:2] == ("uid", "FETCH") and call[3] == "(BODYSTRUCTURE)"
    ] == [b"8", b"9", b"12"]
    assert store.get_scan_cursor("account-a", "INBOX")["last_seen_uid"] == 12
