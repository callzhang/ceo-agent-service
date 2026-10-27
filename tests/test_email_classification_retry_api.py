from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.email_store import EmailStore
from app.email_task_adapter import (
    EmailClassificationTaskAdapter,
    EmailClassificationTaskInput,
)
from app.web_api.email import register_email_routes


def _failed_classification_task(store: EmailStore):
    adapter = EmailClassificationTaskAdapter(store, retry_base_seconds=0)
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "retry-account",
            "folder": "INBOX",
            "uidValidity": 7,
            "uid": 42,
            "messageId": "<retry@example.test>",
            "threadId": "retry-thread",
            "providerUnread": True,
            "subject": "Registration confirmed",
            "from": {"email": "events@example.test"},
        },
        allowed_category_keys=("notification",),
        category_descriptions={"notification": "Event notifications"},
        folder_targets={},
        config_version="test-v1",
        unsubscribe_candidates=(),
    )
    task = adapter.ensure_task(task_input)
    claimed = adapter.claim_next(owner="test-worker")
    assert claimed is not None
    adapter.fail(claimed, error="IMAP EOF", retryable=False)
    return adapter, task


def _client(store: EmailStore) -> TestClient:
    app = FastAPI()
    register_email_routes(app, lambda: store)
    return TestClient(app)


def test_failed_email_classification_task_can_be_retried_through_console_api(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "retry-email.sqlite3")
    adapter, task = _failed_classification_task(store)
    original = adapter.get_task(task.task_id)
    assert original is not None and original.status == "failed"

    response = _client(store).post(
        f"/api/console/email/classification-tasks/{task.task_id}/retry"
    )

    assert response.status_code == 200, response.text
    assert response.json()["item"] == {
        "task_id": task.task_id,
        "status": "pending",
        "requeued": True,
        "stable_message_identity": task.stable_message_identity,
    }
    retried = adapter.get_task(task.task_id)
    assert retried is not None
    assert retried.status == "pending"
    assert retried.attempt_count == 0
    assert retried.stable_message_identity == task.stable_message_identity
    assert retried.input_json == task.input_json
    assert retried.result_json == "null"


def test_email_classification_retry_rejects_nonfailed_task(tmp_path: Path):
    store = EmailStore(tmp_path / "retry-email-active.sqlite3")
    adapter = EmailClassificationTaskAdapter(store, retry_base_seconds=0)
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "retry-account",
            "folder": "INBOX",
            "uidValidity": 7,
            "uid": 43,
            "messageId": "<active@example.test>",
            "threadId": "active-thread",
            "providerUnread": True,
        },
        allowed_category_keys=("notification",),
        category_descriptions={"notification": "Event notifications"},
        folder_targets={},
        config_version="test-v1",
        unsubscribe_candidates=(),
    )
    task = adapter.ensure_task(task_input)

    response = _client(store).post(
        f"/api/console/email/classification-tasks/{task.task_id}/retry"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "email_classification_retry_not_allowed"
    assert adapter.get_task(task.task_id).status == "pending"


def test_email_classification_retry_rejects_failed_task_with_saved_result(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "retry-email-result.sqlite3")
    adapter, task = _failed_classification_task(store)
    with store._connect() as db:
        db.execute(
            "update task_results set result_json=? where task_id=? "
            "and result_kind='email_classification'",
            ('{"classification_id":17}', int(task.task_id)),
        )

    response = _client(store).post(
        f"/api/console/email/classification-tasks/{task.task_id}/retry"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "email_classification_retry_not_allowed"
    unchanged = adapter.get_task(task.task_id)
    assert unchanged is not None and unchanged.status == "failed"
    assert unchanged.result_json == '{"classification_id":17}'
