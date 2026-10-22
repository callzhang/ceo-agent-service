CREATE TABLE runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER REFERENCES tasks(id),
 parent_id INTEGER REFERENCES runs(id), execution_generation TEXT, role TEXT,
 proposal_revision INTEGER, turn_attempt INTEGER, operation_id TEXT, status TEXT,
 session_id TEXT, transcript_start INTEGER, transcript_end INTEGER,
 structured_error_json TEXT, error TEXT, lease_owner TEXT, lease_expires_at TEXT,
 started_at TEXT, completed_at TEXT, created_at TEXT NOT NULL DEFAULT current_timestamp,
 updated_at TEXT)
CREATE TABLE run_provenance (
 run_id INTEGER PRIMARY KEY REFERENCES runs(id), source_type TEXT NOT NULL
 CHECK(source_type IN ('task_agent_runs','meeting_alignment_runs','okr_review_runs')), job_id INTEGER)
CREATE TABLE run_adopted_results (
 run_id INTEGER PRIMARY KEY REFERENCES runs(id), projection_json TEXT NOT NULL)
CREATE TABLE runtime_attempts (
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
 CHECK((session_mode='fresh' AND source_session_id='') OR
       (session_mode='resume' AND source_session_id<>'')),
 CHECK(parent_id IS NOT NULL OR status NOT IN ('starting','running') OR
       (trim(lease_owner)<>'' AND trim(lease_expires_at)<>'')))
CREATE TABLE run_failures (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES runs(id),
 phase TEXT, structured_error_json TEXT, created_at TEXT NOT NULL DEFAULT current_timestamp)
CREATE TABLE schedule_occurrences (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scheduled_task_id INTEGER NOT NULL REFERENCES schedules(id),
 trigger_kind TEXT NOT NULL, scheduled_for TEXT, dispatch_status TEXT NOT NULL,
 skip_or_error_reason TEXT, event_id TEXT UNIQUE, execution_kind TEXT, execution_id TEXT,
 dispatched_at TEXT, first_scheduled_for TEXT, occurrence_count INTEGER,
 result_summary TEXT, snapshot_id INTEGER REFERENCES configs(schedule_snapshot_public_id),
 lease_owner TEXT, lease_expires_at TEXT, created_at TEXT NOT NULL,
 CHECK(trigger_kind IN ('scheduled','manual')),
 CHECK(dispatch_status IN ('pending','dispatched','skipped','failed')))
CREATE TABLE workbench_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
 sequence INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(task_id,sequence))
CREATE TABLE codex_session_search_index (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE,
 source_type TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL)
CREATE TABLE email_classifier_runtime_samples (
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL CHECK(trim(model_id)<>''),
 outcome TEXT NOT NULL CHECK(outcome IN ('success','rejected','failure')),
 fallback_code TEXT NOT NULL DEFAULT '' CHECK(length(fallback_code)<=64),
 cache_hit INTEGER NOT NULL CHECK(cache_hit IN (0,1)),
 runtime_warm INTEGER NOT NULL CHECK(runtime_warm IN (0,1)),
 queue_ms REAL NOT NULL CHECK(queue_ms>=0), http_ms REAL NOT NULL CHECK(http_ms>=0),
 embedding_ms REAL NOT NULL CHECK(embedding_ms>=0), head_ms REAL NOT NULL CHECK(head_ms>=0),
 total_ms REAL NOT NULL CHECK(total_ms>=0), created_at TEXT NOT NULL CHECK(trim(created_at)<>''))
CREATE TABLE setup_wizard_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, step_id TEXT NOT NULL, action_id TEXT NOT NULL,
 status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}',
 stdout_excerpt TEXT NOT NULL DEFAULT '', stderr_excerpt TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT current_timestamp, finished_at TEXT NOT NULL DEFAULT current_timestamp)
CREATE TABLE run_entity_identity_map (
 source_kind TEXT NOT NULL, physical_id INTEGER NOT NULL, public_key TEXT,
 public_key_type TEXT NOT NULL, public_key_order INTEGER, target_id INTEGER NOT NULL,
 allocator_highwater INTEGER, PRIMARY KEY(source_kind,physical_id),
 UNIQUE(source_kind,target_id),
 CHECK(public_key_type IN ('null','integer','text','blob')))
CREATE UNIQUE INDEX idx_runs_turn_identity ON runs(task_id,execution_generation,role,proposal_revision,turn_attempt)
CREATE INDEX idx_runs_status_updated ON runs(status,updated_at)
CREATE INDEX idx_runs_task ON runs(task_id,id)
CREATE INDEX idx_runs_parent ON runs(parent_id,id)
CREATE UNIQUE INDEX idx_runtime_attempts_active_route ON runtime_attempts(workload_kind,workload_key,route_name) WHERE status IN ('starting','running')
CREATE INDEX idx_runtime_attempts_active_lease ON runtime_attempts(status,lease_expires_at) WHERE parent_id IS NULL AND status IN ('starting','running')
CREATE INDEX idx_runtime_attempts_run_order ON runtime_attempts(parent_id,attempt_number)
CREATE INDEX idx_run_failures_run ON run_failures(run_id,id)
CREATE INDEX idx_schedule_occurrences_claim ON schedule_occurrences(dispatch_status,scheduled_for,id)
CREATE INDEX idx_schedule_occurrences_dispatch ON schedule_occurrences(dispatch_status,lease_expires_at,scheduled_for,id) WHERE dispatch_status='pending'
CREATE INDEX idx_schedule_occurrences_latest ON schedule_occurrences(scheduled_task_id,id DESC)
CREATE INDEX idx_schedule_occurrences_reply_execution ON schedule_occurrences(CAST(execution_id AS INTEGER),scheduled_task_id,id) WHERE execution_kind='reply_task'
CREATE UNIQUE INDEX idx_schedule_occurrences_scheduled_instant ON schedule_occurrences(scheduled_task_id,scheduled_for) WHERE trigger_kind='scheduled'
CREATE INDEX idx_workbench_events_id_turn ON workbench_events(id,task_id)
CREATE INDEX idx_workbench_events_task ON workbench_events(task_id,id)
CREATE INDEX idx_workbench_events_type ON workbench_events(event_type)
CREATE INDEX idx_codex_session_source ON codex_session_search_index(source_type,source_id)
CREATE INDEX idx_codex_session_source_id ON codex_session_search_index(source_type,source_id,id DESC)
CREATE INDEX idx_codex_session_id ON codex_session_search_index(id DESC)
CREATE INDEX idx_email_samples_id ON email_classifier_runtime_samples(id DESC)
CREATE INDEX idx_email_samples_model ON email_classifier_runtime_samples(model_id,id DESC)
CREATE INDEX idx_setup_events_id ON setup_wizard_events(id DESC)
CREATE INDEX idx_setup_events_step ON setup_wizard_events(step_id,id)
CREATE TRIGGER trg_run_provenance_task_kind_insert BEFORE INSERT ON run_provenance
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
BEGIN SELECT RAISE(ABORT,'Run source and Task kind mismatch'); END
CREATE TRIGGER trg_run_provenance_immutable BEFORE UPDATE ON run_provenance BEGIN SELECT RAISE(ABORT,'Run provenance is immutable'); END
CREATE TRIGGER trg_workbench_events_task_kind_insert BEFORE INSERT ON workbench_events WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id AND kind='workbench_turn') BEGIN SELECT RAISE(ABORT,'Workbench event requires Workbench Task'); END
CREATE TRIGGER trg_workbench_events_task_kind_update BEFORE UPDATE OF task_id ON workbench_events WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id AND kind='workbench_turn') BEGIN SELECT RAISE(ABORT,'Workbench event requires Workbench Task'); END
CREATE TRIGGER trg_runtime_attempt_lineage_immutable BEFORE UPDATE OF parent_id,workload_kind,workload_key,attempt_number,session_mode,source_session_id,session_id,transcript_reference,transcript_start,transcript_end ON runtime_attempts WHEN NEW.parent_id IS NOT OLD.parent_id OR NEW.workload_kind IS NOT OLD.workload_kind OR NEW.workload_key IS NOT OLD.workload_key OR NEW.attempt_number IS NOT OLD.attempt_number OR NEW.session_mode IS NOT OLD.session_mode OR NEW.source_session_id IS NOT OLD.source_session_id OR NEW.session_id IS NOT OLD.session_id OR NEW.transcript_reference IS NOT OLD.transcript_reference OR NEW.transcript_start IS NOT OLD.transcript_start OR NEW.transcript_end IS NOT OLD.transcript_end BEGIN SELECT RAISE(ABORT,'runtime attempt lineage is immutable'); END
