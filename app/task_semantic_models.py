from __future__ import annotations

from enum import StrEnum
import json
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class BusinessTaskStage(StrEnum):
    CANDIDATE = "candidate"
    FORMAL = "formal"


class BusinessTaskStatus(StrEnum):
    OPEN = "open"
    WAITING = "waiting"
    DONE = "done"
    CANCELLED = "cancelled"
    MERGED = "merged"


class CommitmentStatus(StrEnum):
    NONE = "none"
    ASSIGNED_UNACCEPTED = "assigned_unaccepted"
    ACCEPTED = "accepted"
    DISPUTED = "disputed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class FormalTaskBasis(StrEnum):
    EXPLICIT_COMMITMENT = "explicit_commitment"
    EXPLICIT_ASSIGNMENT = "explicit_assignment"
    EXTERNAL_TODO = "external_todo"
    MEETING_ACTION_ITEM = "meeting_action_item"


class BusinessRelevance(StrEnum):
    UNKNOWN = "unknown"
    NOT_RELEVANT = "not_relevant"
    RELEVANT = "relevant"


class AttentionCategory(StrEnum):
    FYI = "fyi"
    WATCH = "watch"
    DECISION = "decision"
    PUSH = "push"


class AttentionStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"


class BusinessEvidenceRole(StrEnum):
    DISCOVERY = "discovery"
    COMMITMENT = "commitment"
    ASSIGNMENT = "assignment"
    ACCEPTANCE = "acceptance"
    COMPLETION = "completion"
    CORRECTION = "correction"
    MERGE_IDENTITY = "merge_identity"
    RELEVANCE = "relevance"
    RESOLUTION = "resolution"


class BusinessTaskEventType(StrEnum):
    CREATED = "created"
    PROMOTED = "promoted"
    COMMITMENT_CHANGED = "commitment_changed"
    OWNER_CHANGED = "owner_changed"
    DEADLINE_CHANGED = "deadline_changed"
    STATUS_CHANGED = "status_changed"
    RELEVANCE_CHANGED = "relevance_changed"
    MERGED = "merged"


class BusinessRelationType(StrEnum):
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    SUPPORTS = "supports"
    SUPERSEDES = "supersedes"
    RELATED_TO = "related_to"


class BusinessRelationStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class BusinessAnchorType(StrEnum):
    PROJECT = "project"
    OKR = "okr"
    CUSTOMER = "customer"
    PRODUCT = "product"
    REVENUE = "revenue"
    FINANCING = "financing"
    CASH = "cash"
    KEY_HIRE = "key_hire"
    PERSONNEL = "personnel"
    COMPANY_PRIORITY = "company_priority"
    MATTER = "matter"


class BusinessAttentionEventType(StrEnum):
    OPENED = "opened"
    UPDATED = "updated"
    CATEGORY_CHANGED = "category_changed"
    RESOLVED = "resolved"
    REOPENED = "reopened"


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def _json_object(value: str) -> str:
    if not isinstance(json.loads(value), dict):
        raise ValueError("JSON object required")
    return value


def _json_array(value: str) -> str:
    if not isinstance(json.loads(value), list):
        raise ValueError("JSON array required")
    return value


Nonblank = Annotated[str, AfterValidator(_nonblank)]
JsonObject = Annotated[str, AfterValidator(_json_object)]
JsonArray = Annotated[str, AfterValidator(_json_array)]
ReferenceId = Annotated[int, Field(strict=True, gt=0)]


class _FrozenBusinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BusinessTaskSignal(_FrozenBusinessModel):
    id: int
    source_type: Nonblank
    source_ref: Nonblank
    source_time: str = ""
    conversation_id: str = ""
    conversation_title: str = ""
    author_user_id: str = ""
    author_name: str = ""
    evidence_text: Nonblank
    context_json: JsonObject = "{}"
    dedupe_key: Nonblank
    created_at: str


class BusinessTask(_FrozenBusinessModel):
    id: int
    title: Nonblank
    description: str = ""
    stage: BusinessTaskStage
    status: BusinessTaskStatus = BusinessTaskStatus.OPEN
    formal_basis: FormalTaskBasis | None = None
    commitment_status: CommitmentStatus = CommitmentStatus.NONE
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence_json: JsonObject = "{}"
    deadline_at: str = ""
    business_relevance: BusinessRelevance = BusinessRelevance.UNKNOWN
    missing_evidence_json: JsonArray = "[]"
    merged_into_task_id: ReferenceId | None = None
    created_at: str
    updated_at: str
    last_activity_at: str

    @model_validator(mode="after")
    def validate_state(self) -> BusinessTask:
        if self.stage is BusinessTaskStage.FORMAL and self.formal_basis is None:
            raise ValueError("formal task requires formal_basis")
        if self.stage is BusinessTaskStage.CANDIDATE and self.formal_basis is not None:
            raise ValueError("candidate cannot carry formal_basis")
        if (self.status is BusinessTaskStatus.MERGED) != (
            self.merged_into_task_id is not None
        ):
            raise ValueError("only a merged task must carry merged_into_task_id")
        if self.merged_into_task_id == self.id:
            raise ValueError("task cannot merge into itself")
        return self


class BusinessTaskEvidence(_FrozenBusinessModel):
    task_id: ReferenceId
    signal_id: ReferenceId
    evidence_role: BusinessEvidenceRole
    created_at: str


class BusinessTaskEvent(_FrozenBusinessModel):
    id: int
    task_id: ReferenceId
    event_type: BusinessTaskEventType
    signal_id: ReferenceId | None
    before_json: JsonObject
    after_json: JsonObject
    reason: Nonblank
    created_at: str


class BusinessTaskRelation(_FrozenBusinessModel):
    from_task_id: ReferenceId
    to_task_id: ReferenceId
    relation_type: BusinessRelationType
    status: BusinessRelationStatus = BusinessRelationStatus.PROPOSED
    supporting_signal_id: ReferenceId
    reason: str = ""
    created_at: str

    @model_validator(mode="after")
    def validate_distinct_tasks(self) -> BusinessTaskRelation:
        if self.from_task_id == self.to_task_id:
            raise ValueError("relations require distinct tasks")
        return self


class BusinessWorkCluster(_FrozenBusinessModel):
    id: int
    title: Nonblank
    created_at: str


class BusinessWorkClusterTask(_FrozenBusinessModel):
    cluster_id: ReferenceId
    task_id: ReferenceId
    created_at: str


class BusinessAnchor(_FrozenBusinessModel):
    id: int
    anchor_type: BusinessAnchorType
    anchor_ref: Nonblank
    title: Nonblank
    active: bool = True
    created_at: str


class BusinessTaskAnchorLink(_FrozenBusinessModel):
    id: int
    task_id: ReferenceId
    anchor_id: ReferenceId
    status: BusinessRelationStatus = BusinessRelationStatus.PROPOSED
    active: bool = True
    evidence_signal_id: ReferenceId
    reason: str = ""
    created_at: str


class BusinessProject(_FrozenBusinessModel):
    id: int
    canonical_anchor_id: ReferenceId
    anchor_type: Literal["project"] = "project"
    title: Nonblank
    registry_source: Nonblank
    created_at: str


class BusinessProjectCandidate(_FrozenBusinessModel):
    id: int
    cluster_id: ReferenceId
    title: Nonblank
    reason: Nonblank
    status: BusinessRelationStatus = BusinessRelationStatus.PROPOSED
    confirmed_project_id: ReferenceId | None = None
    confirmation_signal_id: ReferenceId | None = None
    created_at: str

    @model_validator(mode="after")
    def validate_confirmation(self) -> BusinessProjectCandidate:
        confirmed = self.status is BusinessRelationStatus.CONFIRMED
        if confirmed != (self.confirmed_project_id is not None) or confirmed != (
            self.confirmation_signal_id is not None
        ):
            raise ValueError(
                "only confirmed candidates must reference a project and confirmation signal"
            )
        return self


class BusinessAttentionItem(_FrozenBusinessModel):
    id: int
    stable_key: Nonblank
    category: AttentionCategory
    status: AttentionStatus = AttentionStatus.ACTIVE
    title: Nonblank
    business_area: str
    why_attention: Nonblank
    current_state: Nonblank
    ceo_action: Nonblank
    anchor_id: ReferenceId
    evidence_signal_id: ReferenceId
    resolution_signal_id: ReferenceId | None = None
    resolved_at: str = ""
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_resolution(self) -> BusinessAttentionItem:
        if self.status is AttentionStatus.RESOLVED:
            if self.resolution_signal_id is None or not self.resolved_at.strip():
                raise ValueError(
                    "resolved attention requires resolution evidence and time"
                )
        elif self.resolution_signal_id is not None or self.resolved_at:
            raise ValueError(
                "active attention cannot carry resolution evidence or time"
            )
        return self


class BusinessAttentionTask(_FrozenBusinessModel):
    attention_item_id: ReferenceId
    task_id: ReferenceId
    created_at: str


class BusinessAttentionProposalTask(_FrozenBusinessModel):
    """Explicit desired membership, retained apart from current eligibility."""

    attention_item_id: ReferenceId
    task_id: ReferenceId
    created_at: str


class BusinessAttentionEvent(_FrozenBusinessModel):
    id: int
    attention_item_id: ReferenceId
    event_type: BusinessAttentionEventType
    signal_id: ReferenceId
    before_json: JsonObject
    after_json: JsonObject
    reason: Nonblank
    created_at: str


class BusinessLegacyLink(_FrozenBusinessModel):
    id: int
    signal_id: ReferenceId | None = None
    task_id: ReferenceId | None = None
    cluster_id: ReferenceId | None = None
    anchor_id: ReferenceId | None = None
    project_id: ReferenceId | None = None
    project_candidate_id: ReferenceId | None = None
    attention_item_id: ReferenceId | None = None
    work_project_id: ReferenceId | None = None
    work_todo_id: ReferenceId | None = None
    work_update_id: ReferenceId | None = None
    created_at: str

    @model_validator(mode="after")
    def validate_endpoints(self) -> BusinessLegacyLink:
        semantic_ids = (
            self.signal_id,
            self.task_id,
            self.cluster_id,
            self.anchor_id,
            self.project_id,
            self.project_candidate_id,
            self.attention_item_id,
        )
        legacy_ids = (self.work_project_id, self.work_todo_id, self.work_update_id)
        if sum(value is not None for value in semantic_ids) != 1:
            raise ValueError("legacy link requires exactly one semantic object")
        if sum(value is not None for value in legacy_ids) != 1:
            raise ValueError("legacy link requires exactly one legacy row")
        return self
