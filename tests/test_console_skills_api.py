import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.skill_features import FeatureRegistry
from app.skill_files import SkillFileService
from app.web_api.registration import register_console_routes


def _skill_content(name: str, description: str = "A test skill") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "metadata:\n"
        "  managed_by: ceo-agent-service\n"
        "---\n\n"
        f"# {name}\n"
    )


def _client(tmp_path: Path) -> tuple[TestClient, SkillFileService]:
    skills_root = tmp_path / "skills"
    (skills_root / "ceo-message-triage").mkdir(parents=True)
    content = _skill_content("ceo-message-triage")
    (skills_root / "ceo-message-triage" / "SKILL.md").write_text(content, encoding="utf-8")
    registry_path = tmp_path / "skill-features.json"
    registry_path.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "feature_id": "message_triage",
                        "name": "Message Triage",
                        "description": "Handle messages",
                        "skills": ["ceo-message-triage"],
                        "default_enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = FeatureRegistry(registry_path, tmp_path / "skill-state.json", skills_root)
    service = SkillFileService(skills_root, runtime_skills_root=tmp_path / "runtime-skills")
    app = FastAPI()
    register_console_routes(
        app,
        store_factory=lambda: None,
        status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: {"processing": 0, "failed": 0, "retryable": 0},
        attention_rows_factory=lambda: [],
        feature_registry_factory=lambda: registry,
        skill_file_service_factory=lambda: service,
    )
    return TestClient(app), service


def test_skills_settings_list_exposes_features_skills_status_and_associations(tmp_path: Path):
    with _client(tmp_path)[0] as client:
        response = client.get("/api/console/settings/skills")

    assert response.status_code == 200
    payload = response.json()
    assert payload["features"] == [
        {
            "feature_id": "message_triage",
            "name": "Message Triage",
            "description": "Handle messages",
            "skills": ["ceo-message-triage"],
            "enabled": True,
            "status": "ready",
        }
    ]
    assert payload["skills"][0]["name"] == "ceo-message-triage"
    assert payload["skills"][0]["referenced_by"] == ["message_triage"]


def test_skill_toggle_and_detail_round_trip(tmp_path: Path):
    client, service = _client(tmp_path)
    with client:
        toggled = client.post(
            "/api/console/settings/skills/message_triage/toggle",
            json={"enabled": False},
        )
        detail = client.get("/api/console/settings/skills/ceo-message-triage")

    assert toggled.status_code == 200
    assert toggled.json()["feature_id"] == "message_triage"
    assert toggled.json()["enabled"] is False
    assert detail.status_code == 200
    assert detail.json()["content"] == service.get_skill("ceo-message-triage").content
    assert detail.json()["sha256"] == hashlib.sha256(detail.json()["content"].encode()).hexdigest()
    assert detail.json()["referenced_by"] == ["message_triage"]


def test_skill_update_requires_matching_sha_and_syncs_runtime_copy(tmp_path: Path):
    client, service = _client(tmp_path)
    current = service.get_skill("ceo-message-triage")
    replacement = _skill_content("ceo-message-triage", "Updated")
    with client:
        response = client.put(
            "/api/console/settings/skills/ceo-message-triage",
            json={"content": replacement, "expected_sha256": current.sha256},
        )

    assert response.status_code == 200
    assert response.json()["content"] == replacement
    assert response.json()["sha256"] == hashlib.sha256(replacement.encode()).hexdigest()
    assert (tmp_path / "runtime-skills" / "ceo-message-triage" / "SKILL.md").read_text() == replacement


def test_skill_api_maps_unknown_validation_conflict_and_persistence_failures(tmp_path: Path, monkeypatch):
    client, service = _client(tmp_path)
    with client:
        assert client.get("/api/console/settings/skills/unknown").status_code == 404
        assert client.post(
            "/api/console/settings/skills/message_triage/toggle", json={"enabled": "no"}
        ).status_code == 422
        assert client.put(
            "/api/console/settings/skills/ceo-message-triage",
            json={"content": _skill_content("ceo-message-triage"), "expected_sha256": "0" * 64},
        ).status_code == 409

        def fail(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(service, "save_skill", fail)
        failed = client.put(
            "/api/console/settings/skills/ceo-message-triage",
            json={"content": _skill_content("ceo-message-triage"), "expected_sha256": service.get_skill("ceo-message-triage").sha256},
        )
    assert failed.status_code == 500


def test_skills_list_isolates_malformed_directory_names(tmp_path: Path):
    client, _service = _client(tmp_path)
    malformed = tmp_path / "skills" / "bad name"
    malformed.mkdir()
    malformed.joinpath("SKILL.md").write_text(_skill_content("bad name"), encoding="utf-8")

    with client:
        response = client.get("/api/console/settings/skills")

    assert response.status_code == 200
    row = next(item for item in response.json()["skills"] if item["name"] == "bad name")
    assert row["status"] == "invalid"
    assert row["referenced_by"] == []
    original = malformed.joinpath("SKILL.md").read_text(encoding="utf-8")
    with client:
        assert client.get("/api/console/settings/skills/bad%20name").status_code == 422
        assert client.put(
            "/api/console/settings/skills/bad%20name",
            json={"content": original + "edited", "expected_sha256": "0" * 64},
        ).status_code == 422
    assert malformed.joinpath("SKILL.md").read_text(encoding="utf-8") == original
