"""Removing presentation labels must preserve the complete Task wire contract."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from app.task_models import TaskAgentDecision, task_agent_output_schema


def test_native_contract_removes_only_redundant_display_titles():
    schema = task_agent_output_schema()
    checked_in = json.loads(
        (Path(__file__).parents[1] / "app/schemas/task_agent_decision.schema.json")
        .read_text(encoding="utf-8")
    )
    assert schema == checked_in

    def normalize_integral_floats(value):
        # Pydantic may render equivalent JSON Schema bounds as 0 or 0.0 across
        # supported dependency versions. Compare their JSON numeric meaning.
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [normalize_integral_floats(item) for item in value]
        if isinstance(value, dict):
            return {key: normalize_integral_floats(item) for key, item in value.items()}
        return value

    canonical = json.dumps(
        normalize_integral_floats(schema),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    # Frozen current-main schema after the project selector fields were added.
    assert (
        hashlib.sha256(canonical.encode()).hexdigest()
        == "9cacda2641f39cd268b5dd3999adf4df7841734c2fc807c815d158fef5ab34b5"
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
