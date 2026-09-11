"""Detect provider effects produced by a turn that was only allowed to propose.

Consumer Agent A is a proposal role: it gathers facts and returns a candidate
for Audit to execute.  The effect review in ``app.agent_cli`` only governs
commands routed through that controlled CLI, so a turn with plain shell access
can call a provider directly and bypass the gate entirely.  When that happens
the effect is invisible twice over: the delivery ledger has no record, so a
retry repeats it, and because DingTalk sends post as the principal, Audit can
read the turn's own message as pre-existing evidence and close the task as a
duplicate.  The breach manufactures the evidence that hides it.

This module does not prevent the call -- it recognises that one happened, from
the receipt the provider returned.
"""

from __future__ import annotations

from typing import Any


# Identifiers a provider returns only once an effect has been accepted.  These
# are the same receipt fields the delivery projection already treats as proof
# of a completed send, not a guess about command text.
PROVIDER_RECEIPT_FIELDS = (
    "openMessageId",
    "open_message_id",
    "openTaskId",
    "open_task_id",
)


def _receipts_in(value: Any, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PROVIDER_RECEIPT_FIELDS and isinstance(item, str) and item.strip():
                found.append(item.strip())
            else:
                _receipts_in(item, found)
        return
    if isinstance(value, list):
        for item in value:
            _receipts_in(item, found)


def _command_output(event: Any) -> Any:
    """The output of a command the turn actually executed, if this is one."""
    if not isinstance(event, dict):
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    # Only a completed execution can carry a receipt; a started or failed one
    # has no provider answer to read.
    if item.get("type") != "command_execution":
        return None
    if item.get("exit_code") != 0:
        return None
    return item.get("output") or item.get("aggregated_output")


def provider_receipts(tool_events: Any) -> tuple[str, ...]:
    """Return the provider receipts a turn's executed commands came back with.

    A non-empty result means the turn reached a provider and the provider
    accepted the effect, whatever the turn then reported in its typed result.
    """
    if not isinstance(tool_events, list):
        return ()
    found: list[str] = []
    for event in tool_events:
        output = _command_output(event)
        if output is None:
            continue
        if isinstance(output, str):
            _receipts_in(_loads(output), found)
        else:
            _receipts_in(output, found)
    # Preserve first-seen order without repeating an id echoed by later events.
    ordered: list[str] = []
    for receipt in found:
        if receipt not in ordered:
            ordered.append(receipt)
    return tuple(ordered)


def _loads(text: str) -> Any:
    import json

    decoder = json.JSONDecoder()
    index = 0
    collected: list[Any] = []
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        collected.append(value)
        index = end
    return collected
