"""Current-fact postfix closure forward/inverse with unchanged nine-type facts."""
import sqlite3

import pytest

from app import action_storage as storage
from app.action_postfix_storage import (
    SOURCE_POSTFIX_DECLARATIONS, capture_action_postfix_schema,
    migrate_action_postfix_schema, restore_action_postfix_schema,
)
from tests.test_action_receipt_storage import target_db as target_db
from tests.test_action_receipt_source import source_values


def source_db(db):
    for source in SOURCE_POSTFIX_DECLARATIONS.values():
        for obj in source['objects']:
            db.execute(obj['sql'])
    for channel, key in [('dingtalk', '01'), ('dingtalk', '1'), ('wechat', '01'), ('wechat', 'unused')]:
        db.execute('insert into outbound_postfixes(channel,delivery_key,final_body,feedback_token,postfix_version) values(?,?,?,?,?)',
                   (channel, key, ' raw body '+key, ' token ', 'original version'))
    db.execute("insert into outbound_postfix_receipts(channel,delivery_key,provider_result_json) values('dingtalk','01',' not JSON ')")
    db.execute("insert into native_reply_dispatches(channel,delivery_key) values('dingtalk','01')")
    storage.insert_action(db, 'external_action_results', source_values('external_action_results', token='independent'))
    storage.insert_action(db, 'agent_execution_receipts', {**source_values('agent_execution_receipts'), 'id': 115})
    storage.delete_action(db, 'agent_execution_receipts', 115)
    db.commit()
    db.execute('pragma foreign_keys=off')
    return db


def nine_snapshot(db):
    columns = [row[1] for row in db.execute('pragma table_xinfo(actions)') if row[6] == 0]
    return [tuple(row) for row in db.execute('select '+','.join(columns)+' from actions where record_type in ('+
            ','.join('?' for _ in storage.ALL_ACTION_TYPES)+') order by id', storage.ALL_ACTION_TYPES)]


def postfix_snapshot(db, target=False):
    return {kind:[tuple(row) for row in db.execute('select '+','.join(storage.SOURCE_COLUMNS[kind])+' from '+
            ('('+storage.ACTION_PROJECTIONS[kind]+')' if target else kind)+' order by channel,delivery_key')]
            for kind in storage.POSTFIX_ACTION_TYPES}


@pytest.mark.parametrize('row_factory', [None, sqlite3.Row])
def test_source_nine_current_fact_inverse_and_forward_again(target_db, row_factory):
    db = source_db(target_db)
    db.row_factory = row_factory
    original_nine, original_postfix = nine_snapshot(db), postfix_snapshot(db)
    state = capture_action_postfix_schema(db)
    db.execute('begin immediate')
    migrate_action_postfix_schema(db)
    assert storage.action_schema_is_current(db, include_postfix=True)
    assert nine_snapshot(db) == original_nine
    assert postfix_snapshot(db, True) == original_postfix
    assert db.execute("select seq from sqlite_sequence where name='actions:agent_execution_receipts'").fetchone()[0] == 115
    storage.insert_action(db, 'outbound_postfixes', dict(channel='wechat', delivery_key='new', final_body=' new body ', feedback_token='', postfix_version='new'))
    storage.insert_action(db, 'native_reply_dispatches', dict(channel='wechat', delivery_key='new'))
    storage.update_action(db, 'outbound_postfixes', ('dingtalk','01'), {'final_body':' changed body '})
    storage.update_action(db, 'outbound_postfix_receipts', ('dingtalk','01'), {'provider_result_json':' changed receipt '})
    storage.delete_action(db, 'outbound_postfixes', ('wechat','unused'))
    new = storage.insert_action(db, 'agent_execution_receipts', source_values('agent_execution_receipts', token='current'))
    assert new.lastrowid == 116
    current_nine, current_postfix = nine_snapshot(db), postfix_snapshot(db, True)
    restore_action_postfix_schema(db, state)
    assert storage._objects(db,'actions') == state['actions']
    assert nine_snapshot(db) == current_nine
    assert postfix_snapshot(db) == current_postfix
    assert db.execute("select seq from sqlite_sequence where name='actions:agent_execution_receipts'").fetchone()[0] == 116
    migrate_action_postfix_schema(db)
    assert nine_snapshot(db) == current_nine
    assert postfix_snapshot(db, True) == current_postfix
    assert storage.insert_action(db, 'agent_execution_receipts', source_values('agent_execution_receipts', token='next')).lastrowid == 117


@pytest.mark.parametrize('mutation', ['index', 'inbound', 'view', 'check', 'blob', 'orphan'])
def test_unsupported_or_invalid_source_is_refused_before_changes(target_db, mutation):
    db = source_db(target_db)
    if mutation == 'index':
        db.execute('create index unknown_postfix_index on outbound_postfixes(created_at)')
    elif mutation == 'inbound':
        db.execute('create table unknown_child(channel text,delivery_key text, foreign key(channel,delivery_key) references outbound_postfixes(channel,delivery_key))')
    elif mutation == 'view':
        db.execute('create view unknown_reader as select final_body from outbound_postfixes')
    elif mutation == 'check':
        db.execute('pragma ignore_check_constraints=on')
        # Nullable source checks cannot be bypassed, so use the actual blank
        # CHECK case rather than pretending source NOT NULL allows NULL.
        db.execute("update outbound_postfixes set final_body=' '")
        db.execute('pragma ignore_check_constraints=off')
    elif mutation == 'blob':
        db.execute("update outbound_postfix_receipts set provider_result_json=x'ff00'")
    else:
        db.execute("insert into native_reply_dispatches(channel,delivery_key) values('dingtalk','missing')")
    db.commit()
    before = tuple(db.iterdump())
    db.execute('begin immediate')
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        migrate_action_postfix_schema(db)
    assert tuple(db.iterdump()) == before
