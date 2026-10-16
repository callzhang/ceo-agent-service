"""Current-fact Runs11 roundtrip under the caller's FK-off transaction."""
from contextlib import closing
import json
import sqlite3

import pytest

from app.configs_run_schema import _declarations, _objects, map_runs_to_configs
from app.run_storage import insert_run
from app.runs11_definitions import RUN11_NEW_KINDS, RUN11_FIELDS, RUN11_BASE_COLUMNS, RUN11_SOURCE_PROFILES, run11_projection
from app.runs11_storage import insert_run11_fact
from app.runs11_schema_release import (
    SOURCES, STAGES, STREAMS, FTS, capture_run11_source, migrate_run11_schema,
    restore_run11_schema, run11_schema_is_current, _stage, _compare,
)
from app.task20_schema_release import _sequence, _set_sequence
from app.workbench_storage import SEARCH_FTS_DDL


def old_insert(db, **extra):
    values = dict(workload_kind='other', workload_key='work', attempt_number=1,
                  route_name='route', runtime_kind='runtime', credential_mode='cred',
                  model='model', status='completed') | extra
    return insert_run(db, 'agent_runtime_attempts', values)


def values(name, number=1):
    if name == 'codex_session_search_index':
        return dict(session_id='session-'+str(number), source_type='meeting_alignment',
                    source_id='45', title='uniquetitle'+str(number))
    if name == 'email_classifier_runtime_samples':
        return dict(model_id='model', outcome='success', cache_hit=1, runtime_warm=0,
                    queue_ms=1, http_ms=2, embedding_ms=3, head_ms=4, total_ms=10, recorded_at='time')
    return dict(step_id='step', action_id='action', status='custom status',
                evidence_json='not valid JSON', stdout_excerpt='literal stdout')


def native_insert(db, name, fields):
    return db.execute('insert into '+name+'('+','.join(fields)+') values ('
                      +','.join('?' for _ in fields)+')', tuple(fields.values()))


@pytest.fixture(params=('actual', 'fresh'))
def source(request):
    with closing(sqlite3.connect(':memory:')) as db:
        for ddl in (
            'create table tasks(id integer primary key, record_type text)',
            'create table schedules(id integer primary key)',
            'create table configs(schedule_snapshot_public_id integer unique)',
            'create table scheduled_tasks(id integer primary key)',
            'create table scheduled_task_config_versions(id integer primary key)',
        ):
            db.execute(ddl)
        for sql in _declarations(mapped=False).values():
            db.execute(sql)
        for objects in RUN11_SOURCE_PROFILES[request.param].values():
            for category in ('table', 'index'):
                for kind, _name, sql in objects:
                    if kind == category:
                        db.execute(sql)
        db.execute(SEARCH_FTS_DDL)
        old_insert(db)
        for name in RUN11_NEW_KINDS:
            native_insert(db, name, values(name))
        db.commit()
        db.execute('begin')
        yield db, request.param


def facts(db, name, *, current=False):
    fields = RUN11_BASE_COLUMNS if name == 'runs' else RUN11_FIELDS[name]
    query = 'select * from '+name
    if current:
        query = ("select * from runs where record_type not in ("
                 + ','.join(map(repr, RUN11_NEW_KINDS))+')') if name == 'runs' else run11_projection(name)
    columns = ','.join('"'+field+'"' for field in fields)
    types = ','.join('typeof("'+field+'")' for field in fields)
    return [tuple(row) for row in db.execute('select '+columns+','+types+' from ('+query+') order by id')]


def declarations(db):
    return [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_schema where sql is not null order by type,name")]


@pytest.mark.parametrize('water', (None, 0, 100))
@pytest.mark.parametrize('row_factory', (None, sqlite3.Row))
def test_roundtrip_all_facts_types_declarations_and_current_counter_presence(source, water, row_factory):
    db, variant = source
    db.row_factory = row_factory
    for name in SOURCES:
        _set_sequence(db, name, water)
    metadata = capture_run11_source(db)
    assert metadata == {'variant': variant}
    assert json.loads(json.dumps(metadata)) == metadata
    map_runs_to_configs(db)
    original = declarations(db)
    before = {name: facts(db, name) for name in SOURCES}
    assert migrate_run11_schema(db, metadata) == dict.fromkeys(SOURCES, 1)
    assert run11_schema_is_current(db)
    assert {name: facts(db, name, current=True) for name in SOURCES} == before
    assert _sequence(db, 'runs:published') == water
    assert all(_sequence(db, 'runs:'+name) == water for name in RUN11_NEW_KINDS)
    restore_run11_schema(db, metadata)
    assert {name: facts(db, name) for name in SOURCES} == before
    assert declarations(db) == original
    assert all(_sequence(db, name) == water for name in SOURCES)
    assert all(_sequence(db, name) is None for name in STREAMS)
    assert not db.execute('select name from temp.sqlite_schema').fetchall()
    assert [tuple(row) for row in db.execute('select rowid from '+FTS+' where '+FTS+" match 'uniquetitle1'")] == [(1,)]


def test_prepared_metadata_does_not_freeze_source_or_target_new_facts(source):
    db, _variant = source
    metadata = capture_run11_source(db)
    map_runs_to_configs(db)
    for name in RUN11_NEW_KINDS:
        native_insert(db, name, values(name, 2) | {'id': 100})
        db.execute('delete from '+name+' where id=100')
    migrate_run11_schema(db, metadata)
    for name in RUN11_NEW_KINDS:
        assert insert_run11_fact(db, name, values(name, 3)).lastrowid == 101
    old_id = old_insert(db, workload_key='new-native').lastrowid
    expected = {name: facts(db, name, current=True) for name in SOURCES}
    restore_run11_schema(db, metadata)
    assert {name: facts(db, name) for name in SOURCES} == expected
    assert _sequence(db, 'runs') == old_id
    for name in RUN11_NEW_KINDS:
        assert native_insert(db, name, values(name, 4)).lastrowid == 102
    assert [tuple(row) for row in db.execute('select rowid from '+FTS+' where '+FTS+" match 'uniquetitle3'")] == [(101,)]
    assert not db.execute('select rowid from '+FTS+' where '+FTS+" match 'absenttoken'").fetchall()
    migrate_run11_schema(db, metadata)
    assert run11_schema_is_current(db)
    assert all(len(facts(db, name, current=True)) == 3 for name in RUN11_NEW_KINDS)


def test_source_multiplicity_and_missing_native_pointer_are_preserved(source):
    db, _variant = source
    native_insert(db, RUN11_NEW_KINDS[0], values(RUN11_NEW_KINDS[0], 2) | {'source_id': '45'})
    native_insert(db, RUN11_NEW_KINDS[0], values(RUN11_NEW_KINDS[0], 3) | {'source_id': 'not-a-number'})
    metadata = capture_run11_source(db)
    map_runs_to_configs(db)
    expected = facts(db, RUN11_NEW_KINDS[0])
    migrate_run11_schema(db, metadata)
    assert facts(db, RUN11_NEW_KINDS[0], current=True) == expected
    restore_run11_schema(db, metadata)
    assert facts(db, RUN11_NEW_KINDS[0]) == expected


def test_native_metadata_input_tie_order_has_an_explicit_current_projection(source):
    db, _variant = source
    name = 'codex_session_search_index'
    native_insert(db, name, values(name, 2) | {'source_id': '0'})
    db.execute("update codex_session_search_index set updated_at='same timestamp'")
    expected = [tuple(row) for row in db.execute(
        'select * from codex_session_search_index order by updated_at desc')]
    assert [row[0] for row in expected] == [1, 2]
    metadata = capture_run11_source(db)
    map_runs_to_configs(db)
    migrate_run11_schema(db, metadata)
    actual = [tuple(row) for row in db.execute(
        'select * from ('+run11_projection(name)+') order by updated_at desc,id asc')]
    assert actual == expected


@pytest.mark.parametrize('collision', (*SOURCES, FTS, *STAGES.values()))
def test_temp_collision_rejected_before_source_change(source, collision):
    db, _variant = source
    db.execute('create temp table "'+collision+'"(value)')
    before = declarations(db)
    with pytest.raises(ValueError, match='TEMP'):
        capture_run11_source(db)
    assert declarations(db) == before


def test_caller_transaction_rolls_back_conversion(source):
    db, _variant = source
    original = declarations(db)
    metadata = capture_run11_source(db)
    map_runs_to_configs(db)
    migrate_run11_schema(db, metadata)
    db.rollback()
    assert declarations(db) == original
    assert _objects(db) == _declarations(mapped=False)
    assert all(len(facts(db, name)) == 1 for name in SOURCES)


@pytest.mark.parametrize('alteration', ('index', 'trigger', 'view', 'foreign-key', 'fts', 'fts-view', 'fts-shadow-view', 'fts-shadow-index'))
def test_unsupported_source_dependency_or_declaration_rejected_readonly(source, alteration):
    db, _variant = source
    statements = {
        'index': 'create index extra_source_title on codex_session_search_index(title)',
        'trigger': "create trigger unrelated after insert on tasks begin delete from setup_wizard_events; end",
        'view': 'create view unexpected as select * from email_classifier_runtime_samples',
        'foreign-key': 'create table unexpected(id references setup_wizard_events(id))',
        'fts': 'drop table codex_session_search_fts',
        'fts-view': 'create view unexpected as select rowid,title from codex_session_search_fts',
        'fts-shadow-view': 'create view unexpected as select * from codex_session_search_fts_docsize',
        'fts-shadow-index': 'create index unexpected on codex_session_search_fts_docsize(id)',
    }
    db.execute(statements[alteration])
    before = declarations(db)
    with pytest.raises(ValueError):
        capture_run11_source(db)
    assert declarations(db) == before


@pytest.mark.parametrize('kind', ('view', 'trigger'))
@pytest.mark.parametrize('column', ('model_id', 'record_type'))
def test_inverse_prepares_external_consumers_against_original_columns(source, kind, column):
    db, _variant = source
    metadata = capture_run11_source(db)
    map_runs_to_configs(db)
    migrate_run11_schema(db, metadata)
    sql = ('create view current_observer as select '+column+' from runs' if kind == 'view' else
           'create trigger current_observer after insert on tasks begin select '+column+' from runs; end')
    db.execute(sql)
    before = declarations(db)
    if column == 'model_id':
        with pytest.raises(ValueError, match='inverse SQL dependency'):
            restore_run11_schema(db, metadata)
        assert declarations(db) == before
        assert run11_schema_is_current(db)
    else:
        restore_run11_schema(db, metadata)
        if kind == 'view':
            assert db.execute('select * from current_observer').fetchall()
        else:
            db.execute("insert into tasks values(1,'reply_tasks')")


def test_fk_on_and_missing_transaction_rejected(source):
    db, _variant = source
    db.commit()
    with pytest.raises(sqlite3.ProgrammingError):
        capture_run11_source(db)
    db.execute('pragma foreign_keys=on')
    db.execute('begin')
    with pytest.raises(sqlite3.ProgrammingError):
        capture_run11_source(db)


@pytest.mark.parametrize('change', ('identity', 'value', 'type'))
def test_indexed_fact_comparison_rejects_same_count_differences(source, change):
    db, _variant = source
    name = 'codex_session_search_index'
    _stage(db, name, 'select * from '+name)
    expressions = dict.fromkeys(RUN11_FIELDS[name])
    expressions.update({field: '"'+field+'"' for field in expressions})
    field, expression = {'identity': ('id', 'id+1'),
                         'value': ('title', "'different title'"),
                         'type': ('id', 'cast(id as real)')}[change]
    expressions[field] = expression
    query = 'select '+','.join(value+' as "'+key+'"' for key, value in expressions.items())+' from '+name
    with pytest.raises(ValueError, match='identity, value or storage class'):
        _compare(db, name, query)


def test_original_runs_fact_comparison_uses_primary_key_without_sort(source):
    db, _variant = source
    _stage(db, 'runs', 'select * from runs')
    statements = []
    db.set_trace_callback(statements.append)
    _compare(db, 'runs', 'select * from runs')
    db.set_trace_callback(None)
    comparison = next(sql for sql in statements if 'left join' in sql)
    plan = [row[3] for row in db.execute('explain query plan '+comparison)]
    assert any('INTEGER PRIMARY KEY' in operation for operation in plan)
    assert not any('TEMP B-TREE' in operation for operation in plan)
