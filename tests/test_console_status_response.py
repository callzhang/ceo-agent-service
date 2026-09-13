from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web_api.registration import register_console_routes


def _status_payload() -> dict[str, object]:
    return {
        "service": {
            "label": "main",
            "target": "gui/501/main",
            "ok": True,
            "state": "running",
            "detail": "running",
            "pid": "42",
            "runs": "1",
            "initialized": "1",
            "last_terminating_signal": "",
            "returncode": 0,
        },
        "system_health": {
            "state": "healthy",
            "detail": "healthy",
            "checked_at": "2026-09-13T00:00:00Z",
            "violations": 0,
            "components": [],
        },
        "components": [],
        "connectors": {},
        "email": {
            "status": "ready",
            "updated_at": "",
            "process": None,
            "runtime_loops": [],
            "accounts": [],
            "checks": [],
        },
        "wechat": {
            "reader": {"enabled": True, "status": "ready", "error": ""},
            "sender": {"enabled": True, "status": "ready", "error": ""},
            "preflight": {"status": "on_send", "error": ""},
            "account": {"ready": True, "account_id": "account"},
        },
        "queues": [],
        "dispatcher_queues": [
            {
                "name": "scheduled",
                "pending": 0,
                "due": 0,
                "oldest_available_at": None,
                "running": 0,
                "latest_error": "",
            }
        ],
        "attention_rows": [],
        "database": {"path": "/tmp/worker.sqlite3"},
        "summary": {
            "queue_count": 0,
            "pending": 0,
            "processing": 0,
            "failed": 0,
            "retryable": 0,
            "attention": 0,
        },
    }


def test_console_status_preserves_required_nullable_fields() -> None:
    app = FastAPI()
    register_console_routes(
        app,
        lambda: object(),
        status_payload_factory=_status_payload,
        feedback_backlog_factory=lambda: [],
        attention_rows_factory=lambda: [],
    )

    with TestClient(app) as client:
        response = client.get("/api/console/status")

    assert response.status_code == 200
    assert response.json()["item"]["dispatcher_queues"][0]["oldest_available_at"] is None
