"""Share exact repeated strings within one DingTalk message tool result."""

import json
from typing import Any

import pydantic_core


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _model_visible_chars(value: object) -> int:
    # Match FastMCP's indented text at this tool's actual nesting depth.
    return len(pydantic_core.to_json({"messages": [value]}, fallback=str, indent=2).decode())


def share_message_raw_payload(message: dict[str, Any]) -> dict[str, Any]:
    """Reference top-level strings repeated in raw_payload without losing raw order.

    A referenced raw key keeps its position with a null placeholder. Its exact
    original string is the named top-level field in raw_payload_field_refs.
    """
    raw = message.get("raw_payload")
    if not isinstance(raw, dict) or "raw_payload_field_refs" in message:
        return message

    top_strings = {
        key: value for key, value in message.items()
        if key != "raw_payload" and isinstance(value, str)
    }
    references: dict[str, str] = {}
    for raw_key, value in raw.items():
        if not isinstance(value, str):
            continue
        matches = (key for key, top_value in top_strings.items() if top_value == value)
        top_key = min(matches, key=_json_bytes, default=None)
        if top_key is None:
            continue
        reference_cost = 4 + _json_bytes(raw_key) + 1 + _json_bytes(top_key) + 1
        if _json_bytes(value) > reference_cost:
            references[raw_key] = top_key
    if not references:
        return message

    projected = {
        **message,
        "raw_payload": {
            key: None if key in references else value for key, value in raw.items()
        },
        "raw_payload_field_refs": references,
    }
    return projected if _model_visible_chars(projected) < _model_visible_chars(message) else message
