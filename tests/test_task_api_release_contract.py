"""Release contracts for semantic Tasks links and complete detail readback."""

from app.store import AutoReplyStore
from tests.test_console_web_api import _client


def _signal(db, key: str) -> int:
    return int(db.execute(
        "insert into business_task_signals (source_type, source_ref, evidence_text, dedupe_key, created_at) values (?, ?, ?, ?, ?)",
        ("test", key, key, key, "2026-09-24T00:00:00+00:00"),
    ).lastrowid)


def test_semantic_detail_links_match_frontend_routes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="Launch", stage="formal", formal_basis="explicit_assignment")
    legacy_id = store.create_work_project(title="Historical", category="dev", status="active", priority="P1", risk_level="low")
    with store._immediate_write_transaction() as db:
        signal_id = _signal(db, "link-source")
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="project", anchor_ref="launch", title="Launch", _db=db)
        project_id = store.create_business_project_in_transaction(canonical_anchor_id=anchor_id, title="Launch", registry_source="registry", _db=db)
        store.create_business_task_anchor_link_in_transaction(task_id=task_id, anchor_id=anchor_id, status="confirmed", active=True, evidence_signal_id=signal_id, _db=db)
    with _client(tmp_path) as client:
        task = client.get("/api/console/tasks/all").json()["items"][0]
        project = client.get("/api/console/tasks/projects").json()["items"][0]
        legacy = client.get(f"/api/console/tasks/legacy-projects/{legacy_id}").json()["item"]
        task_detail = client.get(f"/api/console/tasks/items/{task_id}").json()["item"]
    assert task["detail_url"] == f"/tasks/item/{task_id}"
    assert project["detail_url"] == f"/tasks/project/{project_id}"
    assert task_detail["official_projects"][0]["detail_url"] == project["detail_url"]
    assert legacy["todos"] == []


def test_project_candidates_have_independent_truthful_pagination(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._immediate_write_transaction() as db:
        for index in range(3):
            cluster_id = store.create_business_work_cluster_in_transaction(title=f"Cluster {index}", _db=db)
            store.create_business_project_candidate_in_transaction(cluster_id=cluster_id, title=f"Candidate {index}", reason="Unconfirmed", _db=db)
    with _client(tmp_path) as client:
        first = client.get("/api/console/tasks/projects?candidate_page=1&candidate_page_size=1").json()
        second = client.get("/api/console/tasks/projects?candidate_page=2&candidate_page_size=1").json()
    assert first["meta"]["total"] == 0
    assert first["candidate_meta"]["total"] == 3
    assert first["candidate_meta"]["has_more"] is True
    assert [row["title"] for row in first["candidates"]] == ["Candidate 0"]
    assert [row["title"] for row in second["candidates"]] == ["Candidate 1"]


def test_project_candidate_query_treats_search_text_literally(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._immediate_write_transaction() as db:
        for title in ("Growth 20%", "Growth 200"):
            cluster_id = store.create_business_work_cluster_in_transaction(title=title, _db=db)
            store.create_business_project_candidate_in_transaction(cluster_id=cluster_id, title=title, reason="Unconfirmed", _db=db)
    with _client(tmp_path) as client:
        payload = client.get("/api/console/tasks/projects", params={"q": "20%"}).json()
    assert payload["candidate_meta"]["total"] == 1
    assert [row["title"] for row in payload["candidates"]] == ["Growth 20%"]


def test_task_detail_contains_all_relations_and_anchor_links(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="Central", stage="formal", formal_basis="explicit_assignment")
    with store._immediate_write_transaction() as db:
        signal_id = _signal(db, "all-links")
        for index in range(101):
            other_id = int(db.execute("insert into business_tasks (title, stage, status, commitment_status, business_relevance, created_at, updated_at) values (?, 'candidate', 'open', 'none', 'relevant', current_timestamp, current_timestamp)", (f"Other {index}",)).lastrowid)
            db.execute("insert into business_task_relations (from_task_id, to_task_id, relation_type, supporting_signal_id) values (?, ?, 'related_to', ?)", (task_id, other_id, signal_id))
            anchor_id = store.create_business_anchor_in_transaction(anchor_type="matter", anchor_ref=f"matter-{index}", title=f"Matter {index}", _db=db)
            store.create_business_task_anchor_link_in_transaction(task_id=task_id, anchor_id=anchor_id, status="confirmed", active=True, evidence_signal_id=signal_id, _db=db)
    with _client(tmp_path) as client:
        detail = client.get(f"/api/console/tasks/items/{task_id}").json()["item"]
    assert len(detail["relations"]) == 101
    assert len(detail["anchors"]) == 101
    assert len(detail["summary"]["anchor_labels"]) == 101


def test_task_detail_contains_all_follow_ups_beyond_store_page(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="Follow up", stage="formal", formal_basis="explicit_assignment")
    with store._immediate_write_transaction() as db:
        signal_id = _signal(db, "follow-up-source")
        for index in range(201):
            db.execute(
                "insert into business_task_follow_ups (business_task_id, source_signal_id, target_conversation_id, target_kind, question_text, scheduled_at, dedupe_key) values (?, ?, 'conversation-1', 'group', 'Status?', '2026-10-01T00:00:00+00:00', ?)",
                (task_id, signal_id, f"follow-up-{index}"),
            )
    with _client(tmp_path) as client:
        detail = client.get(f"/api/console/tasks/items/{task_id}").json()["item"]
    assert len(detail["follow_ups"]) == 201


def test_project_member_count_agrees_in_task_and_project_details(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.create_business_task(title="First", stage="formal", formal_basis="explicit_assignment")
    second_id = store.create_business_task(title="Second", stage="formal", formal_basis="explicit_assignment")
    with store._immediate_write_transaction() as db:
        signal_id = _signal(db, "members")
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="project", anchor_ref="members", title="Members", _db=db)
        project_id = store.create_business_project_in_transaction(canonical_anchor_id=anchor_id, title="Members", registry_source="registry", _db=db)
        for task_id in (first_id, second_id):
            store.create_business_task_anchor_link_in_transaction(task_id=task_id, anchor_id=anchor_id, status="confirmed", active=True, evidence_signal_id=signal_id, _db=db)
    with _client(tmp_path) as client:
        task_project = client.get(f"/api/console/tasks/items/{first_id}").json()["item"]["official_projects"][0]
        project = client.get(f"/api/console/tasks/projects/{project_id}").json()["item"]
    assert task_project["confirmed_task_count"] == project["summary"]["confirmed_task_count"] == 2
    assert len(project["confirmed_tasks"]) == 2


def test_sent_todos_includes_task_keyed_links_with_semantic_deep_link(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="Ship", stage="formal", formal_basis="explicit_assignment")
    link_id = store.create_business_task_dingtalk_link(
        business_task_id=task_id, dingtalk_task_id="dingtalk-ship",
        executor_user_id="owner-1", executor_name="Alex",
        title_snapshot="Ship", deadline_at_snapshot="2026-10-01T00:00:00+00:00",
        status="active",
    )
    with _client(tmp_path) as client:
        payload = client.get("/api/console/tasks/sent-todos").json()
    assert payload["meta"]["total"] == 1
    assert payload["items"][0]["id"] == f"business_task_dingtalk:{link_id}"
    assert payload["items"][0]["external_id"] == "dingtalk-ship"
    assert payload["items"][0]["detail_url"] == f"/tasks/item/{task_id}"
