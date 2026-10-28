from contextvars import ContextVar
import base64
import hashlib
import json
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


def test_actual_workbench_executor_routes_to_native_runtime_attempt_with_fake_cli(tmp_path: Path):
    from datetime import UTC, datetime, timedelta

    from app.agent_runtime_config import load_runtime_config
    from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
    from app.agent_runtime_router import AgentRuntimeRouter, RoutedCodexExecution
    from app.codex_runtime_adapter import CodexRuntimeAdapter
    from app.process_runner import ProcessRunResult
    from app.workbench.executor import WorkbenchExecutor
    from app.workbench.runtime import RuntimeRegistry
    from app.workbench.service_runtime import ServiceWorkbenchRuntime

    store = _target_store(tmp_path)
    parent = store.create_task(title="Producer integration", runtime_kind="codex")
    turn = store.create_turn(parent.id, user_text="native answer", client_request_id=str(uuid4()))
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"})
    now = datetime.now(UTC)
    router = AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots={
            route.name: RuntimeCapabilitySnapshot(
                route_name=route.name, healthy=True, checked_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=5)).isoformat(),
            )
            for route in config.routes
        },
        now=lambda: now,
    )

    def fake_cli(_command, **_kwargs):
        stream = "\n".join((
            json.dumps({"type": "thread.started", "thread_id": "fake-session"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "done"},
            }),
            json.dumps({"type": "turn.completed"}),
        ))
        return ProcessRunResult(returncode=0, stdout=stream, stderr="")

    def routed_factory(**kwargs):
        return RoutedCodexExecution(
            store=store,
            config=config,
            router=router,
            adapter=CodexRuntimeAdapter(kwargs["workspace"], config, codex_bin="fake-codex"),
            executor=fake_cli,
            session_line_counter=lambda _session_id: 0,
        )

    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path, routed_execution_factory=routed_factory,
    )
    executor = WorkbenchExecutor(
        store, RuntimeRegistry((runtime,)), workspace=tmp_path,
        heartbeat_interval_seconds=10,
    )
    try:
        assert executor.run_once() == [turn.id]
        completed = store.get_turn(turn.id)
        assert completed is not None and completed.status.value == "completed"
        attempts = store.list_runtime_operation_attempts("workbench", turn.id)
        assert len(attempts) == 1
        assert attempts[0].workload_kind == "workbench"
        assert attempts[0].workload_key == turn.id
        assert attempts[0].session_id == "fake-session"
    finally:
        executor.close()


def test_native_event_hydration_selects_attempt_transcript_ranges_and_handles_missing_source(
    tmp_path: Path, monkeypatch,
):
    from app.workbench import native_events

    store = _target_store(tmp_path)
    parent = store.create_task(title="Attempt transcript history", runtime_kind="codex")
    turn = store.create_turn(
        parent.id, user_text="history", client_request_id=str(uuid4())
    )
    running = store.claim_next_turn(owner="worker", now="2026-10-23T12:00:00+00:00")
    assert running is not None
    store.append_event(
        turn.id, sequence=2, event_type="text_delta", payload={"native_ordinal": 2},
        owner="worker", now="2026-10-23T12:00:01+00:00",
    )
    store.complete_turn(
        turn.id, status="completed", owner="worker", now="2026-10-23T12:00:02+00:00"
    )

    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)

    def session_file(session_id: str, before: str, selected: str, after: str) -> None:
        records = [
            {"type": "session_meta", "payload": {"id": session_id}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": before}],
            }},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": selected}],
            }},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": after}],
            }},
        ]
        (session_dir / f"{session_id}.jsonl").write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )

    session_file("attempt-one", "outside-one", "first-attempt", "outside-two")
    session_file("attempt-two", "outside-three", "second-attempt", "outside-four")
    monkeypatch.setattr(native_events, "_codex_home", lambda: codex_home)

    with store._connect() as db:
        base = (
            "workload_kind,workload_key,attempt_number,route_name,runtime_kind,"
            "credential_mode,model,session_mode,source_session_id,attempt_purpose,"
            "validation_retry_policy_id,validation_result_schema_id,session_id,"
            "transcript_reference,transcript_start,transcript_end,status,failure_class,"
            "failure_code,failover_permitted,lease_owner,lease_expires_at,"
            "first_effect_started_at,result_schema_id,started_at,finished_at,created_at,updated_at"
        )
        for number, session_id, start, end in (
            (1, "attempt-one", 1, 4), (2, "attempt-two", 4, 7),
        ):
            values = (
                "workbench", turn.id, number, "codex_oauth", "codex_cli", "local_oauth",
                "gpt-test", "fresh", "", "normal", "", "", session_id,
                f"codex_session:{session_id}", start, end, "completed", "", "", 0,
                "", "", "", "workbench.result.v1", "", "", "", "",
            )
            db.execute(
                f"insert into runtime_attempts({base}) values({','.join('?' for _ in values)})",
                values,
            )

    selected_event = next(
        event for event in store.events_after(turn.id) if event.event_type == "text_delta"
    )
    assert selected_event.payload == {"text": "second-attempt"}

    with store._connect() as db:
        db.execute(
            "update runtime_attempts set session_id='missing-session',"
            "transcript_reference='codex_session:missing-session' "
            "where workload_kind='workbench' and workload_key=? and attempt_number=2",
            (turn.id,),
        )
    unavailable_event = next(
        event for event in store.events_after(turn.id) if event.event_type == "text_delta"
    )
    assert unavailable_event.payload == {"text": native_events.UNAVAILABLE}
