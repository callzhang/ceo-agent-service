"""Strict IMAP modified UTF-7 mailbox name codec."""

from __future__ import annotations

import base64
import binascii


class ImapMailboxCodecError(ValueError):
    """A mailbox name cannot be represented safely on the IMAP wire."""


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
