from datetime import datetime, timezone
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.feishu.audit_web import csrf_form_input, register_feishu_review_routes
from app.feishu.delivery import delivery_idempotency_key
from app.feishu.models import FeishuInboundMessage, FeishuReplyScope
from app.store import AutoReplyStore


def _seed(store: AutoReplyStore):
    now = datetime.now(timezone.utc).isoformat()
    message = FeishuInboundMessage(
        event_id="evt-audit-1",
        app_id="cli_test",
        message_id="om_audit_1",
        chat_id="oc_audit_1",
        chat_type="group",
        chat_title="Audit group",
        sender_open_id="ou_audit_1",
        sender_name="Alice",
        message_type="text",
        mentioned_bot=True,
        body_text="请审核这个回复",
        event_create_time=now,
        received_at=now,
    )
    event = store.record_feishu_event(
        message,
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=True,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=message.chat_id,
        conversation_title=message.chat_title,
        trigger_message_id=message.message_id,
        trigger_sender=message.sender_name,
        trigger_text=message.body_text,
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="需要给出明确答复",
        draft_reply_text="这是待审核回复",
        send_status="pending",
        channel="feishu",
    )
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=attempt_id,
        app_id=message.app_id,
        chat_id=message.chat_id,
        reply_to_message_id=message.message_id,
        reply_in_thread=False,
        reply_text="这是待审核回复",
        idempotency_key=delivery_idempotency_key(
            app_id=message.app_id,
            reply_task_id=event.reply_task_id,
            trigger_message_id=message.message_id,
        ),
    )
    scope = store.upsert_feishu_reply_scope(
        FeishuReplyScope(
            app_id="cli_test",
            target_type="group",
            target_id="oc_audit_1",
            display_name="Audit group",
            trigger_mode="mention_bot",
        )
    )
    return scope, delivery


def _client(
    store,
    *,
    base_url: str = "http://127.0.0.1:8765",
    client_host: str = "127.0.0.1",
):
    app = FastAPI()
    register_feishu_review_routes(
        app,
        store_factory=lambda: store,
    )
    return TestClient(
        app,
        base_url=base_url,
        client=(client_host, 50000),
    )


def _csrf_data(**values):
    match = re.search(r"value='([^']+)'", csrf_form_input())
    assert match is not None
    return {"csrf_token": match.group(1), **values}


def _origin(base_url: str = "http://127.0.0.1:8765"):
    return {"Origin": base_url}


def test_review_page_shows_sanitized_scope_trigger_reason_and_draft(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _seed(store)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "secret-not-rendered")

    response = _client(store).get("/feishu/review")

    assert response.status_code == 200
    assert "飞书通道审核" in response.text
    assert "Audit group" in response.text
    assert "请审核这个回复" in response.text
    assert "需要给出明确答复" in response.text
    assert "这是待审核回复" in response.text
    assert "secret-not-rendered" not in response.text
    assert "App Secret：configured" in response.text


def test_scope_review_and_delivery_reject_are_local(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    client = _client(store)

    approved = client.post(
        "/feishu/scopes/group/oc_audit_1/approve",
        data=_csrf_data(approved_by="operator"),
        headers=_origin(),
        follow_redirects=False,
    )
    rejected = client.post(
        f"/feishu/deliveries/{delivery.id}/reject",
        data=_csrf_data(),
        headers=_origin(),
        follow_redirects=False,
    )

    assert approved.status_code == 303
    scope = store.get_feishu_reply_scope("cli_test", "group", "oc_audit_1")
    assert scope is not None and scope.binding_status == "verified"
    assert scope.approved_by == "operator"
    assert rejected.status_code == 303
    assert store.get_feishu_delivery(delivery.id).status == "rejected"


def test_approve_route_fails_before_callback_when_outbound_gates_closed(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: False)

    response = _client(store).post(
        f"/feishu/deliveries/{delivery.id}/approve",
        data=_csrf_data(approved_by="operator"),
        headers=_origin(),
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert store.get_feishu_delivery(delivery.id).status == "ready_to_send"
    assert store.get_feishu_delivery(delivery.id).approved_at == ""


def test_approve_route_records_durable_local_approval_without_sending(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    client = _client(store)

    response = client.post(
        f"/feishu/deliveries/{delivery.id}/approve?next=/",
        data=_csrf_data(approved_by="operator"),
        headers=_origin(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "ready_to_send"
    assert saved.approved_at
    assert saved.approved_by == "operator"


def test_mutations_require_csrf_and_same_local_origin(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    client = _client(store)

    missing_token = client.post(
        f"/feishu/deliveries/{delivery.id}/reject",
        headers=_origin(),
        follow_redirects=False,
    )
    cross_site = client.post(
        f"/feishu/deliveries/{delivery.id}/reject",
        data=_csrf_data(),
        headers={"Origin": "https://attacker.example"},
        follow_redirects=False,
    )
    forged_host = client.post(
        f"/feishu/deliveries/{delivery.id}/reject",
        data=_csrf_data(),
        headers={"Origin": "http://attacker.example", "Host": "attacker.example"},
        follow_redirects=False,
    )
    remote_client = _client(store, client_host="10.0.0.8").post(
        f"/feishu/deliveries/{delivery.id}/reject",
        data=_csrf_data(),
        headers=_origin(),
        follow_redirects=False,
    )

    assert missing_token.status_code == 403
    assert cross_site.status_code == 403
    assert forged_host.status_code == 403
    assert remote_client.status_code == 403
    assert store.get_feishu_delivery(delivery.id).status == "ready_to_send"


def test_audit_approval_rejects_configured_app_mismatch(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_other")

    response = _client(store).post(
        f"/feishu/deliveries/{delivery.id}/approve",
        data=_csrf_data(approved_by="operator"),
        headers=_origin(),
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert store.get_feishu_delivery(delivery.id).approved_at == ""

    rejected = _client(store).post(
        f"/feishu/deliveries/{delivery.id}/reject",
        data=_csrf_data(),
        headers=_origin(),
        follow_redirects=False,
    )
    assert rejected.status_code == 409
    assert store.get_feishu_delivery(delivery.id).status == "ready_to_send"


def test_localhost_ipv6_client_can_submit_same_origin_review(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _seed(store)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    client = _client(
        store,
        base_url="http://localhost:8765",
        client_host="::1",
    )

    response = client.post(
        "/feishu/scopes/group/oc_audit_1/approve",
        data=_csrf_data(approved_by="operator"),
        headers=_origin("http://localhost:8765"),
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_delivery_json_is_filtered_to_configured_app(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _seed(store)
    client = _client(store)

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_other")
    assert client.get("/feishu/deliveries").json() == {"items": []}

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    payload = client.get("/feishu/deliveries").json()
    assert len(payload["items"]) == 1


def test_main_audit_app_registers_feishu_review_and_navigation(
    tmp_path, monkeypatch
):
    from app.audit_web import create_audit_app

    monkeypatch.setattr(config, "feishu_app_id", lambda: "")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "")
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    client = TestClient(create_audit_app(store.path))

    history = client.get("/")
    review = client.get("/feishu/review")

    assert history.status_code == 200
    assert 'href="/feishu/review">Feishu</a>' in history.text
    assert review.status_code == 200
    assert "飞书通道审核" in review.text
