"""Finite delivery transition against independent frozen source declarations."""
import sqlite3

import pytest

from app import action_storage as storage
from app.action_delivery_storage import (
    capture_action_delivery_schema, migrate_action_delivery_schema,
    restore_action_delivery_schema, _stored_columns,
)
from app.action_postfix_storage import migrate_action_postfix_schema
from app.run_storage import insert_run
from app.task_storage import insert_task
from tests.test_action_postfix_migration import source_db as postfix_source_db
from tests.test_action_receipt_source import source_values
from tests.test_sent_delivery_source import SOURCE
from tests.test_action_receipt_storage import target_db  # noqa: F401


def delivery_source(db, variant):
    postfix_source_db(db)
    db.execute('begin immediate')
    migrate_action_postfix_schema(db)
    storage.insert_action(db, 'agent_effect_intents', {
        **source_values('agent_effect_intents', token='delivery-water'), 'id': 500,
    })
    storage.delete_action(db, 'agent_effect_intents', 500)
    db.commit()
    definition = SOURCE[variant]
    for kind in storage.DELIVERY_ACTION_TYPES:
        db.execute(next(obj['sql'] for obj in definition[kind]['objects'] if obj['type'] == 'table'))
    for kind in storage.DELIVERY_ACTION_TYPES:
        for obj in definition[kind]['objects']:
            if obj['type'] != 'table':
                db.execute(obj['sql'])
    for task_id, run_id in ((21, 11), (22, 12)):
        insert_task(db, 'reply_tasks', dict(id=task_id, conversation_id=f'chat-{task_id}',
                    conversation_title='source', single_chat=0, trigger_message_id=f'message-{task_id}',
                    trigger_create_time='time', trigger_sender='sender'))
        insert_run(db, 'agent_runs', dict(id=run_id, task_id=task_id,
                   execution_generation='generation', role='consumer'))
    values = dict(id=701, conversation_id=' raw\x00chat ', trigger_message_id='trigger',
                  reply_text=' text\x00body ', sent_at=' time ', send_result_json=' not JSON\x00 ',
                  recall_key=' key ', recall_status='unknown', recall_error=' error ',
                  recalled_at=None, feedback_token=' token ', channel='', agent_run_id=0,
                  external_action_key='external')
    fields = storage.SOURCE_COLUMNS['sent_replies']
    db.execute(f"insert into sent_replies({','.join(fields)}) values({','.join('?' for _ in fields)})",
               tuple(values[field] for field in fields))
    db.execute('insert into sent_reply_observers values(701,11,21,?)', (' time\x00one ',))
    db.execute("insert into sent_reply_observers values(701,12,22,' time two ')")
    db.execute("insert into sent_replies(id,conversation_id,trigger_message_id,reply_text) values(901,'c','t','second')")
    db.execute('delete from sent_replies where id=901')
    db.commit()
    db.execute('pragma foreign_keys=off')
    return db


def action_snapshot(db, delivery=False):
    fields = _stored_columns()
    condition = '' if delivery else "where record_type not in ('sent_replies','sent_reply_observers')"
    return [tuple((value, type(value)) for value in row) for row in db.execute(
        f"select {','.join(fields)} from actions {condition} order by id")]


def delivery_snapshot(db, target=False):
    result = {}
    for kind in storage.DELIVERY_ACTION_TYPES:
        fields = storage.SOURCE_COLUMNS[kind]
        table = f'({storage.ACTION_PROJECTIONS[kind]})' if target else kind
        result[kind] = [tuple((value, type(value)) for value in row) for row in db.execute(
            f"select {','.join(fields)} from {table} order by " +
            ('id' if kind == 'sent_replies' else 'sent_reply_id,agent_run_id'))]
    return result


@pytest.mark.parametrize('variant', tuple(SOURCE))
@pytest.mark.parametrize('row_factory', [None, sqlite3.Row])
def test_forward_current_fact_inverse_repeat(target_db, variant, row_factory):  # noqa: F811
    db = delivery_source(target_db, variant)
    db.row_factory = row_factory
    twelve = action_snapshot(db)
    source = delivery_snapshot(db)
    state = capture_action_delivery_schema(db)
    assert state['sent_sequence'] == 901
    db.execute('begin immediate')
    migrate_action_delivery_schema(db)
    assert storage.action_schema_is_current(db, include_delivery=True)
    assert action_snapshot(db) == twelve
    assert delivery_snapshot(db, True) == source
    assert db.execute("select seq from sqlite_sequence where name='actions:agent_effect_intents'").fetchone()[0] == 500
    assert db.execute("select seq from sqlite_sequence where name='actions:agent_execution_receipts'").fetchone()[0] == 115
    assert storage.action_physical_id(db, 'sent_replies', 701) != 701
    storage.update_action(db, 'sent_replies', 701, {'recall_status': 'recalled', 'recalled_at': ' raw time\x00 '})
    storage.insert_action(db, 'sent_replies', dict(conversation_id='new', trigger_message_id='new', reply_text='new'))
    storage.delete_action(db, 'sent_replies', 902)
    storage.insert_action(db, 'sent_replies', dict(conversation_id='surviving',
                                                 trigger_message_id='surviving', reply_text='surviving'))
    storage.update_action(db, 'sent_reply_observers', (701, 12),
                          {'sent_reply_id': 903, 'agent_run_id': 1, 'reply_task_id': 1})
    storage.insert_action(db, 'sent_reply_observers', dict(sent_reply_id=701, agent_run_id=1,
                                                          reply_task_id=1, created_at=' new observer '))
    storage.insert_action(db, 'agent_execution_receipts', source_values('agent_execution_receipts', token='new'))
    storage.update_action(db, 'outbound_postfixes', ('dingtalk', '01'), {'final_body': ' changed body '})
    storage.insert_action(db, 'outbound_postfixes', dict(channel='wechat', delivery_key='new',
                          final_body=' new body ', postfix_version='new'))
    storage.delete_action(db, 'external_action_results', 'independent')
    current_twelve = action_snapshot(db)
    current_delivery = delivery_snapshot(db, True)
    current_seq = db.execute("select seq from sqlite_sequence where name=?", (storage.DELIVERY_SEQUENCE_NAME,)).fetchone()[0]
    restore_action_delivery_schema(db, state)
    assert action_snapshot(db) == current_twelve
    assert delivery_snapshot(db) == current_delivery
    assert db.execute("select seq from sqlite_sequence where name='actions:agent_effect_intents'").fetchone()[0] == 500
    assert db.execute("select seq from sqlite_sequence where name='actions:agent_execution_receipts'").fetchone()[0] == 116
    assert db.execute("select seq from sqlite_sequence where name='sent_replies'").fetchone()[0] == current_seq
    assert not db.execute("select 1 from sqlite_sequence where name=?", (storage.DELIVERY_SEQUENCE_NAME,)).fetchone()
    assert [row[1] for row in db.execute('pragma table_info(sent_replies)')] == [c['name'] for c in state['tables']['sent_replies']['columns']]
    assert {kind: storage._objects(db, kind) for kind in storage.DELIVERY_ACTION_TYPES} == {
        kind: state['tables'][kind]['objects'] for kind in storage.DELIVERY_ACTION_TYPES}
    migrate_action_delivery_schema(db)
    assert action_snapshot(db) == current_twelve
    assert delivery_snapshot(db, True) == current_delivery
    restore_action_delivery_schema(db, state)
    assert delivery_snapshot(db) == current_delivery


@pytest.mark.parametrize('variant', tuple(SOURCE))
@pytest.mark.parametrize('mutation', ['index', 'inbound', 'view', 'blob', 'orphan', 'wrong_run', 'sequence'])
def test_bad_source_refused_without_mutation(target_db, variant, mutation):  # noqa: F811
    db = delivery_source(target_db, variant)
    if mutation == 'index':
        db.execute('create index unexpected_sent on sent_replies(reply_text)')
    elif mutation == 'inbound':
        db.execute('create table unexpected_child(sent_id integer references sent_replies(id))')
    elif mutation == 'view':
        db.execute('create view unexpected_view as select reply_text from sent_replies')
    elif mutation == 'blob':
        db.execute("update sent_replies set send_result_json=x'ff00'")
    elif mutation == 'orphan':
        db.execute('insert into sent_reply_observers values(5000,11,21,\'x\')')
    elif mutation == 'wrong_run':
        name = 'trg_runs_child_sent_reply_observers_agent_run_id_update'
        sql = next(obj['sql'] for obj in SOURCE[variant]['sent_reply_observers']['objects']
                   if obj['name'] == name)
        db.execute(f'drop trigger {name}')
        db.execute('update sent_reply_observers set agent_run_id=2 where agent_run_id=11')
        db.execute(sql)
    else:
        db.execute("insert into sqlite_sequence(name,seq) values(?,1)", (storage.DELIVERY_SEQUENCE_NAME,))
    db.commit()
    before = tuple(db.iterdump())
    db.execute('begin immediate')
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        migrate_action_delivery_schema(db)
    assert tuple(db.iterdump()) == before


@pytest.mark.parametrize('variant', tuple(SOURCE))
def test_ignored_source_allocation_and_target_inverse_refusal(target_db, variant):  # noqa: F811
    db = delivery_source(target_db, variant)
    db.execute("insert or ignore into sent_replies(conversation_id,trigger_message_id,reply_text,external_action_key) "
               "values('ignored','ignored','ignored','external')")
    assert db.execute("select seq from sqlite_sequence where name='sent_replies'").fetchone()[0] == 902
    db.commit()
    state = capture_action_delivery_schema(db)
    db.execute('begin immediate')
    migrate_action_delivery_schema(db)
    assert db.execute("select seq from sqlite_sequence where name=?", (storage.DELIVERY_SEQUENCE_NAME,)).fetchone()[0] == 902
    result = storage.insert_action(db, 'sent_replies', dict(conversation_id='next',
                                    trigger_message_id='next', reply_text='next'))
    assert result.lastrowid == 903
    observer_id = storage.action_physical_id(db, 'sent_reply_observers', (701, 11))
    db.execute('update actions set reply_task_id=999 where id=?', (observer_id,))
    before = tuple(db.iterdump())
    with pytest.raises(ValueError):
        restore_action_delivery_schema(db, state)
    assert tuple(db.iterdump()) == before
    db.execute('update actions set reply_task_id=21 where id=?', (observer_id,))
    restore_action_delivery_schema(db, state)
    assert db.execute("select seq from sqlite_sequence where name='sent_replies'").fetchone()[0] == 903


@pytest.mark.parametrize('variant', tuple(SOURCE))
def test_inverse_metadata_and_dependency_rejected_before_mutation(target_db, variant):  # noqa: F811
    db = delivery_source(target_db, variant)
    state = capture_action_delivery_schema(db)
    db.execute('begin immediate')
    migrate_action_delivery_schema(db)
    before = tuple(db.iterdump())
    bad = state | {'sent_sequence': -1}
    with pytest.raises(ValueError):
        restore_action_delivery_schema(db, bad)
    assert tuple(db.iterdump()) == before
    db.execute('create view unexpected_delivery_reader as select legacy_key from actions')
    before = tuple(db.iterdump())
    with pytest.raises(ValueError):
        restore_action_delivery_schema(db, state)
    assert tuple(db.iterdump()) == before
