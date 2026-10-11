import sqlite3

from app.store import AutoReplyStore


def values(text='first'):
    return dict(conversation_id='chat', conversation_title='Chat', single_chat=False,
                trigger_message_id='message', trigger_create_time='2026-10-11',
                trigger_sender='sender', trigger_text=text,
                trigger_message_json='{"schema":"sample","message_id":"message"}')


def test_parent_has_real_input_foreign_keys_and_no_payload_cache(tmp_path):
    store = AutoReplyStore(tmp_path / 'db.sqlite3')
    task = store.enqueue_reply_task(**values())
    with sqlite3.connect(store.path) as db:
        columns = {r[1] for r in db.execute('pragma table_info(reply_tasks)')}
        assert not {'trigger_text', 'trigger_message_json', 'input_compacted', 'input_provenance_json'} & columns
        keys = {(r[3], r[2], r[4]) for r in db.execute('pragma foreign_key_list(reply_tasks)')}
        assert ('current_input_id', 'reply_task_inputs', 'id') in keys
        assert ('claimed_input_id', 'reply_task_inputs', 'id') in keys
        assert db.execute('select current_input_id from reply_tasks where id=?', (task,)).fetchone()[0]


def test_claim_keeps_exact_input_when_current_context_changes(tmp_path):
    store = AutoReplyStore(tmp_path / 'db.sqlite3')
    task_id = store.enqueue_reply_task(**values())
    claimed = store.claim_reply_task(task_id)
    updated = values('second')
    updated['trigger_message_id'] = 'message2'
    updated['business_object_key'] = store.get_reply_task(task_id).business_object_key
    store.enqueue_reply_task(**updated)
    assert store.get_reply_task(task_id).trigger_text == 'second'
    execution = store.get_reply_task_for_execution(task_id, claimed.execution_generation)
    assert execution.trigger_text == 'first'
    with sqlite3.connect(store.path) as db:
        current, original = db.execute('select current_input_id, claimed_input_id from reply_tasks where id=?', (task_id,)).fetchone()
        assert current != original


def test_terminal_input_persists_no_compact_json_but_projects_old_shape(tmp_path):
    store = AutoReplyStore(tmp_path / 'db.sqlite3')
    task_id = store.enqueue_reply_task(**values())
    task = store.claim_reply_task(task_id)
    store.complete_reply_task(task_id, expected_execution_generation=task.execution_generation)
    result = store.get_reply_task(task_id)
    assert result.input_compacted
    assert '"input_compacted":true' in result.trigger_message_json
    with sqlite3.connect(store.path) as db:
        assert db.execute('select trigger_text, trigger_message_json, input_compacted from reply_task_inputs').fetchone() == ('', '', 1)
    assert store.list_reply_task_inputs(task_id)[0]['trigger_message_json'] == result.trigger_message_json


def test_refresh_appends_without_changing_old_revision(tmp_path):
    store = AutoReplyStore(tmp_path / 'db.sqlite3')
    task_id = store.enqueue_reply_task(**values())
    old = store.list_reply_task_inputs(task_id)[0]
    assert store.update_reply_task_trigger(task_id, trigger_text='refresh', trigger_message_json='{}') == 1
    inputs = store.list_reply_task_inputs(task_id)
    assert inputs[0] == old
    assert inputs[-1]['trigger_text'] == 'refresh'
    assert store.get_reply_task(task_id).trigger_text == 'refresh'


def test_manual_rerun_appends_exact_new_input(tmp_path):
    store = AutoReplyStore(tmp_path / 'db.sqlite3')
    task_id = store.enqueue_reply_task(**values())
    store.enqueue_manual_rerun_reply_task(**values('rerun'), attempt_id=0)
    assert store.list_reply_task_inputs(task_id)[-1]['trigger_text'] == 'rerun'
    assert store.get_reply_task(task_id).trigger_text == 'rerun'
