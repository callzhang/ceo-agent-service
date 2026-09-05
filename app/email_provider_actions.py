"""Side-effect boundary for deterministic email provider actions."""

from __future__ import annotations

import email
import email.policy
from hashlib import sha256
import imaplib
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

from app.email_classifier_contracts import DIRECT_ACTIONS, EmailAction
from app.email_store import StoredEmailAction, StoredEmailLocator


@dataclass(frozen=True)
class ProviderActionResult:
    status: Literal["done", "failed"]
    provider_operation: str
    provider_target: str
    provider_result_id: str
    error: str = ""
    retryable: bool = True
    updated_locator: StoredEmailLocator | None = None


@dataclass(frozen=True)
class ProviderMessageState:
    revision: str
    labels: frozenset[str]
    is_read: bool
    archived: bool
    folder: str
    trashed: bool
    locator: StoredEmailLocator | None = None

    def satisfies(
        self,
        action_type: EmailAction,
        parameters: Mapping[str, object],
        *,
        destination_folder: str | None = None,
    ) -> bool:
        if action_type is EmailAction.LABEL:
            return set(parameters["labels"]).issubset(self.labels)
        if action_type is EmailAction.MARK_READ:
            return self.is_read
        if action_type is EmailAction.ARCHIVE:
            return (
                self.folder == destination_folder
                if destination_folder is not None
                else self.archived
            )
        if action_type is EmailAction.MOVE:
            return self.folder == (
                destination_folder or parameters["target_folder"]
            )
        if action_type is EmailAction.TRASH:
            return (
                self.folder == destination_folder
                if destination_folder is not None
                else self.trashed
            )
        raise ValueError(f"unsupported deterministic email action: {action_type.value}")


def _provider_operation(action_type: EmailAction) -> str:
    operations = {
        EmailAction.LABEL: "STORE LABELS",
        EmailAction.MARK_READ: "STORE \\Seen",
        EmailAction.ARCHIVE: "MOVE ARCHIVE",
        EmailAction.MOVE: "MOVE",
        EmailAction.TRASH: "move_to_trash",
    }
    try:
        return operations[action_type]
    except KeyError as exc:
        raise ValueError(
            f"unsupported deterministic email action: {action_type.value}"
        ) from exc


class DeterministicEmailProvider(Protocol):
    def read_state(self, locator: StoredEmailLocator) -> ProviderMessageState: ...

    def apply(
        self,
        locator: StoredEmailLocator,
        action_type: EmailAction,
        parameters: Mapping[str, object],
    ) -> None: ...


class DeterministicEmailActionExecutor:
    supported_operations = frozenset(
        _provider_operation(action) for action in DIRECT_ACTIONS
    )

    def __init__(self, provider: DeterministicEmailProvider):
        self.provider = provider

    def execute(self, action: StoredEmailAction) -> ProviderActionResult:
        operation = _provider_operation(action.action_type)
        try:
            destination = self._resolve_destination(action)
            try:
                current = self.provider.read_state(action.locator)
            except Exception as exc:
                return self._failed(action, "READ", "provider_read_failed", exc)
            if current.satisfies(
                action.action_type,
                action.parameters,
                destination_folder=destination,
            ):
                return ProviderActionResult(
                    status="done",
                    provider_operation="readback_noop",
                    provider_target=action.locator.stable_message_identity,
                    provider_result_id=current.revision,
                    updated_locator=_changed_locator(action.locator, current.locator),
                )
            current_locator = current.locator or action.locator
            try:
                self.provider.apply(
                    current_locator,
                    action.action_type,
                    action.parameters,
                )
            except Exception as exc:
                return self._failed(action, operation, "provider_apply_failed", exc)
            try:
                verified = self.provider.read_state(current_locator)
            except Exception as exc:
                return self._failed(
                    action,
                    operation,
                    "provider_readback_failed",
                    exc,
                )
            if verified.satisfies(
                action.action_type,
                action.parameters,
                destination_folder=destination,
            ):
                return ProviderActionResult(
                    status="done",
                    provider_operation=operation,
                    provider_target=action.locator.stable_message_identity,
                    provider_result_id=verified.revision,
                    updated_locator=_changed_locator(action.locator, verified.locator),
                )
            return ProviderActionResult(
                status="failed",
                provider_operation=operation,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=verified.revision,
                error="provider_readback_mismatch",
            )
        except Exception as exc:
            return self._failed(
                action,
                "RESOLVE FOLDER",
                "provider_destination_failed",
                exc,
            )
        finally:
            close = getattr(self.provider, "close", None)
            if callable(close):
                close()

    def _resolve_destination(self, action: StoredEmailAction) -> str | None:
        if action.action_type not in {
            EmailAction.ARCHIVE,
            EmailAction.MOVE,
            EmailAction.TRASH,
        }:
            return None
        resolver = getattr(self.provider, "resolve_destination", None)
        if not callable(resolver):
            return None
        return str(resolver(action.locator, action.action_type, action.parameters))

    @staticmethod
    def _failed(
        action: StoredEmailAction,
        operation: str,
        prefix: str,
        exc: Exception,
    ) -> ProviderActionResult:
        return ProviderActionResult(
            status="failed",
            provider_operation=operation,
            provider_target=action.locator.stable_message_identity,
            provider_result_id="",
            error=f"{prefix}:{type(exc).__name__}",
            retryable=not isinstance(exc, ImapPermanentProviderError),
        )


def _changed_locator(
    original: StoredEmailLocator,
    observed: StoredEmailLocator | None,
) -> StoredEmailLocator | None:
    if observed is None:
        return None
    coordinates = ("folder", "uidvalidity", "uid")
    if all(getattr(original, field) == getattr(observed, field) for field in coordinates):
        return None
    return observed


class ImapProviderError(RuntimeError):
    """A sanitized IMAP provider failure."""


class ImapPermanentProviderError(ImapProviderError):
    """An IMAP contract failure that cannot succeed unchanged on retry."""


class ImapMoveUnsupported(ImapPermanentProviderError):
    """The server does not advertise atomic UID MOVE."""


class ImapDestinationUnavailable(ImapPermanentProviderError):
    """A configured or special-use destination is missing or ambiguous."""


class ImapReadbackUnsupported(ImapPermanentProviderError):
    """A move cannot produce a stable locator suitable for readback."""


class ImapKeywordUnsupported(ImapPermanentProviderError):
    """A configured label is not a safe IMAP keyword atom."""


class ImapMessageUnavailable(ImapProviderError):
    """The stable message could not be located uniquely."""


@dataclass(frozen=True)
class _ImapMailbox:
    name: str
    flags: frozenset[str]


_LIST_RESPONSE = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+(?:NIL|"(?:\\.|[^"])*")\s+(?P<name>.+)$'
)
_UID_RESPONSE = re.compile(rb"\bUID\s+(?P<uid>[1-9][0-9]*)\b")
_COPYUID_RESPONSE = re.compile(
    rb"\bCOPYUID\s+(?P<uidvalidity>[1-9][0-9]*)\s+"
    rb"(?P<source>[1-9][0-9]*)\s+(?P<destination>[1-9][0-9]*)\b"
)


class ImapDeterministicProvider:
    """One-session IMAP implementation for recoverable deterministic actions."""

    def __init__(self, session: Any, *, account_id: str):
        account_id = account_id.strip()
        if not account_id:
            raise ValueError("account_id must be non-empty")
        self.session = session
        self.account_id = account_id
        self._mailbox_cache: tuple[_ImapMailbox, ...] | None = None
        self._moved_locators: dict[str, StoredEmailLocator] = {}
        self._closed = False

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
    ) -> "ImapDeterministicProvider":
        session = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        try:
            status, _ = session.login(username, password)
            _require_ok(status, "IMAP login failed")
        except Exception:
            _logout_or_shutdown(session)
            raise
        return cls(session, account_id=account_id)

    def resolve_destination(
        self,
        locator: StoredEmailLocator,
        action_type: EmailAction,
        parameters: Mapping[str, object],
    ) -> str:
        self._validate_locator(locator)
        capabilities = self._capabilities()
        if "MOVE" not in capabilities:
            raise ImapMoveUnsupported("IMAP UID MOVE is unavailable")
        if locator.rfc_message_id is None and "UIDPLUS" not in capabilities:
            raise ImapReadbackUnsupported("move locator readback is unavailable")
        mailboxes = self._mailboxes()
        if action_type is EmailAction.MOVE:
            target = str(parameters["target_folder"]).strip()
            matches = [mailbox.name for mailbox in mailboxes if mailbox.name == target]
        else:
            special_use = (
                ("\\ARCHIVE", "\\ALL")
                if action_type is EmailAction.ARCHIVE
                else ("\\TRASH",)
            )
            matches = []
            for flag in special_use:
                matches = [
                    mailbox.name
                    for mailbox in mailboxes
                    if flag in {item.upper() for item in mailbox.flags}
                ]
                if matches:
                    break
        if len(matches) != 1:
            raise ImapDestinationUnavailable("IMAP destination is unavailable")
        return matches[0]

    def read_state(self, locator: StoredEmailLocator) -> ProviderMessageState:
        self._validate_locator(locator)
        candidates = [self._moved_locators.get(locator.stable_message_identity), locator]
        seen: set[tuple[str, int, int]] = set()
        for candidate in candidates:
            if candidate is None:
                continue
            key = (candidate.folder, candidate.uidvalidity, candidate.uid)
            if key in seen:
                continue
            seen.add(key)
            state = self._read_exact(candidate, expected_message_id=locator.rfc_message_id)
            if state is not None:
                return state
        if locator.rfc_message_id is None:
            raise ImapReadbackUnsupported("stable Message-ID readback is unavailable")
        matches: list[ProviderMessageState] = []
        for mailbox in self._mailboxes():
            _uidvalidity = self._select(mailbox.name, readonly=True)
            status, data = self.session.uid(
                "SEARCH",
                None,
                "HEADER",
                "Message-ID",
                locator.rfc_message_id,
            )
            _require_ok(status, "IMAP Message-ID search failed")
            for uid in _search_uids(data):
                state = self._read_exact(
                    StoredEmailLocator(
                        account_id=locator.account_id,
                        folder=mailbox.name,
                        uidvalidity=_uidvalidity,
                        uid=uid,
                        rfc_message_id=locator.rfc_message_id,
                        thread_id=locator.thread_id,
                        stable_message_identity=locator.stable_message_identity,
                    ),
                    expected_message_id=locator.rfc_message_id,
                )
                if state is not None:
                    matches.append(state)
        if len(matches) != 1:
            raise ImapMessageUnavailable("message lookup was missing or ambiguous")
        return matches[0]

    def apply(
        self,
        locator: StoredEmailLocator,
        action_type: EmailAction,
        parameters: Mapping[str, object],
    ) -> None:
        self._validate_locator(locator)
        if action_type is EmailAction.LABEL:
            labels = tuple(str(label) for label in parameters["labels"])
            if any(not _is_imap_keyword(label) for label in labels):
                raise ImapKeywordUnsupported("label is not an IMAP keyword atom")
            self._uid_store(locator, "(" + " ".join(labels) + ")")
            return
        if action_type is EmailAction.MARK_READ:
            self._uid_store(locator, "(\\Seen)")
            return
        if action_type not in {
            EmailAction.ARCHIVE,
            EmailAction.MOVE,
            EmailAction.TRASH,
        }:
            raise ValueError(f"unsupported deterministic email action: {action_type.value}")
        destination = self.resolve_destination(locator, action_type, parameters)
        selected_uidvalidity = self._select(locator.folder, readonly=False)
        if selected_uidvalidity != locator.uidvalidity:
            raise ImapMessageUnavailable("message UIDVALIDITY changed before move")
        status, data = self.session.uid(
            "MOVE",
            str(locator.uid),
            _imap_mailbox_argument(destination),
        )
        _require_ok(status, "IMAP UID MOVE failed")
        copied = _copyuid(data, source_uid=locator.uid)
        if copied is not None:
            destination_uidvalidity, destination_uid = copied
            self._moved_locators[locator.stable_message_identity] = StoredEmailLocator(
                account_id=locator.account_id,
                folder=destination,
                uidvalidity=destination_uidvalidity,
                uid=destination_uid,
                rfc_message_id=locator.rfc_message_id,
                thread_id=locator.thread_id,
                stable_message_identity=locator.stable_message_identity,
            )
        elif locator.rfc_message_id is None:
            raise ImapReadbackUnsupported("UID MOVE omitted stable locator readback")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _logout_or_shutdown(self.session)

    def _uid_store(self, locator: StoredEmailLocator, flags: str) -> None:
        selected_uidvalidity = self._select(locator.folder, readonly=False)
        if selected_uidvalidity != locator.uidvalidity:
            raise ImapMessageUnavailable("message UIDVALIDITY changed before store")
        status, _ = self.session.uid(
            "STORE",
            str(locator.uid),
            "+FLAGS.SILENT",
            flags,
        )
        _require_ok(status, "IMAP UID STORE failed")

    def _read_exact(
        self,
        locator: StoredEmailLocator,
        *,
        expected_message_id: str | None,
    ) -> ProviderMessageState | None:
        try:
            uidvalidity = self._select(locator.folder, readonly=True)
        except ImapProviderError:
            return None
        if uidvalidity != locator.uidvalidity:
            return None
        status, data = self.session.uid(
            "FETCH",
            str(locator.uid),
            "(UID FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
        )
        _require_ok(status, "IMAP UID FETCH failed")
        response = _response_bytes(data)
        uid_match = _UID_RESPONSE.search(response)
        if uid_match is None or int(uid_match.group("uid")) != locator.uid:
            return None
        observed_message_id = _fetch_message_id(data)
        if expected_message_id is not None and observed_message_id != expected_message_id:
            return None
        raw_flags = imaplib.ParseFlags(response)
        labels = frozenset(
            flag.decode("ascii")
            for flag in raw_flags
            if not flag.startswith(b"\\")
        )
        mailbox_flags = self._cached_mailbox_flags(locator.folder)
        observed_locator = StoredEmailLocator(
            account_id=locator.account_id,
            folder=locator.folder,
            uidvalidity=locator.uidvalidity,
            uid=locator.uid,
            rfc_message_id=observed_message_id or locator.rfc_message_id,
            thread_id=locator.thread_id,
            stable_message_identity=locator.stable_message_identity,
        )
        revision_payload = "\x00".join(
            (
                locator.account_id,
                locator.folder,
                str(locator.uidvalidity),
                str(locator.uid),
                *sorted(flag.decode("ascii") for flag in raw_flags),
            )
        )
        return ProviderMessageState(
            revision="imap:" + sha256(revision_payload.encode("utf-8")).hexdigest(),
            labels=labels,
            is_read=b"\\Seen" in raw_flags,
            archived=bool({"\\ARCHIVE", "\\ALL"} & mailbox_flags),
            folder=locator.folder,
            trashed="\\TRASH" in mailbox_flags,
            locator=observed_locator,
        )

    def _select(self, mailbox: str, *, readonly: bool) -> int:
        status, _ = self.session.select(
            _imap_mailbox_argument(mailbox),
            readonly=readonly,
        )
        _require_ok(status, "IMAP mailbox select failed")
        response = self.session.response("UIDVALIDITY")
        values = response[1] if isinstance(response, tuple) and len(response) > 1 else ()
        raw = next((value for value in values or () if value), None)
        try:
            uidvalidity = int(raw)
        except (TypeError, ValueError) as exc:
            raise ImapReadbackUnsupported("IMAP UIDVALIDITY is unavailable") from exc
        if uidvalidity <= 0:
            raise ImapReadbackUnsupported("IMAP UIDVALIDITY is invalid")
        return uidvalidity

    def _capabilities(self) -> frozenset[str]:
        raw = getattr(self.session, "capabilities", ())
        return frozenset(
            (item.decode("ascii") if isinstance(item, bytes) else str(item)).upper()
            for item in raw
        )

    def _mailboxes(self) -> tuple[_ImapMailbox, ...]:
        if self._mailbox_cache is not None:
            return self._mailbox_cache
        status, data = self.session.list()
        _require_ok(status, "IMAP folder discovery failed")
        mailboxes = tuple(_parse_mailbox(item) for item in data or ())
        selectable = tuple(
            mailbox
            for mailbox in mailboxes
            if "\\NOSELECT" not in {flag.upper() for flag in mailbox.flags}
        )
        names = [mailbox.name for mailbox in selectable]
        if not selectable or len(names) != len(set(names)):
            raise ImapDestinationUnavailable("IMAP folder list is ambiguous")
        self._mailbox_cache = selectable
        return selectable

    def _cached_mailbox_flags(self, folder: str) -> frozenset[str]:
        if self._mailbox_cache is None:
            return frozenset()
        for mailbox in self._mailbox_cache:
            if mailbox.name == folder:
                return frozenset(flag.upper() for flag in mailbox.flags)
        return frozenset()

    def _validate_locator(self, locator: StoredEmailLocator) -> None:
        if locator.account_id != self.account_id:
            raise ImapPermanentProviderError("IMAP account locator mismatch")


def _parse_mailbox(raw: object) -> _ImapMailbox:
    if not isinstance(raw, bytes):
        raise ImapDestinationUnavailable("unsupported IMAP folder response")
    match = _LIST_RESPONSE.fullmatch(raw.strip())
    if match is None:
        raise ImapDestinationUnavailable("unsupported IMAP folder response")
    try:
        flags = frozenset(match.group("flags").decode("ascii").split())
        name = _decode_imap_quoted(match.group("name"))
    except UnicodeDecodeError as exc:
        raise ImapDestinationUnavailable("non-ASCII IMAP folder is unsupported") from exc
    if not name:
        raise ImapDestinationUnavailable("blank IMAP folder is unsupported")
    return _ImapMailbox(name=name, flags=flags)


def _decode_imap_quoted(raw: bytes) -> str:
    raw = raw.strip()
    if raw.startswith(b'"'):
        if not raw.endswith(b'"'):
            raise ImapDestinationUnavailable("malformed IMAP folder response")
        raw = raw[1:-1]
        raw = raw.replace(b"\\\\", b"\\").replace(b'\\"', b'"')
    elif b" " in raw:
        raise ImapDestinationUnavailable("unquoted IMAP folder is malformed")
    return raw.decode("ascii")


def _response_bytes(data: object) -> bytes:
    parts: list[bytes] = []
    for item in data or ():
        if isinstance(item, bytes):
            parts.append(item)
        elif isinstance(item, tuple):
            parts.extend(part for part in item if isinstance(part, bytes))
    return b" ".join(parts)


def _fetch_message_id(data: object) -> str | None:
    payloads = [
        item[1]
        for item in data or ()
        if isinstance(item, tuple)
        and len(item) > 1
        and isinstance(item[1], bytes)
        and item[1]
    ]
    if not payloads:
        return None
    parsed = email.message_from_bytes(b"\r\n".join(payloads), policy=email.policy.default)
    value = str(parsed.get("Message-ID") or "").strip()
    return value or None


def _search_uids(data: object) -> tuple[int, ...]:
    response = _response_bytes(data).strip()
    if not response:
        return ()
    try:
        values = tuple(int(value) for value in response.split())
    except ValueError as exc:
        raise ImapReadbackUnsupported("invalid IMAP UID search response") from exc
    if any(value <= 0 for value in values):
        raise ImapReadbackUnsupported("invalid IMAP UID search response")
    return values


def _copyuid(data: object, *, source_uid: int) -> tuple[int, int] | None:
    match = _COPYUID_RESPONSE.search(_response_bytes(data))
    if match is None:
        return None
    if int(match.group("source")) != source_uid:
        raise ImapReadbackUnsupported("IMAP COPYUID source mismatch")
    return int(match.group("uidvalidity")), int(match.group("destination"))


def _is_imap_keyword(value: str) -> bool:
    if not value or not value.isascii():
        return False
    atom_specials = frozenset("(){ %*\"\\]")
    return all("!" <= character <= "~" and character not in atom_specials for character in value)


def _imap_mailbox_argument(value: str) -> str:
    if _is_imap_keyword(value):
        return value
    if not value or not value.isascii() or any(character in "\r\n\x00" for character in value):
        raise ImapDestinationUnavailable("unsupported IMAP folder name")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _require_ok(status: object, message: str) -> None:
    if str(status).upper() != "OK":
        raise ImapProviderError(message)


def _logout_or_shutdown(session: Any) -> None:
    try:
        status, _ = session.logout()
    except Exception:
        status = ""
    if str(status).upper() not in {"BYE", "OK"}:
        shutdown = getattr(session, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass
