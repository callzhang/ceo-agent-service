import json

from ceo_agent_service.memory_flush import flush_memory_events
from ceo_agent_service.store import AutoReplyStore


class RecordingMemoryClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or {"episode_uuid": "ep-1"}
        self.error = error

    def memory_write(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def test_flush_memory_events_marks_success_and_sends_source_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MEMORY_CONNECTOR_USER_ID", "derek")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event_id = store.enqueue_memory_write_event(
        attempt_id=12,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )
    client = RecordingMemoryClient()

    sent_count = flush_memory_events(store, client, limit=20)

    assert sent_count == 1
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["data"] == '{"event":"reply_sent"}'
    assert call["type"] == "json"
    assert call["user_id"] == "derek"
    assert call["source_description"] == "ceo-agent-service:reply_sent:12"
    assert call["source_metadata"] == {
        "service": "ceo-agent-service",
        "event_type": "reply_sent",
        "attempt_id": 12,
        "outbox_event_id": event_id,
    }
    assert call["provenance_metadata"] == {
        "source": "ceo-agent-service",
        "outbox_event_id": event_id,
    }
    event = store.list_memory_write_events()[0]
    assert event.status == "sent"
    assert event.memory_episode_id == "ep-1"
    assert event.last_error == ""


def test_flush_memory_events_marks_failure_and_does_not_touch_reply_attempts(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="pending",
    )
    store.enqueue_memory_write_event(
        attempt_id=attempt_id,
        event_type="review_correction",
        payload_json=json.dumps({"event": "review_correction"}),
    )
    client = RecordingMemoryClient(error=RuntimeError("memory unavailable"))

    sent_count = flush_memory_events(store, client, limit=1)

    assert sent_count == 0
    event = store.list_memory_write_events()[0]
    assert event.status == "failed"
    assert event.last_error == "memory unavailable"
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "pending"
