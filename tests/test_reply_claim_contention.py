import sqlite3

import pytest

from app import store as store_module
from app.store import AutoReplyStore


@pytest.mark.parametrize("queue", ["empty", "future", "other_channel"])
def test_idle_reply_claim_does_not_wait_for_an_unrelated_writer(
    tmp_path, monkeypatch, queue
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3", busy_timeout_seconds=0)
    monkeypatch.setattr(store_module, "STORE_WRITE_LOCK_RETRY_ATTEMPTS", 1)
    if queue != "empty":
        store.enqueue_reply_task(
            conversation_id="cid",
            conversation_title="Test",
            single_chat=False,
            trigger_message_id="msg",
            trigger_create_time="2026-10-08 00:00:00",
            trigger_sender="Sender",
            trigger_text="Test",
            channel="email" if queue == "future" else "dingtalk",
            available_at="2026-10-09 00:00:00" if queue == "future" else "",
        )
    writer = sqlite3.connect(store.path)
    try:
        writer.execute("begin immediate")
        assert (
            store.claim_reply_tasks(1, now="2026-10-08 00:00:00", channel="email") == []
        )
    finally:
        writer.rollback()
        writer.close()


def test_reply_claim_rechecks_after_another_worker_claims_the_preview(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    rival = AutoReplyStore(store.path)
    store.enqueue_reply_task(
        conversation_id="cid",
        conversation_title="Test",
        single_chat=False,
        trigger_message_id="msg",
        trigger_create_time="2026-10-08 00:00:00",
        trigger_sender="Sender",
        trigger_text="Test",
        channel="email",
    )
    original_peek = store.peek_reply_tasks
    rival_claims = []

    def raced_peek(*args, **kwargs):
        result = original_peek(*args, **kwargs)
        rival_claims.extend(rival.claim_reply_tasks(1, channel="email"))
        return result

    monkeypatch.setattr(store, "peek_reply_tasks", raced_peek)
    assert store.claim_reply_tasks(1, channel="email") == []
    assert len(rival_claims) == 1
    assert rival_claims[0].attempts == 1
