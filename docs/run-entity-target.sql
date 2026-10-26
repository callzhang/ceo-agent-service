CREATE TABLE runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER REFERENCES tasks(id),
 parent_id INTEGER REFERENCES runs(id), execution_generation TEXT, role TEXT,
 proposal_revision INTEGER, turn_attempt INTEGER, operation_id TEXT, status TEXT,
 session_id TEXT, transcript_start INTEGER, transcript_end INTEGER,
 structured_error_json TEXT, error TEXT, lease_owner TEXT, lease_expires_at TEXT,
 started_at TEXT, completed_at TEXT, created_at TEXT NOT NULL DEFAULT current_timestamp,
 updated_at TEXT);

CREATE TABLE run_provenance (
 run_id INTEGER PRIMARY KEY REFERENCES runs(id), source_type TEXT NOT NULL
 CHECK(source_type IN ('task_agent_runs','meeting_alignment_runs','okr_review_runs')), job_id INTEGER);

CREATE TABLE run_projections (
 run_id INTEGER PRIMARY KEY REFERENCES runs(id), projection_json TEXT NOT NULL);

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
 CHECK(attempt_number>0),
 CHECK(status IN ('starting','running','completed','failed','superseded')),
 CHECK((workload_kind='agent_run' AND parent_id IS NOT NULL AND workload_key=CAST(parent_id AS TEXT)) OR
       workload_kind<>'agent_run'),
 CHECK((session_mode='fresh' AND source_session_id='') OR
       (session_mode='resume' AND trim(source_session_id)<>'')),
 CHECK((attempt_purpose='normal' AND validation_retry_policy_id='' AND validation_result_schema_id='') OR
       (attempt_purpose='result_validation_correction' AND trim(validation_retry_policy_id)<>'' AND trim(validation_result_schema_id)<>'')),
 CHECK(workload_kind='agent_run' OR status NOT IN ('starting','running') OR
       (trim(lease_owner)<>'' AND trim(lease_expires_at)<>'')));

CREATE TABLE run_failures (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES runs(id),
 phase TEXT NOT NULL, structured_error_json TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE schedule_occurrences (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scheduled_task_id INTEGER NOT NULL REFERENCES schedules(id),
 trigger_kind TEXT NOT NULL, scheduled_for TEXT NOT NULL, dispatch_status TEXT NOT NULL,
 skip_or_error_reason TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, execution_kind TEXT NOT NULL, execution_id TEXT NOT NULL,
 dispatched_at TEXT, first_scheduled_for TEXT NOT NULL, occurrence_count INTEGER NOT NULL,
 result_summary TEXT NOT NULL, snapshot_id INTEGER REFERENCES configs(schedule_snapshot_public_id),
 lease_owner TEXT NOT NULL, lease_expires_at TEXT, created_at TEXT NOT NULL,
 CHECK(trigger_kind IN ('scheduled','manual')),
 CHECK(dispatch_status IN ('pending','dispatched','skipped','failed')));

CREATE TABLE workbench_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
 sequence INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(task_id,sequence));

CREATE TABLE codex_session_search_index (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE,
 source_type TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE email_classifier_runtime_samples (
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL CHECK(trim(model_id)<>''),
 outcome TEXT NOT NULL CHECK(outcome IN ('success','rejected','failure')),
 fallback_code TEXT NOT NULL DEFAULT '' CHECK(length(fallback_code)<=64),
 cache_hit INTEGER NOT NULL CHECK(cache_hit IN (0,1)),
 runtime_warm INTEGER NOT NULL CHECK(runtime_warm IN (0,1)),
 queue_ms REAL NOT NULL CHECK(queue_ms>=0), http_ms REAL NOT NULL CHECK(http_ms>=0),
 embedding_ms REAL NOT NULL CHECK(embedding_ms>=0), head_ms REAL NOT NULL CHECK(head_ms>=0),
 total_ms REAL NOT NULL CHECK(total_ms>=0), created_at TEXT NOT NULL CHECK(trim(created_at)<>''));

CREATE TABLE setup_wizard_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, step_id TEXT NOT NULL, action_id TEXT NOT NULL,
 status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}',
 stdout_excerpt TEXT NOT NULL DEFAULT '', stderr_excerpt TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT current_timestamp, finished_at TEXT NOT NULL DEFAULT current_timestamp);

CREATE TABLE run_entity_identity_map (
 source_kind TEXT NOT NULL, physical_id INTEGER NOT NULL, public_key TEXT,
 public_key_type TEXT NOT NULL, public_key_order INTEGER, target_id INTEGER NOT NULL,
 allocator_highwater INTEGER, PRIMARY KEY(source_kind,physical_id),
 UNIQUE(source_kind,target_id),
 CHECK(public_key_type IN ('null','integer','text','blob')));

CREATE UNIQUE INDEX idx_runs_turn_identity ON runs(task_id,execution_generation,role,proposal_revision,turn_attempt);

CREATE INDEX idx_runs_status_updated ON runs(status,updated_at);

CREATE INDEX idx_runs_task ON runs(task_id,id);

CREATE INDEX idx_runs_parent ON runs(parent_id,id);

CREATE UNIQUE INDEX idx_runtime_attempts_active_route ON runtime_attempts(workload_kind,workload_key,route_name) WHERE status IN ('starting','running');

CREATE INDEX idx_runtime_attempts_active_lease ON runtime_attempts(status,lease_expires_at) WHERE workload_kind<>'agent_run' AND status IN ('starting','running');

CREATE INDEX idx_runtime_attempts_run_order ON runtime_attempts(parent_id,attempt_number);

CREATE INDEX idx_run_failures_run ON run_failures(run_id,id);

CREATE INDEX idx_schedule_occurrences_claim ON schedule_occurrences(dispatch_status,scheduled_for,id);

CREATE INDEX idx_schedule_occurrences_dispatch ON schedule_occurrences(dispatch_status,lease_expires_at,scheduled_for,id) WHERE dispatch_status='pending';

CREATE INDEX idx_schedule_occurrences_latest ON schedule_occurrences(scheduled_task_id,id DESC);

CREATE INDEX idx_schedule_occurrences_reply_execution ON schedule_occurrences(CAST(execution_id AS INTEGER),scheduled_task_id,id) WHERE execution_kind='reply_task';

CREATE UNIQUE INDEX idx_schedule_occurrences_scheduled_instant ON schedule_occurrences(scheduled_task_id,scheduled_for) WHERE trigger_kind='scheduled';

CREATE INDEX idx_schedule_occurrences_task_chronology ON schedule_occurrences(scheduled_task_id,julianday(scheduled_for),id);

CREATE INDEX idx_schedule_occurrences_covered_range ON schedule_occurrences(scheduled_task_id,snapshot_id,julianday(first_scheduled_for),julianday(scheduled_for),id) WHERE trigger_kind='scheduled' AND occurrence_count>1;

CREATE INDEX idx_workbench_events_id_turn ON workbench_events(id,task_id);

CREATE INDEX idx_workbench_events_task ON workbench_events(task_id,id);

CREATE INDEX idx_workbench_events_type ON workbench_events(event_type);

CREATE INDEX idx_codex_session_source ON codex_session_search_index(source_type,source_id);

CREATE INDEX idx_codex_session_source_id ON codex_session_search_index(source_type,source_id,id DESC);

CREATE INDEX idx_codex_session_id ON codex_session_search_index(id DESC);

CREATE INDEX idx_email_samples_id ON email_classifier_runtime_samples(id DESC);

CREATE INDEX idx_email_samples_model ON email_classifier_runtime_samples(model_id,id DESC);

CREATE INDEX idx_setup_events_id ON setup_wizard_events(id DESC);

CREATE INDEX idx_setup_events_step ON setup_wizard_events(step_id,id);

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
BEGIN SELECT RAISE(ABORT,'Run source and Task kind mismatch'); END;

CREATE TRIGGER trg_run_provenance_source_immutable BEFORE UPDATE OF source_type ON run_provenance WHEN NEW.source_type IS NOT OLD.source_type BEGIN SELECT RAISE(ABORT,'Run provenance source is immutable'); END;

CREATE TRIGGER trg_run_provenance_job_mapping_update BEFORE UPDATE OF job_id ON run_provenance
WHEN (NEW.source_type='meeting_alignment_runs' AND NOT (
 EXISTS (SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
         WHERE r.id=NEW.run_id AND t.kind='meeting_alignment') AND NEW.job_id IS NULL
 OR (NOT EXISTS (SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
                 WHERE r.id=NEW.run_id) AND NEW.job_id IS NOT NULL)))
 OR (NEW.source_type<>'meeting_alignment_runs' AND NEW.job_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'Run source job mapping mismatch'); END;

CREATE TRIGGER trg_runs_task_kind_update BEFORE UPDATE OF task_id ON runs
WHEN EXISTS (SELECT 1 FROM run_provenance p WHERE p.run_id=NEW.id AND (
 (p.source_type='task_agent_runs' AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.kind='work_summary')) OR
 (p.source_type='meeting_alignment_runs' AND NOT (
   (EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.kind='meeting_alignment') AND p.job_id IS NULL) OR
   (NEW.task_id IS NULL AND p.job_id IS NOT NULL))) OR
 (p.source_type='okr_review_runs' AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.kind='okr_review'))))
BEGIN SELECT RAISE(ABORT,'Run source and Task kind mismatch'); END;

CREATE TRIGGER trg_run_provenance_active_task_unique BEFORE INSERT ON run_provenance WHEN NEW.source_type IN ('task_agent_runs','meeting_alignment_runs') AND EXISTS (SELECT 1 FROM runs current JOIN tasks t ON t.id=current.task_id JOIN runs other ON other.task_id=current.task_id WHERE current.id=NEW.run_id AND current.status='running' AND t.kind IN ('work_summary','meeting_alignment') AND other.id<>current.id AND other.status='running') BEGIN SELECT RAISE(ABORT,'active logical Run already exists for Task'); END;

CREATE TRIGGER trg_runs_parent_logical_insert BEFORE INSERT ON runs WHEN NEW.parent_id IS NOT NULL AND EXISTS (SELECT 1 FROM run_provenance WHERE run_id=NEW.parent_id) BEGIN SELECT RAISE(ABORT,'Run parent must be logical'); END;

CREATE TRIGGER trg_runs_parent_logical_update BEFORE UPDATE OF parent_id ON runs WHEN NEW.parent_id IS NOT NULL AND EXISTS (SELECT 1 FROM run_provenance WHERE run_id=NEW.parent_id) BEGIN SELECT RAISE(ABORT,'Run parent must be logical'); END;

CREATE TRIGGER trg_run_provenance_not_parent BEFORE INSERT ON run_provenance WHEN EXISTS (SELECT 1 FROM runs WHERE parent_id=NEW.run_id) BEGIN SELECT RAISE(ABORT,'professional Run cannot have children'); END;

CREATE TRIGGER trg_runs_active_task_insert BEFORE INSERT ON runs WHEN NEW.status='running' AND NEW.task_id IS NOT NULL AND EXISTS (SELECT 1 FROM tasks t JOIN runs r ON r.task_id=t.id WHERE t.id=NEW.task_id AND t.kind IN ('work_summary','meeting_alignment') AND r.status='running') BEGIN SELECT RAISE(ABORT,'active logical Run already exists for Task'); END;

CREATE TRIGGER trg_runs_active_task_update BEFORE UPDATE OF task_id,status ON runs WHEN NEW.status='running' AND NEW.task_id IS NOT NULL AND EXISTS (SELECT 1 FROM tasks t JOIN runs r ON r.task_id=t.id WHERE t.id=NEW.task_id AND t.kind IN ('work_summary','meeting_alignment') AND r.id<>NEW.id AND r.status='running') BEGIN SELECT RAISE(ABORT,'active logical Run already exists for Task'); END;

CREATE TRIGGER trg_workbench_events_task_kind_insert BEFORE INSERT ON workbench_events WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id AND kind='workbench_turn') BEGIN SELECT RAISE(ABORT,'Workbench event requires Workbench Task'); END;

CREATE TRIGGER trg_workbench_events_task_kind_update BEFORE UPDATE OF task_id ON workbench_events WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id AND kind='workbench_turn') BEGIN SELECT RAISE(ABORT,'Workbench event requires Workbench Task'); END;

CREATE TRIGGER trg_runtime_attempt_lineage_immutable BEFORE UPDATE OF attempt_purpose,validation_retry_policy_id,validation_result_schema_id ON runtime_attempts WHEN NEW.attempt_purpose IS NOT OLD.attempt_purpose OR NEW.validation_retry_policy_id IS NOT OLD.validation_retry_policy_id OR NEW.validation_result_schema_id IS NOT OLD.validation_result_schema_id BEGIN SELECT RAISE(ABORT,'runtime attempt correction lineage is immutable'); END;
