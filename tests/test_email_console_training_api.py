from types import SimpleNamespace
import json
from copy import deepcopy

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.email_store import EmailStore
from app.email_model_registry import EmailModelRegistry
from app.web_api.email import register_email_routes


def client_for(tmp_path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    registry = EmailModelRegistry(tmp_path / "models")
    app = FastAPI()
    register_email_routes(app, lambda: store, email_learning_factory=lambda: SimpleNamespace(
        registry=registry, retrain_state_path=tmp_path / "training.json"))
    return TestClient(app), store, registry


def test_training_console_empty_registry_explains_agent_mode(tmp_path):
    client, store, registry = client_for(tmp_path)
    response = client.get("/api/console/email/learning")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning["runtime"]["mode"] == "agent_primary"
    assert learning["runtime"]["toggle_enabled"] is False
    assert learning["promotion_gate"]["config"]["macro_f1_min"] == .95
    assert learning["promotion_gate"]["promotion_eligible"] is False


def test_promotion_config_changes_do_not_activate_model(tmp_path):
    client, store, registry = client_for(tmp_path)
    initial = store.current_model_promotion_config()
    payload = {"expected_current_version": initial["config_version"],
               "macro_f1_min": .94, "category_precision_min": .96,
               "category_validation_samples_min": 25, "p95_latency_max_ms": 400.}
    response = client.put("/api/console/email/promotion-config", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["config"]["config_version"] != initial["config_version"]
    assert not (registry.root / "online-active.json").exists()
    assert client.put("/api/console/email/promotion-config", json=payload).status_code == 409


def test_primary_switch_rejects_unmeasured_candidate(tmp_path):
    client, store, registry = client_for(tmp_path)
    response = client.put("/api/console/email/runtime-mode", json={
        "mode": "model_primary", "model_id": "no-candidate", "request_id": "r1",
        "expected_mode": "agent_primary", "expected_model_id": None,
    })
    assert response.status_code == 409
    assert not (registry.root / "online-active.json").exists()


@pytest.mark.parametrize("token", [None, "", "   ", "missing"])
def test_category_api_requires_nonblank_current_version(tmp_path, token):
    client, store, _ = client_for(tmp_path)
    initial = store.get_category_config("work")
    payload = {"core_description": "工作业务", "include": ["客户项目"],
               "exclude": ["个人生活"], "threshold": .95, "enabled": False}
    if token != "missing":
        payload["expected_current_version"] = token
    response = client.put("/api/console/email/config/work", json=payload)
    assert response.status_code == 400
    assert store.get_category_config("work") == initial


def test_list_includes_current_provider_important_state(tmp_path, monkeypatch):
    client, store, registry = client_for(tmp_path)
    monkeypatch.setattr(store, "list_classifications", lambda **_: ([{"id": 42}], 1))
    monkeypatch.setattr(store, "get_provider_classification_state", lambda _: {
        "state": "categorized", "category_key": "legal", "important": True,
    })
    item = client.get("/api/console/email/classifications?status=processed").json()["items"][0]
    assert item["important"] is True
    assert item["provider_classification"]["category_key"] == "legal"


def test_category_update_rejects_stale_version_before_provider_calls(tmp_path):
    client, store, registry = client_for(tmp_path)
    config = store.get_category_config("work")
    response = client.put("/api/console/email/config/work", json={
        "core_description": "工作业务", "include": ["客户项目"], "exclude": ["个人生活"],
        "threshold": .95, "enabled": False, "description_version": "new-description",
        "config_version": "new-config", "expected_current_version": "stale-version",
    })
    assert response.status_code == 409
    assert store.get_category_config("work") == config


def ready_client(tmp_path, monkeypatch):
    from copy import deepcopy
    from hashlib import sha256
    from test_email_web_api import _valid_model_detail_evidence
    from app import email_classifier_runtime as runtime
    from app.email_worker import _active_description_set_version

    client, store, registry = client_for(tmp_path)
    with store._connect() as db:
        db.execute("update email_category_configs set enabled=(category_key='legal')")
    current = _valid_model_detail_evidence()
    current["compatibility"]["description_version"] = _active_description_set_version(store)
    current["metrics"]["categories"]["legal"].update(precision=.98, recall=.98, f1=.98, support=25)
    from test_email_promotion_gate import measured_latency
    current["end_to_end_latency_ms"] = measured_latency()
    artifact = b"verified-test-model"
    current["hashes"]["artifact_sha256"] = sha256(artifact).hexdigest()
    previous = deepcopy(current)
    previous["model_id"] = current["compatibility"]["parent_model_id"]
    previous["source_snapshot_id"] = "email-folder-snapshot-20260908T070000.000000Z-cccccccccccc"
    previous["source_snapshot_digest"] = "c" * 64
    previous["source_snapshot_observed_at"] = "2026-09-08T07:00:00+00:00"
    previous["trained_at"] = "2026-09-08T07:01:00+00:00"
    previous["folder_label_watermark"] = 24
    previous["important_label_watermark"] = 19
    for metric in [previous["metrics"]["categories"]["legal"], previous["metrics"]["important"]]:
        metric["accepted_hits"] -= 1
        metric["independent_groups"] -= 1
    import json
    for evidence in (previous, current):
        (registry.staged_evidence / (evidence["model_id"] + ".json")).write_text(json.dumps(evidence))
        (registry.embedding_artifacts / (evidence["model_id"] + ".artifact")).write_bytes(artifact)
    prepare = runtime._prepare_online_activation
    model = SimpleNamespace(enabled_categories=("legal",), input_schema_version="email-folder-model-input-v2",
                            embedding_model_id="jina-small", embedding_revision="gpu4-r17")
    monkeypatch.setattr(runtime, "_prepare_online_activation",
                        lambda registry, model_id, **_: prepare(registry, model_id, classifier_loader=lambda _: model))
    return client, store, registry, current


def enable_request(current, request_id="switch-ready"):
    return {"mode": "model_primary", "model_id": current["model_id"], "request_id": request_id,
            "expected_mode": "agent_primary", "expected_model_id": None}


def test_ready_candidate_switch_and_disable_roundtrip(tmp_path, monkeypatch):
    client, store, registry, current = ready_client(tmp_path, monkeypatch)
    learning = client.get("/api/console/email/learning").json()["learning"]
    assert learning["runtime"]["candidate_ready"] is True, learning["promotion_gate"]
    request = {"mode": "model_primary", "model_id": current["model_id"], "request_id": "switch-ready",
               "expected_mode": "agent_primary", "expected_model_id": None}
    response = client.put("/api/console/email/runtime-mode", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["runtime"]["mode"] == "model_primary"
    assert client.put("/api/console/email/runtime-mode", json=request).status_code == 200
    response = client.put("/api/console/email/runtime-mode", json={
        "mode": "agent_primary", "model_id": None, "request_id": "switch-back",
        "expected_mode": "model_primary", "expected_model_id": current["model_id"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["runtime"]["mode"] == "agent_primary"
    assert len(response.json()["mode_transitions"]) == 2


def test_active_model_with_unreadable_staged_file_keeps_learning_available(tmp_path, monkeypatch):
    client, _, registry, current = ready_client(tmp_path, monkeypatch)
    assert client.put("/api/console/email/runtime-mode", json=enable_request(current)).status_code == 200
    corrupt_id = "email-embedding-mlp-20260908T100000Z-deadbeef"
    (registry.staged_evidence / (corrupt_id + ".json")).write_text("{invalid-json")
    response = client.get("/api/console/email/learning")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning["runtime"]["mode"] == "agent_primary"
    assert learning["promotion_gate"]["promotion_eligible"] is False
    assert any(row["model_id"] == current["model_id"] for row in learning["staged_models"])
    assert learning["registry_issues"]


def test_switch_rejects_same_corrupt_inventory_as_learning(tmp_path, monkeypatch):
    client, store, registry, current = ready_client(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, "list_model_inventory", lambda: [SimpleNamespace(
        metadata=None, integrity_status="corrupt", integrity_error="artifact_digest_mismatch")])
    learning = client.get("/api/console/email/learning").json()["learning"]
    assert learning["promotion_gate"]["promotion_eligible"] is False
    assert learning["registry_issues"]
    response = client.put("/api/console/email/runtime-mode", json=enable_request(current))
    assert response.status_code == 409
    assert not (registry.root / "online-active.json").exists()


def test_learning_keeps_model_rows_when_candidate_artifact_is_unreadable(tmp_path, monkeypatch):
    from pathlib import Path
    client, store, registry, current = ready_client(tmp_path, monkeypatch)
    artifact = registry.embedding_artifacts / f"{current['model_id']}.artifact"
    read_bytes = Path.read_bytes

    def unreadable(path):
        if path == artifact:
            raise OSError("private-artifact-path")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    response = client.get("/api/console/email/learning")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert len(learning["staged_models"]) == 2
    assert learning["promotion_gate"]["promotion_eligible"] is False
    assert "private-artifact-path" not in response.text


@pytest.mark.parametrize("path", [("end_to_end_latency_ms",), ("metrics",),
    ("metrics", "categories"), ("metrics", "categories", "legal"),
    ("metrics", "important"), ("head_latency_ms",), ("evaluation",)])
@pytest.mark.parametrize("value", [[], "private-invalid-value", 1])
def test_learning_preserves_healthy_rows_with_malformed_metric_containers(tmp_path, monkeypatch, path, value):
    client, store, registry, current = ready_client(tmp_path, monkeypatch)
    corrupt = deepcopy(current)
    target = corrupt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    (registry.staged_evidence / f"{current['model_id']}.json").write_text(json.dumps(corrupt))
    response = client.get("/api/console/email/learning")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert [row["model_id"] for row in learning["staged_models"]] == [current["compatibility"]["parent_model_id"]]
    assert learning["registry_issues"]
    assert learning["promotion_gate"]["promotion_eligible"] is False
    assert "private-invalid-value" not in response.text


@pytest.mark.parametrize("payload", [[], "private-manifest", {"mode_transitions": [{}]},
    {"mode": "agent_primary", "promotion_config_version": "v1", "mode_transitions": []}])
def test_learning_reports_corrupt_control_manifest_and_switch_preserves_it(tmp_path, payload):
    client, store, registry = client_for(tmp_path)
    manifest = registry.root / "online-active.json"
    manifest.write_text(json.dumps(payload))
    before = manifest.read_bytes()
    response = client.get("/api/console/email/learning")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert any(issue["integrity_error"] == "online_control_invalid" for issue in learning["registry_issues"])
    assert learning["mode_transitions"] == []
    assert "private-manifest" not in response.text
    response = client.put("/api/console/email/runtime-mode", json={
        "mode": "agent_primary", "model_id": None, "expected_mode": "agent_primary",
        "expected_model_id": None, "request_id": "corrupt-history",
    })
    assert response.status_code == 409
    assert manifest.read_bytes() == before


def test_category_versions_are_generated_and_history_preserves_descriptions(tmp_path):
    from test_email_web_api import _VerifiedFolderCoordinator, _dynamic_category_payload
    store = EmailStore(tmp_path / "category.sqlite3")
    app = FastAPI()
    register_email_routes(app, lambda: store, folder_binding_coordinator=_VerifiedFolderCoordinator())
    client = TestClient(app)
    payload = _dynamic_category_payload()
    del payload["config_version"], payload["description_version"]
    response = client.post("/api/console/email/config", json=payload)
    assert response.status_code == 201, response.text
    first = response.json()["item"]
    update = {key: payload[key] for key in (
        "core_description", "include", "exclude", "threshold", "enabled",
    )}
    update.update(core_description="Updated partner criteria", expected_current_version=first["config_version"])
    response = client.put("/api/console/email/config/partner_updates", json=update)
    assert response.status_code == 200, response.text
    second = response.json()["item"]
    assert first["config_version"] != second["config_version"]
    assert first["description_version"] != second["description_version"]
    assert client.put("/api/console/email/config/partner_updates", json=update).status_code == 409
    response = client.get("/api/console/email/config/partner_updates/history")
    assert response.status_code == 200
    rows = response.json()["items"]
    assert [row["config_version"] for row in rows] == [second["config_version"], first["config_version"]]
    assert [row["config"]["core_description"] for row in rows] == [second["core_description"], first["core_description"]]
    assert all(row["description_version"] == row["config"]["description_version"] for row in rows)
    assert all(row["revision_id"] and row["created_at"] for row in rows)
    assert client.get("/api/console/email/config/unknown_category/history").status_code == 404


def test_category_history_projects_only_description_configuration(tmp_path, monkeypatch):
    client, store, registry = client_for(tmp_path)
    revisions = store.list_category_description_revisions("work")
    revisions[0]["private_rows"] = ["private-row"]
    revisions[0]["config"]["action_parameters"] = {"private": "private-action-url"}
    revisions[0]["config"]["unexpected"] = "private-account"
    monkeypatch.setattr(store, "list_category_description_revisions", lambda _: revisions)
    response = client.get("/api/console/email/config/work/history")
    assert response.status_code == 200
    assert response.json()["items"][0]["config"]["core_description"]
    assert "private-" not in response.text
