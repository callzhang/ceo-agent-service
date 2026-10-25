"""Current logical Runs; immutable Runs11 declarations remain the source oracle.

source_type is original logical provenance, not another lifecycle owner.
Public legacy IDs retain their native SQL values, distinct from physical IDs.
"""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app.runs11_schema_helpers import _target_objects as source_run_objects

SOURCE_TASKS = {
    "task_agent_runs": "work_summary_inputs",
    "meeting_alignment_runs": "meeting_alignment_jobs",
    "okr_review_runs": "okr_review_requests",
}

LOGICAL_RUN_TABLE_DDL = """CREATE TABLE runs (
    id integer primary key autoincrement,
    record_type text not null check(record_type in ('agent_runs','agent_runtime_attempts','agent_run_state_events','scheduled_task_runs','workbench_events','codex_session_search_index','email_classifier_runtime_samples','setup_wizard_events')),
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
    error text,
    projection_json text,
    job_id integer,
    event_id text,
    scheduled_task_id integer,
    trigger_kind text,
    scheduled_for text,
    dispatch_status text,
    skip_or_error_reason text,
    execution_kind text,
    execution_id text,
    dispatched_at text,
    first_scheduled_for text,
    occurrence_count integer,
    result_summary text,
    snapshot_id integer,
    sequence integer,
    event_type text,
    payload_json text,
    source_type text,
    source_id text,
    title text,
    model_id text,
    outcome text,
    fallback_code text,
    cache_hit integer,
    runtime_warm integer,
    queue_ms real,
    http_ms real,
    embedding_ms real,
    head_ms real,
    total_ms real,
    step_id text,
    action_id text,
    summary text,
    evidence_json text,
    stdout_excerpt text,
    stderr_excerpt text,
    foreign key(scheduled_task_id) references schedules(id),
    foreign key(snapshot_id) references configs(schedule_snapshot_public_id),
    check(record_type<>'agent_runs' or source_type is not null or legacy_key is null or legacy_key=cast(id as text)),
    foreign key(task_id) references tasks(id),
    foreign key(parent_id) references runs(id),
    check(record_type<>'agent_runs' or source_type is not null or (
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
    check(record_type<>'agent_run_state_events' or (parent_id is not null and phase is not null)),
    check(record_type<>'agent_runs' or source_type is not null or (task_id is not null and execution_generation is not null and role is not null and proposal_revision is not null and turn_attempt is not null and operation_id is not null and status is not null and session_id is not null and transcript_start is not null and transcript_end is not null and structured_error_json is not null and lease_owner is not null and lease_expires_at is not null and started_at is not null and completed_at is not null and created_at is not null and updated_at is not null)),
    check(record_type<>'agent_runtime_attempts' or (workload_kind is not null and workload_key is not null and attempt_number is not null and route_name is not null and runtime_kind is not null and credential_mode is not null and model is not null and session_mode is not null and source_session_id is not null and attempt_purpose is not null and validation_retry_policy_id is not null and validation_result_schema_id is not null and session_id is not null and status is not null and failure_class is not null and failure_code is not null and failover_permitted is not null and transcript_reference is not null and transcript_start is not null and transcript_end is not null and first_effect_started_at is not null and lease_owner is not null and lease_expires_at is not null and result_schema_id is not null and started_at is not null and finished_at is not null and created_at is not null and updated_at is not null)),
    check(record_type<>'agent_run_state_events' or (parent_id is not null and phase is not null and structured_error_json is not null and created_at is not null)),
    check(record_type<>'scheduled_task_runs' or (created_at is not null and dispatch_status is not null and event_id is not null and execution_id is not null and execution_kind is not null and first_scheduled_for is not null and lease_owner is not null and occurrence_count is not null and result_summary is not null and scheduled_for is not null and scheduled_task_id is not null and skip_or_error_reason is not null and trigger_kind is not null)),
    check(record_type<>'workbench_events' or (created_at is not null and event_type is not null and payload_json is not null and sequence is not null and task_id is not null)),
    check(record_type<>'scheduled_task_runs' or (attempt_number is null and attempt_purpose is null and completed_at is null and credential_mode is null and error is null and event_type is null and execution_generation is null and failover_permitted is null and failure_class is null and failure_code is null and finished_at is null and first_effect_started_at is null and job_id is null and model is null and operation_id is null and parent_id is null and payload_json is null and phase is null and projection_json is null and proposal_revision is null and result_schema_id is null and role is null and route_name is null and runtime_kind is null and sequence is null and session_id is null and session_mode is null and source_session_id is null and started_at is null and status is null and structured_error_json is null and task_id is null and transcript_end is null and transcript_reference is null and transcript_start is null and turn_attempt is null and updated_at is null and validation_result_schema_id is null and validation_retry_policy_id is null and workload_key is null and workload_kind is null)),
    check(record_type<>'workbench_events' or (attempt_number is null and attempt_purpose is null and completed_at is null and credential_mode is null and dispatch_status is null and dispatched_at is null and error is null and event_id is null and execution_generation is null and execution_id is null and execution_kind is null and failover_permitted is null and failure_class is null and failure_code is null and finished_at is null and first_effect_started_at is null and first_scheduled_for is null and job_id is null and lease_expires_at is null and lease_owner is null and model is null and occurrence_count is null and operation_id is null and parent_id is null and phase is null and projection_json is null and proposal_revision is null and result_schema_id is null and result_summary is null and role is null and route_name is null and runtime_kind is null and scheduled_for is null and scheduled_task_id is null and session_id is null and session_mode is null and skip_or_error_reason is null and snapshot_id is null and source_session_id is null and started_at is null and status is null and structured_error_json is null and transcript_end is null and transcript_reference is null and transcript_start is null and trigger_kind is null and turn_attempt is null and updated_at is null and validation_result_schema_id is null and validation_retry_policy_id is null and workload_key is null and workload_kind is null)),
    check(record_type in ('scheduled_task_runs','workbench_events') or (event_id is null and scheduled_task_id is null and trigger_kind is null and scheduled_for is null and dispatch_status is null and skip_or_error_reason is null and execution_kind is null and execution_id is null and dispatched_at is null and first_scheduled_for is null and occurrence_count is null and result_summary is null and snapshot_id is null and sequence is null and event_type is null and payload_json is null)),
    check(record_type<>'scheduled_task_runs' or (trigger_kind in ('scheduled','manual') and dispatch_status in ('pending','dispatched','skipped','failed'))),
    check(record_type<>'codex_session_search_index' or (session_id is not null and source_type is not null and source_id is not null and title is not null and created_at is not null and updated_at is not null and legacy_key is not null and legacy_key=cast(cast(legacy_key as integer) as text) and action_id is null and attempt_number is null and attempt_purpose is null and cache_hit is null and completed_at is null and credential_mode is null and dispatch_status is null and dispatched_at is null and embedding_ms is null and error is null and event_id is null and event_type is null and evidence_json is null and execution_generation is null and execution_id is null and execution_kind is null and failover_permitted is null and failure_class is null and failure_code is null and fallback_code is null and finished_at is null and first_effect_started_at is null and first_scheduled_for is null and head_ms is null and http_ms is null and job_id is null and lease_expires_at is null and lease_owner is null and model is null and model_id is null and occurrence_count is null and operation_id is null and outcome is null and parent_id is null and payload_json is null and phase is null and projection_json is null and proposal_revision is null and queue_ms is null and result_schema_id is null and result_summary is null and role is null and route_name is null and runtime_kind is null and runtime_warm is null and scheduled_for is null and scheduled_task_id is null and sequence is null and session_mode is null and skip_or_error_reason is null and snapshot_id is null and source_session_id is null and started_at is null and status is null and stderr_excerpt is null and stdout_excerpt is null and step_id is null and structured_error_json is null and summary is null and task_id is null and total_ms is null and transcript_end is null and transcript_reference is null and transcript_start is null and trigger_kind is null and turn_attempt is null and validation_result_schema_id is null and validation_retry_policy_id is null and workload_key is null and workload_kind is null)),
    check(record_type<>'email_classifier_runtime_samples' or (model_id is not null and outcome is not null and fallback_code is not null and cache_hit is not null and runtime_warm is not null and queue_ms is not null and http_ms is not null and embedding_ms is not null and head_ms is not null and total_ms is not null and created_at is not null and legacy_key is not null and legacy_key=cast(cast(legacy_key as integer) as text) and trim(model_id)<>'' and outcome in ('success','rejected','failure') and length(fallback_code)<=64 and cache_hit in (0,1) and runtime_warm in (0,1) and trim(created_at)<>'' and queue_ms>=0 and http_ms>=0 and embedding_ms>=0 and head_ms>=0 and total_ms>=0 and action_id is null and attempt_number is null and attempt_purpose is null and completed_at is null and credential_mode is null and dispatch_status is null and dispatched_at is null and error is null and event_id is null and event_type is null and evidence_json is null and execution_generation is null and execution_id is null and execution_kind is null and failover_permitted is null and failure_class is null and failure_code is null and finished_at is null and first_effect_started_at is null and first_scheduled_for is null and job_id is null and lease_expires_at is null and lease_owner is null and model is null and occurrence_count is null and operation_id is null and parent_id is null and payload_json is null and phase is null and projection_json is null and proposal_revision is null and result_schema_id is null and result_summary is null and role is null and route_name is null and runtime_kind is null and scheduled_for is null and scheduled_task_id is null and sequence is null and session_id is null and session_mode is null and skip_or_error_reason is null and snapshot_id is null and source_id is null and source_session_id is null and source_type is null and started_at is null and status is null and stderr_excerpt is null and stdout_excerpt is null and step_id is null and structured_error_json is null and summary is null and task_id is null and title is null and transcript_end is null and transcript_reference is null and transcript_start is null and trigger_kind is null and turn_attempt is null and updated_at is null and validation_result_schema_id is null and validation_retry_policy_id is null and workload_key is null and workload_kind is null)),
    check(record_type<>'setup_wizard_events' or (step_id is not null and action_id is not null and status is not null and summary is not null and evidence_json is not null and stdout_excerpt is not null and stderr_excerpt is not null and created_at is not null and finished_at is not null and legacy_key is not null and legacy_key=cast(cast(legacy_key as integer) as text) and attempt_number is null and attempt_purpose is null and cache_hit is null and completed_at is null and credential_mode is null and dispatch_status is null and dispatched_at is null and embedding_ms is null and error is null and event_id is null and event_type is null and execution_generation is null and execution_id is null and execution_kind is null and failover_permitted is null and failure_class is null and failure_code is null and fallback_code is null and first_effect_started_at is null and first_scheduled_for is null and head_ms is null and http_ms is null and job_id is null and lease_expires_at is null and lease_owner is null and model is null and model_id is null and occurrence_count is null and operation_id is null and outcome is null and parent_id is null and payload_json is null and phase is null and projection_json is null and proposal_revision is null and queue_ms is null and result_schema_id is null and result_summary is null and role is null and route_name is null and runtime_kind is null and runtime_warm is null and scheduled_for is null and scheduled_task_id is null and sequence is null and session_id is null and session_mode is null and skip_or_error_reason is null and snapshot_id is null and source_id is null and source_session_id is null and source_type is null and started_at is null and structured_error_json is null and task_id is null and title is null and total_ms is null and transcript_end is null and transcript_reference is null and transcript_start is null and trigger_kind is null and turn_attempt is null and updated_at is null and validation_result_schema_id is null and validation_retry_policy_id is null and workload_key is null and workload_kind is null)),
    check(record_type in ('codex_session_search_index','email_classifier_runtime_samples','setup_wizard_events') or (source_id is null and title is null and model_id is null and outcome is null and fallback_code is null and cache_hit is null and runtime_warm is null and queue_ms is null and http_ms is null and embedding_ms is null and head_ms is null and total_ms is null and step_id is null and action_id is null and summary is null and evidence_json is null and stdout_excerpt is null and stderr_excerpt is null)),
    check(record_type='agent_runs' or record_type='codex_session_search_index' or source_type is null),
    check(record_type<>'agent_runs' or source_type is null or source_type in ('task_agent_runs','meeting_alignment_runs','okr_review_runs')),
    check((record_type='agent_runs' and source_type is not null) or (error is null and projection_json is null and job_id is null)),
    check(record_type<>'agent_runs' or source_type<>'task_agent_runs' or (completed_at is not null and created_at is not null and error is not null and projection_json is not null and session_id is not null and status is not null and task_id is not null and updated_at is not null and parent_id is null and execution_generation is null and role is null and proposal_revision is null and turn_attempt is null and operation_id is null and transcript_start is null and transcript_end is null and structured_error_json is null and lease_owner is null and lease_expires_at is null and started_at is null and finished_at is null and workload_kind is null and workload_key is null and attempt_number is null and route_name is null and runtime_kind is null and credential_mode is null and model is null and session_mode is null and source_session_id is null and attempt_purpose is null and validation_retry_policy_id is null and validation_result_schema_id is null and failure_class is null and failure_code is null and failover_permitted is null and transcript_reference is null and first_effect_started_at is null and result_schema_id is null and phase is null and job_id is null and event_id is null and scheduled_task_id is null and trigger_kind is null and scheduled_for is null and dispatch_status is null and skip_or_error_reason is null and execution_kind is null and execution_id is null and dispatched_at is null and first_scheduled_for is null and occurrence_count is null and result_summary is null and snapshot_id is null and sequence is null and event_type is null and payload_json is null and source_id is null and title is null and model_id is null and outcome is null and fallback_code is null and cache_hit is null and runtime_warm is null and queue_ms is null and http_ms is null and embedding_ms is null and head_ms is null and total_ms is null and step_id is null and action_id is null and summary is null and evidence_json is null and stdout_excerpt is null and stderr_excerpt is null)),
    check(record_type<>'agent_runs' or source_type<>'meeting_alignment_runs' or (completed_at is not null and created_at is not null and error is not null and session_id is not null and status is not null and task_id is not null and transcript_end is not null and transcript_start is not null and updated_at is not null and parent_id is null and execution_generation is null and role is null and proposal_revision is null and turn_attempt is null and operation_id is null and structured_error_json is null and lease_owner is null and lease_expires_at is null and started_at is null and finished_at is null and workload_kind is null and workload_key is null and attempt_number is null and route_name is null and runtime_kind is null and credential_mode is null and model is null and session_mode is null and source_session_id is null and attempt_purpose is null and validation_retry_policy_id is null and validation_result_schema_id is null and failure_class is null and failure_code is null and failover_permitted is null and transcript_reference is null and first_effect_started_at is null and result_schema_id is null and phase is null and projection_json is null and event_id is null and scheduled_task_id is null and trigger_kind is null and scheduled_for is null and dispatch_status is null and skip_or_error_reason is null and execution_kind is null and execution_id is null and dispatched_at is null and first_scheduled_for is null and occurrence_count is null and result_summary is null and snapshot_id is null and sequence is null and event_type is null and payload_json is null and source_id is null and title is null and model_id is null and outcome is null and fallback_code is null and cache_hit is null and runtime_warm is null and queue_ms is null and http_ms is null and embedding_ms is null and head_ms is null and total_ms is null and step_id is null and action_id is null and summary is null and evidence_json is null and stdout_excerpt is null and stderr_excerpt is null)),
    check(record_type<>'agent_runs' or source_type<>'okr_review_runs' or (created_at is not null and session_id is not null and task_id is not null and transcript_end is not null and transcript_start is not null and parent_id is null and execution_generation is null and role is null and proposal_revision is null and turn_attempt is null and operation_id is null and status is null and structured_error_json is null and lease_owner is null and lease_expires_at is null and started_at is null and completed_at is null and finished_at is null and updated_at is null and workload_kind is null and workload_key is null and attempt_number is null and route_name is null and runtime_kind is null and credential_mode is null and model is null and session_mode is null and source_session_id is null and attempt_purpose is null and validation_retry_policy_id is null and validation_result_schema_id is null and failure_class is null and failure_code is null and failover_permitted is null and transcript_reference is null and first_effect_started_at is null and result_schema_id is null and phase is null and error is null and projection_json is null and job_id is null and event_id is null and scheduled_task_id is null and trigger_kind is null and scheduled_for is null and dispatch_status is null and skip_or_error_reason is null and execution_kind is null and execution_id is null and dispatched_at is null and first_scheduled_for is null and occurrence_count is null and result_summary is null and snapshot_id is null and sequence is null and event_type is null and payload_json is null and source_id is null and title is null and model_id is null and outcome is null and fallback_code is null and cache_hit is null and runtime_warm is null and queue_ms is null and http_ms is null and embedding_ms is null and head_ms is null and total_ms is null and step_id is null and action_id is null and summary is null and evidence_json is null and stdout_excerpt is null and stderr_excerpt is null))
)"""


@lru_cache(maxsize=1)
def logical_run_objects():
    source = source_run_objects()
    retired = {
        "idx_task_agent_runs_input", "idx_task_agent_runs_active_input",
        "idx_meeting_alignment_runs_job", "idx_meeting_alignment_runs_created",
        "idx_meeting_alignment_runs_active_job", "idx_runs_public_id",
        "trg_runs_reply_task_insert", "trg_runs_reply_task_update",
        "trg_runs_meeting_task_insert", "trg_runs_meeting_task_update",
        "trg_runs_logical_parent_insert", "trg_runs_logical_parent_update",
    }
    statements = [sql for name, sql in source.items() if name != "runs" and name not in retired]
    statements += [
        "create unique index idx_runs_public_id on runs(cast(legacy_key as integer),record_type,case when record_type='agent_runs' then coalesce(source_type,'agent_runs') else '' end)",
        "create index idx_logical_runs_task on runs(task_id,source_type,cast(legacy_key as integer)) where record_type='agent_runs' and source_type is not null",
        "create unique index idx_logical_runs_active_task on runs(task_id) where record_type='agent_runs' and source_type in ('task_agent_runs','meeting_alignment_runs') and status='running'",
        "create index idx_logical_runs_source_created on runs(source_type,created_at,cast(legacy_key as integer)) where record_type='agent_runs' and source_type is not null",
    ]
    for event, suffix in (("insert", "insert"), ("update of task_id,job_id,source_type", "update")):
        clauses = ["(new.source_type is null and record_type<>'reply_tasks')"]
        clauses += [f"(new.source_type='{source}' and record_type<>'{task}')" for source, task in SOURCE_TASKS.items()]
        invalid = "exists(select 1 from tasks where id=new.task_id and (" + " or ".join(clauses) + "))"
        invalid += " or (new.source_type='meeting_alignment_runs' and ((exists(select 1 from tasks where id=new.task_id) and new.job_id is not null) or (not exists(select 1 from tasks where id=new.task_id) and new.job_id is null)))"
        statements.append(f"create trigger trg_logical_runs_task_{suffix} before {event} on runs when new.record_type='agent_runs' and ({invalid}) begin select raise(abort,'logical run task mapping mismatch'); end")
    for event, suffix in (("insert", "insert"), ("update of parent_id,record_type", "update")):
        statements.append(f"create trigger trg_runs_logical_parent_{suffix} before {event} on runs when new.parent_id is not null and exists(select 1 from runs where id=new.parent_id and (record_type<>'agent_runs' or source_type is not null)) begin select raise(abort,'run parent must be a logical agent run'); end")
    statements.append("create trigger trg_logical_runs_source_immutable before update of source_type on runs when old.record_type='agent_runs' and new.source_type is not old.source_type begin select raise(abort,'logical run source is immutable'); end")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(LOGICAL_RUN_TABLE_DDL)
        for statement in statements:
            db.execute(statement)
        return dict(db.execute("select name,sql from sqlite_schema where tbl_name='runs' and sql is not null"))


@lru_cache(maxsize=2)
def logical_action_objects(reply_variant):
    """The eight original role-run reference guards, with their old type boundary."""
    from app import action_storage as actions
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=True, result_references=True, execution_owner=True,
        sent_result_references=True, shared_lifecycle=True, specialized_claims=True,
        postfix_owner=True, logical_owner=True, **ACTION_OPTIONS)
