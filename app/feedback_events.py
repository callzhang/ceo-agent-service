import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable
from urllib.parse import quote

from app.feedback_spike import (
    FeedbackLinkContext,
    extract_feedback_link_context,
)
from app.store import AutoReplyStore, SentReply


def feedback_context_for_sent_reply(
    sent_reply: SentReply,
) -> FeedbackLinkContext | None:
    context = extract_feedback_link_context(sent_reply.reply_text)
    if context is not None:
        return context
    token = sent_reply.feedback_token.strip()
    base_url = os.getenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "").strip().rstrip("/")
    if token and (
        base_url.startswith("https://") or base_url.startswith("http://")
    ):
        return FeedbackLinkContext(feedback_token=token, vercel_base_url=base_url)
    return None


def sync_feedback_events_for_sent_replies(
    store: AutoReplyStore,
    sent_replies: Iterable[SentReply],
    *,
    timeout_seconds: float = 2,
    limit_per_token: int = 20,
) -> int:
    contexts = {
        context.feedback_token: context
        for sent_reply in sent_replies
        if (context := feedback_context_for_sent_reply(sent_reply)) is not None
    }
    existing_events = store.list_feedback_events_for_tokens(list(contexts))
    synced = 0
    for context in contexts.values():
        if existing_events.get(context.feedback_token):
            continue
        synced += sync_feedback_events_for_context(
            store,
            context,
            timeout_seconds=timeout_seconds,
            limit_per_token=limit_per_token,
        )
    return synced


def sync_feedback_events_for_context(
    store: AutoReplyStore,
    context: FeedbackLinkContext,
    *,
    timeout_seconds: float = 2,
    limit_per_token: int = 20,
) -> int:
    url = (
        f"{context.vercel_base_url}/api/dingtalk-feedback-spike-events"
        f"?feedback_token={quote(context.feedback_token)}&limit={limit_per_token}"
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
    ):
        return 0
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return 0
    synced = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        token = str(event.get("feedback_token") or "").strip()
        if token != context.feedback_token:
            continue
        key = str(event.get("key") or "").strip()
        if not key:
            key = f"{token}:{event.get('received_at') or ''}:{event.get('rating') or ''}"
        store.upsert_feedback_event(
            key=key,
            feedback_token=token,
            rating=str(event.get("rating") or ""),
            rating_label=str(event.get("rating_label") or ""),
            comment=str(event.get("comment") or ""),
            original_text=str(event.get("original_text") or ""),
            reply_text=str(event.get("reply_text") or ""),
            source=str(event.get("source") or ""),
            received_at=str(event.get("received_at") or ""),
            raw_json=json.dumps(event, ensure_ascii=False),
        )
        synced += 1
    return synced
