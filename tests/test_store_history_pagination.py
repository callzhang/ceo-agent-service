from contextlib import contextmanager

import pytest

from app.store import AutoReplyStore


@pytest.mark.parametrize("offset,limit", [(0, 1), (3, 2), (8, 2)])
def test_history_hydrates_bodies_only_for_requested_page(
    tmp_path, monkeypatch, offset, limit
):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    for number in range(8):
        store.record_reply_attempt(
            conversation_id="pagination",
            conversation_title="Pagination",
            trigger_message_id=str(number),
            trigger_sender="sender",
            trigger_text=f"body {number}",
            action="agent_run",
            sensitivity_kind="general",
            send_status="skipped",
        )
    expected = store.list_operation_logs(
        limit=limit, offset=offset, source_tables=("reply_attempts",)
    )
    body_reads = []
    original_connect = store._connect
    original_base = store._operation_logs_base_query

    @contextmanager
    def instrumented_connection():
        with original_connect() as db:
            db.create_function(
                "read_history_body", 1, lambda value: body_reads.append(value) or value
            )
            yield db

    def instrumented_base(*args, **kwargs):
        return original_base(*args, **kwargs).replace(
            "trigger_text as summary", "read_history_body(trigger_text) as summary"
        )

    monkeypatch.setattr(store, "_connect", instrumented_connection)
    monkeypatch.setattr(store, "_operation_logs_base_query", instrumented_base)
    total, rows = store.list_operation_logs_with_count(
        limit=limit,
        offset=offset,
        source_tables=("reply_attempts",),
        _skip_history_cache=True,
    )
    assert total == 8
    assert rows == expected
    assert len(body_reads) == len(rows)
