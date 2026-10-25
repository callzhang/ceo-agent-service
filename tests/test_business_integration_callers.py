"""Current Business caller and constructor contracts."""


import pytest

from app.business_storage import insert_business
from app.store import AutoReplyStore


@pytest.fixture
def current_business_store(tmp_path):
    return AutoReplyStore(tmp_path / "business-current.sqlite3")


def test_current_task_api_returns_public_id(current_business_store):
    store = current_business_store
    with store._connect() as db:
        db.execute("begin immediate")
        task_id = store.create_business_task_in_transaction(
            title="synthetic", stage="candidate", _db=db
        )
        db.commit()
    row = store.get_business_task(task_id)
    assert row.id == task_id
    with store._connect() as db:
        physical = db.execute(
            "select id from business where record_type='task' and public_id=?",
            (task_id,),
        ).fetchone()[0]
    assert physical > 0


def test_current_task_transaction_reads_use_the_business_projection(current_business_store):
    store = current_business_store
    with store._connect() as db:
        seen = []
        db.set_trace_callback(seen.append)
        db.execute("begin immediate")
        task_id = store.create_business_task_in_transaction(
            title="trace", stage="candidate", _db=db
        )
        row = store.get_business_task_in_transaction(task_id=task_id, _db=db)
        db.commit()
    assert row.id == task_id
    retired = (
        "business_tasks", "business_projects", "business_anchors",
        "business_project_context_revisions",
    )
    assert not [sql for sql in seen if any(name in sql.lower() for name in retired)]


def test_default_task_listing_orders_public_ids_when_physical_ids_differ(
    current_business_store,
):
    store = current_business_store
    with store.business_task_transaction() as db:
        for public_id in (100, 50):
            insert_business(db, "task", {
                "id": public_id,
                "title": f"Task {public_id}",
                "stage": "candidate",
                "created_at": "2026-10-15T00:00:00+00:00",
                "updated_at": "2026-10-15T00:00:00+00:00",
            })
    with store._connect() as db:
        physical_order = tuple(row[0] for row in db.execute(
            "select public_id from business where record_type='task' order by id"
        ))
    assert physical_order == (100, 50)
    assert tuple(task.id for task in store.list_business_tasks()) == (50, 100)


