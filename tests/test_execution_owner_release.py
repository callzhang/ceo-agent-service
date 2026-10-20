"""The existing coordinator publishes A3 and inverses current facts on failure."""

from pathlib import Path
import sqlite3

import pytest

from app import action_storage as actions
from app.email_store import EmailStore
from app.execution_owner_schema import restore_execution_owner
from app.state_storage import update_state_rows
from app.store import AutoReplyStore
from app.store_schema_release import prepare_database_schema_release
from tests.test_result_schema_release import _markers
from tests.test_reviewed_candidate_store import _reviewed

ROOT = Path(__file__).resolve().parents[1]


def _source(tmp_path):
    path = tmp_path / 'release.sqlite3'
    store = AutoReplyStore(path)
    EmailStore(path)
    _, candidate, review, _ = _reviewed(store, options=False)
    with sqlite3.connect(path, isolation_level=None) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        restore_execution_owner(db, {'reply_variant': 'fresh_memory_fk'})
        update_state_rows(db, 'service_state', {'value': '2026-10-19.3', 'updated_at': b'old-clock'}, where_sql="key='store_schema_version'")
        db.commit()
    return path, candidate, review


def test_registered_publication_and_current_fact_inverse(tmp_path):
    path, candidate, review = _source(tmp_path)
    with sqlite3.connect(path) as db:
        original, schema = _markers(db), actions._objects(db, 'actions')
    release = prepare_database_schema_release(ROOT, path)
    assert type(release).__name__ == 'ExecutionOwnerSchemaRelease'
    release.publish()
    store = AutoReplyStore(path)
    EmailStore(path)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60)
    attempt = store.begin_candidate_action(execution['id'], 'owner', 0, 'new-key')
    store.record_candidate_action_outcome(execution['id'], 'owner', 0, 'uncertain', {'unknown': True})
    store.finish_candidate_execution(execution['id'], 'owner', 'uncertain', {'new': True})
    release.verify_loaded()
    release.finalize()
    with sqlite3.connect(path) as db:
        assert _markers(db)[1] == original[1]
        target_execution = db.execute(f'select * from ({actions.CANDIDATE_EXECUTION_SQL})').fetchall()
        waters = db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert _markers(db) == original
        assert actions._objects(db, 'actions') == schema
        assert db.execute('select * from (' + actions.ACTION_PROJECTIONS['candidate_executions'] + ')').fetchall() == target_execution
        assert db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall() == waters
        assert db.execute('select execution_id from (' + actions.ACTION_PROJECTIONS['candidate_action_attempts'] + ') where id=?', (attempt['id'],)).fetchone() == (execution['id'],)
        assert db.execute('pragma foreign_key_check').fetchall() == []
    release.rollback()
    release.publish()
    release.finalize()
    assert prepare_database_schema_release(ROOT, path) is None


@pytest.mark.parametrize('constructor', [AutoReplyStore, EmailStore])
def test_previous_and_mixed_sources_refuse_without_writes(tmp_path, constructor):
    path, _, _ = _source(tmp_path)
    before = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match='unsupported or mixed'):
        constructor(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        db.execute('begin immediate')
        update_state_rows(db, 'service_state', {'value': '2026-10-19.4'}, where_sql="key='store_schema_version'")
    before = path.read_bytes()
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        prepare_database_schema_release(ROOT, path)
    assert path.read_bytes() == before


def test_cold_failure_rolls_back_via_existing_release(tmp_path, monkeypatch):
    from app import store_schema_release
    from app.action_schema_release import publication_candidate_source
    from app.email_publication_sources import publication_email_action_source
    from app.repository_updater import in_flight_work
    path, _, _ = _source(tmp_path)
    with sqlite3.connect(path) as db:
        old = _markers(db)
        db.execute(publication_candidate_source(db)).fetchall()
        db.execute(publication_email_action_source(db)).fetchall()
    assert in_flight_work(path) >= 0
    release = prepare_database_schema_release(ROOT, path)
    def fail(root, path):
        assert in_flight_work(path) >= 0
        raise RuntimeError('cold failure')
    monkeypatch.setattr(store_schema_release, '_migrate_store', fail)
    with pytest.raises(RuntimeError, match='cold failure'):
        release.publish()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert _markers(db) == old

