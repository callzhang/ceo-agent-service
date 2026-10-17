from fastapi.testclient import TestClient

from app.audit_web import create_audit_app
from app.store import AutoReplyStore


def test_operator_can_skip_uncertain_failed_reply_task_without_claiming_success(
    tmp_path,
) -> None:
    database = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(database)
    store.enqueue_reply_task(
        conversation_id="cid-expired-calendar",
        conversation_title="Calendar invite",
        single_chat=True,
        trigger_message_id="msg-expired-calendar",
        trigger_create_time="2026-10-15 09:00:00",
        trigger_sender="Casey",
        trigger_text="Calendar invite",
    )
    [task] = store.claim_reply_tasks(limit=1)
    with store._connect() as db:
        db.execute(
            "update tasks set status='failed', error='external_action_uncertain' "
            "where record_type='reply_tasks' and id=?",
            (task.id,),
        )

    client = TestClient(create_audit_app(database))
    state = client.get(f"/api/console/reply-tasks/{task.id}")
    assert state.status_code == 200
    assert state.json()["item"]["status"] == "failed"

    response = client.post(
        f"/api/console/reply-tasks/{task.id}/skip",
        json={
            "execution_generation": state.json()["item"]["execution_generation"],
            "reason": "The event ended; no further response action is required.",
        },
    )

    assert response.status_code == 200
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "skipped"
    assert updated.error == "external_action_uncertain"


def test_skip_uncertain_reply_task_requires_reason_and_current_generation(
    tmp_path,
) -> None:
    database = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(database)
    store.enqueue_reply_task(
        conversation_id="cid-expired-calendar",
        conversation_title="Calendar invite",
        single_chat=True,
        trigger_message_id="msg-expired-calendar",
        trigger_create_time="2026-10-15 09:00:00",
        trigger_sender="Casey",
        trigger_text="Calendar invite",
    )
    [task] = store.claim_reply_tasks(limit=1)
    with store._connect() as db:
        db.execute(
            "update tasks set status='failed', error='external_action_uncertain' "
            "where record_type='reply_tasks' and id=?",
            (task.id,),
        )

    client = TestClient(create_audit_app(database))
    stale = client.post(
        f"/api/console/reply-tasks/{task.id}/skip",
        json={"execution_generation": "old-generation", "reason": "event ended"},
    )
    missing_reason = client.post(
        f"/api/console/reply-tasks/{task.id}/skip",
        json={"execution_generation": task.execution_generation, "reason": " "},
    )

    assert stale.status_code == 409
    assert missing_reason.status_code == 422
    assert store.get_reply_task(task.id).status == "failed"
