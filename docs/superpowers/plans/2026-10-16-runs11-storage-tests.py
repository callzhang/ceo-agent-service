"""Native source differential for the three independent Runs source writers."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3

import pytest

from app.runs11_definitions import (
    RUN11_NEW_KINDS, RUN11_FIELDS, RUN11_SOURCE_PROFILES,
    create_runs11_schema, initialize_run11_sequences, run11_projection,
)
from app.runs11_storage import insert_run11_fact

CLOCK = '2001-02-03 04:05:06'
MAX = 2**63 - 1
ORACLE_PATH = Path(__file__).parent / 'fixtures/runs11-native-source-oracle.json'
assert hashlib.sha256(ORACLE_PATH.read_bytes()).hexdigest() == 'ed0c98618591f6d81a0bc9303ab29b94178a1dd5f850498a1c940d52afda6c89'
NATIVE_PROFILES = json.loads(ORACLE_PATH.read_text())['profiles']


@pytest.mark.parametrize('profile', ('actual', 'fresh'))
def test_candidate_profiles_match_independent_actual_and_native_constructor_declarations(profile):
    for name in RUN11_NEW_KINDS:
        assert sorted(RUN11_SOURCE_PROFILES[profile][name]) == sorted(
            tuple(row) for row in NATIVE_PROFILES[profile][name])


@pytest.fixture(params=('actual','fresh'))
def pair(request):
    with closing(sqlite3.connect(':memory:')) as native, closing(sqlite3.connect(':memory:')) as target:
        for db in (native,target):
            db.create_function('current_timestamp',0,lambda:CLOCK)
            assert db.execute('select current_timestamp').fetchone()[0] == CLOCK
            db.execute('create table marker(value)')
        for definitions in NATIVE_PROFILES[request.param].values():
            for category in ('table','index'):
                for kind,_name,sql in definitions:
                    if kind == category:
                        native.execute(sql)
        create_runs11_schema(target)
        target.execute('create table tasks(id integer primary key,record_type text)')
        target.execute('begin')
        initialize_run11_sequences(target,{'runs':None,**dict.fromkeys(RUN11_NEW_KINDS)})
        native.commit()
        target.commit()
        yield native,target,request.param


def values(kind):
    if kind == 'codex_session_search_index':
        return dict(session_id='session',source_type='meeting_alignment',source_id='45',title='title')
    if kind == 'email_classifier_runtime_samples':
        return dict(model_id='model',outcome='success',cache_hit=1,runtime_warm=0,
                    queue_ms=1,http_ms=2.0,embedding_ms=3,head_ms=4,total_ms=10,recorded_at=CLOCK)
    return dict(step_id='step',action_id='action',status='arbitrary original status',
                evidence_json='this is literal non-JSON evidence',stdout_excerpt='literal output')


def native_insert(db,kind,payload,expressions=None,parameters=()):
    expressions = {} if expressions is None else expressions
    names=(*payload,*expressions)
    return db.execute('insert into '+kind+' ('+','.join(names)+') values ('
                      +','.join(['?']*len(payload)+list(expressions.values()))+')',
                      (*payload.values(),*parameters))


def snapshot(db,kind,*,current):
    fields=RUN11_FIELDS[kind]
    columns=','.join('"'+name+'"' for name in fields)
    types=','.join('typeof("'+name+'")' for name in fields)
    source='('+run11_projection(kind)+')' if current else 'main.'+kind
    rows=db.execute('select '+columns+','+types+' from '+source+' order by id').fetchall()
    name='runs:'+kind if current else kind
    counter=db.execute('select seq from main.sqlite_sequence where name=?',(name,)).fetchall()
    return rows,counter


@pytest.mark.parametrize('kind',RUN11_NEW_KINDS)
@pytest.mark.parametrize('key',('omit',None,0,-5,2.0,'2.0',float('nan')))
def test_native_id_defaults_affinity_and_current_counter(pair,kind,key):
    native,target,profile=pair
    payload=values(kind)
    if key != 'omit':
        payload['id']=key
    expected=native_insert(native,kind,payload)
    actual=insert_run11_fact(target,kind,payload,source_variant=profile)
    assert (actual.lastrowid,actual.rowcount)==(expected.lastrowid,expected.rowcount)
    assert actual.physical_id>0
    assert snapshot(target,kind,current=True)==snapshot(native,kind,current=False)
    assert native.in_transaction and target.in_transaction
    assert target.execute("select seq from sqlite_sequence where name='runs:published'").fetchall()==[]


@pytest.mark.parametrize('kind',RUN11_NEW_KINDS)
def test_multiple_source_writes_delete_high_and_rollback(pair,kind):
    native,target,profile=pair
    for source_id in (10,20):
        payload=values(kind)|{'id':source_id}
        if kind=='codex_session_search_index':
            payload['session_id']=str(source_id)
        native_insert(native,kind,payload)
        insert_run11_fact(target,kind,payload,source_variant=profile)
    native.commit()
    target.commit()
    native.execute('delete from '+kind+' where id=20')
    target.execute("delete from runs where record_type=? and legacy_key='20'",(kind,))
    native.commit()
    target.commit()
    for db in (native,target):
        db.execute('savepoint witness')
    payload=values(kind)|({'session_id':'after-delete'} if kind=='codex_session_search_index' else {})
    assert native_insert(native,kind,payload).lastrowid==21
    assert insert_run11_fact(target,kind,payload,source_variant=profile).lastrowid==21
    assert snapshot(target,kind,current=True)==snapshot(native,kind,current=False)
    for db in (native,target):
        db.execute('rollback to witness')
        db.execute('release witness')
    assert snapshot(target,kind,current=True)==snapshot(native,kind,current=False)
    assert snapshot(target,kind,current=True)[1]==[(20,)]


@pytest.mark.parametrize('kind',RUN11_NEW_KINDS)
def test_independent_source_auto_full_rolls_back_outer_transaction(pair,kind):
    native,target,profile=pair
    native.execute('insert into sqlite_sequence(name,seq) values(?,?)',(kind,MAX))
    target.execute('insert into sqlite_sequence(name,seq) values(?,?)',('runs:'+kind,MAX))
    native.commit()
    target.commit()
    for db in (native,target):
        db.execute("insert into marker values('pending')")
    outcomes=[]
    for db,current in ((native,False),(target,True)):
        with pytest.raises(sqlite3.OperationalError) as failure:
            (insert_run11_fact(db,kind,values(kind),source_variant=profile) if current
             else native_insert(db,kind,values(kind)))
        outcomes.append((failure.value.sqlite_errorcode,db.in_transaction,db.execute('select * from marker').fetchall()))
    assert outcomes==[(sqlite3.SQLITE_FULL,False,[])]*2
    assert snapshot(target,kind,current=True)==snapshot(native,kind,current=False)


@pytest.mark.parametrize('kind',RUN11_NEW_KINDS)
def test_unrelated_global_run_full_does_not_spend_native_source_identity(pair,kind):
    native,target,profile=pair
    target.execute("insert into sqlite_sequence(name,seq) values('runs',?)",(MAX,))
    target.execute("insert into sqlite_sequence(name,seq) values('runs:published',?)",(MAX,))
    expected=native_insert(native,kind,values(kind))
    actual=insert_run11_fact(target,kind,values(kind),source_variant=profile)
    assert actual.lastrowid==expected.lastrowid==1
    assert actual.physical_id>0
    assert snapshot(target,kind,current=True)==snapshot(native,kind,current=False)
    assert target.execute("select seq from sqlite_sequence where name='runs:published'").fetchone()==(MAX,)


@pytest.mark.parametrize('kind',RUN11_NEW_KINDS)
@pytest.mark.parametrize('bad',(2**65,object()))
def test_native_binding_errors_precede_source_full(pair,kind,bad):
    native,target,profile=pair
    native.execute('insert into sqlite_sequence(name,seq) values(?,?)',(kind,MAX))
    target.execute('insert into sqlite_sequence(name,seq) values(?,?)',('runs:'+kind,MAX))
    native.commit()
    target.commit()
    payload=values(kind)
    payload[next(name for name in payload if name!='id')]=bad
    outcomes=[]
    for db,current in ((native,False),(target,True)):
        try:
            (insert_run11_fact(db,kind,payload,source_variant=profile) if current
             else native_insert(db,kind,payload))
        except (OverflowError,sqlite3.ProgrammingError) as error:
            outcomes.append((type(error),db.in_transaction))
    assert outcomes==[(OverflowError if isinstance(bad,int) else sqlite3.ProgrammingError,True)]*2
    assert snapshot(target,kind,current=True)==snapshot(native,kind,current=False)


@pytest.mark.parametrize('status',('running','completed','anything'))
def test_setup_finished_case_and_default_profile_are_exact(pair,status):
    native,target,profile=pair
    payload=values('setup_wizard_events')|{'status':status}
    expressions={'finished_at':"case when ?='running' then '' else current_timestamp end"}
    native_insert(native,'setup_wizard_events',payload,expressions,(status,))
    insert_run11_fact(target,'setup_wizard_events',payload,source_variant=profile,
                      insert_sql=expressions,insert_parameters=(status,))
    assert snapshot(target,'setup_wizard_events',current=True)==snapshot(native,'setup_wizard_events',current=False)
    row=target.execute(run11_projection('setup_wizard_events')).fetchone()
    assert row[-2:]==(CLOCK,'' if status=='running' else CLOCK)


@pytest.mark.parametrize('kind,field',(('codex_session_search_index','session_id'),
                                     ('email_classifier_runtime_samples','recorded_at'),
                                     ('setup_wizard_events','step_id')))
def test_missing_source_required_value_cannot_inherit_container_default(pair,kind,field):
    native,target,profile=pair
    payload=values(kind)
    payload.pop(field)
    for db,current in ((native,False),(target,True)):
        with pytest.raises(sqlite3.IntegrityError) as error:
            (insert_run11_fact(db,kind,payload,source_variant=profile) if current
             else native_insert(db,kind,payload))
        assert error.value.sqlite_errorcode & 255 == sqlite3.SQLITE_CONSTRAINT
        assert db.in_transaction
    assert snapshot(target,kind,current=True)==snapshot(native,kind,current=False)
