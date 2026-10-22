"""Matched core-run publication must keep replacement writes on inverse."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app.store_schema_release import prepare_store_schema_release
from app.run_storage import insert_run

ROOT = Path(__file__).resolve().parents[1]
VERSION_KEY = 'store_schema_version'


def legacy_database(tmp_path):
    target = tmp_path / 'target'
    (target / 'app').mkdir(parents=True)
    (target / 'app/store.py').write_text("STORE_SCHEMA_VERSION = '2026-10-11.2'\n")
    path = tmp_path / 'source.sqlite3'
    captured = json.loads((ROOT / 'docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json').read_text())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table reply_tasks(id integer primary key)')
        db.execute('insert into reply_tasks values(1)')
        for name in ['agent_runs','agent_runtime_attempts','agent_run_state_events','agent_run_events']:
            source = captured['tables'][name]
            db.execute(source['sql'])
            for kind, _, sql in source['indexes_and_triggers']:
                if sql is not None:
                    db.execute(sql)
        db.execute('create table service_state(key text primary key,value text,updated_at text)')
        db.execute('insert into service_state values(?,?,?)',(VERSION_KEY,'2026-10-11.1','source-time'))
        db.execute('create table receipt(id integer primary key autoincrement,agent_run_id integer references agent_runs(id),body blob)')
        db.execute("insert into agent_runs(id,reply_task_id,execution_generation,role,status) values(1,1,'source','consumer','completed')")
        db.execute("insert into agent_runtime_attempts(id,agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status) values(1,1,'agent_run','1',1,'test','codex_cli','local_oauth','test','completed')")
        db.execute("insert into agent_run_state_events(agent_run_id,phase,structured_error_json) values(1,'source','{}')")
        db.execute("insert into receipt(agent_run_id,body) values(1,x'0010')")
    return target,path


def migrate(root,path):
    from app.run_storage import migrate_run_schema
    with closing(sqlite3.connect(path,isolation_level=None)) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        migrate_run_schema(db)
        db.execute('update service_state set value=?,updated_at=? where key=?',('2026-10-11.2','replacement-time',VERSION_KEY))
        db.commit()


def source_fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        return {
            'ddl': db.execute("select type,name,sql from sqlite_master where type in ('table','index','trigger') and name not like 'sqlite_%' order by type,name").fetchall(),
            'sequence': db.execute('select name,seq from sqlite_sequence order by name').fetchall(),
            'version': db.execute('select * from service_state').fetchall(),
        }


def test_publication_run_source_keeps_current_consumer_audit_boundary(tmp_path):
    from app.run_schema_release import publication_run_sources
    from app.store import AutoReplyStore

    store = AutoReplyStore(tmp_path / 'current-publication.sqlite3')
    reply = store.enqueue_reply_task(
        conversation_id='c', conversation_title='C', single_chat=True,
        trigger_message_id='m', trigger_create_time='', trigger_sender='s', trigger_text='body',
    )
    summary = store.enqueue_work_summary_input('local_file', 'publication', '{}')
    meeting = store.upsert_meeting_alignment_job(
        meeting_id='publication', title='Meeting', source_json='{}',
        participants_json='[]', ended_at='', eligible_at='', status='pending',
    )
    request = store.create_okr_review_request(
        conversation_id='okr', conversation_title='OKR', trigger_message_id='okr',
        trigger_sender='s', trigger_sender_user_id='u', trigger_text='review',
        period_label='period', period_start='', period_end='', okr_source_json='{}',
    )
    with store._connect() as db:
        parent = db.execute(
            "select id from tasks where record_type='reply_tasks' and cast(legacy_key as integer)=?",
            (reply,),
        ).fetchone()[0]
        roles = [insert_run(db, 'agent_runs', dict(task_id=parent, execution_generation='g',
                 role=role, operation_id=role)).lastrowid for role in ('consumer', 'audit')]
    for source, parent in (('task_agent_runs', summary), ('meeting_alignment_runs', meeting),
                           ('okr_review_runs', request)):
        run_id = store.create_logical_run(
            source, parent, status=None if source == 'okr_review_runs' else 'completed',
        )
        with store._connect() as db:
            db.execute('update runs set legacy_key=? where id=?', (str(roles[0]), run_id))
    with store._connect() as db:
        agent_source, runtime_source = publication_run_sources(db)
        assert [row[0] for row in db.execute(f'select id from ({agent_source}) order by id')] == roles
        assert db.execute(f'select count(*) from ({runtime_source})').fetchone()[0] == 0


@pytest.mark.parametrize('unified', [False, True])
def test_publication_run_source_preserves_early_declarations_without_source_type(tmp_path, unified):
    from app.run_schema_release import publication_run_sources
    root, path = legacy_database(tmp_path)
    if unified:
        migrate(root, path)
    with closing(sqlite3.connect(path)) as db:
        table = 'runs' if unified else 'agent_runs'
        assert 'source_type' not in {row[1] for row in db.execute(f'pragma table_info({table})')}
        agent_source, runtime_source = publication_run_sources(db)
        assert db.execute(f'select id,role from ({agent_source})').fetchall() == [(1, 'consumer')]
        assert db.execute(f'select id,agent_run_id from ({runtime_source})').fetchall() == [(1, 1)]


def test_core_release_inverse_restores_exact_source_and_keeps_new_receipt(tmp_path):
    root,path=legacy_database(tmp_path)
    before=source_fingerprint(path)
    publication=prepare_store_schema_release(root,path)
    publication.migrate=migrate
    publication.publish()
    with closing(sqlite3.connect(path)) as db, db:
        insert_run(db,"agent_runs",dict(id=1000,legacy_key="1000",task_id=1,execution_generation="new",role="consumer",status="failed"))
        db.execute("insert into receipt(agent_run_id,body) values(1000,x'ff00')")
    publication.rollback()
    restored=source_fingerprint(path)
    assert restored['ddl']==before['ddl']
    assert restored['version']==before['version']
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select id,status,final_result_json from agent_runs where id=1000').fetchone()==(1000,'failed','')
        assert db.execute('select hex(body) from receipt where agent_run_id=1000').fetchone()[0]=='FF00'
        assert db.execute('pragma foreign_key_check').fetchall()==[]
        assert db.execute("select seq from sqlite_sequence where name='agent_runs'").fetchone()[0]>=1000
    publication.rollback()


def test_empty_core_inverse_restores_exact_sequences(tmp_path):
    root,path=legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db,db:
        for table in ['receipt','agent_runtime_attempts','agent_run_state_events','agent_runs']:
            db.execute('delete from '+table)
    before=source_fingerprint(path)
    publication=prepare_store_schema_release(root,path)
    publication.migrate=migrate
    publication.publish()
    publication.rollback()
    assert source_fingerprint(path)==before


def test_unknown_core_transition_is_rejected_before_mutation(tmp_path):
    root,path=legacy_database(tmp_path)
    (root/'app/store.py').write_text("STORE_SCHEMA_VERSION = 'unverified'\n")
    before=source_fingerprint(path)
    with pytest.raises(ValueError,match='verified inverse'):
        prepare_store_schema_release(root,path)
    assert source_fingerprint(path)==before


@pytest.mark.parametrize('failure', ['dependencies', 'verification', 'restart', 'health', 'loaded'])
def test_core_publication_failure_inverses_before_old_start_and_keeps_new_runs(tmp_path, failure):
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_repository_updater_publication import _State, _repo

    checkout, operation = _repo(tmp_path)
    root, path = legacy_database(tmp_path)
    source = source_fingerprint(path)
    publication = prepare_store_schema_release(root, path)
    publication.migrate = migrate
    events = []
    starts = 0
    state = _State()
    verify, inverse = publication.verify_loaded, publication.rollback

    def verify_loaded():
        if starts and failure == 'loaded':
            raise RuntimeError('loaded failure')
        verify()

    def rollback():
        events.append('inverse')
        inverse()

    publication.verify_loaded = verify_loaded
    publication.rollback = rollback

    def start():
        nonlocal starts
        starts += 1
        events.append('start')
        with closing(sqlite3.connect(path)) as db, db:
            version = db.execute('select value from service_state where key=?', (VERSION_KEY,)).fetchone()[0]
            if starts == 1 and failure not in ('dependencies', 'verification'):
                assert version == '2026-10-11.2'
                insert_run(db, 'agent_runs', dict(id=1000, legacy_key='1000', task_id=1, execution_generation='replacement', role='consumer', status='completed'))
                insert_run(db, 'agent_runtime_attempts', dict(id=1001, legacy_key='1001', parent_id=1000, workload_kind='agent_run', workload_key='1000', attempt_number=1, route_name='test', runtime_kind='codex_cli', credential_mode='local_oauth', model='test', status='completed'))
                db.execute("insert into receipt(agent_run_id,body) values(1000,x'ff00')")
            else:
                assert version == '2026-10-11.1'
                assert source_fingerprint(path)['ddl'] == source['ddl']
        if starts == 1 and failure == 'restart':
            raise RuntimeError('partial replacement start')

    def fail_at(stage):
        events.append(stage)
        if stage == failure:
            raise RuntimeError(stage + ' failure')

    updater = RepositoryUpdater(checkout, state, database_path=path,
        stop=lambda: events.append('stop'), restart=start,
        health=lambda: failure != 'health' or starts > 1,
        database_publication=lambda _backup: publication,
        dependency_sync=lambda: fail_at('dependencies'),
        verification=lambda: fail_at('verification'))
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events.index('inverse') < len(events) - 1
    assert events[-1] == 'start'
    assert (checkout / 'version').read_text() == 'old\n'
    assert json.loads(state.values['repository_upgrade_operation:v1'])['status'] == 'rolled_back'
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('pragma quick_check').fetchone()[0] == 'ok'
        assert db.execute('pragma foreign_key_check').fetchall() == []
        if failure in ('restart', 'health', 'loaded'):
            assert events.count('stop') == 2
            assert events.index('inverse') > events.index('stop', 1)
            assert db.execute('select id,status from agent_runs where id=1000').fetchone() == (1000,'completed')
            assert db.execute('select id,agent_run_id,workload_key from agent_runtime_attempts where id=1001').fetchone() == (1001,1000,'1000')
            assert db.execute('select hex(body) from receipt where agent_run_id=1000').fetchone()[0] == 'FF00'


def test_core_inverse_interruption_is_atomic(tmp_path, monkeypatch):
    import app.run_schema_release as release_module

    root, path = legacy_database(tmp_path)
    publication = prepare_store_schema_release(root, path)
    publication.migrate = migrate
    publication.publish()
    before = source_fingerprint(path)
    restore = release_module.restore_run_schema

    def fail_after_restore(db, captured):
        restore(db, captured)
        raise RuntimeError('inverse interrupted after child rebuild')

    monkeypatch.setattr(release_module, 'restore_run_schema', fail_after_restore)
    with pytest.raises(RuntimeError, match='inverse interrupted'):
        publication.rollback()
    assert source_fingerprint(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select count(*) from runs').fetchone()[0] == 3
        assert db.execute('select hex(body) from receipt').fetchone()[0] == '0010'
        assert db.execute('pragma foreign_key_check').fetchall() == []
