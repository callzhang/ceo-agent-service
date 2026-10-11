from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.audit_web import _queue_attention_rows
from app.store import AgentRole, AutoReplyStore
from app.web_api.registration import register_console_routes
from app.web_api.status import AttentionRow


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
        "meeting_memory_health": {
            "pending": 0,
            "due": 0,
            "delayed": 0,
            "processing": 0,
            "retryable": 0,
            "failed": 0,
            "oldest_due_at": "",
            "oldest_due_seconds": 0,
            "completed_last_hour": 0,
            "active_agents": 0,
            "ghost_runtime_attempts": 0,
            "delayed_after_seconds": 1800,
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


def test_console_status_preserves_real_current_run_attention_diagnostics(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="diagnostic-chat", conversation_title="Diagnostic",
        single_chat=True, trigger_message_id="diagnostic-message",
        trigger_create_time="2026-10-04 13:05:00", trigger_sender="Morgan",
        trigger_text="Review this request.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="diagnostic", owner="test",
    ).run
    store.fail_agent_run(run.id, {
        "code": "agent_reported_failure", "source": "agent",
        "source_code": "provider_risk_rejected", "retryable": False,
        "reported_summary": "The tool call was rejected before external execution.",
    }, owner="test")
    store.fail_reply_task(
        task.id, "agent_reported_failure",
        expected_execution_generation=task.execution_generation,
    )
    rows = _queue_attention_rows(store)
    payload = _status_payload()
    payload["attention_rows"] = rows
    app = FastAPI()
    register_console_routes(
        app, lambda: store, status_payload_factory=lambda: payload,
        feedback_backlog_factory=lambda: [], attention_rows_factory=lambda: rows,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/console/status")

    assert response.status_code == 200
    [row] = response.json()["item"]["attention_rows"]
    assert row["id"] == str(task.id)
    assert row["status"] == "failed"
    assert row["error_code"] == "provider_risk_rejected"
    assert row["error"] == rows[0]["error"]


def test_attention_status_contract_still_rejects_unknown_fields() -> None:
    row = {
        "category": "Reply task", "id": "1", "status": "failed", "context": "",
        "summary": "", "updated_at": "", "error": "provider failure",
        "error_code": "provider_failure", "undeclared_diagnostic": "not allowed",
    }
    with pytest.raises(ValidationError) as error:
        AttentionRow.model_validate(row)

    assert [entry["loc"] for entry in error.value.errors()] == [("undeclared_diagnostic",)]


def test_attention_status_contract_rejects_non_string_error_code() -> None:
    row = {
        "category": "Reply task", "id": "1", "status": "failed", "context": "",
        "summary": "", "updated_at": "", "error": "provider failure", "error_code": 123,
    }
    with pytest.raises(ValidationError) as error:
        AttentionRow.model_validate(row)

    assert error.value.errors()[0]["type"] == "string_type"


@pytest.mark.parametrize("diagnostics", [{}, {"error_code": None}, {"error_code": "provider_failure"}])
def test_attention_status_contract_accepts_optional_diagnostic_code(diagnostics: dict[str, object]) -> None:
    row = AttentionRow.model_validate({
        "category": "Reply task", "id": "1", "status": "failed", "context": "",
        "summary": "", "updated_at": "", "error": "provider failure", **diagnostics,
    })

    assert row.error_code == diagnostics.get("error_code")
