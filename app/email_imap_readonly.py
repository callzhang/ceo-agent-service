"""Standard-library IMAP adapter that never changes mailbox state."""

from __future__ import annotations

import base64
import binascii
import email
import email.header
import email.policy
import email.utils
import html.parser
import imaplib
import json
import quopri
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
from typing import Any

from app.email_classifier_contracts import EmailProviderLocator
from app.email_imap_folders import ImapFolderListError, parse_imap_list_response
from app.email_imap_mailbox import (
    ImapMailboxCodecError,
    encode_imap_mailbox_argument,
    parse_imap_fetch_flags,
)
from app.email_important import ImportantSignals, normalize_important_signals
from app.email_provider_folders import ProviderFolder
from app.email_unsubscribe import UnsubscribeAuthenticationEvidence


_HEADER_FETCH = (
    "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID REFERENCES "
    "IN-REPLY-TO LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST AUTO-SUBMITTED)])"
)
_MAX_TEXT_FETCH_BYTES = 64 * 1024


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


@dataclass(frozen=True)
class ImapUidMembership:
    """Bounded membership result without downloading message content."""

    uidvalidity: int
    existing_uids: frozenset[int]
    important_signals_by_uid: Mapping[int, ImportantSignals]


@dataclass(frozen=True)
class ProviderFolderFingerprint:
    """Lightweight provider state that changes for messages or flags."""

    uidvalidity: int
    uidnext: int
    exists: int
    highest_modseq: int | None


@dataclass(frozen=True, repr=False)
class _EphemeralBodyHtml:
    value: str

    def __repr__(self) -> str:
        return "<ephemeral-email-html:redacted>"


def ephemeral_body_html(message: Mapping[str, object]) -> str:
    """Read provider HTML that is deliberately non-serializable and transient."""

    value = message.get("_ephemeralBodyHtml")
    return value.value if isinstance(value, _EphemeralBodyHtml) else ""


@dataclass(frozen=True, repr=False)
class _EphemeralUnsubscribeAuthentication:
    value: UnsubscribeAuthenticationEvidence

    def __repr__(self) -> str:
        return "<ephemeral-unsubscribe-authentication:redacted>"


def attach_ephemeral_unsubscribe_authentication(
    message: dict[str, object],
    evidence: UnsubscribeAuthenticationEvidence,
) -> None:
    """Attach already-validated provider evidence without making it serializable."""

    if not isinstance(evidence, UnsubscribeAuthenticationEvidence):
        raise TypeError("unsubscribe authentication evidence is invalid")
    message["_ephemeralUnsubscribeAuthentication"] = (
        _EphemeralUnsubscribeAuthentication(evidence)
    )


def ephemeral_unsubscribe_authentication(
    message: Mapping[str, object],
) -> UnsubscribeAuthenticationEvidence | None:
    value = message.get("_ephemeralUnsubscribeAuthentication")
    return (
        value.value if isinstance(value, _EphemeralUnsubscribeAuthentication) else None
    )


@dataclass(frozen=True)
class _BodyPart:
    section: str
    mime_type: str
    charset: str
    transfer_encoding: str
    size_bytes: int
    filename: str
    disposition: str
    children: tuple["_BodyPart", ...] = ()


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
        unread_only: bool = False,
        excluded_uids: frozenset[int] = frozenset(),
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
        try:
            mailbox_argument = encode_imap_mailbox_argument(mailbox)
        except ImapMailboxCodecError as exc:
            raise ValueError("invalid IMAP mailbox") from exc
        status, _ = self.session.select(mailbox_argument, readonly=True)
        _require_ok(status, "IMAP readonly select failed")
        uidvalidity = _uidvalidity(self.session.response("UIDVALIDITY"))
        search_after = last_seen_uid if cursor_uidvalidity == uidvalidity else 0
        first_uid = search_after + 1
        criterion = "UNSEEN" if unread_only else f"UID {first_uid}:*"
        status, data = self.session.uid("SEARCH", None, criterion)
        _require_ok(status, "IMAP UID search failed")
        uids = [
            uid
            for uid in _search_uids(data)
            if (unread_only or int(uid) >= first_uid) and int(uid) not in excluded_uids
        ][:limit]
        messages: list[dict[str, object]] = []
        for uid in uids:
            status, structure_data = self.session.uid("FETCH", uid, "(BODYSTRUCTURE)")
            _require_ok(status, "IMAP BODYSTRUCTURE fetch failed")
            structure = _parse_bodystructure(structure_data)
            status, header_data = self.session.uid("FETCH", uid, _HEADER_FETCH)
            _require_ok(status, "IMAP header fetch failed")
            headers = email.message_from_bytes(
                _fetch_payload(header_data), policy=email.policy.default
            )
            provider_unread = all(
                flag.casefold() != "\\seen"
                for flag in parse_imap_fetch_flags(header_data)
            )
            important_signals = _imap_important_signals(header_data)
            remaining = _MAX_TEXT_FETCH_BYTES
            body_parts: list[str] = []
            html_parts: list[str] = []
            selected_text_sections = {
                part.section for part in _selected_text_parts(structure)
            }
            selected_parts = {
                part.section: part
                for part in (
                    *_selected_text_parts(structure),
                    *_selected_html_parts(structure),
                )
            }
            for part in selected_parts.values():
                if remaining <= 0:
                    break
                status, body_data = self.session.uid(
                    "FETCH",
                    uid,
                    f"(BODY.PEEK[{part.section}]<0.{remaining}>)",
                )
                _require_ok(status, "IMAP text section fetch failed")
                payload = _fetch_payload(body_data)[:remaining]
                remaining -= len(payload)
                decoded = _decode_fetched_content(payload, part)
                if part.mime_type == "text/html" and decoded.strip():
                    html_parts.append(decoded)
                    text = (
                        _html_to_text(decoded)
                        if part.section in selected_text_sections
                        else ""
                    )
                else:
                    text = decoded if part.section in selected_text_sections else ""
                if text := text.strip():
                    body_parts.append(text)
            body = "\n".join(body_parts).strip()
            body_html = "\n".join(html_parts).strip()
            messages.append(
                _normalized_message_record(
                    headers,
                    body=body,
                    body_html=body_html,
                    attachments=_bodystructure_attachment_metadata(structure),
                    account_id=self.account_id,
                    folder=mailbox,
                    uidvalidity=uidvalidity,
                    uid=int(uid),
                    important_signals=important_signals,
                    provider_unread=provider_unread,
                )
            )
        return ImapUidBatch(
            account_id=self.account_id,
            folder=mailbox,
            uidvalidity=uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=messages,
        )

    def fetch_uid_membership(
        self,
        mailbox: str,
        *,
        cursor_uidvalidity: int,
        uids: tuple[int, ...],
    ) -> ImapUidMembership:
        mailbox = mailbox.strip()
        if not mailbox:
            raise ValueError("mailbox must be non-empty")
        if cursor_uidvalidity <= 0:
            raise ValueError("cursor_uidvalidity must be positive")
        if not uids or any(
            isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0
            for uid in uids
        ):
            raise ValueError("membership UIDs must be positive integers")
        if len(uids) != len(set(uids)):
            raise ValueError("membership UIDs must be unique")
        mailbox_argument = encode_imap_mailbox_argument(mailbox)
        status, _ = self.session.select(mailbox_argument, readonly=True)
        _require_ok(status, "IMAP readonly select failed")
        uidvalidity = _uidvalidity(self.session.response("UIDVALIDITY"))
        if uidvalidity != cursor_uidvalidity:
            return ImapUidMembership(uidvalidity, frozenset(), {})
        sequence_set = ",".join(str(uid) for uid in sorted(uids))
        status, data = self.session.uid("SEARCH", None, f"UID {sequence_set}")
        _require_ok(status, "IMAP UID membership search failed")
        requested = frozenset(uids)
        existing = frozenset(int(uid) for uid in _search_uids(data)) & requested
        important_signals_by_uid: dict[int, ImportantSignals] = {}
        for uid in sorted(existing):
            status, flag_data = self.session.uid("FETCH", str(uid), "(FLAGS)")
            _require_ok(status, "IMAP FLAGS fetch failed")
            important_signals_by_uid[uid] = _imap_important_signals(flag_data)
        return ImapUidMembership(uidvalidity, existing, important_signals_by_uid)

    def fetch_folder_fingerprint(self, mailbox: str) -> ProviderFolderFingerprint:
        mailbox = mailbox.strip()
        if not mailbox:
            raise ValueError("mailbox must be non-empty")
        mailbox_argument = encode_imap_mailbox_argument(mailbox)
        status, data = self.session.status(
            mailbox_argument,
            "(UIDVALIDITY UIDNEXT MESSAGES HIGHESTMODSEQ)",
        )
        if str(status).upper() != "OK":
            status, data = self.session.status(
                mailbox_argument,
                "(UIDVALIDITY UIDNEXT MESSAGES)",
            )
        _require_ok(status, "IMAP folder status failed")
        payload = b" ".join(item for item in data if isinstance(item, bytes))

        def required(name: bytes) -> int:
            match = re.search(rb"\b" + name + rb"\s+(\d+)\b", payload, re.I)
            if match is None:
                raise ConnectionError("IMAP folder status is incomplete")
            return int(match.group(1))

        modseq_match = re.search(rb"\bHIGHESTMODSEQ\s+(\d+)\b", payload, re.I)
        return ProviderFolderFingerprint(
            uidvalidity=required(b"UIDVALIDITY"),
            uidnext=required(b"UIDNEXT"),
            exists=required(b"MESSAGES"),
            highest_modseq=(
                int(modseq_match.group(1)) if modseq_match is not None else None
            ),
        )

    def list_folders(self) -> tuple[ProviderFolder, ...]:
        status, data = self.session.list()
        _require_ok(status, "IMAP folder discovery failed")
        try:
            parsed = parse_imap_list_response(data)
        except ImapFolderListError as exc:
            raise ValueError("invalid IMAP folder inventory") from exc
        folders = tuple(
            mailbox.provider_folder() for mailbox in parsed if mailbox.selectable
        )
        identifiers = tuple(folder.provider_folder_id for folder in folders)
        if not folders or len(identifiers) != len(set(identifiers)):
            raise ValueError("IMAP folder inventory is missing or ambiguous")
        return folders

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


def parse_imap_list_folder(raw: object) -> ProviderFolder:
    try:
        parsed = parse_imap_list_response((raw,))
    except ImapFolderListError as exc:
        raise ValueError("invalid IMAP folder response") from exc
    if len(parsed) != 1:
        raise ValueError("invalid IMAP folder response")
    return parsed[0].provider_folder()


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
    return _normalized_message_record(
        parsed,
        body=_message_body(parsed),
        body_html=_message_html_body(parsed),
        attachments=_attachment_metadata(parsed),
        account_id=account_id,
        folder=folder,
        uidvalidity=uidvalidity,
        uid=uid,
        important_signals=ImportantSignals((), False),
        provider_unread=True,
    )


def _normalized_message_record(
    parsed: email.message.Message,
    *,
    body: str,
    body_html: str,
    attachments: list[dict[str, object]],
    account_id: str,
    folder: str,
    uidvalidity: int,
    uid: int,
    important_signals: ImportantSignals,
    provider_unread: bool,
) -> dict[str, object]:
    message_id = _decode_header(parsed.get("Message-ID", ""))
    in_reply_to = _message_ids(parsed.get("In-Reply-To", ""))
    references = _message_ids(parsed.get("References", ""))
    thread_source = references or in_reply_to or _message_ids(message_id)
    thread_id = _thread_identity(
        thread_source[0] if thread_source else "",
        account_id=account_id,
    )
    locator = EmailProviderLocator(
        account_id=account_id,
        folder=folder,
        uidvalidity=uidvalidity,
        uid=uid,
        rfc_message_id=message_id,
        thread_id=thread_id,
    )
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
    result: dict[str, object] = {
        "id": stable_identity,
        "stableMessageIdentity": stable_identity,
        "accountId": locator.account_id,
        "folder": locator.folder,
        "uidValidity": locator.uidvalidity,
        "uid": locator.uid,
        "messageId": locator.rfc_message_id,
        "inReplyTo": in_reply_to[0] if in_reply_to else "",
        "references": list(references),
        "threadId": locator.thread_id,
        "from": {"name": sender_name, "email": sender_email},
        "toRecipients": to_recipients,
        "ccRecipients": cc_recipients,
        "subject": subject,
        "date": date,
        "textBody": body,
        "markdownBody": body,
        "listUnsubscribe": parsed.get("List-Unsubscribe", ""),
        "listUnsubscribePost": parsed.get("List-Unsubscribe-Post", ""),
        "autoSubmitted": parsed.get("Auto-Submitted", ""),
        "hasAttachment": bool(attachments),
        "attachments": attachments,
        "importantSignals": important_signals,
        "providerUnread": provider_unread,
    }
    if body_html:
        result["_ephemeralBodyHtml"] = _EphemeralBodyHtml(body_html)
    return result


def _imap_important_signals(data: object) -> ImportantSignals:
    signal_names = tuple(
        flag
        for flag in parse_imap_fetch_flags(data)
        if flag.casefold() in {"\\flagged", "$important"}
    )
    return normalize_important_signals(
        provider="imap",
        raw_signal_names=signal_names,
    )


def _parse_bodystructure(data: object) -> _BodyPart:
    metadata = _fetch_metadata(data)
    match = re.search(rb"\bBODYSTRUCTURE\b", metadata, flags=re.IGNORECASE)
    if match is None:
        raise ConnectionError("IMAP BODYSTRUCTURE fetch returned invalid data")
    expression = metadata[match.end() :].lstrip()
    try:
        value, _ = _parse_imap_value(expression, 0)
        return _body_part(value, section="")
    except (IndexError, TypeError, ValueError) as exc:
        raise ConnectionError("IMAP BODYSTRUCTURE fetch returned invalid data") from exc


def _fetch_metadata(data: object) -> bytes:
    if not isinstance(data, (list, tuple)):
        raise ConnectionError("IMAP FETCH returned invalid data")
    parts: list[bytes] = []
    for item in data:
        if isinstance(item, bytes):
            parts.append(item)
        elif isinstance(item, tuple) and item and isinstance(item[0], bytes):
            parts.append(item[0])
    return b" ".join(parts)


def _parse_imap_value(data: bytes, index: int) -> tuple[object, int]:
    while index < len(data) and data[index : index + 1].isspace():
        index += 1
    if index >= len(data):
        raise ValueError("missing IMAP value")
    if data[index] == ord("("):
        values: list[object] = []
        index += 1
        while True:
            while index < len(data) and data[index : index + 1].isspace():
                index += 1
            if index >= len(data):
                raise ValueError("unterminated IMAP list")
            if data[index] == ord(")"):
                return values, index + 1
            value, index = _parse_imap_value(data, index)
            values.append(value)
    if data[index] == ord('"'):
        index += 1
        value = bytearray()
        while index < len(data):
            character = data[index]
            index += 1
            if character == ord('"'):
                return value.decode("utf-8", errors="replace"), index
            if character == ord("\\"):
                if index >= len(data):
                    raise ValueError("unterminated IMAP quoted string")
                character = data[index]
                index += 1
            value.append(character)
        raise ValueError("unterminated IMAP quoted string")
    end = index
    while (
        end < len(data)
        and not data[end : end + 1].isspace()
        and data[end]
        not in (
            ord("("),
            ord(")"),
        )
    ):
        end += 1
    atom = data[index:end].decode("ascii", errors="replace")
    if atom.upper() == "NIL":
        return None, end
    if atom.isdigit():
        return int(atom), end
    return atom, end


def _body_part(value: object, *, section: str) -> _BodyPart:
    if not isinstance(value, list) or not value:
        raise ValueError("BODYSTRUCTURE node must be a non-empty list")
    if isinstance(value[0], list):
        child_count = 0
        while child_count < len(value) and isinstance(value[child_count], list):
            child_count += 1
        if child_count == 0 or child_count >= len(value):
            raise ValueError("multipart BODYSTRUCTURE is incomplete")
        children = tuple(
            _body_part(
                child,
                section=f"{section}.{index}" if section else str(index),
            )
            for index, child in enumerate(value[:child_count], start=1)
        )
        disposition, disposition_params = _body_disposition(
            value[child_count + 2] if len(value) > child_count + 2 else None
        )
        return _BodyPart(
            section=section,
            mime_type=f"multipart/{_body_string(value[child_count]).lower()}",
            charset="",
            transfer_encoding="",
            size_bytes=0,
            filename=_body_filename({}, disposition_params),
            disposition=disposition,
            children=children,
        )
    if len(value) < 7:
        raise ValueError("single-part BODYSTRUCTURE is incomplete")
    media_type = _body_string(value[0]).lower()
    subtype = _body_string(value[1]).lower()
    params = _body_params(value[2])
    if media_type == "text":
        disposition_index = 9
    elif media_type == "message" and subtype == "rfc822":
        disposition_index = 11
    else:
        disposition_index = 8
    disposition, disposition_params = _body_disposition(
        value[disposition_index] if len(value) > disposition_index else None
    )
    size = value[6] if isinstance(value[6], int) else 0
    return _BodyPart(
        section=section or "1",
        mime_type=f"{media_type}/{subtype}",
        charset=params.get("CHARSET", ""),
        transfer_encoding=_body_string(value[5]).lower(),
        size_bytes=max(size, 0),
        filename=_body_filename(params, disposition_params),
        disposition=disposition,
    )


def _body_string(value: object) -> str:
    return "" if value is None else str(value)


def _body_params(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    return {
        _body_string(value[index]).upper(): _body_string(value[index + 1])
        for index in range(0, len(value) - 1, 2)
    }


def _body_disposition(value: object) -> tuple[str, dict[str, str]]:
    if not isinstance(value, list) or not value:
        return "", {}
    return _body_string(value[0]).lower(), _body_params(
        value[1] if len(value) > 1 else None
    )


def _body_filename(
    content_params: Mapping[str, str], disposition_params: Mapping[str, str]
) -> str:
    return _decode_header(
        disposition_params.get("FILENAME") or content_params.get("NAME") or ""
    )


def _is_bodystructure_attachment(part: _BodyPart) -> bool:
    return part.disposition in {"attachment", "inline"} or bool(part.filename)


def _selected_text_parts(part: _BodyPart) -> tuple[_BodyPart, ...]:
    if _is_bodystructure_attachment(part):
        return ()
    if part.mime_type == "multipart/alternative":
        descendants = tuple(_text_descendants(part))
        for preferred_type in ("text/plain", "text/html"):
            for candidate in descendants:
                if candidate.mime_type == preferred_type:
                    return (candidate,)
        return ()
    if part.children:
        return tuple(
            child_part
            for child in part.children
            for child_part in _selected_text_parts(child)
        )
    if part.mime_type in {"text/plain", "text/html"}:
        return (part,)
    return ()


def _text_descendants(part: _BodyPart) -> Iterable[_BodyPart]:
    for child in part.children:
        if _is_bodystructure_attachment(child):
            continue
        if child.children:
            yield from _text_descendants(child)
        elif child.mime_type in {"text/plain", "text/html"}:
            yield child


def _selected_html_parts(part: _BodyPart) -> tuple[_BodyPart, ...]:
    if _is_bodystructure_attachment(part):
        return ()
    if part.children:
        return tuple(
            candidate
            for child in part.children
            for candidate in _selected_html_parts(child)
        )
    return (part,) if part.mime_type == "text/html" else ()


def _bodystructure_attachment_metadata(
    part: _BodyPart,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []

    def visit(candidate: _BodyPart) -> None:
        if _is_bodystructure_attachment(candidate):
            result.append(
                {
                    "filename": candidate.filename,
                    "mime_type": candidate.mime_type,
                    "size_bytes": candidate.size_bytes,
                    "inline": candidate.disposition == "inline",
                }
            )
            return
        for child in candidate.children:
            visit(child)

    visit(part)
    return result


def _decode_fetched_text(payload: bytes, part: _BodyPart) -> str:
    value = _decode_fetched_content(payload, part)
    return _html_to_text(value) if part.mime_type == "text/html" else value


def _decode_fetched_content(payload: bytes, part: _BodyPart) -> str:
    decoded = payload
    if part.transfer_encoding == "base64":
        compact = b"".join(payload.split())
        compact += b"=" * (-len(compact) % 4)
        try:
            decoded = base64.b64decode(compact, validate=False)
        except binascii.Error:
            decoded = b""
    elif part.transfer_encoding == "quoted-printable":
        decoded = quopri.decodestring(payload)
    value = decoded.decode(part.charset or "utf-8", errors="replace")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value


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
        text for child in children if (text := _message_body(child).strip())
    ).strip()


def _message_html_body(message: email.message.Message) -> str:
    if _is_attachment(message):
        return ""
    if not message.is_multipart():
        return (
            _decode_part(message)
            if message.get_content_type().lower() == "text/html"
            else ""
        )
    children = list(message.iter_parts())
    if message.get_content_subtype().lower() == "alternative":
        for child in children:
            if child.get_content_type().lower() == "text/html":
                return _message_html_body(child).strip()
        return ""
    return "\n".join(
        html for child in children if (html := _message_html_body(child).strip())
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
            recipients.extend(
                dict(item) for item in values if isinstance(item, Mapping)
            )
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


def _message_ids(value: object) -> tuple[str, ...]:
    result: list[str] = []
    for candidate in re.findall(r"<[^<>\s]+@[^<>\s]+>", str(value or "")):
        normalized = EmailProviderLocator(
            account_id="message-id-normalizer",
            folder="message-id-normalizer",
            uidvalidity=1,
            uid=1,
            rfc_message_id=candidate,
        ).rfc_message_id
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


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
        return str(
            email.header.make_header(email.header.decode_header(str(value or "")))
        )
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
    if (
        not isinstance(data, (list, tuple))
        or not data
        or not isinstance(data[0], bytes)
    ):
        return []
    values = {int(uid) for uid in data[0].split() if uid.isdigit() and int(uid) > 0}
    return [str(uid).encode("ascii") for uid in sorted(values)]


def _fetch_payload(data: object) -> bytes:
    if not isinstance(data, (list, tuple)):
        raise ConnectionError("IMAP UID fetch returned invalid data")
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    raise ConnectionError("IMAP UID fetch returned no literal payload")


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
