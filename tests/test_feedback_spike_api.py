from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1] / "api"


def test_callback_endpoint_accepts_get_and_post_and_redacts_headers():
    source = (API_DIR / "dingtalk-feedback-spike.js").read_text(encoding="utf-8")

    assert '["GET", "POST"].includes(req.method)' in source
    assert 'import { put } from "@vercel/blob";' in source
    assert '"feedback_token"' in source
    assert '"feedbackToken"' in source
    assert '"rating"' in source
    assert "safeHeaders" in source
    assert "content-type" in source
    safe_headers_source = source.split("function safeHeaders", 1)[1].split(
        "function extractBody", 1
    )[0]
    assert "authorization" not in safe_headers_source.lower()


def test_callback_endpoint_writes_event_list_and_expiring_event_key():
    source = (API_DIR / "dingtalk-feedback-spike.js").read_text(encoding="utf-8")

    assert 'const EVENT_LIST_KEY = "feedback-spike-events"' in source
    assert 'const EVENT_KEY_PREFIX = "feedback-spike:"' in source
    assert 'put(`${EVENT_LIST_KEY}/${event.key}.json`, JSON.stringify(event)' in source
    assert 'access: "public"' in source
    assert "BLOB_READ_WRITE_TOKEN" in source


def test_events_endpoint_requires_secret_and_reads_recent_events():
    source = (API_DIR / "dingtalk-feedback-spike-events.js").read_text(encoding="utf-8")

    assert 'import { list } from "@vercel/blob";' in source
    assert "FEEDBACK_SPIKE_SECRET" in source
    assert "x-feedback-spike-secret" in source
    assert "requestFeedbackToken" in source
    assert '"feedback_token"' in source
    assert "filteredEvents" in source
    assert 'res.status(401).json({ ok: false, error: "unauthorized" })' in source
    assert "BLOB_READ_WRITE_TOKEN" in source
    assert 'prefix: `${EVENT_LIST_KEY}/`' in source
