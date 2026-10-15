"""History requests use a fresh snapshot and honor the requested page size."""

from app.store import AutoReplyStore


def record(store, number):
    return store.record_reply_attempt(
        conversation_id="fresh-history", conversation_title="History",
        trigger_message_id=str(number), trigger_sender="sender",
        trigger_text=f"Message {number}", action="chat_message",
        sensitivity_kind="general", send_status="sent",
    )


def test_history_larger_page_is_not_a_reused_smaller_page(tmp_path):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    for number in range(30):
        record(store, number)
    first_total, first = store.list_operation_logs_with_count(
        limit=20, source_tables=("reply_attempts",),
    )
    total, larger = store.list_operation_logs_with_count(
        limit=100, source_tables=("reply_attempts",),
    )
    assert first_total == total == 30
    assert len(first) == 20
    assert len(larger) == 30


def test_history_next_request_sees_new_record_and_matching_total(tmp_path):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    record(store, 1)
    assert store.list_operation_logs_with_count(
        limit=20, source_tables=("reply_attempts",),
    )[0] == 1
    new = record(store, 2)
    total, rows = store.list_operation_logs_with_count(
        limit=20, source_tables=("reply_attempts",),
    )
    assert total == 2
    assert len(rows) == 2
    assert any(row.source_id == new for row in rows)
