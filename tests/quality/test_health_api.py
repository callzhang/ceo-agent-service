from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.audit_web import create_audit_app
from app.store import AutoReplyStore


def test_liveness_stays_available_while_readiness_fails(tmp_path) -> None:
    db_path = tmp_path / "service.sqlite3"
    AutoReplyStore(db_path)
    client = TestClient(create_audit_app(db_path))

    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    assert client.get("/api/quality").status_code == 200
    assert client.get("/").status_code == 200


def test_ready_quality_page_and_incident_lifecycle(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "service.sqlite3"
    store = AutoReplyStore(db_path)
    now = datetime.now(timezone.utc)
    store.record_component_health("producer", success=True, observed_at=now)
    store.record_component_health("consumer", success=True, observed_at=now)
    store.record_quality_run(
        suite="protocol",
        mode="recorded",
        commit_sha="synthetic-commit",
        status="passed",
        total=1,
        passed=1,
        failed=0,
        score=1.0,
    )
    assert store.open_quality_incident(
        "synthetic_incident", severity="medium", summary_code="synthetic"
    )
    monkeypatch.setenv("CEO_QUALITY_REQUIRED_COMPONENTS", "producer, consumer, ")
    client = TestClient(create_audit_app(db_path))

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    page = client.get("/quality")
    assert page.status_code == 200
    assert "synthetic_incident" in page.text
    assert "protocol" in page.text

    invalid = client.post(
        "/api/quality/incidents/synthetic_incident/ack",
        json={"owner": "", "due_at": ""},
    )
    assert invalid.status_code == 400
    acknowledged = client.post(
        "/api/quality/incidents/synthetic_incident/ack",
        json={"owner": "quality-owner", "due_at": "2026-08-01"},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "acknowledged"
    assert client.post(
        "/api/quality/incidents/synthetic_incident/ack",
        json={"owner": "quality-owner", "due_at": "2026-08-01"},
    ).status_code == 409
    assert client.post(
        "/api/quality/incidents/synthetic_incident/resolve"
    ).status_code == 200
    assert client.post(
        "/api/quality/incidents/synthetic_incident/resolve"
    ).status_code == 409


def test_quality_page_has_empty_state_rows(tmp_path) -> None:
    db_path = tmp_path / "service.sqlite3"
    AutoReplyStore(db_path)
    page = TestClient(create_audit_app(db_path)).get("/quality")

    assert page.status_code == 200
    assert "No quality runs recorded" in page.text
    assert "No quality incidents" in page.text
