"""One immutable prepared Action owns its existing claim and accepted result."""
import sqlite3

import pytest

from app.store import AutoReplyStore
from app.shared_action_lifecycle import transition


def test_prepared_claim_and_receipt_have_one_physical_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "prepared.sqlite3")
    message = store.prepare_outbound_postfix(
        channel="dingtalk", delivery_key="prepared:01", body="First body", original_text="",
    )
    with store._connect() as db:
        before = tuple(db.execute(
            "select id,legacy_key,payload_json from actions where record_type='outbound_postfixes'"
        ).fetchone())
    assert store.claim_native_reply_dispatch(message.channel, message.delivery_key)
    assert not store.claim_native_reply_dispatch(message.channel, message.delivery_key)
    assert store.record_outbound_postfix_receipt(
        message.channel, message.delivery_key, {"processQueryKey": "accepted"},
    ) == {"processQueryKey": "accepted"}
    assert store.record_outbound_postfix_receipt(
        message.channel, message.delivery_key, {"processQueryKey": "second"},
    ) == {"processQueryKey": "accepted"}
    with store._connect() as db:
        assert db.execute("select count(*) from actions").fetchone()[0] == 1
        assert tuple(db.execute(
            "select id,legacy_key,payload_json from actions where record_type='outbound_postfixes'"
        ).fetchone()) == before
        assert db.execute("select count(*) from runs").fetchone()[0] == 0
        columns = {row[1] for row in db.execute("pragma table_xinfo(actions)")}
        assert "postfix_type" not in columns
        assert {
            "postfix_receipt_provider_result_json", "postfix_receipt_created_at",
            "postfix_dispatch_created_at",
        } <= columns


def test_shared_prepared_lifecycle_preserves_distinct_source_clocks(tmp_path):
    store = AutoReplyStore(tmp_path / "clocks.sqlite3")
    key = ("wechat", " key:01 ")
    with store._immediate_write_transaction() as db:
        transition(db, "prepared", "prepare", values={
            "channel": key[0], "delivery_key": key[1], "final_body": " original ",
            "feedback_token": "token", "postfix_version": "1", "created_at": "prepare-clock",
        })
        transition(db, "prepared", "claim", identity=key, values={"created_at": "claim-clock"})
        transition(db, "prepared", "outcome", identity=key, values={
            "provider_result_json": ' { "raw" : 1 } ', "created_at": "receipt-clock",
        })
        assert tuple(db.execute(
            "select created_at,postfix_dispatch_created_at,postfix_receipt_created_at,"
            "postfix_receipt_provider_result_json from actions"
        ).fetchone()) == ("prepare-clock", "claim-clock", "receipt-clock", ' { "raw" : 1 } ')


def test_shared_prepared_missing_parent_keeps_foreign_key_error(tmp_path):
    store = AutoReplyStore(tmp_path / "missing.sqlite3")
    with store._immediate_write_transaction() as db:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            transition(db, "prepared", "claim", identity=("dingtalk", "missing"))

