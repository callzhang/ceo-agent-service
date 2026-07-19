"""Best-effort fetch of a shared article's body, to give Codex real context.

WeChat public-account pages (mp.weixin.qq.com) return an anti-bot *verify* page
to plain clients but serve the article to a MicroMessenger (WeChat) UA; other
links fetch normally. Bounded length, proxy-env-independent (trust_env=False),
and failure-safe (returns "" on any problem — context degrades to title only).
"""
from __future__ import annotations

import re

_WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40"
)
_URL_RE = re.compile(r"https?://[^\s)]+")


def first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    return m.group(0) if m else ""


def fetch_article_text(url: str, *, max_chars: int = 1500, timeout: float = 15.0) -> str:
    if not url or not url.startswith("http"):
        return ""
    try:
        import httpx
    except Exception:
        return ""
    try:
        resp = httpx.get(
            url, headers={"User-Agent": _WECHAT_UA}, timeout=timeout,
            follow_redirects=True, trust_env=False,
        )
    except Exception:
        return ""
    html = resp.text or ""
    if "secitptpage" in html or "环境异常" in html:
        return ""  # WeChat anti-bot verify page
    m = (re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<(?:div|script|section)', html, re.S)
         or re.search(r'id="js_content"[^>]*>(.*)', html, re.S))
    body = m.group(1) if m else html
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", body)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 80:
        return ""
    return text[:max_chars]


def enrich_context(messages, *, limit: int = 4, fetch=fetch_article_text):
    """Return context with the article body appended to up to `limit` messages
    that carry a link. Frozen WechatMessage is copied, not mutated. Gated by
    CEO_WECHAT_FETCH_ARTICLES; failure-safe."""
    try:
        from app import config
        if not config.wechat_fetch_articles():
            return messages
        max_chars = config.wechat_article_max_chars()
    except Exception:
        max_chars = 1500
    out = []
    fetched = 0
    for message in messages:
        url = first_url(getattr(message, "text", ""))
        if url and fetched < limit:
            body = fetch(url, max_chars=max_chars)
            if body:
                message = message.model_copy(
                    update={"text": f"{message.text}\n【正文摘录】{body}"}
                )
                fetched += 1
        out.append(message)
    return out
