"""Native Codex structural output and lossless canonical wire conversion."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import ClassVar

from pydantic import JsonValue, RootModel, model_validator

from app.agent_result import parse_typed_agent_result
from app.agent_wire_contracts import ConsumerAgentWireResult, AuditAgentWireResult

_MODELS = {"consumer": ConsumerAgentWireResult, "audit": AuditAgentWireResult}
_UNSUPPORTED = {
    "oneOf",
    "allOf",
    "if",
    "then",
    "else",
    "not",
    "dependentRequired",
    "dependentSchemas",
    "default",
    "title",
}
_JSON_VALUE_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
        {"type": "array", "items": {"$ref": "#/$defs/JsonValue"}},
        {"type": "object", "additionalProperties": {"$ref": "#/$defs/JsonValue"}},
    ]
}


def _resolve(node, definitions):
    return (
        definitions[node["$ref"].removeprefix("#/$defs/")] if "$ref" in node else node
    )


def _project(node):
    if not node:
        node = _JSON_VALUE_SCHEMA
    result = {
        key: copy.deepcopy(value)
        for key, value in node.items()
        if key not in _UNSUPPORTED
    }
    if node.get("type") == "object" and isinstance(
        node.get("additionalProperties"), dict
    ):
        entries = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": _project(node["additionalProperties"]),
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        }
        for source, target in (
            ("minProperties", "minItems"),
            ("maxProperties", "maxItems"),
        ):
            if source in node:
                entries[target] = node[source]
        return {
            "type": "object",
            "properties": {"entries": entries},
            "required": ["entries"],
            "additionalProperties": False,
            "description": "Lossless JSON object: entries contains each original key and its value. Nested JSON objects use the same representation.",
        }
    for key in ("properties", "$defs"):
        if key in node:
            children = dict(node[key])
            if key == "properties":
                # Native constrained generation follows schema key order.
                # Emit the existing evidence/judgment before its action, and
                # the final summary after the complete result.
                if {"sourced_facts", "authored_judgment", "actions"} <= children.keys():
                    children["actions"] = children.pop("actions")
                if "summary" in children:
                    children["summary"] = children.pop("summary")
            result[key] = {name: _project(child) for name, child in children.items()}
    if "anyOf" in node:
        result["anyOf"] = [_project(child) for child in node["anyOf"]]
    if "items" in node:
        result["items"] = _project(node["items"])
    if node.get("type") == "object":
        result["required"] = list(result.get("properties", {}))
        result["additionalProperties"] = False
    return result


def native_output_schema(role: str) -> dict:
    canonical = _MODELS[role].model_json_schema()
    root = copy.deepcopy(_resolve(canonical, canonical["$defs"]))
    root["$defs"] = canonical["$defs"]
    return _project(root)


def native_output_schema_path(role: str) -> Path:
    return (
        Path(__file__).resolve().parent
        / "schemas"
        / f"{role}_agent_native_output.schema.json"
    )


def native_output_instruction(role: str) -> str:
    return (
        "## Native structured output\nReturn the "
        + role
        + " result using the native output schema. "
        "Include every field; use the original empty/default values for fields that do not apply. "
        "JSON objects with arbitrary keys (action target/payload and nested values) use "
        '{"entries":[{"key":"original key","value":original_value}]}. '
        "Arrays and scalar values remain unchanged. The service restores ordinary JSON objects "
        "before applying the complete original business contract; all business rules still apply."
    )


def native_task_prompt(prompt: str, role: str) -> str:
    return prompt + "\n\n" + native_output_instruction(role)


def _matches(value, node, definitions):
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


def _decode(value, node, definitions):
    node = _resolve(node, definitions)
    if not node:
        node = _JSON_VALUE_SCHEMA
    if "anyOf" in node:
        branch = next(
            (child for child in node["anyOf"] if _matches(value, child, definitions)),
            None,
        )
        if branch is None:
            raise ValueError("native value does not match its wire type")
        return _decode(value, branch, definitions)
    if node.get("type") == "object" and isinstance(value, dict):
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            if set(value) != {"entries"} or not isinstance(value["entries"], list):
                raise ValueError("native JSON object must contain only entries")
            result = {}
            for entry in value["entries"]:
                if (
                    not isinstance(entry, dict)
                    or set(entry) != {"key", "value"}
                    or not isinstance(entry["key"], str)
                ):
                    raise ValueError(
                        "native JSON entry requires a string key and value"
                    )
                if entry["key"] in result:
                    raise ValueError("duplicate native JSON object key")
                result[entry["key"]] = _decode(entry["value"], additional, definitions)
            return result
        properties = node.get("properties", {})
        return {
            key: _decode(item, properties[key], definitions)
            if key in properties
            else item
            for key, item in value.items()
        }
    if node.get("type") == "array" and isinstance(value, list):
        return [_decode(item, node["items"], definitions) for item in value]
    return value


class _NativeResult(RootModel[dict[str, JsonValue]]):
    role: ClassVar[str]

    @model_validator(mode="after")
    def restore_canonical_wire(self):
        model = _MODELS[self.role]
        schema = model.model_json_schema()
        self.root = _decode(self.root, schema, schema["$defs"])
        model.model_validate_json(json.dumps(self.root, ensure_ascii=False))
        return self


class _NativeConsumerResult(_NativeResult):
    role = "consumer"


class _NativeAuditResult(_NativeResult):
    role = "audit"


_NATIVE_MODELS = {"consumer": _NativeConsumerResult, "audit": _NativeAuditResult}


def parse_native_output(raw: str, role: str) -> str:
    decoded = parse_typed_agent_result(raw, _NATIVE_MODELS[role])
    return json.dumps(decoded.root, ensure_ascii=False)


def native_result_jsonl(raw: str, role: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": parse_native_output(raw, role)},
        }
    )
