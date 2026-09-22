from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class BusinessTaskStage(StrEnum):
    CANDIDATE = "candidate"
    FORMAL = "formal"


class BusinessTaskStatus(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    MERGED = "merged"


class BusinessCommitmentStatus(StrEnum):
    UNCOMMITTED = "uncommitted"
    ASSIGNED_UNACCEPTED = "assigned_unaccepted"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class BusinessFormalBasis(StrEnum):
    EXPLICIT_ASSIGNMENT = "explicit_assignment"
    EXPLICIT_COMMITMENT = "explicit_commitment"
    APPROVED_PLAN = "approved_plan"


class BusinessRelevance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BusinessSignalSource(StrEnum):
    DINGTALK_MESSAGE = "dingtalk_message"
    AI_MINUTES = "ai_minutes"
    EMAIL = "email"
    DOCUMENT = "document"
    MANUAL = "manual"


class BusinessSignalKind(StrEnum):
    ASSIGNMENT = "assignment"
    COMMITMENT = "commitment"
    PROGRESS = "progress"
    BLOCKER = "blocker"
    DECISION = "decision"


class BusinessEvidenceKind(StrEnum):
    ASSIGNMENT = "assignment"
    COMMITMENT = "commitment"
    PROGRESS = "progress"
    COMPLETION = "completion"
    BLOCKER = "blocker"


class _FrozenBusinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BusinessTaskSignal(_FrozenBusinessModel):
    id: int
    source: BusinessSignalSource
    source_ref: str
    kind: BusinessSignalKind
    summary: str
    observed_at: datetime
    created_at: datetime


class BusinessTask(_FrozenBusinessModel):
    id: int
    title: str
    stage: BusinessTaskStage
    status: BusinessTaskStatus
    commitment_status: BusinessCommitmentStatus
    formal_basis: BusinessFormalBasis | None = None
    business_relevance: BusinessRelevance
    source_signal_id: int | None = None
    merged_into_task_id: int | None = None
    project_id: int | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _validate_stage_and_merge_invariants(self) -> BusinessTask:
        if self.stage is BusinessTaskStage.FORMAL and self.formal_basis is None:
            raise ValueError("formal task requires formal_basis")
        if self.stage is BusinessTaskStage.CANDIDATE and self.formal_basis is not None:
            raise ValueError("candidate task cannot have formal_basis")
        if (
            self.status is BusinessTaskStatus.MERGED
            and self.merged_into_task_id is None
        ):
            raise ValueError("merged task requires merged_into_task_id")
        if (
            self.status is not BusinessTaskStatus.MERGED
            and self.merged_into_task_id is not None
        ):
            raise ValueError("merged_into_task_id is only valid for only merged tasks")
        return self


class BusinessTaskEvidence(_FrozenBusinessModel):
    id: int
    task_id: int
    kind: BusinessEvidenceKind
    source_signal_id: int | None = None
    summary: str
    observed_at: datetime
    created_at: datetime
