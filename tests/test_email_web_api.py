from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import app.email_store as email_store_module
from app.audit_web import create_audit_app
from app.email_store import EmailStore
from app.store import AutoReplyStore
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


class _NonExecutingExecutor:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def recover(self):
        return 0

    def run_once(self):
        return []

    def stop(self, turn_id):
        del turn_id
        return None

    def confirm(self, confirmation_id):
        raise AssertionError(f"unexpected confirmation: {confirmation_id}")

    def cancel(self, confirmation_id):
        raise AssertionError(f"unexpected cancellation: {confirmation_id}")

    def close(self):
        return True


def _assert_email_endpoints_unavailable(client: TestClient) -> None:
    responses = (
        client.get("/api/console/email/classifications?status=invalid"),
        client.post("/api/console/email/classifications/999/feedback"),
        client.get("/api/console/email/config"),
        client.put("/api/console/email/config/invalid"),
    )
    expected = {
        "ok": False,
        "code": "email_store_unavailable",
        "message": "Email storage is unavailable",
        "details": {},
    }
    for response in responses:
        assert response.status_code == 503
        assert response.json() == expected


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


def test_future_email_schema_isolated_from_non_email_routes(tmp_path: Path):
    database = tmp_path / "corrupt.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_schema_migrations set version=?",
            (email_store_module.EMAIL_SCHEMA_VERSION + 1,),
        )
    factory_calls = 0

    def factory() -> EmailStore:
        nonlocal factory_calls
        factory_calls += 1
        return EmailStore(database)

    app = FastAPI()

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "status": "ok"}

    @app.get("/api/non-email")
    def non_email():
        return {"ok": True}

    register_email_routes(app, factory)

    availability = app.state.email_store_availability
    assert availability.store is None
    assert availability.diagnostic == "email_persistence_corruption"
    assert str(database) not in availability.diagnostic
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/non-email").status_code == 200
        _assert_email_endpoints_unavailable(client)
        _assert_email_endpoints_unavailable(client)
    assert factory_calls == 1


@pytest.mark.parametrize(
    "error_type",
    (
        sqlite3.ProgrammingError,
        sqlite3.NotSupportedError,
        TypeError,
        ValueError,
        SystemExit,
        KeyboardInterrupt,
    ),
)
def test_email_route_registration_does_not_hide_programming_or_control_flow_errors(
    error_type: type[BaseException],
):
    app = FastAPI()

    def factory():
        raise error_type("sentinel")

    with pytest.raises(error_type, match="sentinel"):
        register_email_routes(app, factory)


def test_locked_missing_schema_stays_unavailable_without_request_retry(
    tmp_path: Path,
):
    database = tmp_path / "locked-migration.sqlite3"
    factory_calls = 0
    writer = sqlite3.connect(database, timeout=0)
    try:
        writer.execute("begin immediate")

        def factory() -> EmailStore:
            nonlocal factory_calls
            factory_calls += 1
            return _ZeroTimeoutEmailStore(database)

        app = FastAPI()
        register_email_routes(app, factory)
        availability = app.state.email_store_availability
        assert availability.store is None
        assert availability.diagnostic == "sqlite_operational_error"
        writer.rollback()
        with TestClient(app) as client:
            _assert_email_endpoints_unavailable(client)
            _assert_email_endpoints_unavailable(client)
    finally:
        writer.close()

    assert factory_calls == 1
    with sqlite3.connect(database) as db:
        migration_table = db.execute(
            """
            select 1 from sqlite_master
            where type='table' and name='email_schema_migrations'
            """
        ).fetchone()
    assert migration_table is None


def test_audit_app_starts_when_email_schema_is_unavailable(tmp_path: Path):
    database = tmp_path / "audit-app.sqlite3"
    AutoReplyStore(database)
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_schema_migrations set version=?",
            (email_store_module.EMAIL_SCHEMA_VERSION + 1,),
        )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("", encoding="utf-8")

    app = create_audit_app(
        database,
        workbench_asset_dir=assets,
        workbench_workspace=tmp_path,
        workbench_executor=_NonExecutingExecutor(tmp_path),
    )

    with TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    ) as client:
        assert client.get("/healthz").json() == {"ok": True, "status": "ok"}
        tasks = client.get("/api/console/tasks?page=1&page_size=1")
        assert tasks.status_code == 200
        _assert_email_endpoints_unavailable(client)


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
        app = FastAPI()

        @app.get("/healthz")
        def healthz():
            return {"ok": True}

        @app.get("/api/non-email")
        def non_email():
            return {"ok": True}

        register_email_routes(app, factory)
        with TestClient(app) as client:
            health = client.get("/healthz")
            non_email_response = client.get("/api/non-email")
            response = client.get(
                "/api/console/email/classifications?page=1&page_size=1"
            )
        assert health.status_code == 200
        assert non_email_response.status_code == 200
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
