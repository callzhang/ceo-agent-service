"""Eval-only structural expression of the existing Task suggestion invariant.

The production generator and Pydantic validators remain unchanged. Ordinary
source decisions retain their existing complete field contract. Suggestion
alternatives express the existing record/update and empty actual-owner rules.
"""
from copy import deepcopy

from app.task_models import task_agent_output_schema


def suggestion_output_schema():
    schema = task_agent_output_schema()
    original = schema["$defs"]["TaskDecision"]
    properties = original["properties"]
    def non_null(field):
        return deepcopy(next(part for part in properties[field]["anyOf"] if part.get("type") != "null"))

    # Native strict owner_evidence is either its complete source object or null;
    # null is the existing wire spelling of the Pydantic empty dictionary.
    empty_actual = {field: {"type": "null"} for field in (
        "formal_basis", "owner_evidence", "owner_kind", "owner_relation", "status", "business_relevance"
    )}
    empty_actual.update({field: {"anyOf": [{"type": "string", "pattern": r"^\s*$"}, {"type": "null"}]} for field in ("owner_name", "owner_user_id")})
    empty_actual["date_evidence"] = {"anyOf": [{**non_null("date_evidence"), "maxItems": 0}, {"type": "null"}]}
    suggestion = {**empty_actual, "project": non_null("project"), "suggestion": non_null("suggestion")}
    restrictions = [
        {"suggestion": {"type": "null"}},
        {**suggestion, "action": {"type": "string", "enum": ["record_candidate"]}, "transition": {"type": "string", "enum": ["none"]}},
        {**suggestion, "action": {"type": "string", "enum": ["update_task"]}, "transition": {"type": "string", "enum": ["update_fields"]}, "task_id": non_null("task_id")},
    ]
    shared = {}
    for index, (field, definition) in enumerate(properties.items()):
        if any(field not in restriction for restriction in restrictions):
            name = f"TS{index}"
            schema["$defs"][name] = definition
            shared[field] = {"$ref": f"#/$defs/{name}"}
    variants = []
    for restriction in restrictions:
        variant = {key: deepcopy(value) for key, value in original.items() if key != "properties"}
        variant["properties"] = {}
        for field, definition in properties.items():
            selected = deepcopy(restriction.get(field, shared.get(field)))
            if field in restriction and "description" in definition:
                selected["description"] = definition["description"]
            variant["properties"][field] = selected
        variants.append(variant)
    schema["$defs"]["TaskDecision"] = {"anyOf": variants}
    return schema
