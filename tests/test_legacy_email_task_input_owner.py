"""Existing legacy-email terminal handling reads its native TaskInput owner."""

import json

import pytest

from app.store import AutoReplyStore


@pytest.mark.parametrize(("channel", "lifecycle", "expected"), [
    ("email", "email_unsubscribe_consumer_direct_v1", True),
    ("email", "email_unsubscribe_audited_v2", False),
    ("dingtalk", "email_unsubscribe_consumer_direct_v1", False),
])
def test_terminalization_reads_current_input_without_changing_eligibility(
    tmp_path, channel, lifecycle, expected,
):
    store = AutoReplyStore(tmp_path / "input-owner.sqlite3")
    assert store.enqueue_reply_task(
        channel=channel, conversation_id="owner-conversation", conversation_title="Owner",
        single_chat=True, trigger_message_id="owner-message", trigger_create_time="",
        trigger_sender="fixture", trigger_text="unsubscribe fixture",
        trigger_message_json=json.dumps({"schema": "email_agent_action.v1",
            "action_type": "unsubscribe", "lifecycle_version": lifecycle}),
    )
    task = store.get_reply_task_for_message("owner-conversation", "owner-message", channel=channel)
    assert task is not None
    changed = store.terminalize_legacy_email_unsubscribe_task(
        task.id, expected_execution_generation=task.execution_generation,
        expected_status="pending",
    )
    assert changed is expected
    current = store.get_reply_task(task.id)
    assert current.status == ("failed" if expected else "pending")
    assert current.execution_generation == task.execution_generation
    assert current.error == ("legacy_email_unsubscribe_lifecycle" if expected else task.error)
    assert current.trigger_message_json == task.trigger_message_json
