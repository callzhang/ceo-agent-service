"""Exact Signal owner release and inverse with facts written after publication."""
from contextlib import closing
from dataclasses import replace
import hashlib
import sqlite3

import pytest

from app import action_storage
from app.provenance_definitions import SOURCE_FIELDS, SIGNAL_FIELDS
from app.storage_payload import ACTION_RECORD_TYPES, PayloadLayout
from app.state_storage import insert_state
from app.task8_storage import register_task8_functions


def binding(seed='a'):
    from app.business_source_reader import SourceVersionBinding
    return SourceVersionBinding(
        source_type='dingtalk_chat', source_ref='ref-' + seed,
        source_time='clock', conversation_id='conversation', author_user_id='author',
        author_name='Name', author_kind='human', identity_key=hashlib.sha256(seed.encode()).hexdigest(),
        body_sha256=hashlib.sha256(('body-' + seed).encode()).hexdigest(), body_utf8_bytes=12,
    )


def old_signal(db, source, public_id, *, parent_id=None):
    values = dict.fromkeys(SIGNAL_FIELDS)
    if parent_id is None:
        values.update({field: getattr(source, field) for field in SOURCE_FIELDS})
    values.update(provenance_id=parent_id, conversation_title='Title', context_json='{"fact":"α"}',
                  dedupe_key='signal-' + str(public_id), created_at='original-clock')
    db.execute('insert into actions(id,_kind,legacy_key,payload_json) values(?,?,?,jsonb(?))', (
        1000 + public_id, ACTION_RECORD_TYPES.code('business_task_signals'), str(public_id),
        PayloadLayout(SIGNAL_FIELDS).pack(values, scalar_codec=action_storage._encoded_native_sqlite_value)))


def source(path):
    from app.signal_source_owner_schema_release import source_objects, SOURCE_STORE_VERSION
    with closing(sqlite3.connect(path)) as db, db:
        register_task8_functions(db)
        objects = source_objects()
        for category in ('table', 'index', 'trigger'):
            for kind, _name, _owner, sql in objects:
                if kind == category:
                    db.execute(sql)
        db.execute('begin')
        db.execute("insert into email_schema_migrations values(54,'email-clock')")
        insert_state(db, 'service_state', dict(key='store_schema_version', value=SOURCE_STORE_VERSION, updated_at='source-clock'))
        old_signal(db, binding(), 7)
        parent = db.execute('insert into provenance(' + ','.join(SOURCE_FIELDS) + ') values(' + ','.join('?' for _ in SOURCE_FIELDS) + ')',
                            tuple(getattr(binding('b'), name) for name in SOURCE_FIELDS)).lastrowid
        old_signal(db, binding('b'), 9, parent_id=parent)
        old_signal(db, binding('b'), 10, parent_id=parent)


def facts(path, *, old=False):
    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        if old:
            columns = ','.join(f'coalesce(p.{name},a.ba_task_signals_{name})' for name in SOURCE_FIELDS)
        else:
            columns = ','.join('p.' + name for name in SOURCE_FIELDS)
        return db.execute('select a.id,a.legacy_key,' + columns + ',a.ba_task_signals_conversation_title,a.ba_task_signals_context_json,a.ba_task_signals_dedupe_key,a.ba_task_signals_created_at from actions a left join provenance p on p.id=a.ba_task_signals_provenance_id where a.record_type=\'business_task_signals\' order by a.id').fetchall()


def test_frozen_source_digest():
    import json
    from app.signal_source_owner_schema_release import source_objects
    data = json.dumps(source_objects(), ensure_ascii=False, separators=(',', ':')).encode()
    assert hashlib.sha256(data).hexdigest() == '7b4c91875faf9cf1bc57f912fc692b7c2a3966a03b9b2e384b2d8470ce0c832b'
    from app.signal_source_owner_schema_release import target_objects
    target = target_objects()
    assert len(target) == 476
    assert hashlib.sha256(json.dumps(target, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest() == '234e17843afc171b4fa8cb42f3e1d959ef2ba415f43e295dc7db640ab3d02221'


def test_forward_new_signals_current_data_inverse_reforward(tmp_path):
    from app.signal_source_owner_schema_release import prepare_signal_source_owner_release, source_objects
    from app.current_schema_manifest import schema_objects
    from app.provenance_storage import insert_signal_version
    from app.action_entity_storage import create_action, append_action_receipt
    path = tmp_path / 'signals.sqlite3'
    source(path)
    before = facts(path, old=True)
    release = prepare_signal_source_owner_release(path)
    release.publish()
    release.verify_loaded()
    assert facts(path) == before
    with closing(sqlite3.connect(path)) as db, db:
        register_task8_functions(db)
        db.execute('pragma foreign_keys=on')
        db.execute('begin')
        insert_signal_version(db, binding('c'), dedupe_key='new-one')
        insert_signal_version(db, binding('d'), dedupe_key='new-shared-one')
        insert_signal_version(db, binding('d'), dedupe_key='new-shared-two')
        action = create_action(db, values={'operation': 'test-only', 'status': 'done'})
        append_action_receipt(db, action['id'], values={'provider_result_json': '{"substitute":true}'})
        receipts = db.execute('select * from action_receipts').fetchall()
        sequences = db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall()
    current = facts(path)
    release.rollback()
    assert facts(path, old=True) == current
    with closing(sqlite3.connect(path)) as db, db:
        register_task8_functions(db)
        assert schema_objects(db) == source_objects()
        assert db.execute("select value,updated_at from state where key='store_schema_version'").fetchone() == ('2026-10-25.2', 'source-clock')
        assert db.execute('select * from action_receipts').fetchall() == receipts
        assert db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall() == sequences
        assert db.execute('pragma foreign_key_check').fetchall() == []
        old_signal(db, binding('e'), 25)
    old_current = facts(path, old=True)
    release = prepare_signal_source_owner_release(path)
    release.publish()
    release.finalize()
    from app.scheduled_task_notification_schema_source import previous_current_objects
    with closing(sqlite3.connect(path)) as db:
        assert tuple(tuple(row) for row in db.execute("select type,name,tbl_name,sql from sqlite_schema where name not glob 'sqlite_*' order by type,name")) == previous_current_objects(email_ledger=True)
    assert facts(path) == old_current


@pytest.mark.parametrize('damage', ['marker', 'email', 'index'])
def test_nonexact_source_is_rejected_without_mutation(tmp_path, damage):
    from app.signal_source_owner_schema_release import prepare_signal_source_owner_release
    from app.state_storage import update_state_rows
    path = tmp_path / 'damaged.sqlite3'
    source(path)
    with closing(sqlite3.connect(path)) as db, db:
        if damage == 'marker':
            db.execute('begin')
            update_state_rows(db, 'service_state', {'value': 'wrong'}, where_sql='key=?', parameters=('store_schema_version',))
        elif damage == 'email':
            db.execute('update email_schema_migrations set version=99')
        else:
            db.execute('drop index idx_actions_signal_inline_source')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='unsupported'):
        prepare_signal_source_owner_release(path)
    assert path.read_bytes() == before


def test_mid_conversion_error_rolls_back_source_and_parent_insertions(tmp_path, monkeypatch):
    from app import signal_source_owner_schema_release as migration
    from app.current_schema_manifest import schema_objects
    path = tmp_path / 'atomic.sqlite3'
    source(path)
    before = facts(path, old=True)
    with closing(sqlite3.connect(path)) as db:
        parents = db.execute('select * from provenance order by id').fetchall()
        sequences = db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall()
    release = migration.prepare_signal_source_owner_release(path)
    rebuild = migration._rebuild

    def fail_after_rebuild(db, *, inverse):
        rebuild(db, inverse=inverse)
        raise RuntimeError('injected-after-actions-rebuild')

    monkeypatch.setattr(migration, '_rebuild', fail_after_rebuild)
    with pytest.raises(RuntimeError, match='injected-after-actions'):
        release.publish()
    assert not release.published
    assert facts(path, old=True) == before
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == migration.source_objects()
        assert db.execute('select * from provenance order by id').fetchall() == parents
        assert db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall() == sequences


def test_colliding_inline_and_parent_identity_is_not_guessed(tmp_path):
    from app.signal_source_owner_schema_release import prepare_signal_source_owner_release, source_objects
    from app.current_schema_manifest import schema_objects
    path = tmp_path / 'ambiguous.sqlite3'
    source(path)
    with closing(sqlite3.connect(path)) as db, db:
        register_task8_functions(db)
        old_signal(db, replace(binding('b'), author_name='Conflicting'), 30)
    before = facts(path, old=True)
    release = prepare_signal_source_owner_release(path)
    with pytest.raises(ValueError, match='collides'):
        release.publish()
    assert facts(path, old=True) == before
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == source_objects()
