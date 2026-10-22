import sqlite3

import pytest

from app.task_run_entity_definitions import (
    task_run_entity_objects,
    task_run_field_map,
    validate_task_run_field_map,
)


def test_target_schema_executes_and_has_task_run_domain_owners():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    names = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
    assert {'tasks', 'task_inputs', 'task_business_keys', 'task_results',
            'email_classification_inputs', 'workbench_tasks', 'workbench_turns',
            'task_run_identity_map'} <= names
    assert not {'runs', 'actions', 'action_receipts'} & names


def test_field_map_is_total_unique_and_fails_unknown_or_missing_source_fields():
    mapping = task_run_field_map()
    validate_task_run_field_map(mapping)
    with pytest.raises(ValueError, match='unknown source field'):
        validate_task_run_field_map((*mapping, {**mapping[0], 'source_kind': 'invented', 'source_field': 'x'}))
    with pytest.raises(ValueError, match='unmapped source field'):
        validate_task_run_field_map(mapping[1:])
    with pytest.raises(ValueError, match='duplicate source field'):
        validate_task_run_field_map((*mapping, mapping[0]))


def test_task_input_identity_and_multi_key_relation_are_real_constraints():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    params = db.execute("select jsonb('{}')").fetchone()[0]
    task = db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',?,'now','now') returning id", (params,)).fetchone()[0]
    db.execute("insert into task_business_keys(task_id,business_key) values(?, 'a')", (task,))
    db.execute("insert into task_business_keys(task_id,business_key) values(?, 'b')", (task,))
    db.execute("insert into task_inputs(task_id,channel,conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,trigger_text,business_object_key,input_revision_key,created_at) values(?, 'dingtalk','c','title',0,'m','time','sender','text','b','r1','now')", (task,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into task_inputs(task_id,channel,conversation_id,trigger_message_id,input_revision_key,created_at) values(?, 'dingtalk','c','m','r1','now')", (task,))


def test_input_requires_source_fields_and_task_fk_and_business_key_is_global():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    params = db.execute("select jsonb('{}')").fetchone()[0]
    task1, task2 = [db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',?,'now','now') returning id", (params,)).fetchone()[0] for _ in range(2)]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into task_inputs(task_id,channel,conversation_id,trigger_message_id) values(?, 'dingtalk','c','m')", (task1,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into task_inputs(task_id,channel,conversation_id,trigger_message_id,input_revision_key) values(999999, 'dingtalk','c','m','r')")
    db.execute("insert into task_business_keys(task_id,business_key) values(?, 'same-key')", (task1,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into task_business_keys(task_id,business_key) values(?, 'same-key')", (task2,))


def test_workbench_client_request_identity_is_global_and_sequence_is_unique_per_task():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    db.execute("insert into workbench_tasks(id,title,runtime_kind) values(1,'a','x'),(2,'b','x')")
    db.execute("insert into tasks(id,kind,status,parameters_json,created_at,updated_at) values(11,'workbench_turn','queued',jsonb('{}'),'now','now'),(12,'workbench_turn','queued',jsonb('{}'),'now','now')")
    db.execute("insert into workbench_turns(task_id,workbench_task_id,client_request_id,task_sequence,user_text,stop_requested,error_code,error_detail,resume_context,execution_run_id,runtime_quiesced_run_id,started_at,completed_at) values(11,1,'req',1,'x',0,'','','','','','','')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into workbench_turns(task_id,workbench_task_id,client_request_id,task_sequence,user_text,stop_requested,error_code,error_detail,resume_context,execution_run_id,runtime_quiesced_run_id,started_at,completed_at) values(12,2,'req',1,'x',0,'','','','','','','')")


def test_task_without_source_input_keeps_null_links_and_source_status():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('meeting_alignment','ready_to_send',jsonb('{\"meeting_id\":\"m\"}'),'now','now')")
    row = db.execute('select current_input_id,claimed_input_id,status from tasks').fetchone()
    assert row == (None, None, 'ready_to_send')


def test_request_parameter_contract_rejects_unknown_json_keys():
    db = sqlite3.connect(':memory:')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    with pytest.raises(sqlite3.IntegrityError, match='invalid task parameter contract'):
        db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',jsonb('{\"channel\":\"dingtalk\",\"invented\":true}'),'now','now')")
    db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',jsonb('{\"force_new_decision\":true}'),'now','now')")


def test_current_reply_identity_uniqueness_ignores_revision_but_allows_versions_of_one_task():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    params = db.execute("select jsonb('{}')").fetchone()[0]
    tasks = [db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',?,'now','now') returning id", (params,)).fetchone()[0] for _ in range(2)]
    values = ("dingtalk", "chat", "m", "title", 0, "time", "sender", "text", "business", "")
    sql = "insert into task_inputs(task_id,channel,conversation_id,trigger_message_id,conversation_title,single_chat,trigger_create_time,trigger_sender,trigger_text,business_object_key,input_revision_key) values(?,?,?,?,?,?,?,?,?,?,?) returning id"
    input1 = db.execute(sql, (tasks[0], *values)).fetchone()[0]
    input2 = db.execute(sql, (tasks[0], *values[:-1], "revision-2")).fetchone()[0]
    db.execute("update tasks set current_input_id=? where id=?", (input1, tasks[0]))
    db.execute("update tasks set current_input_id=? where id=?", (input2, tasks[0]))
    input3 = db.execute(sql, (tasks[1], *values[:-1], "revision-3")).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="duplicate current reply identity"):
        db.execute("update tasks set current_input_id=? where id=?", (input3, tasks[1]))
