import json
import os
import asyncio
import sqlite3
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest
import httpx

from fastapi.testclient import TestClient
from starlette.requests import Request

import app.workbench.api as workbench_api_module
from app.audit_web import create_audit_app
from app.setup_wizard import SETUP_WIZARD_STEPS
from app.store import AutoReplyStore
from app.workbench.api import WorkbenchScheduler
from app.workbench.models import TurnStatus
from app.workbench.store import WorkbenchStore


def _complete_setup(store: WorkbenchStore) -> None:
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id, status="done", summary="complete"
        )


def _client(tmp_path: Path) -> TestClient:
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)

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


def test_first_turn_derives_default_task_title_in_timeline(tmp_path: Path):
    with _client(tmp_path) as client:
        task_response = client.post(
            "/api/workbench/tasks",
            json={"title": "新任务", "runtime_kind": "codex"},
        )
        task = task_response.json()
        turn_response = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={
                "text": "检查今天的重要事项",
                "client_request_id": "derive-title-api",
            },
        )
        timeline = client.get(f"/api/workbench/tasks/{task['id']}/timeline")

    assert task_response.status_code == 201
    assert turn_response.status_code == 201
    assert timeline.status_code == 200
    assert timeline.json()["task"]["title"] == "检查今天的重要事项"


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
    assert (
        "npm install --prefix frontend &amp;&amp; npm run build:workbench"
        in root.text
    )
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
                "client_request_id": "11111111-1111-4111-8111-111111111111",
                "filename": "notes.txt",
                "media_type": "text/plain",
                "content_base64": "aGVsbG8=",
            },
        )
        invalid = client.post(
            f"/api/workbench/tasks/{task['id']}/attachments",
            json={
                "client_request_id": "22222222-2222-4222-8222-222222222222",
                "filename": "notes.txt",
                "media_type": "text/plain",
                "content_base64": "%%%",
            },
        )
        whitespace = client.post(
            f"/api/workbench/tasks/{task['id']}/attachments",
            json={
                "client_request_id": "33333333-3333-4333-8333-333333333333",
                "filename": "notes.txt",
                "media_type": "text/plain",
                "content_base64": " aGVsbG8=",
            },
        )
        emoji_media_type = client.post(
            f"/api/workbench/tasks/{task['id']}/attachments",
            json={
                "client_request_id": "44444444-4444-4444-8444-444444444444",
                "filename": "notes.txt",
                "media_type": "text/💥",
                "content_base64": "aGVsbG8=",
            },
        )

    assert uploaded.status_code == 201
    assert uploaded.json()["size_bytes"] == 5
    assert "storage_path" not in uploaded.json()
    assert invalid.status_code == 400
    assert whitespace.status_code == 400
    assert emoji_media_type.status_code == 400


def test_attachment_upload_retry_is_idempotent_and_collision_is_fixed(tmp_path: Path):
    request_id = "5e270a4d-9085-4461-a23d-53fa7ef82948"
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "Retry image", "runtime_kind": "codex"},
        ).json()
        endpoint = f"/api/workbench/tasks/{task['id']}/attachments"
        payload = {
            "client_request_id": request_id,
            "filename": "chart.png",
            "media_type": "image/png",
            "content_base64": "aW1hZ2U=",
        }

        created = client.post(endpoint, json=payload)
        retried = client.post(endpoint, json=payload)
        collision = client.post(
            endpoint,
            json={**payload, "content_base64": "ZGlmZmVyZW50"},
        )

    assert created.status_code == 201
    assert retried.status_code == 201
    assert retried.json() == created.json()
    assert collision.status_code == 409
    assert collision.json() == {
        "detail": "Client request ID conflicts with an existing attachment"
    }
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        assert db.execute("select count(*) from workbench_attachments").fetchone()[0] == 1
        row = db.execute(
            "select client_request_id, content_sha256 from workbench_attachments"
        ).fetchone()
    assert row[0] == request_id
    assert len(row[1]) == 64
    files = list((tmp_path / "workbench" / "attachments" / task["id"]).iterdir())
    assert len(files) == 1


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


def test_task_list_is_bounded_and_cursor_paginates_without_duplicates(tmp_path: Path):
    with _client(tmp_path) as client:
        for index in range(5):
            client.post(
                "/api/workbench/tasks",
                json={"title": f"Task {index}", "runtime_kind": "codex"},
            )
        first = client.get("/api/workbench/tasks?limit=2")
        second = client.get(
            "/api/workbench/tasks",
            params={"limit": 2, "cursor": first.headers["x-next-cursor"]},
        )

    first_ids = [item["id"] for item in first.json()]
    second_ids = [item["id"] for item in second.json()]
    assert len(first_ids) == len(second_ids) == 2
    assert set(first_ids).isdisjoint(second_ids)
    assert second.headers.get("x-next-cursor")


def test_single_task_mutation_responses_never_load_full_turn_history(
    tmp_path: Path, monkeypatch
):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/workbench/tasks",
            json={"title": "Summary only", "runtime_kind": "codex"},
        )
        task_id = created.json()["id"]

        def fail_list_turns(*_args, **_kwargs):
            raise AssertionError("single-task response must not load all turns")

        monkeypatch.setattr(WorkbenchStore, "list_turns", fail_list_turns)
        fetched = client.get(f"/api/workbench/tasks/{task_id}")
        renamed = client.patch(
            f"/api/workbench/tasks/{task_id}", json={"title": "Renamed"}
        )
        archived = client.post(f"/api/workbench/tasks/{task_id}/archive", json={})

    assert created.status_code == 201
    assert fetched.status_code == renamed.status_code == archived.status_code == 200
    assert fetched.json()["state"] == "idle"


def test_timeline_turn_cursor_keeps_nested_resources_on_the_selected_page(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="Long history", runtime_kind="codex")
    turn_ids: list[str] = []
    with store._connect() as db:
        for index in range(101):
            turn_id = str(uuid4())
            turn_ids.append(turn_id)
            created_at = f"2026-08-13 00:{index // 60:02d}:{index % 60:02d}"
            db.execute(
                """insert into workbench_turns
                   (id,task_id,client_request_id,task_sequence,user_text,status,
                    created_at,updated_at)
                   values(?,?,?,?,?,?,?,?)""",
                (
                    turn_id,
                    task.id,
                    f"page-{index}",
                    index + 1,
                    f"turn {index}",
                    "completed",
                    created_at,
                    created_at,
                ),
            )
            db.execute(
                """insert into workbench_events
                   (turn_id,sequence,event_type,payload_json,created_at)
                   values(?,1,'turn_completed','{"status":"completed"}',?)""",
                (turn_id, created_at),
            )
        for index in (0, 100):
            db.execute(
                """insert into workbench_artifacts
                   (id,turn_id,label,path,media_type,created_at)
                   values(?,?,?,?,?,?)""",
                (
                    str(uuid4()),
                    turn_ids[index],
                    f"artifact {index}",
                    str(tmp_path / f"artifact-{index}.txt"),
                    "text/plain",
                    f"2026-08-13 00:{index // 60:02d}:{index % 60:02d}",
                ),
            )

    with _client(tmp_path) as client:
        first = client.get(
            f"/api/workbench/tasks/{task.id}/timeline", params={"turn_limit": 100}
        )
        first_body = first.json()
        second = client.get(
            f"/api/workbench/tasks/{task.id}/timeline",
            params={"turn_limit": 100, "before": first_body["next_cursor"]},
        )
        second_body = second.json()

    first_turn_ids = {turn["id"] for turn in first_body["turns"]}
    second_turn_ids = {turn["id"] for turn in second_body["turns"]}
    assert first.status_code == second.status_code == 200
    assert len(first_turn_ids) == 100
    assert len(second_turn_ids) == 1
    assert first_body["has_more"] is True
    assert second_body["has_more"] is False
    assert first_turn_ids.isdisjoint(second_turn_ids)
    assert first_turn_ids | second_turn_ids == set(turn_ids)
    for body, selected_ids in (
        (first_body, first_turn_ids),
        (second_body, second_turn_ids),
    ):
        assert {event["turn_id"] for event in body["events"]} <= selected_ids
        assert {artifact["turn_id"] for artifact in body["artifacts"]} <= selected_ids


def test_same_timestamp_public_turns_keep_creation_order_and_latest_state(
    tmp_path: Path,
):
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "Monotonic", "runtime_kind": "codex"},
        ).json()
        first = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "first", "client_request_id": "monotonic-first"},
        ).json()
        client.post(
            f"/api/workbench/tasks/{task['id']}/turns/{first['id']}/stop", json={}
        )
        second = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "second", "client_request_id": "monotonic-second"},
        ).json()
        store = WorkbenchStore(tmp_path / "worker.sqlite3")
        with store._connect() as db:
            db.execute(
                """update workbench_turns set created_at='2026-08-13 00:00:00'
                   where task_id=?""",
                (task["id"],),
            )
        summary = client.get(f"/api/workbench/tasks/{task['id']}")
        timeline = client.get(f"/api/workbench/tasks/{task['id']}/timeline")

    assert summary.status_code == timeline.status_code == 200
    assert summary.json()["state"] == "queued"
    assert [turn["id"] for turn in timeline.json()["turns"]] == [
        second["id"],
        first["id"],
    ]
    assert "task_sequence" not in timeline.json()["turns"][0]


def test_timeline_cursor_is_bound_to_issuing_task(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    first_task = store.create_task(title="First", runtime_kind="codex")
    second_task = store.create_task(title="Second", runtime_kind="codex")
    for index in range(2):
        turn = store.create_turn(
            first_task.id,
            user_text=f"first {index}",
            client_request_id=f"first-cursor-{index}",
        )
        store.request_stop(turn.id)
    second_turn = store.create_turn(
        second_task.id,
        user_text="second",
        client_request_id="second-cursor",
    )
    store.request_stop(second_turn.id)

    with _client(tmp_path) as client:
        first_page = client.get(
            f"/api/workbench/tasks/{first_task.id}/timeline",
            params={"turn_limit": 1},
        )
        cross_task = client.get(
            f"/api/workbench/tasks/{second_task.id}/timeline",
            params={"before": first_page.json()["next_cursor"]},
        )

    assert first_page.status_code == 200
    assert cross_task.status_code == 400


def test_timeline_does_not_read_confirmation_quiescence_after_snapshot(
    tmp_path: Path, monkeypatch
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="Snapshot confirmations", runtime_kind="codex")

    def fail_separate_read(*_args, **_kwargs):
        raise AssertionError("quiescence must come from timeline snapshot")

    monkeypatch.setattr(WorkbenchStore, "confirmation_quiescence", fail_separate_read)

    with _client(tmp_path) as client:
        response = client.get(f"/api/workbench/tasks/{task.id}/timeline")

    assert response.status_code == 200


def test_timeline_exposes_usable_resource_cursors(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="Paged resources", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="resources", client_request_id="resource-cursors"
    )
    store.request_stop(turn.id)
    with store._connect() as db:
        for sequence in range(3, 105):
            db.execute(
                """insert into workbench_events
                   (turn_id,sequence,event_type,payload_json)
                   values(?,?,'text_delta',?)""",
                (turn.id, sequence, json.dumps({"text": str(sequence)})),
            )

    with _client(tmp_path) as client:
        first = client.get(
            f"/api/workbench/tasks/{task.id}/timeline", params={"event_limit": 100}
        )
        first_body = first.json()
        second = client.get(
            f"/api/workbench/tasks/{task.id}/timeline",
            params={
                "event_limit": 100,
                "event_before": first_body["events_next_cursor"],
            },
        )
        second_body = second.json()

    assert first.status_code == second.status_code == 200
    assert first_body["events_has_more"] is True
    assert first_body["events_next_cursor"] > 0
    assert {event["id"] for event in first_body["events"]}.isdisjoint(
        event["id"] for event in second_body["events"]
    )
    assert second_body["events_has_more"] is False


def test_all_timeline_public_strings_preserve_paths_and_redact_credentials(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="safe", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="safe", client_request_id="privacy-boundary"
    )
    store.request_stop(turn.id)
    attachment_id = str(uuid4())
    artifact_id = str(uuid4())
    confirmation_id = str(uuid4())
    with store._connect() as db:
        db.execute(
            """update workbench_tasks
               set title=?,runtime_kind=?,created_at=?,updated_at=? where id=?""",
            (
                "Bearer legacy-secret /private/task.db",
                "runtime=/opt/provider/config",
                "Bearer created-secret",
                "updated=/etc/task.db",
                task.id,
            ),
        )
        db.execute(
            """update workbench_turns
               set client_request_id=?,user_text=?,final_text=?,error_code=?,error_detail=?,
                   started_at=?,completed_at=?,created_at=?,updated_at=?
               where id=?""",
            (
                "Bearer client-secret",
                "read /Users/derek/private.txt",
                "result=/custom/mount/result.txt",
                "token=legacy-secret",
                "failed at C:\\private\\secret.txt",
                "started=/root/secret",
                "Bearer completed-secret",
                "created=/home/secret",
                "updated=/opt/secret",
                turn.id,
            ),
        )
        db.execute(
            """insert into workbench_attachments
               (id,task_id,client_request_id,filename,media_type,size_bytes,storage_path,content_sha256)
               values(?,?,?,?,?,?,?,?)""",
            (
                attachment_id,
                task.id,
                attachment_id,
                "/etc/private.txt",
                "text/plain",
                0,
                str(tmp_path / "internal-attachment"),
                "0" * 64,
            ),
        )
        db.execute(
            """insert into workbench_artifacts
               (id,turn_id,label,path,media_type)
               values(?,?,?,?,?)""",
            (
                artifact_id,
                turn.id,
                "Bearer artifact-secret /root/report.txt",
                str(tmp_path / "internal-artifact"),
                "text/plain",
            ),
        )
        db.execute(
            """insert into workbench_confirmations
               (id,turn_id,action_kind,target,summary,risk,
                canonical_capability,canonical_operation,canonical_targets_json,
                arguments_json,status,decision_requested)
               values(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                confirmation_id,
                turn.id,
                "tool=/usr/bin/send",
                "Bearer target-secret",
                "write /home/private/message.txt",
                "api_key=legacy-secret",
                "cap=/Volumes/private/cap",
                "op=/Applications/private/op",
                json.dumps(["/dev/private", "Bearer canonical-secret"]),
                "{}",
                "pending",
                "",
            ),
        )

    with _client(tmp_path) as client:
        timeline = client.get(f"/api/workbench/tasks/{task.id}/timeline")
        nested_turn = client.get(
            f"/api/workbench/tasks/{task.id}/turns/{turn.id}"
        )
        task_response = client.get(f"/api/workbench/tasks/{task.id}")

    rendered = json.dumps(
        [timeline.json(), nested_turn.json(), task_response.json()],
        ensure_ascii=False,
    )
    assert timeline.status_code == nested_turn.status_code == task_response.status_code == 200
    for forbidden in (
        "legacy-secret",
        "created-secret",
        "completed-secret",
        "client-secret",
        "target-secret",
        "artifact-secret",
        "canonical-secret",
    ):
        assert forbidden not in rendered

    assert "read /Users/derek/private.txt" in rendered
    assert "result=/custom/mount/result.txt" in rendered
    assert nested_turn.json()["error_detail"] == r"failed at C:\private\secret.txt"


def test_public_turn_preserves_exact_local_paths_but_redacts_credentials(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="White box paths", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="读取 /Users/derek/Documents/Projects/ceo-agent-service/README.md",
        client_request_id="white-box-turn-paths",
    )
    with store._connect() as db:
        db.execute(
            """update workbench_turns
               set status='failed',
                   final_text='检查结果位于 /private/tmp/ceo-agent/report.json',
                   error_code='provider_failed',
                   error_detail='执行 /usr/bin/printf 失败；api_key=credential-value-1234'
               where id=?""",
            (turn.id,),
        )

    with _client(tmp_path) as client:
        response = client.get(f"/api/workbench/tasks/{task.id}/turns/{turn.id}")

    body = response.json()
    assert response.status_code == 200
    assert body["user_text"] == (
        "读取 /Users/derek/Documents/Projects/ceo-agent-service/README.md"
    )
    assert body["final_text"] == "检查结果位于 /private/tmp/ceo-agent/report.json"
    assert "/usr/bin/printf" in body["error_detail"]
    assert "credential-value-1234" not in body["error_detail"]


def test_streaming_json_collector_stops_at_cap_without_consuming_remaining_chunks():
    chunks = [b'{"title":"', b"x" * 20, b'","runtime_kind":"codex"}', b"unused"]
    consumed = 0

    async def receive():
        nonlocal consumed
        chunk = chunks[consumed]
        consumed += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": consumed < len(chunks),
        }

    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []},
        receive,
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            workbench_api_module._request_model(
                request,
                workbench_api_module._CreateTask,
                mutation_guard=lambda _request: None,
                max_bytes=16,
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 413
    assert consumed == 2


def test_streaming_json_collector_rejects_large_content_length_before_receive():
    consumed = False

    async def receive():
        nonlocal consumed
        consumed = True
        raise AssertionError("body must not be read")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-length", b"17")],
        },
        receive,
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            workbench_api_module._request_model(
                request,
                workbench_api_module._CreateTask,
                mutation_guard=lambda _request: None,
                max_bytes=16,
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 413
    assert consumed is False


def test_blocked_confirmation_does_not_block_unrelated_async_request(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="Confirm", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Confirm", client_request_id="confirm")
    assert store.claim_next_turn(owner="seed") is not None
    confirmation = store.create_confirmation(
        turn.id,
        action_kind="reviewed_cli",
        target="target",
        summary="summary",
        risk="risk",
        arguments_json={},
        owner="seed",
    )

    class BlockingExecutor:
        workspace = tmp_path

        def recover(self): return 0
        def run_once(self): return []
        def close(self): return True
        def confirm(self, confirmation_id):
            time.sleep(0.4)
            return store.get_confirmation(confirmation_id)

    app = create_audit_app(
        store.path,
        workbench_asset_dir=tmp_path / "assets",
        workbench_workspace=tmp_path,
        workbench_executor=BlockingExecutor(),
    )

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8765",
            headers={"Host": "127.0.0.1:8765"},
        ) as client:
            confirm_request = asyncio.create_task(client.post(
                f"/api/workbench/tasks/{task.id}/turns/{turn.id}/confirmations/{confirmation.id}/confirm",
                json={},
            ))
            await asyncio.sleep(0.05)
            started = time.monotonic()
            stats = await client.get("/api/workbench/stats")
            elapsed = time.monotonic() - started
            confirmed = await confirm_request
            return stats, confirmed, elapsed

    stats, confirmed, elapsed = asyncio.run(exercise())
    assert stats.status_code == confirmed.status_code == 200
    assert elapsed < 0.25


def test_slow_sse_snapshot_does_not_block_unrelated_async_request(
    tmp_path: Path, monkeypatch
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="SSE", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="SSE", client_request_id="sse-slow")
    store.request_stop(turn.id)
    original = WorkbenchStore.event_stream_snapshot

    def slow_snapshot(self, *args, **kwargs):
        time.sleep(0.4)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(WorkbenchStore, "event_stream_snapshot", slow_snapshot)
    app = create_audit_app(
        store.path,
        workbench_asset_dir=tmp_path / "assets",
        workbench_workspace=tmp_path,
    )

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8765",
            headers={"Host": "127.0.0.1:8765"},
        ) as client:
            stream_request = asyncio.create_task(client.get(
                f"/api/workbench/turns/{turn.id}/events/stream"
            ))
            await asyncio.sleep(0.05)
            started = time.monotonic()
            stats = await client.get("/api/workbench/stats")
            elapsed = time.monotonic() - started
            streamed = await stream_request
            return stats, streamed, elapsed

    stats, streamed, elapsed = asyncio.run(exercise())
    assert stats.status_code == streamed.status_code == 200
    assert elapsed < 0.25


def test_attachment_fsync_does_not_block_unrelated_async_request(
    tmp_path: Path, monkeypatch
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    _complete_setup(store)
    task = store.create_task(title="Upload", runtime_kind="codex")
    original_fsync = WorkbenchStore._fsync_attachment_file

    def slow_fsync(path):
        time.sleep(0.4)
        return original_fsync(path)

    monkeypatch.setattr(
        WorkbenchStore, "_fsync_attachment_file", staticmethod(slow_fsync)
    )
    app = create_audit_app(
        store.path,
        workbench_asset_dir=tmp_path / "assets",
        workbench_workspace=tmp_path,
    )

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8765",
            headers={"Host": "127.0.0.1:8765"},
        ) as client:
            upload_request = asyncio.create_task(
                client.post(
                    f"/api/workbench/tasks/{task.id}/attachments",
                    json={
                        "client_request_id": "55555555-5555-4555-8555-555555555555",
                        "filename": "report.txt",
                        "media_type": "text/plain",
                        "content_base64": "cmVwb3J0",
                    },
                )
            )
            await asyncio.sleep(0.05)
            started = time.monotonic()
            stats = await client.get("/api/workbench/stats")
            elapsed = time.monotonic() - started
            uploaded = await upload_request
            return stats, uploaded, elapsed

    stats, uploaded, elapsed = asyncio.run(exercise())
    assert stats.status_code == 200
    assert uploaded.status_code == 201
    assert elapsed < 0.25


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
            (
                artifact_id,
                turn.id,
                "Bearer artifact-download-secret",
                str(artifact_path),
                "text/💥",
            ),
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
    assert timeline.json()["artifacts"][0]["media_type"] == "application/octet-stream"
    assert download.status_code == 200
    assert download.text == "report"
    assert download.headers["content-type"] == "application/octet-stream"
    assert "artifact-download-secret" not in download.headers["content-disposition"]
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


def test_public_event_projection_preserves_nested_paths_and_redacts_credentials(
    tmp_path: Path,
):
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
    assert str(tmp_path) in encoded
    assert "../../outside.txt" in encoded
    assert "abcdefghijklmnop" not in encoded
    assert "/etc/passwd" in encoded
    assert payload["summary"]["path"] == str(tmp_path / "safe" / "report.md")
    assert payload["summary"]["nested"]["filename"] == "../../outside.txt"
    assert payload["summary"]["nested"]["outputFile"] == str(
        tmp_path / "safe" / "result.json"
    )


def test_public_event_projection_preserves_delimited_paths_and_web_urls(
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
    assert projected["message"].count("/etc/passwd") == 2
    assert "/opt/private/file" in encoded
    assert "/private/tmp/secret.json" in encoded
    assert str(tmp_path) in encoded
    assert r"C:\\Users\\Derek\\secret.txt" in encoded
    for local_path in (
        "/usr/local/bin/private-tool",
        "/home/alice/private/report.csv",
        "/Applications/Private.app/Contents/MacOS/private",
        "/Volumes/private-drive/archive.tar",
        "/root/.ssh/id_ed25519",
        "/dev/disk4",
        "/custom/mount/private-file",
    ):
        assert local_path in encoded
    assert "https://example.com/docs/path?q=/etc/passwd" in projected["message"]
    assert "https://example.com/tmp/public-report" in projected["message"]
    assert "/api/workbench/tasks/123" in projected["message"]
    assert "/workbench-assets/index.js" in projected["message"]
    assert projected["relative"] == "docs/report.md"


def test_white_box_tool_event_preserves_exact_action_and_nested_result(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="White box", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Inspect", client_request_id="white-box-public-event"
    )
    payload = {
        "tool_call_id": "tool-call-1",
        "kind": "command",
        "name": "rg",
        "native_id": "native-command-1",
        "status": "completed",
        "command": "rg --files frontend/src",
        "cwd": str(tmp_path),
        "exit_code": 0,
        "output": "frontend/src/app.tsx\n",
        "provider_item": {
            "id": "native-command-1",
            "type": "command_execution",
            "command": "rg --files frontend/src",
            "cwd": str(tmp_path),
            "aggregated_output": "frontend/src/app.tsx\n",
            "exit_code": 0,
        },
    }
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_events (turn_id, sequence, event_type, payload_json)
            values (?, 2, 'tool_completed', ?)
            """,
            (turn.id, json.dumps(payload)),
        )

    with _client(tmp_path) as client:
        events_response = client.get(
            f"/api/workbench/turns/{turn.id}/events?after=0&limit=10"
        )
        timeline_response = client.get(f"/api/workbench/tasks/{task.id}/timeline")

    assert events_response.status_code == 200
    assert timeline_response.status_code == 200
    assert events_response.json()[1]["payload"] == payload
    assert timeline_response.json()["events"][1]["payload"] == payload


def test_white_box_tool_event_redacts_only_credential_values(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="White box credentials", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Inspect", client_request_id="white-box-credential-event"
    )
    payload = {
        "tool_call_id": "tool-call-credential",
        "kind": "mcp",
        "name": "memory_connector.memory_recall",
        "native_id": "native-memory-1",
        "status": "completed",
        "server": "memory_connector",
        "tool": "memory_recall",
        "arguments": {"query": "管理问题", "access_token": "short-secret"},
        "result": {
            "source": "/Users/derek/.codex/memories/MEMORY.md",
            "summary": "api_key=credential-value-1234",
        },
        "provider_item": {"id": "native-memory-1", "type": "mcp_tool_call"},
    }
    with store._connect() as db:
        db.execute(
            """
            insert into workbench_events (turn_id, sequence, event_type, payload_json)
            values (?, 2, 'tool_completed', ?)
            """,
            (turn.id, json.dumps(payload)),
        )

    with _client(tmp_path) as client:
        response = client.get(f"/api/workbench/turns/{turn.id}/events?after=0&limit=10")

    projected = response.json()[1]["payload"]
    assert response.status_code == 200
    assert projected["arguments"] == {
        "query": "管理问题",
        "access_token": "[redacted]",
    }
    assert projected["result"] == {
        "source": "/Users/derek/.codex/memories/MEMORY.md",
        "summary": "[redacted]",
    }


def test_public_event_projection_preserves_all_diagnostic_path_strings(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Canonical paths", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Paths", client_request_id="canonical-path-request"
    )
    unsafe_paths = [
        "/secret",
        "/etc",
        "/",
        "/api/../../etc",
        "/api/%2e%2e/etc",
        "/api/%252e%252e/etc",
        "/api/%2Fetc",
        "/api/%252fetc",
        "/api/%5c..%5cetc",
        r"/api\..\etc",
        "/api//tasks",
        "/api/./tasks",
        "/api/tasks;../../etc",
        "/api/tasks,../../etc",
        "/api/tasks]../../etc",
        "/api/tasks?next=../../etc",
        "/api/tasks#../../etc",
        "/api/tasks?path=/etc/passwd",
        "/api/tasks#path=/custom/private",
        "/api/%00secret",
        "/api/\x00secret",
        "/api/\x1fsecret",
        "/api/tasks%20one",
    ]
    safe_paths = [
        "/api/workbench/tasks/123",
        "/workbench-assets/index.js",
        "/api/tasks?page=1",
        "https://example.com/api/../../etc",
    ]
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
                        "summary": {"unsafe": unsafe_paths, "safe": safe_paths},
                        "tool_call_id": "tool-canonical-paths",
                    }
                ),
            ),
        )

    with _client(tmp_path) as client:
        response = client.get(f"/api/workbench/turns/{turn.id}/events?after=0&limit=10")

    summary = response.json()[1]["payload"]["summary"]
    assert response.status_code == 200
    assert summary["unsafe"] == unsafe_paths
    assert summary["safe"] == safe_paths


def test_public_event_projection_preserves_repeated_slashes_and_file_uris(
    tmp_path: Path,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Repeated slashes", runtime_kind="codex")
    turn = store.create_turn(
        task.id, user_text="Paths", client_request_id="repeated-slash-request"
    )
    unsafe_paths = [
        "//etc/passwd",
        "///etc/passwd",
        "//api/workbench/tasks/123",
        "///workbench-assets/index.js",
        "file:///etc/passwd",
        "file://localhost/etc/passwd",
        "file://host/private/file",
    ]
    safe_url = "https://example.com///etc/passwd"
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
                            "unsafe": unsafe_paths,
                            "embedded": "x=//etc file=file:///etc/passwd",
                            "safe": safe_url,
                        },
                        "tool_call_id": "tool-repeated-slashes",
                    }
                ),
            ),
        )

    with _client(tmp_path) as client:
        response = client.get(f"/api/workbench/turns/{turn.id}/events?after=0&limit=10")

    summary = response.json()[1]["payload"]["summary"]
    assert response.status_code == 200
    assert summary["unsafe"] == unsafe_paths
    assert summary["embedded"] == "x=//etc file=file:///etc/passwd"
    assert summary["safe"] == safe_url


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
    original_snapshot = WorkbenchStore.event_stream_snapshot

    def snapshot_with_terminal_barrier(self, turn_id, after_id=0, *, limit=100):
        result = original_snapshot(self, turn_id, after_id, limit=limit)
        if turn_id == controller["turn_id"]:
            controller["calls"] += 1
            if controller["calls"] == 2:
                assert result[0] == []
                controller["empty_query_complete"].set()
                assert controller["terminal_committed"].wait(1)
                return result[0], self.get_turn(turn_id)
        return result

    monkeypatch.setattr(
        WorkbenchStore, "event_stream_snapshot", snapshot_with_terminal_barrier
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
