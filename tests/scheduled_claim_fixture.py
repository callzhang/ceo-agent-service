"""Claim scheduled occurrences through the current dispatcher in tests."""
from datetime import timedelta
from app.dispatcher.adapters import ScheduledTaskQueueAdapter

def claim_scheduled_occurrence(store, run_id, *, owner, lease_seconds, now, owner_alive=True):
    """Claim with an explicit simulated state for the previous process."""
    envelope = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: owner_alive).claim(
        now, owner=owner, lease=timedelta(seconds=lease_seconds))
    if envelope is None:
        return None
    assert int(envelope.source_id) == run_id
    return store.get_scheduled_task_run(run_id)
