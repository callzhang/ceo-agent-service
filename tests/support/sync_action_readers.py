"""Narrow test-only reads of current synchronization action projections."""

from app.action_storage import SYNC_ACTION_PROJECTIONS


def todo_sync_outbox(store, *, statuses=None):
    query = f"select * from ({SYNC_ACTION_PROJECTIONS['business_task_todo_sync_outbox']})"
    args = statuses or ()
    if args:
        query += f" where status in ({','.join('?' for _ in args)})"
    with store._connect() as db:
        return list(db.execute(query + " order by id", args))


def follow_up_send_attempts(store, *, draft_id):
    with store._connect() as db:
        return list(db.execute(
            f"select * from ({SYNC_ACTION_PROJECTIONS['business_task_follow_up_send_attempts']}) "
            "where draft_id=? order by id", (draft_id,),
        ))
