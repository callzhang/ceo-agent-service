"""Codex structural output for the current role wire models."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import JsonValue, RootModel, ValidationError

from app.agent_result import ResultParseError, parse_agent_text_result
from app.agent_wire_contracts import AuditAgentWireResult, consumer_wire_model


ResultT = TypeVar("ResultT")


_UNSUPPORTED = {
    "oneOf", "allOf", "if", "then", "else", "not",
    "dependentRequired", "dependentSchemas", "default", "title",
}
_JSON_VALUE_SCHEMA = {"anyOf": [
    {"type": "string"}, {"type": "number"}, {"type": "boolean"},
    {"type": "null"},
    {"type": "array", "items": {"$ref": "#/$defs/JsonValue"}},
    {"type": "object", "additionalProperties": {"$ref": "#/$defs/JsonValue"}},
]}


def _model(role: str, purpose: str):
    if role == "consumer":
        return consumer_wire_model(purpose)
    if role == "audit" and purpose == "general":
        return AuditAgentWireResult
    raise ValueError(f"unknown role/purpose: {role}/{purpose}")


def _resolve(node: dict, definitions: dict) -> dict:
    if "$ref" not in node:
        return node
    return definitions[node["$ref"].removeprefix("#/$defs/")]


def _project(node: dict) -> dict:
    if not node:
        node = _JSON_VALUE_SCHEMA
    result = {key: copy.deepcopy(value) for key, value in node.items()
              if key not in _UNSUPPORTED}
    if node.get("type") == "object" and isinstance(node.get("additionalProperties"), dict):
        entries = {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "value": _project(node["additionalProperties"]),
        }, "required": ["key", "value"], "additionalProperties": False}}
        for source, target in (("minProperties", "minItems"), ("maxProperties", "maxItems")):
            if source in node:
                entries[target] = node[source]
        return {"type": "object", "properties": {"entries": entries},
                "required": ["entries"], "additionalProperties": False,
                "description": "Lossless JSON object: entries holds each original key and value."}
    for key in ("properties", "$defs"):
        if key in node:
            result[key] = {name: _project(child) for name, child in node[key].items()}
    if "anyOf" in node:
        result["anyOf"] = [_project(child) for child in node["anyOf"]]
    if "items" in node:
        result["items"] = _project(node["items"])
    if node.get("type") == "object":
        result["required"] = list(result.get("properties", {}))
        result["additionalProperties"] = False
    return result


def native_output_schema(role: str, *, purpose: str = "general") -> dict:
    """Project the current complete wire model into Codex's structural subset."""
    canonical = _model(role, purpose).model_json_schema()
    root = copy.deepcopy(_resolve(canonical, canonical["$defs"]))
    root["$defs"] = canonical["$defs"]
    return _project(root)


def native_output_schema_path(role: str, *, purpose: str = "general") -> Path:
    _model(role, purpose)
    name = f"consumer_{purpose}" if role == "consumer" else "audit"
    return Path(__file__).resolve().parent / "schemas" / f"{name}_agent_native_output.schema.json"


def _matches(value, node: dict, definitions: dict) -> bool:
    node = _resolve(node, definitions)
    kind = node.get("type")
    return (
        (kind == "object" and isinstance(value, dict))
        or (kind == "array" and isinstance(value, list))
        or (kind == "string" and isinstance(value, str))
        or (kind == "boolean" and type(value) is bool)
        or (kind == "null" and value is None)
        or (kind == "integer" and type(value) is int)
        or (kind == "number" and type(value) in (int, float))
    )


def _decode(value, node: dict, definitions: dict):
    node = _resolve(node, definitions)
    if not node:
        node = _JSON_VALUE_SCHEMA
    if "anyOf" in node:
        branch = next((child for child in node["anyOf"]
                       if _matches(value, child, definitions)), None)
        if branch is None:
            raise ValueError("native value does not match its wire type")
        return _decode(value, branch, definitions)
    if node.get("type") == "object" and isinstance(value, dict):
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            if set(value) != {"entries"} or not isinstance(value["entries"], list):
                raise ValueError("native JSON object must contain only entries")
            restored = {}
            for entry in value["entries"]:
                if (not isinstance(entry, dict) or set(entry) != {"key", "value"}
                        or not isinstance(entry["key"], str)):
                    raise ValueError("native JSON entry requires a string key and value")
                if entry["key"] in restored:
                    raise ValueError("duplicate native JSON object key")
                restored[entry["key"]] = _decode(entry["value"], additional, definitions)
            return restored
        properties = node.get("properties", {})
        return {key: _decode(item, properties[key], definitions)
                if key in properties else item for key, item in value.items()}
    if node.get("type") == "array" and isinstance(value, list):
        return [_decode(item, node["items"], definitions) for item in value]
    return value


class _NativeObject(RootModel[dict[str, JsonValue]]):
    pass


def parse_native_output(raw: str, role: str, *, purpose: str = "general") -> str:
    """Restore ordinary JSON objects, then enforce the complete current contract."""
    model = _model(role, purpose)
    schema = model.model_json_schema()
    candidate = parse_agent_text_result(raw)
    try:
        native = _NativeObject.model_validate_json(candidate).root
        restored = _decode(native, schema, schema["$defs"])
        result = json.dumps(restored, ensure_ascii=False)
        model.model_validate_json(result)
    except (ValueError, ValidationError, TypeError) as exc:
        raise ResultParseError("native result failed current wire contract") from exc
    return result


def parse_native_result(
    raw: str, role: str, *, parser: Callable[[str], ResultT], purpose: str = "general"
) -> ResultT:
    """Pass a strictly restored native result through the existing role parser."""
    restored = parse_native_output(raw, role, purpose=purpose)
    event = json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": restored},
    }, ensure_ascii=False)
    return parser(event)
