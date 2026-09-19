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
# A send the turn runs itself, matched by the *shape* of the command rather
# than a list of known spellings. Over thirty days production turns reached
# for `chat send`, `im send`, `dingtalk send-to-user`, `misc oa approve` and
# a dozen other names that are not real commands; a rule keyed to the exact
# spellings we already know would not see any of them, and a send under an
# invented name is still an attempt to reach a person.
_SEND_SHAPED_TOKEN = re.compile(
    r"^\+?(?:dm|send(?:-to-\w+)?|reply|forward|\w*-send|\w*-reply)$"
)
# `+messages-query-send-status` and `chat message query-send-status` read what
# a send did; they are not sends.
_STATUS_TOKEN = re.compile(r"-status$")


def _command_path(payload: str) -> tuple[str, ...]:
    """The dws subcommand words of one command, without flags or arguments."""

    try:
        argv = shlex.split(payload)
    except ValueError:
        argv = payload.split()
    words = [part for part in argv if not part.startswith("-")]
    if not words or words[0].rsplit("/", 1)[-1] != "dws":
        return ()
    path: list[str] = []
    for word in words[1:5]:
        if re.fullmatch(r"\+?[a-z][a-z0-9-]*", word):
            path.append(word)
        else:
            break
    return tuple(path)


def shell_send_commands(tool_events: Iterable[object]) -> list[str]:
    """Sends this turn ran in its own shell instead of through the service.

    Only shell commands count. The reviewed tool carries the same argv and is
    the sanctioned path: over the fourteen days to 2026-09-18, 473 generations
    shelled a send out directly and 133 went through the reviewed tool, so the
    alternative demonstrably works.
    """

    found: list[str] = []
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
        if "--help" in payload or "--dry-run" in payload:
            continue
        path = _command_path(payload)
        # `dws schema chat +messages-send json` and `dws shortcut schema ...`
        # ask what a send command looks like; they send nothing.
        if not path or "schema" in path:
            continue
        if any(
            _SEND_SHAPED_TOKEN.match(word) and not _STATUS_TOKEN.search(word)
            for word in path
        ):
            found.append(" ".join(path))
    return found


def delivered_shell_send_commands(tool_events: Iterable[object]) -> list[str]:
    """The shell sends that the provider actually accepted.

    `shell_send_commands` answers "did this turn try to send", which is the
    question the correction asks. This one answers "did a message reach
    someone", which is the question that decides whether a retry would send it
    twice -- and those differ, because a send can run and fail.

    A piped command (`dws ... | head`) exits with the last stage's status, so
    exit code 0 does not mean the provider accepted anything; DWS's own
    failure envelope in the output is the reliable signal, and it is the same
    one the execution-evidence gate reads.
    """

    from app.dingtalk_send_evidence import command_reports_failure

    delivered: list[str] = []
    for event in tool_events:
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        if not isinstance(item, Mapping):
            continue
        if not shell_send_commands([event]):
            continue
        exit_code = item.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            continue
        if command_reports_failure(item.get("aggregated_output")):
            continue
        delivered.extend(shell_send_commands([event]))
    return delivered


SHELL_SEND_REQUIREMENT = (
    "This turn sent a message from its own shell. Sending is the service's "
    "to do: it applies the signature and the feedback links, records the "
    "delivery under a key that cannot be sent twice, and survives a retry of "
    "this turn. A shell send has none of that.\n\n"
    "The message has already reached the recipient -- do not send it again, "
    "by any route. Report what was sent and to whom, and propose the action "
    "so the service can record it. If more has to be said, propose that as a "
    "new action and let the service deliver it."
)
