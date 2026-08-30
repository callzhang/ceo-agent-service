from pathlib import Path

from app.store import AutoReplyStore
from app.web_api.tasks import task_list_response


def _create_project(
    store: AutoReplyStore,
    title: str,
    *,
    priority: str = "P1",
    category: str = "dev",
) -> int:
    return store.create_work_project(
        title=title,
        category=category,
        status="active",
        priority=priority,
        risk_level="low",
    )


def test_task_list_sorts_before_pagination(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _create_project(store, "Zulu project")
    alpha_id = _create_project(store, "Alpha project")
    untitled_id = _create_project(store, "")
    _create_project(store, "Finance project", category="finance")
    store.create_work_todo(
        project_id=alpha_id,
        title="Open item",
        status="open",
        priority="P1",
    )

    ascending = task_list_response(
        store,
        page=1,
        page_size=1,
        sort="project_asc",
    )
    descending = task_list_response(
        store,
        page=1,
        page_size=1,
        sort="project_desc",
    )

    assert ascending.items[0].title == "Alpha project"
    assert ascending.meta.total == 4
    assert descending.items[0].title == "Zulu project"
    assert ascending.filters.categories == ["dev", "finance"]
    assert ascending.filters.task_states == ["in progress", "not started"]
    untitled = task_list_response(
        store,
        page=1,
        page_size=10,
    )
    assert next(item for item in untitled.items if item.id == untitled_id).title == (
        f"Project {untitled_id}"
    )


def test_task_list_uses_business_priority_order(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _create_project(store, "Low project", priority="P2")
    _create_project(store, "High project", priority="P0")

    response = task_list_response(
        store,
        page=1,
        page_size=1,
        sort="priority_desc",
    )

    assert response.items[0].title == "High project"
