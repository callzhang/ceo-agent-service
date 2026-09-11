"""Turn a provider effect a proposal turn produced into a ledger entry.

``app.agent_effect_guard`` recognises that a Consumer turn reached a provider
and the provider accepted the effect.  Recognising it is not enough: while the
delivery is missing from ``external_action_results`` the idempotency check has
nothing to find, so the next attempt sends the same message again, to the same
person, as the principal.  The effect is already irreversible; the only thing
still in anyone's control is whether it happens twice.

This module reads one completed run and states, from that run's own evidence,
exactly which delivery it performed.  It refuses to guess.  If the run's
executed commands carry more than one provider receipt, or no proposed action
matches the command that produced the receipt, it raises rather than writing a
delivery that might name the wrong action -- a wrong ledger row is worse than
none, because it would suppress a send that really does still need to happen.

The identity it produces comes from ``expected_external_action``, the same
derivation the normal Audit path uses, so a reconciled row occupies exactly the
slot a later legitimate attempt would look in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agent_effect_guard import PROVIDER_RECEIPT_FIELDS, provider_receipts
from app.external_action_identity import expected_external_action


class UnreconcilableProviderEffect(RuntimeError):
    """The run's evidence does not identify a single delivery beyond doubt."""


@dataclass(frozen=True)
class _Action:
    """The identity half of a proposed action, read straight from the payload.

    Deliberately not ``ProposedAction``: reconciliation runs against results
    persisted by older builds, and a model that has since tightened an
    unrelated field would refuse to load them.  Only these three fields enter
    the external action key, and they are plain strings and a mapping.
    """

    action_identity: str
    capability: str
    operation: str
    target: dict[str, Any]


@dataclass(frozen=True)
class ProviderEffectReconciliation:
    """One delivery a run performed, ready to be recorded."""

    agent_run_id: int
    reply_task_id: int
    business_object_key: str
    external_action_key: str
    action_identity: str
    operation: str
    target_identifiers: dict[str, Any]
    reply_text: str
    provider_result: dict[str, Any]
    receipts: tuple[str, ...]
    command: str


def _json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    collected: list[Any] = []
    index = 0
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


def _carries_receipt(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PROVIDER_RECEIPT_FIELDS and isinstance(item, str) and item.strip():
                return True
            if _carries_receipt(item):
                return True
        return False
    if isinstance(value, list):
        return any(_carries_receipt(item) for item in value)
    return False


def _receipt_bearing_commands(tool_events: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every executed command whose output came back holding a receipt."""
    if not isinstance(tool_events, list):
        return []
    found: list[tuple[str, dict[str, Any]]] = []
    for event in tool_events:
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "command_execution" or item.get("exit_code") != 0:
            continue
        output = item.get("output") or item.get("aggregated_output") or ""
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
        for value in _json_objects(output):
            if isinstance(value, dict) and _carries_receipt(value):
                found.append((str(item.get("command") or ""), value))
    return found


def _proposed_actions(final_result_json: str) -> list[_Action]:
    try:
        payload = json.loads(final_result_json or "")
    except (TypeError, ValueError) as error:
        raise UnreconcilableProviderEffect(
            "run has no readable typed result to identify the action"
        ) from error
    proposal = payload.get("proposal") if isinstance(payload, dict) else None
    actions = proposal.get("actions") if isinstance(proposal, dict) else None
    if not isinstance(actions, list):
        return []
    parsed: list[_Action] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        target = action.get("target")
        parsed.append(
            _Action(
                action_identity=str(action.get("action_identity") or "").strip(),
                capability=str(action.get("capability") or "").strip(),
                operation=str(action.get("operation") or "").strip(),
                target=dict(target) if isinstance(target, dict) else {},
            )
        )
    return parsed


def _reply_text(final_result_json: str, action_identity: str) -> str:
    payload = json.loads(final_result_json)
    proposal = payload.get("proposal") or {}
    for action in proposal.get("actions") or []:
        if not isinstance(action, dict):
            continue
        if str(action.get("action_identity") or "").strip() != action_identity:
            continue
        content = action.get("payload")
        if not isinstance(content, dict):
            continue
        for field in ("content", "text", "reply_text"):
            candidate = content.get(field)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def plan_provider_effect_reconciliation(
    *,
    agent_run_id: int,
    reply_task_id: int,
    business_object_key: str,
    final_result_json: str,
    tool_events: Any,
    confirmation: dict[str, Any] | None = None,
) -> ProviderEffectReconciliation:
    """Describe the one delivery this run performed, or refuse to.

    ``confirmation`` is an optional read-only provider readback (for DingTalk,
    the ``query-send-status`` answer).  A send returns a task id and resolves
    asynchronously, so the run's own output often proves only that the provider
    accepted the request.  Recording the later confirmation alongside it keeps
    the durable message id with the delivery it belongs to.
    """
    receipts = provider_receipts(tool_events)
    if not receipts:
        raise UnreconcilableProviderEffect(
            "run carries no provider receipt, so it performed no delivery to record"
        )
    bearing = _receipt_bearing_commands(tool_events)
    distinct = {json.dumps(value, sort_keys=True, ensure_ascii=False) for _, value in bearing}
    if len(distinct) != 1:
        raise UnreconcilableProviderEffect(
            f"run carries {len(distinct)} distinct provider results; "
            "reconcile each delivery explicitly instead of guessing"
        )
    command, provider_result = bearing[0]

    actions = _proposed_actions(final_result_json)
    matching = [
        action
        for action in actions
        if action.action_identity
        and action.operation
        and action.target
        and all(
            isinstance(value, str) and value and value in command
            for value in action.target.values()
        )
    ]
    if len(matching) != 1:
        raise UnreconcilableProviderEffect(
            f"{len(matching)} proposed actions match the command that produced the "
            "receipt; the delivery cannot be attributed to one action"
        )
    action = matching[0]

    reply_text = _reply_text(final_result_json, action.action_identity)
    if not reply_text:
        raise UnreconcilableProviderEffect(
            "matched action carries no message text to project into History"
        )

    expected = expected_external_action(
        action, action_index=0, business_object_key=business_object_key
    )
    recorded: dict[str, Any] = {
        "reconciled_from": "consumer_unreviewed_provider_effect",
        "reconciled_from_agent_run_id": agent_run_id,
        "receipt": provider_result,
    }
    if confirmation is not None:
        recorded["confirmation"] = confirmation
    return ProviderEffectReconciliation(
        agent_run_id=agent_run_id,
        reply_task_id=reply_task_id,
        business_object_key=business_object_key,
        external_action_key=str(expected["external_action_key"]),
        action_identity=action.action_identity,
        operation=action.operation,
        target_identifiers=dict(action.target),
        reply_text=reply_text,
        provider_result=recorded,
        receipts=receipts,
        command=command,
    )
