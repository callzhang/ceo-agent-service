from app.store import AutoReplyStore
from tests.test_console_web_api import _client


def _events(store, task_id):
    return [(event.event_type.value, event.reason) for event in store.list_business_task_events(task_id)]


def test_ignoring_a_candidate_cancels_it_with_a_human_signal_and_restoring_reopens_it(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="整理访谈问题清单", stage="candidate")
    with _client(tmp_path) as client:
        response = client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "ignore"})
        assert response.status_code == 200
        assert response.json()["item"]["status"] == "cancelled"
        task = client.get(f"/api/console/tasks/items/{task_id}").json()["item"]
        assert task["summary"]["status"] == "cancelled"
        assert task["summary"]["stage"] == "candidate"
        signal = next(row["signal"] for row in task["evidence"] if row["role"] == "correction")
        assert signal["source_type"] == "console"
        assert signal["author_kind"] == "human"
        assert "整理访谈问题清单" in signal["evidence_text"]
        assert [event["event_type"] for event in task["events"]][-1] == "status_changed"

        back = client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "restore"})
        assert back.status_code == 200
        assert client.get(f"/api/console/tasks/items/{task_id}").json()["item"]["summary"]["status"] == "open"
    assert [kind for kind, _ in _events(store, task_id)] == ["status_changed", "status_changed"]


def test_a_second_click_on_the_same_state_is_refused_not_repeated(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="候选", stage="candidate")
    with _client(tmp_path) as client:
        assert client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "ignore"}).status_code == 200
        again = client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "ignore"})
        assert again.status_code == 409
        assert again.json()["code"] == "not_applicable"
        assert client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "restore"}).status_code == 200
        assert client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "restore"}).status_code == 409
    assert len(_events(store, task_id)) == 2


def test_a_formal_task_cannot_be_set_aside_from_the_console(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="正式任务", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant")
    with _client(tmp_path) as client:
        response = client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "ignore"})
        assert response.status_code == 409
        assert client.get(f"/api/console/tasks/items/{task_id}").json()["item"]["summary"]["status"] == "open"
    assert _events(store, task_id) == []


def test_unknown_task_and_unknown_action_are_rejected(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.create_business_task(title="候选", stage="candidate")
    with _client(tmp_path) as client:
        assert client.post("/api/console/tasks/items/9999/candidate-decision", json={"action": "ignore"}).status_code == 404
        assert client.post(f"/api/console/tasks/items/{task_id}/candidate-decision", json={"action": "promote"}).status_code == 400
