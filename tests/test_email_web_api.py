from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import app.email_store as email_store_module
from app.email_store import EmailPersistenceCorruption, EmailStore
from app.web_api.email import register_email_routes


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "worker.sqlite3"
    app = FastAPI()
    register_email_routes(app, lambda: EmailStore(database))
    return TestClient(app)


class _ZeroTimeoutEmailStore(EmailStore):
    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=0)
        db.execute("pragma busy_timeout = 0")
        db.execute("pragma foreign_keys = on")
        db.row_factory = sqlite3.Row
        return db


def test_email_routes_initialize_and_reuse_one_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "singleton.sqlite3"
    initialize_calls = 0
    factory_calls = 0
    original_initialize = EmailStore._initialize

    def counted_initialize(self: EmailStore) -> None:
        nonlocal initialize_calls
        initialize_calls += 1
        original_initialize(self)

    def factory() -> EmailStore:
        nonlocal factory_calls
        factory_calls += 1
        return EmailStore(database)

    monkeypatch.setattr(EmailStore, "_initialize", counted_initialize)
    app = FastAPI()
    register_email_routes(app, factory)

    with TestClient(app) as client:
        classifications = client.get(
            "/api/console/email/classifications?page=1&page_size=1"
        )
        configs = client.get("/api/console/email/config")
        update = client.put(
            "/api/console/email/config/work",
            json={
                "description": "Work",
                "threshold": 0.9,
                "actions": [],
                "enabled": True,
                "config_version": "email-config-v1",
            },
        )
        feedback = client.post(
            "/api/console/email/classifications/999/feedback",
            json={"category": "work"},
        )

    assert classifications.status_code == 200
    assert configs.status_code == 200
    assert update.status_code == 200
    assert feedback.status_code == 404
    assert factory_calls == 1
    assert initialize_calls == 1


def test_email_route_registration_fails_on_corrupt_store(tmp_path: Path):
    database = tmp_path / "corrupt.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_schema_migrations set version=?",
            (email_store_module.EMAIL_SCHEMA_VERSION + 1,),
        )
    app = FastAPI()

    with pytest.raises(EmailPersistenceCorruption, match="newer schema version"):
        register_email_routes(app, lambda: EmailStore(database))


def test_paginated_get_does_not_compete_with_scanner_write_transaction(
    tmp_path: Path,
):
    database = tmp_path / "concurrent-scanner.sqlite3"
    EmailStore(database)
    factory_calls = 0

    def factory() -> EmailStore:
        nonlocal factory_calls
        factory_calls += 1
        return _ZeroTimeoutEmailStore(database)

    app = FastAPI()
    register_email_routes(app, factory)
    writer = sqlite3.connect(database, timeout=0)
    try:
        writer.execute("pragma journal_mode = wal")
        writer.execute("begin immediate")
        writer.execute(
            """
            insert into email_scan_cursors (
                account_id, folder, uidvalidity, last_seen_uid,
                last_success_at, last_error
            ) values (?, ?, ?, ?, ?, ?)
            """,
            ("dingtalk-account", "INBOX", 42, 7, "", ""),
        )
        with TestClient(app) as client:
            response = client.get(
                "/api/console/email/classifications?page=1&page_size=1"
            )
        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 0
        writer.commit()
    finally:
        writer.close()

    assert factory_calls == 1
    assert EmailStore(database).get_scan_cursor("dingtalk-account", "INBOX") == {
        "account_id": "dingtalk-account",
        "folder": "INBOX",
        "uidvalidity": 42,
        "last_seen_uid": 7,
        "last_success_at": "",
        "last_error": "",
    }


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
