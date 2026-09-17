"""Only text the service prepared may be sent to a person.

The service prepares every outbound message body: it applies the canonical
signature and the feedback links, records the prepared body under an immutable
delivery key, and hands that exact text to the turn that sends it. A turn that
composes its own message instead bypasses all of it.

Seen live on 2026-09-17: Audit run 20020 sent Wayne a DM it wrote itself and
transcribed the feedback links out of its own prompt, breaking the
percent-encoding mid-escape (`...%E5%BE%85%E5%A4%84%E7%90%86%E，尚...`); its
repair attempt corrupted them again. Over the previous fourteen days, 23 runs
sent a service-prepared body and 32 sent text the service had never seen.

Derek, 2026-09-17: text the service did not prepare must not go out. A turn
that sends its own composition gets the ordinary correction turn, so the
message is re-proposed and prepared through the service instead.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping

_SEND_COMMAND = re.compile(
    r"\bdws\s+(?:chat|ding|mail)\s+"
    r"(?:\+dm|\+send-to-group|\+messages-send|\+send|send|message\s+send)\b"
)
_TEXT_FLAGS = ("--content", "--text", "--message", "--body")


def _first_stage(command: str) -> str:
    """The command as typed, before any pipe or redirect."""

    for separator in ("|", ">", "&&", ";"):
        command = command.split(separator)[0]
    return command.strip()


def _shell_payload(command: str) -> str:
    """Strip the `/bin/zsh -lc '...'` wrapper the runtime logs commands in."""

    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) >= 3 and parts[0].endswith(("zsh", "bash", "sh")):
        return parts[-1]
    return command


def provider_send_texts(tool_events: Iterable[object]) -> list[str]:
    """Every message body this turn handed to a provider send command."""

    texts: list[str] = []
    for event in tool_events:
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "command_execution":
            continue
        command = str(item.get("command") or "")
        payload = _first_stage(_shell_payload(command))
        if not _SEND_COMMAND.search(payload):
            continue
        if "--help" in payload or "--dry-run" in payload:
            continue
        texts.extend(_text_arguments(payload))
    return texts


def _text_arguments(payload: str) -> list[str]:
    """The message bodies in one send command, however it was quoted.

    A logged command can carry unbalanced quotes once it has been through a
    shell wrapper, so a quoting failure falls back to reading the flag values
    directly rather than dropping the send from the check.
    """

    try:
        argv = shlex.split(payload)
    except ValueError:
        argv = []
    found: list[str] = []
    for index, argument in enumerate(argv):
        for flag in _TEXT_FLAGS:
            if argument == flag and index + 1 < len(argv):
                found.append(argv[index + 1])
            elif argument.startswith(f"{flag}="):
                found.append(argument[len(flag) + 1 :])
    if found:
        return found
    for flag in _TEXT_FLAGS:
        for match in re.finditer(
            rf"{re.escape(flag)}[= ]\s*(\"(?:[^\"\\]|\\.)*\"|\'(?:[^\'\\]|\\.)*\'|\S+)",
            payload,
        ):
            value = match.group(1)
            if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
                value = value[1:-1]
            found.append(value)
    return found


def _normalized(text: str) -> str:
    return "".join(text.split())


def unprepared_send_texts(
    sent_texts: Iterable[str], prepared_bodies: Iterable[str]
) -> list[str]:
    """The sent bodies the service never prepared.

    A prepared body may be sent verbatim or with the provider's own trimming,
    so a sent text counts as prepared when either contains the other once
    whitespace is ignored.
    """

    prepared = [_normalized(body) for body in prepared_bodies if str(body).strip()]
    unprepared: list[str] = []
    for text in sent_texts:
        normalized = _normalized(text)
        if not normalized:
            continue
        if any(
            normalized in body or body in normalized for body in prepared if body
        ):
            continue
        unprepared.append(text)
    return unprepared


UNPREPARED_SEND_REQUIREMENT = (
    "This turn sent a message body the service did not prepare. Every outbound "
    "message must be the exact prepared text the service produced for the "
    "action it belongs to: that text carries the service signature and the "
    "feedback links, and transcribing them by hand corrupts them. Send the "
    "prepared body for the accepted action, or return feedback so the message "
    "is proposed and prepared through the service first."
)
