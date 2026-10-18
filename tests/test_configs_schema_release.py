"""Synthetic pure-schema config retirement and current-fact inverse."""
from contextlib import contextmanager
import json
import sqlite3

import pytest

from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_skill_config_storage import insert_skill_receipt
from app.action_storage import _objects, create_action_schema, insert_action, _current_objects
from app.configs_schema_release import (
    capture_config_source, configs_schema_is_current, migrate_config_schema,
    restore_config_schema,
)
from app.configs_run_schema import _declarations as run_declarations, _objects as run_objects
from app.configs_schema_transfer import SOURCE_TABLES
from app.configs_storage import insert_config
from app.email_classification_schema import create_current_email_classifications
from app.email_current_schema import _CURRENT_INDEX_SQL, _CURRENT_TABLE_SQL
from app.email_store import _REQUIRED_TRIGGER_SQL
from app.run_storage import create_run_schema, insert_run
from app.schedules_storage import insert_schedule
from app.task_storage import create_task_schema
from tests.configs_source_fixtures import ROOT
from tests.test_configs_schema_transfer import native, seed


OBS = 'email_training_snapshot_observations'
SKILL = ('managed_skill_export_receipts', 'runtime_skill_load_receipts')


def install_observations(db, variant):
    if variant == 'actual':
        baseline = json.loads((ROOT / 'docs/superpowers/specs/2026-10-11-database-redesign-schema-baseline.json').read_text())
        db.execute(next(item['schema'] for item in baseline['tables'] if item['name'] == OBS))
        indexes = [item['sql'] for item in baseline['indexes']
                   if item['tbl_name'] == OBS and item['sql']]
        triggers = [item['sql'] for item in baseline['triggers'] if item['tbl_name'] == OBS]
    else:
        db.execute(dict(_CURRENT_TABLE_SQL)[OBS])
        indexes = [sql for name, sql in _CURRENT_INDEX_SQL if 'observations_' in name]
        triggers = [_REQUIRED_TRIGGER_SQL[name] for name in (
            'trg_email_training_observations_immutable_update',
            'trg_email_training_observations_immutable_delete',
            'trg_email_training_observations_require_unfrozen_snapshot',
        )]
    for sql in (*indexes, *triggers):
        db.execute(sql)


@contextmanager
def source(variant='actual_email52', action_variant='actual_no_memory_fk',
           observation_variant=None):
    with native(variant) as db:
        base = variant.split('_')[0]
        install_observations(db, observation_variant or base)
        create_task_schema(db, include_workbench=True)
        create_run_schema(db, include_workbench=True)
        create_current_email_classifications(db)
        create_action_schema(db, include_reply=True, reply_variant=action_variant, include_email=True)
        db.commit()
        seed(db)
        db.rollback()
        db.execute('pragma foreign_keys=off')
        db.execute('begin')
        insert_run(db, 'scheduled_task_runs', dict(
            event_id='source', scheduled_task_id=500, snapshot_id=400,
            trigger_kind='manual', scheduled_for='now', occurrence_count=1))
        insert_action(db, 'outbound_postfixes', dict(
            channel='wechat', delivery_key='old-effect', final_body='body',
            feedback_token='', postfix_version='v1'))
        db.execute("insert into sqlite_sequence(name,seq) values('actions:sent_replies',950)")
        yield db


def action_facts(db, columns):
    names = ','.join('"' + name + '"' for name in columns)
    return (db.execute(f'select {names} from actions where record_type not in (?,?) order by id', SKILL).fetchall(),
            db.execute('select name,seq from sqlite_sequence where name like "actions:%" '
                       'and name not in (?,?) order by name',
                       tuple('actions:' + kind for kind in SKILL)).fetchall())


def test_release_entry_points_exist():
    assert callable(capture_config_source)
    assert callable(migrate_config_schema)
    assert callable(restore_config_schema)


@pytest.mark.parametrize('config_variant', ('actual_email52', 'fresh_email52'))
@pytest.mark.parametrize('observation_variant', ('actual', 'fresh'))
def test_observation_declaration_is_independent_of_config_layout(
    config_variant, observation_variant
):
    with source(config_variant, observation_variant=observation_variant) as db:
        captured = capture_config_source(db)
        assert captured['configs']['variant'] == config_variant
        assert _fingerprint(captured['observations']) == _fingerprint(
            _source_declaration(db, OBS)
        )
        migrate_config_schema(db, captured)
        restore_config_schema(db, captured)
        assert _fingerprint(_source_declaration(db, OBS)) == _fingerprint(
            captured['observations']
        )


@pytest.mark.parametrize('variant', ('actual', 'fresh'))
def test_original_config_profile_with_email29_is_not_a_complete_source(variant):
    with source(variant) as db:
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(sqlite3.OperationalError, match='no such table: main.email_actions'):
            capture_config_source(db)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before
        assert _source_declaration(db, 'configs')['sql'] is None


@pytest.mark.parametrize('variant', ('actual_email52', 'fresh_email52'))
@pytest.mark.parametrize('action_variant', ('actual_no_memory_fk', 'fresh_memory_fk'))
def test_retire_restore_current_facts_and_exact_declarations(variant, action_variant):
    with source(variant, action_variant) as db:
        metadata = capture_config_source(db)
        assert 'rows' not in json.dumps(metadata)
        before = {name: _source_declaration(db, name) for name in (*SOURCE_TABLES, OBS)}
        before_runs = run_objects(db)
        action_columns = tuple(row[1] for row in db.execute('pragma main.table_xinfo(actions)') if row[6] == 0)
        actions = action_facts(db, action_columns)
        counts = migrate_config_schema(db, metadata)
        assert db.in_transaction
        assert sum(counts.values()) > 0
        assert configs_schema_is_current(db, action_variant=action_variant)
        assert db.execute("select 1 from temp.sqlite_schema where name='__configs_actions_stage'").fetchone() is None
        assert all(_source_declaration(db, name)['sql'] is None for name in before)
        assert len(set(row[0] for row in db.execute('select record_type from actions'))) <= 31
        assert _objects(db, 'actions') == _current_objects(
            include_reply=True, reply_variant=action_variant, include_email=True,
            include_skill_config_receipts=True)
        assert action_facts(db, action_columns) == actions
        assert db.execute('select count(*) from runs').fetchone() == (1,)
        insert_schedule(db, dict(id=80, name='new', prompt='p', cron_expression='* * * * *',
                                 timezone='UTC', runtime_id='r', enabled=1))
        insert_config(db, 'scheduled_task_config_versions', {'id': 81, 'snapshot_json': '{}'})
        exported = insert_skill_receipt(db, SKILL[0], dict(
            revision_id=200, sha256=b'new-sha', path=b'new-path', created_at=b'new-time'))
        loaded = insert_skill_receipt(db, SKILL[1], dict(
            config_id=300, pid=98, loaded_json=json.dumps({20: 'digest-é'}, sort_keys=True),
            created_at='new-time'))
        failed = insert_skill_receipt(db, SKILL[1], dict(
            config_id=300, pid=99, loaded_json='{}', error='failure', created_at='new-time'))
        insert_action(db, 'outbound_postfixes', dict(
            channel='wechat', delivery_key='new-effect', final_body='new body',
            feedback_token='', postfix_version='v1'))
        current_actions = action_facts(db, action_columns)
        insert_run(db, 'scheduled_task_runs', dict(
            event_id='new', scheduled_task_id=80, snapshot_id=81,
            trigger_kind='manual', scheduled_for='later', occurrence_count=1))
        assert restore_config_schema(db, metadata)['scheduled_tasks'] == counts['scheduled_tasks'] + 1
        assert db.in_transaction
        assert {name: _source_declaration(db, name) for name in before} == before
        assert run_objects(db) == before_runs == run_declarations(mapped=False)
        assert db.execute("select 1 from temp.sqlite_schema where name='__configs_actions_stage'").fetchone() is None
        assert action_facts(db, action_columns) == current_actions
        assert dict(db.execute('select event_id,snapshot_id from runs')) == {'source': 400, 'new': 81}
        assert db.execute('select id from scheduled_tasks where id=80').fetchone() == (80,)
        assert db.execute('select revision_id,sha256,path,created_at from managed_skill_export_receipts '
                          'where id=?', (exported,)).fetchone() == (200, b'new-sha', b'new-path', b'new-time')
        assert db.execute('select pid,loaded_json,error from runtime_skill_load_receipts '
                          'where id in (?,?) order by id', (loaded, failed)).fetchall() == [
            (98, json.dumps({20: 'digest-é'}, sort_keys=True), ''), (99, '{}', 'failure')]
        assert not configs_schema_is_current(db, action_variant=action_variant)
        migrate_config_schema(db, capture_config_source(db))
        assert configs_schema_is_current(db, action_variant=action_variant)


def test_nonempty_observations_and_changed_source_refuse_before_writes():
    with source() as db:
        fields = [row[1] for row in db.execute(f'pragma table_xinfo({OBS})')]
        values = ['s1' if name == 'snapshot_id' else 'x' for name in fields]
        guard = db.execute("select sql from sqlite_schema where name=?",
                           ('trg_email_training_observations_require_unfrozen_snapshot',)).fetchone()[0]
        db.execute('drop trigger trg_email_training_observations_require_unfrozen_snapshot')
        db.execute('pragma ignore_check_constraints=on')
        db.execute(f'insert into {OBS} values({",".join("?" for _ in fields)})', values)
        db.execute('pragma ignore_check_constraints=off')
        db.execute(guard)
        with pytest.raises(ValueError, match='nonempty'):
            capture_config_source(db)
        assert _source_declaration(db, 'configs')['sql'] is None
    with source() as db:
        db.execute('alter table managed_skills add column unexpected text')
        with pytest.raises(ValueError, match='declaration'):
            capture_config_source(db)
        assert _source_declaration(db, 'configs')['sql'] is None


def test_midcopy_failure_requires_caller_rollback(monkeypatch):
    import app.configs_schema_transfer as transfer
    with source() as db:
        metadata = capture_config_source(db)
        before = {name: _source_declaration(db, name) for name in (*SOURCE_TABLES, OBS)}
        db.execute('savepoint release_attempt')
        monkeypatch.setattr(transfer, '_copy_skill_receipt_row',
                            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                                sqlite3.IntegrityError('synthetic interruption')))
        with pytest.raises(sqlite3.IntegrityError, match='interruption'):
            migrate_config_schema(db, metadata)
        assert db.in_transaction
        db.execute('rollback to release_attempt')
        db.execute('release release_attempt')
        assert {name: _source_declaration(db, name) for name in before} == before
        assert _source_declaration(db, 'configs')['sql'] is None
        assert db.execute('select count(*) from runs').fetchone() == (1,)


def test_unreconstructable_receipt_body_refuses_before_schema_change():
    with source() as db:
        db.execute("insert into runtime_skill_load_receipts(id,config_id,pid,loaded_json,error) "
                   "values(9003,300,13,' { } ','failure')")
        metadata = capture_config_source(db)
        with pytest.raises(ValueError, match='reconstruct'):
            migrate_config_schema(db, metadata)
        assert _source_declaration(db, 'configs')['sql'] is None
        assert _source_declaration(db, OBS)['sql'] == metadata['observations']['sql']


def test_source_target_mixture_and_transaction_contract():
    with source() as db:
        metadata = capture_config_source(db)
        db.execute('create table configs(id integer primary key)')
        with pytest.raises(ValueError, match='partial'):
            migrate_config_schema(db, metadata)
        db.execute('drop table configs')
        db.commit()
        with pytest.raises(sqlite3.ProgrammingError, match='transaction'):
            migrate_config_schema(db, metadata)


def test_unrelated_run_orphan_preserves_same_foreign_key_finding():
    with source() as db:
        insert_run(db, 'workbench_events', dict(
            task_id=987654, sequence=-7, event_type='unknown', payload_json=b'\x00\xff'))
        before = tuple(db.execute('pragma main.foreign_key_check(runs)'))
        assert len(before) == 1 and before[0][2] == 'tasks'
        metadata = capture_config_source(db)
        migrate_config_schema(db, metadata)
        assert tuple(db.execute('pragma main.foreign_key_check(runs)')) == before
        restore_config_schema(db, metadata)
        assert tuple(db.execute('pragma main.foreign_key_check(runs)')) == before


def test_broken_current_config_parent_refuses_inverse_without_native_rows():
    with source() as db:
        metadata = capture_config_source(db)
        migrate_config_schema(db, metadata)
        db.execute('savepoint inverse_attempt')
        insert_config(db, 'runtime_skill_configs',
                      {'id': 900, 'parent_id': 899, 'status': 'pending_restart'})
        with pytest.raises(ValueError, match='foreign keys'):
            restore_config_schema(db, metadata)
        db.execute('rollback to inverse_attempt')
        db.execute('release inverse_attempt')
        assert configs_schema_is_current(db, action_variant=metadata['action_variant'])


def test_changed_runs_and_target_declaration_refuse_before_changes():
    with source() as db:
        db.execute('drop index idx_scheduled_task_runs_event')
        with pytest.raises(ValueError, match='native runs'):
            capture_config_source(db)
        assert _source_declaration(db, 'configs')['sql'] is None
    with source() as db:
        metadata = capture_config_source(db)
        migrate_config_schema(db, metadata)
        db.execute('drop index idx_configs_legacy')
        with pytest.raises(ValueError, match='target declaration'):
            restore_config_schema(db, metadata)
        assert all(_source_declaration(db, name)['sql'] is None
                   for name in (*SOURCE_TABLES, OBS))


def test_new_external_target_foreign_key_refuses_inverse():
    with source() as db:
        metadata = capture_config_source(db)
        migrate_config_schema(db, metadata)
        db.execute('create table unexpected_config_child(id integer primary key, '
                   'config_id integer references configs(id))')
        with pytest.raises(ValueError, match='external config target'):
            restore_config_schema(db, metadata)
        assert configs_schema_is_current(db, action_variant=metadata['action_variant'])


def test_empty_external_observation_child_refuses_before_forward_ddl():
    with source() as db:
        db.execute('create table external_observation_child('
                   'snapshot_id text, account_id text, stable_message_identity text, '
                   'foreign key(snapshot_id,account_id,stable_message_identity) '
                   f'references {OBS}(snapshot_id,account_id,stable_message_identity))')
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(ValueError, match='external config foreign key'):
            capture_config_source(db)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before
        assert _source_declaration(db, 'configs')['sql'] is None


def test_mixed_case_external_source_parent_refuses_before_ddl():
    with source() as db:
        db.execute('create table mixed_case_source_child('
                   'skill_id integer references "MANAGED_SKILLS"(id))')
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(ValueError, match='external config foreign key'):
            capture_config_source(db)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before


@pytest.mark.parametrize('sql', (
    'create view external_config_view as select id from scheduled_tasks',
    f'create view external_config_view as select snapshot_id from {OBS}',
    'create trigger external_config_trigger after insert on external_probe '
    'begin select id from scheduled_tasks; end',
    'create trigger external_config_trigger after insert on external_probe '
    'begin select id from managed_skills; end',
))
def test_external_source_sql_dependency_refuses_before_forward_ddl(sql):
    with source() as db:
        db.execute('create table external_probe(id integer primary key)')
        db.execute(sql)
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(ValueError, match='SQL dependency'):
            capture_config_source(db)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before
        assert _source_declaration(db, 'configs')['sql'] is None


@pytest.mark.parametrize('sql', (
    'create view external_config_view as select id from configs',
    'create trigger external_config_trigger after insert on external_probe '
    'begin select id from schedules; end',
))
def test_external_target_sql_dependency_refuses_before_inverse_ddl(sql):
    with source() as db:
        metadata = capture_config_source(db)
        migrate_config_schema(db, metadata)
        db.execute('create table external_probe(id integer primary key)')
        db.execute(sql)
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(ValueError, match='SQL dependency'):
            restore_config_schema(db, metadata)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before


def test_mixed_case_external_target_parent_refuses_before_ddl():
    with source() as db:
        metadata = capture_config_source(db)
        migrate_config_schema(db, metadata)
        db.execute('create table mixed_case_target_child('
                   'config_id integer references "CONFIGS"(id))')
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(ValueError, match='external config target foreign key'):
            restore_config_schema(db, metadata)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before


@pytest.mark.parametrize('name', ('actions', 'Actions', 'runs', OBS))
def test_forward_temp_shadow_refuses_before_ddl(name):
    with source() as db:
        db.execute(f'create temp table "{name}"(id integer)')
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(ValueError, match='TEMP name collision'):
            capture_config_source(db)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before
        assert _source_declaration(db, 'configs')['sql'] is None


@pytest.mark.parametrize('name', ('configs', 'CONFIGS', 'schedules', 'actions', 'runs', OBS,
                                  'MANAGED_SKILLS'))
def test_inverse_temp_shadow_refuses_before_ddl(name):
    with source() as db:
        metadata = capture_config_source(db)
        migrate_config_schema(db, metadata)
        db.execute(f'create temp table "{name}"(id integer)')
        before = tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name'))
        with pytest.raises(ValueError, match='TEMP name collision'):
            restore_config_schema(db, metadata)
        assert tuple(db.execute('select type,name,sql from main.sqlite_schema order by type,name')) == before
