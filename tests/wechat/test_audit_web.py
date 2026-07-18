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
