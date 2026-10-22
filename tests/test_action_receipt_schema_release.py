"""Paired receipt publication preserves current facts before restarting old code."""
from contextlib import closing
import sqlite3

import pytest

from app.action_receipt_schema_release import (
    prepare_action_receipt_schema_release, publication_receipt_sources,
)
from app.action_receipt_storage import migrate_action_receipt_schema
from app.action_storage import insert_action, action_schema_is_current
from tests.test_action_receipt_storage import source15_database
from tests.test_action_receipt_source import source_values


@pytest.fixture(autouse=True)
def _historical_text_action_dml(monkeypatch):
    """Run rollback-era writes against the source Store15 TEXT Actions layout."""
    from tests.historical_action_text_fixture import install

    install(monkeypatch)


SOURCE = ('2026-10-13.1', 'original marker time')


def source(path):
    with closing(source15_database(path, fresh=True)) as db:
        db.execute('create table service_state(key text primary key,value text,updated_at text)')
        db.execute("insert into service_state values('store_schema_version',?,?)", SOURCE)
        db.commit()


def publish(root, path):
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute('begin immediate')
        migrate_action_receipt_schema(db)
        db.execute("update service_state set value='2026-10-13.2' where key='store_schema_version'")
        db.commit()


def test_receipt_release_retains_new_facts_and_watermarks(tmp_path):
    path = tmp_path / 'receipt.db'
    source(path)
    release = prepare_action_receipt_schema_release(tmp_path, path, SOURCE)
    release.migrate = publish
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        insert_action(db, 'agent_execution_receipts', source_values('agent_execution_receipts', token='new'))
        insert_action(db, 'external_action_results', source_values('external_action_results', token='new'))
        db.commit()
    release.rollback()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select value,updated_at from service_state").fetchone() == SOURCE
        assert db.execute("select external_action_key from external_action_results where external_action_key='new'").fetchone() == ('new',)
        assert db.execute("select seq from sqlite_sequence where name='agent_execution_receipts'").fetchone() == (116,)
        assert not db.execute('pragma foreign_key_check').fetchall()


def test_receipt_publication_sources_follow_exact_marker(tmp_path):
    path = tmp_path / 'receipt.db'
    source(path)
    with closing(sqlite3.connect(path)) as db:
        assert len(publication_receipt_sources(db)) == 3
    publish(tmp_path, path)
    with closing(sqlite3.connect(path)) as db:
        assert len(publication_receipt_sources(db)) == 3
        db.execute("update service_state set value='unknown'")
        with pytest.raises(ValueError, match='source'):
            publication_receipt_sources(db)


@pytest.mark.parametrize('failure', ['dependencies', 'verification', 'restart', 'health', 'loaded'])
def test_failed_publication_inverts_before_old_start(tmp_path, failure):
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_repository_updater_publication import _State, _repo

    checkout, operation = _repo(tmp_path)
    path = tmp_path / 'receipt.db'
    source(path)
    release = prepare_action_receipt_schema_release(tmp_path, path, SOURCE)
    release.migrate = publish
    original_verify, original_inverse = release.verify_loaded, release.rollback
    starts, events = 0, []

    def verify():
        if starts and failure == 'loaded':
            raise RuntimeError('loaded failure')
        original_verify()

    def inverse():
        events.append('inverse')
        original_inverse()

    release.verify_loaded, release.rollback = verify, inverse

    def start():
        nonlocal starts
        starts += 1
        events.append('start')
        with closing(sqlite3.connect(path)) as db:
            marker = db.execute("select value from service_state").fetchone()[0]
            if starts == 1 and failure not in ('dependencies', 'verification'):
                assert marker == '2026-10-13.2'
                insert_action(db, 'external_action_results', source_values('external_action_results', token='replacement'))
                insert_action(db, 'agent_execution_receipts', source_values('agent_execution_receipts', token='replacement'))
                db.commit()
            else:
                assert marker == SOURCE[0]
                assert action_schema_is_current(db, include_receipts=False)
                assert db.execute("select count(*) from agent_execution_receipts").fetchone()[0] >= 1
        if starts == 1 and failure == 'restart':
            raise RuntimeError('restart failure')

    def fail_at(stage):
        if stage == failure:
            raise RuntimeError(stage)

    updater = RepositoryUpdater(
        checkout, _State(), database_path=path, stop=lambda: events.append('stop'),
        restart=start, health=lambda: failure != 'health' or starts > 1,
        database_publication=lambda _backup: release,
        dependency_sync=lambda: fail_at('dependencies'), verification=lambda: fail_at('verification'),
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events.index('inverse') < len(events) - 1
    assert events[-1] == 'start'
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select value,updated_at from service_state").fetchone() == SOURCE
        assert not db.execute('pragma foreign_key_check').fetchall()
        if failure not in ('dependencies', 'verification'):
            assert db.execute("select external_action_key from external_action_results where external_action_key='replacement'").fetchone() == ('replacement',)
            assert db.execute("select operation_id from agent_execution_receipts where operation_id='replacement'").fetchone() == ('replacement',)
