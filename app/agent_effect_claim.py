"""Reject a result that claims an external action from a turn that ran no tool.

Seen live on 2026-09-17: reply task 341173's Consumer run 20016 returned
`no_action` whose summary read "已按实时 OA 材料核验并执行通过". The run had made
zero tool calls -- the approval was never touched and is still pending in
DingTalk -- and the task closed `done` with an empty error. The existing
execution-evidence gate could not catch it: that gate asks whether a *write*
backs an `executed` outcome, and here there was no write to gate and the
outcome was not `executed`.

The check is deliberately narrow. Only a result whose own text claims an
external action already happened is held to it, and only when the turn made no
tool call at all. A turn that called tools is never judged here: whether those
calls back the claim is the evidence gate's question, not this one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

# Phrases in which the turn asserts an external action already happened.
# Each is a completed-action claim, not a plan ("将发送") or a recommendation
# ("建议通过"), which stay outside this check. Phrases that usually describe
# somebody else's action -- "已提交", "已通过" on their own -- are deliberately
# absent: run 19542 only relayed that Lily had submitted a plan.
_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"已执行",
        r"已发送",
        r"执行通过",
        r"已批准",
        r"已同意",
        r"已退回",
        r"已拒绝",
        r"已评论",
    )
)

_TOOL_ITEM_TYPES = frozenset(
    {"command_execution", "mcp_tool_call", "provider_tool_call", "tool_use"}
)


def result_claims_external_action(payload: object) -> bool:
    """Whether this result says an external action has already happened."""

    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(payload)
    if '"outcome": "executed"' in text or '"outcome":"executed"' in text:
        return True
    return any(pattern.search(text) for pattern in _CLAIM_PATTERNS)


def run_made_tool_calls(tool_events: Iterable[object]) -> bool:
    """Whether the turn actually called a tool, in any of the event shapes."""

    for event in tool_events:
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        item_type = item.get("type") if isinstance(item, Mapping) else None
        if item_type in _TOOL_ITEM_TYPES:
            return True
        if event.get("type") in _TOOL_ITEM_TYPES:
            return True
    return False


EXTERNAL_CLAIM_WITHOUT_TOOLS_REQUIREMENT = (
    "This turn made no tool call, so it cannot report that an external action "
    "has happened. Either perform the action with the tool that owns it and "
    "report what the tool returned, or state what is still pending without "
    "claiming it was done."
)


def generation_tool_events(store, *, reply_task_id: int, execution_generation: str):
    """Every tool event of the task's current generation, across its runs."""

    events: list[object] = []
    for run in store.list_agent_runs_for_task_generation(
        reply_task_id, execution_generation
    ):
        events.extend(run.tool_events or [])
    return events


def claims_external_action_without_tools(
    *, result: object, tool_events: Iterable[object]
) -> bool:
    """The narrow failure: an external-action claim with no tool call behind it.

    ``tool_events`` must cover every run of the task's current execution
    generation, not just this turn. A Consumer proposal and its Audit review
    are separate runs of the same generation, and each legitimately describes
    what the other already did: over seven days, judging a run alone flagged
    33 results, of which 31 were true statements about a sibling turn's work.
    Scoped to the generation, the same week leaves only the run that invented
    the action outright.
    """

    events = list(tool_events)
    if run_made_tool_calls(events):
        return False
    return result_claims_external_action(result)
