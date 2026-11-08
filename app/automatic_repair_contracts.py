"""Versioned native result contracts for automatic code repair."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

REPAIR_DIAGNOSIS_CONTRACT = "automatic_repair.diagnosis.v1"
REPAIR_PR_CONTRACT = "automatic_repair.pr.v3"


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
