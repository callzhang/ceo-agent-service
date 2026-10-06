"""Canonical bindings for reviewed Consumer candidates and their actions."""

import hashlib
import json

from app.agent_contracts import ConsumerAgentResult, ConsumerProposal, ProposedAction


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def candidate_digest(result: ConsumerAgentResult) -> str:
    """Bind every typed field of the exact submitted candidate."""
    return _digest(result.model_dump(mode="json"))


def _action_content(action: ProposedAction) -> dict[str, object]:
    return {
        "capability": action.capability,
        "operation": action.operation,
        "target": action.target,
        "payload": action.payload,
        "effect": action.effect,
    }


def action_content_digest(candidate: ConsumerAgentResult | ConsumerProposal) -> str:
    """Compare proposed effects without rationale or presentation metadata."""
    if isinstance(candidate, ConsumerProposal):
        content = [[_action_content(action) for action in candidate.actions]]
    else:
        content = []
        if candidate.proposal is not None:
            content.append([_action_content(action) for action in candidate.proposal.actions])
        for option in candidate.decision_options:
            if option.plan is not None:
                content.append([_action_content(action) for action in option.plan.actions])
            else:
                content.append({"terminal_outcome": option.terminal_outcome, "reason": option.reason})
    return _digest(content)


def rejected_content_changed(before: ConsumerAgentResult, after: ConsumerAgentResult) -> bool:
    """A rejection requires changed executable or terminal branch content."""
    return action_content_digest(before) != action_content_digest(after)
