from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkItemSourceType(StrEnum):
    REPLY_ATTEMPT = "reply_attempt"
    AI_MINUTES = "ai_minutes"
    LOCAL_FILE = "local_file"
    MEMORY_RECALL = "memory_recall"


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


class FollowUpDraftStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkSummaryStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    DISCARDED = "discarded"


class WorkItemSource(BaseModel):
    type: WorkItemSourceType
    ref: str = ""
    title: str = ""
    conversation_id: str = ""
    conversation_title: str = ""
    created_at: str = ""


class WorkItemContext(BaseModel):
    sender: str = ""
    participants: list[str] = Field(default_factory=list)
    source_conversation_kind: WorkItemSourceKind
    source_conversation_title: str = ""


class WorkItem(BaseModel):
    source: WorkItemSource
    summary: str
    project_name: str = ""
    context: WorkItemContext


class ProjectFact(BaseModel):
    description: str
    source: str
    created: str = ""
    updated: str = ""


class ProjectMemoryContextItem(BaseModel):
    source: str = "memory_recall"
    uuid: str = ""
    text: str = ""
    summary: str = ""
    created_at: str = ""


class ProjectMemoryContext(BaseModel):
    query: str = ""
    summary: str = ""
    memories: list[ProjectMemoryContextItem] = Field(default_factory=list)


class TaskProjectPatch(BaseModel):
    id: int | None = None
    title: str = ""
    category: ProjectCategory = ProjectCategory.OTHER
    tags: list[str] = Field(default_factory=list)
    status: ProjectStatus = ProjectStatus.ACTIVE
    priority: ProjectPriority = ProjectPriority.NONE
    risk_level: RiskLevel = RiskLevel.NONE
    needs_derek_attention: bool = False
    owner_user_id: str = ""
    owner_name: str = ""
    related_people: list[dict[str, str]] = Field(default_factory=list)
    goal: str = ""
    background: str = ""
    memory_context: ProjectMemoryContext = Field(default_factory=ProjectMemoryContext)
    facts: list[ProjectFact] = Field(default_factory=list)
    current_state: str = ""
    blocker: str = ""
    next_step: str = ""
    next_follow_up_at: str = ""
    follow_up_mode: FollowUpMode = FollowUpMode.NONE
    source_conversations: list[dict[str, Any]] = Field(default_factory=list)


class TodoChange(BaseModel):
    action: Literal["create", "update", "close", "cancel"]
    todo_id: int | None = None
    title: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    status: TodoStatus = TodoStatus.OPEN
    priority: ProjectPriority = ProjectPriority.NONE
    deadline_at: str = ""
    next_follow_up_at: str = ""
    follow_up_question: str = ""
    completion_evidence: dict[str, Any] | None = None
    blocker: str = ""


class FollowUpDraftDecision(BaseModel):
    todo_id: int | None = None
    owner_user_id: str = ""
    owner_name: str = ""
    target_conversation_id: str = ""
    target_kind: Literal["group", "direct"]
    question_text: str
    scheduled_at: str = ""
    risk_check: dict[str, Any] = Field(default_factory=dict)
    status: FollowUpDraftStatus = FollowUpDraftStatus.DRAFT


class TaskAgentDecision(BaseModel):
    action: Literal["discard", "create_project", "update_project"]
    discard_reason: str = ""
    project: TaskProjectPatch | None = None
    todo_changes: list[TodoChange] = Field(default_factory=list)
    follow_up_drafts: list[FollowUpDraftDecision] = Field(default_factory=list)
    update_summary: str = ""
    merge_reason: str = ""
    memory_recall_used: bool = False
    confidence: float = 0.0
    failure_risk: str = ""
    failure_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)


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
    owner_user_id: str = ""
    owner_name: str = ""
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


class WorkSummaryInput(BaseModel):
    id: int
    source_type: WorkItemSourceType
    source_ref: str
    payload_json: str
    status: WorkSummaryStatus
    attempts: int = 0
    error: str = ""
    created_at: str
    updated_at: str


class TaskAgentRun(BaseModel):
    id: int
    summary_input_id: int
    codex_session_id: str = ""
    decision_json: str = "{}"
    audit_summary: str = ""
    memory_recall_used: bool = False
    created_at: str


class FollowUpDraft(BaseModel):
    id: int
    project_id: int
    todo_id: int = 0
    owner_user_id: str = ""
    owner_name: str = ""
    target_conversation_id: str = ""
    target_kind: str = ""
    question_text: str = ""
    risk_check_json: str = "{}"
    status: FollowUpDraftStatus
    send_result_json: str = "{}"
    scheduled_at: str = ""
    sent_at: str = ""
    created_at: str
