from app.store import AutoReplyStore
from app.web_api.tasks import business_attention_list_response
from tests.test_console_web_api import _client


def _attention(store, category, title, now):
    with store._immediate_write_transaction() as db:
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="company_priority", anchor_ref=title, title=title, _db=db)
        signal_id = db.execute("insert into business_task_signals (source_type, source_ref, evidence_text, dedupe_key, created_at) values (?, ?, ?, ?, ?)", ("test", title, title, title, now)).lastrowid
        return store.create_business_attention_item_in_transaction(stable_key=title, category=category, title=title, business_area="Business", why_attention="Needs attention", current_state="Open", ceo_action="Review", anchor_id=anchor_id, evidence_signal_id=signal_id, now=now, _db=db)


def test_attention_order_and_filter_before_pagination(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old = "2026-09-01T00:00:00+00:00"
    new = "2026-09-02T00:00:00+00:00"
    ids = {
        "fyi": _attention(store, "fyi", "FYI", new),
        "watch": _attention(store, "watch", "Watch", old),
        "push_old": _attention(store, "push", "Push old", old),
        "push_new": _attention(store, "push", "Push new", new),
        "decision": _attention(store, "decision", "Decision", old),
    }
    result = business_attention_list_response(store, page=1, page_size=10)
    assert [item.id for item in result.items] == [ids[key] for key in ("decision", "push_new", "push_old", "watch", "fyi")]
    filtered = business_attention_list_response(store, page=2, page_size=1, category="push")
    assert filtered.meta.total == 2
    assert filtered.items[0].id == ids["push_old"]


def test_semantic_routes_do_not_return_legacy_projects(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    legacy_id = store.create_work_project(title="Legacy", category="dev", status="active", priority="P1", risk_level="low")
    task_id = store.create_business_task(title="Real task", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant")
    with _client(tmp_path) as client:
        all_response = client.get("/api/console/tasks/all")
        assert all_response.status_code == 200
        assert [row["id"] for row in all_response.json()["items"]] == [task_id]
        assert client.get(f"/api/console/tasks/items/{task_id}").status_code == 200
        assert client.get(f"/api/console/tasks/legacy-projects/{legacy_id}").status_code == 200
        assert client.get("/api/console/tasks/attention").status_code == 200
        assert client.get("/api/console/tasks/projects").status_code == 200


def test_detail_routes_show_source_evidence_and_confirmed_membership(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="Launch", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant")
    with store._immediate_write_transaction() as db:
        signal_id = db.execute("insert into business_task_signals (source_type, source_ref, evidence_text, dedupe_key, created_at) values (?, ?, ?, ?, ?)", ("meeting", "minutes-1", "Derek assigned Launch", "meeting:1", "2026-09-02T00:00:00+00:00")).lastrowid
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="project", anchor_ref="official-launch", title="Official launch", _db=db)
        project_id = store.create_business_project_in_transaction(canonical_anchor_id=anchor_id, title="Official launch", registry_source="registry", _db=db)
        store.create_business_task_anchor_link_in_transaction(task_id=task_id, anchor_id=anchor_id, status="confirmed", active=True, evidence_signal_id=signal_id, _db=db)
        db.execute("insert into business_task_evidence (task_id, signal_id, evidence_role) values (?, ?, ?)", (task_id, signal_id, "assignment"))
        attention_id = store.create_business_attention_item_in_transaction(stable_key="launch", category="decision", title="Launch decision", business_area="Product", why_attention="Gate", current_state="Pending", ceo_action="Decide", anchor_id=anchor_id, evidence_signal_id=signal_id, now="2026-09-02T00:00:00+00:00", _db=db)
        store.link_business_attention_task_in_transaction(attention_item_id=attention_id, task_id=task_id, _db=db)
    with _client(tmp_path) as client:
        task = client.get(f"/api/console/tasks/items/{task_id}").json()["item"]
        attention = client.get(f"/api/console/tasks/attention/{attention_id}").json()["item"]
        project = client.get(f"/api/console/tasks/projects/{project_id}").json()["item"]
    assert task["evidence"][0]["signal"]["evidence_text"] == "Derek assigned Launch"
    assert task["official_projects"][0]["id"] == project_id
    assert attention["linked_tasks"][0]["id"] == task_id
    assert project["confirmed_tasks"][0]["id"] == task_id


def test_project_candidates_are_labeled_provisional_and_not_official(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._immediate_write_transaction() as db:
        cluster_id = store.create_business_work_cluster_in_transaction(title="Possible project", _db=db)
        candidate_id = store.create_business_project_candidate_in_transaction(cluster_id=cluster_id, title="Possible project", reason="Needs registry confirmation", _db=db)
    with _client(tmp_path) as client:
        payload = client.get("/api/console/tasks/projects").json()
    assert payload["items"] == []
    assert payload["candidates"] == [{
        "id": candidate_id, "title": "Possible project", "reason": "Needs registry confirmation",
        "status": "proposed", "cluster_id": cluster_id, "provisional": True,
        "confirmed_project_id": None,
    }]


def test_task_list_filters_by_owner_and_sorts_by_creation_or_update(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    owned = store.create_business_task(title="有人负责", stage="candidate", owner_name="王明")
    bare = store.create_business_task(title="无人负责", stage="candidate")
    with store._immediate_write_transaction() as db:
        db.execute("update business_tasks set created_at='2026-09-01 00:00:00', updated_at='2026-09-03 00:00:00' where id=?", (owned,))
        db.execute("update business_tasks set created_at='2026-09-02 00:00:00', updated_at='2026-09-02 00:00:00' where id=?", (bare,))
    with _client(tmp_path) as client:
        def ids(query):
            response = client.get(f"/api/console/tasks/all?{query}")
            assert response.status_code == 200
            return [row["id"] for row in response.json()["items"]]
        assert ids("owner=assigned") == [owned]
        assert ids("owner=unassigned") == [bare]
        assert ids("") == [owned, bare]
        assert ids("sort=updated") == [owned, bare]
        assert ids("sort=created") == [bare, owned]
        assert client.get("/api/console/tasks/all?owner=nobody").status_code == 400
        assert client.get("/api/console/tasks/all?sort=title").status_code == 400
