CREATE TABLE tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, status TEXT NOT NULL,
 available_at TEXT, attempts INTEGER, error TEXT, locked_at TEXT, lease_owner TEXT,
 lease_expires_at TEXT, owner_pid INTEGER, generation INTEGER, adapter_name TEXT,
 execution_generation TEXT, current_input_id INTEGER, claimed_input_id INTEGER,
 input_version INTEGER, claimed_input_version INTEGER, parameters_json BLOB NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(kind IN ('reply','work_summary','meeting_alignment','okr_review','okr_analysis','workbench_turn','email_classification','feedback_processing','wechat_import')),
 FOREIGN KEY(current_input_id) REFERENCES task_inputs(id),
 FOREIGN KEY(claimed_input_id) REFERENCES task_inputs(id),
 CHECK(typeof(parameters_json)='blob' AND json_valid(parameters_json,8)));

CREATE TABLE task_inputs (
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
 channel TEXT NOT NULL, conversation_id TEXT NOT NULL, conversation_title TEXT NOT NULL, single_chat INTEGER NOT NULL,
 trigger_message_id TEXT NOT NULL, trigger_create_time TEXT NOT NULL, trigger_sender TEXT NOT NULL, trigger_text TEXT NOT NULL,
 trigger_message_json TEXT NOT NULL DEFAULT '{}', oa_url TEXT NOT NULL DEFAULT '', business_object_key TEXT NOT NULL,
 input_revision_key TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT current_timestamp,
 input_provenance_json TEXT NOT NULL DEFAULT '{}', input_compacted INTEGER NOT NULL DEFAULT 0,
 UNIQUE(task_id,id), UNIQUE(channel,conversation_id,trigger_message_id,input_revision_key),
 CHECK(channel IS NOT NULL AND conversation_id IS NOT NULL AND trigger_message_id IS NOT NULL AND input_revision_key IS NOT NULL));

CREATE TABLE task_business_keys (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id), business_key TEXT NOT NULL UNIQUE, created_at TEXT, updated_at TEXT);

CREATE TABLE workbench_turns (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), workbench_task_id INTEGER NOT NULL REFERENCES workbench_tasks(id), client_request_id TEXT NOT NULL UNIQUE, task_sequence INTEGER NOT NULL, user_text TEXT NOT NULL, stop_requested INTEGER NOT NULL, error_code TEXT NOT NULL, error_detail TEXT NOT NULL, resume_context TEXT NOT NULL, execution_run_id TEXT NOT NULL, runtime_quiesced_run_id TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT NOT NULL, UNIQUE(workbench_task_id,task_sequence));

CREATE TABLE task_results (task_id INTEGER NOT NULL REFERENCES tasks(id), result_kind TEXT NOT NULL, decision_json TEXT, target_kind TEXT, target_id TEXT, target_title TEXT, mentions_json TEXT, final_message TEXT, send_result_json TEXT, calendar_summary_status TEXT, calendar_summary_result_json TEXT, result_json TEXT, finished_at TEXT, PRIMARY KEY(task_id,result_kind));

CREATE TABLE email_classification_inputs (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), channel TEXT NOT NULL, stable_message_identity TEXT NOT NULL UNIQUE, input_json TEXT NOT NULL);

CREATE TABLE work_summary_inputs (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), source_type TEXT NOT NULL, source_ref TEXT NOT NULL, payload_json TEXT NOT NULL, source_created_at TEXT NOT NULL DEFAULT '', body_sha256 TEXT NOT NULL DEFAULT '', body_bytes INTEGER NOT NULL DEFAULT 0, body_compacted INTEGER NOT NULL DEFAULT 0);

CREATE TABLE feedback_processing_items (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), feedback_key TEXT UNIQUE, batch_id TEXT NOT NULL, current_round_id INTEGER NOT NULL);

CREATE TABLE task_run_identity_map (source_kind TEXT NOT NULL, physical_id INTEGER NOT NULL, public_key TEXT, public_key_type TEXT NOT NULL, target_id INTEGER NOT NULL, PRIMARY KEY(source_kind,physical_id), UNIQUE(source_kind,target_id), CHECK(public_key_type IN ('null','integer','text','blob')));

CREATE TABLE workbench_tasks (id INTEGER PRIMARY KEY,"title" TEXT NOT NULL,"runtime_kind" TEXT NOT NULL,"provider_session_ref" TEXT NOT NULL DEFAULT '',"archived_at" TEXT NOT NULL DEFAULT '',"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp);

CREATE TABLE email_historical_candidates (id INTEGER PRIMARY KEY,"account_id" TEXT NOT NULL,"folder" TEXT NOT NULL,"model_id" TEXT NOT NULL,"stable_message_identity" TEXT NOT NULL,"normalized_text" TEXT NOT NULL,"provider_message_json" TEXT NOT NULL,"uidvalidity" INTEGER NOT NULL,"uid" INTEGER NOT NULL,"state" TEXT NOT NULL,"reason" TEXT NOT NULL DEFAULT '',"attempted_at" TEXT NOT NULL DEFAULT '',"next_retry_at" TEXT NOT NULL DEFAULT '',"updated_at" TEXT NOT NULL);

CREATE TABLE feedback_iteration_decisions (id INTEGER PRIMARY KEY,"batch_id" TEXT NOT NULL REFERENCES feedback_processing_batches(batch_id),"workbench_task_id" TEXT NOT NULL,"workbench_turn_id" TEXT NOT NULL,"decision_json" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp);

CREATE TABLE feedback_processing_batches (id INTEGER PRIMARY KEY,"batch_id" TEXT,"status" TEXT NOT NULL DEFAULT 'pending',"requested_count" INTEGER NOT NULL DEFAULT 0,"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp,"resolved_at" TEXT NOT NULL DEFAULT '');

CREATE TABLE feedback_processing_rounds (id INTEGER PRIMARY KEY,"feedback_key" TEXT NOT NULL REFERENCES feedback_processing_items(feedback_key),"round_number" INTEGER NOT NULL,"batch_id" TEXT NOT NULL DEFAULT '',"status" TEXT NOT NULL,"workbench_task_id" TEXT NOT NULL DEFAULT '',"workbench_turn_id" TEXT NOT NULL DEFAULT '',"attempt_id" INTEGER NOT NULL DEFAULT 0,"agent_run_id" INTEGER NOT NULL DEFAULT 0,"commit_sha" TEXT NOT NULL DEFAULT '',"test_evidence_json" TEXT NOT NULL DEFAULT '{}',"restart_evidence_json" TEXT NOT NULL DEFAULT '{}',"health_evidence_json" TEXT NOT NULL DEFAULT '{}',"backlog_evidence_json" TEXT NOT NULL DEFAULT '{}',"receipt_version" INTEGER NOT NULL DEFAULT 1,"note" TEXT NOT NULL DEFAULT '',"started_at" TEXT NOT NULL DEFAULT '',"resolved_at" TEXT NOT NULL DEFAULT '',"reopened_at" TEXT NOT NULL DEFAULT '',"reopen_reason" TEXT NOT NULL DEFAULT '',"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp,"scope_receipt_json" TEXT NOT NULL DEFAULT '{}');

CREATE TABLE oa_notification_events (id INTEGER PRIMARY KEY,"event_kind" TEXT NOT NULL,"requires_reply" INTEGER NOT NULL DEFAULT 0,"process_instance_id" TEXT NOT NULL DEFAULT '',"task_id" TEXT NOT NULL DEFAULT '',"channel" TEXT NOT NULL DEFAULT 'dingtalk',"conversation_id" TEXT NOT NULL,"conversation_title" TEXT NOT NULL DEFAULT '',"trigger_message_id" TEXT NOT NULL,"trigger_create_time" TEXT NOT NULL DEFAULT '',"trigger_sender" TEXT NOT NULL DEFAULT '',"trigger_text" TEXT NOT NULL DEFAULT '',"trigger_message_json" TEXT NOT NULL DEFAULT '{}',"oa_url" TEXT NOT NULL DEFAULT '',"status" TEXT NOT NULL DEFAULT 'pending',"claimed_attempt_id" INTEGER NOT NULL DEFAULT 0,"result_attempt_id" INTEGER NOT NULL DEFAULT 0,"last_error" TEXT NOT NULL DEFAULT '',"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp,"sent_at" TEXT NOT NULL DEFAULT '');

CREATE TABLE okr_review_items (id INTEGER PRIMARY KEY,"request_id" INTEGER NOT NULL REFERENCES tasks(id),"objective_title" TEXT NOT NULL,"objective_weight" REAL NOT NULL DEFAULT 0,"kr_title" TEXT NOT NULL,"kr_weight" REAL NOT NULL DEFAULT 0,"item_json" TEXT NOT NULL DEFAULT '{}',"created_at" TEXT NOT NULL DEFAULT current_timestamp);

CREATE TABLE workbench_artifacts (id INTEGER PRIMARY KEY,"workbench_turn_id" INTEGER NOT NULL REFERENCES workbench_turns(task_id),"label" TEXT NOT NULL,"path" TEXT NOT NULL,"media_type" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp);

CREATE TABLE workbench_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT,"workbench_task_id" INTEGER NOT NULL REFERENCES workbench_tasks(id),"client_request_id" TEXT NOT NULL,"filename" TEXT NOT NULL,"media_type" TEXT NOT NULL,"size_bytes" INTEGER NOT NULL,"content_sha256" TEXT NOT NULL,"storage_path" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp);

CREATE INDEX idx_tasks_claim_due ON tasks(kind,status,available_at,id);

CREATE UNIQUE INDEX idx_work_summary_source_identity ON work_summary_inputs(source_type,source_ref);

CREATE UNIQUE INDEX idx_meeting_source_identity ON tasks(CASE WHEN json_type(parameters_json,'$.meeting_id')='object' AND json_extract(parameters_json,'$.meeting_id.t')='blob' THEN unhex(json_extract(parameters_json,'$.meeting_id.v')) ELSE jsonb_extract(parameters_json,'$.meeting_id') END) WHERE kind='meeting_alignment';

CREATE UNIQUE INDEX idx_okr_review_source_identity ON tasks(CASE WHEN json_type(parameters_json,'$.conversation_id')='object' AND json_extract(parameters_json,'$.conversation_id.t')='blob' THEN unhex(json_extract(parameters_json,'$.conversation_id.v')) ELSE jsonb_extract(parameters_json,'$.conversation_id') END,CASE WHEN json_type(parameters_json,'$.trigger_message_id')='object' AND json_extract(parameters_json,'$.trigger_message_id.t')='blob' THEN unhex(json_extract(parameters_json,'$.trigger_message_id.v')) ELSE jsonb_extract(parameters_json,'$.trigger_message_id') END) WHERE kind='okr_review';

CREATE UNIQUE INDEX idx_weekly_analysis_source_identity ON tasks(CASE WHEN json_type(parameters_json,'$.week_end')='object' AND json_extract(parameters_json,'$.week_end.t')='blob' THEN unhex(json_extract(parameters_json,'$.week_end.v')) ELSE jsonb_extract(parameters_json,'$.week_end') END,CASE WHEN json_type(parameters_json,'$.manager_user_id')='object' AND json_extract(parameters_json,'$.manager_user_id.t')='blob' THEN unhex(json_extract(parameters_json,'$.manager_user_id.v')) ELSE jsonb_extract(parameters_json,'$.manager_user_id') END,CASE WHEN json_type(parameters_json,'$.source_digest')='object' AND json_extract(parameters_json,'$.source_digest.t')='blob' THEN unhex(json_extract(parameters_json,'$.source_digest.v')) ELSE jsonb_extract(parameters_json,'$.source_digest') END) WHERE kind='okr_analysis';

CREATE UNIQUE INDEX idx_email_historical_candidates_account_id_folder_model_id_stable_message_identity_identity ON email_historical_candidates(account_id,folder,model_id,stable_message_identity);

CREATE INDEX idx_feedback_iteration_decisions_batch ON feedback_iteration_decisions(batch_id,id);

CREATE UNIQUE INDEX idx_feedback_processing_batches_batch_id_identity ON feedback_processing_batches(batch_id);

CREATE INDEX idx_feedback_processing_items_batch ON feedback_processing_items(batch_id);

CREATE INDEX idx_feedback_processing_rounds_batch ON feedback_processing_rounds(batch_id);

CREATE UNIQUE INDEX idx_feedback_processing_rounds_feedback_key_batch_id_identity ON feedback_processing_rounds(feedback_key,batch_id);

CREATE UNIQUE INDEX idx_feedback_processing_rounds_feedback_key_round_number_identity ON feedback_processing_rounds(feedback_key,round_number);

CREATE INDEX idx_oa_notification_events_claim ON oa_notification_events(status,claimed_attempt_id,id);

CREATE INDEX idx_oa_notification_events_case_status ON oa_notification_events(process_instance_id,task_id,requires_reply,status,id);

CREATE UNIQUE INDEX idx_oa_notification_events_channel_conversation_id_trigger_message_id_identity ON oa_notification_events(channel,conversation_id,trigger_message_id);

CREATE INDEX idx_workbench_artifacts_turn_created_id ON workbench_artifacts(workbench_turn_id,created_at,id);

CREATE INDEX idx_workbench_artifacts_created_id_turn ON workbench_artifacts(created_at,id,workbench_turn_id);

CREATE UNIQUE INDEX idx_workbench_attachments_task_request ON workbench_attachments(workbench_task_id,client_request_id);

CREATE INDEX idx_workbench_attachments_task_created_id ON workbench_attachments(workbench_task_id,created_at,id);

CREATE INDEX idx_tasks_email_classification_status ON tasks(status,available_at,lease_expires_at,id) WHERE kind='email_classification';

CREATE INDEX idx_tasks_feedback_processing_status ON tasks(status,id) WHERE kind='feedback_processing';

CREATE INDEX idx_tasks_wechat_import_status ON tasks(status,id) WHERE kind='wechat_import';

CREATE INDEX idx_tasks_workbench_turn_status_created ON tasks(status,created_at,id) WHERE kind='workbench_turn';

CREATE INDEX idx_tasks_workbench_turn_status_lease ON tasks(status,lease_expires_at) WHERE kind='workbench_turn';

CREATE INDEX idx_tasks_workbench_turn_created ON tasks(created_at,id) WHERE kind='workbench_turn';

CREATE INDEX idx_workbench_tasks_updated ON workbench_tasks(updated_at,id);

CREATE TRIGGER trg_tasks_parameters_reply_insert BEFORE INSERT ON tasks WHEN NEW.kind='reply' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('force_new_decision','manual_rerun_attempt_id','manual_rerun_revision_key','recovery_code'))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_reply_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='reply' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('force_new_decision','manual_rerun_attempt_id','manual_rerun_revision_key','recovery_code'))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_work_summary_insert BEFORE INSERT ON tasks WHEN NEW.kind='work_summary' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_work_summary_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='work_summary' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_meeting_alignment_insert BEFORE INSERT ON tasks WHEN NEW.kind='meeting_alignment' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('eligible_at','ended_at','meeting_id','participants_json','source_json','title'))  OR json_type(NEW.parameters_json,'$.meeting_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_meeting_alignment_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='meeting_alignment' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('eligible_at','ended_at','meeting_id','participants_json','source_json','title'))  OR json_type(NEW.parameters_json,'$.meeting_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_okr_review_insert BEFORE INSERT ON tasks WHEN NEW.kind='okr_review' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('codex_session_id','conversation_id','conversation_title','okr_source_json','period_end','period_label','period_start','trigger_message_id','trigger_sender','trigger_sender_user_id','trigger_text'))  OR json_type(NEW.parameters_json,'$.conversation_id') IS NULL OR json_type(NEW.parameters_json,'$.conversation_title') IS NULL OR json_type(NEW.parameters_json,'$.period_end') IS NULL OR json_type(NEW.parameters_json,'$.period_label') IS NULL OR json_type(NEW.parameters_json,'$.period_start') IS NULL OR json_type(NEW.parameters_json,'$.trigger_message_id') IS NULL OR json_type(NEW.parameters_json,'$.trigger_sender') IS NULL OR json_type(NEW.parameters_json,'$.trigger_text') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_okr_review_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='okr_review' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('codex_session_id','conversation_id','conversation_title','okr_source_json','period_end','period_label','period_start','trigger_message_id','trigger_sender','trigger_sender_user_id','trigger_text'))  OR json_type(NEW.parameters_json,'$.conversation_id') IS NULL OR json_type(NEW.parameters_json,'$.conversation_title') IS NULL OR json_type(NEW.parameters_json,'$.period_end') IS NULL OR json_type(NEW.parameters_json,'$.period_label') IS NULL OR json_type(NEW.parameters_json,'$.period_start') IS NULL OR json_type(NEW.parameters_json,'$.trigger_message_id') IS NULL OR json_type(NEW.parameters_json,'$.trigger_sender') IS NULL OR json_type(NEW.parameters_json,'$.trigger_text') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_okr_analysis_insert BEFORE INSERT ON tasks WHEN NEW.kind='okr_analysis' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('manager_user_id','source_digest','week_end'))  OR json_type(NEW.parameters_json,'$.manager_user_id') IS NULL OR json_type(NEW.parameters_json,'$.source_digest') IS NULL OR json_type(NEW.parameters_json,'$.week_end') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_okr_analysis_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='okr_analysis' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('manager_user_id','source_digest','week_end'))  OR json_type(NEW.parameters_json,'$.manager_user_id') IS NULL OR json_type(NEW.parameters_json,'$.source_digest') IS NULL OR json_type(NEW.parameters_json,'$.week_end') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_email_classification_insert BEFORE INSERT ON tasks WHEN NEW.kind='email_classification' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_email_classification_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='email_classification' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_feedback_processing_insert BEFORE INSERT ON tasks WHEN NEW.kind='feedback_processing' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_feedback_processing_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='feedback_processing' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_wechat_import_insert BEFORE INSERT ON tasks WHEN NEW.kind='wechat_import' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('account_id','import_run_id'))  OR json_type(NEW.parameters_json,'$.account_id') IS NULL OR json_type(NEW.parameters_json,'$.import_run_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_wechat_import_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='wechat_import' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('account_id','import_run_id'))  OR json_type(NEW.parameters_json,'$.account_id') IS NULL OR json_type(NEW.parameters_json,'$.import_run_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_workbench_turn_insert BEFORE INSERT ON tasks WHEN NEW.kind='workbench_turn' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_tasks_parameters_workbench_turn_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='workbench_turn' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END;

CREATE TRIGGER trg_reply_current_identity_insert
      BEFORE UPDATE OF current_input_id ON tasks
      WHEN NEW.kind='reply' AND NEW.current_input_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM tasks other JOIN task_inputs old_input ON old_input.id=other.current_input_id
        JOIN task_inputs new_input ON new_input.id=NEW.current_input_id
        WHERE other.kind='reply' AND other.id<>NEW.id
          AND old_input.channel=new_input.channel
          AND old_input.conversation_id=new_input.conversation_id
          AND old_input.trigger_message_id=new_input.trigger_message_id)
      BEGIN SELECT RAISE(ABORT,'duplicate current reply identity'); END;

CREATE TRIGGER trg_reply_current_identity_input_update
      BEFORE UPDATE OF channel,conversation_id,trigger_message_id ON task_inputs
      WHEN EXISTS (
        SELECT 1 FROM tasks owner JOIN tasks other ON other.kind='reply' AND other.id<>owner.id
        JOIN task_inputs old_input ON old_input.id=other.current_input_id
        WHERE owner.kind='reply' AND owner.current_input_id=NEW.id
          AND old_input.channel=NEW.channel AND old_input.conversation_id=NEW.conversation_id
          AND old_input.trigger_message_id=NEW.trigger_message_id)
      BEGIN SELECT RAISE(ABORT,'duplicate current reply identity'); END;

CREATE TRIGGER trg_workbench_active_turn_insert
BEFORE INSERT ON workbench_turns
WHEN (SELECT status FROM tasks WHERE id=NEW.task_id) IN ('queued','running')
 AND EXISTS (SELECT 1 FROM workbench_turns active JOIN tasks active_task ON active_task.id=active.task_id
             WHERE active.workbench_task_id=NEW.workbench_task_id
               AND active_task.status IN ('queued','running'))
BEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END;

CREATE TRIGGER trg_workbench_active_turn_update
BEFORE UPDATE OF workbench_task_id ON workbench_turns
WHEN (SELECT status FROM tasks WHERE id=NEW.task_id) IN ('queued','running')
 AND EXISTS (SELECT 1 FROM workbench_turns active JOIN tasks active_task ON active_task.id=active.task_id
             WHERE active.workbench_task_id=NEW.workbench_task_id AND active.task_id<>NEW.task_id
               AND active_task.status IN ('queued','running'))
BEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END;

CREATE TRIGGER trg_workbench_active_turn_status_update
BEFORE UPDATE OF status ON tasks
WHEN NEW.kind='workbench_turn' AND NEW.status IN ('queued','running')
 AND EXISTS (SELECT 1 FROM workbench_turns turn
             JOIN workbench_turns active ON active.workbench_task_id=turn.workbench_task_id
             JOIN tasks active_task ON active_task.id=active.task_id
             WHERE turn.task_id=NEW.id AND active.task_id<>NEW.id
               AND active_task.status IN ('queued','running'))
BEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END;

CREATE TRIGGER trg_workbench_active_turn_task_id_update
BEFORE UPDATE OF task_id ON workbench_turns
WHEN (SELECT status FROM tasks WHERE id=NEW.task_id) IN ('queued','running')
 AND EXISTS (SELECT 1 FROM workbench_turns active JOIN tasks active_task ON active_task.id=active.task_id
             WHERE active.workbench_task_id=OLD.workbench_task_id AND active.task_id<>OLD.task_id
               AND active_task.status IN ('queued','running'))
BEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END;

CREATE TRIGGER trg_tasks_kind_immutable BEFORE UPDATE OF kind ON tasks WHEN NEW.kind IS NOT OLD.kind BEGIN SELECT RAISE(ABORT,'task record type is immutable'); END;

CREATE TRIGGER trg_okr_review_items_request_kind_insert BEFORE INSERT ON okr_review_items WHEN NOT EXISTS(SELECT 1 FROM tasks WHERE id=NEW.request_id AND kind='okr_review') BEGIN SELECT RAISE(ABORT,'OKR item parent must be an OKR review Task'); END;

CREATE TRIGGER trg_okr_review_items_request_kind_update BEFORE UPDATE OF request_id ON okr_review_items WHEN NOT EXISTS(SELECT 1 FROM tasks WHERE id=NEW.request_id AND kind='okr_review') BEGIN SELECT RAISE(ABORT,'OKR item parent must be an OKR review Task'); END;
