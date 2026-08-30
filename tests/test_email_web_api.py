from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.email_store import EmailStore
from app.web_api.email import register_email_routes


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "worker.sqlite3"
    app = FastAPI()
    register_email_routes(app, lambda: EmailStore(database))
    return TestClient(app)


@pytest.mark.parametrize(
    ("category", "actions", "action_parameters"),
    (
        ("work", ["label"], {"label": {"labels": ["work"]}}),
        (
            "billing",
            ["move"],
            {"move": {"target_folder": "Archive/Billing"}},
        ),
        (
            "important",
            ["auto_reply"],
            {"auto_reply": {"instruction": "Acknowledge receipt"}},
        ),
    ),
)
def test_email_config_api_persists_valid_action_parameters(
    tmp_path: Path,
    category: str,
    actions: list[str],
    action_parameters: dict[str, dict[str, object]],
):
    with _client(tmp_path) as client:
        response = client.put(
            f"/api/console/email/config/{category}",
            json={
                "description": "Configured category",
                "threshold": 0.95,
                "actions": actions,
                "action_parameters": action_parameters,
                "enabled": True,
                "config_version": "email-config-v1",
            },
        )

    assert response.status_code == 200
    assert response.json()["item"]["action_parameters"] == action_parameters


@pytest.mark.parametrize(
    ("actions", "action_parameters"),
    (
        (["label"], None),
        (["move"], {"move": {"target_folder": " "}}),
        (["auto_reply"], {"auto_reply": {"instruction": ""}}),
        (["archive"], {"archive": {"folder": "Archive"}}),
        (["trash"], {"trash": {"permanent_delete": True}}),
        (["label"], {"unknown": {"labels": ["work"]}}),
    ),
)
def test_email_config_api_returns_controlled_4xx_for_invalid_action_parameters(
    tmp_path: Path,
    actions: list[str],
    action_parameters: dict[str, dict[str, object]] | None,
):
    payload = {
        "description": "Invalid category config",
        "threshold": 0.95,
        "actions": actions,
        "enabled": True,
        "config_version": "email-config-v1",
    }
    if action_parameters is not None:
        payload["action_parameters"] = action_parameters
    with _client(tmp_path) as client:
        response = client.put(
            "/api/console/email/config/work",
            json=payload,
        )

    assert response.status_code == 400


def test_email_config_api_retains_no_parameter_archive_behavior(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.put(
            "/api/console/email/config/subscription",
            json={
                "description": "Archive subscription",
                "threshold": 0.98,
                "actions": ["archive"],
                "enabled": True,
                "config_version": "email-config-v1",
            },
        )

    assert response.status_code == 200
    assert response.json()["item"]["actions"] == ["archive"]
    assert response.json()["item"]["action_parameters"] == {}
