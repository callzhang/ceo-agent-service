"""Reviewed exact input contracts shared by Claude's broker and MCP proxies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

_SCHEMAS_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "claude-reviewed-tool-input-schemas.json"
)


def reviewed_claude_tool_schema(tool_name: str) -> dict[str, object] | None:
    payload = json.loads(_SCHEMAS_PATH.read_text(encoding="utf-8"))
    tools = payload.get("tools") if isinstance(payload, dict) else None
    schema = tools.get(tool_name) if isinstance(tools, dict) else None
    return dict(schema) if isinstance(schema, dict) else None


def validate_claude_tool_input(tool_name: str, arguments: Mapping[str, object]) -> bool:
    schema = reviewed_claude_tool_schema(tool_name)
    if schema is None or schema.get("type") != "object":
        return False
    properties = schema.get("properties")
    required = schema.get("required")
    if (
        not isinstance(properties, dict)
        or not isinstance(required, list)
        or not all(isinstance(item, str) for item in required)
        or schema.get("additionalProperties") is not False
        or set(arguments) - set(properties)
        or any(item not in arguments for item in required)
    ):
        return False
    return all(
        _matches_type(arguments[name], definition)
        for name, definition in properties.items()
        if name in arguments
    )


def _matches_type(value: object, definition: object) -> bool:
    if not isinstance(definition, dict):
        return False
    expected = definition.get("type")
    if expected == "string":
        return isinstance(value, str) and bool(value.strip())
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "array":
        items = definition.get("items")
        return isinstance(value, list) and all(
            _matches_type(item, items) for item in value
        )
    return False
