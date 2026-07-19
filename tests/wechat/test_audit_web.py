import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AutoReplyStore
from app.wechat.audit_web import register_wechat_tutorial_routes


class FakeSetup:
    def __init__(self, store, targets):
        self.store = store
        self._targets = targets

    def list_targets(self, *, query, kind, limit, offset):
        items = [t for t in self._targets if t["target_type"] == kind]
        if query:
            items = [t for t in items if query.lower() in t["display_name"].lower()]
        return items[offset:offset + limit]


@pytest.fixture
def client(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    targets = [
        {"target_type": "direct", "target_id": "u-1", "display_name": "Alex"},
        {"target_type": "direct", "target_id": "u-2", "display_name": "Alex"},
    ]
    app = FastAPI()
    register_wechat_tutorial_routes(app, setup_factory=lambda: FakeSetup(store, targets))
    tc = TestClient(app)
    tc.store = store
    return tc


def test_wechat_picker_separates_duplicate_names_by_stable_id(client):
    response = client.get("/tutorial/wechat/conversations?kind=direct&query=Alex")
    assert response.status_code == 200
    assert [row["target_id"] for row in response.json()["items"]] == ["u-1", "u-2"]


def test_invalid_kind_is_422(client):
    assert client.get("/tutorial/wechat/conversations?kind=bogus").status_code == 422


def test_scope_api_forces_group_mention_trigger(client):
    response = client.post("/tutorial/wechat/reply-scope", json={
        "account_id": "acct-1",
        "targets": [{
            "target_type": "group", "target_id": "g-1",
            "display_name": "CEO", "trigger_mode": "every_inbound_text",
        }],
    })
    assert response.status_code == 422


def test_scope_api_saves_valid_targets(client):
    response = client.post("/tutorial/wechat/reply-scope", json={
        "account_id": "acct-1",
        "targets": [{
            "target_type": "group", "target_id": "g-1",
            "display_name": "CEO", "trigger_mode": "mention_current_account",
        }],
    })
    assert response.status_code == 200
    assert response.json()["saved"] == 1
    assert len(client.store.list_wechat_reply_scopes("acct-1")) == 1


def _seed_delivery(store):
    from app.wechat.models import WechatReplyScope
    store.replace_wechat_reply_scopes("acct-1", [WechatReplyScope(
        account_id="acct-1", target_type="group", target_id="g@chatroom",
        conversation_id="g@chatroom", display_name="G",
        trigger_mode="mention_current_account", binding_status="verified")])
    store.enqueue_reply_task(
        channel="wechat", conversation_id="g@chatroom", conversation_title="G",
        single_chat=False, trigger_message_id="m1", trigger_create_time="2026-07-18T10:00:00",
        trigger_sender="X", trigger_text="hi")
    store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="group", target_id="g@chatroom",
        conversation_id="g@chatroom", reply_text="草稿回复")
    return store.get_wechat_delivery_for_task(1)


@pytest.fixture
def review_client(tmp_path):
    from app.wechat.audit_web import register_wechat_review_routes
    from app.wechat.accessibility import SendOutcome
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    sent = []

    class FakeSender:
        def send(self, delivery, scope):
            store.set_wechat_delivery_status(delivery.id, "sent")
            sent.append(delivery.id)
            return SendOutcome("sent")

    app = FastAPI()
    register_wechat_review_routes(app, store_factory=lambda: store,
                                  sender_factory=lambda s: FakeSender())
    tc = TestClient(app)
    tc.store = store
    tc.sent = sent
    return tc


def test_review_page_shows_send_button(review_client):
    _seed_delivery(review_client.store)
    r = review_client.get("/wechat/review")
    assert r.status_code == 200
    assert "发送" in r.text and "草稿回复" in r.text


def test_deliveries_json_lists_pending(review_client):
    _seed_delivery(review_client.store)
    data = review_client.get("/wechat/deliveries").json()
    assert [d["id"] for d in data["pending"]] == [1]


def test_reject_marks_failed_without_send(review_client):
    _seed_delivery(review_client.store)
    r = review_client.post("/wechat/deliveries/1/reject", follow_redirects=False)
    assert r.status_code == 303
    assert review_client.store.get_wechat_delivery_for_task(1).status == "failed"
    assert review_client.sent == []


def test_approve_sends(review_client):
    import time
    _seed_delivery(review_client.store)
    r = review_client.post("/wechat/deliveries/1/approve", follow_redirects=False)
    assert r.status_code == 303
    for _ in range(100):
        if review_client.store.get_wechat_delivery_for_task(1).status == "sent":
            break
        time.sleep(0.02)
    assert review_client.store.get_wechat_delivery_for_task(1).status == "sent"
    assert review_client.sent == [1]
