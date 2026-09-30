from tests.test_console_web_api import _client
from tests.test_task_business_resolution import create_task

from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService


def test_console_confirmation_creates_project_and_links_cluster_tasks(tmp_path):
    path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(path)
    task_id = create_task(store, "console-confirm")
    cluster_id = BusinessResolutionService(store).create_cluster(
        title="海外客户成交", task_ids=[task_id]
    )
    candidate_id = BusinessResolutionService(store).propose_project(
        cluster_id=cluster_id, title="海外客户成交", reason="周报项目清单"
    )

    with _client(tmp_path) as client:
        response = client.post(
            f"/api/console/tasks/project-candidates/{candidate_id}/confirm",
            json={},
        )
        assert response.status_code == 200
        project_id = response.json()["item"]["project_id"]
        replay = client.post(
            f"/api/console/tasks/project-candidates/{candidate_id}/confirm",
            json={},
        )
        assert replay.status_code == 200
        assert replay.json()["item"]["project_id"] == project_id

    project = store.get_business_project(project_id)
    assert project is not None
    assert [item.id for item in store.list_business_task_project_links(task_id=task_id)] == [project_id]
    candidate = store.get_business_project_candidate(candidate_id)
    assert candidate is not None
    assert candidate.status.value == "confirmed"


def test_console_confirmation_requires_source_evidence(tmp_path):
    path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(path)
    task_id = store.create_business_task(title="无来源任务", stage="candidate")
    cluster_id = BusinessResolutionService(store).create_cluster(
        title="无证据项目", task_ids=[task_id]
    )
    candidate_id = BusinessResolutionService(store).propose_project(
        cluster_id=cluster_id, title="无证据项目", reason="缺少来源"
    )

    with _client(tmp_path) as client:
        response = client.post(
            f"/api/console/tasks/project-candidates/{candidate_id}/confirm",
            json={},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_candidate"
    assert store.list_business_projects() == []
