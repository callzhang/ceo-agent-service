"""Render deterministic scalar field evidence from an adopted candidate."""

from __future__ import annotations

import json
from typing import Any


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    raise ValueError("candidate contains a non-JSON value")


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def render_candidate_field_evidence(candidate: object) -> str:
    """List exact JSON paths, values and types from the current candidate.

    Object and array containers are not expanded as literals; their scalar
    descendants are listed at their exact JSON Pointer paths.
    """
    if hasattr(candidate, "model_dump"):
        candidate = candidate.model_dump(mode="json")
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be a JSON object")

    fields: list[str] = []

    def visit(value: Any, path: str) -> None:
        value_type = _json_type(value)
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("candidate object keys must be strings")
                visit(child, f"{path}/{_escape_pointer_token(key)}")
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}/{index}")
            return
        literal = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        fields.append(f"{path} | {value_type} | {literal}")

    visit(candidate, "")
    if not fields:
        return "No scalar fields."
    return "\n".join(fields)
