from contextvars import ContextVar
import base64
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.task_entity_storage import create_task
from app.task_run_entity_assembly import task_run_entity_assembly_objects
from app.task_entity_inverse_files import (
    capture_workbench_attachment_files, finalize_workbench_attachment_inverse_files,
    finalize_workbench_attachment_forward_files, rollback_staged_workbench_attachment_files,
    stage_workbench_attachment_forward_files,
    stage_workbench_attachment_inverse_files,
)
from app.task_entity_schema import (
    capture_task_entities, migrate_task_entities, restore_task_entities,
)
from app.task8_storage import register_task8_functions
from app.workbench.api import register_workbench_routes
from app.workbench.store import WorkbenchConflictError, WorkbenchStore
from app.task_lifecycle_storage import source_assignments, source_insert
from app.storage_payload import TASK_RECORD_TYPES
from tests.test_task_entity_schema import _source


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


def test_native_turn_lifecycle_events_and_artifacts_use_canonical_task_owner(tmp_path: Path):
    store = _target_store(tmp_path)
    parent = store.create_task(title="Turn lifecycle", runtime_kind="codex")
    request_id = str(uuid4())
    created = store.create_turn(parent.id, user_text="hello", client_request_id=request_id)
    assert created.id.isdecimal() and created.task_id == parent.id
    assert store.create_turn(parent.id, user_text="hello", client_request_id=request_id) == created
    with pytest.raises(WorkbenchConflictError, match="client_request_id"):
        store.create_turn(parent.id, user_text="different", client_request_id=request_id)

    running = store.claim_next_turn(owner="worker", now="2026-10-23T12:00:00+00:00")
    assert running is not None and running.id == created.id and running.status.value == "running"
    event = store.append_event(
        running.id, sequence=2, event_type="text_delta", payload={"text": "hello"},
        owner="worker", now="2026-10-23T12:00:01+00:00",
    )
    assert event.turn_id == created.id
    artifact, artifact_event = store.append_artifact_event(
        running.id, sequence=3, label="result", path="/tmp/result.txt",
        media_type="text/plain", owner="worker", now="2026-10-23T12:00:02+00:00",
    )
    assert artifact.id.isdecimal() and artifact.turn_id == created.id
    assert artifact_event.payload["artifact_id"] == artifact.id
    assert store.list_artifacts(parent.id) == [artifact]
    finished = store.complete_turn(
        running.id, status="completed", owner="worker", now="2026-10-23T12:00:03+00:00"
    )
    assert finished.status.value == "completed"
    events = store.events_after(running.id)
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    with store._connect() as db:
        owner = db.execute(
            "select kind,status from tasks where id=?", (int(created.id),)
        ).fetchone()
        assert tuple(owner) == ("workbench_turn", "completed")
        assert db.execute("select count(*) from runs").fetchone()[0] == 0
        assert db.execute("select count(*) from workbench_events").fetchone()[0] == 4


def test_native_turn_lease_recovery_stop_race_and_stream_snapshot(tmp_path: Path):
    store = _target_store(tmp_path)
    parent = store.create_task(title="Lease and stop", runtime_kind="codex")
    turn = store.create_turn(parent.id, user_text="hello", client_request_id=str(uuid4()))
    running = store.claim_next_turn(
        owner="worker-a", lease_seconds=10, now="2026-10-23T12:00:00+00:00"
    )
    assert running is not None and running.id == turn.id
    store.renew_turn_lease(
        turn.id, owner="worker-a", lease_seconds=10, now="2026-10-23T12:00:05+00:00"
    )
    with store._connect() as db:
        lease = db.execute("select lease_expires_at from tasks where id=?", (int(turn.id),)).fetchone()[0]
    assert lease == "2026-10-23 12:00:15"
    assert store.recover_expired_turns(now="2026-10-23T12:00:14+00:00") == 0
    recovered = store.recover_expired_turns(now="2026-10-23T12:00:16+00:00")
    assert recovered == 1
    reclaimed = store.claim_next_turn(
        owner="worker-b", lease_seconds=10, now="2026-10-23T12:00:17+00:00"
    )
    assert reclaimed is not None and reclaimed.id == turn.id
    snapshot_events, snapshot_turn = store.event_stream_snapshot(turn.id, after_id=0)
    assert [event.event_type for event in snapshot_events] == ["status_changed"]
    assert snapshot_turn is not None and snapshot_turn.id == turn.id
    stopped = store.request_stop(
        turn.id, owner="worker-b", now="2026-10-23T12:00:18+00:00"
    )
    assert stopped.status.value == "stopped"
    with store._connect() as db:
        row = db.execute(
            "select status,updated_at from tasks where id=?", (int(turn.id),)
        ).fetchone()
    assert tuple(row) == ("stopped", "2026-10-23 12:00:18")
    with pytest.raises(ValueError, match="invalid turn transition"):
        store.complete_turn(
            turn.id, status="completed", owner="worker-b", now="2026-10-23T12:00:19+00:00"
        )


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


def test_native_reconciliation_preserves_target_injected_source_style_locator(tmp_path: Path):
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


def test_native_attachment_inverse_stages_source_locator_and_preserves_bytes(tmp_path: Path, monkeypatch):
    from app.task20_definitions import task20_projection

    path = tmp_path / "candidate.sqlite3"
    source = _source()
    parent_uuid = str(uuid4())
    control_uuid = str(uuid4())
    source.execute("update tasks set legacy_key=? where id=202", (parent_uuid,))
    source_attachment = source.execute(
        "select id from tasks where _kind=?", (TASK_RECORD_TYPES.code("workbench_attachments"),)
    ).fetchone()[0]
    control_path = tmp_path / "workbench" / "attachments" / parent_uuid / control_uuid
    control_path.parent.mkdir(parents=True)
    control_bytes = b"captured control\x00"
    control_path.write_bytes(control_bytes)
    values = source_assignments("workbench_attachments", {
        "id": control_uuid,
        "size_bytes": len(control_bytes),
        "content_sha256": hashlib.sha256(control_bytes).hexdigest(),
        "storage_path": str(control_path),
    }, {}, payload_source="tasks.payload_json", source_alias="tasks")
    source.execute(
        f"update tasks set {values.sql} where _kind=? and id=?",
        (*values.parameters, TASK_RECORD_TYPES.code("workbench_attachments"), source_attachment),
    )
    source.commit()
    disk = sqlite3.connect(path)
    source.backup(disk)
    disk.close()
    source.close()

    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, metadata)
        db.commit()

    store = object.__new__(WorkbenchStore)
    store.path = path
    store.busy_timeout_seconds = 30
    store.busy_timeout_milliseconds = 30_000
    store._read_snapshot_connection = ContextVar("workbench_inverse_snapshot", default=None)
    store._reused_connection = ContextVar("workbench_inverse_reused", default=None)
    empty_parent = store.get_task("202")
    new_parent = store.create_task(title="new inverse parent", runtime_kind="codex")
    empty_attachment = store.save_attachment(
        empty_parent.id, client_request_id=str(uuid4()), filename="empty-parent.bin",
        media_type="application/octet-stream", content=b"empty parent native file",
    )
    new_attachment = store.save_attachment(
        new_parent.id, client_request_id=str(uuid4()), filename="new-parent.bin",
        media_type="application/octet-stream", content=b"new parent native file",
    )
    managed_root = tmp_path / "workbench" / "attachments"
    with store._connect() as db:
        snapshot = capture_workbench_attachment_files(db, managed_root)
        original_paths = {Path(row["native_path"]) for row in snapshot["attachments"]}
        import app.task_entity_inverse_files as inverse_files
        real_link = inverse_files.os.link
        attempts = 0

        def fail_second_link(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise OSError("injected second hardlink failure")
            return real_link(*args, **kwargs)

        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        failed_inverse = restore_task_entities(db, metadata, forward)
        monkeypatch.setattr(inverse_files.os, "link", fail_second_link)
        try:
            stage_workbench_attachment_inverse_files(
                db, managed_root, snapshot, failed_inverse,
            )
        except OSError as exc:
            assert "injected second hardlink failure" in str(exc)
        else:
            raise AssertionError("injected Workbench hardlink failure was not raised")
        db.rollback()
        monkeypatch.setattr(inverse_files.os, "link", real_link)
        assert all(path.exists() for path in original_paths)
        assert attempts == 2
        assert all(path.is_file() for path in original_paths)

    with store._connect() as db:
        snapshot = capture_workbench_attachment_files(db, managed_root)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        interrupted_inverse = restore_task_entities(db, metadata, forward)
        interrupted_plan = stage_workbench_attachment_inverse_files(
            db, managed_root, snapshot, interrupted_inverse,
        )
        db.rollback()
        rollback_staged_workbench_attachment_files(
            interrupted_plan, interrupted_inverse["inverse_identity"], managed_root,
        )
        for item in interrupted_plan["attachments"]:
            assert Path(item["native_path"]).exists()
            if item["staged"]:
                assert not Path(item["source_path"]).exists()

    with store._connect() as db:
        snapshot = capture_workbench_attachment_files(db, managed_root)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        inverse = restore_task_entities(db, metadata, forward)
        plan = stage_workbench_attachment_inverse_files(db, managed_root, snapshot, inverse)
        try:
            finalize_workbench_attachment_inverse_files(
                db, plan, inverse["inverse_identity"], managed_root,
            )
        except ValueError as exc:
            assert "committed database state" in str(exc)
        else:
            raise AssertionError("inverse files finalized before the caller committed")
        assert all(path.exists() for path in original_paths)
        db.commit()
        real_unlink = Path.unlink
        unlink_count = 0

        def interrupt_after_first_cleanup(target, *args, **kwargs):
            nonlocal unlink_count
            unlink_count += 1
            if unlink_count == 2:
                raise OSError("injected inverse finalization interruption")
            return real_unlink(target, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", interrupt_after_first_cleanup)
        try:
            finalize_workbench_attachment_inverse_files(
                db, plan, inverse["inverse_identity"], managed_root,
            )
        except OSError as exc:
            assert "injected inverse finalization interruption" in str(exc)
        else:
            raise AssertionError("inverse finalization interruption was not raised")
        assert unlink_count == 2
        assert sum(path.exists() for path in original_paths) == len(original_paths) - 1
        monkeypatch.setattr(Path, "unlink", real_unlink)
        finalize_workbench_attachment_inverse_files(
            db, plan, inverse["inverse_identity"], managed_root,
        )

    identities = inverse["inverse_identity"]["workbench_attachment_identities"]
    identity_by_canonical = {row["canonical_id"]: row for row in identities}
    with store._connect() as db:
        for saved in (empty_attachment, new_attachment):
            identity = identity_by_canonical[int(saved.id)]
            assert str(UUID(identity["source_public_id"])) == identity["source_public_id"]
            attachment = db.execute(
                "select storage_path from (" + task20_projection("workbench_attachments") + ") where id=?",
                (identity["source_public_id"],),
            ).fetchone()
            assert Path(attachment[0]).read_bytes() == (
                b"empty parent native file" if saved == empty_attachment else b"new parent native file"
            )
        control = db.execute(
            "select storage_path from (" + task20_projection("workbench_attachments") + ") where id=?",
            (control_uuid,),
        ).fetchone()
        assert control[0] == str(control_path)
        assert control_path.read_bytes() == control_bytes

    portable_identity = json.loads(json.dumps(inverse["inverse_identity"]))
    with store._connect() as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        before_rows = db.execute("select id,parent_id,payload_json from tasks order by id").fetchall()
        before_files = {
            str(path): path.read_bytes() for path in managed_root.rglob("*") if path.is_file()
        }
        swapped_identity = json.loads(json.dumps(portable_identity))
        first_id, second_id = swapped_identity["workbench_attachment_identities"]
        first_id["source_public_id"], second_id["source_public_id"] = (
            second_id["source_public_id"], first_id["source_public_id"],
        )
        wrong_parent = json.loads(json.dumps(portable_identity))
        parent_entry = wrong_parent["workbench_attachment_files"][0]
        parent_entry["source_parent_public_id"] = str(uuid4())
        parent_entry["source_path"] = str(
            managed_root / parent_entry["source_parent_public_id"] / parent_entry["source_public_id"]
        )
        wrong_path = json.loads(json.dumps(portable_identity))
        path_entry = wrong_path["workbench_attachment_files"][0]
        path_entry["source_path"] = str(
            managed_root / path_entry["source_parent_public_id"] / str(uuid4())
        )
        wrong_canonical_parent = json.loads(json.dumps(portable_identity))
        canonical_entry = wrong_canonical_parent["workbench_attachment_files"][0]
        canonical_entry["parent_canonical_id"] += 10000
        canonical_entry["native_path"] = str(
            managed_root / str(canonical_entry["parent_canonical_id"])
            / Path(canonical_entry["native_path"]).name
        )
        for tampered in (swapped_identity, wrong_parent, wrong_path, wrong_canonical_parent):
            try:
                stage_workbench_attachment_forward_files(db, managed_root, tampered)
            except ValueError:
                pass
            else:
                raise AssertionError("tampered Workbench file handoff was accepted")
            assert db.execute("select id,parent_id,payload_json from tasks order by id").fetchall() == before_rows
            assert {str(path): path.read_bytes() for path in managed_root.rglob("*") if path.is_file()} == before_files
        db.rollback()

    with store._connect() as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        inverse_metadata = capture_task_entities(db)
        forward_plan = stage_workbench_attachment_forward_files(
            db, managed_root, portable_identity,
        )
        forward_again = migrate_task_entities(
            db, inverse_metadata, source_context=inverse["source_context"],
            inverse_identity=portable_identity,
        )
        db.commit()
    with store._connect() as db:
        real_unlink = Path.unlink
        unlink_count = 0

        def interrupt_forward_after_first_cleanup(target, *args, **kwargs):
            nonlocal unlink_count
            unlink_count += 1
            if unlink_count == 2:
                raise OSError("injected forward finalization interruption")
            return real_unlink(target, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", interrupt_forward_after_first_cleanup)
        try:
            finalize_workbench_attachment_forward_files(
                db, forward_plan, portable_identity, managed_root,
            )
        except OSError as exc:
            assert "injected forward finalization interruption" in str(exc)
        else:
            raise AssertionError("forward finalization interruption was not raised")
        assert unlink_count == 2
        monkeypatch.setattr(Path, "unlink", real_unlink)
        finalize_workbench_attachment_forward_files(
            db, forward_plan, portable_identity, managed_root,
        )
        for saved in (empty_attachment, new_attachment):
            row = db.execute(
                "select storage_path from workbench_attachments where id=?", (int(saved.id),)
            ).fetchone()
            assert Path(row[0]).read_bytes() in {
                b"empty parent native file", b"new parent native file",
            }

        snapshot = capture_workbench_attachment_files(db, managed_root)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        inverse_again = restore_task_entities(db, inverse_metadata, forward_again)
        plan_again = stage_workbench_attachment_inverse_files(
            db, managed_root, snapshot, inverse_again,
        )
        db.commit()
        finalize_workbench_attachment_inverse_files(
            db, plan_again, inverse_again["inverse_identity"], managed_root,
        )
    assert len(inverse_again["inverse_identity"]["workbench_attachment_files"]) == 2


def test_highest_reconciled_attachment_identity_is_not_reused_across_portable_inverse(
    tmp_path: Path,
):
    from app.task20_definitions import task20_projection

    path = tmp_path / "store.sqlite3"
    source = _source()
    parent_uuid = str(uuid4())
    attachment_uuid = str(uuid4())
    source.execute("update tasks set legacy_key=? where id=202", (parent_uuid,))
    source_attachment_id = source.execute(
        "select id from tasks where _kind=?",
        (TASK_RECORD_TYPES.code("workbench_attachments"),),
    ).fetchone()[0]
    control_uuid = str(uuid4())
    control_path = tmp_path / "workbench" / "attachments" / parent_uuid / control_uuid
    control_path.parent.mkdir(parents=True)
    control_bytes = b"lower captured attachment retained"
    control_path.write_bytes(control_bytes)
    control_fields = source_assignments(
        "workbench_attachments",
        {
            "id": control_uuid,
            "size_bytes": len(control_bytes),
            "content_sha256": hashlib.sha256(control_bytes).hexdigest(),
            "storage_path": str(control_path),
        },
        {},
        payload_source="tasks.payload_json",
        source_alias="tasks",
    )
    source.execute(
        f"update tasks set {control_fields.sql} where _kind=? and id=?",
        (*control_fields.parameters, TASK_RECORD_TYPES.code("workbench_attachments"), source_attachment_id),
    )
    from tests.test_task20_definitions import payload
    old_path = tmp_path / "workbench" / "attachments" / parent_uuid / attachment_uuid
    old_bytes = b"migrated highest attachment before retirement"
    old_path.write_bytes(old_bytes)
    high_values = payload(source, "workbench_attachments")
    high_values.update(
        id=attachment_uuid,
        client_request_id=str(uuid4()),
        filename="retired-highest.bin",
        media_type="application/octet-stream",
        size_bytes=len(old_bytes),
        content_sha256=hashlib.sha256(old_bytes).hexdigest(),
        storage_path=str(old_path),
        created_at="2026-10-23T00:00:00+00:00",
    )
    columns, expressions, parameters = source_insert("workbench_attachments", high_values, {})
    source.execute(
        'insert into tasks("_kind","id","legacy_key","parent_id",'
        + ",".join('"' + column + '"' for column in columns) + ") values(?,?,?, ?,"
        + ",".join(expressions) + ")",
        (TASK_RECORD_TYPES.code("workbench_attachments"), 235, "235", 202, *parameters),
    )
    source.commit()
    disk = sqlite3.connect(path)
    source.backup(disk)
    disk.close()
    source.close()

    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.commit()

    store = object.__new__(WorkbenchStore)
    store.path = path
    store.busy_timeout_seconds = 30
    store.busy_timeout_milliseconds = 30_000
    store._read_snapshot_connection = ContextVar("workbench_reuse_snapshot", default=None)
    store._reused_connection = ContextVar("workbench_reuse_connection", default=None)
    with store._connect() as db:
        control_owner = db.execute(
            "select id,storage_path,content_sha256 from workbench_attachments "
            "where filename<>'retired-highest.bin'"
        ).fetchone()
        old_owner = db.execute(
            "select id,workbench_task_id,client_request_id,storage_path "
            "from workbench_attachments where filename='retired-highest.bin'"
        ).fetchone()
        assert old_owner[2] is not None
        retired_canonical_id = int(old_owner[0])
        migrated_parent_id = str(old_owner[1])
        old_identity = db.execute(
            "select public_key from task_run_identity_map "
            "where source_kind='workbench_attachments' and target_id=?",
            (retired_canonical_id,),
        ).fetchone()
        assert old_identity[0] == attachment_uuid

    old_path.unlink()
    store._reconcile_attachments()
    remaining = store.list_attachments(migrated_parent_id)
    assert [item.id for item in remaining] == [str(control_owner[0])]
    assert Path(control_owner[1]).read_bytes() == control_bytes
    assert hashlib.sha256(Path(control_owner[1]).read_bytes()).hexdigest() == control_owner[2]

    class Executor:
        workspace = tmp_path

        def run_once(self):
            return []

    class Registry:
        def kinds(self):
            return ("codex",)

    app = FastAPI()
    register_workbench_routes(
        app,
        store,
        Executor(),
        Registry(),
        tmp_path / "assets",
        mutation_guard=lambda _request: None,
        scheduler_interval_seconds=60,
    )
    request_id = str(uuid4())
    with TestClient(app) as client:
        response = client.post(
            f"/api/workbench/tasks/{migrated_parent_id}/attachments",
            json={
                "client_request_id": request_id,
                "filename": "replacement.bin",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"replacement attachment").decode(),
            },
        )
    assert response.status_code == 201, response.text
    replacement_id = response.json()["id"]
    assert replacement_id.isdecimal()
    with store._connect() as db:
        assert db.execute("select client_request_id from workbench_attachments where id=?",
                          (int(replacement_id),)).fetchone()[0] == request_id

    managed_root = tmp_path / "workbench" / "attachments"
    with store._attachment_lock(create_workbench=False):
        with store._connect() as db:
            snapshot = capture_workbench_attachment_files(db, managed_root)
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            inverse = restore_task_entities(db, source_metadata, forward)
            inverse_plan = stage_workbench_attachment_inverse_files(
                db, managed_root, snapshot, inverse,
            )
            db.commit()
            finalize_workbench_attachment_inverse_files(
                db, inverse_plan, inverse["inverse_identity"], managed_root,
            )

    portable = json.loads(json.dumps(inverse["inverse_identity"]))
    identity = next(
        row for row in portable["workbench_attachment_identities"]
        if row["canonical_id"] == int(replacement_id)
    )
    new_source_public_id = identity["source_public_id"]
    with store._connect() as db:
        replacement = db.execute(
            "select id,client_request_id,filename,media_type,size_bytes,content_sha256 from ("
            + task20_projection("workbench_attachments") + ") "
            "where id=?",
            (new_source_public_id,),
        ).fetchone()
        assert replacement is not None
    assert tuple(replacement[1:]) == (
        request_id, "replacement.bin", "text/plain", len(b"replacement attachment"),
        hashlib.sha256(b"replacement attachment").hexdigest(),
    )

    with store._attachment_lock(create_workbench=False):
        with store._connect() as db:
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            current_source = capture_task_entities(db)
            forward_plan = stage_workbench_attachment_forward_files(
                db, managed_root, portable,
            )
            next_forward = migrate_task_entities(
                db,
                current_source,
                source_context=inverse["source_context"],
                inverse_identity=portable,
            )
            db.commit()
        with store._connect() as db:
            finalize_workbench_attachment_forward_files(
                db, forward_plan, portable, managed_root,
            )

    with store._connect() as db:
        reread = db.execute(
            "select id,client_request_id,content_sha256,storage_path from workbench_attachments "
            "where id=?",
            (int(replacement_id),),
        ).fetchone()
        assert reread is not None
        assert reread[1] == request_id
        assert hashlib.sha256(Path(reread[3]).read_bytes()).hexdigest() == reread[2]
        assert next_forward["identity_edges"]

    assert int(replacement_id) > retired_canonical_id, (
        "reconciled highest attachment ID was reused, allowing its old sparse source "
        "identity to label a different upload"
    )
    assert new_source_public_id != attachment_uuid

    replacement_facts = _attachment_facts(store, replacement_id)
    replacement_path = Path(replacement_facts["storage_path"])
    replacement_path.unlink()
    store._reconcile_attachments()

    with store._attachment_lock(create_workbench=False):
        with store._connect() as db:
            snapshot = capture_workbench_attachment_files(db, managed_root)
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            inverse_again = restore_task_entities(db, source_metadata, next_forward)
            inverse_plan_again = stage_workbench_attachment_inverse_files(
                db, managed_root, snapshot, inverse_again,
            )
            db.commit()
            finalize_workbench_attachment_inverse_files(
                db, inverse_plan_again, inverse_again["inverse_identity"], managed_root,
            )
    portable_again = json.loads(json.dumps(inverse_again["inverse_identity"]))
    with store._attachment_lock(create_workbench=False):
        with store._connect() as db:
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            source_after_second_inverse = capture_task_entities(db)
            forward_plan_again = stage_workbench_attachment_forward_files(
                db, managed_root, portable_again,
            )
            forward_again = migrate_task_entities(
                db, source_after_second_inverse,
                source_context=inverse_again["source_context"],
                inverse_identity=portable_again,
            )
            db.commit()
        with store._connect() as db:
            finalize_workbench_attachment_forward_files(
                db, forward_plan_again, portable_again, managed_root,
            )
            seq = db.execute(
                "select seq from sqlite_sequence where name='workbench_attachments'"
            ).fetchone()[0]
            assert seq >= int(replacement_id)

    third_request_id = str(uuid4())
    with TestClient(app) as client:
        third_response = client.post(
            f"/api/workbench/tasks/{migrated_parent_id}/attachments",
            json={
                "client_request_id": third_request_id,
                "filename": "third.bin",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"third attachment").decode(),
            },
        )
    assert third_response.status_code == 201, third_response.text
    assert int(third_response.json()["id"]) > int(replacement_id)
    with store._connect() as db:
        third_row = db.execute(
            "select client_request_id,content_sha256 from workbench_attachments where id=?",
            (int(third_response.json()["id"]),),
        ).fetchone()
    assert tuple(third_row) == (
        third_request_id, hashlib.sha256(b"third attachment").hexdigest(),
    )
    assert forward_again["identity_edges"]


def test_native_artifact_download_route_uses_canonical_ids(tmp_path: Path):
    store = _target_store(tmp_path)
    parent = store.create_task(title="Download", runtime_kind="codex")
    created = store.create_turn(
        parent.id, user_text="make artifact", client_request_id=str(uuid4())
    )
    running = store.claim_next_turn(owner="worker", now="2026-10-23T12:00:00+00:00")
    assert running is not None and running.id == created.id
    output = tmp_path / "result.txt"
    output.write_text("native result", encoding="utf-8")
    artifact, _event = store.append_artifact_event(
        running.id, sequence=2, label="result", path=str(output),
        media_type="text/plain", owner="worker", now="2026-10-23T12:00:01+00:00",
    )

    class Executor:
        workspace = tmp_path

        def run_once(self):
            return []

    class Registry:
        def kinds(self):
            return ("codex",)

    app = FastAPI()
    scheduler = register_workbench_routes(
        app, store, Executor(), Registry(), tmp_path / "assets",
        mutation_guard=lambda _request: None, scheduler_interval_seconds=60,
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/workbench/tasks/{parent.id}/turns/{created.id}/artifacts/{artifact.id}/download"
            )
        assert response.status_code == 200
        assert response.content == b"native result"
    finally:
        scheduler.close()


def test_native_turn_sse_serves_database_snapshot_and_terminal_event(tmp_path: Path):
    store = _target_store(tmp_path)
    parent = store.create_task(title="SSE", runtime_kind="codex")
    turn = store.create_turn(parent.id, user_text="stream", client_request_id=str(uuid4()))
    store.request_stop(turn.id, now="2026-10-23T12:00:00+00:00")

    class Executor:
        workspace = tmp_path

        def run_once(self):
            return []

    class Registry:
        def kinds(self):
            return ("codex",)

    app = FastAPI()
    scheduler = register_workbench_routes(
        app, store, Executor(), Registry(), tmp_path / "assets",
        mutation_guard=lambda _request: None, sse_poll_seconds=0.01,
        scheduler_interval_seconds=60,
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/workbench/turns/{turn.id}/events/stream?after=0"
            )
        assert response.status_code == 200
        assert "event: status_changed" in response.text
        assert '"status":"stopped"' in response.text
    finally:
        scheduler.close()
