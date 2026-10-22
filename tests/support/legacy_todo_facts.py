"""Seed retained historical facts without restoring retired operational writers."""

from app.action_storage import insert_sync_action
from app.business_storage import insert_business, update_business


def seed_legacy_work_project(store, **values) -> int:
    with store._immediate_write_transaction() as db:
        return int(insert_business(db, "work_project", values).lastrowid)


def revise_legacy_work_project(store, project_id: int, **values) -> None:
    with store._immediate_write_transaction() as db:
        update_business(db, "work_project", project_id, values)


def seed_legacy_work_todo(store, **values) -> int:
    with store._immediate_write_transaction() as db:
        return int(insert_business(db, "work_todo", values).lastrowid)


def revise_legacy_work_todo(store, todo_id: int, **values) -> None:
    with store._immediate_write_transaction() as db:
        update_business(db, "work_todo", todo_id, values)


def seed_legacy_work_update(store, **values) -> int:
    with store._immediate_write_transaction() as db:
        return int(insert_sync_action(db, "work_updates", values).lastrowid)


def seed_legacy_todo_link(store, **values) -> int:
    with store._immediate_write_transaction() as db:
        return int(insert_sync_action(db, "work_todo_dingtalk_links", values).lastrowid)


def seed_legacy_todo_outbox(store, **values) -> int:
    with store._immediate_write_transaction() as db:
        return int(insert_sync_action(db, "task_todo_sync_outbox", values).lastrowid)
