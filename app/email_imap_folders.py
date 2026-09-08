"""Shared IMAP LIST parsing for readonly and writable provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.email_imap_mailbox import (
    ImapMailboxCodecError,
    decode_imap_mailbox,
    decode_imap_mailbox_token,
)
from app.email_provider_folders import (
    ProviderFolder,
    folder_role_from_special_use_flags,
)


class ImapFolderListError(ValueError):
    """An IMAP LIST response cannot be represented as addressable folders."""


@dataclass(frozen=True)
class ParsedImapFolder:
    name: str
    delimiter: str | None
    flags: frozenset[str]

    @property
    def selectable(self) -> bool:
        return "\\NOSELECT" not in {flag.upper() for flag in self.flags}

    def provider_folder(self) -> ProviderFolder:
        return ProviderFolder(
            provider_folder_id=self.name,
            display_name=self.name,
            role=folder_role_from_special_use_flags(self.flags),
        )


_FLAT_LIST_RESPONSE = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+'
    rb'(?P<delimiter>NIL|"(?:\\.|[^"])*")\s+'
    rb'(?P<name>.+)$'
)
_LITERAL_LIST_RESPONSE = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+'
    rb'(?P<delimiter>NIL|"(?:\\.|[^"])*")\s+'
    rb'\{(?P<size>[0-9]+)\}$'
)


def parse_imap_list_response(data: object) -> tuple[ParsedImapFolder, ...]:
    """Parse imaplib LIST bytes, including literal tuples and empty trailers."""

    if data is None:
        return ()
    if isinstance(data, (bytes, tuple)):
        entries = (data,)
    else:
        try:
            entries = tuple(data)
        except TypeError as exc:
            raise ImapFolderListError("unsupported IMAP folder response") from exc
    parsed: list[ParsedImapFolder] = []
    accepts_trailing_fragment = False
    for entry in entries:
        if isinstance(entry, bytes) and not entry:
            continue
        if isinstance(entry, tuple):
            parsed.append(_parse_literal_entry(entry))
            accepts_trailing_fragment = True
        elif isinstance(entry, bytes):
            if accepts_trailing_fragment and (
                entry[:1] in {b" ", b"\t"} or not entry.startswith(b"(")
            ):
                accepts_trailing_fragment = False
                continue
            parsed.append(_parse_flat_entry(entry))
            accepts_trailing_fragment = False
        else:
            raise ImapFolderListError("unsupported IMAP folder response")
    return tuple(parsed)


def _parse_flat_entry(raw: bytes) -> ParsedImapFolder:
    line = raw.removesuffix(b"\r\n")
    match = _FLAT_LIST_RESPONSE.fullmatch(line)
    if match is None:
        raise ImapFolderListError("unsupported IMAP folder response")
    flags = _flags(match.group("flags"))
    delimiter = _delimiter(match.group("delimiter"))
    try:
        name = decode_imap_mailbox_token(match.group("name"))
    except ImapMailboxCodecError as exc:
        raise ImapFolderListError("malformed IMAP folder response") from exc
    return ParsedImapFolder(name=name, delimiter=delimiter, flags=flags)


def _parse_literal_entry(entry: tuple[object, ...]) -> ParsedImapFolder:
    if len(entry) != 2 or not all(isinstance(part, bytes) for part in entry):
        raise ImapFolderListError("unsupported IMAP folder literal response")
    header, literal = entry
    match = _LITERAL_LIST_RESPONSE.fullmatch(header.removesuffix(b"\r\n"))
    if match is None or int(match.group("size")) != len(literal):
        raise ImapFolderListError("malformed IMAP folder literal response")
    try:
        name = decode_imap_mailbox(literal)
    except ImapMailboxCodecError as exc:
        raise ImapFolderListError("malformed IMAP folder response") from exc
    return ParsedImapFolder(
        name=name,
        delimiter=_delimiter(match.group("delimiter")),
        flags=_flags(match.group("flags")),
    )


def _flags(raw: bytes) -> frozenset[str]:
    try:
        return frozenset(raw.decode("ascii").split())
    except UnicodeDecodeError as exc:
        raise ImapFolderListError("invalid IMAP folder flags") from exc


def _delimiter(raw: bytes) -> str | None:
    if raw.upper() == b"NIL":
        return None
    token = raw[1:-1]
    result = bytearray()
    escaped = False
    for character in token:
        if escaped:
            result.append(character)
            escaped = False
        elif character == 0x5C:
            escaped = True
        elif character == 0x22:
            raise ImapFolderListError("invalid IMAP folder delimiter")
        else:
            result.append(character)
    if escaped:
        raise ImapFolderListError("invalid IMAP folder delimiter")
    try:
        return bytes(result).decode("ascii")
    except UnicodeDecodeError as exc:
        raise ImapFolderListError("invalid IMAP folder delimiter") from exc
