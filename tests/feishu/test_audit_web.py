from datetime import datetime, timezone
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.feishu.actions import build_message_action
from app.feishu.audit_web import csrf_form_input, register_feishu_review_routes
from app.feishu.delivery import delivery_idempotency_key
from app.feishu.models import FeishuInboundMessage, FeishuReplyScope
from app.store import AutoReplyStore


def _seed(store: AutoReplyStore, *, reply_text: str = "这是待审核回复"):
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
    task = next(
        row
        for row in store.list_reply_tasks(channel="feishu")
        if row.id == event.reply_task_id
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=message.chat_title,
        trigger_message_id=message.message_id,
        trigger_sender=message.sender_name,
        trigger_text=message.body_text,
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="需要给出明确答复",
        draft_reply_text=reply_text,
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
        reply_text=reply_text,
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


def _mark_sent(store: AutoReplyStore, delivery, *message_ids: str):
    claimed = store.claim_feishu_delivery(delivery.id, app_id=delivery.app_id)
    assert claimed is not None
    return store.transition_feishu_delivery(
        delivery.id,
        from_statuses=("sending",),
        to_status="sent",
        app_id=delivery.app_id,
        expected_lease_token=claimed.lease_token,
        feishu_message_id=message_ids[0],
        message_ids=message_ids,
    )


def _seed_action(store, delivery, *, kind="add_reaction", secret_text=""):
    target_message_id = delivery.reply_to_message_id
    target_open_id = ""
    payload = {"emoji_type": "OK"}
    allowlist = ()
    if kind == "recall_message":
        [receipt] = store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
        target_message_id = receipt.message_id
        payload = {}
    elif kind == "handoff_notify":
        target_message_id = ""
        target_open_id = "ou_private_owner"
        payload = {"text": secret_text or "private handoff body"}
        allowlist = (target_open_id,)
    action = build_message_action(
        reply_task_id=delivery.reply_task_id,
        attempt_id=delivery.attempt_id,
        app_id=delivery.app_id,
        chat_id=delivery.chat_id,
        action_key=f"audit:{kind}",
        kind=kind,
        target_message_id=target_message_id,
        target_open_id=target_open_id,
        payload=payload,
    )
    return store.create_feishu_message_action(
        action,
        handoff_target_allowlist=allowlist,
    )


def _mark_action_unknown(store, action, *, request_log_id="log_unknown"):
    if action.risk == "R4":
        action = store.approve_feishu_message_action(
            action.id,
            app_id=action.app_id,
            approved_by="preflight-reviewer",
            expected_approval_hash=action.approval_hash,
        )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id=action.app_id,
        kinds=(action.kind,),
        send_mode="auto",
    )
    assert claimed is not None
    return store.transition_feishu_message_action(
        action.id,
        from_statuses=("sending",),
        to_status="result_unknown",
        app_id=action.app_id,
        expected_lease_token=claimed.lease_token,
        request_log_id=request_log_id,
        error_code="unknown",
        error="provider_result_unknown",
    )


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


def test_review_page_shows_hash_bound_escaped_delivery_preview(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "secret-not-rendered")

    response = _client(store).get("/feishu/review")

    assert response.status_code == 200
    assert "飞书通道审核" in response.text
    assert "Audit group" in response.text
    assert "请审核这个回复" in response.text
    assert "需要给出明确答复" in response.text
    assert "这是待审核回复" in response.text
    assert delivery.approval_hash in response.text
    assert "secret-not-rendered" not in response.text
    assert "App Secret：configured" in response.text
    assert "local-audit-review" not in response.text
    assert "name='approved_by' required" in response.text
    assert "name='rejected_by' required" in response.text
    assert "http-equiv='refresh'" not in response.text
    assert "document.activeElement" in response.text
    assert "formDirty" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    nonce_match = re.search(r"\bscript-src 'self' 'nonce-([^']+)'", policy)
    assert nonce_match is not None
    script_tags = re.findall(r"<script\b[^>]*>", response.text)
    assert script_tags
    assert all(f'nonce="{nonce_match.group(1)}"' in tag for tag in script_tags)


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
        data=_csrf_data(rejected_by="operator"),
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
        data=_csrf_data(
            approved_by="operator", approval_hash=delivery.approval_hash
        ),
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
        data=_csrf_data(
            approved_by="operator", approval_hash=delivery.approval_hash
        ),
        headers=_origin(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "ready_to_send"
    assert saved.approved_at
    assert saved.approved_by == "operator"


def test_delivery_approve_requires_exact_preview_hash(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    client = _client(store)
    path = f"/feishu/deliveries/{delivery.id}/approve"

    missing = client.post(
        path,
        data=_csrf_data(approved_by="operator"),
        headers=_origin(),
        follow_redirects=False,
    )
    changed = client.post(
        path,
        data=_csrf_data(approved_by="operator", approval_hash="0" * 64),
        headers=_origin(),
        follow_redirects=False,
    )

    assert missing.status_code == 422
    assert changed.status_code == 409
    assert store.get_feishu_delivery(delivery.id).approved_at == ""


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


def test_feishu_reads_require_loopback_host_and_client(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _seed(store)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "configured-secret")
    paths = (
        "/feishu/review",
        "/feishu/deliveries",
        "/feishu/receipts",
        "/feishu/actions",
    )

    for path in paths:
        response = _client(store).get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert (
            _client(store).get(path, headers={"Host": "attacker.example"}).status_code
            == 403
        )
        assert _client(store, client_host="10.0.0.8").get(path).status_code == 403


def test_audit_approval_rejects_configured_app_mismatch(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_other")

    response = _client(store).post(
        f"/feishu/deliveries/{delivery.id}/approve",
        data=_csrf_data(
            approved_by="operator", approval_hash=delivery.approval_hash
        ),
        headers=_origin(),
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert store.get_feishu_delivery(delivery.id).approved_at == ""

    rejected = _client(store).post(
        f"/feishu/deliveries/{delivery.id}/reject",
        data=_csrf_data(rejected_by="operator"),
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
    assert client.get("/feishu/review").status_code == 200


def test_approved_delivery_remains_rejectable_with_real_operator(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    store.approve_feishu_delivery(
        delivery.id,
        app_id=delivery.app_id,
        approved_by="first-reviewer",
        expected_approval_hash=delivery.approval_hash,
    )
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "configured-secret")
    client = _client(store)

    page = client.get("/feishu/review")
    reject_path = f"/feishu/deliveries/{delivery.id}/reject"
    assert reject_path in page.text
    assert f"/feishu/deliveries/{delivery.id}/approve" not in page.text

    missing_operator = client.post(
        reject_path,
        data=_csrf_data(),
        headers=_origin(),
        follow_redirects=False,
    )
    assert missing_operator.status_code == 422

    rejected = client.post(
        reject_path,
        data=_csrf_data(rejected_by="cancel-reviewer"),
        headers=_origin(),
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "rejected"
    events = store.list_feishu_audit_events(
        entity_type="delivery", entity_id=delivery.id
    )
    assert any(
        event.event_type == "rejected" and event.actor == "cancel-reviewer"
        for event in events
    )


def test_delivery_unknown_web_reconcile_and_requeue_are_offline_guarded_steps(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    assert store.claim_feishu_delivery(delivery.id, app_id=delivery.app_id) is not None
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="provider result is uncertain",
    )
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: False)

    def forbidden_client(*_args, **_kwargs):
        raise AssertionError("delivery reconciliation must not build a Feishu client")

    monkeypatch.setattr("app.feishu.client.build_channel", forbidden_client)
    client = _client(store)
    reconcile_path = f"/feishu/deliveries/{delivery.id}/reconcile"
    page = client.get("/feishu/review")
    assert reconcile_path in page.text
    assert "name='message_ids'" in page.text
    assert "连续 Message ID 前缀" in page.text
    assert "不确定的下一片已确认未发送" in page.text

    missing_csrf = client.post(
        reconcile_path,
        data={
            "outcome": "not_sent",
            "verified_by": "operator",
            "evidence_kind": "message_lookup",
        },
        headers=_origin(),
        follow_redirects=False,
    )
    assert missing_csrf.status_code == 403

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_other")
    wrong_app = client.post(
        reconcile_path,
        data=_csrf_data(
            outcome="not_sent",
            verified_by="operator",
            evidence_kind="message_lookup",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert wrong_app.status_code == 409
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")

    invalid_sent = client.post(
        reconcile_path,
        data=_csrf_data(
            outcome="sent",
            verified_by="operator",
            evidence_kind="message_lookup",
            message_ids="",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert invalid_sent.status_code == 409
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"

    reconciled = client.post(
        reconcile_path,
        data=_csrf_data(
            outcome="not_sent",
            verified_by="operator",
            evidence_kind="admin_audit",
            message_ids="",
            request_log_id="log-verified-not-sent",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert reconciled.status_code == 303
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "failed"
    assert saved.error_code == "verified_not_sent"
    assert saved.request_log_id == "log-verified-not-sent"

    requeue_path = f"/feishu/deliveries/{delivery.id}/requeue"
    assert requeue_path in client.get("/feishu/review").text
    requeued = client.post(
        requeue_path,
        data=_csrf_data(
            verified_by="operator",
            evidence_kind="feishu_ui",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert requeued.status_code == 303
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "retry"
    assert saved.approved_at == ""


def test_delivery_unknown_web_accepts_complete_ordered_chunk_ids(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store, reply_text="x" * 5000)
    assert delivery.expected_chunks == 2
    claimed = store.claim_feishu_delivery(delivery.id, app_id=delivery.app_id)
    assert claimed is not None
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=delivery.expected_chunks,
        message_id="om_first",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="provider result is uncertain",
    )
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    client = _client(store)

    response = client.post(
        f"/feishu/deliveries/{delivery.id}/reconcile",
        data=_csrf_data(
            outcome="sent",
            verified_by="operator",
            evidence_kind="message_lookup",
            message_ids="om_first\nom_second",
        ),
        headers=_origin(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "sent"
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    ] == ["om_first", "om_second"]


def test_delivery_json_is_filtered_to_configured_app(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _seed(store)
    client = _client(store)

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_other")
    assert client.get("/feishu/deliveries").json() == {"items": []}

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    payload = client.get("/feishu/deliveries").json()
    assert len(payload["items"]) == 1
    [item] = payload["items"]
    assert "reply_text" not in item
    assert "chat_id" not in item
    assert item["summary"].startswith("[redacted:")
    assert "approval_hash" not in item
    assert "approval_preview" not in item

    [review_item] = client.get(
        "/feishu/deliveries?include_preview=true"
    ).json()["items"]
    delivery = store.list_feishu_deliveries()[0]
    assert review_item["approval_hash"] == delivery.approval_hash
    assert review_item["approval_preview"]["text"] == delivery.reply_text


def test_main_audit_app_registers_feishu_review_and_navigation(
    tmp_path, monkeypatch
):
    from app.audit_web import create_audit_app

    monkeypatch.setattr(config, "feishu_app_id", lambda: "")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "")
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    client = TestClient(
        create_audit_app(store.path),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    )

    history = client.get("/")
    review = client.get("/feishu/review")
    second_review = client.get("/feishu/review")

    assert history.status_code == 200
    assert 'href="/feishu/review">Feishu</a>' in history.text
    assert review.status_code == 200
    assert "飞书通道审核" in review.text
    first_nonce = re.search(
        r"\bscript-src 'self' 'nonce-([^']+)'",
        review.headers["content-security-policy"],
    )
    second_nonce = re.search(
        r"\bscript-src 'self' 'nonce-([^']+)'",
        second_review.headers["content-security-policy"],
    )
    assert first_nonce is not None and second_nonce is not None
    assert first_nonce.group(1) != second_nonce.group(1)
    assert all(
        f'nonce="{first_nonce.group(1)}"' in tag
        for tag in re.findall(r"<script\b[^>]*>", review.text)
    )


def test_main_audit_app_feishu_form_token_survives_global_boundary(
    tmp_path, monkeypatch
):
    from app.audit_web import create_audit_app

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "secret-not-rendered")
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _seed(store)
    client = TestClient(
        create_audit_app(store.path),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    )

    page = client.get("/feishu/review")
    form = re.search(
        r"action='/feishu/scopes/group/oc_audit_1/approve'[^>]*>"
        r".*?name='csrf_token' value='([^']+)'",
        page.text,
        re.DOTALL,
    )
    assert form is not None

    response = client.post(
        "/feishu/scopes/group/oc_audit_1/approve",
        data={"csrf_token": form.group(1), "approved_by": "browser-reviewer"},
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    scope = store.get_feishu_reply_scope("cli_test", "group", "oc_audit_1")
    assert scope is not None
    assert scope.binding_status == "verified"
    assert scope.enabled is True
    assert scope.approved_by == "browser-reviewer"


def test_review_shows_exact_approval_effect_but_json_list_withholds_draft(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    _mark_sent(store, delivery, "om_bot_chunk_1")
    private_text = "PRIVATE-HANDOFF-CONTENT"
    _seed_action(
        store,
        delivery,
        kind="handoff_notify",
        secret_text=private_text,
    )
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_app_secret", lambda: "PRIVATE-SECRET")
    client = _client(store)

    page = client.get("/feishu/review")
    receipts = client.get("/feishu/receipts").json()
    actions = client.get("/feishu/actions").json()

    assert page.status_code == 200
    assert "消息收据" in page.text
    assert "om_bot_chunk_1" in page.text
    assert "撤回" in page.text
    assert "消息动作" in page.text
    assert "完整交接通知" in page.text
    assert private_text in page.text
    assert "locally_allowlisted_handoff_recipient" in page.text
    assert "sha256:" in page.text
    assert "ou_private_owner" not in page.text
    assert "PRIVATE-SECRET" not in page.text
    assert receipts["items"][0]["message_id"] == "om_bot_chunk_1"
    serialized = str(actions)
    assert "payload_json" not in serialized
    assert "target_open_id" not in serialized
    assert private_text not in serialized
    assert "ou_private_owner" not in serialized
    [action_item] = actions["items"]
    assert "approval_hash" not in action_item
    assert "approval_preview" not in action_item
    assert action_item["summary"].startswith("handoff text=[redacted:")
    assert action_item["preview"]["effect"]["text_summary"].startswith(
        "[redacted:"
    )
    assert action_item["preview"]["target"]["fingerprint"].startswith(
        "sha256:"
    )

    [review_action] = client.get(
        "/feishu/actions?include_preview=true"
    ).json()["items"]
    assert review_action["approval_hash"]
    assert review_action["approval_preview"]["effect"]["text"] == private_text


def test_receipt_recall_is_durable_r4_approval_and_repeat_is_idempotent(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    _mark_sent(store, delivery, "om_bot_recall")
    [receipt] = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_recall_enabled", lambda: True)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(config, "feishu_send_mode", lambda: "auto")
    client = _client(store)

    first = client.post(
        f"/feishu/receipts/{receipt.id}/recall",
        data=_csrf_data(approved_by="human-owner"),
        headers=_origin(),
        follow_redirects=False,
    )
    second = client.post(
        f"/feishu/receipts/{receipt.id}/recall",
        data=_csrf_data(approved_by="human-owner"),
        headers=_origin(),
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert second.status_code == 303
    [action] = store.list_feishu_message_actions(app_id="cli_test")
    assert action.kind == "recall_message"
    assert action.risk == "R4"
    assert action.target_message_id == receipt.message_id
    assert action.status == "ready"
    assert action.approved_by == "human-owner"
    assert action.approved_at
    assert store.get_feishu_delivery_receipt(
        app_id="cli_test", message_id=receipt.message_id
    ).status == "active"


def test_receipt_recall_rejects_origin_gates_wrong_app_and_nonactive_receipt(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    _mark_sent(store, delivery, "om_bot_recall")
    [receipt] = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_recall_enabled", lambda: True)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: True)
    client = _client(store)
    path = f"/feishu/receipts/{receipt.id}/recall"

    no_csrf = client.post(
        path,
        data={"approved_by": "owner"},
        headers=_origin(),
        follow_redirects=False,
    )
    cross_origin = client.post(
        path,
        data=_csrf_data(approved_by="owner"),
        headers={"Origin": "https://attacker.example"},
        follow_redirects=False,
    )
    monkeypatch.setattr(config, "feishu_recall_enabled", lambda: False)
    closed = client.post(
        path,
        data=_csrf_data(approved_by="owner"),
        headers=_origin(),
        follow_redirects=False,
    )
    monkeypatch.setattr(config, "feishu_recall_enabled", lambda: True)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "other_app")
    wrong_app = client.post(
        path,
        data=_csrf_data(approved_by="owner"),
        headers=_origin(),
        follow_redirects=False,
    )
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    not_bot_owned = client.post(
        f"/feishu/receipts/{receipt.id + 999}/recall",
        data=_csrf_data(approved_by="owner"),
        headers=_origin(),
        follow_redirects=False,
    )

    assert no_csrf.status_code == 403
    assert cross_origin.status_code == 403
    assert closed.status_code == 409
    assert wrong_app.status_code == 409
    assert not_bot_owned.status_code == 409
    assert store.list_feishu_message_actions() == []

    with store._connect() as db:
        db.execute(
            "update feishu_delivery_receipts set status='recalled' where id=?",
            (receipt.id,),
        )
    nonactive = client.post(
        path,
        data=_csrf_data(approved_by="owner"),
        headers=_origin(),
        follow_redirects=False,
    )
    assert nonactive.status_code == 409
    assert store.list_feishu_message_actions() == []


def test_action_review_is_app_kind_gate_and_approval_hash_bound(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    action = _seed_action(store, delivery)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_reaction_enabled", lambda: True)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: True)
    client = _client(store)
    path = f"/feishu/actions/{action.id}/approve"

    drift = client.post(
        path,
        data=_csrf_data(
            approved_by="reviewer",
            approval_hash="0" * 64,
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert drift.status_code == 409
    assert store.get_feishu_message_action(action.id).approved_at == ""

    monkeypatch.setattr(config, "feishu_reaction_enabled", lambda: False)
    closed = client.post(
        path,
        data=_csrf_data(
            approved_by="reviewer",
            approval_hash=action.approval_hash,
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert closed.status_code == 409

    monkeypatch.setattr(config, "feishu_reaction_enabled", lambda: True)
    monkeypatch.setattr(config, "feishu_app_id", lambda: "other_app")
    wrong_app = client.post(
        path,
        data=_csrf_data(
            approved_by="reviewer",
            approval_hash=action.approval_hash,
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert wrong_app.status_code == 409

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    approved = client.post(
        path,
        data=_csrf_data(
            approved_by="reviewer",
            approval_hash=action.approval_hash,
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert approved.status_code == 303
    assert store.get_feishu_message_action(action.id).approved_by == "reviewer"


def test_action_reject_ignores_closed_kind_gate_and_rejects_approved_state(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    action = _seed_action(store, delivery)
    action = store.approve_feishu_message_action(
        action.id,
        app_id=action.app_id,
        approved_by="preflight-reviewer",
        expected_approval_hash=action.approval_hash,
    )
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_reaction_enabled", lambda: False)
    client = _client(store)
    path = f"/feishu/actions/{action.id}/reject"

    monkeypatch.setattr(config, "feishu_app_id", lambda: "other_app")
    wrong_app = client.post(
        path,
        data=_csrf_data(rejected_by="reviewer"),
        headers=_origin(),
        follow_redirects=False,
    )
    assert wrong_app.status_code == 409
    assert store.get_feishu_message_action(action.id).status == "ready"

    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    rejected = client.post(
        path,
        data=_csrf_data(rejected_by="reviewer"),
        headers=_origin(),
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "rejected"
    assert saved.error_code == "rejected"
    assert saved.approved_at
    assert saved.approved_by == "preflight-reviewer"


def test_action_unknown_reconcile_is_local_kind_specific_and_idempotent(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    action = _mark_action_unknown(store, _seed_action(store, delivery))
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_reaction_enabled", lambda: False)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: False)

    def forbidden_client(*_args, **_kwargs):
        raise AssertionError("action reconciliation must not build a client")

    monkeypatch.setattr("app.feishu.client.build_channel", forbidden_client)
    client = _client(store)
    path = f"/feishu/actions/{action.id}/reconcile"
    page = client.get("/feishu/review")
    assert page.status_code == 200
    assert "Reaction ID" in page.text
    assert path in page.text

    no_csrf = client.post(
        path,
        data={
            "outcome": "applied",
            "verified_by": "operator",
            "evidence_kind": "feishu_ui",
            "remote_id": "omr_verified",
        },
        headers=_origin(),
        follow_redirects=False,
    )
    assert no_csrf.status_code == 403

    weak = client.post(
        path,
        data=_csrf_data(
            outcome="applied",
            verified_by="operator",
            evidence_kind="free_form_note",
            remote_id="omr_verified",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert weak.status_code == 422

    missing_remote = client.post(
        path,
        data=_csrf_data(
            outcome="applied",
            verified_by="operator",
            evidence_kind="message_lookup",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert missing_remote.status_code == 422

    reconciled = client.post(
        path,
        data=_csrf_data(
            outcome="applied",
            verified_by="operator",
            evidence_kind="message_lookup",
            remote_id="omr_verified",
            request_log_id="log_unknown",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert reconciled.status_code == 303
    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "sent"
    assert saved.remote_id == "omr_verified"

    duplicate = client.post(
        path,
        data=_csrf_data(
            outcome="applied",
            verified_by="operator",
            evidence_kind="message_lookup",
            remote_id="omr_verified",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert duplicate.status_code == 409
    events = store.list_feishu_audit_events(
        entity_type="message_action", entity_id=action.id
    )
    assert sum(row.event_type == "unknown_verified_applied" for row in events) == 1


def test_recall_unknown_web_reconcile_and_requeue_are_atomic_local_steps(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, delivery = _seed(store)
    delivery = _mark_sent(store, delivery, "om_bot_recall")
    action = _mark_action_unknown(
        store, _seed_action(store, delivery, kind="recall_message")
    )
    [receipt] = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    assert receipt.status == "recall_unknown"
    monkeypatch.setattr(config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(config, "feishu_recall_enabled", lambda: False)
    monkeypatch.setattr(config, "feishu_live_send_allowed", lambda: False)

    def forbidden_client(*_args, **_kwargs):
        raise AssertionError("offline action review must not build a client")

    monkeypatch.setattr("app.feishu.client.build_channel", forbidden_client)
    client = _client(store)
    reconcile_path = f"/feishu/actions/{action.id}/reconcile"
    reconciled = client.post(
        reconcile_path,
        data=_csrf_data(
            outcome="not_applied",
            verified_by="operator",
            evidence_kind="feishu_ui",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert reconciled.status_code == 303
    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "failed"
    assert saved.error_code == "verified_not_applied"
    [receipt] = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    assert receipt.status == "active"
    assert receipt.recall_action_id == 0

    page = client.get("/feishu/review")
    assert f"/feishu/actions/{action.id}/requeue" in page.text
    requeued = client.post(
        f"/feishu/actions/{action.id}/requeue",
        data=_csrf_data(
            verified_by="operator",
            evidence_kind="admin_audit",
        ),
        headers=_origin(),
        follow_redirects=False,
    )
    assert requeued.status_code == 303
    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "retry"
    assert saved.approved_at == ""
    assert saved.approved_by == ""
