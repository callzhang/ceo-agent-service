"""Unselected Business candidate storage contract."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.business_definitions import _definitions
from app.action_followup_sync_storage import LINK_TRIGGER_SQL
from app.business_candidate_definitions import (
    CANDIDATE_FIELDS, candidate_projection,
)
from app.business_candidate_storage import (
    capture_business_candidate_source,
    migrate_business_candidate_schema,
    restore_business_candidate_schema,
    insert_business_candidate,
    update_business_candidate,
    delete_business_candidate,
)
from app.business_storage import insert_business

SOURCE_METADATA_PATH = (Path(__file__).parents[1] / 'docs/superpowers/specs'
                        / '2026-10-17-business-three-current-source-metadata.json')
SOURCE_METADATA = json.loads(SOURCE_METADATA_PATH.read_text())


def test_frozen_independent_source_metadata():
    assert hashlib.sha256(SOURCE_METADATA_PATH.read_bytes()).hexdigest() == (
        '922f03417c477b17b0df1bf1541eb5c89d36799ce80ca4e2134c73ddb10c5cd1'
    )


def test_default_business_definitions_equal_frozen_current28():
    assert hashlib.sha256(repr(_definitions()).encode()).hexdigest() == (
        "91fedd3da844ec56624767b78fac27210d342414de7b79c01f02de5f038d9d91"
    )


@pytest.mark.parametrize('kind,table,values', (
    ('attention_item', 'business_attention_items', {
        'stable_key': 'rowid-control', 'category': 'watch', 'title': 'Attention',
        'why_attention': 'why', 'current_state': 'state', 'ceo_action': 'act',
        'anchor_id': 1, 'evidence_signal_id': 1,
    }),
    ('project_candidate', 'business_project_candidates', {
        'cluster_id': 1, 'title': 'Candidate', 'reason': 'reason',
    }),
    ('wechat_memory_candidate', 'wechat_memory_candidates', {
        'import_run_id': 'rowid-control', 'account_id': 'account',
        'statement': 'candidate', 'category': 'unconstrained',
        'confidence': 0.25, 'sensitivity': 'unconstrained',
    }),
))
@pytest.mark.parametrize('public_id', (0, -9, 7))
def test_candidate_projection_rowid_is_native_public_identity(source, kind, table, values, public_id):
    db, variant = source
    if kind == 'attention_item':
        db.execute('insert into business_task_signals(id) values(1)')
        insert_business(db, 'anchor', {'id': 1, 'anchor_type': 'project',
                                       'anchor_ref': 'rowid-control', 'title': 'Anchor'})
    elif kind == 'project_candidate':
        insert_business(db, 'work_cluster', {'id': 1, 'title': 'Cluster'})
    supplied = {'id': public_id, **values}
    db.execute('insert into ' + table + '(' + ','.join(supplied) + ') values (' +
               ','.join('?' for _ in supplied) + ')', tuple(supplied.values()))
    native_rowid = db.execute('select rowid from ' + table).fetchone()[0]
    assert native_rowid == public_id
    metadata = capture_business_candidate_source(db)
    migrate_business_candidate_schema(db, metadata)
    projected_rowid = db.execute('select _source_rowid from (' +
                                 candidate_projection(kind, variant, with_rowid=True) + ')').fetchone()[0]
    prior_expression = candidate_projection(kind, variant, with_rowid=True).replace(
        'public_id as _source_rowid', 'id as _source_rowid', 1)
    prior_rowid = db.execute('select _source_rowid from (' + prior_expression + ')').fetchone()[0]
    assert prior_rowid != native_rowid  # reviewed predecessor expression is a RED control
    assert projected_rowid == native_rowid


@pytest.fixture(params=('actual', 'fresh'))
def source(request):
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('create table actions(id integer primary key,record_type text,legacy_key text)')
        db.execute('create table business_task_signals(id integer primary key)')
        table, indexes, triggers = _definitions()
        for sql in (table, *indexes, *triggers):
            db.execute(sql)
        native = SOURCE_METADATA[f'{request.param}_post_target']
        children = {name: item['fresh_declaration']
                    for name, item in SOURCE_METADATA['incoming_children'].items()}
        if request.param == 'actual':
            children.update(SOURCE_METADATA['actual_incoming_child_declarations'])
        declarations = {**native, **children}
        for part in declarations.values():
            db.execute(next(row[3] for row in part['objects'] if row[0] == 'table'))
        for part in declarations.values():
            for kind, _name, _table, sql in part['objects']:
                if kind in ('index', 'trigger') and sql:
                    db.execute(sql)
        db.execute(LINK_TRIGGER_SQL[2])
        db.commit()
        db.execute('begin')
        yield db, request.param


def test_empty_source_roundtrip(source):
    db, variant = source
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    restore_business_candidate_schema(db, metadata)
    assert capture_business_candidate_source(db)['variant'] == variant


def test_wechat_plain_insert_matches_native(source):
    db, variant = source
    values = dict(import_run_id='synthetic-run', account_id='synthetic-account',
                  statement='synthetic fact', category='native category',
                  confidence=0.25, sensitivity='native sensitivity',
                  status='native-unconstrained-status',
                  source_conversation_ids_json='native text, not JSON',
                  created_at='2026-10-17 00:00:00', updated_at='2026-10-17 00:00:00')
    columns = tuple(values)
    db.execute('savepoint native')
    cursor = db.execute('insert into wechat_memory_candidates(' + ','.join(columns)
                        + ') values(' + ','.join('?' for _ in columns) + ')', tuple(values.values()))
    expected_id = cursor.lastrowid
    expected = tuple(db.execute('select * from wechat_memory_candidates').fetchone())
    db.execute('rollback to native')
    db.execute('release native')
    metadata = capture_business_candidate_source(db)
    migrate_business_candidate_schema(db, metadata)
    result = insert_business_candidate(db, 'wechat_memory_candidate', values)
    assert result.lastrowid == expected_id
    assert tuple(db.execute(candidate_projection('wechat_memory_candidate', variant)).fetchone()) == expected
    restore_business_candidate_schema(db, json.loads(json.dumps(metadata)))
    assert tuple(db.execute('select * from wechat_memory_candidates').fetchone()) == expected


def _seed_business_graph(db):
    db.execute('insert into business_task_signals(id) values(10)')
    insert_business(db, 'anchor', {'id': 1, 'anchor_type': 'project',
                                   'anchor_ref': 'synthetic', 'title': 'Anchor'})
    insert_business(db, 'work_cluster', {'id': 2, 'title': 'Cluster'})
    insert_business(db, 'project', {'id': 3, 'canonical_anchor_id': 1,
                                    'title': 'Project', 'registry_source': 'synthetic'})
    insert_business(db, 'task', {'id': 4, 'title': 'Task', 'stage': 'candidate'})
    insert_business(db, 'work_project', {'id': 5, 'title': 'Work Project'})
    db.execute("insert into business_attention_items(stable_key,category,title,why_attention,"
               "current_state,ceo_action,anchor_id,evidence_signal_id) values(?,?,?,?,?,?,?,?)",
               ('synthetic-key', 'watch', 'Attention', 'why', 'state', 'act', 1, 10))
    db.execute("insert into business_project_candidates(cluster_id,title,reason) values(?,?,?)",
               (2, 'Candidate', 'reason'))
    db.execute("insert into business_attention_events(attention_item_id,event_type,signal_id,"
               "before_json,after_json,reason) values(1,'opened',10,'{}','{}','synthetic')")
    db.execute('insert into business_attention_tasks(attention_item_id,task_id) values(1,4)')
    db.execute('insert into business_attention_proposal_tasks(attention_item_id,task_id) values(1,4)')
    db.execute('insert into business_legacy_links(attention_item_id,work_project_id) values(1,5)')


def _facts(db, names):
    return {name: tuple(tuple(row) for row in db.execute('select * from ' + name + ' order by rowid'))
            for name in names}


def _writer_pair(db):
    native = sqlite3.connect(':memory:')
    db.commit()
    db.backup(native)
    db.execute('begin')
    metadata = capture_business_candidate_source(db)
    migrate_business_candidate_schema(db, metadata)
    db.commit()
    db.execute('pragma foreign_keys=on')
    native.execute('pragma foreign_keys=on')
    db.execute('begin')
    native.execute('begin')
    return native, db


def _sequence(db, name):
    row = db.execute('select seq from sqlite_sequence where name=?', (name,)).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize('damage', ('missing_parent', 'missing_index', 'missing_trigger',
                                    'changed_source', 'temp_collision'))
def test_source_shape_rejected_before_mutation(source, damage):
    db, _ = source
    if damage == 'missing_parent':
        db.execute('drop table wechat_memory_candidates')
    elif damage == 'missing_index':
        db.execute('drop index idx_business_attention_items_list')
    elif damage == 'missing_trigger':
        db.execute('drop trigger trg_sync_legacy_link_parent_insert')
    elif damage == 'changed_source':
        db.execute('alter table business_project_candidates add column extra text')
    else:
        db.execute('create temp table business_attention_items(id)')
    before = '\n'.join(db.iterdump())
    with pytest.raises(ValueError):
        capture_business_candidate_source(db)
    assert '\n'.join(db.iterdump()) == before


def test_changed_target_child_rejected_before_inverse(source):
    db, _ = source
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    db.execute('drop index idx_business_attention_events_item')
    before = '\n'.join(db.iterdump())
    with pytest.raises(ValueError):
        restore_business_candidate_schema(db, metadata)
    assert '\n'.join(db.iterdump()) == before


BASE_VALUES = {
    'attention_item': dict(stable_key='new-key', category='watch', title='Attention',
                           why_attention='why', current_state='state', ceo_action='act',
                           anchor_id=1, evidence_signal_id=10),
    'project_candidate': dict(cluster_id=2, title='Project', reason='reason'),
    'wechat_memory_candidate': dict(import_run_id='run', account_id='account',
                                    statement='fact', category='category', confidence=.5,
                                    sensitivity='sensitivity'),
}


INVALID_CANDIDATES = (
    ('attention_item', {'stable_key': '\u00a0'}),
    ('attention_item', {'category': 'unsupported'}),
    ('attention_item', {'status': 'unsupported'}),
    ('attention_item', {'title': '\u3000'}),
    ('attention_item', {'why_attention': '\t'}),
    ('attention_item', {'current_state': '\n'}),
    ('attention_item', {'ceo_action': '\u2000'}),
    ('attention_item', {'status': 'resolved'}),
    ('attention_item', {'resolution_signal_id': 10}),
    ('attention_item', {'resolved_at': 'now'}),
    ('project_candidate', {'title': '\u00a0'}),
    ('project_candidate', {'reason': '\u3000'}),
    ('project_candidate', {'status': 'unsupported'}),
    ('project_candidate', {'status': 'confirmed'}),
    ('project_candidate', {'confirmed_project_id': 3}),
    ('project_candidate', {'confirmation_signal_id': 10}),
)


@pytest.mark.parametrize('changes', (
    {'account_id': b'\x00\xff', 'category': '\u2000category\u00a0',
     'source_conversation_ids_json': b'not JSON'},
    {'account_id': memoryview(b'account'), 'confidence': '0.25',
     'sensitivity': 5, 'source_message_ids_json': '\x00text'},
    {'confidence': float('inf'), 'category': b'\xff'},
))
def test_wechat_native_scalar_affinity(source, changes):
    db, variant = source
    with closing(_writer_pair(db)[0]) as native:
        values = BASE_VALUES['wechat_memory_candidate'] | changes
        columns = tuple(values)
        native.execute('insert into wechat_memory_candidates(' + ','.join(columns)
                       + ') values(' + ','.join('?' for _ in columns) + ')', tuple(values.values()))
        insert_business_candidate(db, 'wechat_memory_candidate', values)
        fields = CANDIDATE_FIELDS['wechat_memory_candidate']
        target_sql = candidate_projection('wechat_memory_candidate', variant)
        assert tuple(native.execute('select * from wechat_memory_candidates')) == tuple(
            db.execute(target_sql))
        n_types = tuple(native.execute('select ' + ','.join('typeof(' + f + ')' for f in fields)
                                       + ' from wechat_memory_candidates').fetchone())
        t_types = tuple(db.execute('select ' + ','.join('typeof(' + f + ')' for f in fields)
                                   + ' from (' + target_sql + ')').fetchone())
        assert n_types == t_types


@pytest.mark.parametrize('identity', (None, 0, -7, float('nan'), 2**63 - 1))
def test_wechat_explicit_and_null_identity_matches_native(source, identity):
    db, variant = source
    with closing(_writer_pair(db)[0]) as native:
        values = BASE_VALUES['wechat_memory_candidate'] | {'id': identity}
        columns = tuple(values)
        result = native.execute('insert into wechat_memory_candidates(' + ','.join(columns)
                                + ') values(' + ','.join('?' for _ in columns) + ')',
                                tuple(values.values()))
        typed = insert_business_candidate(db, 'wechat_memory_candidate', values)
        assert typed.lastrowid == result.lastrowid
        assert tuple(native.execute('select * from wechat_memory_candidates')) == tuple(
            db.execute(candidate_projection('wechat_memory_candidate', variant)))
        assert _sequence(native, 'wechat_memory_candidates') == _sequence(db, 'business:wechat_memory_candidate')


def test_metadata_tamper_rejected_before_inverse(source):
    db, _ = source
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    metadata['declarations']['business_attention_items']['sql'] += ' '
    before = '\n'.join(db.iterdump())
    with pytest.raises(ValueError):
        restore_business_candidate_schema(db, metadata)
    assert '\n'.join(db.iterdump()) == before

PARENT_FAILURES = (
    ('attention_item', {'anchor_id': 999}),
    ('attention_item', {'evidence_signal_id': 999}),
    ('attention_item', {'status': 'resolved', 'resolution_signal_id': 999,
                        'resolved_at': '2026-10-17 00:00:00'}),
    ('project_candidate', {'cluster_id': 999}),
    ('project_candidate', {'status': 'confirmed', 'confirmed_project_id': 999,
                           'confirmation_signal_id': 10}),
    ('project_candidate', {'status': 'confirmed', 'confirmed_project_id': 3,
                           'confirmation_signal_id': 999}),
)


def test_wechat_unique_ignore_update_delete_and_deleted_high_water(source):
    db, variant = source
    with closing(_writer_pair(db)[0]) as native:
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
        assert tuple(native.execute('select * from wechat_memory_candidates')) == tuple(
            db.execute(candidate_projection('wechat_memory_candidate', variant)))
        high = values | {'id': 900, 'statement': 'high explicit ID'}
        native.execute('insert into wechat_memory_candidates(' + ','.join(high) + ') values('
                       + ','.join('?' for _ in high) + ')', tuple(high.values()))
        insert_business_candidate(db, 'wechat_memory_candidate', high)
        native.execute('delete from wechat_memory_candidates where id=900')
        delete_business_candidate(db, 'wechat_memory_candidate', 900)
        after = values | {'statement': 'after deleted high ID'}
        assert native.execute(sql, tuple(after.values())).lastrowid == 901
        assert insert_business_candidate(db, 'wechat_memory_candidate', after).lastrowid == 901


def test_unrelated_existing_fk_finding_survives_both_directions(source):
    db, variant = source
    db.execute('create table unrelated_parent(id integer primary key)')
    db.execute('create table unrelated_child(id integer primary key, parent_id integer references unrelated_parent(id))')
    db.execute('insert into unrelated_child(id,parent_id) values(1,999)')
    findings = db.execute('pragma foreign_key_check').fetchall()
    assert findings == [('unrelated_child', 1, 'unrelated_parent', 0)]
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    assert db.execute('pragma foreign_key_check').fetchall() == findings
    restore_business_candidate_schema(db, metadata)
    assert db.execute('pragma foreign_key_check').fetchall() == findings
    assert capture_business_candidate_source(db)['variant'] == variant


@pytest.mark.parametrize('capacity', ('source_kind', 'physical_business'))
def test_plain_insert_sqlite_full_rolls_back_whole_caller_transaction(source, capacity):
    db, _ = source
    with closing(_writer_pair(db)[0]) as native:
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
        native.execute('insert into business_task_signals(id) values(98)')
        db.execute('insert into business_task_signals(id) values(98)')
        values = BASE_VALUES['wechat_memory_candidate']
        sql = ('insert into wechat_memory_candidates(' + ','.join(values) + ') values('
               + ','.join('?' for _ in values) + ')')
        with pytest.raises(sqlite3.OperationalError) as native_error:
            native.execute(sql, tuple(values.values()))
        with pytest.raises(sqlite3.OperationalError) as target_error:
            insert_business_candidate(db, 'wechat_memory_candidate', values)
        assert native_error.value.sqlite_errorname == target_error.value.sqlite_errorname == 'SQLITE_FULL'
        assert not native.in_transaction and not db.in_transaction
        assert native.execute('select 1 from business_task_signals where id=98').fetchone() is None
        assert db.execute('select 1 from business_task_signals where id=98').fetchone() is None


def test_reserved_target_temp_index_rejected_before_forward(source):
    db, _ = source
    db.execute('create temp table unrelated_temp(x)')
    db.execute('create index temp.idx_business_attention_stable_key on unrelated_temp(x)')
    before = '\n'.join(db.iterdump())
    with pytest.raises(ValueError):
        capture_business_candidate_source(db)
    assert '\n'.join(db.iterdump()) == before


def test_inverse_reappeared_source_table_rejected_before_ddl(source):
    db, _ = source
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    db.execute('create table business_attention_items(id integer primary key)')
    before = '\n'.join(db.iterdump())
    with pytest.raises(ValueError):
        restore_business_candidate_schema(db, metadata)
    assert '\n'.join(db.iterdump()) == before


def test_unrelated_temp_name_is_allowed(source):
    db, variant = source
    db.execute('create temp table unrelated_temp(x)')
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    restore_business_candidate_schema(db, metadata)
    assert capture_business_candidate_source(db)['variant'] == variant


def test_no_current_writes_restores_exact_business_counter_absence_and_rows(source):
    db, variant = source
    before_business = tuple(db.execute('select * from business order by id'))
    before_sequence = tuple(db.execute('select name,seq from sqlite_sequence order by name'))
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    restore_business_candidate_schema(db, metadata)
    assert tuple(db.execute('select * from business order by id')) == before_business
    assert tuple(db.execute('select name,seq from sqlite_sequence order by name')) == before_sequence
    assert capture_business_candidate_source(db)['variant'] == variant


def test_new_kind_deleted_highwater_restores_native_counter(source):
    db, _ = source
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    values = BASE_VALUES['wechat_memory_candidate'] | {'id': 550}
    insert_business_candidate(db, 'wechat_memory_candidate', values)
    delete_business_candidate(db, 'wechat_memory_candidate', 550)
    assert _sequence(db, 'business:wechat_memory_candidate') == 550
    restore_business_candidate_schema(db, metadata)
    assert _sequence(db, 'wechat_memory_candidates') == 550
    assert db.execute('select count(*) from wechat_memory_candidates').fetchone() == (0,)
    assert db.execute('select count(*) from business where record_type=?',
                      ('wechat_memory_candidate',)).fetchone() == (0,)


def test_candidate_source_fk_violation_rejected_before_ddl(source):
    db, _ = source
    db.execute("insert into business_attention_items(stable_key,category,title,why_attention,"
               "current_state,ceo_action,anchor_id,evidence_signal_id) values(?,?,?,?,?,?,?,?)",
               ('orphan', 'watch', 'Attention', 'why', 'state', 'act', 999, 999))
    before = '\n'.join(db.iterdump())
    with pytest.raises(ValueError, match='broken Business candidate source FK'):
        capture_business_candidate_source(db)
    assert '\n'.join(db.iterdump()) == before


def test_temp_target_index_case_collision_rejected_before_ddl(source):
    db, _ = source
    db.execute('create temp table unrelated_temp(x)')
    db.execute('create index temp.IDX_BUSINESS_ATTENTION_STABLE_KEY on unrelated_temp(x)')
    before = '\n'.join(db.iterdump())
    assert db.in_transaction
    with pytest.raises(ValueError, match='TEMP collision'):
        capture_business_candidate_source(db)
    assert db.in_transaction
    assert '\n'.join(db.iterdump()) == before


def test_unrelated_view_and_trigger_do_not_block_candidate_conversion(source):
    db, variant = source
    db.execute('create table unrelated_read_target(id integer primary key)')
    db.execute('create view unrelated_reader as select id from unrelated_read_target')
    db.execute("create trigger unrelated_reader_trigger after insert on actions begin "
               "select id from unrelated_read_target; end")
    metadata = json.loads(json.dumps(capture_business_candidate_source(db)))
    migrate_business_candidate_schema(db, metadata)
    restore_business_candidate_schema(db, metadata)
    assert capture_business_candidate_source(db)['variant'] == variant
