import errno
import fcntl
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeFailureClass,
    RuntimeKind,
)
from app.codex_failure import (
    CODEX_PROVIDER_AUTH_FAILED,
    classify_codex_process_failure,
)
from app.feedback_policy import FeedbackPressureStats
from app.feedback_processing import (
    FEEDBACK_PROCESSING_BATCH_ERROR,
    FEEDBACK_PROCESSING_CLAIM_ERROR,
    FeedbackProcessingBatch,
    FeedbackProcessingBatchError,
    FeedbackProcessingClaimError,
    FeedbackProcessingItem,
    FeedbackImportItem,
    ResolutionEvidence,
    detail_references,
    persisted_feedback_summary,
    validate_resolution_evidence,
)
from app.config import feedback_spike_vercel_base_url
from app.feedback_spike import extract_configured_feedback_link_context
from app.history import HistoryItem
from app.legacy_receipt import legacy_receipt_has_explicit_failure
from app.meeting_alignment_models import (
    MeetingAlignmentJob,
    MeetingAlignmentQueueStatus,
    MeetingAlignmentRun,
)
from app.task_models import (
    DingTalkTodoLinkStatus,
    FollowUpDraft,
    WorkProject,
    WorkSummaryInput,
    WorkTodo,
    WorkTodoDingTalkLink,
    WorkUpdate,
)
from app.wechat.models import WechatReplyScope

FAST_PATH_UNREAD_BACKOFF_TASK_ERROR = "waiting_fast_path_unread_backoff"
SQLITE_BUSY_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MILLISECONDS = SQLITE_BUSY_TIMEOUT_SECONDS * 1000
STORE_WRITE_LOCK_RETRY_ATTEMPTS = 3
STORE_WRITE_LOCK_RETRY_DELAY_SECONDS = 0.25
AGENT_RUN_WRITE_LOCK_RETRY_ATTEMPTS = 3
AGENT_RUN_WRITE_LOCK_RETRY_DELAY_SECONDS = 0.25
CODEX_SESSION_LOCK_STALE_SECONDS = 20 * 60
CODEX_SESSION_LOCK_RETRY_ATTEMPTS = 3
CODEX_SESSION_LOCK_RETRY_DELAY_SECONDS = 0.25
SCHEMA_CHECK_LOCK_RETRY_ATTEMPTS = 3
SCHEMA_CHECK_LOCK_RETRY_DELAY_SECONDS = 0.25
CODEX_CAPACITY_PAUSE_STATE_KEY = "codex_capacity_pause"
ERROR_RECOVERY_QUIET_PERIOD_SECONDS = 4 * 60 * 60
REPLY_ATTEMPT_CLOSED_AFTER_REVIEW = "closed_after_review"
STORE_SCHEMA_VERSION_KEY = "store_schema_version"
STORE_SCHEMA_VERSION = "2026-08-26.2"
STORE_SCHEMA_REQUIRED_TABLES = (
    "feedback_processing_batches",
    "feedback_processing_items",
    "task_todo_sync_outbox",
    "agent_runtime_attempts",
    "conversation_runtime_sessions",
    "weekly_okr_analysis_jobs",
    "wechat_memory_import_jobs",
    "agent_run_events",
    "agent_run_state_events",
    "agent_effect_intents",
    "follow_up_send_attempts",
    "runtime_route_pauses",
    "workbench_tasks",
    "workbench_turns",
    "workbench_events",
    "workbench_attachments",
    "workbench_artifacts",
    "workbench_confirmations",
)
STORE_SCHEMA_REQUIRED_INDEXES = (
    "idx_feedback_processing_items_status",
    "idx_feedback_processing_items_batch",
    "idx_reply_attempts_agent_run_recovery",
    "idx_runtime_attempt_active_route",
    "idx_runtime_attempt_active_lease",
    "idx_task_agent_runs_active_input",
    "idx_agent_run_state_events_run",
    "idx_agent_effect_intents_run",
    "idx_agent_effect_intents_operation",
    "idx_meeting_alignment_runs_active_job",
    "idx_weekly_okr_analysis_jobs_identity",
    "idx_wechat_memory_import_jobs_status",
    "idx_workbench_events_turn_id_id",
    "idx_workbench_artifacts_turn_created_id",
    "idx_workbench_turns_task_created_id",
    "idx_workbench_turns_task_sequence",
    "idx_workbench_tasks_updated_id",
    "idx_workbench_confirmations_turn_created_id",
    "idx_workbench_attachments_task_created_id",
    "idx_workbench_attachments_task_request",
    "idx_workbench_events_event_type",
)
STORE_SCHEMA_REMOVED_TABLES = (
    "universal_plan_executions",
    "universal_action_executions",
)
STORE_SCHEMA_REQUIRED_COLUMNS = {
    "reply_attempts": (
        "human_decision_options_json",
        "feedback_scope",
        "skill_update_requested",
        "skill_update_receipts_json",
    ),
    "agent_runtime_attempts": (
        "session_mode",
        "source_session_id",
        "attempt_purpose",
        "validation_retry_policy_id",
        "validation_result_schema_id",
        "lease_owner",
        "lease_expires_at",
        "result_schema_id",
        "result_envelope_json",
    ),
    "conversation_runtime_sessions": ("contract_hash",),
    "task_agent_runs": ("status", "error", "finished_at", "updated_at"),
    "meeting_alignment_runs": ("finished_at", "updated_at"),
    "weekly_okr_analysis_jobs": (
        "week_end",
        "manager_user_id",
        "source_digest",
        "status",
        "lease_owner",
        "lease_expires_at",
        "error",
        "finished_at",
        "updated_at",
    ),
    "wechat_memory_import_jobs": (
        "import_run_id",
        "account_id",
        "status",
        "error",
        "finished_at",
        "updated_at",
    ),
}
STORE_SCHEMA_REQUIRED_TRIGGERS = (
    "trg_runtime_attempt_session_evidence_trim_insert",
    "trg_runtime_attempt_session_evidence_trim_update",
    "trg_runtime_attempt_generalized_lease_insert",
    "trg_runtime_attempt_generalized_lease_update",
    "trg_runtime_attempt_lineage_insert",
    "trg_runtime_attempt_lineage_update",
    "trg_runtime_attempt_lineage_immutable",
)
MAX_AGENT_RUN_EVENT_BYTES = 256 * 1024
MAX_RUNTIME_RESULT_ENVELOPE_BYTES = 64 * 1024
MAX_RECONCILIATION_EVENTS = 256
MAX_UNKNOWN_AUDIT_RECONCILIATION_ATTEMPTS = 16
RECONCILIATION_EVENT_LIMIT_ERROR = "agent run reconciliation event limit exceeded"
RECONCILIATION_ATTEMPT_LIMIT_ERROR = "agent run reconciliation attempt limit exceeded"
RUNTIME_OPERATION_WORKLOAD_KINDS = frozenset(
    {"structured", "meeting", "task", "weekly_okr", "memory"}
)
MEETING_ALIGNMENT_RUN_TERMINAL_STATUSES = frozenset(
    {"failed", "retry", "no_action", "ready_to_send"}
)
MEETING_ALIGNMENT_DUPLICATE_RUNNING_MIGRATION_ERROR = (
    "schema_migration_duplicate_running_meeting_run"
)
_INITIALIZED_STORE_PATHS: set[Path] = set()
_INITIALIZE_LOCK = threading.Lock()


def _replace_text_in_json(value: object, old: str, new: str) -> object:
    """Replace one persisted proposal text everywhere its verification names it."""
    if isinstance(value, dict):
        for key, child in value.items():
            value[key] = _replace_text_in_json(child, old, new)
        return value
    if isinstance(value, list):
        for index, child in enumerate(value):
            value[index] = _replace_text_in_json(child, old, new)
        return value
    if isinstance(value, str):
        return value.replace(old, new)
    return value


class OrgUserProfile(BaseModel):
    user_id: str
    name: str = ""
    title: str = ""
    open_dingtalk_id: str | None = None
    manager_user_id: str | None = None
    manager_name: str = ""
    department_ids: set[str] = set()
    department_names: set[str] = set()
    org_labels: list[str] = Field(default_factory=list)
    has_subordinate: bool | None = None


class ReplyAttempt(BaseModel):
    id: int
    conversation_id: str
    conversation_title: str
    trigger_message_id: str
    trigger_sender: str
    trigger_text: str
    action: str
    sensitivity_kind: str
    agent_run_id: int | None = None
    codex_reason: str
    draft_reply_text: str
    direct_user_id: str = ""
    direct_open_dingtalk_id: str = ""
    codex_session_id: str = ""
    codex_transcript_start_line: int = 0
    codex_transcript_end_line: int = 0
    audit_documents_json: str = "[]"
    audit_tool_events_json: str = "[]"
    audit_summary: str = ""
    human_decision_options_json: str = "[]"
    oa_process_instance_id: str = ""
    oa_task_id: str = ""
    oa_url: str = ""
    oa_action: str = ""
    oa_remark: str = ""
    oa_action_result_json: str = ""
    calendar_event_id: str = ""
    calendar_response_status: str = ""
    calendar_response_result_json: str = ""
    mail_mailbox: str = ""
    mail_message_id: str = ""
    mail_subject: str = ""
    mail_reply_text: str = ""
    mail_action_result_json: str = ""
    reaction_action_result_json: str = ""
    document_action_result_json: str = ""
    final_reply_text: str
    permission_action: str
    permission_reason: str
    send_status: str
    send_error: str
    retry_count: int
    reviewed_at: str | None = None
    reviewer_feedback: str = ""
    corrected_reply_text: str = ""
    feedback_scope: str = "one_time"
    skill_update_requested: bool = False
    skill_update_receipts_json: str = "[]"
    channel: str = "dingtalk"
    created_at: str
    updated_at: str


class RecentFollowUpCandidate(BaseModel):
    follow_up_id: int
    project_id: int
    project_title: str = ""
    project_status: str = ""
    project_priority: str = ""
    project_risk_level: str = ""
    todo_id: int = 0
    todo_title: str = ""
    todo_status: str = ""
    todo_priority: str = ""
    todo_deadline_at: str = ""
    todo_next_follow_up_at: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    target_conversation_id: str = ""
    target_kind: str = ""
    question_text: str = ""
    scheduled_at: str = ""
    sent_at: str = ""
    status: str = ""
    reaction_status: str = ""
    reaction_summary: str = ""
    suppressed_reason: str = ""
    evidence_check_json: str = "{}"
    risk_check_json: str = "{}"
    send_result_json: str = "{}"


class ReplyError(BaseModel):
    id: int
    conversation_id: str | None = None
    message_id: str | None = None
    kind: str
    detail: str
    created_at: str
    resolved_at: str = ""
    resolution: str = ""


class OperationLog(BaseModel):
    id: str
    source_table: str
    source_id: int
    occurred_at: str
    category: str
    action: str
    status: str
    context: str = ""
    summary: str = ""
    detail: str = ""
    conversation_id: str = ""
    message_id: str = ""


class SentTodoRecord(BaseModel):
    kind: str
    source_id: int
    sent_at: str
    status: str
    title: str = ""
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    owners_json: str = "[]"
    project_id: int = 0
    project_title: str = ""
    todo_id: int = 0
    todo_title: str = ""
    todo_description: str = ""
    original_text: str = ""
    deadline_at: str = ""
    priority: str = ""
    tags_json: str = "[]"
    participants_json: str = "[]"
    files_json: str = "[]"
    target_kind: str = ""
    target_conversation_id: str = ""
    external_id: str = ""
    detail: str = ""


class SentReply(BaseModel):
    id: int
    conversation_id: str
    trigger_message_id: str
    reply_text: str
    send_result_json: str = ""
    recall_key: str = ""
    recall_status: str = ""
    recall_error: str = ""
    recalled_at: str | None = None
    feedback_token: str = ""
    sent_at: str


class MemoryWriteEvent(BaseModel):
    id: int
    attempt_id: int
    event_type: str
    payload_json: str
    status: str
    attempts: int
    last_error: str
    memory_episode_id: str
    created_at: str
    updated_at: str


class FeedbackEvent(BaseModel):
    key: str
    feedback_token: str
    rating: str = ""
    rating_label: str = ""
    comment: str = ""
    original_text: str = ""
    reply_text: str = ""
    source: str = ""
    received_at: str = ""
    resolved_at: str = ""
    raw_json: str = "{}"
    created_at: str
    updated_at: str


class UserFeedbackItem(BaseModel):
    key: str
    feedback_token: str
    rating: str = ""
    rating_label: str = ""
    comment: str = ""
    source: str = ""
    received_at: str = ""
    attempt_id: int = 0
    agent_run_id: int = 0
    codex_session_id: str = ""
    attempt_role: str = ""
    project_id: int = 0
    processing_status: str = "pending"
    conversation_title: str = ""
    trigger_sender: str = ""
    trigger_text: str = ""
    final_reply_text: str = ""
    draft_reply_text: str = ""
    codex_reason: str = ""
    audit_summary: str = ""
    reviewer_feedback: str = ""
    corrected_reply_text: str = ""
    resolved_at: str = ""
    updated_at: str = ""


class ServiceBugfixCandidate(BaseModel):
    id: int
    feedback_event_key: str
    feedback_token: str = ""
    attempt_id: int = 0
    status: str = "pending"
    title: str
    reason: str
    feedback_comment: str
    conversation_title: str = ""
    trigger_text: str = ""
    created_at: str
    updated_at: str


class ConversationRecord(BaseModel):
    conversation_id: str
    title: str
    single_chat: bool
    codex_session_id: str | None = None


class CodexSessionSearchResult(BaseModel):
    session_id: str
    source_type: str
    source_id: str
    title: str
    summary_text: str
    fts_text: str
    embedding_score: float = 0.0
    bm25_score: float | None = None
    score: float = 0.0
    updated_at: str = ""


class ReplyTask(BaseModel):
    id: int
    channel: str = "dingtalk"
    conversation_id: str
    conversation_title: str
    single_chat: bool
    trigger_message_id: str
    trigger_create_time: str
    trigger_sender: str
    trigger_text: str
    trigger_message_json: str = "{}"
    available_at: str = ""
    force_new_decision: bool = False
    oa_url: str = ""
    manual_rerun_attempt_id: int = 0
    manual_rerun_revision_key: str = ""
    execution_generation: str = "initial"
    recovery_code: str = ""
    status: str
    attempts: int
    locked_at: str | None = None
    error: str = ""
    created_at: str
    updated_at: str


class AgentRole(StrEnum):
    CONSUMER = "consumer"
    AUDIT = "audit"


class RuntimeAttemptSessionMode(StrEnum):
    FRESH = "fresh"
    RESUME = "resume"


class WeeklyOkrAnalysisJobClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    CACHE_HIT = "cache_hit"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class WeeklyOkrAnalysisJobClaim:
    job_id: int
    outcome: WeeklyOkrAnalysisJobClaimOutcome
    reclaimed_stale: bool = False


class AgentRuntimeAttemptStartConflictError(RuntimeError):
    """Raised when another executor already started a persisted attempt."""


class AgentRuntimeAttemptLeaseLostError(RuntimeError):
    """Raised when a generalized attempt write has lost its owner lease."""


class RuntimeRoutePausedError(RuntimeError):
    """Raised when a route pause races with generalized attempt selection."""


class AgentRun(BaseModel):
    id: int
    reply_task_id: int
    execution_generation: str
    role: AgentRole
    proposal_revision: int
    turn_attempt: int
    parent_agent_run_id: int | None
    operation_id: str
    status: str
    codex_session_id: str = ""
    transcript_start_line: int = 0
    transcript_end_line: int = 0
    final_result_json: str = ""
    structured_error_json: str = ""
    tool_events: list[dict[str, object]] = Field(default_factory=list)
    effect_started_count: int = 0
    effect_completed_count: int = 0
    effect_failed_count: int = 0
    effect_receipt_count: int = 0
    effect_unreviewed_count: int = 0
    reconciliation_event_count: int = 0
    lease_owner: str = ""
    lease_expires_at: str = ""
    reconciliation_attempts: int = 0
    reconciliation_next_attempt_at: str = ""
    reconciliation_suspended: bool = False
    started_at: str = ""
    completed_at: str = ""
    created_at: str
    updated_at: str


class AgentRuntimeAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    agent_run_id: int | None = None
    workload_kind: str
    workload_key: str
    attempt_number: int
    route_name: str
    runtime_kind: str
    credential_mode: str
    model: str
    session_mode: RuntimeAttemptSessionMode = RuntimeAttemptSessionMode.FRESH
    source_session_id: str = ""
    attempt_purpose: str = "normal"
    validation_retry_policy_id: str = ""
    validation_result_schema_id: str = ""
    session_id: str = ""
    status: str
    failure_class: str = ""
    failure_code: str = ""
    failover_permitted: bool = False
    transcript_reference: str = ""
    transcript_start: int = 0
    transcript_end: int = 0
    first_effect_started_at: str = ""
    lease_owner: str = ""
    lease_expires_at: str = ""
    result_schema_id: str = ""
    result_envelope_json: str = ""
    started_at: str
    finished_at: str = ""
    created_at: str
    updated_at: str


class RuntimeRoutePause(BaseModel):
    model_config = ConfigDict(frozen=True)

    route_name: str
    failure_code: str
    retry_at: str
    opened_at: str
    updated_at: str


class AgentExecutionReceipt(BaseModel):
    id: int
    agent_run_id: int
    receipt_id: str
    operation_id: str
    cli: str
    command_path: str
    command_digest: str
    exit_code: int
    completed: bool
    persisted: bool
    safe_to_confirm: bool
    effect_counted: bool = False
    created_at: str


@dataclass(frozen=True)
class AgentRunClaim:
    run: AgentRun
    claimed: bool


@dataclass(frozen=True)
class AgentRuntimeAttemptStartClaim:
    attempt: AgentRuntimeAttempt
    start_acquired: bool


@dataclass(frozen=True)
class ClaudeEffectDispatchClaim:
    dispatch_acquired: bool


@dataclass(frozen=True)
class ManualAgentRunResolution:
    run_id: int
    task_id: int
    attempt_id: int
    resolution: str
    execution_generation: str


class AgentRunLeaseLostError(RuntimeError):
    pass


def _persisted_agent_effect_state(events: list[dict[str, object]]) -> str:
    pending: dict[str, int] = {}
    event_closures: set[str] = set()
    confirmed = False
    for event in events:
        event_type = event.get("type")
        item = event.get("item")
        if event_type not in {"item.started", "item.completed", "item.failed"} or not isinstance(
            item, dict
        ):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("effect") != "effectful":
            continue
        call_id = item.get("call_id") or item.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            continue
        if event_type == "item.started":
            pending[call_id] = pending.get(call_id, 0) + 1
        elif event_type == "item.completed":
            if pending.get(call_id, 0) > 0:
                pending[call_id] -= 1
            event_closures.add(call_id)
            confirmed = True
        else:
            if pending.get(call_id, 0) > 0:
                pending[call_id] -= 1
            event_closures.add(call_id)
    for receipt_id in _persisted_agent_receipt_ids(events):
        if receipt_id not in event_closures and pending.get(receipt_id, 0) > 0:
            pending[receipt_id] -= 1
        confirmed = True
    if any(count > 0 for count in pending.values()):
        return "unknown"
    if confirmed:
        return "confirmed"
    return "none"


def _persisted_agent_receipt_ids(value: object) -> set[str]:
    receipt_ids: set[str] = set()
    if isinstance(value, list):
        for item in value:
            receipt_ids.update(_persisted_agent_receipt_ids(item))
        return receipt_ids
    if not isinstance(value, dict):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return receipt_ids
            if isinstance(parsed, dict | list):
                return _persisted_agent_receipt_ids(parsed)
        return receipt_ids
    if frozenset(value) == {
        "receipt_id",
        "operation_id",
        "completed",
        "persisted",
        "safe_to_confirm",
    }:
        operation_id = value.get("operation_id")
        if (
            isinstance(operation_id, str)
            and operation_id.strip()
            and value.get("completed") is True
            and value.get("persisted") is True
            and value.get("safe_to_confirm") is True
        ):
            receipt_ids.add(operation_id)
        return receipt_ids
    for item in value.values():
        receipt_ids.update(_persisted_agent_receipt_ids(item))
    return receipt_ids


def _agent_event_columns(event: dict[str, object]) -> tuple[str, str, str, str]:
    event_type = str(event.get("type") or "")
    item = event.get("item")
    if not isinstance(item, dict):
        return event_type, "", "", ""
    call_id_value = item.get("call_id") or item.get("id")
    call_id = call_id_value.strip() if isinstance(call_id_value, str) else ""
    metadata = item.get("metadata")
    effect_kind = ""
    if isinstance(metadata, dict):
        candidate = metadata.get("effect")
        if candidate in {"read_only", "effectful", "unreviewed"}:
            effect_kind = str(candidate)
    receipt_ids = _persisted_agent_receipt_ids(event)
    receipt_operation_id = next(iter(receipt_ids), "")
    return event_type, call_id, effect_kind, receipt_operation_id


def _agent_effect_identity(event: dict[str, object]) -> dict[str, object] | None:
    item = event.get("item")
    metadata = item.get("metadata") if isinstance(item, dict) else None
    if not isinstance(metadata, dict) or metadata.get("effect") != "effectful":
        return None
    identity = {
        key: metadata.get(key)
        for key in (
            "capability",
            "operation",
            "operation_id",
            "operation_digest",
            "arguments_digest",
            "target_identifiers",
            "action_index",
        )
        if key in metadata
    }
    return identity or None


def _agent_effect_state_from_counts(row: sqlite3.Row) -> str:
    starts = int(row["effect_started_count"])
    completed = int(row["effect_completed_count"])
    failed = int(row["effect_failed_count"])
    receipts = int(row["effect_receipt_count"])
    if int(row["effect_unreviewed_count"]) or starts > completed + failed + receipts:
        return "unknown"
    if completed + receipts:
        return "confirmed"
    return "none"


class OkrReviewRequest(BaseModel):
    id: int
    conversation_id: str
    conversation_title: str
    trigger_message_id: str
    trigger_sender: str
    trigger_sender_user_id: str = ""
    trigger_text: str
    period_label: str
    period_start: str
    period_end: str
    okr_source_json: str = "{}"
    status: str
    error: str = ""
    codex_session_id: str = ""
    created_at: str = ""
    updated_at: str = ""


class CodexSessionLock:
    def __init__(self, store, conversation_id: str, owner: str):
        self.store = store
        self.conversation_id = conversation_id
        self.owner = owner

    def __enter__(self):
        if not self.store.acquire_codex_session_lock(self.conversation_id, self.owner):
            raise RuntimeError(f"codex session locked: {self.conversation_id}")
        return self

    def __exit__(self, exc_type, exc, tb):
        released = self.store.release_codex_session_lock(
            self.conversation_id,
            self.owner,
        )
        if not released and exc_type is None:
            raise RuntimeError(
                f"codex session lock release failed: {self.conversation_id}"
            )
        return False


def _embedding_from_json(text: str) -> list[float]:
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    values: list[float] = []
    for item in payload:
        if isinstance(item, (int, float)):
            values.append(float(item))
    return values


def _embedding_score(
    query_embedding: list[float] | None,
    stored_embedding: list[float],
) -> float:
    if not query_embedding or not stored_embedding:
        return 0.0
    pairs = list(zip(query_embedding, stored_embedding))
    if not pairs:
        return 0.0
    dot = sum(left * right for left, right in pairs)
    left_norm = sum(left * left for left, _ in pairs) ** 0.5
    right_norm = sum(right * right for _, right in pairs) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _utc_store_time(now: str | datetime | None = None) -> tuple[datetime, str]:
    if now is None:
        value = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        value = now
    elif isinstance(now, str) and now.strip():
        try:
            value = datetime.fromisoformat(now.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("now must be an ISO timestamp") from exc
    else:
        raise ValueError("now must be an ISO timestamp or datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    value = value.replace(microsecond=0)
    return value, value.strftime("%Y-%m-%d %H:%M:%S")


def _json_object_text(value: object, *, field: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(json.loads(text), dict):
        raise ValueError(f"{field} must be a JSON object")
    return text


def _canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _is_sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


class AutoReplyStore:
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_seconds: int = SQLITE_BUSY_TIMEOUT_SECONDS,
    ):
        self.path = path
        self.busy_timeout_seconds = busy_timeout_seconds
        self.busy_timeout_milliseconds = busy_timeout_seconds * 1000
        self._read_snapshot_connection: ContextVar[sqlite3.Connection | None] = (
            ContextVar(f"audit_read_snapshot_{id(self)}", default=None)
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        path_key = self.path.resolve()
        if path_key in _INITIALIZED_STORE_PATHS:
            return
        with _INITIALIZE_LOCK:
            if path_key in _INITIALIZED_STORE_PATHS:
                return
            with self._schema_initialize_lock():
                if path_key in _INITIALIZED_STORE_PATHS:
                    return
                self._ensure_wal_journal_mode()
                if self._schema_is_current_after_lock_retry():
                    _INITIALIZED_STORE_PATHS.add(path_key)
                    return
                self._initialize()
                self.backfill_oa_audit_metadata()
                self.set_service_state(STORE_SCHEMA_VERSION_KEY, STORE_SCHEMA_VERSION)
                _INITIALIZED_STORE_PATHS.add(path_key)

    def _ensure_wal_journal_mode(self) -> None:
        with self._connect() as db:
            journal_mode = str(db.execute("pragma journal_mode = wal").fetchone()[0])
        if journal_mode.casefold() != "wal":
            raise RuntimeError(
                f"failed to enable SQLite WAL journal mode: {journal_mode}"
            )

    def _schema_is_current_after_lock_retry(self) -> bool:
        for attempt in range(SCHEMA_CHECK_LOCK_RETRY_ATTEMPTS):
            try:
                return self._schema_is_current()
            except sqlite3.OperationalError as exc:
                if not _is_sqlite_lock_error(exc):
                    raise
                if attempt + 1 >= SCHEMA_CHECK_LOCK_RETRY_ATTEMPTS:
                    raise
                time.sleep(SCHEMA_CHECK_LOCK_RETRY_DELAY_SECONDS)
        raise RuntimeError("schema check retry loop exhausted")

    @contextmanager
    def _schema_initialize_lock(self) -> Iterator[None]:
        """Serialize schema work across the worker and audit-web processes."""
        lock_path = self.path.with_name(f".{self.path.name}.initialize.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _schema_is_current(self) -> bool:
        try:
            with self._connect() as db:
                row = db.execute(
                    "select value from service_state where key=?",
                    (STORE_SCHEMA_VERSION_KEY,),
                ).fetchone()
                if row is None or str(row["value"] or "") != STORE_SCHEMA_VERSION:
                    return False
                present_tables = {
                    str(item["name"])
                    for item in db.execute(
                        "select name from sqlite_master where type='table'"
                    )
                }
                present_indexes = {
                    str(item["name"])
                    for item in db.execute(
                        "select name from sqlite_master where type='index'"
                    )
                }
                present_triggers = {
                    str(item["name"])
                    for item in db.execute(
                        "select name from sqlite_master where type='trigger'"
                    )
                }
                required_columns_present = all(
                    set(required_columns).issubset(
                        {
                            str(item["name"])
                            for item in db.execute(
                                f"pragma table_info({table_name})"
                            )
                        }
                    )
                    for table_name, required_columns in (
                        STORE_SCHEMA_REQUIRED_COLUMNS.items()
                    )
                )
        except sqlite3.OperationalError as exc:
            if _is_sqlite_lock_error(exc):
                raise
            return False
        return (
            set(STORE_SCHEMA_REQUIRED_TABLES).issubset(present_tables)
            and set(STORE_SCHEMA_REQUIRED_INDEXES).issubset(present_indexes)
            and set(STORE_SCHEMA_REQUIRED_TRIGGERS).issubset(present_triggers)
            and required_columns_present
            and not set(STORE_SCHEMA_REMOVED_TABLES).intersection(present_tables)
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        snapshot = self._read_snapshot_connection.get()
        if snapshot is not None:
            yield snapshot
            return
        connection = self._open_connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _optional_connection(
        self, connection: sqlite3.Connection | None
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return
        with self._connect() as db:
            yield db

    @contextmanager
    def _immediate_write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Acquire a short SQLite write transaction with bounded lock retry."""
        for attempt in range(STORE_WRITE_LOCK_RETRY_ATTEMPTS):
            try:
                with self._connect() as db:
                    try:
                        db.execute("begin immediate")
                    except sqlite3.OperationalError as exc:
                        if (
                            not _is_sqlite_lock_error(exc)
                            or attempt + 1 >= STORE_WRITE_LOCK_RETRY_ATTEMPTS
                        ):
                            raise
                        time.sleep(
                            STORE_WRITE_LOCK_RETRY_DELAY_SECONDS * (attempt + 1)
                        )
                        continue
                    yield db
                    return
            except sqlite3.OperationalError as exc:
                if (
                    not _is_sqlite_lock_error(exc)
                    or attempt + 1 >= STORE_WRITE_LOCK_RETRY_ATTEMPTS
                ):
                    raise
                time.sleep(STORE_WRITE_LOCK_RETRY_DELAY_SECONDS * (attempt + 1))
        raise RuntimeError("SQLite write transaction retry loop exhausted")

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_seconds,
        )
        connection.execute(f"pragma busy_timeout = {self.busy_timeout_milliseconds}")
        connection.execute("pragma synchronous = normal")
        connection.execute("pragma foreign_keys = on")
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def read_snapshot(self) -> Iterator[None]:
        """Reuse one read-only SQLite snapshot for a related audit render."""
        if self._read_snapshot_connection.get() is not None:
            yield
            return
        connection = self._open_connection()
        try:
            connection.execute("pragma query_only = on")
            connection.execute("begin")
            token = self._read_snapshot_connection.set(connection)
            try:
                yield
            finally:
                self._read_snapshot_connection.reset(token)
        finally:
            connection.rollback()
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                create table if not exists conversations (
                    conversation_id text primary key,
                    title text not null,
                    single_chat integer not null,
                    codex_session_id text,
                    codex_session_contract_hash text not null default ''
                );
                create table if not exists seen_messages (
                    message_id text primary key,
                    conversation_id text not null,
                    seen_at text not null default current_timestamp
                );
                create table if not exists sent_replies (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    trigger_message_id text not null,
                    reply_text text not null,
                    send_result_json text not null default '',
                    recall_key text not null default '',
                    recall_status text not null default '',
                    recall_error text not null default '',
                    recalled_at text,
                    feedback_token text not null default '',
                    sent_at text not null default current_timestamp
                );
                create table if not exists feedback_events (
                    key text primary key,
                    feedback_token text not null,
                    rating text not null default '',
                    rating_label text not null default '',
                    comment text not null default '',
                    original_text text not null default '',
                    reply_text text not null default '',
                    source text not null default '',
                    received_at text not null default '',
                    resolved_at text not null default '',
                    raw_json text not null default '{}',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_feedback_events_token
                    on feedback_events(feedback_token, received_at);
                create table if not exists feedback_processing_batches (
                    batch_id text primary key,
                    status text not null default 'pending'
                        check (status in ('pending', 'processing', 'resolved')),
                    requested_count integer not null default 0,
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    resolved_at text not null default ''
                );
                create table if not exists feedback_processing_items (
                    feedback_key text primary key,
                    batch_id text not null default '',
                    status text not null default 'pending'
                        check (status in ('pending', 'processing', 'resolved')),
                    workbench_task_id text not null default '',
                    workbench_turn_id text not null default '',
                    attempt_id integer not null default 0,
                    agent_run_id integer not null default 0,
                    commit_sha text not null default '',
                    test_evidence_json text not null default '{}',
                    restart_evidence_json text not null default '{}',
                    health_evidence_json text not null default '{}',
                    note text not null default '',
                    resolved_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_feedback_processing_items_status
                    on feedback_processing_items(status);
                create index if not exists idx_feedback_processing_items_batch
                    on feedback_processing_items(batch_id);
                insert or ignore into feedback_processing_items (
                    feedback_key, status, resolved_at
                )
                    select key,
                           case when trim(resolved_at) <> ''
                                then 'resolved' else 'pending' end,
                           resolved_at
                    from feedback_events;
                update feedback_processing_items
                   set status='resolved',
                       resolved_at=(select fe.resolved_at from feedback_events fe
                                    where fe.key=feedback_processing_items.feedback_key),
                       updated_at=current_timestamp
                 where exists (
                     select 1 from feedback_events fe
                      where fe.key=feedback_processing_items.feedback_key
                        and trim(fe.resolved_at) <> ''
                 );
                create table if not exists service_bugfix_candidates (
                    id integer primary key autoincrement,
                    feedback_event_key text not null unique,
                    feedback_token text not null default '',
                    attempt_id integer not null default 0,
                    status text not null default 'pending',
                    title text not null,
                    reason text not null,
                    feedback_comment text not null,
                    conversation_title text not null default '',
                    trigger_text text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_service_bugfix_candidates_status
                    on service_bugfix_candidates(status, created_at);
                create table if not exists errors (
                    id integer primary key autoincrement,
                    conversation_id text,
                    message_id text,
                    kind text not null,
                    detail text not null,
                    created_at text not null default current_timestamp,
                    resolved_at text not null default '',
                    resolution text not null default ''
                );
                create table if not exists reply_attempts (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    conversation_title text not null,
                    trigger_message_id text not null,
                    trigger_sender text not null,
                    trigger_text text not null,
                    action text not null,
                    sensitivity_kind text not null,
                    agent_run_id integer,
                    codex_reason text not null default '',
                    draft_reply_text text not null default '',
                    direct_user_id text not null default '',
                    direct_open_dingtalk_id text not null default '',
                    codex_session_id text not null default '',
                    codex_transcript_start_line integer not null default 0,
                    codex_transcript_end_line integer not null default 0,
                    audit_documents_json text not null default '[]',
                    audit_tool_events_json text not null default '[]',
                    audit_summary text not null default '',
                    human_decision_options_json text not null default '[]',
                    oa_process_instance_id text not null default '',
                    oa_task_id text not null default '',
                    oa_url text not null default '',
                    oa_action text not null default '',
                    oa_remark text not null default '',
                    oa_action_result_json text not null default '',
                    calendar_event_id text not null default '',
                    calendar_response_status text not null default '',
                    calendar_response_result_json text not null default '',
                    mail_mailbox text not null default '',
                    mail_message_id text not null default '',
                    mail_subject text not null default '',
                    mail_reply_text text not null default '',
                    mail_action_result_json text not null default '',
                    reaction_action_result_json text not null default '',
                    document_action_result_json text not null default '',
                    final_reply_text text not null default '',
                    permission_action text not null default '',
                    permission_reason text not null default '',
                    send_status text not null,
                    send_error text not null default '',
                    retry_count integer not null default 0,
                    reviewed_at text,
                    reviewer_feedback text not null default '',
                    corrected_reply_text text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_reply_attempts_trigger_message_id
                    on reply_attempts(trigger_message_id);
                create index if not exists idx_reply_attempts_status
                    on reply_attempts(send_status, created_at);
                create index if not exists idx_reply_attempts_created
                    on reply_attempts(created_at, id);
                create table if not exists memory_write_events (
                    id integer primary key autoincrement,
                    attempt_id integer not null,
                    event_type text not null,
                    payload_json text not null,
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    last_error text not null default '',
                    memory_episode_id text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(attempt_id, event_type),
                    foreign key(attempt_id) references reply_attempts(id)
                );
                create index if not exists idx_memory_write_events_attempt
                    on memory_write_events(attempt_id, id);
                create index if not exists idx_memory_write_events_status
                    on memory_write_events(status, updated_at);
                create table if not exists reply_tasks (
                    id integer primary key autoincrement,
                    channel text not null default 'dingtalk',
                    conversation_id text not null,
                    conversation_title text not null,
                    single_chat integer not null,
                    trigger_message_id text not null,
                    trigger_create_time text not null,
                    trigger_sender text not null,
                    trigger_text text not null,
                    trigger_message_json text not null default '{}',
                    available_at text not null default '',
                    force_new_decision integer not null default 0,
                    oa_url text not null default '',
                    manual_rerun_attempt_id integer not null default 0,
                    manual_rerun_revision_key text not null default '',
                    execution_generation text not null default 'initial',
                    recovery_code text not null default '',
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    locked_at text,
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(channel, conversation_id, trigger_message_id)
                );
                create index if not exists idx_reply_tasks_status
                    on reply_tasks(status, id);
                create table if not exists agent_runs (
                    id integer primary key autoincrement,
                    reply_task_id integer not null,
                    execution_generation text not null,
                    role text not null check(role in ('consumer', 'audit')),
                    proposal_revision integer not null default 0
                        check(proposal_revision >= 0),
                    turn_attempt integer not null default 0
                        check(turn_attempt >= 0),
                    parent_agent_run_id integer,
                    operation_id text not null default '',
                    status text not null default 'pending'
                        check(status in (
                            'pending', 'running', 'completed', 'failed', 'unknown'
                        )),
                    codex_session_id text not null default '',
                    transcript_start_line integer not null default 0,
                    transcript_end_line integer not null default 0,
                    final_result_json text not null default '',
                    structured_error_json text not null default '',
                    tool_events_json text not null default '[]',
                    side_effect_state text not null default 'none'
                        check(side_effect_state in ('none', 'confirmed', 'unknown')),
                    effect_started_count integer not null default 0,
                    effect_completed_count integer not null default 0,
                    effect_failed_count integer not null default 0,
                    effect_receipt_count integer not null default 0,
                    effect_unreviewed_count integer not null default 0,
                    reconciliation_event_count integer not null default 0,
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
                    reconciliation_attempts integer not null default 0,
                    reconciliation_next_attempt_at text not null default '',
                    reconciliation_suspended integer not null default 0,
                    started_at text not null default '',
                    completed_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(
                        reply_task_id, execution_generation, role,
                        proposal_revision, turn_attempt
                    ),
                    foreign key(reply_task_id) references reply_tasks(id),
                    foreign key(parent_agent_run_id) references agent_runs(id)
                );
                create index if not exists idx_agent_runs_status
                    on agent_runs(status, updated_at);
                create table if not exists agent_run_state_events (
                    id integer primary key autoincrement,
                    agent_run_id integer not null,
                    phase text not null,
                    structured_error_json text not null,
                    created_at text not null default current_timestamp,
                    foreign key(agent_run_id) references agent_runs(id)
                );
                create index if not exists idx_agent_run_state_events_run
                    on agent_run_state_events(agent_run_id, id);
                create table if not exists agent_runtime_attempts (
                    id integer primary key autoincrement,
                    agent_run_id integer,
                    workload_kind text not null,
                    workload_key text not null,
                    attempt_number integer not null check(attempt_number > 0),
                    route_name text not null,
                    runtime_kind text not null,
                    credential_mode text not null,
                    model text not null,
                    session_mode text not null default 'fresh'
                        check(session_mode in ('fresh', 'resume')),
                    source_session_id text not null default '',
                    attempt_purpose text not null default 'normal'
                        check(attempt_purpose in (
                            'normal', 'result_validation_correction'
                        )),
                    validation_retry_policy_id text not null default '',
                    validation_result_schema_id text not null default '',
                    session_id text not null default '',
                    status text not null check(status in (
                        'starting', 'running', 'completed', 'failed', 'superseded'
                    )),
                    failure_class text not null default '',
                    failure_code text not null default '',
                    failover_permitted integer not null default 0,
                    transcript_reference text not null default '',
                    transcript_start integer not null default 0,
                    transcript_end integer not null default 0,
                    first_effect_started_at text not null default '',
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
                    result_schema_id text not null default '',
                    result_envelope_json text not null default '',
                    started_at text not null default current_timestamp,
                    finished_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    check(
                        (workload_kind='agent_run' and agent_run_id is not null
                         and workload_key=cast(agent_run_id as text))
                        or
                        (workload_kind<>'agent_run' and agent_run_id is null)
                    ),
                    check(
                        (session_mode='fresh' and source_session_id='')
                        or (session_mode='resume' and trim(source_session_id)<>'')
                    ),
                    unique(workload_kind, workload_key, attempt_number),
                    foreign key(agent_run_id) references agent_runs(id)
                );
                create unique index if not exists idx_runtime_attempt_active_route
                    on agent_runtime_attempts(workload_kind, workload_key, route_name)
                    where status in ('starting', 'running');
                create table if not exists conversation_runtime_sessions (
                    conversation_id text not null,
                    route_name text not null,
                    session_id text not null,
                    contract_hash text not null default '',
                    updated_at text not null default current_timestamp,
                    primary key(conversation_id, route_name)
                );
                create table if not exists runtime_route_pauses (
                    route_name text primary key,
                    failure_code text not null,
                    retry_at text not null,
                    opened_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create table if not exists agent_run_events (
                    id integer primary key autoincrement,
                    agent_run_id integer not null,
                    sequence integer not null,
                    event_json text not null,
                    event_type text not null default '',
                    call_id text not null default '',
                    effect_kind text not null default '',
                    receipt_operation_id text not null default '',
                    event_scope text not null default 'direct',
                    created_at text not null default current_timestamp,
                    unique(agent_run_id, sequence),
                    foreign key(agent_run_id) references agent_runs(id)
                );
                create index if not exists idx_agent_run_events_run_sequence
                    on agent_run_events(agent_run_id, sequence);
                create index if not exists idx_agent_run_events_run_call
                    on agent_run_events(agent_run_id, call_id, sequence);
                create table if not exists agent_execution_receipts (
                    id integer primary key autoincrement,
                    agent_run_id integer not null,
                    receipt_id text not null,
                    operation_id text not null,
                    cli text not null,
                    command_path text not null,
                    command_digest text not null,
                    exit_code integer not null,
                    completed integer not null,
                    persisted integer not null,
                    safe_to_confirm integer not null,
                    effect_counted integer not null default 0,
                    created_at text not null default current_timestamp,
                    unique(agent_run_id, operation_id),
                    foreign key(agent_run_id) references agent_runs(id)
                );
                create index if not exists idx_agent_execution_receipts_run
                    on agent_execution_receipts(agent_run_id, id);
                create table if not exists agent_effect_intents (
                    id integer primary key autoincrement,
                    agent_run_id integer not null,
                    authorization_id text not null,
                    action_index integer not null check(action_index >= 0),
                    receipt_operation_id text not null,
                    capability text not null,
                    operation text not null,
                    operation_digest text not null,
                    arguments_digest text not null,
                    target_identifiers_json text not null,
                    state text not null default 'prepared'
                        check(state in ('prepared', 'dispatched', 'acknowledged')),
                    result_digest text not null default '',
                    exit_code integer,
                    prepared_at text not null default current_timestamp,
                    dispatched_at text not null default '',
                    acknowledged_at text not null default '',
                    updated_at text not null default current_timestamp,
                    unique(agent_run_id, authorization_id),
                    foreign key(agent_run_id) references agent_runs(id)
                );
                create index if not exists idx_agent_effect_intents_run
                    on agent_effect_intents(agent_run_id, action_index, id);
                create unique index if not exists idx_agent_effect_intents_operation
                    on agent_effect_intents(agent_run_id, receipt_operation_id);
                create table if not exists wechat_read_state (
                    account_id text primary key,
                    account_dir text not null,
                    db_dir text not null,
                    app_version text not null,
                    self_user_id text not null default '',
                    capability_status text not null default 'blocked',
                    capability_reason text not null default '',
                    watermark_sent_at text not null default '',
                    watermark_message_id text not null default '',
                    last_scan_at text not null default '',
                    updated_at text not null default current_timestamp
                );
                create table if not exists wechat_reply_scopes (
                    account_id text not null,
                    target_type text not null,
                    target_id text not null,
                    conversation_id text not null default '',
                    display_name text not null,
                    trigger_mode text not null,
                    enabled integer not null default 1,
                    binding_status text not null default 'unverified',
                    binding_evidence_json text not null default '{}',
                    disabled_reason text not null default '',
                    last_discovered_at text not null default '',
                    updated_at text not null default current_timestamp,
                    primary key(account_id, target_type, target_id)
                );
                create table if not exists wechat_deliveries (
                    id integer primary key autoincrement,
                    reply_task_id integer not null unique,
                    account_id text not null,
                    target_type text not null,
                    target_id text not null,
                    conversation_id text not null default '',
                    reply_text text not null,
                    execution_generation text not null default 'initial',
                    status text not null default 'ready_to_send',
                    action_started_at text not null default '',
                    pre_action_failure integer not null default 0,
                    evidence_json text not null default '{}',
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    foreign key(reply_task_id) references reply_tasks(id)
                );
                create index if not exists idx_wechat_deliveries_status
                    on wechat_deliveries(status, id);
                create table if not exists wechat_memory_import_jobs (
                    id integer primary key autoincrement,
                    import_run_id text not null,
                    account_id text not null,
                    status text not null default 'running'
                        check(status in ('running', 'completed', 'failed')),
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    finished_at text not null default '',
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_wechat_memory_import_jobs_status
                    on wechat_memory_import_jobs(status, id);
                create table if not exists wechat_memory_candidates (
                    id integer primary key autoincrement,
                    import_run_id text not null,
                    account_id text not null,
                    statement text not null,
                    edited_statement text not null default '',
                    category text not null,
                    confidence real not null,
                    sensitivity text not null,
                    source_conversation_ids_json text not null default '[]',
                    source_message_ids_json text not null default '[]',
                    source_time_start text not null default '',
                    source_time_end text not null default '',
                    evidence_excerpt text not null default '',
                    cleanup_notes text not null default '',
                    status text not null default 'pending',
                    reviewer text not null default '',
                    reviewed_at text not null default '',
                    memory_write_status text not null default '',
                    memory_id text not null default '',
                    memory_write_error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(import_run_id, statement)
                );
                create table if not exists meeting_alignment_jobs (
                    id integer primary key autoincrement,
                    meeting_id text not null unique,
                    title text not null default '',
                    source_json text not null default '{}',
                    participants_json text not null default '[]',
                    ended_at text not null default '',
                    eligible_at text not null default '',
                    status text not null default 'waiting',
                    attempts integer not null default 0,
                    locked_at text,
                    available_at text not null default '',
                    error text not null default '',
                    decision_json text not null default '{}',
                    target_kind text not null default '',
                    target_id text not null default '',
                    target_title text not null default '',
                    mentions_json text not null default '[]',
                    final_message text not null default '',
                    send_result_json text not null default '{}',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_meeting_alignment_jobs_claim
                    on meeting_alignment_jobs(status, available_at, eligible_at, id);
                create table if not exists meeting_alignment_runs (
                    id integer primary key autoincrement,
                    job_id integer not null,
                    codex_session_id text not null default '',
                    codex_transcript_start_line integer not null default 0,
                    codex_transcript_end_line integer not null default 0,
                    decision_json text not null default '{}',
                    audit_tool_events_json text not null default '[]',
                    audit_summary text not null default '',
                    status text not null,
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    finished_at text not null default '',
                    updated_at text not null default current_timestamp,
                    foreign key(job_id) references meeting_alignment_jobs(id)
                );
                create index if not exists idx_meeting_alignment_runs_job
                    on meeting_alignment_runs(job_id, id);
                create index if not exists idx_meeting_alignment_runs_created
                    on meeting_alignment_runs(created_at, id);
                create table if not exists codex_session_search_index (
                    id integer primary key autoincrement,
                    session_id text not null unique,
                    source_type text not null default '',
                    source_id text not null default '',
                    title text not null default '',
                    summary_text text not null default '',
                    fts_text text not null default '',
                    embedding_json text not null default '',
                    embedding_model text not null default '',
                    embedding_updated_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_codex_session_search_source
                    on codex_session_search_index(source_type, source_id);
                create virtual table if not exists codex_session_search_fts
                    using fts5(
                        title,
                        summary_text,
                        fts_text,
                        content='codex_session_search_index',
                        content_rowid='id'
                    );
                create table if not exists corpus_sources (
                    source_key text primary key,
                    last_collected_at text
                );
                create table if not exists org_user_profiles (
                    user_id text primary key,
                    name text not null default '',
                    title text not null default '',
                    open_dingtalk_id text,
                    manager_user_id text,
                    manager_name text not null default '',
                    department_ids_json text not null,
                    department_names_json text not null default '[]',
                    org_labels_json text not null default '[]',
                    has_subordinate integer,
                    fetched_at text not null default current_timestamp
                );
                create index if not exists idx_org_user_profiles_open_dingtalk_id
                    on org_user_profiles(open_dingtalk_id);
                create index if not exists idx_org_user_profiles_name
                    on org_user_profiles(name);
                create table if not exists org_cache_metadata (
                    key text primary key,
                    value_json text not null,
                    updated_at text not null default current_timestamp
                );
                create table if not exists service_state (
                    key text primary key,
                    value text not null,
                    updated_at text not null default current_timestamp
                );
                create table if not exists channel_login_reservations (
                    channel text primary key,
                    reservation_owner text not null,
                    reserved_at text not null
                );
                create table if not exists setup_wizard_steps (
                    step_id text primary key,
                    status text not null,
                    summary text not null default '',
                    manual_confirmed_at text not null default '',
                    manual_confirmed_by text not null default '',
                    updated_at text not null default current_timestamp
                );
                create table if not exists setup_wizard_events (
                    id integer primary key autoincrement,
                    step_id text not null,
                    action_id text not null,
                    status text not null,
                    summary text not null default '',
                    evidence_json text not null default '{}',
                    stdout_excerpt text not null default '',
                    stderr_excerpt text not null default '',
                    started_at text not null default current_timestamp,
                    finished_at text not null default ''
                );
                create index if not exists idx_setup_wizard_events_step
                    on setup_wizard_events(step_id, id);
                create table if not exists codex_session_locks (
                    conversation_id text primary key,
                    owner text not null,
                    locked_at text not null default current_timestamp
                );
                create table if not exists okr_review_requests (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    conversation_title text not null,
                    trigger_message_id text not null,
                    trigger_sender text not null,
                    trigger_sender_user_id text not null default '',
                    trigger_text text not null,
                    period_label text not null,
                    period_start text not null,
                    period_end text not null,
                    okr_source_json text not null default '{}',
                    status text not null default 'pending',
                    error text not null default '',
                    codex_session_id text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(conversation_id, trigger_message_id)
                );
                create index if not exists idx_okr_review_requests_status
                    on okr_review_requests(status, id);
                create table if not exists okr_review_runs (
                    id integer primary key autoincrement,
                    request_id integer not null,
                    codex_session_id text not null default '',
                    codex_transcript_start_line integer not null default 0,
                    codex_transcript_end_line integer not null default 0,
                    envelope_json text not null default '{}',
                    audit_tool_events_json text not null default '[]',
                    audit_summary text not null default '',
                    created_at text not null default current_timestamp
                );
                create table if not exists okr_review_items (
                    id integer primary key autoincrement,
                    request_id integer not null,
                    objective_title text not null,
                    objective_weight real not null default 0,
                    kr_title text not null,
                    kr_weight real not null default 0,
                    item_json text not null default '{}',
                    created_at text not null default current_timestamp
                );
                create table if not exists work_projects (
                    id integer primary key autoincrement,
                    title text not null,
                    category text not null default 'other',
                    tags_json text not null default '[]',
                    status text not null default 'active',
                    priority text not null default 'none',
                    risk_level text not null default 'none',
                    needs_derek_attention integer not null default 0,
                    owner_user_id text not null default '',
                    owner_name text not null default '',
                    owner_evidence_json text not null default '{}',
                    related_people_json text not null default '[]',
                    goal text not null default '',
                    background text not null default '',
                    facts_json text not null default '[]',
                    current_state text not null default '',
                    blocker text not null default '',
                    next_step text not null default '',
                    next_follow_up_at text not null default '',
                    follow_up_mode text not null default 'none',
                    source_conversations_json text not null default '[]',
                    memory_context_json text not null default '{}',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    last_activity_at text not null default current_timestamp
                );
                create index if not exists idx_work_projects_status_priority
                    on work_projects(status, priority, updated_at);
                create table if not exists work_todos (
                    id integer primary key autoincrement,
                    project_id integer not null,
                    title text not null,
                    description text not null default '',
                    owner_user_id text not null default '',
                    owner_name text not null default '',
                    owner_evidence_json text not null default '{}',
                    status text not null default 'open',
                    priority text not null default 'none',
                    deadline_at text not null default '',
                    next_follow_up_at text not null default '',
                    follow_up_question text not null default '',
                    blocker text not null default '',
                    completion_evidence_json text not null default '{}',
                    created_from_update_id integer not null default 0,
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    completed_at text not null default ''
                );
                create index if not exists idx_work_todos_project_status
                    on work_todos(project_id, status);
                create index if not exists idx_work_todos_follow_up
                    on work_todos(status, next_follow_up_at);
                create table if not exists work_todo_dingtalk_links (
                    id integer primary key autoincrement,
                    work_todo_id integer not null,
                    dingtalk_task_id text not null default '',
                    executor_user_id text not null default '',
                    executor_name text not null default '',
                    title_snapshot text not null default '',
                    deadline_at_snapshot text not null default '',
                    priority_snapshot text not null default '',
                    status text not null default 'creating',
                    last_dingtalk_done integer,
                    last_dingtalk_payload_json text not null default '{}',
                    last_pull_at text not null default '',
                    last_push_at text not null default '',
                    last_error text not null default '',
                    retry_count integer not null default 0,
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_work_todo_dingtalk_links_todo
                    on work_todo_dingtalk_links(work_todo_id, status, id);
                create unique index if not exists idx_work_todo_dingtalk_links_task_id
                    on work_todo_dingtalk_links(dingtalk_task_id)
                    where dingtalk_task_id != '';
                create unique index if not exists idx_work_todo_dingtalk_links_active_todo
                    on work_todo_dingtalk_links(work_todo_id)
                    where status in ('creating', 'active');
                create table if not exists task_todo_sync_outbox (
                    id integer primary key autoincrement,
                    operation_key text not null unique,
                    work_todo_id integer not null,
                    operation text not null check(operation in ('create', 'complete')),
                    evidence_json text not null default '{}',
                    status text not null default 'queued'
                        check(status in ('queued', 'running', 'completed', 'failed', 'unknown')),
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
                    receipt_json text not null default '{}',
                    error text not null default '',
                    attempt_count integer not null default 0,
                    next_attempt_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    completed_at text not null default ''
                );
                create index if not exists idx_task_todo_sync_outbox_due
                    on task_todo_sync_outbox(status, lease_expires_at, id);
                create table if not exists work_updates (
                    id integer primary key autoincrement,
                    project_id integer not null,
                    source_type text not null,
                    source_ref text not null,
                    summary text not null,
                    changes_json text not null default '{}',
                    merge_reason text not null default '',
                    confidence real not null default 0,
                    created_at text not null default current_timestamp
                );
                create index if not exists idx_work_updates_project
                    on work_updates(project_id, id);
                create index if not exists idx_work_updates_created
                    on work_updates(created_at, id);
                create table if not exists work_summary_inputs (
                    id integer primary key autoincrement,
                    source_type text not null,
                    source_ref text not null,
                    payload_json text not null,
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    error text not null default '',
                    available_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(source_type, source_ref)
                );
                create index if not exists idx_work_summary_inputs_status
                    on work_summary_inputs(status, id);
                create table if not exists task_agent_runs (
                    id integer primary key autoincrement,
                    summary_input_id integer not null,
                    codex_session_id text not null default '',
                    decision_json text not null default '{}',
                    audit_summary text not null default '',
                    memory_recall_used integer not null default 0,
                    status text not null default 'completed'
                        check(status in ('running', 'completed', 'failed')),
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    finished_at text not null default '',
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_task_agent_runs_input
                    on task_agent_runs(summary_input_id, id);
                create table if not exists weekly_okr_analysis_jobs (
                    id integer primary key autoincrement,
                    week_end text not null,
                    manager_user_id text not null,
                    source_digest text not null,
                    status text not null default 'running'
                        check(status in ('running', 'completed', 'failed')),
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    finished_at text not null default '',
                    updated_at text not null default current_timestamp
                );
                create unique index if not exists idx_weekly_okr_analysis_jobs_identity
                    on weekly_okr_analysis_jobs(
                        week_end, manager_user_id, source_digest
                    );
                create table if not exists follow_up_drafts (
                    id integer primary key autoincrement,
                    project_id integer not null,
                    todo_id integer not null default 0,
                    title text not null default '',
                    description text not null default '',
                    owner_user_id text not null default '',
                    owner_name text not null default '',
                    owners_json text not null default '[]',
                    target_conversation_id text not null default '',
                    target_kind text not null default '',
                    question_text text not null default '',
                    priority text not null default '',
                    tags_json text not null default '[]',
                    participants_json text not null default '[]',
                    files_json text not null default '[]',
                    risk_check_json text not null default '{}',
                    status text not null default 'draft',
                    send_result_json text not null default '{}',
                    evidence_check_json text not null default '{}',
                    reaction_status text not null default '',
                    reaction_summary text not null default '',
                    suppressed_reason text not null default '',
                    dedupe_key text not null default '',
                    scheduled_at text not null default '',
                    sent_at text not null default '',
                    revision integer not null default 1,
                    send_claim_revision integer not null default 0,
                    send_claim_token text not null default '',
                    send_claim_idempotency_uuid text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_follow_up_drafts_status
                    on follow_up_drafts(status, scheduled_at, id);
                create index if not exists idx_follow_up_drafts_owner_sent
                    on follow_up_drafts(owner_user_id, sent_at, id);
                create index if not exists idx_follow_up_drafts_conversation_sent
                    on follow_up_drafts(target_conversation_id, sent_at, id);
                create table if not exists follow_up_send_attempts (
                    id integer primary key autoincrement,
                    draft_id integer not null,
                    draft_revision integer not null,
                    claim_token text not null unique,
                    idempotency_uuid text not null,
                    state text not null default 'claimed',
                    lease_owner text not null default '',
                    claimed_at text not null default '',
                    lease_until text not null default '',
                    result_json text not null default '{}',
                    review_enqueued_revision integer not null default 0,
                    review_source_ref text not null default '',
                    late_result_json text not null default '{}',
                    conflict_json text not null default '{}',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_follow_up_send_attempts_draft_revision
                    on follow_up_send_attempts(draft_id, draft_revision, id);
                create index if not exists idx_follow_up_send_attempts_reconciliation
                    on follow_up_send_attempts(state, lease_until, id);
                create table if not exists daily_scan_state (
                    scanner_name text primary key,
                    last_success_at text not null default '',
                    cursor_json text not null default '{}',
                    last_error text not null default '',
                    updated_at text not null default current_timestamp
                );
                create table if not exists workbench_tasks (
                    id text primary key,
                    title text not null,
                    runtime_kind text not null,
                    provider_session_ref text not null default '',
                    archived_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create table if not exists workbench_turns (
                    id text primary key,
                    task_id text not null,
                    client_request_id text not null unique,
                    task_sequence integer not null check(task_sequence > 0),
                    user_text text not null,
                    status text not null check(status in (
                        'queued', 'running', 'waiting_confirmation',
                        'completed', 'stopped', 'failed'
                    )),
                    stop_requested integer not null default 0
                        check(stop_requested in (0, 1)),
                    final_text text not null default '',
                    error_code text not null default '',
                    error_detail text not null default '',
                    resume_context text not null default '',
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
                    execution_run_id text not null default '',
                    runtime_quiesced_run_id text not null default '',
                    started_at text not null default '',
                    completed_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    foreign key(task_id) references workbench_tasks(id)
                );
                create unique index if not exists idx_workbench_one_active_turn
                    on workbench_turns(task_id)
                    where status in ('queued', 'running', 'waiting_confirmation');
                create index if not exists idx_workbench_turns_queue
                    on workbench_turns(status, created_at, id);
                create index if not exists idx_workbench_turns_recovery
                    on workbench_turns(status, lease_expires_at);
                create table if not exists workbench_events (
                    id integer primary key autoincrement,
                    turn_id text not null,
                    sequence integer not null,
                    event_type text not null,
                    payload_json text not null,
                    created_at text not null default current_timestamp,
                    unique(turn_id, sequence),
                    foreign key(turn_id) references workbench_turns(id)
                );
                create table if not exists workbench_attachments (
                    id text primary key,
                    task_id text not null,
                    client_request_id text not null,
                    filename text not null,
                    media_type text not null,
                    size_bytes integer not null check(size_bytes >= 0),
                    content_sha256 text not null,
                    storage_path text not null,
                    created_at text not null default current_timestamp,
                    foreign key(task_id) references workbench_tasks(id)
                );
                create table if not exists workbench_artifacts (
                    id text primary key,
                    turn_id text not null,
                    label text not null,
                    path text not null,
                    media_type text not null,
                    created_at text not null default current_timestamp,
                    foreign key(turn_id) references workbench_turns(id)
                );
                create table if not exists workbench_confirmations (
                    id text primary key,
                    turn_id text not null,
                    action_kind text not null,
                    target text not null,
                    summary text not null,
                    risk text not null,
                    canonical_capability text not null default '',
                    canonical_operation text not null default '',
                    canonical_targets_json text not null default '[]',
                    canonical_operation_digest text not null default '',
                    canonical_arguments_digest text not null default '',
                    arguments_json text not null,
                    status text not null check(status in (
                        'pending', 'confirmed', 'cancelled', 'executed', 'failed'
                    )),
                    result_json text not null default '',
                    created_at text not null default current_timestamp,
                    decided_at text not null default '',
                    execution_owner text not null default '',
                    execution_lease_expires_at text not null default '',
                    execution_started_at text not null default '',
                    authorization_consumed_at text not null default '',
                    proposer_run_id text not null default '',
                    proposer_owner text not null default '',
                    proposer_lease_expires_at text not null default '',
                    proposer_quiesced_at text not null default '',
                    decision_requested text not null default ''
                        check(decision_requested in ('', 'confirm', 'cancel')),
                    decision_requested_at text not null default '',
                    foreign key(turn_id) references workbench_turns(id)
                );
                """
            )
            workbench_turn_columns = {
                row["name"]
                for row in db.execute("pragma table_info(workbench_turns)").fetchall()
            }
            if "resume_context" not in workbench_turn_columns:
                db.execute(
                    "alter table workbench_turns add column "
                    "resume_context text not null default ''"
                )
            if "task_sequence" not in workbench_turn_columns:
                db.execute(
                    "alter table workbench_turns add column "
                    "task_sequence integer not null default 0"
                )
            db.execute(
                """
                with ranked as (
                    select rowid as row_id,
                           row_number() over (
                               partition by task_id order by created_at,rowid
                           ) as sequence
                    from workbench_turns
                )
                update workbench_turns
                set task_sequence=(
                    select sequence from ranked
                    where ranked.row_id=workbench_turns.rowid
                )
                where task_sequence=0
                """
            )
            for column in ("execution_run_id", "runtime_quiesced_run_id"):
                if column not in workbench_turn_columns:
                    db.execute(
                        "alter table workbench_turns add column "
                        f"{column} text not null default ''"
                    )
            workbench_confirmation_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(workbench_confirmations)"
                ).fetchall()
            }
            for column in (
                "execution_owner",
                "execution_lease_expires_at",
                "execution_started_at",
                "canonical_capability",
                "canonical_operation",
                "canonical_targets_json",
                "canonical_operation_digest",
                "canonical_arguments_digest",
                "authorization_consumed_at",
                "proposer_run_id",
                "proposer_owner",
                "proposer_lease_expires_at",
                "proposer_quiesced_at",
                "decision_requested",
                "decision_requested_at",
            ):
                if column not in workbench_confirmation_columns:
                    default = "'[]'" if column == "canonical_targets_json" else "''"
                    db.execute(
                        "alter table workbench_confirmations add column "
                        f"{column} text not null default {default}"
                    )
            workbench_attachment_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(workbench_attachments)"
                ).fetchall()
            }
            if "client_request_id" not in workbench_attachment_columns:
                db.execute(
                    "alter table workbench_attachments add column "
                    "client_request_id text not null default ''"
                )
            if "content_sha256" not in workbench_attachment_columns:
                db.execute(
                    "alter table workbench_attachments add column "
                    "content_sha256 text not null default ''"
                )
            db.execute(
                "update workbench_attachments set client_request_id=id "
                "where client_request_id=''"
            )
            db.execute(
                "create index if not exists idx_workbench_turns_queue "
                "on workbench_turns(status, created_at, id)"
            )
            db.execute(
                "create index if not exists idx_workbench_confirmations_turn_status "
                "on workbench_confirmations(turn_id, status, result_json, created_at, id)"
            )
            db.execute(
                "create index if not exists idx_workbench_turns_recovery "
                "on workbench_turns(status, lease_expires_at)"
            )
            db.execute(
                "create index if not exists idx_workbench_events_turn_id_id "
                "on workbench_events(turn_id, id)"
            )
            db.execute(
                "create index if not exists idx_workbench_artifacts_turn_created_id "
                "on workbench_artifacts(turn_id, created_at, id)"
            )
            db.execute(
                "create index if not exists idx_workbench_turns_task_created_id "
                "on workbench_turns(task_id, created_at, id)"
            )
            db.execute(
                "create unique index if not exists idx_workbench_turns_task_sequence "
                "on workbench_turns(task_id, task_sequence desc)"
            )
            db.execute(
                "create index if not exists idx_workbench_tasks_updated_id "
                "on workbench_tasks(updated_at, id)"
            )
            db.execute(
                "create index if not exists idx_workbench_events_id_turn_id "
                "on workbench_events(id, turn_id)"
            )
            db.execute(
                "create index if not exists idx_workbench_artifacts_created_id_turn "
                "on workbench_artifacts(created_at, id, turn_id)"
            )
            db.execute(
                "create index if not exists idx_workbench_confirmations_created_id_turn "
                "on workbench_confirmations(created_at, id, turn_id)"
            )
            db.execute(
                "create index if not exists idx_workbench_confirmations_turn_created_id "
                "on workbench_confirmations(turn_id, created_at, id)"
            )
            db.execute(
                "create index if not exists idx_workbench_attachments_task_created_id "
                "on workbench_attachments(task_id, created_at, id)"
            )
            db.execute(
                "create unique index if not exists idx_workbench_attachments_task_request "
                "on workbench_attachments(task_id, client_request_id)"
            )
            db.execute(
                "create index if not exists idx_workbench_events_event_type "
                "on workbench_events(event_type)"
            )
            db.execute("drop index if exists idx_workbench_confirmations_recovery")
            db.execute(
                """
                create index idx_workbench_confirmations_recovery
                on workbench_confirmations(execution_lease_expires_at, id)
                where status='confirmed' and result_json=''
                  and execution_owner<>'' and execution_lease_expires_at<>''
                """
            )
            db.execute(
                """
                create index if not exists idx_workbench_confirmations_ready_intents
                on workbench_confirmations(decision_requested_at, id)
                where status='pending' and decision_requested<>''
                  and proposer_run_id<>'' and proposer_quiesced_at<>''
                """
            )
            db.execute(
                """
                create index if not exists idx_workbench_confirmations_proposer_recovery
                on workbench_confirmations(proposer_lease_expires_at, id)
                where status='pending' and proposer_run_id<>''
                  and proposer_quiesced_at=''
                """
            )
            db.execute(
                """
                create index if not exists
                    idx_workbench_confirmations_legacy_proposer_recovery
                on workbench_confirmations(id)
                where status='pending' and proposer_run_id=''
                """
            )
            db.execute(
                """
                create index if not exists
                    idx_workbench_confirmations_legacy_execution_owner_recovery
                on workbench_confirmations(id)
                where status='confirmed' and result_json=''
                  and execution_owner=''
                """
            )
            db.execute(
                """
                create index if not exists
                    idx_workbench_confirmations_legacy_execution_lease_recovery
                on workbench_confirmations(id)
                where status='confirmed' and result_json=''
                  and execution_owner<>'' and execution_lease_expires_at=''
                """
            )
            self._reconcile_legacy_workbench_confirmations(db)
            reply_task_columns = {
                row["name"]
                for row in db.execute("pragma table_info(reply_tasks)").fetchall()
            }
            for column, definition in (
                ("trigger_message_json", "text not null default '{}'"),
                ("available_at", "text not null default ''"),
                ("force_new_decision", "integer not null default 0"),
                ("oa_url", "text not null default ''"),
                ("manual_rerun_attempt_id", "integer not null default 0"),
                ("manual_rerun_revision_key", "text not null default ''"),
                ("channel", "text not null default 'dingtalk'"),
                ("recovery_code", "text not null default ''"),
            ):
                if column not in reply_task_columns:
                    db.execute(
                        f"alter table reply_tasks add column {column} {definition}"
                    )
            agent_run_columns = {
                row["name"]
                for row in db.execute("pragma table_info(agent_runs)").fetchall()
            }
            for column, definition in (
                ("reconciliation_attempts", "integer not null default 0"),
                ("reconciliation_next_attempt_at", "text not null default ''"),
                ("reconciliation_suspended", "integer not null default 0"),
            ):
                if column not in agent_run_columns:
                    db.execute(
                        f"alter table agent_runs add column {column} {definition}"
                    )
            self._migrate_runtime_attempt_session_evidence(db)
            self._migrate_runtime_attempt_execution_state(db)
            self._migrate_agent_run_turn_identity(db)
            agent_run_columns = {
                row["name"]
                for row in db.execute("pragma table_info(agent_runs)").fetchall()
            }
            for column in (
                "effect_started_count",
                "effect_completed_count",
                "effect_failed_count",
                "effect_receipt_count",
                "effect_unreviewed_count",
                "reconciliation_event_count",
            ):
                if column not in agent_run_columns:
                    db.execute(
                        f"alter table agent_runs add column {column} "
                        "integer not null default 0"
                    )
            db.execute(
                "create index if not exists idx_agent_runs_reconciliation_due "
                "on agent_runs(status, reconciliation_next_attempt_at, id)"
            )
            agent_run_event_columns = {
                row["name"]
                for row in db.execute("pragma table_info(agent_run_events)").fetchall()
            }
            if "event_scope" not in agent_run_event_columns:
                db.execute(
                    "alter table agent_run_events add column "
                    "event_scope text not null default 'direct'"
                )
            if "reconciliation_event_count" not in agent_run_columns:
                db.execute(
                    "update agent_runs set reconciliation_event_count=("
                    "select count(*) from agent_run_events "
                    "where agent_run_id=agent_runs.id "
                    "and event_scope='reconciliation')"
                )
            db.execute(
                "create index if not exists idx_agent_run_events_run_scope "
                "on agent_run_events(agent_run_id, event_scope)"
            )
            receipt_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(agent_execution_receipts)"
                ).fetchall()
            }
            if "effect_counted" not in receipt_columns:
                db.execute(
                    "alter table agent_execution_receipts add column "
                    "effect_counted integer not null default 0"
                )
            self._migrate_reply_task_channel_identity(db)
            db.execute(
                """
                create index if not exists idx_reply_tasks_channel_status_id
                    on reply_tasks(channel, status, id)
                """
            )
            sent_reply_columns = {
                row["name"]
                for row in db.execute("pragma table_info(sent_replies)").fetchall()
            }
            for column, definition in (
                ("send_result_json", "text not null default ''"),
                ("recall_key", "text not null default ''"),
                ("recall_status", "text not null default ''"),
                ("recall_error", "text not null default ''"),
                ("recalled_at", "text"),
                ("feedback_token", "text not null default ''"),
            ):
                if column not in sent_reply_columns:
                    try:
                        db.execute(
                            f"alter table sent_replies add column {column} {definition}"
                        )
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc):
                            raise
            feedback_event_columns = {
                row["name"]
                for row in db.execute("pragma table_info(feedback_events)").fetchall()
            }
            for column, definition in (
                ("resolved_at", "text not null default ''"),
            ):
                if column not in feedback_event_columns:
                    db.execute(
                        f"alter table feedback_events add column {column} {definition}"
                    )

            db.execute(
                """
                create table if not exists service_bugfix_candidates (
                    id integer primary key autoincrement,
                    feedback_event_key text not null unique,
                    feedback_token text not null default '',
                    attempt_id integer not null default 0,
                    status text not null default 'pending',
                    title text not null,
                    reason text not null,
                    feedback_comment text not null,
                    conversation_title text not null default '',
                    trigger_text text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                )
                """
            )
            db.execute(
                """
                create index if not exists idx_service_bugfix_candidates_status
                on service_bugfix_candidates(status, created_at)
                """
            )

            conversation_columns = {
                row["name"]
                for row in db.execute("pragma table_info(conversations)").fetchall()
            }
            if "codex_session_contract_hash" not in conversation_columns:
                db.execute(
                    "alter table conversations add column "
                    "codex_session_contract_hash text not null default ''"
                )

            reply_attempt_columns = {
                row["name"]
                for row in db.execute("pragma table_info(reply_attempts)").fetchall()
            }
            for column, definition in (
                ("agent_run_id", "integer"),
                ("codex_session_id", "text not null default ''"),
                ("direct_user_id", "text not null default ''"),
                ("direct_open_dingtalk_id", "text not null default ''"),
                ("codex_transcript_start_line", "integer not null default 0"),
                ("codex_transcript_end_line", "integer not null default 0"),
                ("audit_documents_json", "text not null default '[]'"),
                ("audit_tool_events_json", "text not null default '[]'"),
                ("audit_summary", "text not null default ''"),
                ("human_decision_options_json", "text not null default '[]'"),
                ("oa_process_instance_id", "text not null default ''"),
                ("oa_task_id", "text not null default ''"),
                ("oa_url", "text not null default ''"),
                ("oa_action", "text not null default ''"),
                ("oa_remark", "text not null default ''"),
                ("oa_action_result_json", "text not null default ''"),
                ("calendar_event_id", "text not null default ''"),
                ("calendar_response_status", "text not null default ''"),
                ("calendar_response_result_json", "text not null default ''"),
                ("mail_mailbox", "text not null default ''"),
                ("mail_message_id", "text not null default ''"),
                ("mail_subject", "text not null default ''"),
                ("mail_reply_text", "text not null default ''"),
                ("mail_action_result_json", "text not null default ''"),
                ("reaction_action_result_json", "text not null default ''"),
                ("document_action_result_json", "text not null default ''"),
                ("feedback_scope", "text not null default 'one_time'"),
                ("skill_update_requested", "integer not null default 0"),
                ("skill_update_receipts_json", "text not null default '[]'"),
            ):
                if column not in reply_attempt_columns:
                    try:
                        db.execute(
                            f"alter table reply_attempts add column {column} {definition}"
                        )
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc):
                            raise
            error_columns = {
                row["name"]
                for row in db.execute("pragma table_info(errors)").fetchall()
            }
            for column, definition in (
                ("resolved_at", "text not null default ''"),
                ("resolution", "text not null default ''"),
            ):
                if column not in error_columns:
                    db.execute(f"alter table errors add column {column} {definition}")
            db.execute(
                """
                update reply_attempts
                set codex_session_id=coalesce((
                    select conversations.codex_session_id
                    from conversations
                    where conversations.conversation_id=reply_attempts.conversation_id
                ), '')
                where codex_session_id=''
                """
            )
            db.execute(
                """
                update reply_attempts
                set send_status='failed'
                where send_status='needs_authorization'
                """
            )
            reply_task_columns = {
                row["name"]
                for row in db.execute("pragma table_info(reply_tasks)").fetchall()
            }
            for column, definition in (
                ("trigger_message_json", "text not null default '{}'"),
                ("available_at", "text not null default ''"),
                ("force_new_decision", "integer not null default 0"),
                ("oa_url", "text not null default ''"),
                ("manual_rerun_attempt_id", "integer not null default 0"),
                ("manual_rerun_revision_key", "text not null default ''"),
                ("channel", "text not null default 'dingtalk'"),
                ("execution_generation", "text not null default 'initial'"),
                ("recovery_code", "text not null default ''"),
            ):
                if column not in reply_task_columns:
                    db.execute(
                        f"alter table reply_tasks add column {column} {definition}"
                    )
            for table_name in ("reply_attempts", "sent_replies"):
                existing = {
                    row["name"]
                    for row in db.execute(f"pragma table_info({table_name})").fetchall()
                }
                if "channel" not in existing:
                    db.execute(
                        f"alter table {table_name} add column channel "
                        f"text not null default 'dingtalk'"
                    )
            work_summary_input_columns = {
                row["name"]
                for row in db.execute("pragma table_info(work_summary_inputs)").fetchall()
            }
            for column, definition in (
                ("available_at", "text not null default ''"),
            ):
                if column not in work_summary_input_columns:
                    db.execute(
                        f"alter table work_summary_inputs add column {column} {definition}"
                    )
            work_todo_columns = {
                row["name"]
                for row in db.execute("pragma table_info(work_todos)").fetchall()
            }
            for column, definition in (
                ("description", "text not null default ''"),
                ("owner_evidence_json", "text not null default '{}'"),
            ):
                if column not in work_todo_columns:
                    db.execute(
                        f"alter table work_todos add column {column} {definition}"
                    )
            work_project_columns = {
                row["name"]
                for row in db.execute("pragma table_info(work_projects)").fetchall()
            }
            if "owner_evidence_json" not in work_project_columns:
                db.execute(
                    "alter table work_projects add column "
                    "owner_evidence_json text not null default '{}'"
                )
            work_todo_dingtalk_link_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(work_todo_dingtalk_links)"
                ).fetchall()
            }
            if "retry_count" not in work_todo_dingtalk_link_columns:
                db.execute(
                    "alter table work_todo_dingtalk_links add column "
                    "retry_count integer not null default 0"
                )
            follow_up_draft_columns = {
                row["name"]
                for row in db.execute("pragma table_info(follow_up_drafts)").fetchall()
            }
            for column, definition in (
                ("title", "text not null default ''"),
                ("description", "text not null default ''"),
                ("owners_json", "text not null default '[]'"),
                ("priority", "text not null default ''"),
                ("tags_json", "text not null default '[]'"),
                ("participants_json", "text not null default '[]'"),
                ("files_json", "text not null default '[]'"),
                ("evidence_check_json", "text not null default '{}'"),
                ("reaction_status", "text not null default ''"),
                ("reaction_summary", "text not null default ''"),
                ("suppressed_reason", "text not null default ''"),
                ("dedupe_key", "text not null default ''"),
                ("updated_at", "text not null default ''"),
                ("revision", "integer not null default 1"),
                ("send_claim_revision", "integer not null default 0"),
                ("send_claim_token", "text not null default ''"),
                (
                    "send_claim_idempotency_uuid",
                    "text not null default ''",
                ),
            ):
                if column not in follow_up_draft_columns:
                    db.execute(
                        f"alter table follow_up_drafts add column {column} {definition}"
                    )
            follow_up_send_attempt_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(follow_up_send_attempts)"
                ).fetchall()
            }
            for column, definition in (
                ("lease_owner", "text not null default ''"),
                ("claimed_at", "text not null default ''"),
                ("lease_until", "text not null default ''"),
                ("review_enqueued_revision", "integer not null default 0"),
                ("review_source_ref", "text not null default ''"),
                ("late_result_json", "text not null default '{}'"),
                ("conflict_json", "text not null default '{}'"),
            ):
                if column not in follow_up_send_attempt_columns:
                    db.execute(
                        "alter table follow_up_send_attempts "
                        f"add column {column} {definition}"
                    )
            db.execute(
                """
                create index if not exists idx_follow_up_drafts_owner_sent
                    on follow_up_drafts(owner_user_id, sent_at, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_follow_up_drafts_conversation_sent
                    on follow_up_drafts(target_conversation_id, sent_at, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_follow_up_send_attempts_reconciliation
                    on follow_up_send_attempts(state, lease_until, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_follow_up_drafts_history_updated
                    on follow_up_drafts(updated_at, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_reply_attempts_created
                    on reply_attempts(created_at, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_reply_attempts_oa_history
                    on reply_attempts(
                        oa_process_instance_id, created_at desc, id desc
                    )
                    where oa_process_instance_id <> ''
                """
            )
            db.execute(
                """
                create index if not exists idx_reply_attempts_trigger_history
                    on reply_attempts(
                        conversation_id, trigger_message_id, action, id desc
                    )
                """
            )
            db.execute(
                """
                create index if not exists idx_reply_attempts_current_trigger
                    on reply_attempts(
                        channel, conversation_id, trigger_message_id,
                        updated_at desc, id desc
                    )
                """
            )
            db.execute(
                """
                create index if not exists idx_reply_attempts_agent_run_recovery
                    on reply_attempts(agent_run_id, send_error, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_sent_replies_history
                    on sent_replies(
                        conversation_id, trigger_message_id, sent_at
                    )
                """
            )
            db.execute(
                """
                create index if not exists idx_meeting_alignment_runs_created
                    on meeting_alignment_runs(created_at, id)
                """
            )
            meeting_run_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(meeting_alignment_runs)"
                ).fetchall()
            }
            for column, definition in (
                ("finished_at", "text not null default ''"),
                ("updated_at", "text not null default ''"),
            ):
                if column not in meeting_run_columns:
                    db.execute(
                        "alter table meeting_alignment_runs "
                        f"add column {column} {definition}"
                    )
            db.execute(
                "update meeting_alignment_runs set updated_at=created_at "
                "where updated_at=''"
            )
            db.execute(
                "update meeting_alignment_runs set finished_at=created_at "
                "where status<>'running' and finished_at=''"
            )
            running_rows = db.execute(
                "select id, job_id from meeting_alignment_runs "
                "where status='running' "
                "order by job_id, datetime(created_at) desc, id desc"
            ).fetchall()
            newest_running_job_ids: set[int] = set()
            for row in running_rows:
                job_id = int(row["job_id"])
                if job_id not in newest_running_job_ids:
                    newest_running_job_ids.add(job_id)
                    continue
                db.execute(
                    "update meeting_alignment_runs set status='failed', error=?, "
                    "finished_at=case when created_at<>'' then created_at "
                    "else current_timestamp end, "
                    "updated_at=case when created_at<>'' then created_at "
                    "else current_timestamp end "
                    "where id=? and status='running'",
                    (
                        MEETING_ALIGNMENT_DUPLICATE_RUNNING_MIGRATION_ERROR,
                        int(row["id"]),
                    ),
                )
            db.execute(
                "create unique index if not exists "
                "idx_meeting_alignment_runs_active_job "
                "on meeting_alignment_runs(job_id) where status='running'"
            )
            db.execute(
                """
                create index if not exists idx_work_updates_created
                    on work_updates(created_at, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_work_summary_inputs_updated
                    on work_summary_inputs(updated_at desc, id desc)
                """
            )
            task_run_columns = {
                row["name"]
                for row in db.execute("pragma table_info(task_agent_runs)").fetchall()
            }
            for column, definition in (
                ("status", "text not null default 'completed'"),
                ("error", "text not null default ''"),
                ("finished_at", "text not null default ''"),
                ("updated_at", "text not null default ''"),
            ):
                if column not in task_run_columns:
                    db.execute(
                        f"alter table task_agent_runs add column {column} {definition}"
                    )
            db.execute(
                "update task_agent_runs set status='completed' "
                "where status is null or status=''"
            )
            db.execute(
                "update task_agent_runs set finished_at=created_at "
                "where status<>'running' and finished_at=''"
            )
            db.execute(
                "update task_agent_runs set updated_at=created_at where updated_at=''"
            )
            db.execute(
                "create unique index if not exists idx_task_agent_runs_active_input "
                "on task_agent_runs(summary_input_id) where status='running'"
            )
            db.execute(
                """
                create table if not exists weekly_okr_analysis_jobs (
                    id integer primary key autoincrement,
                    week_end text not null,
                    manager_user_id text not null,
                    source_digest text not null,
                    status text not null default 'running'
                        check(status in ('running', 'completed', 'failed')),
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    finished_at text not null default '',
                    updated_at text not null default current_timestamp
                )
                """
            )
            db.execute(
                "create unique index if not exists "
                "idx_weekly_okr_analysis_jobs_identity "
                "on weekly_okr_analysis_jobs(week_end, manager_user_id, source_digest)"
            )
            weekly_job_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(weekly_okr_analysis_jobs)"
                ).fetchall()
            }
            for column in ("lease_owner", "lease_expires_at"):
                if column not in weekly_job_columns:
                    db.execute(
                        f"alter table weekly_okr_analysis_jobs add column {column} "
                        "text not null default ''"
                    )
            outbox_columns = {
                row["name"]
                for row in db.execute("pragma table_info(task_todo_sync_outbox)").fetchall()
            }
            for column, definition in (
                ("attempt_count", "integer not null default 0"),
                ("next_attempt_at", "text not null default ''"),
            ):
                if column not in outbox_columns:
                    db.execute(f"alter table task_todo_sync_outbox add column {column} {definition}")
            org_user_profile_columns = {
                row["name"]
                for row in db.execute("pragma table_info(org_user_profiles)").fetchall()
            }
            for column, definition in (
                ("title", "text not null default ''"),
                ("manager_name", "text not null default ''"),
                ("department_names_json", "text not null default '[]'"),
                ("org_labels_json", "text not null default '[]'"),
                ("has_subordinate", "integer"),
            ):
                if column not in org_user_profile_columns:
                    db.execute(
                        f"alter table org_user_profiles add column {column} {definition}"
                    )
            wechat_memory_columns = {
                row["name"] for row in db.execute(
                    "pragma table_info(wechat_memory_candidates)"
                ).fetchall()
            }
            if "memory_write_error" not in wechat_memory_columns:
                db.execute(
                    "alter table wechat_memory_candidates add column "
                    "memory_write_error text not null default ''"
                )
            db.execute(
                """
                create table if not exists wechat_memory_import_jobs (
                    id integer primary key autoincrement,
                    import_run_id text not null,
                    account_id text not null,
                    status text not null default 'running'
                        check(status in ('running', 'completed', 'failed')),
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    finished_at text not null default '',
                    updated_at text not null default current_timestamp
                )
                """
            )
            db.execute(
                "create index if not exists idx_wechat_memory_import_jobs_status "
                "on wechat_memory_import_jobs(status, id)"
            )
            wechat_delivery_columns = {
                row["name"]
                for row in db.execute("pragma table_info(wechat_deliveries)").fetchall()
            }
            if "execution_generation" not in wechat_delivery_columns:
                db.execute(
                    "alter table wechat_deliveries add column "
                    "execution_generation text not null default 'initial'"
                )
            if "pre_action_failure" not in wechat_delivery_columns:
                db.execute(
                    "alter table wechat_deliveries add column "
                    "pre_action_failure integer not null default 0"
                )
            error_columns = {
                row["name"] for row in db.execute("pragma table_info(errors)").fetchall()
            }
            if "resolved_at" not in error_columns:
                db.execute(
                    "alter table errors add column resolved_at text not null default ''"
                )
            if "resolution" not in error_columns:
                db.execute(
                    "alter table errors add column resolution text not null default ''"
                )
            self._migrate_removed_runtime(db)
            self._migrate_agent_run_events(db)
            self._backfill_agent_run_effect_counters(db)
            runtime_session_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(conversation_runtime_sessions)"
                ).fetchall()
            }
            if "contract_hash" not in runtime_session_columns:
                db.execute(
                    "alter table conversation_runtime_sessions add column "
                    "contract_hash text not null default ''"
                )
            db.execute(
                """
                insert or ignore into conversation_runtime_sessions (
                    conversation_id, route_name, session_id, contract_hash
                )
                select conversation_id, 'codex_oauth', codex_session_id,
                       coalesce(codex_session_contract_hash, '')
                from conversations
                where codex_session_id is not null and codex_session_id <> ''
                """
            )

    @staticmethod
    def _migrate_runtime_attempt_session_evidence(db: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in db.execute(
                "pragma table_info(agent_runtime_attempts)"
            ).fetchall()
        }
        if "session_mode" not in columns:
            db.execute(
                "alter table agent_runtime_attempts add column "
                "session_mode text not null default 'fresh' "
                "check(session_mode in ('fresh', 'resume'))"
            )
        if "source_session_id" not in columns:
            db.execute(
                "alter table agent_runtime_attempts add column "
                "source_session_id text not null default ''"
            )
        db.execute(
            "update agent_runtime_attempts set session_mode='fresh', "
            "source_session_id='' where session_mode is null "
            "or source_session_id is null "
            "or session_mode not in ('fresh', 'resume') "
            "or (session_mode='fresh' and source_session_id<>'') "
            "or (session_mode='resume' and trim(source_session_id)='')"
        )
        db.execute(
            "drop trigger if exists trg_runtime_attempt_session_evidence_insert"
        )
        db.execute(
            "drop trigger if exists trg_runtime_attempt_session_evidence_update"
        )
        db.execute(
            "drop trigger if exists trg_runtime_attempt_session_evidence_trim_insert"
        )
        db.execute(
            "drop trigger if exists trg_runtime_attempt_session_evidence_trim_update"
        )
        db.execute(
            """
            create trigger trg_runtime_attempt_session_evidence_trim_insert
            before insert on agent_runtime_attempts
            when new.session_mode is null or new.source_session_id is null or not (
                (new.session_mode='fresh' and new.source_session_id='')
                or (new.session_mode='resume' and trim(new.source_session_id)<>'')
            )
            begin
                select raise(abort, 'invalid runtime attempt session evidence');
            end
            """
        )

        db.execute(
            """
            create trigger trg_runtime_attempt_session_evidence_trim_update
            before update of session_mode, source_session_id on agent_runtime_attempts
            when new.session_mode is null or new.source_session_id is null or not (
                (new.session_mode='fresh' and new.source_session_id='')
                or (new.session_mode='resume' and trim(new.source_session_id)<>'')
            )
            begin
                select raise(abort, 'invalid runtime attempt session evidence');
            end
            """
        )

    @staticmethod
    def _migrate_runtime_attempt_execution_state(db: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in db.execute(
                "pragma table_info(agent_runtime_attempts)"
            ).fetchall()
        }
        for column in (
            "lease_owner",
            "lease_expires_at",
            "result_schema_id",
            "result_envelope_json",
        ):
            if column not in columns:
                db.execute(
                    f"alter table agent_runtime_attempts add column "
                    f"{column} text not null default ''"
                )
        lineage_columns = {
            "attempt_purpose": "text not null default 'normal'",
            "validation_retry_policy_id": "text not null default ''",
            "validation_result_schema_id": "text not null default ''",
        }
        for column, definition in lineage_columns.items():
            if column not in columns:
                db.execute(
                    f"alter table agent_runtime_attempts add column "
                    f"{column} {definition}"
                )
        db.execute(
            "update agent_runtime_attempts set attempt_purpose='normal', "
            "validation_retry_policy_id='', validation_result_schema_id='' "
            "where attempt_purpose not in "
            "('normal', 'result_validation_correction') "
            "or (attempt_purpose='normal' and "
            "(validation_retry_policy_id<>'' or validation_result_schema_id<>'')) "
            "or (attempt_purpose='result_validation_correction' and "
            "(trim(validation_retry_policy_id)='' "
            "or trim(validation_result_schema_id)=''))"
        )
        db.execute("drop trigger if exists trg_runtime_attempt_lineage_insert")
        db.execute("drop trigger if exists trg_runtime_attempt_lineage_update")
        db.execute("drop trigger if exists trg_runtime_attempt_lineage_immutable")
        lineage_invalid = """
            new.attempt_purpose not in ('normal', 'result_validation_correction')
            or (new.attempt_purpose='normal' and
                (new.validation_retry_policy_id<>''
                 or new.validation_result_schema_id<>''))
            or (new.attempt_purpose='result_validation_correction' and
                (trim(new.validation_retry_policy_id)=''
                 or trim(new.validation_result_schema_id)=''))
        """
        db.execute(
            f"""
            create trigger trg_runtime_attempt_lineage_insert
            before insert on agent_runtime_attempts
            when {lineage_invalid}
            begin
                select raise(abort, 'invalid runtime attempt correction lineage');
            end
            """
        )
        db.execute(
            f"""
            create trigger trg_runtime_attempt_lineage_update
            before update of attempt_purpose, validation_retry_policy_id,
                             validation_result_schema_id
            on agent_runtime_attempts
            when {lineage_invalid}
            begin
                select raise(abort, 'invalid runtime attempt correction lineage');
            end
            """
        )
        db.execute(
            """
            create trigger trg_runtime_attempt_lineage_immutable
            before update of attempt_purpose, validation_retry_policy_id,
                             validation_result_schema_id
            on agent_runtime_attempts
            when new.attempt_purpose<>old.attempt_purpose
              or new.validation_retry_policy_id<>old.validation_retry_policy_id
              or new.validation_result_schema_id<>old.validation_result_schema_id
            begin
                select raise(abort, 'runtime attempt correction lineage is immutable');
            end
            """
        )
        db.execute(
            """
            create index if not exists idx_runtime_attempt_active_lease
            on agent_runtime_attempts(status, lease_expires_at)
            where agent_run_id is null and status in ('starting', 'running')
            """
        )
        db.execute("drop trigger if exists trg_runtime_attempt_generalized_lease_insert")
        db.execute("drop trigger if exists trg_runtime_attempt_generalized_lease_update")
        db.execute(
            """
            create trigger trg_runtime_attempt_generalized_lease_insert
            before insert on agent_runtime_attempts
            when new.agent_run_id is null
              and new.status in ('starting', 'running')
              and (trim(new.lease_owner)='' or trim(new.lease_expires_at)='')
            begin
                select raise(abort, 'active generalized attempt requires lease');
            end
            """
        )
        db.execute(
            """
            create trigger trg_runtime_attempt_generalized_lease_update
            before update of status, lease_owner, lease_expires_at
            on agent_runtime_attempts
            when new.agent_run_id is null
              and new.status in ('starting', 'running')
              and (trim(new.lease_owner)='' or trim(new.lease_expires_at)='')
            begin
                select raise(abort, 'active generalized attempt requires lease');
            end
            """
        )

    @staticmethod
    def _reconcile_legacy_workbench_confirmations(db: sqlite3.Connection) -> None:
        rows = db.execute(
            """
            select id, turn_id from workbench_confirmations
            where status='pending' and proposer_run_id=''
            order by turn_id, created_at, id
            """
        ).fetchall()
        if not rows:
            return
        result_json = json.dumps(
            {
                "code": "legacy_proposer_state_unknown",
                "retryable": False,
                "status": "failed",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        affected_turn_ids: list[str] = []
        for row in rows:
            changed = db.execute(
                """
                update workbench_confirmations
                set status='failed', result_json=?,
                    decided_at=case when decided_at='' then current_timestamp else decided_at end,
                    execution_owner='', execution_lease_expires_at='',
                    execution_started_at='', authorization_consumed_at='',
                    proposer_owner='', proposer_lease_expires_at='',
                    proposer_quiesced_at='', decision_requested='',
                    decision_requested_at=''
                where id=? and status='pending' and proposer_run_id=''
                """,
                (result_json, row["id"]),
            ).rowcount
            if changed == 1 and row["turn_id"] not in affected_turn_ids:
                affected_turn_ids.append(row["turn_id"])
        for turn_id in affected_turn_ids:
            turn = db.execute(
                "select status from workbench_turns where id=?", (turn_id,)
            ).fetchone()
            if turn is None or turn["status"] not in {
                "queued",
                "running",
                "waiting_confirmation",
            }:
                continue
            sequence = int(
                db.execute(
                    "select coalesce(max(sequence), 0) + 1 from workbench_events where turn_id=?",
                    (turn_id,),
                ).fetchone()[0]
            )
            db.execute(
                """
                insert into workbench_events (
                    turn_id, sequence, event_type, payload_json
                ) values (?, ?, 'turn_failed', ?)
                """,
                (
                    turn_id,
                    sequence,
                    json.dumps(
                        {
                            "code": "legacy_proposer_state_unknown",
                            "status": "failed",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            db.execute(
                """
                update workbench_turns
                set status='failed', lease_owner='', lease_expires_at='',
                    error_code='legacy_proposer_state_unknown',
                    error_detail='Legacy confirmation proposer state is unknown.',
                    completed_at=case when completed_at='' then current_timestamp else completed_at end,
                    updated_at=current_timestamp
                where id=? and status in ('queued', 'running', 'waiting_confirmation')
                """,
                (turn_id,),
            )

    @staticmethod
    @contextmanager
    def _foreign_key_rebuild(
        db: sqlite3.Connection,
        *,
        migration_name: str,
    ) -> Iterator[None]:
        """Run a table rebuild with foreign keys verifiably disabled."""
        if db.in_transaction:
            db.commit()
        if db.in_transaction:
            raise sqlite3.IntegrityError(
                f"{migration_name} migration could not finish prior transaction"
            )
        try:
            db.execute("pragma foreign_keys=off")
            if db.execute("pragma foreign_keys").fetchone()[0] != 0:
                raise sqlite3.IntegrityError(
                    f"{migration_name} migration could not disable foreign keys"
                )
            yield
            if not db.in_transaction:
                raise sqlite3.IntegrityError(
                    f"{migration_name} migration transaction is missing"
                )
            violations = db.execute("pragma foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    f"{migration_name} migration broke foreign keys"
                )
            db.commit()
            violations = db.execute("pragma foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    f"{migration_name} migration broke foreign keys"
                )
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            if db.in_transaction:
                db.rollback()
            db.execute("pragma foreign_keys=on")
            if db.execute("pragma foreign_keys").fetchone()[0] != 1:
                raise sqlite3.IntegrityError(
                    f"{migration_name} migration could not restore foreign keys"
                )

    @staticmethod
    def _migrate_agent_run_turn_identity(db: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in db.execute("pragma table_info(agent_runs)").fetchall()
        }
        unique_columns = {
            tuple(
                row["name"]
                for row in db.execute(
                    "select name from pragma_index_info(?) order by seqno",
                    (index["name"],),
                ).fetchall()
            )
            for index in db.execute("pragma index_list(agent_runs)").fetchall()
            if index["unique"]
        }
        desired_identity = (
            "reply_task_id",
            "execution_generation",
            "role",
            "proposal_revision",
            "turn_attempt",
        )
        required_columns = {
            "role",
            "proposal_revision",
            "turn_attempt",
            "parent_agent_run_id",
            "operation_id",
        }
        if required_columns <= columns and desired_identity in unique_columns:
            return
        existing_turn_columns = required_columns & columns
        if existing_turn_columns and existing_turn_columns != required_columns:
            raise sqlite3.IntegrityError("agent_runs has a partial turn identity schema")
        preserve_turn_identity = required_columns <= columns
        identity_select = (
            "role, proposal_revision, turn_attempt, "
            "parent_agent_run_id, operation_id"
            if preserve_turn_identity
            else "'audit', 0, 0, null, ''"
        )

        with AutoReplyStore._foreign_key_rebuild(
            db,
            migration_name="agent_runs",
        ):
            db.executescript(
                f"""
                begin immediate;
                create table agent_runs_turn_migration (
                    id integer primary key autoincrement,
                    reply_task_id integer not null,
                    execution_generation text not null,
                    role text not null check(role in ('consumer', 'audit')),
                    proposal_revision integer not null default 0
                        check(proposal_revision >= 0),
                    turn_attempt integer not null default 0
                        check(turn_attempt >= 0),
                    parent_agent_run_id integer,
                    operation_id text not null default '',
                    status text not null default 'pending'
                        check(status in (
                            'pending', 'running', 'completed', 'failed', 'unknown'
                        )),
                    codex_session_id text not null default '',
                    transcript_start_line integer not null default 0,
                    transcript_end_line integer not null default 0,
                    final_result_json text not null default '',
                    structured_error_json text not null default '',
                    tool_events_json text not null default '[]',
                    side_effect_state text not null default 'none'
                        check(side_effect_state in ('none', 'confirmed', 'unknown')),
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
                    reconciliation_attempts integer not null default 0,
                    reconciliation_next_attempt_at text not null default '',
                    reconciliation_suspended integer not null default 0,
                    started_at text not null default '',
                    completed_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(
                        reply_task_id, execution_generation, role,
                        proposal_revision, turn_attempt
                    ),
                    foreign key(reply_task_id) references reply_tasks(id),
                    foreign key(parent_agent_run_id) references agent_runs(id)
                );
                insert into agent_runs_turn_migration (
                    id, reply_task_id, execution_generation, role,
                    proposal_revision, turn_attempt, parent_agent_run_id,
                    operation_id, status, codex_session_id,
                    transcript_start_line, transcript_end_line,
                    final_result_json, structured_error_json, tool_events_json,
                    side_effect_state, lease_owner, lease_expires_at,
                    reconciliation_attempts, reconciliation_next_attempt_at,
                    reconciliation_suspended, started_at, completed_at,
                    created_at, updated_at
                )
                select
                    id, reply_task_id, execution_generation, {identity_select},
                    status, codex_session_id,
                    transcript_start_line, transcript_end_line,
                    final_result_json, structured_error_json, tool_events_json,
                    side_effect_state, lease_owner, lease_expires_at,
                    reconciliation_attempts, reconciliation_next_attempt_at,
                    reconciliation_suspended, started_at, completed_at,
                    created_at, updated_at
                from agent_runs;
                drop table agent_runs;
                alter table agent_runs_turn_migration rename to agent_runs;
                create index idx_agent_runs_status
                    on agent_runs(status, updated_at);
                create index idx_agent_runs_reconciliation_due
                    on agent_runs(status, reconciliation_next_attempt_at, id);
                """
            )

    @staticmethod
    def _migrate_removed_runtime(db: sqlite3.Connection) -> None:
        if db.in_transaction:
            db.commit()
        db.execute("begin immediate")
        try:
            tables = {
                str(row["name"])
                for row in db.execute(
                    "select name from sqlite_master where type='table'"
                ).fetchall()
            }
            if {
                "universal_plan_executions",
                "universal_action_executions",
            }.issubset(tables):
                rows = db.execute(
                    """
                    select actions.*, tasks.conversation_id, tasks.conversation_title,
                           tasks.trigger_message_id, tasks.trigger_sender,
                           tasks.trigger_text
                    from universal_action_executions as actions
                    join universal_plan_executions as plans
                      on plans.execution_scope_id=actions.execution_scope_id
                    join reply_tasks as tasks on tasks.id=plans.reply_task_id
                    left join reply_attempts as attempts
                      on attempts.id=actions.attempt_id
                    where attempts.id is null
                    order by actions.created_at, actions.execution_id
                    """
                ).fetchall()
                for row in rows:
                    legacy_status = str(row["status"] or "").strip().lower()
                    action = str(row["action_kind"] or "agent_action").strip()
                    result = str(row["result_json"] or "").strip()
                    send_status, migration_error = (
                        AutoReplyStore._removed_runtime_attempt_status(
                            action=action,
                            legacy_status=legacy_status,
                            result_json=result,
                        )
                    )
                    error = str(row["error"] or "").strip() or migration_error
                    summary = (
                        result
                        or error
                        or f"migrated removed runtime state: {legacy_status}"
                    )
                    db.execute(
                        """
                        insert into reply_attempts (
                            conversation_id, conversation_title, trigger_message_id,
                            trigger_sender, trigger_text, action, sensitivity_kind,
                            codex_reason, audit_summary, send_status, send_error,
                            created_at, updated_at
                        ) values (?, ?, ?, ?, ?, ?, 'general', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["conversation_id"],
                            row["conversation_title"],
                            row["trigger_message_id"],
                            row["trigger_sender"],
                            row["trigger_text"],
                            action,
                            "migrated from removed runtime",
                            summary[:2000],
                            send_status,
                            error[:1000],
                            row["created_at"],
                            row["updated_at"],
                        ),
                    )
            db.execute("drop table if exists universal_action_executions")
            db.execute("drop table if exists universal_plan_executions")
            db.execute("drop index if exists idx_reply_attempts_universal_execution")
            db.execute("delete from service_state where key = 'dws_auth_backup'")
        except Exception:
            db.rollback()
            raise
        db.commit()

    @staticmethod
    def _removed_runtime_attempt_status(
        *,
        action: str,
        legacy_status: str,
        result_json: str,
    ) -> tuple[str, str]:
        if legacy_status == "failed":
            return "failed", ""
        if legacy_status in {"blocked", "unknown"}:
            return "blocked", ""
        if legacy_status == "skipped":
            return "skipped", ""
        if legacy_status != "succeeded":
            return "failed", f"migrated_incomplete_status:{legacy_status}"

        terminal_statuses = {
            "no_reply": "skipped",
            "handoff_to_human": "blocked",
            "blocked": "blocked",
            "stop_with_error": "failed",
        }
        if action in terminal_statuses:
            return terminal_statuses[action], ""
        try:
            receipt = json.loads(result_json)
        except json.JSONDecodeError:
            receipt = None
        if not isinstance(receipt, dict) or not receipt:
            return "failed", "migrated_missing_execution_receipt"
        if legacy_receipt_has_explicit_failure(receipt):
            return "failed", "migrated_explicit_execution_failure"
        if receipt.get("outcome") == "blocked":
            return "blocked", "migrated_structured_execution_block"

        effect_statuses = {
            "send_reply": "sent",
            "ask_clarifying_question": "sent",
            "oa_approval": "completed",
            "mail_reply": "sent",
            "calendar_response": "calendar",
            "dws_markdown_document_reply": "document",
            "dws_message_reaction": "reacted",
            "queue_okr_review": "completed",
            "memory_write": "completed",
        }
        status = effect_statuses.get(action, "completed")
        if AutoReplyStore._legacy_action_receipt_is_success(action, receipt):
            return status, ""
        tool_events = receipt.get("tool_events")
        if (
            isinstance(tool_events, list)
            and all(isinstance(event, dict) for event in tool_events)
            and _persisted_agent_effect_state(tool_events) == "confirmed"
        ):
            return status, ""
        if _persisted_agent_receipt_ids(receipt):
            return status, ""
        return "failed", "migrated_unverified_execution_receipt"

    @staticmethod
    def _legacy_action_receipt_is_success(
        action: str,
        receipt: dict[str, object],
    ) -> bool:
        if action in {"send_reply", "ask_clarifying_question"}:
            return (
                receipt.get("action_kind") == action
                and receipt.get("outcome")
                in {
                    "delivered",
                    "delivery_salvaged_after_error",
                    "duplicate_existing_delivery",
                }
            )
        if action == "oa_approval":
            outcome = receipt.get("outcome")
            process_id = str(receipt.get("process_instance_id") or "").strip()
            approval_action = str(receipt.get("action") or "").strip()
            if not process_id or not approval_action:
                return False
            if outcome == "commented":
                return True
            task_id = str(receipt.get("task_id") or "").strip()
            return bool(task_id) and outcome in {
                "already_handled",
                "applicant_notified",
                "applied",
                "handled_by_different_action",
                "salvaged",
            }
        if action in {"mail_reply", "calendar_response"}:
            if receipt.get("success") is True:
                return True
            if receipt.get("ok") is True:
                return AutoReplyStore._legacy_receipt_has_identifier(
                    receipt.get("result"), {"messageid", "eventid", "receipt"}
                )
            return any(
                receipt.get(field) == 0 or receipt.get(field) == "0"
                for field in ("errcode", "code")
            )
        if action == "dws_markdown_document_reply":
            return (
                bool(str(receipt.get("node_id") or "").strip())
                and bool(str(receipt.get("url") or "").strip())
                and AutoReplyStore._legacy_receipt_has_identifier(
                    receipt.get("delivery"), {"messageid", "receipt"}
                )
            )
        if action == "dws_message_reaction":
            return AutoReplyStore._legacy_receipt_has_identifier(
                receipt,
                {"emotionid", "reactionid", "receipt"},
            )
        if action == "queue_okr_review":
            return (
                receipt.get("action_kind") == action
                and receipt.get("outcome") == "okr_review_queued_and_acknowledged"
            )
        if action == "memory_write":
            return (
                bool(str(receipt.get("episode_uuid") or "").strip())
                and receipt.get("processing_status") == "completed"
            )
        return False

    @staticmethod
    def _legacy_receipt_has_identifier(
        value: object,
        fields: set[str],
    ) -> bool:
        if isinstance(value, list):
            return any(
                AutoReplyStore._legacy_receipt_has_identifier(item, fields)
                for item in value
            )
        if not isinstance(value, dict):
            return False
        for key, item in value.items():
            normalized_key = str(key).replace("_", "").casefold()
            if normalized_key in fields and str(item or "").strip():
                return True
            if isinstance(item, (dict, list)) and (
                AutoReplyStore._legacy_receipt_has_identifier(item, fields)
            ):
                return True
        return False

    @staticmethod
    def _migrate_agent_run_events(db: sqlite3.Connection) -> None:
        rows = db.execute(
            "select id, tool_events_json from agent_runs "
            "where tool_events_json <> '[]'"
        ).fetchall()
        for row in rows:
            try:
                events = json.loads(row["tool_events_json"])
            except json.JSONDecodeError as exc:
                raise ValueError("agent run tool events are not valid JSON") from exc
            if not isinstance(events, list) or any(
                not isinstance(event, dict) for event in events
            ):
                raise ValueError("agent run tool events must be JSON objects")
            for sequence, event in enumerate(events, start=1):
                event_text = _json_object_text(event, field="event")
                event_type, call_id, effect_kind, receipt_operation_id = (
                    _agent_event_columns(event)
                )
                db.execute(
                    """
                    insert or ignore into agent_run_events (
                        agent_run_id, sequence, event_json, event_type,
                        call_id, effect_kind, receipt_operation_id
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        sequence,
                        event_text,
                        event_type,
                        call_id,
                        effect_kind,
                        receipt_operation_id,
                    ),
                )
                persisted = db.execute(
                    "select event_json from agent_run_events "
                    "where agent_run_id=? and sequence=?",
                    (row["id"], sequence),
                ).fetchone()
                if persisted is None or json.loads(persisted["event_json"]) != event:
                    raise ValueError("conflicting agent run event migration")
            db.execute(
                "update agent_runs set tool_events_json='[]' where id=?",
                (row["id"],),
            )

    @staticmethod
    def _backfill_agent_run_effect_counters(db: sqlite3.Connection) -> None:
        candidate_ids = [
            row["id"]
            for row in db.execute(
                """
                select id from agent_runs
                where effect_started_count=0
                  and effect_completed_count=0
                  and effect_failed_count=0
                  and effect_receipt_count=0
                  and effect_unreviewed_count=0
                  and exists (
                      select 1 from agent_run_events
                      where agent_run_id=agent_runs.id
                  )
                """
            ).fetchall()
        ]
        if not candidate_ids:
            return
        db.execute(
            """
            update agent_runs
            set effect_started_count=(
                    select count(*) from agent_run_events
                    where agent_run_id=agent_runs.id
                      and effect_kind='effectful' and event_type='item.started'
                ),
                effect_completed_count=(
                    select count(*) from agent_run_events
                    where agent_run_id=agent_runs.id
                      and effect_kind='effectful' and event_type='item.completed'
                ),
                effect_failed_count=(
                    select count(*) from agent_run_events
                    where agent_run_id=agent_runs.id
                      and effect_kind='effectful' and event_type='item.failed'
                ),
                effect_receipt_count=min(
                    (select count(*) from agent_run_events
                     where agent_run_id=agent_runs.id
                       and receipt_operation_id<>''),
                    max(0,
                        (select count(*) from agent_run_events
                         where agent_run_id=agent_runs.id
                           and effect_kind='effectful'
                           and event_type='item.started')
                        - (select count(*) from agent_run_events
                           where agent_run_id=agent_runs.id
                             and effect_kind='effectful'
                             and event_type in ('item.completed', 'item.failed'))
                    )
                ),
                effect_unreviewed_count=(
                    select count(*) from agent_run_events
                    where agent_run_id=agent_runs.id and effect_kind='unreviewed'
                )
            where effect_started_count=0
              and effect_completed_count=0
              and effect_failed_count=0
              and effect_receipt_count=0
              and effect_unreviewed_count=0
              and exists (
                  select 1 from agent_run_events
                  where agent_run_id=agent_runs.id
              )
            """
        )
        for run_id in candidate_ids:
            row = db.execute(
                """
                select id, effect_started_count, effect_completed_count,
                       effect_failed_count, effect_receipt_count,
                       effect_unreviewed_count
                from agent_runs where id=?
                """,
                (run_id,),
            ).fetchone()
            db.execute(
                "update agent_runs set side_effect_state=? where id=?",
                (_agent_effect_state_from_counts(row), row["id"]),
            )

    @staticmethod
    def _migrate_reply_task_channel_identity(db: sqlite3.Connection) -> None:
        """Replace the legacy cross-channel UNIQUE constraint in place."""
        columns = {
            row["name"] for row in db.execute("pragma table_info(reply_tasks)").fetchall()
        }
        if "channel" not in columns:
            db.execute(
                "alter table reply_tasks add column channel "
                "text not null default 'dingtalk'"
            )
        unique_columns = {
            tuple(
                row["name"]
                for row in db.execute(
                    "select name from pragma_index_info(?) order by seqno",
                    (index["name"],),
                ).fetchall()
            )
            for index in db.execute("pragma index_list(reply_tasks)").fetchall()
            if index["unique"]
        }
        if ("conversation_id", "trigger_message_id") not in unique_columns:
            return

        generation_select = (
            "execution_generation"
            if "execution_generation" in columns
            else "'initial'"
        )
        with AutoReplyStore._foreign_key_rebuild(
            db,
            migration_name="reply_tasks",
        ):
            db.executescript(
                f"""
                begin immediate;
                create table reply_tasks_channel_migration (
                    id integer primary key autoincrement,
                    channel text not null default 'dingtalk',
                    conversation_id text not null,
                    conversation_title text not null,
                    single_chat integer not null,
                    trigger_message_id text not null,
                    trigger_create_time text not null,
                    trigger_sender text not null,
                    trigger_text text not null,
                    trigger_message_json text not null default '{{}}',
                    available_at text not null default '',
                    force_new_decision integer not null default 0,
                    oa_url text not null default '',
                    manual_rerun_attempt_id integer not null default 0,
                    manual_rerun_revision_key text not null default '',
                    execution_generation text not null default 'initial',
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    locked_at text,
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(channel, conversation_id, trigger_message_id)
                );
                insert into reply_tasks_channel_migration (
                    id, channel, conversation_id, conversation_title, single_chat,
                    trigger_message_id, trigger_create_time, trigger_sender,
                    trigger_text, trigger_message_json, available_at,
                    force_new_decision, oa_url, manual_rerun_attempt_id,
                    manual_rerun_revision_key, execution_generation, status,
                    attempts, locked_at, error, created_at, updated_at
                )
                select
                    id, channel, conversation_id, conversation_title, single_chat,
                    trigger_message_id, trigger_create_time, trigger_sender,
                    trigger_text, trigger_message_json, available_at,
                    force_new_decision, oa_url, manual_rerun_attempt_id,
                    manual_rerun_revision_key, {generation_select}, status,
                    attempts, locked_at, error, created_at, updated_at
                from reply_tasks;
                drop table reply_tasks;
                alter table reply_tasks_channel_migration rename to reply_tasks;
                create index idx_reply_tasks_status on reply_tasks(status, id);
                """
            )

    @staticmethod
    def _reply_task_from_row(row: sqlite3.Row) -> ReplyTask:
        return ReplyTask(
            id=row["id"],
            channel=(row["channel"] if "channel" in row.keys() else "dingtalk"),
            conversation_id=row["conversation_id"],
            conversation_title=row["conversation_title"],
            single_chat=bool(row["single_chat"]),
            trigger_message_id=row["trigger_message_id"],
            trigger_create_time=row["trigger_create_time"],
            trigger_sender=row["trigger_sender"],
            trigger_text=row["trigger_text"],
            trigger_message_json=row["trigger_message_json"],
            available_at=row["available_at"],
            force_new_decision=bool(row["force_new_decision"]),
            oa_url=row["oa_url"],
            manual_rerun_attempt_id=row["manual_rerun_attempt_id"],
            manual_rerun_revision_key=row["manual_rerun_revision_key"],
            execution_generation=row["execution_generation"],
            recovery_code=(
                row["recovery_code"] if "recovery_code" in row.keys() else ""
            ),
            status=row["status"],
            attempts=row["attempts"],
            locked_at=row["locked_at"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _agent_run_from_row(
        row: sqlite3.Row,
        *,
        db: sqlite3.Connection,
        load_events: bool = True,
    ) -> AgentRun:
        tool_events: list[dict[str, object]] = []
        if load_events:
            event_rows = db.execute(
                "select event_json from agent_run_events "
                "where agent_run_id=? order by sequence",
                (row["id"],),
            ).fetchall()
            tool_events = [json.loads(event["event_json"]) for event in event_rows]
        return AgentRun(
            id=row["id"],
            reply_task_id=row["reply_task_id"],
            execution_generation=row["execution_generation"],
            role=AgentRole(row["role"]),
            proposal_revision=row["proposal_revision"],
            turn_attempt=row["turn_attempt"],
            parent_agent_run_id=row["parent_agent_run_id"],
            operation_id=row["operation_id"],
            status=row["status"],
            codex_session_id=row["codex_session_id"],
            transcript_start_line=row["transcript_start_line"],
            transcript_end_line=row["transcript_end_line"],
            final_result_json=row["final_result_json"],
            structured_error_json=row["structured_error_json"],
            tool_events=tool_events,
            effect_started_count=row["effect_started_count"],
            effect_completed_count=row["effect_completed_count"],
            effect_failed_count=row["effect_failed_count"],
            effect_receipt_count=row["effect_receipt_count"],
            effect_unreviewed_count=row["effect_unreviewed_count"],
            reconciliation_event_count=row["reconciliation_event_count"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            reconciliation_attempts=row["reconciliation_attempts"],
            reconciliation_next_attempt_at=row["reconciliation_next_attempt_at"],
            reconciliation_suspended=bool(row["reconciliation_suspended"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _agent_runtime_attempt_from_row(row: sqlite3.Row) -> AgentRuntimeAttempt:
        return AgentRuntimeAttempt.model_validate(dict(row))

    @staticmethod
    def _okr_review_request_from_row(row: sqlite3.Row) -> OkrReviewRequest:
        return OkrReviewRequest.model_validate(dict(row))

    def enqueue_reply_task(
        self,
        *,
        conversation_id: str,
        conversation_title: str,
        single_chat: bool,
        trigger_message_id: str,
        trigger_create_time: str,
        trigger_sender: str,
        trigger_text: str,
        trigger_message_json: str = "{}",
        available_at: str = "",
        force_new_decision: bool = False,
        oa_url: str = "",
        manual_rerun_attempt_id: int = 0,
        error: str = "",
        channel: str = "dingtalk",
        execution_generation: str = "initial",
    ) -> bool:
        if (
            not isinstance(execution_generation, str)
            or not execution_generation.strip()
        ):
            raise ValueError("execution_generation must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                insert or ignore into reply_tasks (
                    channel,
                    conversation_id,
                    conversation_title,
                    single_chat,
                    trigger_message_id,
                    trigger_create_time,
                    trigger_sender,
                    trigger_text,
                    trigger_message_json,
                    available_at,
                    force_new_decision,
                    oa_url,
                    manual_rerun_attempt_id,
                    execution_generation,
                    error
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel,
                    conversation_id,
                    conversation_title,
                    int(single_chat),
                    trigger_message_id,
                    trigger_create_time,
                    trigger_sender,
                    trigger_text,
                    trigger_message_json,
                    available_at,
                    int(force_new_decision),
                    oa_url,
                    manual_rerun_attempt_id,
                    execution_generation,
                    error,
                ),
            )
            return cursor.rowcount == 1

    def enqueue_manual_rerun_reply_task(
        self,
        *,
        conversation_id: str,
        conversation_title: str,
        single_chat: bool,
        trigger_message_id: str,
        trigger_create_time: str,
        trigger_sender: str,
        trigger_text: str,
        trigger_message_json: str,
        oa_url: str = "",
        attempt_id: int = 0,
        channel: str = "dingtalk",
        force_rotation: bool = False,
    ) -> ReplyTask:
        with self._immediate_write_transaction() as db:
            revision_key = self._manual_rerun_revision_key(db, attempt_id)
            task = self._enqueue_manual_rerun_reply_task_in_connection(
                db,
                conversation_id=conversation_id,
                conversation_title=conversation_title,
                single_chat=single_chat,
                trigger_message_id=trigger_message_id,
                trigger_create_time=trigger_create_time,
                trigger_sender=trigger_sender,
                trigger_text=trigger_text,
                trigger_message_json=trigger_message_json,
                oa_url=oa_url,
                attempt_id=attempt_id,
                revision_key=revision_key,
                channel=channel,
                force_rotation=force_rotation,
            )
        return task

    @staticmethod
    def _manual_rerun_revision_key(
        db: sqlite3.Connection,
        attempt_id: int,
    ) -> str:
        revision: dict[str, object] = {
            "attempt_id": attempt_id,
            "corrected_reply_text": "",
            "reviewer_feedback": "",
            "version": 1,
        }
        if attempt_id > 0:
            row = db.execute(
                """
                select reviewer_feedback, corrected_reply_text
                from reply_attempts where id=?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"manual rerun attempt does not exist: {attempt_id}")
            revision["reviewer_feedback"] = str(
                row["reviewer_feedback"] or ""
            ).strip()
            revision["corrected_reply_text"] = str(
                row["corrected_reply_text"] or ""
            ).strip()
        canonical = json.dumps(
            revision,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _enqueue_manual_rerun_reply_task_in_connection(
        cls,
        db: sqlite3.Connection,
        *,
        conversation_id: str,
        conversation_title: str,
        single_chat: bool,
        trigger_message_id: str,
        trigger_create_time: str,
        trigger_sender: str,
        trigger_text: str,
        trigger_message_json: str,
        oa_url: str,
        attempt_id: int,
        revision_key: str,
        channel: str,
        force_rotation: bool = False,
    ) -> ReplyTask:
        existing = db.execute(
            """
            select * from reply_tasks
            where channel=? and conversation_id=? and trigger_message_id=?
            """,
            (channel, conversation_id, trigger_message_id),
        ).fetchone()
        if (
            existing is not None
            and not force_rotation
            and existing["status"] in {"pending", "processing"}
            and int(existing["manual_rerun_attempt_id"] or 0) == attempt_id
            and str(existing["manual_rerun_revision_key"] or "") == revision_key
        ):
            return cls._reply_task_from_row(existing)
        execution_generation = uuid4().hex
        if existing is not None:
            now_text = str(db.execute("select current_timestamp").fetchone()[0])
            if force_rotation:
                active_run = db.execute(
                    """
                    select 1 from agent_runs
                    where reply_task_id=? and execution_generation=?
                      and status='running'
                    limit 1
                    """,
                    (int(existing["id"]), str(existing["execution_generation"])),
                ).fetchone()
                if active_run is not None:
                    raise ValueError(
                        "active agent run must finish before forced rerun"
                    )
            cls._supersede_running_agent_runs(
                db,
                int(existing["id"]),
                str(existing["execution_generation"]),
                now_text=now_text,
            )
            cls._supersede_ready_wechat_delivery(
                db, int(existing["id"]), execution_generation
            )
        db.execute(
            """
            insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, trigger_message_json, available_at,
                force_new_decision, oa_url, manual_rerun_attempt_id,
                manual_rerun_revision_key, execution_generation, status,
                locked_at, error
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 1, ?, ?, ?, ?,
                      'pending', null, ?)
            on conflict(channel, conversation_id, trigger_message_id) do update set
                conversation_title=excluded.conversation_title,
                single_chat=excluded.single_chat,
                trigger_create_time=excluded.trigger_create_time,
                trigger_sender=excluded.trigger_sender,
                trigger_text=excluded.trigger_text,
                trigger_message_json=excluded.trigger_message_json,
                available_at='',
                attempts=0,
                force_new_decision=1,
                oa_url=excluded.oa_url,
                manual_rerun_attempt_id=excluded.manual_rerun_attempt_id,
                manual_rerun_revision_key=excluded.manual_rerun_revision_key,
                execution_generation=excluded.execution_generation,
                status='pending',
                locked_at=null,
                error=excluded.error,
                updated_at=current_timestamp
            """,
            (
                channel,
                conversation_id,
                conversation_title,
                int(single_chat),
                trigger_message_id,
                trigger_create_time,
                trigger_sender,
                trigger_text,
                trigger_message_json,
                oa_url,
                attempt_id,
                revision_key,
                execution_generation,
                f"manual_rerun_from_attempt:{attempt_id}",
            ),
        )
        row = db.execute(
            """
            select * from reply_tasks
            where channel=? and conversation_id=? and trigger_message_id=?
            """,
            (channel, conversation_id, trigger_message_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("manual rerun reply task was not persisted")
        return cls._reply_task_from_row(row)

    def get_agent_run(self, run_id: int) -> AgentRun | None:
        with self._connect() as db:
            row = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            return self._agent_run_from_row(row, db=db) if row is not None else None

    def get_agent_run_for_turn(
        self,
        reply_task_id: int,
        execution_generation: str,
        *,
        role: AgentRole,
        proposal_revision: int,
        turn_attempt: int,
    ) -> AgentRun | None:
        role = AgentRole(role)
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from agent_runs
                where reply_task_id=? and execution_generation=? and role=?
                  and proposal_revision=? and turn_attempt=?
                """,
                (
                    reply_task_id,
                    execution_generation,
                    role.value,
                    proposal_revision,
                    turn_attempt,
                ),
            ).fetchone()
            return self._agent_run_from_row(row, db=db) if row is not None else None

    def next_agent_run_turn_attempt(
        self,
        reply_task_id: int,
        execution_generation: str,
        *,
        role: AgentRole,
        proposal_revision: int,
    ) -> int:
        """Return the next persisted attempt number for one Agent role/revision."""
        role = AgentRole(role)
        if not execution_generation.strip():
            raise ValueError("execution_generation must be non-empty")
        if proposal_revision < 0:
            raise ValueError("proposal_revision must not be negative")
        with self._connect() as db:
            row = db.execute(
                """
                select coalesce(max(turn_attempt), -1) + 1 as next_turn_attempt
                from agent_runs
                where reply_task_id=? and execution_generation=? and role=?
                  and proposal_revision=?
                """,
                (
                    reply_task_id,
                    execution_generation,
                    role.value,
                    proposal_revision,
                ),
            ).fetchone()
            return int(row["next_turn_attempt"])

    def list_agent_runs_for_task_generation(
        self,
        reply_task_id: int,
        execution_generation: str,
    ) -> list[AgentRun]:
        with self._connect() as db:
            rows = db.execute(
                """
                select * from agent_runs
                where reply_task_id=? and execution_generation=?
                order by proposal_revision,
                         case role when 'consumer' then 0 else 1 end,
                         turn_attempt, id
                """,
                (reply_task_id, execution_generation),
            ).fetchall()
            return [self._agent_run_from_row(row, db=db) for row in rows]

    def list_agent_run_summaries_for_terminal_runs(
        self,
        run_ids: list[int],
    ) -> dict[int, list[AgentRun]]:
        terminal_ids = list(
            dict.fromkeys(
                run_id for run_id in run_ids if type(run_id) is int and run_id > 0
            )
        )
        if not terminal_ids:
            return {}
        placeholders = ", ".join("?" for _ in terminal_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""
                with terminal_runs as (
                    select id as terminal_id, reply_task_id, execution_generation
                    from agent_runs
                    where id in ({placeholders})
                )
                select terminal_runs.terminal_id, agent_runs.*
                from terminal_runs
                join agent_runs
                  on agent_runs.reply_task_id=terminal_runs.reply_task_id
                 and agent_runs.execution_generation=terminal_runs.execution_generation
                order by terminal_runs.terminal_id,
                         agent_runs.proposal_revision,
                         case agent_runs.role when 'consumer' then 0 else 1 end,
                         agent_runs.turn_attempt, agent_runs.id
                """,
                terminal_ids,
            ).fetchall()
            summaries: dict[int, list[AgentRun]] = {}
            for row in rows:
                terminal_id = int(row["terminal_id"])
                summaries.setdefault(terminal_id, []).append(
                    self._agent_run_from_row(row, db=db, load_events=False)
                )
            return summaries

    def agent_run_lease_is_active(
        self,
        run_id: int,
        *,
        now: str | datetime | None = None,
    ) -> bool:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            row = db.execute(
                "select 1 from agent_runs "
                "where id=? and status='running' and lease_expires_at>?",
                (run_id, now_text),
            ).fetchone()
            return row is not None

    def foreign_key_violations(self) -> list[tuple[object, ...]]:
        with self._connect() as db:
            return [tuple(row) for row in db.execute("pragma foreign_key_check")]

    def record_agent_execution_receipt(
        self,
        run_id: int,
        *,
        receipt_id: str,
        operation_id: str,
        cli: str,
        command_path: str,
        command_digest: str,
        exit_code: int,
        owner: str,
        expected_status: str = "running",
        now: str | datetime | None = None,
    ) -> AgentExecutionReceipt:
        if not all(
            value.strip()
            for value in (
                receipt_id,
                operation_id,
                cli,
                command_path,
                command_digest,
            )
        ):
            raise ValueError("execution receipt identity must be non-empty")
        if exit_code != 0:
            raise ValueError("only successful executions can produce receipts")
        if expected_status not in {"running", "unknown"}:
            raise ValueError("invalid execution receipt run status")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            run_row = self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                expected_status=expected_status,
            )
            run_row = db.execute(
                "select role from agent_runs where id=?", (run_id,)
            ).fetchone()
            if run_row is not None and run_row["role"] == AgentRole.CONSUMER.value:
                raise ValueError("Consumer Agent cannot persist execution receipts")
            db.execute(
                """
                insert or ignore into agent_execution_receipts (
                    agent_run_id, receipt_id, operation_id, cli,
                    command_path, command_digest, exit_code,
                    completed, persisted, safe_to_confirm, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, 1, 1, 1, ?)
                """,
                (
                    run_id,
                    receipt_id,
                    operation_id,
                    cli,
                    command_path,
                    command_digest,
                    exit_code,
                    now_text,
                ),
            )
            row = db.execute(
                """
                select * from agent_execution_receipts
                where agent_run_id=? and operation_id=?
                """,
                (run_id, operation_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("execution receipt was not persisted")
            if (
                row["receipt_id"] != receipt_id
                or row["cli"] != cli
                or row["command_path"] != command_path
                or row["command_digest"] != command_digest
                or row["exit_code"] != exit_code
            ):
                raise ValueError("conflicting execution receipt")
            return AgentExecutionReceipt.model_validate(dict(row))

    @staticmethod
    def _agent_effect_intent_identity(
        authorization: dict[str, object],
    ) -> tuple[str, int, str, str, str, str, str, str]:
        values = (
            authorization.get("authorization_id"),
            authorization.get("action_index"),
            authorization.get("receipt_operation_id"),
            authorization.get("capability"),
            authorization.get("operation"),
            authorization.get("operation_digest"),
            authorization.get("arguments_digest"),
        )
        target_identifiers = authorization.get("target_identifiers")
        if (
            not isinstance(values[0], str)
            or not values[0].strip()
            or isinstance(values[1], bool)
            or not isinstance(values[1], int)
            or values[1] < 0
            or not all(
                isinstance(value, str) and value.strip()
                for value in values[2:]
            )
            or not isinstance(target_identifiers, dict)
        ):
            raise ValueError("effect intent identity is invalid")
        return (
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            values[5],
            values[6],
            _json_object_text(target_identifiers, field="target_identifiers"),
        )

    @staticmethod
    def _persisted_agent_effect_intent_identity(
        row: sqlite3.Row,
    ) -> tuple[str, int, str, str, str, str, str, str]:
        return tuple(
            row[key]
            for key in (
                "authorization_id",
                "action_index",
                "receipt_operation_id",
                "capability",
                "operation",
                "operation_digest",
                "arguments_digest",
                "target_identifiers_json",
            )
        )

    def prepare_agent_effect_intents(
        self,
        run_id: int,
        authorizations: tuple[dict[str, object], ...],
        *,
        owner: str,
        now: str | datetime | None = None,
    ) -> None:
        """Persist exact one-shot write intents before the model can dispatch them."""
        identities = tuple(
            self._agent_effect_intent_identity(authorization)
            for authorization in authorizations
        )
        if len({identity[0] for identity in identities}) != len(identities):
            raise ValueError("effect intent authorization IDs must be unique")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            run_row = db.execute(
                "select * from agent_runs where id=?", (run_id,)
            ).fetchone()
            if run_row is None or run_row["status"] not in {"running", "unknown"}:
                raise ValueError("effect intents require an active Audit run")
            run_row = self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                expected_status=run_row["status"],
            )
            if run_row["role"] != AgentRole.AUDIT.value:
                raise ValueError("effect intents require an Audit run")
            for identity in identities:
                existing = db.execute(
                    "select * from agent_effect_intents "
                    "where agent_run_id=? and receipt_operation_id=?",
                    (run_id, identity[2]),
                ).fetchone()
                if (
                    existing is not None
                    and self._persisted_agent_effect_intent_identity(existing)
                    != identity
                ):
                    raise ValueError("conflicting logical effect intent")
                db.execute(
                    """
                    insert or ignore into agent_effect_intents (
                        agent_run_id, authorization_id, action_index,
                        receipt_operation_id, capability, operation,
                        operation_digest, arguments_digest,
                        target_identifiers_json, state, prepared_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)
                    """,
                    (run_id, *identity, now_text, now_text),
                )
                row = db.execute(
                    "select * from agent_effect_intents "
                    "where agent_run_id=? and authorization_id=?",
                    (run_id, identity[0]),
                ).fetchone()
                if self._persisted_agent_effect_intent_identity(row) != identity:
                    raise ValueError("conflicting effect intent")

    def dispatch_agent_effect_intent(
        self,
        run_id: int,
        authorization: dict[str, object],
        *,
        now: str | datetime | None = None,
    ) -> None:
        """Consume one prepared authorization immediately before target dispatch."""
        identity = self._agent_effect_intent_identity(authorization)
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            run_row = db.execute(
                "select status, lease_owner, lease_expires_at from agent_runs "
                "where id=?", (run_id,)
            ).fetchone()
            if (
                run_row is None
                or run_row["status"] not in {"running", "unknown"}
                or not run_row["lease_owner"]
                or run_row["lease_expires_at"] <= now_text
            ):
                raise ValueError("effect intent run is not active")
            row = db.execute(
                "select * from agent_effect_intents "
                "where agent_run_id=? and authorization_id=?",
                (run_id, identity[0]),
            ).fetchone()
            if row is None:
                raise ValueError("effect intent was not prepared")
            if self._persisted_agent_effect_intent_identity(row) != identity:
                raise ValueError("effect intent identity mismatch")
            if row["state"] != "prepared":
                raise ValueError("effect intent already dispatched")
            cursor = db.execute(
                "update agent_effect_intents set state='dispatched', "
                "dispatched_at=?, updated_at=? where id=? and state='prepared'",
                (now_text, now_text, row["id"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("effect intent already dispatched")
            db.execute(
                "update agent_runs set side_effect_state='unknown', updated_at=? "
                "where id=? and status in ('running', 'unknown')",
                (now_text, run_id),
            )

    def acknowledge_agent_effect_intent(
        self,
        run_id: int,
        authorization: dict[str, object],
        *,
        result_digest: str,
        exit_code: int,
        now: str | datetime | None = None,
    ) -> None:
        """Persist a successful tool ack even if the service lease was lost."""
        identity = self._agent_effect_intent_identity(authorization)
        if not result_digest.strip() or exit_code != 0:
            raise ValueError("only a successful durable ack can confirm an intent")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = db.execute(
                "select * from agent_effect_intents "
                "where agent_run_id=? and authorization_id=?",
                (run_id, identity[0]),
            ).fetchone()
            if row is None or row["state"] != "dispatched":
                raise ValueError("effect intent is not awaiting acknowledgement")
            if self._persisted_agent_effect_intent_identity(row) != identity:
                raise ValueError("effect intent identity mismatch")
            db.execute(
                """
                insert or ignore into agent_execution_receipts (
                    agent_run_id, receipt_id, operation_id, cli,
                    command_path, command_digest, exit_code,
                    completed, persisted, safe_to_confirm, created_at
                ) values (?, ?, ?, ?, ?, ?, 0, 1, 1, 1, ?)
                """,
                (
                    run_id,
                    identity[0],
                    identity[2],
                    identity[3].rsplit(".", 1)[-1],
                    identity[4],
                    identity[5],
                    now_text,
                ),
            )
            receipt = db.execute(
                "select * from agent_execution_receipts "
                "where agent_run_id=? and operation_id=?",
                (run_id, identity[2]),
            ).fetchone()
            if (
                receipt is None
                or receipt["receipt_id"] != identity[0]
                or receipt["cli"] != identity[3].rsplit(".", 1)[-1]
                or receipt["command_path"] != identity[4]
                or receipt["command_digest"] != identity[5]
                or receipt["exit_code"] != exit_code
            ):
                raise ValueError("conflicting execution receipt")
            db.execute(
                "update agent_effect_intents set state='acknowledged', "
                "result_digest=?, exit_code=?, acknowledged_at=?, updated_at=? "
                "where id=? and state='dispatched'",
                (result_digest, exit_code, now_text, now_text, row["id"]),
            )

    def confirm_agent_execution_receipt(
        self,
        run_id: int,
        operation_id: str,
        *,
        owner: str,
        expected_status: str = "unknown",
        now: str | datetime | None = None,
    ) -> None:
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                expected_status=expected_status,
            )
            receipt = db.execute(
                """
                select * from agent_execution_receipts
                where agent_run_id=? and operation_id=?
                  and completed=1 and persisted=1 and safe_to_confirm=1
                """,
                (run_id, operation_id),
            ).fetchone()
            if receipt is None:
                raise ValueError("execution receipt is not confirmable")
            if receipt["effect_counted"]:
                return
            action_index = int(json.loads(operation_id)["action_index"])
            action_state = db.execute(
                """
                select
                    sum(case when event_type='item.started' then 1 else 0 end) as starts,
                    sum(case when event_type in ('item.completed', 'item.failed')
                             then 1 else 0 end) as closures
                from agent_run_events
                where agent_run_id=? and effect_kind='effectful'
                  and json_extract(event_json, '$.item.metadata.action_index')=?
                """,
                (run_id, action_index),
            ).fetchone()
            starts = int(action_state["starts"] or 0)
            closures = int(action_state["closures"] or 0)
            # Reconciliation may confirm a write that the Codex stream already
            # marked completed, but for which no durable receipt was captured
            # before the run became unknown.  A matching live read is validated
            # by the caller before reaching this method, so that closed event is
            # still an eligible effect to account for.  Reject only missing or
            # inconsistent lifecycle evidence.
            if starts == 0 or closures > starts:
                raise ValueError("execution receipt has no matching effect")
            db.execute(
                "update agent_execution_receipts set effect_counted=1 where id=?",
                (receipt["id"],),
            )
            db.execute(
                "update agent_runs set effect_receipt_count=effect_receipt_count+1 "
                "where id=?",
                (run_id,),
            )
            counts = db.execute(
                """
                select effect_started_count, effect_completed_count,
                       effect_failed_count, effect_receipt_count,
                       effect_unreviewed_count
                from agent_runs where id=?
                """,
                (run_id,),
            ).fetchone()
            db.execute(
                "update agent_runs set side_effect_state=? where id=?",
                (_agent_effect_state_from_counts(counts), run_id),
            )

    def bind_legacy_unknown_effect_action(
        self,
        run_id: int,
        *,
        action_index: int,
        operation_id: str,
        expected_identity: dict[str, object],
        owner: str,
        now: str | datetime | None = None,
    ) -> bool:
        """Bind one pre-action-index unknown start to an exact reviewed action."""
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                expected_status="unknown",
            )
            rows = db.execute(
                """
                select id, event_json from agent_run_events
                where agent_run_id=? and event_type='item.started'
                  and effect_kind='effectful'
                  and json_type(event_json, '$.item.metadata.action_index') is null
                  and json_extract(event_json, '$.item.metadata.operation_id')=?
                  and json_extract(event_json, '$.item.metadata.capability')=?
                  and json_extract(event_json, '$.item.metadata.operation')=?
                  and json_extract(event_json, '$.item.metadata.operation_digest')=?
                  and json_extract(event_json, '$.item.metadata.arguments_digest')=?
                order by sequence
                """,
                (
                    run_id,
                    operation_id,
                    expected_identity.get("capability"),
                    expected_identity.get("operation"),
                    expected_identity.get("operation_digest"),
                    expected_identity.get("arguments_digest"),
                ),
            ).fetchall()
            target = expected_identity.get("target_identifiers")
            for row in rows:
                event = json.loads(row["event_json"])
                identity = _agent_effect_identity(event) or {}
                if identity.get("target_identifiers") != target:
                    continue
                event["item"]["metadata"]["action_index"] = action_index
                cursor = db.execute(
                    """
                    update agent_run_events set event_json=?
                    where id=?
                      and json_type(event_json, '$.item.metadata.action_index') is null
                    """,
                    (
                        json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                        row["id"],
                    ),
                )
                return cursor.rowcount == 1
            return False

    def list_agent_execution_receipts(
        self,
        run_id: int,
    ) -> list[AgentExecutionReceipt]:
        with self._connect() as db:
            rows = db.execute(
                """
                select * from agent_execution_receipts
                where agent_run_id=?
                order by id
                """,
                (run_id,),
            ).fetchall()
            return [
                AgentExecutionReceipt.model_validate(dict(row)) for row in rows
            ]

    @contextmanager
    def _agent_run_write_transaction(
        self,
        now: str | datetime | None,
    ) -> Iterator[tuple[sqlite3.Connection, tuple[datetime, str]]]:
        for attempt in range(AGENT_RUN_WRITE_LOCK_RETRY_ATTEMPTS):
            connection = self._connect()
            try:
                db = connection.__enter__()
            except sqlite3.OperationalError as exc:
                if (
                    not _is_sqlite_lock_error(exc)
                    or attempt + 1 >= AGENT_RUN_WRITE_LOCK_RETRY_ATTEMPTS
                ):
                    raise
                time.sleep(AGENT_RUN_WRITE_LOCK_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            try:
                db.execute("begin immediate")
            except sqlite3.OperationalError as exc:
                connection.__exit__(type(exc), exc, exc.__traceback__)
                if (
                    not _is_sqlite_lock_error(exc)
                    or attempt + 1 >= AGENT_RUN_WRITE_LOCK_RETRY_ATTEMPTS
                ):
                    raise
                time.sleep(AGENT_RUN_WRITE_LOCK_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            else:
                try:
                    yield db, _utc_store_time(now)
                except BaseException as exc:
                    connection.__exit__(type(exc), exc, exc.__traceback__)
                    raise
                else:
                    connection.__exit__(None, None, None)
                return

    @staticmethod
    def _require_runtime_attempt_text(value: str, *, field: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(f"{field} must be non-empty")
        return value

    @staticmethod
    def _validate_runtime_operation_workload(
        workload_kind: str,
        workload_key: str,
    ) -> tuple[str, str]:
        workload_kind = AutoReplyStore._require_runtime_attempt_text(
            workload_kind, field="workload_kind"
        )
        workload_key = AutoReplyStore._require_runtime_attempt_text(
            workload_key, field="workload_key"
        )
        if workload_kind not in RUNTIME_OPERATION_WORKLOAD_KINDS:
            raise ValueError("unsupported runtime operation workload kind")
        if workload_kind in {"structured", "meeting"}:
            if not workload_key.isdecimal() or int(workload_key) <= 0:
                raise ValueError("runtime operation workload key must be a persisted ID")
        elif workload_kind == "task":
            task_id, separator, suffix = workload_key.partition(":")
            if not task_id.isdecimal() or int(task_id) <= 0:
                raise ValueError("task workload key must start with a persisted ID")
            if separator and suffix != "memory_backfill":
                raise ValueError("task workload key has an unsupported suffix")
        elif workload_kind == "weekly_okr":
            week_end, separator, remaining = workload_key.partition(":")
            manager_user_id, separator_2, source_digest = remaining.partition(":")
            try:
                if datetime.fromisoformat(week_end).strftime("%Y-%m-%d") != week_end:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("weekly_okr workload key must start with week end") from exc
            if manager_user_id != manager_user_id.strip():
                raise ValueError(
                    "weekly_okr manager_user_id must be canonical"
                )
            if (
                not separator
                or not separator_2
                or not manager_user_id.strip()
                or not source_digest.strip()
                or len(source_digest) != 64
                or any(char not in "0123456789abcdef" for char in source_digest)
            ):
                raise ValueError("weekly_okr workload key must be stable and complete")
        else:
            source, separator, source_id = workload_key.partition(":")
            if (
                source not in {
                    "memory_write_event",
                    "wechat_memory_candidate",
                    "wechat_memory_import_job",
                }
                or not separator
                or not source_id.isdecimal()
                or int(source_id) <= 0
            ):
                raise ValueError("memory workload key must name a persisted source row")
        return workload_kind, workload_key

    @staticmethod
    def _runtime_operation_parent_exists(
        db: sqlite3.Connection, workload_kind: str, workload_key: str
    ) -> bool:
        if workload_kind == "weekly_okr":
            week_end, manager_user_id, source_digest = workload_key.split(":", 2)
            return db.execute(
                "select 1 from weekly_okr_analysis_jobs "
                "where week_end=? and manager_user_id=? and source_digest=? "
                "and status='running'",
                (week_end, manager_user_id, source_digest),
            ).fetchone() is not None
        if workload_kind == "structured":
            query = "select 1 from okr_review_requests where id=? and status='processing'"
            args = (int(workload_key),)
        elif workload_kind == "meeting":
            query = "select 1 from meeting_alignment_runs where id=? and status='running'"
            args = (int(workload_key),)
        elif workload_kind == "task":
            row_id, separator, _ = workload_key.partition(":")
            if separator:
                query = (
                    "select 1 from work_projects where id=? "
                    "and status in ('active', 'waiting', 'done', 'archived')"
                )
            else:
                query = "select 1 from task_agent_runs where id=? and status='running'"
            args = (int(row_id),)
        else:
            source, _, row_id = workload_key.partition(":")
            query = {
                "memory_write_event": (
                    "select 1 from memory_write_events where id=? "
                    "and status in ('pending', 'failed')"
                ),
                "wechat_memory_candidate": (
                    "select 1 from wechat_memory_candidates where id=? "
                    "and status='approved' and memory_write_status='writing'"
                ),
                "wechat_memory_import_job": (
                    "select 1 from wechat_memory_import_jobs "
                    "where id=? and status='running'"
                ),
            }[source]
            args = (int(row_id),)
        return db.execute(query, args).fetchone() is not None

    @staticmethod
    def _validate_runtime_attempt_details(
        route_name: str,
        runtime_kind: str,
        credential_mode: str,
        model: str,
        session_mode: str | RuntimeAttemptSessionMode,
        source_session_id: str,
    ) -> tuple[str, str, str, str, str, str]:
        route_name = AutoReplyStore._require_runtime_attempt_text(
            route_name, field="route_name"
        )
        try:
            runtime_kind = RuntimeKind(runtime_kind).value
        except ValueError as exc:
            raise ValueError("unsupported runtime_kind") from exc
        try:
            credential_mode = CredentialMode(credential_mode).value
        except ValueError as exc:
            raise ValueError("unsupported credential_mode") from exc
        model = AutoReplyStore._require_runtime_attempt_text(model, field="model")
        try:
            session_mode = RuntimeAttemptSessionMode(session_mode).value
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported runtime attempt session_mode") from exc
        if not isinstance(source_session_id, str):
            raise TypeError("source_session_id must be a string")
        source_session_id = source_session_id.strip()
        if session_mode == RuntimeAttemptSessionMode.FRESH.value and source_session_id:
            raise ValueError("fresh session evidence requires empty source_session_id")
        if (
            session_mode == RuntimeAttemptSessionMode.RESUME.value
            and not source_session_id
        ):
            raise ValueError("resume session evidence requires source_session_id")
        return (
            route_name,
            runtime_kind,
            credential_mode,
            model,
            session_mode,
            source_session_id,
        )

    @staticmethod
    def _validate_runtime_failure(
        failure_class: str,
        failure_code: str,
        failover_permitted: bool,
    ) -> tuple[str, str, int]:
        try:
            failure_class = RuntimeFailureClass(failure_class).value
        except ValueError as exc:
            raise ValueError("unsupported runtime failure class") from exc
        failure_code = AutoReplyStore._require_runtime_attempt_text(
            failure_code, field="failure_code"
        )
        if not failure_code.replace("_", "").isalnum():
            raise ValueError("failure_code must be a typed code")
        if not isinstance(failover_permitted, bool):
            raise ValueError("failover_permitted must be a boolean")
        return failure_class, failure_code, int(failover_permitted)

    def _claim_runtime_attempt(
        self,
        *,
        agent_run_id: int | None,
        workload_kind: str,
        workload_key: str,
        route_name: str,
        runtime_kind: str,
        credential_mode: str,
        model: str,
        session_mode: str | RuntimeAttemptSessionMode,
        source_session_id: str,
        attempt_purpose: str = "normal",
        validation_retry_policy_id: str = "",
        validation_result_schema_id: str = "",
        owner: str = "",
        lease_seconds: int = 0,
        unknown_recovery_owner: str = "",
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt:
        (
            route_name,
            runtime_kind,
            credential_mode,
            model,
            session_mode,
            source_session_id,
        ) = (
            self._validate_runtime_attempt_details(
                route_name,
                runtime_kind,
                credential_mode,
                model,
                session_mode,
                source_session_id,
            )
        )
        if attempt_purpose not in {"normal", "result_validation_correction"}:
            raise ValueError("unsupported runtime attempt purpose")
        if attempt_purpose == "normal":
            if validation_retry_policy_id or validation_result_schema_id:
                raise ValueError("normal runtime attempt cannot carry correction lineage")
        else:
            validation_retry_policy_id = self._require_runtime_attempt_text(
                validation_retry_policy_id,
                field="validation_retry_policy_id",
            )
            validation_result_schema_id = self._require_runtime_attempt_text(
                validation_result_schema_id,
                field="validation_result_schema_id",
            )
        if agent_run_id is None:
            owner = self._require_runtime_attempt_text(owner, field="owner")
            if lease_seconds <= 0:
                raise ValueError("lease_seconds must be positive")
        elif unknown_recovery_owner:
            unknown_recovery_owner = self._require_runtime_attempt_text(
                unknown_recovery_owner, field="unknown_recovery_owner"
            )
            if session_mode != RuntimeAttemptSessionMode.FRESH.value:
                raise ValueError("unknown recovery runtime attempt must be fresh")
            if lease_seconds <= 0:
                raise ValueError("unknown recovery lease_seconds must be positive")
        with self._agent_run_write_transaction(now) as (db, (now_value, now_text)):
            lease_expires_at = (
                (now_value + timedelta(seconds=lease_seconds)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if agent_run_id is None or unknown_recovery_owner
                else ""
            )
            active_recovery_conflict = None
            if unknown_recovery_owner:
                active_recovery_conflict = db.execute(
                    "select * from agent_runtime_attempts "
                    "where workload_kind=? and workload_key=? "
                    "and status in ('starting', 'running') limit 1",
                    (workload_kind, workload_key),
                ).fetchone()
            if agent_run_id is not None:
                run = db.execute(
                    "select agent_runs.status, agent_runs.role, "
                    "agent_runs.side_effect_state, "
                    "agent_runs.effect_started_count, agent_runs.lease_owner, "
                    "agent_runs.lease_expires_at, "
                    "agent_runs.reconciliation_suspended, "
                    "agent_runs.operation_id, reply_tasks.status as task_status, "
                    "reply_tasks.execution_generation as task_execution_generation, "
                    "agent_runs.execution_generation "
                    "from agent_runs "
                    "join reply_tasks on reply_tasks.id=agent_runs.reply_task_id "
                    "where agent_runs.id=?",
                    (agent_run_id,),
                ).fetchone()
                if unknown_recovery_owner:
                    runtime_effect_boundary = db.execute(
                        "select 1 from agent_runtime_attempts "
                        "where agent_run_id=? and workload_kind='agent_run' "
                        "and workload_key=? and first_effect_started_at<>'' "
                        "limit 1",
                        (agent_run_id, str(agent_run_id)),
                    ).fetchone()
                    if (
                        run is None
                        or run["status"] != "unknown"
                        or run["role"] != AgentRole.AUDIT.value
                        or not run["operation_id"]
                        or run["task_status"] != "processing"
                        or run["execution_generation"]
                        != run["task_execution_generation"]
                        or run["side_effect_state"]
                        not in {
                            "unknown",
                            "confirmed",
                        }
                        # A provider crash can happen after the runtime boundary
                        # was durably crossed but before a normalized tool event
                        # increments the per-effect counters.  That boundary is
                        # sufficient to permit *only* fresh read-only
                        # reconciliation; rejecting it creates an unrecoverable
                        # `unknown` with no execution path.
                        or (
                            int(run["effect_started_count"]) <= 0
                            and runtime_effect_boundary is None
                        )
                        or run["lease_owner"] != unknown_recovery_owner
                        or run["lease_expires_at"] <= now_text
                        or int(run["reconciliation_suspended"]) != 0
                    ):
                        raise ValueError(
                            "unknown recovery agent run is not safely claimed"
                        )
                elif run is None or run["status"] != "running":
                    raise ValueError("agent run does not exist or is not running")
            elif not self._runtime_operation_parent_exists(
                db, workload_kind, workload_key
            ):
                raise ValueError(
                    "runtime operation parent does not exist or is not running"
                )
            if active_recovery_conflict is not None:
                if (
                    not active_recovery_conflict["lease_owner"]
                    or not active_recovery_conflict["lease_expires_at"]
                    or active_recovery_conflict["lease_expires_at"] > now_text
                    or active_recovery_conflict["first_effect_started_at"]
                ):
                    raise AgentRuntimeAttemptStartConflictError(
                        "unknown recovery runtime attempt start already claimed"
                    )
                cursor = db.execute(
                    "update agent_runtime_attempts set status='failed', "
                    "failure_class='process', "
                    "failure_code='runtime_recovery_lease_expired', "
                    "failover_permitted=0, lease_owner='', lease_expires_at='', "
                    "finished_at=?, updated_at=? "
                    "where id=? and status='running' and lease_owner=? "
                    "and lease_expires_at<=? and first_effect_started_at=''",
                    (
                        now_text,
                        now_text,
                        active_recovery_conflict["id"],
                        active_recovery_conflict["lease_owner"],
                        now_text,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AgentRuntimeAttemptStartConflictError(
                        "unknown recovery runtime attempt start already claimed"
                    )
            if db.execute(
                """
                select 1 from runtime_route_pauses
                where route_name=? and retry_at>?
                """,
                (route_name, now_text),
            ).fetchone() is not None:
                raise RuntimeRoutePausedError("runtime route is paused")
            active = db.execute(
                """
                select * from agent_runtime_attempts
                where workload_kind=? and workload_key=? and route_name=?
                  and status in ('starting', 'running')
                order by attempt_number
                """,
                (workload_kind, workload_key, route_name),
            ).fetchone()
            if active is not None:
                immutable = (
                    "runtime_kind",
                    "credential_mode",
                    "model",
                    "session_mode",
                    "source_session_id",
                    "attempt_purpose",
                    "validation_retry_policy_id",
                    "validation_result_schema_id",
                )
                if any(active[field] != value for field, value in zip(
                    immutable,
                    (
                        runtime_kind,
                        credential_mode,
                        model,
                        session_mode,
                        source_session_id,
                        attempt_purpose,
                        validation_retry_policy_id,
                        validation_result_schema_id,
                    ),
                    strict=True,
                )):
                    raise ValueError("conflicting active runtime attempt claim")
                return self._agent_runtime_attempt_from_row(active)
            attempt_number = int(
                db.execute(
                    """
                    select coalesce(max(attempt_number), 0) + 1 as attempt_number
                    from agent_runtime_attempts
                    where workload_kind=? and workload_key=?
                    """,
                    (workload_kind, workload_key),
                ).fetchone()["attempt_number"]
            )
            cursor = db.execute(
                """
                insert into agent_runtime_attempts (
                    agent_run_id, workload_kind, workload_key, attempt_number,
                    route_name, runtime_kind, credential_mode, model, session_mode,
                    source_session_id, attempt_purpose,
                    validation_retry_policy_id, validation_result_schema_id, status,
                    lease_owner, lease_expires_at,
                    started_at, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?)
                """,
                (
                    agent_run_id,
                    workload_kind,
                    workload_key,
                    attempt_number,
                    route_name,
                    runtime_kind,
                    credential_mode,
                    model,
                    session_mode,
                    source_session_id,
                    attempt_purpose,
                    validation_retry_policy_id,
                    validation_result_schema_id,
                    "running" if unknown_recovery_owner else "starting",
                    unknown_recovery_owner or owner,
                    lease_expires_at,
                    now_text,
                    now_text,
                    now_text,
                ),
            )
            row = db.execute(
                "select * from agent_runtime_attempts where id=?",
                (cursor.lastrowid,),
            ).fetchone()
            return self._agent_runtime_attempt_from_row(row)

    def claim_agent_runtime_attempt(
        self,
        agent_run_id: int,
        route_name: str,
        runtime_kind: str,
        credential_mode: str,
        model: str,
        *,
        session_mode: str | RuntimeAttemptSessionMode = RuntimeAttemptSessionMode.FRESH,
        source_session_id: str = "",
        attempt_purpose: str = "normal",
        validation_retry_policy_id: str = "",
        validation_result_schema_id: str = "",
    ) -> AgentRuntimeAttempt:
        if agent_run_id <= 0:
            raise ValueError("agent_run_id must be positive")
        return self._claim_runtime_attempt(
            agent_run_id=agent_run_id,
            workload_kind="agent_run",
            workload_key=str(agent_run_id),
            route_name=route_name,
            runtime_kind=runtime_kind,
            credential_mode=credential_mode,
            model=model,
            session_mode=session_mode,
            source_session_id=source_session_id,
            attempt_purpose=attempt_purpose,
            validation_retry_policy_id=validation_retry_policy_id,
            validation_result_schema_id=validation_result_schema_id,
        )


    def claim_runtime_operation_attempt(
        self,
        workload_kind: str,
        workload_key: str,
        route_name: str,
        runtime_kind: str,
        credential_mode: str,
        model: str,
        *,
        session_mode: str | RuntimeAttemptSessionMode = RuntimeAttemptSessionMode.FRESH,
        source_session_id: str = "",
        attempt_purpose: str = "normal",
        validation_retry_policy_id: str = "",
        validation_result_schema_id: str = "",
        owner: str = "legacy-runtime-owner",
        lease_seconds: int = 1800,
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt:
        workload_kind, workload_key = self._validate_runtime_operation_workload(
            workload_kind, workload_key
        )
        return self._claim_runtime_attempt(
            agent_run_id=None,
            workload_kind=workload_kind,
            workload_key=workload_key,
            route_name=route_name,
            runtime_kind=runtime_kind,
            credential_mode=credential_mode,
            model=model,
            session_mode=session_mode,
            source_session_id=source_session_id,
            attempt_purpose=attempt_purpose,
            validation_retry_policy_id=validation_retry_policy_id,
            validation_result_schema_id=validation_result_schema_id,
            owner=owner,
            lease_seconds=lease_seconds,
            now=now,
        )

    def _runtime_attempt_for_transition(
        self,
        db: sqlite3.Connection,
        attempt_id: int,
    ) -> sqlite3.Row:
        row = db.execute(
            "select * from agent_runtime_attempts where id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise ValueError("agent runtime attempt does not exist")
        return row

    @staticmethod
    def _require_runtime_attempt_owner(
        row: sqlite3.Row, *, owner: str, now_text: str
    ) -> None:
        if row["agent_run_id"] is not None:
            return
        if row["lease_owner"] != owner or row["lease_expires_at"] <= now_text:
            raise AgentRuntimeAttemptLeaseLostError(
                f"runtime attempt lease lost: {row['id']}"
            )

    def mark_agent_runtime_attempt_running(
        self,
        attempt_id: int,
    ) -> AgentRuntimeAttempt:
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            if row["status"] == "running":
                return self._agent_runtime_attempt_from_row(row)
            if row["status"] == "completed":
                raise ValueError("cannot transition from completed runtime attempt")
            if row["status"] in {"failed", "superseded"}:
                raise ValueError("cannot transition terminal runtime attempt to running")
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set status='running', updated_at=?
                where id=? and status='starting'
                """,
                (now_text, attempt_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("runtime attempt transition conflict")
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    def mark_agent_runtime_attempt_running_once(
        self,
        attempt_id: int,
        *,
        owner: str = "legacy-runtime-owner",
        lease_seconds: int = 1800,
        effectful: bool = False,
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt:
        """Acquire the one-shot process-start fence for a runtime attempt."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not isinstance(effectful, bool):
            raise TypeError("effectful must be a boolean")
        with self._agent_run_write_transaction(now) as (db, (now_value, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            if row["status"] != "starting":
                raise AgentRuntimeAttemptStartConflictError(
                    "runtime attempt process start already claimed"
                )
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set status='running',
                    lease_expires_at=case when agent_run_id is null then ?
                                          else lease_expires_at end,
                    first_effect_started_at=case
                        when agent_run_id is null and ? then ?
                        else first_effect_started_at end,
                    updated_at=?
                where id=? and status='starting'
                  and (agent_run_id is not null
                       or (lease_owner=? and lease_expires_at>?))
                """,
                (
                    (now_value + timedelta(seconds=lease_seconds)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    int(effectful),
                    now_text,
                    now_text,
                    attempt_id,
                    owner,
                    now_text,
                ),
            )
            if cursor.rowcount != 1:
                raise AgentRuntimeAttemptStartConflictError(
                    "runtime attempt process start already claimed"
                )
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    def complete_agent_runtime_attempt(
        self,
        attempt_id: int,
        session_id: str,
        transcript_reference: str,
        transcript_start: int,
        transcript_end: int,
        *,
        owner: str = "legacy-runtime-owner",
        result_schema_id: str = "",
        result_envelope_json: str = "",
        conversation_id: str = "",
        route_name: str = "",
        conversation_contract_hash: str = "",
        agent_run_final_result: dict[str, object] | None = None,
        agent_run_transcript_end: int | None = None,
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt:
        if not isinstance(session_id, str) or not isinstance(transcript_reference, str):
            raise TypeError("runtime attempt session and transcript reference must be strings")
        if transcript_start < 0 or transcript_end < transcript_start:
            raise ValueError("invalid runtime attempt transcript range")
        if result_schema_id:
            result_schema_id = self._require_runtime_attempt_text(
                result_schema_id, field="result_schema_id"
            )
            try:
                envelope = json.loads(result_envelope_json)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("result envelope must be valid JSON") from exc
            if not isinstance(envelope, dict) or envelope.get("schema_id") != result_schema_id:
                raise ValueError("result envelope schema mismatch")
            if (
                len(result_envelope_json.encode("utf-8"))
                > MAX_RUNTIME_RESULT_ENVELOPE_BYTES
            ):
                raise ValueError("result envelope exceeds size limit")
        elif result_envelope_json:
            raise ValueError("result schema is required")
        if agent_run_final_result is not None:
            if not result_schema_id:
                raise ValueError("agent run result reference requires result schema")
            if agent_run_transcript_end is None or agent_run_transcript_end < 0:
                raise ValueError("agent run transcript end is required")
            agent_run_final_result_json = _json_object_text(
                agent_run_final_result, field="agent_run_final_result"
            )
        else:
            agent_run_final_result_json = ""
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            expected = (
                session_id, transcript_reference, transcript_start, transcript_end,
                result_schema_id, result_envelope_json,
            )
            actual = (
                row["session_id"],
                row["transcript_reference"],
                row["transcript_start"],
                row["transcript_end"],
                row["result_schema_id"],
                row["result_envelope_json"],
            )
            if row["status"] == "completed":
                if actual == expected:
                    return self._agent_runtime_attempt_from_row(row)
                raise ValueError("conflicting terminal rewrite")
            self._require_runtime_attempt_owner(row, owner=owner, now_text=now_text)
            if row["status"] in {"failed", "superseded"}:
                raise ValueError("cannot complete terminal runtime attempt")
            if result_envelope_json and row["agent_run_id"] is not None:
                evidence = envelope.get("evidence")
                if isinstance(evidence, dict):
                    self._validate_runtime_result_evidence_snapshot(
                        db,
                        int(row["agent_run_id"]),
                        evidence,
                    )
            if agent_run_final_result is not None:
                if row["agent_run_id"] is None:
                    raise ValueError("agent run result reference requires agent run")
                reference = envelope.get("result_ref")
                if (
                    not isinstance(reference, dict)
                    or set(reference) != {"agent_run_id", "result_sha256"}
                    or reference.get("agent_run_id") != row["agent_run_id"]
                    or reference.get("result_sha256")
                    != hashlib.sha256(
                        agent_run_final_result_json.encode("utf-8")
                    ).hexdigest()
                ):
                    raise ValueError("agent run result reference mismatch")
                run_row = self._require_current_agent_run_write_access(
                    db,
                    int(row["agent_run_id"]),
                    owner=owner,
                    now_text=now_text,
                    expected_status="running",
                )
                if run_row["role"] != AgentRole.CONSUMER.value:
                    raise ValueError("agent run result reference requires Consumer")
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set status='completed', session_id=?, transcript_reference=?,
                    transcript_start=?, transcript_end=?, result_schema_id=?,
                    result_envelope_json=?, lease_owner='', lease_expires_at='',
                    finished_at=?, updated_at=?
                where id=? and status in ('starting', 'running')
                  and (agent_run_id is not null
                       or (lease_owner=? and lease_expires_at>?))
                """,
                (
                    session_id,
                    transcript_reference,
                    transcript_start,
                    transcript_end,
                    result_schema_id,
                    result_envelope_json,
                    now_text,
                    now_text,
                    attempt_id,
                    owner,
                    now_text,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("runtime attempt transition conflict")
            if conversation_id and session_id:
                self._upsert_conversation_runtime_session_in_connection(
                    db,
                    conversation_id,
                    route_name,
                    session_id,
                    conversation_contract_hash,
                    now_text,
                )
            if agent_run_final_result is not None:
                run_cursor = db.execute(
                    """
                    update agent_runs
                    set status='completed', final_result_json=?,
                        structured_error_json='',
                        transcript_end_line=?, lease_owner='', lease_expires_at='',
                        completed_at=?, updated_at=?
                    where id=? and status='running' and lease_owner=?
                      and lease_expires_at>?
                    """,
                    (
                        agent_run_final_result_json,
                        agent_run_transcript_end,
                        now_text,
                        now_text,
                        row["agent_run_id"],
                        owner,
                        now_text,
                    ),
                )
                if run_cursor.rowcount != 1:
                    raise AgentRunLeaseLostError(
                        f"agent run lease lost: {row['agent_run_id']}"
                    )
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    @staticmethod
    def _validate_runtime_result_evidence_snapshot(
        db: sqlite3.Connection,
        run_id: int,
        evidence: dict[str, object],
    ) -> None:
        event_start = evidence.get("event_start")
        event_end = evidence.get("event_end")
        if (
            type(event_start) is not int
            or type(event_end) is not int
            or event_start < 0
            or event_end < event_start
        ):
            raise ValueError("runtime result evidence bounds invalid")
        event_rows = db.execute(
            "select event_json from agent_run_events "
            "where agent_run_id=? order by sequence",
            (run_id,),
        ).fetchall()
        events = [json.loads(row["event_json"]) for row in event_rows]
        receipt_rows = db.execute(
            "select * from agent_execution_receipts "
            "where agent_run_id=? order by id",
            (run_id,),
        ).fetchall()
        receipts = [
            {
                "receipt_id": str(receipt["receipt_id"]),
                "operation_id": str(receipt["operation_id"]),
                "cli": str(receipt["cli"]),
                "command_path": str(receipt["command_path"]),
                "command_digest": str(receipt["command_digest"]),
                "exit_code": int(receipt["exit_code"]),
                "completed": bool(receipt["completed"]),
                "persisted": bool(receipt["persisted"]),
                "safe_to_confirm": bool(receipt["safe_to_confirm"]),
                "effect_counted": bool(receipt["effect_counted"]),
            }
            for receipt in receipt_rows
        ]
        if (
            event_end != len(events)
            or evidence.get("events_sha256")
            != _canonical_json_sha256(events[event_start:event_end])
            or evidence.get("receipts_sha256") != _canonical_json_sha256(receipts)
        ):
            raise ValueError("runtime result evidence changed before completion")

    def renew_runtime_operation_attempt_lease(
        self,
        attempt_id: int,
        *,
        owner: str,
        lease_seconds: int,
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._agent_run_write_transaction(now) as (db, (now_value, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            if row["agent_run_id"] is not None:
                raise ValueError("operation lease requires generalized workload")
            self._require_runtime_attempt_owner(row, owner=owner, now_text=now_text)
            expires_at = (
                now_value + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%d %H:%M:%S")
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set lease_expires_at=?, updated_at=?
                where id=? and status in ('starting', 'running')
                  and lease_owner=? and lease_expires_at>?
                """,
                (expires_at, now_text, attempt_id, owner, now_text),
            )
            if cursor.rowcount != 1:
                raise AgentRuntimeAttemptLeaseLostError(
                    f"runtime attempt lease lost: {attempt_id}"
                )
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    def recover_expired_runtime_operation_attempt(
        self,
        workload_kind: str,
        workload_key: str,
        *,
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt | None:
        workload_kind, workload_key = self._validate_runtime_operation_workload(
            workload_kind, workload_key
        )
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = db.execute(
                """
                select * from agent_runtime_attempts
                where agent_run_id is null and workload_kind=? and workload_key=?
                  and status in ('starting', 'running')
                order by attempt_number desc limit 1
                """,
                (workload_kind, workload_key),
            ).fetchone()
            if row is None or row["lease_expires_at"] > now_text:
                return None
            if row["first_effect_started_at"]:
                return self._agent_runtime_attempt_from_row(row)
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set status='failed', failure_class='process',
                    failure_code='runtime_lease_expired', failover_permitted=1,
                    lease_owner='', lease_expires_at='', finished_at=?, updated_at=?
                where id=? and status in ('starting', 'running')
                  and first_effect_started_at='' and lease_expires_at<=?
                """,
                (now_text, now_text, row["id"], now_text),
            )
            if cursor.rowcount != 1:
                return None
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, row["id"])
            )

    def recover_expired_terminal_task_runtime_attempts(
        self,
        *,
        now: str | datetime | None = None,
    ) -> int:
        """Close read-only task attempts abandoned after their task run ended."""
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            cursor = db.execute(
                """
                update agent_runtime_attempts as attempt
                set status='failed', failure_class='process',
                    failure_code='runtime_lease_expired', failover_permitted=1,
                    lease_owner='', lease_expires_at='', finished_at=?, updated_at=?
                where attempt.agent_run_id is null
                  and attempt.workload_kind='task'
                  and attempt.workload_key not like '%:%'
                  and attempt.status in ('starting', 'running')
                  and attempt.first_effect_started_at=''
                  and attempt.lease_expires_at!=''
                  and attempt.lease_expires_at<=?
                  and exists (
                      select 1
                      from task_agent_runs as task_run
                      where cast(task_run.id as text)=attempt.workload_key
                        and task_run.status in ('completed', 'failed')
                  )
                """,
                (now_text, now_text, now_text),
            )
            return cursor.rowcount

    def recover_stale_runtime_attempts(
        self,
        *,
        stale_after_seconds: int,
        now: str | datetime | None = None,
    ) -> int:
        """Close runtime attempts that can no longer be active.

        A runtime attempt is an execution record, not a business result.  Once
        its lease has expired, it has no live owner; an attempt whose parent
        run is already terminal is stale for the same reason.  Closing these
        records keeps startup and monitoring state truthful without trying to
        infer or replay any provider action.
        """
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        with self._agent_run_write_transaction(now) as (db, (now_value, now_text)):
            stale_before = (
                now_value - timedelta(seconds=stale_after_seconds)
            ).strftime("%Y-%m-%d %H:%M:%S")
            cursor = db.execute(
                """
                update agent_runtime_attempts as attempt
                set status='failed', failure_class='process',
                    failure_code='runtime_lease_expired', failover_permitted=1,
                    lease_owner='', lease_expires_at='', finished_at=?, updated_at=?
                where attempt.status in ('starting', 'running')
                  and (
                    (attempt.lease_expires_at!='' and attempt.lease_expires_at<=?)
                    or (attempt.lease_expires_at='' and attempt.updated_at<=?)
                    or (
                      attempt.agent_run_id is not null
                      and exists (
                        select 1 from agent_runs parent
                        where parent.id=attempt.agent_run_id
                          and parent.status in ('completed', 'failed')
                      )
                    )
                  )
                """,
                (now_text, now_text, now_text, stale_before),
            )
            return cursor.rowcount

    def set_agent_runtime_attempt_session(
        self,
        attempt_id: int,
        session_id: str,
        transcript_reference: str | None = None,
        *,
        owner: str = "legacy-runtime-owner",
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt:
        session_id = self._require_runtime_attempt_text(
            session_id, field="session_id"
        )
        if transcript_reference is not None and not isinstance(
            transcript_reference, str
        ):
            raise TypeError("transcript_reference must be a string")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            selected_reference = (
                row["transcript_reference"]
                if transcript_reference is None
                else transcript_reference
            )
            if row["status"] not in {"starting", "running"}:
                if (
                    row["session_id"] == session_id
                    and row["transcript_reference"] == selected_reference
                ):
                    return self._agent_runtime_attempt_from_row(row)
                raise ValueError("cannot mutate terminal runtime attempt")
            self._require_runtime_attempt_owner(row, owner=owner, now_text=now_text)
            db.execute(
                """
                update agent_runtime_attempts
                set session_id=?, transcript_reference=?, updated_at=?
                where id=? and status in ('starting', 'running')
                  and (agent_run_id is not null
                       or (lease_owner=? and lease_expires_at>?))
                """,
                (
                    session_id, selected_reference, now_text, attempt_id,
                    owner, now_text,
                ),
            )
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    def fail_agent_runtime_attempt(
        self,
        attempt_id: int,
        failure_class: str,
        failure_code: str,
        failover_permitted: bool,
        *,
        session_id: str | None = None,
        transcript_reference: str | None = None,
        transcript_start: int | None = None,
        transcript_end: int | None = None,
        owner: str = "legacy-runtime-owner",
        now: str | datetime | None = None,
    ) -> AgentRuntimeAttempt:
        failure_class, failure_code, failover_permitted = self._validate_runtime_failure(
            failure_class, failure_code, failover_permitted
        )
        if session_id is not None and not isinstance(session_id, str):
            raise TypeError("runtime attempt session and transcript reference must be strings")
        if transcript_reference is not None and not isinstance(transcript_reference, str):
            raise TypeError("runtime attempt session and transcript reference must be strings")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            session_id = row["session_id"] if session_id is None else session_id
            transcript_reference = (
                row["transcript_reference"]
                if transcript_reference is None
                else transcript_reference
            )
            transcript_start = (
                row["transcript_start"]
                if transcript_start is None
                else transcript_start
            )
            transcript_end = (
                row["transcript_end"] if transcript_end is None else transcript_end
            )
            if transcript_start < 0 or transcript_end < transcript_start:
                raise ValueError("invalid runtime attempt transcript range")
            expected = (
                failure_class,
                failure_code,
                failover_permitted,
                session_id,
                transcript_reference,
                transcript_start,
                transcript_end,
            )
            actual = (
                row["failure_class"],
                row["failure_code"],
                row["failover_permitted"],
                row["session_id"],
                row["transcript_reference"],
                row["transcript_start"],
                row["transcript_end"],
            )
            if row["status"] == "failed":
                if actual == expected:
                    return self._agent_runtime_attempt_from_row(row)
                raise ValueError("conflicting terminal rewrite")
            self._require_runtime_attempt_owner(row, owner=owner, now_text=now_text)
            if row["status"] == "completed":
                raise ValueError("cannot transition from completed runtime attempt")
            if row["status"] == "superseded":
                raise ValueError("cannot fail terminal runtime attempt")
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set status='failed', failure_class=?, failure_code=?,
                    failover_permitted=?, session_id=?, transcript_reference=?,
                    transcript_start=?, transcript_end=?, lease_owner='',
                    lease_expires_at='', finished_at=?, updated_at=?
                where id=? and status in ('starting', 'running')
                  and (agent_run_id is not null
                       or (lease_owner=? and lease_expires_at>?))
                """,
                (
                    failure_class,
                    failure_code,
                    failover_permitted,
                    session_id,
                    transcript_reference,
                    transcript_start,
                    transcript_end,
                    now_text,
                    now_text,
                    attempt_id,
                    owner,
                    now_text,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("runtime attempt transition conflict")
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    def mark_agent_runtime_attempt_superseded(
        self,
        attempt_id: int,
    ) -> AgentRuntimeAttempt:
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            if row["status"] == "superseded":
                return self._agent_runtime_attempt_from_row(row)
            if row["status"] == "completed":
                raise ValueError("cannot transition from completed runtime attempt")
            if row["status"] != "failed":
                raise ValueError("only failed runtime attempts can be superseded")
            successor = db.execute(
                """
                select 1 from agent_runtime_attempts
                where workload_kind=? and workload_key=? and attempt_number>?
                limit 1
                """,
                (row["workload_kind"], row["workload_key"], row["attempt_number"]),
            ).fetchone()
            if successor is None:
                raise ValueError("runtime attempt requires a durably claimed successor")
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set status='superseded', updated_at=?
                where id=? and status='failed'
                """,
                (now_text, attempt_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("runtime attempt transition conflict")
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    def list_agent_runtime_attempts(
        self,
        agent_run_id: int,
    ) -> list[AgentRuntimeAttempt]:
        with self._connect() as db:
            rows = db.execute(
                """
                select * from agent_runtime_attempts
                where agent_run_id=?
                order by attempt_number
                """,
                (agent_run_id,),
            ).fetchall()
            return [self._agent_runtime_attempt_from_row(row) for row in rows]

    def list_runtime_operation_attempts(
        self,
        workload_kind: str,
        workload_key: str,
    ) -> list[AgentRuntimeAttempt]:
        workload_kind, workload_key = self._validate_runtime_operation_workload(
            workload_kind, workload_key
        )
        with self._connect() as db:
            rows = db.execute(
                """
                select * from agent_runtime_attempts
                where agent_run_id is null
                  and workload_kind=? and workload_key=?
                order by attempt_number
                """,
                (workload_kind, workload_key),
            ).fetchall()
            return [self._agent_runtime_attempt_from_row(row) for row in rows]

    def runtime_operation_parent_is_runnable(
        self,
        workload_kind: str,
        workload_key: str,
    ) -> bool:
        workload_kind, workload_key = self._validate_runtime_operation_workload(
            workload_kind, workload_key
        )
        with self._connect() as db:
            return self._runtime_operation_parent_exists(
                db, workload_kind, workload_key
            )

    def get_agent_runtime_attempt(self, attempt_id: int) -> AgentRuntimeAttempt | None:
        with self._connect() as db:
            row = db.execute(
                "select * from agent_runtime_attempts where id=?", (attempt_id,)
            ).fetchone()
            return None if row is None else self._agent_runtime_attempt_from_row(row)

    def note_runtime_attempt_effect_started(
        self,
        attempt_id: int,
        at: str | datetime | None = None,
        *,
        owner: str = "legacy-runtime-owner",
    ) -> AgentRuntimeAttempt:
        _, effect_started_at = _utc_store_time(at)
        with self._agent_run_write_transaction(at) as (db, (_, now_text)):
            row = self._runtime_attempt_for_transition(db, attempt_id)
            if row["status"] == "completed":
                raise ValueError("cannot mutate completed runtime attempt")
            if row["status"] in {"failed", "superseded"}:
                raise ValueError("cannot mutate terminal runtime attempt")
            self._require_runtime_attempt_owner(row, owner=owner, now_text=now_text)
            if row["first_effect_started_at"]:
                return self._agent_runtime_attempt_from_row(row)
            cursor = db.execute(
                """
                update agent_runtime_attempts
                set first_effect_started_at=?, updated_at=?
                where id=? and status in ('starting', 'running')
                  and first_effect_started_at=''
                  and (agent_run_id is not null
                       or (lease_owner=? and lease_expires_at>?))
                """,
                (effect_started_at, now_text, attempt_id, owner, now_text),
            )
            if cursor.rowcount != 1:
                raise ValueError("runtime attempt transition conflict")
            return self._agent_runtime_attempt_from_row(
                self._runtime_attempt_for_transition(db, attempt_id)
            )

    def authorize_claude_effect_dispatch(
        self,
        *,
        run_id: int,
        attempt_id: int,
        owner: str,
        event: dict[str, object],
        expected_action: dict[str, object],
        required_skill_receipts: tuple[tuple[str, str, str], ...] = (),
        now: str | datetime | None = None,
    ) -> ClaudeEffectDispatchClaim:
        """Persist one exact Claude effect start before allowing target dispatch."""
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        event_text = _json_object_text(event, field="event")
        if len(event_text.encode("utf-8")) > MAX_AGENT_RUN_EVENT_BYTES:
            raise ValueError("agent run event exceeds size limit")
        normalized_event = json.loads(event_text)
        event_type, call_id, effect_kind, receipt_operation_id = (
            _agent_event_columns(normalized_event)
        )
        item = normalized_event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if (
            event_type != "item.started"
            or effect_kind != "effectful"
            or receipt_operation_id
            or not call_id
            or not isinstance(metadata, dict)
            or any(metadata.get(key) != value for key, value in expected_action.items())
        ):
            raise ValueError("Claude effect dispatch identity mismatch")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            run_row = self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                status_error="Claude effect dispatch requires running Audit",
            )
            if run_row["role"] != AgentRole.AUDIT.value:
                raise ValueError("Claude effect dispatch requires Audit")
            if metadata.get("operation_id") != run_row["operation_id"]:
                raise ValueError("effect operation identity mismatch")
            attempt_row = self._runtime_attempt_for_transition(db, attempt_id)
            if (
                attempt_row["agent_run_id"] != run_id
                or attempt_row["route_name"] != "claude_api"
                or attempt_row["runtime_kind"] != "claude_cli"
                or attempt_row["status"] != "running"
            ):
                raise ValueError("Claude effect dispatch attempt is not active")
            if required_skill_receipts:
                rows = db.execute(
                    "select event_json from agent_run_events "
                    "where agent_run_id=? and event_type='item.completed'",
                    (run_id,),
                ).fetchall()
                observed: set[tuple[str, str, str]] = set()
                for row in rows:
                    try:
                        persisted_event = json.loads(row["event_json"])
                    except json.JSONDecodeError:
                        continue
                    persisted_item = persisted_event.get("item")
                    persisted_metadata = (
                        persisted_item.get("metadata")
                        if isinstance(persisted_item, dict)
                        else None
                    )
                    if isinstance(persisted_metadata, dict):
                        identity = tuple(
                            str(persisted_metadata.get(key) or "")
                            for key in ("skill_name", "skill_path", "skill_sha256")
                        )
                        if all(identity):
                            observed.add(identity)
                if not set(required_skill_receipts).issubset(observed):
                    raise ValueError("Claude effect dispatch skill receipt missing")
            prior = db.execute(
                "select event_json from agent_run_events "
                "where agent_run_id=? and call_id=? and event_type='item.started' "
                "order by sequence",
                (run_id, call_id),
            ).fetchall()
            if prior:
                if len(prior) == 1 and prior[0]["event_json"] == event_text:
                    return ClaudeEffectDispatchClaim(dispatch_acquired=False)
                raise ValueError("Claude effect dispatch call identity conflict")
            sequence = db.execute(
                "select coalesce(max(sequence), 0) + 1 from agent_run_events "
                "where agent_run_id=?",
                (run_id,),
            ).fetchone()[0]
            db.execute(
                """
                insert into agent_run_events (
                    agent_run_id, sequence, event_json, event_type,
                    call_id, effect_kind, receipt_operation_id, event_scope, created_at
                ) values (?, ?, ?, 'item.started', ?, 'effectful', '', 'direct', ?)
                """,
                (run_id, sequence, event_text, call_id, now_text),
            )
            attempt_cursor = db.execute(
                """
                update agent_runtime_attempts
                set first_effect_started_at=?, updated_at=?
                where id=? and agent_run_id=? and status='running'
                  and first_effect_started_at=''
                """,
                (now_text, now_text, attempt_id, run_id),
            )
            if attempt_cursor.rowcount != 1:
                raise ValueError("Claude effect dispatch attempt conflict")
            run_cursor = db.execute(
                """
                update agent_runs
                set effect_started_count=effect_started_count+1,
                    side_effect_state='unknown',
                    transcript_end_line=transcript_end_line+1,
                    updated_at=?
                where id=? and status='running' and lease_owner=?
                  and lease_expires_at>?
                """,
                (now_text, run_id, owner, now_text),
            )
            if run_cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
            return ClaudeEffectDispatchClaim(dispatch_acquired=True)

    @staticmethod
    def _require_current_agent_run_write_access(
        db: sqlite3.Connection,
        run_id: int,
        *,
        owner: str,
        now_text: str,
        expected_status: str = "running",
        status_error: str | None = None,
    ) -> sqlite3.Row:
        row = db.execute(
            """
            select agent_runs.*,
                   reply_tasks.execution_generation as task_execution_generation
            from agent_runs
            join reply_tasks on reply_tasks.id=agent_runs.reply_task_id
            where agent_runs.id=?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("agent run does not exist")
        if row["execution_generation"] != row["task_execution_generation"]:
            raise AgentRunLeaseLostError(f"agent run superseded: {run_id}")
        if row["status"] != expected_status:
            raise ValueError(
                status_error or f"agent run write requires {expected_status} status"
            )
        if row["lease_owner"] != owner or row["lease_expires_at"] <= now_text:
            raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
        return row

    @staticmethod
    def _supersede_running_agent_runs(
        db: sqlite3.Connection,
        task_id: int,
        current_generation: str,
        *,
        now_text: str,
    ) -> None:
        error_json = json.dumps(
            {"code": "superseded_by_new_generation", "retryable": False},
            separators=(",", ":"),
        )
        db.execute(
            """
            update agent_runs
            set status='failed',
                structured_error_json=?,
                lease_owner='', lease_expires_at='',
                completed_at=?,
                updated_at=?
            where reply_task_id=? and execution_generation=? and status='running'
              and not exists (
                  select 1 from agent_effect_intents
                  where agent_run_id=agent_runs.id and state='dispatched'
              )
            """,
            (error_json, now_text, now_text, task_id, current_generation),
        )

    def claim_agent_run(
        self,
        reply_task_id: int,
        execution_generation: str,
        *,
        role: AgentRole,
        proposal_revision: int,
        turn_attempt: int,
        parent_agent_run_id: int | None,
        operation_id: str,
        owner: str,
        lease_seconds: int = 1800,
        now: str | datetime | None = None,
    ) -> AgentRunClaim:
        role = AgentRole(role)
        if not execution_generation.strip():
            raise ValueError("execution_generation must be non-empty")
        if proposal_revision < 0:
            raise ValueError("proposal_revision must not be negative")
        if turn_attempt < 0:
            raise ValueError("turn_attempt must not be negative")
        if parent_agent_run_id is not None and parent_agent_run_id <= 0:
            raise ValueError("parent_agent_run_id must be positive")
        operation_id = operation_id.strip()
        if role is AgentRole.CONSUMER and operation_id:
            raise ValueError("Consumer operation_id must be empty")
        if role is AgentRole.AUDIT and not operation_id:
            raise ValueError("Audit operation_id must be non-empty")
        if (
            role is AgentRole.CONSUMER
            and proposal_revision == 0
            and parent_agent_run_id is not None
        ):
            raise ValueError("Initial Consumer parent must be empty")
        if (
            role is AgentRole.CONSUMER
            and proposal_revision > 0
            and parent_agent_run_id is None
        ):
            raise ValueError("Revised Consumer parent must be non-empty")
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._agent_run_write_transaction(now) as (
            db,
            (now_value, now_text),
        ):
            lease_expires_at = (
                now_value + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%d %H:%M:%S")
            task = db.execute(
                "select execution_generation from reply_tasks where id=?",
                (reply_task_id,),
            ).fetchone()
            if task is None:
                raise ValueError("reply task does not exist")
            if task["execution_generation"] != execution_generation:
                raise ValueError("reply task execution generation mismatch")
            parent = None
            if role is AgentRole.AUDIT and parent_agent_run_id is not None:
                parent = db.execute(
                    """
                    select id from agent_runs
                    where id=? and role='consumer' and reply_task_id=?
                      and execution_generation=? and proposal_revision=?
                    """,
                    (
                        parent_agent_run_id,
                        reply_task_id,
                        execution_generation,
                        proposal_revision,
                    ),
                ).fetchone()
                if parent is None:
                    raise ValueError(
                        "Audit parent must be the matching Consumer turn"
                    )
            if role is AgentRole.CONSUMER and proposal_revision > 0:
                parent = db.execute(
                    """
                    select id from agent_runs
                    where id=? and role='audit' and reply_task_id=?
                      and execution_generation=? and proposal_revision=?
                    """,
                    (
                        parent_agent_run_id,
                        reply_task_id,
                        execution_generation,
                        proposal_revision - 1,
                    ),
                ).fetchone()
                if parent is None:
                    raise ValueError(
                        "Revised Consumer parent must be the previous Audit turn"
                    )
            cursor = db.execute(
                """
                insert or ignore into agent_runs (
                    reply_task_id, execution_generation, role,
                    proposal_revision, turn_attempt, parent_agent_run_id,
                    operation_id, status,
                    lease_owner, lease_expires_at, started_at,
                    created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                """,
                (
                    reply_task_id,
                    execution_generation,
                    role.value,
                    proposal_revision,
                    turn_attempt,
                    parent_agent_run_id,
                    operation_id,
                    owner,
                    lease_expires_at,
                    now_text,
                    now_text,
                    now_text,
                ),
            )
            claimed = cursor.rowcount == 1
            row = db.execute(
                """
                select *
                from agent_runs
                where reply_task_id=? and execution_generation=? and role=?
                  and proposal_revision=? and turn_attempt=?
                """,
                (
                    reply_task_id,
                    execution_generation,
                    role.value,
                    proposal_revision,
                    turn_attempt,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("agent run claim did not create a row")
            if (
                row["parent_agent_run_id"] != parent_agent_run_id
                or row["operation_id"] != operation_id
            ):
                raise ValueError("conflicting agent turn identity")
            if (
                not claimed
                and row["status"] == "running"
                and row["lease_expires_at"] <= now_text
            ):
                reclaimed = db.execute(
                    """update agent_runs set lease_owner=?, lease_expires_at=?, updated_at=?
                    where id=? and status='running' and lease_expires_at<=?""",
                    (owner, lease_expires_at, now_text, row["id"], now_text),
                )
                claimed = reclaimed.rowcount == 1
                row = db.execute("select * from agent_runs where id=?", (row["id"],)).fetchone()
            if (
                not claimed
                and role is AgentRole.AUDIT
                and row["status"] == "failed"
            ):
                try:
                    structured_error = json.loads(row["structured_error_json"])
                except json.JSONDecodeError:
                    structured_error = {}
                retryable = (
                    isinstance(structured_error, dict)
                    and structured_error.get("retryable") is True
                    and row["side_effect_state"] == "none"
                )
                if retryable:
                    reclaimed = db.execute(
                        """
                        update agent_runs
                        set status='running', lease_owner=?, lease_expires_at=?,
                            transcript_start_line=transcript_end_line,
                            final_result_json='', structured_error_json='',
                            completed_at='', updated_at=?
                        where id=? and status='failed'
                          and side_effect_state='none'
                        """,
                        (owner, lease_expires_at, now_text, row["id"]),
                    )
                    claimed = reclaimed.rowcount == 1
                    row = db.execute(
                        "select * from agent_runs where id=?",
                        (row["id"],),
                    ).fetchone()
            return AgentRunClaim(
                run=self._agent_run_from_row(row, db=db),
                claimed=claimed,
            )

    def renew_agent_run_lease(
        self,
        run_id: int,
        *,
        owner: str,
        lease_seconds: int = 1800,
        expected_status: str = "running",
        now: str | datetime | None = None,
    ) -> AgentRun:
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if expected_status not in {"running", "unknown"}:
            raise ValueError("invalid agent run lease status")
        with self._agent_run_write_transaction(now) as (
            db,
            (now_value, now_text),
        ):
            self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                expected_status=expected_status,
                status_error=f"agent run lease requires {expected_status} status",
            )
            lease_expires_at = (
                now_value + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%d %H:%M:%S")
            cursor = db.execute(
                """
                update agent_runs
                set lease_expires_at=?, updated_at=?
                where id=? and status=? and lease_owner=?
                  and lease_expires_at>?
                """,
                (
                    lease_expires_at,
                    now_text,
                    run_id,
                    expected_status,
                    owner,
                    now_text,
                ),
            )
            if cursor.rowcount != 1:
                row = db.execute(
                    "select * from agent_runs where id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("agent run does not exist")
                if row["status"] != expected_status:
                    raise ValueError(
                        f"agent run lease requires {expected_status} status"
                    )
                raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
            updated = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            return self._agent_run_from_row(updated, db=db)

    def set_agent_run_session(
        self,
        run_id: int,
        codex_session_id: str,
        *,
        owner: str,
        transcript_start_line: int = 0,
        allow_consumer_session_handoff: bool = False,
        now: str | datetime | None = None,
    ) -> AgentRun:
        if not codex_session_id.strip():
            raise ValueError("codex_session_id must be non-empty")
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        if transcript_start_line < 0:
            raise ValueError("transcript_start_line must not be negative")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                status_error="agent run session requires running status",
            )
            current = db.execute(
                """
                select role, codex_session_id, side_effect_state
                from agent_runs
                where id=?
                """,
                (run_id,),
            ).fetchone()
            if current is None:
                raise ValueError("agent run does not exist")
            current_session_id = str(current["codex_session_id"] or "")
            replace_session = (
                allow_consumer_session_handoff
                and current["role"] == AgentRole.CONSUMER.value
                and current["side_effect_state"] == "none"
                and bool(current_session_id)
                and current_session_id != codex_session_id
                and db.execute(
                    """
                    select 1 from agent_execution_receipts
                    where agent_run_id=? and completed=1 and persisted=1
                    limit 1
                    """,
                    (run_id,),
                ).fetchone()
                is None
            )
            cursor = db.execute(
                """
                update agent_runs
                set codex_session_id=case
                        when codex_session_id='' or ? then ? else codex_session_id
                    end,
                    transcript_start_line=case
                        when codex_session_id='' or ? then ? else transcript_start_line
                    end,
                    transcript_end_line=max(transcript_end_line, ?),
                    updated_at=?
                where id=? and status='running' and lease_owner=?
                  and lease_expires_at>?
                  and (codex_session_id='' or codex_session_id=? or ?)
                """,
                (
                    int(replace_session),
                    codex_session_id,
                    int(replace_session),
                    transcript_start_line,
                    transcript_start_line,
                    now_text,
                    run_id,
                    owner,
                    now_text,
                    codex_session_id,
                    int(replace_session),
                ),
            )
            if cursor.rowcount != 1:
                row = db.execute(
                    "select * from agent_runs where id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("agent run does not exist")
                if row["status"] != "running":
                    raise ValueError("agent run session requires running status")
                if (
                    row["lease_owner"] != owner
                    or row["lease_expires_at"] <= now_text
                ):
                    raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
                raise ValueError("agent run session cannot be replaced")
            updated = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            return self._agent_run_from_row(updated, db=db)

    def append_agent_run_event(
        self,
        run_id: int,
        event: dict[str, object],
        *,
        owner: str,
        now: str | datetime | None = None,
    ) -> AgentRun:
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        event_text = _json_object_text(event, field="event")
        normalized_event = json.loads(event_text)
        event_type, call_id, effect_kind, receipt_operation_id = (
            _agent_event_columns(normalized_event)
        )
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                status_error="cannot append event to terminal agent run",
            )
            sequence = db.execute(
                "select coalesce(max(sequence), 0) + 1 from agent_run_events "
                "where agent_run_id=?",
                (run_id,),
            ).fetchone()[0]
            db.execute(
                """
                insert into agent_run_events (
                    agent_run_id, sequence, event_json, event_type,
                    call_id, effect_kind, receipt_operation_id, event_scope, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'direct', ?)
                """,
                (
                    run_id,
                    sequence,
                    event_text,
                    event_type,
                    call_id,
                    effect_kind,
                    receipt_operation_id,
                    now_text,
                ),
            )
            receipt_delta = 0
            if receipt_operation_id:
                call_state = db.execute(
                    """
                    select
                        sum(case when effect_kind='effectful'
                                  and event_type='item.started' then 1 else 0 end)
                            as starts,
                        sum(case when effect_kind='effectful'
                                  and event_type in ('item.completed', 'item.failed')
                                 then 1 else 0 end) as closures,
                        sum(case when receipt_operation_id=? then 1 else 0 end)
                            as receipts
                    from agent_run_events
                    where agent_run_id=?
                      and (call_id=? or receipt_operation_id=?)
                    """,
                    (
                        receipt_operation_id,
                        run_id,
                        receipt_operation_id,
                        receipt_operation_id,
                    ),
                ).fetchone()
                receipt_delta = int(
                    (call_state["starts"] or 0)
                    > (call_state["closures"] or 0) + (call_state["receipts"] or 0) - 1
                )
            started_delta = int(
                effect_kind == "effectful" and event_type == "item.started"
            )
            completed_delta = int(
                effect_kind == "effectful" and event_type == "item.completed"
            )
            failed_delta = int(
                effect_kind == "effectful" and event_type == "item.failed"
            )
            unreviewed_delta = int(effect_kind == "unreviewed")
            db.execute(
                """
                update agent_runs
                set effect_started_count=effect_started_count+?,
                    effect_completed_count=effect_completed_count+?,
                    effect_failed_count=effect_failed_count+?,
                    effect_receipt_count=effect_receipt_count+?,
                    effect_unreviewed_count=effect_unreviewed_count+?
                where id=?
                """,
                (
                    started_delta,
                    completed_delta,
                    failed_delta,
                    receipt_delta,
                    unreviewed_delta,
                    run_id,
                ),
            )
            cursor = db.execute(
                """
                update agent_runs
                set transcript_end_line=transcript_end_line + 1,
                    updated_at=?
                where id=? and status='running' and lease_owner=?
                  and lease_expires_at>?
                """,
                (now_text, run_id, owner, now_text),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
            updated = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            return self._agent_run_from_row(updated, db=db, load_events=False)

    @staticmethod
    def _validate_agent_effect_event_identity(
        db: sqlite3.Connection,
        run_id: int,
        event: dict[str, object],
        *,
        event_type: str,
        call_id: str,
        effect_kind: str,
    ) -> None:
        if (
            effect_kind != "effectful"
            or event_type not in {"item.completed", "item.failed"}
            or not call_id
        ):
            return
        row = db.execute(
            """
            select event_json from agent_run_events
            where agent_run_id=? and call_id=? and effect_kind='effectful'
              and event_type='item.started'
            order by sequence desc limit 1
            """,
            (run_id, call_id),
        ).fetchone()
        if row is None:
            raise ValueError("effect completion requires matching start")
        try:
            started = json.loads(row["event_json"])
        except json.JSONDecodeError as exc:
            raise ValueError("effect start identity is invalid") from exc
        if _agent_effect_identity(started) != _agent_effect_identity(event):
            raise ValueError("effect completion identity mismatch")


    def _transition_agent_run(
        self,
        run_id: int,
        *,
        expected_status: str,
        owner: str | None,
        target_status: str,
        final_result_json: str,
        structured_error_json: str,
        side_effect_state: str,
        transcript_end_line: int | None,
        now: str | datetime | None,
    ) -> AgentRun:
        if owner is None or not owner.strip():
            raise ValueError("owner must be non-empty")
        if expected_status not in {"running", "unknown"}:
            raise ValueError("invalid expected agent run status")
        if side_effect_state not in {"none", "confirmed", "unknown"}:
            raise ValueError("invalid side_effect_state")
        if transcript_end_line is not None and transcript_end_line < 0:
            raise ValueError("transcript_end_line must not be negative")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = db.execute(
                """
                select agent_runs.*,
                       reply_tasks.execution_generation as task_execution_generation
                from agent_runs
                join reply_tasks on reply_tasks.id=agent_runs.reply_task_id
                where agent_runs.id=?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("agent run does not exist")
            if row["execution_generation"] != row["task_execution_generation"]:
                raise AgentRunLeaseLostError(f"agent run superseded: {run_id}")
            if (
                row["role"] == AgentRole.CONSUMER.value
                and side_effect_state != "none"
            ):
                raise ValueError("Consumer Agent cannot persist side effects")
            dispatched_intent = None
            if row["role"] == AgentRole.AUDIT.value:
                dispatched_intent = db.execute(
                    "select 1 from agent_effect_intents "
                    "where agent_run_id=? and state='dispatched' limit 1",
                    (run_id,),
                ).fetchone()
            if dispatched_intent is not None and target_status in {
                "completed",
                "failed",
            }:
                target_status = "failed"
                final_result_json = ""
                side_effect_state = "none"
                structured_error_json = json.dumps(
                    {
                        "code": "audit_external_action_result_missing",
                        "retryable": False,
                    },
                    separators=(",", ":"),
                )
            end_line = (
                row["transcript_end_line"]
                if transcript_end_line is None
                else transcript_end_line
            )
            exact_terminal_write = (
                row["status"] == target_status
                and row["final_result_json"] == final_result_json
                and row["structured_error_json"] == structured_error_json
                and row["side_effect_state"] == side_effect_state
                and row["transcript_end_line"] == end_line
            )
            if exact_terminal_write:
                return self._agent_run_from_row(row, db=db)
            if row["status"] == target_status:
                raise ValueError("conflicting terminal rewrite")
            if row["status"] == "completed":
                raise ValueError("cannot transition from completed agent run")
            allowed_targets = (
                {"completed", "failed", "unknown"}
                if expected_status == "running"
                else {"completed", "failed"}
            )
            if row["status"] != expected_status or target_status not in allowed_targets:
                raise ValueError(
                    f"invalid agent run transition: {row['status']} -> {target_status}"
                )
            self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                expected_status=expected_status,
            )
            completed_at = now_text if target_status in {"completed", "failed"} else ""
            values = (
                target_status,
                final_result_json,
                structured_error_json,
                side_effect_state,
                end_line,
                completed_at,
                now_text,
                run_id,
            )
            if expected_status == "running":
                cursor = db.execute(
                    """
                    update agent_runs
                    set status=?, final_result_json=?, structured_error_json=?,
                        side_effect_state=?, transcript_end_line=?,
                        lease_owner='', lease_expires_at='', completed_at=?,
                        updated_at=?
                    where id=? and status='running' and lease_owner=?
                      and lease_expires_at>?
                    """,
                    (*values, owner, now_text),
                )
                if cursor.rowcount != 1:
                    raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
            else:
                cursor = db.execute(
                    """
                    update agent_runs
                    set status=?, final_result_json=?, structured_error_json=?,
                        side_effect_state=?, transcript_end_line=?,
                        lease_owner='', lease_expires_at='', completed_at=?,
                        updated_at=?
                    where id=? and status='unknown' and lease_owner=?
                      and lease_expires_at>?
                    """,
                    (*values, owner, now_text),
                )
                if cursor.rowcount != 1:
                    raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
            updated = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            return self._agent_run_from_row(updated, db=db)

    def update_agent_run_projection(
        self,
        run_id: int,
        *,
        status: str,
        final_result_json: str = "",
        structured_error_json: str = "",
        side_effect_state: str = "none",
        owner: str,
        now: str | datetime | None = None,
    ) -> AgentRun:
        """Update the run's current projection while retaining append-only facts."""
        if status not in {"completed", "failed", "unknown"}:
            raise ValueError("invalid projection status")
        if side_effect_state not in {"none", "confirmed", "unknown"}:
            raise ValueError("invalid side_effect_state")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = db.execute("select * from agent_runs where id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("agent run does not exist")
            self._require_current_agent_run_write_access(
                db, run_id, owner=owner, now_text=now_text, expected_status=row["status"]
            )
            db.execute(
                "update agent_runs set status=?, final_result_json=?, structured_error_json=?, side_effect_state=?, completed_at=?, updated_at=? where id=?",
                (status, final_result_json, structured_error_json, side_effect_state, now_text if status != "unknown" else "", now_text, run_id),
            )
            db.execute(
                "insert into agent_run_state_events(agent_run_id, phase, structured_error_json, created_at) values (?, 'projection_update', ?, ?)",
                (run_id, structured_error_json, now_text),
            )
            updated = db.execute("select * from agent_runs where id=?", (run_id,)).fetchone()
            projection = self._agent_run_from_row(updated, db=db, load_events=False)
        return self.get_agent_run(projection.id) or projection

    def complete_agent_run(
        self,
        run_id: int,
        final_result: dict[str, object],
        *,
        owner: str,
        side_effect_state: str = "none",
        transcript_end_line: int | None = None,
        expected_status: str = "running",
        now: str | datetime | None = None,
    ) -> AgentRun:
        return self._transition_agent_run(
            run_id,
            expected_status=expected_status,
            owner=owner,
            target_status="completed",
            final_result_json=_json_object_text(
                final_result,
                field="final_result",
            ),
            structured_error_json="",
            side_effect_state=side_effect_state,
            transcript_end_line=transcript_end_line,
            now=now,
        )

    def fail_agent_run(
        self,
        run_id: int,
        structured_error: dict[str, object],
        *,
        owner: str,
        transcript_end_line: int | None = None,
        side_effect_state: str = "none",
        now: str | datetime | None = None,
    ) -> AgentRun:
        return self._transition_agent_run(
            run_id,
            expected_status="running",
            owner=owner,
            target_status="failed",
            final_result_json="",
            structured_error_json=_json_object_text(
                structured_error,
                field="structured_error",
            ),
            side_effect_state=side_effect_state,
            transcript_end_line=transcript_end_line,
            now=now,
        )


    def block_consumer_agent_run_for_completed_result_recovery(
        self,
        run_id: int,
        *,
        owner: str,
        now: str | datetime | None = None,
    ) -> AgentRun:
        """Suspend a corrupt durable Consumer result for explicit recovery."""
        code = "completed_runtime_result_invalid"
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = self._require_current_agent_run_write_access(
                db,
                run_id,
                owner=owner,
                now_text=now_text,
                expected_status="running",
            )
            if (
                row["role"] != AgentRole.CONSUMER.value
                or int(row["effect_started_count"]) != 0
                or row["side_effect_state"] != "none"
            ):
                raise ValueError("completed result block requires effect-free Consumer")
            error_json = json.dumps(
                {
                    "authorization_required": False,
                    "code": code,
                    "retryable": False,
                    "reason": (
                        "The durable runtime result failed integrity validation; "
                        "manual recovery is required."
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            run_cursor = db.execute(
                """
                update agent_runs
                set status='unknown', final_result_json='',
                    structured_error_json=?, side_effect_state='none',
                    reconciliation_suspended=1,
                    reconciliation_next_attempt_at='',
                    lease_owner='', lease_expires_at='', updated_at=?
                where id=? and status='running' and lease_owner=?
                  and lease_expires_at>?
                """,
                (error_json, now_text, run_id, owner, now_text),
            )
            task_cursor = db.execute(
                """
                update reply_tasks
                set status='failed', locked_at=null, available_at='',
                    error=?, recovery_code=?, updated_at=?
                where id=? and status='processing'
                  and execution_generation=?
                """,
                (
                    code,
                    code,
                    now_text,
                    row["reply_task_id"],
                    row["execution_generation"],
                ),
            )
            if run_cursor.rowcount != 1 or task_cursor.rowcount != 1:
                raise AgentRunLeaseLostError(
                    f"completed runtime result block is stale: {run_id}"
                )
            updated = db.execute(
                "select * from agent_runs where id=?", (run_id,)
            ).fetchone()
            return self._agent_run_from_row(updated, db=db)

    def finalize_closed_failed_audit_run(
        self,
        run_id: int,
        *,
        reason: str,
        now: str | datetime | None = None,
    ) -> AgentRun:
        """Replace a false unknown with the exact closed write failure."""
        if not reason.strip():
            raise ValueError("reason must be non-empty")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("agent run does not exist")
            if db.execute(
                "select 1 from agent_effect_intents "
                "where agent_run_id=? and state='dispatched' limit 1",
                (run_id,),
            ).fetchone() is not None:
                raise ValueError(
                    "agent run has a dispatched effect intent awaiting reconciliation"
                )
            if (
                row["role"] != AgentRole.AUDIT.value
                or row["status"] not in {"unknown", "completed"}
                or row["side_effect_state"] != "unknown"
                or int(row["effect_failed_count"]) <= 0
                or int(row["effect_unreviewed_count"]) != 0
                or int(row["effect_started_count"])
                > int(row["effect_completed_count"])
                + int(row["effect_failed_count"])
                + int(row["effect_receipt_count"])
            ):
                raise ValueError("agent run does not have a closed failed effect")
            failed_event = db.execute(
                """
                select event_json
                from agent_run_events
                where agent_run_id=? and event_type='item.failed'
                  and effect_kind='effectful'
                order by sequence desc
                limit 1
                """,
                (run_id,),
            ).fetchone()
            if failed_event is None:
                raise ValueError("agent run has no persisted failed effect event")
            event = json.loads(str(failed_event["event_json"]))
            item = event.get("item") if isinstance(event, dict) else None
            metadata = item.get("metadata") if isinstance(item, dict) else None
            failure_code = (
                metadata.get("failure_code") if isinstance(metadata, dict) else None
            )
            if not isinstance(failure_code, str) or not failure_code:
                failure_code = "audit_action_failed_before_completion"
            side_effect_state = (
                "confirmed"
                if int(row["effect_completed_count"])
                + int(row["effect_receipt_count"])
                else "none"
            )
            structured_error = json.dumps(
                {
                    "authorization_required": False,
                    "code": failure_code,
                    "reason": reason.strip(),
                    "retryable": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            cursor = db.execute(
                """
                update agent_runs
                set status='failed', final_result_json='', structured_error_json=?,
                    side_effect_state=?, reconciliation_suspended=0,
                    reconciliation_next_attempt_at='', lease_owner='',
                    lease_expires_at='', completed_at=?, updated_at=?
                where id=? and status=? and side_effect_state='unknown'
                """,
                (
                    structured_error,
                    side_effect_state,
                    now_text,
                    now_text,
                    run_id,
                    row["status"],
                ),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"agent run changed: {run_id}")
            updated = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            return self._agent_run_from_row(updated, db=db)



    def fail_expired_agent_run(
        self,
        run_id: int,
        structured_error: dict[str, object],
        *,
        expected_execution_generation: str,
        now: str | datetime | None = None,
    ) -> AgentRun:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        error_json = _json_object_text(structured_error, field="structured_error")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            cursor = db.execute(
                """
                update agent_runs
                set status='failed',
                    structured_error_json=?,
                    lease_owner='',
                    lease_expires_at='',
                    completed_at=?,
                    updated_at=?
                where id=? and status='running'
                  and execution_generation=?
                  and side_effect_state='none'
                  and not exists (
                      select 1 from agent_effect_intents
                      where agent_run_id=agent_runs.id and state='dispatched'
                  )
                  and lease_expires_at<=?
                  and exists (
                      select 1 from reply_tasks
                      where reply_tasks.id=agent_runs.reply_task_id
                        and reply_tasks.status='processing'
                        and reply_tasks.execution_generation=?
                  )
                """,
                (
                    error_json,
                    now_text,
                    now_text,
                    run_id,
                    expected_execution_generation,
                    now_text,
                    expected_execution_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("expired agent run is not a definite failure")
            row = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            return self._agent_run_from_row(row, db=db)







    @staticmethod
    def _insert_reconciliation_attempt_in_connection(
        db: sqlite3.Connection,
        *,
        run_id: int,
        task_id: int,
        codex_reason: str,
        audit_summary: str,
        send_status: str,
        send_error: str,
    ) -> int:
        row = db.execute(
            """
            select reply_tasks.channel, reply_tasks.conversation_id,
                   reply_tasks.conversation_title, reply_tasks.trigger_message_id,
                   reply_tasks.trigger_sender, reply_tasks.trigger_text,
                   reply_tasks.oa_url,
                   agent_runs.codex_session_id, agent_runs.transcript_start_line,
                   agent_runs.transcript_end_line, agent_runs.tool_events_json
            from reply_tasks
            join agent_runs on agent_runs.reply_task_id=reply_tasks.id
            where reply_tasks.id=? and agent_runs.id=?
            """,
            (task_id, run_id),
        ).fetchone()
        if row is None:
            raise ValueError("reconciliation run and task were not found")
        oa_url = str(row["oa_url"] or "")
        oa_process_instance_id, oa_task_id = AutoReplyStore._oa_identifiers_from_url(
            oa_url
        )
        cursor = db.execute(
            """
            insert into reply_attempts (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                agent_run_id, codex_reason, codex_session_id,
                codex_transcript_start_line, codex_transcript_end_line,
                audit_tool_events_json, audit_summary, send_status,
                send_error, channel, oa_process_instance_id, oa_task_id,
                oa_url, oa_action
            ) values (?, ?, ?, ?, ?, 'agent_run', 'general', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["conversation_id"],
                row["conversation_title"],
                row["trigger_message_id"],
                row["trigger_sender"],
                row["trigger_text"],
                run_id,
                codex_reason,
                row["codex_session_id"],
                row["transcript_start_line"],
                row["transcript_end_line"],
                row["tool_events_json"],
                audit_summary,
                send_status,
                send_error,
                row["channel"],
                oa_process_instance_id,
                oa_task_id,
                oa_url,
                "review" if oa_process_instance_id else "",
            ),
        )
        return int(cursor.lastrowid)





    def resolve_agent_run_manually(
        self,
        run_id: int,
        *,
        expected_execution_generation: str,
        resolution: str,
        reason: str,
        actor: str,
        now: str | datetime | None = None,
    ) -> ManualAgentRunResolution:
        allowed = {
            "confirmed_occurred",
            "confirmed_not_occurred",
            "terminate_unrecoverable",
        }
        if resolution not in allowed:
            raise ValueError("invalid manual reconciliation resolution")
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        if not reason.strip():
            raise ValueError("manual reconciliation reason must be non-empty")
        if not actor.strip():
            raise ValueError("manual reconciliation actor must be non-empty")
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            row = db.execute(
                """
                select agent_runs.*, reply_tasks.status as task_status,
                       reply_tasks.execution_generation as task_generation
                from agent_runs
                join reply_tasks on reply_tasks.id=agent_runs.reply_task_id
                where agent_runs.id=?
                  and agent_runs.role='audit'
                """,
                (run_id,),
            ).fetchone()
            is_suspended_unknown = (
                row is not None
                and row["status"] == "unknown"
                and bool(row["reconciliation_suspended"])
                and row["task_status"] in {"pending", "processing", "failed"}
            )
            is_failed_with_confirmed_effect = (
                row is not None
                and row["status"] == "failed"
                and row["task_status"] == "failed"
                and resolution == "confirmed_occurred"
            )
            if (
                not (is_suspended_unknown or is_failed_with_confirmed_effect)
                or row["execution_generation"] != expected_execution_generation
                or row["task_generation"] != expected_execution_generation
            ):
                raise AgentRunLeaseLostError(
                    f"manual reconciliation target is stale: {run_id}"
                )
            task_id = int(row["reply_task_id"])
            expected_run_status = str(row["status"])
            expected_task_status = str(row["task_status"])
            expected_suspended = int(bool(row["reconciliation_suspended"]))
            code = f"manual_reconciliation_{resolution}"
            audit_summary = f"{actor}: {reason}"
            next_generation = expected_execution_generation
            if resolution == "confirmed_occurred":
                run_status = "completed"
                side_effect_state = "confirmed"
                task_status = "done"
                send_status = "completed"
                final_result_json = json.dumps(
                    {
                        "outcome": "completed",
                        "summary": reason,
                        "manual_resolution": resolution,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            elif resolution == "confirmed_not_occurred":
                run_status = "failed"
                side_effect_state = "none"
                task_status = "pending"
                send_status = "failed"
                final_result_json = ""
                next_generation = uuid4().hex
            else:
                run_status = "failed"
                side_effect_state = "unknown"
                task_status = "failed"
                send_status = "blocked"
                final_result_json = ""
            structured_error_json = json.dumps(
                {
                    "code": code,
                    "retryable": False,
                    "reason": reason,
                    "actor": actor,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            run_cursor = db.execute(
                """
                update agent_runs
                set status=?, final_result_json=?, structured_error_json=?,
                    side_effect_state=?, reconciliation_suspended=0,
                    reconciliation_next_attempt_at='', lease_owner='',
                    lease_expires_at='', completed_at=?, updated_at=?
                where id=? and status=? and reconciliation_suspended=?
                  and execution_generation=?
                """,
                (
                    run_status,
                    final_result_json,
                    "" if resolution == "confirmed_occurred" else structured_error_json,
                    side_effect_state,
                    now_text,
                    now_text,
                    run_id,
                    expected_run_status,
                    expected_suspended,
                    expected_execution_generation,
                ),
            )
            if resolution == "confirmed_not_occurred":
                self._supersede_running_agent_runs(
                    db,
                    task_id,
                    expected_execution_generation,
                    now_text=now_text,
                )
            task_cursor = db.execute(
                """
                update reply_tasks
                set status=?, execution_generation=?, force_new_decision=?,
                    locked_at=null, available_at='', error=?, updated_at=?
                where id=? and status=? and execution_generation=?
                """,
                (
                    task_status,
                    next_generation,
                    int(resolution == "confirmed_not_occurred"),
                    "" if task_status == "done" else code,
                    now_text,
                    task_id,
                    expected_task_status,
                    expected_execution_generation,
                ),
            )
            if run_cursor.rowcount != 1 or task_cursor.rowcount != 1:
                raise AgentRunLeaseLostError(
                    f"manual reconciliation target is stale: {run_id}"
                )
            attempt_id = self._insert_reconciliation_attempt_in_connection(
                db,
                run_id=run_id,
                task_id=task_id,
                codex_reason=reason,
                audit_summary=audit_summary,
                send_status=send_status,
                send_error=code,
            )
            return ManualAgentRunResolution(
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                resolution=resolution,
                execution_generation=next_generation,
            )

    @staticmethod
    def _claimed_unknown_run_end_line(
        db: sqlite3.Connection,
        run_id: int,
        task_id: int,
        owner: str,
        now_text: str,
        transcript_end_line: int | None,
    ) -> tuple[int, str]:
        row = db.execute(
            """
            select agent_runs.*
            from agent_runs
            join reply_tasks on reply_tasks.id=agent_runs.reply_task_id
            where agent_runs.id=? and agent_runs.reply_task_id=?
              and agent_runs.role='audit'
              and reply_tasks.execution_generation=agent_runs.execution_generation
            """,
            (run_id, task_id),
        ).fetchone()
        if (
            row is None
            or row["status"] != "unknown"
            or row["lease_owner"] != owner
            or row["lease_expires_at"] <= now_text
        ):
            raise AgentRunLeaseLostError(f"agent run lease lost: {run_id}")
        if transcript_end_line is not None and transcript_end_line < 0:
            raise ValueError("transcript_end_line must not be negative")
        end_line = (
            row["transcript_end_line"]
            if transcript_end_line is None
            else transcript_end_line
        )
        return end_line, row["execution_generation"]

    def list_unknown_agent_runs(
        self,
        *,
        limit: int = 100,
        now: str | datetime | None = None,
    ) -> list[AgentRun]:
        if limit <= 0:
            return []
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            rows = db.execute(
                "select agent_runs.* from agent_runs "
                "join reply_tasks on reply_tasks.id=agent_runs.reply_task_id "
                "where agent_runs.status='unknown' "
                "and agent_runs.role='audit' "
                "and reply_tasks.status in ('pending', 'processing', 'failed') "
                "and reply_tasks.execution_generation=agent_runs.execution_generation "
                "and agent_runs.reconciliation_suspended=0 "
                "and (agent_runs.reconciliation_next_attempt_at='' "
                "or agent_runs.reconciliation_next_attempt_at<=?) "
                "and (agent_runs.lease_owner='' or agent_runs.lease_expires_at<=?) "
                "order by agent_runs.updated_at, agent_runs.id limit ?",
                (now_text, now_text, limit),
            ).fetchall()
            return [self._agent_run_from_row(row, db=db) for row in rows]

    def claim_unknown_agent_run(
        self,
        run_id: int,
        *,
        owner: str,
        lease_seconds: int = 1800,
        now: str | datetime | None = None,
    ) -> AgentRunClaim:
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._agent_run_write_transaction(now) as (
            db,
            (now_value, now_text),
        ):
            lease_expires_at = (now_value + timedelta(seconds=lease_seconds)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            cursor = db.execute(
                """
                update agent_runs
                set lease_owner=?, lease_expires_at=?,
                    reconciliation_attempts=reconciliation_attempts + 1,
                    updated_at=?
                where id=? and status='unknown'
                  and role='audit'
                  and reconciliation_suspended=0
                  and (reconciliation_next_attempt_at=''
                       or reconciliation_next_attempt_at<=?)
                  and (lease_owner='' or lease_expires_at<=?)
                  and exists (
                      select 1 from reply_tasks
                      where reply_tasks.id=agent_runs.reply_task_id
                        and reply_tasks.status in ('pending', 'processing', 'failed')
                        and reply_tasks.execution_generation=
                            agent_runs.execution_generation
                  )
                """,
                (
                    owner,
                    lease_expires_at,
                    now_text,
                    run_id,
                    now_text,
                    now_text,
                ),
            )
            row = db.execute(
                "select * from agent_runs where id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("agent run does not exist")
            return AgentRunClaim(
                run=self._agent_run_from_row(row, db=db),
                claimed=cursor.rowcount == 1,
            )


    def peek_reply_tasks(
        self,
        limit: int,
        now: str | None = None,
        *,
        channel: str | None = None,
        after_id: int | None = None,
        max_id: int | None = None,
    ) -> list[ReplyTask]:
        if limit <= 0:
            return []
        with self._connect() as db:
            now_expression = "current_timestamp" if now is None else "?"
            clauses = [
                "status='pending'",
                f"(available_at='' or available_at <= {now_expression})",
                """not exists (
                    select 1 from agent_runs as runs
                    where runs.reply_task_id=reply_tasks.id
                      and runs.execution_generation=reply_tasks.execution_generation
                      and runs.role='audit'
                      and runs.status='unknown'
                      and runs.reconciliation_suspended=1
                )""",
            ]
            args: list[str | int] = []
            if now is not None:
                args.append(now)
            if channel is not None:
                clauses.append("channel=?")
                args.append(channel)
            if after_id is not None:
                clauses.append("id>?")
                args.append(after_id)
            if max_id is not None:
                clauses.append("id<=?")
                args.append(max_id)
            args.append(limit)
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where {' and '.join(clauses)}
                order by id
                limit ?
                """,
                args,
            ).fetchall()
            return [self._reply_task_from_row(row) for row in rows]

    def peek_pending_reconciliation_reply_tasks(
        self,
        limit: int,
        now: str | None = None,
        *,
        channel: str | None = None,
        max_id: int | None = None,
    ) -> list[ReplyTask]:
        """Return pending tasks whose current audit run has an unknown effect."""
        if limit <= 0:
            return []
        with self._connect() as db:
            now_expression = "current_timestamp" if now is None else "?"
            clauses = [
                "reply_tasks.status='pending'",
                "agent_runs.role='audit'",
                "agent_runs.status='unknown'",
                "agent_runs.execution_generation=reply_tasks.execution_generation",
                "agent_runs.reconciliation_suspended=0",
                f"(agent_runs.reconciliation_next_attempt_at='' or agent_runs.reconciliation_next_attempt_at <= {now_expression})",
                f"(agent_runs.lease_owner='' or agent_runs.lease_expires_at <= {now_expression})",
            ]
            args: list[str | int] = []
            if now is not None:
                args.extend((now, now))
            if channel is not None:
                clauses.append("reply_tasks.channel=?")
                args.append(channel)
            if max_id is not None:
                clauses.append("reply_tasks.id<=?")
                args.append(max_id)
            args.append(limit)
            rows = db.execute(
                f"""
                select distinct reply_tasks.*
                from reply_tasks
                left join agent_runs on agent_runs.reply_task_id=reply_tasks.id
                where {' and '.join(clauses)}
                order by reply_tasks.id
                limit ?
                """,
                args,
            ).fetchall()
            return [self._reply_task_from_row(row) for row in rows]

    def max_pending_reply_task_id(
        self,
        now: str | None = None,
        *,
        channel: str | None = None,
    ) -> int | None:
        with self._connect() as db:
            now_expression = "current_timestamp" if now is None else "?"
            clauses = [
                "status='pending'",
                f"""(
                    available_at='' or available_at <= {now_expression}
                    or exists (
                        select 1 from agent_runs
                        where agent_runs.reply_task_id=reply_tasks.id
                          and agent_runs.role='audit'
                          and agent_runs.status='unknown'
                          and agent_runs.execution_generation=
                              reply_tasks.execution_generation
                          and agent_runs.reconciliation_suspended=0
                          and (
                              agent_runs.reconciliation_next_attempt_at=''
                              or agent_runs.reconciliation_next_attempt_at <= {now_expression}
                          )
                          and (
                              agent_runs.lease_owner=''
                              or agent_runs.lease_expires_at <= {now_expression}
                          )
                    )
                )""",
            ]
            args: list[str] = []
            if now is not None:
                args.extend((now, now, now))
            if channel is not None:
                clauses.append("channel=?")
                args.append(channel)
            row = db.execute(
                f"""
                select max(id) as max_id
                from reply_tasks
                where {' and '.join(clauses)}
                """,
                args,
            ).fetchone()
            return row["max_id"] if row is not None else None

    def get_reply_task(self, task_id: int) -> ReplyTask | None:
        with self._connect() as db:
            row = db.execute(
                "select * from reply_tasks where id=?",
                (task_id,),
            ).fetchone()
            return self._reply_task_from_row(row) if row is not None else None

    def claim_reply_task(
        self, task_id: int, now: str | None = None
    ) -> ReplyTask | None:
        with self._immediate_write_transaction() as db:
            now_expression = "current_timestamp" if now is None else "?"
            args: list[str | int] = [task_id]
            if now is not None:
                args.extend((now, now, now))
            cursor = db.execute(
                f"""
                update reply_tasks
                set status='processing',
                    attempts=attempts + 1,
                    locked_at=current_timestamp,
                    available_at='',
                    updated_at=current_timestamp
                where id=?
                  and status='pending'
                  and (
                      available_at='' or available_at <= {now_expression}
                      or exists (
                          select 1 from agent_runs
                          where agent_runs.reply_task_id=reply_tasks.id
                            and agent_runs.role='audit'
                            and agent_runs.status='unknown'
                            and agent_runs.execution_generation=
                                reply_tasks.execution_generation
                            and agent_runs.reconciliation_suspended=0
                            and (
                                agent_runs.reconciliation_next_attempt_at=''
                                or agent_runs.reconciliation_next_attempt_at <= {now_expression}
                            )
                            and (
                                agent_runs.lease_owner=''
                                or agent_runs.lease_expires_at <= {now_expression}
                            )
                      )
                  )
                """,
                args,
            )
            if cursor.rowcount != 1:
                return None
            row = db.execute(
                "select * from reply_tasks where id=?",
                (task_id,),
            ).fetchone()
            return self._reply_task_from_row(row)

    def claim_reply_tasks(
        self, limit: int, now: str | None = None, *, channel: str = "dingtalk"
    ) -> list[ReplyTask]:
        if limit <= 0:
            return []
        with self._immediate_write_transaction() as db:
            now_expression = "current_timestamp" if now is None else "?"
            args: list[str | int] = [channel]
            if now is not None:
                args.append(now)
            args.append(limit)
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where status='pending'
                  and channel=?
                  and (available_at='' or available_at <= {now_expression})
                order by id
                limit ?
                """,
                args,
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='processing',
                    attempts=attempts + 1,
                    locked_at=current_timestamp,
                    available_at='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                task_ids,
            )
            claimed_rows = db.execute(
                f"""
                select *
                from reply_tasks
                where id in ({placeholders})
                order by id
                """,
                task_ids,
            ).fetchall()
            return [self._reply_task_from_row(row) for row in claimed_rows]

    def list_stale_processing_reply_tasks(
        self, max_age_seconds: int
    ) -> list[ReplyTask]:
        if max_age_seconds <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_tasks as tasks
                where tasks.status='processing'
                  and tasks.locked_at is not null
                  and datetime(tasks.locked_at) <= datetime('now', ?)
                  and not exists (
                      select 1
                      from agent_runs as runs
                      where runs.reply_task_id=tasks.id
                        and runs.execution_generation=tasks.execution_generation
                        and runs.status in ('running', 'unknown')
                        and runs.lease_expires_at>current_timestamp
                        and datetime(runs.updated_at) > datetime('now', ?)
                  )
                order by tasks.locked_at, tasks.id
                """,
                (f"-{int(max_age_seconds)} seconds", f"-{int(max_age_seconds)} seconds"),
            ).fetchall()
            return [self._reply_task_from_row(row) for row in rows]

    def recover_orphaned_processing_reply_tasks(
        self,
        *,
        limit: int = 100,
    ) -> list[ReplyTask]:
        if limit <= 0:
            return []
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select tasks.*
                from reply_tasks as tasks
                where tasks.status='processing'
                  and not exists (
                      select 1
                      from agent_runs as runs
                      where runs.reply_task_id=tasks.id
                        and runs.execution_generation=tasks.execution_generation
                  )
                order by tasks.id
                limit ?
                """,
                (limit,),
            ).fetchall()
            recovered: list[ReplyTask] = []
            for row in rows:
                recovery_error = "orphaned_before_agent_start"
                if (
                    row["channel"] == "wechat"
                    and row["error"] == "wechat_read_only_decision_running"
                ):
                    recovery_error = "interrupted_read_only_decision"
                cursor = db.execute(
                    """
                    update reply_tasks
                    set status='pending', attempts=max(attempts - 1, 0),
                        locked_at=null, available_at='',
                        error=?,
                        updated_at=current_timestamp
                    where id=? and status='processing' and execution_generation=?
                      and not exists (
                          select 1
                          from agent_runs
                          where reply_task_id=reply_tasks.id
                            and execution_generation=reply_tasks.execution_generation
                      )
                    """,
                    (recovery_error, row["id"], row["execution_generation"]),
                )
                if cursor.rowcount != 1:
                    continue
                updated = db.execute(
                    "select * from reply_tasks where id=?",
                    (row["id"],),
                ).fetchone()
                recovered.append(self._reply_task_from_row(updated))
            return recovered

    def recover_no_effect_agent_runs_after_service_restart(
        self,
        *,
        limit: int = 100,
    ) -> list[ReplyTask]:
        """Release runs the stopped service can prove never started an effect."""
        if limit <= 0:
            return []
        error_json = json.dumps(
            {"code": "service_restart_before_effect", "retryable": True},
            separators=(",", ":"),
        )
        with self._immediate_write_transaction() as db:
            # A process may have died after its parent run was already marked
            # failed.  It cannot be retried through the active-attempt path;
            # close only entries whose parent proves no external effect began.
            db.execute(
                """
                update agent_runtime_attempts
                set status='failed', failure_class='process',
                    failure_code='runtime_parent_terminal_no_effect',
                    failover_permitted=1, lease_owner='', lease_expires_at='',
                    finished_at=current_timestamp, updated_at=current_timestamp
                where status in ('starting', 'running')
                  and first_effect_started_at=''
                  and agent_run_id in (
                      select id from agent_runs
                      where status='failed' and side_effect_state='none'
                  )
                """
            )
            rows = db.execute(
                """
                select tasks.*
                from reply_tasks as tasks
                where tasks.status='processing'
                  and exists (
                      select 1
                      from agent_runs as runs
                      where runs.reply_task_id=tasks.id
                        and runs.execution_generation=tasks.execution_generation
                      and runs.status in ('running', 'failed')
                        and runs.side_effect_state='none'
                  )
                  and not exists (
                      select 1
                      from agent_runs as runs
                      where runs.reply_task_id=tasks.id
                        and runs.execution_generation=tasks.execution_generation
                        and (
                            runs.status='unknown'
                            or (
                                runs.status='running'
                                and runs.side_effect_state<>'none'
                            )
                        )
                  )
                order by tasks.id
                limit ?
                """,
                (limit,),
            ).fetchall()
            recovered: list[ReplyTask] = []
            for row in rows:
                task_id = int(row["id"])
                generation = str(row["execution_generation"])
                next_generation = uuid4().hex
                db.execute(
                    """
                    update agent_runs
                    set status='failed', structured_error_json=case
                            when status='running' then ?
                            else structured_error_json
                        end,
                        lease_owner='', lease_expires_at='',
                        completed_at=case
                            when status='running' then current_timestamp
                            else completed_at
                        end,
                        updated_at=current_timestamp
                    where reply_task_id=? and execution_generation=?
                      and status in ('running', 'failed') and side_effect_state='none'
                    """,
                    (error_json, task_id, generation),
                )
                db.execute(
                    """
                    update agent_runtime_attempts
                    set status='failed', failure_class='process',
                        failure_code='service_restart_before_effect',
                        failover_permitted=1, lease_owner='', lease_expires_at='',
                        finished_at=current_timestamp, updated_at=current_timestamp
                    where status in ('starting', 'running')
                      and first_effect_started_at=''
                      and agent_run_id in (
                          select id from agent_runs
                          where reply_task_id=? and execution_generation=?
                            and status='failed' and side_effect_state='none'
                      )
                    """,
                    (task_id, generation),
                )
                cursor = db.execute(
                    """
                    update reply_tasks
                    set force_new_decision=0, execution_generation=?,
                        status='pending', locked_at=null, available_at='',
                        error='service_restart_before_effect',
                        updated_at=current_timestamp
                    where id=? and status='processing' and execution_generation=?
                    """,
                    (next_generation, task_id, generation),
                )
                if cursor.rowcount != 1:
                    continue
                db.execute(
                    "delete from codex_session_locks where conversation_id=?",
                    (row["conversation_id"],),
                )
                updated = db.execute(
                    "select * from reply_tasks where id=?", (task_id,)
                ).fetchone()
                recovered.append(self._reply_task_from_row(updated))
            return recovered

    def retry_failed_service_restart_tasks(self, *, limit: int = 100) -> list[ReplyTask]:
        """Immediately reopen failed no-effect tasks caused by a service restart."""
        if limit <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select tasks.*
                from reply_tasks tasks
                where tasks.status='failed'
                  and tasks.error like 'service_restart_before_effect%'
                  and not exists (
                    select 1 from agent_runs runs
                    where runs.reply_task_id=tasks.id
                      and runs.execution_generation=tasks.execution_generation
                      and (runs.status in ('running','unknown')
                           or runs.side_effect_state<>'none')
                  )
                order by tasks.id limit ?
                """, (limit,),
            ).fetchall()
            recovered = []
            for row in rows:
                generation = str(row["execution_generation"])
                cursor = db.execute(
                    """update reply_tasks set status='pending', attempts=0,
                       locked_at=null, available_at='',
                       error='service_restart_immediate_retry',
                       execution_generation=?, updated_at=current_timestamp
                       where id=? and status='failed' and execution_generation=?""",
                    (uuid4().hex, row["id"], generation),
                )
                if cursor.rowcount:
                    recovered.append(self._reply_task_from_row(
                        db.execute("select * from reply_tasks where id=?", (row["id"],)).fetchone()
                    ))
            return recovered

    def recover_effectful_audit_runs_after_service_restart(
        self,
        *,
        limit: int = 100,
    ) -> list[ReplyTask]:
        """Resume interrupted Audit effects through reconciliation, never replay."""
        if limit <= 0:
            return []
        error_json = json.dumps(
            {
                "code": "service_restart_effect_failed",
                "retryable": True,
            },
            separators=(",", ":"),
        )
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select tasks.*
                from reply_tasks as tasks
                where tasks.status='processing'
                  and exists (
                      select 1
                      from agent_runs as runs
                      where runs.reply_task_id=tasks.id
                        and runs.execution_generation=tasks.execution_generation
                        and runs.role='audit'
                        and runs.status='running'
                  )
                order by tasks.id
                limit ?
                """,
                (limit,),
            ).fetchall()
            recovered: list[ReplyTask] = []
            for row in rows:
                task_id = int(row["id"])
                generation = str(row["execution_generation"])
                transitioned_run_ids = [
                    int(run["id"])
                    for run in db.execute(
                        "select id from agent_runs "
                        "where reply_task_id=? and execution_generation=? "
                        "and role='audit' and status='running'",
                        (task_id, generation),
                    ).fetchall()
                ]
                db.execute(
                    """
                    update agent_runs
                    set status='failed', structured_error_json=?,
                        lease_owner='', lease_expires_at='',
                        completed_at=current_timestamp, updated_at=current_timestamp
                    where reply_task_id=? and execution_generation=?
                      and role='audit' and status='running'
                    """,
                    (error_json, task_id, generation),
                )
                db.executemany(
                    "insert into agent_run_state_events ("
                    "agent_run_id, phase, structured_error_json"
                    ") values (?, 'terminal_failure', ?)",
                    (
                        (run_id, error_json)
                        for run_id in transitioned_run_ids
                    ),
                )
                cursor = db.execute(
                    """
                    update reply_tasks
                    set status='pending', execution_generation=?,
                        locked_at=null, available_at='',
                        error='service_restart_effect_failed',
                        updated_at=current_timestamp
                    where id=? and status='processing' and execution_generation=?
                      and exists (
                          select 1 from agent_runs
                          where reply_task_id=reply_tasks.id
                            and execution_generation=reply_tasks.execution_generation
                            and role='audit' and status='failed'
                      )
                    """,
                    (uuid4().hex, task_id, generation),
                )
                if cursor.rowcount != 1:
                    continue
                db.execute(
                    "delete from codex_session_locks where conversation_id=?",
                    (row["conversation_id"],),
                )
                updated = db.execute(
                    "select * from reply_tasks where id=?", (task_id,)
                ).fetchone()
                recovered.append(self._reply_task_from_row(updated))
            return recovered

    def skip_unstarted_service_tasks(
        self,
        task_ids: list[int] | tuple[int, ...],
        *,
        reason: str,
        expected_source: str = "",
        now: str | datetime | None = None,
    ) -> list[ReplyTask]:
        """Finalize explicitly selected synthetic tasks that never started a write."""
        normalized_ids = tuple(dict.fromkeys(int(task_id) for task_id in task_ids))
        if not normalized_ids:
            return []
        if any(task_id <= 0 for task_id in normalized_ids):
            raise ValueError("task ids must be positive")
        if not reason.strip():
            raise ValueError("reason must be non-empty")

        placeholders = ",".join("?" for _ in normalized_ids)
        with self._agent_run_write_transaction(now) as (db, (_, now_text)):
            rows = db.execute(
                f"select * from reply_tasks where id in ({placeholders}) order by id",
                normalized_ids,
            ).fetchall()
            if len(rows) != len(normalized_ids):
                raise ValueError("one or more reply tasks do not exist")
            for row in rows:
                try:
                    payload = json.loads(str(row["trigger_message_json"]))
                except json.JSONDecodeError as exc:
                    raise ValueError("service task payload is invalid") from exc
                raw_payload = payload.get("raw_payload")
                source = (
                    str(raw_payload.get("source") or "").strip()
                    if isinstance(raw_payload, dict)
                    else ""
                )
                explicitly_selected_legacy_source = (
                    bool(expected_source.strip()) and source == expected_source.strip()
                )
                if not isinstance(raw_payload, dict) or not (
                    raw_payload.get("service_task")
                    or explicitly_selected_legacy_source
                ):
                    raise ValueError("reply task is not an explicit service task")
                if row["status"] not in {"pending", "processing", "failed"}:
                    raise ValueError("reply task is already terminal")
                unsafe = db.execute(
                    """
                    select 1
                    from agent_runs
                    where reply_task_id=? and execution_generation=?
                      and (
                          side_effect_state<>'none'
                          or effect_receipt_count>0
                          or effect_unreviewed_count>0
                      )
                    limit 1
                    """,
                    (int(row["id"]), str(row["execution_generation"])),
                ).fetchone()
                if unsafe is not None:
                    raise ValueError("service task has started or uncertain effects")

            error_json = json.dumps(
                {
                    "authorization_required": False,
                    "code": "invalid_service_task_skipped",
                    "reason": reason.strip(),
                    "retryable": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for row in rows:
                task_id = int(row["id"])
                generation = str(row["execution_generation"])
                db.execute(
                    """
                    update agent_runs
                    set status='failed', structured_error_json=?,
                        lease_owner='', lease_expires_at='',
                        completed_at=?, updated_at=?
                    where reply_task_id=? and execution_generation=?
                      and status='running' and side_effect_state='none'
                      and effect_receipt_count=0
                      and effect_unreviewed_count=0
                    """,
                    (error_json, now_text, now_text, task_id, generation),
                )
                db.execute(
                    """
                    update reply_tasks
                    set status='done', locked_at=null, available_at='', error=?,
                        updated_at=?
                    where id=? and execution_generation=?
                      and status in ('pending', 'processing', 'failed')
                    """,
                    (reason.strip(), now_text, task_id, generation),
                )
                db.execute(
                    "delete from codex_session_locks where conversation_id=?",
                    (str(row["conversation_id"]),),
                )
            updated_rows = db.execute(
                f"select * from reply_tasks where id in ({placeholders}) order by id",
                normalized_ids,
            ).fetchall()
            return [self._reply_task_from_row(row) for row in updated_rows]

    def release_unknown_audit_reconciliation_leases_after_service_restart(
        self,
        *,
        limit: int = 100,
    ) -> list[AgentRun]:
        """Make interrupted Audit reconciliation eligible on the next worker pass."""
        if limit <= 0:
            return []
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select runs.*
                from agent_runs as runs
                join reply_tasks as tasks on tasks.id=runs.reply_task_id
                where runs.status='unknown'
                  and runs.role='audit'
                  and runs.reconciliation_suspended=0
                  and runs.lease_owner<>''
                  and tasks.status in ('processing', 'pending', 'failed')
                  and tasks.execution_generation=runs.execution_generation
                order by runs.updated_at, runs.id
                limit ?
                """,
                (limit,),
            ).fetchall()
            released: list[AgentRun] = []
            for row in rows:
                cursor = db.execute(
                    """
                    update agent_runs
                    set lease_owner='', lease_expires_at='',
                        reconciliation_next_attempt_at='', updated_at=current_timestamp
                    where id=? and status='unknown' and role='audit'
                      and reconciliation_suspended=0 and lease_owner<>''
                    """,
                    (row["id"],),
                )
                if cursor.rowcount != 1:
                    continue
                updated = db.execute(
                    "select * from agent_runs where id=?", (row["id"],)
                ).fetchone()
                released.append(self._agent_run_from_row(updated, db=db))
            return released

    def resume_completed_agent_turns_after_service_restart(
        self,
        *,
        limit: int = 100,
    ) -> list[ReplyTask]:
        """Resume orchestration interrupted after a terminal Agent turn.

        The persisted AgentRun is already the durable source of truth. Requeue
        the same generation so AgentOrchestrator can derive the next state from
        it, rather than waiting for stale-task recovery or rerunning a turn.
        """
        if limit <= 0:
            return []
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select tasks.*
                from reply_tasks as tasks
                where tasks.status='processing'
                  and not exists (
                      select 1
                      from agent_runs as runs
                      where runs.reply_task_id=tasks.id
                        and runs.execution_generation=tasks.execution_generation
                        and runs.status in ('running', 'unknown')
                  )
                  and (
                      select latest.status
                      from agent_runs as latest
                      where latest.reply_task_id=tasks.id
                        and latest.execution_generation=tasks.execution_generation
                      order by latest.id desc
                      limit 1
                  )='completed'
                order by tasks.id
                limit ?
                """,
                (limit,),
            ).fetchall()
            recovered: list[ReplyTask] = []
            for row in rows:
                cursor = db.execute(
                    """
                    update reply_tasks
                    set status='pending', locked_at=null, available_at='',
                        error='service_restart_after_completed_turn',
                        updated_at=current_timestamp
                    where id=? and status='processing' and execution_generation=?
                      and not exists (
                          select 1
                          from agent_runs as runs
                          where runs.reply_task_id=reply_tasks.id
                            and runs.execution_generation=reply_tasks.execution_generation
                            and runs.status in ('running', 'unknown')
                      )
                    """,
                    (row["id"], row["execution_generation"]),
                )
                if cursor.rowcount != 1:
                    continue
                db.execute(
                    "delete from codex_session_locks where conversation_id=?",
                    (row["conversation_id"],),
                )
                updated = db.execute(
                    "select * from reply_tasks where id=?", (row["id"],)
                ).fetchone()
                recovered.append(self._reply_task_from_row(updated))
            return recovered

    def mark_wechat_read_only_decision_started(
        self,
        task_id: int,
        *,
        expected_execution_generation: str,
    ) -> None:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set error='wechat_read_only_decision_running',
                    updated_at=current_timestamp
                where id=? and channel='wechat' and status='processing'
                  and execution_generation=?
                """,
                (task_id, expected_execution_generation),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")

    def complete_reply_task(
        self,
        task_id: int,
        *,
        expected_execution_generation: str,
    ) -> None:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set status='done',
                    locked_at=null,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id=? and status='processing' and execution_generation=?
                """,
                (task_id, expected_execution_generation),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")

    def settle_failed_reply_task_without_replay(
        self,
        task_id: int,
        *,
        reason: str,
        audit_summary: str,
    ) -> int:
        """Close a failed task when read-only reconciliation proves replay stale.

        This is deliberately stricter than a manual status update: any active
        run, persisted delivery, or recorded side effect keeps the task failed
        until its external state can be reconciled through the normal path.
        """
        reason = reason.strip()
        audit_summary = audit_summary.strip()
        if not reason or not audit_summary:
            raise ValueError("reason and audit_summary must be non-empty")
        with self._immediate_write_transaction() as db:
            task = db.execute(
                "select * from reply_tasks where id=? and status='failed'",
                (task_id,),
            ).fetchone()
            if task is None:
                raise ValueError("failed reply task was not found")
            unsafe = db.execute(
                """
                select 1
                where exists (
                    select 1 from agent_runs
                    where reply_task_id=? and execution_generation=?
                      and (status in ('running', 'unknown')
                           or side_effect_state<>'none')
                ) or exists (
                    select 1 from sent_replies
                    where channel=? and conversation_id=?
                      and trigger_message_id=?
                ) or exists (
                    select 1
                    from agent_execution_receipts as receipts
                    join agent_runs as runs on runs.id=receipts.agent_run_id
                    where runs.reply_task_id=?
                      and receipts.completed=1 and receipts.persisted=1
                ) or exists (
                    select 1 from wechat_deliveries
                    where reply_task_id=?
                      and status not in ('failed', 'superseded')
                )
                """,
                (
                    task_id,
                    task["execution_generation"],
                    task["channel"],
                    task["conversation_id"],
                    task["trigger_message_id"],
                    task_id,
                    task_id,
                ),
            ).fetchone()
            if unsafe is not None:
                raise ValueError("failed reply task requires external reconciliation")
            cursor = db.execute(
                """
                insert into reply_attempts (
                    conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    codex_reason, audit_summary, send_status, send_error, channel
                ) values (?, ?, ?, ?, ?, 'no_reply', 'general', ?, ?,
                          'skipped', ?, ?)
                """,
                (
                    task["conversation_id"],
                    task["conversation_title"],
                    task["trigger_message_id"],
                    task["trigger_sender"],
                    task["trigger_text"],
                    reason,
                    audit_summary,
                    "settled_without_replay",
                    task["channel"],
                ),
            )
            db.execute(
                """
                update reply_tasks
                set status='done', locked_at=null, available_at='', error='',
                    recovery_code='settled_without_replay',
                    updated_at=current_timestamp
                where id=? and status='failed'
                """,
                (task_id,),
            )
            return int(cursor.lastrowid)

    def fail_reply_task(
        self,
        task_id: int,
        error: str,
        *,
        expected_execution_generation: str,
    ) -> None:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set status='failed',
                    locked_at=null,
                    error=?,
                    available_at='',
                    updated_at=current_timestamp
                where id=? and status='processing' and execution_generation=?
                """,
                (error, task_id, expected_execution_generation),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")

    def terminalize_exhausted_pending_reply_tasks(
        self,
        *,
        max_attempts: int,
        limit: int = 100,
    ) -> list[int]:
        """Close retry-loop sentinels once their bounded budget is exhausted.

        Startup recovery intentionally requeues effect-free work, but a service
        that repeatedly restarts must not leave those rows pending forever.
        Only known retry-exhaustion/restart markers are terminalized; ordinary
        provider waits and user-actionable tasks remain eligible.
        """
        if max_attempts <= 0 or limit <= 0:
            return []
        markers = (
            "service_restart_%",
            "consumer_retry_exhausted",
            "audit_retry_exhausted",
        )
        with self._connect() as db:
            rows = db.execute(
                """
                select id
                from reply_tasks
                where status='pending' and attempts>=?
                  and (error like ? or error=? or error=?)
                order by id
                limit ?
                """,
                (max_attempts, markers[0], markers[1], markers[2], limit),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            db.execute(
                f"update reply_tasks set status='failed', available_at='', "
                f"locked_at=null, error=error || '; retry_deadline_exhausted', "
                f"updated_at=current_timestamp where status='pending' and id in ({placeholders})",
                ids,
            )
            return ids

    def requeue_reply_task(
        self,
        task_id: int,
        error: str,
        *,
        expected_execution_generation: str,
        available_at: str = "",
    ) -> None:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        with self._connect() as db:
            stale_run_error = json.dumps(
                {
                    "authorization_required": False,
                    "code": "stale_agent_turn_recovery",
                    "retryable": True,
                },
                separators=(",", ":"),
            )
            db.execute(
                """
                update agent_runs
                set status='failed', structured_error_json=?,
                    lease_owner='', lease_expires_at='',
                    completed_at=current_timestamp, updated_at=current_timestamp
                where reply_task_id=? and execution_generation=?
                  and status='running' and side_effect_state='none'
                  and (
                    (lease_expires_at<>'' and lease_expires_at<=current_timestamp)
                    or datetime(updated_at)<=datetime('now', '-600 seconds')
                  )
                """,
                (stale_run_error, task_id, expected_execution_generation),
            )
            unknown = db.execute(
                """
                select 1 from agent_runs
                where reply_task_id=? and execution_generation=?
                  and role='audit' and status='unknown'
                  and effect_receipt_count=0
                  and effect_unreviewed_count=0
                  and reconciliation_suspended=0
                  and exists (
                      select 1 from reply_tasks
                      where reply_tasks.id=agent_runs.reply_task_id
                        and reply_tasks.status='pending'
                  )
                limit 1
                """,
                (task_id, expected_execution_generation),
            ).fetchone()
            next_generation = uuid4().hex if unknown is not None else expected_execution_generation
            cursor = db.execute(
                """
                update reply_tasks
                set status='pending',
                    locked_at=null,
                    available_at=?,
                    error=?,
                    force_new_decision=1,
                    execution_generation=?,
                    updated_at=current_timestamp
                where id=? and status in ('processing', 'pending')
                  and execution_generation=?
                """,
                (
                    available_at,
                    error,
                    next_generation,
                    task_id,
                    expected_execution_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")

    def retry_failed_reply_task(
        self,
        task_id: int,
        run_id: int,
        *,
        reason: str,
        recovery_code: str = "",
    ) -> ReplyTask:
        """Reopen a failed Consumer or Audit turn only when retry is effect-free."""
        reason = reason.strip()
        if not reason:
            raise ValueError("retry reason must be non-empty")
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            row = db.execute(
                """
                select tasks.execution_generation, tasks.status as task_status,
                       runs.role as run_role, runs.status as run_status,
                       runs.side_effect_state
                from reply_tasks as tasks
                join agent_runs as runs on runs.reply_task_id=tasks.id
                where tasks.id=? and runs.id=?
                  and runs.execution_generation=tasks.execution_generation
                  and runs.id=(
                      select max(latest.id)
                      from agent_runs as latest
                      where latest.reply_task_id=tasks.id
                        and latest.execution_generation=tasks.execution_generation
                  )
                """,
                (task_id, run_id),
            ).fetchone()
            retryable = False
            if row is not None:
                retryable = (
                    row["task_status"] == "failed"
                    and row["run_role"]
                    in {AgentRole.CONSUMER.value, AgentRole.AUDIT.value}
                    and row["run_status"] == "failed"
                    and row["side_effect_state"] == "none"
                )
            unsafe_generation = None
            if row is not None:
                unsafe_generation = db.execute(
                    """
                    select 1
                    from agent_runs as runs
                    where runs.reply_task_id=? and runs.execution_generation=?
                      and (
                          runs.status in ('running', 'unknown')
                          or runs.side_effect_state<>'none'
                          or exists (
                              select 1
                              from agent_execution_receipts as receipts
                              where receipts.agent_run_id=runs.id
                                and receipts.completed=1 and receipts.persisted=1
                          )
                          or exists (
                              select 1
                              from reply_tasks as sent_tasks
                              join sent_replies as replies
                                on replies.channel=sent_tasks.channel
                               and replies.conversation_id=
                                   sent_tasks.conversation_id
                               and replies.trigger_message_id=
                                   sent_tasks.trigger_message_id
                              where sent_tasks.id=runs.reply_task_id
                                and json_valid(replies.send_result_json)=1
                                and json_extract(
                                    replies.send_result_json, '$.agent_run_id'
                                )=runs.id
                                and json_extract(
                                    replies.send_result_json, '$.operation_id'
                                )=runs.operation_id
                          )
                      )
                    limit 1
                    """,
                    (task_id, row["execution_generation"]),
                ).fetchone()
            if not retryable or unsafe_generation is not None:
                raise ValueError("failed reply task is not safely retryable")
            cursor = db.execute(
                """
                update reply_tasks
                set status='pending', attempts=0,
                    locked_at=null, available_at='', error=?, recovery_code=?,
                    execution_generation=?, updated_at=?
                where id=? and status='failed' and execution_generation=?
                """,
                (reason, recovery_code.strip(), row["execution_generation"], now_text, task_id, row["execution_generation"]),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")
            updated = db.execute(
                "select * from reply_tasks where id=?", (task_id,)
            ).fetchone()
            if updated is None:
                raise RuntimeError("recovered reply task was not persisted")
            return self._reply_task_from_row(updated)

    def recover_failed_effect_free_consumer_tasks(self, *, channel: str) -> list[int]:
        """Retry each safely failed Consumer generation once, regardless of age."""
        recovery_code = "automatic_effect_free_consumer_retry"
        with self._connect() as db:
            rows = db.execute(
                """
                select tasks.id, runs.id as run_id
                from reply_tasks as tasks
                join agent_runs as runs on runs.reply_task_id=tasks.id
                where tasks.channel=?
                  and tasks.status='failed'
                  and tasks.recovery_code<>?
                  and runs.execution_generation=tasks.execution_generation
                  and runs.id=(
                      select max(latest.id)
                      from agent_runs as latest
                      where latest.reply_task_id=tasks.id
                        and latest.execution_generation=tasks.execution_generation
                  )
                  and runs.role='consumer'
                  and runs.status='failed'
                  and runs.side_effect_state='none'
                  and not exists (
                      select 1
                      from agent_runs as generation_runs
                      where generation_runs.reply_task_id=tasks.id
                        and generation_runs.execution_generation=tasks.execution_generation
                        and generation_runs.status in ('running', 'unknown')
                  )
                  and not exists (
                      select 1 from sent_replies as sent
                      where sent.channel=tasks.channel
                        and sent.conversation_id=tasks.conversation_id
                        and sent.trigger_message_id=tasks.trigger_message_id
                  )
                order by tasks.id
                """,
                (channel, recovery_code),
            ).fetchall()
        recovered: list[int] = []
        for row in rows:
            try:
                self.retry_failed_reply_task(
                    int(row["id"]),
                    int(row["run_id"]),
                    reason=recovery_code,
                    recovery_code=recovery_code,
                )
            except (AgentRunLeaseLostError, ValueError):
                continue
            recovered.append(int(row["id"]))
        return recovered

    def recover_failed_effect_free_audit_tasks(self, *, channel: str) -> list[int]:
        """Retry a failed Audit delivery once without regenerating Consumer's proposal.

        This recovery is limited to a completed Consumer parent and an Audit turn
        that recorded no effect.  Reopening the same generation lets the
        orchestrator reuse the durable proposal, while Audit still applies the
        current execution and verification gates before it can write anything.
        """
        recovery_code = "automatic_effect_free_audit_retry"
        with self._connect() as db:
            rows = db.execute(
                """
                select tasks.id, runs.id as run_id
                from reply_tasks as tasks
                join agent_runs as runs on runs.reply_task_id=tasks.id
                join agent_runs as parents on parents.id=runs.parent_agent_run_id
                where tasks.channel=?
                  and tasks.status='failed'
                  and tasks.recovery_code<>?
                  and runs.execution_generation=tasks.execution_generation
                  and runs.id=(
                      select max(latest.id)
                      from agent_runs as latest
                      where latest.reply_task_id=tasks.id
                        and latest.execution_generation=tasks.execution_generation
                  )
                  and runs.role='audit'
                  and runs.status='failed'
                  and runs.side_effect_state='none'
                  and parents.reply_task_id=tasks.id
                  and parents.execution_generation=tasks.execution_generation
                  and parents.role='consumer'
                  and parents.status='completed'
                  and parents.proposal_revision=runs.proposal_revision
                  and parents.final_result_json<>''
                  and not exists (
                      select 1
                      from agent_runs as generation_runs
                      where generation_runs.reply_task_id=tasks.id
                        and generation_runs.execution_generation=tasks.execution_generation
                        and (
                            generation_runs.status in ('running', 'unknown')
                            or generation_runs.side_effect_state<>'none'
                        )
                  )
                  and not exists (
                      select 1
                      from agent_execution_receipts as receipts
                      join agent_runs as receipt_runs on receipt_runs.id=receipts.agent_run_id
                      where receipt_runs.reply_task_id=tasks.id
                        and receipt_runs.execution_generation=tasks.execution_generation
                        and receipts.completed=1 and receipts.persisted=1
                  )
                  and not exists (
                      select 1 from sent_replies as sent
                      where sent.channel=tasks.channel
                        and sent.conversation_id=tasks.conversation_id
                        and sent.trigger_message_id=tasks.trigger_message_id
                  )
                order by tasks.id
                """,
                (channel, recovery_code),
            ).fetchall()
        recovered: list[int] = []
        for row in rows:
            try:
                self.retry_failed_reply_task(
                    int(row["id"]),
                    int(row["run_id"]),
                    reason=recovery_code,
                    recovery_code=recovery_code,
                )
            except (AgentRunLeaseLostError, ValueError):
                continue
            recovered.append(int(row["id"]))
        return recovered

    def recover_terminal_sessionless_audit_deliveries(
        self,
        *,
        channel: str,
        now: datetime | None = None,
    ) -> list[int]:
        """Replay legacy sessionless Audit deliveries with their saved decision.

        Older releases terminalized a no-session unknown Audit as
        ``audit_recovery_session_missing``.  The explicit replay policy reuses
        the persisted Consumer decision for every affected action type.  A
        delayed text delivery identifies its original message time so recipients
        can distinguish the replay from a newly generated instruction.
        """
        recovery_code = "legacy_sessionless_audit_delivery_replay"
        with self._agent_run_write_transaction(now) as (db, (now_value, now_text)):
            rows = db.execute(
                """
                select tasks.id as task_id,
                       tasks.execution_generation as task_generation,
                       tasks.trigger_create_time,
                       audits.id as audit_run_id,
                       parents.proposal_revision,
                       parents.final_result_json as consumer_result_json,
                       parents.tool_events_json as consumer_tool_events_json
                from reply_tasks as tasks
                join agent_runs as audits on audits.reply_task_id=tasks.id
                left join reply_attempts as attempts
                  on attempts.agent_run_id=audits.id
                 and attempts.send_error='audit_recovery_session_missing'
                join agent_runs as parents on parents.id=audits.parent_agent_run_id
                where tasks.channel=?
                  and tasks.status='done'
                  and not exists (
                      select 1
                      from sent_replies as sent
                      where sent.conversation_id=tasks.conversation_id
                        and sent.trigger_message_id=tasks.trigger_message_id
                  )
                  and not exists (
                      select 1
                      from agent_execution_receipts as receipts
                      join agent_runs as receipt_runs
                        on receipt_runs.id=receipts.agent_run_id
                      where receipt_runs.reply_task_id=tasks.id
                        and receipts.completed=1
                        and receipts.persisted=1
                        and receipts.safe_to_confirm=1
                  )
                  and (
                      tasks.recovery_code<>?
                      or not exists (
                          select 1
                          from agent_runs as replay_audits
                          where replay_audits.reply_task_id=tasks.id
                            and replay_audits.execution_generation=tasks.execution_generation
                            and replay_audits.role='audit'
                            and replay_audits.status='completed'
                            and replay_audits.side_effect_state='confirmed'
                      )
                  )
                  and audits.role='audit'
                  and audits.status='completed'
                  and (
                      attempts.id is not null
                      or (
                          json_valid(audits.final_result_json)=1
                          and json_extract(
                              audits.final_result_json, '$.error.code'
                          )='audit_recovery_session_missing'
                      )
                  )
                  and parents.reply_task_id=tasks.id
                  and parents.role='consumer'
                  and parents.status='completed'
                  and parents.proposal_revision=audits.proposal_revision
                  and json_valid(parents.final_result_json)=1
                  and json_extract(
                      parents.final_result_json, '$.outcome'
                  )='proposal'
                order by audits.id, attempts.id
                """,
                (channel, recovery_code),
            ).fetchall()
            recovered: list[int] = []
            for row in rows:
                next_generation = uuid4().hex
                consumer_result_json = self._sessionless_replay_result_json(
                    str(row["consumer_result_json"]),
                    trigger_create_time=str(row["trigger_create_time"]),
                    replay_generation=next_generation,
                    now=now_value,
                )
                cursor = db.execute(
                    """
                    update reply_tasks
                    set status='pending', attempts=0, locked_at=null,
                        available_at='', force_new_decision=0,
                        execution_generation=?, error=?, recovery_code=?,
                        updated_at=?
                    where id=? and status='done' and execution_generation=?
                    """,
                    (
                        next_generation,
                        recovery_code,
                        recovery_code,
                        now_text,
                        int(row["task_id"]),
                        str(row["task_generation"]),
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                db.execute(
                    """
                    insert into agent_runs (
                        reply_task_id, execution_generation, role,
                        proposal_revision, turn_attempt, parent_agent_run_id,
                        operation_id, status, final_result_json,
                        tool_events_json, side_effect_state, started_at,
                        completed_at, created_at, updated_at
                    ) values (?, ?, 'consumer', ?, 0, null, '', 'completed',
                              ?, ?, 'none', ?, ?, ?, ?)
                    """,
                    (
                        int(row["task_id"]),
                        next_generation,
                        int(row["proposal_revision"]),
                        consumer_result_json,
                        str(row["consumer_tool_events_json"]),
                        now_text,
                        now_text,
                        now_text,
                        now_text,
                    ),
                )
                recovered.append(int(row["task_id"]))
            return recovered

    @staticmethod
    def _sessionless_replay_result_json(
        consumer_result_json: str,
        *,
        trigger_create_time: str,
        replay_generation: str,
        now: datetime,
    ) -> str:
        """Return a replay-safe copy of a persisted Consumer proposal."""
        result = json.loads(consumer_result_json)
        proposal = result.get("proposal")
        actions = proposal.get("actions") if isinstance(proposal, dict) else None
        if not isinstance(actions, list):
            raise ValueError("sessionless replay requires Consumer proposal actions")
        source_time = datetime.fromisoformat(trigger_create_time).replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
        replay_time = now.astimezone(ZoneInfo("Asia/Shanghai"))
        notice = ""
        if replay_time - source_time > timedelta(minutes=30):
            notice = (
                "原消息生成于 "
                f"{source_time.year}-{source_time.month}-{source_time.day} "
                f"{source_time.hour}:{source_time.minute:02d}"
            )
        for action in actions:
            if not isinstance(action, dict):
                continue
            payload = action.get("payload")
            if not isinstance(payload, dict):
                continue
            for text_field in ("content", "text"):
                content = payload.get(text_field)
                if isinstance(content, str) and notice:
                    replay_content = f"{content}\n\n{notice}"
                    _replace_text_in_json(action, content, replay_content)
            argv = payload.get("argv")
            if not isinstance(argv, list):
                continue
            for index, argument in enumerate(argv[:-1]):
                if argument == "--idempotency-key" and isinstance(argv[index + 1], str):
                    key_material = f"{argv[index + 1]}:{replay_generation}".encode()
                    argv[index + 1] = (
                        "replay-" + hashlib.sha256(key_material).hexdigest()[:24]
                    )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    def retry_failed_pre_agent_reply_task(
        self,
        task_id: int,
        *,
        reason: str,
    ) -> ReplyTask:
        """Reopen a failed task only when no agent turn or delivery was recorded."""
        reason = reason.strip()
        if not reason:
            raise ValueError("retry reason must be non-empty")
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            task = db.execute(
                """
                select id, execution_generation, conversation_id, trigger_message_id
                from reply_tasks
                where id=? and status='failed'
                """,
                (task_id,),
            ).fetchone()
            if task is None:
                raise ValueError("failed reply task was not found")
            agent_run = db.execute(
                """
                select 1 from agent_runs
                where reply_task_id=? and execution_generation=?
                limit 1
                """,
                (task_id, task["execution_generation"]),
            ).fetchone()
            if agent_run is not None:
                raise ValueError("failed reply task already has an agent run")
            sent_reply = db.execute(
                """
                select 1 from sent_replies
                where conversation_id=? and trigger_message_id=?
                limit 1
                """,
                (task["conversation_id"], task["trigger_message_id"]),
            ).fetchone()
            if sent_reply is not None:
                raise ValueError("failed reply task already has a sent reply")
            cursor = db.execute(
                """
                update reply_tasks
                set status='pending', attempts=0, locked_at=null,
                    available_at='', error=?, updated_at=?
                where id=? and status='failed' and execution_generation=?
                """,
                (reason, now_text, task_id, task["execution_generation"]),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")
            updated = db.execute(
                "select * from reply_tasks where id=?", (task_id,)
            ).fetchone()
            if updated is None:
                raise RuntimeError("recovered pre-agent reply task was not persisted")
            return self._reply_task_from_row(updated)

    def has_failed_native_codex_auth_tasks(self, *, channel: str) -> bool:
        with self._connect() as db:
            rows = db.execute(
                """
                select tasks.error, tasks.recovery_code, attempts.send_error
                from reply_tasks as tasks
                left join reply_attempts as attempts
                  on attempts.id=(
                      select latest.id from reply_attempts as latest
                      where latest.channel=tasks.channel
                        and latest.conversation_id=tasks.conversation_id
                        and latest.trigger_message_id=tasks.trigger_message_id
                      order by latest.id desc limit 1
                  )
                where tasks.channel=? and (
                    tasks.status='failed'
                    or (
                        tasks.status='processing'
                        and tasks.error='codex_auth_recovered'
                        and tasks.locked_at<>''
                        and tasks.locked_at<=datetime('now', '-90 seconds')
                    )
                )
                """,
                (channel,),
            ).fetchall()
        return any(
            self._is_native_codex_auth_failure_row(row) for row in rows
        )

    def recover_failed_native_codex_auth_tasks(
        self,
        *,
        channel: str,
        reason: str,
    ) -> list[int]:
        """Requeue only no-effect failures after a verified native-login recovery."""
        if not reason.strip():
            raise ValueError("native Codex auth recovery reason must be non-empty")
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            rows = db.execute(
                """
                select tasks.id, tasks.status, tasks.locked_at,
                       tasks.execution_generation, tasks.error,
                       tasks.recovery_code, attempts.send_error
                from reply_tasks as tasks
                left join reply_attempts as attempts
                  on attempts.id=(
                      select latest.id from reply_attempts as latest
                      where latest.channel=tasks.channel
                        and latest.conversation_id=tasks.conversation_id
                        and latest.trigger_message_id=tasks.trigger_message_id
                      order by latest.id desc limit 1
                  )
                where tasks.channel=? and (
                    tasks.status='failed'
                    or (
                        tasks.status='processing'
                        and tasks.error='codex_auth_recovered'
                        and tasks.locked_at<>''
                        and tasks.locked_at<=datetime('now', '-90 seconds')
                    )
                )
                  and not exists (
                      select 1 from sent_replies as sent
                      where sent.channel=tasks.channel
                        and sent.conversation_id=tasks.conversation_id
                        and sent.trigger_message_id=tasks.trigger_message_id
                  )
                  and not exists (
                      select 1 from agent_runs as runs
                      where runs.reply_task_id=tasks.id
                        and runs.execution_generation=tasks.execution_generation
                        and (
                            runs.status in ('running', 'unknown')
                            or runs.side_effect_state<>'none'
                            or exists (
                                select 1 from agent_execution_receipts as receipts
                                where receipts.agent_run_id=runs.id
                                  and receipts.completed=1 and receipts.persisted=1
                            )
                        )
                  )
                  and not exists (
                      select 1 from wechat_deliveries as deliveries
                      where deliveries.reply_task_id=tasks.id
                        and deliveries.execution_generation=tasks.execution_generation
                        and not (
                            deliveries.status='failed'
                            and deliveries.pre_action_failure=1
                        )
                  )
                """,
                (channel,),
            ).fetchall()
            recovered: list[int] = []
            for row in rows:
                if not self._is_native_codex_auth_failure_row(row):
                    continue
                next_generation = uuid4().hex
                cursor = db.execute(
                    """
                    update reply_tasks
                    set status='pending', attempts=0, locked_at=null,
                        available_at='', execution_generation=?, error=?,
                        recovery_code='', updated_at=?
                    where id=? and execution_generation=? and (
                        status='failed'
                        or (
                            status='processing' and error='codex_auth_recovered'
                            and locked_at<>''
                            and locked_at<=datetime(?, '-90 seconds')
                        )
                    )
                    """,
                    (
                        next_generation,
                        reason,
                        now_text,
                        row["id"],
                        row["execution_generation"],
                        now_text,
                    ),
                )
                if cursor.rowcount == 1:
                    recovered.append(int(row["id"]))
            return recovered

    @staticmethod
    def _is_native_codex_auth_failure_row(row: sqlite3.Row) -> bool:
        if str(row["recovery_code"] or "") == CODEX_PROVIDER_AUTH_FAILED:
            return True
        for field in ("error", "send_error"):
            detail = str(row[field] or "")
            if detail == "codex_auth_recovered":
                return True
            if detail.startswith(f"{CODEX_PROVIDER_AUTH_FAILED}:"):
                return True
            if classify_codex_process_failure(detail, "") == CODEX_PROVIDER_AUTH_FAILED:
                return True
        return False


    def rotate_reply_task_execution_generation(self, task_id: int) -> str:
        execution_generation = uuid4().hex
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            task = db.execute(
                "select execution_generation from reply_tasks where id=? "
                "and status in ('processing', 'pending')",
                (task_id,),
            ).fetchone()
            if task is None:
                raise ValueError("retryable reply task was not found")
            current_generation = str(task["execution_generation"])
            unresolved_wechat_delivery = db.execute(
                """
                select 1
                from wechat_deliveries
                where reply_task_id=?
                  and execution_generation=?
                  and status in ('sending', 'send_unknown')
                limit 1
                """,
                (task_id, current_generation),
            ).fetchone()
            if unresolved_wechat_delivery is not None:
                raise ValueError(
                    "WeChat delivery reconciliation required before rotation"
                )
            self._supersede_running_agent_runs(
                db,
                task_id,
                current_generation,
                now_text=now_text,
            )
            self._supersede_ready_wechat_delivery(
                db, task_id, execution_generation
            )
            cursor = db.execute(
                """
                update reply_tasks
                set force_new_decision=1,
                    execution_generation=?,
                    status='pending',
                    locked_at=null,
                    available_at='',
                    error='execution_generation_rotated',
                    updated_at=current_timestamp
                where id=? and status in ('processing', 'pending')
                  and execution_generation=?
                """,
                (execution_generation, task_id, current_generation),
            )
            if cursor.rowcount != 1:
                raise ValueError("retryable reply task was not found")
        return execution_generation

    def defer_reply_task(
        self,
        task_id: int,
        error: str,
        *,
        expected_execution_generation: str,
        available_at: str = "",
    ) -> None:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set status='pending',
                    attempts=max(attempts - 1, 0),
                    locked_at=null,
                    available_at=?,
                    error=?,
                    updated_at=current_timestamp
                where id=? and status='processing' and execution_generation=?
                """,
                (available_at, error, task_id, expected_execution_generation),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")

    def defer_reply_task_for_authorization(
        self,
        task_id: int,
        error: str,
        *,
        expected_execution_generation: str,
        available_at: str = "",
    ) -> None:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set status='pending',
                    locked_at=null,
                    available_at=?,
                    error=?,
                    updated_at=current_timestamp
                where id=? and status='processing' and execution_generation=?
                """,
                (available_at, error, task_id, expected_execution_generation),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")

    def count_reply_tasks(
        self, status: str | None = None, *, channel: str | None = None
    ) -> int:
        clauses: list[str] = []
        args: list[str] = []
        if status is not None:
            clauses.append("status=?")
            args.append(status)
        if channel is not None:
            clauses.append("channel=?")
            args.append(channel)
        where = f" where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            row = db.execute(
                f"select count(*) as count from reply_tasks{where}", args
            ).fetchone()
            return int(row["count"])

    def count_due_follow_up_drafts(
        self,
        *,
        due_before: str,
        statuses: tuple[str, ...] = ("draft", "approved"),
    ) -> int:
        if not due_before.strip() or not statuses:
            return 0
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as db:
            row = db.execute(
                f"""
                select count(*) as count
                from follow_up_drafts
                where status in ({placeholders})
                  and scheduled_at != ''
                  and datetime(scheduled_at) <= datetime(?)
                """,
                [*statuses, due_before.strip()],
            ).fetchone()
            return int(row["count"] or 0)

    def list_reply_tasks(
        self,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
        *,
        channel: str | None = None,
    ) -> list[ReplyTask]:
        with self._connect() as db:
            query = """
                select *
                from reply_tasks
            """
            args: list[str | int] = []
            clauses: list[str] = []
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                clauses.append(f"status in ({placeholders})")
                args.extend(statuses)
            if channel is not None:
                clauses.append("channel=?")
                args.append(channel)
            if clauses:
                query = f"{query} where {' and '.join(clauses)}"
            query = f"{query} order by id desc"
            if limit is not None:
                query = f"{query} limit ?"
                args.append(limit)
            rows = db.execute(query, args).fetchall()
            return [self._reply_task_from_row(row) for row in rows]

    def get_reply_task_for_message(
        self, conversation_id: str, trigger_message_id: str, *,
        channel: str = "dingtalk",
    ) -> ReplyTask | None:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from reply_tasks
                where channel=? and conversation_id=? and trigger_message_id=?
                order by id desc
                limit 1
                """,
                (channel, conversation_id, trigger_message_id),
            ).fetchone()
            if row is None:
                return None
            return self._reply_task_from_row(row)

    # ---- WeChat channel: reply scopes ----
    def replace_wechat_reply_scopes(
        self, account_id: str, scopes: list[WechatReplyScope]
    ) -> None:
        if any(scope.account_id != account_id for scope in scopes):
            raise ValueError("scope account mismatch")
        activation_at = datetime.now().astimezone().isoformat()
        with self._connect() as db:
            existing = {
                (row["target_type"], row["target_id"]): row
                for row in db.execute(
                    "select * from wechat_reply_scopes where account_id=?",
                    (account_id,),
                ).fetchall()
            }
            db.execute(
                "update wechat_reply_scopes set enabled=0, "
                "disabled_reason='not_selected', updated_at=current_timestamp "
                "where account_id=?",
                (account_id,),
            )
            for scope in scopes:
                previous = existing.get((scope.target_type, scope.target_id))
                if scope.last_active_at:
                    watermark = scope.last_active_at
                elif previous is not None and bool(previous["enabled"]):
                    watermark = previous["last_discovered_at"] or activation_at
                else:
                    watermark = activation_at
                db.execute(
                    """
                    insert into wechat_reply_scopes (
                        account_id, target_type, target_id, conversation_id,
                        display_name, trigger_mode, enabled, binding_status,
                        binding_evidence_json, disabled_reason, last_discovered_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
                    on conflict(account_id, target_type, target_id) do update set
                        conversation_id=excluded.conversation_id,
                        display_name=excluded.display_name,
                        trigger_mode=excluded.trigger_mode,
                        enabled=excluded.enabled,
                        binding_status=excluded.binding_status,
                        binding_evidence_json=excluded.binding_evidence_json,
                        disabled_reason='',
                        last_discovered_at=excluded.last_discovered_at,
                        updated_at=current_timestamp
                    """,
                    (
                        scope.account_id, scope.target_type, scope.target_id,
                        scope.conversation_id, scope.display_name,
                        scope.trigger_mode, int(scope.enabled),
                        scope.binding_status,
                        json.dumps(scope.binding_evidence, ensure_ascii=False),
                        watermark,
                    ),
                )

    def advance_wechat_scope_watermark(
        self, account_id: str, target_type: str, target_id: str, sent_at: str
    ) -> bool:
        if not sent_at:
            raise ValueError("scope watermark requires sent_at")
        with self._connect() as db:
            cursor = db.execute(
                """
                update wechat_reply_scopes
                set last_discovered_at=?, updated_at=current_timestamp
                where account_id=? and target_type=? and target_id=?
                  and (
                    last_discovered_at=''
                    or last_discovered_at < ?
                  )
                """,
                (sent_at, account_id, target_type, target_id, sent_at),
            )
            return cursor.rowcount == 1

    def list_wechat_reply_scopes(
        self, account_id: str, *, enabled_only: bool = False
    ) -> list[WechatReplyScope]:
        where = "where account_id=?" + (" and enabled=1" if enabled_only else "")
        with self._connect() as db:
            rows = db.execute(
                f"select * from wechat_reply_scopes {where} "
                f"order by target_type, display_name, target_id",
                (account_id,),
            ).fetchall()
        return [
            WechatReplyScope(
                account_id=row["account_id"], target_type=row["target_type"],
                target_id=row["target_id"], conversation_id=row["conversation_id"],
                display_name=row["display_name"], trigger_mode=row["trigger_mode"],
                enabled=bool(row["enabled"]), binding_status=row["binding_status"],
                binding_evidence=json.loads(row["binding_evidence_json"]),
                disabled_reason=row["disabled_reason"],
                last_active_at=row["last_discovered_at"],
            )
            for row in rows
        ]

    def get_wechat_reply_scope(
        self, account_id: str, target_type: str, target_id: str
    ) -> WechatReplyScope | None:
        return next(
            (
                scope for scope in self.list_wechat_reply_scopes(account_id)
                if scope.target_type == target_type and scope.target_id == target_id
            ),
            None,
        )

    # ---- WeChat channel: read state ----
    def upsert_wechat_read_state(
        self, *, account_id: str, account_dir: str, db_dir: str,
        app_version: str, self_user_id: str, capability_status: str,
        capability_reason: str = "", watermark_sent_at: str = "",
        watermark_message_id: str = "", last_scan_at: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into wechat_read_state (
                    account_id, account_dir, db_dir, app_version, self_user_id,
                    capability_status, capability_reason, watermark_sent_at,
                    watermark_message_id, last_scan_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(account_id) do update set
                    account_dir=excluded.account_dir, db_dir=excluded.db_dir,
                    app_version=excluded.app_version,
                    self_user_id=coalesce(
                        nullif(excluded.self_user_id, ''),
                        wechat_read_state.self_user_id
                    ),
                    capability_status=excluded.capability_status,
                    capability_reason=excluded.capability_reason,
                    watermark_sent_at=coalesce(
                        nullif(excluded.watermark_sent_at, ''),
                        wechat_read_state.watermark_sent_at
                    ),
                    watermark_message_id=coalesce(
                        nullif(excluded.watermark_message_id, ''),
                        wechat_read_state.watermark_message_id
                    ),
                    last_scan_at=coalesce(
                        nullif(excluded.last_scan_at, ''),
                        wechat_read_state.last_scan_at
                    ),
                    updated_at=current_timestamp
                """,
                (
                    account_id, account_dir, db_dir, app_version, self_user_id,
                    capability_status, capability_reason, watermark_sent_at,
                    watermark_message_id, last_scan_at,
                ),
            )

    def get_wechat_read_state(self, account_id: str) -> dict[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from wechat_read_state where account_id=?", (account_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_wechat_read_states(self) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "select * from wechat_read_state order by account_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_wechat_reply_scopes_for_ready_account(
        self, *, enabled_only: bool = True
    ) -> list[WechatReplyScope]:
        ready = [
            row for row in self.list_wechat_read_states()
            if row["capability_status"] == "ready"
        ]
        if len(ready) != 1:
            return []
        return self.list_wechat_reply_scopes(
            ready[0]["account_id"], enabled_only=enabled_only
        )

    # ---- WeChat channel: deliveries ----
    @classmethod
    def _supersede_ready_wechat_delivery(
        cls,
        db: sqlite3.Connection,
        task_id: int,
        new_generation: str,
    ) -> None:
        row = db.execute(
            "select id from wechat_deliveries "
            "where reply_task_id=? and status='ready_to_send'",
            (task_id,),
        ).fetchone()
        if row is None:
            return
        error = f"superseded_by_generation:{new_generation}"
        db.execute(
            "update wechat_deliveries set status='superseded', error=?, "
            "updated_at=current_timestamp where id=? and status='ready_to_send'",
            (error, row["id"]),
        )
        cls._sync_wechat_delivery_reply_attempt(
            db,
            delivery_id=int(row["id"]),
            delivery_status="superseded",
            error=error,
        )

    @classmethod
    def _prepare_new_wechat_delivery(
        cls,
        db: sqlite3.Connection,
        *,
        reply_task_id: int,
        account_id: str,
        target_type: str,
        target_id: str,
        conversation_id: str,
    ) -> None:
        unresolved = db.execute(
            """
            select 1
            from wechat_deliveries
            where reply_task_id!=?
              and account_id=?
              and target_type=?
              and target_id=?
              and conversation_id=?
              and status in ('sending', 'send_unknown')
            limit 1
            """,
            (
                reply_task_id,
                account_id,
                target_type,
                target_id,
                conversation_id,
            ),
        ).fetchone()
        if unresolved is not None:
            raise ValueError(
                "WeChat delivery reconciliation required before a newer trigger"
            )
        error = f"superseded_by_newer_wechat_trigger:{reply_task_id}"
        older_deliveries = db.execute(
            """
            select id
            from wechat_deliveries
            where reply_task_id!=?
              and account_id=?
              and target_type=?
              and target_id=?
              and conversation_id=?
              and status in ('ready_to_send', 'failed')
              and error!='user_rejected'
            """,
            (
                reply_task_id,
                account_id,
                target_type,
                target_id,
                conversation_id,
            ),
        ).fetchall()
        for older in older_deliveries:
            db.execute(
                """
                update wechat_deliveries
                set status='superseded', error=?, updated_at=current_timestamp
                where id=?
                """,
                (error, older["id"]),
            )
            cls._sync_wechat_delivery_reply_attempt(
                db,
                delivery_id=older["id"],
                delivery_status="superseded",
                error=error,
            )

    def finalize_wechat_reply_task(
        self,
        *,
        task_id: int,
        expected_execution_generation: str,
        action: str,
        sensitivity_kind: str,
        codex_reason: str,
        draft_reply_text: str,
        audit_summary: str,
        send_status: str,
        send_error: str = "",
        recovery_code: str = "",
        task_status: str = "done",
        available_at: str = "",
        account_id: str = "",
        target_type: str = "",
        target_id: str = "",
        conversation_id: str = "",
        reply_text: str = "",
        evidence: dict[str, str] | None = None,
    ) -> int:
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        if task_status not in {"done", "failed", "pending"}:
            raise ValueError("invalid WeChat task status")
        if task_status == "pending" and not available_at.strip():
            raise ValueError("pending WeChat task requires available_at")
        if task_status != "pending" and available_at:
            raise ValueError("terminal WeChat task cannot set available_at")
        if recovery_code and task_status not in {"failed", "pending"}:
            raise ValueError("recovery code requires a recoverable WeChat task")
        has_delivery = bool(reply_text)
        if has_delivery and not all(
            value.strip()
            for value in (account_id, target_type, target_id, conversation_id)
        ):
            raise ValueError("WeChat delivery target must be complete")
        with self._immediate_write_transaction() as db:
            task = db.execute(
                "select * from reply_tasks where id=? and status='processing' "
                "and execution_generation=? and channel='wechat'",
                (task_id, expected_execution_generation),
            ).fetchone()
            if task is None:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")
            cursor = db.execute(
                """
                insert into reply_attempts (
                    conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    codex_reason, draft_reply_text, audit_summary,
                    send_status, send_error, channel
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'wechat')
                """,
                (
                    task["conversation_id"], task["conversation_title"],
                    task["trigger_message_id"], task["trigger_sender"],
                    task["trigger_text"], action, sensitivity_kind,
                    codex_reason, draft_reply_text, audit_summary,
                    send_status, send_error,
                ),
            )
            attempt_id = int(cursor.lastrowid)
            if has_delivery:
                self._prepare_new_wechat_delivery(
                    db,
                    reply_task_id=task_id,
                    account_id=account_id,
                    target_type=target_type,
                    target_id=target_id,
                    conversation_id=conversation_id,
                )
                existing = db.execute(
                    "select * from wechat_deliveries where reply_task_id=?",
                    (task_id,),
                ).fetchone()
                evidence_json = json.dumps(evidence or {}, ensure_ascii=False)
                if existing is None:
                    db.execute(
                        """
                        insert into wechat_deliveries (
                            reply_task_id, account_id, target_type, target_id,
                            conversation_id, reply_text, execution_generation,
                            evidence_json
                        ) values (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id, account_id, target_type, target_id,
                            conversation_id, reply_text,
                            expected_execution_generation, evidence_json,
                        ),
                    )
                elif (
                    existing["execution_generation"]
                    != expected_execution_generation
                    and (
                        existing["status"] in {"ready_to_send", "superseded"}
                        or (
                            existing["status"] == "failed"
                            and bool(existing["pre_action_failure"])
                        )
                    )
                ):
                    db.execute(
                        """
                        update wechat_deliveries
                        set account_id=?, target_type=?, target_id=?,
                            conversation_id=?, reply_text=?,
                            execution_generation=?, status='ready_to_send',
                            action_started_at='', pre_action_failure=0,
                            evidence_json=?, error='',
                            updated_at=current_timestamp
                        where id=? and (
                            status in ('ready_to_send', 'superseded')
                            or (status='failed' and pre_action_failure=1)
                        )
                        """,
                        (
                            account_id, target_type, target_id, conversation_id,
                            reply_text, expected_execution_generation,
                            evidence_json, existing["id"],
                        ),
                    )
                elif existing["execution_generation"] != expected_execution_generation:
                    raise ValueError("started WeChat delivery cannot be replaced")
            task_cursor = db.execute(
                """
                update reply_tasks
                set status=?, locked_at=null, available_at=?, error=?,
                    recovery_code=?,
                    updated_at=current_timestamp
                where id=? and status='processing' and execution_generation=?
                """,
                (
                    task_status,
                    available_at if task_status == "pending" else "",
                    send_error if task_status in {"failed", "pending"} else "",
                    recovery_code if task_status in {"failed", "pending"} else "",
                    task_id,
                    expected_execution_generation,
                ),
            )
            if task_cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")
            return attempt_id

    def create_wechat_delivery(
        self, *, reply_task_id: int, account_id: str, target_type: str,
        target_id: str, conversation_id: str, reply_text: str,
        evidence: dict[str, str] | None = None,
    ) -> int:
        with self._connect() as db:
            self._prepare_new_wechat_delivery(
                db,
                reply_task_id=reply_task_id,
                account_id=account_id,
                target_type=target_type,
                target_id=target_id,
                conversation_id=conversation_id,
            )
            db.execute(
                """
                insert into wechat_deliveries (
                    reply_task_id, account_id, target_type, target_id,
                    conversation_id, reply_text, execution_generation, evidence_json
                ) values (?, ?, ?, ?, ?, ?, coalesce((
                    select execution_generation from reply_tasks where id=?
                ), 'initial'), ?)
                on conflict(reply_task_id) do update set
                    account_id=excluded.account_id,
                    target_type=excluded.target_type,
                    target_id=excluded.target_id,
                    conversation_id=excluded.conversation_id,
                    reply_text=excluded.reply_text,
                    execution_generation=excluded.execution_generation,
                    status='ready_to_send',
                    pre_action_failure=0,
                    evidence_json=excluded.evidence_json,
                    error='',
                    updated_at=current_timestamp
                where wechat_deliveries.status='failed'
                  and wechat_deliveries.pre_action_failure=1
                """,
                (
                    reply_task_id, account_id, target_type, target_id,
                    conversation_id, reply_text, reply_task_id,
                    json.dumps(evidence or {}, ensure_ascii=False),
                ),
            )
            row = db.execute(
                "select id from wechat_deliveries where reply_task_id=?",
                (reply_task_id,),
            ).fetchone()
            return int(row["id"])

    def get_wechat_delivery_for_task(self, reply_task_id: int):
        from app.wechat.models import WechatDelivery
        with self._connect() as db:
            row = db.execute(
                "select * from wechat_deliveries where reply_task_id=?",
                (reply_task_id,),
            ).fetchone()
        if row is None:
            return None
        return WechatDelivery(
            id=row["id"], task_id=row["reply_task_id"], account_id=row["account_id"],
            target_type=row["target_type"], target_id=row["target_id"],
            conversation_id=row["conversation_id"], reply_text=row["reply_text"],
            action_started_at=row["action_started_at"],
            execution_generation=row["execution_generation"],
            status=row["status"], evidence=json.loads(row["evidence_json"]),
            error=row["error"],
            pre_action_failure=bool(row["pre_action_failure"]),
        )

    def list_wechat_deliveries_by_status(self, status: str) -> list:
        from app.wechat.models import WechatDelivery
        with self._connect() as db:
            rows = db.execute(
                """
                select wechat_deliveries.* from wechat_deliveries
                join reply_tasks on reply_tasks.id=wechat_deliveries.reply_task_id
                where wechat_deliveries.status=?
                  and wechat_deliveries.execution_generation=
                      reply_tasks.execution_generation
                order by wechat_deliveries.id
                """,
                (status,),
            ).fetchall()
        return [
            WechatDelivery(
                id=row["id"], task_id=row["reply_task_id"], account_id=row["account_id"],
                target_type=row["target_type"], target_id=row["target_id"],
                conversation_id=row["conversation_id"], reply_text=row["reply_text"],
                action_started_at=row["action_started_at"],
                execution_generation=row["execution_generation"],
                status=row["status"], evidence=json.loads(row["evidence_json"]),
                error=row["error"],
                pre_action_failure=bool(row["pre_action_failure"]),
            )
            for row in rows
        ]

    def ready_wechat_delivery_ids_for_messages(
        self,
        message_keys: list[tuple[str, str]],
    ) -> dict[tuple[str, str], int]:
        if not message_keys:
            return {}
        placeholders = ",".join(["(?, ?)"] * len(message_keys))
        args = [value for key in message_keys for value in key]
        with self._connect() as db:
            rows = db.execute(
                f"""
                select
                    reply_tasks.conversation_id,
                    reply_tasks.trigger_message_id,
                    wechat_deliveries.id as delivery_id
                from wechat_deliveries
                join reply_tasks on reply_tasks.id=wechat_deliveries.reply_task_id
                where wechat_deliveries.status='ready_to_send'
                  and wechat_deliveries.execution_generation=
                      reply_tasks.execution_generation
                  and reply_tasks.channel='wechat'
                  and (reply_tasks.conversation_id, reply_tasks.trigger_message_id)
                      in ({placeholders})
                """,
                args,
            ).fetchall()
        return {
            (str(row["conversation_id"]), str(row["trigger_message_id"])):
            int(row["delivery_id"])
            for row in rows
        }

    def requeue_unperformed_wechat_deliveries(self, *, max_retries: int = 2) -> int:
        """Return pre-action failures to the send queue for a bounded retry."""
        if max_retries < 1:
            return 0
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select deliveries.id as delivery_id, (
                    select attempts.id
                    from reply_attempts as attempts
                    where attempts.channel='wechat'
                      and attempts.conversation_id=tasks.conversation_id
                      and attempts.trigger_message_id=tasks.trigger_message_id
                    order by attempts.id desc
                    limit 1
                ) as attempt_id
                from wechat_deliveries as deliveries
                join reply_tasks as tasks on tasks.id=deliveries.reply_task_id
                where deliveries.status='failed'
                  and deliveries.pre_action_failure=1
                  and deliveries.action_started_at<>''
                  and deliveries.execution_generation=tasks.execution_generation
                  and coalesce((
                      select attempts.retry_count
                      from reply_attempts as attempts
                      where attempts.channel='wechat'
                        and attempts.conversation_id=tasks.conversation_id
                        and attempts.trigger_message_id=tasks.trigger_message_id
                      order by attempts.id desc
                      limit 1
                  ), ?) < ?
                """,
                (0, max_retries),
            ).fetchall()
            requeued = 0
            for row in rows:
                delivery_id = int(row["delivery_id"])
                cursor = db.execute(
                    """
                    update wechat_deliveries
                    set status='ready_to_send', error='', pre_action_failure=0,
                        updated_at=current_timestamp
                    where id=?
                      and status='failed'
                      and pre_action_failure=1
                      and exists (
                      select 1 from reply_tasks
                      where reply_tasks.id=wechat_deliveries.reply_task_id
                        and reply_tasks.execution_generation=
                            wechat_deliveries.execution_generation
                      )
                    """,
                    (delivery_id,),
                )
                if cursor.rowcount != 1:
                    continue
                self._sync_wechat_delivery_reply_attempt(
                    db,
                    delivery_id=delivery_id,
                    delivery_status="ready_to_send",
                    error="",
                )
                if row["attempt_id"] is None:
                    task = db.execute(
                        """
                        select conversation_id, conversation_title,
                               trigger_message_id, trigger_sender, trigger_text
                        from reply_tasks
                        where id=(
                            select reply_task_id from wechat_deliveries where id=?
                        )
                        """,
                        (delivery_id,),
                    ).fetchone()
                    if task is None:
                        raise RuntimeError("wechat delivery retry task is missing")
                    db.execute(
                        """
                        insert into reply_attempts (
                            conversation_id, conversation_title,
                            trigger_message_id, trigger_sender, trigger_text,
                            action, sensitivity_kind, codex_reason, audit_summary,
                            send_status, send_error, retry_count, channel
                        ) values (?, ?, ?, ?, ?, 'send_reply', 'normal',
                                  'legacy_wechat_delivery_recovery',
                                  'created during bounded legacy delivery recovery',
                                  'pending', 'wechat_delivery_ready_to_send', 1,
                                  'wechat')
                        """,
                        (
                            task["conversation_id"],
                            task["conversation_title"],
                            task["trigger_message_id"],
                            task["trigger_sender"],
                            task["trigger_text"],
                        ),
                    )
                else:
                    db.execute(
                        "update reply_attempts set retry_count=retry_count + 1 "
                        "where id=?",
                        (row["attempt_id"],),
                    )
                requeued += 1
            return requeued

    def claim_wechat_delivery(
        self,
        delivery_id: int,
        *,
        expected_execution_generation: str,
        now: str = "",
    ):
        from app.wechat.models import WechatDelivery

        with self._immediate_write_transaction() as db:
            cursor = db.execute(
                """
                update wechat_deliveries
                set status='sending',
                    action_started_at=case
                        when ?='' then current_timestamp else ? end,
                    pre_action_failure=0,
                    error='',
                    updated_at=current_timestamp
                where id=? and status='ready_to_send'
                  and execution_generation=?
                  and exists (
                      select 1 from reply_tasks
                      where reply_tasks.id=wechat_deliveries.reply_task_id
                        and reply_tasks.execution_generation=?
                  )
                """,
                (
                    now,
                    now,
                    delivery_id,
                    expected_execution_generation,
                    expected_execution_generation,
                ),
            )
            if cursor.rowcount != 1:
                return None
            self._sync_wechat_delivery_reply_attempt(
                db,
                delivery_id=delivery_id,
                delivery_status="sending",
                error="",
            )
            row = db.execute(
                "select * from wechat_deliveries where id=?", (delivery_id,)
            ).fetchone()
        return WechatDelivery(
            id=row["id"], task_id=row["reply_task_id"],
            account_id=row["account_id"], target_type=row["target_type"],
            target_id=row["target_id"], conversation_id=row["conversation_id"],
            reply_text=row["reply_text"],
            action_started_at=row["action_started_at"],
            execution_generation=row["execution_generation"],
            status=row["status"], evidence=json.loads(row["evidence_json"]),
            error=row["error"],
            pre_action_failure=bool(row["pre_action_failure"]),
        )

    def mark_wechat_delivery_sending(self, delivery_id: int, *, now: str = "") -> None:
        delivery = self.get_wechat_delivery_by_id(delivery_id)
        if delivery is None or self.claim_wechat_delivery(
            delivery_id,
            expected_execution_generation=delivery.execution_generation,
            now=now,
        ) is None:
            raise ValueError("WeChat delivery is not claimable")

    def get_wechat_delivery_by_id(self, delivery_id: int):
        from app.wechat.models import WechatDelivery
        with self._connect() as db:
            row = db.execute(
                "select * from wechat_deliveries where id=?", (delivery_id,)
            ).fetchone()
        if row is None:
            return None
        return WechatDelivery(
            id=row["id"], task_id=row["reply_task_id"],
            account_id=row["account_id"], target_type=row["target_type"],
            target_id=row["target_id"], conversation_id=row["conversation_id"],
            reply_text=row["reply_text"],
            action_started_at=row["action_started_at"],
            execution_generation=row["execution_generation"],
            status=row["status"], evidence=json.loads(row["evidence_json"]),
            error=row["error"],
            pre_action_failure=bool(row["pre_action_failure"]),
        )

    def set_wechat_delivery_status(
        self, delivery_id: int, status: str, *, error: str = "",
        action_started_at: str | None = None,
        pre_action_failure: bool = False,
    ) -> None:
        expected_statuses = self._wechat_delivery_source_statuses(status, error)
        placeholders = ",".join("?" for _ in expected_statuses)
        generation_guard = (
            "and exists (select 1 from reply_tasks "
            "where reply_tasks.id=wechat_deliveries.reply_task_id "
            "and reply_tasks.execution_generation="
            "wechat_deliveries.execution_generation)"
        )
        with self._connect() as db:
            if action_started_at is not None:
                cursor = db.execute(
                    "update wechat_deliveries set status=?, error=?, "
                    "action_started_at=?, pre_action_failure=?, "
                    "updated_at=current_timestamp where id=? "
                    f"and status in ({placeholders}) {generation_guard}",
                    (
                        status,
                        error,
                        action_started_at,
                        int(pre_action_failure),
                        delivery_id,
                        *expected_statuses,
                    ),
                )
            else:
                cursor = db.execute(
                    "update wechat_deliveries set status=?, error=?, "
                    "pre_action_failure=?, updated_at=current_timestamp where id=? "
                    f"and status in ({placeholders}) {generation_guard}",
                    (
                        status,
                        error,
                        int(pre_action_failure),
                        delivery_id,
                        *expected_statuses,
                    ),
                )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(
                    f"WeChat delivery superseded or not in expected state: {delivery_id}"
                )
            self._sync_wechat_delivery_reply_attempt(
                db,
                delivery_id=delivery_id,
                delivery_status=status,
                error=error,
            )
            if status == "sent":
                self._supersede_failed_wechat_deliveries_with_newer_sent(
                    db,
                    sent_delivery_id=delivery_id,
                )

    def supersede_reconciled_wechat_delivery(
        self,
        delivery_id: int,
        *,
        expected_execution_generation: str,
        reason: str,
        inactive_before: str,
    ) -> None:
        """Close a stale unknown delivery after read-only reconciliation."""
        generation = expected_execution_generation.strip()
        explanation = reason.strip()
        inactivity_cutoff = inactive_before.strip()
        if not generation:
            raise ValueError("expected_execution_generation must be non-empty")
        if not explanation:
            raise ValueError("reason must be non-empty")
        if not inactivity_cutoff:
            raise ValueError("inactive_before must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update wechat_deliveries
                set status='superseded', error=?, updated_at=current_timestamp
                where id=? and status='send_unknown'
                  and execution_generation=?
                  and datetime(
                    coalesce(nullif(action_started_at, ''), created_at)
                  ) <= datetime(?)
                  and exists (
                    select 1 from reply_tasks
                    where reply_tasks.id=wechat_deliveries.reply_task_id
                      and reply_tasks.execution_generation=?
                  )
                """,
                (
                    explanation,
                    delivery_id,
                    generation,
                    inactivity_cutoff,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(
                    f"WeChat delivery superseded or not in expected state: {delivery_id}"
                )
            self._sync_wechat_delivery_reply_attempt(
                db,
                delivery_id=delivery_id,
                delivery_status="superseded",
                error=explanation,
            )

    def supersede_stale_ready_wechat_delivery(
        self,
        delivery_id: int,
        *,
        expected_execution_generation: str,
        reason: str,
        inactive_before: str,
    ) -> None:
        """Close an expired, never-started delivery without sending it."""
        generation = expected_execution_generation.strip()
        explanation = reason.strip()
        inactivity_cutoff = inactive_before.strip()
        if not generation:
            raise ValueError("expected_execution_generation must be non-empty")
        if not explanation:
            raise ValueError("reason must be non-empty")
        if not inactivity_cutoff:
            raise ValueError("inactive_before must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update wechat_deliveries
                set status='superseded', error=?, updated_at=current_timestamp
                where id=? and status='ready_to_send'
                  and execution_generation=?
                  and coalesce(action_started_at, '')=''
                  and datetime(created_at) <= datetime(?)
                  and exists (
                    select 1 from reply_tasks
                    where reply_tasks.id=wechat_deliveries.reply_task_id
                      and reply_tasks.execution_generation=?
                  )
                """,
                (
                    explanation,
                    delivery_id,
                    generation,
                    inactivity_cutoff,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                raise AgentRunLeaseLostError(
                    f"WeChat delivery superseded or not in expected state: {delivery_id}"
                )
            self._sync_wechat_delivery_reply_attempt(
                db,
                delivery_id=delivery_id,
                delivery_status="superseded",
                error=explanation,
            )

    @classmethod
    def _supersede_failed_wechat_deliveries_with_newer_sent(
        cls,
        db: sqlite3.Connection,
        *,
        sent_delivery_id: int,
    ) -> int:
        """Close old pre-action failures once a newer reply reached the chat.

        A later successful direct-chat reply makes earlier failed drafts stale.
        Replaying them would reverse the conversation and duplicate a response,
        so preserve the audit trail as ``superseded`` instead of retrying.
        """
        sent = db.execute(
            """
            select account_id, target_type, target_id, conversation_id, reply_task_id
            from wechat_deliveries
            where id=? and status='sent'
            """,
            (sent_delivery_id,),
        ).fetchone()
        if sent is None:
            return 0
        rows = db.execute(
            """
            select id
            from wechat_deliveries
            where account_id=?
              and target_type=?
              and target_id=?
              and conversation_id=?
              and reply_task_id < ?
              and status='failed'
              and pre_action_failure=1
            """,
            (
                sent["account_id"],
                sent["target_type"],
                sent["target_id"],
                sent["conversation_id"],
                sent["reply_task_id"],
            ),
        ).fetchall()
        error = f"superseded_by_newer_wechat_delivery:{sent_delivery_id}"
        for row in rows:
            delivery_id = int(row["id"])
            db.execute(
                """
                update wechat_deliveries
                set status='superseded', error=?, pre_action_failure=0,
                    updated_at=current_timestamp
                where id=? and status='failed' and pre_action_failure=1
                """,
                (error, delivery_id),
            )
            cls._sync_wechat_delivery_reply_attempt(
                db,
                delivery_id=delivery_id,
                delivery_status="superseded",
                error=error,
            )
        return len(rows)

    def supersede_failed_wechat_deliveries_with_newer_sent(self) -> int:
        """Reconcile historical failures after an interrupted sender restart."""
        with self._connect() as db:
            sent_rows = db.execute(
                "select id from wechat_deliveries where status='sent' order by id"
            ).fetchall()
            total = 0
            for row in sent_rows:
                total += self._supersede_failed_wechat_deliveries_with_newer_sent(
                    db,
                    sent_delivery_id=int(row["id"]),
                )
            return total

    def normalize_user_rejected_wechat_deliveries(self) -> int:
        """Convert legacy user-rejected deliveries to terminal skipped state."""
        with self._connect() as db:
            rows = db.execute(
                """
                select id
                from wechat_deliveries
                where status='failed' and error='user_rejected'
                order by id
                """
            ).fetchall()
            for row in rows:
                delivery_id = int(row["id"])
                db.execute(
                    """
                    update wechat_deliveries
                    set status='skipped', updated_at=current_timestamp
                    where id=? and status='failed' and error='user_rejected'
                    """,
                    (delivery_id,),
                )
                self._sync_wechat_delivery_reply_attempt(
                    db,
                    delivery_id=delivery_id,
                    delivery_status="skipped",
                    error="user_rejected",
                )
            return len(rows)

    @staticmethod
    def _wechat_delivery_source_statuses(status: str, error: str) -> tuple[str, ...]:
        if status == "sending":
            return ("ready_to_send",)
        if status == "sent":
            return ("sending", "send_unknown")
        if status == "send_unknown":
            return ("sending", "send_unknown")
        if status == "failed" and error in {
            "user_rejected",
            "target_binding_unverified",
        }:
            return ("ready_to_send",)
        if status == "failed" and error == "recalled":
            return ("sent",)
        if status == "failed":
            return ("sending", "send_unknown")
        raise ValueError(f"Unsupported WeChat delivery transition target: {status}")

    @staticmethod
    def _wechat_delivery_reply_attempt_status(
        delivery_status: str,
        error: str,
    ) -> tuple[str, str] | None:
        status = delivery_status.strip().lower()
        reason = error.strip()
        if status in {"ready_to_send", "sending"}:
            return "pending", reason or f"wechat_delivery_{status}"
        if status == "sent":
            return "sent", reason
        if status == "superseded":
            return "skipped", reason or status
        if status == "skipped":
            return "skipped", reason or status
        if status == "failed" and reason == "user_rejected":
            return "skipped", reason
        if status in {"failed", "send_unknown"}:
            return "failed", reason or status
        return None

    @classmethod
    def _sync_wechat_delivery_reply_attempt(
        cls,
        db: sqlite3.Connection,
        *,
        delivery_id: int,
        delivery_status: str,
        error: str,
    ) -> None:
        next_status = cls._wechat_delivery_reply_attempt_status(
            delivery_status,
            error,
        )
        if next_status is None:
            return
        send_status, send_error = next_status
        row = db.execute(
            """
            select tasks.conversation_id, tasks.trigger_message_id
            from wechat_deliveries as deliveries
            join reply_tasks as tasks on tasks.id=deliveries.reply_task_id
            where deliveries.id=?
            """,
            (delivery_id,),
        ).fetchone()
        if row is None:
            return
        attempt = db.execute(
            """
            select id
            from reply_attempts
            where channel='wechat'
              and conversation_id=?
              and trigger_message_id=?
            order by id desc
            limit 1
            """,
            (row["conversation_id"], row["trigger_message_id"]),
        ).fetchone()
        if attempt is None:
            return
        db.execute(
            """
            update reply_attempts
            set send_status=?,
                send_error=?,
                updated_at=current_timestamp
            where id=?
            """,
            (send_status, send_error, attempt["id"]),
        )

    # ---- WeChat channel: memory candidates ----
    def add_wechat_memory_candidate(self, *, import_run_id: str, account_id: str,
                                    candidate) -> int | None:
        with self._connect() as db:
            canonical = " ".join(candidate.statement.split()).casefold()
            existing = db.execute(
                "select * from wechat_memory_candidates where account_id=? "
                "and status in ('pending', 'approved') order by id",
                (account_id,),
            ).fetchall()
            for row in existing:
                if " ".join(row["statement"].split()).casefold() != canonical:
                    continue
                conversations = sorted(set(json.loads(row["source_conversation_ids_json"]))
                                       | set(candidate.source_conversation_ids))
                messages = sorted(set(json.loads(row["source_message_ids_json"]))
                                  | set(candidate.source_message_ids))
                starts = [value for value in (row["source_time_start"],
                          candidate.source_time_start) if value]
                ends = [value for value in (row["source_time_end"],
                        candidate.source_time_end) if value]
                db.execute(
                    "update wechat_memory_candidates set source_conversation_ids_json=?, "
                    "source_message_ids_json=?, source_time_start=?, source_time_end=?, "
                    "updated_at=current_timestamp where id=?",
                    (json.dumps(conversations, ensure_ascii=False),
                     json.dumps(messages, ensure_ascii=False), min(starts, default=""),
                     max(ends, default=""), row["id"]),
                )
                return None
            cur = db.execute(
                """
                insert or ignore into wechat_memory_candidates (
                    import_run_id, account_id, statement, category, confidence,
                    sensitivity, source_conversation_ids_json, source_message_ids_json,
                    source_time_start, source_time_end, evidence_excerpt, cleanup_notes
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_run_id, account_id, candidate.statement, candidate.category,
                    candidate.confidence, candidate.sensitivity,
                    json.dumps(candidate.source_conversation_ids, ensure_ascii=False),
                    json.dumps(candidate.source_message_ids, ensure_ascii=False),
                    candidate.source_time_start, candidate.source_time_end,
                    candidate.evidence_excerpt, candidate.cleanup_notes,
                ),
            )
            if cur.rowcount != 1:
                return None
            return int(cur.lastrowid)

    def get_wechat_memory_candidate(self, candidate_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "select * from wechat_memory_candidates where id=?", (candidate_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_wechat_memory_candidates(
        self, *, status: str | None = None, category: str | None = None,
        sensitivity: str | None = None,
    ) -> list[dict]:
        with self._connect() as db:
            clauses, values = [], []
            for column, value in (("status", status), ("category", category),
                                  ("sensitivity", sensitivity)):
                if value:
                    clauses.append(f"{column}=?")
                    values.append(value)
            where = " where " + " and ".join(clauses) if clauses else ""
            rows = db.execute(
                f"select * from wechat_memory_candidates{where} order by id",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def review_wechat_memory_candidate(
        self, candidate_id: int, action: str, *, reviewer: str = "",
        final_statement: str = "",
    ) -> dict:
        with self._connect() as db:
            row = db.execute(
                "select * from wechat_memory_candidates where id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("candidate not found")
            current = row["status"]
            write_status = row["memory_write_status"]
            reviewer = reviewer.strip()
            if not reviewer:
                raise ValueError("reviewer required")
            if write_status == "writing":
                raise ValueError("candidate is writing and cannot be reviewed")
            if action == "approve":
                from app.wechat.memory_import import validate_final_statement
                statement = validate_final_statement(final_statement)
                if current != "pending":
                    raise ValueError("only pending candidate can be approved")
                db.execute(
                    "update wechat_memory_candidates set status='approved', reviewer=?, "
                    "edited_statement=?, reviewed_at=current_timestamp, "
                    "updated_at=current_timestamp where id=? and status='pending'",
                    (reviewer, statement, candidate_id),
                )
            elif action == "reject":
                if current not in {"pending", "approved"} or write_status in {"writing", "written"}:
                    raise ValueError("candidate cannot be rejected")
                db.execute(
                    "update wechat_memory_candidates set status='rejected', reviewer=?, "
                    "reviewed_at=current_timestamp, updated_at=current_timestamp where id=?",
                    (reviewer, candidate_id),
                )
            elif action == "revoke":
                if current != "approved":
                    raise ValueError("only approved candidate can be revoked")
                next_write_status = (
                    "revocation_unavailable" if write_status == "written" else write_status
                )
                db.execute(
                    "update wechat_memory_candidates set status='revoked', reviewer=?, "
                    "memory_write_status=?, reviewed_at=current_timestamp, "
                    "updated_at=current_timestamp where id=?",
                    (reviewer, next_write_status, candidate_id),
                )
            else:
                raise ValueError("invalid review action")
        result = self.get_wechat_memory_candidate(candidate_id)
        assert result is not None
        return result

    def claim_wechat_memory_candidate_write(self, candidate_id: int) -> dict:
        with self._connect() as db:
            row = db.execute(
                "select * from wechat_memory_candidates where id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                return {"outcome": "rejected", "reason": "candidate not found"}
            candidate = dict(row)
            if candidate["status"] != "approved":
                return {"outcome": "rejected", "reason": "candidate must be approved before writing memory"}
            if candidate["memory_id"]:
                return {"outcome": "written", "memory_id": candidate["memory_id"]}
            if candidate["memory_write_status"] == "writing":
                return {"outcome": "writing"}
            if candidate["memory_write_status"] == "unknown":
                return {"outcome": "rejected", "reason": "unknown memory write outcome requires manual resolution"}
            if candidate["memory_write_status"] == "revocation_unavailable":
                return {"outcome": "rejected", "reason": "revoked candidate cannot be written"}
            updated = db.execute(
                "update wechat_memory_candidates set memory_write_status='writing', "
                "memory_write_error='', updated_at=current_timestamp where id=? "
                "and status='approved' and memory_id='' "
                "and memory_write_status in ('', 'failed')",
                (candidate_id,),
            )
            if updated.rowcount != 1:
                return {"outcome": "writing"}
            candidate["edited_statement"] = (
                candidate["edited_statement"] or candidate["statement"]
            )
            return {"outcome": "claimed", "candidate": candidate}

    def finish_wechat_memory_candidate_write(
        self, candidate_id: int, *, status: str, memory_id: str = "",
        error: str = "",
    ) -> None:
        if status not in {"written", "failed", "unknown"}:
            raise ValueError("invalid memory write status")
        with self._connect() as db:
            if status == "written":
                changed = db.execute(
                    "update wechat_memory_candidates set memory_write_status='written', "
                    "memory_id=?, memory_write_error='', updated_at=current_timestamp "
                    "where id=? and status='approved' and memory_write_status='writing'",
                    (memory_id, candidate_id),
                )
                if changed.rowcount == 1:
                    return
                row = db.execute(
                    "select status, memory_write_status from wechat_memory_candidates where id=?",
                    (candidate_id,),
                ).fetchone()
                if row is None or row["memory_write_status"] != "writing":
                    raise RuntimeError("memory write claim lost")
                fallback = "revocation_unavailable" if row["status"] == "revoked" else "unknown"
                db.execute(
                    "update wechat_memory_candidates set memory_write_status=?, memory_id=?, "
                    "memory_write_error='review state changed during write', "
                    "updated_at=current_timestamp where id=? and memory_write_status='writing'",
                    (fallback, memory_id, candidate_id),
                )
                return
            changed = db.execute(
                "update wechat_memory_candidates set memory_write_status=?, memory_id='', "
                "memory_write_error=?, updated_at=current_timestamp "
                "where id=? and status='approved' and memory_write_status='writing'",
                (status, error[:500], candidate_id),
            )
            if changed.rowcount != 1:
                raise RuntimeError("memory write claim lost")

    def resolve_wechat_memory_candidate_write_unknown(
        self, candidate_id: int, *, reviewer: str, confirm: bool = False,
        stale_after_seconds: int = 900,
    ) -> None:
        if not confirm:
            raise ValueError("explicit stale write confirmation required")
        if stale_after_seconds < 900:
            raise ValueError("stale write threshold cannot be less than 900 seconds")
        if not reviewer.strip():
            raise ValueError("reviewer required")
        with self._connect() as db:
            changed = db.execute(
                "update wechat_memory_candidates set memory_write_status='unknown', "
                "memory_write_error='manually resolved after interrupted write', reviewer=?, "
                "reviewed_at=current_timestamp, updated_at=current_timestamp "
                "where id=? and memory_write_status='writing' "
                "and datetime(updated_at) <= datetime('now', ?)",
                (reviewer.strip(), candidate_id, f"-{int(stale_after_seconds)} seconds"),
            )
            if changed.rowcount != 1:
                raise ValueError("only confirmed stale writing candidate can be resolved to unknown")

    @staticmethod
    def _meeting_alignment_job_from_row(
        row: sqlite3.Row,
    ) -> MeetingAlignmentJob:
        return MeetingAlignmentJob.model_validate(dict(row))

    @staticmethod
    def _meeting_alignment_run_from_row(
        row: sqlite3.Row,
    ) -> MeetingAlignmentRun:
        return MeetingAlignmentRun.model_validate(dict(row))

    @staticmethod
    def _validate_meeting_alignment_status(status: object) -> str:
        return TypeAdapter(MeetingAlignmentQueueStatus).validate_python(status)

    def upsert_meeting_alignment_job(
        self,
        *,
        meeting_id: str,
        title: str,
        source_json: str,
        participants_json: str,
        ended_at: str,
        eligible_at: str,
        status: MeetingAlignmentQueueStatus,
    ) -> int:
        validated_status = self._validate_meeting_alignment_status(status)
        with self._connect() as db:
            db.execute(
                """
                insert into meeting_alignment_jobs (
                    meeting_id,
                    title,
                    source_json,
                    participants_json,
                    ended_at,
                    eligible_at,
                    status
                )
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(meeting_id) do update set
                    title=excluded.title,
                    source_json=excluded.source_json,
                    participants_json=excluded.participants_json,
                    ended_at=excluded.ended_at,
                    eligible_at=excluded.eligible_at,
                    status=case
                        when meeting_alignment_jobs.status='waiting'
                            then excluded.status
                        else meeting_alignment_jobs.status
                    end,
                    updated_at=current_timestamp
                """,
                (
                    meeting_id,
                    title,
                    source_json,
                    participants_json,
                    ended_at,
                    eligible_at,
                    validated_status,
                ),
            )
            row = db.execute(
                """
                select id
                from meeting_alignment_jobs
                where meeting_id=?
                """,
                (meeting_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("meeting alignment job was not persisted")
            return int(row["id"])

    def get_meeting_alignment_job(self, job_id: int) -> MeetingAlignmentJob:
        with self._connect() as db:
            row = db.execute(
                "select * from meeting_alignment_jobs where id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"meeting alignment job not found: {job_id}")
            return self._meeting_alignment_job_from_row(row)

    def get_meeting_alignment_job_by_meeting_id(
        self,
        meeting_id: str,
    ) -> MeetingAlignmentJob | None:
        with self._connect() as db:
            row = db.execute(
                "select * from meeting_alignment_jobs where meeting_id=?",
                (meeting_id,),
            ).fetchone()
            if row is None:
                return None
            return self._meeting_alignment_job_from_row(row)

    def claim_meeting_alignment_jobs(
        self,
        limit: int,
        now: str,
    ) -> list[MeetingAlignmentJob]:
        if limit <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                with candidates as (
                    select id
                    from meeting_alignment_jobs
                    where status in ('waiting', 'pending', 'retry')
                      and datetime(eligible_at) <= datetime(?)
                      and (
                          available_at=''
                          or datetime(available_at) <= datetime(?)
                      )
                    order by datetime(eligible_at), id
                    limit ?
                )
                update meeting_alignment_jobs
                set status='processing',
                    attempts=attempts + 1,
                    locked_at=current_timestamp,
                    updated_at=current_timestamp
                where id in (select id from candidates)
                  and status in ('waiting', 'pending', 'retry')
                returning *
                """,
                (now, now, limit),
            ).fetchall()
            jobs = [self._meeting_alignment_job_from_row(row) for row in rows]
            return sorted(jobs, key=lambda job: (job.eligible_at, job.id))

    def update_meeting_alignment_job(self, job_id: int, **values: object) -> None:
        if not values:
            return
        allowed_columns = {
            "title",
            "source_json",
            "participants_json",
            "ended_at",
            "eligible_at",
            "status",
            "locked_at",
            "available_at",
            "error",
            "decision_json",
            "target_kind",
            "target_id",
            "target_title",
            "mentions_json",
            "final_message",
            "send_result_json",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        release_ready_lock_on_transition = False
        if "status" in filtered:
            filtered["status"] = self._validate_meeting_alignment_status(
                filtered["status"]
            )
            if (
                filtered["status"] == "ready_to_send"
                and "locked_at" not in filtered
            ):
                release_ready_lock_on_transition = True
                filtered.setdefault("available_at", "")
            elif filtered["status"] in {
                "waiting",
                "pending",
                "no_action",
                "sent",
                "retry",
                "failed",
                "quarantined",
            }:
                filtered.setdefault("locked_at", None)
        assignments = [f"{column}=?" for column in filtered]
        if release_ready_lock_on_transition:
            assignments.append(
                "locked_at=case "
                "when status!='ready_to_send' then null "
                "else locked_at end"
            )
        args = [*filtered.values(), job_id]
        with self._connect() as db:
            db.execute(
                f"""
                update meeting_alignment_jobs
                set {', '.join(assignments)}, updated_at=current_timestamp
                where id=?
                """,
                args,
            )

    def schedule_meeting_alignment_job_retry(
        self,
        job_id: int,
        error: str,
        *,
        available_at: str,
    ) -> None:
        self.update_meeting_alignment_job(
            job_id,
            status="retry",
            locked_at=None,
            available_at=available_at,
            error=error,
        )

    def claim_ready_to_send_meeting_alignment_jobs(
        self,
        limit: int,
        now: str,
    ) -> list[MeetingAlignmentJob]:
        if limit <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                with candidates as (
                    select id
                    from meeting_alignment_jobs
                    where status='ready_to_send'
                      and locked_at is null
                      and (
                          available_at=''
                          or datetime(available_at) <= datetime(?)
                      )
                    order by id
                    limit ?
                )
                update meeting_alignment_jobs
                set locked_at=current_timestamp,
                    updated_at=current_timestamp
                where id in (select id from candidates)
                  and status='ready_to_send'
                  and locked_at is null
                returning *
                """,
                (now, limit),
            ).fetchall()
            jobs = [self._meeting_alignment_job_from_row(row) for row in rows]
            return sorted(jobs, key=lambda job: job.id)

    def claim_ready_to_send_meeting_alignment_job(
        self, job_id: int, *, now: str
    ) -> MeetingAlignmentJob | None:
        if job_id <= 0:
            raise ValueError("meeting alignment job id must be positive")
        with self._connect() as db:
            row = db.execute(
                """update meeting_alignment_jobs
                   set locked_at=current_timestamp, updated_at=current_timestamp
                   where id=? and status='ready_to_send' and locked_at is null
                     and (available_at='' or datetime(available_at)<=datetime(?))
                   returning *""",
                (job_id, now),
            ).fetchone()
            return self._meeting_alignment_job_from_row(row) if row else None

    def schedule_ready_to_send_meeting_alignment_reconciliation(
        self,
        job_id: int,
        *,
        error: str,
        available_at: str,
    ) -> MeetingAlignmentJob:
        with self._connect() as db:
            row = db.execute(
                """
                update meeting_alignment_jobs
                set attempts=attempts + 1,
                    available_at=?,
                    error=?,
                    locked_at=null,
                    updated_at=current_timestamp
                where id=?
                  and status='ready_to_send'
                  and locked_at is not null
                returning *
                """,
                (available_at, error, job_id),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "ready meeting reconciliation requires an exclusive claim"
                )
            return self._meeting_alignment_job_from_row(row)

    def reset_ready_to_send_meeting_alignment_jobs(
        self,
    ) -> list[MeetingAlignmentJob]:
        with self._connect() as db:
            rows = db.execute(
                """
                update meeting_alignment_jobs
                set locked_at=null,
                    updated_at=current_timestamp
                where status='ready_to_send'
                  and locked_at is not null
                returning *
                """
            ).fetchall()
            jobs = [self._meeting_alignment_job_from_row(row) for row in rows]
            return sorted(jobs, key=lambda job: job.id)

    def reset_processing_meeting_alignment_jobs(
        self,
    ) -> list[MeetingAlignmentJob]:
        with self._connect() as db:
            rows = db.execute(
                """
                update meeting_alignment_jobs
                set status='retry',
                    attempts=max(attempts - 1, 0),
                    locked_at=null,
                    updated_at=current_timestamp
                where status='processing'
                returning *
                """
            ).fetchall()
            jobs = [self._meeting_alignment_job_from_row(row) for row in rows]
            return sorted(jobs, key=lambda job: job.id)

    def rerun_meeting_alignment_jobs(self, job_ids: list[int]) -> list[int]:
        """Reset selected failed meeting jobs for a fresh analysis turn.

        Existing meeting alignment runs remain immutable. Only the queue
        projection and retry counter are reset; delivery must be reached again
        through the current analysis and target-selection path.
        """
        ids = sorted({int(job_id) for job_id in job_ids if int(job_id) > 0})
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(
                f"""update meeting_alignment_jobs
                    set status='retry', attempts=0, locked_at=null,
                        available_at='', error='', decision_json='{{}}',
                        target_kind='', target_id='', target_title='',
                        mentions_json='[]', final_message='',
                        send_result_json='{{}}', updated_at=current_timestamp
                    where id in ({placeholders}) and status='failed'
                    returning id""",
                ids,
            ).fetchall()
            return [int(row["id"]) for row in rows]

    def baseline_meeting_alignment_jobs_before(
        self,
        activated_at: str,
    ) -> list[MeetingAlignmentJob]:
        with self._connect() as db:
            rows = db.execute(
                """
                update meeting_alignment_jobs
                set status='no_action',
                    locked_at=null,
                    available_at='',
                    error='',
                    decision_json='{}',
                    target_kind='',
                    target_id='',
                    target_title='',
                    mentions_json='[]',
                    final_message='',
                    send_result_json='{}',
                    updated_at=current_timestamp
                where datetime(ended_at) < datetime(?)
                  and status in (
                      'waiting',
                      'pending',
                      'processing',
                      'retry',
                      'ready_to_send',
                      'failed'
                  )
                  and send_result_json='{}'
                returning *
                """,
                (activated_at,),
            ).fetchall()
            jobs = [self._meeting_alignment_job_from_row(row) for row in rows]
            return sorted(jobs, key=lambda job: job.id)

    def reopen_meeting_alignment_job_for_replay(
        self,
        job_id: int,
        *,
        title: str,
        source_json: str,
        participants_json: str,
        ended_at: str,
        eligible_at: str,
    ) -> MeetingAlignmentJob | None:
        with self._connect() as db:
            row = db.execute(
                """
                update meeting_alignment_jobs
                set title=?,
                    source_json=?,
                    participants_json=?,
                    ended_at=?,
                    eligible_at=?,
                    status='pending',
                    attempts=0,
                    locked_at=null,
                    available_at='',
                    error='',
                    decision_json='{}',
                    target_kind='',
                    target_id='',
                    target_title='',
                    mentions_json='[]',
                    final_message='',
                    send_result_json='{}',
                    updated_at=current_timestamp
                where id=?
                  and status in ('no_action', 'failed')
                  and send_result_json='{}'
                returning *
                """,
                (
                    title,
                    source_json,
                    participants_json,
                    ended_at,
                    eligible_at,
                    job_id,
                ),
            ).fetchone()
            if row is None:
                return None
            return self._meeting_alignment_job_from_row(row)

    def begin_meeting_alignment_run(self, job_id: int) -> int:
        if job_id <= 0:
            raise ValueError("meeting alignment job id must be positive")
        with self._agent_run_write_transaction(None) as (db, _):
            parent = db.execute(
                "select 1 from meeting_alignment_jobs "
                "where id=? and status='processing'",
                (job_id,),
            ).fetchone()
            if parent is None:
                raise ValueError("meeting alignment job is not processing")
            active = db.execute(
                "select id from meeting_alignment_runs "
                "where job_id=? and status='running'",
                (job_id,),
            ).fetchone()
            if active is not None:
                return int(active["id"])
            cursor = db.execute(
                "insert into meeting_alignment_runs "
                "(job_id, status, finished_at, updated_at) "
                "values (?, 'running', '', current_timestamp)",
                (job_id,),
            )
            return int(cursor.lastrowid)

    def finish_meeting_alignment_run(
        self,
        run_id: int,
        *,
        status: str,
        codex_session_id: str = "",
        codex_transcript_start_line: int = 0,
        codex_transcript_end_line: int = 0,
        decision_json: str = "{}",
        audit_tool_events_json: str = "[]",
        audit_summary: str = "",
        error: str = "",
    ) -> None:
        if status not in MEETING_ALIGNMENT_RUN_TERMINAL_STATUSES:
            raise ValueError("meeting alignment run terminal status is invalid")
        values = (
            status,
            codex_session_id,
            codex_transcript_start_line,
            codex_transcript_end_line,
            decision_json,
            audit_tool_events_json,
            audit_summary,
            error,
        )
        with self._agent_run_write_transaction(None) as (db, _):
            row = db.execute(
                "select * from meeting_alignment_runs where id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError("meeting alignment run does not exist")
            if row["status"] != "running":
                actual = tuple(
                    row[field]
                    for field in (
                        "status",
                        "codex_session_id",
                        "codex_transcript_start_line",
                        "codex_transcript_end_line",
                        "decision_json",
                        "audit_tool_events_json",
                        "audit_summary",
                        "error",
                    )
                )
                if actual == values:
                    return
                raise ValueError("conflicting meeting alignment run terminal rewrite")
            db.execute(
                "update meeting_alignment_runs set status=?, codex_session_id=?, "
                "codex_transcript_start_line=?, codex_transcript_end_line=?, "
                "decision_json=?, audit_tool_events_json=?, audit_summary=?, error=?, "
                "finished_at=current_timestamp, updated_at=current_timestamp "
                "where id=? and status='running'",
                (*values, run_id),
            )

    def record_meeting_alignment_run(
        self,
        *,
        job_id: int,
        codex_session_id: str,
        decision_json: str,
        audit_summary: str,
        status: str,
        error: str,
        codex_transcript_start_line: int = 0,
        codex_transcript_end_line: int = 0,
        audit_tool_events_json: str = "[]",
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into meeting_alignment_runs (
                    job_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    decision_json,
                    audit_tool_events_json,
                    audit_summary,
                    status,
                    error,
                    finished_at,
                    updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?,
                        case when ?='running' then '' else current_timestamp end,
                        current_timestamp)
                """,
                (
                    job_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    decision_json,
                    audit_tool_events_json,
                    audit_summary,
                    status,
                    error,
                    status,
                ),
            )
            return int(cursor.lastrowid)

    def list_meeting_alignment_runs(
        self,
        job_id: int,
    ) -> list[MeetingAlignmentRun]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from meeting_alignment_runs
                where job_id=?
                order by id desc
                """,
                (job_id,),
            ).fetchall()
            return [
                self._meeting_alignment_run_from_row(row)
                for row in rows
            ]

    def get_meeting_alignment_run(
        self,
        run_id: int,
    ) -> MeetingAlignmentRun | None:
        with self._connect() as db:
            row = db.execute(
                "select * from meeting_alignment_runs where id=?",
                (run_id,),
            ).fetchone()
        return self._meeting_alignment_run_from_row(row) if row is not None else None

    def recovered_meeting_alignment_run_ids_since(
        self,
        created_since: str,
    ) -> set[int]:
        """Return retry runs superseded by a completed meeting outcome."""
        with self._connect() as db:
            rows = db.execute(
                """
                select earlier.id
                from meeting_alignment_runs as earlier
                join meeting_alignment_jobs as jobs on jobs.id=earlier.job_id
                where earlier.created_at>=?
                  and earlier.status in ('retry', 'failed')
                  and jobs.status in ('sent', 'no_action')
                  and exists (
                      select 1
                      from meeting_alignment_runs as later
                      where later.job_id=earlier.job_id
                        and later.id>earlier.id
                        and later.status in ('ready_to_send', 'sent', 'no_action')
                  )
                """,
                (created_since,),
            ).fetchall()
        return {int(row["id"]) for row in rows}

    def has_later_meeting_alignment_run(self, job_id: int, run_id: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                """
                select 1 from meeting_alignment_runs
                where job_id=? and id>?
                limit 1
                """,
                (job_id, run_id),
            ).fetchone()
        return row is not None

    def list_meeting_alignment_runs_for_codex_session(
        self,
        codex_session_id: str,
    ) -> list[MeetingAlignmentRun]:
        with self._connect() as db:
            rows = db.execute(
                """
                select * from meeting_alignment_runs
                where codex_session_id=?
                order by id desc
                """,
                (codex_session_id,),
            ).fetchall()
        return [self._meeting_alignment_run_from_row(row) for row in rows]

    def create_okr_review_request(
        self,
        *,
        conversation_id: str,
        conversation_title: str,
        trigger_message_id: str,
        trigger_sender: str,
        trigger_sender_user_id: str,
        trigger_text: str,
        period_label: str,
        period_start: str,
        period_end: str,
        okr_source_json: str,
    ) -> int:
        with self._connect() as db:
            db.execute(
                """
                insert into okr_review_requests (
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    trigger_sender,
                    trigger_sender_user_id,
                    trigger_text,
                    period_label,
                    period_start,
                    period_end,
                    okr_source_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(conversation_id, trigger_message_id) do update set
                    okr_source_json=excluded.okr_source_json,
                    status='pending',
                    error='',
                    codex_session_id='',
                    updated_at=current_timestamp
                where okr_review_requests.status='failed'
                """,
                (
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    trigger_sender,
                    trigger_sender_user_id,
                    trigger_text,
                    period_label,
                    period_start,
                    period_end,
                    okr_source_json,
                ),
            )
            row = db.execute(
                """
                select id from okr_review_requests
                where conversation_id=? and trigger_message_id=?
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            return int(row["id"])

    def claim_okr_review_requests(self, limit: int) -> list[OkrReviewRequest]:
        if limit <= 0:
            return []
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select *
                from okr_review_requests
                where status='pending'
                order by id
                limit ?
                """,
                (limit,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            db.execute(
                f"""
                update okr_review_requests
                set status='processing',
                    error='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                ids,
            )
            claimed = db.execute(
                f"""
                select *
                from okr_review_requests
                where id in ({placeholders})
                order by id
                """,
                ids,
            ).fetchall()
            return [self._okr_review_request_from_row(row) for row in claimed]

    def reset_recoverable_okr_review_requests(
        self, *, processing_max_age_seconds: int | None = None
    ) -> list[OkrReviewRequest]:
        with self._immediate_write_transaction() as db:
            params: list[object] = []
            processing_clause = "status='processing'"
            if processing_max_age_seconds is not None:
                if processing_max_age_seconds <= 0:
                    return []
                processing_clause = (
                    "status='processing' "
                    "and datetime(updated_at) <= datetime('now', ?)"
                )
                params.append(f"-{int(processing_max_age_seconds)} seconds")
            rows = db.execute(
                f"""
                select *
                from okr_review_requests
                where ({processing_clause})
                   or (
                       status='failed'
                       and error like 'codex session locked:%'
                       and not exists (
                           select 1
                           from codex_session_locks
                           where codex_session_locks.conversation_id =
                                 okr_review_requests.conversation_id
                             and datetime(codex_session_locks.locked_at) >
                                 datetime('now', ?)
                       )
                   )
                order by updated_at, id
                """,
                (*params, f"-{CODEX_SESSION_LOCK_STALE_SECONDS} seconds"),
            ).fetchall()
            request_ids = [row["id"] for row in rows]
            if not request_ids:
                return []
            owners = [f"okr_review:{request_id}" for request_id in request_ids]
            owner_placeholders = ",".join("?" for _ in owners)
            db.execute(
                f"""
                delete from codex_session_locks
                where owner in ({owner_placeholders})
                """,
                owners,
            )
            request_placeholders = ",".join("?" for _ in request_ids)
            db.execute(
                f"""
                update okr_review_requests
                set status='pending',
                    error='',
                    codex_session_id='',
                    updated_at=current_timestamp
                where id in ({request_placeholders})
                """,
                request_ids,
            )
            return [self._okr_review_request_from_row(row) for row in rows]

    def get_okr_review_request(self, request_id: int) -> OkrReviewRequest:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from okr_review_requests
                where id=?
                """,
                (request_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"okr review request not found: {request_id}")
            return self._okr_review_request_from_row(row)

    def mark_okr_review_request_done(
        self, request_id: int, *, codex_session_id: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update okr_review_requests
                set status='done',
                    error='',
                    codex_session_id=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (codex_session_id, request_id),
            )

    def mark_okr_review_request_failed(self, request_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update okr_review_requests
                set status='failed',
                    error=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (error, request_id),
            )

    def record_okr_review_run(
        self,
        *,
        request_id: int,
        codex_session_id: str,
        codex_transcript_start_line: int,
        codex_transcript_end_line: int,
        envelope_json: str,
        audit_tool_events_json: str,
        audit_summary: str,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into okr_review_runs (
                    request_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    envelope_json,
                    audit_tool_events_json,
                    audit_summary
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    envelope_json,
                    audit_tool_events_json,
                    audit_summary,
                ),
            )
            return int(cursor.lastrowid)

    def record_okr_review_item(
        self,
        *,
        request_id: int,
        objective_title: str,
        objective_weight: float,
        kr_title: str,
        kr_weight: float,
        item_json: str,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into okr_review_items (
                    request_id,
                    objective_title,
                    objective_weight,
                    kr_title,
                    kr_weight,
                    item_json
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    objective_title,
                    objective_weight,
                    kr_title,
                    kr_weight,
                    item_json,
                ),
            )
            return int(cursor.lastrowid)

    def upsert_conversation(
        self,
        conversation_id: str,
        title: str,
        single_chat: bool,
        codex_session_id: str | None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into conversations (
                    conversation_id, title, single_chat, codex_session_id
                )
                values (?, ?, ?, ?)
                on conflict(conversation_id) do update set
                    title=excluded.title,
                    single_chat=excluded.single_chat,
                    codex_session_id=coalesce(
                        excluded.codex_session_id,
                        conversations.codex_session_id
                    )
                """,
                (conversation_id, title, int(single_chat), codex_session_id),
            )

    def get_codex_session_id(self, conversation_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "select codex_session_id from conversations where conversation_id=?",
                (conversation_id,),
            ).fetchone()
            return None if row is None else row["codex_session_id"]

    def upsert_conversation_runtime_session(
        self,
        conversation_id: str,
        route_name: str,
        session_id: str,
        contract_hash: str = "",
    ) -> None:
        conversation_id = self._require_runtime_attempt_text(
            conversation_id, field="conversation_id"
        )
        route_name = self._require_runtime_attempt_text(route_name, field="route_name")
        session_id = self._require_runtime_attempt_text(session_id, field="session_id")
        contract_hash = contract_hash.strip()
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            self._upsert_conversation_runtime_session_in_connection(
                db, conversation_id, route_name, session_id, contract_hash, now_text
            )

    @staticmethod
    def _upsert_conversation_runtime_session_in_connection(
        db: sqlite3.Connection,
        conversation_id: str,
        route_name: str,
        session_id: str,
        contract_hash: str,
        now_text: str,
    ) -> None:
        db.execute(
                """
                insert into conversation_runtime_sessions (
                    conversation_id, route_name, session_id, contract_hash, updated_at
                ) values (?, ?, ?, ?, ?)
                on conflict(conversation_id, route_name) do update set
                    session_id=excluded.session_id,
                    contract_hash=excluded.contract_hash,
                    updated_at=excluded.updated_at
                """,
                (conversation_id, route_name, session_id, contract_hash, now_text),
        )
        if route_name == "codex_oauth":
            db.execute(
                    """
                    update conversations
                    set codex_session_id=?, codex_session_contract_hash=?
                    where conversation_id=?
                    """,
                    (session_id, contract_hash, conversation_id),
            )

    def get_conversation_runtime_session(
        self,
        conversation_id: str,
        route_name: str,
        *,
        required_contract_hash: str | None = None,
    ) -> str | None:
        with self._connect() as db:
            if required_contract_hash is None:
                row = db.execute(
                    """
                    select session_id from conversation_runtime_sessions
                    where conversation_id=? and route_name=?
                    """,
                    (conversation_id, route_name),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    select session_id from conversation_runtime_sessions
                    where conversation_id=? and route_name=? and contract_hash=?
                    """,
                    (conversation_id, route_name, required_contract_hash.strip()),
                ).fetchone()
            return None if row is None else str(row["session_id"])

    def get_conversation_runtime_session_contract_hash(
        self,
        conversation_id: str,
        route_name: str,
    ) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """
                select contract_hash from conversation_runtime_sessions
                where conversation_id=? and route_name=?
                """,
                (conversation_id, route_name),
            ).fetchone()
            return None if row is None else str(row["contract_hash"])

    def clear_conversation_runtime_session_if_matches(
        self,
        conversation_id: str,
        route_name: str,
        expected_session_id: str,
        *,
        additional_expected_session_ids: tuple[str, ...] = (),
    ) -> int:
        """Clear one Consumer route slot without deleting attempt/Audit evidence.

        The OAuth compatibility column is cleared in the same transaction when
        it names the same session. Other route slots are never affected.
        """
        conversation_id = self._require_runtime_attempt_text(
            conversation_id, field="conversation_id"
        )
        route_name = self._require_runtime_attempt_text(route_name, field="route_name")
        expected_session_id = self._require_runtime_attempt_text(
            expected_session_id, field="expected_session_id"
        )
        expected_session_ids = (expected_session_id,) + tuple(
            self._require_runtime_attempt_text(value, field="expected_session_id")
            for value in additional_expected_session_ids
        )
        placeholders = ",".join("?" for _ in expected_session_ids)
        with self._agent_run_write_transaction(None) as (db, _):
            cursor = db.execute(
                f"""
                delete from conversation_runtime_sessions
                where conversation_id=? and route_name=?
                  and session_id in ({placeholders})
                """,
                (conversation_id, route_name, *expected_session_ids),
            )
            cleared = cursor.rowcount
            if route_name == "codex_oauth":
                legacy = db.execute(
                    f"""
                    update conversations
                    set codex_session_id=null, codex_session_contract_hash=''
                    where conversation_id=?
                      and codex_session_id in ({placeholders})
                    """,
                    (conversation_id, *expected_session_ids),
                )
                cleared = max(cleared, legacy.rowcount)
            return cleared

    def open_runtime_route_pause(
        self,
        route_name: str,
        failure_code: str,
        retry_at: str | datetime,
    ) -> bool:
        route_name = self._require_runtime_attempt_text(route_name, field="route_name")
        failure_code = self._require_runtime_attempt_text(
            failure_code, field="failure_code"
        )
        if not failure_code.replace("_", "").isalnum():
            raise ValueError("failure_code must be a typed code")
        _, retry_at_text = _utc_store_time(retry_at)
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            existing = db.execute(
                "select retry_at from runtime_route_pauses where route_name=?",
                (route_name,),
            ).fetchone()
            if existing is not None and str(existing["retry_at"]) > now_text:
                return False
            if existing is not None:
                db.execute(
                    "delete from runtime_route_pauses where route_name=? and retry_at<=?",
                    (route_name, now_text),
                )
            cursor = db.execute(
                """
                insert into runtime_route_pauses (
                    route_name, failure_code, retry_at, opened_at, updated_at
                ) values (?, ?, ?, ?, ?)
                """,
                (route_name, failure_code, retry_at_text, now_text, now_text),
            )
            return cursor.rowcount == 1

    def active_runtime_route_pause(
        self,
        route_name: str,
        now: str | datetime | None = None,
    ) -> str | None:
        route_name = self._require_runtime_attempt_text(route_name, field="route_name")
        _, now_text = _utc_store_time(now)
        with self._agent_run_write_transaction(now) as (db, _):
            row = db.execute(
                """
                select failure_code, retry_at from runtime_route_pauses
                where route_name=?
                """,
                (route_name,),
            ).fetchone()
            if row is None:
                return None
            if str(row["retry_at"]) <= now_text:
                db.execute(
                    "delete from runtime_route_pauses where route_name=? and retry_at<=?",
                    (route_name, now_text),
                )
                return None
            return str(row["failure_code"])

    def close_runtime_route_pause(self, route_name: str) -> bool:
        route_name = self._require_runtime_attempt_text(route_name, field="route_name")
        with self._agent_run_write_transaction(None) as (db, _):
            cursor = db.execute(
                "delete from runtime_route_pauses where route_name=?", (route_name,)
            )
            return cursor.rowcount == 1

    def get_codex_session_contract_hash(self, conversation_id: str) -> str:
        with self._connect() as db:
            row = db.execute(
                """
                select codex_session_contract_hash
                from conversations
                where conversation_id=?
                """,
                (conversation_id,),
            ).fetchone()
            return "" if row is None else str(row["codex_session_contract_hash"] or "")

    def set_codex_session_contract_hash(
        self,
        conversation_id: str,
        contract_hash: str,
    ) -> int:
        if not contract_hash.strip():
            raise ValueError("contract_hash must be non-empty")
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            cursor = db.execute(
                """
                update conversations
                set codex_session_contract_hash=?
                where conversation_id=?
                """,
                (contract_hash, conversation_id),
            )
            db.execute(
                """
                insert into conversation_runtime_sessions (
                    conversation_id, route_name, session_id,
                    contract_hash, updated_at
                )
                select conversation_id, 'codex_oauth', codex_session_id, ?, ?
                from conversations
                where conversation_id=? and codex_session_id is not null
                  and codex_session_id<>''
                on conflict(conversation_id, route_name) do update set
                    session_id=excluded.session_id,
                    contract_hash=excluded.contract_hash,
                    updated_at=excluded.updated_at
                """,
                (contract_hash, now_text, conversation_id),
            )
            return cursor.rowcount

    def acquire_codex_session_lock(self, conversation_id: str, owner: str) -> bool:
        if not conversation_id.strip():
            raise ValueError("missing conversation_id")
        if not owner.strip():
            raise ValueError("missing lock owner")
        def acquire() -> bool:
            with self._connect() as db:
                db.execute(
                    """
                    delete from codex_session_locks
                    where conversation_id=?
                      and datetime(locked_at) <= datetime('now', ?)
                    """,
                    (
                        conversation_id,
                        f"-{CODEX_SESSION_LOCK_STALE_SECONDS} seconds",
                    ),
                )
                cursor = db.execute(
                    """
                    insert or ignore into codex_session_locks (conversation_id, owner)
                    values (?, ?)
                    """,
                    (conversation_id, owner),
                )
                if cursor.rowcount == 1:
                    return True
                row = db.execute(
                    "select owner from codex_session_locks where conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                return row is not None and str(row["owner"]) == owner

        return self._retry_codex_session_lock_operation(acquire)

    def release_codex_session_lock(self, conversation_id: str, owner: str) -> bool:
        if not conversation_id.strip():
            raise ValueError("missing conversation_id")
        if not owner.strip():
            raise ValueError("missing lock owner")
        def release() -> bool:
            with self._connect() as db:
                cursor = db.execute(
                    """
                    delete from codex_session_locks
                    where conversation_id=? and owner=?
                    """,
                    (conversation_id, owner),
                )
                if cursor.rowcount == 1:
                    return True
                row = db.execute(
                    "select 1 from codex_session_locks where conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                return row is None

        return self._retry_codex_session_lock_operation(release)

    def renew_codex_session_lock(
        self,
        conversation_id: str,
        owner: str,
        *,
        now: str | datetime | None = None,
    ) -> bool:
        if not conversation_id.strip():
            raise ValueError("missing conversation_id")
        if not owner.strip():
            raise ValueError("missing lock owner")
        now_value, now_text = _utc_store_time(now)
        stale_before = (
            now_value - timedelta(seconds=CODEX_SESSION_LOCK_STALE_SECONDS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        def renew() -> bool:
            with self._connect() as db:
                cursor = db.execute(
                    """
                    update codex_session_locks
                    set locked_at=?
                    where conversation_id=? and owner=? and locked_at>?
                    """,
                    (now_text, conversation_id, owner, stale_before),
                )
                return cursor.rowcount == 1

        return self._retry_codex_session_lock_operation(renew)

    @staticmethod
    def _retry_codex_session_lock_operation(operation: Callable[[], bool]) -> bool:
        for attempt in range(CODEX_SESSION_LOCK_RETRY_ATTEMPTS):
            try:
                return operation()
            except OSError as exc:
                if exc.errno != errno.EDEADLK or attempt + 1 == CODEX_SESSION_LOCK_RETRY_ATTEMPTS:
                    raise
                time.sleep(CODEX_SESSION_LOCK_RETRY_DELAY_SECONDS * (attempt + 1))
        raise AssertionError("unreachable codex session lock retry state")

    def codex_session_lock(self, conversation_id: str, owner: str) -> CodexSessionLock:
        return CodexSessionLock(self, conversation_id, owner)

    def update_reply_task_trigger(
        self,
        task_id: int,
        *,
        trigger_text: str,
        trigger_message_json: str,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set trigger_text=?,
                    trigger_message_json=?,
                    updated_at=current_timestamp
                where id=?
                  and status='pending'
                  and attempts=0
                """,
                (trigger_text, trigger_message_json, task_id),
            )
            return cursor.rowcount

    def update_pending_reply_task_trigger_for_message(
        self,
        conversation_id: str,
        trigger_message_id: str,
        *,
        trigger_text: str,
        trigger_message_json: str,
        channel: str = "dingtalk",
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set trigger_text=?,
                    trigger_message_json=?,
                    updated_at=current_timestamp
                where channel=?
                  and conversation_id=?
                  and trigger_message_id=?
                  and status='pending'
                  and attempts=0
                  and (
                    trigger_text != ?
                    or trigger_message_json != ?
                  )
                """,
                (
                    trigger_text,
                    trigger_message_json,
                    channel,
                    conversation_id,
                    trigger_message_id,
                    trigger_text,
                    trigger_message_json,
                ),
            )
            return cursor.rowcount

    def replace_pending_single_chat_reply_task_trigger(
        self,
        *,
        conversation_id: str,
        trigger_message_id: str,
        trigger_create_time: str,
        trigger_sender: str,
        trigger_text: str,
        trigger_message_json: str,
        available_at: str = "",
        error: str = "",
        channel: str = "dingtalk",
    ) -> int:
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            target = db.execute(
                """
                select id, execution_generation, trigger_message_id,
                       trigger_create_time, trigger_sender, trigger_text,
                       trigger_message_json, available_at, error
                from reply_tasks
                where channel=?
                  and conversation_id=?
                  and single_chat=1
                  and status='pending'
                  and attempts=0
                  and trigger_create_time <= ?
                order by trigger_create_time desc, id desc
                limit 1
                """,
                (channel, conversation_id, trigger_create_time),
            ).fetchone()
            if target is None:
                return 0
            task_id = int(target["id"])
            current_generation = str(target["execution_generation"])
            unchanged = all(
                str(target[field]) == value
                for field, value in (
                    ("trigger_message_id", trigger_message_id),
                    ("trigger_create_time", trigger_create_time),
                    ("trigger_sender", trigger_sender),
                    ("trigger_text", trigger_text),
                    ("trigger_message_json", trigger_message_json),
                    ("available_at", available_at),
                    ("error", error),
                )
            )
            if unchanged:
                return 0
            self._supersede_running_agent_runs(
                db,
                task_id,
                current_generation,
                now_text=now_text,
            )
            execution_generation = uuid4().hex
            cursor = db.execute(
                """
                update reply_tasks
                set trigger_message_id=?,
                    trigger_create_time=?,
                    trigger_sender=?,
                    trigger_text=?,
                    trigger_message_json=?,
                    execution_generation=?,
                    available_at=?,
                    error=?,
                    updated_at=?
                where id=?
                  and execution_generation=?
                  and (
                    trigger_message_id != ?
                    or trigger_create_time != ?
                    or trigger_sender != ?
                    or trigger_text != ?
                    or trigger_message_json != ?
                    or available_at != ?
                    or error != ?
                  )
                """,
                (
                    trigger_message_id,
                    trigger_create_time,
                    trigger_sender,
                    trigger_text,
                    trigger_message_json,
                    execution_generation,
                    available_at,
                    error,
                    now_text,
                    task_id,
                    current_generation,
                    trigger_message_id,
                    trigger_create_time,
                    trigger_sender,
                    trigger_text,
                    trigger_message_json,
                    available_at,
                    error,
                ),
            )
            db.execute(
                """
                delete from reply_tasks
                where channel=?
                  and conversation_id=?
                  and single_chat=1
                  and status='pending'
                  and attempts=0
                  and id != ?
                """,
                (channel, conversation_id, task_id),
            )
            return cursor.rowcount

    def reset_codex_sessions(self) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update conversations
                set codex_session_id=null, codex_session_contract_hash=''
                where codex_session_id is not null and codex_session_id != ''
                """
            )
            return cursor.rowcount

    def clear_codex_session(self, conversation_id: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update conversations
                set codex_session_id=null, codex_session_contract_hash=''
                where conversation_id=?
                """,
                (conversation_id,),
            )
            return cursor.rowcount

    def clear_codex_session_if_matches(
        self,
        conversation_id: str,
        expected_session_id: str,
    ) -> int:
        if not expected_session_id.strip():
            raise ValueError("expected_session_id must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update conversations
                set codex_session_id=null, codex_session_contract_hash=''
                where conversation_id=? and codex_session_id=?
                """,
                (conversation_id, expected_session_id),
            )
            return cursor.rowcount

    def clear_agent_run_session(
        self,
        reply_task_id: int,
        execution_generation: str,
        *,
        role: AgentRole,
        proposal_revision: int,
        turn_attempt: int,
    ) -> int:
        role = AgentRole(role)
        with self._connect() as db:
            cursor = db.execute(
                """
                update agent_runs
                set codex_session_id=''
                where reply_task_id=? and execution_generation=? and role=?
                  and proposal_revision=? and turn_attempt=?
                """,
                (
                    reply_task_id,
                    execution_generation,
                    role.value,
                    proposal_revision,
                    turn_attempt,
                ),
            )
            return cursor.rowcount

    def list_codex_conversations(self) -> list[ConversationRecord]:
        with self._connect() as db:
            rows = db.execute(
                """
                select conversation_id, title, single_chat, codex_session_id
                from conversations
                where codex_session_id is not null and codex_session_id != ''
                order by title, conversation_id
                """
            ).fetchall()
            return [
                ConversationRecord(
                    conversation_id=row["conversation_id"],
                    title=row["title"],
                    single_chat=bool(row["single_chat"]),
                    codex_session_id=row["codex_session_id"],
                )
                for row in rows
            ]

    def list_recent_single_chat_conversations(
        self,
        since_utc: str,
        limit: int,
    ) -> list[ConversationRecord]:
        with self._connect() as db:
            rows = db.execute(
                """
                select
                    c.conversation_id,
                    c.title,
                    c.single_chat,
                    c.codex_session_id,
                    max(s.seen_at) as latest_seen_at
                from conversations c
                join seen_messages s on s.conversation_id=c.conversation_id
                where c.single_chat=1 and s.seen_at >= ?
                group by c.conversation_id, c.title, c.single_chat, c.codex_session_id
                order by latest_seen_at desc
                limit ?
                """,
                (since_utc, limit),
            ).fetchall()
            return [
                ConversationRecord(
                    conversation_id=row["conversation_id"],
                    title=row["title"],
                    single_chat=bool(row["single_chat"]),
                    codex_session_id=row["codex_session_id"],
                )
                for row in rows
            ]

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._connect() as db:
            row = db.execute(
                """
                select conversation_id, title, single_chat, codex_session_id
                from conversations
                where conversation_id=?
                """,
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            return ConversationRecord(
                conversation_id=row["conversation_id"],
                title=row["title"],
                single_chat=bool(row["single_chat"]),
                codex_session_id=row["codex_session_id"],
            )

    def find_single_chat_conversation_by_title(
        self, title: str
    ) -> ConversationRecord | None:
        with self._connect() as db:
            rows = db.execute(
                """
                select conversation_id, title, single_chat, codex_session_id
                from conversations
                where title=? and single_chat=1
                order by conversation_id
                limit 2
                """,
                (title,),
            ).fetchall()
            if len(rows) != 1:
                return None
            row = rows[0]
            return ConversationRecord(
                conversation_id=row["conversation_id"],
                title=row["title"],
                single_chat=bool(row["single_chat"]),
                codex_session_id=row["codex_session_id"],
            )

    def find_conversation_by_title(self, title: str) -> ConversationRecord | None:
        with self._connect() as db:
            rows = db.execute(
                """
                select conversation_id, title, single_chat, codex_session_id
                from conversations
                where title=?
                order by single_chat, conversation_id
                limit 2
                """,
                (title,),
            ).fetchall()
            if len(rows) != 1:
                return None
            row = rows[0]
            return ConversationRecord(
                conversation_id=row["conversation_id"],
                title=row["title"],
                single_chat=bool(row["single_chat"]),
                codex_session_id=row["codex_session_id"],
            )

    def has_seen(self, message_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "select 1 from seen_messages where message_id=?",
                (message_id,),
            ).fetchone()
            return row is not None

    def mark_seen(self, message_id: str, conversation_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert or ignore into seen_messages (message_id, conversation_id)
                values (?, ?)
                """,
                (message_id, conversation_id),
            )
            return cursor.rowcount == 1

    def record_sent_reply(
        self,
        conversation_id: str,
        trigger_message_id: str,
        reply_text: str,
        *,
        send_result_json: str = "",
        recall_key: str = "",
        feedback_token: str = "",
    ) -> None:
        if not feedback_token:
            feedback_context = extract_configured_feedback_link_context(
                reply_text,
                vercel_base_url=feedback_spike_vercel_base_url(),
            )
            feedback_token = (
                feedback_context.feedback_token
                if feedback_context is not None
                else ""
            )
        with self._connect() as db:
            db.execute(
                """
                insert into sent_replies (
                    conversation_id,
                    trigger_message_id,
                    reply_text,
                    send_result_json,
                    recall_key,
                    feedback_token
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    trigger_message_id,
                    reply_text,
                    send_result_json,
                    recall_key,
                    feedback_token,
                ),
            )

    def list_confirmed_audit_runs_missing_sent_reply(
        self,
        *,
        limit: int = 50,
    ) -> list[AgentRun]:
        """Return completed DingTalk audits whose verified direct send lacks a ledger row."""
        if limit <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select agent_runs.*
                from agent_runs
                join reply_tasks on reply_tasks.id=agent_runs.reply_task_id
                join agent_runs as consumer_runs
                  on consumer_runs.id=agent_runs.parent_agent_run_id
                where agent_runs.role='audit'
                  and agent_runs.status='completed'
                  and agent_runs.side_effect_state='confirmed'
                  and reply_tasks.channel='dingtalk'
                  and consumer_runs.role='consumer'
                  and (
                      instr(consumer_runs.final_result_json,
                            '"operation":"chat +messages-send"') > 0
                      or instr(consumer_runs.final_result_json,
                               '"operation":"chat message send"') > 0
                  )
                  and (
                      instr(consumer_runs.final_result_json,
                            '"open_dingtalk_id"') > 0
                      or instr(consumer_runs.final_result_json,
                               '"user"') > 0
                  )
                  and not exists (
                      select 1
                      from sent_replies
                      where sent_replies.conversation_id=reply_tasks.conversation_id
                        and sent_replies.trigger_message_id=reply_tasks.trigger_message_id
                  )
                order by agent_runs.id asc
                limit ?
                """,
                (limit,),
            ).fetchall()
            return [self._agent_run_from_row(row, db=db) for row in rows]

    def record_confirmed_sent_reply_if_absent(
        self,
        *,
        audit_run_id: int,
        reply_text: str,
        send_result_json: str,
    ) -> bool:
        """Atomically backfill a delivery ledger row after a verified audit readback.

        The caller must derive the reply from the persisted Consumer and Audit
        contracts.  This method never performs an external delivery.
        """
        if not reply_text.strip():
            raise ValueError("reply_text must be non-empty")
        feedback_context = extract_configured_feedback_link_context(
            reply_text,
            vercel_base_url=feedback_spike_vercel_base_url(),
        )
        feedback_token = (
            feedback_context.feedback_token if feedback_context is not None else ""
        )
        with self._immediate_write_transaction() as db:
            row = db.execute(
                """
                select reply_tasks.conversation_id, reply_tasks.trigger_message_id
                from agent_runs
                join reply_tasks on reply_tasks.id=agent_runs.reply_task_id
                where agent_runs.id=?
                  and agent_runs.role='audit'
                  and agent_runs.status='completed'
                  and agent_runs.side_effect_state='confirmed'
                  and reply_tasks.channel='dingtalk'
                """,
                (audit_run_id,),
            ).fetchone()
            if row is None:
                return False
            cursor = db.execute(
                """
                insert into sent_replies (
                    conversation_id, trigger_message_id, reply_text, send_result_json,
                    feedback_token
                )
                select ?, ?, ?, ?, ?
                where not exists (
                    select 1 from sent_replies
                    where conversation_id=? and trigger_message_id=?
                )
                """,
                (
                    row["conversation_id"],
                    row["trigger_message_id"],
                    reply_text,
                    send_result_json,
                    feedback_token,
                    row["conversation_id"],
                    row["trigger_message_id"],
                ),
            )
            return cursor.rowcount == 1

    def has_sent_reply_for_trigger(
        self,
        conversation_id: str,
        trigger_message_id: str,
    ) -> bool:
        with self._connect() as db:
            row = db.execute(
                """
                select 1
                from sent_replies
                where conversation_id=? and trigger_message_id=?
                limit 1
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            return row is not None

    def sent_reply_exists(
        self,
        conversation_id: str,
        trigger_message_id: str,
    ) -> bool:
        return self.has_sent_reply_for_trigger(
            conversation_id,
            trigger_message_id,
        )

    def get_sent_reply(
        self, conversation_id: str, trigger_message_id: str
    ) -> SentReply | None:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from sent_replies
                where conversation_id=? and trigger_message_id=?
                order by id desc
                limit 1
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            if row is None:
                return None
            return SentReply.model_validate(dict(row))

    def list_sent_replies_after(self, sent_reply_id: int) -> list[SentReply]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from sent_replies
                where id > ?
                order by id asc
                """,
                (sent_reply_id,),
            ).fetchall()
            return [SentReply.model_validate(dict(row)) for row in rows]

    def list_sent_replies_for_attempts(
        self, attempts: list[ReplyAttempt]
    ) -> dict[tuple[str, str], SentReply]:
        keys = [
            (attempt.conversation_id, attempt.trigger_message_id)
            for attempt in attempts
        ]
        if not keys:
            return {}
        placeholders = ",".join(["(?, ?)"] * len(keys))
        args = [value for key in keys for value in key]
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from sent_replies
                where (conversation_id, trigger_message_id) in ({placeholders})
                order by id desc
                """,
                args,
            ).fetchall()
            result: dict[tuple[str, str], SentReply] = {}
            for row in rows:
                reply = SentReply.model_validate(dict(row))
                key = (reply.conversation_id, reply.trigger_message_id)
                if key not in result:
                    result[key] = reply
            return result

    def list_sent_replies_with_feedback_tokens(
        self, limit: int = 500
    ) -> list[SentReply]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from sent_replies
                where trim(feedback_token) <> ''
                order by sent_at desc, id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
            return [SentReply.model_validate(dict(row)) for row in rows]

    def list_sent_replies_waiting_for_feedback_events(
        self, limit: int = 50
    ) -> list[SentReply]:
        with self._connect() as db:
            rows = db.execute(
                """
                select sr.*
                from sent_replies sr
                where trim(sr.feedback_token) <> ''
                  and not exists (
                      select 1
                      from feedback_events fe
                      where fe.feedback_token = sr.feedback_token
                  )
                order by sr.sent_at desc, sr.id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
            return [SentReply.model_validate(dict(row)) for row in rows]

    def list_sent_replies_with_feedback_tokens_for_conversation(
        self,
        conversation_id: str,
        *,
        limit: int = 20,
    ) -> list[SentReply]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from sent_replies
                where conversation_id=?
                  and trim(feedback_token) <> ''
                order by sent_at desc, id desc
                limit ?
                """,
                (conversation_id, limit),
            ).fetchall()
            return [SentReply.model_validate(dict(row)) for row in rows]

    def feedback_pressure_stats(
        self,
        conversation_id: str,
        *,
        now_utc: str | None = None,
    ) -> FeedbackPressureStats:
        now_expression = "current_timestamp" if now_utc is None else "?"
        args = [conversation_id]
        if now_utc is not None:
            args.extend([now_utc, now_utc])
        with self._connect() as db:
            row = db.execute(
                f"""
                with latest_feedback as (
                    select max(datetime(coalesce(
                        nullif(fe.received_at, ''),
                        fe.updated_at,
                        fe.created_at
                    ))) as latest_feedback_at
                    from sent_replies sr
                    join feedback_events fe
                        on fe.feedback_token = sr.feedback_token
                    where sr.conversation_id=?
                      and trim(sr.feedback_token) <> ''
                ),
                unanswered as (
                    select sr.*
                    from sent_replies sr
                    left join latest_feedback lf
                    where sr.conversation_id=?
                      and trim(sr.feedback_token) <> ''
                      and not exists (
                          select 1
                          from feedback_events fe
                          where fe.feedback_token = sr.feedback_token
                      )
                      and (
                          lf.latest_feedback_at is null
                          or datetime(sr.sent_at) > lf.latest_feedback_at
                      )
                )
                select
                    count(*) as unanswered_since_last_feedback,
                    sum(
                        case
                            when datetime(sent_at)
                                <= datetime({now_expression}, '-7 days')
                            then 1
                            else 0
                        end
                    ) as unanswered_older_than_7_days,
                    sum(
                        case
                            when datetime(sent_at)
                                <= datetime({now_expression}, '-10 days')
                            then 1
                            else 0
                        end
                    ) as unanswered_older_than_10_days
                from unanswered
                """,
                [conversation_id, *args],
            ).fetchone()
        if row is None:
            return FeedbackPressureStats()
        return FeedbackPressureStats(
            unanswered_since_last_feedback=int(
                row["unanswered_since_last_feedback"] or 0
            ),
            unanswered_older_than_7_days=int(
                row["unanswered_older_than_7_days"] or 0
            ),
            unanswered_older_than_10_days=int(
                row["unanswered_older_than_10_days"] or 0
            ),
        )

    def upsert_feedback_event(
        self,
        *,
        key: str,
        feedback_token: str,
        rating: str = "",
        rating_label: str = "",
        comment: str = "",
        original_text: str = "",
        reply_text: str = "",
        source: str = "",
        received_at: str = "",
        raw_json: str = "{}",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into feedback_events (
                    key,
                    feedback_token,
                    rating,
                    rating_label,
                    comment,
                    original_text,
                    reply_text,
                    source,
                    received_at,
                    raw_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(key) do update set
                    feedback_token=excluded.feedback_token,
                    rating=excluded.rating,
                    rating_label=excluded.rating_label,
                    comment=excluded.comment,
                    original_text=excluded.original_text,
                    reply_text=excluded.reply_text,
                    source=excluded.source,
                    received_at=excluded.received_at,
                    raw_json=excluded.raw_json,
                    updated_at=current_timestamp
                """,
                (
                    key,
                    feedback_token,
                    rating,
                    rating_label,
                    comment,
                    original_text,
                    reply_text,
                    source,
                    received_at,
                    raw_json,
                ),
            )
            # Keep a durable, independently mutable processing projection while
            # preserving the original feedback event as the source of truth.
            db.execute(
                """
                insert into feedback_processing_items (feedback_key, status, resolved_at)
                values (?, case when trim((select resolved_at from feedback_events where key=?)) <> ''
                              then 'resolved' else 'pending' end,
                        coalesce((select resolved_at from feedback_events where key=?), ''))
                on conflict(feedback_key) do update set
                    status=case when trim((select resolved_at from feedback_events where key=excluded.feedback_key)) <> ''
                                then 'resolved' else feedback_processing_items.status end,
                    resolved_at=case when trim((select resolved_at from feedback_events where key=excluded.feedback_key)) <> ''
                                     then (select resolved_at from feedback_events where key=excluded.feedback_key)
                                     else feedback_processing_items.resolved_at end,
                    updated_at=current_timestamp
                """,
                (key, key, key),
            )

    @staticmethod
    def _feedback_processing_item_from_row(row: sqlite3.Row) -> FeedbackProcessingItem:
        values = dict(row)
        for field in ("attempt_id", "agent_run_id"):
            raw = values.get(field, 0)
            if raw in (None, ""):
                values[field] = 0
            else:
                try:
                    values[field] = int(raw)
                except (TypeError, ValueError):
                    values[field] = 0
        for field in ("test_evidence", "restart_evidence", "health_evidence"):
            raw = values.pop(f"{field}_json", "{}")
            try:
                parsed = json.loads(raw or "{}")
            except (TypeError, ValueError):
                parsed = {}
            values[field] = parsed if isinstance(parsed, dict) else {}
        return FeedbackProcessingItem.model_validate(values)

    @staticmethod
    def _feedback_processing_batch_from_row(
        row: sqlite3.Row,
    ) -> FeedbackProcessingBatch:
        return FeedbackProcessingBatch.model_validate(dict(row))

    def create_feedback_processing_batch(
        self,
        feedback_keys: Sequence[str] = (),
        *,
        batch_id: str | None = None,
    ) -> FeedbackProcessingBatch:
        """Create (or reopen) a processing batch and seed its requested items."""
        cleaned_batch_id = (batch_id or uuid4().hex).strip()
        if not cleaned_batch_id:
            raise ValueError("batch_id must not be empty")
        keys = list(dict.fromkeys(key.strip() for key in feedback_keys if key.strip()))
        with self._immediate_write_transaction() as db:
            existing_batch = db.execute(
                "select * from feedback_processing_batches where batch_id=?",
                (cleaned_batch_id,),
            ).fetchone()
            if existing_batch is not None:
                existing_keys = {
                    str(row["feedback_key"])
                    for row in db.execute(
                        "select feedback_key from feedback_processing_items where batch_id=?",
                        (cleaned_batch_id,),
                    )
                }
                if existing_keys != set(keys):
                    raise FeedbackProcessingBatchError(FEEDBACK_PROCESSING_BATCH_ERROR)
                return self._feedback_processing_batch_from_row(existing_batch)
            if keys:
                placeholders = ",".join("?" for _ in keys)
                source_rows = db.execute(
                    f"""
                    select key, resolved_at
                    from feedback_events
                    where key in ({placeholders})
                    """,
                    keys,
                ).fetchall()
                source_by_key = {str(row["key"]): row for row in source_rows}
                if len(source_rows) != len(keys) or any(
                    str(source_by_key[key]["resolved_at"] or "").strip()
                    for key in keys
                    if key in source_by_key
                ):
                    raise FeedbackProcessingBatchError(FEEDBACK_PROCESSING_BATCH_ERROR)
            conflicting = db.execute(
                """
                select fe.key as feedback_key from feedback_events fe
                left join feedback_processing_items pi on pi.feedback_key=fe.key
                where fe.key in ({})
                  and (
                      trim(pi.batch_id) <> ''
                      or pi.status='resolved'
                      or trim(coalesce(fe.resolved_at, '')) <> ''
                  )
                """.format(",".join("?" for _ in keys) or "null"),
                keys,
            ).fetchall()
            if conflicting:
                raise FeedbackProcessingBatchError(FEEDBACK_PROCESSING_BATCH_ERROR)
            db.execute(
                """
                insert into feedback_processing_batches (batch_id, requested_count)
                values (?, ?)
                on conflict(batch_id) do update set
                    requested_count=excluded.requested_count,
                    updated_at=current_timestamp
                """,
                (cleaned_batch_id, len(keys)),
            )
            for key in keys:
                db.execute(
                    """
                    insert into feedback_processing_items (
                        feedback_key, batch_id, status, resolved_at
                    )
                    values (
                        ?, ?,
                        case when trim(coalesce((select resolved_at from feedback_events where key=?), '')) <> ''
                             then 'resolved' else 'pending' end,
                        coalesce((select resolved_at from feedback_events where key=?), '')
                    )
                    on conflict(feedback_key) do update set
                        batch_id=case
                            when feedback_processing_items.status='resolved'
                            then feedback_processing_items.batch_id
                            else excluded.batch_id
                        end,
                        status=case when trim(coalesce((select resolved_at from feedback_events where key=excluded.feedback_key), '')) <> ''
                                    then 'resolved' else feedback_processing_items.status end,
                        resolved_at=case when trim(coalesce((select resolved_at from feedback_events where key=excluded.feedback_key), '')) <> ''
                                         then (select resolved_at from feedback_events where key=excluded.feedback_key)
                                         else feedback_processing_items.resolved_at end,
                        updated_at=current_timestamp
                    """,
                    (key, cleaned_batch_id, key, key),
                )
            row = db.execute(
                "select * from feedback_processing_batches where batch_id=?",
                (cleaned_batch_id,),
            ).fetchone()
        assert row is not None
        return self._feedback_processing_batch_from_row(row)

    def claim_feedback_processing_items(
        self,
        batch_id: str,
        feedback_keys: Sequence[str],
    ) -> list[FeedbackProcessingItem]:
        """Atomically claim requested feedback keys for one processing batch."""
        cleaned_batch_id = batch_id.strip()
        keys = list(dict.fromkeys(key.strip() for key in feedback_keys if key.strip()))
        if not cleaned_batch_id or not keys:
            return []
        with self._immediate_write_transaction() as db:
            existing_batch = db.execute(
                "select 1 from feedback_processing_batches where batch_id=?",
                (cleaned_batch_id,),
            ).fetchone()
            if existing_batch is not None:
                existing_keys = {
                    str(row["feedback_key"])
                    for row in db.execute(
                        "select feedback_key from feedback_processing_items where batch_id=?",
                        (cleaned_batch_id,),
                    )
                }
                if existing_keys != set(keys):
                    raise FeedbackProcessingBatchError(FEEDBACK_PROCESSING_BATCH_ERROR)
            placeholders = ",".join("?" for _ in keys)
            existing = db.execute(
                f"""
                select fe.key as feedback_key,
                       fe.resolved_at as event_resolved_at,
                       coalesce(pi.status, 'pending') as item_status,
                       coalesce(pi.batch_id, '') as item_batch_id
                from feedback_events fe
                left join feedback_processing_items pi on pi.feedback_key=fe.key
                where fe.key in ({placeholders})
                """,
                keys,
            ).fetchall()
            by_key = {str(row["feedback_key"]): row for row in existing}
            # A retry from the same Workbench turn is idempotent: return the
            # original claims without creating another batch or duplicate row.
            same_batch_processing = all(
                key in by_key
                and not str(by_key[key]["event_resolved_at"] or "").strip()
                and str(by_key[key]["item_status"] or "") == "processing"
                and str(by_key[key]["item_batch_id"] or "").strip() == cleaned_batch_id
                for key in keys
            ) and len(existing) == len(keys)
            if same_batch_processing:
                rows = db.execute(
                    f"""
                    select * from feedback_processing_items
                    where feedback_key in ({placeholders}) and batch_id=?
                    order by created_at asc, feedback_key asc
                    """,
                    [*keys, cleaned_batch_id],
                ).fetchall()
                return [self._feedback_processing_item_from_row(row) for row in rows]
            invalid = [
                key for key in keys
                if key not in by_key
                or str(by_key[key]["event_resolved_at"] or "").strip()
                or str(by_key[key]["item_status"] or "") != "pending"
                or (
                    str(by_key[key]["item_batch_id"] or "").strip()
                    and str(by_key[key]["item_batch_id"]).strip() != cleaned_batch_id
                )
            ]
            if invalid or len(existing) != len(keys):
                raise FeedbackProcessingClaimError(FEEDBACK_PROCESSING_CLAIM_ERROR)
            db.execute(
                """
                insert or ignore into feedback_processing_batches
                    (batch_id, requested_count)
                values (?, ?)
                """,
                (cleaned_batch_id, len(keys)),
            )
            cursor = db.execute(
                f"""
                update feedback_processing_items
                set batch_id=?, status='processing', updated_at=current_timestamp
                where feedback_key in ({placeholders})
                  and status in ('pending', 'processing')
                """,
                [cleaned_batch_id, *keys],
            )
            if cursor.rowcount != len(keys):
                raise FeedbackProcessingClaimError(FEEDBACK_PROCESSING_CLAIM_ERROR)
            db.execute(
                """
                update feedback_processing_batches
                set status='processing', requested_count=?, updated_at=current_timestamp
                where batch_id=?
                """,
                (len(keys), cleaned_batch_id),
            )
            rows = db.execute(
                f"""
                select * from feedback_processing_items
                where feedback_key in ({placeholders}) and batch_id=?
                  and status='processing'
                order by created_at asc, feedback_key asc
                """,
                [*keys, cleaned_batch_id],
            ).fetchall()
            if len(rows) != len(keys):
                raise FeedbackProcessingClaimError(FEEDBACK_PROCESSING_CLAIM_ERROR)
        return [self._feedback_processing_item_from_row(row) for row in rows]

    def associate_feedback_processing_turn(
        self,
        feedback_key: str,
        *,
        workbench_task_id: str = "",
        workbench_turn_id: str = "",
        attempt_id: int = 0,
        agent_run_id: int = 0,
    ) -> FeedbackProcessingItem | None:
        cleaned_key = feedback_key.strip()
        if not cleaned_key:
            return None
        with self._connect() as db:
            db.execute(
                """
                update feedback_processing_items
                set workbench_task_id=?, workbench_turn_id=?, attempt_id=?,
                    agent_run_id=?, updated_at=current_timestamp
                where feedback_key=?
                """,
                (
                    workbench_task_id,
                    workbench_turn_id,
                    attempt_id,
                    agent_run_id,
                    cleaned_key,
                ),
            )
            row = db.execute(
                "select * from feedback_processing_items where feedback_key=?",
                (cleaned_key,),
            ).fetchone()
        return self._feedback_processing_item_from_row(row) if row else None

    def get_feedback_processing_batch(
        self, batch_id: str
    ) -> FeedbackProcessingBatch | None:
        with self._connect() as db:
            row = db.execute(
                "select * from feedback_processing_batches where batch_id=?",
                (batch_id.strip(),),
            ).fetchone()
        return self._feedback_processing_batch_from_row(row) if row else None

    def get_feedback_processing_item(
        self, feedback_key: str
    ) -> FeedbackProcessingItem | None:
        with self._connect() as db:
            row = db.execute(
                "select * from feedback_processing_items where feedback_key=?",
                (feedback_key.strip(),),
            ).fetchone()
        return self._feedback_processing_item_from_row(row) if row else None

    def patch_feedback_processing_item_evidence(
        self,
        feedback_key: str,
        *,
        test_evidence: dict[str, object] | None = None,
        restart_evidence: dict[str, object] | None = None,
        health_evidence: dict[str, object] | None = None,
        commit_sha: str | None = None,
        note: str | None = None,
        status: str | None = None,
    ) -> FeedbackProcessingItem | None:
        cleaned_key = feedback_key.strip()
        if not cleaned_key:
            return None
        allowed_statuses = {"pending", "processing", "resolved"}
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"unsupported feedback processing status: {status}")
        if status == "resolved":
            raise ValueError("only batch resolution may mark feedback resolved")
        if status == "processing":
            raise ValueError("only atomic batch claim may mark feedback processing")
        assignments: list[str] = ["updated_at=current_timestamp"]
        args: list[object] = []
        for field, value in (
            ("test_evidence_json", test_evidence),
            ("restart_evidence_json", restart_evidence),
            ("health_evidence_json", health_evidence),
        ):
            if value is not None:
                if not isinstance(value, dict):
                    raise TypeError(f"{field} must be a JSON object")
                assignments.append(f"{field}=?")
                args.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        for field, value in (("commit_sha", commit_sha), ("note", note), ("status", status)):
            if value is not None:
                assignments.append(f"{field}=?")
                args.append(value)
        if status == "resolved":
            assignments.append("resolved_at=current_timestamp")
        args.append(cleaned_key)
        with self._connect() as db:
            current = db.execute(
                "select * from feedback_processing_items where feedback_key=?",
                (cleaned_key,),
            ).fetchone()
            if current is None:
                return None
            current_status = str(current["status"] or "pending")
            if status is not None and (
                current_status == "resolved" and status != "resolved"
                or current_status == "processing" and status == "pending"
            ):
                raise ValueError(
                    f"invalid feedback processing status transition: {current_status}->{status}"
                )
            changed = False
            for field, value in (
                ("test_evidence_json", test_evidence),
                ("restart_evidence_json", restart_evidence),
                ("health_evidence_json", health_evidence),
            ):
                if value is not None:
                    old = json.loads(current[field] or "{}")
                    if json.dumps(old, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")):
                        changed = True
            for field, value in (("commit_sha", commit_sha), ("note", note), ("status", status)):
                if value is not None and str(current[field] or "") != str(value):
                    changed = True
            if not changed:
                return self._feedback_processing_item_from_row(current)
            db.execute(
                f"update feedback_processing_items set {', '.join(assignments)} where feedback_key=?",
                args,
            )
            row = db.execute(
                "select * from feedback_processing_items where feedback_key=?",
                (cleaned_key,),
            ).fetchone()
        return self._feedback_processing_item_from_row(row) if row else None

    def resolve_feedback_processing_batch(
        self,
        batch_id: str,
        evidence: ResolutionEvidence | dict[str, object] | None = None,
        *,
        current_head: str | None = None,
    ) -> bool:
        """Resolve all items together after validating completion evidence.

        The no-evidence form preserves the original internal transition used
        by older callers (all item rows must already be ``resolved``). New
        callers provide a receipt and get one atomic processing->resolved
        transaction, including source-event projection updates.
        """
        cleaned_batch_id = batch_id.strip()
        if not cleaned_batch_id:
            return False
        normalized_evidence = (
            evidence
            if isinstance(evidence, ResolutionEvidence)
            else ResolutionEvidence.model_validate(evidence)
            if evidence is not None
            else None
        )
        if normalized_evidence is not None:
            if current_head is None:
                raise ValueError("current_head is required when resolving with evidence")
            validate_resolution_evidence(normalized_evidence, current_head=current_head)
        with self._immediate_write_transaction() as db:
            batch = db.execute(
                "select status, requested_count from feedback_processing_batches where batch_id=?",
                (cleaned_batch_id,),
            ).fetchone()
            if batch is None:
                return False
            row = db.execute(
                """
                select count(*) as total,
                       sum(case when status='resolved' then 1 else 0 end) as resolved
                from feedback_processing_items where batch_id=?
                """,
                (cleaned_batch_id,),
            ).fetchone()
            total = int(row["total"] or 0) if row else 0
            resolved = int(row["resolved"] or 0) if row else 0
            requested_count = int(batch["requested_count"] or 0)
            if normalized_evidence is not None:
                if str(batch["status"] or "") == "resolved":
                    return True
                if str(batch["status"] or "") != "processing":
                    raise ValueError("resolution requires a processing batch")
                if requested_count <= 0 or total != requested_count:
                    raise ValueError("resolution requires complete batch item associations")
                rows = db.execute(
                    "select * from feedback_processing_items where batch_id=? order by feedback_key",
                    (cleaned_batch_id,),
                ).fetchall()
                for row in rows:
                    if str(row["status"] or "") != "processing":
                        raise ValueError("resolution requires every item to be processing")
                    if not str(row["workbench_task_id"] or "").strip() or not str(row["workbench_turn_id"] or "").strip() or int(row["attempt_id"] or 0) <= 0 or int(row["agent_run_id"] or 0) <= 0:
                        raise ValueError("resolution requires complete item associations")
                    item_commit = str(row["commit_sha"] or "").strip()
                    if item_commit.lower() != normalized_evidence.commit_sha.strip().lower():
                        raise ValueError("resolution item commit does not match receipt")
                    test_json = json.loads(row["test_evidence_json"] or "{}")
                    restart_json = json.loads(row["restart_evidence_json"] or "{}")
                    health_json = json.loads(row["health_evidence_json"] or "{}")
                    validate_resolution_evidence(
                        ResolutionEvidence(
                            commit_sha=item_commit,
                            test_evidence=test_json,
                            restart_evidence=restart_json,
                            health_evidence=health_json,
                        ),
                        current_head=current_head,
                    )
                    db.execute(
                        """
                        update feedback_processing_items
                        set commit_sha=?, test_evidence_json=?, restart_evidence_json=?,
                            health_evidence_json=?, status='resolved',
                            resolved_at=current_timestamp, updated_at=current_timestamp
                        where feedback_key=? and batch_id=?
                        """,
                        (
                            item_commit,
                            json.dumps(test_json, ensure_ascii=False, sort_keys=True),
                            json.dumps(restart_json, ensure_ascii=False, sort_keys=True),
                            json.dumps(health_json, ensure_ascii=False, sort_keys=True),
                            row["feedback_key"],
                            cleaned_batch_id,
                        ),
                    )
                db.execute(
                    """
                    update feedback_events
                    set resolved_at=coalesce(nullif(resolved_at, ''), current_timestamp),
                        updated_at=current_timestamp
                    where key in (select feedback_key from feedback_processing_items where batch_id=?)
                    """,
                    (cleaned_batch_id,),
                )
                cursor = db.execute(
                    """
                    update feedback_processing_batches
                    set status='resolved', resolved_at=current_timestamp, updated_at=current_timestamp
                    where batch_id=? and status<>'resolved'
                    """,
                    (cleaned_batch_id,),
                )
                return cursor.rowcount == 1
            if requested_count <= 0 or total != requested_count or total != resolved:
                return False
            if str(batch["status"] or "") == "resolved":
                return True
            cursor = db.execute(
                """
                update feedback_processing_batches
                set status='resolved', resolved_at=current_timestamp,
                    updated_at=current_timestamp
                where batch_id=? and status<>'resolved'
                """,
                (cleaned_batch_id,),
            )
        return cursor.rowcount == 1

    def list_feedback_events_for_token(self, feedback_token: str) -> list[FeedbackEvent]:
        if not feedback_token.strip():
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from feedback_events
                where feedback_token=?
                order by received_at desc, updated_at desc
                """,
                (feedback_token,),
            ).fetchall()
            return [FeedbackEvent.model_validate(dict(row)) for row in rows]

    def get_feedback_event(self, key: str) -> FeedbackEvent | None:
        """Return one original feedback event by its stable key."""
        cleaned_key = key.strip()
        if not cleaned_key:
            return None
        with self._connect() as db:
            row = db.execute(
                "select * from feedback_events where key=?",
                (cleaned_key,),
            ).fetchone()
        return FeedbackEvent.model_validate(dict(row)) if row else None

    def list_feedback_events_for_tokens(
        self, feedback_tokens: list[str]
    ) -> dict[str, list[FeedbackEvent]]:
        tokens = sorted({token for token in feedback_tokens if token.strip()})
        if not tokens:
            return {}
        placeholders = ",".join(["?"] * len(tokens))
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from feedback_events
                where feedback_token in ({placeholders})
                order by received_at desc, updated_at desc
                """,
                tokens,
            ).fetchall()
            result: dict[str, list[FeedbackEvent]] = {}
            for row in rows:
                event = FeedbackEvent.model_validate(dict(row))
                result.setdefault(event.feedback_token, []).append(event)
            return result

    def create_service_bugfix_candidate(
        self,
        *,
        feedback_event_key: str,
        feedback_token: str = "",
        attempt_id: int = 0,
        title: str,
        reason: str,
        feedback_comment: str,
        conversation_title: str = "",
        trigger_text: str = "",
    ) -> ServiceBugfixCandidate | None:
        cleaned_key = feedback_event_key.strip()
        if not cleaned_key:
            return None
        with self._connect() as db:
            cursor = db.execute(
                """
                insert or ignore into service_bugfix_candidates (
                    feedback_event_key,
                    feedback_token,
                    attempt_id,
                    title,
                    reason,
                    feedback_comment,
                    conversation_title,
                    trigger_text
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned_key,
                    feedback_token,
                    max(0, int(attempt_id)),
                    title,
                    reason,
                    feedback_comment,
                    conversation_title,
                    trigger_text,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = db.execute(
                """
                select *
                from service_bugfix_candidates
                where feedback_event_key=?
                """,
                (cleaned_key,),
            ).fetchone()
            return ServiceBugfixCandidate.model_validate(dict(row)) if row else None

    def create_service_bugfix_candidate_for_feedback_event(
        self,
        event: FeedbackEvent,
        *,
        title: str,
        reason: str,
    ) -> ServiceBugfixCandidate | None:
        with self._connect() as db:
            row = db.execute(
                """
                select
                    coalesce(ra.id, 0) as attempt_id,
                    coalesce(ra.conversation_title, '') as conversation_title,
                    coalesce(ra.trigger_text, '') as trigger_text
                from feedback_events fe
                left join sent_replies sr
                    on sr.feedback_token = fe.feedback_token
                left join reply_attempts ra
                    on ra.conversation_id = sr.conversation_id
                   and ra.trigger_message_id = sr.trigger_message_id
                where fe.key=?
                order by ra.id desc
                limit 1
                """,
                (event.key,),
            ).fetchone()
        return self.create_service_bugfix_candidate(
            feedback_event_key=event.key,
            feedback_token=event.feedback_token,
            attempt_id=int(row["attempt_id"] or 0) if row else 0,
            title=title,
            reason=reason,
            feedback_comment=event.comment,
            conversation_title=str(row["conversation_title"] or "") if row else "",
            trigger_text=str(row["trigger_text"] or "") if row else "",
        )

    def list_service_bugfix_candidates(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[ServiceBugfixCandidate]:
        filters: list[str] = []
        args: list[object] = []
        if status is not None:
            filters.append("status=?")
            args.append(status)
        query = "select * from service_bugfix_candidates"
        if filters:
            query = f"{query} where {' and '.join(filters)}"
        query = f"{query} order by created_at desc, id desc limit ?"
        args.append(max(1, limit))
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
            return [ServiceBugfixCandidate.model_validate(dict(row)) for row in rows]

    def count_service_bugfix_candidates(self, *, status: str | None = None) -> int:
        filters: list[str] = []
        args: list[object] = []
        if status is not None:
            filters.append("status=?")
            args.append(status)
        query = "select count(*) as count from service_bugfix_candidates"
        if filters:
            query = f"{query} where {' and '.join(filters)}"
        with self._connect() as db:
            row = db.execute(query, args).fetchone()
            return int(row["count"] if row else 0)

    def list_user_feedback_items(
        self, limit: int = 200, offset: int = 0
    ) -> list[UserFeedbackItem]:
        with self._connect() as db:
            rows = db.execute(
                """
                with latest_attempt_by_token as (
                    select
                        sr.feedback_token as feedback_token,
                        max(ra.id) as attempt_id
                    from sent_replies sr
                    join reply_attempts ra
                        on ra.conversation_id = sr.conversation_id
                       and ra.trigger_message_id = sr.trigger_message_id
                    where trim(sr.feedback_token) <> ''
                    group by sr.feedback_token
                ), manual_attempt_by_key as (
                    select
                        fe.key as feedback_key,
                        max(ra.id) as attempt_id
                    from feedback_events fe
                    join reply_attempts ra
                        on ra.id = cast(substr(fe.key, 8) as integer)
                    where fe.key like 'manual:%'
                    group by fe.key
                )
                select
                    fe.key,
                    fe.feedback_token,
                    fe.rating,
                    fe.rating_label,
                    fe.comment,
                    fe.source,
                    fe.received_at,
                    coalesce(ra.id, 0) as attempt_id,
                    coalesce(ra.agent_run_id, 0) as agent_run_id,
                    coalesce(ra.codex_session_id, '') as codex_session_id,
                    coalesce(ar.role, '') as attempt_role,
                    coalesce(ra.conversation_title, '') as conversation_title,
                    coalesce(ra.trigger_sender, '') as trigger_sender,
                    coalesce(ra.trigger_text, '') as trigger_text,
                    coalesce(ra.final_reply_text, '') as final_reply_text,
                    coalesce(ra.draft_reply_text, '') as draft_reply_text,
                    coalesce(ra.codex_reason, '') as codex_reason,
                    coalesce(ra.audit_summary, '') as audit_summary,
                    case when latest.attempt_id is not null
                         then coalesce(ra.reviewer_feedback, '') else '' end as reviewer_feedback,
                    case when latest.attempt_id is not null
                         then coalesce(ra.corrected_reply_text, '') else '' end as corrected_reply_text,
                    coalesce(pi.status, case when trim(fe.resolved_at) <> '' then 'resolved' else 'pending' end) as processing_status,
                    fe.resolved_at,
                    fe.updated_at
                from feedback_events fe
                left join latest_attempt_by_token latest
                    on latest.feedback_token = fe.feedback_token
                left join manual_attempt_by_key manual
                    on manual.feedback_key = fe.key
                left join reply_attempts ra
                    on ra.id = coalesce(latest.attempt_id, manual.attempt_id)
                left join agent_runs ar
                    on ar.id = ra.agent_run_id
                left join feedback_processing_items pi
                    on pi.feedback_key = fe.key
                order by fe.received_at desc, fe.updated_at desc
                limit ?
                offset ?
                """,
                (limit, max(0, offset)),
            ).fetchall()
            return [UserFeedbackItem.model_validate(dict(row)) for row in rows]

    def count_user_feedback_items(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "select count(*) as count from feedback_events"
            ).fetchone()
            return int(row["count"])

    def list_feedback_import_items(
        self, limit: int = 200, offset: int = 0
    ) -> list[FeedbackImportItem]:
        """Project feedback rows into the deterministic startup payload."""
        return [
            FeedbackImportItem(
                feedback_key=item.key,
                summary=persisted_feedback_summary(item),
                references=detail_references(item),
            )
            for item in self.list_user_feedback_items(limit=limit, offset=offset)
        ]

    def count_pending_user_feedback_items(self) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                with latest_attempt_by_token as (
                    select
                        sr.feedback_token as feedback_token,
                        max(ra.id) as attempt_id
                    from sent_replies sr
                    join reply_attempts ra
                        on ra.conversation_id = sr.conversation_id
                       and ra.trigger_message_id = sr.trigger_message_id
                    where trim(sr.feedback_token) <> ''
                    group by sr.feedback_token
                )
                select count(*) as pending_count
                from feedback_events fe
                left join feedback_processing_items pi
                    on pi.feedback_key = fe.key
                left join latest_attempt_by_token latest
                    on latest.feedback_token = fe.feedback_token
                left join reply_attempts ra
                    on ra.id = latest.attempt_id
                where trim(fe.resolved_at) = ''
                  and coalesce(pi.status, 'pending') = 'pending'
                  and trim(coalesce(ra.reviewer_feedback, '')) = ''
                  and trim(coalesce(ra.corrected_reply_text, '')) = ''
                """
            ).fetchone()
            return int(row["pending_count"] if row else 0)

    def resolve_feedback_event(self, key: str) -> bool:
        cleaned_key = key.strip()
        if not cleaned_key:
            return False
        with self._connect() as db:
            cursor = db.execute(
                """
                update feedback_events
                set resolved_at=current_timestamp,
                    updated_at=current_timestamp
                where key=?
                """,
                (cleaned_key,),
            )
            if cursor.rowcount == 1:
                db.execute(
                    """
                    insert into feedback_processing_items (
                        feedback_key, status, resolved_at
                    ) values (?, 'resolved', current_timestamp)
                    on conflict(feedback_key) do update set
                        status='resolved',
                        resolved_at=current_timestamp,
                        updated_at=current_timestamp
                    """,
                    (cleaned_key,),
                )
            return cursor.rowcount == 1

    def update_sent_reply_recall(
        self,
        sent_reply_id: int,
        *,
        recall_status: str,
        recall_error: str,
    ) -> None:
        recalled_at_sql = (
            "current_timestamp" if recall_status == "recalled" else "recalled_at"
        )
        with self._connect() as db:
            db.execute(
                f"""
                update sent_replies
                set recall_status=?,
                    recall_error=?,
                    recalled_at={recalled_at_sql}
                where id=?
                """,
                (recall_status, recall_error, sent_reply_id),
            )

    def record_reply_attempt(
        self,
        *,
        conversation_id: str,
        conversation_title: str,
        trigger_message_id: str,
        trigger_sender: str,
        trigger_text: str,
        action: str,
        sensitivity_kind: str,
        codex_reason: str = "",
        draft_reply_text: str = "",
        direct_user_id: str = "",
        direct_open_dingtalk_id: str = "",
        codex_session_id: str = "",
        codex_transcript_start_line: int = 0,
        codex_transcript_end_line: int = 0,
        audit_documents_json: str = "[]",
        audit_tool_events_json: str = "[]",
        audit_summary: str = "",
        human_decision_options_json: str = "[]",
        oa_process_instance_id: str = "",
        oa_task_id: str = "",
        oa_url: str = "",
        oa_action: str = "",
        oa_remark: str = "",
        oa_action_result_json: str = "",
        calendar_event_id: str = "",
        calendar_response_status: str = "",
        calendar_response_result_json: str = "",
        mail_mailbox: str = "",
        mail_message_id: str = "",
        mail_subject: str = "",
        mail_reply_text: str = "",
        mail_action_result_json: str = "",
        send_status: str = "pending",
        channel: str = "dingtalk",
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into reply_attempts (
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    trigger_sender,
                    trigger_text,
                    action,
                    sensitivity_kind,
                    codex_reason,
                    draft_reply_text,
                    direct_user_id,
                    direct_open_dingtalk_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    audit_documents_json,
                    audit_tool_events_json,
                    audit_summary,
                    human_decision_options_json,
                    oa_process_instance_id,
                    oa_task_id,
                    oa_url,
                    oa_action,
                    oa_remark,
                    oa_action_result_json,
                    calendar_event_id,
                    calendar_response_status,
                    calendar_response_result_json,
                    mail_mailbox,
                    mail_message_id,
                    mail_subject,
                    mail_reply_text,
                    mail_action_result_json,
                    send_status,
                    channel
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    trigger_sender,
                    trigger_text,
                    action,
                    sensitivity_kind,
                    codex_reason,
                    draft_reply_text,
                    direct_user_id,
                    direct_open_dingtalk_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    audit_documents_json,
                    audit_tool_events_json,
                    audit_summary,
                    human_decision_options_json,
                    oa_process_instance_id,
                    oa_task_id,
                    oa_url,
                    oa_action,
                    oa_remark,
                    oa_action_result_json,
                    calendar_event_id,
                    calendar_response_status,
                    calendar_response_result_json,
                    mail_mailbox,
                    mail_message_id,
                    mail_subject,
                    mail_reply_text,
                    mail_action_result_json,
                    send_status,
                    channel,
                ),
            )
            attempt_id = int(cursor.lastrowid)
            self._record_memory_write_events_in_connection(
                db,
                attempt_id,
                audit_tool_events_json,
            )
            return attempt_id

    def record_reply_attempt_for_trigger(
        self,
        *,
        conversation_id: str,
        conversation_title: str,
        trigger_message_id: str,
        trigger_sender: str,
        trigger_text: str,
        action: str,
        sensitivity_kind: str,
        codex_reason: str = "",
        draft_reply_text: str = "",
        direct_user_id: str = "",
        direct_open_dingtalk_id: str = "",
        codex_session_id: str = "",
        codex_transcript_start_line: int = 0,
        codex_transcript_end_line: int = 0,
        audit_documents_json: str = "[]",
        audit_tool_events_json: str = "[]",
        audit_summary: str = "",
        human_decision_options_json: str = "[]",
        oa_process_instance_id: str = "",
        oa_task_id: str = "",
        oa_url: str = "",
        oa_action: str = "",
        oa_remark: str = "",
        oa_action_result_json: str = "",
        calendar_event_id: str = "",
        calendar_response_status: str = "",
        calendar_response_result_json: str = "",
        mail_mailbox: str = "",
        mail_message_id: str = "",
        mail_subject: str = "",
        mail_reply_text: str = "",
        mail_action_result_json: str = "",
        send_status: str = "pending",
    ) -> int:
        existing_attempt = self.get_latest_reply_attempt_for_trigger(
            conversation_id, trigger_message_id
        )
        if (
            existing_attempt is None
            or self.has_sent_reply_for_trigger(conversation_id, trigger_message_id)
        ):
            return self.record_reply_attempt(
                conversation_id=conversation_id,
                conversation_title=conversation_title,
                trigger_message_id=trigger_message_id,
                trigger_sender=trigger_sender,
                trigger_text=trigger_text,
                action=action,
                sensitivity_kind=sensitivity_kind,
                codex_reason=codex_reason,
                draft_reply_text=draft_reply_text,
                direct_user_id=direct_user_id,
                direct_open_dingtalk_id=direct_open_dingtalk_id,
                codex_session_id=codex_session_id,
                codex_transcript_start_line=codex_transcript_start_line,
                codex_transcript_end_line=codex_transcript_end_line,
                audit_documents_json=audit_documents_json,
                audit_tool_events_json=audit_tool_events_json,
                audit_summary=audit_summary,
                human_decision_options_json=human_decision_options_json,
                oa_process_instance_id=oa_process_instance_id,
                oa_task_id=oa_task_id,
                oa_url=oa_url,
                oa_action=oa_action,
                oa_remark=oa_remark,
                oa_action_result_json=oa_action_result_json,
                calendar_event_id=calendar_event_id,
                calendar_response_status=calendar_response_status,
                calendar_response_result_json=calendar_response_result_json,
                mail_mailbox=mail_mailbox,
                mail_message_id=mail_message_id,
                mail_subject=mail_subject,
                mail_reply_text=mail_reply_text,
                mail_action_result_json=mail_action_result_json,
                send_status=send_status,
            )
        with self._connect() as db:
            db.execute(
                """
                update reply_attempts
                set conversation_id=?,
                    conversation_title=?,
                    trigger_message_id=?,
                    trigger_sender=?,
                    trigger_text=?,
                    action=?,
                    sensitivity_kind=?,
                    codex_reason=?,
                    draft_reply_text=?,
                    direct_user_id=?,
                    direct_open_dingtalk_id=?,
                    codex_session_id=?,
                    codex_transcript_start_line=?,
                    codex_transcript_end_line=?,
                    audit_documents_json=?,
                    audit_tool_events_json=?,
                    audit_summary=?,
                    human_decision_options_json=?,
                    oa_process_instance_id=?,
                    oa_task_id=?,
                    oa_url=?,
                    oa_action=?,
                    oa_remark=?,
                    oa_action_result_json=?,
                    calendar_event_id=?,
                    calendar_response_status=?,
                    calendar_response_result_json=?,
                    mail_mailbox=?,
                    mail_message_id=?,
                    mail_subject=?,
                    mail_reply_text=?,
                    mail_action_result_json=?,
                    final_reply_text='',
                    permission_action='',
                    permission_reason='',
                    send_status=?,
                    send_error='',
                    retry_count=0,
                    updated_at=current_timestamp
                where id=?
                """,
                (
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    trigger_sender,
                    trigger_text,
                    action,
                    sensitivity_kind,
                    codex_reason,
                    draft_reply_text,
                    direct_user_id,
                    direct_open_dingtalk_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    audit_documents_json,
                    audit_tool_events_json,
                    audit_summary,
                    human_decision_options_json,
                    oa_process_instance_id,
                    oa_task_id,
                    oa_url,
                    oa_action,
                    oa_remark,
                    oa_action_result_json,
                    calendar_event_id,
                    calendar_response_status,
                    calendar_response_result_json,
                    mail_mailbox,
                    mail_message_id,
                    mail_subject,
                    mail_reply_text,
                    mail_action_result_json,
                    send_status,
                    existing_attempt.id,
                ),
            )
            self._record_memory_write_events_in_connection(
                db,
                existing_attempt.id,
                audit_tool_events_json,
            )
        return existing_attempt.id

    def update_reply_attempt(
        self,
        attempt_id: int,
        *,
        action: str | None = None,
        final_reply_text: str | None = None,
        permission_action: str | None = None,
        permission_reason: str | None = None,
        direct_user_id: str | None = None,
        direct_open_dingtalk_id: str | None = None,
        oa_process_instance_id: str | None = None,
        oa_task_id: str | None = None,
        oa_url: str | None = None,
        oa_action: str | None = None,
        oa_remark: str | None = None,
        oa_action_result_json: str | None = None,
        calendar_event_id: str | None = None,
        calendar_response_status: str | None = None,
        calendar_response_result_json: str | None = None,
        mail_mailbox: str | None = None,
        mail_message_id: str | None = None,
        mail_subject: str | None = None,
        mail_reply_text: str | None = None,
        mail_action_result_json: str | None = None,
        reaction_action_result_json: str | None = None,
        document_action_result_json: str | None = None,
        audit_tool_events_json: str | None = None,
        audit_summary: str | None = None,
        human_decision_options_json: str | None = None,
        send_status: str | None = None,
        send_error: str | None = None,
        retry_count: int | None = None,
        feedback_scope: str | None = None,
        skill_update_requested: bool | None = None,
        skill_update_receipts_json: str | None = None,
    ) -> None:
        updates = self._reply_attempt_update_values(
            action=action,
            final_reply_text=final_reply_text,
            permission_action=permission_action,
            permission_reason=permission_reason,
            direct_user_id=direct_user_id,
            direct_open_dingtalk_id=direct_open_dingtalk_id,
            oa_process_instance_id=oa_process_instance_id,
            oa_task_id=oa_task_id,
            oa_url=oa_url,
            oa_action=oa_action,
            oa_remark=oa_remark,
            oa_action_result_json=oa_action_result_json,
            calendar_event_id=calendar_event_id,
            calendar_response_status=calendar_response_status,
            calendar_response_result_json=calendar_response_result_json,
            mail_mailbox=mail_mailbox,
            mail_message_id=mail_message_id,
            mail_subject=mail_subject,
            mail_reply_text=mail_reply_text,
            mail_action_result_json=mail_action_result_json,
            reaction_action_result_json=reaction_action_result_json,
            document_action_result_json=document_action_result_json,
            audit_tool_events_json=audit_tool_events_json,
            audit_summary=audit_summary,
            human_decision_options_json=human_decision_options_json,
            send_status=send_status,
            send_error=send_error,
            retry_count=retry_count,
            feedback_scope=feedback_scope,
            skill_update_requested=(
                int(skill_update_requested)
                if skill_update_requested is not None
                else None
            ),
            skill_update_receipts_json=skill_update_receipts_json,
        )
        if not updates:
            return
        with self._connect() as db:
            self._update_reply_attempt_in_connection(db, attempt_id, updates)
            if audit_tool_events_json is not None:
                self._record_memory_write_events_in_connection(
                    db,
                    attempt_id,
                    audit_tool_events_json,
                )

    def finalize_orchestrated_reply_task(
        self,
        *,
        task_id: int,
        expected_execution_generation: str,
        run_id: int,
        task_status: str,
        task_error: str,
        available_at: str,
        conversation_id: str,
        conversation_title: str,
        trigger_message_id: str,
        trigger_sender: str,
        trigger_text: str,
        codex_reason: str,
        codex_session_id: str,
        codex_transcript_start_line: int,
        codex_transcript_end_line: int,
        audit_tool_events_json: str,
        audit_summary: str,
        human_decision_options_json: str = "[]",
        send_status: str,
        send_error: str,
        channel: str,
        preserve_attempt_budget: bool = False,
        oa_process_instance_id: str = "",
        oa_task_id: str = "",
        oa_url: str = "",
        oa_action: str = "",
        oa_remark: str = "",
        oa_action_result_json: str = "",
        sent_reply_text: str = "",
        sent_reply_result_json: str = "",
    ) -> int:
        """Persist one orchestration result and its task transition atomically."""
        if task_status not in {"done", "failed", "pending", "unchanged"}:
            raise ValueError("invalid reply task terminal status")
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        if sent_reply_text and (task_status != "done" or send_status != "completed"):
            raise ValueError("sent reply ledger requires completed task delivery")
        feedback_context = (
            extract_configured_feedback_link_context(
                sent_reply_text,
                vercel_base_url=feedback_spike_vercel_base_url(),
            )
            if sent_reply_text
            else None
        )
        sent_reply_feedback_token = (
            feedback_context.feedback_token if feedback_context is not None else ""
        )
        with self._immediate_write_transaction() as db:
            row = db.execute(
                """
                select agent_runs.status as run_status,
                       agent_runs.execution_generation as run_generation,
                       reply_tasks.execution_generation as task_generation,
                       reply_tasks.oa_url as task_oa_url
                from agent_runs
                join reply_tasks on reply_tasks.id=agent_runs.reply_task_id
                where agent_runs.id=? and reply_tasks.id=?
                """,
                (run_id, task_id),
            ).fetchone()
            if (
                row is None
                or row["run_generation"] != expected_execution_generation
                or row["task_generation"] != expected_execution_generation
                or row["run_status"] not in {"completed", "failed", "unknown"}
            ):
                raise AgentRunLeaseLostError(f"agent run superseded: {run_id}")
            persisted_oa_url = oa_url.strip() or str(row["task_oa_url"] or "").strip()
            task_process_id, task_oa_task_id = self._oa_identifiers_from_url(
                persisted_oa_url
            )
            persisted_process_id = oa_process_instance_id.strip() or task_process_id
            persisted_task_id = oa_task_id.strip() or task_oa_task_id
            persisted_oa_action = oa_action.strip() or (
                "review" if persisted_process_id else ""
            )
            current_attempt = db.execute(
                """
                select attempts.id
                from reply_attempts as attempts
                left join agent_runs as runs on runs.id=attempts.agent_run_id
                where attempts.channel=?
                  and attempts.conversation_id=?
                  and attempts.trigger_message_id=?
                  and (
                      runs.reply_task_id=?
                      or attempts.id=(
                          select manual_rerun_attempt_id
                          from reply_tasks where id=?
                      )
                      or attempts.id=(
                          select max(latest.id)
                          from reply_attempts as latest
                          where latest.channel=attempts.channel
                            and latest.conversation_id=attempts.conversation_id
                            and latest.trigger_message_id=attempts.trigger_message_id
                      )
                  )
                order by attempts.id desc
                limit 1
                """,
                (
                    channel,
                    conversation_id,
                    trigger_message_id,
                    task_id,
                    task_id,
                ),
            ).fetchone()
            projection_values = (
                run_id,
                codex_reason,
                codex_session_id,
                codex_transcript_start_line,
                codex_transcript_end_line,
                audit_tool_events_json,
                audit_summary,
                human_decision_options_json,
                persisted_process_id,
                persisted_task_id,
                persisted_oa_url,
                persisted_oa_action,
                oa_remark,
                oa_action_result_json,
                send_status,
                send_error,
            )
            if current_attempt is None:
                cursor = db.execute(
                    """
                    insert into reply_attempts (
                        conversation_id, conversation_title, trigger_message_id,
                        trigger_sender, trigger_text, action, sensitivity_kind,
                        agent_run_id, codex_reason, codex_session_id,
                        codex_transcript_start_line, codex_transcript_end_line,
                        audit_tool_events_json, audit_summary,
                        human_decision_options_json,
                        oa_process_instance_id, oa_task_id, oa_url, oa_action,
                        oa_remark, oa_action_result_json, send_status, send_error,
                        channel
                    ) values (?, ?, ?, ?, ?, 'agent_run', 'general', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        conversation_title,
                        trigger_message_id,
                        trigger_sender,
                        trigger_text,
                        *projection_values,
                        channel,
                    ),
                )
                attempt_id = int(cursor.lastrowid)
            else:
                attempt_id = int(current_attempt["id"])
                db.execute(
                    """
                    update reply_attempts
                    set conversation_id=?, conversation_title=?,
                        trigger_message_id=?, trigger_sender=?, trigger_text=?,
                        action='agent_run', sensitivity_kind='general',
                        agent_run_id=?, codex_reason=?, codex_session_id=?,
                        codex_transcript_start_line=?, codex_transcript_end_line=?,
                        audit_tool_events_json=?, audit_summary=?,
                        human_decision_options_json=?, oa_process_instance_id=?,
                        oa_task_id=?, oa_url=?, oa_action=?, oa_remark=?,
                        oa_action_result_json=?, send_status=?, send_error=?,
                        final_reply_text='', permission_action='',
                        permission_reason='', retry_count=0,
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (
                        conversation_id,
                        conversation_title,
                        trigger_message_id,
                        trigger_sender,
                        trigger_text,
                        *projection_values,
                        attempt_id,
                    ),
                )
            self._record_memory_write_events_in_connection(
                db,
                attempt_id,
                audit_tool_events_json,
            )
            if task_status != "unchanged":
                cursor = db.execute(
                    """
                    update reply_tasks
                    set status=?, attempts=case when ? then max(attempts - 1, 0) else attempts end,
                        locked_at=null, available_at=?, error=?,
                        updated_at=current_timestamp
                    where id=? and execution_generation=?
                      and status in ('processing', 'pending')
                    """,
                    (
                        task_status,
                        int(preserve_attempt_budget),
                        available_at if task_status == "pending" else "",
                        task_error if task_status != "done" else "",
                        task_id,
                        expected_execution_generation,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")
            if sent_reply_text:
                db.execute(
                    """
                    insert into sent_replies (
                        conversation_id, trigger_message_id, reply_text, send_result_json,
                        feedback_token
                    )
                    select ?, ?, ?, ?, ?
                    where not exists (
                        select 1 from sent_replies
                        where conversation_id=? and trigger_message_id=?
                    )
                    """,
                    (
                        conversation_id,
                        trigger_message_id,
                        sent_reply_text,
                        sent_reply_result_json,
                        sent_reply_feedback_token,
                        conversation_id,
                        trigger_message_id,
                    ),
                )
            return attempt_id

    def finalize_reply_task_without_run(
        self,
        *,
        task_id: int,
        expected_execution_generation: str,
        task_status: str,
        task_error: str,
        available_at: str,
        conversation_id: str,
        conversation_title: str,
        trigger_message_id: str,
        trigger_sender: str,
        trigger_text: str,
        codex_reason: str,
        audit_summary: str,
        send_status: str,
        send_error: str,
        channel: str,
    ) -> int:
        """Persist a pre-run failure and its generation-bound task transition."""
        if task_status not in {"failed", "pending"}:
            raise ValueError("invalid pre-run reply task status")
        if not expected_execution_generation.strip():
            raise ValueError("expected_execution_generation must be non-empty")
        with self._immediate_write_transaction() as db:
            task = db.execute(
                """
                select execution_generation, status, oa_url
                from reply_tasks
                where id=?
                """,
                (task_id,),
            ).fetchone()
            if (
                task is None
                or task["status"] != "processing"
                or task["execution_generation"] != expected_execution_generation
            ):
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")
            persisted_oa_url = str(task["oa_url"] or "").strip()
            process_instance_id, oa_task_id = self._oa_identifiers_from_url(
                persisted_oa_url
            )
            current_attempt = db.execute(
                """
                select id
                from reply_attempts
                where channel=? and conversation_id=? and trigger_message_id=?
                order by id desc
                limit 1
                """,
                (channel, conversation_id, trigger_message_id),
            ).fetchone()
            projection_values = (
                conversation_id,
                conversation_title,
                trigger_message_id,
                trigger_sender,
                trigger_text,
                codex_reason,
                audit_summary,
                process_instance_id,
                oa_task_id,
                persisted_oa_url,
                "review" if process_instance_id else "",
                send_status,
                send_error,
            )
            if current_attempt is None:
                cursor = db.execute(
                    """
                    insert into reply_attempts (
                        conversation_id, conversation_title, trigger_message_id,
                        trigger_sender, trigger_text, action, sensitivity_kind,
                        codex_reason, audit_summary, oa_process_instance_id,
                        oa_task_id, oa_url, oa_action, send_status, send_error, channel
                    ) values (?, ?, ?, ?, ?, 'agent_run', 'general', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*projection_values, channel),
                )
                attempt_id = int(cursor.lastrowid)
            else:
                attempt_id = int(current_attempt["id"])
                db.execute(
                    """
                    update reply_attempts
                    set conversation_id=?, conversation_title=?,
                        trigger_message_id=?, trigger_sender=?, trigger_text=?,
                        action='agent_run', sensitivity_kind='general',
                        codex_reason=?, audit_summary=?,
                        oa_process_instance_id=?, oa_task_id=?, oa_url=?,
                        oa_action=?, send_status=?, send_error=?,
                        final_reply_text='', permission_action='',
                        permission_reason='', retry_count=0,
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (*projection_values, attempt_id),
                )
            task_cursor = db.execute(
                """
                update reply_tasks
                set status=?, locked_at=null, available_at=?, error=?,
                    updated_at=current_timestamp
                where id=? and status='processing' and execution_generation=?
                """,
                (
                    task_status,
                    available_at if task_status == "pending" else "",
                    task_error,
                    task_id,
                    expected_execution_generation,
                ),
            )
            if task_cursor.rowcount != 1:
                raise AgentRunLeaseLostError(f"reply task superseded: {task_id}")
            return attempt_id

    def reply_task_is_done(self, task_id: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                "select status from reply_tasks where id=?",
                (task_id,),
            ).fetchone()
        return bool(row and row["status"] == "done")

    def list_memory_write_events_for_attempt(
        self,
        attempt_id: int,
    ) -> list[MemoryWriteEvent]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from memory_write_events
                where attempt_id=?
                order by id
                """,
                (attempt_id,),
            ).fetchall()
        return [MemoryWriteEvent.model_validate(dict(row)) for row in rows]

    @staticmethod
    def _record_memory_write_events_in_connection(
        db: sqlite3.Connection,
        attempt_id: int,
        audit_tool_events_json: str,
    ) -> None:
        try:
            audit_events = json.loads(audit_tool_events_json or "[]")
        except json.JSONDecodeError:
            audit_events = []
        if not isinstance(audit_events, list):
            audit_events = []
        tool_outputs_by_call_id = {
            str(event.get("call_id") or ""): str(event.get("output") or "")
            for event in audit_events
            if isinstance(event, dict)
            and str(event.get("tool") or "") == "tool_output"
            and str(event.get("call_id") or "")
            and str(event.get("output") or "")
        }
        memory_events = [
            AutoReplyStore._memory_write_event_from_audit_event(
                event,
                tool_outputs_by_call_id=tool_outputs_by_call_id,
            )
            for event in audit_events
            if isinstance(event, dict)
        ]
        memory_events = [event for event in memory_events if event is not None]
        db.execute("delete from memory_write_events where attempt_id=?", (attempt_id,))
        event_type_counts: dict[str, int] = {}
        for event in memory_events:
            base_event_type = event["event_type"]
            count = event_type_counts.get(base_event_type, 0) + 1
            event_type_counts[base_event_type] = count
            event_type = base_event_type if count == 1 else f"{base_event_type}_{count}"
            db.execute(
                """
                insert into memory_write_events (
                    attempt_id,
                    event_type,
                    payload_json,
                    status,
                    attempts,
                    last_error,
                    memory_episode_id
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    event_type,
                    event["payload_json"],
                    event["status"],
                    1,
                    event["last_error"],
                    event["memory_episode_id"],
                ),
            )

    @staticmethod
    def _memory_write_event_from_audit_event(
        event: dict[str, object],
        *,
        tool_outputs_by_call_id: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        tool = str(event.get("tool") or "")
        if not AutoReplyStore._is_memory_write_tool_name(tool):
            return None
        output = str(event.get("output") or "")
        call_id = str(event.get("call_id") or "")
        if not output and call_id and tool_outputs_by_call_id:
            output = tool_outputs_by_call_id.get(call_id, "")
        parsed_output = AutoReplyStore._parse_memory_write_output(
            output
        )
        status = parsed_output.get("status") or "pending"
        payload = {
            "tool": tool,
            "call_id": call_id,
            "input": str(event.get("input") or ""),
            "output": output,
        }
        return {
            "event_type": "memory_write",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "status": status,
            "last_error": parsed_output.get("last_error") or "",
            "memory_episode_id": parsed_output.get("memory_episode_id") or "",
        }

    @staticmethod
    def _is_memory_write_tool_name(tool: str) -> bool:
        normalized = tool.strip()
        return normalized == "memory_write" or normalized.endswith(
            (".memory_write", "__memory_write", " memory_write")
        )

    @staticmethod
    def _parse_memory_write_output(output: str) -> dict[str, str]:
        if not output.strip():
            return {}
        payload = AutoReplyStore._load_memory_json(output)
        if not isinstance(payload, dict):
            return {}
        result = payload.get("structured_content")
        if isinstance(result, dict):
            nested = AutoReplyStore._load_memory_json(str(result.get("result") or ""))
            if isinstance(nested, dict):
                payload = nested
        elif isinstance(payload.get("result"), str):
            nested = AutoReplyStore._load_memory_json(str(payload.get("result") or ""))
            if isinstance(nested, dict):
                payload = nested
        elif isinstance(payload.get("content"), list):
            for item in payload["content"]:
                if not isinstance(item, dict):
                    continue
                nested = AutoReplyStore._load_memory_json(str(item.get("text") or ""))
                if isinstance(nested, dict):
                    payload = nested
                    break
        processing_status = str(payload.get("processing_status") or "").casefold()
        ok = payload.get("ok") is True
        if processing_status == "failed" or payload.get("ok") is False:
            status = "failed"
        else:
            status = "pending"
        memory_episode_id = str(
            payload.get("episode_uuid")
            or payload.get("uuid")
            or payload.get("memory_episode_id")
            or payload.get("duplicate_of_episode_uuid")
            or ""
        )
        if memory_episode_id and (
            ok
            or payload.get("failure_kind") == "duplicate_memory_write"
            or processing_status in {"completed", "success", "done", "ready"}
        ):
            status = "written"
        last_error = str(payload.get("last_error") or payload.get("error") or "")
        processing_statuses = payload.get("processing_statuses")
        if not last_error and isinstance(processing_statuses, list):
            for item in processing_statuses:
                if not isinstance(item, dict):
                    continue
                last_error = str(item.get("last_error") or item.get("error") or "")
                if last_error:
                    break
        return {
            "status": status,
            "memory_episode_id": memory_episode_id,
            "last_error": last_error,
        }

    @staticmethod
    def _load_memory_json(raw: str) -> object | None:
        text = raw.strip()
        if not text:
            return None
        if "\nOutput:\n" in text:
            text = text.rsplit("\nOutput:\n", 1)[1].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _reply_attempt_update_values(**updates: object) -> dict[str, object]:
        allowed_columns = {
            "action",
            "final_reply_text",
            "permission_action",
            "permission_reason",
            "direct_user_id",
            "direct_open_dingtalk_id",
            "oa_process_instance_id",
            "oa_task_id",
            "oa_url",
            "oa_action",
            "oa_remark",
            "oa_action_result_json",
            "calendar_event_id",
            "calendar_response_status",
            "calendar_response_result_json",
            "mail_mailbox",
            "mail_message_id",
            "mail_subject",
            "mail_reply_text",
            "mail_action_result_json",
            "reaction_action_result_json",
            "document_action_result_json",
            "audit_tool_events_json",
            "audit_summary",
            "human_decision_options_json",
            "send_status",
            "send_error",
            "retry_count",
            "feedback_scope",
            "skill_update_requested",
            "skill_update_receipts_json",
        }
        unknown = set(updates) - allowed_columns
        if unknown:
            raise ValueError(
                "unknown reply_attempt update column: "
                + ", ".join(sorted(unknown))
            )
        return {column: value for column, value in updates.items() if value is not None}

    @staticmethod
    def _update_reply_attempt_in_connection(
        db: sqlite3.Connection,
        attempt_id: int,
        updates: dict[str, object],
    ) -> None:
        assignments = [f"{column}=?" for column in updates]
        values = list(updates.values())
        assignments.append("updated_at=current_timestamp")
        values.append(attempt_id)
        db.execute(
            f"update reply_attempts set {', '.join(assignments)} where id=?",
            values,
        )

    def record_reply_feedback(
        self,
        attempt_id: int,
        *,
        feedback: str,
        corrected_reply_text: str = "",
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_attempts
                set reviewer_feedback=?,
                    corrected_reply_text=?,
                    reviewed_at=current_timestamp,
                    updated_at=current_timestamp
                where id=?
                """,
                (feedback, corrected_reply_text, attempt_id),
            )
            return cursor.rowcount == 1

    def resolve_needs_human_attempt(
        self,
        attempt_id: int,
        *,
        reviewer_feedback: str,
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_attempts
                set send_status='decision_selected',
                    send_error='',
                    reviewer_feedback=?,
                    reviewed_at=current_timestamp,
                    updated_at=current_timestamp
                where id=? and send_status='needs_human'
                """,
                (reviewer_feedback, attempt_id),
            )
            return cursor.rowcount == 1

    def record_reviewed_reply_rerun(
        self,
        *,
        conversation_id: str,
        conversation_title: str,
        single_chat: bool,
        trigger_message_id: str,
        trigger_create_time: str,
        trigger_sender: str,
        trigger_text: str,
        trigger_message_json: str,
        suggested_reply_text: str,
        reviewer_feedback: str = "",
        channel: str = "dingtalk",
        oa_url: str = "",
        source_attempt_id: int = 0,
    ) -> tuple[int, ReplyTask]:
        """Atomically persist one reviewed instruction and queue its generation."""
        feedback = reviewer_feedback.strip()
        suggestion = suggested_reply_text.strip()
        task: ReplyTask | None = None
        attempt_id = 0
        with self._immediate_write_transaction() as db:
            source_row = None
            if source_attempt_id > 0:
                source_row = db.execute(
                    "select * from reply_attempts where id=?",
                    (source_attempt_id,),
                ).fetchone()
                if source_row is None:
                    raise ValueError("actionable attempt does not exist")
                source_status = str(source_row["send_status"] or "")
                if source_status == "decision_selected":
                    if str(source_row["reviewer_feedback"] or "") != feedback:
                        raise ValueError("attempt decision was already selected")
                elif source_status not in {"failed", "needs_human", "pending"}:
                    raise ValueError("attempt no longer requires a decision")
                # A reviewed decision rewrites the source attempt in place.
                # The execution generation changes on the task, but the
                # business attempt id and Codex conversation session remain
                # stable; do not create a second reply_attempt row.
                audit_summary = (
                    "Reviewer feedback: "
                    + feedback
                    + "\nSuggested response: "
                    + suggestion
                ).strip()
                # The attempt row is the current projection, but its audit
                # events are historical evidence.  Keep the prior events and
                # append the review enqueue marker instead of replacing the
                # evidence captured by the original run.
                try:
                    prior_events = json.loads(
                        str(source_row["audit_tool_events_json"] or "[]")
                    )
                except (TypeError, json.JSONDecodeError):
                    prior_events = []
                if not isinstance(prior_events, list):
                    prior_events = []
                queued_events = [*prior_events, {"tool": "audit_review", "result": "queued"}]
                db.execute(
                    """
                    update reply_attempts
                    set action='send_reply',
                        codex_reason='reviewed_message_reply',
                        draft_reply_text=?,
                        audit_tool_events_json=?,
                        audit_summary=?,
                        reviewer_feedback=?,
                        corrected_reply_text=?,
                        reviewed_at=current_timestamp,
                        send_status='pending',
                        send_error='',
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (
                        suggestion,
                        json.dumps(
                            queued_events,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        audit_summary,
                        feedback,
                        suggestion,
                        source_attempt_id,
                    ),
                )
                revision_key = self._manual_rerun_revision_key(
                    db, source_attempt_id
                )
                task = self._enqueue_manual_rerun_reply_task_in_connection(
                    db,
                    conversation_id=conversation_id,
                    conversation_title=conversation_title,
                    single_chat=single_chat,
                    trigger_message_id=trigger_message_id,
                    trigger_create_time=trigger_create_time,
                    trigger_sender=trigger_sender,
                    trigger_text=trigger_text,
                    trigger_message_json=trigger_message_json,
                    oa_url=oa_url,
                    attempt_id=source_attempt_id,
                    revision_key=revision_key,
                    channel=channel,
                )
                return source_attempt_id, task
            existing = db.execute(
                """
                select attempts.id as attempt_id, tasks.*
                from reply_tasks as tasks
                join reply_attempts as attempts
                  on attempts.id=tasks.manual_rerun_attempt_id
                where tasks.channel=?
                  and tasks.conversation_id=?
                  and tasks.trigger_message_id=?
                  and tasks.status in ('pending', 'processing')
                  and attempts.codex_reason='reviewed_message_reply'
                  and attempts.reviewer_feedback=?
                  and attempts.corrected_reply_text=?
                limit 1
                """,
                (
                    channel,
                    conversation_id,
                    trigger_message_id,
                    feedback,
                    suggestion,
                ),
            ).fetchone()
            if existing is not None:
                if source_attempt_id > 0:
                    self._resolve_actionable_attempt_in_connection(
                        db,
                        source_attempt_id,
                        reviewer_feedback=feedback,
                    )
                return int(existing["attempt_id"]), self._reply_task_from_row(existing)

            current_task = db.execute(
                """
                select * from reply_tasks
                where channel=? and conversation_id=? and trigger_message_id=?
                """,
                (channel, conversation_id, trigger_message_id),
            ).fetchone()
            if current_task is not None:
                task = self._reply_task_from_row(current_task)

            audit_summary = (
                "Reviewer feedback: "
                + feedback
                + "\nSuggested response: "
                + suggestion
            ).strip()
            cursor = db.execute(
                """
                insert into reply_attempts (
                    conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    codex_reason, draft_reply_text, audit_tool_events_json,
                    audit_summary, reviewer_feedback, corrected_reply_text,
                    reviewed_at, send_status, channel
                ) values (?, ?, ?, ?, ?, 'send_reply', 'general',
                          'reviewed_message_reply', ?, ?, ?, ?, ?,
                          current_timestamp, 'pending', ?)
                """,
                (
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    trigger_sender,
                    trigger_text,
                    suggestion,
                    json.dumps(
                        [{"tool": "audit_review", "result": "queued"}],
                        ensure_ascii=False,
                    ),
                    audit_summary,
                    feedback,
                    suggestion,
                    channel,
                ),
            )
            attempt_id = int(cursor.lastrowid)
            revision_key = self._manual_rerun_revision_key(db, attempt_id)
            task = self._enqueue_manual_rerun_reply_task_in_connection(
                db,
                conversation_id=conversation_id,
                conversation_title=conversation_title,
                single_chat=single_chat,
                trigger_message_id=trigger_message_id,
                trigger_create_time=trigger_create_time,
                trigger_sender=trigger_sender,
                trigger_text=trigger_text,
                trigger_message_json=trigger_message_json,
                oa_url=oa_url,
                attempt_id=attempt_id,
                revision_key=revision_key,
                channel=channel,
            )
            if source_attempt_id > 0:
                self._resolve_actionable_attempt_in_connection(
                    db,
                    source_attempt_id,
                    reviewer_feedback=feedback,
                )
        return attempt_id, task

    @staticmethod
    def _resolve_actionable_attempt_in_connection(
        db: sqlite3.Connection,
        attempt_id: int,
        *,
        reviewer_feedback: str,
    ) -> None:
        cursor = db.execute(
            """
            update reply_attempts
            set send_status='decision_selected',
                send_error='',
                reviewer_feedback=?,
                reviewed_at=current_timestamp,
                updated_at=current_timestamp
            where id=? and send_status in ('failed', 'needs_human')
            """,
            (reviewer_feedback, attempt_id),
        )
        if cursor.rowcount == 1:
            return
        row = db.execute(
            "select send_status, reviewer_feedback from reply_attempts where id=?",
            (attempt_id,),
        ).fetchone()
        if (
            row is not None
            and row["send_status"] == "decision_selected"
            and str(row["reviewer_feedback"] or "") == reviewer_feedback
        ):
            return
        raise ValueError("attempt no longer requires a decision")

    def record_actionable_attempt_decision(
        self,
        source_attempt_id: int,
        *,
        reviewer_feedback: str,
        conversation_title: str,
        single_chat: bool,
        trigger_create_time: str,
        trigger_message_json: str,
    ) -> tuple[int, ReplyTask]:
        feedback = reviewer_feedback.strip()
        if not feedback:
            raise ValueError("reviewer feedback must be non-empty")
        source = self.get_reply_attempt(source_attempt_id)
        if source is None:
            raise ValueError("actionable attempt does not exist")
        return self.record_reviewed_reply_rerun(
            conversation_id=source.conversation_id,
            conversation_title=conversation_title,
            single_chat=single_chat,
            trigger_message_id=source.trigger_message_id,
            trigger_create_time=trigger_create_time,
            trigger_sender=source.trigger_sender,
            trigger_text=source.trigger_text,
            trigger_message_json=trigger_message_json,
            suggested_reply_text="",
            reviewer_feedback=feedback,
            channel=source.channel or "dingtalk",
            oa_url=source.oa_url,
            source_attempt_id=source_attempt_id,
        )

    def get_reply_attempt(self, attempt_id: int) -> ReplyAttempt | None:
        with self._connect() as db:
            row = db.execute(
                "select * from reply_attempts where id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            return ReplyAttempt.model_validate(dict(row))

    def get_latest_reply_attempt_for_trigger(
        self, conversation_id: str, trigger_message_id: str
    ) -> ReplyAttempt | None:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from reply_attempts
                where conversation_id=? and trigger_message_id=?
                order by id desc
                limit 1
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            if row is None:
                return None
            return ReplyAttempt.model_validate(dict(row))

    def list_current_unresolved_problem_attempts(
        self, *, limit: int = 50
    ) -> list[ReplyAttempt]:
        """Return the latest unresolved problem state for each message trigger."""
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_attempts as attempts
                where attempts.send_status in ('needs_human', 'blocked', 'failed')
                  and (
                      (
                          attempts.send_status = 'needs_human'
                          and attempts.reviewed_at is null
                          and not exists (
                              select 1
                              from reply_tasks as tasks
                              where tasks.channel=attempts.channel
                                and tasks.conversation_id=attempts.conversation_id
                                and tasks.trigger_message_id=attempts.trigger_message_id
                                and tasks.status in ('done', 'pending', 'processing')
                          )
                      )
                      or (
                          attempts.send_status != 'needs_human'
                          and not exists (
                              select 1
                              from reply_tasks as tasks
                              where tasks.channel=attempts.channel
                                and tasks.conversation_id=attempts.conversation_id
                                and tasks.trigger_message_id=attempts.trigger_message_id
                                and (
                                    tasks.status in ('done', 'pending', 'processing')
                                )
                          )
                      )
                  )
                  and attempts.id=(
                      select max(latest.id)
                      from reply_attempts as latest
                      where latest.conversation_id=attempts.conversation_id
                        and latest.trigger_message_id=attempts.trigger_message_id
                  )
                order by attempts.id desc
                limit ?
                """,
                (max(1, limit),),
            ).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def count_current_unresolved_problem_attempts(self) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                select count(*) as count
                from reply_attempts as attempts
                where attempts.send_status in ('needs_human', 'blocked', 'failed')
                  and (
                      (
                          attempts.send_status = 'needs_human'
                          and attempts.reviewed_at is null
                          and not exists (
                              select 1
                              from reply_tasks as tasks
                              where tasks.channel=attempts.channel
                                and tasks.conversation_id=attempts.conversation_id
                                and tasks.trigger_message_id=attempts.trigger_message_id
                                and tasks.status in ('done', 'pending', 'processing')
                          )
                      )
                      or (
                          attempts.send_status != 'needs_human'
                          and not exists (
                              select 1
                              from reply_tasks as tasks
                              where tasks.channel=attempts.channel
                                and tasks.conversation_id=attempts.conversation_id
                                and tasks.trigger_message_id=attempts.trigger_message_id
                                and (
                                    tasks.status in ('done', 'pending', 'processing')
                                )
                          )
                      )
                  )
                  and attempts.id=(
                      select max(latest.id)
                      from reply_attempts as latest
                      where latest.conversation_id=attempts.conversation_id
                        and latest.trigger_message_id=attempts.trigger_message_id
                  )
                """,
            ).fetchone()
            return int(row["count"])

    def list_reply_attempts(
        self,
        limit: int | None = None,
        offset: int = 0,
        *,
        send_status: str | None = None,
        send_statuses: tuple[str, ...] | None = None,
        query_text: str = "",
    ) -> list[ReplyAttempt]:
        with self._connect() as db:
            query = """
                select *
                from reply_attempts
            """
            filters, args = self._reply_attempt_filters(
                send_status=send_status,
                send_statuses=send_statuses,
                query_text=query_text,
            )
            if filters:
                query = f"{query} where {' and '.join(filters)}"
            query = f"{query} order by id desc"
            if limit is not None:
                query = f"{query} limit ? offset ?"
                args.extend([limit, max(0, offset)])
            rows = db.execute(query, args).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def list_history_items(
        self,
        limit: int | None = None,
        offset: int = 0,
        *,
        send_statuses: tuple[str, ...] | None = None,
        query_text: str = "",
        kinds: tuple[str, ...] | None = None,
        reply_channels: tuple[str, ...] | None = None,
        object_types: tuple[str, ...] | None = None,
        created_since: str = "",
    ) -> list[HistoryItem]:
        query, args = self._history_items_query(
            send_statuses=send_statuses,
            query_text=query_text,
            kinds=kinds,
            reply_channels=reply_channels,
            object_types=object_types,
            created_since=created_since,
        )
        query = f"{query} order by created_at desc, source_id desc, kind desc"
        if limit is not None:
            query = f"{query} limit ? offset ?"
            args.extend([limit, max(0, offset)])
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        return [HistoryItem.model_validate(dict(row)) for row in rows]

    def count_history_items(
        self,
        *,
        send_statuses: tuple[str, ...] | None = None,
        query_text: str = "",
        kinds: tuple[str, ...] | None = None,
        reply_channels: tuple[str, ...] | None = None,
        object_types: tuple[str, ...] | None = None,
        created_since: str = "",
    ) -> int:
        query, args = self._history_items_query(
            send_statuses=send_statuses,
            query_text=query_text,
            kinds=kinds,
            reply_channels=reply_channels,
            object_types=object_types,
            created_since=created_since,
        )
        with self._connect() as db:
            row = db.execute(f"select count(*) as count from ({query})", args).fetchone()
        return int(row["count"])

    @staticmethod
    def _history_items_query(
        *,
        send_statuses: tuple[str, ...] | None,
        query_text: str,
        kinds: tuple[str, ...] | None,
        reply_channels: tuple[str, ...] | None,
        object_types: tuple[str, ...] | None,
        created_since: str,
    ) -> tuple[str, list[object]]:
        query = """
            with history_items as (
                select
                    'reply' as kind,
                    case
                        when action='oa_approval' or oa_process_instance_id<>'' then 'approval'
                        when channel='wechat' then 'wechat'
                        else 'replay'
                    end as object_type,
                    id as source_id,
                    conversation_title as source_title,
                    trigger_sender as source_actor,
                    '问' as input_label,
                    trigger_text as input_text,
                    '答' as output_label,
                    case
                        when final_reply_text != '' then final_reply_text
                        else draft_reply_text
                    end as output_text,
                    action,
                    case
                        when channel = 'wechat' then coalesce((
                            select case deliveries.status
                                when 'ready_to_send' then 'pending'
                                when 'sending' then 'processing'
                                when 'failed' then case
                                    when deliveries.error='user_rejected' then 'skipped'
                                    else 'failed'
                                end
                                else deliveries.status
                            end
                            from reply_tasks as tasks
                            join wechat_deliveries as deliveries
                                on deliveries.reply_task_id=tasks.id
                            where tasks.channel='wechat'
                              and tasks.conversation_id=reply_attempts.conversation_id
                              and tasks.trigger_message_id=reply_attempts.trigger_message_id
                            limit 1
                        ), send_status)
                        when action in ('memory_write', 'oa_approval')
                             and send_status in ('failed', 'blocked', 'pending', 'dry_run', 'needs_human')
                             and exists (
                                select 1
                                from reply_attempts as newer_side_effects
                                where newer_side_effects.conversation_id=reply_attempts.conversation_id
                                  and newer_side_effects.trigger_message_id=reply_attempts.trigger_message_id
                                  and newer_side_effects.action=reply_attempts.action
                                  and newer_side_effects.id>reply_attempts.id
                                  and newer_side_effects.send_status in (
                                      'sent', 'skipped', 'commented', 'reacted',
                                      'calendar', 'document', 'blocked'
                                  )
                             )
                        then 'skipped'
                        when send_status in ('failed', 'blocked', 'pending', 'dry_run', 'needs_human')
                             and action not in ('memory_write', 'oa_approval')
                             and exists (
                                select 1
                                from reply_attempts as newer_attempts
                                where newer_attempts.conversation_id=reply_attempts.conversation_id
                                  and newer_attempts.trigger_message_id=reply_attempts.trigger_message_id
                                  and newer_attempts.id>reply_attempts.id
                                  and newer_attempts.send_status in (
                                      'sent', 'skipped', 'commented', 'reacted',
                                      'calendar', 'document', 'blocked'
                                  )
                             )
                        then 'skipped'
                        when send_status in ('failed', 'blocked', 'pending', 'dry_run', 'needs_human')
                             and action not in ('memory_write', 'oa_approval')
                             and exists (
                                select 1
                                from sent_replies as sent
                                where sent.conversation_id=reply_attempts.conversation_id
                                  and sent.trigger_message_id=reply_attempts.trigger_message_id
                                  and datetime(sent.sent_at)>=datetime(reply_attempts.created_at)
                             )
                        then 'skipped'
                        else send_status
                    end as status,
                    conversation_title as target_title,
                    codex_session_id,
                    0 as project_id,
                    0 as todo_id,
                    0 as follow_up_id,
                    channel,
                    created_at,
                    iif(?1, conversation_id || ' ' || conversation_title || ' ' ||
                    trigger_message_id || ' ' || trigger_sender || ' ' ||
                    trigger_text || ' ' || action || ' ' || sensitivity_kind || ' ' ||
                    codex_reason || ' ' || draft_reply_text || ' ' || final_reply_text || ' ' ||
                    permission_action || ' ' || permission_reason || ' ' || send_status || ' ' ||
                    send_error || ' ' || reviewer_feedback || ' ' || corrected_reply_text
                    , '') as search_text
                from reply_attempts
                where oa_process_instance_id = ''
                   or id = (
                        select process_attempts.id
                        from reply_attempts as process_attempts
                        where process_attempts.oa_process_instance_id = reply_attempts.oa_process_instance_id
                          and process_attempts.oa_process_instance_id <> ''
                        order by process_attempts.created_at desc,
                            process_attempts.id desc
                        limit 1
                   )
                union all
                select
                    'meeting' as kind,
                    'meeting' as object_type,
                    runs.id as source_id,
                    jobs.title as source_title,
                    'Meeting Alignment Agent' as source_actor,
                    '会议' as input_label,
                    jobs.title as input_text,
                    '对齐' as output_label,
                    case
                        when jobs.final_message != '' then jobs.final_message
                        else runs.audit_summary
                    end as output_text,
                    case
                        when jobs.status='no_action' then 'no_action'
                        else 'meeting_alignment'
                    end as action,
                    case
                        when runs.status='no_action' then 'skipped'
                        when runs.status in ('retry', 'failed') then 'failed'
                        when runs.status='ready_to_send' and jobs.status='sent' then 'sent'
                        when runs.status='ready_to_send' and exists (
                            select 1 from meeting_alignment_runs as later_runs
                            where later_runs.job_id=runs.job_id and later_runs.id>runs.id
                        ) then 'skipped'
                        when runs.status='ready_to_send' and jobs.status in ('retry', 'failed') then 'failed'
                        else runs.status
                    end as status,
                    jobs.target_title,
                    runs.codex_session_id,
                    0 as project_id,
                    0 as todo_id,
                    0 as follow_up_id,
                    'dingtalk' as channel,
                    runs.created_at,
                    iif(?1, jobs.meeting_id || ' ' || jobs.title || ' ' || jobs.source_json || ' ' ||
                    jobs.participants_json || ' ' || jobs.error || ' ' || jobs.decision_json || ' ' ||
                    jobs.target_kind || ' ' || jobs.target_id || ' ' || jobs.target_title || ' ' ||
                    jobs.mentions_json || ' ' || jobs.final_message || ' ' || jobs.send_result_json || ' ' ||
                    runs.decision_json || ' ' || runs.audit_summary || ' ' || runs.error || ' ' ||
                    runs.codex_session_id || ' ' || runs.status
                    , '') as search_text
                from meeting_alignment_runs as runs
                join meeting_alignment_jobs as jobs on jobs.id=runs.job_id
                union all
                select
                    'task' as kind,
                    'task' as object_type,
                    updates.id as source_id,
                    projects.title as source_title,
                    'Task Agent' as source_actor,
                    '来源' as input_label,
                    updates.source_type || ':' || updates.source_ref as input_text,
                    '更新' as output_label,
                    updates.summary as output_text,
                    'task_update' as action,
                    'done' as status,
                    projects.title as target_title,
                    '' as codex_session_id,
                    updates.project_id as project_id,
                    0 as todo_id,
                    0 as follow_up_id,
                    'dingtalk' as channel,
                    updates.created_at,
                    iif(?1, projects.title || ' ' || projects.category || ' ' ||
                    projects.owner_name || ' ' || projects.goal || ' ' ||
                    projects.background || ' ' || projects.current_state || ' ' ||
                    projects.next_step || ' ' || updates.source_type || ' ' ||
                    updates.source_ref || ' ' || updates.summary || ' ' ||
                    updates.changes_json || ' ' || updates.merge_reason
                    , '') as search_text
                from work_updates as updates
                join work_projects as projects on projects.id=updates.project_id
                union all
                select
                    'task' as kind,
                    'task' as object_type,
                    drafts.id as source_id,
                    projects.title as source_title,
                    'Follow-up' as source_actor,
                    '跟进' as input_label,
                    drafts.question_text as input_text,
                    '结果' as output_label,
                    case
                        when drafts.status='sent' then coalesce(nullif(drafts.reaction_summary, ''), '已发送跟进')
                        when drafts.status='completed' then coalesce(nullif(drafts.suppressed_reason, ''), '已完成跟进')
                        when drafts.status in ('skipped', 'cancelled') then coalesce(nullif(drafts.suppressed_reason, ''), '已跳过跟进')
                        when drafts.status='failed' then coalesce(nullif(drafts.send_result_json, '{}'), '发送失败')
                        else drafts.scheduled_at
                    end as output_text,
                    'follow_up_' || drafts.status as action,
                    case
                        when drafts.status='sent' then 'sent'
                        when drafts.status='completed' then 'done'
                        when drafts.status in ('draft', 'approved') then 'pending'
                        when drafts.status in ('skipped', 'cancelled') then 'skipped'
                        when drafts.status='failed' then 'failed'
                        else drafts.status
                    end as status,
                    coalesce(nullif(todos.title, ''), drafts.owner_name, projects.title) as target_title,
                    '' as codex_session_id,
                    drafts.project_id as project_id,
                    drafts.todo_id as todo_id,
                    drafts.id as follow_up_id,
                    'dingtalk' as channel,
                    coalesce(nullif(drafts.sent_at, ''), nullif(drafts.updated_at, ''), drafts.created_at) as created_at,
                    iif(?1, projects.title || ' ' || projects.category || ' ' ||
                    projects.owner_name || ' ' || projects.goal || ' ' ||
                    projects.background || ' ' || projects.current_state || ' ' ||
                    projects.next_step || ' ' || coalesce(todos.title, '') || ' ' ||
                    coalesce(todos.description, '') || ' ' || drafts.owner_name || ' ' ||
                    drafts.target_conversation_id || ' ' || drafts.target_kind || ' ' ||
                    drafts.question_text || ' ' || drafts.status || ' ' ||
                    drafts.send_result_json || ' ' || drafts.evidence_check_json || ' ' ||
                    drafts.reaction_status || ' ' || drafts.reaction_summary || ' ' ||
                    drafts.suppressed_reason
                    , '') as search_text
                from follow_up_drafts as drafts
                join work_projects as projects on projects.id=drafts.project_id
                left join work_todos as todos on todos.id=drafts.todo_id
            )
            select * from history_items
        """
        filters: list[str] = []
        args: list[object] = [bool(query_text.strip())]
        if send_statuses:
            placeholders = ",".join("?" for _ in send_statuses)
            filters.append(f"status in ({placeholders})")
            args.extend(send_statuses)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            filters.append(f"kind in ({placeholders})")
            args.extend(kinds)
        if reply_channels:
            placeholders = ",".join("?" for _ in reply_channels)
            filters.append(f"(kind != 'reply' or channel in ({placeholders}))")
            args.extend(reply_channels)
        if object_types:
            placeholders = ",".join("?" for _ in object_types)
            filters.append(f"object_type in ({placeholders})")
            args.extend(object_types)
        if created_since.strip():
            filters.append("created_at >= ?")
            args.append(created_since)
        if query_text.strip():
            needle = f"%{query_text.strip().lower()}%"
            filters.append("lower(search_text) like ?")
            args.append(needle)
        if filters:
            query = f"{query} where {' and '.join(filters)}"
        return query, args

    def list_reply_attempts_by_ids(self, attempt_ids: list[int]) -> list[ReplyAttempt]:
        if not attempt_ids:
            return []
        placeholders = ",".join("?" for _ in attempt_ids)
        with self._connect() as db:
            rows = db.execute(
                f"select * from reply_attempts where id in ({placeholders})",
                attempt_ids,
            ).fetchall()
        return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def list_reply_attempts_after(self, attempt_id: int) -> list[ReplyAttempt]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_attempts
                where id > ?
                order by id asc
                """,
                (attempt_id,),
            ).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def list_reply_attempts_since(self, since_utc: str) -> list[ReplyAttempt]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_attempts
                where created_at >= ?
                order by created_at asc, id asc
                """,
                (since_utc,),
            ).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def list_reply_attempts_for_conversation(
        self, conversation_id: str, limit: int | None = None
    ) -> list[ReplyAttempt]:
        with self._connect() as db:
            query = """
                select *
                from reply_attempts
                where conversation_id=?
                order by id desc
            """
            args: tuple[object, ...] = (conversation_id,)
            if limit is not None:
                query = f"{query} limit ?"
                args = (conversation_id, limit)
            rows = db.execute(query, args).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def list_oa_attempt_history(
        self, process_instance_id: str, limit: int = 50
    ) -> list[ReplyAttempt]:
        process_id = process_instance_id.strip()
        if not process_id:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_attempts
                where oa_process_instance_id=?
                order by created_at desc, id desc
                limit ?
                """,
                (process_id, max(1, limit)),
            ).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def list_oa_attempt_histories(
        self, process_instance_ids: Sequence[str]
    ) -> dict[str, list[ReplyAttempt]]:
        """Load every attempt for several approval processes in one query."""
        process_ids = list(
            dict.fromkeys(
                process_id.strip()
                for process_id in process_instance_ids
                if process_id.strip()
            )
        )
        histories: dict[str, list[ReplyAttempt]] = {
            process_id: [] for process_id in process_ids
        }
        if not process_ids:
            return histories
        placeholders = ", ".join("?" for _ in process_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from reply_attempts
                where oa_process_instance_id in ({placeholders})
                order by oa_process_instance_id, created_at desc, id desc
                """,
                process_ids,
            ).fetchall()
        for row in rows:
            attempt = ReplyAttempt.model_validate(dict(row))
            histories[attempt.oa_process_instance_id].append(attempt)
        return histories

    def backfill_oa_audit_metadata(self) -> int:
        """Recover OA identity for historical agent attempts by exact task key."""
        with self._connect() as db:
            rows = db.execute(
                """
                select reply_attempts.id, reply_tasks.oa_url
                from reply_attempts
                join reply_tasks on reply_tasks.conversation_id=reply_attempts.conversation_id
                    and reply_tasks.trigger_message_id=reply_attempts.trigger_message_id
                where reply_attempts.action='agent_run'
                    and reply_attempts.oa_process_instance_id=''
                    and reply_tasks.oa_url<>''
                """
            ).fetchall()
            repaired = 0
            for row in rows:
                process_instance_id, task_id = self._oa_identifiers_from_url(
                    str(row["oa_url"] or "")
                )
                if not process_instance_id:
                    continue
                cursor = db.execute(
                    """
                    update reply_attempts
                    set oa_process_instance_id=?, oa_task_id=?, oa_url=?,
                        oa_action=case when oa_action='' then 'review' else oa_action end,
                        updated_at=current_timestamp
                    where id=? and oa_process_instance_id=''
                    """,
                    (
                        process_instance_id,
                        task_id,
                        str(row["oa_url"] or ""),
                        int(row["id"]),
                    ),
                )
                repaired += cursor.rowcount
            return repaired

    @staticmethod
    def _oa_identifiers_from_url(url: str) -> tuple[str, str]:
        query = parse_qs(urlsplit(url).query)
        values = {
            "".join(key.replace("_", "").casefold().split()): value
            for key, value in query.items()
        }
        process_values = values.get("procinstid") or values.get("processinstanceid")
        task_values = values.get("taskid")
        process_instance_id = str(process_values[0]).strip() if process_values else ""
        task_id = str(task_values[0]).strip() if task_values else ""
        return process_instance_id, task_id

    def list_reply_attempts_for_codex_session(
        self, codex_session_id: str, limit: int | None = None
    ) -> list[ReplyAttempt]:
        with self._connect() as db:
            query = """
                select *
                from reply_attempts
                where codex_session_id=?
                order by id desc
            """
            args: tuple[object, ...] = (codex_session_id,)
            if limit is not None:
                query = f"{query} limit ?"
                args = (codex_session_id, limit)
            rows = db.execute(query, args).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def upsert_codex_session_search_index(
        self,
        *,
        session_id: str,
        source_type: str,
        source_id: str,
        title: str,
        summary_text: str,
        fts_text: str,
        embedding: list[float] | None = None,
        embedding_model: str = "",
    ) -> None:
        if not session_id.strip():
            return
        embedding_json = (
            json.dumps(embedding, ensure_ascii=False) if embedding is not None else ""
        )
        embedding_updated_at_sql = (
            "current_timestamp" if embedding is not None else "embedding_updated_at"
        )
        with self._connect() as db:
            row = db.execute(
                """
                select id from codex_session_search_index
                where session_id=?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                cursor = db.execute(
                    f"""
                    insert into codex_session_search_index (
                        session_id,
                        source_type,
                        source_id,
                        title,
                        summary_text,
                        fts_text,
                        embedding_json,
                        embedding_model,
                        embedding_updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, {
                        'current_timestamp' if embedding is not None else "''"
                    })
                    """,
                    (
                        session_id,
                        source_type,
                        source_id,
                        title,
                        summary_text,
                        fts_text,
                        embedding_json,
                        embedding_model,
                    ),
                )
                row_id = int(cursor.lastrowid)
            else:
                row_id = int(row["id"])
                db.execute(
                    f"""
                    update codex_session_search_index
                    set source_type=?,
                        source_id=?,
                        title=?,
                        summary_text=?,
                        fts_text=?,
                        embedding_json=case when ? != '' then ? else embedding_json end,
                        embedding_model=case when ? != '' then ? else embedding_model end,
                        embedding_updated_at={embedding_updated_at_sql},
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (
                        source_type,
                        source_id,
                        title,
                        summary_text,
                        fts_text,
                        embedding_json,
                        embedding_json,
                        embedding_model,
                        embedding_model,
                        row_id,
                    ),
                )
                db.execute(
                    "delete from codex_session_search_fts where rowid=?",
                    (row_id,),
                )
            db.execute(
                """
                insert into codex_session_search_fts (
                    rowid, title, summary_text, fts_text
                )
                values (?, ?, ?, ?)
                """,
                (row_id, title, summary_text, fts_text),
            )

    def search_codex_sessions(
        self,
        *,
        fts_query: str,
        query_embedding: list[float] | None = None,
        limit: int = 3,
    ) -> list[CodexSessionSearchResult]:
        fts_scores: dict[int, float] = {}
        with self._connect() as db:
            if fts_query.strip():
                try:
                    rows = db.execute(
                        """
                        select rowid, bm25(codex_session_search_fts) as bm25_score
                        from codex_session_search_fts
                        where codex_session_search_fts match ?
                        order by bm25_score
                        limit ?
                        """,
                        (fts_query, max(limit * 5, 10)),
                    ).fetchall()
                    fts_scores = {
                        int(row["rowid"]): float(row["bm25_score"]) for row in rows
                    }
                except sqlite3.OperationalError:
                    fts_scores = {}
            rows = db.execute(
                """
                select *
                from codex_session_search_index
                order by updated_at desc
                """
            ).fetchall()
        results = []
        for row in rows:
            row_id = int(row["id"])
            stored_embedding = _embedding_from_json(row["embedding_json"])
            embedding_score = _embedding_score(
                query_embedding,
                stored_embedding,
            )
            bm25_score = fts_scores.get(row_id)
            has_embedding_candidate = bool(query_embedding and stored_embedding)
            if bm25_score is None and not has_embedding_candidate:
                continue
            bm25_normalized = (
                1.0 / (1.0 + max(0.0, bm25_score))
                if bm25_score is not None
                else 0.0
            )
            score = 0.55 * embedding_score + 0.30 * bm25_normalized
            results.append(
                CodexSessionSearchResult(
                    session_id=row["session_id"],
                    source_type=row["source_type"],
                    source_id=row["source_id"],
                    title=row["title"],
                    summary_text=row["summary_text"],
                    fts_text=row["fts_text"],
                    embedding_score=embedding_score,
                    bm25_score=bm25_score,
                    score=score,
                    updated_at=row["updated_at"],
                )
            )
        results.sort(key=lambda result: result.score, reverse=True)
        return results[:limit]

    def list_reviewed_reply_attempts(
        self, limit: int | None = None
    ) -> list[ReplyAttempt]:
        with self._connect() as db:
            query = """
                select *
                from reply_attempts
                where reviewer_feedback != '' or corrected_reply_text != ''
                order by id desc
            """
            args: tuple[int, ...] = ()
            if limit is not None:
                query = f"{query} limit ?"
                args = (limit,)
            rows = db.execute(query, args).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def count_reply_attempts(
        self,
        *,
        send_status: str | None = None,
        send_statuses: tuple[str, ...] | None = None,
        query_text: str = "",
    ) -> int:
        with self._connect() as db:
            filters, args = self._reply_attempt_filters(
                send_status=send_status,
                send_statuses=send_statuses,
                query_text=query_text,
            )
            where_sql = f" where {' and '.join(filters)}" if filters else ""
            row = db.execute(
                f"select count(*) as count from reply_attempts{where_sql}",
                args,
            ).fetchone()
            return int(row["count"])

    def count_recoverable_blocked_reply_attempts(self) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                select count(*) as count
                from reply_attempts as attempts
                where attempts.send_status='blocked'
                  and (
                      (
                          attempts.action in ('memory_write', 'oa_approval')
                          and attempts.id = (
                              select max(latest.id)
                              from reply_attempts as latest
                              where latest.conversation_id=attempts.conversation_id
                                and latest.trigger_message_id=attempts.trigger_message_id
                                and latest.action=attempts.action
                          )
                      )
                      or (
                          attempts.action not in ('memory_write', 'oa_approval')
                          and attempts.id = (
                              select max(latest.id)
                              from reply_attempts as latest
                              where latest.conversation_id=attempts.conversation_id
                                and latest.trigger_message_id=attempts.trigger_message_id
                          )
                          and not exists (
                              select 1
                              from sent_replies as sent
                              where sent.conversation_id=attempts.conversation_id
                                and sent.trigger_message_id=attempts.trigger_message_id
                          )
                      )
                  )
                """,
            ).fetchone()
            return int(row["count"])

    def _reply_attempt_filters(
        self,
        *,
        send_status: str | None = None,
        send_statuses: tuple[str, ...] | None = None,
        query_text: str = "",
    ) -> tuple[list[str], list[object]]:
        filters: list[str] = []
        args: list[object] = []
        statuses = send_statuses or ((send_status,) if send_status else ())
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            filters.append(f"send_status in ({placeholders})")
            args.extend(statuses)
        if query_text.strip():
            needle = f"%{query_text.strip().lower()}%"
            filters.append(
                """(
                    lower(coalesce(conversation_id, '')) like ?
                    or lower(coalesce(conversation_title, '')) like ?
                    or lower(coalesce(trigger_message_id, '')) like ?
                    or lower(coalesce(trigger_sender, '')) like ?
                    or lower(coalesce(trigger_text, '')) like ?
                    or lower(coalesce(draft_reply_text, '')) like ?
                    or lower(coalesce(final_reply_text, '')) like ?
                    or lower(coalesce(corrected_reply_text, '')) like ?
                    or lower(coalesce(action, '')) like ?
                    or lower(coalesce(send_status, '')) like ?
                    or lower(coalesce(send_error, '')) like ?
                )"""
            )
            args.extend([needle] * 11)
        return filters, args

    def enqueue_work_summary_input(
        self,
        source_type: str,
        source_ref: str,
        payload_json: str,
    ) -> int:
        with self._connect() as db:
            db.execute(
                """
                insert into work_summary_inputs (source_type, source_ref, payload_json)
                values (?, ?, ?)
                on conflict(source_type, source_ref) do update set
                    payload_json=excluded.payload_json,
                    status=case
                        when work_summary_inputs.status in ('failed', 'skipped')
                            then 'pending'
                        else work_summary_inputs.status
                    end,
                    error=case
                        when work_summary_inputs.status in ('failed', 'skipped')
                            then ''
                        else work_summary_inputs.error
                    end,
                    available_at=case
                        when work_summary_inputs.status in ('failed', 'skipped')
                            then ''
                        else work_summary_inputs.available_at
                    end,
                    updated_at=current_timestamp
                """,
                (source_type, source_ref, payload_json),
            )
            row = db.execute(
                """
                select id from work_summary_inputs
                where source_type=? and source_ref=?
                """,
                (source_type, source_ref),
            ).fetchone()
            return int(row["id"])

    def claim_work_summary_inputs(self, limit: int) -> list[WorkSummaryInput]:
        if limit <= 0:
            return []
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select *
                from work_summary_inputs
                where status='pending'
                  and (available_at='' or available_at <= current_timestamp)
                order by id
                limit ?
                """,
                (limit,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            db.execute(
                f"""
                update work_summary_inputs
                set status='processing',
                    attempts=attempts + 1,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                ids,
            )
            claimed = db.execute(
                f"""
                select *
                from work_summary_inputs
                where id in ({placeholders})
                order by id
                """,
                ids,
            ).fetchall()
            return [WorkSummaryInput.model_validate(dict(row)) for row in claimed]

    def reset_stale_processing_work_summary_inputs(self, max_age_seconds: int) -> int:
        if max_age_seconds <= 0:
            return 0
        with self._connect() as db:
            cursor = db.execute(
                """
                update work_summary_inputs
                set status='pending',
                    attempts=max(attempts - 1, 0),
                    error='',
                    updated_at=current_timestamp
                where status='processing'
                  and datetime(updated_at) <= datetime('now', ?)
                """,
                (f"-{int(max_age_seconds)} seconds",),
            )
            return cursor.rowcount

    def reset_processing_work_summary_inputs(self) -> list[WorkSummaryInput]:
        with self._immediate_write_transaction() as db:
            rows = db.execute(
                """
                select *
                from work_summary_inputs
                where status='processing'
                order by updated_at, id
                """
            ).fetchall()
            input_ids = [row["id"] for row in rows]
            if not input_ids:
                return []
            placeholders = ",".join("?" for _ in input_ids)
            db.execute(
                f"""
                update work_summary_inputs
                set status='pending',
                    attempts=max(attempts - 1, 0),
                    error='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                input_ids,
            )
            return [WorkSummaryInput.model_validate(dict(row)) for row in rows]

    def mark_work_summary_input_done(
        self, input_id: int, *, _db: sqlite3.Connection | None = None
    ) -> None:
        with self._optional_connection(_db) as db:
            db.execute(
                """
                update work_summary_inputs
                set status='done', error='', updated_at=current_timestamp
                where id=?
                """,
                (input_id,),
            )

    def mark_work_summary_input_skipped(
        self,
        input_id: int,
        reason: str,
        *,
        _db: sqlite3.Connection | None = None,
    ) -> None:
        with self._optional_connection(_db) as db:
            db.execute(
                """
                update work_summary_inputs
                set status='skipped', error=?, updated_at=current_timestamp
                where id=?
                """,
                (reason, input_id),
            )

    def mark_work_summary_input_failed(self, input_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='failed', error=?, updated_at=current_timestamp
                where id=?
                """,
                (error, input_id),
            )

    def requeue_failed_work_summary_input(self, input_id: int, reason: str) -> bool:
        """Restore one reviewed failure after its root cause has been fixed."""
        with self._connect() as db:
            cursor = db.execute(
                """
                update work_summary_inputs
                set status='pending',
                    error=?,
                    available_at='',
                    updated_at=current_timestamp
                where id=? and status='failed'
                """,
                (reason.strip(), input_id),
            )
            return cursor.rowcount == 1

    def schedule_work_summary_input_retry(
        self, input_id: int, error: str, *, available_at: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='pending',
                    error=?,
                    available_at=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (error, available_at, input_id),
            )

    def defer_work_summary_input_for_capacity(
        self, input_id: int, error: str, *, available_at: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='pending',
                    attempts=max(attempts - 1, 0),
                    error=?,
                    available_at=?,
                    updated_at=current_timestamp
                where id=? and status in ('processing', 'failed')
                """,
                (error, available_at, input_id),
            )

    def defer_meeting_alignment_job_for_capacity(
        self, job_id: int, *, available_at: str, error: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update meeting_alignment_jobs
                set status='retry',
                    attempts=max(attempts - 1, 0),
                    locked_at=null,
                    available_at=?,
                    error=?,
                    updated_at=current_timestamp
                where id=? and status='processing'
                """,
                (available_at, error, job_id),
            )

    @staticmethod
    def _filter_allowed_values(
        values: dict[str, object],
        allowed_columns: set[str],
    ) -> dict[str, object]:
        unknown_columns = set(values) - allowed_columns
        if unknown_columns:
            unknown = ", ".join(sorted(unknown_columns))
            raise ValueError(f"Unsupported column(s): {unknown}")
        return dict(values)

    def create_work_project(
        self, *, _db: sqlite3.Connection | None = None, **values
    ) -> int:
        allowed_columns = {
            "title",
            "category",
            "tags_json",
            "status",
            "priority",
            "risk_level",
            "needs_derek_attention",
            "owner_user_id",
            "owner_name",
            "owner_evidence_json",
            "related_people_json",
            "goal",
            "background",
            "facts_json",
            "current_state",
            "blocker",
            "next_step",
            "next_follow_up_at",
            "follow_up_mode",
            "source_conversations_json",
            "memory_context_json",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if "needs_derek_attention" in filtered:
            filtered["needs_derek_attention"] = int(
                bool(filtered["needs_derek_attention"])
            )
        keys = list(filtered.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._optional_connection(_db) as db:
            cursor = db.execute(
                f"insert into work_projects ({columns}) values ({placeholders})",
                [filtered[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_work_project(
        self, project_id: int, *, _db: sqlite3.Connection | None = None, **values
    ) -> None:
        if not values:
            return
        allowed_columns = {
            "title",
            "category",
            "tags_json",
            "status",
            "priority",
            "risk_level",
            "needs_derek_attention",
            "owner_user_id",
            "owner_name",
            "owner_evidence_json",
            "related_people_json",
            "goal",
            "background",
            "facts_json",
            "current_state",
            "blocker",
            "next_step",
            "next_follow_up_at",
            "follow_up_mode",
            "source_conversations_json",
            "memory_context_json",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if "needs_derek_attention" in filtered:
            filtered["needs_derek_attention"] = int(
                bool(filtered["needs_derek_attention"])
            )
        assignments = ", ".join(f"{key}=?" for key in filtered)
        with self._optional_connection(_db) as db:
            db.execute(
                f"""
                update work_projects
                set {assignments},
                    updated_at=current_timestamp,
                    last_activity_at=current_timestamp
                where id=?
                """,
                [*filtered.values(), project_id],
            )

    def update_work_project_memory_context(
        self,
        project_id: int,
        memory_context_json: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_projects
                set memory_context_json=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (memory_context_json, project_id),
            )

    def get_work_project(
        self, project_id: int, *, _db: sqlite3.Connection | None = None
    ) -> WorkProject | None:
        with self._optional_connection(_db) as db:
            row = db.execute(
                "select * from work_projects where id=?",
                (project_id,),
            ).fetchone()
            return None if row is None else WorkProject.model_validate(dict(row))

    def list_work_projects(
        self,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[WorkProject]:
        query = "select * from work_projects"
        args: list[str | int] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query = f"{query} where status in ({placeholders})"
            args.extend(statuses)
        query = f"{query} order by last_activity_at desc, id desc"
        if limit is not None:
            query = f"{query} limit ?"
            args.append(limit)
        with self._connect() as db:
            return [
                WorkProject.model_validate(dict(row)) for row in db.execute(query, args)
            ]

    def list_work_projects_missing_memory_context(
        self,
        limit: int | None = None,
    ) -> list[WorkProject]:
        query = """
            select *
            from work_projects
            where trim(coalesce(memory_context_json, '')) in ('', '{}')
            order by last_activity_at desc, id desc
        """
        args: list[int] = []
        if limit is not None:
            query = f"{query} limit ?"
            args.append(limit)
        with self._connect() as db:
            return [
                WorkProject.model_validate(dict(row)) for row in db.execute(query, args)
            ]

    def create_work_todo(
        self, *, _db: sqlite3.Connection | None = None, **values
    ) -> int:
        allowed_columns = {
            "project_id",
            "title",
            "description",
            "owner_user_id",
            "owner_name",
            "owner_evidence_json",
            "status",
            "priority",
            "deadline_at",
            "next_follow_up_at",
            "follow_up_question",
            "blocker",
            "completion_evidence_json",
            "created_from_update_id",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        keys = list(filtered.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._optional_connection(_db) as db:
            cursor = db.execute(
                f"insert into work_todos ({columns}) values ({placeholders})",
                [filtered[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_work_todo(
        self, todo_id: int, *, _db: sqlite3.Connection | None = None, **values
    ) -> None:
        if not values:
            return
        allowed_columns = {
            "project_id",
            "title",
            "description",
            "owner_user_id",
            "owner_name",
            "owner_evidence_json",
            "status",
            "priority",
            "deadline_at",
            "next_follow_up_at",
            "follow_up_question",
            "blocker",
            "completion_evidence_json",
            "created_from_update_id",
            "completed_at",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if filtered.get("status") == "done" and "completed_at" not in filtered:
            filtered["completed_at"] = "__CURRENT_TIMESTAMP__"
        assignments: list[str] = []
        parameters: list[object] = []
        for key, value in filtered.items():
            if key == "completed_at" and value == "__CURRENT_TIMESTAMP__":
                assignments.append("completed_at=current_timestamp")
                continue
            assignments.append(f"{key}=?")
            parameters.append(value)
        with self._optional_connection(_db) as db:
            db.execute(
                f"""
                update work_todos
                set {', '.join(assignments)}, updated_at=current_timestamp
                where id=?
                """,
                [*parameters, todo_id],
            )

    def get_work_todo(
        self, todo_id: int, *, _db: sqlite3.Connection | None = None
    ) -> WorkTodo | None:
        with self._optional_connection(_db) as db:
            row = db.execute(
                "select * from work_todos where id=?",
                (todo_id,),
            ).fetchone()
            return None if row is None else WorkTodo.model_validate(dict(row))

    def list_work_todos(
        self,
        *,
        project_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
        due_before: str | None = None,
    ) -> list[WorkTodo]:
        query = "select * from work_todos"
        clauses: list[str] = []
        args: list[str | int] = []
        if project_id is not None:
            clauses.append("project_id=?")
            args.append(project_id)
        if statuses:
            clauses.append(f"status in ({','.join('?' for _ in statuses)})")
            args.extend(statuses)
        if due_before is not None:
            clauses.append("next_follow_up_at != '' and next_follow_up_at <= ?")
            args.append(due_before)
        if clauses:
            query = f"{query} where {' and '.join(clauses)}"
        query = f"{query} order by id"
        with self._connect() as db:
            return [WorkTodo.model_validate(dict(row)) for row in db.execute(query, args)]

    def list_work_project_ids_for_todo_owner(
        self,
        owner_user_id: str,
        *,
        project_statuses: tuple[str, ...] = ("active", "waiting"),
        limit: int = 500,
    ) -> set[int]:
        owner_user_id = owner_user_id.strip()
        if not owner_user_id or limit <= 0:
            return set()
        placeholders = ",".join("?" for _ in project_statuses)
        query = f"""
            select distinct todos.project_id
            from work_todos todos
            join work_projects projects on projects.id=todos.project_id
            where todos.owner_user_id=?
              and projects.status in ({placeholders})
            order by projects.last_activity_at desc, projects.id desc
            limit ?
        """
        with self._connect() as db:
            rows = db.execute(
                query,
                [owner_user_id, *project_statuses, limit],
            ).fetchall()
            return {int(row["project_id"]) for row in rows}

    @staticmethod
    def _normalize_dingtalk_todo_link_status(status: object) -> str:
        return DingTalkTodoLinkStatus(str(status)).value

    @staticmethod
    def _normalize_dingtalk_todo_link_row(
        row: sqlite3.Row,
    ) -> WorkTodoDingTalkLink:
        return WorkTodoDingTalkLink.model_validate(dict(row))

    def create_work_todo_dingtalk_link(self, **values) -> int:
        allowed_columns = {
            "work_todo_id",
            "dingtalk_task_id",
            "executor_user_id",
            "executor_name",
            "title_snapshot",
            "deadline_at_snapshot",
            "priority_snapshot",
            "status",
            "last_dingtalk_done",
            "last_dingtalk_payload_json",
            "last_pull_at",
            "last_push_at",
            "last_error",
            "retry_count",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if "work_todo_id" not in filtered:
            raise ValueError("missing work_todo_id")
        if "status" in filtered:
            filtered["status"] = self._normalize_dingtalk_todo_link_status(
                filtered["status"]
            )
        if (
            "last_dingtalk_done" in filtered
            and filtered["last_dingtalk_done"] is not None
        ):
            filtered["last_dingtalk_done"] = int(bool(filtered["last_dingtalk_done"]))
        keys = list(filtered.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._immediate_write_transaction() as db:
            existing = db.execute(
                """
                select id
                from work_todo_dingtalk_links
                where work_todo_id=?
                  and status in ('creating', 'active')
                order by id
                limit 1
                """,
                (filtered["work_todo_id"],),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            try:
                cursor = db.execute(
                    f"""
                    insert into work_todo_dingtalk_links ({columns})
                    values ({placeholders})
                    """,
                    [filtered[key] for key in keys],
                )
            except sqlite3.IntegrityError:
                existing = db.execute(
                    """
                    select id
                    from work_todo_dingtalk_links
                    where work_todo_id=?
                      and status in ('creating', 'active')
                    order by id
                    limit 1
                    """,
                    (filtered["work_todo_id"],),
                ).fetchone()
                if existing is not None:
                    return int(existing["id"])
                raise
            return int(cursor.lastrowid)

    def update_work_todo_dingtalk_link(self, link_id: int, **values) -> None:
        if not values:
            return
        allowed_columns = {
            "dingtalk_task_id",
            "executor_user_id",
            "executor_name",
            "title_snapshot",
            "deadline_at_snapshot",
            "priority_snapshot",
            "status",
            "last_dingtalk_done",
            "last_dingtalk_payload_json",
            "last_pull_at",
            "last_push_at",
            "last_error",
            "retry_count",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if "status" in filtered:
            filtered["status"] = self._normalize_dingtalk_todo_link_status(
                filtered["status"]
            )
        if (
            "last_dingtalk_done" in filtered
            and filtered["last_dingtalk_done"] is not None
        ):
            filtered["last_dingtalk_done"] = int(bool(filtered["last_dingtalk_done"]))
        assignments = ", ".join(f"{key}=?" for key in filtered)
        with self._connect() as db:
            db.execute(
                f"""
                update work_todo_dingtalk_links
                set {assignments},
                    updated_at=current_timestamp
                where id=?
                """,
                [*filtered.values(), link_id],
            )

    def get_work_todo_dingtalk_link(
        self,
        link_id: int,
    ) -> WorkTodoDingTalkLink | None:
        with self._connect() as db:
            row = db.execute(
                "select * from work_todo_dingtalk_links where id=?",
                (link_id,),
            ).fetchone()
            return None if row is None else self._normalize_dingtalk_todo_link_row(row)

    def get_active_work_todo_dingtalk_link(
        self,
        work_todo_id: int,
    ) -> WorkTodoDingTalkLink | None:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from work_todo_dingtalk_links
                where work_todo_id=?
                  and status in ('creating', 'active')
                order by id
                limit 1
                """,
                (work_todo_id,),
            ).fetchone()
            return None if row is None else self._normalize_dingtalk_todo_link_row(row)

    def list_work_todo_dingtalk_links(
        self,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
        work_todo_id: int | None = None,
        with_dingtalk_task_id: bool = False,
    ) -> list[WorkTodoDingTalkLink]:
        if limit <= 0:
            return []
        query = "select * from work_todo_dingtalk_links"
        clauses: list[str] = []
        args: list[str | int] = []
        if work_todo_id is not None:
            clauses.append("work_todo_id=?")
            args.append(work_todo_id)
        if with_dingtalk_task_id:
            clauses.append("trim(coalesce(dingtalk_task_id, '')) != ''")
        if statuses:
            normalized_statuses = tuple(
                self._normalize_dingtalk_todo_link_status(status)
                for status in statuses
            )
            clauses.append(f"status in ({','.join('?' for _ in statuses)})")
            args.extend(normalized_statuses)
        if clauses:
            query = f"{query} where {' and '.join(clauses)}"
        query = f"{query} order by id limit ?"
        args.append(limit)
        with self._connect() as db:
            return [
                self._normalize_dingtalk_todo_link_row(row)
                for row in db.execute(query, args)
            ]

    def list_work_todo_dingtalk_links_for_todo(
        self,
        work_todo_id: int,
        *,
        statuses: tuple[str, ...] | None = None,
    ) -> list[WorkTodoDingTalkLink]:
        query = "select * from work_todo_dingtalk_links where work_todo_id=?"
        args: list[str | int] = [work_todo_id]
        if statuses:
            normalized_statuses = tuple(
                self._normalize_dingtalk_todo_link_status(status)
                for status in statuses
            )
            query = f"{query} and status in ({','.join('?' for _ in statuses)})"
            args.extend(normalized_statuses)
        query = f"{query} order by id"
        with self._connect() as db:
            return [
                self._normalize_dingtalk_todo_link_row(row)
                for row in db.execute(query, args)
            ]

    def list_work_todo_dingtalk_links_for_todos(
        self,
        todo_ids: list[int],
    ) -> dict[int, list[WorkTodoDingTalkLink]]:
        if not todo_ids:
            return {}
        placeholders = ",".join("?" for _ in todo_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from work_todo_dingtalk_links
                where work_todo_id in ({placeholders})
                order by id desc
                """,
                todo_ids,
            ).fetchall()
        result: dict[int, list[WorkTodoDingTalkLink]] = {}
        for row in rows:
            link = self._normalize_dingtalk_todo_link_row(row)
            result.setdefault(link.work_todo_id, []).append(link)
        return result

    def enqueue_task_todo_sync_outbox(
        self,
        *,
        operation_key: str,
        work_todo_id: int,
        operation: str,
        evidence_json: str = "{}",
        _db: sqlite3.Connection | None = None,
    ) -> None:
        if operation not in {"create", "complete"}:
            raise ValueError("task todo sync operation is invalid")
        with self._optional_connection(_db) as db:
            db.execute(
                "insert or ignore into task_todo_sync_outbox "
                "(operation_key, work_todo_id, operation, evidence_json) values (?, ?, ?, ?)",
                (operation_key, work_todo_id, operation, evidence_json),
            )

    def list_task_todo_sync_outbox(
        self, *, statuses: tuple[str, ...] | None = None
    ) -> list[sqlite3.Row]:
        query = "select * from task_todo_sync_outbox"
        args: list[str] = []
        if statuses:
            query += f" where status in ({','.join('?' for _ in statuses)})"
            args.extend(statuses)
        with self._connect() as db:
            return list(db.execute(f"{query} order by id", args).fetchall())

    def claim_task_todo_sync_outbox(
        self, *, owner: str, now: str, lease_seconds: int = 300
    ) -> sqlite3.Row | None:
        lease_until = (
            datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
            + timedelta(seconds=lease_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._agent_run_write_transaction(now) as (db, _):
            db.execute(
                "update task_todo_sync_outbox set status='unknown', lease_owner='', "
                "lease_expires_at='', error='receipt_reconciliation_required', updated_at=? "
                "where status='running' and lease_expires_at<=?",
                (now, now),
            )
            row = db.execute(
                "select * from task_todo_sync_outbox where "
                "(status='queued' or (status='failed' and attempt_count<3 and next_attempt_at<=?)) "
                "order by id limit 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            changed = db.execute(
                "update task_todo_sync_outbox set status='running', lease_owner=?, "
                "lease_expires_at=?, attempt_count=attempt_count+1, updated_at=? "
                "where id=? and status in ('queued', 'failed')",
                (owner, lease_until, now, row["id"]),
            )
            return row if changed.rowcount == 1 else None

    def finish_task_todo_sync_outbox(
        self, *, outbox_id: int, owner: str, status: str, receipt_json: str = "{}", error: str = ""
    ) -> None:
        if status not in {"completed", "failed", "unknown"}:
            raise ValueError("task todo sync terminal status is invalid")
        with self._connect() as db:
            changed = db.execute(
                "update task_todo_sync_outbox set status=?, receipt_json=?, error=?, "
                "lease_owner='', lease_expires_at='', completed_at=current_timestamp, "
                "updated_at=current_timestamp where id=? and status='running' and lease_owner=?",
                (status, receipt_json, error, outbox_id, owner),
            )
            if changed.rowcount != 1:
                raise ValueError("task todo sync receipt ownership lost")

    def retry_task_todo_sync_outbox(
        self, *, outbox_id: int, owner: str, error: str, now: str
    ) -> None:
        with self._connect() as db:
            row = db.execute("select attempt_count from task_todo_sync_outbox where id=?", (outbox_id,)).fetchone()
            if row is None:
                raise ValueError("task todo sync outbox does not exist")
            attempts = int(row["attempt_count"])
            exhausted = attempts >= 3
            next_attempt_at = "" if exhausted else (
                datetime.strptime(now, "%Y-%m-%d %H:%M:%S") + timedelta(seconds=60 * attempts)
            ).strftime("%Y-%m-%d %H:%M:%S")
            changed = db.execute(
                "update task_todo_sync_outbox set status=?, error=?, next_attempt_at=?, "
                "lease_owner='', lease_expires_at='', updated_at=? where id=? and status='running' and lease_owner=?",
                ("failed", f"task_todo_sync_retry_exhausted:{error}" if exhausted else error, next_attempt_at, now, outbox_id, owner),
            )
            if changed.rowcount != 1:
                raise ValueError("task todo sync receipt ownership lost")

    def create_work_update(
        self, *, _db: sqlite3.Connection | None = None, **values
    ) -> int:
        allowed_columns = {
            "project_id",
            "source_type",
            "source_ref",
            "summary",
            "changes_json",
            "merge_reason",
            "confidence",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        keys = list(filtered.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._optional_connection(_db) as db:
            cursor = db.execute(
                f"insert into work_updates ({columns}) values ({placeholders})",
                [filtered[key] for key in keys],
            )
            db.execute(
                """
                update work_projects
                set updated_at=current_timestamp,
                    last_activity_at=current_timestamp
                where id=?
                """,
                (filtered["project_id"],),
            )
            return int(cursor.lastrowid)

    def has_work_update(
        self,
        *,
        project_id: int,
        source_type: str,
        source_ref: str,
    ) -> bool:
        with self._connect() as db:
            row = db.execute(
                """
                select 1
                from work_updates
                where project_id=?
                  and source_type=?
                  and source_ref=?
                limit 1
                """,
                (project_id, source_type, source_ref),
            ).fetchone()
            return row is not None

    def list_work_updates(self, project_id: int, limit: int = 50) -> list[WorkUpdate]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from work_updates
                where project_id=?
                order by id desc
                limit ?
                """,
                (project_id, limit),
            ).fetchall()
            return [WorkUpdate.model_validate(dict(row)) for row in rows]

    def record_task_agent_run(
        self,
        summary_input_id: int,
        codex_session_id: str = "",
        decision_json: str = "{}",
        audit_summary: str = "",
        memory_recall_used: bool = False,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into task_agent_runs (
                    summary_input_id,
                    codex_session_id,
                    decision_json,
                    audit_summary,
                    memory_recall_used,
                    status,
                    finished_at,
                    updated_at
                )
                values (?, ?, ?, ?, ?, 'completed', current_timestamp, current_timestamp)
                """,
                (
                    summary_input_id,
                    codex_session_id,
                    decision_json,
                    audit_summary,
                    int(memory_recall_used),
                ),
            )
            return int(cursor.lastrowid)

    def begin_weekly_okr_analysis_job(
        self,
        *,
        week_end: str,
        manager_user_id: str,
        source_digest: str,
        owner: str = "legacy-weekly-owner",
        lease_seconds: int = 1860,
        now: str | datetime | None = None,
    ) -> WeeklyOkrAnalysisJobClaim:
        manager_user_id = self._require_runtime_attempt_text(
            manager_user_id, field="manager_user_id"
        )
        _, workload_key = self._validate_runtime_operation_workload(
            "weekly_okr", f"{week_end}:{manager_user_id}:{source_digest}"
        )
        normalized_week_end, normalized_manager, normalized_digest = workload_key.split(
            ":", 2
        )
        owner = self._require_runtime_attempt_text(owner, field="owner")
        if lease_seconds <= 0:
            raise ValueError("weekly OKR lease_seconds must be positive")
        with self._agent_run_write_transaction(now) as (db, clock):
            now_value, now_text = clock
            lease_expires_at = (now_value + timedelta(seconds=lease_seconds)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            inserted = db.execute(
                "insert or ignore into weekly_okr_analysis_jobs "
                "(week_end, manager_user_id, source_digest, status, lease_owner, "
                "lease_expires_at, updated_at) values (?, ?, ?, 'running', ?, ?, ?)",
                (
                    normalized_week_end,
                    normalized_manager,
                    normalized_digest,
                    owner,
                    lease_expires_at,
                    now_text,
                ),
            )
            row = db.execute(
                "select id, status, lease_expires_at from weekly_okr_analysis_jobs "
                "where week_end=? and manager_user_id=? and source_digest=?",
                (normalized_week_end, normalized_manager, normalized_digest),
            ).fetchone()
            job_id = int(row["id"])
            if inserted.rowcount == 1:
                return WeeklyOkrAnalysisJobClaim(
                    job_id=job_id,
                    outcome=WeeklyOkrAnalysisJobClaimOutcome.CLAIMED,
                )
            if row["status"] == "failed":
                changed = db.execute(
                    "update weekly_okr_analysis_jobs set status='running', "
                    "error='', finished_at='', lease_owner=?, lease_expires_at=?, "
                    "updated_at=? "
                    "where id=? and status='failed'",
                    (owner, lease_expires_at, now_text, job_id),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("weekly OKR analysis reopen claim lost")
                return WeeklyOkrAnalysisJobClaim(
                    job_id=job_id,
                    outcome=WeeklyOkrAnalysisJobClaimOutcome.CLAIMED,
                )
            if row["status"] == "completed":
                return WeeklyOkrAnalysisJobClaim(
                    job_id=job_id,
                    outcome=WeeklyOkrAnalysisJobClaimOutcome.CACHE_HIT,
                )
            if row["status"] != "running":
                raise RuntimeError("weekly OKR analysis job has invalid status")
            if not row["lease_expires_at"] or row["lease_expires_at"] <= now_text:
                changed = db.execute(
                    "update weekly_okr_analysis_jobs set lease_owner=?, "
                    "lease_expires_at=?, error='', finished_at='', updated_at=? "
                    "where id=? and status='running' and "
                    "(lease_expires_at='' or lease_expires_at<=?)",
                    (owner, lease_expires_at, now_text, job_id, now_text),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("weekly OKR stale lease reclaim lost")
                return WeeklyOkrAnalysisJobClaim(
                    job_id=job_id,
                    outcome=WeeklyOkrAnalysisJobClaimOutcome.CLAIMED,
                    reclaimed_stale=True,
                )
            return WeeklyOkrAnalysisJobClaim(
                job_id=job_id,
                outcome=WeeklyOkrAnalysisJobClaimOutcome.IN_PROGRESS,
            )

    def finish_weekly_okr_analysis_job(
        self,
        job_id: int,
        *,
        status: str,
        error: str = "",
        owner: str = "legacy-weekly-owner",
        now: str | datetime | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("weekly OKR analysis terminal status is invalid")
        owner = self._require_runtime_attempt_text(owner, field="owner")
        with self._agent_run_write_transaction(now) as (db, clock):
            _, now_text = clock
            row = db.execute(
                "select status, error, lease_owner, lease_expires_at "
                "from weekly_okr_analysis_jobs where id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("weekly OKR analysis job does not exist")
            if row["status"] != "running":
                if (row["status"], row["error"]) == (status, error):
                    return
                raise ValueError("conflicting weekly OKR analysis terminal rewrite")
            if row["lease_owner"] != owner or row["lease_expires_at"] <= now_text:
                raise ValueError("weekly OKR analysis lease ownership lost")
            changed = db.execute(
                "update weekly_okr_analysis_jobs set status=?, error=?, "
                "lease_owner='', lease_expires_at='', finished_at=?, updated_at=? "
                "where id=? and status='running' and lease_owner=? "
                "and lease_expires_at>?",
                (status, error, now_text, now_text, job_id, owner, now_text),
            )
            if changed.rowcount != 1:
                raise ValueError("weekly OKR analysis lease ownership lost")

    def reclaim_weekly_okr_analysis_job_cache_miss(
        self,
        job_id: int,
        *,
        week_end: str,
        manager_user_id: str,
        source_digest: str,
        owner: str = "legacy-weekly-owner",
        lease_seconds: int = 1860,
        now: str | datetime | None = None,
    ) -> WeeklyOkrAnalysisJobClaim:
        if job_id <= 0:
            raise ValueError("weekly OKR analysis job id must be positive")
        manager_user_id = self._require_runtime_attempt_text(
            manager_user_id, field="manager_user_id"
        )
        _, workload_key = self._validate_runtime_operation_workload(
            "weekly_okr", f"{week_end}:{manager_user_id}:{source_digest}"
        )
        expected_key = tuple(workload_key.split(":", 2))
        owner = self._require_runtime_attempt_text(owner, field="owner")
        if lease_seconds <= 0:
            raise ValueError("weekly OKR lease_seconds must be positive")
        with self._agent_run_write_transaction(now) as (db, clock):
            now_value, now_text = clock
            lease_expires_at = (now_value + timedelta(seconds=lease_seconds)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            row = db.execute(
                "select week_end, manager_user_id, source_digest, status "
                "from weekly_okr_analysis_jobs where id=?",
                (job_id,),
            ).fetchone()
            if (
                row is None
                or tuple(
                    row[field]
                    for field in ("week_end", "manager_user_id", "source_digest")
                )
                != expected_key
            ):
                raise ValueError("weekly OKR analysis job natural key mismatch")
            if row["status"] == "completed":
                changed = db.execute(
                    "update weekly_okr_analysis_jobs set status='running', "
                    "error='', finished_at='', lease_owner=?, lease_expires_at=?, "
                    "updated_at=? "
                    "where id=? and status='completed'",
                    (owner, lease_expires_at, now_text, job_id),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("weekly OKR cache-miss reclaim lost")
                return WeeklyOkrAnalysisJobClaim(
                    job_id=job_id,
                    outcome=WeeklyOkrAnalysisJobClaimOutcome.CLAIMED,
                )
            if row["status"] == "running":
                return WeeklyOkrAnalysisJobClaim(
                    job_id=job_id,
                    outcome=WeeklyOkrAnalysisJobClaimOutcome.IN_PROGRESS,
                )
            raise ValueError(
                "weekly OKR cache-miss reclaim requires completed or running job"
            )

    def begin_wechat_memory_import_job(
        self, *, import_run_id: str, account_id: str
    ) -> int:
        import_run_id = self._require_runtime_attempt_text(
            import_run_id, field="import_run_id"
        )
        account_id = self._require_runtime_attempt_text(
            account_id, field="account_id"
        )
        with self._agent_run_write_transaction(None) as (db, _):
            cursor = db.execute(
                "insert into wechat_memory_import_jobs "
                "(import_run_id, account_id, status) values (?, ?, 'running')",
                (import_run_id, account_id),
            )
            return int(cursor.lastrowid)

    def finish_wechat_memory_import_job(
        self, job_id: int, *, status: str, error: str = ""
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("WeChat Memory import terminal status is invalid")
        with self._agent_run_write_transaction(None) as (db, _):
            row = db.execute(
                "select status, error from wechat_memory_import_jobs where id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("WeChat Memory import job does not exist")
            if row["status"] != "running":
                if (row["status"], row["error"]) == (status, error):
                    return
                raise ValueError("conflicting WeChat Memory import terminal rewrite")
            db.execute(
                "update wechat_memory_import_jobs set status=?, error=?, "
                "finished_at=current_timestamp, updated_at=current_timestamp "
                "where id=? and status='running'",
                (status, error, job_id),
            )

    def begin_task_agent_run(self, summary_input_id: int) -> int:
        if summary_input_id <= 0:
            raise ValueError("summary_input_id must be positive")
        with self._agent_run_write_transaction(None) as (db, _):
            parent = db.execute(
                "select 1 from work_summary_inputs "
                "where id=? and status='processing'",
                (summary_input_id,),
            ).fetchone()
            if parent is None:
                raise ValueError("task agent run parent is not processing")
            active = db.execute(
                "select id from task_agent_runs "
                "where summary_input_id=? and status='running'",
                (summary_input_id,),
            ).fetchone()
            if active is not None:
                return int(active["id"])
            cursor = db.execute(
                "insert into task_agent_runs "
                "(summary_input_id, status, finished_at, updated_at) "
                "values (?, 'running', '', current_timestamp)",
                (summary_input_id,),
            )
            return int(cursor.lastrowid)

    def finish_task_agent_run(
        self,
        run_id: int,
        *,
        status: str,
        codex_session_id: str = "",
        decision_json: str = "{}",
        audit_summary: str = "",
        memory_recall_used: bool = False,
        error: str = "",
        _db: sqlite3.Connection | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("task agent run terminal status is invalid")
        expected = (
            status,
            codex_session_id,
            decision_json,
            audit_summary,
            int(memory_recall_used),
            error,
        )
        if _db is not None:
            self._finish_task_agent_run_in_connection(_db, run_id, expected)
            return
        with self._agent_run_write_transaction(None) as (db, _):
            self._finish_task_agent_run_in_connection(db, run_id, expected)

    def recover_orphaned_task_agent_runs(self) -> int:
        """Close task runs whose parent input is no longer processing."""
        with self._agent_run_write_transaction(None) as (db, (_, now_text)):
            rows = db.execute(
                """
                select run.id
                from task_agent_runs as run
                join work_summary_inputs as input
                  on input.id=run.summary_input_id
                where run.status='running'
                  and input.status<>'processing'
                """
            ).fetchall()
            if not rows:
                return 0
            run_ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in run_ids)
            db.execute(
                f"""
                update task_agent_runs
                set status='failed', error='orphaned_task_agent_run_parent_not_processing',
                    finished_at=?, updated_at=?
                where status='running' and id in ({placeholders})
                """,
                [now_text, now_text, *run_ids],
            )
            db.execute(
                f"""
                update agent_runtime_attempts
                set status='failed', failure_class='process',
                    failure_code='runtime_parent_terminal_no_effect',
                    failover_permitted=1, lease_owner='', lease_expires_at='',
                    finished_at=?, updated_at=?
                where workload_kind='task'
                  and workload_key in ({placeholders})
                  and status in ('starting', 'running')
                  and first_effect_started_at=''
                """,
                [now_text, now_text, *[str(run_id) for run_id in run_ids]],
            )
            return len(run_ids)

    @staticmethod
    def _finish_task_agent_run_in_connection(
        db: sqlite3.Connection,
        run_id: int,
        expected: tuple[str, str, str, str, int, str],
    ) -> None:
        row = db.execute(
            "select * from task_agent_runs where id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError("task agent run does not exist")
        if row["status"] != "running":
            actual = tuple(
                row[field]
                for field in (
                    "status",
                    "codex_session_id",
                    "decision_json",
                    "audit_summary",
                    "memory_recall_used",
                    "error",
                )
            )
            if actual == expected:
                return
            raise ValueError("conflicting task agent run terminal rewrite")
        db.execute(
            "update task_agent_runs set status=?, codex_session_id=?, "
            "decision_json=?, audit_summary=?, memory_recall_used=?, error=?, "
            "finished_at=current_timestamp, updated_at=current_timestamp "
            "where id=? and status='running'",
            (*expected, run_id),
        )

    @contextmanager
    def task_agent_domain_apply_transaction(self) -> Iterator[sqlite3.Connection]:
        """Make local task-domain changes and terminal run state one commit."""
        with self._agent_run_write_transaction(None) as (db, _):
            yield db

    def create_follow_up_draft(
        self, *, _db: sqlite3.Connection | None = None, **values
    ) -> int:
        allowed_columns = {
            "project_id",
            "todo_id",
            "title",
            "description",
            "owner_user_id",
            "owner_name",
            "owners_json",
            "target_conversation_id",
            "target_kind",
            "question_text",
            "priority",
            "tags_json",
            "participants_json",
            "files_json",
            "risk_check_json",
            "status",
            "send_result_json",
            "evidence_check_json",
            "reaction_status",
            "reaction_summary",
            "suppressed_reason",
            "dedupe_key",
            "scheduled_at",
            "sent_at",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        filtered.setdefault("dedupe_key", self._follow_up_dedupe_key(filtered))
        keys = list(filtered.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._optional_connection(_db) as db:
            dedupe_key = str(filtered.get("dedupe_key") or "").strip()
            if dedupe_key:
                existing = db.execute(
                    """
                    select id
                    from follow_up_drafts
                    where dedupe_key=?
                      and status in ('draft', 'approved', 'sent', 'completed', 'skipped', 'cancelled')
                    order by id desc
                    limit 1
                    """,
                    (dedupe_key,),
                ).fetchone()
                if existing is not None:
                    return int(existing["id"])
            cursor = db.execute(
                f"insert into follow_up_drafts ({columns}) values ({placeholders})",
                [filtered[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_follow_up_draft(
        self, draft_id: int, *, _db: sqlite3.Connection | None = None, **values
    ) -> None:
        if not values:
            return
        allowed_columns = {
            "project_id",
            "todo_id",
            "title",
            "description",
            "owner_user_id",
            "owner_name",
            "owners_json",
            "target_conversation_id",
            "target_kind",
            "question_text",
            "priority",
            "tags_json",
            "participants_json",
            "files_json",
            "risk_check_json",
            "status",
            "send_result_json",
            "evidence_check_json",
            "reaction_status",
            "reaction_summary",
            "suppressed_reason",
            "dedupe_key",
            "scheduled_at",
            "sent_at",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if filtered.get("status") == "sent" and "sent_at" not in filtered:
            filtered["sent_at"] = "__CURRENT_TIMESTAMP__"
        assignments = []
        parameters = []
        for key, value in filtered.items():
            if key == "sent_at" and value == "__CURRENT_TIMESTAMP__":
                assignments.append("sent_at=current_timestamp")
                continue
            assignments.append(f"{key}=?")
            parameters.append(value)
        with self._optional_connection(_db) as db:
            cursor = db.execute(
                f"""
                update follow_up_drafts
                set {', '.join(assignments)},
                    revision=revision+1,
                    send_claim_revision=0,
                    send_claim_token='',
                    send_claim_idempotency_uuid='',
                    updated_at=current_timestamp
                where id=?
                """,
                [*parameters, draft_id],
            )
            if cursor.rowcount == 1:
                db.execute(
                    """
                    update follow_up_send_attempts
                    set state='invalidated', updated_at=current_timestamp
                    where draft_id=?
                      and state='claimed'
                    """,
                    (draft_id,),
                )

    def update_follow_up_draft_if_revision(
        self,
        draft_id: int,
        expected_revision: int,
        **values,
    ) -> bool:
        if not values:
            return False
        allowed_columns = {
            "project_id",
            "todo_id",
            "title",
            "description",
            "owner_user_id",
            "owner_name",
            "owners_json",
            "target_conversation_id",
            "target_kind",
            "question_text",
            "priority",
            "tags_json",
            "participants_json",
            "files_json",
            "risk_check_json",
            "status",
            "send_result_json",
            "evidence_check_json",
            "reaction_status",
            "reaction_summary",
            "suppressed_reason",
            "dedupe_key",
            "scheduled_at",
            "sent_at",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if not filtered:
            return False
        if filtered.get("status") == "sent" and "sent_at" not in filtered:
            filtered["sent_at"] = "__CURRENT_TIMESTAMP__"
        assignments = []
        parameters = []
        for key, value in filtered.items():
            if key == "sent_at" and value == "__CURRENT_TIMESTAMP__":
                assignments.append("sent_at=current_timestamp")
                continue
            assignments.append(f"{key}=?")
            parameters.append(value)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                update follow_up_drafts
                set {', '.join(assignments)},
                    revision=revision+1,
                    send_claim_revision=0,
                    send_claim_token='',
                    send_claim_idempotency_uuid='',
                    updated_at=current_timestamp
                where id=? and revision=?
                """,
                [*parameters, draft_id, expected_revision],
            )
            if cursor.rowcount == 1:
                db.execute(
                    """
                    update follow_up_send_attempts
                    set state='invalidated', updated_at=current_timestamp
                    where draft_id=?
                      and state='claimed'
                    """,
                    (draft_id,),
                )
            return cursor.rowcount == 1

    def claim_follow_up_draft_revision(
        self,
        draft_id: int,
        *,
        expected_revision: int,
        claim_token: str,
        idempotency_uuid: str,
        lease_owner: str,
        claimed_at: str,
        lease_until: str,
    ) -> bool:
        with self._immediate_write_transaction() as db:
            draft = db.execute(
                """
                select revision, status, send_claim_token
                from follow_up_drafts
                where id=? and revision=?
                """,
                (draft_id, expected_revision),
            ).fetchone()
            if draft is None or str(draft["status"]) not in {"draft", "approved"}:
                return False
            attempt = db.execute(
                """
                select *
                from follow_up_send_attempts
                where draft_id=? and draft_revision=?
                order by id desc
                limit 1
                """,
                (draft_id, expected_revision),
            ).fetchone()
            if attempt is not None:
                if str(attempt["idempotency_uuid"]) != idempotency_uuid:
                    return False
                state = str(attempt["state"])
                lease_expired = not str(attempt["lease_until"] or "").strip() or (
                    db.execute(
                        "select datetime(?) <= datetime(?)",
                        (attempt["lease_until"], claimed_at),
                    ).fetchone()[0]
                    == 1
                )
                if state not in {"retryable", "failed"} and not (
                    state in {"claimed", "sending"} and lease_expired
                ):
                    return False
                reclaimed = db.execute(
                    """
                    update follow_up_send_attempts
                    set state='claimed',
                        claim_token=?,
                        lease_owner=?,
                        claimed_at=?,
                        lease_until=?,
                        updated_at=current_timestamp
                    where id=?
                      and state=?
                      and claim_token=?
                    """,
                    (
                        claim_token,
                        lease_owner,
                        claimed_at,
                        lease_until,
                        attempt["id"],
                        state,
                        attempt["claim_token"],
                    ),
                )
                if reclaimed.rowcount != 1:
                    return False
            elif str(draft["send_claim_token"] or "").strip():
                return False

            cursor = db.execute(
                """
                update follow_up_drafts
                set send_claim_revision=?,
                    send_claim_token=?,
                    send_claim_idempotency_uuid=?,
                    updated_at=current_timestamp
                where id=?
                  and revision=?
                  and status in ('draft', 'approved')
                """,
                (
                    expected_revision,
                    claim_token,
                    idempotency_uuid,
                    draft_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount == 1 and attempt is None:
                db.execute(
                    """
                    insert into follow_up_send_attempts (
                        draft_id,
                        draft_revision,
                        claim_token,
                        idempotency_uuid,
                        state,
                        lease_owner,
                        claimed_at,
                        lease_until
                    ) values (?, ?, ?, ?, 'claimed', ?, ?, ?)
                    """,
                    (
                        draft_id,
                        expected_revision,
                        claim_token,
                        idempotency_uuid,
                        lease_owner,
                        claimed_at,
                        lease_until,
                    ),
                )
            return cursor.rowcount == 1

    def transition_follow_up_attempt_to_sending(
        self,
        draft_id: int,
        *,
        claimed_revision: int,
        claim_token: str,
        lease_owner: str,
        now: str,
        lease_until: str,
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                update follow_up_send_attempts
                set state='sending',
                    lease_until=?,
                    updated_at=current_timestamp
                where draft_id=?
                  and draft_revision=?
                  and state='claimed'
                  and claim_token=?
                  and lease_owner=?
                  and datetime(lease_until) >= datetime(?)
                  and exists (
                      select 1 from follow_up_drafts
                      where id=?
                        and revision=?
                        and send_claim_revision=?
                        and send_claim_token=?
                        and status in ('draft', 'approved')
                  )
                """,
                (
                    lease_until,
                    draft_id,
                    claimed_revision,
                    claim_token,
                    lease_owner,
                    now,
                    draft_id,
                    claimed_revision,
                    claimed_revision,
                    claim_token,
                ),
            )
            return cursor.rowcount == 1

    def record_follow_up_sending_result(
        self,
        draft_id: int,
        *,
        draft_revision: int,
        claim_token: str,
        lease_owner: str,
        now: str,
        result_json: str,
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                update follow_up_send_attempts
                set result_json=?, updated_at=current_timestamp
                where draft_id=?
                  and draft_revision=?
                  and state='sending'
                  and claim_token=?
                  and lease_owner=?
                  and datetime(lease_until) >= datetime(?)
                """,
                (
                    result_json,
                    draft_id,
                    draft_revision,
                    claim_token,
                    lease_owner,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def apply_follow_up_late_send_result(
        self,
        *,
        attempt_id: int,
        draft_id: int,
        draft_revision: int,
        claim_token: str,
        idempotency_uuid: str,
        outcome: str,
        result_json: str,
        sent_at: str,
    ) -> dict[str, object]:
        if outcome not in {"sent", "failed"}:
            raise ValueError("late follow-up result must be sent or failed")
        with self._immediate_write_transaction() as db:
            row = db.execute(
                """
                select *
                from follow_up_send_attempts
                where id=?
                  and draft_id=?
                  and draft_revision=?
                  and claim_token=?
                  and idempotency_uuid=?
                """,
                (
                    attempt_id,
                    draft_id,
                    draft_revision,
                    claim_token,
                    idempotency_uuid,
                ),
            ).fetchone()
            if row is None:
                return {"outcome": "stale", "draft_finalized": False}

            state = str(row["state"])
            existing_conflict = str(row["conflict_json"] or "{}").strip()
            confirmed_sent = state == "sent"
            confirmed_failed = state in {"failed", "not_sent", "retryable"}
            contradictory = (confirmed_sent and outcome == "failed") or (
                confirmed_failed and outcome == "sent"
            )
            if existing_conflict != "{}":
                db.execute(
                    """
                    update follow_up_send_attempts
                    set late_result_json=?, updated_at=current_timestamp
                    where id=? and claim_token=? and idempotency_uuid=?
                    """,
                    (result_json, attempt_id, claim_token, idempotency_uuid),
                )
                return {"outcome": "conflict", "draft_finalized": False}
            if contradictory:
                conflict_json = json.dumps(
                    {
                        "existing_state": state,
                        "existing_result": json.loads(str(row["result_json"] or "{}")),
                        "late_outcome": outcome,
                        "late_result": json.loads(result_json),
                    },
                    ensure_ascii=False,
                )
                # A contradictory late provider result is retained as an
                # append-only conflict fact, but it must not reintroduce the
                # removed application-level ``unknown`` state machine.  Keep
                # the ordinary failed/retryable projection and let the next
                # normal revision decide whether another send is needed.
                db.execute(
                    """
                    update follow_up_send_attempts
                    set lease_owner='',
                        lease_until='',
                        late_result_json=?,
                        conflict_json=?,
                        updated_at=current_timestamp
                    where id=? and claim_token=? and idempotency_uuid=?
                    """,
                    (
                        result_json,
                        conflict_json,
                        attempt_id,
                        claim_token,
                        idempotency_uuid,
                    ),
                )
                return {"outcome": "conflict", "draft_finalized": False}

            if confirmed_sent or confirmed_failed:
                db.execute(
                    """
                    update follow_up_send_attempts
                    set late_result_json=?, updated_at=current_timestamp
                    where id=? and claim_token=? and idempotency_uuid=?
                    """,
                    (result_json, attempt_id, claim_token, idempotency_uuid),
                )
                return {
                    "outcome": f"equivalent_{outcome}",
                    "draft_finalized": False,
                }

            if state not in {"sending", "unknown"}:
                return {"outcome": "stale", "draft_finalized": False}

            next_state = "sent" if outcome == "sent" else "not_sent"
            db.execute(
                """
                update follow_up_send_attempts
                set state=?,
                    lease_owner='',
                    lease_until='',
                    late_result_json=?,
                    updated_at=current_timestamp
                where id=? and claim_token=? and idempotency_uuid=?
                """,
                (
                    next_state,
                    result_json,
                    attempt_id,
                    claim_token,
                    idempotency_uuid,
                ),
            )
            draft_finalized = False
            if outcome == "sent":
                draft = db.execute(
                    """
                    update follow_up_drafts
                    set status='sent',
                        send_result_json=?,
                        sent_at=?,
                        revision=revision+1,
                        send_claim_revision=0,
                        send_claim_token='',
                        send_claim_idempotency_uuid='',
                        updated_at=current_timestamp
                    where id=?
                      and revision=?
                      and send_claim_revision=?
                      and send_claim_token=?
                      and send_claim_idempotency_uuid=?
                    """,
                    (
                        result_json,
                        sent_at,
                        draft_id,
                        draft_revision,
                        draft_revision,
                        claim_token,
                        idempotency_uuid,
                    ),
                )
                draft_finalized = draft.rowcount == 1
            else:
                draft = db.execute(
                    """
                    update follow_up_drafts
                    set send_claim_revision=0,
                        send_claim_token='',
                        send_claim_idempotency_uuid='',
                        updated_at=current_timestamp
                    where id=?
                      and revision=?
                      and send_claim_revision=?
                      and send_claim_token=?
                      and send_claim_idempotency_uuid=?
                    """,
                    (
                        draft_id,
                        draft_revision,
                        draft_revision,
                        claim_token,
                        idempotency_uuid,
                    ),
                )
                if draft.rowcount == 1:
                    db.execute(
                        """
                        update follow_up_send_attempts
                        set state='retryable', updated_at=current_timestamp
                        where id=? and state='not_sent'
                        """,
                        (attempt_id,),
                    )
            return {
                "outcome": f"confirmed_{outcome}",
                "draft_finalized": draft_finalized,
            }

    def mark_follow_up_sending_retryable(
        self,
        draft_id: int,
        *,
        draft_revision: int,
        claim_token: str,
        lease_owner: str,
        result_json: str,
    ) -> bool:
        with self._immediate_write_transaction() as db:
            cursor = db.execute(
                """
                update follow_up_send_attempts
                set state='retryable',
                    result_json=?,
                    lease_owner='',
                    lease_until='',
                    updated_at=current_timestamp
                where draft_id=?
                  and draft_revision=?
                  and state='sending'
                  and claim_token=?
                  and lease_owner=?
                """,
                (
                    result_json,
                    draft_id,
                    draft_revision,
                    claim_token,
                    lease_owner,
                ),
            )
            if cursor.rowcount != 1:
                return False
            db.execute(
                """
                update follow_up_drafts
                set send_claim_revision=0,
                    send_claim_token='',
                    send_claim_idempotency_uuid='',
                    updated_at=current_timestamp
                where id=? and revision=? and send_claim_token=?
                """,
                (draft_id, draft_revision, claim_token),
            )
            return True

    def update_claimed_follow_up_draft(
        self,
        draft_id: int,
        *,
        claimed_revision: int,
        claim_token: str,
        lease_owner: str,
        now: str,
        attempt_state: str,
        attempt_result_json: str,
        **values,
    ) -> bool:
        if not values:
            return False
        allowed_columns = {
            "status",
            "send_result_json",
            "evidence_check_json",
            "reaction_status",
            "reaction_summary",
            "suppressed_reason",
            "scheduled_at",
            "sent_at",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        if not filtered:
            return False
        if filtered.get("status") == "sent" and "sent_at" not in filtered:
            filtered["sent_at"] = "__CURRENT_TIMESTAMP__"
        assignments = []
        parameters = []
        for key, value in filtered.items():
            if key == "sent_at" and value == "__CURRENT_TIMESTAMP__":
                assignments.append("sent_at=current_timestamp")
                continue
            assignments.append(f"{key}=?")
            parameters.append(value)
        with self._immediate_write_transaction() as db:
            attempt = db.execute(
                """
                update follow_up_send_attempts
                set state=?, result_json=?, updated_at=current_timestamp
                where draft_id=?
                  and draft_revision=?
                  and state='sending'
                  and claim_token=?
                  and lease_owner=?
                  and datetime(lease_until) >= datetime(?)
                """,
                (
                    attempt_state,
                    attempt_result_json,
                    draft_id,
                    claimed_revision,
                    claim_token,
                    lease_owner,
                    now,
                ),
            )
            if attempt.rowcount != 1:
                return False
            cursor = db.execute(
                f"""
                update follow_up_drafts
                set {', '.join(assignments)},
                    revision=revision+1,
                    send_claim_revision=0,
                    send_claim_token='',
                    send_claim_idempotency_uuid='',
                    updated_at=current_timestamp
                where id=?
                  and revision=?
                  and send_claim_revision=?
                  and send_claim_token=?
                """,
                [
                    *parameters,
                    draft_id,
                    claimed_revision,
                    claimed_revision,
                    claim_token,
                ],
            )
            return cursor.rowcount == 1

    def list_blocking_prior_follow_up_send_attempts(
        self,
        *,
        draft_id: int,
        before_revision: int,
    ) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select attempts.*,
                       coalesce(reviews.status, '') as review_status
                from follow_up_send_attempts as attempts
                left join work_summary_inputs as reviews
                  on reviews.source_type='follow_up_completion_check'
                 and reviews.source_ref=attempts.review_source_ref
                where attempts.draft_id=?
                  and attempts.draft_revision < ?
                  and (
                    attempts.state in ('claimed', 'sending')
                    or (
                      attempts.state='sent'
                      and coalesce(reviews.status, '') not in ('done', 'skipped')
                    )
                  )
                order by attempts.draft_revision, attempts.id
                """,
                (draft_id, before_revision),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_prior_follow_up_send_attempts(
        self,
        *,
        draft_id: int,
        before_revision: int,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from follow_up_send_attempts
                where draft_id=? and draft_revision < ?
                order by draft_revision desc, id desc
                limit ?
                """,
                (draft_id, before_revision, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def finalize_reviewed_current_follow_up_delivery(
        self,
        *,
        draft_id: int,
        draft_revision: int,
    ) -> bool:
        with self._immediate_write_transaction() as db:
            attempt = db.execute(
                """
                select attempts.result_json
                from follow_up_send_attempts as attempts
                join work_summary_inputs as reviews
                  on reviews.source_type='follow_up_completion_check'
                 and reviews.source_ref=attempts.review_source_ref
                where attempts.draft_id=?
                  and attempts.draft_revision=?
                  and attempts.state='sent'
                  and attempts.review_enqueued_revision=?
                  and attempts.review_source_ref!=''
                  and reviews.status='done'
                order by attempts.id desc
                limit 1
                """,
                (draft_id, draft_revision, draft_revision),
            ).fetchone()
            if attempt is None:
                return False
            cursor = db.execute(
                """
                update follow_up_drafts
                set status='sent',
                    send_result_json=?,
                    sent_at=current_timestamp,
                    revision=revision+1,
                    send_claim_revision=0,
                    send_claim_token='',
                    send_claim_idempotency_uuid='',
                    updated_at=current_timestamp
                where id=?
                  and revision=?
                  and status in ('draft', 'approved')
                  and send_claim_revision=0
                  and send_claim_token=''
                  and send_claim_idempotency_uuid=''
                """,
                (attempt["result_json"], draft_id, draft_revision),
            )
            return cursor.rowcount == 1

    def invalidate_expired_prior_follow_up_claim(
        self,
        *,
        draft_id: int,
        draft_revision: int,
        claim_token: str,
        current_revision: int,
        now: str,
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                update follow_up_send_attempts
                set state='invalidated',
                    lease_owner='',
                    lease_until='',
                    updated_at=current_timestamp
                where draft_id=?
                  and draft_revision=?
                  and claim_token=?
                  and state in ('claimed', 'sending')
                  and (lease_until='' or datetime(lease_until) <= datetime(?))
                  and exists (
                    select 1
                    from follow_up_drafts
                    where id=?
                      and revision=?
                      and revision > ?
                  )
                """,
                (
                    draft_id,
                    draft_revision,
                    claim_token,
                    now,
                    draft_id,
                    current_revision,
                    draft_revision,
                ),
            )
            return cursor.rowcount == 1

    def enqueue_follow_up_delivery_review(
        self,
        *,
        draft_id: int,
        draft_revision: int,
        claim_token: str,
        current_revision: int,
        source_type: str,
        source_ref: str,
        payload_json: str,
    ) -> bool:
        with self._immediate_write_transaction() as db:
            attempt = db.execute(
                """
                update follow_up_send_attempts
                set review_enqueued_revision=?,
                    review_source_ref=?,
                    updated_at=current_timestamp
                where draft_id=?
                  and draft_revision=?
                  and claim_token=?
                  and state='sent'
                  and (
                    review_enqueued_revision < ?
                    or (
                      review_enqueued_revision=?
                      and review_source_ref=?
                      and exists (
                        select 1
                        from work_summary_inputs
                        where source_type=?
                          and source_ref=?
                          and status='failed'
                      )
                    )
                  )
                """,
                (
                    current_revision,
                    source_ref,
                    draft_id,
                    draft_revision,
                    claim_token,
                    current_revision,
                    current_revision,
                    source_ref,
                    source_type,
                    source_ref,
                ),
            )
            if attempt.rowcount != 1:
                return False
            db.execute(
                """
                insert into work_summary_inputs (source_type, source_ref, payload_json)
                values (?, ?, ?)
                on conflict(source_type, source_ref) do update set
                    payload_json=excluded.payload_json,
                    status=case
                        when work_summary_inputs.status in ('failed', 'skipped')
                            then 'pending'
                        else work_summary_inputs.status
                    end,
                    error=case
                        when work_summary_inputs.status in ('failed', 'skipped')
                            then ''
                        else work_summary_inputs.error
                    end,
                    available_at=case
                        when work_summary_inputs.status in ('failed', 'skipped')
                            then ''
                        else work_summary_inputs.available_at
                    end,
                    updated_at=current_timestamp
                """,
                (source_type, source_ref, payload_json),
            )
            return True

    def list_failed_follow_up_delivery_reviews(
        self,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select attempts.*
                from follow_up_send_attempts as attempts
                join work_summary_inputs as reviews
                  on reviews.source_type='follow_up_completion_check'
                 and reviews.source_ref=attempts.review_source_ref
                join follow_up_drafts as drafts
                  on drafts.id=attempts.draft_id
                where attempts.state='sent'
                  and attempts.review_source_ref!=''
                  and reviews.status='failed'
                  and drafts.revision >= attempts.review_enqueued_revision
                order by attempts.draft_id, attempts.draft_revision, attempts.id
                limit ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_follow_up_send_attempt(
        self,
        *,
        draft_id: int,
        draft_revision: int,
    ) -> dict[str, object] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from follow_up_send_attempts
                where draft_id=? and draft_revision=?
                order by id desc
                limit 1
                """,
                (draft_id, draft_revision),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_follow_up_draft(
        self, draft_id: int, *, _db: sqlite3.Connection | None = None
    ) -> FollowUpDraft | None:
        if draft_id <= 0:
            return None
        with self._optional_connection(_db) as db:
            row = db.execute(
                "select * from follow_up_drafts where id=?",
                (draft_id,),
            ).fetchone()
            return None if row is None else FollowUpDraft.model_validate(dict(row))

    def list_follow_up_drafts(
        self,
        *,
        project_id: int | None = None,
        todo_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
        due_before: str | None = None,
        limit: int = 200,
    ) -> list[FollowUpDraft]:
        query = "select * from follow_up_drafts"
        clauses: list[str] = []
        args: list[str | int] = []
        if project_id is not None:
            clauses.append("project_id=?")
            args.append(project_id)
        if todo_id is not None:
            clauses.append("todo_id=?")
            args.append(todo_id)
        if statuses:
            clauses.append(f"status in ({','.join('?' for _ in statuses)})")
            args.extend(statuses)
        if due_before is not None:
            clauses.append("scheduled_at != '' and datetime(scheduled_at) <= datetime(?)")
            args.append(due_before)
        if clauses:
            query = f"{query} where {' and '.join(clauses)}"
        query = f"{query} order by scheduled_at, id limit ?"
        args.append(limit)
        with self._connect() as db:
            return [
                FollowUpDraft.model_validate(dict(row))
                for row in db.execute(query, args)
            ]

    def list_follow_up_drafts_for_todo(
        self,
        todo_id: int,
        *,
        statuses: tuple[str, ...] = ("draft", "approved"),
        _db: sqlite3.Connection | None = None,
    ) -> list[FollowUpDraft]:
        query = "select * from follow_up_drafts where todo_id=?"
        args: list[str | int] = [todo_id]
        if statuses:
            query = f"{query} and status in ({','.join('?' for _ in statuses)})"
            args.extend(statuses)
        query = f"{query} order by scheduled_at, id"
        with self._optional_connection(_db) as db:
            return [
                FollowUpDraft.model_validate(dict(row))
                for row in db.execute(query, args)
            ]

    def list_recent_follow_up_candidates(
        self,
        *,
        conversation_id: str = "",
        owner_user_id: str = "",
        since: str,
        limit: int = 20,
    ) -> list[RecentFollowUpCandidate]:
        conversation_id = conversation_id.strip()
        owner_user_id = owner_user_id.strip()
        if not since.strip() or (not conversation_id and not owner_user_id):
            return []
        if limit <= 0:
            return []
        owner_expr = """
            coalesce(
                nullif(f.owner_user_id, ''),
                nullif(t.owner_user_id, ''),
                nullif(p.owner_user_id, ''),
                ''
            )
        """
        owner_name_expr = """
            coalesce(
                nullif(f.owner_name, ''),
                nullif(t.owner_name, ''),
                nullif(p.owner_name, ''),
                ''
            )
        """
        recency_expr = """
            coalesce(
                nullif(f.sent_at, ''),
                nullif(f.scheduled_at, ''),
                f.created_at
            )
        """
        clauses = [
            "f.status in ('sent', 'draft', 'approved')",
            f"{recency_expr} >= ?",
        ]
        args: list[object] = [since.strip()]
        match_clauses: list[str] = []
        if conversation_id:
            match_clauses.append("f.target_conversation_id=?")
            args.append(conversation_id)
        if owner_user_id:
            match_clauses.append(f"{owner_expr}=?")
            args.append(owner_user_id)
        if match_clauses:
            clauses.append(f"({' or '.join(match_clauses)})")
        args.extend(
            [
                conversation_id,
                conversation_id,
                owner_user_id,
                owner_user_id,
                limit,
            ]
        )
        with self._connect() as db:
            rows = db.execute(
                f"""
                select
                    f.id as follow_up_id,
                    f.project_id,
                    coalesce(p.title, '') as project_title,
                    coalesce(p.status, '') as project_status,
                    coalesce(p.priority, '') as project_priority,
                    coalesce(p.risk_level, '') as project_risk_level,
                    f.todo_id,
                    coalesce(t.title, '') as todo_title,
                    coalesce(t.status, '') as todo_status,
                    coalesce(t.priority, '') as todo_priority,
                    coalesce(t.deadline_at, '') as todo_deadline_at,
                    coalesce(t.next_follow_up_at, '') as todo_next_follow_up_at,
                    {owner_expr} as owner_user_id,
                    {owner_name_expr} as owner_name,
                    f.target_conversation_id,
                    f.target_kind,
                    f.question_text,
                    f.scheduled_at,
                    f.sent_at,
                    f.status,
                    f.reaction_status,
                    f.reaction_summary,
                    f.suppressed_reason,
                    f.evidence_check_json,
                    f.risk_check_json,
                    f.send_result_json
                from follow_up_drafts f
                left join work_projects p on p.id=f.project_id
                left join work_todos t on t.id=f.todo_id
                where {' and '.join(clauses)}
                order by
                    case
                        when ? != '' and f.target_conversation_id=? then 0
                        else 1
                    end,
                    case
                        when ? != '' and {owner_expr}=? then 0
                        else 1
                    end,
                    {recency_expr} desc,
                    f.id desc
                limit ?
                """,
                args,
            ).fetchall()
            return [RecentFollowUpCandidate.model_validate(dict(row)) for row in rows]

    @staticmethod
    def _follow_up_dedupe_key(values: dict[str, object]) -> str:
        parts = [
            str(values.get("project_id") or ""),
            str(values.get("todo_id") or ""),
            str(values.get("owner_user_id") or "").strip(),
            str(values.get("target_conversation_id") or "").strip(),
            str(values.get("target_kind") or "").strip(),
            " ".join(str(values.get("question_text") or "").split()),
        ]
        raw_key = "\n".join(parts)
        if not raw_key.strip():
            return ""
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def count_sent_follow_ups_for_owner_since(
        self,
        owner_user_id: str,
        since: str,
    ) -> int:
        if not owner_user_id.strip():
            return 0
        with self._connect() as db:
            row = db.execute(
                """
                select count(*) as count
                from follow_up_drafts
                where status='sent'
                  and owner_user_id=?
                  and sent_at != ''
                  and datetime(sent_at) >= datetime(?)
                """,
                (owner_user_id.strip(), since),
            ).fetchone()
            return int(row["count"] or 0)

    def count_sent_follow_ups_for_conversation_since(
        self,
        conversation_id: str,
        since: str,
    ) -> int:
        if not conversation_id.strip():
            return 0
        with self._connect() as db:
            row = db.execute(
                """
                select count(*) as count
                from follow_up_drafts
                where status='sent'
                  and target_conversation_id=?
                  and sent_at != ''
                  and datetime(sent_at) >= datetime(?)
                """,
                (conversation_id.strip(), since),
            ).fetchone()
            return int(row["count"] or 0)

    def list_recent_reply_attempts_for_follow_up(
        self,
        *,
        conversation_id: str,
        since: str,
        limit: int = 20,
    ) -> list[ReplyAttempt]:
        if not conversation_id.strip() or not since.strip():
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_attempts
                where conversation_id=?
                  and datetime(created_at) >= datetime(?)
                order by created_at asc, id asc
                limit ?
                """,
                (conversation_id.strip(), since.strip(), limit),
            ).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

    def list_recent_follow_up_reactions(
        self,
        *,
        project_id: int,
        owner_user_id: str,
        since: str,
        limit: int = 10,
    ) -> list[FollowUpDraft]:
        clauses = [
            "project_id=?",
            "reaction_status != ''",
            "sent_at != ''",
            "datetime(sent_at) >= datetime(?)",
        ]
        args: list[object] = [project_id, since]
        if owner_user_id.strip():
            clauses.append("owner_user_id=?")
            args.append(owner_user_id.strip())
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from follow_up_drafts
                where {' and '.join(clauses)}
                order by sent_at desc, id desc
                limit ?
                """,
                args,
            ).fetchall()
            return [FollowUpDraft.model_validate(dict(row)) for row in rows]

    def list_sent_follow_ups_since(
        self,
        since: str,
        *,
        limit: int = 100,
    ) -> list[FollowUpDraft]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from follow_up_drafts
                where status='sent'
                  and sent_at != ''
                  and datetime(sent_at) >= datetime(?)
                order by sent_at desc, id desc
                limit ?
                """,
                (since, limit),
            ).fetchall()
            return [FollowUpDraft.model_validate(dict(row)) for row in rows]

    def list_sent_todo_records(self, *, limit: int = 5000) -> list[SentTodoRecord]:
        if limit <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from (
                    select
                        'dingtalk_todo' as kind,
                        links.id as source_id,
                        coalesce(nullif(links.last_push_at, ''), links.created_at) as sent_at,
                        links.status as status,
                        coalesce(nullif(todos.title, ''), links.title_snapshot, '') as title,
                        coalesce(todos.description, '') as description,
                        links.executor_user_id as owner_user_id,
                        links.executor_name as owner_name,
                        case
                            when trim(coalesce(links.executor_user_id, '')) != ''
                            then json_array(json_object(
                                'user_id', links.executor_user_id,
                                'name', links.executor_name,
                                'role', 'owner'
                            ))
                            else '[]'
                        end as owners_json,
                        coalesce(projects.id, 0) as project_id,
                        coalesce(projects.title, '') as project_title,
                        coalesce(todos.id, 0) as todo_id,
                        coalesce(todos.title, '') as todo_title,
                        coalesce(todos.description, '') as todo_description,
                        coalesce(nullif(links.title_snapshot, ''), todos.title, '') as original_text,
                        coalesce(nullif(links.deadline_at_snapshot, ''), todos.deadline_at, '') as deadline_at,
                        coalesce(nullif(links.priority_snapshot, ''), todos.priority, '') as priority,
                        coalesce(projects.tags_json, '[]') as tags_json,
                        coalesce(projects.related_people_json, '[]') as participants_json,
                        '[]' as files_json,
                        '' as target_kind,
                        '' as target_conversation_id,
                        links.dingtalk_task_id as external_id,
                        links.last_error as detail
                    from work_todo_dingtalk_links links
                    left join work_todos todos on todos.id=links.work_todo_id
                    left join work_projects projects on projects.id=todos.project_id
                    where trim(coalesce(links.dingtalk_task_id, '')) != ''
                    union all
                    select
                        'follow_up' as kind,
                        drafts.id as source_id,
                        coalesce(nullif(drafts.sent_at, ''), drafts.updated_at, drafts.created_at) as sent_at,
                        drafts.status as status,
                        coalesce(nullif(drafts.title, ''), todos.title, '') as title,
                        coalesce(nullif(drafts.description, ''), todos.description, '') as description,
                        drafts.owner_user_id as owner_user_id,
                        drafts.owner_name as owner_name,
                        coalesce(nullif(drafts.owners_json, ''), '[]') as owners_json,
                        coalesce(projects.id, 0) as project_id,
                        coalesce(projects.title, '') as project_title,
                        coalesce(todos.id, 0) as todo_id,
                        coalesce(todos.title, '') as todo_title,
                        coalesce(todos.description, '') as todo_description,
                        drafts.question_text as original_text,
                        coalesce(todos.deadline_at, '') as deadline_at,
                        coalesce(nullif(drafts.priority, ''), todos.priority, '') as priority,
                        coalesce(nullif(drafts.tags_json, ''), projects.tags_json, '[]') as tags_json,
                        coalesce(nullif(drafts.participants_json, ''), projects.related_people_json, '[]') as participants_json,
                        coalesce(nullif(drafts.files_json, ''), '[]') as files_json,
                        drafts.target_kind as target_kind,
                        drafts.target_conversation_id as target_conversation_id,
                        '' as external_id,
                        drafts.send_result_json as detail
                    from follow_up_drafts drafts
                    left join work_todos todos on todos.id=drafts.todo_id
                    left join work_projects projects on projects.id=drafts.project_id
                    where drafts.status='sent'
                      and trim(coalesce(drafts.sent_at, '')) != ''
                )
                order by datetime(sent_at) desc, source_id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
            return [SentTodoRecord.model_validate(dict(row)) for row in rows]

    def set_daily_scan_state(
        self,
        scanner_name: str,
        last_success_at: str,
        cursor_json: str = "{}",
        last_error: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into daily_scan_state (
                    scanner_name,
                    last_success_at,
                    cursor_json,
                    last_error
                )
                values (?, ?, ?, ?)
                on conflict(scanner_name) do update set
                    last_success_at=excluded.last_success_at,
                    cursor_json=excluded.cursor_json,
                    last_error=excluded.last_error,
                    updated_at=current_timestamp
                """,
                (scanner_name, last_success_at, cursor_json, last_error),
            )

    def get_daily_scan_state(self, scanner_name: str) -> dict[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select scanner_name, last_success_at, cursor_json, last_error
                from daily_scan_state
                where scanner_name=?
                """,
                (scanner_name,),
            ).fetchone()
            return None if row is None else dict(row)

    def record_error(
        self,
        conversation_id: str | None,
        message_id: str | None,
        kind: str,
        detail: str,
    ) -> None:
        with self._immediate_write_transaction() as db:
            db.execute(
                """
                insert into errors (conversation_id, message_id, kind, detail)
                values (?, ?, ?, ?)
                """,
                (conversation_id, message_id, kind, detail),
            )

    def list_errors(
        self, limit: int | None = None, offset: int = 0
    ) -> list[ReplyError]:
        with self._connect() as db:
            query = """
                select *
                from errors
                order by id desc
            """
            args: tuple[int, ...] = ()
            if limit is not None:
                query = f"{query} limit ? offset ?"
                args = (limit, max(0, offset))
            rows = db.execute(query, args).fetchall()
            return [ReplyError.model_validate(dict(row)) for row in rows]

    def get_error(self, error_id: int) -> ReplyError | None:
        with self._connect() as db:
            row = db.execute(
                "select * from errors where id=?",
                (error_id,),
            ).fetchone()
            return None if row is None else ReplyError.model_validate(dict(row))

    def list_errors_after(self, error_id: int) -> list[ReplyError]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from errors
                where id > ?
                order by id asc
                """,
                (error_id,),
            ).fetchall()
            return [ReplyError.model_validate(dict(row)) for row in rows]

    def resolve_errors(self, error_ids: list[int], *, resolution: str) -> int:
        """Close verified historical incidents without removing their audit rows."""
        unique_ids = sorted({error_id for error_id in error_ids if error_id > 0})
        if not unique_ids:
            return 0
        if not resolution.strip():
            raise ValueError("error resolution must be non-empty")
        placeholders = ",".join("?" for _ in unique_ids)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                update errors
                set resolved_at=current_timestamp,
                    resolution=?
                where id in ({placeholders})
                  and resolved_at=''
                """,
                (resolution.strip(), *unique_ids),
            )
            return cursor.rowcount

    def redact_and_resolve_error(
        self,
        error_id: int,
        *,
        detail: str,
        resolution: str,
    ) -> bool:
        """Replace an unsafe historical detail while retaining its audit outcome."""
        if error_id <= 0:
            return False
        if not detail.strip() or not resolution.strip():
            raise ValueError("redacted detail and resolution must be non-empty")
        with self._connect() as db:
            cursor = db.execute(
                """
                update errors
                set detail=?,
                    resolved_at=current_timestamp,
                    resolution=?
                where id=? and coalesce(resolved_at, '')=''
                """,
                (detail.strip(), resolution.strip(), error_id),
            )
            return cursor.rowcount == 1

    def resolve_errors_recovered_by_reply_attempts(self) -> int:
        """Close errors whose trigger has a later verified terminal outcome."""
        terminal_statuses = (
            "calendar",
            "commented",
            "completed",
            "document",
            "reacted",
            "sent",
            "skipped",
        )
        placeholders = ",".join("?" for _ in terminal_statuses)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                update errors as error_event
                set resolved_at=current_timestamp,
                    resolution='recovered by later terminal reply attempt'
                where coalesce(error_event.resolved_at, '')=''
                  and coalesce(error_event.conversation_id, '')<>''
                  and coalesce(error_event.message_id, '')<>''
                  and exists (
                    select 1
                    from reply_attempts recovery
                    where recovery.conversation_id=error_event.conversation_id
                      and recovery.trigger_message_id=error_event.message_id
                      and datetime(recovery.updated_at) >= datetime(error_event.created_at)
                      and lower(recovery.send_status) in ({placeholders})
                  )
                """,
                terminal_statuses,
            )
            return cursor.rowcount

    def resolve_errors_recovered_by_completed_reply_tasks(self) -> int:
        """Close a trigger error once its own task completed after the error.

        A completed task is durable evidence that the current generation
        reached a terminal workflow state.  This is intentionally narrower
        than a time-based observation: it does not infer message delivery or
        an external write, and it keeps errors open while any linked agent run
        still has an unknown side effect.
        """
        with self._connect() as db:
            cursor = db.execute(
                """
                update errors as error_event
                set resolved_at=current_timestamp,
                    resolution='recovered by completed reply task'
                where coalesce(error_event.resolved_at, '')=''
                  and coalesce(error_event.conversation_id, '')<>''
                  and coalesce(error_event.message_id, '')<>''
                  and exists (
                    select 1
                    from reply_tasks task
                    where task.conversation_id=error_event.conversation_id
                      and task.trigger_message_id=error_event.message_id
                      and lower(task.status)='done'
                      and datetime(task.updated_at) >= datetime(error_event.created_at)
                      and not exists (
                        select 1
                        from agent_runs run
                        where run.reply_task_id=task.id
                          and (
                            lower(run.status)='unknown'
                            or lower(run.side_effect_state)='unknown'
                          )
                      )
                  )
                """
            )
            return cursor.rowcount

    def resolve_errors_recovered_by_terminal_work_summary_inputs(self) -> int:
        """Close a work-item incident only after that exact input is terminal."""
        with self._connect() as db:
            cursor = db.execute(
                """
                update errors as incident
                set resolved_at=current_timestamp,
                    resolution='recovered by terminal work summary input'
                where coalesce(incident.resolved_at, '')=''
                  and incident.kind='task_agent'
                  and incident.conversation_id='work_summary_input'
                  and exists (
                      select 1
                      from work_summary_inputs as work_input
                      where cast(work_input.id as text)=incident.message_id
                        and lower(work_input.status) in ('done', 'skipped')
                        and datetime(work_input.updated_at) >=
                            datetime(incident.created_at)
                  )
                """
            )
            return cursor.rowcount

    def resolve_closed_blocked_reply_attempts(self) -> int:
        """Close latest blocked attempts that have no remaining recovery work.

        The task completion proves the workflow has reached a terminal state;
        it does not turn a skipped external action into a successful one.  The
        original business explanation remains in the immutable audit summary.
        """
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_attempts as attempt
                set send_status='skipped',
                    send_error='',
                    permission_action=?,
                    permission_reason='关联任务已完成；没有待恢复或未知副作用。详见审计说明。',
                    updated_at=current_timestamp
                where lower(attempt.send_status)='blocked'
                  and trim(coalesce(attempt.permission_action, ''))=''
                  and attempt.id=(
                    select max(latest.id)
                    from reply_attempts latest
                    where latest.channel=attempt.channel
                      and latest.conversation_id=attempt.conversation_id
                      and latest.trigger_message_id=attempt.trigger_message_id
                  )
                  and exists (
                    select 1
                    from reply_tasks task
                    where task.channel=attempt.channel
                      and task.conversation_id=attempt.conversation_id
                      and task.trigger_message_id=attempt.trigger_message_id
                      and lower(task.status)='done'
                      and not exists (
                        select 1
                        from agent_runs run
                        where run.reply_task_id=task.id
                          and (
                            lower(run.status)='unknown'
                            or lower(run.side_effect_state)='unknown'
                          )
                      )
                  )
                """,
                (REPLY_ATTEMPT_CLOSED_AFTER_REVIEW,),
            )
            return cursor.rowcount

    def resolve_unattributed_errors_after_quiet_period(
        self,
        *,
        now: datetime | None = None,
        quiet_period_seconds: int = ERROR_RECOVERY_QUIET_PERIOD_SECONDS,
    ) -> int:
        """Close service incidents after a clean observation window.

        These records have no trigger message identity, so a later reply cannot
        prove recovery. They are retained, but another open error of the same
        component within the observation window keeps them active.
        """
        if quiet_period_seconds <= 0:
            raise ValueError("quiet period must be positive")
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff_text = (observed_at - timedelta(seconds=quiet_period_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._connect() as db:
            cursor = db.execute(
                """
                update errors as incident
                set resolved_at=current_timestamp,
                    resolution='no recurrence during the four-hour healthy observation window'
                where coalesce(incident.resolved_at, '')=''
                  and trim(coalesce(incident.message_id, ''))=''
                  and datetime(incident.created_at) < datetime(?)
                  and not exists (
                    select 1
                    from errors newer
                    where newer.kind=incident.kind
                      and coalesce(newer.resolved_at, '')=''
                      and datetime(newer.created_at) >= datetime(?)
                  )
                """,
                (cutoff_text, cutoff_text),
            )
            return cursor.rowcount

    def resolve_inactive_trigger_errors_after_quiet_period(
        self,
        *,
        now: datetime | None = None,
        quiet_period_seconds: int = ERROR_RECOVERY_QUIET_PERIOD_SECONDS,
    ) -> int:
        """Close historical trigger incidents once no recovery work remains.

        This is incident convergence, not a delivery claim. A trigger error is
        eligible only after the observation window has passed without a newer
        error for that trigger, with no active task, unresolved latest attempt,
        or linked agent run whose side effect is unknown.
        """
        if quiet_period_seconds <= 0:
            raise ValueError("quiet period must be positive")
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff_text = (observed_at - timedelta(seconds=quiet_period_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        unresolved_attempt_statuses = (
            "blocked",
            "dry_run",
            "failed",
            "needs_human",
            "pending",
            "pending_reconciliation",
            "processing",
        )
        task_statuses = ("failed", "pending", "processing")
        attempt_placeholders = ",".join("?" for _ in unresolved_attempt_statuses)
        task_placeholders = ",".join("?" for _ in task_statuses)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                update errors as incident
                set resolved_at=current_timestamp,
                    resolution='no active workflow during the four-hour healthy observation window'
                where coalesce(incident.resolved_at, '')=''
                  and trim(coalesce(incident.conversation_id, ''))<>''
                  and trim(coalesce(incident.message_id, ''))<>''
                  and datetime(incident.created_at) < datetime(?)
                  and not exists (
                    select 1
                    from errors newer
                    where newer.conversation_id=incident.conversation_id
                      and newer.message_id=incident.message_id
                      and coalesce(newer.resolved_at, '')=''
                      and datetime(newer.created_at) >= datetime(?)
                  )
                  and not exists (
                    select 1
                    from reply_tasks task
                    where task.conversation_id=incident.conversation_id
                      and task.trigger_message_id=incident.message_id
                      and lower(task.status) in ({task_placeholders})
                  )
                  and not exists (
                    select 1
                    from reply_attempts attempt
                    where attempt.conversation_id=incident.conversation_id
                      and attempt.trigger_message_id=incident.message_id
                      and attempt.id=(
                        select max(latest.id)
                        from reply_attempts latest
                        where latest.channel=attempt.channel
                          and latest.conversation_id=attempt.conversation_id
                          and latest.trigger_message_id=attempt.trigger_message_id
                      )
                      and lower(attempt.send_status) in ({attempt_placeholders})
                  )
                  and not exists (
                    select 1
                    from reply_tasks task
                    join agent_runs run on run.reply_task_id=task.id
                    where task.conversation_id=incident.conversation_id
                      and task.trigger_message_id=incident.message_id
                      and (
                        lower(run.status)='unknown'
                        or lower(run.side_effect_state)='unknown'
                      )
                  )
                """,
                (
                    cutoff_text,
                    cutoff_text,
                    *task_statuses,
                    *unresolved_attempt_statuses,
                ),
            )
            return cursor.rowcount

    def count_sent_replies(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "select count(*) as count from sent_replies"
            ).fetchone()
            return int(row["count"])

    def max_reply_attempt_id(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "select coalesce(max(id), 0) as max_id from reply_attempts"
            ).fetchone()
            return int(row["max_id"])

    def max_sent_reply_id(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "select coalesce(max(id), 0) as max_id from sent_replies"
            ).fetchone()
            return int(row["max_id"])

    def max_error_id(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "select coalesce(max(id), 0) as max_id from errors"
            ).fetchone()
            return int(row["max_id"])

    def count_errors(self) -> int:
        with self._connect() as db:
            row = db.execute("select count(*) as count from errors").fetchone()
            return int(row["count"])

    def list_operation_logs(
        self,
        limit: int | None = None,
        offset: int = 0,
        query: str = "",
        log_type: str = "",
    ) -> list[OperationLog]:
        sql = self._operation_logs_base_query()
        where_sql, where_args = self._operation_log_filters(query=query, log_type=log_type)
        sql = f"""
            {sql}
            {where_sql}
            order by occurred_at desc, source_table desc, source_id desc
        """
        args: list[object] = [*where_args]
        if limit is not None:
            sql = f"{sql} limit ? offset ?"
            args.extend([limit, max(0, offset)])
        with self._connect() as db:
            rows = db.execute(sql, tuple(args)).fetchall()
            return [OperationLog.model_validate(dict(row)) for row in rows]

    def list_operation_log_types(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                f"""
                select distinct category
                from ({self._operation_logs_base_query()})
                order by category asc
                """
            ).fetchall()
            return [str(row["category"]) for row in rows if row["category"]]

    def count_operation_logs(self, query: str = "", log_type: str = "") -> int:
        where_sql, where_args = self._operation_log_filters(
            query=query,
            log_type=log_type,
        )
        with self._connect() as db:
            row = db.execute(
                f"""
                select count(*) as count
                from ({self._operation_logs_base_query()} {where_sql})
                """,
                tuple(where_args),
            ).fetchone()
            return int(row["count"] or 0)

    def _operation_logs_base_query(self) -> str:
        return """
            select *
            from (
                select
                    'error:' || id as id,
                    'errors' as source_table,
                    id as source_id,
                    created_at as occurred_at,
                    'Error' as category,
                    kind as action,
                    case
                        when coalesce(resolved_at, '')<>'' then
                            'resolved: ' || coalesce(nullif(resolution, ''), 'verified recovery')
                        else 'active'
                    end as status,
                    coalesce(conversation_id, '') as context,
                    detail as summary,
                    case when coalesce(resolved_at, '')='' then detail
                         else detail || char(10) || 'Resolved: ' || resolution end as detail,
                    coalesce(conversation_id, '') as conversation_id,
                    coalesce(message_id, '') as message_id
                from errors
                union all
                select
                    'reply-task:' || id as id,
                    'reply_tasks' as source_table,
                    id as source_id,
                    updated_at as occurred_at,
                    'Reply task' as category,
                    status as action,
                    status as status,
                    conversation_title as context,
                    trigger_text as summary,
                    error as detail,
                    conversation_id as conversation_id,
                    trigger_message_id as message_id
                from reply_tasks
                union all
                select
                    'reply:' || id as id,
                    'reply_attempts' as source_table,
                    id as source_id,
                    updated_at as occurred_at,
                    'Reply' as category,
                    action as action,
                    send_status as status,
                    conversation_title as context,
                    trigger_text as summary,
                    send_error as detail,
                    conversation_id as conversation_id,
                    trigger_message_id as message_id
                from reply_attempts
                union all
                select
                    'task-input:' || id as id,
                    'work_summary_inputs' as source_table,
                    id as source_id,
                    updated_at as occurred_at,
                    'Task input' as category,
                    source_type || ':' || source_ref as action,
                    status as status,
                    source_type || ':' || source_ref as context,
                    payload_json as summary,
                    error as detail,
                    '' as conversation_id,
                    '' as message_id
                from work_summary_inputs
                union all
                select
                    'task-update:' || id as id,
                    'work_updates' as source_table,
                    id as source_id,
                    created_at as occurred_at,
                    'Task update' as category,
                    source_type || ':' || source_ref as action,
                    'done' as status,
                    'project #' || project_id as context,
                    summary as summary,
                    changes_json as detail,
                    '' as conversation_id,
                    '' as message_id
                from work_updates
                union all
                select
                    'follow-up:' || id as id,
                    'follow_up_drafts' as source_table,
                    id as source_id,
                    coalesce(nullif(sent_at, ''), created_at) as occurred_at,
                    'Follow-up' as category,
                    target_kind as action,
                    status as status,
                    'project #' || project_id || ' todo #' || todo_id as context,
                    question_text as summary,
                    send_result_json as detail,
                    target_conversation_id as conversation_id,
                    '' as message_id
                from follow_up_drafts
                union all
                select
                    'dingtalk-todo:' || id as id,
                    'work_todo_dingtalk_links' as source_table,
                    id as source_id,
                    updated_at as occurred_at,
                    'DingTalk Todo' as category,
                    dingtalk_task_id as action,
                    status as status,
                    'work_todo #' || work_todo_id || ' dingtalk #' || dingtalk_task_id as context,
                    title_snapshot as summary,
                    last_error as detail,
                    '' as conversation_id,
                    '' as message_id
                from work_todo_dingtalk_links
            )
        """

    def _operation_log_filters(self, query: str = "", log_type: str = "") -> tuple[str, list[object]]:
        filters: list[str] = []
        args: list[object] = []
        if log_type.strip():
            filters.append("category = ?")
            args.append(log_type.strip())
        if query.strip():
            needle = f"%{query.strip().lower()}%"
            filters.append(
                """(
                    lower(coalesce(id, '')) like ?
                    or lower(coalesce(category, '')) like ?
                    or lower(coalesce(action, '')) like ?
                    or lower(coalesce(status, '')) like ?
                    or lower(coalesce(context, '')) like ?
                    or lower(coalesce(summary, '')) like ?
                    or lower(coalesce(detail, '')) like ?
                )"""
            )
            args.extend([needle] * 7)
        if not filters:
            return "", args
        return "where " + " and ".join(filters), args

    def set_service_state(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into service_state (key, value, updated_at)
                values (?, ?, current_timestamp)
                on conflict(key) do update set
                    value=excluded.value,
                    updated_at=current_timestamp
                """,
                (key, value),
            )

    def active_codex_capacity_pause(self, *, now: datetime) -> str:
        """Return the shared retry timestamp while a Codex capacity pause is active."""
        raw = self.get_service_state(CODEX_CAPACITY_PAUSE_STATE_KEY)
        if not raw:
            return ""
        try:
            value = json.loads(raw)
            retry_at = str(value.get("retry_at") or "")
            retry_time = datetime.fromisoformat(retry_at)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return ""
        if retry_time.tzinfo is None:
            retry_time = retry_time.replace(tzinfo=timezone.utc)
        current = now.astimezone(timezone.utc)
        return retry_at if retry_time.astimezone(timezone.utc) > current else ""

    def codex_capacity_failure_count(self) -> int:
        raw = self.get_service_state(CODEX_CAPACITY_PAUSE_STATE_KEY)
        if not raw:
            return 0
        try:
            value = json.loads(raw)
            return max(int(value.get("failure_count") or 0), 0)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return 0

    def clear_codex_capacity_pause(self) -> None:
        with self._connect() as db:
            db.execute(
                "delete from service_state where key=?",
                (CODEX_CAPACITY_PAUSE_STATE_KEY,),
            )

    def open_codex_capacity_pause(self, *, retry_at: str, now: datetime) -> bool:
        """Persist one shared capacity incident and report whether it is new."""
        retry_time = datetime.fromisoformat(retry_at)
        if retry_time.tzinfo is None:
            retry_time = retry_time.replace(tzinfo=timezone.utc)
        current = now.astimezone(timezone.utc)
        if retry_time.astimezone(timezone.utc) <= current:
            raise ValueError("codex capacity retry_at must be in the future")
        with self._immediate_write_transaction() as db:
            row = db.execute(
                "select value from service_state where key=?",
                (CODEX_CAPACITY_PAUSE_STATE_KEY,),
            ).fetchone()
            active = False
            if row is not None:
                try:
                    previous = json.loads(row["value"])
                    previous_at = datetime.fromisoformat(
                        str(previous.get("retry_at") or "")
                    )
                    if previous_at.tzinfo is None:
                        previous_at = previous_at.replace(tzinfo=timezone.utc)
                    active = previous_at.astimezone(timezone.utc) > current
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    active = False
            if active:
                return False
            failure_count = 1
            if row is not None:
                try:
                    previous = json.loads(row["value"])
                    failure_count = max(
                        int(previous.get("failure_count") or 0) + 1,
                        1,
                    )
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    failure_count = 1
            db.execute(
                """
                insert into service_state (key, value, updated_at)
                values (?, ?, current_timestamp)
                on conflict(key) do update set
                    value=excluded.value,
                    updated_at=current_timestamp
                """,
                (
                    CODEX_CAPACITY_PAUSE_STATE_KEY,
                    json.dumps(
                        {
                            "reason_code": "workspace_credits_exhausted",
                            "retry_at": retry_at,
                            "failure_count": failure_count,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            return True

    def claim_channel_login_request(
        self,
        *,
        channel: str,
        reason_code: str,
        now: datetime,
        suppression_seconds: int,
        reservation_owner: str,
    ) -> tuple[bool, dict[str, object]]:
        now_utc = now.astimezone(timezone.utc)
        key = f"channel_login_request:{channel}"
        with self._immediate_write_transaction() as db:
            row = db.execute(
                "select value from service_state where key=?",
                (key,),
            ).fetchone()
            state = self._channel_login_state(row["value"] if row else None)
            reservation = db.execute(
                """
                select reservation_owner, reserved_at
                from channel_login_reservations
                where channel=?
                """,
                (channel,),
            ).fetchone()
            started_at = state.get("started_at")
            if self._channel_login_timestamp_is_recent(
                started_at,
                now_utc,
                suppression_seconds,
            ) or (
                reservation is not None
                and self._channel_login_timestamp_is_recent(
                    reservation["reserved_at"],
                    now_utc,
                    suppression_seconds,
                )
            ):
                return False, state

            reserved_state = {
                **{
                    field: value
                    for field, value in state.items()
                    if field not in {"pid", "exited_at"}
                },
                "status": "starting",
                "reason_code": reason_code,
                "started_at": now_utc.isoformat(),
                "checked_at": now_utc.isoformat(),
            }
            db.execute(
                """
                insert into channel_login_reservations (
                    channel, reservation_owner, reserved_at
                ) values (?, ?, ?)
                on conflict(channel) do update set
                    reservation_owner=excluded.reservation_owner,
                    reserved_at=excluded.reserved_at
                """,
                (channel, reservation_owner, now_utc.isoformat()),
            )
            self._set_service_state_in_transaction(db, key, reserved_state)
            return True, reserved_state

    def update_claimed_channel_login_request(
        self,
        *,
        channel: str,
        reservation_owner: str,
        state: dict[str, object],
    ) -> bool:
        key = f"channel_login_request:{channel}"
        with self._immediate_write_transaction() as db:
            reservation = db.execute(
                """
                select reservation_owner
                from channel_login_reservations
                where channel=?
                """,
                (channel,),
            ).fetchone()
            if (
                reservation is None
                or reservation["reservation_owner"] != reservation_owner
            ):
                return False
            row = db.execute(
                "select value from service_state where key=?",
                (key,),
            ).fetchone()
            current = self._channel_login_state(row["value"] if row else None)
            self._set_service_state_in_transaction(db, key, {**current, **state})
            db.execute(
                "delete from channel_login_reservations where channel=?",
                (channel,),
            )
            return True

    @staticmethod
    def _channel_login_state(raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _channel_login_timestamp_is_recent(
        value: object,
        now: datetime,
        suppression_seconds: int,
    ) -> bool:
        if not isinstance(value, str):
            return False
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age_seconds = (now - timestamp.astimezone(timezone.utc)).total_seconds()
        return 0 <= age_seconds < suppression_seconds

    @staticmethod
    def _set_service_state_in_transaction(
        db: sqlite3.Connection,
        key: str,
        state: dict[str, object],
    ) -> None:
        safe_fields = {
            "status",
            "reason_code",
            "started_at",
            "checked_at",
            "exited_at",
            "pid",
        }
        safe_state = {
            field: value for field, value in state.items() if field in safe_fields
        }
        db.execute(
            """
            insert into service_state (key, value, updated_at)
            values (?, ?, current_timestamp)
            on conflict(key) do update set
                value=excluded.value,
                updated_at=current_timestamp
            """,
            (key, json.dumps(safe_state, ensure_ascii=False, sort_keys=True)),
        )

    def get_service_state(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "select value from service_state where key=?",
                (key,),
            ).fetchone()
            return None if row is None else row["value"]

    def upsert_setup_wizard_step(
        self,
        *,
        step_id: str,
        status: str,
        summary: str,
        manual_confirmed_by: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into setup_wizard_steps (
                    step_id,
                    status,
                    summary,
                    manual_confirmed_at,
                    manual_confirmed_by
                )
                values (?, ?, ?, case when ? != '' then current_timestamp else '' end, ?)
                on conflict(step_id) do update set
                    status=excluded.status,
                    summary=excluded.summary,
                    manual_confirmed_at=case
                        when excluded.manual_confirmed_by != '' then current_timestamp
                        else setup_wizard_steps.manual_confirmed_at
                    end,
                    manual_confirmed_by=case
                        when excluded.manual_confirmed_by != '' then excluded.manual_confirmed_by
                        else setup_wizard_steps.manual_confirmed_by
                    end,
                    updated_at=current_timestamp
                """,
                (
                    step_id,
                    status,
                    summary,
                    manual_confirmed_by,
                    manual_confirmed_by,
                ),
            )

    def get_setup_wizard_step(self, step_id: str) -> dict[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select step_id, status, summary, manual_confirmed_at,
                       manual_confirmed_by, updated_at
                from setup_wizard_steps
                where step_id=?
                """,
                (step_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_setup_wizard_steps(self) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select step_id, status, summary, manual_confirmed_at,
                       manual_confirmed_by, updated_at
                from setup_wizard_steps
                order by updated_at desc, step_id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def record_setup_wizard_event(
        self,
        *,
        step_id: str,
        action_id: str,
        status: str,
        summary: str = "",
        evidence_json: str = "{}",
        stdout_excerpt: str = "",
        stderr_excerpt: str = "",
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into setup_wizard_events (
                    step_id,
                    action_id,
                    status,
                    summary,
                    evidence_json,
                    stdout_excerpt,
                    stderr_excerpt,
                    finished_at
                )
                values (?, ?, ?, ?, ?, ?, ?, case when ? = 'running' then '' else current_timestamp end)
                """,
                (
                    step_id,
                    action_id,
                    status,
                    summary,
                    evidence_json,
                    stdout_excerpt,
                    stderr_excerpt,
                    status,
                ),
            )
            return int(cursor.lastrowid)

    def list_setup_wizard_events(
        self,
        step_id: str | None = None,
        *,
        limit: int = 20,
    ) -> list[dict[str, str | int]]:
        with self._connect() as db:
            args: list[str | int] = []
            where = ""
            if step_id is not None:
                where = "where step_id=?"
                args.append(step_id)
            args.append(limit)
            rows = db.execute(
                f"""
                select id, step_id, action_id, status, summary, evidence_json,
                       stdout_excerpt, stderr_excerpt, started_at, finished_at
                from setup_wizard_events
                {where}
                order by id desc
                limit ?
                """,
                args,
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_org_user_profile(
        self,
        user_id: str,
        name: str,
        open_dingtalk_id: str | None,
        manager_user_id: str | None,
        department_ids: set[str],
        title: str = "",
        manager_name: str = "",
        department_names: set[str] | None = None,
        org_labels: list[str] | None = None,
        has_subordinate: bool | None = None,
    ) -> None:
        department_names = department_names or set()
        org_labels = org_labels or []
        with self._connect() as db:
            db.execute(
                """
                insert into org_user_profiles (
                    user_id,
                    name,
                    title,
                    open_dingtalk_id,
                    manager_user_id,
                    manager_name,
                    department_ids_json,
                    department_names_json,
                    org_labels_json,
                    has_subordinate,
                    fetched_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                on conflict(user_id) do update set
                    name=excluded.name,
                    title=excluded.title,
                    open_dingtalk_id=excluded.open_dingtalk_id,
                    manager_user_id=excluded.manager_user_id,
                    manager_name=excluded.manager_name,
                    department_ids_json=excluded.department_ids_json,
                    department_names_json=excluded.department_names_json,
                    org_labels_json=excluded.org_labels_json,
                    has_subordinate=excluded.has_subordinate,
                    fetched_at=current_timestamp
                """,
                (
                    user_id,
                    name,
                    title,
                    open_dingtalk_id,
                    manager_user_id,
                    manager_name,
                    json.dumps(sorted(department_ids), ensure_ascii=False),
                    json.dumps(sorted(department_names), ensure_ascii=False),
                    json.dumps(org_labels, ensure_ascii=False),
                    None if has_subordinate is None else int(has_subordinate),
                ),
            )

    def get_org_user_profile(self, user_id: str) -> OrgUserProfile | None:
        with self._connect() as db:
            row = db.execute(
                "select * from org_user_profiles where user_id=?",
                (user_id,),
            ).fetchone()
            return self._org_user_profile_from_row(row)

    def find_org_user_by_open_dingtalk_id(
        self, open_dingtalk_id: str
    ) -> OrgUserProfile | None:
        with self._connect() as db:
            row = db.execute(
                """
                select * from org_user_profiles
                where open_dingtalk_id=?
                """,
                (open_dingtalk_id,),
            ).fetchone()
            return self._org_user_profile_from_row(row)

    def find_org_users_by_name(self, name: str) -> list[OrgUserProfile]:
        with self._connect() as db:
            rows = db.execute(
                "select * from org_user_profiles where name=? order by user_id",
                (name,),
            ).fetchall()
            return [
                profile
                for row in rows
                if (profile := self._org_user_profile_from_row(row)) is not None
            ]

    def list_org_user_ids(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "select user_id from org_user_profiles order by user_id"
            ).fetchall()
            return [row["user_id"] for row in rows]

    def set_current_user_id(self, user_id: str) -> None:
        self._set_metadata("current_user_id", user_id)

    def get_current_user_id(self) -> str | None:
        return self._get_metadata("current_user_id")

    def set_hr_department_ids(self, department_ids: set[str]) -> None:
        self._set_metadata("hr_department_ids", sorted(department_ids))

    def get_hr_department_ids(self) -> set[str]:
        value = self._get_metadata("hr_department_ids")
        if not isinstance(value, list):
            return set()
        return {str(item) for item in value if item}

    def _set_metadata(self, key: str, value) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into org_cache_metadata (key, value_json, updated_at)
                values (?, ?, current_timestamp)
                on conflict(key) do update set
                    value_json=excluded.value_json,
                    updated_at=current_timestamp
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def _get_metadata(self, key: str):
        with self._connect() as db:
            row = db.execute(
                "select value_json from org_cache_metadata where key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["value_json"])

    @staticmethod
    def _org_user_profile_from_row(row: sqlite3.Row | None) -> OrgUserProfile | None:
        if row is None:
            return None
        return OrgUserProfile(
            user_id=row["user_id"],
            name=row["name"],
            title=row["title"],
            open_dingtalk_id=row["open_dingtalk_id"],
            manager_user_id=row["manager_user_id"],
            manager_name=row["manager_name"],
            department_ids=set(json.loads(row["department_ids_json"])),
            department_names=set(json.loads(row["department_names_json"])),
            org_labels=list(json.loads(row["org_labels_json"])),
            has_subordinate=(
                None
                if row["has_subordinate"] is None
                else bool(row["has_subordinate"])
            ),
        )
