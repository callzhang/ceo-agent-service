"""Unselected retained-runs FK translation against the real Workbench table."""
import sqlite3

import pytest

from app.configs_definitions import CONFIG_INDEX_DDL
from app.configs_run_schema import map_runs_to_configs, restore_native_run_fks
from app.configs_storage import create_configs_schema, insert_config
from app.run_storage import _declared_run_objects, create_run_schema, insert_run
from app.schedules_storage import create_schedules_schema, insert_schedule
from app.task_storage import create_task_schema
from tests.configs_source_fixtures import source_db


SCHEDULE = dict(id=7, name='seven', prompt='p', cron_expression='* * * * *',
                timezone='UTC', runtime_id='r', enabled=1)
SOURCE_EDGES = (
    'foreign key(scheduled_task_id) references scheduled_tasks(id)',
    'foreign key(snapshot_id) references scheduled_task_config_versions(id)',
)
TARGET_EDGES = (
    'foreign key(scheduled_task_id) references schedules(id)',
    'foreign key(snapshot_id) references configs(schedule_snapshot_public_id)',
)


def objects(db):
    return dict(db.execute("select name,sql from main.sqlite_schema where tbl_name='runs' and sql is not null"))


def snapshot(db):
    columns = [r[1] for r in db.execute('pragma main.table_xinfo(runs)') if r[6] == 0]
    return (tuple(columns), db.execute('select ' + ','.join(columns) + ' from main.runs order by id').fetchall(),
            db.execute('select ' + ','.join(f'typeof({c})' for c in columns) + ' from main.runs order by id').fetchall(),
            dict(db.execute("select name,seq from main.sqlite_sequence where name='runs' or name like 'runs:%'")))


@pytest.fixture(params=('actual', 'fresh'))
def db(request):
    with source_db(request.param) as conn:
        create_task_schema(conn, include_workbench=True)
        create_run_schema(conn, include_workbench=True)
        create_schedules_schema(conn)
        create_configs_schema(conn)
        conn.execute("insert into scheduled_tasks(id,name,prompt,cron_expression,timezone,runtime_id,enabled) values(7,'seven','p','* * * * *','UTC','r',1)")
        conn.execute("insert into scheduled_task_config_versions(id,snapshot_json) values(11,'{}')")
        conn.execute('begin') if not conn.in_transaction else None
        insert_schedule(conn, SCHEDULE)
        insert_config(conn, 'scheduled_task_config_versions', {'id': 11, 'snapshot_json': '{}'})
        conn.commit()
        yield conn


def run(db, event, schedule=7, snap=11, **extra):
    values = dict(event_id=event, scheduled_task_id=schedule, snapshot_id=snap,
                  trigger_kind='manual', scheduled_for='instant', occurrence_count=-8)
    values.update(extra)
    return insert_run(db, 'scheduled_task_runs', values)


def convert(db):
    db.execute('pragma foreign_keys=off')
    db.execute('begin')
    map_runs_to_configs(db)


def test_exact_fk_declarations_and_full_unique_alias(db):
    before = objects(db)
    convert(db)
    after = objects(db)
    assert set(after) == set(before)
    assert all(x in before['runs'] for x in SOURCE_EDGES)
    assert all(x in after['runs'] for x in TARGET_EDGES)
    expected = before['runs']
    for old, new in zip(SOURCE_EDGES, TARGET_EDGES):
        expected = expected.replace(old, new)
    assert after == before | {'runs': expected}
    assert 'schedule_snapshot_public_id' in [r[1] for r in db.execute('pragma main.table_xinfo(configs)')]
    assert any('on configs(schedule_snapshot_public_id)' in sql and ' where ' not in sql.lower()
               for sql in CONFIG_INDEX_DDL)
    assert db.execute("select schedule_snapshot_public_id from configs where record_type='scheduled_task_config_versions'").fetchone() == (11,)
    db.rollback()


def test_real_scheduled_run_insert_and_inverse_current_facts(db):
    run(db, 'existing')
    db.execute("update sqlite_sequence set seq=500 where name='runs'")
    db.execute("insert into sqlite_sequence(name,seq) values('runs:scheduled_task_runs',190)")
    db.execute("insert into sqlite_sequence(name,seq) values('runs:agent_runs',50)")
    before = snapshot(db)
    db.commit()
    convert(db)
    assert snapshot(db) == before
    db.commit()
    db.execute('pragma foreign_keys=on')
    assert run(db, 'after', snap=None).rowcount == 1
    for schedule, snap in ((999, 11), (7, 999)):
        with pytest.raises(sqlite3.IntegrityError):
            run(db, f'missing-{schedule}-{snap}', schedule=schedule, snap=snap)
    db.commit()
    target = snapshot(db)
    db.execute('pragma foreign_keys=off')
    db.execute('begin')
    restore_native_run_fks(db)
    assert snapshot(db) == target
    assert objects(db) == _declared_run_objects(True, True)
    db.rollback()


def test_signed_zero_and_native_integer_affinity_with_kind_restricted_snapshot(db):
    for identity in (-3, 0):
        native = SCHEDULE | {'id': identity, 'name': f's-{identity}'}
        db.execute("insert into scheduled_tasks(id,name,prompt,cron_expression,timezone,runtime_id,enabled) values(?,?,?,?,?,?,?)",
                   (identity, native['name'], 'p', '* * * * *', 'UTC', 'r', 1))
        insert_schedule(db, native)
        payload = f'{{"id":{identity}}}'
        db.execute('insert into scheduled_task_config_versions(id,snapshot_json) values(?,?)',
                   (identity, payload))
        insert_config(db, 'scheduled_task_config_versions',
                      {'id': identity, 'snapshot_json': payload})
    insert_config(db, 'managed_skills',
                  {'id': 21, 'name': 'wrong-kind', 'display_name': 'Wrong', 'created_at': 't'})
    db.commit()
    convert(db)
    db.commit()
    db.execute('pragma foreign_keys=on')
    for identity in (-3, 0):
        assert run(db, f'affinity-{identity}', schedule=str(identity), snap=str(identity)).rowcount == 1
        assert db.execute('select scheduled_task_id,snapshot_id,typeof(scheduled_task_id),typeof(snapshot_id) '
                          'from runs where event_id=?', (f'affinity-{identity}',)).fetchone() == (
                              identity, identity, 'integer', 'integer')
    with pytest.raises(sqlite3.IntegrityError):
        run(db, 'wrong-kind', snap=21)
    assert db.execute("select schedule_snapshot_public_id from configs where record_type='managed_skills'").fetchone() == (None,)


def test_unrelated_orphan_and_binary_event_fact_survive_both_directions(db):
    db.execute('pragma foreign_keys=off')
    insert_run(db, 'workbench_events', dict(
        task_id=987654, sequence=-7, event_type='unknown', payload_json=b'\x00\xff'))
    before = snapshot(db)
    finding = tuple(db.execute('pragma main.foreign_key_check(runs)'))
    assert len(finding) == 1 and finding[0][2] == 'tasks'
    db.commit()
    db.execute('begin')
    map_runs_to_configs(db)
    assert snapshot(db) == before
    assert tuple(db.execute('pragma main.foreign_key_check(runs)')) == finding
    restore_native_run_fks(db)
    assert snapshot(db) == before
    assert tuple(db.execute('pragma main.foreign_key_check(runs)')) == finding
    db.rollback()


def test_reject_changed_or_partial_declaration_and_require_fk_off_transaction(db):
    with pytest.raises(sqlite3.ProgrammingError):
        map_runs_to_configs(db)
    db.execute('begin')
    with pytest.raises(sqlite3.ProgrammingError):
        map_runs_to_configs(db)
    db.rollback()
    db.execute('pragma foreign_keys=off')
    db.execute('begin')
    db.execute('drop index idx_scheduled_task_runs_event')
    with pytest.raises(ValueError, match='runs declaration'):
        map_runs_to_configs(db)
    db.rollback()
    db.execute('begin')
    db.execute('pragma writable_schema=on')
    source = objects(db)['runs']
    db.execute("update main.sqlite_schema set sql=? where name='runs'", (source.replace(SOURCE_EDGES[0], TARGET_EDGES[0]),))
    db.execute('pragma writable_schema=off')
    with pytest.raises(ValueError, match='runs declaration'):
        map_runs_to_configs(db)
    db.rollback()


def test_savepoint_rollback_restores_source_rows_and_declarations(db):
    run(db, 'before')
    db.commit()
    db.execute('pragma foreign_keys=off')
    db.execute('begin')
    before = snapshot(db), objects(db)
    db.execute('savepoint retained_runs')
    map_runs_to_configs(db)
    run(db, 'during', snap=None)
    db.execute('rollback to retained_runs')
    assert (snapshot(db), objects(db)) == before
    db.execute('release retained_runs')
    db.rollback()


def _event(conn, physical_id, task_id, sequence, marker):
    insert_run(conn, 'workbench_events', dict(
        id=physical_id, task_id=task_id, sequence=sequence,
        event_type=b'\x00\xff' + bytes([marker]),
        payload_json='head\x00tail' + str(marker)))


def _stage_absent(conn):
    assert conn.execute("select 1 from temp.sqlite_schema where name='__configs_runs_fk_stage'").fetchone() is None


def test_storage_classes_and_current_facts_across_repeated_round_trip(db):
    db.execute('pragma foreign_keys=off')
    _event(db, -5, 987654, 2.5, 1)
    raw = db.execute('select event_type,payload_json from main.runs where id=-5').fetchone()
    assert raw == (bytes((0, 255, 1)), 'head' + chr(0) + 'tail1')
    assert raw[0][0] == 0 and ord(raw[1][4]) == 0
    before = snapshot(db)
    assert ('real',) in [(row[before[0].index('sequence')],) for row in before[2]]
    db.commit()
    db.execute('begin')
    for cycle in range(2):
        map_runs_to_configs(db)
        assert snapshot(db) == before
        _stage_absent(db)
        _event(db, -6 - cycle, 987655 + cycle, 3.5 + cycle, cycle + 2)
        current = snapshot(db)
        restore_native_run_fks(db)
        assert snapshot(db) == current
        assert len(tuple(db.execute('pragma main.foreign_key_check(runs)'))) == cycle + 2
        _stage_absent(db)
        before = current
    db.rollback()


@pytest.mark.parametrize('name', (
    '__configs_runs_fk_stage', 'RUNS', '__CONFIGS_RUNS_FK_STAGE'))
def test_temp_stage_collision_fails_before_main_write(db, name):
    db.execute('pragma foreign_keys=off')
    db.execute('begin')
    before = snapshot(db), objects(db)
    db.execute(f'create temp table "{name}"(x)')
    with pytest.raises(ValueError, match='TEMP name collision'):
        map_runs_to_configs(db)
    assert (snapshot(db), objects(db)) == before
    db.rollback()
    _stage_absent(db)
