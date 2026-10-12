import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.dispatcher import adapters
from app.store import AutoReplyStore


NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
ADAPTERS = [
    adapters.ScheduledTaskQueueAdapter, adapters.ScheduledExecutionQueueAdapter,
    adapters.ReplyQueueAdapter, adapters.MeetingQueueAdapter,
    adapters.OkrReviewQueueAdapter, adapters.WorkSummaryQueueAdapter,
    adapters.TaskTodoSyncOutboxQueueAdapter, adapters.TaskMemoryWriteQueueAdapter,
    adapters.BusinessTaskTodoSyncOutboxQueueAdapter,
]


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_empty_dispatcher_claim_is_read_only_with_another_writer(
    tmp_path, monkeypatch, adapter_type,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    original = store._open_connection

    def no_wait():
        db = original()
        db.execute("pragma busy_timeout=0")
        return db

    monkeypatch.setattr(store, "_open_connection", no_wait)
    other = sqlite3.connect(store.path, timeout=0)
    try:
        other.execute("begin immediate")
        assert adapter_type(store).claim(
            NOW, owner="idle-worker", lease=timedelta(minutes=10),
        ) is None
    finally:
        other.rollback()
        other.close()


def test_positive_dispatcher_probe_rechecks_competing_claim(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    other_store = AutoReplyStore(store.path)
    store.enqueue_reply_task(
        conversation_id="cid", conversation_title="Title", single_chat=False,
        trigger_message_id="msg", trigger_create_time="2026-10-10 10:00:00",
        trigger_sender="Derek", trigger_text="Task",
    )
    original = store._immediate_write_transaction
    winner = []

    @contextmanager
    def race_before_begin():
        winner.append(adapters.ReplyQueueAdapter(other_store).claim(
            NOW, owner="other", lease=timedelta(minutes=10),
        ))
        with original() as db:
            yield db

    monkeypatch.setattr(store, "_immediate_write_transaction", race_before_begin)
    assert adapters.ReplyQueueAdapter(store).claim(
        NOW, owner="ours", lease=timedelta(minutes=10),
    ) is None
    assert len(winner) == 1 and winner[0] is not None
