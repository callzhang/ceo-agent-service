from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AutoReplyStore
from app.managed_skills import REPOSITORY_MANAGED_SKILL_NAMES
from app.web_api.registration import register_console_routes


def _content(name: str, description: str = "Managed test Skill") -> str:
    return f"""---
name: {name}
description: {description}
metadata:
  managed_by: ceo-agent-service
---

# {name}
"""


def _client(tmp_path: Path) -> tuple[TestClient, AutoReplyStore]:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    app = FastAPI()
    register_console_routes(
        app,
        store_factory=lambda: store,
        status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: {"processing": 0, "failed": 0, "retryable": 0},
        attention_rows_factory=lambda: [],
    )
    return TestClient(app), store


def test_managed_skills_create_revision_config_and_read_load_receipt(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client:
        created = client.post(
            "/api/console/settings/managed-skills",
            json={"name": "ceo-test", "display_name": "Test Skill"},
        )
        skill_id = created.json()["id"]
        revision = client.post(
            f"/api/console/settings/managed-skills/{skill_id}/revisions",
            json={"content": _content("ceo-test")},
        )
        revision_id = revision.json()["id"]
        predecessor = client.get("/api/console/settings/runtime-skill-configs/current")
        config = client.post(
            "/api/console/settings/runtime-skill-configs",
            json={
                "expected_parent_id": predecessor.json()["pending_or_active"]["id"],
                "bindings": [{"skill_id": skill_id, "revision_id": revision_id}],
            },
        )

    assert created.status_code == 201
    assert revision.status_code == 201
    assert config.status_code == 201
    assert config.json()["status"] == "pending_restart"
    receipt = store.record_runtime_skill_load(
        config.json()["id"], pid=123, loaded={skill_id: revision.json()["sha256"]}
    )
    with client:
        listed = client.get(f"/api/console/settings/runtime-skill-configs/{config.json()['id']}/load-receipts")
        revision_detail = client.get(f"/api/console/settings/managed-skill-revisions/{revision_id}")
    assert listed.status_code == 200
    assert listed.json()["items"] == [{
        "id": receipt.id, "config_id": config.json()["id"], "pid": 123,
        "loaded_json": receipt.loaded_json, "error": "", "created_at": receipt.created_at,
    }]
    assert revision_detail.status_code == 200
    assert revision_detail.json()["id"] == revision_id


def test_fresh_console_managed_skill_api_initializes_service_owned_baseline(
    tmp_path: Path,
) -> None:
    client, _store = _client(tmp_path)

    with client:
        skills = client.get("/api/console/settings/managed-skills")
        current = client.get("/api/console/settings/runtime-skill-configs/current")

    assert skills.status_code == 200
    assert {item["name"] for item in skills.json()["items"]} == set(
        REPOSITORY_MANAGED_SKILL_NAMES
    )
    assert current.status_code == 200
    assert current.json()["pending_or_active"]["status"] == "pending_restart"


def test_managed_skill_api_rejects_unknown_ids_paths_and_config_conflicts(tmp_path: Path) -> None:
    client, _store = _client(tmp_path)
    with client:
        assert client.get("/api/console/settings/managed-skills/../revisions").status_code in {404, 422}
        assert client.post("/api/console/settings/managed-skills/999/revisions", json={"content": _content("ceo-test")}).status_code == 404
        assert client.get("/api/console/settings/managed-skill-revisions/999").status_code == 404
        assert client.post("/api/console/settings/managed-skill-revisions/999/export").status_code == 404
        conflict = client.post("/api/console/settings/runtime-skill-configs", json={"expected_parent_id": 999, "bindings": []})
    assert conflict.status_code == 409


def test_managed_skill_api_rejects_unsafe_name_without_persisting_it(tmp_path: Path) -> None:
    client, store = _client(tmp_path)

    with client:
        response = client.post(
            "/api/console/settings/managed-skills",
            json={"name": r"ceo\\test", "display_name": "Test Skill"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert store.get_managed_skill_by_name(r"ceo\\test") is None
