from app.wechat.article import first_url, enrich_context
from app.wechat.models import WechatMessage


def _msg(text):
    return WechatMessage(
        account_id="a", conversation_id="g", message_id="m", sender_id="s",
        sender_display_name="S", conversation_type="group", direction="inbound",
        sent_at="2026-07-18T10:00:00", kind="unknown", text=text, source_version="4.1.10")


def test_first_url():
    assert first_url("[链接]《X》 https://mp.weixin.qq.com/s?a=1&b=2 tail").startswith(
        "https://mp.weixin.qq.com/s?a=1")
    assert first_url("no url here") == ""


def test_enrich_context_appends_body(monkeypatch):
    monkeypatch.setenv("CEO_WECHAT_FETCH_ARTICLES", "1")
    msgs = [_msg("[链接]《X》 https://mp.weixin.qq.com/s?a=1"), _msg("plain text")]
    out = enrich_context(msgs, fetch=lambda url, max_chars=1500: "FULLBODY")
    assert "【正文摘录】FULLBODY" in out[0].text
    assert out[1].text == "plain text"


def test_enrich_context_respects_limit(monkeypatch):
    monkeypatch.setenv("CEO_WECHAT_FETCH_ARTICLES", "1")
    msgs = [_msg(f"[链接] http://x/{i}") for i in range(6)]
    calls = []

    def fake_fetch(url, max_chars=1500):
        calls.append(url); return "B"

    enrich_context(msgs, limit=2, fetch=fake_fetch)
    assert len(calls) == 2


def test_enrich_disabled_by_flag(monkeypatch):
    monkeypatch.setenv("CEO_WECHAT_FETCH_ARTICLES", "0")
    msgs = [_msg("[链接] http://x/1")]
    out = enrich_context(msgs, fetch=lambda *a, **k: "B")
    assert out[0].text == "[链接] http://x/1"


def test_enrich_skips_empty_fetch(monkeypatch):
    monkeypatch.setenv("CEO_WECHAT_FETCH_ARTICLES", "1")
    msgs = [_msg("[链接] http://x/1")]
    out = enrich_context(msgs, fetch=lambda *a, **k: "")  # e.g. verify page
    assert out[0].text == "[链接] http://x/1"
