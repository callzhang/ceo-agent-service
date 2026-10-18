from contextlib import closing
import sqlite3

import pytest

from test_action_storage import source_database


def _source(path):
    db = source_database(path)
    db.execute("create table service_state (key text primary key,value text not null,updated_at text not null default current_timestamp)")
    db.execute("insert into service_state values('store_schema_version','2026-10-12.1','original marker time')")
    db.commit()
    db.close()


def _publish(root, path):
    from app.action_storage import migrate_action_schema

    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("begin immediate")
        migrate_action_schema(db)
        db.execute("update service_state set value='2026-10-13.1' where key='store_schema_version'")
        db.commit()


def test_actions_release_inverse_retains_current_facts_and_original_marker(tmp_path):
    from app.action_schema_release import prepare_action_schema_release
    from app.action_storage import insert_action, update_action

    path = tmp_path / "release.db"
    _source(path)
    release = prepare_action_schema_release(tmp_path, path, ("2026-10-12.1", "original marker time"))
    release.migrate = _publish
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        new = insert_action(db, "candidate_supplements", {"candidate_id": 7, "instruction": "post-publication fact"})
        update_action(db, "candidate_executions", 1, {"lease_owner": "post-publication worker"})
        db.commit()
    release.rollback()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == ("2026-10-12.1", "original marker time")
        assert db.execute("select instruction from candidate_supplements where id=?", (new.lastrowid,)).fetchone() == ("post-publication fact",)
        assert db.execute("select lease_owner from candidate_executions where id=1").fetchone() == ("post-publication worker",)
        assert not db.execute("pragma foreign_key_check").fetchall()


def test_actions_release_rejects_unsupported_or_changed_source_marker(tmp_path):
    from app.action_schema_release import prepare_action_schema_release

    path = tmp_path / "release.db"
    _source(path)
    with pytest.raises(ValueError, match="source|transition"):
        prepare_action_schema_release(tmp_path, path, ("unknown", "original marker time"))
    with pytest.raises(ValueError, match="source|marker"):
        prepare_action_schema_release(tmp_path, path, ("2026-10-12.1", "different marker time"))


def test_actions_publication_reads_exact_source_or_target_without_recreating_tables(tmp_path):
    from app.action_schema_release import publication_candidate_source

    path = tmp_path / "publication.db"
    _source(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(f"select count(*) from ({publication_candidate_source(db)})").fetchone()[0] == 2
    _publish(tmp_path, path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(f"select count(*) from ({publication_candidate_source(db)})").fetchone()[0] == 2
        db.execute("update service_state set value='unknown' where key='store_schema_version'")
        with pytest.raises(ValueError, match="source"):
            publication_candidate_source(db)


@pytest.mark.parametrize('failure', ['dependencies', 'verification', 'restart', 'health', 'loaded'])
def test_actions_publication_failure_restores_schema_before_old_start_and_keeps_receipts(tmp_path, failure):
    import json
    from app.action_schema_release import prepare_action_schema_release
    from app.action_storage import insert_action, update_action
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_repository_updater_publication import _State, _repo

    checkout, operation = _repo(tmp_path)
    path = tmp_path / 'actions.db'
    _source(path)
    release = prepare_action_schema_release(tmp_path, path, ('2026-10-12.1', 'original marker time'))
    release.migrate = _publish
    events, written = [], {}
    state = _State()
    starts = 0
    verify, inverse = release.verify_loaded, release.rollback

    def verify_loaded():
        if starts and failure == 'loaded':
            raise RuntimeError('loaded failure')
        verify()

    def rollback():
        events.append('inverse')
        inverse()

    release.verify_loaded = verify_loaded
    release.rollback = rollback

    def start():
        nonlocal starts
        starts += 1
        events.append('start')
        with closing(sqlite3.connect(path)) as db:
            marker = db.execute("select value from service_state where key='store_schema_version'").fetchone()[0]
            if starts == 1 and failure not in ('dependencies', 'verification'):
                assert marker == '2026-10-13.1'
                written['supplement'] = insert_action(db, 'candidate_supplements', {
                    'candidate_id': 7, 'instruction': 'replacement business fact',
                }).lastrowid
                update_action(db, 'candidate_executions', 1, {'lease_owner': 'replacement worker'})
                db.execute("insert into external_action_results values('replacement receipt','business','identity','operation','{}',' {\"provider_receipt\": 1} ','digest',1,'new receipt time')")
                written['receipt'] = db.execute("select * from external_action_results where external_action_key='replacement receipt'").fetchone()
                db.commit()
            else:
                assert marker == '2026-10-12.1'
                assert db.execute("select name from sqlite_master where name='actions'").fetchone() is None
                assert db.execute('select count(*) from review_candidates').fetchone()[0] == 2
        if starts == 1 and failure == 'restart':
            raise RuntimeError('partial replacement start')

    def fail_at(stage):
        events.append(stage)
        if stage == failure:
            raise RuntimeError(stage + ' failure')

    updater = RepositoryUpdater(
        checkout, state, database_path=path,
        stop=lambda: events.append('stop'), restart=start,
        health=lambda: failure != 'health' or starts > 1,
        database_publication=lambda _backup: release,
        dependency_sync=lambda: fail_at('dependencies'),
        verification=lambda: fail_at('verification'),
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events[-1] == 'start'
    assert events.index('inverse') < len(events) - 1
    assert (checkout / 'version').read_text() == 'old\n'
    assert json.loads(state.values['repository_upgrade_operation:v1'])['status'] == 'rolled_back'
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('pragma quick_check').fetchone()[0] == 'ok'
        assert not db.execute('pragma foreign_key_check').fetchall()
        assert db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == ('2026-10-12.1', 'original marker time')
        if written:
            assert events.count('stop') == 2
            assert events.index('inverse') > events.index('stop', 1)
            assert db.execute('select instruction from candidate_supplements where id=?', (written['supplement'],)).fetchone() == ('replacement business fact',)
            assert db.execute('select lease_owner from candidate_executions where id=1').fetchone() == ('replacement worker',)
            assert db.execute("select * from external_action_results where external_action_key='replacement receipt'").fetchone() == written['receipt']


@pytest.mark.parametrize('statement', [
    'create view actions as select 1 as id',
    'create view review_candidates as select 1 as id',
    'create index actions on unrelated(value)',
    'create trigger candidate_reviews after insert on unrelated begin select 1; end',
    'create index idx_actions_public_id on unrelated(value)',
    'create trigger trg_actions_record_type_immutable after insert on unrelated begin select 1; end',
    'create view Actions as select 1 as id',
    'create index IDX_actions_PUBLIC_id on unrelated(value)',
])
def test_store_bootstrap_rejects_action_name_collisions_before_schema_changes(tmp_path, statement):
    from app.store import AutoReplyStore

    path = tmp_path / 'collision.db'
    with closing(sqlite3.connect(path)) as db:
        db.executescript("create table unrelated(value text);insert into unrelated values('retained original');")
        db.execute(statement)
        db.commit()
        before = db.execute('select type,name,tbl_name,sql from sqlite_master order by type,name').fetchall()
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select type,name,tbl_name,sql from sqlite_master order by type,name').fetchall() == before
        assert db.execute('select * from unrelated').fetchall() == [('retained original',)]


def test_store_bootstrap_preserves_unrelated_unicode_schema_name(tmp_path):
    from app.store import AutoReplyStore

    path = tmp_path / 'unicode.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('create view "actionſ" as select 17 as retained')
        db.commit()
    store = AutoReplyStore(path)
    with store._connect() as db:
        assert db.execute('select * from "actionſ"').fetchone()[0] == 17
