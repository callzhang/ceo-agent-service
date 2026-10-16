"""Relationship diagnostics for rejected results; never an adoption model."""
from pydantic import ConfigDict, ValidationError, create_model, model_validator

from app.task_models import (
    ProjectDecision, ProjectSelector, TaskAgentDecision, TaskDecision,
    TaskProjectAssessment, StrictTaskModel,
)


def _fields(model, names):
    return {name: (model.model_fields[name].annotation, model.model_fields[name])
            for name in names}


class _RelationModel(StrictTaskModel):
    model_config = ConfigDict(extra="ignore")


_TaskRelations = create_model(
    "_TaskRelations", __base__=_RelationModel,
    **_fields(TaskDecision, ("action", "project")),
)


class _AssessmentIdentity(_RelationModel):
    @model_validator(mode="after")
    def validate_selector(self):
        if self.anchor_id is not None or self.project_decision_index is not None:
            ProjectSelector.model_validate({
                "anchor_id": self.anchor_id,
                "project_decision_index": self.project_decision_index,
            })
        return self


_AssessmentRelations = create_model(
    "_AssessmentRelations", __base__=_AssessmentIdentity,
    **_fields(TaskProjectAssessment, (
        "project_title", "anchor_id", "project_decision_index",
        "decision_indexes", "attention_proposal", "assessment_basis",
    )),
)


class _EnvelopeRelations(_RelationModel):
    project_decisions: list[ProjectDecision]
    task_decisions: list[_TaskRelations]
    project_assessments: list[_AssessmentRelations]
    todo_changes: list[object] = []
    update_summary: str = ""


def task_envelope_problems(candidate: dict) -> list[str]:
    # Only typed identities/relations are used. Invalid relationship syntax
    # already appears in full-model errors and cannot support extra deductions.
    try:
        relations = _EnvelopeRelations.model_validate(candidate)
    except ValidationError:
        return []
    try:
        TaskAgentDecision.validate_decision_envelope(relations)
    except ValueError as exc:
        return [str(exc)]
    return []
