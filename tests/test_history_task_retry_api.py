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
    task, _failed_run_id = _failed_retryable_task(
        store, code="business_rule_gap", retryable=False
    )

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "reply_task_retry_not_allowed"
    current = store.get_reply_task(task.id)
    assert current is not None
    assert current.status == "failed"
    assert current.execution_generation == task.execution_generation


def test_dependency_read_failure_retries_with_fresh_route_session(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, _failed_run_id = _failed_retryable_task(
        store, code="dependency_read_unavailable", retryable=False
    )
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_oauth",
        "old-context-session",
        "service-contract",
    )

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 200, response.text
    current = store.get_reply_task(task.id)
    assert current is not None
    assert current.status == "pending"
    assert current.execution_generation != task.execution_generation
    assert store.get_conversation_runtime_session(
        task.conversation_id, "codex_oauth"
    ) is None


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


def _failed_effect_free_email_unsubscribe(store: AutoReplyStore):
    import json

    action_identity = "email-action:retry-api-unsubscribe"
    payload = {
        "schema": "email_agent_action.v1",
        "action_type": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_identity": action_identity,
        "action_parameters": {"candidate_reference": "entry-1", "candidate_source": "body_html_https"},
        "unsubscribe_entries": [{"reference": "entry-1", "source": "body_html_https"}],
    }
    assert store.enqueue_reply_task(
        conversation_id="email-thread:retry-api",
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=action_identity,
        trigger_create_time="2026-10-30T10:00:00Z",
        trigger_sender="sender@example.com",
        trigger_text="Authorized unsubscribe action.",
        trigger_message_json=json.dumps(payload),
        channel="email",
    )
    task = store.list_reply_tasks(limit=1)[0]
    task = store.claim_reply_task(task.id)
    assert task is not None
    store.fail_reply_task(
        task.id, "provider source temporarily unavailable",
        expected_execution_generation=task.execution_generation,
    )
    return task


def test_task_retry_api_requeues_failed_effect_free_email_unsubscribe(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_effect_free_email_unsubscribe(store)

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["task_id"] == task.id
    assert item["status"] == "pending"
    assert item["retried_generation"] == task.execution_generation
    retried = store.get_reply_task(task.id)
    assert retried is not None
    assert retried.status == "pending"
    assert retried.execution_generation != task.execution_generation
    assert retried.current_input_id == task.current_input_id
    assert retried.error == "task_retry:effect_free_email_unsubscribe"
    retry_state = store.get_service_state(
        f"email_unsubscribe_manual_retry:v1:{task.trigger_message_id}"
    )
    assert retry_state == '{"retry_count": 1}'


def test_task_retry_api_bounds_effect_free_email_unsubscribe_retries(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_effect_free_email_unsubscribe(store)
    store.set_service_state(
        f"email_unsubscribe_manual_retry:v1:{task.trigger_message_id}",
        '{"retry_count":3}',
    )

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "email_unsubscribe_retry_not_allowed"
    current = store.get_reply_task(task.id)
    assert current is not None
    assert current.status == "failed"
    assert current.execution_generation == task.execution_generation


def test_task_retry_api_preserves_unresolved_email_unsubscribe_marker(tmp_path: Path):
    import json

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_effect_free_email_unsubscribe(store)
    marker_key = f"email_unsubscribe_execution_pending:v1:{task.trigger_message_id}"
    marker = json.dumps({
        "task_id": task.id,
        "action_identity": task.trigger_message_id,
        "execution_generation": task.execution_generation,
        "input_version": task.input_version,
        "input_digest": "unknown",
        "started_at": "2026-10-30T10:00:00Z",
    })
    store.set_service_state(marker_key, marker)

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "email_unsubscribe_retry_not_allowed"
    current = store.get_reply_task(task.id)
    assert current is not None
    assert current.status == "failed"
    assert current.execution_generation == task.execution_generation
    assert store.get_service_state(marker_key) == marker

