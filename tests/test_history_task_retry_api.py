from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AgentRole, AutoReplyStore
from app.web_api.history_tasks import register_history_task_routes


def _failed_retryable_task(
    store: AutoReplyStore,
    *,
    code: str = "dependency_read_unavailable",
    retryable: bool = True,
):
    assert store.enqueue_reply_task(
        conversation_id="scheduled:retry-api",
        conversation_title="Scheduled retry",
        single_chat=False,
        trigger_message_id="scheduled-retry-api",
        trigger_create_time="2026-10-27T00:00:00Z",
        trigger_sender="scheduler",
        trigger_text="Run scheduled task",
        execution_generation="generation-1",
        channel="scheduled",
    )
    task = store.get_reply_task_for_message(
        "scheduled:retry-api", "scheduled-retry-api", channel="scheduled"
    )
    assert task is not None
    task = store.claim_reply_task(task.id)
    assert task is not None
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="test-worker",
    )
    error = {
        "code": code,
        "retryable": retryable,
        "source": "agent",
    }
    store.fail_agent_run(claim.run.id, error, owner="test-worker")
    store.fail_reply_task(
        task.id,
        code,
        expected_execution_generation=task.execution_generation,
    )
    return task, claim.run.id


def _client(store: AutoReplyStore) -> TestClient:
    app = FastAPI()
    register_history_task_routes(app, lambda: store)
    return TestClient(app)


def test_failed_retryable_reply_task_retries_through_task_api(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, failed_run_id = _failed_retryable_task(store)
    client = _client(store)

    response = client.post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["task_id"] == task.id
    assert item["status"] == "pending"
    assert item["retried_run_id"] == failed_run_id
    retried = store.get_reply_task(task.id)
    assert retried is not None
    assert retried.status == "pending"
    assert retried.execution_generation != task.execution_generation
    assert retried.current_input_id == task.current_input_id
    assert retried.error == "task_retry:dependency_read_unavailable"


def test_task_retry_api_keeps_nonretryable_failure_failed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, _failed_run_id = _failed_retryable_task(store, retryable=False)

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "reply_task_retry_not_allowed"
    current = store.get_reply_task(task.id)
    assert current is not None
    assert current.status == "failed"
    assert current.execution_generation == task.execution_generation


def test_task_retry_api_rejects_provider_risk_rejection(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, _failed_run_id = _failed_retryable_task(
        store, code="provider_risk_rejected"
    )

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "provider_risk_rejected"
    current = store.get_reply_task(task.id)
    assert current is not None
    assert current.status == "failed"
    assert current.execution_generation == task.execution_generation

