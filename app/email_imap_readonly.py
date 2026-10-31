"""Standard-library IMAP adapter that never changes mailbox state."""

from __future__ import annotations

import base64
import binascii
import email
import email.header
import email.policy
import email.utils
import imaplib
import json
import quopri
import re
import socket
import threading
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
from app.email_html_text import html_to_text
from app.email_provider_folders import ProviderFolder
from app.email_unsubscribe import (
    ConnectedMailboxOtp,
    EmailOtpChallenge,
    UnsubscribeAuthenticationEvidence,
    email_otp_context_reference,
)


_HEADER_FETCH = (
    "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID REFERENCES "
    "IN-REPLY-TO LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST AUTO-SUBMITTED)])"
)
_MAX_TEXT_FETCH_BYTES = 64 * 1024
IMAP_READ_TIMEOUT_SECONDS = 90.0
IMAP_COMMAND_TIMEOUT_SECONDS = 90.0


class _CommandDeadlineMixin:
    """Bound one IMAP command even when the peer keeps trickling bytes."""

    def __init__(self, *args: Any, command_timeout: float, **kwargs: Any) -> None:
        if command_timeout <= 0:
            raise ValueError("command_timeout must be positive")
        self._command_timeout = command_timeout
        self._command_lock = threading.Lock()
        self._command_timer: threading.Timer | None = None
        self._command_active = False
        self._command_expired = False
        super().__init__(*args, **kwargs)

    def _start_command_deadline(self) -> None:
        with self._command_lock:
            if self._command_active:
                raise RuntimeError("IMAP commands cannot overlap on one session")
            self._command_active = True
            self._command_expired = False
            timer = threading.Timer(self._command_timeout, self._expire_command)
            timer.daemon = True
            self._command_timer = timer
            timer.start()

    def _expire_command(self) -> None:
        with self._command_lock:
            if not self._command_active:
                return
            self._command_expired = True
            session_socket = getattr(self, "sock", None)
        if session_socket is None:
            return
        try:
            session_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _command_deadline_expired(self) -> bool:
        with self._command_lock:
            return self._command_expired

    def _finish_command(self) -> None:
        with self._command_lock:
            self._command_active = False
            timer = self._command_timer
            self._command_timer = None
        if timer is not None:
            timer.cancel()

    def _raise_command_timeout(self, cause: Exception) -> None:
        self._finish_command()
        try:
            self.file.close()
        except Exception:
            pass
        raise TimeoutError(
            f"IMAP command exceeded its {self._command_timeout:g}s total deadline"
        ) from cause

    def _command(self, name: str, *args: Any) -> bytes:
        self._start_command_deadline()
        try:
            return super()._command(name, *args)
        except Exception as exc:
            if self._command_deadline_expired():
                self._raise_command_timeout(exc)
            self._finish_command()
            raise

    def _get_tagged_response(self, tag: bytes, expect_bye: bool = False) -> Any:
        try:
            return super()._get_tagged_response(tag, expect_bye=expect_bye)
        except Exception as exc:
            if self._command_deadline_expired():
                self._raise_command_timeout(exc)
            raise
        finally:
            self._finish_command()

    def _command_complete(self, name: str, tag: bytes) -> Any:
        try:
            return super()._command_complete(name, tag)
        finally:
            self._finish_command()


class _DeadlineIMAP4SSL(_CommandDeadlineMixin, imaplib.IMAP4_SSL):
    pass


class _DeadlineIMAP4(_CommandDeadlineMixin, imaplib.IMAP4):
    """Plain IMAP variant used to exercise deadline behavior with socketpair tests."""

    pass


class EmailSourceMessageMissing(LookupError):
    """An exact RFC Message-ID is absent from one observed folder."""

    retryable_external_dependency = True


@dataclass(frozen=True)
class ImapUidBatch:
    """One readonly folder observation and the UID generation it belongs to."""

    account_id: str
    folder: str
    uidvalidity: int
    previous_uidvalidity: int | None
    messages: list[dict[str, object]]
    # Messages that matched the search and were not excluded, but did not fit
    # in this batch. None when the source cannot say.
    remaining: int | None = None
    # The highest UID this call attempted, whether or not it produced a
    # message (a malformed message this Python cannot parse is attempted and
    # skipped, not fetched again next time). None when nothing was attempted.
    attempted_max_uid: int | None = None


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


def _domains_share_identity(sender_domain: str, site_domain: str) -> bool:
    sender = sender_domain.casefold().rstrip(".")
    site = site_domain.casefold().rstrip(".")
    return bool(sender and site) and (
        sender == site
        or sender.endswith("." + site)
        or site.endswith("." + sender)
    )


def _provider_domains_share_identity(left_domain: str, right_domain: str) -> bool:
    """Match sibling provider hosts without accepting broad public suffixes."""

    if _domains_share_identity(left_domain, right_domain):
        return True
    left = left_domain.casefold().rstrip(".").split(".")
    right = right_domain.casefold().rstrip(".").split(".")
    if len(left) < 3 or len(right) < 3:
        return False
    blocked_suffixes = {
        "co.uk",
        "com.cn",
        "com.au",
        "co.jp",
        "co.kr",
        "co.nz",
    }
    common = ".".join(left[-2:])
    return common == ".".join(right[-2:]) and common not in blocked_suffixes


def _single_bounded_numeric_token(value: str) -> str | None:
    tokens: list[str] = []
    current: list[str] = []
    for character in value:
        if "0" <= character <= "9":
            current.append(character)
            continue
        if 4 <= len(current) <= 10:
            tokens.append("".join(current))
        current = []
    if 4 <= len(current) <= 10:
        tokens.append("".join(current))
    unique = tuple(dict.fromkeys(tokens))
    return unique[0] if len(unique) == 1 else None


def _authenticated_sender_domain(
    headers: email.message.Message,
    *,
    trusted_authserv_domain: str,
    sender_domain: str,
    site_domain: str,
) -> tuple[str, str] | None:
    """Accept only provider-issued aligned pass evidence from Authentication-Results."""

    if not trusted_authserv_domain:
        return None
    values = headers.get_all("authentication-results", [])
    if not values:
        return None
    value = " ".join(str(values[0]).split())
    authserv_id = value.split(";", 1)[0].strip().casefold().rstrip(".")
    if not _provider_domains_share_identity(authserv_id, trusted_authserv_domain):
        return None
    result_text = value.split(";", 1)[1] if ";" in value else ""
    result_starts = tuple(
        re.finditer(
            r"(?:^|;)\s*([a-z][a-z0-9_-]*)\s*=\s*([a-z][a-z0-9_-]*)\b",
            result_text,
            re.I,
        )
    )
    clauses: dict[str, list[tuple[str, str]]] = {}
    for index, match in enumerate(result_starts):
        end = (
            result_starts[index + 1].start()
            if index + 1 < len(result_starts)
            else len(result_text)
        )
        mechanism = match.group(1).casefold()
        clauses.setdefault(mechanism, []).append(
            (match.group(2).casefold(), result_text[match.end() : end])
        )
    accepted: list[tuple[str, str]] = []
    for mechanism, property_name in (
        ("dkim", "header.d"),
        ("spf", "smtp.mailfrom"),
    ):
        method_results = clauses.get(mechanism, [])
        if len(method_results) > 1:
            return None
        if not method_results:
            continue
        result, clause = method_results[0]
        if result != "pass":
            continue
        properties = re.findall(
            rf"\b{re.escape(property_name)}\s*=\s*([^;\s]+)",
            clause,
            re.I,
        )
        if len(properties) != 1:
            return None
        authenticated = properties[0].strip("<>@\"'").casefold().rstrip(".")
        if "@" in authenticated:
            authenticated = authenticated.rsplit("@", 1)[-1]
        if not (
            _domains_share_identity(authenticated, sender_domain)
            and _domains_share_identity(authenticated, site_domain)
        ):
            return None
        accepted.append((mechanism, authenticated))
    if not accepted or len({domain for _, domain in accepted}) != 1:
        return None
    mechanism, authenticated = accepted[0]
    reference = "otp-auth:" + sha256(
        f"{authserv_id}\n{mechanism}\n{authenticated}\n{value}".encode()
    ).hexdigest()
    return authenticated, reference


def _imap_internaldate(data: object) -> datetime:
    raw = b" ".join(
        item[0] if isinstance(item, tuple) and isinstance(item[0], bytes) else item
        for item in (data if isinstance(data, list) else ())
        if isinstance(item, (bytes, tuple))
    )
    marker = b'INTERNALDATE "'
    start = raw.find(marker)
    if start < 0:
        raise ValueError("IMAP OTP INTERNALDATE is unavailable")
    start += len(marker)
    end = raw.find(b'"', start)
    if end < 0:
        raise ValueError("IMAP OTP INTERNALDATE is invalid")
    parsed = email.utils.parsedate_to_datetime(raw[start:end].decode("ascii"))
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("IMAP OTP INTERNALDATE is invalid")
    return parsed.astimezone(timezone.utc)


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

    def __init__(
        self,
        session: Any,
        *,
        account_id: str,
        mailbox_address: str = "",
        trusted_authserv_domain: str = "",
    ):
        account_id = account_id.strip()
        if not account_id:
            raise ValueError("account_id must be non-empty")
        self.session = session
        self.account_id = account_id
        self.mailbox_address = mailbox_address.strip()
        self.trusted_authserv_domain = trusted_authserv_domain.strip().casefold()

    @classmethod
    def connect(
        cls,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 993,
        # 20s was too tight for at least one real account: fetching a single
        # message's headers reliably took 5-6s on a slow path to it (measured
        # 2026-09-28), and a busy folder's own SEARCH can add more on top,
        # which surfaced as a socket TimeoutError that aborted the whole scan
        # or training-observation batch -- and, because the batch never
        # completes, the same slow point is retried and times out again on
        # every future attempt. A raw socket timeout mid-command cannot be
        # caught and continued on the same session (the read desyncs from
        # the server's still-pending response), so widening the ceiling is
        # the safe fix here, not a catch-and-skip.
        timeout: float | None = IMAP_READ_TIMEOUT_SECONDS,
        account_id: str,
        mailbox_address: str = "",
        trusted_authserv_domain: str = "",
    ) -> "ImapReadonlyAdapter":
        from app.email_provider_actions import _imap_response_text

        session = _DeadlineIMAP4SSL(
            host,
            port,
            timeout=timeout,
            command_timeout=IMAP_COMMAND_TIMEOUT_SECONDS,
        )
        try:
            status, login_data = session.login(username, password)
        except imaplib.IMAP4.error as exc:
            _close_imap_session(session)
            raise ConnectionError(f"IMAP login failed: {exc}") from exc
        if status != "OK":
            _close_imap_session(session)
            detail = _imap_response_text(login_data)
            raise ConnectionError(
                f"IMAP login failed: {detail}" if detail else "IMAP login failed"
            )
        return cls(
            session,
            account_id=account_id,
            mailbox_address=mailbox_address,
            trusted_authserv_domain=trusted_authserv_domain or host,
        )

    def fetch_email_otp_candidates(
        self,
        challenge: EmailOtpChallenge,
        *,
        limit: int = 8,
    ) -> tuple[ConnectedMailboxOtp, ...]:
        """Read bounded post-challenge OTP candidates without changing mailbox state."""

        if not isinstance(challenge, EmailOtpChallenge):
            raise TypeError("challenge must be EmailOtpChallenge")
        if limit <= 0 or limit > 32:
            raise ValueError("OTP candidate limit is invalid")
        if (
            not self.mailbox_address
            or challenge.recipient.casefold() != self.mailbox_address.casefold()
        ):
            return ()
        status, _ = self.session.select("INBOX", readonly=True)
        _require_ok(status, "IMAP readonly select failed")
        since = challenge.opened_at.astimezone(timezone.utc).strftime("%d-%b-%Y")
        status, data = self.session.uid(
            "SEARCH",
            None,
            "SINCE",
            since,
            "TO",
            self.mailbox_address,
        )
        _require_ok(status, "IMAP OTP search failed")
        uids = list(reversed(_search_uids(data)[-min(limit * 4, 32) :]))
        candidates: list[ConnectedMailboxOtp] = []
        for uid in uids:
            status, header_data = self.session.uid(
                "FETCH",
                uid,
                "(INTERNALDATE BODY.PEEK[HEADER.FIELDS "
                "(FROM TO SUBJECT AUTHENTICATION-RESULTS)])",
            )
            _require_ok(status, "IMAP OTP header fetch failed")
            received_at = _imap_internaldate(header_data)
            if not (challenge.opened_at <= received_at <= challenge.expires_at):
                continue
            headers = email.message_from_bytes(
                _fetch_payload(header_data), policy=email.policy.default
            )
            recipients = {
                address.casefold()
                for _, address in email.utils.getaddresses(headers.get_all("to", []))
            }
            sender_addresses = email.utils.getaddresses(headers.get_all("from", []))
            sender_domain = (
                sender_addresses[0][1].rsplit("@", 1)[-1].casefold().rstrip(".")
                if sender_addresses and "@" in sender_addresses[0][1]
                else ""
            )
            if (
                challenge.recipient.casefold() not in recipients
                or not _domains_share_identity(sender_domain, challenge.site_domain)
            ):
                continue
            authenticated = _authenticated_sender_domain(
                headers,
                trusted_authserv_domain=self.trusted_authserv_domain,
                sender_domain=sender_domain,
                site_domain=challenge.site_domain,
            )
            if authenticated is None:
                continue
            status, body_data = self.session.uid(
                "FETCH", uid, "(BODY.PEEK[TEXT]<0.8192>)"
            )
            _require_ok(status, "IMAP OTP text fetch failed")
            body = _fetch_payload(body_data)[:8192].decode("utf-8", "replace")
            subject = str(headers.get("subject") or "")
            evidence = f"{subject}\n{body}".casefold()
            if challenge.site_domain not in evidence:
                continue
            context_reference = email_otp_context_reference(
                challenge.recipient,
                challenge.site_domain,
            )
            if context_reference != challenge.context_reference:
                continue
            value = _single_bounded_numeric_token(body)
            if value is None:
                continue
            candidates.append(
                ConnectedMailboxOtp(
                    recipient=challenge.recipient,
                    sender_domain=sender_domain,
                    context_reference=context_reference,
                    authenticated_sender_domain=authenticated[0],
                    authentication_reference=authenticated[1],
                    received_at=received_at,
                    value=value,
                )
            )
            if len(candidates) >= limit:
                break
        return tuple(candidates)

    def fetch_uid_batch(
        self,
        mailbox: str = "INBOX",
        *,
        cursor_uidvalidity: int | None,
        last_seen_uid: int,
        limit: int = 50,
        unread_only: bool = False,
        excluded_uids: frozenset[int] = frozenset(),
        since: date | None = None,
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
        if since is not None:
            # IMAP SINCE compares the server's internal date by calendar day;
            # it is what bounds a mailbox with years of unread mail.
            criterion = f"{criterion} SINCE {_imap_date(since)}"
        status, data = self.session.uid("SEARCH", None, criterion)
        _require_ok(status, "IMAP UID search failed")
        matched = [
            uid
            for uid in _search_uids(data)
            if (unread_only or int(uid) >= first_uid) and int(uid) not in excluded_uids
        ]
        uids = matched[:limit]
        not_in_batch = len(matched) - len(uids)
        messages: list[dict[str, object]] = []
        for uid in uids:
            try:
                messages.append(
                    self._fetch_one_message(uid, mailbox=mailbox, uidvalidity=uidvalidity)
                )
            except LookupError:
                # A provider occasionally sends a header Python's email package
                # cannot parse -- e.g. a structured header (In-Reply-To,
                # References) carrying a charset name Python does not know,
                # which _decode_header's own guard does not reach because
                # nothing there calls it on those headers. Observed 2026-09-28:
                # this silently failed the whole Gmail training-observation
                # batch, every attempt, for days. One bad message must not
                # cost the rest of a bounded batch.
                not_in_batch += 1
        return ImapUidBatch(
            account_id=self.account_id,
            folder=mailbox,
            uidvalidity=uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=messages,
            remaining=not_in_batch,
            attempted_max_uid=max((int(uid) for uid in uids), default=None),
        )

    def locate_message(
        self, locator: EmailProviderLocator, *, folder: str,
    ) -> EmailProviderLocator:
        """Locate one exact Message-ID in an explicitly observed current folder."""
        if locator.account_id != self.account_id:
            raise ValueError("email source account mismatch")
        if locator.rfc_message_id is None:
            raise LookupError("email source has no Message-ID for moved lookup")
        mailbox = encode_imap_mailbox_argument(folder)
        status, _ = self.session.select(mailbox, readonly=True)
        _require_ok(status, "IMAP readonly select failed")
        generation = _uidvalidity(self.session.response("UIDVALIDITY"))
        # Message-ID domains are canonicalized locally. Some providers index
        # HEADER by the exact original spelling, so a normalized search can
        # miss a message that is present. Read header-only candidates and apply
        # the existing canonical identity contract, rather than guessing case
        # variants or accepting a sender/subject match.
        status, data = self.session.uid("SEARCH", None, "ALL")
        _require_ok(status, "IMAP UID search failed")
        uids = _search_uids(data)
        matches = []
        for start in range(0, len(uids), 50):
            batch = uids[start:start + 50]
            status, header_data = self.session.uid(
                "FETCH", b','.join(batch), '(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])',
            )
            _require_ok(status, "IMAP header fetch failed")
            records = _split_exact_fetch_records(header_data, {int(uid) for uid in batch})
            for uid, record in records.items():
                headers = email.message_from_bytes(_fetch_payload(record), policy=email.policy.default)
                observed = EmailProviderLocator(
                    account_id=self.account_id, folder=folder, uidvalidity=generation, uid=uid,
                    rfc_message_id=_decode_header(headers.get("Message-ID", "")),
                    thread_id=locator.thread_id,
                )
                if observed.rfc_message_id == locator.rfc_message_id:
                    matches.append(observed)
        status, _ = self.session.select(mailbox, readonly=True)
        _require_ok(status, "IMAP readonly select failed")
        if _uidvalidity(self.session.response("UIDVALIDITY")) != generation:
            raise LookupError("email source generation changed")
        if not matches:
            raise EmailSourceMessageMissing("email source Message-ID is missing")
        if len(matches) > 1:
            raise LookupError("email source Message-ID is ambiguous")
        return matches[0]

    def locate_message_across_folders(
        self, locator: EmailProviderLocator,
    ) -> EmailProviderLocator:
        """Resolve one exact RFC Message-ID in the current selectable folders."""
        if locator.account_id != self.account_id:
            raise ValueError("email source account mismatch")
        if locator.rfc_message_id is None:
            raise LookupError("email source has no Message-ID for moved lookup")
        matches = []
        for folder in self.list_folders():
            try:
                matches.append(
                    self.locate_message(locator, folder=folder.display_name)
                )
            except EmailSourceMessageMissing:
                continue
        if not matches:
            raise EmailSourceMessageMissing("email source Message-ID is missing")
        if len(matches) > 1:
            raise LookupError("email source Message-ID is ambiguous")
        return matches[0]

    def fetch_message(
        self, locator: EmailProviderLocator, *, stable_message_identity: str,
    ) -> dict[str, object]:
        """Read the full selected body at exact coordinates; never search or write."""
        if locator.account_id != self.account_id:
            raise ValueError("email source account mismatch")
        mailbox = encode_imap_mailbox_argument(locator.folder)

        def select_generation():
            status, _ = self.session.select(mailbox, readonly=True)
            _require_ok(status, "IMAP readonly select failed")
            if _uidvalidity(self.session.response("UIDVALIDITY")) != locator.uidvalidity:
                raise LookupError("email source generation changed")

        select_generation()
        message = self._fetch_one_message(
            str(locator.uid).encode("ascii"), mailbox=locator.folder,
            uidvalidity=locator.uidvalidity, complete=True,
        )
        observed = EmailProviderLocator(
            account_id=self.account_id, folder=locator.folder,
            uidvalidity=locator.uidvalidity, uid=locator.uid,
            rfc_message_id=message["messageId"],
        )
        if (message["stableMessageIdentity"] != stable_message_identity
            or (locator.rfc_message_id is not None
                and observed.rfc_message_id != locator.rfc_message_id)):
            raise LookupError("email source identity mismatch")
        select_generation()
        return message

    def fetch_messages(self, requests):
        """Read finite exact originals with native UID sets, retaining no cache.

        Missing/changed individual originals return LookupError at that identity;
        malformed batch responses and generation changes fail the whole read.
        """
        groups = {}
        for locator, identity in requests:
            if locator.account_id != self.account_id:
                raise ValueError("email source account mismatch")
            groups.setdefault((locator.folder, locator.uidvalidity), []).append((locator, identity))
        result = {}
        for (folder, generation), items in groups.items():
            for start in range(0, len(items), 50):
                batch = items[start:start + 50]
                by_uid = {locator.uid: (locator, identity) for locator, identity in batch}
                if len(by_uid) != len(batch):
                    raise ValueError("email source batch has duplicate UID")
                mailbox = encode_imap_mailbox_argument(folder)

                def select_generation():
                    status, _ = self.session.select(mailbox, readonly=True)
                    _require_ok(status, "IMAP readonly select failed")
                    if _uidvalidity(self.session.response("UIDVALIDITY")) != generation:
                        raise LookupError("email source generation changed")

                def fetch(uids, query):
                    sequence = ','.join(str(uid) for uid in sorted(uids)).encode('ascii')
                    status, data = self.session.uid('FETCH', sequence, query)
                    _require_ok(status, "IMAP exact batch fetch failed")
                    return _split_exact_fetch_records(data, set(uids))

                select_generation()
                structures = fetch(by_uid, '(BODYSTRUCTURE)')
                headers = fetch(by_uid, _HEADER_FETCH)
                sections = {}
                for uid in by_uid.keys() & structures.keys() & headers.keys():
                    structure = _parse_bodystructure(structures[uid])
                    for part in (*_selected_text_parts(structure), *_selected_html_parts(structure)):
                        sections.setdefault(f'(BODY.PEEK[{part.section}])', set()).add(uid)
                body_data = {query: fetch(uids, query) for query, uids in sections.items()}
                for uid, (locator, identity) in by_uid.items():
                    def source_data(query, error):
                        records = (structures if query == '(BODYSTRUCTURE)' else
                                   headers if query == _HEADER_FETCH else body_data[query])
                        if uid not in records:
                            raise LookupError("email source UID is unavailable")
                        return records[uid]

                    try:
                        message = self._decode_selected_message(
                            str(uid).encode('ascii'), mailbox=folder,
                            uidvalidity=generation, complete=True, fetch=source_data)
                        if (message['stableMessageIdentity'] != identity or
                            (locator.rfc_message_id is not None and
                             message['messageId'] != locator.rfc_message_id)):
                            raise LookupError("email source identity mismatch")
                        result[identity] = message
                    except LookupError as exc:
                        result[identity] = exc
                select_generation()
        return result

    def _fetch_one_message(
        self, uid: bytes, *, mailbox: str, uidvalidity: int, complete: bool = False
    ) -> dict[str, object]:
        def fetch(query, error):
            status, data = self.session.uid("FETCH", uid, query)
            _require_ok(status, error)
            if complete:
                _require_exact_fetch_uid(data, uid)
            return data

        return self._decode_selected_message(
            uid, mailbox=mailbox, uidvalidity=uidvalidity, complete=complete, fetch=fetch)

    def _decode_selected_message(self, uid, *, mailbox, uidvalidity, complete, fetch):
        structure_data = fetch("(BODYSTRUCTURE)", "IMAP BODYSTRUCTURE fetch failed")
        structure = _parse_bodystructure(structure_data)
        header_data = fetch(_HEADER_FETCH, "IMAP header fetch failed")
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
            if not complete and remaining <= 0:
                break
            query = (f"(BODY.PEEK[{part.section}])" if complete
                     else f"(BODY.PEEK[{part.section}]<0.{remaining}>)")
            body_data = fetch(query, "IMAP text section fetch failed")
            payload = _fetch_payload(body_data)
            if not complete:
                payload = payload[:remaining]
                remaining -= len(payload)
            decoded = _decode_fetched_content(payload, part)
            if part.mime_type == "text/html" and decoded.strip():
                html_parts.append(decoded)
                text = (
                    html_to_text(decoded)
                    if part.section in selected_text_sections
                    else ""
                )
            else:
                text = decoded if part.section in selected_text_sections else ""
            if text := text.strip():
                body_parts.append(text)
        body = "\n".join(body_parts).strip()
        body_html = "\n".join(html_parts).strip()
        return _normalized_message_record(
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
        if existing:
            existing_sequence_set = ",".join(str(uid) for uid in sorted(existing))
            status, flag_data = self.session.uid(
                "FETCH", existing_sequence_set, "(FLAGS)"
            )
            _require_ok(status, "IMAP FLAGS fetch failed")
            important_signals_by_uid = _imap_important_signals_by_uid(flag_data)
            if frozenset(important_signals_by_uid) != existing:
                raise ConnectionError("IMAP FLAGS fetch returned incomplete UIDs")
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
        messages_match = re.search(rb"\bMESSAGES\s+(\d+)\b", payload, re.I)
        if messages_match is None:
            select_status, select_data = self.session.select(
                mailbox_argument, readonly=True
            )
            _require_ok(select_status, "IMAP readonly folder select failed")
            selected_payload = b" ".join(
                item for item in select_data if isinstance(item, bytes)
            )
            selected_count = re.fullmatch(rb"\s*(\d+)\s*", selected_payload)
            if selected_count is None:
                raise ConnectionError("IMAP readonly folder count is incomplete")
            exists = int(selected_count.group(1))
        else:
            exists = int(messages_match.group(1))
        return ProviderFolderFingerprint(
            uidvalidity=required(b"UIDVALIDITY"),
            uidnext=required(b"UIDNEXT"),
            exists=exists,
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


def _imap_important_signals_by_uid(data: object) -> dict[int, ImportantSignals]:
    if not isinstance(data, (list, tuple)):
        raise ConnectionError("IMAP FLAGS fetch returned invalid data")
    result: dict[int, ImportantSignals] = {}
    for item in data:
        metadata = (
            item[0]
            if isinstance(item, tuple) and item and isinstance(item[0], bytes)
            else item
        )
        if not isinstance(metadata, bytes):
            continue
        match = re.search(rb"\bUID\s+(\d+)\b", metadata, flags=re.IGNORECASE)
        if match is None:
            continue
        uid = int(match.group(1))
        if uid in result:
            raise ConnectionError("IMAP FLAGS fetch returned duplicate UID")
        result[uid] = _imap_important_signals([metadata])
    return result


def _parse_bodystructure(data: object) -> _BodyPart:
    metadata = _fetch_metadata(data, literals=True)
    match = re.search(rb"\bBODYSTRUCTURE\b", metadata, flags=re.IGNORECASE)
    if match is None:
        raise ConnectionError("IMAP BODYSTRUCTURE fetch returned invalid data")
    expression = metadata[match.end() :].lstrip()
    try:
        value, _ = _parse_imap_value(expression, 0)
        return _body_part(value, section="")
    except (IndexError, TypeError, ValueError) as exc:
        raise ConnectionError("IMAP BODYSTRUCTURE fetch returned invalid data") from exc


def _fetch_metadata(data: object, *, literals: bool = False) -> bytes:
    if not isinstance(data, (list, tuple)):
        raise ConnectionError("IMAP FETCH returned invalid data")
    parts: list[bytes] = []
    for item in data:
        if isinstance(item, bytes):
            parts.append(item)
        elif isinstance(item, tuple) and item and isinstance(item[0], bytes):
            if literals:
                if len(item) != 2 or not isinstance(item[1], bytes):
                    raise ConnectionError("IMAP FETCH returned invalid literal data")
                parts.append(item[0] + b"\r\n" + item[1])
            else:
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
    if data[index] == ord("{"):
        end = data.find(b"}", index + 1)
        length = data[index + 1:end]
        if end < 0 or not length.isdigit() or data[end + 1:end + 3] != b"\r\n":
            raise ValueError("invalid IMAP literal")
        start = end + 3
        end = start + int(length)
        if end > len(data):
            raise ValueError("truncated IMAP literal")
        return data[start:end], end
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
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
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
    return html_to_text(value) if part.mime_type == "text/html" else value


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
    charset = part.charset or "utf-8"
    try:
        value = decoded.decode(charset, errors="replace")
    except LookupError:
        # Providers occasionally emit an IANA/legacy charset name that Python
        # does not know.  Keep the message in the bounded batch instead of
        # aborting the whole training observation run.
        value = decoded.decode("utf-8", errors="replace")
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
            return html_to_text(_decode_part(message))
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
        charset = part.get_content_charset() or "utf-8"
        try:
            value = payload.decode(charset, errors="replace")
        except LookupError:
            # Keep one malformed provider charset from aborting the whole
            # bounded observation batch.
            value = payload.decode("utf-8", errors="replace")
    return value.replace("\r\n", "\n").replace("\r", "\n")


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


_IMAP_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _imap_date(value: date) -> str:
    """Format a date as RFC 3501 date-text, independent of the process locale."""

    return f"{value.day:02d}-{_IMAP_MONTHS[value.month - 1]}-{value.year:04d}"


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


def _split_exact_fetch_records(data, requested):
    """Bind complete response records to UID, never parsing literal bytes.

    imaplib may put UID after the literal, so accumulate only record metadata
    until the existing IMAP parser sees the closing attribute list.
    """
    if not isinstance(data, (list, tuple)):
        raise LookupError("email source batch metadata is unavailable")
    result = {}
    record = []
    metadata_parts = []
    for item in data:
        metadata = item[0] if isinstance(item, tuple) and item else item
        if metadata is None and not record:
            continue
        if not isinstance(metadata, bytes):
            raise LookupError("email source batch metadata is invalid")
        record.append(item)
        metadata_parts.append(_fetch_metadata([item], literals=True))
        metadata = b' '.join(metadata_parts)
        sequence, separator, attributes = metadata.partition(b' ')
        if not separator or not sequence.isdigit() or not attributes.startswith(b'('):
            raise LookupError("email source batch record is invalid")
        try:
            values, end = _parse_imap_value(attributes, 0)
        except (IndexError, ValueError):
            # A literal prefix ends before the attribute-list terminator.
            continue
        if attributes[end:].strip():
            raise LookupError("email source batch record has trailing metadata")
        uids = [values[index + 1] for index, token in enumerate(values[:-1])
                if isinstance(token, str) and token.upper() == 'UID']
        if (len(uids) != 1 or type(uids[0]) is not int or
            uids[0] not in requested or uids[0] in result):
            raise LookupError("email source batch UID is missing, unexpected or duplicated")
        result[uids[0]] = record
        record, metadata_parts = [], []
    if record:
        raise LookupError("email source batch record is incomplete")
    return result


def _require_exact_fetch_uid(data: object, expected: bytes) -> None:
    # FETCH literal bodies are never interpreted as response metadata.
    if not isinstance(data, (list, tuple)):
        raise LookupError("email source UID is unavailable")
    if not _split_exact_fetch_records(data, {int(expected)}):
        raise LookupError("email source UID is unavailable")


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
