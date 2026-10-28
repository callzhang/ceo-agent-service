import sqlite3

import pytest

from app.task_run_entity_definitions import (
    task_run_entity_objects,
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


def test_workbench_active_turn_partial_unique_contract_only_blocks_queued_or_running():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    db.execute("insert into workbench_tasks(id,title,runtime_kind) values(1,'a','x')")
    db.execute("insert into tasks(id,kind,status,parameters_json,created_at,updated_at) values(11,'workbench_turn','queued',jsonb('{}'),'now','now'),(12,'workbench_turn','queued',jsonb('{}'),'now','now')")
    turn_insert = "insert into workbench_turns(task_id,workbench_task_id,client_request_id,task_sequence,user_text,stop_requested,error_code,error_detail,resume_context,execution_run_id,runtime_quiesced_run_id,started_at,completed_at) values(?,1,?,?,'x',0,'','','','','','','')"
    db.execute(turn_insert, (11, 'active-1', 1))
    with pytest.raises(sqlite3.IntegrityError, match='active Workbench turn already exists'):
        db.execute(turn_insert, (12, 'active-2', 2))
    db.execute("update tasks set status='completed' where id=11")
    db.execute(turn_insert, (12, 'active-2', 2))
    db.execute("update tasks set status='running' where id=12")
    with pytest.raises(sqlite3.IntegrityError, match='active Workbench turn already exists'):
        db.execute("update tasks set status='queued' where id=11")


def test_workbench_active_turn_task_id_reassignment_checks_new_task_but_excludes_old_row():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    db.execute("insert into workbench_tasks(id,title,runtime_kind) values(1,'a','x'),(2,'b','x')")
    db.execute("insert into tasks(id,kind,status,parameters_json,created_at,updated_at) values"
               "(11,'workbench_turn','queued',jsonb('{}'),'now','now'),"
               "(12,'workbench_turn','completed',jsonb('{}'),'now','now'),"
               "(13,'workbench_turn','queued',jsonb('{}'),'now','now'),"
               "(14,'workbench_turn','queued',jsonb('{}'),'now','now'),"
               "(15,'workbench_turn','queued',jsonb('{}'),'now','now')")
    turn_insert = "insert into workbench_turns(task_id,workbench_task_id,client_request_id,task_sequence,user_text,stop_requested,error_code,error_detail,resume_context,execution_run_id,runtime_quiesced_run_id,started_at,completed_at) values(?,?,?,?,'x',0,'','','','','','','')"
    db.execute(turn_insert, (11, 1, 'active', 1))
    db.execute(turn_insert, (12, 1, 'terminal', 2))
    with pytest.raises(sqlite3.IntegrityError, match='active Workbench turn already exists'):
        db.execute("update workbench_turns set task_id=13 where task_id=12")
    db.execute(turn_insert, (14, 2, 'move', 1))
    db.execute("update workbench_turns set task_id=15 where task_id=14")


def test_current_source_active_turn_index_has_the_same_predicate_and_parent_key():
    from contextlib import closing
    from app.task20_definitions import create_task20_schema, TASK20_LAYOUTS
    from tests.test_task20_definitions import insert as source_insert, payload as source_payload

    with closing(sqlite3.connect(':memory:')) as source:
        source.execute('pragma foreign_keys=on')
        create_task20_schema(source)
        source_insert(source, 'workbench_tasks', physical_id=100, legacy_key='workbench-public',
                      values=source_payload(source, 'workbench_tasks', title='Workbench', runtime_kind='codex'))
        first = source_payload(source, 'workbench_turns', client_request_id='source-1', task_sequence=1,
                               user_text='one', status='queued')
        second = source_payload(source, 'workbench_turns', client_request_id='source-2', task_sequence=2,
                                user_text='two', status='running')
        source_insert(source, 'workbench_turns', physical_id=101, legacy_key='turn-1', parent_id=100, values=first)
        with pytest.raises(sqlite3.IntegrityError):
            source_insert(source, 'workbench_turns', physical_id=102, legacy_key='turn-2', parent_id=100, values=second)
        source.execute("update tasks set payload_json=jsonb_set(payload_json,?,json(?)) where id=101",
                       (TASK20_LAYOUTS['workbench_turns'].path('status'), '"completed"'))
        source_insert(source, 'workbench_turns', physical_id=102, legacy_key='turn-2', parent_id=100, values=second)


def test_source_and_target_preserve_sqlite_affinity_acceptance_for_json_parameters():
    from contextlib import closing
    from app.task20_definitions import task20_column
    from app.task_lifecycle_current_schema import create_current_task20_schema
    from app.task8_storage import register_task8_functions, task8_insert_parts
    from tests.test_task20_definitions import payload

    with closing(sqlite3.connect(':memory:')) as source:
        register_task8_functions(source)
        create_current_task20_schema(source)
        source_values = payload(source, 'reply_tasks', force_new_decision='true')
        insert = task8_insert_parts('reply_tasks', source_values)
        source.execute(f"insert into tasks({insert.columns}) values({insert.values})", insert.parameters)
        source_type = source.execute(
            f"select typeof({task20_column('reply_tasks','force_new_decision')}) from tasks"
        ).fetchone()[0]

    target = sqlite3.connect(':memory:')
    for ddl in task_run_entity_objects().values():
        target.execute(ddl)
    target.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) values('reply','pending',jsonb('{\"force_new_decision\":\"true\"}'),'now','now')")
    target_type = target.execute("select json_type(parameters_json,'$.force_new_decision') from tasks").fetchone()[0]
    assert source_type == 'text'
    assert target_type == 'text'


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


def test_feedback_item_native_insert_accepts_source_valid_unassigned_batch_values():
    db = sqlite3.connect(':memory:')
    db.execute('pragma foreign_keys=on')
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    params = db.execute("select jsonb('{}')").fetchone()[0]
    for task_id, feedback_key, batch_id in (
        (901, 'feedback-empty', ''),
        (902, 'feedback-unresolved', 'historical-missing'),
    ):
        db.execute(
            "insert into tasks(id,kind,status,parameters_json,created_at,updated_at) values(?, 'feedback_processing','pending',?,'now','now')",
            (task_id, params),
        )
        db.execute(
            """insert into feedback_processing_items(
                task_id,feedback_key,batch_id,current_round_id
            ) values(?,?,?,0)""",
            (task_id, feedback_key, batch_id),
        )
    assert db.execute(
        "select feedback_key,batch_id from feedback_processing_items order by feedback_key"
    ).fetchall() == [
        ('feedback-empty', ''),
        ('feedback-unresolved', 'historical-missing'),
    ]
    foreign_keys = db.execute('pragma foreign_key_list(feedback_processing_items)').fetchall()
    assert [(row[3], row[2], row[4]) for row in foreign_keys] == [('task_id', 'tasks', 'id')]


def test_workbench_attachment_ids_are_never_reused_after_highest_row_deletion():
    db = sqlite3.connect(":memory:")
    db.execute("pragma foreign_keys=on")
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    db.execute("insert into workbench_tasks(id,title,runtime_kind) values(1,'parent','codex')")
    db.execute(
        "insert into workbench_attachments(workbench_task_id,client_request_id,filename,"
        "media_type,size_bytes,content_sha256,storage_path) values"
        "(1,'request-1','one.txt','text/plain',0,'hash','/one'),"
        "(1,'request-2','two.txt','text/plain',0,'hash','/two')"
    )
    retired_id = db.execute("select max(id) from workbench_attachments").fetchone()[0]
    db.execute("delete from workbench_attachments where id=?", (retired_id,))

    replacement_id = db.execute(
        "insert into workbench_attachments(workbench_task_id,client_request_id,filename,"
        "media_type,size_bytes,content_sha256,storage_path) "
        "values(1,'request-3','three.txt','text/plain',0,'hash','/three') returning id"
    ).fetchone()[0]

    assert replacement_id > retired_id
