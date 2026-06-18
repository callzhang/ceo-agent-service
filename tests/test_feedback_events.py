import json
from pathlib import Path

from app.feedback_events import sync_feedback_events_for_sent_replies
from app.feedback_spike import build_feedback_spike_link_message
from app.store import AutoReplyStore


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_sync_feedback_events_for_sent_replies_imports_remote_event(
    tmp_path: Path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    link = build_feedback_spike_link_message(
        vercel_base_url="https://feedback.example.com",
        reply_text="收到",
        feedback_token="token-1",
    )
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        link.text,
        feedback_token="token-1",
    )
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return FakeResponse(
            {
                "events": [
                    {
                        "key": "event-1",
                        "feedback_token": "token-1",
                        "rating": "useful",
                        "rating_label": "很有用",
                        "comment": "继续这样回复",
                        "received_at": "2026-06-18T08:55:00.000Z",
                    }
                ]
            }
        )

    monkeypatch.setattr("app.feedback_events.urllib.request.urlopen", fake_urlopen)

    synced = sync_feedback_events_for_sent_replies(
        store,
        store.list_sent_replies_with_feedback_tokens(),
        timeout_seconds=0.5,
    )

    events = store.list_feedback_events_for_token("token-1")
    assert synced == 1
    assert len(events) == 1
    assert events[0].key == "event-1"
    assert events[0].comment == "继续这样回复"
    assert calls == [
        (
            "https://feedback.example.com/api/dingtalk-feedback-spike-events"
            "?feedback_token=token-1&limit=20",
            0.5,
        )
    ]
