"""Strict IMAP mailbox and bounded FETCH metadata parsing helpers."""

from __future__ import annotations

import base64
import binascii
import re


class ImapMailboxCodecError(ValueError):
    """A mailbox name cannot be represented safely on the IMAP wire."""


class ImapFetchFlagsError(ValueError):
    """A FETCH response contains malformed or ambiguous FLAGS metadata."""


_IMAP_ATOM_SPECIALS = frozenset(b"(){ %*\"\\]")
_FETCH_FLAGS_TOKEN = re.compile(rb"FLAGS", flags=re.IGNORECASE)
_FETCH_FLAGS_LIST = re.compile(rb" +\((?P<flags>[^()]*)\)")


def _is_imap_atom_octet(value: int) -> bool:
    return 0x21 <= value <= 0x7E and value not in _IMAP_ATOM_SPECIALS


def is_valid_imap_flag(value: str) -> bool:
    """Return whether one ASCII value is a legal IMAP flag token."""

    if not isinstance(value, str) or not value:
        return False
    if value == "\\*":
        return True
    atom = value[1:] if value.startswith("\\") else value
    return bool(atom) and all(_is_imap_atom_octet(ord(character)) for character in atom)


def parse_imap_flag_list(value: bytes) -> tuple[str, ...]:
    """Parse an inner flag-list using exact single-space separators."""

    if not isinstance(value, bytes):
        raise ImapFetchFlagsError("invalid IMAP flag list")
    if value == b"":
        return ()
    if value.startswith(b" ") or value.endswith(b" ") or b"  " in value:
        raise ImapFetchFlagsError("invalid IMAP flag list")
    try:
        flags = tuple(item.decode("ascii") for item in value.split(b" "))
    except UnicodeDecodeError as exc:
        raise ImapFetchFlagsError("invalid IMAP flag list") from exc
    if any(not is_valid_imap_flag(flag) for flag in flags):
        raise ImapFetchFlagsError("invalid IMAP flag list")
    return flags


def parse_imap_fetch_flags(data: object) -> tuple[str, ...]:
    """Parse one FLAGS attribute without examining FETCH literal payloads."""

    if not isinstance(data, (list, tuple)):
        raise ImapFetchFlagsError("invalid IMAP FETCH response")
    metadata_parts: list[bytes] = []
    for item in data:
        if isinstance(item, bytes):
            metadata_parts.append(item)
        elif isinstance(item, tuple) and item and isinstance(item[0], bytes):
            metadata_parts.append(item[0])

    bounded_tokens = [
        (part, match)
        for part in metadata_parts
        for match in _FETCH_FLAGS_TOKEN.finditer(part)
        if (match.start() == 0 or not _is_imap_atom_octet(part[match.start() - 1]))
        and (match.end() == len(part) or not _is_imap_atom_octet(part[match.end()]))
    ]
    if not bounded_tokens:
        return ()
    if len(bounded_tokens) != 1:
        raise ImapFetchFlagsError("invalid IMAP FETCH FLAGS metadata")
    part, token = bounded_tokens[0]
    flag_list = _FETCH_FLAGS_LIST.match(part, token.end())
    if flag_list is None:
        raise ImapFetchFlagsError("invalid IMAP FETCH FLAGS metadata")
    return parse_imap_flag_list(flag_list.group("flags"))


def encode_imap_mailbox(value: str) -> str:
    """Encode one Unicode mailbox name using RFC 3501 modified UTF-7."""

    if not isinstance(value, str) or not value:
        raise ImapMailboxCodecError("invalid IMAP mailbox")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ImapMailboxCodecError("invalid IMAP mailbox")

    result: list[str] = []
    encoded_run: list[str] = []

    def flush_encoded_run() -> None:
        if not encoded_run:
            return
        try:
            raw = "".join(encoded_run).encode("utf-16-be")
        except UnicodeEncodeError as exc:
            raise ImapMailboxCodecError("invalid IMAP mailbox") from exc
        payload = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        result.append(f"&{payload}-")
        encoded_run.clear()

    for character in value:
        if " " <= character <= "~":
            flush_encoded_run()
            result.append("&-" if character == "&" else character)
        else:
            encoded_run.append(character)
    flush_encoded_run()
    return "".join(result)


def decode_imap_mailbox(value: bytes) -> str:
    """Decode one strict, canonical modified UTF-7 mailbox wire value."""

    if not isinstance(value, bytes) or not value:
        raise ImapMailboxCodecError("invalid IMAP mailbox")
    try:
        wire = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ImapMailboxCodecError("invalid IMAP mailbox") from exc
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in wire):
        raise ImapMailboxCodecError("invalid IMAP mailbox")

    result: list[str] = []
    index = 0
    while index < len(wire):
        if wire[index] != "&":
            result.append(wire[index])
            index += 1
            continue
        end = wire.find("-", index + 1)
        if end < 0:
            raise ImapMailboxCodecError("invalid IMAP mailbox")
        payload = wire[index + 1 : end]
        if not payload:
            result.append("&")
            index = end + 1
            continue
        if any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+,"
            for character in payload
        ):
            raise ImapMailboxCodecError("invalid IMAP mailbox")
        standard_payload = payload.replace(",", "/")
        if len(standard_payload) % 4 == 1:
            raise ImapMailboxCodecError("invalid IMAP mailbox")
        padded = standard_payload + "=" * (-len(standard_payload) % 4)
        try:
            raw = base64.b64decode(padded, validate=True)
            decoded = raw.decode("utf-16-be")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ImapMailboxCodecError("invalid IMAP mailbox") from exc
        if not decoded or any(" " <= character <= "~" for character in decoded):
            raise ImapMailboxCodecError("invalid IMAP mailbox")
        if encode_imap_mailbox(decoded) != wire[index : end + 1]:
            raise ImapMailboxCodecError("invalid IMAP mailbox")
        result.append(decoded)
        index = end + 1
    return "".join(result)


def encode_imap_mailbox_argument(value: str) -> str:
    """Encode and quote a mailbox name for an imaplib command argument."""

    wire = encode_imap_mailbox(value)
    if _is_imap_atom(wire):
        return wire
    escaped = wire.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def decode_imap_mailbox_token(raw: bytes) -> str:
    """Unquote and decode the mailbox-name token from one LIST response."""

    if not isinstance(raw, bytes):
        raise ImapMailboxCodecError("invalid IMAP mailbox")
    token = raw.strip()
    if not token:
        raise ImapMailboxCodecError("invalid IMAP mailbox")
    if token.startswith(b'"'):
        if len(token) < 2 or not token.endswith(b'"'):
            raise ImapMailboxCodecError("invalid IMAP mailbox")
        token = _unescape_quoted(token[1:-1])
    elif any(character in b" (){%*\\\"]" for character in token):
        raise ImapMailboxCodecError("invalid IMAP mailbox")
    return decode_imap_mailbox(token)


def _unescape_quoted(value: bytes) -> bytes:
    result = bytearray()
    index = 0
    while index < len(value):
        character = value[index]
        if character in {0x00, 0x0A, 0x0D, 0x22}:
            raise ImapMailboxCodecError("invalid IMAP mailbox")
        if character == 0x5C:
            index += 1
            if index >= len(value):
                raise ImapMailboxCodecError("invalid IMAP mailbox")
            character = value[index]
            if character not in {0x22, 0x5C}:
                raise ImapMailboxCodecError("invalid IMAP mailbox")
        result.append(character)
        index += 1
    return bytes(result)


def _is_imap_atom(value: str) -> bool:
    atom_specials = frozenset("(){ %*\"\\]")
    return bool(value) and all(
        "!" <= character <= "~" and character not in atom_specials
        for character in value
    )
