import json
import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from pydantic import BaseModel, Field, TypeAdapter

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
from app.history import HistoryItem

FAST_PATH_UNREAD_BACKOFF_TASK_ERROR = "waiting_fast_path_unread_backoff"
SQLITE_BUSY_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MILLISECONDS = SQLITE_BUSY_TIMEOUT_SECONDS * 1000
CODEX_SESSION_LOCK_STALE_SECONDS = 20 * 60
_INITIALIZED_STORE_PATHS: set[Path] = set()
_INITIALIZE_LOCK = threading.Lock()


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
    final_reply_text: str
    permission_action: str
    permission_reason: str
    send_status: str
    send_error: str
    retry_count: int
    reviewed_at: str | None = None
    reviewer_feedback: str = ""
    corrected_reply_text: str = ""
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
    status: str
    attempts: int
    locked_at: str | None = None
    error: str = ""
    created_at: str
    updated_at: str


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
    def __init__(self, path: Path):
        self.path = path
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
            timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
        )
        connection.execute(f"pragma busy_timeout = {SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
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
                create table if not exists reply_tasks (
                    id integer primary key autoincrement,
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
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    locked_at text,
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(conversation_id, trigger_message_id)
                );
                create index if not exists idx_reply_tasks_status
                    on reply_tasks(status, id);
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
            ):
                if column not in reply_task_columns:
                    db.execute(
                        f"alter table reply_tasks add column {column} {definition}"
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

    @staticmethod
    def _reply_task_from_row(row: sqlite3.Row) -> ReplyTask:
        return ReplyTask(
            id=row["id"],
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
            status=row["status"],
            attempts=row["attempts"],
            locked_at=row["locked_at"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
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
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert or ignore into reply_tasks (
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
                    error
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    available_at,
                    int(force_new_decision),
                    oa_url,
                    manual_rerun_attempt_id,
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
        with self._connect() as db:
            db.execute(
                """
                insert into reply_tasks (
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
                    status,
                    locked_at,
                    error
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, '', 1, ?, ?, 'pending', null, ?)
                on conflict(conversation_id, trigger_message_id) do update set
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
                    f"manual_rerun_from_attempt:{attempt_id}",
                ),
            )
            row = db.execute(
                """
                select *
                from reply_tasks
                where conversation_id=? and trigger_message_id=?
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("manual rerun reply task was not persisted")
            return self._reply_task_from_row(row)

    def claim_reply_tasks(self, limit: int, now: str | None = None) -> list[ReplyTask]:
        if limit <= 0:
            return []
        with self._connect() as db:
            db.execute("begin immediate")
            now_expression = "current_timestamp" if now is None else "?"
            args: list[str | int] = []
            if now is not None:
                args.append(now)
            args.append(limit)
            rows = db.execute(
                f"""
                select *
                from reply_tasks
                where status='pending'
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

    def reset_stale_processing_reply_tasks(self, max_age_seconds: int) -> int:
        if max_age_seconds <= 0:
            return 0
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set status='pending',
                    locked_at=null,
                    error='',
                    updated_at=current_timestamp
                where status='processing'
                  and locked_at is not null
                  and datetime(locked_at) <= datetime('now', ?)
                """,
                (f"-{int(max_age_seconds)} seconds",),
            )
            return cursor.rowcount

    def reset_processing_reply_tasks(self) -> list[ReplyTask]:
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select *
                from reply_tasks
                where status='processing'
                order by locked_at, id
                """
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='pending',
                    locked_at=null,
                    error='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                task_ids,
            )
            return [self._reply_task_from_row(row) for row in rows]

    def list_stale_processing_reply_tasks(
        self, max_age_seconds: int
    ) -> list[ReplyTask]:
        if max_age_seconds <= 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_tasks
                where status='processing'
                  and locked_at is not null
                  and datetime(locked_at) <= datetime('now', ?)
                order by locked_at, id
                """,
                (f"-{int(max_age_seconds)} seconds",),
            ).fetchall()
            return [self._reply_task_from_row(row) for row in rows]

    def complete_unfinished_reply_tasks_before_trigger(
        self,
        *,
        conversation_id: str,
        trigger_create_time: str,
        exclude_task_id: int,
    ) -> list[ReplyTask]:
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select *
                from reply_tasks
                where conversation_id=?
                  and status in ('pending', 'processing')
                  and trigger_create_time < ?
                  and id != ?
                order by trigger_create_time, id
                """,
                (conversation_id, trigger_create_time, exclude_task_id),
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='done',
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
                where conversation_id=?
                  and status in ('pending', 'processing')
                  and trigger_message_id in ({placeholders})
                  and id != ?
                order by trigger_create_time, id
                """,
                [conversation_id, *trigger_message_ids, exclude_task_id],
            ).fetchall()
            task_ids = [row["id"] for row in rows]
            if not task_ids:
                return []
            task_placeholders = ",".join("?" for _ in task_ids)
            db.execute(
                f"""
                update reply_tasks
                set status='done',
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
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id=?
                """,
                (task_id,),
            )

    def complete_reply_task_for_message(
        self, conversation_id: str, trigger_message_id: str
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set status='done',
                    locked_at=null,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where conversation_id=?
                  and trigger_message_id=?
                """,
                (conversation_id, trigger_message_id),
            )
            return cursor.rowcount

    def fail_reply_task(self, task_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='failed',
                    error=?,
                    available_at='',
                    updated_at=current_timestamp
                where id=?
                """,
                (error, task_id),
            )

    def requeue_reply_task(
        self, task_id: int, error: str, *, available_at: str = ""
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='pending',
                    locked_at=null,
                    available_at=?,
                    error=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (available_at, error, task_id),
            )

    def defer_reply_task(
        self, task_id: int, error: str, *, available_at: str = ""
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='pending',
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

    def count_reply_tasks(self, status: str | None = None) -> int:
        with self._connect() as db:
            if status is None:
                row = db.execute("select count(*) as count from reply_tasks").fetchone()
            else:
                row = db.execute(
                    "select count(*) as count from reply_tasks where status=?",
                    (status,),
                ).fetchone()
            return int(row["count"])

    def list_reply_tasks(
        self,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[ReplyTask]:
        with self._connect() as db:
            query = """
                select *
                from reply_tasks
            """
            args: list[str | int] = []
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                query = f"{query} where status in ({placeholders})"
                args.extend(statuses)
            query = f"{query} order by id desc"
            if limit is not None:
                query = f"{query} limit ?"
                args.append(limit)
            rows = db.execute(query, args).fetchall()
            return [self._reply_task_from_row(row) for row in rows]

    def get_reply_task_for_message(
        self, conversation_id: str, trigger_message_id: str
    ) -> ReplyTask | None:
        with self._connect() as db:
            row = db.execute(
                """
                select *
                from reply_tasks
                where conversation_id=? and trigger_message_id=?
                order by id desc
                limit 1
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            if row is None:
                return None
            return self._reply_task_from_row(row)

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
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                update reply_tasks
                set trigger_text=?,
                    trigger_message_json=?,
                    updated_at=current_timestamp
                where conversation_id=?
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
    ) -> int:
        with self._connect() as db:
            target = db.execute(
                """
                select id
                from reply_tasks
                where conversation_id=?
                  and single_chat=1
                  and status='pending'
                  and attempts=0
                  and trigger_create_time <= ?
                order by trigger_create_time desc, id desc
                limit 1
                """,
                (conversation_id, trigger_create_time),
            ).fetchone()
            if target is None:
                return 0
            task_id = int(target["id"])
            cursor = db.execute(
                """
                update reply_tasks
                set trigger_message_id=?,
                    trigger_create_time=?,
                    trigger_sender=?,
                    trigger_text=?,
                    trigger_message_json=?,
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
                where conversation_id=?
                  and single_chat=1
                  and status='pending'
                  and attempts=0
                  and id != ?
                """,
                (conversation_id, task_id),
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
                    send_status
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            return int(cursor.lastrowid)

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
        if existing_attempt is None or self.has_sent_reply_for_trigger(
            conversation_id, trigger_message_id
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
        audit_tool_events_json: str | None = None,
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
            audit_tool_events_json=audit_tool_events_json,
            send_status=send_status,
            send_error=send_error,
            retry_count=retry_count,
        )
        if not updates:
            return
        with self._connect() as db:
            self._update_reply_attempt_in_connection(db, attempt_id, updates)

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
            db.execute(
                """
                update reply_tasks
                set status='done',
                    locked_at=null,
                    error='',
                    available_at='',
                    updated_at=current_timestamp
                where id=?
                """,
                (task_id,),
            )

    def reply_task_is_done(self, task_id: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                "select status from reply_tasks where id=?",
                (task_id,),
            ).fetchone()
        return bool(row and row["status"] == "done")

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
            "audit_tool_events_json",
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
        created_since: str = "",
    ) -> list[HistoryItem]:
        query, args = self._history_items_query(
            send_statuses=send_statuses,
            query_text=query_text,
            kinds=kinds,
            created_since=created_since,
        )
        query = (
            f"{query} order by datetime(created_at) desc, source_id desc, kind desc"
        )
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
        created_since: str = "",
    ) -> int:
        query, args = self._history_items_query(
            send_statuses=send_statuses,
            query_text=query_text,
            kinds=kinds,
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
                    send_status as status,
                    conversation_title as target_title,
                    codex_session_id,
                    0 as project_id,
                    0 as todo_id,
                    0 as follow_up_id,
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
                        when runs.status='ready_to_send' and exists (
                            select 1 from meeting_alignment_runs as later_runs
                            where later_runs.job_id=runs.job_id and later_runs.id>runs.id
                        ) then 'failed'
                        when runs.status='ready_to_send' and jobs.status='sent' then 'sent'
                        when runs.status='ready_to_send' and jobs.status in ('retry', 'failed') then 'failed'
                        else runs.status
                    end as status,
                    jobs.target_title,
                    runs.codex_session_id,
                    0 as project_id,
                    0 as todo_id,
                    0 as follow_up_id,
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
                        when drafts.status in ('skipped', 'cancelled') then 'skipped'
                        when drafts.status='failed' then 'failed'
                        else 'processing'
                    end as status,
                    coalesce(nullif(todos.title, ''), drafts.owner_name, projects.title) as target_title,
                    '' as codex_session_id,
                    drafts.project_id as project_id,
                    drafts.todo_id as todo_id,
                    drafts.id as follow_up_id,
                    coalesce(nullif(drafts.sent_at, ''), drafts.updated_at, drafts.created_at) as created_at,
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
        if created_since.strip():
            filters.append("datetime(created_at) >= datetime(?)")
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
                where datetime(created_at) >= datetime(?)
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
            clauses.append("scheduled_at != '' and scheduled_at <= ?")
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
