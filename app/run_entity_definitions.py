"""Pinned, unactivated Run-side entity contract for Task/Run Phase 2.

This module contains target DDL and a source-field crosswalk only. It does not
import or call the legacy Runs schema factory and performs no database IO.
"""
from __future__ import annotations

import sqlite3


RUN_FIELDS = (
    ("id", "INTEGER", True, None), ("task_id", "INTEGER", False, None),
    ("parent_id", "INTEGER", False, None), ("execution_generation", "TEXT", False, None),
    ("role", "TEXT", False, None), ("proposal_revision", "INTEGER", False, None),
    ("turn_attempt", "INTEGER", False, None), ("operation_id", "TEXT", False, None),
    ("status", "TEXT", False, None), ("session_id", "TEXT", False, None),
    ("transcript_start", "INTEGER", False, None), ("transcript_end", "INTEGER", False, None),
    ("structured_error_json", "TEXT", False, None), ("error", "TEXT", False, None),
    ("lease_owner", "TEXT", False, None), ("lease_expires_at", "TEXT", False, None),
    ("started_at", "TEXT", False, None), ("completed_at", "TEXT", False, None),
    ("created_at", "TEXT", True, "current_timestamp"), ("updated_at", "TEXT", False, None),
)

TABLES = {
    "runs": """CREATE TABLE runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER REFERENCES tasks(id),
 parent_id INTEGER REFERENCES runs(id), execution_generation TEXT, role TEXT,
 proposal_revision INTEGER, turn_attempt INTEGER, operation_id TEXT, status TEXT,
 session_id TEXT, transcript_start INTEGER, transcript_end INTEGER,
 structured_error_json TEXT, error TEXT, lease_owner TEXT, lease_expires_at TEXT,
 started_at TEXT, completed_at TEXT, created_at TEXT NOT NULL DEFAULT current_timestamp,
 updated_at TEXT)""",
    "run_provenance": """CREATE TABLE run_provenance (
 run_id INTEGER PRIMARY KEY REFERENCES runs(id), source_type TEXT NOT NULL
 CHECK(source_type IN ('task_agent_runs','meeting_alignment_runs','okr_review_runs')), job_id INTEGER)""",
    "run_projections": """CREATE TABLE run_projections (
 run_id INTEGER PRIMARY KEY REFERENCES runs(id), projection_json TEXT NOT NULL)""",
    "runtime_attempts": """CREATE TABLE runtime_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER REFERENCES runs(id),
 workload_kind TEXT NOT NULL, workload_key TEXT NOT NULL, attempt_number INTEGER NOT NULL,
 route_name TEXT NOT NULL, runtime_kind TEXT NOT NULL, credential_mode TEXT NOT NULL,
 model TEXT NOT NULL, session_mode TEXT NOT NULL, source_session_id TEXT NOT NULL,
 attempt_purpose TEXT NOT NULL, validation_retry_policy_id TEXT NOT NULL,
 validation_result_schema_id TEXT NOT NULL, session_id TEXT NOT NULL,
 transcript_reference TEXT NOT NULL, transcript_start INTEGER NOT NULL,
 transcript_end INTEGER NOT NULL, status TEXT NOT NULL, failure_class TEXT NOT NULL,
 failure_code TEXT NOT NULL, failover_permitted INTEGER NOT NULL, lease_owner TEXT NOT NULL,
 lease_expires_at TEXT NOT NULL, first_effect_started_at TEXT NOT NULL,
 result_schema_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(workload_kind,workload_key,attempt_number),
 CHECK(attempt_number>0),
 CHECK(status IN ('starting','running','completed','failed','superseded')),
 CHECK((workload_kind='agent_run' AND parent_id IS NOT NULL AND workload_key=CAST(parent_id AS TEXT)) OR
       workload_kind<>'agent_run'),
 CHECK((session_mode='fresh' AND source_session_id='') OR
       (session_mode='resume' AND trim(source_session_id)<>'')),
 CHECK((attempt_purpose='normal' AND validation_retry_policy_id='' AND validation_result_schema_id='') OR
       (attempt_purpose='result_validation_correction' AND trim(validation_retry_policy_id)<>'' AND trim(validation_result_schema_id)<>'')),
 CHECK(workload_kind='agent_run' OR status NOT IN ('starting','running') OR
       (trim(lease_owner)<>'' AND trim(lease_expires_at)<>'')))""",
    "run_failures": """CREATE TABLE run_failures (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES runs(id),
 phase TEXT NOT NULL, structured_error_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "schedule_occurrences": """CREATE TABLE schedule_occurrences (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scheduled_task_id INTEGER NOT NULL REFERENCES schedules(id),
 trigger_kind TEXT NOT NULL, scheduled_for TEXT NOT NULL, dispatch_status TEXT NOT NULL,
 skip_or_error_reason TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, execution_kind TEXT NOT NULL, execution_id TEXT NOT NULL,
 dispatched_at TEXT, first_scheduled_for TEXT NOT NULL, occurrence_count INTEGER NOT NULL,
 result_summary TEXT NOT NULL, snapshot_id INTEGER REFERENCES configs(schedule_snapshot_public_id),
 lease_owner TEXT NOT NULL, lease_expires_at TEXT, created_at TEXT NOT NULL,
 CHECK(trigger_kind IN ('scheduled','manual')),
 CHECK(dispatch_status IN ('pending','dispatched','skipped','failed')))""",
    "workbench_events": """CREATE TABLE workbench_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
 sequence INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(task_id,sequence))""",
    "codex_session_search_index": """CREATE TABLE codex_session_search_index (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE,
 source_type TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "email_classifier_runtime_samples": """CREATE TABLE email_classifier_runtime_samples (
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL CHECK(trim(model_id)<>''),
 outcome TEXT NOT NULL CHECK(outcome IN ('success','rejected','failure')),
 fallback_code TEXT NOT NULL DEFAULT '' CHECK(length(fallback_code)<=64),
 cache_hit INTEGER NOT NULL CHECK(cache_hit IN (0,1)),
 runtime_warm INTEGER NOT NULL CHECK(runtime_warm IN (0,1)),
 queue_ms REAL NOT NULL CHECK(queue_ms>=0), http_ms REAL NOT NULL CHECK(http_ms>=0),
 embedding_ms REAL NOT NULL CHECK(embedding_ms>=0), head_ms REAL NOT NULL CHECK(head_ms>=0),
 total_ms REAL NOT NULL CHECK(total_ms>=0), created_at TEXT NOT NULL CHECK(trim(created_at)<>''))""",
    "setup_wizard_events": """CREATE TABLE setup_wizard_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, step_id TEXT NOT NULL, action_id TEXT NOT NULL,
 status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}',
 stdout_excerpt TEXT NOT NULL DEFAULT '', stderr_excerpt TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT current_timestamp, finished_at TEXT NOT NULL DEFAULT current_timestamp)""",
    "run_entity_identity_map": """CREATE TABLE run_entity_identity_map (
 source_kind TEXT NOT NULL, physical_id INTEGER NOT NULL, public_key TEXT,
 public_key_type TEXT NOT NULL, public_key_order INTEGER, target_id INTEGER NOT NULL,
 allocator_highwater INTEGER, PRIMARY KEY(source_kind,physical_id),
 UNIQUE(source_kind,target_id),
 CHECK(public_key_type IN ('null','integer','text','blob')))""",
}

INDEXES = {
    "idx_runs_turn_identity": "CREATE UNIQUE INDEX idx_runs_turn_identity ON runs(task_id,execution_generation,role,proposal_revision,turn_attempt)",
    "idx_runs_status_updated": "CREATE INDEX idx_runs_status_updated ON runs(status,updated_at)",
    "idx_runs_task": "CREATE INDEX idx_runs_task ON runs(task_id,id)",
    "idx_runs_parent": "CREATE INDEX idx_runs_parent ON runs(parent_id,id)",
    "idx_runtime_attempts_active_route": "CREATE UNIQUE INDEX idx_runtime_attempts_active_route ON runtime_attempts(workload_kind,workload_key,route_name) WHERE status IN ('starting','running')",
    "idx_runtime_attempts_active_lease": "CREATE INDEX idx_runtime_attempts_active_lease ON runtime_attempts(status,lease_expires_at) WHERE workload_kind<>'agent_run' AND status IN ('starting','running')",
    "idx_runtime_attempts_run_order": "CREATE INDEX idx_runtime_attempts_run_order ON runtime_attempts(parent_id,attempt_number)",
    "idx_run_failures_run": "CREATE INDEX idx_run_failures_run ON run_failures(run_id,id)",
    "idx_schedule_occurrences_claim": "CREATE INDEX idx_schedule_occurrences_claim ON schedule_occurrences(dispatch_status,scheduled_for,id)",
    "idx_schedule_occurrences_dispatch": "CREATE INDEX idx_schedule_occurrences_dispatch ON schedule_occurrences(dispatch_status,lease_expires_at,scheduled_for,id) WHERE dispatch_status='pending'",
    "idx_schedule_occurrences_latest": "CREATE INDEX idx_schedule_occurrences_latest ON schedule_occurrences(scheduled_task_id,id DESC)",
    "idx_schedule_occurrences_reply_execution": "CREATE INDEX idx_schedule_occurrences_reply_execution ON schedule_occurrences(CAST(execution_id AS INTEGER),scheduled_task_id,id) WHERE execution_kind='reply_task'",
    "idx_schedule_occurrences_scheduled_instant": "CREATE UNIQUE INDEX idx_schedule_occurrences_scheduled_instant ON schedule_occurrences(scheduled_task_id,scheduled_for) WHERE trigger_kind='scheduled'",
    "idx_workbench_events_id_turn": "CREATE INDEX idx_workbench_events_id_turn ON workbench_events(id,task_id)",
    "idx_workbench_events_task": "CREATE INDEX idx_workbench_events_task ON workbench_events(task_id,id)",
    "idx_workbench_events_type": "CREATE INDEX idx_workbench_events_type ON workbench_events(event_type)",
    "idx_codex_session_source": "CREATE INDEX idx_codex_session_source ON codex_session_search_index(source_type,source_id)",
    "idx_codex_session_source_id": "CREATE INDEX idx_codex_session_source_id ON codex_session_search_index(source_type,source_id,id DESC)",
    "idx_codex_session_id": "CREATE INDEX idx_codex_session_id ON codex_session_search_index(id DESC)",
    "idx_email_samples_id": "CREATE INDEX idx_email_samples_id ON email_classifier_runtime_samples(id DESC)",
    "idx_email_samples_model": "CREATE INDEX idx_email_samples_model ON email_classifier_runtime_samples(model_id,id DESC)",
    "idx_setup_events_id": "CREATE INDEX idx_setup_events_id ON setup_wizard_events(id DESC)",
    "idx_setup_events_step": "CREATE INDEX idx_setup_events_step ON setup_wizard_events(step_id,id)",
}

SOURCE_FIELDS = {
    "agent_runs": ("task_id parent_id execution_generation role proposal_revision turn_attempt operation_id status session_id transcript_start transcript_end structured_error_json error lease_owner lease_expires_at started_at completed_at created_at updated_at projection_json job_id source_type",),
    "agent_runtime_attempts": ("parent_id status session_id transcript_start transcript_end lease_owner lease_expires_at started_at finished_at created_at updated_at workload_kind workload_key attempt_number route_name runtime_kind credential_mode model session_mode source_session_id attempt_purpose validation_retry_policy_id validation_result_schema_id failure_class failure_code failover_permitted transcript_reference first_effect_started_at result_schema_id",),
    "agent_run_state_events": ("parent_id structured_error_json created_at phase",),
    "scheduled_task_runs": ("lease_owner lease_expires_at created_at event_id scheduled_task_id trigger_kind scheduled_for dispatch_status skip_or_error_reason execution_kind execution_id dispatched_at first_scheduled_for occurrence_count result_summary snapshot_id",),
    "workbench_events": ("task_id created_at sequence event_type payload_json",),
    "codex_session_search_index": ("session_id created_at updated_at source_type source_id title",),
    "email_classifier_runtime_samples": ("created_at model_id outcome fallback_code cache_hit runtime_warm queue_ms http_ms embedding_ms head_ms total_ms",),
    "setup_wizard_events": ("status finished_at created_at step_id action_id summary evidence_json stdout_excerpt stderr_excerpt",),
}

SOURCE_DECLARED_FIELDS = tuple("""id record_type legacy_key task_id parent_id execution_generation role proposal_revision turn_attempt operation_id status session_id transcript_start transcript_end structured_error_json lease_owner lease_expires_at started_at completed_at finished_at created_at updated_at workload_kind workload_key attempt_number route_name runtime_kind credential_mode model session_mode source_session_id attempt_purpose validation_retry_policy_id validation_result_schema_id failure_class failure_code failover_permitted transcript_reference first_effect_started_at result_schema_id phase error projection_json job_id event_id scheduled_task_id trigger_kind scheduled_for dispatch_status skip_or_error_reason execution_kind execution_id dispatched_at first_scheduled_for occurrence_count result_summary snapshot_id sequence event_type payload_json source_type source_id title model_id outcome fallback_code cache_hit runtime_warm queue_ms http_ms embedding_ms head_ms total_ms step_id action_id summary evidence_json stdout_excerpt stderr_excerpt""".split())
_INTEGER_FIELDS = frozenset("id task_id parent_id proposal_revision turn_attempt transcript_start transcript_end attempt_number failover_permitted job_id scheduled_task_id occurrence_count snapshot_id sequence cache_hit runtime_warm".split())
_REAL_FIELDS = frozenset("queue_ms http_ms embedding_ms head_ms total_ms".split())


def _source_metadata(field):
    sql_type = "INTEGER" if field in _INTEGER_FIELDS else "REAL" if field in _REAL_FIELDS else "TEXT"
    null_contract = "PRIMARY KEY" if field == "id" else "NOT NULL" if field in {"record_type", "created_at"} else "NULL allowed"
    default = "current_timestamp" if field == "created_at" else None
    return sql_type, null_contract, default


def run_entity_objects() -> dict[str, str]:
    guards = {
        "trg_run_provenance_task_kind_insert": """CREATE TRIGGER trg_run_provenance_task_kind_insert BEFORE INSERT ON run_provenance
WHEN (NEW.source_type='task_agent_runs' AND NOT EXISTS (
 SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
 WHERE r.id=NEW.run_id AND t.kind='work_summary'))
 OR (NEW.source_type='meeting_alignment_runs' AND (
 EXISTS (SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
         WHERE r.id=NEW.run_id AND (t.kind<>'meeting_alignment' OR NEW.job_id IS NOT NULL))
 OR (NOT EXISTS (SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
                 WHERE r.id=NEW.run_id) AND NEW.job_id IS NULL)))
 OR (NEW.source_type='okr_review_runs' AND NOT EXISTS (
 SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
 WHERE r.id=NEW.run_id AND t.kind='okr_review'))
BEGIN SELECT RAISE(ABORT,'Run source and Task kind mismatch'); END""",
        "trg_run_provenance_source_immutable": "CREATE TRIGGER trg_run_provenance_source_immutable BEFORE UPDATE OF source_type ON run_provenance WHEN NEW.source_type IS NOT OLD.source_type BEGIN SELECT RAISE(ABORT,'Run provenance source is immutable'); END",
        "trg_run_provenance_job_mapping_update": """CREATE TRIGGER trg_run_provenance_job_mapping_update BEFORE UPDATE OF job_id ON run_provenance
WHEN (NEW.source_type='meeting_alignment_runs' AND NOT (
 EXISTS (SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
         WHERE r.id=NEW.run_id AND t.kind='meeting_alignment') AND NEW.job_id IS NULL
 OR (NOT EXISTS (SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
                 WHERE r.id=NEW.run_id) AND NEW.job_id IS NOT NULL)))
 OR (NEW.source_type<>'meeting_alignment_runs' AND NEW.job_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'Run source job mapping mismatch'); END""",
        "trg_runs_task_kind_update": """CREATE TRIGGER trg_runs_task_kind_update BEFORE UPDATE OF task_id ON runs
WHEN EXISTS (SELECT 1 FROM run_provenance p WHERE p.run_id=NEW.id AND (
 (p.source_type='task_agent_runs' AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.kind='work_summary')) OR
 (p.source_type='meeting_alignment_runs' AND NOT (
   (EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.kind='meeting_alignment') AND p.job_id IS NULL) OR
   (NEW.task_id IS NULL AND p.job_id IS NOT NULL))) OR
 (p.source_type='okr_review_runs' AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.kind='okr_review'))))
BEGIN SELECT RAISE(ABORT,'Run source and Task kind mismatch'); END""",
        "trg_run_provenance_active_task_unique": "CREATE TRIGGER trg_run_provenance_active_task_unique BEFORE INSERT ON run_provenance WHEN NEW.source_type IN ('task_agent_runs','meeting_alignment_runs') AND EXISTS (SELECT 1 FROM runs current JOIN tasks t ON t.id=current.task_id JOIN runs other ON other.task_id=current.task_id WHERE current.id=NEW.run_id AND current.status='running' AND t.kind IN ('work_summary','meeting_alignment') AND other.id<>current.id AND other.status='running') BEGIN SELECT RAISE(ABORT,'active logical Run already exists for Task'); END",
        "trg_runs_parent_logical_insert": "CREATE TRIGGER trg_runs_parent_logical_insert BEFORE INSERT ON runs WHEN NEW.parent_id IS NOT NULL AND EXISTS (SELECT 1 FROM run_provenance WHERE run_id=NEW.parent_id) BEGIN SELECT RAISE(ABORT,'Run parent must be logical'); END",
        "trg_runs_parent_logical_update": "CREATE TRIGGER trg_runs_parent_logical_update BEFORE UPDATE OF parent_id ON runs WHEN NEW.parent_id IS NOT NULL AND EXISTS (SELECT 1 FROM run_provenance WHERE run_id=NEW.parent_id) BEGIN SELECT RAISE(ABORT,'Run parent must be logical'); END",
        "trg_run_provenance_not_parent": "CREATE TRIGGER trg_run_provenance_not_parent BEFORE INSERT ON run_provenance WHEN EXISTS (SELECT 1 FROM runs WHERE parent_id=NEW.run_id) BEGIN SELECT RAISE(ABORT,'professional Run cannot have children'); END",
        "trg_runs_active_task_insert": "CREATE TRIGGER trg_runs_active_task_insert BEFORE INSERT ON runs WHEN NEW.status='running' AND NEW.task_id IS NOT NULL AND EXISTS (SELECT 1 FROM tasks t JOIN runs r ON r.task_id=t.id WHERE t.id=NEW.task_id AND t.kind IN ('work_summary','meeting_alignment') AND r.status='running') BEGIN SELECT RAISE(ABORT,'active logical Run already exists for Task'); END",
        "trg_runs_active_task_update": "CREATE TRIGGER trg_runs_active_task_update BEFORE UPDATE OF task_id,status ON runs WHEN NEW.status='running' AND NEW.task_id IS NOT NULL AND EXISTS (SELECT 1 FROM tasks t JOIN runs r ON r.task_id=t.id WHERE t.id=NEW.task_id AND t.kind IN ('work_summary','meeting_alignment') AND r.id<>NEW.id AND r.status='running') BEGIN SELECT RAISE(ABORT,'active logical Run already exists for Task'); END",
        "trg_workbench_events_task_kind_insert": "CREATE TRIGGER trg_workbench_events_task_kind_insert BEFORE INSERT ON workbench_events WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id AND kind='workbench_turn') BEGIN SELECT RAISE(ABORT,'Workbench event requires Workbench Task'); END",
        "trg_workbench_events_task_kind_update": "CREATE TRIGGER trg_workbench_events_task_kind_update BEFORE UPDATE OF task_id ON workbench_events WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id AND kind='workbench_turn') BEGIN SELECT RAISE(ABORT,'Workbench event requires Workbench Task'); END",
        "trg_runtime_attempt_lineage_immutable": "CREATE TRIGGER trg_runtime_attempt_lineage_immutable BEFORE UPDATE OF attempt_purpose,validation_retry_policy_id,validation_result_schema_id ON runtime_attempts WHEN NEW.attempt_purpose IS NOT OLD.attempt_purpose OR NEW.validation_retry_policy_id IS NOT OLD.validation_retry_policy_id OR NEW.validation_result_schema_id IS NOT OLD.validation_result_schema_id BEGIN SELECT RAISE(ABORT,'runtime attempt correction lineage is immutable'); END",
    }
    return {**TABLES, **INDEXES, **guards}


def run_entity_field_map() -> list[dict]:
    owners = {
        "agent_runs": {"projection_json": ("run_projections", "projection_json"), "source_type": ("run_provenance", "source_type"), "job_id": ("run_provenance", "job_id")},
        "agent_runtime_attempts": {field: ("runtime_attempts", field) for field in SOURCE_FIELDS["agent_runtime_attempts"][0].split()},
        "agent_run_state_events": {"parent_id": ("run_failures", "run_id"), "phase": ("run_failures", "phase"), "structured_error_json": ("run_failures", "structured_error_json"), "created_at": ("run_failures", "created_at")},
        "scheduled_task_runs": {field: ("schedule_occurrences", field) for field in SOURCE_FIELDS["scheduled_task_runs"][0].split()},
        "workbench_events": {field: ("workbench_events", field) for field in SOURCE_FIELDS["workbench_events"][0].split()},
        "codex_session_search_index": {field: ("codex_session_search_index", field) for field in SOURCE_FIELDS["codex_session_search_index"][0].split()},
        "email_classifier_runtime_samples": {field: ("email_classifier_runtime_samples", "created_at" if field == "created_at" else field) for field in SOURCE_FIELDS["email_classifier_runtime_samples"][0].split()},
        "setup_wizard_events": {field: ("setup_wizard_events", field) for field in SOURCE_FIELDS["setup_wizard_events"][0].split()},
    }
    primary = {"agent_runs": "runs", "agent_runtime_attempts": "runtime_attempts", "agent_run_state_events": "run_failures", "scheduled_task_runs": "schedule_occurrences", "workbench_events": "workbench_events", "codex_session_search_index": "codex_session_search_index", "email_classifier_runtime_samples": "email_classifier_runtime_samples", "setup_wizard_events": "setup_wizard_events"}
    rows = []
    for source, fields in SOURCE_FIELDS.items():
        present = set(fields[0].split())
        for field in ("id", "record_type", "legacy_key", *SOURCE_DECLARED_FIELDS[3:]):
            if field == "id":
                owner, target, target_field = "physical row identity", primary[source], "id"
            elif field == "record_type":
                owner, target, target_field = "source-kind migration discriminator", "run_entity_identity_map", "source_kind"
            elif field == "legacy_key":
                owner, target, target_field = "typed public identity and allocator", "run_entity_identity_map", "public_key"
            elif field in owners[source] and field in present:
                owner = ("typed Task/Meeting workload key or source fact"
                         if source == "agent_runtime_attempts" and field == "parent_id"
                         else "source fact")
                target, target_field = owners[source][field]
            elif source == "agent_runs" and field in present:
                owner, target, target_field = "logical Run", "runs", field
            elif field in present:
                target = primary[source]
                target_field = {"parent_id": "run_id", "record_type": "source_kind"}.get(field, "created_at" if field == "recorded_at" else field)
                owner = "logical Run" if source == "agent_runs" else "source fact"
            else:
                owner, target, target_field = "source CHECK requires NULL for this kind", None, None
            rows.append({"source_kind": source, "source_field": field, "target_owner": owner, "target_table": target, "target_field": target_field,
                         "retirement_reason": "field is structurally NULL for this source kind" if field not in present and field not in {"id", "record_type", "legacy_key"} else None,
                         "migration_metadata_only": field in {"record_type", "legacy_key"},
                         "identity_order_semantics": "SQLite CAST(public_key AS INTEGER) where source index ordered by legacy_key" if field == "legacy_key" else None,
                         "sql_type": _source_metadata(field)[0], "null_contract": _source_metadata(field)[1],
                         "source_default": _source_metadata(field)[2]})
    return rows


def validate_run_entity_contract(mapping=None) -> None:
    rows = run_entity_field_map() if mapping is None else list(mapping)
    from app.logical_run_definitions import logical_run_objects

    source_db = sqlite3.connect(":memory:")
    source_db.execute(logical_run_objects()["runs"])
    source_columns = {row[1]: row for row in source_db.execute("PRAGMA table_info(runs)")}
    source_fields = tuple(source_columns)
    expected = {(source, field) for source in SOURCE_FIELDS for field in source_fields}
    seen = [(row.get("source_kind"), row.get("source_field")) for row in rows]
    if len(seen) != len(set(seen)) or set(seen) != expected:
        raise ValueError("source Run field coverage is duplicate, unknown, or incomplete")
    generated = {(row["source_kind"], row["source_field"]): row for row in run_entity_field_map()}
    for row in rows:
        key = (row["source_kind"], row["source_field"])
        expected_row = generated[key]
        if any(row.get(field) != expected_row[field] for field in (
                "target_owner", "target_table", "target_field", "retirement_reason",
                "migration_metadata_only", "identity_order_semantics")):
            raise ValueError(f"target mapping mismatch: {key}")
        _, source_name, source_type, not_null, default, primary_key = source_columns[row["source_field"]]
        source_metadata = (
            source_type.upper(),
            "PRIMARY KEY" if primary_key else "NOT NULL" if not_null else "NULL allowed",
            default,
        )
        actual_metadata = (row.get("sql_type"), row.get("null_contract"), row.get("source_default"))
        if source_name != row["source_field"] or actual_metadata != source_metadata:
            raise ValueError(f"source declaration metadata mismatch: {(row['source_kind'], row['source_field'])}")
    db = sqlite3.connect(":memory:")
    from app.task_run_entity_definitions import task_run_entity_objects
    task_objects = task_run_entity_objects()
    run_objects = run_entity_objects()
    for ddl in (*task_objects.values(), *run_objects.values()):
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    from app.configs_storage import create_configs_schema
    from app.schedules_storage import create_schedules_schema
    create_schedules_schema(db)
    create_configs_schema(db)
    for ddl in (*task_objects.values(), *run_objects.values()):
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    target_columns = {
        name: {column[1] for column in db.execute(f'PRAGMA table_info("{name}")')}
        for name, in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    }
    for row in rows:
        if row["target_table"] is None:
            if not row.get("retirement_reason"):
                raise ValueError(f"unmapped source field: {(row['source_kind'], row['source_field'])}")
            continue
        if row["target_table"] not in target_columns:
            raise ValueError(f"unknown target owner: {row['target_table']}")
        if row["target_field"] not in target_columns[row["target_table"]]:
            raise ValueError(f"unknown target field: {row['target_table']}.{row['target_field']}")
