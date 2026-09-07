"""Stable business identities for typed external actions.

This module deliberately knows nothing about CLI commands or runtime tools.
It gives retries and projections the same business action key while the
selected Agent/runtime owns provider invocation details.
"""

from __future__ import annotations

from app.business_identity import external_action_key


def expected_external_action(
    action,
    *,
    action_index: int = 0,
    business_object_key: str,
) -> dict[str, object]:
    """Describe one typed action without translating it into a provider command."""
    target = dict(action.target)
    action_key = external_action_key(
        business_object_key=business_object_key,
        action_identity=action.action_identity,
        operation=action.operation,
        target_identifiers=target,
    )
    return {
        "action_index": action_index,
        "action_identity": action.action_identity,
        "capability": action.capability,
        "operation": action.operation,
        "target_identifiers": target,
        "external_action_key": action_key,
    }
