"""Removing presentation labels must preserve the complete Task wire contract."""

from copy import deepcopy
import hashlib
import json

from app.task_models import TaskAgentDecision, task_agent_output_schema


def test_native_contract_removes_only_redundant_display_titles():
    schema = task_agent_output_schema()
    canonical = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    # Independently frozen pre-change native schema generator with string title annotations removed.
    # Contains every description, constraint and field from ae08b7c7.
    assert (
        hashlib.sha256(canonical.encode()).hexdigest()
        == "71be9fce8612df2beec24a3170296e7798b99f66768dfe6d3f232cd4f6286de7"
    )
    assert schema["$defs"]["ProjectContext"]["properties"]["goal"]["type"] == "string"


def test_title_fields_and_literal_business_data_are_preserved(monkeypatch):
    raw = TaskAgentDecision.model_json_schema()
    raw["$defs"]["title"] = {"type": "string", "title": "Definition label"}
    raw["$defs"]["SourceCitation"]["patternProperties"] = {
        "title": {"type": "string", "title": "Pattern label"}
    }
    properties = raw["$defs"]["SourceCitation"]["properties"]
    properties["title"] = {
        "type": "string",
        "title": "Display label",
        "description": "Business title value",
    }
    literal = {
        "title": "Exact  business title\n",
        "type": "object",
        "properties": {"title": "Keep this too"},
    }
    properties["literal_value"] = {
        "const": deepcopy(literal),
        "enum": [deepcopy(literal)],
        "title": "Literal Value",
    }
    monkeypatch.setattr(
        TaskAgentDecision, "model_json_schema", classmethod(lambda cls: deepcopy(raw))
    )
    schema = task_agent_output_schema()
    assert schema["$defs"]["title"] == {"type": "string"}
    result = schema["$defs"]["SourceCitation"]
    assert result["patternProperties"] == {"title": {"type": "string"}}
    assert result["properties"]["title"] == {
        "type": "string",
        "description": "Business title value",
    }
    assert "title" in result["required"]
    assert result["properties"]["literal_value"] == {
        "const": literal,
        "enum": [literal],
    }
