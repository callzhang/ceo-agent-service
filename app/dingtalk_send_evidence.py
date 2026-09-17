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

from app.agent_effect_guard import provider_receipts
from app.agent_result import EffectKind
from app.consumer_agent import dingtalk_outgoing_text_key
from app.native_cli_metadata import (
    NativeCliMetadataClassifier,
    NativeCliMetadataUnavailableError,
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
        actions = self._accepted_actions(task, run.proposal_revision)
        if any(_is_chat_send(action) for action in actions) and not provider_receipts(
            run.tool_events
        ):
            return False
        calendar_actions = [
            action for action in actions if action.get("capability") == "dingtalk-calendar"
        ]
        if calendar_actions and not self._responded_to_each(
            run.tool_events, calendar_actions
        ):
            return False
        # Approvals and reactions keep the contract they have until each one
        # has evidence of its own: a reaction returns `result: {}` with no
        # identifier, so a receipt shape cannot describe it.
        return True

    def execution_evidence_requirement(self) -> str:
        return (
            "external_result: executed requires evidence that the provider "
            "accepted the effect. For a chat send, that is the receipt the send "
            "returned; a message id taken from the conversation or from this "
            "prompt is not a receipt. For a calendar action, that is a completed "
            "respond call on the proposed event. If a calendar action needs no "
            "write -- it only verifies, or the response is already in place -- "
            "it is not executable: return feedback_provided so the proposal "
            "drops it, instead of reporting it executed."
        )

    def _responded_to_each(
        self, tool_events: list[dict[str, object]], calendar_actions: list[dict]
    ) -> bool:
        """Whether every proposed calendar event received an effectful call.

        A write is recognised from DWS's own schema through the native CLI
        classifier, so a `get`, a `list` or a `--help` never counts, and a chat
        send made instead of the response does not count either: the effect has
        to land on the event the proposal named.
        """
        touched: set[str] = set()
        for event in tool_events:
            command = _completed_native_command(event)
            if command is None:
                continue
            classified = self._classify(command)
            if classified is None or classified.effect is not EffectKind.EFFECTFUL:
                continue
            touched.update(classified.target_identifiers.values())
        return all(_action_identifiers(action) & touched for action in calendar_actions)

    def _classify(self, command: dict[str, object]):
        if self._classifier is None:
            self._classifier = NativeCliMetadataClassifier()
        try:
            return self._classifier.classify(command)
        except NativeCliMetadataUnavailableError:
            return None

    def _accepted_actions(self, task: ReplyTask, proposal_revision: int) -> list[dict]:
        """The actions of the proposal this Audit revision reviewed."""
        runs = self.store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation
        )
        consumer = next(
            (
                run
                for run in reversed(runs)
                if run.role is AgentRole.CONSUMER
                and run.proposal_revision == proposal_revision
                and run.final_result_json
            ),
            None,
        )
        if consumer is None:
            return []
        # Read the fields this question needs rather than validating the whole
        # result: the scoring fields a Consumer result carries have changed
        # shape over time and have nothing to do with what the proposal does.
        proposal = json.loads(consumer.final_result_json).get("proposal")
        if not isinstance(proposal, dict):
            return []
        actions = proposal.get("actions")
        if not isinstance(actions, list):
            return []
        return [action for action in actions if isinstance(action, dict)]


def _is_chat_send(action: dict) -> bool:
    """A chat action carrying a message body, whether or not its target resolved.

    Deliberately wider than the actions the service prepared a body for: an
    action naming no resolvable target is one the service could not prepare,
    but a turn can still report having sent it, and that report is exactly what
    needs a receipt behind it.
    """
    payload = action.get("payload")
    return (
        action.get("capability") == "dingtalk-chat"
        and isinstance(payload, dict)
        and dingtalk_outgoing_text_key(payload) is not None
    )


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
        if item.get("exit_code") != 0:
            return None
        return {"command": item.get("command")}
    if item.get("type") == "mcp_tool_call":
        if item.get("error") or not item.get("result"):
            return None
        arguments = item.get("arguments")
        argv = arguments.get("argv") if isinstance(arguments, dict) else None
        return {"argv": argv} if isinstance(argv, list) else None
    return None
