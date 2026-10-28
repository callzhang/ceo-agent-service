"""Seed effect provenance for current runtime recovery tests."""
from datetime import datetime, timezone

def seed_effect_started(store, attempt_id, at=None):
    if at is None:
        at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(at, datetime):
        at = at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with store._connect() as db:
        db.execute("update runtime_attempts set first_effect_started_at=? where id=?", (at, attempt_id))
    return store.get_agent_runtime_attempt(attempt_id)
