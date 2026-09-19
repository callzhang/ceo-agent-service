"""Require a provider receipt before Audit may report a DingTalk send executed.

``executed`` asserts that an external action happened.  The only thing that can
support that assertion is something the service wrote down itself: the receipt
a provider returned when it accepted the effect.  Everything the model types --
the message id, the delivery key, the readback -- is authored by the same turn
that is making the claim, and two of those values are handed to it in its own
prompt, so echoing them proves nothing.

What this rejects, seen live: a turn that ran no provider call at all and
reported the *trigger* message id as the id of the message it had just sent.
The task closed as done, the delivery ledger stayed empty, and the question it
was supposed to ask was never asked by anyone.

What this deliberately does not reject: a turn that sent, then identified its
own message by reading the conversation back instead of querying the send
status.  The send is real and the receipt is there; which id the turn then
reports is bookkeeping, not evidence.  Blocking those would force a retry, and
a retry of a delivered message sends it twice.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import replace

from app.agent_effect_guard import provider_receipts
from app.agent_effects import McpToolEffectRegistry
from app.agent_result import EffectKind
from app.consumer_agent import dingtalk_outgoing_text_key
from app.mcp_tool_effects import reviewed_mcp_tool_effect
from app.native_cli_metadata import (
    NativeCliMetadataClassifier,
    NativeCliMetadataUnavailableError,
    describe_native_command,
)
from app.store import AgentRole, AutoReplyStore, ReplyTask


class DingTalkSendEvidenceDriver:
    """Answer whether one Audit run really reached a provider for its effects."""

    def __init__(
        self,
        store: AutoReplyStore,
        *,
        classifier: NativeCliMetadataClassifier | None = None,
    ) -> None:
        self.store = store
        self._classifier = classifier

    def audit_run_has_execution_evidence(
        self, task: ReplyTask, *, audit_run_id: int
    ) -> bool:
        if task.channel != "dingtalk":
            return True
        run = self.store.get_agent_run(audit_run_id)
        if run is None:
            return False
        actions = self._accepted_actions(run)
        if actions is None:
            # The proposal this run reviewed cannot be read, so nothing it
            # claims can be checked against anything.
            return False
        written, unclassifiable = self._touched_objects(run.tool_events)
        has_receipt = bool(provider_receipts(run.tool_events))
        # Neither `capability` nor `operation` decides anything here: both are
        # free text the model writes, and September alone spelled chat as
        # `dingtalk-chat`, `dingtalk_chat`, `dingtalk chat` and `dws chat`. A
        # gate keyed on one spelling let the others through.
        for action in actions:
            if action.get("effect") == "none" and not _carries_outgoing_text(action):
                # The proposer declared this action changes nothing outside the
                # service -- a verification, or a response already in place.
                # There is no provider receipt to ask for. A message body is
                # still an external effect whatever the action declares, so the
                # declaration cannot be used to slip a send past the gate.
                continue
            identifiers = _action_identifiers(action)
            if identifiers & written:
                continue
            if _carries_outgoing_text(action) and has_receipt:
                # A send can address its recipient by display name, which no
                # proposed identifier matches, so a message body is also
                # satisfied by the receipt the send returned. A document
                # comment carries a body too but returns no message receipt;
                # its classified write on the document is what satisfies it.
                continue
            if identifiers & unclassifiable:
                # The object was only reached through a tool the service
                # cannot classify -- a third-party MCP server such as the
                # interview system -- so a write cannot be told from a read.
                # Refusing here would block honest work the service has no
                # means to recognise; that class stays on self-report.
                continue
            return False
        return True

    def execution_evidence_requirement(self) -> str:
        return (
            "external_result: executed requires evidence that the provider "
            "accepted the effect. For a chat send, that is the receipt the send "
            "returned; a message id taken from the conversation or from this "
            "prompt is not a receipt. For any other action, that is a completed "
            "write call on the object the proposal named -- a calendar response, "
            "an approval, a reaction. If an action needs no "
            "write -- it only verifies, or the response is already in place -- "
            "it is not executable: return feedback_provided so the proposal "
            "drops it, instead of reporting it executed."
        )

    def _touched_objects(
        self, tool_events: list[dict[str, object]]
    ) -> tuple[set[str], set[str]]:
        """Identifiers the run wrote to, and identifiers it reached unclassifiably.

        A write is recognised from DWS's own schema through the native CLI
        classifier, so a `get`, a `list` or a `--help` never counts, and the
        identifiers are the targets that classified write named. Calls to
        third-party MCP servers are outside that schema: their results are
        collected separately, as objects the service saw but cannot judge.
        """
        written: set[str] = set()
        unclassifiable: set[str] = set()
        for event in tool_events:
            command = _completed_native_command(event)
            if command is not None:
                classified = self._classify(command)
                if classified is not None and classified.effect is EffectKind.EFFECTFUL:
                    written.update(classified.target_identifiers.values())
                continue
            answer = _third_party_tool_answer(event)
            if answer is None:
                continue
            effect, identifiers = answer
            if effect is EffectKind.EFFECTFUL:
                written.update(identifiers)
            elif effect is None:
                unclassifiable.update(identifiers)
        return written, unclassifiable

    def _classify(self, command: dict[str, object]):
        if self._classifier is None:
            self._classifier = NativeCliMetadataClassifier()
        try:
            classified = self._classifier.classify(command)
        except NativeCliMetadataUnavailableError:
            return None
        if classified is not None:
            return classified
        # A write DWS publishes no runtime schema for is still a write. The
        # execution path already falls back to the registered-write list for
        # exactly this case; without the same fallback here the effect leaves
        # no trace in the evidence, and a real action reads as unproven.
        # Live: `oa approval revert-task` sent an approval back to its
        # originator, DingTalk recorded REDIRECT_PROCESS and the item left the
        # pending list, and the task still ended `failed`.
        descriptor = describe_native_command(command)
        if (
            descriptor is None
            or descriptor.cli != "dws"
            or not McpToolEffectRegistry.default().is_registered_write_operation(
                descriptor.command_path
            )
        ):
            return None
        return replace(descriptor, effect=EffectKind.EFFECTFUL)

    def _accepted_actions(self, run) -> list[dict] | None:
        """The actions of the proposal this Audit run reviewed, from its own parent.

        The parent link names the exact Consumer run whose candidate was under
        review. Searching the task's current generation instead finds a
        different proposal once the task has been rerun, and a search that
        finds nothing must not read as "nothing was proposed".
        """
        if run.parent_agent_run_id is None:
            return None
        consumer = self.store.get_agent_run(run.parent_agent_run_id)
        if consumer is None or consumer.role is not AgentRole.CONSUMER:
            return None
        if not consumer.final_result_json.strip():
            return None
        # Read the fields this question needs rather than validating the whole
        # result: the scoring fields a Consumer result carries have changed
        # shape over time and have nothing to do with what the proposal does.
        try:
            proposal = json.loads(consumer.final_result_json).get("proposal")
        except ValueError:
            return None
        if not isinstance(proposal, dict):
            return []
        actions = proposal.get("actions")
        if not isinstance(actions, list):
            return []
        return [action for action in actions if isinstance(action, dict)]

def _carries_outgoing_text(action: dict) -> bool:
    """An action whose payload is a message body to deliver.

    Judged by the payload alone, so it holds however the model spelled the
    capability, and it stays wider than the actions the service prepared a
    body for: an action naming no resolvable target could not be prepared, but
    a turn can still report having sent it.
    """
    payload = action.get("payload")
    return isinstance(payload, dict) and dingtalk_outgoing_text_key(payload) is not None


def completed_provider_writes(
    tool_events: list[dict[str, object]],
    *,
    store: AutoReplyStore | None = None,
) -> set[str]:
    """Identifiers a turn actually wrote to at the provider.

    The same judgement the execution evidence gate makes, exposed for the
    lifecycle: a task whose approval was rejected or sent back cannot end as a
    plain failure, because `failed` invites a rerun of something irreversible.
    """

    driver = DingTalkSendEvidenceDriver(store)
    written, _ = driver._touched_objects(tool_events)
    return written


def _action_identifiers(action: dict) -> set[str]:
    """Every identifier the action names, wherever the proposal put it."""
    values: set[str] = set()
    for field in ("target", "payload"):
        container = action.get(field)
        if isinstance(container, dict):
            values.update(
                value.strip()
                for value in container.values()
                if isinstance(value, str) and value.strip()
            )
    return values


def _completed_native_command(event: object) -> dict[str, object] | None:
    """The native command a completed, successful call ran, in classifier shape.

    Shell calls carry the command string; calls through the reviewed CLI's MCP
    tool carry `arguments.argv`. Both are the same provider operation.
    """
    if not isinstance(event, dict):
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") == "command_execution":
        if item.get("exit_code") != 0 or _reports_failure(item.get("aggregated_output")):
            return None
        argv = _first_stage_argv(item.get("command"))
        return {"argv": list(argv)} if argv else None
    if item.get("type") == "mcp_tool_call":
        if item.get("error") or not item.get("result"):
            return None
        arguments = item.get("arguments")
        argv = arguments.get("argv") if isinstance(arguments, dict) else None
        return {"argv": argv} if isinstance(argv, list) else None
    return None


def command_reports_failure(output: object) -> bool:
    """Public name for the failure-envelope check; see `_reports_failure`."""

    return _reports_failure(output)


def _reports_failure(output: object) -> bool:
    """Whether the call printed DWS's own failure envelope.

    A piped command (`dws ... | head`) exits with the last stage's status, so
    exit code 0 does not mean the provider call succeeded. Audit run 20012
    counted `dws oa approval oa-comments` that printed `--content is required`
    as a write on the approval, and a proposed approval that never ran passed.
    """
    if not isinstance(output, str):
        return False
    try:
        payload = json.loads(output)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("error")) or payload.get("ok") is False or payload.get("success") is False


def _third_party_tool_answer(
    event: object,
) -> tuple[EffectKind | None, set[str]] | None:
    """What a successful call to a non-native MCP server did, and to what.

    The native classifier reads DWS's own schema and cannot speak for another
    server, so each one's catalogue is recorded in
    `data/config/mcp-tool-effects.json`. A listed write contributes the
    identifiers it returned as evidence; a listed read contributes nothing, so
    a turn that only read cannot pass as a turn that wrote. A tool missing from
    that file stays unjudged, which keeps a newly added tool from refusing work
    the service has no means to recognise.
    """
    if not isinstance(event, dict):
        return None
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
        return None
    if item.get("server") == "agent_cli" or item.get("error") or not item.get("result"):
        return None
    effect = reviewed_mcp_tool_effect(
        str(item.get("server") or ""), str(item.get("tool") or "")
    )
    if effect is EffectKind.READ_ONLY:
        return effect, set()
    values: set[str] = set()
    _collect_strings(item.get("result"), values, depth=0)
    return effect, values


def _collect_strings(value: object, found: set[str], *, depth: int) -> None:
    if depth > 8:
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, found, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _collect_strings(item, found, depth=depth + 1)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                _collect_strings(json.loads(stripped), found, depth=depth + 1)
            except ValueError:
                found.add(stripped)
        elif stripped:
            found.add(stripped)


_SHELL_OPERATOR_CHARACTERS = frozenset("|&;<>")


def _first_stage_argv(command: object) -> tuple[str, ...] | None:
    """The simple command a shell line ran first, for recognising its effect.

    Models routinely append `2>&1` or `| head` to a provider call. The native
    classifier refuses any line containing shell operators, which is right
    when deciding whether a command may run, but here the question is only what
    already ran: in `dws ... 2>&1 | head` the provider call is `dws ...`. So the
    line is cut at its first operator and a file-descriptor number left before a
    redirection is dropped. Command substitution is still refused, because then
    the text no longer says what executed.
    """
    if not isinstance(command, str) or "$(" in command or "`" in command:
        return None
    try:
        outer = shlex.split(command)
    except ValueError:
        return None
    if len(outer) >= 3 and outer[0].rsplit("/", 1)[-1] in {"bash", "sh", "zsh"}:
        for flag in ("-lc", "-c"):
            if flag in outer and outer.index(flag) + 1 < len(outer):
                return _first_stage_argv(outer[outer.index(flag) + 1])
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None
    stage: list[str] = []
    for token in tokens:
        if token and set(token) <= _SHELL_OPERATOR_CHARACTERS:
            if stage and stage[-1].isdigit():
                stage.pop()
            break
        stage.append(token)
    return tuple(stage) or None
