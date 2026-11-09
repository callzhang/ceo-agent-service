"""Versioned native result contracts for automatic code repair."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPAIR_DIAGNOSIS_CONTRACT = "automatic_repair.diagnosis.v1"
REPAIR_PR_CONTRACT = "automatic_repair.pr.v3"
REPAIR_MERGE_CONTRACT = "automatic_repair.merge.v1"


class RepairDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["pr_created", "no_code_change", "blocked"]
    summary: str = Field(min_length=1)


class PRRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["pr_updated", "blocked"]
    summary: str = Field(min_length=1)
    addressed_review_ids: list[str]
    remaining_work: list[str]


class RepairMergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["merged", "pr_updated", "review_required", "blocked"]
    head_sha: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    deterministic_fix: bool
    regression_evidence: list[str]

    @model_validator(mode="after")
    def merged_fix_has_evidence(self):
        if self.outcome == "merged" and (
            not self.deterministic_fix or not self.regression_evidence
        ):
            raise ValueError("merged requires a deterministic fix and regression evidence")
        return self
