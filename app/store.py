import json
import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections.abc import Iterator
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter

from app.feishu.models import (
    FeishuAuditEvent,
    FeishuDelivery,
    FeishuDeliveryReceipt,
    FeishuEventRecord,
    FeishuInboundMessage,
    FeishuReplyScope,
)
from app.feishu.actions import (
    FeishuMessageAction,
    action_approval_hash,
    build_message_action,
)
from app.feishu.media import (
    DEFAULT_MAX_EVENT_BYTES,
    DEFAULT_MAX_EVENT_RESOURCES,
    DEFAULT_MAX_RESOURCE_BYTES,
    DEFAULT_MEDIA_PROCESSING_GRACE_SECONDS,
    FeishuMediaAsset,
    FeishuMediaRejected,
    file_key_sha256,
    safe_media_name,
)
from app.feishu.payloads import (
    FeishuReplyPayload,
    delivery_approval_hash,
    delivery_chunk_plan_sha256,
    split_reply_payload,
)
from app.wechat.models import WechatReplyScope
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
from app.feedback_policy import FeedbackPressureStats
from app.history import (
    HistoryItem,
    UniversalActionObservation,
    UniversalExecutionObservation,
    safe_observability_error,
)
from app.universal_context import (
    UniversalTaskContext,
    canonical_universal_context_json,
    universal_context_sha256,
)
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    UniversalPlanExecution,
    build_universal_action_execution,
    canonical_universal_action_json,
)
from app.universal_plan import (
    PlannedActionKind,
    UniversalPlan,
    with_context_action_targets,
)

FAST_PATH_UNREAD_BACKOFF_TASK_ERROR = "waiting_fast_path_unread_backoff"
SQLITE_BUSY_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MILLISECONDS = SQLITE_BUSY_TIMEOUT_SECONDS * 1000
UNIVERSAL_MEMORY_LEASE_SECONDS = 15 * 60
CODEX_SESSION_LOCK_STALE_SECONDS = 20 * 60
FEISHU_DELIVERY_STATUSES = frozenset(
    {
        "ready_to_send",
        "sending",
        "sent",
        "retry",
        "send_unknown",
        "failed",
        "rejected",
    }
)
FEISHU_DELIVERY_TRANSITIONS = frozenset(
    {
        ("ready_to_send", "sending"),
        ("ready_to_send", "rejected"),
        ("retry", "sending"),
        ("retry", "rejected"),
        ("sending", "sent"),
        ("sending", "retry"),
        ("sending", "send_unknown"),
        ("sending", "failed"),
        ("send_unknown", "sent"),
        ("send_unknown", "retry"),
        ("send_unknown", "failed"),
        ("failed", "retry"),
    }
)
FEISHU_RECONCILIATION_EVIDENCE_KINDS = frozenset(
    {"feishu_ui", "message_lookup", "admin_audit"}
)
FEISHU_RETRYABLE_ERROR_CODES = frozenset(
    {"rate_limited", "not_connected", "target_state_unknown"}
)
FEISHU_CONFIRMED_NOT_SENT_ERROR_CODES = frozenset(
    {
        *FEISHU_RETRYABLE_ERROR_CODES,
        "format_error",
        "target_revoked",
        "permission_denied",
        "upload_failed",
        "download_failed",
        "ssrf_blocked",
    }
)

_LEGACY_FEISHU_CLI_TASK_KEYS = frozenset(
    {
        "channel",
        "conversation_id",
        "conversation_title",
        "conversation_type",
        "message_id",
        "sent_at",
        "sender_display",
        "text",
        "raw_json",
    }
)
_LEGACY_FEISHU_CLI_QUARANTINE_PREFIX = "feishu_cli_quarantine:"
FEISHU_UNCERTAIN_SEND_ERROR_CODES = frozenset({"send_timeout", "unknown"})
FEISHU_ACTION_STATUSES = frozenset(
    {"ready", "sending", "sent", "retry", "result_unknown", "failed", "rejected"}
)
FEISHU_ACTION_TRANSITIONS = frozenset(
    {
        ("ready", "sending"),
        ("ready", "rejected"),
        ("retry", "sending"),
        ("retry", "rejected"),
        ("sending", "sent"),
        ("sending", "retry"),
        ("sending", "result_unknown"),
        ("sending", "failed"),
    }
)
FEISHU_ACTION_RETRYABLE_ERROR_CODES = frozenset(
    {"rate_limited", "not_connected"}
)
FEISHU_ACTION_TERMINAL_ERROR_CODES = frozenset(
    {"format_error", "permission_denied", "target_revoked"}
)
FEISHU_ACTION_UNCERTAIN_ERROR_CODES = frozenset({"send_timeout", "unknown"})
FEISHU_LOCAL_NOTIFICATION_STATUSES = frozenset(
    {
        "waiting_remote",
        "pending",
        "sending",
        "retry",
        "result_unknown",
        "sent",
        "failed",
        "cancelled",
    }
)
FEISHU_RECEIPT_STATUSES = frozenset(
    {"active", "recalled", "recall_unknown"}
)
FEISHU_RECALLABLE_DELIVERY_STATUSES = frozenset(
    {"sent", "failed", "rejected"}
)
FEISHU_SINGLE_CHUNK_UNKNOWN_ERRORS = frozenset(
    {
        "feishu_partial_delivery_result_unknown",
        "successful_response_missing_message_id",
        "feishu_send_cancelled_result_unknown",
        "feishu_send_failed:send_timeout",
        "feishu_send_failed:unknown",
        "orphaned_sending_requires_review",
    }
)
FEISHU_MEDIA_STATUSES = frozenset(
    {"pending", "downloading", "ready", "rejected", "purged"}
)
FEISHU_MEDIA_RESOURCE_TYPES = frozenset(
    {"image", "file", "audio", "video", "sticker"}
)
_INITIALIZED_STORE_PATHS: set[Path] = set()
_INITIALIZE_LOCK = threading.Lock()


def _feishu_unknown_allows_one_chunk_verification(row: sqlite3.Row) -> bool:
    """Recognize only locally-produced, single-invocation uncertainty."""
    error = str(row["error"] or "")
    if error in FEISHU_SINGLE_CHUNK_UNKNOWN_ERRORS:
        return True
    code = str(row["error_code"] or "")
    if code == "send_timeout":
        return True
    return code == "unknown" and error.startswith(
        f"feishu_send_exception:{code}:"
    )


@dataclass(frozen=True)
class UniversalMemoryActionClaim:
    state: UniversalActionExecutionState
    lease_token: str = ""


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
    universal_execution_id: str = ""
    universal_execution_scope_id: str = ""
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
    conversation_title: str = ""
    trigger_sender: str = ""
    trigger_text: str = ""
    final_reply_text: str = ""
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
    execution_generation: str = "initial"
    status: str
    attempts: int
    lease_token: str = ""
    locked_at: str | None = None
    error: str = ""
    created_at: str
    updated_at: str


class FeishuLocalNotification(BaseModel):
    """One durable, offline-only fallback notification."""

    id: int
    reply_task_id: int
    attempt_id: int
    app_id: str
    execution_generation: str
    kind: str = "handoff_fallback"
    dependency_mode: str
    title: str
    message: str
    status: str
    attempts: int = 0
    lease_token: str = ""
    locked_at: str = ""
    mutation_started_at: str = ""
    available_at: str = ""
    error_code: str = ""
    error: str = ""
    sent_at: str = ""
    created_at: str = ""
    updated_at: str = ""


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        path_key = self.path.resolve()
        if path_key in _INITIALIZED_STORE_PATHS:
            return
        with _INITIALIZE_LOCK:
            if path_key in _INITIALIZED_STORE_PATHS:
                return
            self._initialize()
            _INITIALIZED_STORE_PATHS.add(path_key)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_seconds,
        )
        connection.execute(f"pragma busy_timeout = {self.busy_timeout_milliseconds}")
        connection.execute("pragma synchronous = normal")
        connection.execute("pragma foreign_keys = on")
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("pragma journal_mode = wal")
            db.executescript(
                """
                create table if not exists conversations (
                    conversation_id text primary key,
                    title text not null,
                    single_chat integer not null,
                    codex_session_id text
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
                    created_at text not null default current_timestamp
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
                    universal_execution_id text not null default '',
                    universal_execution_scope_id text not null default '',
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
                    execution_generation text not null default 'initial',
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    lease_token text not null default '',
                    locked_at text,
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(channel, conversation_id, trigger_message_id)
                );
                create index if not exists idx_reply_tasks_status
                    on reply_tasks(status, id);
                create table if not exists feishu_events (
                    id integer primary key autoincrement,
                    event_id text not null,
                    app_id text not null,
                    message_id text not null,
                    chat_id text not null,
                    chat_type text not null,
                    chat_title text not null default '',
                    thread_id text not null default '',
                    root_message_id text not null default '',
                    parent_message_id text not null default '',
                    reply_to_message_id text not null default '',
                    sender_open_id text not null,
                    sender_type text not null default 'user',
                    sender_name text not null default '',
                    message_type text not null,
                    mentioned_bot integer not null default 0,
                    body_text text not null default '',
                    normalized_summary text not null default '',
                    normalization_version integer not null default 1,
                    content_truncated integer not null default 0,
                    resource_truncated integer not null default 0,
                    media_required integer not null default 0,
                    event_create_time text not null,
                    event_create_time_ms integer not null default 0,
                    received_at text not null default current_timestamp,
                    eligibility_status text not null,
                    reject_reason text not null default '',
                    reply_task_id integer,
                    created_at text not null default current_timestamp,
                    unique(app_id, message_id),
                    foreign key(reply_task_id) references reply_tasks(id)
                );
                create index if not exists idx_feishu_events_context
                    on feishu_events(app_id, chat_id, eligibility_status,
                                     event_create_time, id);
                create index if not exists idx_feishu_events_thread_context
                    on feishu_events(
                        app_id, chat_id, thread_id, eligibility_status,
                        event_create_time, id
                    );
                create index if not exists idx_feishu_events_reply_task
                    on feishu_events(reply_task_id);
                create index if not exists idx_feishu_events_retention
                    on feishu_events(created_at, id);
                create table if not exists feishu_media_assets (
                    id integer primary key autoincrement,
                    event_record_id integer not null,
                    app_id text not null,
                    message_id text not null,
                    ordinal integer not null,
                    resource_type text not null,
                    role text not null default '',
                    file_key text not null default '',
                    file_key_sha256 text not null,
                    safe_name text not null default '',
                    duration_ms integer not null default 0,
                    status text not null default 'pending'
                        check(status in (
                            'pending', 'downloading', 'ready',
                            'rejected', 'purged'
                        )),
                    lease_token text not null default '',
                    relative_path text not null default '',
                    mime_type text not null default '',
                    size_bytes integer not null default 0,
                    sha256 text not null default '',
                    error_code text not null default '',
                    error text not null default '',
                    locked_at text not null default '',
                    ready_at text not null default '',
                    purged_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(event_record_id, ordinal),
                    foreign key(event_record_id) references feishu_events(id)
                        on delete cascade
                );
                create index if not exists idx_feishu_media_assets_claim
                    on feishu_media_assets(app_id, status, id);
                create index if not exists idx_feishu_media_assets_event
                    on feishu_media_assets(event_record_id, status, ordinal);
                create table if not exists feishu_reply_scopes (
                    app_id text not null,
                    target_type text not null,
                    target_id text not null,
                    display_name text not null default '',
                    trigger_mode text not null,
                    enabled integer not null default 0,
                    binding_status text not null default 'pending',
                    last_seen_at text not null default '',
                    approved_at text not null default '',
                    approved_by text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    primary key(app_id, target_type, target_id)
                );
                create index if not exists idx_feishu_reply_scopes_review
                    on feishu_reply_scopes(binding_status, enabled,
                                           target_type, target_id);
                create table if not exists feishu_deliveries (
                    id integer primary key autoincrement,
                    reply_task_id integer not null unique,
                    attempt_id integer not null,
                    app_id text not null,
                    chat_id text not null,
                    reply_to_message_id text not null,
                    reply_in_thread integer not null default 0,
                    reply_text text not null,
                    reply_format text not null default 'text',
                    mention_open_ids_json text not null default '[]',
                    payload_sha256 text not null default '',
                    idempotency_key text not null unique,
                    expected_chunks integer not null default 1
                        check(expected_chunks >= 1 and expected_chunks <= 100),
                    chunk_plan_sha256 text not null default '',
                    review_generation integer not null default 1
                        check(review_generation >= 1),
                    approval_hash text not null default '',
                    status text not null default 'ready_to_send',
                    feishu_message_id text not null default '',
                    request_log_id text not null default '',
                    attempts integer not null default 0,
                    remote_failures integer not null default 0,
                    lease_token text not null default '',
                    mutation_started_at text not null default '',
                    approved_at text not null default '',
                    approved_by text not null default '',
                    locked_at text not null default '',
                    available_at text not null default '',
                    error_code text not null default '',
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    foreign key(reply_task_id) references reply_tasks(id),
                    foreign key(attempt_id) references reply_attempts(id)
                );
                create index if not exists idx_feishu_deliveries_claim
                    on feishu_deliveries(status, available_at, id);
                create index if not exists idx_feishu_deliveries_chat
                    on feishu_deliveries(app_id, chat_id, id);
                create table if not exists feishu_delivery_receipts (
                    id integer primary key autoincrement,
                    delivery_id integer not null,
                    app_id text not null,
                    ordinal integer not null check(ordinal >= 0 and ordinal < 100),
                    message_id text not null,
                    request_log_id text not null default '',
                    status text not null default 'active'
                        check(status in ('active', 'recalled', 'recall_unknown')),
                    recall_action_id integer not null default 0,
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(delivery_id, ordinal),
                    unique(app_id, message_id),
                    foreign key(delivery_id) references feishu_deliveries(id)
                        on delete restrict
                );
                create index if not exists idx_feishu_delivery_receipts_owner
                    on feishu_delivery_receipts(app_id, message_id, status);
                create table if not exists feishu_message_actions (
                    id integer primary key autoincrement,
                    reply_task_id integer not null,
                    attempt_id integer not null,
                    app_id text not null,
                    chat_id text not null default '',
                    action_key text not null,
                    kind text not null check(kind in (
                        'add_reaction', 'recall_message', 'handoff_notify'
                    )),
                    target_message_id text not null default '',
                    target_open_id text not null default '',
                    payload_json text not null,
                    payload_sha256 text not null,
                    idempotency_key text not null unique,
                    review_generation integer not null default 1
                        check(review_generation >= 1),
                    approval_hash text not null,
                    risk text not null check(risk in ('R2', 'R4')),
                    status text not null default 'ready' check(status in (
                        'ready', 'sending', 'sent', 'retry',
                        'result_unknown', 'failed', 'rejected'
                    )),
                    remote_id text not null default '',
                    request_log_id text not null default '',
                    attempts integer not null default 0,
                    remote_failures integer not null default 0,
                    lease_token text not null default '',
                    mutation_started_at text not null default '',
                    approved_at text not null default '',
                    approved_by text not null default '',
                    locked_at text not null default '',
                    available_at text not null default '',
                    error_code text not null default '',
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(reply_task_id, action_key),
                    foreign key(reply_task_id) references reply_tasks(id)
                        on delete restrict,
                    foreign key(attempt_id) references reply_attempts(id)
                        on delete restrict
                );
                create index if not exists idx_feishu_message_actions_claim
                    on feishu_message_actions(app_id, status, available_at, id);
                create index if not exists idx_feishu_message_actions_chat
                    on feishu_message_actions(app_id, chat_id, id);
                create trigger if not exists feishu_message_actions_identity_immutable
                before update on feishu_message_actions
                when old.reply_task_id<>new.reply_task_id
                  or old.attempt_id<>new.attempt_id
                  or old.app_id<>new.app_id
                  or old.chat_id<>new.chat_id
                  or old.action_key<>new.action_key
                  or old.kind<>new.kind
                  or old.target_message_id<>new.target_message_id
                  or old.target_open_id<>new.target_open_id
                  or old.payload_json<>new.payload_json
                  or old.payload_sha256<>new.payload_sha256
                  or old.idempotency_key<>new.idempotency_key
                  or old.risk<>new.risk
                  or (
                    old.status='failed'
                    and old.error_code='verified_not_applied'
                    and new.status='retry'
                    and not (
                      new.error_code=''
                      and new.review_generation=old.review_generation + 1
                      and new.approval_hash<>old.approval_hash
                      and new.approved_at='' and new.approved_by=''
                    )
                  )
                  or (
                    (
                      old.review_generation<>new.review_generation
                      or old.approval_hash<>new.approval_hash
                    )
                    and not (
                      old.status='failed'
                      and old.error_code='verified_not_applied'
                      and new.status='retry' and new.error_code=''
                      and new.review_generation=old.review_generation + 1
                      and new.approval_hash<>old.approval_hash
                      and new.approved_at='' and new.approved_by=''
                    )
                  )
                begin
                    select raise(abort, 'Feishu message action identity is immutable');
                end;
                create table if not exists feishu_local_notifications (
                    id integer primary key autoincrement,
                    reply_task_id integer not null,
                    attempt_id integer not null,
                    app_id text not null,
                    execution_generation text not null,
                    kind text not null default 'handoff_fallback'
                        check(kind='handoff_fallback'),
                    dependency_mode text not null check(dependency_mode in (
                        'immediate', 'remote_failure'
                    )),
                    title text not null,
                    message text not null,
                    status text not null check(status in (
                        'waiting_remote', 'pending', 'sending', 'retry',
                        'result_unknown', 'sent', 'failed', 'cancelled'
                    )),
                    attempts integer not null default 0,
                    lease_token text not null default '',
                    locked_at text not null default '',
                    mutation_started_at text not null default '',
                    available_at text not null default '',
                    error_code text not null default '',
                    error text not null default '',
                    sent_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(reply_task_id, execution_generation, kind),
                    foreign key(reply_task_id) references reply_tasks(id)
                        on delete restrict,
                    foreign key(attempt_id) references reply_attempts(id)
                        on delete restrict
                );
                create index if not exists idx_feishu_local_notifications_claim
                    on feishu_local_notifications(
                        app_id, status, available_at, id
                    );
                create trigger if not exists feishu_local_notifications_identity_immutable
                before update on feishu_local_notifications
                when old.reply_task_id<>new.reply_task_id
                  or old.attempt_id<>new.attempt_id
                  or old.app_id<>new.app_id
                  or old.execution_generation<>new.execution_generation
                  or old.kind<>new.kind
                  or old.dependency_mode<>new.dependency_mode
                  or old.title<>new.title
                  or old.message<>new.message
                begin
                    select raise(abort, 'Feishu local notification identity is immutable');
                end;
                create table if not exists feishu_audit_events (
                    id integer primary key autoincrement,
                    app_id text not null,
                    entity_type text not null,
                    entity_id text not null,
                    event_type text not null,
                    previous_state text not null default '',
                    new_state text not null default '',
                    actor text not null default '',
                    detail text not null default '',
                    created_at text not null default current_timestamp
                );
                create index if not exists idx_feishu_audit_events_entity
                    on feishu_audit_events(
                        app_id, entity_type, entity_id, id
                    );
                create trigger if not exists feishu_audit_events_no_update
                before update on feishu_audit_events
                begin
                    select raise(abort, 'Feishu audit events are immutable');
                end;
                create trigger if not exists feishu_audit_events_no_delete
                before delete on feishu_audit_events
                begin
                    select raise(abort, 'Feishu audit events are immutable');
                end;
                create table if not exists universal_plan_executions (
                    execution_scope_id text primary key,
                    reply_task_id integer not null,
                    execution_generation text not null,
                    plan_json text not null,
                    context_hash text not null default '',
                    context_json text not null default '',
                    status text not null default 'active',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(reply_task_id, execution_generation),
                    foreign key(reply_task_id) references reply_tasks(id)
                );
                create table if not exists universal_action_executions (
                    execution_id text primary key,
                    execution_scope_id text not null,
                    action_index integer not null,
                    action_kind text not null,
                    action_hash text not null,
                    action_json text not null,
                    canonical_payload_json text not null default '',
                    lease_token text not null default '',
                    lease_expires_at text not null default '',
                    status text not null,
                    attempt_id integer not null default 0,
                    result_json text not null default '',
                    error text not null default '',
                    started_at text not null default '',
                    completed_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(execution_scope_id, action_index),
                    foreign key(execution_scope_id)
                        references universal_plan_executions(execution_scope_id)
                );
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
                    status text not null default 'ready_to_send',
                    action_started_at text not null default '',
                    evidence_json text not null default '{}',
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    foreign key(reply_task_id) references reply_tasks(id)
                );
                create index if not exists idx_wechat_deliveries_status
                    on wechat_deliveries(status, id);
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
                    created_at text not null default current_timestamp
                );
                create index if not exists idx_task_agent_runs_input
                    on task_agent_runs(summary_input_id, id);
                create table if not exists follow_up_drafts (
                    id integer primary key autoincrement,
                    project_id integer not null,
                    todo_id integer not null default 0,
                    owner_user_id text not null default '',
                    owner_name text not null default '',
                    target_conversation_id text not null default '',
                    target_kind text not null default '',
                    question_text text not null default '',
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
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_follow_up_drafts_status
                    on follow_up_drafts(status, scheduled_at, id);
                create index if not exists idx_follow_up_drafts_owner_sent
                    on follow_up_drafts(owner_user_id, sent_at, id);
                create index if not exists idx_follow_up_drafts_conversation_sent
                    on follow_up_drafts(target_conversation_id, sent_at, id);
                create table if not exists daily_scan_state (
                    scanner_name text primary key,
                    last_success_at text not null default '',
                    cursor_json text not null default '{}',
                    last_error text not null default '',
                    updated_at text not null default current_timestamp
                );
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
                ("channel", "text not null default 'dingtalk'"),
                ("execution_generation", "text not null default 'initial'"),
                ("lease_token", "text not null default ''"),
            ):
                if column not in reply_task_columns:
                    db.execute(
                        f"alter table reply_tasks add column {column} {definition}"
                    )
            feishu_event_columns = {
                row["name"]
                for row in db.execute("pragma table_info(feishu_events)").fetchall()
            }
            for column, definition in (
                ("root_message_id", "text not null default ''"),
                ("parent_message_id", "text not null default ''"),
                ("normalized_summary", "text not null default ''"),
                ("normalization_version", "integer not null default 1"),
                ("content_truncated", "integer not null default 0"),
                ("resource_truncated", "integer not null default 0"),
                ("media_required", "integer not null default 0"),
                ("event_create_time_ms", "integer not null default 0"),
            ):
                if column not in feishu_event_columns:
                    try:
                        db.execute(
                            "alter table feishu_events add column "
                            f"{column} {definition}"
                        )
                    except sqlite3.OperationalError:
                        concurrent_columns = {
                            row["name"]
                            for row in db.execute(
                                "pragma table_info(feishu_events)"
                            ).fetchall()
                        }
                        if column not in concurrent_columns:
                            raise
            self._migrate_feishu_event_identity(db)
            db.execute(
                """
                update feishu_events
                set media_required=1
                where media_required=0 and exists (
                    select 1 from feishu_media_assets as asset
                    where asset.event_record_id=feishu_events.id
                )
                """
            )
            while True:
                legacy_events = db.execute(
                    """
                    select id, event_create_time from feishu_events
                    where event_create_time_ms=0
                      and eligibility_status='eligible'
                    order by id
                    limit 500
                    """
                ).fetchall()
                if not legacy_events:
                    break
                for legacy_event in legacy_events:
                    normalized_ms = self._feishu_event_time_ms(
                        legacy_event["event_create_time"]
                    )
                    db.execute(
                        """
                        update feishu_events set event_create_time_ms=?
                        where id=?
                        """,
                        (
                            normalized_ms if normalized_ms > 0 else -1,
                            legacy_event["id"],
                        ),
                    )
            db.execute(
                """
                create index if not exists idx_feishu_events_thread_asof
                on feishu_events(
                    app_id, chat_id, thread_id, eligibility_status,
                    event_create_time_ms, id
                )
                """
            )
            db.execute(
                """
                create index if not exists idx_feishu_events_root_asof
                on feishu_events(
                    app_id, chat_id, root_message_id, eligibility_status,
                    event_create_time_ms, id
                )
                """
            )
            feishu_delivery_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(feishu_deliveries)"
                ).fetchall()
            }
            if "attempt_id" not in feishu_delivery_columns:
                db.execute(
                    "alter table feishu_deliveries add column "
                    "attempt_id integer not null default 0"
                )
            for column in ("approved_at", "approved_by", "lease_token"):
                if column not in feishu_delivery_columns:
                    db.execute(
                        "alter table feishu_deliveries add column "
                        f"{column} text not null default ''"
                    )
            feishu_delivery_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(feishu_deliveries)"
                ).fetchall()
            }
            for column, definition in (
                ("reply_format", "text not null default 'text'"),
                ("mention_open_ids_json", "text not null default '[]'"),
                ("payload_sha256", "text not null default ''"),
                ("expected_chunks", "integer not null default 1"),
                ("chunk_plan_sha256", "text not null default ''"),
                ("approval_hash", "text not null default ''"),
                ("review_generation", "integer not null default 1"),
                ("remote_failures", "integer not null default 0"),
                ("mutation_started_at", "text not null default ''"),
            ):
                if column not in feishu_delivery_columns:
                    db.execute(
                        "alter table feishu_deliveries add column "
                        f"{column} {definition}"
                    )
            db.execute(
                """
                update feishu_deliveries
                set mutation_started_at=coalesce(
                    nullif(locked_at, ''), nullif(updated_at, ''),
                    current_timestamp
                )
                where mutation_started_at=''
                  and (
                    status in ('sending', 'send_unknown', 'sent')
                    or feishu_message_id<>''
                    or exists (
                        select 1 from feishu_delivery_receipts as receipts
                        where receipts.delivery_id=feishu_deliveries.id
                    )
                  )
                """
            )
            feishu_action_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(feishu_message_actions)"
                ).fetchall()
            }
            for column, definition in (
                ("remote_failures", "integer not null default 0"),
                ("mutation_started_at", "text not null default ''"),
                ("review_generation", "integer not null default 1"),
            ):
                if column not in feishu_action_columns:
                    db.execute(
                        "alter table feishu_message_actions add column "
                        f"{column} {definition}"
                    )
            db.execute(
                """
                update feishu_message_actions
                set mutation_started_at=coalesce(
                    nullif(locked_at, ''), nullif(updated_at, ''),
                    current_timestamp
                )
                where mutation_started_at=''
                  and status in ('sending', 'result_unknown', 'sent')
                """
            )
            self._migrate_feishu_action_approval_identity(db)
            self._migrate_feishu_local_notifications(db)
            for legacy_delivery in db.execute(
                """
                select id, reply_text, reply_format, mention_open_ids_json,
                       payload_sha256
                from feishu_deliveries
                where payload_sha256=''
                """
            ).fetchall():
                try:
                    mentions = tuple(
                        json.loads(legacy_delivery["mention_open_ids_json"] or "[]")
                    )
                    payload = FeishuReplyPayload(
                        kind=legacy_delivery["reply_format"] or "text",
                        text=legacy_delivery["reply_text"],
                        mention_open_ids=mentions,
                    )
                except Exception as exc:
                    raise sqlite3.IntegrityError(
                        "legacy Feishu delivery payload is invalid"
                    ) from exc
                db.execute(
                    """
                    update feishu_deliveries set payload_sha256=?
                    where id=? and payload_sha256=''
                    """,
                    (payload.sha256(), legacy_delivery["id"]),
                )
            receipt_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(feishu_delivery_receipts)"
                ).fetchall()
            }
            if "request_log_id" not in receipt_columns:
                db.execute(
                    "alter table feishu_delivery_receipts add column "
                    "request_log_id text not null default ''"
                )
            self._migrate_reply_task_channel_identity(db)
            self._migrate_legacy_feishu_cli_reply_tasks(db)
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

            reply_attempt_columns = {
                row["name"]
                for row in db.execute("pragma table_info(reply_attempts)").fetchall()
            }
            for column, definition in (
                ("codex_session_id", "text not null default ''"),
                ("direct_user_id", "text not null default ''"),
                ("direct_open_dingtalk_id", "text not null default ''"),
                ("codex_transcript_start_line", "integer not null default 0"),
                ("codex_transcript_end_line", "integer not null default 0"),
                ("audit_documents_json", "text not null default '[]'"),
                ("audit_tool_events_json", "text not null default '[]'"),
                ("audit_summary", "text not null default ''"),
                ("universal_execution_id", "text not null default ''"),
                ("universal_execution_scope_id", "text not null default ''"),
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
            ):
                if column not in reply_attempt_columns:
                    try:
                        db.execute(
                            f"alter table reply_attempts add column {column} {definition}"
                        )
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc):
                            raise
            db.execute(
                """
                create unique index if not exists idx_reply_attempts_universal_execution
                on reply_attempts(universal_execution_id)
                where universal_execution_id <> ''
                """
            )
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
                ("channel", "text not null default 'dingtalk'"),
                ("execution_generation", "text not null default 'initial'"),
            ):
                if column not in reply_task_columns:
                    db.execute(
                        f"alter table reply_tasks add column {column} {definition}"
                    )
            universal_plan_execution_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(universal_plan_executions)"
                ).fetchall()
            }
            for column in ("context_hash", "context_json"):
                if column not in universal_plan_execution_columns:
                    db.execute(
                        f"alter table universal_plan_executions add column {column} "
                        "text not null default ''"
                    )
            universal_action_execution_columns = {
                row["name"]
                for row in db.execute(
                    "pragma table_info(universal_action_executions)"
                ).fetchall()
            }
            for column in (
                "canonical_payload_json",
                "lease_token",
                "lease_expires_at",
            ):
                if column not in universal_action_execution_columns:
                    db.execute(
                        "alter table universal_action_executions add column "
                        f"{column} text not null default ''"
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
            self._migrate_feishu_delivery_attempts(db)
            self._migrate_feishu_delivery_approval_identity(
                db, create_trigger=False
            )
            self._migrate_feishu_delivery_bindings(db)
            self._migrate_feishu_delivery_approval_identity(
                db, create_trigger=True
            )
            self._migrate_feishu_delivery_receipts(db)
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
            ):
                if column not in work_todo_columns:
                    db.execute(
                        f"alter table work_todos add column {column} {definition}"
                    )
            follow_up_draft_columns = {
                row["name"]
                for row in db.execute("pragma table_info(follow_up_drafts)").fetchall()
            }
            for column, definition in (
                ("evidence_check_json", "text not null default '{}'"),
                ("reaction_status", "text not null default ''"),
                ("reaction_summary", "text not null default ''"),
                ("suppressed_reason", "text not null default ''"),
                ("dedupe_key", "text not null default ''"),
                ("updated_at", "text not null default ''"),
            ):
                if column not in follow_up_draft_columns:
                    db.execute(
                        f"alter table follow_up_drafts add column {column} {definition}"
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
                create index if not exists idx_meeting_alignment_runs_created
                    on meeting_alignment_runs(created_at, id)
                """
            )
            db.execute(
                """
                create index if not exists idx_work_updates_created
                    on work_updates(created_at, id)
                """
            )
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

    @staticmethod
    def _migrate_feishu_local_notifications(db: sqlite3.Connection) -> None:
        """Add the local mutation fence and explicit uncertainty state."""
        definition = db.execute(
            """
            select sql from sqlite_master
            where type='table' and name='feishu_local_notifications'
            """
        ).fetchone()
        if definition is None:
            return
        columns = {
            row["name"]
            for row in db.execute(
                "pragma table_info(feishu_local_notifications)"
            ).fetchall()
        }
        table_sql = str(definition["sql"] or "")
        if (
            "mutation_started_at" in columns
            and "'result_unknown'" in table_sql
        ):
            return

        legacy_has_fence = "mutation_started_at" in columns
        db.execute(
            "drop trigger if exists feishu_local_notifications_identity_immutable"
        )
        db.execute("drop index if exists idx_feishu_local_notifications_claim")
        db.execute(
            """
            alter table feishu_local_notifications
            rename to feishu_local_notifications_legacy
            """
        )
        db.execute(
            """
            create table feishu_local_notifications (
                id integer primary key autoincrement,
                reply_task_id integer not null,
                attempt_id integer not null,
                app_id text not null,
                execution_generation text not null,
                kind text not null default 'handoff_fallback'
                    check(kind='handoff_fallback'),
                dependency_mode text not null check(dependency_mode in (
                    'immediate', 'remote_failure'
                )),
                title text not null,
                message text not null,
                status text not null check(status in (
                    'waiting_remote', 'pending', 'sending', 'retry',
                    'result_unknown', 'sent', 'failed', 'cancelled'
                )),
                attempts integer not null default 0,
                lease_token text not null default '',
                locked_at text not null default '',
                mutation_started_at text not null default '',
                available_at text not null default '',
                error_code text not null default '',
                error text not null default '',
                sent_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(reply_task_id, execution_generation, kind),
                foreign key(reply_task_id) references reply_tasks(id)
                    on delete restrict,
                foreign key(attempt_id) references reply_attempts(id)
                    on delete restrict
            )
            """
        )
        fence_expression = (
            "mutation_started_at"
            if legacy_has_fence
            else "case when status='sending' then "
            "coalesce(nullif(locked_at, ''), nullif(updated_at, ''), "
            "current_timestamp) else '' end"
        )
        db.execute(
            f"""
            insert into feishu_local_notifications (
                id, reply_task_id, attempt_id, app_id,
                execution_generation, kind, dependency_mode,
                title, message, status, attempts, lease_token, locked_at,
                mutation_started_at, available_at, error_code, error,
                sent_at, created_at, updated_at
            )
            select
                id, reply_task_id, attempt_id, app_id,
                execution_generation, kind, dependency_mode,
                title, message, status, attempts, lease_token, locked_at,
                {fence_expression}, available_at, error_code, error,
                sent_at, created_at, updated_at
            from feishu_local_notifications_legacy
            """
        )
        db.execute("drop table feishu_local_notifications_legacy")
        db.execute(
            """
            create index idx_feishu_local_notifications_claim
            on feishu_local_notifications(app_id, status, available_at, id)
            """
        )
        db.execute(
            """
            create trigger feishu_local_notifications_identity_immutable
            before update on feishu_local_notifications
            when old.reply_task_id<>new.reply_task_id
              or old.attempt_id<>new.attempt_id
              or old.app_id<>new.app_id
              or old.execution_generation<>new.execution_generation
              or old.kind<>new.kind
              or old.dependency_mode<>new.dependency_mode
              or old.title<>new.title
              or old.message<>new.message
            begin
                select raise(abort, 'Feishu local notification identity is immutable');
            end
            """
        )

    @staticmethod
    def _migrate_feishu_event_identity(db: sqlite3.Connection) -> None:
        """Drop the legacy global event-ID uniqueness constraint safely.

        Feishu documents ``message_id`` as the receive-event deduplication key.
        ``event_id`` is retained as audit evidence only and may repeat across
        redeliveries, messages, or applications.
        """
        unique_columns = {
            tuple(
                row["name"]
                for row in db.execute(
                    f"pragma index_info('{index['name']}')"
                ).fetchall()
            )
            for index in db.execute("pragma index_list(feishu_events)").fetchall()
            if index["unique"]
        }
        if ("event_id",) not in unique_columns:
            return

        if db.in_transaction:
            db.commit()
        db.execute("pragma foreign_keys=off")
        if db.execute("pragma foreign_keys").fetchone()[0] != 0:
            raise sqlite3.OperationalError(
                "could not disable foreign keys for feishu_events migration"
            )
        try:
            db.executescript(
                """
                begin immediate;
                drop table if exists feishu_events_identity_migration;
                create table feishu_events_identity_migration (
                    id integer primary key autoincrement,
                    event_id text not null,
                    app_id text not null,
                    message_id text not null,
                    chat_id text not null,
                    chat_type text not null,
                    chat_title text not null default '',
                    thread_id text not null default '',
                    root_message_id text not null default '',
                    parent_message_id text not null default '',
                    reply_to_message_id text not null default '',
                    sender_open_id text not null,
                    sender_type text not null default 'user',
                    sender_name text not null default '',
                    message_type text not null,
                    mentioned_bot integer not null default 0,
                    body_text text not null default '',
                    normalized_summary text not null default '',
                    normalization_version integer not null default 1,
                    content_truncated integer not null default 0,
                    resource_truncated integer not null default 0,
                    media_required integer not null default 0,
                    event_create_time text not null,
                    event_create_time_ms integer not null default 0,
                    received_at text not null default current_timestamp,
                    eligibility_status text not null,
                    reject_reason text not null default '',
                    reply_task_id integer,
                    created_at text not null default current_timestamp,
                    unique(app_id, message_id),
                    foreign key(reply_task_id) references reply_tasks(id)
                );
                insert into feishu_events_identity_migration (
                    id, event_id, app_id, message_id, chat_id, chat_type,
                    chat_title, thread_id, root_message_id, parent_message_id,
                    reply_to_message_id, sender_open_id, sender_type,
                    sender_name, message_type, mentioned_bot, body_text,
                    normalized_summary, normalization_version,
                    content_truncated, resource_truncated, media_required,
                    event_create_time,
                    event_create_time_ms, received_at, eligibility_status,
                    reject_reason, reply_task_id, created_at
                )
                select
                    id, event_id, app_id, message_id, chat_id, chat_type,
                    chat_title, thread_id, root_message_id, parent_message_id,
                    reply_to_message_id, sender_open_id, sender_type,
                    sender_name, message_type, mentioned_bot, body_text,
                    normalized_summary, normalization_version,
                    content_truncated, resource_truncated, media_required,
                    event_create_time,
                    event_create_time_ms, received_at, eligibility_status,
                    reject_reason, reply_task_id, created_at
                from feishu_events;
                drop table feishu_events;
                alter table feishu_events_identity_migration rename to feishu_events;
                create index idx_feishu_events_context
                    on feishu_events(app_id, chat_id, eligibility_status,
                                     event_create_time, id);
                create index idx_feishu_events_thread_context
                    on feishu_events(
                        app_id, chat_id, thread_id, eligibility_status,
                        event_create_time, id
                    );
                create index idx_feishu_events_reply_task
                    on feishu_events(reply_task_id);
                create index idx_feishu_events_retention
                    on feishu_events(created_at, id);
                create index idx_feishu_events_thread_asof
                    on feishu_events(
                        app_id, chat_id, thread_id, eligibility_status,
                        event_create_time_ms, id
                    );
                create index idx_feishu_events_root_asof
                    on feishu_events(
                        app_id, chat_id, root_message_id, eligibility_status,
                        event_create_time_ms, id
                    );
                commit;
                """
            )
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.execute("pragma foreign_keys=on")
        violations = db.execute("pragma foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                "feishu_events migration broke foreign keys"
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
                    f"pragma index_info('{index['name']}')"
                ).fetchall()
            )
            for index in db.execute("pragma index_list(reply_tasks)").fetchall()
            if index["unique"]
        }
        if ("conversation_id", "trigger_message_id") not in unique_columns:
            return

        # ``PRAGMA foreign_keys`` is a no-op inside a transaction.  Earlier
        # resumable migrations may have issued DML, and ``executescript`` would
        # otherwise commit that transaction only *after* this PRAGMA, leaving
        # FK enforcement on while the referenced parent table is replaced.
        # This migration already owns an explicit transaction below, so make
        # the boundary deterministic before disabling FK enforcement.
        if db.in_transaction:
            db.commit()
        db.execute("pragma foreign_keys=off")
        if db.execute("pragma foreign_keys").fetchone()[0] != 0:
            raise sqlite3.OperationalError(
                "could not disable foreign keys for reply_tasks migration"
            )
        try:
            db.executescript(
                """
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
                    trigger_message_json text not null default '{}',
                    available_at text not null default '',
                    force_new_decision integer not null default 0,
                    oa_url text not null default '',
                    manual_rerun_attempt_id integer not null default 0,
                    execution_generation text not null default 'initial',
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    lease_token text not null default '',
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
                    execution_generation, status, attempts, lease_token,
                    locked_at, error,
                    created_at, updated_at
                )
                select
                    id, channel, conversation_id, conversation_title, single_chat,
                    trigger_message_id, trigger_create_time, trigger_sender,
                    trigger_text, trigger_message_json, available_at,
                    force_new_decision, oa_url, manual_rerun_attempt_id,
                    execution_generation, status, attempts, lease_token,
                    locked_at, error,
                    created_at, updated_at
                from reply_tasks;
                drop table reply_tasks;
                alter table reply_tasks_channel_migration rename to reply_tasks;
                create index idx_reply_tasks_status on reply_tasks(status, id);
                commit;
                """
            )
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.execute("pragma foreign_keys=on")
        violations = db.execute("pragma foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError("reply_tasks migration broke foreign keys")

    @staticmethod
    def _legacy_feishu_cli_task_matches(row: sqlite3.Row, payload: object) -> bool:
        """Recognize the exact ChannelMessage envelope emitted by the old CLI.

        Matching the persisted task columns as well as the complete JSON shape
        prevents a malformed or official Bot trigger from being reclassified
        merely because it happens to contain one similarly named field.
        """
        if not isinstance(payload, dict):
            return False
        if frozenset(payload) != _LEGACY_FEISHU_CLI_TASK_KEYS:
            return False
        string_fields = (
            "channel",
            "conversation_id",
            "conversation_title",
            "conversation_type",
            "message_id",
            "sent_at",
            "sender_display",
            "text",
        )
        if any(not isinstance(payload.get(name), str) for name in string_fields):
            return False
        if payload["channel"] != "feishu":
            return False
        if payload["conversation_type"] not in {"direct", "group", "unknown"}:
            return False
        if not all(
            payload[name].strip()
            for name in (
                "conversation_id",
                "message_id",
                "sent_at",
                "sender_display",
            )
        ):
            return False
        if not isinstance(payload["raw_json"], dict):
            return False
        if bool(row["single_chat"]) != (payload["conversation_type"] == "direct"):
            return False
        return all(
            payload[payload_name] == row[column_name]
            for payload_name, column_name in (
                ("conversation_id", "conversation_id"),
                ("conversation_title", "conversation_title"),
                ("message_id", "trigger_message_id"),
                ("sent_at", "trigger_create_time"),
                ("sender_display", "trigger_sender"),
                ("text", "trigger_text"),
            )
        )

    @classmethod
    def _migrate_legacy_feishu_cli_reply_tasks(
        cls, db: sqlite3.Connection
    ) -> None:
        """Move only provable pre-namespace CLI tasks away from the Bot queue.

        The old diagnostic adapter wrote generic ``ChannelMessage`` envelopes
        under ``channel='feishu'``, which is now reserved for official durable
        Bot events.  Official rows are protected only by durable event/delivery
        ownership.  An unbound normalized-looking trigger is still isolated:
        the official producer creates its event-to-task binding atomically, so
        payload shape alone is not sufficient provenance for an active queue.
        Anything that is not a provable old CLI envelope is likewise isolated
        instead of being guessed into an active queue.  Failed rows are also
        reclassified because stale Codex-lock failures can later be recovered
        into ``pending``; only terminal ``done`` history stays untouched.
        """
        rows = db.execute(
            """
            select tasks.*,
                   exists(
                       select 1 from feishu_events as events
                       where events.reply_task_id=tasks.id
                   ) as has_official_event,
                   exists(
                       select 1 from feishu_deliveries as deliveries
                       where deliveries.reply_task_id=tasks.id
                   ) as has_official_delivery
            from reply_tasks as tasks
            where tasks.channel='feishu'
              and tasks.status in ('pending', 'processing', 'failed')
            order by tasks.id
            """
        ).fetchall()
        for row in rows:
            # Durable official Bot ownership always wins, even if a future
            # payload version happens to add fields resembling the old wrapper.
            if row["has_official_event"] or row["has_official_delivery"]:
                continue
            try:
                payload = json.loads(row["trigger_message_json"] or "")
            except (json.JSONDecodeError, TypeError):
                payload = None

            target_channel = (
                "feishu_cli"
                if cls._legacy_feishu_cli_task_matches(row, payload)
                else f"{_LEGACY_FEISHU_CLI_QUARANTINE_PREFIX}{row['id']}"
            )
            cursor = db.execute(
                """
                update or ignore reply_tasks
                set channel=?
                where id=? and channel='feishu'
                  and status in ('pending', 'processing', 'failed')
                  and not exists (
                      select 1 from feishu_events
                      where feishu_events.reply_task_id=reply_tasks.id
                  )
                  and not exists (
                      select 1 from feishu_deliveries
                      where feishu_deliveries.reply_task_id=reply_tasks.id
                  )
                """,
                (target_channel, row["id"]),
            )
            if cursor.rowcount or target_channel != "feishu_cli":
                continue
            # A separately enqueued post-upgrade CLI task may already own the
            # destination identity.  Preserve both records while fencing the
            # legacy duplicate away from every active consumer.
            db.execute(
                """
                update reply_tasks
                set channel=?
                where id=? and channel='feishu'
                  and status in ('pending', 'processing', 'failed')
                  and not exists (
                      select 1 from feishu_events
                      where feishu_events.reply_task_id=reply_tasks.id
                  )
                  and not exists (
                      select 1 from feishu_deliveries
                      where feishu_deliveries.reply_task_id=reply_tasks.id
                  )
                """,
                (f"{_LEGACY_FEISHU_CLI_QUARANTINE_PREFIX}{row['id']}", row["id"]),
            )

    @classmethod
    def _migrate_feishu_delivery_attempts(
        cls, db: sqlite3.Connection
    ) -> None:
        """Backfill preview-era deliveries or quarantine unverifiable rows.

        Older preview databases allowed ``attempt_id=0``.  A delivery with a
        trustworthy Feishu task binding receives a dedicated audit attempt.
        Rows whose App/chat/message identity cannot be proven are made
        unsendable; an in-flight row becomes ``send_unknown`` because startup
        cannot know whether the remote side effect happened.
        """
        deliveries = db.execute(
            """
            select * from feishu_deliveries
            where attempt_id<=0
            order by id
            """
        ).fetchall()
        for delivery in deliveries:
            task = db.execute(
                "select * from reply_tasks where id=?",
                (delivery["reply_task_id"],),
            ).fetchone()
            try:
                trigger = json.loads(
                    (task["trigger_message_json"] if task is not None else "")
                    or "{}"
                )
            except json.JSONDecodeError:
                trigger = None
            expected_conversation_id = cls._feishu_task_conversation_id(
                delivery["app_id"], delivery["chat_id"]
            )
            task_binding_valid = bool(
                task is not None
                and task["channel"] == "feishu"
                and task["conversation_id"]
                in {expected_conversation_id, delivery["chat_id"]}
            )
            if not task_binding_valid:
                raise sqlite3.IntegrityError(
                    "legacy Feishu delivery has no trustworthy reply task"
                )
            target_binding_valid = bool(
                isinstance(trigger, dict)
                and trigger.get("app_id") == delivery["app_id"]
                and trigger.get("chat_id") == delivery["chat_id"]
                and trigger.get("message_id")
                == delivery["reply_to_message_id"]
                and task["trigger_message_id"]
                == delivery["reply_to_message_id"]
            )
            previous_status = delivery["status"]
            next_status = previous_status
            next_error_code = delivery["error_code"]
            next_error = delivery["error"]
            if not target_binding_valid and previous_status in {
                "ready_to_send",
                "retry",
            }:
                next_status = "failed"
                next_error_code = "missing_attempt_audit"
                next_error = "legacy_delivery_identity_unverifiable"
            elif previous_status == "sending":
                next_status = "send_unknown"
                next_error_code = (
                    "legacy_identity_unverifiable"
                    if not target_binding_valid
                    else "unknown"
                )
                next_error = (
                    "legacy_delivery_identity_unverifiable"
                    if not target_binding_valid
                    else "legacy_sending_requires_review"
                )
            elif not target_binding_valid and previous_status == "send_unknown":
                next_error_code = "legacy_identity_unverifiable"
                next_error = "legacy_delivery_identity_unverifiable"
            if (
                next_status != previous_status
                or next_error_code != delivery["error_code"]
                or next_error != delivery["error"]
            ):
                db.execute(
                    """
                    update feishu_deliveries set status=?, error_code=?, error=?,
                        locked_at='', approved_at='', approved_by='',
                        updated_at=current_timestamp
                    where id=? and attempt_id<=0
                    """,
                    (
                        next_status,
                        next_error_code,
                        next_error,
                        delivery["id"],
                    ),
                )
            attempt_status = cls._feishu_attempt_send_status(next_status)
            cursor = db.execute(
                """
                insert into reply_attempts (
                    conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    draft_reply_text, final_reply_text, send_status,
                    send_error, retry_count, channel
                ) values (?, ?, ?, ?, ?, 'send_reply', 'general', ?, ?, ?, ?, ?,
                          'feishu')
                """,
                (
                    task["conversation_id"],
                    task["conversation_title"],
                    task["trigger_message_id"],
                    task["trigger_sender"],
                    task["trigger_text"],
                    delivery["reply_text"],
                    delivery["reply_text"],
                    attempt_status,
                    next_error,
                    max(0, int(delivery["attempts"] or 0) - 1),
                ),
            )
            attempt_id = int(cursor.lastrowid)
            db.execute(
                """
                update feishu_deliveries
                set attempt_id=?, updated_at=current_timestamp
                where id=? and attempt_id<=0
                """,
                (attempt_id, delivery["id"]),
            )
            cls._append_feishu_audit_event(
                db,
                app_id=delivery["app_id"],
                entity_type="reply_attempt",
                entity_id=attempt_id,
                event_type="legacy_attempt_backfilled",
                new_state=attempt_status,
                actor="schema-migration",
            )
            cls._append_feishu_audit_event(
                db,
                app_id=delivery["app_id"],
                entity_type="delivery",
                entity_id=delivery["id"],
                event_type=(
                    "legacy_attempt_bound"
                    if target_binding_valid
                    else "legacy_attempt_quarantined"
                ),
                previous_state=previous_status,
                new_state=next_status,
                actor="schema-migration",
                detail=(
                    ""
                    if target_binding_valid
                    else "error_code=legacy_identity_unverifiable"
                ),
            )

    @classmethod
    def _quarantine_feishu_delivery_binding(
        cls,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        actor: str,
    ) -> bool:
        """Make an invalid active delivery unsendable without external I/O."""
        previous_status = row["status"]
        if previous_status not in FEISHU_DELIVERY_STATUSES:
            return False
        if previous_status in {"ready_to_send", "retry"}:
            next_status = "failed"
        elif previous_status in {"sending", "send_unknown"}:
            # A pre-existing sending/unknown row may already have caused a
            # remote side effect.  Preserve uncertainty for reconciliation.
            next_status = "send_unknown"
        else:
            # Preserve an already-terminal external fact.  The binding defect
            # is still made explicit in the row and immutable audit trail.
            next_status = previous_status
        existing = db.execute(
            """
            select 1 from feishu_audit_events
            where app_id=? and entity_type='delivery' and entity_id=?
              and event_type='invalid_binding_quarantined'
            limit 1
            """,
            (row["app_id"], str(row["id"])),
        ).fetchone()
        db.execute(
            """
            update feishu_deliveries
            set status=?, error_code='legacy_identity_unverifiable',
                error='delivery_identity_unverifiable', locked_at='',
                lease_token='', approved_at='', approved_by='',
                updated_at=current_timestamp
            where id=?
              and (
                status<>? or error_code<>'legacy_identity_unverifiable'
                or error<>'delivery_identity_unverifiable' or locked_at<>''
                or lease_token<>'' or approved_at<>'' or approved_by<>''
              )
            """,
            (next_status, row["id"], next_status),
        )
        quarantined = db.execute(
            "select * from feishu_deliveries where id=?", (row["id"],)
        ).fetchone()
        try:
            # Keep a still-trustworthy attempt in the same terminal/uncertain
            # state as its delivery.  A missing or cross-task attempt is not
            # trustworthy enough to mutate and remains evidence for repair.
            cls._sync_feishu_attempt_from_delivery(db, quarantined)
        except ValueError:
            pass
        if existing is None:
            cls._append_feishu_audit_event(
                db,
                app_id=row["app_id"],
                entity_type="delivery",
                entity_id=row["id"],
                event_type="invalid_binding_quarantined",
                previous_state=previous_status,
                new_state=next_status,
                actor=actor,
                detail="error_code=legacy_identity_unverifiable",
            )
        return True

    @classmethod
    def _migrate_feishu_delivery_bindings(
        cls, db: sqlite3.Connection
    ) -> None:
        """Repair attempt links, then quarantine old target misbindings."""
        rows = db.execute(
            "select * from feishu_deliveries order by id"
        ).fetchall()
        for row in rows:
            try:
                cls._validate_feishu_delivery_binding(
                    db, row, require_target_identity=False
                )
            except ValueError:
                task = db.execute(
                    "select * from reply_tasks where id=?",
                    (row["reply_task_id"],),
                ).fetchone()
                if task is None or task["channel"] != "feishu":
                    raise sqlite3.IntegrityError(
                        "Feishu delivery has no trustworthy reply task"
                    )
                attempt_status = cls._feishu_attempt_send_status(row["status"])
                cursor = db.execute(
                    """
                    insert into reply_attempts (
                        conversation_id, conversation_title,
                        trigger_message_id, trigger_sender, trigger_text,
                        action, sensitivity_kind, draft_reply_text,
                        final_reply_text, send_status, send_error, retry_count,
                        channel
                    ) values (?, ?, ?, ?, ?, 'send_reply', 'general', ?, ?, ?,
                              ?, ?, 'feishu')
                    """,
                    (
                        task["conversation_id"],
                        task["conversation_title"],
                        task["trigger_message_id"],
                        task["trigger_sender"],
                        task["trigger_text"],
                        row["reply_text"],
                        row["reply_text"],
                        attempt_status,
                        row["error"],
                        max(0, int(row["attempts"] or 0) - 1),
                    ),
                )
                replacement_attempt_id = int(cursor.lastrowid)
                db.execute(
                    """
                    update feishu_deliveries
                    set attempt_id=?, updated_at=current_timestamp
                    where id=?
                    """,
                    (replacement_attempt_id, row["id"]),
                )
                cls._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="reply_attempt",
                    entity_id=replacement_attempt_id,
                    event_type="legacy_attempt_rebound",
                    new_state=attempt_status,
                    actor="schema-migration",
                )
                row = db.execute(
                    "select * from feishu_deliveries where id=?",
                    (row["id"],),
                ).fetchone()
                expected_chunks, chunk_plan_sha256, approval_hash = (
                    cls._feishu_delivery_approval_values(row)
                )
                db.execute(
                    """
                    update feishu_deliveries
                    set expected_chunks=?, chunk_plan_sha256=?, approval_hash=?,
                        approved_at='', approved_by='',
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (
                        expected_chunks,
                        chunk_plan_sha256,
                        approval_hash,
                        row["id"],
                    ),
                )
                cls._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="delivery",
                    entity_id=row["id"],
                    event_type="approval_snapshot_migrated",
                    previous_state=row["status"],
                    new_state=row["status"],
                    actor="schema-migration",
                    detail="attempt_rebound=1;approval_invalidated=1",
                )
                row = db.execute(
                    "select * from feishu_deliveries where id=?",
                    (row["id"],),
                ).fetchone()
            try:
                cls._validate_feishu_delivery_binding(
                    db, row, require_target_identity=True
                )
            except ValueError:
                cls._quarantine_feishu_delivery_binding(
                    db, row, actor="schema-migration"
                )
            else:
                # Preview databases could transition only the delivery row.
                # Converge every trustworthy legacy attempt to the delivery's
                # durable state during startup.
                cls._sync_feishu_attempt_from_delivery(db, row)

    @classmethod
    def _feishu_delivery_approval_values(
        cls,
        row: sqlite3.Row,
        *,
        review_generation: int | None = None,
    ) -> tuple[int, str, str]:
        mentions = tuple(json.loads(row["mention_open_ids_json"] or "[]"))
        payload = FeishuReplyPayload(
            kind=row["reply_format"] or "text",
            text=row["reply_text"],
            mention_open_ids=mentions,
        )
        chunks = split_reply_payload(payload)
        expected_chunks = len(chunks)
        chunk_plan_sha256 = delivery_chunk_plan_sha256(chunks)
        generation = (
            int(row["review_generation"])
            if review_generation is None
            else review_generation
        )
        return expected_chunks, chunk_plan_sha256, delivery_approval_hash(
            reply_task_id=int(row["reply_task_id"]),
            attempt_id=int(row["attempt_id"]),
            app_id=row["app_id"],
            chat_id=row["chat_id"],
            reply_to_message_id=row["reply_to_message_id"],
            reply_in_thread=bool(row["reply_in_thread"]),
            payload_sha256=row["payload_sha256"],
            idempotency_key=row["idempotency_key"],
            expected_chunks=expected_chunks,
            chunk_plan_sha256=chunk_plan_sha256,
            review_generation=generation,
        )

    @classmethod
    def _migrate_feishu_action_approval_identity(
        cls, db: sqlite3.Connection
    ) -> None:
        """Bind legacy action approvals to a persisted review generation."""
        db.execute(
            "drop trigger if exists feishu_message_actions_identity_immutable"
        )
        for row in db.execute(
            "select * from feishu_message_actions order by id"
        ).fetchall():
            try:
                generation = int(row["review_generation"] or 0)
                if generation <= 0:
                    generation = 1
                approval_hash = action_approval_hash(
                    reply_task_id=int(row["reply_task_id"]),
                    attempt_id=int(row["attempt_id"]),
                    app_id=row["app_id"],
                    chat_id=row["chat_id"],
                    action_key=row["action_key"],
                    kind=row["kind"],
                    target_id=(
                        row["target_message_id"] or row["target_open_id"]
                    ),
                    payload_sha256=row["payload_sha256"],
                    idempotency_key=row["idempotency_key"],
                    risk=row["risk"],
                    review_generation=generation,
                )
            except Exception as exc:
                raise sqlite3.IntegrityError(
                    "legacy Feishu action approval identity is invalid"
                ) from exc
            if (
                int(row["review_generation"] or 0) != generation
                or row["approval_hash"] != approval_hash
            ):
                db.execute(
                    """
                    update feishu_message_actions
                    set review_generation=?, approval_hash=?,
                        approved_at='', approved_by='',
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (generation, approval_hash, row["id"]),
                )
                cls._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="message_action",
                    entity_id=row["id"],
                    event_type="approval_snapshot_migrated",
                    previous_state=row["status"],
                    new_state=row["status"],
                    actor="schema-migration",
                    detail=(
                        f"review_generation={generation};"
                        "approval_invalidated=1"
                    ),
                )
        db.execute(
            """
            create trigger feishu_message_actions_identity_immutable
            before update on feishu_message_actions
            when old.reply_task_id<>new.reply_task_id
              or old.attempt_id<>new.attempt_id
              or old.app_id<>new.app_id
              or old.chat_id<>new.chat_id
              or old.action_key<>new.action_key
              or old.kind<>new.kind
              or old.target_message_id<>new.target_message_id
              or old.target_open_id<>new.target_open_id
              or old.payload_json<>new.payload_json
              or old.payload_sha256<>new.payload_sha256
              or old.idempotency_key<>new.idempotency_key
              or old.risk<>new.risk
              or (
                old.status='failed'
                and old.error_code='verified_not_applied'
                and new.status='retry'
                and not (
                  new.error_code=''
                  and new.review_generation=old.review_generation + 1
                  and new.approval_hash<>old.approval_hash
                  and new.approved_at='' and new.approved_by=''
                )
              )
              or (
                (
                  old.review_generation<>new.review_generation
                  or old.approval_hash<>new.approval_hash
                )
                and not (
                  old.status='failed'
                  and old.error_code='verified_not_applied'
                  and new.status='retry' and new.error_code=''
                  and new.review_generation=old.review_generation + 1
                  and new.approval_hash<>old.approval_hash
                  and new.approved_at='' and new.approved_by=''
                )
              )
            begin
                select raise(abort, 'Feishu message action identity is immutable');
            end
            """
        )

    @classmethod
    def _migrate_feishu_delivery_approval_identity(
        cls, db: sqlite3.Connection, *, create_trigger: bool
    ) -> None:
        """Backfill the immutable chunk plan and approval preview hash."""
        db.execute("drop trigger if exists feishu_deliveries_identity_immutable")
        for row in db.execute(
            "select * from feishu_deliveries order by id"
        ).fetchall():
            try:
                expected_chunks, chunk_plan_sha256, approval_hash = (
                    cls._feishu_delivery_approval_values(row)
                )
            except Exception as exc:
                raise sqlite3.IntegrityError(
                    "legacy Feishu delivery approval identity is invalid"
                ) from exc
            if (
                int(row["expected_chunks"] or 0) != expected_chunks
                or row["chunk_plan_sha256"] != chunk_plan_sha256
                or row["approval_hash"] != approval_hash
            ):
                persisted_plan_hash = str(
                    row["chunk_plan_sha256"] or ""
                ).strip()
                plan_snapshot_changed = (
                    persisted_plan_hash != chunk_plan_sha256
                )
                receipt_count = int(db.execute(
                    """
                    select count(*) from feishu_delivery_receipts
                    where delivery_id=?
                    """,
                    (row["id"],),
                ).fetchone()[0])
                has_receipt = receipt_count > 0
                legacy_sent_would_become_partial = bool(
                    row["status"] == "sent"
                    and expected_chunks > 1
                    and receipt_count < expected_chunks
                )
                unsafe_legacy_resume = bool(
                    plan_snapshot_changed
                    and (
                        (
                            row["status"] in {
                                "ready_to_send",
                                "retry",
                                "sending",
                                "send_unknown",
                                "failed",
                            }
                            and (
                                has_receipt
                                or row["feishu_message_id"]
                                or row["mutation_started_at"]
                            )
                        )
                        or legacy_sent_would_become_partial
                    )
                )
                db.execute(
                    """
                    update feishu_deliveries
                    set expected_chunks=?, chunk_plan_sha256=?, approval_hash=?,
                        status=case when ? then 'send_unknown' else status end,
                        error_code=case
                            when ? then 'legacy_chunk_plan_unverifiable'
                            else error_code end,
                        error=case
                            when ? then 'legacy_chunk_plan_requires_review'
                            else error end,
                        lease_token=case when ? then '' else lease_token end,
                        locked_at=case when ? then '' else locked_at end,
                        approved_at='', approved_by='',
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (
                        expected_chunks,
                        chunk_plan_sha256,
                        approval_hash,
                        *([int(unsafe_legacy_resume)] * 5),
                        row["id"],
                    ),
                )
                cls._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="delivery",
                    entity_id=row["id"],
                    event_type="approval_snapshot_migrated",
                    previous_state=row["status"],
                    new_state=row["status"],
                    actor="schema-migration",
                    detail=(
                        "approval_invalidated=1;chunk_plan_unverifiable=1"
                        if unsafe_legacy_resume
                        else "approval_invalidated=1"
                    ),
                )
                if unsafe_legacy_resume:
                    cls._append_feishu_audit_event(
                        db,
                        app_id=row["app_id"],
                        entity_type="delivery",
                        entity_id=row["id"],
                        event_type="legacy_chunk_plan_quarantined",
                        previous_state=row["status"],
                        new_state="send_unknown",
                        actor="schema-migration",
                        detail="resume_blocked=1",
                    )
        if not create_trigger:
            return
        db.execute(
            """
            create trigger feishu_deliveries_identity_immutable
            before update on feishu_deliveries
            when old.reply_task_id<>new.reply_task_id
              or old.attempt_id<>new.attempt_id
              or old.app_id<>new.app_id
              or old.chat_id<>new.chat_id
              or old.reply_to_message_id<>new.reply_to_message_id
              or old.reply_in_thread<>new.reply_in_thread
              or old.reply_text<>new.reply_text
              or old.reply_format<>new.reply_format
              or old.mention_open_ids_json<>new.mention_open_ids_json
              or old.payload_sha256<>new.payload_sha256
              or old.idempotency_key<>new.idempotency_key
              or old.expected_chunks<>new.expected_chunks
              or old.chunk_plan_sha256<>new.chunk_plan_sha256
              or (
                old.status='failed'
                and old.error_code='verified_not_sent'
                and new.status='retry'
                and not (
                  new.error_code=''
                  and new.review_generation=old.review_generation + 1
                  and new.approval_hash<>old.approval_hash
                  and new.approved_at='' and new.approved_by=''
                )
              )
              or (
                (
                  old.review_generation<>new.review_generation
                  or old.approval_hash<>new.approval_hash
                )
                and not (
                  old.status='failed'
                  and old.error_code='verified_not_sent'
                  and new.status='retry' and new.error_code=''
                  and new.review_generation=old.review_generation + 1
                  and new.approval_hash<>old.approval_hash
                  and new.approved_at='' and new.approved_by=''
                )
              )
            begin
                select raise(abort, 'Feishu delivery identity is immutable');
            end
            """
        )

    @classmethod
    def _migrate_feishu_delivery_receipts(
        cls, db: sqlite3.Connection
    ) -> None:
        """Idempotently bind legacy primary message IDs to receipt ordinal 0."""
        legacy = db.execute(
            """
            select id, app_id, feishu_message_id
            from feishu_deliveries
            where status='sent' and feishu_message_id<>''
            order by id
            """
        ).fetchall()
        for delivery in legacy:
            message_id = str(delivery["feishu_message_id"] or "").strip()
            if (
                not message_id
                or len(message_id) > 512
                or any(ord(character) < 32 for character in message_id)
            ):
                raise sqlite3.IntegrityError(
                    "legacy Feishu delivery message ID is invalid"
                )
            db.execute(
                """
                insert into feishu_delivery_receipts (
                    delivery_id, app_id, ordinal, message_id, status
                ) values (?, ?, 0, ?, 'active')
                on conflict do nothing
                """,
                (delivery["id"], delivery["app_id"], message_id),
            )
            receipt = db.execute(
                """
                select delivery_id, app_id, ordinal, message_id
                from feishu_delivery_receipts
                where delivery_id=? and ordinal=0
                """,
                (delivery["id"],),
            ).fetchone()
            if (
                receipt is None
                or receipt["app_id"] != delivery["app_id"]
                or receipt["message_id"] != message_id
            ):
                raise sqlite3.IntegrityError(
                    "legacy Feishu delivery receipt ownership conflicts"
                )
        incomplete = db.execute(
            """
            select deliveries.*
            from feishu_deliveries as deliveries
            where deliveries.status='sent'
              and deliveries.expected_chunks<>(
                select count(*) from feishu_delivery_receipts as receipts
                where receipts.delivery_id=deliveries.id
              )
            order by deliveries.id
            """
        ).fetchall()
        for delivery in incomplete:
            db.execute(
                """
                update feishu_deliveries
                set status='send_unknown', error_code='unknown',
                    error='legacy_chunk_receipts_incomplete',
                    updated_at=current_timestamp
                where id=? and status='sent'
                """,
                (delivery["id"],),
            )
            updated = db.execute(
                "select * from feishu_deliveries where id=?",
                (delivery["id"],),
            ).fetchone()
            cls._sync_feishu_attempt_from_delivery(db, updated)
            cls._append_feishu_audit_event(
                db,
                app_id=delivery["app_id"],
                entity_type="delivery",
                entity_id=delivery["id"],
                event_type="legacy_chunk_receipts_incomplete",
                previous_state="sent",
                new_state="send_unknown",
                actor="schema-migration",
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
            execution_generation=row["execution_generation"],
            status=row["status"],
            attempts=row["attempts"],
            lease_token=row["lease_token"],
            locked_at=row["locked_at"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _feishu_event_from_row(
        row: sqlite3.Row,
        *,
        inserted: bool = False,
        enqueued: bool | None = None,
    ) -> FeishuEventRecord:
        reply_task_id = int(row["reply_task_id"] or 0)
        return FeishuEventRecord(
            id=row["id"],
            event_id=row["event_id"],
            app_id=row["app_id"],
            message_id=row["message_id"],
            chat_id=row["chat_id"],
            chat_type=row["chat_type"],
            chat_title=row["chat_title"],
            thread_id=row["thread_id"],
            root_message_id=row["root_message_id"],
            parent_message_id=row["parent_message_id"],
            reply_to_message_id=row["reply_to_message_id"],
            sender_open_id=row["sender_open_id"],
            sender_type=row["sender_type"],
            sender_name=row["sender_name"],
            message_type=row["message_type"],
            mentioned_bot=bool(row["mentioned_bot"]),
            body_text=row["body_text"],
            normalized_summary=row["normalized_summary"],
            normalization_version=int(row["normalization_version"]),
            content_truncated=bool(row["content_truncated"]),
            resource_truncated=bool(row["resource_truncated"]),
            media_required=bool(row["media_required"]),
            event_create_time=row["event_create_time"],
            event_create_time_ms=row["event_create_time_ms"],
            received_at=row["received_at"],
            eligibility_status=row["eligibility_status"],
            reject_reason=row["reject_reason"],
            reply_task_id=reply_task_id,
            created_at=row["created_at"],
            inserted=inserted,
            enqueued=(reply_task_id > 0 if enqueued is None else enqueued),
        )

    @staticmethod
    def _feishu_media_asset_from_row(row: sqlite3.Row) -> FeishuMediaAsset:
        return FeishuMediaAsset(
            id=row["id"],
            event_record_id=row["event_record_id"],
            app_id=row["app_id"],
            message_id=row["message_id"],
            ordinal=row["ordinal"],
            resource_type=row["resource_type"],
            role=row["role"],
            file_key=row["file_key"],
            file_key_sha256=row["file_key_sha256"],
            safe_name=row["safe_name"],
            duration_ms=row["duration_ms"],
            status=row["status"],
            lease_token=row["lease_token"],
            relative_path=row["relative_path"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            error_code=row["error_code"],
            error=row["error"],
            locked_at=row["locked_at"],
            ready_at=row["ready_at"],
            purged_at=row["purged_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _feishu_scope_from_row(row: sqlite3.Row) -> FeishuReplyScope:
        return FeishuReplyScope(
            app_id=row["app_id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            display_name=row["display_name"],
            trigger_mode=row["trigger_mode"],
            enabled=bool(row["enabled"]),
            binding_status=row["binding_status"],
            last_seen_at=row["last_seen_at"],
            approved_at=row["approved_at"],
            approved_by=row["approved_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _feishu_delivery_from_row(row: sqlite3.Row) -> FeishuDelivery:
        try:
            mention_open_ids = tuple(
                json.loads(row["mention_open_ids_json"] or "[]")
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("Feishu delivery mentions are invalid") from exc
        return FeishuDelivery(
            id=row["id"],
            reply_task_id=row["reply_task_id"],
            attempt_id=row["attempt_id"],
            app_id=row["app_id"],
            chat_id=row["chat_id"],
            reply_to_message_id=row["reply_to_message_id"],
            reply_in_thread=bool(row["reply_in_thread"]),
            reply_text=row["reply_text"],
            reply_format=row["reply_format"],
            mention_open_ids=mention_open_ids,
            payload_sha256=row["payload_sha256"],
            idempotency_key=row["idempotency_key"],
            expected_chunks=row["expected_chunks"],
            chunk_plan_sha256=row["chunk_plan_sha256"],
            review_generation=row["review_generation"],
            approval_hash=row["approval_hash"],
            status=row["status"],
            feishu_message_id=row["feishu_message_id"],
            request_log_id=row["request_log_id"],
            attempts=row["attempts"],
            remote_failures=row["remote_failures"],
            lease_token=row["lease_token"],
            mutation_started_at=row["mutation_started_at"],
            approved_at=row["approved_at"],
            approved_by=row["approved_by"],
            locked_at=row["locked_at"],
            available_at=row["available_at"],
            error_code=row["error_code"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _feishu_delivery_receipt_from_row(
        row: sqlite3.Row,
    ) -> FeishuDeliveryReceipt:
        return FeishuDeliveryReceipt(
            id=row["id"],
            delivery_id=row["delivery_id"],
            app_id=row["app_id"],
            ordinal=row["ordinal"],
            message_id=row["message_id"],
            request_log_id=row["request_log_id"],
            status=row["status"],
            recall_action_id=row["recall_action_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _feishu_message_action_from_row(
        row: sqlite3.Row,
    ) -> FeishuMessageAction:
        return FeishuMessageAction.model_validate(dict(row))

    @staticmethod
    def _feishu_local_notification_from_row(
        row: sqlite3.Row,
    ) -> FeishuLocalNotification:
        return FeishuLocalNotification.model_validate(dict(row))

    @staticmethod
    def _feishu_audit_event_from_row(row: sqlite3.Row) -> FeishuAuditEvent:
        return FeishuAuditEvent(
            id=row["id"],
            app_id=row["app_id"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            event_type=row["event_type"],
            previous_state=row["previous_state"],
            new_state=row["new_state"],
            actor=row["actor"],
            detail=row["detail"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _append_feishu_audit_event(
        db: sqlite3.Connection,
        *,
        app_id: str,
        entity_type: str,
        entity_id: str | int,
        event_type: str,
        previous_state: str = "",
        new_state: str = "",
        actor: str = "",
        detail: str = "",
    ) -> None:
        """Append sanitized state evidence without copying message payloads."""
        db.execute(
            """
            insert into feishu_audit_events (
                app_id, entity_type, entity_id, event_type,
                previous_state, new_state, actor, detail
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                entity_type,
                str(entity_id),
                event_type,
                previous_state,
                new_state,
                safe_observability_error(actor)[:128],
                safe_observability_error(detail)[:512],
            ),
        )

    @staticmethod
    def _feishu_scope_audit_id(target_type: str, target_id: str) -> str:
        return hashlib.sha256(
            f"{target_type}\0{target_id}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _feishu_task_app_id(task: sqlite3.Row) -> str:
        try:
            trigger = json.loads(task["trigger_message_json"] or "{}")
        except (json.JSONDecodeError, KeyError):
            return "*"
        if not isinstance(trigger, dict):
            return "*"
        return str(trigger.get("app_id") or "*")

    @staticmethod
    def _feishu_attempt_send_status(delivery_status: str) -> str:
        return {
            "ready_to_send": "pending",
            "sending": "processing",
            "retry": "processing",
        }.get(delivery_status, delivery_status)

    @classmethod
    def _validate_feishu_delivery_binding(
        cls,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        require_target_identity: bool,
    ) -> None:
        attempt_id = int(row["attempt_id"] or 0)
        if attempt_id <= 0:
            raise ValueError("Feishu delivery has no durable attempt audit")
        try:
            mention_open_ids = tuple(
                json.loads(row["mention_open_ids_json"] or "[]")
            )
            payload = FeishuReplyPayload(
                kind=row["reply_format"],
                text=row["reply_text"],
                mention_open_ids=mention_open_ids,
            )
        except Exception as exc:
            raise ValueError("Feishu delivery payload contract is invalid") from exc
        if row["payload_sha256"] != payload.sha256():
            raise ValueError("Feishu delivery payload hash does not match")
        chunks = split_reply_payload(payload)
        expected_chunks = len(chunks)
        if int(row["expected_chunks"] or 0) != expected_chunks:
            raise ValueError("Feishu delivery chunk plan does not match payload")
        chunk_plan_sha256 = delivery_chunk_plan_sha256(chunks)
        if row["chunk_plan_sha256"] != chunk_plan_sha256:
            raise ValueError("Feishu delivery chunk boundaries do not match")
        expected_approval_hash = delivery_approval_hash(
            reply_task_id=int(row["reply_task_id"]),
            attempt_id=attempt_id,
            app_id=row["app_id"],
            chat_id=row["chat_id"],
            reply_to_message_id=row["reply_to_message_id"],
            reply_in_thread=bool(row["reply_in_thread"]),
            payload_sha256=row["payload_sha256"],
            idempotency_key=row["idempotency_key"],
            expected_chunks=expected_chunks,
            chunk_plan_sha256=chunk_plan_sha256,
            review_generation=int(row["review_generation"]),
        )
        if row["approval_hash"] != expected_approval_hash:
            raise ValueError("Feishu delivery approval hash does not match")
        binding = db.execute(
            """
            select attempts.channel as attempt_channel,
                   attempts.conversation_id as attempt_conversation_id,
                   attempts.trigger_message_id as attempt_trigger_message_id,
                   tasks.*
            from reply_attempts as attempts
            join reply_tasks as tasks on tasks.id=?
            where attempts.id=?
            """,
            (row["reply_task_id"], attempt_id),
        ).fetchone()
        if binding is None:
            raise ValueError("Feishu delivery attempt audit row is unavailable")
        if (
            binding["attempt_channel"] != "feishu"
            or binding["channel"] != "feishu"
            or binding["attempt_conversation_id"]
            != binding["conversation_id"]
            or binding["attempt_trigger_message_id"]
            != binding["trigger_message_id"]
        ):
            raise ValueError("Feishu delivery attempt does not match reply task")
        if not require_target_identity:
            return
        try:
            trigger = json.loads(binding["trigger_message_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Feishu reply task trigger is invalid") from exc
        expected_conversation_id = cls._feishu_task_conversation_id(
            row["app_id"], row["chat_id"]
        )
        if not (
            isinstance(trigger, dict)
            and trigger.get("app_id") == row["app_id"]
            and trigger.get("chat_id") == row["chat_id"]
            and trigger.get("message_id") == row["reply_to_message_id"]
            and binding["conversation_id"]
            in {expected_conversation_id, row["chat_id"]}
            and binding["trigger_message_id"]
            == row["reply_to_message_id"]
        ):
            raise ValueError(
                "Feishu delivery target identity does not match reply task"
            )

    @classmethod
    def _sync_feishu_attempt_from_delivery(
        cls, db: sqlite3.Connection, row: sqlite3.Row
    ) -> None:
        attempt_id = int(row["attempt_id"] or 0)
        cls._validate_feishu_delivery_binding(
            db, row, require_target_identity=False
        )
        send_status = cls._feishu_attempt_send_status(row["status"])
        send_error = safe_observability_error(row["error"])
        retry_count = max(0, int(row["attempts"] or 0) - 1)
        db.execute(
            """
            update reply_attempts
            set send_status=?, send_error=?, retry_count=?,
                final_reply_text=case
                    when final_reply_text='' then ? else final_reply_text end,
                updated_at=current_timestamp
            where id=? and channel='feishu'
              and (
                send_status<>? or send_error<>? or retry_count<>?
                or (final_reply_text='' and ?<>'')
              )
            """,
            (
                send_status,
                send_error,
                retry_count,
                row["reply_text"],
                attempt_id,
                send_status,
                send_error,
                retry_count,
                row["reply_text"],
            ),
        )

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
    ) -> ReplyTask:
        execution_generation = uuid4().hex
        with self._connect() as db:
            db.execute(
                """
                insert into reply_tasks (
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
                    status,
                    locked_at,
                    error
                )
                values ('dingtalk', ?, ?, ?, ?, ?, ?, ?, ?, '', 1, ?, ?, ?, 'pending', null, ?)
                on conflict(channel, conversation_id, trigger_message_id) do update set
                    conversation_title=excluded.conversation_title,
                    single_chat=excluded.single_chat,
                    trigger_create_time=excluded.trigger_create_time,
                    trigger_sender=excluded.trigger_sender,
                    trigger_text=excluded.trigger_text,
                    trigger_message_json=excluded.trigger_message_json,
                    available_at='',
                    force_new_decision=1,
                    oa_url=excluded.oa_url,
                    manual_rerun_attempt_id=excluded.manual_rerun_attempt_id,
                    execution_generation=excluded.execution_generation,
                    status='pending',
                    locked_at=null,
                    error=excluded.error,
                    updated_at=current_timestamp
                """,
                (
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
                    execution_generation,
                    f"manual_rerun_from_attempt:{attempt_id}",
                ),
            )
            row = db.execute(
                """
                select *
                from reply_tasks
                where channel='dingtalk'
                  and conversation_id=? and trigger_message_id=?
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("manual rerun reply task was not persisted")
            return self._reply_task_from_row(row)

    @staticmethod
    def _canonical_universal_plan_json(plan: UniversalPlan) -> str:
        return json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _universal_plan_execution_from_row(
        row: sqlite3.Row,
    ) -> UniversalPlanExecution:
        plan = UniversalPlan.model_validate_json(row["plan_json"], strict=True)
        return UniversalPlanExecution(
            execution_scope_id=row["execution_scope_id"],
            execution_generation=row["execution_generation"],
            plan=plan,
        )

    def _normalize_universal_plan_execution_targets(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        context: UniversalTaskContext,
    ) -> UniversalPlanExecution:
        plan = UniversalPlan.model_validate_json(row["plan_json"], strict=True)
        normalized = with_context_action_targets(
            plan,
            conversation_id=context.conversation_id,
            trigger_message_id=context.trigger_message_id,
        )
        if normalized == plan:
            return UniversalPlanExecution(
                execution_scope_id=row["execution_scope_id"],
                execution_generation=row["execution_generation"],
                plan=plan,
            )

        plan_json = self._canonical_universal_plan_json(normalized)
        cursor = db.execute(
            """
            update universal_plan_executions
            set plan_json=?, updated_at=current_timestamp
            where execution_scope_id=? and status='active' and plan_json=?
            """,
            (plan_json, row["execution_scope_id"], row["plan_json"]),
        )
        if cursor.rowcount != 1:
            raise ValueError("plan execution changed during target normalization")
        return UniversalPlanExecution(
            execution_scope_id=row["execution_scope_id"],
            execution_generation=row["execution_generation"],
            plan=normalized,
        )

    @staticmethod
    def _validate_reply_task_generation(
        db: sqlite3.Connection,
        task_id: int,
        execution_generation: str,
    ) -> sqlite3.Row:
        if (
            not isinstance(execution_generation, str)
            or not execution_generation.strip()
        ):
            raise ValueError("execution_generation must be non-empty")
        task = db.execute(
            "select * from reply_tasks where id=?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError("reply task not found")
        if task["execution_generation"] != execution_generation:
            raise ValueError("execution generation mismatch")
        return task

    @staticmethod
    def _validate_context_matches_reply_task(
        task: sqlite3.Row,
        context: UniversalTaskContext,
    ) -> None:
        durable_context = (
            task["id"],
            task["conversation_id"],
            task["conversation_title"],
            bool(task["single_chat"]),
            task["trigger_message_id"],
            task["trigger_create_time"],
            task["trigger_sender"],
            task["trigger_text"],
            bool(task["force_new_decision"]),
            task["execution_generation"],
        )
        supplied_context = (
            context.task_id,
            context.conversation_id,
            context.conversation_title,
            context.single_chat,
            context.trigger_message_id,
            context.trigger_create_time,
            context.trigger_sender,
            context.trigger_text,
            context.force_new_decision,
            context.execution_generation,
        )
        if durable_context != supplied_context:
            raise ValueError("task context mismatch")

    @staticmethod
    def _validate_plan_context_identity(
        row: sqlite3.Row,
        context_json: str,
        context_hash: str,
    ) -> None:
        if not row["context_json"] or not row["context_hash"]:
            raise ValueError("legacy plan context missing")
        if row["context_json"] == context_json and row["context_hash"] == context_hash:
            return
        stored_json = row["context_json"]
        if hashlib.sha256(stored_json.encode("utf-8")).hexdigest() != row["context_hash"]:
            raise ValueError("context identity mismatch")
        if hashlib.sha256(context_json.encode("utf-8")).hexdigest() != context_hash:
            raise ValueError("context identity mismatch")
        try:
            stored = json.loads(stored_json)
            current = json.loads(context_json)
        except json.JSONDecodeError as exc:
            raise ValueError("context identity mismatch") from exc
        if not isinstance(stored, dict) or not isinstance(current, dict):
            raise ValueError("context identity mismatch")
        compatible_default_fields = {
            "trusted_mail_mailbox",
            "trusted_mail_message_id",
            "trusted_mail_subject",
            "trusted_calendar_event_id",
            "trusted_calendar_response_status",
            "trusted_calendar_organizer",
        }
        missing = set(current) - set(stored)
        if (
            set(stored) - set(current)
            or not missing
            or not missing <= compatible_default_fields
            or any(current.get(field_name) != "" for field_name in missing)
        ):
            raise ValueError("context identity mismatch")
        normalized_stored = dict(stored)
        normalized_stored.update({field_name: "" for field_name in missing})
        if normalized_stored != current:
            raise ValueError("context identity mismatch")

    def _validate_or_upgrade_plan_context_identity(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        context_json: str,
        context_hash: str,
        trigger_create_time: str,
    ) -> None:
        try:
            self._validate_plan_context_identity(row, context_json, context_hash)
            return
        except ValueError as exc:
            if str(exc) != "context identity mismatch":
                raise
        try:
            stored = json.loads(row["context_json"])
            supplied = json.loads(context_json)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("context identity mismatch") from None
        if not isinstance(stored, dict) or not isinstance(supplied, dict):
            raise ValueError("context identity mismatch")
        if stored.get("trigger_create_time") not in {None, ""}:
            raise ValueError("context identity mismatch")
        supplied_trigger_time = supplied.get("trigger_create_time")
        if supplied_trigger_time != trigger_create_time or not trigger_create_time:
            raise ValueError("context identity mismatch")
        stored_without_time = dict(stored)
        supplied_without_time = dict(supplied)
        stored_without_time.pop("trigger_create_time", None)
        supplied_without_time.pop("trigger_create_time", None)
        if stored_without_time != supplied_without_time:
            raise ValueError("context identity mismatch")
        trigger_messages = [
            message
            for message in supplied.get("context_messages", [])
            if isinstance(message, dict)
            and message.get("open_message_id") == supplied.get("trigger_message_id")
        ]
        known_message_times = {
            message.get("create_time") for message in trigger_messages
            if message.get("create_time")
        }
        if known_message_times and known_message_times != {trigger_create_time}:
            raise ValueError("context identity mismatch")
        cursor = db.execute(
            """
            update universal_plan_executions
            set context_json=?, context_hash=?, updated_at=current_timestamp
            where execution_scope_id=? and status='active'
              and context_json=? and context_hash=?
            """,
            (
                context_json,
                context_hash,
                row["execution_scope_id"],
                row["context_json"],
                row["context_hash"],
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("context identity mismatch")

    def load_universal_plan_execution(
        self,
        context: UniversalTaskContext,
    ) -> UniversalPlanExecution | None:
        context_json = canonical_universal_context_json(context)
        context_hash = universal_context_sha256(context)
        with self._connect() as db:
            db.execute("begin")
            task = self._validate_reply_task_generation(
                db,
                context.task_id,
                context.execution_generation,
            )
            self._validate_context_matches_reply_task(task, context)
            row = db.execute(
                """
                select *
                from universal_plan_executions
                where reply_task_id=? and execution_generation=?
                """,
                (context.task_id, context.execution_generation),
            ).fetchone()
            if row is None:
                return None
            self._validate_or_upgrade_plan_context_identity(
                db,
                row,
                context_json,
                context_hash,
                task["trigger_create_time"],
            )
            return self._normalize_universal_plan_execution_targets(db, row, context)

    def create_universal_plan_execution(
        self,
        context: UniversalTaskContext,
        plan: UniversalPlan,
    ) -> UniversalPlanExecution:
        if not isinstance(plan, UniversalPlan):
            raise TypeError("plan must be UniversalPlan")
        plan = with_context_action_targets(
            plan,
            conversation_id=context.conversation_id,
            trigger_message_id=context.trigger_message_id,
        )
        context_json = canonical_universal_context_json(context)
        context_hash = universal_context_sha256(context)
        plan_json = self._canonical_universal_plan_json(plan)
        with self._connect() as db:
            db.execute("begin immediate")
            task = self._validate_reply_task_generation(
                db,
                context.task_id,
                context.execution_generation,
            )
            self._validate_context_matches_reply_task(task, context)
            existing = db.execute(
                """
                select *
                from universal_plan_executions
                where reply_task_id=? and execution_generation=?
                """,
                (context.task_id, context.execution_generation),
            ).fetchone()
            if existing is not None:
                self._validate_or_upgrade_plan_context_identity(
                    db,
                    existing,
                    context_json,
                    context_hash,
                    task["trigger_create_time"],
                )
                return self._normalize_universal_plan_execution_targets(
                    db, existing, context
                )

            execution_scope_id = uuid4().hex
            db.execute(
                """
                insert into universal_plan_executions (
                    execution_scope_id,
                    reply_task_id,
                    execution_generation,
                    plan_json,
                    context_hash,
                    context_json
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_scope_id,
                    context.task_id,
                    context.execution_generation,
                    plan_json,
                    context_hash,
                    context_json,
                ),
            )
            row = db.execute(
                """
                select *
                from universal_plan_executions
                where execution_scope_id=?
                """,
                (execution_scope_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("universal plan execution was not persisted")
            return self._universal_plan_execution_from_row(row)

    def _validate_universal_action_execution(
        self,
        db: sqlite3.Connection,
        execution: UniversalActionExecution,
    ) -> sqlite3.Row | None:
        if not isinstance(execution, UniversalActionExecution):
            raise TypeError("execution must be UniversalActionExecution")
        plan_row = db.execute(
            """
            select
                universal_plan_executions.*,
                reply_tasks.execution_generation as task_execution_generation,
                reply_tasks.conversation_id as task_conversation_id,
                reply_tasks.conversation_title as task_conversation_title,
                reply_tasks.single_chat as task_single_chat,
                reply_tasks.trigger_message_id as task_trigger_message_id,
                reply_tasks.trigger_create_time as task_trigger_create_time,
                reply_tasks.trigger_sender as task_trigger_sender,
                reply_tasks.trigger_text as task_trigger_text,
                reply_tasks.force_new_decision as task_force_new_decision
            from universal_plan_executions
            join reply_tasks
              on reply_tasks.id=universal_plan_executions.reply_task_id
            where universal_plan_executions.execution_scope_id=?
            """,
            (execution.execution_scope_id,),
        ).fetchone()
        if plan_row is None:
            raise ValueError("execution scope mismatch")
        if plan_row["status"] != "active":
            raise ValueError("plan execution is not active")

        context = execution.context
        if plan_row["reply_task_id"] != context.task_id:
            raise ValueError("task context mismatch")
        if (
            plan_row["execution_generation"] != context.execution_generation
            or plan_row["task_execution_generation"] != context.execution_generation
        ):
            raise ValueError("execution generation mismatch")
        durable_context = (
            plan_row["task_conversation_id"],
            plan_row["task_conversation_title"],
            bool(plan_row["task_single_chat"]),
            plan_row["task_trigger_message_id"],
            plan_row["task_trigger_create_time"],
            plan_row["task_trigger_sender"],
            plan_row["task_trigger_text"],
            bool(plan_row["task_force_new_decision"]),
        )
        supplied_context = (
            context.conversation_id,
            context.conversation_title,
            context.single_chat,
            context.trigger_message_id,
            context.trigger_create_time,
            context.trigger_sender,
            context.trigger_text,
            context.force_new_decision,
        )
        if durable_context != supplied_context:
            raise ValueError("task context mismatch")
        self._validate_or_upgrade_plan_context_identity(
            db,
            plan_row,
            canonical_universal_context_json(context),
            universal_context_sha256(context),
            plan_row["task_trigger_create_time"],
        )

        plan_execution = self._universal_plan_execution_from_row(plan_row)
        if (
            not isinstance(execution.action_index, int)
            or isinstance(execution.action_index, bool)
            or execution.action_index < 0
            or execution.action_index >= len(plan_execution.plan.actions)
        ):
            raise ValueError("action index mismatch")
        planned_action = plan_execution.plan.actions[execution.action_index]
        expected = build_universal_action_execution(
            context,
            plan_execution,
            planned_action,
            execution.action_index,
        )
        expected_action_json = canonical_universal_action_json(planned_action)
        supplied_action_json = canonical_universal_action_json(execution.action)
        if (
            execution.execution_id != expected.execution_id
            or execution.action_hash != expected.action_hash
            or supplied_action_json != expected_action_json
        ):
            raise ValueError("action identity mismatch")

        action_row = db.execute(
            """
            select *
            from universal_action_executions
            where execution_scope_id=? and action_index=?
            """,
            (execution.execution_scope_id, execution.action_index),
        ).fetchone()
        if action_row is None:
            return None
        if (
            action_row["execution_id"] != execution.execution_id
            or action_row["execution_scope_id"] != execution.execution_scope_id
            or action_row["action_index"] != execution.action_index
            or action_row["action_kind"] != execution.action.kind.value
            or action_row["action_hash"] != execution.action_hash
            or action_row["action_json"] != expected_action_json
        ):
            raise ValueError("action identity mismatch")
        return action_row

    def get_universal_action_execution_state(
        self,
        execution: UniversalActionExecution,
    ) -> UniversalActionExecutionState:
        with self._connect() as db:
            db.execute("begin")
            row = self._validate_universal_action_execution(db, execution)
            if row is None or row["status"] == "failed":
                return UniversalActionExecutionState.NOT_STARTED
            if row["status"] == "succeeded":
                return UniversalActionExecutionState.SUCCEEDED
            return UniversalActionExecutionState.UNKNOWN

    def claim_universal_action_execution(
        self,
        execution: UniversalActionExecution,
    ) -> UniversalActionExecutionState:
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._validate_universal_action_execution(db, execution)
            if row is None:
                db.execute(
                    """
                    insert into universal_action_executions (
                        execution_id,
                        execution_scope_id,
                        action_index,
                        action_kind,
                        action_hash,
                        action_json,
                        status,
                        started_at
                    ) values (?, ?, ?, ?, ?, ?, 'started', current_timestamp)
                    """,
                    (
                        execution.execution_id,
                        execution.execution_scope_id,
                        execution.action_index,
                        execution.action.kind.value,
                        execution.action_hash,
                        canonical_universal_action_json(execution.action),
                    ),
                )
                return UniversalActionExecutionState.NOT_STARTED
            if row["status"] == "failed":
                db.execute(
                    """
                    update universal_action_executions
                    set status='started',
                        attempt_id=0,
                        result_json='',
                        error='',
                        started_at=current_timestamp,
                        completed_at='',
                        updated_at=current_timestamp
                    where execution_id=?
                    """,
                    (execution.execution_id,),
                )
                return UniversalActionExecutionState.NOT_STARTED
            if row["status"] == "succeeded":
                return UniversalActionExecutionState.SUCCEEDED
            return UniversalActionExecutionState.UNKNOWN

    def claim_universal_memory_action_execution(
        self,
        execution: UniversalActionExecution,
        canonical_payload_json: str,
    ) -> UniversalMemoryActionClaim:
        if execution.action.kind is not PlannedActionKind.MEMORY_WRITE:
            raise ValueError("memory claim requires a memory_write action")
        try:
            payload = json.loads(canonical_payload_json)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("canonical memory payload must be valid JSON") from None
        if not isinstance(payload, dict) or json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) != canonical_payload_json:
            raise ValueError("canonical memory payload must be canonical JSON")

        with self._connect() as db:
            db.execute("begin immediate")
            row = self._validate_universal_action_execution(db, execution)
            lease_token = uuid4().hex
            if row is None:
                db.execute(
                    """
                    insert into universal_action_executions (
                        execution_id,
                        execution_scope_id,
                        action_index,
                        action_kind,
                        action_hash,
                        action_json,
                        canonical_payload_json,
                        status,
                        started_at,
                        lease_token,
                        lease_expires_at
                    ) values (
                        ?, ?, ?, ?, ?, ?, ?, 'started', current_timestamp, ?,
                        datetime(current_timestamp, '+' || ? || ' seconds')
                    )
                    """,
                    (
                        execution.execution_id,
                        execution.execution_scope_id,
                        execution.action_index,
                        execution.action.kind.value,
                        execution.action_hash,
                        canonical_universal_action_json(execution.action),
                        canonical_payload_json,
                        lease_token,
                        UNIVERSAL_MEMORY_LEASE_SECONDS,
                    ),
                )
                return UniversalMemoryActionClaim(
                    UniversalActionExecutionState.NOT_STARTED,
                    lease_token,
                )
            if row["canonical_payload_json"] != canonical_payload_json:
                raise ValueError("memory payload identity mismatch")
            if row["status"] == "succeeded":
                return UniversalMemoryActionClaim(
                    UniversalActionExecutionState.SUCCEEDED
                )
            if row["status"] == "started" and db.execute(
                "select datetime(?) > current_timestamp",
                (row["lease_expires_at"],),
            ).fetchone()[0]:
                return UniversalMemoryActionClaim(
                    UniversalActionExecutionState.UNKNOWN
                )
            previous_status = row["status"]
            if previous_status not in {"started", "unknown", "failed"}:
                return UniversalMemoryActionClaim(
                    UniversalActionExecutionState.UNKNOWN
                )
            cursor = db.execute(
                """
                update universal_action_executions
                set status='started',
                    result_json='',
                    error='',
                    started_at=current_timestamp,
                    lease_token=?,
                    lease_expires_at=datetime(
                        current_timestamp, '+' || ? || ' seconds'
                    ),
                    completed_at='',
                    updated_at=current_timestamp
                where execution_id=? and status=?
                """,
                (
                    lease_token,
                    UNIVERSAL_MEMORY_LEASE_SECONDS,
                    execution.execution_id,
                    previous_status,
                ),
            )
            if cursor.rowcount != 1:
                return UniversalMemoryActionClaim(
                    UniversalActionExecutionState.UNKNOWN
                )
            return UniversalMemoryActionClaim(
                UniversalActionExecutionState.NOT_STARTED,
                lease_token,
            )

    def complete_universal_memory_action_execution(
        self,
        execution: UniversalActionExecution,
        *,
        canonical_payload_json: str,
        lease_token: str,
        attempt_id: int,
        result_json: str,
    ) -> None:
        self._transition_universal_memory_action_execution(
            execution,
            canonical_payload_json=canonical_payload_json,
            lease_token=lease_token,
            status="succeeded",
            attempt_id=attempt_id,
            result_json=result_json,
            error="",
        )

    def mark_universal_memory_action_execution(
        self,
        execution: UniversalActionExecution,
        *,
        canonical_payload_json: str,
        lease_token: str,
        status: str,
        error: str,
    ) -> None:
        if status not in {"unknown", "failed"}:
            raise ValueError("invalid memory action transition")
        self._transition_universal_memory_action_execution(
            execution,
            canonical_payload_json=canonical_payload_json,
            lease_token=lease_token,
            status=status,
            attempt_id=0,
            result_json="",
            error=error,
        )

    def _transition_universal_memory_action_execution(
        self,
        execution: UniversalActionExecution,
        *,
        canonical_payload_json: str,
        lease_token: str,
        status: str,
        attempt_id: int,
        result_json: str,
        error: str,
    ) -> None:
        if not lease_token:
            raise ValueError("memory action lease token is required")
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._validate_universal_action_execution(db, execution)
            if row is None or row["canonical_payload_json"] != canonical_payload_json:
                raise ValueError("memory payload identity mismatch")
            cursor = db.execute(
                """
                update universal_action_executions
                set status=?, attempt_id=?, result_json=?, error=?,
                    completed_at=case when ?='succeeded' then current_timestamp else '' end,
                    lease_token='', lease_expires_at='', updated_at=current_timestamp
                where execution_id=? and status='started' and lease_token=?
                      and canonical_payload_json=?
                """,
                (
                    status,
                    attempt_id,
                    result_json,
                    error,
                    status,
                    execution.execution_id,
                    lease_token,
                    canonical_payload_json,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("memory action lease ownership mismatch")

    @staticmethod
    def _valid_universal_recovery_checkpoint(
        execution: UniversalActionExecution,
        checkpoint_json: str,
    ) -> bool:
        if not checkpoint_json.strip():
            return False
        try:
            checkpoint = json.loads(checkpoint_json)
        except json.JSONDecodeError:
            return False
        if not isinstance(checkpoint, dict):
            return False
        if execution.action.kind is PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY:
            return (
                isinstance(checkpoint.get("doc_result"), dict)
                and bool(str(checkpoint.get("node_id") or "").strip())
                and bool(str(checkpoint.get("url") or "").strip())
            )
        if (
            execution.action.kind is PlannedActionKind.DWS_MESSAGE_REACTION
            and str(execution.action.payload.get("reaction_type") or "emoji").strip()
            == "text_emotion"
        ):
            create = checkpoint.get("create")
            add_state = str(checkpoint.get("add_state") or "").strip()
            return (
                isinstance(create, dict)
                and isinstance(create.get("result"), dict)
                and create.get("trusted") is True
                and bool(str(create.get("emotion_id") or "").strip())
                and add_state not in {"ambiguous", "started"}
            )
        return False

    def claim_universal_action_execution_recovery(
        self,
        execution: UniversalActionExecution,
        *,
        checkpoint_column: str,
    ) -> tuple[UniversalActionExecutionState, str]:
        if checkpoint_column not in {
            "document_action_result_json",
            "reaction_action_result_json",
        }:
            raise ValueError("unsupported universal recovery checkpoint")
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._validate_universal_action_execution(db, execution)
            attempt = db.execute(
                f"""
                select {checkpoint_column} as checkpoint_json
                from reply_attempts
                where universal_execution_id=?
                """,
                (execution.execution_id,),
            ).fetchone()
            checkpoint_json = (
                str(attempt["checkpoint_json"] or "") if attempt is not None else ""
            )
            has_checkpoint = self._valid_universal_recovery_checkpoint(
                execution,
                checkpoint_json,
            )
            if row is None:
                db.execute(
                    """
                    insert into universal_action_executions (
                        execution_id,
                        execution_scope_id,
                        action_index,
                        action_kind,
                        action_hash,
                        action_json,
                        status,
                        started_at
                    ) values (?, ?, ?, ?, ?, ?, 'started', current_timestamp)
                    """,
                    (
                        execution.execution_id,
                        execution.execution_scope_id,
                        execution.action_index,
                        execution.action.kind.value,
                        execution.action_hash,
                        canonical_universal_action_json(execution.action),
                    ),
                )
                return UniversalActionExecutionState.NOT_STARTED, checkpoint_json
            if row["status"] == "failed":
                db.execute(
                    """
                    update universal_action_executions
                    set status='started',
                        attempt_id=0,
                        result_json='',
                        error='',
                        started_at=current_timestamp,
                        completed_at='',
                        updated_at=current_timestamp
                    where execution_id=? and status='failed'
                    """,
                    (execution.execution_id,),
                )
                return UniversalActionExecutionState.NOT_STARTED, checkpoint_json
            if row["status"] == "succeeded":
                return UniversalActionExecutionState.SUCCEEDED, checkpoint_json
            if row["status"] == "started" and has_checkpoint:
                cursor = db.execute(
                    """
                    update universal_action_executions
                    set status='recovering',
                        started_at=current_timestamp,
                        updated_at=current_timestamp
                    where execution_id=? and status='started'
                    """,
                    (execution.execution_id,),
                )
                if cursor.rowcount == 1:
                    return UniversalActionExecutionState.NOT_STARTED, checkpoint_json
            return UniversalActionExecutionState.UNKNOWN, checkpoint_json

    def complete_universal_action_execution(
        self,
        execution: UniversalActionExecution,
        attempt_id: int = 0,
        result_json: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._validate_universal_action_execution(db, execution)
            if row is None or row["status"] not in {"started", "recovering"}:
                raise ValueError("universal action execution must be started")
            cursor = db.execute(
                """
                update universal_action_executions
                set status='succeeded',
                    attempt_id=?,
                    result_json=?,
                    error='',
                    completed_at=current_timestamp,
                    updated_at=current_timestamp
                where execution_id=? and status in ('started', 'recovering')
                """,
                (attempt_id, result_json, execution.execution_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("universal action execution must be started")

    def mark_universal_action_execution_unknown(
        self,
        execution: UniversalActionExecution,
        error: str,
    ) -> None:
        self._mark_universal_action_execution(
            execution,
            status="unknown",
            error=error,
        )

    def mark_universal_action_execution_failed(
        self,
        execution: UniversalActionExecution,
        error: str,
    ) -> None:
        self._mark_universal_action_execution(
            execution,
            status="failed",
            error=error,
        )

    def _mark_universal_action_execution(
        self,
        execution: UniversalActionExecution,
        *,
        status: str,
        error: str,
    ) -> None:
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._validate_universal_action_execution(db, execution)
            if row is None or row["status"] not in {"started", "recovering"}:
                raise ValueError("universal action execution must be started")
            cursor = db.execute(
                """
                update universal_action_executions
                set status=?,
                    error=?,
                    updated_at=current_timestamp
                where execution_id=? and status in ('started', 'recovering')
                """,
                (status, error, execution.execution_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("universal action execution must be started")

    def claim_reply_tasks(
        self,
        limit: int,
        now: str | None = None,
        *,
        channel: str = "dingtalk",
        feishu_app_id: str = "",
    ) -> list[ReplyTask]:
        if limit <= 0:
            return []
        if feishu_app_id and channel != "feishu":
            raise ValueError("Feishu app claim requires channel=feishu")
        with self._connect() as db:
            db.execute("begin immediate")
            now_expression = "current_timestamp" if now is None else "?"
            args: list[str | int] = [channel]
            app_clause = ""
            if feishu_app_id:
                app_clause = """
                  and exists (
                    select 1 from feishu_events as event
                    where event.reply_task_id=reply_tasks.id and event.app_id=?
                  )
                """
                args.append(feishu_app_id.strip())
            if now is not None:
                args.append(now)
            args.append(limit)
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where status='pending'
                  and channel=?
                  {app_clause}
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
            lease_token = uuid4().hex
            db.execute(
                f"""
                update reply_tasks
                set status='processing',
                    attempts=attempts + 1,
                    lease_token=?,
                    locked_at=current_timestamp,
                    available_at='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                (lease_token, *task_ids),
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

    def reset_stale_processing_reply_tasks(
        self,
        max_age_seconds: int,
        *,
        channel: str = "",
        feishu_app_id: str = "",
    ) -> int:
        if max_age_seconds <= 0:
            return 0
        if feishu_app_id and channel != "feishu":
            raise ValueError("Feishu app reset requires channel=feishu")
        channel_clause = " and channel=?" if channel else ""
        args: list[str] = []
        if channel:
            args.append(channel)
        app_clause = ""
        if feishu_app_id:
            app_clause = """
              and exists (
                select 1 from feishu_events as event
                where event.reply_task_id=reply_tasks.id and event.app_id=?
              )
            """
            args.append(feishu_app_id.strip())
        args.append(f"-{int(max_age_seconds)} seconds")
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where status='processing'
                  {channel_clause}
                  {app_clause}
                  and locked_at is not null
                  and datetime(locked_at) <= datetime('now', ?)
                order by locked_at, id
                """,
                args,
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return 0
            conversation_ids = [row["conversation_id"] for row in rows]
            conversation_placeholders = ",".join("?" for _ in conversation_ids)
            db.execute(
                f"""
                delete from codex_session_locks
                where conversation_id in ({conversation_placeholders})
                """,
                conversation_ids,
            )
            task_placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='pending',
                    lease_token='',
                    locked_at=null,
                    error='',
                    updated_at=current_timestamp
                where id in ({task_placeholders})
                """,
                task_ids,
            )
            return len(task_ids)

    def reset_recoverable_reply_tasks(
        self, *, channel: str = ""
    ) -> list[ReplyTask]:
        channel_clause = " and channel=?" if channel else ""
        args: list[str] = [channel] if channel else []
        args.append(f"-{CODEX_SESSION_LOCK_STALE_SECONDS} seconds")
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where status='failed'
                  {channel_clause}
                  and error like 'codex session locked:%'
                  and not exists (
                      select 1
                      from codex_session_locks
                      where codex_session_locks.conversation_id =
                            reply_tasks.conversation_id
                        and datetime(codex_session_locks.locked_at) >
                            datetime('now', ?)
                  )
                order by updated_at, id
                """,
                args,
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            conversation_ids = [row["conversation_id"] for row in rows]
            conversation_placeholders = ",".join("?" for _ in conversation_ids)
            db.execute(
                f"""
                delete from codex_session_locks
                where conversation_id in ({conversation_placeholders})
                  and datetime(locked_at) <= datetime('now', ?)
                """,
                [
                    *conversation_ids,
                    f"-{CODEX_SESSION_LOCK_STALE_SECONDS} seconds",
                ],
            )
            task_placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='pending',
                    lease_token='',
                    attempts=0,
                    locked_at=null,
                    available_at='',
                    error='',
                    updated_at=current_timestamp
                where id in ({task_placeholders})
                """,
                task_ids,
            )
            return [self._reply_task_from_row(row) for row in rows]

    def reset_processing_reply_tasks(
        self, *, channel: str = ""
    ) -> list[ReplyTask]:
        channel_clause = " and channel=?" if channel else ""
        args: list[str] = [channel] if channel else []
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where status='processing'
                  {channel_clause}
                order by locked_at, id
                """,
                args,
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            conversation_ids = [row["conversation_id"] for row in rows]
            conversation_placeholders = ",".join("?" for _ in conversation_ids)
            db.execute(
                f"""
                delete from codex_session_locks
                where conversation_id in ({conversation_placeholders})
                """,
                conversation_ids,
            )
            placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='pending',
                    lease_token='',
                    locked_at=null,
                    error='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                task_ids,
            )
            return [self._reply_task_from_row(row) for row in rows]

    def list_stale_processing_reply_tasks(
        self, max_age_seconds: int, *, channel: str = ""
    ) -> list[ReplyTask]:
        if max_age_seconds <= 0:
            return []
        channel_clause = " and channel=?" if channel else ""
        args: list[str] = []
        if channel:
            args.append(channel)
        args.append(f"-{int(max_age_seconds)} seconds")
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where status='processing'
                  {channel_clause}
                  and locked_at is not null
                  and datetime(locked_at) <= datetime('now', ?)
                order by locked_at, id
                """,
                args,
            ).fetchall()
            return [self._reply_task_from_row(row) for row in rows]

    def complete_unfinished_reply_tasks_before_trigger(
        self,
        *,
        conversation_id: str,
        trigger_create_time: str,
        exclude_task_id: int,
        channel: str = "dingtalk",
    ) -> list[ReplyTask]:
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select *
                from reply_tasks
                where channel=?
                  and conversation_id=?
                  and status in ('pending', 'processing')
                  and trigger_create_time < ?
                  and id != ?
                order by trigger_create_time, id
                """,
                (channel, conversation_id, trigger_create_time, exclude_task_id),
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='done',
                    lease_token='',
                    locked_at=null,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                task_ids,
            )
            return [self._reply_task_from_row(row) for row in rows]

    def complete_unfinished_reply_tasks_for_messages(
        self,
        *,
        conversation_id: str,
        trigger_message_ids: list[str],
        exclude_task_id: int,
        channel: str = "dingtalk",
    ) -> list[ReplyTask]:
        if not trigger_message_ids:
            return []
        with self._connect() as db:
            db.execute("begin immediate")
            placeholders = ",".join("?" for _ in trigger_message_ids)
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where channel=?
                  and conversation_id=?
                  and status in ('pending', 'processing')
                  and trigger_message_id in ({placeholders})
                  and id != ?
                order by trigger_create_time, id
                """,
                [channel, conversation_id, *trigger_message_ids, exclude_task_id],
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            task_placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='done',
                    lease_token='',
                    locked_at=null,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id in ({task_placeholders})
                """,
                task_ids,
            )
            return [self._reply_task_from_row(row) for row in rows]

    def complete_reply_task(self, task_id: int) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='done',
                    lease_token='',
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id=?
                """,
                (task_id,),
            )

    def complete_processing_reply_task(
        self, task_id: int, *, channel: str, lease_token: str = ""
    ) -> bool:
        """Complete only the exact channel claim that is still active."""
        if not channel.strip():
            raise ValueError("reply task channel must be non-empty")
        lease_clause = " and lease_token=?" if lease_token else ""
        args: list[str | int] = [task_id, channel]
        if lease_token:
            args.append(lease_token)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                update reply_tasks
                set status='done', lease_token='', locked_at=null, error='', available_at='',
                    updated_at=current_timestamp
                where id=? and channel=? and status='processing'
                  {lease_clause}
                """,
                args,
            )
            return cursor.rowcount == 1

    def complete_reply_task_for_message(
        self, conversation_id: str, trigger_message_id: str, *,
        channel: str = "dingtalk",
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set status='done',
                    lease_token='',
                    locked_at=null,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where channel=?
                  and conversation_id=?
                  and trigger_message_id=?
                """,
                (channel, conversation_id, trigger_message_id),
            )
            return cursor.rowcount

    def fail_reply_task(self, task_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='failed',
                    lease_token='',
                    error=?,
                    available_at='',
                    updated_at=current_timestamp
                where id=?
                """,
                (error, task_id),
            )

    def fail_processing_reply_task(
        self,
        task_id: int,
        error: str,
        *,
        channel: str,
        lease_token: str = "",
        feishu_app_id: str = "",
    ) -> bool:
        """Fail an active channel claim without overwriting a requeue."""
        if not channel.strip():
            raise ValueError("reply task channel must be non-empty")
        if feishu_app_id and channel != "feishu":
            raise ValueError("Feishu app failure requires channel=feishu")
        lease_clause = " and lease_token=?" if lease_token else ""
        app_clause = ""
        args: list[str | int] = [
            safe_observability_error(error),
            task_id,
            channel,
        ]
        if feishu_app_id:
            app_clause = """
              and exists (
                select 1 from feishu_events as event
                where event.reply_task_id=reply_tasks.id and event.app_id=?
              )
            """
            args.append(feishu_app_id.strip())
        if lease_token:
            args.append(lease_token)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                update reply_tasks
                set status='failed', lease_token='', locked_at=null,
                    error=?, available_at='',
                    updated_at=current_timestamp
                where id=? and channel=? and status='processing'
                  {app_clause}
                  {lease_clause}
                """,
                args,
            )
            return cursor.rowcount == 1

    def requeue_reply_task(
        self, task_id: int, error: str, *, available_at: str = ""
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='pending',
                    lease_token='',
                    locked_at=null,
                    available_at=?,
                    error=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (available_at, error, task_id),
            )

    def rotate_reply_task_execution_generation(self, task_id: int) -> str:
        execution_generation = uuid4().hex
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set force_new_decision=1,
                    execution_generation=?,
                    updated_at=current_timestamp
                where id=? and status in ('processing', 'pending')
                """,
                (execution_generation, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("retryable reply task was not found")
        return execution_generation

    def defer_reply_task(
        self, task_id: int, error: str, *, available_at: str = ""
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='pending',
                    lease_token='',
                    attempts=max(attempts - 1, 0),
                    locked_at=null,
                    available_at=?,
                    error=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (available_at, error, task_id),
            )

    def defer_reply_task_for_authorization(
        self, task_id: int, error: str, *, available_at: str = ""
    ) -> None:
        self.defer_reply_task(task_id, error, available_at=available_at)

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

    # ---- Feishu channel: normalized events ----
    @staticmethod
    def _feishu_event_time_ms(value: str) -> int:
        from app.feishu.ingress import event_datetime

        parsed = event_datetime(value)
        return 0 if parsed is None else int(parsed.timestamp() * 1000)

    @staticmethod
    def _feishu_task_conversation_id(app_id: str, chat_id: str) -> str:
        app_namespace = hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:16]
        return f"feishu:{app_namespace}:{chat_id}"

    @staticmethod
    def _feishu_reference_root(row) -> str:
        """Return the app/chat-local reference root used for supersession."""
        return str(
            row["thread_id"] or row["root_message_id"] or row["message_id"]
        )

    @classmethod
    def _cancel_feishu_local_notifications_for_task_db(
        cls,
        db: sqlite3.Connection,
        *,
        reply_task_id: int,
        app_id: str,
        actor: str,
    ) -> int:
        rows = db.execute(
            """
            select * from feishu_local_notifications
            where reply_task_id=? and app_id=?
              and (
                status in ('waiting_remote', 'pending', 'retry')
                or (status='sending' and mutation_started_at='')
              )
            order by id
            """,
            (reply_task_id, app_id),
        ).fetchall()
        cancelled = 0
        for row in rows:
            updated = db.execute(
                """
                update feishu_local_notifications
                set status='cancelled', lease_token='', locked_at='',
                    available_at='', error_code='superseded',
                    error='superseded_by_newer_feishu_trigger',
                    updated_at=current_timestamp
                where id=? and status=?
                  and (?<>'sending' or mutation_started_at='')
                """,
                (row["id"], row["status"], row["status"]),
            )
            if updated.rowcount != 1:
                continue
            cancelled += 1
            cls._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="local_notification",
                entity_id=int(row["id"]),
                event_type="trigger_superseded",
                previous_state=str(row["status"]),
                new_state="cancelled",
                actor=actor,
                detail="error_code=superseded",
            )
        return cancelled

    @classmethod
    def _supersede_pending_feishu_tasks_db(
        cls,
        db: sqlite3.Connection,
        *,
        event_row: sqlite3.Row,
    ) -> None:
        """Terminalize older pending triggers under exactly one reference root."""
        reference_root = cls._feishu_reference_root(event_row)
        rows = db.execute(
            """
            select events.id as event_record_id, events.app_id,
                   events.message_id, events.thread_id,
                   events.root_message_id, events.event_create_time_ms,
                   events.reply_task_id, tasks.status
            from feishu_events as events
            left join reply_tasks as tasks on tasks.id=events.reply_task_id
            where events.app_id=? and events.chat_id=?
              and events.eligibility_status='eligible'
              and coalesce(nullif(events.thread_id, ''),
                           nullif(events.root_message_id, ''),
                           events.message_id)=?
            order by events.event_create_time_ms, events.id
            """,
            (event_row["app_id"], event_row["chat_id"], reference_root),
        ).fetchall()
        if len(rows) <= 1:
            return
        latest = max(
            rows,
            key=lambda candidate: (
                int(candidate["event_create_time_ms"] or 0),
                int(candidate["event_record_id"]),
            ),
        )
        for candidate in rows:
            if int(candidate["event_record_id"]) == int(
                latest["event_record_id"]
            ):
                continue
            if not int(candidate["reply_task_id"] or 0):
                continue
            task_id = int(candidate["reply_task_id"])
            if candidate["status"] == "pending":
                updated = db.execute(
                    """
                    update reply_tasks
                    set status='done', lease_token='', locked_at=null,
                        error='superseded_by_newer_feishu_trigger',
                        available_at='', updated_at=current_timestamp
                    where id=? and channel='feishu' and status='pending'
                    """,
                    (task_id,),
                )
                if updated.rowcount == 1:
                    cls._append_feishu_audit_event(
                        db,
                        app_id=str(candidate["app_id"]),
                        entity_type="reply_task",
                        entity_id=task_id,
                        event_type="trigger_superseded",
                        previous_state="pending",
                        new_state="done",
                        actor="ingress",
                        detail=(
                            "newer_event_record_id="
                            f"{int(latest['event_record_id'])}"
                        ),
                    )

            deliveries = db.execute(
                """
                select deliveries.* from feishu_deliveries as deliveries
                where deliveries.reply_task_id=?
                  and deliveries.status in (
                    'ready_to_send', 'retry', 'sending'
                  )
                  and deliveries.mutation_started_at=''
                  and not exists (
                    select 1 from feishu_delivery_receipts as receipts
                    where receipts.delivery_id=deliveries.id
                  )
                """,
                (task_id,),
            ).fetchall()
            for delivery in deliveries:
                updated = db.execute(
                    """
                    update feishu_deliveries
                    set status='rejected', lease_token='', locked_at='',
                        approved_at='', approved_by='', available_at='',
                        error_code='superseded',
                        error='superseded_by_newer_feishu_trigger',
                        updated_at=current_timestamp
                    where id=?
                      and status in ('ready_to_send', 'retry', 'sending')
                      and mutation_started_at=''
                      and not exists (
                        select 1 from feishu_delivery_receipts as receipts
                        where receipts.delivery_id=feishu_deliveries.id
                      )
                    """,
                    (delivery["id"],),
                )
                if updated.rowcount != 1:
                    continue
                rejected = db.execute(
                    "select * from feishu_deliveries where id=?",
                    (delivery["id"],),
                ).fetchone()
                cls._sync_feishu_attempt_from_delivery(db, rejected)
                cls._append_feishu_audit_event(
                    db,
                    app_id=str(candidate["app_id"]),
                    entity_type="delivery",
                    entity_id=int(delivery["id"]),
                    event_type="trigger_superseded",
                    previous_state=str(delivery["status"]),
                    new_state="rejected",
                    actor="ingress",
                )

            actions = db.execute(
                """
                select * from feishu_message_actions
                where reply_task_id=?
                  and status in ('ready', 'retry', 'sending')
                  and kind<>'recall_message'
                  and mutation_started_at='' and remote_id=''
                """,
                (task_id,),
            ).fetchall()
            for action in actions:
                updated = db.execute(
                    """
                    update feishu_message_actions
                    set status='rejected', lease_token='', locked_at='',
                        approved_at='', approved_by='', available_at='',
                        error_code='superseded',
                        error='superseded_by_newer_feishu_trigger',
                        updated_at=current_timestamp
                    where id=? and status in ('ready', 'retry', 'sending')
                      and kind<>'recall_message'
                      and mutation_started_at='' and remote_id=''
                    """,
                    (action["id"],),
                )
                if updated.rowcount != 1:
                    continue
                cls._append_feishu_audit_event(
                    db,
                    app_id=str(candidate["app_id"]),
                    entity_type="message_action",
                    entity_id=int(action["id"]),
                    event_type="trigger_superseded",
                    previous_state=str(action["status"]),
                    new_state="rejected",
                    actor="ingress",
                )
            cls._cancel_feishu_local_notifications_for_task_db(
                db,
                reply_task_id=task_id,
                app_id=str(candidate["app_id"]),
                actor="ingress",
            )

    @staticmethod
    def _enqueue_feishu_event_row(
        db: sqlite3.Connection, row: sqlite3.Row
    ) -> int:
        """Attach one eligible event to its channel-isolated reply task."""
        if row["eligibility_status"] != "eligible":
            return int(row["reply_task_id"] or 0)
        if row["reply_task_id"]:
            return int(row["reply_task_id"])
        if not row["body_text"].strip():
            return 0
        if bool(row["media_required"]) and not AutoReplyStore._feishu_media_event_ready_db(
            db,
            event_record_id=int(row["id"]),
            app_id=str(row["app_id"]),
            message_id=str(row["message_id"]),
        ):
            # Resource-bearing turns are never attachable until a non-empty,
            # complete terminal asset set is durable. This also fail-closes
            # manual ``produce-once`` against legacy/corrupted orphan rows.
            return 0
        task_conversation_id = AutoReplyStore._feishu_task_conversation_id(
            row["app_id"], row["chat_id"]
        )

        trigger_message = {
            "event_id": row["event_id"],
            "app_id": row["app_id"],
            "message_id": row["message_id"],
            "chat_id": row["chat_id"],
            "chat_type": row["chat_type"],
            "chat_title": row["chat_title"],
            "thread_id": row["thread_id"],
            "root_message_id": row["root_message_id"],
            "parent_message_id": row["parent_message_id"],
            "reply_to_message_id": row["reply_to_message_id"],
            "sender_open_id": row["sender_open_id"],
            "sender_type": row["sender_type"],
            "sender_name": row["sender_name"],
            "message_type": row["message_type"],
            "mentioned_bot": bool(row["mentioned_bot"]),
            "body_text": row["body_text"],
            "normalized_summary": row["normalized_summary"],
            "normalization_version": int(row["normalization_version"]),
            "content_truncated": bool(row["content_truncated"]),
            "resource_truncated": bool(row["resource_truncated"]),
            "media_required": bool(row["media_required"]),
            "event_create_time": row["event_create_time"],
            "received_at": row["received_at"],
        }
        db.execute(
            """
            insert or ignore into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, trigger_message_json
            ) values ('feishu', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_conversation_id,
                row["chat_title"] or row["chat_id"],
                int(row["chat_type"] == "p2p"),
                row["message_id"],
                row["event_create_time"],
                row["sender_name"] or row["sender_open_id"],
                row["body_text"],
                json.dumps(
                    trigger_message,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        task = db.execute(
            """
            select id from reply_tasks
            where channel='feishu'
              and conversation_id=? and trigger_message_id=?
            """,
            (task_conversation_id, row["message_id"]),
        ).fetchone()
        if task is None:
            raise RuntimeError("eligible Feishu event was not enqueued")
        task_id = int(task["id"])
        db.execute(
            """
            update feishu_events set reply_task_id=?
            where id=? and reply_task_id is null
            """,
            (task_id, row["id"]),
        )
        attached_row = db.execute(
            "select * from feishu_events where id=?", (row["id"],)
        ).fetchone()
        AutoReplyStore._supersede_pending_feishu_tasks_db(
            db, event_row=attached_row
        )
        return task_id

    def record_feishu_event(
        self,
        message: FeishuInboundMessage,
        *,
        eligibility_status: str,
        reject_reason: str = "",
        store_body: bool = False,
        enqueue_eligible: bool = True,
        normalization_version: int = 1,
        content_truncated: bool = False,
        resource_truncated: bool = False,
        media_candidates=None,
        media_max_event_resources: int = DEFAULT_MAX_EVENT_RESOURCES,
    ) -> FeishuEventRecord:
        """Idempotently record an event and, when eligible, enqueue it atomically.

        The first observation is immutable.  A repeated ``(app_id,
        message_id)`` returns the original row and never stores a later
        payload.  ``event_id`` is audit evidence, not an identity key.  An
        eligible event initially recorded in receive-only mode may be attached
        to its reply task by a later duplicate call.
        """
        if not eligibility_status.strip():
            raise ValueError("eligibility_status must be non-empty")
        if eligibility_status == "eligible" and not store_body:
            raise ValueError("eligible Feishu events must persist normalized body")
        if normalization_version != 1:
            raise ValueError("unsupported Feishu normalization version")
        media_required = media_candidates is not None
        normalized_media = (
            self._normalize_feishu_media_candidates(
                media_candidates,
                max_event_resources=media_max_event_resources,
            )
            if media_required
            else []
        )
        if media_required and (
            eligibility_status != "eligible" or enqueue_eligible
        ):
            raise ValueError(
                "Feishu media events must be eligible and recorded receive-only"
            )
        received_at = message.received_at or datetime.now().astimezone().isoformat()
        body_text = message.body_text if store_body else ""
        event_create_time_ms = self._feishu_event_time_ms(
            message.event_create_time
        )
        if eligibility_status == "eligible" and event_create_time_ms <= 0:
            raise ValueError("eligible Feishu event requires valid create_time")
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                """
                select * from feishu_events
                where app_id=? and message_id=?
                """,
                (message.app_id, message.message_id),
            ).fetchone()
            if row is not None:
                if media_required and bool(row["media_required"]):
                    self._insert_feishu_media_assets_db(
                        db,
                        event_record_id=int(row["id"]),
                        app_id=message.app_id,
                        message_id=message.message_id,
                        normalized=normalized_media,
                        actor="ingress",
                    )
                if enqueue_eligible and row["eligibility_status"] == "eligible":
                    self._enqueue_feishu_event_row(db, row)
                    row = db.execute(
                        "select * from feishu_events where id=?", (row["id"],)
                    ).fetchone()
                return self._feishu_event_from_row(row)

            cursor = db.execute(
                """
                insert into feishu_events (
                    event_id, app_id, message_id, chat_id, chat_type,
                    chat_title, thread_id, root_message_id, parent_message_id,
                    reply_to_message_id,
                    sender_open_id, sender_type, sender_name, message_type,
                    mentioned_bot, body_text, normalized_summary,
                    normalization_version, content_truncated,
                    resource_truncated, media_required,
                    event_create_time, received_at,
                    event_create_time_ms, eligibility_status, reject_reason
                ) values (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    message.event_id,
                    message.app_id,
                    message.message_id,
                    message.chat_id,
                    message.chat_type,
                    message.chat_title,
                    message.thread_id,
                    message.root_message_id,
                    message.parent_message_id,
                    message.reply_to_message_id,
                    message.sender_open_id,
                    message.sender_type,
                    message.sender_name,
                    message.message_type,
                    int(message.mentioned_bot),
                    body_text,
                    message.normalized_summary if store_body else "",
                    normalization_version,
                    int(content_truncated),
                    int(resource_truncated),
                    int(media_required),
                    message.event_create_time,
                    received_at,
                    event_create_time_ms,
                    eligibility_status,
                    reject_reason,
                ),
            )
            row = db.execute(
                "select * from feishu_events where id=?", (cursor.lastrowid,)
            ).fetchone()
            if media_required:
                self._insert_feishu_media_assets_db(
                    db,
                    event_record_id=int(row["id"]),
                    app_id=message.app_id,
                    message_id=message.message_id,
                    normalized=normalized_media,
                    actor="ingress",
                )
            if enqueue_eligible and eligibility_status == "eligible":
                self._enqueue_feishu_event_row(db, row)
                row = db.execute(
                    "select * from feishu_events where id=?", (cursor.lastrowid,)
                ).fetchone()
            return self._feishu_event_from_row(row, inserted=True)

    # ---- Feishu channel: inbound media assets ----
    @staticmethod
    def _feishu_media_candidate_value(candidate, name: str, default=""):
        if isinstance(candidate, dict):
            return candidate.get(name, default)
        return getattr(candidate, name, default)

    @staticmethod
    def _feishu_media_event_ready_db(
        db: sqlite3.Connection,
        *,
        event_record_id: int,
        app_id: str,
        message_id: str,
    ) -> bool:
        event = db.execute(
            """
            select eligibility_status, reply_task_id, body_text
            from feishu_events
            where id=? and app_id=? and message_id=?
            """,
            (event_record_id, app_id, message_id),
        ).fetchone()
        if (
            event is None
            or event["eligibility_status"] != "eligible"
            or event["reply_task_id"] is not None
            or not event["body_text"].strip()
        ):
            return False
        counts = db.execute(
            """
            select count(*) as total,
                   sum(case when status in ('ready', 'rejected') then 1 else 0 end)
                       as terminal
            from feishu_media_assets
            where event_record_id=? and app_id=? and message_id=?
            """,
            (event_record_id, app_id, message_id),
        ).fetchone()
        return bool(
            counts is not None
            and int(counts["total"] or 0) > 0
            and int(counts["total"] or 0) == int(counts["terminal"] or 0)
        )

    @staticmethod
    def _sanitize_feishu_media_error(error: str, *secrets: str) -> str:
        cleaned = str(error or "")
        for secret in secrets:
            if secret:
                cleaned = cleaned.replace(secret, "[REDACTED]")
        return safe_observability_error(cleaned)[:512]

    @staticmethod
    def _validate_feishu_media_relative_path(
        relative_path: str,
        *,
        app_id: str,
        sha256: str,
    ) -> str:
        cleaned = str(relative_path or "").strip()
        if (
            not cleaned
            or "\\" in cleaned
            or any(ord(char) < 32 or ord(char) == 127 for char in cleaned)
        ):
            raise ValueError("invalid Feishu media relative path")
        path = PurePosixPath(cleaned)
        app_digest = hashlib.sha256(app_id.encode("utf-8")).hexdigest()
        expected = (
            ".ceo-agent",
            "feishu-media",
            app_digest,
            sha256[:2],
            sha256,
        )
        if path.is_absolute() or path.parts != expected:
            raise ValueError("invalid Feishu media relative path")
        return path.as_posix()

    @classmethod
    def _normalize_feishu_media_candidates(
        cls,
        candidates,
        *,
        max_event_resources: int,
    ) -> list[dict[str, object]]:
        if (
            max_event_resources <= 0
            or max_event_resources > DEFAULT_MAX_EVENT_RESOURCES
        ):
            raise ValueError(
                "Feishu media event resource limit must be between 1 and 8"
            )
        materialized = list(candidates)
        if not materialized:
            raise ValueError("Feishu media event requires at least one resource")
        if len(materialized) > max_event_resources:
            raise ValueError("Feishu media event has too many resources")

        normalized: list[dict[str, object]] = []
        ordinals: set[int] = set()
        for candidate in materialized:
            ordinal = int(cls._feishu_media_candidate_value(candidate, "ordinal", -1))
            resource_type = str(
                cls._feishu_media_candidate_value(candidate, "resource_type", "")
                or ""
            ).strip().lower()
            file_key = str(
                cls._feishu_media_candidate_value(candidate, "file_key", "") or ""
            ).strip()
            role = str(
                cls._feishu_media_candidate_value(candidate, "role", "") or ""
            ).strip()
            duration_ms = int(
                cls._feishu_media_candidate_value(candidate, "duration_ms", 0) or 0
            )
            file_name = str(
                cls._feishu_media_candidate_value(candidate, "file_name", "") or ""
            )
            if ordinal < 0 or ordinal > 10_000 or ordinal in ordinals:
                raise ValueError("Feishu media candidate ordinal is invalid")
            ordinals.add(ordinal)
            if resource_type not in FEISHU_MEDIA_RESOURCE_TYPES:
                raise ValueError("Feishu media resource type is unsupported")
            if (
                not file_key
                or len(file_key) > 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in file_key)
            ):
                raise ValueError("Feishu media file key is invalid")
            if (
                len(role) > 64
                or any(ord(char) < 32 or ord(char) == 127 for char in role)
            ):
                raise ValueError("Feishu media role is invalid")
            if duration_ms < 0 or duration_ms > 86_400_000:
                raise ValueError("Feishu media duration is invalid")
            status = "pending"
            error_code = ""
            try:
                safe_name = safe_media_name(
                    file_name, resource_type=resource_type
                )
            except FeishuMediaRejected as exc:
                safe_name = ""
                status = "rejected"
                error_code = exc.error_code
            normalized.append(
                {
                    "ordinal": ordinal,
                    "resource_type": resource_type,
                    "role": role,
                    "file_key": file_key if status == "pending" else "",
                    "file_key_sha256": file_key_sha256(file_key),
                    "safe_name": safe_name,
                    "duration_ms": duration_ms,
                    "status": status,
                    "error_code": error_code,
                }
            )
        return normalized

    @classmethod
    def _insert_feishu_media_assets_db(
        cls,
        db: sqlite3.Connection,
        *,
        event_record_id: int,
        app_id: str,
        message_id: str,
        normalized: list[dict[str, object]],
        actor: str,
    ) -> list[sqlite3.Row]:
        event = db.execute(
            """
            select id, eligibility_status, media_required from feishu_events
            where id=? and app_id=? and message_id=?
            """,
            (event_record_id, app_id, message_id),
        ).fetchone()
        if event is None:
            raise ValueError("Feishu media event identity does not match")
        if event["eligibility_status"] != "eligible" or not bool(
            event["media_required"]
        ):
            raise PermissionError(
                "Feishu media keys require an approved media event"
            )

        existing = db.execute(
            """
            select * from feishu_media_assets
            where event_record_id=? order by ordinal
            """,
            (event_record_id,),
        ).fetchall()
        if existing:
            if len(existing) != len(normalized):
                raise ValueError("Feishu media candidate replay does not match")
            by_ordinal = {int(row["ordinal"]): row for row in existing}
            for candidate in normalized:
                row = by_ordinal.get(int(candidate["ordinal"]))
                if row is None or any(
                    row[field] != candidate[field]
                    for field in (
                        "resource_type",
                        "role",
                        "file_key_sha256",
                        "safe_name",
                        "duration_ms",
                    )
                ):
                    raise ValueError("Feishu media candidate replay does not match")
            return existing

        for candidate in normalized:
            cursor = db.execute(
                """
                insert into feishu_media_assets (
                    event_record_id, app_id, message_id, ordinal,
                    resource_type, role, file_key, file_key_sha256,
                    safe_name, duration_ms, status, error_code, error
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_record_id,
                    app_id,
                    message_id,
                    candidate["ordinal"],
                    candidate["resource_type"],
                    candidate["role"],
                    candidate["file_key"],
                    candidate["file_key_sha256"],
                    candidate["safe_name"],
                    candidate["duration_ms"],
                    candidate["status"],
                    candidate["error_code"],
                    candidate["error_code"],
                ),
            )
            cls._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="media_asset",
                entity_id=cursor.lastrowid,
                event_type="created",
                new_state=str(candidate["status"]),
                actor=actor,
                detail=(
                    f"error_code={candidate['error_code']}"
                    if candidate["error_code"]
                    else ""
                ),
            )
        return db.execute(
            """
            select * from feishu_media_assets
            where event_record_id=? and app_id=? and message_id=?
            order by ordinal
            """,
            (event_record_id, app_id, message_id),
        ).fetchall()

    def insert_feishu_media_assets(
        self,
        event_record_id: int,
        *,
        app_id: str,
        message_id: str,
        candidates,
        max_event_resources: int = DEFAULT_MAX_EVENT_RESOURCES,
        actor: str = "ingress",
    ) -> list[FeishuMediaAsset]:
        """Atomically persist approved resource descriptors without payloads.

        Plaintext file keys are accepted only for an already-approved event and
        are erased as soon as the asset becomes terminal.  Replays validate the
        immutable candidate identity rather than replacing the first record.
        """
        if event_record_id <= 0 or not app_id.strip() or not message_id.strip():
            raise ValueError("Feishu media event identity is incomplete")
        normalized = self._normalize_feishu_media_candidates(
            candidates,
            max_event_resources=max_event_resources,
        )

        with self._connect() as db:
            db.execute("begin immediate")
            rows = self._insert_feishu_media_assets_db(
                db,
                event_record_id=event_record_id,
                app_id=app_id,
                message_id=message_id,
                normalized=normalized,
                actor=actor,
            )
            return [self._feishu_media_asset_from_row(row) for row in rows]

    def list_feishu_media_assets(
        self,
        *,
        event_record_id: int = 0,
        app_id: str = "",
        message_id: str = "",
        statuses: tuple[str, ...] | list[str] | set[str] = (),
        limit: int = 100,
    ) -> list[FeishuMediaAsset]:
        if limit <= 0:
            return []
        if limit > 1000:
            raise ValueError("Feishu media asset limit must not exceed 1000")
        selected = tuple(statuses)
        if any(status not in FEISHU_MEDIA_STATUSES for status in selected):
            raise ValueError("invalid Feishu media status")
        where: list[str] = []
        args: list[str | int] = []
        if event_record_id:
            where.append("event_record_id=?")
            args.append(event_record_id)
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if message_id:
            where.append("message_id=?")
            args.append(message_id)
        if selected:
            placeholders = ",".join("?" for _ in selected)
            where.append(f"status in ({placeholders})")
            args.extend(selected)
        clause = f"where {' and '.join(where)}" if where else ""
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_media_assets {clause}
                order by event_record_id, ordinal, id limit ?
                """,
                args,
            ).fetchall()
        return [self._feishu_media_asset_from_row(row) for row in rows]

    def get_feishu_media_asset(self, asset_id: int) -> FeishuMediaAsset | None:
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_media_assets where id=?", (asset_id,)
            ).fetchone()
        return self._feishu_media_asset_from_row(row) if row is not None else None

    def claim_feishu_media_assets(
        self,
        app_id: str,
        *,
        limit: int = 1,
        now: str = "",
        actor: str = "media-resolver",
    ) -> list[FeishuMediaAsset]:
        """Atomically lease a bounded set of pending assets for one app."""
        normalized_app_id = app_id.strip()
        if not normalized_app_id:
            raise ValueError("Feishu media claim requires app_id")
        if limit <= 0:
            return []
        if limit > 64:
            raise ValueError("Feishu media claim limit must not exceed 64")
        locked_at = now or datetime.now().astimezone().isoformat()
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select assets.*
                from feishu_media_assets as assets
                join feishu_events as events
                  on events.id=assets.event_record_id
                 and events.app_id=assets.app_id
                 and events.message_id=assets.message_id
                where assets.app_id=? and assets.status='pending'
                  and assets.file_key<>''
                  and events.eligibility_status='eligible'
                order by assets.id
                limit ?
                """,
                (normalized_app_id, limit),
            ).fetchall()
            claimed: list[FeishuMediaAsset] = []
            for row in rows:
                lease_token = uuid4().hex
                cursor = db.execute(
                    """
                    update feishu_media_assets
                    set status='downloading', lease_token=?, locked_at=?,
                        error_code='', error='', updated_at=current_timestamp
                    where id=? and event_record_id=? and app_id=?
                      and message_id=? and resource_type=? and file_key=?
                      and file_key_sha256=? and status='pending'
                      and lease_token=''
                    """,
                    (
                        lease_token,
                        locked_at,
                        row["id"],
                        row["event_record_id"],
                        row["app_id"],
                        row["message_id"],
                        row["resource_type"],
                        row["file_key"],
                        row["file_key_sha256"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Feishu media claim lost atomic race")
                current = db.execute(
                    "select * from feishu_media_assets where id=?", (row["id"],)
                ).fetchone()
                self._append_feishu_audit_event(
                    db,
                    app_id=current["app_id"],
                    entity_type="media_asset",
                    entity_id=current["id"],
                    event_type="claimed",
                    previous_state="pending",
                    new_state="downloading",
                    actor=actor,
                )
                claimed.append(self._feishu_media_asset_from_row(current))
            return claimed

    def claim_feishu_media_asset(
        self,
        app_id: str,
        *,
        now: str = "",
        actor: str = "media-resolver",
    ) -> FeishuMediaAsset | None:
        claimed = self.claim_feishu_media_assets(
            app_id, limit=1, now=now, actor=actor
        )
        return claimed[0] if claimed else None

    @staticmethod
    def _require_feishu_media_claim_db(
        db: sqlite3.Connection,
        asset_id: int,
        *,
        event_record_id: int,
        app_id: str,
        message_id: str,
        file_key: str,
        resource_type: str,
        lease_token: str,
    ) -> sqlite3.Row:
        if not lease_token.strip() or not file_key:
            raise ValueError("Feishu media terminal transition requires its lease")
        row = db.execute(
            """
            select * from feishu_media_assets
            where id=? and event_record_id=? and app_id=? and message_id=?
              and file_key=? and resource_type=? and lease_token=?
              and status='downloading'
            """,
            (
                asset_id,
                event_record_id,
                app_id,
                message_id,
                file_key,
                resource_type,
                lease_token,
            ),
        ).fetchone()
        if row is None:
            raise ValueError("Feishu media lease or identity does not match")
        if row["file_key_sha256"] != file_key_sha256(file_key):
            raise ValueError("Feishu media file-key binding is invalid")
        return row

    def mark_feishu_media_ready(
        self,
        asset_id: int,
        *,
        event_record_id: int,
        app_id: str,
        message_id: str,
        file_key: str,
        resource_type: str,
        lease_token: str,
        relative_path: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        max_resource_bytes: int = DEFAULT_MAX_RESOURCE_BYTES,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        actor: str = "media-resolver",
    ) -> tuple[FeishuMediaAsset, bool]:
        """CAS-complete a download and return the atomic event enqueue signal."""
        digest = str(sha256 or "").strip().lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid Feishu media sha256")
        if not mime_type.strip() or len(mime_type) > 128:
            raise ValueError("invalid Feishu media MIME type")
        if size_bytes <= 0:
            raise ValueError("Feishu media size must be positive")
        if max_resource_bytes <= 0 or max_event_bytes <= 0:
            raise ValueError("Feishu media byte limits must be positive")
        cleaned_path = self._validate_feishu_media_relative_path(
            relative_path, app_id=app_id, sha256=digest
        )
        with self._connect() as db:
            db.execute("begin immediate")
            current = self._require_feishu_media_claim_db(
                db,
                asset_id,
                event_record_id=event_record_id,
                app_id=app_id,
                message_id=message_id,
                file_key=file_key,
                resource_type=resource_type,
                lease_token=lease_token,
            )
            aggregate = db.execute(
                """
                select coalesce(sum(size_bytes), 0) as ready_bytes
                from feishu_media_assets
                where event_record_id=? and app_id=? and message_id=?
                  and id<>? and status='ready'
                """,
                (event_record_id, app_id, message_id, asset_id),
            ).fetchone()
            total_bytes = int(aggregate["ready_bytes"] or 0) + size_bytes
            error_code = ""
            if size_bytes > max_resource_bytes:
                error_code = "resource_too_large"
            elif total_bytes > max_event_bytes:
                error_code = "event_too_large"
            next_status = "rejected" if error_code else "ready"
            cursor = db.execute(
                """
                update feishu_media_assets
                set status=?, file_key='', lease_token='', locked_at='',
                    relative_path=?, mime_type=?, size_bytes=?, sha256=?,
                    error_code=?, error=?,
                    ready_at=case when ?='ready' then current_timestamp else '' end,
                    updated_at=current_timestamp
                where id=? and event_record_id=? and app_id=?
                  and message_id=? and resource_type=? and file_key=?
                  and lease_token=? and status='downloading'
                """,
                (
                    next_status,
                    "" if error_code else cleaned_path,
                    "" if error_code else mime_type.strip().lower(),
                    0 if error_code else size_bytes,
                    "" if error_code else digest,
                    error_code,
                    error_code,
                    next_status,
                    asset_id,
                    event_record_id,
                    app_id,
                    message_id,
                    resource_type,
                    file_key,
                    lease_token,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu media ready transition lost its lease")
            row = db.execute(
                "select * from feishu_media_assets where id=?", (asset_id,)
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="media_asset",
                entity_id=asset_id,
                event_type="rejected" if error_code else "ready",
                previous_state=current["status"],
                new_state=next_status,
                actor=actor,
                detail=(
                    f"error_code={error_code}"
                    if error_code
                    else f"mime_type={row['mime_type']};size_bytes={size_bytes};sha256={digest}"
                ),
            )
            ready_for_enqueue = self._feishu_media_event_ready_db(
                db,
                event_record_id=event_record_id,
                app_id=app_id,
                message_id=message_id,
            )
            return self._feishu_media_asset_from_row(row), ready_for_enqueue

    def mark_feishu_media_rejected(
        self,
        asset_id: int,
        *,
        event_record_id: int,
        app_id: str,
        message_id: str,
        file_key: str,
        resource_type: str,
        lease_token: str,
        error_code: str,
        error: str = "",
        actor: str = "media-resolver",
    ) -> tuple[FeishuMediaAsset, bool]:
        code = str(error_code or "").strip().lower()
        if (
            not code
            or len(code) > 64
            or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in code)
        ):
            raise ValueError("invalid Feishu media error code")
        safe_error = self._sanitize_feishu_media_error(error or code, file_key)
        with self._connect() as db:
            db.execute("begin immediate")
            current = self._require_feishu_media_claim_db(
                db,
                asset_id,
                event_record_id=event_record_id,
                app_id=app_id,
                message_id=message_id,
                file_key=file_key,
                resource_type=resource_type,
                lease_token=lease_token,
            )
            cursor = db.execute(
                """
                update feishu_media_assets
                set status='rejected', file_key='', lease_token='', locked_at='',
                    relative_path='', mime_type='', size_bytes=0, sha256='',
                    error_code=?, error=?, updated_at=current_timestamp
                where id=? and event_record_id=? and app_id=?
                  and message_id=? and resource_type=? and file_key=?
                  and lease_token=? and status='downloading'
                """,
                (
                    code,
                    safe_error,
                    asset_id,
                    event_record_id,
                    app_id,
                    message_id,
                    resource_type,
                    file_key,
                    lease_token,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu media rejection lost its lease")
            row = db.execute(
                "select * from feishu_media_assets where id=?", (asset_id,)
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="media_asset",
                entity_id=asset_id,
                event_type="rejected",
                previous_state=current["status"],
                new_state="rejected",
                actor=actor,
                detail=f"error_code={code}",
            )
            ready_for_enqueue = self._feishu_media_event_ready_db(
                db,
                event_record_id=event_record_id,
                app_id=app_id,
                message_id=message_id,
            )
            return self._feishu_media_asset_from_row(row), ready_for_enqueue

    def feishu_media_event_ready_for_enqueue(
        self,
        event_record_id: int,
        *,
        app_id: str,
        message_id: str,
    ) -> bool:
        """Atomically re-check whether every approved asset is terminal."""
        with self._connect() as db:
            db.execute("begin immediate")
            return self._feishu_media_event_ready_db(
                db,
                event_record_id=event_record_id,
                app_id=app_id,
                message_id=message_id,
            )

    def recover_stale_feishu_media_assets(
        self,
        *,
        app_id: str = "",
        stale_after_seconds: int = 5 * 60,
        now: str = "",
        batch_limit: int = 100,
        actor: str = "media-recovery",
    ) -> int:
        """Return only stale downloading leases to pending for safe retry."""
        if stale_after_seconds <= 0:
            raise ValueError("Feishu media stale interval must be positive")
        if batch_limit <= 0 or batch_limit > 1000:
            raise ValueError("Feishu media recovery batch limit is invalid")
        if now:
            try:
                current = datetime.fromisoformat(now.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid Feishu media recovery time") from exc
            if current.tzinfo is None or current.utcoffset() is None:
                raise ValueError("Feishu media recovery time must include timezone")
        else:
            current = datetime.now(timezone.utc)
        cutoff = (current.astimezone(timezone.utc) - timedelta(
            seconds=stale_after_seconds
        )).isoformat()
        app_clause = " and app_id=?" if app_id else ""
        args: list[str | int] = [cutoff]
        if app_id:
            args.append(app_id)
        args.append(batch_limit)
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select * from feishu_media_assets
                where status='downloading' and lease_token<>''
                  and locked_at<>'' and julianday(locked_at) <= julianday(?)
                  {app_clause}
                order by id limit ?
                """,
                args,
            ).fetchall()
            recovered = 0
            for row in rows:
                cursor = db.execute(
                    """
                    update feishu_media_assets
                    set status='pending', lease_token='', locked_at='',
                        error_code='stale_lease_recovered',
                        error='stale_lease_recovered',
                        updated_at=current_timestamp
                    where id=? and event_record_id=? and app_id=?
                      and message_id=? and resource_type=? and file_key=?
                      and lease_token=? and status='downloading'
                    """,
                    (
                        row["id"],
                        row["event_record_id"],
                        row["app_id"],
                        row["message_id"],
                        row["resource_type"],
                        row["file_key"],
                        row["lease_token"],
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                recovered += 1
                self._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="media_asset",
                    entity_id=row["id"],
                    event_type="stale_lease_recovered",
                    previous_state="downloading",
                    new_state="pending",
                    actor=actor,
                )
            return recovered

    def expire_feishu_media_keys_before(
        self,
        cutoff: str,
        *,
        downloading_stale_before: str,
        app_id: str = "",
        batch_limit: int = 100,
        actor: str = "media-retention",
    ) -> list[FeishuMediaAsset]:
        """Terminally discard expired opaque keys without racing live downloads."""
        normalized: list[str] = []
        for value, label in (
            (cutoff, "retention cutoff"),
            (downloading_stale_before, "downloading cutoff"),
        ):
            cleaned = str(value or "").strip()
            try:
                parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"invalid Feishu media {label}") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError(f"Feishu media {label} must include timezone")
            normalized.append(
                parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            )
        if batch_limit <= 0 or batch_limit > 1000:
            raise ValueError("Feishu media key expiry batch_limit must be 1..1000")
        app_clause = "and app_id=?" if app_id else ""
        args: list[str | int] = [normalized[0], normalized[1]]
        if app_id:
            args.append(app_id)
        args.append(batch_limit)
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select * from feishu_media_assets
                where file_key<>''
                  and datetime(created_at) < datetime(?)
                  and (
                    status='pending'
                    or (
                      status='downloading'
                      and (
                        locked_at=''
                        or datetime(locked_at) < datetime(?)
                      )
                    )
                  )
                  {app_clause}
                order by id
                limit ?
                """,
                args,
            ).fetchall()
            expired: list[FeishuMediaAsset] = []
            for row in rows:
                cursor = db.execute(
                    """
                    update feishu_media_assets
                    set status='rejected', file_key='', lease_token='',
                        locked_at='', relative_path='', mime_type='',
                        size_bytes=0, sha256='',
                        error_code='retention_expired',
                        error='retention_expired',
                        updated_at=current_timestamp
                    where id=? and event_record_id=? and app_id=?
                      and message_id=? and file_key=? and status=?
                      and lease_token=?
                    """,
                    (
                        row["id"],
                        row["event_record_id"],
                        row["app_id"],
                        row["message_id"],
                        row["file_key"],
                        row["status"],
                        row["lease_token"],
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                current = db.execute(
                    "select * from feishu_media_assets where id=?", (row["id"],)
                ).fetchone()
                self._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="media_asset",
                    entity_id=row["id"],
                    event_type="retention_key_expired",
                    previous_state=row["status"],
                    new_state="rejected",
                    actor=actor,
                    detail="error_code=retention_expired",
                )
                expired.append(self._feishu_media_asset_from_row(current))
            return expired

    def list_feishu_media_app_ids(
        self, *, app_id: str = "", limit: int = 1000
    ) -> list[str]:
        """Return a bounded set of clear app identities for hashed-dir cleanup."""
        cleaned_app_id = str(app_id or "").strip()
        if cleaned_app_id:
            return [cleaned_app_id]
        if limit <= 0:
            return []
        if limit > 1000:
            raise ValueError("Feishu media app list limit must not exceed 1000")
        with self._connect() as db:
            rows = db.execute(
                """
                select app_id from (
                    select app_id from feishu_media_assets
                    union
                    select app_id from feishu_events
                )
                where app_id<>''
                order by app_id
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["app_id"]) for row in rows]

    def feishu_media_blob_is_referenced(
        self, *, app_id: str, sha256: str, relative_path: str
    ) -> bool:
        """Recheck one exact content path while its interprocess lock is held."""
        digest = str(sha256 or "").strip().lower()
        cleaned_path = self._validate_feishu_media_relative_path(
            relative_path, app_id=app_id, sha256=digest
        )
        with self._connect() as db:
            row = db.execute(
                """
                select 1 from feishu_media_assets
                where app_id=? and sha256=? and relative_path<>''
                  and relative_path=? and status in ('ready', 'purged')
                limit 1
                """,
                (app_id, digest, cleaned_path),
            ).fetchone()
        return row is not None

    def mark_feishu_media_purged_before(
        self,
        cutoff: str,
        *,
        app_id: str = "",
        batch_limit: int = 100,
        processing_stale_before: str = "",
        actor: str = "media-retention",
    ) -> list[FeishuMediaAsset]:
        """Expire ready assets without allowing queue state to bypass the TTL."""
        cleaned_cutoff = str(cutoff or "").strip()
        if not cleaned_cutoff:
            raise ValueError("Feishu media retention requires a cutoff")
        try:
            parsed = datetime.fromisoformat(cleaned_cutoff.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid Feishu media retention cutoff") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Feishu media retention cutoff must include timezone")
        normalized_cutoff = parsed.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if processing_stale_before:
            try:
                stale = datetime.fromisoformat(
                    processing_stale_before.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError(
                    "invalid Feishu media processing grace cutoff"
                ) from exc
            if stale.tzinfo is None or stale.utcoffset() is None:
                raise ValueError(
                    "Feishu media processing grace cutoff must include timezone"
                )
        else:
            stale = datetime.now(timezone.utc) - timedelta(
                seconds=DEFAULT_MEDIA_PROCESSING_GRACE_SECONDS
            )
        normalized_stale = stale.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if batch_limit <= 0 or batch_limit > 1000:
            raise ValueError("Feishu media retention batch_limit must be 1..1000")
        app_clause = "and assets.app_id=?" if app_id else ""
        args: list[str | int] = [normalized_cutoff]
        if app_id:
            args.append(app_id)
        args.append(normalized_stale)
        args.append(batch_limit)
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select assets.*,
                       events.reply_task_id as retention_task_id,
                       tasks.status as retention_task_status,
                       tasks.lease_token as retention_task_lease
                from feishu_media_assets as assets
                join feishu_events as events
                  on events.id=assets.event_record_id
                 and events.app_id=assets.app_id
                 and events.message_id=assets.message_id
                left join reply_tasks as tasks
                  on tasks.id=events.reply_task_id
                where assets.status='ready'
                  and datetime(
                    case when assets.ready_at<>''
                         then assets.ready_at else assets.created_at end
                  ) < datetime(?)
                  {app_clause}
                  and (
                    events.reply_task_id is null
                    or tasks.status='pending'
                    or tasks.status not in ('pending', 'processing')
                    or (
                      tasks.status='processing'
                      and (
                        tasks.locked_at is null
                        or tasks.locked_at=''
                        or datetime(tasks.locked_at) <= datetime(?)
                      )
                    )
                  )
                order by assets.id
                limit ?
                """,
                args,
            ).fetchall()
            marked: list[FeishuMediaAsset] = []
            terminated_tasks: set[int] = set()
            for row in rows:
                task_id = int(row["retention_task_id"] or 0)
                task_status = str(row["retention_task_status"] or "")
                if task_id and task_id not in terminated_tasks:
                    if task_status == "pending":
                        task_cursor = db.execute(
                            """
                            update reply_tasks
                            set status='failed', lease_token='', locked_at=null,
                                error='feishu_media_retention_expired',
                                available_at='', updated_at=current_timestamp
                            where id=? and channel='feishu' and status='pending'
                            """,
                            (task_id,),
                        )
                    elif task_status == "processing":
                        task_cursor = db.execute(
                            """
                            update reply_tasks
                            set status='failed', lease_token='', locked_at=null,
                                error='feishu_media_retention_expired',
                                available_at='', updated_at=current_timestamp
                            where id=? and channel='feishu'
                              and status='processing' and lease_token=?
                              and (
                                locked_at is null or locked_at=''
                                or datetime(locked_at) <= datetime(?)
                              )
                            """,
                            (
                                task_id,
                                row["retention_task_lease"],
                                normalized_stale,
                            ),
                        )
                    else:
                        task_cursor = None
                    if task_cursor is not None:
                        if task_cursor.rowcount != 1:
                            continue
                        terminated_tasks.add(task_id)
                        self._append_feishu_audit_event(
                            db,
                            app_id=row["app_id"],
                            entity_type="reply_task",
                            entity_id=task_id,
                            event_type="media_retention_expired",
                            previous_state=task_status,
                            new_state="failed",
                            actor=actor,
                            detail="error_code=feishu_media_retention_expired",
                        )
                cursor = db.execute(
                    """
                    update feishu_media_assets
                    set status='purged', purged_at=current_timestamp,
                        lease_token='', locked_at='', error_code='', error='',
                        updated_at=current_timestamp
                    where id=? and event_record_id=? and app_id=?
                      and message_id=? and status='ready'
                      and relative_path=? and sha256=?
                    """,
                    (
                        row["id"],
                        row["event_record_id"],
                        row["app_id"],
                        row["message_id"],
                        row["relative_path"],
                        row["sha256"],
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                current = db.execute(
                    "select * from feishu_media_assets where id=?", (row["id"],)
                ).fetchone()
                self._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="media_asset",
                    entity_id=row["id"],
                    event_type="expired_marked_purged",
                    previous_state="ready",
                    new_state="purged",
                    actor=actor,
                )
                marked.append(self._feishu_media_asset_from_row(current))
            return marked

    def list_feishu_media_pending_purge(
        self,
        *,
        app_id: str = "",
        limit: int = 100,
    ) -> list[FeishuMediaAsset]:
        """List crash-recoverable purged rows whose local path is retained."""
        if limit <= 0:
            return []
        if limit > 1000:
            raise ValueError("Feishu media purge list limit must not exceed 1000")
        app_clause = "and app_id=?" if app_id else ""
        args: list[str | int] = []
        if app_id:
            args.append(app_id)
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_media_assets
                where status='purged' and relative_path<>'' {app_clause}
                order by case when error_code='' then 0 else 1 end, id limit ?
                """,
                args,
            ).fetchall()
        return [self._feishu_media_asset_from_row(row) for row in rows]

    def finalize_feishu_media_purge(
        self,
        asset_id: int,
        *,
        app_id: str,
        sha256: str,
        relative_path: str,
        delete_file,
        actor: str = "media-retention",
    ) -> tuple[FeishuMediaAsset, str]:
        """Delete and CAS-finalize one purged file with live-ref exclusion.

        ``delete_file`` executes while a SQLite write transaction is held.  It
        must be a bounded, local-only callback returning true when it unlinked
        a file and false when the exact path was already absent.
        """
        if asset_id <= 0 or not app_id.strip():
            raise ValueError("Feishu media purge identity is incomplete")
        digest = str(sha256 or "").strip().lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid Feishu media purge sha256")
        cleaned_path = self._validate_feishu_media_relative_path(
            relative_path, app_id=app_id, sha256=digest
        )
        if not callable(delete_file):
            raise ValueError("Feishu media purge requires a delete callback")
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_media_assets where id=?", (asset_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu media asset not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu media purge App ID does not match")
            if row["status"] != "purged":
                raise ValueError("Feishu media asset is not marked purged")
            if not row["relative_path"]:
                return self._feishu_media_asset_from_row(row), "already_finalized"
            if row["sha256"] != digest or row["relative_path"] != cleaned_path:
                raise ValueError("Feishu media purge identity changed")
            live_reference = db.execute(
                """
                select 1 from feishu_media_assets
                where id<>? and app_id=? and status='ready'
                  and sha256=? and relative_path=?
                limit 1
                """,
                (asset_id, app_id, digest, cleaned_path),
            ).fetchone()
            if live_reference is not None:
                cursor = db.execute(
                    """
                    update feishu_media_assets
                    set relative_path='', error_code='', error='',
                        lease_token='', locked_at='', updated_at=current_timestamp
                    where id=? and app_id=? and status='purged'
                      and relative_path=? and sha256=?
                    """,
                    (asset_id, app_id, cleaned_path, digest),
                )
                if cursor.rowcount != 1:
                    raise ValueError(
                        "Feishu media shared-reference finalization lost atomic race"
                    )
                current = db.execute(
                    "select * from feishu_media_assets where id=?", (asset_id,)
                ).fetchone()
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id,
                    entity_type="media_asset",
                    entity_id=asset_id,
                    event_type="purge_file_shared_reference",
                    previous_state="purged",
                    new_state="purged",
                    actor=actor,
                )
                return self._feishu_media_asset_from_row(current), "shared_reference"
            deleted = delete_file(cleaned_path, digest)
            if not isinstance(deleted, bool):
                raise ValueError("Feishu media delete callback returned invalid result")
            cursor = db.execute(
                """
                update feishu_media_assets
                set relative_path='', error_code='', error='',
                    lease_token='', locked_at='', updated_at=current_timestamp
                where id=? and app_id=? and status='purged'
                  and relative_path=? and sha256=?
                """,
                (asset_id, app_id, cleaned_path, digest),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu media purge finalization lost atomic race")
            current = db.execute(
                "select * from feishu_media_assets where id=?", (asset_id,)
            ).fetchone()
            outcome = "deleted" if deleted else "missing"
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="media_asset",
                entity_id=asset_id,
                event_type=f"purge_file_{outcome}",
                previous_state="purged",
                new_state="purged",
                actor=actor,
            )
            return self._feishu_media_asset_from_row(current), outcome

    def record_feishu_media_purge_failure(
        self,
        asset_id: int,
        *,
        app_id: str,
        error_code: str,
        actor: str = "media-retention",
    ) -> FeishuMediaAsset:
        """Record only a closed, path-free purge error for later retry."""
        allowed = {
            "content_hash_mismatch",
            "file_size_exceeded",
            "filesystem_error",
            "not_regular_file",
            "path_validation_failed",
            "symlink_rejected",
        }
        code = str(error_code or "").strip().lower()
        if code not in allowed:
            code = "filesystem_error"
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_media_assets where id=?", (asset_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu media asset not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu media purge App ID does not match")
            if row["status"] != "purged" or not row["relative_path"]:
                raise ValueError("Feishu media purge failure is no longer current")
            db.execute(
                """
                update feishu_media_assets
                set error_code=?, error=?, updated_at=current_timestamp
                where id=? and app_id=? and status='purged'
                  and relative_path<>''
                """,
                (code, code, asset_id, app_id),
            )
            current = db.execute(
                "select * from feishu_media_assets where id=?", (asset_id,)
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="media_asset",
                entity_id=asset_id,
                event_type="purge_failed",
                previous_state="purged",
                new_state="purged",
                actor=actor,
                detail=f"error_code={code}",
            )
            return self._feishu_media_asset_from_row(current)

    def attach_feishu_event_reply_task(
        self, event_record_id: int
    ) -> FeishuEventRecord:
        """Attach a receive-only eligible event without changing its payload."""
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_events where id=?", (event_record_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu event not found")
            self._enqueue_feishu_event_row(db, row)
            row = db.execute(
                "select * from feishu_events where id=?", (event_record_id,)
            ).fetchone()
            return self._feishu_event_from_row(row)

    def get_feishu_event(self, event_id: str | int) -> FeishuEventRecord | None:
        with self._connect() as db:
            if isinstance(event_id, int):
                row = db.execute(
                    "select * from feishu_events where id=?", (event_id,)
                ).fetchone()
            else:
                rows = db.execute(
                    "select * from feishu_events where event_id=? order by id",
                    (event_id,),
                ).fetchall()
                if len(rows) > 1:
                    raise ValueError(
                        "Feishu event_id is ambiguous; use app_id and message_id"
                    )
                row = rows[0] if rows else None
        return self._feishu_event_from_row(row) if row is not None else None

    def get_feishu_event_for_message(
        self, app_id: str, message_id: str
    ) -> FeishuEventRecord | None:
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_events where app_id=? and message_id=?",
                (app_id, message_id),
            ).fetchone()
        return self._feishu_event_from_row(row) if row is not None else None

    def list_feishu_events(
        self,
        app_id: str = "",
        *,
        eligibility_status: str = "",
        unqueued_only: bool = False,
        limit: int | None = 100,
    ) -> list[FeishuEventRecord]:
        """List normalized local events for bounded, offline produce-once work."""
        if limit is not None and limit <= 0:
            return []
        where: list[str] = []
        args: list[str | int] = []
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if eligibility_status:
            where.append("eligibility_status=?")
            args.append(eligibility_status)
        if unqueued_only:
            where.append("reply_task_id is null")
        clause = f"where {' and '.join(where)}" if where else ""
        query = f"select * from feishu_events {clause} order by id"
        if limit is not None:
            query += " limit ?"
            args.append(limit)
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        return [self._feishu_event_from_row(row) for row in rows]

    def purge_feishu_events_before(
        self,
        cutoff: str,
        *,
        app_id: str = "",
        actor: str = "retention-worker",
        batch_limit: int = 500,
    ) -> int:
        """Delete normalized event rows older than an explicit UTC cutoff.

        Reply tasks, attempts, deliveries, and append-only audit evidence are
        intentionally preserved.  A redelivered old message is still rejected
        by the ingress stale-event policy and cannot rehydrate its body.
        """
        cleaned_cutoff = cutoff.strip()
        if not cleaned_cutoff:
            raise ValueError("Feishu event retention requires a cutoff")
        try:
            parsed_cutoff = datetime.fromisoformat(
                cleaned_cutoff.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("invalid Feishu event retention cutoff") from exc
        if parsed_cutoff.tzinfo is None or parsed_cutoff.utcoffset() is None:
            raise ValueError("Feishu event retention cutoff must include timezone")
        normalized_cutoff = parsed_cutoff.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if batch_limit <= 0 or batch_limit > 10_000:
            raise ValueError("Feishu retention batch_limit must be 1..10000")
        app_clause = " and events.app_id=?" if app_id else ""
        args: list[str | int] = [normalized_cutoff]
        if app_id:
            args.append(app_id)
        args.append(batch_limit)
        with self._connect() as db:
            db.execute("begin immediate")
            cursor = db.execute(
                f"""
                delete from feishu_events
                where id in (
                    select events.id
                    from feishu_events as events
                    left join reply_tasks as tasks
                      on tasks.id=events.reply_task_id
                    where events.created_at < ?
                      {app_clause}
                      and (
                        tasks.id is null
                        or (
                          tasks.status not in ('pending', 'processing')
                          and not exists (
                            select 1 from feishu_deliveries as deliveries
                            where deliveries.reply_task_id=tasks.id
                              and (
                                deliveries.status in (
                                  'ready_to_send', 'sending', 'retry',
                                  'send_unknown'
                                )
                                or (
                                  deliveries.status='failed'
                                  and deliveries.error_code='verified_not_sent'
                                )
                              )
                          )
                          and not exists (
                            select 1 from feishu_message_actions as actions
                            where actions.reply_task_id=tasks.id
                              and (
                                actions.status in (
                                  'ready', 'sending', 'retry', 'result_unknown'
                                )
                                or (
                                  actions.status='failed'
                                  and actions.error_code='verified_not_applied'
                                )
                              )
                          )
                          and not exists (
                            select 1
                            from feishu_local_notifications as local_notifications
                            where local_notifications.reply_task_id=tasks.id
                              and local_notifications.status in (
                                'waiting_remote', 'pending', 'sending', 'retry',
                                'result_unknown'
                              )
                          )
                        )
                      )
                      and not exists (
                        select 1 from feishu_media_assets as media
                        where media.event_record_id=events.id
                          and (
                            media.status in (
                              'pending', 'downloading', 'ready'
                            )
                            or (
                              media.status='purged'
                              and media.relative_path<>''
                            )
                          )
                      )
                    order by events.id
                    limit ?
                )
                """,
                args,
            )
            deleted = max(0, int(cursor.rowcount or 0))
            if deleted:
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id or "*",
                    entity_type="retention",
                    entity_id=app_id or "all-apps",
                    event_type="events_purged",
                    actor=actor,
                    detail=f"deleted={deleted}",
                )
            return deleted

    def list_feishu_context(
        self,
        chat_id: str,
        limit: int = 20,
        *,
        app_id: str = "",
        thread_id: str | None = None,
        root_message_id: str | None = None,
        before_message_id: str = "",
        lookback_seconds: int = 24 * 60 * 60,
    ) -> list[FeishuEventRecord]:
        """Return approved local context oldest-first, optionally thread-bound.

        Passing ``thread_id=''`` explicitly selects only the main conversation;
        a non-empty value selects exactly one topic.  ``None`` retains the
        legacy all-thread query for non-consumer diagnostic callers.
        """
        if lookback_seconds <= 0 or lookback_seconds > 30 * 24 * 60 * 60:
            raise ValueError(
                "Feishu context lookback_seconds must be between 1 and 2592000"
            )
        if limit <= 0:
            return []
        where = ["chat_id=?", "eligibility_status='eligible'", "body_text<>''"]
        args: list[str | int] = [chat_id]
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if thread_id is not None:
            if thread_id:
                where.append("thread_id=?")
                args.append(thread_id)
            else:
                where.append("thread_id='' ")
                if root_message_id:
                    where.append("(root_message_id=? or message_id=?)")
                    args.extend((root_message_id, root_message_id))
                else:
                    where.append("root_message_id='' ")
        with self._connect() as db:
            if before_message_id:
                if not app_id or thread_id is None:
                    raise ValueError(
                        "as-of Feishu context requires app_id and thread_id"
                    )
                boundary = db.execute(
                    """
                    select id, chat_id, thread_id, event_create_time_ms
                         , root_message_id, message_id
                    from feishu_events
                    where app_id=? and message_id=?
                    """,
                    (app_id, before_message_id),
                ).fetchone()
                if boundary is None:
                    raise ValueError("Feishu context boundary message not found")
                boundary_root = (
                    boundary["root_message_id"] or boundary["message_id"]
                )
                root_matches = (
                    True
                    if thread_id
                    else (
                        boundary_root == root_message_id
                        if root_message_id
                        else not boundary["root_message_id"]
                    )
                )
                if boundary["chat_id"] != chat_id or (
                    boundary["thread_id"] != thread_id
                ) or not root_matches:
                    raise ValueError("Feishu context boundary scope does not match")
                # Both normalized event time and local insertion order must be
                # at-or-before the trigger.  This excludes earlier-arriving
                # future events as well as late arrivals observed afterwards.
                where.append("id < ?")
                args.append(boundary["id"])
                where.append("event_create_time_ms <= ?")
                args.append(boundary["event_create_time_ms"])
                where.append("event_create_time_ms >= ?")
                args.append(
                    int(boundary["event_create_time_ms"])
                    - (lookback_seconds * 1000)
                )
            args.append(limit)
            rows = db.execute(
                f"""
                select * from feishu_events
                where {' and '.join(where)}
                order by event_create_time_ms desc, id desc
                limit ?
                """,
                args,
            ).fetchall()
        return [self._feishu_event_from_row(row) for row in reversed(rows)]

    def list_feishu_audit_events(
        self,
        *,
        app_id: str = "",
        entity_type: str = "",
        entity_id: str | int = "",
        before_id: int = 0,
        limit: int = 100,
    ) -> list[FeishuAuditEvent]:
        if limit <= 0:
            return []
        if limit > 1000:
            raise ValueError("Feishu audit event limit must not exceed 1000")
        where: list[str] = []
        args: list[str | int] = []
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if entity_type:
            where.append("entity_type=?")
            args.append(entity_type)
        if entity_id != "":
            where.append("entity_id=?")
            args.append(str(entity_id))
        if before_id > 0:
            where.append("id<?")
            args.append(before_id)
        clause = f"where {' and '.join(where)}" if where else ""
        query = f"select * from feishu_audit_events {clause} order by id desc"
        query += " limit ?"
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        return [self._feishu_audit_event_from_row(row) for row in rows]

    # ---- Feishu channel: reviewed reply scopes ----
    def upsert_feishu_reply_scope(
        self, scope: FeishuReplyScope
    ) -> FeishuReplyScope:
        """Discover a scope without granting or revoking authorization.

        New targets are always pending and disabled.  Rediscovery updates only
        descriptive fields and ``last_seen_at``; review state can change only
        through :meth:`review_feishu_reply_scope`.
        """
        last_seen_at = scope.last_seen_at or datetime.now().astimezone().isoformat()
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                """
                select * from feishu_reply_scopes
                where app_id=? and target_type=? and target_id=?
                """,
                (scope.app_id, scope.target_type, scope.target_id),
            ).fetchone()
            db.execute(
                """
                insert into feishu_reply_scopes (
                    app_id, target_type, target_id, display_name, trigger_mode,
                    enabled, binding_status, last_seen_at
                ) values (?, ?, ?, ?, ?, 0, 'pending', ?)
                on conflict(app_id, target_type, target_id) do update set
                    display_name=coalesce(
                        nullif(excluded.display_name, ''),
                        feishu_reply_scopes.display_name
                    ),
                    trigger_mode=excluded.trigger_mode,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=current_timestamp
                """,
                (
                    scope.app_id,
                    scope.target_type,
                    scope.target_id,
                    scope.display_name,
                    scope.trigger_mode,
                    last_seen_at,
                ),
            )
            row = db.execute(
                """
                select * from feishu_reply_scopes
                where app_id=? and target_type=? and target_id=?
                """,
                (scope.app_id, scope.target_type, scope.target_id),
            ).fetchone()
            if existing is None:
                self._append_feishu_audit_event(
                    db,
                    app_id=scope.app_id,
                    entity_type="reply_scope",
                    entity_id=self._feishu_scope_audit_id(
                        scope.target_type, scope.target_id
                    ),
                    event_type="discovered",
                    new_state="pending",
                    actor="ingress",
                )
            return self._feishu_scope_from_row(row)

    def get_feishu_reply_scope(
        self, app_id: str, target_type: str, target_id: str
    ) -> FeishuReplyScope | None:
        with self._connect() as db:
            row = db.execute(
                """
                select * from feishu_reply_scopes
                where app_id=? and target_type=? and target_id=?
                """,
                (app_id, target_type, target_id),
            ).fetchone()
        return self._feishu_scope_from_row(row) if row is not None else None

    def list_feishu_reply_scopes(
        self,
        app_id: str = "",
        *,
        target_type: str = "",
        binding_status: str = "",
        enabled_only: bool = False,
    ) -> list[FeishuReplyScope]:
        where: list[str] = []
        args: list[str | int] = []
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if target_type:
            where.append("target_type=?")
            args.append(target_type)
        if binding_status:
            where.append("binding_status=?")
            args.append(binding_status)
        if enabled_only:
            where.append("enabled=1")
        clause = f"where {' and '.join(where)}" if where else ""
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_reply_scopes {clause}
                order by app_id, target_type, display_name, target_id
                """,
                args,
            ).fetchall()
        return [self._feishu_scope_from_row(row) for row in rows]

    def review_feishu_reply_scope(
        self,
        app_id: str,
        target_type: str,
        target_id: str,
        *,
        approved: bool,
        approved_by: str,
        now: str = "",
    ) -> FeishuReplyScope:
        if not approved_by.strip():
            raise ValueError("Feishu scope review requires approved_by")
        reviewed_at = now or datetime.now().astimezone().isoformat()
        binding_status = "verified" if approved else "disabled"
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                """
                select * from feishu_reply_scopes
                where app_id=? and target_type=? and target_id=?
                """,
                (app_id, target_type, target_id),
            ).fetchone()
            if existing is None:
                raise ValueError("Feishu reply scope not found")
            cursor = db.execute(
                """
                update feishu_reply_scopes
                set enabled=?, binding_status=?, approved_at=?, approved_by=?,
                    updated_at=current_timestamp
                where app_id=? and target_type=? and target_id=?
                """,
                (
                    int(approved),
                    binding_status,
                    reviewed_at,
                    approved_by,
                    app_id,
                    target_type,
                    target_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu reply scope not found")
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="reply_scope",
                entity_id=self._feishu_scope_audit_id(target_type, target_id),
                event_type="approved" if approved else "disabled",
                previous_state=existing["binding_status"],
                new_state=binding_status,
                actor=approved_by.strip(),
            )
            row = db.execute(
                """
                select * from feishu_reply_scopes
                where app_id=? and target_type=? and target_id=?
                """,
                (app_id, target_type, target_id),
            ).fetchone()
            return self._feishu_scope_from_row(row)

    # ---- Feishu channel: outbound deliveries ----
    @staticmethod
    def _normalize_feishu_reply_payload(
        *,
        reply_text: str,
        reply_format: str,
        mention_open_ids,
        payload_sha256: str,
    ) -> tuple[FeishuReplyPayload, str]:
        if isinstance(mention_open_ids, str):
            raise ValueError("Feishu delivery mentions must be a sequence")
        payload = FeishuReplyPayload(
            kind=reply_format,
            text=reply_text,
            mention_open_ids=tuple(mention_open_ids),
        )
        digest = payload.sha256()
        if payload_sha256 and payload_sha256 != digest:
            raise ValueError("Feishu delivery payload hash does not match")
        mentions_json = json.dumps(
            list(payload.mention_open_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return payload, mentions_json

    @classmethod
    def _supersede_processing_feishu_reply_task_db(
        cls,
        db: sqlite3.Connection,
        *,
        task: sqlite3.Row,
        app_id: str,
        lease_token: str,
    ) -> bool:
        current = db.execute(
            """
            select * from feishu_events
            where reply_task_id=? and app_id=?
            """,
            (task["id"], app_id),
        ).fetchone()
        if current is None:
            raise ValueError("Feishu reply task event identity is missing")
        reference_root = cls._feishu_reference_root(current)
        newer = db.execute(
            """
            select events.id
            from feishu_events as events
            where events.app_id=? and events.chat_id=?
              and events.eligibility_status='eligible'
              and coalesce(nullif(events.thread_id, ''),
                           nullif(events.root_message_id, ''),
                           events.message_id)=?
              and (
                    events.event_create_time_ms > ?
                    or (
                        events.event_create_time_ms=? and events.id>?
                    )
                  )
            order by events.event_create_time_ms desc, events.id desc
            limit 1
            """,
            (
                app_id,
                current["chat_id"],
                reference_root,
                current["event_create_time_ms"],
                current["event_create_time_ms"],
                current["id"],
            ),
        ).fetchone()
        if newer is None:
            return False
        updated = db.execute(
            """
            update reply_tasks
            set status='done', lease_token='', locked_at=null,
                error='superseded_by_newer_feishu_trigger',
                available_at='', updated_at=current_timestamp
            where id=? and channel='feishu' and status='processing'
              and lease_token=?
            """,
            (task["id"], lease_token),
        )
        if updated.rowcount != 1:
            raise ValueError("Feishu reply task lease is no longer active")
        db.execute(
            """
            update feishu_deliveries
            set status='rejected', lease_token='', locked_at='',
                available_at='', error_code='target_revoked',
                error='superseded_by_newer_feishu_trigger',
                updated_at=current_timestamp
            where reply_task_id=? and status in ('ready_to_send', 'retry')
            """,
            (task["id"],),
        )
        cls._cancel_feishu_local_notifications_for_task_db(
            db,
            reply_task_id=int(task["id"]),
            app_id=app_id,
            actor="consumer",
        )
        db.execute(
            """
            update feishu_message_actions
            set status='rejected', lease_token='', locked_at='',
                available_at='', error_code='target_revoked',
                error='superseded_by_newer_feishu_trigger',
                updated_at=current_timestamp
            where reply_task_id=? and status in ('ready', 'retry')
              and kind<>'recall_message'
            """,
            (task["id"],),
        )
        cls._append_feishu_audit_event(
            db,
            app_id=app_id,
            entity_type="reply_task",
            entity_id=int(task["id"]),
            event_type="trigger_superseded",
            previous_state="processing",
            new_state="done",
            actor="consumer",
            detail=f"newer_event_record_id={int(newer['id'])}",
        )
        return True

    def supersede_processing_feishu_reply_task(
        self,
        reply_task_id: int,
        *,
        app_id: str,
        lease_token: str,
    ) -> bool:
        """CAS-terminalize one active task when a newer same-root trigger exists."""
        normalized_app_id = str(app_id or "").strip()
        if not normalized_app_id or not str(lease_token or "").strip():
            raise ValueError("Feishu supersession requires App ID and lease")
        with self._connect() as db:
            db.execute("begin immediate")
            task = db.execute(
                "select * from reply_tasks where id=?", (reply_task_id,)
            ).fetchone()
            if task is None or task["channel"] != "feishu":
                raise ValueError("Feishu supersession requires a Feishu task")
            if self._feishu_task_app_id(task) != normalized_app_id:
                raise PermissionError("Feishu supersession App ID does not match")
            if (
                task["status"] != "processing"
                or task["lease_token"] != lease_token
            ):
                raise ValueError("Feishu reply task lease is no longer active")
            return self._supersede_processing_feishu_reply_task_db(
                db,
                task=task,
                app_id=normalized_app_id,
                lease_token=lease_token,
            )

    def recover_feishu_reply_task(
        self,
        reply_task_id: int,
        *,
        app_id: str,
        lease_token: str = "",
    ) -> bool:
        """Close crash windows between attempt, delivery, and task commits.

        Returns true when an already-created immutable delivery was found and
        the task was completed without running the model a second time.
        """
        normalized_app_id = str(app_id or "").strip()
        if not normalized_app_id:
            raise ValueError("Feishu recovery requires app_id")
        with self._connect() as db:
            db.execute("begin immediate")
            task = db.execute(
                "select * from reply_tasks where id=?", (reply_task_id,)
            ).fetchone()
            if task is None or task["channel"] != "feishu":
                raise ValueError("Feishu recovery requires a Feishu reply task")
            if self._feishu_task_app_id(task) != normalized_app_id:
                raise PermissionError("Feishu recovery App ID does not match task")
            if lease_token and (
                task["status"] != "processing"
                or task["lease_token"] != lease_token
            ):
                raise ValueError("Feishu reply task lease is no longer active")
            if lease_token and self._supersede_processing_feishu_reply_task_db(
                db,
                task=task,
                app_id=normalized_app_id,
                lease_token=lease_token,
            ):
                return True
            delivery = db.execute(
                "select * from feishu_deliveries where reply_task_id=?",
                (reply_task_id,),
            ).fetchone()
            if delivery is not None:
                self._sync_feishu_attempt_from_delivery(db, delivery)
                cursor = db.execute(
                    """
                    update reply_tasks
                    set status='done', lease_token='', locked_at=null,
                        error='', available_at='',
                        updated_at=current_timestamp
                    where id=? and status='processing'
                    """,
                    (reply_task_id,),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Feishu reply task is not processing")
                self._append_feishu_audit_event(
                    db,
                    app_id=delivery["app_id"],
                    entity_type="delivery",
                    entity_id=delivery["id"],
                    event_type="task_recovered",
                    previous_state=delivery["status"],
                    new_state=delivery["status"],
                    actor="consumer-recovery",
                )
                return True

            orphaned = db.execute(
                """
                select attempts.id, attempts.action
                from reply_attempts as attempts
                where attempts.channel='feishu'
                  and attempts.conversation_id=?
                  and attempts.trigger_message_id=?
                  and attempts.send_status in ('pending', 'processing')
                  and not exists (
                    select 1 from feishu_deliveries as deliveries
                    where deliveries.attempt_id=attempts.id
                  )
                order by attempts.id
                """,
                (task["conversation_id"], task["trigger_message_id"]),
            ).fetchall()
            orphan_ids = [int(row["id"]) for row in orphaned]
            if orphan_ids:
                task_app_id = self._feishu_task_app_id(task)
                latest = orphaned[-1]
                terminal_without_delivery = latest["action"] in {
                    "no_reply",
                    "handoff_to_human",
                }
                superseded_ids = (
                    orphan_ids[:-1] if terminal_without_delivery else orphan_ids
                )
                if superseded_ids:
                    placeholders = ",".join("?" for _ in superseded_ids)
                    db.execute(
                        f"""
                        update reply_attempts
                        set send_status='failed',
                            send_error='superseded_after_consumer_recovery',
                            updated_at=current_timestamp
                        where id in ({placeholders})
                        """,
                        superseded_ids,
                    )
                    for superseded_id in superseded_ids:
                        self._append_feishu_audit_event(
                            db,
                            app_id=task_app_id,
                            entity_type="reply_attempt",
                            entity_id=superseded_id,
                            event_type="attempt_superseded_after_recovery",
                            previous_state="pending",
                            new_state="failed",
                            actor="consumer-recovery",
                        )
                if terminal_without_delivery:
                    db.execute(
                        """
                        update reply_attempts
                        set send_status='skipped', send_error='',
                            updated_at=current_timestamp
                        where id=?
                        """,
                        (latest["id"],),
                    )
                    cursor = db.execute(
                        """
                        update reply_tasks
                        set status='done', lease_token='', locked_at=null,
                            error='', available_at='',
                            updated_at=current_timestamp
                        where id=? and status='processing'
                        """,
                        (reply_task_id,),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("Feishu reply task is not processing")
                    self._append_feishu_audit_event(
                        db,
                        app_id=task_app_id,
                        entity_type="reply_attempt",
                        entity_id=latest["id"],
                        event_type="no_send_recovered",
                        previous_state="pending",
                        new_state="skipped",
                        actor="consumer-recovery",
                    )
                    return True
            return False

    def finalize_feishu_reply_task(
        self,
        reply_task_id: int,
        *,
        app_id: str,
        lease_token: str,
        action: str,
        sensitivity_kind: str,
        codex_reason: str = "",
        draft_reply_text: str = "",
        audit_summary: str = "",
        task_status: str,
        send_status: str,
        error: str = "",
        delivery_app_id: str = "",
        delivery_chat_id: str = "",
        reply_to_message_id: str = "",
        reply_in_thread: bool = False,
        reply_format: str = "text",
        mention_open_ids: tuple[str, ...] | list[str] = (),
        payload_sha256: str = "",
        idempotency_key: str = "",
        message_action_specs: tuple[dict, ...] | list[dict] = (),
        handoff_target_allowlist=(),
        local_notification_spec: dict | None = None,
    ) -> tuple[int, FeishuDelivery | None]:
        """Atomically persist one audited Feishu decision and task outcome.

        A validated reply creates its attempt, immutable delivery, and task
        completion in one transaction.  No attempt-only crash window can make
        a restarted consumer execute the model a second time.
        """
        normalized_consumer_app_id = str(app_id or "").strip()
        if not normalized_consumer_app_id:
            raise ValueError("Feishu task finalization requires app_id")
        if not lease_token.strip():
            raise ValueError("Feishu task finalization requires a lease token")
        if task_status not in {"done", "failed"}:
            raise ValueError("Feishu task finalization status is invalid")
        delivery_requested = bool(
            delivery_app_id
            or delivery_chat_id
            or reply_to_message_id
            or idempotency_key
            or mention_open_ids
            or payload_sha256
            or reply_format != "text"
        )
        if delivery_requested and not all(
            (
                delivery_app_id.strip(),
                delivery_chat_id.strip(),
                reply_to_message_id.strip(),
                draft_reply_text.strip(),
                idempotency_key.strip(),
            )
        ):
            raise ValueError("Feishu delivery finalization is incomplete")
        if delivery_requested and (
            task_status != "done" or send_status != "pending"
        ):
            raise ValueError("Feishu delivery finalization state is invalid")
        delivery_payload: FeishuReplyPayload | None = None
        delivery_mentions_json = "[]"
        if delivery_requested:
            delivery_payload, delivery_mentions_json = (
                self._normalize_feishu_reply_payload(
                    reply_text=draft_reply_text,
                    reply_format=reply_format,
                    mention_open_ids=mention_open_ids,
                    payload_sha256=payload_sha256,
                )
            )
        normalized_action_specs = list(message_action_specs)
        if len(normalized_action_specs) > 20:
            raise ValueError("too many Feishu message actions")
        allowed_spec_fields = {
            "action_key",
            "kind",
            "target_message_id",
            "target_open_id",
            "payload",
        }
        for spec in normalized_action_specs:
            if not isinstance(spec, dict) or set(spec) - allowed_spec_fields:
                raise ValueError("invalid Feishu message action spec")
            if not isinstance(spec.get("payload", {}), dict):
                raise ValueError("invalid Feishu message action payload")
        if normalized_action_specs and task_status != "done":
            raise ValueError("failed Feishu tasks cannot enqueue message actions")
        normalized_local_notification: dict[str, str] | None = None
        if local_notification_spec is not None:
            if not isinstance(local_notification_spec, dict) or set(
                local_notification_spec
            ) != {"kind", "dependency_mode", "title", "message"}:
                raise ValueError("invalid Feishu local notification spec")
            notification_kind = str(local_notification_spec.get("kind") or "")
            dependency_mode = str(
                local_notification_spec.get("dependency_mode") or ""
            )
            title = str(local_notification_spec.get("title") or "")
            message = str(local_notification_spec.get("message") or "")
            if (
                notification_kind != "handoff_fallback"
                or dependency_mode not in {"immediate", "remote_failure"}
                or not title.strip()
                or title != title.strip()
                or len(title) > 200
                or not message.strip()
                or message != message.strip()
                or len(message) > 2000
                or any(ord(character) < 32 for character in title)
                or any(
                    ord(character) < 32 and character not in "\n\t"
                    for character in message
                )
            ):
                raise ValueError("invalid Feishu local notification spec")
            handoff_specs = [
                spec
                for spec in normalized_action_specs
                if str(spec.get("kind") or "") == "handoff_notify"
            ]
            if (
                action != "handoff_to_human"
                or task_status != "done"
                or (
                    dependency_mode == "remote_failure"
                    and (
                        not handoff_specs
                        or len(handoff_specs) != len(normalized_action_specs)
                    )
                )
                or (dependency_mode == "immediate" and handoff_specs)
            ):
                raise ValueError("Feishu local notification dependency is invalid")
            normalized_local_notification = {
                "kind": notification_kind,
                "dependency_mode": dependency_mode,
                "title": title,
                "message": message,
            }
        normalized_handoff_allowlist = self._normalize_feishu_handoff_allowlist(
            handoff_target_allowlist
        )
        with self._connect() as db:
            db.execute("begin immediate")
            task = db.execute(
                "select * from reply_tasks where id=?", (reply_task_id,)
            ).fetchone()
            if task is None or task["channel"] != "feishu":
                raise ValueError("Feishu task finalization requires a Feishu task")
            if self._feishu_task_app_id(task) != normalized_consumer_app_id:
                raise PermissionError(
                    "Feishu task finalization App ID does not match consumer"
                )
            if (
                task["status"] != "processing"
                or task["lease_token"] != lease_token
            ):
                raise ValueError("Feishu reply task lease is no longer active")
            if self._supersede_processing_feishu_reply_task_db(
                db,
                task=task,
                app_id=normalized_consumer_app_id,
                lease_token=lease_token,
            ):
                return 0, None

            trigger: dict[str, object] = {}
            if (
                delivery_requested
                or normalized_action_specs
                or normalized_local_notification is not None
            ):
                try:
                    decoded = json.loads(task["trigger_message_json"] or "{}")
                except json.JSONDecodeError as exc:
                    raise ValueError("Feishu reply task trigger is invalid") from exc
                if not isinstance(decoded, dict):
                    raise ValueError("Feishu reply task trigger is invalid")
                trigger = decoded
                expected_app_id = delivery_app_id or str(trigger.get("app_id") or "")
                expected_chat_id = delivery_chat_id or str(trigger.get("chat_id") or "")
                if trigger.get("app_id") != expected_app_id:
                    raise PermissionError(
                        "Feishu finalization App ID does not match task"
                    )
                if trigger.get("chat_id") != expected_chat_id:
                    raise ValueError(
                        "Feishu finalization chat does not match reply task"
                    )
                if trigger.get("message_id") != task["trigger_message_id"]:
                    raise ValueError(
                        "Feishu finalization trigger does not match reply task"
                    )
                if delivery_requested and trigger.get("message_id") != reply_to_message_id:
                    raise ValueError(
                        "Feishu delivery reply target does not match task"
                    )
                expected_conversation_id = self._feishu_task_conversation_id(
                    expected_app_id, expected_chat_id
                )
                if task["conversation_id"] not in {
                    expected_conversation_id,
                    expected_chat_id,
                }:
                    raise ValueError(
                        "Feishu task conversation identity is invalid"
                    )

            cursor = db.execute(
                """
                insert into reply_attempts (
                    conversation_id, conversation_title,
                    trigger_message_id, trigger_sender, trigger_text,
                    action, sensitivity_kind, codex_reason,
                    draft_reply_text, audit_summary, send_status,
                    send_error, channel
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'feishu')
                """,
                (
                    task["conversation_id"],
                    task["conversation_title"],
                    task["trigger_message_id"],
                    task["trigger_sender"],
                    task["trigger_text"],
                    action,
                    sensitivity_kind,
                    safe_observability_error(codex_reason),
                    draft_reply_text,
                    safe_observability_error(audit_summary),
                    send_status,
                    safe_observability_error(error),
                ),
            )
            attempt_id = int(cursor.lastrowid)
            app_id = self._feishu_task_app_id(task)
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="reply_attempt",
                entity_id=attempt_id,
                event_type="attempt_recorded",
                new_state=send_status,
                actor="consumer",
            )

            delivery: FeishuDelivery | None = None
            if delivery_requested:
                delivery_chunks = split_reply_payload(delivery_payload)
                delivery_expected_chunks = len(delivery_chunks)
                delivery_plan_hash = delivery_chunk_plan_sha256(delivery_chunks)
                delivery_preview_hash = delivery_approval_hash(
                    reply_task_id=reply_task_id,
                    attempt_id=attempt_id,
                    app_id=delivery_app_id,
                    chat_id=delivery_chat_id,
                    reply_to_message_id=reply_to_message_id,
                    reply_in_thread=reply_in_thread,
                    payload_sha256=delivery_payload.sha256(),
                    idempotency_key=idempotency_key,
                    expected_chunks=delivery_expected_chunks,
                    chunk_plan_sha256=delivery_plan_hash,
                    review_generation=1,
                )
                delivery_cursor = db.execute(
                    """
                    insert into feishu_deliveries (
                        reply_task_id, attempt_id, app_id, chat_id,
                        reply_to_message_id, reply_in_thread, reply_text,
                        reply_format, mention_open_ids_json, payload_sha256,
                        idempotency_key, expected_chunks, chunk_plan_sha256,
                        review_generation, approval_hash
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reply_task_id,
                        attempt_id,
                        delivery_app_id,
                        delivery_chat_id,
                        reply_to_message_id,
                        int(reply_in_thread),
                        delivery_payload.text,
                        delivery_payload.kind,
                        delivery_mentions_json,
                        delivery_payload.sha256(),
                        idempotency_key,
                        delivery_expected_chunks,
                        delivery_plan_hash,
                        1,
                        delivery_preview_hash,
                    ),
                )
                delivery_row = db.execute(
                    "select * from feishu_deliveries where id=?",
                    (delivery_cursor.lastrowid,),
                ).fetchone()
                self._sync_feishu_attempt_from_delivery(db, delivery_row)
                self._append_feishu_audit_event(
                    db,
                    app_id=delivery_app_id,
                    entity_type="delivery",
                    entity_id=delivery_row["id"],
                    event_type="created",
                    new_state=delivery_row["status"],
                    actor="consumer",
                )
                delivery = self._feishu_delivery_from_row(delivery_row)

            for index, spec in enumerate(normalized_action_specs):
                action_row = build_message_action(
                    reply_task_id=reply_task_id,
                    attempt_id=attempt_id,
                    app_id=str(trigger["app_id"]),
                    chat_id=str(trigger["chat_id"]),
                    action_key=str(spec.get("action_key") or f"action:{index}"),
                    kind=str(spec.get("kind") or ""),
                    target_message_id=str(spec.get("target_message_id") or ""),
                    target_open_id=str(spec.get("target_open_id") or ""),
                    payload=dict(spec.get("payload") or {}),
                )
                if (
                    action_row.kind == "handoff_notify"
                    and action_row.target_open_id not in normalized_handoff_allowlist
                ):
                    raise PermissionError(
                        "Feishu handoff target is not locally allowlisted"
                    )
                if action_row.kind == "add_reaction":
                    if action_row.target_message_id != task["trigger_message_id"]:
                        raise PermissionError(
                            "Feishu reaction target is not its persisted trigger"
                        )
                elif action_row.kind == "recall_message":
                    owned = self._feishu_recall_target_row(
                        db,
                        app_id=action_row.app_id,
                        chat_id=action_row.chat_id,
                        message_id=action_row.target_message_id,
                    )
                    if owned is None:
                        raise PermissionError(
                            "Feishu recall target is not an active terminal receipt"
                        )
                action_cursor = db.execute(
                    """
                    insert into feishu_message_actions (
                        reply_task_id, attempt_id, app_id, chat_id, action_key,
                        kind, target_message_id, target_open_id, payload_json,
                        payload_sha256, idempotency_key, review_generation,
                        approval_hash, risk
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_row.reply_task_id,
                        action_row.attempt_id,
                        action_row.app_id,
                        action_row.chat_id,
                        action_row.action_key,
                        action_row.kind,
                        action_row.target_message_id,
                        action_row.target_open_id,
                        action_row.payload_json,
                        action_row.payload_sha256,
                        action_row.idempotency_key,
                        action_row.review_generation,
                        action_row.approval_hash,
                        action_row.risk,
                    ),
                )
                self._append_feishu_audit_event(
                    db,
                    app_id=action_row.app_id,
                    entity_type="message_action",
                    entity_id=action_cursor.lastrowid,
                    event_type="created",
                    new_state="ready",
                    actor="consumer",
                    detail=f"kind={action_row.kind};risk={action_row.risk}",
                )

            if normalized_local_notification is not None:
                notification_status = (
                    "pending"
                    if normalized_local_notification["dependency_mode"]
                    == "immediate"
                    else "waiting_remote"
                )
                notification_cursor = db.execute(
                    """
                    insert into feishu_local_notifications (
                        reply_task_id, attempt_id, app_id,
                        execution_generation, kind, dependency_mode,
                        title, message, status
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reply_task_id,
                        attempt_id,
                        app_id,
                        str(task["execution_generation"]),
                        normalized_local_notification["kind"],
                        normalized_local_notification["dependency_mode"],
                        normalized_local_notification["title"],
                        normalized_local_notification["message"],
                        notification_status,
                    ),
                )
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id,
                    entity_type="local_notification",
                    entity_id=notification_cursor.lastrowid,
                    event_type="created",
                    new_state=notification_status,
                    actor="consumer",
                    detail=(
                        "kind=handoff_fallback;dependency="
                        + normalized_local_notification["dependency_mode"]
                    ),
                )

            task_error = (
                safe_observability_error(error)
                if task_status == "failed"
                else ""
            )
            updated = db.execute(
                """
                update reply_tasks
                set status=?, lease_token='', locked_at=null, error=?,
                    available_at='', updated_at=current_timestamp
                where id=? and channel='feishu' and status='processing'
                  and lease_token=?
                """,
                (task_status, task_error, reply_task_id, lease_token),
            )
            if updated.rowcount != 1:
                raise ValueError("Feishu reply task lease was lost")
            if not delivery_requested:
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id,
                    entity_type="reply_attempt",
                    entity_id=attempt_id,
                    event_type=(
                        "no_send_completed"
                        if task_status == "done"
                        else "attempt_failed"
                    ),
                    previous_state="pending",
                    new_state=send_status,
                    actor="consumer",
                    detail=(
                        "error_code=local_consumer_failure"
                        if task_status == "failed"
                        else ""
                    ),
                )
            return attempt_id, delivery

    def create_feishu_delivery(
        self,
        *,
        reply_task_id: int,
        attempt_id: int = 0,
        app_id: str,
        chat_id: str,
        reply_to_message_id: str,
        reply_in_thread: bool,
        reply_text: str,
        reply_format: str = "text",
        mention_open_ids: tuple[str, ...] | list[str] = (),
        payload_sha256: str = "",
        idempotency_key: str = "",
    ) -> FeishuDelivery:
        """Create exactly one immutable-idempotency delivery per reply task."""
        if not app_id.strip() or not chat_id.strip():
            raise ValueError("Feishu delivery requires app_id and chat_id")
        if not reply_to_message_id.strip():
            raise ValueError("Feishu delivery requires reply_to_message_id")
        if not reply_text.strip():
            raise ValueError("Feishu delivery reply_text must be non-empty")
        if attempt_id <= 0:
            raise ValueError("Feishu delivery requires a durable reply attempt")
        payload, mentions_json = self._normalize_feishu_reply_payload(
            reply_text=reply_text,
            reply_format=reply_format,
            mention_open_ids=mention_open_ids,
            payload_sha256=payload_sha256,
        )
        stable_key = idempotency_key or str(uuid4())
        with self._connect() as db:
            db.execute("begin immediate")
            task = db.execute(
                "select * from reply_tasks where id=?", (reply_task_id,)
            ).fetchone()
            if task is None:
                raise ValueError("reply task not found")
            if task["channel"] != "feishu":
                raise ValueError("Feishu delivery requires channel=feishu task")
            try:
                trigger = json.loads(task["trigger_message_json"] or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("Feishu reply task trigger is invalid") from exc
            if not isinstance(trigger, dict):
                raise ValueError("Feishu reply task trigger is invalid")
            if trigger.get("app_id") != app_id:
                raise PermissionError("Feishu delivery App ID does not match task")
            if trigger.get("chat_id") != chat_id:
                raise ValueError("Feishu delivery chat does not match reply task")
            if trigger.get("message_id") != reply_to_message_id:
                raise ValueError("Feishu delivery reply target does not match task")
            expected_conversation_id = self._feishu_task_conversation_id(
                app_id, chat_id
            )
            if task["conversation_id"] not in {
                expected_conversation_id,
                chat_id,  # compatibility with pre-namespace preview databases
            }:
                raise ValueError("Feishu task conversation identity is invalid")
            attempt = db.execute(
                "select * from reply_attempts where id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise ValueError("reply attempt not found")
            if attempt["channel"] != "feishu":
                raise ValueError("Feishu delivery requires channel=feishu attempt")
            if (
                attempt["conversation_id"] != task["conversation_id"]
                or attempt["trigger_message_id"] != task["trigger_message_id"]
            ):
                raise ValueError("reply attempt does not match reply task")
            chunks = split_reply_payload(payload)
            expected_chunks = len(chunks)
            plan_hash = delivery_chunk_plan_sha256(chunks)
            approval_hash = delivery_approval_hash(
                reply_task_id=reply_task_id,
                attempt_id=attempt_id,
                app_id=app_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                reply_in_thread=reply_in_thread,
                payload_sha256=payload.sha256(),
                idempotency_key=stable_key,
                expected_chunks=expected_chunks,
                chunk_plan_sha256=plan_hash,
                review_generation=1,
            )
            cursor = db.execute(
                """
                insert into feishu_deliveries (
                    reply_task_id, attempt_id, app_id, chat_id,
                    reply_to_message_id, reply_in_thread, reply_text,
                    reply_format, mention_open_ids_json, payload_sha256,
                    idempotency_key, expected_chunks, chunk_plan_sha256,
                    review_generation, approval_hash
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(reply_task_id) do nothing
                """,
                (
                    reply_task_id,
                    attempt_id,
                    app_id,
                    chat_id,
                    reply_to_message_id,
                    int(reply_in_thread),
                    payload.text,
                    payload.kind,
                    mentions_json,
                    payload.sha256(),
                    stable_key,
                    expected_chunks,
                    plan_hash,
                    1,
                    approval_hash,
                ),
            )
            row = db.execute(
                "select * from feishu_deliveries where reply_task_id=?",
                (reply_task_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Feishu delivery was not persisted")
            if cursor.rowcount == 1:
                self._sync_feishu_attempt_from_delivery(db, row)
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id,
                    entity_type="delivery",
                    entity_id=row["id"],
                    event_type="created",
                    new_state=row["status"],
                    actor="consumer",
                )
            else:
                immutable = {
                    "app_id": app_id,
                    "chat_id": chat_id,
                    "reply_to_message_id": reply_to_message_id,
                    "reply_in_thread": int(reply_in_thread),
                }
                if any(row[name] != value for name, value in immutable.items()):
                    raise ValueError(
                        "existing Feishu delivery identity does not match"
                    )
                if int(row["attempt_id"]) != attempt_id:
                    db.execute(
                        """
                        update reply_attempts
                        set send_status='failed',
                            send_error='superseded_by_existing_delivery',
                            updated_at=current_timestamp
                        where id=? and channel='feishu'
                          and send_status in ('pending', 'processing')
                        """,
                        (attempt_id,),
                    )
                    self._append_feishu_audit_event(
                        db,
                        app_id=app_id,
                        entity_type="reply_attempt",
                        entity_id=attempt_id,
                        event_type="attempt_superseded_by_existing_delivery",
                        previous_state=attempt["send_status"],
                        new_state="failed",
                        actor="consumer-recovery",
                    )
            return self._feishu_delivery_from_row(row)

    def get_feishu_delivery(self, delivery_id: int) -> FeishuDelivery | None:
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
        return self._feishu_delivery_from_row(row) if row is not None else None

    @staticmethod
    def _normalize_feishu_delivery_message_ids(
        message_ids,
    ) -> tuple[str, ...]:
        if message_ids is None:
            return ()
        if isinstance(message_ids, str) or not isinstance(
            message_ids, (tuple, list)
        ):
            raise ValueError("Feishu delivery message_ids must be a sequence")
        if len(message_ids) > 100:
            raise ValueError("Feishu delivery produced too many message IDs")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in message_ids:
            if not isinstance(raw, str):
                raise ValueError("Feishu delivery message ID is invalid")
            value = raw.strip()
            if (
                not value
                or value != raw
                or len(value) > 512
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError("Feishu delivery message ID is invalid")
            if value in seen:
                raise ValueError("Feishu delivery message IDs must be unique")
            seen.add(value)
            normalized.append(value)
        return tuple(normalized)

    @classmethod
    def _validate_feishu_delivery_receipt_prefix(
        cls,
        db: sqlite3.Connection,
        delivery: sqlite3.Row,
        *,
        allow_primary_without_receipt: bool = False,
    ) -> list[sqlite3.Row]:
        """Return a proven contiguous prefix or reject corrupted receipts."""
        rows = db.execute(
            """
            select * from feishu_delivery_receipts
            where delivery_id=? order by ordinal
            """,
            (delivery["id"],),
        ).fetchall()
        expected_chunks = int(delivery["expected_chunks"] or 0)
        if expected_chunks <= 0 or len(rows) > expected_chunks:
            raise ValueError("Feishu delivery receipt prefix exceeds chunk plan")
        message_ids = cls._normalize_feishu_delivery_message_ids(
            tuple(row["message_id"] for row in rows)
        )
        for expected_ordinal, row in enumerate(rows):
            if int(row["ordinal"]) != expected_ordinal:
                raise ValueError(
                    "Feishu delivery receipts are not a contiguous ordered prefix"
                )
            if row["app_id"] != delivery["app_id"]:
                raise ValueError("Feishu delivery receipt App ID does not match")
            if row["status"] != "active" or int(row["recall_action_id"] or 0):
                raise ValueError("Feishu delivery receipt prefix is not active")
        primary = str(delivery["feishu_message_id"] or "")
        if message_ids:
            if primary != message_ids[0]:
                raise ValueError(
                    "Feishu delivery primary message ID does not match receipts"
                )
        elif primary and not allow_primary_without_receipt:
            raise ValueError(
                "Feishu delivery primary message ID has no durable receipt"
            )
        return rows

    @classmethod
    def _persist_feishu_delivery_receipts(
        cls,
        db: sqlite3.Connection,
        delivery: sqlite3.Row,
        message_ids: tuple[str, ...],
        *,
        allow_existing_prefix: bool = False,
    ) -> None:
        if not message_ids or message_ids[0] != delivery["feishu_message_id"]:
            raise ValueError(
                "Feishu delivery receipts require the compatible primary ID"
            )
        existing = cls._validate_feishu_delivery_receipt_prefix(
            db,
            delivery,
            allow_primary_without_receipt=True,
        )
        if existing:
            existing_ids = [row["message_id"] for row in existing]
            if existing_ids != list(message_ids[: len(existing_ids)]):
                raise ValueError("Feishu delivery receipt replay does not match")
            if any(row["app_id"] != delivery["app_id"] for row in existing):
                raise ValueError("Feishu delivery receipt App ID does not match")
            if len(existing) == len(message_ids):
                return
            if not allow_existing_prefix:
                raise ValueError("Feishu delivery receipt set is incomplete")
        for ordinal, message_id in enumerate(
            message_ids[len(existing) :], start=len(existing)
        ):
            db.execute(
                """
                insert into feishu_delivery_receipts (
                    delivery_id, app_id, ordinal, message_id, status
                ) values (?, ?, ?, ?, 'active')
                """,
                (delivery["id"], delivery["app_id"], ordinal, message_id),
            )
        cls._validate_feishu_delivery_receipt_prefix(db, delivery)

    def begin_feishu_delivery_mutation(
        self,
        delivery_id: int,
        *,
        app_id: str,
        lease_token: str,
        now: str = "",
    ) -> FeishuDelivery | None:
        """Fence the first remote send against a newer same-root trigger.

        ``None`` means ingress, or this transaction itself, safely superseded
        the delivery before any remote mutation could begin.  Once this marker
        is durable, ingress must treat the delivery as potentially mutated and
        leave final-state recovery to the sender/reconciliation workflow.
        """
        if delivery_id <= 0 or not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu delivery mutation identity is incomplete")
        mutation_at = now or datetime.now().astimezone().isoformat()
        try:
            parsed = datetime.fromisoformat(mutation_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Feishu delivery mutation time is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Feishu delivery mutation time requires timezone")

        with self._connect() as db:
            db.execute("begin immediate")
            delivery = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            if delivery is None:
                raise ValueError("Feishu delivery not found")
            if delivery["app_id"] != app_id:
                raise PermissionError(
                    "Feishu delivery App ID does not match runtime"
                )
            if (
                delivery["status"] == "rejected"
                and delivery["error_code"] == "superseded"
            ):
                return None
            if (
                delivery["status"] != "sending"
                or delivery["lease_token"] != lease_token
            ):
                raise ValueError("Feishu delivery send lease is no longer active")
            self._validate_feishu_delivery_binding(
                db, delivery, require_target_identity=True
            )
            receipts = self._validate_feishu_delivery_receipt_prefix(
                db, delivery
            )
            if receipts:
                # The first remote-mutation fence linearizes the complete
                # immutable delivery. A proven prefix must finish in order.
                updated = db.execute(
                    """
                    update feishu_deliveries
                    set mutation_started_at=case
                            when mutation_started_at='' then ?
                            else mutation_started_at
                        end,
                        locked_at=?, updated_at=current_timestamp
                    where id=? and status='sending' and lease_token=?
                    """,
                    (mutation_at, mutation_at, delivery_id, lease_token),
                )
                if updated.rowcount != 1:
                    raise ValueError(
                        "Feishu delivery mutation fence lost atomic race"
                    )
                row = db.execute(
                    "select * from feishu_deliveries where id=?", (delivery_id,)
                ).fetchone()
                return self._feishu_delivery_from_row(row)
            if delivery["mutation_started_at"]:
                raise ValueError(
                    "Feishu delivery remote mutation already started"
                )
            current = db.execute(
                """
                select * from feishu_events
                where reply_task_id=? and app_id=? and chat_id=?
                  and message_id=? and eligibility_status='eligible'
                """,
                (
                    delivery["reply_task_id"],
                    app_id,
                    delivery["chat_id"],
                    delivery["reply_to_message_id"],
                ),
            ).fetchone()
            if current is None:
                raise ValueError("Feishu delivery trigger event is unavailable")
            reference_root = self._feishu_reference_root(current)
            newer = db.execute(
                """
                select events.id
                from feishu_events as events
                where events.app_id=? and events.chat_id=?
                  and events.eligibility_status='eligible'
                  and coalesce(nullif(events.thread_id, ''),
                               nullif(events.root_message_id, ''),
                               events.message_id)=?
                  and (
                    events.event_create_time_ms>?
                    or (
                      events.event_create_time_ms=? and events.id>?
                    )
                  )
                order by events.event_create_time_ms desc, events.id desc
                limit 1
                """,
                (
                    app_id,
                    delivery["chat_id"],
                    reference_root,
                    current["event_create_time_ms"],
                    current["event_create_time_ms"],
                    current["id"],
                ),
            ).fetchone()
            if newer is not None:
                updated = db.execute(
                        """
                        update feishu_deliveries
                        set status='rejected', lease_token='', locked_at='',
                            approved_at='', approved_by='', available_at='',
                            error_code='superseded',
                            error='superseded_by_newer_feishu_trigger',
                            updated_at=current_timestamp
                        where id=? and app_id=? and status='sending'
                          and lease_token=? and mutation_started_at=''
                          and not exists (
                            select 1
                            from feishu_delivery_receipts as receipts
                            where receipts.delivery_id=feishu_deliveries.id
                          )
                        """,
                        (delivery_id, app_id, lease_token),
                )
                if updated.rowcount != 1:
                    raise ValueError(
                        "Feishu delivery mutation fence lost atomic race"
                    )
                rejected = db.execute(
                    "select * from feishu_deliveries where id=?", (delivery_id,)
                ).fetchone()
                self._sync_feishu_attempt_from_delivery(db, rejected)
                self._cancel_feishu_local_notifications_for_task_db(
                    db,
                    reply_task_id=int(delivery["reply_task_id"]),
                    app_id=app_id,
                    actor="sender",
                )
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id,
                    entity_type="delivery",
                    entity_id=delivery_id,
                    event_type="trigger_superseded_at_send_fence",
                    previous_state="sending",
                    new_state="rejected",
                    actor="sender",
                    detail=f"newer_event_record_id={int(newer['id'])}",
                )
                return None

            updated = db.execute(
                """
                update feishu_deliveries
                set mutation_started_at=?, locked_at=?,
                    updated_at=current_timestamp
                where id=? and app_id=? and status='sending'
                  and lease_token=? and mutation_started_at=''
                  and not exists (
                    select 1 from feishu_delivery_receipts as receipts
                    where receipts.delivery_id=feishu_deliveries.id
                  )
                """,
                (mutation_at, mutation_at, delivery_id, app_id, lease_token),
            )
            if updated.rowcount != 1:
                raise ValueError("Feishu delivery mutation fence lost atomic race")
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="delivery",
                entity_id=delivery_id,
                event_type="mutation_fence_acquired",
                previous_state="sending",
                new_state="sending",
                actor="sender",
                detail=f"trigger_event_record_id={int(current['id'])}",
            )
            row = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            return self._feishu_delivery_from_row(row)

    def record_feishu_delivery_chunk(
        self,
        delivery_id: int,
        *,
        app_id: str,
        lease_token: str,
        ordinal: int,
        expected_chunks: int,
        message_id: str,
        request_log_id: str = "",
    ) -> FeishuDeliveryReceipt:
        """Persist one proven remote result before attempting the next chunk."""
        if not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu delivery chunk owner identity is incomplete")
        if (
            isinstance(ordinal, bool)
            or isinstance(expected_chunks, bool)
            or not isinstance(ordinal, int)
            or not isinstance(expected_chunks, int)
            or ordinal < 0
            or expected_chunks <= 0
            or ordinal >= expected_chunks
        ):
            raise ValueError("Feishu delivery chunk position is invalid")
        [normalized_message_id] = self._normalize_feishu_delivery_message_ids(
            (message_id,)
        )
        normalized_log_id = request_log_id.strip()
        if request_log_id and (
            request_log_id != normalized_log_id
            or len(normalized_log_id) > 256
            or any(ord(character) < 32 for character in normalized_log_id)
        ):
            raise ValueError("Feishu delivery request log ID is invalid")
        with self._connect() as db:
            db.execute("begin immediate")
            delivery = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            if delivery is None:
                raise ValueError("Feishu delivery not found")
            if delivery["app_id"] != app_id:
                raise PermissionError(
                    "Feishu delivery App ID does not match runtime"
                )
            if (
                delivery["status"] != "sending"
                or delivery["lease_token"] != lease_token
            ):
                raise ValueError("Feishu delivery send lease is no longer active")
            self._validate_feishu_delivery_binding(
                db, delivery, require_target_identity=True
            )
            if int(delivery["expected_chunks"]) != expected_chunks:
                raise ValueError("Feishu delivery chunk plan changed")
            existing = self._validate_feishu_delivery_receipt_prefix(
                db, delivery
            )
            if ordinal < len(existing):
                prior = existing[ordinal]
                if (
                    prior["message_id"] != normalized_message_id
                    or prior["app_id"] != app_id
                    or prior["request_log_id"] != normalized_log_id
                ):
                    raise ValueError("Feishu delivery chunk replay does not match")
                return self._feishu_delivery_receipt_from_row(prior)
            if ordinal != len(existing):
                raise ValueError("Feishu delivery chunks must be recorded in order")
            cursor = db.execute(
                """
                insert into feishu_delivery_receipts (
                    delivery_id, app_id, ordinal, message_id,
                    request_log_id, status
                ) values (?, ?, ?, ?, ?, 'active')
                """,
                (
                    delivery_id,
                    app_id,
                    ordinal,
                    normalized_message_id,
                    normalized_log_id,
                ),
            )
            heartbeat_at = datetime.now().astimezone().isoformat()
            if ordinal == 0:
                db.execute(
                    """
                    update feishu_deliveries
                    set feishu_message_id=?, request_log_id=?,
                        remote_failures=0, locked_at=?,
                        mutation_started_at=case
                            when mutation_started_at='' then ?
                            else mutation_started_at
                        end,
                        updated_at=current_timestamp
                    where id=? and status='sending' and lease_token=?
                    """,
                    (
                        normalized_message_id,
                        normalized_log_id,
                        heartbeat_at,
                        heartbeat_at,
                        delivery_id,
                        lease_token,
                    ),
                )
            else:
                db.execute(
                    """
                    update feishu_deliveries
                    set request_log_id=case when ?<>'' then ? else request_log_id end,
                        remote_failures=0, locked_at=?,
                        mutation_started_at=case
                            when mutation_started_at='' then ?
                            else mutation_started_at
                        end,
                        updated_at=current_timestamp
                    where id=? and status='sending' and lease_token=?
                    """,
                    (
                        normalized_log_id,
                        normalized_log_id,
                        heartbeat_at,
                        heartbeat_at,
                        delivery_id,
                        lease_token,
                    ),
                )
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="delivery",
                entity_id=delivery_id,
                event_type="chunk_sent",
                previous_state="sending",
                new_state="sending",
                actor="sender",
                detail=f"ordinal={ordinal};expected={expected_chunks}",
            )
            row = db.execute(
                "select * from feishu_delivery_receipts where id=?",
                (cursor.lastrowid,),
            ).fetchone()
            return self._feishu_delivery_receipt_from_row(row)

    def heartbeat_feishu_delivery_send(
        self,
        delivery_id: int,
        *,
        app_id: str,
        lease_token: str,
        now: str = "",
    ) -> FeishuDelivery:
        """Refresh only the currently leased delivery using a token CAS."""

        if delivery_id <= 0 or not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu delivery heartbeat identity is incomplete")
        heartbeat_at = now or datetime.now().astimezone().isoformat()
        try:
            parsed = datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Feishu delivery heartbeat time is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Feishu delivery heartbeat time requires timezone")
        with self._connect() as db:
            db.execute("begin immediate")
            cursor = db.execute(
                """
                update feishu_deliveries
                set locked_at=?, updated_at=current_timestamp
                where id=? and app_id=? and status='sending' and lease_token=?
                """,
                (heartbeat_at, delivery_id, app_id, lease_token),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu delivery send lease is no longer active")
            row = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            return self._feishu_delivery_from_row(row)

    def validate_feishu_delivery_receipt_prefix(
        self, delivery_id: int, *, app_id: str
    ) -> list[FeishuDeliveryReceipt]:
        """Load the only receipt shape that is safe for deterministic resume."""
        if not app_id.strip():
            raise ValueError("Feishu receipt validation requires app_id")
        with self._connect() as db:
            delivery = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            if delivery is None:
                raise ValueError("Feishu delivery not found")
            if delivery["app_id"] != app_id:
                raise PermissionError(
                    "Feishu delivery App ID does not match runtime"
                )
            rows = self._validate_feishu_delivery_receipt_prefix(db, delivery)
        return [self._feishu_delivery_receipt_from_row(row) for row in rows]

    def list_feishu_delivery_receipts(
        self,
        *,
        delivery_id: int = 0,
        app_id: str = "",
        status: str = "",
    ) -> list[FeishuDeliveryReceipt]:
        if status and status not in FEISHU_RECEIPT_STATUSES:
            raise ValueError("unknown Feishu delivery receipt status")
        where: list[str] = []
        args: list[str | int] = []
        if delivery_id:
            where.append("delivery_id=?")
            args.append(delivery_id)
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if status:
            where.append("status=?")
            args.append(status)
        clause = f"where {' and '.join(where)}" if where else ""
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_delivery_receipts {clause}
                order by delivery_id, ordinal
                """,
                args,
            ).fetchall()
        return [self._feishu_delivery_receipt_from_row(row) for row in rows]

    def get_feishu_delivery_receipt(
        self, *, app_id: str, message_id: str
    ) -> FeishuDeliveryReceipt | None:
        if not app_id.strip() or not message_id.strip():
            raise ValueError("Feishu delivery receipt identity is incomplete")
        with self._connect() as db:
            row = db.execute(
                """
                select * from feishu_delivery_receipts
                where app_id=? and message_id=?
                """,
                (app_id.strip(), message_id.strip()),
            ).fetchone()
        return (
            self._feishu_delivery_receipt_from_row(row)
            if row is not None
            else None
        )

    @staticmethod
    def _feishu_recall_target_row(
        db: sqlite3.Connection,
        *,
        app_id: str,
        chat_id: str,
        message_id: str,
    ) -> sqlite3.Row | None:
        return db.execute(
            """
            select receipts.*, deliveries.status as delivery_status,
                   deliveries.chat_id as delivery_chat_id
            from feishu_delivery_receipts as receipts
            join feishu_deliveries as deliveries
              on deliveries.id=receipts.delivery_id
             and deliveries.app_id=receipts.app_id
            where receipts.app_id=? and receipts.message_id=?
              and deliveries.chat_id=? and receipts.status='active'
              and deliveries.status in ('sent', 'failed', 'rejected')
            """,
            (app_id, message_id, chat_id),
        ).fetchone()

    def validate_feishu_delivery_receipt_for_recall(
        self, receipt_id: int, *, app_id: str
    ) -> tuple[FeishuDeliveryReceipt, FeishuDelivery]:
        """Return an active receipt only at the unified terminal boundary."""
        if receipt_id <= 0 or not app_id.strip():
            raise ValueError("Feishu recall receipt identity is incomplete")
        with self._connect() as db:
            row = db.execute(
                """
                select receipts.message_id, deliveries.chat_id
                from feishu_delivery_receipts as receipts
                join feishu_deliveries as deliveries
                  on deliveries.id=receipts.delivery_id
                 and deliveries.app_id=receipts.app_id
                where receipts.id=? and receipts.app_id=?
                """,
                (receipt_id, app_id.strip()),
            ).fetchone()
            target = (
                self._feishu_recall_target_row(
                    db,
                    app_id=app_id.strip(),
                    chat_id=row["chat_id"],
                    message_id=row["message_id"],
                )
                if row is not None
                else None
            )
            if target is None or int(target["id"]) != receipt_id:
                raise PermissionError(
                    "Feishu recall target is not an active terminal receipt"
                )
            delivery = db.execute(
                "select * from feishu_deliveries where id=?",
                (target["delivery_id"],),
            ).fetchone()
        return (
            self._feishu_delivery_receipt_from_row(target),
            self._feishu_delivery_from_row(delivery),
        )

    @staticmethod
    def _validate_feishu_message_action_binding(
        db: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        require_active_target: bool,
    ) -> FeishuMessageAction:
        try:
            action = AutoReplyStore._feishu_message_action_from_row(row)
        except Exception as exc:
            raise ValueError("Feishu message action identity is invalid") from exc
        expected_approval_hash = action_approval_hash(
            reply_task_id=action.reply_task_id,
            attempt_id=action.attempt_id,
            app_id=action.app_id,
            chat_id=action.chat_id,
            action_key=action.action_key,
            kind=action.kind,
            target_id=action.target_message_id or action.target_open_id,
            payload_sha256=action.payload_sha256,
            idempotency_key=action.idempotency_key,
            risk=action.risk,
            review_generation=action.review_generation,
        )
        if action.approval_hash != expected_approval_hash:
            raise ValueError("Feishu message action approval hash does not match")
        task = db.execute(
            "select * from reply_tasks where id=?", (action.reply_task_id,)
        ).fetchone()
        attempt = db.execute(
            "select * from reply_attempts where id=?", (action.attempt_id,)
        ).fetchone()
        if task is None or attempt is None:
            raise ValueError("Feishu message action audit binding is unavailable")
        try:
            trigger = json.loads(task["trigger_message_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Feishu message action trigger is invalid") from exc
        if not isinstance(trigger, dict):
            raise ValueError("Feishu message action trigger is invalid")
        expected_conversation = AutoReplyStore._feishu_task_conversation_id(
            action.app_id, action.chat_id
        )
        if (
            task["channel"] != "feishu"
            or attempt["channel"] != "feishu"
            or attempt["conversation_id"] != task["conversation_id"]
            or attempt["trigger_message_id"] != task["trigger_message_id"]
            or trigger.get("app_id") != action.app_id
            or trigger.get("chat_id") != action.chat_id
            or trigger.get("message_id") != task["trigger_message_id"]
            or task["conversation_id"]
            not in {expected_conversation, action.chat_id}
        ):
            raise ValueError("Feishu message action does not match its task")
        if action.kind == "add_reaction":
            if action.target_message_id != task["trigger_message_id"]:
                raise PermissionError(
                    "Feishu reaction target is not its persisted trigger"
                )
        elif action.kind == "recall_message":
            receipt = AutoReplyStore._feishu_recall_target_row(
                db,
                app_id=action.app_id,
                chat_id=action.chat_id,
                message_id=action.target_message_id,
            )
            if require_active_target and receipt is None:
                raise PermissionError(
                    "Feishu recall target is not an active terminal receipt"
                )
        return action

    @staticmethod
    def _normalize_feishu_handoff_allowlist(values) -> frozenset[str]:
        if values is None:
            return frozenset()
        if isinstance(values, str):
            raise ValueError("Feishu handoff allowlist must be a local sequence")
        normalized: set[str] = set()
        for raw in values:
            if not isinstance(raw, str):
                raise ValueError("Feishu handoff allowlist contains invalid target")
            value = raw.strip()
            suffix = value[3:] if value.startswith("ou_") else ""
            if (
                value != raw
                or not suffix
                or len(value) > 256
                or any(
                    character
                    not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                    for character in suffix
                )
            ):
                raise ValueError("Feishu handoff allowlist contains invalid target")
            normalized.add(value)
        return frozenset(normalized)

    @staticmethod
    def _normalize_feishu_action_review_actor(
        value: str, *, operation: str
    ) -> str:
        if not isinstance(value, str):
            raise ValueError(
                f"Feishu message action {operation} requires a valid actor"
            )
        normalized = value.strip()
        if (
            not normalized
            or normalized != value
            or len(normalized) > 128
            or any(ord(character) < 32 for character in normalized)
        ):
            raise ValueError(
                f"Feishu message action {operation} requires a valid actor"
            )
        return normalized

    @staticmethod
    def _normalize_feishu_action_requeue_time(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("invalid Feishu message action requeue available_at")
        normalized = value.strip()
        if not normalized:
            return ""
        if normalized != value:
            raise ValueError("invalid Feishu message action requeue available_at")
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "invalid Feishu message action requeue available_at"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(
                "Feishu message action requeue available_at requires timezone"
            )
        return parsed.astimezone(timezone.utc).isoformat()

    def create_feishu_message_action(
        self,
        action: FeishuMessageAction,
        *,
        handoff_target_allowlist=(),
        actor: str = "consumer",
    ) -> FeishuMessageAction:
        """Persist one closed action after proving all local ownership links."""
        action = FeishuMessageAction.model_validate(action.model_dump())
        if (
            action.id != 0
            or action.review_generation != 1
            or action.status != "ready"
            or action.attempts != 0
            or action.remote_failures != 0
            or action.lease_token
            or action.mutation_started_at
            or action.approved_at
            or action.approved_by
            or action.remote_id
            or action.request_log_id
            or action.error_code
            or action.error
        ):
            raise ValueError("new Feishu message action contains mutable state")
        allowlist = self._normalize_feishu_handoff_allowlist(
            handoff_target_allowlist
        )
        if action.kind == "handoff_notify" and action.target_open_id not in allowlist:
            raise PermissionError("Feishu handoff target is not locally allowlisted")
        with self._connect() as db:
            db.execute("begin immediate")
            task = db.execute(
                "select * from reply_tasks where id=?", (action.reply_task_id,)
            ).fetchone()
            attempt = db.execute(
                "select * from reply_attempts where id=?", (action.attempt_id,)
            ).fetchone()
            if task is None or attempt is None:
                raise ValueError("Feishu message action task or attempt is missing")
            try:
                trigger = json.loads(task["trigger_message_json"] or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("Feishu message action trigger is invalid") from exc
            expected_conversation = self._feishu_task_conversation_id(
                action.app_id, action.chat_id
            )
            if (
                not isinstance(trigger, dict)
                or task["channel"] != "feishu"
                or attempt["channel"] != "feishu"
                or attempt["conversation_id"] != task["conversation_id"]
                or attempt["trigger_message_id"] != task["trigger_message_id"]
                or trigger.get("app_id") != action.app_id
                or trigger.get("chat_id") != action.chat_id
                or trigger.get("message_id") != task["trigger_message_id"]
                or task["conversation_id"]
                not in {expected_conversation, action.chat_id}
            ):
                raise ValueError("Feishu message action does not match its task")
            if action.kind == "add_reaction":
                if action.target_message_id != task["trigger_message_id"]:
                    raise PermissionError(
                        "Feishu reaction target is not its persisted trigger"
                    )
            elif action.kind == "recall_message":
                owned = self._feishu_recall_target_row(
                    db,
                    app_id=action.app_id,
                    chat_id=action.chat_id,
                    message_id=action.target_message_id,
                )
                if owned is None:
                    raise PermissionError(
                        "Feishu recall target is not an active terminal receipt"
                    )
            existing = db.execute(
                """
                select * from feishu_message_actions
                where reply_task_id=? and action_key=?
                """,
                (action.reply_task_id, action.action_key),
            ).fetchone()
            if existing is not None:
                immutable_fields = (
                    "attempt_id",
                    "app_id",
                    "chat_id",
                    "kind",
                    "target_message_id",
                    "target_open_id",
                    "payload_json",
                    "payload_sha256",
                    "idempotency_key",
                    "review_generation",
                    "approval_hash",
                    "risk",
                )
                if any(
                    existing[field] != getattr(action, field)
                    for field in immutable_fields
                ):
                    raise ValueError("Feishu message action replay does not match")
                return self._feishu_message_action_from_row(existing)
            try:
                cursor = db.execute(
                    """
                    insert into feishu_message_actions (
                        reply_task_id, attempt_id, app_id, chat_id, action_key,
                        kind, target_message_id, target_open_id, payload_json,
                        payload_sha256, idempotency_key, review_generation,
                        approval_hash, risk
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action.reply_task_id,
                        action.attempt_id,
                        action.app_id,
                        action.chat_id,
                        action.action_key,
                        action.kind,
                        action.target_message_id,
                        action.target_open_id,
                        action.payload_json,
                        action.payload_sha256,
                        action.idempotency_key,
                        action.review_generation,
                        action.approval_hash,
                        action.risk,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Feishu message action identity conflicts") from exc
            row = db.execute(
                "select * from feishu_message_actions where id=?",
                (cursor.lastrowid,),
            ).fetchone()
            self._validate_feishu_message_action_binding(
                db, row, require_active_target=True
            )
            self._append_feishu_audit_event(
                db,
                app_id=action.app_id,
                entity_type="message_action",
                entity_id=row["id"],
                event_type="created",
                new_state="ready",
                actor=actor,
                detail=f"kind={action.kind};risk={action.risk}",
            )
            return self._feishu_message_action_from_row(row)

    def get_feishu_message_action(
        self, action_id: int
    ) -> FeishuMessageAction | None:
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
        return self._feishu_message_action_from_row(row) if row is not None else None

    def list_feishu_message_actions(
        self,
        *,
        app_id: str = "",
        statuses: tuple[str, ...] | list[str] | set[str] = (),
        kinds: tuple[str, ...] | list[str] | set[str] = (),
        limit: int = 100,
    ) -> list[FeishuMessageAction]:
        if limit <= 0:
            return []
        selected_statuses = tuple(statuses)
        if set(selected_statuses) - FEISHU_ACTION_STATUSES:
            raise ValueError("unknown Feishu message action status")
        selected_kinds = tuple(kinds)
        allowed_kinds = {"add_reaction", "recall_message", "handoff_notify"}
        if set(selected_kinds) - allowed_kinds:
            raise ValueError("unknown Feishu message action kind")
        where: list[str] = []
        args: list[str | int] = []
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if selected_statuses:
            placeholders = ",".join("?" for _ in selected_statuses)
            where.append(f"status in ({placeholders})")
            args.extend(selected_statuses)
        if selected_kinds:
            placeholders = ",".join("?" for _ in selected_kinds)
            where.append(f"kind in ({placeholders})")
            args.extend(selected_kinds)
        clause = f"where {' and '.join(where)}" if where else ""
        args.append(min(limit, 1000))
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_message_actions {clause}
                order by id limit ?
                """,
                args,
            ).fetchall()
        return [self._feishu_message_action_from_row(row) for row in rows]

    def validate_feishu_message_action_for_send(
        self,
        action_id: int,
        *,
        app_id: str,
        lease_token: str,
    ) -> FeishuMessageAction:
        if not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu message action send lease is required")
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu message action not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu message action App ID does not match")
            if row["status"] != "sending" or row["lease_token"] != lease_token:
                raise ValueError("Feishu message action lease is no longer active")
            return self._validate_feishu_message_action_binding(
                db, row, require_active_target=True
            )

    def begin_feishu_message_action_mutation(
        self,
        action_id: int,
        *,
        app_id: str,
        lease_token: str,
        now: str = "",
    ) -> FeishuMessageAction | None:
        """Fence the first SDK mutation against a newer same-root trigger."""
        if action_id <= 0 or not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu message action mutation identity is incomplete")
        mutation_at = now or datetime.now().astimezone().isoformat()
        try:
            parsed = datetime.fromisoformat(mutation_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "Feishu message action mutation time is invalid"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(
                "Feishu message action mutation time requires timezone"
            )

        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu message action not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu message action App ID does not match")
            if row["status"] == "rejected" and row["error_code"] == "superseded":
                return None
            if row["status"] != "sending" or row["lease_token"] != lease_token:
                raise ValueError("Feishu message action lease is no longer active")
            action = self._validate_feishu_message_action_binding(
                db, row, require_active_target=True
            )
            if row["mutation_started_at"]:
                raise ValueError(
                    "Feishu message action remote mutation already started"
                )

            if action.kind == "recall_message":
                # Recall is bound to a terminal app-owned receipt rather than
                # to the continued retention or freshness of its trigger event.
                # This keeps explicit R4 cleanup operable after event purge.
                updated = db.execute(
                    """
                    update feishu_message_actions
                    set mutation_started_at=?, locked_at=?,
                        updated_at=current_timestamp
                    where id=? and app_id=? and status='sending'
                      and lease_token=? and mutation_started_at=''
                      and remote_id=''
                    """,
                    (mutation_at, mutation_at, action_id, app_id, lease_token),
                )
                if updated.rowcount != 1:
                    raise ValueError(
                        "Feishu message action mutation fence lost atomic race"
                    )
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id,
                    entity_type="message_action",
                    entity_id=action_id,
                    event_type="mutation_fence_acquired",
                    previous_state="sending",
                    new_state="sending",
                    actor="action-sender",
                    detail="receipt_owned_recall=1",
                )
                current_row = db.execute(
                    "select * from feishu_message_actions where id=?",
                    (action_id,),
                ).fetchone()
                return self._feishu_message_action_from_row(current_row)

            current = db.execute(
                """
                select * from feishu_events
                where reply_task_id=? and app_id=? and chat_id=?
                  and eligibility_status='eligible'
                """,
                (action.reply_task_id, app_id, action.chat_id),
            ).fetchone()
            if current is None:
                raise ValueError("Feishu message action trigger event is unavailable")
            reference_root = self._feishu_reference_root(current)
            newer = db.execute(
                """
                select events.id
                from feishu_events as events
                where events.app_id=? and events.chat_id=?
                  and events.eligibility_status='eligible'
                  and coalesce(nullif(events.thread_id, ''),
                               nullif(events.root_message_id, ''),
                               events.message_id)=?
                  and (
                    events.event_create_time_ms>?
                    or (
                      events.event_create_time_ms=? and events.id>?
                    )
                  )
                order by events.event_create_time_ms desc, events.id desc
                limit 1
                """,
                (
                    app_id,
                    action.chat_id,
                    reference_root,
                    current["event_create_time_ms"],
                    current["event_create_time_ms"],
                    current["id"],
                ),
            ).fetchone()
            # Recall is an explicitly approved cleanup of an app-owned remote
            # effect, so a newer trigger in the same thread must not strand it.
            # Reaction and handoff actions remain tied to their trigger epoch.
            if newer is not None and action.kind != "recall_message":
                updated = db.execute(
                    """
                    update feishu_message_actions
                    set status='rejected', lease_token='', locked_at='',
                        approved_at='', approved_by='', available_at='',
                        error_code='superseded',
                        error='superseded_by_newer_feishu_trigger',
                        updated_at=current_timestamp
                    where id=? and app_id=? and status='sending'
                      and lease_token=? and mutation_started_at=''
                      and remote_id=''
                    """,
                    (action_id, app_id, lease_token),
                )
                if updated.rowcount != 1:
                    raise ValueError(
                        "Feishu message action mutation fence lost atomic race"
                    )
                self._cancel_feishu_local_notifications_for_task_db(
                    db,
                    reply_task_id=action.reply_task_id,
                    app_id=app_id,
                    actor="action-sender",
                )
                self._append_feishu_audit_event(
                    db,
                    app_id=app_id,
                    entity_type="message_action",
                    entity_id=action_id,
                    event_type="trigger_superseded_at_mutation_fence",
                    previous_state="sending",
                    new_state="rejected",
                    actor="action-sender",
                    detail=f"newer_event_record_id={int(newer['id'])}",
                )
                return None

            updated = db.execute(
                """
                update feishu_message_actions
                set mutation_started_at=?, locked_at=?,
                    updated_at=current_timestamp
                where id=? and app_id=? and status='sending'
                  and lease_token=? and mutation_started_at=''
                  and remote_id=''
                """,
                (mutation_at, mutation_at, action_id, app_id, lease_token),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "Feishu message action mutation fence lost atomic race"
                )
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="message_action",
                entity_id=action_id,
                event_type="mutation_fence_acquired",
                previous_state="sending",
                new_state="sending",
                actor="action-sender",
                detail=f"trigger_event_record_id={int(current['id'])}",
            )
            current_row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            return self._feishu_message_action_from_row(current_row)

    def approve_feishu_message_action(
        self,
        action_id: int,
        *,
        app_id: str,
        approved_by: str,
        expected_approval_hash: str,
        now: str = "",
    ) -> FeishuMessageAction:
        if not app_id.strip() or not approved_by.strip():
            raise ValueError("Feishu message action approval identity is incomplete")
        if (
            len(expected_approval_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_approval_hash
            )
        ):
            raise ValueError("Feishu message action approval hash is invalid")
        approved_at = now or datetime.now().astimezone().isoformat()
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu message action not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu message action App ID does not match")
            action = self._validate_feishu_message_action_binding(
                db, row, require_active_target=True
            )
            recalculated = action_approval_hash(
                reply_task_id=action.reply_task_id,
                attempt_id=action.attempt_id,
                app_id=action.app_id,
                chat_id=action.chat_id,
                action_key=action.action_key,
                kind=action.kind,
                target_id=action.target_message_id or action.target_open_id,
                payload_sha256=action.payload_sha256,
                idempotency_key=action.idempotency_key,
                risk=action.risk,
                review_generation=action.review_generation,
            )
            if (
                action.approval_hash != recalculated
                or expected_approval_hash != action.approval_hash
            ):
                raise ValueError("Feishu message action approval hash changed")
            if row["status"] not in {"ready", "retry"} or row["approved_at"]:
                raise ValueError("Feishu message action is not approvable")
            cursor = db.execute(
                """
                update feishu_message_actions
                set approved_at=?, approved_by=?, updated_at=current_timestamp
                where id=? and app_id=? and status in ('ready', 'retry')
                  and approved_at='' and approval_hash=?
                """,
                (
                    approved_at,
                    approved_by.strip(),
                    action_id,
                    app_id,
                    action.approval_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu message action approval lost atomic race")
            current = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="message_action",
                entity_id=action_id,
                event_type="approved",
                previous_state=row["status"],
                new_state=row["status"],
                actor=approved_by.strip(),
                detail=f"risk={row['risk']}",
            )
            return self._feishu_message_action_from_row(current)

    def claim_feishu_message_actions(
        self,
        limit: int,
        *,
        app_id: str,
        kinds: tuple[str, ...] | list[str] | set[str],
        send_mode: str = "confirm",
        action_id: int = 0,
        now: str = "",
    ) -> list[FeishuMessageAction]:
        if limit <= 0:
            return []
        if not app_id.strip():
            raise ValueError("Feishu message action claim requires app_id")
        if send_mode not in {"confirm", "auto"}:
            raise ValueError("Feishu message action send_mode is invalid")
        selected_kinds = tuple(kinds)
        allowed_kinds = {"add_reaction", "recall_message", "handoff_notify"}
        if not selected_kinds or set(selected_kinds) - allowed_kinds:
            raise ValueError("Feishu message action claim kinds are invalid")
        kind_placeholders = ",".join("?" for _ in selected_kinds)
        approval_clause = (
            "and candidate.approved_at<>''"
            if send_mode == "confirm"
            else "and (candidate.risk<>'R4' or candidate.approved_at<>'')"
        )
        action_clause = "and candidate.id=?" if action_id else ""
        now_expression = "current_timestamp" if not now else "?"
        args: list[str | int] = [app_id.strip(), *selected_kinds]
        if action_id:
            args.append(action_id)
        if now:
            args.append(now)
        args.append(min(limit, 100))
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select candidate.* from feishu_message_actions as candidate
                where candidate.app_id=?
                  and candidate.kind in ({kind_placeholders})
                  and candidate.status in ('ready', 'retry')
                  {approval_clause}
                  {action_clause}
                  and (
                    candidate.available_at=''
                    or datetime(candidate.available_at) <= datetime({now_expression})
                  )
                  and not exists (
                    select 1 from feishu_deliveries as delivery_blocker
                    where delivery_blocker.app_id=candidate.app_id
                      and delivery_blocker.chat_id=candidate.chat_id
                      and delivery_blocker.status in ('sending', 'send_unknown')
                  )
                  and candidate.id=(
                    select min(head.id) from feishu_message_actions as head
                    where head.app_id=candidate.app_id
                      and head.chat_id=candidate.chat_id
                      and head.status in (
                        'ready', 'retry', 'sending', 'result_unknown'
                      )
                  )
                order by candidate.id
                limit ?
                """,
                args,
            ).fetchall()
            claimed: list[FeishuMessageAction] = []
            for row in rows:
                try:
                    self._validate_feishu_message_action_binding(
                        db, row, require_active_target=True
                    )
                except (ValueError, PermissionError):
                    cursor = db.execute(
                        """
                        update feishu_message_actions
                        set status='failed', lease_token='', locked_at='',
                            approved_at='', approved_by='',
                            error_code='invalid_binding',
                            error='message_action_identity_unverifiable',
                            updated_at=current_timestamp
                        where id=? and status in ('ready', 'retry')
                        """,
                        (row["id"],),
                    )
                    if cursor.rowcount:
                        self._append_feishu_audit_event(
                            db,
                            app_id=row["app_id"],
                            entity_type="message_action",
                            entity_id=row["id"],
                            event_type="invalid_binding_quarantined",
                            previous_state=row["status"],
                            new_state="failed",
                            actor="action-sender",
                            detail="error_code=invalid_binding",
                        )
                    continue
                lease_token = uuid4().hex
                locked_at = now or datetime.now().astimezone().isoformat()
                cursor = db.execute(
                    """
                    update feishu_message_actions
                    set status='sending', attempts=attempts + 1,
                        lease_token=?, locked_at=?, available_at='',
                        error_code='', error='', updated_at=current_timestamp
                    where id=? and status in ('ready', 'retry')
                    """,
                    (lease_token, locked_at, row["id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Feishu message action claim lost atomic race")
                current = db.execute(
                    "select * from feishu_message_actions where id=?",
                    (row["id"],),
                ).fetchone()
                self._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="message_action",
                    entity_id=row["id"],
                    event_type="claimed",
                    previous_state=row["status"],
                    new_state="sending",
                    actor="action-sender",
                    detail=f"kind={row['kind']};risk={row['risk']}",
                )
                claimed.append(self._feishu_message_action_from_row(current))
            return claimed

    def claim_feishu_message_action(
        self,
        action_id: int,
        *,
        app_id: str,
        kinds: tuple[str, ...] | list[str] | set[str],
        send_mode: str = "confirm",
        now: str = "",
    ) -> FeishuMessageAction | None:
        rows = self.claim_feishu_message_actions(
            1,
            app_id=app_id,
            kinds=kinds,
            send_mode=send_mode,
            action_id=action_id,
            now=now,
        )
        return rows[0] if rows else None

    def transition_feishu_message_action(
        self,
        action_id: int,
        *,
        from_statuses: tuple[str, ...] | list[str] | set[str],
        to_status: str,
        app_id: str,
        expected_lease_token: str = "",
        remote_id: str = "",
        request_log_id: str = "",
        error_code: str = "",
        error: str = "",
        available_at: str = "",
        remote_failures: int | None = None,
        actor: str = "action-sender",
        audit_event_type: str = "",
    ) -> FeishuMessageAction:
        sources = tuple(from_statuses)
        if not sources or (set(sources) | {to_status}) - FEISHU_ACTION_STATUSES:
            raise ValueError("unknown Feishu message action status")
        invalid = [
            (source, to_status)
            for source in sources
            if (source, to_status) not in FEISHU_ACTION_TRANSITIONS
        ]
        if invalid:
            raise ValueError(f"invalid Feishu message action transition: {invalid}")
        safe_remote_id = remote_id.strip()
        safe_request_log_id = request_log_id.strip()
        if remote_id and (
            safe_remote_id != remote_id
            or len(safe_remote_id) > 512
            or any(ord(character) < 32 for character in safe_remote_id)
        ):
            raise ValueError("Feishu message action remote ID is invalid")
        if request_log_id and (
            safe_request_log_id != request_log_id
            or len(safe_request_log_id) > 256
            or any(ord(character) < 32 for character in safe_request_log_id)
        ):
            raise ValueError("Feishu message action request log ID is invalid")
        if to_status == "retry" and error_code not in FEISHU_ACTION_RETRYABLE_ERROR_CODES:
            raise ValueError("Feishu message action retry is not proven safe")
        if to_status == "failed" and error_code not in (
            FEISHU_ACTION_RETRYABLE_ERROR_CODES
            | FEISHU_ACTION_TERMINAL_ERROR_CODES
            | {"invalid_binding"}
        ):
            raise ValueError("Feishu message action failure is not definite")
        if to_status == "result_unknown" and error_code not in (
            FEISHU_ACTION_UNCERTAIN_ERROR_CODES
        ):
            raise ValueError("Feishu message action uncertainty code is invalid")
        if remote_failures is not None and (
            isinstance(remote_failures, bool) or remote_failures < 0
        ):
            raise ValueError("Feishu message action remote failures is invalid")
        placeholders = ",".join("?" for _ in sources)
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu message action not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu message action App ID does not match")
            if row["status"] not in sources:
                raise ValueError("Feishu message action status changed")
            if row["status"] == "sending":
                if (
                    not expected_lease_token
                    or row["lease_token"] != expected_lease_token
                ):
                    raise ValueError("Feishu message action lease is no longer active")
                action = self._validate_feishu_message_action_binding(
                    db, row, require_active_target=True
                )
            else:
                action = self._validate_feishu_message_action_binding(
                    db, row, require_active_target=True
                )
            if to_status == "sent":
                if action.kind in {"add_reaction", "handoff_notify"} and not safe_remote_id:
                    raise ValueError(
                        f"successful {action.kind} requires a remote identifier"
                    )
                if action.kind == "recall_message" and safe_remote_id:
                    raise ValueError("successful recall must not copy its target ID")
            safe_error = safe_observability_error(error)[:512]
            next_remote_failures = (
                int(row["remote_failures"])
                if remote_failures is None
                else int(remote_failures)
            )
            next_mutation_started_at = str(
                row["mutation_started_at"] or ""
            )
            if to_status == "retry":
                # Retry is allowed only for a provider result that proves the
                # attempted action was not applied.
                next_mutation_started_at = ""
            elif (
                to_status in {"sent", "result_unknown"}
                and not next_mutation_started_at
            ):
                next_mutation_started_at = (
                    datetime.now().astimezone().isoformat()
                )
            lease_clause = "and lease_token=?" if expected_lease_token else ""
            args: list[str | int] = [
                to_status,
                safe_remote_id or row["remote_id"],
                safe_request_log_id or row["request_log_id"],
                next_remote_failures,
                next_mutation_started_at,
                available_at,
                error_code,
                safe_error,
                action_id,
                app_id,
                *sources,
            ]
            if expected_lease_token:
                args.append(expected_lease_token)
            cursor = db.execute(
                f"""
                update feishu_message_actions
                set status=?, remote_id=?, request_log_id=?,
                    remote_failures=?, mutation_started_at=?,
                    lease_token='', locked_at='', available_at=?,
                    error_code=?, error=?, updated_at=current_timestamp
                where id=? and app_id=? and status in ({placeholders})
                  {lease_clause}
                """,
                args,
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu message action transition lost atomic race")
            if action.kind == "recall_message" and to_status in {
                "sent",
                "result_unknown",
            }:
                receipt_status = (
                    "recalled" if to_status == "sent" else "recall_unknown"
                )
                receipt = db.execute(
                    """
                    update feishu_delivery_receipts
                    set status=?, recall_action_id=?, updated_at=current_timestamp
                    where app_id=? and message_id=? and status='active'
                    """,
                    (
                        receipt_status,
                        action.id,
                        action.app_id,
                        action.target_message_id,
                    ),
                )
                if receipt.rowcount != 1:
                    raise ValueError("Feishu recall receipt transition lost atomic race")
            current = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="message_action",
                entity_id=action_id,
                event_type=audit_event_type or to_status,
                previous_state=row["status"],
                new_state=to_status,
                actor=actor,
                detail=(f"error_code={error_code}" if error_code else ""),
            )
            return self._feishu_message_action_from_row(current)

    def reconcile_feishu_message_action_unknown(
        self,
        action_id: int,
        *,
        app_id: str,
        outcome: str,
        verified_by: str,
        evidence_kind: str,
        remote_id: str = "",
        request_log_id: str = "",
    ) -> FeishuMessageAction:
        """Resolve one uncertain IM action and its recall receipt atomically."""
        from app.feishu.action_delivery import plan_message_action_reconciliation

        if not isinstance(app_id, str) or not app_id.strip():
            raise ValueError("Feishu action reconciliation requires app_id")
        if not all(
            isinstance(value, str)
            for value in (
                outcome,
                verified_by,
                evidence_kind,
                remote_id,
                request_log_id,
            )
        ):
            raise ValueError("Feishu action reconciliation inputs are invalid")
        normalized_app_id = app_id.strip()
        actor = self._normalize_feishu_action_review_actor(
            verified_by, operation="reconciliation"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu message action not found")
            if row["app_id"] != normalized_app_id:
                raise PermissionError("Feishu message action App ID does not match")
            if row["status"] != "result_unknown":
                raise ValueError(
                    "Feishu action reconciliation requires result_unknown"
                )
            if row["error_code"] not in FEISHU_ACTION_UNCERTAIN_ERROR_CODES:
                raise ValueError(
                    "Feishu action reconciliation uncertainty is invalid"
                )
            if (
                row["remote_id"]
                or row["lease_token"]
                or row["locked_at"]
                or row["available_at"]
            ):
                raise ValueError(
                    "Feishu action reconciliation mutable state is invalid"
                )
            action = self._validate_feishu_message_action_binding(
                db, row, require_active_target=False
            )
            decision = plan_message_action_reconciliation(
                action,
                outcome=outcome,
                verified_by=actor,
                evidence_kind=evidence_kind,
                remote_id=remote_id,
                request_log_id=request_log_id,
            )
            if decision.final_status == "sent":
                if decision.error_code or decision.audit_event_type != (
                    "unknown_verified_applied"
                ):
                    raise ValueError(
                        "Feishu action reconciliation decision is invalid"
                    )
                final_error = ""
            elif decision.final_status == "failed":
                if (
                    decision.error_code != "verified_not_applied"
                    or decision.remote_id
                    or decision.audit_event_type
                    != "unknown_verified_not_applied"
                ):
                    raise ValueError(
                        "Feishu action reconciliation decision is invalid"
                    )
                final_error = "manual_verification_confirmed_not_applied"
            else:
                raise ValueError("Feishu action reconciliation decision is invalid")

            existing_request_log_id = row["request_log_id"]
            if existing_request_log_id and (
                existing_request_log_id != existing_request_log_id.strip()
                or len(existing_request_log_id) > 256
                or any(
                    ord(character) < 32
                    for character in existing_request_log_id
                )
            ):
                raise ValueError(
                    "Feishu action reconciliation request log identity is invalid"
                )
            if (
                decision.request_log_id
                and existing_request_log_id
                and decision.request_log_id != existing_request_log_id
            ):
                raise ValueError(
                    "Feishu action reconciliation request log ID conflicts"
                )
            resulting_request_log_id = (
                existing_request_log_id or decision.request_log_id
            )

            receipt_row = None
            if action.kind == "recall_message":
                expected_receipt_status = (
                    "recalled"
                    if decision.final_status == "sent"
                    else "active"
                )
                if decision.recall_receipt_status != expected_receipt_status:
                    raise ValueError(
                        "Feishu recall reconciliation decision is invalid"
                    )
                receipt_row = db.execute(
                    """
                    select receipts.id, receipts.status,
                           receipts.recall_action_id
                    from feishu_delivery_receipts as receipts
                    join feishu_deliveries as deliveries
                      on deliveries.id=receipts.delivery_id
                     and deliveries.app_id=receipts.app_id
                    where receipts.app_id=? and receipts.message_id=?
                      and deliveries.app_id=? and deliveries.chat_id=?
                    """,
                    (
                        action.app_id,
                        action.target_message_id,
                        action.app_id,
                        action.chat_id,
                    ),
                ).fetchone()
                if (
                    receipt_row is None
                    or receipt_row["status"] != "recall_unknown"
                    or receipt_row["recall_action_id"] != action.id
                ):
                    raise ValueError(
                        "Feishu recall receipt does not match unknown action"
                    )
            elif decision.recall_receipt_status:
                raise ValueError("Feishu action reconciliation decision is invalid")

            cursor = db.execute(
                """
                update feishu_message_actions
                set status=?, remote_id=?, request_log_id=?,
                    lease_token='', locked_at='', available_at='',
                    error_code=?, error=?, updated_at=current_timestamp
                where id=? and app_id=? and status='result_unknown'
                  and error_code=? and remote_id='' and request_log_id=?
                  and lease_token=''
                """,
                (
                    decision.final_status,
                    decision.remote_id,
                    resulting_request_log_id,
                    decision.error_code,
                    final_error,
                    action_id,
                    normalized_app_id,
                    row["error_code"],
                    existing_request_log_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "Feishu action reconciliation lost atomic race"
                )

            if receipt_row is not None:
                resulting_recall_action_id = (
                    action.id
                    if decision.recall_receipt_status == "recalled"
                    else 0
                )
                receipt_cursor = db.execute(
                    """
                    update feishu_delivery_receipts
                    set status=?, recall_action_id=?,
                        updated_at=current_timestamp
                    where id=? and app_id=? and message_id=?
                      and status='recall_unknown' and recall_action_id=?
                      and exists (
                        select 1 from feishu_deliveries as deliveries
                        where deliveries.id=feishu_delivery_receipts.delivery_id
                          and deliveries.app_id=? and deliveries.chat_id=?
                      )
                    """,
                    (
                        decision.recall_receipt_status,
                        resulting_recall_action_id,
                        receipt_row["id"],
                        action.app_id,
                        action.target_message_id,
                        action.id,
                        action.app_id,
                        action.chat_id,
                    ),
                )
                if receipt_cursor.rowcount != 1:
                    raise ValueError(
                        "Feishu recall reconciliation lost atomic race"
                    )

            self._append_feishu_audit_event(
                db,
                app_id=normalized_app_id,
                entity_type="message_action",
                entity_id=action_id,
                event_type=decision.audit_event_type,
                previous_state="result_unknown",
                new_state=decision.final_status,
                actor=decision.verified_by,
                detail=f"evidence_kind={decision.evidence_kind}",
            )
            current = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            return self._feishu_message_action_from_row(current)

    def requeue_feishu_message_action_after_verification(
        self,
        action_id: int,
        *,
        app_id: str,
        verified_by: str,
        evidence_kind: str,
        available_at: str = "",
    ) -> FeishuMessageAction:
        """Create a fresh retry only after a definite not-applied decision."""
        if not isinstance(app_id, str) or not app_id.strip():
            raise ValueError("Feishu action requeue requires app_id")
        if not isinstance(evidence_kind, str):
            raise ValueError("unknown Feishu action reconciliation evidence kind")
        normalized_app_id = app_id.strip()
        normalized_evidence = evidence_kind.strip().lower()
        if normalized_evidence not in FEISHU_RECONCILIATION_EVIDENCE_KINDS:
            raise ValueError("unknown Feishu action reconciliation evidence kind")
        actor = self._normalize_feishu_action_review_actor(
            verified_by, operation="requeue"
        )
        normalized_available_at = self._normalize_feishu_action_requeue_time(
            available_at
        )

        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu message action not found")
            if row["app_id"] != normalized_app_id:
                raise PermissionError("Feishu message action App ID does not match")
            if (
                row["status"] != "failed"
                or row["error_code"] != "verified_not_applied"
            ):
                raise ValueError(
                    "Feishu message action is not verified for requeue"
                )
            if row["remote_id"] or row["lease_token"] or row["locked_at"]:
                raise ValueError("Feishu message action requeue state is invalid")
            action = self._validate_feishu_message_action_binding(
                db, row, require_active_target=True
            )
            next_review_generation = action.review_generation + 1
            next_approval_hash = action_approval_hash(
                reply_task_id=action.reply_task_id,
                attempt_id=action.attempt_id,
                app_id=action.app_id,
                chat_id=action.chat_id,
                action_key=action.action_key,
                kind=action.kind,
                target_id=(
                    action.target_message_id or action.target_open_id
                ),
                payload_sha256=action.payload_sha256,
                idempotency_key=action.idempotency_key,
                risk=action.risk,
                review_generation=next_review_generation,
            )
            cursor = db.execute(
                """
                update feishu_message_actions
                set status='retry', approved_at='', approved_by='',
                    review_generation=?, approval_hash=?,
                    lease_token='', locked_at='', available_at=?,
                    mutation_started_at='',
                    remote_failures=0, error_code='', error='',
                    updated_at=current_timestamp
                where id=? and app_id=? and status='failed'
                  and error_code='verified_not_applied' and remote_id=''
                  and lease_token='' and review_generation=?
                  and approval_hash=?
                """,
                (
                    next_review_generation,
                    next_approval_hash,
                    normalized_available_at,
                    action_id,
                    normalized_app_id,
                    action.review_generation,
                    action.approval_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu action requeue lost atomic race")
            self._append_feishu_audit_event(
                db,
                app_id=normalized_app_id,
                entity_type="message_action",
                entity_id=action_id,
                event_type="requeued_after_verification",
                previous_state="failed",
                new_state="retry",
                actor=actor,
                detail=f"evidence_kind={normalized_evidence}",
            )
            current = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            return self._feishu_message_action_from_row(current)

    def reject_feishu_message_action(
        self,
        action_id: int,
        *,
        app_id: str,
        rejected_by: str = "local-reviewer",
    ) -> FeishuMessageAction:
        """Close a queued local action even when its remote target is inactive."""
        normalized_app_id = app_id.strip()
        if not normalized_app_id:
            raise ValueError("Feishu message action rejection requires app_id")
        actor = self._normalize_feishu_action_review_actor(
            rejected_by, operation="rejection"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu message action not found")
            if row["app_id"] != normalized_app_id:
                raise PermissionError("Feishu message action App ID does not match")
            if row["status"] not in {"ready", "retry"}:
                raise ValueError("Feishu message action is not rejectable")
            self._validate_feishu_message_action_binding(
                db, row, require_active_target=False
            )
            cursor = db.execute(
                """
                update feishu_message_actions
                set status='rejected', lease_token='', locked_at='',
                    available_at='', error_code='rejected',
                    error='user_rejected', updated_at=current_timestamp
                where id=? and app_id=? and status in ('ready', 'retry')
                """,
                (action_id, normalized_app_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu message action rejection lost atomic race")
            current = db.execute(
                "select * from feishu_message_actions where id=?", (action_id,)
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=normalized_app_id,
                entity_type="message_action",
                entity_id=action_id,
                event_type="rejected",
                previous_state=row["status"],
                new_state="rejected",
                actor=actor,
                detail="error_code=rejected",
            )
            return self._feishu_message_action_from_row(current)

    def list_stale_feishu_message_actions(
        self,
        max_age_seconds: int,
        *,
        app_id: str = "",
        now: datetime | None = None,
    ) -> list[FeishuMessageAction]:
        if max_age_seconds <= 0:
            return []
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.astimezone()
        cutoff = (current - timedelta(seconds=max_age_seconds)).isoformat()
        app_clause = "and app_id=?" if app_id else ""
        args: list[str] = [cutoff]
        if app_id:
            args.append(app_id)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_message_actions
                where status='sending'
                  and (locked_at='' or datetime(locked_at) <= datetime(?))
                  {app_clause}
                order by id
                """,
                args,
            ).fetchall()
        return [self._feishu_message_action_from_row(row) for row in rows]

    @classmethod
    def _feishu_local_notification_is_current_db(
        cls,
        db: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        task = db.execute(
            "select * from reply_tasks where id=?",
            (row["reply_task_id"],),
        ).fetchone()
        if (
            task is None
            or task["channel"] != "feishu"
            or task["status"] != "done"
            or task["execution_generation"] != row["execution_generation"]
            or "superseded" in str(task["error"] or "")
            or cls._feishu_task_app_id(task) != row["app_id"]
        ):
            return False
        event = db.execute(
            """
            select * from feishu_events
            where reply_task_id=? and app_id=?
            """,
            (row["reply_task_id"], row["app_id"]),
        ).fetchone()
        if event is None:
            return False
        reference_root = cls._feishu_reference_root(event)
        newer = db.execute(
            """
            select 1 from feishu_events
            where app_id=? and chat_id=? and eligibility_status='eligible'
              and coalesce(nullif(thread_id, ''), nullif(root_message_id, ''),
                           message_id)=?
              and (
                    event_create_time_ms>?
                    or (event_create_time_ms=? and id>?)
                  )
            limit 1
            """,
            (
                event["app_id"],
                event["chat_id"],
                reference_root,
                event["event_create_time_ms"],
                event["event_create_time_ms"],
                event["id"],
            ),
        ).fetchone()
        return newer is None

    @classmethod
    def _feishu_remote_handoff_dependency_db(
        cls,
        db: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> str:
        actions = db.execute(
            """
            select status, error_code, error from feishu_message_actions
            where reply_task_id=? and attempt_id=? and app_id=?
              and kind='handoff_notify'
            order by id
            """,
            (row["reply_task_id"], row["attempt_id"], row["app_id"]),
        ).fetchall()
        if not actions:
            return "invalid"
        if any(action["status"] == "sent" for action in actions):
            return "remote_sent"
        if any(
            action["status"] not in {"failed", "rejected"}
            for action in actions
        ):
            return "waiting"
        if any(
            action["error_code"] == "superseded"
            or "superseded" in str(action["error"] or "")
            for action in actions
        ):
            return "superseded"
        return "all_failed"

    @classmethod
    def _set_feishu_local_notification_state_db(
        cls,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        status: str,
        event_type: str,
        error_code: str = "",
        error: str = "",
        actor: str = "local-notification-worker",
    ) -> bool:
        updated = db.execute(
            """
            update feishu_local_notifications
            set status=?, lease_token='', locked_at='', available_at='',
                error_code=?, error=?, updated_at=current_timestamp
            where id=? and status=?
            """,
            (
                status,
                error_code,
                safe_observability_error(error)[:512],
                row["id"],
                row["status"],
            ),
        )
        if updated.rowcount != 1:
            return False
        cls._append_feishu_audit_event(
            db,
            app_id=str(row["app_id"]),
            entity_type="local_notification",
            entity_id=int(row["id"]),
            event_type=event_type,
            previous_state=str(row["status"]),
            new_state=status,
            actor=actor,
            detail=(f"error_code={error_code}" if error_code else ""),
        )
        return True

    def list_feishu_local_notifications(
        self,
        *,
        app_id: str = "",
        statuses: tuple[str, ...] | list[str] | set[str] = (),
        limit: int = 100,
    ) -> list[FeishuLocalNotification]:
        if limit <= 0:
            return []
        selected = tuple(statuses)
        if set(selected) - FEISHU_LOCAL_NOTIFICATION_STATUSES:
            raise ValueError("unknown Feishu local notification status")
        where: list[str] = []
        args: list[str | int] = []
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        if selected:
            placeholders = ",".join("?" for _ in selected)
            where.append(f"status in ({placeholders})")
            args.extend(selected)
        clause = f"where {' and '.join(where)}" if where else ""
        args.append(min(limit, 1000))
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_local_notifications {clause}
                order by id limit ?
                """,
                args,
            ).fetchall()
        return [self._feishu_local_notification_from_row(row) for row in rows]

    def claim_feishu_local_notifications(
        self,
        limit: int,
        *,
        app_id: str,
        now: str = "",
    ) -> list[FeishuLocalNotification]:
        if limit <= 0:
            return []
        normalized_app_id = str(app_id or "").strip()
        if not normalized_app_id:
            raise ValueError("Feishu local notification claim requires app_id")
        with self._connect() as db:
            db.execute("begin immediate")
            waiting = db.execute(
                """
                select * from feishu_local_notifications
                where app_id=? and status='waiting_remote'
                order by id limit 100
                """,
                (normalized_app_id,),
            ).fetchall()
            for row in waiting:
                if not self._feishu_local_notification_is_current_db(db, row):
                    self._set_feishu_local_notification_state_db(
                        db,
                        row,
                        status="cancelled",
                        event_type="stale_cancelled",
                        error_code="superseded",
                        error="local_notification_task_is_stale",
                    )
                    continue
                dependency = self._feishu_remote_handoff_dependency_db(db, row)
                if dependency == "all_failed":
                    self._set_feishu_local_notification_state_db(
                        db,
                        row,
                        status="pending",
                        event_type="remote_handoff_failed",
                    )
                elif dependency in {"remote_sent", "superseded", "invalid"}:
                    self._set_feishu_local_notification_state_db(
                        db,
                        row,
                        status="cancelled",
                        event_type=(
                            "remote_handoff_sent"
                            if dependency == "remote_sent"
                            else "dependency_cancelled"
                        ),
                        error_code=(
                            "remote_sent"
                            if dependency == "remote_sent"
                            else dependency
                        ),
                        error=f"local_notification_dependency_{dependency}",
                    )

            due_expression = "current_timestamp" if not now else "?"
            args: list[str | int] = [normalized_app_id]
            if now:
                args.append(now)
            args.append(min(limit * 4, 100))
            candidates = db.execute(
                f"""
                select * from feishu_local_notifications
                where app_id=? and status in ('pending', 'retry')
                  and (
                    available_at=''
                    or datetime(available_at)<=datetime({due_expression})
                  )
                order by id limit ?
                """,
                args,
            ).fetchall()
            claimed: list[FeishuLocalNotification] = []
            for row in candidates:
                if len(claimed) >= min(limit, 20):
                    break
                if not self._feishu_local_notification_is_current_db(db, row):
                    self._set_feishu_local_notification_state_db(
                        db,
                        row,
                        status="cancelled",
                        event_type="stale_cancelled",
                        error_code="superseded",
                        error="local_notification_task_is_stale",
                    )
                    continue
                if row["dependency_mode"] == "remote_failure" and (
                    self._feishu_remote_handoff_dependency_db(db, row)
                    != "all_failed"
                ):
                    self._set_feishu_local_notification_state_db(
                        db,
                        row,
                        status="cancelled",
                        event_type="dependency_changed",
                        error_code="dependency_changed",
                        error="local_notification_dependency_changed",
                    )
                    continue
                lease_token = uuid4().hex
                locked_at = now or datetime.now().astimezone().isoformat()
                updated = db.execute(
                    """
                    update feishu_local_notifications
                    set status='sending', attempts=attempts + 1,
                        lease_token=?, locked_at=?, available_at='',
                        mutation_started_at='',
                        error_code='', error='', updated_at=current_timestamp
                    where id=? and status=?
                    """,
                    (lease_token, locked_at, row["id"], row["status"]),
                )
                if updated.rowcount != 1:
                    raise ValueError(
                        "Feishu local notification claim lost atomic race"
                    )
                current = db.execute(
                    "select * from feishu_local_notifications where id=?",
                    (row["id"],),
                ).fetchone()
                self._append_feishu_audit_event(
                    db,
                    app_id=normalized_app_id,
                    entity_type="local_notification",
                    entity_id=int(row["id"]),
                    event_type="claimed",
                    previous_state=str(row["status"]),
                    new_state="sending",
                    actor="local-notification-worker",
                )
                claimed.append(self._feishu_local_notification_from_row(current))
            return claimed

    def begin_feishu_local_notification_mutation(
        self,
        notification_id: int,
        *,
        app_id: str,
        lease_token: str,
        now: str = "",
    ) -> FeishuLocalNotification | None:
        """Revalidate then durably fence one imminent local OS mutation."""
        if not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu local notification send lease is required")
        started_at = now or datetime.now().astimezone().isoformat()
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_local_notifications where id=?",
                (notification_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Feishu local notification not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu local notification App ID mismatch")
            if row["status"] != "sending" or row["lease_token"] != lease_token:
                raise ValueError("Feishu local notification lease is no longer active")
            if row["mutation_started_at"]:
                raise ValueError("Feishu local notification mutation already started")
            current = self._feishu_local_notification_is_current_db(db, row)
            dependency_current = not (
                row["dependency_mode"] == "remote_failure"
                and self._feishu_remote_handoff_dependency_db(db, row)
                != "all_failed"
            )
            if not current or not dependency_current:
                self._set_feishu_local_notification_state_db(
                    db,
                    row,
                    status="cancelled",
                    event_type="pre_mutation_cancelled",
                    error_code=("superseded" if not current else "dependency_changed"),
                    error=(
                        "local_notification_task_is_stale"
                        if not current
                        else "local_notification_dependency_changed"
                    ),
                )
                return None
            updated = db.execute(
                """
                update feishu_local_notifications
                set mutation_started_at=?, updated_at=current_timestamp
                where id=? and app_id=? and status='sending'
                  and lease_token=? and mutation_started_at=''
                """,
                (started_at, notification_id, app_id, lease_token),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "Feishu local notification mutation fence lost atomic race"
                )
            current_row = db.execute(
                "select * from feishu_local_notifications where id=?",
                (notification_id,),
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="local_notification",
                entity_id=notification_id,
                event_type="mutation_started",
                previous_state="sending",
                new_state="sending",
                actor="local-notification-worker",
            )
            return self._feishu_local_notification_from_row(current_row)

    def validate_feishu_local_notification_for_send(
        self,
        notification_id: int,
        *,
        app_id: str,
        lease_token: str,
    ) -> FeishuLocalNotification:
        if not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu local notification send lease is required")
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_local_notifications where id=?",
                (notification_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Feishu local notification not found")
            if row["app_id"] != app_id:
                raise PermissionError("Feishu local notification App ID mismatch")
            if row["status"] != "sending" or row["lease_token"] != lease_token:
                raise ValueError("Feishu local notification lease is no longer active")
            if not row["mutation_started_at"]:
                raise ValueError("Feishu local notification mutation has not started")
            return self._feishu_local_notification_from_row(row)

    def transition_feishu_local_notification(
        self,
        notification_id: int,
        *,
        app_id: str,
        lease_token: str,
        to_status: str,
        error_code: str = "",
        error: str = "",
        available_at: str = "",
        audit_event_type: str = "",
    ) -> FeishuLocalNotification:
        if to_status not in {
            "sent",
            "retry",
            "result_unknown",
            "failed",
        }:
            raise ValueError("invalid Feishu local notification transition")
        if not app_id.strip() or not lease_token.strip():
            raise ValueError("Feishu local notification transition lease is required")
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from feishu_local_notifications where id=?",
                (notification_id,),
            ).fetchone()
            if row is None or row["app_id"] != app_id:
                raise ValueError("Feishu local notification not found")
            if row["status"] != "sending" or row["lease_token"] != lease_token:
                raise ValueError("Feishu local notification lease is no longer active")
            if not row["mutation_started_at"]:
                raise ValueError("Feishu local notification mutation has not started")
            if to_status == "retry" and error_code != "local_notification_not_started":
                raise ValueError("local notification retry requires proven non-start")
            if to_status == "failed" and error_code != "local_notification_not_started":
                raise ValueError("local notification failure requires proven non-start")
            if to_status == "result_unknown" and error_code not in {
                "send_timeout",
                "unknown",
            }:
                raise ValueError("local notification uncertainty code is invalid")
            safe_error = safe_observability_error(error)[:512]
            updated = db.execute(
                """
                update feishu_local_notifications
                set status=?, lease_token='', locked_at='', available_at=?,
                    mutation_started_at=case
                      when ?='retry' then '' else mutation_started_at end,
                    error_code=?, error=?,
                    sent_at=case when ?='sent' then current_timestamp else sent_at end,
                    updated_at=current_timestamp
                where id=? and app_id=? and status='sending' and lease_token=?
                """,
                (
                    to_status,
                    available_at,
                    to_status,
                    error_code,
                    safe_error,
                    to_status,
                    notification_id,
                    app_id,
                    lease_token,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "Feishu local notification transition lost atomic race"
                )
            current = db.execute(
                "select * from feishu_local_notifications where id=?",
                (notification_id,),
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="local_notification",
                entity_id=notification_id,
                event_type=audit_event_type or to_status,
                previous_state="sending",
                new_state=to_status,
                actor="local-notification-worker",
                detail=(f"error_code={error_code}" if error_code else ""),
            )
            return self._feishu_local_notification_from_row(current)

    def recover_stale_feishu_local_notifications(
        self,
        *,
        app_id: str,
        stale_after_seconds: int,
        now: datetime | None = None,
    ) -> int:
        if not app_id.strip() or stale_after_seconds <= 0:
            raise ValueError("invalid Feishu local notification recovery scope")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.astimezone()
        cutoff = (current - timedelta(seconds=stale_after_seconds)).isoformat()
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select * from feishu_local_notifications
                where app_id=? and status='sending'
                  and (locked_at='' or datetime(locked_at)<=datetime(?))
                order by id limit 100
                """,
                (app_id, cutoff),
            ).fetchall()
            recovered = 0
            for row in rows:
                mutation_started = bool(row["mutation_started_at"])
                if self._set_feishu_local_notification_state_db(
                    db,
                    row,
                    status=("result_unknown" if mutation_started else "retry"),
                    event_type=(
                        "stale_mutation_result_unknown"
                        if mutation_started
                        else "stale_claim_recovered"
                    ),
                    error_code=(
                        "unknown" if mutation_started else "stale_claim_recovered"
                    ),
                    error=(
                        "local_notification_result_unknown"
                        if mutation_started
                        else "local_notification_stale_claim_recovered"
                    ),
                    actor="local-notification-recovery",
                ):
                    recovered += 1
            return recovered

    def get_feishu_delivery_for_task(
        self, reply_task_id: int
    ) -> FeishuDelivery | None:
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_deliveries where reply_task_id=?",
                (reply_task_id,),
            ).fetchone()
        return self._feishu_delivery_from_row(row) if row is not None else None

    def validate_feishu_delivery_for_send(
        self, delivery_id: int, *, app_id: str, lease_token: str = ""
    ) -> FeishuDelivery:
        """Revalidate identity, plus the active owner lease before a send."""
        if not app_id.strip():
            raise ValueError("Feishu send validation requires app_id")
        with self._connect() as db:
            row = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feishu delivery not found")
            if row["app_id"] != app_id:
                raise PermissionError(
                    "Feishu delivery App ID does not match runtime"
                )
            if lease_token and (
                row["status"] != "sending" or row["lease_token"] != lease_token
            ):
                raise ValueError("Feishu delivery send lease is no longer active")
            self._validate_feishu_delivery_binding(
                db, row, require_target_identity=True
            )
            return self._feishu_delivery_from_row(row)

    def approve_feishu_delivery(
        self,
        delivery_id: int,
        *,
        app_id: str,
        approved_by: str,
        expected_approval_hash: str,
        now: str = "",
    ) -> FeishuDelivery:
        """Durably approve one delivery without opening a network client.

        The application identity is part of the compare-and-set boundary.  A
        reviewer configured for one Feishu application cannot approve a row
        belonging to another application in the same SQLite database.
        """
        if not app_id.strip():
            raise ValueError("Feishu delivery approval requires app_id")
        if not approved_by.strip():
            raise ValueError("Feishu delivery approval requires approved_by")
        if (
            len(expected_approval_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_approval_hash
            )
        ):
            raise ValueError("Feishu delivery approval hash is invalid")
        approved_at = now or datetime.now().astimezone().isoformat()
        with self._connect() as db:
            db.execute("begin immediate")
            current = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            if current is None:
                raise ValueError("Feishu delivery not found")
            if current["app_id"] != app_id:
                raise PermissionError("Feishu delivery App ID does not match runtime")
            self._validate_feishu_delivery_binding(
                db, current, require_target_identity=True
            )
            if current["approval_hash"] != expected_approval_hash:
                raise ValueError("Feishu delivery approval hash changed")
            if current["status"] not in {"ready_to_send", "retry"}:
                raise ValueError("Feishu delivery is not approvable")
            if current["approved_at"]:
                raise ValueError("Feishu delivery is already approved")
            cursor = db.execute(
                """
                update feishu_deliveries
                set approved_at=?, approved_by=?, updated_at=current_timestamp
                where id=? and app_id=? and status in ('ready_to_send', 'retry')
                  and approved_at='' and approval_hash=?
                """,
                (
                    approved_at,
                    approved_by.strip(),
                    delivery_id,
                    app_id,
                    expected_approval_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu delivery approval lost atomic race")
            row = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            self._append_feishu_audit_event(
                db,
                app_id=app_id,
                entity_type="delivery",
                entity_id=delivery_id,
                event_type="approved",
                previous_state=current["status"],
                new_state=row["status"],
                actor=approved_by.strip(),
            )
            return self._feishu_delivery_from_row(row)

    def list_feishu_deliveries(
        self,
        status: str = "",
        *,
        statuses: tuple[str, ...] | list[str] | set[str] | None = None,
        app_id: str = "",
        limit: int | None = None,
    ) -> list[FeishuDelivery]:
        if limit is not None and limit <= 0:
            return []
        selected = tuple(statuses or ())
        if status:
            if selected:
                raise ValueError("use either status or statuses")
            selected = (status,)
        unknown = set(selected) - FEISHU_DELIVERY_STATUSES
        if unknown:
            raise ValueError(f"unknown Feishu delivery statuses: {sorted(unknown)}")
        where: list[str] = []
        args: list[str | int] = []
        if selected:
            placeholders = ",".join("?" for _ in selected)
            where.append(f"status in ({placeholders})")
            args.extend(selected)
        if app_id:
            where.append("app_id=?")
            args.append(app_id)
        clause = f"where {' and '.join(where)}" if where else ""
        query = f"select * from feishu_deliveries {clause} order by id"
        if limit is not None:
            query += " limit ?"
            args.append(limit)
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        return [self._feishu_delivery_from_row(row) for row in rows]

    def list_feishu_deliveries_by_status(
        self, status: str
    ) -> list[FeishuDelivery]:
        return self.list_feishu_deliveries(status)

    def list_stale_feishu_sending(
        self,
        max_age_seconds: int,
        *,
        app_id: str = "",
        now: datetime | None = None,
    ) -> list[FeishuDelivery]:
        if max_age_seconds <= 0:
            return []
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.astimezone()
        cutoff = (current - timedelta(seconds=max_age_seconds)).isoformat()
        app_clause = " and app_id=?" if app_id else ""
        args: list[str] = [cutoff]
        if app_id:
            args.append(app_id)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from feishu_deliveries
                where status='sending'
                  and (locked_at='' or datetime(locked_at) <= datetime(?))
                  {app_clause}
                order by id
                """,
                args,
            ).fetchall()
        return [self._feishu_delivery_from_row(row) for row in rows]

    @staticmethod
    def _validate_feishu_claim_statuses(statuses: tuple[str, ...]) -> None:
        if not statuses:
            raise ValueError("Feishu delivery claim statuses must be non-empty")
        invalid = set(statuses) - {"ready_to_send", "retry"}
        if invalid:
            raise ValueError(
                f"cannot claim Feishu delivery statuses: {sorted(invalid)}"
            )

    def claim_feishu_delivery(
        self,
        delivery_id: int,
        *,
        statuses: tuple[str, ...] = ("ready_to_send", "retry"),
        now: str = "",
        app_id: str = "",
        approved_only: bool = False,
    ) -> FeishuDelivery | None:
        selected = tuple(statuses)
        self._validate_feishu_claim_statuses(selected)
        placeholders = ",".join("?" for _ in selected)
        now_expression = "current_timestamp" if not now else "?"
        app_clause = " and app_id=?" if app_id else ""
        approval_clause = " and approved_at<>''" if approved_only else ""
        args: list[str | int] = [delivery_id, *selected]
        if app_id:
            args.append(app_id)
        if now:
            args.append(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"""
                select * from feishu_deliveries
                where id=? and status in ({placeholders})
                  {app_clause}
                  {approval_clause}
                  and (
                    available_at=''
                    or datetime(available_at) <= datetime({now_expression})
                  )
                """,
                args,
            ).fetchone()
            if row is None:
                return None
            try:
                self._validate_feishu_delivery_binding(
                    db, row, require_target_identity=True
                )
            except ValueError:
                self._quarantine_feishu_delivery_binding(
                    db, row, actor="sender-claim"
                )
                return None
            conversation_busy = db.execute(
                """
                select 1 from feishu_deliveries
                where app_id=? and chat_id=? and id<>?
                  and (
                    status='sending'
                    or (
                      id < ?
                      and status in ('ready_to_send', 'retry', 'send_unknown')
                    )
                  )
                limit 1
                """,
                (row["app_id"], row["chat_id"], delivery_id, delivery_id),
            ).fetchone()
            if conversation_busy is not None:
                return None
            action_busy = db.execute(
                """
                select 1 from feishu_message_actions
                where app_id=? and chat_id=?
                  and status in ('sending', 'result_unknown')
                limit 1
                """,
                (row["app_id"], row["chat_id"]),
            ).fetchone()
            if action_busy is not None:
                return None
            locked_at = now or datetime.now().astimezone().isoformat()
            lease_token = uuid4().hex
            cursor = db.execute(
                f"""
                update feishu_deliveries
                set status='sending', attempts=attempts + 1,
                    lease_token=?, locked_at=?, available_at='',
                    error_code='', error='',
                    updated_at=current_timestamp
                where id=? and status in ({placeholders})
                """,
                (lease_token, locked_at, delivery_id, *selected),
            )
            if cursor.rowcount != 1:
                return None
            claimed = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            self._sync_feishu_attempt_from_delivery(db, claimed)
            self._append_feishu_audit_event(
                db,
                app_id=claimed["app_id"],
                entity_type="delivery",
                entity_id=delivery_id,
                event_type="claimed",
                previous_state=row["status"],
                new_state="sending",
                actor="sender",
            )
            return self._feishu_delivery_from_row(claimed)

    def claim_feishu_deliveries(
        self,
        limit: int,
        *,
        statuses: tuple[str, ...] = ("ready_to_send", "retry"),
        now: str = "",
        app_id: str = "",
        approved_only: bool = False,
    ) -> list[FeishuDelivery]:
        if limit <= 0:
            return []
        selected = tuple(statuses)
        self._validate_feishu_claim_statuses(selected)
        placeholders = ",".join("?" for _ in selected)
        now_expression = "current_timestamp" if not now else "?"
        app_clause = " and candidate.app_id=?" if app_id else ""
        approval_clause = (
            " and candidate.approved_at<>''" if approved_only else ""
        )
        args: list[str | int] = list(selected)
        if app_id:
            args.append(app_id)
        if now:
            args.append(now)
        args.append(limit)
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select candidate.* from feishu_deliveries as candidate
                where candidate.status in ({placeholders})
                  {app_clause}
                  {approval_clause}
                  and (
                    candidate.available_at=''
                    or datetime(candidate.available_at) <= datetime({now_expression})
                  )
                  and not exists (
                    select 1 from feishu_deliveries as active
                    where active.app_id=candidate.app_id
                      and active.chat_id=candidate.chat_id
                      and active.status='sending'
                  )
                  and not exists (
                    select 1 from feishu_message_actions as action_blocker
                    where action_blocker.app_id=candidate.app_id
                      and action_blocker.chat_id=candidate.chat_id
                      and action_blocker.status in ('sending', 'result_unknown')
                  )
                  and candidate.id=(
                    select min(head.id) from feishu_deliveries as head
                    where head.app_id=candidate.app_id
                      and head.chat_id=candidate.chat_id
                      and head.status in (
                        'ready_to_send', 'retry', 'send_unknown'
                      )
                  )
                order by candidate.id
                limit ?
                """,
                args,
            ).fetchall()
            valid_rows = []
            for row in rows:
                try:
                    self._validate_feishu_delivery_binding(
                        db, row, require_target_identity=True
                    )
                except ValueError:
                    self._quarantine_feishu_delivery_binding(
                        db, row, actor="sender-claim"
                    )
                else:
                    valid_rows.append(row)
            rows = valid_rows
            ids = [int(row["id"]) for row in rows]
            if not ids:
                return []
            id_placeholders = ",".join("?" for _ in ids)
            locked_at = now or datetime.now().astimezone().isoformat()
            lease_token = uuid4().hex
            db.execute(
                f"""
                update feishu_deliveries
                set status='sending', attempts=attempts + 1,
                    lease_token=?, locked_at=?, available_at='',
                    error_code='', error='',
                    updated_at=current_timestamp
                where id in ({id_placeholders})
                  and status in ({placeholders})
                """,
                (lease_token, locked_at, *ids, *selected),
            )
            claimed = db.execute(
                f"""
                select * from feishu_deliveries
                where id in ({id_placeholders}) order by id
                """,
                ids,
            ).fetchall()
            previous_statuses = {int(row["id"]): row["status"] for row in rows}
            for row in claimed:
                self._sync_feishu_attempt_from_delivery(db, row)
                self._append_feishu_audit_event(
                    db,
                    app_id=row["app_id"],
                    entity_type="delivery",
                    entity_id=row["id"],
                    event_type="claimed",
                    previous_state=previous_statuses[int(row["id"])],
                    new_state="sending",
                    actor="sender",
                )
            return [self._feishu_delivery_from_row(row) for row in claimed]

    def transition_feishu_delivery(
        self,
        delivery_id: int,
        *,
        from_statuses: tuple[str, ...] | list[str] | set[str],
        to_status: str,
        feishu_message_id: str = "",
        message_ids: tuple[str, ...] | list[str] = (),
        request_log_id: str = "",
        error_code: str = "",
        error: str = "",
        available_at: str = "",
        locked_at: str = "",
        remote_failures: int | None = None,
        app_id: str = "",
        actor: str = "sender",
        audit_event_type: str = "",
        clear_approval: bool = False,
        rotate_review_generation: bool = False,
        audit_detail: str = "",
        required_error_code: str = "",
        expected_lease_token: str = "",
        fill_request_log_id_only: bool = False,
        verify_receipt_prefix_extension: bool = False,
    ) -> FeishuDelivery:
        """Compare-and-swap a delivery while preserving its idempotency key."""
        sources = tuple(from_statuses)
        if not sources:
            raise ValueError("from_statuses must be non-empty")
        unknown = (set(sources) | {to_status}) - FEISHU_DELIVERY_STATUSES
        if unknown:
            raise ValueError(f"unknown Feishu delivery statuses: {sorted(unknown)}")
        invalid = [
            (source, to_status)
            for source in sources
            if (source, to_status) not in FEISHU_DELIVERY_TRANSITIONS
        ]
        if invalid:
            raise ValueError(f"invalid Feishu delivery transition: {invalid}")
        if "send_unknown" in sources:
            expected_event = {
                "sent": "unknown_verified_sent",
                "retry": "unknown_verified_partial",
                "failed": "unknown_verified_not_sent",
            }.get(to_status)
            if not expected_event or audit_event_type != expected_event:
                raise ValueError(
                    "send_unknown requires an exact verified reconciliation"
                )
        if "failed" in sources and to_status == "retry" and not (
            audit_event_type == "requeued_after_verification"
            and required_error_code == "verified_not_sent"
            and clear_approval
            and rotate_review_generation
        ):
            raise ValueError("failed delivery requires verified requeue workflow")
        if rotate_review_generation and not (
            "failed" in sources
            and to_status == "retry"
            and audit_event_type == "requeued_after_verification"
            and required_error_code == "verified_not_sent"
            and clear_approval
        ):
            raise ValueError(
                "Feishu delivery review generation rotation is not allowed"
            )
        if verify_receipt_prefix_extension and not (
            "send_unknown" in sources
            and to_status in {"sent", "retry"}
            and audit_event_type
            in {"unknown_verified_sent", "unknown_verified_partial"}
        ):
            raise ValueError(
                "Feishu delivery receipt-prefix verification is not allowed"
            )
        normalized_message_ids = self._normalize_feishu_delivery_message_ids(
            message_ids
        )
        normalized_primary = feishu_message_id.strip()
        if feishu_message_id and normalized_primary != feishu_message_id:
            raise ValueError("Feishu delivery message ID is invalid")
        if normalized_primary and (
            len(normalized_primary) > 512
            or any(ord(character) < 32 for character in normalized_primary)
        ):
            raise ValueError("Feishu delivery message ID is invalid")
        if normalized_message_ids:
            if normalized_primary and normalized_primary != normalized_message_ids[0]:
                raise ValueError(
                    "Feishu delivery primary message ID does not match receipts"
                )
            normalized_primary = normalized_message_ids[0]
        normalized_request_log_id = request_log_id.strip()
        if request_log_id and (
            request_log_id != normalized_request_log_id
            or len(normalized_request_log_id) > 256
            or any(
                ord(character) < 32
                for character in normalized_request_log_id
            )
        ):
            raise ValueError("Feishu delivery request log ID is invalid")
        if remote_failures is not None and (
            isinstance(remote_failures, bool) or remote_failures < 0
        ):
            raise ValueError("Feishu delivery remote failures is invalid")
        placeholders = ",".join("?" for _ in sources)
        with self._connect() as db:
            db.execute("begin immediate")
            current = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            if current is None:
                raise ValueError("Feishu delivery not found")
            if app_id and current["app_id"] != app_id:
                raise PermissionError("Feishu delivery App ID does not match runtime")
            if required_error_code and current["error_code"] != required_error_code:
                raise ValueError("Feishu delivery verification state does not match")
            if expected_lease_token and current["lease_token"] != expected_lease_token:
                raise ValueError("Feishu delivery send lease is no longer active")
            if current["status"] not in sources:
                raise ValueError(
                    "Feishu delivery status changed: "
                    f"expected {sources}, found {current['status']}"
                )
            if rotate_review_generation:
                recall_blocker = db.execute(
                    """
                    select 1
                    from feishu_delivery_receipts as receipts
                    join feishu_message_actions as actions
                      on actions.app_id=receipts.app_id
                     and actions.target_message_id=receipts.message_id
                    where receipts.delivery_id=?
                      and actions.kind='recall_message'
                      and actions.status in (
                        'ready', 'retry', 'sending', 'result_unknown'
                      )
                    limit 1
                    """,
                    (delivery_id,),
                ).fetchone()
                if recall_blocker is not None:
                    raise ValueError(
                        "Feishu delivery has an open recall action"
                    )
            legacy_plan_unverifiable = (
                current["error_code"] == "legacy_chunk_plan_unverifiable"
            )
            if (
                current["status"] == "send_unknown"
                and legacy_plan_unverifiable
                and not (
                    to_status == "failed"
                    and error_code == "verified_unresumable_not_sent"
                    and audit_event_type == "unknown_verified_not_sent"
                )
            ):
                raise ValueError(
                    "legacy Feishu chunk plan cannot be resumed or requeued"
                )
            if current["status"] == "sending":
                if to_status == "retry" and error_code not in (
                    FEISHU_RETRYABLE_ERROR_CODES
                ):
                    raise ValueError(
                        "Feishu send retry requires a confirmed retryable error"
                    )
                if to_status == "failed" and error_code not in (
                    FEISHU_CONFIRMED_NOT_SENT_ERROR_CODES
                ):
                    raise ValueError(
                        "uncertain Feishu send must enter send_unknown"
                    )
                if to_status == "send_unknown" and error_code not in (
                    FEISHU_UNCERTAIN_SEND_ERROR_CODES
                ):
                    raise ValueError(
                        "Feishu send_unknown requires an uncertain send error"
                    )
            resulting_message_id = normalized_primary or current["feishu_message_id"]
            if to_status == "sent" and not resulting_message_id:
                raise ValueError("sent Feishu delivery requires feishu_message_id")
            receipt_message_ids = normalized_message_ids
            if to_status == "sent" and not receipt_message_ids:
                receipt_message_ids = (resulting_message_id,)
            expected_chunks = int(current["expected_chunks"] or 0)
            if verify_receipt_prefix_extension:
                if legacy_plan_unverifiable:
                    raise ValueError(
                        "legacy Feishu chunk plan cannot be reconciled as sent"
                    )
                if current["error"] == "sdk_returned_unplanned_wire_chunks":
                    raise ValueError(
                        "unplanned Feishu wire chunks cannot be reconciled as "
                        "the planned delivery"
                    )
                durable_prefix = tuple(
                    str(receipt["message_id"])
                    for receipt in db.execute(
                        """
                        select message_id
                        from feishu_delivery_receipts
                        where delivery_id=? order by ordinal
                        """,
                        (delivery_id,),
                    ).fetchall()
                )
                if (
                    len(receipt_message_ids) != len(durable_prefix) + 1
                    or receipt_message_ids[:-1] != durable_prefix
                ):
                    raise ValueError(
                        "verified Feishu delivery must extend the durable "
                        "receipt prefix by exactly one message ID"
                    )
                if (
                    to_status == "retry"
                    and not _feishu_unknown_allows_one_chunk_verification(
                        current
                    )
                ):
                    raise ValueError(
                        "Feishu delivery uncertainty is not safely resumable"
                    )
            if to_status == "sent" and len(receipt_message_ids) != expected_chunks:
                raise ValueError(
                    "sent Feishu delivery requires the complete ordered chunk set"
                )
            if len(receipt_message_ids) > expected_chunks:
                raise ValueError("Feishu delivery receipts exceed the chunk plan")
            existing_log_id = str(current["request_log_id"] or "")
            if fill_request_log_id_only:
                if (
                    existing_log_id
                    and normalized_request_log_id
                    and existing_log_id != normalized_request_log_id
                ):
                    raise ValueError(
                        "Feishu delivery reconciliation request log ID conflicts"
                    )
                resulting_log_id = existing_log_id or normalized_request_log_id
            else:
                resulting_log_id = normalized_request_log_id or existing_log_id
            has_durable_receipt = db.execute(
                """
                select 1 from feishu_delivery_receipts
                where delivery_id=? limit 1
                """,
                (delivery_id,),
            ).fetchone() is not None
            next_mutation_started_at = str(
                current["mutation_started_at"] or ""
            )
            if (
                to_status == "retry"
                and not has_durable_receipt
                and not receipt_message_ids
            ):
                # Every retry without a receipt is backed by confirmed
                # non-delivery, so a future lease must acquire a fresh fence.
                next_mutation_started_at = ""
            elif (
                not next_mutation_started_at
                and (
                    has_durable_receipt
                    or receipt_message_ids
                    or to_status in {"sent", "send_unknown"}
                )
            ):
                next_mutation_started_at = (
                    datetime.now().astimezone().isoformat()
                )
            next_locked_at = ""
            next_lease_token = ""
            increment_attempt = 0
            next_remote_failures = (
                int(current["remote_failures"])
                if remote_failures is None
                else int(remote_failures)
            )
            next_review_generation = int(current["review_generation"])
            next_approval_hash = str(current["approval_hash"])
            if rotate_review_generation:
                next_review_generation += 1
                (
                    frozen_expected_chunks,
                    frozen_plan_hash,
                    next_approval_hash,
                ) = self._feishu_delivery_approval_values(
                    current, review_generation=next_review_generation
                )
                if (
                    frozen_expected_chunks != int(current["expected_chunks"])
                    or frozen_plan_hash != current["chunk_plan_sha256"]
                ):
                    raise ValueError(
                        "Feishu delivery chunk plan changed before requeue"
                    )
            if to_status == "sending":
                next_locked_at = (
                    locked_at or datetime.now().astimezone().isoformat()
                )
                next_lease_token = uuid4().hex
                increment_attempt = 1
            lease_clause = " and lease_token=?" if expected_lease_token else ""
            cursor = db.execute(
                f"""
                update feishu_deliveries
                set status=?, feishu_message_id=?, request_log_id=?,
                    attempts=attempts + ?, remote_failures=?,
                    lease_token=?, locked_at=?,
                    mutation_started_at=?,
                    available_at=?,
                    error_code=?, error=?,
                    review_generation=?, approval_hash=?,
                    approved_at=case when ? then '' else approved_at end,
                    approved_by=case when ? then '' else approved_by end,
                    updated_at=current_timestamp
                where id=? and status in ({placeholders})
                  {lease_clause}
                """,
                (
                    to_status,
                    resulting_message_id,
                    resulting_log_id,
                    increment_attempt,
                    next_remote_failures,
                    next_lease_token,
                    next_locked_at,
                    next_mutation_started_at,
                    available_at,
                    error_code,
                    error,
                    next_review_generation,
                    next_approval_hash,
                    int(clear_approval),
                    int(clear_approval),
                    delivery_id,
                    *sources,
                    *([expected_lease_token] if expected_lease_token else []),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu delivery transition lost atomic race")
            row = db.execute(
                "select * from feishu_deliveries where id=?", (delivery_id,)
            ).fetchone()
            if receipt_message_ids:
                self._persist_feishu_delivery_receipts(
                    db,
                    row,
                    receipt_message_ids,
                    allow_existing_prefix=(
                        current["status"] == "send_unknown"
                        and to_status in {"sent", "retry"}
                    ),
                )
            self._sync_feishu_attempt_from_delivery(db, row)
            self._append_feishu_audit_event(
                db,
                app_id=row["app_id"],
                entity_type="delivery",
                entity_id=delivery_id,
                event_type=audit_event_type or to_status,
                previous_state=current["status"],
                new_state=to_status,
                actor=actor,
                detail=audit_detail or (
                    f"error_code={error_code}" if error_code else ""
                ),
            )
            return self._feishu_delivery_from_row(row)

    def set_feishu_delivery_status(
        self,
        delivery_id: int,
        status: str,
        *,
        from_statuses: tuple[str, ...] | list[str] | set[str],
        **fields,
    ) -> FeishuDelivery:
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=from_statuses,
            to_status=status,
            **fields,
        )

    def mark_feishu_delivery_sent(
        self,
        delivery_id: int,
        *,
        feishu_message_id: str,
        message_ids: tuple[str, ...] | list[str] = (),
        request_log_id: str = "",
    ) -> FeishuDelivery:
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=("sending",),
            to_status="sent",
            feishu_message_id=feishu_message_id,
            message_ids=message_ids,
            request_log_id=request_log_id,
        )

    def mark_feishu_delivery_retry(
        self,
        delivery_id: int,
        *,
        error_code: str,
        error: str,
        available_at: str = "",
    ) -> FeishuDelivery:
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=("sending",),
            to_status="retry",
            error_code=error_code,
            error=error,
            available_at=available_at,
        )

    def mark_feishu_delivery_send_unknown(
        self,
        delivery_id: int,
        *,
        error_code: str,
        error: str,
        request_log_id: str = "",
    ) -> FeishuDelivery:
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=("sending",),
            to_status="send_unknown",
            request_log_id=request_log_id,
            error_code=error_code,
            error=error,
        )

    def mark_feishu_delivery_failed(
        self,
        delivery_id: int,
        *,
        error_code: str,
        error: str,
    ) -> FeishuDelivery:
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=("sending",),
            to_status="failed",
            error_code=error_code,
            error=error,
        )

    def mark_feishu_delivery_rejected(
        self,
        delivery_id: int,
        *,
        app_id: str,
        rejected_by: str = "local-reviewer",
        error: str = "rejected_by_reviewer",
    ) -> FeishuDelivery:
        if not app_id.strip():
            raise ValueError("Feishu delivery rejection requires app_id")
        if not rejected_by.strip():
            raise ValueError("Feishu delivery rejection requires rejected_by")
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=("ready_to_send", "retry"),
            to_status="rejected",
            error_code="rejected",
            error=error,
            app_id=app_id,
            actor=rejected_by.strip(),
            audit_event_type="rejected",
        )

    def reject_feishu_delivery(
        self,
        delivery_id: int,
        *,
        app_id: str,
        rejected_by: str = "local-reviewer",
        error: str = "rejected_by_reviewer",
    ) -> FeishuDelivery:
        return self.mark_feishu_delivery_rejected(
            delivery_id,
            app_id=app_id,
            rejected_by=rejected_by,
            error=error,
        )

    def reconcile_feishu_delivery_unknown(
        self,
        delivery_id: int,
        *,
        app_id: str,
        outcome: str,
        verified_by: str,
        evidence_kind: str,
        feishu_message_id: str = "",
        message_ids: tuple[str, ...] | list[str] = (),
        expected_chunks: int = 0,
        request_log_id: str = "",
    ) -> FeishuDelivery:
        """Resolve one uncertain send from independently verified evidence.

        Only a closed evidence-kind enum is persisted.  A verified prefix is
        either complete (``sent``) or safely resumed from its suffix
        (``retry``).  A verified non-delivery becomes ``failed`` and requires
        a separate requeue plus a fresh approval before another send.
        """
        normalized = outcome.strip().lower().replace("-", "_")
        if normalized not in {"sent", "not_sent"}:
            raise ValueError("unknown Feishu reconciliation outcome")
        normalized_evidence = evidence_kind.strip().lower()
        if normalized_evidence not in FEISHU_RECONCILIATION_EVIDENCE_KINDS:
            raise ValueError("unknown Feishu reconciliation evidence kind")
        if not app_id.strip() or not verified_by.strip():
            raise ValueError(
                "Feishu reconciliation requires app_id and verified_by"
            )
        detail = f"evidence_kind={normalized_evidence}"
        current = self.get_feishu_delivery(delivery_id)
        if current is None:
            raise ValueError("Feishu delivery not found")
        if current.app_id != app_id.strip():
            raise PermissionError(
                "Feishu delivery App ID does not match runtime"
            )
        if normalized == "sent":
            if feishu_message_id.strip() and message_ids:
                raise ValueError(
                    "verified sent outcome must use one message ID input form"
                )
            verified_ids = self._normalize_feishu_delivery_message_ids(
                message_ids
                if message_ids
                else ((feishu_message_id.strip(),) if feishu_message_id.strip() else ())
            )
            supplied_expected = expected_chunks or (
                1 if feishu_message_id.strip() and not message_ids else 0
            )
            if supplied_expected != current.expected_chunks:
                raise ValueError(
                    "verified sent outcome expected chunk count does not match"
                )
            if not verified_ids or len(verified_ids) > supplied_expected:
                raise ValueError(
                    "verified sent outcome requires an ordered chunk prefix"
                )
            complete = len(verified_ids) == supplied_expected
            return self.transition_feishu_delivery(
                delivery_id,
                from_statuses=("send_unknown",),
                to_status=("sent" if complete else "retry"),
                feishu_message_id=verified_ids[0],
                message_ids=verified_ids,
                request_log_id=request_log_id.strip(),
                remote_failures=0,
                fill_request_log_id_only=True,
                verify_receipt_prefix_extension=True,
                app_id=app_id.strip(),
                actor=verified_by.strip(),
                audit_event_type=(
                    "unknown_verified_sent"
                    if complete
                    else "unknown_verified_partial"
                ),
                audit_detail=(
                    f"{detail};verified_prefix={len(verified_ids)}/"
                    f"{supplied_expected}"
                ),
            )
        if feishu_message_id.strip() or message_ids or expected_chunks:
            raise ValueError(
                "verified not-sent outcome must not include chunk results"
            )
        structurally_unresumable = not (
            current.error_code != "legacy_chunk_plan_unverifiable"
            and _feishu_unknown_allows_one_chunk_verification(
                {
                    "error_code": current.error_code,
                    "error": current.error,
                }
            )
        )
        terminal_error_code = (
            "verified_unresumable_not_sent"
            if structurally_unresumable
            else "verified_not_sent"
        )
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=("send_unknown",),
            to_status="failed",
            request_log_id=request_log_id.strip(),
            remote_failures=0,
            fill_request_log_id_only=True,
            error_code=terminal_error_code,
            error=(
                "manual_verification_closed_unresumable_delivery"
                if structurally_unresumable
                else "manual_verification_confirmed_not_sent"
            ),
            app_id=app_id.strip(),
            actor=verified_by.strip(),
            audit_event_type=f"unknown_verified_{normalized}",
            audit_detail=detail,
        )

    def requeue_feishu_delivery_after_verification(
        self,
        delivery_id: int,
        *,
        app_id: str,
        verified_by: str,
        evidence_kind: str,
        available_at: str = "",
    ) -> FeishuDelivery:
        """Create an explicit retry opportunity with prior approval revoked."""
        normalized_evidence = evidence_kind.strip().lower()
        if normalized_evidence not in FEISHU_RECONCILIATION_EVIDENCE_KINDS:
            raise ValueError("unknown Feishu reconciliation evidence kind")
        if not app_id.strip() or not verified_by.strip():
            raise ValueError(
                "Feishu requeue requires app_id and verified_by"
            )
        normalized_available_at = available_at.strip()
        if normalized_available_at:
            try:
                parsed = datetime.fromisoformat(
                    normalized_available_at.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError("invalid Feishu requeue available_at") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Feishu requeue available_at requires timezone")
            normalized_available_at = parsed.astimezone(timezone.utc).isoformat()
        self.validate_feishu_delivery_for_send(
            delivery_id, app_id=app_id.strip()
        )
        self.validate_feishu_delivery_receipt_prefix(
            delivery_id, app_id=app_id.strip()
        )
        return self.transition_feishu_delivery(
            delivery_id,
            from_statuses=("failed",),
            to_status="retry",
            error_code="",
            error="",
            available_at=normalized_available_at,
            remote_failures=0,
            app_id=app_id.strip(),
            actor=verified_by.strip(),
            audit_event_type="requeued_after_verification",
            clear_approval=True,
            rotate_review_generation=True,
            audit_detail=f"evidence_kind={normalized_evidence}",
            required_error_code="verified_not_sent",
        )

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
    def create_wechat_delivery(
        self, *, reply_task_id: int, account_id: str, target_type: str,
        target_id: str, conversation_id: str, reply_text: str,
        evidence: dict[str, str] | None = None,
    ) -> int:
        with self._connect() as db:
            db.execute(
                """
                insert into wechat_deliveries (
                    reply_task_id, account_id, target_type, target_id,
                    conversation_id, reply_text, evidence_json
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(reply_task_id) do nothing
                """,
                (
                    reply_task_id, account_id, target_type, target_id,
                    conversation_id, reply_text,
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
            status=row["status"], evidence=json.loads(row["evidence_json"]),
            error=row["error"],
        )

    def list_wechat_deliveries_by_status(self, status: str) -> list:
        from app.wechat.models import WechatDelivery
        with self._connect() as db:
            rows = db.execute(
                "select * from wechat_deliveries where status=? order by id", (status,)
            ).fetchall()
        return [
            WechatDelivery(
                id=row["id"], task_id=row["reply_task_id"], account_id=row["account_id"],
                target_type=row["target_type"], target_id=row["target_id"],
                conversation_id=row["conversation_id"], reply_text=row["reply_text"],
                status=row["status"], evidence=json.loads(row["evidence_json"]),
                error=row["error"],
            )
            for row in rows
        ]

    def mark_wechat_delivery_sending(self, delivery_id: int, *, now: str = "") -> None:
        self.set_wechat_delivery_status(delivery_id, "sending", action_started_at=now)

    def set_wechat_delivery_status(
        self, delivery_id: int, status: str, *, error: str = "",
        action_started_at: str | None = None,
    ) -> None:
        with self._connect() as db:
            if action_started_at is not None:
                db.execute(
                    "update wechat_deliveries set status=?, error=?, "
                    "action_started_at=?, updated_at=current_timestamp where id=?",
                    (status, error, action_started_at, delivery_id),
                )
            else:
                db.execute(
                    "update wechat_deliveries set status=?, error=?, "
                    "updated_at=current_timestamp where id=?",
                    (status, error, delivery_id),
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
                    where status in ('pending', 'retry')
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
                  and status in ('pending', 'retry')
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
                    locked_at=null,
                    updated_at=current_timestamp
                where status='processing'
                returning *
                """
            ).fetchall()
            jobs = [self._meeting_alignment_job_from_row(row) for row in rows]
            return sorted(jobs, key=lambda job: job.id)

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
                  and status='no_action'
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
                    error
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        with self._connect() as db:
            db.execute("begin immediate")
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
        with self._connect() as db:
            db.execute("begin immediate")
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

    def acquire_codex_session_lock(self, conversation_id: str, owner: str) -> bool:
        if not conversation_id.strip():
            raise ValueError("missing conversation_id")
        if not owner.strip():
            raise ValueError("missing lock owner")
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
            return cursor.rowcount == 1

    def release_codex_session_lock(self, conversation_id: str, owner: str) -> bool:
        if not conversation_id.strip():
            raise ValueError("missing conversation_id")
        if not owner.strip():
            raise ValueError("missing lock owner")
        with self._connect() as db:
            cursor = db.execute(
                """
                delete from codex_session_locks
                where conversation_id=? and owner=?
                """,
                (conversation_id, owner),
            )
            return cursor.rowcount == 1

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
        with self._connect() as db:
            target = db.execute(
                """
                select id
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
                    updated_at=current_timestamp
                where id=?
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
                    task_id,
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
                set codex_session_id=null
                where codex_session_id is not null and codex_session_id != ''
                """
            )
            return cursor.rowcount

    def clear_codex_session(self, conversation_id: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update conversations
                set codex_session_id=null
                where conversation_id=?
                """,
                (conversation_id,),
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
                    coalesce(ra.conversation_title, '') as conversation_title,
                    coalesce(ra.trigger_sender, '') as trigger_sender,
                    coalesce(ra.trigger_text, '') as trigger_text,
                    coalesce(ra.final_reply_text, '') as final_reply_text,
                    coalesce(ra.reviewer_feedback, '') as reviewer_feedback,
                    coalesce(ra.corrected_reply_text, '') as corrected_reply_text,
                    fe.resolved_at,
                    fe.updated_at
                from feedback_events fe
                left join latest_attempt_by_token latest
                    on latest.feedback_token = fe.feedback_token
                left join reply_attempts ra
                    on ra.id = latest.attempt_id
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
                left join latest_attempt_by_token latest
                    on latest.feedback_token = fe.feedback_token
                left join reply_attempts ra
                    on ra.id = latest.attempt_id
                where trim(fe.resolved_at) = ''
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
        universal_execution_id: str = "",
        universal_execution_scope_id: str = "",
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
                    universal_execution_id,
                    universal_execution_scope_id,
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
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    universal_execution_id,
                    universal_execution_scope_id,
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
            if channel == "feishu":
                task = db.execute(
                    """
                    select channel, conversation_id, trigger_message_id,
                           trigger_message_json
                    from reply_tasks
                    where channel='feishu' and conversation_id=?
                      and trigger_message_id=?
                    """,
                    (conversation_id, trigger_message_id),
                ).fetchone()
                if task is None:
                    raise ValueError(
                        "Feishu reply attempt requires a durable reply task"
                    )
                self._append_feishu_audit_event(
                    db,
                    app_id=self._feishu_task_app_id(task),
                    entity_type="reply_attempt",
                    entity_id=attempt_id,
                    event_type="attempt_recorded",
                    new_state=send_status,
                    actor="consumer",
                )
            self._record_memory_write_events_in_connection(
                db,
                attempt_id,
                audit_tool_events_json,
            )
            return attempt_id

    def record_universal_reply_attempt(
        self,
        execution: UniversalActionExecution,
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
        audit_tool_events_json: str = "[]",
        audit_summary: str = "",
        send_status: str = "pending",
    ) -> int:
        if execution.context.conversation_id != conversation_id:
            raise ValueError("universal attempt conversation mismatch")
        if execution.context.trigger_message_id != trigger_message_id:
            raise ValueError("universal attempt trigger mismatch")
        if execution.action.kind.value != action:
            raise ValueError("universal attempt action mismatch")
        with self._connect() as db:
            db.execute("begin immediate")
            execution_row = self._validate_universal_action_execution(db, execution)
            if execution_row is None or execution_row["status"] not in {
                "started",
                "recovering",
            }:
                raise ValueError("universal action execution must be started")
            existing = db.execute(
                """
                select * from reply_attempts
                where universal_execution_id=?
                """,
                (execution.execution_id,),
            ).fetchone()
            if existing is not None:
                immutable_fields = {
                    "universal_execution_scope_id": execution.execution_scope_id,
                    "conversation_id": conversation_id,
                    "conversation_title": conversation_title,
                    "trigger_message_id": trigger_message_id,
                    "trigger_sender": trigger_sender,
                    "trigger_text": trigger_text,
                    "action": action,
                    "sensitivity_kind": sensitivity_kind,
                    "codex_reason": codex_reason,
                    "draft_reply_text": draft_reply_text,
                }
                mismatched_fields = [
                    field_name
                    for field_name, expected_value in immutable_fields.items()
                    if existing[field_name] != expected_value
                ]
                if mismatched_fields:
                    raise ValueError(
                        "universal attempt identity mismatch: "
                        + ", ".join(mismatched_fields)
                    )
                db.execute(
                    """
                    update reply_attempts
                    set direct_user_id='',
                        direct_open_dingtalk_id='',
                        final_reply_text='',
                        permission_action='',
                        permission_reason='',
                        send_status=?,
                        send_error='',
                        retry_count=retry_count + 1,
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (send_status, existing["id"]),
                )
                return int(existing["id"])

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
                    audit_tool_events_json,
                    audit_summary,
                    universal_execution_id,
                    universal_execution_scope_id,
                    send_status
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    audit_tool_events_json,
                    audit_summary,
                    execution.execution_id,
                    execution.execution_scope_id,
                    send_status,
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
            or existing_attempt.universal_execution_id
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
        send_status: str | None = None,
        send_error: str | None = None,
        retry_count: int | None = None,
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
            send_status=send_status,
            send_error=send_error,
            retry_count=retry_count,
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

    def update_reply_attempt_and_complete_task(
        self,
        attempt_id: int,
        task_id: int,
        **updates: object,
    ) -> None:
        update_values = self._reply_attempt_update_values(**updates)
        with self._connect() as db:
            if update_values:
                self._update_reply_attempt_in_connection(
                    db,
                    attempt_id,
                    update_values,
                )
                audit_tool_events_json = update_values.get("audit_tool_events_json")
                if isinstance(audit_tool_events_json, str):
                    self._record_memory_write_events_in_connection(
                        db,
                        attempt_id,
                        audit_tool_events_json,
                    )
            db.execute(
                """
                update reply_tasks
                set status='done',
                    lease_token='',
                    locked_at=null,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id=?
                """,
                (task_id,),
            )

    def update_reply_attempt_and_fail_task(
        self,
        attempt_id: int,
        task_id: int,
        *,
        error: str,
        **updates: object,
    ) -> None:
        """Atomically terminalize a Feishu attempt and its claimed task."""
        update_values = self._reply_attempt_update_values(**updates)
        with self._connect() as db:
            db.execute("begin immediate")
            attempt = db.execute(
                """
                select channel, conversation_id, trigger_message_id, send_status
                from reply_attempts where id=?
                """,
                (attempt_id,),
            ).fetchone()
            task = db.execute(
                """
                select channel, conversation_id, trigger_message_id,
                       trigger_message_json
                from reply_tasks where id=?
                """,
                (task_id,),
            ).fetchone()
            if attempt is None or task is None:
                raise ValueError("Feishu attempt or task not found")
            if attempt["channel"] != "feishu" or task["channel"] != "feishu":
                raise ValueError("Feishu terminal update requires Feishu rows")
            if (
                attempt["conversation_id"] != task["conversation_id"]
                or attempt["trigger_message_id"] != task["trigger_message_id"]
            ):
                raise ValueError("Feishu attempt does not match reply task")
            if update_values:
                self._update_reply_attempt_in_connection(
                    db, attempt_id, update_values
                )
            cursor = db.execute(
                """
                update reply_tasks
                set status='failed', lease_token='', locked_at=null,
                    error=?, available_at='',
                    updated_at=current_timestamp
                where id=? and status='processing'
                """,
                (safe_observability_error(error), task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu reply task is not processing")
            self._append_feishu_audit_event(
                db,
                app_id=self._feishu_task_app_id(task),
                entity_type="reply_attempt",
                entity_id=attempt_id,
                event_type="attempt_failed",
                previous_state=attempt["send_status"],
                new_state=str(update_values.get("send_status") or "failed"),
                actor="consumer",
                detail="error_code=local_consumer_failure",
            )

    def update_feishu_reply_attempt_and_complete_task(
        self,
        attempt_id: int,
        task_id: int,
        **updates: object,
    ) -> None:
        """Atomically terminalize a no-send Feishu decision and its task."""
        update_values = self._reply_attempt_update_values(**updates)
        with self._connect() as db:
            db.execute("begin immediate")
            attempt = db.execute(
                """
                select channel, conversation_id, trigger_message_id, send_status
                from reply_attempts where id=?
                """,
                (attempt_id,),
            ).fetchone()
            task = db.execute(
                """
                select channel, conversation_id, trigger_message_id,
                       trigger_message_json
                from reply_tasks where id=?
                """,
                (task_id,),
            ).fetchone()
            if attempt is None or task is None:
                raise ValueError("Feishu attempt or task not found")
            if attempt["channel"] != "feishu" or task["channel"] != "feishu":
                raise ValueError("Feishu terminal update requires Feishu rows")
            if (
                attempt["conversation_id"] != task["conversation_id"]
                or attempt["trigger_message_id"] != task["trigger_message_id"]
            ):
                raise ValueError("Feishu attempt does not match reply task")
            if update_values:
                self._update_reply_attempt_in_connection(
                    db, attempt_id, update_values
                )
            cursor = db.execute(
                """
                update reply_tasks
                set status='done', lease_token='', locked_at=null,
                    error='', available_at='',
                    updated_at=current_timestamp
                where id=? and status='processing'
                """,
                (task_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Feishu reply task is not processing")
            self._append_feishu_audit_event(
                db,
                app_id=self._feishu_task_app_id(task),
                entity_type="reply_attempt",
                entity_id=attempt_id,
                event_type="no_send_completed",
                previous_state=attempt["send_status"],
                new_state=str(update_values.get("send_status") or "skipped"),
                actor="consumer",
            )

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
            "send_status",
            "send_error",
            "retry_count",
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

    def get_reply_attempt(self, attempt_id: int) -> ReplyAttempt | None:
        with self._connect() as db:
            row = db.execute(
                "select * from reply_attempts where id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            return ReplyAttempt.model_validate(dict(row))

    def get_universal_execution_observability(
        self,
        attempt_id: int,
    ) -> UniversalExecutionObservation | None:
        return self.list_universal_execution_observability([attempt_id]).get(
            attempt_id
        )

    def list_universal_execution_observability(
        self,
        attempt_ids: list[int],
    ) -> dict[int, UniversalExecutionObservation]:
        if not attempt_ids:
            return {}
        placeholders = ",".join("?" for _ in attempt_ids)
        with self._connect() as db:
            plan_rows = db.execute(
                f"""
                select
                    attempts.id as attempt_id,
                    plans.execution_scope_id,
                    plans.plan_json,
                    plans.context_json,
                    tasks.status as task_status,
                    tasks.error as task_error
                from reply_attempts as attempts
                join universal_plan_executions as plans
                  on plans.execution_scope_id=attempts.universal_execution_scope_id
                join reply_tasks as tasks on tasks.id=plans.reply_task_id
                where attempts.id in ({placeholders})
                """,
                attempt_ids,
            ).fetchall()
            scope_ids = [row["execution_scope_id"] for row in plan_rows]
            action_rows: list[sqlite3.Row] = []
            if scope_ids:
                scope_placeholders = ",".join("?" for _ in scope_ids)
                action_rows = db.execute(
                    f"""
                    select execution_scope_id, action_index, action_kind, status, error
                    from universal_action_executions
                    where execution_scope_id in ({scope_placeholders})
                    order by execution_scope_id, action_index
                    """,
                    scope_ids,
                ).fetchall()

        actions_by_scope = {
            scope_id: {
                int(row["action_index"]): row
                for row in action_rows
                if row["execution_scope_id"] == scope_id
            }
            for scope_id in scope_ids
        }
        observations: dict[int, UniversalExecutionObservation] = {}
        for row in plan_rows:
            plan = UniversalPlan.model_validate_json(row["plan_json"])
            context_payload = json.loads(row["context_json"])
            required_dependencies = context_payload.get("required_dependencies", [])
            dependencies = list(
                dict.fromkeys(
                    [
                        dependency
                        for dependency in required_dependencies
                        if isinstance(dependency, str) and dependency
                    ]
                    + [str(dependency) for dependency in plan.dependencies]
                )
            )
            blocking_dependency = self._universal_blocking_dependency(
                dependencies,
                row["task_status"],
                row["task_error"],
            )
            persisted_actions = actions_by_scope.get(row["execution_scope_id"], {})
            observations[int(row["attempt_id"])] = UniversalExecutionObservation(
                capability=plan.task_kind,
                dependencies=dependencies,
                blocking_dependency=blocking_dependency,
                actions=[
                    UniversalActionObservation(
                        index=index,
                        kind=action.kind.value,
                        status=(
                            persisted_actions[index]["status"]
                            if index in persisted_actions
                            else "not_started"
                        ),
                        error=(
                            safe_observability_error(persisted_actions[index]["error"])
                            if index in persisted_actions
                            else ""
                        ),
                    )
                    for index, action in enumerate(plan.actions)
                ],
            )
        return observations

    @staticmethod
    def _universal_blocking_dependency(
        dependencies: list[str],
        task_status: str,
        task_error: str,
    ) -> str:
        if task_status not in {"pending", "processing", "failed"}:
            return ""
        normalized_error = task_error.strip().casefold()
        for dependency in dependencies:
            normalized_dependency = dependency.casefold()
            if normalized_error in {
                f"{normalized_dependency}_unavailable",
                f"{normalized_dependency}_authorization_required",
                f"dependency_status_missing:{normalized_dependency}",
            }:
                return dependency
        return ""

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
        created_since: str = "",
    ) -> list[HistoryItem]:
        query, args = self._history_items_query(
            send_statuses=send_statuses,
            query_text=query_text,
            kinds=kinds,
            reply_channels=reply_channels,
            created_since=created_since,
        )
        query = f"{query} order by created_at desc, source_id desc, kind desc"
        if limit is not None:
            query = f"{query} limit ? offset ?"
            args.extend([limit, max(0, offset)])
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        items = [HistoryItem.model_validate(dict(row)) for row in rows]
        observations = self.list_universal_execution_observability(
            [item.source_id for item in items if item.kind == "reply"]
        )
        return [
            item.model_copy(
                update={
                    "planner_kind": observation.planner_kind,
                    "capability": observation.capability,
                    "blocking_dependency": observation.blocking_dependency,
                    "planned_actions": observation.actions,
                }
            )
            if (observation := observations.get(item.source_id)) is not None
            else item
            for item in items
        ]

    def count_history_items(
        self,
        *,
        send_statuses: tuple[str, ...] | None = None,
        query_text: str = "",
        kinds: tuple[str, ...] | None = None,
        reply_channels: tuple[str, ...] | None = None,
        created_since: str = "",
    ) -> int:
        query, args = self._history_items_query(
            send_statuses=send_statuses,
            query_text=query_text,
            kinds=kinds,
            reply_channels=reply_channels,
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
        created_since: str,
    ) -> tuple[str, list[object]]:
        query = """
            with history_items as (
                select
                    'reply' as kind,
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
                    case channel
                        when 'wechat' then coalesce((
                            select case deliveries.status
                                when 'ready_to_send' then 'pending'
                                when 'sending' then 'processing'
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
                        when 'feishu' then coalesce((
                            select case deliveries.status
                                when 'ready_to_send' then 'pending'
                                when 'sending' then 'processing'
                                when 'retry' then 'processing'
                                else deliveries.status
                            end
                            from feishu_deliveries as deliveries
                            where deliveries.attempt_id=reply_attempts.id
                            limit 1
                        ), send_status)
                        else send_status
                    end as status,
                    conversation_title as target_title,
                    codex_session_id,
                    0 as project_id,
                    0 as todo_id,
                    0 as follow_up_id,
                    channel,
                    created_at,
                    conversation_id || ' ' || conversation_title || ' ' ||
                    trigger_message_id || ' ' || trigger_sender || ' ' ||
                    trigger_text || ' ' || action || ' ' || sensitivity_kind || ' ' ||
                    codex_reason || ' ' || draft_reply_text || ' ' || final_reply_text || ' ' ||
                    permission_action || ' ' || permission_reason || ' ' || send_status || ' ' ||
                    send_error || ' ' || reviewer_feedback || ' ' || corrected_reply_text
                    as search_text
                from reply_attempts
                union all
                select
                    'meeting' as kind,
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
                        ) then 'ready_to_send'
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
                    jobs.meeting_id || ' ' || jobs.title || ' ' || jobs.source_json || ' ' ||
                    jobs.participants_json || ' ' || jobs.error || ' ' || jobs.decision_json || ' ' ||
                    jobs.target_kind || ' ' || jobs.target_id || ' ' || jobs.target_title || ' ' ||
                    jobs.mentions_json || ' ' || jobs.final_message || ' ' || jobs.send_result_json || ' ' ||
                    runs.decision_json || ' ' || runs.audit_summary || ' ' || runs.error || ' ' ||
                    runs.codex_session_id || ' ' || runs.status
                    as search_text
                from meeting_alignment_runs as runs
                join meeting_alignment_jobs as jobs on jobs.id=runs.job_id
                union all
                select
                    'task' as kind,
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
                    projects.title || ' ' || projects.category || ' ' ||
                    projects.owner_name || ' ' || projects.goal || ' ' ||
                    projects.background || ' ' || projects.current_state || ' ' ||
                    projects.next_step || ' ' || updates.source_type || ' ' ||
                    updates.source_ref || ' ' || updates.summary || ' ' ||
                    updates.changes_json || ' ' || updates.merge_reason
                    as search_text
                from work_updates as updates
                join work_projects as projects on projects.id=updates.project_id
                union all
                select
                    'task' as kind,
                    drafts.id as source_id,
                    projects.title as source_title,
                    'Follow-up' as source_actor,
                    '跟进' as input_label,
                    drafts.question_text as input_text,
                    '结果' as output_label,
                    case
                        when drafts.status='sent' then coalesce(nullif(drafts.reaction_summary, ''), '已发送跟进')
                        when drafts.status in ('skipped', 'cancelled') then coalesce(nullif(drafts.suppressed_reason, ''), '已跳过跟进')
                        when drafts.status='failed' then coalesce(nullif(drafts.send_result_json, '{}'), '发送失败')
                        else drafts.scheduled_at
                    end as output_text,
                    'follow_up_' || drafts.status as action,
                    case
                        when drafts.status='sent' then 'sent'
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
                    projects.title || ' ' || projects.category || ' ' ||
                    projects.owner_name || ' ' || projects.goal || ' ' ||
                    projects.background || ' ' || projects.current_state || ' ' ||
                    projects.next_step || ' ' || coalesce(todos.title, '') || ' ' ||
                    coalesce(todos.description, '') || ' ' || drafts.owner_name || ' ' ||
                    drafts.target_conversation_id || ' ' || drafts.target_kind || ' ' ||
                    drafts.question_text || ' ' || drafts.status || ' ' ||
                    drafts.send_result_json || ' ' || drafts.evidence_check_json || ' ' ||
                    drafts.reaction_status || ' ' || drafts.reaction_summary || ' ' ||
                    drafts.suppressed_reason
                    as search_text
                from follow_up_drafts as drafts
                join work_projects as projects on projects.id=drafts.project_id
                left join work_todos as todos on todos.id=drafts.todo_id
            )
            select * from history_items
        """
        filters: list[str] = []
        args: list[object] = []
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
                order by id desc
                limit ?
                """,
                (process_id, max(1, limit)),
            ).fetchall()
            return [ReplyAttempt.model_validate(dict(row)) for row in rows]

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
                        when work_summary_inputs.status in ('failed', 'discarded')
                            then 'pending'
                        else work_summary_inputs.status
                    end,
                    error=case
                        when work_summary_inputs.status in ('failed', 'discarded')
                            then ''
                        else work_summary_inputs.error
                    end,
                    available_at=case
                        when work_summary_inputs.status in ('failed', 'discarded')
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
        with self._connect() as db:
            db.execute("begin immediate")
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
                    error='',
                    updated_at=current_timestamp
                where status='processing'
                  and datetime(updated_at) <= datetime('now', ?)
                """,
                (f"-{int(max_age_seconds)} seconds",),
            )
            return cursor.rowcount

    def reset_processing_work_summary_inputs(self) -> list[WorkSummaryInput]:
        with self._connect() as db:
            db.execute("begin immediate")
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
                    error='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                input_ids,
            )
            return [WorkSummaryInput.model_validate(dict(row)) for row in rows]

    def mark_work_summary_input_done(self, input_id: int) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='done', error='', updated_at=current_timestamp
                where id=?
                """,
                (input_id,),
            )

    def mark_work_summary_input_discarded(self, input_id: int, reason: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='discarded', error=?, updated_at=current_timestamp
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

    def create_work_project(self, **values) -> int:
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
        with self._connect() as db:
            cursor = db.execute(
                f"insert into work_projects ({columns}) values ({placeholders})",
                [filtered[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_work_project(self, project_id: int, **values) -> None:
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
        with self._connect() as db:
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

    def get_work_project(self, project_id: int) -> WorkProject | None:
        with self._connect() as db:
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

    def create_work_todo(self, **values) -> int:
        allowed_columns = {
            "project_id",
            "title",
            "description",
            "owner_user_id",
            "owner_name",
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
        with self._connect() as db:
            cursor = db.execute(
                f"insert into work_todos ({columns}) values ({placeholders})",
                [filtered[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_work_todo(self, todo_id: int, **values) -> None:
        if not values:
            return
        allowed_columns = {
            "project_id",
            "title",
            "description",
            "owner_user_id",
            "owner_name",
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
        with self._connect() as db:
            db.execute(
                f"""
                update work_todos
                set {', '.join(assignments)}, updated_at=current_timestamp
                where id=?
                """,
                [*parameters, todo_id],
            )

    def get_work_todo(self, todo_id: int) -> WorkTodo | None:
        with self._connect() as db:
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
        with self._connect() as db:
            db.execute("begin immediate")
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

    def create_work_update(self, **values) -> int:
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
        with self._connect() as db:
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
                    memory_recall_used
                )
                values (?, ?, ?, ?, ?)
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

    def create_follow_up_draft(self, **values) -> int:
        allowed_columns = {
            "project_id",
            "todo_id",
            "owner_user_id",
            "owner_name",
            "target_conversation_id",
            "target_kind",
            "question_text",
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
        with self._connect() as db:
            dedupe_key = str(filtered.get("dedupe_key") or "").strip()
            if dedupe_key:
                existing = db.execute(
                    """
                    select id
                    from follow_up_drafts
                    where dedupe_key=?
                      and status in ('draft', 'approved', 'sent', 'skipped', 'cancelled')
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

    def update_follow_up_draft(self, draft_id: int, **values) -> None:
        if not values:
            return
        allowed_columns = {
            "project_id",
            "todo_id",
            "owner_user_id",
            "owner_name",
            "target_conversation_id",
            "target_kind",
            "question_text",
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
        with self._connect() as db:
            db.execute(
                f"""
                update follow_up_drafts
                set {', '.join(assignments)},
                    updated_at=current_timestamp
                where id=?
                """,
                [*parameters, draft_id],
            )

    def get_follow_up_draft(self, draft_id: int) -> FollowUpDraft | None:
        if draft_id <= 0:
            return None
        with self._connect() as db:
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
    ) -> list[FollowUpDraft]:
        query = "select * from follow_up_drafts where todo_id=?"
        args: list[str | int] = [todo_id]
        if statuses:
            query = f"{query} and status in ({','.join('?' for _ in statuses)})"
            args.extend(statuses)
        query = f"{query} order by scheduled_at, id"
        with self._connect() as db:
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
        with self._connect() as db:
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
                    'active' as status,
                    coalesce(conversation_id, '') as context,
                    detail as summary,
                    detail as detail,
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
