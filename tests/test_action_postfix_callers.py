"""Real Store bootstrap and existing postfix APIs on the current delivery target."""
import sqlite3

from app.action_storage import action_schema_is_current
from app.current_database_schema import FINAL_ACTION_OPTIONS
from app.state_definitions import state_projection
from app.store import AutoReplyStore


def test_fresh_store_keeps_postfix_types_and_no_retired_postfix_tables(tmp_path):
    path = tmp_path / 'store.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(f"select value from ({state_projection('service_state')}) where key='store_schema_version'").fetchone() == ('2026-10-14.1',)
        assert action_schema_is_current(db, reply_variant="fresh_memory_fk", **FINAL_ACTION_OPTIONS)
        assert not db.execute("select name from sqlite_schema where name in ('outbound_postfixes','outbound_postfix_receipts','native_reply_dispatches')").fetchall()


def test_existing_prepare_claim_receipt_and_echo_api_keep_first_values(tmp_path):
    path = tmp_path / 'store.sqlite3'
    store = AutoReplyStore(path)
    first = store.prepare_outbound_postfix('dingtalk', 'owned-key', 'first body', 'source', feedback_base_url='')
    assert store.prepare_outbound_postfix('dingtalk', 'owned-key', 'changed body', 'changed source', feedback_base_url='') == first
    assert store.get_outbound_postfix('dingtalk', 'owned-key') == first
    assert store.claim_native_reply_dispatch('dingtalk', 'owned-key')
    assert not store.claim_native_reply_dispatch('dingtalk', 'owned-key')
    receipt = {'result': {'openMessageId': 'owned-message'}, 'raw': [2, 1]}
    assert store.record_outbound_postfix_receipt('dingtalk', 'owned-key', receipt) == receipt
    assert store.record_outbound_postfix_receipt('dingtalk', 'owned-key', {'different': True}) == receipt
    assert store.get_outbound_postfix_receipt('dingtalk', 'owned-key') == receipt
    assert len(store.recorded_outbound_body_keys('dingtalk', '')) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("select record_type,count(*) from actions where record_type in ('outbound_postfixes','outbound_postfix_receipts','native_reply_dispatches') group by record_type order by record_type").fetchall() == [
            ('native_reply_dispatches', 1), ('outbound_postfix_receipts', 1), ('outbound_postfixes', 1),
        ]
    # Real second constructor, not a cached Store instance or a fake marker.
    reopened = AutoReplyStore(path)
    assert reopened.get_outbound_postfix('dingtalk', 'owned-key') == first
    assert not reopened.claim_native_reply_dispatch('dingtalk', 'owned-key')
    assert reopened.get_outbound_postfix_receipt('dingtalk', 'owned-key') == receipt
