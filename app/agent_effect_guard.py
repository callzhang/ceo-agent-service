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


# Decode transport envelopes, not arbitrary business content. Historical
# messages and document text can contain the same fields as a send response.
_MAX_ANSWER_DEPTH = 8


def _receipts_in(value: Any, found: list[str], depth: int = 0) -> None:
    if depth > _MAX_ANSWER_DEPTH or not isinstance(value, dict):
        return
    if value.get("success") is False or value.get("ok") is False:
        return
    result = value.get("result")
    if isinstance(result, dict):
        if result.get("success") is not False and result.get("ok") is not False:
            for field, receipt in result.items():
                if field in PROVIDER_RECEIPT_FIELDS and isinstance(receipt, str) and receipt.strip():
                    found.append(receipt.strip())
        _receipts_in(result, found, depth + 1)
    _receipts_in(value.get("data"), found, depth + 1)
    _receipts_in(value.get("provider_result"), found, depth + 1)


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
        if not isinstance(result, dict):
            return [result] if result else []
        answers = [
            entry["text"]
            for entry in result.get("content", [])
            if isinstance(entry, dict)
            and entry.get("type") == "text"
            and isinstance(entry.get("text"), str)
        ] if isinstance(result.get("content"), list) else []
        if isinstance(result.get("structuredContent"), dict):
            answers.append(result["structuredContent"])
        if "content" not in result:
            answers.append(result)
        return answers
    return []


def provider_receipts(tool_events: Any) -> tuple[str, ...]:
    """Return the provider receipts a turn's executed calls came back with.

    Only receipt fields in provider result envelopes qualify. An identifier in
    a read projection or quoted business content is not evidence of a new send.
    """
    if not isinstance(tool_events, list):
        return ()
    found: list[str] = []
    for event in tool_events:
        for answer in _provider_answers(event):
            if isinstance(answer, str):
                for response in _loads(answer):
                    _receipts_in(response, found)
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
