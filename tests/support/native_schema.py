"""Encode valid canonical fixtures as native constrained-output responses."""

from jsonschema import Draft202012Validator

from app.agent_wire_contracts import AuditAgentWireResult, ConsumerAgentWireResult


def native_wire_fixture(payload: dict, *, role: str) -> dict:
    model = ConsumerAgentWireResult if role == "consumer" else AuditAgentWireResult
    canonical = model.model_validate(payload).model_dump(mode="json")
    schema = model.model_json_schema()
    definitions = schema["$defs"]

    def encode(value, node):
        if "$ref" in node:
            node = definitions[node["$ref"].removeprefix("#/$defs/")]
        if not node:
            if isinstance(value, dict):
                return {"entries": [
                    {"key": key, "value": encode(item, {})}
                    for key, item in value.items()
                ]}
            if isinstance(value, list):
                return [encode(item, {}) for item in value]
            return value
        if "anyOf" in node:
            branch = next(
                child for child in node["anyOf"]
                if Draft202012Validator({"$defs": definitions, **child}).is_valid(value)
            )
            return encode(value, branch)
        if isinstance(value, dict):
            additional = node.get("additionalProperties")
            if isinstance(additional, dict):
                return {"entries": [
                    {"key": key, "value": encode(item, additional)}
                    for key, item in value.items()
                ]}
            return {
                key: encode(item, node["properties"][key])
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [encode(item, node["items"]) for item in value]
        return value

    return encode(canonical, schema)
