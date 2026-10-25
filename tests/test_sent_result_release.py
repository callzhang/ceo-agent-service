"""Existing paired release protocol, current-fact inverse and exact source refusal."""

from pathlib import Path
import sqlite3

import pytest

from app import action_storage as actions
from app.email_store import EmailStore
from app.sent_result_schema import restore_sent_result_references
from app.state_storage import update_state_rows
from app.store import AutoReplyStore
from app.store_schema_release import prepare_database_schema_release
from tests.test_result_schema_release import _markers
from tests.test_agent_turn_store import _claim_consumer, _task

ROOT = Path(__file__).resolve().parents[1]


def _source(tmp_path):
    path = tmp_path / 'release.sqlite3'
    store = AutoReplyStore(path)
    EmailStore(path)
    task = _task(store)
    run = _claim_consumer(store, task).run
    store.complete_agent_run(run.id, {'outcome': 'proposal'}, owner='consumer')
    values = dict(agent_run_id=run.id, external_action_key='before', business_object_key=task.business_object_key,
        action_identity='before', operation='send', target_identifiers={'conversation_id': task.conversation_id},
        conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
        reply_text='Sent', provider_result={'receipt': 'before'})
    store.record_completed_agent_message_delivery(**values)
    with sqlite3.connect(path, isolation_level=None) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        restore_sent_result_references(db, {'reply_variant': 'fresh_memory_fk'})
        update_state_rows(db, 'service_state', {'value': '2026-10-19.4', 'updated_at': b'old-clock'}, where_sql="key='store_schema_version'")
        db.commit()
    return path, values


def test_registered_publication_inverse_current_new_facts_and_repeat(tmp_path):
    path, values = _source(tmp_path)
    with sqlite3.connect(path) as db:
        original, schema = _markers(db), actions._objects(db, 'actions')
    release = prepare_database_schema_release(ROOT, path)
    assert type(release).__name__ == 'SentResultSchemaRelease'
    release.publish()
    store = AutoReplyStore(path)
    EmailStore(path)
    sent = store.record_completed_agent_message_delivery(**(values | {'external_action_key': 'after', 'action_identity': 'after', 'provider_result': {'receipt': 'after'}}))
    store.update_sent_reply_recall(sent.id, recall_status='failed', recall_error='current error')
    release.verify_loaded()
    release.finalize()
    with sqlite3.connect(path) as db:
        current = db.execute(actions.SENT_REPLY_SQL + ' order by id').fetchall()
        ids = db.execute("select id,legacy_key from actions where record_type='sent_replies' order by id").fetchall()
        waters = db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert _markers(db) == original
        assert actions._objects(db, 'actions') == schema
        assert db.execute(actions.ACTION_PROJECTIONS['sent_replies'] + ' order by id').fetchall() == current
        assert db.execute("select id,legacy_key from actions where record_type='sent_replies' order by id").fetchall() == ids
        assert db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall() == waters
        assert db.execute('pragma foreign_key_check').fetchall() == []
    release.rollback()
    release.publish()
    release.finalize()
    assert prepare_database_schema_release(ROOT, path) is None


@pytest.mark.parametrize('constructor', [AutoReplyStore, EmailStore])
def test_previous_and_mixed_sources_refuse_without_writes(tmp_path, constructor):
    path, _ = _source(tmp_path)
    before = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match='unsupported or mixed'):
        constructor(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        db.execute('begin immediate')
        update_state_rows(db, 'service_state', {'value': '2026-10-19.5'}, where_sql="key='store_schema_version'")
    before = path.read_bytes()
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        prepare_database_schema_release(ROOT, path)
    assert path.read_bytes() == before


