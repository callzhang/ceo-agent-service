from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from pathlib import Path

import pytest

from app.action_storage import create_action_schema, insert_sync_action, update_sync_action
from app.action_followup_sync_definitions import SYNC_ACTION_PROJECTIONS
from app.dispatcher.adapters import (
    BusinessTaskTodoSyncOutboxQueueAdapter,
    TaskTodoSyncOutboxQueueAdapter,
)
from app.state_definitions import state_projection
from app.state_storage import create_state_schema
from app.store import AutoReplyStore


NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _target_store(tmp_path: Path) -> AutoReplyStore:
    path = tmp_path / "actions.sqlite3"
    db = sqlite3.connect(path)
    db.execute("pragma foreign_keys=on")
    db.execute("create table tasks(id integer primary key, record_type text)")
    db.execute("create table runs(id integer primary key, record_type text)")
    db.execute("create table email_classifications(id integer primary key)")
    db.execute("create table work_todos(id integer primary key)")
    db.execute("create table business_tasks(id integer primary key)")
    db.execute("create table business(task_public_id integer primary key)")
    db.execute("create table business_task_signals(id integer primary key)")
    db.execute("insert into work_todos values(1)")
    db.execute("insert into business_tasks values(1)")
    db.execute("insert into business values(1)")
    db.execute("insert into business_task_signals values(1)")
    create_action_schema(
        db,
        include_reply=True,
        reply_variant="actual_no_memory_fk",
        include_email=True,
        include_followup_sync=True,
        include_business_sync=True,
    )
    create_state_schema(db)
    db.commit()
    db.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = 1
    store.busy_timeout_milliseconds = 1000
    store._read_snapshot_connection = ContextVar("test_snapshot", default=None)
    store._reused_connection = ContextVar(f"test_reused_connection_{id(store)}", default=None)
    return store


@pytest.mark.parametrize(
    ("kind", "adapter_type", "row"),
    (
        (
            "task_todo_sync_outbox",
            TaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "task:1:create", "work_todo_id": 1, "operation": "create"},
        ),
        (
            "business_task_todo_sync_outbox",
            BusinessTaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "business:1:create", "business_task_id": 1, "operation": "create"},
        ),
    ),
)
def test_todo_outbox_dispatcher_claim_lifecycle_uses_actions(
    tmp_path: Path, kind: str, adapter_type, row: dict,
) -> None:
    store = _target_store(tmp_path)
    with store._immediate_write_transaction() as db:
        source_id = insert_sync_action(db, kind, row).lastrowid
    adapter = adapter_type(store, owner_alive=lambda _pid: False)

    metrics = adapter.metrics(NOW)
    assert metrics.pending == 1
    assert metrics.due == 1

    envelope = adapter.claim(
        NOW, owner="dispatcher", owner_pid=42, lease=timedelta(minutes=5)
    )
    assert envelope is not None
    assert envelope.source_id == str(source_id)
    assert envelope.attempt == 1
    assert adapter.metrics(NOW).running == 1

    adapter.renew(
        envelope, owner="dispatcher", now=NOW, lease=timedelta(minutes=8)
    )
    with store._connect() as db:
        row_after_renew = db.execute(
            f"select lease_expires_at from ({SYNC_ACTION_PROJECTIONS[kind]}) where id=?",
            (source_id,),
        ).fetchone()
    assert row_after_renew[0] == "2026-10-17 12:08:00"

    adapter.release(envelope, owner="dispatcher", now=NOW)
    with store._connect() as db:
        row_after_release = db.execute(
            f"select status, attempt_count, lease_owner, lease_expires_at from ({SYNC_ACTION_PROJECTIONS[kind]}) where id=?",
            (source_id,),
        ).fetchone()
    assert tuple(row_after_release) == ("queued", 0, "", "")
    assert adapter.metrics(NOW).running == 0


@pytest.mark.parametrize(
    ("kind", "adapter_type", "parent_field", "parent_id"),
    (
        ("task_todo_sync_outbox", TaskTodoSyncOutboxQueueAdapter, "work_todo_id", 1),
        (
            "business_task_todo_sync_outbox",
            BusinessTaskTodoSyncOutboxQueueAdapter,
            "business_task_id",
            1,
        ),
    ),
)
def test_todo_outbox_claim_marks_expired_running_effect_unknown(
    tmp_path: Path, kind: str, adapter_type, parent_field: str, parent_id: int,
) -> None:
    store = _target_store(tmp_path)
    with store._immediate_write_transaction() as db:
        expired_id = insert_sync_action(
            db,
            kind,
            {
                "operation_key": "expired:old-effect",
                parent_field: parent_id,
                "operation": "create",
                "status": "running",
                "lease_owner": "lost-worker",
                "lease_expires_at": "2026-10-17 11:59:59",
                "attempt_count": 1,
            },
        ).lastrowid
        queued_id = insert_sync_action(
            db,
            kind,
            {"operation_key": "queued:new-effect", parent_field: parent_id, "operation": "create"},
        ).lastrowid
    adapter = adapter_type(store, owner_alive=lambda _pid: False)

    envelope = adapter.claim(
        NOW, owner="next-worker", owner_pid=43, lease=timedelta(minutes=5)
    )

    assert envelope is not None
    assert envelope.source_id == str(queued_id)
    with store._connect() as db:
        expired = db.execute(
            f"select status,error,lease_owner,lease_expires_at from ({SYNC_ACTION_PROJECTIONS[kind]}) where id=?",
            (expired_id,),
        ).fetchone()
    assert tuple(expired) == ("unknown", "receipt_reconciliation_required", "", "")


@pytest.mark.parametrize(
    ("kind", "adapter_type", "row"),
    (
        (
            "task_todo_sync_outbox",
            TaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "task:finish:create", "work_todo_id": 1, "operation": "create"},
        ),
        (
            "business_task_todo_sync_outbox",
            BusinessTaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "business:finish:create", "business_task_id": 1, "operation": "create"},
        ),
    ),
)
def test_todo_outbox_finish_verifies_terminal_action_status(
    tmp_path: Path, kind: str, adapter_type, row: dict,
) -> None:
    store = _target_store(tmp_path)
    with store._immediate_write_transaction() as db:
        source_id = insert_sync_action(db, kind, row).lastrowid
    adapter = adapter_type(store, owner_alive=lambda _pid: False)
    envelope = adapter.claim(
        NOW, owner="dispatcher", owner_pid=42, lease=timedelta(minutes=5)
    )
    assert envelope is not None

    with store._immediate_write_transaction() as db:
        update_sync_action(
            db, kind, int(source_id), {"status": "completed", "completed_at": "2026-10-17 12:00:00"},
            where_sql="status='running' and lease_owner=?", parameters=("dispatcher",),
        )
    adapter.finish(envelope, owner="dispatcher", now=NOW, status="completed")

    with store._connect() as db:
        lease = db.execute(
            "select owner,lease_expires_at,terminal_at from (" + state_projection(
                "dispatcher_claim_leases", include_rowid=True
            ) + ") where adapter_name=? and source_id=?",
            (adapter.name, str(source_id)),
        ).fetchone()
    assert tuple(lease) == ("", "", "2026-10-17 12:00:00")


@pytest.mark.parametrize(
    ("kind", "enqueue", "list_rows", "get_row", "arguments"),
    (
        (
            "task_todo_sync_outbox",
            "enqueue_task_todo_sync_outbox",
            "list_task_todo_sync_outbox",
            "get_task_todo_sync_outbox",
            {"operation_key": "task:store:create", "work_todo_id": 1, "operation": "create"},
        ),
        (
            "business_task_todo_sync_outbox",
            "enqueue_business_task_todo_sync_outbox",
            "list_business_task_todo_sync_outbox",
            "get_business_task_todo_sync_outbox",
            {"operation_key": "business:store:create", "business_task_id": 1, "operation": "create"},
        ),
    ),
)
def test_store_outbox_enqueue_list_get_uses_typed_actions(
    tmp_path: Path, kind: str, enqueue: str, list_rows: str, get_row: str, arguments: dict,
) -> None:
    store = _target_store(tmp_path)

    getattr(store, enqueue)(**arguments)

    rows = getattr(store, list_rows)()
    assert len(rows) == 1
    assert rows[0]["operation_key"] == arguments["operation_key"]
    assert rows[0]["status"] == "queued"
    fetched = getattr(store, get_row)(int(rows[0]["id"]))
    assert fetched is not None
    assert fetched["id"] == rows[0]["id"]


@pytest.mark.parametrize(
    ("kind", "enqueue", "claim", "get_row", "arguments"),
    (
        (
            "task_todo_sync_outbox",
            "enqueue_task_todo_sync_outbox",
            "claim_task_todo_sync_outbox",
            "get_task_todo_sync_outbox",
            {"operation_key": "task:store:claim", "work_todo_id": 1, "operation": "create"},
        ),
        (
            "business_task_todo_sync_outbox",
            "enqueue_business_task_todo_sync_outbox",
            "claim_business_task_todo_sync_outbox",
            "get_business_task_todo_sync_outbox",
            {"operation_key": "business:store:claim", "business_task_id": 1, "operation": "create"},
        ),
    ),
)
def test_store_outbox_claim_uses_typed_actions(
    tmp_path: Path, kind: str, enqueue: str, claim: str, get_row: str, arguments: dict,
) -> None:
    store = _target_store(tmp_path)
    getattr(store, enqueue)(**arguments)

    claimed = getattr(store, claim)(owner="store-worker", now="2026-10-17 12:00:00")

    assert claimed is not None
    assert claimed["status"] == "queued"
    persisted = getattr(store, get_row)(claimed["id"])
    assert persisted["status"] == "running"
    assert persisted["attempt_count"] == 1
    assert persisted["lease_owner"] == "store-worker"


@pytest.mark.parametrize(
    ("kind", "enqueue", "get_row", "adapter_type", "arguments", "error_prefix"),
    (
        (
            "task_todo_sync_outbox",
            "enqueue_task_todo_sync_outbox",
            "get_task_todo_sync_outbox",
            TaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "task:retry:create", "work_todo_id": 1, "operation": "create"},
            "task_todo_sync_retry_exhausted:",
        ),
        (
            "business_task_todo_sync_outbox",
            "enqueue_business_task_todo_sync_outbox",
            "get_business_task_todo_sync_outbox",
            BusinessTaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "business:retry:create", "business_task_id": 1, "operation": "create"},
            "business_task_todo_sync_retry_exhausted:",
        ),
    ),
)
def test_outbox_delivery_retry_preserves_backoff_and_attempt_limit(
    tmp_path: Path, kind: str, enqueue: str, get_row: str, adapter_type, arguments: dict,
    error_prefix: str,
) -> None:
    store = _target_store(tmp_path)
    getattr(store, enqueue)(**arguments)
    outbox_id = int(getattr(store, get_row)(1)["id"])
    adapter = adapter_type(store, owner_alive=lambda _pid: False)

    first = adapter.claim(NOW, owner="worker-1", owner_pid=41, lease=timedelta(minutes=5))
    assert first is not None
    adapter.finish_delivery(
        first, owner="worker-1", now=NOW, status="failed", error="temporary failure"
    )
    after_first = getattr(store, get_row)(outbox_id)
    assert after_first["status"] == "failed"
    assert after_first["attempt_count"] == 1
    assert after_first["next_attempt_at"] == "2026-10-17 12:01:00"
    assert after_first["error"] == "temporary failure"

    third_attempt_time = NOW + timedelta(minutes=1)
    second = adapter.claim(
        third_attempt_time, owner="worker-2", owner_pid=42, lease=timedelta(minutes=5)
    )
    assert second is not None and second.attempt == 2
    adapter.finish_delivery(
        second, owner="worker-2", now=third_attempt_time,
        status="failed", error="temporary failure",
    )
    after_second = getattr(store, get_row)(outbox_id)
    assert after_second["attempt_count"] == 2
    assert after_second["next_attempt_at"] == "2026-10-17 12:03:00"

    third_attempt_time = NOW + timedelta(minutes=3)
    third = adapter.claim(
        third_attempt_time, owner="worker-3", owner_pid=43, lease=timedelta(minutes=5)
    )
    assert third is not None and third.attempt == 3
    adapter.finish_delivery(
        third, owner="worker-3", now=third_attempt_time,
        status="failed", error="temporary failure",
    )
    exhausted = getattr(store, get_row)(outbox_id)
    assert exhausted["attempt_count"] == 3
    assert exhausted["next_attempt_at"] == ""
    assert exhausted["error"] == error_prefix + "temporary failure"


@pytest.mark.parametrize(
    ("kind", "enqueue", "get_row", "adapter_type", "arguments"),
    (
        (
            "task_todo_sync_outbox",
            "enqueue_task_todo_sync_outbox",
            "get_task_todo_sync_outbox",
            TaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "task:receipt:create", "work_todo_id": 1, "operation": "create"},
        ),
        (
            "business_task_todo_sync_outbox",
            "enqueue_business_task_todo_sync_outbox",
            "get_business_task_todo_sync_outbox",
            BusinessTaskTodoSyncOutboxQueueAdapter,
            {"operation_key": "business:receipt:create", "business_task_id": 1, "operation": "create"},
        ),
    ),
)
def test_delivery_receipt_and_claim_ledger_finish_together(
    tmp_path: Path, kind: str, enqueue: str, get_row: str, adapter_type, arguments: dict,
) -> None:
    store = _target_store(tmp_path)
    getattr(store, enqueue)(**arguments)
    adapter = adapter_type(store, owner_alive=lambda _pid: False)
    envelope = adapter.claim(
        NOW, owner="dispatcher", owner_pid=44, lease=timedelta(minutes=5)
    )
    assert envelope is not None
    receipt = '{"provider_task_id":"provider-123"}'

    adapter.finish_delivery(
        envelope, owner="dispatcher", now=NOW, status="completed", receipt_json=receipt
    )

    item = getattr(store, get_row)(int(envelope.source_id))
    assert item["status"] == "completed"
    assert item["receipt_json"] == receipt
    assert item["completed_at"]
    with store._connect() as db:
        lease = db.execute(
            "select owner,lease_expires_at,terminal_at from (" + state_projection(
                "dispatcher_claim_leases", include_rowid=True
            ) + ") where adapter_name=? and source_id=?",
            (adapter.name, envelope.source_id),
        ).fetchone()
    assert tuple(lease) == ("", "", "2026-10-17 12:00:00")


@pytest.mark.parametrize(
    ("kind", "parent_field", "parent_id", "reconcile"),
    (
        ("task_todo_sync_outbox", "work_todo_id", 1, "reconcile_unknown_task_todo_sync_outbox"),
        (
            "business_task_todo_sync_outbox",
            "business_task_id",
            1,
            "reconcile_unknown_business_task_todo_sync_outbox",
        ),
    ),
)
def test_unknown_create_reconciliation_uses_actions(
    tmp_path: Path, kind: str, parent_field: str, parent_id: int, reconcile: str,
) -> None:
    store = _target_store(tmp_path)
    with store._immediate_write_transaction() as db:
        action_id = insert_sync_action(
            db,
            kind,
            {
                "operation_key": f"unknown:{kind}",
                parent_field: parent_id,
                "operation": "create",
                "status": "unknown",
                "evidence_json": '{"source":"existing"}',
            },
        ).lastrowid
        link_table = (
            "work_todo_dingtalk_links"
            if kind == "task_todo_sync_outbox"
            else "business_task_dingtalk_links"
        )
        parent_column = parent_field
        insert_sync_action(
            db,
            link_table,
            {parent_column: parent_id, "dingtalk_task_id": "", "status": "creating"},
        )

    assert getattr(store, reconcile)(
        outbox_id=int(action_id), provider_absent_evidence="provider read confirms absent"
    )
    item = getattr(
        store,
        "get_task_todo_sync_outbox"
        if kind == "task_todo_sync_outbox"
        else "get_business_task_todo_sync_outbox",
    )(int(action_id))
    assert item["status"] == "queued"
    if kind == "task_todo_sync_outbox":
        assert json.loads(item["evidence_json"]) == {
            "source": "existing",
            "provider_absent_reconciliation": "provider read confirms absent",
        }
    with store._connect() as db:
        link = db.execute(
            f"select status from ({SYNC_ACTION_PROJECTIONS[link_table]})"
        ).fetchone()
    assert link["status"] == "cancelled"


@pytest.mark.parametrize(
    ("kind", "parent_field", "parent_id", "complete", "get_row", "link_table"),
    (
        (
            "task_todo_sync_outbox",
            "work_todo_id",
            1,
            "complete_unknown_task_todo_sync_outbox_from_receipt",
            "get_task_todo_sync_outbox",
            "work_todo_dingtalk_links",
        ),
        (
            "business_task_todo_sync_outbox",
            "business_task_id",
            1,
            "complete_unknown_business_task_todo_sync_outbox_from_receipt",
            "get_business_task_todo_sync_outbox",
            "business_task_dingtalk_links",
        ),
    ),
)
def test_unknown_provider_receipt_completion_uses_actions(
    tmp_path: Path,
    kind: str,
    parent_field: str,
    parent_id: int,
    complete: str,
    get_row: str,
    link_table: str,
) -> None:
    store = _target_store(tmp_path)
    with store._immediate_write_transaction() as db:
        action_id = insert_sync_action(
            db,
            kind,
            {
                "operation_key": f"receipt:{kind}",
                parent_field: parent_id,
                "operation": "create",
                "status": "unknown",
            },
        ).lastrowid
        insert_sync_action(
            db,
            link_table,
            {parent_field: parent_id, "dingtalk_task_id": "provider-123", "status": "creating"},
        )

    assert getattr(store, complete)(
        outbox_id=int(action_id), provider_readback_json='{"done":false,"title":"Task"}'
    )
    item = getattr(store, get_row)(int(action_id))
    assert item["status"] == "completed"
    assert json.loads(item["receipt_json"]) == {
        "link_id": 1,
        "dingtalk_task_id": "provider-123",
    }


def test_business_unknown_create_receipt_scan_uses_actions(tmp_path: Path) -> None:
    store = _target_store(tmp_path)
    with store._immediate_write_transaction() as db:
        action_id = insert_sync_action(
            db,
            "business_task_todo_sync_outbox",
            {
                "operation_key": "business:unknown-receipt",
                "business_task_id": 1,
                "operation": "create",
                "status": "unknown",
            },
        ).lastrowid
        insert_sync_action(
            db,
            "business_task_dingtalk_links",
            {"business_task_id": 1, "dingtalk_task_id": "provider-123", "status": "active"},
        )

    rows = store.list_unknown_business_task_todo_creates_with_receipts()

    assert [tuple(row) for row in rows] == [(int(action_id), 1, "provider-123")]
