"""Schema annotation formatting shared by Agent input builders."""

def _compact_prompt_schema(value):
    """Omit schema annotations, preserving property/definition names and assertions."""
    if isinstance(value, list):
        return [_compact_prompt_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"title", "default"}:
            continue
        if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}:
            result[key] = {name: _compact_prompt_schema(schema) for name, schema in item.items()}
        elif key in {
            "allOf", "anyOf", "oneOf", "prefixItems", "items", "additionalProperties",
            "additionalItems", "contains", "not", "if", "then", "else", "propertyNames",
            "unevaluatedProperties", "unevaluatedItems", "contentSchema",
        }:
            result[key] = _compact_prompt_schema(item)
        else:
            result[key] = item
    return result

