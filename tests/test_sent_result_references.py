"""Sent owns only exceptional bodies; exact provider bytes have one owner."""

import inspect

import pytest

from app.store import AutoReplyStore
from tests.test_agent_turn_store import _claim_consumer, _task


def test_completed_delivery_keeps_one_provider_body_and_public_projection(tmp_path):
    store = AutoReplyStore(tmp_path / 'sent.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    store.complete_agent_run(run.id, {'outcome': 'proposal'}, owner='consumer')
    sent = store.record_completed_agent_message_delivery(
        agent_run_id=run.id, external_action_key='key', business_object_key=task.business_object_key,
        action_identity='reply', operation='message.send',
        target_identifiers={'conversation_id': task.conversation_id},
        conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
        reply_text='Delivered', provider_result={'receipt': 'substitute'})
    with store._connect() as connection:
        assert "send_result_json" not in {row[1] for row in connection.execute("pragma table_xinfo(actions)")}
        assert connection.execute("select count(*) from action_receipts where provider_result_json=?", ('{"receipt":"substitute"}',)).fetchone()[0] == 1
        assert connection.execute('pragma foreign_key_check').fetchall() == []
    assert sent.send_result_json == '{"receipt":"substitute"}'
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) == sent


def test_unused_writers_and_finalize_sent_parameters_are_retired():
    assert not hasattr(AutoReplyStore, 'record_agent_message_delivery')
    assert not hasattr(AutoReplyStore, 'record_reply_attempt_for_trigger')
    parameters = inspect.signature(AutoReplyStore.finalize_orchestrated_reply_task).parameters
    assert 'sent_reply_text' not in parameters
    assert 'sent_reply_result_json' not in parameters


@pytest.mark.parametrize('legacy', [False, True])
def test_existing_native_readback_repairs_explicit_sent_result_without_resending(tmp_path, monkeypatch, legacy):
    from app.dingtalk_delivery_repair import apply_delivery_reverification, apply_native_chat_readback
    from tests.test_dingtalk_delivery_repair import historical_receipt, proof, _native_chat_proof, _legacy_native_readback_case
    if legacy:
        store, task, receipt, readback, _ = _legacy_native_readback_case(tmp_path, monkeypatch)
        handler = None
    else:
        store, task, _, _, handler, receipt, _ = historical_receipt(tmp_path)
        assert apply_delivery_reverification(store, proof(receipt, 'ambiguous'))
        readback = _native_chat_proof(task, store.get_sent_reply(task.conversation_id, task.trigger_message_id))
    assert apply_native_chat_readback(store, readback)
    expected = store.get_candidate_external_action(receipt['external_action_key'])['provider_result_json']
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id).send_result_json == expected
    with store._connect() as connection:
        assert "send_result_json" not in {row[1] for row in connection.execute("pragma table_xinfo(actions)")}
        assert connection.execute('pragma foreign_key_check').fetchall() == []
    if handler is not None:
        assert handler.sends == 1
