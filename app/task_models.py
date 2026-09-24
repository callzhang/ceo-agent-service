from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo

from app.decision_quality import DecisionQualityResult, DecisionRisk, classify_decision_quality
from app.task_semantic_models import FormalTaskBasis


def _null_means_omitted(field: FieldInfo) -> bool:
    """Optional fields with a non-null default: the model may send null for them."""
    return not field.is_required() and field.default is not None


def _mark_optional_fields_nullable(
    schema: dict[str, Any], model: type[BaseModel]
) -> None:
    """Show the fields that accept null as nullable in the schema handed to the model."""
    properties = schema.get("properties", {})
    for name, field in model.model_fields.items():
        property_schema = properties.get(name)
        if property_schema is None or not _null_means_omitted(field):
            continue
        if any(
            option.get("type") == "null"
            for option in property_schema.get("anyOf", [])
        ):
            continue
        header = {
            key: property_schema.pop(key)
            for key in ("title", "default")
            if key in property_schema
        }
        properties[name] = {
            "anyOf": [property_schema, {"type": "null"}],
            **header,
        }


class StrictTaskModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_mark_optional_fields_nullable
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_null_optional_fields(cls, data: object) -> object:
        # null from the model means "not provided", the same as omitting the
        # key; the declared default applies and the key stays out of
        # model_fields_set.
        if not isinstance(data, dict):
            return data
        return {
            key: value
            for key, value in data.items()
            if not (
                value is None
                and key in cls.model_fields
                and _null_means_omitted(cls.model_fields[key])
            )
        }


OWNER_IDENTITY_FIELD_ALIASES = {
    "user_id": "user_id",
    "owner_user_id": "user_id",
    "name": "display_name",
    "display_name": "display_name",
    "owner_name": "display_name",
    "open_dingtalk_id": "open_dingtalk_id",
}


def owner_identity_record(
    values: Mapping[str, object],
) -> frozenset[tuple[str, str]]:
    record: dict[str, str] = {}
    for source_field, canonical_field in OWNER_IDENTITY_FIELD_ALIASES.items():
        value = str(values.get(source_field) or "").strip()
        if not value:
            continue
        if canonical_field == "display_name":
            value = value.casefold()
        if canonical_field in record and record[canonical_field] != value:
            raise ValueError(f"conflicting owner identity field: {canonical_field}")
        record[canonical_field] = value
    return frozenset(record.items())


def owner_identity_evidence_records(
    evidence: object,
) -> list[frozenset[tuple[str, str]]]:
    items: list[object]
    if isinstance(evidence, list):
        items = list(evidence)
    elif isinstance(evidence, dict):
        items = [evidence]
        for key in ("records", "verified_resolution"):
            nested = evidence.get(key)
            if isinstance(nested, list):
                items.extend(nested)
            elif isinstance(nested, dict):
                items.append(nested)
    else:
        items = []
    records: list[frozenset[tuple[str, str]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = owner_identity_record(item)
        if record:
            records.append(record)
    return records


def owner_identity_is_supported(
    assigned: Mapping[str, object],
    evidence: object,
) -> bool:
    assigned_record = owner_identity_record(assigned)
    if not assigned_record:
        return True
    return any(
        assigned_record <= evidence_record
        for evidence_record in owner_identity_evidence_records(evidence)
    )


class WorkItemSourceType(StrEnum):
    REPLY_ATTEMPT = "reply_attempt"
    AI_MINUTES = "ai_minutes"
    LOCAL_FILE = "local_file"
    MEMORY_RECALL = "memory_recall"
    FOLLOW_UP_COMPLETION_CHECK = "follow_up_completion_check"
    TODO_COMPLETION_CHECK = "todo_completion_check"
    TODO_COMPLETION_EVIDENCE_CANDIDATE = "todo_completion_evidence_candidate"


class WorkItemSourceKind(StrEnum):
    GROUP = "group"
    DIRECT = "direct"
    FILE = "file"
    MINUTES = "minutes"
    MEMORY = "memory"


class ProjectCategory(StrEnum):
    MANAGEMENT = "management"
    STRATEGY = "strategy"
    PROJECTS = "projects"
    MARKETING = "marketing"
    RESEARCH = "research"
    DEV = "dev"
    PRODUCT = "product"
    RECRUITING = "recruiting"
    SALES = "sales"
    FINANCE = "finance"
    ADMIN = "admin"
    HR = "HR"
    OTHER = "other"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    WAITING = "waiting"
    DONE = "done"
    ARCHIVED = "archived"


class ProjectPriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    NONE = "none"


class RiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FollowUpMode(StrEnum):
    AUTO = "auto"
    DRAFT = "draft"
    NONE = "none"


class TodoStatus(StrEnum):
    OPEN = "open"
    WAITING_OWNER = "waiting_owner"
    DONE = "done"
    CANCELLED = "cancelled"


class DingTalkTodoLinkStatus(StrEnum):
    CREATING = "creating"
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class FollowUpDraftStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkSummaryStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class TodoEvidenceCandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    ENQUEUED = "enqueued"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


class WorkItemSource(BaseModel):
    type: WorkItemSourceType
    ref: str = ""
    title: str = ""
    conversation_id: str = ""
    conversation_title: str = ""
    created_at: str = ""


class WorkItemContext(BaseModel):
    sender: str = ""
    sender_user_id: str = ""
    owner_identity: dict[str, str] = Field(default_factory=dict)
    assignment_authorized: bool = False
    external_task_id: str = ""
    reply_to_source_ref: str = ""
    participants: list[str] = Field(default_factory=list)
    source_conversation_kind: WorkItemSourceKind
    source_conversation_title: str = ""


class WorkItemTaskSignals(BaseModel):
    possible_task_update: bool = False
    mentions_follow_up: bool = False
    progress_claim: bool = False
    owner_correction: bool = False
    complaint_about_followup: bool = False
    signal_reason: str = ""


class WorkItem(BaseModel):
    source: WorkItemSource
    summary: str
    context: WorkItemContext
    task_signals: WorkItemTaskSignals = Field(default_factory=WorkItemTaskSignals)
    scheduled_consumer: dict[str, object] = Field(default_factory=dict)


class ProjectMemoryContextItem(StrictTaskModel):
    source: str = "memory_recall"
    uuid: str = ""
    text: str = ""
    summary: str = ""
    created_at: str = ""


class ProjectMemoryContext(StrictTaskModel):
    query: str = ""
    summary: str = ""
    memories: list[ProjectMemoryContextItem] = Field(default_factory=list)


class TaskDateEvidence(StrictTaskModel):
    kind: Literal[
        "assigned_at", "requested_deadline_at", "external_deadline_at",
        "committed_deadline_at", "estimated_deadline_at", "next_check_at",
    ]
    value: str = ""
    source_ref: str
    source_excerpt: str
    actor_user_id: str = ""
    actor_name: str = ""

    @model_validator(mode="after")
    def source_is_explicit(self) -> "TaskDateEvidence":
        if not self.source_ref.strip() or not self.source_excerpt.strip():
            raise ValueError("date evidence requires exact source provenance")
        if self.kind == "committed_deadline_at" and not (
            self.actor_user_id.strip() or self.actor_name.strip()
        ):
            raise ValueError("committed deadline requires a named source actor")
        if self.kind == "committed_deadline_at" and not self.value.strip():
            raise ValueError("committed deadline requires a concrete ISO date or datetime")
        return self


class TaskIdentityEvidence(StrictTaskModel):
    """Untrusted match proposal; the service verifies both signals and derives identity."""

    basis: Literal[
        "same_external_task_id", "explicit_source_reference",
        "same_deliverable_owner_context_time",
    ]
    source_signal_id: int = Field(strict=True, gt=0)
    target_signal_id: int = Field(strict=True, gt=0)


class TaskIdentityProposal(StrictTaskModel):
    source_task_id: int = Field(gt=0)
    target_task_id: int = Field(gt=0)
    identity_evidence: TaskIdentityEvidence
    reason: str = ""

    @model_validator(mode="after")
    def distinct_tasks(self) -> "TaskIdentityProposal":
        if self.source_task_id == self.target_task_id:
            raise ValueError("identity proposal requires distinct Tasks")
        return self


class TaskRelationProposal(StrictTaskModel):
    from_task_id: int = Field(gt=0)
    to_task_id: int = Field(gt=0)
    relation_type: Literal["depends_on", "blocks", "supports", "supersedes", "related_to"]
    reason: str = ""


class TaskClusterProposal(StrictTaskModel):
    cluster_id: int | None = Field(default=None, gt=0)
    title: str = ""
    task_ids: list[int] = Field(default_factory=list)
    reason: str = ""


class TaskAnchorMatchProposal(StrictTaskModel):
    anchor_id: int = Field(gt=0)
    reason: str


class ProjectCandidateProposal(StrictTaskModel):
    cluster_id: int = Field(gt=0)
    title: str
    reason: str


class TaskAttentionProposal(StrictTaskModel):
    category: Literal["fyi", "watch", "decision", "push"]
    title: str
    why_attention: str
    current_state: str
    ceo_action: str
    anchor_id: int = Field(gt=0)
    material_trigger: Literal[
        "threatened_commitment", "material_change", "material_dispute",
        "ceo_decision", "ceo_push", "required_gate", "risk_escalation",
    ]
    trigger_evidence: str


class TaskDecision(StrictTaskModel):
    action: Literal["skip", "record_candidate", "create_task", "update_task"]
    transition: Literal[
        "none", "promote_candidate", "apply_acceptance", "update_fields", "merge_identity"
    ]
    skip_reason: str = ""
    task_id: int | None = Field(default=None, gt=0)
    target_task_id: int | None = Field(default=None, gt=0)
    source_excerpt: str = ""
    source_ref: str = ""
    title: str = ""
    description: str = ""
    formal_basis: FormalTaskBasis | None = None
    acceptance_polarity: Literal["accepted", "declined", "ambiguous"] | None = None
    acceptance_target_signal_id: int | None = Field(default=None, gt=0)
    status: Literal["open", "waiting", "done", "cancelled"] | None = None
    business_relevance: Literal["unknown", "not_relevant", "relevant"] | None = None
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence: dict[str, Any] = Field(default_factory=dict)
    date_evidence: list[TaskDateEvidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    identity_proposal: TaskIdentityProposal | None = None
    relation_proposals: list[TaskRelationProposal] = Field(default_factory=list)
    cluster_proposal: TaskClusterProposal | None = None
    anchor_match_proposals: list[TaskAnchorMatchProposal] = Field(default_factory=list)
    project_candidate_proposal: ProjectCandidateProposal | None = None
    attention_proposal: TaskAttentionProposal | None = None
    update_summary: str = ""
    memory_recall_used: bool = False
    risk: DecisionRisk = Field(
        default=DecisionRisk.LOW,
        description="Acting on an incorrect task decision is low, medium, or high risk.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the decision, from 0 to 1.",
    )
    rule_coverage: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="How completely the applicable Skill rules cover this case.",
    )
    information_completeness: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="How complete the evidence needed for this decision is.",
    )

    @model_validator(mode="after")
    def validate_transition_shape(self) -> "TaskDecision":
        if self.action != "skip" and (not self.source_excerpt.strip() or not self.source_ref.strip()):
            raise ValueError("task decisions require an exact source excerpt and reference")
        if self.action == "create_task" and self.formal_basis is None:
            raise ValueError("formal Task creation requires formal_basis")
        if self.action == "record_candidate" and self.formal_basis is not None:
            raise ValueError("candidate cannot carry formal_basis")
        if (self.owner_user_id.strip() or self.owner_name.strip()) and not self.owner_evidence:
            raise ValueError("source-backed owner assignment requires owner_evidence")
        if self.action in {"skip", "record_candidate", "create_task"} and self.transition != "none":
            raise ValueError("new/skip decisions cannot transition an existing Task")
        if self.action == "update_task" and (self.task_id is None or self.transition == "none"):
            raise ValueError("update_task requires task_id and a dedicated transition")
        if self.transition != "update_fields" and (self.status is not None or self.business_relevance is not None):
            raise ValueError("status and business relevance require update_fields transition")
        if self.transition == "merge_identity":
            if (
                self.identity_proposal is None
                or self.identity_proposal.source_task_id != self.task_id
                or self.identity_proposal.target_task_id != self.target_task_id
            ):
                raise ValueError("merge_identity requires matching structured identity proposal")
        if self.transition != "merge_identity" and self.identity_proposal is not None:
            raise ValueError("identity proposal requires merge_identity transition")
        if self.transition == "apply_acceptance" and self.acceptance_polarity != "accepted":
            raise ValueError("apply_acceptance requires explicit accepted polarity")
        if self.transition == "apply_acceptance" and self.acceptance_target_signal_id is None:
            raise ValueError("apply_acceptance requires an explicitly cited assignment signal")
        if self.acceptance_polarity is not None and self.transition != "apply_acceptance":
            raise ValueError("acceptance polarity requires apply_acceptance transition")
        if self.acceptance_target_signal_id is not None and self.transition != "apply_acceptance":
            raise ValueError("acceptance target signal requires apply_acceptance transition")
        return self

    def decision_quality(self) -> DecisionQualityResult:
        """Classify this task decision with the shared result-quality rules."""
        return classify_decision_quality(
            risk=self.risk,
            confidence=self.confidence,
            rule_coverage=self.rule_coverage,
            information_completeness=self.information_completeness,
        )


class CompletionSearchTrace(StrictTaskModel):
    source_kind: str
    result: str
    source_ref: str
    reason: str
    source_created_at: str | None = None
    retrieved_at: str = ""
    audit_call_ids: list[str] = Field(default_factory=list, max_length=8)


class CompletionTodoChange(StrictTaskModel):
    action: Literal["close"]
    todo_id: int | None = Field(default=None, gt=0)
    business_task_id: int | None = Field(default=None, gt=0)
    completion_evidence: dict[str, Any]

    @model_validator(mode="after")
    def one_target(self) -> "CompletionTodoChange":
        if (self.todo_id is None) == (self.business_task_id is None):
            raise ValueError("completion requires exactly one Task or legacy TODO ID")
        return self


class CompletionFollowUpChange(StrictTaskModel):
    follow_up_id: int = Field(gt=0)
    todo_id: int | None = Field(default=None, gt=0)
    action: Literal["suppress", "close", "reschedule", "reassign", "keep_open"]
    reason: str = ""
    evidence_check: dict[str, Any] = Field(default_factory=dict)
    next_due_at: str | None = None
    owner_user_id: str | None = None
    owner_name: str | None = None
    owner_evidence: dict[str, Any] = Field(default_factory=dict)


class TaskAgentDecision(StrictTaskModel):
    task_decisions: list[TaskDecision] = Field(default_factory=list)
    todo_changes: list[CompletionTodoChange] = Field(default_factory=list)
    follow_up_changes: list[CompletionFollowUpChange] = Field(default_factory=list)
    search_trace: list[CompletionSearchTrace] = Field(default_factory=list, max_length=3)
    update_summary: str = ""
    memory_recall_used: bool = False

    @model_validator(mode="after")
    def at_most_one_todo_close(self) -> "TaskAgentDecision":
        if len(self.todo_changes) > 1:
            raise ValueError("a Task Agent decision may close at most one TODO")
        return self


class WorkProject(BaseModel):
    id: int
    title: str
    category: ProjectCategory
    tags_json: str = "[]"
    status: ProjectStatus
    priority: ProjectPriority
    risk_level: RiskLevel
    needs_derek_attention: bool = False
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence_json: str = "{}"
    related_people_json: str = "[]"
    goal: str = ""
    background: str = ""
    facts_json: str = "[]"
    current_state: str = ""
    blocker: str = ""
    next_step: str = ""
    next_follow_up_at: str = ""
    follow_up_mode: FollowUpMode = FollowUpMode.NONE
    source_conversations_json: str = "[]"
    memory_context_json: str = "{}"
    created_at: str
    updated_at: str
    last_activity_at: str = ""


class WorkTodo(BaseModel):
    id: int
    project_id: int
    title: str
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence_json: str = "{}"
    status: TodoStatus
    priority: ProjectPriority
    deadline_at: str = ""
    next_follow_up_at: str = ""
    follow_up_question: str = ""
    blocker: str = ""
    completion_evidence_json: str = "{}"
    created_from_update_id: int = 0
    created_at: str
    updated_at: str
    completed_at: str = ""


class WorkTodoDingTalkLink(BaseModel):
    id: int
    work_todo_id: int
    dingtalk_task_id: str = ""
    executor_user_id: str = ""
    executor_name: str = ""
    title_snapshot: str = ""
    deadline_at_snapshot: str = ""
    priority_snapshot: str = ""
    status: DingTalkTodoLinkStatus
    last_dingtalk_done: bool | None = None
    last_dingtalk_payload_json: str = "{}"
    last_pull_at: str = ""
    last_push_at: str = ""
    last_error: str = ""
    retry_count: int = 0
    created_at: str
    updated_at: str


class WorkUpdate(BaseModel):
    id: int
    project_id: int
    source_type: str
    source_ref: str
    summary: str
    changes_json: str = "{}"
    merge_reason: str = ""
    confidence: float = 0.0
    created_at: str


class TodoEvidenceCandidate(BaseModel):
    id: int
    project_id: int
    todo_id: int
    source_type: str
    source_ref: str
    source_created_at: str = ""
    evidence_text: str = ""
    reason: str = ""
    confidence: float = 0.0
    status: TodoEvidenceCandidateStatus
    work_summary_input_id: int = 0
    decision_json: str = "{}"
    dedupe_key: str = ""
    created_at: str
    updated_at: str


class WorkSummaryInput(BaseModel):
    id: int
    source_type: WorkItemSourceType
    source_ref: str
    payload_json: str
    status: WorkSummaryStatus
    attempts: int = 0
    error: str = ""
    available_at: str = ""
    created_at: str
    updated_at: str


class TaskAgentRun(BaseModel):
    id: int
    summary_input_id: int
    codex_session_id: str = ""
    decision_json: str = "{}"
    audit_summary: str = ""
    memory_recall_used: bool = False
    status: str = "completed"
    error: str = ""
    created_at: str
    finished_at: str = ""
    updated_at: str = ""


class FollowUpDraft(BaseModel):
    id: int
    project_id: int
    todo_id: int = 0
    title: str = ""
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    owners_json: str = "[]"
    target_conversation_id: str = ""
    target_kind: str = ""
    question_text: str = ""
    priority: str = ""
    tags_json: str = "[]"
    participants_json: str = "[]"
    files_json: str = "[]"
    risk_check_json: str = "{}"
    status: FollowUpDraftStatus
    send_result_json: str = "{}"
    evidence_check_json: str = "{}"
    reaction_status: str = ""
    reaction_summary: str = ""
    suppressed_reason: str = ""
    dedupe_key: str = ""
    scheduled_at: str = ""
    sent_at: str = ""
    revision: int = 1
    send_claim_revision: int = 0
    send_claim_token: str = ""
    send_claim_idempotency_uuid: str = ""
    created_at: str
    updated_at: str = ""
