import json

from app.audit_web import render_attempt_list
from app.store import AutoReplyStore
from tests.support.legacy_todo_facts import (
    seed_legacy_todo_link,
    seed_legacy_work_project,
    seed_legacy_work_todo,
    seed_legacy_work_update,
)
from tests.test_console_web_api import _client
from tests.support.business_task_seed import seed_task
from tests.support.legacy_todo_facts import seed_legacy_follow_up


def test_formal_task_list_and_legacy_history_detail_have_disjoint_read_surfaces(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    legacy_id = seed_legacy_work_project(
        store,
        title="Historical delivery",
        category="projects",
        status="active",
        facts_json=json.dumps(
            [
                {
                    "description": {"text": "Historical outcome"},
                    "source": {"title": "meeting-notes.md"},
                }
            ]
        ),
        source_conversations_json=json.dumps(
            [{"id": "conversation-legacy", "title": "Delivery group"}]
        ),
    )
    todo_id = seed_legacy_work_todo(
        store,
        project_id=legacy_id,
        title="Historical follow-up",
        status="done",
    )
    seed_legacy_work_update(
        store,
        project_id=legacy_id,
        source_type="meeting",
        source_ref="minutes-legacy",
        summary="Historical update",
    )
    seed_legacy_todo_link(
        store,
        work_todo_id=todo_id,
        dingtalk_task_id="receipt-legacy",
        executor_user_id="legacy-owner",
        title_snapshot="Historical follow-up",
        status="done",
        last_dingtalk_done=True,
        last_dingtalk_payload_json=json.dumps({"id": "receipt-legacy", "done": True}),
    )
    formal_id = seed_task(store,
        title="Current formal task",
        stage="formal",
        formal_basis="explicit_assignment",
        business_relevance="relevant",
    )
    assert formal_id == legacy_id

    with _client(tmp_path) as client:
        current = client.get("/api/console/tasks?page=1&page_size=20")
        legacy = client.get(f"/api/console/tasks/legacy-projects/{legacy_id}")
        retired_search = client.get(
            "/api/task-management/search", params={"q": "Historical delivery"}
        )
        retired_project = client.get(f"/api/task-management/projects/{legacy_id}")
        retired_html = client.get("/tasks")

    assert current.status_code == 200
    assert [item["id"] for item in current.json()["items"]] == [formal_id]
    assert [item["title"] for item in current.json()["items"]] == [
        "Current formal task"
    ]

    assert legacy.status_code == 200
    historical = legacy.json()["item"]
    assert historical["project"]["facts"] == [
        {
            "description": "Historical outcome",
            "source": "meeting-notes.md",
            "created": "",
            "updated": "",
        }
    ]
    assert historical["project"]["source_conversations"] == [
        {"id": "conversation-legacy", "title": "Delivery group"}
    ]
    assert historical["updates"][0]["source_ref"] == "minutes-legacy"
    assert historical["todos"][0]["dingtalk_todos"][0]["dingtalk_task_id"] == (
        "receipt-legacy"
    )

    assert retired_search.status_code == 404
    assert retired_project.status_code == 404
    assert retired_html.status_code == 404


def test_legacy_history_links_share_one_canonical_spa_and_html_detail_path(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = seed_legacy_work_project(
        store,
        title="Historical delivery",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(
        store,
        project_id=project_id,
        title="Historical follow-up",
        status="open",
    )
    follow_up_id = seed_legacy_follow_up(store,
        project_id=project_id,
        todo_id=todo_id,
        target_kind="direct",
        question_text="Is the historical delivery complete?",
        scheduled_at="2026-10-25 10:00:00",
        status="sent",
    )
    canonical_detail = f"/tasks/legacy-project/{project_id}"
    canonical_follow_up = f"{canonical_detail}#follow-up-{follow_up_id}"

    history_html = render_attempt_list(store, search_object_type="task")
    assert f'data-history-detail-href="{canonical_follow_up}"' in history_html
    assert f'href="{canonical_follow_up}"' in history_html
    assert f'"/tasks/{project_id}#follow-up-{follow_up_id}"' not in history_html

    with _client(tmp_path) as client:
        html_detail = client.get(canonical_detail)
        ambiguous_detail = client.get(f"/tasks/{project_id}")

    assert html_detail.status_code == 200
    assert "Historical delivery" in html_detail.text
    assert "Historical follow-up" in html_detail.text
    assert ambiguous_detail.status_code == 404

    spa_root = tmp_path / "spa"
    spa_root.mkdir()
    spa_index = b"<!doctype html><title>Current Tasks</title>"
    with _client(spa_root, spa_enabled=True, asset=spa_index) as client:
        spa_detail = client.get(canonical_detail)

    assert spa_detail.status_code == 200
    assert spa_detail.content == spa_index

