from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.web_api.registration import register_console_routes
from test_store_task_entity_creator import _target_store


def test_feedback_iteration_api_reports_disabled_capability_and_rejects_claim(tmp_path):
    store = _target_store(tmp_path / "feedback.sqlite3")
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
    store = _target_store(tmp_path / "feedback.sqlite3")
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.claim_feedback_processing_items("batch-1", ["manual:1"])
    store.associate_feedback_processing_turn(
        "manual:1", expected_batch_id="batch-1", workbench_task_id="100",
        workbench_turn_id="200", attempt_id=1, agent_run_id=2,
    )
    app = FastAPI()
    register_console_routes(app, store_factory=lambda: store, status_payload_factory=lambda: {}, feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [])
    payload = {
        "workbench_task_id": "101", "workbench_turn_id": "201",
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


@pytest.mark.parametrize(
    ("task_id", "turn_id"),
    [(1, "actual-turn"), ("100", None)],
)
def test_feedback_iteration_decision_api_rejects_non_string_provenance(
    tmp_path, task_id, turn_id
):
    store = _target_store(tmp_path / "feedback.sqlite3")
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.claim_feedback_processing_items("batch-1", ["manual:1"])
    store.associate_feedback_processing_turn(
        "manual:1", expected_batch_id="batch-1", workbench_task_id="100",
        workbench_turn_id="200", attempt_id=1, agent_run_id=2,
    )
    app = FastAPI()
    register_console_routes(app, store_factory=lambda: store, status_payload_factory=lambda: {}, feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [])
    payload = {
        "workbench_task_id": task_id, "workbench_turn_id": turn_id,
        "decision": {
            "scope": "skill_only", "root_cause": "Procedure is incomplete.",
            "feedback_keys": ["manual:1"], "source_references": ["attempt#1"],
            "target_skill_revisions": [{"skill_id": 1, "from_revision": 1, "to_revision": 2}],
            "why_not_code": "The required tool already exists.",
            "acceptance": {"scenario": "attempt#1", "expected_behavior": "Read detail.", "verification": ["test"]},
        },
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/console/feedback/batches/batch-1/decisions", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_feedback_item_patch_keeps_empty_optional_workbench_references(tmp_path):
    store = _target_store(tmp_path / "feedback.sqlite3")
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.claim_feedback_processing_items("batch-1", ["manual:1"])
    app = FastAPI()
    register_console_routes(app, store_factory=lambda: store, status_payload_factory=lambda: {}, feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [])

    with TestClient(app) as client:
        response = client.patch(
            "/api/console/feedback/items/manual:1",
            json={"workbench_task_id": "", "workbench_turn_id": "", "attempt_id": 7},
        )

    assert response.status_code == 200
    item = store.get_feedback_processing_item("manual:1")
    assert item.workbench_task_id == ""
    assert item.workbench_turn_id == ""
    assert item.attempt_id == 7


@pytest.mark.parametrize(
    ("scope", "invalid_load", "unknown_field", "expected_status", "expected_error"),
    [
        ("runtime_config", False, False, 200, None),
        ("runtime_config", True, False, 409, "load receipt"),
        ("code", False, False, 409, "commit"),
        ("runtime_config", False, True, 409, "unsupported feedback resolution field"),
    ],
)
def test_feedback_resolve_api_preserves_native_scope_evidence(
    tmp_path, scope, invalid_load, unknown_field, expected_status, expected_error,
):
    from app.feedback_processing import FeedbackIterationDecision
    from test_feedback_iteration import _decision, _resolution_receipt

    store = _target_store(tmp_path / "scope-api.sqlite3")
    previous = store.create_runtime_skill_config({}, expected_parent_id=None)
    store.record_runtime_skill_load(previous.id, pid=201, loaded={})
    target = store.create_runtime_skill_config({}, expected_parent_id=previous.id)
    load = store.record_runtime_skill_load(target.id, pid=202, loaded={})
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.claim_feedback_processing_items("scope-api", ["manual:1"])
    store.associate_feedback_processing_turn(
        "manual:1", expected_batch_id="scope-api", workbench_task_id="101",
        workbench_turn_id="102", attempt_id=1, agent_run_id=2,
    )
    evidence = _resolution_receipt(
        commit_sha="", runtime_config_id=target.id,
        previous_runtime_config_id=previous.id,
        load_receipt_id=load.id + 1000 if invalid_load else load.id,
        skill_revisions=[],
        backlog_evidence={"processing": 1, "failed": 12, "retryable": 0},
    )
    store.patch_feedback_processing_item_evidence(
        "manual:1", test_evidence=evidence.test_evidence,
        restart_evidence=evidence.restart_evidence,
        health_evidence=evidence.health_evidence,
    )
    decision = FeedbackIterationDecision.model_validate({
        **_decision(feedback_key="manual:1").model_dump(),
        "scope": scope, "target_skill_revisions": [],
        "target_runtime_config_id": target.id if scope == "runtime_config" else None,
    })
    store.record_feedback_iteration_decision(
        "scope-api", decision, workbench_task_id="101", workbench_turn_id="102",
    )
    app = FastAPI()
    register_console_routes(
        app, store_factory=lambda: store, status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [],
    )
    payload = evidence.model_dump(mode="json", exclude={"backlog_evidence"})
    payload["feedback_ids"] = ["manual:1"]
    if unknown_field:
        payload["unrecognized_evidence"] = True
    with TestClient(app) as client:
        response = client.post(
            "/api/console/feedback/batches/scope-api/resolve", json=payload,
        )
    assert response.status_code == expected_status, response.text
    assert store.get_feedback_processing_item("manual:1").status == (
        "resolved" if expected_status == 200 else "processing"
    )
    if expected_error is not None:
        assert expected_error in response.json()["message"]
