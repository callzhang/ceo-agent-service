"""Side-effect boundary for deterministic email provider actions."""

from __future__ import annotations

import email
import email.policy
from hashlib import sha256
import imaplib
import re
import ssl
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol

from app.email_classifier_contracts import (
    DIRECT_ACTIONS,
    EmailAction,
    EmailProviderLocator,
)
from app.email_imap_mailbox import (
    ImapFetchFlagsError,
    ImapMailboxCodecError,
    encode_imap_mailbox_argument,
    parse_imap_flag_list,
    parse_imap_fetch_flags,
)
from app.email_imap_folders import (
    ImapFolderListError,
    ParsedImapFolder,
    parse_imap_list_response,
)
from app.email_store import StoredEmailAction, StoredEmailLocator
from app.email_provider_folders import ProviderFolder


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
    important_signal_names: frozenset[str] = frozenset()
    required_important_signal_names: frozenset[str] = frozenset()
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
        if action_type is EmailAction.FLAG_IMPORTANT:
            return bool(self.required_important_signal_names) and (
                self.required_important_signal_names <= self.important_signal_names
            )
        raise ValueError(f"unsupported deterministic email action: {action_type.value}")


def _provider_operation(action_type: EmailAction) -> str:
    operations = {
        EmailAction.LABEL: "STORE LABELS",
        EmailAction.MARK_READ: "STORE \\Seen",
        EmailAction.ARCHIVE: "MOVE ARCHIVE",
        EmailAction.MOVE: "MOVE",
        EmailAction.TRASH: "move_to_trash",
        EmailAction.FLAG_IMPORTANT: "STORE IMPORTANT",
    }
    try:
        return operations[action_type]
    except KeyError as exc:
        raise ValueError(
            f"unsupported deterministic email action: {action_type.value}"
        ) from exc


class DeterministicEmailProvider(Protocol):
    def list_folders(self) -> tuple[ProviderFolder, ...]: ...

    def create_folder_exact(self, name: str) -> None: ...

    def read_state(
        self,
        locator: StoredEmailLocator,
        *,
        action_type: EmailAction,
    ) -> ProviderMessageState: ...

    def apply(
        self,
        locator: StoredEmailLocator,
        action_type: EmailAction,
        parameters: Mapping[str, object],
        *,
        observed_state: ProviderMessageState,
    ) -> StoredEmailLocator | None: ...


class DeterministicEmailActionExecutor:
    supported_operations = frozenset(
        _provider_operation(action) for action in DIRECT_ACTIONS
    )

    def __init__(
        self,
        provider: DeterministicEmailProvider,
        *,
        readback_provider_factory: Callable[[], DeterministicEmailProvider] | None = None,
    ):
        self.provider = provider
        self._readback_provider_factory = readback_provider_factory

    def execute(self, action: StoredEmailAction) -> ProviderActionResult:
        operation = _provider_operation(action.action_type)
        readback_provider: DeterministicEmailProvider | None = None
        provider_closed = False
        try:
            destination = self._resolve_destination(action)
            try:
                current = self.provider.read_state(
                    action.locator,
                    action_type=action.action_type,
                )
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
                applied_locator = self.provider.apply(
                    current_locator,
                    action.action_type,
                    action.parameters,
                    observed_state=current,
                )
            except Exception as exc:
                return self._failed(action, operation, "provider_apply_failed", exc)
            readback_locator = applied_locator or current_locator
            try:
                if self._readback_provider_factory is not None:
                    _close_provider(self.provider)
                    provider_closed = True
                    readback_provider = self._readback_provider_factory()
                verification_provider = readback_provider or self.provider
                verified = verification_provider.read_state(
                    readback_locator,
                    action_type=action.action_type,
                )
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
            if readback_provider is not None:
                _close_provider(readback_provider)
            if not provider_closed:
                _close_provider(self.provider)

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


def _close_provider(provider: object) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


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


class ImapPermanentFlagsUnsupported(ImapPermanentProviderError):
    """The selected mailbox cannot persist the requested flag."""


class ImapMessageUnavailable(ImapProviderError):
    """The stable message could not be located uniquely."""


_UID_RESPONSE = re.compile(rb"\bUID\s+(?P<uid>[1-9][0-9]*)\b")
_COPYUID_RESPONSE = re.compile(
    rb"^(?P<uidvalidity>[1-9][0-9]*)\s+"
    rb"(?P<source>\S+)\s+(?P<destination>\S+)$"
)
_PERMANENTFLAGS_RESPONSE = re.compile(rb"^\((?P<flags>[^()]*)\)$")


@dataclass(frozen=True)
class _PermanentFlagsState:
    advertised: bool
    flags: frozenset[str]


class ImapDeterministicProvider:
    """One-session IMAP implementation for recoverable deterministic actions."""

    def __init__(
        self,
        session: Any,
        *,
        account_id: str,
        capabilities: frozenset[str] | None = None,
        move_mode: Literal["move", "copy_as_move"] = "move",
    ):
        account_id = account_id.strip()
        if not account_id:
            raise ValueError("account_id must be non-empty")
        self.session = session
        self.account_id = account_id
        if move_mode not in {"move", "copy_as_move"}:
            raise ValueError("unsupported IMAP move mode")
        self.move_mode = move_mode
        self._authenticated_capabilities = capabilities
        self._mailbox_cache: tuple[ParsedImapFolder, ...] | None = None
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
        move_mode: Literal["move", "copy_as_move"] = "move",
    ) -> "ImapDeterministicProvider":
        session = imaplib.IMAP4_SSL(
            host,
            port,
            ssl_context=ssl.create_default_context(),
            timeout=timeout,
        )
        try:
            status, _ = session.login(username, password)
            _require_ok(status, "IMAP login failed")
            status, data = session.capability()
            _require_ok(status, "IMAP capability refresh failed")
            capabilities = _capability_tokens(data)
        except Exception:
            _logout_or_shutdown(session)
            raise
        return cls(
            session,
            account_id=account_id,
            capabilities=capabilities,
            move_mode=move_mode,
        )

    def resolve_destination(
        self,
        locator: StoredEmailLocator,
        action_type: EmailAction,
        parameters: Mapping[str, object],
    ) -> str:
        self._validate_locator(locator)
        capabilities = self._capabilities()
        if "MOVE" not in capabilities and self.move_mode != "copy_as_move":
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

    def list_folders(self) -> tuple[ProviderFolder, ...]:
        return tuple(mailbox.provider_folder() for mailbox in self._mailboxes())

    def create_folder_exact(self, name: str) -> None:
        try:
            status, _ = self.session.create(_imap_mailbox_argument(name))
            _require_ok(status, "IMAP folder creation failed")
        finally:
            self._mailbox_cache = None

    def read_state(
        self,
        locator: StoredEmailLocator,
        *,
        action_type: EmailAction,
    ) -> ProviderMessageState:
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
            state = self._read_exact(
                candidate,
                expected_message_id=locator.rfc_message_id,
                action_type=action_type,
            )
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
                    action_type=action_type,
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
        *,
        observed_state: ProviderMessageState,
    ) -> StoredEmailLocator | None:
        self._validate_locator(locator)
        if action_type is EmailAction.LABEL:
            labels = tuple(str(label) for label in parameters["labels"])
            if any(not _is_imap_keyword(label) for label in labels):
                raise ImapKeywordUnsupported("label is not an IMAP keyword atom")
            self._uid_store(locator, labels, wildcard_permits=True)
            return None
        if action_type is EmailAction.MARK_READ:
            self._uid_store(locator, ("\\Seen",), wildcard_permits=False)
            return None
        if action_type is EmailAction.FLAG_IMPORTANT:
            missing_flags = (
                observed_state.required_important_signal_names
                - observed_state.important_signal_names
            )
            self._uid_store(
                locator,
                tuple(
                    flag
                    for flag in ("\\Flagged", "$Important")
                    if flag in missing_flags
                ),
                wildcard_permits=False,
                allow_absent_standard_flagged=True,
            )
            return None
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
        command = "MOVE" if "MOVE" in self._capabilities() else "COPY"
        status, _ = self.session.uid(
            command,
            str(locator.uid),
            _imap_mailbox_argument(destination),
        )
        _require_ok(status, f"IMAP UID {command} failed")
        copied = _copyuid(self.session.response("COPYUID"), source_uid=locator.uid)
        if copied is not None:
            destination_uidvalidity, destination_uid = copied
            moved_locator = StoredEmailLocator(
                account_id=locator.account_id,
                folder=destination,
                uidvalidity=destination_uidvalidity,
                uid=destination_uid,
                rfc_message_id=locator.rfc_message_id,
                thread_id=locator.thread_id,
                stable_message_identity=locator.stable_message_identity,
            )
            self._moved_locators[locator.stable_message_identity] = moved_locator
            return moved_locator
        elif locator.rfc_message_id is None:
            raise ImapReadbackUnsupported("UID MOVE omitted stable locator readback")
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _logout_or_shutdown(self.session)

    def _uid_store(
        self,
        locator: StoredEmailLocator,
        flags: tuple[str, ...],
        *,
        wildcard_permits: bool,
        allow_absent_standard_flagged: bool = False,
    ) -> None:
        selected_uidvalidity = self._select(locator.folder, readonly=False)
        if selected_uidvalidity != locator.uidvalidity:
            raise ImapMessageUnavailable("message UIDVALIDITY changed before store")
        permanent_flags = self._permanent_flags()
        if permanent_flags.advertised:
            normalized = {flag.casefold() for flag in permanent_flags.flags}
        elif allow_absent_standard_flagged and all(
            flag.casefold() == "\\flagged" for flag in flags
        ):
            normalized = {"\\flagged"}
        else:
            raise ImapPermanentFlagsUnsupported(
                "IMAP PERMANENTFLAGS is unavailable"
            )
        wildcard = "\\*".casefold() in normalized
        if any(
            flag.casefold() not in normalized and not (wildcard_permits and wildcard)
            for flag in flags
        ):
            raise ImapPermanentFlagsUnsupported(
                "IMAP mailbox does not persist the requested flag"
            )
        status, _ = self.session.uid(
            "STORE",
            str(locator.uid),
            "+FLAGS.SILENT",
            "(" + " ".join(flags) + ")",
        )
        _require_ok(status, "IMAP UID STORE failed")

    def _permanent_flags(self) -> _PermanentFlagsState:
        response = self.session.response("PERMANENTFLAGS")
        if response is None:
            return _PermanentFlagsState(False, frozenset())
        if not isinstance(response, tuple) or len(response) != 2:
            raise ImapPermanentFlagsUnsupported("invalid IMAP PERMANENTFLAGS")
        code, values = response
        normalized_code = str(code).upper() if code is not None else None
        if values is None and code is None:
            return _PermanentFlagsState(False, frozenset())
        if (
            normalized_code in {None, "PERMANENTFLAGS"}
            and isinstance(values, (list, tuple))
            and tuple(values) == (None,)
        ):
            return _PermanentFlagsState(False, frozenset())
        if (
            normalized_code != "PERMANENTFLAGS"
            or not isinstance(values, (list, tuple))
        ):
            raise ImapPermanentFlagsUnsupported("invalid IMAP PERMANENTFLAGS")
        raw_values = tuple(value for value in values if value is not None)
        if len(raw_values) != 1:
            raise ImapPermanentFlagsUnsupported("invalid IMAP PERMANENTFLAGS")
        raw = raw_values[0]
        if not isinstance(raw, bytes):
            raise ImapPermanentFlagsUnsupported("invalid IMAP PERMANENTFLAGS")
        match = _PERMANENTFLAGS_RESPONSE.fullmatch(raw.strip())
        if match is None:
            raise ImapPermanentFlagsUnsupported("invalid IMAP PERMANENTFLAGS")
        try:
            flags = frozenset(parse_imap_flag_list(match.group("flags")))
        except ImapFetchFlagsError as exc:
            raise ImapPermanentFlagsUnsupported(
                "invalid IMAP PERMANENTFLAGS"
            ) from exc
        return _PermanentFlagsState(True, flags)

    def _read_exact(
        self,
        locator: StoredEmailLocator,
        *,
        expected_message_id: str | None,
        action_type: EmailAction,
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
        try:
            raw_flags = parse_imap_fetch_flags(data)
        except ImapFetchFlagsError as exc:
            raise ImapProviderError("invalid IMAP FETCH FLAGS metadata") from exc
        labels = frozenset(
            flag
            for flag in raw_flags
            if not flag.startswith("\\")
        )
        normalized_raw_flags = {flag.casefold() for flag in raw_flags}
        important_signal_names = frozenset(
            canonical
            for normalized, canonical in (
                ("\\flagged", "\\Flagged"),
                ("$important", "$Important"),
            )
            if normalized in normalized_raw_flags
        )
        required_important_signal_names: set[str] = set()
        if action_type is EmailAction.FLAG_IMPORTANT:
            permanent_flags = self._permanent_flags()
            required_important_signal_names.add("\\Flagged")
            if permanent_flags.advertised and "$important" in {
                flag.casefold() for flag in permanent_flags.flags
            }:
                required_important_signal_names.add("$Important")
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
                *sorted(raw_flags),
            )
        )
        return ProviderMessageState(
            revision="imap:" + sha256(revision_payload.encode("utf-8")).hexdigest(),
            labels=labels,
            is_read="\\seen" in normalized_raw_flags,
            archived=bool({"\\ARCHIVE", "\\ALL"} & mailbox_flags),
            folder=locator.folder,
            trashed="\\TRASH" in mailbox_flags,
            important_signal_names=important_signal_names,
            required_important_signal_names=frozenset(
                required_important_signal_names
            ),
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
        if self._authenticated_capabilities is not None:
            return self._authenticated_capabilities
        raw = getattr(self.session, "capabilities", ())
        return frozenset(
            (item.decode("ascii") if isinstance(item, bytes) else str(item)).upper()
            for item in raw
        )

    def _mailboxes(self) -> tuple[ParsedImapFolder, ...]:
        if self._mailbox_cache is not None:
            return self._mailbox_cache
        status, data = self.session.list()
        _require_ok(status, "IMAP folder discovery failed")
        try:
            mailboxes = parse_imap_list_response(data)
        except ImapFolderListError as exc:
            raise ImapDestinationUnavailable("invalid IMAP folder list") from exc
        selectable = tuple(mailbox for mailbox in mailboxes if mailbox.selectable)
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


def _parse_mailbox(raw: object) -> ParsedImapFolder:
    try:
        parsed = parse_imap_list_response((raw,))
    except ImapFolderListError as exc:
        raise ImapDestinationUnavailable("malformed IMAP folder response") from exc
    if len(parsed) != 1:
        raise ImapDestinationUnavailable("malformed IMAP folder response")
    return parsed[0]


def _response_bytes(data: object) -> bytes:
    parts: list[bytes] = []
    for item in data or ():
        if isinstance(item, bytes):
            parts.append(item)
        elif isinstance(item, tuple):
            parts.extend(part for part in item if isinstance(part, bytes))
    return b" ".join(parts)


def _capability_tokens(data: object) -> frozenset[str]:
    raw = _response_bytes(data).strip()
    try:
        capabilities = frozenset(item.decode("ascii").upper() for item in raw.split())
    except UnicodeDecodeError as exc:
        raise ImapProviderError("invalid IMAP capability response") from exc
    if not capabilities:
        raise ImapProviderError("empty IMAP capability response")
    return capabilities


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
    return EmailProviderLocator.normalize_rfc_message_id(
        str(parsed.get("Message-ID") or "")
    )


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


def _copyuid(response: object, *, source_uid: int) -> tuple[int, int] | None:
    if not isinstance(response, tuple) or len(response) != 2:
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    code, values = response
    raw_values = tuple(value for value in values or () if value is not None)
    if str(code).upper() != "COPYUID":
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    if not raw_values:
        return None
    if len(raw_values) != 1:
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    raw = raw_values[0]
    if not isinstance(raw, bytes):
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    match = _COPYUID_RESPONSE.fullmatch(raw.strip())
    if match is None:
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    parsed_source = _single_uid_set(match.group("source"))
    parsed_destination = _single_uid_set(match.group("destination"))
    if parsed_source != source_uid:
        raise ImapReadbackUnsupported("IMAP COPYUID source mismatch")
    return int(match.group("uidvalidity")), parsed_destination


def _single_uid_set(raw: bytes) -> int:
    """Accept only a UID set whose expansion contains exactly one positive UID."""

    if b"," in raw or b"*" in raw:
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    parts = raw.split(b":")
    if len(parts) not in {1, 2} or any(
        not part or not part.isascii() or not part.isdigit() for part in parts
    ):
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    values = tuple(int(part) for part in parts)
    if any(value <= 0 for value in values) or (
        len(values) == 2 and values[0] != values[1]
    ):
        raise ImapReadbackUnsupported("invalid IMAP COPYUID response")
    return values[0]


def _is_imap_keyword(value: str) -> bool:
    if not value or not value.isascii():
        return False
    atom_specials = frozenset("(){ %*\"\\]")
    return all("!" <= character <= "~" and character not in atom_specials for character in value)


def _imap_mailbox_argument(value: str) -> str:
    try:
        return encode_imap_mailbox_argument(value)
    except ImapMailboxCodecError as exc:
        raise ImapDestinationUnavailable("unsupported IMAP folder name") from exc


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
