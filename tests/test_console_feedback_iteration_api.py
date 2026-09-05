from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AutoReplyStore
from app.web_api.registration import register_console_routes


def test_feedback_iteration_api_reports_disabled_capability_and_rejects_claim(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.set_feedback_iteration_enabled(False)
    app = FastAPI()
    register_console_routes(app, store_factory=lambda: store, status_payload_factory=lambda: {}, feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [])

    with TestClient(app) as client:
        capability = client.get("/api/console/settings/feedback-iteration")
        claim = client.post("/api/console/feedback/batches", json={"batch_id": "batch-1", "feedback_keys": ["manual:1"]})

    assert capability.status_code == 200
    assert capability.json()["enabled"] is False
    assert claim.status_code == 409
    assert claim.json()["code"] == "feedback_iteration_disabled"


def test_feedback_iteration_decision_api_rejects_forged_round_identity(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.claim_feedback_processing_items("batch-1", ["manual:1"])
    store.associate_feedback_processing_turn(
        "manual:1", expected_batch_id="batch-1", workbench_task_id="actual-task",
        workbench_turn_id="actual-turn", attempt_id=1, agent_run_id=2,
    )
    app = FastAPI()
    register_console_routes(app, store_factory=lambda: store, status_payload_factory=lambda: {}, feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [])
    payload = {
        "workbench_task_id": "forged-task", "workbench_turn_id": "forged-turn",
        "decision": {
            "scope": "skill_only", "root_cause": "Procedure is incomplete.",
            "feedback_keys": ["manual:1"], "source_references": ["attempt#1"],
            "target_skill_revisions": [{"skill_id": 1, "from_revision": 1, "to_revision": 2}],
            "why_not_code": "The required tool already exists.",
            "acceptance": {"scenario": "attempt#1", "expected_behavior": "Read detail.", "verification": ["test"]},
        },
    }

    with TestClient(app) as client:
        response = client.post("/api/console/feedback/batches/batch-1/decisions", json=payload)

    assert response.status_code == 409
    assert response.json()["code"] == "feedback_iteration_association_mismatch"
