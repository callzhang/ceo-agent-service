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
    from tests.task_lifecycle_test_support import insert
    path = tmp_path / "quiet.sqlite3"
    store = AutoReplyStore(path)
    with store._connect() as db:
        db.execute("begin")
        insert(db, "reply_tasks", physical_id=7, legacy_key="7")
        if terminal:
            insert_state(db, "dispatcher_claim_leases", {
                "adapter_name": "reply", "source_id": "7", "owner": "worker",
                "owner_pid": 42, "lease_expires_at": expiry, "terminal_at": terminal,
            })
        else:
            acquire_claim(db, adapter_name="reply", source_id="7", owner="worker",
                owner_pid=42, lease_expires_at=expiry, now="1999-01-01 00:00:00")
    before = path.read_bytes()
    assert in_flight_work(path) == expected
    assert path.read_bytes() == before


