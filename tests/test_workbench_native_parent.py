from contextvars import ContextVar
import base64
import hashlib
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.task_entity_storage import create_task
from app.task_run_entity_assembly import task_run_entity_assembly_objects
from app.task8_storage import register_task8_functions
from app.workbench.api import register_workbench_routes
from app.workbench.store import WorkbenchConflictError, WorkbenchStore


def _target_store(tmp_path: Path) -> WorkbenchStore:
    path = tmp_path / "native.sqlite3"
    db = sqlite3.connect(path)
    db.execute("pragma foreign_keys=on")
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    db.close()

    # The candidate target is unactivated; bind the actual Store methods to
    # the assembled schema without running the still-source constructor.
    store = object.__new__(WorkbenchStore)
    store.path = path
    store.busy_timeout_seconds = 30
    store.busy_timeout_milliseconds = 30_000
    store._read_snapshot_connection = ContextVar("workbench_native_snapshot", default=None)
    store._reused_connection = ContextVar("workbench_native_reused", default=None)
    return store


def _seed_turn(store: WorkbenchStore, task_id: int, *, status: str) -> int:
    with store._connect() as db:
        now = db.execute("select current_timestamp").fetchone()[0]
        task = create_task(
            db,
            values={
                "kind": "workbench_turn",
                "status": status,
                "created_at": now,
                "updated_at": now,
                "lease_owner": "executor" if status == "running" else "",
                "lease_expires_at": "9999-12-31T23:59:59+00:00" if status == "running" else "",
            },
        )
        db.execute(
            "insert into workbench_turns(task_id,workbench_task_id,client_request_id,"
            "task_sequence,user_text,stop_requested,error_code,error_detail,resume_context,"
            "execution_run_id,runtime_quiesced_run_id,started_at,completed_at) "
            "values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task["id"], task_id, str(uuid4()), 1, "input", 0, "", "", "", "", "", "", ""),
        )
        return int(task["id"])


def test_native_parent_crud_state_cursor_and_archive_fence(tmp_path: Path):
    store = _target_store(tmp_path)
    task = store.create_task(title="  Native parent  ", runtime_kind="codex")

    assert task.id.isdecimal()
    assert store.get_task(task.id) == task
    assert store.get_task_summary(task.id) == (task, "idle")

    turn_id = _seed_turn(store, int(task.id), status="queued")
    assert store.get_task_summary(task.id)[1] == "queued"
    with pytest.raises(WorkbenchConflictError, match="active turn"):
        store.archive_task(task.id, now="2026-10-23T12:00:00+00:00")

    renamed = store.rename_task(task.id, title=" Renamed ")
    assert renamed.id == task.id
    assert renamed.title == "Renamed"
    page = store.list_tasks_with_state(limit=1)
    assert page[0][0].id == task.id
    assert page[0][1] == "queued"
    assert store.list_tasks_with_state(cursor=(renamed.updated_at, task.id)) == []

    with store._connect() as db:
        db.execute("update tasks set status='completed' where id=?", (turn_id,))
    archived = store.archive_task(task.id, now="2026-10-23T12:00:00+00:00")
    assert archived.archived_at == "2026-10-23 12:00:00"
    assert store.list_tasks_with_state() == []
    assert store.list_tasks_with_state(archived_only=True)[0][0].id == task.id

    second_task = store.create_task(title="Running parent", runtime_kind="codex")
    _seed_turn(store, int(second_task.id), status="running")
    with pytest.raises(WorkbenchConflictError, match="active turn"):
        store.archive_task(second_task.id, now="2026-10-23T12:00:00+00:00")


def test_native_attachment_owner_keeps_idempotency_metadata_path_and_bytes(tmp_path: Path):
    store = _target_store(tmp_path)
    task = store.create_task(title="Attachments", runtime_kind="codex")
    client_request_id = str(uuid4())
    content = b"native attachment bytes\x00\xff"

    saved = store.save_attachment(
        task.id,
        client_request_id=client_request_id,
        filename="evidence.bin",
        media_type="application/octet-stream",
        content=content,
    )
    listed = store.list_attachments(task.id)

    assert saved.id.isdecimal()
    assert saved.task_id == task.id
    assert listed == [saved]
    assert saved.size_bytes == len(content)
    assert Path(_attachment_facts(store, saved.id)["storage_path"]).read_bytes() == content
    assert store.save_attachment(
        task.id,
        client_request_id=client_request_id,
        filename="evidence.bin",
        media_type="application/octet-stream",
        content=content,
    ) == saved
    with pytest.raises(WorkbenchConflictError, match="client request ID"):
        store.save_attachment(
            task.id,
            client_request_id=client_request_id,
            filename="evidence.bin",
            media_type="application/octet-stream",
            content=content + b"!",
        )
    with store._connect() as db:
        row = db.execute(
            "select workbench_task_id,client_request_id,filename,media_type,size_bytes,"
            "content_sha256,storage_path from workbench_attachments where id=?",
            (int(saved.id),),
        ).fetchone()
    assert tuple(row) == (
        int(task.id), client_request_id, "evidence.bin", "application/octet-stream",
        len(content), hashlib.sha256(content).hexdigest(), row[6],
    )
    assert Path(row[6]).read_bytes() == content


def _attachment_facts(store: WorkbenchStore, attachment_id: str) -> dict[str, object]:
    with store._connect() as db:
        row = db.execute(
            "select * from workbench_attachments where id=?", (int(attachment_id),)
        ).fetchone()
        return dict(row)


def test_native_reconciliation_preserves_historical_storage_locator(tmp_path: Path):
    store = _target_store(tmp_path)
    task = store.create_task(title="Migrated parent", runtime_kind="codex")
    old_parent = "8fb4c9c5-a0e2-4e72-89d6-318f0921234d"
    old_attachment = "918b76f6-7092-4b81-b133-a103d66cbcdc"
    path = tmp_path / "workbench" / "attachments" / old_parent / old_attachment
    path.parent.mkdir(parents=True)
    content = b"legacy-locator-bytes"
    path.write_bytes(content)
    client_request_id = str(uuid4())

    with store._connect() as db:
        db.execute(
            "insert into workbench_attachments(workbench_task_id,client_request_id,filename,"
            "media_type,size_bytes,content_sha256,storage_path) values(?,?,?,?,?,?,?)",
            (int(task.id), client_request_id, "legacy.txt", "text/plain", len(content),
             hashlib.sha256(content).hexdigest(), str(path)),
        )
    store._reconcile_attachments()

    rows = store.list_attachments(task.id)
    assert len(rows) == 1
    assert rows[0].task_id == task.id
    facts = _attachment_facts(store, rows[0].id)
    assert facts["client_request_id"] == client_request_id
    assert facts["storage_path"] == str(path)
    assert facts["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert path.read_bytes() == content


def test_native_provider_session_update_uses_parent_from_native_turn(tmp_path: Path):
    store = _target_store(tmp_path)
    task = store.create_task(title="Session owner", runtime_kind="codex")
    turn_id = _seed_turn(store, int(task.id), status="running")

    updated = store.set_provider_session(
        str(turn_id),
        "opaque:provider/ref",
        owner="executor",
        now="2026-10-23T12:00:00+00:00",
    )

    assert updated.id == task.id
    assert updated.provider_session_ref == "opaque:provider/ref"
    assert updated.updated_at == "2026-10-23 12:00:00"


def test_existing_parent_routes_use_canonical_decimal_task_ids(tmp_path: Path):
    store = _target_store(tmp_path)

    class Executor:
        workspace = tmp_path

        def run_once(self):
            return []

    class Registry:
        def kinds(self):
            return ("codex",)

    app = FastAPI()
    scheduler = register_workbench_routes(
        app,
        store,
        Executor(),
        Registry(),
        tmp_path / "assets",
        mutation_guard=lambda _request: None,
        scheduler_interval_seconds=60,
    )
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/workbench/tasks",
                json={"title": "API parent", "runtime_kind": "codex"},
            )
            assert created.status_code == 201
            task = created.json()
            assert task["id"].isdecimal()
            task_id = task["id"]
            assert client.get(f"/api/workbench/tasks/{task_id}").json()["id"] == task_id
            assert client.patch(
                f"/api/workbench/tasks/{task_id}", json={"title": "API renamed"}
            ).json()["title"] == "API renamed"
            request_id = str(uuid4())
            uploaded = client.post(
                f"/api/workbench/tasks/{task_id}/attachments",
                json={
                    "client_request_id": request_id,
                    "filename": "api.txt",
                    "media_type": "text/plain",
                    "content_base64": base64.b64encode(b"api bytes").decode(),
                },
            )
            assert uploaded.status_code == 201
            assert uploaded.json()["id"].isdecimal()
            listed_attachments = client.get(
                f"/api/workbench/tasks/{task_id}/attachments"
            )
            assert listed_attachments.json()[0]["id"] == uploaded.json()["id"]
            assert listed_attachments.json()[0]["task_id"] == task_id
            created_second = client.post(
                "/api/workbench/tasks",
                json={"title": "API second", "runtime_kind": "codex"},
            )
            assert created_second.status_code == 201
            listed = client.get("/api/workbench/tasks?limit=1")
            next_cursor = listed.headers.get("x-next-cursor")
            assert next_cursor
            next_page = client.get(
                "/api/workbench/tasks?limit=1", params={"cursor": next_cursor}
            )
            assert next_page.status_code == 200
            assert listed.json()[0]["id"] != next_page.json()[0]["id"]
            assert all(
                row["id"].isdecimal()
                for row in (listed.json()[0], next_page.json()[0])
            )
            archived = client.post(f"/api/workbench/tasks/{task_id}/archive", json={})
            assert archived.status_code == 200
            assert archived.json()["id"] == task_id
    finally:
        scheduler.close()


def test_native_attachment_commit_failure_removes_file_and_metadata(tmp_path: Path):
    store = _target_store(tmp_path)
    task = store.create_task(title="Atomic attachment", runtime_kind="codex")
    request_id = str(uuid4())
    store._commit_attachment_metadata = lambda _db: (_ for _ in ()).throw(  # type: ignore[method-assign]
        sqlite3.OperationalError("simulated commit failure")
    )

    with pytest.raises(sqlite3.OperationalError, match="simulated commit failure"):
        store.save_attachment(
            task.id,
            client_request_id=request_id,
            filename="atomic.txt",
            media_type="text/plain",
            content=b"must roll back",
        )

    with store._connect() as db:
        assert db.execute(
            "select 1 from workbench_attachments where client_request_id=?", (request_id,)
        ).fetchone() is None
    attachment_root = tmp_path / "workbench" / "attachments"
    assert not list(attachment_root.rglob("*.tmp"))
    assert not [path for path in attachment_root.rglob("*") if path.is_file()]
