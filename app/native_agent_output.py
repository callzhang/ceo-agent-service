"""Codex native output for the existing Consumer wire contract."""

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
                "description": "Lossless JSON object encoded as key/value entries."}
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


def _consumer_action_branches(root: dict) -> list[dict]:
    for clause in root.get("allOf", []):
        branches = clause.get("oneOf", [])
        if branches and all(
            set(branch.get("properties", {})) >= {"action", "target", "payload"}
            and "const" in branch["properties"]["action"]
            for branch in branches
        ):
            return branches
    raise ValueError("consumer action branch schema missing")


def native_output_schema(role: str = "consumer", *, purpose: str = "general") -> dict:
    """Project the current purpose wire model into Codex's structural subset."""
    if role == "audit":
        canonical = AuditAgentWireResult.model_json_schema()
        root = copy.deepcopy(_resolve(canonical, canonical["$defs"]))
        root["$defs"] = canonical["$defs"]
        projected = _project(root)
        outcome_feedback = [
            {
                "type": "object",
                "properties": {
                    "outcome": {"const": outcome},
                    "feedback": (
                        {"type": "null"} if feedback_type == "null"
                        else {"$ref": "#/$defs/_AuditFeedbackWire"}
                    ),
                },
                "required": ["outcome", "feedback"],
                "additionalProperties": False,
            }
            for outcome, feedback_type in (
                ("approve", "null"), ("return", "object"),
                ("reject", "object"), ("failed", "null"),
            )
        ]
        projected["properties"].pop("outcome")
        projected["properties"].pop("feedback")
        projected["properties"]["decision"] = {"anyOf": outcome_feedback}
        projected["required"] = list(projected["properties"])
        return projected
    if role != "consumer":
        raise ValueError(f"unknown native output role: {role}")
    canonical = consumer_wire_model(purpose).model_json_schema()
    root = copy.deepcopy(_resolve(canonical, canonical["$defs"]))
    root["$defs"] = canonical["$defs"]
    projected = _project(root)
    for name in ("action", "target", "payload"):
        projected["properties"].pop(name)
    branches = []
    for branch in _consumer_action_branches(root):
        shape = _project({
            "type": "object", "properties": branch["properties"],
            "required": ["action", "target", "payload"],
            "additionalProperties": False,
        })
        shape["properties"]["action"] = {
            "type": "string", "enum": [branch["properties"]["action"]["const"]],
        }
        branches.append(shape)
    projected["properties"]["decision"] = {"anyOf": branches}
    projected["required"] = list(projected["properties"])
    return projected


def native_output_schema_path(role: str = "consumer", *, purpose: str = "general") -> Path:
    if role == "audit":
        return Path(__file__).resolve().parent / "schemas" / "audit_agent_native_output.schema.json"
    if role != "consumer":
        raise ValueError(f"unknown native output role: {role}")
    consumer_wire_model(purpose)
    return Path(__file__).resolve().parent / "schemas" / (
        f"consumer_{purpose}_agent_native_output.schema.json"
    )


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
    if "anyOf" in node and isinstance(value, dict) and set(value) == {"entries"}:
        object_branch = next((child for child in node["anyOf"]
                              if child.get("type") == "object"
                              and "entries" in child.get("properties", {})), None)
        if object_branch is not None:
            entry_schema = object_branch["properties"]["entries"]["items"]
            value_schema = entry_schema["properties"]["value"]
            restored = {}
            for entry in value["entries"]:
                if (not isinstance(entry, dict) or set(entry) != {"key", "value"}
                        or not isinstance(entry["key"], str)
                        or entry["key"] in restored):
                    raise ValueError("native JSON value entries are invalid")
                restored[entry["key"]] = _decode(entry["value"], value_schema, definitions)
            return restored
    if "anyOf" in node:
        if isinstance(value, dict) and "outcome" in value:
            branch = next((child for child in node["anyOf"]
                           if child.get("properties", {}).get("outcome", {}).get("const")
                           == value["outcome"]), None)
            if branch is not None:
                return _decode(value, branch, definitions)
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


def parse_native_output(raw: str, role: str = "consumer", *, purpose: str = "general") -> str:
    """Restore lossless JSON objects, then enforce the complete wire contract."""
    if role == "audit":
        model = AuditAgentWireResult
    elif role == "consumer":
        model = consumer_wire_model(purpose)
    else:
        raise ValueError(f"unknown native output role: {role}")
    schema = model.model_json_schema()
    try:
        candidate = parse_agent_text_result(raw)
    except ResultParseError as exc:
        if str(exc) == "no agent text result found in Codex JSONL":
            raise ResultParseError(
                "no valid typed result JSON found in Codex JSONL"
            ) from exc
        raise
    try:
        native = _NativeObject.model_validate_json(candidate).root
        if role == "audit":
            native_schema = native_output_schema("audit")
            restored_native = _decode(native, native_schema, native_schema["$defs"])
            decision = restored_native.pop("decision", None)
            if (not isinstance(decision, dict)
                    or set(decision) != {"outcome", "feedback"}
                    or "outcome" in restored_native or "feedback" in restored_native):
                raise ValueError("native audit decision is invalid")
            restored = {**restored_native, **decision}
            result = json.dumps(restored, ensure_ascii=False)
            model.model_validate_json(result)
            return result
        decision = native.pop("decision", None)
        if (not isinstance(decision, dict)
                or set(decision) != {"action", "target", "payload"}
                or any(name in native for name in ("action", "target", "payload"))):
            raise ValueError("native consumer decision is invalid")
        action = decision["action"]
        root = _resolve(schema, schema["$defs"])
        branch = next((item for item in _consumer_action_branches(root)
                       if item["properties"]["action"]["const"] == action), None)
        if branch is None:
            raise ValueError("native consumer action is unknown")
        restored = _decode(native, root, schema["$defs"])
        restored.update({
            "action": action,
            "target": _decode(decision["target"], branch["properties"]["target"],
                              schema["$defs"]),
            "payload": _decode(decision["payload"], branch["properties"]["payload"],
                               schema["$defs"]),
        })
        result = json.dumps(restored, ensure_ascii=False)
        model.model_validate_json(result)
    except (ValueError, ValidationError, TypeError) as exc:
        label = "Audit" if role == "audit" else "Consumer"
        raise ResultParseError(f"native result failed current {label} wire contract") from exc
    return result


def parse_native_result(
    raw: str,
    role: str,
    *,
    parser: Callable[[str], ResultT],
    purpose: str = "general",
) -> ResultT:
    """Pass restored output through the existing strict role parser."""
    restored = parse_native_output(raw, role, purpose=purpose)
    event = json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": restored},
    }, ensure_ascii=False)
    return parser(event)
