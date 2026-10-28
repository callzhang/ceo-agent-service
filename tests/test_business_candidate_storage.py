"""Current candidate writers against the native SQLite source oracle."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app import store as store_module
from app.business_candidate_definitions import CANDIDATE_FIELDS, candidate_projection
from app.business_candidate_storage import (insert_business_candidate, update_business_candidate,
                                            delete_business_candidate)
from app.store import AutoReplyStore
from app.business_storage import insert_business

SOURCE_METADATA = json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs'
    / '2026-10-17-business-three-current-source-metadata.json').read_text())


@pytest.fixture(params=('actual', 'fresh'))
def source(request, tmp_path):
    path = tmp_path / 'current.sqlite'
    AutoReplyStore(path)
    store_module._close_idle_connections()
    db = sqlite3.connect(path)
    db.execute('pragma foreign_keys=off')
    db.execute('create table test_transaction_marker(id integer primary key)')
    db.commit()
    db.execute('begin')
    yield db, request.param
    db.close()


def _writer_pair(db, variant):
    native = sqlite3.connect(':memory:')
    native.execute('create table test_transaction_marker(id integer primary key)')
    objects = SOURCE_METADATA[f'{variant}_post_target']['wechat_memory_candidates']['objects']
    for kind in ('table', 'index', 'trigger'):
        for obj_kind, _name, _table, sql in objects:
            if obj_kind == kind and sql:
                native.execute(sql)
    native.commit()
    native.execute('begin')
    return native, db


def _sequence(db, name):
    row = db.execute('select seq from sqlite_sequence where name=?', (name,)).fetchone()
    return row[0] if row else None


BASE_VALUES = {
    'attention_item': dict(stable_key='new-key', category='watch', title='Attention',
                           why_attention='why', current_state='state', ceo_action='act',
                           anchor_id=1, evidence_signal_id=10),
    'project_candidate': dict(cluster_id=2, title='Project', reason='reason'),
    'wechat_memory_candidate': dict(import_run_id='run', account_id='account',
                                    statement='fact', category='category', confidence=.5,
                                    sensitivity='sensitivity'),
}


@pytest.mark.parametrize('changes', (
    {'account_id': b'\x00\xff', 'category': '\u2000category\u00a0',
     'source_conversation_ids_json': b'not JSON'},
    {'account_id': memoryview(b'account'), 'confidence': '0.25',
     'sensitivity': 5, 'source_message_ids_json': '\x00text'},
    {'confidence': float('inf'), 'category': b'\xff'},
    {'status': 'native-unconstrained-status',
     'source_conversation_ids_json': 'native text, not JSON',
     'created_at': '2026-10-17 00:00:00', 'updated_at': '2026-10-17 01:00:00'},
))
def test_wechat_native_scalar_affinity(source, changes):
    db, variant = source
    with closing(_writer_pair(db, variant)[0]) as native:
        values = BASE_VALUES['wechat_memory_candidate'] | changes
        columns = tuple(values)
        native.execute('insert into wechat_memory_candidates(' + ','.join(columns)
                       + ') values(' + ','.join('?' for _ in columns) + ')', tuple(values.values()))
        insert_business_candidate(db, 'wechat_memory_candidate', values)
        fields = CANDIDATE_FIELDS['wechat_memory_candidate']
        target_sql = candidate_projection('wechat_memory_candidate')
        assert tuple(native.execute('select ' + ','.join(CANDIDATE_FIELDS['wechat_memory_candidate']) + ' from wechat_memory_candidates')) == tuple(
            db.execute(target_sql))
        n_types = tuple(native.execute('select ' + ','.join('typeof(' + f + ')' for f in fields)
                                       + ' from wechat_memory_candidates').fetchone())
        t_types = tuple(db.execute('select ' + ','.join('typeof(' + f + ')' for f in fields)
                                   + ' from (' + target_sql + ')').fetchone())
        assert n_types == t_types


@pytest.mark.parametrize('identity', (None, 0, -7, float('nan'), 2**63 - 1))
def test_wechat_explicit_and_null_identity_matches_native(source, identity):
    db, variant = source
    with closing(_writer_pair(db, variant)[0]) as native:
        values = BASE_VALUES['wechat_memory_candidate'] | {'id': identity}
        columns = tuple(values)
        result = native.execute('insert into wechat_memory_candidates(' + ','.join(columns)
                                + ') values(' + ','.join('?' for _ in columns) + ')',
                                tuple(values.values()))
        typed = insert_business_candidate(db, 'wechat_memory_candidate', values)
        assert typed.lastrowid == result.lastrowid
        assert tuple(native.execute('select ' + ','.join(CANDIDATE_FIELDS['wechat_memory_candidate']) + ' from wechat_memory_candidates')) == tuple(
            db.execute(candidate_projection('wechat_memory_candidate')))
        assert _sequence(native, 'wechat_memory_candidates') == _sequence(db, 'business:wechat_memory_candidate')


def test_wechat_unique_ignore_update_delete_and_deleted_high_water(source):
    db, variant = source
    with closing(_writer_pair(db, variant)[0]) as native:
        values = BASE_VALUES['wechat_memory_candidate']
        sql = ('insert into wechat_memory_candidates(' + ','.join(values) + ') values('
               + ','.join('?' for _ in values) + ')')
        first = native.execute(sql, tuple(values.values())).lastrowid
        typed = insert_business_candidate(db, 'wechat_memory_candidate', values)
        assert typed.lastrowid == first
        ignored = native.execute(sql.replace('insert into', 'insert or ignore into'),
                                 tuple(values.values()))
        typed_ignored = insert_business_candidate(db, 'wechat_memory_candidate', values, ignore=True)
        assert ignored.rowcount == typed_ignored.rowcount == 0
        assert _sequence(native, 'wechat_memory_candidates') == _sequence(db, 'business:wechat_memory_candidate')
        native.execute('update wechat_memory_candidates set id=900 where id=?', (first,))
        assert update_business_candidate(db, 'wechat_memory_candidate', first, {'id': 900}) == 1
        native.execute('delete from wechat_memory_candidates where id=900')
        assert delete_business_candidate(db, 'wechat_memory_candidate', 900) == 1
        values = values | {'statement': 'second fact'}
        next_native = native.execute(sql, tuple(values.values())).lastrowid
        next_typed = insert_business_candidate(db, 'wechat_memory_candidate', values)
        assert next_typed.lastrowid == next_native == 3
        assert tuple(native.execute('select ' + ','.join(CANDIDATE_FIELDS['wechat_memory_candidate']) + ' from wechat_memory_candidates')) == tuple(
            db.execute(candidate_projection('wechat_memory_candidate')))
        high = values | {'id': 900, 'statement': 'high explicit ID'}
        native.execute('insert into wechat_memory_candidates(' + ','.join(high) + ') values('
                       + ','.join('?' for _ in high) + ')', tuple(high.values()))
        insert_business_candidate(db, 'wechat_memory_candidate', high)
        native.execute('delete from wechat_memory_candidates where id=900')
        delete_business_candidate(db, 'wechat_memory_candidate', 900)
        after = values | {'statement': 'after deleted high ID'}
        assert native.execute(sql, tuple(after.values())).lastrowid == 901
        assert insert_business_candidate(db, 'wechat_memory_candidate', after).lastrowid == 901


@pytest.mark.parametrize('capacity', ('source_kind', 'physical_business'))
def test_plain_insert_sqlite_full_rolls_back_whole_caller_transaction(source, capacity):
    db, variant = source
    with closing(_writer_pair(db, variant)[0]) as native:
        max_id = 2**63 - 1
        native.execute("insert into sqlite_sequence(name,seq) values('wechat_memory_candidates',?)", (max_id,))
        if capacity == 'source_kind':
            db.execute("insert into sqlite_sequence(name,seq) values('business:wechat_memory_candidate',?)", (max_id,))
        else:
            if not db.execute("update sqlite_sequence set seq=? where name='business'",
                              (max_id,)).rowcount:
                db.execute("insert into sqlite_sequence(name,seq) values('business',?)", (max_id,))
        native.commit()
        db.commit()
        native.execute('begin')
        db.execute('begin')
        native.execute('insert into test_transaction_marker(id) values(98)')
        db.execute('insert into test_transaction_marker(id) values(98)')
        values = BASE_VALUES['wechat_memory_candidate']
        sql = ('insert into wechat_memory_candidates(' + ','.join(values) + ') values('
               + ','.join('?' for _ in values) + ')')
        with pytest.raises(sqlite3.OperationalError) as native_error:
            native.execute(sql, tuple(values.values()))
        with pytest.raises(sqlite3.OperationalError) as target_error:
            insert_business_candidate(db, 'wechat_memory_candidate', values)
        assert native_error.value.sqlite_errorname == target_error.value.sqlite_errorname == 'SQLITE_FULL'
        assert not native.in_transaction and not db.in_transaction
        assert native.execute('select 1 from test_transaction_marker where id=98').fetchone() is None
        assert db.execute('select 1 from test_transaction_marker where id=98').fetchone() is None




def test_update_compare_and_set_guards(source):
    db, _ = source
    values = BASE_VALUES['wechat_memory_candidate'] | {'updated_at': '2000-01-01 00:00:00'}
    identity = insert_business_candidate(db, 'wechat_memory_candidate', values).lastrowid
    assert update_business_candidate(db, 'wechat_memory_candidate', identity, {'statement': 'changed'},
                                     expected={'status': 'wrong'}) == 0
    assert update_business_candidate(db, 'wechat_memory_candidate', identity, {'statement': 'changed'},
                                     expected_in={'status': ['pending']}, stale_seconds=1) == 1
    assert update_business_candidate(db, 'wechat_memory_candidate', identity, {'statement': 'again'},
                                     expected={'statement': 'fact'}) == 0
    with pytest.raises(ValueError, match='empty'):
        update_business_candidate(db, 'wechat_memory_candidate', identity, {}, expected_in={'status': []})


@pytest.mark.parametrize('kind,table,values', (
    ('attention_item', 'business_attention_items', {
        'stable_key': 'identity', 'category': 'watch', 'title': 'Attention',
        'why_attention': 'why', 'current_state': 'state', 'ceo_action': 'act',
        'anchor_id': 1, 'evidence_signal_id': 1,
    }),
    ('project_candidate', 'business_project_candidates', {
        'cluster_id': 1, 'title': 'Candidate', 'reason': 'reason',
    }),
    ('wechat_memory_candidate', 'wechat_memory_candidates', BASE_VALUES['wechat_memory_candidate']),
))
@pytest.mark.parametrize('public_id', (0, -9, 7))
def test_candidate_projection_rowid_is_native_public_identity(source, kind, table, values, public_id):
    db, variant = source
    insert_business(db, 'task', {'id': 99, 'title': 'physical identity control', 'stage': 'candidate'})
    with closing(sqlite3.connect(':memory:')) as native:
        objects = SOURCE_METADATA[f'{variant}_post_target'][table]['objects']
        native.execute(next(obj[3] for obj in objects if obj[0] == 'table'))
        supplied = {'id': public_id, **values}
        native.execute('insert into ' + table + '(' + ','.join(supplied) + ') values (' +
                       ','.join('?' for _ in supplied) + ')', tuple(supplied.values()))
        insert_business_candidate(db, kind, supplied)
        native_rowid = native.execute('select rowid from ' + table).fetchone()[0]
        projection = candidate_projection(kind, with_rowid=True)
        projected = db.execute('select _source_rowid from (' + projection + ')').fetchone()[0]
        physical = db.execute('select id from business where record_type=?', (kind,)).fetchone()[0]
        assert native_rowid == projected == public_id
        assert physical != projected
