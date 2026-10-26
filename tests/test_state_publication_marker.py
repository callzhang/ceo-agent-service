"""Publication marker selection has an explicit current declaration boundary."""

import pytest

from app.state_storage import insert_state

CURRENT = "2026-10-14.1"


@pytest.mark.parametrize("expiry,terminal,expected", (
    ("2099-01-01 00:00:00", "", 1),
    ("2000-01-01 00:00:00", "", 0),
    ("2099-01-01 00:00:00", "done", 0),
))
def test_current_quiet_wait_reads_dispatcher_without_native_table(tmp_path, expiry, terminal, expected):
    from app.repository_updater import in_flight_work
    from app.store import AutoReplyStore
    from app.task_lifecycle_storage import acquire_claim
    path = tmp_path / "quiet.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="chat", conversation_title="Chat", single_chat=True,
        trigger_message_id="message", trigger_create_time="2026-10-25 10:00:00",
        trigger_sender="Sender", trigger_text="text",
    )
    task = store.get_reply_task_for_message("chat", "message")
    source_id = str(task.id)
    with store._connect() as db:
        db.execute("begin")
        if terminal:
            insert_state(db, "dispatcher_claim_leases", {
                "adapter_name": "reply", "source_id": source_id, "owner": "worker",
                "owner_pid": 42, "lease_expires_at": expiry, "terminal_at": terminal,
            })
        else:
            acquire_claim(db, adapter_name="reply", source_id=source_id, owner="worker",
                owner_pid=42, lease_expires_at=expiry, now="1999-01-01 00:00:00")
    before = path.read_bytes()
    assert in_flight_work(path) == expected
    assert path.read_bytes() == before


