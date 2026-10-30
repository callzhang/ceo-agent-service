"""Focused unselected Runs11 declarations and native old-eight allocator proof."""
import sqlite3
from contextlib import closing

import pytest

from app import runs11_definitions as target
from app.configs_run_schema import _declarations
from app.run_storage import insert_run

META, SAMPLE, SETUP = target.RUN11_NEW_KINDS


def connection(candidate=True):
    db = sqlite3.connect(':memory:')
    if candidate:
        target.create_runs11_schema(db)
    else:
        for sql in _declarations(mapped=True).values():
            db.execute(sql)
    db.execute('create table tasks(id integer primary key,record_type text)')
    return db


def new_values(kind):
    if kind == META:
        return dict(session_id='session', source_type='', source_id='', title='',
                    created_at='created', updated_at='updated')
    if kind == SAMPLE:
        return dict(model_id='model', outcome='success', fallback_code='', cache_hit=0,
                    runtime_warm=1, queue_ms=1.5, http_ms=2., embedding_ms=3.,
                    head_ms=4., total_ms=5., recorded_at='time')
    return dict(step_id='step', action_id='action', status='arbitrary', summary='',
                evidence_json='not JSON', stdout_excerpt='out', stderr_excerpt='err',
                started_at='start', finished_at='finish')


def raw_insert(db, kind, key='1', **overrides):
    logical = new_values(kind) | overrides
    mapping = target.RUN11_COLUMN_MAP[kind]
    values = dict(record_type=kind, legacy_key=key) | {mapping.get(k, k): v for k, v in logical.items()}
    return db.execute('insert into runs(' + ','.join(values) + ') values('
                      + ','.join('?' for _ in values) + ')', tuple(values.values()))


def old_insert(db, *, ignore=False, **changes):
    values = dict(workload_kind='other', workload_key='work', attempt_number=1,
                  route_name='route', runtime_kind='runtime', credential_mode='cred',
                  model='m', status='completed') | changes
    return insert_run(db, 'agent_runtime_attempts', values, ignore=ignore)


def water(db, name):
    row = db.execute('select seq from main.sqlite_sequence where name=?', (name,)).fetchone()
    return row[0] if row else None


def initialize(db, **waters):
    db.execute('begin')
    target.initialize_run11_sequences(db, dict.fromkeys(('runs', *target.RUN11_NEW_KINDS)) | waters)


def test_original_columns_objects_and_fks_exact():
    with closing(connection()) as new, closing(connection(False)) as old:
        assert new.execute('pragma table_info(runs)').fetchall()[:62] == old.execute('pragma table_info(runs)').fetchall()
        assert new.execute('pragma foreign_key_list(runs)').fetchall() == old.execute('pragma foreign_key_list(runs)').fetchall()
        original = _declarations(mapped=True)
        current = dict(new.execute("select name,sql from sqlite_schema where tbl_name='runs' and sql is not null"))
        assert all(current[name] == sql for name, sql in original.items() if name != 'runs')
        old_table = original['runs']
        assert target.RUN11_BASE_TABLE_DDL == old_table


@pytest.mark.parametrize('kind', target.RUN11_NEW_KINDS)
def test_projection_native_order_and_source_scalars(kind):
    with closing(connection()) as db:
        raw_insert(db, kind, '-3')
        cur = db.execute(target.run11_projection(kind))
        assert tuple(d[0] for d in cur.description) == target.RUN11_FIELDS[kind]
        assert cur.fetchone() == (-3, *new_values(kind).values())
        assert water(db, 'runs:published') is None
        assert water(db, 'runs:' + kind) == 0
        if kind == SAMPLE:
            assert db.execute('select typeof(queue_ms),typeof(http_ms),typeof(embedding_ms),typeof(head_ms),typeof(total_ms) from runs').fetchone() == ('real',) * 5


@pytest.mark.parametrize('kind', target.RUN11_NEW_KINDS)
def test_each_source_notnull(kind):
    for field in target.RUN11_FIELDS[kind]:
        if field == 'id':
            continue
        with closing(connection()) as db, pytest.raises(sqlite3.IntegrityError):
            raw_insert(db, kind, **{field: None})


@pytest.mark.parametrize('kind', target.RUN11_NEW_KINDS)
@pytest.mark.parametrize('key', (None, '01', '1.0', ' 1', 'a', '9223372036854775808'))
def test_public_key_canonical_integer(kind, key):
    with closing(connection()) as db, pytest.raises(sqlite3.IntegrityError):
        raw_insert(db, kind, key)


@pytest.mark.parametrize('kind', target.RUN11_NEW_KINDS)
@pytest.mark.parametrize('key', ('0', '-1', '-9223372036854775808', '9223372036854775807'))
def test_source_zero_negative_and_extreme_ids(kind, key):
    with closing(connection()) as db:
        raw_insert(db, kind, key)
        assert db.execute(target.run11_projection(kind)).fetchone()[0] == int(key)
        assert water(db, 'runs:' + kind) == max(0, int(key))


@pytest.mark.parametrize('kind', target.RUN11_NEW_KINDS)
def test_cross_kind_columns_rejected(kind):
    for field in ('task_id', 'parent_id', 'operation_id', 'event_id'):
        with closing(connection()) as db, pytest.raises(sqlite3.IntegrityError):
            raw_insert(db, kind, **{field: 'unexpected'})
    for other in target.RUN11_NEW_KINDS:
        for field in set(target.RUN11_COLUMN_MAP[other].values()) - set(target.RUN11_COLUMN_MAP[kind].values()):
            with closing(connection()) as db, pytest.raises(sqlite3.IntegrityError):
                raw_insert(db, kind, **{field: 'unexpected'})


def test_new_columns_null_for_old_kind():
    with closing(connection()) as db:
        old_insert(db)
        for field, _affinity in target.RUN11_ADDED_COLUMNS:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute('update runs set ' + field + '=1')


@pytest.mark.parametrize('field,value', (
    ('model_id', ''), ('model_id', '   '), ('outcome', 'no'), ('fallback_code', 'x' * 65),
    ('cache_hit', 2), ('runtime_warm', -1), ('queue_ms', -1), ('http_ms', -1),
    ('embedding_ms', -1), ('head_ms', -1), ('total_ms', -1), ('recorded_at', ' '),
))
def test_native_sample_checks(field, value):
    with closing(connection()) as db, pytest.raises(sqlite3.IntegrityError):
        raw_insert(db, SAMPLE, **{field: value})


def test_source_unique_id_and_session_scope():
    with closing(connection()) as db:
        raw_insert(db, META)
        with pytest.raises(sqlite3.IntegrityError):
            raw_insert(db, META, '2')
        with pytest.raises(sqlite3.IntegrityError):
            raw_insert(db, META, session_id='second')
        raw_insert(db, SAMPLE)
        with pytest.raises(sqlite3.IntegrityError):
            raw_insert(db, SAMPLE)
        raw_insert(db, SETUP)
        with pytest.raises(sqlite3.IntegrityError):
            raw_insert(db, SETUP)
        # Source source_id is arbitrary TEXT, without a new parent FK.
        raw_insert(db, META, '2', session_id='second', source_id='unknown-parent')
        assert db.execute('select count(*) from runs').fetchone()[0] == 4


@pytest.mark.parametrize('native_water', (None, 0, 10))
def test_initialize_presence_and_no_commit(native_water):
    with closing(connection()) as db:
        initialize(db, runs=native_water)
        assert db.in_transaction
        assert water(db, 'runs:published') == native_water
        assert all(water(db, 'runs:' + kind) is None for kind in target.RUN11_NEW_KINDS)
        db.rollback()
        assert water(db, 'runs:published') is None


@pytest.mark.parametrize('native_water', (None, 0, 50))
@pytest.mark.parametrize('operation', ('ordinary', 'unique_ignore', 'check_ignore', 'check_abort', 'trigger_abort', 'savepoint', 'rollback'))
def test_old_native_attempts_differential(native_water, operation):
    result = []
    for candidate in (False, True):
        with closing(connection(candidate)) as db:
            if native_water is not None:
                db.execute("insert into sqlite_sequence(name,seq) values('runs',?)", (native_water,))
            db.commit()
            if candidate:
                initialize(db, runs=native_water)
                db.commit()
            first = old_insert(db)
            assert (first.lastrowid, first.rowcount) == ((native_water or 0) + 1, 1)
            db.commit()
            db.execute('begin')
            if operation == 'ordinary':
                cur = old_insert(db, attempt_number=2)
                outcome = (cur.lastrowid, cur.rowcount)
            elif operation in ('unique_ignore', 'check_ignore'):
                cur = old_insert(db, ignore=True, attempt_number=0 if operation == 'check_ignore' else 1)
                outcome = (cur.lastrowid, cur.rowcount)
            elif operation in ('check_abort', 'trigger_abort'):
                with pytest.raises(sqlite3.IntegrityError):
                    old_insert(db, ignore=operation == 'trigger_abort', attempt_number=0 if operation == 'check_abort' else 2,
                               session_mode='resume' if operation == 'trigger_abort' else 'fresh')
                outcome = db.in_transaction
            elif operation == 'savepoint':
                db.execute('savepoint one')
                old_insert(db, attempt_number=2)
                db.execute('rollback to one')
                db.execute('release one')
                outcome = db.in_transaction
            else:
                old_insert(db, attempt_number=2)
                db.rollback()
                outcome = db.in_transaction
            result.append((outcome, water(db, 'runs:published' if candidate else 'runs'),
                           db.execute('select id,legacy_key,attempt_number from runs order by id').fetchall(), db.in_transaction))
    assert result[0] == result[1]


def test_internal_new_rows_then_old_ignore_tracks_attempted_physical_id():
    with closing(connection()) as db:
        initialize(db, runs=10)
        db.execute("insert into sqlite_sequence(name,seq) values('runs',10)")
        raw_insert(db, SETUP, '1')
        assert water(db, 'runs:published') == 10
        assert old_insert(db).lastrowid == 12
        assert old_insert(db, ignore=True).rowcount == 0
        assert water(db, 'runs:published') == 13
        assert water(db, 'runs') == 13
        assert water(db, 'runs:' + SETUP) == 1


@pytest.mark.parametrize('ignore', (False, True))
@pytest.mark.parametrize('published_water', (None, 0, 9223372036854775807))
def test_native_full_preserves_outer_transaction_differential(ignore, published_water):
    results = []
    for candidate in (False, True):
        with closing(connection(candidate)) as db:
            if candidate:
                initialize(db, runs=published_water)
                db.commit()
            db.execute('create table marker(value)')
            db.execute("insert into sqlite_sequence(name,seq) values('runs',9223372036854775807)")
            db.commit()
            db.execute('insert into marker values(1)')
            with pytest.raises(sqlite3.OperationalError) as exc:
                old_insert(db, ignore=ignore)
            if candidate:
                assert water(db, 'runs:published') == published_water
            results.append((exc.value.sqlite_errorcode, db.in_transaction,
                            db.execute('select * from marker').fetchall(), water(db, 'runs')))
    assert results == [(sqlite3.SQLITE_FULL, True, [(1,)], 9223372036854775807)] * 2


def test_profiles_exact_not_business_snapshots():
    actual = target.RUN11_SOURCE_PROFILES['actual']
    fresh = target.RUN11_SOURCE_PROFILES['fresh']
    assert actual[META] == fresh[META]
    assert actual[SAMPLE] == fresh[SAMPLE]
    assert actual[SETUP] != fresh[SETUP]
    for profile, finished in ((actual, 'current_timestamp'), (fresh, "''")):
        with closing(sqlite3.connect(':memory:')) as db:
            for kind in target.RUN11_NEW_KINDS:
                for object_type, _name, sql in sorted(profile[kind], key=lambda x: x[0] != 'table'):
                    db.execute(sql)
            assert db.execute('pragma table_info(setup_wizard_events)').fetchall()[-1][4] == finished
            assert len(db.execute('pragma table_info(codex_session_search_index)').fetchall()) == 7
            assert len(db.execute('pragma table_info(email_classifier_runtime_samples)').fetchall()) == 12
            assert len(db.execute('pragma table_info(setup_wizard_events)').fetchall()) == 10


@pytest.mark.parametrize('kind,values', (
    ('agent_runs', dict(task_id=101, execution_generation='g', role='consumer')),
    ('agent_runtime_attempts', dict(workload_kind='other', workload_key='work', attempt_number=1,
        route_name='route', runtime_kind='runtime', credential_mode='cred', model='m', status='completed')),
    ('agent_run_state_events', dict(parent_id=99, phase='phase', structured_error_json='')),
    ('task_agent_runs', dict(summary_input_id=1)),
    ('meeting_alignment_runs', dict(task_id=102, status='any')),
    ('okr_review_runs', dict(request_id=1)),
    ('scheduled_task_runs', dict(event_id='event', scheduled_task_id=1,
        trigger_kind='manual', scheduled_for='time')),
    ('workbench_events', dict(task_id=103, sequence=1, event_type='event', payload_json='literal')),
))
def test_all_original_kinds_native_positive_insert(kind, values):
    results = []
    for candidate in (False, True):
        with closing(connection(candidate)) as db:
            # Freeze defaults too: runtime attempts also initialize started_at/updated_at.
            db.create_function('current_timestamp', 0, lambda: '2026-01-01 00:00:00')
            db.executemany('insert into tasks values (?,?)', (
                (101, 'reply_tasks'), (102, 'meeting_alignment_jobs'), (103, 'workbench_turns')))
            # Fixed literal timestamp removes elapsed-clock noise only from this differential.
            cursor = insert_run(db, kind, values | {'created_at': 'fixed'})
            results.append((cursor.lastrowid, cursor.rowcount, db.execute(
                'select ' + ','.join(target.RUN11_BASE_COLUMNS) + ' from runs').fetchall(),
                water(db, 'runs:published' if candidate else 'runs')))
    assert results[0] == results[1]


@pytest.mark.parametrize('value', (None, 0, 37))
def test_initialize_all_current_streams_and_explicit_rollback(value):
    with closing(connection()) as db:
        initialize(db, **dict.fromkeys(('runs', *target.RUN11_NEW_KINDS), value))
        assert [water(db, name) for name in ('runs:published', *(('runs:' + k) for k in target.RUN11_NEW_KINDS))] == [value] * 4
        assert water(db, 'runs') is None
        db.rollback()
        assert db.execute('select count(*) from sqlite_sequence').fetchone()[0] == 0


def test_schema_creator_does_not_commit_and_no_added_fk():
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('begin')
        target.create_runs11_schema(db)
        assert db.in_transaction
        db.rollback()
        assert not db.execute("select 1 from sqlite_schema where name='runs'").fetchone()
    with closing(connection()) as db:
        db.execute('create table configs(schedule_snapshot_public_id integer unique)')
        db.execute('create table schedules(id integer primary key)')
        db.execute('pragma foreign_keys=on')
        raw_insert(db, META, source_id='no-parent')
        raw_insert(db, SETUP, evidence_json='invalid JSON', status='anything')
        assert db.execute('pragma foreign_key_check').fetchall() == []


def test_metadata_public_id_max_cost_bounded_by_its_kind():
    """Unrelated old Runs history must not enter the new-source allocator scan."""
    query = 'select max(cast(legacy_key as integer)) from main.runs where record_type=?'
    with closing(connection()) as db:
        raw_insert(db, META)

        def measured_max():
            instructions = 0

            def count_instruction():
                nonlocal instructions
                instructions += 1
                return 0

            db.set_progress_handler(count_instruction, 1)
            try:
                result = db.execute(query, (META,)).fetchone()[0]
            finally:
                db.set_progress_handler(None, 0)
            return result, instructions

        before_value, before_steps = measured_max()
        # Native positive old-kind rows have public IDs above metadata's ID1.
        for attempt in range(1, 2001):
            old_insert(db, attempt_number=attempt)
        after_value, after_steps = measured_max()
        assert before_value == after_value == 1
        assert after_steps <= before_steps + 8, (before_steps, after_steps)
        assert after_steps < 100, after_steps
        plan = ' '.join(str(row[3]) for row in db.execute(
            'explain query plan ' + query, (META,)))
        assert 'idx_runs11_metadata_id' in plan, plan
