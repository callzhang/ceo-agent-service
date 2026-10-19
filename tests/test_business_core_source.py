"""Native source contract for ten business families, without target/runtime code.

Actual/fresh captured DDL and owned triggers run in SQLite memory databases.
Only synthetic values are written. External families are endpoint metadata only.
"""

from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3

import pytest

CAPTURE_PATH = Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json'
CAPTURE = json.loads(CAPTURE_PATH.read_text())
NAMES = tuple(CAPTURE['actual']['tables'])
COUNTS = (19, 13, 4, 4, 10, 8, 12, 6, 3, 5)
AUTO = tuple(n for n in NAMES if n not in ('business_task_evidence', 'business_project_evidence'))
IMMUTABLE = ('business_task_signals', 'business_source_documents', 'business_task_date_evidence')


def insert(db, name, **values):
    return db.execute(f"insert into {name}({','.join(values)}) values({','.join('?' for _ in values)})", tuple(values.values()))


def values(name, suffix='a'):
    return {
        'business_tasks': dict(title='synthetic', stage='candidate'),
        'business_source_documents': dict(identity_key=suffix, body='synthetic body'),
        'business_task_signals': dict(source_type='synthetic', source_ref=suffix, source_document_id=-12, dedupe_key=suffix),
        'business_task_evidence': dict(task_id=-11, signal_id=-13, evidence_role='discovery'),
        'business_task_date_evidence': dict(task_id=-11, date_type='next_check_at', raw_phrase='synthetic phrase', source_signal_id=-13, actor_kind='unknown'),
        'business_task_events': dict(task_id=-11, event_type='created', before_json='{}', after_json='{}', reason='synthetic'),
        'business_anchors': dict(anchor_type='project', anchor_ref=suffix, title='synthetic'),
        'business_projects': dict(canonical_anchor_id=-14, title='synthetic', registry_source='synthetic'),
        'business_project_evidence': dict(project_id=-15, signal_id=-13),
        'business_project_context_revisions': dict(project_id=-15, context_json='{}', evidence_json='[]'),
    }[name]


def seed(db):
    for name, pk in (('business_tasks', -11), ('business_source_documents', -12), ('business_task_signals', -13), ('business_anchors', -14), ('business_projects', -15)):
        insert(db, name, id=pk, **values(name, 'seed'))
    db.commit()


def sequence(db, name):
    row = db.execute('select seq from sqlite_sequence where name=?', (name,)).fetchone()
    return None if row is None else row[0]


def pragma(db, kind, name):
    cursor = db.execute(f'pragma {kind}("{name}")')
    return [dict(zip((c[0] for c in cursor.description), row, strict=True)) for row in cursor]


@pytest.fixture(params=('actual', 'fresh'))
def variant(request):
    return request.param


@pytest.fixture
def db(variant):
    source = CAPTURE[variant]
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.execute('pragma foreign_keys=on')
        for table in source['tables'].values():
            connection.execute(table['sql'])
        for table in source['tables'].values():
            for index in reversed(table['indexes']):
                if index['sql']:
                    connection.execute(index['sql'])
            for trigger in table['triggers']:
                connection.execute(trigger['sql'])
        connection.commit()
        yield connection


def test_complete_source_artifact_exists():
    assert CAPTURE_PATH.is_file()
    assert len(NAMES) == 10
    assert tuple(len(CAPTURE['actual']['tables'][n]['columns']) for n in NAMES) == COUNTS
    assert sum(COUNTS) == 84
    assert tuple(CAPTURE['actual']['tables'][n]['row_count'] for n in NAMES) == (594, 833, 188, 897, 0, 893, 32, 50, 33, 6)
    assert sum(t['row_count'] for t in CAPTURE['actual']['tables'].values()) == 3526
    assert all(t['row_count'] == 0 and t['sequence'] is None for t in CAPTURE['fresh']['tables'].values())
    assert CAPTURE['source_binding']['published_code'] == '7dadc492c1c62d5b0624f2f97dd0c89f4f358646'


def test_all_84_actual_fields_match_approved_source_csv():
    with (CAPTURE_PATH.parent / '2026-10-11-database-redesign-field-map.csv').open() as source_csv:
        rows = list(csv.DictReader(source_csv))
    mapped = {(r['old_table'], r['old_column']): (r['sql_type'], int(r['not_null']), int(r['primary_key_position'])) for r in rows if r['old_table'] in NAMES}
    actual = {(name, c['name']): (c['type'], c['notnull'], c['pk']) for name, table in CAPTURE['actual']['tables'].items() for c in table['columns']}
    assert len(mapped) == len(actual) == 84
    assert mapped == actual


def test_exact_native_metadata(db, variant):
    source = CAPTURE[variant]
    assert sum(len(t['indexes']) for t in source['tables'].values()) == 16
    assert sum(len(t['check_expressions']) for t in source['tables'].values()) == 37
    assert sum(len(t['triggers']) for t in source['tables'].values()) == 9
    assert sum(len(t['foreign_keys']) for t in source['tables'].values()) == 13
    for name, table in source['tables'].items():
        assert db.execute('select sql from sqlite_schema where name=?', (name,)).fetchone()[0] == table['sql']
        assert pragma(db, 'table_xinfo', name) == table['columns']
        assert pragma(db, 'foreign_key_list', name) == table['foreign_keys']
        assert pragma(db, 'index_list', name) == [{k: i[k] for k in ('seq', 'name', 'unique', 'origin', 'partial')} for i in table['indexes']]
        for index in table['indexes']:
            assert pragma(db, 'index_xinfo', index['name']) == index['xinfo']
            assert db.execute('select sql from sqlite_schema where name=?', (index['name'],)).fetchone()[0] == index['sql']
            assert all(x['coll'] == 'BINARY' for x in index['xinfo'])
        assert [dict(zip(('type', 'name', 'tbl_name', 'sql'), r, strict=True)) for r in db.execute('select type,name,tbl_name,sql from sqlite_schema where type=\'trigger\' and tbl_name=? order by rowid', (name,))] == table['triggers']
        assert table['identity'] == dict(autoincrement=name in AUTO, without_rowid=False, integer_primary_key_rowid_alias=name in AUTO)
        assert all(fk['table'] in NAMES for fk in table['foreign_keys'])
    assert db.execute('pragma foreign_key_check').fetchall() == []


def test_external_endpoint_map_is_exact(variant):
    source = CAPTURE[variant]
    expected = {
        'business_task_dingtalk_links': {'business_task_id': 'business_tasks'},
        'business_task_follow_ups': {'business_task_id': 'business_tasks', 'source_signal_id': 'business_task_signals'},
        'business_task_todo_sync_outbox': {'business_task_id': 'business_tasks'},
        'business_task_relations': {'from_task_id': 'business_tasks', 'to_task_id': 'business_tasks', 'supporting_signal_id': 'business_task_signals'},
        'business_work_cluster_tasks': {'task_id': 'business_tasks'},
        'business_task_anchor_links': {'task_id': 'business_tasks', 'anchor_id': 'business_anchors', 'evidence_signal_id': 'business_task_signals'},
        'business_project_candidates': {'confirmed_project_id': 'business_projects', 'confirmation_signal_id': 'business_task_signals'},
        'business_attention_items': {'anchor_id': 'business_anchors', 'evidence_signal_id': 'business_task_signals', 'resolution_signal_id': 'business_task_signals'},
        'business_attention_tasks': {'task_id': 'business_tasks'},
        'business_attention_proposal_tasks': {'task_id': 'business_tasks'},
        'business_attention_events': {'signal_id': 'business_task_signals'},
        'business_legacy_links': {'task_id': 'business_tasks', 'signal_id': 'business_task_signals', 'anchor_id': 'business_anchors', 'project_id': 'business_projects'},
    }
    assert set(source['external_endpoint_tables']) == set(expected)
    assert sum(len(t['columns']) for t in source['external_endpoint_tables'].values()) == 118
    incoming = [r for r in source['incoming_foreign_keys'] if r['child_table'] not in NAMES]
    assert len(incoming) == len({(r['child_table'], r['id']) for r in incoming}) == 23
    assert all(r['seq'] == 0 and r['to'] == 'id' and r['on_delete'] == r['on_update'] == 'NO ACTION' for r in incoming)
    assert {n: {r['from']: r['table'] for r in incoming if r['child_table'] == n} for n in expected} == expected
    assert source['external_schema_references'] == []
    # No child DML or migration: reconstruct just each literal endpoint table.
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.execute('pragma foreign_keys=on')
        for name, table in source['external_endpoint_tables'].items():
            connection.execute(table['sql'])
            assert pragma(connection, 'table_xinfo', name) == table['columns']
            assert pragma(connection, 'foreign_key_list', name) == table['foreign_keys']


def test_actual_fresh_differences_and_defaults(db, variant):
    source = CAPTURE[variant]['tables']
    differing_columns = {n for n in NAMES if CAPTURE['actual']['tables'][n]['columns'] != CAPTURE['fresh']['tables'][n]['columns']}
    assert differing_columns == {'business_tasks', 'business_projects'}
    assert {n for n in NAMES if CAPTURE['actual']['tables'][n]['sql'] != CAPTURE['fresh']['tables'][n]['sql']} == {'business_tasks', 'business_projects', 'business_task_signals'}
    assert next(c['cid'] for c in source['business_tasks']['columns'] if c['name'] == 'origin') == (17 if variant == 'actual' else 4)
    assert next(c['cid'] for c in source['business_projects']['columns'] if c['name'] == 'created_at') == (5 if variant == 'actual' else 11)
    for name in NAMES:
        assert {c['name']: {k: v for k, v in c.items() if k != 'cid'} for c in CAPTURE['actual']['tables'][name]['columns']} == {c['name']: {k: v for k, v in c.items() if k != 'cid'} for c in CAPTURE['fresh']['tables'][name]['columns']}
    before = db.execute('select current_timestamp').fetchone()[0]
    seed(db)
    after = db.execute('select current_timestamp').fetchone()[0]
    assert db.execute('select description,status,commitment_status,formal_basis,business_relevance,origin,suggestion_json from business_tasks').fetchone() == ('', 'open', 'none', None, 'unknown', 'source', '{}')
    times = db.execute('select created_at,updated_at,last_activity_at from business_tasks').fetchone()
    assert times[0] == times[1] == times[2] and before <= times[0] <= after
    assert db.execute('select crm_customer_lookup_status,crm_customer_candidates_json,crm_customer_evidence_json from business_projects').fetchone() == ('not_requested', '[]', '{}')
    # The source revision table does not impose JSON validity; service validation is separate.
    insert(db, 'business_project_context_revisions', project_id=-15, context_json='not json', evidence_json='not json')
    assert db.execute('select context_json,evidence_json from business_project_context_revisions').fetchone() == ('not json', 'not json')


def test_typed_anchor_composite_fk_and_binary_uniqueness(db):
    seed(db)
    other = insert(db, 'business_anchors', anchor_type='customer', anchor_ref='seed', title='other').lastrowid
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        insert(db, 'business_projects', canonical_anchor_id=other, title='bad', registry_source='synthetic')
    with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
        insert(db, 'business_projects', canonical_anchor_id=other, anchor_type='customer', title='bad', registry_source='synthetic')
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        insert(db, 'business_projects', **values('business_projects'))
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        insert(db, 'business_anchors', **values('business_anchors', 'seed'))
    insert(db, 'business_anchors', **values('business_anchors', 'SEED'))
    # The composite parent key includes anchor_type, so changing only its type fails immediately.
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.execute("update business_anchors set anchor_type='matter' where id=-14")
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.execute('delete from business_anchors where id=-14')


def test_task_self_merge_and_state_checks(db):
    seed(db)
    second = insert(db, 'business_tasks', **values('business_tasks')).lastrowid
    for assignments in ("status='merged',merged_into_task_id=id", "status='merged',merged_into_task_id=null", "status='open',merged_into_task_id=-11", "stage='formal'", "owner_evidence_json='[]'", "missing_evidence_json='{}'", "suggestion_json='[]'", "title=char(160)"):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            db.execute(f'update business_tasks set {assignments} where id=?', (second,))
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.execute("update business_tasks set status='merged',merged_into_task_id=999 where id=?", (second,))
    db.execute("update business_tasks set status='merged',merged_into_task_id=-11 where id=?", (second,))
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.execute('delete from business_tasks where id=-11')
    assert db.execute('select status,merged_into_task_id from business_tasks where id=?', (second,)).fetchone() == ('merged', -11)


def test_shared_signal_distinct_evidence_roles_and_immediate_fk(db):
    seed(db)
    second = insert(db, 'business_tasks', **values('business_tasks')).lastrowid
    for task in (-11, second):
        for role in ('discovery', 'assignment'):
            insert(db, 'business_task_evidence', task_id=task, signal_id=-13, evidence_role=role)
    insert(db, 'business_project_evidence', **values('business_project_evidence'))
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        insert(db, 'business_task_evidence', **values('business_task_evidence'))
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        insert(db, 'business_project_evidence', **values('business_project_evidence'))
    with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
        insert(db, 'business_task_evidence', task_id=-11, signal_id=-13, evidence_role='invented')
    for name, fields in (('business_task_evidence', dict(task_id=999, signal_id=-13, evidence_role='discovery')), ('business_project_evidence', dict(project_id=-15, signal_id=999)), ('business_task_events', dict(task_id=-11, signal_id=999, event_type='created', before_json='{}', after_json='{}', reason='synthetic'))):
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            insert(db, name, **fields)
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.execute('update business_task_evidence set task_id=999 where task_id=-11')
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        db.execute('delete from business_projects where id=-15')
    assert db.execute('select count(*) from business_task_evidence').fetchone()[0] == 4


def test_signal_document_date_immutability_and_guards(db):
    seed(db)
    date = insert(db, 'business_task_date_evidence', **values('business_task_date_evidence')).lastrowid
    for name, pk in (('business_source_documents', -12), ('business_task_signals', -13), ('business_task_date_evidence', date)):
        for sql in (f'update {name} set id=id where id=?', f'delete from {name} where id=?'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(sql, (pk,))
        fields = dict(id=pk, **values(name, 'seed'))
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute(f"insert or replace into {name}({','.join(fields)}) values({','.join('?' for _ in fields)})", tuple(fields.values()))
    # Duplicate natural identities are blocked before IGNORE/REPLACE can hide them.
    for name in ('business_source_documents', 'business_task_signals'):
        fields = values(name, 'seed')
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute(f"insert or ignore into {name}({','.join(fields)}) values({','.join('?' for _ in fields)})", tuple(fields.values()))
    insert(db, 'business_source_documents', **values('business_source_documents', 'SEED'))
    insert(db, 'business_task_signals', **values('business_task_signals', 'SEED'))
    for override in (dict(date_type='invented'), dict(actor_kind='invented'), dict(raw_phrase='\u3000')):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            insert(db, 'business_task_date_evidence', **(values('business_task_date_evidence') | override))
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        insert(db, 'business_task_date_evidence', **(values('business_task_date_evidence') | dict(source_signal_id=999)))
    assert db.execute('select value_at,actor_user_id,actor_name from business_task_date_evidence').fetchone() == ('', '', '')


@pytest.mark.parametrize('name', AUTO)
def test_native_independent_allocators_zero_negative_and_deleted_max(db, name):
    assert all(sequence(db, table) is None for table in AUTO)
    seed(db)
    for explicit_id, suffix in ((-99, 'negative'), (0, 'zero')):
        fields = values(name, suffix)
        if name == 'business_projects':
            fields['canonical_anchor_id'] = insert(db, 'business_anchors', id=explicit_id - 1000, **values('business_anchors', suffix)).lastrowid
        assert insert(db, name, id=explicit_id, **fields).lastrowid == explicit_id
        assert sequence(db, name) == 0
    fields = values(name, 'auto')
    if name == 'business_projects':
        fields['canonical_anchor_id'] = insert(db, 'business_anchors', id=-2000, **values('business_anchors', 'auto')).lastrowid
    assert insert(db, name, **fields).lastrowid == 1
    high = values(name, 'high')
    if name == 'business_projects':
        high['canonical_anchor_id'] = insert(db, 'business_anchors', id=-3000, **values('business_anchors', 'high')).lastrowid
    insert(db, name, id=1000, **high)
    if name in IMMUTABLE:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute(f'delete from {name} where id=1000')
    else:
        db.execute(f'delete from {name} where id=1000')
    high |= {k: 'after' for k in ('identity_key', 'dedupe_key', 'anchor_ref') if k in high}
    assert insert(db, name, **high).lastrowid == 1001
    assert all(sequence(db, other) in (None, 0) for other in AUTO if other != name)


def test_transaction_failure_rolls_back_facts_and_allocation(db):
    seed(db)
    before_counts = {n: db.execute(f'select count(*) from {n}').fetchone()[0] for n in NAMES}
    before_seq = {n: sequence(db, n) for n in AUTO}
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'), db:
        task = insert(db, 'business_tasks', **values('business_tasks')).lastrowid
        doc = insert(db, 'business_source_documents', **values('business_source_documents')).lastrowid
        signal = insert(db, 'business_task_signals', **(values('business_task_signals') | dict(source_document_id=doc))).lastrowid
        insert(db, 'business_task_evidence', task_id=task, signal_id=signal, evidence_role='discovery')
        insert(db, 'business_task_date_evidence', **(values('business_task_date_evidence') | dict(task_id=task, source_signal_id=signal)))
        anchor = insert(db, 'business_anchors', **values('business_anchors')).lastrowid
        project = insert(db, 'business_projects', **(values('business_projects') | dict(canonical_anchor_id=anchor))).lastrowid
        insert(db, 'business_project_evidence', project_id=project, signal_id=signal)
        insert(db, 'business_project_context_revisions', project_id=project, context_json='{}', evidence_json='[]')
        insert(db, 'business_task_events', **(values('business_task_events') | dict(task_id=task, signal_id=signal)))
        insert(db, 'business_task_events', **(values('business_task_events') | dict(task_id=999)))
    assert {n: db.execute(f'select count(*) from {n}').fetchone()[0] for n in NAMES} == before_counts
    assert {n: sequence(db, n) for n in AUTO} == before_seq
    assert db.execute('pragma foreign_key_check').fetchall() == []


def test_native_identity_and_event_validation(db):
    seed(db)
    insert(db, 'business_tasks', id='1', **values('business_tasks'))
    assert db.execute('select typeof(id) from business_tasks where id=1').fetchone() == ('integer',)
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        insert(db, 'business_tasks', id=1, **values('business_tasks'))
    with pytest.raises(sqlite3.IntegrityError, match='NOT NULL'):
        insert(db, 'business_tasks', title=None, stage='candidate')
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        insert(db, 'business_task_signals', **(values('business_task_signals') | dict(source_document_id=999)))
    for override in (dict(event_type='invented'), dict(before_json='[]'), dict(after_json='[]'), dict(reason='\u3000')):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            insert(db, 'business_task_events', **(values('business_task_events') | override))
    assert db.execute('select count(*) from business_task_events').fetchone() == (0,)
