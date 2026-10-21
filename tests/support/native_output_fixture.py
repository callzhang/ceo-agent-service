"""Adapt valid scripted wire results to the current Codex native format."""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from app.agent_wire_contracts import AuditAgentWireResult, consumer_wire_model
from app.native_agent_output import (
    _JSON_VALUE_SCHEMA,
    _consumer_action_branches,
    _matches,
    _resolve,
    native_output_schema,
    native_output_schema_path,
)


def _encode(value, node: dict, definitions: dict):
    node = _resolve(node, definitions)
    if not node:
        node = _JSON_VALUE_SCHEMA
    if "anyOf" in node:
        branch = next((part for part in node["anyOf"]
                       if _matches(value, part, definitions)), None)
        if branch is None:
            return value
        return _encode(value, branch, definitions)
    if node.get("type") == "object" and isinstance(value, dict):
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            return {"entries": [
                {"key": key, "value": _encode(item, additional, definitions)}
                for key, item in value.items()
            ]}
        properties = node.get("properties", {})
        return {key: _encode(item, properties[key], definitions)
                if key in properties else item for key, item in value.items()}
    if node.get("type") == "array" and isinstance(value, list):
        return [_encode(item, node["items"], definitions) for item in value]
    return value


def native_stream_for_command(stdout: str, command: list[str]) -> str:
    """Convert valid legacy test fixtures; leave malformed fixtures untouched."""
    if "--output-schema" not in command:
        return stdout
    path = command[command.index("--output-schema") + 1]
    if path == str(native_output_schema_path("audit")):
        role, purpose, model = "audit", "general", AuditAgentWireResult
    elif path == str(native_output_schema_path("consumer", purpose="oa")):
        role, purpose, model = "consumer", "oa", consumer_wire_model("oa")
    elif path == str(native_output_schema_path("consumer", purpose="general")):
        role, purpose, model = "consumer", "general", consumer_wire_model("general")
    else:
        return stdout
    validator = Draft202012Validator(native_output_schema(role, purpose=purpose))
    schema = model.model_json_schema()
    encoded_lines = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
            item = event.get("item") if isinstance(event, dict) else None
            if (event.get("type") == "item.completed" and isinstance(item, dict)
                    and item.get("type") == "agent_message"):
                value = json.loads(item["text"])
                if isinstance(value, dict) and not validator.is_valid(value):
                    model.model_validate(value)
                    if role == "consumer":
                        root = _resolve(schema, schema["$defs"])
                        branch = next(part for part in _consumer_action_branches(root)
                                      if part["properties"]["action"]["const"] == value["action"])
                        encoded = {key: _encode(item, root["properties"][key], schema["$defs"])
                                   for key, item in value.items()
                                   if key not in {"action", "target", "payload"}}
                        encoded["decision"] = {
                            "action": value["action"],
                            "target": _encode(value["target"], branch["properties"]["target"],
                                              schema["$defs"]),
                            "payload": _encode(value["payload"], branch["properties"]["payload"],
                                               schema["$defs"]),
                        }
                    else:
                        encoded = _encode(value, schema, schema["$defs"])
                    item["text"] = json.dumps(encoded, ensure_ascii=False)
                    line = json.dumps(event, ensure_ascii=False)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        encoded_lines.append(line)
    return "\n".join(encoded_lines)
