"""Physical core run storage and exact, paired source-schema conversion.

Public numeric IDs remain scoped by record_type. Native trajectories and adopted
business results stay in their existing sources. The caller owns the transaction
and disables foreign keys while rebuilding declared FK children.
"""

from contextlib import closing
from functools import lru_cache
import re
import sqlite3

RUN_TYPES = ("agent_runs", "agent_runtime_attempts", "agent_run_state_events")
RETIRED_RUN_TABLES = (*RUN_TYPES, "agent_run_events")
COLUMN_MAP = {
    "agent_runs": {
        "reply_task_id": "task_id",
        "parent_agent_run_id": "parent_id",
        "codex_session_id": "session_id",
        "transcript_start_line": "transcript_start",
        "transcript_end_line": "transcript_end",
    },
    "agent_runtime_attempts": {"agent_run_id": "parent_id"},
    "agent_run_state_events": {"agent_run_id": "parent_id"},
}
SOURCE_COLUMNS = {
    "agent_runs": (
        "id",
        "reply_task_id",
        "execution_generation",
        "role",
        "proposal_revision",
        "turn_attempt",
        "parent_agent_run_id",
        "operation_id",
        "status",
        "codex_session_id",
        "transcript_start_line",
        "transcript_end_line",
        "final_result_json",
        "structured_error_json",
        "lease_owner",
        "lease_expires_at",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    ),
    "agent_runtime_attempts": (
        "id",
        "agent_run_id",
        "workload_kind",
        "workload_key",
        "attempt_number",
        "route_name",
        "runtime_kind",
        "credential_mode",
        "model",
        "session_mode",
        "source_session_id",
        "attempt_purpose",
        "validation_retry_policy_id",
        "validation_result_schema_id",
        "session_id",
        "status",
        "failure_class",
        "failure_code",
        "failover_permitted",
        "transcript_reference",
        "transcript_start",
        "transcript_end",
        "first_effect_started_at",
        "lease_owner",
        "lease_expires_at",
        "result_schema_id",
        "result_envelope_json",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ),
    "agent_run_state_events": (
        "id",
        "agent_run_id",
        "phase",
        "structured_error_json",
        "created_at",
    ),
}
# Frozen from the approved 11.1 production inventory. Capture keeps source DDL
# for inverse restoration; it does not authorize arbitrary forward declarations.
SOURCE_RUN_DECLARATIONS = {
    "agent_runs": (
        (
            "index",
            "idx_agent_runs_status",
            """CREATE INDEX idx_agent_runs_status
                    on agent_runs(status, updated_at)""",
        ),
        (
            "table",
            "agent_runs",
            """CREATE TABLE "agent_runs" (
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
                            'pending', 'running', 'completed', 'failed'
                        )),
                    codex_session_id text not null default '',
                    transcript_start_line integer not null default 0,
                    transcript_end_line integer not null default 0,
                    final_result_json text not null default '',
                    structured_error_json text not null default '',
                    lease_owner text not null default '',
                    lease_expires_at text not null default '',
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
                )""",
        ),
    ),
    "agent_runtime_attempts": (
        (
            "index",
            "idx_agent_runtime_attempts_run",
            """CREATE INDEX idx_agent_runtime_attempts_run
            on agent_runtime_attempts(agent_run_id, attempt_number)
            where agent_run_id is not null""",
        ),
        (
            "index",
            "idx_runtime_attempt_active_lease",
            """CREATE INDEX idx_runtime_attempt_active_lease
            on agent_runtime_attempts(status, lease_expires_at)
            where agent_run_id is null and status in ('starting', 'running')""",
        ),
        (
            "index",
            "idx_runtime_attempt_active_route",
            """CREATE UNIQUE INDEX idx_runtime_attempt_active_route
                    on agent_runtime_attempts(workload_kind, workload_key, route_name)
                    where status in ('starting', 'running')""",
        ),
        (
            "table",
            "agent_runtime_attempts",
            """CREATE TABLE agent_runtime_attempts (
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
                )""",
        ),
        (
            "trigger",
            "trg_runtime_attempt_generalized_lease_insert",
            """CREATE TRIGGER trg_runtime_attempt_generalized_lease_insert
            before insert on agent_runtime_attempts
            when new.agent_run_id is null
              and new.status in ('starting', 'running')
              and (trim(new.lease_owner)='' or trim(new.lease_expires_at)='')
            begin
                select raise(abort, 'active generalized attempt requires lease');
            end""",
        ),
        (
            "trigger",
            "trg_runtime_attempt_generalized_lease_update",
            """CREATE TRIGGER trg_runtime_attempt_generalized_lease_update
            before update of status, lease_owner, lease_expires_at
            on agent_runtime_attempts
            when new.agent_run_id is null
              and new.status in ('starting', 'running')
              and (trim(new.lease_owner)='' or trim(new.lease_expires_at)='')
            begin
                select raise(abort, 'active generalized attempt requires lease');
            end""",
        ),
        (
            "trigger",
            "trg_runtime_attempt_lineage_immutable",
            """CREATE TRIGGER trg_runtime_attempt_lineage_immutable
            before update of attempt_purpose, validation_retry_policy_id,
                             validation_result_schema_id
            on agent_runtime_attempts
            when new.attempt_purpose<>old.attempt_purpose
              or new.validation_retry_policy_id<>old.validation_retry_policy_id
              or new.validation_result_schema_id<>old.validation_result_schema_id
            begin
                select raise(abort, 'runtime attempt correction lineage is immutable');
            end""",
        ),
        (
            "trigger",
            "trg_runtime_attempt_lineage_insert",
            """CREATE TRIGGER trg_runtime_attempt_lineage_insert
            before insert on agent_runtime_attempts
            when\x20
            new.attempt_purpose not in ('normal', 'result_validation_correction')
            or (new.attempt_purpose='normal' and
                (new.validation_retry_policy_id<>''
                 or new.validation_result_schema_id<>''))
            or (new.attempt_purpose='result_validation_correction' and
                (trim(new.validation_retry_policy_id)=''
                 or trim(new.validation_result_schema_id)=''))
       \x20
            begin
                select raise(abort, 'invalid runtime attempt correction lineage');
            end""",
        ),
        (
            "trigger",
            "trg_runtime_attempt_lineage_update",
            """CREATE TRIGGER trg_runtime_attempt_lineage_update
            before update of attempt_purpose, validation_retry_policy_id,
                             validation_result_schema_id
            on agent_runtime_attempts
            when\x20
            new.attempt_purpose not in ('normal', 'result_validation_correction')
            or (new.attempt_purpose='normal' and
                (new.validation_retry_policy_id<>''
                 or new.validation_result_schema_id<>''))
            or (new.attempt_purpose='result_validation_correction' and
                (trim(new.validation_retry_policy_id)=''
                 or trim(new.validation_result_schema_id)=''))
       \x20
            begin
                select raise(abort, 'invalid runtime attempt correction lineage');
            end""",
        ),
        (
            "trigger",
            "trg_runtime_attempt_session_evidence_trim_insert",
            """CREATE TRIGGER trg_runtime_attempt_session_evidence_trim_insert
            before insert on agent_runtime_attempts
            when new.session_mode is null or new.source_session_id is null or not (
                (new.session_mode='fresh' and new.source_session_id='')
                or (new.session_mode='resume' and trim(new.source_session_id)<>'')
            )
            begin
                select raise(abort, 'invalid runtime attempt session evidence');
            end""",
        ),
        (
            "trigger",
            "trg_runtime_attempt_session_evidence_trim_update",
            """CREATE TRIGGER trg_runtime_attempt_session_evidence_trim_update
            before update of session_mode, source_session_id on agent_runtime_attempts
            when new.session_mode is null or new.source_session_id is null or not (
                (new.session_mode='fresh' and new.source_session_id='')
                or (new.session_mode='resume' and trim(new.source_session_id)<>'')
            )
            begin
                select raise(abort, 'invalid runtime attempt session evidence');
            end""",
        ),
    ),
    "agent_run_state_events": (
        (
            "index",
            "idx_agent_run_state_events_run",
            """CREATE INDEX idx_agent_run_state_events_run
                    on agent_run_state_events(agent_run_id, id)""",
        ),
        (
            "table",
            "agent_run_state_events",
            """CREATE TABLE agent_run_state_events (
                    id integer primary key autoincrement,
                    agent_run_id integer not null,
                    phase text not null,
                    structured_error_json text not null,
                    created_at text not null default current_timestamp,
                    foreign key(agent_run_id) references agent_runs(id)
                )""",
        ),
    ),
    "agent_run_events": (
        (
            "index",
            "idx_agent_run_events_run_call",
            """CREATE INDEX idx_agent_run_events_run_call
                    on agent_run_events(agent_run_id, call_id, sequence)""",
        ),
        (
            "index",
            "idx_agent_run_events_run_scope",
            """CREATE INDEX idx_agent_run_events_run_scope on agent_run_events(agent_run_id, event_scope)""",
        ),
        (
            "index",
            "idx_agent_run_events_run_sequence",
            """CREATE INDEX idx_agent_run_events_run_sequence
                    on agent_run_events(agent_run_id, sequence)""",
        ),
        (
            "table",
            "agent_run_events",
            """CREATE TABLE agent_run_events (
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
                )""",
        ),
    ),
}

RETIRED_COLUMNS = {
    "final_result_json": "",
    "result_envelope_json": "",
    "tool_events_json": "[]",
}


def _projection(record_type: str) -> str:
    fields = []
    for column in SOURCE_COLUMNS[record_type]:
        if column == "id":
            fields.append(
                "id"
                if record_type == "agent_runs"
                else "cast(legacy_key as integer) as id"
            )
        elif column in RETIRED_COLUMNS:
            fields.append(f"'{RETIRED_COLUMNS[column]}' as {column}")
        else:
            physical = COLUMN_MAP[record_type].get(column, column)
            fields.append(f"{physical} as {column}" if physical != column else column)
    return f"select {', '.join(fields)} from runs where record_type='{record_type}'"


AGENT_RUN_SQL = _projection("agent_runs")
RUNTIME_ATTEMPT_SQL = _projection("agent_runtime_attempts")
RUN_STATE_EVENT_SQL = _projection("agent_run_state_events")
RUN_PROJECTIONS = dict(
    zip(
        RUN_TYPES,
        (AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL, RUN_STATE_EVENT_SQL),
        strict=True,
    )
)

RUN_TABLE_DDL = """
create table runs (
    id integer primary key autoincrement,
    record_type text not null check(record_type in ('agent_runs','agent_runtime_attempts','agent_run_state_events')),
    legacy_key text,
    task_id integer,
    parent_id integer,
    execution_generation text,
    role text,
    proposal_revision integer,
    turn_attempt integer,
    operation_id text,
    status text,
    session_id text,
    transcript_start integer,
    transcript_end integer,
    structured_error_json text,
    lease_owner text,
    lease_expires_at text,
    started_at text,
    completed_at text,
    finished_at text,
    created_at text not null default current_timestamp,
    updated_at text,
    workload_kind text,
    workload_key text,
    attempt_number integer,
    route_name text,
    runtime_kind text,
    credential_mode text,
    model text,
    session_mode text,
    source_session_id text,
    attempt_purpose text,
    validation_retry_policy_id text,
    validation_result_schema_id text,
    failure_class text,
    failure_code text,
    failover_permitted integer,
    transcript_reference text,
    first_effect_started_at text,
    result_schema_id text,
    phase text,
    check(record_type<>'agent_runs' or legacy_key is null or legacy_key=cast(id as text)),
    foreign key(task_id) references reply_tasks(id),
    foreign key(parent_id) references runs(id),
    check(record_type<>'agent_runs' or (
        task_id is not null and execution_generation is not null
        and role is not null and role in ('consumer','audit')
        and proposal_revision is not null and proposal_revision>=0
        and turn_attempt is not null and turn_attempt>=0
        and status is not null and status in ('pending','running','completed','failed'))),
    check(record_type<>'agent_runtime_attempts' or (
        workload_kind is not null and workload_key is not null
        and attempt_number is not null and attempt_number>0
        and route_name is not null and runtime_kind is not null
        and credential_mode is not null and model is not null
        and status is not null and status in ('starting','running','completed','failed','superseded')
        and ((workload_kind='agent_run' and parent_id is not null and workload_key=cast(parent_id as text))
             or (workload_kind<>'agent_run' and parent_id is null))
        and ((session_mode='fresh' and source_session_id='')
             or (session_mode='resume' and trim(source_session_id)<>'')))),
    check(record_type<>'agent_run_state_events' or (parent_id is not null and phase is not null))
)
"""
RUN_REQUIRED_FIELDS = {
    record_type: tuple(
        COLUMN_MAP[record_type].get(column, column)
        for column in columns
        if column not in RETIRED_COLUMNS
        and column != "id"
        and not (record_type == "agent_runs" and column == "parent_agent_run_id")
        and not (record_type == "agent_runtime_attempts" and column == "agent_run_id")
    )
    for record_type, columns in SOURCE_COLUMNS.items()
}
RUN_TABLE_DDL = (
    RUN_TABLE_DDL.rstrip().removesuffix(")")
    + ","
    + ",".join(
        f"check(record_type<>'{record_type}' or ({' and '.join(field + ' is not null' for field in fields)}))"
        for record_type, fields in RUN_REQUIRED_FIELDS.items()
    )
    + ")"
)

# These are source schema defaults, supplied only for a newly inserted applicable type.
RUN_DEFAULT_SQL = {
    "agent_runs": dict(
        operation_id="''",
        status="'pending'",
        session_id="''",
        transcript_start="0",
        transcript_end="0",
        structured_error_json="''",
        lease_owner="''",
        lease_expires_at="''",
        started_at="''",
        completed_at="''",
        created_at="current_timestamp",
        updated_at="current_timestamp",
        proposal_revision="0",
        turn_attempt="0",
    ),
    "agent_runtime_attempts": dict(
        session_mode="'fresh'",
        source_session_id="''",
        attempt_purpose="'normal'",
        validation_retry_policy_id="''",
        validation_result_schema_id="''",
        session_id="''",
        failure_class="''",
        failure_code="''",
        failover_permitted="0",
        transcript_reference="''",
        transcript_start="0",
        transcript_end="0",
        first_effect_started_at="''",
        lease_owner="''",
        lease_expires_at="''",
        result_schema_id="''",
        started_at="current_timestamp",
        finished_at="''",
        created_at="current_timestamp",
        updated_at="current_timestamp",
    ),
    "agent_run_state_events": dict(created_at="current_timestamp"),
}


def insert_run(
    db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False
) -> sqlite3.Cursor:
    """Insert one typed fact using only that original source type's defaults."""
    if values.get("legacy_key") is not None:
        values = values | {"legacy_key": str(int(values["legacy_key"]))}
    defaults = {
        field: sql
        for field, sql in RUN_DEFAULT_SQL[record_type].items()
        if field not in values
    }
    columns = ["record_type", *values, *defaults]
    expressions = ["?"] * (1 + len(values)) + list(defaults.values())
    verb = "insert or ignore" if ignore else "insert"
    return db.execute(
        f"{verb} into runs ({','.join(columns)}) values ({','.join(expressions)})",
        (record_type, *values.values()),
    )


RUN_INDEX_DDL = (
    "create unique index idx_runs_public_id on runs(cast(legacy_key as integer),record_type)",
    "create unique index idx_agent_runs_turn_identity on runs(task_id,execution_generation,role,proposal_revision,turn_attempt) where record_type='agent_runs'",
    "create index idx_agent_runs_status on runs(status,updated_at) where record_type='agent_runs'",
    "create index idx_agent_run_state_events_run on runs(parent_id,cast(legacy_key as integer)) where record_type='agent_run_state_events'",
    "create unique index idx_runtime_attempt_identity on runs(workload_kind,workload_key,attempt_number) where record_type='agent_runtime_attempts'",
    "create unique index idx_runtime_attempt_active_route on runs(workload_kind,workload_key,route_name) where record_type='agent_runtime_attempts' and status in ('starting','running')",
    "create index idx_runtime_attempt_active_lease on runs(status,lease_expires_at) where record_type='agent_runtime_attempts' and parent_id is null and status in ('starting','running')",
    "create index idx_agent_runtime_attempts_run on runs(parent_id,attempt_number) where record_type='agent_runtime_attempts' and parent_id is not null",
)


def _runtime_triggers() -> tuple[str, ...]:
    statements = []
    rules = (
        (
            "session_evidence_trim",
            "session_mode, source_session_id",
            "new.session_mode is null or new.source_session_id is null or not ((new.session_mode='fresh' and new.source_session_id='') or (new.session_mode='resume' and trim(new.source_session_id)<>''))",
            "invalid runtime attempt session evidence",
        ),
        (
            "lineage",
            "attempt_purpose, validation_retry_policy_id, validation_result_schema_id",
            "new.attempt_purpose not in ('normal','result_validation_correction') or (new.attempt_purpose='normal' and (new.validation_retry_policy_id<>'' or new.validation_result_schema_id<>'')) or (new.attempt_purpose='result_validation_correction' and (trim(new.validation_retry_policy_id)='' or trim(new.validation_result_schema_id)=''))",
            "invalid runtime attempt correction lineage",
        ),
        (
            "generalized_lease",
            "status, lease_owner, lease_expires_at",
            "new.parent_id is null and new.status in ('starting','running') and (trim(new.lease_owner)='' or trim(new.lease_expires_at)='')",
            "active generalized attempt requires lease",
        ),
    )
    for name, columns, invalid, error in rules:
        for event, suffix in (("insert", "insert"), (f"update of {columns}", "update")):
            statements.append(
                f"create trigger trg_runtime_attempt_{name}_{suffix} before {event} on runs when new.record_type='agent_runtime_attempts' and ({invalid}) begin select raise(abort,'{error}'); end"
            )
    statements.append(
        "create trigger trg_runtime_attempt_lineage_immutable before update of attempt_purpose,validation_retry_policy_id,validation_result_schema_id on runs when new.record_type='agent_runtime_attempts' and (new.attempt_purpose<>old.attempt_purpose or new.validation_retry_policy_id<>old.validation_retry_policy_id or new.validation_result_schema_id<>old.validation_result_schema_id) begin select raise(abort,'runtime attempt correction lineage is immutable'); end"
    )
    return tuple(statements)


RUN_TRIGGER_DDL = (
    "create trigger trg_runs_record_type_immutable before update of record_type on runs when new.record_type<>old.record_type begin select raise(abort,'run record type is immutable'); end",
    "create trigger trg_runs_public_key after insert on runs when new.legacy_key is null begin update runs set legacy_key=cast(new.id as text) where id=new.id; end",
    *tuple(
        f"create trigger trg_runs_logical_parent_{suffix} before {event} on runs when new.parent_id is not null and exists(select 1 from runs where id=new.parent_id and record_type<>'agent_runs') begin select raise(abort,'run parent must be a logical agent run'); end"
        for event, suffix in (
            ("insert", "insert"),
            ("update of parent_id,record_type", "update"),
        )
    ),
    *_runtime_triggers(),
)


TASK_AGENT_RUN_COLUMNS = (
    "id",
    "summary_input_id",
    "codex_session_id",
    "decision_json",
    "audit_summary",
    "memory_recall_used",
    "created_at",
    "status",
    "error",
    "finished_at",
    "updated_at",
    "projection_json",
)
TASK_AGENT_RUN_RETIRED = {
    "decision_json": "{}",
    "audit_summary": "",
    "memory_recall_used": 0,
}
TASK_AGENT_RUN_SQL = """select cast(legacy_key as integer) as id, summary_input_id,
    session_id as codex_session_id, '{}' as decision_json, '' as audit_summary,
    0 as memory_recall_used, created_at, status, error, finished_at, updated_at,
    projection_json from runs where record_type='task_agent_runs'"""
RUN_DEFAULT_SQL["task_agent_runs"] = dict(
    session_id="''",
    status="'completed'",
    error="''",
    finished_at="''",
    updated_at="''",
    created_at="current_timestamp",
    projection_json="'{}'",
)
MEETING_RUN_COLUMNS = (
    "id", "job_id", "codex_session_id", "codex_transcript_start_line",
    "codex_transcript_end_line", "decision_json", "audit_tool_events_json",
    "audit_summary", "status", "error", "created_at", "finished_at", "updated_at",
)
MEETING_RUN_RETIRED = {
    "decision_json": "{}", "audit_tool_events_json": "[]", "audit_summary": "",
}
MEETING_RUN_SQL = """select cast(r.legacy_key as integer) as id,
    case when p.record_type='meeting_alignment_jobs' and r.job_id is null
         then cast(p.legacy_key as integer)
         when p.id is null then r.job_id end as job_id,
    r.session_id as codex_session_id,
    r.transcript_start as codex_transcript_start_line,
    r.transcript_end as codex_transcript_end_line,
    '{}' as decision_json, '[]' as audit_tool_events_json, '' as audit_summary,
    r.status, r.error, r.created_at, r.finished_at, r.updated_at
    from runs r left join tasks p on p.id=r.task_id
    where r.record_type='meeting_alignment_runs'"""
RUN_DEFAULT_SQL["meeting_alignment_runs"] = dict(
    session_id="''", transcript_start="0", transcript_end="0", error="''",
    created_at="current_timestamp", finished_at="''", updated_at="''",
)
OKR_RUN_COLUMNS = (
    "id", "request_id", "codex_session_id", "codex_transcript_start_line",
    "codex_transcript_end_line", "envelope_json", "audit_tool_events_json",
    "audit_summary", "created_at",
)
OKR_RUN_RETIRED = {
    "envelope_json": "{}", "audit_tool_events_json": "[]", "audit_summary": "",
}
OKR_RUN_SQL = """select cast(legacy_key as integer) as id, request_id,
    session_id as codex_session_id, transcript_start as codex_transcript_start_line,
    transcript_end as codex_transcript_end_line, '{}' as envelope_json,
    '[]' as audit_tool_events_json, '' as audit_summary, created_at
    from runs where record_type='okr_review_runs'"""
RUN_DEFAULT_SQL["okr_review_runs"] = dict(
    session_id="''", transcript_start="0", transcript_end="0",
    created_at="current_timestamp",
)
# Keep the 4A declaration and inverse usable for their original release. 4B
# extends the actual domain fields and only the source-declared task parent FKs.
TASK_RUN_TABLE_DDL = RUN_TABLE_DDL.replace(
    "'agent_run_state_events'))",
    "'agent_run_state_events','task_agent_runs','meeting_alignment_runs','okr_review_runs'))", 1
).replace("references reply_tasks(id)", "references tasks(id)")
TASK_RUN_TABLE_DDL = TASK_RUN_TABLE_DDL.replace(
    "    phase text,",
    "    phase text,\n    summary_input_id integer,\n    error text,\n    projection_json text,\n    job_id integer,\n    request_id integer,",
    1,
)
TASK_RUN_TABLE_DDL = (
    TASK_RUN_TABLE_DDL.rstrip().removesuffix(")")
    + """,
    check(record_type<>'task_agent_runs' or (
        summary_input_id is not null and session_id is not null and status is not null
        and error is not null and finished_at is not null and updated_at is not null
        and projection_json is not null and task_id is null and parent_id is null)),
    check(record_type<>'meeting_alignment_runs' or (
        task_id is not null and session_id is not null and transcript_start is not null
        and transcript_end is not null and status is not null and error is not null
        and finished_at is not null and updated_at is not null)),
    check(record_type<>'okr_review_runs' or (
        request_id is not null and session_id is not null and transcript_start is not null
        and transcript_end is not null))
)"""
)
_TASK_DOMAIN_FIELDS = {
    "summary_input_id",
    "session_id",
    "created_at",
    "status",
    "error",
    "finished_at",
    "updated_at",
    "projection_json",
}
_MEETING_DOMAIN_FIELDS = {
    "task_id", "job_id", "session_id", "transcript_start", "transcript_end",
    "status", "error", "created_at", "finished_at", "updated_at",
}
_OKR_DOMAIN_FIELDS = {
    "request_id", "session_id", "transcript_start", "transcript_end", "created_at",
}
_CORE_RUN_FIELDS = {
    COLUMN_MAP[record_type].get(column, column)
    for record_type, columns in SOURCE_COLUMNS.items()
    for column in columns
    if column != "id" and column not in RETIRED_COLUMNS
}
TASK_RUN_TABLE_DDL = (
    TASK_RUN_TABLE_DDL.rstrip().removesuffix(")")
    + ","
    + ",".join(
        f"check(record_type<>'{record_type}' or ("
        + " and ".join(field + " is null" for field in sorted(
            (_CORE_RUN_FIELDS | _TASK_DOMAIN_FIELDS | _MEETING_DOMAIN_FIELDS | _OKR_DOMAIN_FIELDS) - fields
        ))
        + "))"
        for record_type, fields in (
            ("task_agent_runs", _TASK_DOMAIN_FIELDS),
            ("meeting_alignment_runs", _MEETING_DOMAIN_FIELDS),
            ("okr_review_runs", _OKR_DOMAIN_FIELDS),
        )
    )
    + ",check(record_type in ('task_agent_runs','meeting_alignment_runs','okr_review_runs') or (summary_input_id is null and error is null and projection_json is null and job_id is null and request_id is null)))"
)
TASK_RUN_INDEX_DDL = (
    *RUN_INDEX_DDL,
    "create index idx_task_agent_runs_input on runs(summary_input_id,cast(legacy_key as integer)) where record_type='task_agent_runs'",
    "create unique index idx_task_agent_runs_active_input on runs(summary_input_id) where record_type='task_agent_runs' and status='running'",
    "create index idx_meeting_alignment_runs_job on runs(task_id,cast(legacy_key as integer)) where record_type='meeting_alignment_runs'",
    "create index idx_meeting_alignment_runs_created on runs(created_at,cast(legacy_key as integer)) where record_type='meeting_alignment_runs'",
    "create unique index idx_meeting_alignment_runs_active_job on runs(task_id) where record_type='meeting_alignment_runs' and status='running'",
)
TASK_RUN_TRIGGER_DDL = (
    *RUN_TRIGGER_DDL,
    *tuple(
        f"create trigger trg_runs_reply_task_{suffix} before {event} on runs when new.record_type='agent_runs' and new.task_id is not null and exists(select 1 from tasks where id=new.task_id and record_type<>'reply_tasks') begin select raise(abort,'run task must be a reply task'); end"
        for event, suffix in (("insert", "insert"), ("update of task_id", "update"))
    ),
    *tuple(
        f"create trigger trg_runs_meeting_task_{suffix} before {event} on runs when new.record_type='meeting_alignment_runs' and (exists(select 1 from tasks where id=new.task_id and (record_type<>'meeting_alignment_jobs' or new.job_id is not null)) or (not exists(select 1 from tasks where id=new.task_id) and new.job_id is null)) begin select raise(abort,'meeting run parent mapping mismatch'); end"
        for event, suffix in (("insert", "insert"), ("update of task_id,job_id", "update"))
    ),
)


SCHEDULED_RUN_COLUMNS = ("id", "event_id", "scheduled_task_id", "trigger_kind",
    "scheduled_for", "dispatch_status", "skip_or_error_reason", "execution_kind",
    "execution_id", "lease_owner", "lease_expires_at", "created_at", "dispatched_at",
    "first_scheduled_for", "occurrence_count", "result_summary", "snapshot_id")
WORKBENCH_EVENT_COLUMNS = ("id", "turn_id", "sequence", "event_type", "payload_json", "created_at")
SCHEDULED_RUN_SQL = "select cast(legacy_key as integer) as id," + ",".join(SCHEDULED_RUN_COLUMNS[1:]) + " from runs where record_type='scheduled_task_runs'"
WORKBENCH_EVENT_SQL = """select cast(r.legacy_key as integer) as id,
    (select t.legacy_key from tasks t where t.id=r.task_id) as turn_id,
    r.sequence,r.event_type,r.payload_json,r.created_at
    from runs r where r.record_type='workbench_events'"""
RUN_DEFAULT_SQL.update({
    "scheduled_task_runs": dict(dispatch_status="'pending'", skip_or_error_reason="''",
        execution_kind="''", execution_id="''", lease_owner="''", created_at="current_timestamp",
        first_scheduled_for="''", occurrence_count="1", result_summary="''"),
    "workbench_events": dict(created_at="current_timestamp"),
})
_WORKBENCH_RUN_FIELDS = {
    "scheduled_task_runs": set(SCHEDULED_RUN_COLUMNS[1:]),
    "workbench_events": {"task_id", "sequence", "event_type", "payload_json", "created_at"},
}
_EXISTING_RUN_FIELDS = _CORE_RUN_FIELDS | _TASK_DOMAIN_FIELDS | _MEETING_DOMAIN_FIELDS | _OKR_DOMAIN_FIELDS
_WORKBENCH_RUN_NEW_FIELDS = tuple(dict.fromkeys(
    c for c in (*SCHEDULED_RUN_COLUMNS[1:], "sequence", "event_type", "payload_json")
    if c not in _EXISTING_RUN_FIELDS
))
WORKBENCH_RUN_TABLE_DDL = TASK_RUN_TABLE_DDL.replace(
    "'okr_review_runs'))", "'okr_review_runs','scheduled_task_runs','workbench_events'))", 1,
).replace(
    "    check(record_type<>'agent_runs' or legacy_key",
    "    " + ",\n    ".join(c + (" integer" if c in ("scheduled_task_id", "snapshot_id", "occurrence_count", "sequence") else " text") for c in _WORKBENCH_RUN_NEW_FIELDS)
    + ",\n    foreign key(scheduled_task_id) references scheduled_tasks(id),"
    + "\n    foreign key(snapshot_id) references scheduled_task_config_versions(id),"
    + "\n    check(record_type<>'agent_runs' or legacy_key", 1,
).removesuffix(")") + "," + ",".join(
    "check(record_type<>'" + kind + "' or ("
    + " and ".join(c + " is not null" for c in sorted(fields - {"lease_expires_at", "dispatched_at", "snapshot_id"})) + "))"
    for kind, fields in _WORKBENCH_RUN_FIELDS.items()
) + "," + ",".join(
    "check(record_type<>'" + kind + "' or ("
    + " and ".join(c + " is null" for c in sorted(
        (_EXISTING_RUN_FIELDS | set(_WORKBENCH_RUN_NEW_FIELDS)) - fields)) + "))"
    for kind, fields in _WORKBENCH_RUN_FIELDS.items()
) + ",check(record_type in ('scheduled_task_runs','workbench_events') or (" + " and ".join(
    c + " is null" for c in _WORKBENCH_RUN_NEW_FIELDS
) + "))" + ",check(record_type<>'scheduled_task_runs' or (trigger_kind in ('scheduled','manual') and dispatch_status in ('pending','dispatched','skipped','failed'))))"
WORKBENCH_RUN_INDEX_DDL = (
    *TASK_RUN_INDEX_DDL,
    "create unique index idx_scheduled_task_runs_event on runs(event_id) where record_type='scheduled_task_runs'",
    "create unique index idx_scheduled_task_runs_scheduled_instant on runs(scheduled_task_id,scheduled_for) where record_type='scheduled_task_runs' and trigger_kind='scheduled'",
    "create index idx_scheduled_task_runs_dispatch on runs(dispatch_status,lease_expires_at,scheduled_for,cast(legacy_key as integer)) where record_type='scheduled_task_runs'",
    "create index idx_scheduled_task_runs_claim on runs(dispatch_status,scheduled_for,cast(legacy_key as integer)) where record_type='scheduled_task_runs'",
    "create index idx_scheduled_task_runs_latest on runs(scheduled_task_id,cast(legacy_key as integer) desc) where record_type='scheduled_task_runs'",
    "create index idx_scheduled_task_runs_reply_execution on runs(cast(execution_id as integer),scheduled_task_id,cast(legacy_key as integer)) where record_type='scheduled_task_runs' and execution_kind='reply_task'",
    "create unique index idx_workbench_events_sequence on runs(task_id,sequence) where record_type='workbench_events'",
    "create index idx_workbench_events_turn_id_id on runs(task_id,cast(legacy_key as integer)) where record_type='workbench_events'",
    "create index idx_workbench_events_id_turn_id on runs(cast(legacy_key as integer),task_id) where record_type='workbench_events'",
    "create index idx_workbench_events_event_type on runs(event_type) where record_type='workbench_events'",
)
WORKBENCH_RUN_TRIGGER_DDL = (
    *TASK_RUN_TRIGGER_DDL,
    *tuple(
        f"create trigger trg_runs_workbench_turn_{suffix} before {event} on runs when new.record_type='workbench_events' and exists(select 1 from tasks where id=new.task_id and record_type<>'workbench_turns') begin select raise(abort,'event task must be a workbench turn'); end"
        for event,suffix in (("insert","insert"),("update of task_id","update"))
    ),
)


def create_run_schema(
    db: sqlite3.Connection, *, include_task_runs: bool = False, include_workbench: bool = False
) -> None:
    db.execute(
        (WORKBENCH_RUN_TABLE_DDL if include_workbench else TASK_RUN_TABLE_DDL if include_task_runs else RUN_TABLE_DDL).replace(
            "create table runs", "create table if not exists runs", 1
        )
    )
    for sql in (
        (*WORKBENCH_RUN_INDEX_DDL, *WORKBENCH_RUN_TRIGGER_DDL)
        if include_workbench else (*TASK_RUN_INDEX_DDL, *TASK_RUN_TRIGGER_DDL)
        if include_task_runs
        else (*RUN_INDEX_DDL, *RUN_TRIGGER_DDL)
    ):
        if sql.startswith("create unique index "):
            sql = sql.replace(
                "create unique index ", "create unique index if not exists ", 1
            )
        elif sql.startswith("create index "):
            sql = sql.replace("create index ", "create index if not exists ", 1)
        else:
            sql = sql.replace("create trigger ", "create trigger if not exists ", 1)
        db.execute(sql)


@lru_cache(maxsize=3)
def _declared_run_objects(include_task_runs: bool, include_workbench: bool = False) -> dict[str, str]:
    """Read SQLite's canonical declarations without changing SQL literals."""
    with closing(sqlite3.connect(":memory:")) as reference:
        create_run_schema(reference, include_task_runs=include_task_runs, include_workbench=include_workbench)
        return dict(
            reference.execute(
                "select name,sql from sqlite_master where tbl_name='runs' and sql is not null"
            )
        )


def run_schema_is_current(
    db: sqlite3.Connection,
    *,
    allow_missing_objects: bool = False,
    include_task_runs: bool = False,
    include_workbench: bool = False,
) -> bool:
    """Match the current core layout and its declared indexes/triggers.

    Missing objects can be recreated by the existing Store initialization path.
    A changed definition or an old core layout is not that repair case.
    """
    retired_names = ",".join("?" for _ in RETIRED_RUN_TABLES)
    if db.execute(
        f"select 1 from sqlite_master where type in ('table','view') and name in ({retired_names}) limit 1",
        RETIRED_RUN_TABLES,
    ).fetchone():
        return False
    if include_workbench and db.execute("select 1 from sqlite_master where name in ('scheduled_task_runs','workbench_events')").fetchone():
        return False
    objects = {
        row[0]: str(row[1] or "")
        for row in db.execute(
            "select name,sql from sqlite_master where tbl_name='runs'"
        )
    }

    expected = _declared_run_objects(include_task_runs, include_workbench)
    if objects.get("runs", "").strip() != expected["runs"].strip():
        return False
    for name, sql in expected.items():
        if name == "runs":
            continue
        if name not in objects:
            if allow_missing_objects:
                continue
            return False
        if objects[name].strip() != sql.strip():
            return False
    return True


def _schema_objects(db, table):
    return [
        dict(zip(("type", "name", "sql"), row, strict=True))
        for row in db.execute(
            "select type,name,sql from sqlite_master where tbl_name=? and sql is not null order by type,name",
            (table,),
        )
    ]


def _validate_source_declarations(db: sqlite3.Connection) -> None:
    for table, expected in SOURCE_RUN_DECLARATIONS.items():
        actual = tuple(
            (obj["type"], obj["name"], obj["sql"].strip())
            for obj in _schema_objects(db, table)
        )
        if actual != expected:
            raise ValueError(f"unsupported 11.1 source declaration for {table}")


def capture_run_schema(db: sqlite3.Connection) -> dict:
    tables = [
        r[0]
        for r in db.execute(
            "select name from sqlite_master where type='table' order by name"
        )
    ]
    children = [
        table
        for table in tables
        if table not in RETIRED_RUN_TABLES
        and any(
            row[2] == "agent_runs"
            for row in db.execute(f'pragma foreign_key_list("{table}")')
        )
    ]
    scoped = [table for table in RETIRED_RUN_TABLES if table in tables] + children
    sequences = (
        dict(db.execute("select name,seq from sqlite_sequence"))
        if "sqlite_sequence" in tables
        else {}
    )
    source_high_water = max(
        [0]
        + [int(sequences.get(table) or 0) for table in scoped]
        + [
            int(db.execute(f'select coalesce(max(id),0) from "{table}"').fetchone()[0])
            for table in RETIRED_RUN_TABLES
            if table in tables
        ]
    )
    migration_high_water = source_high_water + sum(
        int(db.execute(f'select count(*) from "{table}"').fetchone()[0])
        for table in ("agent_runtime_attempts", "agent_run_state_events")
        if table in tables
    )
    return {
        "tables": {
            table: {
                "objects": _schema_objects(db, table),
                "columns": [r[1] for r in db.execute(f'pragma table_info("{table}")')],
                "sequence": sequences.get(table),
            }
            for table in scoped
        },
        "children": children,
        "allocator": {
            "source_high_water": source_high_water,
            "migration_high_water": migration_high_water,
        },
    }


def _replace_parent_ddl(sql, target):
    return re.sub(
        r'(?i)(references\s+)["`\[]?agent_runs["`\]]?(\s*\()',
        lambda m: m[1] + target + m[2],
        sql,
    )


def _rebuild_child(db, table, schema, parent):
    columns = schema["columns"]
    rows = [tuple(row) for row in db.execute(f'select * from "{table}"')]
    sequence = db.execute(
        "select seq from sqlite_sequence where name=?", (table,)
    ).fetchone()
    db.execute(f'drop table "{table}"')
    table_sql = next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table")
    db.execute(_replace_parent_ddl(table_sql, parent))
    names = ",".join(f'"{column}"' for column in columns)
    db.executemany(
        f'insert into "{table}" ({names}) values ({",".join("?" for _ in columns)})',
        rows,
    )
    for obj in schema["objects"]:
        if obj["type"] != "table":
            db.execute(obj["sql"])
    if sequence is not None:
        _set_sequence(db, table, max(int(sequence[0]), int(schema["sequence"] or 0)))
    if parent == "runs":
        _install_child_guards(db, table)


def _install_child_guards(db: sqlite3.Connection, table: str) -> None:
    for fk in db.execute(f'pragma foreign_key_list("{table}")'):
        if fk[2] != "runs":
            continue
        column = fk[3]
        for event, suffix in (
            ("insert", "insert"),
            (f'update of "{column}"', "update"),
        ):
            db.execute(
                f'create trigger if not exists trg_runs_child_{table}_{column}_{suffix} before {event} on "{table}" when new."{column}" is not null and exists(select 1 from runs where id=new."{column}" and record_type<>\'agent_runs\') begin select raise(abort,\'run parent must be a logical agent run\'); end'
            )


def install_run_child_guards(db: sqlite3.Connection) -> None:
    """Retain each old declared FK's logical-run parent namespace."""
    for row in db.execute(
        "select name from sqlite_master where type='table' and name<>'runs'"
    ).fetchall():
        _install_child_guards(db, row[0])


def _drop_explicit_objects(db: sqlite3.Connection, objects: list[dict]) -> None:
    for obj in objects:
        if obj["type"] in ("index", "trigger"):
            db.execute(f'drop {obj["type"]} "{obj["name"]}"')


def _set_sequence(db, table, value):
    db.execute("delete from sqlite_sequence where name=?", (table,))
    db.execute("insert into sqlite_sequence(name,seq) values (?,?)", (table, value))


def migrate_run_schema(db: sqlite3.Connection) -> dict:
    tables = {
        r[0] for r in db.execute("select name from sqlite_master where type='table'")
    }
    if (
        "agent_run_events" in tables
        and db.execute("select 1 from agent_run_events limit 1").fetchone()
    ):
        raise ValueError("agent_run_events contains native events; migration stopped")
    if "runs" in tables:
        raise ValueError("unsupported 11.1 source schema: runs already exists")
    state = capture_run_schema(db)
    for table in RUN_TYPES:
        if table not in state["tables"] or set(
            state["tables"][table]["columns"]
        ) != set(SOURCE_COLUMNS[table]):
            raise ValueError(f"unsupported 11.1 source schema for {table}")
    high = state["allocator"]["source_high_water"]
    for table in RUN_TYPES:
        columns = state["tables"][table]["columns"]
        for column, placeholder in RETIRED_COLUMNS.items():
            if (
                column in columns
                and db.execute(
                    f'select 1 from "{table}" where "{column}" is null or "{column}"<>? limit 1',
                    (placeholder,),
                ).fetchone()
            ):
                raise ValueError(f"{table}.{column} is not a retired empty placeholder")
    _validate_source_declarations(db)
    for table in RETIRED_RUN_TABLES:
        if table in state["tables"]:
            _drop_explicit_objects(db, state["tables"][table]["objects"])
    create_run_schema(db)
    next_id = high
    counts = {}
    for table in RUN_TYPES:
        columns = state["tables"][table]["columns"]
        counts[table] = 0
        for row in db.execute(f'select * from "{table}" order by id').fetchall():
            source = dict(zip(columns, row, strict=True))
            if table == "agent_runs":
                physical_id = source["id"]
            else:
                next_id += 1
                physical_id = next_id
            values = {
                "id": physical_id,
                "record_type": table,
                "legacy_key": str(source["id"]),
            }
            values.update(
                {
                    COLUMN_MAP[table].get(column, column): value
                    for column, value in source.items()
                    if column not in RETIRED_COLUMNS and column != "id"
                }
            )
            db.execute(
                f"insert into runs ({','.join(values)}) values ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            counts[table] += 1
    for table in state["children"]:
        _rebuild_child(db, table, state["tables"][table], "runs")
    for table in RETIRED_RUN_TABLES:
        if table in tables:
            db.execute(f'drop table "{table}"')
    _set_sequence(db, "runs", max(high, next_id))
    return {
        "counts": counts,
        "high_water": max(high, next_id),
        "children": state["children"],
    }


def restore_run_schema(db: sqlite3.Connection, state: dict) -> dict:
    # Rows can have been allocated and legitimately deleted since replacement.
    # Reserve those public IDs in every restored active source namespace. The
    # deterministic migration allocation itself does not advance old sequences.
    sequence = db.execute(
        "select seq from sqlite_sequence where name='runs'"
    ).fetchone()
    current_high_water = int(sequence[0]) if sequence is not None else 0
    replacement_high_water = (
        current_high_water
        if current_high_water > state["allocator"]["migration_high_water"]
        else 0
    )
    _drop_explicit_objects(db, _schema_objects(db, "runs"))
    counts = {}
    for table in RETIRED_RUN_TABLES:
        if table not in state["tables"]:
            continue
        schema = state["tables"][table]
        db.execute(
            next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table")
        )
        rows = (
            db.execute(RUN_PROJECTIONS[table]).fetchall() if table in RUN_TYPES else []
        )
        projection_columns = SOURCE_COLUMNS.get(table, ())
        values = []
        for row in rows:
            source = dict(zip(projection_columns, row, strict=True))
            values.append(
                tuple(
                    source[column] if column in source else RETIRED_COLUMNS[column]
                    for column in schema["columns"]
                )
            )
        names = ",".join(f'"{column}"' for column in schema["columns"])
        db.executemany(
            f'insert into "{table}" ({names}) values ({",".join("?" for _ in schema["columns"])})',
            values,
        )
        for obj in schema["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
        reserved_high_water = replacement_high_water if table in RUN_TYPES else 0
        if schema["sequence"] is not None or reserved_high_water:
            maximum = db.execute(
                f'select coalesce(max(id),0) from "{table}"'
            ).fetchone()[0]
            _set_sequence(
                db,
                table,
                max(int(schema["sequence"] or 0), maximum, reserved_high_water),
            )
        counts[table] = len(values)
    for table in state["children"]:
        _rebuild_child(db, table, state["tables"][table], "agent_runs")
    db.execute("drop table runs")
    return {"counts": counts, "children": state["children"]}
