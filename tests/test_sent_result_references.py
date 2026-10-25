"""Sent owns only exceptional bodies; exact provider bytes have one owner."""

import inspect
import sqlite3

import pytest

from app import action_storage as actions
from app.store import AutoReplyStore
from tests.test_agent_turn_store import _claim_consumer, _task
from tests.support.historical_action_storage import db as db


def test_completed_delivery_keeps_one_provider_body_and_public_projection(tmp_path):
    store = AutoReplyStore(tmp_path / 'sent.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    store.complete_agent_run(run.id, {'outcome': 'proposal'}, owner='consumer')
    sent = store.record_completed_agent_message_delivery(
        agent_run_id=run.id, external_action_key='key', business_object_key=task.business_object_key,
        action_identity='reply', operation='message.send',
        target_identifiers={'conversation_id': task.conversation_id},
        conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
        reply_text='Delivered', provider_result={'receipt': 'substitute'})
    with store._connect() as connection:
        assert connection.execute("select send_result_json from actions where record_type='sent_replies'").fetchone()[0] is None
        assert connection.execute("select provider_result_json from actions where record_type='external_action_results'").fetchone()[0] == '{"receipt":"substitute"}'
        assert connection.execute('pragma foreign_key_check').fetchall() == []
    assert sent.send_result_json == '{"receipt":"substitute"}'
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) == sent


def test_unused_writers_and_finalize_sent_parameters_are_retired():
    assert not hasattr(AutoReplyStore, 'record_agent_message_delivery')
    assert not hasattr(AutoReplyStore, 'record_reply_attempt_for_trigger')
    parameters = inspect.signature(AutoReplyStore.finalize_orchestrated_reply_task).parameters
    assert 'sent_reply_text' not in parameters
    assert 'sent_reply_result_json' not in parameters


def _owner(db, key, body):
    return actions.insert_action(db, 'external_action_results', dict(
        external_action_key=key, business_object_key='business', action_identity=key,
        operation='send', target_identifiers_json='{}', provider_result_json=body,
        result_digest='digest', first_agent_run_id=1)).lastrowid


def _sent(db, key, body, *, public=41):
    return actions.insert_action(db, 'sent_replies', dict(
        id=public, conversation_id='chat', trigger_message_id='trigger', reply_text='exact body',
        sent_at='sent-clock', send_result_json=body, recall_key='recall', recall_status='failed',
        recall_error='error', recalled_at='recall-clock', feedback_token='feedback',
        channel='dingtalk', agent_run_id=1, external_action_key=key)).lastrowid


def test_sent_key_change_keeps_old_result_unless_body_is_explicitly_changed(tmp_path):
    store = AutoReplyStore(tmp_path / 'key-change.sqlite3')
    task = _task(store)
    _claim_consumer(store, task)
    with store._connect() as connection:
        _owner(connection, 'old', 'old result')
        _owner(connection, 'new', 'new result')
        _sent(connection, 'old', 'old result')
        actions.update_action(connection, 'sent_replies', 41, {'external_action_key': 'new'})
        assert connection.execute('select send_result_json from (' + actions.SENT_REPLY_SQL + ') where id=41').fetchone()[0] == 'old result'
        actions.update_action(connection, 'sent_replies', 41, {'send_result_json': 'new result'})
        assert connection.execute("select send_result_json from actions where record_type='sent_replies'").fetchone()[0] is None
        actions.update_action(connection, 'sent_replies', 41, {'external_action_key': ''})
        assert connection.execute('select external_action_key,send_result_json from (' + actions.SENT_REPLY_SQL + ') where id=41').fetchone()[:] == ('', 'new result')


def test_reference_checks_seek_sent_rows_and_bound_unrelated_index_cost(tmp_path):
    store = AutoReplyStore(tmp_path / 'reference-cost.sqlite3')
    with store._connect() as connection:
        actions.insert_error_action(connection, {'kind': 'synthetic', 'detail': 'small'})
        connection.execute("with recursive n(i) as (select 2 union all select i+1 from n where i<20001) "
            "insert into actions(_kind,legacy_key,payload_json) select a._kind,cast(n.i as text),a.payload_json "
            "from n cross join actions a where a.record_type='errors' and a.legacy_key='1'")
        plan = [row[3] for row in connection.execute('explain query plan select rowid from actions where sent_result_owner_key=?', ('key',))]
        assert any('SEARCH actions USING INDEX idx_actions_sent_result_owner' in item for item in plan)
        parent_bytes = connection.execute("select sum(pgsize) from dbstat where name='idx_actions_sent_result_receipt'").fetchone()[0]
        child_bytes = connection.execute("select sum(pgsize) from dbstat where name='idx_actions_sent_result_owner'").fetchone()[0]
        assert parent_bytes <= 12 * 20001
        assert child_bytes == connection.execute('pragma page_size').fetchone()[0]


def test_empty_json_scalar_and_non_json_results_keep_exact_value_and_type(tmp_path):
    store = AutoReplyStore(tmp_path / 'raw-values.sqlite3')
    task = _task(store)
    _claim_consumer(store, task)
    bodies = ['', 'null', '0', 'false', '[]', '{}', ' { } ', 'raw\x00result', '原始\n\r值']
    with store._connect() as connection:
        for public, body in enumerate(bodies, 41):
            key = f'raw-{public}'
            _owner(connection, key, body)
            _sent(connection, key, body, public=public)
            assert connection.execute('select send_result_json,typeof(send_result_json) from (' + actions.SENT_REPLY_SQL + ') where id=?', (public,)).fetchone()[:] == (body, 'text')
            assert connection.execute("select send_result_json from actions where record_type='sent_replies' and legacy_key=?", (str(public),)).fetchone()[0] is None
            actions.update_action(connection, 'external_action_results', key, {'provider_result_json': 'different'})
            assert connection.execute('select send_result_json,typeof(send_result_json) from (' + actions.SENT_REPLY_SQL + ') where id=?', (public,)).fetchone()[:] == (body, 'text')
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
            _sent(connection, 'raw-41', '', public=99)
        for value in (None, 17, 2.5, b'raw'):
            error = sqlite3.IntegrityError if value is None else ValueError
            with pytest.raises(error, match='CHECK|storage type'):
                _owner(connection, f'invalid-{type(value).__name__}', value)


def test_candidate_correction_keeps_untouched_sent_and_shared_attempt_meanings(tmp_path):
    from app import shared_action_lifecycle
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path / 'shared-correction.sqlite3')
    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    old = {'provider_result': {'message': 'same'}, 'verification': {'state': 'ambiguous'}}
    receipt = store.record_candidate_external_action(execution['id'], 'worker', 0, 'receipt', 'send', {'user_id': 'x'}, old)
    with store._connect() as connection:
        _sent(connection, 'receipt', receipt['provider_result_json'])
        _owner(connection, 'independent', 'independent raw')
        _sent(connection, 'independent', 'independent raw', public=42)
        extra = shared_action_lifecycle._insert(connection, 'candidate_action_attempts', dict(
            execution_id=execution['id'], action_index=1, external_action_key='receipt',
            status='verified', result_json=None))
    changed = store.record_candidate_external_action(execution['id'], 'worker', 0, 'receipt', 'send', {'user_id': 'x'},
        {'provider_result': {'message': 'same'}, 'verification': {'state': 'sent'}})
    attempts = store.list_candidate_action_attempts(execution['id'])
    assert attempts[0]['result_json'] == changed['provider_result_json']
    assert attempts[1]['id'] == extra.lastrowid
    assert attempts[1]['result_json'] == receipt['provider_result_json']
    with store._connect() as connection:
        assert [tuple(row) for row in connection.execute('select id,send_result_json from (' + actions.SENT_REPLY_SQL + ') order by id')] == [(41, receipt['provider_result_json']), (42, 'independent raw')]
        assert connection.execute("select send_result_json from actions where record_type='sent_replies' and legacy_key='41'").fetchone()[0] == receipt['provider_result_json']


@pytest.mark.parametrize('legacy', [False, True])
def test_existing_native_readback_repairs_explicit_sent_result_without_resending(tmp_path, legacy):
    from app.dingtalk_delivery_repair import apply_delivery_reverification, apply_native_chat_readback
    from tests.test_dingtalk_delivery_repair import historical_receipt, proof, _native_chat_proof, _legacy_native_readback_case
    if legacy:
        store, task, handler, receipt, readback = _legacy_native_readback_case(tmp_path)
    else:
        store, task, _, _, handler, receipt, _ = historical_receipt(tmp_path)
        assert apply_delivery_reverification(store, proof(receipt, 'ambiguous'))
        readback = _native_chat_proof(task, store.get_sent_reply(task.conversation_id, task.trigger_message_id))
    assert apply_native_chat_readback(store, readback)
    expected = store.get_candidate_external_action(receipt['external_action_key'])['provider_result_json']
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id).send_result_json == expected
    with store._connect() as connection:
        assert connection.execute("select send_result_json from actions where record_type='sent_replies' and external_action_key=?", (receipt['external_action_key'],)).fetchone()[0] is None
        assert connection.execute('pragma foreign_key_check').fetchall() == []
    assert handler.sends == 1
