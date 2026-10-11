from enum import StrEnum
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.fields import FieldInfo

from app.decision_quality import (
    DecisionQualityResult,
    DecisionRisk,
    classify_decision_quality,
)
from app.task_semantic_models import (
    FormalTaskBasis,
    ProjectContext,
    SourceCitation,
    TaskSuggestion,
)


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
            option.get("type") == "null" for option in property_schema.get("anyOf", [])
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
    MANAGEMENT_WEEKLY_REPORT = "management_weekly_report"
    PROJECT_WEEKLY_REPORT = "project_weekly_report"
    DEPARTMENT_WEEKLY_REPORT = "department_weekly_report"
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
        "assigned_at",
        "requested_deadline_at",
        "external_deadline_at",
        "committed_deadline_at",
        "estimated_deadline_at",
        "next_check_at",
    ]
    value: str = Field(
        default="",
        description="Normalized value must equal the complete parseable date phrase quoted in source_excerpt; otherwise preserve source wording without typed date_evidence. Do not move Project registry deadlines onto Tasks.",
    )
    source_ref: str
    source_excerpt: str = Field(
        description="Quote only the complete parseable date phrase, not a whole registry row or surrounding action prose."
    )
    actor_user_id: str = Field(
        default="",
        description="For source-derived dates use trusted WorkItem.context.sender_user_id; next_check_at uses task-agent. Without trusted sender identity omit typed date_evidence.",
    )
    actor_name: str = Field(
        default="",
        description="Use trusted WorkItem.context.sender; next_check_at uses CEO Agent. Report/document names are not date actors.",
    )

    @model_validator(mode="after")
    def source_is_explicit(self) -> "TaskDateEvidence":
        if not self.source_ref.strip() or not self.source_excerpt.strip():
            raise ValueError("date evidence requires exact source provenance")
        if self.kind == "committed_deadline_at" and not (
            self.actor_user_id.strip() or self.actor_name.strip()
        ):
            raise ValueError("committed deadline requires a named source actor")
        if self.kind == "committed_deadline_at" and not self.value.strip():
            raise ValueError(
                "committed deadline requires a concrete ISO date or datetime"
            )
        return self


class TaskIdentityEvidence(StrictTaskModel):
    """Untrusted match proposal; the service verifies both signals and derives identity."""

    basis: Literal[
        "same_external_task_id",
        "explicit_source_reference",
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
    related_task_id: int = Field(
        gt=0,
        strict=True,
        description="Real existing related Task ID; the current Task is this decision's applied result, never a guessed new ID.",
    )
    direction: Literal["current_to_related", "related_to_current"]
    relation_type: Literal[
        "depends_on", "blocks", "supports", "supersedes", "related_to"
    ]
    reason: str = ""

    def endpoints(self, current_task_id: int) -> tuple[int, int]:
        return (
            (current_task_id, self.related_task_id)
            if self.direction == "current_to_related"
            else (self.related_task_id, current_task_id)
        )


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


class ProjectProposal(StrictTaskModel):
    """Adopt the current authoritative Project definition and register or reuse its identity."""

    title: str
    reason: str
    source_excerpt: str = Field(
        description="Exact current authoritative Project definition to register or reuse, distinct from the Task's action evidence; its source-named title takes precedence over a different stored similar or shorter name.",
    )
    authority: Literal[
        "management_weekly_report",
        "project_weekly_report",
        "department_weekly_report",
        "meeting_decision",
    ]

    @field_validator("source_excerpt")
    @classmethod
    def nonblank_excerpt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("project proposal requires a nonblank source excerpt")
        return value

    @model_validator(mode="after")
    def nonblank(self) -> "ProjectProposal":
        if not self.title.strip() or not self.reason.strip():
            raise ValueError("project proposal requires a title and reason")
        return self


class ProjectSelector(StrictTaskModel):
    anchor_id: int | None = Field(default=None, strict=True, gt=0)
    project_decision_index: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        description="Zero-based position in this result's project_decisions, never task_decisions or a persisted ID.",
    )

    @model_validator(mode="after")
    def one_project(self) -> "ProjectSelector":
        if (self.anchor_id is None) == (self.project_decision_index is None):
            raise ValueError(
                "ProjectSelector requires exactly one anchor_id or project_decision_index"
            )
        return self


class ProjectDecision(StrictTaskModel):
    anchor_id: int | None = Field(default=None, strict=True, gt=0)
    registration: ProjectProposal | None = None
    context: ProjectContext | None = Field(
        default=None,
        description="Complete current Project snapshot with original proof for each role/fact. Keep unchanged historical citations; null only adds evidence and does not replace context.",
    )
    crm_customer_label: str = Field(
        default="",
        description="Customer name/alias stated in source evidence, never extracted only by guessing from a Project title.",
    )
    crm_customer_evidence: SourceCitation | None = Field(
        default=None,
        description="Exact source citation that explicitly identifies crm_customer_label. CRM identity is resolved separately by read-only exact lookup.",
    )
    evidence: list[SourceCitation] = Field(min_length=1)
    reason: str

    @model_validator(mode="after")
    def one_project(self) -> "ProjectDecision":
        if (self.anchor_id is None) == (self.registration is None):
            raise ValueError(
                "ProjectDecision requires exactly one anchor_id or registration"
            )
        if not self.reason.strip():
            raise ValueError("project decision requires a nonblank reason")
        if bool(self.crm_customer_label.strip()) != (self.crm_customer_evidence is not None):
            raise ValueError("CRM customer label and source evidence must be provided together")
        if self.crm_customer_evidence is not None:
            if self.crm_customer_evidence not in self.evidence:
                raise ValueError("CRM customer evidence must also support the Project decision")
            if self.crm_customer_label.strip() not in self.crm_customer_evidence.source_excerpt:
                raise ValueError("CRM customer label must appear verbatim in its source evidence")
        return self


class TaskAttentionEvidence(StrictTaskModel):
    signal_id: int | None = Field(default=None, strict=True, gt=0)
    source_ref: str
    source_excerpt: str = Field(
        description="A contiguous verbatim source quote, preserving punctuation, spaces, and line breaks. Do not join separate spans or paraphrase; use separate evidence entries for separate spans."
    )

    @field_validator("source_ref", "source_excerpt")
    @classmethod
    def nonblank_provenance(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "attention evidence requires a nonblank source reference and excerpt"
            )
        return value


class TaskAttentionProposal(StrictTaskModel):
    assessment_basis: Literal["current_observation", "historical_comparison"] = Field(
        description="current_observation asserts only current-source facts, not confirmation of history merely retold there. historical_comparison relies on comparison, continuity, escalation or conflict with stored history and requires current null-ID evidence plus positive persisted-ID original evidence. When the current source explicitly compares earlier facts and matching original Signals are delivered, verify that comparison against the originals and use historical_comparison. If the originals are unavailable, mark the comparison uncertain and assert only current facts; never invent historical evidence.",
    )
    category: Literal["fyi", "watch", "decision", "push"]
    title: str
    why_attention: str
    current_state: str = Field(
        description="Project-level risk facts, not per-Task action summaries. This proposal belongs to one Project assessment, not each supporting Task."
    )
    ceo_action: str
    material_trigger: Literal[
        "threatened_commitment",
        "material_change",
        "material_dispute",
        "ceo_decision",
        "ceo_push",
        "required_gate",
        "risk_escalation",
    ]
    evidence: list[TaskAttentionEvidence] = Field(
        min_length=1,
        description="Exact original citations covering the actual assessment claims; historical comparison cites relevant original persisted Signals alongside current evidence, not all retrieved sources.",
    )

    @model_validator(mode="after")
    def validate_assessment_basis(self) -> "TaskAttentionProposal":
        current = any(item.signal_id is None for item in self.evidence)
        historical = any(item.signal_id is not None for item in self.evidence)
        if self.assessment_basis == "historical_comparison" and not (
            current and historical
        ):
            raise ValueError(
                "historical_comparison requires current null-ID and positive persisted-ID evidence"
            )
        if self.assessment_basis == "current_observation" and not current:
            raise ValueError("current_observation requires current null-ID evidence")
        return self


class TaskDecision(StrictTaskModel):
    action: Literal["skip", "record_candidate", "create_task", "update_task"] = Field(
        description="Create/record only independently completable deliverables. Scope/content additions to an existing deliverable update that Task by its real ID, not a second Task; identical quotes alone do not establish identity."
    )
    transition: Literal[
        "none",
        "promote_candidate",
        "apply_acceptance",
        "update_fields",
        "merge_identity",
    ]
    skip_reason: str = ""
    task_id: int | None = Field(default=None, gt=0)
    target_task_id: int | None = Field(default=None, gt=0)
    source_excerpt: str = Field(
        default="",
        description="Exact contiguous verbatim quote supporting this actual Task action/update or a display-only suggestion's factual basis, not Project registration scope already covered by concrete actions. Project registration belongs in project_decisions. Preserve punctuation, spaces, and line breaks; do not quote an inferred action as human instructions.",
    )
    source_ref: str = ""
    source_link: str = Field(
        default="",
        description="A link to the source (a document, minutes page, message or thread URL). Required whenever the source has one.",
    )
    source_description: str = Field(
        default="",
        description="Where a reader can find the source when there is no link, in words: e.g. a DingTalk message is its group and the person who sent it.",
    )
    source_group: str = Field(
        default="", description="The group or conversation the source was said in."
    )
    source_person: str = Field(default="", description="Who said it.")
    evidence_origin: Literal["current", "session", "memory"] = Field(
        default="current",
        description=(
            "Where source_excerpt comes from. current: the Work Item being processed. "
            "session: something read earlier in this Agent session. memory: provenance found through memory_recall. "
            "For session and memory, source_ref is the ORIGINAL source's reference and source_excerpt an exact quote of its text."
        ),
    )
    title: str = Field(
        default="",
        description="Name the independently completable deliverable; an addition to an existing Task's scope is an update, not a new deliverable. Required nonblank for create_task/record_candidate. Existing-ID updates may omit it or use empty string; a nonempty provided update title must not be whitespace-only. Only update_fields changes a provided title. Promotion, acceptance and merge preserve the stored title.",
    )
    description: str = Field(
        default="",
        description="Describe this deliverable or the existing Task's actual scope/content update; do not split additions into duplicate Tasks.",
    )
    formal_basis: FormalTaskBasis | None = None
    acceptance_polarity: Literal["accepted", "declined", "ambiguous"] | None = None
    acceptance_target_signal_id: int | None = Field(default=None, gt=0)
    status: Literal["open", "waiting", "done", "cancelled"] | None = None
    business_relevance: Literal["unknown", "not_relevant", "relevant"] | None = None
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={
            "properties": {
                "source_ref": {"type": "string"},
                "excerpt": {"type": "string"},
            },
            "required": ["source_ref", "excerpt"],
            "additionalProperties": False,
        },
    )
    owner_kind: Literal["individual", "team", "unknown"] | None = Field(
        default=None,
        description=(
            "Whether the source identifies one individual owner, a team, or no resolvable owner. "
            "A team is not sufficient for formal Task creation."
        ),
    )
    owner_relation: (
        Literal[
            "explicit_assignment",
            "self_commitment",
            "meeting_summary_action_item",
            "speaker_only",
            "unknown",
        ]
        | None
    ) = Field(
        default=None,
        description=(
            "How the source connects the named owner to the deliverable. "
            "speaker_only means the person merely spoke and is not an assignment."
        ),
    )
    date_evidence: list[TaskDateEvidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    identity_proposal: TaskIdentityProposal | None = None
    relation_proposals: list[TaskRelationProposal] = Field(default_factory=list)
    cluster_proposal: TaskClusterProposal | None = None
    anchor_match_proposals: list[TaskAnchorMatchProposal] = Field(default_factory=list)
    project_candidate_proposal: ProjectCandidateProposal | None = None
    project: ProjectSelector | None = None
    project_link_evidence: list[SourceCitation] = Field(
        default_factory=list,
        description="Original evidence linking this Task to the selected Project. Reuse an unchanged confirmed existing link without manufacturing a Task update.",
    )
    suggestion: TaskSuggestion | None = Field(
        default=None,
        description="Display-only action inferred from Project facts/roles, not a human assignment or acceptance. For later-source updates carry the existing Task ID; origin remains a suggestion after real human promotion.",
    )
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
        if (
            self.action in {"record_candidate", "create_task"}
            and not self.title.strip()
        ):
            raise ValueError("new task decision requires title")
        if self.action == "update_task" and self.title and not self.title.strip():
            raise ValueError("provided update title must be nonblank")
        if self.action != "skip" and (
            not self.source_excerpt.strip() or not self.source_ref.strip()
        ):
            raise ValueError(
                "task decisions require a source excerpt (a sentence taken from the source) and reference"
            )
        if self.action == "update_task" and any(
            relation.related_task_id == self.task_id
            for relation in self.relation_proposals
        ):
            raise ValueError(
                "relation requires a different related Task than this decision's task_id"
            )
        if self.evidence_origin != "current" and not (
            self.action == "record_candidate"
            or (self.action == "update_task" and self.transition == "update_fields")
        ):
            raise ValueError(
                "earlier session or memory evidence may refine a Task (update_fields) or record a candidate; "
                "formal creation, promotion, acceptance and merges need the current Work Item's authority"
            )
        if (
            self.action != "skip"
            and self.evidence_origin != "current"
            and not (
                self.source_link.strip()
                or self.source_description.strip()
                or (self.source_group.strip() and self.source_person.strip())
            )
        ):
            raise ValueError(
                "earlier or remembered evidence needs its source link, or, when there is none, a description of where it is (e.g. group and person)"
            )
        if self.evidence_origin != "current" and self.date_evidence:
            raise ValueError("date evidence must come from the current Work Item")
        if self.action == "create_task" and self.formal_basis is None:
            raise ValueError("formal Task creation requires formal_basis")
        if self.action == "record_candidate" and self.formal_basis is not None:
            raise ValueError("candidate cannot carry formal_basis")
        if self.project_link_evidence and self.project is None:
            raise ValueError("project_link_evidence requires a Project selector")
        if self.project is not None and self.project_candidate_proposal is not None:
            raise ValueError(
                "a decision cannot select a formal Project and a Project candidate"
            )
        if self.suggestion is not None:
            if self.project is None:
                raise ValueError(
                    "displayed suggestion requires a registered Project selector"
                )
            if not (
                self.action == "record_candidate"
                and self.transition == "none"
                or self.action == "update_task"
                and self.transition == "update_fields"
            ):
                raise ValueError(
                    "suggestion must record or update a candidate, not assign or accept it"
                )
            if (
                self.formal_basis is not None
                or self.owner_name.strip()
                or self.owner_user_id.strip()
                or self.owner_evidence
                or self.owner_kind is not None
                or self.owner_relation is not None
                or self.date_evidence
                or self.status is not None
                or self.business_relevance is not None
            ):
                raise ValueError(
                    "suggestion cannot carry actual owner, formal basis, dates or lifecycle changes"
                )
        if (
            self.owner_user_id.strip() or self.owner_name.strip()
        ) and not self.owner_evidence:
            raise ValueError("source-backed owner assignment requires owner_evidence")
        if (
            self.action in {"skip", "record_candidate", "create_task"}
            and self.transition != "none"
        ):
            raise ValueError("new/skip decisions cannot transition an existing Task")
        if self.action == "update_task" and (
            self.task_id is None or self.transition == "none"
        ):
            raise ValueError("update_task requires task_id and a dedicated transition")
        if self.transition != "update_fields" and (
            self.status is not None or self.business_relevance is not None
        ):
            raise ValueError(
                "status and business relevance require update_fields transition"
            )
        if self.transition == "merge_identity":
            if (
                self.identity_proposal is None
                or self.identity_proposal.source_task_id != self.task_id
                or self.identity_proposal.target_task_id != self.target_task_id
            ):
                raise ValueError(
                    "merge_identity requires matching structured identity proposal"
                )
        if self.transition != "merge_identity" and self.identity_proposal is not None:
            raise ValueError("identity proposal requires merge_identity transition")
        if (
            self.transition == "apply_acceptance"
            and self.acceptance_polarity != "accepted"
        ):
            raise ValueError("apply_acceptance requires explicit accepted polarity")
        if (
            self.transition == "apply_acceptance"
            and self.acceptance_target_signal_id is None
        ):
            raise ValueError(
                "apply_acceptance requires an explicitly cited assignment signal"
            )
        if (
            self.acceptance_polarity is not None
            and self.transition != "apply_acceptance"
        ):
            raise ValueError("acceptance polarity requires apply_acceptance transition")
        if (
            self.acceptance_target_signal_id is not None
            and self.transition != "apply_acceptance"
        ):
            raise ValueError(
                "acceptance target signal requires apply_acceptance transition"
            )
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
    completion_evidence: dict[str, Any] = Field(
        json_schema_extra={
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    )

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
    evidence_check: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    next_due_at: str | None = None
    owner_user_id: str | None = None
    owner_name: str | None = None
    owner_evidence: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )


class TaskProjectAssessment(StrictTaskModel):
    project_title: str = Field(
        description="Registered Project title when its identity is known; when identity is unresolved, preserve the source's Project clue without treating it as a registered Project.",
    )
    outcome: Literal["needs_attention", "not_needed", "insufficient_evidence"] = Field(
        description="Exactly one judgment for this Project, with or without Tasks: needs_attention for a retained card or this assessment's proposal; not_needed for supported normal progress or a negative judgment; insufficient_evidence for genuine unresolved identity or missing facts needed to judge. No Tasks or no reported risk alone is not insufficient_evidence.",
    )
    reason: str = Field(
        description="Concrete reason for the outcome, grounded in the cited facts; do not restate an inference as an original quote.",
    )
    assessment_basis: Literal["current_observation", "historical_comparison"] = Field(
        description="current_observation asserts only current-source facts, not confirmation of history merely retold there. historical_comparison relies on comparison, continuity, escalation or conflict with stored history and requires current null-ID evidence plus positive persisted-ID original evidence. When the current source explicitly compares earlier facts and matching original Signals are delivered, verify that comparison against the originals and use historical_comparison. If the originals are unavailable, mark the comparison uncertain and assert only current facts; never invent historical evidence.",
    )
    evidence: list[TaskAttentionEvidence] = Field(
        min_length=1,
        description="Exact factual original quotes supporting the judgment, separated from the assessment's inference; current evidence has no persisted signal ID.",
    )
    anchor_id: int | None = Field(
        default=None,
        strict=True,
        gt=0,
        description="Positive persisted ID of a known registered Project anchor; never a list position or a guessed ID.",
    )
    project_decision_index: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        description="Zero-based position in this result's project_decisions; never task_decisions or a persisted Project ID.",
    )
    existing_attention_id: int | None = Field(
        default=None,
        strict=True,
        gt=0,
        description="Positive persisted ID of a retained needs_attention card: an original-proof claim, not an update target. For current risk changes use attention_proposal; its Project key reuses the existing card. Leave existing_attention_id null unless you cite and verify that card's stored original evidence. Read the card's current_project_attention entry and cite at least one assessment_json.evidence item with signal_id, source_ref, and source_excerpt unchanged. A current restatement does not replace that stored proof. current_project_attention.task_ids lists actual saved members; Project peers are not automatically card members. Name only saved members as supporting Tasks for a retained card; use attention_proposal with current original evidence for new membership or changed risk. Never a list position.",
    )
    decision_indexes: list[Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=list,
        description="Zero-based positions of supporting Task decisions in this result; a genuine candidate Task may support the assessment without being promoted.",
    )
    task_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(
        default_factory=list,
        description="Positive persisted IDs of supporting existing Tasks; never decision positions or guessed IDs.",
    )
    attention_proposal: TaskAttentionProposal | None = None

    @model_validator(mode="after")
    def validate_project_assessment(self) -> "TaskProjectAssessment":
        if not self.project_title.strip() or not self.reason.strip():
            raise ValueError("project assessment requires a nonblank title and reason")
        if self.anchor_id is not None and self.project_decision_index is not None:
            raise ValueError(
                "project assessment accepts either anchor_id or project_decision_index, not both"
            )
        if (
            self.anchor_id is None
            and self.project_decision_index is None
            and self.outcome != "insufficient_evidence"
        ):
            raise ValueError(
                "unknown Project may only have an insufficient_evidence outcome"
            )
        if self.existing_attention_id is not None and self.outcome != "needs_attention":
            raise ValueError("existing_attention_id requires needs_attention outcome")
        if self.attention_proposal is not None and self.outcome != "needs_attention":
            raise ValueError("attention_proposal requires needs_attention outcome")
        if (
            self.outcome == "needs_attention"
            and self.existing_attention_id is None
            and self.attention_proposal is None
        ):
            raise ValueError(
                "needs_attention requires existing_attention_id or attention_proposal"
            )
        if len(set(self.decision_indexes)) != len(self.decision_indexes):
            raise ValueError("project assessment rejects duplicate decision_indexes")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("project assessment rejects duplicate task_ids")

        has_current = any(item.signal_id is None for item in self.evidence)
        has_persisted = any(item.signal_id is not None for item in self.evidence)
        if self.assessment_basis == "historical_comparison" and not (
            has_current and has_persisted
        ):
            raise ValueError(
                "historical_comparison requires current null-ID and positive persisted-ID evidence"
            )
        if self.assessment_basis == "current_observation" and not has_current:
            raise ValueError("current_observation requires current null-ID evidence")
        return self


class TaskAgentDecision(StrictTaskModel):
    project_decisions: list[ProjectDecision] = Field(
        description="Independent official Project registration/context/evidence updates. A Project may have zero Tasks; do not create a Task merely to carry Project facts."
    )
    task_decisions: list[TaskDecision]
    project_assessments: list[TaskProjectAssessment] = Field(
        description="One explicit outcome, concrete reason, and original evidence set for every relevant business Project or Project clue in the current source and current Tasks' confirmed Project links. This semantic coverage is not limited to structured selectors emitted in this output; envelope validation can only prove coverage of emitted selectors. Exact duplicate current titles and repeated known anchors share one judgment. Use [] only when no relevant Project or clue exists and explain that in update_summary."
    )
    todo_changes: list[CompletionTodoChange] = Field(default_factory=list)
    follow_up_changes: list[CompletionFollowUpChange] = Field(default_factory=list)
    search_trace: list[CompletionSearchTrace] = Field(
        default_factory=list, max_length=3
    )
    update_summary: str = ""
    memory_recall_used: bool = False

    @model_validator(mode="after")
    def validate_decision_envelope(self) -> "TaskAgentDecision":
        if len(self.todo_changes) > 1:
            raise ValueError("a Task Agent decision may close at most one TODO")

        def project_key(
            anchor_id: int | None, index: int | None
        ) -> tuple[str, int | str]:
            if anchor_id is not None:
                return ("anchor", anchor_id)
            if index is None or index >= len(self.project_decisions):
                raise ValueError("project_decision_index is out of bounds")
            project = self.project_decisions[index]
            if project.anchor_id is not None:
                return ("anchor", project.anchor_id)
            assert project.registration is not None
            return ("registration", project.registration.title)

        required_projects = {
            project_key(project.anchor_id, index if project.anchor_id is None else None)
            for index, project in enumerate(self.project_decisions)
        }
        for task in self.task_decisions:
            if task.project is not None:
                required_projects.add(
                    project_key(
                        task.project.anchor_id, task.project.project_decision_index
                    )
                )

        covered: set[tuple[str, int | str]] = set()
        covered_titles: set[str] = set()
        for assessment in self.project_assessments:
            if assessment.project_title in covered_titles:
                raise ValueError(
                    "each exact current Project title requires exactly one assessment"
                )
            covered_titles.add(assessment.project_title)
            key = (
                ("clue", assessment.project_title)
                if assessment.anchor_id is None
                and assessment.project_decision_index is None
                else project_key(
                    assessment.anchor_id, assessment.project_decision_index
                )
            )
            if key in covered:
                raise ValueError(
                    "each structured Project or exact Project clue requires exactly one assessment"
                )
            covered.add(key)
            if key[0] == "registration" and key[1] != assessment.project_title:
                raise ValueError(
                    "project assessment project_title must exactly match its registration title"
                )
            for index in assessment.decision_indexes:
                if index >= len(self.task_decisions):
                    raise ValueError(
                        "project assessment decision index is out of bounds"
                    )
                task = self.task_decisions[index]
                if task.action == "skip":
                    raise ValueError(
                        "project assessment cannot reference a skip decision"
                    )
                if (
                    key[0] != "clue"
                    and task.project is None
                    and task.action in {"record_candidate", "create_task"}
                ):
                    raise ValueError("new supporting Task requires a Project selector")
                if (
                    task.project is not None
                    and project_key(
                        task.project.anchor_id, task.project.project_decision_index
                    )
                    != key
                ):
                    raise ValueError(
                        "supporting Task selector must match the assessment Project"
                    )
            if assessment.attention_proposal is not None:
                if (
                    assessment.attention_proposal.assessment_basis
                    != assessment.assessment_basis
                ):
                    raise ValueError(
                        "attention proposal and assessment must use the same assessment_basis"
                    )

        if required_projects - covered:
            raise ValueError(
                "every relevant structured Project requires exactly one project assessment"
            )
        if not self.project_assessments and not self.update_summary.strip():
            raise ValueError(
                "empty project_assessments requires a nonblank update_summary explaining that no relevant Project was found"
            )
        return self


def task_agent_output_schema() -> dict[str, Any]:
    """Return the Task Agent's complete contract in Codex strict-schema form."""
    schema = TaskAgentDecision.model_json_schema()

    def normalize(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                value.clear()
                value["$ref"] = reference
                return
            properties = value.get("properties")
            if value.get("type") == "object" and isinstance(properties, dict):
                value["additionalProperties"] = False
                value["required"] = list(properties)
                for property_schema in properties.values():
                    if isinstance(property_schema, dict):
                        property_schema.pop("default", None)
            for nested in value.values():
                normalize(nested)
        elif isinstance(value, list):
            for nested in value:
                normalize(nested)

    normalize(schema)
    return schema


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
    source_created_at: str = ""
    body_sha256: str = ""
    body_bytes: int = 0
    body_compacted: int = 0
    status: WorkSummaryStatus
    attempts: int = 0
    error: str = ""
    available_at: str = ""
    created_at: str
    updated_at: str


class TaskAttentionProjectionOutcome(BaseModel):
    task_id: int | None
    assessment_index: int | None = None
    anchor_id: int | None = None
    attention_id: int | None = None
    status: Literal["applied", "rejected", "error"]
    reason: str = ""


class TaskAttentionVerifiedCitation(BaseModel):
    source_ref: str
    source_excerpt: str
    signal_id: int | None = None
    source_time: str = ""
    source_link: str = ""


class TaskAttentionAssessmentResult(BaseModel):
    assessment_index: int
    anchor_id: int | None = None
    task_ids: list[int] = Field(default_factory=list)
    attention_id: int | None = None
    status: Literal["recorded", "applied", "existing", "rejected", "error"]
    reason: str = ""
    evidence: list[TaskAttentionVerifiedCitation] = Field(default_factory=list)


class TaskProjectDecisionResult(BaseModel):
    project_decision_index: int
    project_id: int
    anchor_id: int
    revision_id: int | None = None
    signal_ids: list[int] = Field(default_factory=list)


class TaskDecisionResult(BaseModel):
    decision_index: int
    task_id: int
    signal_id: int
    anchor_id: int | None = None


class TaskAttentionProjectionReceipt(BaseModel):
    status: Literal["pending", "no_proposal", "completed", "partial", "failed"]
    source_type: str
    task_decision_count: int
    project_link_count: int
    registry_row_count: int | None = None
    proposal_count: int
    applied_count: int = 0
    outcomes: list[TaskAttentionProjectionOutcome] = Field(default_factory=list)
    project_decisions: list[TaskProjectDecisionResult] = Field(default_factory=list)
    task_decisions: list[TaskDecisionResult] = Field(default_factory=list)
    project_assessments: list[TaskAttentionAssessmentResult] = Field(
        default_factory=list
    )
    recompute_error: str = ""


class TaskAgentRun(BaseModel):
    id: int
    summary_input_id: int
    codex_session_id: str = ""
    decision_json: str = "{}"
    projection_json: str = "{}"
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
