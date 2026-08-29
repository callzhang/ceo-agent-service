import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.audit_web as audit_web_module
from app.audit_web import create_audit_app
from app.store import AutoReplyStore
from app.web_api.attention import group_attention_rows
from app.web_api.common import (
    ApiItemEnvelope,
    ApiMeta,
    normalize_display_value,
)


class NonExecutingExecutor:
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


def _client(tmp_path: Path, *, spa_enabled: bool = False, asset: bytes = b""):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_bytes(asset)
    return TestClient(
        create_audit_app(
            tmp_path / "worker.sqlite3",
            workbench_asset_dir=assets,
            workbench_workspace=tmp_path,
            workbench_executor=NonExecutingExecutor(tmp_path),
            spa_enabled=spa_enabled,
        ),
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    )


def _project(store: AutoReplyStore, title: str) -> int:
    return store.create_work_project(
        title=title,
        category="dev",
        status="active",
        priority="P1",
        risk_level="low",
        facts_json=json.dumps(
            [
                {
                    "description": {"text": f"Fact for {title}"},
                    "source": {"title": "Roadmap.md"},
                }
            ],
            ensure_ascii=False,
        ),
        memory_context_json=json.dumps(
            {"summary": {"text": f"Memory for {title}"}},
            ensure_ascii=False,
        ),
    )


def test_common_envelopes_and_normalization_are_explicitly_json_serializable():
    item = ApiItemEnvelope(
        item={"label": normalize_display_value({"title": "Readable"})},
        meta=ApiMeta(snapshot_at="2026-08-29T00:00:00Z"),
    )

    encoded = item.model_dump(mode="json")

    assert encoded == {
        "item": {"label": "Readable"},
        "meta": {"snapshot_at": "2026-08-29T00:00:00Z"},
    }
    assert normalize_display_value(["one", {"text": "two"}]) == '["one", {"text": "two"}]'
    assert normalize_display_value(None) == ""
    assert "[object Object]" not in normalize_display_value({"nested": {"value": 1}})


def test_attention_grouping_keeps_records_and_uses_root_cause_context_key():
    rows = [
        {
            "category": "Reply task",
            "id": "1",
            "status": "failed",
            "context": "Sales",
            "summary": "first",
            "updated_at": "2026-08-29 10:00:00",
            "error": "provider_timeout",
        },
        {
            "category": "Reply task",
            "id": "2",
            "status": "failed",
            "context": "Sales",
            "summary": "second",
            "updated_at": "2026-08-29 10:01:00",
            "error": "provider_timeout",
        },
        {
            "category": "Reply task",
            "id": "3",
            "status": "failed",
            "context": "Product",
            "summary": "third",
            "updated_at": "2026-08-29 10:02:00",
            "error": "provider_timeout",
        },
    ]

    groups = group_attention_rows(rows)

    assert len(groups) == 2
    sales = next(group for group in groups if group.context == "Sales")
    assert sales.root_cause == "provider_timeout"
    assert sales.count == 2
    assert [record.id for record in sales.records] == ["1", "2"]


def test_console_tasks_endpoint_returns_paginated_json_envelope_and_serializable_values(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = _project(store, "First project")
    second_id = _project(store, "Second project")
    store.create_work_todo(
        project_id=first_id,
        title="Prepare release",
        status="open",
        priority="P1",
    )
    store.create_work_todo(
        project_id=second_id,
        title="Review release",
        status="done",
        priority="P2",
    )

    with _client(tmp_path) as client:
        response = client.get("/api/console/tasks?page=1&page_size=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["page"] == 1
    assert payload["meta"]["page_size"] == 1
    assert payload["meta"]["total"] == 2
    assert payload["meta"]["next_cursor"] == "2"
    assert payload["meta"]["has_more"] is True
    assert payload["items"][0]["id"] in {first_id, second_id}
    assert "[object Object]" not in json.dumps(payload, ensure_ascii=False)


def test_console_task_detail_contains_facts_todos_updates_and_memory_context(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = _project(store, "Detailed project")
    store.create_work_todo(
        project_id=project_id,
        title="Prepare release",
        description="Keep the description available to React.",
        status="open",
        priority="P1",
    )

    with _client(tmp_path) as client:
        response = client.get(f"/api/console/tasks/{project_id}")

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["project"]["id"] == project_id
    assert item["project"]["facts"][0]["description"] == "Fact for Detailed project"
    assert item["project"]["memory_context"]["summary"] == "Memory for Detailed project"
    assert item["todos"][0]["title"] == "Prepare release"
    assert isinstance(item["updates"], list)
    assert "[object Object]" not in json.dumps(item, ensure_ascii=False)


def test_console_status_is_json_serializable_and_has_snapshot(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        audit_web_module,
        "build_worker_status_payload",
        lambda store: {
            "service": {"state": "ok"},
            "connectors": {"dingtalk": {"status": "ready"}},
            "non_json_display": {"text": "safe"},
        },
    )

    with _client(tmp_path) as client:
        response = client.get("/api/console/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["item"]["service"]["state"] == "ok"
    assert payload["meta"]["snapshot_at"]
    json.dumps(payload, ensure_ascii=False)


def test_console_attention_returns_grouped_json_with_snapshot(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        audit_web_module,
        "_queue_attention_rows",
        lambda store: [
            {
                "category": "Service error",
                "id": "1",
                "status": "failed",
                "context": "runtime",
                "summary": "Database unavailable",
                "updated_at": "2026-08-29 10:00:00",
                "error": "db_locked",
            },
            {
                "category": "Service error",
                "id": "2",
                "status": "failed",
                "context": "runtime",
                "summary": "Retry required",
                "updated_at": "2026-08-29 10:01:00",
                "error": "db_locked",
            },
        ],
    )

    with _client(tmp_path) as client:
        response = client.get("/api/console/attention")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["snapshot_at"]
    assert payload["meta"]["total"] == 1
    assert payload["items"][0]["count"] == 2
    assert payload["items"][0]["root_cause"] == "db_locked"


def test_spa_mode_serves_same_react_index_for_business_deep_links_and_keeps_api_404_json(
    tmp_path: Path,
):
    expected = b"<!doctype html><title>React console</title>"
    routes = (
        "/",
        "/history",
        "/tasks",
        "/tasks/836",
        "/settings",
        "/user-feedback",
        "/tutorial",
        "/notifications",
        "/codex",
        "/codex/session-1",
        "/attempts/1",
        "/meeting-attempts/1",
        "/oa-approvals/path/to/approval",
        "/wechat/review",
        "/wechat/memory-review",
        "/wechat/deliveries",
    )

    with _client(tmp_path, spa_enabled=True, asset=expected) as client:
        responses = [client.get(route) for route in routes]
        unknown_api = client.get("/api/unknown")

    assert all(response.status_code == 200 for response in responses)
    assert all(response.content == expected for response in responses)
    assert unknown_api.status_code == 404
    assert unknown_api.headers["content-type"].startswith("application/json")
    assert "<!doctype html>" not in unknown_api.text
