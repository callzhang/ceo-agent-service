import sqlite3
import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.task_models import (
    FollowUpDraft,
    TaskAgentRun,
    WorkProject,
    WorkSummaryInput,
    WorkTodo,
    WorkUpdate,
)

FAST_PATH_UNREAD_BACKOFF_TASK_ERROR = "waiting_fast_path_unread_backoff"


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


class ReplyError(BaseModel):
    id: int
    conversation_id: str | None = None
    message_id: str | None = None
    kind: str
    detail: str
    created_at: str


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
    status: str
    attempts: int
    locked_at: str | None = None
    error: str = ""
    created_at: str
    updated_at: str


class AutoReplyStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
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
                    scheduled_at text not null default '',
                    sent_at text not null default '',
                    created_at text not null default current_timestamp
                );
                create index if not exists idx_follow_up_drafts_status
                    on follow_up_drafts(status, scheduled_at, id);
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
            ):
                if column not in reply_task_columns:
                    db.execute(
                        f"alter table reply_tasks add column {column} {definition}"
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
            status=row["status"],
            attempts=row["attempts"],
            locked_at=row["locked_at"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

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
                    error
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    error,
                ),
            )
            return cursor.rowcount == 1

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
                    error=case
                        when error=? then ''
                        else error
                    end,
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                [FAST_PATH_UNREAD_BACKOFF_TASK_ERROR, *task_ids],
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

    def requeue_reply_task(self, task_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='pending',
                    locked_at=null,
                    available_at='',
                    error=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (error, task_id),
            )

    def defer_reply_task(self, task_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update reply_tasks
                set status='pending',
                    attempts=max(attempts - 1, 0),
                    locked_at=null,
                    available_at='',
                    error=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (error, task_id),
            )

    def defer_reply_task_for_authorization(self, task_id: int, error: str) -> None:
        self.defer_reply_task(task_id, error)

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
                    send_status
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        send_status: str = "pending",
    ) -> int:
        existing_attempt = self.get_latest_reply_attempt_for_trigger(
            conversation_id, trigger_message_id
        )
        if existing_attempt is None:
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
        send_status: str | None = None,
        send_error: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        assignments = []
        values = []
        for column, value in (
            ("action", action),
            ("final_reply_text", final_reply_text),
            ("permission_action", permission_action),
            ("permission_reason", permission_reason),
            ("direct_user_id", direct_user_id),
            ("direct_open_dingtalk_id", direct_open_dingtalk_id),
            ("oa_process_instance_id", oa_process_instance_id),
            ("oa_task_id", oa_task_id),
            ("oa_url", oa_url),
            ("oa_action", oa_action),
            ("oa_remark", oa_remark),
            ("oa_action_result_json", oa_action_result_json),
            ("calendar_event_id", calendar_event_id),
            ("calendar_response_status", calendar_response_status),
            ("calendar_response_result_json", calendar_response_result_json),
            ("send_status", send_status),
            ("send_error", send_error),
            ("retry_count", retry_count),
        ):
            if value is None:
                continue
            assignments.append(f"{column}=?")
            values.append(value)
        if not assignments:
            return
        assignments.append("updated_at=current_timestamp")
        values.append(attempt_id)
        with self._connect() as db:
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
        self, limit: int | None = None, offset: int = 0
    ) -> list[ReplyAttempt]:
        with self._connect() as db:
            query = """
                select *
                from reply_attempts
                order by id desc
            """
            args: tuple[int, ...] = ()
            if limit is not None:
                query = f"{query} limit ? offset ?"
                args = (limit, max(0, offset))
            rows = db.execute(query, args).fetchall()
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

    def count_reply_attempts(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "select count(*) as count from reply_attempts"
            ).fetchone()
            return int(row["count"])

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

    def create_work_todo(self, **values) -> int:
        allowed_columns = {
            "project_id",
            "title",
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
            "scheduled_at",
            "sent_at",
        }
        filtered = self._filter_allowed_values(values, allowed_columns)
        keys = list(filtered.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._connect() as db:
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
                f"update follow_up_drafts set {', '.join(assignments)} where id=?",
                [*parameters, draft_id],
            )

    def list_follow_up_drafts(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        due_before: str | None = None,
        limit: int = 200,
    ) -> list[FollowUpDraft]:
        query = "select * from follow_up_drafts"
        clauses: list[str] = []
        args: list[str | int] = []
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
