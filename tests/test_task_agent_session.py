import threading

import pytest

from app.store import AutoReplyStore
from app.task_agent_session import (
    TASK_AGENT_SESSION_SCOPE_ID,
    TaskAgentSessionLease,
    TaskAgentSessionLeaseLost,
)


def test_task_agent_session_lease_serializes_store_instances(tmp_path):
    db_path = tmp_path / "task-agent-session.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)

    first = TaskAgentSessionLease.try_acquire(first_store)
    assert first is not None
    try:
        assert TaskAgentSessionLease.try_acquire(second_store) is None
    finally:
        first.close()

    second = TaskAgentSessionLease.try_acquire(second_store)
    assert second is not None
    second.close()


def test_task_agent_session_lease_marks_failed_renewal_as_lost():
    class Store:
        def acquire_codex_session_lock(self, conversation_id, owner):
            return True

        def renew_codex_session_lock(self, conversation_id, owner):
            return False

        def release_codex_session_lock(self, conversation_id, owner):
            return True

    lease = TaskAgentSessionLease.try_acquire(Store(), renew_interval_seconds=60)
    assert lease is not None
    try:
        with pytest.raises(TaskAgentSessionLeaseLost, match="no longer owned"):
            lease.assert_owned()
    finally:
        lease.close()


def test_task_agent_session_lease_renews_until_closed(tmp_path):
    store = AutoReplyStore(tmp_path / "task-agent-session-renew.sqlite3")
    renewals = []
    two_renewals = threading.Event()
    original_renew = store.renew_codex_session_lock

    def record_renewal(conversation_id, owner, *, now=None):
        renewed = original_renew(conversation_id, owner, now=now)
        renewals.append(renewed)
        if len(renewals) >= 2:
            two_renewals.set()
        return renewed

    store.renew_codex_session_lock = record_renewal
    lease = TaskAgentSessionLease.try_acquire(
        store,
        renew_interval_seconds=0.01,
    )
    assert lease is not None
    assert two_renewals.wait(timeout=2)
    lease.close()

    assert renewals and all(renewals)
    assert not lease._renew_thread.is_alive()
    with store._connect() as db:
        row = db.execute(
            "select owner from codex_session_locks where conversation_id=?",
            (TASK_AGENT_SESSION_SCOPE_ID,),
        ).fetchone()
    assert row is None
