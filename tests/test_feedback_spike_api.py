from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1] / "api"


def test_callback_endpoint_accepts_get_and_post_and_redacts_headers():
    source = (API_DIR / "dingtalk-feedback-spike.js").read_text(encoding="utf-8")

    assert '["GET", "POST"].includes(req.method)' in source
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
    assert '["SET", event.key, eventJson, "EX", "604800"]' in source
    assert '["LPUSH", EVENT_LIST_KEY, eventJson]' in source
    assert '["LTRIM", EVENT_LIST_KEY, "0", "99"]' in source


def test_events_endpoint_requires_secret_and_reads_recent_events():
    source = (API_DIR / "dingtalk-feedback-spike-events.js").read_text(encoding="utf-8")

    assert "FEEDBACK_SPIKE_SECRET" in source
    assert "x-feedback-spike-secret" in source
    assert "KV_REST_API_READ_ONLY_TOKEN || process.env.KV_REST_API_TOKEN" in source
    assert 'res.status(401).json({ ok: false, error: "unauthorized" })' in source
    assert '["LRANGE", EVENT_LIST_KEY, "0", String(limit - 1)]' in source
