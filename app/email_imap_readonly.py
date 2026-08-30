"""Standard-library IMAP adapter that never changes mailbox state."""

from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import html.parser
import imaplib
import re
from collections.abc import Iterable
from typing import Any

from app.email_classifier_contracts import EmailProviderLocator


class _HTMLTextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


class ImapReadonlyAdapter:
    """Fetch normalized messages using only readonly IMAP operations."""

    def __init__(self, session: Any, *, account_id: str):
        account_id = account_id.strip()
        if not account_id:
            raise ValueError("account_id must be non-empty")
        self.session = session
        self.account_id = account_id

    @classmethod
    def connect(
        cls,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 993,
        timeout: float | None = 20.0,
        account_id: str,
    ) -> "ImapReadonlyAdapter":
        session = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        status, _ = session.login(username, password)
        if status != "OK":
            try:
                session.logout()
            finally:
                raise ConnectionError("IMAP login failed")
        return cls(session, account_id=account_id)

    def fetch_recent(self, mailbox: str = "INBOX", *, limit: int = 50) -> list[dict[str, object]]:
        mailbox = mailbox.strip()
        if not mailbox:
            raise ValueError("mailbox must be non-empty")
        if limit <= 0:
            raise ValueError("limit must be positive")
        status, _ = self.session.select(mailbox, readonly=True)
        _require_ok(status, "IMAP readonly select failed")
        uidvalidity = _uidvalidity(self.session.response("UIDVALIDITY"))
        status, data = self.session.uid("SEARCH", None, "ALL")
        _require_ok(status, "IMAP UID search failed")
        uids = _search_uids(data)[-limit:]
        messages: list[dict[str, object]] = []
        for uid in uids:
            status, fetch_data = self.session.uid("FETCH", uid, "(RFC822)")
            _require_ok(status, "IMAP UID fetch failed")
            messages.append(
                parse_rfc822_message(
                    _fetch_payload(fetch_data),
                    account_id=self.account_id,
                    folder=mailbox,
                    uidvalidity=uidvalidity,
                    uid=int(uid),
                )
            )
        return messages

    def logout(self) -> None:
        self.session.logout()

    def __enter__(self) -> "ImapReadonlyAdapter":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.logout()


def parse_rfc822_message(
    raw: bytes,
    *,
    account_id: str,
    folder: str,
    uidvalidity: int,
    uid: int,
) -> dict[str, object]:
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = _decode_header(parsed.get("Message-ID", ""))
    references = parsed.get("References") or parsed.get("In-Reply-To") or message_id
    thread_id = _decode_header(references.split()[0]) if references else None
    locator = EmailProviderLocator(
        account_id=account_id,
        folder=folder,
        uidvalidity=uidvalidity,
        uid=uid,
        rfc_message_id=message_id,
        thread_id=thread_id,
    )
    plain_parts: list[str] = []
    html_parts: list[str] = []
    has_attachment = False
    for part in _parts(parsed):
        if (part.get_content_disposition() or "").lower() == "attachment":
            has_attachment = True
            continue
        content_type = part.get_content_type().lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        text = _decode_part(part)
        if content_type == "text/plain":
            plain_parts.append(text)
        else:
            html_parts.append(_html_to_text(text))
    body = "\n".join(part for part in plain_parts if part.strip()).strip()
    if not body:
        body = "\n".join(part for part in html_parts if part.strip()).strip()
    sender_name, sender_email = _first_address(parsed.get("From", ""))
    return {
        "id": locator.stable_message_identity,
        "accountId": locator.account_id,
        "folder": locator.folder,
        "uidValidity": locator.uidvalidity,
        "uid": locator.uid,
        "messageId": locator.rfc_message_id,
        "threadId": locator.thread_id,
        "from": {"name": sender_name, "email": sender_email},
        "toRecipients": _addresses(parsed.get_all("To", [])),
        "ccRecipients": _addresses(parsed.get_all("Cc", [])),
        "subject": _decode_header(parsed.get("Subject", "")),
        "date": parsed.get("Date", ""),
        "textBody": body,
        "markdownBody": body,
        "listUnsubscribe": parsed.get("List-Unsubscribe", ""),
        "autoSubmitted": parsed.get("Auto-Submitted", ""),
        "hasAttachment": has_attachment,
    }


def _parts(message: email.message.Message) -> Iterable[email.message.Message]:
    if message.is_multipart():
        return (part for part in message.walk() if not part.is_multipart())
    return (message,)


def _decode_part(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return str(part.get_payload() or "")
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()


def _decode_header(value: object) -> str:
    try:
        return str(email.header.make_header(email.header.decode_header(str(value or ""))))
    except (LookupError, UnicodeError, ValueError):
        return str(value or "")


def _first_address(value: str) -> tuple[str, str]:
    addresses = email.utils.getaddresses([value])
    if not addresses:
        return "", ""
    name, address = addresses[0]
    return _decode_header(name), address.strip()


def _addresses(values: list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for name, address in email.utils.getaddresses(values):
        if address.strip():
            result.append({"name": _decode_header(name), "email": address.strip()})
    return result


def _require_ok(status: object, message: str) -> None:
    if str(status).upper() != "OK":
        raise ConnectionError(message)


def _search_uids(data: object) -> list[bytes]:
    if not isinstance(data, (list, tuple)) or not data or not isinstance(data[0], bytes):
        return []
    return [uid for uid in data[0].split() if uid.isdigit()]


def _fetch_payload(data: object) -> bytes:
    if not isinstance(data, (list, tuple)):
        raise ConnectionError("IMAP UID fetch returned invalid data")
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    raise ConnectionError("IMAP UID fetch returned no RFC822 payload")


def _uidvalidity(response: object) -> int:
    if not isinstance(response, (list, tuple)) or len(response) < 2:
        raise ConnectionError("IMAP select returned no UIDVALIDITY")
    values = response[1]
    if not isinstance(values, (list, tuple)) or not values:
        raise ConnectionError("IMAP select returned no UIDVALIDITY")
    raw = values[0]
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConnectionError("IMAP select returned invalid UIDVALIDITY") from exc
    if value <= 0:
        raise ConnectionError("IMAP select returned invalid UIDVALIDITY")
    return value
