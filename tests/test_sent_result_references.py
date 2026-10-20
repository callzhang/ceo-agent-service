"""Sent owns only exceptional bodies; exact provider bytes have one owner."""

import inspect
import sqlite3

import pytest

from app import action_storage as actions
from app.store import AutoReplyStore
from tests.test_agent_turn_store import _claim_consumer, _task
from tests.test_provenance_vector_storage import db as db


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


def _source(db):
    from tests.test_execution_owner import _source as predecessor
    from app.execution_owner_schema import migrate_execution_owner
    predecessor(db)
    migrate_execution_owner(db)
    for key, body in [('equal', ' { "raw": [2, 1] } '), ('different', '{"receipt":1}'), ('non-json', 'raw\x00receipt')]:
        _owner(db, key, body)
    for public, key, body in [(41, 'equal', ' { "raw": [2, 1] } '),
                              (42, 'different', '{}'), (43, '', 'legacy raw'),
                              (44, 'unmatched', 'missing raw'), (45, 'non-json', 'raw\x00receipt')]:
        _sent(db, key, body, public=public)
    actions.insert_action(db, 'sent_reply_observers', dict(sent_reply_id=41, agent_run_id=1, reply_task_id=1))
    db.execute("update sqlite_sequence set seq=190 where name='actions:sent_replies'")


def test_forward_inverse_exact_raw_facts_observers_and_waters(db):
    from app.sent_result_schema import migrate_sent_result_references, restore_sent_result_references
    from tests.test_model_action_schema import _snapshot
    _source(db)
    before = _snapshot(db)
    projection = db.execute(actions.ACTION_PROJECTIONS['sent_replies'] + ' order by id').fetchall()
    metadata = migrate_sent_result_references(db)
    assert metadata == {'reply_variant': actions._reply_variant_for(db)}
    assert db.execute("select legacy_key,send_result_json from actions where record_type='sent_replies' order by cast(legacy_key as integer)").fetchall() == [
        ('41', None), ('42', '{}'), ('43', 'legacy raw'), ('44', 'missing raw'), ('45', None)]
    assert db.execute(actions.SENT_REPLY_SQL + ' order by id').fetchall() == projection
    assert db.execute('pragma foreign_key_check').fetchall() == []
    restore_sent_result_references(db, metadata)
    assert _snapshot(db) == before


@pytest.mark.parametrize('mutation', ['body', 'key', 'delete', 'no_match'])
def test_owner_mutation_preserves_untouched_sent_and_explicit_correction(db, mutation):
    from app.sent_result_schema import migrate_sent_result_references
    _source(db)
    migrate_sent_result_references(db)
    original = ' { "raw": [2, 1] } '
    if mutation == 'delete':
        actions.delete_action(db, 'external_action_results', 'equal')
    elif mutation == 'key':
        actions.update_action(db, 'external_action_results', 'equal', {'external_action_key': 'renamed'})
    else:
        actions.update_action(db, 'external_action_results', 'equal', {'provider_result_json': 'new raw'},
                              where_sql='provider_result_json=?', parameters=('not equal' if mutation == 'no_match' else original,))
    assert db.execute('select send_result_json from (' + actions.SENT_REPLY_SQL + ') where id=41').fetchone() == (original,)
    assert db.execute("select send_result_json from actions where record_type='sent_replies' and legacy_key='41'").fetchone() == ((None,) if mutation == 'no_match' else (original,))
    if mutation == 'body':
        actions.update_action(db, 'sent_replies', 41, {'send_result_json': 'new raw'})
        assert db.execute("select send_result_json from actions where record_type='sent_replies' and legacy_key='41'").fetchone() == (None,)
        assert db.execute('select send_result_json from (' + actions.SENT_REPLY_SQL + ') where id=41').fetchone() == ('new raw',)


def test_new_fact_inverse_and_allocator_continuation(db):
    from app.sent_result_schema import migrate_sent_result_references, restore_sent_result_references
    _source(db)
    metadata = migrate_sent_result_references(db)
    # Sent can arrive before its independent receipt; no receipt is invented.
    public = _sent(db, 'new', 'new raw', public=None)
    assert public == 191
    _owner(db, 'new', 'new raw')
    physical = actions.action_physical_id(db, 'sent_replies', public)
    actions.update_action(db, 'sent_replies', public, {'recall_status': 'recalled', 'recalled_at': 'new-clock'})
    current = db.execute(actions.SENT_REPLY_SQL + ' order by id').fetchall()
    assert db.execute('select send_result_json from actions where id=?', (physical,)).fetchone() == (None,)
    restore_sent_result_references(db, metadata)
    assert db.execute(actions.ACTION_PROJECTIONS['sent_replies'] + ' order by id').fetchall() == current
    assert actions.action_physical_id(db, 'sent_replies', public) == physical
    assert _sent(db, '', 'legacy after inverse', public=None) == 192


def test_missing_null_owner_cannot_commit_but_atomic_pair_can(db):
    from app.sent_result_schema import migrate_sent_result_references
    _source(db)
    migrate_sent_result_references(db)
    db.commit()
    db.execute('pragma foreign_keys=on')
    db.execute('begin immediate')
    _sent(db, 'future', None, public=191)
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.commit()
    assert db.in_transaction
    _owner(db, 'future', 'provider raw')
    db.commit()
    assert db.execute('pragma foreign_key_check').fetchall() == []
    db.execute('begin immediate')
    _sent(db, '', None, public=192)
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.commit()
    db.rollback()


def test_unknown_source_refuses_before_writes(db):
    from app.sent_result_schema import migrate_sent_result_references
    from tests.test_model_action_schema import _snapshot
    _source(db)
    db.execute('create index unknown_sent_index on actions(sent_at)')
    before = _snapshot(db)
    with pytest.raises(ValueError, match='declaration'):
        migrate_sent_result_references(db)
    assert _snapshot(db) == before


@pytest.mark.parametrize('invalid', ['null', '17'])
def test_invalid_source_result_refuses_before_writes(db, invalid):
    from app.sent_result_schema import migrate_sent_result_references
    from tests.test_model_action_schema import _snapshot
    _source(db)
    path = actions.action_payloads(actions._reply_variant_for(db)).path('sent_replies', 'send_result_json')
    db.execute('pragma ignore_check_constraints=on')
    db.execute("update actions set payload_json=jsonb_set(payload_json,?,json(?)) "
               "where record_type='sent_replies' and legacy_key='41'", (path, invalid))
    db.execute('pragma ignore_check_constraints=off')
    before = _snapshot(db)
    with pytest.raises(ValueError, match='integrity'):
        migrate_sent_result_references(db)
    assert _snapshot(db) == before


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


def test_source_declarations_and_business_layout_are_frozen():
    from app.action_email_storage import _fingerprint
    from app.execution_owner_schema import execution_action_objects
    from app.sent_result_schema import sent_action_objects
    for variant, digest in {
        'actual_no_memory_fk': '829ed4f2f5e9fcb3d0eb3b780c6e5857e3e4e050255fbaf18a64ce6db5598e3e',
        'fresh_memory_fk': '7862eb54c651e272bb8c917c9232d91ec3b3663200eeedaf865b3b72d26a0bc4',
    }.items():
        assert _fingerprint(execution_action_objects(variant)) == digest
        assert sent_action_objects(variant, sent_result_references=False) == execution_action_objects(variant)
        assert actions.DEFAULTS['sent_replies']['send_result_json'] == ''
        assert actions.NULLABLE_PAYLOAD_FIELDS['sent_replies'] == {'recalled_at'}
        assert actions.action_payloads(variant).layouts['sent_replies'].fields == actions.action_payloads(variant, execution_owner=True).layouts['sent_replies'].fields


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
