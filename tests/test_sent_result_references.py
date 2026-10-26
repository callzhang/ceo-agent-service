"""Sent owns only exceptional bodies; exact provider bytes have one owner."""

import inspect
import sqlite3

import pytest

from app import action_storage as actions
from app.action_entity_storage import (
    append_action_receipt,
    create_action,
    record_action_result,
)
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
        action = connection.execute(
            "select id,action_external_action_key,action_delivery_receipt_id "
            "from actions where record_type='action'"
        ).fetchone()
        assert (action["id"], action["action_external_action_key"]) == (sent.id, "key")
        bodies = connection.execute(
            "select id,provider_result_json from action_receipts "
            "where action_id=? and provider_result_json is not null",
            (action["id"],),
        ).fetchall()
        assert len(bodies) == 1
        fact = connection.execute(
            "select provider_receipt_id from action_receipts where id=? and action_id=?",
            (action["action_delivery_receipt_id"], action["id"]),
        ).fetchone()
        body = connection.execute(
            "select provider_result_json from action_receipts where id=? and action_id=?",
            (fact["provider_receipt_id"], action["id"]),
        ).fetchone()
        assert body["provider_result_json"] == '{"receipt":"substitute"}'
        assert connection.execute('pragma foreign_key_check').fetchall() == []
    assert sent.send_result_json == '{"receipt":"substitute"}'
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) == sent


def test_unused_writers_and_finalize_sent_parameters_are_retired():
    assert not hasattr(AutoReplyStore, 'record_agent_message_delivery')
    assert not hasattr(AutoReplyStore, 'record_reply_attempt_for_trigger')
    parameters = inspect.signature(AutoReplyStore.finalize_orchestrated_reply_task).parameters
    assert 'sent_reply_text' not in parameters
    assert 'sent_reply_result_json' not in parameters


def test_action_external_key_lookup_uses_current_identity_index(tmp_path):
    store = AutoReplyStore(tmp_path / 'reference-cost.sqlite3')
    with store._connect() as connection:
        create_action(connection, values={"external_action_key": "key"})
        plan = [row[3] for row in connection.execute(
            "explain query plan select id from actions "
            "where record_type='action' and action_external_action_key=?",
            ("key",),
        )]
        assert any("SEARCH actions USING INDEX idx_actions_action_external_key" in item for item in plan)


def test_empty_json_scalar_and_non_json_results_keep_exact_value_and_type(tmp_path):
    store = AutoReplyStore(tmp_path / 'raw-values.sqlite3')
    bodies = ['', 'null', '0', 'false', '[]', '{}', ' { } ', 'raw\x00result', '原始\n\r值']
    with store._connect() as connection:
        body_receipts = []
        for ordinal, body in enumerate(bodies):
            action = create_action(connection, values={
                "external_action_key": f"raw-{ordinal}",
                "conversation_id": "chat", "trigger_message_id": f"trigger-{ordinal}",
                "prepared_body": "exact body", "delivery_channel": "dingtalk",
            })
            body_receipt = append_action_receipt(
                connection, action["id"], values={"provider_result_json": body},
            )
            delivery = append_action_receipt(
                connection, action["id"], values={
                    "provider_receipt_id": body_receipt, "observed_at": "sent-clock",
                },
            )
            assert record_action_result(
                connection, action["id"], expected={"delivery_receipt_id": None},
                changes={"delivery_receipt_id": delivery},
            ) is not None
            row = connection.execute(
                "select send_result_json,typeof(send_result_json) from (" + actions.SENT_REPLY_SQL + ") "
                "where id=?", (action["id"],),
            ).fetchone()
            assert tuple(row) == (body, "text")
            body_receipts.append(body_receipt)

        null_action = create_action(connection, values={"external_action_key": "sql-null"})
        null_receipt = append_action_receipt(
            connection, null_action["id"], values={"provider_result_json": None},
        )
        assert tuple(connection.execute(
            "select provider_result_json,typeof(provider_result_json) "
            "from action_receipts where id=?", (null_receipt,),
        ).fetchone()) == (None, "null")

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "update action_receipts set provider_result_json='different' where id=?",
                (body_receipts[0],),
            )


@pytest.mark.parametrize('legacy', [False, True])
def test_existing_native_readback_repairs_explicit_sent_result_without_resending(
    tmp_path, monkeypatch, legacy,
):
    from app.dingtalk_delivery_repair import apply_delivery_reverification, apply_native_chat_readback
    from tests.test_dingtalk_delivery_repair import (
        _legacy_delivery_facts,
        _legacy_native_readback_case,
        _native_chat_proof,
        historical_receipt,
        proof,
    )
    if legacy:
        store, task, receipt, readback, _attempt_id = _legacy_native_readback_case(
            tmp_path, monkeypatch,
        )
        facts_before = _legacy_delivery_facts(
            store, task, receipt['external_action_key'],
        )
    else:
        store, task, _, _, handler, receipt, _ = historical_receipt(tmp_path)
        assert apply_delivery_reverification(store, proof(receipt, 'ambiguous'))
        readback = _native_chat_proof(task, store.get_sent_reply(task.conversation_id, task.trigger_message_id))
    assert apply_native_chat_readback(store, readback)
    expected = store.get_candidate_external_action(receipt['external_action_key'])['provider_result_json']
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    assert sent.send_result_json == expected
    with store._connect() as connection:
        action = connection.execute(
            "select id,action_delivery_receipt_id from actions "
            "where record_type='action' and action_external_action_key=?",
            (receipt['external_action_key'],),
        ).fetchone()
        fact = connection.execute(
            "select provider_receipt_id from action_receipts where id=? and action_id=?",
            (action['action_delivery_receipt_id'], action['id']),
        ).fetchone()
        body = connection.execute(
            "select provider_result_json from action_receipts where id=? and action_id=?",
            (fact['provider_receipt_id'], action['id']),
        ).fetchone()
        assert action['id'] == sent.id
        assert body['provider_result_json'] == expected
        assert connection.execute('pragma foreign_key_check').fetchall() == []
    if legacy:
        assert _legacy_delivery_facts(
            store, task, receipt['external_action_key'],
        ) == facts_before
        assert not apply_native_chat_readback(store, readback)
    else:
        assert handler.sends == 1
