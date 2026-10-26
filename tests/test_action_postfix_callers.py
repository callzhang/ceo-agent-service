"""Real Store bootstrap and existing postfix APIs on the current delivery target."""
import sqlite3

import pytest

from app.state_definitions import state_projection
from app.store import AutoReplyStore, STORE_SCHEMA_VERSION


def test_fresh_store_keeps_postfix_types_and_no_retired_postfix_tables(tmp_path):
    path = tmp_path / 'store.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(f"select value from ({state_projection('service_state')}) where key='store_schema_version'").fetchone() == (STORE_SCHEMA_VERSION,)
        assert not db.execute("select name from sqlite_schema where name in ('outbound_postfixes','outbound_postfix_receipts','native_reply_dispatches')").fetchall()


def test_existing_prepare_claim_receipt_and_echo_api_keep_first_values(tmp_path):
    path = tmp_path / 'store.sqlite3'
    store = AutoReplyStore(path)
    first = store.prepare_outbound_postfix('dingtalk', 'owned-key', 'first body', 'source', feedback_base_url='')
    assert store.prepare_outbound_postfix('dingtalk', 'owned-key', 'changed body', 'changed source', feedback_base_url='') == first
    assert store.get_outbound_postfix('dingtalk', 'owned-key') == first
    assert store.claim_native_reply_dispatch('dingtalk', 'owned-key', operation='send_reply_to_trigger')
    assert not store.claim_native_reply_dispatch('dingtalk', 'owned-key', operation='send_reply_to_trigger')
    receipt = {'result': {'openMessageId': 'owned-message'}, 'raw': [2, 1]}
    assert store.record_outbound_postfix_receipt('dingtalk', 'owned-key', receipt, operation='send_reply_to_trigger') == receipt
    with pytest.raises(ValueError, match='different provider receipt'):
        store.record_outbound_postfix_receipt('dingtalk', 'owned-key', {'different': True}, operation='send_reply_to_trigger')
    assert store.get_outbound_postfix_receipt('dingtalk', 'owned-key') == receipt
    assert len(store.recorded_outbound_body_keys('dingtalk', '')) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from actions where record_type in ('outbound_postfixes','outbound_postfix_receipts','native_reply_dispatches')").fetchone() == (0,)
        assert db.execute("select count(*) from actions where record_type='action' and action_delivery_key='owned-key'").fetchone() == (1,)
    # Real second constructor, not a cached Store instance or a fake marker.
    reopened = AutoReplyStore(path)
    assert reopened.get_outbound_postfix('dingtalk', 'owned-key') == first
    assert not reopened.claim_native_reply_dispatch('dingtalk', 'owned-key', operation='send_reply_to_trigger')
    assert reopened.get_outbound_postfix_receipt('dingtalk', 'owned-key') == receipt
