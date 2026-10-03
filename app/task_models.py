from enum import StrEnum
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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
        "assigned_at", "requested_deadline_at", "external_deadline_at",
        "committed_deadline_at", "estimated_deadline_at", "next_check_at",
    ]
    value: str = Field(default="", description="Normalized value must equal the complete parseable date phrase quoted in source_excerpt; otherwise preserve source wording without typed date_evidence. Do not move Project registry deadlines onto Tasks.")
    source_ref: str
    source_excerpt: str = Field(description="Quote only the complete parseable date phrase, not a whole registry row or surrounding action prose.")
    actor_user_id: str = Field(default="", description="For source-derived dates use trusted WorkItem.context.sender_user_id; next_check_at uses task-agent. Without trusted sender identity omit typed date_evidence.")
    actor_name: str = Field(default="", description="Use trusted WorkItem.context.sender; next_check_at uses CEO Agent. Report/document names are not date actors.")

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
    related_task_id: int = Field(gt=0, strict=True,
        description="Real existing related Task ID; the current Task is this decision's applied result, never a guessed new ID.")
    direction: Literal["current_to_related", "related_to_current"]
    relation_type: Literal["depends_on", "blocks", "supports", "supersedes", "related_to"]
    reason: str = ""

    def endpoints(self, current_task_id: int) -> tuple[int, int]:
        return ((current_task_id, self.related_task_id) if self.direction == "current_to_related"
                else (self.related_task_id, current_task_id))


class TaskClusterProposal(StrictTaskModel):
    cluster_id: int | None = Field(default=None, gt=0)
    title: str = ""
    task_ids: list[int] = Field(default_factory=list)
    reason: str = ""


class TaskAnchorMatchProposal(StrictTaskModel):
    anchor_id: int = Field(gt=0)
    reason: str


class TaskProjectLinkProposal(StrictTaskModel):
    """Current action explicitly supplements a known Project after resolving source authority."""

    anchor_id: int = Field(gt=0, strict=True)
    source_excerpt: str = Field(description="Exact same-action compound quote containing the stored Project/anchor title and this Task's action excerpt, explicitly supplementing that known Project rather than replacing a different current authoritative Project name; a complete compound sentence is allowed, not another paragraph or the whole report assembled to supply a name.")
    reason: str

    @model_validator(mode="after")
    def nonblank(self) -> "TaskProjectLinkProposal":
        if not self.source_excerpt.strip() or not self.reason.strip():
            raise ValueError("existing Project link requires a nonblank action quote and reason")
        return self


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
        "management_weekly_report", "project_weekly_report",
        "department_weekly_report", "meeting_decision",
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


class TaskAttentionEvidence(StrictTaskModel):
    signal_id: int | None = Field(default=None, strict=True, gt=0)
    source_ref: str
    source_excerpt: str = Field(description="A contiguous verbatim source quote, preserving punctuation, spaces, and line breaks. Do not join separate spans or paraphrase; use separate evidence entries for separate spans.")

    @field_validator("source_ref", "source_excerpt")
    @classmethod
    def nonblank_provenance(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("attention evidence requires a nonblank source reference and excerpt")
        return value


class TaskAttentionProposal(StrictTaskModel):
    assessment_basis: Literal["current_observation", "historical_comparison"] = Field(
        description="current_observation asserts only current-source facts, not confirmation of history merely retold there. historical_comparison relies on comparison, continuity, escalation or conflict with stored history and requires current null-ID evidence plus positive persisted-ID original evidence.",
    )
    category: Literal["fyi", "watch", "decision", "push"]
    title: str
    why_attention: str
    current_state: str = Field(description="Project-level risk facts, not per-Task action summaries. Keep this identical across supporting Tasks for the same Project/risk; put each Task's own action in its description or update_summary.")
    ceo_action: str
    anchor_id: int | None = Field(
        default=None, strict=True, gt=0,
        description="Registered anchor ID; null resolves only this TaskDecision's project_proposal.",
    )
    related_task_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list)
    material_trigger: Literal[
        "threatened_commitment", "material_change", "material_dispute",
        "ceo_decision", "ceo_push", "required_gate", "risk_escalation",
    ]
    evidence: list[TaskAttentionEvidence] = Field(min_length=1,
        description="Exact original citations covering the actual assessment claims; historical comparison cites relevant original persisted Signals alongside current evidence, not all retrieved sources.")

    @model_validator(mode="after")
    def validate_assessment_basis(self) -> "TaskAttentionProposal":
        current = any(item.signal_id is None for item in self.evidence)
        historical = any(item.signal_id is not None for item in self.evidence)
        if self.assessment_basis == "historical_comparison" and not (current and historical):
            raise ValueError("historical_comparison requires current null-ID and positive persisted-ID evidence")
        if self.assessment_basis == "current_observation" and not current:
            raise ValueError("current_observation requires current null-ID evidence")
        return self


class TaskDecision(StrictTaskModel):
    action: Literal["skip", "record_candidate", "create_task", "update_task"] = Field(
        description="Create/record only independently completable deliverables. Scope/content additions to an existing deliverable update that Task by its real ID, not a second Task; identical quotes alone do not establish identity.")
    transition: Literal[
        "none", "promote_candidate", "apply_acceptance", "update_fields", "merge_identity"
    ]
    skip_reason: str = ""
    task_id: int | None = Field(default=None, gt=0)
    target_task_id: int | None = Field(default=None, gt=0)
    source_excerpt: str = Field(default="", description="Exact contiguous verbatim quote of this independent Task action or this existing Task's actual update, not Project registration scope already covered by concrete actions; cite registration separately in project_proposal. Preserve punctuation, spaces, and line breaks.")
    source_ref: str = ""
    source_link: str = Field(default="", description="A link to the source (a document, minutes page, message or thread URL). Required whenever the source has one.")
    source_description: str = Field(default="", description="Where a reader can find the source when there is no link, in words: e.g. a DingTalk message is its group and the person who sent it.")
    source_group: str = Field(default="", description="The group or conversation the source was said in.")
    source_person: str = Field(default="", description="Who said it.")
    evidence_origin: Literal["current", "session", "memory"] = Field(
        default="current",
        description=(
            "Where source_excerpt comes from. current: the Work Item being processed. "
            "session: something read earlier in this Agent session. memory: provenance found through memory_recall. "
            "For session and memory, source_ref is the ORIGINAL source's reference and source_excerpt an exact quote of its text."
        ),
    )
    title: str = Field(default="", description="Name the independently completable deliverable; an addition to an existing Task's scope is an update, not a new deliverable. Required nonblank for create_task/record_candidate. Existing-ID updates may omit it or use empty string; a nonempty provided update title must not be whitespace-only. Only update_fields changes a provided title. Promotion, acceptance and merge preserve the stored title.")
    description: str = Field(default="", description="Describe this deliverable or the existing Task's actual scope/content update; do not split additions into duplicate Tasks.")
    formal_basis: FormalTaskBasis | None = None
    acceptance_polarity: Literal["accepted", "declined", "ambiguous"] | None = None
    acceptance_target_signal_id: int | None = Field(default=None, gt=0)
    status: Literal["open", "waiting", "done", "cancelled"] | None = None
    business_relevance: Literal["unknown", "not_relevant", "relevant"] | None = None
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence: dict[str, Any] = Field(default_factory=dict)
    owner_kind: Literal["individual", "team", "unknown"] | None = Field(
        default=None,
        description=(
            "Whether the source identifies one individual owner, a team, or no resolvable owner. "
            "A team is not sufficient for formal Task creation."
        ),
    )
    owner_relation: Literal[
        "explicit_assignment", "self_commitment", "meeting_summary_action_item",
        "speaker_only", "unknown",
    ] | None = Field(
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
    project_proposal: ProjectProposal | None = None
    project_link_proposal: TaskProjectLinkProposal | None = Field(default=None,
        description="Required for a new-action Task supporting Attention at an existing positive Project anchor. Existing confirmed Task links may be reused with update_task; Attention anchor alone does not confirm a link.")
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
        if self.action in {"record_candidate", "create_task"} and not self.title.strip():
            raise ValueError("new task decision requires title")
        if self.action == "update_task" and self.title and not self.title.strip():
            raise ValueError("provided update title must be nonblank")
        if self.action != "skip" and (not self.source_excerpt.strip() or not self.source_ref.strip()):
            raise ValueError("task decisions require a source excerpt (a sentence taken from the source) and reference")
        if self.action == "update_task" and any(
            relation.related_task_id == self.task_id for relation in self.relation_proposals
        ):
            raise ValueError("relation requires a different related Task than this decision's task_id")
        if self.evidence_origin != "current" and not (
            self.action == "record_candidate"
            or (self.action == "update_task" and self.transition == "update_fields")
        ):
            raise ValueError(
                "earlier session or memory evidence may refine a Task (update_fields) or record a candidate; "
                "formal creation, promotion, acceptance and merges need the current Work Item's authority"
            )
        if self.action != "skip" and self.evidence_origin != "current" and not (
            self.source_link.strip() or self.source_description.strip()
            or (self.source_group.strip() and self.source_person.strip())
        ):
            raise ValueError("earlier or remembered evidence needs its source link, or, when there is none, a description of where it is (e.g. group and person)")
        if self.evidence_origin != "current" and self.date_evidence:
            raise ValueError("date evidence must come from the current Work Item")
        if self.action == "create_task" and self.formal_basis is None:
            raise ValueError("formal Task creation requires formal_basis")
        if self.action == "record_candidate" and self.formal_basis is not None:
            raise ValueError("candidate cannot carry formal_basis")
        if self.project_proposal is not None and self.evidence_origin != "current":
            raise ValueError("formal Project proposals require the current Work Item evidence")
        if self.project_proposal is not None and self.project_candidate_proposal is not None:
            raise ValueError("a decision cannot contain both a formal Project and a Project candidate proposal")
        if self.project_link_proposal is not None:
            if self.evidence_origin != "current":
                raise ValueError("existing Project links require the current Work Item evidence")
            if self.project_proposal is not None:
                raise ValueError("a decision cannot mix Project registration and existing Project link")
            if (self.attention_proposal is not None
                and self.attention_proposal.anchor_id != self.project_link_proposal.anchor_id):
                raise ValueError("Attention anchor must match the existing Project link target")
        if (self.action in {"record_candidate", "create_task"}
            and self.attention_proposal is not None and self.attention_proposal.anchor_id is not None
            and self.project_proposal is None and self.project_link_proposal is None):
            raise ValueError("new Task Attention requires matching project_link_proposal for an existing Project")
        if (
            self.attention_proposal is not None
            and self.attention_proposal.anchor_id is None
            and self.project_proposal is None
        ):
            raise ValueError("attention with a null anchor requires this decision's project_proposal")
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


class TaskProjectAssessment(StrictTaskModel):
    project_title: str = Field(
        description="Registered Project title when its identity is known; when identity is unresolved, preserve the source's Project clue without treating it as a registered Project.",
    )
    outcome: Literal["needs_attention", "not_needed", "insufficient_evidence"] = Field(
        description="Exactly one judgment for this Project: needs_attention for a retained existing card or matching current proposal, not_needed for a supported negative judgment, or insufficient_evidence only for genuine unconfirmed identity or missing Task/risk evidence.",
    )
    reason: str = Field(
        description="Concrete reason for the outcome, grounded in the cited facts; do not restate an inference as an original quote.",
    )
    assessment_basis: Literal["current_observation", "historical_comparison"] = Field(
        description="current_observation asserts current-source facts. historical_comparison compares them with original persisted evidence and therefore requires both citation shapes.",
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
        description="Zero-based position in this result's task_decisions list whose project_proposal identifies the Project; never a persisted Project ID.",
    )
    existing_attention_id: int | None = Field(
        default=None,
        strict=True,
        gt=0,
        description="Positive persisted ID of a retained needs_attention card: an original-proof claim, not an update target. For current risk changes use attention_proposal; its Project key reuses the existing card. Leave existing_attention_id null unless you cite and verify that card's stored original evidence. Read the card's current_project_attention entry and cite at least one assessment_json.evidence item with signal_id, source_ref, and source_excerpt unchanged. A current restatement does not replace that stored proof. Never a list position.",
    )
    decision_indexes: list[Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=list,
        description="Zero-based positions of supporting Task decisions in this result; a genuine candidate Task may support the assessment without being promoted.",
    )
    task_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(
        default_factory=list,
        description="Positive persisted IDs of supporting existing Tasks; never decision positions or guessed IDs.",
    )

    @model_validator(mode="after")
    def validate_project_assessment(self) -> "TaskProjectAssessment":
        if not self.project_title.strip() or not self.reason.strip():
            raise ValueError("project assessment requires a nonblank title and reason")
        if self.anchor_id is not None and self.project_decision_index is not None:
            raise ValueError("project assessment accepts either anchor_id or project_decision_index, not both")
        if self.anchor_id is None and self.project_decision_index is None and self.outcome != "insufficient_evidence":
            raise ValueError("unknown Project may only have an insufficient_evidence outcome")
        if self.existing_attention_id is not None and self.outcome != "needs_attention":
            raise ValueError("existing_attention_id requires needs_attention outcome")
        if len(set(self.decision_indexes)) != len(self.decision_indexes):
            raise ValueError("project assessment rejects duplicate decision_indexes")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("project assessment rejects duplicate task_ids")

        has_current = any(item.signal_id is None for item in self.evidence)
        has_persisted = any(item.signal_id is not None for item in self.evidence)
        if self.assessment_basis == "historical_comparison" and not (has_current and has_persisted):
            raise ValueError("historical_comparison requires current null-ID and positive persisted-ID evidence")
        if self.assessment_basis == "current_observation" and not has_current:
            raise ValueError("current_observation requires current null-ID evidence")
        return self


class TaskAgentDecision(StrictTaskModel):
    project_assessments: list[TaskProjectAssessment] = Field(
        description="One explicit outcome, concrete reason, and original evidence set for every relevant business Project or Project clue in the current source and current Tasks' confirmed Project links. This semantic coverage is not limited to structured selectors emitted in this output; envelope validation can only prove coverage of emitted selectors. Exact duplicate current titles and repeated known anchors share one judgment. Use [] only when there is no relevant business Project or Project clue, and explain that in update_summary.",
    )
    task_decisions: list[TaskDecision] = Field(default_factory=list)
    todo_changes: list[CompletionTodoChange] = Field(default_factory=list)
    follow_up_changes: list[CompletionFollowUpChange] = Field(default_factory=list)
    search_trace: list[CompletionSearchTrace] = Field(default_factory=list, max_length=3)
    update_summary: str = ""
    memory_recall_used: bool = False

    @model_validator(mode="after")
    def validate_decision_envelope(self) -> "TaskAgentDecision":
        if len(self.todo_changes) > 1:
            raise ValueError("a Task Agent decision may close at most one TODO")

        def selected_anchor_ids(decision: TaskDecision) -> set[int]:
            anchors: set[int] = set()
            if decision.project_link_proposal is not None:
                anchors.add(decision.project_link_proposal.anchor_id)
            if decision.attention_proposal is not None and decision.attention_proposal.anchor_id is not None:
                anchors.add(decision.attention_proposal.anchor_id)
            return anchors

        relevant_anchor_ids: set[int] = set()
        current_project_titles: set[str] = set()
        for decision in self.task_decisions:
            if decision.project_link_proposal is not None:
                relevant_anchor_ids.add(decision.project_link_proposal.anchor_id)
            if decision.project_proposal is not None:
                current_project_titles.add(decision.project_proposal.title)
            if decision.attention_proposal is not None:
                if decision.attention_proposal.anchor_id is not None:
                    relevant_anchor_ids.add(decision.attention_proposal.anchor_id)

        if not self.project_assessments:
            if relevant_anchor_ids or current_project_titles:
                raise ValueError("every relevant structured Project requires one project assessment")
            if not self.update_summary.strip():
                raise ValueError("empty project_assessments requires a nonblank update_summary explaining that no relevant Project was found")
            return self

        assessments_by_anchor: dict[int, list[TaskProjectAssessment]] = {}
        assessments_by_title: dict[str, list[TaskProjectAssessment]] = {}
        for assessment in self.project_assessments:
            known_supporting_anchors = {
                anchor_id
                for index in assessment.decision_indexes
                if index < len(self.task_decisions)
                for anchor_id in selected_anchor_ids(self.task_decisions[index])
            }
            if assessment.anchor_id is not None:
                known_supporting_anchors.add(assessment.anchor_id)
            if len(known_supporting_anchors) > 1:
                raise ValueError("one project assessment cannot combine unequal known Project anchors")
            known_supporting_titles = {
                self.task_decisions[index].project_proposal.title
                for index in assessment.decision_indexes
                if index < len(self.task_decisions)
                and self.task_decisions[index].project_proposal is not None
            }
            if len(known_supporting_titles) > 1:
                raise ValueError("one project assessment cannot combine unequal current Project proposal titles")
            if assessment.project_decision_index is not None:
                index = assessment.project_decision_index
                if index >= len(self.task_decisions):
                    raise ValueError("project_decision_index is out of bounds")
                project_decision = self.task_decisions[index]
                if project_decision.action == "skip" or project_decision.project_proposal is None:
                    raise ValueError("project_decision_index must select a non-skip decision with project_proposal")
                if index not in assessment.decision_indexes:
                    raise ValueError("project_decision_index must be one of the supporting decision_indexes")
                if assessment.project_title != project_decision.project_proposal.title:
                    raise ValueError("project assessment project_title must exactly match its project_proposal title")
            for index in assessment.decision_indexes:
                if index >= len(self.task_decisions):
                    raise ValueError("project assessment decision index is out of bounds")
                supporting = self.task_decisions[index]
                if supporting.action == "skip":
                    raise ValueError("project assessment cannot reference a skip decision")
                if (
                    supporting.project_proposal is not None
                    and supporting.project_proposal.title != assessment.project_title
                ):
                    raise ValueError(
                        "supporting current project_proposal title must exactly match assessment project_title"
                    )
                if (
                    assessment.anchor_id is None
                    and assessment.project_decision_index is None
                    and (
                        supporting.project_proposal is not None
                        or supporting.project_link_proposal is not None
                        or (
                            supporting.attention_proposal is not None
                            and supporting.attention_proposal.anchor_id is not None
                        )
                    )
                ):
                    raise ValueError("an unknown Project clue cannot reference a decision selecting the same Project or another structured Project")

            if assessment.anchor_id is not None:
                assessments_by_anchor.setdefault(assessment.anchor_id, []).append(assessment)
            elif assessment.project_decision_index is not None:
                assessments_by_title.setdefault(assessment.project_title, []).append(assessment)

        for anchor_id in relevant_anchor_ids:
            covering_assessments = [
                assessment
                for assessment in self.project_assessments
                if assessment.anchor_id == anchor_id
                or (
                    assessment.project_decision_index is not None
                    and any(
                        anchor_id in selected_anchor_ids(self.task_decisions[index])
                        for index in assessment.decision_indexes
                    )
                )
            ]
            if len(covering_assessments) != 1:
                raise ValueError(f"structured Project anchor {anchor_id} requires exactly one assessment")
        for title in current_project_titles:
            covering_assessments = [
                assessment
                for assessment in self.project_assessments
                if (
                    assessment.project_decision_index is not None
                    and assessment.project_title == title
                )
                or (
                    assessment.anchor_id is not None
                    and assessment.project_title == title
                    and any(
                        self.task_decisions[index].project_proposal is not None
                        and self.task_decisions[index].project_proposal.title == title
                        for index in assessment.decision_indexes
                    )
                )
            ]
            if len(covering_assessments) != 1:
                raise ValueError(f"current project_proposal title {title!r} requires exactly one assessment")

        for anchor_id, assessments in assessments_by_anchor.items():
            if len(assessments) > 1:
                raise ValueError(f"Project anchor {anchor_id} must have exactly one assessment")
        for title, assessments in assessments_by_title.items():
            if len(assessments) > 1:
                raise ValueError(f"exact Project proposal title {title!r} must have exactly one assessment")

        def assessment_matches_attention(
            assessment: TaskProjectAssessment,
            index: int,
            *,
            require_support: bool,
        ) -> bool:
            if require_support and index not in assessment.decision_indexes:
                return False
            decision = self.task_decisions[index]
            proposal = decision.attention_proposal
            if proposal is None:
                return False
            if proposal.anchor_id is not None:
                if assessment.anchor_id is not None:
                    return assessment.anchor_id == proposal.anchor_id
                if require_support:
                    return assessment.project_decision_index is not None
                if (
                    decision.project_proposal is not None
                    and assessment.project_title == decision.project_proposal.title
                ):
                    return True
                return any(
                    proposal.anchor_id
                    in selected_anchor_ids(self.task_decisions[supporting_index])
                    for supporting_index in assessment.decision_indexes
                )
            return (
                decision.project_proposal is not None
                and assessment.project_title == decision.project_proposal.title
                and (
                    assessment.anchor_id is not None
                    or assessment.project_decision_index is not None
                )
            )

        for assessment in self.project_assessments:
            matching_attention_indexes = [
                index
                for index in assessment.decision_indexes
                if assessment_matches_attention(
                    assessment, index, require_support=True
                )
            ]
            if assessment.outcome == "needs_attention":
                if assessment.existing_attention_id is None and not matching_attention_indexes:
                    has_omitted_matching_proposal = any(
                        assessment_matches_attention(
                            assessment, index, require_support=False
                        )
                        for index in range(len(self.task_decisions))
                    )
                    if has_omitted_matching_proposal:
                        raise ValueError("every attention_proposal must be named as a supporting decision")
                    raise ValueError("needs_attention requires existing_attention_id or a matching attention_proposal")
            elif matching_attention_indexes:
                raise ValueError("an attention_proposal requires a needs_attention assessment")

        for index, decision in enumerate(self.task_decisions):
            proposal = decision.attention_proposal
            if proposal is None:
                continue
            matching = [
                assessment
                for assessment in self.project_assessments
                if assessment_matches_attention(
                    assessment, index, require_support=True
                )
            ]
            if len(matching) != 1 or matching[0].outcome != "needs_attention":
                raise ValueError("every attention_proposal requires one corresponding needs_attention assessment")
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


class TaskAttentionProjectionOutcome(BaseModel):
    task_id: int | None
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


class TaskAttentionProjectionReceipt(BaseModel):
    status: Literal["pending", "no_proposal", "completed", "partial", "failed"]
    source_type: str
    task_decision_count: int
    project_link_count: int
    registry_row_count: int | None = None
    proposal_count: int
    applied_count: int = 0
    outcomes: list[TaskAttentionProjectionOutcome] = Field(default_factory=list)
    project_assessments: list[TaskAttentionAssessmentResult] = Field(default_factory=list)
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
