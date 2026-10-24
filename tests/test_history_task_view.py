from pathlib import Path

from app.store import AutoReplyStore
from tests.test_console_attempt_detail_api import _seed_email_attempt
from tests.test_console_web_api import _client


def test_history_reply_row_names_its_task_and_opens_the_task_page(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=True)
    task_id = store.get_agent_run(store.get_reply_attempt(attempt_id).agent_run_id).reply_task_id

    with _client(tmp_path) as client:
        rows = client.get("/api/console/history?object_type=email_unsubscribe").json()["items"]

    row = next(item for item in rows if item["id"] == str(attempt_id))
    assert row["task_id"] == task_id
    assert row["run_count"] == 2
    assert row["detail_url"] == f"/history/tasks/{task_id}"


def test_history_task_page_lists_the_task_runs_and_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=True)
    attempt = store.get_reply_attempt(attempt_id)
    task_id = store.get_agent_run(attempt.agent_run_id).reply_task_id

    with _client(tmp_path) as client:
        response = client.get(f"/api/console/history/tasks/{task_id}")
        missing = client.get("/api/console/history/tasks/999999")

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["task"]["id"] == task_id
    assert attempt.agent_run_id in [run["id"] for run in item["runs"]]
    assert [entry["id"] for entry in item["attempts"]] == [attempt_id]
    assert item["attempts"][0]["detail_url"] == f"/attempts/{attempt_id}"
    assert missing.status_code == 404


def test_history_attempt_without_a_run_link_belongs_to_the_task_of_its_message(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=False)

    with _client(tmp_path) as client:
        rows = client.get("/api/console/history?object_type=email_unsubscribe").json()["items"]
        row = next(item for item in rows if item["id"] == str(attempt_id))
        task = client.get(f"/api/console/history/tasks/{row['task_id']}").json()["item"]

    assert row["detail_url"] == f"/history/tasks/{row['task_id']}"
    assert row["run_count"] == 0
    assert [entry["id"] for entry in task["attempts"]] == [attempt_id]
