"""Typed 4B task storage with an exact, paired 11.2 source inverse.

The caller owns the private FK-off transaction. Public numeric IDs stay in their
source namespaces; only declared references use the global physical task ID.
"""

from contextlib import closing
from functools import lru_cache
import re
import sqlite3

from app.run_storage import (
    MEETING_RUN_COLUMNS,
    MEETING_RUN_RETIRED,
    MEETING_RUN_SQL,
    OKR_RUN_COLUMNS,
    OKR_RUN_RETIRED,
    OKR_RUN_SQL,
    TASK_AGENT_RUN_COLUMNS,
    TASK_AGENT_RUN_RETIRED,
    TASK_AGENT_RUN_SQL,
    _drop_explicit_objects,
    _schema_objects,
    _set_sequence,
    create_run_schema,
    insert_run,
    run_schema_is_current,
)

TASK_TYPES = (
    "reply_tasks", "reply_task_inputs", "work_summary_inputs", "meeting_alignment_jobs",
    "okr_review_requests", "weekly_okr_analysis_jobs",
)
DOMAIN_RUN_COLUMNS = {
    "task_agent_runs": TASK_AGENT_RUN_COLUMNS,
    "meeting_alignment_runs": MEETING_RUN_COLUMNS,
    "okr_review_runs": OKR_RUN_COLUMNS,
}
DOMAIN_RUN_RETIRED = {
    "task_agent_runs": TASK_AGENT_RUN_RETIRED,
    "meeting_alignment_runs": MEETING_RUN_RETIRED,
    "okr_review_runs": OKR_RUN_RETIRED,
}
DOMAIN_RUN_PROJECTIONS = {
    "task_agent_runs": TASK_AGENT_RUN_SQL,
    "meeting_alignment_runs": MEETING_RUN_SQL,
    "okr_review_runs": OKR_RUN_SQL,
}
RETIRED_TASK_TABLES = (*TASK_TYPES, *DOMAIN_RUN_COLUMNS)
SOURCE_COLUMNS = {
    "reply_tasks": (
        "id",
        "channel",
        "conversation_id",
        "conversation_title",
        "single_chat",
        "trigger_message_id",
        "trigger_create_time",
        "trigger_sender",
        "available_at",
        "force_new_decision",
        "oa_url",
        "manual_rerun_attempt_id",
        "status",
        "attempts",
        "locked_at",
        "error",
        "created_at",
        "updated_at",
        "execution_generation",
        "manual_rerun_revision_key",
        "recovery_code",
        "business_object_key",
        "input_version",
        "claimed_input_version",
        "current_input_id",
        "claimed_input_id",
    ),
    "reply_task_inputs": (
        "id",
        "reply_task_id",
        "channel",
        "conversation_id",
        "conversation_title",
        "single_chat",
        "trigger_message_id",
        "trigger_create_time",
        "trigger_sender",
        "trigger_text",
        "trigger_message_json",
        "oa_url",
        "business_object_key",
        "input_revision_key",
        "created_at",
        "input_provenance_json",
        "input_compacted",
    ),
    "work_summary_inputs": (
        "id",
        "source_type",
        "source_ref",
        "payload_json",
        "status",
        "attempts",
        "error",
        "created_at",
        "updated_at",
        "available_at",
        "source_created_at",
        "body_sha256",
        "body_bytes",
        "body_compacted",
    ),
    "meeting_alignment_jobs": (
        "id", "meeting_id", "title", "source_json", "participants_json",
        "ended_at", "eligible_at", "status", "attempts", "locked_at",
        "available_at", "error", "decision_json", "target_kind", "target_id",
        "target_title", "mentions_json", "final_message", "send_result_json",
        "created_at", "updated_at", "calendar_summary_status",
        "calendar_summary_result_json",
    ),
    "okr_review_requests": (
        "id", "conversation_id", "conversation_title", "trigger_message_id",
        "trigger_sender", "trigger_sender_user_id", "trigger_text", "period_label",
        "period_start", "period_end", "okr_source_json", "status", "error",
        "codex_session_id", "created_at", "updated_at",
    ),
    "weekly_okr_analysis_jobs": (
        "id", "week_end", "manager_user_id", "source_digest", "status", "lease_owner",
        "lease_expires_at", "error", "created_at", "finished_at", "updated_at",
    ),
}
# Frozen actual production source contract from the committed Phase 4 inventory.
# These are historical declarations, not target defaults or a runtime variant.
SOURCE_TASK_DECLARATIONS = {'reply_tasks': (('index',
                  'idx_reply_tasks_business_object_key',
                  'CREATE INDEX idx_reply_tasks_business_object_key on '
                  'reply_tasks(business_object_key)'),
                 ('index',
                  'idx_reply_tasks_channel_status_id',
                  'CREATE INDEX idx_reply_tasks_channel_status_id\n'
                  '                    on reply_tasks(channel, status, id)'),
                 ('index',
                  'idx_reply_tasks_status',
                  'CREATE INDEX idx_reply_tasks_status on reply_tasks(status, id)'),
                 ('table',
                  'reply_tasks',
                  'CREATE TABLE "reply_tasks" (\n'
                  '                    id integer primary key autoincrement,\n'
                  "                    channel text not null default 'dingtalk',\n"
                  '                    conversation_id text not null,\n'
                  '                    conversation_title text not null,\n'
                  '                    single_chat integer not null,\n'
                  '                    trigger_message_id text not null,\n'
                  '                    trigger_create_time text not null,\n'
                  '                    trigger_sender text not null,\n'
                  "                    available_at text not null default '',\n"
                  '                    force_new_decision integer not null default 0,\n'
                  "                    oa_url text not null default '',\n"
                  '                    manual_rerun_attempt_id integer not null default 0,\n'
                  "                    status text not null default 'pending',\n"
                  '                    attempts integer not null default 0,\n'
                  '                    locked_at text,\n'
                  "                    error text not null default '',\n"
                  '                    created_at text not null default current_timestamp,\n'
                  '                    updated_at text not null default current_timestamp, '
                  "execution_generation text not null default 'initial', "
                  "manual_rerun_revision_key text not null default '', recovery_code text not "
                  "null default '', business_object_key text not null default '', "
                  'input_version integer not null default 1, claimed_input_version integer not '
                  'null default 0, current_input_id integer references reply_task_inputs(id), '
                  'claimed_input_id integer references reply_task_inputs(id),\n'
                  '                    unique(channel, conversation_id, trigger_message_id)\n'
                  '                )')),
 'reply_task_inputs': (('index',
                        'idx_reply_task_inputs_task',
                        'CREATE INDEX idx_reply_task_inputs_task\n'
                        '                    on reply_task_inputs(reply_task_id, id)'),
                       ('table',
                        'reply_task_inputs',
                        'CREATE TABLE "reply_task_inputs" (\n'
                        '                    id integer primary key autoincrement,\n'
                        '                    reply_task_id integer not null,\n'
                        '                    channel text not null,\n'
                        '                    conversation_id text not null,\n'
                        '                    conversation_title text not null,\n'
                        '                    single_chat integer not null,\n'
                        '                    trigger_message_id text not null,\n'
                        '                    trigger_create_time text not null,\n'
                        '                    trigger_sender text not null,\n'
                        '                    trigger_text text not null,\n'
                        "                    trigger_message_json text not null default '{}',\n"
                        "                    oa_url text not null default '',\n"
                        '                    business_object_key text not null,\n'
                        "                    input_revision_key text not null default '',\n"
                        '                    created_at text not null default '
                        "current_timestamp, input_provenance_json text not null default '{}', "
                        'input_compacted integer not null default 0,\n'
                        '                    unique(\n'
                        '                        channel, conversation_id, '
                        'trigger_message_id,\n'
                        '                        input_revision_key\n'
                        '                    ),\n'
                        '                    foreign key(reply_task_id) references '
                        'reply_tasks(id)\n'
                        '                )')),
 'work_summary_inputs': (('index',
                          'idx_work_summary_inputs_status',
                          'CREATE INDEX idx_work_summary_inputs_status\n'
                          '                    on work_summary_inputs(status, id)'),
                         ('index',
                          'idx_work_summary_inputs_updated',
                          'CREATE INDEX idx_work_summary_inputs_updated\n'
                          '                    on work_summary_inputs(updated_at desc, id '
                          'desc)'),
                         ('table',
                          'work_summary_inputs',
                          'CREATE TABLE work_summary_inputs (\n'
                          '                    id integer primary key autoincrement,\n'
                          '                    source_type text not null,\n'
                          '                    source_ref text not null,\n'
                          '                    payload_json text not null,\n'
                          "                    status text not null default 'pending',\n"
                          '                    attempts integer not null default 0,\n'
                          "                    error text not null default '',\n"
                          '                    created_at text not null default '
                          'current_timestamp,\n'
                          '                    updated_at text not null default '
                          "current_timestamp, available_at text not null default '', "
                          "source_created_at text not null default '', body_sha256 text not "
                          "null default '', body_bytes integer not null default 0, "
                          'body_compacted integer not null default 0,\n'
                          '                    unique(source_type, source_ref)\n'
                          '                )')),
 'task_agent_runs': (('index',
                      'idx_task_agent_runs_active_input',
                      'CREATE UNIQUE INDEX idx_task_agent_runs_active_input on '
                      "task_agent_runs(summary_input_id) where status='running'"),
                     ('index',
                      'idx_task_agent_runs_input',
                      'CREATE INDEX idx_task_agent_runs_input\n'
                      '                    on task_agent_runs(summary_input_id, id)'),
                     ('table',
                      'task_agent_runs',
                      'CREATE TABLE task_agent_runs (\n'
                      '                    id integer primary key autoincrement,\n'
                      '                    summary_input_id integer not null,\n'
                      "                    codex_session_id text not null default '',\n"
                      "                    decision_json text not null default '{}',\n"
                      "                    audit_summary text not null default '',\n"
                      '                    memory_recall_used integer not null default 0,\n'
                      '                    created_at text not null default current_timestamp\n'
                      "                , status text not null default 'completed', error text "
                      "not null default '', finished_at text not null default '', updated_at "
                      "text not null default '', projection_json text not null default '{}')"))}

SOURCE_TASK_DECLARATIONS.update({
    "meeting_alignment_jobs": (
        ("index", "idx_meeting_alignment_jobs_claim", """CREATE INDEX idx_meeting_alignment_jobs_claim
                    on meeting_alignment_jobs(status, available_at, eligible_at, id)"""),
        ("table", "meeting_alignment_jobs", """CREATE TABLE meeting_alignment_jobs (
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
                , calendar_summary_status text not null default 'not_started', calendar_summary_result_json text not null default '{}')"""),
    ),
    "meeting_alignment_runs": (
        ("index", "idx_meeting_alignment_runs_active_job", "CREATE UNIQUE INDEX idx_meeting_alignment_runs_active_job on meeting_alignment_runs(job_id) where status='running'"),
        ("index", "idx_meeting_alignment_runs_created", """CREATE INDEX idx_meeting_alignment_runs_created
                    on meeting_alignment_runs(created_at, id)"""),
        ("index", "idx_meeting_alignment_runs_job", """CREATE INDEX idx_meeting_alignment_runs_job
                    on meeting_alignment_runs(job_id, id)"""),
        ("table", "meeting_alignment_runs", """CREATE TABLE meeting_alignment_runs (
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
                    created_at text not null default current_timestamp, finished_at text not null default '', updated_at text not null default '',
                    foreign key(job_id) references meeting_alignment_jobs(id)
                )"""),
    ),
    "okr_review_requests": (
        ("index", "idx_okr_review_requests_status", """CREATE INDEX idx_okr_review_requests_status
                    on okr_review_requests(status, id)"""),
        ("table", "okr_review_requests", """CREATE TABLE okr_review_requests (
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
                )"""),
    ),
    "okr_review_runs": (
        ("table", "okr_review_runs", """CREATE TABLE okr_review_runs (
                    id integer primary key autoincrement,
                    request_id integer not null,
                    codex_session_id text not null default '',
                    codex_transcript_start_line integer not null default 0,
                    codex_transcript_end_line integer not null default 0,
                    envelope_json text not null default '{}',
                    audit_tool_events_json text not null default '[]',
                    audit_summary text not null default '',
                    created_at text not null default current_timestamp
                )"""),
    ),
    "weekly_okr_analysis_jobs": (
        ("index", "idx_weekly_okr_analysis_jobs_identity", """CREATE UNIQUE INDEX idx_weekly_okr_analysis_jobs_identity
                    on weekly_okr_analysis_jobs(
                        week_end, manager_user_id, source_digest
                    )"""),
        ("table", "weekly_okr_analysis_jobs", """CREATE TABLE weekly_okr_analysis_jobs (
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
                )"""),
    ),
})


TASK_DEFAULT_SQL = {
    "reply_tasks": dict(
        channel="'dingtalk'",
        available_at="''",
        force_new_decision="0",
        oa_url="''",
        manual_rerun_attempt_id="0",
        status="'pending'",
        attempts="0",
        error="''",
        created_at="current_timestamp",
        updated_at="current_timestamp",
        execution_generation="'initial'",
        manual_rerun_revision_key="''",
        recovery_code="''",
        business_object_key="''",
        input_version="1",
        claimed_input_version="0",
    ),
    "reply_task_inputs": dict(
        trigger_message_json="'{}'",
        oa_url="''",
        input_revision_key="''",
        created_at="current_timestamp",
        input_provenance_json="'{}'",
        input_compacted="0",
    ),
    "work_summary_inputs": dict(
        status="'pending'",
        attempts="0",
        error="''",
        created_at="current_timestamp",
        updated_at="current_timestamp",
        available_at="''",
        source_created_at="''",
        body_sha256="''",
        body_bytes="0",
        body_compacted="0",
    ),
    "meeting_alignment_jobs": dict(
        title="''", source_json="'{}'", participants_json="'[]'", ended_at="''",
        eligible_at="''", status="'waiting'", attempts="0", available_at="''",
        error="''", decision_json="'{}'", target_kind="''", target_id="''",
        target_title="''", mentions_json="'[]'", final_message="''",
        send_result_json="'{}'", created_at="current_timestamp",
        updated_at="current_timestamp", calendar_summary_status="'not_started'",
        calendar_summary_result_json="'{}'",
    ),
    "okr_review_requests": dict(
        trigger_sender_user_id="''", okr_source_json="'{}'", status="'pending'",
        error="''", codex_session_id="''", created_at="current_timestamp",
        updated_at="current_timestamp",
    ),
    "weekly_okr_analysis_jobs": dict(
        status="'running'", lease_owner="''", lease_expires_at="''", error="''",
        created_at="current_timestamp", finished_at="''", updated_at="current_timestamp",
    ),
}
NULLABLE = {
    "reply_tasks": {"locked_at", "current_input_id", "claimed_input_id"},
    "meeting_alignment_jobs": {"locked_at"},
}
INTEGER_FIELDS = {
    "parent_id",
    "single_chat",
    "force_new_decision",
    "manual_rerun_attempt_id",
    "attempts",
    "input_version",
    "claimed_input_version",
    "current_input_id",
    "claimed_input_id",
    "input_compacted",
    "body_bytes",
    "body_compacted",
}
COLUMN_MAP = {"reply_task_id": "parent_id"}
PHYSICAL_COLUMNS = tuple(
    dict.fromkeys(
        COLUMN_MAP.get(field, field)
        for columns in SOURCE_COLUMNS.values()
        for field in columns
        if field != "id"
    )
)
TASK_TABLE_DDL = (
    "create table tasks (id integer primary key autoincrement, record_type text not null check(record_type in ('reply_tasks','reply_task_inputs','work_summary_inputs','meeting_alignment_jobs','okr_review_requests','weekly_okr_analysis_jobs')), legacy_key text,"
    + ",".join(
        field + (" integer" if field in INTEGER_FIELDS else " text")
        for field in PHYSICAL_COLUMNS
    )
    + ",foreign key(parent_id) references tasks(id),foreign key(current_input_id) references tasks(id),foreign key(claimed_input_id) references tasks(id),check(record_type<>'reply_tasks' or legacy_key is null or legacy_key=cast(id as text)),"
    + ",".join(
        "check(record_type<>'"
        + record_type
        + "' or ("
        + " and ".join(
            COLUMN_MAP.get(field, field) + " is not null"
            for field in fields
            if field != "id" and field not in NULLABLE.get(record_type, ())
        )
        + "))"
        for record_type, fields in SOURCE_COLUMNS.items()
    )
    + ","
    + ",".join(
        "check(record_type<>'"
        + record_type
        + "' or ("
        + " and ".join(
            field + " is null"
            for field in PHYSICAL_COLUMNS
            if field not in {COLUMN_MAP.get(column, column) for column in columns}
        )
        + "))"
        for record_type, columns in SOURCE_COLUMNS.items()
    )
    + ",check(record_type<>'weekly_okr_analysis_jobs' or status in ('running','completed','failed')))"
)

TASK_INDEX_DDL = (
    "create unique index idx_tasks_public_id on tasks(cast(legacy_key as integer),record_type)",
    "create unique index idx_reply_tasks_identity on tasks(channel,conversation_id,trigger_message_id) where record_type='reply_tasks'",
    "create index idx_reply_tasks_status on tasks(status,cast(legacy_key as integer)) where record_type='reply_tasks'",
    "create index idx_reply_tasks_channel_status_id on tasks(channel,status,cast(legacy_key as integer)) where record_type='reply_tasks'",
    "create index idx_reply_tasks_business_object_key on tasks(business_object_key) where record_type='reply_tasks'",
    "create unique index idx_reply_task_inputs_identity on tasks(channel,conversation_id,trigger_message_id,input_revision_key) where record_type='reply_task_inputs'",
    "create index idx_reply_task_inputs_task on tasks(parent_id,cast(legacy_key as integer)) where record_type='reply_task_inputs'",
    "create unique index idx_work_summary_inputs_identity on tasks(source_type,source_ref) where record_type='work_summary_inputs'",
    "create index idx_work_summary_inputs_status on tasks(status,cast(legacy_key as integer)) where record_type='work_summary_inputs'",
    "create index idx_work_summary_inputs_updated on tasks(updated_at desc,cast(legacy_key as integer) desc) where record_type='work_summary_inputs'",
    "create unique index idx_meeting_alignment_jobs_identity on tasks(meeting_id) where record_type='meeting_alignment_jobs'",
    "create index idx_meeting_alignment_jobs_claim on tasks(status,available_at,eligible_at,cast(legacy_key as integer)) where record_type='meeting_alignment_jobs'",
    "create unique index idx_okr_review_requests_identity on tasks(conversation_id,trigger_message_id) where record_type='okr_review_requests'",
    "create index idx_okr_review_requests_status on tasks(status,cast(legacy_key as integer)) where record_type='okr_review_requests'",
    "create unique index idx_weekly_okr_analysis_jobs_identity on tasks(week_end,manager_user_id,source_digest) where record_type='weekly_okr_analysis_jobs'",
)
TASK_TRIGGER_DDL = (
    "create trigger trg_tasks_record_type_immutable before update of record_type on tasks when new.record_type<>old.record_type begin select raise(abort,'task record type is immutable'); end",
    "create trigger trg_tasks_public_key after insert on tasks when new.legacy_key is null begin update tasks set legacy_key=cast(new.id as text) where id=new.id; end",
    *tuple(
        f"create trigger trg_tasks_{column}_{suffix} before {event} on tasks when new.{column} is not null and exists(select 1 from tasks where id=new.{column} and record_type<>'{parent_type}') begin select raise(abort,'task parent type mismatch'); end"
        for column, parent_type in (
            ("parent_id", "reply_tasks"),
            ("current_input_id", "reply_task_inputs"),
            ("claimed_input_id", "reply_task_inputs"),
        )
        for event, suffix in (("insert", "insert"), (f"update of {column}", "update"))
    ),
)


# 4C extends this same container. The 11.3 declaration above remains the
# explicit historical boundary used by the preceding publication and inverse.
WORKBENCH_TASK_COLUMNS = {
    "workbench_tasks": ("id", "title", "runtime_kind", "provider_session_ref",
        "archived_at", "created_at", "updated_at"),
    "workbench_turns": ("id", "task_id", "client_request_id", "task_sequence",
        "user_text", "status", "stop_requested", "final_text", "error_code",
        "error_detail", "resume_context", "lease_owner", "lease_expires_at",
        "execution_run_id", "runtime_quiesced_run_id", "started_at",
        "completed_at", "created_at", "updated_at"),
}
TEXT_TASK_TYPES = tuple(WORKBENCH_TASK_COLUMNS)
WORKBENCH_TASK_COLUMN_MAP = {"task_id": "parent_id"}
WORKBENCH_TASK_FIELDS = {
    kind: {WORKBENCH_TASK_COLUMN_MAP.get(c, c) for c in columns
           if c not in ("id", "final_text")}
    for kind, columns in WORKBENCH_TASK_COLUMNS.items()
}
_WORKBENCH_NEW_FIELDS = tuple(dict.fromkeys(
    WORKBENCH_TASK_COLUMN_MAP.get(c, c)
    for columns in WORKBENCH_TASK_COLUMNS.values() for c in columns
    if c not in ("id", "final_text")
    and WORKBENCH_TASK_COLUMN_MAP.get(c, c) not in PHYSICAL_COLUMNS
))
WORKBENCH_TASK_TABLE_DDL = TASK_TABLE_DDL.replace(
    "'weekly_okr_analysis_jobs'))",
    "'weekly_okr_analysis_jobs','workbench_tasks','workbench_turns'))", 1,
).replace(
    ",foreign key(parent_id)",
    "," + ",".join(c + (" integer" if c in ("task_sequence", "stop_requested") else " text")
        for c in _WORKBENCH_NEW_FIELDS) + ",foreign key(parent_id)", 1,
).removesuffix(")") + "," + ",".join(
    "check(record_type<>'" + kind + "' or ("
    + " and ".join(c + " is not null" for c in sorted(fields)) + "))"
    for kind, fields in WORKBENCH_TASK_FIELDS.items()
) + "," + ",".join(
    "check(record_type<>'" + kind + "' or ("
    + " and ".join(c + " is null" for c in sorted(
        (set(PHYSICAL_COLUMNS) | set(_WORKBENCH_NEW_FIELDS)) - fields)) + "))"
    for kind, fields in WORKBENCH_TASK_FIELDS.items()
) + ",check(record_type in ('workbench_tasks','workbench_turns') or (" + " and ".join(
    c + " is null" for c in _WORKBENCH_NEW_FIELDS
) + "))" + ",check(record_type<>'workbench_turns' or (task_sequence>0 and stop_requested in (0,1) and status in ('queued','running','waiting_confirmation','completed','stopped','failed'))))"
WORKBENCH_TASK_INDEX_DDL = (
    TASK_INDEX_DDL[0] + " where record_type not in ('workbench_tasks','workbench_turns')",
    "create unique index idx_tasks_text_public_id on tasks(legacy_key,record_type) where record_type in ('workbench_tasks','workbench_turns')",
    *TASK_INDEX_DDL[1:],
    "create index idx_workbench_tasks_updated_id on tasks(updated_at,legacy_key) where record_type='workbench_tasks'",
    "create unique index idx_workbench_turns_client_request on tasks(client_request_id) where record_type='workbench_turns'",
    "create unique index idx_workbench_one_active_turn on tasks(parent_id) where record_type='workbench_turns' and status in ('queued','running','waiting_confirmation')",
    "create index idx_workbench_turns_queue on tasks(status,created_at,legacy_key) where record_type='workbench_turns'",
    "create index idx_workbench_turns_recovery on tasks(status,lease_expires_at) where record_type='workbench_turns'",
    "create index idx_workbench_turns_task_created_id on tasks(parent_id,created_at,legacy_key) where record_type='workbench_turns'",
    "create unique index idx_workbench_turns_task_sequence on tasks(parent_id,task_sequence desc) where record_type='workbench_turns'",
)
WORKBENCH_TASK_TRIGGER_DDL = tuple(
    sql.replace("when new.legacy_key is null", "when new.legacy_key is null and new.record_type not in ('workbench_tasks','workbench_turns')")
       .replace("record_type<>'reply_tasks')", "record_type<>case when new.record_type='workbench_turns' then 'workbench_tasks' else 'reply_tasks' end)")
    for sql in TASK_TRIGGER_DDL
)
TASK_DEFAULT_SQL.update({
    "workbench_tasks": dict(provider_session_ref="''", archived_at="''",
        created_at="current_timestamp", updated_at="current_timestamp"),
    "workbench_turns": dict(stop_requested="0", error_code="''", error_detail="''",
        resume_context="''", lease_owner="''", lease_expires_at="''",
        execution_run_id="''", runtime_quiesced_run_id="''", started_at="''",
        completed_at="''", created_at="current_timestamp", updated_at="current_timestamp"),
})


def task_id_expression(record_type: str, public_expression: str) -> str:
    if record_type not in (*TASK_TYPES, *TEXT_TASK_TYPES):
        raise ValueError("unknown task record type")
    identity = "legacy_key" if record_type in TEXT_TASK_TYPES else "cast(legacy_key as integer)"
    return f"(select id from tasks where record_type='{record_type}' and {identity}={public_expression})"


def task_physical_id(
    db: sqlite3.Connection, record_type: str, public_id: int | str | bytes | None
) -> int | None:
    if public_id is None:
        return None
    row = db.execute(
        "select " + task_id_expression(record_type, "?"), (public_id,)
    ).fetchone()
    if row[0] is None:
        raise ValueError(f"missing {record_type} public ID {public_id}")
    return int(row[0])


def _projection(record_type: str) -> str:
    fields = []
    for field in (WORKBENCH_TASK_COLUMNS[record_type] if record_type in TEXT_TASK_TYPES else SOURCE_COLUMNS[record_type]):
        if field == "id":
            value = "t.legacy_key" if record_type in TEXT_TASK_TYPES else "cast(t.legacy_key as integer)"
        elif record_type == "workbench_turns" and field == "task_id":
            value = "(select p.legacy_key from tasks p where p.id=t.parent_id)"
        elif record_type == "workbench_turns" and field == "final_text":
            value = "''"
        elif field in ("current_input_id", "claimed_input_id"):
            value = f"(select cast(i.legacy_key as integer) from tasks i where i.id=t.{field})"
        else:
            value = "t." + COLUMN_MAP.get(field, field)
        fields.append(value + " as " + field)
    predicate = f"t.record_type='{record_type}'"
    if record_type not in TEXT_TASK_TYPES:
        # SQLite cannot infer this partial-index predicate from one type equality.
        predicate += " and t.record_type not in ('workbench_tasks','workbench_turns')"
    return f"select {','.join(fields)} from tasks t where {predicate}"


TASK_PROJECTIONS = {record_type: _projection(record_type) for record_type in TASK_TYPES}
REPLY_TASK_SQL = TASK_PROJECTIONS["reply_tasks"]
REPLY_TASK_INPUT_SQL = TASK_PROJECTIONS["reply_task_inputs"]
WORK_SUMMARY_INPUT_SQL = TASK_PROJECTIONS["work_summary_inputs"]
MEETING_JOB_SQL = TASK_PROJECTIONS["meeting_alignment_jobs"]
OKR_REQUEST_SQL = TASK_PROJECTIONS["okr_review_requests"]
WEEKLY_JOB_SQL = TASK_PROJECTIONS["weekly_okr_analysis_jobs"]
WORKBENCH_TASK_SQL = _projection("workbench_tasks")
WORKBENCH_TURN_SQL = _projection("workbench_turns")


def insert_task(
    db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False
) -> sqlite3.Cursor:
    """Values are physical scalars; omitted defaults apply only to this type."""
    if record_type not in TEXT_TASK_TYPES and values.get("legacy_key") is not None:
        values = values | {"legacy_key": str(int(values["legacy_key"]))}
    defaults = {
        field: sql
        for field, sql in TASK_DEFAULT_SQL[record_type].items()
        if field not in values
    }
    columns = ["record_type", *values, *defaults]
    expressions = ["?"] * (1 + len(values)) + list(defaults.values())
    verb = "insert or ignore" if ignore else "insert"
    return db.execute(
        f"{verb} into tasks ({','.join(columns)}) values ({','.join(expressions)})",
        (record_type, *values.values()),
    )


def create_task_schema(db: sqlite3.Connection, *, include_workbench: bool = False) -> None:
    db.execute(
        (WORKBENCH_TASK_TABLE_DDL if include_workbench else TASK_TABLE_DDL).replace(
            "create table tasks", "create table if not exists tasks", 1
        )
    )
    for sql in ((*WORKBENCH_TASK_INDEX_DDL, *WORKBENCH_TASK_TRIGGER_DDL) if include_workbench else (*TASK_INDEX_DDL, *TASK_TRIGGER_DDL)):
        kind = (
            "unique index"
            if sql.startswith("create unique")
            else "index"
            if sql.startswith("create index")
            else "trigger"
        )
        db.execute(
            sql.replace("create " + kind, "create " + kind + " if not exists", 1)
        )


@lru_cache(maxsize=2)
def _declared_task_objects(include_workbench: bool = False) -> dict[str, str]:
    """Read SQLite's canonical declarations without changing SQL literals."""
    with closing(sqlite3.connect(":memory:")) as reference:
        create_task_schema(reference, include_workbench=include_workbench)
        return dict(
            reference.execute(
                "select name,sql from sqlite_master where tbl_name='tasks' and sql is not null"
            )
        )


def task_schema_is_current(
    db: sqlite3.Connection, *, allow_missing_objects: bool = False, include_workbench: bool = False
) -> bool:
    if db.execute(
        "select 1 from sqlite_master where type in ('table','view') and name in ("
        + ",".join("?" for _ in RETIRED_TASK_TABLES) + ")",
        RETIRED_TASK_TABLES,
    ).fetchone():
        return False
    if include_workbench and db.execute("select 1 from sqlite_master where name in ('workbench_tasks','workbench_turns')").fetchone():
        return False
    objects = {
        row[0]: row[1]
        for row in db.execute(
            "select name,sql from sqlite_master where tbl_name='tasks'"
        )
    }

    expected = _declared_task_objects(include_workbench)
    if (objects.get("tasks") or "").strip() != expected["tasks"].strip():
        return False
    for name, sql in expected.items():
        if name == "tasks":
            continue
        if name not in objects:
            if allow_missing_objects:
                continue
            return False
        if objects[name].strip() != sql.strip():
            return False
    return True


def _capture_table(db, table, sequences):
    return {
        "objects": _schema_objects(db, table),
        "columns": [row[1] for row in db.execute(f'pragma table_info("{table}")')],
        "foreign_keys": [
            tuple(row) for row in db.execute(f'pragma foreign_key_list("{table}")')
        ],
        "sequence": sequences.get(table),
    }


def capture_task_schema(db: sqlite3.Connection) -> dict:
    tables = {
        row[0]
        for row in db.execute("select name from sqlite_master where type='table'")
    }
    children = sorted(
        table
        for table in tables
        if table not in {*RETIRED_TASK_TABLES, "runs"}
        and any(
            row[2] in TASK_TYPES
            for row in db.execute(f'pragma foreign_key_list("{table}")')
        )
    )
    sequences = (
        dict(db.execute("select name,seq from sqlite_sequence"))
        if "sqlite_sequence" in tables
        else {}
    )
    scoped = [
        table for table in (*RETIRED_TASK_TABLES, "runs", *children) if table in tables
    ]
    high = max(
        [0]
        + [
            max(
                int(sequences.get(table) or 0),
                db.execute(f'select coalesce(max(id),0) from "{table}"').fetchone()[0],
            )
            for table in TASK_TYPES
            if table in tables
        ]
    )
    run_high = max(
        [0]
        + [
            max(
                int(sequences.get(table) or 0),
                db.execute(f'select coalesce(max(id),0) from "{table}"').fetchone()[0],
            )
            for table in ("runs", *DOMAIN_RUN_COLUMNS)
            if table in tables
        ]
    )
    missing_meeting_parents = (
        [row[0] for row in db.execute("""select distinct r.job_id
            from meeting_alignment_runs r
            left join meeting_alignment_jobs p on p.id=r.job_id
            where p.id is null order by r.job_id""")]
        if {"meeting_alignment_jobs", "meeting_alignment_runs"} <= tables else []
    )
    return {
        "tables": {table: _capture_table(db, table, sequences) for table in scoped},
        "children": children,
        "missing_meeting_parents": missing_meeting_parents,
        "allocator": {
            "source_high_water": high,
            "migration_high_water": high
            + sum(
                db.execute(f'select count(*) from "{table}"').fetchone()[0]
                for table in TASK_TYPES[1:]
                if table in tables
            ) + len(missing_meeting_parents),
            "run_source_high_water": run_high,
            "run_migration_high_water": run_high
            + sum(
                db.execute(f'select count(*) from "{table}"').fetchone()[0]
                for table in DOMAIN_RUN_COLUMNS if table in tables
            ),
        },
    }


def _replace_task_parents(sql):
    for table in TASK_TYPES:
        sql = re.sub(
            r'(?i)(references\s+)["`\[]?' + table + r'["`\]]?(\s*\()',
            lambda match: match[1] + "tasks" + match[2],
            sql,
        )
    return sql


def _rebuild(db, table, schema, values, *, forward=False, sequence=None):
    db.execute(f'drop table "{table}"')
    sql = next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table")
    db.execute(_replace_task_parents(sql) if forward else sql)
    names = ",".join('"' + column + '"' for column in schema["columns"])
    db.executemany(
        f'insert into "{table}" ({names}) values ({",".join("?" for _ in schema["columns"])})',
        values,
    )
    for obj in schema["objects"]:
        if obj["type"] != "table":
            db.execute(obj["sql"])
    if sequence is not None:
        _set_sequence(db, table, sequence)


def install_task_child_guards(db: sqlite3.Connection) -> None:
    for (table,) in db.execute(
        "select name from sqlite_master where type='table' and name not in ('tasks','runs')"
    ).fetchall():
        for fk in db.execute(f'pragma foreign_key_list("{table}")'):
            if fk[2] != "tasks":
                continue
            column = fk[3]
            parent_type = (
                "meeting_alignment_jobs"
                if (table, column) in {
                    ("meeting_memory_write_events", "meeting_job_id"),
                    ("meeting_alignment_delivery_claims", "job_id"),
                }
                else "workbench_tasks" if table == "workbench_attachments"
                else "workbench_turns" if table in ("workbench_artifacts", "workbench_confirmations")
                else "reply_tasks"
            )
            parent_label = {
                "meeting_alignment_jobs": "meeting job",
                "reply_tasks": "reply task",
                "workbench_tasks": "workbench task",
                "workbench_turns": "workbench turn",
            }[parent_type]
            for event, suffix in (
                ("insert", "insert"),
                (f'update of "{column}"', "update"),
            ):
                db.execute(
                    f'create trigger if not exists trg_tasks_child_{table}_{column}_{suffix} before {event} on "{table}" when new."{column}" is not null and exists(select 1 from tasks where id=new."{column}" and record_type<>\'{parent_type}\') begin select raise(abort,\'task parent must be a {parent_label}\'); end'
                )


def validate_reply_input_references(db: sqlite3.Connection) -> None:
    if db.execute("""select 1 from tasks parent
        left join tasks current on current.id=parent.current_input_id
        left join tasks claimed on claimed.id=parent.claimed_input_id
        where parent.record_type='reply_tasks' and (
            current.id is null or current.record_type<>'reply_task_inputs' or current.parent_id<>parent.id
            or (parent.claimed_input_id is not null and (claimed.id is null or claimed.record_type<>'reply_task_inputs' or claimed.parent_id<>parent.id))) limit 1""").fetchone():
        raise ValueError("reply input ownership mismatch")


def validate_meeting_run_references(db: sqlite3.Connection) -> None:
    """Typed parents and reserved absent parents are distinct source relations."""
    if db.execute("""select 1 from runs r left join tasks p on p.id=r.task_id
        where r.record_type='meeting_alignment_runs' and (
            (p.id is not null and (p.record_type<>'meeting_alignment_jobs' or r.job_id is not null))
            or (p.id is null and r.job_id is null)) limit 1""").fetchone():
        raise ValueError("meeting run parent mapping mismatch")
    for group, target in (("job_id", "task_id"), ("task_id", "job_id")):
        if db.execute(
            f"select 1 from runs where record_type='meeting_alignment_runs' and job_id is not null group by {group} having count(distinct {target})<>1 limit 1"
        ).fetchone():
            raise ValueError("meeting orphan parent mapping mismatch")
    if db.execute("""select 1 from runs r where r.record_type='meeting_alignment_runs'
        and r.job_id is not null and r.task_id>coalesce(
            (select seq from sqlite_sequence where name='tasks'),0) limit 1""").fetchone():
        raise ValueError("meeting orphan parent allocator is not reserved")


def _validate_source_declarations(db: sqlite3.Connection) -> None:
    """Only the captured production 11.2 declarations have this exact inverse."""
    for table, expected in SOURCE_TASK_DECLARATIONS.items():
        actual = tuple(
            (item["type"], item["name"], item["sql"].strip())
            for item in _schema_objects(db, table)
        )
        if actual != expected:
            raise ValueError(f"unsupported source declaration for {table}")


def migrate_task_schema(db: sqlite3.Connection) -> dict:
    if not db.execute(
        "select 1 from sqlite_master where type='table' and name='service_state'"
    ).fetchone():
        raise ValueError("unsupported task source version: missing Store marker")
    version = db.execute(
        "select value from service_state where key='store_schema_version'"
    ).fetchone()
    if version is None or version[0] != "2026-10-11.2":
        raise ValueError("unsupported task source version: expected 2026-10-11.2")
    state = capture_task_schema(db)
    if db.execute("select 1 from sqlite_master where name='tasks'").fetchone():
        raise ValueError("unsupported 11.2 source schema: tasks already exists")
    for table, columns in {
        **SOURCE_COLUMNS,
        **DOMAIN_RUN_COLUMNS,
    }.items():
        if table not in state["tables"] or set(
            state["tables"][table]["columns"]
        ) != set(columns):
            raise ValueError(f"unsupported 11.2 source schema for {table}")
    _validate_source_declarations(db)
    if not run_schema_is_current(db):
        raise ValueError("unsupported 11.2 core run schema")
    for table, retired in DOMAIN_RUN_RETIRED.items():
        for column, empty in retired.items():
            if db.execute(
                f'select 1 from "{table}" where "{column}" is null or "{column}"<>? limit 1',
                (empty,),
            ).fetchone():
                raise ValueError(f"{table}.{column} is not an empty retired cache")
    if db.execute("""select 1 from reply_tasks p left join reply_task_inputs c on c.id=p.current_input_id
        left join reply_task_inputs x on x.id=p.claimed_input_id where c.id is null or c.reply_task_id<>p.id
        or (p.claimed_input_id is not null and (x.id is null or x.reply_task_id<>p.id)) limit 1""").fetchone():
        raise ValueError("reply input ownership mismatch")
    mapping = {}
    next_id = state["allocator"]["source_high_water"]
    for table in TASK_TYPES:
        for (key,) in db.execute(f'select id from "{table}" order by id'):
            if table == "reply_tasks":
                physical = key
            else:
                next_id += 1
                physical = next_id
            mapping[table, key] = physical
    # Reserve one absent physical parent per actual missing public key. No task
    # row is manufactured; the sequence prevents new automatic IDs filling it.
    for key in state["missing_meeting_parents"]:
        next_id += 1
        mapping["meeting_alignment_jobs", key] = next_id
    for table in RETIRED_TASK_TABLES:
        _drop_explicit_objects(db, state["tables"][table]["objects"])
    create_task_schema(db)
    counts = {}
    for table in TASK_TYPES:
        source_rows = db.execute(f'select * from "{table}" order by id').fetchall()
        counts[table] = len(source_rows)
        for row in source_rows:
            source = dict(zip(state["tables"][table]["columns"], row, strict=True))
            values = {
                "id": mapping[table, source["id"]],
                "legacy_key": str(source["id"]),
            }
            for column, value in source.items():
                if column == "id":
                    continue
                if (
                    column in ("current_input_id", "claimed_input_id")
                    and value is not None
                ):
                    value = mapping["reply_task_inputs", value]
                elif column == "reply_task_id":
                    value = mapping["reply_tasks", value]
                values[COLUMN_MAP.get(column, column)] = value
            insert_task(db, table, values)
    old_runs = db.execute("select * from runs").fetchall()
    db.execute("drop table runs")
    create_run_schema(db, include_task_runs=True)
    run_columns = state["tables"]["runs"]["columns"]
    db.executemany(
        f"insert into runs ({','.join(run_columns)}) values ({','.join('?' for _ in run_columns)})",
        old_runs,
    )
    next_run = state["allocator"]["run_source_high_water"]
    run_column_map = {
        "codex_session_id": "session_id",
        "codex_transcript_start_line": "transcript_start",
        "codex_transcript_end_line": "transcript_end",
    }
    for table in DOMAIN_RUN_COLUMNS:
        source_rows = db.execute(f'select * from "{table}" order by id').fetchall()
        counts[table] = len(source_rows)
        for row in source_rows:
            source = dict(zip(state["tables"][table]["columns"], row, strict=True))
            next_run += 1
            values = {"id": next_run, "legacy_key": str(source.pop("id"))}
            if table == "meeting_alignment_runs":
                key = source.pop("job_id")
                values["task_id"] = mapping["meeting_alignment_jobs", key]
                if key in state["missing_meeting_parents"]:
                    values["job_id"] = key
            values.update({
                run_column_map.get(column, column): value
                for column, value in source.items()
                if column not in DOMAIN_RUN_RETIRED[table]
            })
            insert_run(db, table, values)
    for table in state["children"]:
        schema = state["tables"][table]
        child_rows = []
        for row in db.execute(f'select * from "{table}"').fetchall():
            source = dict(zip(schema["columns"], row, strict=True))
            for fk in schema["foreign_keys"]:
                if fk[2] in TASK_TYPES and source[fk[3]] is not None:
                    source[fk[3]] = mapping[fk[2], source[fk[3]]]
            child_rows.append(tuple(source[column] for column in schema["columns"]))
        _rebuild(
            db, table, schema, child_rows, forward=True, sequence=schema["sequence"]
        )
    for table in RETIRED_TASK_TABLES:
        db.execute(f'drop table "{table}"')
    _set_sequence(db, "tasks", next_id)
    _set_sequence(db, "runs", next_run)
    install_task_child_guards(db)
    validate_reply_input_references(db)
    validate_meeting_run_references(db)
    return {"counts": counts, "children": state["children"]}


def restore_task_schema(db: sqlite3.Connection, state: dict) -> dict:
    validate_meeting_run_references(db)
    sequences = dict(db.execute("select name,seq from sqlite_sequence"))
    task_reserve = (
        sequences.get("tasks", 0)
        if sequences.get("tasks", 0) > state["allocator"]["migration_high_water"]
        else 0
    )
    run_reserve = (
        sequences.get("runs", 0)
        if sequences.get("runs", 0) > state["allocator"]["run_migration_high_water"]
        else 0
    )
    mapping = {
        row[0]: int(row[1]) for row in db.execute("select id,legacy_key from tasks")
    }
    saved = {
        table: db.execute(projection).fetchall()
        for table, projection in TASK_PROJECTIONS.items()
    }
    saved.update({table: db.execute(projection).fetchall()
                  for table, projection in DOMAIN_RUN_PROJECTIONS.items()})
    saved["runs"] = db.execute(
        "select "
        + ",".join(state["tables"]["runs"]["columns"])
        + " from runs where record_type not in ("
        + ",".join("?" for _ in DOMAIN_RUN_COLUMNS) + ")",
        tuple(DOMAIN_RUN_COLUMNS),
    ).fetchall()
    _drop_explicit_objects(db, _schema_objects(db, "tasks"))
    _drop_explicit_objects(db, _schema_objects(db, "runs"))
    for table in RETIRED_TASK_TABLES:
        schema = state["tables"][table]
        db.execute(
            next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table")
        )
        projection_columns = (
            DOMAIN_RUN_COLUMNS[table]
            if table in DOMAIN_RUN_COLUMNS
            else SOURCE_COLUMNS[table]
        )
        values = [
            tuple(
                dict(zip(projection_columns, row, strict=True))[column]
                for column in schema["columns"]
            )
            for row in saved[table]
        ]
        names = ",".join('"' + column + '"' for column in schema["columns"])
        db.executemany(
            f'insert into "{table}" ({names}) values ({",".join("?" for _ in schema["columns"])})',
            values,
        )
        for obj in schema["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
        reserve = run_reserve if table in DOMAIN_RUN_COLUMNS else task_reserve
        if schema["sequence"] is not None or reserve:
            maximum = db.execute(
                f'select coalesce(max(id),0) from "{table}"'
            ).fetchone()[0]
            _set_sequence(db, table, max(schema["sequence"] or 0, maximum, reserve))
    for table in state["children"]:
        schema = state["tables"][table]
        child_rows = []
        for row in db.execute(f'select * from "{table}"').fetchall():
            source = dict(zip(schema["columns"], row, strict=True))
            for fk in schema["foreign_keys"]:
                if fk[2] in TASK_TYPES and source[fk[3]] is not None:
                    source[fk[3]] = mapping[source[fk[3]]]
            child_rows.append(tuple(source[column] for column in schema["columns"]))
        sequence = db.execute(
            "select seq from sqlite_sequence where name=?", (table,)
        ).fetchone()
        _rebuild(
            db, table, schema, child_rows, sequence=sequence[0] if sequence else None
        )
    run_schema = state["tables"]["runs"]
    maximum = max(
        [0] + [row[run_schema["columns"].index("id")] for row in saved["runs"]]
    )
    run_sequence = (
        max(run_schema["sequence"] or 0, maximum, run_reserve)
        if run_schema["sequence"] is not None or maximum or run_reserve
        else None
    )
    _rebuild(db, "runs", run_schema, saved["runs"], sequence=run_sequence)
    db.execute("drop table tasks")
    return {
        "counts": {table: len(rows) for table, rows in saved.items()},
        "children": state["children"],
    }
