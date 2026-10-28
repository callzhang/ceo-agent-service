from contextlib import closing
import sqlite3

import pytest

from app.feedback_owner_schema_release import prepare_feedback_owner_release, ROUND_NEUTRALS
from app.feedback_owner_schema_source import feedback_source_objects
from app.action_feedback_storage import insert_feedback_action
from app.task8_storage import register_task8_functions


def source(path):
    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        for kind in ('table','index','trigger'):
            for row in feedback_source_objects():
                if row[0] == kind:
                    db.execute(row[3])
        db.execute("insert into state(record_type,legacy_key,payload_json) values('service_state','1',?)",
                   ('{"key":"store_schema_version","value":"2026-10-28.2","updated_at":"source-clock"}',))
        db.execute("insert into email_schema_migrations values(54,'source-clock')")
        db.execute("insert into tasks(id,kind,status,parameters_json,created_at,updated_at) values(7,'feedback_processing','resolved',jsonb('{}'),'created','updated')")
        insert_feedback_action(db,'feedback_events', {'key':'source-a','feedback_token':'token','comment':'c','original_text':'o','resolved_at':'resolved-clock'})
        fields=','.join(ROUND_NEUTRALS)
        db.execute('insert into feedback_processing_items(task_id,feedback_key,batch_id,current_round_id,'+fields+',resolved_at) values(7,\'source-a\',\'\',0,'+','.join('?' for _ in ROUND_NEUTRALS)+',?)',(*ROUND_NEUTRALS.values(),'resolved-clock'))
        db.commit()


def test_no_round_resolution_survives_and_inverse_uses_new_exact_round(tmp_path):
    path=tmp_path/'feedback.sqlite3'
    source(path)
    release=prepare_feedback_owner_release(path)
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        assert [r[1] for r in db.execute('pragma table_info(feedback_processing_items)')] == ['task_id','feedback_key','batch_id','current_round_id']
        assert db.execute("select fb_event_resolved_at from actions where record_type='feedback_events'").fetchone()==('resolved-clock',)
        db.execute("insert into feedback_processing_rounds(id,feedback_key,batch_id,round_number,status,note) values(11,'source-a','first',1,'resolved','old note')")
        db.execute("insert into feedback_processing_rounds(id,feedback_key,batch_id,round_number,status,note) values(12,'source-a','second',2,'processing','later note')")
        db.execute("update feedback_processing_items set current_round_id=11,batch_id='first' where task_id=7")
        db.execute("insert into sqlite_sequence(rowid,name,seq) values(999,'current-water','3001')")
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select note,resolved_at,current_round_id from feedback_processing_items where task_id=7').fetchone()==('old note','resolved-clock',11)
        assert db.execute("select rowid,seq,typeof(seq) from sqlite_sequence where name='current-water'").fetchone()==(999,'3001','text')
    again=prepare_feedback_owner_release(path)
    again.publish()
    again.verify_loaded()


@pytest.mark.parametrize('mutation',[
    "update feedback_processing_items set note='orphan copy'",
    "update feedback_processing_items set resolved_at='different'",
    "update feedback_processing_items set current_round_id=999,batch_id='missing'",
    "delete from actions where record_type='feedback_events'",
])
def test_missing_or_conflicting_owner_rejected_without_source_mutation(tmp_path,mutation):
    path=tmp_path/'conflict.sqlite3'
    source(path)
    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        db.execute(mutation)
        db.commit()
        before=db.serialize()
    with pytest.raises(ValueError,match='Feedback current owner'):
        prepare_feedback_owner_release(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.serialize()==before


def test_mid_rebuild_failure_rolls_back_every_source_fact(tmp_path,monkeypatch):
    import app.feedback_owner_schema_release as module
    path=tmp_path/'interrupted.sqlite3'
    source(path)
    release=prepare_feedback_owner_release(path)
    with closing(sqlite3.connect(path)) as db:
        before=db.serialize()
    original=module._rebuild_items
    def interrupted(db, *, inverse):
        original(db,inverse=inverse)
        raise RuntimeError('interrupted')
    monkeypatch.setattr(module,'_rebuild_items',interrupted)
    with pytest.raises(RuntimeError,match='interrupted'):
        release.publish()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.serialize()==before


def test_frozen_source_and_target_catalogs_match_exact_published_declarations():
    import hashlib
    import json
    from app.feedback_owner_schema_source import feedback_source_objects
    from app.feedback_owner_schema_release import feedback_target_objects

    source_objects = feedback_source_objects()
    assert len(source_objects) == len(feedback_target_objects()) == 463
    for objects, expected in (
        (source_objects, '8fe0b0d99c39c9bcab5b6637ba188677038cd620a72c78e7daa720a8d0d36b2a'),
        (feedback_target_objects(), 'ec77e9b01dc0915a387ed8b01d8d28cd9f150d7addaed9262b3ecb9d2b129a0a'),
    ):
        assert hashlib.sha256(json.dumps(objects, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest() == expected


def test_release_and_new_writes_preserve_facts_through_inverse(tmp_path):
    from app.store import AutoReplyStore
    from tools.validate_feedback_owner_copy import fact_digest

    path = tmp_path / 'source-independent.sqlite3'
    source(path)
    before = fact_digest(path)
    release = prepare_feedback_owner_release(path)
    release.publish()
    assert fact_digest(path) == before
    # The current Store is one release later; stack it to open the Store, then peel it off.
    from app.wechat_voice_transcript_schema_release import prepare_wechat_voice_transcript_release
    voice = prepare_wechat_voice_transcript_release(path)
    voice.publish()
    store = AutoReplyStore(path)
    store.upsert_feedback_event(key='new-fact', feedback_token='token', comment='new', original_text='new')
    voice.rollback()
    after_write = fact_digest(path)
    release.rollback()
    assert fact_digest(path) == after_write
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select count(*) from actions where record_type='feedback_events' and fb_event_key='new-fact'").fetchone() == (1,)
    prepare_feedback_owner_release(path).publish()
    assert fact_digest(path) == after_write


def test_deployment_ledger_survives_release_and_inverse(tmp_path):
    from app.repository_updater import ExistingSchemaUpgradeStateStore

    path = tmp_path / "ledger.sqlite3"
    source(path)
    state = ExistingSchemaUpgradeStateStore(path)
    state.bind_committed_schema()
    state.set_service_state("retirement-ledger-fixture", "updating")
    release = prepare_feedback_owner_release(path)
    release.publish()
    state.bind_committed_schema()
    assert state.get_service_state("retirement-ledger-fixture") == "updating"
    state.set_service_state("retirement-ledger-fixture", "restarting")
    release.rollback()
    state.bind_committed_schema()
    assert state.get_service_state("retirement-ledger-fixture") == "restarting"
