import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.audit_web as audit_web_module
import app.config as app_config_module
from app.audit_web import create_audit_app
from app.email_classifier_contracts import (
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
)
from app.email_store import EmailStore
from app.email_classifier_learning import EmailClassifierLearningService
from app.store import AutoReplyStore
from tests.test_audit_web import seed_attempt
from app.web_api.attention import group_attention_rows
from app.web_api.common import (
    ApiItemEnvelope,
    ApiMeta,
    json_safe,
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


def _client(
    tmp_path: Path,
    *,
    spa_enabled: bool = False,
    asset: bytes = b"",
    email_learning_factory=None,
):
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
            email_learning_factory=email_learning_factory,
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
    assert "<structured error>" not in json.dumps(json_safe({"detail": "<structured error>"}), ensure_ascii=False)


def test_feedback_direct_resolve_requires_batch(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1", comment="Needs work")

    with _client(tmp_path) as client:
        response = client.post("/api/console/feedback/feedback-1/resolve")

    assert response.status_code == 409
    assert response.json()["code"] == "feedback_batch_required"


def test_feedback_batch_routes_claim_atomically_and_expose_processing_state(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(key=key, feedback_token=f"token-{key}", comment=f"Comment {key}")

    with _client(tmp_path) as client:
        listed = client.get("/api/console/feedback?status=pending")
        assert listed.status_code == 200
        assert {item["feedback_key"] for item in listed.json()["items"]} == {"feedback-1", "feedback-2"}
        assert all("summary" in item and "references" in item for item in listed.json()["items"])

        claimed = client.post("/api/console/feedback/batches", json={"feedback_keys": ["feedback-1", "feedback-2"]})
        assert claimed.status_code == 200
        batch = claimed.json()["item"]
        assert batch["status"] == "processing"
        assert batch["feedback_keys"] == ["feedback-1", "feedback-2"]
        assert "start_message" in batch

        detail = client.get(f"/api/console/feedback/batches/{batch['batch_id']}")
        assert detail.status_code == 200
        assert {item["feedback_key"] for item in detail.json()["item"]["items"]} == {"feedback-1", "feedback-2"}

        conflict = client.post("/api/console/feedback/batches", json={"feedback_keys": ["feedback-1"]})
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "feedback_already_processing"

        missing = client.post("/api/console/feedback/batches", json={"feedback_keys": ["unknown"]})
        assert missing.status_code == 404
        assert missing.json()["code"] == "not_found"


def test_feedback_item_invalid_status_does_not_mutate_association(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")

    with _client(tmp_path) as client:
        claimed = client.post("/api/console/feedback/batches", json={"feedback_keys": ["feedback-1"]})
        batch_id = claimed.json()["item"]["batch_id"]
        response = client.patch(
            "/api/console/feedback/items/feedback-1",
            json={"status": "bogus", "workbench_task_id": "task-1", "workbench_turn_id": "turn-1"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "feedback_evidence_invalid"
    item = store.get_feedback_processing_item("feedback-1")
    assert item is not None
    assert item.batch_id == batch_id
    assert item.workbench_task_id == ""
    assert item.workbench_turn_id == ""


def test_feedback_history_with_corrected_reply_is_resolved_and_not_claimable(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply("cid-1", "msg-1", "先按A方案走", feedback_token="token-1")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    store.record_reply_feedback(attempt_id, feedback="已修正", corrected_reply_text="修正版")

    with _client(tmp_path) as client:
        listed = client.get("/api/console/feedback?status=resolved")
        conflict = client.post("/api/console/feedback/batches", json={"feedback_keys": ["feedback-1"]})

    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "resolved"
    assert 'status-resolved">resolved</span>' in audit_web_module.render_user_feedback_list(store)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "feedback_already_processing"


def test_feedback_claim_returns_associated_item_receipts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")

    with _client(tmp_path) as client:
        response = client.post(
            "/api/console/feedback/batches",
            json={"feedback_keys": ["feedback-1"], "workbench_task_id": "task-1", "workbench_turn_id": "turn-1"},
        )

    assert response.status_code == 200
    receipt = response.json()["item"]["items"][0]
    assert receipt["workbench_task_id"] == "task-1"
    assert receipt["workbench_turn_id"] == "turn-1"


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


def test_console_history_includes_chart_snapshot(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="history-conversation",
        conversation_title="History test",
        trigger_message_id="history-message",
        trigger_sender="Derek",
        trigger_text="Show recent events",
        action="chat_message",
        sensitivity_kind="",
        codex_reason="test",
        draft_reply_text="Recent event",
        send_status="sent",
    )

    with _client(tmp_path) as client:
        response = client.get("/api/console/history?page=1&page_size=20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chart"]["total"] >= 1
    assert len(payload["chart"]["labels"]) == 24


def test_console_feedback_pending_badge_is_global_when_filtered_to_resolved(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_feedback_event(
        key="pending-feedback",
        feedback_token="pending-token",
        rating_label="一般",
        comment="待处理",
        received_at="2026-08-29T08:00:00.000Z",
    )
    store.upsert_feedback_event(
        key="resolved-feedback",
        feedback_token="resolved-token",
        rating_label="有用",
        comment="已处理",
        received_at="2026-08-29T07:00:00.000Z",
    )
    store.resolve_feedback_event("resolved-feedback")

    with _client(tmp_path) as client:
        response = client.get("/api/console/feedback?status=resolved")

    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 1
    assert response.json()["pending_count"] == 1


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


def test_console_audit_rules_template_preview_is_rendered_but_template_is_preserved(
    monkeypatch, tmp_path: Path
):
    template_path = tmp_path / "audit_rules.md"
    template_path.write_text("Escalate to {{principal}} only when needed.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(template_path))
    monkeypatch.setenv("USER_ALIAS", "Alex")

    with _client(tmp_path) as client:
        response = client.get("/api/console/settings/audit-rules")

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["fields"]["template"] == "Escalate to {{principal}} only when needed."
    assert item["preview"]["template"] == "Escalate to Alex only when needed."
    assert "{{principal}}" not in item["preview"]["template"]


def test_console_agent_runtime_returns_saved_credentials_for_prefill(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        app_config_module,
        "read_env_file",
        lambda: {
            "CEO_CODEX_API_KEY": "codex-token",
            "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY": "provider-token",
            "CEO_FRIDAY_RUNTIME_TICKET": "runtime-ticket",
            "CEO_FRIDAY_SESSION_TOKEN": "session-token",
        },
    )

    with _client(tmp_path) as client:
        response = client.get("/api/console/settings/agent-runtime")

    assert response.status_code == 200
    fields = response.json()["item"]["fields"]
    assert fields["CEO_CODEX_API_KEY"] == "codex-token"
    assert fields["CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY"] == "provider-token"
    assert fields["CEO_FRIDAY_RUNTIME_TICKET"] == "runtime-ticket"
    assert fields["CEO_FRIDAY_SESSION_TOKEN"] == "session-token"


def test_console_agent_runtime_writes_prefilled_credentials_and_compatible_model(monkeypatch, tmp_path: Path):
    runtime_env_keys = (
        "CEO_AGENT_RUNTIME_ROUTES",
        "CEO_CODEX_MODEL",
        "CEO_CODEX_MODEL_REASONING_EFFORT",
        "CEO_CODEX_API_BASE_URL",
        "CEO_CODEX_API_MODEL",
        "CEO_CODEX_API_KEY",
        "CEO_FRIDAY_RUNTIME_BASE_URL",
        "CEO_FRIDAY_RUNTIME_PROJECT_ID",
        "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL",
        "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL",
        "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY",
        "CEO_FRIDAY_RUNTIME_TICKET",
        "CEO_FRIDAY_SESSION_TOKEN",
        "CEO_FRIDAY_RUNTIME_AUTH_DISABLED",
    )
    original_runtime_env = {
        key: os.environ.get(key)
        for key in runtime_env_keys
    }

    def restore_runtime_env() -> None:
        for key, value in original_runtime_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    env_path = tmp_path / ".env"
    env_path.write_text(
        "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api,friday_runtime\n"
        "CEO_CODEX_MODEL=gpt-5.5\n"
        "CEO_CODEX_MODEL_REASONING_EFFORT=medium\n"
        "CEO_CODEX_API_BASE_URL=https://api.openai.com/v1\n"
        "CEO_CODEX_API_MODEL=gpt-5.5\n"
        "CEO_CODEX_API_KEY=old-codex-token\n"
        "CEO_FRIDAY_RUNTIME_BASE_URL=http://127.0.0.1:8080\n"
        "CEO_FRIDAY_RUNTIME_PROJECT_ID=project-1\n"
        "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL=https://provider.example/v1\n"
        "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL=qwen-plus\n"
        "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY=old-provider-token\n"
        "CEO_FRIDAY_RUNTIME_TICKET=old-ticket\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))

    try:
        with _client(tmp_path) as client:
            response = client.post(
                "/api/console/settings/agent-runtime",
                json={"fields": {
                    "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api,friday_runtime",
                    "CEO_CODEX_MODEL": "gpt-5.5",
                    "CEO_CODEX_MODEL_REASONING_EFFORT": "medium",
                    "CEO_CODEX_API_BASE_URL": "https://api.openai.com/v1",
                    "CEO_CODEX_API_MODEL": "MiniMax-M2.5",
                    "CEO_CODEX_API_KEY": "new-codex-token",
                    "CEO_FRIDAY_RUNTIME_BASE_URL": "http://127.0.0.1:8080",
                    "CEO_FRIDAY_RUNTIME_PROJECT_ID": "project-1",
                    "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL": "https://provider.example/v1",
                    "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL": "qwen-plus",
                    "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY": "new-provider-token",
                    "CEO_FRIDAY_RUNTIME_TICKET": "new-ticket",
                    "CEO_FRIDAY_SESSION_TOKEN": "",
                }},
            )

        assert response.status_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "CEO_CODEX_API_MODEL=MiniMax-M2.5" in env_text
        assert "CEO_CODEX_API_KEY=new-codex-token" in env_text
        assert "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY=new-provider-token" in env_text
        assert "CEO_FRIDAY_RUNTIME_TICKET=new-ticket" in env_text
    finally:
        restore_runtime_env()


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


def test_console_attention_preserves_record_detail_url(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        audit_web_module,
        "_queue_attention_rows",
        lambda store: [
            {
                "category": "Service error",
                "id": "12830",
                "status": "failed",
                "context": "producer_loop_error",
                "summary": "database is locked",
                "updated_at": "2026-08-29 18:27:11",
                "error": "database is locked",
                "detail_url": "/history/errors/12830",
            },
        ],
    )

    with _client(tmp_path) as client:
        response = client.get("/api/console/attention")

    assert response.status_code == 200
    assert response.json()["items"][0]["records"][0]["detail_url"] == "/history/errors/12830"


def test_queue_attention_rows_routes_service_errors_to_history_detail(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("", "", "producer_loop_error", "database is locked")
    error_id = store.list_errors(limit=1)[0].id

    rows = audit_web_module._queue_attention_rows(store)

    service_error = next(row for row in rows if row["category"] == "Service error")
    assert service_error["id"] == str(error_id)
    assert service_error["detail_url"] == f"/history/errors/{error_id}"


def test_console_error_detail_returns_error_record(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("", "", "producer_loop_error", "database is locked")
    error_id = store.list_errors(limit=1)[0].id

    with _client(tmp_path) as client:
        response = client.get(f"/api/console/history/errors/{error_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["item"]["id"] == error_id
    assert payload["item"]["kind"] == "producer_loop_error"
    assert payload["item"]["error"] == "database is locked"


def test_console_sent_todos_endpoint_returns_structured_rows_before_project_route(tmp_path: Path, monkeypatch):
    record = SimpleNamespace(
        kind="dingtalk_todo",
        source_id=7,
        sent_at="2026-08-29 18:00:00",
        status="done",
        owner_name="Alex",
        owner_user_id="user-7",
        project_id=836,
        project_title="Launch project",
        todo_id=12,
        todo_title="Review launch checklist",
        todo_description="Review the launch checklist",
        original_text="Review launch checklist",
        deadline_at="2026-08-30 10:00:00",
        priority="high",
        target_kind="dingtalk",
        target_conversation_id="",
        external_id="task-7",
        detail="",
    )
    monkeypatch.setattr(
        AutoReplyStore,
        "list_sent_todo_records",
        lambda _store, *, limit=5000: [record],
    )

    with _client(tmp_path) as client:
        response = client.get("/api/console/tasks/sent-todos")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["total"] == 1
    assert payload["items"] == [
        {
            "id": "dingtalk_todo:7",
            "kind": "dingtalk_todo",
            "kind_label": "DingTalk Todo",
            "sent_at": "2026-08-29 18:00:00",
            "status": "done",
            "owner": "Alex",
            "project_title": "Launch project",
            "todo_title": "Review launch checklist",
            "description": "Review the launch checklist",
            "original_text": "Review launch checklist",
            "deadline": "2026-08-30 10:00:00",
            "priority": "high",
            "target": "task-7",
            "external_id": "task-7",
            "detail_url": "/tasks/836#todo-12",
        }
    ]


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


def test_console_wechat_targets_and_reply_scope_use_json_contract(
    tmp_path: Path, monkeypatch,
):
    class FakeSetup:
        def list_targets(self, *, query, kind, limit, offset):
            del limit, offset
            if kind == "direct":
                items = [{
                    "target_type": "direct", "target_id": "alice",
                    "conversation_id": "alice", "display_name": "Alice",
                    "trigger_mode": "every_inbound_text",
                }]
            else:
                items = [{
                    "target_type": "group", "target_id": "team",
                    "conversation_id": "team", "display_name": "Team",
                    "trigger_mode": "mention_current_account",
                }]
            return [item for item in items if query.casefold() in item["display_name"].casefold()]

    ready_state = {"account_id": "wx-account", "capability_status": "ready"}
    import app.wechat.service as wechat_service

    monkeypatch.setattr(wechat_service, "ready_account_state", lambda store: ready_state)
    monkeypatch.setattr(wechat_service, "build_setup_service", lambda store: FakeSetup())
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with _client(tmp_path) as client:
        targets = client.get("/api/console/wechat/targets?query=team")
        saved = client.post("/api/console/wechat/reply-scope", json={
            "account_id": "wx-account",
            "targets": [{
                "target_type": "group", "target_id": "team",
                "display_name": "Team", "trigger_mode": "mention_current_account",
                "conversation_id": "team",
            }],
        })

    assert targets.status_code == 200
    assert targets.json()["account_id"] == "wx-account"
    assert {item["target_id"] for item in targets.json()["items"]} == {"team"}
    assert saved.status_code == 200
    assert saved.json()["item"] == {"account_id": "wx-account", "saved": 1}
    assert [scope.target_id for scope in store.list_wechat_reply_scopes("wx-account", enabled_only=True)] == ["team"]


def test_console_email_tabs_and_feedback_are_local_classifier_operations(
    tmp_path: Path,
):
    email_store = EmailStore(tmp_path / "worker.sqlite3")
    classification = EmailClassification.model_validate(
        {
            "classification_id": 1,
            "stable_message_identity": (
                "dingtalk-account:message-id:<email-1@example.test>"
            ),
            "provider_locator": {
                "account_id": "dingtalk-account",
                "folder": "INBOX",
                "uidvalidity": 1,
                "uid": 1,
                "rfc_message_id": "<email-1@example.test>",
            },
            "category": EmailCategory.WORK,
            "confidence": 0.61,
            "margin": 0.04,
            "probabilities": {"work": 0.61, "important": 0.57},
            "model_id": "email/logistic/model-1",
            "config_version": "email-v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model",
            "action_plan": None,
        }
    )
    row = email_store.upsert_classification(
        classification, sender="sender@example.com", subject="Please decide"
    )

    with _client(tmp_path) as client:
        default_tab = client.get("/api/console/email/classifications")
        pending_tab = client.get(
            "/api/console/email/classifications?status=pending_feedback"
        )
        feedback = client.post(
            f"/api/console/email/classifications/{row['id']}/feedback",
            json={"category": "important"},
        )
        processed_tab = client.get("/api/console/email/classifications")

    assert default_tab.status_code == 200
    assert default_tab.json()["items"] == []
    assert pending_tab.status_code == 200
    assert pending_tab.json()["items"][0]["subject"] == "Please decide"
    assert feedback.status_code == 200
    assert feedback.json()["item"]["classification_source"] == "user"
    assert processed_tab.json()["items"][0]["category"] == "important"


def test_console_email_config_is_separate_from_provider_actions(tmp_path: Path):
    with _client(tmp_path) as client:
        saved = client.put(
            "/api/console/email/config/subscription",
            json={
                "description": "营销订阅",
                "threshold": 0.98,
                "actions": ["archive"],
                "enabled": True,
                "config_version": "email-v2",
            },
        )
        listed = client.get("/api/console/email/config")

    assert saved.status_code == 200
    assert saved.json()["item"]["actions"] == ["archive"]
    assert listed.status_code == 200
    assert listed.json()["items"][0]["category"] == "subscription"


def test_console_email_accounts_are_registered_and_keep_classifier_config_global(
    tmp_path: Path,
):
    account = {
        "account_id": "console_mail",
        "display_name": "Console Mail",
        "email_address": "console-mail@example.test",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "imap_tls": True,
        "imap_username": "console-mail@example.test",
        "imap_secret_reference": "CEO_EMAIL_CONSOLE_MAIL_IMAP_SECRET",
        "smtp_host": "smtp.example.test",
        "smtp_port": 465,
        "smtp_tls": True,
        "smtp_username": "console-mail@example.test",
        "smtp_secret_reference": "CEO_EMAIL_CONSOLE_MAIL_SMTP_SECRET",
        "enabled": True,
        "scan_folders": ["INBOX"],
        "scan_interval_seconds": 60,
    }

    with _client(tmp_path) as client:
        config = client.put(
            "/api/console/email/config/work",
            json={
                "description": "Work",
                "threshold": 0.91,
                "actions": ["archive"],
                "enabled": True,
                "config_version": "email-config-v1",
            },
        )
        created = client.post("/api/console/email/accounts", json=account)
        listed = client.get("/api/console/email/accounts")

    assert config.status_code == 200
    assert created.status_code == 201
    assert created.json()["restart_required"] is True
    assert listed.status_code == 200
    assert listed.json()["items"][0]["account_id"] == "console_mail"
    assert "threshold" not in listed.json()["items"][0]
    assert "model_id" not in listed.json()["items"][0]


def test_console_email_feedback_can_trigger_local_learning_service(tmp_path: Path):
    email_store = EmailStore(tmp_path / "worker.sqlite3")
    classification = EmailClassification.model_validate(
        {
            "classification_id": 2,
            "stable_message_identity": (
                "dingtalk-account:message-id:<email-learning-1@example.test>"
            ),
            "provider_locator": {
                "account_id": "dingtalk-account",
                "folder": "INBOX",
                "uidvalidity": 1,
                "uid": 2,
                "rfc_message_id": "<email-learning-1@example.test>",
            },
            "category": EmailCategory.WORK,
            "confidence": 0.61,
            "margin": 0.04,
            "probabilities": {"work": 0.61, "important": 0.57},
            "model_id": "email/logistic/model-1",
            "config_version": "email-v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model",
            "action_plan": None,
        }
    )
    row = email_store.upsert_classification(
        classification,
        sender="sender@example.com",
        subject="Please decide",
        model_text="__from_domain__example.com __subject__项目 工作",
    )
    learning_service = EmailClassifierLearningService(
        email_store,
        active_path=tmp_path / "models" / "model.active.pkl",
        previous_path=tmp_path / "models" / "model.previous.pkl",
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
    )

    with _client(
        tmp_path, email_learning_factory=lambda: learning_service
    ) as client:
        feedback = client.post(
            f"/api/console/email/classifications/{row['id']}/feedback",
            json={"category": "important"},
        )

    assert feedback.status_code == 200
    assert feedback.json()["item"]["classification_source"] == "user"
    assert feedback.json()["learning"] == {
        "retrain_due": False,
        "retrain_reason": None,
        "promoted": False,
        "error": None,
    }
    assert (tmp_path / "models" / "retrain-state.json").exists()
