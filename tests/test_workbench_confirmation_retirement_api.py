"""Workbench's retired confirmation surface, without a database or runtime."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.workbench.api import (
    _public_event,
    _resource_cursor,
    _turn_cursor,
    register_workbench_routes,
)
from app.workbench.models import (
    WorkbenchArtifact,
    WorkbenchAttachment,
    WorkbenchEvent,
    WorkbenchTask,
    WorkbenchTurn,
)


@pytest.fixture
def surface(tmp_path: Path):
    timestamp = "2026-10-16 00:00:00"
    task = WorkbenchTask(
        id=str(uuid4()), title="Native work", runtime_kind="codex",
        created_at=timestamp, updated_at=timestamp,
    )
    turn = WorkbenchTurn(
        id=str(uuid4()), task_id=task.id, client_request_id="request",
        task_sequence=4, user_text="Inspect", status="stopped",
        created_at=timestamp, updated_at=timestamp,
    )
    artifact = WorkbenchArtifact(
        id=str(uuid4()), turn_id=turn.id, label="Output", path="unused",
        media_type="text/plain", created_at=timestamp,
    )
    attachment = WorkbenchAttachment(
        id=str(uuid4()), task_id=task.id, filename="input.txt",
        media_type="text/plain", size_bytes=4, created_at=timestamp,
    )
    calls = []
    counts = {
        "tasks": {"total": 1, "active": 1, "archived": 0},
        "turns": {key: 0 for key in ("queued", "running", "completed", "stopped", "failed")},
        "events": {"tool_completed": 1}, "attachments": 1, "artifacts": 1,
        "duration": {"completed_count": 0, "total_seconds": 0.0, "average_seconds": 0.0},
    }

    def snapshot(task_id, **kwargs):
        calls.append((task_id, kwargs))
        return task, [turn], [], [attachment], [artifact], {
            "task_state": "stopped", "has_more": True,
            "events_has_more": True, "events_next_cursor": 12,
            "artifacts_has_more": True, "attachments_has_more": True,
        }

    app = FastAPI()
    store = SimpleNamespace(
        path=tmp_path / "unused.sqlite3", timeline_snapshot=snapshot,
        workbench_stats=lambda: counts,
    )
    executor = SimpleNamespace(workspace=tmp_path)
    registry = SimpleNamespace(kinds=lambda: [])
    register_workbench_routes(
        app, store, executor, registry, tmp_path / "assets",
        mutation_guard=lambda request: None,
    )
    return app, task, turn, artifact, attachment, calls, counts


def test_removed_confirmation_routes_are_plain_404_and_absent_from_openapi(surface):
    app, task, turn, *_ = surface
    schema = app.openapi()
    assert not any("confirmation" in path for path in schema["paths"])
    with TestClient(app, raise_server_exceptions=False) as client:
        for identifier in (str(uuid4()), "not-a-uuid"):
            for action in ("confirm", "cancel"):
                response = client.post(
                    f"/api/workbench/tasks/{task.id}/turns/{turn.id}"
                    f"/confirmations/{identifier}/{action}", json={},
                )
                assert response.status_code == 404
                assert response.json() == {"detail": "Not Found"}
    schema = app.openapi()
    assert not any("confirmation" in path for path in schema["paths"])
    assert "PublicConfirmation" not in schema["components"]["schemas"]
    parameters = schema["paths"]["/api/workbench/tasks/{task_id}/timeline"]["get"]["parameters"]
    assert {item["name"] for item in parameters} == {
        "task_id", "turn_limit", "event_limit", "before", "event_before",
        "artifact_after", "attachment_after",
    }


def test_timeline_consumes_six_tuple_and_preserves_other_resources_and_cursors(surface):
    app, task, turn, artifact, attachment, calls, _ = surface
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/workbench/tasks/{task.id}/timeline",
            params={
                "turn_limit": 2, "event_limit": 3,
                "before": _turn_cursor(task.id, turn), "event_before": 9,
                "artifact_after": _resource_cursor(artifact),
                "attachment_after": _resource_cursor(attachment),
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "task", "turns", "events", "attachments", "artifacts", "next_cursor",
        "has_more", "events_has_more", "events_next_cursor",
        "artifacts_has_more", "artifacts_next_cursor", "attachments_has_more",
        "attachments_next_cursor",
    }
    assert payload["task"]["id"] == task.id
    assert payload["turns"][0]["id"] == turn.id
    assert payload["attachments"][0]["id"] == attachment.id
    assert payload["artifacts"][0]["id"] == artifact.id
    assert payload["next_cursor"] == _turn_cursor(task.id, turn)
    assert payload["events_next_cursor"] == 12
    assert payload["artifacts_next_cursor"] == _resource_cursor(artifact)
    assert payload["attachments_next_cursor"] == _resource_cursor(attachment)
    assert calls == [(task.id, {
        "turn_limit": 2, "event_limit": 3, "before_sequence": 4, "event_before": 9,
        "artifact_after": (artifact.created_at, artifact.id),
        "attachment_after": (attachment.created_at, attachment.id),
    })]


def test_stats_exposes_only_retained_counts(surface):
    app, *_, counts = surface
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/workbench/stats")
    assert response.status_code == 200
    assert response.json() == counts


@pytest.mark.parametrize("kind", ["status_changed", "turn_failed"])
def test_status_payload_drops_legacy_confirmation_fields_only(tmp_path, kind):
    event = WorkbenchEvent(
        id=1, turn_id=str(uuid4()), sequence=1, event_type=kind,
        payload={
            "status": "failed", "code": "runtime_error",
            "confirmation_id": "retired", "confirmation_status": "executed",
        }, created_at="2026-10-16 00:00:00",
    )
    assert _public_event(event, tmp_path).payload == {
        "status": "failed", "code": "runtime_error",
    }


def test_native_tool_review_payload_is_preserved(tmp_path):
    payload = {
        "tool_call_id": "native-review", "kind": "command",
        "output": "native auto_review approved",
        "provider_item": {"type": "command_execution", "review": {"status": "approved"}},
    }
    event = WorkbenchEvent(
        id=1, turn_id=str(uuid4()), sequence=1, event_type="tool_completed",
        payload=payload, created_at="2026-10-16 00:00:00",
    )
    assert _public_event(event, tmp_path).payload == payload
