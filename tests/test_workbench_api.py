import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient

import app.workbench.api as workbench_api_module
from app.audit_web import create_audit_app
from app.setup_wizard import SETUP_WIZARD_STEPS
from app.store import AutoReplyStore
from app.workbench.api import WorkbenchScheduler
from app.workbench.models import TurnStatus
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


def test_archived_task_and_active_turn_archive_return_fixed_conflicts(tmp_path: Path):
    with _client(tmp_path) as client:
        archived = client.post(
            "/api/workbench/tasks",
            json={"title": "Archived", "runtime_kind": "codex"},
        ).json()
        assert (
            client.post(
                f"/api/workbench/tasks/{archived['id']}/archive", json={}
            ).status_code
            == 200
        )
        create_on_archived = client.post(
            f"/api/workbench/tasks/{archived['id']}/turns",
            json={"text": "Blocked", "client_request_id": "blocked-request"},
        )

        active = client.post(
            "/api/workbench/tasks",
            json={"title": "Active", "runtime_kind": "codex"},
        ).json()
        client.post(
            f"/api/workbench/tasks/{active['id']}/turns",
            json={"text": "Running", "client_request_id": "active-request"},
        )
        archive_active = client.post(
            f"/api/workbench/tasks/{active['id']}/archive", json={}
        )

    assert create_on_archived.status_code == 409
    assert create_on_archived.json() == {
        "detail": "Archived tasks cannot accept new turns"
    }
    assert archive_active.status_code == 409
    assert archive_active.json() == {
        "detail": "Tasks with active turns cannot be archived"
    }


def test_archived_task_idempotent_retry_precedes_fixed_collision_and_archive_errors(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "Retry", "runtime_kind": "codex"},
        ).json()
        created = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "Generate report", "client_request_id": "stable-request"},
        ).json()
        claimed = store.claim_next_turn(owner="worker")
        assert claimed is not None
        store.complete_turn(
            claimed.id,
            status=TurnStatus.COMPLETED,
            final_text="done",
            owner="worker",
        )
        store.archive_task(task["id"])

        retry = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "Generate report", "client_request_id": "stable-request"},
        )
        collision = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "Different body", "client_request_id": "stable-request"},
        )
        new_request = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "Another report", "client_request_id": "new-request"},
        )

    assert retry.status_code == 201
    assert retry.json()["id"] == created["id"]
    assert collision.status_code == 409
    assert collision.json() == {
        "detail": "Client request ID conflicts with an existing turn"
    }
    assert new_request.status_code == 409
    assert new_request.json() == {
        "detail": "Archived tasks cannot accept new turns"
    }


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


def test_artifact_download_streams_opened_descriptor_across_path_swap(
    tmp_path: Path, monkeypatch
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Artifacts", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Create report", client_request_id="swap-request"
    )
    artifact_path = tmp_path / "report.txt"
    artifact_path.write_text("original", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    artifact_id = str(uuid4())
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_artifacts (id, turn_id, label, path, media_type)
            values (?, ?, ?, ?, ?)
            """,
            (artifact_id, turn.id, "Report", str(artifact_path), "text/plain"),
        )

    opened_fd = []
    original_open = workbench_api_module._open_artifact_fd

    def open_then_swap(path, roots):
        result = original_open(path, roots)
        opened_fd.append(result.fd)
        artifact_path.unlink()
        artifact_path.symlink_to(outside)
        return result

    monkeypatch.setattr(workbench_api_module, "_open_artifact_fd", open_then_swap)
    with _client(tmp_path) as client:
        response = client.get(
            f"/api/workbench/tasks/{task.id}/turns/{turn.id}/artifacts/{artifact_id}/download"
        )

    assert response.status_code == 200
    assert response.text == "original"
    assert "outside-secret" not in response.text
    with pytest.raises(OSError):
        os.fstat(opened_fd[0])


def test_public_event_projection_redacts_nested_paths_and_credentials(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Events", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Events", client_request_id="public-event-request"
    )
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_events (turn_id, sequence, event_type, payload_json)
            values (?, 2, 'tool_started', ?)
            """,
            (
                turn.id,
                json.dumps(
                    {
                        "tool": "reader",
                        "summary": {
                            "path": str(tmp_path / "safe" / "report.md"),
                            "nested": {
                                "filename": "../../outside.txt",
                                "outputFile": str(
                                    tmp_path / "safe" / "result.json"
                                ),
                                "note": "Bearer abcdefghijklmnop",
                                "other": "Read /etc/passwd before continuing",
                            },
                        },
                        "tool_call_id": "tool-1",
                    }
                ),
            ),
        )

    with _client(tmp_path) as client:
        response = client.get(f"/api/workbench/turns/{turn.id}/events?after=0&limit=10")

    encoded = json.dumps(response.json(), ensure_ascii=False)
    payload = response.json()[1]["payload"]
    assert response.status_code == 200
    assert str(tmp_path) not in encoded
    assert "../../" not in encoded
    assert "abcdefghijklmnop" not in encoded
    assert "/etc/passwd" not in encoded
    assert payload["summary"]["path"] == "safe/report.md"
    assert payload["summary"]["nested"]["filename"] == "outside.txt"
    assert payload["summary"]["nested"]["outputFile"] == "safe/result.json"


def test_public_event_projection_redacts_delimited_paths_but_preserves_web_urls(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Delimited paths", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Events", client_request_id="delimited-path-request"
    )
    message = (
        'path=/etc/passwd error:/opt/private/file '
        'nested={"source":"/private/tmp/secret.json"} '
        f'workspace=[{tmp_path}/private/report.md] '
        r'windows="C:\Users\Derek\secret.txt" '
        "system=/usr/local/bin/private-tool "
        "home=/home/alice/private/report.csv "
        "application=/Applications/Private.app/Contents/MacOS/private "
        "volume=/Volumes/private-drive/archive.tar "
        "root=/root/.ssh/id_ed25519 device=/dev/disk4 "
        "custom=/custom/mount/private-file "
        "url=https://example.com/docs/path?q=/etc/passwd "
        "public=https://example.com/tmp/public-report "
        "api=/api/workbench/tasks/123 asset=/workbench-assets/index.js"
    )
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_events (turn_id, sequence, event_type, payload_json)
            values (?, 2, 'tool_started', ?)
            """,
            (
                turn.id,
                json.dumps(
                    {
                        "tool": "reader",
                        "summary": {"message": message, "relative": "docs/report.md"},
                        "tool_call_id": "tool-2",
                    }
                ),
            ),
        )

    with _client(tmp_path) as client:
        response = client.get(f"/api/workbench/turns/{turn.id}/events?after=0&limit=10")

    projected = response.json()[1]["payload"]["summary"]
    encoded = json.dumps(projected)
    assert response.status_code == 200
    assert projected["message"].count("/etc/passwd") == 1
    assert "/opt/private/file" not in encoded
    assert "/private/tmp/secret.json" not in encoded
    assert str(tmp_path) not in encoded
    assert r"C:\\Users\\Derek\\secret.txt" not in encoded
    for local_path in (
        "/usr/local/bin/private-tool",
        "/home/alice/private/report.csv",
        "/Applications/Private.app/Contents/MacOS/private",
        "/Volumes/private-drive/archive.tar",
        "/root/.ssh/id_ed25519",
        "/dev/disk4",
        "/custom/mount/private-file",
    ):
        assert local_path not in encoded
    assert "https://example.com/docs/path?q=/etc/passwd" in projected["message"]
    assert "https://example.com/tmp/public-report" in projected["message"]
    assert "/api/workbench/tasks/123" in projected["message"]
    assert "/workbench-assets/index.js" in projected["message"]
    assert projected["relative"] == "docs/report.md"


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


def test_sse_terminal_race_rechecks_sqlite_without_missing_or_duplicates_100_times(
    tmp_path: Path, monkeypatch
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    controller = {
        "turn_id": "",
        "calls": 0,
        "empty_query_complete": threading.Event(),
        "terminal_committed": threading.Event(),
    }
    original_events_after = WorkbenchStore.events_after

    def events_after_with_terminal_barrier(self, turn_id, after_id=0, *, limit=100):
        result = original_events_after(self, turn_id, after_id, limit=limit)
        if turn_id == controller["turn_id"]:
            controller["calls"] += 1
            if controller["calls"] == 2:
                assert result == []
                controller["empty_query_complete"].set()
                assert controller["terminal_committed"].wait(1)
        return result

    monkeypatch.setattr(
        WorkbenchStore, "events_after", events_after_with_terminal_barrier
    )
    with _client(tmp_path) as client:
        for repetition in range(100):
            task = store.create_task(
                title=f"Terminal race {repetition}", runtime_kind="codex"
            )
            turn = store.create_turn(
                task.id,
                user_text="Stop after empty replay",
                client_request_id=f"terminal-race-{repetition}",
            )
            initial_event_id = original_events_after(store, turn.id)[0].id
            controller.update(
                {
                    "turn_id": turn.id,
                    "calls": 0,
                    "empty_query_complete": threading.Event(),
                    "terminal_committed": threading.Event(),
                }
            )

            def commit_terminal_event():
                assert controller["empty_query_complete"].wait(1)
                WorkbenchStore(store.path).request_stop(turn.id)
                controller["terminal_committed"].set()

            writer = threading.Thread(target=commit_terminal_event)
            writer.start()
            response = client.get(
                f"/api/workbench/turns/{turn.id}/events/stream",
                headers={"Last-Event-ID": str(initial_event_id)},
            )
            writer.join(timeout=1)

            assert writer.is_alive() is False
            event_ids = [
                int(line.removeprefix("id: "))
                for line in response.text.splitlines()
                if line.startswith("id: ")
            ]
            terminal_events = [
                line
                for line in response.text.splitlines()
                if line == "event: turn_completed"
            ]
            assert response.status_code == 200
            assert len(event_ids) == len(set(event_ids)) == 1
            assert len(terminal_events) == 1


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


def test_scheduler_polls_without_overlap_and_recovers_with_sanitized_log(caplog):
    class FlakyExecutor:
        def __init__(self):
            self.calls = 0
            self.concurrent = 0
            self.max_concurrent = 0
            self.third_call = threading.Event()

        def run_once(self):
            self.calls += 1
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            if self.calls == 1:
                self.concurrent -= 1
                raise RuntimeError("/Users/derek/private/provider.log")
            if self.calls >= 3:
                self.third_call.set()
            self.concurrent -= 1
            return []

    executor = FlakyExecutor()
    scheduler = WorkbenchScheduler(executor, interval_seconds=0.02)
    scheduler.start()

    assert executor.third_call.wait(1)
    scheduler.close()
    assert executor.max_concurrent == 1
    assert scheduler.is_alive is False
    assert "/Users/derek" not in caplog.text


def test_scheduler_wakeup_runs_before_next_poll_interval():
    class TrackingExecutor:
        def __init__(self):
            self.calls = 0
            self.first_call = threading.Event()
            self.second_call = threading.Event()

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                self.first_call.set()
            if self.calls == 2:
                self.second_call.set()
            return []

    executor = TrackingExecutor()
    scheduler = WorkbenchScheduler(executor, interval_seconds=60)
    scheduler.start()
    assert executor.first_call.wait(1)

    scheduler.wake()

    assert executor.second_call.wait(0.2)
    scheduler.close()


def test_scheduler_join_is_bounded_and_records_incomplete_close():
    entered = threading.Event()
    release = threading.Event()

    class BlockedExecutor:
        def run_once(self):
            entered.set()
            release.wait()
            return []

    scheduler = WorkbenchScheduler(BlockedExecutor(), interval_seconds=60)
    scheduler.start()
    assert entered.wait(1)

    try:
        scheduler.stop()
        complete = scheduler.join(timeout=0.02)

        assert complete is False
        assert scheduler.close_complete is False
        release.set()
        assert scheduler.join(timeout=1) is True
        assert scheduler.close_complete is True
        assert scheduler.is_alive is False
    finally:
        release.set()
        scheduler.close()


def test_app_startup_recovers_then_runs_persisted_queue_and_shutdown_joins(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id, status="done", summary="complete"
        )
    task = store.create_task(title="Persisted", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Resume", client_request_id="persisted-request"
    )
    calls = []
    completed = threading.Event()

    class TrackingExecutor:
        workspace = tmp_path

        def recover(self):
            calls.append("recover")
            return 0

        def run_once(self):
            calls.append("run")
            claimed = store.claim_next_turn(owner="scheduler")
            if claimed is None:
                return []
            store.complete_turn(
                claimed.id,
                status=TurnStatus.COMPLETED,
                final_text="done",
                owner="scheduler",
            )
            completed.set()
            return [claimed.id]

        def close(self):
            calls.append("executor_close")
            return True

    executor = TrackingExecutor()
    app = create_audit_app(
        store.path,
        workbench_asset_dir=tmp_path / "assets",
        workbench_workspace=tmp_path,
        workbench_executor=executor,
        workbench_scheduler_interval_seconds=0.02,
    )
    with TestClient(app):
        assert completed.wait(1)
        assert store.get_turn(turn.id).status is TurnStatus.COMPLETED
        assert calls[:2] == ["recover", "run"]

    assert app.state.workbench_scheduler.is_alive is False
    assert calls[-1] == "executor_close"


def test_app_shutdown_stops_executor_before_bounded_scheduler_join(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id, status="done", summary="complete"
        )
    run_entered = threading.Event()
    release_run = threading.Event()
    allow_shutdown = threading.Event()
    shutdown_done = threading.Event()
    calls = []

    class BlockedExecutor:
        workspace = tmp_path

        def recover(self):
            calls.append("recover")
            return 0

        def run_once(self):
            calls.append("run")
            run_entered.set()
            release_run.wait()
            calls.append("run_exit")
            return []

        def close(self):
            calls.append("executor_close")
            release_run.set()
            return True

    executor = BlockedExecutor()
    app = create_audit_app(
        store.path,
        workbench_asset_dir=tmp_path / "assets",
        workbench_workspace=tmp_path,
        workbench_executor=executor,
        workbench_scheduler_interval_seconds=60,
    )

    def run_lifecycle():
        with TestClient(app):
            allow_shutdown.wait()
        shutdown_done.set()

    lifecycle_thread = threading.Thread(target=run_lifecycle)
    lifecycle_thread.start()
    completed_without_cleanup = False
    try:
        assert run_entered.wait(1)
        allow_shutdown.set()
        completed_without_cleanup = shutdown_done.wait(1)
    finally:
        allow_shutdown.set()
        release_run.set()
        lifecycle_thread.join(timeout=1)

    assert completed_without_cleanup is True
    assert lifecycle_thread.is_alive() is False
    assert app.state.workbench_scheduler.is_alive is False
    assert app.state.workbench_shutdown_complete is True
    assert calls.index("executor_close") < calls.index("run_exit")


def test_unknown_store_error_never_reflects_exception_text(tmp_path: Path, monkeypatch):
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "Rename", "runtime_kind": "codex"},
        ).json()

        def fail_with_private_path(*args, **kwargs):
            del args, kwargs
            raise ValueError("database failed at /Users/derek/private.sqlite3")

        monkeypatch.setattr(WorkbenchStore, "rename_task", fail_with_private_path)
        response = client.patch(
            f"/api/workbench/tasks/{task['id']}", json={"title": "Updated"}
        )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "request conflicts with current resource state"
    }
    assert "/Users/derek" not in response.text
