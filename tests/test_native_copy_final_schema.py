from pathlib import Path
from app.store import AutoReplyStore
from app.store import STORE_SCHEMA_VERSION
from app.email_store import EMAIL_SCHEMA_VERSION, EmailStore
from scripts.validate_native_copy_lifecycle import _publish_email_pair


def test_native_harness_accepts_verified_final_input_without_remigration(tmp_path):
    path = tmp_path / 'final.sqlite3'
    AutoReplyStore(path)
    EmailStore(path)
    result = _publish_email_pair(Path(__file__).resolve().parents[1], path)
    assert result['store'] == STORE_SCHEMA_VERSION
    assert result['email'] == EMAIL_SCHEMA_VERSION
    assert result['core_tables'] == 10
    assert result['source_body_table_present'] is False
    assert result['action_types'] > 29


def test_settlement_fault_injection_follows_final_payload_write(tmp_path):
    import sqlite3
    import pytest
    from scripts.validate_native_copy_lifecycle import _install_settlement_interrupt
    store = AutoReplyStore(tmp_path / 'settlement.sqlite3')
    task = store.ensure_reply_task(conversation_id='copy', conversation_title='copy',
        single_chat=True, trigger_message_id='test', trigger_sender='operator',
        trigger_create_time='', trigger_text='synthetic input')
    _install_settlement_interrupt(store, task.id)
    with store._connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match='native copy settlement interrupted'):
            db.execute("update tasks set status='done' where id=?", (task.id,))
        db.execute('drop trigger native_copy_interrupt')
    assert AutoReplyStore(store.path).get_reply_task(task.id).status == 'pending'
