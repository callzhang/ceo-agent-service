import sqlite3

import pytest

from app.conversation_session_schema_release import prepare_conversation_session_release
from app.task8_storage import register_task8_functions
from tests.support.store28_session_source import create_store28_1_source as source


def conversation(db, identity, *, session='session-a', mirror_hash='', canonical_hash='hash-a'):
    import json
    db.execute("insert into state(id,record_type,legacy_key,payload_json) values(?,'conversations',?,?)",
               (identity, str(identity), json.dumps({'conversation_id': 'chat-a', 'title': 'Chat',
                'single_chat': 1, 'codex_session_id': session, 'codex_session_contract_hash': mirror_hash})))
    db.execute("insert into state(id,record_type,legacy_key,payload_json) values(?,'conversation_runtime_sessions',?,?)",
               (identity+1, str(identity+1), json.dumps({'conversation_id': 'chat-a', 'route_name': 'codex_oauth',
                'session_id': 'session-a', 'contract_hash': canonical_hash, 'updated_at': 'canonical-clock'})))
    db.commit()


def test_current_session_facts_and_new_metadata_survive_inverse_and_reforward(tmp_path):
    path = tmp_path/'sessions.sqlite3'
    with sqlite3.connect(path) as db:
        source(db)
        conversation(db, 10)
        canonical = db.execute("select id,payload_json from state where record_type='conversation_runtime_sessions'").fetchone()
    release = prepare_conversation_session_release(path)
    release.publish()
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        assert 'codex_session_id' not in {r[1] for r in db.execute('pragma table_xinfo(state)')}
        assert db.execute("select json_type(payload_json,'$.codex_session_id') from state where id=10").fetchone() == (None,)
        assert db.execute("select id,payload_json from state where record_type='conversation_runtime_sessions'").fetchone() == canonical
        db.execute("update state set payload_json=json_set(payload_json,'$.title','Changed') where id=10")
        db.execute("update state set payload_json=json_set(payload_json,'$.session_id','session-new','$.contract_hash','hash-new') where id=11")
    release.rollback()
    with sqlite3.connect(path) as db:
        row=db.execute('select title,codex_session_id,codex_session_contract_hash from state where id=10').fetchone()
        assert row == ('Changed','session-new','hash-new')
        assert db.execute('pragma foreign_key_check').fetchall() == []
    second=prepare_conversation_session_release(path)
    second.publish()
    second.verify_loaded()


@pytest.mark.parametrize('session,mirror_hash', [('different',''), ('session-a','conflicting')])
def test_conflicting_mirror_is_rejected_without_mutation(tmp_path, session, mirror_hash):
    path=tmp_path/'conflict.sqlite3'
    with sqlite3.connect(path) as db:
        source(db)
        conversation(db,10,session=session,mirror_hash=mirror_hash)
        before=db.serialize()
    with pytest.raises(ValueError,match='session owner'):
        prepare_conversation_session_release(path)
    with sqlite3.connect(path) as db:
        assert db.serialize() == before


def test_missing_canonical_owner_is_rejected_without_mutation(tmp_path):
    path=tmp_path/'missing.sqlite3'
    with sqlite3.connect(path) as db:
        source(db)
        conversation(db,10)
        db.execute("delete from state where record_type='conversation_runtime_sessions'")
    with pytest.raises(ValueError,match='session owner'):
        prepare_conversation_session_release(path)
    with sqlite3.connect(path) as db:
        assert db.execute('select codex_session_id from state where id=10').fetchone() == ('session-a',)


def test_state_codec_values_and_canonical_only_records_survive_cycles(tmp_path):
    from app.action_storage import _native_payload

    path=tmp_path/'typed.sqlite3'
    with sqlite3.connect(path) as db:
        source(db)
        conversation(db,10)
        db.execute('update state set payload_json=? where id=10', (_native_payload({
            'conversation_id':'chat-a','title':b'\x00\xff','single_chat':1,
            'codex_session_id':'session-a','codex_session_contract_hash':''}),))
        db.execute("insert into sqlite_sequence(rowid,name,seq) values(2001,'custom-water','3001')")
    release=prepare_conversation_session_release(path)
    release.publish()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert db.execute('select title,typeof(title) from state where id=10').fetchone() == (b'\x00\xff','blob')
        assert db.execute("select rowid,seq,typeof(seq) from sqlite_sequence where name='custom-water'").fetchone() == (2001,'3001','text')


def test_current_constructor_rejects_previous_source_without_conversion(tmp_path):
    from app.store import AutoReplyStore

    path=tmp_path/'old-runtime.sqlite3'
    with sqlite3.connect(path) as db:
        source(db)
        before=db.serialize()
    with pytest.raises(sqlite3.IntegrityError,match='explicit paired schema release required'):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.serialize() == before


def test_failure_during_state_rebuild_restores_source(tmp_path,monkeypatch):
    import app.conversation_session_schema_release as module
    path=tmp_path/'interrupted.sqlite3'
    with sqlite3.connect(path) as db:
        source(db)
        conversation(db,10)
        before=db.serialize()
    release=prepare_conversation_session_release(path)
    original=module._rebuild_state
    def interrupted(db, *, inverse):
        original(db,inverse=inverse)
        raise RuntimeError('interrupted')
    monkeypatch.setattr(module,'_rebuild_state',interrupted)
    with pytest.raises(RuntimeError,match='interrupted'):
        release.publish()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert db.serialize() == before
