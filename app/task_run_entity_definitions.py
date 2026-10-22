"""Pure, unactivated Task/Run target contract.

The frozen Task20 declarations remain the source oracle. This module describes
the target shape and a field-by-field crosswalk; it performs no database IO.
"""
from __future__ import annotations

import sqlite3



_TASK_TABLES = {
    "reply_tasks": "reply", "work_summary_inputs": "work_summary",
    "meeting_alignment_jobs": "meeting_alignment", "okr_review_requests": "okr_review",
    "weekly_okr_analysis_jobs": "okr_analysis", "email_agent_classification_tasks": "email_classification",
    "feedback_processing_items": "feedback_processing", "wechat_memory_import_jobs": "wechat_import",
    "workbench_turns": "workbench_turn",
}
_DOMAIN_TARGETS = {
    "reply_task_inputs": "task_inputs", "business_object_tasks": "task_business_keys",
    "meeting_alignment_jobs": "tasks", "workbench_tasks": "workbench_tasks",
    "workbench_turns": "workbench_turns", "email_agent_classification_tasks": "tasks",
    "work_summary_inputs": "work_summary_inputs", "email_historical_candidates": "email_historical_candidates",
    "feedback_iteration_decisions": "feedback_iteration_decisions",
    "feedback_processing_batches": "feedback_processing_batches",
    "feedback_processing_items": "feedback_processing_items",
    "feedback_processing_rounds": "feedback_processing_rounds",
    "oa_notification_events": "oa_notification_events", "okr_review_items": "okr_review_items",
    "workbench_artifacts": "workbench_artifacts", "workbench_attachments": "workbench_attachments",
    "weekly_okr_analysis_jobs": "tasks", "wechat_memory_import_jobs": "tasks",
    "okr_review_requests": "tasks", "reply_tasks": "tasks",
}
_REPLY_PARAMETERS = frozenset({"force_new_decision", "manual_rerun_attempt_id", "manual_rerun_revision_key", "recovery_code"})
_MEETING_RESULT_FIELDS = frozenset({"decision_json", "target_kind", "target_id", "target_title", "mentions_json", "final_message", "send_result_json", "calendar_summary_status", "calendar_summary_result_json"})
_MEETING_REQUEST_FIELDS = frozenset({"meeting_id", "title", "source_json", "participants_json", "ended_at", "eligible_at"})
_WORKBENCH_DOMAIN_FIELDS = frozenset({"task_id", "client_request_id", "task_sequence", "user_text",
    "stop_requested", "error_code", "error_detail", "resume_context",
    "execution_run_id", "runtime_quiesced_run_id", "started_at", "completed_at"})
_REQUEST_FIELDS = {
    "reply_tasks": _REPLY_PARAMETERS,
    "meeting_alignment_jobs": _MEETING_REQUEST_FIELDS,
    "okr_review_requests": frozenset({"conversation_id", "conversation_title", "trigger_message_id", "trigger_sender", "trigger_sender_user_id", "trigger_text", "period_label", "period_start", "period_end", "okr_source_json", "codex_session_id"}),
    "weekly_okr_analysis_jobs": frozenset({"week_end", "manager_user_id", "source_digest"}),
    "wechat_memory_import_jobs": frozenset({"import_run_id", "account_id"}),
}
_TASK_DOMAIN_FIELDS = {
    "workbench_turns": _WORKBENCH_DOMAIN_FIELDS,
    "feedback_processing_items": frozenset({"feedback_key", "batch_id", "workbench_task_id", "workbench_turn_id", "attempt_id", "agent_run_id", "commit_sha", "test_evidence_json", "restart_evidence_json", "health_evidence_json", "note", "resolved_at", "current_round_id"}),
}
_INPUT_FIELDS = {
    "reply_tasks": frozenset({"channel", "conversation_id", "conversation_title", "single_chat",
        "trigger_message_id", "trigger_create_time", "trigger_sender", "oa_url", "business_object_key"}),
    "reply_task_inputs": frozenset({"channel", "conversation_id", "conversation_title", "single_chat",
        "trigger_message_id", "trigger_create_time", "trigger_sender", "trigger_text",
        "trigger_message_json", "oa_url", "business_object_key", "input_revision_key",
        "input_provenance_json", "input_compacted"}),
}
_RETIRED = {
    ("reply_tasks", "current_input_id"): "replaced by tasks.current_input_id FK",
    ("reply_tasks", "claimed_input_id"): "replaced by tasks.claimed_input_id FK",
    ("business_object_tasks", "reply_task_id"): "replaced by task_business_keys.task_id FK",
    ("reply_task_inputs", "reply_task_id"): "replaced by task_inputs.task_id FK",
    ("workbench_turns", "task_id"): "retained as the typed parent reference to workbench_tasks",
    ("workbench_artifacts", "turn_id"): "retained as domain FK in workbench_artifacts",
    ("workbench_attachments", "task_id"): "retained as domain FK in workbench_attachments",
}


def _sql_type(affinity: str) -> str:
    return affinity.upper()


def _task_kind(source: str) -> str:
    if source == "reply_tasks":
        return "reply"
    return _TASK_TABLES.get(source, "domain")


def _field_map() -> tuple[dict, ...]:
    from app import task20_definitions as task20
    from app import task_lifecycle_definitions as lifecycle

    rows = []
    for table, columns in task20.TASK20_COLUMNS.items():
        for name, affinity, required, default, storage in columns:
            retired = _RETIRED.get((table, name))
            if (table, name) in {("reply_tasks", "current_input_id"), ("reply_tasks", "claimed_input_id"), ("workbench_turns", "task_id")}:
                retired = None
            if table == "reply_tasks" and name in _INPUT_FIELDS[table]:
                owner, target, expression = "Reply TaskInput read projection", "task_inputs", name
            elif table == "reply_tasks" and name in _REPLY_PARAMETERS:
                owner, target, expression = "Task request parameters", "tasks", f"parameters_json.$.{name}"
            elif table == "meeting_alignment_jobs" and name in _MEETING_RESULT_FIELDS:
                owner, target, expression = "TaskResult", "task_results", name
            elif table == "meeting_alignment_jobs" and name in _MEETING_REQUEST_FIELDS:
                owner, target, expression = "Task request parameters", "tasks", f"parameters_json.$.{name}"
            elif table in _REQUEST_FIELDS and name in _REQUEST_FIELDS[table]:
                owner, target, expression = "Task request parameters", "tasks", f"parameters_json.$.{name}"
            elif table == "email_agent_classification_tasks" and name in {"input_json", "channel", "stable_message_identity"}:
                owner, target, expression = "EmailClassificationInput", "email_classification_inputs", name
            elif table == "email_agent_classification_tasks" and name == "result_json":
                owner, target, expression = "TaskResult", "task_results", "result_json"
            elif table == "work_summary_inputs" and name in {"source_type", "source_ref", "payload_json", "source_created_at", "body_sha256", "body_bytes", "body_compacted"}:
                owner, target, expression = "WorkSummaryInput", "work_summary_inputs", name
            elif table == "weekly_okr_analysis_jobs" and name == "finished_at":
                owner, target, expression = "TaskResult", "task_results", name
            elif table == "wechat_memory_import_jobs" and name == "finished_at":
                owner, target, expression = "TaskResult", "task_results", name
            elif table == "email_agent_classification_tasks" and name == "task_id":
                owner, target, expression = "EmailClassification source public key", "task_run_identity_map", "public_key"
            elif table == "workbench_turns" and name in _WORKBENCH_DOMAIN_FIELDS:
                owner, target, expression = "WorkbenchTurn", "workbench_turns", ("workbench_task_id" if name == "task_id" else name)
            elif table == "workbench_turns" and name == "final_text":
                owner, target, expression = "native-hydrated value", None, None
                retired = "source declaration storage is none; value is hydrated, not physically persisted"
            elif table == "reply_tasks" and name in {"execution_generation", "input_version", "claimed_input_version", "current_input_id", "claimed_input_id"}:
                owner, target, expression = "Task", "tasks", name
            elif table == "reply_task_inputs" and name in _INPUT_FIELDS[table]:
                owner, target, expression = "TaskInput", "task_inputs", name
            elif table == "reply_task_inputs" and name == "reply_task_id":
                owner, target, expression = "TaskInput owner FK", "task_inputs", "task_id"
            elif table == "reply_task_inputs" and name == "id":
                owner, target, expression = "TaskInput public id", "task_run_identity_map", "public_key"
            elif table == "business_object_tasks":
                owner, target, expression = "TaskBusinessKey", "task_business_keys", {"business_object_key": "business_key", "reply_task_id": "task_id"}.get(name, name)
            elif table in _TASK_TABLES and name in lifecycle.SOURCE_FIELDS.get(table, {}):
                owner, target, expression = "Task", "tasks", lifecycle.SOURCE_FIELDS[table][name]
            elif table in _TASK_TABLES and name == "id":
                owner, target, expression = "Task public id", "task_run_identity_map", "public_key"
            elif table in _TASK_TABLES and storage == "payload":
                if table == "meeting_alignment_jobs" and name in _MEETING_RESULT_FIELDS:
                    owner, target, expression = "TaskResult", "task_results", name
                elif table == "email_agent_classification_tasks" and name in {"input_json", "channel", "stable_message_identity"}:
                    owner, target, expression = "EmailClassificationInput", "email_classification_inputs", name
                elif table == "email_agent_classification_tasks" and name == "result_json":
                    owner, target, expression = "TaskResult", "task_results", name
                elif table in _TASK_DOMAIN_FIELDS and name in _TASK_DOMAIN_FIELDS[table]:
                    owner, target, expression = "Task domain detail", _DOMAIN_TARGETS[table], name
                elif table == "weekly_okr_analysis_jobs" and name == "finished_at":
                    owner, target, expression = "TaskResult", "task_results", name
                else:
                    raise ValueError(f"payload field lacks an approved typed owner: {table}.{name}")
            elif storage == "none":
                owner, target, expression = "unpersisted source projection", None, None
                retired = "source declaration explicitly has no stored value"
            elif table in _DOMAIN_TARGETS:
                if name == "id":
                    owner, target, expression = "domain public id", "task_run_identity_map", "public_key"
                else:
                    target_field = {
                        ("workbench_artifacts", "turn_id"): "workbench_turn_id",
                        ("workbench_attachments", "task_id"): "workbench_task_id",
                    }.get((table, name), name)
                    owner, target, expression = "domain entity", _DOMAIN_TARGETS[table], target_field
            else:
                raise ValueError(f"unclassified Task source field: {table}.{name}")
            rows.append({
                "source_table": "Task20 logical projection", "source_discriminator": table,
                "source_kind": table, "source_field": name, "mapping_layer": "logical_projection",
                "physical_or_logical_owner": owner,
                "target_table": target, "target_field_or_expression": expression,
                "retirement_reason": retired,
                "sql_type": _sql_type(affinity),
                "null_contract": "NOT NULL" if required else "NULL allowed",
                "source_default": default,
                "storage_wrapper": storage,
            })
    for source_kind in task20.TASK20_COLUMNS:
        target_table = _DOMAIN_TARGETS.get(source_kind, "tasks")
        if source_kind in _TASK_TABLES:
            target_table = "tasks"
        rows.append({
            "source_table": "tasks", "source_discriminator": source_kind,
            "source_kind": source_kind, "source_field": "id", "mapping_layer": "physical",
            "physical_or_logical_owner": "physical Task20 row id",
            "target_table": target_table, "target_field_or_expression": "id",
            "retirement_reason": None, "sql_type": "INTEGER", "null_contract": "PRIMARY KEY",
            "source_default": None, "storage_wrapper": "physical INTEGER PRIMARY KEY",
        })
    return tuple(rows)


def task_run_field_map() -> tuple[dict, ...]:
    return _field_map()


def validate_task_run_field_map(mapping=None) -> None:
    from app import task20_definitions as task20

    rows = task_run_field_map() if mapping is None else tuple(mapping)
    expected = {(t, c[0], "logical_projection") for t, cs in task20.TASK20_COLUMNS.items() for c in cs}
    expected.update((t, "id", "physical") for t in task20.TASK20_COLUMNS)
    seen = set()
    target_objects = task_run_entity_objects()
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        for ddl in target_objects.values():
            db.execute(ddl)
        target_columns = {
            table: {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
            for table in target_objects if not table.startswith("idx_")
        }
    finally:
        db.close()
    for row in rows:
        key = (row.get("source_discriminator", row.get("source_kind")), row.get("source_field"), row.get("mapping_layer", "logical_projection"))
        if key not in expected:
            raise ValueError(f"unknown source field: {key}")
        if key in seen:
            raise ValueError(f"duplicate source field: {key}")
        if key[2] == "logical_projection":
            source_fields = {column[0]: column for column in task20.TASK20_COLUMNS[key[0]]}
            _name, affinity, required, default, storage = source_fields[key[1]]
            expected_metadata = (affinity.upper(), "NOT NULL" if required else "NULL allowed", default, storage)
            actual_metadata = (row.get("sql_type"), row.get("null_contract"), row.get("source_default"), row.get("storage_wrapper"))
            if actual_metadata != expected_metadata:
                raise ValueError(f"source metadata mismatch: {key}")
        elif (row.get("sql_type"), row.get("null_contract"), row.get("source_default"), row.get("storage_wrapper")) != (
            "INTEGER", "PRIMARY KEY", None, "physical INTEGER PRIMARY KEY"
        ):
            raise ValueError(f"physical identity metadata mismatch: {key}")
        if not row.get("target_table") and not row.get("retirement_reason"):
            raise ValueError(f"unowned source field: {key}")
        target = row.get("target_table")
        expression = row.get("target_field_or_expression")
        target_field = expression.split(".", 1)[0] if expression else None
        if target and target not in target_columns:
            raise ValueError(f"unknown target table: {row.get('target_table')}")
        if target and target_field not in target_columns[target]:
            raise ValueError(f"unknown target field: {target}.{target_field}")
        seen.add(key)
    missing = expected - seen
    if missing:
        raise ValueError(f"unmapped source field: {sorted(missing)[0]}")


def task_run_entity_objects() -> dict[str, str]:
    """Return the pinned native target DDL; never consult source factories."""
    return {'tasks': "CREATE TABLE tasks (\n id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, status TEXT NOT NULL,\n available_at TEXT, attempts INTEGER, error TEXT, locked_at TEXT, lease_owner TEXT,\n lease_expires_at TEXT, owner_pid INTEGER, generation INTEGER, adapter_name TEXT,\n execution_generation TEXT, current_input_id INTEGER, claimed_input_id INTEGER,\n input_version INTEGER, claimed_input_version INTEGER, parameters_json BLOB NOT NULL,\n created_at TEXT NOT NULL, updated_at TEXT NOT NULL,\n CHECK(kind IN ('reply','work_summary','meeting_alignment','okr_review','okr_analysis','workbench_turn','email_classification','feedback_processing','wechat_import')),\n FOREIGN KEY(current_input_id) REFERENCES task_inputs(id),\n FOREIGN KEY(claimed_input_id) REFERENCES task_inputs(id),\n CHECK(typeof(parameters_json)='blob' AND json_valid(parameters_json,8)))", 'task_inputs': "CREATE TABLE task_inputs (\n id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),\n channel TEXT NOT NULL, conversation_id TEXT NOT NULL, conversation_title TEXT NOT NULL, single_chat INTEGER NOT NULL,\n trigger_message_id TEXT NOT NULL, trigger_create_time TEXT NOT NULL, trigger_sender TEXT NOT NULL, trigger_text TEXT NOT NULL,\n trigger_message_json TEXT NOT NULL DEFAULT '{}', oa_url TEXT NOT NULL DEFAULT '', business_object_key TEXT NOT NULL,\n input_revision_key TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT current_timestamp,\n input_provenance_json TEXT NOT NULL DEFAULT '{}', input_compacted INTEGER NOT NULL DEFAULT 0,\n UNIQUE(task_id,id), UNIQUE(channel,conversation_id,trigger_message_id,input_revision_key),\n CHECK(channel IS NOT NULL AND conversation_id IS NOT NULL AND trigger_message_id IS NOT NULL AND input_revision_key IS NOT NULL))", 'task_business_keys': 'CREATE TABLE task_business_keys (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id), business_key TEXT NOT NULL UNIQUE, created_at TEXT, updated_at TEXT)', 'workbench_turns': 'CREATE TABLE workbench_turns (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), workbench_task_id INTEGER NOT NULL REFERENCES workbench_tasks(id), client_request_id TEXT NOT NULL UNIQUE, task_sequence INTEGER NOT NULL, user_text TEXT NOT NULL, stop_requested INTEGER NOT NULL, error_code TEXT NOT NULL, error_detail TEXT NOT NULL, resume_context TEXT NOT NULL, execution_run_id TEXT NOT NULL, runtime_quiesced_run_id TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT NOT NULL, UNIQUE(workbench_task_id,task_sequence))', 'task_results': 'CREATE TABLE task_results (task_id INTEGER NOT NULL REFERENCES tasks(id), result_kind TEXT NOT NULL, decision_json TEXT, target_kind TEXT, target_id TEXT, target_title TEXT, mentions_json TEXT, final_message TEXT, send_result_json TEXT, calendar_summary_status TEXT, calendar_summary_result_json TEXT, result_json TEXT, finished_at TEXT, PRIMARY KEY(task_id,result_kind))', 'email_classification_inputs': 'CREATE TABLE email_classification_inputs (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), channel TEXT NOT NULL, stable_message_identity TEXT NOT NULL UNIQUE, input_json TEXT NOT NULL)', 'work_summary_inputs': "CREATE TABLE work_summary_inputs (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), source_type TEXT NOT NULL, source_ref TEXT NOT NULL, payload_json TEXT NOT NULL, source_created_at TEXT NOT NULL DEFAULT '', body_sha256 TEXT NOT NULL DEFAULT '', body_bytes INTEGER NOT NULL DEFAULT 0, body_compacted INTEGER NOT NULL DEFAULT 0)", 'feedback_processing_items': 'CREATE TABLE feedback_processing_items (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), feedback_key TEXT UNIQUE, batch_id TEXT NOT NULL, workbench_task_id TEXT NOT NULL, workbench_turn_id TEXT NOT NULL, attempt_id INTEGER NOT NULL, agent_run_id INTEGER NOT NULL, commit_sha TEXT NOT NULL, test_evidence_json TEXT NOT NULL, restart_evidence_json TEXT NOT NULL, health_evidence_json TEXT NOT NULL, note TEXT NOT NULL, resolved_at TEXT NOT NULL, current_round_id INTEGER NOT NULL, FOREIGN KEY(batch_id) REFERENCES feedback_processing_batches(batch_id))', 'task_run_identity_map': "CREATE TABLE task_run_identity_map (source_kind TEXT NOT NULL, physical_id INTEGER NOT NULL, public_key TEXT, public_key_type TEXT NOT NULL, target_id INTEGER NOT NULL, PRIMARY KEY(source_kind,physical_id), UNIQUE(source_kind,target_id), CHECK(public_key_type IN ('null','integer','text','blob'))) ", 'trg_tasks_parameters_reply_insert': "CREATE TRIGGER trg_tasks_parameters_reply_insert BEFORE INSERT ON tasks WHEN NEW.kind='reply' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('force_new_decision','manual_rerun_attempt_id','manual_rerun_revision_key','recovery_code'))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_reply_update': "CREATE TRIGGER trg_tasks_parameters_reply_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='reply' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('force_new_decision','manual_rerun_attempt_id','manual_rerun_revision_key','recovery_code'))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_work_summary_insert': "CREATE TRIGGER trg_tasks_parameters_work_summary_insert BEFORE INSERT ON tasks WHEN NEW.kind='work_summary' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_work_summary_update': "CREATE TRIGGER trg_tasks_parameters_work_summary_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='work_summary' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_meeting_alignment_insert': "CREATE TRIGGER trg_tasks_parameters_meeting_alignment_insert BEFORE INSERT ON tasks WHEN NEW.kind='meeting_alignment' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('eligible_at','ended_at','meeting_id','participants_json','source_json','title'))  OR json_type(NEW.parameters_json,'$.meeting_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_meeting_alignment_update': "CREATE TRIGGER trg_tasks_parameters_meeting_alignment_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='meeting_alignment' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('eligible_at','ended_at','meeting_id','participants_json','source_json','title'))  OR json_type(NEW.parameters_json,'$.meeting_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_okr_review_insert': "CREATE TRIGGER trg_tasks_parameters_okr_review_insert BEFORE INSERT ON tasks WHEN NEW.kind='okr_review' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('codex_session_id','conversation_id','conversation_title','okr_source_json','period_end','period_label','period_start','trigger_message_id','trigger_sender','trigger_sender_user_id','trigger_text'))  OR json_type(NEW.parameters_json,'$.conversation_id') IS NULL OR json_type(NEW.parameters_json,'$.conversation_title') IS NULL OR json_type(NEW.parameters_json,'$.period_end') IS NULL OR json_type(NEW.parameters_json,'$.period_label') IS NULL OR json_type(NEW.parameters_json,'$.period_start') IS NULL OR json_type(NEW.parameters_json,'$.trigger_message_id') IS NULL OR json_type(NEW.parameters_json,'$.trigger_sender') IS NULL OR json_type(NEW.parameters_json,'$.trigger_text') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_okr_review_update': "CREATE TRIGGER trg_tasks_parameters_okr_review_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='okr_review' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('codex_session_id','conversation_id','conversation_title','okr_source_json','period_end','period_label','period_start','trigger_message_id','trigger_sender','trigger_sender_user_id','trigger_text'))  OR json_type(NEW.parameters_json,'$.conversation_id') IS NULL OR json_type(NEW.parameters_json,'$.conversation_title') IS NULL OR json_type(NEW.parameters_json,'$.period_end') IS NULL OR json_type(NEW.parameters_json,'$.period_label') IS NULL OR json_type(NEW.parameters_json,'$.period_start') IS NULL OR json_type(NEW.parameters_json,'$.trigger_message_id') IS NULL OR json_type(NEW.parameters_json,'$.trigger_sender') IS NULL OR json_type(NEW.parameters_json,'$.trigger_text') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_okr_analysis_insert': "CREATE TRIGGER trg_tasks_parameters_okr_analysis_insert BEFORE INSERT ON tasks WHEN NEW.kind='okr_analysis' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('manager_user_id','source_digest','week_end'))  OR json_type(NEW.parameters_json,'$.manager_user_id') IS NULL OR json_type(NEW.parameters_json,'$.source_digest') IS NULL OR json_type(NEW.parameters_json,'$.week_end') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_okr_analysis_update': "CREATE TRIGGER trg_tasks_parameters_okr_analysis_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='okr_analysis' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('manager_user_id','source_digest','week_end'))  OR json_type(NEW.parameters_json,'$.manager_user_id') IS NULL OR json_type(NEW.parameters_json,'$.source_digest') IS NULL OR json_type(NEW.parameters_json,'$.week_end') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_email_classification_insert': "CREATE TRIGGER trg_tasks_parameters_email_classification_insert BEFORE INSERT ON tasks WHEN NEW.kind='email_classification' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_email_classification_update': "CREATE TRIGGER trg_tasks_parameters_email_classification_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='email_classification' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_feedback_processing_insert': "CREATE TRIGGER trg_tasks_parameters_feedback_processing_insert BEFORE INSERT ON tasks WHEN NEW.kind='feedback_processing' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_feedback_processing_update': "CREATE TRIGGER trg_tasks_parameters_feedback_processing_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='feedback_processing' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_wechat_import_insert': "CREATE TRIGGER trg_tasks_parameters_wechat_import_insert BEFORE INSERT ON tasks WHEN NEW.kind='wechat_import' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('account_id','import_run_id'))  OR json_type(NEW.parameters_json,'$.account_id') IS NULL OR json_type(NEW.parameters_json,'$.import_run_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_wechat_import_update': "CREATE TRIGGER trg_tasks_parameters_wechat_import_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='wechat_import' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN ('account_id','import_run_id'))  OR json_type(NEW.parameters_json,'$.account_id') IS NULL OR json_type(NEW.parameters_json,'$.import_run_id') IS NULL THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_workbench_turn_insert': "CREATE TRIGGER trg_tasks_parameters_workbench_turn_insert BEFORE INSERT ON tasks WHEN NEW.kind='workbench_turn' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_tasks_parameters_workbench_turn_update': "CREATE TRIGGER trg_tasks_parameters_workbench_turn_update BEFORE UPDATE OF kind,parameters_json ON tasks WHEN NEW.kind='workbench_turn' BEGIN SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.parameters_json) WHERE key NOT IN (''))  THEN RAISE(ABORT,'invalid task parameter contract') END; END", 'trg_reply_current_identity_insert': "CREATE TRIGGER trg_reply_current_identity_insert\n      BEFORE UPDATE OF current_input_id ON tasks\n      WHEN NEW.kind='reply' AND NEW.current_input_id IS NOT NULL AND EXISTS (\n        SELECT 1 FROM tasks other JOIN task_inputs old_input ON old_input.id=other.current_input_id\n        JOIN task_inputs new_input ON new_input.id=NEW.current_input_id\n        WHERE other.kind='reply' AND other.id<>NEW.id\n          AND old_input.channel=new_input.channel\n          AND old_input.conversation_id=new_input.conversation_id\n          AND old_input.trigger_message_id=new_input.trigger_message_id)\n      BEGIN SELECT RAISE(ABORT,'duplicate current reply identity'); END", 'trg_reply_current_identity_input_update': "CREATE TRIGGER trg_reply_current_identity_input_update\n      BEFORE UPDATE OF channel,conversation_id,trigger_message_id ON task_inputs\n      WHEN EXISTS (\n        SELECT 1 FROM tasks owner JOIN tasks other ON other.kind='reply' AND other.id<>owner.id\n        JOIN task_inputs old_input ON old_input.id=other.current_input_id\n        WHERE owner.kind='reply' AND owner.current_input_id=NEW.id\n          AND old_input.channel=NEW.channel AND old_input.conversation_id=NEW.conversation_id\n          AND old_input.trigger_message_id=NEW.trigger_message_id)\n      BEGIN SELECT RAISE(ABORT,'duplicate current reply identity'); END", 'workbench_tasks': 'CREATE TABLE workbench_tasks (id INTEGER PRIMARY KEY,"title" TEXT NOT NULL,"runtime_kind" TEXT NOT NULL,"provider_session_ref" TEXT NOT NULL DEFAULT \'\',"archived_at" TEXT NOT NULL DEFAULT \'\',"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp)', 'email_historical_candidates': 'CREATE TABLE email_historical_candidates (id INTEGER PRIMARY KEY,"account_id" TEXT NOT NULL,"folder" TEXT NOT NULL,"model_id" TEXT NOT NULL,"stable_message_identity" TEXT NOT NULL,"normalized_text" TEXT NOT NULL,"provider_message_json" TEXT NOT NULL,"uidvalidity" INTEGER NOT NULL,"uid" INTEGER NOT NULL,"state" TEXT NOT NULL,"reason" TEXT NOT NULL DEFAULT \'\',"attempted_at" TEXT NOT NULL DEFAULT \'\',"next_retry_at" TEXT NOT NULL DEFAULT \'\',"updated_at" TEXT NOT NULL)', 'feedback_iteration_decisions': 'CREATE TABLE feedback_iteration_decisions (id INTEGER PRIMARY KEY,"batch_id" TEXT NOT NULL REFERENCES feedback_processing_batches(batch_id),"workbench_task_id" TEXT NOT NULL,"workbench_turn_id" TEXT NOT NULL,"decision_json" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp)', 'feedback_processing_batches': 'CREATE TABLE feedback_processing_batches (id INTEGER PRIMARY KEY,"batch_id" TEXT,"status" TEXT NOT NULL DEFAULT \'pending\',"requested_count" INTEGER NOT NULL DEFAULT 0,"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp,"resolved_at" TEXT NOT NULL DEFAULT \'\')', 'feedback_processing_rounds': 'CREATE TABLE feedback_processing_rounds (id INTEGER PRIMARY KEY,"feedback_key" TEXT NOT NULL REFERENCES feedback_processing_items(feedback_key),"round_number" INTEGER NOT NULL,"batch_id" TEXT NOT NULL DEFAULT \'\',"status" TEXT NOT NULL,"workbench_task_id" TEXT NOT NULL DEFAULT \'\',"workbench_turn_id" TEXT NOT NULL DEFAULT \'\',"attempt_id" INTEGER NOT NULL DEFAULT 0,"agent_run_id" INTEGER NOT NULL DEFAULT 0,"commit_sha" TEXT NOT NULL DEFAULT \'\',"test_evidence_json" TEXT NOT NULL DEFAULT \'{}\',"restart_evidence_json" TEXT NOT NULL DEFAULT \'{}\',"health_evidence_json" TEXT NOT NULL DEFAULT \'{}\',"backlog_evidence_json" TEXT NOT NULL DEFAULT \'{}\',"receipt_version" INTEGER NOT NULL DEFAULT 1,"note" TEXT NOT NULL DEFAULT \'\',"started_at" TEXT NOT NULL DEFAULT \'\',"resolved_at" TEXT NOT NULL DEFAULT \'\',"reopened_at" TEXT NOT NULL DEFAULT \'\',"reopen_reason" TEXT NOT NULL DEFAULT \'\',"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp,"scope_receipt_json" TEXT NOT NULL DEFAULT \'{}\')', 'oa_notification_events': 'CREATE TABLE oa_notification_events (id INTEGER PRIMARY KEY,"event_kind" TEXT NOT NULL,"requires_reply" INTEGER NOT NULL DEFAULT 0,"process_instance_id" TEXT NOT NULL DEFAULT \'\',"task_id" TEXT NOT NULL DEFAULT \'\',"channel" TEXT NOT NULL DEFAULT \'dingtalk\',"conversation_id" TEXT NOT NULL,"conversation_title" TEXT NOT NULL DEFAULT \'\',"trigger_message_id" TEXT NOT NULL,"trigger_create_time" TEXT NOT NULL DEFAULT \'\',"trigger_sender" TEXT NOT NULL DEFAULT \'\',"trigger_text" TEXT NOT NULL DEFAULT \'\',"trigger_message_json" TEXT NOT NULL DEFAULT \'{}\',"oa_url" TEXT NOT NULL DEFAULT \'\',"status" TEXT NOT NULL DEFAULT \'pending\',"claimed_attempt_id" INTEGER NOT NULL DEFAULT 0,"result_attempt_id" INTEGER NOT NULL DEFAULT 0,"last_error" TEXT NOT NULL DEFAULT \'\',"created_at" TEXT NOT NULL DEFAULT current_timestamp,"updated_at" TEXT NOT NULL DEFAULT current_timestamp,"sent_at" TEXT NOT NULL DEFAULT \'\')', 'okr_review_items': 'CREATE TABLE okr_review_items (id INTEGER PRIMARY KEY,"request_id" INTEGER NOT NULL,"objective_title" TEXT NOT NULL,"objective_weight" REAL NOT NULL DEFAULT 0,"kr_title" TEXT NOT NULL,"kr_weight" REAL NOT NULL DEFAULT 0,"item_json" TEXT NOT NULL DEFAULT \'{}\',"created_at" TEXT NOT NULL DEFAULT current_timestamp)', 'workbench_artifacts': 'CREATE TABLE workbench_artifacts (id INTEGER PRIMARY KEY,"workbench_turn_id" INTEGER NOT NULL REFERENCES workbench_turns(task_id),"label" TEXT NOT NULL,"path" TEXT NOT NULL,"media_type" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp)', 'workbench_attachments': 'CREATE TABLE workbench_attachments (id INTEGER PRIMARY KEY,"workbench_task_id" INTEGER NOT NULL REFERENCES workbench_tasks(id),"client_request_id" TEXT NOT NULL,"filename" TEXT NOT NULL,"media_type" TEXT NOT NULL,"size_bytes" INTEGER NOT NULL,"content_sha256" TEXT NOT NULL,"storage_path" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp)', 'idx_tasks_claim_due': 'CREATE INDEX idx_tasks_claim_due ON tasks(kind,status,available_at,id)', 'idx_work_summary_source_identity': 'CREATE UNIQUE INDEX idx_work_summary_source_identity ON work_summary_inputs(source_type,source_ref)', 'idx_meeting_source_identity': "CREATE UNIQUE INDEX idx_meeting_source_identity ON tasks(json_extract(parameters_json,'$.meeting_id')) WHERE kind='meeting_alignment'", 'idx_okr_review_source_identity': "CREATE UNIQUE INDEX idx_okr_review_source_identity ON tasks(json_extract(parameters_json,'$.conversation_id'),json_extract(parameters_json,'$.trigger_message_id')) WHERE kind='okr_review'", 'idx_weekly_analysis_source_identity': "CREATE UNIQUE INDEX idx_weekly_analysis_source_identity ON tasks(json_extract(parameters_json,'$.week_end'),json_extract(parameters_json,'$.manager_user_id'),json_extract(parameters_json,'$.source_digest')) WHERE kind='okr_analysis'", 'idx_business_object_tasks_business_key_identity': 'CREATE UNIQUE INDEX idx_business_object_tasks_business_key_identity ON task_business_keys(business_key)', 'idx_email_historical_candidates_account_id_folder_model_id_stable_message_identity_identity': 'CREATE UNIQUE INDEX idx_email_historical_candidates_account_id_folder_model_id_stable_message_identity_identity ON email_historical_candidates(account_id,folder,model_id,stable_message_identity)', 'idx_feedback_iteration_decisions_batch': 'CREATE INDEX idx_feedback_iteration_decisions_batch ON feedback_iteration_decisions(batch_id,id)', 'idx_feedback_processing_batches_batch_id_identity': 'CREATE UNIQUE INDEX idx_feedback_processing_batches_batch_id_identity ON feedback_processing_batches(batch_id)', 'idx_feedback_processing_items_batch': 'CREATE INDEX idx_feedback_processing_items_batch ON feedback_processing_items(batch_id)', 'idx_feedback_processing_items_feedback_key_identity': 'CREATE UNIQUE INDEX idx_feedback_processing_items_feedback_key_identity ON feedback_processing_items(feedback_key)', 'idx_feedback_processing_rounds_batch': 'CREATE INDEX idx_feedback_processing_rounds_batch ON feedback_processing_rounds(batch_id)', 'idx_feedback_processing_rounds_feedback': 'CREATE INDEX idx_feedback_processing_rounds_feedback ON feedback_processing_rounds(feedback_key,round_number)', 'idx_feedback_processing_rounds_feedback_key_batch_id_identity': 'CREATE UNIQUE INDEX idx_feedback_processing_rounds_feedback_key_batch_id_identity ON feedback_processing_rounds(feedback_key,batch_id)', 'idx_feedback_processing_rounds_feedback_key_round_number_identity': 'CREATE UNIQUE INDEX idx_feedback_processing_rounds_feedback_key_round_number_identity ON feedback_processing_rounds(feedback_key,round_number)', 'idx_oa_notification_events_claim': 'CREATE INDEX idx_oa_notification_events_claim ON oa_notification_events(status,claimed_attempt_id,id)', 'idx_oa_notification_events_case_status': 'CREATE INDEX idx_oa_notification_events_case_status ON oa_notification_events(process_instance_id,task_id,requires_reply,status,id)', 'idx_oa_notification_events_channel_conversation_id_trigger_message_id_identity': 'CREATE UNIQUE INDEX idx_oa_notification_events_channel_conversation_id_trigger_message_id_identity ON oa_notification_events(channel,conversation_id,trigger_message_id)', 'idx_workbench_artifacts_turn_created_id': 'CREATE INDEX idx_workbench_artifacts_turn_created_id ON workbench_artifacts(workbench_turn_id,created_at,id)', 'idx_workbench_artifacts_created_id_turn': 'CREATE INDEX idx_workbench_artifacts_created_id_turn ON workbench_artifacts(created_at,id,workbench_turn_id)', 'idx_workbench_artifacts_id_identity': 'CREATE UNIQUE INDEX idx_workbench_artifacts_id_identity ON workbench_artifacts(id)', 'idx_workbench_attachments_task_request': 'CREATE UNIQUE INDEX idx_workbench_attachments_task_request ON workbench_attachments(workbench_task_id,client_request_id)', 'idx_workbench_attachments_task_created_id': 'CREATE INDEX idx_workbench_attachments_task_created_id ON workbench_attachments(workbench_task_id,created_at,id)', 'idx_workbench_attachments_id_identity': 'CREATE UNIQUE INDEX idx_workbench_attachments_id_identity ON workbench_attachments(id)', 'trg_workbench_active_turn_insert': "CREATE TRIGGER trg_workbench_active_turn_insert\nBEFORE INSERT ON workbench_turns\nWHEN (SELECT status FROM tasks WHERE id=NEW.task_id) IN ('queued','running')\n AND EXISTS (SELECT 1 FROM workbench_turns active JOIN tasks active_task ON active_task.id=active.task_id\n             WHERE active.workbench_task_id=NEW.workbench_task_id\n               AND active_task.status IN ('queued','running'))\nBEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END", 'trg_workbench_active_turn_update': "CREATE TRIGGER trg_workbench_active_turn_update\nBEFORE UPDATE OF workbench_task_id ON workbench_turns\nWHEN (SELECT status FROM tasks WHERE id=NEW.task_id) IN ('queued','running')\n AND EXISTS (SELECT 1 FROM workbench_turns active JOIN tasks active_task ON active_task.id=active.task_id\n             WHERE active.workbench_task_id=NEW.workbench_task_id AND active.task_id<>NEW.task_id\n               AND active_task.status IN ('queued','running'))\nBEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END", 'trg_workbench_active_turn_status_update': "CREATE TRIGGER trg_workbench_active_turn_status_update\nBEFORE UPDATE OF status ON tasks\nWHEN NEW.kind='workbench_turn' AND NEW.status IN ('queued','running')\n AND EXISTS (SELECT 1 FROM workbench_turns turn\n             JOIN workbench_turns active ON active.workbench_task_id=turn.workbench_task_id\n             JOIN tasks active_task ON active_task.id=active.task_id\n             WHERE turn.task_id=NEW.id AND active.task_id<>NEW.id\n               AND active_task.status IN ('queued','running'))\nBEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END", 'trg_workbench_active_turn_task_id_update': "CREATE TRIGGER trg_workbench_active_turn_task_id_update\nBEFORE UPDATE OF task_id ON workbench_turns\nWHEN (SELECT status FROM tasks WHERE id=NEW.task_id) IN ('queued','running')\n AND EXISTS (SELECT 1 FROM workbench_turns active JOIN tasks active_task ON active_task.id=active.task_id\n             WHERE active.workbench_task_id=OLD.workbench_task_id AND active.task_id<>OLD.task_id\n               AND active_task.status IN ('queued','running'))\nBEGIN SELECT RAISE(ABORT,'active Workbench turn already exists'); END"}
