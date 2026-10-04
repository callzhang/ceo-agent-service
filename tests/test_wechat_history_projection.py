"""WeChat delivery facts remain independent of candidate completion."""

from types import SimpleNamespace

import pytest

import app.audit_web as audit_web
from app.attempt_projection import project_attempt_status
from app.store import AutoReplyStore
from app.web_api.attempts import build_attempt_detail
from tests.test_console_web_api import _client


@pytest.mark.parametrize(
    "delivery_status,generation,conversation,latest_sent,expected_failed,attempt_status",
    [
        ("failed", "initial", "wechat-current", False, True, "failed"),
        ("send_unknown", "initial", "wechat-current", False, True, "failed"),
        ("failed", "old-generation", "wechat-current", False, False, "failed"),
        ("failed", "initial", "another-conversation", False, False, "failed"),
        ("failed", "initial", "wechat-current", True, False, "failed"),
        ("failed", "initial", "wechat-current", False, False, "pending"),
        ("failed", "initial", "wechat-current", False, False, "sent"),
        ("failed", "initial", "wechat-current", False, False, "skipped"),
        ("failed", "initial", "wechat-current", False, False, "needs_human"),
    ],
)
def test_wechat_history_uses_latest_delivery_for_current_object_and_generation(
    tmp_path, delivery_status, generation, conversation, latest_sent, expected_failed,
    attempt_status,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        channel="wechat", conversation_id="wechat-current", conversation_title="Morgan",
        single_chat=True, trigger_message_id="message-1",
        trigger_create_time="2026-10-04 13:05:00", trigger_sender="Morgan",
        trigger_text="Can you help later?",
    )
    task = store.get_reply_task_for_message("wechat-current", "message-1", channel="wechat")
    delivery_id = store.create_wechat_delivery(
        reply_task_id=task.id, account_id="acct-1", target_type="direct",
        target_id="wechat-current", conversation_id="wechat-current", reply_text="Yes.",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="wechat-current", conversation_title="Morgan",
        trigger_message_id="message-1", trigger_sender="Morgan",
        trigger_text="Can you help later?", action="send_reply",
        sensitivity_kind="normal", send_status=attempt_status, channel="wechat",
    )
    with store._connect() as db:
        db.execute("update reply_tasks set status='done' where id=?", (task.id,))
        db.execute(
            "update wechat_deliveries set status=?, error='wechat_ui_not_ready', "
            "pre_action_failure=1, execution_generation=?, conversation_id=? where id=?",
            (delivery_status, generation, conversation, delivery_id),
        )
        if latest_sent:
            db.execute(
                "insert into wechat_deliveries (reply_task_id, execution_generation, account_id, "
                "target_type, target_id, conversation_id, reply_text, status) "
                "values (?, 'initial', 'acct-1', 'direct', 'wechat-current', 'wechat-current', 'Yes.', 'sent')",
                (task.id,),
            )
        original_deliveries = [tuple(row) for row in db.execute("select * from wechat_deliveries")]
        original_tasks = [tuple(row) for row in db.execute("select * from reply_tasks")]
        original_attempts = [tuple(row) for row in db.execute("select * from reply_attempts")]

    with _client(tmp_path) as client:
        response = client.get("/api/console/history?status=failed")
        assert response.status_code == 200
        assert response.json()["meta"]["total"] == int(expected_failed)
        assert (str(attempt_id) in {str(item["id"]) for item in response.json()["items"]}) == expected_failed
        detail = client.get(f"/api/console/history/{attempt_id}")
        assert detail.status_code == 200
        assert detail.json()["item"]["status"]["raw"] == ("failed" if expected_failed else "done")

    with store._connect() as db:
        snapshot = audit_web._reply_attempt_queue_snapshot(db)
    assert snapshot["counts"].get("failed", 0) == int(expected_failed)
    chart = audit_web._history_chart_payload(store)
    series = {row["name"]: sum(row["data"]) for row in chart["series"]}
    assert series.get("Failed", 0) == int(expected_failed)
    event_label = "Failed" if expected_failed else {
        "pending": "Pending", "skipped": "Skipped", "needs_human": "Needs human",
    }.get(attempt_status, "Done")
    assert series.get(event_label, 0) == 1
    assert sum(series.values()) == 1
    with store._connect() as db:
        assert [tuple(row) for row in db.execute("select * from wechat_deliveries")] == original_deliveries
        assert [tuple(row) for row in db.execute("select * from reply_tasks")] == original_tasks
        assert [tuple(row) for row in db.execute("select * from reply_attempts")] == original_attempts


def test_wechat_delivery_for_another_task_does_not_override_attempt():
    attempt = SimpleNamespace(channel="wechat", conversation_id="same-chat", send_status="pending")
    task = SimpleNamespace(id=1, execution_generation="initial", status="done")
    delivery = SimpleNamespace(task_id=2, execution_generation="initial",
                               conversation_id="same-chat", status="failed")
    assert project_attempt_status(attempt, task, [], delivery=delivery) == "done"


def test_old_pending_wechat_attempt_still_projects_closed_task_as_done(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        channel="wechat", conversation_id="old-chat", conversation_title="Old task",
        single_chat=True, trigger_message_id="old-message", trigger_create_time="2026-10-04 10:00:00",
        trigger_sender="Morgan", trigger_text="Earlier request",
    )
    task = store.get_reply_task_for_message("old-chat", "old-message", channel="wechat")
    attempt_id = store.record_reply_attempt(
        channel="wechat", conversation_id="old-chat", conversation_title="Old task",
        trigger_message_id="old-message", trigger_sender="Morgan", trigger_text="Earlier request",
        action="send_reply", sensitivity_kind="normal", send_status="pending",
    )
    with store._connect() as db:
        db.execute("update reply_tasks set status='done' where id=?", (task.id,))
    status, detail = build_attempt_detail(store, attempt_id)
    assert status == 200
    assert detail["status"]["raw"] == "done"
    assert store.get_reply_attempt(attempt_id).send_status == "pending"
