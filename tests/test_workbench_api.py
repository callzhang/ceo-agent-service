from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient

from app.audit_web import create_audit_app
from app.setup_wizard import SETUP_WIZARD_STEPS
from app.store import AutoReplyStore
from app.workbench.api import _ExecutionScheduler
from app.workbench.store import WorkbenchStore


def _client(tmp_path: Path) -> TestClient:
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id, status="done", summary="complete"
        )

    class NonExecutingExecutor:
        workspace = tmp_path

        def recover(self):
            return 0

        def run_once(self):
            return []

        def stop(self, turn_id):
            return store.request_stop(turn_id)

        def confirm(self, confirmation_id):
            raise AssertionError(f"unexpected confirmation: {confirmation_id}")

        def cancel(self, confirmation_id):
            raise AssertionError(f"unexpected cancellation: {confirmation_id}")

        def close(self):
            return True

    return TestClient(
        create_audit_app(
            tmp_path / "worker.sqlite3",
            workbench_asset_dir=tmp_path / "missing-assets",
            workbench_workspace=tmp_path,
            workbench_executor=NonExecutingExecutor(),
        ),
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    )


def test_task_turn_and_event_replay(tmp_path: Path):
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "New task", "runtime_kind": "codex"},
        ).json()
        turn = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "Inspect the repo", "client_request_id": "request-1"},
        ).json()

        response = client.get(
            f"/api/workbench/turns/{turn['id']}/events?after=0&limit=100"
        )

    assert response.status_code == 200
    assert response.json()[0]["event_type"] == "status_changed"
    assert response.json()[0]["payload"] == {"status": "queued"}


def test_cross_origin_mutation_is_rejected(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.post(
            "/api/workbench/tasks",
            json={"title": "Blocked", "runtime_kind": "codex"},
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403


def test_root_reports_missing_workbench_build_and_history_keeps_query(tmp_path: Path):
    with _client(tmp_path) as client:
        root = client.get("/", follow_redirects=False)
        history = client.get("/history?object_type=meeting&q=roadmap")

    assert root.status_code == 503
    assert "npm run build:workbench" in root.text
    assert history.status_code == 200
    assert "History" in history.text


def test_root_serves_exact_workbench_index_when_built(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    expected = b"<!doctype html><title>Workbench</title>"
    (assets / "index.html").write_bytes(expected)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id, status="done", summary="complete"
        )
    app = create_audit_app(
        store.path,
        workbench_asset_dir=assets,
        workbench_workspace=tmp_path,
    )

    with TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.content == expected
    assert response.headers["cache-control"] == "no-cache"


def test_attachment_is_strict_bounded_and_has_no_storage_path(tmp_path: Path):
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "Files", "runtime_kind": "codex"},
        ).json()
        uploaded = client.post(
            f"/api/workbench/tasks/{task['id']}/attachments",
            json={
                "filename": "notes.txt",
                "media_type": "text/plain",
                "content_base64": "aGVsbG8=",
            },
        )
        invalid = client.post(
            f"/api/workbench/tasks/{task['id']}/attachments",
            json={
                "filename": "notes.txt",
                "media_type": "text/plain",
                "content_base64": "%%%",
            },
        )
        whitespace = client.post(
            f"/api/workbench/tasks/{task['id']}/attachments",
            json={
                "filename": "notes.txt",
                "media_type": "text/plain",
                "content_base64": " aGVsbG8=",
            },
        )

    assert uploaded.status_code == 201
    assert uploaded.json()["size_bytes"] == 5
    assert "storage_path" not in uploaded.json()
    assert invalid.status_code == 400
    assert whitespace.status_code == 400


def test_task_archive_filter_is_explicit(tmp_path: Path):
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "Archived", "runtime_kind": "codex"},
        ).json()
        archived = client.post(f"/api/workbench/tasks/{task['id']}/archive", json={})
        active_list = client.get("/api/workbench/tasks?archived=active")
        archived_list = client.get("/api/workbench/tasks?archived=archived")
        all_list = client.get("/api/workbench/tasks?archived=all")

    assert archived.status_code == 200
    assert active_list.json() == []
    assert [item["id"] for item in archived_list.json()] == [task["id"]]
    assert [item["id"] for item in all_list.json()] == [task["id"]]


def test_nested_turn_and_confirmation_resources_do_not_leak_across_tasks(
    tmp_path: Path,
):
    with _client(tmp_path) as client:
        first = client.post(
            "/api/workbench/tasks",
            json={"title": "First", "runtime_kind": "codex"},
        ).json()
        second = client.post(
            "/api/workbench/tasks",
            json={"title": "Second", "runtime_kind": "codex"},
        ).json()
        turn = client.post(
            f"/api/workbench/tasks/{first['id']}/turns",
            json={"text": "Inspect", "client_request_id": "ownership-request"},
        ).json()
        response = client.get(f"/api/workbench/tasks/{second['id']}/turns/{turn['id']}")

    assert response.status_code == 404


def test_runtime_capabilities_and_stats_are_public_stable_models(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post(
            "/api/workbench/tasks",
            json={"title": "Stats", "runtime_kind": "codex"},
        )
        runtimes = client.get("/api/workbench/runtimes")
        stats = client.get("/api/workbench/stats")

    assert runtimes.status_code == 200
    assert runtimes.json()[0]["kind"] == "codex"
    assert set(runtimes.json()[0]) == {"kind", "capabilities"}
    assert stats.status_code == 200
    assert stats.json()["tasks"]["total"] == 1
    assert stats.json()["turns"] == {
        "queued": 0,
        "running": 0,
        "waiting_confirmation": 0,
        "completed": 0,
        "stopped": 0,
        "failed": 0,
    }
    assert "database_path" not in stats.json()


def test_artifact_download_checks_nested_ownership_and_does_not_expose_path(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Artifacts", runtime_kind="codex")
    other = store.create_task(title="Other", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Create report", client_request_id="artifact-request"
    )
    artifact_id = str(uuid4())
    artifact_path = tmp_path / "report.txt"
    artifact_path.write_text("report", encoding="utf-8")
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_artifacts (id, turn_id, label, path, media_type)
            values (?, ?, ?, ?, ?)
            """,
            (artifact_id, turn.id, "Report", str(artifact_path), "text/plain"),
        )

    with _client(tmp_path) as client:
        timeline = client.get(f"/api/workbench/tasks/{task.id}/timeline")
        download = client.get(
            f"/api/workbench/tasks/{task.id}/turns/{turn.id}/artifacts/{artifact_id}/download"
        )
        cross_task = client.get(
            f"/api/workbench/tasks/{other.id}/turns/{turn.id}/artifacts/{artifact_id}/download"
        )

    assert timeline.status_code == 200
    assert "path" not in timeline.json()["artifacts"][0]
    assert download.status_code == 200
    assert download.text == "report"
    assert cross_task.status_code == 404


def test_artifact_download_rejects_symlink(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Artifacts", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Create report", client_request_id="symlink-request"
    )
    target = tmp_path / "target.txt"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    artifact_id = str(uuid4())
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_artifacts (id, turn_id, label, path, media_type)
            values (?, ?, ?, ?, ?)
            """,
            (artifact_id, turn.id, "Report", str(link), "text/plain"),
        )

    with _client(tmp_path) as client:
        response = client.get(
            f"/api/workbench/tasks/{task.id}/turns/{turn.id}/artifacts/{artifact_id}/download"
        )

    assert response.status_code == 404


def test_artifact_download_rejects_symlinked_parent(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Artifacts", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Create report", client_request_id="parent-symlink-request"
    )
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    target = real_directory / "report.txt"
    target.write_text("private", encoding="utf-8")
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    artifact_id = str(uuid4())
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_artifacts (id, turn_id, label, path, media_type)
            values (?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                turn.id,
                "Report",
                str(linked_directory / "report.txt"),
                "text/plain",
            ),
        )

    with _client(tmp_path) as client:
        response = client.get(
            f"/api/workbench/tasks/{task.id}/turns/{turn.id}/artifacts/{artifact_id}/download"
        )

    assert response.status_code == 404


def test_sse_replays_persisted_events_with_last_event_id_precedence(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Replay", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Replay", client_request_id="replay-request"
    )
    first_event = store.events_after(turn.id)[0]
    store.request_stop(turn.id)
    final_event = store.events_after(turn.id)[-1]

    with _client(tmp_path) as client:
        response = client.get(
            f"/api/workbench/turns/{turn.id}/events/stream?after=0",
            headers={"Last-Event-ID": str(first_event.id)},
        )
        reconnect = client.get(
            f"/api/workbench/turns/{turn.id}/events/stream",
            headers={"Last-Event-ID": str(final_event.id)},
        )

    assert response.status_code == 200
    assert f"id: {first_event.id}\n" not in response.text
    assert f"id: {final_event.id}\n" in response.text
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert reconnect.status_code == 200
    assert reconnect.text == ""


def test_sse_polling_delivers_database_event_without_broker_wakeup(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Polling", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Polling", client_request_id="polling-request"
    )

    def persist_terminal_event():
        time.sleep(0.05)
        WorkbenchStore(store.path).request_stop(turn.id)

    writer = threading.Thread(target=persist_terminal_event)
    writer.start()
    with _client(tmp_path) as client:
        response = client.get(
            f"/api/workbench/turns/{turn.id}/events/stream",
            headers={"Last-Event-ID": str(store.events_after(turn.id)[0].id)},
        )
        assert client.app.state.workbench_event_broker.subscriber_count == 0
    writer.join(timeout=2)

    assert not writer.is_alive()
    assert response.status_code == 200
    assert "event: turn_completed" in response.text


@pytest.mark.parametrize("cursor", ["-1", "1.0", "abc", ""])
def test_sse_rejects_invalid_cursor(tmp_path: Path, cursor: str):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Cursor", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Cursor", client_request_id=f"cursor-{cursor}"
    )
    with _client(tmp_path) as client:
        response = client.get(
            f"/api/workbench/turns/{turn.id}/events/stream",
            headers={"Last-Event-ID": cursor},
        )

    assert response.status_code == 400


def test_sse_rejects_cross_origin_and_unknown_turn(tmp_path: Path):
    with _client(tmp_path) as client:
        cross_origin = client.get(
            f"/api/workbench/turns/{uuid4()}/events/stream",
            headers={"Origin": "https://attacker.example"},
        )
        unknown = client.get(f"/api/workbench/turns/{uuid4()}/events/stream")

    assert cross_origin.status_code == 403
    assert unknown.status_code == 404


def test_background_scheduler_recovers_after_executor_error():
    class FlakyExecutor:
        def __init__(self):
            self.calls = 0
            self.second_call = threading.Event()

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("failed run")
            self.second_call.set()
            return []

    executor = FlakyExecutor()
    scheduler = _ExecutionScheduler(executor)
    scheduler.schedule()
    deadline = time.monotonic() + 1
    while executor.calls < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    scheduler.schedule()

    assert executor.second_call.wait(1)
    scheduler.close()
