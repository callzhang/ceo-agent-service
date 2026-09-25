from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AutoReplyStore
from app.web_api.registration import register_console_routes


def _client(tmp_path: Path, **routes) -> TestClient:
    store = AutoReplyStore(tmp_path / "connectors.sqlite3")
    app = FastAPI()
    register_console_routes(
        app,
        store_factory=lambda: store,
        feedback_backlog_factory=lambda: {"processing": 0, "failed": 0, "retryable": 0},
        attention_rows_factory=lambda: [],
        **routes,
    )
    return TestClient(app)


def test_the_connectors_tab_does_not_build_the_whole_status_payload(tmp_path: Path) -> None:
    """On a cold start the full payload took a minute, and the tab spun on it."""

    def whole_payload():
        raise AssertionError("the connectors tab must not build the full status payload")

    connectors = {"dingtalk": {"channel": "dingtalk", "state": "ready"}}
    client = _client(
        tmp_path,
        status_payload_factory=whole_payload,
        connector_status_factory=lambda: connectors,
    )

    with client:
        response = client.get("/api/console/settings/connectors")

    assert response.status_code == 200
    assert response.json()["item"] == connectors


def test_the_connectors_tab_still_works_when_only_the_status_payload_is_wired(
    tmp_path: Path,
) -> None:
    client = _client(
        tmp_path,
        status_payload_factory=lambda: {"connectors": {"lark": {"state": "ready"}}},
    )

    with client:
        response = client.get("/api/console/settings/connectors")

    assert response.status_code == 200
    assert response.json()["item"] == {"lark": {"state": "ready"}}
