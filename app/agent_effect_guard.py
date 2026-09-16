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


# A provider answer nests: an MCP result wraps its payload in content entries
# whose text is the provider's JSON re-encoded as a string, and the controlled
# CLI wraps that again in its own envelope.  The receipt sits at the bottom, so
# the walk decodes text it meets on the way down instead of stopping at it.
_MAX_ANSWER_DEPTH = 8


def _receipts_in(value: Any, found: list[str], depth: int = 0) -> None:
    if depth > _MAX_ANSWER_DEPTH:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PROVIDER_RECEIPT_FIELDS and isinstance(item, str) and item.strip():
                found.append(item.strip())
            else:
                _receipts_in(item, found, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _receipts_in(item, found, depth + 1)
        return
    if isinstance(value, str) and "{" in value:
        _receipts_in(_loads(value), found, depth + 1)


def _provider_answers(event: Any) -> list[Any]:
    """Every provider answer this event carries, however the turn reached one.

    A turn reaches a provider two ways: a shell command, and a call to the
    controlled CLI exposed as an MCP tool.  Reading only the first left every
    effect routed through the controlled path invisible -- which is the path
    the runtime prefers, so the blind spot covered the well-behaved case.
    """
    if not isinstance(event, dict):
        return []
    item = event.get("item")
    if not isinstance(item, dict):
        return []
    item_type = item.get("type")
    # Only a completed call can carry a receipt; a started or failed one has no
    # provider answer to read.
    if item_type == "command_execution":
        if item.get("exit_code") != 0:
            return []
        output = item.get("output") or item.get("aggregated_output")
        return [output] if output else []
    if item_type == "mcp_tool_call":
        if item.get("error"):
            return []
        result = item.get("result")
        return [result] if result else []
    return []


def provider_receipts(tool_events: Any) -> tuple[str, ...]:
    """Return the provider receipts a turn's executed calls came back with.

    A non-empty result means the turn reached a provider and the provider
    accepted the effect, whatever the turn then reported in its typed result.
    """
    if not isinstance(tool_events, list):
        return ()
    found: list[str] = []
    for event in tool_events:
        for answer in _provider_answers(event):
            if isinstance(answer, str):
                _receipts_in(_loads(answer), found)
            else:
                _receipts_in(answer, found)
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
