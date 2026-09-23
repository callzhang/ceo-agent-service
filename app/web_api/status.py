"""Strict response contract for the React Status page."""

from pydantic import BaseModel, ConfigDict, Field


class StrictStatusModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StatusMeta(StrictStatusModel):
    snapshot_at: str


class ServiceStatus(StrictStatusModel):
    label: str
    target: str
    ok: bool
    state: str
    detail: str
    pid: str
    runs: str
    initialized: str
    last_terminating_signal: str
    returncode: int


class PersistedComponentHealth(StrictStatusModel):
    component: str
    state: str
    status: str
    detail: str
    latest_tick_at: str
    latest_error: str
    latest_error_at: str
    updated_at: str


class SystemHealth(StrictStatusModel):
    state: str
    detail: str
    checked_at: str
    violations: int
    components: list[PersistedComponentHealth] = Field(default_factory=list)


class ComponentStatus(StrictStatusModel):
    name: str
    role: str
    cadence: str
    status: str
    latest_tick_at: str
    latest_error: str
    latest_error_at: str


class ConnectorStatus(StrictStatusModel):
    channel: str
    state: str
    reason_code: str
    detail: str
    commands: list[list[str]]


class EmailHealthEntry(StrictStatusModel):
    scope: str
    status: str
    error_code: str | None = None
    error_stage: str | None = None
    error_type: str | None = None
    error_fields: str | None = None
    failures: int | None = None
    persisted_count: int | None = None
    task_count: int | None = None
    isolated_count: int | None = None
    superseded_count: int | None = None
    unresolved_count: int | None = None
    updated_at: str


class EmailProcessHealth(StrictStatusModel):
    status: str
    accounts: int
    runtime_loops: int
    readiness_ready: int
    readiness_total: int
    updated_at: str


class EmailHealth(StrictStatusModel):
    status: str
    updated_at: str
    process: EmailProcessHealth | None
    runtime_loops: list[EmailHealthEntry]
    accounts: list[EmailHealthEntry]
    checks: list[EmailHealthEntry]


class MeetingMemoryHealth(StrictStatusModel):
    pending: int
    due: int
    delayed: int
    processing: int
    retryable: int
    failed: int
    oldest_due_at: str
    oldest_due_seconds: int
    completed_last_hour: int
    active_agents: int
    ghost_runtime_attempts: int
    delayed_after_seconds: int


class WechatEndpointStatus(StrictStatusModel):
    enabled: bool
    status: str
    error: str


class WechatPreflightStatus(StrictStatusModel):
    status: str
    error: str


class WechatAccountStatus(StrictStatusModel):
    ready: bool
    account_id: str


class WechatStatus(StrictStatusModel):
    reader: WechatEndpointStatus
    sender: WechatEndpointStatus
    preflight: WechatPreflightStatus
    account: WechatAccountStatus


class QueueStatus(StrictStatusModel):
    name: str
    table: str
    counts: dict[str, int]
    pending: int
    processing: int
    failed: int
    retryable: int
    latest_updated_at: str
    latest_error: str


class DispatcherQueueStatus(StrictStatusModel):
    name: str
    pending: int
    due: int
    oldest_available_at: str | None
    running: int
    latest_error: str


class AttentionRow(StrictStatusModel):
    category: str
    id: str
    status: str
    context: str
    summary: str
    updated_at: str
    error: str
    root_cause: str | None = None
    detail_url: str | None = None


class HumanDecisionRow(AttentionRow):
    detail_label: str
    detail: str


class DatabaseStatus(StrictStatusModel):
    path: str


class QueueSummary(StrictStatusModel):
    queue_count: int
    pending: int
    processing: int
    failed: int
    retryable: int
    attention: int


class WorkerStatus(StrictStatusModel):
    service: ServiceStatus
    system_health: SystemHealth
    components: list[ComponentStatus]
    connectors: dict[str, ConnectorStatus]
    email: EmailHealth
    meeting_memory_health: MeetingMemoryHealth
    wechat: WechatStatus
    queues: list[QueueStatus]
    dispatcher_queues: list[DispatcherQueueStatus]
    attention_rows: list[AttentionRow]
    human_decision_rows: list[HumanDecisionRow] = Field(default_factory=list)
    database: DatabaseStatus
    summary: QueueSummary


class StatusEnvelope(StrictStatusModel):
    item: WorkerStatus
    meta: StatusMeta
