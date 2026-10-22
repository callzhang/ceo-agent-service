"""Exact typed Actions migration and current-data schema inverse."""
from pathlib import Path
import sqlite3
import pytest

from app import action_storage as actions
from app.business_legacy_fields import current_action_objects as historical_objects
from app.current_action_schema import current_reply_action_projection
from app.current_action_schema_release import prepare_current_action_schema_release, _layouts
from app.current_schema_manifest import schema_is_current
from app.email_store import EmailStore
from app.state_storage import update_state_rows

ROOT = Path(__file__).resolve().parents[1]


def source_database(path, variant):
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('drop table actions')
        for obj in sorted(historical_objects(variant), key=lambda obj: obj['type'] != 'table'):
            db.execute(obj['sql'])
        layouts = _layouts(variant)
        data = actions._ATTEMPT_DEFAULTS | dict(conversation_id='fixture',conversation_title='fixture',
            trigger_message_id='original:17',trigger_sender='fixture',trigger_text='original',action='reply',
            sensitivity_kind='none',send_status='sent',created_at='original-clock',updated_at='original-clock')
        if variant == 'actual_no_memory_fk':
            data |= dict(conversation_single_chat=1,trigger_create_time='source-time',
                         universal_execution_id='exec-17',universal_execution_scope_id='scope-17')
        payload = layouts['reply_attempts'].pack({k:data[k] for k in layouts['reply_attempts'].fields},scalar_codec=actions._encoded_native_sqlite_value)
        db.execute('insert into actions(id,_kind,legacy_key,payload_json) values(101,?,?,jsonb(?))',
                   (actions.ACTION_RECORD_TYPES.code('reply_attempts'),'17',payload))
        data = actions._MEMORY_DEFAULTS | dict(attempt_id=17,event_type='test',status='completed',
                                               created_at='memory-clock',updated_at='memory-clock')
        payload = layouts['memory_write_events'].pack({k:data[k] for k in layouts['memory_write_events'].fields},
                                                      scalar_codec=actions._encoded_native_sqlite_value)
        cols, args = ('',()) if variant == 'actual_no_memory_fk' else (',memory_attempt_ref',(101,))
        db.execute(f'insert into actions(id,_kind,legacy_key,payload_json{cols}) values(102,?,?,jsonb(?){",?" if args else ""})',
                   (actions.ACTION_RECORD_TYPES.code('memory_write_events'),'19',payload,*args))
        update_state_rows(db,'service_state',{'value':'2026-10-19.11','updated_at':'source-clock'},
                          where_sql='key=?',parameters=('store_schema_version',))


def dump(path):
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump())


def facts(db, projection):
    return tuple(tuple(row) for row in db.execute('select * from ('+projection+') order by id'))


@pytest.mark.parametrize('variant',actions.REPLY_VARIANTS)
def test_explicit_forward_current_writes_inverse_reforward(tmp_path,variant):
    path=tmp_path/'source.sqlite3';source_database(path,variant)
    with sqlite3.connect(path) as db:
        before = {kind:facts(db,actions.reply_action_projection(kind,variant)) for kind in actions.REPLY_ACTION_TYPES}
        water = tuple(db.execute('select rowid,name,seq from sqlite_sequence order by rowid'))
    release=prepare_current_action_schema_release(ROOT,path)
    release.publish()
    with sqlite3.connect(path) as db:
        assert schema_is_current(db)
        assert db.execute("select memory_attempt_ref from actions where id=102").fetchone()==(101,)
        for kind in actions.REPLY_ACTION_TYPES:
            projected = (current_reply_action_projection(kind) if kind == 'memory_write_events' or variant == 'actual_no_memory_fk'
                         else actions.reply_action_projection(kind,'fresh_memory_fk'))
            assert facts(db,projected)==before[kind]
        assert tuple(db.execute('select rowid,name,seq from sqlite_sequence order by rowid'))==water
        actions.insert_action(db,'reply_attempts',dict(id=18,conversation_id='new',conversation_title='new',
            trigger_message_id='new:18',trigger_sender='fixture',trigger_text='new',action='reply',
            sensitivity_kind='none',send_status='sent'))
        actions.insert_action(db,'memory_write_events',dict(id=20,attempt_id=18,event_type='test',status='completed'))
        current=facts(db,current_reply_action_projection('memory_write_events'))
    release.rollback()
    with sqlite3.connect(path) as db:
        assert facts(db,actions.reply_action_projection('memory_write_events',variant))==current
        assert db.execute('pragma foreign_key_check').fetchall()==[]
    release.publish()
    with sqlite3.connect(path) as db:
        assert schema_is_current(db)
        assert facts(db,current_reply_action_projection('memory_write_events'))==current


def test_unresolvable_public_memory_parent_fails_without_changes(tmp_path):
    path=tmp_path/'invalid.sqlite3';source_database(path,'actual_no_memory_fk')
    with sqlite3.connect(path) as db:
        pointer=_layouts('actual_no_memory_fk')['memory_write_events'].path('attempt_id')
        db.execute('update actions set payload_json=jsonb_set(payload_json,?,999) where id=102',(pointer,))
    before=dump(path);release=prepare_current_action_schema_release(ROOT,path)
    with pytest.raises(ValueError,match='cannot be resolved'):
        release.publish()
    assert dump(path)==before


def test_fresh_inverse_refuses_to_erase_new_retained_fact(tmp_path):
    path=tmp_path/'fresh.sqlite3';source_database(path,'fresh_memory_fk')
    release=prepare_current_action_schema_release(ROOT,path);release.publish()
    with sqlite3.connect(path) as db:
        actions.update_action(db,'reply_attempts',17,{'universal_execution_id':'new-owned-fact'})
    before=dump(path)
    with pytest.raises(ValueError,match='cannot represent'):
        release.rollback()
    assert dump(path)==before
