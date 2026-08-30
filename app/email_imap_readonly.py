"""Standard-library IMAP adapter that never changes mailbox state."""

from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import html.parser
import imaplib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
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


@dataclass(frozen=True)
class ImapUidBatch:
    """One readonly folder observation and the UID generation it belongs to."""

    account_id: str
    folder: str
    uidvalidity: int
    previous_uidvalidity: int | None
    messages: list[dict[str, object]]


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
        try:
            status, _ = session.login(username, password)
        except imaplib.IMAP4.error as exc:
            _close_imap_session(session)
            raise ConnectionError("IMAP login failed") from exc
        if status != "OK":
            _close_imap_session(session)
            raise ConnectionError("IMAP login failed")
        return cls(session, account_id=account_id)

    def fetch_uid_batch(
        self,
        mailbox: str = "INBOX",
        *,
        cursor_uidvalidity: int | None,
        last_seen_uid: int,
        limit: int = 50,
    ) -> ImapUidBatch:
        mailbox = mailbox.strip()
        if not mailbox:
            raise ValueError("mailbox must be non-empty")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if cursor_uidvalidity is not None and cursor_uidvalidity <= 0:
            raise ValueError("cursor_uidvalidity must be positive")
        if isinstance(last_seen_uid, bool) or last_seen_uid < 0:
            raise ValueError("last_seen_uid must be a non-negative integer")
        status, _ = self.session.select(mailbox, readonly=True)
        _require_ok(status, "IMAP readonly select failed")
        uidvalidity = _uidvalidity(self.session.response("UIDVALIDITY"))
        search_after = last_seen_uid if cursor_uidvalidity == uidvalidity else 0
        first_uid = search_after + 1
        status, data = self.session.uid("SEARCH", None, f"UID {first_uid}:*")
        _require_ok(status, "IMAP UID search failed")
        uids = [uid for uid in _search_uids(data) if int(uid) >= first_uid][:limit]
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
        return ImapUidBatch(
            account_id=self.account_id,
            folder=mailbox,
            uidvalidity=uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=messages,
        )

    def fetch_recent(
        self, mailbox: str = "INBOX", *, limit: int = 50
    ) -> list[dict[str, object]]:
        """Fetch from UID 1 for diagnostics that do not own a durable cursor."""

        return self.fetch_uid_batch(
            mailbox,
            cursor_uidvalidity=None,
            last_seen_uid=0,
            limit=limit,
        ).messages

    def logout(self) -> None:
        _close_imap_session(self.session)

    def __enter__(self) -> "ImapReadonlyAdapter":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.logout()


def _close_imap_session(session: Any) -> None:
    try:
        session.logout()
    except Exception:
        shutdown = getattr(session, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass


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
    thread_id = _thread_identity(str(references or ""), account_id=account_id)
    locator = EmailProviderLocator(
        account_id=account_id,
        folder=folder,
        uidvalidity=uidvalidity,
        uid=uid,
        rfc_message_id=message_id,
        thread_id=thread_id,
    )
    body = _message_body(parsed)
    attachments = _attachment_metadata(parsed)
    sender_name, sender_email = _first_address(parsed.get("From", ""))
    to_recipients = _addresses(parsed.get_all("To", []))
    cc_recipients = _addresses(parsed.get_all("Cc", []))
    subject = _decode_header(parsed.get("Subject", ""))
    date = str(parsed.get("Date", ""))
    stable_identity = locator.stable_message_identity
    if locator.rfc_message_id is None:
        stable_identity = fallback_stable_message_identity(
            {
                "from": {"name": sender_name, "email": sender_email},
                "toRecipients": to_recipients,
                "ccRecipients": cc_recipients,
                "subject": subject,
                "date": date,
                "textBody": body,
            },
            account_id=locator.account_id,
        )
    return {
        "id": stable_identity,
        "stableMessageIdentity": stable_identity,
        "accountId": locator.account_id,
        "folder": locator.folder,
        "uidValidity": locator.uidvalidity,
        "uid": locator.uid,
        "messageId": locator.rfc_message_id,
        "threadId": locator.thread_id,
        "from": {"name": sender_name, "email": sender_email},
        "toRecipients": to_recipients,
        "ccRecipients": cc_recipients,
        "subject": subject,
        "date": date,
        "textBody": body,
        "markdownBody": body,
        "listUnsubscribe": parsed.get("List-Unsubscribe", ""),
        "autoSubmitted": parsed.get("Auto-Submitted", ""),
        "hasAttachment": bool(attachments),
        "attachments": attachments,
    }


def _parts(message: email.message.Message) -> Iterable[email.message.Message]:
    if message.is_multipart():
        return (part for part in message.walk() if not part.is_multipart())
    return (message,)


def _message_body(message: email.message.Message) -> str:
    if _is_attachment(message):
        return ""
    if not message.is_multipart():
        content_type = message.get_content_type().lower()
        if content_type == "text/plain":
            return _decode_part(message).strip()
        if content_type == "text/html":
            return _html_to_text(_decode_part(message))
        return ""
    children = list(message.iter_parts())
    if message.get_content_subtype().lower() == "alternative":
        for preferred_type in ("text/plain", "text/html"):
            for child in children:
                if child.get_content_type().lower() != preferred_type:
                    continue
                text = _message_body(child).strip()
                if text:
                    return text
        return ""
    return "\n".join(
        text
        for child in children
        if (text := _message_body(child).strip())
    ).strip()


def _attachment_metadata(message: email.message.Message) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []

    def visit(part: email.message.Message) -> None:
        if not _is_attachment(part):
            if part.is_multipart():
                for child in part.iter_parts():
                    visit(child)
            return
        disposition = (part.get_content_disposition() or "").lower()
        result.append(
            {
                "filename": _decode_header(part.get_filename() or ""),
                "mime_type": part.get_content_type().lower(),
                "size_bytes": _encoded_payload_size(part),
                "inline": disposition == "inline",
            }
        )

    visit(message)
    return result


def _is_attachment(part: email.message.Message) -> bool:
    disposition = (part.get_content_disposition() or "").lower()
    return disposition in {"attachment", "inline"} or bool(part.get_filename())


def _encoded_payload_size(part: email.message.Message) -> int:
    content_length = part.get("Content-Length")
    if content_length is not None:
        try:
            parsed_length = int(str(content_length))
        except ValueError:
            parsed_length = -1
        if parsed_length >= 0:
            return parsed_length
    payload = part.get_payload(decode=False)
    if payload is None:
        return 0
    if isinstance(payload, bytes):
        return len(payload)
    text = str(payload)
    transfer_encoding = str(part.get("Content-Transfer-Encoding", "")).lower()
    if transfer_encoding == "base64":
        length = 0
        tail = ""
        for character in text:
            if character.isspace():
                continue
            length += 1
            tail = (tail + character)[-2:]
        padding = len(tail) - len(tail.rstrip("="))
        return max(0, (length // 4) * 3 - padding)
    if transfer_encoding == "quoted-printable":
        return _quoted_printable_size(text)
    return sum(len(character.encode("utf-8")) for character in text)


def _quoted_printable_size(value: str) -> int:
    size = 0
    index = 0
    while index < len(value):
        if value[index] == "=" and index + 1 < len(value):
            if value[index + 1] == "\n":
                index += 2
                continue
            if value[index + 1 : index + 3] == "\r\n":
                index += 3
                continue
            if re.fullmatch(r"[0-9A-Fa-f]{2}", value[index + 1 : index + 3]):
                size += 1
                index += 3
                continue
        size += len(value[index].encode("utf-8"))
        index += 1
    return size


def fallback_stable_message_identity(
    message: Mapping[str, object],
    *,
    account_id: str,
) -> str:
    """Build a folder/UID-independent identity when RFC Message-ID is absent."""

    sender_value = message.get("from") or {}
    sender = dict(sender_value) if isinstance(sender_value, Mapping) else {}
    recipients: list[dict[str, str]] = []
    for field in ("toRecipients", "ccRecipients"):
        values = message.get(field) or ()
        if isinstance(values, Iterable) and not isinstance(values, str | bytes):
            recipients.extend(dict(item) for item in values if isinstance(item, Mapping))
    canonical = json.dumps(
        {
            "sender": _canonical_address(sender),
            "recipients": sorted({_canonical_address(item) for item in recipients}),
            "subject": _canonical_text(str(message.get("subject") or "")),
            "date": _canonical_date(
                str(message.get("date") or message.get("received_at") or "")
            ),
            "body": _canonical_text(
                str(message.get("markdownBody") or message.get("textBody") or "")
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    return f"{account_id}:content-sha256:{digest}"


def _canonical_address(value: dict[str, str]) -> str:
    address = value.get("email", "").strip().casefold()
    return address or _canonical_text(value.get("name", "")).casefold()


def _canonical_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _canonical_date(value: str) -> str:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        return _canonical_text(value)
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone(timezone.utc).isoformat()


def _thread_identity(value: str, *, account_id: str) -> str | None:
    match = re.search(r"<[^<>\s]+@[^<>\s]+>", value)
    if match is None:
        return _decode_header(value).strip() or None
    return EmailProviderLocator(
        account_id=account_id,
        folder="thread",
        uidvalidity=1,
        uid=1,
        rfc_message_id=match.group(0),
    ).rfc_message_id


def _decode_part(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        value = str(part.get_payload() or "")
    else:
        value = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return value.replace("\r\n", "\n").replace("\r", "\n")


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
