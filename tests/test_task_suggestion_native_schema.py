from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError

from app.task_models import TaskDecision, task_agent_output_schema
from evals.trajectory_optimization.task_suggestion_native_schema import suggestion_output_schema

ROOT = Path(__file__).resolve().parents[1]


def wire():
    return json.loads((ROOT / "evals/trajectory_optimization/task_suggestion_wire.v1.json").read_text())


def validator(schema):
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/TaskDecision"})


@pytest.mark.parametrize("changes", [
    {"owner_kind": "unknown"}, {"owner_relation": "unknown"},
    {"owner_name": "陈明"}, {"owner_user_id": "anonymous-user"},
    {"status": "open"}, {"business_relevance": "relevant"},
    {"project": None, "project_link_evidence": []},
    {"transition": "apply_acceptance"},
    {"formal_basis": "explicit_assignment"},
    {"owner_evidence": {"source_ref": "anonymous", "excerpt": "陈明负责核验"}},
    {"date_evidence": [{"kind": "requested_deadline_at", "value": "2026-10-16", "source_ref": "anonymous", "source_excerpt": "2026-10-16", "actor_user_id": "anonymous-user", "actor_name": "陈明"}]},
    {"action": "update_task", "transition": "update_fields", "task_id": None},
])
def test_native_suggestion_rejects_existing_business_invariant_violations(changes):
    value = {**wire(), **changes}
    assert validator(task_agent_output_schema()).is_valid(value)
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(value)
    assert not validator(suggestion_output_schema()).is_valid(value)


@pytest.mark.parametrize("changes", [{}, {"action": "update_task", "transition": "update_fields", "task_id": 7}, {"owner_name": " \t", "owner_user_id": None}])
def test_valid_suggestion_forms_preserve_field_values(changes):
    value = {**wire(), **changes}
    TaskDecision.model_validate(value)
    assert validator(suggestion_output_schema()).is_valid(value)


def test_actual_source_task_fields_remain_available():
    value = {**wire(), "suggestion": None, "owner_kind": "individual", "owner_relation": "explicit_assignment", "owner_name": "陈明", "owner_evidence": {"source_ref": "anonymous", "excerpt": "陈明负责核实验收材料"}}
    TaskDecision.model_validate(value)
    assert validator(suggestion_output_schema()).is_valid(value)


def test_base_schema_and_other_definitions_are_untouched():
    original = task_agent_output_schema()
    frozen = deepcopy(original)
    result = suggestion_output_schema()
    assert original == frozen
    for key, value in original["$defs"].items():
        if key != "TaskDecision":
            assert result["$defs"][key] == value
    assert {k: v for k, v in result.items() if k != "$defs"} == {k: v for k, v in original.items() if k != "$defs"}
