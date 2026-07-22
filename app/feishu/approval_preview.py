"""Privacy-preserving, complete previews for immutable Feishu effects."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.feishu.payloads import FeishuReplyPayload, split_reply_payload


_FINGERPRINT_DOMAIN = b"ceo-agent:feishu-approval-preview:v1\0"


def stable_identifier_fingerprint(kind: str, value: str) -> str:
    """Return a domain-separated fingerprint without disclosing an identifier."""
    normalized_kind = str(kind or "").strip()
    normalized_value = str(value or "").strip()
    if not normalized_kind or not normalized_value:
        return "unavailable"
    digest = hashlib.sha256(
        _FINGERPRINT_DOMAIN
        + normalized_kind.encode("utf-8")
        + b"\0"
        + normalized_value.encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def delivery_approval_preview(delivery: Any) -> dict[str, object]:
    """Describe every immutable remote effect covered by delivery approval."""
    mentions = tuple(getattr(delivery, "mention_open_ids", ()) or ())
    payload = FeishuReplyPayload(
        kind=str(getattr(delivery, "reply_format", "") or "text"),
        text=str(getattr(delivery, "reply_text", "") or ""),
        # Mentions affect payload/approval identity but not text boundaries;
        # previewing must remain robust for redacted diagnostic fixtures.
        mention_open_ids=(),
    )
    chunks = split_reply_payload(payload)
    return {
        "target": {
            "provenance": "reply_to_inbound_message",
            "conversation_provenance": "reply_task_chat",
            "conversation_fingerprint": stable_identifier_fingerprint(
                "chat_id", str(getattr(delivery, "chat_id", "") or "")
            ),
            "message_fingerprint": stable_identifier_fingerprint(
                "message_id",
                str(getattr(delivery, "reply_to_message_id", "") or ""),
            ),
        },
        "reply_in_thread": bool(getattr(delivery, "reply_in_thread", False)),
        "mentions": [
            {
                "alias": f"mention-{ordinal}",
                "fingerprint": stable_identifier_fingerprint(
                    "open_id", str(open_id or "")
                ),
            }
            for ordinal, open_id in enumerate(mentions, start=1)
        ],
        "reply_format": str(getattr(delivery, "reply_format", "") or ""),
        "expected_chunks": int(getattr(delivery, "expected_chunks", 0) or 0),
        "chunk_plan_sha256": str(
            getattr(delivery, "chunk_plan_sha256", "") or ""
        ),
        "review_generation": int(
            getattr(delivery, "review_generation", 0) or 0
        ),
        "chunks": [
            {"ordinal": ordinal, "text": chunk}
            for ordinal, chunk in enumerate(chunks)
        ],
        "text": str(getattr(delivery, "reply_text", "") or ""),
    }


def delivery_list_preview(delivery: Any) -> dict[str, object]:
    """Return identifier-safe metadata while withholding the draft by default."""
    preview = delivery_approval_preview(delivery)
    text = str(preview.pop("text"))
    preview.pop("chunk_plan_sha256", None)
    chunks = list(preview.pop("chunks"))
    preview["chunks"] = [
        {
            "ordinal": int(chunk["ordinal"]),
            "length": len(str(chunk["text"])),
        }
        for chunk in chunks
    ]
    preview["text_summary"] = f"[redacted:{len(text)} chars]"
    return preview


def action_approval_preview(action: Any) -> dict[str, object]:
    """Describe the exact, immutable effect covered by an action approval."""
    kind = str(getattr(action, "kind", "") or "")
    try:
        payload = json.loads(str(getattr(action, "payload_json", "") or "{}"))
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if kind == "handoff_notify":
        target_kind = "open_id"
        target_value = str(getattr(action, "target_open_id", "") or "")
        provenance = "locally_allowlisted_handoff_recipient"
        text = payload.get("text")
        effect: dict[str, object] = {
            "type": "send_handoff_notification",
            "text": text if isinstance(text, str) else "",
        }
    elif kind == "add_reaction":
        target_kind = "message_id"
        target_value = str(getattr(action, "target_message_id", "") or "")
        provenance = "persisted_reply_task_trigger_message"
        emoji = payload.get("emoji_type")
        effect = {
            "type": "add_reaction",
            "emoji_type": emoji if isinstance(emoji, str) else "",
        }
    else:
        target_kind = "message_id"
        target_value = str(getattr(action, "target_message_id", "") or "")
        provenance = "active_app_owned_delivery_receipt"
        effect = {"type": "recall_bot_owned_message"}

    return {
        "target": {
            "provenance": provenance,
            "fingerprint": stable_identifier_fingerprint(
                target_kind, target_value
            ),
        },
        "effect": effect,
    }


def action_list_preview(action: Any) -> dict[str, object]:
    """Return safe action metadata while withholding handoff draft text."""
    preview = action_approval_preview(action)
    effect = dict(preview["effect"])
    if effect.get("type") == "send_handoff_notification":
        text = str(effect.pop("text", ""))
        effect["text_summary"] = f"[redacted:{len(text)} chars]"
    return {"target": preview["target"], "effect": effect}
