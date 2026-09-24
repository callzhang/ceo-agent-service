from datetime import datetime, timezone

from app.store import AutoReplyStore
from app.web_api.tasks import business_task_list_response


def test_semantic_task_filter_and_pagination_precede_serialization(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.create_work_project(title="Legacy project", category="dev", status="active", priority="P1", risk_level="low")
    older = store.create_business_task(title="Alpha", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant", now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    store.create_business_task(title="Excluded candidate", stage="candidate", business_relevance="relevant")
    newer = store.create_business_task(title="Beta", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant", now=datetime(2026, 9, 2, tzinfo=timezone.utc))

    first = business_task_list_response(store, page=1, page_size=1, stage="formal")
    second = business_task_list_response(store, page=2, page_size=1, stage="formal")

    assert first.meta.total == second.meta.total == 2
    assert [first.items[0].id, second.items[0].id] == [newer, older]
    assert first.items[0].detail_url == f"/tasks/item/{newer}"
    assert "project_status" not in first.items[0].model_dump()


def test_semantic_task_query_filters_before_page(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.create_business_task(title="Customer renewal", stage="candidate")
    wanted = store.create_business_task(title="Revenue forecast", stage="candidate")
    response = business_task_list_response(store, page=1, page_size=1, query="revenue")
    assert response.meta.total == 1
    assert response.items[0].id == wanted
