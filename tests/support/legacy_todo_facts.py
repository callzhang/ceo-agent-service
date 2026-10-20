"""Seed retained historical facts without restoring retired operational writers."""

from app.action_storage import insert_sync_action


def seed_legacy_todo_link(store, **values) -> int:
    with store._immediate_write_transaction() as db:
        return int(insert_sync_action(db, "work_todo_dingtalk_links", values).lastrowid)


def seed_legacy_todo_outbox(store, **values) -> int:
    with store._immediate_write_transaction() as db:
        return int(insert_sync_action(db, "task_todo_sync_outbox", values).lastrowid)
