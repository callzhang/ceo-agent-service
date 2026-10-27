"""Real Web source projection with the configured provider dependency replaced."""

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tests.test_provenance_vector_storage import db as db
from tests.test_business_weekly_source_reader import signal, binding, FakeDws
from app.store import AutoReplyStore
from app.business_storage import insert_business
from app.action_business_storage import insert_business_action
from app.web_api.registration import register_console_routes
from tests.support.business_task_seed import seed_task_signal


@pytest.mark.parametrize("route", ("signals", "items"))
def test_source_detail_uses_existing_dws_factory(db, tmp_path, monkeypatch, route):
    item = signal()
    exact = binding(item)
    insert_business(db, "task", dict(id=43, title="Task", stage="candidate"))
    db.commit()
    path = tmp_path / "final-web-component.sqlite3"
    with sqlite3.connect(path) as target:
        db.backup(target)
    with monkeypatch.context() as patch:
        # Full constructor selection is a separate final integration gate.
        patch.setattr(AutoReplyStore, "_ensure_initialized", lambda self: None)
        store = AutoReplyStore(path)
    public_id = seed_task_signal(store,
        source_type=item.source_type, source_ref=item.source_ref, source_time=item.source_time,
        conversation_id=item.conversation_id, author_user_id=item.author_user_id,
        author_name=item.author_name, author_kind=item.author_kind,
        evidence_text=item.evidence_text, dedupe_key=item.dedupe_key, source_binding=exact)
    with store.business_task_transaction() as current:
        insert_business_action(current, "business_task_evidence", dict(
            task_id=43, signal_id=public_id, evidence_role="discovery"))
    provider = FakeDws()
    app = FastAPI()
    register_console_routes(app, lambda: store, status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: [], attention_rows_factory=lambda: [], dws_factory=lambda: provider)
    with TestClient(app) as client:
        response = client.get(f"/api/console/tasks/{route}/{public_id if route == 'signals' else 43}")
    assert response.status_code == 200, response.text
    payload = response.json()["item"]
    view = payload if route == "signals" else payload["evidence"][0]["signal"]
    assert view["source_available"] is True
    assert view["evidence_text"] == item.evidence_text
    assert "source_document_id" not in view
    assert len(provider.commands) == 1
