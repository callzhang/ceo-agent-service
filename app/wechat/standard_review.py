"""Binding of a WeChat delivery to the standard Consumer -> Audit review.

A delivery may be sent only after an independent review approved exactly the
text and target being sent. The original WeChat pipeline records that approval
in the delivery's own evidence (``WechatDeliveryAuditor``). A delivery created
from the standard pipeline is bound instead to the standard review of the
candidate that proposed it: ``standard_candidate_id`` and ``standard_review_id``
name the adopted candidate and its Audit review, and
``standard_action_identity`` names the proposed action inside that candidate.

These helpers are pure. The store reads the rows and applies every check below;
anything it cannot prove leaves the delivery unapproved.
"""

from __future__ import annotations

import json

WECHAT_SEND_CAPABILITY = "wechat"
WECHAT_SEND_OPERATION = "send_message"
STANDARD_EVIDENCE_KEYS = (
    "standard_candidate_id", "standard_review_id", "standard_action_identity",
)
SEND_TARGET_FIELDS = ("account_id", "target_type", "target_id", "conversation_id")


def approved_send_action(candidate_json: str, action_identity: str) -> dict | None:
    """The one reviewed WeChat send action with this identity, or None.

    Only a plain ``proposal`` candidate qualifies; a choice that still needs a
    human selection is not a reviewed send.
    """
    try:
        candidate = json.loads(candidate_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(candidate, dict) or candidate.get("outcome") != "proposal":
        return None
    proposal = candidate.get("proposal")
    actions = proposal.get("actions") if isinstance(proposal, dict) else None
    if not isinstance(actions, list):
        return None
    matches = [
        action for action in actions
        if isinstance(action, dict) and action.get("action_identity") == action_identity
    ]
    if len(matches) != 1:
        return None
    action = matches[0]
    if (action.get("capability") != WECHAT_SEND_CAPABILITY
            or action.get("operation") != WECHAT_SEND_OPERATION):
        return None
    return action


def send_fields(action: dict) -> dict[str, str] | None:
    """Target and text of a send action, or None when any is missing or blank."""
    target = action.get("target")
    payload = action.get("payload")
    if not isinstance(target, dict) or not isinstance(payload, dict):
        return None
    fields = {name: target.get(name) for name in SEND_TARGET_FIELDS}
    fields["text"] = payload.get("text")
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        return None
    return fields


def action_matches_delivery(
    action: dict, *, account_id: str, target_type: str, target_id: str,
    conversation_id: str, reply_text: str,
) -> bool:
    """Whether the reviewed action is exactly what the delivery would send."""
    fields = send_fields(action)
    return bool(
        fields is not None
        and fields["account_id"] == account_id
        and fields["target_type"] == target_type
        and fields["target_id"] == target_id
        and fields["conversation_id"] == conversation_id
        and fields["text"] == reply_text
    )
