"""Retired feature closure, transaction ownership and exact empty inverse."""
from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from app import confirmation_schema_release as release
from app.task_storage import create_task_schema, insert_task
from app.task20_definitions import create_task20_schema, initialize_task8_sequence
from app.task8_storage import register_task8_functions, task8_insert_parts
from app.workbench_storage import create_workbench_auxiliary_schema


@pytest.fixture
def db():
    with closing(sqlite3.connect(':memory:')) as connection:
        create_task_schema(connection, include_workbench=True)
        create_workbench_auxiliary_schema(connection)
        connection.execute('create table runs(id integer primary key, legacy_key text, task_id integer, sequence integer, event_type text, payload_json text, created_at text, record_type text)')
        register_task8_functions(connection)
        connection.commit()
        connection.execute('begin immediate')
        yield connection


def current(db):
    db.execute('drop table tasks')
    create_task20_schema(db)
    initialize_task8_sequence(db, 0)


def confirmation(db):
    db.execute("insert into workbench_confirmations(id,turn_id,action_kind,target,summary,risk,arguments_json,status) values('old',99,'kind','target','summary','risk','{}','pending')")


def test_native_declaration_roundtrip_and_caller_commit_ownership(db):
    metadata = release.capture_confirmation_source(db)
    assert set(metadata) == {'declaration'}
    assert release._fingerprint(metadata['declaration']) == release.SOURCE_HASH
    before = deepcopy(metadata)
    current(db)
    release.retire_confirmation_schema(db, metadata)
    assert db.in_transaction
    assert db.execute("select 1 from sqlite_schema where tbl_name='workbench_confirmations'").fetchone() is None
    parts = task8_insert_parts('workbench_tasks', dict(legacy_key='task', title='ordinary', runtime_kind='codex'))
    db.execute('insert into tasks (' + parts.columns + ') values (' + parts.values + ')', parts.parameters)
    release.restore_confirmation_schema(db, metadata)
    assert db.in_transaction
    assert release._declaration(db, release.TABLE) == before['declaration']
    assert db.execute('select count(*) from workbench_confirmations').fetchone()[0] == 0
    assert db.execute("select count(*) from tasks where record_type='workbench_tasks'").fetchone()[0] == 1
    assert metadata == before


@pytest.mark.parametrize('kind', ('row', 'waiting', 'event'))
def test_source_closure_is_checked_before_target_ddl(db, kind):
    if kind == 'row':
        confirmation(db)
    elif kind == 'waiting':
        insert_task(db, 'workbench_turns', dict(legacy_key='turn', parent_id=99, client_request_id='request', task_sequence=1, user_text='text', status='waiting_confirmation'))
    else:
        db.execute("insert into runs(record_type,event_type) values('workbench_events','confirmation_required')")
    with pytest.raises(ValueError, match='nonempty'):
        release.capture_confirmation_source(db)
    assert db.in_transaction
    assert db.execute("select 1 from sqlite_schema where name='workbench_confirmations'").fetchone()


def test_retirement_rechecks_late_feature_write(db):
    metadata = release.capture_confirmation_source(db)
    current(db)
    confirmation(db)
    with pytest.raises(ValueError, match='nonempty'):
        release.retire_confirmation_schema(db, metadata)
    assert db.execute('select id from workbench_confirmations').fetchall() == [('old',)]


def test_ordinary_native_events_are_preserved(db):
    metadata = release.capture_confirmation_source(db)
    db.execute("insert into runs(record_type,event_type,payload_json) values('workbench_events','tool_result','{\"native_receipt\":true}')")
    current(db)
    release.retire_confirmation_schema(db, metadata)
    release.restore_confirmation_schema(db, metadata)
    assert db.execute('select event_type,payload_json from runs').fetchall() == [('tool_result', '{"native_receipt":true}')]


def test_caller_rollback_restores_original_schema(db):
    db.commit()
    db.execute('begin immediate')
    metadata = release.capture_confirmation_source(db)
    current(db)
    release.retire_confirmation_schema(db, metadata)
    db.rollback()
    assert release._declaration(db, release.TABLE) == metadata['declaration']
    assert not db.in_transaction


@pytest.mark.parametrize('name', ('tasks','runs','workbench_confirmations'))
def test_temp_shadow_is_rejected_before_any_mutation(db, name):
    db.execute('create temp table "' + name + '"(id)')
    with pytest.raises(ValueError, match='TEMP'):
        release.capture_confirmation_source(db)
    assert db.execute('select count(*) from main.workbench_confirmations').fetchone()[0] == 0


@pytest.mark.parametrize('operation', ('capture','retire','restore'))
def test_transaction_required(db, operation):
    metadata = release.capture_confirmation_source(db)
    db.commit()
    function = getattr(release, {'capture':'capture_confirmation_source','retire':'retire_confirmation_schema','restore':'restore_confirmation_schema'}[operation])
    with pytest.raises(sqlite3.ProgrammingError, match='transaction'):
        function(db, *(() if operation == 'capture' else (metadata,)))


def test_changed_declaration_and_tampered_inverse_rejected(db):
    metadata = release.capture_confirmation_source(db)
    tampered = deepcopy(metadata)
    tampered['declaration']['sql'] += ' '
    with pytest.raises(ValueError, match='metadata'):
        release.restore_confirmation_schema(db, tampered)
    db.execute('create index extra_confirmation_index on workbench_confirmations(summary)')
    with pytest.raises(ValueError, match='declaration'):
        release.retire_confirmation_schema(db, metadata)
    assert db.execute("select 1 from sqlite_schema where name='extra_confirmation_index'").fetchone()
