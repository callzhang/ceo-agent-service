"""Current Business caller and constructor contracts."""

from contextlib import closing
import sqlite3

import pytest

from app.business_schema_release import business_schema_is_current
from app.business_storage import insert_business
from app.email_store import EmailStore
from app.state_definitions import state_projection
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


@pytest.mark.parametrize("first", ("store", "email"))
def test_fresh_and_cold_constructor_orders_create_business(tmp_path, first):
    path = tmp_path / (first + ".sqlite3")
    if first == "store":
        AutoReplyStore(path)
        EmailStore(path)
    else:
        EmailStore(path)
        AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert business_schema_is_current(
            db,
            include_followup_sync=True,
            include_candidates=True,
            include_business_actions=True,
            inline_legacy_owner=True,
        )
        assert db.execute(
            f"select value from ({state_projection('service_state')}) "
            "where key='store_schema_version'"
        ).fetchone() == ("2026-10-14.1",)
        assert db.execute(
            "select max(version) from email_schema_migrations"
        ).fetchone() == (52,)
    EmailStore(path)
    AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert business_schema_is_current(
            db,
            include_followup_sync=True,
            include_candidates=True,
            include_business_actions=True,
            inline_legacy_owner=True,
        )
        db.execute("drop index idx_business_task_public_id")
        db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source"):
        AutoReplyStore(path)
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source"):
        EmailStore(path)
