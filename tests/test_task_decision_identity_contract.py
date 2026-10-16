"""Native field guidance preserves the existing Task update/merge contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.task_models import TaskAgentDecision, TaskDecision, task_agent_output_schema


@pytest.mark.parametrize(
    "model_name,field_name,terms",
    [
        ("TaskDecision", "task_id", ("update_task", "existing Task", "update_fields")),
        ("TaskDecision", "target_task_id", ("merge_identity", "distinct", "null")),
        ("TaskDecision", "identity_proposal", ("merge_identity", "distinct", "update_fields")),
        ("TaskDecision", "project", ("supporting Task", "Project selector", "assessment")),
        ("TaskIdentityProposal", "source_task_id", ("task_id", "existing Task")),
        ("TaskIdentityProposal", "target_task_id", ("target_task_id", "distinct")),
        ("TaskIdentityProposal", "reason", ("merge", "scope")),
    ],
)
def test_native_schema_explains_existing_update_and_merge_fields(
    model_name, field_name, terms,
):
    field = task_agent_output_schema()["$defs"][model_name]["properties"][field_name]
    description = field.get("description", "") or next(
        (option["description"] for option in field.get("anyOf", [])
         if option.get("description")),
        "",
    )
    assert description, f"{model_name}.{field_name} needs native field guidance"
    assert all(term in description for term in terms)


def _update(**overrides):
    return {
        "action": "update_task",
        "transition": "update_fields",
        "task_id": 471,
        "source_excerpt": "The owner will revise the wireframe and share its notes.",
        "source_ref": "minutes:wireframe#todos-sha256=example",
        "description": "Add the requested revision and notes to the existing deliverable.",
        **overrides,
    }


def _identity(source=471, target=472):
    return {
        "source_task_id": source,
        "target_task_id": target,
        "identity_evidence": {
            "basis": "same_deliverable_owner_context_time",
            "source_signal_id": 822,
            "target_signal_id": 693,
        },
        "reason": "Two saved Tasks describe the same sourced deliverable.",
    }


def test_scope_update_uses_existing_task_without_identity_proposal():
    decision = TaskDecision.model_validate(_update())
    assert decision.task_id == 471
    assert decision.target_task_id is None
    assert decision.identity_proposal is None


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"task_id": None, "target_task_id": 471}, "update_task requires task_id"),
        ({"identity_proposal": _identity(471, 471)}, "identity proposal requires distinct Tasks"),
        ({"identity_proposal": _identity()}, "identity proposal requires merge_identity transition"),
    ],
)
def test_scope_update_rejects_native_failure_shapes(patch, message):
    with pytest.raises(ValidationError, match=message):
        TaskDecision.model_validate(_update(**patch))


def test_identity_merge_keeps_two_distinct_existing_task_ids():
    decision = TaskDecision.model_validate(_update(
        transition="merge_identity", target_task_id=472, identity_proposal=_identity(),
    ))
    assert decision.identity_proposal.source_task_id == decision.task_id == 471
    assert decision.identity_proposal.target_task_id == decision.target_task_id == 472


def _supporting_task_envelope(project):
    return {
        "project_decisions": [],
        "task_decisions": [{
            **_update(), "action": "record_candidate", "transition": "none",
            "task_id": None, "title": "Revise the wireframe", "project": project,
        }],
        "project_assessments": [{
            "project_title": "Product", "anchor_id": 1, "outcome": "not_needed",
            "reason": "The requested revision is normal progress.",
            "assessment_basis": "current_observation", "decision_indexes": [0],
            "evidence": [{"source_ref": "minutes:wireframe#todos-sha256=example",
                          "source_excerpt": "The owner will revise the wireframe and share its notes."}],
        }],
    }


def test_new_supporting_task_requires_its_own_project_selector():
    with pytest.raises(ValidationError, match="new supporting Task requires a Project selector"):
        TaskAgentDecision.model_validate(_supporting_task_envelope(None))
    decision = TaskAgentDecision.model_validate(
        _supporting_task_envelope({"anchor_id": 1})
    )
    assert decision.task_decisions[0].project.anchor_id == 1


def test_unresolved_project_clue_allows_new_supporting_task_without_selector():
    payload = _supporting_task_envelope(None)
    assessment = payload["project_assessments"][0]
    assessment.pop("anchor_id")
    assessment["outcome"] = "insufficient_evidence"
    assessment["reason"] = "The source names a Project clue without a registered identity."

    decision = TaskAgentDecision.model_validate(payload)

    assert decision.task_decisions[0].project is None
    assert decision.project_assessments[0].anchor_id is None
    assert decision.project_assessments[0].project_decision_index is None


def test_checked_in_native_strict_schema_matches_model_contract():
    path = Path(__file__).parents[1] / "app/schemas/task_agent_decision.schema.json"
    schema = task_agent_output_schema()
    assert json.loads(path.read_text()) == schema
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert definition["required"] == list(definition["properties"])


def test_project_support_mismatch_locates_both_sides_in_correction_prompt():
    from copy import deepcopy
    from app.task_agent import _task_result_validation_repair_prompt

    payload = _supporting_task_envelope({"anchor_id": 1})
    second = deepcopy(payload["project_assessments"][0])
    second.update(project_title="Another Product", anchor_id=2)
    payload["project_assessments"].append(second)
    with pytest.raises(ValidationError, match="supporting Task selector"):
        TaskAgentDecision.model_validate(payload)
    prompt = _task_result_validation_repair_prompt(json.dumps(payload))
    assert "project_assessments[1].decision_indexes" in prompt
    assert "task_decisions[0].project" in prompt
    assert "('anchor', 1)" in prompt
    assert "('anchor', 2)" in prompt
    # Removing the wrong cross-Project membership is valid without changing
    # either Task ownership or the independently assessed second Project.
    second["decision_indexes"] = []
    accepted = TaskAgentDecision.model_validate(payload)
    assert accepted.task_decisions[0].project.anchor_id == 1
    assert accepted.project_assessments[1].anchor_id == 2
